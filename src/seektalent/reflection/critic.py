from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import (
    ReflectionAdvice,
    ReflectionAdviceDraft,
    ReflectionContext,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
)
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.repair import RepairCallError, repair_reflection_draft, unpack_repair_result
from seektalent.tracing import ProviderUsageSnapshot, combine_provider_usage, provider_usage_from_result


def _join_terms(terms: Iterable[str]) -> str:
    return ", ".join(terms)


def _keyword_summary(keyword_advice: ReflectionKeywordAdvice) -> str:
    parts: list[str] = []
    if keyword_advice.suggested_activate_terms:
        parts.append(f"Activate {_join_terms(keyword_advice.suggested_activate_terms)} from the reserve term bank.")
    if keyword_advice.suggested_keep_terms:
        parts.append(f"Keep {_join_terms(keyword_advice.suggested_keep_terms)}.")
    if keyword_advice.suggested_deprioritize_terms:
        parts.append(f"De-prioritize {_join_terms(keyword_advice.suggested_deprioritize_terms)}.")
    if keyword_advice.suggested_drop_terms:
        parts.append(f"Drop {_join_terms(keyword_advice.suggested_drop_terms)}.")
    return " ".join(parts) if parts else "No keyword changes."


def _filter_summary(draft: ReflectionAdviceDraft) -> str:
    parts: list[str] = []
    if draft.filter_advice.suggested_keep_filter_fields:
        parts.append(f"keep filters {_join_terms(draft.filter_advice.suggested_keep_filter_fields)}")
    if draft.filter_advice.suggested_drop_filter_fields:
        parts.append(f"drop filters {_join_terms(draft.filter_advice.suggested_drop_filter_fields)}")
    if draft.filter_advice.suggested_add_filter_fields:
        parts.append(f"add filters {_join_terms(draft.filter_advice.suggested_add_filter_fields)}")
    return "; ".join(parts) if parts else "no filter changes"


