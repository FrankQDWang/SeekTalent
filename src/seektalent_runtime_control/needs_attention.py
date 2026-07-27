"""Atomic main-owned needs-attention lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import sqlite3
from typing import Never
import weakref

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
from seektalent.user_action import UserActionV1
from seektalent.source_port.verify_session_contract import VerifySessionResultV1
from seektalent_runtime_control.checkpoint_participant import (
    write_checkpoint_participant,
)
from seektalent_runtime_control.clock import timestamp_lte
from seektalent_runtime_control.errors import (
    RuntimeControlError,
    RuntimeControlLookupError,
)
from seektalent_runtime_control.failed_outcome import validate_failed_outcome_row
from seektalent_runtime_control.models import RuntimeCheckpoint
from seektalent_runtime_control.user_action_mapping import (
    map_verify_session_user_action,
)


StatementHook = Callable[[int, str], None]
_MAX_SAFE_INTEGER = 9007199254740991


def _normalized_sql(sql: str) -> str:
    return " ".join(sql.lower().split())

NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS = (
    """
    ALTER TABLE runtime_control_runs
    ADD COLUMN current_action_id TEXT
      CHECK (
        current_action_id IS NULL
        OR (
          status = 'needs_attention'
          AND product_outcome = 'needs_attention'
          AND current_failure_id IS NOT NULL
          AND current_failure_revision IS NOT NULL
        )
      )
    """,
    """
    CREATE TABLE runtime_control_user_actions (
      action_id TEXT PRIMARY KEY,
      runtime_run_id TEXT NOT NULL,
      action_code TEXT NOT NULL,
      instruction_key TEXT NOT NULL,
      action_scope TEXT NOT NULL,
      affected_scope_ref TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      checkpoint_id TEXT NOT NULL,
      checkpoint_hash TEXT NOT NULL,
      candidate_truth_hash TEXT NOT NULL,
      failure_id TEXT NOT NULL,
      failure_revision INTEGER NOT NULL,
      status TEXT NOT NULL,
      resolution_evidence_ref TEXT,
      resolution_at TEXT,
      authority_mode TEXT NOT NULL,
      owner_lease_id TEXT,
      created_at TEXT NOT NULL,
      CHECK (status IN ('pending', 'resolved', 'cancelled', 'failed')),
      CHECK (
        (status = 'pending' AND resolution_evidence_ref IS NULL AND resolution_at IS NULL)
        OR
        (status <> 'pending' AND resolution_evidence_ref IS NOT NULL AND resolution_at IS NOT NULL)
      ),
      CHECK (
        (authority_mode = 'no_owner' AND owner_lease_id IS NULL)
        OR
        (authority_mode = 'active_owner' AND owner_lease_id IS NOT NULL)
      ),
      CHECK (failure_revision >= 1 AND failure_revision <= 9007199254740991)
    )
    """,
    """
    CREATE UNIQUE INDEX idx_runtime_user_actions_one_pending
      ON runtime_control_user_actions(runtime_run_id)
      WHERE status = 'pending'
    """,
    """
    CREATE INDEX idx_runtime_user_actions_run_created
      ON runtime_control_user_actions(runtime_run_id, created_at, action_id)
    """,
    """
    CREATE TRIGGER runtime_user_actions_immutable_binding
    BEFORE UPDATE ON runtime_control_user_actions
    WHEN
      NEW.action_id <> OLD.action_id
      OR NEW.runtime_run_id <> OLD.runtime_run_id
      OR NEW.action_code <> OLD.action_code
      OR NEW.instruction_key <> OLD.instruction_key
      OR NEW.action_scope <> OLD.action_scope
      OR NEW.affected_scope_ref <> OLD.affected_scope_ref
      OR NEW.operation_id <> OLD.operation_id
      OR NEW.checkpoint_id <> OLD.checkpoint_id
      OR NEW.checkpoint_hash <> OLD.checkpoint_hash
      OR NEW.candidate_truth_hash <> OLD.candidate_truth_hash
      OR NEW.failure_id <> OLD.failure_id
      OR NEW.failure_revision <> OLD.failure_revision
      OR NEW.authority_mode <> OLD.authority_mode
      OR COALESCE(NEW.owner_lease_id, '') <> COALESCE(OLD.owner_lease_id, '')
      OR NEW.created_at <> OLD.created_at
    BEGIN
      SELECT RAISE(ABORT, 'runtime_user_action_binding_immutable');
    END
    """,
    """
    CREATE TRIGGER runtime_user_actions_one_way_resolution
    BEFORE UPDATE ON runtime_control_user_actions
    WHEN
      OLD.status <> 'pending'
      OR NEW.status = 'pending'
      OR NEW.status NOT IN ('resolved', 'cancelled', 'failed')
    BEGIN
      SELECT RAISE(ABORT, 'runtime_user_action_resolution_immutable');
    END
    """,
    """
    CREATE TRIGGER runtime_user_actions_delete_forbidden
    BEFORE DELETE ON runtime_control_user_actions
    BEGIN
      SELECT RAISE(ABORT, 'runtime_user_action_delete_forbidden');
    END
    """,
)

_V15_OBJECTS = {
    "table": {"runtime_control_user_actions"},
    "index": {
        "idx_runtime_user_actions_one_pending",
        "idx_runtime_user_actions_run_created",
    },
    "trigger": {
        "runtime_user_actions_immutable_binding",
        "runtime_user_actions_one_way_resolution",
        "runtime_user_actions_delete_forbidden",
    },
}
_ACTION_COLUMNS = {
    "action_id": ("TEXT", 0, None, 1, 0),
    "runtime_run_id": ("TEXT", 1, None, 0, 0),
    "action_code": ("TEXT", 1, None, 0, 0),
    "instruction_key": ("TEXT", 1, None, 0, 0),
    "action_scope": ("TEXT", 1, None, 0, 0),
    "affected_scope_ref": ("TEXT", 1, None, 0, 0),
    "operation_id": ("TEXT", 1, None, 0, 0),
    "checkpoint_id": ("TEXT", 1, None, 0, 0),
    "checkpoint_hash": ("TEXT", 1, None, 0, 0),
    "candidate_truth_hash": ("TEXT", 1, None, 0, 0),
    "failure_id": ("TEXT", 1, None, 0, 0),
    "failure_revision": ("INTEGER", 1, None, 0, 0),
    "status": ("TEXT", 1, None, 0, 0),
    "resolution_evidence_ref": ("TEXT", 0, None, 0, 0),
    "resolution_at": ("TEXT", 0, None, 0, 0),
    "authority_mode": ("TEXT", 1, None, 0, 0),
    "owner_lease_id": ("TEXT", 0, None, 0, 0),
    "created_at": ("TEXT", 1, None, 0, 0),
}
_ACTION_COLUMN_ORDER = tuple(_ACTION_COLUMNS)
_EXPECTED_OBJECT_SQL = {
    "runtime_control_user_actions": _normalized_sql(
        NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[1]
    ),
    "idx_runtime_user_actions_one_pending": _normalized_sql(
        NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[2]
    ),
    "idx_runtime_user_actions_run_created": _normalized_sql(
        NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[3]
    ),
    "runtime_user_actions_immutable_binding": _normalized_sql(
        NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[4]
    ),
    "runtime_user_actions_one_way_resolution": _normalized_sql(
        NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[5]
    ),
    "runtime_user_actions_delete_forbidden": _normalized_sql(
        NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[6]
    ),
}
_CURRENT_ACTION_DEFINITION = _normalized_sql(
    NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[0]
).split(" add column ", 1)[1]


@dataclass(frozen=True, slots=True)
class _NeedsAttentionAdmissionData:
    action: UserActionV1
    runtime_run_id: str
    operation_id: str
    checkpoint_id: str
    frozen_required_source_ids: tuple[str, ...]
    reconciliation_evidence_ref: str


@dataclass(frozen=True, slots=True)
class _ActionSatisfactionData:
    action: UserActionV1
    runtime_run_id: str
    operation_id: str
    checkpoint_id: str
    authenticated_evidence_ref: str
    current_profile_binding_ref: str
    current_profile_binding_generation: int
    current_browser_control_scope_id: str


class NeedsAttentionAdmission:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("NeedsAttentionAdmission is factory-only")

    def __copy__(self) -> Never:
        raise TypeError("NeedsAttentionAdmission cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("NeedsAttentionAdmission cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("NeedsAttentionAdmission cannot be serialized")


class ActionSatisfactionAdmission:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("ActionSatisfactionAdmission is factory-only")

    def __copy__(self) -> Never:
        raise TypeError("ActionSatisfactionAdmission cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("ActionSatisfactionAdmission cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("ActionSatisfactionAdmission cannot be serialized")


_ENTRY_ADMISSIONS: weakref.WeakKeyDictionary[
    NeedsAttentionAdmission,
    _NeedsAttentionAdmissionData,
] = weakref.WeakKeyDictionary()
_SATISFACTION_ADMISSIONS: weakref.WeakKeyDictionary[
    ActionSatisfactionAdmission,
    _ActionSatisfactionData,
] = weakref.WeakKeyDictionary()


def admit_needs_attention(
    *,
    result: VerifySessionResultV1,
    checkpoint_id: str,
    frozen_required_source_ids: tuple[str, ...],
    reconciliation_evidence_ref: str,
) -> NeedsAttentionAdmission:
    if (
        type(result) is not VerifySessionResultV1
        or result.session_readiness != "not_ready"
        or result.user_action is None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        )
    action = map_verify_session_user_action(
        result.user_action,
        affected_scope_ref=result.identity.browser_control_scope_id,
    )
    if (
        type(checkpoint_id) is not str
        or frozen_required_source_ids != ("liepin",)
        or not _sha256_hex(reconciliation_evidence_ref)
    ):
        raise RuntimeControlError("runtime_needs_attention_admission_rejected")
    admission = object.__new__(NeedsAttentionAdmission)
    _ENTRY_ADMISSIONS[admission] = _NeedsAttentionAdmissionData(
        action=action,
        runtime_run_id=result.identity.run_id,
        operation_id=result.identity.operation_id,
        checkpoint_id=checkpoint_id,
        frozen_required_source_ids=frozen_required_source_ids,
        reconciliation_evidence_ref=reconciliation_evidence_ref,
    )
    return admission


def admit_action_satisfaction(
    *,
    action: UserActionV1,
    result: VerifySessionResultV1,
    checkpoint_id: str,
    authenticated_evidence_ref: str,
) -> ActionSatisfactionAdmission:
    action = UserActionV1.model_validate(
        action.model_dump(mode="python"),
        strict=True,
    )
    if (
        type(result) is not VerifySessionResultV1
        or result.session_readiness != "ready"
        or result.user_action is not None
        or type(checkpoint_id) is not str
        or not _sha256_hex(authenticated_evidence_ref)
        or result.identity.browser_control_scope_id
        != action.affected_scope_ref
        or result.actual_profile_binding_ref is None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    admission = object.__new__(ActionSatisfactionAdmission)
    _SATISFACTION_ADMISSIONS[admission] = _ActionSatisfactionData(
        action=action,
        runtime_run_id=result.identity.run_id,
        operation_id=result.identity.operation_id,
        checkpoint_id=checkpoint_id,
        authenticated_evidence_ref=authenticated_evidence_ref,
        current_profile_binding_ref=result.actual_profile_binding_ref,
        current_profile_binding_generation=(
            result.actual_profile_binding_generation
        ),
        current_browser_control_scope_id=(
            result.identity.browser_control_scope_id
        ),
    )
    return admission


def migrate_needs_attention_v14_to_v15(conn: sqlite3.Connection) -> None:
    try:
        incomplete = conn.execute(
            """
            SELECT 1
            FROM runtime_control_runs
            WHERE status = 'needs_attention'
               OR product_outcome = 'needs_attention'
            LIMIT 1
            """
        ).fetchone()
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(runtime_control_runs)")
        }
        objects = {
            (row[0], row[1])
            for row in conn.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE name LIKE 'runtime_user_actions_%'
                   OR name LIKE 'idx_runtime_user_actions_%'
                   OR name = 'runtime_control_user_actions'
                """
            )
        }
    except sqlite3.Error:
        raise RuntimeControlError(
            "runtime_needs_attention_schema_collision"
        ) from None
    if incomplete is not None:
        raise RuntimeControlError(
            "runtime_needs_attention_incomplete_migration"
        )
    expected_objects = {
        (kind, name)
        for kind, names in _V15_OBJECTS.items()
        for name in names
    }
    current_present = "current_action_id" in columns
    if current_present or objects:
        if not current_present or objects != expected_objects:
            raise RuntimeControlError(
                "runtime_needs_attention_schema_collision"
            )
        validate_needs_attention_schema(conn)
        return
    for statement in NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS:
        conn.execute(statement)
    validate_needs_attention_schema(conn)


