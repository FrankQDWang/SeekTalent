"""Durable Liepin round plan shared by Runtime and the Source Port."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class _RoundPlanModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


class LiepinCompiledSearchRequestV1(_RoundPlanModel):
    query_terms: Annotated[tuple[str, ...], Field(max_length=64)]
    query_role: Literal["primary", "expansion"]
    keyword_query: Annotated[str, Field(min_length=1, max_length=4096)]
    adapter_notes: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    fetch_mode: Literal["summary", "detail"]
    page_size: Annotated[int, Field(ge=1, le=200)]
    provider_context: dict[str, str]
    cursor: Annotated[str, Field(min_length=1, max_length=4096)] | None = None


class LiepinRoundLaneWorkItemV1(_RoundPlanModel):
    lane_ordinal: Annotated[int, Field(ge=1, le=100)]
    logical_query_ordinal: Annotated[int, Field(ge=1, le=100)]
    target_ordinal: Annotated[int, Field(ge=1, le=100)]
    source_lane_run_id: Annotated[str, Field(min_length=1, max_length=256)]
    query_instance_id: Annotated[str, Field(min_length=1, max_length=96)]
    query_fingerprint: Annotated[str, Field(min_length=1, max_length=128)]
    query_role: Literal["exploit", "explore"]
    lane_type: Annotated[str, Field(min_length=1, max_length=64)]
    term_group_key: Annotated[str, Field(min_length=1, max_length=128)]
    primary_anchor_family_id: Annotated[
        str,
        Field(min_length=1, max_length=256),
    ]
    non_anchor_term_family_ids: Annotated[
        tuple[str, ...],
        Field(max_length=64),
    ]
    source_plan_version: Annotated[str, Field(min_length=1, max_length=128)]
    logical_query_terms: Annotated[tuple[str, ...], Field(max_length=64)]
    query_terms: Annotated[tuple[str, ...], Field(max_length=64)]
    keyword_query: Annotated[str, Field(min_length=1, max_length=4096)]
    logical_target_total: Annotated[int, Field(ge=1, le=200)]
    logical_requested_count: Annotated[int, Field(ge=1, le=200)]
    provider_scan_limit: Annotated[int, Field(ge=1, le=200)]
    unsupported_filter_reason_codes: Annotated[
        tuple[str, ...],
        Field(max_length=64),
    ] = ()
    compiled_search_request: LiepinCompiledSearchRequestV1 | None = None


class LiepinRoundWorkPlanV1(_RoundPlanModel):
    contract_version: Literal[
        "seektalent.source.liepin-round-work-plan/v1"
    ]
    runtime_run_id: Annotated[str, Field(min_length=1, max_length=96)]
    base_checkpoint_id: Annotated[str, Field(min_length=1, max_length=96)]
    accepted_requirement_revision_id: Annotated[
        str,
        Field(min_length=1, max_length=96),
    ]
    requirement_sheet_hash: Annotated[
        str,
        Field(pattern=r"^[0-9a-f]{64}$"),
    ]
    source_plan_id: Annotated[str, Field(min_length=1, max_length=256)]
    round_no: Annotated[int, Field(ge=1)]
    job_title: Annotated[str, Field(min_length=1, max_length=4096)]
    jd: Annotated[str, Field(max_length=500_000)]
    notes: Annotated[str, Field(max_length=100_000)] = ""
    requirement_sheet: dict[str, JsonValue]
    source_context: dict[str, JsonValue]
    source_budget_policy: dict[str, JsonValue]
    resume_context: dict[str, JsonValue]
    detail_claim_aware: bool
    lanes: Annotated[
        tuple[LiepinRoundLaneWorkItemV1, ...],
        Field(min_length=1, max_length=100),
    ]

    @model_validator(mode="after")
    def validate_lane_order(self) -> Self:
        ordinals = [lane.lane_ordinal for lane in self.lanes]
        identities = [
            (lane.source_lane_run_id, lane.query_instance_id)
            for lane in self.lanes
        ]
        if (
            ordinals != list(range(1, len(self.lanes) + 1))
            or len(identities) != len(set(identities))
        ):
            raise ValueError(
                "liepin_round_work_plan_lane_identity_invalid"
            )
        return self


__all__ = [
    "LiepinCompiledSearchRequestV1",
    "LiepinRoundLaneWorkItemV1",
    "LiepinRoundWorkPlanV1",
]
