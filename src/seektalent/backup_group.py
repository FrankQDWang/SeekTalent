from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from seektalent.config import AppSettings
from seektalent.local_storage_lifecycle import SQLiteFileReport, sqlite_file_report
from seektalent.sqlite_migrations import (
    SQLiteMigrationError,
    backup_sqlite_before_migration,
    run_sqlite_integrity_checks,
)


BACKUP_GROUP_SCHEMA_VERSION = "seektalent-db-group-backup/v1"
BackupEntryStatus = Literal["verified", "missing", "failed"]
BackupGroupStatus = Literal["ok", "warning", "failed"]

@dataclass(frozen=True)
class ProductDatabaseSpec:
    name: str
    path: Path


@dataclass(frozen=True)
class DatabaseBackupEntry:
    name: str
    source_path: Path
    status: BackupEntryStatus
    reason_code: str
    source_size_bytes: int
    source_wal_size_bytes: int
    source_shm_size_bytes: int
    backup_path: Path | None = None
    metadata_path: Path | None = None
    backup_size_bytes: int = 0
    error: str | None = None

    def to_manifest_entry(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source_path": str(self.source_path),
            "status": self.status,
            "reason_code": self.reason_code,
            "source_size_bytes": self.source_size_bytes,
            "source_wal_size_bytes": self.source_wal_size_bytes,
            "source_shm_size_bytes": self.source_shm_size_bytes,
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "metadata_path": str(self.metadata_path) if self.metadata_path else None,
            "backup_size_bytes": self.backup_size_bytes,
            "error": self.error,
        }


@dataclass(frozen=True)
class DatabaseGroupBackupResult:
    status: BackupGroupStatus
    reason_code: str
    created_at: str
    backup_root: Path
    manifest_path: Path
    entries: tuple[DatabaseBackupEntry, ...]


def product_database_specs(settings: AppSettings) -> tuple[ProductDatabaseSpec, ...]:
    workspace_root = settings.project_root
    return (
        ProductDatabaseSpec("workbench", workspace_root / ".seektalent" / "workbench.sqlite3"),
        ProductDatabaseSpec("workbench_v2", workspace_root / ".seektalent" / "workbench_v2.sqlite3"),
        ProductDatabaseSpec("runtime_control", settings.runtime_control_path),
        ProductDatabaseSpec("conversation", settings.conversation_agent_path),
        ProductDatabaseSpec("workbench_stream", workspace_root / ".seektalent" / "agent_workbench_stream.sqlite3"),
        ProductDatabaseSpec("agent_memory", settings.agent_memory_path),
        ProductDatabaseSpec("liepin", settings.resolve_workspace_path(settings.liepin_connector_db_path)),
        ProductDatabaseSpec("corpus", settings.corpus_path),
    )


