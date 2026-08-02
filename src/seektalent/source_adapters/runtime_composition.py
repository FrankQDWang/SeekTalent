from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.service import RetrievalService
from seektalent.evaluation import AsyncJudgeLimiter
from seektalent.providers.plugins import (
    ProviderAdapterBuildContext,
    ProviderAdapterRegistry,
    build_default_provider_adapter_registry,
)
from seektalent.runtime.composition import RuntimeComposition, build_workflow_runtime
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent.source_adapters.query_policy import default_source_query_policies
from seektalent.source_adapters.registry import build_default_source_registry
from seektalent.source_contracts import SourceRegistry


def build_runtime_composition(
    settings: AppSettings,
    *,
    provider_adapter_registry: ProviderAdapterRegistry | None = None,
    retrieval_service: RetrievalService | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
    source_registry: SourceRegistry | None = None,
) -> RuntimeComposition:
    return RuntimeComposition(
        settings=settings,
        source_registry=source_registry or build_default_source_registry(settings),
        source_query_policy_provider=lambda source_plan: default_source_query_policies(
            settings=settings,
            source_plan=source_plan,
        ),
        retrieval_service=retrieval_service
        or build_provider_retrieval_service(
            settings,
            provider_adapter_registry=provider_adapter_registry,
            source_id="cts",
        ),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )


def build_source_enabled_runtime(
    settings: AppSettings,
    *,
    retrieval_service: RetrievalService | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
    source_registry: SourceRegistry | None = None,
) -> WorkflowRuntime:
    return build_workflow_runtime(
        build_runtime_composition(
            settings,
            retrieval_service=retrieval_service,
            judge_limiter=judge_limiter,
            eval_remote_logging=eval_remote_logging,
            source_registry=source_registry,
        )
    )


def build_provider_retrieval_service(
    settings: AppSettings,
    *,
    provider_adapter_registry: ProviderAdapterRegistry | None = None,
    source_id: str | None = None,
) -> RetrievalService:
    registry = provider_adapter_registry or build_default_provider_adapter_registry()
    provider = registry.build_adapter(
        source_id or settings.provider_name,
        ProviderAdapterBuildContext(
            settings=settings,
        ),
    )
    return RetrievalService(provider=provider)
