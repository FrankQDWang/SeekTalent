"""Content-addressed private request artifacts for cold detail-step resume."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from pydantic import ValidationError

from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsOperationRequestV1,
)
from seektalent.source_port._atomic_artifact import (
    publish_content_addressed_bytes,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes


_REF_PREFIX = "liepin-details-request://sha256/"


@dataclass(frozen=True, slots=True)
class LiepinDetailsRequestArtifactWrite:
    artifact_ref: str
    artifact_hash: str
    payload_size_bytes: int
    write_duration_ms: float
    published: bool


def write_liepin_details_request_artifact(
    root: Path,
    request: LiepinDetailsOperationRequestV1,
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> LiepinDetailsRequestArtifactWrite:
    if type(request) is not LiepinDetailsOperationRequestV1:
        raise TypeError(
            "request must be LiepinDetailsOperationRequestV1"
        )
    started = perf_counter()
    payload = canonical_json_bytes(request.model_dump(mode="json"))
    digest = sha256(payload).hexdigest()
    published = publish_content_addressed_bytes(
        root,
        payload,
        digest,
        fault_injector=fault_injector,
    )
    return LiepinDetailsRequestArtifactWrite(
        artifact_ref=f"{_REF_PREFIX}{digest}",
        artifact_hash=digest,
        payload_size_bytes=len(payload),
        write_duration_ms=(perf_counter() - started) * 1000,
        published=published,
    )


def read_liepin_details_request_artifact(
    root: Path,
    artifact_ref: str,
    *,
    expected_hash: str,
) -> LiepinDetailsOperationRequestV1:
    if (
        not artifact_ref.startswith(_REF_PREFIX)
        or artifact_ref.removeprefix(_REF_PREFIX) != expected_hash
        or len(expected_hash) != 64
    ):
        raise ValueError(
            "liepin_details_request_artifact_ref_invalid"
        )
    raw = (
        root.resolve(strict=False) / f"{expected_hash}.json"
    ).read_bytes()
    if sha256(raw).hexdigest() != expected_hash:
        raise ValueError(
            "liepin_details_request_artifact_hash_mismatch"
        )
    try:
        request = LiepinDetailsOperationRequestV1.model_validate_json(
            raw,
            strict=True,
        )
    except ValidationError:
        raise ValueError(
            "liepin_details_request_artifact_invalid"
        ) from None
    if canonical_json_bytes(request.model_dump(mode="json")) != raw:
        raise ValueError(
            "liepin_details_request_artifact_noncanonical"
        )
    return request


__all__ = [
    "LiepinDetailsRequestArtifactWrite",
    "read_liepin_details_request_artifact",
    "write_liepin_details_request_artifact",
]
