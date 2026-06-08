from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime

import httpx

from seektalent.config import AppSettings
from seektalent.corpus.runtime import ProviderReturnedCandidate, build_deterministic_provider_request_id
from seektalent.core.retrieval.provider_contract import ProviderSnapshot
from seektalent.core.retrieval.service import RetrievalService
from seektalent.evaluation import AsyncJudgeLimiter
from seektalent.models import QueryOutcomeThresholds, ResumeCandidate, RuntimeSourceEvidence
from seektalent.providers import get_provider_adapter
from seektalent.runtime.orchestrator import RuntimeSourceRoundContext, WorkflowRuntime
from seektalent.runtime.public_events import public_source_reason_code as runtime_public_source_reason_code
from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult
from seektalent.runtime.source_lanes import RuntimeSourceLanePlan, RuntimeSourceLaneRequest, RuntimeSourceLaneResult
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent, RuntimeSourceQueryPolicy
from seektalent.runtime.source_round_dispatch import (
    SourceRoundAdapter,
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
    SourceRoundDispatchStatus,
)
from seektalent.source_contracts import (
    LogicalQueryDispatch,
    RegisteredSource,
    SourceBudget,
    SourceCapabilities,
    SourceLaneRequest,
    SourceLaneResult,
    SourcePlan,
    SourceRegistry,
)
from seektalent.sources.cts.filter_projection import project_constraints_to_cts
from seektalent.sources.liepin.reason_codes import LIEPIN_PUBLIC_EVENT_REASON_MAP
from seektalent.sources.liepin.runtime_lane import (
    LiepinWorkerClient,
    run_liepin_logical_query_bundle,
    run_liepin_source_lane,
)
from seektalent.sources.provider_card_lane import run_provider_card_lane
from seektalent.tracing import RunTracer

_SOURCE_ROUND_STATUSES: dict[str, SourceRoundDispatchStatus] = {
    "blocked": "blocked", "completed": "completed", "failed": "failed", "partial": "partial"
}


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


def public_source_reason_code(reason_code: object) -> str | None:
    public_code = runtime_public_source_reason_code(reason_code)
    if public_code is not None:
        return public_code
    text = str(reason_code or "").strip()
    if not text:
        return None
    mapped = LIEPIN_PUBLIC_EVENT_REASON_MAP.get(text)
    return runtime_public_source_reason_code(mapped)


def _build_provider_retrieval_service(settings: AppSettings) -> RetrievalService:
    return RetrievalService(provider=get_provider_adapter(settings))


def build_default_source_registry(settings: AppSettings) -> SourceRegistry:
    return SourceRegistry(
        [
            _registered_cts_source(settings),
            _registered_liepin_source(),
        ],
        default_source_ids=("cts",),
    )


def build_source_lane_request_runner(settings: AppSettings):
    async def run_source_lane_request(
        request: RuntimeSourceLaneRequest,
        source_client: object | None,
    ) -> RuntimeSourceLaneResult:
        return await run_liepin_source_lane(
            settings=settings,
            request=request,
            worker_client=_liepin_worker_client(source_client),
        )

    return run_source_lane_request


def default_source_round_adapter_provider(
    runtime: WorkflowRuntime,
    context: RuntimeSourceRoundContext,
) -> Mapping[str, SourceRoundAdapter]:
    adapters: dict[str, SourceRoundAdapter] = {}
    for source_id in context.source_plan_by_source:
        if source_id == "cts":
            adapters[source_id] = lambda request, source_id=source_id: _run_cts_source_round(
                runtime=runtime,
                context=context,
                request=request,
                source_id=source_id,
            )
        elif source_id == "liepin":
            adapters[source_id] = lambda request, source_id=source_id: _run_liepin_source_round(
                runtime=runtime,
                context=context,
                request=request,
                source_id=source_id,
            )
    return adapters


def default_source_query_policies(
    *,
    settings: AppSettings,
    source_plan: tuple[RuntimeSourceLanePlan, ...],
) -> Mapping[str, RuntimeSourceQueryPolicy]:
    policies: dict[str, RuntimeSourceQueryPolicy] = {}
    for lane in source_plan:
        if lane.source == "liepin":
            policies[lane.source] = _liepin_source_query_policy(settings)
    return policies


