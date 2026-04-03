from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deepmatch.config import AppSettings, load_process_env
from deepmatch.models import FinalResult
from deepmatch.runtime import RunArtifacts, WorkflowRuntime


@dataclass(frozen=True)
class MatchRunResult:
    final_result: FinalResult
    final_markdown: str
    run_id: str
    run_dir: Path
    trace_log_path: Path

    @classmethod
    def from_artifacts(cls, artifacts: RunArtifacts) -> "MatchRunResult":
        return cls(
            final_result=artifacts.final_result,
            final_markdown=artifacts.final_markdown,
            run_id=artifacts.run_id,
            run_dir=artifacts.run_dir,
            trace_log_path=artifacts.trace_log_path,
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
    return AppSettings(_env_file=env_file)


def run_match(
    *,
    jd: str,
    notes: str = "",
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
) -> MatchRunResult:
    runtime = WorkflowRuntime(_effective_settings(settings=settings, env_file=env_file))
    return MatchRunResult.from_artifacts(runtime.run(jd=jd, notes=notes))


async def run_match_async(
    *,
    jd: str,
    notes: str = "",
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
) -> MatchRunResult:
    runtime = WorkflowRuntime(_effective_settings(settings=settings, env_file=env_file))
    return MatchRunResult.from_artifacts(await runtime.run_async(jd=jd, notes=notes))