def validate_needs_attention_schema(conn: sqlite3.Connection) -> None:
    try:
        run_columns = {
            row[1]: (row[2].upper(), row[3], row[4], row[5], row[6])
            for row in conn.execute("PRAGMA table_xinfo(runtime_control_runs)")
        }
        action_column_rows = list(
            conn.execute(
                "PRAGMA table_xinfo(runtime_control_user_actions)"
            )
        )
        action_columns = {
            row[1]: (row[2].upper(), row[3], row[4], row[5], row[6])
            for row in action_column_rows
        }
        object_rows = list(
            conn.execute(
                """
                SELECT type, name, sql
                FROM sqlite_master
                WHERE name LIKE 'runtime_user_actions_%'
                   OR name LIKE 'idx_runtime_user_actions_%'
                   OR name = 'runtime_control_user_actions'
                """
            )
        )
        objects = {(row[0], row[1]) for row in object_rows}
        object_sql = {
            row[1]: _normalized_sql(row[2])
            for row in object_rows
            if row[2] is not None
        }
        run_sql_row = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'runtime_control_runs'
            """
        ).fetchone()
        if run_sql_row is None or run_sql_row[0] is None:
            raise sqlite3.DatabaseError
        from seektalent_runtime_control.failed_outcome import (
            _top_level_definitions,
        )

        run_definitions = {
            definition.split(" ", 1)[0]: definition
            for definition in _top_level_definitions(run_sql_row[0])
        }
    except sqlite3.Error:
        raise RuntimeControlError(
            "runtime_needs_attention_schema_collision"
        ) from None
    expected_objects = {
        (kind, name)
        for kind, names in _V15_OBJECTS.items()
        for name in names
    }
    if (
        run_columns.get("current_action_id")
        != ("TEXT", 0, None, 0, 0)
        or run_definitions.get("current_action_id")
        != _CURRENT_ACTION_DEFINITION
        or action_columns != _ACTION_COLUMNS
        or tuple(row[1] for row in action_column_rows)
        != _ACTION_COLUMN_ORDER
        or objects != expected_objects
        or object_sql != _EXPECTED_OBJECT_SQL
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_schema_collision"
        )


def commit_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    admission: NeedsAttentionAdmission,
    checkpoint: RuntimeCheckpoint,
    envelope: FailureEnvelopeV1 | bytes,
    expected_state_revision: int,
    entered_at: str,
    executor_id: str | None,
    attempt_no: int | None,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    data = _entry_admission(admission)
    admitted_envelope = _admit_envelope(envelope)
    _require_revision(expected_state_revision)
    _require_timestamp(entered_at)
    if (
        runtime_run_id != data.runtime_run_id
        or checkpoint.runtime_run_id != runtime_run_id
        or checkpoint.checkpoint_id != data.checkpoint_id
        or not _opaque(action_id)
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_admission_mismatch"
        )
    _require_needs_attention_envelope(
        admitted_envelope,
        data=data,
        runtime_run_id=runtime_run_id,
        entered_at=entered_at,
        attempt_no=attempt_no,
    )
    hook = statement_hook or (lambda _index, _phase: None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _run_row(conn, runtime_run_id)
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        if row["status"] == "needs_attention":
            replay = _require_entry_replay(
                conn,
                row=row,
                action_id=action_id,
                data=data,
                checkpoint=checkpoint,
                envelope=admitted_envelope,
                expected_state_revision=expected_state_revision,
                executor_id=executor_id,
                attempt_no=attempt_no,
            )
            conn.commit()
            return replay
        if row["status"] in {
            "cancellation_requested",
            "cancelled",
        }:
            raise RuntimeControlError(
                "runtime_needs_attention_cancellation_won"
            )
        if row["status"] in {"completed", "failed"}:
            raise RuntimeControlError(
                "runtime_needs_attention_terminal_immutable"
            )
        if int(row["state_revision"]) != expected_state_revision:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        if any(
            row[name] is not None
            for name in (
                "product_outcome",
                "current_failure_id",
                "current_failure_revision",
                "current_failure_owner_lease_id",
                "current_failure_authority_mode",
                "current_action_id",
            )
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_truth_conflict"
            )
        active_lease = _active_lease_row(conn, runtime_run_id)
        owner_supplied = type(executor_id) is str and type(attempt_no) is int
        if (executor_id is None) != (attempt_no is None):
            raise RuntimeControlError(
                "runtime_needs_attention_authority_rejected"
            )
        if owner_supplied:
            if (
                row["status"]
                not in {"starting", "running", "pause_requested"}
                or active_lease is None
                or active_lease["executor_id"] != executor_id
                or int(active_lease["attempt_no"]) != attempt_no
                or timestamp_lte(active_lease["lease_expires_at"], entered_at)
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_authority_rejected"
                )
        else:
            if row["status"] != "resume_requested" or active_lease is not None:
                raise RuntimeControlError(
                    "runtime_needs_attention_authority_rejected"
                )
            _require_no_reconcile_first(conn, runtime_run_id)
            if row["latest_checkpoint_id"] != checkpoint.checkpoint_id:
                raise RuntimeControlError(
                    "runtime_needs_attention_checkpoint_mismatch"
                )
        owner_lease_id = (
            active_lease["lease_id"]
            if owner_supplied and active_lease is not None
            else None
        )

        hook(0, "before_checkpoint")
        write_checkpoint_participant(conn, checkpoint)
        hook(1, "after_checkpoint")
        checkpoint_hash = _checkpoint_hash(checkpoint)
        candidate_truth_hash = _candidate_truth_hash(checkpoint)

        hook(2, "before_failure_envelope")
        stored = store_failure_envelope_revision(conn, admitted_envelope)
        hook(3, "after_failure_envelope")

        hook(4, "before_action")
        conn.execute(
            """
            INSERT INTO runtime_control_user_actions (
                action_id, runtime_run_id, action_code, instruction_key,
                action_scope, affected_scope_ref, operation_id, checkpoint_id,
                checkpoint_hash, candidate_truth_hash, failure_id,
                failure_revision, status, resolution_evidence_ref,
                resolution_at, authority_mode, owner_lease_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?)
            """,
            (
                action_id,
                runtime_run_id,
                data.action.code,
                data.action.instruction_key,
                data.action.scope,
                data.action.affected_scope_ref,
                data.operation_id,
                checkpoint.checkpoint_id,
                checkpoint_hash,
                candidate_truth_hash,
                stored.ref.failure_id,
                stored.ref.revision,
                "active_owner" if owner_supplied else "no_owner",
                owner_lease_id,
                entered_at,
            ),
        )
        hook(5, "after_action")

        hook(6, "before_authority_release")
        if owner_lease_id is not None:
            released = conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET status = 'revoked', released_at = ?,
                    reason_code = 'runtime_needs_attention'
                WHERE lease_id = ? AND runtime_run_id = ?
                  AND executor_id = ? AND attempt_no = ? AND status = 'active'
                """,
                (
                    entered_at,
                    owner_lease_id,
                    runtime_run_id,
                    executor_id,
                    attempt_no,
                ),
            )
            if released.rowcount != 1:
                raise RuntimeControlError(
                    "runtime_needs_attention_authority_rejected"
                )
        hook(7, "after_authority_release")

        hook(8, "before_run_update")
        updated = conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = 'needs_attention',
                current_stage = ?,
                current_round = ?,
                latest_checkpoint_id = ?,
                product_outcome = 'needs_attention',
                current_failure_id = ?,
                current_failure_revision = ?,
                current_failure_owner_lease_id = ?,
                current_failure_authority_mode = ?,
                current_action_id = ?,
                stop_reason_code = ?,
                updated_at = ?,
                completed_at = NULL,
                state_revision = state_revision + 1
            WHERE runtime_run_id = ? AND state_revision = ?
              AND status = ?
              AND product_outcome IS NULL
              AND current_failure_id IS NULL
              AND current_failure_revision IS NULL
              AND current_failure_owner_lease_id IS NULL
              AND current_failure_authority_mode IS NULL
              AND current_action_id IS NULL
            """,
            (
                checkpoint.stage,
                checkpoint.round_no,
                checkpoint.checkpoint_id,
                stored.ref.failure_id,
                stored.ref.revision,
                owner_lease_id,
                "active_owner" if owner_supplied else "no_owner",
                action_id,
                admitted_envelope.reason_code,
                entered_at,
                runtime_run_id,
                expected_state_revision,
                row["status"],
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        hook(9, "after_run_update")
        committed = _run_row(conn, runtime_run_id)
        if committed is None:
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        validate_needs_attention_row(conn, committed)
        hook(10, "before_commit")
        conn.commit()
        hook(11, "after_commit")
        return committed
    except FailureEnvelopeStorageError:
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None
    except RuntimeControlError:
        _rollback(conn)
        raise
    except (sqlite3.Error, TypeError, ValueError):
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_storage_failed"
        ) from None
    except RuntimeError:
        _rollback(conn)
        raise


def resolve_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    admission: ActionSatisfactionAdmission,
    expected_state_revision: int,
    resolved_at: str,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    data = _satisfaction_admission(admission)
    if runtime_run_id != data.runtime_run_id or not _opaque(action_id):
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_mismatch"
        )
    return _exit_needs_attention(
        conn,
        runtime_run_id=runtime_run_id,
        action_id=action_id,
        expected_state_revision=expected_state_revision,
        resolved_at=resolved_at,
        target_status="resume_requested",
        resolution_evidence_ref=data.authenticated_evidence_ref,
        satisfaction=data,
        failed_envelope=None,
        terminal_reason_code=None,
        statement_hook=statement_hook,
    )


def cancel_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    expected_state_revision: int,
    cancelled_at: str,
    cancellation_evidence_ref: str,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    if not _sha256_hex(cancellation_evidence_ref):
        raise RuntimeControlError(
            "runtime_needs_attention_cancellation_rejected"
        )
    return _exit_needs_attention(
        conn,
        runtime_run_id=runtime_run_id,
        action_id=action_id,
        expected_state_revision=expected_state_revision,
        resolved_at=cancelled_at,
        target_status="cancelled",
        resolution_evidence_ref=cancellation_evidence_ref,
        satisfaction=None,
        failed_envelope=None,
        terminal_reason_code=None,
        statement_hook=statement_hook,
    )


def fail_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    envelope: FailureEnvelopeV1 | bytes,
    expected_state_revision: int,
    terminal_reason_code: str,
    terminal_at: str,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    admitted = _admit_envelope(envelope)
    if (
        admitted.run_id != runtime_run_id
        or admitted.current_outcome != "failed"
        or admitted.user_action is not None
        or admitted.reason_code != terminal_reason_code
        or admitted.occurred_at != terminal_at
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_failed_envelope_mismatch"
        )
    return _exit_needs_attention(
        conn,
        runtime_run_id=runtime_run_id,
        action_id=action_id,
        expected_state_revision=expected_state_revision,
        resolved_at=terminal_at,
        target_status="failed",
        resolution_evidence_ref=f"sha256:{sha256(canonical_diagnostics_bytes(admitted)).hexdigest()}",
        satisfaction=None,
        failed_envelope=admitted,
        terminal_reason_code=terminal_reason_code,
        statement_hook=statement_hook,
    )


def _exit_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    expected_state_revision: int,
    resolved_at: str,
    target_status: str,
    resolution_evidence_ref: str,
    satisfaction: _ActionSatisfactionData | None,
    failed_envelope: FailureEnvelopeV1 | None,
    terminal_reason_code: str | None,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    _require_revision(expected_state_revision)
    _require_timestamp(resolved_at)
    if target_status not in {"resume_requested", "cancelled", "failed"}:
        raise RuntimeControlError(
            "runtime_needs_attention_resolution_rejected"
        )
    if not (
        _sha256_hex(resolution_evidence_ref)
        or (
            resolution_evidence_ref.startswith("sha256:")
            and _sha256_hex(resolution_evidence_ref[7:])
        )
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_resolution_rejected"
        )
    hook = statement_hook or (lambda _index, _phase: None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _run_row(conn, runtime_run_id)
        action_row = _action_row(conn, action_id)
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        if (
            row["status"] == target_status
            and action_row is not None
            and action_row["status"]
            == {
                "resume_requested": "resolved",
                "cancelled": "cancelled",
                "failed": "failed",
            }[target_status]
        ):
            replay = _require_exit_replay(
                conn,
                row=row,
                action_row=action_row,
                expected_state_revision=expected_state_revision,
                target_status=target_status,
                resolution_evidence_ref=resolution_evidence_ref,
                resolved_at=resolved_at,
                failed_envelope=failed_envelope,
            )
            conn.commit()
            return replay
        if row["status"] != "needs_attention":
            raise RuntimeControlError(
                "runtime_needs_attention_resolution_conflict"
            )
        validate_needs_attention_row(conn, row)
        if (
            int(row["state_revision"]) != expected_state_revision
            or row["current_action_id"] != action_id
            or action_row is None
            or action_row["runtime_run_id"] != runtime_run_id
            or action_row["status"] != "pending"
            or _active_lease_row(conn, runtime_run_id) is not None
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        checkpoint = _checkpoint_from_action(conn, action_row)
        if satisfaction is not None:
            if (
                satisfaction.action
                != _canonical_action_from_row(action_row)
                or satisfaction.operation_id != action_row["operation_id"]
                or satisfaction.checkpoint_id != action_row["checkpoint_id"]
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_satisfaction_mismatch"
                )
            _require_no_reconcile_first(conn, runtime_run_id)
        _require_checkpoint_binding(conn, action_row, checkpoint)

        new_failure_id: str | None = None
        new_failure_revision: int | None = None
        if failed_envelope is not None:
            hook(0, "before_failure_envelope")
            stored = store_failure_envelope_revision(conn, failed_envelope)
            new_failure_id = stored.ref.failure_id
            new_failure_revision = stored.ref.revision
            hook(1, "after_failure_envelope")

        hook(2, "before_action_resolution")
        action_status = {
            "resume_requested": "resolved",
            "cancelled": "cancelled",
            "failed": "failed",
        }[target_status]
        resolved = conn.execute(
            """
            UPDATE runtime_control_user_actions
            SET status = ?, resolution_evidence_ref = ?, resolution_at = ?
            WHERE action_id = ? AND runtime_run_id = ? AND status = 'pending'
            """,
            (
                action_status,
                resolution_evidence_ref,
                resolved_at,
                action_id,
                runtime_run_id,
            ),
        )
        if resolved.rowcount != 1:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        hook(3, "after_action_resolution")

        hook(4, "before_run_update")
        updated = conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = ?,
                current_stage = ?,
                product_outcome = ?,
                current_failure_id = ?,
                current_failure_revision = ?,
                current_failure_owner_lease_id = NULL,
                current_failure_authority_mode = ?,
                current_action_id = NULL,
                stop_reason_code = ?,
                updated_at = ?,
                completed_at = ?,
                state_revision = state_revision + 1
            WHERE runtime_run_id = ?
              AND state_revision = ?
              AND status = 'needs_attention'
              AND product_outcome = 'needs_attention'
              AND current_action_id = ?
            """,
            (
                target_status,
                target_status if target_status != "resume_requested" else checkpoint.stage,
                None if target_status == "resume_requested" else target_status,
                new_failure_id,
                new_failure_revision,
                "no_owner" if target_status == "failed" else None,
                terminal_reason_code,
                resolved_at,
                resolved_at if target_status in {"cancelled", "failed"} else None,
                runtime_run_id,
                expected_state_revision,
                action_id,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        hook(5, "after_run_update")
        committed = _run_row(conn, runtime_run_id)
        if committed is None:
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        validate_needs_attention_row(conn, committed)
        validate_failed_outcome_row(conn, committed)
        hook(6, "before_commit")
        conn.commit()
        hook(7, "after_commit")
        return committed
    except FailureEnvelopeStorageError:
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None
    except RuntimeControlError:
        _rollback(conn)
        raise
    except (sqlite3.Error, TypeError, ValueError):
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_storage_failed"
        ) from None
    except RuntimeError:
        _rollback(conn)
        raise


