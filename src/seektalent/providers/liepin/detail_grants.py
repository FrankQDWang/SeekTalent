from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


NonEmptyStr = Annotated[str, Field(min_length=1)]


class LiepinBoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LiepinDetailFailureCode(StrEnum):
    DETAIL_OPEN_GRANT_MISSING = "detail_open_grant_missing"
    DETAIL_OPEN_GRANT_EXPIRED = "detail_open_grant_expired"
    DETAIL_OPEN_GRANT_CANDIDATE_MISMATCH = "detail_open_grant_candidate_mismatch"
    DETAIL_OPEN_GRANT_SOURCE_RUN_MISMATCH = "detail_open_grant_source_run_mismatch"


def require_timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class DetailOpenGrant(LiepinBoundaryModel):
    schema_version: Literal["detail-open-grant-v1"]
    approval_id: NonEmptyStr
    budget_reservation_id: NonEmptyStr
    candidate_ref: NonEmptyStr
    source_run_id: NonEmptyStr
    provider: Literal["liepin"]
    max_detail_opens: int = Field(default=1, ge=1, le=1)
    expires_at: datetime
    issued_by: Literal["workflow_runtime"]
    idempotency_key: NonEmptyStr
    grant_signature: str = Field(min_length=1, repr=False)

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return require_timezone_aware(value, "expires_at")
