"""Canonical event and failure-envelope v1 models."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import field_validator, model_validator

from seektalent.diagnostics_identity import (
    PositiveSafeInteger,
    RandomIdentity,
    Sha256Ref,
    SpanId,
    TraceId,
    UtcTimestamp,
)
from seektalent.diagnostics_model_common import (
    MAX_EVENT_BYTES,
    ArtifactModel,
    AuthorityRefsV1,
    BoundaryFactsV1,
    CauseRefV1,
    CorrelationRefsV1,
    DiagnosticGapV1,
    RedactionStateV1,
    SourceCoverageV1,
    SupportActionV1,
    UserActionV1,
)
from seektalent.diagnostics_registry import (
    ARRIVAL_CLASSES,
    AUTHORITY_REF_FIELDS,
    COMPONENTS,
    DOMAINS,
    EVENT_DEFINITIONS,
    EXTERNAL_CAUSE_REASONS,
    FAILURE_DETAIL_CONTRACTS,
    FAILURE_KINDS,
    PHASES,
    REASON_DEFINITIONS,
    SEVERITIES,
    STATUSES,
    require_event_name,
    require_reason_code,
    require_token,
)
from seektalent.diagnostics_scalar import validate_scalar
from seektalent.product_outcome import ProductOutcome


class CanonicalEventV1(ArtifactModel):
    _max_raw_bytes = MAX_EVENT_BYTES

    schema_version: Literal["seektalent.canonical-event/v1"]
    event_id: RandomIdentity
    journal_seq: PositiveSafeInteger
    correlation_id: RandomIdentity | None
    diagnostic_trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None
    caused_by_event_id: RandomIdentity | None
    run_id: RandomIdentity | None
    operation_id: RandomIdentity | None
    attempt_no: PositiveSafeInteger | None
    component: str
    component_instance_id: RandomIdentity
    component_event_seq: PositiveSafeInteger
    release_manifest_ref: Sha256Ref | None
    component_build_ref: Sha256Ref
    event_name: str
    phase: str
    severity: str
    status: str
    arrival_class: str
    reason_code: str | None
    occurred_at: UtcTimestamp
    observed_at: UtcTimestamp
    authority_refs: AuthorityRefsV1
    correlation_refs: CorrelationRefsV1
    attributes: dict[str, object]
    redaction: RedactionStateV1

    @field_validator("component")
    @classmethod
    def validate_component(cls, value: str) -> str:
        return require_token(value, COMPONENTS, "component")

    @field_validator("event_name")
    @classmethod
    def validate_event_name(cls, value: str) -> str:
        return require_event_name(value)

    @field_validator("phase")
    @classmethod
    def validate_phase(cls, value: str) -> str:
        return require_token(value, PHASES, "phase")

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, value: str) -> str:
        return require_token(value, SEVERITIES, "severity")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return require_token(value, STATUSES, "status")

    @field_validator("arrival_class")
    @classmethod
    def validate_arrival(cls, value: str) -> str:
        return require_token(value, ARRIVAL_CLASSES, "arrival_class")

    @field_validator("reason_code")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return None if value is None else require_reason_code(value)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 64:
            raise ValueError("diagnostics_safe_object_too_large")
        return value

    @model_validator(mode="after")
    def validate_registry_contract(self) -> Self:
        definition = EVENT_DEFINITIONS[self.event_name]
        if (
            self.component not in definition.components
            or self.phase not in definition.phases
            or self.status not in definition.statuses
            or (self.reason_code is not None and self.reason_code not in definition.reason_codes)
            or not definition.required_attribute_fields <= set(self.attributes)
            or not set(self.attributes) <= definition.attribute_fields
        ):
            raise ValueError("diagnostics_event_registry_mismatch")
        for key, value in self.attributes.items():
            try:
                validate_scalar(value, definition.attribute_contracts[key])
            except ValueError:
                raise ValueError("diagnostics_event_attribute_mismatch") from None
        present_authority_refs = {
            name
            for name in AUTHORITY_REF_FIELDS
            if getattr(self.authority_refs, name) is not None
        }
        if not present_authority_refs <= definition.authority_ref_fields:
            raise ValueError("diagnostics_event_authority_mismatch")
        if self.status in {"failed", "rejected", "unknown"} and self.reason_code is None:
            raise ValueError("diagnostics_event_reason_required")
        if (
            self.reason_code is not None
            and self.status not in REASON_DEFINITIONS[self.reason_code].event_statuses
        ):
            raise ValueError("diagnostics_event_reason_status_mismatch")
        if self.reason_code is not None:
            reason_definition = REASON_DEFINITIONS[self.reason_code]
            if "failure" in reason_definition.artifacts and (
                self.component not in reason_definition.failure_components
                or self.phase not in reason_definition.failure_phases
            ):
                raise ValueError("diagnostics_event_reason_context_mismatch")
        operation_identity = (
            self.correlation_id,
            self.run_id,
            self.operation_id,
            self.attempt_no,
        )
        if definition.requires_operation and any(item is None for item in operation_identity):
            raise ValueError("diagnostics_operation_identity_required")
        if self.operation_id is not None and (self.run_id is None or self.attempt_no is None):
            raise ValueError("diagnostics_operation_identity_incomplete")
        return self


class FailureEnvelopeV1(ArtifactModel):
    schema_version: Literal["seektalent.failure-envelope/v1"]
    failure_id: RandomIdentity
    revision: PositiveSafeInteger
    correlation_id: RandomIdentity | None
    run_id: RandomIdentity
    operation_id: RandomIdentity | None
    attempt_no: PositiveSafeInteger | None
    diagnostic_trace_id: TraceId
    first_failure_event_id: RandomIdentity | None
    last_observed_event_id: RandomIdentity | None
    component: str
    component_instance_id: RandomIdentity
    component_build_ref: Sha256Ref
    phase: str
    domain: str
    failure_kind: str
    reason_code: str
    cause_ref: CauseRefV1
    detail: dict[str, object]
    boundary_facts: BoundaryFactsV1
    last_safe_boundary: RandomIdentity | Literal["none", "unknown"]
    authority_refs: AuthorityRefsV1
    correlation_refs: CorrelationRefsV1
    diagnostic_gap: DiagnosticGapV1 | None
    observed_boundary_ref: RandomIdentity | None
    source_coverage: SourceCoverageV1 | None
    current_outcome: ProductOutcome | None
    user_action: UserActionV1 | None
    support_action: SupportActionV1 | None
    occurred_at: UtcTimestamp
    observed_at: UtcTimestamp
    redaction: RedactionStateV1

    @field_validator("component")
    @classmethod
    def validate_component(cls, value: str) -> str:
        return require_token(value, COMPONENTS, "component")

    @field_validator("phase")
    @classmethod
    def validate_phase(cls, value: str) -> str:
        return require_token(value, PHASES, "phase")

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return require_token(value, DOMAINS, "domain")

    @field_validator("failure_kind")
    @classmethod
    def validate_failure_kind(cls, value: str) -> str:
        return require_token(value, FAILURE_KINDS, "failure_kind")

    @field_validator("reason_code")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return require_reason_code(value)

    @field_validator("detail")
    @classmethod
    def validate_detail(cls, value: dict[str, object]) -> dict[str, object]:
        allowed = {
            field
            for detail_definition in FAILURE_DETAIL_CONTRACTS.values()
            for field in detail_definition.contracts
        }
        if not set(value) <= allowed:
            raise ValueError("diagnostics_failure_detail_mismatch")
        return value

    @model_validator(mode="after")
    def validate_failure(self) -> Self:
        definition = REASON_DEFINITIONS[self.reason_code]
        if "failure" not in definition.artifacts:
            raise ValueError("diagnostics_reason_not_failure")
        if (self.domain, self.failure_kind) != (definition.domain, definition.failure_kind):
            raise ValueError("diagnostics_failure_mapping_mismatch")
        if (
            self.component not in definition.failure_components
            or self.phase not in definition.failure_phases
        ):
            raise ValueError("diagnostics_failure_context_mismatch")
        detail_definition = FAILURE_DETAIL_CONTRACTS[self.reason_code]
        if (
            not detail_definition.required_fields <= set(self.detail)
            or not set(self.detail) <= set(detail_definition.contracts)
        ):
            raise ValueError("diagnostics_failure_detail_mismatch")
        for key, item in self.detail.items():
            try:
                validate_scalar(item, detail_definition.contracts[key])
            except ValueError:
                raise ValueError("diagnostics_failure_detail_mismatch") from None
        if self.cause_ref.kind == "external_code":
            cause_code = self.cause_ref.code
            if (
                cause_code is None
                or self.reason_code not in EXTERNAL_CAUSE_REASONS[cause_code]
            ):
                raise ValueError("diagnostics_external_cause_mismatch")
        if self.operation_id is not None and self.attempt_no is None:
            raise ValueError("diagnostics_failure_attempt_required")
        has_anchor = self.first_failure_event_id is not None
        has_gap = self.diagnostic_gap is not None and self.observed_boundary_ref is not None
        if has_anchor == has_gap:
            raise ValueError("diagnostics_failure_anchor_gap_mismatch")
        return self
