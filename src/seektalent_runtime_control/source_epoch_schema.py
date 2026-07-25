from __future__ import annotations

import sqlite3

from seektalent.sqlite_migrations import SQLiteMigrationError
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.source_operations import (
    source_operation_from_row,
    validate_source_dispatch_ack,
    validate_source_operation_acceptance,
    validate_source_operation_admission_expectation,
)


def _source_dispatch_outbox_table_sql(table_name: str) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
      outbox_id TEXT PRIMARY KEY,
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      canonical_request_hash TEXT NOT NULL,
      dispatch_intent_id TEXT NOT NULL,
      dispatch_intent_revision INTEGER NOT NULL,
      dispatch_intent_digest TEXT NOT NULL,
      dispatch_authorization_ordinal INTEGER NOT NULL,
      safe_retry_commit_ref TEXT,
      source_operation_acceptance_ref TEXT NOT NULL,
      expected_ledger_revision INTEGER NOT NULL,
      expected_reconciliation_revision INTEGER NOT NULL,
      status TEXT NOT NULL,
      outbox_revision INTEGER NOT NULL,
      accepted_sidecar_generation INTEGER,
      accepted_sidecar_journal_revision INTEGER,
      ack_ref TEXT,
      ack_kind TEXT,
      acknowledged_at TEXT,
      UNIQUE(runtime_run_id, operation_id, dispatch_authorization_ordinal),
      UNIQUE(runtime_run_id, dispatch_intent_id),
      UNIQUE(runtime_run_id, operation_id, safe_retry_commit_ref),
      CHECK (dispatch_intent_revision > 0),
      CHECK (
        (
          dispatch_authorization_ordinal = 1
          AND safe_retry_commit_ref IS NULL
          AND expected_ledger_revision = 1
          AND expected_reconciliation_revision = 0
        )
        OR (
          dispatch_authorization_ordinal BETWEEN 2 AND 9007199254740991
          AND safe_retry_commit_ref IS NOT NULL
          AND length(CAST(safe_retry_commit_ref AS BLOB)) BETWEEN 1 AND 256
          AND safe_retry_commit_ref = trim(safe_retry_commit_ref)
          AND dispatch_intent_revision BETWEEN 1 AND 9007199254740991
          AND expected_ledger_revision BETWEEN 1 AND 9007199254740991
          AND expected_reconciliation_revision BETWEEN 1 AND 9007199254740991
        )
      ),
      CHECK (status IN ('pending', 'acknowledged')),
      CHECK (outbox_revision > 0),
      CHECK (accepted_sidecar_generation IS NULL OR accepted_sidecar_generation > 0),
      CHECK (accepted_sidecar_journal_revision IS NULL OR accepted_sidecar_journal_revision > 0),
      CHECK (ack_kind IS NULL OR ack_kind IN (
        'new_logical_operation', 'new_dispatch_authorization', 'same_intent_replay'
      )),
      CHECK (
        (status = 'pending' AND outbox_revision = 1
          AND accepted_sidecar_generation IS NULL
          AND accepted_sidecar_journal_revision IS NULL
          AND ack_ref IS NULL AND ack_kind IS NULL AND acknowledged_at IS NULL)
        OR
        (status = 'acknowledged' AND outbox_revision = 2
          AND accepted_sidecar_generation IS NOT NULL
          AND accepted_sidecar_journal_revision IS NOT NULL
          AND ack_ref IS NOT NULL AND ack_kind IS NOT NULL AND acknowledged_at IS NOT NULL)
      )
    )
    """


def _source_dispatch_outbox_v8_table_sql(table_name: str) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
      outbox_id TEXT PRIMARY KEY,
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      canonical_request_hash TEXT NOT NULL,
      dispatch_intent_id TEXT NOT NULL,
      dispatch_intent_revision INTEGER NOT NULL,
      dispatch_intent_digest TEXT NOT NULL,
      dispatch_authorization_ordinal INTEGER NOT NULL,
      source_operation_acceptance_ref TEXT NOT NULL,
      expected_ledger_revision INTEGER NOT NULL,
      expected_reconciliation_revision INTEGER NOT NULL,
      status TEXT NOT NULL,
      outbox_revision INTEGER NOT NULL,
      accepted_sidecar_generation INTEGER,
      accepted_sidecar_journal_revision INTEGER,
      ack_ref TEXT,
      ack_kind TEXT,
      acknowledged_at TEXT,
      UNIQUE(runtime_run_id, operation_id, dispatch_authorization_ordinal),
      UNIQUE(runtime_run_id, dispatch_intent_id),
      CHECK (dispatch_intent_revision > 0),
      CHECK (dispatch_authorization_ordinal = 1),
      CHECK (expected_ledger_revision = 1),
      CHECK (expected_reconciliation_revision = 0),
      CHECK (status IN ('pending', 'acknowledged')),
      CHECK (outbox_revision > 0),
      CHECK (accepted_sidecar_generation IS NULL OR accepted_sidecar_generation > 0),
      CHECK (accepted_sidecar_journal_revision IS NULL OR accepted_sidecar_journal_revision > 0),
      CHECK (ack_kind IS NULL OR ack_kind IN (
        'new_logical_operation', 'new_dispatch_authorization', 'same_intent_replay'
      )),
      CHECK (
        (status = 'pending' AND outbox_revision = 1
          AND accepted_sidecar_generation IS NULL
          AND accepted_sidecar_journal_revision IS NULL
          AND ack_ref IS NULL AND ack_kind IS NULL AND acknowledged_at IS NULL)
        OR
        (status = 'acknowledged' AND outbox_revision = 2
          AND accepted_sidecar_generation IS NOT NULL
          AND accepted_sidecar_journal_revision IS NOT NULL
          AND ack_ref IS NOT NULL AND ack_kind IS NOT NULL AND acknowledged_at IS NOT NULL)
      )
    )
    """


