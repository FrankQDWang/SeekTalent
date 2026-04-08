from __future__ import annotations

from seektalent.models import (
    CareerStabilityProfile,
    RetrievedCandidate_t,
    ScoringCandidate_t,
    SearchExecutionResult_t,
    SearchObservation,
    SearchPageStatistics,
    stable_deduplicate,
)


def build_search_execution_result(
    raw_candidates: list[RetrievedCandidate_t],
    *,
    runtime_negative_keywords: list[str],
    target_new_candidate_count: int,
    latency_ms: int,
) -> SearchExecutionResult_t:
    runtime_filtered_candidates = [
        candidate for candidate in raw_candidates if not _negative_hit(candidate, runtime_negative_keywords)
    ]
    deduplicated_candidates = deduplicate_candidates(runtime_filtered_candidates)
    scoring_candidates = build_scoring_candidates(deduplicated_candidates)
    return SearchExecutionResult_t(
        raw_candidates=raw_candidates,
        deduplicated_candidates=deduplicated_candidates,
        scoring_candidates=scoring_candidates,
        search_page_statistics=SearchPageStatistics(
            pages_fetched=max(1, (len(raw_candidates) + max(1, target_new_candidate_count) - 1) // max(1, target_new_candidate_count)),
            duplicate_rate=0.0 if not raw_candidates else 1 - len(deduplicated_candidates) / len(raw_candidates),
            latency_ms=latency_ms,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=[candidate.candidate_id for candidate in deduplicated_candidates],
            shortage_after_last_page=len(deduplicated_candidates) < target_new_candidate_count,
        ),
    )


def deduplicate_candidates(candidates: list[RetrievedCandidate_t]) -> list[RetrievedCandidate_t]:
    seen: set[str] = set()
    deduplicated: list[RetrievedCandidate_t] = []
    for candidate in candidates:
        if candidate.candidate_id in seen:
            continue
        seen.add(candidate.candidate_id)
        deduplicated.append(candidate)
    return deduplicated


def build_scoring_candidates(candidates: list[RetrievedCandidate_t]) -> list[ScoringCandidate_t]:
    return [
        ScoringCandidate_t(
            candidate_id=candidate.candidate_id,
            scoring_text=candidate.search_text,
            capability_signals=stable_deduplicate(candidate.project_names + candidate.work_summaries),
            years_of_experience=candidate.years_of_experience_raw,
            age=candidate.age,
            gender=candidate.gender,
            location_signals=stable_deduplicate(
                [
                    value
                    for value in [candidate.now_location, candidate.expected_location]
                    if isinstance(value, str)
                ]
            ),
            work_experience_summaries=list(candidate.work_experience_summaries),
            education_summaries=list(candidate.education_summaries),
            career_stability_profile=build_career_stability_profile(candidate.work_experience_summaries),
        )
        for candidate in candidates
    ]


def build_career_stability_profile(work_experience_summaries: list[str]) -> CareerStabilityProfile:
    experience_count = len(work_experience_summaries)
    if experience_count == 0:
        return CareerStabilityProfile.low_confidence(0)
    return CareerStabilityProfile.low_confidence(experience_count)


def _negative_hit(candidate: RetrievedCandidate_t, negative_keywords: list[str]) -> bool:
    haystack = _candidate_text(candidate)
    for term in negative_keywords:
        normalized = " ".join(term.lower().split()).strip()
        if normalized and normalized in haystack:
            return True
    return False


def _candidate_text(candidate: RetrievedCandidate_t) -> str:
    return " ".join(
        part
        for part in [
            candidate.search_text,
            " ".join(candidate.work_summaries),
            " ".join(candidate.project_names),
        ]
        if part
    ).lower()
