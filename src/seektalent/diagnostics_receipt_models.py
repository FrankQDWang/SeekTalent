"""Machine, startup, operation-evidence, and append-ack v1 models."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from seektalent.canonical_json import canonical_json_bytes
from seektalent.diagnostics_identity import (
    NonNegativeSafeInteger,
    PositiveSafeInteger,
    RandomIdentity,
    Sha256,
    Sha256Ref,
    TraceId,
    UtcTimestamp,
    VersionString,
)
from seektalent.diagnostics_model_common import (
    MAX_EVENT_BYTES,
    ArtifactModel,
    AuthorityRefsV1,
    BoundaryFactsV1,
    RedactionStateV1,
    StrictDiagnosticsModel,
)
from seektalent.diagnostics_registry import (
    COMPONENTS,
    EVENT_DEFINITIONS,
    require_reason_code,
    require_token,
)
from seektalent.diagnostics_scalar import validate_scalar


class NetworkPostureV1(StrictDiagnosticsModel):
    offline: bool
    system_proxy_present: bool
    custom_ca_present: bool
    chrome_managed: bool


class MachineCapabilityReceiptV1(ArtifactModel):
    schema_version: Literal["seektalent.machine-capability-receipt/v1"]
    receipt_id: RandomIdentity
    revision: PositiveSafeInteger
    canonical_hash: Sha256
    generated_at: UtcTimestamp
    created_at: UtcTimestamp
    observed_at: UtcTimestamp
    release_manifest_ref: Sha256Ref
    product_version: VersionString
    product_build_ref: Sha256Ref
    domi_version: VersionString
    domi_build_ref: Sha256Ref
    install_channel: Literal["internal", "candidate", "production"]
    platform: Literal["windows", "macos"]
    architecture: Literal["x86_64", "arm64"]
    os_family: Literal["windows", "macos"]
    os_build: VersionString
    os_version_bucket: VersionString
    runtime_versions: dict[str, str]
    chrome_channel: Literal["stable", "beta", "dev", "canary"]
    active_slot_ref: Sha256Ref
    previous_slot_ref: Sha256Ref | None
    switch_status: Literal["not_attempted", "completed", "failed"]
    rollback_status: Literal["not_required", "available", "completed", "failed"]
    manifest_hash: Sha256
    artifact_hash: Sha256
    manifest_signature_status: Literal["verified", "failed", "not_present"]
    artifact_signature_status: Literal["verified", "failed", "not_present"]
    component_build_refs: dict[str, Sha256Ref]
    bridge_implementation: Literal["wtscli", "legacy_opencli", "none"]
    bridge_build_ref: Sha256Ref | None
    bridge_protocol_ref: Sha256Ref | None
    bridge_capabilities: Annotated[
        tuple[Literal["authenticated_framing", "source_port_v1", "browser_control"], ...],
        Field(max_length=16),
    ]
    profile_mode: Literal["isolated", "shared", "none"]
    profile_binding_hash: Sha256 | None
    profile_binding_generation: PositiveSafeInteger | None
    extension_version: VersionString | None
    extension_id_hash: Sha256 | None
    provider_account_hash: Sha256 | None
    endpoint_ownership: Literal["verified", "conflict", "unknown"]
    database_logical_name: Literal["runtime_control", "source_port"]
    database_schema_version: PositiveSafeInteger
    database_journal_mode: Literal["wal", "delete", "truncate", "memory", "off"]
    database_integrity: Literal["ok", "failed", "unknown"]
    database_file_size_bucket: Literal["empty", "small", "medium", "large"]
    database_wal_size_bucket: Literal["empty", "small", "medium", "large"]
    database_shm_size_bucket: Literal["empty", "small", "medium", "large"]
    disk_free_size_bucket: Literal["critical", "low", "adequate", "unknown"]
    disk_writable: bool
    disk_executable: bool
    capabilities: dict[str, Literal["supported", "unsupported", "indeterminate"]]
    network_posture: NetworkPostureV1
    result: Literal["supported", "unsupported", "indeterminate"]
    gap_codes: Annotated[
        tuple[
            Literal[
                "browser_bridge_unsupported",
                "endpoint_owner_unknown",
                "database_integrity_failed",
                "disk_not_writable",
                "disk_not_executable",
            ],
            ...,
        ],
        Field(max_length=32),
    ]
    redaction: RedactionStateV1

    @field_validator("gap_codes", "bridge_capabilities", mode="before")
    @classmethod
    def decode_arrays(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @field_validator("runtime_versions")
    @classmethod
    def validate_runtime_versions(cls, value: dict[str, str]) -> dict[str, str]:
        if not set(value) <= {"python", "node", "sqlite", "chrome"}:
            raise ValueError("diagnostics_unknown_runtime_version")
        if any(re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", item) is None for item in value.values()):
            raise ValueError("diagnostics_invalid_version")
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
        allowed = {
            "source_port",
            "browser_bridge",
            "endpoint_ownership",
            "database_integrity",
            "disk_access",
            "network_posture",
        }
        if not value or not set(value) <= allowed:
            raise ValueError("diagnostics_invalid_capability")
        return value

    @model_validator(mode="after")
    def validate_facts(self) -> Self:
        if self.platform == "windows" and self.architecture != "x86_64":
            raise ValueError("diagnostics_platform_architecture_mismatch")
        states = set(self.capabilities.values())
        expected = (
            "unsupported"
            if "unsupported" in states
            else "indeterminate"
            if "indeterminate" in states
            else "supported"
        )
        if self.result != expected:
            raise ValueError("diagnostics_capability_aggregate_mismatch")
        if (self.result == "supported") != (not self.gap_codes):
            raise ValueError("diagnostics_capability_gap_mismatch")
        if self.os_family != self.platform:
            raise ValueError("diagnostics_os_platform_mismatch")
        if self.created_at != self.generated_at or self.observed_at < self.created_at:
            raise ValueError("diagnostics_capability_time_mismatch")
        if self.bridge_implementation == "none":
            if self.bridge_build_ref is not None or self.bridge_protocol_ref is not None:
                raise ValueError("diagnostics_bridge_fact_mismatch")
        elif self.bridge_build_ref is None or self.bridge_protocol_ref is None:
            raise ValueError("diagnostics_bridge_fact_mismatch")
        if self.canonical_hash != machine_capability_content_hash(self):
            raise ValueError("diagnostics_machine_capability_hash_mismatch")
        return self


class StartupReceiptV1(ArtifactModel):
    schema_version: Literal["seektalent.startup-receipt/v1"]
    startup_receipt_id: RandomIdentity
    revision: PositiveSafeInteger
    canonical_hash: Sha256
    created_at: UtcTimestamp
    observed_at: UtcTimestamp
    component: str
    component_instance_id: RandomIdentity
    parent_instance_id: RandomIdentity | None
    capability_receipt_ref: RandomIdentity
    release_manifest_ref: Sha256Ref
    component_build_ref: Sha256Ref
    protocol_refs: Annotated[tuple[Sha256Ref, ...], Field(max_length=32)]
    capability_refs: Annotated[tuple[Sha256Ref, ...], Field(max_length=32)]
    startup_kind: Literal["fresh", "restart", "upgrade_rebind", "wake"]
    started_at: UtcTimestamp
    readiness_observed_at: UtcTimestamp
    exited_at: UtcTimestamp | None
    readiness: Literal["ready", "not_ready"]
    reason_code: str
    restart_count: NonNegativeSafeInteger
    restart_budget_ref: Sha256Ref
    previous_instance_ref: RandomIdentity | None
    last_exit_cause_ref: RandomIdentity | None
    profile_binding_generation: PositiveSafeInteger | None
    browser_control_scope_id: RandomIdentity | None
    extension_install_generation: PositiveSafeInteger | None
    service_worker_generation: PositiveSafeInteger | None
    database_refs: Annotated[tuple[Sha256Ref, ...], Field(max_length=32)]
    endpoint_ownership_ref: Sha256Ref | None
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

    @model_validator(mode="after")
    def validate_facts(self) -> Self:
        started = datetime.strptime(self.started_at, "%Y-%m-%dT%H:%M:%SZ")
        readiness = datetime.strptime(self.readiness_observed_at, "%Y-%m-%dT%H:%M:%SZ")
        exited = (
            datetime.strptime(self.exited_at, "%Y-%m-%dT%H:%M:%SZ")
            if self.exited_at is not None
            else None
        )
        if readiness < started or (exited is not None and exited < started):
            raise ValueError("diagnostics_startup_time_order_invalid")
        expected_reason = "component_ready" if self.readiness == "ready" else "component_startup_failed"
        if self.reason_code != expected_reason:
            raise ValueError("diagnostics_startup_reason_mismatch")
        if self.created_at != self.started_at or self.observed_at != self.readiness_observed_at:
            raise ValueError("diagnostics_startup_observation_mismatch")
        if self.canonical_hash != startup_receipt_content_hash(self):
            raise ValueError("diagnostics_startup_hash_mismatch")
        return self


class OperationEvidenceV1(ArtifactModel):
    schema_version: Literal["seektalent.operation-evidence/v1"]
    operation_evidence_id: RandomIdentity
    revision: PositiveSafeInteger
    canonical_hash: Sha256
    release_manifest_ref: Sha256Ref
    correlation_id: RandomIdentity
    run_id: RandomIdentity
    operation_id: RandomIdentity
    attempt_no: PositiveSafeInteger
    diagnostic_trace_id: TraceId
    authority_refs: AuthorityRefsV1
    capability_receipt_refs: Annotated[tuple[RandomIdentity, ...], Field(max_length=32)]
    startup_receipt_refs: Annotated[tuple[RandomIdentity, ...], Field(max_length=32)]
    source_id: Literal["liepin"]
    operation_kind: Literal["verify_session", "search", "cards", "details", "continuation", "cleanup"]
    first_event_ref: RandomIdentity | None
    last_event_ref: RandomIdentity | None
    failure_envelope_ref: RandomIdentity | None
    checkpoint_ref: Sha256Ref | None
    boundary_facts: BoundaryFactsV1
    summary: dict[str, object]
    source_operation_disposition: Literal["completed", "failed", "unknown"] | None
    product_outcome: Literal["succeeded", "failed", "partial", "unknown"] | None
    missing_evidence_refs: Annotated[tuple[RandomIdentity, ...], Field(max_length=32)]
    rejected_stale_write_count: NonNegativeSafeInteger
    journal_truncation: Literal["none", "budget", "retention", "gap"]
    created_at: UtcTimestamp
    observed_at: UtcTimestamp
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
        contracts = {
            "result_count": EVENT_DEFINITIONS["operation.accepted"].attribute_contracts["safe_count"],
            "coverage": EVENT_DEFINITIONS["operation.accepted"].attribute_contracts["coverage"],
        }
        if not set(value) <= set(contracts):
            raise ValueError("diagnostics_operation_summary_mismatch")
        for key, item in value.items():
            validate_scalar(item, contracts[key])
        return value

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        if self.observed_at < self.created_at:
            raise ValueError("diagnostics_operation_evidence_time_mismatch")
        if self.canonical_hash != operation_evidence_content_hash(self):
            raise ValueError("diagnostics_operation_evidence_hash_mismatch")
        return self


class JournalAppendAckV1(ArtifactModel):
    _max_raw_bytes = MAX_EVENT_BYTES

    schema_version: Literal["seektalent.journal-append-ack/v1"]
    event_id: RandomIdentity
    journal_seq: PositiveSafeInteger
    canonical_hash: Sha256
    accepted_at: UtcTimestamp


def _content_hash(artifact: ArtifactModel) -> str:
    payload = artifact.model_dump(mode="json")
    del payload["canonical_hash"]
    return sha256(canonical_json_bytes(payload)).hexdigest()


def operation_evidence_content_hash(evidence: OperationEvidenceV1) -> str:
    return _content_hash(evidence)


def machine_capability_content_hash(receipt: MachineCapabilityReceiptV1) -> str:
    return _content_hash(receipt)


def startup_receipt_content_hash(receipt: StartupReceiptV1) -> str:
    return _content_hash(receipt)
