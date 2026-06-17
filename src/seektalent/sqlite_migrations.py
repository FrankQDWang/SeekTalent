from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class SQLiteMigrationError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SQLiteMigrationStep:
    from_version: int
    to_version: int
    migrate: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class SQLiteMigrationBackup:
    database_path: Path
    metadata_path: Path


def read_user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def require_supported_version(conn: sqlite3.Connection, *, supported_version: int, store_name: str) -> int:
    version = read_user_version(conn)
    if version > supported_version:
        raise SQLiteMigrationError(
            "sqlite_schema_unsupported",
            f"{store_name} schema version {version} is newer than supported version {supported_version}",
        )
    return version


def run_ordered_migrations(
    conn: sqlite3.Connection,
    *,
    from_version: int,
    to_version: int,
    migrations: dict[int, SQLiteMigrationStep],
    store_name: str,
) -> None:
    current = from_version
    while current < to_version:
        step = migrations.get(current)
        if step is None or step.from_version != current or step.to_version <= current:
            raise SQLiteMigrationError(
                "sqlite_schema_migration_missing",
                f"{store_name} missing SQLite migration from version {current} to {to_version}",
            )
        step.migrate(conn)
        conn.execute(f"PRAGMA user_version = {step.to_version}")
        current = step.to_version


def backup_sqlite_before_migration(
    path: Path,
    *,
    backup_root: Path,
    store_name: str,
    now: str,
    max_backups: int = 10,
) -> SQLiteMigrationBackup | None:
    if not path.exists():
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    _chmod_private_dir(backup_root)
    timestamp = _safe_timestamp(now)
    backup_path = backup_root / f"{store_name}-{timestamp}.sqlite3"
    metadata_path = backup_path.with_suffix(".json")
    try:
        _copy_sqlite_database(path, backup_path)
    except sqlite3.Error as exc:
        backup_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise SQLiteMigrationError(
            "sqlite_backup_failed",
            f"Could not back up {store_name} SQLite database before migration: {exc}",
        ) from exc
    _chmod_private_file(backup_path)
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": "sqlite-migration-backup/v1",
                "store_name": store_name,
                "source_database": str(path),
                "backup_database": str(backup_path),
                "created_at": now,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    _chmod_private_file(metadata_path)
    _prune_migration_backups(backup_root, store_name=store_name, max_backups=max_backups)
    return SQLiteMigrationBackup(database_path=backup_path, metadata_path=metadata_path)


def run_sqlite_integrity_checks(conn: sqlite3.Connection, *, store_name: str, foreign_keys: bool) -> None:
    integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
    integrity_messages = [str(row[0]) for row in integrity_rows]
    if integrity_messages != ["ok"]:
        raise SQLiteMigrationError(
            "sqlite_integrity_check_failed",
            f"{store_name} SQLite integrity check failed: {'; '.join(integrity_messages)}",
        )
    if foreign_keys:
        failures = conn.execute("PRAGMA foreign_key_check").fetchall()
        if failures:
            raise SQLiteMigrationError(
                "sqlite_foreign_key_check_failed",
                f"{store_name} SQLite foreign key check failed",
            )


def has_user_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _copy_sqlite_database(source_path: Path, backup_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()


def _prune_migration_backups(backup_root: Path, *, store_name: str, max_backups: int) -> None:
    backups = sorted(backup_root.glob(f"{store_name}-*.sqlite3"), key=lambda path: path.stat().st_mtime)
    while len(backups) > max_backups:
        victim = backups.pop(0)
        metadata = victim.with_suffix(".json")
        victim.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


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
