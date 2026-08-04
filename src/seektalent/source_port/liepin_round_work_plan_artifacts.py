"""Content-addressed artifacts for durable Liepin round plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from pydantic import ValidationError

from seektalent.liepin_round_work_plan_contracts import (
    LiepinCompiledSearchRequestV1,
    LiepinRoundLaneWorkItemV1,
    LiepinRoundWorkPlanV1,
)
from seektalent.source_port._atomic_artifact import (
    publish_content_addressed_bytes,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes


_REF_PREFIX = "liepin-round-work-plan://sha256/"
MAX_ROUND_WORK_PLAN_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class LiepinRoundWorkPlanArtifactWrite:
    artifact_ref: str
    artifact_hash: str
    payload_size_bytes: int
    write_duration_ms: float
    published: bool


def write_liepin_round_work_plan_artifact(
    root: Path,
    plan: LiepinRoundWorkPlanV1,
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> LiepinRoundWorkPlanArtifactWrite:
    if type(plan) is not LiepinRoundWorkPlanV1:
        raise TypeError("plan must be LiepinRoundWorkPlanV1")
    started = perf_counter()
    payload = canonical_json_bytes(plan.model_dump(mode="json"))
    if len(payload) > MAX_ROUND_WORK_PLAN_BYTES:
        raise ValueError("liepin_round_work_plan_artifact_too_large")
    digest = sha256(payload).hexdigest()
    published = publish_content_addressed_bytes(
        root,
        payload,
        digest,
        fault_injector=fault_injector,
    )
    return LiepinRoundWorkPlanArtifactWrite(
        artifact_ref=f"{_REF_PREFIX}{digest}",
        artifact_hash=digest,
        payload_size_bytes=len(payload),
        write_duration_ms=(perf_counter() - started) * 1000,
        published=published,
    )


def read_liepin_round_work_plan_artifact(
    root: Path,
    artifact_ref: str,
    *,
    expected_hash: str,
) -> LiepinRoundWorkPlanV1:
    if (
        not artifact_ref.startswith(_REF_PREFIX)
        or artifact_ref.removeprefix(_REF_PREFIX) != expected_hash
        or len(expected_hash) != 64
    ):
        raise ValueError("liepin_round_work_plan_artifact_ref_invalid")
    path = root.resolve(strict=False) / f"{expected_hash}.json"
    with path.open("rb") as handle:
        raw = handle.read(MAX_ROUND_WORK_PLAN_BYTES + 1)
    if len(raw) > MAX_ROUND_WORK_PLAN_BYTES:
        raise ValueError("liepin_round_work_plan_artifact_too_large")
    if sha256(raw).hexdigest() != expected_hash:
        raise ValueError("liepin_round_work_plan_artifact_hash_mismatch")
    try:
        plan = LiepinRoundWorkPlanV1.model_validate_json(raw, strict=True)
    except ValidationError:
        raise ValueError("liepin_round_work_plan_artifact_invalid") from None
    if canonical_json_bytes(plan.model_dump(mode="json")) != raw:
        raise ValueError("liepin_round_work_plan_artifact_noncanonical")
    return plan


__all__ = [
    "MAX_ROUND_WORK_PLAN_BYTES",
    "LiepinCompiledSearchRequestV1",
    "LiepinRoundLaneWorkItemV1",
    "LiepinRoundWorkPlanArtifactWrite",
    "LiepinRoundWorkPlanV1",
    "read_liepin_round_work_plan_artifact",
    "write_liepin_round_work_plan_artifact",
]
