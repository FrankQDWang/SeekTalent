from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime

from seektalent.corpus.runtime import ProviderReturnedCandidate, build_deterministic_provider_request_id
from seektalent.core.retrieval.provider_contract import ProviderSnapshot
from seektalent.models import ResumeCandidate, RuntimeSourceEvidence
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult
from seektalent.runtime.source_lanes import RuntimeSourceLaneResult
from seektalent.source_contracts import LogicalQueryDispatch
from seektalent.tracing import RunTracer

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
