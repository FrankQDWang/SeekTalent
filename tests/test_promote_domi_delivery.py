from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from scripts.promote_domi_delivery import PLATFORMS, promote_delivery


def test_promotion_preserves_old_artifacts_and_requires_one_exact_identity(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    build = dist / "tmp" / "0.8.0rc1-aaaaaaaaaaaa"
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

    (build / "seektalent-0.8.0rc1-py3-none-any.whl").write_bytes(b"wheel")
    (build / "seektalent-0.8.0rc1.tar.gz").write_bytes(b"sdist")
    source_revision = "a" * 40
    fixture_sha = "b" * 64
    for platform_name in PLATFORMS:
        archive = build / (
            f"seektalent-offline-0.8.0rc1-{platform_name}-py313.zip"
        )
        manifest = {
            "schema_version": 2,
            "product_version": "0.8.0rc1",
            "platform": platform_name,
            "source_revision": source_revision,
            "product_build_id": f"seektalent-0.8.0rc1+{source_revision}",
            "acceptance_fixture": {"sha256": fixture_sha},
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
    archived = dist / "archive" / "before-0.8.0rc1-aaaaaaaaaaaa"
    assert (archived / "top-level" / "seektalent-0.7.49.tar.gz").exists()
    assert (
        archived
        / "last-version"
        / "seektalent-offline-0.7.46-macos-arm64-py313.zip"
    ).exists()
    for platform_name in PLATFORMS:
        assert (
            dist
            / f"seektalent-offline-0.8.0rc1-{platform_name}-py313.zip"
        ).exists()
