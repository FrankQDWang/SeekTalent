from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.config import AppSettings


StorageClass = Literal[
    "product_db",
    "product_projection_db",
    "advisory_memory",
    "provider_state",
    "artifact_debug",
    "artifact_export",
    "cache",
    "backup",
    "support_bundle",
    "browser_state",
]
LocalStorageCleanupScope = Literal["full", "runtime_artifacts"]

PROD_LOCAL_STORAGE_BUDGET_BYTES = 2_000_000_000
DEV_LOCAL_STORAGE_BUDGET_BYTES = 10_000_000_000
STORAGE_WARNING_RATIO = 0.8


class LocalStorageLifecyclePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debug_retention_days: int = Field(default=7, ge=0)
    cache_retention_days: int = Field(default=7, ge=0)
    support_bundle_retention_days: int = Field(default=7, ge=0)
    backup_retention_days: int = Field(default=30, ge=0)
    max_backup_count: int = Field(default=10, ge=1)
    max_backup_total_bytes: int = Field(default=2_000_000_000, ge=1)


@dataclass(frozen=True)
class LocalStorageRoot:
    name: str
    path: Path
    storage_class: StorageClass
    protected: bool
    exists: bool
    size_bytes: int
    file_count: int
    sqlite_sibling_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class LocalStorageInventory:
    runtime_mode: str
    budget_bytes: int
    warning_threshold_bytes: int
    roots: tuple[LocalStorageRoot, ...]

    @property
    def total_size_bytes(self) -> int:
        return sum(root.size_bytes for root in self.roots)

    @property
    def over_warning_threshold(self) -> bool:
        return self.total_size_bytes >= self.warning_threshold_bytes

    @property
    def over_budget(self) -> bool:
        return self.total_size_bytes > self.budget_bytes


@dataclass(frozen=True)
class LocalStorageCleanupCandidate:
    path: Path
    storage_class: StorageClass
    reason_code: str
    size_bytes: int


@dataclass(frozen=True)
class LocalStorageStoreCleanupCount:
    store_name: str
    item_kind: str
    count: int


@dataclass(frozen=True)
class LocalStorageCleanupResult:
    dry_run: bool
    inventory: LocalStorageInventory
    candidates: tuple[LocalStorageCleanupCandidate, ...]
    store_cleanup_counts: tuple[LocalStorageStoreCleanupCount, ...] = ()
    deleted_file_count: int = 0
    deleted_bytes: int = 0
    errors: tuple[str, ...] = ()

    @property
    def store_candidate_count(self) -> int:
        return sum(item.count for item in self.store_cleanup_counts)


@dataclass(frozen=True)
class SQLiteFileReport:
    path: Path
    database_size_bytes: int
    wal_size_bytes: int
    shm_size_bytes: int
    freelist_count: int | None


@dataclass(frozen=True)
class SQLiteCheckpointResult:
    path: Path
    status: Literal["checkpointed", "blocked", "missing"]
    busy_count: int
    log_frame_count: int
    checkpointed_frame_count: int
    wal_size_before_bytes: int
    wal_size_after_bytes: int


@dataclass(frozen=True)
class SQLiteVacuumResult:
    path: Path
    status: Literal["vacuumed", "missing"]
    database_size_before_bytes: int
    database_size_after_bytes: int


def build_local_storage_inventory(settings: AppSettings) -> LocalStorageInventory:
    budget = PROD_LOCAL_STORAGE_BUDGET_BYTES if settings.runtime_mode == "prod" else DEV_LOCAL_STORAGE_BUDGET_BYTES
    roots = tuple(_root_inventory(root) for root in _storage_roots(settings))
    return LocalStorageInventory(
        runtime_mode=settings.runtime_mode,
        budget_bytes=budget,
        warning_threshold_bytes=int(budget * STORAGE_WARNING_RATIO),
        roots=roots,
    )


