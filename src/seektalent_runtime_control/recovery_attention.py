"""Durable attention evidence for unresolved external effects after owner loss."""

from __future__ import annotations

import sqlite3

from seektalent_runtime_control.errors import RuntimeControlError


RECOVERY_ATTENTION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runtime_control_recovery_attention (
      runtime_run_id TEXT PRIMARY KEY,
      operation_id TEXT NOT NULL,
      reason_code TEXT NOT NULL,
      effect_boundary TEXT NOT NULL,
      evidence_digest TEXT NOT NULL,
      status TEXT NOT NULL,
      entered_at TEXT NOT NULL,
      resolved_at TEXT,
      CHECK (reason_code = 'runtime_source_operation_unresolved'),
      CHECK (effect_boundary IN (
        'accepted', 'dispatch_intent', 'observed', 'reconciled'
      )),
      CHECK (length(evidence_digest) = 64),
      CHECK (status IN ('active', 'resolved')),
      CHECK (
        (status = 'active' AND resolved_at IS NULL)
        OR (status = 'resolved' AND resolved_at IS NOT NULL)
      )
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS runtime_recovery_attention_one_way
    BEFORE UPDATE ON runtime_control_recovery_attention
    WHEN
      OLD.status != 'active'
      OR NEW.status != 'resolved'
      OR NEW.runtime_run_id != OLD.runtime_run_id
      OR NEW.operation_id != OLD.operation_id
      OR NEW.reason_code != OLD.reason_code
      OR NEW.effect_boundary != OLD.effect_boundary
      OR NEW.evidence_digest != OLD.evidence_digest
      OR NEW.entered_at != OLD.entered_at
      OR NEW.resolved_at IS NULL
    BEGIN
      SELECT RAISE(ABORT, 'runtime_recovery_attention_immutable');
    END
    """,
)


def create_recovery_attention_schema(
    connection: sqlite3.Connection,
) -> None:
    for statement in RECOVERY_ATTENTION_SCHEMA_STATEMENTS:
        connection.execute(statement)


def enter_recovery_attention(
    connection: sqlite3.Connection,
    *,
    runtime_run_id: str,
    entered_at: str,
) -> None:
    operation = connection.execute(
        """
        SELECT operation_id, operation_phase, canonical_request_hash
        FROM runtime_control_source_operations
        WHERE runtime_run_id = ?
          AND (
            operation_phase != 'main_committed'
            OR main_commit_ref IS NULL
          )
        ORDER BY operation_id
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()
    if operation is None:
        raise RuntimeControlError(
            "runtime_source_operation_unresolved_evidence_missing"
        )
    existing = connection.execute(
        """
        SELECT * FROM runtime_control_recovery_attention
        WHERE runtime_run_id = ?
        """,
        (runtime_run_id,),
    ).fetchone()
    values = (
        str(operation["operation_id"]),
        str(operation["operation_phase"]),
        str(operation["canonical_request_hash"]),
    )
    if existing is not None:
        if (
            existing["status"] != "active"
            or (
                existing["operation_id"],
                existing["effect_boundary"],
                existing["evidence_digest"],
            )
            != values
        ):
            raise RuntimeControlError(
                "runtime_recovery_attention_conflict"
            )
        return
    connection.execute(
        """
        INSERT INTO runtime_control_recovery_attention (
          runtime_run_id, operation_id, reason_code, effect_boundary,
          evidence_digest, status, entered_at, resolved_at
        )
        VALUES (?, ?, 'runtime_source_operation_unresolved', ?, ?,
                'active', ?, NULL)
        """,
        (
            runtime_run_id,
            values[0],
            values[1],
            values[2],
            entered_at,
        ),
    )


def resolve_recovery_attention(
    connection: sqlite3.Connection,
    *,
    runtime_run_id: str,
    resolved_at: str,
) -> None:
    active = connection.execute(
        """
        SELECT * FROM runtime_control_recovery_attention
        WHERE runtime_run_id = ? AND status = 'active'
        """,
        (runtime_run_id,),
    ).fetchone()
    if active is None:
        raise RuntimeControlError(
            "runtime_recovery_attention_not_active"
        )
    unresolved = connection.execute(
        """
        SELECT 1
        FROM runtime_control_source_operations AS operation
        WHERE operation.runtime_run_id = ?
          AND (
            (
              operation.operation_phase != 'main_committed'
              OR operation.main_commit_ref IS NULL
            )
            AND NOT (
              operation.retry_posture = 'safe_retry'
              AND operation.conclusive_observation_ref IS NULL
              AND EXISTS (
                SELECT 1
                FROM runtime_control_source_reconciliations AS reconciliation
                WHERE reconciliation.runtime_run_id = operation.runtime_run_id
                  AND reconciliation.operation_id = operation.operation_id
                  AND reconciliation.committed_reconciliation_revision =
                    operation.reconciliation_revision
                  AND reconciliation.decision_kind = 'no_dispatch_proved'
                  AND reconciliation.history_conclusion = 'accepted_no_dispatch'
                  AND reconciliation.retry_posture = 'safe_retry'
              )
            )
          )
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()
    if unresolved is not None:
        raise RuntimeControlError(
            "runtime_source_operation_unresolved"
        )
    connection.execute(
        """
        UPDATE runtime_control_recovery_attention
        SET status = 'resolved', resolved_at = ?
        WHERE runtime_run_id = ? AND status = 'active'
        """,
        (resolved_at, runtime_run_id),
    )
