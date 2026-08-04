from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, cast

from seektalent.config import AppSettings
from seektalent.candidate_observation_merge import (
    merge_resume_candidate_observations,
    merge_runtime_source_evidence_updates,
)
from seektalent.core.retrieval.provider_contract import (
    ProviderFirstPageExpansionError,
    ProviderSearchContinuation,
    ProviderSearchError,
    SearchRequest,
    SearchResult,
)
from seektalent.source_contracts.first_page_expansion import (
    SourceFirstPageExpansionError,
    SourceFirstPageExpansionRequest,
    SourceFirstPageExpansionResult,
)
from seektalent.models import (
    LaneType,
    QueryRole,
    ResumeCandidate,
    RuntimeSourceEvidence,
)
from seektalent.providers.liepin.adapter import LiepinProviderAdapter
from seektalent.normalization import normalize_resume
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.providers.liepin.card_policy import (
    LiepinCardDecisionAction,
    LiepinCardSummary,
    build_liepin_card_decisions,
)
from seektalent.providers.liepin.client import (
    LiepinWorkerClient,
    LiepinWorkerModeError,
    build_liepin_worker_client,
    is_detail_open_claim_capable_liepin_worker,
    is_live_liepin_worker_mode,
)
from seektalent.providers.liepin.filter_compiler import LiepinSourceQueryIntent
from seektalent.providers.liepin.source_compiler import (
    LiepinCompiledQuery,
    compile_liepin_source_query_intents,
    render_liepin_keyword_query,
)
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerPartialSearchError
from seektalent.failure_interpretation import (
    legacy_lane_retryable_metadata,
    public_source_problem_code,
)
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
    RuntimeSourceStepResumeResult,
    SourceQueryExecutionOutcome,
)
from seektalent.liepin_round_work_plan_contracts import (
    LiepinRoundLaneWorkItemV1,
    LiepinRoundWorkPlanV1,
)
from seektalent.liepin_cards_contracts import (
    LiepinCardsArtifactV1,
    LiepinCardsOperationRequestV1,
    canonical_liepin_cards_request_hash,
    stable_liepin_cards_operation_id,
)
from seektalent.canonical_json import canonical_json_bytes
from seektalent.sources.liepin.round_work_plan import (
    build_liepin_round_work_plan,
    source_budget_from_liepin_round_work_plan,
)


@dataclass(frozen=True, slots=True)
class LiepinRoundResumeResult:
    plan: LiepinRoundWorkPlanV1
    lane_result: RuntimeSourceLaneResult


@dataclass(frozen=True, slots=True)
class LiepinPreparedRoundResume:
    plan: LiepinRoundWorkPlanV1
    lane_payloads: tuple[dict[str, object], ...]
    cards_requests: tuple[LiepinCardsOperationRequestV1, ...]

    @property
    def lane_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple((lane.source_lane_run_id, lane.query_instance_id) for lane in self.plan.lanes)


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


async def run_liepin_first_page_expansion(
    *,
    settings: AppSettings,
    request: SourceFirstPageExpansionRequest,
    detail_open_claim_ledger: DetailOpenClaimLedger,
    cards_operation_executor=None,
    worker_client: LiepinWorkerClient | None = None,
) -> SourceFirstPageExpansionResult:
    if settings.liepin_worker_mode == "opencli" and cards_operation_executor is None:
        raise RuntimeError("liepin_source_operation_executor_required")
    client = worker_client or (
        build_liepin_worker_client(
            settings,
            cards_operation_executor=cards_operation_executor,
            lifecycle_supervisor=_lifecycle_supervisor_from_executor(cards_operation_executor),
        )
        if cards_operation_executor is not None
        else build_liepin_worker_client(settings)
    )
    provider = _build_provider(
        settings=settings,
        worker_client=client,
        readiness_preparer=getattr(
            cards_operation_executor,
            "prepare_readiness",
            None,
        ),
    )
    try:
        result = await provider.handle_first_page_continuation_with_detail_open_claim_ledger(
            action=request.action,
            continuation=cast(ProviderSearchContinuation, request.continuation),
            detail_open_claim_ledger=detail_open_claim_ledger,
            logical_round_no=request.round_no,
            query_instance_id=request.query_instance_id,
        )
    except ProviderFirstPageExpansionError as exc:
        raise SourceFirstPageExpansionError(
            str(exc),
            status=exc.status,
            safe_reason_code=exc.safe_reason_code,
            continuation_deleted=exc.continuation_deleted,
        ) from exc
    except ProviderSearchError as exc:
        raise SourceFirstPageExpansionError(str(exc), status="failed", safe_reason_code=exc.reason_code) from exc
    except LiepinWorkerModeError as exc:
        raise SourceFirstPageExpansionError(
            str(exc), status="blocked", safe_reason_code=str(exc.code or "liepin_first_page_expansion_blocked")
        ) from exc
    candidates = tuple(result.search_result.candidates)
    attributions = tuple(
        RuntimeQueryCandidateAttribution(
            source_kind="liepin",
            query_instance_id=request.query_instance_id,
            resume_id=item.resume_id,
            dedup_key=item.dedup_key,
        )
        for item in candidates
    )
    source_plan_id = f"{request.runtime_run_id}:source:{request.round_no}:liepin"
    source_lane_run_id = f"{request.runtime_run_id}:expansion:{request.continuation_id}"
    source_plan = RuntimeSourceLanePlan(
        source_plan_id=source_plan_id,
        runtime_run_id=request.runtime_run_id,
        source="liepin",
        label="Liepin",
        lane_mode="detail",
        backend_mode="runtime_source_lane",
        produces_private_first_page_continuations=True,
    )
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
            evidence_level="detail",
            source_lane_run_id=source_lane_run_id,
            provider_rank=index,
        )
        for index, candidate in enumerate(candidates, start=1)
    )
    lane = RuntimeSourceLaneResult(
        runtime_run_id=request.runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="card",
        attempt=request.round_no,
        status=result.status,
        candidate_store_updates={item.resume_id: item for item in candidates},
        source_evidence_updates=evidence_updates,
        raw_candidate_count=result.search_result.raw_candidate_count,
        candidate_query_attributions=attributions,
        stop_reason_code=result.safe_reason_code,
    )
    return SourceFirstPageExpansionResult(
        source_kind="liepin",
        query_instance_id=request.query_instance_id,
        continuation_id=request.continuation_id,
        status=result.status,
        candidates=candidates,
        candidate_query_attributions=attributions,
        lane_result=lane,
        first_page_visible_count=result.first_page_visible_count,
        first_page_eligible_count=result.first_page_eligible_count,
        initial_opened_count=result.initial_opened_count,
        expansion_opened_count=result.expansion_opened_count,
        expansion_skipped_seen_count=result.expansion_skipped_seen_count,
        expansion_terminal_failure_count=result.expansion_terminal_failure_count,
        safe_reason_code=result.safe_reason_code,
        continuation_deleted=result.continuation_deleted,
    )