SOURCE_OPERATION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runtime_control_source_operations (
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      source_id TEXT NOT NULL,
      operation_kind TEXT NOT NULL,
      canonical_request_hash TEXT NOT NULL,
      idempotency_key TEXT NOT NULL,
      accepted_requirement_revision_id TEXT NOT NULL,
      runtime_attempt_no INTEGER NOT NULL,
      runtime_attempt_authority_ref TEXT NOT NULL,
      operation_phase TEXT NOT NULL,
      dispatch_intent_ref TEXT,
      conclusive_observation_ref TEXT,
      source_operation_disposition TEXT,
      retry_posture TEXT NOT NULL,
      reconciliation_revision INTEGER NOT NULL,
      main_commit_ref TEXT,
      ledger_revision INTEGER NOT NULL,
      PRIMARY KEY(runtime_run_id, operation_id),
      UNIQUE(runtime_run_id, idempotency_key),
      CHECK (source_id = 'liepin'),
      CHECK (operation_kind IN ('verify_session', 'search', 'cards', 'details', 'continuation', 'cleanup')),
      CHECK (operation_phase IN ('accepted', 'dispatch_intent', 'observed', 'reconciled', 'main_committed')),
      CHECK (source_operation_disposition IS NULL OR source_operation_disposition IN (
        'completed', 'partial', 'user_action_required', 'incompatible', 'failed',
        'cancelled', 'reconciliation_unknown'
      )),
      CHECK (retry_posture IN ('no_retry', 'safe_retry', 'reconcile_first')),
      CHECK (runtime_attempt_no > 0),
      CHECK (reconciliation_revision >= 0),
      CHECK (ledger_revision > 0)
    )
    """,
    _source_dispatch_outbox_table_sql("runtime_control_source_dispatch_outbox"),
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_source_dispatch_pending
      ON runtime_control_source_dispatch_outbox(status, outbox_id)
    """,
)

SOURCE_OPERATION_V8_SCHEMA_STATEMENTS = (
    SOURCE_OPERATION_SCHEMA_STATEMENTS[0],
    _source_dispatch_outbox_v8_table_sql("runtime_control_source_dispatch_outbox"),
    SOURCE_OPERATION_SCHEMA_STATEMENTS[2],
)


