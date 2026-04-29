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
from seektalent.providers.cts.filter_projection import build_default_filter_plan, canonicalize_filter_plan
from seektalent.retrieval import canonicalize_controller_query_terms, select_query_terms
from seektalent.retrieval.query_plan import normalize_term
from seektalent.runtime.rescue_router import RescueDecision
from seektalent.tracing import RunTracer


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
                else:
                    controller_decision = sanitize_controller_decision(
                        decision=controller_decision,
                        run_state=run_state,
                        round_no=round_no,
                        max_rounds=max_rounds,
                    )
                    if isinstance(controller_decision, StopControllerDecision) and not controller_context.stop_guidance.can_stop:
                        controller_decision = force_continue_decision(
                            run_state=run_state,
                            round_no=round_no,
                            reason=controller_context.stop_guidance.reason,
                        )
            else:
                controller_decision = feedback_decision
        elif rescue_decision.selected_lane == "anchor_only":
            run_state.retrieval_state.anchor_only_broaden_attempted = True
            controller_decision = force_anchor_only_decision(
                run_state=run_state,
                round_no=round_no,
                reason=controller_context.stop_guidance.reason,
            )
        else:
            controller_decision = sanitize_controller_decision(
                decision=controller_decision,
                run_state=run_state,
                round_no=round_no,
                max_rounds=max_rounds,
            )
            if isinstance(controller_decision, StopControllerDecision) and not controller_context.stop_guidance.can_stop:
                controller_decision = force_continue_decision(
                    run_state=run_state,
                    round_no=round_no,
                    reason=controller_context.stop_guidance.reason,
                )
    else:
        controller_decision = sanitize_controller_decision(
            decision=controller_decision,
            run_state=run_state,
            round_no=round_no,
            max_rounds=max_rounds,
        )
        if isinstance(controller_decision, StopControllerDecision) and not controller_context.stop_guidance.can_stop:
            controller_decision = force_continue_decision(
                run_state=run_state,
                round_no=round_no,
                reason=controller_context.stop_guidance.reason,
            )
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
    query_terms = canonicalize_controller_query_terms(
        decision.proposed_query_terms,
        round_no=round_no,
        title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
        query_term_pool=run_state.retrieval_state.query_term_pool,
        allowed_inactive_non_anchor_terms=allowed_inactive_terms,
    )
    filter_plan = canonicalize_filter_plan(
        requirement_sheet=run_state.requirement_sheet,
        filter_plan=decision.proposed_filter_plan,
    )
    return decision.model_copy(
        update={
            "proposed_query_terms": query_terms,
            "proposed_filter_plan": filter_plan,
            "stop_reason": None,
        }
    )


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
        action="search_cts",
        decision_rationale=f"Runtime stop guidance requires continuing: {reason}",
        proposed_query_terms=select_query_terms(
            run_state.retrieval_state.query_term_pool,
            round_no=round_no,
            title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
        ),
        proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
        response_to_reflection=f"Runtime override: {reason}",
    )
