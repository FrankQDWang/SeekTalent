from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from seektalent.artifacts import ArtifactSession, ArtifactStore
from seektalent.config import AppSettings, load_process_env
from seektalent.evaluation import AsyncJudgeLimiter, EvaluationResult
from seektalent.models import FinalResult, StopGuidance
from seektalent.progress import ProgressCallback
from seektalent.runtime import RunArtifacts, WorkflowRuntime
from seektalent.tracing import RunTracer as BaseRunTracer

_TRACER_OVERRIDE = threading.local()
_TRACER_PATCH_LOCK = threading.Lock()
_TRACER_PATCHED = False


@dataclass(frozen=True)
class MatchRunResult:
    final_result: FinalResult
    final_markdown: str
    run_id: str
    run_dir: Path
    trace_log_path: Path
    evaluation_result: EvaluationResult | None
    terminal_stop_guidance: StopGuidance | None = None

    @classmethod
    def from_artifacts(cls, artifacts: RunArtifacts) -> "MatchRunResult":
        return cls(
            final_result=artifacts.final_result,
            final_markdown=artifacts.final_markdown,
            run_id=artifacts.run_id,
            run_dir=artifacts.run_dir,
            trace_log_path=artifacts.trace_log_path,
            evaluation_result=artifacts.evaluation_result,
            terminal_stop_guidance=artifacts.terminal_stop_guidance,
        )


class _InjectedSessionRunTracer(BaseRunTracer):
    def __init__(self, artifacts_root: Path) -> None:
        session = getattr(_TRACER_OVERRIDE, "artifact_session", None)
        if session is None:
            super().__init__(artifacts_root)
            return
        self.store = ArtifactStore(artifacts_root)
        self.session = session
        self.run_id = session.manifest.artifact_id
        self.run_dir = session.root
        self.trace_log_path, self._trace_handle = self.session.open_text_stream("runtime.trace_log")
        self.events_path, self._events_handle = self.session.open_text_stream("runtime.events")
        self._lock = threading.Lock()


def _install_run_tracer_patch() -> None:
    global _TRACER_PATCHED
    if _TRACER_PATCHED:
        return
    with _TRACER_PATCH_LOCK:
        if _TRACER_PATCHED:
            return
        from seektalent.runtime import orchestrator as orchestrator_module

        orchestrator_module.RunTracer = _InjectedSessionRunTracer
        _TRACER_PATCHED = True


@contextmanager
def _bind_artifact_session(artifact_session: ArtifactSession | None):
    previous = getattr(_TRACER_OVERRIDE, "artifact_session", None)
    _TRACER_OVERRIDE.artifact_session = artifact_session
    try:
        yield
    finally:
        if previous is None:
            if hasattr(_TRACER_OVERRIDE, "artifact_session"):
                delattr(_TRACER_OVERRIDE, "artifact_session")
        else:
            _TRACER_OVERRIDE.artifact_session = previous


def _effective_settings(
    *,
    settings: AppSettings | None,
    env_file: str | Path | None,
    workspace_root: str | Path | None = None,
) -> AppSettings:
    if env_file is not None:
        load_process_env(env_file)
    if settings is not None:
        if workspace_root is None:
            return settings
        return settings.with_overrides(workspace_root=str(workspace_root))
    return AppSettings(  # ty: ignore[unknown-argument]
        _env_file=env_file,
        workspace_root=str(workspace_root) if workspace_root else None,
    )


def run_match(
    *,
    job_title: str,
    jd: str,
    notes: str = "",
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
    workspace_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
    artifact_session: ArtifactSession | None = None,
) -> MatchRunResult:
    _install_run_tracer_patch()
    runtime = WorkflowRuntime(
        _effective_settings(settings=settings, env_file=env_file, workspace_root=workspace_root),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )
    with _bind_artifact_session(artifact_session):
        return MatchRunResult.from_artifacts(
            runtime.run(job_title=job_title, jd=jd, notes=notes, progress_callback=progress_callback)
        )


async def run_match_async(
    *,
    job_title: str,
    jd: str,
    notes: str = "",
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
    workspace_root: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
    artifact_session: ArtifactSession | None = None,
) -> MatchRunResult:
    _install_run_tracer_patch()
    runtime = WorkflowRuntime(
        _effective_settings(settings=settings, env_file=env_file, workspace_root=workspace_root),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )
    with _bind_artifact_session(artifact_session):
        return MatchRunResult.from_artifacts(
            await runtime.run_async(job_title=job_title, jd=jd, notes=notes, progress_callback=progress_callback)
        )
