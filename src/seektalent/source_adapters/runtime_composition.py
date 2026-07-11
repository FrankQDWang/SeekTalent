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
from seektalent.source_adapters.registry import build_default_source_registry, build_source_lane_request_runner
from seektalent.source_adapters.round_adapters import default_source_first_page_expander_provider, default_source_round_adapter_provider


def build_runtime_composition(
    settings: AppSettings,
    *,
    provider_adapter_registry: ProviderAdapterRegistry | None = None,
    retrieval_service: RetrievalService | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
) -> RuntimeComposition:
    return RuntimeComposition(
        settings=settings,
        source_registry=build_default_source_registry(settings),
        source_lane_request_runner=build_source_lane_request_runner(settings),
        source_round_adapter_provider=default_source_round_adapter_provider,
        source_first_page_expander_provider=default_source_first_page_expander_provider,
        source_query_policy_provider=lambda source_plan: default_source_query_policies(
            settings=settings,
            source_plan=source_plan,
        ),
        retrieval_service=retrieval_service
        or build_provider_retrieval_service(
            settings,
            provider_adapter_registry=provider_adapter_registry,
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
) -> WorkflowRuntime:
    return build_workflow_runtime(
        build_runtime_composition(
            settings,
            retrieval_service=retrieval_service,
            judge_limiter=judge_limiter,
            eval_remote_logging=eval_remote_logging,
        )
    )


def build_provider_retrieval_service(
    settings: AppSettings,
    *,
    provider_adapter_registry: ProviderAdapterRegistry | None = None,
) -> RetrievalService:
    registry = provider_adapter_registry or build_default_provider_adapter_registry()
    provider = registry.build_adapter(
        settings.provider_name,
        ProviderAdapterBuildContext(settings=settings),
    )
    return RetrievalService(provider=provider)
