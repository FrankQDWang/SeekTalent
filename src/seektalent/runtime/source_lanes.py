from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Literal, cast

from seektalent.models import (
    NormalizedResume,
    QueryRole,
    ResumeCandidate,
    RuntimeCandidateIdentity,
    RuntimeCanonicalResumeSelection,
    RunState,
    RuntimeFinalizationRevision,
    RuntimeIdentitySignals,
    RuntimeSourceEvidence,
)
from seektalent.progress import ProgressCallback

SourceKind = Literal["cts", "liepin"]
RuntimeSourceLaneMode = Literal["card", "detail"]
RuntimeSourceLaneStatus = Literal["completed", "blocked", "partial", "failed", "cancelled"]
RuntimeEvidenceLevel = Literal["card", "detail", "final"]
RuntimeSourceLaneEventType = Literal[
    "source_plan_created",
    "source_lane_started",
    "source_lane_completed",
    "source_lane_blocked",
    "source_lane_partial",
    "source_lane_failed",
    "source_lane_cancelled",
    "detail_recommended",
    "detail_approved",
    "detail_leased",
    "detail_completed",
    "detail_blocked",
]

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_TOKENS = {
    "access_token",
    "apikey",
    "api_key",
    "approval_secret",
    "authorization",
    "bearer",
    "cookie",
    "csrf",
    "password",
    "provider_key",
    "raw_html",
    "raw_provider_payload",
    "raw_resume",
    "secret",
    "session_secret",
    "token",
}
_SAFE_REASON_CODES = {
    "blocked_approval_missing",
    "blocked_backend_unavailable",
    "blocked_budget_exhausted",
    "blocked_compliance",
    "blocked_login_required",
    "cancelled_by_user",
    "card_rank_budget",
    "detail_enrichment_applied",
    "detail_evidence",
    "failed_internal_error",
    "failed_provider_error",
    "hard_education_mismatch",
    "hard_filter_passed",
    "hard_location_mismatch",
    "high_value_card",
    "insufficient_card_signal",
    "login_required",
    "matched_card_terms",
    "must_have_zero_overlap",
    "obvious_role_mismatch",
    "partial_budget_exhausted",
    "partial_timeout",
    "provider_rank_preserved",
    "source_card_candidate",
    "source_detail_candidate",
    "source_lanes_completed",
    "source_lanes_degraded",
    "within_run_detail_budget",
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
    "liepin_opencli_detail_not_opened",
}
_PUBLIC_SOURCE_REASON_CODE_MAP = {
    "liepin_opencli_backend_disabled": "source_browser_backend_unavailable",
    "liepin_opencli_command_missing": "source_browser_backend_unavailable",
    "liepin_opencli_extension_disconnected": "source_browser_backend_unavailable",
    "liepin_opencli_status_unavailable": "source_browser_backend_unavailable",
    "liepin_opencli_forbidden_command": "source_browser_backend_unavailable",
    "liepin_opencli_forbidden_text": "source_browser_backend_unavailable",
    "liepin_opencli_host_blocked": "source_browser_backend_unavailable",
    "liepin_opencli_start_url_blocked": "source_browser_backend_unavailable",
    "liepin_opencli_window_policy_blocked": "source_browser_backend_unavailable",
    "liepin_opencli_budget_exhausted": "blocked_budget_exhausted",
    "liepin_opencli_timeout": "source_browser_timeout",
    "liepin_opencli_login_required": "source_login_required",
    "liepin_opencli_identity_intercept": "source_login_required",
    "liepin_opencli_risk_page": "source_risk_challenge",
    "liepin_opencli_unknown_modal": "source_risk_challenge",
    "liepin_opencli_source_policy_missing": "source_browser_backend_unavailable",
    "liepin_opencli_malformed_state": "source_browser_backend_unavailable",
    "liepin_opencli_detail_not_opened": "source_browser_timeout",
}
_PUBLIC_SOURCE_REASON_CODES = {
    code for code in _SAFE_REASON_CODES if not code.startswith("liepin_opencli_")
} | {
    "source_browser_backend_unavailable",
    "source_browser_timeout",
    "source_login_required",
    "source_risk_challenge",
    "source_filter_unsupported",
    "source_filter_degraded",
    "source_location_filter_unsupported",
    "source_age_filter_unsupported",
}
_SAFE_COUNT_KEYS = {
    "cards_filtered",
    "cards_seen",
    "candidates",
    "detail_recommendations",
    "details_opened",
    "raw_candidates",
}
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?:^|[;\s])[-A-Za-z0-9_]*(?:cookie|secret|token|password|auth)=[^;\s]+", re.IGNORECASE),
)


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceBudgetPolicy:
    max_cts_pages: int = 1
    cts_page_size: int = 10
    liepin_card_page_size: int = 30
    liepin_max_cards: int = 30
    liepin_max_detail_recommendations: int = 6
    liepin_max_detail_opens_per_run: int = 4
    policy_version: str = "runtime_source_budget_v1"

    @classmethod
    def defaults(cls) -> RuntimeSourceBudgetPolicy:
        return cls()

    def to_public_payload(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "max_cts_pages": self.max_cts_pages,
            "cts_page_size": self.cts_page_size,
            "liepin_card_page_size": self.liepin_card_page_size,
            "liepin_max_cards": self.liepin_max_cards,
            "liepin_max_detail_recommendations": self.liepin_max_detail_recommendations,
            "liepin_max_detail_opens_per_run": self.liepin_max_detail_opens_per_run,
        }


DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY = RuntimeSourceBudgetPolicy.defaults()


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceLanePlan:
    source_plan_id: str
    runtime_run_id: str
    source: SourceKind
    label: str
    schema_version: Literal["runtime_source_lane_plan_v1"] = "runtime_source_lane_plan_v1"
    enabled: bool = True
    lane_mode: RuntimeSourceLaneMode = "card"
    backend_mode: str | None = None
    max_cards: int | None = None
    max_details: int | None = None
    source_budget_policy: RuntimeSourceBudgetPolicy = field(default_factory=RuntimeSourceBudgetPolicy.defaults)
    safe_posture: Mapping[str, str | int | bool | None] = field(default_factory=dict)

    def to_public_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_plan_id": self.source_plan_id,
            "runtime_run_id": self.runtime_run_id,
            "source": self.source,
            "label": self.label,
            "enabled": self.enabled,
            "lane_mode": self.lane_mode,
            "backend_mode": self.backend_mode,
            "max_cards": self.max_cards,
            "max_details": self.max_details,
            "source_budget_policy": self.source_budget_policy.to_public_payload(),
            "safe_posture": _sanitize_mapping(self.safe_posture),
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceLaneEvent:
    schema_version: Literal["runtime_source_lane_event_v1"]
    runtime_run_id: str
    source_plan_id: str
    source_lane_run_id: str
    source: SourceKind
    attempt: int
    event_seq: int
    event_type: RuntimeSourceLaneEventType
    status: RuntimeSourceLaneStatus | None = None
    safe_counts: Mapping[str, int] = field(default_factory=dict)
    safe_reason_code: str | None = None
    artifact_refs: tuple[str, ...] = ()

    def to_public_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runtime_run_id": self.runtime_run_id,
            "source_plan_id": self.source_plan_id,
            "source_lane_run_id": self.source_lane_run_id,
            "source": self.source,
            "attempt": self.attempt,
            "event_seq": self.event_seq,
            "event_type": self.event_type,
            "status": self.status,
            "safe_counts": _sanitize_count_mapping(self.safe_counts),
            "safe_reason_code": _sanitize_reason_code(self.safe_reason_code),
            "artifact_refs": [ref for ref in (_sanitize_artifact_ref(ref) for ref in self.artifact_refs) if ref],
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeDetailRecommendation:
    recommendation_id: str
    source: SourceKind
    source_evidence_id: str
    candidate_resume_id: str
    provider_candidate_key_hash: str
    evidence_level: RuntimeEvidenceLevel = "card"
    value_score: int | None = None
    provider_rank: int | None = None
    card_policy_rank: int | None = None
    hard_filter_status: str | None = None
    budget_reason_code: str | None = None
    reason_code: str | None = None
    safe_reason: str | None = None
    safe_reason_codes: tuple[str, ...] = ()
    provider_snapshot_ref: str | None = None
    safe_summary_ref: str | None = None
    budget_policy_version: str | None = None
    expires_at: str | None = None

    def to_public_payload(self) -> dict[str, object]:
        return {
            "recommendation_id": self.recommendation_id,
            "source": self.source,
            "source_evidence_id": self.source_evidence_id,
            "candidate_resume_id": self.candidate_resume_id,
            "provider_candidate_key_hash": self.provider_candidate_key_hash,
            "evidence_level": self.evidence_level,
            "value_score": self.value_score,
            "provider_rank": self.provider_rank,
            "card_policy_rank": self.card_policy_rank,
            "hard_filter_status": _sanitize_reason_code(self.hard_filter_status),
            "budget_reason_code": _sanitize_reason_code(self.budget_reason_code),
            "reason_code": _sanitize_reason_code(self.reason_code),
            "safe_reason_codes": [_sanitize_reason_code(value) for value in self.safe_reason_codes],
            "provider_snapshot_ref": _sanitize_artifact_ref(self.provider_snapshot_ref),
            "safe_summary_ref": _sanitize_artifact_ref(self.safe_summary_ref),
            "budget_policy_version": self.budget_policy_version,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeApprovedDetailLease:
    lease_ref: str
    lease_id: str | None = None
    runtime_run_id: str | None = None
    source_plan_id: str | None = None
    source_lane_run_id: str | None = None
    source: SourceKind = "liepin"
    recommendation_id: str | None = None
    source_evidence_id: str | None = None
    request_id: str
    ledger_id: str
    candidate_evidence_id: str
    candidate_resume_id: str | None = None
    provider_candidate_key_hash: str
    approved_by_actor_hash: str | None = None
    approved_at: str | None = None
    budget_policy_hash: str | None = None
    lease_signature_ref: str | None = None
    connection_id: str
    compliance_gate_ref: str
    provider_account_hash: str
    detail_candidates_json: str
    daily_budget: int
    budget_date: str
    provider_day_key: str
    timezone: str
    open_policy_version: str
    already_opened_provider_ids_json: str = "[]"
    already_seen_weak_fingerprints_json: str = "[]"
    score_metadata_json: str = "{}"
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if self.source != "liepin":
            raise ValueError("RuntimeApprovedDetailLease currently supports only liepin.")
        if self.source_evidence_id is not None and self.source_evidence_id != self.candidate_evidence_id:
            raise ValueError("source_evidence_id and candidate_evidence_id must match during migration.")

    def to_public_payload(self) -> dict[str, object]:
        return {
            "lease_ref": _sanitize_text(self.lease_ref),
            "lease_id": _sanitize_text(self.lease_id),
            "runtime_run_id": self.runtime_run_id,
            "source_plan_id": self.source_plan_id,
            "source_lane_run_id": self.source_lane_run_id,
            "source": self.source,
            "recommendation_id": self.recommendation_id,
            "source_evidence_id": self.source_evidence_id or self.candidate_evidence_id,
            "request_id": _sanitize_text(self.request_id),
            "ledger_id": _sanitize_text(self.ledger_id),
            "candidate_evidence_id": _sanitize_text(self.candidate_evidence_id),
            "candidate_resume_id": self.candidate_resume_id,
            "provider_candidate_key_hash": self.provider_candidate_key_hash,
            "connection_id": _sanitize_text(self.connection_id),
            "compliance_gate_ref": _sanitize_text(self.compliance_gate_ref),
            "detail_candidate_count": _json_list_count(self.detail_candidates_json),
            "daily_budget": self.daily_budget,
            "budget_date": self.budget_date,
            "budget_policy_hash": self.budget_policy_hash,
            "provider_day_key": _sanitize_text(self.provider_day_key),
            "timezone": self.timezone,
            "open_policy_version": _sanitize_text(self.open_policy_version),
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceLaneResult:
    runtime_run_id: str
    source_plan_id: str
    source_lane_run_id: str
    source: SourceKind
    lane_mode: RuntimeSourceLaneMode
    attempt: int
    status: RuntimeSourceLaneStatus
    schema_version: Literal["runtime_source_lane_result_v1"] = "runtime_source_lane_result_v1"
    candidate_store_updates: dict[str, ResumeCandidate] = field(default_factory=dict)
    normalized_store_updates: dict[str, NormalizedResume] = field(default_factory=dict)
    source_evidence_updates: tuple[RuntimeSourceEvidence, ...] = ()
    provider_snapshots: tuple[Any, ...] = ()
    raw_candidate_count: int | None = None
    provider_snapshot_refs: tuple[str, ...] = ()
    safe_summary_refs: tuple[str, ...] = ()
    detail_recommendations: tuple[RuntimeDetailRecommendation, ...] = ()
    events: tuple[RuntimeSourceLaneEvent, ...] = ()
    blocked_reason_code: str | None = None
    stop_reason_code: str | None = None
    retryable: bool = False
    safe_error_summary: str | None = None
    error_ref: str | None = None

    def to_public_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runtime_run_id": self.runtime_run_id,
            "source_plan_id": self.source_plan_id,
            "source_lane_run_id": self.source_lane_run_id,
            "source": self.source,
            "lane_mode": self.lane_mode,
            "attempt": self.attempt,
            "status": self.status,
            "candidate_count": len(self.candidate_store_updates),
            "source_evidence_count": len(self.source_evidence_updates),
            "provider_snapshot_count": len(self.provider_snapshots),
            "raw_candidate_count": self.raw_candidate_count,
            "detail_recommendation_count": len(self.detail_recommendations),
            "provider_snapshot_refs": [ref for ref in (_sanitize_artifact_ref(ref) for ref in self.provider_snapshot_refs) if ref],
            "safe_summary_refs": [ref for ref in (_sanitize_artifact_ref(ref) for ref in self.safe_summary_refs) if ref],
            "detail_recommendations": [item.to_public_payload() for item in self.detail_recommendations],
            "events": [event.to_public_payload() for event in self.events],
            "blocked_reason_code": _sanitize_reason_code(self.blocked_reason_code),
            "stop_reason_code": _sanitize_reason_code(self.stop_reason_code),
            "retryable": self.retryable,
            "safe_error_summary": _sanitize_text(self.safe_error_summary),
            "error_ref": _sanitize_artifact_ref(self.error_ref),
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceLaneRequest:
    source: SourceKind
    lane_mode: RuntimeSourceLaneMode
    job_title: str
    jd: str
    notes: str | None
    runtime_run_id: str | None = None
    source_plan_id: str | None = None
    source_lane_run_id: str | None = None
    attempt: int = 1
    source_query_terms: tuple[str, ...] = ()
    logical_query_instance_id: str | None = None
    logical_query_fingerprint: str | None = None
    logical_query_role: QueryRole | None = None
    logical_keyword_query: str | None = None
    logical_requested_count: int | None = None
    logical_provider_scan_limit: int | None = None
    logical_unsupported_filter_reason_codes: tuple[str, ...] = ()
    liepin_context: Mapping[str, str | int | bool | None] | None = None
    source_budget_policy: RuntimeSourceBudgetPolicy = field(default_factory=RuntimeSourceBudgetPolicy.defaults)
    approved_detail_lease_ref: str | None = None
    approved_detail_lease: RuntimeApprovedDetailLease | None = None
    progress_callback: ProgressCallback | None = None

    def to_public_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "lane_mode": self.lane_mode,
            "runtime_run_id": self.runtime_run_id,
            "source_plan_id": self.source_plan_id,
            "source_lane_run_id": self.source_lane_run_id,
            "attempt": self.attempt,
            "source_query_term_count": len(self.source_query_terms),
            "logical_query_count": 1 if self.logical_query_instance_id else 0,
            "logical_requested_count": self.logical_requested_count,
            "logical_provider_scan_limit": self.logical_provider_scan_limit,
            "logical_unsupported_filter_reason_codes": list(self.logical_unsupported_filter_reason_codes),
            "source_budget_policy": self.source_budget_policy.to_public_payload(),
            "liepin_context": _sanitize_mapping(self.liepin_context or {}),
            "approved_detail_lease_ref": _sanitize_text(
                self.approved_detail_lease.lease_ref if self.approved_detail_lease is not None else self.approved_detail_lease_ref
            ),
            "approved_detail_lease": (
                self.approved_detail_lease.to_public_payload() if self.approved_detail_lease is not None else None
            ),
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeDetailEnrichmentResult:
    runtime_run_id: str
    base_finalization_revision: int
    lane_result: RuntimeSourceLaneResult
    finalization_revision: RuntimeFinalizationRevision
    schema_version: Literal["runtime_detail_enrichment_result_v1"] = "runtime_detail_enrichment_result_v1"

    def to_public_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runtime_run_id": self.runtime_run_id,
            "base_finalization_revision": self.base_finalization_revision,
            "lane_result": self.lane_result.to_public_payload(),
            "finalization_revision": self.finalization_revision.to_public_payload(),
        }


def normalize_source_kinds(source_kinds: Sequence[str] | None) -> tuple[SourceKind, ...]:
    if not source_kinds:
        return ("cts",)

    normalized: list[SourceKind] = []
    for source in source_kinds:
        if source not in {"cts", "liepin"}:
            raise ValueError(f"Unsupported runtime source: {source}")
        if source in normalized:
            raise ValueError(f"Duplicate runtime source: {source}")
        normalized.append(cast(SourceKind, source))
    return tuple(normalized)


def build_runtime_source_plan(
    *,
    source_kinds: Sequence[str] | None,
    settings: Any,
    runtime_run_id: str,
    liepin_context: Mapping[str, str | int | bool | None] | None = None,
) -> tuple[RuntimeSourceLanePlan, ...]:
    plans: list[RuntimeSourceLanePlan] = []
    for index, source in enumerate(normalize_source_kinds(source_kinds)):
        if source == "cts":
            plans.append(
                RuntimeSourceLanePlan(
                    source_plan_id=f"{runtime_run_id}:source:{index}:cts",
                    runtime_run_id=runtime_run_id,
                    source="cts",
                    label="CTS",
                    backend_mode="api",
                    max_cards=RuntimeSourceBudgetPolicy.defaults().cts_page_size,
                )
            )
            continue

        worker_mode = str(getattr(settings, "liepin_worker_mode", "disabled"))
        if worker_mode == "disabled":
            backend_mode = "blocked"
        elif worker_mode == "pi_agent":
            backend_mode = "pi_agent"
        elif worker_mode == "fake_fixture":
            backend_mode = "fake_fixture"
        else:
            backend_mode = "worker_compat"
        safe_posture = {"worker_mode": worker_mode, **dict(liepin_context or {})}
        plans.append(
            RuntimeSourceLanePlan(
                source_plan_id=f"{runtime_run_id}:source:{index}:liepin",
                runtime_run_id=runtime_run_id,
                source="liepin",
                label="Liepin",
                backend_mode=backend_mode,
                max_cards=RuntimeSourceBudgetPolicy.defaults().liepin_max_cards,
                max_details=RuntimeSourceBudgetPolicy.defaults().liepin_max_detail_recommendations,
                safe_posture=safe_posture,
            )
        )
    return tuple(plans)


def apply_source_lane_result(
    *,
    run_state: RunState,
    result: RuntimeSourceLaneResult,
    source_order: Mapping[SourceKind, int],
) -> None:
    merge_source_lane_result_updates(run_state=run_state, result=result, source_order=source_order)


def merge_source_lane_result_updates(
    *,
    run_state: RunState,
    result: RuntimeSourceLaneResult,
    source_order: Mapping[SourceKind, int],
    rebuild_identity: bool = True,
) -> None:
    _append_source_lane_public_payload_once(run_state, result)
    if result.status == "blocked":
        return

    for resume_id, candidate in result.candidate_store_updates.items():
        run_state.candidate_store[resume_id] = candidate
        if resume_id not in run_state.seen_resume_ids:
            run_state.seen_resume_ids.append(resume_id)

    run_state.normalized_store.update(result.normalized_store_updates)
    append_source_evidence_once(
        run_state,
        result.source_evidence_updates,
        source_order=source_order,
    )
    if rebuild_identity:
        _rebuild_identity_state(run_state, source_order=source_order)


def rebuild_candidate_identities(
    run_state: RunState,
    *,
    source_order: Mapping[SourceKind, int],
) -> None:
    _rebuild_identity_state(run_state, source_order=source_order)


def clone_run_state_for_source_lane(run_state: RunState) -> RunState:
    return run_state.model_copy(
        deep=True,
        update={
            "seen_resume_ids": [],
            "candidate_store": {},
            "normalized_store": {},
            "source_evidence_by_resume_id": {},
            "source_evidence_by_identity_id": {},
            "candidate_identity_by_resume_id": {},
            "candidate_identities": {},
            "identity_aliases_by_canonical_id": {},
            "identity_conflicts": [],
            "canonical_resume_by_identity_id": {},
            "source_coverage_summary": None,
            "finalization_revisions": [],
            "runtime_source_lane_results": [],
            "scorecards_by_resume_id": {},
            "top_pool_ids": [],
            "round_history": [],
        },
    )


def _append_source_lane_public_payload_once(run_state: RunState, result: RuntimeSourceLaneResult) -> None:
    payload = result.to_public_payload()
    existing_keys = {
        (
            str(item.get("runtime_run_id") or ""),
            str(item.get("source") or ""),
            str(item.get("source_lane_run_id") or ""),
            _payload_int(item.get("attempt")),
        )
        for item in run_state.runtime_source_lane_results
        if isinstance(item, dict)
    }
    key = (
        str(payload.get("runtime_run_id") or ""),
        str(payload.get("source") or ""),
        str(payload.get("source_lane_run_id") or ""),
        _payload_int(payload.get("attempt")),
    )
    if key not in existing_keys:
        run_state.runtime_source_lane_results.append(payload)


def _payload_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def append_source_evidence_once(
    run_state: RunState,
    evidence_updates: tuple[RuntimeSourceEvidence, ...],
    *,
    source_order: Mapping[SourceKind, int],
) -> None:
    for evidence in evidence_updates:
        entries = run_state.source_evidence_by_resume_id.setdefault(evidence.candidate_resume_id, [])
        if any(item.evidence_id == evidence.evidence_id for item in entries):
            continue
        entries.append(evidence)
        entries.sort(key=lambda item: _evidence_sort_key(item, source_order))


def _rebuild_identity_state(
    run_state: RunState,
    *,
    source_order: Mapping[SourceKind, int],
) -> None:
    index = RuntimeCandidateIdentityIndex()
    candidate_identity_by_resume_id: dict[str, str] = {}
    source_evidence_by_identity_id: dict[str, list[RuntimeSourceEvidence]] = {}

    for resume_id in run_state.seen_resume_ids:
        if resume_id not in run_state.candidate_store:
            continue
        candidate = run_state.candidate_store[resume_id]
        evidence_items = run_state.source_evidence_by_resume_id.get(resume_id, [])
        if not evidence_items:
            identity = index.upsert_candidate(
                resume_id=resume_id,
                evidence_id=f"candidate:{resume_id}",
                signals=_identity_signals_for_candidate(candidate=candidate, normalized=run_state.normalized_store.get(resume_id)),
            )
            candidate_identity_by_resume_id[resume_id] = identity.identity_id
            continue

        identity_id: str | None = None
        for evidence in evidence_items:
            identity = index.upsert_candidate(
                resume_id=resume_id,
                evidence_id=evidence.evidence_id,
                signals=_identity_signals_for_candidate(
                    candidate=candidate,
                    normalized=run_state.normalized_store.get(resume_id),
                    evidence=evidence,
                ),
            )
            identity_id = identity.identity_id
        if identity_id is not None:
            candidate_identity_by_resume_id[resume_id] = identity_id

    identities = index.identities()
    aliases_by_canonical_id = index.aliases_by_canonical_id()
    for resume_id, identity_id in candidate_identity_by_resume_id.items():
        source_evidence_by_identity_id.setdefault(identity_id, []).extend(
            run_state.source_evidence_by_resume_id.get(resume_id, [])
        )
    for identity_id, evidence_items in source_evidence_by_identity_id.items():
        unique: dict[str, RuntimeSourceEvidence] = {item.evidence_id: item for item in evidence_items}
        source_evidence_by_identity_id[identity_id] = sorted(
            unique.values(),
            key=lambda item: _evidence_sort_key(item, source_order),
        )

    run_state.candidate_identities = identities
    run_state.identity_aliases_by_canonical_id = aliases_by_canonical_id
    run_state.candidate_identity_by_resume_id = candidate_identity_by_resume_id
    run_state.source_evidence_by_identity_id = source_evidence_by_identity_id
    run_state.canonical_resume_by_identity_id = {
        identity_id: choose_canonical_resume_for_identity(
            identity_id=identity_id,
            resume_ids=identity.resume_ids,
            candidates=run_state.candidate_store,
            normalized_store=run_state.normalized_store,
            evidence=source_evidence_by_identity_id.get(identity_id, []),
        )
        for identity_id, identity in identities.items()
        if identity.resume_ids
    }


class RuntimeCandidateIdentityIndex:
    def __init__(self, identities: Mapping[str, RuntimeCandidateIdentity] | None = None) -> None:
        self._identities: dict[str, RuntimeCandidateIdentity] = dict(identities or {})
        self._key_to_identity_id: dict[str, str] = {}
        self._aliases_by_canonical_id: dict[str, set[str]] = {
            identity_id: set(identity.alias_identity_ids)
            for identity_id, identity in self._identities.items()
        }

    def upsert_candidate(
        self,
        *,
        resume_id: str,
        evidence_id: str,
        signals: RuntimeIdentitySignals,
    ) -> RuntimeCandidateIdentity:
        keys = _identity_keys(signals=signals, evidence_id=evidence_id)
        primary_key = _primary_identity_key(signals=signals, evidence_id=evidence_id)
        target_identity_id = _stable_identity_id(primary_key)
        existing_identity_ids = {self._key_to_identity_id[key] for key in keys if key in self._key_to_identity_id}
        existing_identity_ids.update(
            identity_id
            for identity_id, identity in self._identities.items()
            if resume_id in identity.resume_ids or evidence_id in identity.evidence_ids
        )
        if not signals.protected_contact_hashes and existing_identity_ids:
            target_identity_id = sorted(existing_identity_ids)[0]

        self._ensure_identity(target_identity_id, strongest_signal=_strongest_signal_code(signals))
        for old_identity_id in sorted(existing_identity_ids):
            if old_identity_id != target_identity_id:
                self._merge_identity(old_identity_id=old_identity_id, target_identity_id=target_identity_id)

        identity = self._identities[target_identity_id]
        resume_ids = _append_sorted_once(identity.resume_ids, resume_id)
        evidence_ids = _append_sorted_once(identity.evidence_ids, evidence_id)
        aliases = sorted(self._aliases_by_canonical_id.get(target_identity_id, set()))
        updated = identity.model_copy(
            update={
                "resume_ids": resume_ids,
                "evidence_ids": evidence_ids,
                "alias_identity_ids": aliases,
                "strongest_signal": _strongest_signal_code(signals),
            }
        )
        self._identities[target_identity_id] = updated
        for key in keys:
            self._key_to_identity_id[key] = target_identity_id
        return updated

    def aliases_for(self, canonical_identity_id: str) -> tuple[str, ...]:
        aliases = set(self._aliases_by_canonical_id.get(canonical_identity_id, set()))
        aliases.add(canonical_identity_id)
        return tuple(sorted(aliases))

    def identity_for_resume_id(self, resume_id: str) -> str | None:
        for identity in self._identities.values():
            if resume_id in identity.resume_ids:
                return identity.canonical_identity_id
        return None

    def identities(self) -> dict[str, RuntimeCandidateIdentity]:
        return dict(self._identities)

    def aliases_by_canonical_id(self) -> dict[str, list[str]]:
        return {identity_id: sorted(aliases | {identity_id}) for identity_id, aliases in self._aliases_by_canonical_id.items()}

    def _ensure_identity(self, identity_id: str, *, strongest_signal: str | None) -> None:
        if identity_id in self._identities:
            return
        self._identities[identity_id] = RuntimeCandidateIdentity(
            identity_id=identity_id,
            canonical_identity_id=identity_id,
            strongest_signal=strongest_signal,
        )
        self._aliases_by_canonical_id.setdefault(identity_id, {identity_id})

    def _merge_identity(self, *, old_identity_id: str, target_identity_id: str) -> None:
        old_identity = self._identities.pop(old_identity_id, None)
        if old_identity is None:
            return
        target_identity = self._identities[target_identity_id]
        self._aliases_by_canonical_id.setdefault(target_identity_id, {target_identity_id}).update(
            self._aliases_by_canonical_id.pop(old_identity_id, {old_identity_id})
        )
        self._aliases_by_canonical_id[target_identity_id].add(old_identity_id)
        self._identities[target_identity_id] = target_identity.model_copy(
            update={
                "resume_ids": sorted(set(target_identity.resume_ids) | set(old_identity.resume_ids)),
                "evidence_ids": sorted(set(target_identity.evidence_ids) | set(old_identity.evidence_ids)),
                "alias_identity_ids": sorted(self._aliases_by_canonical_id[target_identity_id]),
            }
        )
        for key, identity_id in list(self._key_to_identity_id.items()):
            if identity_id == old_identity_id:
                self._key_to_identity_id[key] = target_identity_id


def choose_canonical_resume_for_identity(
    *,
    identity_id: str,
    resume_ids: Sequence[str],
    candidates: Mapping[str, ResumeCandidate],
    normalized_store: Mapping[str, NormalizedResume],
    evidence: Sequence[RuntimeSourceEvidence],
) -> RuntimeCanonicalResumeSelection:
    evidence_by_resume_id: dict[str, list[RuntimeSourceEvidence]] = {}
    for item in evidence:
        evidence_by_resume_id.setdefault(item.candidate_resume_id, []).append(item)

    def sort_key(resume_id: str) -> tuple[int, str, int, int, int, str]:
        resume_evidence = evidence_by_resume_id.get(resume_id, [])
        best_level = 1 if any(item.evidence_level == "detail" for item in resume_evidence) else 0
        newest_collected_at = max((item.collected_at for item in resume_evidence), default="")
        normalized = normalized_store.get(resume_id)
        completeness = normalized.completeness_score if normalized is not None else 0
        source_trust = max((_source_trust(item.source) for item in resume_evidence), default=0)
        provider_rank = min((item.provider_rank for item in resume_evidence if item.provider_rank is not None), default=9999)
        return (best_level, newest_collected_at, completeness, source_trust, -provider_rank, resume_id)

    selected_resume_id = max(resume_ids, key=sort_key)
    selected_evidence = sorted(
        evidence_by_resume_id.get(selected_resume_id, []),
        key=lambda item: (1 if item.evidence_level == "detail" else 0, item.collected_at, item.evidence_id),
        reverse=True,
    )
    reason_codes = ["detail_evidence"] if selected_evidence and selected_evidence[0].evidence_level == "detail" else [
        "provider_rank_preserved"
    ]
    return RuntimeCanonicalResumeSelection(
        identity_id=identity_id,
        canonical_resume_id=selected_resume_id,
        selected_evidence_id=selected_evidence[0].evidence_id if selected_evidence else None,
        selected_at=selected_evidence[0].collected_at if selected_evidence else None,
        safe_reason_codes=tuple(reason_codes),
    )


def _evidence_sort_key(
    evidence: RuntimeSourceEvidence,
    source_order: Mapping[SourceKind, int],
) -> tuple[int, int, str, str]:
    level_order = {"card": 0, "detail": 1, "final": 2}
    source_index = source_order.get(evidence.source, 999)
    return (
        source_index,
        level_order.get(evidence.evidence_level, 999),
        evidence.collected_at,
        evidence.evidence_id,
    )


def _identity_keys(*, signals: RuntimeIdentitySignals, evidence_id: str) -> tuple[str, ...]:
    keys: list[str] = []
    for contact_hash in sorted(signals.protected_contact_hashes):
        keys.append(f"contact:{contact_hash}")
    if signals.provider_candidate_key_hash:
        keys.append(f"provider:{signals.provider_candidate_key_hash}")
    if not keys and not signals.is_masked_name and signals.normalized_name:
        distinctive_parts = [
            signals.normalized_name,
            signals.current_company_norm or "",
            signals.current_title_norm or "",
            "|".join(signals.school_norms),
            "|".join(signals.work_chronology_fingerprints),
        ]
        if any(distinctive_parts[1:]):
            keys.append("identity-fields:" + hashlib.sha256("||".join(distinctive_parts).encode("utf-8")).hexdigest())
    return tuple(keys or [f"evidence:{evidence_id}"])


def _primary_identity_key(*, signals: RuntimeIdentitySignals, evidence_id: str) -> str:
    if signals.protected_contact_hashes:
        return f"contact:{sorted(signals.protected_contact_hashes)[0]}"
    if signals.provider_candidate_key_hash:
        return f"provider:{signals.provider_candidate_key_hash}"
    return _identity_keys(signals=signals, evidence_id=evidence_id)[0]


def _stable_identity_id(identity_key: str) -> str:
    return "identity-" + hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:16]


