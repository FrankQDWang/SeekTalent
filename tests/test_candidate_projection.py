from __future__ import annotations

import pytest

from seektalent.models import RetrievedCandidate_t
from seektalent.retrieval import build_search_execution_result


def _candidate(candidate_id: str, *, search_text: str, work_summaries: list[str] | None = None) -> RetrievedCandidate_t:
    return RetrievedCandidate_t(
        candidate_id=candidate_id,
        now_location="上海",
        expected_location="上海",
        years_of_experience_raw=5,
        education_summaries=["复旦大学 计算机 硕士"],
        work_experience_summaries=["TestCo | Python Engineer | Built retrieval ranking flows."],
        project_names=["retrieval platform"],
        work_summaries=work_summaries or ["python", "agent"],
        search_text=search_text,
        raw_payload={"title": "Python Engineer"},
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
