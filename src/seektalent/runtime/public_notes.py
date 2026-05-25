from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from seektalent.runtime.public_events import PUBLIC_EVENT_SCHEMA_VERSION

_SAFE_COUNT_KEYS = {
    "roundReturned",
    "roundIdentities",
    "sourceCumulativeReturned",
    "sourceCumulativeIdentities",
    "mergedIdentities",
    "topPoolCount",
    "selectedIdentityCount",
    "feedbackCandidateCount",
}


def runtime_note_facts_from_events(events: Sequence[Mapping[str, object]]) -> tuple[list[str], list[int]]:
    facts: list[str] = []
    numbers: list[int] = []
    for event in events[-25:]:
        payload = _mapping(event.get("payload"))
        if payload is None or payload.get("schemaVersion") != PUBLIC_EVENT_SCHEMA_VERSION:
            continue
        stage = _safe_token(payload.get("stage"))
        if not stage:
            continue
        round_no = _optional_int(payload.get("roundNo"))
        prefix = f"runtime_{stage}"
        if round_no is not None:
            numbers.append(round_no)
            prefix = f"{prefix}_round_{round_no}"
        facts.append(f"{prefix}=seen")
        source = _safe_token(payload.get("sourceKind"))
        if source:
            facts.append(f"{prefix}_source={source}")
        status = _safe_token(payload.get("status"))
        if status:
            facts.append(f"{prefix}_status={status}")
        reason = _safe_token(payload.get("safeReasonCode"))
        if reason:
            facts.append(f"{prefix}_reason={reason}")
        counts = _mapping(payload.get("counts"))
        if counts is None:
            continue
        for key, raw_value in counts.items():
            if not isinstance(key, str) or key not in _SAFE_COUNT_KEYS:
                continue
            value = _optional_int(raw_value)
            if value is None:
                continue
            numbers.append(value)
            facts.append(f"{prefix}_{key}={value}")
    return facts, numbers


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _safe_token(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    result = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in text)
    return result[:80]


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
