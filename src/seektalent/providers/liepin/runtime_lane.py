from __future__ import annotations

import hashlib
import math
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import cast

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.models import ResumeCandidate, RuntimeSourceEvidence
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
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
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerPartialSearchError
from seektalent.runtime.source_lanes import (
    RuntimeDetailRecommendation,
    RuntimeEvidenceLevel,
    RuntimeSourceBudgetPolicy,
    RuntimeSourceLaneEventType,
    RuntimeSourceLaneEvent,
    RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
    RuntimeSourceLaneStatus,
)


OPENCLI_SAFE_REASON_CODES = frozenset(
    {
        "liepin_opencli_backend_disabled",
        "liepin_opencli_command_missing",
        "liepin_opencli_extension_disconnected",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_forbidden_command",
        "liepin_opencli_forbidden_text",
        "liepin_opencli_host_blocked",
        "liepin_opencli_start_url_blocked",
        "liepin_opencli_window_policy_blocked",
        "liepin_opencli_budget_exhausted",
        "liepin_opencli_timeout",
        "liepin_opencli_login_required",
        "liepin_opencli_identity_intercept",
        "liepin_opencli_risk_page",
        "liepin_opencli_unknown_modal",
        "liepin_opencli_source_policy_missing",
        "liepin_opencli_malformed_state",
    }
)


def liepin_backend_posture(settings: AppSettings) -> dict[str, str]:
    worker_mode = settings.liepin_worker_mode
    if worker_mode == "pi_agent":
        return {"backend_mode": "pi_agent", "reason": worker_mode}
    if is_live_liepin_worker_mode(worker_mode):
        return {"backend_mode": "worker_compat", "reason": worker_mode}
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

    context = request.liepin_context or {}
    query_terms = list(request.source_query_terms or _basic_source_query_terms(request))
    budget = request.source_budget_policy
    client = worker_client or build_liepin_worker_client(settings)
    provider = _build_provider(settings=settings, worker_client=client)
    keyword_query = request.logical_keyword_query or " ".join(query_terms)
    query_fingerprint = request.logical_query_fingerprint or hashlib.sha256(" ".join(query_terms).encode("utf-8")).hexdigest()
    page_size = request.logical_requested_count or budget.liepin_card_page_size
    provider_context = {
        key: value
        for key, value in {
            "liepin_tenant_id": _context_text(context, "tenant_id", default="local"),
            "liepin_workspace_id": _context_text(context, "workspace_id", default="default"),
            "liepin_actor_id": _context_text(context, "actor_id", default="local"),
            "liepin_connection_id": _context_text(context, "connection_id"),
            "liepin_compliance_gate_ref": _context_text(context, "compliance_gate_ref"),
            "liepin_provider_account_hash": _context_text(context, "provider_account_hash"),
            "liepin_card_page_size": str(budget.liepin_card_page_size),
            "liepin_max_cards": str(budget.liepin_max_cards),
            "liepin_max_pages": str(_liepin_max_pages(budget)),
            "query_instance_id": request.logical_query_instance_id or source_lane_run_id,
            "query_fingerprint": query_fingerprint,
        }.items()
        if value is not None
    }
    search_request = SearchRequest(
        query_terms=query_terms,
        query_role="primary",
        keyword_query=keyword_query,
        adapter_notes=[request.notes or ""],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=page_size,
        provider_context=provider_context,
    )
    try:
        search_result = await provider.search(
            search_request,
            round_no=1,
            trace_id=source_lane_run_id,
        )
    except LiepinWorkerPartialSearchError as error:
        stop_reason_code = runtime_safe_reason_code_from_pi_failure_code(
            error.code,
            cards_collected=error.cards_collected > 0,
        )
        return _card_lane_result_from_search_result(
            request=request,
            search_result=error.partial_search_result,
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            query_terms=query_terms,
            query_fingerprint=query_fingerprint,
            status="partial",
            stop_reason_code=stop_reason_code,
        )
    except LiepinWorkerModeError as error:
        reason_code = runtime_safe_reason_code_from_pi_failure_code(error.code)
        return _blocked_card_result(
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            attempt=request.attempt,
            reason_code=reason_code,
        )
    return _card_lane_result_from_search_result(
        request=request,
        search_result=search_result,
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        query_terms=query_terms,
        query_fingerprint=query_fingerprint,
        status="completed",
    )


