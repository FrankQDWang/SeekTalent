from __future__ import annotations

import errno
import os
import platform
import re
import stat
import sys
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, LiteralString, Never, Self, SupportsIndex, TypeVar

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from seektalent.release_manifest import (
    ComponentV1,
    FileRefV1,
    ProtocolFactV1,
    ProductBuildId,
    ReleaseManifestV1,
    Sha256,
    TargetV1,
    parse_release_manifest,
    release_manifest_digest,
)
from seektalent.release_signing import (
    ReleaseManifestTrustPolicyV1,
    parse_release_manifest_signature,
    verify_release_manifest_signature,
)
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


INSTALLED_MANIFEST_RELATIVE_PATH = Path("release/release-manifest.json")
INSTALLED_SIGNATURE_RELATIVE_PATH = Path("release/signatures/release-manifest.sig")
INSTALLATION_ID_RELATIVE_PATH = Path("control/installation-id")
ACTIVE_SLOT_POINTER_RELATIVE_PATH = Path("control/active-slot.json")
ACTIVE_SLOT_LOCK_RELATIVE_PATH = Path("control/active-slot.lock")
SLOT_LOCK_RELATIVE_PATHS = {
    "A": Path("control/slot-A.lock"),
    "B": Path("control/slot-B.lock"),
}
SLOT_ROOT_RELATIVE_PATHS = {"A": Path("slots/A"), "B": Path("slots/B")}
SIDECAR_COMPONENT_ID = "liepin_execution_sidecar"
_ADMISSION_FACTORY_TOKEN = object()
_HASH_CHUNK_SIZE = 1024 * 1024
# A release manifest is metadata, not payload. One MiB leaves ample room for
# file closure while bounding strict-JSON parser input and transient memory.
MAX_INSTALLED_MANIFEST_BYTES = 1024 * 1024
# The sidecar is one desktop executable. 512 MiB leaves packaging headroom
# while bounding point-in-time hashing when installed content is untrusted.
MAX_INSTALLED_SIDECAR_BYTES = 512 * 1024 * 1024
MAX_ACTIVE_SLOT_POINTER_BYTES = 64 * 1024
_OPAQUE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-=]{0,127}\Z")
_UTC_RFC3339_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


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


