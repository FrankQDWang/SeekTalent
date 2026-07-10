from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, cast

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.models import ResumeCandidate, RuntimeSourceEvidence
from seektalent.providers.liepin.adapter import LiepinProviderAdapter
from seektalent.providers.liepin.card_policy import (
    LiepinCardDecisionAction,
    LiepinCardSummary,
    build_liepin_card_decisions,
)
from seektalent.providers.liepin.client import (
    LiepinWorkerClient,
    LiepinWorkerModeError,
    build_liepin_worker_client,
    is_live_liepin_worker_mode,
)
from seektalent.providers.liepin.filter_compiler import LiepinSourceQueryIntent
from seektalent.providers.liepin.source_compiler import LiepinCompiledQuery, compile_liepin_source_query_intents
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerPartialSearchError
from seektalent.sources.liepin.reason_codes import LIEPIN_WORKER_SAFE_REASON_CODES
from seektalent.sources.liepin.context import RuntimeLiepinContext, RuntimeLiepinContextInput
from seektalent.sources.liepin.context import normalize_runtime_liepin_context
from seektalent.source_contracts import (
    LogicalQueryDispatch,
    RuntimeDetailRecommendation,
    RuntimeEvidenceLevel,
    RuntimeQueryCandidateAttribution,
    RuntimeQueryPackage,
    RuntimeSourceBudgetPolicy,
    RuntimeSourceLaneEventType,
    RuntimeSourceLaneEvent,
    RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
    RuntimeSourceLaneStatus,
    SourceQueryExecutionOutcome,
)

if TYPE_CHECKING:
    from seektalent.models import RequirementSheet


def liepin_backend_posture(settings: AppSettings) -> dict[str, str]:
    worker_mode = settings.liepin_worker_mode
    if worker_mode == "opencli":
        return {"backend_mode": "opencli", "reason": worker_mode}
    if worker_mode == "external_http":
        return {"backend_mode": "external_http", "reason": worker_mode}
    if worker_mode == "fake_fixture" and settings.liepin_allow_fake_fixture_worker:
        return {"backend_mode": "fake_fixture", "reason": "explicit_test_fixture"}
    return {"backend_mode": "blocked", "reason": "no_live_action_backend"}


async def run_liepin_source_lane(
    *,
    settings: AppSettings,
    request: RuntimeSourceLaneRequest,
    worker_client: LiepinWorkerClient | None = None,
    compiled_search_request: SearchRequest | None = None,
) -> RuntimeSourceLaneResult:
    runtime_run_id = request.runtime_run_id or f"runtime-source-lane:{request.source}"
    source_plan_id = request.source_plan_id or f"{runtime_run_id}:source:0:liepin"
    source_lane_run_id = request.source_lane_run_id or f"{source_plan_id}:lane:{request.attempt}"
    if request.lane_mode == "detail" and request.approved_detail_lease is None:
        return _blocked_detail_result(
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            attempt=request.attempt,
        )
    if request.lane_mode == "detail":
        if not _detail_lease_matches_request(
            request=request,
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
        ):
            return _blocked_detail_result(
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                attempt=request.attempt,
            )
        return await _run_detail_lane(
            settings=settings,
            request=request,
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            worker_client=worker_client,
        )
    if request.lane_mode != "card":
        raise ValueError(f"Unsupported Liepin source lane mode: {request.lane_mode}")

    context = normalize_runtime_liepin_context(request.source_context)
    client = worker_client or build_liepin_worker_client(settings)
    query_started = False

    def mark_query_started() -> None:
        nonlocal query_started
        query_started = True

    provider = _build_provider(
        settings=settings,
        worker_client=client,
        worker_search_started_callback=mark_query_started,
    )
    search_request = _card_search_request(
        request=request,
        context=context,
        source_lane_run_id=source_lane_run_id,
        compiled_search_request=compiled_search_request,
    )
    query_terms = list(search_request.query_terms)
    query_fingerprint = search_request.provider_context.get("query_fingerprint") or hashlib.sha256(
        " ".join(query_terms).encode("utf-8")
    ).hexdigest()
    try:
        search_result = await provider.search(
            search_request,
            round_no=1,
            trace_id=source_lane_run_id,
        )
        if search_request.provider_context.get("liepin_fetch_strategy") == "detail_backed_resume_search":
            _assert_detail_backed_liepin_search_result(search_result)
    except LiepinWorkerPartialSearchError as error:
        stop_reason_code = runtime_safe_reason_code_from_worker_failure_code(
            error.code,
            cards_collected=error.cards_collected > 0,
        )
        if search_request.provider_context.get("liepin_fetch_strategy") == "detail_backed_resume_search":
            _assert_detail_backed_liepin_search_result(error.partial_search_result)
        return _card_lane_result_from_search_result(
            request=request,
            search_result=error.partial_search_result,
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            query_terms=query_terms,
            query_fingerprint=query_fingerprint,
            status="partial",
            query_started=query_started,
            stop_reason_code=stop_reason_code,
        )
    except LiepinWorkerModeError as error:
        reason_code = runtime_safe_reason_code_from_worker_failure_code(error.code)
        blocked_result = _blocked_card_result(
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            attempt=request.attempt,
            reason_code=reason_code,
            query_started=query_started,
            safe_error_summary=_safe_worker_error_summary(error, reason_code=reason_code),
        )
        partial_search_result = getattr(error, "partial_search_result", None)
        if isinstance(partial_search_result, SearchResult):
            workflow_events = _workflow_events_from_search_result(
                search_result=partial_search_result,
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                attempt=request.attempt,
                start_seq=len(blocked_result.events) + 1,
                status_override="blocked",
            )
            if workflow_events:
                return RuntimeSourceLaneResult(
                    runtime_run_id=blocked_result.runtime_run_id,
                    source_plan_id=blocked_result.source_plan_id,
                    source_lane_run_id=blocked_result.source_lane_run_id,
                    source=blocked_result.source,
                    lane_mode=blocked_result.lane_mode,
                    attempt=blocked_result.attempt,
                    status=blocked_result.status,
                    raw_candidate_count=partial_search_result.raw_candidate_count,
                    query_started=blocked_result.query_started,
                    events=blocked_result.events + workflow_events,
                    blocked_reason_code=blocked_result.blocked_reason_code,
                    stop_reason_code=blocked_result.stop_reason_code,
                    retryable=blocked_result.retryable,
                )
        return blocked_result
    return _card_lane_result_from_search_result(
        request=request,
        search_result=search_result,
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        query_terms=query_terms,
        query_fingerprint=query_fingerprint,
        status="completed",
        query_started=query_started,
    )


