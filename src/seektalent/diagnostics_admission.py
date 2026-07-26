"""Strict bytes-only admission for diagnostics v1 artifacts."""

from __future__ import annotations

from seektalent.diagnostics_bytes import load_strict, parse_artifact_bytes
from seektalent.diagnostics_errors import DiagnosticsReason, DiagnosticsSchemaError
from seektalent.diagnostics_event_models import CanonicalEventV1, FailureEnvelopeV1
from seektalent.diagnostics_model_common import (
    CANONICAL_EVENT_V1,
    FAILURE_ENVELOPE_V1,
    JOURNAL_APPEND_ACK_V1,
    MACHINE_CAPABILITY_RECEIPT_V1,
    MAX_ARTIFACT_BYTES,
    OPERATION_EVIDENCE_V1,
    STARTUP_RECEIPT_V1,
    ArtifactModel as ArtifactBase,
)
from seektalent.diagnostics_receipt_models import (
    JournalAppendAckV1,
    MachineCapabilityReceiptV1,
    OperationEvidenceV1,
    StartupReceiptV1,
)


DiagnosticsArtifactV1 = (
    CanonicalEventV1
    | FailureEnvelopeV1
    | MachineCapabilityReceiptV1
    | StartupReceiptV1
    | OperationEvidenceV1
    | JournalAppendAckV1
)
_MODELS_BY_SCHEMA: dict[str, type[ArtifactBase]] = {
    CANONICAL_EVENT_V1: CanonicalEventV1,
    FAILURE_ENVELOPE_V1: FailureEnvelopeV1,
    MACHINE_CAPABILITY_RECEIPT_V1: MachineCapabilityReceiptV1,
    STARTUP_RECEIPT_V1: StartupReceiptV1,
    OPERATION_EVIDENCE_V1: OperationEvidenceV1,
    JOURNAL_APPEND_ACK_V1: JournalAppendAckV1,
}

def parse_diagnostics_artifact(raw: bytes) -> DiagnosticsArtifactV1:
    if not isinstance(raw, bytes):
        raise DiagnosticsSchemaError(DiagnosticsReason.RAW_INPUT_REQUIRED)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise DiagnosticsSchemaError(DiagnosticsReason.PAYLOAD_TOO_LARGE)
    payload = load_strict(raw)
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
    return parse_artifact_bytes(CanonicalEventV1, raw)


def parse_failure_envelope(raw: bytes) -> FailureEnvelopeV1:
    return parse_artifact_bytes(FailureEnvelopeV1, raw)


def parse_machine_capability_receipt(raw: bytes) -> MachineCapabilityReceiptV1:
    return parse_artifact_bytes(MachineCapabilityReceiptV1, raw)


def parse_startup_receipt(raw: bytes) -> StartupReceiptV1:
    return parse_artifact_bytes(StartupReceiptV1, raw)


def parse_operation_evidence(raw: bytes) -> OperationEvidenceV1:
    return parse_artifact_bytes(OperationEvidenceV1, raw)


def parse_journal_append_ack(raw: bytes) -> JournalAppendAckV1:
    return parse_artifact_bytes(JournalAppendAckV1, raw)
