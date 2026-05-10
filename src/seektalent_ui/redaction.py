from __future__ import annotations

from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"

FORBIDDEN_PAYLOAD_TOKENS = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "authorization",
    "authheader",
    "bearer",
    "browsercontext",
    "cdp",
    "cookie",
    "localstorage",
    "password",
    "playwright",
    "raw_profile",
    "raw_resume",
    "rawpayload",
    "refresh_token",
    "refreshtoken",
    "secret",
    "sessionstorage",
    "set-cookie",
    "storagestate",
    "token",
    "websocketdebuggerurl",
    "wsendpoint",
}


def redact_event_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        redacted_index = 0
        for key, item in value.items():
            key_text = str(key)
            if _contains_forbidden_token(key_text):
                redacted[f"redacted_{redacted_index}"] = REDACTED
                redacted_index += 1
                continue
            redacted[key_text] = redact_event_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_event_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_event_payload(item) for item in value]
    if isinstance(value, str):
        if _contains_forbidden_token(value):
            return REDACTED
        return value
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)


def redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    if _contains_forbidden_token(value):
        return REDACTED
    return value


def _contains_forbidden_token(value: str) -> bool:
    compact = value.replace("_", "").replace("-", "").casefold()
    lowered = value.casefold()
    return any(token in lowered or token.replace("_", "").replace("-", "") in compact for token in FORBIDDEN_PAYLOAD_TOKENS)
