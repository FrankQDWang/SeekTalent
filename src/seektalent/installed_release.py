from __future__ import annotations

import errno
import os
import platform
import stat
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from seektalent.release_manifest import (
    ComponentV1,
    FileRefV1,
    ReleaseManifestV1,
    TargetV1,
    parse_release_manifest,
    release_manifest_digest,
)


INSTALLED_MANIFEST_RELATIVE_PATH = Path("release/release-manifest.json")
SIDECAR_COMPONENT_ID = "liepin_execution_sidecar"
_HASH_CHUNK_SIZE = 1024 * 1024
# A release manifest is metadata, not payload. One MiB leaves ample room for
# file closure while bounding strict-JSON parser input and transient memory.
MAX_INSTALLED_MANIFEST_BYTES = 1024 * 1024
# The sidecar is one desktop executable. 512 MiB leaves packaging headroom
# while bounding point-in-time hashing when installed content is untrusted.
MAX_INSTALLED_SIDECAR_BYTES = 512 * 1024 * 1024


class InstalledReleaseReason(StrEnum):
    INVALID_SLOT_ROOT = "invalid_slot_root"
    PATH_ESCAPE = "path_escape"
    SYMLINK = "symlink"
    PATH_CHANGED = "path_changed"
    NOT_REGULAR_FILE = "not_regular_file"
    HARDLINK = "hardlink"
    HOST_UNSUPPORTED = "host_unsupported"
    TARGET_MISMATCH = "target_mismatch"
    SIDECAR_DECLARATION_INVALID = "sidecar_declaration_invalid"
    FILE_SIZE_MISMATCH = "file_size_mismatch"
    FILE_SIZE_LIMIT_EXCEEDED = "file_size_limit_exceeded"
    FILE_ACCESS_DENIED = "file_access_denied"
    FILE_MODE_MISMATCH = "file_mode_mismatch"
    FILE_DIGEST_MISMATCH = "file_digest_mismatch"


class InstalledReleaseError(ValueError):
    def __init__(self, reason: InstalledReleaseReason, path: Path | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class InstalledSidecarExecutableResolution:
    """A point-in-time schema, host, and content match for an installed sidecar.

    This value records that the unsigned manifest was schema-valid, its target
    matched the inspecting host, and the executable content matched while it was
    inspected. It is not signature-authenticated or release-authorized, and it
    does not bind the inspected filesystem object to a later process spawn.
    """

    slot_root: Path
    manifest_path: Path
    executable_path: Path
    manifest_id: str
    manifest_sha256: str
    product_build_id: str
    target: TargetV1
    executable_size_bytes: int
    executable_sha256: str


@dataclass(frozen=True, slots=True)
class _HostPlatform:
    os: str
    arch: str
    build: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class _PathSnapshot:
    path: Path
    device: int
    inode: int
    mode: int
    link_count: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, path: Path, value: os.stat_result) -> _PathSnapshot:
        return cls(
            path=path,
            device=value.st_dev,
            inode=value.st_ino,
            mode=value.st_mode,
            link_count=value.st_nlink,
            size=value.st_size,
            mtime_ns=value.st_mtime_ns,
            ctime_ns=value.st_ctime_ns,
        )


def resolve_installed_sidecar_executable(slot_root: Path) -> InstalledSidecarExecutableResolution:
    """Resolve the fixed installed sidecar entrypoint and inspect its current bytes."""
    root = _validate_slot_root(slot_root)
    manifest_path = root / INSTALLED_MANIFEST_RELATIVE_PATH
    manifest_bytes = _read_stable_regular_file(root, manifest_path)
    manifest = parse_release_manifest(manifest_bytes)
    _require_host_match(manifest.target)
    component, file_ref = _select_sidecar_file(manifest)
    executable_path = root / manifest.payload_root / component.root_path / file_ref.path
    executable_digest = _inspect_executable(root, executable_path, file_ref)
    return InstalledSidecarExecutableResolution(
        slot_root=root,
        manifest_path=manifest_path,
        executable_path=executable_path,
        manifest_id=manifest.manifest_id,
        manifest_sha256=release_manifest_digest(manifest),
        product_build_id=manifest.product_build_id,
        target=manifest.target,
        executable_size_bytes=file_ref.size_bytes,
        executable_sha256=executable_digest,
    )


def _validate_slot_root(slot_root: Path) -> Path:
    if not isinstance(slot_root, Path) or not slot_root.is_absolute() or ".." in slot_root.parts:
        raise InstalledReleaseError(InstalledReleaseReason.INVALID_SLOT_ROOT)
    try:
        root_stat = os.lstat(slot_root)
    except OSError as exc:
        raise InstalledReleaseError(InstalledReleaseReason.INVALID_SLOT_ROOT, slot_root) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise InstalledReleaseError(InstalledReleaseReason.INVALID_SLOT_ROOT, slot_root)
    return slot_root


