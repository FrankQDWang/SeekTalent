from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict, cast


PUBLIC_EVENT_SCHEMA_VERSION = "runtime_public_event_v1"
SourceKind = Literal["cts", "liepin"]

_RUNTIME_PUBLIC_EVENT_NAMES = {
    "round_query": "runtime_round_query_ready",
    "source_dispatch": "runtime_round_source_dispatch",
    "source_result": "runtime_round_source_result",
    "merge": "runtime_round_merge_completed",
    "scoring": "runtime_round_scoring_completed",
    "feedback": "runtime_round_feedback_completed",
    "finalization": "runtime_finalization_completed",
}

PUBLIC_SOURCE_REASON_CODES = {
    "job_lease_expired",
    "relay_pending_worker",
    "runtime_failed",
    "source_login_required",
    "source_account_mismatch",
    "source_browser_timeout",
    "source_browser_backend_unavailable",
    "source_browser_extension_disconnected",
    "source_browser_policy_blocked",
    "source_risk_or_verification_required",
    "source_browser_interaction_required",
    "source_budget_exhausted",
    "source_provider_failed",
    "source_partial",
    "source_unknown",
}

_PUBLIC_REASON_MAP = {
    "blocked_backend_unavailable": "source_browser_backend_unavailable",
    "failed_provider_error": "source_provider_failed",
    "login_required": "source_login_required",
    "partial_timeout": "source_browser_timeout",
    "runtime_failed": "source_provider_failed",
    "cancelled_by_user": "source_unknown",
    "liepin_connection_not_connected": "source_login_required",
    "liepin_browser_login_required": "source_login_required",
    "liepin_browser_probe_unavailable": "source_browser_backend_unavailable",
    "liepin_browser_account_mismatch": "source_account_mismatch",
    "liepin_pi_disabled": "source_browser_backend_unavailable",
    "liepin_pi_command_missing": "source_browser_backend_unavailable",
    "liepin_pi_command_invalid": "source_browser_backend_unavailable",
    "liepin_pi_skill_missing": "source_browser_backend_unavailable",
    "liepin_pi_account_secret_missing": "source_browser_backend_unavailable",
    "liepin_pi_mcp_config_missing": "source_browser_backend_unavailable",
    "liepin_pi_mcp_config_invalid": "source_browser_backend_unavailable",
    "liepin_pi_mcp_adapter_missing": "source_browser_backend_unavailable",
    "liepin_pi_mcp_adapter_unavailable": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_mcp_command_missing": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_mcp_config_mismatch": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_mcp_tool_names_missing": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_mcp_missing": "source_browser_backend_unavailable",
    "liepin_pi_dokobot_tool_unobserved": "source_browser_backend_unavailable",
    "liepin_opencli_backend_disabled": "source_browser_backend_unavailable",
    "liepin_opencli_command_missing": "source_browser_backend_unavailable",
    "liepin_opencli_extension_disconnected": "source_browser_extension_disconnected",
    "liepin_opencli_status_unavailable": "source_browser_backend_unavailable",
    "liepin_opencli_forbidden_command": "source_browser_policy_blocked",
    "liepin_opencli_forbidden_text": "source_browser_policy_blocked",
    "liepin_opencli_host_blocked": "source_browser_policy_blocked",
    "liepin_opencli_start_url_blocked": "source_browser_policy_blocked",
    "liepin_opencli_window_policy_blocked": "source_browser_policy_blocked",
    "liepin_opencli_budget_exhausted": "source_budget_exhausted",
    "liepin_opencli_timeout": "source_browser_timeout",
    "liepin_opencli_login_required": "source_login_required",
    "liepin_opencli_identity_intercept": "source_risk_or_verification_required",
    "liepin_opencli_risk_page": "source_risk_or_verification_required",
    "liepin_opencli_unknown_modal": "source_browser_interaction_required",
    "liepin_opencli_source_policy_missing": "source_browser_policy_blocked",
    "liepin_opencli_malformed_state": "source_browser_backend_unavailable",
}

_PUBLIC_COUNT_KEYS = {
    "roundReturned",
    "roundIdentities",
    "sourceCumulativeReturned",
    "sourceCumulativeIdentities",
    "mergedIdentities",
    "topPoolCount",
    "selectedIdentityCount",
    "feedbackCandidateCount",
}


