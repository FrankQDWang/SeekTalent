from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal
import unicodedata

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.source_operations import (
    RETRY_POSTURES,
    SOURCE_OPERATION_DISPOSITIONS,
    SOURCE_OPERATION_KINDS,
    RetryPosture,
    SourceOperationDisposition,
    SourceOperationKind,
)


SourceOperationReconciliationDecisionKind = Literal[
    "no_dispatch_proved",
    "unresolved",
    "conclusive_observation",
]
SourceOperationHistoryOutcome = Literal["matched", "not_found", "history_unavailable"]
SourceOperationHistoryConclusion = Literal[
    "accepted_no_dispatch",
    "dispatch_not_observed",
    "observed_result",
    "observed_failure",
]
CommittedSourceOperationPhase = Literal["reconciled"]

SOURCE_OPERATION_RECONCILIATION_DECISION_KINDS = frozenset(
    {"no_dispatch_proved", "unresolved", "conclusive_observation"}
)
SOURCE_OPERATION_HISTORY_OUTCOMES = frozenset({"matched", "not_found", "history_unavailable"})
SOURCE_OPERATION_HISTORY_CONCLUSIONS = frozenset(
    {"accepted_no_dispatch", "dispatch_not_observed", "observed_result", "observed_failure"}
)
CONCLUSIVE_SOURCE_OPERATION_DISPOSITIONS = frozenset(
    {"completed", "partial", "incompatible", "failed"}
)

_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")
_SQLITE_INTEGER_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class SourceOperationReconciliationDecision:
    reconciliation_id: str
    runtime_run_id: str
    operation_id: str
    source_id: Literal["liepin"]
    operation_kind: SourceOperationKind
    canonical_request_hash: str
    idempotency_key: str
    accepted_requirement_revision_id: str
    runtime_attempt_no: int
    runtime_attempt_authority_ref: str
    history_result_ref: str
    history_result_digest: str
    decision_kind: SourceOperationReconciliationDecisionKind
    history_outcome: SourceOperationHistoryOutcome
    history_conclusion: SourceOperationHistoryConclusion | None
    dispatch_intent_ref: str | None
    conclusive_observation_ref: str | None
    source_operation_disposition: SourceOperationDisposition | None
    retry_posture: RetryPosture
    expected_ledger_revision: int
    expected_reconciliation_revision: int
    committed_at: str


@dataclass(frozen=True, slots=True)
class SourceOperationReconciliationRecord:
    reconciliation_id: str
    runtime_run_id: str
    operation_id: str
    source_id: Literal["liepin"]
    operation_kind: SourceOperationKind
    canonical_request_hash: str
    idempotency_key: str
    accepted_requirement_revision_id: str
    runtime_attempt_no: int
    runtime_attempt_authority_ref: str
    history_result_ref: str
    history_result_digest: str
    decision_kind: SourceOperationReconciliationDecisionKind
    history_outcome: SourceOperationHistoryOutcome
    history_conclusion: SourceOperationHistoryConclusion | None
    dispatch_intent_ref: str | None
    conclusive_observation_ref: str | None
    source_operation_disposition: SourceOperationDisposition | None
    retry_posture: RetryPosture
    expected_ledger_revision: int
    expected_reconciliation_revision: int
    committed_at: str
    committed_operation_phase: CommittedSourceOperationPhase
    committed_ledger_revision: int
    committed_reconciliation_revision: int


