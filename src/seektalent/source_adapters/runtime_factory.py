from __future__ import annotations

from typing import TYPE_CHECKING

from seektalent.config import AppSettings
from seektalent.core.retrieval.service import RetrievalService
from seektalent.evaluation import AsyncJudgeLimiter
from seektalent.source_contracts import SourceRegistry

if TYPE_CHECKING:
    from seektalent.source_adapters.runtime_composition import WorkflowRuntime


def build_source_enabled_runtime(
    settings: AppSettings,
    *,
    retrieval_service: RetrievalService | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
    source_registry: SourceRegistry | None = None,
) -> WorkflowRuntime:
    from seektalent.source_adapters.runtime_composition import (
        build_source_enabled_runtime as _build_source_enabled_runtime,
    )

    return _build_source_enabled_runtime(
        settings,
        retrieval_service=retrieval_service
        or _build_provider_retrieval_service(
            settings,
            source_id="cts",
        ),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
        source_registry=source_registry,
    )


def _build_provider_retrieval_service(
    settings: AppSettings,
    *,
    source_id: str,
) -> RetrievalService:
    from seektalent.source_adapters.runtime_composition import build_provider_retrieval_service

    return build_provider_retrieval_service(
        settings,
        source_id=source_id,
    )
