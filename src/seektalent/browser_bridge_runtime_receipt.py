from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from seektalent.browser_bridge_manifest import (
    BrowserBridgeManifestError,
    BrowserBridgeRequirement,
)


WTSCLI_PACKAGE_ARCHIVE_FILENAME = ".seektalent-wtscli-package.tgz"
WTSCLI_PACKAGE_RECEIPT_FILENAME = ".seektalent-wtscli-package-receipt.json"
WTSCLI_PACKAGE_RECEIPT_SCHEMA = "seektalent.wtscli_package_receipt.v1"
_MAX_RUNTIME_FILES = 20_000
_MAX_RUNTIME_UNPACKED_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RuntimePackageFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimePackageReceipt:
    source_size: int
    source_sha256: str
    tree_sha256: str
    files: tuple[RuntimePackageFile, ...]


def bind_runtime_package_receipt(
    *,
    runtime_dir: Path,
    runtime_package: Path,
    requirement: BrowserBridgeRequirement,
) -> None:
    archive_path = runtime_dir / WTSCLI_PACKAGE_ARCHIVE_FILENAME
    receipt_path = runtime_dir / WTSCLI_PACKAGE_RECEIPT_FILENAME
    try:
        archive_path.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        archive_path.write_bytes(runtime_package.read_bytes())
        receipt = runtime_package_receipt(archive_path, requirement=requirement)
        receipt_path.write_bytes(_receipt_bytes(receipt, requirement=requirement))
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    verify_installed_runtime_package(runtime_dir, requirement=requirement)


def verify_installed_runtime_package(
    runtime_dir: Path,
    *,
    requirement: BrowserBridgeRequirement,
) -> None:
    archive_path = runtime_dir / WTSCLI_PACKAGE_ARCHIVE_FILENAME
    receipt_path = runtime_dir / WTSCLI_PACKAGE_RECEIPT_FILENAME
    package_dir = (
        runtime_dir
        / "node_modules"
        / requirement.runtime_identity.package.name
    )
    try:
        if (
            archive_path.is_symlink()
            or receipt_path.is_symlink()
            or not archive_path.is_file()
            or not receipt_path.is_file()
        ):
            raise BrowserBridgeManifestError("integrity_failed")
        expected = runtime_package_receipt(archive_path, requirement=requirement)
        if receipt_path.read_bytes() != _receipt_bytes(
            expected,
            requirement=requirement,
        ):
            raise BrowserBridgeManifestError("integrity_failed")
        if _installed_package_files(package_dir) != expected.files:
            raise BrowserBridgeManifestError("integrity_failed")
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc


def runtime_package_receipt(
    runtime_package: Path,
    *,
    requirement: BrowserBridgeRequirement,
) -> RuntimePackageReceipt:
    try:
        source_size = runtime_package.stat().st_size
        source_sha256 = _file_sha256(runtime_package)
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    if (
        source_size != requirement.cli.size
        or source_sha256 != requirement.cli.sha256
    ):
        raise BrowserBridgeManifestError("integrity_failed")

    seen: set[str] = set()
    files: list[RuntimePackageFile] = []
    total_size = 0
    try:
        archive = tarfile.open(runtime_package, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    with archive:
        try:
            for member in archive:
                relative = _runtime_member_path(member.name)
                if relative is None:
                    if member.isdir() and member.name.rstrip("/") == "package":
                        continue
                    raise BrowserBridgeManifestError("integrity_failed")
                collision_key = relative.as_posix().casefold()
                if collision_key in seen:
                    raise BrowserBridgeManifestError("integrity_failed")
                seen.add(collision_key)
                if member.isdir():
                    continue
                if not member.isfile() or member.size < 0:
                    raise BrowserBridgeManifestError("integrity_failed")
                total_size += member.size
                if (
                    len(files) >= _MAX_RUNTIME_FILES
                    or total_size > _MAX_RUNTIME_UNPACKED_BYTES
                ):
                    raise BrowserBridgeManifestError("integrity_failed")
                source = archive.extractfile(member)
                if source is None:
                    raise BrowserBridgeManifestError("integrity_failed")
                digest = hashlib.sha256()
                consumed = 0
                with source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        consumed += len(chunk)
                        digest.update(chunk)
                if consumed != member.size:
                    raise BrowserBridgeManifestError("integrity_failed")
                files.append(
                    RuntimePackageFile(
                        path=relative.as_posix(),
                        size=member.size,
                        sha256=digest.hexdigest(),
                    )
                )
        except (OSError, tarfile.TarError) as exc:
            raise BrowserBridgeManifestError("integrity_failed") from exc
    ordered = tuple(sorted(files, key=lambda item: item.path))
    tree_text = "".join(
        f"{item.sha256}  {item.size}  {item.path}\n"
        for item in ordered
    )
    return RuntimePackageReceipt(
        source_size=source_size,
        source_sha256=source_sha256,
        tree_sha256=hashlib.sha256(tree_text.encode()).hexdigest(),
        files=ordered,
    )


def _installed_package_files(package_dir: Path) -> tuple[RuntimePackageFile, ...]:
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise BrowserBridgeManifestError("integrity_failed")
    files: list[RuntimePackageFile] = []
    total_size = 0
    try:
        for candidate in package_dir.rglob("*"):
            if candidate.is_symlink():
                raise BrowserBridgeManifestError("integrity_failed")
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(package_dir).as_posix()
            total_size += candidate.stat().st_size
            if (
                len(files) >= _MAX_RUNTIME_FILES
                or total_size > _MAX_RUNTIME_UNPACKED_BYTES
            ):
                raise BrowserBridgeManifestError("integrity_failed")
            files.append(
                RuntimePackageFile(
                    path=relative,
                    size=candidate.stat().st_size,
                    sha256=_file_sha256(candidate),
                )
            )
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    return tuple(sorted(files, key=lambda item: item.path))


def _receipt_bytes(
    receipt: RuntimePackageReceipt,
    *,
    requirement: BrowserBridgeRequirement,
) -> bytes:
    payload = {
        "schemaVersion": WTSCLI_PACKAGE_RECEIPT_SCHEMA,
        "package": requirement.cli.package,
        "version": requirement.cli.version,
        "source": {
            "size": receipt.source_size,
            "sha256": receipt.source_sha256,
        },
        "treeSha256": receipt.tree_sha256,
        "files": [
            {
                "path": item.path,
                "size": item.size,
                "sha256": item.sha256,
            }
            for item in receipt.files
        ],
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def _runtime_member_path(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.parts[0] != "package":
        return None
    relative = PurePosixPath(*path.parts[1:])
    if (
        not relative.parts
        or "\\" in value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None
    return relative


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "WTSCLI_PACKAGE_ARCHIVE_FILENAME",
    "WTSCLI_PACKAGE_RECEIPT_FILENAME",
    "bind_runtime_package_receipt",
    "verify_installed_runtime_package",
]
