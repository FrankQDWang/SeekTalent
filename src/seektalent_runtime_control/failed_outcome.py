"""Atomic main-owned terminal failure boundary."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3

from seektalent.diagnostics_event_models import FailureEnvelopeV1
from seektalent.diagnostics_schema import (
    canonical_diagnostics_bytes,
    parse_failure_envelope,
)
from seektalent.diagnostics_storage import (
    FailureEnvelopeStorageError,
    load_failure_envelope_revision,
    store_failure_envelope_revision,
)
from seektalent_runtime_control.clock import timestamp_lte
from seektalent_runtime_control.errors import (
    RuntimeControlError,
    RuntimeControlLookupError,
)


FAILED_OUTCOME_V14_SCHEMA_STATEMENTS = (
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN state_revision INTEGER NOT NULL DEFAULT 0
      CHECK (state_revision >= 0 AND state_revision <= 9007199254740991)
    """,
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN current_failure_revision INTEGER
      CHECK (
        current_failure_revision IS NULL
        OR (
          current_failure_revision >= 1
          AND current_failure_revision <= 9007199254740991
        )
      )
    """,
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN current_failure_id TEXT
      CHECK (
        (current_failure_id IS NULL) = (current_failure_revision IS NULL)
      )
    """,
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN product_outcome TEXT
      CHECK (
        product_outcome IS NULL
        OR (
          product_outcome = 'failed'
          AND status = 'failed'
          AND current_failure_id IS NOT NULL
          AND current_failure_revision IS NOT NULL
        )
        OR (product_outcome = 'cancelled' AND status = 'cancelled')
        OR (
          product_outcome IN (
            'succeeded_with_results',
            'succeeded_empty',
            'degraded_with_results'
          )
          AND status = 'completed'
        )
        OR (product_outcome = 'needs_attention' AND status = 'needs_attention')
      )
    """,
)


def migrate_failed_outcome_v13_to_v14(conn: sqlite3.Connection) -> None:
    required_columns = {
        "state_revision",
        "current_failure_revision",
        "current_failure_id",
        "product_outcome",
    }
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(runtime_control_runs)")
    }
    present = columns & required_columns
    if present:
        if present != required_columns:
            raise RuntimeControlError(
                "runtime_control_failed_outcome_schema_collision"
            )
        table_sql_row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'runtime_control_runs'
            """
        ).fetchone()
        table_sql = table_sql_row[0] if table_sql_row is not None else ""
        required_fragments = (
            "state_revision >= 0",
            "(current_failure_id IS NULL) = (current_failure_revision IS NULL)",
            "succeeded_with_results",
            "needs_attention",
        )
        if not all(fragment in table_sql for fragment in required_fragments):
            raise RuntimeControlError(
                "runtime_control_failed_outcome_schema_collision"
            )
        return
    for statement in FAILED_OUTCOME_V14_SCHEMA_STATEMENTS:
        conn.execute(statement)


