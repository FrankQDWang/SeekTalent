from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from seektalent.models import ResumeCandidate
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch

if TYPE_CHECKING:
    from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult
    from seektalent.runtime.source_lanes import RuntimeSourceLaneResult

SourceKind = Literal["cts", "liepin"]
SourceRoundDispatchStatus = Literal["completed", "partial", "blocked", "failed"]
SourceRoundAdapter = Callable[["SourceRoundDispatchRequest"], Awaitable["SourceRoundAdapterResult"]]


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


@dataclass(frozen=True)
class SourceRoundDispatchResult:
    source_results: tuple[SourceRoundAdapterResult, ...]
    candidates: tuple[ResumeCandidate, ...]
    raw_candidate_count: int


async def dispatch_source_rounds(
    *,
    request: SourceRoundDispatchRequest,
    cts_adapter: SourceRoundAdapter,
    liepin_adapter: SourceRoundAdapter,
) -> SourceRoundDispatchResult:
    adapters: dict[SourceKind, SourceRoundAdapter] = {
        "cts": cts_adapter,
        "liepin": liepin_adapter,
    }
    tasks: dict[SourceKind, asyncio.Task[SourceRoundAdapterResult]] = {}
    try:
        async with asyncio.TaskGroup() as task_group:
            for source in request.selected_sources:
                if source not in adapters:
                    raise RuntimeSourceInvariantError(f"unsupported_source_kind:{source}")
                tasks[source] = task_group.create_task(_run_adapter_safely(source, adapters[source], request))
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
    for result in source_results:
        candidates.extend(result.candidates)
        raw_candidate_count += result.raw_candidate_count
    return SourceRoundDispatchResult(
        source_results=source_results,
        candidates=tuple(candidates),
        raw_candidate_count=raw_candidate_count,
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
        )
    except SourceProviderPartial:
        return SourceRoundAdapterResult(
            source=source,
            status="partial",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="partial_timeout",
            diagnostics=(f"{source} source returned partial coverage.",),
        )
    except SourceProviderFailed:
        return SourceRoundAdapterResult(
            source=source,
            status="failed",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="failed_provider_error",
            diagnostics=(f"{source} source failed before completion.",),
        )