async def run_liepin_logical_query_bundle(
    *,
    settings: AppSettings,
    runtime_run_id: str,
    source_plan_id: str,
    job_title: str,
    jd: str,
    notes: str,
    requirement_sheet: "RequirementSheet",
    logical_queries: tuple[LogicalQueryDispatch, ...],
    source_budget_policy: RuntimeSourceBudgetPolicy,
    liepin_context: RuntimeLiepinContextInput | None,
    source_query_intents: tuple[LiepinSourceQueryIntent, ...] | None = None,
    worker_client: LiepinWorkerClient | None = None,
) -> RuntimeSourceLaneResult:
    compiled_bundle = (
        compile_liepin_source_query_intents(source_query_intents) if source_query_intents is not None else None
    )
    compiled_queries = compiled_bundle.queries if compiled_bundle is not None else ()
    context = normalize_runtime_liepin_context(liepin_context)

    async def run_logical_query(index: int, logical_query: LogicalQueryDispatch) -> RuntimeSourceLaneResult:
        logical_compiled_queries = tuple(
            query for query in compiled_queries if query.intent.query_instance_id == logical_query.query_instance_id
        )
        if not logical_compiled_queries:
            logical_compiled_queries = (None,)
        logical_result: RuntimeSourceLaneResult | None = None
        target_results: list[RuntimeSourceLaneResult] = []
        for target_index, compiled_query in enumerate(logical_compiled_queries, start=1):
            source_query_terms = logical_query.query_terms
            logical_query_role = logical_query.query_role
            logical_requested_count = logical_query.requested_count
            logical_provider_scan_limit = min(logical_query.requested_count, source_budget_policy.max_cards)
            logical_unsupported_filter_reason_codes: tuple[str, ...] = ()
            compiled_request = None
            if compiled_query is not None:
                compiled_request = compiled_query.search_request
                source_query_terms = tuple(compiled_request.query_terms)
                logical_query_role = compiled_query.intent.query_role
                logical_requested_count = compiled_query.intent.requested_count
                logical_provider_scan_limit = compiled_query.intent.provider_scan_limit
                logical_unsupported_filter_reason_codes = tuple(
                    item.safe_reason_code for item in compiled_query.unsupported_filters
                )
            lane_run_id = f"{source_plan_id}:round:{logical_query.round_no}:lane:{index}"
            if compiled_query is not None:
                lane_run_id = f"{lane_run_id}:target:{target_index}"
            result = await run_liepin_source_lane(
                settings=settings,
                request=RuntimeSourceLaneRequest(
                    source="liepin",
                    lane_mode="card",
                    job_title=job_title,
                    jd=jd,
                    notes=notes,
                    requirement_sheet=requirement_sheet,
                    runtime_run_id=runtime_run_id,
                    source_plan_id=source_plan_id,
                    source_lane_run_id=lane_run_id,
                    source_query_terms=source_query_terms,
                    logical_query_instance_id=logical_query.query_instance_id,
                    logical_query_fingerprint=logical_query.query_fingerprint,
                    logical_query_role=logical_query_role,
                    logical_keyword_query=logical_query.keyword_query,
                    logical_requested_count=logical_requested_count,
                    logical_provider_scan_limit=logical_provider_scan_limit,
                    logical_unsupported_filter_reason_codes=logical_unsupported_filter_reason_codes,
                    source_budget_policy=source_budget_policy,
                    source_context=context.to_runtime_payload(),
                ),
                worker_client=worker_client,
                compiled_search_request=compiled_request,
            )
            result = _with_liepin_executed_query_package(
                result,
                logical_query=logical_query,
                compiled_query=compiled_query,
            )
            target_results.append(result)
            logical_result = (
                result if logical_result is None else merge_liepin_card_lane_results(logical_result, result)
            )
            if len(logical_result.candidate_store_updates) >= logical_requested_count:
                break
        if logical_result is None:
            raise ValueError("Liepin logical query bundle requires at least one logical query.")
        return _with_liepin_query_execution_outcome(
            logical_result,
            logical_query=logical_query,
            target_results=target_results,
        )

    logical_results: dict[int, RuntimeSourceLaneResult] = {}
    if settings.liepin_worker_mode == "opencli" or context.backend_mode == "opencli":
        for index, logical_query in enumerate(logical_queries, start=1):
            logical_results[index] = await run_logical_query(index, logical_query)
    else:
        tasks: dict[int, asyncio.Task[RuntimeSourceLaneResult]] = {}
        async with asyncio.TaskGroup() as task_group:
            for index, logical_query in enumerate(logical_queries, start=1):
                tasks[index] = task_group.create_task(run_logical_query(index, logical_query))
        logical_results = {index: tasks[index].result() for index in tasks}

    merged_result: RuntimeSourceLaneResult | None = None
    for index in sorted(logical_results):
        logical_result = logical_results[index]
        merged_result = (
            logical_result
            if merged_result is None
            else merge_liepin_card_lane_results(merged_result, logical_result)
        )
    if merged_result is None:
        raise ValueError("Liepin logical query bundle requires at least one logical query.")
    return merged_result


