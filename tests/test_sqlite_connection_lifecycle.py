from __future__ import annotations

import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


class TrackingConnection:
    def __init__(self, conn: sqlite3.Connection) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "closed", False)
        object.__setattr__(self, "executed_sql", [])

    def __enter__(self) -> TrackingConnection:
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object:
        return self._conn.__exit__(exc_type, exc, tb)

    def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
        self.executed_sql.append(sql)
        return self._conn.execute(sql, parameters)

    def executemany(self, sql: str, parameters: object, /) -> sqlite3.Cursor:
        self.executed_sql.append(sql)
        return self._conn.executemany(sql, parameters)

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        self.executed_sql.append(sql_script)
        return self._conn.executescript(sql_script)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        object.__setattr__(self, "closed", True)
        self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"_conn", "closed", "executed_sql"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._conn, name, value)


class ConnectionTracker:
    def __init__(self) -> None:
        self._real_connect = sqlite3.connect
        self.connections: list[TrackingConnection] = []

    def connect(self, *args: object, **kwargs: object) -> TrackingConnection:
        conn = TrackingConnection(self._real_connect(*args, **kwargs))
        self.connections.append(conn)
        return conn

    def assert_all_closed(self) -> None:
        leaked = [conn for conn in self.connections if not conn.closed]
        assert leaked == []

    def assert_executed(self, expected_fragment: str) -> None:
        executed_sql = "\n".join(sql for conn in self.connections for sql in conn.executed_sql)
        assert expected_fragment in executed_sql


def _track_sqlite_connect(monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> ConnectionTracker:
    tracker = ConnectionTracker()
    monkeypatch.setattr(module.sqlite3, "connect", tracker.connect)
    return tracker


def test_runtime_control_store_closes_connections_and_sets_busy_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import seektalent_runtime_control.store as store_module
    from seektalent_runtime_control.models import RuntimeRunRecord

    tracker = _track_sqlite_connect(monkeypatch, store_module)
    store = store_module.RuntimeControlStore(tmp_path / "runtime_control.sqlite3", busy_timeout_ms=1234)

    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["source_1"],
            stop_reason_code=None,
            created_at="2026-06-17T00:00:00Z",
            updated_at="2026-06-17T00:00:00Z",
            completed_at=None,
        )
    )
    assert store.get_run("runtime_run_1").runtime_run_id == "runtime_run_1"

    tracker.assert_executed("PRAGMA busy_timeout = 1234")
    tracker.assert_all_closed()


def test_liepin_store_closes_connections_and_sets_busy_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import seektalent.providers.liepin.store as store_module

    tracker = _track_sqlite_connect(monkeypatch, store_module)
    store = store_module.LiepinStore(tmp_path / "liepin.sqlite3")

    attempt = store.reserve_detail_attempt(
        tenant_id="tenant_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
        provider_account_hash="account_hash_1",
        candidate_provider_id="candidate_1",
        budget_date="2026-06-17",
        provider_day_key="liepin:account_hash_1:2026-06-17",
        timezone="Asia/Shanghai",
        idempotency_key="open:candidate_1",
    )
    assert attempt.state == "approved_not_started"
    assert store.count_detail_budget_consumed(
        tenant_id="tenant_1",
        workspace_id="workspace_1",
        actor_id="actor_1",
        provider_account_hash="account_hash_1",
        provider_day_key="liepin:account_hash_1:2026-06-17",
    ) == 0

    tracker.assert_executed("PRAGMA busy_timeout")
    tracker.assert_all_closed()


def test_local_storage_sqlite_helpers_close_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import seektalent.local_storage_lifecycle as lifecycle_module

    db_path = tmp_path / "local.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO items(value) VALUES ('one')")

    tracker = _track_sqlite_connect(monkeypatch, lifecycle_module)

    assert lifecycle_module.sqlite_file_report(db_path).freelist_count == 0
    assert lifecycle_module.checkpoint_sqlite_database(db_path).status == "checkpointed"
    assert lifecycle_module.vacuum_sqlite_database(db_path).status == "vacuumed"
    tracker.assert_all_closed()
