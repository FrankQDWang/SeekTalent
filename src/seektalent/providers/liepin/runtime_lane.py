from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from datetime import datetime

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.models import ResumeCandidate, RuntimeSourceEvidence
from seektalent.providers.liepin.adapter import LiepinProviderAdapter
from seektalent.providers.liepin.client import LiepinWorkerClient, build_liepin_worker_client
from seektalent.providers.liepin.store import LiepinStore
from seektalent.runtime.source_lanes import (
    RuntimeDetailRecommendation,
    RuntimeSourceLaneEvent,
    RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
)


def liepin_backend_posture(settings: AppSettings) -> dict[str, str]:
    worker_mode = settings.liepin_worker_mode
    if worker_mode in {"managed_local", "external_http"}:
        return {"backend_mode": "legacy_worker_compat", "reason": worker_mode}
    if worker_mode == "fake_fixture" and settings.liepin_allow_fake_fixture_worker:
        return {"backend_mode": "fake_fixture", "reason": "explicit_test_fixture"}
    return {"backend_mode": "blocked", "reason": "no_live_action_backend"}


async def run_liepin_source_lane(
    *,
    settings: AppSettings,
    request: RuntimeSourceLaneRequest,
    worker_client: LiepinWorkerClient | None = None,
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

    context = request.liepin_context or {}
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
            fetch_mode="summary",
            page_size=30,
            provider_context={
                "liepin_tenant_id": _context_text(context, "tenant_id", default="local"),
                "liepin_workspace_id": _context_text(context, "workspace_id", default="default"),
                "liepin_actor_id": _context_text(context, "actor_id", default="local"),
                "liepin_connection_id": _context_text(context, "connection_id"),
                "liepin_compliance_gate_ref": _context_text(context, "compliance_gate_ref"),
                "liepin_provider_account_hash": _context_text(context, "provider_account_hash"),
                "query_instance_id": source_lane_run_id,
                "query_fingerprint": hashlib.sha256(" ".join(query_terms).encode("utf-8")).hexdigest(),
            },
        ),
        round_no=1,
        trace_id=source_lane_run_id,
    )
    source_plan = RuntimeSourceLanePlan(
        source_plan_id=source_plan_id,
        runtime_run_id=runtime_run_id,
        source="liepin",
        label="Liepin",
        backend_mode="runtime_source_lane",
    )
    candidates = tuple(search_result.candidates)
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
        )
        for candidate in candidates
    )
    detail_recommendations = _detail_recommendations_for_candidates(
        source_plan_id=source_plan_id,
        candidates=candidates,
        evidence_updates=evidence_updates,
        query_terms=query_terms,
    )
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="card",
        attempt=request.attempt,
        status="completed",
        candidate_store_updates={candidate.resume_id: candidate for candidate in candidates},
        source_evidence_updates=evidence_updates,
        detail_recommendations=detail_recommendations,
        provider_snapshots=tuple(search_result.provider_snapshots),
        raw_candidate_count=search_result.raw_candidate_count,
        events=_card_lane_events(
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            attempt=request.attempt,
            raw_candidate_count=search_result.raw_candidate_count,
            candidate_count=len(candidates),
            detail_recommendation_count=len(detail_recommendations),
        ),
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
    context = request.liepin_context or {}
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
    )
    candidates = tuple(search_result.candidates)
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
            evidence_level="detail",
        )
        for candidate in candidates
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


