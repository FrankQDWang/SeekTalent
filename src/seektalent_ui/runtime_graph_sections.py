from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from seektalent_ui.models import (
    WorkbenchRuntimeGraphFactResponse,
    WorkbenchRuntimeGraphSectionResponse,
)

_REDACTED_KEYS = {
    "artifact",
    "artifact_path",
    "artifactPath",
    "auth",
    "authorization",
    "browser_endpoint",
    "cdp",
    "cookie",
    "cookies",
    "file",
    "filepath",
    "path",
    "provider_payload",
    "raw_payload",
    "runtimeRunId",
    "storage_state",
    "token",
    "url",
    "websocket",
}
_REDACTED_KEY_LOOKUP = {item.casefold() for item in _REDACTED_KEYS}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def section_from_text(heading: str, text: str | None) -> WorkbenchRuntimeGraphSectionResponse | None:
    clean = clean_runtime_graph_text(text)
    if clean is None:
        return None
    return WorkbenchRuntimeGraphSectionResponse(heading=heading, kind="text", text=clean)


def section_from_facts(
    heading: str,
    facts: Sequence[tuple[str, object | None]],
) -> WorkbenchRuntimeGraphSectionResponse:
    visible = [
        WorkbenchRuntimeGraphFactResponse(label=label, value=value_text)
        for label, value in facts
        if (value_text := runtime_graph_value_text(value)) is not None
    ]
    return WorkbenchRuntimeGraphSectionResponse(heading=heading, kind="facts", facts=visible)


def section_from_list(
    heading: str,
    values: Sequence[object],
) -> WorkbenchRuntimeGraphSectionResponse:
    visible = [text for value in values if (text := runtime_graph_value_text(value)) is not None]
    return WorkbenchRuntimeGraphSectionResponse(heading=heading, kind="list", values=visible)


def safe_natural_text(value: object) -> str:
    lines = _natural_lines(value)
    return "\n".join(line for line in lines if line.strip())


def _natural_lines(value: object, *, prefix: str | None = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        lines: list[str] = []
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            if _is_redacted_key(key):
                continue
            item_text = runtime_graph_value_text(raw_item)
            if item_text is not None:
                lines.append(f"{key}：{item_text}" if prefix is None else f"{prefix}.{key}：{item_text}")
        return lines
    text = runtime_graph_value_text(value)
    return [text] if text is not None else []


def runtime_graph_value_text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return clean_runtime_graph_text(value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, Mapping):
        parts = []
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            if _is_redacted_key(key):
                continue
            text = runtime_graph_value_text(raw_item)
            if text is not None:
                parts.append(f"{key}={text}")
        return "；".join(parts) if parts else None
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        parts = [text for item in value if (text := runtime_graph_value_text(item)) is not None]
        return "、".join(parts) if parts else None
    return clean_runtime_graph_text(str(value))


def clean_runtime_graph_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _is_redacted_key(key: str) -> bool:
    lowered = key.strip().casefold()
    return lowered in _REDACTED_KEY_LOOKUP or any(
        token in lowered for token in ("cookie", "token", "authorization", "artifact", "storage")
    )
