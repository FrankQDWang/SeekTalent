from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from seektalent.backup_group import ProductDatabaseSpec, product_database_specs
from seektalent.config import AppSettings
from seektalent.corpus.store import CORPUS_SQLITE_USER_VERSION
from seektalent.local_storage_lifecycle import SQLiteFileReport, sqlite_file_report
from seektalent.providers.liepin.store import LIEPIN_SCHEMA_VERSION
from seektalent.sqlite_migrations import SQLiteMigrationError, has_user_tables, read_user_version, run_sqlite_integrity_checks
from seektalent_agent_memory.store import AGENT_MEMORY_SCHEMA_VERSION
from seektalent_conversation_agent.store import CONVERSATION_AGENT_SCHEMA_VERSION
from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION


DEFAULT_DISK_PREFLIGHT_BYTES = 1_000_000_000
OperatorHealthStatus = Literal["ok", "warning", "failed"]
DatabaseSchemaStatus = Literal["ok", "missing", "uninitialized", "upgrade_available", "unsupported", "unavailable"]
DatabaseIntegrityStatus = Literal["ok", "missing", "failed", "unavailable"]

_EXPECTED_USER_VERSIONS = {
    "runtime_control": RUNTIME_CONTROL_SCHEMA_VERSION,
    "conversation": CONVERSATION_AGENT_SCHEMA_VERSION,
    "agent_memory": AGENT_MEMORY_SCHEMA_VERSION,
    "liepin": LIEPIN_SCHEMA_VERSION,
    "corpus": CORPUS_SQLITE_USER_VERSION,
}


@dataclass(frozen=True)
class DiskPreflightStatus:
    path: Path
    status: OperatorHealthStatus
    reason_code: str
    required_free_bytes: int
    free_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class DatabaseHealthStatus:
    name: str
    path: Path
    status: OperatorHealthStatus
    reason_code: str
    exists: bool
    database_size_bytes: int
    wal_size_bytes: int
    shm_size_bytes: int
    schema_status: DatabaseSchemaStatus
    schema_user_version: int | None
    expected_schema_user_version: int | None
    integrity_status: DatabaseIntegrityStatus
    error: str | None = None


@dataclass(frozen=True)
class OperatorHealthReport:
    status: OperatorHealthStatus
    reason_code: str
    disk: DiskPreflightStatus
    databases: tuple[DatabaseHealthStatus, ...]


def build_operator_health_report(
    settings: AppSettings,
    *,
    required_free_bytes: int = DEFAULT_DISK_PREFLIGHT_BYTES,
) -> OperatorHealthReport:
    disk = check_disk_preflight(settings.project_root / ".seektalent", required_free_bytes=required_free_bytes)
    databases = tuple(inspect_database_health(spec) for spec in product_database_specs(settings))
    status, reason_code = _report_status(disk=disk, databases=databases)
    return OperatorHealthReport(status=status, reason_code=reason_code, disk=disk, databases=databases)


def check_disk_preflight(path: Path, *, required_free_bytes: int = DEFAULT_DISK_PREFLIGHT_BYTES) -> DiskPreflightStatus:
    usage_path = _nearest_existing_parent(path)
    usage = shutil.disk_usage(usage_path)
    if usage.free < required_free_bytes:
        return DiskPreflightStatus(
            path=usage_path,
            status="failed",
            reason_code="disk_free_below_required",
            required_free_bytes=required_free_bytes,
            free_bytes=usage.free,
            total_bytes=usage.total,
        )
    return DiskPreflightStatus(
        path=usage_path,
        status="ok",
        reason_code="disk_free_ok",
        required_free_bytes=required_free_bytes,
        free_bytes=usage.free,
        total_bytes=usage.total,
    )


