from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict

PUBLIC_EVENT_SCHEMA_VERSION = "runtime_public_event_v1"
SourceKind = str

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
    "source_filter_applied",
    "source_filter_partial",
    "source_filter_unavailable",
    "source_filter_unsupported",
    "source_filter_degraded",
    "source_location_filter_unsupported",
    "source_age_filter_unsupported",
    "source_provider_failed",
    "source_partial",
    "source_unknown",
}

_PUBLIC_REASON_MAP = {
    "blocked_backend_unavailable": "source_browser_backend_unavailable",
    "blocked_login_required": "source_login_required",
    "failed_provider_error": "source_provider_failed",
    "login_required": "source_login_required",
    "partial_timeout": "source_browser_timeout",
    "runtime_failed": "source_provider_failed",
    "cancelled_by_user": "source_unknown",
    "source_location_filter_partial": "source_filter_partial",
    "source_age_filter_unsupported": "source_filter_unavailable",
    "source_location_filter_unsupported": "source_filter_unavailable",
    "source_filter_unsupported": "source_filter_unavailable",
    "source_filter_applied": "source_filter_applied",
}

_PUBLIC_COUNT_KEYS = {
    "roundReturned",
    "roundIdentities",
    "sourceCumulativeReturned",
    "sourceCumulativeIdentities",
    "roundUniqueIdentities",
    "mergedIdentities",
    "topPoolCount",
    "selectedIdentityCount",
    "feedbackCandidateCount",
}
_PUBLIC_DETAIL_TEXT_KEYS = {
    "keywordQuery",
    "resumeQualityComment",
    "reflectionSummary",
    "suggestedStopReason",
    "finalizationReasonCode",
}
_PUBLIC_DETAIL_INT_KEYS = {"finalizationRevision"}
_PUBLIC_DETAIL_BOOL_KEYS = {
    "suggestStop",
}
_PUBLIC_DETAIL_LIST_KEYS = {
    "queryTerms",
    "suggestedActivateTerms",
    "suggestedAddFilterFields",
    "suggestedDeprioritizeTerms",
    "suggestedDropFilterFields",
    "suggestedDropTerms",
    "suggestedKeepFilterFields",
    "suggestedKeepTerms",
}
_PUBLIC_DETAIL_QUERY_PACKAGE_KEYS = {"plannedQueries", "executedQueries"}


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
    details: dict[str, object]
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
        details=_safe_public_details(payload.get("details")),
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
    details: Mapping[str, object] | None = None,
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
            "details": dict(details or {}),
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
    if not isinstance(value, str):
        raise ValueError("runtime_public_event_source_kind_invalid")
    text = value.strip()
    if not text:
        raise ValueError("runtime_public_event_source_kind_invalid")
    return text


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


def _safe_public_details(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    details: dict[str, object] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str):
            continue
        if key in _PUBLIC_DETAIL_TEXT_KEYS:
            text = _public_detail_text(raw_value, max_length=2000)
            if text is not None:
                details[key] = text
        elif key in _PUBLIC_DETAIL_BOOL_KEYS:
            if isinstance(raw_value, bool):
                details[key] = raw_value
        elif key in _PUBLIC_DETAIL_INT_KEYS:
            value = _optional_non_negative_int(raw_value)
            if value is not None:
                details[key] = value
        elif key in _PUBLIC_DETAIL_LIST_KEYS:
            values = _public_detail_list(raw_value)
            if values:
                details[key] = values
        elif key in _PUBLIC_DETAIL_QUERY_PACKAGE_KEYS:
            values = _public_query_packages(raw_value)
            if values:
                details[key] = values
    return details


def _public_query_packages(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    packages: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        package: dict[str, object] = {}
        item_mapping = _string_key_mapping(item)
        source_kind = _public_detail_text(item_mapping.get("sourceKind"), max_length=80)
        query_role = _public_detail_text(item_mapping.get("queryRole"), max_length=80)
        lane_type = _public_detail_text(item_mapping.get("laneType"), max_length=80)
        query_terms = _public_detail_list(item_mapping.get("queryTerms"))
        keyword_query = _public_detail_text(item_mapping.get("keywordQuery"), max_length=2000)
        if source_kind is not None:
            package["sourceKind"] = source_kind
        if query_role is not None:
            package["queryRole"] = query_role
        if lane_type is not None:
            package["laneType"] = lane_type
        if query_terms:
            package["queryTerms"] = query_terms
        if keyword_query is not None:
            package["keywordQuery"] = keyword_query
        if package:
            packages.append(package)
        if len(packages) >= 50:
            break
    return packages


def _string_key_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _public_detail_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    values: list[str] = []
    for item in value:
        text = _public_detail_text(item, max_length=160)
        if text is not None and text not in values:
            values.append(text)
        if len(values) >= 40:
            break
    return values


def _public_detail_text(value: object, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] if text else None
