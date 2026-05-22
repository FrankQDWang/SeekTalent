from __future__ import annotations

import argparse
from importlib import metadata as importlib_metadata
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence, cast

from seektalent_ui.workbench_store import DEFAULT_WORKSPACE_ID, WorkbenchStore


ColumnSignature = tuple[str, int, str | None, int]
ForeignKeySignature = tuple[int, int, str, str, str | None, str, str, str]
SchemaSignature = dict[str, object]
EXCLUDED_DATA = ["browser_profiles", "raw_provider_session_state"]
BACKUP_METADATA_SCHEMA = "workbench_sqlite_backup_v1"
RETENTION_POLICY = "manual_retention"
ROLLOUT_READINESS_SCHEMA = "workbench_rollout_readiness_v1"
ROLLOUT_MANUAL_GATES = (
    "real_device_lan_access",
    "real_liepin_login_relay",
    "provider_budget_detail_behavior",
)
WORKBENCH_REQUIRED_TABLES = frozenset(
    {
        "candidate_actions",
        "candidate_evidence",
        "candidate_review_items",
        "connection_status_events",
        "detail_open_ledger",
        "detail_open_requests",
        "external_write_intents",
        "login_attempts",
        "runtime_source_lane_latest_state",
        "runtime_candidate_identity_snapshots",
        "runtime_finalization_revisions",
        "runtime_sourcing_jobs",
        "security_audit_events",
        "session_events",
        "session_requirement_triage",
        "sessions",
        "source_connections",
        "source_run_jobs",
        "source_run_policies",
        "source_runs",
        "tenants",
        "user_sessions",
        "users",
        "workbench_note_writer_leases",
        "workspace_memberships",
        "workspaces",
    }
)
WORKBENCH_REQUIRED_COLUMNS = {
    "candidate_evidence": frozenset(
        {
            "evidence_id",
            "review_item_id",
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "source_run_id",
            "source_kind",
            "evidence_level",
            "provider_candidate_key_hash",
            "resume_id",
            "matched_must_haves_json",
            "matched_preferences_json",
            "missing_risks_json",
            "strengths_json",
            "weaknesses_json",
        }
    ),
    "candidate_review_items": frozenset(
        {
            "review_item_id",
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "primary_evidence_id",
            "display_name",
            "review_status",
        }
    ),
    "connection_status_events": frozenset({"event_id", "tenant_id", "workspace_id", "connection_id", "status"}),
    "detail_open_ledger": frozenset(
        {
            "ledger_id",
            "tenant_id",
            "workspace_id",
            "actor_id",
            "connection_id",
            "source_run_id",
            "request_id",
            "candidate_evidence_id",
            "status",
            "budget_day",
        }
    ),
    "detail_open_requests": frozenset(
        {
            "request_id",
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "source_run_id",
            "connection_id",
            "candidate_evidence_id",
            "review_item_id",
            "detail_open_mode",
            "status",
            "ledger_id",
        }
    ),
    "security_audit_events": frozenset(
        {
            "audit_id",
            "tenant_id",
            "workspace_id",
            "target_type",
            "action",
            "result",
            "metadata_redacted_json",
            "created_at",
        }
    ),
    "session_requirement_triage": frozenset(
        {
            "session_id",
            "tenant_id",
            "workspace_id",
            "user_id",
            "status",
            "must_haves_json",
            "nice_to_haves_json",
            "synonyms_json",
            "seniority_filters_json",
            "exclusions_json",
            "generated_query_hints_json",
        }
    ),
    "users": frozenset({"user_id", "email", "display_name", "password_hash", "disabled_at", "created_at"}),
    "sessions": frozenset({"session_id", "tenant_id", "workspace_id", "user_id", "job_title", "jd_text", "status"}),
    "source_runs": frozenset(
        {
            "source_run_id",
            "session_id",
            "tenant_id",
            "workspace_id",
            "user_id",
            "source_kind",
            "status",
            "auth_state",
            "runtime_run_id",
            "cards_scanned_count",
            "unique_candidates_count",
            "detail_open_used_count",
            "detail_open_blocked_count",
        }
    ),
    "session_events": frozenset(
        {
            "global_seq",
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "session_seq",
            "event_name",
            "schema_version",
            "idempotency_key",
            "payload_redacted_json",
            "occurred_at",
            "created_at",
        }
    ),
    "runtime_source_lane_latest_state": frozenset(
        {
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "source_run_id",
            "source_kind",
            "source_lane_run_id",
            "attempt",
            "event_seq",
            "event_type",
            "status",
            "payload_json",
            "updated_at",
        }
    ),
    "runtime_sourcing_jobs": frozenset(
        {
            "job_id",
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "status",
            "source_kinds_json",
            "runtime_run_id",
            "lease_owner",
            "lease_expires_at",
            "idempotency_key",
            "attempt_count",
            "error_message",
            "created_at",
            "updated_at",
        }
    ),
    "runtime_finalization_revisions": frozenset(
        {
            "session_id",
            "runtime_run_id",
            "revision",
            "reason_code",
            "ordered_candidate_identity_ids_json",
            "coverage_summary_json",
            "created_at",
        }
    ),
    "runtime_candidate_identity_snapshots": frozenset(
        {
            "session_id",
            "runtime_run_id",
            "identity_id",
            "canonical_resume_id",
            "merged_resume_ids_json",
            "source_evidence_ids_json",
            "created_at",
        }
    ),
    "source_connections": frozenset(
        {
            "connection_id",
            "tenant_id",
            "workspace_id",
            "user_id",
            "source_kind",
            "status",
            "provider_account_hash",
        }
    ),
    "source_run_policies": frozenset({"session_id", "tenant_id", "workspace_id", "user_id", "source_kind", "detail_open_mode"}),
    "workbench_note_writer_leases": frozenset(
        {
            "tenant_id",
            "workspace_id",
            "user_id",
            "session_id",
            "lease_owner",
            "lease_expires_at",
            "last_tick_slot",
            "in_flight_started_at",
            "created_at",
            "updated_at",
        }
    ),
    "workspace_memberships": frozenset({"workspace_id", "user_id", "role", "created_at"}),
}
WORKBENCH_REQUIRED_INDEXES = frozenset(
    {
        "idx_candidate_evidence_source",
        "idx_candidate_review_items_session",
        "idx_connection_status_events_connection",
        "idx_detail_open_ledger_active",
        "idx_detail_open_ledger_budget",
        "idx_detail_open_ledger_idempotency",
        "idx_detail_open_ledger_one_active_lease",
        "idx_detail_open_requests_idempotency",
        "idx_detail_open_requests_queue",
        "idx_external_write_intents_idempotency",
        "idx_external_write_intents_pending",
        "idx_login_attempts_email_created",
        "idx_security_audit_events_action",
        "idx_security_audit_events_scope",
        "idx_session_events_global",
        "idx_session_events_runtime_public_idempotency",
        "idx_session_events_runtime_source_lane_idempotency",
        "idx_session_events_session",
        "idx_session_events_workbench_note_idempotency",
        "idx_runtime_source_lane_latest_session",
        "idx_runtime_candidate_identity_snapshots_session",
        "idx_runtime_finalization_revisions_latest",
        "idx_runtime_sourcing_jobs_claim",
        "idx_runtime_sourcing_jobs_session_status",
        "idx_sessions_owner",
        "idx_sessions_user_updated",
        "idx_sessions_workspace_updated",
        "idx_source_connections_scope",
        "idx_source_connections_user_source",
        "idx_source_run_jobs_claim",
        "idx_source_run_jobs_source_status",
        "idx_source_run_policies_scope",
        "idx_source_runs_session",
        "idx_source_runs_source_card",
        "idx_source_runs_status",
        "idx_user_sessions_user_workspace",
        "idx_workbench_note_writer_leases_expires",
    }
)
WORKBENCH_REQUIRED_TABLE_FRAGMENTS = {
    "users": ("email TEXT NOT NULL UNIQUE COLLATE NOCASE",),
}


