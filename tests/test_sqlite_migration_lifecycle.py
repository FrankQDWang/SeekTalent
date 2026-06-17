from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from seektalent.sqlite_migrations import SQLiteMigrationError
from seektalent.corpus.store import CORPUS_SQLITE_USER_VERSION, CorpusStore
from seektalent.providers.liepin.store import LIEPIN_SCHEMA_VERSION, LiepinStore
from seektalent_ui.agent_workbench_models import AgentWorkbenchTranscriptPayloadResponse
from seektalent_ui.agent_workbench_stream_store import (
    AGENT_WORKBENCH_STREAM_SCHEMA_VERSION,
    AgentWorkbenchStreamStore,
)


def test_shared_migration_helper_runs_ordered_backup_integrity_and_retention(tmp_path: Path) -> None:
    from seektalent.sqlite_migrations import (
        SQLiteMigrationStep,
        backup_sqlite_before_migration,
        read_user_version,
        run_ordered_migrations,
        run_sqlite_integrity_checks,
    )

    db_path = tmp_path / "unit.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO items(value) VALUES ('before')")
        conn.execute("PRAGMA user_version = 1")

    backup_root = tmp_path / "migration-backups"
    for index in range(12):
        stale = backup_root / f"unit-20260617T0000{index:02d}000000Z.sqlite3"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"stale")
        stale.with_suffix(".json").write_text("{}", encoding="utf-8")

    backup = backup_sqlite_before_migration(
        db_path,
        backup_root=backup_root,
        store_name="unit",
        now="2026-06-17T00:01:00.000000Z",
        max_backups=10,
    )

    def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE items ADD COLUMN migrated INTEGER NOT NULL DEFAULT 1")

    with sqlite3.connect(db_path) as conn:
        assert read_user_version(conn) == 1
        with conn:
            run_ordered_migrations(
                conn,
                from_version=1,
                to_version=2,
                migrations={1: SQLiteMigrationStep(1, 2, migrate_v1_to_v2)},
                store_name="unit",
            )
        run_sqlite_integrity_checks(conn, store_name="unit", foreign_keys=True)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value, migrated FROM items").fetchone()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

    assert row == ("before", 1)
    assert backup is not None
    assert backup.database_path.exists()
    assert backup.metadata_path.exists()
    if os.name == "posix":
        assert _mode(backup_root) == 0o700
        assert _mode(backup.database_path) == 0o600
        assert _mode(backup.metadata_path) == 0o600
    assert len(sorted(backup_root.glob("unit-*.sqlite3"))) == 10


def test_shared_migration_helper_fails_without_raw_copy_when_sqlite_backup_api_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.sqlite_migrations import backup_sqlite_before_migration

    db_path = tmp_path / "unit.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO items(value) VALUES ('before')")

    class BrokenSourceConnection:
        def backup(self, _target: sqlite3.Connection) -> None:
            raise sqlite3.Error("backup api unavailable")

        def close(self) -> None:
            pass

    real_connect = sqlite3.connect

    def fake_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection | BrokenSourceConnection:
        if kwargs.get("uri") is True and str(database).startswith("file:"):
            return BrokenSourceConnection()
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    backup_root = tmp_path / "migration-backups"

    with pytest.raises(SQLiteMigrationError) as exc_info:
        backup_sqlite_before_migration(
            db_path,
            backup_root=backup_root,
            store_name="unit",
            now="2026-06-17T00:01:00.000000Z",
        )

    assert exc_info.value.reason_code == "sqlite_backup_failed"
    assert not list(backup_root.glob("unit-*.sqlite3"))
    assert not list(backup_root.glob("unit-*.json"))


def test_shared_migration_helper_rejects_future_and_missing_migration(tmp_path: Path) -> None:
    from seektalent.sqlite_migrations import SQLiteMigrationError, require_supported_version, run_ordered_migrations

    db_path = tmp_path / "unit.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 99")
        with pytest.raises(SQLiteMigrationError) as unsupported:
            require_supported_version(conn, supported_version=2, store_name="unit")
        with pytest.raises(SQLiteMigrationError) as missing:
            run_ordered_migrations(conn, from_version=1, to_version=3, migrations={}, store_name="unit")

    assert unsupported.value.reason_code == "sqlite_schema_unsupported"
    assert missing.value.reason_code == "sqlite_schema_migration_missing"