def validate_needs_attention_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> None:
    if row["status"] != "needs_attention":
        if (
            row["product_outcome"] == "needs_attention"
            or row["current_action_id"] is not None
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        return
    state_revision = row["state_revision"]
    if (
        type(state_revision) is not int
        or state_revision < 0
        or state_revision > _MAX_SAFE_INTEGER
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        )
    if row["status"] == "needs_attention":
        if (
            row["product_outcome"] != "needs_attention"
            or row["current_action_id"] is None
            or row["current_failure_id"] is None
            or row["current_failure_revision"] is None
            or row["current_failure_authority_mode"]
            not in {"no_owner", "active_owner"}
            or _active_lease_row(conn, row["runtime_run_id"]) is not None
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        action_row = _action_row(conn, row["current_action_id"])
        if (
            action_row is None
            or action_row["runtime_run_id"] != row["runtime_run_id"]
            or action_row["status"] != "pending"
            or action_row["failure_id"] != row["current_failure_id"]
            or action_row["failure_revision"]
            != row["current_failure_revision"]
            or action_row["checkpoint_id"] != row["latest_checkpoint_id"]
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        envelope = load_failure_envelope_revision(
            conn,
            failure_id=row["current_failure_id"],
            revision=row["current_failure_revision"],
        )
        if (
            envelope.run_id != row["runtime_run_id"]
            or envelope.current_outcome != "needs_attention"
            or envelope.user_action != _canonical_action_from_row(action_row)
            or envelope.operation_id != action_row["operation_id"]
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        checkpoint = _checkpoint_from_action(conn, action_row)
        _require_checkpoint_binding(conn, action_row, checkpoint)
        if row["current_failure_authority_mode"] != action_row["authority_mode"]:
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        if (
            row["current_failure_owner_lease_id"]
            != action_row["owner_lease_id"]
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        if action_row["authority_mode"] == "active_owner":
            lease = conn.execute(
                """
                SELECT * FROM runtime_control_executor_leases
                WHERE lease_id = ?
                """,
                (action_row["owner_lease_id"],),
            ).fetchone()
            if (
                lease is None
                or lease["runtime_run_id"] != row["runtime_run_id"]
                or lease["status"] != "revoked"
                or lease["reason_code"] != "runtime_needs_attention"
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_integrity_failed"
                )
        return


def _require_needs_attention_envelope(
    envelope: FailureEnvelopeV1,
    *,
    data: _NeedsAttentionAdmissionData,
    runtime_run_id: str,
    entered_at: str,
    attempt_no: int | None,
) -> None:
    if (
        envelope.run_id != runtime_run_id
        or envelope.operation_id != data.operation_id
        or envelope.current_outcome != "needs_attention"
        or envelope.user_action != data.action
        or envelope.reason_code != "user_action_required"
        or envelope.occurred_at != entered_at
        or (attempt_no is not None and envelope.attempt_no != attempt_no)
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_envelope_mismatch"
        )


def _require_entry_replay(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    action_id: str,
    data: _NeedsAttentionAdmissionData,
    checkpoint: RuntimeCheckpoint,
    envelope: FailureEnvelopeV1,
    expected_state_revision: int,
    executor_id: str | None,
    attempt_no: int | None,
) -> sqlite3.Row:
    action_row = _action_row(conn, action_id)
    owner_supplied = type(executor_id) is str and type(attempt_no) is int
    if not owner_supplied and (
        executor_id is not None or attempt_no is not None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    if (
        int(row["state_revision"]) != expected_state_revision + 1
        or row["current_action_id"] != action_id
        or action_row is None
        or action_row["status"] != "pending"
        or _canonical_action_from_row(action_row) != data.action
        or action_row["operation_id"] != data.operation_id
        or action_row["checkpoint_id"] != checkpoint.checkpoint_id
        or action_row["checkpoint_hash"] != _checkpoint_hash(checkpoint)
        or action_row["failure_id"] != envelope.failure_id
        or action_row["failure_revision"] != envelope.revision
        or (action_row["authority_mode"] == "active_owner")
        != owner_supplied
        or _active_lease_row(conn, row["runtime_run_id"]) is not None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    if owner_supplied:
        lease = conn.execute(
            """
            SELECT * FROM runtime_control_executor_leases
            WHERE lease_id = ? AND runtime_run_id = ?
            """,
            (action_row["owner_lease_id"], row["runtime_run_id"]),
        ).fetchone()
        if (
            lease is None
            or lease["executor_id"] != executor_id
            or lease["attempt_no"] != attempt_no
            or lease["status"] != "revoked"
            or lease["reason_code"] != "runtime_needs_attention"
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_replay_conflict"
            )
    validate_needs_attention_row(conn, row)
    validate_failed_outcome_row(conn, row)
    return row


def _require_exit_replay(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    action_row: sqlite3.Row,
    expected_state_revision: int,
    target_status: str,
    resolution_evidence_ref: str,
    resolved_at: str,
    failed_envelope: FailureEnvelopeV1 | None,
) -> sqlite3.Row:
    expected_outcome = None if target_status == "resume_requested" else target_status
    if (
        int(row["state_revision"]) != expected_state_revision + 1
        or row["product_outcome"] != expected_outcome
        or row["current_action_id"] is not None
        or action_row["resolution_evidence_ref"]
        != resolution_evidence_ref
        or action_row["resolution_at"] != resolved_at
        or _active_lease_row(conn, row["runtime_run_id"]) is not None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    if failed_envelope is not None and (
        row["current_failure_id"] != failed_envelope.failure_id
        or row["current_failure_revision"] != failed_envelope.revision
        or load_failure_envelope_revision(
            conn,
            failure_id=failed_envelope.failure_id,
            revision=failed_envelope.revision,
        )
        != failed_envelope
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    validate_needs_attention_row(conn, row)
    validate_failed_outcome_row(conn, row)
    return row


def _require_checkpoint_binding(
    conn: sqlite3.Connection,
    action_row: sqlite3.Row,
    checkpoint: RuntimeCheckpoint,
) -> None:
    if (
        _checkpoint_hash(checkpoint) != action_row["checkpoint_hash"]
        or _candidate_truth_hash(checkpoint)
        != action_row["candidate_truth_hash"]
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        )
    from seektalent_runtime_control.store import (
        _candidate_truth_matches_checkpoint,
    )

    if not _candidate_truth_matches_checkpoint(conn, checkpoint):
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        )


def _checkpoint_from_action(
    conn: sqlite3.Connection,
    action_row: sqlite3.Row,
) -> RuntimeCheckpoint:
    row = conn.execute(
        """
        SELECT * FROM runtime_control_checkpoints
        WHERE checkpoint_id = ? AND runtime_run_id = ?
        """,
        (action_row["checkpoint_id"], action_row["runtime_run_id"]),
    ).fetchone()
    if row is None:
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        )
    try:
        return RuntimeCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            runtime_run_id=row["runtime_run_id"],
            stage=row["stage"],
            round_no=row["round_no"],
            safe_boundary=row["safe_boundary"],
            run_state=json.loads(row["run_state_json"]),
            source_plan=json.loads(row["source_plan_json"]),
            pending_commands=json.loads(row["pending_commands_json"]),
            artifact_manifest_ref=row["artifact_manifest_ref"],
            schema_version=row["schema_version"],
            created_at=row["created_at"],
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        ) from None


def _canonical_action_from_row(row: sqlite3.Row) -> UserActionV1:
    try:
        return UserActionV1(
            code=row["action_code"],
            instruction_key=row["instruction_key"],
            scope=row["action_scope"],
            affected_scope_ref=row["affected_scope_ref"],
        )
    except ValueError:
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None


def _entry_admission(
    admission: NeedsAttentionAdmission,
) -> _NeedsAttentionAdmissionData:
    if type(admission) is not NeedsAttentionAdmission:
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        )
    data = _ENTRY_ADMISSIONS.get(admission)
    if data is None:
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        )
    return data


def _satisfaction_admission(
    admission: ActionSatisfactionAdmission,
) -> _ActionSatisfactionData:
    if type(admission) is not ActionSatisfactionAdmission:
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    data = _SATISFACTION_ADMISSIONS.get(admission)
    if data is None:
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    return data


def _admit_envelope(
    envelope: FailureEnvelopeV1 | bytes,
) -> FailureEnvelopeV1:
    try:
        if type(envelope) is bytes:
            return parse_failure_envelope(envelope)
        if type(envelope) is FailureEnvelopeV1:
            return parse_failure_envelope(
                canonical_diagnostics_bytes(envelope)
            )
    except ValueError:
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        ) from None
    raise RuntimeControlError(
        "runtime_needs_attention_admission_rejected"
    )


def _require_no_reconcile_first(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> None:
    if conn.execute(
        """
        SELECT 1
        FROM runtime_control_source_operations
        WHERE runtime_run_id = ? AND retry_posture = 'reconcile_first'
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone() is not None:
        raise RuntimeControlError(
            "runtime_needs_attention_reconciliation_unresolved"
        )


def _checkpoint_hash(checkpoint: RuntimeCheckpoint) -> str:
    payload = checkpoint.model_dump(mode="json")
    return sha256(_canonical_json(payload)).hexdigest()


def _candidate_truth_hash(checkpoint: RuntimeCheckpoint) -> str:
    from seektalent_runtime_control.candidates import (
        candidate_truth_from_run_state,
    )

    truth = candidate_truth_from_run_state(
        runtime_run_id=checkpoint.runtime_run_id,
        run_state=checkpoint.run_state,
        source_checkpoint_id=checkpoint.checkpoint_id,
        observed_at=checkpoint.created_at,
    )
    payload = {
        "identities": [
            item.model_dump(mode="json") for item in truth.identities
        ],
        "evidence": [
            item.model_dump(mode="json") for item in truth.evidence
        ],
        "finalization_revisions": [
            item.model_dump(mode="json")
            for item in truth.finalization_revisions
        ],
    }
    return sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _run_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (runtime_run_id,),
    ).fetchone()


def _action_row(
    conn: sqlite3.Connection,
    action_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_user_actions WHERE action_id = ?",
        (action_id,),
    ).fetchone()


def _active_lease_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND status = 'active'
        ORDER BY attempt_no DESC
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()


def _require_revision(value: int) -> None:
    if (
        type(value) is not int
        or value < 0
        or value > _MAX_SAFE_INTEGER
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_revision_conflict"
        )


def _require_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (AttributeError, TypeError, ValueError):
        raise RuntimeControlError(
            "runtime_needs_attention_time_invalid"
        ) from None


def _sha256_hex(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _opaque(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value.encode("utf-8")) <= 96
        and value.strip() == value
        and "\x00" not in value
    )


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        return


__all__ = [
    "ActionSatisfactionAdmission",
    "NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS",
    "NeedsAttentionAdmission",
    "admit_action_satisfaction",
    "admit_needs_attention",
    "cancel_needs_attention",
    "commit_needs_attention",
    "fail_needs_attention",
    "migrate_needs_attention_v14_to_v15",
    "resolve_needs_attention",
    "validate_needs_attention_row",
    "validate_needs_attention_schema",
]
