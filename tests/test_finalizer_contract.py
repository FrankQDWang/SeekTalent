import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelRetry

from seektalent.finalize.finalizer import Finalizer
from seektalent.models import FinalCandidateDraft, FinalResultDraft, FinalizeContext, ScoredCandidate, ScoredCandidateDraft
from seektalent.prompting import LoadedPrompt
from seektalent.scoring.scorer import _materialize_scored_candidate
from tests.settings_factory import make_settings


def _scored_candidate(resume_id: str, *, source_round: int, score: int) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
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


def _draft_candidate(resume_id: str) -> FinalCandidateDraft:
    return FinalCandidateDraft(
        resume_id=resume_id,
        match_summary="Strong backend match.",
        why_selected="Strong role fit.",
    )


class _StubAgent:
    def __init__(self, output: FinalResultDraft) -> None:
        self.output = output

    async def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(output=self.output)


def _validator(monkeypatch: pytest.MonkeyPatch):
    finalizer, validator = _finalizer_and_validator(monkeypatch)
    return validator


def _finalizer_and_validator(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    finalizer = Finalizer(
        make_settings(),
        LoadedPrompt(name="finalize", path=Path("finalize.md"), content="finalize prompt", sha256="hash"),
    )
    return finalizer, finalizer._get_agent()._output_validators[0].function


def _deps() -> FinalizeContext:
    return FinalizeContext(
        run_id="run-1",
        run_dir="/tmp/run-1",
        rounds_executed=2,
        stop_reason="reflection_stop",
        top_candidates=[
            _scored_candidate("r-1", source_round=1, score=95),
            _scored_candidate("r-2", source_round=2, score=90),
            _scored_candidate("r-3", source_round=2, score=85),
        ],
    )


def test_finalizer_output_validator_rejects_duplicate_resume_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizer, validator = _finalizer_and_validator(monkeypatch)
    output = FinalResultDraft(
        summary="Returned 2 candidates.",
        candidates=[
            _draft_candidate("r-1"),
            _draft_candidate("r-1"),
        ],
    )

    with pytest.raises(ModelRetry, match="Duplicate"):
        validator(type("Ctx", (), {"deps": _deps()})(), output)

    assert finalizer.last_validator_retry_reasons == ["Duplicate resume_id 'r-1' in final candidates."]


def test_finalizer_output_validator_rejects_unknown_resume_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizer, validator = _finalizer_and_validator(monkeypatch)
    output = FinalResultDraft(
        summary="Returned 1 candidate.",
        candidates=[_draft_candidate("r-9")],
    )

    with pytest.raises(ModelRetry, match="Unknown resume_id"):
        validator(type("Ctx", (), {"deps": _deps()})(), output)

    assert finalizer.last_validator_retry_reasons == ["Unknown resume_id 'r-9' in final candidates."]


def test_finalizer_output_validator_rejects_out_of_order_resume_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizer, validator = _finalizer_and_validator(monkeypatch)
    output = FinalResultDraft(
        summary="Returned 3 candidates.",
        candidates=[
            _draft_candidate("r-1"),
            _draft_candidate("r-3"),
            _draft_candidate("r-2"),
        ],
    )

    with pytest.raises(ModelRetry, match="runtime ranking order"):
        validator(type("Ctx", (), {"deps": _deps()})(), output)

    assert finalizer.last_validator_retry_reasons == ["Final candidates must preserve runtime ranking order."]


def test_finalizer_output_validator_rejects_incomplete_shortlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizer, validator = _finalizer_and_validator(monkeypatch)
    output = FinalResultDraft(
        summary="Returned 2 candidates.",
        candidates=[
            _draft_candidate("r-1"),
            _draft_candidate("r-2"),
        ],
    )

    with pytest.raises(ModelRetry, match="count must equal runtime top candidate count"):
        validator(type("Ctx", (), {"deps": _deps()})(), output)

    assert finalizer.last_validator_retry_reasons == [
        "Final candidates count must equal runtime top candidate count."
    ]


def test_finalizer_output_validator_accepts_complete_runtime_shortlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _validator(monkeypatch)
    output = FinalResultDraft(
        summary="Returned 3 candidates.",
        candidates=[
            _draft_candidate("r-1"),
            _draft_candidate("r-2"),
            _draft_candidate("r-3"),
        ],
    )

    validated = validator(type("Ctx", (), {"deps": _deps()})(), output)

    assert validated == output


def test_finalizer_materializes_public_result_from_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    finalizer = Finalizer(
        make_settings(),
        LoadedPrompt(name="finalize", path=Path("finalize.md"), content="finalize prompt", sha256="hash"),
    )
    draft = FinalResultDraft(
        summary="Returned 2 candidates.",
        candidates=[
            FinalCandidateDraft(
                resume_id="r-1",
                match_summary="Strong first match.",
                why_selected="Best Python fit.",
            ),
            FinalCandidateDraft(
                resume_id="r-2",
                match_summary="Strong second match.",
                why_selected="Good tracing fit.",
            ),
        ],
    )
    monkeypatch.setattr(finalizer, "_get_agent", lambda: _StubAgent(draft))

    result = asyncio.run(
        finalizer.finalize(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=2,
            stop_reason="reflection_stop",
            ranked_candidates=_deps().top_candidates[:2],
        )
    )

    assert result.run_id == "run-1"
    assert result.run_dir == "/tmp/run-1"
    assert result.rounds_executed == 2
    assert result.stop_reason == "reflection_stop"
    assert result.summary == draft.summary
    assert [candidate.resume_id for candidate in result.candidates] == ["r-1", "r-2"]
    assert [candidate.rank for candidate in result.candidates] == [1, 2]
    assert [candidate.final_score for candidate in result.candidates] == [95, 90]
    assert result.candidates[0].fit_bucket == "fit"
    assert result.candidates[0].strengths == ["Relevant backend work."]
    assert result.candidates[0].matched_must_haves == ["python"]
    assert result.candidates[0].matched_preferences == ["trace"]
    assert result.candidates[0].source_round == 1
    assert result.candidates[0].match_summary == "Strong first match."
    assert result.candidates[0].why_selected == "Best Python fit."
    assert finalizer.last_draft_output == draft


def test_finalizer_keeps_materialized_scorecard_explanations(monkeypatch: pytest.MonkeyPatch) -> None:
    finalizer = Finalizer(
        make_settings(),
        LoadedPrompt(name="finalize", path=Path("finalize.md"), content="finalize prompt", sha256="hash"),
    )
    scored = _materialize_scored_candidate(
        draft=ScoredCandidateDraft(
            fit_bucket="fit",
            overall_score=88,
            must_have_match_score=90,
            preferred_match_score=72,
            risk_score=20,
            risk_flags=["limited large-scale ownership"],
            reasoning_summary="Strong backend fit with some scale risk.",
            matched_must_haves=["python"],
            missing_must_haves=[],
            matched_preferences=["retrieval"],
            negative_signals=[],
        ),
        resume_id="r-1",
        source_round=1,
    )
    monkeypatch.setattr(
        finalizer,
        "_get_agent",
        lambda: _StubAgent(
            FinalResultDraft(
                summary="Returned one candidate.",
                candidates=[
                    FinalCandidateDraft(
                        resume_id="r-1",
                        match_summary="Strong backend match.",
                        why_selected="Best available Python fit.",
                    )
                ],
            )
        ),
    )

    result = asyncio.run(
        finalizer.finalize(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=2,
            stop_reason="controller_stop",
            ranked_candidates=[scored],
        )
    )

    assert result.candidates[0].strengths == ["Matched must-have: python", "Matched preference: retrieval"]
    assert result.candidates[0].weaknesses == ["Risk flag: limited large-scale ownership"]
