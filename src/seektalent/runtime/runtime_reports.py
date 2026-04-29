from __future__ import annotations

from collections import Counter

from seektalent.config import AppSettings
from seektalent.models import (
    FinalResult,
    PoolDecision,
    ReflectionAdvice,
    RoundRetrievalPlan,
    RunState,
    ScoredCandidate,
    SearchControllerDecision,
    SearchObservation,
    TerminalControllerRound,
)


def render_run_summary(
    *,
    settings: AppSettings,
    prompt_hashes: dict[str, str],
    run_state: RunState,
    final_result: FinalResult,
    terminal_controller_round: TerminalControllerRound | None,
) -> str:
    lines = [
        "# Run Summary",
        "",
        f"- Run ID: `{final_result.run_id}`",
        f"- Rounds executed: `{final_result.rounds_executed}`",
        f"- Stop reason: `{final_result.stop_reason}`",
        f"- Eval enabled: `{settings.enable_eval}`",
        (
            f"- Text LLM: protocol=`{settings.text_llm_protocol_family}`, "
            f"endpoint=`{settings.text_llm_endpoint_kind}`, region=`{settings.text_llm_endpoint_region}`"
        ),
        (
            f"- Models: requirements=`{settings.requirements_model_id}`, controller=`{settings.controller_model_id}`, "
            f"scoring=`{settings.scoring_model_id}`, reflection=`{settings.reflection_model_id}`, "
            f"finalize=`{settings.finalize_model_id}`"
        ),
        "- Final candidates: `final_candidates.json`",
        "",
        "## Prompt Hashes",
        "",
    ]
    if settings.enable_eval:
        lines.insert(7, "- Judge packet: `judge_packet.json`")
    if terminal_controller_round is not None:
        lines[5:5] = [
            f"- Stop decision round: `{terminal_controller_round.round_no}`",
            f"- Terminal decision: {terminal_controller_round.controller_decision.decision_rationale}",
        ]
    for name, digest in sorted(prompt_hashes.items()):
        lines.append(f"- `{name}`: `{digest}`")
    lines.extend(["", "## Round Index", ""])
    for round_state in run_state.round_history:
        observation = round_state.search_observation
        reflection = round_state.reflection_advice
        lines.append(
            "- "
            f"Round {round_state.round_no}: "
            f"queries=`{len(round_state.cts_queries)}`, "
            f"new=`{observation.unique_new_count if observation else 0}`, "
            f"shortage=`{observation.shortage_count if observation else 0}`, "
            f"global_top=`{', '.join(item.resume_id for item in round_state.top_candidates) or 'None'}`, "
            f"reflection=`{reflection.reflection_summary if reflection else 'none'}`"
        )
    lines.extend(["", "## Final Shortlist", ""])
    for candidate in final_result.candidates:
        lines.append(
            f"- Rank {candidate.rank}: `{candidate.resume_id}` score=`{candidate.final_score}` source_round=`{candidate.source_round}`"
        )
    return "\n".join(lines).strip() + "\n"


def render_run_finished_summary(
    *,
    rounds_executed: int,
    terminal_controller_round: TerminalControllerRound | None,
) -> str:
    if terminal_controller_round is None:
        return f"Run completed after {rounds_executed} retrieval rounds."
    return (
        f"Run completed after {rounds_executed} retrieval rounds; "
        f"controller stopped in round {terminal_controller_round.round_no}."
    )


