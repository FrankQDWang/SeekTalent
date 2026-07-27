"""SQLite v15 schema and migration for durable needs-attention truth."""

from __future__ import annotations

import sqlite3

from seektalent_runtime_control.errors import RuntimeControlError


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
    CREATE TABLE runtime_control_authenticated_observations (
      observation_ref TEXT PRIMARY KEY,
      result_digest TEXT NOT NULL,
      session_id TEXT NOT NULL,
      direction_seq INTEGER NOT NULL,
      message_id TEXT NOT NULL,
      reply_to TEXT NOT NULL,
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      source_id TEXT NOT NULL,
      operation_kind TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      request_semantic_digest TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      accepted_requirement_revision_id TEXT NOT NULL,
      runtime_attempt_no INTEGER NOT NULL,
      runtime_attempt_fence_ref TEXT NOT NULL,
      expected_ledger_revision INTEGER NOT NULL,
      expected_reconciliation_revision INTEGER NOT NULL,
      profile_binding_generation INTEGER NOT NULL,
      browser_control_scope_id TEXT NOT NULL,
      actual_profile_binding_ref TEXT NOT NULL,
      actual_profile_binding_generation INTEGER NOT NULL,
      session_readiness TEXT NOT NULL,
      action_digest TEXT,
      dispatch_authorization_ordinal INTEGER NOT NULL,
      dispatch_intent_id TEXT NOT NULL,
      dispatch_intent_digest TEXT NOT NULL,
      source_operation_acceptance_ref TEXT NOT NULL,
      committed_at TEXT NOT NULL,
      UNIQUE(session_id, direction_seq),
      UNIQUE(session_id, message_id),
      CHECK (source_id = 'liepin'),
      CHECK (operation_kind = 'verify_session'),
      CHECK (session_readiness IN ('ready', 'not_ready')),
      CHECK (
        (session_readiness = 'ready' AND action_digest IS NULL)
        OR
        (session_readiness = 'not_ready' AND action_digest IS NOT NULL)
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
      entry_observation_ref TEXT NOT NULL,
      entry_observation_digest TEXT NOT NULL,
      accepted_requirement_revision_id TEXT NOT NULL,
      runtime_attempt_no INTEGER NOT NULL,
      runtime_attempt_fence_ref TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      entry_request_semantic_digest TEXT NOT NULL,
      profile_binding_generation INTEGER NOT NULL,
      browser_control_scope_id TEXT NOT NULL,
      source_ledger_revision INTEGER NOT NULL,
      source_reconciliation_revision INTEGER NOT NULL,
      entry_dispatch_authorization_ordinal INTEGER NOT NULL,
      dispatch_intent_id TEXT NOT NULL,
      dispatch_intent_digest TEXT NOT NULL,
      source_operation_acceptance_ref TEXT NOT NULL,
      reconciliation_id TEXT,
      reconciliation_digest TEXT,
      failure_id TEXT NOT NULL,
      failure_revision INTEGER NOT NULL,
      status TEXT NOT NULL,
      resolution_evidence_ref TEXT,
      resolution_binding_digest TEXT,
      resolution_operation_id TEXT,
      resolution_result_digest TEXT,
      resolution_request_hash TEXT,
      resolution_request_semantic_digest TEXT,
      resolution_runtime_attempt_fence_ref TEXT,
      resolution_dispatch_authorization_ordinal INTEGER,
      resolution_reconciliation_id TEXT,
      resolution_reconciliation_digest TEXT,
      resolution_source_ledger_revision INTEGER,
      resolution_source_reconciliation_revision INTEGER,
      resolution_at TEXT,
      authority_mode TEXT NOT NULL,
      owner_lease_id TEXT,
      created_at TEXT NOT NULL,
      CHECK (status IN ('pending', 'resolved', 'cancelled', 'failed')),
      CHECK (
        (status = 'pending' AND resolution_evidence_ref IS NULL
          AND resolution_binding_digest IS NULL
          AND resolution_operation_id IS NULL
          AND resolution_result_digest IS NULL
          AND resolution_request_hash IS NULL
          AND resolution_request_semantic_digest IS NULL
          AND resolution_runtime_attempt_fence_ref IS NULL
          AND resolution_dispatch_authorization_ordinal IS NULL
          AND resolution_reconciliation_id IS NULL
          AND resolution_reconciliation_digest IS NULL
          AND resolution_source_ledger_revision IS NULL
          AND resolution_source_reconciliation_revision IS NULL
          AND resolution_at IS NULL)
        OR
        (status = 'resolved' AND resolution_evidence_ref IS NOT NULL
          AND resolution_binding_digest IS NOT NULL
          AND resolution_operation_id IS NOT NULL
          AND resolution_result_digest IS NOT NULL
          AND resolution_request_hash IS NOT NULL
          AND resolution_request_semantic_digest IS NOT NULL
          AND resolution_runtime_attempt_fence_ref IS NOT NULL
          AND resolution_dispatch_authorization_ordinal IS NOT NULL
          AND resolution_reconciliation_id IS NOT NULL
          AND resolution_reconciliation_digest IS NOT NULL
          AND resolution_source_ledger_revision IS NOT NULL
          AND resolution_source_reconciliation_revision IS NOT NULL
          AND resolution_at IS NOT NULL)
        OR
        (status IN ('cancelled', 'failed')
          AND resolution_evidence_ref IS NOT NULL
          AND resolution_binding_digest IS NOT NULL
          AND resolution_operation_id IS NULL
          AND resolution_result_digest IS NULL
          AND resolution_request_hash IS NULL
          AND resolution_request_semantic_digest IS NULL
          AND resolution_runtime_attempt_fence_ref IS NULL
          AND resolution_dispatch_authorization_ordinal IS NULL
          AND resolution_reconciliation_id IS NULL
          AND resolution_reconciliation_digest IS NULL
          AND resolution_source_ledger_revision IS NULL
          AND resolution_source_reconciliation_revision IS NULL
          AND resolution_at IS NOT NULL)
      ),
      CHECK (
        (authority_mode = 'no_owner' AND owner_lease_id IS NULL
          AND reconciliation_id IS NOT NULL AND reconciliation_digest IS NOT NULL)
        OR
        (authority_mode = 'active_owner' AND owner_lease_id IS NOT NULL
          AND reconciliation_id IS NULL AND reconciliation_digest IS NULL)
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
      OR NEW.entry_observation_ref <> OLD.entry_observation_ref
      OR NEW.entry_observation_digest <> OLD.entry_observation_digest
      OR NEW.entry_request_semantic_digest
         <> OLD.entry_request_semantic_digest
      OR NEW.entry_dispatch_authorization_ordinal
         <> OLD.entry_dispatch_authorization_ordinal
      OR NEW.accepted_requirement_revision_id <> OLD.accepted_requirement_revision_id
      OR NEW.runtime_attempt_no <> OLD.runtime_attempt_no
      OR NEW.runtime_attempt_fence_ref <> OLD.runtime_attempt_fence_ref
      OR NEW.request_hash <> OLD.request_hash
      OR NEW.profile_binding_generation <> OLD.profile_binding_generation
      OR NEW.browser_control_scope_id <> OLD.browser_control_scope_id
      OR NEW.source_ledger_revision <> OLD.source_ledger_revision
      OR NEW.source_reconciliation_revision <> OLD.source_reconciliation_revision
      OR NEW.dispatch_intent_id <> OLD.dispatch_intent_id
      OR NEW.dispatch_intent_digest <> OLD.dispatch_intent_digest
      OR NEW.source_operation_acceptance_ref <> OLD.source_operation_acceptance_ref
      OR COALESCE(NEW.reconciliation_id, '') <> COALESCE(OLD.reconciliation_id, '')
      OR COALESCE(NEW.reconciliation_digest, '') <> COALESCE(OLD.reconciliation_digest, '')
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
    """
    CREATE TRIGGER runtime_authenticated_observations_immutable
    BEFORE UPDATE ON runtime_control_authenticated_observations
    BEGIN
      SELECT RAISE(ABORT, 'runtime_authenticated_observation_immutable');
    END
    """,
    """
    CREATE TRIGGER runtime_authenticated_observations_delete_forbidden
    BEFORE DELETE ON runtime_control_authenticated_observations
    BEGIN
      SELECT RAISE(ABORT, 'runtime_authenticated_observation_delete_forbidden');
    END
    """,
    """
    CREATE TRIGGER runtime_action_checkpoints_update_forbidden
    BEFORE UPDATE ON runtime_control_checkpoints
    WHEN EXISTS (
      SELECT 1 FROM runtime_control_user_actions
      WHERE checkpoint_id = OLD.checkpoint_id
    )
    BEGIN
      SELECT RAISE(ABORT, 'runtime_action_checkpoint_immutable');
    END
    """,
    """
    CREATE TRIGGER runtime_action_checkpoints_delete_forbidden
    BEFORE DELETE ON runtime_control_checkpoints
    WHEN EXISTS (
      SELECT 1 FROM runtime_control_user_actions
      WHERE checkpoint_id = OLD.checkpoint_id
    )
    BEGIN
      SELECT RAISE(ABORT, 'runtime_action_checkpoint_delete_forbidden');
    END
    """,
)

_V15_OBJECTS = {
    "table": {
        "runtime_control_authenticated_observations",
        "runtime_control_user_actions",
    },
    "index": {
        "idx_runtime_user_actions_one_pending",
        "idx_runtime_user_actions_run_created",
    },
    "trigger": {
        "runtime_user_actions_immutable_binding",
        "runtime_user_actions_one_way_resolution",
        "runtime_user_actions_delete_forbidden",
        "runtime_authenticated_observations_immutable",
        "runtime_authenticated_observations_delete_forbidden",
        "runtime_action_checkpoints_update_forbidden",
        "runtime_action_checkpoints_delete_forbidden",
    },
}
ACTION_COLUMNS = {
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
    "entry_observation_ref": ("TEXT", 1, None, 0, 0),
    "entry_observation_digest": ("TEXT", 1, None, 0, 0),
    "accepted_requirement_revision_id": ("TEXT", 1, None, 0, 0),
    "runtime_attempt_no": ("INTEGER", 1, None, 0, 0),
    "runtime_attempt_fence_ref": ("TEXT", 1, None, 0, 0),
    "request_hash": ("TEXT", 1, None, 0, 0),
    "entry_request_semantic_digest": ("TEXT", 1, None, 0, 0),
    "profile_binding_generation": ("INTEGER", 1, None, 0, 0),
    "browser_control_scope_id": ("TEXT", 1, None, 0, 0),
    "source_ledger_revision": ("INTEGER", 1, None, 0, 0),
    "source_reconciliation_revision": ("INTEGER", 1, None, 0, 0),
    "entry_dispatch_authorization_ordinal": ("INTEGER", 1, None, 0, 0),
    "dispatch_intent_id": ("TEXT", 1, None, 0, 0),
    "dispatch_intent_digest": ("TEXT", 1, None, 0, 0),
    "source_operation_acceptance_ref": ("TEXT", 1, None, 0, 0),
    "reconciliation_id": ("TEXT", 0, None, 0, 0),
    "reconciliation_digest": ("TEXT", 0, None, 0, 0),
    "failure_id": ("TEXT", 1, None, 0, 0),
    "failure_revision": ("INTEGER", 1, None, 0, 0),
    "status": ("TEXT", 1, None, 0, 0),
    "resolution_evidence_ref": ("TEXT", 0, None, 0, 0),
    "resolution_binding_digest": ("TEXT", 0, None, 0, 0),
    "resolution_operation_id": ("TEXT", 0, None, 0, 0),
    "resolution_result_digest": ("TEXT", 0, None, 0, 0),
    "resolution_request_hash": ("TEXT", 0, None, 0, 0),
    "resolution_request_semantic_digest": ("TEXT", 0, None, 0, 0),
    "resolution_runtime_attempt_fence_ref": ("TEXT", 0, None, 0, 0),
    "resolution_dispatch_authorization_ordinal": ("INTEGER", 0, None, 0, 0),
    "resolution_reconciliation_id": ("TEXT", 0, None, 0, 0),
    "resolution_reconciliation_digest": ("TEXT", 0, None, 0, 0),
    "resolution_source_ledger_revision": ("INTEGER", 0, None, 0, 0),
    "resolution_source_reconciliation_revision": (
        "INTEGER",
        0,
        None,
        0,
        0,
    ),
    "resolution_at": ("TEXT", 0, None, 0, 0),
    "authority_mode": ("TEXT", 1, None, 0, 0),
    "owner_lease_id": ("TEXT", 0, None, 0, 0),
    "created_at": ("TEXT", 1, None, 0, 0),
}
_ACTION_COLUMN_ORDER = tuple(ACTION_COLUMNS)
OBSERVATION_COLUMNS = {
    "observation_ref": ("TEXT", 0, None, 1, 0),
    "result_digest": ("TEXT", 1, None, 0, 0),
    "session_id": ("TEXT", 1, None, 0, 0),
    "direction_seq": ("INTEGER", 1, None, 0, 0),
    "message_id": ("TEXT", 1, None, 0, 0),
    "reply_to": ("TEXT", 1, None, 0, 0),
    "runtime_run_id": ("TEXT", 1, None, 0, 0),
    "operation_id": ("TEXT", 1, None, 0, 0),
    "source_id": ("TEXT", 1, None, 0, 0),
    "operation_kind": ("TEXT", 1, None, 0, 0),
    "request_hash": ("TEXT", 1, None, 0, 0),
    "request_semantic_digest": ("TEXT", 1, None, 0, 0),
    "idempotency_key": ("TEXT", 1, None, 0, 0),
    "accepted_requirement_revision_id": ("TEXT", 1, None, 0, 0),
    "runtime_attempt_no": ("INTEGER", 1, None, 0, 0),
    "runtime_attempt_fence_ref": ("TEXT", 1, None, 0, 0),
    "expected_ledger_revision": ("INTEGER", 1, None, 0, 0),
    "expected_reconciliation_revision": ("INTEGER", 1, None, 0, 0),
    "profile_binding_generation": ("INTEGER", 1, None, 0, 0),
    "browser_control_scope_id": ("TEXT", 1, None, 0, 0),
    "actual_profile_binding_ref": ("TEXT", 1, None, 0, 0),
    "actual_profile_binding_generation": ("INTEGER", 1, None, 0, 0),
    "session_readiness": ("TEXT", 1, None, 0, 0),
    "action_digest": ("TEXT", 0, None, 0, 0),
    "dispatch_authorization_ordinal": ("INTEGER", 1, None, 0, 0),
    "dispatch_intent_id": ("TEXT", 1, None, 0, 0),
    "dispatch_intent_digest": ("TEXT", 1, None, 0, 0),
    "source_operation_acceptance_ref": ("TEXT", 1, None, 0, 0),
    "committed_at": ("TEXT", 1, None, 0, 0),
}
_EXPECTED_OBJECT_SQL = {
    name: _normalized_sql(NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[index])
    for name, index in {
        "runtime_control_authenticated_observations": 1,
        "runtime_control_user_actions": 2,
        "idx_runtime_user_actions_one_pending": 3,
        "idx_runtime_user_actions_run_created": 4,
        "runtime_user_actions_immutable_binding": 5,
        "runtime_user_actions_one_way_resolution": 6,
        "runtime_user_actions_delete_forbidden": 7,
        "runtime_authenticated_observations_immutable": 8,
        "runtime_authenticated_observations_delete_forbidden": 9,
        "runtime_action_checkpoints_update_forbidden": 10,
        "runtime_action_checkpoints_delete_forbidden": 11,
    }.items()
}
_CURRENT_ACTION_DEFINITION = _normalized_sql(
    NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS[0]
).split(" add column ", 1)[1]


def migrate_needs_attention_v14_to_v15(conn: sqlite3.Connection) -> None:
    try:
        incomplete = conn.execute(
            """
            SELECT 1 FROM runtime_control_runs
            WHERE status = 'needs_attention' OR product_outcome = 'needs_attention'
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
                SELECT type, name FROM sqlite_master
                WHERE name IN (
                    'runtime_control_authenticated_observations',
                    'runtime_control_user_actions'
                )
                   OR (
                     type IN ('index', 'trigger')
                     AND tbl_name IN (
                       'runtime_control_authenticated_observations',
                       'runtime_control_user_actions',
                       'runtime_control_checkpoints'
                     )
                     AND NOT (
                       name LIKE 'sqlite_autoindex_%' AND sql IS NULL
                     )
                   )
                   OR (
                     type = 'trigger' AND tbl_name = 'runtime_control_runs'
                   )
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
            conn.execute("PRAGMA table_xinfo(runtime_control_user_actions)")
        )
        action_columns = {
            row[1]: (row[2].upper(), row[3], row[4], row[5], row[6])
            for row in action_column_rows
        }
        observation_columns = {
            row[1]: (row[2].upper(), row[3], row[4], row[5], row[6])
            for row in conn.execute(
                "PRAGMA table_xinfo(runtime_control_authenticated_observations)"
            )
        }
        object_rows = list(
            conn.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE name IN (
                    'runtime_control_authenticated_observations',
                    'runtime_control_user_actions'
                )
                   OR (
                     type IN ('index', 'trigger')
                     AND tbl_name IN (
                       'runtime_control_authenticated_observations',
                       'runtime_control_user_actions',
                       'runtime_control_checkpoints'
                     )
                     AND NOT (
                       name LIKE 'sqlite_autoindex_%' AND sql IS NULL
                     )
                   )
                   OR (
                     type = 'trigger' AND tbl_name = 'runtime_control_runs'
                   )
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
        run_columns.get("current_action_id") != ("TEXT", 0, None, 0, 0)
        or run_definitions.get("current_action_id")
        != _CURRENT_ACTION_DEFINITION
        or action_columns != ACTION_COLUMNS
        or observation_columns != OBSERVATION_COLUMNS
        or tuple(row[1] for row in action_column_rows)
        != _ACTION_COLUMN_ORDER
        or objects != expected_objects
        or object_sql != _EXPECTED_OBJECT_SQL
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_schema_collision"
        )


__all__ = [
    "ACTION_COLUMNS",
    "NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS",
    "OBSERVATION_COLUMNS",
    "migrate_needs_attention_v14_to_v15",
    "validate_needs_attention_schema",
]
