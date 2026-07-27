from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from seektalent.diagnostics_schema import (
    FailureEnvelopeV1,
    canonical_diagnostics_bytes,
    canonical_diagnostics_hash,
    parse_failure_envelope,
)
from tests.test_diagnostics_schema import _failure


def _envelope(*, revision: int = 1, failure_id: str | None = None) -> FailureEnvelopeV1:
    payload = _failure()
    payload["revision"] = revision
    if failure_id is not None:
        payload["failure_id"] = failure_id
    return parse_failure_envelope(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )


def _initialized_path(tmp_path: Path) -> Path:
    from seektalent_runtime_control.store import RuntimeControlStore

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    return path


def _downgrade_v14_run_columns_to_v13(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "ALTER TABLE runtime_control_runs "
            "DROP COLUMN current_failure_authority_mode"
        )
        conn.execute(
            "ALTER TABLE runtime_control_runs "
            "DROP COLUMN current_failure_owner_lease_id"
        )
        conn.execute("ALTER TABLE runtime_control_runs DROP COLUMN product_outcome")
        conn.execute("ALTER TABLE runtime_control_runs DROP COLUMN current_failure_id")
        conn.execute(
            "ALTER TABLE runtime_control_runs DROP COLUMN current_failure_revision"
        )
        conn.execute("ALTER TABLE runtime_control_runs DROP COLUMN state_revision")
        conn.execute("PRAGMA user_version = 13")


def test_fresh_runtime_control_schema_v14_owns_failure_envelope_table(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import (
        RUNTIME_CONTROL_SCHEMA_VERSION,
        RuntimeControlStore,
    )

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()

    with sqlite3.connect(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(runtime_control_failure_envelope_revisions)"
            )
        }
        indexes = {
            row[1]
            for row in conn.execute(
                "PRAGMA index_list(runtime_control_failure_envelope_revisions)"
            )
        }

    assert version == RUNTIME_CONTROL_SCHEMA_VERSION == 14
    assert {
        "failure_id",
        "revision",
        "canonical_bytes",
        "canonical_sha256",
        "run_id",
        "operation_id",
        "attempt_no",
        "correlation_id",
        "component",
        "domain",
        "failure_kind",
        "reason_code",
        "current_outcome",
        "occurred_at",
        "observed_at",
    } <= columns
    assert "idx_runtime_failure_envelopes_run" in indexes