def create_source_operation_schema(conn: sqlite3.Connection) -> None:
    for statement in SOURCE_OPERATION_SCHEMA_STATEMENTS:
        conn.execute(statement)


def _source_operation_expectation_table_sql(table_name: str) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      dispatch_authorization_ordinal INTEGER NOT NULL,
      runtime_attempt_no INTEGER NOT NULL,
      runtime_attempt_authority_ref TEXT NOT NULL,
      runtime_attempt_fence_ref TEXT NOT NULL,
      profile_binding_generation INTEGER NOT NULL,
      browser_control_scope_id TEXT,
      controller_fence_ref TEXT,
      PRIMARY KEY(runtime_run_id, operation_id, dispatch_authorization_ordinal),
      FOREIGN KEY(runtime_run_id, operation_id)
        REFERENCES runtime_control_source_operations(runtime_run_id, operation_id),
      CHECK (dispatch_authorization_ordinal BETWEEN 1 AND 9007199254740991),
      CHECK (
        runtime_attempt_no > 0
        AND (
          dispatch_authorization_ordinal = 1
          OR runtime_attempt_no <= 9007199254740991
        )
      ),
      CHECK (
        length(CAST(runtime_attempt_authority_ref AS BLOB)) BETWEEN 1 AND 256
        AND runtime_attempt_authority_ref = trim(runtime_attempt_authority_ref)
      ),
      CHECK (
        length(runtime_attempt_fence_ref) = 64
        AND runtime_attempt_fence_ref NOT GLOB '*[^0-9a-f]*'
      ),
      CHECK (
        typeof(profile_binding_generation) = 'integer'
        AND profile_binding_generation BETWEEN 1 AND 9007199254740991
      ),
      CHECK (
        browser_control_scope_id IS NULL
        OR (
          length(CAST(browser_control_scope_id AS BLOB)) BETWEEN 1 AND 96
          AND browser_control_scope_id = trim(browser_control_scope_id)
        )
      ),
      CHECK (
        controller_fence_ref IS NULL
        OR (
          length(controller_fence_ref) = 64
          AND controller_fence_ref NOT GLOB '*[^0-9a-f]*'
        )
      )
    )
    """


def _source_operation_expectation_trigger_statements(
    table_name: str,
) -> tuple[str, str, str]:
    return (
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_runtime_source_admission_expectation_no_update
        BEFORE UPDATE ON {table_name}
        BEGIN
          SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_runtime_source_admission_expectation_no_delete
        BEFORE DELETE ON {table_name}
        BEGIN
          SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_runtime_source_admission_expectation_no_replace
        BEFORE INSERT ON {table_name}
        WHEN EXISTS (
          SELECT 1
          FROM {table_name}
          WHERE runtime_run_id = NEW.runtime_run_id
            AND operation_id = NEW.operation_id
            AND dispatch_authorization_ordinal = NEW.dispatch_authorization_ordinal
        )
        BEGIN
          SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
        END
        """,
    )


SOURCE_OPERATION_ADMISSION_EXPECTATION_SCHEMA_STATEMENTS = (
    _source_operation_expectation_table_sql("runtime_control_source_operation_admission_expectations"),
    *_source_operation_expectation_trigger_statements("runtime_control_source_operation_admission_expectations"),
)