async def run_liepin_source_lane(
    *,
    settings: AppSettings,
    request: RuntimeSourceLaneRequest,
    worker_client: LiepinWorkerClient | None = None,
    compiled_search_request: SearchRequest | None = None,
    detail_open_claim_ledger: DetailOpenClaimLedger | None = None,
    cards_operation_executor=None,
) -> RuntimeSourceLaneResult:
    if settings.liepin_worker_mode == "opencli" and cards_operation_executor is None:
        raise RuntimeError("liepin_source_operation_executor_required")
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
            cards_operation_executor=cards_operation_executor,
        )
    if request.lane_mode != "card":
        raise ValueError(f"Unsupported Liepin source lane mode: {request.lane_mode}")

    context = normalize_runtime_liepin_context(request.source_context)
    if cards_operation_executor is not None:
        bind_lane = getattr(cards_operation_executor, "bind_lane", None)
        if callable(bind_lane):
            lane_query_terms = tuple(request.source_query_terms or _basic_source_query_terms(request))
            bind_lane(
                source_lane_run_id,
                request.logical_query_instance_id or source_lane_run_id,
                source_plan_id=source_plan_id,
                round_no=request.logical_round_no or request.attempt,
                query_terms=lane_query_terms,
                keyword_query=(request.logical_keyword_query or " ".join(lane_query_terms)),
                query_fingerprint=(
                    request.logical_query_fingerprint or request.logical_query_instance_id or source_lane_run_id
                ),
                query_role=request.logical_query_role or "exploit",
                requested_count=(request.logical_requested_count or request.source_budget_policy.card_target or 1),
                max_pages=request.source_budget_policy.max_pages,
                max_cards=(request.logical_provider_scan_limit or request.source_budget_policy.max_cards),
                claim_aware=detail_open_claim_ledger is not None,
            )
    client = worker_client or (
        build_liepin_worker_client(
            settings,
            cards_operation_executor=cards_operation_executor,
            lifecycle_supervisor=_lifecycle_supervisor_from_executor(cards_operation_executor),
        )
        if cards_operation_executor is not None
        else build_liepin_worker_client(settings)
    )
    query_started = False

    def mark_query_started() -> None:
        nonlocal query_started
        query_started = True

    provider = _build_provider(
        settings=settings,
        worker_client=client,
        worker_search_started_callback=mark_query_started,
        readiness_preparer=getattr(
            cards_operation_executor,
            "prepare_readiness",
            None,
        ),
    )
    search_request = _card_search_request(
        request=request,
        context=context,
        source_lane_run_id=source_lane_run_id,
        compiled_search_request=compiled_search_request,
    )
    query_terms = list(search_request.query_terms)
    query_fingerprint = (
        search_request.provider_context.get("query_fingerprint")
        or hashlib.sha256(" ".join(query_terms).encode("utf-8")).hexdigest()
    )
    try:
        if _uses_private_detail_open_claim_route(
            worker_client=client,
            search_request=search_request,
            detail_open_claim_ledger=detail_open_claim_ledger,
        ):
            if detail_open_claim_ledger is None:
                raise ValueError("liepin_detail_open_claim_route_missing_ledger")
            if (
                request.logical_round_no is None
                or request.logical_round_no < 1
                or not request.logical_query_instance_id
            ):
                raise ValueError("liepin_detail_open_claim_route_missing_logical_provenance")
            search_result = await provider.search_with_detail_open_claim_ledger(
                search_request,
                round_no=request.logical_round_no,
                trace_id=source_lane_run_id,
                detail_open_claim_ledger=detail_open_claim_ledger,
                logical_round_no=request.logical_round_no,
                query_instance_id=request.logical_query_instance_id,
            )
        else:
            search_result = await provider.search(
                search_request,
                round_no=1,
                trace_id=source_lane_run_id,
            )
        if search_request.provider_context.get("liepin_fetch_strategy") == "detail_backed_resume_search":
            _assert_detail_backed_liepin_search_result(search_result)
    except LiepinWorkerPartialSearchError as error:
        stop_reason_code = runtime_reason_code_from_worker_failure_code(
            error.code,
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
        reason_code = runtime_reason_code_from_worker_failure_code(error.code)
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
    detail_open_claim_ledger: DetailOpenClaimLedger | None = None,
    cards_operation_executor=None,
    round_resume_context: Mapping[str, object] | None = None,
) -> RuntimeSourceLaneResult:
    if settings.liepin_worker_mode == "opencli" and cards_operation_executor is None:
        raise RuntimeError("liepin_source_operation_executor_required")
    if not logical_queries:
        raise ValueError("Liepin logical query bundle requires at least one logical query.")
    compiled_bundle = (
        compile_liepin_source_query_intents(source_query_intents) if source_query_intents is not None else None
    )
    compiled_queries = compiled_bundle.queries if compiled_bundle is not None else ()
    context = normalize_runtime_liepin_context(liepin_context)
    bind_round_work_plan = getattr(
        cards_operation_executor,
        "bind_round_work_plan",
        None,
    )
    round_plan: LiepinRoundWorkPlanV1 | None = None
    if callable(bind_round_work_plan):
        if round_resume_context is None:
            raise ValueError("liepin_round_resume_context_required")
        round_work_plan_authority = getattr(
            cards_operation_executor,
            "round_work_plan_authority",
            None,
        )
        if not callable(round_work_plan_authority):
            raise ValueError("liepin_round_work_plan_authority_required")
        round_numbers = {query.round_no for query in logical_queries}
        if len(round_numbers) != 1:
            raise ValueError("liepin_round_work_plan_round_mismatch")
        base_checkpoint_id, accepted_requirement_revision_id = round_work_plan_authority(
            round_no=next(iter(round_numbers))
        )
        round_plan = build_liepin_round_work_plan(
            runtime_run_id=runtime_run_id,
            base_checkpoint_id=base_checkpoint_id,
            accepted_requirement_revision_id=(accepted_requirement_revision_id),
            source_plan_id=source_plan_id,
            job_title=job_title,
            jd=jd,
            notes=notes,
            requirement_sheet=requirement_sheet,
            logical_queries=logical_queries,
            compiled_queries=compiled_queries,
            source_budget_policy=source_budget_policy,
            context=context,
            detail_claim_aware=detail_open_claim_ledger is not None,
            resume_context=round_resume_context,
        )
        bind_round_work_plan(round_plan)
    bundle_worker_client = worker_client or (
        build_liepin_worker_client(
            settings,
            cards_operation_executor=cards_operation_executor,
            lifecycle_supervisor=_lifecycle_supervisor_from_executor(cards_operation_executor),
        )
        if cards_operation_executor is not None
        else build_liepin_worker_client(settings)
    )

    started_lane_keys: set[tuple[str, str]] = set()
    skipped_lane_keys: set[tuple[str, str]] = set()

    async def run_logical_query(index: int, logical_query: LogicalQueryDispatch) -> RuntimeSourceLaneResult:
        logical_compiled_queries = tuple(
            query for query in compiled_queries if query.intent.query_instance_id == logical_query.query_instance_id
        )
        if not logical_compiled_queries:
            logical_compiled_queries = (None,)
        logical_result: RuntimeSourceLaneResult | None = None
        target_results: list[RuntimeSourceLaneResult] = []
        logical_target_total = (
            logical_compiled_queries[0].intent.requested_count
            if logical_compiled_queries[0] is not None
            else logical_query.requested_count
        )
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
            captured_detail_count = sum(len(item.candidate_store_updates) for item in target_results)
            remaining_target = max(0, logical_target_total - captured_detail_count)
            if remaining_target == 0:
                break
            logical_requested_count = remaining_target
            lane_run_id = f"{source_plan_id}:round:{logical_query.round_no}:lane:{index}"
            if compiled_query is not None:
                lane_run_id = f"{lane_run_id}:target:{target_index}"
            started_lane_keys.add((lane_run_id, logical_query.query_instance_id))
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
                    logical_round_no=logical_query.round_no,
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
                worker_client=bundle_worker_client,
                compiled_search_request=compiled_request,
                detail_open_claim_ledger=detail_open_claim_ledger,
                cards_operation_executor=cards_operation_executor,
            )
            result = _with_liepin_executed_query_package(
                result,
                logical_query=logical_query,
                compiled_query=compiled_query,
            )
            _complete_durable_liepin_lane(
                cards_operation_executor=cards_operation_executor,
                result=result,
                query_instance_id=logical_query.query_instance_id,
            )
            target_results.append(result)
            logical_result = (
                result if logical_result is None else merge_liepin_card_lane_results(logical_result, result)
            )
            if _reconciliation_required_reason((result,)) is not None:
                break
            captured_detail_count = sum(len(item.candidate_store_updates) for item in target_results)
            if captured_detail_count >= logical_target_total:
                break
        if logical_result is None:
            raise ValueError("Liepin logical query bundle requires at least one logical query.")
        if round_plan is not None and _reconciliation_required_reason((logical_result,)) is None:
            skipped_lane_keys.update(
                (
                    lane.source_lane_run_id,
                    lane.query_instance_id,
                )
                for lane in round_plan.lanes
                if lane.logical_query_ordinal == index
                and (
                    lane.source_lane_run_id,
                    lane.query_instance_id,
                )
                not in started_lane_keys
            )
        return _with_liepin_query_execution_outcome(
            logical_result,
            logical_query=logical_query,
            target_results=target_results,
        )

    logical_results: dict[int, RuntimeSourceLaneResult] = {}
    if settings.liepin_worker_mode == "opencli" or context.backend_mode == "opencli":
        for index, logical_query in enumerate(logical_queries, start=1):
            logical_results[index] = await run_logical_query(index, logical_query)
            if _reconciliation_required_reason((logical_results[index],)) is not None:
                break
    else:
        tasks: dict[int, asyncio.Task[RuntimeSourceLaneResult]] = {}
        async with asyncio.TaskGroup() as task_group:
            for index, logical_query in enumerate(logical_queries, start=1):
                tasks[index] = task_group.create_task(run_logical_query(index, logical_query))
        logical_results = {index: tasks[index].result() for index in tasks}

    skip_lane = getattr(cards_operation_executor, "skip_lane", None)
    if callable(skip_lane) and round_plan is not None:
        for lane in round_plan.lanes:
            lane_key = (
                lane.source_lane_run_id,
                lane.query_instance_id,
            )
            if lane_key in skipped_lane_keys:
                skip_lane(
                    round_no=round_plan.round_no,
                    source_lane_run_id=lane.source_lane_run_id,
                    query_instance_id=lane.query_instance_id,
                )

    merged_result: RuntimeSourceLaneResult | None = None
    for index in sorted(logical_results):
        logical_result = logical_results[index]
        merged_result = (
            logical_result if merged_result is None else merge_liepin_card_lane_results(merged_result, logical_result)
        )
    assert merged_result is not None
    return merged_result