def test_real_v13_to_v14_migration_matches_fresh_schema_and_reopens(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    migrated_path = _initialized_path(tmp_path / "migrated")
    _downgrade_v14_run_columns_to_v13(migrated_path)
    RuntimeControlStore(migrated_path).initialize()
    RuntimeControlStore(migrated_path).initialize()

    fresh_path = _initialized_path(tmp_path / "fresh")
    with sqlite3.connect(migrated_path) as migrated, sqlite3.connect(
        fresh_path
    ) as fresh:
        migrated_columns = migrated.execute(
            "PRAGMA table_info(runtime_control_runs)"
        ).fetchall()
        fresh_columns = fresh.execute(
            "PRAGMA table_info(runtime_control_runs)"
        ).fetchall()
        migrated_version = migrated.execute("PRAGMA user_version").fetchone()[0]
        fresh_version = fresh.execute("PRAGMA user_version").fetchone()[0]

    assert migrated_columns == fresh_columns
    assert migrated_version == fresh_version == 14


def test_v13_failure_envelope_migration_orders_interleaved_lineages(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import (
        FAILURE_ENVELOPE_TABLE,
        store_failure_envelope_revision,
    )
    from seektalent_runtime_control.store import RuntimeControlStore

    migrated_path = _initialized_path(tmp_path / "migrated")
    lineage_a = "a" * 32
    lineage_b = "b" * 32
    envelopes = (
        _envelope(failure_id=lineage_a),
        _envelope(failure_id=lineage_b),
        _envelope(failure_id=lineage_b, revision=2),
        _envelope(failure_id=lineage_a, revision=2),
    )
    with sqlite3.connect(migrated_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for envelope in envelopes:
            store_failure_envelope_revision(conn, envelope)
        conn.commit()
        before = conn.execute(
            f"""
            SELECT *
            FROM {FAILURE_ENVELOPE_TABLE}
            ORDER BY failure_id, revision
            """
        ).fetchall()

    _downgrade_v14_run_columns_to_v13(migrated_path)
    RuntimeControlStore(migrated_path).initialize()

    fresh_path = _initialized_path(tmp_path / "fresh")
    with sqlite3.connect(migrated_path) as migrated, sqlite3.connect(
        fresh_path
    ) as fresh:
        after = migrated.execute(
            f"""
            SELECT *
            FROM {FAILURE_ENVELOPE_TABLE}
            ORDER BY failure_id, revision
            """
        ).fetchall()
        migrated_schema = migrated.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE tbl_name = ?
            ORDER BY type, name
            """,
            (FAILURE_ENVELOPE_TABLE,),
        ).fetchall()
        fresh_schema = fresh.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE tbl_name = ?
            ORDER BY type, name
            """,
            (FAILURE_ENVELOPE_TABLE,),
        ).fetchall()

    assert after == before
    assert [(row[0], row[1]) for row in after] == [
        (lineage_a, 1),
        (lineage_a, 2),
        (lineage_b, 1),
        (lineage_b, 2),
    ]
    assert migrated_schema == fresh_schema


@pytest.mark.parametrize("poisoning", ("extra", "missing", "reordered"))
def test_v13_failure_envelope_column_shape_fails_closed_and_retries(
    tmp_path: Path,
    poisoning: str,
) -> None:
    import seektalent.diagnostics_storage as storage_module
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        create_failure_envelope_schema,
    )
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    table_sql = storage_module._SCHEMA_STATEMENTS[0]
    if poisoning == "extra":
        table_sql = table_sql.replace(
            "      observed_at TEXT NOT NULL,\n",
            "      observed_at TEXT NOT NULL,\n      poisoned TEXT,\n",
        )
    elif poisoning == "missing":
        table_sql = table_sql.replace("      correlation_id TEXT,\n", "")
    else:
        table_sql = table_sql.replace(
            "      operation_id TEXT,\n      attempt_no INTEGER,\n",
            "      attempt_no INTEGER,\n      operation_id TEXT,\n",
        )
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"DROP TABLE {storage_module.FAILURE_ENVELOPE_TABLE}"
        )
        conn.execute(table_sql)
        conn.execute("PRAGMA user_version = 13")

    with pytest.raises(FailureEnvelopeStorageError) as exc_info:
        RuntimeControlStore(path).initialize()
    assert exc_info.value.reason == "failure_envelope_schema_failed"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        conn.execute(
            f"DROP TABLE {storage_module.FAILURE_ENVELOPE_TABLE}"
        )
        create_failure_envelope_schema(conn)

    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v14_outcome_authority_shape_constraint_matrix(
    tmp_path: Path,
) -> None:
    path = _initialized_path(tmp_path)

    def insert_shape(
        conn: sqlite3.Connection,
        *,
        suffix: int,
        status: str,
        outcome: str,
        failure_id: str | None,
        failure_revision: int | None,
        authority_mode: str | None,
        owner_lease_id: str | None,
    ) -> None:
        runtime_run_id = f"{suffix:032x}"
        conn.execute(
            """
            INSERT INTO runtime_control_runs (
                runtime_run_id, run_intent_id, start_idempotency_key,
                approved_requirement_revision_id, status, current_stage,
                source_ids_json, created_at, updated_at,
                product_outcome, current_failure_id,
                current_failure_revision, current_failure_authority_mode,
                current_failure_owner_lease_id
            )
            VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                runtime_run_id,
                f"intent_{runtime_run_id}",
                f"start_{runtime_run_id}",
                "reqapproved_test",
                status,
                status,
                "2026-07-27T00:00:00Z",
                "2026-07-27T00:00:00Z",
                outcome,
                failure_id,
                failure_revision,
                authority_mode,
                owner_lease_id,
            ),
        )

    valid = (
        ("failed", "failed", "no_owner", None),
        ("failed", "failed", "active_owner", "rtlease_failed"),
        ("needs_attention", "needs_attention", "no_owner", None),
        (
            "needs_attention",
            "needs_attention",
            "active_owner",
            "rtlease_attention",
        ),
    )
    invalid = (
        ("failed", "needs_attention", "no_owner", None, "1" * 32, 1),
        ("needs_attention", "failed", "no_owner", None, "2" * 32, 1),
        ("needs_attention", "needs_attention", None, None, None, None),
        ("failed", "failed", "active_owner", None, "3" * 32, 1),
        ("cancelled", "cancelled", None, None, "4" * 32, 1),
    )
    with sqlite3.connect(path) as conn:
        for suffix, (status, outcome, mode, owner) in enumerate(
            valid,
            start=1,
        ):
            insert_shape(
                conn,
                suffix=suffix,
                status=status,
                outcome=outcome,
                failure_id=f"{suffix + 10:032x}",
                failure_revision=1,
                authority_mode=mode,
                owner_lease_id=owner,
            )
        for suffix, (
            status,
            outcome,
            mode,
            owner,
            failure_id,
            revision,
        ) in enumerate(invalid, start=100):
            with pytest.raises(sqlite3.IntegrityError):
                insert_shape(
                    conn,
                    suffix=suffix,
                    status=status,
                    outcome=outcome,
                    failure_id=failure_id,
                    failure_revision=revision,
                    authority_mode=mode,
                    owner_lease_id=owner,
                )


def test_v13_partial_outcome_schema_fails_closed_and_is_retryable(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    _downgrade_v14_run_columns_to_v13(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            ALTER TABLE runtime_control_runs
            ADD COLUMN state_revision INTEGER NOT NULL DEFAULT 0
            """
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeControlStore(path).initialize()
    assert (
        exc_info.value.reason_code
        == "runtime_control_failed_outcome_schema_collision"
    )
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(runtime_control_runs)")
        }
        assert "state_revision" in columns
        assert "product_outcome" not in columns
        conn.execute("ALTER TABLE runtime_control_runs DROP COLUMN state_revision")

    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("completed_statements", (0, 1, 2, 3, 4, 5))