def cleanup_local_storage(
    settings: AppSettings,
    *,
    now: datetime | None = None,
    policy: LocalStorageLifecyclePolicy | None = None,
    apply: bool = False,
    cleanup_scope: LocalStorageCleanupScope = "full",
) -> LocalStorageCleanupResult:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    active_policy = policy or LocalStorageLifecyclePolicy()
    inventory = _cleanup_inventory(settings, cleanup_scope=cleanup_scope)
    candidates = tuple(
        _cleanup_candidates(
            settings=settings,
            now=current_time,
            policy=active_policy,
            cleanup_scope=cleanup_scope,
        )
    )
    deleted_count = 0
    deleted_bytes = 0
    errors: list[str] = []
    if apply:
        for candidate in candidates:
            try:
                if not candidate.path.exists():
                    continue
                if candidate.path.is_dir():
                    shutil.rmtree(candidate.path)
                else:
                    candidate.path.unlink()
                deleted_count += 1
                deleted_bytes += candidate.size_bytes
            except OSError as exc:
                errors.append(f"{candidate.path}: {exc}")
        _remove_empty_dirs(settings.artifacts_path)
        _remove_empty_dirs(settings.llm_cache_path)
        _remove_empty_dirs(_backup_root(settings))
    return LocalStorageCleanupResult(
        dry_run=not apply,
        inventory=inventory,
        candidates=candidates,
        deleted_file_count=deleted_count,
        deleted_bytes=deleted_bytes,
        errors=tuple(errors),
    )


def sqlite_file_report(path: Path) -> SQLiteFileReport:
    freelist_count: int | None = None
    if path.exists():
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA freelist_count").fetchone()
            freelist_count = int(row[0]) if row is not None else None
        except sqlite3.Error:
            freelist_count = None
        finally:
            conn.close()
    return SQLiteFileReport(
        path=path,
        database_size_bytes=_file_size(path),
        wal_size_bytes=_file_size(_sqlite_sibling(path, "-wal")),
        shm_size_bytes=_file_size(_sqlite_sibling(path, "-shm")),
        freelist_count=freelist_count,
    )


def checkpoint_sqlite_database(path: Path) -> SQLiteCheckpointResult:
    if not path.exists():
        return SQLiteCheckpointResult(
            path=path,
            status="missing",
            busy_count=0,
            log_frame_count=0,
            checkpointed_frame_count=0,
            wal_size_before_bytes=0,
            wal_size_after_bytes=0,
        )
    wal_path = _sqlite_sibling(path, "-wal")
    wal_size_before = _file_size(wal_path)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()
    busy_count = int(row[0])
    log_frame_count = int(row[1])
    checkpointed_frame_count = int(row[2])
    return SQLiteCheckpointResult(
        path=path,
        status="blocked" if busy_count else "checkpointed",
        busy_count=busy_count,
        log_frame_count=log_frame_count,
        checkpointed_frame_count=checkpointed_frame_count,
        wal_size_before_bytes=wal_size_before,
        wal_size_after_bytes=_file_size(wal_path),
    )


