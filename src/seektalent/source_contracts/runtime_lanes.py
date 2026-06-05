from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import re
from typing import TYPE_CHECKING, Literal

from seektalent.models import (
    NormalizedResume,
    QueryRole,
    ResumeCandidate,
    RuntimeFinalizationRevision,
    RuntimeSourceEvidence,
)
from seektalent.progress import ProgressCallback

if TYPE_CHECKING:
    from seektalent.models import RequirementSheet


SourceKind = str
RuntimeSourceLaneMode = Literal["card", "detail"]
RuntimeSourceLaneStatus = Literal["running", "completed", "blocked", "partial", "failed", "cancelled"]
RuntimeEvidenceLevel = Literal["card", "detail", "final"]
RuntimeSourceLaneEventType = Literal[
    "source_plan_created",
    "source_lane_started",
    "source_lane_completed",
    "source_lane_blocked",
    "source_lane_partial",
    "source_lane_failed",
    "source_lane_cancelled",
    "source_workflow_step_started",
    "source_workflow_step_completed",
    "source_workflow_step_failed",
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
    "source_browser_backend_unavailable",
    "source_browser_timeout",
    "source_card_candidate",
    "source_detail_candidate",
    "source_filter_degraded",
    "source_age_filter_unsupported",
    "source_filter_unavailable",
    "source_filter_unsupported",
    "source_location_filter_unsupported",
    "source_lanes_completed",
    "source_lanes_degraded",
    "source_login_required",
    "source_risk_challenge",
    "within_run_detail_budget",
}
_SAFE_COUNT_KEYS = {
    "cards_filtered",
    "cards_seen",
    "candidates",
    "detail_recommendations",
    "details_opened",
    "visible_cards",
    "target_resumes",
    "resumes_returned",
    "cached_detail_urls",
    "closed_tabs",
    "raw_candidates",
}
_SAFE_WORKFLOW_STEP_NAMES = {
    "prepare_search",
    "apply_filters",
    "submit_search",
    "observe_cards",
    "cache_detail_urls",
    "open_detail",
    "capture_detail",
    "cleanup_detail_tabs",
    "finalize",
}
_SAFE_METADATA_KEYS = {
    "rank",
    "open_mode",
}
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?:^|[;\s])[-A-Za-z0-9_]*(?:cookie|secret|token|password|auth)=[^;\s]+", re.IGNORECASE),
)


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceBudgetPolicy:
    card_target: int = 10
    detail_target: int = 0
    scan_limit: int = 10
    page_size: int = 30
    max_pages: int = 1
    max_cards: int = 30
    max_details: int = 6
    max_detail_recommendations: int = 6
    max_detail_opens_per_run: int = 4
    policy_version: str = "runtime_source_budget_v1"

    @classmethod
    def defaults(cls) -> RuntimeSourceBudgetPolicy:
        return cls()

    def __post_init__(self) -> None:
        for field_name in (
            "card_target",
            "detail_target",
            "scan_limit",
            "page_size",
            "max_pages",
            "max_cards",
            "max_details",
            "max_detail_recommendations",
            "max_detail_opens_per_run",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"runtime_source_budget_negative_{field_name}")

    def to_public_payload(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "card_target": self.card_target,
            "detail_target": self.detail_target,
            "scan_limit": self.scan_limit,
            "page_size": self.page_size,
            "max_pages": self.max_pages,
            "max_cards": self.max_cards,
            "max_details": self.max_details,
            "max_detail_recommendations": self.max_detail_recommendations,
            "max_detail_opens_per_run": self.max_detail_opens_per_run,
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
    step_name: str | None = None
    safe_metadata: Mapping[str, str | int | bool | None] = field(default_factory=dict)

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
            "artifact_refs": [
                ref for ref in (_sanitize_protected_artifact_ref(ref) for ref in self.artifact_refs) if ref
            ],
            "step_name": _sanitize_step_name(self.step_name),
            "safe_metadata": _sanitize_safe_metadata(self.safe_metadata),
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeDetailRecommendation:
    recommendation_id: str
    source: SourceKind
    source_evidence_id: str
    candidate_resume_id: str
    provider_candidate_key_hash: str
    source_lane_run_id: str | None = None
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
            "source_lane_run_id": self.source_lane_run_id,
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
    source: SourceKind
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
    provider_snapshots: tuple[object, ...] = ()
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
    requirement_sheet: "RequirementSheet"
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
    source_context: Mapping[str, str | int | bool | None] | object | None = None
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
            "requirement_sheet": {
                "job_title": self.requirement_sheet.job_title,
                "must_have_count": len(self.requirement_sheet.must_have_capabilities),
                "preferred_count": len(self.requirement_sheet.preferred_capabilities),
                "exclusion_count": len(self.requirement_sheet.exclusion_signals),
            },
            "source_budget_policy": self.source_budget_policy.to_public_payload(),
            "source_context": _safe_context_payload(self.source_context),
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


def _safe_context_payload(value: object) -> dict[str, str | bool]:
    to_safe_posture = getattr(value, "to_safe_posture", None)
    if callable(to_safe_posture):
        payload = to_safe_posture()
        if isinstance(payload, Mapping):
            return {
                str(key): cast_value
                for key, item in payload.items()
                if isinstance(key, str) and not _is_sensitive_key(key) and (cast_value := _safe_context_value(item)) is not None
            }
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): cast_value
        for key, item in value.items()
        if isinstance(key, str) and not _is_sensitive_key(key) and (cast_value := _safe_context_value(item)) is not None
    }


