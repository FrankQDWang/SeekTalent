from __future__ import annotations

from typing import cast, get_args

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import ControllerContext, ControllerDecision, FilterField, SearchControllerDecision
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.repair import repair_controller_decision
from seektalent.retrieval.query_plan import canonicalize_controller_query_terms, normalize_term
from seektalent.tracing import ProviderUsageSnapshot, combine_provider_usage, provider_usage_from_result


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


def render_controller_prompt(context: ControllerContext) -> str:
    sheet = context.requirement_sheet
    admitted_terms = [item for item in context.query_term_pool if item.queryability == "admitted"]
    term_rows = [
        "| term | family | role | priority | active | tried |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    tried_terms = {term.casefold() for record in context.sent_query_history for term in record.query_terms}
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
        f"- round {record.round_no}: {', '.join(record.query_terms)}; {record.keyword_query}"
        for record in context.sent_query_history[-6:]
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
    elif context.previous_reflection.reflection_rationale:
        previous_reflection = (
            f"{context.previous_reflection.decision}: {context.previous_reflection.reflection_summary} "
            f"Rationale: {context.previous_reflection.reflection_rationale}"
        )
    else:
        previous_reflection = f"{context.previous_reflection.decision}: {context.previous_reflection.reflection_summary}"
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
        "hard_constraints": sheet.hard_constraints.model_dump(mode="json"),
        "preferences": sheet.preferences.model_dump(mode="json"),
    }
    exact_data = {
        "round_no": context.round_no,
        "action_options": ["search_cts", "stop"],
        "allowed_filter_fields": list(get_args(FilterField)),
        "admitted_terms": [item.term for item in admitted_terms],
        "role_anchor_terms": [item.term for item in admitted_terms if item.retrieval_role == "role_anchor"],
        "stop_guidance_can_stop": context.stop_guidance.can_stop,
        "quality_gate_status": context.stop_guidance.quality_gate_status,
    }
    return "\n\n".join(
        [
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
            (
                "STOP GUIDANCE\n"
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
            ),
            (
                "REQUIREMENTS\n"
                f"- Role: {sheet.role_title}\n"
                f"- Summary: {sheet.role_summary}\n"
                f"- Must have:\n{_items(sheet.must_have_capabilities)}\n"
                f"- Preferred:\n{_items(sheet.preferred_capabilities)}\n"
                f"- Scoring rationale: {sheet.scoring_rationale}\n"
                f"- JD: {context.full_jd}\n"
                f"- Notes: {context.full_notes or '(none)'}"
            ),
            "TERM BANK\n" + "\n".join(term_rows),
            "SENT QUERY HISTORY\n" + ("\n".join(query_history) if query_history else "- (none)"),
            f"LATEST SEARCH OBSERVATION\n{latest_search}",
            "CURRENT TOP POOL\n" + ("\n".join(top_pool) if top_pool else "- (empty)"),
            json_block("STRUCTURED CONSTRAINTS", structured_constraints),
            json_block("REFLECTION ADVICE", reflection_advice),
            f"PREVIOUS REFLECTION\n{previous_reflection}",
            json_block("EXACT DATA", exact_data),
        ]
    )


def validate_controller_decision(*, context: ControllerContext, decision: ControllerDecision) -> str | None:
    if isinstance(decision, SearchControllerDecision) and not decision.proposed_query_terms:
        return "proposed_query_terms must contain at least one term."
    if isinstance(decision, SearchControllerDecision):
        try:
            canonicalize_controller_query_terms(
                decision.proposed_query_terms,
                round_no=context.round_no,
                title_anchor_term=context.requirement_sheet.title_anchor_term,
                query_term_pool=context.query_term_pool,
                allowed_inactive_non_anchor_terms=_reflection_backed_inactive_terms(context),
            )
        except ValueError as exc:
            return str(exc)
    if context.previous_reflection is not None and not (decision.response_to_reflection or "").strip():
        return "response_to_reflection is required when previous_reflection exists."
    return None


class ReActController:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []
        self.last_provider_usage: ProviderUsageSnapshot | None = None
        self.last_repair_attempt_count = 0
        self.last_repair_succeeded = False
        self.last_repair_reason: str | None = None
        self.last_full_retry_count = 0

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

    def _get_agent(self, prompt_cache_key: str | None = None) -> Agent[ControllerContext, ControllerDecision]:
        model = build_model(self.settings.controller_model)
        return cast(Agent[ControllerContext, ControllerDecision], Agent(
            model=model,
            output_type=build_output_spec(self.settings.controller_model, model, ControllerDecision),
            system_prompt=self.prompt.content,
            deps_type=ControllerContext,
            model_settings=build_model_settings(
                self.settings,
                self.settings.controller_model,
                enable_thinking=self.settings.controller_enable_thinking,
                prompt_cache_key=prompt_cache_key,
            ),
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
        repaired, repair_usage = await repair_controller_decision(
            self.settings,
            self.prompt,
            source_user_prompt,
            decision,
            reason,
        )
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
        raise ValueError(retry_reason)
