from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from seektalent.config import AppSettings, load_process_env
from seektalent.evaluation import AsyncJudgeLimiter, EvaluationResult
from seektalent.models import FinalResult, StopGuidance
from seektalent.progress import ProgressCallback
from seektalent.runtime import RunArtifacts, WorkflowRuntime


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


def _effective_settings(
    *,
    settings: AppSettings | None,
    env_file: str | Path | None,
) -> AppSettings:
    if env_file is not None:
        load_process_env(env_file)
    if settings is not None:
        return settings
    return AppSettings(_env_file=env_file)  # ty: ignore[unknown-argument]


def run_match(
    *,
    job_title: str,
    jd: str,
    notes: str = "",
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
    progress_callback: ProgressCallback | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
) -> MatchRunResult:
    runtime = WorkflowRuntime(
        _effective_settings(settings=settings, env_file=env_file),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )
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
    progress_callback: ProgressCallback | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
) -> MatchRunResult:
    runtime = WorkflowRuntime(
        _effective_settings(settings=settings, env_file=env_file),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )
    return MatchRunResult.from_artifacts(
        await runtime.run_async(job_title=job_title, jd=jd, notes=notes, progress_callback=progress_callback)
    )
