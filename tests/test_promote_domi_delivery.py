from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from scripts.promote_domi_delivery import (
    PLATFORMS,
    promote_delivery,
    stage_delivery,
)


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.8.3"


def test_promotion_script_runs_by_file_path_without_pythonpath() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "scripts/promote_domi_delivery.py", "--help"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


def test_stage_keeps_only_three_candidate_zips_in_tmp(
    tmp_path: Path,
) -> None:
    dist = _dist_layout(tmp_path)
    production_files = {
        path.name: path.read_bytes()
        for path in (dist / "last-version").iterdir()
    }
    build = _build_release(tmp_path, source_revision="a" * 40)

    identity = stage_delivery(dist_dir=dist, build_dir=build)

    assert identity["source_revision"] == "a" * 40
    assert set(path.name for path in dist.iterdir()) == {
        "README.md",
        "active",
        "last-version",
        "tmp",
    }
    assert list((dist / "active").iterdir()) == []
    assert {
        path.name: path.read_bytes()
        for path in (dist / "last-version").iterdir()
    } == production_files
    assert {path.name for path in (dist / "tmp").iterdir()} == {
        f"seektalent-offline-{VERSION}-{platform}-py313.zip"
        for platform in PLATFORMS
    }


def test_promote_moves_tmp_to_active_and_preserves_production_last_version(
    tmp_path: Path,
) -> None:
    dist = _dist_layout(tmp_path)
    production_files = {
        path.name: path.read_bytes()
        for path in (dist / "last-version").iterdir()
    }
    build = _build_release(tmp_path, source_revision="a" * 40)
    stage_delivery(dist_dir=dist, build_dir=build)

    identity = promote_delivery(dist_dir=dist)

    assert identity["source_revision"] == "a" * 40
    assert list((dist / "tmp").iterdir()) == []
    assert {path.name for path in (dist / "active").iterdir()} == {
        f"seektalent-offline-{VERSION}-{platform}-py313.zip"
        for platform in PLATFORMS
    }
    assert {
        path.name: path.read_bytes()
        for path in (dist / "last-version").iterdir()
    } == production_files


def test_next_promotion_rotates_active_to_last_version(
    tmp_path: Path,
) -> None:
    dist = _dist_layout(tmp_path)
    first_build = _build_release(
        tmp_path / "first",
        source_revision="a" * 40,
    )
    stage_delivery(dist_dir=dist, build_dir=first_build)
    promote_delivery(dist_dir=dist)
    active_before = {
        path.name: path.read_bytes() for path in (dist / "active").iterdir()
    }
    second_build = _build_release(
        tmp_path / "second",
        source_revision="c" * 40,
    )
    stage_delivery(dist_dir=dist, build_dir=second_build)

    promote_delivery(dist_dir=dist)

    assert {
        path.name: path.read_bytes()
        for path in (dist / "last-version").iterdir()
    } == active_before
    assert list((dist / "tmp").iterdir()) == []


def test_next_promotion_accepts_an_older_valid_active_version(
    tmp_path: Path,
) -> None:
    dist = _dist_layout(tmp_path)
    old_active = _delivery_archives(
        dist / "active",
        version="0.8.2",
        source_revision="d" * 40,
    )
    old_active_bytes = {path.name: path.read_bytes() for path in old_active}
    build = _build_release(tmp_path, source_revision="a" * 40)
    stage_delivery(dist_dir=dist, build_dir=build)

    promote_delivery(dist_dir=dist)

    assert {
        path.name: path.read_bytes()
        for path in (dist / "last-version").iterdir()
    } == old_active_bytes
    assert {path.name for path in (dist / "active").iterdir()} == {
        f"seektalent-offline-{VERSION}-{platform}-py313.zip"
        for platform in PLATFORMS
    }


def test_stage_rejects_top_level_wheel_that_differs_from_archives(
    tmp_path: Path,
) -> None:
    dist = _dist_layout(tmp_path)
    build = _build_release(tmp_path, source_revision="a" * 40)
    (build / f"seektalent-{VERSION}-py3-none-any.whl").write_bytes(
        b"different-wheel"
    )

    with pytest.raises(ValueError, match="top-level wheel does not match"):
        stage_delivery(dist_dir=dist, build_dir=build)


def test_stage_rejects_unexpected_dist_entries(tmp_path: Path) -> None:
    dist = _dist_layout(tmp_path)
    (dist / "archive").mkdir()
    build = _build_release(tmp_path, source_revision="a" * 40)

    with pytest.raises(ValueError, match="unexpected dist entries: archive"):
        stage_delivery(dist_dir=dist, build_dir=build)


def test_promotion_scope_matches_the_three_platform_release_handoff() -> None:
    assert PLATFORMS == ("macos-arm64", "macos-x86_64", "windows-x64")


def _dist_layout(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    for name in ("active", "last-version", "tmp"):
        (dist / name).mkdir(parents=True)
    (dist / "README.md").write_text("delivery states\n", encoding="utf-8")
    for platform in PLATFORMS:
        (dist / "last-version" / f"production-{platform}.zip").write_bytes(
            f"0.7.47-{platform}".encode()
        )
    return dist


def _build_release(
    tmp_path: Path,
    *,
    source_revision: str,
) -> Path:
    build = tmp_path / "build"
    build.mkdir(parents=True)
    wheel_bytes = f"wheel-{source_revision}".encode()
    wheel_sha = hashlib.sha256(wheel_bytes).hexdigest()
    (build / f"seektalent-{VERSION}-py3-none-any.whl").write_bytes(
        wheel_bytes
    )
    (build / f"seektalent-{VERSION}.tar.gz").write_bytes(b"sdist")
    archives = _delivery_archives(
        build,
        version=VERSION,
        source_revision=source_revision,
        wheel_sha=wheel_sha,
    )
    for archive in archives:
        archive.with_name(f"{archive.name}.sha256").write_text(
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  "
            f"{archive.name}\n",
            encoding="utf-8",
        )
    return build


def _delivery_archives(
    directory: Path,
    *,
    version: str,
    source_revision: str,
    wheel_sha: str = "e" * 64,
) -> tuple[Path, ...]:
    directory.mkdir(parents=True, exist_ok=True)
    archives: list[Path] = []
    for platform in PLATFORMS:
        archive = directory / (
            f"seektalent-offline-{version}-{platform}-py313.zip"
        )
        manifest = {
            "schema_version": 2,
            "product_version": version,
            "platform": platform,
            "source_revision": source_revision,
            "product_build_id": f"seektalent-{version}+{source_revision}",
            "acceptance_fixture": {"sha256": "b" * 64},
            "seektalent_wheel_sha256": wheel_sha,
        }
        with zipfile.ZipFile(archive, "w") as delivery:
            delivery.writestr(
                f"{archive.stem}/delivery-manifest.json",
                json.dumps(manifest),
            )
        archives.append(archive)
    return tuple(archives)