def _clean_rationale(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= 900:
        return cleaned
    return f"{cleaned[:897].rstrip()}..."


def _term_key(term: str) -> str:
    return " ".join(term.strip().split()).casefold()


def _untried_admitted_terms(context: ReflectionContext) -> list[str]:
    term_pool = context.query_term_pool or context.requirement_sheet.initial_query_term_pool
    term_index = {_term_key(item.term): item for item in term_pool}
    tried_families = {
        candidate.family
        for record in context.sent_query_history
        for term in record.query_terms
        if (candidate := term_index.get(_term_key(term))) is not None
    }
    terms: list[str] = []
    seen_families: set[str] = set()
    for item in sorted(
        term_pool,
        key=lambda item: (item.priority, item.first_added_round, item.family),
    ):
        if item.queryability != "admitted" or item.retrieval_role == "role_anchor":
            continue
        if item.family in tried_families or item.family in seen_families:
            continue
        terms.append(item.term)
        seen_families.add(item.family)
    return terms


def _term_bank_rows(context: ReflectionContext) -> str:
    tried_terms = {_term_key(term) for record in context.sent_query_history for term in record.query_terms}
    term_pool = context.query_term_pool or context.requirement_sheet.initial_query_term_pool
    rows = [
        "| term | family | role | queryability | active | priority | source | tried |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in term_pool:
        tried = "yes" if _term_key(item.term) in tried_terms else "no"
        rows.append(
            f"| {item.term} | {item.family} | {item.retrieval_role} | {item.queryability} | "
            f"{item.active} | {item.priority} | {item.source} | {tried} |"
        )
    return "\n".join(rows)


def _top_pool_is_strong(context: ReflectionContext) -> bool:
    strong_fit_count = sum(
        1
        for item in context.top_candidates
        if item.fit_bucket == "fit"
        and item.overall_score >= 80
        and item.must_have_match_score >= 70
        and item.risk_score <= 30
    )
    return len(context.top_candidates) >= 10 and strong_fit_count >= 5


def _candidate_line(candidate, rank: int) -> str:  # noqa: ANN001
    return (
        f"- {rank}. {candidate.resume_id}: {candidate.fit_bucket}, score={candidate.overall_score}, "
        f"must={candidate.must_have_match_score}, risk={candidate.risk_score}; {candidate.reasoning_summary}"
    )


def render_reflection_prompt(context: ReflectionContext) -> str:
    plan = context.current_retrieval_plan
    observation = context.search_observation
    attempts = [
        f"- attempt {item.attempt_no}: raw={item.raw_candidate_count}, "
        f"new={item.batch_unique_new_count}, duplicates={item.batch_duplicate_count}, "
        f"exhausted={item.exhausted_reason or '(none)'}"
        for item in context.search_attempts[:8]
    ]
    top_candidates = [_candidate_line(candidate, index) for index, candidate in enumerate(context.top_candidates[:8], start=1)]
    dropped_candidates = [
        _candidate_line(candidate, index) for index, candidate in enumerate(context.dropped_candidates[:5], start=1)
    ]
    failures = [
        f"- {item.resume_id}: {item.error_message}"
        for item in context.scoring_failures[:5]
    ]
    sent_queries = [
        f"- round {record.round_no}: {', '.join(record.query_terms)}; {record.keyword_query}"
        for record in context.sent_query_history[-8:]
    ]
    exact_data = {
        "round_no": context.round_no,
        "current_query_terms": plan.query_terms,
        "projected_filter_fields": sorted(plan.projected_cts_filters),
        "top_candidate_ids": [item.resume_id for item in context.top_candidates[:8]],
        "dropped_candidate_ids": [item.resume_id for item in context.dropped_candidates[:5]],
        "stop_advice_fields": ["suggest_stop", "suggested_stop_reason"],
    }
    return "\n\n".join(
        [
            (
                "TASK\n"
                "Review this retrieval round and return structured keyword/filter advice, "
                "a reflection_rationale explanation, and stop advice."
            ),
            (
                "REQUIREMENTS\n"
                f"- Role: {context.requirement_sheet.role_title}\n"
                f"- Summary: {context.requirement_sheet.role_summary}\n"
                f"- Must have:\n{_join_terms(context.requirement_sheet.must_have_capabilities) or '(none)'}\n"
                f"- Preferred:\n{_join_terms(context.requirement_sheet.preferred_capabilities) or '(none)'}\n"
                f"- Hard constraints: {context.requirement_sheet.hard_constraints.model_dump(mode='json')}\n"
                f"- Preferences: {context.requirement_sheet.preferences.model_dump(mode='json')}\n"
                f"- JD: {context.full_jd}\n"
                f"- Notes: {context.full_notes or '(none)'}"
            ),
            "TERM BANK\n" + _term_bank_rows(context),
            (
                "ROUND RESULT\n"
                f"- Round: {context.round_no}\n"
                f"- Requested: {observation.requested_count}\n"
                f"- Counts: raw={observation.raw_candidate_count}, new={observation.unique_new_count}, "
                f"shortage={observation.shortage_count}\n"
                f"- Fetch attempts: {observation.fetch_attempt_count}\n"
                f"- Exhausted reason: {observation.exhausted_reason or '(none)'}\n"
                f"- Adapter notes: {', '.join(observation.adapter_notes) or '(none)'}"
            ),
            (
                "CURRENT QUERY\n"
                f"- Terms: {', '.join(plan.query_terms) or '(none)'}\n"
                f"- Keyword query: {plan.keyword_query}\n"
                f"- Non-location filters: {plan.projected_cts_filters or {}}\n"
                f"- Rationale: {plan.rationale}"
            ),
            "SEARCH ATTEMPTS\n" + ("\n".join(attempts) if attempts else "- (none)"),
            "SENT QUERY HISTORY\n" + ("\n".join(sent_queries) if sent_queries else "- (none)"),
            "TOP CANDIDATES\n" + ("\n".join(top_candidates) if top_candidates else "- (empty)"),
            "DROPPED CANDIDATES\n" + ("\n".join(dropped_candidates) if dropped_candidates else "- (none)"),
            "SCORING FAILURES\n" + ("\n".join(failures) if failures else "- (none)"),
            "UNTRIED ADMITTED TERMS\n" + (_join_terms(_untried_admitted_terms(context)) or "(none)"),
            json_block("EXACT DATA", exact_data),
        ]
    )


def validate_reflection_draft(draft: ReflectionAdviceDraft) -> str | None:
    if draft.suggest_stop and not draft.suggested_stop_reason:
        return "suggested_stop_reason is required when suggest_stop is true"
    if not draft.suggest_stop and draft.suggested_stop_reason is not None:
        return "suggested_stop_reason must be null when suggest_stop is false"
    return None


def repair_reflection_stop_fields(draft: ReflectionAdviceDraft) -> ReflectionAdviceDraft:
    if not draft.suggest_stop and draft.suggested_stop_reason is not None:
        return draft.model_copy(update={"suggested_stop_reason": None})
    return draft


def materialize_reflection_advice(*, context: ReflectionContext, draft: ReflectionAdviceDraft) -> ReflectionAdvice:
    keyword_advice = ReflectionKeywordAdvice(
        suggested_activate_terms=draft.keyword_advice.suggested_activate_terms,
        suggested_keep_terms=draft.keyword_advice.suggested_keep_terms,
        suggested_deprioritize_terms=draft.keyword_advice.suggested_deprioritize_terms,
        suggested_drop_terms=draft.keyword_advice.suggested_drop_terms,
    )
    keyword_summary = _keyword_summary(keyword_advice)
    untried_terms = _untried_admitted_terms(context)
    suppress_reflection_stop_advice = draft.suggest_stop and untried_terms and not _top_pool_is_strong(context)
    suggest_stop = draft.suggest_stop and not suppress_reflection_stop_advice
    suggested_stop_reason = draft.suggested_stop_reason if suggest_stop else None
    new_count = context.search_observation.unique_new_count
    noun = "candidate" if new_count == 1 else "candidates"
    summary_parts = [f"Round {context.round_no} yielded {new_count} new {noun}."]
    summary_parts.append(f"Keywords: {keyword_summary}")
    summary_parts.append(f"Filters: {_filter_summary(draft)}.")
    if suggest_stop and suggested_stop_reason:
        summary_parts.append(f"Stop: {suggested_stop_reason}.")
    elif suppress_reflection_stop_advice:
        summary_parts.append(f"Continue: untried admitted reserve terms remain: {_join_terms(untried_terms[:3])}.")
    else:
        summary_parts.append("Continue.")
    return ReflectionAdvice(
        keyword_advice=keyword_advice,
        filter_advice=ReflectionFilterAdvice(
            suggested_keep_filter_fields=draft.filter_advice.suggested_keep_filter_fields,
            suggested_drop_filter_fields=draft.filter_advice.suggested_drop_filter_fields,
            suggested_add_filter_fields=draft.filter_advice.suggested_add_filter_fields,
        ),
        reflection_rationale=_clean_rationale(draft.reflection_rationale),
        suggest_stop=suggest_stop,
        suggested_stop_reason=suggested_stop_reason,
        reflection_summary=" ".join(summary_parts),
    )


class ReflectionCritic:
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

    def _get_agent(self, prompt_cache_key: str | None = None) -> Agent[None, ReflectionAdviceDraft]:
        model = build_model(self.settings.reflection_model)
        return cast(Agent[None, ReflectionAdviceDraft], Agent(
            model=model,
            output_type=build_output_spec(self.settings.reflection_model, model, ReflectionAdviceDraft),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(
                self.settings,
                self.settings.reflection_model,
                enable_thinking=self.settings.reflection_enable_thinking,
                prompt_cache_key=prompt_cache_key,
            ),
            retries=0,
            output_retries=2,
        ))

    async def _reflect_live(
        self,
        *,
        context: ReflectionContext,
        prompt_cache_key: str | None = None,
        source_user_prompt: str | None = None,
    ) -> ReflectionAdviceDraft:
        agent = self._get_agent() if prompt_cache_key is None else self._get_agent(prompt_cache_key=prompt_cache_key)
        result = await agent.run(source_user_prompt or render_reflection_prompt(context))
        self.last_provider_usage = provider_usage_from_result(result)
        return result.output

    async def reflect(
        self,
        *,
        context: ReflectionContext,
        prompt_cache_key: str | None = None,
    ) -> ReflectionAdvice:
        self._reset_metadata()
        total_provider_usage: ProviderUsageSnapshot | None = None
        source_user_prompt = render_reflection_prompt(context)
        draft = await self._reflect_live(
            context=context,
            prompt_cache_key=prompt_cache_key,
            source_user_prompt=source_user_prompt,
        )
        total_provider_usage = combine_provider_usage(total_provider_usage, self.last_provider_usage)
        self.last_provider_usage = total_provider_usage
        reason = validate_reflection_draft(draft)
        if reason is None:
            return materialize_reflection_advice(context=context, draft=draft)

        self._record_retry(reason)

        repaired = repair_reflection_stop_fields(draft)
        repaired_reason = validate_reflection_draft(repaired)
        if repaired_reason is not None:
            self.last_repair_attempt_count = 1
            self.last_repair_reason = repaired_reason
            try:
                repaired, repair_usage, repair_call_artifact = unpack_repair_result(
                    await repair_reflection_draft(
                        self.settings,
                        self.prompt,
                        self.repair_prompt,
                        source_user_prompt,
                        repaired,
                        repaired_reason,
                    )
                )
            except RepairCallError as exc:
                self.last_repair_call_artifact = exc.call_artifact
                raise
            self.last_repair_call_artifact = repair_call_artifact
            total_provider_usage = combine_provider_usage(total_provider_usage, repair_usage)
            self.last_provider_usage = total_provider_usage
            repaired_reason = validate_reflection_draft(repaired)
        if repaired_reason is None:
            if self.last_repair_attempt_count > 0:
                self.last_repair_succeeded = True
            return materialize_reflection_advice(context=context, draft=repaired)

        self.last_full_retry_count = 1
        retried = await self._reflect_live(
            context=context,
            prompt_cache_key=prompt_cache_key,
            source_user_prompt=source_user_prompt,
        )
        total_provider_usage = combine_provider_usage(total_provider_usage, self.last_provider_usage)
        self.last_provider_usage = total_provider_usage
        retry_reason = validate_reflection_draft(retried)
        if retry_reason is None:
            return materialize_reflection_advice(context=context, draft=retried)
        raise ValueError(retry_reason)
