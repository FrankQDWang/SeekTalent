from __future__ import annotations

import copy
import errno
import os
import socket
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

import pytest

import seektalent.installed_release as installed_release
from seektalent.installed_release import (
    InstalledReleaseError,
    InstalledReleaseReason,
    resolve_installed_sidecar_executable,
)
from seektalent.release_manifest import ReleaseManifestError, ReleaseManifestReason
from tests.test_release_manifest import TARGETS, _manifest_payload, _raw, _recalculate


HostFactory = Callable[[dict[str, str], str], installed_release._HostPlatform]


@pytest.fixture
def host() -> HostFactory:
    def make(target: dict[str, str], build: str) -> installed_release._HostPlatform:
        return installed_release._HostPlatform(
            os=target["os"],
            arch=target["arch"],
            build=installed_release._parse_build(build),
        )

    return make


def _sidecar_component(payload: dict[str, object]) -> dict[str, object]:
    components = payload["components"]
    assert isinstance(components, list)
    return next(
        component
        for component in components
        if isinstance(component, dict) and component["component_id"] == "liepin_execution_sidecar"
    )


def _install_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: dict[str, str] | None = None,
    executable_bytes: bytes = b"sidecar executable\n",
) -> tuple[Path, Path, dict[str, object]]:
    selected_target = copy.deepcopy(target or TARGETS[1])
    payload = _manifest_payload(selected_target)
    component = _sidecar_component(payload)
    files = component["files"]
    assert isinstance(files, list) and isinstance(files[0], dict)
    files[0]["size_bytes"] = len(executable_bytes)
    files[0]["sha256"] = sha256(executable_bytes).hexdigest()
    _recalculate(payload)

    slot_root = tmp_path / "slot"
    manifest_path = slot_root / "release" / "release-manifest.json"
    executable_path = slot_root / "release" / str(component["root_path"]) / str(files[0]["path"])
    manifest_path.parent.mkdir(parents=True)
    executable_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(_raw(payload))
    executable_path.write_bytes(executable_bytes)
    executable_path.chmod(0o500)
    monkeypatch.setattr(
        installed_release,
        "_current_host_platform",
        lambda: installed_release._HostPlatform(
            os=selected_target["os"],
            arch=selected_target["arch"],
            build=installed_release._parse_build(selected_target["min_os_build"]),
        ),
    )
    return slot_root, executable_path, payload


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("bound", ["min_os_build", "max_os_build"])
def test_resolver_accepts_each_target_at_inclusive_build_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: HostFactory,
    target: dict[str, str],
    bound: str,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch, target=target)
    monkeypatch.setattr(installed_release, "_current_host_platform", lambda: host(target, target[bound]))

    resolution = resolve_installed_sidecar_executable(slot_root)

    assert resolution.executable_path == executable
    assert resolution.target.os == target["os"]
    assert resolution.target.arch == target["arch"]
    assert resolution.executable_sha256 == sha256(executable.read_bytes()).hexdigest()
    assert resolution.manifest_path == slot_root / "release" / "release-manifest.json"


@pytest.mark.parametrize(
    ("system", "machine", "version", "expected"),
    [
        ("Darwin", "x86_64", "15.2", ("macos", "x86_64", (15, 2, 0, 0))),
        ("Darwin", "arm64", "14.6.1", ("macos", "arm64", (14, 6, 1, 0))),
        ("Windows", "AMD64", "10.0.26100", ("windows", "x86_64", (10, 0, 26100, 0))),
    ],
)
def test_current_host_platform_maps_supported_native_names(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    version: str,
    expected: tuple[str, str, tuple[int, int, int, int]],
) -> None:
    monkeypatch.setattr(installed_release.platform, "system", lambda: system)
    monkeypatch.setattr(installed_release.platform, "machine", lambda: machine)
    monkeypatch.setattr(installed_release.platform, "mac_ver", lambda: (version, ("", "", ""), ""))
    monkeypatch.setattr(installed_release.platform, "win32_ver", lambda: ("", version, "", ""))

    actual = installed_release._current_host_platform()

    assert (actual.os, actual.arch, actual.build) == expected


@pytest.mark.parametrize(
    ("system", "machine", "version"),
    [
        ("Linux", "x86_64", "6.0"),
        ("Darwin", "riscv64", "15.0"),
        ("Windows", "AMD64", "unknown"),
    ],
)
def test_current_host_platform_rejects_unknown_os_arch_or_build(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    version: str,
) -> None:
    monkeypatch.setattr(installed_release.platform, "system", lambda: system)
    monkeypatch.setattr(installed_release.platform, "machine", lambda: machine)
    monkeypatch.setattr(installed_release.platform, "mac_ver", lambda: (version, ("", "", ""), ""))
    monkeypatch.setattr(installed_release.platform, "win32_ver", lambda: ("", version, "", ""))

    with pytest.raises(InstalledReleaseError) as raised:
        installed_release._current_host_platform()

    assert raised.value.reason == InstalledReleaseReason.HOST_UNSUPPORTED