def validate_source_operation_reconciliation_decision(
    decision: SourceOperationReconciliationDecision,
) -> None:
    if not isinstance(decision, SourceOperationReconciliationDecision):
        raise RuntimeControlError("source_reconciliation_decision_invalid")
    _require_opaque(decision.reconciliation_id, "reconciliation_id", max_bytes=96)
    _require_opaque(decision.runtime_run_id, "runtime_run_id", max_bytes=96)
    _require_opaque(decision.operation_id, "operation_id", max_bytes=96)
    if decision.source_id != "liepin":
        raise RuntimeControlError("source_operation_source_invalid")
    if not isinstance(decision.operation_kind, str) or decision.operation_kind not in SOURCE_OPERATION_KINDS:
        raise RuntimeControlError("source_operation_kind_invalid")
    _require_sha256(decision.canonical_request_hash, "canonical_request_hash")
    _require_opaque(decision.idempotency_key, "idempotency_key", max_bytes=128)
    _require_opaque(
        decision.accepted_requirement_revision_id,
        "accepted_requirement_revision_id",
        max_bytes=96,
    )
    _require_positive(decision.runtime_attempt_no, "runtime_attempt_no")
    _require_opaque(
        decision.runtime_attempt_authority_ref,
        "runtime_attempt_authority_ref",
        max_bytes=256,
    )
    _require_opaque(decision.history_result_ref, "history_result_ref", max_bytes=256)
    _require_sha256(decision.history_result_digest, "history_result_digest")
    _require_closed_value(
        decision.decision_kind,
        SOURCE_OPERATION_RECONCILIATION_DECISION_KINDS,
        "source_reconciliation_decision_kind_invalid",
    )
    _require_closed_value(
        decision.history_outcome,
        SOURCE_OPERATION_HISTORY_OUTCOMES,
        "source_reconciliation_history_outcome_invalid",
    )
    if decision.history_conclusion is not None:
        _require_closed_value(
            decision.history_conclusion,
            SOURCE_OPERATION_HISTORY_CONCLUSIONS,
            "source_reconciliation_history_conclusion_invalid",
        )
    _require_optional_opaque(decision.dispatch_intent_ref, "dispatch_intent_ref", max_bytes=256)
    _require_optional_opaque(
        decision.conclusive_observation_ref,
        "conclusive_observation_ref",
        max_bytes=256,
    )
    if decision.source_operation_disposition is not None:
        _require_closed_value(
            decision.source_operation_disposition,
            SOURCE_OPERATION_DISPOSITIONS,
            "source_reconciliation_source_operation_disposition_invalid",
        )
    _require_closed_value(
        decision.retry_posture,
        RETRY_POSTURES,
        "source_reconciliation_retry_posture_invalid",
    )
    _require_incrementable_positive_revision(
        decision.expected_ledger_revision,
        "expected_ledger_revision",
    )
    _require_incrementable_nonnegative_revision(
        decision.expected_reconciliation_revision,
        "expected_reconciliation_revision",
    )
    _require_opaque(decision.committed_at, "committed_at", max_bytes=64)
    _validate_decision_matrix(decision)


def source_reconciliation_from_row(row: sqlite3.Row) -> SourceOperationReconciliationRecord:
    return SourceOperationReconciliationRecord(
        reconciliation_id=row["reconciliation_id"],
        runtime_run_id=row["runtime_run_id"],
        operation_id=row["operation_id"],
        source_id=row["source_id"],
        operation_kind=row["operation_kind"],
        canonical_request_hash=row["canonical_request_hash"],
        idempotency_key=row["idempotency_key"],
        accepted_requirement_revision_id=row["accepted_requirement_revision_id"],
        runtime_attempt_no=int(row["runtime_attempt_no"]),
        runtime_attempt_authority_ref=row["runtime_attempt_authority_ref"],
        history_result_ref=row["history_result_ref"],
        history_result_digest=row["history_result_digest"],
        decision_kind=row["decision_kind"],
        history_outcome=row["history_outcome"],
        history_conclusion=row["history_conclusion"],
        dispatch_intent_ref=row["dispatch_intent_ref"],
        conclusive_observation_ref=row["conclusive_observation_ref"],
        source_operation_disposition=row["source_operation_disposition"],
        retry_posture=row["retry_posture"],
        expected_ledger_revision=int(row["expected_ledger_revision"]),
        expected_reconciliation_revision=int(row["expected_reconciliation_revision"]),
        committed_at=row["committed_at"],
        committed_operation_phase=row["committed_operation_phase"],
        committed_ledger_revision=int(row["committed_ledger_revision"]),
        committed_reconciliation_revision=int(row["committed_reconciliation_revision"]),
    )


def source_reconciliation_matches_decision(
    record: SourceOperationReconciliationRecord,
    decision: SourceOperationReconciliationDecision,
) -> bool:
    return (
        record.reconciliation_id == decision.reconciliation_id
        and record.runtime_run_id == decision.runtime_run_id
        and record.operation_id == decision.operation_id
        and record.source_id == decision.source_id
        and record.operation_kind == decision.operation_kind
        and record.canonical_request_hash == decision.canonical_request_hash
        and record.idempotency_key == decision.idempotency_key
        and record.accepted_requirement_revision_id == decision.accepted_requirement_revision_id
        and record.runtime_attempt_no == decision.runtime_attempt_no
        and record.runtime_attempt_authority_ref == decision.runtime_attempt_authority_ref
        and record.history_result_ref == decision.history_result_ref
        and record.history_result_digest == decision.history_result_digest
        and record.decision_kind == decision.decision_kind
        and record.history_outcome == decision.history_outcome
        and record.history_conclusion == decision.history_conclusion
        and record.dispatch_intent_ref == decision.dispatch_intent_ref
        and record.conclusive_observation_ref == decision.conclusive_observation_ref
        and record.source_operation_disposition == decision.source_operation_disposition
        and record.retry_posture == decision.retry_posture
        and record.expected_ledger_revision == decision.expected_ledger_revision
        and record.expected_reconciliation_revision == decision.expected_reconciliation_revision
        and record.committed_at == decision.committed_at
    )


