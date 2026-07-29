from __future__ import annotations

from seektalent.failure_interpretation import PUBLIC_SOURCE_REASON_CODES


def require_public_source_reason_code(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    if reason_code not in PUBLIC_SOURCE_REASON_CODES:
        raise ValueError(f"unknown_public_source_reason_code:{reason_code}")
    return reason_code