def backup_product_database_group(
    settings: AppSettings,
    *,
    backup_root: Path | None = None,
    now: datetime | None = None,
    max_backups_per_database: int = 10,
) -> DatabaseGroupBackupResult:
    created_at = _created_at(now)
    destination_root = backup_root or settings.project_root / ".seektalent" / "backups"
    destination_root.mkdir(parents=True, exist_ok=True)
    _chmod_private_dir(destination_root)

    entries = tuple(
        _backup_database(
            spec,
            backup_root=destination_root,
            created_at=created_at,
            max_backups_per_database=max_backups_per_database,
        )
        for spec in product_database_specs(settings)
    )
    status, reason_code = _group_status(entries)
    manifest_path = destination_root / f"db-group-{_safe_timestamp(created_at)}.manifest.json"
    result = DatabaseGroupBackupResult(
        status=status,
        reason_code=reason_code,
        created_at=created_at,
        backup_root=destination_root,
        manifest_path=manifest_path,
        entries=entries,
    )
    manifest_path.write_text(
        json.dumps(_manifest_payload(result), ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    _chmod_private_file(manifest_path)
    return result


def _backup_database(
    spec: ProductDatabaseSpec,
    *,
    backup_root: Path,
    created_at: str,
    max_backups_per_database: int,
) -> DatabaseBackupEntry:
    report = _safe_sqlite_file_report(spec.path)
    if not spec.path.exists():
        return DatabaseBackupEntry(
            name=spec.name,
            source_path=spec.path,
            status="missing",
            reason_code="db_missing",
            source_size_bytes=0,
            source_wal_size_bytes=0,
            source_shm_size_bytes=0,
        )
    if not spec.path.is_file():
        return DatabaseBackupEntry(
            name=spec.name,
            source_path=spec.path,
            status="failed",
            reason_code="db_path_not_file",
            source_size_bytes=report.database_size_bytes,
            source_wal_size_bytes=report.wal_size_bytes,
            source_shm_size_bytes=report.shm_size_bytes,
        )
    try:
        backup = backup_sqlite_before_migration(
            spec.path,
            backup_root=backup_root,
            store_name=f"db-group-{spec.name}",
            now=created_at,
            max_backups=max_backups_per_database,
        )
        if backup is None:
            raise RuntimeError("backup helper returned no backup for existing database")
        _verify_sqlite_backup(backup.database_path, store_name=spec.name)
    except SQLiteMigrationError as exc:
        return _failed_entry(spec, report=report, reason_code=exc.reason_code, error=str(exc))
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        return _failed_entry(spec, report=report, reason_code="sqlite_backup_failed", error=str(exc))

    return DatabaseBackupEntry(
        name=spec.name,
        source_path=spec.path,
        status="verified",
        reason_code="sqlite_backup_verified",
        source_size_bytes=report.database_size_bytes,
        source_wal_size_bytes=report.wal_size_bytes,
        source_shm_size_bytes=report.shm_size_bytes,
        backup_path=backup.database_path,
        metadata_path=backup.metadata_path,
        backup_size_bytes=backup.database_path.stat().st_size,
    )


def _failed_entry(
    spec: ProductDatabaseSpec,
    *,
    report: SQLiteFileReport,
    reason_code: str,
    error: str,
) -> DatabaseBackupEntry:
    return DatabaseBackupEntry(
        name=spec.name,
        source_path=spec.path,
        status="failed",
        reason_code=reason_code,
        source_size_bytes=report.database_size_bytes,
        source_wal_size_bytes=report.wal_size_bytes,
        source_shm_size_bytes=report.shm_size_bytes,
        error=error,
    )


def _verify_sqlite_backup(path: Path, *, store_name: str) -> None:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        run_sqlite_integrity_checks(conn, store_name=store_name, foreign_keys=False)
    finally:
        conn.close()


def _group_status(entries: tuple[DatabaseBackupEntry, ...]) -> tuple[BackupGroupStatus, str]:
    if any(entry.status == "failed" for entry in entries):
        return "failed", "db_group_backup_failed"
    if any(entry.status == "missing" for entry in entries):
        return "warning", "db_group_backup_partial"
    return "ok", "db_group_backup_verified"


def _safe_sqlite_file_report(path: Path) -> SQLiteFileReport:
    try:
        return sqlite_file_report(path)
    except sqlite3.Error:
        return SQLiteFileReport(
            path=path,
            database_size_bytes=_file_size(path),
            wal_size_bytes=_file_size(path.with_name(path.name + "-wal")),
            shm_size_bytes=_file_size(path.with_name(path.name + "-shm")),
            freelist_count=None,
        )


def _manifest_payload(result: DatabaseGroupBackupResult) -> dict[str, object]:
    return {
        "schema_version": BACKUP_GROUP_SCHEMA_VERSION,
        "created_at": result.created_at,
        "status": result.status,
        "reason_code": result.reason_code,
        "backup_root": str(result.backup_root),
        "databases": [entry.to_manifest_entry() for entry in result.entries],
    }


def _created_at(now: datetime | None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_timestamp(value: str) -> str:
    return (
        value.replace("+00:00", "Z")
        .replace("-", "")
        .replace(":", "")
        .replace(".", "")
        .replace("/", "")
    )


def _chmod_private_dir(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o700)


def _chmod_private_file(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o600)


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() and path.is_file() else 0
