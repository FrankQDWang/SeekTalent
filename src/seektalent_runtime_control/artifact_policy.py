from __future__ import annotations

import re
from typing import Literal


RuntimeArtifactOutputMode = Literal["prod", "dev", "debug_full_local"]
COMPACT_ARTIFACT_STRING_LIMIT = 240
COMPACT_ARTIFACT_LIST_LIMIT = 20

SENSITIVE_ARTIFACT_KEY_TOKENS = {
    "accesstoken",
    "authorization",
    "authheaders",
    "browserstorage",
    "cookie",
    "cookies",
    "fullprompt",
    "fullPrompt",
    "messages",
    "prompt",
    "providerpayload",
    "refreshtoken",
    "rawprovider",
    "rawproviderpayload",
    "rawresume",
    "rawresumetext",
    "requestheaders",
    "responsebody",
    "secret",
    "sessiontoken",
    "setcookie",
    "structuredoutput",
    "structuredoutputs",
    "token",
    "xapikey",
}
SENSITIVE_ARTIFACT_KEY_FRAGMENTS = (
    "auth",
    "apikey",
    "cookie",
    "password",
    "rawresponse",
    "rawoutput",
    "rawstructuredoutput",
    "secret",
)
SENSITIVE_ARTIFACT_VALUE_PATTERNS = (
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\b(?:access[_-]?token|refresh[_-]?token|session[_-]?token|api[_-]?key|password|secret)\s*[:=]", re.IGNORECASE),
)
REDACTED_ARTIFACT_VALUE = "[redacted]"


class RuntimeArtifactPolicy:
    def __init__(self, mode: RuntimeArtifactOutputMode = "dev") -> None:
        self.mode = mode

    @property
    def writes_local_debug_artifacts(self) -> bool:
        return self.mode != "prod"

    @property
    def compacts_sensitive_payloads(self) -> bool:
        return self.mode != "debug_full_local"

    @property
    def writes_runtime_public_event_mirror(self) -> bool:
        return self.mode in {"dev", "debug_full_local"}

    @property
    def retention_metadata(self) -> dict[str, object]:
        if self.mode == "debug_full_local":
            return {
                "retention_ttl_class": "debug_short",
                "max_bytes": 20_000_000,
                "delete_eligible": True,
                "safety_class": "artifact_debug_full",
                "support_bundle_only": False,
            }
        if self.mode == "dev":
            return {
                "retention_ttl_class": "dev_debug",
                "max_bytes": 5_000_000,
                "delete_eligible": True,
                "safety_class": "artifact_debug",
                "support_bundle_only": False,
            }
        return {
            "retention_ttl_class": "none",
            "max_bytes": 0,
            "delete_eligible": False,
            "safety_class": "product_db_only",
            "support_bundle_only": False,
        }

    def filter_payload(self, value: object) -> object:
        if self.mode == "debug_full_local":
            return value
        return _redact_sensitive(value)


def normalize_artifact_output_mode(value: object) -> RuntimeArtifactOutputMode:
    if value == "prod":
        return "prod"
    if value == "dev":
        return "dev"
    if value == "debug_full_local":
        return "debug_full_local"
    raise ValueError("runtime_artifact_output_mode_unsupported")


def _redact_sensitive(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        redacted_count = 0
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if _sensitive_key(key):
                redacted_count += 1
            else:
                result[key] = _redact_sensitive(item)
        if redacted_count:
            result["_redactedSensitiveFieldCount"] = redacted_count
        return result
    if isinstance(value, list):
        capped = [_redact_sensitive(item) for item in value[:COMPACT_ARTIFACT_LIST_LIMIT]]
        if len(value) > COMPACT_ARTIFACT_LIST_LIMIT:
            capped.append({"_truncatedCount": len(value) - COMPACT_ARTIFACT_LIST_LIMIT})
        return capped
    if isinstance(value, str) and _sensitive_string(value):
        return REDACTED_ARTIFACT_VALUE
    if isinstance(value, str) and len(value) > COMPACT_ARTIFACT_STRING_LIMIT:
        return f"{value[:COMPACT_ARTIFACT_STRING_LIMIT].rstrip()}..."
    return value


def _sensitive_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return (
        normalized in SENSITIVE_ARTIFACT_KEY_TOKENS
        or normalized.endswith("token")
        or any(fragment in normalized for fragment in SENSITIVE_ARTIFACT_KEY_FRAGMENTS)
    )


def _sensitive_string(value: str) -> bool:
    return any(pattern.search(value) for pattern in SENSITIVE_ARTIFACT_VALUE_PATTERNS)
