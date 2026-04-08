from __future__ import annotations

from pathlib import Path
from typing import Never

from seektalent.config import AppSettings
from seektalent.runtime import WorkflowRuntime


def _effective_settings(
    *,
    settings: AppSettings | None,
    env_file: str | Path | None,
) -> AppSettings:
    if settings is not None:
        return settings
    return AppSettings(_env_file=env_file)


def run_match(
    *,
    job_description: str,
    hiring_notes: str = "",
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
) -> Never:
    runtime = WorkflowRuntime(_effective_settings(settings=settings, env_file=env_file))
    return runtime.run(job_description=job_description, hiring_notes=hiring_notes)


async def run_match_async(
    *,
    job_description: str,
    hiring_notes: str = "",
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
) -> Never:
    runtime = WorkflowRuntime(_effective_settings(settings=settings, env_file=env_file))
    return await runtime.run_async(job_description=job_description, hiring_notes=hiring_notes)