def prepare_liepin_round_work_plan_resume(
    *,
    resume_lanes: object,
    cards_operation_executor,
) -> LiepinPreparedRoundResume:
    """Load and validate durable resume inputs without executing a source step."""
    if not isinstance(resume_lanes, list) or not resume_lanes:
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")
    lane_payloads: dict[tuple[str, str], dict[str, object]] = {}
    plan_binding: tuple[str, str, str] | None = None
    for raw in resume_lanes:
        if not isinstance(raw, Mapping):
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        raw_mapping = cast(Mapping[str, object], raw)
        source_lane_run_id = raw_mapping.get("sourceLaneRunId")
        query_instance_id = raw_mapping.get("queryInstanceId")
        base_checkpoint_id = raw_mapping.get("baseCheckpointId")
        artifact_ref = raw_mapping.get("workPlanArtifactRef")
        artifact_hash = raw_mapping.get("workPlanArtifactHash")
        if (
            not isinstance(source_lane_run_id, str)
            or not isinstance(query_instance_id, str)
            or not isinstance(base_checkpoint_id, str)
            or not isinstance(artifact_ref, str)
            or not isinstance(artifact_hash, str)
        ):
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        key = (source_lane_run_id, query_instance_id)
        binding = (base_checkpoint_id, artifact_ref, artifact_hash)
        if key in lane_payloads or (plan_binding is not None and plan_binding != binding):
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        lane_payloads[key] = dict(raw_mapping)
        plan_binding = binding
    assert plan_binding is not None
    prepare_plan = getattr(
        cards_operation_executor,
        "prepare_recovered_round_work_plan",
        None,
    )
    if not callable(prepare_plan):
        raise ValueError("runtime_workflow_round_plan_preparer_missing")
    plan = prepare_plan(
        artifact_ref=plan_binding[1],
        artifact_hash=plan_binding[2],
    )
    plan_keys = tuple((lane.source_lane_run_id, lane.query_instance_id) for lane in plan.lanes)
    if plan.base_checkpoint_id != plan_binding[0] or set(plan_keys) != set(lane_payloads):
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")

    from seektalent.models import RequirementSheet

    requirement_sheet = RequirementSheet.model_validate(plan.requirement_sheet)
    budget = source_budget_from_liepin_round_work_plan(plan)
    requests: list[LiepinCardsOperationRequestV1] = []
    ordered_payloads: list[dict[str, object]] = []
    for lane in plan.lanes:
        raw = lane_payloads[(lane.source_lane_run_id, lane.query_instance_id)]
        request = _cards_operation_request_from_round_plan_lane(
            plan=plan,
            lane=lane,
            requirement_sheet=requirement_sheet,
            budget=budget,
        )
        _validate_round_resume_lane_payload(
            raw=raw,
            plan=plan,
            lane=lane,
            request=request,
            artifact_ref=plan_binding[1],
            artifact_hash=plan_binding[2],
        )
        ordered_payloads.append(raw)
        requests.append(request)
    return LiepinPreparedRoundResume(
        plan=plan,
        lane_payloads=tuple(ordered_payloads),
        cards_requests=tuple(requests),
    )