def merge_liepin_card_lane_results(
    first: RuntimeSourceLaneResult,
    second: RuntimeSourceLaneResult,
) -> RuntimeSourceLaneResult:
    candidate_updates = dict(first.candidate_store_updates)
    candidate_updates.update(second.candidate_store_updates)
    normalized_updates = dict(first.normalized_store_updates)
    normalized_updates.update(second.normalized_store_updates)
    status: RuntimeSourceLaneStatus = "completed" if candidate_updates else second.status
    stop_reason_code = None if candidate_updates else (second.stop_reason_code or first.stop_reason_code)
    blocked_reason_code = None if candidate_updates else (second.blocked_reason_code or first.blocked_reason_code)
    return RuntimeSourceLaneResult(
        runtime_run_id=first.runtime_run_id,
        source_plan_id=first.source_plan_id,
        source_lane_run_id=first.source_lane_run_id,
        source=first.source,
        lane_mode=first.lane_mode,
        attempt=first.attempt,
        status=status,
        candidate_store_updates=candidate_updates,
        normalized_store_updates=normalized_updates,
        source_evidence_updates=first.source_evidence_updates + second.source_evidence_updates,
        provider_snapshots=first.provider_snapshots + second.provider_snapshots,
        raw_candidate_count=int(first.raw_candidate_count or 0) + int(second.raw_candidate_count or 0),
        provider_snapshot_refs=first.provider_snapshot_refs + second.provider_snapshot_refs,
        safe_summary_refs=first.safe_summary_refs + second.safe_summary_refs,
        detail_recommendations=first.detail_recommendations + second.detail_recommendations,
        events=first.events + second.events,
        executed_query_packages=first.executed_query_packages + second.executed_query_packages,
        query_started=first.query_started or second.query_started,
        query_execution_outcomes=first.query_execution_outcomes + second.query_execution_outcomes,
        candidate_query_attributions=first.candidate_query_attributions + second.candidate_query_attributions,
        blocked_reason_code=blocked_reason_code,
        stop_reason_code=stop_reason_code,
        retryable=first.retryable or second.retryable,
        safe_error_summary=first.safe_error_summary or second.safe_error_summary,
        error_ref=first.error_ref or second.error_ref,
    )


def _with_liepin_executed_query_package(
    result: RuntimeSourceLaneResult,
    *,
    logical_query: LogicalQueryDispatch,
    compiled_query: LiepinCompiledQuery | None,
) -> RuntimeSourceLaneResult:
    if result.status not in {"completed", "partial"}:
        return result
    return replace(
        result,
        executed_query_packages=result.executed_query_packages
        + (_liepin_executed_query_package(logical_query=logical_query, compiled_query=compiled_query),),
    )


def _with_liepin_query_execution_outcome(
    result: RuntimeSourceLaneResult,
    *,
    logical_query: LogicalQueryDispatch,
    target_results: Collection[RuntimeSourceLaneResult],
) -> RuntimeSourceLaneResult:
    raw_candidate_count = sum(int(item.raw_candidate_count or 0) for item in target_results)
    per_target_duplicate_candidate_count = sum(
        max(0, int(item.raw_candidate_count or 0) - len(item.candidate_store_updates))
        for item in target_results
    )
    target_candidate_count = sum(len(item.candidate_store_updates) for item in target_results)
    candidate_identity_keys = {
        candidate.dedup_key or candidate.resume_id
        for item in target_results
        for candidate in item.candidate_store_updates.values()
    }
    cross_target_duplicate_candidate_count = max(0, target_candidate_count - len(candidate_identity_keys))
    safe_reason = _shared_safe_reason(target_results)
    outcome = SourceQueryExecutionOutcome(
        query_instance_id=logical_query.query_instance_id,
        status=_outcome_status(target_results),
        dispatch_started=any(item.query_started for item in target_results),
        raw_candidate_count=raw_candidate_count,
        unique_candidate_count=len(candidate_identity_keys),
        duplicate_candidate_count=(
            per_target_duplicate_candidate_count + cross_target_duplicate_candidate_count
        ),
        exhausted_reason=safe_reason,
        safe_reason_code=safe_reason,
    )
    candidate_query_attributions = tuple(
        RuntimeQueryCandidateAttribution(
            source_kind="liepin",
            query_instance_id=logical_query.query_instance_id,
            resume_id=candidate.resume_id,
            dedup_key=candidate.dedup_key,
        )
        for candidate in result.candidate_store_updates.values()
    )
    return replace(
        result,
        query_execution_outcomes=result.query_execution_outcomes + (outcome,),
        candidate_query_attributions=result.candidate_query_attributions + candidate_query_attributions,
    )