def _source_operation_expectation_v10_table_sql(
    table_name: str,
) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      runtime_attempt_fence_ref TEXT NOT NULL,
      profile_binding_generation INTEGER NOT NULL,
      browser_control_scope_id TEXT,
      controller_fence_ref TEXT,
      PRIMARY KEY(runtime_run_id, operation_id),
      FOREIGN KEY(runtime_run_id, operation_id)
        REFERENCES runtime_control_source_operations(runtime_run_id, operation_id),
      CHECK (
        length(runtime_attempt_fence_ref) = 64
        AND runtime_attempt_fence_ref NOT GLOB '*[^0-9a-f]*'
      ),
      CHECK (
        typeof(profile_binding_generation) = 'integer'
        AND profile_binding_generation BETWEEN 1 AND 9007199254740991
      ),
      CHECK (
        browser_control_scope_id IS NULL
        OR (
          length(CAST(browser_control_scope_id AS BLOB)) BETWEEN 1 AND 96
          AND browser_control_scope_id = trim(browser_control_scope_id)
        )
      ),
      CHECK (
        controller_fence_ref IS NULL
        OR (
          length(controller_fence_ref) = 64
          AND controller_fence_ref NOT GLOB '*[^0-9a-f]*'
        )
      )
    )
    """


def _source_operation_expectation_v10_trigger_statements(
    table_name: str,
) -> tuple[str, str, str]:
    return (
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_runtime_source_admission_expectation_no_update
        BEFORE UPDATE ON {table_name}
        BEGIN
          SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_runtime_source_admission_expectation_no_delete
        BEFORE DELETE ON {table_name}
        BEGIN
          SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
        END
        """,
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_runtime_source_admission_expectation_no_replace
        BEFORE INSERT ON {table_name}
        WHEN EXISTS (
          SELECT 1
          FROM {table_name}
          WHERE runtime_run_id = NEW.runtime_run_id
            AND operation_id = NEW.operation_id
        )
        BEGIN
          SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
        END
        """,
    )


SOURCE_OPERATION_ADMISSION_EXPECTATION_V10_SCHEMA_STATEMENTS = (
    _source_operation_expectation_v10_table_sql("runtime_control_source_operation_admission_expectations"),
    *_source_operation_expectation_v10_trigger_statements("runtime_control_source_operation_admission_expectations"),
)


def create_source_operation_admission_expectation_schema(
    conn: sqlite3.Connection,
) -> None:
    for statement in SOURCE_OPERATION_ADMISSION_EXPECTATION_SCHEMA_STATEMENTS:
        conn.execute(statement)


_SOURCE_DISPATCH_OUTBOX_V11_COLUMNS = (
    "outbox_id",
    "runtime_run_id",
    "operation_id",
    "canonical_request_hash",
    "dispatch_intent_id",
    "dispatch_intent_revision",
    "dispatch_intent_digest",
    "dispatch_authorization_ordinal",
    "source_operation_acceptance_ref",
    "expected_ledger_revision",
    "expected_reconciliation_revision",
    "status",
    "outbox_revision",
    "accepted_sidecar_generation",
    "accepted_sidecar_journal_revision",
    "ack_ref",
    "ack_kind",
    "acknowledged_at",
)
_SOURCE_OPERATION_EXPECTATION_V11_COLUMNS = (
    "runtime_run_id",
    "operation_id",
    "runtime_attempt_fence_ref",
    "profile_binding_generation",
    "browser_control_scope_id",
    "controller_fence_ref",
)
_OUTBOX_MIGRATION_TABLE = "runtime_control_source_dispatch_outbox_v12"
_EXPECTATION_MIGRATION_TABLE = "runtime_control_source_operation_admission_expectations_v12"


def migrate_source_epochs_v11_to_v12(
    conn: sqlite3.Connection,
) -> None:
    _validate_v11_state(conn)
    _inject_source_epoch_migration_fault("after_validation")

    conn.execute(_source_dispatch_outbox_table_sql(_OUTBOX_MIGRATION_TABLE))
    _inject_source_epoch_migration_fault("after_outbox_create")
    legacy_outbox_columns = ", ".join(_SOURCE_DISPATCH_OUTBOX_V11_COLUMNS)
    conn.execute(
        f"""
        INSERT INTO {_OUTBOX_MIGRATION_TABLE} (
            outbox_id, runtime_run_id, operation_id, canonical_request_hash,
            dispatch_intent_id, dispatch_intent_revision,
            dispatch_intent_digest, dispatch_authorization_ordinal,
            safe_retry_commit_ref, source_operation_acceptance_ref,
            expected_ledger_revision, expected_reconciliation_revision,
            status, outbox_revision, accepted_sidecar_generation,
            accepted_sidecar_journal_revision, ack_ref, ack_kind,
            acknowledged_at
        )
        SELECT outbox_id, runtime_run_id, operation_id,
               canonical_request_hash, dispatch_intent_id,
               dispatch_intent_revision, dispatch_intent_digest,
               dispatch_authorization_ordinal, NULL,
               source_operation_acceptance_ref,
               expected_ledger_revision,
               expected_reconciliation_revision, status,
               outbox_revision, accepted_sidecar_generation,
               accepted_sidecar_journal_revision, ack_ref, ack_kind,
               acknowledged_at
        FROM runtime_control_source_dispatch_outbox
        """
    )
    if _tables_differ(
        conn,
        old_table="runtime_control_source_dispatch_outbox",
        new_table=_OUTBOX_MIGRATION_TABLE,
        columns_sql=legacy_outbox_columns,
    ):
        raise _migration_error()
    _inject_source_epoch_migration_fault("after_outbox_copy")

    conn.execute(_source_operation_expectation_table_sql(_EXPECTATION_MIGRATION_TABLE))
    _inject_source_epoch_migration_fault("after_expectation_create")
    conn.execute(
        f"""
        INSERT INTO {_EXPECTATION_MIGRATION_TABLE} (
            runtime_run_id, operation_id,
            dispatch_authorization_ordinal, runtime_attempt_no,
            runtime_attempt_authority_ref, runtime_attempt_fence_ref,
            profile_binding_generation, browser_control_scope_id,
            controller_fence_ref
        )
        SELECT expectation.runtime_run_id, expectation.operation_id, 1,
               operation.runtime_attempt_no,
               operation.runtime_attempt_authority_ref,
               expectation.runtime_attempt_fence_ref,
               expectation.profile_binding_generation,
               expectation.browser_control_scope_id,
               expectation.controller_fence_ref
        FROM runtime_control_source_operation_admission_expectations
             AS expectation
        JOIN runtime_control_source_operations AS operation
          ON operation.runtime_run_id = expectation.runtime_run_id
         AND operation.operation_id = expectation.operation_id
        """
    )
    legacy_expectation_columns = ", ".join(_SOURCE_OPERATION_EXPECTATION_V11_COLUMNS)
    if _tables_differ(
        conn,
        old_table=("runtime_control_source_operation_admission_expectations"),
        new_table=_EXPECTATION_MIGRATION_TABLE,
        columns_sql=legacy_expectation_columns,
    ):
        raise _migration_error()
    _inject_source_epoch_migration_fault("after_expectation_copy")

    for trigger_name in (
        "trg_runtime_source_admission_expectation_no_update",
        "trg_runtime_source_admission_expectation_no_delete",
        "trg_runtime_source_admission_expectation_no_replace",
    ):
        conn.execute(f"DROP TRIGGER {trigger_name}")
    _inject_source_epoch_migration_fault("after_trigger_drop")
    conn.execute(
        """
        DROP TABLE
          runtime_control_source_operation_admission_expectations
        """
    )
    conn.execute("DROP TABLE runtime_control_source_dispatch_outbox")
    _inject_source_epoch_migration_fault("after_legacy_drop")
    conn.execute(
        f"""
        ALTER TABLE {_OUTBOX_MIGRATION_TABLE}
        RENAME TO runtime_control_source_dispatch_outbox
        """
    )
    conn.execute(
        f"""
        ALTER TABLE {_EXPECTATION_MIGRATION_TABLE}
        RENAME TO runtime_control_source_operation_admission_expectations
        """
    )
    _inject_source_epoch_migration_fault("after_table_rename")
    conn.execute(
        """
        CREATE INDEX idx_runtime_source_dispatch_pending
        ON runtime_control_source_dispatch_outbox(status, outbox_id)
        """
    )
    for statement in _source_operation_expectation_trigger_statements(
        "runtime_control_source_operation_admission_expectations"
    ):
        conn.execute(statement)
    _inject_source_epoch_migration_fault("after_schema_restore")


def _validate_v11_state(conn: sqlite3.Connection) -> None:
    try:
        for migration_table in (
            _OUTBOX_MIGRATION_TABLE,
            _EXPECTATION_MIGRATION_TABLE,
        ):
            if _table_exists(conn, migration_table):
                raise ValueError
        if (
            _ordered_column_names(
                conn,
                "runtime_control_source_dispatch_outbox",
            )
            != _SOURCE_DISPATCH_OUTBOX_V11_COLUMNS
        ):
            raise ValueError
        if (
            _ordered_column_names(
                conn,
                ("runtime_control_source_operation_admission_expectations"),
            )
            != _SOURCE_OPERATION_EXPECTATION_V11_COLUMNS
        ):
            raise ValueError
        outbox_schema = _normalized_table_schema(
            conn,
            "runtime_control_source_dispatch_outbox",
        )
        expectation_schema = _normalized_table_schema(
            conn,
            "runtime_control_source_operation_admission_expectations",
        )
        if (
            "CHECK (dispatch_authorization_ordinal = 1)" not in outbox_schema
            or "UNIQUE(runtime_run_id, dispatch_intent_id)" not in outbox_schema
            or "PRIMARY KEY(runtime_run_id, operation_id)" not in expectation_schema
        ):
            raise ValueError
        required_schema_objects = {
            ("index", "idx_runtime_source_dispatch_pending"),
            (
                "trigger",
                "trg_runtime_source_admission_expectation_no_update",
            ),
            (
                "trigger",
                "trg_runtime_source_admission_expectation_no_delete",
            ),
            (
                "trigger",
                "trg_runtime_source_admission_expectation_no_replace",
            ),
        }
        stored_schema_objects = {
            (str(row["type"]), str(row["name"]))
            for row in conn.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE name IN (
                  'idx_runtime_source_dispatch_pending',
                  'trg_runtime_source_admission_expectation_no_update',
                  'trg_runtime_source_admission_expectation_no_delete',
                  'trg_runtime_source_admission_expectation_no_replace'
                )
                """
            )
        }
        if stored_schema_objects != required_schema_objects:
            raise ValueError
        duplicate_alias = conn.execute(
            """
            SELECT 1
            FROM runtime_control_source_dispatch_outbox
            GROUP BY runtime_run_id, dispatch_intent_id
            HAVING COUNT(*) != 1
            LIMIT 1
            """
        ).fetchone()
        if duplicate_alias is not None:
            raise ValueError

        operation_rows = {
            (row["runtime_run_id"], row["operation_id"]): row
            for row in conn.execute("SELECT * FROM runtime_control_source_operations")
        }
        expectation_rows = {
            (row["runtime_run_id"], row["operation_id"]): row
            for row in conn.execute(
                """
                SELECT *
                FROM runtime_control_source_operation_admission_expectations
                """
            )
        }
        for identity, row in expectation_rows.items():
            operation_row = operation_rows.get(identity)
            if operation_row is None:
                raise ValueError
            operation = source_operation_from_row(operation_row)
            validate_source_operation_admission_expectation(
                runtime_run_id=operation.runtime_run_id,
                operation_id=operation.operation_id,
                dispatch_authorization_ordinal=1,
                runtime_attempt_no=operation.runtime_attempt_no,
                runtime_attempt_authority_ref=(operation.runtime_attempt_authority_ref),
                runtime_attempt_fence_ref=(row["runtime_attempt_fence_ref"]),
                profile_binding_generation=(row["profile_binding_generation"]),
                browser_control_scope_id=(row["browser_control_scope_id"]),
                controller_fence_ref=row["controller_fence_ref"],
            )
        for row in conn.execute("SELECT * FROM runtime_control_source_dispatch_outbox"):
            identity = (row["runtime_run_id"], row["operation_id"])
            operation_row = operation_rows.get(identity)
            if operation_row is None:
                raise ValueError
            operation = source_operation_from_row(operation_row)
            expectation_row = expectation_rows.get(identity)
            validate_source_operation_acceptance(
                runtime_run_id=operation.runtime_run_id,
                operation_id=operation.operation_id,
                source_id=operation.source_id,
                operation_kind=operation.operation_kind,
                canonical_request_hash=(operation.canonical_request_hash),
                idempotency_key=operation.idempotency_key,
                accepted_requirement_revision_id=(operation.accepted_requirement_revision_id),
                runtime_attempt_no=operation.runtime_attempt_no,
                runtime_attempt_authority_ref=(operation.runtime_attempt_authority_ref),
                runtime_attempt_fence_ref=(
                    expectation_row["runtime_attempt_fence_ref"] if expectation_row is not None else "0" * 64
                ),
                profile_binding_generation=(
                    expectation_row["profile_binding_generation"] if expectation_row is not None else 1
                ),
                browser_control_scope_id=(
                    expectation_row["browser_control_scope_id"] if expectation_row is not None else None
                ),
                controller_fence_ref=(expectation_row["controller_fence_ref"] if expectation_row is not None else None),
                outbox_id=row["outbox_id"],
                dispatch_intent_id=row["dispatch_intent_id"],
                dispatch_intent_revision=(row["dispatch_intent_revision"]),
                dispatch_intent_digest=row["dispatch_intent_digest"],
                dispatch_authorization_ordinal=(row["dispatch_authorization_ordinal"]),
                source_operation_acceptance_ref=(row["source_operation_acceptance_ref"]),
                expected_ledger_revision=(row["expected_ledger_revision"]),
                expected_reconciliation_revision=(row["expected_reconciliation_revision"]),
            )
            _validate_outbox_status(row)
    except (KeyError, RuntimeControlError, TypeError, ValueError):
        raise _migration_error() from None


