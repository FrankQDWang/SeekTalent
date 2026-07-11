from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from seektalent.models import (
    ControllerContext,
    ControllerDecision,
    ReflectionAdvice,
    RunState,
    SearchControllerDecision,
    StopControllerDecision,
)
from seektalent.progress import ProgressCallback
from seektalent.core.filter_plan import build_default_filter_plan, canonicalize_filter_plan
from seektalent.retrieval import (
    canonicalize_controller_query_terms,
    select_query_terms,
    try_project_secondary_title_anchor_after_round_one,
)
from seektalent.retrieval.query_plan import normalize_term
from seektalent.retrieval.query_plan import _ROUND_SECONDARY_TITLE_ANCHOR_REASON
from seektalent.runtime.rescue_router import RescueDecision, SkippedRescueLane
from seektalent.runtime.query_identity import consumed_non_anchor_term_family_ids
from seektalent.runtime import rescue_execution_runtime
from seektalent.tracing import RunTracer


async def resolve_pre_controller_exhaustion(
    *,
    run_state: RunState,
    round_no: int,
    controller_context: ControllerContext,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    candidate_feedback_enabled: bool,
    force_broaden_decision: Callable[..., SearchControllerDecision],
    force_candidate_feedback_decision: Callable[..., SearchControllerDecision | None],
    continue_after_empty_feedback: Callable[..., Awaitable[RescueDecision]],
    force_anchor_only_decision: Callable[..., SearchControllerDecision],
    write_rescue_decision: Callable[..., None],
) -> tuple[ControllerDecision, RescueDecision] | None:
    """Resolve query-family exhaustion without spending a controller call."""
    if _has_fresh_controller_selectable_family(run_state):
        return None

    skipped: list[SkippedRescueLane] = []
    reserve = _fresh_inactive_reserve(run_state)
    if reserve is not None:
        decision = RescueDecision(selected_lane="reserve_broaden")
        controller_decision: ControllerDecision = force_broaden_decision(
            run_state=run_state,
            round_no=round_no,
            reason="query family exhaustion preflight",
        )
    else:
        skipped.append(SkippedRescueLane(lane="reserve_broaden", reason="no_untried_reserve_family"))
        if candidate_feedback_enabled and not run_state.retrieval_state.candidate_feedback_attempted:
            decision = RescueDecision(selected_lane="candidate_feedback", skipped_lanes=skipped)
            feedback_decision = force_candidate_feedback_decision(
                run_state=run_state,
                round_no=round_no,
                reason="query family exhaustion preflight",
                tracer=tracer,
                progress_callback=progress_callback,
            )
            if feedback_decision is not None:
                controller_decision = feedback_decision
            else:
                decision = await continue_after_empty_feedback(
                    run_state=run_state,
                    controller_context=controller_context,
                    round_no=round_no,
                    tracer=tracer,
                    rescue_decision=decision,
                    progress_callback=progress_callback,
                )
                if decision.selected_lane == "anchor_only":
                    run_state.retrieval_state.anchor_only_broaden_attempted = True
                    controller_decision = force_anchor_only_decision(
                        run_state=run_state,
                        round_no=round_no,
                        reason="query family exhaustion preflight",
                    )
                else:
                    controller_decision = _family_exhausted_stop()
        elif rescue_execution_runtime.has_novel_anchor_only_group(run_state.retrieval_state):
            skipped.append(
                SkippedRescueLane(
                    lane="candidate_feedback",
                    reason="disabled" if not candidate_feedback_enabled else "already_attempted",
                )
            )
            decision = RescueDecision(selected_lane="anchor_only", skipped_lanes=skipped)
            run_state.retrieval_state.anchor_only_broaden_attempted = True
            controller_decision = force_anchor_only_decision(
                run_state=run_state,
                round_no=round_no,
                reason="query family exhaustion preflight",
            )
        else:
            skipped.extend(
                [
                    SkippedRescueLane(
                        lane="candidate_feedback",
                        reason="disabled" if not candidate_feedback_enabled else "already_attempted",
                    ),
                    SkippedRescueLane(lane="anchor_only", reason="no_novel_anchor_only_query"),
                ]
            )
            decision = RescueDecision(selected_lane="allow_stop", skipped_lanes=skipped)
            controller_decision = _family_exhausted_stop()

    run_state.retrieval_state.rescue_lane_history.append(
        {"round_no": round_no, "selected_lane": decision.selected_lane}
    )
    write_rescue_decision(
        tracer=tracer,
        round_no=round_no,
        controller_context=controller_context,
        decision=decision,
        forced_query_terms=(
            controller_decision.proposed_query_terms
            if isinstance(controller_decision, SearchControllerDecision)
            else []
        ),
    )
    tracer.session.register_path(
        f"round.{round_no:02d}.controller.controller_decision",
        f"rounds/{round_no:02d}/controller/controller_decision.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.write_json(
        f"round.{round_no:02d}.controller.controller_decision",
        controller_decision.model_dump(mode="json"),
    )
    return controller_decision, decision


