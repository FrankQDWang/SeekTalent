from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from seektalent.finalize.finalizer import Finalizer
from seektalent.models import FinalizeContext, HardConflictEvidence, ScoredCandidate
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


def test_deterministic_finalization_excludes_hard_conflict_candidates() -> None:
    fit_candidates = [
        _scored_candidate("fit-1", source_round=1, score=90),
        _scored_candidate("fit-2", source_round=1, score=80),
    ]
    not_fit_candidates = [
        _scored_candidate(f"not-fit-{index}", source_round=1, score=99 - index).model_copy(
            update={
                "fit_bucket": "not_fit",
                "hard_conflicts": [
                    HardConflictEvidence(
                        policy_reference="exclusion_signals[0]",
                        resume_evidence="Resume explicitly matches the blocking exclusion.",
                    )
                ],
            }
        )
        for index in range(8)
    ]

    result = build_deterministic_final_result(
        FinalizeContext(
            run_id="run-hard-conflicts",
            run_dir="/tmp/run-hard-conflicts",
            rounds_executed=1,
            stop_reason="controller_stop",
            top_candidates=[*fit_candidates, *not_fit_candidates],
        )
    )

    assert [candidate.resume_id for candidate in result.candidates] == [
        "fit-1",
        "fit-2",
    ]


def test_deterministic_finalization_exposes_claim_aware_presentation_id_not_carrier() -> None:
    carried_key_hash = hashlib.sha256(b"private-liepin-carrier").hexdigest()
    presentation_resume_id = hashlib.sha256(
        f"liepin:detail:presentation:v1:{carried_key_hash}".encode("utf-8")
    ).hexdigest()
    result = build_deterministic_final_result(
        FinalizeContext(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=1,
            stop_reason="controller_stop",
            top_candidates=[_scored_candidate(presentation_resume_id, source_round=1, score=95)],
        )
    )

    assert result.candidates[0].resume_id == presentation_resume_id
    assert carried_key_hash not in result.model_dump_json()


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
