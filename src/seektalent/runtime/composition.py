from __future__ import annotations

from dataclasses import dataclass

from seektalent.config import AppSettings
from seektalent.core.retrieval.service import RetrievalService
from seektalent.evaluation import AsyncJudgeLimiter
from seektalent.runtime.orchestrator import (
    RuntimeSourceLaneRequestRunner,
    RuntimeSourceQueryPolicyProvider,
    RuntimeSourceRoundAdapterProvider,
    WorkflowRuntime,
)
from seektalent.source_contracts import SourceRegistry


@dataclass(frozen=True)
class RuntimeComposition:
    settings: AppSettings
    source_registry: SourceRegistry
    source_lane_request_runner: RuntimeSourceLaneRequestRunner
    source_round_adapter_provider: RuntimeSourceRoundAdapterProvider
    source_query_policy_provider: RuntimeSourceQueryPolicyProvider
    retrieval_service: RetrievalService
    judge_limiter: AsyncJudgeLimiter | None = None
    eval_remote_logging: bool = True


def build_workflow_runtime(composition: RuntimeComposition) -> WorkflowRuntime:
    return WorkflowRuntime(
        composition.settings,
        source_registry=composition.source_registry,
        source_lane_request_runner=composition.source_lane_request_runner,
        source_round_adapter_provider=composition.source_round_adapter_provider,
        source_query_policy_provider=composition.source_query_policy_provider,
        retrieval_service=composition.retrieval_service,
        judge_limiter=composition.judge_limiter,
        eval_remote_logging=composition.eval_remote_logging,
    )
