from __future__ import annotations

import json
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .store import atomic_write_text


class ArchiveCollisionError(RuntimeError):
    pass


class ArchiveMigrationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str
    destination_path: str


class ArchiveMigrationReport(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    plan_path: Path
    result_path: Path | None = None
    rows: list[ArchiveMigrationRow]


def classify_legacy_entries(*, project_root: Path, legacy_runs_root: Path, artifacts_root: Path) -> list[ArchiveMigrationRow]:
    if not legacy_runs_root.exists():
        return []
    rows: list[ArchiveMigrationRow] = []
    for entry in sorted(legacy_runs_root.iterdir(), key=lambda path: path.name):
        source = entry.relative_to(project_root).as_posix()
        destination = (artifacts_root / "archive" / "legacy-runs" / entry.name).relative_to(project_root).as_posix()
        rows.append(ArchiveMigrationRow(source_path=source, destination_path=destination))
    return rows


def dry_run_archive_migration(*, project_root: Path, legacy_runs_root: Path, artifacts_root: Path) -> ArchiveMigrationReport:
    rows = [
        row
        for row in classify_legacy_entries(
            project_root=project_root,
            legacy_runs_root=legacy_runs_root,
            artifacts_root=artifacts_root,
        )
        if row.source_path not in {"runs/.decommissioned", "runs/README.md"}
    ]
    plan_path = artifacts_root / "archive" / "archive_migration_plan.json"
    atomic_write_text(
        plan_path,
        json.dumps([row.model_dump(mode="json") for row in rows], ensure_ascii=False, indent=2),
    )
    return ArchiveMigrationReport(plan_path=plan_path, rows=rows)


def execute_archive_migration(*, project_root: Path, legacy_runs_root: Path, artifacts_root: Path) -> ArchiveMigrationReport:
    plan = dry_run_archive_migration(
        project_root=project_root,
        legacy_runs_root=legacy_runs_root,
        artifacts_root=artifacts_root,
    )
    for row in plan.rows:
        source = project_root / row.source_path
        destination = project_root / row.destination_path
        if source == destination or not source.exists():
            continue
        if destination.exists():
            raise ArchiveCollisionError(f"Archive destination already exists: {destination}")
    for row in plan.rows:
        source = project_root / row.source_path
        destination = project_root / row.destination_path
        if source == destination or not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    _write_decommission_markers(legacy_runs_root)
    result_path = artifacts_root / "archive" / "archive_migration_result.json"
    atomic_write_text(
        result_path,
        json.dumps([row.model_dump(mode="json") for row in plan.rows], ensure_ascii=False, indent=2),
    )
    return ArchiveMigrationReport(plan_path=plan.plan_path, result_path=result_path, rows=plan.rows)


def _write_decommission_markers(legacy_runs_root: Path) -> None:
    legacy_runs_root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(legacy_runs_root / ".decommissioned", "Legacy runs root archived. Use artifacts/ instead.\n")
    atomic_write_text(
        legacy_runs_root / "README.md",
        "\n".join(
            [
                "# Legacy runs root decommissioned",
                "",
                "Historical contents were archived under `artifacts/archive/legacy-runs/`.",
                "Use `artifacts/` as the active output root.",
                "",
            ]
        ),
    )