@pytest.mark.parametrize(
    "host_value",
    [
        installed_release._HostPlatform("windows", "x86_64", (10, 0, 22621, 0)),
        installed_release._HostPlatform("macos", "arm64", (15, 0, 0, 0)),
        installed_release._HostPlatform("macos", "x86_64", (12, 9, 0, 0)),
        installed_release._HostPlatform("macos", "x86_64", (16, 0, 0, 0)),
    ],
)
def test_resolver_rejects_host_target_or_build_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_value: installed_release._HostPlatform,
) -> None:
    slot_root, _, _ = _install_slot(tmp_path, monkeypatch, target=TARGETS[1])
    monkeypatch.setattr(installed_release, "_current_host_platform", lambda: host_value)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.TARGET_MISMATCH


@pytest.mark.parametrize("entrypoints", [[], ["component.bin", "other.bin"]])
def test_resolver_requires_exactly_one_sidecar_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoints: list[str],
) -> None:
    slot_root, _, payload = _install_slot(tmp_path, monkeypatch)
    component = _sidecar_component(payload)
    files = component["files"]
    assert isinstance(files, list)
    if len(entrypoints) == 2:
        other = copy.deepcopy(files[0])
        assert isinstance(other, dict)
        other["path"] = "other.bin"
        files.append(other)
        files.sort(key=lambda item: str(item["path"]))
    component["entrypoints"] = entrypoints
    _recalculate(payload)
    (slot_root / "release" / "release-manifest.json").write_bytes(_raw(payload))

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.SIDECAR_DECLARATION_INVALID


def test_resolver_rejects_platform_independent_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, payload = _install_slot(tmp_path, monkeypatch)
    _sidecar_component(payload)["platform"] = "platform_independent"
    _recalculate(payload)
    (slot_root / "release" / "release-manifest.json").write_bytes(_raw(payload))

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.SIDECAR_DECLARATION_INVALID