def _validate_decision_matrix(decision: SourceOperationReconciliationDecision) -> None:
    if decision.decision_kind == "no_dispatch_proved":
        valid_history = (
            decision.history_outcome == "not_found" and decision.history_conclusion is None
        ) or (
            decision.history_outcome == "matched"
            and decision.history_conclusion == "accepted_no_dispatch"
        )
        if not valid_history:
            raise RuntimeControlError("source_reconciliation_history_matrix_invalid")
        if decision.dispatch_intent_ref is not None or decision.conclusive_observation_ref is not None:
            raise RuntimeControlError("source_reconciliation_reference_matrix_invalid")
        if decision.retry_posture != "safe_retry":
            raise RuntimeControlError("source_reconciliation_retry_posture_matrix_invalid")
        return

    if decision.decision_kind == "unresolved":
        valid_history = (
            decision.history_outcome == "history_unavailable" and decision.history_conclusion is None
        ) or (
            decision.history_outcome == "matched"
            and decision.history_conclusion == "dispatch_not_observed"
        )
        if not valid_history:
            raise RuntimeControlError("source_reconciliation_history_matrix_invalid")
        if (
            decision.history_outcome == "matched"
            and decision.dispatch_intent_ref is None
        ) or decision.conclusive_observation_ref is not None:
            raise RuntimeControlError("source_reconciliation_reference_matrix_invalid")
        if decision.source_operation_disposition != "reconciliation_unknown":
            raise RuntimeControlError("source_reconciliation_disposition_matrix_invalid")
        if decision.retry_posture != "reconcile_first":
            raise RuntimeControlError("source_reconciliation_retry_posture_matrix_invalid")
        return

    if decision.history_outcome != "matched" or decision.history_conclusion not in {
        "observed_result",
        "observed_failure",
    }:
        raise RuntimeControlError("source_reconciliation_history_matrix_invalid")
    if decision.dispatch_intent_ref is None or decision.conclusive_observation_ref is None:
        raise RuntimeControlError("source_reconciliation_reference_matrix_invalid")
    if decision.source_operation_disposition not in CONCLUSIVE_SOURCE_OPERATION_DISPOSITIONS:
        raise RuntimeControlError("source_reconciliation_disposition_matrix_invalid")
    if decision.retry_posture != "no_retry":
        raise RuntimeControlError("source_reconciliation_retry_posture_matrix_invalid")


def _require_opaque(value: object, field: str, *, max_bytes: int) -> None:
    reason_code = f"source_reconciliation_{field}_invalid"
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeControlError(reason_code)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise RuntimeControlError(reason_code) from None
    if len(encoded) > max_bytes or any(unicodedata.category(character) == "Cc" for character in value):
        raise RuntimeControlError(reason_code)


def _require_optional_opaque(value: object, field: str, *, max_bytes: int) -> None:
    if value is not None:
        _require_opaque(value, field, max_bytes=max_bytes)


def _require_sha256(value: object, field: str) -> None:
    if not isinstance(value, str) or _LOWERCASE_SHA256.fullmatch(value) is None:
        raise RuntimeControlError(f"source_reconciliation_{field}_invalid")


def _require_closed_value(value: object, allowed: frozenset[str], reason_code: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise RuntimeControlError(reason_code)


def _require_positive(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _SQLITE_INTEGER_MAX:
        raise RuntimeControlError(f"source_reconciliation_{field}_invalid")


def _require_incrementable_positive_revision(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value < _SQLITE_INTEGER_MAX:
        raise RuntimeControlError(f"source_reconciliation_{field}_invalid")


def _require_incrementable_nonnegative_revision(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < _SQLITE_INTEGER_MAX:
        raise RuntimeControlError(f"source_reconciliation_{field}_invalid")