def _has_fresh_controller_selectable_family(run_state: RunState) -> bool:
    consumed = consumed_non_anchor_term_family_ids(run_state.retrieval_state.query_execution_ledger)
    allowed_inactive = reflection_backed_inactive_terms(
        run_state.round_history[-1].reflection_advice if run_state.round_history else None
    )
    return any(
        item.queryability == "admitted"
        and item.retrieval_role not in {"primary_role_anchor", "role_anchor", "secondary_title_anchor"}
        and item.family not in consumed
        and (item.active or normalize_term(item.term).casefold() in allowed_inactive)
        for item in run_state.retrieval_state.query_term_pool
    )


def _fresh_inactive_reserve(run_state: RunState):
    reserve = rescue_execution_runtime.untried_admitted_non_anchor_reserve(run_state.retrieval_state)
    return reserve if reserve is not None and not reserve.active else None


def _family_exhausted_stop() -> StopControllerDecision:
    return StopControllerDecision(
        thought_summary="Runtime terminal: no fresh query family remains.",
        action="stop",
        decision_rationale="All legal query-family rescue routes are exhausted.",
        response_to_reflection="Runtime exhaustion takes precedence over ordinary stop guidance.",
        stop_reason="query_family_exhausted",
    )


async def resolve_round_decision(
    *,
    run_state: RunState,
    round_no: int,
    max_rounds: int,
    controller_context: ControllerContext,
    controller_decision: ControllerDecision,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    choose_rescue_decision: Callable[..., RescueDecision],
    force_broaden_decision: Callable[..., SearchControllerDecision],
    force_candidate_feedback_decision: Callable[..., SearchControllerDecision | None],
    continue_after_empty_feedback: Callable[..., Awaitable[RescueDecision]],
    force_anchor_only_decision: Callable[..., SearchControllerDecision],
    write_rescue_decision: Callable[..., None],
) -> tuple[ControllerDecision, RescueDecision | None]:
    rescue_decision: RescueDecision | None = None
    if controller_context.stop_guidance.quality_gate_status in {"broaden_required", "low_quality_exhausted"}:
        rescue_decision = choose_rescue_decision(
            run_state=run_state,
            controller_context=controller_context,
            round_no=round_no,
        )
        if rescue_decision.selected_lane == "reserve_broaden":
            controller_decision = force_broaden_decision(
                run_state=run_state,
                round_no=round_no,
                reason=controller_context.stop_guidance.reason,
            )
        elif rescue_decision.selected_lane == "candidate_feedback":
            feedback_decision = force_candidate_feedback_decision(
                run_state=run_state,
                round_no=round_no,
                reason=controller_context.stop_guidance.reason,
                tracer=tracer,
                progress_callback=progress_callback,
            )
            if feedback_decision is None:
                rescue_decision = await continue_after_empty_feedback(
                    run_state=run_state,
                    controller_context=controller_context,
                    round_no=round_no,
                    tracer=tracer,
                    rescue_decision=rescue_decision,
                    progress_callback=progress_callback,
                )
                run_state.retrieval_state.rescue_lane_history[-1]["selected_lane"] = rescue_decision.selected_lane
                if rescue_decision.selected_lane == "anchor_only":
                    run_state.retrieval_state.anchor_only_broaden_attempted = True
                    controller_decision = force_anchor_only_decision(
                        run_state=run_state,
                        round_no=round_no,
                        reason=controller_context.stop_guidance.reason,
                    )
                elif rescue_decision.selected_lane == "allow_stop":
                    controller_decision = sanitize_controller_decision(
                        decision=controller_decision,
                        run_state=run_state,
                        round_no=round_no,
                        max_rounds=max_rounds,
                    )
                else:
                    controller_decision = sanitize_controller_decision(
                        decision=controller_decision,
                        run_state=run_state,
                        round_no=round_no,
                        max_rounds=max_rounds,
                    )
                    _raise_if_stop_disallowed(controller_context=controller_context, decision=controller_decision)
            else:
                controller_decision = feedback_decision
        elif rescue_decision.selected_lane == "anchor_only":
            run_state.retrieval_state.anchor_only_broaden_attempted = True
            controller_decision = force_anchor_only_decision(
                run_state=run_state,
                round_no=round_no,
                reason=controller_context.stop_guidance.reason,
            )
        elif rescue_decision.selected_lane == "allow_stop":
            controller_decision = sanitize_controller_decision(
                decision=controller_decision,
                run_state=run_state,
                round_no=round_no,
                max_rounds=max_rounds,
            )
        else:
            controller_decision = sanitize_controller_decision(
                decision=controller_decision,
                run_state=run_state,
                round_no=round_no,
                max_rounds=max_rounds,
            )
            _raise_if_stop_disallowed(controller_context=controller_context, decision=controller_decision)
    else:
        controller_decision = sanitize_controller_decision(
            decision=controller_decision,
            run_state=run_state,
            round_no=round_no,
            max_rounds=max_rounds,
        )
        _raise_if_stop_disallowed(controller_context=controller_context, decision=controller_decision)
    if rescue_decision is not None:
        write_rescue_decision(
            tracer=tracer,
            round_no=round_no,
            controller_context=controller_context,
            decision=rescue_decision,
            forced_query_terms=(
                controller_decision.proposed_query_terms
                if isinstance(controller_decision, SearchControllerDecision)
                else []
            ),
        )
    return controller_decision, rescue_decision