def test_resolver_uses_only_fixed_strict_manifest_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, _ = _install_slot(tmp_path, monkeypatch)
    manifest_path = slot_root / "release" / "release-manifest.json"
    manifest_path.write_bytes(b'{"schema_version":"duplicate",' + manifest_path.read_bytes()[1:])

    with pytest.raises(ReleaseManifestError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == ReleaseManifestReason.DUPLICATE_KEY


def test_resolver_rejects_non_absolute_or_symlink_slot_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, _ = _install_slot(tmp_path, monkeypatch)
    symlink = tmp_path / "slot-link"
    symlink.symlink_to(slot_root, target_is_directory=True)

    for root in (Path("relative-slot"), symlink):
        with pytest.raises(InstalledReleaseError) as raised:
            resolve_installed_sidecar_executable(root)
        assert raised.value.reason == InstalledReleaseReason.INVALID_SLOT_ROOT


@pytest.mark.parametrize(
    "target_kind",
    ["manifest_parent", "manifest", "executable_parent", "executable"],
)
def test_resolver_rejects_parent_or_final_symlink_even_when_target_stays_in_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    if target_kind == "manifest_parent":
        original = slot_root / "real-release"
        (slot_root / "release").rename(original)
        (slot_root / "release").symlink_to(original, target_is_directory=True)
    elif target_kind == "manifest":
        manifest = slot_root / "release" / "release-manifest.json"
        original = manifest.with_name("real-release-manifest.json")
        manifest.rename(original)
        manifest.symlink_to(original)
    elif target_kind == "executable_parent":
        original = slot_root / "release" / "real-sidecar"
        executable.parent.rename(original)
        executable.parent.symlink_to(original, target_is_directory=True)
    else:
        original = executable.with_name("real-component.bin")
        executable.rename(original)
        executable.symlink_to(original)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.SYMLINK


def test_containment_rejects_path_outside_slot(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")

    with pytest.raises(InstalledReleaseError) as raised:
        installed_release._snapshot_path_chain(slot_root, outside)

    assert raised.value.reason == InstalledReleaseReason.PATH_ESCAPE


def test_manifest_path_escape_is_rejected_before_filesystem_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, payload = _install_slot(tmp_path, monkeypatch)
    _sidecar_component(payload)["root_path"] = "../outside"
    (slot_root / "release" / "release-manifest.json").write_bytes(_raw(payload))

    with pytest.raises(ReleaseManifestError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == ReleaseManifestReason.INVALID_PATH


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-kind matrix")
@pytest.mark.parametrize("kind", ["directory", "fifo", "socket"])
def test_resolver_rejects_non_regular_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    temporary_root = tempfile.TemporaryDirectory(prefix="st358-", dir="/tmp") if kind == "socket" else None
    try:
        root = Path(temporary_root.name) if temporary_root is not None else tmp_path
        slot_root, executable, _ = _install_slot(root, monkeypatch)
        executable.unlink()
        open_socket: socket.socket | None = None
        if kind == "directory":
            executable.mkdir()
        elif kind == "fifo":
            os.mkfifo(executable)
        else:
            open_socket = socket.socket(socket.AF_UNIX)
            open_socket.bind(str(executable))
        try:
            with pytest.raises(InstalledReleaseError) as raised:
                resolve_installed_sidecar_executable(slot_root)
            assert raised.value.reason == InstalledReleaseReason.NOT_REGULAR_FILE
        finally:
            if open_socket is not None:
                open_socket.close()
    finally:
        if temporary_root is not None:
            temporary_root.cleanup()


def test_resolver_rejects_hardlink_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    os.link(executable, executable.with_name("alias.bin"))

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.HARDLINK


def test_resolver_rejects_hardlinked_installed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, _ = _install_slot(tmp_path, monkeypatch)
    manifest = slot_root / "release" / "release-manifest.json"
    os.link(manifest, manifest.with_name("manifest-alias.json"))

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.HARDLINK


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable mode")
def test_resolver_rejects_missing_posix_execute_bit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    executable.chmod(0o400)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.FILE_MODE_MISMATCH


@pytest.mark.skipif(os.name != "posix", reason="POSIX effective executable access")
def test_resolver_rejects_owned_file_not_executable_by_effective_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    executable.chmod(0o401)
    assert not os.access(executable, os.X_OK, effective_ids=True)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.FILE_MODE_MISMATCH


@pytest.mark.skipif(os.name != "posix", reason="POSIX effective executable access")
def test_resolver_fails_closed_when_effective_access_checks_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, _ = _install_slot(tmp_path, monkeypatch)
    monkeypatch.setattr(installed_release.os, "supports_effective_ids", set())

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.FILE_MODE_MISMATCH


def test_resolver_rejects_sparse_manifest_over_product_limit_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, _ = _install_slot(tmp_path, monkeypatch)
    manifest = slot_root / "release" / "release-manifest.json"
    with manifest.open("r+b") as stream:
        stream.truncate(installed_release.MAX_INSTALLED_MANIFEST_BYTES + 1)
    manifest_inode = manifest.stat().st_ino
    original_read = installed_release.os.read

    def reject_manifest_read(descriptor: int, size: int) -> bytes:
        if os.fstat(descriptor).st_ino == manifest_inode:
            pytest.fail("oversized manifest must be rejected before reading")
        return original_read(descriptor, size)

    monkeypatch.setattr(installed_release.os, "read", reject_manifest_read)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.FILE_SIZE_LIMIT_EXCEEDED


def test_resolver_rejects_sparse_sidecar_over_product_limit_before_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, executable, payload = _install_slot(tmp_path, monkeypatch)
    component = _sidecar_component(payload)
    files = component["files"]
    assert isinstance(files, list) and isinstance(files[0], dict)
    files[0]["size_bytes"] = installed_release.MAX_INSTALLED_SIDECAR_BYTES + 1
    files[0]["sha256"] = "0" * 64
    _recalculate(payload)
    (slot_root / "release" / "release-manifest.json").write_bytes(_raw(payload))
    executable.chmod(0o700)
    with executable.open("r+b") as stream:
        stream.truncate(installed_release.MAX_INSTALLED_SIDECAR_BYTES + 1)
    executable.chmod(0o500)
    executable_inode = executable.stat().st_ino
    original_read = installed_release.os.read

    def reject_executable_read(descriptor: int, size: int) -> bytes:
        if os.fstat(descriptor).st_ino == executable_inode:
            pytest.fail("oversized sidecar must be rejected before hashing")
        return original_read(descriptor, size)

    monkeypatch.setattr(installed_release.os, "read", reject_executable_read)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.FILE_SIZE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"short", InstalledReleaseReason.FILE_SIZE_MISMATCH),
        (b"x" * 19, InstalledReleaseReason.FILE_DIGEST_MISMATCH),
    ],
)
def test_resolver_rejects_size_and_same_size_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    reason: InstalledReleaseReason,
) -> None:
    expected = b"same-size-right-byt"
    assert len(content) == len(expected) or reason == InstalledReleaseReason.FILE_SIZE_MISMATCH
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch, executable_bytes=expected)
    executable.chmod(0o700)
    executable.write_bytes(content)
    executable.chmod(0o500)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == reason


