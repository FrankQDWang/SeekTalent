from __future__ import annotations

from datetime import UTC, datetime


def max_iso_timestamp(*values: str | None) -> str:
    parsed = [_parse_timestamp(value) for value in values if value]
    if not parsed:
        raise ValueError("at least one timestamp is required")
    return max(parsed).isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp_lte(left: str, right: str) -> bool:
    return _parse_timestamp(left) <= _parse_timestamp(right)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
