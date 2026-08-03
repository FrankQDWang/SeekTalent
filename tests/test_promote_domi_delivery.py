from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.promote_domi_delivery import PLATFORMS, promote_delivery


def test_promotion_preserves_old_artifacts_and_requires_one_exact_identity(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    build = dist / "tmp" / "0.8.0rc2-aaaaaaaaaaaa"
    build.mkdir(parents=True)
    (dist / ".gitignore").write_text("*\n", encoding="utf-8")
    old_current = dist / "seektalent-offline-0.7.47-macos-arm64-py313.zip"
    old_current.write_bytes(b"0.7.47")
    (dist / "seektalent-0.7.49.tar.gz").write_bytes(b"0.7.49")
    last = dist / "last-version"
    last.mkdir()
    old_current_intel = last / "seektalent-offline-0.7.47-macos-x86_64-py313.zip"
    old_current_intel.write_bytes(b"0.7.47-intel")
    (last / "seektalent-offline-0.7.46-macos-arm64-py313.zip").write_bytes(
        b"0.7.46"
    )

    (build / "seektalent-0.8.0rc2-py3-none-any.whl").write_bytes(b"wheel")
    (build / "seektalent-0.8.0rc2.tar.gz").write_bytes(b"sdist")
    source_revision = "a" * 40
    fixture_sha = "b" * 64
    for platform_name in PLATFORMS:
        archive = build / (
            f"seektalent-offline-0.8.0rc2-{platform_name}-py313.zip"
        )
        manifest = {
            "schema_version": 2,
            "product_version": "0.8.0rc2",
            "platform": platform_name,
            "source_revision": source_revision,
            "product_build_id": f"seektalent-0.8.0rc2+{source_revision}",
            "acceptance_fixture": {"sha256": fixture_sha},
            "seektalent_wheel_sha256": hashlib.sha256(b"wheel").hexdigest(),
        }
        with zipfile.ZipFile(archive, "w") as delivery:
            delivery.writestr(
                f"{archive.stem}/delivery-manifest.json",
                json.dumps(manifest),
            )
        archive.with_name(f"{archive.name}.sha256").write_text(
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
            encoding="utf-8",
        )

    identity = promote_delivery(dist_dir=dist, build_dir=build)

    assert identity["source_revision"] == source_revision
    assert (dist / old_current.name).exists() is False
    assert (dist / "last-version" / old_current.name).read_bytes() == b"0.7.47"
    assert (dist / "last-version" / old_current_intel.name).read_bytes() == b"0.7.47-intel"
    archived = dist / "archive" / "before-0.8.0rc2-aaaaaaaaaaaa"
    assert (archived / "top-level" / "seektalent-0.7.49.tar.gz").exists()
    assert (
        archived
        / "last-version"
        / "seektalent-offline-0.7.46-macos-arm64-py313.zip"
    ).exists()
    for platform_name in PLATFORMS:
        assert (
            dist
            / f"seektalent-offline-0.8.0rc2-{platform_name}-py313.zip"
        ).exists()


def test_promotion_rejects_top_level_wheel_that_differs_from_delivery_archives(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    build = dist / "tmp" / "0.8.0rc2-aaaaaaaaaaaa"
    build.mkdir(parents=True)
    (dist / ".gitignore").write_text("*\n", encoding="utf-8")
    (build / "seektalent-0.8.0rc2-py3-none-any.whl").write_bytes(b"intermediate-wheel")
    (build / "seektalent-0.8.0rc2.tar.gz").write_bytes(b"sdist")
    source_revision = "a" * 40
    fixture_sha = "b" * 64
    exact_wheel_sha = hashlib.sha256(b"exact-wheel").hexdigest()
    for platform_name in PLATFORMS:
        archive = build / (
            f"seektalent-offline-0.8.0rc2-{platform_name}-py313.zip"
        )
        with zipfile.ZipFile(archive, "w") as delivery:
            delivery.writestr(
                f"{archive.stem}/delivery-manifest.json",
                json.dumps(
                    {
                        "schema_version": 2,
                        "product_version": "0.8.0rc2",
                        "platform": platform_name,
                        "source_revision": source_revision,
                        "product_build_id": f"seektalent-0.8.0rc2+{source_revision}",
                        "acceptance_fixture": {"sha256": fixture_sha},
                        "seektalent_wheel_sha256": exact_wheel_sha,
                    }
                ),
            )
        archive.with_name(f"{archive.name}.sha256").write_text(
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="top-level wheel does not match"):
        promote_delivery(dist_dir=dist, build_dir=build)


def test_promotion_scope_matches_the_macos_arm64_release_handoff() -> None:
    assert PLATFORMS == ("macos-arm64",)