def _liepin_source_query_policy(settings: AppSettings) -> RuntimeSourceQueryPolicy:
    return RuntimeSourceQueryPolicy(
        requested_count_caps_by_lane={
            "exploit": settings.liepin_exploit_detail_target,
            "generic_explore": settings.liepin_explore_detail_target,
        },
        provider_scan_multiplier=3,
        provider_scan_cap=settings.liepin_opencli_max_cards_per_task,
    )


def _registered_cts_source(settings: AppSettings) -> RegisteredSource:
    budget = SourceBudget(card_target=10, detail_target=0, scan_limit=10)

    async def run_card_lane(request: SourceLaneRequest) -> SourceLaneResult:
        retrieval_service = _build_provider_retrieval_service(settings)
        return await run_provider_card_lane(
            request=request,
            search=retrieval_service.search,
            provider_context={
                "runtime_source_lane_mode": "cts_single_page",
                "target_new": str(request.budget.card_target),
                "max_pages": "1",
                "allow_pagination": "false",
            },
        )

    return RegisteredSource(
        source_id="cts",
        label="CTS",
        capabilities=SourceCapabilities(
            supports_card_search=True,
            supports_detail_fetch=False,
            supports_native_filters=True,
            supports_incremental_detail=False,
            requires_human_login=False,
            max_safe_concurrency=1,
            stable_external_id=True,
            stable_dedup_key=True,
        ),
        default_budget=budget,
        plan=_source_plan_builder(source_id="cts", label="CTS", budget=budget),
        run_card_lane=run_card_lane,
    )


def _registered_liepin_source() -> RegisteredSource:
    budget = SourceBudget(card_target=30, detail_target=6, scan_limit=30)

    async def run_card_lane(request: SourceLaneRequest) -> SourceLaneResult:
        return SourceLaneResult(
            runtime_run_id=request.runtime_run_id,
            source_plan_id=request.source_plan_id,
            source_lane_run_id=request.source_lane_run_id,
            source_id=request.source_id,
            lane_mode=request.lane_mode,
            attempt=request.attempt,
            status="blocked",
            blocked_reason_code="source_context_required",
        )

    return RegisteredSource(
        source_id="liepin",
        label="Liepin",
        capabilities=SourceCapabilities(
            supports_card_search=True,
            supports_detail_fetch=True,
            supports_native_filters=True,
            supports_incremental_detail=True,
            requires_human_login=True,
            max_safe_concurrency=1,
            stable_external_id=True,
            stable_dedup_key=True,
        ),
        default_budget=budget,
        plan=_source_plan_builder(source_id="liepin", label="Liepin", budget=budget),
        run_card_lane=run_card_lane,
        run_detail_lane=run_card_lane,
    )


def _source_plan_builder(*, source_id: str, label: str, budget: SourceBudget):
    def build_plan(
        *,
        runtime_run_id: str,
        source_index: int,
        budget_overrides: Mapping[str, int] | None,
    ) -> SourcePlan:
        selected_budget = _budget_with_overrides(budget, budget_overrides)
        return SourcePlan(
            source_id=source_id,
            source_plan_id=f"{runtime_run_id}:source:{source_index}:{source_id}",
            runtime_run_id=runtime_run_id,
            label=label,
            budget=selected_budget,
        )

    return build_plan


def _budget_with_overrides(
    budget: SourceBudget,
    overrides: Mapping[str, int] | None,
) -> SourceBudget:
    if not overrides:
        return budget
    return SourceBudget(
        card_target=int(overrides.get("card_target", budget.card_target)),
        detail_target=int(overrides.get("detail_target", budget.detail_target)),
        scan_limit=int(overrides.get("scan_limit", budget.scan_limit)),
    )