def test_runtime_control_future_version_is_refused(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore

    runtime_db = tmp_path / "runtime_control.sqlite3"
    with sqlite3.connect(runtime_db) as conn:
        conn.execute(f"PRAGMA user_version = {RUNTIME_CONTROL_SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeControlError) as runtime_exc:
        RuntimeControlStore(runtime_db).initialize()

    assert runtime_exc.value.reason_code == "runtime_control_schema_unsupported"


def test_workbench_schema_has_version_gate_and_backs_up_legacy_database(tmp_path: Path) -> None:
    from seektalent_ui.workbench_schema import WORKBENCH_SCHEMA_VERSION, initialize_workbench_schema

    legacy_db = tmp_path / "workbench.sqlite3"
    with sqlite3.connect(legacy_db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO legacy_marker(value) VALUES ('keep')")
        initialize_workbench_schema(conn, now="2026-06-17T00:00:00Z", database_path=legacy_db)

    backup_root = legacy_db.parent / "migration_backups"
    backups = sorted(backup_root.glob("workbench-*.sqlite3"))
    assert backups
    with sqlite3.connect(legacy_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == WORKBENCH_SCHEMA_VERSION
        assert conn.execute("SELECT value FROM legacy_marker").fetchone()[0] == "keep"

    future_db = tmp_path / "workbench_future.sqlite3"
    with sqlite3.connect(future_db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA user_version = {WORKBENCH_SCHEMA_VERSION + 1}")
        with pytest.raises(SQLiteMigrationError) as exc_info:
            initialize_workbench_schema(conn, now="2026-06-17T00:00:00Z", database_path=future_db)

    assert exc_info.value.reason_code == "sqlite_schema_unsupported"


def test_workbench_stream_sets_schema_version_and_rejects_newer_db(tmp_path: Path) -> None:
    path = tmp_path / "stream.sqlite3"
    AgentWorkbenchStreamStore(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == AGENT_WORKBENCH_STREAM_SCHEMA_VERSION
        conn.execute(f"PRAGMA user_version = {AGENT_WORKBENCH_STREAM_SCHEMA_VERSION + 1}")

    with pytest.raises(SQLiteMigrationError) as exc_info:
        AgentWorkbenchStreamStore(path)
    assert exc_info.value.reason_code == "sqlite_schema_unsupported"


def test_workbench_stream_migrates_unversioned_legacy_events(tmp_path: Path) -> None:
    path = tmp_path / "stream.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE agent_workbench_stream_events (
                conversation_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(conversation_id, seq),
                UNIQUE(conversation_id, event_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO agent_workbench_stream_events (
                conversation_id, seq, event_id, kind, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "agent_conv_1",
                1,
                "message:msg_1",
                "message.completed",
                AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId="msg_1").model_dump_json(),
                "2026-06-17T00:00:00Z",
            ),
        )

    store = AgentWorkbenchStreamStore(path)

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_workbench_stream_events)").fetchall()}
        assert {"source_kind", "source_id", "source_seq", "idempotency_key"} <= columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == AGENT_WORKBENCH_STREAM_SCHEMA_VERSION
    replay = store.replay_stream_envelopes(conversation_id="agent_conv_1", after_seq=0)
    assert replay[0].payload.messageId == "msg_1"


def test_liepin_store_sets_schema_version_and_rejects_newer_db(tmp_path: Path) -> None:
    path = tmp_path / "liepin.sqlite3"
    LiepinStore(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LIEPIN_SCHEMA_VERSION
        conn.execute(f"PRAGMA user_version = {LIEPIN_SCHEMA_VERSION + 1}")

    with pytest.raises(SQLiteMigrationError) as exc_info:
        LiepinStore(path)
    assert exc_info.value.reason_code == "sqlite_schema_unsupported"


def test_liepin_store_migrates_unversioned_connection_columns(tmp_path: Path) -> None:
    path = tmp_path / "liepin.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE liepin_connections (
                connection_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                compliance_gate_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE liepin_compliance_gates (
                gate_ref TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                provider_account_hash TEXT,
                status TEXT NOT NULL,
                candidate_personal_info_processing_basis TEXT NOT NULL,
                personal_information_processor TEXT NOT NULL,
                operator_audit_owner TEXT NOT NULL,
                account_holder_authorized INTEGER NOT NULL,
                human_initiated_recruiting INTEGER NOT NULL,
                allowed_purposes_json TEXT NOT NULL,
                retention_policy TEXT NOT NULL,
                deletion_sla_days INTEGER NOT NULL,
                deletion_path TEXT NOT NULL,
                raw_payload_access_scope TEXT NOT NULL,
                raw_detail_retention_allowed_after_debug INTEGER NOT NULL,
                fixture_export_allowed INTEGER NOT NULL,
                policy_ref TEXT NOT NULL,
                requested_purpose TEXT NOT NULL
            );
            """
        )

    LiepinStore(path)

    with sqlite3.connect(path) as conn:
        connection_columns = {row[1] for row in conn.execute("PRAGMA table_info(liepin_connections)").fetchall()}
        gate_columns = {row[1] for row in conn.execute("PRAGMA table_info(liepin_compliance_gates)").fetchall()}
        assert {"session_store_key_id", "encrypted_state_sha256", "session_updated_at", "revoked_at"} <= connection_columns
        assert "created_at" in gate_columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LIEPIN_SCHEMA_VERSION


def test_corpus_store_rejects_newer_db_with_preservation_guidance(tmp_path: Path) -> None:
    path = tmp_path / "corpus.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {CORPUS_SQLITE_USER_VERSION + 1}")

    with pytest.raises(SQLiteMigrationError) as exc_info:
        CorpusStore(path).connect()
    assert exc_info.value.reason_code == "sqlite_schema_unsupported"
    assert "export" in str(exc_info.value).lower()
    assert "rebuild" in str(exc_info.value).lower()


def test_corpus_store_rejects_unversioned_schema_with_preservation_guidance(tmp_path: Path) -> None:
    path = tmp_path / "corpus.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE resume_documents (resume_doc_id TEXT)")

    with pytest.raises(SQLiteMigrationError) as exc_info:
        CorpusStore(path).connect()
    assert exc_info.value.reason_code == "sqlite_schema_migration_required"
    assert "unversioned" in str(exc_info.value).lower()
    assert "export" in str(exc_info.value).lower()
    assert "rebuild" in str(exc_info.value).lower()


def test_corpus_stale_schema_error_is_user_safe(tmp_path: Path) -> None:
    path = tmp_path / "corpus_stale.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_meta(key, value) VALUES ('schema_version', 'corpus-schema-v1')")

    with pytest.raises(SQLiteMigrationError) as exc_info:
        CorpusStore(path).connect()

    assert exc_info.value.reason_code == "sqlite_schema_migration_required"
    assert "export" in str(exc_info.value).lower()
    assert "rebuild" in str(exc_info.value).lower()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)
