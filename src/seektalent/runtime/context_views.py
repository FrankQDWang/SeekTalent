from __future__ import annotations

from seektalent.models import (
    ReflectionSummaryView,
    RoundState,
    RunState,
    ScoredCandidate,
    SearchObservationView,
    TopPoolEntryView,
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
        reflection_rationale=advice.reflection_rationale,
    )
