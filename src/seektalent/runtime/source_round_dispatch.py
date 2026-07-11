from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from seektalent.models import QueryExecutionReceipt, ResumeCandidate
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.source_query_intent import RuntimeQueryPackage, RuntimeSourceQueryIntent
from seektalent.source_contracts.runtime_lanes import (
    RuntimeQueryCandidateAttribution,
    SourceQueryExecutionOutcome,
)

if TYPE_CHECKING:
    from seektalent.models import RequirementSheet
    from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult
    from seektalent.runtime.source_lanes import RuntimeSourceLaneResult

SourceKind = str
SourceRoundDispatchStatus = Literal["completed", "partial", "blocked", "failed"]
SourceRoundAdapter = Callable[["SourceRoundDispatchRequest"], Awaitable["SourceRoundAdapterResult"]]
SourceRoundResultCallback = Callable[["SourceRoundAdapterResult"], Awaitable[None] | None]


class SourceProviderBlocked(Exception):
    pass


class SourceProviderFailed(Exception):
    pass


class SourceProviderPartial(Exception):
    pass


class RuntimeSourceInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRoundDispatchRequest:
    runtime_run_id: str
    round_no: int
    logical_queries: tuple[LogicalQueryDispatch, ...]
    selected_sources: tuple[SourceKind, ...]
    seen_resume_ids: frozenset[str]
    seen_dedup_keys: frozenset[str]
    requirement_sheet: "RequirementSheet"
    source_query_intents_by_source: Mapping[SourceKind, tuple[RuntimeSourceQueryIntent, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceRoundAdapterResult:
    source: SourceKind
    status: SourceRoundDispatchStatus
    candidates: tuple[ResumeCandidate, ...] = ()
    raw_candidate_count: int = 0
    safe_reason_code: str | None = None
    diagnostics: tuple[str, ...] = ()
    retrieval_result: "RetrievalExecutionResult | None" = None
    lane_result: "RuntimeSourceLaneResult | None" = None
    executed_query_packages: tuple[RuntimeQueryPackage, ...] = ()
    query_execution_outcomes: tuple[SourceQueryExecutionOutcome, ...] = ()
    candidate_query_attributions: tuple[RuntimeQueryCandidateAttribution, ...] = ()


@dataclass(frozen=True)
class SourceRoundDispatchResult:
    source_results: tuple[SourceRoundAdapterResult, ...]
    candidates: tuple[ResumeCandidate, ...]
    raw_candidate_count: int
    executed_query_packages: tuple[RuntimeQueryPackage, ...] = ()
    query_execution_receipts: tuple[QueryExecutionReceipt, ...] = ()
    candidate_query_attributions: tuple[RuntimeQueryCandidateAttribution, ...] = ()


async def dispatch_source_rounds(
    *,
    request: SourceRoundDispatchRequest,
    source_adapters: Mapping[SourceKind, SourceRoundAdapter] | None = None,
    result_callback: SourceRoundResultCallback | None = None,
) -> SourceRoundDispatchResult:
    adapters: dict[SourceKind, SourceRoundAdapter] = dict(source_adapters or {})
    _validate_source_query_intents(request)
    tasks: dict[SourceKind, asyncio.Task[SourceRoundAdapterResult]] = {}
    try:
        async with asyncio.TaskGroup() as task_group:
            for source in request.selected_sources:
                if source not in adapters:
                    raise RuntimeSourceInvariantError(f"unsupported_source_kind:{source}")
                tasks[source] = task_group.create_task(
                    _run_adapter_and_report(
                        source,
                        adapters[source],
                        request,
                        result_callback=result_callback,
                    )
                )
    except* RuntimeSourceInvariantError as group:
        raise group.exceptions[0]
    except* AssertionError as group:
        raise group.exceptions[0]
    except* TypeError as group:
        raise group.exceptions[0]
    except* Exception as group:  # noqa: BLE001
        # Provider taxonomy is handled inside _run_adapter_safely. Anything
        # still unhandled here is a Runtime/programmer error and must fail
        # the round instead of becoming degraded source coverage.
        raise group.exceptions[0]

    source_results = tuple(tasks[source].result() for source in request.selected_sources)
    candidates: list[ResumeCandidate] = []
    raw_candidate_count = 0
    query_execution_receipts: list[QueryExecutionReceipt] = []
    candidate_query_attributions: list[RuntimeQueryCandidateAttribution] = []
    for source, result in zip(request.selected_sources, source_results, strict=True):
        if result.source != source:
            raise RuntimeSourceInvariantError(f"source_round_result_wrong_source:{source}")
        candidates.extend(result.candidates)
        raw_candidate_count += result.raw_candidate_count
        query_execution_receipts.extend(
            _receipts_for_source_result(
                request=request,
                source=source,
                result=result,
            )
        )
        candidate_query_attributions.extend(result.candidate_query_attributions)
    return SourceRoundDispatchResult(
        source_results=source_results,
        candidates=tuple(candidates),
        raw_candidate_count=raw_candidate_count,
        executed_query_packages=tuple(
            package for result in source_results for package in result.executed_query_packages
        ),
        query_execution_receipts=tuple(query_execution_receipts),
        candidate_query_attributions=tuple(candidate_query_attributions),
    )


def _validate_source_query_intents(request: SourceRoundDispatchRequest) -> None:
    if not request.source_query_intents_by_source:
        return
    for source in request.selected_sources:
        intents = request.source_query_intents_by_source.get(source)
        if intents is None:
            raise RuntimeSourceInvariantError(f"missing_source_query_intents:{source}")
        for intent in intents:
            if intent.source_kind != source:
                raise RuntimeSourceInvariantError(f"source_query_intent_wrong_source:{source}")
            if intent.round_no != request.round_no:
                raise RuntimeSourceInvariantError(f"source_query_intent_wrong_round:{source}")


def _receipts_for_source_result(
    *,
    request: SourceRoundDispatchRequest,
    source: SourceKind,
    result: SourceRoundAdapterResult,
) -> tuple[QueryExecutionReceipt, ...]:
    if not request.source_query_intents_by_source:
        return ()
    intents = request.source_query_intents_by_source[source]
    intents_by_query_instance_id: dict[str, RuntimeSourceQueryIntent] = {}
    for intent in intents:
        if intent.query_instance_id in intents_by_query_instance_id:
            raise RuntimeSourceInvariantError(f"duplicate_source_query_intent:{source}:{intent.query_instance_id}")
        intents_by_query_instance_id[intent.query_instance_id] = intent

    outcomes_by_query_instance_id: dict[str, SourceQueryExecutionOutcome] = {}
    for outcome in result.query_execution_outcomes:
        query_instance_id = outcome.query_instance_id
        if query_instance_id in outcomes_by_query_instance_id:
            raise RuntimeSourceInvariantError(f"duplicate_source_query_outcome:{source}:{query_instance_id}")
        if query_instance_id not in intents_by_query_instance_id:
            raise RuntimeSourceInvariantError(f"unmatched_source_query_outcome:{source}:{query_instance_id}")
        outcomes_by_query_instance_id[query_instance_id] = outcome

    receipts: list[QueryExecutionReceipt] = []
    for intent in intents:
        outcome = outcomes_by_query_instance_id.get(intent.query_instance_id)
        if outcome is None:
            raise RuntimeSourceInvariantError(f"missing_source_query_outcome:{source}:{intent.query_instance_id}")
        receipts.append(
            QueryExecutionReceipt(
                round_no=intent.round_no,
                source_kind=intent.source_kind,
                query_instance_id=intent.query_instance_id,
                query_fingerprint=intent.query_fingerprint,
                term_group_key=intent.term_group_key,
                primary_anchor_family_id=intent.primary_anchor_family_id,
                non_anchor_term_family_ids=list(intent.non_anchor_term_family_ids),
                query_role=intent.query_role,
                lane_type=intent.lane_type,
                query_terms=list(intent.query_terms),
                keyword_query=intent.keyword_query,
                requested_count=intent.requested_count,
                source_plan_version=intent.source_plan_version,
                status=outcome.status,
                dispatch_started=outcome.dispatch_started,
                raw_candidate_count=outcome.raw_candidate_count,
                unique_candidate_count=outcome.unique_candidate_count,
                duplicate_candidate_count=outcome.duplicate_candidate_count,
                pre_click_skipped_seen_count=outcome.pre_click_skipped_seen_count,
                exhausted_reason=outcome.exhausted_reason,
                safe_reason_code=outcome.safe_reason_code,
            )
        )
    return tuple(receipts)


def _terminal_outcomes_for_exception(
    *,
    request: SourceRoundDispatchRequest,
    source: SourceKind,
    status: SourceRoundDispatchStatus,
    dispatch_started: bool,
    safe_reason_code: str,
) -> tuple[SourceQueryExecutionOutcome, ...]:
    return tuple(
        SourceQueryExecutionOutcome(
            query_instance_id=intent.query_instance_id,
            status=status,
            dispatch_started=dispatch_started,
            safe_reason_code=safe_reason_code,
        )
        for intent in request.source_query_intents_by_source.get(source, ())
    )


async def _run_adapter_safely(
    source: SourceKind,
    adapter: SourceRoundAdapter,
    request: SourceRoundDispatchRequest,
) -> SourceRoundAdapterResult:
    try:
        return await adapter(request)
    except asyncio.CancelledError:
        raise
    except RuntimeSourceInvariantError:
        raise
    except (AssertionError, TypeError):
        raise
    except SourceProviderBlocked:
        return SourceRoundAdapterResult(
            source=source,
            status="blocked",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="blocked_backend_unavailable",
            diagnostics=(f"{source} source was blocked before completion.",),
            query_execution_outcomes=_terminal_outcomes_for_exception(
                request=request,
                source=source,
                status="blocked",
                dispatch_started=False,
                safe_reason_code="blocked_backend_unavailable",
            ),
        )


    except SourceProviderPartial:
        return SourceRoundAdapterResult(
            source=source,
            status="partial",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="partial_timeout",
            diagnostics=(f"{source} source returned partial coverage.",),
            query_execution_outcomes=_terminal_outcomes_for_exception(
                request=request,
                source=source,
                status="partial",
                dispatch_started=True,
                safe_reason_code="partial_timeout",
            ),
        )
    except SourceProviderFailed:
        return SourceRoundAdapterResult(
            source=source,
            status="failed",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="failed_provider_error",
            diagnostics=(f"{source} source failed before completion.",),
            query_execution_outcomes=_terminal_outcomes_for_exception(
                request=request,
                source=source,
                status="failed",
                dispatch_started=True,
                safe_reason_code="failed_provider_error",
            ),
        )


async def _run_adapter_and_report(
    source: SourceKind,
    adapter: SourceRoundAdapter,
    request: SourceRoundDispatchRequest,
    *,
    result_callback: SourceRoundResultCallback | None,
) -> SourceRoundAdapterResult:
    result = await _run_adapter_safely(source, adapter, request)
    if result_callback is not None:
        maybe_awaitable = result_callback(result)
        if maybe_awaitable is not None:
            await maybe_awaitable
    return result
