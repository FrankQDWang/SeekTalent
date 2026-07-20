from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
import unicodedata
from typing import Literal, NamedTuple

from seektalent_runtime_control.errors import RuntimeControlError


SourceOperationKind = Literal["verify_session", "search", "cards", "details", "continuation", "cleanup"]
SourceOperationPhase = Literal["accepted", "dispatch_intent", "observed", "reconciled", "main_committed"]
SourceOperationDisposition = Literal[
    "completed",
    "partial",
    "user_action_required",
    "incompatible",
    "failed",
    "cancelled",
    "reconciliation_unknown",
]
RetryPosture = Literal["no_retry", "safe_retry", "reconcile_first"]
SourceDispatchStatus = Literal["pending", "acknowledged"]
SourceDispatchAckKind = Literal[
    "new_logical_operation",
    "new_dispatch_authorization",
    "same_intent_replay",
]

SOURCE_OPERATION_KINDS = frozenset({"verify_session", "search", "cards", "details", "continuation", "cleanup"})
SOURCE_OPERATION_PHASES = frozenset({"accepted", "dispatch_intent", "observed", "reconciled", "main_committed"})
SOURCE_OPERATION_DISPOSITIONS = frozenset(
    {
        "completed",
        "partial",
        "user_action_required",
        "incompatible",
        "failed",
        "cancelled",
        "reconciliation_unknown",
    }
)
RETRY_POSTURES = frozenset({"no_retry", "safe_retry", "reconcile_first"})
SOURCE_DISPATCH_ACK_KINDS = frozenset({"new_logical_operation", "new_dispatch_authorization", "same_intent_replay"})
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")
_SQLITE_INTEGER_MAX = 2**63 - 1
_JSON_SAFE_INTEGER_MAX = 2**53 - 1


@dataclass(frozen=True, slots=True)
class SourceOperationRecord:
    runtime_run_id: str
    operation_id: str
    source_id: Literal["liepin"]
    operation_kind: SourceOperationKind
    canonical_request_hash: str
    idempotency_key: str
    accepted_requirement_revision_id: str
    runtime_attempt_no: int
    runtime_attempt_authority_ref: str
    operation_phase: SourceOperationPhase
    dispatch_intent_ref: str | None
    conclusive_observation_ref: str | None
    source_operation_disposition: SourceOperationDisposition | None
    retry_posture: RetryPosture
    reconciliation_revision: int
    main_commit_ref: str | None
    ledger_revision: int


@dataclass(frozen=True, slots=True)
class SourceOperationAdmissionExpectation:
    runtime_run_id: str
    operation_id: str
    runtime_attempt_fence_ref: str
    profile_binding_generation: int
    browser_control_scope_id: str | None
    controller_fence_ref: str | None


@dataclass(frozen=True, slots=True)
class SourceDispatchMetadata:
    outbox_id: str
    runtime_run_id: str
    operation_id: str
    canonical_request_hash: str
    dispatch_intent_id: str
    dispatch_intent_revision: int
    dispatch_intent_digest: str
    dispatch_authorization_ordinal: int
    source_operation_acceptance_ref: str
    expected_ledger_revision: int
    expected_reconciliation_revision: int
    status: SourceDispatchStatus
    outbox_revision: int
    accepted_sidecar_generation: int | None
    accepted_sidecar_journal_revision: int | None
    ack_ref: str | None
    ack_kind: SourceDispatchAckKind | None
    acknowledged_at: str | None


class AcceptedSourceOperation(NamedTuple):
    operation: SourceOperationRecord
    expectation: SourceOperationAdmissionExpectation
    dispatch: SourceDispatchMetadata