def _validate_outbox_status(row: sqlite3.Row) -> None:
    if row["status"] == "pending":
        if row["outbox_revision"] != 1 or any(
            row[column] is not None
            for column in (
                "accepted_sidecar_generation",
                "accepted_sidecar_journal_revision",
                "ack_ref",
                "ack_kind",
                "acknowledged_at",
            )
        ):
            raise ValueError
        return
    if row["status"] != "acknowledged":
        raise ValueError
    validate_source_dispatch_ack(
        runtime_run_id=row["runtime_run_id"],
        operation_id=row["operation_id"],
        outbox_id=row["outbox_id"],
        canonical_request_hash=row["canonical_request_hash"],
        dispatch_intent_id=row["dispatch_intent_id"],
        dispatch_intent_revision=row["dispatch_intent_revision"],
        dispatch_intent_digest=row["dispatch_intent_digest"],
        dispatch_authorization_ordinal=(row["dispatch_authorization_ordinal"]),
        expected_outbox_revision=1,
        accepted_sidecar_generation=(row["accepted_sidecar_generation"]),
        accepted_sidecar_journal_revision=(row["accepted_sidecar_journal_revision"]),
        ack_ref=row["ack_ref"],
        ack_kind=row["ack_kind"],
        acknowledged_at=row["acknowledged_at"],
    )
    if row["outbox_revision"] != 2:
        raise ValueError


