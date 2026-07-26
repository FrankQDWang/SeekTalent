from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal
import unicodedata

from seektalent.sqlite_migrations import SQLiteMigrationError
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.source_operations import (
    RETRY_POSTURES,
    SOURCE_OPERATION_DISPOSITIONS,
    SOURCE_OPERATION_KINDS,
    RetryPosture,
    SourceDispatchMetadata,
    SourceOperationDisposition,
    SourceOperationKind,
    SourceOperationRecord,
    validate_source_dispatch_ack,
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
    {"completed", "partial", "user_action_required", "incompatible", "failed"}
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
    """Match semantic reconciliation identity; committed_at is first-write audit metadata."""
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
    )


def reconciliation_dispatch_precondition_matches(
    dispatch: SourceDispatchMetadata,
    decision: SourceOperationReconciliationDecision,
    dispatch_precondition: SourceDispatchMetadata | None,
) -> bool:
    if dispatch_precondition is not None:
        return type(dispatch_precondition) is SourceDispatchMetadata and dispatch == dispatch_precondition
    if decision.decision_kind != "no_dispatch_proved":
        return True
    return (
        dispatch.status == "pending"
        and dispatch.outbox_revision == 1
        and all(
            value is None
            for value in (
                dispatch.accepted_sidecar_generation,
                dispatch.accepted_sidecar_journal_revision,
                dispatch.ack_ref,
                dispatch.ack_kind,
                dispatch.acknowledged_at,
            )
        )
    )


def reconciliation_dispatch_ack_requires_update(
    dispatch: SourceDispatchMetadata,
    dispatch_ack: SourceDispatchMetadata | None,
    decision: SourceOperationReconciliationDecision,
) -> bool:
    if dispatch_ack is None:
        return False
    if type(dispatch_ack) is not SourceDispatchMetadata:
        raise RuntimeControlError("source_reconciliation_dispatch_ack_invalid")
    if decision.history_outcome != "matched" or decision.history_conclusion not in {
        "accepted_no_dispatch",
        "dispatch_not_observed",
        "observed_result",
        "observed_failure",
    }:
        raise RuntimeControlError("source_reconciliation_dispatch_ack_invalid")
    if dispatch.status == "acknowledged":
        if dispatch_ack != dispatch:
            raise RuntimeControlError("source_reconciliation_dispatch_conflict")
        return False
    _validate_reconciliation_dispatch_ack(dispatch_ack)
    if dispatch.status != "pending" or dispatch.outbox_revision != 1:
        raise RuntimeControlError("source_reconciliation_dispatch_conflict")
    if (
        dispatch_ack.outbox_id != dispatch.outbox_id
        or dispatch_ack.runtime_run_id != dispatch.runtime_run_id
        or dispatch_ack.operation_id != dispatch.operation_id
        or dispatch_ack.canonical_request_hash != dispatch.canonical_request_hash
        or dispatch_ack.dispatch_intent_id != dispatch.dispatch_intent_id
        or dispatch_ack.dispatch_intent_revision != dispatch.dispatch_intent_revision
        or dispatch_ack.dispatch_intent_digest != dispatch.dispatch_intent_digest
        or dispatch_ack.dispatch_authorization_ordinal != dispatch.dispatch_authorization_ordinal
        or dispatch_ack.safe_retry_commit_ref != dispatch.safe_retry_commit_ref
        or dispatch_ack.source_operation_acceptance_ref != dispatch.source_operation_acceptance_ref
        or dispatch_ack.expected_ledger_revision != dispatch.expected_ledger_revision
        or dispatch_ack.expected_reconciliation_revision != dispatch.expected_reconciliation_revision
        or dispatch_ack.status != "acknowledged"
        or dispatch_ack.outbox_revision != 2
    ):
        raise RuntimeControlError("source_reconciliation_dispatch_ack_invalid")
    return True


def source_dispatch_is_currently_deliverable(
    dispatch: SourceDispatchMetadata,
    operation: SourceOperationRecord,
    *,
    latest_dispatch_authorization_ordinal: int,
) -> bool:
    return (
        dispatch.status == "pending"
        and dispatch.outbox_revision == 1
        and dispatch.dispatch_authorization_ordinal == latest_dispatch_authorization_ordinal
        and operation.operation_phase in {"accepted", "reconciled"}
        and operation.dispatch_intent_ref is None
        and operation.conclusive_observation_ref is None
        and operation.source_operation_disposition is None
        and operation.retry_posture == "no_retry"
        and operation.reconciliation_revision == dispatch.expected_reconciliation_revision
        and operation.ledger_revision == dispatch.expected_ledger_revision
        and operation.main_commit_ref is None
    )