def _outcome_status(target_results: Collection[RuntimeSourceLaneResult]):
    statuses = {result.status for result in target_results}
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"blocked"}:
        return "blocked"
    if statuses <= {"failed", "cancelled"}:
        return "failed"
    return "partial"


def _shared_safe_reason(target_results: Collection[RuntimeSourceLaneResult]) -> str | None:
    reasons = {
        reason
        for result in target_results
        if (reason := result.stop_reason_code or result.blocked_reason_code) is not None
    }
    return reasons.pop() if len(reasons) == 1 else None


def _liepin_executed_query_package(
    *,
    logical_query: LogicalQueryDispatch,
    compiled_query: LiepinCompiledQuery | None,
) -> RuntimeQueryPackage:
    if compiled_query is not None:
        intent = compiled_query.intent
        return RuntimeQueryPackage(
            source_kind="liepin",
            query_role=intent.query_role,
            lane_type=intent.lane_type,
            query_instance_id=intent.query_instance_id,
            query_fingerprint=intent.query_fingerprint,
            term_group_key=intent.term_group_key,
            query_terms=tuple(intent.query_terms),
            keyword_query=intent.keyword_query,
        )
    return RuntimeQueryPackage(
        source_kind="liepin",
        query_role=logical_query.query_role,
        lane_type=logical_query.lane_type,
        query_instance_id=logical_query.query_instance_id,
        query_fingerprint=logical_query.query_fingerprint,
        term_group_key=logical_query.term_group_key,
        query_terms=tuple(logical_query.query_terms),
        keyword_query=logical_query.keyword_query,
    )


def _card_lane_result_from_search_result(
    *,
    request: RuntimeSourceLaneRequest,
    search_result: SearchResult,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    query_terms: list[str],
    status: RuntimeSourceLaneStatus,
    query_fingerprint: str | None = None,
    query_started: bool = False,
    stop_reason_code: str | None = None,
) -> RuntimeSourceLaneResult:
    budget = request.source_budget_policy
    detail_backed = _is_detail_backed_liepin_search_result(search_result)
    source_plan = RuntimeSourceLanePlan(
        source_plan_id=source_plan_id,
        runtime_run_id=runtime_run_id,
        source="liepin",
        label="Liepin",
        lane_mode="detail" if detail_backed else "card",
        backend_mode="runtime_source_lane",
        max_cards=budget.max_cards,
        max_details=budget.max_detail_recommendations,
        source_budget_policy=budget,
    )
    candidates = tuple(search_result.candidates[: budget.max_cards])
    normalized_updates = {}
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
            evidence_level="detail" if detail_backed else "card",
            source_lane_run_id=source_lane_run_id,
            provider_rank=index,
            query_fingerprint=query_fingerprint,
        )
        for index, candidate in enumerate(candidates, start=1)
    )
    detail_recommendations = (
        ()
        if detail_backed
        else _detail_recommendations_for_candidates(
            source_plan_id=source_plan_id,
            candidates=candidates,
            evidence_updates=evidence_updates,
            query_terms=query_terms,
            job_title=request.job_title,
            max_recommendations=budget.max_detail_recommendations,
            budget_policy_version=budget.policy_version,
        )
    )
    base_events = _card_lane_events(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        attempt=request.attempt,
        raw_candidate_count=search_result.raw_candidate_count,
        candidate_count=len(candidates),
        detail_recommendation_count=len(detail_recommendations),
        detail_backed=detail_backed,
        status=status,
        stop_reason_code=stop_reason_code,
    )
    workflow_events = _workflow_events_from_search_result(
        search_result=search_result,
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        attempt=request.attempt,
        start_seq=len(base_events) + 1,
    )
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="detail" if detail_backed else "card",
        attempt=request.attempt,
        status=status,
        candidate_store_updates={candidate.resume_id: candidate for candidate in candidates},
        normalized_store_updates=normalized_updates,
        source_evidence_updates=evidence_updates,
        detail_recommendations=detail_recommendations,
        provider_snapshots=tuple(search_result.provider_snapshots),
        raw_candidate_count=search_result.raw_candidate_count,
        events=base_events + workflow_events,
        query_started=query_started,
        stop_reason_code=stop_reason_code,
    )


