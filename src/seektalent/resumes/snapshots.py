from __future__ import annotations

from typing import Any

from seektalent.storage.json import sha256_json


def canonical_resume_snapshot_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    return raw_payload


def snapshot_sha256(raw_payload: dict[str, Any]) -> str:
    return sha256_json(canonical_resume_snapshot_payload(raw_payload))
