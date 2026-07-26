"""Shared strict models and boundary facts for diagnostics v1 artifacts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Annotated, ClassVar, Literal, Never, Self, TypeGuard

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from seektalent.diagnostics_identity import (
    NonNegativeSafeInteger,
    PositiveSafeInteger,
    RandomIdentity,
    Sha256,
    Sha256Ref,
)
from seektalent.diagnostics_errors import DiagnosticsReason, DiagnosticsSchemaError
from seektalent.diagnostics_registry import (
    CAUSE_CODES,
    CAUSE_KIND_SHAPES,
    DIAGNOSTIC_GAP_REASONS,
    REDACTION_RULES,
    SUPPORT_ACTION_INSTRUCTIONS,
    USER_ACTION_INSTRUCTIONS,
    require_reason_code,
    require_token,
)


CANONICAL_EVENT_V1 = "seektalent.canonical-event/v1"
FAILURE_ENVELOPE_V1 = "seektalent.failure-envelope/v1"
MACHINE_CAPABILITY_RECEIPT_V1 = "seektalent.machine-capability-receipt/v1"
STARTUP_RECEIPT_V1 = "seektalent.startup-receipt/v1"
OPERATION_EVIDENCE_V1 = "seektalent.operation-evidence/v1"
JOURNAL_APPEND_ACK_V1 = "seektalent.journal-append-ack/v1"

MAX_EVENT_BYTES = 16 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024
_REDACTION_PATH_RE = re.compile(r"root(?:\.(?:<field>|<item>|<redacted-field>))*")


class FrozenDict(dict[str, object]):
    """JSON-object-shaped mapping that cannot be changed after admission."""

    def __setitem__(self, key: str, value: object) -> Never:
        del key, value
        raise TypeError("diagnostics_frozen_mapping")

    def __delitem__(self, key: str) -> Never:
        del key
        raise TypeError("diagnostics_frozen_mapping")

    def clear(self) -> Never:
        raise TypeError("diagnostics_frozen_mapping")

    def pop(self, key: str, default: object = None) -> Never:
        del key, default
        raise TypeError("diagnostics_frozen_mapping")

    def popitem(self) -> Never:
        raise TypeError("diagnostics_frozen_mapping")

    def setdefault(self, key: str, default: object = None) -> Never:
        del key, default
        raise TypeError("diagnostics_frozen_mapping")

    def update(  # ty: ignore[invalid-method-override]
        self,
        other: Mapping[str, object] | Iterable[tuple[str, object]] = (),
        /,
        **kwargs: object,
    ) -> Never:
        del other, kwargs
        raise TypeError("diagnostics_frozen_mapping")

    def __ior__(  # ty: ignore[invalid-method-override]
        self,
        value: Mapping[str, object] | Iterable[tuple[str, object]],
    ) -> Never:
        del value
        raise TypeError("diagnostics_frozen_mapping")


def _is_plain_string_dict(value: object) -> TypeGuard[dict[str, object]]:
    return type(value) is dict and all(type(key) is str for key in value)


def _freeze(value: object) -> object:
    if _is_plain_string_dict(value):
        return FrozenDict({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    if type(value) is tuple:
        return tuple(_freeze(item) for item in value)
    return value


class StrictDiagnosticsModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def freeze_containers(self) -> Self:
        for name in type(self).model_fields:
            value = getattr(self, name)
            frozen = _freeze(value)
            if frozen is not value:
                object.__setattr__(self, name, frozen)
        return self


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
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        del _fields_set, values
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        del update, deep
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(validated=True)"

    __str__ = __repr__

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


class VersionedIdentityRefV1(StrictDiagnosticsModel):
    identity: RandomIdentity
    revision: PositiveSafeInteger


class HashedVersionedIdentityRefV1(VersionedIdentityRefV1):
    canonical_hash: Sha256


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
        requires_ref, requires_code, certainties = CAUSE_KIND_SHAPES[self.kind]
        if (
            (self.ref_id is not None) != requires_ref
            or (self.code is not None) != requires_code
            or self.certainty not in certainties
        ):
            raise ValueError("diagnostics_cause_ref_shape_mismatch")
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
        value = require_reason_code(value)
        if value not in DIAGNOSTIC_GAP_REASONS:
            raise ValueError("diagnostics_reason_not_gap")
        return value


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

    @model_validator(mode="after")
    def validate_instruction(self) -> Self:
        if USER_ACTION_INSTRUCTIONS[self.code] != self.instruction_key:
            raise ValueError("diagnostics_user_action_mismatch")
        return self


class SupportActionV1(StrictDiagnosticsModel):
    code: Literal["contact_support", "collect_diagnostics"]
    instruction_key: Literal["support.contact", "support.collect_diagnostics"]

    @model_validator(mode="after")
    def validate_instruction(self) -> Self:
        if SUPPORT_ACTION_INSTRUCTIONS[self.code] != self.instruction_key:
            raise ValueError("diagnostics_support_action_mismatch")
        return self
