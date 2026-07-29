"""Immutable content-addressed result artifacts for Liepin cards operations."""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from seektalent.source_port.liepin_cards_contract import LiepinCardsArtifactV1
from seektalent.source_port.wire_primitives import canonical_json_bytes


_REF_PREFIX = "liepin-cards://sha256/"


def write_liepin_cards_artifact(
    root: Path,
    artifact: LiepinCardsArtifactV1,
) -> tuple[str, str]:
    if type(artifact) is not LiepinCardsArtifactV1:
        raise TypeError("artifact must be LiepinCardsArtifactV1")
    payload = canonical_json_bytes(artifact.model_dump(mode="json"))
    digest = sha256(payload).hexdigest()
    artifact_root = root.resolve(strict=False)
    artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = artifact_root / f"{digest}.json"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        if path.read_bytes() != payload:
            raise RuntimeError("liepin_cards_artifact_hash_conflict") from None
    else:
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _persist_directory(artifact_root)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return f"{_REF_PREFIX}{digest}", digest


def read_liepin_cards_artifact(
    root: Path,
    artifact_ref: str,
    *,
    expected_hash: str,
) -> LiepinCardsArtifactV1:
    if (
        not artifact_ref.startswith(_REF_PREFIX)
        or artifact_ref.removeprefix(_REF_PREFIX) != expected_hash
        or len(expected_hash) != 64
    ):
        raise ValueError("liepin_cards_artifact_ref_invalid")
    raw = (root.resolve(strict=False) / f"{expected_hash}.json").read_bytes()
    if sha256(raw).hexdigest() != expected_hash:
        raise ValueError("liepin_cards_artifact_hash_mismatch")
    try:
        artifact = LiepinCardsArtifactV1.model_validate_json(raw, strict=True)
    except ValidationError:
        raise ValueError("liepin_cards_artifact_invalid") from None
    if canonical_json_bytes(artifact.model_dump(mode="json")) != raw:
        raise ValueError("liepin_cards_artifact_noncanonical")
    return artifact


def _persist_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["read_liepin_cards_artifact", "write_liepin_cards_artifact"]