async def _run_detail_lane(
    *,
    settings: AppSettings,
    request: RuntimeSourceLaneRequest,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    worker_client: LiepinWorkerClient | None,
) -> RuntimeSourceLaneResult:
    context = normalize_runtime_liepin_context(request.source_context)
    query_terms = list(request.source_query_terms or _basic_source_query_terms(request))
    client = worker_client or build_liepin_worker_client(settings)
    provider = _build_provider(settings=settings, worker_client=client)
    search_result = await provider.search(
        SearchRequest(
            query_terms=query_terms,
            query_role="primary",
            keyword_query=" ".join(query_terms),
            adapter_notes=[request.notes or ""],
            runtime_constraints=[],
            fetch_mode="detail",
            page_size=10,
            provider_context=_detail_provider_context(
                request=request,
                context=context,
                source_lane_run_id=source_lane_run_id,
                query_terms=query_terms,
            ),
        ),
        round_no=1,
        trace_id=source_lane_run_id,
    )
    source_plan = RuntimeSourceLanePlan(
        source_plan_id=source_plan_id,
        runtime_run_id=runtime_run_id,
        source="liepin",
        label="Liepin",
        lane_mode="detail",
        backend_mode="runtime_source_lane",
        source_budget_policy=request.source_budget_policy,
    )
    candidates = tuple(search_result.candidates)
    normalized_updates = {}
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
            evidence_level="detail",
            source_lane_run_id=source_lane_run_id,
            provider_rank=index,
            query_fingerprint=request.logical_query_fingerprint,
        )
        for index, candidate in enumerate(candidates, start=1)
    )
    provider_snapshot_refs = tuple(
        ref
        for candidate in candidates
        if (ref := _candidate_ref(candidate, "provider_snapshot_ref", "raw_payload_artifact_ref")) is not None
    )
    safe_summary_refs = tuple(
        ref for candidate in candidates if (ref := _candidate_ref(candidate, "safe_summary_ref")) is not None
    )
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="detail",
        attempt=request.attempt,
        status="completed",
        candidate_store_updates={candidate.resume_id: candidate for candidate in candidates},
        normalized_store_updates=normalized_updates,
        source_evidence_updates=evidence_updates,
        provider_snapshots=tuple(search_result.provider_snapshots),
        raw_candidate_count=search_result.raw_candidate_count,
        provider_snapshot_refs=provider_snapshot_refs,
        safe_summary_refs=safe_summary_refs,
        events=(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                attempt=request.attempt,
                event_seq=1,
                event_type="detail_completed",
                status="completed",
                safe_counts={"details_opened": len(candidates)},
                artifact_refs=provider_snapshot_refs + safe_summary_refs,
            ),
        ),
    )


def _blocked_detail_result(
    *,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    attempt: int,
) -> RuntimeSourceLaneResult:
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="detail",
        attempt=attempt,
        status="blocked",
        blocked_reason_code="blocked_approval_missing",
        retryable=False,
        events=(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                attempt=attempt,
                event_seq=1,
                event_type="detail_blocked",
                status="blocked",
                safe_reason_code="blocked_approval_missing",
            ),
        ),
    )


def _blocked_card_result(
    *,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    attempt: int,
    reason_code: str,
    query_started: bool = False,
    safe_error_summary: str | None = None,
) -> RuntimeSourceLaneResult:
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="card",
        attempt=attempt,
        status="blocked",
        query_started=query_started,
        blocked_reason_code=reason_code,
        stop_reason_code=reason_code,
        retryable=reason_code in {"blocked_backend_unavailable", "failed_provider_error"},
        safe_error_summary=safe_error_summary,
        events=(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                attempt=attempt,
                event_seq=1,
                event_type="source_lane_blocked",
                status="blocked",
                safe_reason_code=reason_code,
            ),
        ),
    )


def _safe_worker_error_summary(error: LiepinWorkerModeError, *, reason_code: str) -> str:
    summary = f"{type(error).__name__}: {reason_code}"
    message = str(error).strip()
    if message.startswith("Liepin ") and len(message) <= 160:
        summary = f"{summary}; {message}"
    return summary


def _card_lane_events(
    *,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    attempt: int,
    raw_candidate_count: int | None,
    candidate_count: int,
    detail_recommendation_count: int,
    detail_backed: bool = False,
    status: RuntimeSourceLaneStatus,
    stop_reason_code: str | None = None,
) -> tuple[RuntimeSourceLaneEvent, ...]:
    event_type: RuntimeSourceLaneEventType = "source_lane_partial" if status == "partial" else "source_lane_completed"
    events = [
        RuntimeSourceLaneEvent(
            schema_version="runtime_source_lane_event_v1",
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            source="liepin",
            attempt=attempt,
            event_seq=1,
            event_type=event_type,
            status=status,
            safe_counts=(
                {"cards_seen": int(raw_candidate_count or candidate_count), "details_opened": candidate_count, "candidates": candidate_count}
                if detail_backed
                else {"cards_seen": int(raw_candidate_count or candidate_count), "candidates": candidate_count}
            ),
            safe_reason_code=stop_reason_code,
        )
    ]
    if detail_recommendation_count:
        events.append(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                attempt=attempt,
                event_seq=2,
                event_type="detail_recommended",
                status="completed",
                safe_counts={"detail_recommendations": detail_recommendation_count},
                safe_reason_code="matched_card_terms",
            )
        )
    return tuple(events)


def _workflow_events_from_search_result(
    *,
    search_result: SearchResult,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    attempt: int,
    start_seq: int,
    status_override: RuntimeSourceLaneStatus | None = None,
) -> tuple[RuntimeSourceLaneEvent, ...]:
    raw_steps = search_result.request_payload.get("workflowSteps")
    if not isinstance(raw_steps, list):
        return ()
    events: list[RuntimeSourceLaneEvent] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            continue
        event_type = _workflow_step_event_type(raw_step.get("event_type"))
        if event_type is None:
            continue
        events.append(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                attempt=attempt,
                event_seq=start_seq + len(events),
                event_type=event_type,
                status=status_override or _workflow_step_status(raw_step.get("status")),
                step_name=str(raw_step.get("step_name") or ""),
                safe_counts=_int_mapping(raw_step.get("safe_counts")),
                safe_metadata=_safe_metadata_mapping(raw_step.get("safe_metadata")),
                safe_reason_code=str(raw_step.get("safe_reason_code") or "") or None,
                artifact_refs=_string_tuple(raw_step.get("artifact_refs")),
            )
        )
    return tuple(events)