def _raise_if_stop_disallowed(*, controller_context: ControllerContext, decision: ControllerDecision) -> None:
    if isinstance(decision, StopControllerDecision) and not controller_context.stop_guidance.can_stop:
        raise ValueError(
            "controller_stop_not_allowed:"
            f"{controller_context.stop_guidance.reason}"
        )


def sanitize_controller_decision(
    *,
    decision: ControllerDecision,
    run_state: RunState,
    round_no: int,
    max_rounds: int,
) -> ControllerDecision:
    previous_reflection = run_state.round_history[-1].reflection_advice if run_state.round_history else None
    allowed_inactive_terms = reflection_backed_inactive_terms(previous_reflection)
    if previous_reflection is not None and not (decision.response_to_reflection or "").strip():
        raise ValueError("response_to_reflection is required after a reflection round")
    if isinstance(decision, StopControllerDecision):
        return decision.model_copy(
            update={
                "decision_rationale": sanitize_premature_max_round_claim(
                    decision.decision_rationale,
                    round_no=round_no,
                    max_rounds=max_rounds,
                ),
                "stop_reason": sanitize_premature_max_round_claim(
                    decision.stop_reason,
                    round_no=round_no,
                    max_rounds=max_rounds,
                ),
            }
        )
    try:
        query_terms = canonicalize_controller_query_terms(
            decision.proposed_query_terms,
            round_no=round_no,
            title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
            query_term_pool=run_state.retrieval_state.query_term_pool,
            allowed_inactive_non_anchor_terms=allowed_inactive_terms,
            allow_anchor_only=True,
        )
    except ValueError as exc:
        if not str(exc).startswith(_ROUND_SECONDARY_TITLE_ANCHOR_REASON):
            raise
        projected_terms = try_project_secondary_title_anchor_after_round_one(
            decision.proposed_query_terms,
            round_no=round_no,
            query_term_pool=run_state.retrieval_state.query_term_pool,
        )
        if projected_terms is None:
            raise
        query_terms = canonicalize_controller_query_terms(
            projected_terms,
            round_no=round_no,
            title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
            query_term_pool=run_state.retrieval_state.query_term_pool,
            allowed_inactive_non_anchor_terms=allowed_inactive_terms,
            allow_anchor_only=True,
        )
    filter_plan = canonicalize_filter_plan(
        requirement_sheet=run_state.requirement_sheet,
        filter_plan=decision.proposed_filter_plan,
    )
    query_terms = _repair_consumed_families(
        query_terms=query_terms,
        run_state=run_state,
        allowed_inactive_terms=allowed_inactive_terms,
    )
    from seektalent.retrieval.query_identity import build_term_group_key
    from seektalent.runtime.query_identity import used_term_group_keys

    term_group_key = build_term_group_key(
        query_terms=query_terms,
        query_term_pool=run_state.retrieval_state.query_term_pool,
    )
    if term_group_key in used_term_group_keys(run_state.retrieval_state.query_execution_ledger):
        raise ValueError("proposed_term_group_already_executed")
    return decision.model_copy(
        update={
            "proposed_query_terms": query_terms,
            "proposed_filter_plan": filter_plan,
            "stop_reason": None,
        }
    )


