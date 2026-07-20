from __future__ import annotations

import os
import platform
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


from seektalent.installed_filesystem import (
    InstalledReleaseError,
    InstalledReleaseReason,
    _PathSnapshot,
    _hash_exact_snapshot,
    _open_readonly,
    _read_stable_regular_file,
    _require_effective_executable,
    _require_path_chain_unchanged,
    _require_regular_single_link,
    _require_same_snapshot,
    _require_size_within_limit,
    _snapshot_path_chain,
)
from seektalent.release_manifest import (
    ComponentV1,
    FileRefV1,
    ProtocolFactV1,
    ReleaseManifestV1,
    TargetV1,
    parse_release_manifest,
    release_manifest_digest,
)
from seektalent.release_signing import (
    ReleaseManifestTrustPolicyV1,
    parse_release_manifest_signature,
    verify_release_manifest_signature,
)
from seektalent.windows_installed_binding import WindowsOpenedInstalledRelease


INSTALLED_MANIFEST_RELATIVE_PATH = Path("release/release-manifest.json")
INSTALLED_SIGNATURE_RELATIVE_PATH = Path("release/signatures/release-manifest.sig")
SIDECAR_COMPONENT_ID = "liepin_execution_sidecar"
_ADMISSION_FACTORY_TOKEN = object()
# A release manifest is metadata, not payload. One MiB leaves ample room for
# file closure while bounding strict-JSON parser input and transient memory.
MAX_INSTALLED_MANIFEST_BYTES = 1024 * 1024
# The sidecar is one desktop executable. 512 MiB leaves packaging headroom
# while bounding point-in-time hashing when installed content is untrusted.
MAX_INSTALLED_SIDECAR_BYTES = 512 * 1024 * 1024


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
    return _admit_installed_sidecar_launch(
        root,
        trust_policy,
        verification_time,
        read_regular=lambda path, limit: _read_stable_regular_file(root, path, limit=limit),
        inspect_executable=lambda path, file_ref: _inspect_executable(root, path, file_ref),
    )


def admit_windows_opened_sidecar_launch(
    slot_root: Path,
    opened_release: WindowsOpenedInstalledRelease,
    trust_policy: ReleaseManifestTrustPolicyV1,
    verification_time: datetime,
) -> AuthenticatedInstalledSidecarLaunch:
    """Authenticate one Windows release exclusively through its live opened objects."""
    root = _validate_slot_root(slot_root)
    opened_release.require_slot_root(root)
    return _admit_installed_sidecar_launch(
        root,
        trust_policy,
        verification_time,
        read_regular=opened_release.read_regular,
        inspect_executable=lambda path, file_ref: opened_release.inspect_executable(
            path,
            expected_size=file_ref.size_bytes,
            expected_sha256=file_ref.sha256,
            limit=MAX_INSTALLED_SIDECAR_BYTES,
        ),
    )


def _admit_installed_sidecar_launch(
    root: Path,
    trust_policy: ReleaseManifestTrustPolicyV1,
    verification_time: datetime,
    *,
    read_regular: Callable[[Path, int], bytes],
    inspect_executable: Callable[[Path, FileRefV1], str],
) -> AuthenticatedInstalledSidecarLaunch:
    manifest_path = root / INSTALLED_MANIFEST_RELATIVE_PATH
    signature_path = root / INSTALLED_SIGNATURE_RELATIVE_PATH
    manifest = parse_release_manifest(read_regular(manifest_path, MAX_INSTALLED_MANIFEST_BYTES))
    signature = parse_release_manifest_signature(
        read_regular(signature_path, MAX_INSTALLED_MANIFEST_BYTES)
    )
    verified = verify_release_manifest_signature(signature, manifest, trust_policy, verification_time)
    resolution = _resolve_installed_sidecar_executable(root, manifest, inspect_executable)

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


def resolve_installed_sidecar_executable(slot_root: Path) -> InstalledSidecarExecutableResolution:
    """Resolve the fixed installed sidecar entrypoint and inspect its current bytes."""
    root = _validate_slot_root(slot_root)
    manifest_path = root / INSTALLED_MANIFEST_RELATIVE_PATH
    manifest_bytes = _read_stable_regular_file(root, manifest_path, limit=MAX_INSTALLED_MANIFEST_BYTES)
    manifest = parse_release_manifest(manifest_bytes)
    return _resolve_installed_sidecar_executable(
        root,
        manifest,
        lambda path, file_ref: _inspect_executable(root, path, file_ref),
    )


def _resolve_installed_sidecar_executable(
    root: Path,
    manifest: ReleaseManifestV1,
    inspect_executable: Callable[[Path, FileRefV1], str],
) -> InstalledSidecarExecutableResolution:
    manifest_path = root / INSTALLED_MANIFEST_RELATIVE_PATH
    _require_host_match(manifest.target)
    component, file_ref = _select_sidecar_file(manifest)
    executable_path = root / manifest.payload_root / component.root_path / file_ref.path
    executable_digest = inspect_executable(executable_path, file_ref)
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