def _card_lane_events(
    *,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    attempt: int,
    raw_candidate_count: int | None,
    candidate_count: int,
    detail_recommendation_count: int,
) -> tuple[RuntimeSourceLaneEvent, ...]:
    events = [
        RuntimeSourceLaneEvent(
            schema_version="runtime_source_lane_event_v1",
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            source="liepin",
            attempt=attempt,
            event_seq=1,
            event_type="source_lane_completed",
            status="completed",
            safe_counts={"cards_seen": int(raw_candidate_count or candidate_count), "candidates": candidate_count},
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


def _source_evidence_for_candidate(
    *,
    source_plan: RuntimeSourceLanePlan,
    candidate: ResumeCandidate,
    collected_at: str,
    evidence_level: str = "card",
) -> RuntimeSourceEvidence:
    provider_candidate_key = candidate.source_resume_id or candidate.dedup_key or candidate.resume_id
    provider_candidate_key_hash = hashlib.sha256(
        f"{source_plan.runtime_run_id}:liepin:{provider_candidate_key}".encode("utf-8")
    ).hexdigest()
    return RuntimeSourceEvidence(
        evidence_id=f"{source_plan.source_plan_id}:liepin:{provider_candidate_key_hash}",
        source="liepin",
        provider="liepin",
        evidence_level=evidence_level,
        candidate_resume_id=candidate.resume_id,
        provider_candidate_key_hash=provider_candidate_key_hash,
        query_fingerprint=None,
        provider_snapshot_ref=_candidate_ref(candidate, "provider_snapshot_ref", "raw_payload_artifact_ref"),
        safe_summary_ref=_candidate_ref(candidate, "safe_summary_ref"),
        collected_at=collected_at,
        score_hint=None,
        reason_code="source_detail_candidate" if evidence_level == "detail" else "source_card_candidate",
    )


def _detail_recommendations_for_candidates(
    *,
    source_plan_id: str,
    candidates: tuple[ResumeCandidate, ...],
    evidence_updates: tuple[RuntimeSourceEvidence, ...],
    query_terms: Collection[str],
) -> tuple[RuntimeDetailRecommendation, ...]:
    recommendations: list[RuntimeDetailRecommendation] = []
    for candidate, evidence in zip(candidates, evidence_updates, strict=True):
        searchable = candidate.search_text.casefold()
        matched_terms = [term.strip() for term in query_terms if term.strip() and term.strip().casefold() in searchable]
        if len(matched_terms) < 2:
            continue
        recommendations.append(
            RuntimeDetailRecommendation(
                recommendation_id=f"{source_plan_id}:detail:{candidate.resume_id}",
                source="liepin",
                source_evidence_id=evidence.evidence_id,
                candidate_resume_id=candidate.resume_id,
                provider_candidate_key_hash=evidence.provider_candidate_key_hash,
                value_score=min(100, 40 + len(matched_terms) * 20),
                reason_code="matched_card_terms",
                safe_reason=f"Agent recommends opening detail after matched card terms: {', '.join(matched_terms[:3])}",
                provider_snapshot_ref=evidence.provider_snapshot_ref,
                safe_summary_ref=evidence.safe_summary_ref,
            )
        )
    return tuple(recommendations)


def _context_text(context: Mapping[str, object], key: str, *, default: str | None = None) -> str | None:
    value = context.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


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


def _detail_provider_context(
    *,
    request: RuntimeSourceLaneRequest,
    context: Mapping[str, object],
    source_lane_run_id: str,
    query_terms: list[str],
) -> dict[str, str]:
    lease = request.approved_detail_lease
    if lease is None:
        raise ValueError("Liepin detail source lane requires an approved detail lease.")
    return {
        "liepin_tenant_id": _context_text(context, "tenant_id", default="local") or "local",
        "liepin_workspace_id": _context_text(context, "workspace_id", default="default") or "default",
        "liepin_actor_id": _context_text(context, "actor_id", default="local") or "local",
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


def _build_provider(*, settings: AppSettings, worker_client: LiepinWorkerClient) -> LiepinProviderAdapter:
    store = None
    if settings.liepin_worker_mode in {"managed_local", "external_http"}:
        store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    return LiepinProviderAdapter(settings, worker_client=worker_client, store=store)


def _candidate_ref(candidate: ResumeCandidate, *keys: str) -> str | None:
    if not isinstance(candidate.raw, dict):
        return None
    for key in keys:
        value = candidate.raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
