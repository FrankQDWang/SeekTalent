from __future__ import annotations

from typing import cast, get_args

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec, resolve_stage_model_config
from seektalent.models import (
    ControllerContext,
    ControllerDecision,
    FilterField,
    SearchControllerDecision,
    StopControllerDecision,
)
from seektalent.protected_attributes import PROTECTED_ATTRIBUTE_FIELDS, PROTECTED_ATTRIBUTE_FILTER_ADVICE_TEXT
from seektalent.prompt_safety import (
    render_template_version_block,
    render_untrusted_json_block,
    render_untrusted_text_block,
    validate_allowed_actions,
)
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.repair import RepairCallError, repair_controller_decision, unpack_repair_result
from seektalent.retrieval.query_plan import (
    _ROUND_SECONDARY_TITLE_ANCHOR_REASON,
    canonicalize_controller_query_terms,
    normalize_term,
    try_project_secondary_title_anchor_after_round_one,
)
from seektalent.tracing import ProviderUsageSnapshot, combine_provider_usage, provider_usage_from_result

DISABLED_FILTER_FIELDS = frozenset({"position", *PROTECTED_ATTRIBUTE_FIELDS})


def _items(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- (none)"


def _reflection_backed_inactive_terms(context: ControllerContext) -> set[str]:
    if context.previous_reflection is None:
        return set()
    advice = context.latest_reflection_keyword_advice
    if advice is None:
        return set()
    return {
        normalize_term(term).casefold()
        for term in [
            *advice.suggested_activate_terms,
            *advice.suggested_keep_terms,
        ]
    }


def _allowed_filter_fields() -> list[str]:
    return [field for field in get_args(FilterField) if field not in DISABLED_FILTER_FIELDS]


def _prompt_safe_constraints(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in PROTECTED_ATTRIBUTE_FIELDS}


def _disabled_filter_fields_in(decision: SearchControllerDecision) -> list[str]:
    fields = [
        *decision.proposed_filter_plan.pinned_filters,
        *decision.proposed_filter_plan.optional_filters,
        *decision.proposed_filter_plan.added_filter_fields,
    ]
    return sorted({field for field in fields if field in DISABLED_FILTER_FIELDS})


def render_controller_prompt(context: ControllerContext) -> str:
    validate_allowed_actions(["search_cts", "stop"], allowed={"search_cts", "stop"})
    sheet = context.requirement_sheet
    admitted_terms = [item for item in context.query_term_pool if item.queryability == "admitted"]
    term_rows = [
        "| term | family | role | priority | active | tried |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    tried_terms = {normalize_term(term).casefold() for term in context.tried_query_terms}
    for item in admitted_terms:
        tried = "yes" if item.term.casefold() in tried_terms else "no"
        term_rows.append(
            f"| {item.term} | {item.family} | {item.retrieval_role} | {item.priority} | {item.active} | {tried} |"
        )
    top_pool = [
        f"- {item.resume_id}: {item.fit_bucket}, score={item.overall_score}, "
        f"must={item.must_have_match_score}, risk={item.risk_score}; {item.reasoning_summary}"
        for item in context.current_top_pool[:8]
    ]
    query_history = [
        (
            f"- round {receipt.round_no}: {', '.join(receipt.query_terms)}; "
            f"{receipt.keyword_query}; status={receipt.status}"
        )
        for receipt in context.recent_query_execution_receipts
    ]
    latest = context.latest_search_observation
    city_search_summaries = (
        [item.model_dump(mode="json") for item in latest.city_search_summaries] if latest is not None else []
    )
    latest_search = (
        "\n".join(
            [
                f"- new={latest.unique_new_count}; shortage={latest.shortage_count}; attempts={latest.fetch_attempt_count}",
                f"- exhausted_reason={latest.exhausted_reason or '(none)'}",
                f"- adapter_notes={', '.join(latest.adapter_notes) or '(none)'}",
                f"- new_candidate_summaries={'; '.join(latest.new_candidate_summaries[:5]) or '(none)'}",
                f"- city_search_summaries={city_search_summaries}",
            ]
        )
        if latest is not None
        else "(none yet)"
    )
    if context.previous_reflection is None:
        previous_reflection = "(none)"
    else:
        previous_reflection = f"{context.previous_reflection.decision}: {context.previous_reflection.reflection_summary}"
    previous_reflection_block = render_untrusted_text_block("PREVIOUS_REFLECTION", previous_reflection)
    reflection_advice = {
        "keyword_advice": (
            context.latest_reflection_keyword_advice.model_dump(mode="json")
            if context.latest_reflection_keyword_advice is not None
            else None
        ),
        "filter_advice": (
            context.latest_reflection_filter_advice.model_dump(mode="json")
            if context.latest_reflection_filter_advice is not None
            else None
        ),
        "previous_reflection": (
            context.previous_reflection.model_dump(mode="json") if context.previous_reflection is not None else None
        ),
    }
    structured_constraints = {
        "hard_constraints": _prompt_safe_constraints(sheet.hard_constraints.model_dump(mode="json")),
        "preferences": sheet.preferences.model_dump(mode="json"),
        "protected_attributes": PROTECTED_ATTRIBUTE_FILTER_ADVICE_TEXT,
    }
    requirement_sheet_text = (
        f"- Job Title: {sheet.job_title}\n"
        f"- Summary: {sheet.role_summary}\n"
        f"- Must have:\n{_items(sheet.must_have_capabilities)}\n"
        f"- Preferred:\n{_items(sheet.preferred_capabilities)}\n"
        f"- Scoring rationale: {sheet.scoring_rationale}"
    )
    stop_guidance_text = (
        f"- Can stop: {context.stop_guidance.can_stop}\n"
        f"- Reason: {context.stop_guidance.reason}\n"
        f"- Top pool strength: {context.stop_guidance.top_pool_strength}\n"
        f"- Fit count: {context.stop_guidance.fit_count}\n"
        f"- Strong fit count: {context.stop_guidance.strong_fit_count}\n"
        f"- High-risk fit count: {context.stop_guidance.high_risk_fit_count}\n"
        f"- Productive rounds: {context.stop_guidance.productive_round_count}\n"
        f"- Zero-gain rounds: {context.stop_guidance.zero_gain_round_count}\n"
        f"- Quality gate status: {context.stop_guidance.quality_gate_status}\n"
        f"- Broadening attempted: {context.stop_guidance.broadening_attempted}\n"
        f"- Continue reasons: {', '.join(context.stop_guidance.continue_reasons) or '(none)'}\n"
        f"- Untried admitted families: {', '.join(context.stop_guidance.untried_admitted_families) or '(none)'}"
    )
    exact_data = {
        "round_no": context.round_no,
        "action_options": ["search_cts", "stop"],
        "allowed_filter_fields": _allowed_filter_fields(),
        "stop_guidance_can_stop": context.stop_guidance.can_stop,
        "quality_gate_status": context.stop_guidance.quality_gate_status,
        "tried_query_terms": context.tried_query_terms,
        "recent_query_execution_receipts": [
            receipt.model_dump(mode="json") for receipt in context.recent_query_execution_receipts
        ],
        "used_term_group_keys": context.used_term_group_keys,
        "previous_query_outcomes": [
            outcome.model_dump(mode="json") for outcome in context.previous_query_outcomes
        ],
    }
    return "\n\n".join(
        [
            render_template_version_block("controller"),
            "TASK\nChoose the next retrieval action. Return one ControllerDecision.",
            (
                "DECISION STATE\n"
                f"- Round: {context.round_no} / {context.max_rounds}\n"
                f"- Min rounds: {context.min_rounds}\n"
                f"- Retrieval rounds completed: {context.retrieval_rounds_completed}\n"
                f"- Rounds remaining after current: {context.rounds_remaining_after_current}\n"
                f"- Budget used ratio: {context.budget_used_ratio:.2f}\n"
                f"- Near budget limit: {context.near_budget_limit}\n"
                f"- Final allowed round: {context.is_final_allowed_round}\n"
                f"- Target new resumes: {context.target_new}\n"
                f"- Shortage history: {context.shortage_history}\n"
                f"- Budget reminder: {context.budget_reminder or '(none)'}"
            ),
            "STOP GUIDANCE\n" + render_untrusted_text_block("STOP_GUIDANCE", stop_guidance_text),
            (
                "REQUIREMENTS\n"
                f"{render_untrusted_text_block('REQUIREMENT_SHEET', requirement_sheet_text)}\n"
                f"- JD:\n{render_untrusted_text_block('JOB_DESCRIPTION', context.full_jd)}\n"
                f"- Notes:\n{render_untrusted_text_block('SOURCING_NOTES', context.full_notes or '(none)')}"
            ),
            "TERM BANK\n" + render_untrusted_text_block("TERM_BANK", "\n".join(term_rows)),
            "QUERY EXECUTION HISTORY\n"
            + render_untrusted_text_block(
                "QUERY_EXECUTION_HISTORY",
                "\n".join(query_history) if query_history else "- (none)",
            ),
            "LATEST SEARCH OBSERVATION\n" + render_untrusted_text_block("LATEST_SEARCH_OBSERVATION", latest_search),
            "CURRENT TOP POOL\n"
            + render_untrusted_text_block("CURRENT_TOP_POOL", "\n".join(top_pool) if top_pool else "- (empty)"),
            "STRUCTURED CONSTRAINTS\n" + render_untrusted_json_block("STRUCTURED_CONSTRAINTS", structured_constraints),
            "REFLECTION ADVICE\n" + render_untrusted_json_block("REFLECTION_ADVICE", reflection_advice),
            f"PREVIOUS REFLECTION\n{previous_reflection_block}",
            json_block("EXACT DATA", exact_data),
        ]
    )


def validate_controller_decision(*, context: ControllerContext, decision: ControllerDecision) -> str | None:
    if isinstance(decision, StopControllerDecision) and not context.stop_guidance.can_stop:
        return f"action=stop is not allowed because stop_guidance.can_stop is false: {context.stop_guidance.reason}"
    if isinstance(decision, SearchControllerDecision) and not decision.proposed_query_terms:
        return "proposed_query_terms must contain at least one term."
    if isinstance(decision, SearchControllerDecision):
        disabled_fields = _disabled_filter_fields_in(decision)
        if disabled_fields:
            return (
                f"{', '.join(disabled_fields)} filter is disabled; "
                "express role intent through proposed_query_terms instead."
            )
        try:
            canonical_query_terms = canonicalize_controller_query_terms(
                decision.proposed_query_terms,
                round_no=context.round_no,
                title_anchor_terms=context.requirement_sheet.title_anchor_terms,
                query_term_pool=context.query_term_pool,
                allowed_inactive_non_anchor_terms=_reflection_backed_inactive_terms(context),
            )
        except ValueError as exc:
            return str(exc)
        from seektalent.runtime.query_identity import build_term_group_key

        term_group_key = build_term_group_key(
            query_terms=canonical_query_terms,
            query_term_pool=context.query_term_pool,
        )
        if term_group_key in context.used_term_group_keys:
            return "proposed_term_group_already_executed"
    if context.previous_reflection is not None and not (decision.response_to_reflection or "").strip():
        return "response_to_reflection is required when previous_reflection exists."
    return None


def project_controller_decision_if_round_legal(
    context: ControllerContext,
    decision: ControllerDecision,
    reason: str,
) -> ControllerDecision | None:
    if _ROUND_SECONDARY_TITLE_ANCHOR_REASON not in reason:
        return None
    if not isinstance(decision, SearchControllerDecision):
        return None
    projected_terms = try_project_secondary_title_anchor_after_round_one(
        decision.proposed_query_terms,
        round_no=context.round_no,
        query_term_pool=context.query_term_pool,
    )
    if projected_terms is None:
        return None
    projected = decision.model_copy(update={"proposed_query_terms": projected_terms})
    if validate_controller_decision(context=context, decision=projected) is not None:
        return None
    return projected


class ReActController:
    def __init__(
        self,
        settings: AppSettings,
        prompt: LoadedPrompt,
        repair_prompt: LoadedPrompt | None = None,
    ) -> None:
        self.settings = settings
        self.prompt = prompt
        self.repair_prompt = repair_prompt or prompt
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []
        self.last_provider_usage: ProviderUsageSnapshot | None = None
        self.last_repair_attempt_count = 0
        self.last_repair_succeeded = False
        self.last_repair_reason: str | None = None
        self.last_full_retry_count = 0
        self.last_repair_call_artifact: dict[str, object] | None = None

    def _record_retry(self, reason: str) -> None:
        self.last_validator_retry_count += 1
        self.last_validator_retry_reasons.append(reason)

    def _reset_metadata(self) -> None:
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons = []
        self.last_provider_usage = None
        self.last_repair_attempt_count = 0
        self.last_repair_succeeded = False
        self.last_repair_reason = None
        self.last_full_retry_count = 0
        self.last_repair_call_artifact = None

    def _get_agent(self, prompt_cache_key: str | None = None) -> Agent[ControllerContext, ControllerDecision]:
        config = resolve_stage_model_config(self.settings, stage="controller")
        model = build_model(config)
        return cast(Agent[ControllerContext, ControllerDecision], Agent(
            model=model,
            output_type=build_output_spec(config, model, ControllerDecision),
            system_prompt=self.prompt.content,
            deps_type=ControllerContext,
            model_settings=build_model_settings(config, prompt_cache_key=prompt_cache_key),
            retries=0,
            output_retries=2,
        ))

    async def _decide_live(
        self,
        *,
        context: ControllerContext,
        prompt_cache_key: str | None = None,
        source_user_prompt: str | None = None,
    ) -> ControllerDecision:
        agent = self._get_agent() if prompt_cache_key is None else self._get_agent(prompt_cache_key=prompt_cache_key)
        result = await agent.run(source_user_prompt or render_controller_prompt(context), deps=context)
        self.last_provider_usage = provider_usage_from_result(result)
        return result.output

    async def decide(
        self,
        *,
        context: ControllerContext,
        prompt_cache_key: str | None = None,
    ) -> ControllerDecision:
        self._reset_metadata()
        total_provider_usage: ProviderUsageSnapshot | None = None
        source_user_prompt = render_controller_prompt(context)
        decision = await self._decide_live(
            context=context,
            prompt_cache_key=prompt_cache_key,
            source_user_prompt=source_user_prompt,
        )
        total_provider_usage = combine_provider_usage(total_provider_usage, self.last_provider_usage)
        self.last_provider_usage = total_provider_usage
        reason = validate_controller_decision(context=context, decision=decision)
        if reason is None:
            return decision

        self._record_retry(reason)
        self.last_repair_attempt_count = 1
        self.last_repair_reason = reason
        try:
            repaired, repair_usage, repair_call_artifact = unpack_repair_result(
                await repair_controller_decision(
                    self.settings,
                    self.prompt,
                    self.repair_prompt,
                    source_user_prompt,
                    decision,
                    reason,
                )
            )
        except RepairCallError as exc:
            self.last_repair_call_artifact = exc.call_artifact
            raise
        self.last_repair_call_artifact = repair_call_artifact
        total_provider_usage = combine_provider_usage(total_provider_usage, repair_usage)
        self.last_provider_usage = total_provider_usage
        repaired_reason = validate_controller_decision(context=context, decision=repaired)
        if repaired_reason is None:
            self.last_repair_succeeded = True
            return repaired

        self.last_full_retry_count = 1
        retried = await self._decide_live(
            context=context,
            prompt_cache_key=prompt_cache_key,
            source_user_prompt=source_user_prompt,
        )
        total_provider_usage = combine_provider_usage(total_provider_usage, self.last_provider_usage)
        self.last_provider_usage = total_provider_usage
        retry_reason = validate_controller_decision(context=context, decision=retried)
        if retry_reason is None:
            return retried
        projected = project_controller_decision_if_round_legal(context, retried, retry_reason)
        if projected is not None:
            return projected
        raise ValueError(retry_reason)
