"""Content-addressed work plans for resumable Liepin detail lanes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, ValidationError, model_validator

from seektalent.source_port.wire_primitives import (
    StrictWireModel,
    canonical_json_bytes,
)
from seektalent.source_port._atomic_artifact import (
    publish_content_addressed_bytes,
)


_REF_PREFIX = "liepin-detail-work-plan://sha256/"


class _WorkPlanModel(StrictWireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


class LiepinDetailWorkItemV1(_WorkPlanModel):
    rank: Annotated[int, Field(ge=1, le=100)]
    card_ref: Annotated[str, Field(min_length=1, max_length=96)]
    provider_candidate_key_hash: Annotated[
        str,
        Field(pattern=r"^[0-9a-f]{64}$"),
    ] | None = None


class LiepinDetailWorkPlanV1(_WorkPlanModel):
    contract_version: Literal[
        "seektalent.source.liepin-detail-work-plan/v1"
    ]
    runtime_run_id: Annotated[str, Field(min_length=1, max_length=96)]
    source_plan_id: Annotated[str, Field(min_length=1, max_length=256)]
    source_lane_run_id: Annotated[
        str,
        Field(min_length=1, max_length=256),
    ]
    round_no: Annotated[int, Field(ge=1)]
    query_instance_id: Annotated[str, Field(min_length=1, max_length=96)]
    query_fingerprint: Annotated[str, Field(min_length=1, max_length=128)]
    query_role: Literal["exploit", "explore"]
    query_terms: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    keyword_query: Annotated[str, Field(min_length=1, max_length=4096)]
    requested_count: Annotated[int, Field(ge=1, le=100)]
    max_pages: Annotated[int, Field(ge=1, le=20)]
    max_cards: Annotated[int, Field(ge=1, le=200)]
    phase: Literal["locators", "captures"]
    claim_aware: bool
    cards_artifact_ref: Annotated[str, Field(min_length=1, max_length=256)]
    cards_artifact_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    items: Annotated[tuple[LiepinDetailWorkItemV1, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def validate_items(self) -> Self:
        ranks = [item.rank for item in self.items]
        if ranks != sorted(set(ranks)):
            raise ValueError("liepin_detail_work_plan_rank_invalid")
        if self.phase == "locators":
            if any(
                item.provider_candidate_key_hash is not None
                for item in self.items
            ):
                raise ValueError(
                    "liepin_detail_locator_plan_hash_forbidden"
                )
        elif any(
            item.provider_candidate_key_hash is None
            for item in self.items
        ):
            raise ValueError("liepin_detail_capture_plan_hash_missing")
        return self


@dataclass(frozen=True, slots=True)
class LiepinDetailWorkPlanArtifactWrite:
    artifact_ref: str
    artifact_hash: str
    payload_size_bytes: int
    write_duration_ms: float
    published: bool


def write_liepin_detail_work_plan_artifact(
    root: Path,
    plan: LiepinDetailWorkPlanV1,
) -> LiepinDetailWorkPlanArtifactWrite:
    if type(plan) is not LiepinDetailWorkPlanV1:
        raise TypeError("plan must be LiepinDetailWorkPlanV1")
    started = perf_counter()
    payload = canonical_json_bytes(plan.model_dump(mode="json"))
    digest = sha256(payload).hexdigest()
    published = publish_content_addressed_bytes(
        root,
        payload,
        digest,
    )
    return LiepinDetailWorkPlanArtifactWrite(
        artifact_ref=f"{_REF_PREFIX}{digest}",
        artifact_hash=digest,
        payload_size_bytes=len(payload),
        write_duration_ms=(perf_counter() - started) * 1000,
        published=published,
    )


def read_liepin_detail_work_plan_artifact(
    root: Path,
    artifact_ref: str,
    *,
    expected_hash: str,
) -> LiepinDetailWorkPlanV1:
    if (
        not artifact_ref.startswith(_REF_PREFIX)
        or artifact_ref.removeprefix(_REF_PREFIX) != expected_hash
        or len(expected_hash) != 64
    ):
        raise ValueError("liepin_detail_work_plan_artifact_ref_invalid")
    raw = (root.resolve(strict=False) / f"{expected_hash}.json").read_bytes()
    if sha256(raw).hexdigest() != expected_hash:
        raise ValueError("liepin_detail_work_plan_artifact_hash_mismatch")
    try:
        plan = LiepinDetailWorkPlanV1.model_validate_json(raw, strict=True)
    except ValidationError:
        raise ValueError("liepin_detail_work_plan_artifact_invalid") from None
    if canonical_json_bytes(plan.model_dump(mode="json")) != raw:
        raise ValueError("liepin_detail_work_plan_artifact_noncanonical")
    return plan


__all__ = [
    "LiepinDetailWorkItemV1",
    "LiepinDetailWorkPlanArtifactWrite",
    "LiepinDetailWorkPlanV1",
    "read_liepin_detail_work_plan_artifact",
    "write_liepin_detail_work_plan_artifact",
]