def test_fresh_v14_outcome_ddl_failure_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_statements: int,
) -> None:
    from seektalent_runtime_control import failed_outcome as outcome_module
    from seektalent_runtime_control.store import RuntimeControlStore

    path = tmp_path / "runtime_control.sqlite3"
    statements = outcome_module.FAILED_OUTCOME_V14_SCHEMA_STATEMENTS
    monkeypatch.setattr(
        outcome_module,
        "FAILED_OUTCOME_V14_SCHEMA_STATEMENTS",
        (
            *statements[:completed_statements],
            "ALTER TABL runtime_control_runs injected_invalid_statement",
        ),
    )
    with pytest.raises(sqlite3.OperationalError):
        RuntimeControlStore(path).initialize()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(runtime_control_runs)")
        }
        assert not {
            "state_revision",
            "current_failure_revision",
            "current_failure_id",
            "product_outcome",
            "current_failure_owner_lease_id",
            "current_failure_authority_mode",
        } & columns
        assert conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE name LIKE '%failure_envelope%'
              AND name NOT LIKE 'sqlite_autoindex%'
            """
        ).fetchone() is None

    monkeypatch.setattr(
        outcome_module,
        "FAILED_OUTCOME_V14_SCHEMA_STATEMENTS",
        statements,
    )
    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("completed_statements", (0, 1, 2, 3, 4, 5))
def test_v13_to_v14_outcome_ddl_failure_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_statements: int,
) -> None:
    from seektalent_runtime_control import failed_outcome as outcome_module
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    _downgrade_v14_run_columns_to_v13(path)
    statements = outcome_module.FAILED_OUTCOME_V14_SCHEMA_STATEMENTS
    monkeypatch.setattr(
        outcome_module,
        "FAILED_OUTCOME_V14_SCHEMA_STATEMENTS",
        (
            *statements[:completed_statements],
            "ALTER TABL runtime_control_runs injected_invalid_statement",
        ),
    )
    with pytest.raises(sqlite3.OperationalError):
        RuntimeControlStore(path).initialize()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(runtime_control_runs)")
        }
        assert not {
            "state_revision",
            "current_failure_revision",
            "current_failure_id",
            "product_outcome",
            "current_failure_owner_lease_id",
            "current_failure_authority_mode",
        } & columns

    monkeypatch.setattr(
        outcome_module,
        "FAILED_OUTCOME_V14_SCHEMA_STATEMENTS",
        statements,
    )
    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v13_legacy_outcome_row_fails_closed_without_alias_migration(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    payload = _failure()
    payload["current_outcome"] = "failed"
    envelope = parse_failure_envelope(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    canonical = canonical_diagnostics_bytes(envelope)
    legacy = canonical.replace(
        b'"current_outcome":"failed"',
        b'"current_outcome":"partial"',
    )
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, envelope)
        conn.commit()
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE runtime_control_failure_envelope_revisions
            SET canonical_bytes = ?, canonical_sha256 = ?, current_outcome = 'partial'
            WHERE failure_id = ? AND revision = 1
            """,
            (legacy, sha256(legacy).hexdigest(), envelope.failure_id),
        )
        conn.execute(
            """
            CREATE TRIGGER runtime_control_failure_envelopes_no_update
            BEFORE UPDATE ON runtime_control_failure_envelope_revisions
            BEGIN
              SELECT RAISE(ABORT, 'failure_envelope_immutable');
            END
            """
        )
    _downgrade_v14_run_columns_to_v13(path)

    with pytest.raises(FailureEnvelopeStorageError):
        RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert conn.execute(
            """
            SELECT current_outcome
            FROM runtime_control_failure_envelope_revisions
            WHERE failure_id = ? AND revision = 1
            """,
            (envelope.failure_id,),
        ).fetchone() == ("partial",)
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE runtime_control_failure_envelope_revisions
            SET canonical_bytes = ?, canonical_sha256 = ?, current_outcome = 'failed'
            WHERE failure_id = ? AND revision = 1
            """,
            (canonical, sha256(canonical).hexdigest(), envelope.failure_id),
        )
        conn.execute(
            """
            CREATE TRIGGER runtime_control_failure_envelopes_no_update
            BEFORE UPDATE ON runtime_control_failure_envelope_revisions
            BEGIN
              SELECT RAISE(ABORT, 'failure_envelope_immutable');
            END
            """
        )

    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14


@pytest.mark.parametrize("claimed_version", (13, 14))
def test_poisoned_complete_failed_outcome_schema_fails_closed_before_version_bump(
    tmp_path: Path,
    claimed_version: int,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    _downgrade_v14_run_columns_to_v13(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            ALTER TABLE runtime_control_runs
            ADD COLUMN state_revision TEXT NOT NULL DEFAULT 0
              CHECK (state_revision >= 0)
            """
        )
        conn.execute(
            """
            ALTER TABLE runtime_control_runs
            ADD COLUMN current_failure_revision INTEGER
              CHECK (current_failure_revision IS NULL OR current_failure_revision >= 1)
            """
        )
        conn.execute(
            """
            ALTER TABLE runtime_control_runs
            ADD COLUMN current_failure_id TEXT
              CHECK (
                (current_failure_id IS NULL) = (current_failure_revision IS NULL)
              )
            """
        )
        conn.execute(
            """
            ALTER TABLE runtime_control_runs
            ADD COLUMN product_outcome TEXT
              CHECK (
                product_outcome IS NULL
                OR product_outcome IN (
                  'succeeded_with_results',
                  'needs_attention'
                )
              )
            """
        )
        conn.execute(
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
            """
        )
        conn.execute(
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
            """
        )
        conn.execute(f"PRAGMA user_version = {claimed_version}")

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeControlStore(path).initialize()

    assert (
        exc_info.value.reason_code
        == "runtime_control_failed_outcome_schema_collision"
    )
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == claimed_version
        conn.execute(
            "ALTER TABLE runtime_control_runs "
            "DROP COLUMN current_failure_authority_mode"
        )
        conn.execute(
            "ALTER TABLE runtime_control_runs "
            "DROP COLUMN current_failure_owner_lease_id"
        )
        conn.execute("ALTER TABLE runtime_control_runs DROP COLUMN product_outcome")
        conn.execute("ALTER TABLE runtime_control_runs DROP COLUMN current_failure_id")
        conn.execute(
            "ALTER TABLE runtime_control_runs DROP COLUMN current_failure_revision"
        )
        conn.execute("ALTER TABLE runtime_control_runs DROP COLUMN state_revision")
        conn.execute("PRAGMA user_version = 13")

    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14


