from __future__ import annotations

from collections.abc import Mapping, Sequence

from seektalent_runtime_control.errors import RuntimeControlError


_PUBLIC_STAGE_OUTPUT_SCHEMA = "runtime-public-stage-output/v2"
_PUBLIC_EVENT_SCHEMA = "runtime_public_event_v1"
_PUBLIC_ROUND_STAGES = {"round_query", "source_result", "merge", "scoring", "feedback"}
_PUBLIC_STAGE_OUTPUT_KINDS = {
    "runtime_public_round_query",
    "runtime_public_source_result",
    "runtime_public_merge",
    "runtime_public_scoring",
    "runtime_public_feedback",
    "runtime_public_finalization",
}
_PUBLIC_ALLOWED_KEYS = {
    "schemaVersion",
    "publicEventSchemaVersion",
    "stage",
    "roundNo",
    "sourceKind",
    "status",
    "counts",
    "details",
    "safeReasonCode",
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
    "resumeQualityComment",
    "reflectionSummary",
    "suggestedStopReason",
    "finalizationReasonCode",
}
_PUBLIC_DETAIL_INT_KEYS = {"finalizationRevision"}
_PUBLIC_DETAIL_BOOL_KEYS = {
    "suggestStop",
}
_PUBLIC_DETAIL_STRING_LIST_KEYS = {
    "suggestedActivateTerms",
    "suggestedAddFilterFields",
    "suggestedDeprioritizeTerms",
    "suggestedDropFilterFields",
    "suggestedDropTerms",
    "suggestedKeepFilterFields",
    "suggestedKeepTerms",
}
_PUBLIC_DETAIL_QUERY_GROUP_KEYS = {"queryGroups"}
_PUBLIC_QUERY_GROUP_LIFECYCLES = {"planned", "executed"}
_PUBLIC_QUERY_EXECUTION_STATUSES = {"completed", "partial", "blocked", "failed"}
_PUBLIC_SOURCE_REASON_CODES = {
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
_SENSITIVE_KEY_EXACT = {
    "provider",
    "resume",
}
_SENSITIVE_KEY_FRAGMENTS = (
    "prompt",
    "providerpayload",
    "providerresponse",
    "rawprovider",
    "rawpayload",
    "rawresume",
    "candidateresume",
    "resumetext",
    "resumepayload",
    "resumehtml",
    "resumejson",
    "rawresponse",
    "rawoutput",
    "structuredoutput",
    "authorization",
    "password",
    "secret",
    "cookie",
    "token",
    "apikey",
)


def sanitize_stage_output_payload(
    *,
    output_kind: str,
    schema_version: str,
    output: Mapping[str, object],
    stage: str,
    round_no: int | None,
    node_id: str | None,
) -> dict[str, object]:
    _reject_sensitive_keys(output)
    if output_kind in _PUBLIC_STAGE_OUTPUT_KINDS:
        _validate_public_stage_output_metadata(
            output=output,
            stage=stage,
            round_no=round_no,
            output_kind=output_kind,
            schema_version=schema_version,
            node_id=node_id,
        )
        return _sanitize_public_stage_output(output)
    return _safe_json_object(output)


def _validate_public_stage_output_metadata(
    *,
    output: Mapping[str, object],
    stage: str,
    round_no: int | None,
    output_kind: str,
    schema_version: str,
    node_id: str | None,
) -> None:
    payload_stage = output.get("stage")
    payload_round = output.get("roundNo")
    payload_source = output.get("sourceKind")
    if schema_version != _PUBLIC_STAGE_OUTPUT_SCHEMA:
        raise RuntimeControlError("runtime_stage_output_schema_mismatch")
    if payload_stage != stage or output_kind != f"runtime_public_{stage}":
        raise RuntimeControlError("runtime_stage_output_metadata_mismatch")
    if payload_round != round_no or payload_source != node_id:
        raise RuntimeControlError("runtime_stage_output_metadata_mismatch")
    if output.get("schemaVersion") != _PUBLIC_STAGE_OUTPUT_SCHEMA:
        raise RuntimeControlError("runtime_stage_output_metadata_mismatch")
    if output.get("publicEventSchemaVersion") != _PUBLIC_EVENT_SCHEMA:
        raise RuntimeControlError("runtime_stage_output_metadata_mismatch")
    if stage in _PUBLIC_ROUND_STAGES and round_no is None:
        raise RuntimeControlError("runtime_public_round_required")
    if stage == "finalization" and round_no is not None:
        raise RuntimeControlError("runtime_public_finalization_run_level_required")


def _sanitize_public_stage_output(output: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key in _PUBLIC_ALLOWED_KEYS:
        if key not in output:
            continue
        value = output[key]
        if key == "counts":
            sanitized[key] = _safe_counts(value)
        elif key == "details":
            sanitized[key] = _safe_details(value)
        elif key == "roundNo":
            sanitized[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        elif isinstance(value, str | int | bool) or value is None:
            sanitized[key] = value
    return sanitized


def _safe_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _PUBLIC_COUNT_KEYS:
            continue
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            counts[key] = item
    return counts


def _safe_details(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    details: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if key in _PUBLIC_DETAIL_QUERY_GROUP_KEYS:
            details[key] = _safe_query_groups(item)
        elif key in _PUBLIC_DETAIL_TEXT_KEYS and isinstance(item, str):
            details[key] = item[:2000]
        elif key in _PUBLIC_DETAIL_INT_KEYS and isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            details[key] = item
        elif key in _PUBLIC_DETAIL_BOOL_KEYS and isinstance(item, bool):
            details[key] = item
        elif (
            key in _PUBLIC_DETAIL_STRING_LIST_KEYS
            and isinstance(item, Sequence)
            and not isinstance(item, str | bytes | bytearray)
        ):
            details[key] = [entry[:200] for entry in item if isinstance(entry, str) and entry.strip()][:50]
    return details


def _safe_query_groups(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    groups: list[dict[str, object]] = []
    seen_query_instance_ids: set[str] = set()
    for item in value:
        group = _safe_query_group(item)
        if group is None:
            continue
        query_instance_id = group.get("queryInstanceId")
        if not isinstance(query_instance_id, str) or query_instance_id in seen_query_instance_ids:
            continue
        seen_query_instance_ids.add(query_instance_id)
        groups.append(group)
        if len(groups) >= 2:
            break
    return groups


def _safe_query_group(value: object) -> dict[str, object] | None:
    item = _string_object_mapping(value)
    query_instance_id = _text(item.get("queryInstanceId"))
    term_group_key = _text(item.get("termGroupKey"))
    query_role = _text(item.get("queryRole"))
    lane_type = _text(item.get("laneType"))
    query_terms = _string_list(item.get("queryTerms"))
    keyword_query = _text(item.get("keywordQuery"))
    lifecycle = _text(item.get("lifecycle"))
    if (
        query_instance_id is None
        or term_group_key is None
        or query_role is None
        or lane_type is None
        or not query_terms
        or keyword_query is None
        or lifecycle not in _PUBLIC_QUERY_GROUP_LIFECYCLES
    ):
        return None
    group: dict[str, object] = {
        "queryInstanceId": query_instance_id,
        "termGroupKey": term_group_key,
        "queryRole": query_role,
        "laneType": lane_type,
        "queryTerms": query_terms,
        "keywordQuery": keyword_query,
        "lifecycle": lifecycle,
    }
    if lifecycle == "planned":
        group.update(
            executionStatus=None,
            attempted=False,
            rawCandidateCount=0,
            uniqueCandidateCount=0,
            duplicateCandidateCount=0,
            executions=[],
        )
        return group

    execution_status = _text(item.get("executionStatus"))
    attempted = item.get("attempted")
    if execution_status not in _PUBLIC_QUERY_EXECUTION_STATUSES or not isinstance(attempted, bool):
        return None
    group.update(
        executionStatus=execution_status,
        attempted=attempted,
        rawCandidateCount=_non_negative_int(item.get("rawCandidateCount")) or 0,
        uniqueCandidateCount=_non_negative_int(item.get("uniqueCandidateCount")) or 0,
        duplicateCandidateCount=_non_negative_int(item.get("duplicateCandidateCount")) or 0,
        executions=_safe_query_executions(item.get("executions")),
    )
    return group


def _safe_query_executions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    executions: list[dict[str, object]] = []
    seen_sources: set[str] = set()
    for item in value:
        entry = _string_object_mapping(item)
        source_kind = _text(entry.get("sourceKind"))
        status = _text(entry.get("status"))
        if source_kind is None or source_kind in seen_sources or status not in _PUBLIC_QUERY_EXECUTION_STATUSES:
            continue
        execution: dict[str, object] = {
            "sourceKind": source_kind,
            "status": status,
            "rawCandidateCount": _non_negative_int(entry.get("rawCandidateCount")) or 0,
            "uniqueCandidateCount": _non_negative_int(entry.get("uniqueCandidateCount")) or 0,
            "duplicateCandidateCount": _non_negative_int(entry.get("duplicateCandidateCount")) or 0,
        }
        safe_reason_code = _safe_reason_code(entry.get("safeReasonCode"))
        if safe_reason_code is not None:
            execution["safeReasonCode"] = safe_reason_code
        executions.append(execution)
        seen_sources.add(source_kind)
        if len(executions) >= 2:
            break
    return executions


def _text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value[:2000]
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [entry[:200] for entry in value if isinstance(entry, str) and entry.strip()][:50]


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _safe_reason_code(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if text in _PUBLIC_SOURCE_REASON_CODES:
        return text
    mapped = _PUBLIC_REASON_MAP.get(text)
    return mapped if mapped in _PUBLIC_SOURCE_REASON_CODES else None


def _safe_json_object(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, str | int | float | bool) or item is None:
            safe[key] = item
        elif isinstance(item, Mapping):
            safe[key] = _safe_json_object(_string_object_mapping(item))
        elif isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            safe[key] = [_safe_json_value(entry) for entry in item]
    return safe


def _safe_json_value(value: object) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        return _safe_json_object(_string_object_mapping(value))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_safe_json_value(item) for item in value]
    return str(value)


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                raise RuntimeControlError("runtime_stage_output_sensitive_payload", payload={"key": key})
            _reject_sensitive_keys(item)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            _reject_sensitive_keys(item)


def _string_object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(char for char in key.lower() if char.isalnum())
    return normalized in _SENSITIVE_KEY_EXACT or any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)