async def _run_cts_source_round(
    *,
    runtime: WorkflowRuntime,
    context: RuntimeSourceRoundContext,
    request: SourceRoundDispatchRequest,
    source_id: str,
) -> SourceRoundAdapterResult:
    source_plan = context.source_plan_by_source[source_id]
    projection_result = project_constraints_to_cts(
        requirement_sheet=context.run_state.requirement_sheet,
        filter_plan=context.proposed_filter_plan,
    )
    retrieval_plan = context.retrieval_plan.model_copy(
        update={
            "projected_provider_filters": projection_result.provider_filters,
            "runtime_only_constraints": projection_result.runtime_only_constraints,
        }
    )
    try:
        result = await runtime.retrieval_runtime.execute_logical_dispatch_search(
            round_no=context.round_no,
            retrieval_plan=retrieval_plan,
            logical_queries=request.logical_queries,
            base_adapter_notes=[*context.adapter_notes, *projection_result.adapter_notes],
            target_new=context.target_new,
            seen_resume_ids=set(context.seen_resume_ids),
            seen_dedup_keys=set(context.seen_dedup_keys),
            tracer=context.tracer,
            score_for_query_outcome=lambda candidates: runtime._score_candidates_for_query_outcome(
                round_no=context.round_no,
                candidates=candidates,
                run_state=context.run_state,
                runtime_only_constraints=retrieval_plan.runtime_only_constraints,
            ),
            query_outcome_thresholds=QueryOutcomeThresholds(),
            record_provider_return_batch=lambda batch: runtime._record_corpus_provider_results(
                tracer=context.tracer,
                returned_candidates=batch,
            ),
        )
    except (TimeoutError, httpx.HTTPError):
        return SourceRoundAdapterResult(
            source=source_id,
            status="failed",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="source_provider_failed",
            diagnostics=(f"{source_id} provider request failed before completion",),
        )
    lane_result = _source_lane_result_from_retrieval_result(
        source_id=source_id,
        source_plan=source_plan,
        retrieval_result=result,
        round_no=context.round_no,
        runtime_run_id=context.tracer.run_id,
        logical_queries=request.logical_queries,
    )
    return SourceRoundAdapterResult(
        source=source_id,
        status="completed",
        candidates=tuple(result.new_candidates),
        raw_candidate_count=result.search_observation.raw_candidate_count,
        diagnostics=tuple(result.search_observation.adapter_notes),
        retrieval_result=result,
        lane_result=lane_result,
    )


async def _run_liepin_source_round(
    *,
    runtime: WorkflowRuntime,
    context: RuntimeSourceRoundContext,
    request: SourceRoundDispatchRequest,
    source_id: str,
) -> SourceRoundAdapterResult:
    source_plan = context.source_plan_by_source[source_id]
    safe_posture = dict(source_plan.safe_posture)
    if source_plan.backend_mode == "blocked" or safe_posture.get("status") == "blocked" or context.source_context is None:
        safe_reason_code = str(
            safe_posture.get("safe_reason_code")
            or safe_posture.get("reason")
            or "source_browser_backend_unavailable"
        )
        return SourceRoundAdapterResult(
            source=source_id,
            status="blocked",
            safe_reason_code=safe_reason_code,
            diagnostics=(f"{source_id} source blocked before provider dispatch",),
        )
    result = await run_liepin_logical_query_bundle(
        settings=runtime.settings,
        runtime_run_id=context.tracer.run_id,
        source_plan_id=source_plan.source_plan_id,
        job_title=str(getattr(context.run_state.input_truth, "job_title", "")),
        jd=str(getattr(context.run_state.input_truth, "jd", "")),
        notes=str(getattr(context.run_state.input_truth, "notes", "") or ""),
        requirement_sheet=request.requirement_sheet,
        logical_queries=request.logical_queries,
        source_query_intents=request.source_query_intents_by_source.get(source_id),
        source_budget_policy=source_plan.source_budget_policy,
        liepin_context=context.source_context,
    )
    _record_source_provider_results_from_lane(
        runtime=runtime,
        source_id=source_id,
        result=result,
        logical_queries=request.logical_queries,
        tracer=context.tracer,
    )
    filter_warning_reason = _source_filter_warning_reason(request.source_query_intents_by_source.get(source_id, ()))
    return SourceRoundAdapterResult(
        source=source_id,
        status=_source_round_status(result.status),
        candidates=tuple(result.candidate_store_updates.values()),
        raw_candidate_count=int(result.raw_candidate_count or 0),
        safe_reason_code=result.stop_reason_code or result.blocked_reason_code or filter_warning_reason,
        lane_result=result,
    )


def _source_filter_warning_reason(intents: tuple[RuntimeSourceQueryIntent, ...]) -> str | None:
    supported_filter_fields = {
        "degree_requirement",
        "school_type_requirement",
        "experience_requirement",
        "age_requirement",
    }
    if any(
        filter_intent.field not in supported_filter_fields for intent in intents for filter_intent in intent.filter_intents
    ):
        return "source_filter_unsupported"
    return None


