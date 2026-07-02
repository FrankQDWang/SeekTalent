from __future__ import annotations

from collections.abc import Mapping, Sequence

from seektalent_runtime_control.errors import RuntimeControlError


_PUBLIC_STAGE_OUTPUT_SCHEMA = "runtime-public-stage-output/v1"
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
_PUBLIC_DETAIL_STRING_LIST_KEYS = {
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
        if key in _PUBLIC_DETAIL_QUERY_PACKAGE_KEYS:
            details[key] = _safe_query_packages(item)
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


def _safe_query_packages(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    packages: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        item_mapping = _string_object_mapping(item)
        package: dict[str, object] = {}
        source_kind = _text(item_mapping.get("sourceKind"), item_mapping.get("source_kind"))
        query_role = _text(item_mapping.get("queryRole"), item_mapping.get("query_role"))
        lane_type = _text(item_mapping.get("laneType"), item_mapping.get("lane_type"))
        query_terms = _string_list(item_mapping.get("queryTerms", item_mapping.get("query_terms")))
        keyword_query = _text(item_mapping.get("keywordQuery"), item_mapping.get("keyword_query"))
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
    return packages[:50]


def _text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value[:2000]
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [entry[:200] for entry in value if isinstance(entry, str) and entry.strip()][:50]


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