def _tables_differ(
    conn: sqlite3.Connection,
    *,
    old_table: str,
    new_table: str,
    columns_sql: str,
) -> bool:
    return any(
        conn.execute(query).fetchone() is not None
        for query in (
            f"""
            SELECT {columns_sql} FROM {old_table}
            EXCEPT
            SELECT {columns_sql} FROM {new_table}
            """,
            f"""
            SELECT {columns_sql} FROM {new_table}
            EXCEPT
            SELECT {columns_sql} FROM {old_table}
            """,
        )
    )


def _normalized_table_schema(
    conn: sqlite3.Connection,
    table_name: str,
) -> str:
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if row is None or type(row["sql"]) is not str:
        raise ValueError
    return " ".join(row["sql"].split())


def _ordered_column_names(
    conn: sqlite3.Connection,
    table_name: str,
) -> tuple[str, ...]:
    return tuple(str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})"))


def _table_exists(
    conn: sqlite3.Connection,
    table_name: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        is not None
    )


def _migration_error() -> SQLiteMigrationError:
    return SQLiteMigrationError(
        "runtime_control_source_dispatch_epoch_migration_invalid",
        "runtime-control source dispatch epoch v11 state is invalid",
    )


def _inject_source_epoch_migration_fault(point: str) -> None:
    del point