def _workflow_step_status(value: object) -> RuntimeSourceLaneStatus | None:
    if value == "running":
        return "running"
    if value == "completed":
        return "completed"
    if value == "blocked":
        return "blocked"
    if value == "partial":
        return "partial"
    if value == "failed":
        return "failed"
    if value == "cancelled":
        return "cancelled"
    return None


def _workflow_step_event_type(value: object) -> RuntimeSourceLaneEventType | None:
    if value == "source_workflow_step_started":
        return "source_workflow_step_started"
    if value == "source_workflow_step_completed":
        return "source_workflow_step_completed"
    if value == "source_workflow_step_failed":
        return "source_workflow_step_failed"
    return None


def _int_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, int) and not isinstance(item, bool)}


def _safe_metadata_mapping(value: object) -> dict[str, str | int | bool]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str | int | bool] = {}
    for key, item in value.items():
        if isinstance(item, str | int | bool):
            result[str(key)] = item
    return result


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _source_evidence_for_candidate(
    *,
    source_plan: RuntimeSourceLanePlan,
    candidate: ResumeCandidate,
    collected_at: str,
    evidence_level: RuntimeEvidenceLevel = "card",
    source_lane_run_id: str | None = None,
    provider_rank: int | None = None,
    query_fingerprint: str | None = None,
) -> RuntimeSourceEvidence:
    provider_candidate_key_hash = _candidate_ref(candidate, "provider_candidate_key_hash")
    if provider_candidate_key_hash is None:
        provider_candidate_key = candidate.source_resume_id or candidate.dedup_key or candidate.resume_id
        provider_candidate_key_hash = hashlib.sha256(
            f"{source_plan.runtime_run_id}:liepin:{provider_candidate_key}".encode("utf-8")
        ).hexdigest()
    return RuntimeSourceEvidence(
        evidence_id=f"{source_plan.source_plan_id}:liepin:{provider_candidate_key_hash}",
        source="liepin",
        provider="liepin",
        source_plan_id=source_plan.source_plan_id,
        source_lane_run_id=source_lane_run_id,
        evidence_level=evidence_level,
        candidate_resume_id=candidate.resume_id,
        provider_candidate_key_hash=provider_candidate_key_hash,
        provider_rank=provider_rank,
        query_fingerprint=query_fingerprint,
        provider_snapshot_ref=_candidate_ref(candidate, "provider_snapshot_ref", "raw_payload_artifact_ref"),
        safe_summary_ref=_candidate_ref(candidate, "safe_summary_ref"),
        collected_at=collected_at,
        score_hint=None,
        reason_code="source_detail_candidate" if evidence_level == "detail" else "source_card_candidate",
        safe_reason_codes=("source_detail_candidate" if evidence_level == "detail" else "source_card_candidate",),
    )


def _detail_recommendations_for_candidates(
    *,
    source_plan_id: str,
    candidates: tuple[ResumeCandidate, ...],
    evidence_updates: tuple[RuntimeSourceEvidence, ...],
    query_terms: Collection[str],
    job_title: str,
    max_recommendations: int,
    budget_policy_version: str,
) -> tuple[RuntimeDetailRecommendation, ...]:
    evidence_by_resume_id = {item.candidate_resume_id: item for item in evidence_updates}
    candidate_by_resume_id = {candidate.resume_id: candidate for candidate in candidates}
    decisions = build_liepin_card_decisions(
        cards=[
            _card_summary_for_candidate(
                candidate=candidate,
                provider_rank=evidence_by_resume_id[candidate.resume_id].provider_rank or index,
            )
            for index, candidate in enumerate(candidates, start=1)
            if candidate.resume_id in evidence_by_resume_id
        ],
        query_terms=tuple(query_terms),
        job_title=job_title,
        max_detail_recommendations=max_recommendations,
    )
    recommendations: list[RuntimeDetailRecommendation] = []
    for decision in decisions:
        if decision.action != LiepinCardDecisionAction.RECOMMEND_DETAIL:
            continue
        candidate = candidate_by_resume_id[decision.candidate_resume_id]
        evidence = evidence_by_resume_id[decision.candidate_resume_id]
        recommendations.append(
            RuntimeDetailRecommendation(
                recommendation_id=f"{source_plan_id}:detail:{candidate.resume_id}",
                source="liepin",
                source_evidence_id=evidence.evidence_id,
                candidate_resume_id=candidate.resume_id,
                provider_candidate_key_hash=evidence.provider_candidate_key_hash,
                source_lane_run_id=evidence.source_lane_run_id,
                value_score=decision.value_score,
                provider_rank=decision.provider_rank,
                card_policy_rank=decision.card_policy_rank,
                hard_filter_status=decision.hard_filter_status,
                budget_reason_code=decision.budget_reason_code,
                reason_code=_primary_card_policy_reason(decision.reason_codes),
                safe_reason="Agent recommends opening detail after matched card terms.",
                safe_reason_codes=decision.reason_codes,
                provider_snapshot_ref=evidence.provider_snapshot_ref,
                safe_summary_ref=evidence.safe_summary_ref,
                budget_policy_version=budget_policy_version,
            )
        )
    return tuple(recommendations)