def inspect_database_health(spec: ProductDatabaseSpec) -> DatabaseHealthStatus:
    report, report_error = _safe_sqlite_file_report(spec.path)
    expected_user_version = _EXPECTED_USER_VERSIONS.get(spec.name)
    if not spec.path.exists():
        return DatabaseHealthStatus(
            name=spec.name,
            path=spec.path,
            status="warning",
            reason_code="db_missing",
            exists=False,
            database_size_bytes=0,
            wal_size_bytes=0,
            shm_size_bytes=0,
            schema_status="missing",
            schema_user_version=None,
            expected_schema_user_version=expected_user_version,
            integrity_status="missing",
        )
    if not spec.path.is_file():
        return DatabaseHealthStatus(
            name=spec.name,
            path=spec.path,
            status="failed",
            reason_code="db_path_not_file",
            exists=True,
            database_size_bytes=report.database_size_bytes,
            wal_size_bytes=report.wal_size_bytes,
            shm_size_bytes=report.shm_size_bytes,
            schema_status="unavailable",
            schema_user_version=None,
            expected_schema_user_version=expected_user_version,
            integrity_status="unavailable",
        )
    if report_error is not None:
        return _failed_database_health(
            spec,
            reason_code="sqlite_open_failed",
            error=report_error,
            expected_user_version=expected_user_version,
            report=report,
        )

    try:
        conn = sqlite3.connect(f"file:{spec.path.resolve()}?mode=ro", uri=True)
        try:
            user_version = read_user_version(conn)
            has_tables = has_user_tables(conn)
            run_sqlite_integrity_checks(conn, store_name=spec.name, foreign_keys=False)
        finally:
            conn.close()
    except SQLiteMigrationError as exc:
        return _failed_database_health(
            spec,
            reason_code=exc.reason_code,
            error=str(exc),
            expected_user_version=expected_user_version,
            report=report,
        )
    except sqlite3.Error as exc:
        return _failed_database_health(
            spec,
            reason_code="sqlite_open_failed",
            error=str(exc),
            expected_user_version=expected_user_version,
            report=report,
        )

    schema_status, schema_reason = _schema_status(
        user_version=user_version,
        expected_user_version=expected_user_version,
        has_tables=has_tables,
    )
    status: OperatorHealthStatus = "ok" if schema_status == "ok" else "warning"
    if schema_status == "unsupported":
        status = "failed"

    return DatabaseHealthStatus(
        name=spec.name,
        path=spec.path,
        status=status,
        reason_code=schema_reason,
        exists=True,
        database_size_bytes=report.database_size_bytes,
        wal_size_bytes=report.wal_size_bytes,
        shm_size_bytes=report.shm_size_bytes,
        schema_status=schema_status,
        schema_user_version=user_version,
        expected_schema_user_version=expected_user_version,
        integrity_status="ok",
    )


def _failed_database_health(
    spec: ProductDatabaseSpec,
    *,
    reason_code: str,
    error: str,
    expected_user_version: int | None,
    report: SQLiteFileReport,
) -> DatabaseHealthStatus:
    return DatabaseHealthStatus(
        name=spec.name,
        path=spec.path,
        status="failed",
        reason_code=reason_code,
        exists=True,
        database_size_bytes=report.database_size_bytes,
        wal_size_bytes=report.wal_size_bytes,
        shm_size_bytes=report.shm_size_bytes,
        schema_status="unavailable",
        schema_user_version=None,
        expected_schema_user_version=expected_user_version,
        integrity_status="failed",
        error=error,
    )


def _schema_status(
    *,
    user_version: int,
    expected_user_version: int | None,
    has_tables: bool,
) -> tuple[DatabaseSchemaStatus, str]:
    if expected_user_version is None:
        return "ok", "db_ok"
    if user_version > expected_user_version:
        return "unsupported", "sqlite_schema_unsupported"
    if user_version == 0 and has_tables:
        return "uninitialized", "sqlite_schema_unversioned"
    if 0 < user_version < expected_user_version:
        return "upgrade_available", "sqlite_schema_upgrade_available"
    return "ok", "db_ok"


def _safe_sqlite_file_report(path: Path) -> tuple[SQLiteFileReport, str | None]:
    try:
        return sqlite_file_report(path), None
    except sqlite3.Error as exc:
        return (
            SQLiteFileReport(
                path=path,
                database_size_bytes=_file_size(path),
                wal_size_bytes=_file_size(path.with_name(path.name + "-wal")),
                shm_size_bytes=_file_size(path.with_name(path.name + "-shm")),
                freelist_count=None,
            ),
            str(exc),
        )


def _report_status(
    *,
    disk: DiskPreflightStatus,
    databases: tuple[DatabaseHealthStatus, ...],
) -> tuple[OperatorHealthStatus, str]:
    if disk.status == "failed" or any(database.status == "failed" for database in databases):
        return "failed", "operator_health_failed"
    if disk.status == "warning" or any(database.status == "warning" for database in databases):
        return "warning", "operator_health_warnings"
    return "ok", "operator_health_ok"


def _nearest_existing_parent(path: Path) -> Path:
    current = path.expanduser()
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() and path.is_file() else 0