async def run_liepin_logical_query_bundle(
    *,
    settings: AppSettings,
    runtime_run_id: str,
    source_plan_id: str,
    job_title: str,
    jd: str,
    notes: str,
    logical_queries: tuple[LogicalQueryDispatch, ...],
    source_budget_policy: RuntimeSourceBudgetPolicy,
    liepin_context: Mapping[str, str | int | bool | None] | None,
    worker_client: LiepinWorkerClient | None = None,
) -> RuntimeSourceLaneResult:
    merged_result: RuntimeSourceLaneResult | None = None
    for index, logical_query in enumerate(logical_queries, start=1):
        result = await run_liepin_source_lane(
            settings=settings,
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title=job_title,
                jd=jd,
                notes=notes,
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=f"{source_plan_id}:round:{logical_query.round_no}:lane:{index}",
                source_query_terms=logical_query.query_terms,
                logical_query_instance_id=logical_query.query_instance_id,
                logical_query_fingerprint=logical_query.query_fingerprint,
                logical_keyword_query=logical_query.keyword_query,
                logical_requested_count=logical_query.requested_count,
                source_budget_policy=source_budget_policy,
                liepin_context=liepin_context or {},
            ),
            worker_client=worker_client,
        )
        merged_result = result if merged_result is None else merge_liepin_card_lane_results(merged_result, result)
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
        blocked_reason_code=blocked_reason_code,
        stop_reason_code=stop_reason_code,
        retryable=first.retryable or second.retryable,
        safe_error_summary=first.safe_error_summary or second.safe_error_summary,
        error_ref=first.error_ref or second.error_ref,
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
    stop_reason_code: str | None = None,
) -> RuntimeSourceLaneResult:
    budget = request.source_budget_policy
    source_plan = RuntimeSourceLanePlan(
        source_plan_id=source_plan_id,
        runtime_run_id=runtime_run_id,
        source="liepin",
        label="Liepin",
        backend_mode="runtime_source_lane",
        max_cards=budget.liepin_max_cards,
        max_details=budget.liepin_max_detail_recommendations,
        source_budget_policy=budget,
    )
    candidates = tuple(search_result.candidates[: budget.liepin_max_cards])
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_updates = tuple(
        _source_evidence_for_candidate(
            source_plan=source_plan,
            candidate=candidate,
            collected_at=collected_at,
            source_lane_run_id=source_lane_run_id,
            provider_rank=index,
            query_fingerprint=query_fingerprint,
        )
        for index, candidate in enumerate(candidates, start=1)
    )
    detail_recommendations = _detail_recommendations_for_candidates(
        source_plan_id=source_plan_id,
        candidates=candidates,
        evidence_updates=evidence_updates,
        query_terms=query_terms,
        role_title=request.job_title,
        max_recommendations=budget.liepin_max_detail_recommendations,
        budget_policy_version=budget.policy_version,
    )
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="card",
        attempt=request.attempt,
        status=status,
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
            status=status,
            stop_reason_code=stop_reason_code,
        ),
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
        source_budget_policy=request.source_budget_policy,
    )
    candidates = tuple(search_result.candidates)
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
) -> RuntimeSourceLaneResult:
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source="liepin",
        lane_mode="card",
        attempt=attempt,
        status="blocked",
        blocked_reason_code=reason_code,
        stop_reason_code=reason_code,
        retryable=reason_code in {"blocked_backend_unavailable", "failed_provider_error"},
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


