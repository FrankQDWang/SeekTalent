from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from typing import cast
import zipfile
from pathlib import Path

from seektalent.release_source import source_revision
from seektalent.version import __version__


ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("macos-arm64", "macos-x86_64", "windows-x64")
DIST_ENTRIES = frozenset({"README.md", "active", "last-version", "tmp"})


def stage_delivery(
    *,
    dist_dir: Path,
    build_dir: Path,
    expected_source_revision: str | None = None,
) -> dict[str, str]:
    """Validate one native build set and publish only its ZIPs to tmp."""
    dist_dir = dist_dir.resolve(strict=True)
    build_dir = build_dir.resolve(strict=True)
    archives, identity = _validated_build_artifacts(build_dir)
    _require_source_revision(identity, expected_source_revision)
    _require_dist_layout(dist_dir)
    _replace_archive_directory(dist_dir / "tmp", archives)
    return identity


def promote_delivery(
    *,
    dist_dir: Path,
    expected_source_revision: str | None = None,
) -> dict[str, str]:
    """Move the tested tmp candidate to active and rotate active to last."""
    dist_dir = dist_dir.resolve(strict=True)
    _require_dist_layout(dist_dir)
    staged = _archive_paths(dist_dir / "tmp", allow_empty=False)
    identity = _validated_delivery_archives(staged)
    _require_source_revision(identity, expected_source_revision)

    active_dir = dist_dir / "active"
    active = _archive_paths(active_dir, allow_empty=True)
    if active:
        _validated_delivery_archives(active, expected_version=None)
        _replace_archive_directory(dist_dir / "last-version", active)
    _replace_archive_directory(active_dir, staged)
    _replace_archive_directory(dist_dir / "tmp", ())
    return identity


def _validated_build_artifacts(
    build_dir: Path,
) -> tuple[tuple[Path, ...], dict[str, str]]:
    wheel = build_dir / f"seektalent-{__version__}-py3-none-any.whl"
    sdist = build_dir / f"seektalent-{__version__}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise FileNotFoundError("the exact wheel and sdist are required")

    archives: list[Path] = []
    for platform_name in PLATFORMS:
        archive = build_dir / (
            f"seektalent-offline-{__version__}-{platform_name}-py313.zip"
        )
        checksum = archive.with_name(f"{archive.name}.sha256")
        if not archive.is_file() or not checksum.is_file():
            raise FileNotFoundError(
                f"missing delivery artifact for {platform_name}"
            )
        expected_line = f"{_sha256(archive)}  {archive.name}\n"
        if checksum.read_text(encoding="utf-8") != expected_line:
            raise ValueError(f"archive checksum mismatch for {platform_name}")
        archives.append(archive)

    identity = _validated_delivery_archives(tuple(archives))
    if identity["seektalent_wheel_sha256"] != _sha256(wheel):
        raise ValueError("top-level wheel does not match delivery archives")
    return tuple(archives), identity


