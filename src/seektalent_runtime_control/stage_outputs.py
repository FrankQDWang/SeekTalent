from __future__ import annotations

from collections.abc import Mapping, Sequence

from seektalent_runtime_control.errors import RuntimeControlError


_PUBLIC_STAGE_OUTPUT_SCHEMA = "runtime-public-stage-output/v1"
_PUBLIC_STAGE_OUTPUT_KINDS = {
    "runtime_public_source_result",
    "runtime_public_merge",
    "runtime_public_scoring",
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
    "mergedIdentities",
    "topPoolCount",
    "selectedIdentityCount",
    "feedbackCandidateCount",
}
_PUBLIC_DETAIL_KEYS = {
    "reflectionSummary",
    "reflectionRationale",
    "suggestedStopReason",
    "suggestStop",
    "suggestedActivateTerms",
    "suggestedAddFilterFields",
    "suggestedDeprioritizeTerms",
    "suggestedDropFilterFields",
    "suggestedDropTerms",
    "suggestedKeepFilterFields",
    "suggestedKeepTerms",
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


def sanitize_stage_output_payload(*, output_kind: str, schema_version: str, output: Mapping[str, object]) -> dict[str, object]:
    _reject_sensitive_keys(output)
    if output_kind in _PUBLIC_STAGE_OUTPUT_KINDS:
        if schema_version != _PUBLIC_STAGE_OUTPUT_SCHEMA:
            raise RuntimeControlError("runtime_stage_output_schema_unsupported")
        return _sanitize_public_stage_output(output)
    return _safe_json_object(output)


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
        if not isinstance(key, str) or key not in _PUBLIC_DETAIL_KEYS:
            continue
        if isinstance(item, str):
            details[key] = item[:2000]
        elif isinstance(item, bool):
            details[key] = item
        elif isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            details[key] = [entry[:200] for entry in item if isinstance(entry, str) and entry.strip()][:50]
    return details


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