def _card_lane_events(
    *,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    attempt: int,
    raw_candidate_count: int | None,
    candidate_count: int,
    detail_recommendation_count: int,
    status: RuntimeSourceLaneStatus,
    stop_reason_code: str | None = None,
) -> tuple[RuntimeSourceLaneEvent, ...]:
    event_type = cast(
        RuntimeSourceLaneEventType,
        "source_lane_partial" if status == "partial" else "source_lane_completed",
    )
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
            safe_counts={"cards_seen": int(raw_candidate_count or candidate_count), "candidates": candidate_count},
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
    role_title: str,
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
        role_title=role_title,
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
        city=_summary_string(summary, "city"),
        expected_city=_summary_string(summary, "expected_city"),
        education_level=_summary_string(summary, "education_level"),
        school_names=_summary_string_tuple(summary, "school_names"),
        major_names=_summary_string_tuple(summary, "major_names"),
        skill_tags=_summary_string_tuple(summary, "skill_tags"),
        job_intention=_summary_string(summary, "job_intention"),
        recent_experience_text=_summary_string(summary, "recent_experience_text"),
        normalized_card_text=candidate.search_text,
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


def _primary_card_policy_reason(reason_codes: tuple[str, ...]) -> str:
    for reason in ("matched_card_terms", "high_value_card", "card_rank_budget"):
        if reason in reason_codes:
            return reason
    return reason_codes[-1] if reason_codes else "matched_card_terms"


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


def _build_provider(*, settings: AppSettings, worker_client: LiepinWorkerClient) -> LiepinProviderAdapter:
    store = None
    if is_live_liepin_worker_mode(settings.liepin_worker_mode):
        store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    return LiepinProviderAdapter(settings, worker_client=worker_client, store=store)


def _liepin_max_pages(budget) -> int:
    page_size = max(1, budget.liepin_card_page_size)
    return max(1, math.ceil(budget.liepin_max_cards / page_size))


def runtime_safe_reason_code_from_pi_failure_code(
    failure_code: object,
    *,
    cards_collected: bool = False,
) -> str:
    value = str(getattr(failure_code, "value", failure_code or ""))
    if value in OPENCLI_SAFE_REASON_CODES:
        return value
    if value in {
        "liepin_pi_disabled",
        "liepin_pi_command_missing",
        "liepin_pi_command_invalid",
        "liepin_pi_skill_missing",
        "liepin_pi_account_secret_missing",
        "liepin_pi_mcp_config_missing",
        "liepin_pi_mcp_config_invalid",
        "liepin_pi_mcp_adapter_missing",
        "liepin_pi_mcp_adapter_unavailable",
        "liepin_pi_dokobot_mcp_command_missing",
        "liepin_pi_dokobot_mcp_config_mismatch",
        "liepin_pi_dokobot_mcp_tool_names_missing",
        "liepin_pi_dokobot_mcp_missing",
        "liepin_pi_dokobot_tool_unobserved",
        "liepin_browser_login_required",
        "liepin_browser_probe_unavailable",
        "liepin_browser_account_mismatch",
    }:
        return value
    if value in {"blocked_login_required", "login_expired"}:
        return "blocked_login_required"
    if value in {"blocked_permission_required", "verification_required", "risk_control"}:
        return "blocked_compliance"
    if value in {"blocked_backend_unavailable", "dokobot_tool_capability_unavailable", "provider_connection_locked"}:
        return "blocked_backend_unavailable"
    if value in {"partial_timeout", "page_timeout"}:
        return "partial_timeout" if cards_collected else "failed_provider_error"
    if value in {"failed_provider_error", "failed_malformed_output", "selector_drift", "extraction_failure"}:
        return "failed_provider_error"
    return "failed_provider_error"


def _candidate_ref(candidate: ResumeCandidate, *keys: str) -> str | None:
    if not isinstance(candidate.raw, dict):
        return None
    for key in keys:
        value = candidate.raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None
