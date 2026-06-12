from __future__ import annotations

import hashlib as hashlib
from collections.abc import Mapping as Mapping, Sequence as Sequence
from datetime import datetime as datetime

import httpx as httpx

from seektalent.config import AppSettings as AppSettings
from seektalent.corpus.runtime import (
    ProviderReturnedCandidate as ProviderReturnedCandidate,
    build_deterministic_provider_request_id as build_deterministic_provider_request_id,
)
from seektalent.core.retrieval.provider_contract import ProviderSnapshot as ProviderSnapshot
from seektalent.core.retrieval.service import RetrievalService as RetrievalService
from seektalent.evaluation import AsyncJudgeLimiter as AsyncJudgeLimiter
from seektalent.models import (
    QueryOutcomeThresholds as QueryOutcomeThresholds,
    ResumeCandidate as ResumeCandidate,
    RuntimeSourceEvidence as RuntimeSourceEvidence,
)
from seektalent.providers import get_provider_adapter as get_provider_adapter
from seektalent.runtime.orchestrator import (
    RuntimeSourceRoundContext as RuntimeSourceRoundContext,
    WorkflowRuntime as WorkflowRuntime,
)
from seektalent.runtime.public_events import public_source_reason_code as runtime_public_source_reason_code
from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult as RetrievalExecutionResult
from seektalent.runtime.source_lanes import (
    RuntimeSourceLanePlan as RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest as RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult as RuntimeSourceLaneResult,
)
from seektalent.runtime.source_query_intent import (
    RuntimeSourceQueryIntent as RuntimeSourceQueryIntent,
    RuntimeSourceQueryPolicy as RuntimeSourceQueryPolicy,
)
from seektalent.runtime.source_round_dispatch import (
    SourceRoundAdapter as SourceRoundAdapter,
    SourceRoundAdapterResult as SourceRoundAdapterResult,
    SourceRoundDispatchRequest as SourceRoundDispatchRequest,
    SourceRoundDispatchStatus as SourceRoundDispatchStatus,
)
from seektalent.source_contracts import (
    LogicalQueryDispatch as LogicalQueryDispatch,
    RegisteredSource as RegisteredSource,
    SourceBudget as SourceBudget,
    SourceCapabilities as SourceCapabilities,
    SourceLaneRequest as SourceLaneRequest,
    SourceLaneResult as SourceLaneResult,
    SourcePlan as SourcePlan,
    SourceRegistry as SourceRegistry,
)
from seektalent.sources.cts.filter_projection import project_constraints_to_cts as project_constraints_to_cts
from seektalent.sources.liepin.reason_codes import LIEPIN_PUBLIC_EVENT_REASON_MAP as LIEPIN_PUBLIC_EVENT_REASON_MAP
from seektalent.sources.liepin.runtime_lane import (
    LiepinWorkerClient as LiepinWorkerClient,
    run_liepin_logical_query_bundle as run_liepin_logical_query_bundle,
    run_liepin_source_lane as run_liepin_source_lane,
)
from seektalent.sources.provider_card_lane import run_provider_card_lane as run_provider_card_lane
from seektalent.tracing import RunTracer as RunTracer

from .evidence import (
    _provider_snapshot_values as _provider_snapshot_values,
    _provider_snapshots_by_candidate_key as _provider_snapshots_by_candidate_key,
    _record_source_provider_results_from_lane as _record_source_provider_results_from_lane,
    _snapshot_for_candidate as _snapshot_for_candidate,
    _source_evidence_for_candidate as _source_evidence_for_candidate,
    _source_lane_result_from_retrieval_result as _source_lane_result_from_retrieval_result,
)
from .query_policy import (
    _liepin_source_query_policy as _liepin_source_query_policy,
    default_source_query_policies as default_source_query_policies,
)
from .registry import (
    _budget_with_overrides as _budget_with_overrides,
    _liepin_worker_client as _liepin_worker_client,
    _registered_cts_source as _registered_cts_source,
    _registered_liepin_source as _registered_liepin_source,
    _source_plan_builder as _source_plan_builder,
    build_default_source_registry as build_default_source_registry,
    build_source_lane_request_runner as build_source_lane_request_runner,
)
from .round_adapters import (
    _SOURCE_ROUND_STATUSES as _SOURCE_ROUND_STATUSES,
    _run_cts_source_round as _run_cts_source_round,
    _run_liepin_source_round as _run_liepin_source_round,
    _source_filter_warning_reason as _source_filter_warning_reason,
    _source_round_status as _source_round_status,
    default_source_round_adapter_provider as default_source_round_adapter_provider,
)
from .runtime_factory import (
    _build_provider_retrieval_service as _build_provider_retrieval_service,
    build_source_enabled_runtime as build_source_enabled_runtime,
)


def public_source_reason_code(reason_code: object) -> str | None:
    public_code = runtime_public_source_reason_code(reason_code)
    if public_code is not None:
        return public_code
    text = str(reason_code or "").strip()
    if not text:
        return None
    mapped = LIEPIN_PUBLIC_EVENT_REASON_MAP.get(text)
    return runtime_public_source_reason_code(mapped)