@pytest.mark.parametrize("completed_statements", (1, 2, 3))
def test_fresh_failure_envelope_ddl_failure_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_statements: int,
) -> None:
    import seektalent.diagnostics_storage as storage_module
    from seektalent.diagnostics_storage import FailureEnvelopeStorageError
    from seektalent_runtime_control.store import RuntimeControlStore

    path = tmp_path / "runtime_control.sqlite3"
    statements = storage_module._SCHEMA_STATEMENTS
    monkeypatch.setattr(
        storage_module,
        "_SCHEMA_STATEMENTS",
        (*statements[:completed_statements], "CREATE TABL injected_invalid_statement"),
    )

    with pytest.raises(FailureEnvelopeStorageError) as exc_info:
        RuntimeControlStore(path).initialize()

    assert exc_info.value.reason == "failure_envelope_schema_failed"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            conn.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE name LIKE '%failure_envelope%'
                  AND name NOT LIKE 'sqlite_autoindex%'
                """
            ).fetchall()
            == []
        )

    monkeypatch.setattr(storage_module, "_SCHEMA_STATEMENTS", statements)
    RuntimeControlStore(path).initialize()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM sqlite_master
                WHERE name LIKE '%failure_envelope%'
                  AND name NOT LIKE 'sqlite_autoindex%'
                """
            ).fetchone()[0]
            == 6
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_real_v12_to_v13_migration_creates_backup_and_reopens(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            DROP TRIGGER runtime_control_failure_envelopes_no_update;
            DROP TRIGGER runtime_control_failure_envelopes_no_delete;
            DROP TABLE runtime_control_failure_envelope_revisions;
            PRAGMA user_version = 12;
            """
        )

    RuntimeControlStore(path).initialize()
    RuntimeControlStore(path).initialize()

    backups = list((tmp_path / "migration_backups").glob("runtime-control-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 12
        assert (
            backup.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'runtime_control_failure_envelope_revisions'
                """
            ).fetchone()
            is None
        )