@dataclass(frozen=True, slots=True, weakref_slot=True, init=False)
class AuthenticatedInstalledSidecarLaunch:
    """Launch admission derived exclusively from one verified installed manifest."""

    resolution: InstalledSidecarExecutableResolution
    manifest_id: str
    manifest_sha256: str
    product_build_id: str
    main_application_build_id: str
    main_application_tree_sha256: str
    sidecar_build_id: str
    sidecar_tree_sha256: str
    sidecar_executable_sha256: str
    source_port_protocol: ProtocolFactV1
    signer_key_id: str
    trust_policy_id: str
    trust_policy_revision: int
    _factory_token: object = field(init=False, repr=False, compare=False)

    @property
    def slot_root(self) -> Path:
        return self.resolution.slot_root

    @property
    def manifest_path(self) -> Path:
        return self.resolution.manifest_path

    @property
    def executable_path(self) -> Path:
        return self.resolution.executable_path

    @property
    def target(self) -> TargetV1:
        return self.resolution.target

    def __init__(
        self,
        *,
        resolution: InstalledSidecarExecutableResolution,
        manifest_id: str,
        manifest_sha256: str,
        product_build_id: str,
        main_application_build_id: str,
        main_application_tree_sha256: str,
        sidecar_build_id: str,
        sidecar_tree_sha256: str,
        sidecar_executable_sha256: str,
        source_port_protocol: ProtocolFactV1,
        signer_key_id: str,
        trust_policy_id: str,
        trust_policy_revision: int,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _ADMISSION_FACTORY_TOKEN:
            raise TypeError("AuthenticatedInstalledSidecarLaunch is factory-only")
        object.__setattr__(self, "resolution", resolution)
        object.__setattr__(self, "manifest_id", manifest_id)
        object.__setattr__(self, "manifest_sha256", manifest_sha256)
        object.__setattr__(self, "product_build_id", product_build_id)
        object.__setattr__(self, "main_application_build_id", main_application_build_id)
        object.__setattr__(self, "main_application_tree_sha256", main_application_tree_sha256)
        object.__setattr__(self, "sidecar_build_id", sidecar_build_id)
        object.__setattr__(self, "sidecar_tree_sha256", sidecar_tree_sha256)
        object.__setattr__(self, "sidecar_executable_sha256", sidecar_executable_sha256)
        object.__setattr__(self, "source_port_protocol", source_port_protocol)
        object.__setattr__(self, "signer_key_id", signer_key_id)
        object.__setattr__(self, "trust_policy_id", trust_policy_id)
        object.__setattr__(self, "trust_policy_revision", trust_policy_revision)
        object.__setattr__(self, "_factory_token", _factory_token)

    def _is_factory_admission(self) -> bool:
        return self._factory_token is _ADMISSION_FACTORY_TOKEN

    def __copy__(self) -> AuthenticatedInstalledSidecarLaunch:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> AuthenticatedInstalledSidecarLaunch:
        del memo
        return self


def admit_installed_sidecar_launch(
    slot_root: Path,
    trust_policy: ReleaseManifestTrustPolicyV1,
    verification_time: datetime,
) -> AuthenticatedInstalledSidecarLaunch:
    """Authenticate and inspect the fixed installed sidecar launch identity."""
    root = _validate_slot_root(slot_root)
    manifest_path = root / INSTALLED_MANIFEST_RELATIVE_PATH
    signature_path = root / INSTALLED_SIGNATURE_RELATIVE_PATH
    manifest = parse_release_manifest(_read_stable_regular_file(root, manifest_path))
    signature = parse_release_manifest_signature(_read_stable_regular_file(root, signature_path))
    verified = verify_release_manifest_signature(signature, manifest, trust_policy, verification_time)
    resolution = _resolve_installed_sidecar_executable(root, manifest)

    main = next((item for item in manifest.components if item.component_id == "main_application"), None)
    sidecar = next((item for item in manifest.components if item.component_id == SIDECAR_COMPONENT_ID), None)
    if main is None or sidecar is None:
        raise InstalledReleaseError(InstalledReleaseReason.SIDECAR_DECLARATION_INVALID, manifest_path)
    admission = AuthenticatedInstalledSidecarLaunch(
        resolution=resolution,
        manifest_id=manifest.manifest_id,
        manifest_sha256=verified.release_manifest_sha256,
        product_build_id=manifest.product_build_id,
        main_application_build_id=main.build_id,
        main_application_tree_sha256=main.tree_sha256,
        sidecar_build_id=sidecar.build_id,
        sidecar_tree_sha256=sidecar.tree_sha256,
        sidecar_executable_sha256=resolution.executable_sha256,
        source_port_protocol=manifest.compatibility.main_sidecar.source_port_protocol,
        signer_key_id=verified.signer_key_id,
        trust_policy_id=verified.trust_policy_id,
        trust_policy_revision=verified.trust_policy_revision,
        _factory_token=_ADMISSION_FACTORY_TOKEN,
    )
    return admission


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
    return _resolve_installed_sidecar_executable(root, manifest)


def _resolve_installed_sidecar_executable(
    root: Path, manifest: ReleaseManifestV1
) -> InstalledSidecarExecutableResolution:
    manifest_path = root / INSTALLED_MANIFEST_RELATIVE_PATH
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
        raw_build = _windows_platform_build()
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


def _windows_platform_build() -> str:
    get_windows_version = getattr(sys, "getwindowsversion", None)
    if get_windows_version is None:
        raise InstalledReleaseError(InstalledReleaseReason.HOST_UNSUPPORTED)
    try:
        platform_version = get_windows_version().platform_version
    except (AttributeError, OSError) as exc:
        raise InstalledReleaseError(InstalledReleaseReason.HOST_UNSUPPORTED) from exc
    if (
        not isinstance(platform_version, tuple)
        or not 2 <= len(platform_version) <= 4
        or any(
            isinstance(part, bool) or not isinstance(part, int) or part < 0
            for part in platform_version
        )
    ):
        raise InstalledReleaseError(InstalledReleaseReason.HOST_UNSUPPORTED)
    return ".".join(str(part) for part in platform_version)


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


def _read_stable_regular_file(root: Path, path: Path, *, limit: int = MAX_INSTALLED_MANIFEST_BYTES) -> bytes:
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
        # Windows can materialize the final metadata-change timestamp after a
        # completed write closes and the read handle opens. Its value therefore
        # cannot distinguish a stable pointer from a just-committed one. The
        # remaining fields still bind the object, size, and content timestamp.
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


class ActiveSlotPointerReason(StrEnum):
    RAW_INPUT_REQUIRED = "raw_input_required"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    ILLEGAL_NUMBER = "illegal_number"
    INVALID_UNICODE = "invalid_unicode"
    ROOT_NOT_OBJECT = "root_not_object"
    UNKNOWN_FIELD = "unknown_field"
    SCHEMA_VALIDATION = "schema_validation"
    INVALID_VALUE = "invalid_value"
    NON_CANONICAL = "non_canonical"


class ActiveSlotPointerError(ValueError):
    def __init__(self, reason: ActiveSlotPointerReason, location: tuple[str | int, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason.value)


class InstalledSlotReason(StrEnum):
    INSTALLED_ROOT_INVALID = "installed_root_invalid"
    ACTIVE_SLOT_POINTER_INVALID = "active_slot_pointer_invalid"
    ACTIVE_SLOT_POINTER_CHANGED = "active_slot_pointer_changed"
    SLOT_IDENTITY_MISMATCH = "slot_identity_mismatch"
    SLOT_LEASE_CONFLICT = "slot_lease_conflict"
    SLOT_LEASE_UNAVAILABLE = "slot_lease_unavailable"
    SLOT_RELEASE_FAILED = "slot_release_failed"


class InstalledSlotError(ValueError):
    def __init__(self, reason: InstalledSlotReason, path: Path | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(reason.value)


def _pointer_schema_error(reason: ActiveSlotPointerReason, message: LiteralString) -> PydanticCustomError:
    return PydanticCustomError(reason.value, message)


def _validate_pointer_identifier(value: str) -> str:
    if _OPAQUE_TOKEN_RE.fullmatch(value) is None:
        raise _pointer_schema_error(ActiveSlotPointerReason.INVALID_VALUE, "identifier has an invalid format")
    return value


def _validate_committed_at(value: str) -> str:
    if _UTC_RFC3339_RE.fullmatch(value) is None:
        raise _pointer_schema_error(
            ActiveSlotPointerReason.INVALID_VALUE,
            "committed_at must be second-precision UTC RFC3339",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise _pointer_schema_error(
            ActiveSlotPointerReason.INVALID_VALUE,
            "committed_at is not a real UTC timestamp",
        ) from exc
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise _pointer_schema_error(
            ActiveSlotPointerReason.INVALID_VALUE,
            "committed_at must be UTC",
        )
    return value


class ActiveSlotPointerV1(BaseModel):
    """The canonical, bytes-only active installed-slot selection record."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["seektalent.active-slot/v1"]
    installation_id: Annotated[str, Field(min_length=1, max_length=128)]
    physical_slot: Literal["A", "B"]
    pointer_generation: Annotated[int, Field(gt=0)]
    product_build_id: ProductBuildId
    release_manifest_sha256: Sha256
    committed_at: Annotated[str, Field(min_length=20, max_length=20)]

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        from_attributes: bool | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if not isinstance(obj, cls):
            raise ActiveSlotPointerError(ActiveSlotPointerReason.RAW_INPUT_REQUIRED)
        return BaseModel.model_validate.__func__(
            cls,
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if not isinstance(json_data, bytes):
            raise ActiveSlotPointerError(ActiveSlotPointerReason.RAW_INPUT_REQUIRED)
        return _parse_active_slot_pointer_bytes(
            cls,
            json_data,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @field_validator("installation_id")
    @classmethod
    def validate_installation_id(cls, value: str) -> str:
        return _validate_pointer_identifier(value)

    @field_validator("committed_at")
    @classmethod
    def validate_committed_at(cls, value: str) -> str:
        return _validate_committed_at(value)

    @model_validator(mode="after")
    def validate_pointer(self) -> Self:
        if self.pointer_generation > (1 << 53) - 1:
            raise _pointer_schema_error(
                ActiveSlotPointerReason.INVALID_VALUE,
                "pointer_generation must be an I-JSON safe integer",
            )
        return self


PointerModel = TypeVar("PointerModel", bound=ActiveSlotPointerV1)


def canonical_active_slot_pointer_bytes(pointer: ActiveSlotPointerV1) -> bytes:
    """Return the RFC 8785 bytes required for an active-slot pointer on disk."""
    return rfc8785.dumps(pointer.model_dump(mode="json"))


def parse_active_slot_pointer(raw: bytes) -> ActiveSlotPointerV1:
    """Parse one canonical active-slot pointer without accepting mappings or text."""
    if not isinstance(raw, bytes):
        raise ActiveSlotPointerError(ActiveSlotPointerReason.RAW_INPUT_REQUIRED)
    return _parse_active_slot_pointer_bytes(ActiveSlotPointerV1, raw)


def _parse_active_slot_pointer_bytes(
    model_cls: type[PointerModel],
    raw: bytes,
    *,
    context: object | None = None,
    by_alias: bool | None = None,
    by_name: bool | None = None,
) -> PointerModel:
    try:
        payload = strict_json_object_loads(raw)
    except StrictJsonError as exc:
        raise ActiveSlotPointerError(ActiveSlotPointerReason(exc.reason.value), exc.location) from None
    unknown_fields = set(payload) - set(model_cls.model_fields)
    if unknown_fields:
        raise ActiveSlotPointerError(ActiveSlotPointerReason.UNKNOWN_FIELD, (min(unknown_fields),))
    try:
        pointer = BaseModel.model_validate_json.__func__(
            model_cls,
            raw,
            strict=True,
            extra="forbid",
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False)[0]
        error_type = str(first["type"])
        try:
            reason = ActiveSlotPointerReason(error_type)
        except ValueError:
            reason = (
                ActiveSlotPointerReason.UNKNOWN_FIELD
                if error_type == "extra_forbidden"
                else ActiveSlotPointerReason.SCHEMA_VALIDATION
            )
        raise ActiveSlotPointerError(reason, tuple(first["loc"])) from None
    if raw != canonical_active_slot_pointer_bytes(pointer):
        raise ActiveSlotPointerError(ActiveSlotPointerReason.NON_CANONICAL)
    return pointer


@dataclass(frozen=True, slots=True)
class InstalledSlotIdentity:
    """A durable identity for one concrete release ever selected for a slot."""

    installation_id: str
    physical_slot: Literal["A", "B"]
    pointer_generation: int
    product_build_id: str
    release_manifest_sha256: str


def _pointer_identity(pointer: ActiveSlotPointerV1) -> InstalledSlotIdentity:
    return InstalledSlotIdentity(
        installation_id=pointer.installation_id,
        physical_slot=pointer.physical_slot,
        pointer_generation=pointer.pointer_generation,
        product_build_id=pointer.product_build_id,
        release_manifest_sha256=pointer.release_manifest_sha256,
    )


@dataclass(slots=True)
class _NativeSlotLock:
    path: Path
    descriptor: int | None
    platform: Literal["posix", "windows"]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        failure: OSError | None = None
        try:
            _unlock_native_slot_lock(descriptor, self.platform)
        except OSError as exc:
            failure = exc
        try:
            os.close(descriptor)
        except OSError as exc:
            failure = failure or exc
        if failure is not None:
            raise InstalledSlotError(InstalledSlotReason.SLOT_RELEASE_FAILED, self.path) from failure


@dataclass(slots=True)
class _InstalledSidecarLeaseState:
    admission: AuthenticatedInstalledSidecarLaunch
    identity: InstalledSlotIdentity
    slot_lock: _NativeSlotLock
    released: bool = False
    transferred: bool = False

    def close(self) -> None:
        if self.released:
            return
        self.released = True
        self.slot_lock.close()


_LIVE_LAUNCH_LEASES: dict[
    int,
    tuple[weakref.ReferenceType["InstalledSidecarLaunchLease"], _InstalledSidecarLeaseState],
] = {}


class InstalledSidecarLaunchLease:
    """A factory-only live slot lease that can be consumed by exactly one spawn."""

    __slots__ = ("_identity", "_admission", "__weakref__")

    _identity: InstalledSlotIdentity
    _admission: AuthenticatedInstalledSidecarLaunch

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("InstalledSidecarLaunchLease is factory-only")

    @property
    def identity(self) -> InstalledSlotIdentity:
        return self._identity

    @property
    def admission(self) -> AuthenticatedInstalledSidecarLaunch:
        return self._admission

    @property
    def resolution(self) -> InstalledSidecarExecutableResolution:
        return self._admission.resolution

    @property
    def slot_root(self) -> Path:
        return self._admission.slot_root

    @property
    def manifest_path(self) -> Path:
        return self._admission.manifest_path

    @property
    def executable_path(self) -> Path:
        return self._admission.executable_path

    def close(self) -> None:
        state = _pop_live_lease_state(self)
        if state is not None:
            state.close()

    def __enter__(self) -> Self:
        if _find_live_lease_state(self) is None:
            raise TypeError("lease must be live")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __copy__(self) -> Self:
        raise TypeError("InstalledSidecarLaunchLease cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Self:
        raise TypeError("InstalledSidecarLaunchLease cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("InstalledSidecarLaunchLease cannot be serialized")

    def _take_for_spawn(self) -> _InstalledSidecarLeaseState:
        state = _pop_live_lease_state(self)
        if state is None or state.released or state.transferred:
            raise TypeError("lease must be a live factory InstalledSidecarLaunchLease")
        state.transferred = True
        return state


def _new_launch_lease(state: _InstalledSidecarLeaseState) -> InstalledSidecarLaunchLease:
    lease = object.__new__(InstalledSidecarLaunchLease)
    object.__setattr__(lease, "_identity", state.identity)
    object.__setattr__(lease, "_admission", state.admission)
    lease_id = id(lease)

    def remove_if_unclaimed(reference: weakref.ReferenceType[InstalledSidecarLaunchLease]) -> None:
        entry = _LIVE_LAUNCH_LEASES.get(lease_id)
        if entry is not None and entry[0] is reference:
            _LIVE_LAUNCH_LEASES.pop(lease_id, None)
            with suppress(InstalledSlotError):
                entry[1].close()

    reference = weakref.ref(lease, remove_if_unclaimed)
    _LIVE_LAUNCH_LEASES[lease_id] = (reference, state)
    return lease


def _find_live_lease_state(lease: object) -> _InstalledSidecarLeaseState | None:
    entry = _LIVE_LAUNCH_LEASES.get(id(lease))
    if entry is None or entry[0]() is not lease:
        return None
    return entry[1]


def _pop_live_lease_state(lease: object) -> _InstalledSidecarLeaseState | None:
    entry = _LIVE_LAUNCH_LEASES.get(id(lease))
    if entry is None or entry[0]() is not lease:
        return None
    _LIVE_LAUNCH_LEASES.pop(id(lease), None)
    return entry[1]


def acquire_installed_sidecar_launch_lease(
    installation_root: Path,
    trust_policy: ReleaseManifestTrustPolicyV1,
    verification_time: datetime,
) -> InstalledSidecarLaunchLease:
    """Lease the stable active slot, then authenticate its exact sidecar launch."""
    root = _validate_installation_root(installation_root)
    with _brief_control_lock(root):
        installation_id = _read_installation_id(root)
        initial_pointer = _read_active_slot_pointer(root)
        initial_identity = _pointer_identity(initial_pointer)
        _require_pointer_installation(initial_identity, installation_id)

    slot_root = root / SLOT_ROOT_RELATIVE_PATHS[initial_identity.physical_slot]
    _require_concrete_slot_root(root, slot_root)
    slot_lock = _acquire_slot_lock(root, initial_identity.physical_slot)
    try:
        with _brief_control_lock(root):
            reread_pointer = _read_active_slot_pointer(root)
            reread_identity = _pointer_identity(reread_pointer)
            if reread_identity != initial_identity:
                raise InstalledSlotError(
                    InstalledSlotReason.ACTIVE_SLOT_POINTER_CHANGED,
                    root / ACTIVE_SLOT_POINTER_RELATIVE_PATH,
                )
            _require_pointer_installation(reread_identity, installation_id)

        admission = admit_installed_sidecar_launch(slot_root, trust_policy, verification_time)
        if (
            admission.product_build_id != initial_identity.product_build_id
            or admission.manifest_sha256 != initial_identity.release_manifest_sha256
        ):
            raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH, slot_root)
        return _new_launch_lease(
            _InstalledSidecarLeaseState(
                admission=admission,
                identity=initial_identity,
                slot_lock=slot_lock,
            )
        )
    except BaseException:
        slot_lock.close()
        raise


def _validate_installation_root(installation_root: Path) -> Path:
    if (
        not isinstance(installation_root, Path)
        or not installation_root.is_absolute()
        or ".." in installation_root.parts
    ):
        raise InstalledSlotError(InstalledSlotReason.INSTALLED_ROOT_INVALID)
    try:
        value = os.lstat(installation_root)
    except OSError as exc:
        if _is_permission_error(exc):
            raise InstalledReleaseError(InstalledReleaseReason.FILE_ACCESS_DENIED, installation_root) from exc
        raise InstalledSlotError(InstalledSlotReason.INSTALLED_ROOT_INVALID, installation_root) from exc
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise InstalledSlotError(InstalledSlotReason.INSTALLED_ROOT_INVALID, installation_root)
    return installation_root


def _read_installation_id(root: Path) -> str:
    path = root / INSTALLATION_ID_RELATIVE_PATH
    raw = _read_stable_regular_file(root, path, limit=1024)
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH, path) from exc
    if _OPAQUE_TOKEN_RE.fullmatch(value) is None:
        raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH, path)
    return value


def _read_active_slot_pointer(root: Path) -> ActiveSlotPointerV1:
    path = root / ACTIVE_SLOT_POINTER_RELATIVE_PATH
    try:
        raw = _read_stable_regular_file(root, path, limit=MAX_ACTIVE_SLOT_POINTER_BYTES)
    except InstalledReleaseError as exc:
        if exc.reason == InstalledReleaseReason.PATH_CHANGED:
            raise InstalledSlotError(InstalledSlotReason.ACTIVE_SLOT_POINTER_CHANGED, path) from exc
        raise
    try:
        return parse_active_slot_pointer(raw)
    except ActiveSlotPointerError as exc:
        raise InstalledSlotError(InstalledSlotReason.ACTIVE_SLOT_POINTER_INVALID, path) from exc


def _require_pointer_installation(identity: InstalledSlotIdentity, installation_id: str) -> None:
    if identity.installation_id != installation_id:
        raise InstalledSlotError(InstalledSlotReason.SLOT_IDENTITY_MISMATCH)


def _require_concrete_slot_root(root: Path, slot_root: Path) -> None:
    snapshots = _snapshot_path_chain(root, slot_root)
    if not stat.S_ISDIR(snapshots[-1].mode):
        raise InstalledReleaseError(InstalledReleaseReason.NOT_REGULAR_FILE, slot_root)


def _brief_control_lock(root: Path) -> _NativeSlotLock:
    return _acquire_native_slot_lock(root, root / ACTIVE_SLOT_LOCK_RELATIVE_PATH)


def _acquire_slot_lock(root: Path, physical_slot: Literal["A", "B"]) -> _NativeSlotLock:
    return _acquire_native_slot_lock(root, root / SLOT_LOCK_RELATIVE_PATHS[physical_slot])


def _acquire_native_slot_lock(root: Path, path: Path) -> _NativeSlotLock:
    before = _snapshot_path_chain(root, path)
    _require_regular_single_link(before[-1])
    descriptor = _open_readwrite(path)
    try:
        opened = _PathSnapshot.from_stat(path, os.fstat(descriptor))
        _require_same_snapshot(before[-1], opened)
        _require_path_chain_unchanged(before, _snapshot_path_chain(root, path))
        platform_name = _lock_native_slot_nonblocking(descriptor, path)
    except BaseException:
        os.close(descriptor)
        raise
    return _NativeSlotLock(path=path, descriptor=descriptor, platform=platform_name)


def _open_readwrite(path: Path) -> int:
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        if _is_permission_error(exc):
            raise InstalledReleaseError(InstalledReleaseReason.FILE_ACCESS_DENIED, path) from exc
        raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path) from exc


def _lock_native_slot_nonblocking(descriptor: int, path: Path) -> Literal["posix", "windows"]:
    if os.name == "nt":
        import msvcrt

        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EBUSY}:
                raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_CONFLICT, path) from exc
            raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path) from exc
        return "windows"
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_CONFLICT, path) from exc
        except OSError as exc:
            raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path) from exc
        return "posix"
    raise InstalledSlotError(InstalledSlotReason.SLOT_LEASE_UNAVAILABLE, path)


def _unlock_native_slot_lock(descriptor: int, platform_name: Literal["posix", "windows"]) -> None:
    if platform_name == "windows":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_UNLCK"), 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)