def vacuum_sqlite_database(path: Path) -> SQLiteVacuumResult:
    if not path.exists():
        return SQLiteVacuumResult(
            path=path,
            status="missing",
            database_size_before_bytes=0,
            database_size_after_bytes=0,
        )
    size_before = _file_size(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return SQLiteVacuumResult(
        path=path,
        status="vacuumed",
        database_size_before_bytes=size_before,
        database_size_after_bytes=_file_size(path),
    )


def _storage_roots(settings: AppSettings) -> tuple[LocalStorageRootSeed, ...]:
    workspace_root = settings.project_root
    liepin_session_store = settings.resolve_workspace_path(settings.liepin_session_store_dir)
    return (
        LocalStorageRootSeed("runtime_control_db", settings.runtime_control_path, "product_db", True, True),
        LocalStorageRootSeed("conversation_agent_db", settings.conversation_agent_path, "product_db", True, True),
        LocalStorageRootSeed("agent_memory_db", settings.agent_memory_path, "advisory_memory", True, True),
        LocalStorageRootSeed(
            "workbench_db",
            workspace_root / ".seektalent" / "workbench.sqlite3",
            "product_projection_db",
            True,
            True,
        ),
        LocalStorageRootSeed(
            "workbench_stream_db",
            workspace_root / ".seektalent" / "agent_workbench_stream.sqlite3",
            "product_projection_db",
            True,
            True,
        ),
        LocalStorageRootSeed(
            "liepin_db",
            settings.resolve_workspace_path(settings.liepin_connector_db_path),
            "provider_state",
            True,
            True,
        ),
        LocalStorageRootSeed("liepin_session_store", liepin_session_store, "browser_state", True, False),
        LocalStorageRootSeed("corpus_db", settings.corpus_path, "product_db", True, True),
        LocalStorageRootSeed("corpus_artifacts", settings.artifacts_path / "corpus", "artifact_export", False, False),
        LocalStorageRootSeed("export_artifacts", settings.artifacts_path / "exports", "artifact_export", False, False),
        LocalStorageRootSeed("artifacts_root", settings.artifacts_path, "artifact_debug", False, False),
        LocalStorageRootSeed("llm_cache", settings.llm_cache_path, "cache", False, False),
        LocalStorageRootSeed("backup_root", _backup_root(settings), "backup", False, False),
        LocalStorageRootSeed("agent_memory_workspace", settings.agent_memory_workspace_path, "advisory_memory", True, False),
    )


def _runtime_artifact_storage_roots(settings: AppSettings) -> tuple[LocalStorageRootSeed, ...]:
    return (
        LocalStorageRootSeed("runtime_run_artifacts", settings.artifacts_path / "runs", "artifact_debug", False, False),
        LocalStorageRootSeed(
            "runtime_benchmark_artifacts",
            settings.artifacts_path / "benchmark-executions",
            "artifact_debug",
            False,
            False,
        ),
    )


@dataclass(frozen=True)
class LocalStorageRootSeed:
    name: str
    path: Path
    storage_class: StorageClass
    protected: bool
    sqlite_siblings: bool


def _root_inventory(seed: LocalStorageRootSeed) -> LocalStorageRoot:
    paths = [seed.path]
    sibling_paths: tuple[Path, ...] = ()
    if seed.sqlite_siblings:
        sibling_paths = (_sqlite_sibling(seed.path, "-wal"), _sqlite_sibling(seed.path, "-shm"))
        paths.extend(sibling_paths)
    size = 0
    file_count = 0
    for path in paths:
        path_size, path_count = _path_size(path)
        size += path_size
        file_count += path_count
    return LocalStorageRoot(
        name=seed.name,
        path=seed.path,
        storage_class=seed.storage_class,
        protected=seed.protected,
        exists=seed.path.exists(),
        size_bytes=size,
        file_count=file_count,
        sqlite_sibling_paths=sibling_paths,
    )


def _cleanup_inventory(settings: AppSettings, *, cleanup_scope: LocalStorageCleanupScope) -> LocalStorageInventory:
    if cleanup_scope == "full":
        return build_local_storage_inventory(settings)
    budget = PROD_LOCAL_STORAGE_BUDGET_BYTES if settings.runtime_mode == "prod" else DEV_LOCAL_STORAGE_BUDGET_BYTES
    return LocalStorageInventory(
        runtime_mode=settings.runtime_mode,
        budget_bytes=budget,
        warning_threshold_bytes=int(budget * STORAGE_WARNING_RATIO),
        roots=tuple(_root_inventory(root) for root in _runtime_artifact_storage_roots(settings)),
    )


def _cleanup_candidates(
    *,
    settings: AppSettings,
    now: datetime,
    policy: LocalStorageLifecyclePolicy,
    cleanup_scope: LocalStorageCleanupScope,
) -> tuple[LocalStorageCleanupCandidate, ...]:
    candidates: list[LocalStorageCleanupCandidate] = []
    candidates.extend(
        _expired_partition_files(
            settings.artifacts_path / "runs",
            storage_class="artifact_debug",
            reason_code="artifact_run_expired",
            cutoff_date=_cutoff(now, policy.debug_retention_days).date(),
        )
    )
    candidates.extend(
        _expired_partition_files(
            settings.artifacts_path / "benchmark-executions",
            storage_class="artifact_debug",
            reason_code="artifact_benchmark_expired",
            cutoff_date=_cutoff(now, policy.debug_retention_days).date(),
        )
    )
    if cleanup_scope == "runtime_artifacts":
        return _dedupe_candidates(candidate for candidate in candidates if not _under_active_artifact_run(candidate.path))
    candidates.extend(
        _expired_files(
            settings.artifacts_path / "debug",
            storage_class="artifact_debug",
            reason_code="artifact_debug_expired",
            cutoff=_cutoff(now, policy.debug_retention_days),
        )
    )
    candidates.extend(
        _expired_files(
            settings.artifacts_path / "support-bundles",
            storage_class="support_bundle",
            reason_code="support_bundle_expired",
            cutoff=_cutoff(now, policy.support_bundle_retention_days),
        )
    )
    candidates.extend(
        _expired_files(
            settings.llm_cache_path,
            storage_class="cache",
            reason_code="cache_expired",
            cutoff=_cutoff(now, policy.cache_retention_days),
        )
    )
    candidates.extend(_backup_cleanup_candidates(_backup_root(settings), now=now, policy=policy))
    return _dedupe_candidates(candidate for candidate in candidates if not _under_active_artifact_run(candidate.path))


def _expired_files(
    root: Path,
    *,
    storage_class: StorageClass,
    reason_code: str,
    cutoff: datetime,
) -> list[LocalStorageCleanupCandidate]:
    if not root.exists():
        return []
    candidates: list[LocalStorageCleanupCandidate] = []
    for path in root.rglob("*"):
        if path.is_file() and _mtime(path) < cutoff:
            candidates.append(
                LocalStorageCleanupCandidate(
                    path=path,
                    storage_class=storage_class,
                    reason_code=reason_code,
                    size_bytes=_file_size(path),
                )
            )
    return candidates


def _expired_partition_files(
    root: Path,
    *,
    storage_class: StorageClass,
    reason_code: str,
    cutoff_date: date,
) -> list[LocalStorageCleanupCandidate]:
    if not root.exists():
        return []
    candidates: list[LocalStorageCleanupCandidate] = []
    for year_dir in root.iterdir():
        if not year_dir.is_dir():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir():
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir():
                    continue
                partition_date = _partition_date(year_dir.name, month_dir.name, day_dir.name)
                if partition_date is None or partition_date >= cutoff_date:
                    continue
                for path in day_dir.rglob("*"):
                    if path.is_file():
                        candidates.append(
                            LocalStorageCleanupCandidate(
                                path=path,
                                storage_class=storage_class,
                                reason_code=reason_code,
                                size_bytes=_file_size(path),
                            )
                        )
    return candidates


def _backup_cleanup_candidates(
    root: Path,
    *,
    now: datetime,
    policy: LocalStorageLifecyclePolicy,
) -> list[LocalStorageCleanupCandidate]:
    if not root.exists():
        return []
    backup_files = sorted(
        (path for path in root.glob("*.sqlite3") if path.is_file()),
        key=lambda path: _mtime(path),
        reverse=True,
    )
    keep = set(backup_files[: policy.max_backup_count])
    candidates: list[LocalStorageCleanupCandidate] = []
    cutoff = _cutoff(now, policy.backup_retention_days)
    total_size = 0
    for path in backup_files:
        total_size += _file_size(path) + _file_size(path.with_suffix(".json"))
        expired = _mtime(path) < cutoff
        over_count = path not in keep
        over_total = total_size > policy.max_backup_total_bytes
        if expired or over_count or over_total:
            candidates.extend(_backup_pair_candidates(path))
    return candidates


def _backup_pair_candidates(path: Path) -> list[LocalStorageCleanupCandidate]:
    candidates = [
        LocalStorageCleanupCandidate(
            path=path,
            storage_class="backup",
            reason_code="backup_expired",
            size_bytes=_file_size(path),
        )
    ]
    metadata_path = path.with_suffix(".json")
    if metadata_path.exists():
        candidates.append(
            LocalStorageCleanupCandidate(
                path=metadata_path,
                storage_class="backup",
                reason_code="backup_expired",
                size_bytes=_file_size(metadata_path),
            )
        )
    return candidates


def _under_active_artifact_run(path: Path) -> bool:
    for parent in (path, *path.parents):
        manifests = parent / "manifests"
        if not manifests.exists():
            continue
        for manifest_path in manifests.glob("*_manifest.json"):
            with suppress(OSError, json.JSONDecodeError):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("status") == "running":
                    return True
    return False


def _dedupe_candidates(candidates: Iterable[LocalStorageCleanupCandidate]) -> tuple[LocalStorageCleanupCandidate, ...]:
    by_path: dict[Path, LocalStorageCleanupCandidate] = {}
    for candidate in candidates:
        by_path.setdefault(candidate.path, candidate)
    return tuple(by_path[path] for path in sorted(by_path))


def _path_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        return _file_size(path), 1
    size = 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file():
            size += _file_size(item)
            count += 1
    return size, count


def _remove_empty_dirs(root: Path) -> None:
    if not root.exists() or not root.is_dir():
        return
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        with suppress(OSError):
            path.rmdir()


def _backup_root(settings: AppSettings) -> Path:
    return settings.project_root / ".seektalent" / "backups"


def _sqlite_sibling(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() and path.is_file() else 0


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _partition_date(year: str, month: str, day: str) -> date | None:
    try:
        return datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d").date()
    except ValueError:
        return None
