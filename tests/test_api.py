from __future__ import annotations

import asyncio
from pathlib import Path

from seektalent import AppSettings, MatchRunResult, run_match, run_match_async
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalResult
from seektalent.runtime import RunArtifacts


def _artifacts(tmp_path: Path) -> RunArtifacts:
    trace_log_path = tmp_path / "trace.log"
    trace_log_path.write_text("", encoding="utf-8")
    return RunArtifacts(
        final_result=FinalResult(
            run_id="run-1",
            run_dir=str(tmp_path),
            rounds_executed=2,
            stop_reason="controller_stop",
            summary="done",
            candidates=[],
        ),
        final_markdown="# result",
        run_id="run-1",
        run_dir=tmp_path,
        trace_log_path=trace_log_path,
        candidate_store={},
        normalized_store={},
        evaluation_result=EvaluationResult(
            run_id="run-1",
            judge_model="openai-chat:deepseek-v3.2",
            jd_sha256="jd",
            round_01=EvaluationStageResult(
                stage="round_01",
                ndcg_at_10=0.5,
                precision_at_10=0.4,
                total_score=0.43,
                candidates=[],
            ),
            final=EvaluationStageResult(
                stage="final",
                ndcg_at_10=0.7,
                precision_at_10=0.6,
                total_score=0.63,
                candidates=[],
            ),
        ),
    )


def test_run_match_returns_stable_result(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class FakeRuntime:
        def __init__(self, settings: AppSettings) -> None:
            captured["settings"] = settings

        def run(self, *, jd: str, notes: str) -> RunArtifacts:
            captured["jd"] = jd
            captured["notes"] = notes
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: captured.setdefault("env_file", env_file))

    result = run_match(
        jd="JD",
        notes="Notes",
        settings=AppSettings(_env_file=None, mock_cts=True),
        env_file="custom.env",
    )

    assert isinstance(result, MatchRunResult)
    assert result.run_id == "run-1"
    assert result.final_markdown == "# result"
    assert result.run_dir == tmp_path
    assert result.trace_log_path == tmp_path / "trace.log"
    assert captured["jd"] == "JD"
    assert captured["notes"] == "Notes"
    assert captured["env_file"] == "custom.env"


def test_run_match_defaults_notes_to_empty_string(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class FakeRuntime:
        def __init__(self, settings: AppSettings) -> None:
            del settings

        def run(self, *, jd: str, notes: str) -> RunArtifacts:
            captured["jd"] = jd
            captured["notes"] = notes
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = run_match(jd="JD", settings=AppSettings(_env_file=None, mock_cts=True), env_file=None)

    assert isinstance(result, MatchRunResult)
    assert captured == {"jd": "JD", "notes": ""}


def test_run_match_async_returns_stable_result(monkeypatch, tmp_path: Path) -> None:
    class FakeRuntime:
        def __init__(self, settings: AppSettings) -> None:
            del settings

        async def run_async(self, *, jd: str, notes: str) -> RunArtifacts:
            assert jd == "JD"
            assert notes == "Notes"
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = asyncio.run(
        run_match_async(
            jd="JD",
            notes="Notes",
            settings=AppSettings(_env_file=None, mock_cts=True),
            env_file=None,
        )
    )

    assert isinstance(result, MatchRunResult)
    assert result.final_result.run_id == "run-1"


def test_run_match_async_defaults_notes_to_empty_string(monkeypatch, tmp_path: Path) -> None:
    class FakeRuntime:
        def __init__(self, settings: AppSettings) -> None:
            del settings

        async def run_async(self, *, jd: str, notes: str) -> RunArtifacts:
            assert jd == "JD"
            assert notes == ""
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = asyncio.run(
        run_match_async(
            jd="JD",
            settings=AppSettings(_env_file=None, mock_cts=True),
            env_file=None,
        )
    )

    assert isinstance(result, MatchRunResult)
    assert result.final_result.run_id == "run-1"


def test_top_level_exports_are_available() -> None:
    settings = AppSettings(_env_file=None, mock_cts=True)
    assert settings.mock_cts is True
