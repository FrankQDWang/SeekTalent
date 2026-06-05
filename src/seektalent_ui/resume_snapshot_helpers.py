from __future__ import annotations

import json


_MATERIALIZATION_ALLOWED_USES = {
    "search",
    "recruiting",
    "internal_materialization",
    "workspace_recruiting_record",
}
_BLOCKED_REDACTION_STATUSES = {"blocked", "forbidden", "failed"}
_FORBIDDEN_SAFE_TEXT_TOKENS = (
    "cookie",
    "authorization",
    "bearer ",
    "set-cookie",
    "authheader",
    "storagestate",
    "localstorage",
    "sessionstorage",
    "cdp",
    "websocket",
    "websocketdebuggerurl",
    "wsendpoint",
    "run_dir",
    "artifact",
    "provider_account_hash",
    "provider-secret",
    "source-secret",
    "token=",
    "ticket=",
)


def json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return {str(key): item for key, item in parsed.items()} if isinstance(parsed, dict) else {}
    return {}


def json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def snapshot_materialization_allowed(doc: dict[str, object]) -> bool:
    if not bool(doc.get("internal_materialization_eligible")):
        return False
    redaction_status = str(doc.get("redaction_status") or "").strip().casefold()
    if redaction_status in _BLOCKED_REDACTION_STATUSES:
        return False
    allowed_uses = {str(value).strip().casefold() for value in json_list(doc.get("allowed_uses_json"))}
    return bool(allowed_uses.intersection(_MATERIALIZATION_ALLOWED_USES))


def safe_snapshot_text(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if any(token in lowered for token in _FORBIDDEN_SAFE_TEXT_TOKENS):
        return None
    if "://" in lowered and ("http://" in lowered or "https://" in lowered or "ws://" in lowered or "wss://" in lowered):
        return None
    return text[:max_length]