def _select_sidecar_file(manifest: ReleaseManifestV1) -> tuple[ComponentV1, FileRefV1]:
    component = next(
        (item for item in manifest.components if item.component_id == SIDECAR_COMPONENT_ID),
        None,
    )
    if component is None or component.platform != manifest.target or len(component.entrypoints) != 1:
        raise InstalledReleaseError(InstalledReleaseReason.SIDECAR_DECLARATION_INVALID)
    entrypoint = component.entrypoints[0]
    file_ref = next((item for item in component.files if item.path == entrypoint), None)
    if file_ref is None or not file_ref.executable or file_ref.mode_class != "regular_executable":
        raise InstalledReleaseError(InstalledReleaseReason.SIDECAR_DECLARATION_INVALID)
    return component, file_ref


def _require_host_match(target: TargetV1) -> None:
    host = _current_host_platform()
    if (host.os, host.arch) != (target.os, target.arch):
        raise InstalledReleaseError(InstalledReleaseReason.TARGET_MISMATCH)
    minimum = _parse_build(target.min_os_build)
    maximum = _parse_build(target.max_os_build)
    if not minimum <= host.build <= maximum:
        raise InstalledReleaseError(InstalledReleaseReason.TARGET_MISMATCH)


def _current_host_platform() -> _HostPlatform:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        host_os = "macos"
        raw_build = platform.mac_ver()[0]
    elif system == "Windows":
        host_os = "windows"
        raw_build = platform.win32_ver()[1]
    else:
        raise InstalledReleaseError(InstalledReleaseReason.HOST_UNSUPPORTED)
    arch = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine)
    if arch is None:
        raise InstalledReleaseError(InstalledReleaseReason.HOST_UNSUPPORTED)
    try:
        build = _parse_build(raw_build)
    except ValueError as exc:
        raise InstalledReleaseError(InstalledReleaseReason.HOST_UNSUPPORTED) from exc
    return _HostPlatform(os=host_os, arch=arch, build=build)


def _parse_build(value: str) -> tuple[int, int, int, int]:
    parts = value.split(".")
    if not 2 <= len(parts) <= 4 or any(not part.isascii() or not part.isdecimal() for part in parts):
        raise ValueError(value)
    numbers = [int(part) for part in parts]
    numbers.extend([0] * (4 - len(numbers)))
    return numbers[0], numbers[1], numbers[2], numbers[3]


def _inspect_executable(root: Path, path: Path, file_ref: FileRefV1) -> str:
    before = _snapshot_path_chain(root, path)
    final = before[-1]
    _require_regular_single_link(final)
    if final.size != file_ref.size_bytes:
        raise InstalledReleaseError(InstalledReleaseReason.FILE_SIZE_MISMATCH, path)
    if os.name == "posix":
        _require_effective_executable(path, final)

    descriptor = _open_readonly(path)
    try:
        opened = _PathSnapshot.from_stat(path, os.fstat(descriptor))
        _require_same_snapshot(final, opened)
        _require_size_within_limit(opened, MAX_INSTALLED_SIDECAR_BYTES)
        actual_digest = _hash_exact_snapshot(descriptor, opened.size, path)
        after_read = _PathSnapshot.from_stat(path, os.fstat(descriptor))
        _require_same_snapshot(opened, after_read)
    finally:
        os.close(descriptor)
    _require_path_chain_unchanged(before, _snapshot_path_chain(root, path))
    if actual_digest != file_ref.sha256:
        raise InstalledReleaseError(InstalledReleaseReason.FILE_DIGEST_MISMATCH, path)
    return actual_digest


def _read_stable_regular_file(root: Path, path: Path) -> bytes:
    before = _snapshot_path_chain(root, path)
    final = before[-1]
    _require_regular_single_link(final)
    descriptor = _open_readonly(path)
    try:
        opened = _PathSnapshot.from_stat(path, os.fstat(descriptor))
        _require_same_snapshot(final, opened)
        _require_size_within_limit(opened, MAX_INSTALLED_MANIFEST_BYTES)
        content = _read_exact_snapshot(descriptor, opened.size, path)
        after_read = _PathSnapshot.from_stat(path, os.fstat(descriptor))
        _require_same_snapshot(opened, after_read)
    finally:
        os.close(descriptor)
    _require_path_chain_unchanged(before, _snapshot_path_chain(root, path))
    return content


