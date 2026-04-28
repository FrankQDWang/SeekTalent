from __future__ import annotations

import json
from pathlib import Path

import pytest

from seektalent.artifacts.legacy import (
    ArchiveCollisionError,
    dry_run_archive_migration,
    execute_archive_migration,
)
from tests.settings_factory import make_settings


def test_archive_migration_writes_dry_run_plan_and_result(tmp_path: Path) -> None:
    legacy_runs = tmp_path / "runs"
    (legacy_runs / "20260422_192141_deadbeef" / "trace.log").parent.mkdir(parents=True, exist_ok=True)
    (legacy_runs / "debug_openclaw").mkdir(parents=True)

    report = dry_run_archive_migration(
        project_root=tmp_path,
        legacy_runs_root=legacy_runs,
        artifacts_root=tmp_path / "artifacts",
    )

    assert report.plan_path == tmp_path / "artifacts" / "archive" / "archive_migration_plan.json"
    assert report.rows[0].destination_path.startswith("artifacts/archive/")
    payload = json.loads(report.plan_path.read_text(encoding="utf-8"))
    assert payload[0]["destination_path"].startswith("artifacts/archive/")


def test_archive_migration_is_idempotent_and_leaves_decommissioned_runs_root(tmp_path: Path) -> None:
    legacy_runs = tmp_path / "runs"
    (legacy_runs / "20260422_192141_deadbeef" / "trace.log").parent.mkdir(parents=True, exist_ok=True)

    first = execute_archive_migration(
        project_root=tmp_path,
        legacy_runs_root=legacy_runs,
        artifacts_root=tmp_path / "artifacts",
    )
    second = execute_archive_migration(
        project_root=tmp_path,
        legacy_runs_root=legacy_runs,
        artifacts_root=tmp_path / "artifacts",
    )

    assert first.result_path == tmp_path / "artifacts" / "archive" / "archive_migration_result.json"
    assert second.rows == []
    assert (legacy_runs / ".decommissioned").exists()
    assert (legacy_runs / "README.md").exists()


def test_archive_migration_fails_on_destination_collision(tmp_path: Path) -> None:
    legacy_runs = tmp_path / "runs"
    (legacy_runs / "20260422_192141_deadbeef").mkdir(parents=True)
    collision = tmp_path / "artifacts" / "archive" / "legacy-runs" / "20260422_192141_deadbeef"
    collision.mkdir(parents=True)

    with pytest.raises(ArchiveCollisionError):
        execute_archive_migration(
            project_root=tmp_path,
            legacy_runs_root=legacy_runs,
            artifacts_root=tmp_path / "artifacts",
        )


def test_runtime_rejects_legacy_runs_root_as_active_output_decommissioned(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="decommissioned"):
        make_settings(artifacts_dir=str(tmp_path / "runs"), mock_cts=True).artifacts_path


def test_runtime_rejects_nested_legacy_runs_root_as_active_output_decommissioned(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="decommissioned"):
        make_settings(artifacts_dir=str(tmp_path / "runs" / "subtree"), mock_cts=True).artifacts_path
