from __future__ import annotations


PUBLIC_SOURCE_REASON_CODES = frozenset(
    {
        "source_backend_unavailable",
        "source_timeout",
        "source_login_required",
        "source_risk_challenge",
        "source_filter_unavailable",
        "source_filter_unsupported",
        "source_budget_exhausted",
        "source_provider_error",
        "source_cancelled",
        "source_partial",
    }
)


def require_public_source_reason_code(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    if reason_code not in PUBLIC_SOURCE_REASON_CODES:
        raise ValueError(f"unknown_public_source_reason_code:{reason_code}")
    return reason_code