def test_v12_to_v13_failure_rolls_back_partial_schema_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import seektalent_runtime_control.store as store_module
    from seektalent.diagnostics_storage import create_failure_envelope_schema
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            DROP TABLE runtime_control_failure_envelope_revisions;
            PRAGMA user_version = 12;
            """
        )

    def fail_after_schema(conn: sqlite3.Connection) -> None:
        create_failure_envelope_schema(conn)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        store_module,
        "create_failure_envelope_schema",
        fail_after_schema,
    )
    with pytest.raises(RuntimeError, match="injected migration failure"):
        RuntimeControlStore(path).initialize()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        assert (
            conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'runtime_control_failure_envelope_revisions'
                """
            ).fetchone()
            is None
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("poison_kind", ("table", "index", "trigger"))
def test_v12_to_v13_rejects_poisoned_schema_objects_without_partial_ddl(
    tmp_path: Path,
    poison_kind: str,
) -> None:
    from seektalent.diagnostics_storage import FailureEnvelopeStorageError
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE runtime_control_failure_envelope_revisions")
        if poison_kind == "table":
            conn.executescript(
                """
                CREATE TABLE runtime_control_failure_envelope_revisions (
                  failure_id TEXT,
                  revision INTEGER,
                  reason_code TEXT
                );
                CREATE TRIGGER runtime_control_failure_envelopes_no_update
                BEFORE UPDATE ON runtime_control_failure_envelope_revisions
                BEGIN
                  SELECT 1;
                END;
                """
            )
        elif poison_kind == "index":
            conn.executescript(
                """
                CREATE TABLE poisoned_failure_envelope_object (value INTEGER);
                CREATE INDEX idx_runtime_failure_envelopes_run
                  ON poisoned_failure_envelope_object(value);
                """
            )
        else:
            conn.executescript(
                """
                CREATE TABLE poisoned_failure_envelope_object (value INTEGER);
                CREATE TRIGGER runtime_control_failure_envelopes_no_update
                BEFORE UPDATE ON poisoned_failure_envelope_object
                BEGIN
                  SELECT 1;
                END;
                """
            )
        conn.execute("PRAGMA user_version = 12")
        before = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name LIKE '%failure_envelope%'
            ORDER BY type, name
            """
        ).fetchall()

    with pytest.raises(FailureEnvelopeStorageError) as exc_info:
        RuntimeControlStore(path).initialize()

    assert exc_info.value.reason == "failure_envelope_schema_failed"
    with sqlite3.connect(path) as conn:
        after = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name LIKE '%failure_envelope%'
            ORDER BY type, name
            """
        ).fetchall()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert after == before


