from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Literal


StreamSubjectType = Literal["connection", "run"]


def hmac_provider_account_hash(secret: str, provider_account_identity_hint: str) -> str:
    identity = provider_account_identity_hint.strip().lower()
    digest = hmac.new(secret.encode("utf-8"), identity.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def issue_stream_token(
    *,
    secret: str,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    subject_type: StreamSubjectType,
    subject_id: str,
    ttl_seconds: int = 60,
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "exp": int(time.time()) + ttl_seconds,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded_payload = _encode_json(payload)
    signature = _sign(secret, encoded_payload)
    return f"{encoded_payload}.{signature}"


def verify_stream_token(
    token: str,
    *,
    secret: str,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    subject_type: StreamSubjectType,
    subject_id: str,
) -> bool:
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError:
        return False
    expected_signature = _sign(secret, encoded_payload)
    if not hmac.compare_digest(signature, expected_signature):
        return False
    try:
        payload = json.loads(_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    expected = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return False
    return int(payload.get("exp", 0)) >= int(time.time())


def read_stream_token_payload(token: str, *, secret: str) -> dict[str, object] | None:
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError:
        return None
    expected_signature = _sign(secret, encoded_payload)
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        payload = json.loads(_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def _encode_json(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(secret: str, encoded_payload: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