def _require_effective_executable(path: Path, snapshot: _PathSnapshot) -> None:
    if snapshot.mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) == 0:
        raise InstalledReleaseError(InstalledReleaseReason.FILE_MODE_MISMATCH, path)
    supports_effective_ids = getattr(os, "supports_effective_ids", set())
    if os.access not in supports_effective_ids:
        raise InstalledReleaseError(InstalledReleaseReason.FILE_MODE_MISMATCH, path)
    try:
        allowed = os.access(path, os.X_OK, effective_ids=True)
    except (NotImplementedError, OSError, TypeError) as exc:
        raise InstalledReleaseError(InstalledReleaseReason.FILE_MODE_MISMATCH, path) from exc
    if not allowed:
        raise InstalledReleaseError(InstalledReleaseReason.FILE_MODE_MISMATCH, path)


def _require_size_within_limit(snapshot: _PathSnapshot, limit: int) -> None:
    if snapshot.size > limit:
        raise InstalledReleaseError(InstalledReleaseReason.FILE_SIZE_LIMIT_EXCEEDED, snapshot.path)


def _read_exact_snapshot(descriptor: int, size: int, path: Path) -> bytes:
    content = bytearray()
    remaining = size
    while remaining:
        chunk = _read_descriptor(descriptor, min(remaining, _HASH_CHUNK_SIZE), path)
        if not chunk:
            raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path)
        content.extend(chunk)
        remaining -= len(chunk)
    if _read_descriptor(descriptor, 1, path):
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path)
    return bytes(content)


def _hash_exact_snapshot(descriptor: int, size: int, path: Path) -> str:
    digest = sha256()
    remaining = size
    while remaining:
        chunk = _read_descriptor(descriptor, min(remaining, _HASH_CHUNK_SIZE), path)
        if not chunk:
            raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path)
        digest.update(chunk)
        remaining -= len(chunk)
    if _read_descriptor(descriptor, 1, path):
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path)
    return digest.hexdigest()


def _read_descriptor(descriptor: int, size: int, path: Path) -> bytes:
    try:
        return os.read(descriptor, size)
    except OSError as exc:
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path) from exc


def _snapshot_path_chain(root: Path, path: Path) -> tuple[_PathSnapshot, ...]:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise InstalledReleaseError(InstalledReleaseReason.PATH_ESCAPE, path) from exc
    if relative.is_absolute() or ".." in relative.parts:
        raise InstalledReleaseError(InstalledReleaseReason.PATH_ESCAPE, path)
    snapshots: list[_PathSnapshot] = []
    current = root
    parts = (Path("."), *relative.parts)
    for index, part in enumerate(parts):
        if index:
            current /= part
        try:
            value = os.lstat(current)
        except OSError as exc:
            raise InstalledReleaseError(InstalledReleaseReason.NOT_REGULAR_FILE, current) from exc
        if _is_link_like(current, value):
            raise InstalledReleaseError(InstalledReleaseReason.SYMLINK, current)
        if index < len(parts) - 1 and not stat.S_ISDIR(value.st_mode):
            raise InstalledReleaseError(InstalledReleaseReason.NOT_REGULAR_FILE, current)
        snapshots.append(_PathSnapshot.from_stat(current, value))
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise InstalledReleaseError(InstalledReleaseReason.PATH_ESCAPE, path) from exc
    return tuple(snapshots)


def _require_regular_single_link(snapshot: _PathSnapshot) -> None:
    if not stat.S_ISREG(snapshot.mode):
        raise InstalledReleaseError(InstalledReleaseReason.NOT_REGULAR_FILE, snapshot.path)
    if snapshot.link_count != 1:
        raise InstalledReleaseError(InstalledReleaseReason.HARDLINK, snapshot.path)


def _open_readonly(path: Path) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    if os.name == "posix":
        flags |= os.O_NONBLOCK
    try:
        return os.open(path, flags)
    except OSError as exc:
        if isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM}:
            raise InstalledReleaseError(InstalledReleaseReason.FILE_ACCESS_DENIED, path) from exc
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path) from exc


def _is_link_like(path: Path, value: os.stat_result) -> bool:
    if stat.S_ISLNK(value.st_mode):
        return True
    if os.name != "nt":
        return False
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(value, "st_file_attributes", 0) & reparse_attribute:
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction is not None and is_junction(path))


def _require_same_snapshot(expected: _PathSnapshot, actual: _PathSnapshot) -> None:
    if expected != actual:
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, actual.path)


def _require_path_chain_unchanged(
    before: tuple[_PathSnapshot, ...],
    after: tuple[_PathSnapshot, ...],
) -> None:
    if before != after:
        path = after[-1].path if after else before[-1].path
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path)
