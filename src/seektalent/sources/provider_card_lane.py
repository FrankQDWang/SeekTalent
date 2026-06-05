from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from seektalent.core.retrieval.provider_contract import FetchMode, QueryRole as ProviderQueryRole, SearchResult
from seektalent.models import ConstraintValue, ResumeCandidate, RuntimeConstraint, RuntimeSourceEvidence
from seektalent.source_contracts import SourceLaneRequest, SourceLaneResult


class ProviderCardSearch(Protocol):
    def __call__(
        self,
        *,
        query_terms: list[str],
        query_role: ProviderQueryRole,
        keyword_query: str,
        adapter_notes: list[str],
        provider_filters: dict[str, ConstraintValue],
        runtime_constraints: list[RuntimeConstraint],
        page_size: int,
        round_no: int,
        trace_id: str,
        fetch_mode: FetchMode = "summary",
        provider_context: dict[str, str] | None = None,
        cursor: str | None = None,
    ) -> Awaitable[SearchResult]: ...


async def run_provider_card_lane(
    *,
    request: SourceLaneRequest,
    search: ProviderCardSearch,
    provider_filters: Mapping[str, ConstraintValue] | None = None,
    runtime_constraints: Sequence[RuntimeConstraint] = (),
    adapter_notes: Sequence[str] = (),
    provider_context: Mapping[str, str] | None = None,
    collected_at: str | None = None,
) -> SourceLaneResult:
    query_terms = list(request.source_query_terms or (request.job_title,))
    page_size = request.budget.card_target
    search_result = await search(
        query_terms=query_terms,
        query_role="primary",
        keyword_query=" ".join(query_terms),
        adapter_notes=list(adapter_notes),
        provider_filters=dict(provider_filters or {}),
        runtime_constraints=list(runtime_constraints),
        page_size=page_size,
        round_no=request.attempt,
        trace_id=f"{request.runtime_run_id}-source-{request.source_id}-a{request.attempt}",
        fetch_mode="summary",
        provider_context=dict(provider_context or {}),
        cursor="1",
    )
    if not isinstance(search_result, SearchResult):
        raise TypeError("provider card search must return SearchResult")

    candidates = tuple(search_result.candidates[:page_size])
    collected = collected_at or datetime.now().astimezone().isoformat(timespec="seconds")
    query_fingerprint = hashlib.sha256(" ".join(query_terms).encode("utf-8")).hexdigest()
    return SourceLaneResult(
        runtime_run_id=request.runtime_run_id,
        source_plan_id=request.source_plan_id,
        source_lane_run_id=request.source_lane_run_id,
        source_id=request.source_id,
        lane_mode=request.lane_mode,
        attempt=request.attempt,
        status="completed",
        candidate_store_updates={candidate.resume_id: candidate for candidate in candidates},
        raw_candidate_count=search_result.raw_candidate_count,
        source_evidence_updates=tuple(
            _source_evidence_for_candidate(
                request=request,
                candidate=candidate,
                collected_at=collected,
                provider_rank=index,
                query_fingerprint=query_fingerprint,
            )
            for index, candidate in enumerate(candidates, start=1)
        ),
    )


def _source_evidence_for_candidate(
    *,
    request: SourceLaneRequest,
    candidate: ResumeCandidate,
    collected_at: str,
    provider_rank: int,
    query_fingerprint: str,
) -> RuntimeSourceEvidence:
    provider_candidate_key = candidate.source_resume_id or candidate.dedup_key or candidate.resume_id
    provider_candidate_key_hash = hashlib.sha256(
        f"{request.runtime_run_id}:{request.source_id}:{provider_candidate_key}".encode("utf-8")
    ).hexdigest()
    provider_snapshot_ref = None
    safe_summary_ref = None
    if isinstance(candidate.raw, dict):
        raw_snapshot_ref = candidate.raw.get("provider_snapshot_ref")
        raw_summary_ref = candidate.raw.get("safe_summary_ref")
        provider_snapshot_ref = raw_snapshot_ref if isinstance(raw_snapshot_ref, str) else None
        safe_summary_ref = raw_summary_ref if isinstance(raw_summary_ref, str) else None
    return RuntimeSourceEvidence(
        evidence_id=f"{request.source_plan_id}:{request.source_id}:{provider_candidate_key_hash}",
        source=request.source_id,
        provider=request.source_id,
        source_plan_id=request.source_plan_id,
        source_lane_run_id=request.source_lane_run_id,
        evidence_level=request.lane_mode,
        candidate_resume_id=candidate.resume_id,
        provider_candidate_key_hash=provider_candidate_key_hash,
        provider_rank=provider_rank,
        query_fingerprint=query_fingerprint,
        provider_snapshot_ref=provider_snapshot_ref,
        safe_summary_ref=safe_summary_ref,
        collected_at=collected_at,
        reason_code="source_card_candidate",
    )
