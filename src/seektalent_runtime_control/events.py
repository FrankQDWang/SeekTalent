from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json

from seektalent.progress import ProgressEvent
from seektalent.runtime.public_events import normalize_runtime_public_event, runtime_public_event_name
from seektalent_runtime_control.models import RuntimeControlEvent, RuntimeControlEventInput


PUBLIC_RUNTIME_EVENT_SCHEMA_VERSION = "runtime_public_event_v1"
RUNTIME_CONTROL_EVENT_SCHEMA_VERSION = "runtime-control-event/v1"

_REDACTED_SUMMARY = "runtime progress"
_SENSITIVE_KEY_FRAGMENTS = (
    "prompt",
    "provider",
    "resume",
    "auth",
    "token",
    "secret",
    "password",
    "cookie",
    "apikey",
    "rawstructuredoutput",
    "rawoutput",
    "rawresponse",
)
_SUMMARY_SENSITIVE_TERMS = (
    "authorization",
    "bearer",
    "cookie",
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "prompt",
    "provider",
    "resume",
)


def normalize_progress_event(progress: ProgressEvent, runtime_run_id: str, now: str) -> RuntimeControlEventInput:
    if progress.type == "runtime_public_event":
        return _normalize_public_progress_event(progress, runtime_run_id=runtime_run_id, now=now)
    return _normalize_internal_progress_event(progress, runtime_run_id=runtime_run_id, now=now)


def public_event_payload(event: RuntimeControlEvent | RuntimeControlEventInput) -> dict[str, object] | None:
    if event.visibility != "public":
        return None
    try:
        return _runtime_public_event_payload(
            normalize_runtime_public_event(event.payload),
            runtime_run_id=event.runtime_run_id,
        )
    except (TypeError, ValueError):
        return None


def _normalize_public_progress_event(
    progress: ProgressEvent,
    *,
    runtime_run_id: str,
    now: str,
) -> RuntimeControlEventInput:
    public_event = normalize_runtime_public_event(progress.payload)
    event_type = runtime_public_event_name(public_event["stage"])
    payload = _runtime_public_event_payload(public_event, runtime_run_id=runtime_run_id)
    idempotency_key = _text(progress.payload.get("idempotencyKey")) or _text(payload.get("eventId"))
    if idempotency_key is None:
        idempotency_key = _fallback_idempotency_key(
            runtime_run_id=runtime_run_id,
            progress_type=progress.type,
            round_no=public_event["roundNo"],
            timestamp=progress.timestamp,
            payload=payload,
        )
    return RuntimeControlEventInput(
        event_id=_event_id(runtime_run_id=runtime_run_id, idempotency_key=idempotency_key),
        runtime_run_id=runtime_run_id,
        event_type=event_type,
        stage=public_event["stage"],
        round_no=public_event["roundNo"],
        source_id=public_event["sourceKind"],
        status=public_event["status"],
        summary=_safe_summary(progress.message),
        payload=payload,
        schema_version=RUNTIME_CONTROL_EVENT_SCHEMA_VERSION,
        visibility="public",
        idempotency_key=idempotency_key,
        payload_kind="compact",
        workbench_event_global_seq=None,
        created_at=now,
    )


def _normalize_internal_progress_event(
    progress: ProgressEvent,
    *,
    runtime_run_id: str,
    now: str,
) -> RuntimeControlEventInput:
    payload = _redacted_dict(progress.payload)
    stage = _text(payload.get("stage")) or "runtime"
    round_no = progress.round_no if progress.round_no is not None else _non_negative_int(payload.get("roundNo"))
    idempotency_key = _text(progress.payload.get("eventId")) or _text(progress.payload.get("idempotencyKey"))
    compact_payload: dict[str, object] = {
        "progressType": progress.type,
        "occurredAt": progress.timestamp,
        **payload,
    }
    if idempotency_key is None:
        idempotency_key = _fallback_idempotency_key(
            runtime_run_id=runtime_run_id,
            progress_type=progress.type,
            round_no=round_no,
            timestamp=progress.timestamp,
            payload=compact_payload,
        )
    return RuntimeControlEventInput(
        event_id=_event_id(runtime_run_id=runtime_run_id, idempotency_key=idempotency_key),
        runtime_run_id=runtime_run_id,
        event_type=_progress_event_type(progress.type),
        stage=stage,
        round_no=round_no,
        source_id=_text(payload.get("sourceId")) or _text(payload.get("sourceKind")),
        status=_text(payload.get("status")) or "completed",
        summary=_safe_summary(progress.message),
        payload=compact_payload,
        schema_version=RUNTIME_CONTROL_EVENT_SCHEMA_VERSION,
        visibility="developer",
        idempotency_key=idempotency_key,
        payload_kind="compact",
        workbench_event_global_seq=None,
        created_at=now,
    )


def _runtime_public_event_payload(event: Mapping[str, object], *, runtime_run_id: str) -> dict[str, object]:
    return {
        "schemaVersion": PUBLIC_RUNTIME_EVENT_SCHEMA_VERSION,
        "runtimeRunId": runtime_run_id,
        "eventId": _runtime_public_event_id(event, runtime_run_id=runtime_run_id),
        "eventSeq": event["eventSeq"],
        "stage": event["stage"],
        "roundNo": event["roundNo"],
        "sourceKind": event["sourceKind"],
        "status": event["status"],
        "counts": _string_object_mapping(event.get("counts")),
        "details": _string_object_mapping(event.get("details")),
        "safeReasonCode": event["safeReasonCode"],
        "createdAt": event["createdAt"],
    }


def _runtime_public_event_id(event: Mapping[str, object], *, runtime_run_id: str) -> str:
    round_no = event["roundNo"]
    round_part = round_no if round_no is not None else "final"
    source_part = event["sourceKind"] or "all"
    return f"{runtime_run_id}:{round_part}:{event['stage']}:{source_part}"


def _string_object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _redacted_dict(value: Mapping[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or _is_sensitive_key(key):
            continue
        redacted[key] = _redacted_value(item)
    return redacted


def _redacted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _redacted_dict(_string_object_mapping(value))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_redacted_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(char for char in key.lower() if char.isalnum())
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_summary(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return _REDACTED_SUMMARY
    lower = text.lower()
    if any(term in lower for term in _SUMMARY_SENSITIVE_TERMS):
        return _REDACTED_SUMMARY
    return text[:300]


def _progress_event_type(event_type: str) -> str:
    if event_type.startswith("runtime_"):
        return event_type
    return f"runtime_{event_type}"


def _fallback_idempotency_key(
    *,
    runtime_run_id: str,
    progress_type: str,
    round_no: int | None,
    timestamp: str,
    payload: Mapping[str, object],
) -> str:
    digest = _digest(
        {
            "runtimeRunId": runtime_run_id,
            "progressType": progress_type,
            "roundNo": round_no,
            "timestamp": timestamp,
            "payload": payload,
        }
    )
    return f"{runtime_run_id}:{progress_type}:{round_no if round_no is not None else 'none'}:{digest[:16]}"


def _event_id(*, runtime_run_id: str, idempotency_key: str) -> str:
    return f"rtevt_{_digest({'runtimeRunId': runtime_run_id, 'idempotencyKey': idempotency_key})[:32]}"


def _digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