def validate_source_operation_acceptance(
    *,
    runtime_run_id: str,
    operation_id: str,
    source_id: str,
    operation_kind: str,
    canonical_request_hash: str,
    idempotency_key: str,
    accepted_requirement_revision_id: str,
    runtime_attempt_no: int,
    runtime_attempt_authority_ref: str,
    runtime_attempt_fence_ref: str,
    profile_binding_generation: int,
    browser_control_scope_id: str | None,
    controller_fence_ref: str | None,
    outbox_id: str,
    dispatch_intent_id: str,
    dispatch_intent_revision: int,
    dispatch_intent_digest: str,
    dispatch_authorization_ordinal: int,
    source_operation_acceptance_ref: str,
    expected_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> None:
    _require_opaque(runtime_run_id, "runtime_run_id", max_bytes=96)
    _require_opaque(operation_id, "operation_id", max_bytes=96)
    if source_id != "liepin":
        raise RuntimeControlError("source_operation_source_invalid")
    if not isinstance(operation_kind, str) or operation_kind not in SOURCE_OPERATION_KINDS:
        raise RuntimeControlError("source_operation_kind_invalid")
    _require_sha256(canonical_request_hash, "canonical_request_hash")
    _require_opaque(idempotency_key, "idempotency_key", max_bytes=128)
    _require_opaque(accepted_requirement_revision_id, "accepted_requirement_revision_id", max_bytes=96)
    _require_positive(runtime_attempt_no, "runtime_attempt_no")
    _require_opaque(runtime_attempt_authority_ref, "runtime_attempt_authority_ref", max_bytes=256)
    validate_source_operation_admission_expectation(
        runtime_run_id=runtime_run_id,
        operation_id=operation_id,
        runtime_attempt_fence_ref=runtime_attempt_fence_ref,
        profile_binding_generation=profile_binding_generation,
        browser_control_scope_id=browser_control_scope_id,
        controller_fence_ref=controller_fence_ref,
    )
    _require_opaque(outbox_id, "outbox_id", max_bytes=96)
    _require_opaque(dispatch_intent_id, "dispatch_intent_id", max_bytes=96)
    _require_positive(dispatch_intent_revision, "dispatch_intent_revision")
    _require_sha256(dispatch_intent_digest, "dispatch_intent_digest")
    _require_exact_int(
        dispatch_authorization_ordinal,
        expected=1,
        reason_code="source_dispatch_authorization_ordinal_invalid",
    )
    _require_opaque(source_operation_acceptance_ref, "source_operation_acceptance_ref", max_bytes=256)
    _require_exact_int(
        expected_ledger_revision,
        expected=1,
        reason_code="source_operation_expected_ledger_revision_invalid",
    )
    _require_exact_int(
        expected_reconciliation_revision,
        expected=0,
        reason_code="source_operation_expected_reconciliation_revision_invalid",
    )


def validate_source_operation_admission_expectation(
    *,
    runtime_run_id: str,
    operation_id: str,
    runtime_attempt_fence_ref: str,
    profile_binding_generation: int,
    browser_control_scope_id: str | None,
    controller_fence_ref: str | None,
) -> None:
    _require_opaque(runtime_run_id, "runtime_run_id", max_bytes=96)
    _require_opaque(operation_id, "operation_id", max_bytes=96)
    _require_sha256(runtime_attempt_fence_ref, "runtime_attempt_fence_ref")
    _require_positive_json_safe(profile_binding_generation, "profile_binding_generation")
    if browser_control_scope_id is not None:
        _require_wire_opaque(browser_control_scope_id, "browser_control_scope_id", max_bytes=96)
    if controller_fence_ref is not None:
        _require_sha256(controller_fence_ref, "controller_fence_ref")


def validate_source_dispatch_ack(
    *,
    runtime_run_id: str,
    operation_id: str,
    outbox_id: str,
    canonical_request_hash: str,
    dispatch_intent_id: str,
    dispatch_intent_revision: int,
    dispatch_intent_digest: str,
    dispatch_authorization_ordinal: int,
    expected_outbox_revision: int,
    accepted_sidecar_generation: int,
    accepted_sidecar_journal_revision: int,
    ack_ref: str,
    ack_kind: str,
    acknowledged_at: str,
) -> None:
    _require_opaque(runtime_run_id, "runtime_run_id", max_bytes=96)
    _require_opaque(operation_id, "operation_id", max_bytes=96)
    _require_opaque(outbox_id, "outbox_id", max_bytes=96)
    _require_sha256(canonical_request_hash, "canonical_request_hash")
    _require_opaque(dispatch_intent_id, "dispatch_intent_id", max_bytes=96)
    _require_positive(dispatch_intent_revision, "dispatch_intent_revision")
    _require_sha256(dispatch_intent_digest, "dispatch_intent_digest")
    _require_exact_int(
        dispatch_authorization_ordinal,
        expected=1,
        reason_code="source_dispatch_authorization_ordinal_invalid",
    )
    _require_positive(expected_outbox_revision, "expected_outbox_revision")
    _require_positive(accepted_sidecar_generation, "accepted_sidecar_generation")
    _require_positive(accepted_sidecar_journal_revision, "accepted_sidecar_journal_revision")
    _require_opaque(ack_ref, "ack_ref", max_bytes=256)
    if not isinstance(ack_kind, str) or ack_kind not in SOURCE_DISPATCH_ACK_KINDS:
        raise RuntimeControlError("source_dispatch_ack_kind_invalid")
    _require_opaque(acknowledged_at, "acknowledged_at", max_bytes=64)


def source_operation_from_row(row: sqlite3.Row) -> SourceOperationRecord:
    return SourceOperationRecord(
        runtime_run_id=row["runtime_run_id"],
        operation_id=row["operation_id"],
        source_id=row["source_id"],
        operation_kind=row["operation_kind"],
        canonical_request_hash=row["canonical_request_hash"],
        idempotency_key=row["idempotency_key"],
        accepted_requirement_revision_id=row["accepted_requirement_revision_id"],
        runtime_attempt_no=int(row["runtime_attempt_no"]),
        runtime_attempt_authority_ref=row["runtime_attempt_authority_ref"],
        operation_phase=row["operation_phase"],
        dispatch_intent_ref=row["dispatch_intent_ref"],
        conclusive_observation_ref=row["conclusive_observation_ref"],
        source_operation_disposition=row["source_operation_disposition"],
        retry_posture=row["retry_posture"],
        reconciliation_revision=int(row["reconciliation_revision"]),
        main_commit_ref=row["main_commit_ref"],
        ledger_revision=int(row["ledger_revision"]),
    )


def source_dispatch_from_row(row: sqlite3.Row) -> SourceDispatchMetadata:
    return SourceDispatchMetadata(
        outbox_id=row["outbox_id"],
        runtime_run_id=row["runtime_run_id"],
        operation_id=row["operation_id"],
        canonical_request_hash=row["canonical_request_hash"],
        dispatch_intent_id=row["dispatch_intent_id"],
        dispatch_intent_revision=int(row["dispatch_intent_revision"]),
        dispatch_intent_digest=row["dispatch_intent_digest"],
        dispatch_authorization_ordinal=int(row["dispatch_authorization_ordinal"]),
        source_operation_acceptance_ref=row["source_operation_acceptance_ref"],
        expected_ledger_revision=int(row["expected_ledger_revision"]),
        expected_reconciliation_revision=int(row["expected_reconciliation_revision"]),
        status=row["status"],
        outbox_revision=int(row["outbox_revision"]),
        accepted_sidecar_generation=row["accepted_sidecar_generation"],
        accepted_sidecar_journal_revision=row["accepted_sidecar_journal_revision"],
        ack_ref=row["ack_ref"],
        ack_kind=row["ack_kind"],
        acknowledged_at=row["acknowledged_at"],
    )


def source_operation_admission_expectation_from_row(
    row: sqlite3.Row,
) -> SourceOperationAdmissionExpectation:
    return SourceOperationAdmissionExpectation(
        runtime_run_id=row["runtime_run_id"],
        operation_id=row["operation_id"],
        runtime_attempt_fence_ref=row["runtime_attempt_fence_ref"],
        profile_binding_generation=row["profile_binding_generation"],
        browser_control_scope_id=row["browser_control_scope_id"],
        controller_fence_ref=row["controller_fence_ref"],
    )


def operation_matches_acceptance(
    operation: SourceOperationRecord,
    *,
    operation_id: str,
    source_id: str,
    operation_kind: str,
    canonical_request_hash: str,
    idempotency_key: str,
    accepted_requirement_revision_id: str,
    runtime_attempt_no: int,
    runtime_attempt_authority_ref: str,
) -> bool:
    return (
        operation.operation_id == operation_id
        and operation.source_id == source_id
        and operation.operation_kind == operation_kind
        and operation.canonical_request_hash == canonical_request_hash
        and operation.idempotency_key == idempotency_key
        and operation.accepted_requirement_revision_id == accepted_requirement_revision_id
        and operation.runtime_attempt_no == runtime_attempt_no
        and operation.runtime_attempt_authority_ref == runtime_attempt_authority_ref
    )


def expectation_matches_operation(
    expectation: SourceOperationAdmissionExpectation,
    operation: SourceOperationRecord,
) -> bool:
    return expectation.runtime_run_id == operation.runtime_run_id and expectation.operation_id == operation.operation_id


def expectation_matches_acceptance(
    expectation: SourceOperationAdmissionExpectation,
    *,
    runtime_attempt_fence_ref: str,
    profile_binding_generation: int,
    browser_control_scope_id: str | None,
    controller_fence_ref: str | None,
) -> bool:
    return (
        expectation.runtime_attempt_fence_ref == runtime_attempt_fence_ref
        and expectation.profile_binding_generation == profile_binding_generation
        and expectation.browser_control_scope_id == browser_control_scope_id
        and expectation.controller_fence_ref == controller_fence_ref
    )


def dispatch_matches_operation(
    dispatch: SourceDispatchMetadata,
    operation: SourceOperationRecord,
) -> bool:
    return (
        dispatch.runtime_run_id == operation.runtime_run_id
        and dispatch.operation_id == operation.operation_id
        and dispatch.canonical_request_hash == operation.canonical_request_hash
        and dispatch.dispatch_authorization_ordinal == 1
        and dispatch.expected_ledger_revision == 1
        and dispatch.expected_reconciliation_revision == 0
    )


def dispatch_matches_acceptance(
    dispatch: SourceDispatchMetadata,
    *,
    outbox_id: str,
    canonical_request_hash: str,
    dispatch_intent_id: str,
    dispatch_intent_revision: int,
    dispatch_intent_digest: str,
    dispatch_authorization_ordinal: int,
    source_operation_acceptance_ref: str,
    expected_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> bool:
    return (
        dispatch.outbox_id == outbox_id
        and dispatch.canonical_request_hash == canonical_request_hash
        and dispatch.dispatch_intent_id == dispatch_intent_id
        and dispatch.dispatch_intent_revision == dispatch_intent_revision
        and dispatch.dispatch_intent_digest == dispatch_intent_digest
        and dispatch.dispatch_authorization_ordinal == dispatch_authorization_ordinal
        and dispatch.source_operation_acceptance_ref == source_operation_acceptance_ref
        and dispatch.expected_ledger_revision == expected_ledger_revision
        and dispatch.expected_reconciliation_revision == expected_reconciliation_revision
    )


def dispatch_ack_matches(
    dispatch: SourceDispatchMetadata,
    *,
    accepted_sidecar_generation: int,
    accepted_sidecar_journal_revision: int,
    ack_ref: str,
    ack_kind: str,
    acknowledged_at: str,
) -> bool:
    return (
        dispatch.status == "acknowledged"
        and dispatch.accepted_sidecar_generation == accepted_sidecar_generation
        and dispatch.accepted_sidecar_journal_revision == accepted_sidecar_journal_revision
        and dispatch.ack_ref == ack_ref
        and dispatch.ack_kind == ack_kind
        and dispatch.acknowledged_at == acknowledged_at
    )


def _require_opaque(value: object, field: str, *, max_bytes: int) -> str:
    reason_code = f"source_operation_{field}_invalid"
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeControlError(reason_code)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise RuntimeControlError(reason_code) from None
    if len(encoded) > max_bytes or any(ord(character) < 32 for character in value):
        raise RuntimeControlError(reason_code)
    return value


def _require_wire_opaque(value: object, field: str, *, max_bytes: int) -> None:
    validated = _require_opaque(value, field, max_bytes=max_bytes)
    if any(unicodedata.category(character) == "Cc" for character in validated):
        raise RuntimeControlError(f"source_operation_{field}_invalid")


def _require_sha256(value: object, field: str) -> None:
    if not isinstance(value, str) or _LOWERCASE_SHA256.fullmatch(value) is None:
        raise RuntimeControlError(f"source_operation_{field}_invalid")


def _require_positive(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _SQLITE_INTEGER_MAX:
        raise RuntimeControlError(f"source_operation_{field}_invalid")


def _require_positive_json_safe(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _JSON_SAFE_INTEGER_MAX:
        raise RuntimeControlError(f"source_operation_{field}_invalid")


def _require_exact_int(value: object, *, expected: int, reason_code: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise RuntimeControlError(reason_code)