def commit_failed_outcome(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    envelope: FailureEnvelopeV1 | bytes,
    terminal_reason_code: str,
    terminal_at: str,
    expected_state_revision: int,
    executor_id: str | None,
    attempt_no: int | None,
    operation_id: str | None,
    statement_hook: Callable[[int, str], None] | None,
) -> sqlite3.Row:
    admitted = _admit_envelope(envelope)
    hook = statement_hook or (lambda _index, _phase: None)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _run_row(conn, runtime_run_id)
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        if row["status"] == "failed":
            replay = _require_exact_replay(
                conn,
                row,
                admitted,
                terminal_reason_code=terminal_reason_code,
                terminal_at=terminal_at,
                expected_state_revision=expected_state_revision,
                executor_id=executor_id,
                attempt_no=attempt_no,
                operation_id=operation_id,
            )
            conn.commit()
            return replay
        if row["status"] in {"cancelled", "completed"}:
            raise RuntimeControlError("runtime_failed_outcome_terminal_immutable")
        if row["status"] == "cancellation_requested":
            raise RuntimeControlError("runtime_failed_outcome_cancellation_won")
        if int(row["state_revision"]) != expected_state_revision:
            raise RuntimeControlError("runtime_failed_outcome_revision_conflict")
        _require_envelope_matches(
            admitted,
            runtime_run_id=runtime_run_id,
            terminal_reason_code=terminal_reason_code,
            terminal_at=terminal_at,
            attempt_no=attempt_no,
            operation_id=operation_id,
        )

        active_lease = _active_lease_row(conn, runtime_run_id)
        owner_supplied = executor_id is not None or attempt_no is not None
        if owner_supplied:
            if (
                type(executor_id) is not str
                or type(attempt_no) is not int
                or active_lease is None
                or row["status"] not in {"starting", "running", "pause_requested"}
                or active_lease["executor_id"] != executor_id
                or int(active_lease["attempt_no"]) != attempt_no
                or timestamp_lte(active_lease["lease_expires_at"], terminal_at)
            ):
                raise RuntimeControlError(
                    "runtime_failed_outcome_authority_rejected"
                )
        elif active_lease is not None:
            raise RuntimeControlError("runtime_failed_outcome_authority_rejected")

        hook(0, "before_failure_envelope")
        stored = store_failure_envelope_revision(conn, admitted)
        hook(1, "after_failure_envelope")

        hook(2, "before_authority_release")
        if active_lease is not None:
            released = conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET status = 'revoked', released_at = ?, reason_code = ?
                WHERE lease_id = ? AND status = 'active'
                  AND executor_id = ? AND attempt_no = ?
                """,
                (
                    terminal_at,
                    terminal_reason_code,
                    active_lease["lease_id"],
                    executor_id,
                    attempt_no,
                ),
            )
            if released.rowcount != 1:
                raise RuntimeControlError(
                    "runtime_failed_outcome_authority_rejected"
                )
        hook(3, "after_authority_release")

        hook(4, "before_run_update")
        updated = conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = 'failed',
                current_stage = 'failed',
                product_outcome = 'failed',
                current_failure_id = ?,
                current_failure_revision = ?,
                stop_reason_code = ?,
                updated_at = ?,
                completed_at = ?,
                state_revision = state_revision + 1
            WHERE runtime_run_id = ?
              AND state_revision = ?
              AND status NOT IN (
                'cancellation_requested', 'cancelled', 'completed', 'failed'
              )
            """,
            (
                stored.ref.failure_id,
                stored.ref.revision,
                terminal_reason_code,
                terminal_at,
                terminal_at,
                runtime_run_id,
                expected_state_revision,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError("runtime_failed_outcome_revision_conflict")
        hook(5, "after_run_update")

        committed = _run_row(conn, runtime_run_id)
        if committed is None:
            raise RuntimeControlError("runtime_failed_outcome_integrity_failed")
        validate_failed_outcome_row(conn, committed)
        if (
            committed["current_failure_id"] != admitted.failure_id
            or committed["current_failure_revision"] != admitted.revision
            or _active_lease_row(conn, runtime_run_id) is not None
        ):
            raise RuntimeControlError("runtime_failed_outcome_integrity_failed")
        hook(6, "before_commit")
        conn.commit()
        hook(7, "after_commit")
        return committed
    except FailureEnvelopeStorageError:
        conn.rollback()
        raise RuntimeControlError(
            "runtime_failed_outcome_integrity_failed"
        ) from None
    except RuntimeControlError:
        conn.rollback()
        raise
    except sqlite3.Error:
        conn.rollback()
        raise RuntimeControlError("runtime_failed_outcome_storage_failed") from None
    except RuntimeError:
        conn.rollback()
        raise


def validate_failed_outcome_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> None:
    outcome_truth = (
        row["product_outcome"],
        row["current_failure_id"],
        row["current_failure_revision"],
    )
    if outcome_truth == (None, None, None):
        return
    if (
        row["status"] != "failed"
        or row["product_outcome"] != "failed"
        or row["current_failure_id"] is None
        or row["current_failure_revision"] is None
    ):
        raise RuntimeControlError("runtime_failed_outcome_integrity_failed")
    try:
        envelope = load_failure_envelope_revision(
            conn,
            failure_id=row["current_failure_id"],
            revision=row["current_failure_revision"],
        )
    except FailureEnvelopeStorageError:
        raise RuntimeControlError(
            "runtime_failed_outcome_integrity_failed"
        ) from None
    if (
        envelope.run_id != row["runtime_run_id"]
        or envelope.current_outcome != "failed"
        or envelope.reason_code != row["stop_reason_code"]
        or envelope.occurred_at != row["completed_at"]
    ):
        raise RuntimeControlError("runtime_failed_outcome_integrity_failed")


def _admit_envelope(
    envelope: FailureEnvelopeV1 | bytes,
) -> FailureEnvelopeV1:
    try:
        if type(envelope) is bytes:
            return parse_failure_envelope(envelope)
        if type(envelope) is FailureEnvelopeV1:
            return parse_failure_envelope(canonical_diagnostics_bytes(envelope))
    except ValueError:
        raise RuntimeControlError(
            "runtime_failed_outcome_admission_failed"
        ) from None
    raise RuntimeControlError("runtime_failed_outcome_admission_failed")


def _require_envelope_matches(
    envelope: FailureEnvelopeV1,
    *,
    runtime_run_id: str,
    terminal_reason_code: str,
    terminal_at: str,
    attempt_no: int | None,
    operation_id: str | None,
) -> None:
    if (
        envelope.run_id != runtime_run_id
        or envelope.current_outcome != "failed"
        or envelope.reason_code != terminal_reason_code
        or envelope.occurred_at != terminal_at
        or (attempt_no is not None and envelope.attempt_no != attempt_no)
        or (operation_id is not None and envelope.operation_id != operation_id)
    ):
        raise RuntimeControlError("runtime_failed_outcome_envelope_mismatch")


def _require_exact_replay(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    envelope: FailureEnvelopeV1,
    *,
    terminal_reason_code: str,
    terminal_at: str,
    expected_state_revision: int,
    executor_id: str | None,
    attempt_no: int | None,
    operation_id: str | None,
) -> sqlite3.Row:
    _require_envelope_matches(
        envelope,
        runtime_run_id=row["runtime_run_id"],
        terminal_reason_code=terminal_reason_code,
        terminal_at=terminal_at,
        attempt_no=attempt_no,
        operation_id=operation_id,
    )
    if (
        int(row["state_revision"]) != expected_state_revision + 1
        or row["stop_reason_code"] != terminal_reason_code
        or row["completed_at"] != terminal_at
        or row["current_failure_id"] != envelope.failure_id
        or row["current_failure_revision"] != envelope.revision
        or _active_lease_row(conn, row["runtime_run_id"]) is not None
    ):
        raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
    if executor_id is not None or attempt_no is not None:
        if type(executor_id) is not str or type(attempt_no) is not int:
            raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
        lease = conn.execute(
            """
            SELECT *
            FROM runtime_control_executor_leases
            WHERE runtime_run_id = ? AND executor_id = ? AND attempt_no = ?
            """,
            (row["runtime_run_id"], executor_id, attempt_no),
        ).fetchone()
        if lease is None or lease["status"] not in {"released", "revoked"}:
            raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
    loaded = load_failure_envelope_revision(
        conn,
        failure_id=envelope.failure_id,
        revision=envelope.revision,
    )
    if loaded != envelope:
        raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
    validate_failed_outcome_row(conn, row)
    return row


def _run_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (runtime_run_id,),
    ).fetchone()


def _active_lease_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND status = 'active'
        ORDER BY attempt_no DESC
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()
