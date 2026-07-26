"""Stable public facade for strict diagnostics v1 schemas and admission."""

from __future__ import annotations

from hashlib import sha256

from seektalent.canonical_json import canonical_json_bytes
from seektalent.diagnostics_coverage import (
    MACHINE_CAPABILITY_FIELD_COVERAGE,
    OPERATION_EVIDENCE_FIELD_COVERAGE,
    STARTUP_RECEIPT_FIELD_COVERAGE,
)
from seektalent.diagnostics_event_models import CanonicalEventV1, FailureEnvelopeV1
from seektalent.diagnostics_model_common import (
    CANONICAL_EVENT_V1,
    FAILURE_ENVELOPE_V1,
    JOURNAL_APPEND_ACK_V1,
    MACHINE_CAPABILITY_RECEIPT_V1,
    MAX_ARTIFACT_BYTES,
    MAX_EVENT_BYTES,
    OPERATION_EVIDENCE_V1,
    STARTUP_RECEIPT_V1,
    ArtifactModel,
    DiagnosticsReason,
    DiagnosticsSchemaError,
)
from seektalent.diagnostics_receipt_models import (
    JournalAppendAckV1,
    MachineCapabilityReceiptV1,
    OperationEvidenceV1,
    StartupReceiptV1,
    machine_capability_content_hash,
    operation_evidence_content_hash,
    startup_receipt_content_hash,
)


DiagnosticsArtifactV1 = (
    CanonicalEventV1
    | FailureEnvelopeV1
    | MachineCapabilityReceiptV1
    | StartupReceiptV1
    | OperationEvidenceV1
    | JournalAppendAckV1
)

MODELS_BY_SCHEMA: dict[str, type[ArtifactModel]] = {
    CANONICAL_EVENT_V1: CanonicalEventV1,
    FAILURE_ENVELOPE_V1: FailureEnvelopeV1,
    MACHINE_CAPABILITY_RECEIPT_V1: MachineCapabilityReceiptV1,
    STARTUP_RECEIPT_V1: StartupReceiptV1,
    OPERATION_EVIDENCE_V1: OperationEvidenceV1,
    JOURNAL_APPEND_ACK_V1: JournalAppendAckV1,
}


def parse_diagnostics_artifact(raw: bytes) -> DiagnosticsArtifactV1:
    from seektalent.diagnostics_admission import parse_diagnostics_artifact as parse

    return parse(raw)


def parse_canonical_event(raw: bytes) -> CanonicalEventV1:
    from seektalent.diagnostics_admission import parse_canonical_event as parse

    return parse(raw)


def parse_failure_envelope(raw: bytes) -> FailureEnvelopeV1:
    from seektalent.diagnostics_admission import parse_failure_envelope as parse

    return parse(raw)


def parse_machine_capability_receipt(raw: bytes) -> MachineCapabilityReceiptV1:
    from seektalent.diagnostics_admission import parse_machine_capability_receipt as parse

    return parse(raw)


def parse_startup_receipt(raw: bytes) -> StartupReceiptV1:
    from seektalent.diagnostics_admission import parse_startup_receipt as parse

    return parse(raw)


def parse_operation_evidence(raw: bytes) -> OperationEvidenceV1:
    from seektalent.diagnostics_admission import parse_operation_evidence as parse

    return parse(raw)


def parse_journal_append_ack(raw: bytes) -> JournalAppendAckV1:
    from seektalent.diagnostics_admission import parse_journal_append_ack as parse

    return parse(raw)


def canonical_diagnostics_bytes(artifact: DiagnosticsArtifactV1) -> bytes:
    if not isinstance(artifact, tuple(MODELS_BY_SCHEMA.values())):
        raise DiagnosticsSchemaError(DiagnosticsReason.SCHEMA_VALIDATION)
    return canonical_json_bytes(artifact.model_dump(mode="json"))


def canonical_diagnostics_hash(artifact: DiagnosticsArtifactV1) -> str:
    return sha256(canonical_diagnostics_bytes(artifact)).hexdigest()


__all__ = [
    "CANONICAL_EVENT_V1",
    "FAILURE_ENVELOPE_V1",
    "JOURNAL_APPEND_ACK_V1",
    "MACHINE_CAPABILITY_FIELD_COVERAGE",
    "MACHINE_CAPABILITY_RECEIPT_V1",
    "MAX_ARTIFACT_BYTES",
    "MAX_EVENT_BYTES",
    "OPERATION_EVIDENCE_FIELD_COVERAGE",
    "OPERATION_EVIDENCE_V1",
    "STARTUP_RECEIPT_FIELD_COVERAGE",
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
    "machine_capability_content_hash",
    "operation_evidence_content_hash",
    "parse_canonical_event",
    "parse_diagnostics_artifact",
    "parse_failure_envelope",
    "parse_journal_append_ack",
    "parse_machine_capability_receipt",
    "parse_operation_evidence",
    "parse_startup_receipt",
    "startup_receipt_content_hash",
]
