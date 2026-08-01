from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tests.settings_factory import make_settings


NOW = datetime(2026, 6, 17, tzinfo=timezone.utc)


def test_inventory_reports_product_dbs_sqlite_siblings_and_debug_roots(tmp_path: Path) -> None:
    from seektalent.local_storage_lifecycle import build_local_storage_inventory

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    settings.runtime_control_path.parent.mkdir(parents=True)
    settings.runtime_control_path.write_bytes(b"runtime-control")
    settings.runtime_control_path.with_name(settings.runtime_control_path.name + "-wal").write_bytes(b"wal-bytes")
    settings.conversation_agent_path.write_bytes(b"conversation")
    (settings.artifacts_path / "debug" / "2026" / "06" / "17").mkdir(parents=True)
    (settings.artifacts_path / "debug" / "2026" / "06" / "17" / "trace.json").write_text("{}", encoding="utf-8")
    (settings.artifacts_path / "corpus" / "2026" / "06" / "17").mkdir(parents=True)
    (settings.artifacts_path / "exports" / "2026" / "06" / "17").mkdir(parents=True)
    (settings.llm_cache_path / "exact").mkdir(parents=True)
    (settings.llm_cache_path / "exact" / "cache.json").write_text("cache", encoding="utf-8")
    settings.resolve_workspace_path(settings.liepin_session_store_dir).mkdir(parents=True)

    inventory = build_local_storage_inventory(settings)
    roots = {root.name: root for root in inventory.roots}

    assert inventory.runtime_mode == "prod"
    assert inventory.budget_bytes == 2_000_000_000
    assert roots["runtime_control_db"].storage_class == "product_db"
    assert roots["runtime_control_db"].protected is True
    assert roots["runtime_control_db"].size_bytes == len(b"runtime-control") + len(b"wal-bytes")
    assert roots["conversation_agent_db"].storage_class == "product_db"
    assert roots["liepin_session_store"].storage_class == "browser_state"
    assert roots["corpus_artifacts"].storage_class == "artifact_export"
    assert roots["export_artifacts"].storage_class == "artifact_export"
    assert roots["artifacts_root"].storage_class == "artifact_debug"
    assert roots["llm_cache"].storage_class == "cache"
    assert inventory.total_size_bytes >= roots["runtime_control_db"].size_bytes


