from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal

from seektalent.models import NormalizedResume, ResumeCandidate, RunState, RuntimeSourceEvidence
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
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?:^|[;\s])[-A-Za-z0-9_]*(?:cookie|secret|token|password|auth)=[^;\s]+", re.IGNORECASE),
)


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
            "safe_counts": dict(self.safe_counts),
            "safe_reason_code": _sanitize_text(self.safe_reason_code),
            "artifact_refs": [_sanitize_text(ref) for ref in self.artifact_refs],
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
    reason_code: str | None = None
    safe_reason: str | None = None
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
            "reason_code": _sanitize_text(self.reason_code),
            "safe_reason": _sanitize_text(self.safe_reason),
            "provider_snapshot_ref": _sanitize_text(self.provider_snapshot_ref),
            "safe_summary_ref": _sanitize_text(self.safe_summary_ref),
            "budget_policy_version": self.budget_policy_version,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeApprovedDetailLease:
    lease_ref: str
    request_id: str
    ledger_id: str
    candidate_evidence_id: str
    provider_candidate_key_hash: str
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

    def to_public_payload(self) -> dict[str, object]:
        return {
            "lease_ref": _sanitize_text(self.lease_ref),
            "request_id": _sanitize_text(self.request_id),
            "ledger_id": _sanitize_text(self.ledger_id),
            "candidate_evidence_id": _sanitize_text(self.candidate_evidence_id),
            "provider_candidate_key_hash": self.provider_candidate_key_hash,
            "connection_id": _sanitize_text(self.connection_id),
            "compliance_gate_ref": _sanitize_text(self.compliance_gate_ref),
            "detail_candidate_count": _json_list_count(self.detail_candidates_json),
            "daily_budget": self.daily_budget,
            "budget_date": self.budget_date,
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
            "provider_snapshot_refs": [_sanitize_text(ref) for ref in self.provider_snapshot_refs],
            "safe_summary_refs": [_sanitize_text(ref) for ref in self.safe_summary_refs],
            "detail_recommendations": [item.to_public_payload() for item in self.detail_recommendations],
            "events": [event.to_public_payload() for event in self.events],
            "blocked_reason_code": _sanitize_text(self.blocked_reason_code),
            "stop_reason_code": _sanitize_text(self.stop_reason_code),
            "retryable": self.retryable,
            "safe_error_summary": _sanitize_text(self.safe_error_summary),
            "error_ref": _sanitize_text(self.error_ref),
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
    liepin_context: Mapping[str, str | int | bool | None] | None = None
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
            "liepin_context": _sanitize_mapping(self.liepin_context or {}),
            "approved_detail_lease_ref": _sanitize_text(
                self.approved_detail_lease.lease_ref if self.approved_detail_lease is not None else self.approved_detail_lease_ref
            ),
            "approved_detail_lease": (
                self.approved_detail_lease.to_public_payload() if self.approved_detail_lease is not None else None
            ),
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
        normalized.append(source)  # type: ignore[arg-type]
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
                )
            )
            continue

        worker_mode = str(getattr(settings, "liepin_worker_mode", "disabled"))
        backend_mode = "blocked" if worker_mode == "disabled" else "legacy_worker_compat"
        safe_posture = {"worker_mode": worker_mode, **dict(liepin_context or {})}
        plans.append(
            RuntimeSourceLanePlan(
                source_plan_id=f"{runtime_run_id}:source:{index}:liepin",
                runtime_run_id=runtime_run_id,
                source="liepin",
                label="Liepin",
                backend_mode=backend_mode,
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


def clone_run_state_for_source_lane(run_state: RunState) -> RunState:
    return run_state.model_copy(
        deep=True,
        update={
            "seen_resume_ids": [],
            "candidate_store": {},
            "normalized_store": {},
            "source_evidence_by_resume_id": {},
            "scorecards_by_resume_id": {},
            "top_pool_ids": [],
            "round_history": [],
        },
    )


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


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    if _is_sensitive_value(value):
        return _REDACTED
    return value


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
