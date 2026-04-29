from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from seektalent import AppSettings, MatchRunResult, run_match, run_match_async
from seektalent.evaluation import AsyncJudgeLimiter
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalResult
from seektalent.runtime import RunArtifacts
from tests.settings_factory import make_settings


def _evaluation_result() -> EvaluationResult:
    return EvaluationResult(
        run_id="run-1",
        judge_model="deepseek-v4-pro",
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
    )


def _artifacts(tmp_path: Path, *, include_evaluation: bool = True) -> RunArtifacts:
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
        evaluation_result=_evaluation_result() if include_evaluation else None,
        terminal_stop_guidance=None,
    )


def test_run_match_returns_stable_result(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class FakeRuntime:
        def __init__(
            self,
            settings: AppSettings,
            *,
            judge_limiter=None,  # noqa: ANN001
            eval_remote_logging=True,  # noqa: ANN001
        ) -> None:
            del judge_limiter, eval_remote_logging
            captured["settings"] = settings

        def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del progress_callback
            captured["job_title"] = job_title
            captured["jd"] = jd
            captured["notes"] = notes
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: captured.setdefault("env_file", env_file))

    result = run_match(
        job_title="Python Engineer",
        jd="JD",
        notes="Notes",
        settings=make_settings(mock_cts=True),
        env_file="custom.env",
    )

    assert isinstance(result, MatchRunResult)
    assert result.run_id == "run-1"
    assert result.final_markdown == "# result"
    assert result.run_dir == tmp_path
    assert result.trace_log_path == tmp_path / "trace.log"
    assert captured["job_title"] == "Python Engineer"
    assert captured["jd"] == "JD"
    assert captured["notes"] == "Notes"
    assert captured["env_file"] == "custom.env"