async def resume_liepin_round_work_plan(
    *,
    settings: AppSettings,
    prepared_resume: object,
    detail_open_claim_ledger: DetailOpenClaimLedger,
    cards_operation_executor,
) -> LiepinRoundResumeResult:
    """Execute only a plan already validated by the Runtime authority."""
    if not isinstance(prepared_resume, LiepinPreparedRoundResume):
        raise ValueError("runtime_workflow_round_resume_plan_invalid")
    plan = prepared_resume.plan
    activate_plan = getattr(
        cards_operation_executor,
        "activate_recovered_round_work_plan",
        None,
    )
    if not callable(activate_plan):
        raise ValueError("runtime_workflow_round_plan_activator_missing")
    activate_plan(plan)

    from seektalent.models import RequirementSheet

    requirement_sheet = RequirementSheet.model_validate(plan.requirement_sheet)
    budget = source_budget_from_liepin_round_work_plan(plan)
    lane_payloads = {
        (lane.source_lane_run_id, lane.query_instance_id): raw
        for lane, raw in zip(
            plan.lanes,
            prepared_resume.lane_payloads,
            strict=True,
        )
    }
    requests = {
        (lane.source_lane_run_id, lane.query_instance_id): request
        for lane, request in zip(
            plan.lanes,
            prepared_resume.cards_requests,
            strict=True,
        )
    }
    logical_results: dict[int, RuntimeSourceLaneResult] = {}
    target_results: dict[int, list[RuntimeSourceLaneResult]] = {}
    logical_dispatches: dict[int, LogicalQueryDispatch] = {}

    for lane in plan.lanes:
        raw = lane_payloads[(lane.source_lane_run_id, lane.query_instance_id)]
        if raw.get("roundNo") != plan.round_no:
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        status = raw.get("barrierStatus")
        raw_transitions = raw.get("transitions")
        if not isinstance(raw_transitions, list):
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        prior_target_results = target_results.get(
            lane.logical_query_ordinal,
            [],
        )
        captured_detail_count = sum(len(item.candidate_store_updates) for item in prior_target_results)
        remaining_target = max(
            0,
            lane.logical_target_total - captured_detail_count,
        )
        if status == "skipped":
            if raw_transitions:
                raise ValueError("runtime_workflow_round_resume_lanes_invalid")
            continue
        if status == "pending":
            if raw_transitions:
                raise ValueError("runtime_workflow_round_resume_lanes_invalid")
            if remaining_target == 0:
                cards_operation_executor.skip_lane(
                    round_no=plan.round_no,
                    source_lane_run_id=lane.source_lane_run_id,
                    query_instance_id=lane.query_instance_id,
                )
                continue
            result = await _run_pending_round_plan_lane(
                settings=settings,
                plan=plan,
                lane=lane,
                requirement_sheet=requirement_sheet,
                budget=budget,
                detail_open_claim_ledger=detail_open_claim_ledger,
                cards_operation_executor=cards_operation_executor,
                logical_requested_count=remaining_target,
            )
        elif status in {"active", "completed"}:
            if not raw_transitions or not all(isinstance(item, Mapping) for item in raw_transitions):
                raise ValueError("runtime_workflow_round_resume_lanes_invalid")
            active = cast(Mapping[str, object], raw_transitions[-1])
            step_kind = active.get("stepKind")
            if status == "active" and step_kind == "source_dispatch":
                if remaining_target == 0:
                    raise ValueError("runtime_workflow_round_resume_lanes_invalid")
                result = await _run_pending_round_plan_lane(
                    settings=settings,
                    plan=plan,
                    lane=lane,
                    requirement_sheet=requirement_sheet,
                    budget=budget,
                    detail_open_claim_ledger=detail_open_claim_ledger,
                    cards_operation_executor=cards_operation_executor,
                    logical_requested_count=remaining_target,
                )
            elif status == "active" and step_kind in {
                "detail_queued",
                "detail_dispatch",
            }:
                resumed = cards_operation_executor.resume_detail_workflow_transition(
                    dict(active),
                    detail_open_claim_ledger=(detail_open_claim_ledger),
                )
                result = resumed.lane_result
            elif status == "completed" and step_kind == "lane_completed":
                continuation = active.get("continuation")
                if not isinstance(continuation, Mapping):
                    raise ValueError("runtime_workflow_round_resume_lanes_invalid")
                continuation_mapping = cast(Mapping[str, object], continuation)
                if continuation_mapping.get("laneResultKind") == "liepin_detail_work_plan":
                    resumed = cards_operation_executor.resume_completed_detail_workflow_transition(dict(active))
                    result = resumed.lane_result
                elif continuation_mapping.get("laneResultKind") == "cards_only":
                    artifact = cards_operation_executor.resume_completed_cards_workflow_transition(
                        dict(active),
                        expected_request=requests[
                            (
                                lane.source_lane_run_id,
                                lane.query_instance_id,
                            )
                        ],
                    )
                    result = build_resumed_liepin_cards_lane_result(
                        plan=plan,
                        lane=lane,
                        budget=budget,
                        artifact=artifact,
                    )
                else:
                    raise ValueError("runtime_workflow_round_resume_lanes_invalid")
            else:
                raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        else:
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")

        package = RuntimeQueryPackage(
            source_kind="liepin",
            query_role=lane.query_role,
            lane_type=lane.lane_type,
            query_instance_id=lane.query_instance_id,
            query_fingerprint=lane.query_fingerprint,
            term_group_key=lane.term_group_key,
            query_terms=tuple(lane.logical_query_terms),
            keyword_query=lane.keyword_query,
        )
        result = replace(
            result,
            executed_query_packages=(package,),
            query_execution_outcomes=(),
            candidate_query_attributions=(),
        )
        _complete_durable_liepin_lane(
            cards_operation_executor=cards_operation_executor,
            result=result,
            query_instance_id=lane.query_instance_id,
        )
        targets = target_results.setdefault(
            lane.logical_query_ordinal,
            [],
        )
        targets.append(result)
        previous = logical_results.get(lane.logical_query_ordinal)
        logical_results[lane.logical_query_ordinal] = (
            result if previous is None else merge_liepin_card_lane_results(previous, result)
        )
        logical_dispatches.setdefault(
            lane.logical_query_ordinal,
            _logical_dispatch_from_round_plan_lane(plan, lane),
        )
        if _reconciliation_required_reason((result,)) is not None:
            break

    merged: RuntimeSourceLaneResult | None = None
    for logical_ordinal in sorted(logical_results):
        logical_result = _with_liepin_query_execution_outcome(
            logical_results[logical_ordinal],
            logical_query=logical_dispatches[logical_ordinal],
            target_results=target_results[logical_ordinal],
        )
        merged = logical_result if merged is None else merge_liepin_card_lane_results(merged, logical_result)
    if merged is None:
        raise ValueError("runtime_workflow_round_resume_result_missing")
    return LiepinRoundResumeResult(plan=plan, lane_result=merged)


