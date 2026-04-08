from __future__ import annotations

from datetime import date

import pytest

from seektalent.models import RetrievedCandidate_t
from seektalent.retrieval import (
    build_career_stability_profile,
    build_search_execution_result,
    build_search_execution_sidecar,
)


def _candidate(
    candidate_id: str,
    *,
    search_text: str,
    work_summaries: list[str] | None = None,
    work_experience_list: list[dict[str, str]] | None = None,
) -> RetrievedCandidate_t:
    work_experience_list = work_experience_list or []
    return RetrievedCandidate_t(
        candidate_id=candidate_id,
        now_location="上海",
        expected_location="上海",
        years_of_experience_raw=5,
        education_summaries=["复旦大学 计算机 硕士"],
        work_experience_summaries=[
            " | ".join(part for part in [item.get("company"), item.get("title"), item.get("summary")] if part)
            for item in work_experience_list
        ]
        or ["TestCo | Python Engineer | Built retrieval ranking flows."],
        project_names=["retrieval platform"],
        work_summaries=work_summaries or ["python", "agent"],
        search_text=search_text,
        raw_payload={"title": "Python Engineer", "workExperienceList": work_experience_list},
    )


def test_search_execution_result_preserves_candidate_id_alignment() -> None:
    raw_candidates = [
        _candidate("c-1", search_text="python agent"),
        _candidate("c-1", search_text="python agent duplicate"),
        _candidate("c-2", search_text="python retrieval"),
    ]

    result = build_search_execution_result(
        raw_candidates,
        runtime_negative_keywords=[],
        target_new_candidate_count=2,
        latency_ms=18,
    )

    assert [candidate.candidate_id for candidate in result.raw_candidates] == ["c-1", "c-1", "c-2"]
    assert [candidate.candidate_id for candidate in result.deduplicated_candidates] == ["c-1", "c-2"]
    assert [candidate.candidate_id for candidate in result.scoring_candidates] == ["c-1", "c-2"]
    assert result.search_page_statistics.pages_fetched == 2
    assert result.search_page_statistics.duplicate_rate == pytest.approx(1 / 3)
    assert result.search_observation.unique_candidate_ids == ["c-1", "c-2"]
    assert result.search_observation.shortage_after_last_page is False


def test_search_execution_result_applies_runtime_negative_keywords() -> None:
    raw_candidates = [
        _candidate("keep", search_text="python agent retrieval"),
        _candidate("drop", search_text="pure frontend react", work_summaries=["frontend", "react"]),
    ]

    result = build_search_execution_result(
        raw_candidates,
        runtime_negative_keywords=["frontend"],
        target_new_candidate_count=3,
        latency_ms=9,
    )

    assert [candidate.candidate_id for candidate in result.deduplicated_candidates] == ["keep"]
    assert [candidate.candidate_id for candidate in result.scoring_candidates] == ["keep"]
    profile = result.scoring_candidates[0].career_stability_profile
    assert profile.confidence_score > 0
    assert result.search_observation.shortage_after_last_page is True


def test_search_execution_sidecar_collects_runtime_audit_tags_after_negative_filter_and_dedup() -> None:
    raw_candidates = [
        _candidate("keep-1", search_text="python agent retrieval"),
        _candidate("drop", search_text="frontend only", work_summaries=["frontend"]),
        _candidate("keep-1", search_text="java backend"),
        _candidate("keep-2", search_text="ranking only", work_summaries=["ranking"]),
    ]

    sidecar = build_search_execution_sidecar(
        raw_candidates,
        runtime_negative_keywords=["frontend"],
        runtime_must_have_keywords=[" python ", "RANKING"],
        target_new_candidate_count=3,
        latency_ms=9,
    )

    assert [candidate.candidate_id for candidate in sidecar.execution_result.deduplicated_candidates] == ["keep-1", "keep-2"]
    assert sidecar.runtime_audit_tags == {
        "keep-1": ["python"],
        "keep-2": ["RANKING"],
    }


def test_build_career_stability_profile_parses_explicit_dates_and_current_role() -> None:
    candidate = _candidate(
        "stable-1",
        search_text="python agent retrieval",
        work_experience_list=[
            {
                "company": "NowCo",
                "title": "Staff Engineer",
                "summary": "Built agent runtime.",
                "startTime": "2023-06",
                "endTime": "至今",
            },
            {
                "company": "PrevCo",
                "title": "Senior Engineer",
                "summary": "Built retrieval ranking.",
                "startTime": "2021/01",
                "endTime": "2023/05",
            },
            {
                "company": "OldCo",
                "title": "Engineer",
                "summary": "Backend services.",
                "startTime": "2019年03月",
                "endTime": "2020年12月",
            },
        ],
    )

    profile = build_career_stability_profile(candidate, reference_date=date(2025, 1, 1))

    assert profile.model_dump() == {
        "job_count_last_5y": 3,
        "short_tenure_count": 0,
        "median_tenure_months": 22,
        "current_tenure_months": 20,
        "parsed_experience_count": 3,
        "confidence_score": 1.0,
    }


def test_build_career_stability_profile_uses_duration_when_dates_are_missing() -> None:
    candidate = _candidate(
        "duration-1",
        search_text="python agent retrieval",
        work_experience_list=[
            {"company": "NowCo", "title": "Engineer", "summary": "Agent runtime.", "endTime": "present", "duration": "10 months"},
            {"company": "PrevCo", "title": "Engineer", "summary": "Ranking.", "duration": "1年6个月"},
        ],
    )

    profile = build_career_stability_profile(candidate, reference_date=date(2025, 1, 1))

    assert profile.model_dump() == {
        "job_count_last_5y": 0,
        "short_tenure_count": 1,
        "median_tenure_months": 14,
        "current_tenure_months": 10,
        "parsed_experience_count": 2,
        "confidence_score": 1.0,
    }


def test_build_career_stability_profile_falls_back_to_low_confidence_for_unparseable_timelines() -> None:
    candidate = _candidate(
        "fallback-1",
        search_text="python agent retrieval",
        work_experience_list=[
            {"company": "NowCo", "title": "Engineer", "summary": "Agent runtime.", "startTime": "unknown", "endTime": "soon"},
        ],
    )

    profile = build_career_stability_profile(candidate, reference_date=date(2025, 1, 1))

    assert profile.model_dump() == {
        "job_count_last_5y": 1,
        "short_tenure_count": 0,
        "median_tenure_months": 0,
        "current_tenure_months": 0,
        "parsed_experience_count": 0,
        "confidence_score": 0.2,
    }
