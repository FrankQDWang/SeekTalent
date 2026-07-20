"""Stable installed-filesystem evidence shared by release admission and slot lifecycle."""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path


_READ_CHUNK_SIZE = 1024 * 1024


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


def _read_stable_regular_file(root: Path, path: Path, *, limit: int) -> bytes:
    before = _snapshot_path_chain(root, path)
    final = before[-1]
    _require_regular_single_link(final)
    descriptor = _open_readonly(path)
    try:
        opened = _PathSnapshot.from_stat(path, os.fstat(descriptor))
        _require_same_snapshot(final, opened)
        _require_size_within_limit(opened, limit)
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
        chunk = _read_descriptor(descriptor, min(remaining, _READ_CHUNK_SIZE), path)
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
        chunk = _read_descriptor(descriptor, min(remaining, _READ_CHUNK_SIZE), path)
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
            if _is_permission_error(exc):
                raise InstalledReleaseError(
                    InstalledReleaseReason.FILE_ACCESS_DENIED,
                    current,
                ) from exc
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
        if _is_permission_error(exc):
            raise InstalledReleaseError(InstalledReleaseReason.FILE_ACCESS_DENIED, path) from exc
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path) from exc


def _is_permission_error(error: OSError) -> bool:
    return isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.EPERM}


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
    if not _same_snapshot(expected, actual):
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, actual.path)


def _require_path_chain_unchanged(
    before: tuple[_PathSnapshot, ...],
    after: tuple[_PathSnapshot, ...],
) -> None:
    if len(before) != len(after) or any(
        not _same_snapshot(expected, actual) for expected, actual in zip(before, after, strict=True)
    ):
        path = after[-1].path if after else before[-1].path
        raise InstalledReleaseError(InstalledReleaseReason.PATH_CHANGED, path)


def _same_snapshot(expected: _PathSnapshot, actual: _PathSnapshot) -> bool:
    if os.name == "nt":
        # Windows can materialize ctime after an installed file or coordination
        # file closes and a reader opens it. It is therefore not identity
        # evidence; object identity, size, and content timestamp still bind.
        return (
            expected.path,
            expected.device,
            expected.inode,
            expected.mode,
            expected.link_count,
            expected.size,
            expected.mtime_ns,
        ) == (
            actual.path,
            actual.device,
            actual.inode,
            actual.mode,
            actual.link_count,
            actual.size,
            actual.mtime_ns,
        )
    return expected == actual


def is_permission_error(error: OSError) -> bool:
    """Return whether an OS error means the installed path could not be read."""
    return _is_permission_error(error)


def read_stable_regular_file(root: Path, path: Path, *, limit: int) -> bytes:
    """Read one fixed installed file after binding its path chain and descriptor snapshot."""
    return _read_stable_regular_file(root, path, limit=limit)


def require_real_directory(root: Path, path: Path) -> None:
    """Require one fixed path beneath an installed root to be a real directory."""
    snapshots = _snapshot_path_chain(root, path)
    if not stat.S_ISDIR(snapshots[-1].mode):
        raise InstalledReleaseError(InstalledReleaseReason.NOT_REGULAR_FILE, path)


def open_stable_regular_for_update(root: Path, path: Path) -> int:
    """Open a regular installed coordination file after binding its path-chain evidence."""
    before = _snapshot_path_chain(root, path)
    _require_regular_single_link(before[-1])
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        opened = _PathSnapshot.from_stat(path, os.fstat(descriptor))
        _require_same_snapshot(before[-1], opened)
        _require_path_chain_unchanged(before, _snapshot_path_chain(root, path))
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor
