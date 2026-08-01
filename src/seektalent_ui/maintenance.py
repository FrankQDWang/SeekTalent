"""Maintenance commands for the active runtime, Liepin, and corpus stores."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence

from seektalent.config import AppSettings
from seektalent.backup_group import DatabaseGroupBackupResult, backup_product_database_group
from seektalent.local_storage_lifecycle import (
    LocalStorageCleanupResult,
    LocalStorageLifecyclePolicy,
    cleanup_local_storage,
)
from seektalent.operator_health import OperatorHealthReport, build_operator_health_report
from seektalent.support_bundle import create_execution_support_bundle
from seektalent_runtime_control.retention import (
    RuntimeControlRetentionPolicy,
    RuntimeRetentionResult,
    RuntimeRetentionService,
)
from seektalent_runtime_control.store import RuntimeControlStore


class MaintenanceError(RuntimeError):
    pass


def run_runtime_control_retention(
    *,
    runtime_control_db_path: Path,
    apply: bool = False,
    now: Callable[[], str] | None = None,
    terminal_run_min_age_days: int = 30,
    developer_event_ttl_days: int = 14,
    internal_event_ttl_days: int = 30,
    checkpoint_ttl_days: int = 30,
    lease_ttl_days: int = 7,
    command_ttl_days: int = 30,
    non_required_stage_output_ttl_days: int = 30,
    batch_size: int = 500,
    database_budget_bytes: int | None = None,
) -> RuntimeRetentionResult:
    store = RuntimeControlStore(runtime_control_db_path)
    store.initialize()
    policy = RuntimeControlRetentionPolicy(
        terminal_run_min_age_days=terminal_run_min_age_days,
        developer_event_ttl_days=developer_event_ttl_days,
        internal_event_ttl_days=internal_event_ttl_days,
        checkpoint_ttl_days=checkpoint_ttl_days,
        lease_ttl_days=lease_ttl_days,
        command_ttl_days=command_ttl_days,
        non_required_stage_output_ttl_days=non_required_stage_output_ttl_days,
        batch_size=batch_size,
        database_budget_bytes=database_budget_bytes,
    )
    return RuntimeRetentionService(store=store, now=now, policy=policy).cleanup(dry_run=not apply)


def run_local_storage_cleanup(
    *,
    workspace_root: Path,
    runtime_mode: str = "dev",
    artifacts_dir: str | None = None,
    llm_cache_dir: str | None = None,
    apply: bool = False,
    now: datetime | None = None,
    debug_retention_days: int = 7,
    cache_retention_days: int = 7,
    support_bundle_retention_days: int = 7,
    backup_retention_days: int = 30,
    max_backup_count: int = 10,
    max_backup_total_bytes: int = 2_000_000_000,
) -> LocalStorageCleanupResult:
    current_time = now or datetime.now(UTC)
    settings = AppSettings(
        _env_file=None,
        runtime_mode=runtime_mode,
        workspace_root=str(workspace_root),
        artifacts_dir=artifacts_dir,
        llm_cache_dir=llm_cache_dir,
    )
    policy = LocalStorageLifecyclePolicy(
        debug_retention_days=debug_retention_days,
        cache_retention_days=cache_retention_days,
        support_bundle_retention_days=support_bundle_retention_days,
        backup_retention_days=backup_retention_days,
        max_backup_count=max_backup_count,
        max_backup_total_bytes=max_backup_total_bytes,
    )
    return cleanup_local_storage(settings, now=current_time, policy=policy, apply=apply)


def run_database_group_backup(
    *,
    workspace_root: Path,
    runtime_mode: str = "prod",
    artifacts_dir: str | None = None,
    llm_cache_dir: str | None = None,
    backup_root: Path | None = None,
    now: datetime | None = None,
) -> DatabaseGroupBackupResult:
    settings = AppSettings(
        _env_file=None,
        runtime_mode=runtime_mode,
        workspace_root=str(workspace_root),
        artifacts_dir=artifacts_dir,
        llm_cache_dir=llm_cache_dir,
    )
    return backup_product_database_group(settings, backup_root=backup_root, now=now)


def run_operator_health(
    *,
    workspace_root: Path,
    runtime_mode: str = "prod",
    artifacts_dir: str | None = None,
    llm_cache_dir: str | None = None,
    required_free_bytes: int = 1_000_000_000,
) -> OperatorHealthReport:
    settings = AppSettings(
        _env_file=None,
        runtime_mode=runtime_mode,
        workspace_root=str(workspace_root),
        artifacts_dir=artifacts_dir,
        llm_cache_dir=llm_cache_dir,
    )
    return build_operator_health_report(settings, required_free_bytes=required_free_bytes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Maintain active SeekTalent runtime data.")
    parser.add_argument("command", choices=("retention", "cleanup", "backup", "health", "support-bundle"))
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    settings = AppSettings(_env_file=None, workspace_root=str(args.workspace_root))
    if args.command == "retention":
        result = run_runtime_control_retention(runtime_control_db_path=settings.runtime_control_path, apply=args.apply)
        print(result)
    elif args.command == "cleanup":
        print(run_local_storage_cleanup(workspace_root=args.workspace_root, apply=args.apply))
    elif args.command == "backup":
        print(run_database_group_backup(workspace_root=args.workspace_root))
    elif args.command == "health":
        print(run_operator_health(workspace_root=args.workspace_root))
    else:
        print(create_execution_support_bundle(settings=settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