def _validate_round_resume_lane_payload(
    *,
    raw: Mapping[str, object],
    plan: LiepinRoundWorkPlanV1,
    lane: LiepinRoundLaneWorkItemV1,
    request: LiepinCardsOperationRequestV1,
    artifact_ref: str,
    artifact_hash: str,
) -> None:
    if (
        raw.get("roundNo") != plan.round_no
        or raw.get("baseCheckpointId") != plan.base_checkpoint_id
        or raw.get("sourceLaneRunId") != lane.source_lane_run_id
        or raw.get("queryInstanceId") != lane.query_instance_id
        or raw.get("workPlanArtifactRef") != artifact_ref
        or raw.get("workPlanArtifactHash") != artifact_hash
    ):
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")
    status = raw.get("barrierStatus")
    transitions = raw.get("transitions")
    if not isinstance(transitions, list):
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")
    if status in {"pending", "skipped"}:
        if transitions:
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        return
    if status not in {"active", "completed"} or not transitions:
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")
    if not all(isinstance(item, Mapping) for item in transitions):
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")
    transition_items = [cast(Mapping[str, object], item) for item in transitions]
    previous_transition_id: str | None = None
    for index, item in enumerate(transition_items):
        transition_id = item.get("transitionId")
        parent_transition_id = item.get("parentTransitionId")
        if (
            not isinstance(transition_id, str)
            or item.get("sourceLaneRunId") != lane.source_lane_run_id
            or item.get("queryInstanceId") != lane.query_instance_id
            or item.get("baseCheckpointId") != plan.base_checkpoint_id
            or item.get("roundNo") != plan.round_no
            or parent_transition_id != previous_transition_id
        ):
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        if index == 0 and item.get("stepKind") != "source_dispatch":
            raise ValueError("runtime_workflow_round_resume_lanes_invalid")
        previous_transition_id = transition_id
    root = transition_items[0]
    root_continuation = root.get("continuation")
    root_artifact_refs = root.get("artifactRefs")
    expected_request_hash = canonical_liepin_cards_request_hash(request)
    if not isinstance(root_continuation, Mapping) or not isinstance(root_artifact_refs, list):
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")
    root_continuation_mapping = cast(Mapping[str, object], root_continuation)
    if (
        root_continuation_mapping.get("schemaVersion") != "runtime-source-dispatch-continuation/v1"
        or root_continuation_mapping.get("operationId") != stable_liepin_cards_operation_id(request)
        or root_continuation_mapping.get("requestHash") != expected_request_hash
        or root_continuation_mapping.get("queryFingerprint") != expected_request_hash
        or root_continuation_mapping.get("roundWorkPlanArtifactRef") != artifact_ref
        or root_continuation_mapping.get("roundWorkPlanArtifactHash") != artifact_hash
        or artifact_ref not in root_artifact_refs
    ):
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")
    final_step = transition_items[-1].get("stepKind")
    if (status == "completed" and final_step != "lane_completed") or (
        status == "active" and final_step == "lane_completed"
    ):
        raise ValueError("runtime_workflow_round_resume_lanes_invalid")


def _round_plan_lane_runtime_request(
    *,
    plan: LiepinRoundWorkPlanV1,
    lane: LiepinRoundLaneWorkItemV1,
    requirement_sheet: "RequirementSheet",
    budget: RuntimeSourceBudgetPolicy,
    logical_requested_count: int | None = None,
) -> tuple[RuntimeSourceLaneRequest, SearchRequest | None]:
    compiled = lane.compiled_search_request
    compiled_request = (
        SearchRequest(
            query_terms=list(compiled.query_terms),
            query_role=compiled.query_role,
            keyword_query=compiled.keyword_query,
            adapter_notes=list(compiled.adapter_notes),
            runtime_constraints=[],
            fetch_mode=compiled.fetch_mode,
            page_size=compiled.page_size,
            provider_context=dict(compiled.provider_context),
            cursor=compiled.cursor,
        )
        if compiled is not None
        else None
    )
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title=plan.job_title,
        jd=plan.jd,
        notes=plan.notes,
        requirement_sheet=requirement_sheet,
        runtime_run_id=plan.runtime_run_id,
        source_plan_id=plan.source_plan_id,
        source_lane_run_id=lane.source_lane_run_id,
        source_query_terms=tuple(lane.query_terms),
        logical_round_no=plan.round_no,
        logical_query_instance_id=lane.query_instance_id,
        logical_query_fingerprint=lane.query_fingerprint,
        logical_query_role=lane.query_role,
        logical_keyword_query=lane.keyword_query,
        logical_requested_count=(
            logical_requested_count if logical_requested_count is not None else lane.logical_target_total
        ),
        logical_provider_scan_limit=lane.provider_scan_limit,
        logical_unsupported_filter_reason_codes=(lane.unsupported_filter_reason_codes),
        source_budget_policy=budget,
        source_context=plan.source_context,
    )
    return request, compiled_request


def _cards_operation_request_from_round_plan_lane(
    *,
    plan: LiepinRoundWorkPlanV1,
    lane: LiepinRoundLaneWorkItemV1,
    requirement_sheet: "RequirementSheet",
    budget: RuntimeSourceBudgetPolicy,
) -> LiepinCardsOperationRequestV1:
    request, compiled_request = _round_plan_lane_runtime_request(
        plan=plan,
        lane=lane,
        requirement_sheet=requirement_sheet,
        budget=budget,
    )
    context = normalize_runtime_liepin_context(plan.source_context)
    search_request = _card_search_request(
        request=request,
        context=context,
        source_lane_run_id=lane.source_lane_run_id,
        compiled_search_request=compiled_request,
    )
    raw_native_filters = search_request.provider_context.get("liepin_native_filters_json")
    native_filters: dict[str, object] | None = None
    if isinstance(raw_native_filters, str) and raw_native_filters.strip():
        try:
            decoded_native_filters = json.loads(raw_native_filters)
        except json.JSONDecodeError:
            raise ValueError("runtime_workflow_round_native_filters_invalid") from None
        if not isinstance(decoded_native_filters, dict):
            raise ValueError("runtime_workflow_round_native_filters_invalid")
        native_filters = {str(key): value for key, value in decoded_native_filters.items()}
    max_cards = _positive_context_int(
        search_request.provider_context.get("liepin_max_cards"),
        default=search_request.page_size,
    )
    max_pages = _positive_context_int(
        search_request.provider_context.get("liepin_max_pages"),
        default=1,
    )
    return LiepinCardsOperationRequestV1.model_validate(
        {
            "contract_version": ("seektalent.source.liepin-cards.request/v1"),
            "runtime_run_id": plan.runtime_run_id,
            "source_lane_run_id": lane.source_lane_run_id,
            "query_instance_id": lane.query_instance_id,
            "keyword_query": render_liepin_keyword_query(
                search_request.query_terms,
                logical_keyword_query=search_request.keyword_query,
            ),
            "max_pages": max_pages,
            "max_cards": max_cards,
            "native_filters": native_filters,
        },
        strict=True,
    )


