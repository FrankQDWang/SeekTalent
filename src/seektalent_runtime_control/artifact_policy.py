from __future__ import annotations

from typing import Literal


RuntimeArtifactOutputMode = Literal["dev_full_local", "prod_compact_local", "off_except_db"]

SENSITIVE_ARTIFACT_KEYS = {
    "authHeaders",
    "browserStorage",
    "cookie",
    "cookies",
    "fullPrompt",
    "messages",
    "prompt",
    "rawProviderPayload",
    "rawResumeText",
    "requestHeaders",
    "responseBody",
}


class RuntimeArtifactPolicy:
    def __init__(self, mode: RuntimeArtifactOutputMode = "dev_full_local") -> None:
        self.mode = mode

    @property
    def writes_local_debug_artifacts(self) -> bool:
        return self.mode != "off_except_db"

    @property
    def compacts_sensitive_payloads(self) -> bool:
        return self.mode == "prod_compact_local"

    def filter_payload(self, value: object) -> object:
        if self.mode == "dev_full_local":
            return value
        return _redact_sensitive(value)


def normalize_artifact_output_mode(value: object) -> RuntimeArtifactOutputMode:
    if value == "dev_full_local":
        return "dev_full_local"
    if value == "prod_compact_local":
        return "prod_compact_local"
    if value == "off_except_db":
        return "off_except_db"
    return "dev_full_local"


def _redact_sensitive(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        redacted_count = 0
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if key in SENSITIVE_ARTIFACT_KEYS:
                redacted_count += 1
            else:
                result[key] = _redact_sensitive(item)
        if redacted_count:
            result["_redactedSensitiveFieldCount"] = redacted_count
        return result
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value
