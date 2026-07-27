"""Atomic main-owned terminal failure boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
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


def _normalized_sql(sql: str) -> str:
    return " ".join(sql.lower().split())


def _store_decision_time() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


FAILED_OUTCOME_V14_SCHEMA_STATEMENTS = (
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN state_revision INTEGER NOT NULL DEFAULT 0
      CHECK (
        typeof(state_revision) = 'integer'
        AND state_revision >= 0
        AND state_revision <= 9007199254740991
      )
    """,
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN current_failure_revision INTEGER
      CHECK (
        current_failure_revision IS NULL
        OR (
          typeof(current_failure_revision) = 'integer'
          AND
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
        OR (
          product_outcome = 'needs_attention'
          AND status = 'needs_attention'
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
      )
    """,
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN current_failure_owner_lease_id TEXT
      CHECK (
        current_failure_owner_lease_id IS NULL
        OR (
          product_outcome IN ('failed', 'needs_attention')
          AND current_failure_id IS NOT NULL
          AND current_failure_revision IS NOT NULL
        )
      )
    """,
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN current_failure_authority_mode TEXT
      CHECK (
        (
          current_failure_authority_mode IS NULL
          AND current_failure_id IS NULL
          AND current_failure_revision IS NULL
          AND current_failure_owner_lease_id IS NULL
        )
        OR (
          current_failure_authority_mode = 'no_owner'
          AND product_outcome IN ('failed', 'needs_attention')
          AND current_failure_id IS NOT NULL
          AND current_failure_revision IS NOT NULL
          AND current_failure_owner_lease_id IS NULL
        )
        OR (
          current_failure_authority_mode = 'active_owner'
          AND product_outcome IN ('failed', 'needs_attention')
          AND current_failure_id IS NOT NULL
          AND current_failure_revision IS NOT NULL
          AND current_failure_owner_lease_id IS NOT NULL
        )
      )
    """,
)

_FAILED_OUTCOME_COLUMN_FACTS = {
    "state_revision": ("INTEGER", 1, "0", 0, 0),
    "current_failure_revision": ("INTEGER", 0, None, 0, 0),
    "current_failure_id": ("TEXT", 0, None, 0, 0),
    "product_outcome": ("TEXT", 0, None, 0, 0),
    "current_failure_owner_lease_id": ("TEXT", 0, None, 0, 0),
    "current_failure_authority_mode": ("TEXT", 0, None, 0, 0),
}
_FAILED_OUTCOME_COLUMN_DEFINITIONS = {
    _normalized_sql(statement).split(" add column ", 1)[1].split(" ", 1)[0]:
    _normalized_sql(statement).split(" add column ", 1)[1]
    for statement in FAILED_OUTCOME_V14_SCHEMA_STATEMENTS
}


def migrate_failed_outcome_v13_to_v14(conn: sqlite3.Connection) -> None:
    required_columns = {
        "state_revision",
        "current_failure_revision",
        "current_failure_id",
        "product_outcome",
        "current_failure_owner_lease_id",
        "current_failure_authority_mode",
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
        validate_failed_outcome_schema(conn)
        return
    for statement in FAILED_OUTCOME_V14_SCHEMA_STATEMENTS:
        conn.execute(statement)
    validate_failed_outcome_schema(conn)


def validate_failed_outcome_schema(conn: sqlite3.Connection) -> None:
    """Require the exact v14 column facts and canonical column constraints."""

    try:
        column_rows = {
            row[1]: (row[2].upper(), row[3], row[4], row[5], row[6])
            for row in conn.execute("PRAGMA table_xinfo(runtime_control_runs)")
        }
        table_sql_row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'runtime_control_runs'
            """
        ).fetchone()
    except sqlite3.Error:
        raise RuntimeControlError(
            "runtime_control_failed_outcome_schema_collision"
        ) from None
    if (
        table_sql_row is None
        or table_sql_row[0] is None
        or any(
            column_rows.get(name) != facts
            for name, facts in _FAILED_OUTCOME_COLUMN_FACTS.items()
        )
    ):
        raise RuntimeControlError(
            "runtime_control_failed_outcome_schema_collision"
        )
    definitions = {
        definition.split(" ", 1)[0]: definition
        for definition in _top_level_definitions(table_sql_row[0])
    }
    if any(
        definitions.get(name) != expected
        for name, expected in _FAILED_OUTCOME_COLUMN_DEFINITIONS.items()
    ):
        raise RuntimeControlError(
            "runtime_control_failed_outcome_schema_collision"
        )


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
    if (
        type(expected_state_revision) is not int
        or expected_state_revision < 0
        or expected_state_revision > 9007199254740991
    ):
        raise RuntimeControlError("runtime_failed_outcome_revision_conflict")
    hook = statement_hook or (lambda _index, _phase: None)
    try:
        conn.execute("BEGIN IMMEDIATE")
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
        require_run_truth_mutable(row)
        if _state_revision(row) != expected_state_revision:
            raise RuntimeControlError("runtime_failed_outcome_revision_conflict")
        _require_envelope_matches(
            admitted,
            runtime_run_id=runtime_run_id,
            terminal_reason_code=terminal_reason_code,
            terminal_at=terminal_at,
            attempt_no=attempt_no,
            operation_id=operation_id,
        )

        decision_at = _store_decision_time()
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
            ):
                raise RuntimeControlError(
                    "runtime_failed_outcome_authority_rejected"
                )
        if active_lease is not None:
            if timestamp_lte(active_lease["lease_expires_at"], decision_at):
                _commit_expired_authority(
                    conn,
                    row=row,
                    lease=active_lease,
                    decision_at=decision_at,
                    expected_state_revision=expected_state_revision,
                    hook=hook,
                )
            if not owner_supplied:
                raise RuntimeControlError(
                    "runtime_failed_outcome_authority_rejected"
                )

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
                    decision_at,
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
                current_failure_owner_lease_id = ?,
                current_failure_authority_mode = ?,
                stop_reason_code = ?,
                updated_at = ?,
                completed_at = ?,
                state_revision = state_revision + 1
            WHERE runtime_run_id = ?
              AND state_revision = ?
              AND status NOT IN (
                'cancellation_requested', 'cancelled', 'completed', 'failed'
              )
              AND product_outcome IS NULL
              AND current_failure_id IS NULL
              AND current_failure_revision IS NULL
              AND current_failure_owner_lease_id IS NULL
              AND current_failure_authority_mode IS NULL
            """,
            (
                stored.ref.failure_id,
                stored.ref.revision,
                (
                    active_lease["lease_id"]
                    if active_lease is not None
                    else None
                ),
                (
                    "active_owner"
                    if active_lease is not None
                    else "no_owner"
                ),
                terminal_reason_code,
                decision_at,
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
        _rollback_quietly(conn)
        raise RuntimeControlError(
            "runtime_failed_outcome_integrity_failed"
        ) from None
    except RuntimeControlError:
        _rollback_quietly(conn)
        raise
    except (sqlite3.Error, TypeError, ValueError):
        _rollback_quietly(conn)
        raise RuntimeControlError("runtime_failed_outcome_storage_failed") from None
    except RuntimeError:
        _rollback_quietly(conn)
        raise


def validate_failed_outcome_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> None:
    _state_revision(row)
    outcome_truth = (
        row["product_outcome"],
        row["current_failure_id"],
        row["current_failure_revision"],
        row["current_failure_owner_lease_id"],
        row["current_failure_authority_mode"],
    )
    if outcome_truth == (None, None, None, None, None):
        return
    try:
        active_lease_present = (
            _active_lease_row(conn, row["runtime_run_id"]) is not None
        )
    except sqlite3.Error:
        raise RuntimeControlError(
            "runtime_failed_outcome_integrity_failed"
        ) from None
    if (
        row["status"] != "failed"
        or row["product_outcome"] != "failed"
        or row["current_failure_id"] is None
        or row["current_failure_revision"] is None
        or type(row["current_failure_revision"]) is not int
        or row["current_failure_revision"] < 1
        or row["current_failure_revision"] > 9007199254740991
        or row["current_failure_authority_mode"]
        not in {"no_owner", "active_owner"}
        or (
            row["current_failure_authority_mode"] == "no_owner"
            and row["current_failure_owner_lease_id"] is not None
        )
        or (
            row["current_failure_authority_mode"] == "active_owner"
            and row["current_failure_owner_lease_id"] is None
        )
        or active_lease_present
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
    try:
        timestamp_lte(row["updated_at"], row["updated_at"])
        timestamp_lte(row["completed_at"], row["completed_at"])
    except (TypeError, ValueError):
        raise RuntimeControlError(
            "runtime_failed_outcome_integrity_failed"
        ) from None
    owner_lease_id = row["current_failure_owner_lease_id"]
    if row["current_failure_authority_mode"] == "active_owner":
        try:
            lease = conn.execute(
                """
                SELECT *
                FROM runtime_control_executor_leases
                WHERE lease_id = ?
                """,
                (owner_lease_id,),
            ).fetchone()
        except sqlite3.Error:
            raise RuntimeControlError(
                "runtime_failed_outcome_integrity_failed"
            ) from None
        if (
            lease is None
            or lease["runtime_run_id"] != row["runtime_run_id"]
            or envelope.attempt_no is None
            or lease["attempt_no"] != envelope.attempt_no
            or lease["status"] != "revoked"
            or lease["released_at"] != row["updated_at"]
            or lease["reason_code"] != row["stop_reason_code"]
        ):
            raise RuntimeControlError(
                "runtime_failed_outcome_integrity_failed"
            )
        try:
            if timestamp_lte(
                lease["lease_expires_at"],
                lease["released_at"],
            ):
                raise RuntimeControlError(
                    "runtime_failed_outcome_integrity_failed"
                )
        except (TypeError, ValueError):
            raise RuntimeControlError(
                "runtime_failed_outcome_integrity_failed"
            ) from None


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
        _state_revision(row) != expected_state_revision + 1
        or row["stop_reason_code"] != terminal_reason_code
        or row["completed_at"] != terminal_at
        or row["current_failure_id"] != envelope.failure_id
        or row["current_failure_revision"] != envelope.revision
        or _active_lease_row(conn, row["runtime_run_id"]) is not None
    ):
        raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
    owner_lease_id = row["current_failure_owner_lease_id"]
    authority_mode = row["current_failure_authority_mode"]
    owner_arguments_supplied = (
        type(executor_id) is str and type(attempt_no) is int
    )
    if (
        authority_mode == "active_owner"
        and not owner_arguments_supplied
    ) or (
        authority_mode == "no_owner"
        and owner_arguments_supplied
    ) or authority_mode not in {"no_owner", "active_owner"}:
        raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
    if (
        not owner_arguments_supplied
        and (executor_id is not None or attempt_no is not None)
    ):
        raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
    if owner_arguments_supplied:
        lease = conn.execute(
            """
            SELECT *
            FROM runtime_control_executor_leases
            WHERE runtime_run_id = ? AND executor_id = ? AND attempt_no = ?
            """,
            (row["runtime_run_id"], executor_id, attempt_no),
        ).fetchone()
        if (
            lease is None
            or lease["lease_id"] != owner_lease_id
            or lease["status"] != "revoked"
            or lease["released_at"] is None
            or lease["released_at"] != row["updated_at"]
            or lease["reason_code"] != terminal_reason_code
        ):
            raise RuntimeControlError("runtime_failed_outcome_replay_conflict")
        try:
            if timestamp_lte(
                lease["lease_expires_at"],
                lease["released_at"],
            ):
                raise RuntimeControlError(
                    "runtime_failed_outcome_replay_conflict"
                )
        except (TypeError, ValueError):
            raise RuntimeControlError(
                "runtime_failed_outcome_replay_conflict"
            ) from None
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


def _commit_expired_authority(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    lease: sqlite3.Row,
    decision_at: str,
    expected_state_revision: int,
    hook: Callable[[int, str], None],
) -> None:
    hook(2, "before_authority_expiry")
    expired = conn.execute(
        """
        UPDATE runtime_control_executor_leases
        SET status = 'expired', released_at = ?,
            reason_code = 'runtime_executor_lease_expired'
        WHERE lease_id = ?
          AND runtime_run_id = ?
          AND executor_id = ?
          AND attempt_no = ?
          AND status = 'active'
        """,
        (
            decision_at,
            lease["lease_id"],
            row["runtime_run_id"],
            lease["executor_id"],
            lease["attempt_no"],
        ),
    )
    if expired.rowcount != 1:
        raise RuntimeControlError(
            "runtime_failed_outcome_authority_rejected"
        )
    hook(3, "after_authority_expiry")
    hook(4, "before_expiry_revision")
    advanced = conn.execute(
        """
        UPDATE runtime_control_runs
        SET state_revision = state_revision + 1
        WHERE runtime_run_id = ?
          AND state_revision = ?
          AND product_outcome IS NULL
          AND current_failure_id IS NULL
          AND current_failure_revision IS NULL
          AND current_failure_owner_lease_id IS NULL
          AND current_failure_authority_mode IS NULL
        """,
        (row["runtime_run_id"], expected_state_revision),
    )
    if advanced.rowcount != 1:
        raise RuntimeControlError(
            "runtime_failed_outcome_authority_rejected"
        )
    hook(5, "after_expiry_revision")
    hook(6, "before_expiry_commit")
    conn.commit()
    hook(7, "after_expiry_commit")
    raise RuntimeControlError(
        "runtime_failed_outcome_authority_expired"
    )


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        return


def require_run_truth_mutable(row: sqlite3.Row) -> None:
    if (
        row["product_outcome"] is not None
        or row["current_failure_id"] is not None
        or row["current_failure_revision"] is not None
        or row["current_failure_owner_lease_id"] is not None
        or row["current_failure_authority_mode"] is not None
    ):
        raise RuntimeControlError("runtime_failed_outcome_terminal_immutable")


def _state_revision(row: sqlite3.Row) -> int:
    revision = row["state_revision"]
    if (
        type(revision) is not int
        or revision < 0
        or revision > 9007199254740991
    ):
        raise RuntimeControlError("runtime_failed_outcome_integrity_failed")
    return revision


def _top_level_definitions(table_sql: str) -> tuple[str, ...]:
    start = table_sql.find("(")
    end = table_sql.rfind(")")
    if start < 0 or end <= start:
        return ()
    definitions: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in table_sql[start + 1:end]:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            if depth < 0:
                return ()
            current.append(char)
        elif char == "," and depth == 0:
            definitions.append(_normalized_sql("".join(current)))
            current = []
        else:
            current.append(char)
    if quote is not None or depth != 0:
        return ()
    definitions.append(_normalized_sql("".join(current)))
    return tuple(definition for definition in definitions if definition)