async def _run_pending_round_plan_lane(
    *,
    settings: AppSettings,
    plan: LiepinRoundWorkPlanV1,
    lane: LiepinRoundLaneWorkItemV1,
    requirement_sheet: "RequirementSheet",
    budget: RuntimeSourceBudgetPolicy,
    detail_open_claim_ledger: DetailOpenClaimLedger,
    cards_operation_executor,
    logical_requested_count: int,
) -> RuntimeSourceLaneResult:
    request, compiled_request = _round_plan_lane_runtime_request(
        plan=plan,
        lane=lane,
        requirement_sheet=requirement_sheet,
        budget=budget,
        logical_requested_count=logical_requested_count,
    )
    return await run_liepin_source_lane(
        settings=settings,
        request=request,
        compiled_search_request=compiled_request,
        detail_open_claim_ledger=detail_open_claim_ledger,
        cards_operation_executor=cards_operation_executor,
    )


def _logical_dispatch_from_round_plan_lane(
    plan: LiepinRoundWorkPlanV1,
    lane: LiepinRoundLaneWorkItemV1,
) -> LogicalQueryDispatch:
    if lane.lane_type not in {"exploit", "prf_probe", "generic_explore"}:
        raise ValueError("runtime_workflow_round_lane_type_invalid")
    return LogicalQueryDispatch(
        round_no=plan.round_no,
        query_role=lane.query_role,
        lane_type=cast(LaneType, lane.lane_type),
        query_instance_id=lane.query_instance_id,
        query_fingerprint=lane.query_fingerprint,
        term_group_key=lane.term_group_key,
        primary_anchor_family_id=lane.primary_anchor_family_id,
        non_anchor_term_family_ids=(lane.non_anchor_term_family_ids),
        query_terms=tuple(lane.logical_query_terms),
        keyword_query=lane.keyword_query,
        requested_count=lane.logical_requested_count,
        source_plan_version=lane.source_plan_version,
    )


def _complete_durable_liepin_lane(
    *,
    cards_operation_executor: object | None,
    result: RuntimeSourceLaneResult,
    query_instance_id: str,
) -> None:
    if result.status not in {"completed", "partial"} or _reconciliation_required_reason((result,)) is not None:
        return
    complete_lane = getattr(
        cards_operation_executor,
        "complete_lane",
        None,
    )
    if callable(complete_lane):
        complete_lane(
            source_lane_run_id=result.source_lane_run_id,
            query_instance_id=query_instance_id,
        )


def _uses_private_detail_open_claim_route(
    *,
    worker_client: LiepinWorkerClient,
    search_request: SearchRequest,
    detail_open_claim_ledger: DetailOpenClaimLedger | None,
) -> bool:
    return (
        detail_open_claim_ledger is not None
        and is_detail_open_claim_capable_liepin_worker(worker_client)
        and search_request.provider_context.get("liepin_fetch_strategy") == "detail_backed_resume_search"
    )


def merge_liepin_card_lane_results(
    first: RuntimeSourceLaneResult,
    second: RuntimeSourceLaneResult,
) -> RuntimeSourceLaneResult:
    evidence_updates = merge_runtime_source_evidence_updates(
        first.source_evidence_updates,
        second.source_evidence_updates,
    )
    first_evidence_by_resume_id = _evidence_by_resume_id(first.source_evidence_updates)
    second_evidence_by_resume_id = _evidence_by_resume_id(second.source_evidence_updates)
    candidate_updates = dict(first.candidate_store_updates)
    for resume_id, candidate in second.candidate_store_updates.items():
        existing = candidate_updates.get(resume_id)
        if existing is None:
            candidate_updates[resume_id] = candidate
            continue
        merged_candidate, _ = merge_resume_candidate_observations(
            existing,
            candidate,
            left_evidence=first_evidence_by_resume_id.get(resume_id, ()),
            right_evidence=second_evidence_by_resume_id.get(resume_id, ()),
        )
        candidate_updates[resume_id] = merged_candidate
    normalized_updates = {resume_id: normalize_resume(candidate) for resume_id, candidate in candidate_updates.items()}
    reconciliation_reason = _reconciliation_required_reason((first, second))
    if reconciliation_reason is not None:
        status: RuntimeSourceLaneStatus = "blocked"
        stop_reason_code = reconciliation_reason
        blocked_reason_code = reconciliation_reason
    elif candidate_updates:
        status = "completed"
        stop_reason_code = None
        blocked_reason_code = None
    else:
        status = second.status
        stop_reason_code = second.stop_reason_code or first.stop_reason_code
        blocked_reason_code = second.blocked_reason_code or first.blocked_reason_code
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
        source_evidence_updates=evidence_updates,
        provider_snapshots=first.provider_snapshots + second.provider_snapshots,
        private_first_page_continuations=(
            first.private_first_page_continuations + second.private_first_page_continuations
        ),
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
        retryable=(False if reconciliation_reason is not None else first.retryable or second.retryable),
        safe_error_summary=first.safe_error_summary or second.safe_error_summary,
        error_ref=first.error_ref or second.error_ref,
    )


