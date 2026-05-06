from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from seektalent.artifacts import ArtifactSession, atomic_write_text, safe_artifact_path

SAFE_SNAPSHOT_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class RawPayloadArtifact:
    logical_name: str
    relative_path: str
    content_sha256: str
    size_bytes: int


def write_raw_payload_artifact(
    session: ArtifactSession,
    snapshot_sha256: str,
    raw_payload: dict[str, Any],
) -> RawPayloadArtifact:
    if SAFE_SNAPSHOT_SHA256_RE.fullmatch(snapshot_sha256) is None:
        raise ValueError("snapshot_sha256 must be a lowercase 64-character hex string")

    logical_name = f"corpus.raw_payloads.{snapshot_sha256}"
    relative_path = f"raw_payloads/{snapshot_sha256}.json"
    text = json.dumps(raw_payload, ensure_ascii=False, indent=2, sort_keys=True)
    encoded = text.encode("utf-8")
    path = safe_artifact_path(session.root, relative_path)

    atomic_write_text(path, text)
    session.register_path(
        logical_name,
        relative_path,
        content_type="application/json",
        schema_version="v1",
    )
    return RawPayloadArtifact(
        logical_name=logical_name,
        relative_path=relative_path,
        content_sha256=sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
    )
