from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.service import RetrievalService
from seektalent.evaluation import AsyncJudgeLimiter
from seektalent.providers import get_provider_adapter
from seektalent.runtime.orchestrator import WorkflowRuntime

from .query_policy import default_source_query_policies
from .registry import build_default_source_registry, build_source_lane_request_runner
from .round_adapters import default_source_round_adapter_provider

def build_source_enabled_runtime(
    settings: AppSettings,
    *,
    retrieval_service: RetrievalService | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    eval_remote_logging: bool = True,
) -> WorkflowRuntime:
    return WorkflowRuntime(
        settings,
        source_registry=build_default_source_registry(settings),
        source_lane_request_runner=build_source_lane_request_runner(settings),
        source_round_adapter_provider=default_source_round_adapter_provider,
        source_query_policy_provider=lambda source_plan: default_source_query_policies(
            settings=settings,
            source_plan=source_plan,
        ),
        retrieval_service=retrieval_service or _build_provider_retrieval_service(settings),
        judge_limiter=judge_limiter,
        eval_remote_logging=eval_remote_logging,
    )


def _build_provider_retrieval_service(settings: AppSettings) -> RetrievalService:
    return RetrievalService(provider=get_provider_adapter(settings))
