from __future__ import annotations

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
    rounds_remaining_after_current = max(0, max_rounds - round_no)
    budget_used_ratio = round_no / max_rounds
    return ControllerContext(
        full_jd=run_state.input_truth.jd,
        full_notes=run_state.input_truth.notes,
        requirement_sheet=run_state.requirement_sheet,
        round_no=round_no,
        min_rounds=min_rounds,
        max_rounds=max_rounds,
        rounds_remaining_after_current=rounds_remaining_after_current,
        budget_used_ratio=budget_used_ratio,
        near_budget_limit=budget_used_ratio >= 0.8,
        is_final_allowed_round=round_no >= max_rounds,
        target_new=target_new,
        stop_guidance=_build_stop_guidance(
            run_state=run_state,
            top_pool=top_pool,
            round_no=round_no,
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
    min_rounds: int,
    max_rounds: int,
) -> StopGuidance:
    top_pool_strength = _top_pool_strength(top_pool)
    tried_families = _tried_families(
        run_state.retrieval_state.query_term_pool,
        run_state.retrieval_state.sent_query_history,
    )
    untried_families = _untried_admitted_families(
        run_state.retrieval_state.query_term_pool,
        tried_families,
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
    if round_no >= max_rounds:
        reason = "max_rounds reached; stop is allowed."
    else:
        if round_no < min_rounds:
            continue_reasons.append(f"round {round_no} is below min_rounds {min_rounds}.")
        elif top_pool_strength in {"empty", "weak"} and untried_families:
            continue_reasons.append("top pool is weak and admitted families remain untried.")
        elif top_pool_strength != "strong" and productive_round_count < 2 and untried_families:
            continue_reasons.append(
                "top pool is not strong, fewer than two rounds were productive, and admitted families remain untried."
            )
        reason = continue_reasons[0] if continue_reasons else "stop allowed by budget and coverage guidance."

    return StopGuidance(
        can_stop=not continue_reasons,
        reason=reason,
        continue_reasons=continue_reasons,
        tried_families=tried_families,
        untried_admitted_families=untried_families,
        productive_round_count=productive_round_count,
        zero_gain_round_count=zero_gain_round_count,
        top_pool_strength=top_pool_strength,
    )


def _top_pool_strength(top_pool: list[ScoredCandidate]) -> TopPoolStrength:
    if not top_pool:
        return "empty"
    fit_candidates = [item for item in top_pool if item.fit_bucket == "fit"]
    if len(top_pool) < 5 or not fit_candidates:
        return "weak"
    strong_candidates = [
        item
        for item in fit_candidates
        if item.overall_score >= 80 and item.must_have_match_score >= 70 and item.risk_score <= 30
    ]
    if len(top_pool) >= 10 and len(strong_candidates) >= 5:
        return "strong"
    return "usable"


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
