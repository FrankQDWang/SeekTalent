import pytest

from seektalent.mock_data import load_mock_resume_corpus
from seektalent.models import FinalCandidate, FinalResult
from seektalent.normalization import normalize_resume
from seektalent_ui.mapper import build_ui_payloads


def _final_result() -> FinalResult:
    return FinalResult(
        run_id="run-1",
        run_dir="/tmp/run-1",
        rounds_executed=3,
        stop_reason="reflection_stop",
        summary="Returned 1 candidates after 3 rounds.",
        candidates=[
            FinalCandidate(
                resume_id="mock-r001",
                rank=1,
                final_score=92,
                fit_bucket="fit",
                match_summary="Must 92/100, preferred 65/100, risk 8/100.",
                strengths=["Matched must-have: python"],
                weaknesses=[],
                matched_must_haves=["python", "agent"],
                matched_preferences=["resume"],
                risk_flags=[],
                why_selected="Direct Python agent experience with tracing and ranking.",
                source_round=1,
            )
        ],
    )


def test_build_ui_payloads_maps_shortlist_and_detail() -> None:
    corpus = {candidate.resume_id: candidate for candidate in load_mock_resume_corpus()}
    normalized = {
        resume_id: normalize_resume(candidate)
        for resume_id, candidate in corpus.items()
    }

    shortlist, details = build_ui_payloads(_final_result(), corpus, normalized)

    assert len(shortlist) == 1
    assert shortlist[0].candidateId == "mock-r001"
    assert shortlist[0].name == "Lin Qian"
    assert shortlist[0].title == "Senior Python Agent Engineer"
    assert shortlist[0].company == "Hewa Talent Cloud"
    assert shortlist[0].location == "上海"
    assert shortlist[0].score == 0.92
    assert shortlist[0].reason == "Direct Python agent experience with tracing and ranking."

    detail = details["mock-r001"]
    assert detail.candidate.name == "Lin Qian"
    assert detail.resumeView.projection.workYear == 8
    assert detail.resumeView.projection.currentLocation == "上海"
    assert detail.resumeView.projection.workExperience[0].company == "Hewa Talent Cloud"
    assert detail.resumeView.projection.workSummaries[:3] == ["python", "agent", "pydantic ai"]
    assert detail.aiAnalysis.evidenceSpans == ["python", "agent", "resume"]


def test_build_ui_payloads_requires_candidate_store_entry() -> None:
    normalized = {
        candidate.resume_id: normalize_resume(candidate)
        for candidate in load_mock_resume_corpus()
    }

    with pytest.raises(KeyError, match="mock-r001"):
        build_ui_payloads(_final_result(), {}, normalized)


def test_build_ui_payloads_requires_normalized_store_entry() -> None:
    corpus = {candidate.resume_id: candidate for candidate in load_mock_resume_corpus()}

    with pytest.raises(KeyError, match="mock-r001"):
        build_ui_payloads(_final_result(), corpus, {})