def test_v12_to_v13_rejects_exact_preexisting_schema_and_corrupt_row(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    envelope = _envelope()
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, envelope)
        conn.commit()
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
        conn.execute(
            """
            UPDATE runtime_control_failure_envelope_revisions
            SET canonical_sha256 = ?
            WHERE failure_id = ? AND revision = 1
            """,
            ("0" * 64, envelope.failure_id),
        )
        conn.execute(
            """
            CREATE TRIGGER runtime_control_failure_envelopes_no_update
            BEFORE UPDATE ON runtime_control_failure_envelope_revisions
            BEGIN
              SELECT RAISE(ABORT, 'failure_envelope_immutable');
            END
            """
        )
        conn.execute("PRAGMA user_version = 12")
        before = conn.execute(
            """
            SELECT canonical_sha256
            FROM runtime_control_failure_envelope_revisions
            WHERE failure_id = ? AND revision = 1
            """,
            (envelope.failure_id,),
        ).fetchone()
        before_objects = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name LIKE '%failure_envelope%'
            ORDER BY type, name
            """
        ).fetchall()

    with pytest.raises(FailureEnvelopeStorageError) as exc_info:
        RuntimeControlStore(path).initialize()

    assert exc_info.value.reason == "failure_envelope_schema_failed"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        after = conn.execute(
            """
            SELECT canonical_sha256
            FROM runtime_control_failure_envelope_revisions
            WHERE failure_id = ? AND revision = 1
            """,
            (envelope.failure_id,),
        ).fetchone()
        after_objects = conn.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name LIKE '%failure_envelope%'
            ORDER BY type, name
            """
        ).fetchall()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert after == before == ("0" * 64,)
    assert after_objects == before_objects


def test_caller_transaction_controls_rollback_commit_and_restart_readback(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import (
        load_failure_envelope_revision,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    envelope = _envelope()

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created = store_failure_envelope_revision(conn, envelope)
        assert created.disposition == "created"
        conn.rollback()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 0
        )

        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, envelope)
        conn.commit()

    with sqlite3.connect(path) as restarted:
        loaded = load_failure_envelope_revision(
            restarted,
            failure_id=envelope.failure_id,
            revision=envelope.revision,
        )

    assert loaded == envelope


class _TrackingConnection(sqlite3.Connection):
    transaction_calls: list[str]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.transaction_calls = []

    def execute(
        self,
        sql: str,
        parameters: object = (),
        /,
    ) -> sqlite3.Cursor:
        operation = sql.lstrip().split(maxsplit=1)[0].upper()
        if operation in {"BEGIN", "COMMIT", "ROLLBACK"}:
            self.transaction_calls.append(operation)
        return super().execute(sql, parameters)

    def commit(self) -> None:
        self.transaction_calls.append("COMMIT")
        super().commit()

    def rollback(self) -> None:
        self.transaction_calls.append("ROLLBACK")
        super().rollback()

    def close(self) -> None:
        self.transaction_calls.append("CLOSE")
        super().close()