def _strongest_signal_code(signals: RuntimeIdentitySignals) -> str:
    if signals.protected_contact_hashes:
        return "protected_contact_hash"
    if signals.provider_candidate_key_hash:
        return "provider_candidate_key_hash"
    if signals.is_masked_name:
        return "masked_name_only"
    return "normalized_identity_fields"


def _append_sorted_once(values: Sequence[str], value: str) -> list[str]:
    return sorted(set(values) | {value})


def _source_trust(source: SourceKind | str) -> int:
    return {"liepin": 2, "cts": 1}.get(str(source), 0)


def _identity_signals_for_candidate(
    *,
    candidate: ResumeCandidate,
    normalized: NormalizedResume | None,
    evidence: RuntimeSourceEvidence | None = None,
) -> RuntimeIdentitySignals:
    name = normalized.candidate_name.strip() if normalized and normalized.candidate_name else None
    current_company = normalized.current_company.strip() if normalized and normalized.current_company else None
    current_title = normalized.current_title.strip() if normalized and normalized.current_title else None
    school_norms: tuple[str, ...] = ()
    if normalized and normalized.education_summary:
        school_norms = tuple(_normalize_identity_text(part) for part in [normalized.education_summary] if part.strip())
    chronology: list[str] = []
    if normalized:
        for item in normalized.recent_experiences:
            fingerprint = ":".join(
                part
                for part in (
                    _normalize_identity_text(item.company),
                    _normalize_identity_text(item.title),
                    _normalize_identity_text(item.duration),
                )
                if part
            )
            if fingerprint:
                chronology.append(fingerprint)
    if not chronology:
        for summary in candidate.work_experience_summaries:
            text = _normalize_identity_text(summary)
            if text:
                chronology.append(text)
    return RuntimeIdentitySignals(
        normalized_name=_normalize_identity_text(name) if name else None,
        is_masked_name=_is_masked_identity_name(name),
        current_company_norm=_normalize_identity_text(current_company) if current_company else None,
        current_title_norm=_normalize_identity_text(current_title) if current_title else None,
        school_norms=school_norms,
        work_chronology_fingerprints=tuple(sorted(set(chronology))),
        provider_candidate_key_hash=evidence.provider_candidate_key_hash if evidence else None,
        protected_contact_hashes=evidence.protected_contact_hashes if evidence else (),
    )


