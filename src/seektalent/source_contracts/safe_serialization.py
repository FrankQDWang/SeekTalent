from __future__ import annotations

from collections.abc import Mapping
import json
import re

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_TOKENS = {
    "access_token",
    "apikey",
    "api_key",
    "approval_secret",
    "authorization",
    "bearer",
    "cookie",
    "csrf",
    "password",
    "provider_key",
    "raw_html",
    "raw_provider_payload",
    "raw_resume",
    "secret",
    "session_secret",
    "token",
}
_SAFE_REASON_CODES = {
    "blocked_approval_missing",
    "blocked_backend_unavailable",
    "blocked_budget_exhausted",
    "blocked_compliance",
    "blocked_login_required",
    "cancelled_by_user",
    "card_rank_budget",
    "detail_enrichment_applied",
    "detail_evidence",
    "failed_internal_error",
    "failed_provider_error",
    "hard_education_mismatch",
    "hard_filter_passed",
    "hard_location_mismatch",
    "high_value_card",
    "insufficient_card_signal",
    "login_required",
    "matched_card_terms",
    "must_have_zero_overlap",
    "obvious_role_mismatch",
    "partial_budget_exhausted",
    "partial_timeout",
    "provider_rank_preserved",
    "source_age_filter_unsupported",
    "source_browser_backend_unavailable",
    "source_browser_timeout",
    "source_card_candidate",
    "source_detail_candidate",
    "source_filter_degraded",
    "source_filter_unavailable",
    "source_filter_unsupported",
    "source_lanes_completed",
    "source_lanes_degraded",
    "source_location_filter_unsupported",
    "source_login_required",
    "source_risk_challenge",
    "within_run_detail_budget",
}
_SAFE_COUNT_KEYS = {
    "cached_detail_urls",
    "candidates",
    "cards_filtered",
    "cards_seen",
    "closed_tabs",
    "detail_recommendations",
    "details_opened",
    "raw_candidates",
    "resumes_returned",
    "target_resumes",
    "visible_cards",
}
_SAFE_WORKFLOW_STEP_NAMES = {
    "apply_filters",
    "cache_detail_urls",
    "capture_detail",
    "finalize",
    "observe_cards",
    "open_detail",
    "prepare_search",
    "submit_search",
}
_SAFE_METADATA_KEYS = {
    "open_mode",
    "rank",
}
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?:^|[;\s])[-A-Za-z0-9_]*(?:cookie|secret|token|password|auth)=[^;\s]+", re.IGNORECASE),
)


def safe_context_payload(value: object) -> dict[str, str | bool]:
    to_safe_posture = getattr(value, "to_safe_posture", None)
    if callable(to_safe_posture):
        payload = to_safe_posture()
        if isinstance(payload, Mapping):
            return {
                str(key): cast_value
                for key, item in payload.items()
                if isinstance(key, str)
                and not is_sensitive_key(key)
                and (cast_value := safe_context_value(item)) is not None
            }
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): cast_value
        for key, item in value.items()
        if isinstance(key, str)
        and not is_sensitive_key(key)
        and (cast_value := safe_context_value(item)) is not None
    }


def safe_context_value(value: object) -> str | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text if text and not is_sensitive_value(text) else None
    return None


def sanitize_mapping(values: Mapping[str, str | int | bool | None]) -> dict[str, str | int | bool | None]:
    sanitized: dict[str, str | int | bool | None] = {}
    for key, value in values.items():
        key_text = str(key)
        if is_sensitive_key(key_text):
            sanitized[key_text] = _REDACTED
        elif isinstance(value, str) and is_sensitive_value(value):
            sanitized[key_text] = _REDACTED
        else:
            sanitized[key_text] = value
    return sanitized


def sanitize_count_mapping(values: Mapping[str, int]) -> dict[str, int]:
    sanitized: dict[str, int] = {}
    for key, value in values.items():
        if key not in _SAFE_COUNT_KEYS:
            continue
        if not isinstance(value, int):
            continue
        if value < 0:
            continue
        sanitized[key] = value
    return sanitized


def sanitize_step_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text if text in _SAFE_WORKFLOW_STEP_NAMES else None


def sanitize_safe_metadata(values: Mapping[str, str | int | bool | None]) -> dict[str, str | int | bool]:
    sanitized: dict[str, str | int | bool] = {}
    for key, value in values.items():
        if key not in _SAFE_METADATA_KEYS:
            continue
        if isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, int):
            sanitized[key] = value
        elif isinstance(value, str):
            text = value.strip()
            if text and not is_sensitive_value(text):
                sanitized[key] = text
    return sanitized


def sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _REDACTED if is_sensitive_value(text) else text


def sanitize_reason_code(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value in _SAFE_REASON_CODES else "unknown_reason"


def sanitize_artifact_ref(value: str | None) -> str | None:
    text = sanitize_text(value)
    if text is None or text == _REDACTED:
        return None
    return text if text.startswith(("artifact://", "corpus://")) else None


def sanitize_protected_artifact_ref(value: str | None) -> str | None:
    text = sanitize_text(value)
    if text is None or text == _REDACTED:
        return None
    return text if text.startswith(("artifact://protected/", "corpus://protected/", "protected://")) else None


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS)


def is_sensitive_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS)


def json_list_count(value: str) -> int:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return 0
    return len(decoded) if isinstance(decoded, list) else 0
