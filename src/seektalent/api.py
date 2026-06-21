from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from seektalent.artifacts import ArtifactSession, ArtifactStore
from seektalent.config import AppSettings, load_process_env
from seektalent.evaluation import AsyncJudgeLimiter, EvaluationResult
from seektalent.models import FinalResult, RequirementSheet, StopGuidance
from seektalent.progress import ProgressCallback
from seektalent.runtime import RunArtifacts
from seektalent.runtime.production_contract import (
    ProductionMatchResultV1,
    SourceSelectionV1,
    digest_model_payload,
    digest_text_parts,
)
from seektalent.source_adapters import build_source_enabled_runtime
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
    run_state: object | None = None

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
            run_state=artifacts.run_state,
        )


class _InjectedSessionRunTracer(BaseRunTracer):
    def __init__(self, artifacts_root: Path, *, output_mode: object = "dev") -> None:
        session = getattr(_TRACER_OVERRIDE, "artifact_session", None)
        if session is None:
            super().__init__(artifacts_root, output_mode=output_mode)
            return
        from seektalent_runtime_control.artifact_policy import RuntimeArtifactPolicy, normalize_artifact_output_mode

        self.artifact_policy = RuntimeArtifactPolicy(normalize_artifact_output_mode(output_mode))
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

        orchestrator_module.RunTracer = _InjectedSessionRunTracer  # ty:ignore[invalid-assignment]
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
    return AppSettings(
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
    runtime_profile: Literal["prod_core", "development", "workbench"] = "prod_core",
    source_selection: SourceSelectionV1 | None = None,
    source_context: dict[str, str | int | bool | None] | None = None,
    approved_requirement_sheet: RequirementSheet | None = None,
) -> ProductionMatchResultV1:
    selection = source_selection or SourceSelectionV1()
    debug_result = run_match_debug(
        job_title=job_title,
        jd=jd,
        notes=notes,
        settings=settings,
        env_file=env_file,
        workspace_root=workspace_root,
        progress_callback=progress_callback,
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
        artifact_session=artifact_session,
        source_selection=selection,
        source_context=source_context,
        approved_requirement_sheet=approved_requirement_sheet,
    )
    return ProductionMatchResultV1.from_debug_result(
        debug_result,
        input_digest=digest_text_parts(job_title, jd, notes),
        approved_requirement_sheet_digest=digest_model_payload(approved_requirement_sheet),
        source_selection=selection,
        runtime_profile=runtime_profile,
    )


def run_match_debug(
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
    source_selection: SourceSelectionV1 | None = None,
    source_context: dict[str, str | int | bool | None] | None = None,
    approved_requirement_sheet: RequirementSheet | None = None,
) -> MatchRunResult:
    _install_run_tracer_patch()
    selection = source_selection or SourceSelectionV1()
    runtime = build_source_enabled_runtime(
        _effective_settings(settings=settings, env_file=env_file, workspace_root=workspace_root),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )
    with _bind_artifact_session(artifact_session):
        if not selection.source_kinds and source_context is None and approved_requirement_sheet is None:
            artifacts = runtime.run(
                job_title=job_title,
                jd=jd,
                notes=notes,
                progress_callback=progress_callback,
            )
        else:
            artifacts = runtime.run(
                job_title=job_title,
                jd=jd,
                notes=notes,
                source_kinds=selection.source_kinds or None,
                source_context=source_context,
                progress_callback=progress_callback,
                approved_requirement_sheet=approved_requirement_sheet,
            )
        return MatchRunResult.from_artifacts(artifacts)


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
    runtime_profile: Literal["prod_core", "development", "workbench"] = "prod_core",
    source_selection: SourceSelectionV1 | None = None,
    source_context: dict[str, str | int | bool | None] | None = None,
    approved_requirement_sheet: RequirementSheet | None = None,
) -> ProductionMatchResultV1:
    selection = source_selection or SourceSelectionV1()
    debug_result = await run_match_debug_async(
        job_title=job_title,
        jd=jd,
        notes=notes,
        settings=settings,
        env_file=env_file,
        workspace_root=workspace_root,
        progress_callback=progress_callback,
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
        artifact_session=artifact_session,
        source_selection=selection,
        source_context=source_context,
        approved_requirement_sheet=approved_requirement_sheet,
    )
    return ProductionMatchResultV1.from_debug_result(
        debug_result,
        input_digest=digest_text_parts(job_title, jd, notes),
        approved_requirement_sheet_digest=digest_model_payload(approved_requirement_sheet),
        source_selection=selection,
        runtime_profile=runtime_profile,
    )


async def run_match_debug_async(
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
    source_selection: SourceSelectionV1 | None = None,
    source_context: dict[str, str | int | bool | None] | None = None,
    approved_requirement_sheet: RequirementSheet | None = None,
) -> MatchRunResult:
    _install_run_tracer_patch()
    selection = source_selection or SourceSelectionV1()
    runtime = build_source_enabled_runtime(
        _effective_settings(settings=settings, env_file=env_file, workspace_root=workspace_root),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )
    with _bind_artifact_session(artifact_session):
        if not selection.source_kinds and source_context is None and approved_requirement_sheet is None:
            artifacts = await runtime.run_async(
                job_title=job_title,
                jd=jd,
                notes=notes,
                progress_callback=progress_callback,
            )
        else:
            artifacts = await runtime.run_async(
                job_title=job_title,
                jd=jd,
                notes=notes,
                source_kinds=selection.source_kinds or None,
                source_context=source_context,
                progress_callback=progress_callback,
                approved_requirement_sheet=approved_requirement_sheet,
            )
        return MatchRunResult.from_artifacts(artifacts)