def _evidence_by_resume_id(
    evidence: Collection[RuntimeSourceEvidence],
) -> dict[str, tuple[RuntimeSourceEvidence, ...]]:
    grouped: dict[str, list[RuntimeSourceEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.candidate_resume_id, []).append(item)
    return {resume_id: tuple(items) for resume_id, items in grouped.items()}


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
    pre_click_duplicate_count = sum(
        int(event.safe_counts.get("detail_open_skipped_seen_count", 0))
        for item in target_results
        for event in item.events
        if event.step_name == "finalize"
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
        duplicate_candidate_count=(pre_click_duplicate_count + cross_target_duplicate_candidate_count),
        pre_click_skipped_seen_count=pre_click_duplicate_count,
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
    reconciliation_reason = _reconciliation_required_reason(target_results)
    if reconciliation_reason is not None:
        return reconciliation_reason
    reasons = {
        reason
        for result in target_results
        if (reason := result.stop_reason_code or result.blocked_reason_code) is not None
    }
    return reasons.pop() if len(reasons) == 1 else None


def _reconciliation_required_reason(
    results: Collection[RuntimeSourceLaneResult],
) -> str | None:
    for result in results:
        for reason in (
            result.stop_reason_code,
            result.blocked_reason_code,
        ):
            if public_source_problem_code(reason) == ("liepin_browser_lane_reconciliation_required"):
                return reason
    return None


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
        produces_private_first_page_continuations=True,
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
        private_first_page_continuations=search_result.private_continuations,
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
    cards_operation_executor,
) -> RuntimeSourceLaneResult:
    context = normalize_runtime_liepin_context(request.source_context)
    query_terms = list(request.source_query_terms or _basic_source_query_terms(request))
    if cards_operation_executor is not None:
        bind_lane = getattr(cards_operation_executor, "bind_lane", None)
        if callable(bind_lane):
            bind_lane(
                source_lane_run_id,
                request.logical_query_instance_id or source_lane_run_id,
                source_plan_id=source_plan_id,
                round_no=request.logical_round_no or request.attempt,
                query_terms=tuple(query_terms),
                keyword_query=(request.logical_keyword_query or " ".join(query_terms)),
                query_fingerprint=(
                    request.logical_query_fingerprint or request.logical_query_instance_id or source_lane_run_id
                ),
                query_role=request.logical_query_role or "exploit",
                requested_count=(request.logical_requested_count or request.source_budget_policy.detail_target or 1),
                max_pages=request.source_budget_policy.max_pages,
                max_cards=request.source_budget_policy.max_cards,
                claim_aware=False,
            )
    client = worker_client or build_liepin_worker_client(
        settings,
        cards_operation_executor=cards_operation_executor,
        lifecycle_supervisor=_lifecycle_supervisor_from_executor(cards_operation_executor),
    )
    provider = _build_provider(
        settings=settings,
        worker_client=client,
        readiness_preparer=getattr(
            cards_operation_executor,
            "prepare_readiness",
            None,
        ),
    )
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
        produces_private_first_page_continuations=True,
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
        retryable=legacy_lane_retryable_metadata(reason_code),
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
    public_reason = public_source_problem_code(reason_code) or "source_unknown"
    summary = f"{type(error).__name__}: {public_reason}"
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
                {
                    "cards_seen": int(raw_candidate_count or candidate_count),
                    "details_opened": candidate_count,
                    "candidates": candidate_count,
                }
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
        source_references=candidate.source_references,
    )


def build_resumed_liepin_cards_lane_result(
    *,
    plan: LiepinRoundWorkPlanV1,
    lane: LiepinRoundLaneWorkItemV1,
    budget: RuntimeSourceBudgetPolicy,
    artifact: LiepinCardsArtifactV1,
) -> RuntimeSourceLaneResult:
    """Project one completed cards artifact without re-entering the provider."""
    from seektalent.providers.liepin.mapper import map_liepin_worker_card
    from seektalent.providers.liepin.worker_contracts import (
        LiepinSafeCardSummary,
        LiepinWorkerCandidateCard,
    )

    mapped_candidates = []
    for raw_card in artifact.cards[: budget.max_cards]:
        card = {str(key): value for key, value in raw_card.items()}
        summary_payload = {key: card[key] for key in LiepinSafeCardSummary.model_fields if key in card}
        if "masked_name" not in summary_payload and isinstance(
            card.get("display_name_masked"),
            bool,
        ):
            summary_payload["masked_name"] = card["display_name_masked"]
        summary = LiepinSafeCardSummary.model_validate(summary_payload)
        fingerprint = hashlib.sha256(
            canonical_json_bytes(
                {
                    "contract": "seektalent.liepin-card-identity/v1",
                    "card": card,
                }
            )
        ).hexdigest()
        mapped_candidates.append(
            map_liepin_worker_card(
                LiepinWorkerCandidateCard(
                    payload=card,
                    normalized_text="",
                    provider_subject_id=None,
                    provider_listing_id=None,
                    synthetic_candidate_fingerprint=fingerprint,
                    identity_confidence="synthetic_fingerprint",
                    extraction_source="dom_fallback",
                    extractor_version=("liepin-opencli-deterministic-v1"),
                    pii_classification="no_direct_contact",
                    retention_policy="provider_snapshot_7d",
                    access_scope="local_run_only",
                    redaction_state="raw_provider_payload",
                    safeCardSummary=summary,
                )
            )
        )
    candidates = tuple(item.candidate for item in mapped_candidates)
    source_plan = RuntimeSourceLanePlan(
        source_plan_id=plan.source_plan_id,
        runtime_run_id=plan.runtime_run_id,
        source="liepin",
        label="Liepin",
        lane_mode="card",
        backend_mode="runtime_source_lane",
        max_cards=budget.max_cards,
        max_details=budget.max_details,
        produces_private_first_page_continuations=True,
        source_budget_policy=budget,
    )
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
            evidence_level="card",
            source_lane_run_id=lane.source_lane_run_id,
            provider_rank=index,
            query_fingerprint=lane.query_fingerprint,
        )
        for index, candidate in enumerate(candidates, start=1)
    )
    attributions = tuple(
        RuntimeQueryCandidateAttribution(
            source_kind="liepin",
            query_instance_id=lane.query_instance_id,
            resume_id=candidate.resume_id,
            dedup_key=candidate.dedup_key,
        )
        for candidate in candidates
    )
    if artifact.status == "succeeded":
        status: RuntimeSourceLaneStatus = "completed"
    elif artifact.status == "partial":
        status = "partial"
    else:
        status = "failed"
    return RuntimeSourceLaneResult(
        runtime_run_id=plan.runtime_run_id,
        source_plan_id=plan.source_plan_id,
        source_lane_run_id=lane.source_lane_run_id,
        source="liepin",
        lane_mode="card",
        attempt=plan.round_no,
        status=status,
        candidate_store_updates={candidate.resume_id: candidate for candidate in candidates},
        source_evidence_updates=evidence_updates,
        provider_snapshots=tuple(item.provider_snapshot for item in mapped_candidates),
        raw_candidate_count=artifact.cards_seen,
        events=(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=plan.runtime_run_id,
                source_plan_id=plan.source_plan_id,
                source_lane_run_id=lane.source_lane_run_id,
                source="liepin",
                attempt=plan.round_no,
                event_seq=1,
                event_type="source_lane_completed",
                status=status,
                safe_counts={"cards_seen": artifact.cards_seen},
                safe_reason_code=artifact.safe_reason_code,
            ),
        ),
        query_started=True,
        candidate_query_attributions=attributions,
        stop_reason_code=artifact.safe_reason_code,
    )


