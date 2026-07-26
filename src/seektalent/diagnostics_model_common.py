"""Shared strict models and boundary facts for diagnostics v1 artifacts."""

from __future__ import annotations

import re
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from seektalent.diagnostics_identity import (
    NonNegativeSafeInteger,
    PositiveSafeInteger,
    RandomIdentity,
    Sha256Ref,
)
from seektalent.diagnostics_errors import DiagnosticsReason, DiagnosticsSchemaError
from seektalent.diagnostics_registry import CAUSE_CODES, REDACTION_RULES, require_reason_code, require_token


CANONICAL_EVENT_V1 = "seektalent.canonical-event/v1"
FAILURE_ENVELOPE_V1 = "seektalent.failure-envelope/v1"
MACHINE_CAPABILITY_RECEIPT_V1 = "seektalent.machine-capability-receipt/v1"
STARTUP_RECEIPT_V1 = "seektalent.startup-receipt/v1"
OPERATION_EVIDENCE_V1 = "seektalent.operation-evidence/v1"
JOURNAL_APPEND_ACK_V1 = "seektalent.journal-append-ack/v1"

MAX_EVENT_BYTES = 16 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024
_REDACTION_PATH_RE = re.compile(r"root(?:\.(?:<field>|<item>|<redacted-field>))*")


class StrictDiagnosticsModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class ArtifactModel(StrictDiagnosticsModel):
    _max_raw_bytes: ClassVar[int] = MAX_ARTIFACT_BYTES

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        from_attributes: bool | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        del obj, strict, extra, from_attributes, context, by_alias, by_name
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)

    @classmethod
    def from_trusted_fields(cls, **values: object) -> Self:
        return BaseModel.model_validate.__func__(cls, values, strict=True, extra="forbid")

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        del strict, extra
        if not isinstance(json_data, bytes):
            raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)
        from seektalent.diagnostics_bytes import parse_artifact_bytes

        return parse_artifact_bytes(
            cls,
            json_data,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


class RedactionReportV1(StrictDiagnosticsModel):
    rule: str
    path: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    count: PositiveSafeInteger

    @field_validator("rule")
    @classmethod
    def validate_rule(cls, value: str) -> str:
        return require_token(value, REDACTION_RULES, "redaction_rule")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if _REDACTION_PATH_RE.fullmatch(value) is None:
            raise ValueError("diagnostics_invalid_redaction_path")
        return value


class RedactionStateV1(StrictDiagnosticsModel):
    policy_version: Literal["seektalent.diagnostics-redaction/v1"]
    result: Literal["safe", "redacted"]
    redacted_field_count: NonNegativeSafeInteger
    report: Annotated[tuple[RedactionReportV1, ...], Field(max_length=32)]

    @field_validator("report", mode="before")
    @classmethod
    def decode_report(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if sum(item.count for item in self.report) > self.redacted_field_count:
            raise ValueError("diagnostics_redaction_count_mismatch")
        if (self.redacted_field_count == 0) != (self.result == "safe"):
            raise ValueError("diagnostics_redaction_result_mismatch")
        return self


class AuthorityRefsV1(StrictDiagnosticsModel):
    runtime_attempt_fence_ref: Sha256Ref | None = None
    profile_binding_generation: PositiveSafeInteger | None = None
    browser_control_fence_ref: Sha256Ref | None = None


class CorrelationRefsV1(StrictDiagnosticsModel):
    browser_control_scope_id: RandomIdentity | None = None
    sidecar_command_ref: RandomIdentity | None = None


class CauseRefV1(StrictDiagnosticsModel):
    kind: Literal["event", "failure", "durable_fact", "external_code", "unknown"]
    ref_id: RandomIdentity | None
    code: Annotated[str, Field(strict=True, min_length=1, max_length=96)] | None
    certainty: Literal["observed", "derived", "unknown"]
    derivation_rule_id: Sha256Ref | None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str | None) -> str | None:
        if value is not None and value not in CAUSE_CODES:
            raise ValueError("diagnostics_invalid_cause_code")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "unknown" and self.ref_id is not None:
            raise ValueError("diagnostics_unknown_cause_has_ref")
        if self.kind != "unknown" and self.ref_id is None and self.code is None:
            raise ValueError("diagnostics_cause_ref_incomplete")
        if (self.certainty == "derived") != (self.derivation_rule_id is not None):
            raise ValueError("diagnostics_derivation_rule_mismatch")
        return self


class BoundaryFactV1(StrictDiagnosticsModel):
    state: Literal["not_started", "not_observed", "observed", "unknown"]
    ref: RandomIdentity | None

    @model_validator(mode="after")
    def validate_ref(self) -> Self:
        if self.state == "observed" and self.ref is None:
            raise ValueError("diagnostics_observed_boundary_ref_required")
        if self.state != "observed" and self.ref is not None:
            raise ValueError("diagnostics_unobserved_boundary_has_ref")
        return self


class BoundaryFactsV1(StrictDiagnosticsModel):
    acceptance: BoundaryFactV1
    dispatch: BoundaryFactV1
    side_effect: BoundaryFactV1
    result_persistence: BoundaryFactV1
    main_commit: BoundaryFactV1
    cleanup: BoundaryFactV1


class DiagnosticGapV1(StrictDiagnosticsModel):
    reason_code: str
    counter: PositiveSafeInteger

    @field_validator("reason_code")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return require_reason_code(value)


class SourceCoverageV1(StrictDiagnosticsModel):
    source_id: Literal["liepin"]
    state: Literal["started", "completed", "partial", "unknown"]
    safe_count: NonNegativeSafeInteger


class UserActionV1(StrictDiagnosticsModel):
    code: Literal["reauthenticate", "restart_component", "retry_later"]
    instruction_key: Literal[
        "provider.reauthenticate", "component.restart", "operation.retry_later"
    ]
    affected_scope_ref: RandomIdentity | None = None


class SupportActionV1(StrictDiagnosticsModel):
    code: Literal["contact_support", "collect_diagnostics"]
    instruction_key: Literal["support.contact", "support.collect_diagnostics"]
