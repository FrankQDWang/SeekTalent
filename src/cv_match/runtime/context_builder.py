from __future__ import annotations

from cv_match.models import (
    ControllerContext,
    FinalizeContext,
    ReflectionContext,
    ReflectionSummaryView,
    RoundState,
    RunState,
    ScoredCandidate,
    ScoringContext,
    SearchObservationView,
    TopPoolEntryView,
)
from cv_match.requirements import build_requirement_digest


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
    return ControllerContext(
        full_jd=run_state.input_truth.jd,
        full_notes=run_state.input_truth.notes,
        requirement_sheet=run_state.requirement_sheet,
        round_no=round_no,
        min_rounds=min_rounds,
        max_rounds=max_rounds,
        target_new=target_new,
        requirement_digest=build_requirement_digest(run_state.requirement_sheet),
        query_term_pool=run_state.retrieval_state.query_term_pool,
        current_top_pool=[_top_pool_entry(item) for item in top_candidates(run_state)],
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
