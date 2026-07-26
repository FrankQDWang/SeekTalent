"""Strict, production-unreachable v1 canonical diagnostics artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import re
from typing import Annotated, ClassVar, Literal, Self, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from seektalent.diagnostics_registry import (
    ARRIVAL_CLASSES,
    COMPONENTS,
    DOMAINS,
    EVENT_DEFINITIONS,
    FAILURE_KINDS,
    PHASES,
    REASON_DEFINITIONS,
    REDACTION_RULES,
    SEVERITIES,
    STATUSES,
    require_event_name,
    require_reason_code,
    require_token,
)
from seektalent.strict_json import StrictJsonError, strict_json_object_loads

from .source_port.wire_primitives import canonical_json_bytes


CANONICAL_EVENT_V1 = "seektalent.canonical-event/v1"
FAILURE_ENVELOPE_V1 = "seektalent.failure-envelope/v1"
MACHINE_CAPABILITY_RECEIPT_V1 = "seektalent.machine-capability-receipt/v1"
STARTUP_RECEIPT_V1 = "seektalent.startup-receipt/v1"
OPERATION_EVIDENCE_V1 = "seektalent.operation-evidence/v1"
JOURNAL_APPEND_ACK_V1 = "seektalent.journal-append-ack/v1"

MAX_EVENT_BYTES = 16 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024
MAX_SAFE_INTEGER = (1 << 53) - 1
_OPAQUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TRACE_RE = re.compile(r"(?!0{32})[0-9a-f]{32}")
_SPAN_RE = re.compile(r"(?!0{16})[0-9a-f]{16}")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_SAFE_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_TOKEN_RE = re.compile(r"[a-z][a-z0-9_.-]{0,95}")
_ABSOLUTE_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_FORBIDDEN_TEXT_RE = re.compile(
    r"(?:\bBearer\s+\S+|https?://|<\s*(?:html|body|script)\b|"
    r"(?:password|api[_-]?key|authorization|token)\s*[=:]\s*\S+)",
    re.IGNORECASE,
)
_FORBIDDEN_KEY_RE = re.compile(
    r"(?:authorization|auth|cookie|password|secret|token|api_?key|nonce|fence_token|"
    r"observed_provider_account_subject|prompt|^jd$|resume|candidate|company|school|"
    r"search_query|dom|html|visible_text|screenshot|download|clipboard|url|^ip$|ssid|"
    r"proxy|certificate_subject|hostname|username|absolute_path|profile_path|sqlite|"
    r"^db$|^wal$|^shm$|stdout|stderr|^log$|exception_detail|^env$|command_line)",
    re.IGNORECASE,
)


class DiagnosticsReason:
    RAW_INPUT_REQUIRED = "diagnostics_raw_input_required"
    INVALID_UTF8 = "diagnostics_invalid_utf8"
    INVALID_JSON = "diagnostics_invalid_json"
    DUPLICATE_KEY = "diagnostics_duplicate_key"
    ILLEGAL_NUMBER = "diagnostics_illegal_number"
    INVALID_UNICODE = "diagnostics_invalid_unicode"
    ROOT_NOT_OBJECT = "diagnostics_root_not_object"
    UNKNOWN_SCHEMA = "diagnostics_unknown_schema"
    PAYLOAD_TOO_LARGE = "diagnostics_payload_too_large"
    SCHEMA_VALIDATION = "diagnostics_schema_validation"


class DiagnosticsSchemaError(ValueError):
    def __init__(self, reason: str, location: tuple[str | int, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason)


def _validate_opaque(value: str) -> str:
    if (
        _OPAQUE_RE.fullmatch(value) is None
        or value.startswith("/")
        or _ABSOLUTE_WINDOWS_PATH_RE.search(value)
        or "://" in value
        or ".." in value
        or "//" in value
        or "\\" in value
        or _FORBIDDEN_TEXT_RE.search(value)
    ):
        raise ValueError("diagnostics_invalid_opaque_reference")
    return value


def _validate_sha256(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_sha256")
    return value


def _validate_trace(value: str) -> str:
    if _TRACE_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_trace_id")
    return value


def _validate_span(value: str) -> str:
    if _SPAN_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_span_id")
    return value


def _validate_timestamp(value: str) -> str:
    if _UTC_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise ValueError("diagnostics_invalid_timestamp") from None
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("diagnostics_invalid_timestamp")
    return value


OpaqueRef = Annotated[str, Field(strict=True, min_length=1, max_length=96), AfterValidator(_validate_opaque)]
Sha256 = Annotated[str, Field(strict=True), AfterValidator(_validate_sha256)]
TraceId = Annotated[str, Field(strict=True), AfterValidator(_validate_trace)]
SpanId = Annotated[str, Field(strict=True), AfterValidator(_validate_span)]
UtcTimestamp = Annotated[str, Field(strict=True), AfterValidator(_validate_timestamp)]
PositiveSafeInteger = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
NonNegativeSafeInteger = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_INTEGER)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class _ArtifactModel(_StrictModel):
    _max_raw_bytes: ClassVar[int] = MAX_ARTIFACT_BYTES

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
        if not isinstance(json_data, bytes):
            raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)
        return _parse_artifact_bytes(
            cls,
            json_data,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


class RedactionReportV1(_StrictModel):
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
        if _FORBIDDEN_TEXT_RE.search(value):
            raise ValueError("diagnostics_invalid_redaction_path")
        return value


class RedactionStateV1(_StrictModel):
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


class AuthorityRefsV1(_StrictModel):
    runtime_attempt_fence_ref: OpaqueRef | None = None
    profile_binding_generation: PositiveSafeInteger | None = None
    browser_control_fence_ref: OpaqueRef | None = None


class CorrelationRefsV1(_StrictModel):
    browser_control_scope_id: OpaqueRef | None = None
    sidecar_command_ref: OpaqueRef | None = None


class CauseRefV1(_StrictModel):
    kind: Literal["event", "failure", "durable_fact", "external_code", "unknown"]
    ref_id: OpaqueRef | None
    code: Annotated[str, Field(strict=True, min_length=1, max_length=96)] | None
    certainty: Literal["observed", "derived", "unknown"]
    derivation_rule_id: OpaqueRef | None

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str | None) -> str | None:
        if value is not None and _SAFE_TOKEN_RE.fullmatch(value) is None:
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


class BoundaryFactV1(_StrictModel):
    state: Literal["not_started", "not_observed", "observed", "unknown"]
    ref: OpaqueRef | None

    @model_validator(mode="after")
    def validate_ref(self) -> Self:
        if self.state == "observed" and self.ref is None:
            raise ValueError("diagnostics_observed_boundary_ref_required")
        if self.state != "observed" and self.ref is not None:
            raise ValueError("diagnostics_unobserved_boundary_has_ref")
        return self


class BoundaryFactsV1(_StrictModel):
    acceptance: BoundaryFactV1
    dispatch: BoundaryFactV1
    side_effect: BoundaryFactV1
    result_persistence: BoundaryFactV1
    main_commit: BoundaryFactV1
    cleanup: BoundaryFactV1


class DiagnosticGapV1(_StrictModel):
    reason_code: str
    counter: PositiveSafeInteger

    @field_validator("reason_code")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return require_reason_code(value)


class SourceCoverageV1(_StrictModel):
    source_id: Literal["liepin"]
    state: Literal["started", "completed", "partial", "unknown"]
    safe_count: NonNegativeSafeInteger


class UserActionV1(_StrictModel):
    code: OpaqueRef
    instruction_key: OpaqueRef
    affected_scope_ref: OpaqueRef | None = None


class SupportActionV1(_StrictModel):
    code: OpaqueRef
    instruction_key: OpaqueRef


def _validate_safe_value(value: object, *, depth: int = 0, key_count: list[int] | None = None) -> None:
    if depth > 3:
        raise ValueError("diagnostics_safe_object_too_deep")
    if key_count is None:
        key_count = [0]
    if value is None or type(value) in {bool, int}:
        if type(value) is int and (value < -MAX_SAFE_INTEGER or value > MAX_SAFE_INTEGER):
            raise ValueError("diagnostics_safe_integer_out_of_range")
        return
    if isinstance(value, str):
        if len(value) > 256 or _FORBIDDEN_TEXT_RE.search(value):
            raise ValueError("diagnostics_unsafe_value")
        return
    if type(value) is list:
        if len(value) > 32:
            raise ValueError("diagnostics_safe_array_too_large")
        for item in value:
            _validate_safe_value(item, depth=depth + 1, key_count=key_count)
        return
    if type(value) is dict:
        if len(value) > 64:
            raise ValueError("diagnostics_safe_object_too_large")
        for key, item in value.items():
            key_count[0] += 1
            if (
                key_count[0] > 64
                or not isinstance(key, str)
                or _SAFE_KEY_RE.fullmatch(key) is None
                or _FORBIDDEN_KEY_RE.search(key)
            ):
                raise ValueError("diagnostics_unsafe_field")
            _validate_safe_value(item, depth=depth + 1, key_count=key_count)
        return
    raise ValueError("diagnostics_safe_value_invalid")


class CanonicalEventV1(_ArtifactModel):
    _max_raw_bytes = MAX_EVENT_BYTES

    schema_version: Literal["seektalent.canonical-event/v1"]
    event_id: OpaqueRef
    journal_seq: PositiveSafeInteger
    correlation_id: OpaqueRef | None
    diagnostic_trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None
    caused_by_event_id: OpaqueRef | None
    run_id: OpaqueRef | None
    operation_id: OpaqueRef | None
    attempt_no: PositiveSafeInteger | None
    component: str
    component_instance_id: OpaqueRef
    component_event_seq: PositiveSafeInteger
    release_manifest_ref: OpaqueRef | None
    component_build_ref: OpaqueRef
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
        _validate_safe_value(value)
        return value

    @model_validator(mode="after")
    def validate_registry_contract(self) -> Self:
        definition = EVENT_DEFINITIONS[self.event_name]
        if (
            self.component not in definition.components
            or self.phase not in definition.phases
            or self.status not in definition.statuses
            or (self.reason_code is not None and self.reason_code not in definition.reason_codes)
            or not set(self.attributes) <= definition.attribute_fields
        ):
            raise ValueError("diagnostics_event_registry_mismatch")
        if self.status in {"failed", "rejected", "unknown"} and self.reason_code is None:
            raise ValueError("diagnostics_event_reason_required")
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


class FailureEnvelopeV1(_ArtifactModel):
    schema_version: Literal["seektalent.failure-envelope/v1"]
    failure_id: OpaqueRef
    revision: PositiveSafeInteger
    correlation_id: OpaqueRef | None
    run_id: OpaqueRef
    operation_id: OpaqueRef | None
    attempt_no: PositiveSafeInteger | None
    diagnostic_trace_id: TraceId
    first_failure_event_id: OpaqueRef | None
    last_observed_event_id: OpaqueRef | None
    component: str
    component_instance_id: OpaqueRef
    component_build_ref: OpaqueRef
    phase: str
    domain: str
    failure_kind: str
    reason_code: str
    cause_ref: CauseRefV1
    detail: dict[str, object]
    boundary_facts: BoundaryFactsV1
    last_safe_boundary: OpaqueRef | Literal["none", "unknown"]
    authority_refs: AuthorityRefsV1
    correlation_refs: CorrelationRefsV1
    diagnostic_gap: DiagnosticGapV1 | None
    observed_boundary_ref: OpaqueRef | None
    source_coverage: SourceCoverageV1 | None
    current_outcome: OpaqueRef | None
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
        _validate_safe_value(value)
        return value

    @model_validator(mode="after")
    def validate_failure(self) -> Self:
        definition = REASON_DEFINITIONS[self.reason_code]
        if (self.domain, self.failure_kind) != (definition.domain, definition.failure_kind):
            raise ValueError("diagnostics_failure_mapping_mismatch")
        if self.operation_id is not None and self.attempt_no is None:
            raise ValueError("diagnostics_failure_attempt_required")
        has_anchor = self.first_failure_event_id is not None
        has_gap = self.diagnostic_gap is not None and self.observed_boundary_ref is not None
        if has_anchor == has_gap:
            raise ValueError("diagnostics_failure_anchor_gap_mismatch")
        return self


class NetworkPostureV1(_StrictModel):
    offline: bool
    system_proxy_present: bool
    custom_ca_present: bool
    chrome_managed: bool


class MachineCapabilityReceiptV1(_ArtifactModel):
    schema_version: Literal["seektalent.machine-capability-receipt/v1"]
    receipt_id: OpaqueRef
    revision: PositiveSafeInteger
    generated_at: UtcTimestamp
    release_manifest_ref: OpaqueRef
    product_version: OpaqueRef
    product_build_ref: OpaqueRef
    install_channel: Literal["internal", "candidate", "production"]
    platform: Literal["windows", "macos"]
    architecture: Literal["x86_64", "arm64"]
    os_version_bucket: OpaqueRef
    runtime_versions: dict[str, str]
    component_build_refs: dict[str, OpaqueRef]
    capabilities: dict[str, Literal["supported", "unsupported", "indeterminate"]]
    network_posture: NetworkPostureV1
    result: Literal["supported", "unsupported", "indeterminate"]
    gap_codes: Annotated[tuple[OpaqueRef, ...], Field(max_length=32)]
    redaction: RedactionStateV1

    @field_validator("gap_codes", mode="before")
    @classmethod
    def decode_gap_codes(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @field_validator("runtime_versions")
    @classmethod
    def validate_runtime_versions(cls, value: dict[str, str]) -> dict[str, str]:
        if not set(value) <= {"python", "node", "sqlite", "chrome"}:
            raise ValueError("diagnostics_unknown_runtime_version")
        for item in value.values():
            _validate_safe_value(item)
        return value

    @field_validator("component_build_refs")
    @classmethod
    def validate_component_build_refs(cls, value: dict[str, str]) -> dict[str, str]:
        if not set(value) <= COMPONENTS:
            raise ValueError("diagnostics_unknown_component_build_ref")
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or any(_SAFE_KEY_RE.fullmatch(key) is None for key in value):
            raise ValueError("diagnostics_invalid_capability")
        return value

    @model_validator(mode="after")
    def validate_platform(self) -> Self:
        if self.platform == "windows" and self.architecture != "x86_64":
            raise ValueError("diagnostics_platform_architecture_mismatch")
        if self.result == "supported" and self.gap_codes:
            raise ValueError("diagnostics_supported_capability_has_gaps")
        return self


class StartupReceiptV1(_ArtifactModel):
    schema_version: Literal["seektalent.startup-receipt/v1"]
    startup_receipt_id: OpaqueRef
    revision: PositiveSafeInteger
    component: str
    component_instance_id: OpaqueRef
    parent_instance_id: OpaqueRef | None
    capability_receipt_ref: OpaqueRef
    release_manifest_ref: OpaqueRef
    component_build_ref: OpaqueRef
    protocol_refs: Annotated[tuple[OpaqueRef, ...], Field(max_length=32)]
    capability_refs: Annotated[tuple[OpaqueRef, ...], Field(max_length=32)]
    startup_kind: Literal["fresh", "restart", "upgrade_rebind", "wake"]
    started_at: UtcTimestamp
    readiness_observed_at: UtcTimestamp
    exited_at: UtcTimestamp | None
    readiness: Literal["ready", "not_ready"]
    reason_code: str
    restart_count: NonNegativeSafeInteger
    restart_budget_ref: OpaqueRef
    previous_instance_ref: OpaqueRef | None
    last_exit_cause_ref: OpaqueRef | None
    profile_binding_generation: PositiveSafeInteger | None
    browser_control_scope_id: OpaqueRef | None
    extension_install_generation: PositiveSafeInteger | None
    service_worker_generation: PositiveSafeInteger | None
    database_refs: Annotated[tuple[OpaqueRef, ...], Field(max_length=32)]
    endpoint_ownership_ref: OpaqueRef | None
    redaction: RedactionStateV1

    @field_validator("component")
    @classmethod
    def validate_component(cls, value: str) -> str:
        return require_token(value, COMPONENTS, "component")

    @field_validator("reason_code")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return require_reason_code(value)

    @field_validator("protocol_refs", "capability_refs", "database_refs", mode="before")
    @classmethod
    def decode_arrays(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class OperationEvidenceV1(_ArtifactModel):
    schema_version: Literal["seektalent.operation-evidence/v1"]
    operation_evidence_id: OpaqueRef
    revision: PositiveSafeInteger
    canonical_hash: Sha256
    correlation_id: OpaqueRef
    run_id: OpaqueRef
    operation_id: OpaqueRef
    attempt_no: PositiveSafeInteger
    diagnostic_trace_id: TraceId
    authority_refs: AuthorityRefsV1
    capability_receipt_refs: Annotated[tuple[OpaqueRef, ...], Field(max_length=32)]
    startup_receipt_refs: Annotated[tuple[OpaqueRef, ...], Field(max_length=32)]
    source_id: Literal["liepin"]
    operation_kind: Literal["verify_session", "search", "cards", "details", "continuation", "cleanup"]
    first_event_ref: OpaqueRef | None
    last_event_ref: OpaqueRef | None
    failure_envelope_ref: OpaqueRef | None
    checkpoint_ref: OpaqueRef | None
    boundary_facts: BoundaryFactsV1
    summary: dict[str, object]
    source_operation_disposition: OpaqueRef | None
    product_outcome: OpaqueRef | None
    missing_evidence_refs: Annotated[tuple[OpaqueRef, ...], Field(max_length=32)]
    rejected_stale_write_count: NonNegativeSafeInteger
    journal_truncation: Literal["none", "budget", "retention", "gap"]
    created_at: UtcTimestamp
    redaction: RedactionStateV1

    @field_validator(
        "capability_receipt_refs",
        "startup_receipt_refs",
        "missing_evidence_refs",
        mode="before",
    )
    @classmethod
    def decode_arrays(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: dict[str, object]) -> dict[str, object]:
        _validate_safe_value(value)
        return value

    @model_validator(mode="after")
    def validate_canonical_hash(self) -> Self:
        if self.canonical_hash != operation_evidence_content_hash(self):
            raise ValueError("diagnostics_operation_evidence_hash_mismatch")
        return self


class JournalAppendAckV1(_ArtifactModel):
    _max_raw_bytes = MAX_EVENT_BYTES

    schema_version: Literal["seektalent.journal-append-ack/v1"]
    event_id: OpaqueRef
    journal_seq: PositiveSafeInteger
    canonical_hash: Sha256
    accepted_at: UtcTimestamp


DiagnosticsArtifactV1 = (
    CanonicalEventV1
    | FailureEnvelopeV1
    | MachineCapabilityReceiptV1
    | StartupReceiptV1
    | OperationEvidenceV1
    | JournalAppendAckV1
)

_MODELS_BY_SCHEMA: dict[str, type[_ArtifactModel]] = {
    CANONICAL_EVENT_V1: CanonicalEventV1,
    FAILURE_ENVELOPE_V1: FailureEnvelopeV1,
    MACHINE_CAPABILITY_RECEIPT_V1: MachineCapabilityReceiptV1,
    STARTUP_RECEIPT_V1: StartupReceiptV1,
    OPERATION_EVIDENCE_V1: OperationEvidenceV1,
    JOURNAL_APPEND_ACK_V1: JournalAppendAckV1,
}

ArtifactModel = TypeVar("ArtifactModel", bound=_ArtifactModel)


def _load_strict(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes):
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)
    try:
        return strict_json_object_loads(raw)
    except StrictJsonError as exc:
        reason = {
            "invalid_utf8": DiagnosticsReason.INVALID_UTF8,
            "invalid_json": DiagnosticsReason.INVALID_JSON,
            "duplicate_key": DiagnosticsReason.DUPLICATE_KEY,
            "illegal_number": DiagnosticsReason.ILLEGAL_NUMBER,
            "invalid_unicode": DiagnosticsReason.INVALID_UNICODE,
            "root_not_object": DiagnosticsReason.ROOT_NOT_OBJECT,
        }[exc.reason.value]
        raise DiagnosticsSchemaError(reason, exc.location) from None


def _parse_artifact_bytes(
    model_cls: type[ArtifactModel],
    raw: bytes,
    *,
    context: object | None = None,
    by_alias: bool | None = None,
    by_name: bool | None = None,
) -> ArtifactModel:
    if len(raw) > model_cls._max_raw_bytes:
        raise DiagnosticsSchemaError(DiagnosticsReason.PAYLOAD_TOO_LARGE)
    _load_strict(raw)
    try:
        return BaseModel.model_validate_json.__func__(
            model_cls,
            raw,
            strict=True,
            extra="forbid",
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        raise DiagnosticsSchemaError(
            DiagnosticsReason.SCHEMA_VALIDATION,
            tuple(first["loc"]),
        ) from None


def parse_diagnostics_artifact(raw: bytes) -> DiagnosticsArtifactV1:
    if not isinstance(raw, bytes):
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise DiagnosticsSchemaError(DiagnosticsReason.PAYLOAD_TOO_LARGE)
    payload = _load_strict(raw)
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or schema_version not in _MODELS_BY_SCHEMA:
        raise DiagnosticsSchemaError(DiagnosticsReason.UNKNOWN_SCHEMA)
    if schema_version == CANONICAL_EVENT_V1:
        return parse_canonical_event(raw)
    if schema_version == FAILURE_ENVELOPE_V1:
        return parse_failure_envelope(raw)
    if schema_version == MACHINE_CAPABILITY_RECEIPT_V1:
        return parse_machine_capability_receipt(raw)
    if schema_version == STARTUP_RECEIPT_V1:
        return parse_startup_receipt(raw)
    if schema_version == OPERATION_EVIDENCE_V1:
        return parse_operation_evidence(raw)
    return parse_journal_append_ack(raw)


def parse_canonical_event(raw: bytes) -> CanonicalEventV1:
    return _parse_artifact_bytes(CanonicalEventV1, raw)


def parse_failure_envelope(raw: bytes) -> FailureEnvelopeV1:
    return _parse_artifact_bytes(FailureEnvelopeV1, raw)


def parse_machine_capability_receipt(raw: bytes) -> MachineCapabilityReceiptV1:
    return _parse_artifact_bytes(MachineCapabilityReceiptV1, raw)


def parse_startup_receipt(raw: bytes) -> StartupReceiptV1:
    return _parse_artifact_bytes(StartupReceiptV1, raw)


def parse_operation_evidence(raw: bytes) -> OperationEvidenceV1:
    return _parse_artifact_bytes(OperationEvidenceV1, raw)


def parse_journal_append_ack(raw: bytes) -> JournalAppendAckV1:
    return _parse_artifact_bytes(JournalAppendAckV1, raw)


def canonical_diagnostics_bytes(artifact: DiagnosticsArtifactV1) -> bytes:
    if not isinstance(artifact, tuple(_MODELS_BY_SCHEMA.values())):
        raise DiagnosticsSchemaError(DiagnosticsReason.SCHEMA_VALIDATION)
    return canonical_json_bytes(artifact.model_dump(mode="json"))


def canonical_diagnostics_hash(artifact: DiagnosticsArtifactV1) -> str:
    return sha256(canonical_diagnostics_bytes(artifact)).hexdigest()


def operation_evidence_content_hash(evidence: OperationEvidenceV1) -> str:
    """Hash the immutable evidence body; the embedded digest is excluded."""
    payload = evidence.model_dump(mode="json")
    del payload["canonical_hash"]
    return sha256(canonical_json_bytes(payload)).hexdigest()


__all__ = [
    "CANONICAL_EVENT_V1",
    "FAILURE_ENVELOPE_V1",
    "JOURNAL_APPEND_ACK_V1",
    "MACHINE_CAPABILITY_RECEIPT_V1",
    "OPERATION_EVIDENCE_V1",
    "STARTUP_RECEIPT_V1",
    "CanonicalEventV1",
    "DiagnosticsSchemaError",
    "FailureEnvelopeV1",
    "JournalAppendAckV1",
    "MachineCapabilityReceiptV1",
    "OperationEvidenceV1",
    "StartupReceiptV1",
    "canonical_diagnostics_bytes",
    "canonical_diagnostics_hash",
    "operation_evidence_content_hash",
    "parse_canonical_event",
    "parse_diagnostics_artifact",
    "parse_failure_envelope",
    "parse_journal_append_ack",
    "parse_machine_capability_receipt",
    "parse_operation_evidence",
    "parse_startup_receipt",
]
