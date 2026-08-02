from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from typing import cast
import zipfile
from pathlib import Path

from seektalent.version import __version__


PLATFORMS = ("macos-arm64", "macos-x86_64", "windows-x64")


def promote_delivery(*, dist_dir: Path, build_dir: Path) -> dict[str, str]:
    """Validate one three-platform build and preserve every previous artifact."""
    dist_dir = dist_dir.resolve(strict=True)
    build_dir = build_dir.resolve(strict=True)
    artifacts, identity = _validated_artifacts(build_dir)
    short_revision = identity["source_revision"][:12]
    archive_dir = dist_dir / "archive" / f"before-{__version__}-{short_revision}"
    if archive_dir.exists():
        raise FileExistsError(f"archive destination already exists: {archive_dir}")

    previous_top = [
        path
        for path in dist_dir.iterdir()
        if path.name not in {".gitignore", "archive", "last-version", "tmp"}
    ]
    previous_release = [
        path
        for path in previous_top
        if "0.7.47" in path.name
    ]
    archived_top = [path for path in previous_top if path not in previous_release]
    previous_last = dist_dir / "last-version"

    archive_top = archive_dir / "top-level"
    archive_top.mkdir(parents=True)
    for path in archived_top:
        shutil.move(str(path), archive_top / path.name)
    if previous_last.exists():
        shutil.move(str(previous_last), archive_dir / "last-version")

    next_last = dist_dir / "last-version"
    next_last.mkdir()
    for path in previous_release:
        shutil.move(str(path), next_last / path.name)
    for source in artifacts:
        shutil.copy2(source, dist_dir / source.name)
    return identity


def _validated_artifacts(build_dir: Path) -> tuple[list[Path], dict[str, str]]:
    wheel = build_dir / f"seektalent-{__version__}-py3-none-any.whl"
    sdist = build_dir / f"seektalent-{__version__}.tar.gz"
    artifacts = [wheel, sdist]
    manifests: list[dict[str, object]] = []
    for platform_name in PLATFORMS:
        archive = build_dir / (
            f"seektalent-offline-{__version__}-{platform_name}-py313.zip"
        )
        checksum = archive.with_name(f"{archive.name}.sha256")
        artifacts.extend((archive, checksum))
        if not archive.is_file() or not checksum.is_file():
            raise FileNotFoundError(f"missing delivery artifact for {platform_name}")
        expected_line = f"{_sha256(archive)}  {archive.name}\n"
        if checksum.read_text(encoding="utf-8") != expected_line:
            raise ValueError(f"archive checksum mismatch for {platform_name}")
        with zipfile.ZipFile(archive) as delivery:
            if delivery.testzip() is not None:
                raise ValueError(f"corrupt delivery archive for {platform_name}")
            root = archive.stem
            manifests.append(
                json.loads(delivery.read(f"{root}/delivery-manifest.json"))
            )
    if not wheel.is_file() or not sdist.is_file():
        raise FileNotFoundError("the exact wheel and sdist are required")

    first = manifests[0]
    fixture = first.get("acceptance_fixture")
    if not isinstance(fixture, dict):
        raise ValueError("acceptance fixture identity is missing")
    fixture = cast(dict[str, object], fixture)
    identity = {
        "schema_version": str(first.get("schema_version")),
        "product_version": str(first.get("product_version")),
        "source_revision": str(first.get("source_revision")),
        "product_build_id": str(first.get("product_build_id")),
        "fixture_sha256": str(fixture.get("sha256")),
    }
    if (
        identity["schema_version"] != "2"
        or identity["product_version"] != __version__
        or len(identity["source_revision"]) != 40
        or identity["product_build_id"]
        != f"seektalent-{__version__}+{identity['source_revision']}"
        or len(identity["fixture_sha256"]) != 64
    ):
        raise ValueError("invalid delivery identity")
    for platform_name, manifest in zip(PLATFORMS, manifests, strict=True):
        manifest_fixture = manifest.get("acceptance_fixture")
        if isinstance(manifest_fixture, dict):
            manifest_fixture = cast(dict[str, object], manifest_fixture)
        candidate = {
            "schema_version": str(manifest.get("schema_version")),
            "product_version": str(manifest.get("product_version")),
            "source_revision": str(manifest.get("source_revision")),
            "product_build_id": str(manifest.get("product_build_id")),
            "fixture_sha256": str(
                manifest_fixture.get("sha256")
                if isinstance(manifest_fixture, dict)
                else None
            ),
        }
        if candidate != identity or manifest.get("platform") != platform_name:
            raise ValueError("three-platform delivery identity mismatch")
    return artifacts, identity


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote one exact three-platform Domi delivery.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()
    identity = promote_delivery(dist_dir=args.dist_dir, build_dir=args.build_dir)
    print(json.dumps(identity, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