def _validated_delivery_archives(
    archives: tuple[Path, ...],
    *,
    expected_version: str | None = __version__,
) -> dict[str, str]:
    if len(archives) != len(PLATFORMS):
        raise ValueError("delivery must contain exactly three platform ZIPs")

    identity: dict[str, str] | None = None
    seen_platforms: set[str] = set()
    for archive in archives:
        if not archive.is_file() or archive.suffix != ".zip":
            raise ValueError("delivery directory may contain only ZIP archives")
        with zipfile.ZipFile(archive) as delivery:
            if delivery.testzip() is not None:
                raise ValueError(f"corrupt delivery archive: {archive.name}")
            manifest = json.loads(
                delivery.read(f"{archive.stem}/delivery-manifest.json")
            )
        fixture = manifest.get("acceptance_fixture")
        if not isinstance(fixture, dict):
            raise ValueError("acceptance fixture identity is missing")
        fixture = cast(dict[str, object], fixture)
        platform_name = str(manifest.get("platform"))
        candidate = {
            "schema_version": str(manifest.get("schema_version")),
            "product_version": str(manifest.get("product_version")),
            "source_revision": str(manifest.get("source_revision")),
            "product_build_id": str(manifest.get("product_build_id")),
            "fixture_sha256": str(fixture.get("sha256")),
            "seektalent_wheel_sha256": str(
                manifest.get("seektalent_wheel_sha256")
            ),
        }
        expected_name = (
            f"seektalent-offline-{candidate['product_version']}-"
            f"{platform_name}-py313.zip"
        )
        if archive.name != expected_name:
            raise ValueError("delivery archive name does not match manifest")
        if identity is None:
            identity = candidate
        elif candidate != identity:
            raise ValueError("delivery identity mismatch")
        if platform_name in seen_platforms:
            raise ValueError("delivery platform is duplicated")
        seen_platforms.add(platform_name)

    assert identity is not None
    product_version = identity["product_version"]
    if (
        identity["schema_version"] != "2"
        or (
            expected_version is not None
            and product_version != expected_version
        )
        or len(identity["source_revision"]) != 40
        or identity["product_build_id"]
        != f"seektalent-{product_version}+{identity['source_revision']}"
        or len(identity["fixture_sha256"]) != 64
        or len(identity["seektalent_wheel_sha256"]) != 64
        or seen_platforms != set(PLATFORMS)
    ):
        raise ValueError("invalid delivery identity")
    return identity


def _require_dist_layout(dist_dir: Path) -> None:
    unexpected = sorted(
        path.name for path in dist_dir.iterdir() if path.name not in DIST_ENTRIES
    )
    if unexpected:
        raise ValueError(f"unexpected dist entries: {', '.join(unexpected)}")
    readme = dist_dir / "README.md"
    if not readme.is_file():
        raise FileNotFoundError("dist README.md is required")
    for name in ("active", "last-version", "tmp"):
        path = dist_dir / name
        if not path.is_dir():
            raise FileNotFoundError(f"dist directory is required: {name}")


def _archive_paths(
    directory: Path,
    *,
    allow_empty: bool,
) -> tuple[Path, ...]:
    entries = tuple(sorted(directory.iterdir()))
    if not entries and allow_empty:
        return ()
    if not entries:
        raise ValueError(f"delivery directory is empty: {directory.name}")
    if any(not path.is_file() or path.suffix != ".zip" for path in entries):
        raise ValueError(
            f"delivery directory may contain only ZIP archives: {directory.name}"
        )
    return entries


def _replace_archive_directory(
    target: Path,
    sources: tuple[Path, ...],
) -> None:
    parent = target.parent
    staged = Path(tempfile.mkdtemp(prefix=f".{target.name}.next-", dir=parent))
    backup = parent / f".{target.name}.previous"
    try:
        for source in sources:
            shutil.copy2(source, staged / source.name)
        if backup.exists():
            raise FileExistsError(f"stale delivery backup exists: {backup}")
        target.rename(backup)
        staged.rename(target)
        shutil.rmtree(backup)
    except Exception:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def _require_source_revision(
    identity: dict[str, str],
    expected_source_revision: str | None,
) -> None:
    if (
        expected_source_revision is not None
        and identity["source_revision"] != expected_source_revision
    ):
        raise ValueError("delivery source revision does not match checkout")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage or promote one exact three-platform Domi delivery."
    )
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build-dir", type=Path)
    action.add_argument("--promote-staged", action="store_true")
    args = parser.parse_args()
    revision = source_revision(ROOT)
    if args.build_dir is not None:
        identity = stage_delivery(
            dist_dir=args.dist_dir,
            build_dir=args.build_dir,
            expected_source_revision=revision,
        )
    else:
        identity = promote_delivery(
            dist_dir=args.dist_dir,
            expected_source_revision=revision,
        )
    print(json.dumps(identity, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
