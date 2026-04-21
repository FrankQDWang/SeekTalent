from __future__ import annotations

from math import ceil

from seektalent.models import (
    ControllerContext,
    FinalizeContext,
    QueryTermCandidate,
    ReflectionContext,
    ReflectionSummaryView,
    RoundState,
    RunState,
    ScoredCandidate,
    ScoringContext,
    SearchObservationView,
    StopGuidance,
    TopPoolEntryView,
    TopPoolStrength,
    unique_strings,
)
from seektalent.requirements import build_requirement_digest

BUDGET_STOP_RATIO = 0.8
STRONG_FIT_STOP_MIN = 3
HIGH_RISK_FIT_THRESHOLD = 70


def build_controller_context(
    *,
    run_state: RunState,
    round_no: int,
    min_rounds: int,
    max_rounds: int,
    target_new: int,
) -> ControllerContext:
    last_round = run_state.round_history[-1] if run_state.round_history else None
    previous_reflection = last_round.reflection_advice if last_round is not None else None
    latest_search_observation = last_round.search_observation if last_round is not None else None
    top_pool = top_candidates(run_state)
    retrieval_rounds_completed = len(run_state.round_history)
    rounds_remaining_after_current = max(0, max_rounds - round_no)
    budget_used_ratio = round_no / max_rounds
    budget_stop_round = ceil(max_rounds * BUDGET_STOP_RATIO)
    near_budget_limit = round_no >= budget_stop_round
    return ControllerContext(
        full_jd=run_state.input_truth.jd,
        full_notes=run_state.input_truth.notes,
        requirement_sheet=run_state.requirement_sheet,
        round_no=round_no,
        min_rounds=min_rounds,
        max_rounds=max_rounds,
        retrieval_rounds_completed=retrieval_rounds_completed,
        rounds_remaining_after_current=rounds_remaining_after_current,
        budget_used_ratio=budget_used_ratio,
        near_budget_limit=near_budget_limit,
        is_final_allowed_round=round_no >= max_rounds,
        target_new=target_new,
        stop_guidance=_build_stop_guidance(
            run_state=run_state,
            top_pool=top_pool,
            round_no=round_no,
            retrieval_rounds_completed=retrieval_rounds_completed,
            min_rounds=min_rounds,
            max_rounds=max_rounds,
        ),
        requirement_digest=build_requirement_digest(run_state.requirement_sheet),
        query_term_pool=run_state.retrieval_state.query_term_pool,
        current_top_pool=[_top_pool_entry(item) for item in top_pool],
        latest_search_observation=_search_observation_view(latest_search_observation),
        previous_reflection=_reflection_summary(previous_reflection),
        latest_reflection_keyword_advice=previous_reflection.keyword_advice if previous_reflection else None,
        latest_reflection_filter_advice=previous_reflection.filter_advice if previous_reflection else None,
        shortage_history=[
            round_state.search_observation.shortage_count
            for round_state in run_state.round_history
            if round_state.search_observation is not None
        ],
        budget_reminder=_budget_reminder(
            round_no=round_no,
            retrieval_rounds_completed=retrieval_rounds_completed,
            max_rounds=max_rounds,
            budget_stop_round=budget_stop_round,
            rounds_remaining_after_current=rounds_remaining_after_current,
            near_budget_limit=near_budget_limit,
        ),
    )


def build_scoring_context(
    *,
    run_state: RunState,
    round_no: int,
    normalized_resume,
) -> ScoringContext:
    return ScoringContext(
        round_no=round_no,
        scoring_policy=run_state.scoring_policy,
        normalized_resume=normalized_resume,
    )


def build_reflection_context(
    *,
    run_state: RunState,
    round_state: RoundState,
) -> ReflectionContext:
    if round_state.search_observation is None:
        raise ValueError("round_state.search_observation is required for reflection context")
    return ReflectionContext(
        round_no=round_state.round_no,
        full_jd=run_state.input_truth.jd,
        full_notes=run_state.input_truth.notes,
        requirement_sheet=run_state.requirement_sheet,
        current_retrieval_plan=round_state.retrieval_plan,
        search_observation=round_state.search_observation,
        search_attempts=round_state.search_attempts,
        top_candidates=round_state.top_candidates or top_candidates(run_state),
        dropped_candidates=dropped_candidates(run_state, round_state),
        scoring_failures=[],
        sent_query_history=run_state.retrieval_state.sent_query_history,
    )


def build_finalize_context(
    *,
    run_state: RunState,
    rounds_executed: int,
    stop_reason: str,
    run_id: str,
    run_dir: str,
) -> FinalizeContext:
    return FinalizeContext(
        run_id=run_id,
        run_dir=run_dir,
        rounds_executed=rounds_executed,
        stop_reason=stop_reason,
        top_candidates=top_candidates(run_state),
        requirement_digest=build_requirement_digest(run_state.requirement_sheet),
        sent_query_history=run_state.retrieval_state.sent_query_history,
    )


