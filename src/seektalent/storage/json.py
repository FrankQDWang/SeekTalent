from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_json(payload: object) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()