def render_round_review(
    *,
    round_no: int,
    controller_decision: SearchControllerDecision,
    retrieval_plan: RoundRetrievalPlan,
    observation: SearchObservation,
    newly_scored_count: int,
    pool_decisions: list[PoolDecision],
    top_candidates: list[ScoredCandidate],
    dropped_candidates: list[ScoredCandidate],
    reflection: ReflectionAdvice | None,
    next_step: str,
) -> str:
    selected = [item.resume_id for item in pool_decisions if item.decision == "selected"]
    retained = [item.resume_id for item in pool_decisions if item.decision == "retained"]
    dropped = [item.resume_id for item in pool_decisions if item.decision == "dropped"]
    drop_reason_counter = Counter(
        reason
        for item in pool_decisions
        if item.decision == "dropped"
        for reason in item.reasons_for_rejection
    )
    common_drop_reasons = ", ".join(
        f"{reason} x{count}" for reason, count in drop_reason_counter.most_common(3)
    ) or "None"
    projected_filters = (
        ", ".join(
            f"{field}={value!r}" for field, value in retrieval_plan.projected_provider_filters.items()
        )
        or "None"
    )
    runtime_constraints = (
        ", ".join(
            f"{item.field}={item.normalized_value!r}" for item in retrieval_plan.runtime_only_constraints
        )
        or "None"
    )
    lines = [
        f"# Round {round_no} Review",
        "",
        "## Controller",
        "",
        f"- Thought summary: {controller_decision.thought_summary}",
        f"- Decision rationale: {controller_decision.decision_rationale}",
        f"- Query terms: {', '.join(retrieval_plan.query_terms) or 'None'}",
        f"- Keyword query: `{retrieval_plan.keyword_query}`",
        f"- Projected provider filters: {projected_filters}",
        f"- Runtime-only constraints: {runtime_constraints}",
        "",
        "## Location Execution",
        "",
        f"- Mode: `{retrieval_plan.location_execution_plan.mode}`",
        f"- Allowed locations: {', '.join(retrieval_plan.location_execution_plan.allowed_locations) or 'None'}",
        f"- Preferred locations: {', '.join(retrieval_plan.location_execution_plan.preferred_locations) or 'None'}",
        f"- Priority order: {', '.join(retrieval_plan.location_execution_plan.priority_order) or 'None'}",
        f"- Balanced order: {', '.join(retrieval_plan.location_execution_plan.balanced_order) or 'None'}",
        f"- Rotation offset: `{retrieval_plan.location_execution_plan.rotation_offset}`",
        "",
        "## Search Outcome",
        "",
        f"- Requested new candidates: `{observation.requested_count}`",
        f"- Unique new candidates: `{observation.unique_new_count}`",
        f"- Shortage: `{observation.shortage_count}`",
        f"- Fetch attempts: `{observation.fetch_attempt_count}`",
        f"- Exhausted reason: `{observation.exhausted_reason or 'none'}`",
        f"- Adapter notes: {', '.join(observation.adapter_notes) or 'None'}",
        "",
        "## City Dispatches",
        "",
    ]
    if observation.city_search_summaries:
        for city_summary in observation.city_search_summaries:
            lines.append(
                "- "
                f"{city_summary.query_role} "
                f"{city_summary.city} "
                f"(phase=`{city_summary.phase}`, batch=`{city_summary.batch_no}`): "
                f"requested=`{city_summary.requested_count}`, "
                f"new=`{city_summary.unique_new_count}`, "
                f"shortage=`{city_summary.shortage_count}`, "
                f"next_page=`{city_summary.next_page}`, "
                f"reason=`{city_summary.exhausted_reason or 'none'}`"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Pool Review", ""])
    lines.extend(
        [
            f"- Newly scored this round: `{newly_scored_count}`",
            f"- Current global top pool: {', '.join(candidate.resume_id for candidate in top_candidates) or 'None'}",
            f"- Newly selected: {', '.join(selected) or 'None'}",
            f"- Retained: {', '.join(retained) or 'None'}",
            f"- Dropped from global top pool: {', '.join(dropped) or 'None'}",
            f"- Common drop reasons: {common_drop_reasons}",
            f"- Dropped candidates reviewed: `{len(dropped_candidates)}`",
        ]
    )
    if reflection is not None:
        lines.extend(
            [
                "",
                "## Reflection",
                "",
                f"- Reflection summary: {reflection.reflection_summary}",
                f"- Reflection rationale: {reflection.reflection_rationale or 'None'}",
                f"- Reflection decision: `{'stop' if reflection.suggest_stop else 'continue'}`",
            ]
        )
        if reflection.suggested_stop_reason:
            lines.append(f"- Stop reason: {reflection.suggested_stop_reason}")
    else:
        lines.extend(["", "## Reflection", "", "- Reflection summary: Reflection disabled."])
    lines.extend(["", f"- Next step: `{next_step}`"])
    return "\n".join(lines).strip() + "\n"