def _source_lane_result_from_retrieval_result(
    *,
    source_id: str,
    source_plan,
    retrieval_result: RetrievalExecutionResult,
    round_no: int,
    runtime_run_id: str,
    logical_queries: Sequence[LogicalQueryDispatch],
) -> RuntimeSourceLaneResult:
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    source_lane_run_id = f"{source_plan.source_plan_id}:round:{round_no}:{source_id}"
    fallback_query_fingerprint = logical_queries[0].query_fingerprint if logical_queries else None
    query_fingerprint_by_resume_id: dict[str, str | None] = {}
    provider_rank_by_resume_id: dict[str, int | None] = {}
    for hit in retrieval_result.query_resume_hits:
        query_fingerprint_by_resume_id.setdefault(hit.resume_id, hit.query_fingerprint)
        provider_rank_by_resume_id.setdefault(hit.resume_id, hit.rank_global_in_query or hit.rank_in_query)
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan.source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source=source_id,
        lane_mode="card",
        attempt=round_no,
        status="completed",
        candidate_store_updates={candidate.resume_id: candidate for candidate in retrieval_result.new_candidates},
        raw_candidate_count=retrieval_result.search_observation.raw_candidate_count,
        source_evidence_updates=tuple(
            _source_evidence_for_candidate(
                source_id=source_id,
                source_plan=source_plan,
                candidate=candidate,
                collected_at=collected_at,
                provider_rank=provider_rank_by_resume_id.get(candidate.resume_id) or index,
                query_fingerprint=query_fingerprint_by_resume_id.get(
                    candidate.resume_id,
                    fallback_query_fingerprint,
                ),
                source_lane_run_id=source_lane_run_id,
                runtime_run_id=runtime_run_id,
            )
            for index, candidate in enumerate(retrieval_result.new_candidates, start=1)
        ),
    )


def _source_evidence_for_candidate(
    *,
    source_id: str,
    source_plan,
    candidate: ResumeCandidate,
    collected_at: str,
    runtime_run_id: str,
    provider_rank: int | None = None,
    query_fingerprint: str | None = None,
    source_lane_run_id: str | None = None,
) -> RuntimeSourceEvidence:
    provider_candidate_key = candidate.source_resume_id or candidate.dedup_key or candidate.resume_id
    provider_candidate_key_hash = hashlib.sha256(
        f"{runtime_run_id}:{source_id}:{provider_candidate_key}".encode("utf-8")
    ).hexdigest()
    provider_snapshot_ref = None
    safe_summary_ref = None
    if isinstance(candidate.raw, dict):
        raw_snapshot_ref = candidate.raw.get("provider_snapshot_ref")
        raw_summary_ref = candidate.raw.get("safe_summary_ref")
        provider_snapshot_ref = raw_snapshot_ref if isinstance(raw_snapshot_ref, str) else None
        safe_summary_ref = raw_summary_ref if isinstance(raw_summary_ref, str) else None
    return RuntimeSourceEvidence(
        evidence_id=f"{source_plan.source_plan_id}:{source_id}:{provider_candidate_key_hash}",
        source=source_id,
        provider=source_id,
        source_plan_id=source_plan.source_plan_id,
        source_lane_run_id=source_lane_run_id or f"{source_plan.source_plan_id}:lane:1",
        evidence_level="card",
        candidate_resume_id=candidate.resume_id,
        provider_candidate_key_hash=provider_candidate_key_hash,
        provider_rank=provider_rank,
        query_fingerprint=query_fingerprint,
        provider_snapshot_ref=provider_snapshot_ref,
        safe_summary_ref=safe_summary_ref,
        collected_at=collected_at,
        score_hint=None,
        reason_code="source_card_candidate",
    )


