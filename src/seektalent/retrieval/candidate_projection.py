from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
import re

from seektalent.models import (
    CareerStabilityProfile,
    RetrievedCandidate_t,
    ScoringCandidate_t,
    SearchExecutionResult_t,
    SearchObservation,
    SearchPageStatistics,
    stable_deduplicate,
)


CURRENT_TIME_TOKENS = {"至今", "现在", "current", "present"}
SHORT_TENURE_MONTHS = 12


@dataclass(frozen=True)
class SearchExecutionSidecar:
    execution_result: SearchExecutionResult_t
    runtime_audit_tags: dict[str, list[str]]


@dataclass(frozen=True)
class _ParsedExperience:
    tenure_months: int
    start_month: date | None = None
    end_month: date | None = None
    used_explicit_dates: bool = False
    is_current: bool = False


def build_search_execution_result(
    raw_candidates: list[RetrievedCandidate_t],
    *,
    runtime_negative_keywords: list[str],
    runtime_must_have_keywords: list[str] | None = None,
    target_new_candidate_count: int,
    latency_ms: int,
) -> SearchExecutionResult_t:
    return build_search_execution_sidecar(
        raw_candidates,
        runtime_negative_keywords=runtime_negative_keywords,
        runtime_must_have_keywords=runtime_must_have_keywords,
        target_new_candidate_count=target_new_candidate_count,
        latency_ms=latency_ms,
    ).execution_result


def build_search_execution_sidecar(
    raw_candidates: list[RetrievedCandidate_t],
    *,
    runtime_negative_keywords: list[str],
    runtime_must_have_keywords: list[str] | None = None,
    target_new_candidate_count: int,
    latency_ms: int,
) -> SearchExecutionSidecar:
    runtime_filtered_candidates = [
        candidate for candidate in raw_candidates if not _negative_hit(candidate, runtime_negative_keywords)
    ]
    deduplicated_candidates = deduplicate_candidates(runtime_filtered_candidates)
    scoring_candidates = build_scoring_candidates(deduplicated_candidates)
    runtime_audit_tags = {
        candidate.candidate_id: _matching_terms(candidate, runtime_must_have_keywords or [])
        for candidate in deduplicated_candidates
    }
    return SearchExecutionSidecar(
        execution_result=SearchExecutionResult_t(
            raw_candidates=raw_candidates,
            deduplicated_candidates=deduplicated_candidates,
            scoring_candidates=scoring_candidates,
            search_page_statistics=SearchPageStatistics(
                pages_fetched=max(
                    1,
                    (len(raw_candidates) + max(1, target_new_candidate_count) - 1) // max(1, target_new_candidate_count),
                ),
                duplicate_rate=0.0 if not raw_candidates else 1 - len(deduplicated_candidates) / len(raw_candidates),
                latency_ms=latency_ms,
            ),
            search_observation=SearchObservation(
                unique_candidate_ids=[candidate.candidate_id for candidate in deduplicated_candidates],
                shortage_after_last_page=len(deduplicated_candidates) < target_new_candidate_count,
            ),
        ),
        runtime_audit_tags=runtime_audit_tags,
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
            career_stability_profile=build_career_stability_profile(candidate),
        )
        for candidate in candidates
    ]