def test_run_match_passes_progress_callback(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    progress_event = object()
    events = []
    callback = events.append

    class FakeRuntime:
        def __init__(
            self,
            settings: AppSettings,
            *,
            judge_limiter=None,  # noqa: ANN001
            eval_remote_logging=True,  # noqa: ANN001
        ) -> None:
            del settings, judge_limiter, eval_remote_logging

        def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del job_title, jd, notes
            captured["progress_callback"] = progress_callback
            assert progress_callback is not None
            progress_callback(progress_event)
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    run_match(
        job_title="Python Engineer",
        jd="JD",
        settings=make_settings(mock_cts=True),
        env_file=None,
        progress_callback=callback,
    )

    assert captured["progress_callback"] is callback
    assert events == [progress_event]


def test_run_match_passes_eval_options_to_runtime(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    limiter = cast(AsyncJudgeLimiter, object())

    class FakeRuntime:
        def __init__(self, settings, *, judge_limiter=None, eval_remote_logging=True):  # noqa: ANN001
            captured["settings"] = settings
            captured["judge_limiter"] = judge_limiter
            captured["eval_remote_logging"] = eval_remote_logging

        def run(self, *, job_title, jd, notes, progress_callback=None):  # noqa: ANN001
            del job_title, jd, notes, progress_callback
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)

    run_match(
        job_title="Role",
        jd="JD",
        settings=make_settings(mock_cts=True),
        env_file=None,
        judge_limiter=limiter,
        eval_remote_logging=False,
    )

    assert captured["judge_limiter"] is limiter
    assert captured["eval_remote_logging"] is False


def test_run_match_uses_explicit_workspace_root_for_artifacts_dir(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, settings: AppSettings, **_: object) -> None:
            captured["project_root"] = settings.project_root
            captured["artifacts_path"] = settings.artifacts_path
            captured["runs_path"] = settings.runs_path

        def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del job_title, jd, notes, progress_callback
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    run_match(
        job_title="Python Engineer",
        jd="JD",
        settings=make_settings(runs_dir="runs", mock_cts=True),
        env_file=None,
        workspace_root=tmp_path,
    )

    assert captured["project_root"] == tmp_path
    assert captured["artifacts_path"] == tmp_path / "artifacts"
    assert captured["runs_path"] == tmp_path / "runs"


def test_match_run_result_constructor_keeps_terminal_guidance_optional(tmp_path: Path) -> None:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("", encoding="utf-8")

    result = MatchRunResult(
        final_result=FinalResult(
            run_id="run-1",
            run_dir=str(tmp_path),
            rounds_executed=1,
            stop_reason="controller_stop",
            summary="done",
            candidates=[],
        ),
        final_markdown="# Final",
        run_id="run-1",
        run_dir=tmp_path,
        trace_log_path=trace_log,
        evaluation_result=None,
    )

    assert result.terminal_stop_guidance is None


def test_run_match_defaults_notes_to_empty_string(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    class FakeRuntime:
        def __init__(
            self,
            settings: AppSettings,
            *,
            judge_limiter=None,  # noqa: ANN001
            eval_remote_logging=True,  # noqa: ANN001
        ) -> None:
            del settings, judge_limiter, eval_remote_logging

        def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del progress_callback
            captured["job_title"] = job_title
            captured["jd"] = jd
            captured["notes"] = notes
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = run_match(job_title="Python Engineer", jd="JD", settings=make_settings(mock_cts=True), env_file=None)

    assert isinstance(result, MatchRunResult)
    assert captured == {"job_title": "Python Engineer", "jd": "JD", "notes": ""}


def test_run_match_async_returns_stable_result(monkeypatch, tmp_path: Path) -> None:
    class FakeRuntime:
        def __init__(
            self,
            settings: AppSettings,
            *,
            judge_limiter=None,  # noqa: ANN001
            eval_remote_logging=True,  # noqa: ANN001
        ) -> None:
            del settings, judge_limiter, eval_remote_logging

        async def run_async(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del progress_callback
            assert job_title == "Python Engineer"
            assert jd == "JD"
            assert notes == "Notes"
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = asyncio.run(
        run_match_async(
            job_title="Python Engineer",
            jd="JD",
            notes="Notes",
            settings=make_settings(mock_cts=True),
            env_file=None,
        )
    )

    assert isinstance(result, MatchRunResult)
    assert result.final_result.run_id == "run-1"


def test_run_match_async_passes_progress_callback(monkeypatch, tmp_path: Path) -> None:
    progress_event = object()
    events = []

    class FakeRuntime:
        def __init__(
            self,
            settings: AppSettings,
            *,
            judge_limiter=None,  # noqa: ANN001
            eval_remote_logging=True,  # noqa: ANN001
        ) -> None:
            del settings, judge_limiter, eval_remote_logging

        async def run_async(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del job_title, jd, notes
            assert progress_callback is not None
            progress_callback(progress_event)
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = asyncio.run(
        run_match_async(
            job_title="Python Engineer",
            jd="JD",
            settings=make_settings(mock_cts=True),
            env_file=None,
            progress_callback=events.append,
        )
    )

    assert isinstance(result, MatchRunResult)
    assert events == [progress_event]


def test_run_match_async_uses_explicit_workspace_root(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, settings: AppSettings, **_: object) -> None:
            captured["project_root"] = settings.project_root
            captured["runs_path"] = settings.runs_path

        async def run_async(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del job_title, jd, notes, progress_callback
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    asyncio.run(
        run_match_async(
            job_title="Python Engineer",
            jd="JD",
            settings=make_settings(runs_dir="runs", mock_cts=True),
            env_file=None,
            workspace_root=tmp_path,
        )
    )

    assert captured["project_root"] == tmp_path
    assert captured["runs_path"] == tmp_path / "runs"


def test_run_match_async_defaults_notes_to_empty_string(monkeypatch, tmp_path: Path) -> None:
    class FakeRuntime:
        def __init__(
            self,
            settings: AppSettings,
            *,
            judge_limiter=None,  # noqa: ANN001
            eval_remote_logging=True,  # noqa: ANN001
        ) -> None:
            del settings, judge_limiter, eval_remote_logging

        async def run_async(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del progress_callback
            assert job_title == "Python Engineer"
            assert jd == "JD"
            assert notes == ""
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = asyncio.run(
        run_match_async(
            job_title="Python Engineer",
            jd="JD",
            settings=make_settings(mock_cts=True),
            env_file=None,
        )
    )

    assert isinstance(result, MatchRunResult)
    assert result.final_result.run_id == "run-1"


def test_run_match_allows_missing_evaluation_result(monkeypatch, tmp_path: Path) -> None:
    class FakeRuntime:
        def __init__(
            self,
            settings: AppSettings,
            *,
            judge_limiter=None,  # noqa: ANN001
            eval_remote_logging=True,  # noqa: ANN001
        ) -> None:
            del settings, judge_limiter, eval_remote_logging

        def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del progress_callback
            assert job_title == "Python Engineer"
            assert jd == "JD"
            assert notes == ""
            return _artifacts(tmp_path, include_evaluation=False)

    monkeypatch.setattr("seektalent.api.WorkflowRuntime", FakeRuntime)
    monkeypatch.setattr("seektalent.api.load_process_env", lambda env_file: None)

    result = run_match(job_title="Python Engineer", jd="JD", settings=make_settings(mock_cts=True), env_file=None)

    assert result.evaluation_result is None


def test_top_level_exports_are_available() -> None:
    settings = make_settings(mock_cts=True)
    assert isinstance(settings, AppSettings)
    assert settings.mock_cts is True
