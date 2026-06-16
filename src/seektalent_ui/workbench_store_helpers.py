from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime

from seektalent_ui.redaction import redact_text


def json_list(values: list[str]) -> str:
    return json.dumps([bounded_text(value, 500) or "" for value in values], ensure_ascii=False)


def json_to_list(raw_value: str) -> list[str]:
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def json_to_dict(raw_value: str) -> dict[str, object]:
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def bounded_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length]


def like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def safe_candidate_text(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    redacted = redact_text(str(value).strip())
    if redacted is None:
        return None
    return bounded_text(redacted, max_length)


def safe_list(value: object, max_items: int, max_length: int) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    result: list[str] = []
    for item in value[:max_items]:
        text = safe_candidate_text(item, max_length)
        if text:
            result.append(text)
    return result


def object_list(value: object | None) -> list[object]:
    if isinstance(value, list | tuple):
        return list(value)
    return []


def attr(value: object, name: str) -> object | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == name:
                return item
        return None
    return getattr(value, name, None)


def mapping_get(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        for candidate_key, item in value.items():
            if candidate_key == key:
                return item
    return None


def first(value: object) -> object | None:
    if isinstance(value, list | tuple) and value:
        return value[0]
    return None


def int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return iso(now())


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