def _card_summary_for_candidate(*, candidate: ResumeCandidate, provider_rank: int) -> LiepinCardSummary:
    raw = candidate.raw if isinstance(candidate.raw, dict) else {}
    safe_summary = raw.get("safe_card_summary")
    summary = safe_summary if isinstance(safe_summary, dict) else {}
    return LiepinCardSummary(
        candidate_resume_id=candidate.resume_id,
        provider_rank=provider_rank,
        display_title=_summary_string(summary, "display_title"),
        current_or_recent_company=_summary_string(summary, "current_or_recent_company"),
        current_or_recent_title=_summary_string(summary, "current_or_recent_title"),
        work_years=_summary_int(summary, "work_years"),
        age=_summary_int(summary, "age"),
        gender=_summary_string(summary, "gender"),
        city=_summary_string(summary, "city"),
        expected_city=_summary_string(summary, "expected_city"),
        education_level=_summary_string(summary, "education_level"),
        school_names=_summary_string_tuple(summary, "school_names"),
        major_names=_summary_string_tuple(summary, "major_names"),
        skill_tags=_summary_string_tuple(summary, "skill_tags"),
        job_intention=_summary_string(summary, "job_intention"),
        active_status=_summary_string(summary, "active_status"),
        badges=_summary_string_tuple(summary, "badges"),
        experience_preview=_summary_mapping_tuple(
            summary,
            "experience_preview",
            string_keys=("company", "title", "date_range", "duration"),
            bool_keys=("is_current",),
        ),
        education_preview=_summary_mapping_tuple(
            summary,
            "education_preview",
            string_keys=("school", "major", "degree", "recruitment_type", "date_range"),
        ),
        masked_name=bool(summary.get("masked_name", False)),
    )


def _summary_string(summary: dict[object, object], key: str) -> str | None:
    value = summary.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _summary_int(summary: dict[object, object], key: str) -> int | None:
    value = summary.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _summary_string_tuple(summary: dict[object, object], key: str) -> tuple[str, ...]:
    value = summary.get(key)
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _summary_mapping_tuple(
    summary: dict[object, object],
    key: str,
    *,
    string_keys: tuple[str, ...],
    bool_keys: tuple[str, ...] = (),
) -> tuple[dict[str, object], ...]:
    value = summary.get(key)
    if not isinstance(value, list | tuple):
        return ()
    items: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        item = cast(Mapping[str, object], item)
        filtered: dict[str, object] = {}
        for item_key in string_keys:
            item_value = item.get(item_key)
            if isinstance(item_value, str) and item_value.strip():
                filtered[item_key] = item_value.strip()
        for item_key in bool_keys:
            item_value = item.get(item_key)
            if isinstance(item_value, bool):
                filtered[item_key] = item_value
        if filtered:
            items.append(filtered)
    return tuple(items)


def _primary_card_policy_reason(reason_codes: tuple[str, ...]) -> str:
    for reason in ("matched_card_terms", "high_value_card", "card_rank_budget"):
        if reason in reason_codes:
            return reason
    return reason_codes[-1] if reason_codes else "matched_card_terms"


def _basic_source_query_terms(request: RuntimeSourceLaneRequest) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in (request.job_title, request.notes or "", request.jd):
        for token in value.replace(",", " ").replace("，", " ").replace(";", " ").replace("；", " ").split():
            text = token.strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            terms.append(text)
            if len(terms) >= 8:
                return tuple(terms)
    return tuple(terms or [request.job_title.strip() or "candidate"])