def test_cleanup_dry_run_and_apply_prunes_only_disposable_local_files(tmp_path: Path) -> None:
    from seektalent.local_storage_lifecycle import LocalStorageLifecyclePolicy, cleanup_local_storage

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    settings.runtime_control_path.parent.mkdir(parents=True)
    settings.runtime_control_path.write_text("must stay", encoding="utf-8")
    old_debug = settings.artifacts_path / "debug" / "2026" / "05" / "01" / "debug.json"
    old_debug.parent.mkdir(parents=True)
    old_debug.write_text("debug", encoding="utf-8")
    old_run = settings.artifacts_path / "runs" / "2026" / "05" / "01" / "run_done" / "runtime" / "events.jsonl"
    old_run.parent.mkdir(parents=True)
    old_run.write_text("done", encoding="utf-8")
    active_run = settings.artifacts_path / "runs" / "2026" / "05" / "01" / "run_active" / "runtime" / "events.jsonl"
    active_run.parent.mkdir(parents=True)
    active_run.write_text("active", encoding="utf-8")
    (active_run.parents[1] / "manifests").mkdir(parents=True)
    (active_run.parents[1] / "manifests" / "run_manifest.json").write_text('{"status":"running"}', encoding="utf-8")
    cache_file = settings.llm_cache_path / "exact_llm_cache.sqlite3"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("cache", encoding="utf-8")
    backups = tmp_path / ".seektalent" / "backups"
    backups.mkdir(parents=True)
    old_backup = backups / "workbench-old.sqlite3"
    old_backup.write_text("old backup", encoding="utf-8")
    old_backup.with_suffix(".json").write_text("{}", encoding="utf-8")
    new_backup = backups / "workbench-new.sqlite3"
    new_backup.write_text("new backup", encoding="utf-8")
    new_backup.with_suffix(".json").write_text("{}", encoding="utf-8")
    _set_mtime(old_debug, "2026-05-01T00:00:00+00:00")
    _set_mtime(cache_file, "2026-05-01T00:00:00+00:00")
    _set_mtime(old_backup, "2026-05-01T00:00:00+00:00")
    _set_mtime(old_backup.with_suffix(".json"), "2026-05-01T00:00:00+00:00")
    _set_mtime(new_backup, "2026-06-16T00:00:00+00:00")
    _set_mtime(new_backup.with_suffix(".json"), "2026-06-16T00:00:00+00:00")

    policy = LocalStorageLifecyclePolicy(debug_retention_days=7, cache_retention_days=7, backup_retention_days=7, max_backup_count=1)
    dry_run = cleanup_local_storage(settings, now=NOW, policy=policy, apply=False)

    assert dry_run.dry_run is True
    assert {item.reason_code for item in dry_run.candidates} >= {
        "artifact_debug_expired",
        "artifact_run_expired",
        "cache_expired",
        "backup_expired",
    }
    assert settings.runtime_control_path.exists()
    assert old_debug.exists()
    assert old_run.exists()
    assert cache_file.exists()
    assert old_backup.exists()
    assert active_run.exists()

    applied = cleanup_local_storage(settings, now=NOW, policy=policy, apply=True)

    assert applied.deleted_file_count >= 4
    assert settings.runtime_control_path.exists()
    assert not old_debug.exists()
    assert not old_run.exists()
    assert not cache_file.exists()
    assert not old_backup.exists()
    assert not old_backup.with_suffix(".json").exists()
    assert new_backup.exists()
    assert active_run.exists()


def test_runtime_artifact_cleanup_scope_inventory_only_scans_runtime_artifact_roots(tmp_path: Path) -> None:
    from seektalent.local_storage_lifecycle import LocalStorageLifecyclePolicy, cleanup_local_storage

    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    old_run = settings.artifacts_path / "runs" / "2026" / "05" / "01" / "run_done" / "runtime" / "events.jsonl"
    old_run.parent.mkdir(parents=True)
    old_run.write_text("done", encoding="utf-8")
    cache_file = settings.llm_cache_path / "exact_llm_cache.sqlite3"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("cache should not be scanned for scoped cleanup inventory", encoding="utf-8")

    result = cleanup_local_storage(
        settings,
        now=NOW,
        policy=LocalStorageLifecyclePolicy(debug_retention_days=7),
        apply=False,
        cleanup_scope="runtime_artifacts",
    )

    assert {root.name for root in result.inventory.roots} == {
        "runtime_run_artifacts",
        "runtime_benchmark_artifacts",
    }
    assert {candidate.reason_code for candidate in result.candidates} == {"artifact_run_expired"}


def test_sqlite_checkpoint_reports_blocked_when_reader_prevents_wal_truncate(tmp_path: Path) -> None:
    from seektalent.local_storage_lifecycle import checkpoint_sqlite_database, sqlite_file_report, vacuum_sqlite_database

    db_path = tmp_path / "runtime_control.sqlite3"
    writer = sqlite3.connect(db_path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
    writer.execute("INSERT INTO items(value) VALUES ('before-reader')")
    writer.commit()
    reader = sqlite3.connect(db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM items").fetchall()
    writer.execute("INSERT INTO items(value) VALUES ('after-reader')")
    writer.commit()

    report = sqlite_file_report(db_path)
    checkpoint = checkpoint_sqlite_database(db_path)

    reader.close()
    writer.close()
    vacuum = vacuum_sqlite_database(db_path)
    assert report.wal_size_bytes > 0
    assert checkpoint.status == "blocked"
    assert checkpoint.busy_count > 0
    assert vacuum.status == "vacuumed"


def _set_mtime(path: Path, timestamp: str) -> None:
    parsed = datetime.fromisoformat(timestamp)
    os.utime(path, (parsed.timestamp(), parsed.timestamp()))