def build_career_stability_profile(
    candidate_or_work_experience_summaries: RetrievedCandidate_t | list[str],
    *,
    reference_date: date | None = None,
) -> CareerStabilityProfile:
    reference_month = (reference_date or date.today()).replace(day=1)
    if isinstance(candidate_or_work_experience_summaries, RetrievedCandidate_t):
        work_experience_summaries = candidate_or_work_experience_summaries.work_experience_summaries
        work_experience_items = _raw_work_experience_items(candidate_or_work_experience_summaries)
    else:
        work_experience_summaries = list(candidate_or_work_experience_summaries)
        work_experience_items = []

    parsed_experiences = [
        parsed
        for parsed in (_parse_experience(item, reference_month) for item in work_experience_items)
        if parsed is not None
    ]
    if not parsed_experiences:
        return CareerStabilityProfile.low_confidence(max(len(work_experience_items), len(work_experience_summaries)))

    tenure_months = [item.tenure_months for item in parsed_experiences]
    current_tenure_months = max((item.tenure_months for item in parsed_experiences if item.is_current), default=0)
    confidence_score = len(parsed_experiences) / max(1, len(work_experience_items))
    if any(item.used_explicit_dates for item in parsed_experiences):
        confidence_score += 0.15
    if current_tenure_months > 0:
        confidence_score += 0.15

    return CareerStabilityProfile(
        job_count_last_5y=sum(1 for item in parsed_experiences if _overlaps_last_five_years(item, reference_month)),
        short_tenure_count=sum(1 for months in tenure_months if months < SHORT_TENURE_MONTHS),
        median_tenure_months=int(median(tenure_months)),
        current_tenure_months=current_tenure_months,
        parsed_experience_count=len(parsed_experiences),
        confidence_score=round(min(1.0, confidence_score), 2),
    )


def _negative_hit(candidate: RetrievedCandidate_t, negative_keywords: list[str]) -> bool:
    haystack = _candidate_text(candidate)
    for term in _clean_terms(negative_keywords):
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


def _matching_terms(candidate: RetrievedCandidate_t, terms: list[str]) -> list[str]:
    haystack = _candidate_text(candidate)
    return [term for term in _clean_terms(terms) if term.casefold() in haystack]


def _clean_terms(terms: list[str]) -> list[str]:
    return stable_deduplicate(terms)


def _raw_work_experience_items(candidate: RetrievedCandidate_t) -> list[dict[str, object]]:
    work_experience_list = candidate.raw_payload.get("workExperienceList")
    if not isinstance(work_experience_list, list):
        return []
    return [item for item in work_experience_list if isinstance(item, dict)]


def _parse_experience(item: dict[str, object], reference_month: date) -> _ParsedExperience | None:
    start_month, _ = _parse_month_value(item.get("startTime"), reference_month)
    end_month, is_current = _parse_month_value(item.get("endTime"), reference_month)
    if start_month is not None and end_month is not None:
        if end_month < start_month:
            start_month, end_month = end_month, start_month
        return _ParsedExperience(
            tenure_months=_month_span(start_month, end_month),
            start_month=start_month,
            end_month=end_month,
            used_explicit_dates=True,
            is_current=is_current,
        )

    duration_months = _parse_duration_months(item.get("duration"))
    if duration_months is None:
        return None
    return _ParsedExperience(tenure_months=duration_months, is_current=is_current)


def _parse_month_value(value: object, reference_month: date) -> tuple[date | None, bool]:
    if not isinstance(value, str):
        return None, False
    text = " ".join(value.lower().split()).strip()
    if not text:
        return None, False
    if text in CURRENT_TIME_TOKENS:
        return reference_month, True

    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-").strip("-")
    matched = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", normalized)
    if matched is None:
        return None, False

    year = int(matched.group(1))
    month = int(matched.group(2))
    if month < 1 or month > 12:
        return None, False
    return date(year, month, 1), False


def _parse_duration_months(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.lower().split()).strip()
    if not text:
        return None

    years = _match_duration_unit(text, r"(\d+)\s*(?:年|years?|yrs?|y)")
    months = _match_duration_unit(text, r"(\d+)\s*(?:个月|個月|months?|mos?|m)")
    total_months = years * 12 + months
    return total_months or None


def _match_duration_unit(text: str, pattern: str) -> int:
    matched = re.search(pattern, text)
    return int(matched.group(1)) if matched is not None else 0


def _month_span(start_month: date, end_month: date) -> int:
    return ((end_month.year - start_month.year) * 12) + (end_month.month - start_month.month) + 1


def _overlaps_last_five_years(experience: _ParsedExperience, reference_month: date) -> bool:
    if experience.start_month is None or experience.end_month is None:
        return False
    window_start = _add_months(reference_month, -59)
    return experience.start_month <= reference_month and experience.end_month >= window_start


def _add_months(value: date, months: int) -> date:
    month_index = (value.year * 12) + (value.month - 1) + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)
