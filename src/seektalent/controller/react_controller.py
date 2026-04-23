from __future__ import annotations

from typing import cast, get_args

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import ControllerContext, ControllerDecision, FilterField, SearchControllerDecision
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.retrieval.query_plan import canonicalize_controller_query_terms


def _items(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- (none)"


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
    latest_search = (
        f"new={latest.unique_new_count}; shortage={latest.shortage_count}; attempts={latest.fetch_attempt_count}"
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
    exact_data = {
        "round_no": context.round_no,
        "action_options": ["search_cts", "stop"],
        "allowed_filter_fields": list(get_args(FilterField)),
        "admitted_terms": [item.term for item in admitted_terms],
        "role_anchor_terms": [item.term for item in admitted_terms if item.retrieval_role == "role_anchor"],
        "stop_guidance_can_stop": context.stop_guidance.can_stop,
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
            f"PREVIOUS REFLECTION\n{previous_reflection}",
            json_block("EXACT DATA", exact_data),
        ]
    )


class ReActController:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    def _record_retry(self, reason: str) -> ModelRetry:
        self.last_validator_retry_count += 1
        self.last_validator_retry_reasons.append(reason)
        return ModelRetry(reason)

    def _get_agent(self) -> Agent[ControllerContext, ControllerDecision]:
        model = build_model(self.settings.controller_model)
        agent = cast(Agent[ControllerContext, ControllerDecision], Agent(
            model=model,
            output_type=build_output_spec(self.settings.controller_model, model, ControllerDecision),
            system_prompt=self.prompt.content,
            deps_type=ControllerContext,
            model_settings=build_model_settings(
                self.settings,
                self.settings.controller_model,
                enable_thinking=self.settings.controller_enable_thinking,
            ),
            retries=0,
            output_retries=2,
        ))

        @agent.output_validator
        def validate_output(
            ctx: RunContext[ControllerContext],
            output: ControllerDecision,
        ) -> ControllerDecision:
            if isinstance(output, SearchControllerDecision) and not output.proposed_query_terms:
                raise self._record_retry("proposed_query_terms must contain at least one term.")
            if isinstance(output, SearchControllerDecision):
                try:
                    canonicalize_controller_query_terms(
                        output.proposed_query_terms,
                        round_no=ctx.deps.round_no,
                        title_anchor_term=ctx.deps.requirement_sheet.title_anchor_term,
                        query_term_pool=ctx.deps.query_term_pool,
                    )
                except ValueError as exc:
                    raise self._record_retry(str(exc)) from exc
            if ctx.deps.previous_reflection is not None and not (output.response_to_reflection or "").strip():
                raise self._record_retry("response_to_reflection is required when previous_reflection exists.")
            return output

        return agent

    async def decide(self, *, context: ControllerContext) -> ControllerDecision:
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons = []
        result = await self._get_agent().run(render_controller_prompt(context), deps=context)
        return result.output