def top_candidates(run_state: RunState) -> list[ScoredCandidate]:
    return [
        run_state.scorecards_by_resume_id[resume_id]
        for resume_id in run_state.top_pool_ids
        if resume_id in run_state.scorecards_by_resume_id
    ]


def _build_stop_guidance(
    *,
    run_state: RunState,
    top_pool: list[ScoredCandidate],
    round_no: int,
    retrieval_rounds_completed: int,
    min_rounds: int,
    max_rounds: int,
) -> StopGuidance:
    top_pool_strength = _top_pool_strength(top_pool)
    fit_candidates = [item for item in top_pool if item.fit_bucket == "fit"]
    strong_fit_count = len(_strong_fit_candidates(top_pool))
    high_risk_fit_count = sum(1 for item in fit_candidates if item.risk_score >= HIGH_RISK_FIT_THRESHOLD)
    tried_families = _tried_families(
        run_state.retrieval_state.query_term_pool,
        run_state.retrieval_state.sent_query_history,
    )
    untried_families = _untried_admitted_families(
        run_state.retrieval_state.query_term_pool,
        tried_families,
    )
    broadening_attempted = _broadening_attempted(
        run_state.retrieval_state.query_term_pool,
        run_state.retrieval_state.sent_query_history,
    )
    productive_round_count = sum(
        1
        for round_state in run_state.round_history
        if round_state.search_observation is not None and round_state.search_observation.unique_new_count > 0
    )
    zero_gain_round_count = sum(
        1
        for round_state in run_state.round_history
        if round_state.search_observation is not None and round_state.search_observation.unique_new_count == 0
    )

    continue_reasons: list[str] = []
    quality_gate_status = "pass"
    budget_stop_round = ceil(max_rounds * BUDGET_STOP_RATIO)
    if retrieval_rounds_completed < min_rounds:
        continue_reasons.append(
            f"{retrieval_rounds_completed} retrieval rounds completed; min_rounds is {min_rounds}."
        )
        reason = continue_reasons[0]
    elif round_no >= budget_stop_round:
        quality_gate_status = "budget_stop_allowed"
        reason = f"round {round_no} reached the {budget_stop_round}/{max_rounds} near-budget stop threshold."
    else:
        if top_pool_strength in {"empty", "weak"}:
            if untried_families:
                continue_reasons.append("top pool is weak and admitted families remain untried.")
                quality_gate_status = "continue_low_quality"
            elif broadening_attempted:
                quality_gate_status = "low_quality_exhausted"
                reason = f"top pool is {top_pool_strength}, but no admitted families remain untried."
            else:
                continue_reasons.append(
                    f"top pool is {top_pool_strength} and no active admitted families remain untried; "
                    "one broaden round is required before stopping."
                )
                quality_gate_status = "broaden_required"
        elif top_pool_strength == "usable" and strong_fit_count < STRONG_FIT_STOP_MIN:
            if untried_families:
                continue_reasons.append(
                    f"top pool is usable but has only {strong_fit_count} strong-fit candidates; admitted families remain untried."
                )
                quality_gate_status = "continue_low_quality"
            elif broadening_attempted:
                quality_gate_status = "low_quality_exhausted"
                reason = (
                    f"top pool is usable with only {strong_fit_count} strong-fit candidates, "
                    "but no admitted families remain untried."
                )
            else:
                continue_reasons.append(
                    f"top pool is usable but has only {strong_fit_count} strong-fit candidates, "
                    "and no active admitted families remain untried; one broaden round is required before stopping."
                )
                quality_gate_status = "broaden_required"
        elif top_pool_strength != "strong" and productive_round_count < 2 and untried_families:
            continue_reasons.append(
                "top pool is not strong, fewer than two rounds were productive, and admitted families remain untried."
            )
            quality_gate_status = "continue_low_quality"
        if continue_reasons:
            reason = continue_reasons[0]
        elif quality_gate_status == "pass":
            reason = "stop allowed by budget, coverage, and quality guidance."

    return StopGuidance(
        can_stop=not continue_reasons,
        reason=reason,
        continue_reasons=continue_reasons,
        tried_families=tried_families,
        untried_admitted_families=untried_families,
        productive_round_count=productive_round_count,
        zero_gain_round_count=zero_gain_round_count,
        top_pool_strength=top_pool_strength,
        fit_count=len(fit_candidates),
        strong_fit_count=strong_fit_count,
        high_risk_fit_count=high_risk_fit_count,
        quality_gate_status=quality_gate_status,
        broadening_attempted=broadening_attempted,
    )


