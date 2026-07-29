"""Pure-data contract for one Liepin details browser operation."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from seektalent.source_port.wire_primitives import StrictWireModel, canonical_json_bytes


class _DetailsModel(StrictWireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


class LiepinDetailsOperationRequestV1(_DetailsModel):
    """Semantic request only; delivery and authorization live in the frame.

    Wire carries no raw detail URL. Sidecar resolves locators privately.
    """

    contract_version: Literal["seektalent.source.liepin-details.request/v1"]
    runtime_run_id: Annotated[str, Field(min_length=1, max_length=96)]
    source_lane_run_id: Annotated[str, Field(min_length=1, max_length=256)]
    query_instance_id: Annotated[str, Field(min_length=1, max_length=96)]
    card_ref: Annotated[str, Field(min_length=1, max_length=96)]
    rank: Annotated[int, Field(ge=1, le=100)]
    open_mode: Literal["resolve_locator", "cached_locator"]
    provider_candidate_key_hash: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ] | None = None
    expected_provider_candidate_key_hash: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ] | None = None

    @field_validator(
        "runtime_run_id",
        "source_lane_run_id",
        "query_instance_id",
        "card_ref",
    )
    @classmethod
    def reject_untrimmed_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("liepin_details_request_text_untrimmed")
        return value

    @model_validator(mode="after")
    def validate_open_mode_identity(self) -> Self:
        if self.open_mode == "resolve_locator":
            if self.provider_candidate_key_hash is not None:
                raise ValueError("liepin_details_resolve_hash_forbidden")
            return self
        if self.provider_candidate_key_hash is None:
            raise ValueError("liepin_details_open_hash_required")
        return self


class LiepinDetailsArtifactV1(_DetailsModel):
    contract_version: Literal["seektalent.source.liepin-details.artifact/v1"]
    operation_id: Annotated[str, Field(min_length=1, max_length=96)]
    canonical_request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["succeeded", "partial", "failed"]
    open_mode: Literal["resolve_locator", "cached_locator"]
    provider_candidate_key_hash: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ] | None = None
    rank: Annotated[int, Field(ge=1, le=100)]
    card_ref: Annotated[str, Field(min_length=1, max_length=96)]
    detail_url: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    resume: dict[str, JsonValue] | None = None
    action_attempted: Annotated[int, Field(ge=0, le=10)] = 0
    effect_posture: Literal["not_attempted", "attempted", "unknown"]
    safe_reason_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None


class LiepinDetailsObservationV1(_DetailsModel):
    """Compact terminal fact safe to persist in the command journal."""

    contract_version: Literal["seektalent.source.liepin-details.observation/v1"]
    operation_id: Annotated[str, Field(min_length=1, max_length=96)]
    canonical_request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    disposition: Literal["completed", "partial", "failed", "reconciliation_unknown"]
    artifact_ref: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    artifact_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    open_mode: Literal["resolve_locator", "cached_locator"]
    provider_candidate_key_hash: Annotated[
        str, Field(pattern=r"^[0-9a-f]{64}$")
    ] | None = None
    rank: Annotated[int, Field(ge=1, le=100)] = 1
    action_attempted: Annotated[int, Field(ge=0, le=10)] = 0
    effect_posture: Literal["not_attempted", "attempted", "unknown"]
    safe_reason_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    producer_generation: Annotated[int, Field(ge=1)]


def canonical_liepin_details_request_bytes(
    request: LiepinDetailsOperationRequestV1,
) -> bytes:
    if type(request) is not LiepinDetailsOperationRequestV1:
        raise TypeError("request must be LiepinDetailsOperationRequestV1")
    return canonical_json_bytes(request.model_dump(mode="json"))


def canonical_liepin_details_request_hash(
    request: LiepinDetailsOperationRequestV1,
) -> str:
    return sha256(canonical_liepin_details_request_bytes(request)).hexdigest()


def stable_liepin_details_operation_id(
    request: LiepinDetailsOperationRequestV1,
) -> str:
    if type(request) is not LiepinDetailsOperationRequestV1:
        raise TypeError("request must be LiepinDetailsOperationRequestV1")
    if request.open_mode == "resolve_locator":
        identity_body = {
            "contract": "seektalent.source.liepin-details.identity/v1",
            "runtime_run_id": request.runtime_run_id,
            "source_lane_run_id": request.source_lane_run_id,
            "query_instance_id": request.query_instance_id,
            "card_ref": request.card_ref,
            "rank": request.rank,
            "open_mode": "resolve_locator",
            "operation_kind": "details",
        }
    else:
        identity_body = {
            "contract": "seektalent.source.liepin-details.identity/v1",
            "runtime_run_id": request.runtime_run_id,
            "source_lane_run_id": request.source_lane_run_id,
            "query_instance_id": request.query_instance_id,
            "provider_candidate_key_hash": request.provider_candidate_key_hash,
            "operation_kind": "details",
        }
    return f"details_{sha256(canonical_json_bytes(identity_body)).hexdigest()[:48]}"


__all__ = [
    "LiepinDetailsArtifactV1",
    "LiepinDetailsObservationV1",
    "LiepinDetailsOperationRequestV1",
    "canonical_liepin_details_request_bytes",
    "canonical_liepin_details_request_hash",
    "stable_liepin_details_operation_id",
]
