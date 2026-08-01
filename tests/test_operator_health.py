from __future__ import annotations

import sqlite3
from pathlib import Path

from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION
from tests.settings_factory import make_settings


def test_operator_health_reports_db_files_sqlite_siblings_schema_integrity_and_missing_reason_codes(
    tmp_path: Path,
) -> None:
    from seektalent.operator_health import build_operator_health_report

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    writer = _create_wal_database(settings.runtime_control_path, user_version=RUNTIME_CONTROL_SCHEMA_VERSION)
    try:
        report = build_operator_health_report(settings, required_free_bytes=1)
    finally:
        writer.close()

    databases = {database.name: database for database in report.databases}
    runtime = databases["runtime_control"]

    assert report.status == "warning"
    assert report.reason_code == "operator_health_warnings"
    assert report.disk.status == "ok"
    assert report.disk.reason_code == "disk_free_ok"
    assert runtime.exists is True
    assert runtime.database_size_bytes > 0
    assert runtime.wal_size_bytes > 0
    assert runtime.shm_size_bytes >= 0
    assert runtime.schema_status == "ok"
    assert runtime.schema_user_version == RUNTIME_CONTROL_SCHEMA_VERSION
    assert runtime.integrity_status == "ok"
    assert runtime.reason_code == "db_ok"
    assert databases["conversation"].exists is False
    assert databases["conversation"].status == "warning"
    assert databases["conversation"].reason_code == "db_missing"


def test_operator_health_fails_for_low_disk_preflight_and_unsupported_schema(tmp_path: Path) -> None:
    from seektalent.operator_health import build_operator_health_report

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    _create_sqlite_database(settings.runtime_control_path, user_version=RUNTIME_CONTROL_SCHEMA_VERSION + 100)

    report = build_operator_health_report(settings, required_free_bytes=10**30)
    databases = {database.name: database for database in report.databases}

    assert report.status == "failed"
    assert report.reason_code == "operator_health_failed"
    assert report.disk.status == "failed"
    assert report.disk.reason_code == "disk_free_below_required"
    assert databases["runtime_control"].status == "failed"
    assert databases["runtime_control"].schema_status == "unsupported"
    assert databases["runtime_control"].reason_code == "sqlite_schema_unsupported"


def test_operator_health_reports_corrupt_database_without_raising(tmp_path: Path) -> None:
    from seektalent.operator_health import build_operator_health_report

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    settings.conversation_agent_path.parent.mkdir(parents=True, exist_ok=True)
    settings.conversation_agent_path.write_bytes(b"not a sqlite database")

    report = build_operator_health_report(settings, required_free_bytes=1)
    databases = {database.name: database for database in report.databases}

    assert report.status == "failed"
    assert databases["conversation"].status == "failed"
    assert databases["conversation"].reason_code == "sqlite_open_failed"
    assert databases["conversation"].integrity_status == "failed"
    assert databases["conversation"].error


def _create_wal_database(path: Path, *, user_version: int) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    conn.execute("INSERT INTO marker(value) VALUES ('runtime_control')")
    conn.execute(f"PRAGMA user_version = {user_version}")
    conn.commit()
    return conn


def _create_sqlite_database(path: Path, *, user_version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES ('runtime_control')")
        conn.execute(f"PRAGMA user_version = {user_version}")