def test_writer_uses_only_active_caller_connection_and_never_controls_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    conn = sqlite3.connect(path, factory=_TrackingConnection)
    try:
        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, _envelope())
        assert exc_info.value.reason == "failure_envelope_transaction_required"

        conn.execute("BEGIN IMMEDIATE")
        conn.transaction_calls.clear()

        def forbidden_connect(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("writer opened a second connection")

        monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
        store_failure_envelope_revision(conn, _envelope())

        assert conn.in_transaction
        assert conn.transaction_calls == []
    finally:
        sqlite3.Connection.rollback(conn)
        sqlite3.Connection.close(conn)


def test_closed_caller_connection_fails_closed_before_payload_admission() -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    conn = sqlite3.connect(":memory:")
    conn.close()

    with pytest.raises(FailureEnvelopeStorageError) as exc_info:
        store_failure_envelope_revision(conn, b'{"raw_secret":"must_not_be_parsed"}')

    assert exc_info.value.reason == "failure_envelope_transaction_required"
    assert str(exc_info.value) == "failure_envelope_transaction_required"
    error_details = f"{exc_info.value!r} {exc_info.value.__dict__!r}"
    assert "closed" not in error_details
    assert "raw_secret" not in error_details


def test_exact_replay_is_idempotent_and_keeps_exact_canonical_identity(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import store_failure_envelope_revision

    path = _initialized_path(tmp_path)
    envelope = _envelope()

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created = store_failure_envelope_revision(conn, envelope)
        replayed = store_failure_envelope_revision(
            conn,
            canonical_diagnostics_bytes(envelope),
        )
        row = conn.execute(
            """
            SELECT canonical_bytes, canonical_sha256
            FROM runtime_control_failure_envelope_revisions
            """
        ).fetchone()
        conn.commit()

    assert created.disposition == "created"
    assert replayed.disposition == "exact_replay"
    assert created.ref == replayed.ref
    assert row == (
        canonical_diagnostics_bytes(envelope),
        canonical_diagnostics_hash(envelope),
    )
    assert len(row[0]) <= 32 * 1024


@pytest.mark.parametrize(
    ("first_revision", "second_revision", "reason"),
    (
        (2, None, "failure_envelope_revision_sequence"),
        (1, 3, "failure_envelope_revision_sequence"),
        (1, 1, "failure_envelope_revision_conflict"),
    ),
)
def test_revision_sequence_and_conflicts_fail_without_mutation(
    tmp_path: Path,
    first_revision: int,
    second_revision: int | None,
    reason: str,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    failure_id = "7" * 32

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if first_revision == 1:
            store_failure_envelope_revision(conn, _envelope(failure_id=failure_id))
            candidate = _envelope(
                revision=second_revision or 1,
                failure_id=failure_id,
            )
            if second_revision == 1:
                payload = _failure()
                payload["failure_id"] = failure_id
                payload["detail"] = {
                    "operation_kind": "search",
                    "source_id": "liepin",
                }
                candidate = parse_failure_envelope(
                    json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
                )
        else:
            candidate = _envelope(
                revision=first_revision,
                failure_id=failure_id,
            )

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, candidate)
        assert exc_info.value.reason == reason
        count = conn.execute(
            "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
        ).fetchone()[0]
        conn.rollback()

    assert count == (1 if first_revision == 1 else 0)


def test_exact_replay_of_older_revision_remains_idempotent(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import store_failure_envelope_revision

    path = _initialized_path(tmp_path)
    failure_id = "7" * 32
    first = _envelope(failure_id=failure_id)
    second = _envelope(revision=2, failure_id=failure_id)

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, first)
        store_failure_envelope_revision(conn, second)
        replay = store_failure_envelope_revision(conn, first)
        assert replay.disposition == "exact_replay"
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize(
    ("field", "changed_value"),
    (
        ("correlation_id", "a" * 32),
        ("run_id", "a" * 32),
        ("operation_id", "b" * 32),
        ("attempt_no", 2),
    ),
)
def test_new_revision_rejects_frozen_identity_drift_without_mutation(
    tmp_path: Path,
    field: str,
    changed_value: object,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    first_payload = _failure()
    second_payload = _failure()
    second_payload["revision"] = 2
    second_payload[field] = changed_value
    first = parse_failure_envelope(
        json.dumps(first_payload, separators=(",", ":"), sort_keys=True).encode()
    )
    second = parse_failure_envelope(
        json.dumps(second_payload, separators=(",", ":"), sort_keys=True).encode()
    )

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, first)

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, second)

        assert exc_info.value.reason == "failure_envelope_identity_conflict"
        assert conn.in_transaction
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 1
        )
        conn.rollback()


@pytest.mark.parametrize("corruption", ("bytes", "hash", "projection"))
def test_corrupt_predecessor_blocks_append_without_mutation(
    tmp_path: Path,
    corruption: str,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    first = _envelope()
    second = _envelope(revision=2)
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, first)
        conn.commit()
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
        if corruption == "bytes":
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET canonical_bytes = ?
                WHERE failure_id = ? AND revision = 1
                """,
                (b'{"poisoned":"predecessor"}', first.failure_id),
            )
        elif corruption == "hash":
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET canonical_sha256 = ?
                WHERE failure_id = ? AND revision = 1
                """,
                ("0" * 64, first.failure_id),
            )
        else:
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET run_id = ?
                WHERE failure_id = ? AND revision = 1
                """,
                ("a" * 32, first.failure_id),
            )
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, second)

        assert exc_info.value.reason == "failure_envelope_integrity_failed"
        assert conn.in_transaction
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 1
        )
        conn.rollback()


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE runtime_control_failure_envelope_revisions SET reason_code = 'changed'",
        "DELETE FROM runtime_control_failure_envelope_revisions",
        """
        INSERT OR REPLACE INTO runtime_control_failure_envelope_revisions
        SELECT * FROM runtime_control_failure_envelope_revisions
        """,
    ),
)
def test_sql_cannot_update_delete_or_replace_accepted_revision(
    tmp_path: Path,
    statement: str,
) -> None:
    from seektalent.diagnostics_storage import store_failure_envelope_revision

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, _envelope())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 1
        )
        conn.rollback()


@pytest.mark.parametrize("corruption", ("bytes", "hash", "projection"))
def test_readback_corruption_fails_closed_without_payload_leakage(
    tmp_path: Path,
    corruption: str,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        load_failure_envelope_revision,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    envelope = _envelope()
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, envelope)
        conn.commit()
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
        if corruption == "bytes":
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET canonical_bytes = ?
                """,
                (b'{"secret":"raw payload"}',),
            )
        elif corruption == "hash":
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET canonical_sha256 = ?
                """,
                ("0" * 64,),
            )
        else:
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET run_id = ?
                """,
                ("other_run",),
            )
        conn.commit()

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            load_failure_envelope_revision(
                conn,
                failure_id=envelope.failure_id,
                revision=1,
            )

    assert exc_info.value.reason == "failure_envelope_integrity_failed"
    assert str(exc_info.value) == "failure_envelope_integrity_failed"
    assert "secret" not in repr(exc_info.value)
    assert str(path) not in repr(exc_info.value)


