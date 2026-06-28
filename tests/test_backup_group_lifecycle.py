from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from tests.settings_factory import make_settings


NOW = datetime(2026, 6, 17, 8, 30, tzinfo=timezone.utc)


def test_group_backup_covers_product_databases_with_manifest_and_verified_copies(tmp_path: Path) -> None:
    from seektalent.backup_group import backup_product_database_group, product_database_specs

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    specs = product_database_specs(settings)
    assert {spec.name for spec in specs} == {
        "workbench",
        "workbench_v2",
        "runtime_control",
        "conversation",
        "workbench_stream",
        "agent_memory",
        "liepin",
        "corpus",
    }
    for spec in specs:
        _create_sqlite_database(spec.path, marker=spec.name)

    result = backup_product_database_group(settings, now=NOW)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    entries = {entry.name: entry for entry in result.entries}

    assert result.status == "ok"
    assert result.reason_code == "db_group_backup_verified"
    assert manifest["schema_version"] == "seektalent-db-group-backup/v1"
    assert {entry["name"] for entry in manifest["databases"]} == set(entries)
    if os.name == "posix":
        assert _mode(result.backup_root) == 0o700
        assert _mode(result.manifest_path) == 0o600

    for name, entry in entries.items():
        assert entry.status == "verified"
        assert entry.reason_code == "sqlite_backup_verified"
        assert entry.source_path.exists()
        assert entry.backup_path is not None
        assert entry.backup_path.exists()
        assert entry.metadata_path is not None
        assert entry.metadata_path.exists()
        assert entry.backup_size_bytes > 0
        if os.name == "posix":
            assert _mode(entry.backup_path) == 0o600
            assert _mode(entry.metadata_path) == 0o600
        with sqlite3.connect(entry.backup_path) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT value FROM marker").fetchone()[0] == name


def test_group_backup_manifest_records_missing_databases_without_failing_existing_backups(tmp_path: Path) -> None:
    from seektalent.backup_group import backup_product_database_group, product_database_specs

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    runtime_spec = next(spec for spec in product_database_specs(settings) if spec.name == "runtime_control")
    _create_sqlite_database(runtime_spec.path, marker="runtime_control")

    result = backup_product_database_group(settings, now=NOW)
    entries = {entry.name: entry for entry in result.entries}

    assert result.status == "warning"
    assert result.reason_code == "db_group_backup_partial"
    assert entries["runtime_control"].status == "verified"
    assert entries["runtime_control"].reason_code == "sqlite_backup_verified"
    assert entries["conversation"].status == "missing"
    assert entries["conversation"].reason_code == "db_missing"


def test_group_backup_reports_corrupt_database_as_failed_entry(tmp_path: Path) -> None:
    from seektalent.backup_group import backup_product_database_group, product_database_specs

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    runtime_spec = next(spec for spec in product_database_specs(settings) if spec.name == "runtime_control")
    runtime_spec.path.parent.mkdir(parents=True, exist_ok=True)
    runtime_spec.path.write_bytes(b"not a sqlite database")

    result = backup_product_database_group(settings, now=NOW)
    entries = {entry.name: entry for entry in result.entries}

    assert result.status == "failed"
    assert result.reason_code == "db_group_backup_failed"
    assert entries["runtime_control"].status == "failed"
    assert entries["runtime_control"].reason_code == "sqlite_backup_failed"
    assert entries["runtime_control"].error


def _create_sqlite_database(path: Path, *, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES (?)", (marker,))
        conn.execute("PRAGMA user_version = 1")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)