class RuntimePublicEvent(TypedDict):
    schemaVersion: str
    runtimeRunId: str
    eventId: str
    eventSeq: int
    stage: str
    roundNo: int | None
    sourceKind: SourceKind | None
    status: str
    counts: dict[str, int]
    safeReasonCode: str | None
    createdAt: str | None


def runtime_public_event_name(stage: object) -> str:
    text = str(stage or "").strip()
    if not text:
        raise ValueError("runtime_public_event_stage_required")
    try:
        return _RUNTIME_PUBLIC_EVENT_NAMES[text]
    except KeyError as exc:
        raise ValueError(f"runtime_public_event_stage_unsupported:{text}") from exc


def public_source_reason_code(reason_code: object) -> str | None:
    text = str(reason_code or "").strip()
    if not text:
        return None
    if text in PUBLIC_SOURCE_REASON_CODES:
        return text
    mapped = _PUBLIC_REASON_MAP.get(text)
    if mapped in PUBLIC_SOURCE_REASON_CODES:
        return mapped
    return None


def normalize_runtime_public_event(payload: Mapping[str, object]) -> RuntimePublicEvent:
    if payload.get("schemaVersion") != PUBLIC_EVENT_SCHEMA_VERSION:
        raise ValueError("runtime_public_event_schema_version_invalid")
    stage = str(payload.get("stage") or "").strip()
    event_name = runtime_public_event_name(stage)
    del event_name
    runtime_run_id = _required_text(payload.get("runtimeRunId"), "runtime_public_event_runtime_run_id_required")
    event_id = _required_text(payload.get("eventId"), "runtime_public_event_event_id_required")
    event_seq = _required_non_negative_int(payload.get("eventSeq"), "runtime_public_event_event_seq_invalid")
    round_no = _optional_non_negative_int(payload.get("roundNo"))
    source_kind = _source_kind_or_none(payload.get("sourceKind"))
    return RuntimePublicEvent(
        schemaVersion=PUBLIC_EVENT_SCHEMA_VERSION,
        runtimeRunId=runtime_run_id,
        eventId=event_id,
        eventSeq=event_seq,
        stage=stage,
        roundNo=round_no,
        sourceKind=source_kind,
        status=str(payload.get("status") or "completed"),
        counts=_safe_public_counts(payload.get("counts")),
        safeReasonCode=public_source_reason_code(payload.get("safeReasonCode")),
        createdAt=str(payload.get("createdAt")).strip() if payload.get("createdAt") is not None else None,
    )


def make_runtime_public_event(
    *,
    runtime_run_id: str,
    stage: str,
    event_seq: int,
    round_no: int | None,
    source_kind: SourceKind | None = None,
    status: str = "completed",
    counts: Mapping[str, int] | None = None,
    safe_reason_code: object = None,
    created_at: str | None = None,
) -> RuntimePublicEvent:
    runtime_public_event_name(stage)
    source_part = source_kind or "all"
    round_part = round_no if round_no is not None else "final"
    event_id = f"{runtime_run_id}:{round_part}:{stage}:{source_part}"
    return normalize_runtime_public_event(
        {
            "schemaVersion": PUBLIC_EVENT_SCHEMA_VERSION,
            "runtimeRunId": runtime_run_id,
            "eventId": event_id,
            "eventSeq": event_seq,
            "stage": stage,
            "roundNo": round_no,
            "sourceKind": source_kind,
            "status": status,
            "counts": dict(counts or {}),
            "safeReasonCode": safe_reason_code,
            "createdAt": created_at,
        }
    )


def _required_text(value: object, error_message: str) -> str:
    if not isinstance(value, str):
        raise ValueError(error_message)
    text = value.strip()
    if not text:
        raise ValueError(error_message)
    return text


def _required_non_negative_int(value: object, error_message: str) -> int:
    parsed = _optional_non_negative_int(value)
    if parsed is None:
        raise ValueError(error_message)
    return parsed


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _source_kind_or_none(value: object) -> SourceKind | None:
    if value is None:
        return None
    if value in {"cts", "liepin"}:
        return cast(SourceKind, value)
    raise ValueError("runtime_public_event_source_kind_invalid")


def _safe_public_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, raw_count in value.items():
        if not isinstance(key, str) or key not in _PUBLIC_COUNT_KEYS:
            continue
        count = _optional_non_negative_int(raw_count)
        if count is not None:
            counts[key] = count
    return counts
