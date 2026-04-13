from __future__ import annotations

from pathlib import Path
from typing import Any

from seektalent.bootstrap_assets import BootstrapAssets
from seektalent.clients.cts_client import CTSClientProtocol
from seektalent.config import AppSettings
from seektalent.models import SearchRunBundle
from seektalent.progress import ProgressCallback
from seektalent.runtime import WorkflowRuntime
from seektalent.search_ops import AsyncRerankRequest


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
    top_k: int = 10,
    round_budget: int | None = None,
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
    assets: BootstrapAssets | None = None,
    cts_client: CTSClientProtocol | None = None,
    rerank_request: AsyncRerankRequest | None = None,
    requirement_extraction_model: Any | None = None,
    bootstrap_keyword_generation_model: Any | None = None,
    search_controller_decision_model: Any | None = None,
    branch_outcome_evaluation_model: Any | None = None,
    search_run_finalization_model: Any | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SearchRunBundle:
    runtime = WorkflowRuntime(
        _effective_settings(settings=settings, env_file=env_file),
        env_file=env_file,
        assets=assets,
        cts_client=cts_client,
        rerank_request=rerank_request,
        requirement_extraction_model=requirement_extraction_model,
        bootstrap_keyword_generation_model=bootstrap_keyword_generation_model,
        search_controller_decision_model=search_controller_decision_model,
        branch_outcome_evaluation_model=branch_outcome_evaluation_model,
        search_run_finalization_model=search_run_finalization_model,
        progress_callback=progress_callback,
    )
    return runtime.run(
        job_description=job_description,
        hiring_notes=hiring_notes,
        top_k=top_k,
        round_budget=round_budget,
    )


async def run_match_async(
    *,
    job_description: str,
    hiring_notes: str = "",
    top_k: int = 10,
    round_budget: int | None = None,
    settings: AppSettings | None = None,
    env_file: str | Path | None = ".env",
    assets: BootstrapAssets | None = None,
    cts_client: CTSClientProtocol | None = None,
    rerank_request: AsyncRerankRequest | None = None,
    requirement_extraction_model: Any | None = None,
    bootstrap_keyword_generation_model: Any | None = None,
    search_controller_decision_model: Any | None = None,
    branch_outcome_evaluation_model: Any | None = None,
    search_run_finalization_model: Any | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SearchRunBundle:
    runtime = WorkflowRuntime(
        _effective_settings(settings=settings, env_file=env_file),
        env_file=env_file,
        assets=assets,
        cts_client=cts_client,
        rerank_request=rerank_request,
        requirement_extraction_model=requirement_extraction_model,
        bootstrap_keyword_generation_model=bootstrap_keyword_generation_model,
        search_controller_decision_model=search_controller_decision_model,
        branch_outcome_evaluation_model=branch_outcome_evaluation_model,
        search_run_finalization_model=search_run_finalization_model,
        progress_callback=progress_callback,
    )
    return await runtime.run_async(
        job_description=job_description,
        hiring_notes=hiring_notes,
        top_k=top_k,
        round_budget=round_budget,
    )