def _safe_context_value(value: object) -> str | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text if text and not _is_sensitive_value(text) else None
    return None


def _sanitize_mapping(values: Mapping[str, str | int | bool | None]) -> dict[str, str | int | bool | None]:
    sanitized: dict[str, str | int | bool | None] = {}
    for key, value in values.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            sanitized[key_text] = _REDACTED
        elif isinstance(value, str) and _is_sensitive_value(value):
            sanitized[key_text] = _REDACTED
        else:
            sanitized[key_text] = value
    return sanitized


def _sanitize_count_mapping(values: Mapping[str, int]) -> dict[str, int]:
    sanitized: dict[str, int] = {}
    for key, value in values.items():
        if key not in _SAFE_COUNT_KEYS:
            continue
        if not isinstance(value, int):
            continue
        if value < 0:
            continue
        sanitized[key] = value
    return sanitized


def _sanitize_step_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text if text in _SAFE_WORKFLOW_STEP_NAMES else None


def _sanitize_safe_metadata(values: Mapping[str, str | int | bool | None]) -> dict[str, str | int | bool]:
    sanitized: dict[str, str | int | bool] = {}
    for key, value in values.items():
        if key not in _SAFE_METADATA_KEYS:
            continue
        if isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, int):
            sanitized[key] = value
        elif isinstance(value, str):
            text = value.strip()
            if text and not _is_sensitive_value(text):
                sanitized[key] = text
    return sanitized


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _REDACTED if _is_sensitive_value(text) else text


def _sanitize_reason_code(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value in _SAFE_REASON_CODES else "unknown_reason"


def _sanitize_artifact_ref(value: str | None) -> str | None:
    text = _sanitize_text(value)
    if text is None or text == _REDACTED:
        return None
    return text if text.startswith(("artifact://", "corpus://")) else None


def _sanitize_protected_artifact_ref(value: str | None) -> str | None:
    text = _sanitize_text(value)
    if text is None or text == _REDACTED:
        return None
    return text if text.startswith(("artifact://protected/", "corpus://protected/", "protected://")) else None


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS)


def _is_sensitive_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS)


def _json_list_count(value: str) -> int:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return 0
    return len(decoded) if isinstance(decoded, list) else 0