def _normalize_identity_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _is_masked_identity_name(value: str | None) -> bool:
    if value is None:
        return False
    text = value.strip()
    if not text:
        return True
    lowered = text.casefold()
    if lowered in {"匿名", "候选人", "candidate", "-", "--"}:
        return True
    if "*" in text or "某" in text or "女士" in text or "先生" in text:
        return True
    if re.fullmatch(r"候选人\d+", text):
        return True
    return False


def _sanitize_mapping(values: Mapping[str, str | int | bool | None]) -> dict[str, str | int | bool | None]:
    safe: dict[str, str | int | bool | None] = {}
    for key, value in values.items():
        if _is_sensitive_key(key):
            continue
        if isinstance(value, str):
            safe[key] = _sanitize_text(value)
        else:
            safe[key] = value
    return safe


def _sanitize_count_mapping(values: Mapping[str, int]) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key, value in values.items():
        if key not in _SAFE_COUNT_KEYS:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        safe[key] = value
    return safe


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    if _is_sensitive_value(value):
        return _REDACTED
    return value


def _sanitize_reason_code(value: str | None) -> str | None:
    if value is None:
        return None
    public_code = _PUBLIC_SOURCE_REASON_CODE_MAP.get(value, value)
    return public_code if public_code in _PUBLIC_SOURCE_REASON_CODES else "unknown_reason"


def _sanitize_artifact_ref(value: str | None) -> str | None:
    text = _sanitize_text(value)
    if text is None or text == _REDACTED:
        return None
    return text if text.startswith("artifact://") else None


def _is_sensitive_key(value: str) -> bool:
    compact = "".join(character for character in value.casefold() if character.isalnum() or character == "_")
    return any(token in compact for token in _SENSITIVE_KEY_TOKENS)


def _is_sensitive_value(value: str) -> bool:
    lowered = value.casefold()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS) or any(
        pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS
    )


def _json_list_count(value: str) -> int:
    try:
        decoded = json.loads(value)
    except Exception:  # noqa: BLE001
        return 0
    return len(decoded) if isinstance(decoded, list) else 0
