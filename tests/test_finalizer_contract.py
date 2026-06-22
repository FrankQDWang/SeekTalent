from __future__ import annotations

import asyncio
from pathlib import Path

from seektalent.finalize.finalizer import Finalizer
from seektalent.models import FinalizeContext, ScoredCandidate
from seektalent.prompting import LoadedPrompt
from seektalent.finalize.deterministic import build_deterministic_final_result
from tests.settings_factory import make_settings


def test_deterministic_finalization_preserves_runtime_ranking_and_scorecard_facts() -> None:
    context = FinalizeContext(
        run_id="run-1",
        run_dir="/tmp/run-1",
        rounds_executed=2,
        stop_reason="reflection_stop",
        top_candidates=[
            _scored_candidate("r-1", source_round=1, score=95),
            _scored_candidate("r-2", source_round=2, score=90),
        ],
    )

    result = build_deterministic_final_result(context)

    assert result.run_id == "run-1"
    assert result.rounds_executed == 2
    assert result.stop_reason == "reflection_stop"
    assert [candidate.resume_id for candidate in result.candidates] == ["r-1", "r-2"]
    assert [candidate.rank for candidate in result.candidates] == [1, 2]
    assert result.candidates[0].final_score == 95
    assert result.candidates[0].match_summary == "Strong role match."
    assert result.candidates[0].why_selected.startswith("Ranked by runtime score 95.")


def test_legacy_finalizer_adapter_uses_deterministic_runtime_result(tmp_path: Path) -> None:
    finalizer = Finalizer(
        make_settings(workspace_root=str(tmp_path)),
        LoadedPrompt(name="finalize", path=tmp_path / "finalize.md", content="unused", sha256="hash"),
    )

    result = asyncio.run(
        finalizer.finalize(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=1,
            stop_reason="controller_stop",
            ranked_candidates=[_scored_candidate("r-1", source_round=1, score=95)],
        )
    )

    assert result.summary == "Selected 1 final candidate by deterministic runtime ranking."
    assert result.candidates[0].resume_id == "r-1"
    assert finalizer.last_provider_usage is None
    assert finalizer.last_validator_retry_count == 0


def _scored_candidate(resume_id: str, *, source_round: int, score: int) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        source_provider="cts",
        fit_bucket="fit",
        overall_score=score,
        must_have_match_score=score,
        preferred_match_score=70,
        risk_score=10,
        risk_flags=[],
        reasoning_summary="Strong role match.",
        evidence=["python"],
        confidence="high",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["trace"],
        negative_signals=[],
        strengths=["Relevant backend work."],
        weaknesses=[],
        source_round=source_round,
    )