def _validate_reconciliation_dispatch_ack(dispatch: SourceDispatchMetadata) -> None:
    ack_ref = dispatch.ack_ref
    if (
        dispatch.accepted_sidecar_generation is None
        or dispatch.accepted_sidecar_journal_revision is None
        or ack_ref is None
        or not ack_ref.startswith("sha256:")
        or _LOWERCASE_SHA256.fullmatch(ack_ref.removeprefix("sha256:")) is None
        or dispatch.ack_kind
        != (
            "new_logical_operation"
            if dispatch.dispatch_authorization_ordinal == 1
            else "new_dispatch_authorization"
        )
        or dispatch.acknowledged_at is None
    ):
        raise RuntimeControlError("source_reconciliation_dispatch_ack_invalid")
    validate_source_dispatch_ack(
        runtime_run_id=dispatch.runtime_run_id,
        operation_id=dispatch.operation_id,
        outbox_id=dispatch.outbox_id,
        canonical_request_hash=dispatch.canonical_request_hash,
        dispatch_intent_id=dispatch.dispatch_intent_id,
        dispatch_intent_revision=dispatch.dispatch_intent_revision,
        dispatch_intent_digest=dispatch.dispatch_intent_digest,
        dispatch_authorization_ordinal=dispatch.dispatch_authorization_ordinal,
        expected_outbox_revision=1,
        accepted_sidecar_generation=dispatch.accepted_sidecar_generation,
        accepted_sidecar_journal_revision=dispatch.accepted_sidecar_journal_revision,
        ack_ref=ack_ref,
        ack_kind=dispatch.ack_kind,
        acknowledged_at=dispatch.acknowledged_at,
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


_SOURCE_RECONCILIATION_COLUMNS = (
    "reconciliation_id",
    "runtime_run_id",
    "operation_id",
    "source_id",
    "operation_kind",
    "canonical_request_hash",
    "idempotency_key",
    "accepted_requirement_revision_id",
    "runtime_attempt_no",
    "runtime_attempt_authority_ref",
    "history_result_ref",
    "history_result_digest",
    "history_outcome",
    "history_conclusion",
    "decision_kind",
    "dispatch_intent_ref",
    "conclusive_observation_ref",
    "source_operation_disposition",
    "retry_posture",
    "expected_ledger_revision",
    "expected_reconciliation_revision",
    "committed_at",
    "committed_operation_phase",
    "committed_ledger_revision",
    "committed_reconciliation_revision",
)
_SOURCE_RECONCILIATION_INTEGER_COLUMNS = frozenset(
    {
        "runtime_attempt_no",
        "expected_ledger_revision",
        "expected_reconciliation_revision",
        "committed_ledger_revision",
        "committed_reconciliation_revision",
    }
)
_SOURCE_RECONCILIATION_OPTIONAL_TEXT_COLUMNS = frozenset(
    {
        "history_conclusion",
        "dispatch_intent_ref",
        "conclusive_observation_ref",
        "source_operation_disposition",
    }
)
_SOURCE_RECONCILIATION_TABLE = "runtime_control_source_reconciliations"
_SOURCE_RECONCILIATION_MIGRATION_TABLE = "runtime_control_source_reconciliations_v11"
_LEGACY_CONCLUSIVE_DISPOSITIONS_SQL = (
    "source_operation_disposition IN ('completed', 'partial', 'incompatible', 'failed')"
)
_V11_CONCLUSIVE_DISPOSITIONS_SQL = (
    "source_operation_disposition IN ('completed', 'partial', 'user_action_required', 'incompatible', 'failed')"
)


def _source_reconciliation_table_sql(
    table_name: str,
    *,
    conclusive_dispositions_sql: str = _V11_CONCLUSIVE_DISPOSITIONS_SQL,
) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
      reconciliation_id TEXT PRIMARY KEY,
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      source_id TEXT NOT NULL,
      operation_kind TEXT NOT NULL,
      canonical_request_hash TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      accepted_requirement_revision_id TEXT NOT NULL,
      runtime_attempt_no INTEGER NOT NULL,
      runtime_attempt_authority_ref TEXT NOT NULL,
      history_result_ref TEXT NOT NULL,
      history_result_digest TEXT NOT NULL,
      history_outcome TEXT NOT NULL,
      history_conclusion TEXT,
      decision_kind TEXT NOT NULL,
      dispatch_intent_ref TEXT,
      conclusive_observation_ref TEXT,
      source_operation_disposition TEXT,
      retry_posture TEXT NOT NULL,
      expected_ledger_revision INTEGER NOT NULL,
      expected_reconciliation_revision INTEGER NOT NULL,
      committed_at TEXT NOT NULL,
      committed_operation_phase TEXT NOT NULL,
      committed_ledger_revision INTEGER NOT NULL,
      committed_reconciliation_revision INTEGER NOT NULL,
      UNIQUE(runtime_run_id, operation_id, committed_reconciliation_revision),
      CHECK (source_id = 'liepin'),
      CHECK (operation_kind IN ('verify_session', 'search', 'cards', 'details', 'continuation', 'cleanup')),
      CHECK (history_outcome IN ('matched', 'not_found', 'history_unavailable')),
      CHECK (history_conclusion IS NULL OR history_conclusion IN (
        'accepted_no_dispatch', 'dispatch_not_observed', 'observed_result', 'observed_failure'
      )),
      CHECK (decision_kind IN ('no_dispatch_proved', 'unresolved', 'conclusive_observation')),
      CHECK (source_operation_disposition IS NULL OR source_operation_disposition IN (
        'completed', 'partial', 'user_action_required', 'incompatible', 'failed',
        'cancelled', 'reconciliation_unknown'
      )),
      CHECK (retry_posture IN ('no_retry', 'safe_retry', 'reconcile_first')),
      CHECK (runtime_attempt_no > 0),
      CHECK (expected_ledger_revision > 0),
      CHECK (expected_reconciliation_revision >= 0),
      CHECK (committed_operation_phase = 'reconciled'),
      CHECK (committed_ledger_revision = expected_ledger_revision + 1),
      CHECK (committed_reconciliation_revision = expected_reconciliation_revision + 1),
      CHECK (
        (
          decision_kind = 'no_dispatch_proved'
          AND (
            (history_outcome = 'not_found' AND history_conclusion IS NULL)
            OR (history_outcome = 'matched' AND history_conclusion = 'accepted_no_dispatch')
          )
          AND dispatch_intent_ref IS NULL
          AND conclusive_observation_ref IS NULL
          AND retry_posture = 'safe_retry'
        )
        OR (
          decision_kind = 'unresolved'
          AND (
            (history_outcome = 'history_unavailable' AND history_conclusion IS NULL)
            OR (
              history_outcome = 'matched'
              AND history_conclusion = 'dispatch_not_observed'
              AND dispatch_intent_ref IS NOT NULL
            )
          )
          AND conclusive_observation_ref IS NULL
          AND source_operation_disposition = 'reconciliation_unknown'
          AND retry_posture = 'reconcile_first'
        )
        OR (
          decision_kind = 'conclusive_observation'
          AND history_outcome = 'matched'
          AND history_conclusion IN ('observed_result', 'observed_failure')
          AND dispatch_intent_ref IS NOT NULL
          AND conclusive_observation_ref IS NOT NULL
          AND {conclusive_dispositions_sql}
          AND retry_posture = 'no_retry'
        )
      )
    )
    """


def _source_reconciliation_trigger_statements(table_name: str) -> tuple[str, str]:
    return (
        f"""
        CREATE TRIGGER IF NOT EXISTS runtime_control_source_reconciliations_no_update
        BEFORE UPDATE ON {table_name}
        BEGIN SELECT RAISE(ABORT, 'runtime_control_source_reconciliations_immutable'); END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS runtime_control_source_reconciliations_no_delete
        BEFORE DELETE ON {table_name}
        BEGIN SELECT RAISE(ABORT, 'runtime_control_source_reconciliations_immutable'); END
        """,
    )


SOURCE_RECONCILIATION_SCHEMA_STATEMENTS = (
    _source_reconciliation_table_sql(_SOURCE_RECONCILIATION_TABLE),
    *_source_reconciliation_trigger_statements(_SOURCE_RECONCILIATION_TABLE),
)
SOURCE_RECONCILIATION_V10_SCHEMA_STATEMENTS = (
    _source_reconciliation_table_sql(
        _SOURCE_RECONCILIATION_TABLE,
        conclusive_dispositions_sql=_LEGACY_CONCLUSIVE_DISPOSITIONS_SQL,
    ),
    *_source_reconciliation_trigger_statements(_SOURCE_RECONCILIATION_TABLE),
)


def migrate_source_reconciliation_v10_to_v11(conn: sqlite3.Connection) -> None:
    schema_row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (_SOURCE_RECONCILIATION_TABLE,),
    ).fetchone()
    columns = tuple(str(row["name"]) for row in conn.execute(f"PRAGMA table_info({_SOURCE_RECONCILIATION_TABLE})"))
    normalized_schema = " ".join(str(schema_row["sql"]).split()) if schema_row is not None else ""
    if (
        columns != _SOURCE_RECONCILIATION_COLUMNS
        or _LEGACY_CONCLUSIVE_DISPOSITIONS_SQL not in normalized_schema
        or _V11_CONCLUSIVE_DISPOSITIONS_SQL in normalized_schema
    ):
        raise _source_reconciliation_migration_error()
    rows = conn.execute(f"SELECT * FROM {_SOURCE_RECONCILIATION_TABLE} ORDER BY reconciliation_id").fetchall()
    for row in rows:
        _validate_legacy_source_reconciliation_row(row)

    conn.execute(f"DROP TABLE IF EXISTS {_SOURCE_RECONCILIATION_MIGRATION_TABLE}")
    conn.execute(_source_reconciliation_table_sql(_SOURCE_RECONCILIATION_MIGRATION_TABLE))
    columns_sql = ", ".join(_SOURCE_RECONCILIATION_COLUMNS)
    conn.execute(
        f"""
        INSERT INTO {_SOURCE_RECONCILIATION_MIGRATION_TABLE} ({columns_sql})
        SELECT {columns_sql} FROM {_SOURCE_RECONCILIATION_TABLE}
        """
    )
    if _source_reconciliation_tables_differ(conn, columns_sql):
        raise _source_reconciliation_migration_error()
    for trigger_name in (
        "runtime_control_source_reconciliations_no_update",
        "runtime_control_source_reconciliations_no_delete",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    conn.execute(f"DROP TABLE {_SOURCE_RECONCILIATION_TABLE}")
    conn.execute(
        f"""
        ALTER TABLE {_SOURCE_RECONCILIATION_MIGRATION_TABLE}
        RENAME TO {_SOURCE_RECONCILIATION_TABLE}
        """
    )
    for statement in _source_reconciliation_trigger_statements(_SOURCE_RECONCILIATION_TABLE):
        conn.execute(statement)


def _validate_legacy_source_reconciliation_row(row: sqlite3.Row) -> None:
    try:
        for column in _SOURCE_RECONCILIATION_COLUMNS:
            value = row[column]
            if column in _SOURCE_RECONCILIATION_INTEGER_COLUMNS:
                if type(value) is not int:
                    raise TypeError
            elif column in _SOURCE_RECONCILIATION_OPTIONAL_TEXT_COLUMNS:
                if value is not None and type(value) is not str:
                    raise TypeError
            elif type(value) is not str:
                raise TypeError
        record = source_reconciliation_from_row(row)
        decision = SourceOperationReconciliationDecision(
            reconciliation_id=record.reconciliation_id,
            runtime_run_id=record.runtime_run_id,
            operation_id=record.operation_id,
            source_id=record.source_id,
            operation_kind=record.operation_kind,
            canonical_request_hash=record.canonical_request_hash,
            idempotency_key=record.idempotency_key,
            accepted_requirement_revision_id=record.accepted_requirement_revision_id,
            runtime_attempt_no=record.runtime_attempt_no,
            runtime_attempt_authority_ref=record.runtime_attempt_authority_ref,
            history_result_ref=record.history_result_ref,
            history_result_digest=record.history_result_digest,
            decision_kind=record.decision_kind,
            history_outcome=record.history_outcome,
            history_conclusion=record.history_conclusion,
            dispatch_intent_ref=record.dispatch_intent_ref,
            conclusive_observation_ref=record.conclusive_observation_ref,
            source_operation_disposition=record.source_operation_disposition,
            retry_posture=record.retry_posture,
            expected_ledger_revision=record.expected_ledger_revision,
            expected_reconciliation_revision=record.expected_reconciliation_revision,
            committed_at=record.committed_at,
        )
        validate_source_operation_reconciliation_decision(decision)
        if (
            record.committed_operation_phase != "reconciled"
            or record.committed_ledger_revision != record.expected_ledger_revision + 1
            or record.committed_reconciliation_revision != record.expected_reconciliation_revision + 1
        ):
            raise ValueError
    except (KeyError, RuntimeControlError, TypeError, ValueError):
        raise _source_reconciliation_migration_error() from None


def _source_reconciliation_tables_differ(
    conn: sqlite3.Connection,
    columns_sql: str,
) -> bool:
    queries = (
        f"""
        SELECT {columns_sql} FROM {_SOURCE_RECONCILIATION_TABLE}
        EXCEPT
        SELECT {columns_sql} FROM {_SOURCE_RECONCILIATION_MIGRATION_TABLE}
        """,
        f"""
        SELECT {columns_sql} FROM {_SOURCE_RECONCILIATION_MIGRATION_TABLE}
        EXCEPT
        SELECT {columns_sql} FROM {_SOURCE_RECONCILIATION_TABLE}
        """,
    )
    return any(conn.execute(query).fetchone() is not None for query in queries)


def _source_reconciliation_migration_error() -> SQLiteMigrationError:
    return SQLiteMigrationError(
        "runtime_control_source_reconciliation_migration_invalid",
        "runtime-control source reconciliation v10 state is invalid",
    )


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