def _requirement_sheet_provider_context(request: RuntimeSourceLaneRequest) -> dict[str, str]:
    return {
        "liepin_requirement_sheet_json": json.dumps(
            request.requirement_sheet.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
    }


def _card_search_request(
    *,
    request: RuntimeSourceLaneRequest,
    context: RuntimeLiepinContext,
    source_lane_run_id: str,
    compiled_search_request: SearchRequest | None,
) -> SearchRequest:
    default_query_terms = list(request.source_query_terms or _basic_source_query_terms(request))
    default_query_fingerprint = request.logical_query_fingerprint or hashlib.sha256(
        " ".join(default_query_terms).encode("utf-8")
    ).hexdigest()
    provider_scan_limit = (
        request.logical_provider_scan_limit
        or request.logical_requested_count
        or request.source_budget_policy.max_cards
    )
    if compiled_search_request is not None and compiled_search_request.fetch_mode == "detail":
        page_size = int(request.logical_requested_count or compiled_search_request.page_size or 10)
    else:
        page_size = compiled_search_request.page_size if compiled_search_request is not None else provider_scan_limit
    provider_context = {
        key: value
        for key, value in {
            **_requirement_sheet_provider_context(request),
            **context.to_provider_context(),
            "liepin_card_page_size": str(request.source_budget_policy.page_size),
            "liepin_max_cards": str(provider_scan_limit),
            "query_instance_id": request.logical_query_instance_id or source_lane_run_id,
            "query_fingerprint": default_query_fingerprint,
        }.items()
        if value is not None
    }
    if compiled_search_request is not None:
        provider_context.update(compiled_search_request.provider_context)
        provider_context.update(_requirement_sheet_provider_context(request))
    max_cards = _positive_context_int(provider_context.get("liepin_max_cards"), default=provider_scan_limit)
    provider_context["liepin_max_pages"] = str(_liepin_max_pages_for(max_cards=max_cards, page_size=page_size))

    if compiled_search_request is None:
        return SearchRequest(
            query_terms=default_query_terms,
            query_role="primary" if request.logical_query_role != "explore" else "expansion",
            keyword_query=request.logical_keyword_query or " ".join(default_query_terms),
            adapter_notes=[request.notes or ""],
            runtime_constraints=[],
            fetch_mode="summary",
            page_size=page_size,
            provider_context=provider_context,
        )
    return SearchRequest(
        query_terms=list(compiled_search_request.query_terms),
        query_role=compiled_search_request.query_role,
        keyword_query=compiled_search_request.keyword_query,
        adapter_notes=list(compiled_search_request.adapter_notes),
        runtime_constraints=list(compiled_search_request.runtime_constraints),
        fetch_mode=compiled_search_request.fetch_mode,
        page_size=page_size,
        provider_filters=dict(compiled_search_request.provider_filters),
        provider_context=provider_context,
        cursor=compiled_search_request.cursor,
    )


def _positive_context_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
    else:
        return default
    return parsed if parsed > 0 else default


def _detail_provider_context(
    *,
    request: RuntimeSourceLaneRequest,
    context: RuntimeLiepinContext,
    source_lane_run_id: str,
    query_terms: list[str],
) -> dict[str, str]:
    lease = request.approved_detail_lease
    if lease is None:
        raise ValueError("Liepin detail source lane requires an approved detail lease.")
    return {
        **_requirement_sheet_provider_context(request),
        **context.to_provider_actor_context(),
        "liepin_connection_id": lease.connection_id,
        "liepin_compliance_gate_ref": lease.compliance_gate_ref,
        "liepin_provider_account_hash": lease.provider_account_hash,
        "query_instance_id": source_lane_run_id,
        "query_fingerprint": hashlib.sha256(" ".join(query_terms).encode("utf-8")).hexdigest(),
        "liepin_detail_open_plan_ref": lease.lease_ref,
        "liepin_detail_candidates_json": lease.detail_candidates_json,
        "liepin_detail_daily_budget": str(lease.daily_budget),
        "liepin_detail_budget_date": lease.budget_date,
        "liepin_detail_provider_day_key": lease.provider_day_key,
        "liepin_detail_timezone": lease.timezone,
        "liepin_detail_open_policy_version": lease.open_policy_version,
        "liepin_detail_already_opened_provider_ids_json": lease.already_opened_provider_ids_json,
        "liepin_detail_already_seen_weak_fingerprints_json": lease.already_seen_weak_fingerprints_json,
        "liepin_detail_score_metadata_json": lease.score_metadata_json,
    }


def _detail_lease_matches_request(
    *,
    request: RuntimeSourceLaneRequest,
    runtime_run_id: str,
    source_plan_id: str,
) -> bool:
    lease = request.approved_detail_lease
    if lease is None:
        return False
    if lease.source != "liepin":
        return False
    if lease.runtime_run_id is not None and lease.runtime_run_id != runtime_run_id:
        return False
    if lease.source_plan_id is not None and lease.source_plan_id != source_plan_id:
        return False
    if lease.source_evidence_id is not None and lease.source_evidence_id != lease.candidate_evidence_id:
        return False
    return True


def _build_provider(
    *,
    settings: AppSettings,
    worker_client: LiepinWorkerClient,
    worker_search_started_callback: Callable[[], None] | None = None,
) -> LiepinProviderAdapter:
    store = None
    if is_live_liepin_worker_mode(settings.liepin_worker_mode):
        store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    return LiepinProviderAdapter(
        settings,
        worker_client=worker_client,
        worker_search_started_callback=worker_search_started_callback,
        store=store,
    )


def _liepin_max_pages(budget: RuntimeSourceBudgetPolicy) -> int:
    return _liepin_max_pages_for(max_cards=budget.max_cards, page_size=budget.page_size)


def _liepin_max_pages_for(*, max_cards: int, page_size: int) -> int:
    normalized_page_size = max(1, page_size)
    return max(1, math.ceil(max_cards / normalized_page_size))


def runtime_safe_reason_code_from_worker_failure_code(
    failure_code: object,
    *,
    cards_collected: bool = False,
) -> str:
    value = str(getattr(failure_code, "value", failure_code or ""))
    if value in LIEPIN_WORKER_SAFE_REASON_CODES:
        return value
    if value in {"blocked_login_required", "login_expired", "connection_safety_expired"}:
        return "blocked_login_required"
    if value in {"blocked_permission_required", "verification_required", "risk_control"}:
        return "blocked_compliance"
    if value in {"blocked_backend_unavailable", "provider_connection_locked"}:
        return "blocked_backend_unavailable"
    if value in {"partial_timeout", "page_timeout"}:
        return "partial_timeout" if cards_collected else "failed_provider_error"
    if value in {"failed_provider_error", "failed_malformed_output", "selector_drift", "extraction_failure"}:
        return "failed_provider_error"
    return "failed_provider_error"


def _assert_detail_backed_liepin_search_result(search_result: SearchResult) -> None:
    if not _is_detail_backed_liepin_search_result(search_result):
        raise ValueError("liepin_detail_backed_search_returned_card_only_candidates")


def _is_detail_backed_liepin_search_result(search_result: SearchResult) -> bool:
    if not search_result.candidates:
        return True
    snapshots = tuple(search_result.provider_snapshots)
    if snapshots and all(
        snapshot.payload_kind == "detail" and snapshot.score_evidence_source == "detail_enriched"
        for snapshot in snapshots
    ):
        return True
    return all(
        isinstance(candidate.raw, dict) and candidate.raw.get("score_evidence_source") == "detail_enriched"
        for candidate in search_result.candidates
    )


def _candidate_ref(candidate: ResumeCandidate, *keys: str) -> str | None:
    if not isinstance(candidate.raw, dict):
        return None
    for key in keys:
        value = candidate.raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