@pytest.mark.parametrize("mutation", ["truncate", "replace", "append"])
def test_resolver_rejects_executable_changed_during_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch, executable_bytes=b"x" * (2 * 1024 * 1024))
    original_read = installed_release.os.read
    executable_inode = executable.stat().st_ino
    changed = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        if not changed and chunk and os.fstat(descriptor).st_ino == executable_inode:
            changed = True
            if mutation == "truncate":
                executable.chmod(0o700)
                executable.write_bytes(b"x")
            elif mutation == "append":
                executable.chmod(0o700)
                with executable.open("ab") as stream:
                    stream.write(b"y")
            else:
                replacement = executable.with_name("replacement.bin")
                replacement.write_bytes(b"x" * (2 * 1024 * 1024))
                replacement.chmod(0o500)
                os.replace(replacement, executable)
        return chunk

    monkeypatch.setattr(installed_release.os, "read", mutating_read)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.PATH_CHANGED


@pytest.mark.parametrize("target", ["manifest", "executable"])
def test_resolver_stops_at_open_snapshot_size_when_file_keeps_growing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    target_path = slot_root / "release" / "release-manifest.json" if target == "manifest" else executable
    if target == "executable":
        target_path.chmod(0o700)
    target_inode = target_path.stat().st_ino
    original_read = installed_release.os.read
    matching_reads = 0

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal matching_reads
        chunk = original_read(descriptor, size)
        if chunk and os.fstat(descriptor).st_ino == target_inode:
            matching_reads += 1
            if matching_reads > 8:
                pytest.fail("reader did not stop at the opened snapshot size")
            with target_path.open("ab") as stream:
                stream.write(b"x")
        return chunk

    monkeypatch.setattr(installed_release.os, "read", growing_read)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.PATH_CHANGED
    assert matching_reads == 2


@pytest.mark.parametrize("target", ["manifest", "executable"])
def test_lstat_to_open_fifo_swap_fails_without_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    swapped_path = slot_root / "release" / "release-manifest.json" if target == "manifest" else executable
    original_open = installed_release.os.open
    swapped = False

    def swap_then_open(path: os.PathLike[str] | str, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if Path(path) == swapped_path and not swapped:
            assert flags & os.O_NONBLOCK
            swapped = True
            swapped_path.unlink()
            os.mkfifo(swapped_path)
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(installed_release.os, "open", swap_then_open)

    started = time.monotonic()
    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.PATH_CHANGED
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("target", ["manifest", "executable"])
def test_resolver_classifies_open_permission_denied_as_file_access_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    denied_path = slot_root / "release" / "release-manifest.json" if target == "manifest" else executable
    original_open = installed_release.os.open

    def permission_denied(
        path: os.PathLike[str] | str,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        if Path(path) == denied_path:
            raise PermissionError(errno.EACCES, "permission denied", str(path))
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(installed_release.os, "open", permission_denied)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.FILE_ACCESS_DENIED
    assert raised.value.path == denied_path
    assert isinstance(raised.value.__cause__, PermissionError)


def test_resolver_rejects_manifest_changed_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, _, _ = _install_slot(tmp_path, monkeypatch)
    manifest = slot_root / "release" / "release-manifest.json"
    manifest_inode = manifest.stat().st_ino
    original_read = installed_release.os.read
    changed = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        if not changed and chunk and os.fstat(descriptor).st_ino == manifest_inode:
            changed = True
            manifest.write_bytes(b"{}")
        return chunk

    monkeypatch.setattr(installed_release.os, "read", mutating_read)

    with pytest.raises(InstalledReleaseError) as raised:
        resolve_installed_sidecar_executable(slot_root)

    assert raised.value.reason == InstalledReleaseReason.PATH_CHANGED


def test_windows_junction_is_treated_as_link_like(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    value = os.lstat(directory)
    monkeypatch.setattr(installed_release.os, "name", "nt")
    monkeypatch.setattr(installed_release.os.path, "isjunction", lambda _: True)

    assert installed_release._is_link_like(directory, value)


def test_resolver_does_not_read_path_environment_or_spawn_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path / "attacker"))
    popen_calls: list[object] = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    resolution = resolve_installed_sidecar_executable(slot_root)

    assert resolution.executable_path == executable
    assert popen_calls == []