def _record_source_provider_results_from_lane(
    *,
    runtime: WorkflowRuntime,
    source_id: str,
    result: RuntimeSourceLaneResult,
    logical_queries: Sequence[LogicalQueryDispatch],
    tracer: RunTracer,
) -> None:
    if not result.candidate_store_updates or not result.provider_snapshots:
        return

    logical_query_by_fingerprint = {
        str(query.query_fingerprint): query
        for query in logical_queries
        if getattr(query, "query_fingerprint", None)
    }
    fallback_query = logical_queries[0] if logical_queries else None
    candidates_by_resume_id = {
        str(candidate.resume_id): candidate
        for candidate in result.candidate_store_updates.values()
        if getattr(candidate, "resume_id", None)
    }
    snapshots_by_key = _provider_snapshots_by_candidate_key(_provider_snapshot_values(result.provider_snapshots))
    returned_candidates: list[ProviderReturnedCandidate] = []
    seen_candidates: set[str] = set()

    for index, evidence in enumerate(result.source_evidence_updates, start=1):
        candidate = candidates_by_resume_id.get(str(evidence.candidate_resume_id))
        if candidate is None:
            continue
        snapshot = _snapshot_for_candidate(candidate, snapshots_by_key)
        if snapshot is None:
            continue
        query = logical_query_by_fingerprint.get(str(evidence.query_fingerprint or "")) or fallback_query
        query_instance_id = str(getattr(query, "query_instance_id", "") or "")
        query_fingerprint = str(evidence.query_fingerprint or getattr(query, "query_fingerprint", "") or "")
        round_no = int(getattr(query, "round_no", 0) or 0)
        source_lane_run_id = str(evidence.source_lane_run_id or result.source_lane_run_id)
        provider_rank = int(evidence.provider_rank or index)
        seen_candidates.add(str(candidate.resume_id))
        returned_candidates.append(
            ProviderReturnedCandidate(
                candidate=candidate,
                provider_snapshot=snapshot,
                stage_id=source_lane_run_id,
                round_no=round_no,
                query_instance_id=query_instance_id,
                query_fingerprint=query_fingerprint,
                provider_name=source_id,
                provider_request_id=build_deterministic_provider_request_id(
                    provider_name=source_id,
                    query_instance_id=query_instance_id,
                    query_fingerprint=query_fingerprint,
                    page_no=1,
                    fetch_no=1,
                    request_payload={
                        "source_plan_id": result.source_plan_id,
                        "source_lane_run_id": source_lane_run_id,
                    },
                ),
                provider_rank=provider_rank,
                provider_page_no=1,
                provider_fetch_no=1,
                attempt_no=result.attempt,
            )
        )

    for index, candidate in enumerate(result.candidate_store_updates.values(), start=1):
        if str(candidate.resume_id) in seen_candidates:
            continue
        snapshot = _snapshot_for_candidate(candidate, snapshots_by_key)
        if snapshot is None:
            continue
        query_instance_id = str(getattr(fallback_query, "query_instance_id", "") or "")
        query_fingerprint = str(getattr(fallback_query, "query_fingerprint", "") or "")
        returned_candidates.append(
            ProviderReturnedCandidate(
                candidate=candidate,
                provider_snapshot=snapshot,
                stage_id=result.source_lane_run_id,
                round_no=int(getattr(fallback_query, "round_no", 0) or 0),
                query_instance_id=query_instance_id,
                query_fingerprint=query_fingerprint,
                provider_name=source_id,
                provider_request_id=build_deterministic_provider_request_id(
                    provider_name=source_id,
                    query_instance_id=query_instance_id,
                    query_fingerprint=query_fingerprint,
                    page_no=1,
                    fetch_no=1,
                    request_payload={
                        "source_plan_id": result.source_plan_id,
                        "source_lane_run_id": result.source_lane_run_id,
                    },
                ),
                provider_rank=index,
                provider_page_no=1,
                provider_fetch_no=1,
                attempt_no=result.attempt,
            )
        )

    if returned_candidates:
        runtime._record_corpus_provider_results(
            tracer=tracer,
            returned_candidates=returned_candidates,
        )


def _liepin_worker_client(value: object | None) -> LiepinWorkerClient | None:
    if value is None:
        return None
    if isinstance(value, LiepinWorkerClient):
        return value
    raise TypeError("liepin_worker_client_invalid")


def _source_round_status(status: str) -> SourceRoundDispatchStatus:
    return _SOURCE_ROUND_STATUSES.get(status, "failed")


def _provider_snapshot_values(provider_snapshots: Sequence[object]) -> tuple[ProviderSnapshot, ...]:
    return tuple(snapshot for snapshot in provider_snapshots if isinstance(snapshot, ProviderSnapshot))


def _provider_snapshots_by_candidate_key(provider_snapshots: Sequence[ProviderSnapshot]) -> dict[str, ProviderSnapshot]:
    snapshots: dict[str, ProviderSnapshot] = {}
    for snapshot in provider_snapshots:
        for key in (
            getattr(snapshot, "provider_subject_id", None),
            getattr(snapshot, "synthetic_candidate_fingerprint", None),
        ):
            if isinstance(key, str) and key:
                snapshots[key] = snapshot
    return snapshots


def _snapshot_for_candidate(
    candidate: ResumeCandidate,
    snapshots_by_key: Mapping[str, ProviderSnapshot],
) -> ProviderSnapshot | None:
    for key in (candidate.resume_id, candidate.source_resume_id, candidate.dedup_key):
        if key and key in snapshots_by_key:
            return snapshots_by_key[key]
    return None