def build_resumed_liepin_detail_lane_result(
    *,
    plan,
    structured_results: Sequence[Mapping[str, object]],
) -> RuntimeSourceStepResumeResult:
    """Project a completed durable capture queue through the normal lane boundary."""
    from seektalent.providers.liepin.mapper import (
        liepin_worker_detail_from_resume_payload,
        map_liepin_worker_detail,
    )

    if plan.phase != "captures":
        raise ValueError("runtime_detail_work_plan_capture_required")
    candidates: list[ResumeCandidate] = []
    provider_snapshots: list[object] = []
    rank_by_resume_id: dict[str, int] = {}
    item_by_rank = {item.rank: item for item in plan.items}
    for structured in structured_results:
        resume = structured.get("resume")
        raw_counts = structured.get("counts")
        rank = cast(Mapping[str, object], raw_counts).get("rank") if isinstance(raw_counts, Mapping) else None
        if (
            structured.get("ingest_ready") is not True
            or not isinstance(resume, Mapping)
            or isinstance(rank, bool)
            or not isinstance(rank, int)
        ):
            continue
        item = item_by_rank.get(rank)
        if item is None or item.provider_candidate_key_hash is None:
            raise ValueError("runtime_detail_work_plan_result_mismatch")
        resume_payload = {str(key): value for key, value in resume.items()}
        resume_payload["claim_aware"] = True
        resume_payload["provider_candidate_key_hash"] = item.provider_candidate_key_hash
        mapped = map_liepin_worker_detail(
            liepin_worker_detail_from_resume_payload(
                resume_payload,
                action_trace_ref=None,
            )
        )
        candidates.append(mapped.candidate)
        provider_snapshots.append(mapped.provider_snapshot)
        rank_by_resume_id[mapped.candidate.resume_id] = rank

    source_plan = RuntimeSourceLanePlan(
        source_plan_id=plan.source_plan_id,
        runtime_run_id=plan.runtime_run_id,
        source="liepin",
        label="Liepin",
        lane_mode="detail",
        backend_mode="runtime_source_lane",
        max_cards=plan.max_cards,
        max_details=plan.requested_count,
        produces_private_first_page_continuations=True,
    )
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
            evidence_level="detail",
            source_lane_run_id=plan.source_lane_run_id,
            provider_rank=rank_by_resume_id[candidate.resume_id],
            query_fingerprint=plan.query_fingerprint,
        )
        for candidate in candidates
    )
    attributions = tuple(
        RuntimeQueryCandidateAttribution(
            source_kind="liepin",
            query_instance_id=plan.query_instance_id,
            resume_id=candidate.resume_id,
            dedup_key=candidate.dedup_key,
        )
        for candidate in candidates
    )
    status: RuntimeSourceLaneStatus = "completed" if len(candidates) >= plan.requested_count else "partial"
    lane_result = RuntimeSourceLaneResult(
        runtime_run_id=plan.runtime_run_id,
        source_plan_id=plan.source_plan_id,
        source_lane_run_id=plan.source_lane_run_id,
        source="liepin",
        lane_mode="detail",
        attempt=plan.round_no,
        status=status,
        candidate_store_updates={candidate.resume_id: candidate for candidate in candidates},
        source_evidence_updates=evidence_updates,
        provider_snapshots=tuple(provider_snapshots),
        raw_candidate_count=len(plan.items),
        events=(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=plan.runtime_run_id,
                source_plan_id=plan.source_plan_id,
                source_lane_run_id=plan.source_lane_run_id,
                source="liepin",
                attempt=plan.round_no,
                event_seq=1,
                event_type="detail_completed",
                status=status,
                safe_counts={"details_opened": len(candidates)},
            ),
        ),
        executed_query_packages=(
            RuntimeQueryPackage(
                source_kind="liepin",
                query_role=plan.query_role,
                query_instance_id=plan.query_instance_id,
                query_fingerprint=plan.query_fingerprint,
                query_terms=tuple(plan.query_terms),
                keyword_query=plan.keyword_query,
            ),
        ),
        query_started=True,
        query_execution_outcomes=(
            SourceQueryExecutionOutcome(
                query_instance_id=plan.query_instance_id,
                status=status,
                dispatch_started=True,
                raw_candidate_count=len(plan.items),
                unique_candidate_count=len(candidates),
                exhausted_reason=(None if status == "completed" else "durable_detail_target_not_met"),
                safe_reason_code=(None if status == "completed" else "durable_detail_target_not_met"),
            ),
        ),
        candidate_query_attributions=attributions,
        stop_reason_code=(None if status == "completed" else "durable_detail_target_not_met"),
    )
    return RuntimeSourceStepResumeResult(
        round_no=plan.round_no,
        lane_result=lane_result,
        query_terms=tuple(plan.query_terms),
        keyword_query=plan.keyword_query,
        query_instance_id=plan.query_instance_id,
        query_fingerprint=plan.query_fingerprint,
        query_role=cast(QueryRole, plan.query_role),
        requested_count=plan.requested_count,
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
    default_query_fingerprint = (
        request.logical_query_fingerprint or hashlib.sha256(" ".join(default_query_terms).encode("utf-8")).hexdigest()
    )
    provider_scan_limit = (
        request.logical_provider_scan_limit or request.logical_requested_count or request.source_budget_policy.max_cards
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
        provider_context["liepin_max_cards"] = str(provider_scan_limit)
    max_cards = _positive_context_int(provider_context.get("liepin_max_cards"), default=provider_scan_limit)
    if compiled_search_request is not None and compiled_search_request.fetch_mode == "detail":
        provider_context["liepin_max_pages"] = "1"
    else:
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
    readiness_preparer: Callable[[], None] | None = None,
) -> LiepinProviderAdapter:
    from seektalent.liepin_verify_session_gate import (
        create_production_liepin_verify_session_gate,
    )

    store = None
    if is_live_liepin_worker_mode(settings.liepin_worker_mode):
        store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    return LiepinProviderAdapter(
        settings,
        worker_client=worker_client,
        worker_search_started_callback=worker_search_started_callback,
        store=store,
        verify_session_gate=(
            create_production_liepin_verify_session_gate(settings) if settings.liepin_worker_mode == "opencli" else None
        ),
        readiness_preparer=(readiness_preparer if settings.liepin_worker_mode == "opencli" else None),
    )


def _lifecycle_supervisor_from_executor(executor: object | None):
    if executor is None:
        return None
    return getattr(executor, "_wtscli_lifecycle_supervisor", None)


def _liepin_max_pages(budget: RuntimeSourceBudgetPolicy) -> int:
    return _liepin_max_pages_for(max_cards=budget.max_cards, page_size=budget.page_size)


def _liepin_max_pages_for(*, max_cards: int, page_size: int) -> int:
    normalized_page_size = max(1, page_size)
    return max(1, math.ceil(max_cards / normalized_page_size))


def runtime_reason_code_from_worker_failure_code(
    failure_code: object,
) -> str:
    value = str(getattr(failure_code, "value", failure_code or "")).strip()
    return value or "failed_internal_error"


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