def _repair_consumed_families(
    *, query_terms: list[str], run_state: RunState, allowed_inactive_terms: set[str]
) -> list[str]:
    pool = run_state.retrieval_state.query_term_pool
    index = {normalize_term(item.term).casefold(): item for item in pool}
    consumed = consumed_non_anchor_term_family_ids(run_state.retrieval_state.query_execution_ledger)
    selected = [index[normalize_term(term).casefold()] for term in query_terms]
    anchors = [item for item in selected if item.retrieval_role in {"primary_role_anchor", "role_anchor"}]
    fresh = [item for item in selected if item not in anchors and item.family not in consumed]
    target = len([item for item in selected if item not in anchors])
    if target == 0:
        from seektalent.retrieval.query_identity import build_term_group_key
        from seektalent.runtime.query_identity import used_term_group_keys

        current_key = build_term_group_key(query_terms=query_terms, query_term_pool=pool)
        if current_key in used_term_group_keys(run_state.retrieval_state.query_execution_ledger):
            target = 1
    seen = {item.family for item in fresh}
    selectable = sorted(
        (
            item for item in pool
            if item.queryability == "admitted"
            and item.retrieval_role not in {"primary_role_anchor", "role_anchor", "secondary_title_anchor"}
            and item.family not in consumed
            and item.family not in seen
            and (item.active or normalize_term(item.term).casefold() in allowed_inactive_terms)
        ),
        key=lambda item: (item.priority, item.first_added_round, item.family, item.term.casefold()),
    )
    for item in selectable:
        fresh.append(item)
        seen.add(item.family)
        if len(fresh) >= target:
            break
    if target and not fresh:
        raise ValueError("no_fresh_controller_selectable_family")
    return [*(item.term for item in anchors[:1]), *(item.term for item in fresh[:target])]


def reflection_backed_inactive_terms(reflection_advice: ReflectionAdvice | None) -> set[str]:
    if reflection_advice is None:
        return set()
    advice = reflection_advice.keyword_advice
    return {
        normalize_term(term).casefold()
        for term in [
            *advice.suggested_activate_terms,
            *advice.suggested_keep_terms,
        ]
    }


def sanitize_premature_max_round_claim(text: str, *, round_no: int, max_rounds: int) -> str:
    if round_no >= max_rounds:
        return text
    lowered = text.casefold()
    if "max rounds" not in lowered and "maximum rounds" not in lowered:
        return text
    cleaned = re.sub(
        r"(?i)the search has reached the maximum rounds \(\d+\),\s*",
        "The search appears exhausted with diminishing returns, ",
        text,
    )
    cleaned = re.sub(
        r"(?i)search is exhausted:\s*max(?:imum)? rounds? reached,\s*",
        "Search is exhausted with diminishing returns; ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\bmax(?:imum)? rounds? reached\b[:,]?\s*",
        "diminishing returns, ",
        cleaned,
    )
    return " ".join(cleaned.split())


def force_continue_decision(*, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
    return SearchControllerDecision(
        thought_summary="Runtime override: stop guidance requires continuing.",
        action="source_search",
        decision_rationale=f"Runtime stop guidance requires continuing: {reason}",
        proposed_query_terms=select_query_terms(
            run_state.retrieval_state.query_term_pool,
            round_no=round_no,
            title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
        ),
        proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
        response_to_reflection=f"Runtime override: {reason}",
    )
