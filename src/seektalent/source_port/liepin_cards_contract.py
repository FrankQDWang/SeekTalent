"""Pure-data contract for one Liepin cards browser operation."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, field_validator

from seektalent.source_port.wire_primitives import StrictWireModel, canonical_json_bytes


class _CardsModel(StrictWireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


class LiepinCardsOperationRequestV1(_CardsModel):
    """Semantic request only; delivery and authorization live in the frame."""

    contract_version: Literal["seektalent.source.liepin-cards.request/v1"]
    runtime_run_id: Annotated[str, Field(min_length=1, max_length=96)]
    source_lane_run_id: Annotated[str, Field(min_length=1, max_length=256)]
    query_instance_id: Annotated[str, Field(min_length=1, max_length=96)]
    keyword_query: Annotated[str, Field(min_length=1, max_length=4096)]
    max_pages: Annotated[int, Field(ge=1, le=20)]
    max_cards: Annotated[int, Field(ge=1, le=200)]
    native_filters: dict[str, JsonValue] | None = None

    @field_validator("runtime_run_id", "source_lane_run_id", "query_instance_id", "keyword_query")
    @classmethod
    def reject_untrimmed_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("liepin_cards_request_text_untrimmed")
        return value


class LiepinCardsArtifactV1(_CardsModel):
    contract_version: Literal["seektalent.source.liepin-cards.artifact/v1"]
    operation_id: Annotated[str, Field(min_length=1, max_length=96)]
    canonical_request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["succeeded", "partial", "failed"]
    cards: tuple[dict[str, JsonValue], ...] = ()
    cards_seen: Annotated[int, Field(ge=0, le=10_000)] = 0
    safe_reason_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None


class LiepinCardsObservationV1(_CardsModel):
    """Compact terminal fact safe to persist in the command journal."""

    contract_version: Literal["seektalent.source.liepin-cards.observation/v1"]
    operation_id: Annotated[str, Field(min_length=1, max_length=96)]
    canonical_request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    disposition: Literal["completed", "partial", "failed", "reconciliation_unknown"]
    artifact_ref: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    artifact_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    cards_seen: Annotated[int, Field(ge=0, le=10_000)] = 0
    card_count: Annotated[int, Field(ge=0, le=200)] = 0
    safe_reason_code: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    producer_generation: Annotated[int, Field(ge=1)]


def canonical_liepin_cards_request_bytes(request: LiepinCardsOperationRequestV1) -> bytes:
    if type(request) is not LiepinCardsOperationRequestV1:
        raise TypeError("request must be LiepinCardsOperationRequestV1")
    return canonical_json_bytes(request.model_dump(mode="json"))


def canonical_liepin_cards_request_hash(request: LiepinCardsOperationRequestV1) -> str:
    return sha256(canonical_liepin_cards_request_bytes(request)).hexdigest()


def stable_liepin_cards_operation_id(request: LiepinCardsOperationRequestV1) -> str:
    if type(request) is not LiepinCardsOperationRequestV1:
        raise TypeError("request must be LiepinCardsOperationRequestV1")
    identity = canonical_json_bytes(
        {
            "contract": "seektalent.source.liepin-cards.identity/v1",
            "runtime_run_id": request.runtime_run_id,
            "source_lane_run_id": request.source_lane_run_id,
            "query_instance_id": request.query_instance_id,
            "operation_kind": "cards",
        }
    )
    return f"cards_{sha256(identity).hexdigest()[:48]}"


__all__ = [
    "LiepinCardsArtifactV1",
    "LiepinCardsObservationV1",
    "LiepinCardsOperationRequestV1",
    "canonical_liepin_cards_request_bytes",
    "canonical_liepin_cards_request_hash",
    "stable_liepin_cards_operation_id",
]