def test_readback_unknown_ref_fails_closed() -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        create_failure_envelope_schema,
        load_failure_envelope_revision,
    )

    with sqlite3.connect(":memory:") as conn:
        create_failure_envelope_schema(conn)
        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            load_failure_envelope_revision(
                conn,
                failure_id="missing_failure",
                revision=1,
            )

    assert exc_info.value.reason == "failure_envelope_not_found"


def test_storage_abort_has_no_partial_row_and_database_integrity_is_ok(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TRIGGER injected_failure_envelope_abort
            AFTER INSERT ON runtime_control_failure_envelope_revisions
            BEGIN
              SELECT RAISE(ABORT, 'injected raw sqlite detail');
            END
            """
        )
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, _envelope())

        assert exc_info.value.reason == "failure_envelope_storage_failed"
        assert "injected" not in repr(exc_info.value)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 0
        )
        conn.rollback()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_sidecar_browser_source_and_wtscli_have_zero_writer_calls_or_table_access() -> None:
    root = Path(__file__).parents[1] / "src"
    seektalent_root = root / "seektalent"
    forbidden_roots = (
        seektalent_root / "opencli_browser",
        seektalent_root / "source_adapters",
        seektalent_root / "source_contracts",
        seektalent_root / "source_port",
        seektalent_root / "sources",
        seektalent_root / "providers",
    )
    root_module_patterns = (
        "sidecar_*.py",
        "wtscli_*.py",
        "browser_bridge_*.py",
        "owned_sidecar_process.py",
        "windows_sidecar_process.py",
    )
    source_suffixes = {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
    }
    forbidden_access = (
        "store_failure_envelope_revision",
        "runtime_control_failure_envelope_revisions",
        "seektalent_runtime_control",
        "RuntimeControlStore",
        "runtime_control_path",
        "runtime_control_db_path",
        "runtime_control.sqlite3",
    )
    forbidden_files: set[Path] = set()
    for pattern in root_module_patterns:
        matches = set(seektalent_root.glob(pattern))
        assert matches, f"missing source-boundary modules for pattern: {pattern}"
        assert all(path.is_file() for path in matches)
        forbidden_files.update(matches)
    violations: list[str] = []
    for package_root in forbidden_roots:
        assert package_root.is_dir(), f"missing source-boundary root: {package_root}"
        for path in package_root.rglob("*"):
            if path.is_file() and path.suffix in source_suffixes:
                forbidden_files.add(path)
    assert forbidden_files
    for path in sorted(forbidden_files):
        source = path.read_text(encoding="utf-8")
        if any(token in source for token in forbidden_access):
            violations.append(str(path.relative_to(root)))
    assert violations == []


def test_failure_envelope_writer_has_only_the_main_owned_atomic_boundary() -> None:
    root = Path(__file__).parents[1] / "src"
    callers: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "diagnostics_storage.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "store_failure_envelope_revision"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "store_failure_envelope_revision"
                )
            ):
                callers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert len(callers) == 1
    assert callers[0].startswith(
        "seektalent_runtime_control/failed_outcome.py:"
    )


def test_production_failed_outcome_callers_remain_zero() -> None:
    root = Path(__file__).parents[1] / "src"
    callers: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "store.py" and path.parent.name == "seektalent_runtime_control":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "commit_failed_outcome"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "commit_failed_outcome"
                )
            ):
                callers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert callers == []