def _top_pool_strength(top_pool: list[ScoredCandidate]) -> TopPoolStrength:
    if not top_pool:
        return "empty"
    fit_candidates = [item for item in top_pool if item.fit_bucket == "fit"]
    if len(top_pool) < 5 or not fit_candidates:
        return "weak"
    if len(top_pool) >= 10 and len(_strong_fit_candidates(top_pool)) >= 5:
        return "strong"
    return "usable"


def _strong_fit_candidates(top_pool: list[ScoredCandidate]) -> list[ScoredCandidate]:
    return [
        item
        for item in top_pool
        if item.fit_bucket == "fit"
        and item.overall_score >= 80
        and item.must_have_match_score >= 70
        and item.risk_score <= 30
    ]


def _budget_reminder(
    *,
    round_no: int,
    retrieval_rounds_completed: int,
    max_rounds: int,
    budget_stop_round: int,
    rounds_remaining_after_current: int,
    near_budget_limit: bool,
) -> str:
    return (
        f"Budget reminder: current controller round {round_no}; "
        f"completed retrieval rounds {retrieval_rounds_completed}; "
        f"max_rounds {max_rounds}; 80% stop threshold starts at round {budget_stop_round}; "
        f"rounds remaining after current decision {rounds_remaining_after_current}; "
        f"near_budget_limit={near_budget_limit}."
    )


def _tried_families(
    query_term_pool: list[QueryTermCandidate],
    sent_query_history,
) -> list[str]:
    term_index = {_term_key(item.term): item for item in query_term_pool}
    return unique_strings(
        candidate.family
        for record in sent_query_history
        for term in record.query_terms
        if (candidate := term_index.get(_term_key(term))) is not None
    )


def _untried_admitted_families(
    query_term_pool: list[QueryTermCandidate],
    tried_families: list[str],
) -> list[str]:
    tried = set(tried_families)
    family_candidates: dict[str, QueryTermCandidate] = {}
    for item in query_term_pool:
        if not item.active or item.queryability != "admitted" or item.retrieval_role == "role_anchor":
            continue
        if item.family in tried:
            continue
        family_candidates.setdefault(item.family, item)
    return [
        item.family
        for item in sorted(
            family_candidates.values(),
            key=lambda item: (item.priority, item.first_added_round, item.family),
        )
    ]


def _broadening_attempted(query_term_pool: list[QueryTermCandidate], sent_query_history) -> bool:
    term_index = {_term_key(item.term): item for item in query_term_pool}
    for record in sent_query_history:
        if str(record.rationale).startswith("Runtime broaden"):
            return True
        candidates = [
            candidate
            for term in record.query_terms
            if (candidate := term_index.get(_term_key(term))) is not None
        ]
        if len(candidates) == 1 and candidates[0].queryability == "admitted" and candidates[0].retrieval_role == "role_anchor":
            return True
    return False


def _term_key(term: str) -> str:
    return " ".join(term.strip().split()).casefold()


def dropped_candidates(run_state: RunState, round_state: RoundState) -> list[ScoredCandidate]:
    if round_state.dropped_candidates:
        return round_state.dropped_candidates
    return [
        run_state.scorecards_by_resume_id[resume_id]
        for resume_id in round_state.dropped_candidate_ids
        if resume_id in run_state.scorecards_by_resume_id
    ]


def _top_pool_entry(candidate: ScoredCandidate) -> TopPoolEntryView:
    return TopPoolEntryView(
        resume_id=candidate.resume_id,
        fit_bucket=candidate.fit_bucket,
        overall_score=candidate.overall_score,
        must_have_match_score=candidate.must_have_match_score,
        risk_score=candidate.risk_score,
        matched_must_haves=candidate.matched_must_haves[:4],
        risk_flags=candidate.risk_flags[:4],
        reasoning_summary=candidate.reasoning_summary,
    )


def _search_observation_view(observation) -> SearchObservationView | None:
    if observation is None:
        return None
    return SearchObservationView(
        unique_new_count=observation.unique_new_count,
        shortage_count=observation.shortage_count,
        fetch_attempt_count=observation.fetch_attempt_count,
        exhausted_reason=observation.exhausted_reason,
        new_candidate_summaries=observation.new_candidate_summaries[:5],
        adapter_notes=observation.adapter_notes[:5],
        city_search_summaries=observation.city_search_summaries,
    )


def _reflection_summary(advice) -> ReflectionSummaryView | None:
    if advice is None:
        return None
    return ReflectionSummaryView(
        decision="stop" if advice.suggest_stop else "continue",
        stop_reason=advice.suggested_stop_reason,
        reflection_summary=advice.reflection_summary,
    )