@dataclass(frozen=True)
class BackupResult:
    database_path: Path
    metadata_path: Path
    integrity_check: str


@dataclass(frozen=True)
class RestoreResult:
    database_path: Path
    integrity_check: str


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    message: str
    evidence: dict[str, object]


@dataclass(frozen=True)
class RolloutReadinessResult:
    status: str
    report_path: Path
    markdown_path: Path
    checks: tuple[ReadinessCheck, ...]


class MaintenanceError(RuntimeError):
    pass


def backup_workbench(*, workspace_root: Path) -> BackupResult:
    workspace_root = workspace_root.resolve()
    source_path = _workbench_db_path(workspace_root)
    if not source_path.exists():
        raise MaintenanceError(f"workbench database does not exist: {source_path}")
    _validate_workbench_schema(source_path)

    backups_dir = workspace_root / ".seektalent" / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.chmod(0o700)

    backup_path = _next_backup_path(backups_dir)
    _sqlite_backup(source_path=source_path, backup_path=backup_path)
    backup_path.chmod(0o600)

    integrity_check = _integrity_check(backup_path)
    schema = _canonical_schema_signature()
    metadata_path = backup_path.with_suffix(".json")
    metadata = {
        "app_version": _package_version(),
        "backup_database_name": backup_path.name,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": "seektalent-ui-maintenance",
        "source_database": str(source_path),
        "backup_database": str(backup_path),
        "git_commit": _git_commit(),
        "integrity_check": integrity_check,
        "metadata_schema": BACKUP_METADATA_SCHEMA,
        "retention_policy": RETENTION_POLICY,
        "workbench_required_checks": {
            table: list(checks)
            for table, checks in cast(dict[str, tuple[str, ...]], schema["checks"]).items()
            if checks
        },
        "workbench_required_columns": {
            table: sorted(columns)
            for table, columns in cast(dict[str, dict[str, ColumnSignature]], schema["columns"]).items()
        },
        "workbench_required_foreign_keys": {
            table: [list(item) for item in foreign_keys]
            for table, foreign_keys in cast(dict[str, tuple[ForeignKeySignature, ...]], schema["foreign_keys"]).items()
            if foreign_keys
        },
        "workbench_required_indexes": sorted(cast(dict[str, str], schema["indexes"])),
        "workbench_required_table_fragments": WORKBENCH_REQUIRED_TABLE_FRAGMENTS,
        "workbench_required_tables": sorted(cast(dict[str, str], schema["tables"])),
        "excluded_data": EXCLUDED_DATA,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata_path.chmod(0o600)
    _record_maintenance_audit(
        source_path,
        action="workbench_backup_created",
        result="success",
        target_id=backup_path.name,
        metadata={"backupPath": str(backup_path), "metadataPath": str(metadata_path), "excludedData": EXCLUDED_DATA},
    )

    return BackupResult(database_path=backup_path, metadata_path=metadata_path, integrity_check=integrity_check)


def verify_backup(backup_path: Path) -> str:
    backup_path = backup_path.resolve()
    if not backup_path.exists():
        raise MaintenanceError(f"backup database does not exist: {backup_path}")

    integrity_check = _integrity_check(backup_path)
    _validate_backup_metadata(backup_path)
    _validate_workbench_schema(backup_path)
    return integrity_check


def restore_workbench(*, backup_path: Path, workspace_root: Path, yes: bool) -> RestoreResult:
    if not yes:
        raise MaintenanceError("restore requires --yes")

    integrity_check = verify_backup(backup_path)
    workspace_root = workspace_root.resolve()
    target_path = _workbench_db_path(workspace_root)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.parent.chmod(0o700)

    _restore_database_file(
        backup_path=backup_path.resolve(),
        target_path=target_path,
        audit_target_id=backup_path.name,
        audit_metadata={"backupPath": str(backup_path), "restoredPath": str(target_path), "excludedData": EXCLUDED_DATA},
    )

    return RestoreResult(database_path=target_path, integrity_check=integrity_check)


def run_rollout_readiness(*, workspace_root: Path, output_dir: Path | None = None) -> RolloutReadinessResult:
    workspace_root = workspace_root.resolve()
    database_path = _workbench_db_path(workspace_root)
    if not database_path.exists():
        raise MaintenanceError(f"workbench database does not exist: {database_path}")

    checks: list[ReadinessCheck] = []

    _validate_workbench_schema(database_path)
    checks.append(
        ReadinessCheck(
            name="workbench_schema",
            status="pass",
            message="Workbench SQLite schema matches the expected productized schema.",
            evidence={"database": "present", "schema": "validated"},
        )
    )

    backup = backup_workbench(workspace_root=workspace_root)
    checks.append(
        ReadinessCheck(
            name="workbench_backup",
            status="pass",
            message="Consistent SQLite backup was created with maintenance metadata.",
            evidence={"backup_database_name": backup.database_path.name, "integrity_check": backup.integrity_check},
        )
    )

    verified_integrity = verify_backup(backup.database_path)
    checks.append(
        ReadinessCheck(
            name="backup_verify",
            status="pass",
            message="Backup metadata, schema, and SQLite integrity checks passed.",
            evidence={"backup_database_name": backup.database_path.name, "integrity_check": verified_integrity},
        )
    )

    with TemporaryDirectory(prefix="seektalent-rollout-readiness-") as temp_dir:
        restore_root = Path(temp_dir) / "restore"
        restored = restore_workbench(backup_path=backup.database_path, workspace_root=restore_root, yes=True)
        checks.append(
            ReadinessCheck(
                name="restore_to_temp",
                status="pass",
                message="Backup restored into an isolated temporary workspace.",
                evidence={"integrity_check": restored.integrity_check, "target": "temporary_workspace"},
            )
        )
        smoke_evidence = _read_path_smoke(restored.database_path)
        checks.append(
            ReadinessCheck(
                name="workbench_read_path_smoke",
                status="pass",
                message="Restored workbench database supports expected read paths.",
                evidence=smoke_evidence,
            )
        )

    checks.extend(_manual_rollout_checks())
    status = "manual_required" if any(check.status == "manual_required" for check in checks) else "pass"
    report_dir = _rollout_readiness_dir(workspace_root=workspace_root, output_dir=output_dir)
    report_path, markdown_path = _write_rollout_readiness_reports(
        report_dir=report_dir,
        status=status,
        checks=tuple(checks),
    )
    return RolloutReadinessResult(
        status=status,
        report_path=report_path,
        markdown_path=markdown_path,
        checks=tuple(checks),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            result = backup_workbench(workspace_root=args.workspace_root)
            print(f"backup: {result.database_path}")
            print(f"metadata: {result.metadata_path}")
            print(f"integrity_check: {result.integrity_check}")
            return 0
        if args.command == "verify-backup":
            integrity_check = verify_backup(args.backup_path)
            print(f"integrity_check: {integrity_check}")
            return 0
        if args.command == "restore":
            result = restore_workbench(backup_path=args.backup_path, workspace_root=args.workspace_root, yes=args.yes)
            print(f"restored: {result.database_path}")
            print(f"integrity_check: {result.integrity_check}")
            return 0
        if args.command == "rollout-readiness":
            result = run_rollout_readiness(workspace_root=args.workspace_root, output_dir=args.output_dir)
            print(f"status: {result.status}")
            print(f"report: {result.report_path}")
            print(f"markdown: {result.markdown_path}")
            return 1 if result.status == "fail" else 0
    except MaintenanceError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain the local SeekTalent workbench SQLite database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Create a consistent SQLite backup of the workbench database.")
    backup.add_argument("--workspace-root", type=Path, default=Path.cwd())

    verify = subparsers.add_parser("verify-backup", help="Run PRAGMA integrity_check on a backup database.")
    verify.add_argument("backup_path", type=Path)

    restore = subparsers.add_parser("restore", help="Restore a verified backup into the workspace workbench database.")
    restore.add_argument("backup_path", type=Path)
    restore.add_argument("--workspace-root", type=Path, default=Path.cwd())
    restore.add_argument("--yes", action="store_true", help="Confirm replacement of the target workbench database.")

    readiness = subparsers.add_parser(
        "rollout-readiness",
        help="Run local workbench rollout readiness checks and write redacted evidence.",
    )
    readiness.add_argument("--workspace-root", type=Path, default=Path.cwd())
    readiness.add_argument("--output-dir", type=Path, default=None)

    return parser


def _workbench_db_path(workspace_root: Path) -> Path:
    return workspace_root / ".seektalent" / "workbench.sqlite3"


def _next_backup_path(backups_dir: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return backups_dir / f"workbench-{timestamp}.sqlite3"


def _sqlite_backup(*, source_path: Path, backup_path: Path) -> None:
    with (
        sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source,
        sqlite3.connect(backup_path) as backup,
    ):
        source.backup(backup)


def _integrity_check(database_path: Path) -> str:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as conn:
        rows = conn.execute("PRAGMA integrity_check").fetchall()

    messages = [row[0] for row in rows]
    if messages != ["ok"]:
        raise MaintenanceError(f"backup integrity check failed: {'; '.join(messages)}")
    return "ok"


def _validate_backup_metadata(backup_path: Path) -> dict[str, object]:
    canonical = _canonical_schema_signature()
    canonical_checks = cast(dict[str, tuple[str, ...]], canonical["checks"])
    canonical_columns = cast(dict[str, dict[str, ColumnSignature]], canonical["columns"])
    canonical_foreign_keys = cast(dict[str, tuple[ForeignKeySignature, ...]], canonical["foreign_keys"])
    canonical_indexes = cast(dict[str, str], canonical["indexes"])
    canonical_tables = cast(dict[str, str], canonical["tables"])
    metadata_path = backup_path.with_suffix(".json")
    if not metadata_path.exists():
        raise MaintenanceError(f"backup metadata does not exist: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MaintenanceError(f"backup metadata is invalid JSON: {metadata_path}") from exc
    if not isinstance(metadata, dict):
        raise MaintenanceError("backup metadata must be a JSON object")
    if metadata.get("metadata_schema") != BACKUP_METADATA_SCHEMA:
        raise MaintenanceError("backup metadata schema is not supported")
    if metadata.get("backup_database_name") != backup_path.name:
        raise MaintenanceError("backup metadata does not match backup filename")
    if Path(str(metadata.get("backup_database", ""))).name != backup_path.name:
        raise MaintenanceError("backup metadata backup_database does not match backup filename")
    excluded = metadata.get("excluded_data")
    if not isinstance(excluded, list) or not set(EXCLUDED_DATA).issubset({str(item) for item in excluded}):
        raise MaintenanceError("backup metadata does not record required exclusions")
    if metadata.get("retention_policy") != RETENTION_POLICY:
        raise MaintenanceError("backup metadata does not record supported retention policy")
    if not isinstance(metadata.get("app_version"), str):
        raise MaintenanceError("backup metadata does not record app version")
    if "git_commit" not in metadata:
        raise MaintenanceError("backup metadata does not record git commit")
    tables = metadata.get("workbench_required_tables")
    if not isinstance(tables, list) or not set(canonical_tables).issubset({str(item) for item in tables}):
        raise MaintenanceError("backup metadata does not record workbench schema tables")
    columns = metadata.get("workbench_required_columns")
    if not isinstance(columns, dict):
        raise MaintenanceError("backup metadata does not record workbench schema columns")
    for table, required_columns in canonical_columns.items():
        table_columns = columns.get(table)
        if not isinstance(table_columns, list) or not set(required_columns).issubset({str(item) for item in table_columns}):
            raise MaintenanceError(f"backup metadata does not record workbench columns for {table}")
    checks = metadata.get("workbench_required_checks")
    if not isinstance(checks, dict):
        raise MaintenanceError("backup metadata does not record workbench schema checks")
    for table, required_checks in canonical_checks.items():
        if not required_checks:
            continue
        table_checks = checks.get(table)
        if not isinstance(table_checks, list) or not set(required_checks).issubset({str(item) for item in table_checks}):
            raise MaintenanceError(f"backup metadata does not record workbench checks for {table}")
    foreign_keys = metadata.get("workbench_required_foreign_keys")
    if not isinstance(foreign_keys, dict):
        raise MaintenanceError("backup metadata does not record workbench schema foreign keys")
    for table, required_foreign_keys in canonical_foreign_keys.items():
        if not required_foreign_keys:
            continue
        table_foreign_keys = foreign_keys.get(table)
        if not isinstance(table_foreign_keys, list) or len(table_foreign_keys) < len(required_foreign_keys):
            raise MaintenanceError(f"backup metadata does not record workbench foreign keys for {table}")
    indexes = metadata.get("workbench_required_indexes")
    if not isinstance(indexes, list) or not set(canonical_indexes).issubset({str(item) for item in indexes}):
        raise MaintenanceError("backup metadata does not record workbench schema indexes")
    table_fragments = metadata.get("workbench_required_table_fragments")
    if not isinstance(table_fragments, dict):
        raise MaintenanceError("backup metadata does not record workbench schema table fragments")
    for table, required_fragments in WORKBENCH_REQUIRED_TABLE_FRAGMENTS.items():
        fragments = table_fragments.get(table)
        if not isinstance(fragments, list) or not set(required_fragments).issubset({str(item) for item in fragments}):
            raise MaintenanceError(f"backup metadata does not record workbench table fragments for {table}")
    return metadata


def _validate_workbench_schema(database_path: Path) -> None:
    canonical = _canonical_schema_signature()
    canonical_checks = cast(dict[str, tuple[str, ...]], canonical["checks"])
    canonical_columns = cast(dict[str, dict[str, ColumnSignature]], canonical["columns"])
    canonical_foreign_keys = cast(dict[str, tuple[ForeignKeySignature, ...]], canonical["foreign_keys"])
    canonical_indexes = cast(dict[str, str], canonical["indexes"])
    canonical_tables = cast(dict[str, str], canonical["tables"])
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as conn:
        extra_executable_objects = conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')"
        ).fetchall()
        if extra_executable_objects:
            names = ", ".join(f"{row[0]}:{row[1]}" for row in extra_executable_objects)
            raise MaintenanceError(f"backup is not a workbench database; executable schema objects are not allowed: {names}")
        table_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_sql_by_name = {str(row[0]): _normalize_schema_sql(row[1]) for row in table_rows}
        missing = sorted(set(canonical_tables) - set(table_sql_by_name))
        if missing:
            raise MaintenanceError(f"backup is not a workbench database; missing tables: {', '.join(missing)}")
        for table, canonical_table_columns in canonical_columns.items():
            column_signature = _table_column_signature(conn, table)
            column_names = set(column_signature)
            missing_columns = sorted(set(canonical_table_columns) - column_names)
            if missing_columns:
                raise MaintenanceError(
                    f"backup is not a workbench database; missing columns in {table}: {', '.join(missing_columns)}"
                )
            for column, expected_signature in canonical_table_columns.items():
                if column_signature[column] != expected_signature:
                    raise MaintenanceError(f"backup is not a workbench database; schema mismatch in {table}.{column}")
            actual_foreign_keys = _table_foreign_key_signature(conn, table)
            if actual_foreign_keys != canonical_foreign_keys[table]:
                raise MaintenanceError(f"backup is not a workbench database; foreign key schema mismatch in {table}")
            actual_checks = _check_constraint_fragments(table_sql_by_name[table])
            missing_checks = sorted(set(canonical_checks[table]) - set(actual_checks))
            if missing_checks:
                raise MaintenanceError(f"backup is not a workbench database; missing check constraints in {table}")
            missing_fragments = [
                fragment
                for fragment in WORKBENCH_REQUIRED_TABLE_FRAGMENTS.get(table, ())
                if not _table_has_required_column_definition(table_sql_by_name[table], fragment)
            ]
            if missing_fragments:
                raise MaintenanceError(f"backup is not a workbench database; missing table schema fragments in {table}")
        index_rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL").fetchall()
        index_sql_by_name = {str(row[0]): _normalize_schema_sql(row[1]) for row in index_rows}
        missing_indexes = sorted(set(canonical_indexes) - set(index_sql_by_name))
        if missing_indexes:
            raise MaintenanceError(f"backup is not a workbench database; missing indexes: {', '.join(missing_indexes)}")
        for index_name, expected_index_sql in canonical_indexes.items():
            if index_sql_by_name[index_name] != expected_index_sql:
                raise MaintenanceError(f"backup is not a workbench database; index schema mismatch in {index_name}")
        foreign_key_failures = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_failures:
            raise MaintenanceError("backup is not a workbench database; foreign key check failed")
        _smoke_workbench_schema(conn)


def _canonical_schema_signature() -> dict[str, SchemaSignature]:
    with TemporaryDirectory(prefix="seektalent-workbench-schema-") as temp_dir:
        canonical_path = Path(temp_dir) / "workbench.sqlite3"
        WorkbenchStore(canonical_path).list_security_audit_events()
        with sqlite3.connect(canonical_path) as conn:
            table_rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            index_rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            ).fetchall()
            return {
                "checks": {
                    table: _check_constraint_fragments(sql)
                    for table, sql in ((str(row[0]), _normalize_schema_sql(row[1])) for row in table_rows)
                },
                "columns": {
                    table: _table_column_signature(conn, table)
                    for table in sorted(str(row[0]) for row in table_rows)
                },
                "foreign_keys": {
                    table: _table_foreign_key_signature(conn, table)
                    for table in sorted(str(row[0]) for row in table_rows)
                },
                "indexes": {str(row[0]): _normalize_schema_sql(row[1]) for row in index_rows},
                "tables": {str(row[0]): _normalize_schema_sql(row[1]) for row in table_rows},
            }


def _table_column_signature(conn: sqlite3.Connection, table: str) -> dict[str, ColumnSignature]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {
        str(row[1]): (str(row[2]).upper(), int(row[3]), row[4] if row[4] is None else str(row[4]), int(row[5]))
        for row in rows
    }


def _table_foreign_key_signature(conn: sqlite3.Connection, table: str) -> tuple[ForeignKeySignature, ...]:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    return tuple(
        sorted(
            (
                int(row[0]),
                int(row[1]),
                str(row[2]),
                str(row[3]),
                row[4] if row[4] is None else str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
            )
            for row in rows
        )
    )


def _check_constraint_fragments(sql: str) -> tuple[str, ...]:
    fragments: list[str] = []
    index = 0
    while True:
        start = sql.find("CHECK", index)
        if start == -1:
            break
        open_index = sql.find("(", start)
        if open_index == -1:
            break
        end = _matching_paren(sql, open_index)
        if end == -1:
            break
        fragments.append(_normalize_schema_sql(sql[start : end + 1]))
        index = end + 1
    return tuple(fragments)


def _table_has_required_column_definition(table_sql: str, required_definition: str) -> bool:
    required_name, required_normalized = _column_definition_signature(required_definition)
    column_definitions = _column_definition_fragments(table_sql)
    return column_definitions.get(required_name) == required_normalized


def _column_definition_fragments(sql: str) -> dict[str, str]:
    open_index = sql.find("(")
    if open_index == -1:
        return {}
    close_index = _matching_paren(sql, open_index)
    if close_index == -1:
        return {}
    definitions: dict[str, str] = {}
    for item in _split_top_level_csv(sql[open_index + 1 : close_index]):
        signature = _maybe_column_definition_signature(item)
        if signature is None:
            continue
        name, normalized_definition = signature
        definitions[name] = normalized_definition
    return definitions


def _column_definition_signature(definition: str) -> tuple[str, str]:
    signature = _maybe_column_definition_signature(definition)
    if signature is None:
        raise MaintenanceError(f"invalid required column schema fragment: {definition}")
    return signature


def _maybe_column_definition_signature(definition: str) -> tuple[str, str] | None:
    stripped = definition.strip()
    if not stripped:
        return None
    first_token, rest = _read_sql_identifier(stripped)
    if not first_token:
        return None
    if _unquote_sql_identifier(first_token).upper() in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
        return None
    name = _unquote_sql_identifier(first_token)
    normalized_rest = _normalize_schema_sql(rest)
    normalized_definition = name if not normalized_rest else f"{name} {normalized_rest}"
    return name, normalized_definition


def _split_top_level_csv(value: str) -> list[str]:
    items: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    index = 0
    while index < len(value):
        character = value[index]
        if quote is not None:
            if character == quote:
                if quote == "'" and index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1
        index += 1
    tail = value[start:].strip()
    if tail:
        items.append(tail)
    return items


def _read_sql_identifier(value: str) -> tuple[str, str]:
    stripped = value.lstrip()
    if not stripped:
        return "", ""
    if stripped[0] == '"':
        index = 1
        while index < len(stripped):
            if stripped[index] == '"':
                if index + 1 < len(stripped) and stripped[index + 1] == '"':
                    index += 2
                    continue
                return stripped[: index + 1], stripped[index + 1 :].strip()
            index += 1
        return stripped, ""
    parts = stripped.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _unquote_sql_identifier(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('""', '"')
    return value


def _matching_paren(value: str, open_index: int) -> int:
    depth = 0
    quote: str | None = None
    index = open_index
    while index < len(value):
        character = value[index]
        if quote is not None:
            if character == quote:
                if quote == "'" and index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _normalize_schema_sql(value: object) -> str:
    return " ".join(str(value).split())


def _smoke_workbench_schema(conn: sqlite3.Connection) -> None:
    smoke_queries = (
        """
        SELECT u.user_id, u.email, u.display_name, u.disabled_at, m.workspace_id, m.role
        FROM users AS u
        JOIN workspace_memberships AS m ON m.user_id = u.user_id
        LIMIT 1
        """,
        """
        SELECT s.session_id, sr.source_run_id, triage.status
        FROM sessions AS s
        LEFT JOIN source_runs AS sr ON sr.session_id = s.session_id
        LEFT JOIN session_requirement_triage AS triage ON triage.session_id = s.session_id
        LIMIT 1
        """,
        """
        SELECT cri.review_item_id, ce.evidence_id, dor.request_id, dol.ledger_id
        FROM candidate_review_items AS cri
        LEFT JOIN candidate_evidence AS ce ON ce.review_item_id = cri.review_item_id
        LEFT JOIN detail_open_requests AS dor ON dor.review_item_id = cri.review_item_id
        LEFT JOIN detail_open_ledger AS dol ON dol.request_id = dor.request_id
        LIMIT 1
        """,
        """
        SELECT audit_id, action, result, metadata_redacted_json
        FROM security_audit_events
        LIMIT 1
        """,
    )
    try:
        for query in smoke_queries:
            conn.execute(query).fetchone()
    except sqlite3.DatabaseError as exc:
        raise MaintenanceError(f"backup workbench schema smoke check failed: {exc}") from exc


def _read_path_smoke(database_path: Path) -> dict[str, object]:
    store = WorkbenchStore(database_path)
    audit_events = store.list_security_audit_events()
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as conn:
        session_count = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        review_item_count = int(conn.execute("SELECT COUNT(*) FROM candidate_review_items").fetchone()[0])
        detail_request_count = int(conn.execute("SELECT COUNT(*) FROM detail_open_requests").fetchone()[0])
    return {
        "security_audit_event_count": len(audit_events),
        "session_count": session_count,
        "candidate_review_item_count": review_item_count,
        "detail_open_request_count": detail_request_count,
    }


def _manual_rollout_checks() -> tuple[ReadinessCheck, ...]:
    return (
        ReadinessCheck(
            name="real_device_lan_access",
            status="manual_required",
            message="Confirm a real LAN device can reach the operator workbench before business use.",
            evidence={
                "why_manual": "Requires a physical device and operator network context.",
                "operator_guidance": "Open the workbench from a real LAN device and confirm login plus main workbench navigation.",
            },
        ),
        ReadinessCheck(
            name="real_liepin_login_relay",
            status="manual_required",
            message="Confirm the real Liepin login relay with an operator account before business use.",
            evidence={
                "why_manual": "Requires an approved real provider account and cannot be simulated safely.",
                "operator_guidance": "Use the approved operator account to complete the Liepin login relay and verify connected status.",
            },
        ),
        ReadinessCheck(
            name="provider_budget_detail_behavior",
            status="manual_required",
            message="Confirm provider budget and detail-open behavior with an approved real account.",
            evidence={
                "why_manual": "May consume provider account budget and must remain a human gate.",
                "operator_guidance": "Verify detail-open prompts, budget limits, and blocked/allowed outcomes with approved test candidates.",
            },
        ),
    )


def _rollout_readiness_dir(*, workspace_root: Path, output_dir: Path | None) -> Path:
    report_dir = output_dir.resolve() if output_dir is not None else workspace_root / ".seektalent" / "rollout-readiness"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_dir.chmod(0o700)
    return report_dir


def _write_rollout_readiness_reports(
    *,
    report_dir: Path,
    status: str,
    checks: tuple[ReadinessCheck, ...],
) -> tuple[Path, Path]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    report_path = report_dir / f"rollout-readiness-{timestamp}.json"
    markdown_path = report_dir / f"rollout-readiness-{timestamp}.md"
    payload = _rollout_readiness_payload(status=status, checks=checks)
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.chmod(0o600)
    markdown_path.write_text(_rollout_readiness_markdown(payload), encoding="utf-8")
    markdown_path.chmod(0o600)
    return report_path, markdown_path


def _rollout_readiness_payload(
    *,
    status: str,
    checks: tuple[ReadinessCheck, ...],
) -> dict[str, object]:
    return {
        "metadata_schema": ROLLOUT_READINESS_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": "seektalent-ui-maintenance",
        "app_version": _package_version(),
        "git_commit": _git_commit(),
        "workspace_root": "provided",
        "status": status,
        "command": "uv run seektalent-ui-maintenance rollout-readiness --workspace-root .",
        "manual_gates": [
            gate
            for gate in ROLLOUT_MANUAL_GATES
            if any(check.name == gate and check.status == "manual_required" for check in checks)
        ],
        "redaction_policy": {
            "contains": "local readiness check names, counts, filenames, and operator guidance only",
            "excludes_sensitive_runtime_material": True,
        },
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "message": check.message,
                "evidence": check.evidence,
            }
            for check in checks
        ],
    }


def _rollout_readiness_markdown(payload: dict[str, object]) -> str:
    checks = cast(list[dict[str, object]], payload["checks"])
    lines = [
        "# Workbench Rollout Readiness",
        "",
        f"- Status: `{payload['status']}`",
        f"- Created at: `{payload['created_at']}`",
        f"- Command: `{payload['command']}`",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        lines.extend(
            [
                f"- `{check['name']}`: `{check['status']}`",
                f"  - {check['message']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Manual Gates",
            "",
            "- Real-device LAN access must be performed by an operator.",
            "- Real Liepin login relay must be performed by an approved operator.",
            "- Provider budget and detail-open behavior must be confirmed before business use.",
            "",
            "## Redaction",
            "",
            "This evidence records readiness status, counts, filenames, and operator guidance only.",
            "It must not include sensitive browser session material, provider payloads, resume bodies, or personal candidate data.",
            "",
        ]
    )
    return "\n".join(lines)


def _sqlite_database_files(database_path: Path) -> list[Path]:
    return [
        database_path,
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
    ]


def _restore_database_file(
    *,
    backup_path: Path,
    target_path: Path,
    audit_target_id: str,
    audit_metadata: dict[str, object],
) -> None:
    temp_path = target_path.with_name(f"{target_path.name}.restore-{os.getpid()}.tmp")
    temp_path.unlink(missing_ok=True)
    quarantined: list[tuple[Path, Path]] = []
    restored = False
    try:
        _sqlite_backup(source_path=backup_path, backup_path=temp_path)
        temp_path.chmod(0o600)
        _integrity_check(temp_path)
        _validate_workbench_schema(temp_path)
        quarantined = _quarantine_existing_sqlite_files(target_path)
        try:
            temp_path.replace(target_path)
            target_path.chmod(0o600)
            _record_maintenance_audit(
                target_path,
                action="workbench_backup_restored",
                result="success",
                target_id=audit_target_id,
                metadata=audit_metadata,
            )
        except Exception as exc:
            _restore_quarantined_sqlite_files(target_path, quarantined)
            raise MaintenanceError("restore failed; original database restored") from exc
        restored = True
    finally:
        temp_path.unlink(missing_ok=True)
        if restored:
            _delete_quarantined_sqlite_files(quarantined)


def _quarantine_existing_sqlite_files(database_path: Path) -> list[tuple[Path, Path]]:
    quarantined: list[tuple[Path, Path]] = []
    suffix = f"restore-{os.getpid()}.old"
    try:
        for path in _sqlite_database_files(database_path):
            if path.exists():
                quarantine_path = path.with_name(f"{path.name}.{suffix}")
                quarantine_path.unlink(missing_ok=True)
                path.replace(quarantine_path)
                quarantined.append((path, quarantine_path))
    except Exception:
        _restore_quarantined_sqlite_files(database_path, quarantined)
        raise
    return quarantined


def _restore_quarantined_sqlite_files(database_path: Path, quarantined: list[tuple[Path, Path]]) -> None:
    for path in _sqlite_database_files(database_path):
        path.unlink(missing_ok=True)
    for original_path, quarantine_path in quarantined:
        if quarantine_path.exists():
            quarantine_path.replace(original_path)


def _delete_quarantined_sqlite_files(quarantined: list[tuple[Path, Path]]) -> None:
    for _, quarantine_path in quarantined:
        quarantine_path.unlink(missing_ok=True)


def _package_version() -> str:
    try:
        return importlib_metadata.version("seektalent")
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _record_maintenance_audit(
    database_path: Path,
    *,
    action: str,
    result: str,
    target_id: str,
    metadata: dict[str, object],
) -> None:
    WorkbenchStore(database_path).record_security_audit_event(
        actor_user_id=None,
        actor_role="system",
        workspace_id=DEFAULT_WORKSPACE_ID,
        target_type="workbench_database",
        target_id=target_id,
        action=action,
        result=result,
        reason_code="maintenance_cli",
        metadata=metadata,
    )


if __name__ == "__main__":
    raise SystemExit(main())
