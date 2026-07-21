from __future__ import annotations

import copy
import ctypes
import os
import pickle
import stat
from pathlib import Path

import pytest

import seektalent.installed_release as installed_release
import seektalent.installed_slot as installed_slot
import seektalent.owned_sidecar_process as owned_process
import seektalent.windows_installed_binding as windows_binding
import seektalent.windows_native_files as windows_native
from seektalent.installed_filesystem import InstalledReleaseError, InstalledReleaseReason
from seektalent.installed_slot import acquire_installed_sidecar_launch_lease
from seektalent.owned_sidecar_process import spawn_owned_sidecar
from seektalent.release_manifest import parse_release_manifest
from seektalent.windows_installed_binding import (
    WindowsLaunchBindingError,
    WindowsLaunchBindingReason,
    WindowsOpenedInstalledRelease,
)
from tests.test_installed_slot_lease import _install_active_slot
from tests.test_release_signing import VERIFICATION_TIME, _policy


requires_windows = pytest.mark.skipif(os.name != "nt", reason="real Win32 handle semantics")


def _executable_path(slot_root: Path) -> Path:
    manifest = parse_release_manifest(
        (slot_root / installed_release.INSTALLED_MANIFEST_RELATIVE_PATH).read_bytes()
    )
    component = next(
        item for item in manifest.components if item.component_id == installed_release.SIDECAR_COMPONENT_ID
    )
    return slot_root / manifest.payload_root / component.root_path / component.entrypoints[0]


def _acquire(root: Path):
    return acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)


def _make_executable_writable(slot_root: Path) -> Path:
    executable = _executable_path(slot_root)
    executable.chmod(stat.S_IREAD | stat.S_IWRITE)
    return executable


def _open_writer(path: Path) -> tuple[object, int]:
    from ctypes import wintypes

    api = windows_native.windows_api()
    handle = api.CreateFileW(
        str(path),
        windows_native.GENERIC_READ | windows_native.GENERIC_WRITE,
        windows_native.FILE_SHARE_READ | windows_native.FILE_SHARE_WRITE,
        None,
        windows_native.OPEN_EXISTING,
        0,
        None,
    )
    assert handle != wintypes.HANDLE(-1).value
    return api, int(handle)


def _create_directory_symlink(link: Path, target: Path) -> None:
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.CreateSymbolicLinkW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    kernel32.CreateSymbolicLinkW.restype = wintypes.BOOLEAN
    created = kernel32.CreateSymbolicLinkW(str(link), str(target), 0x1 | 0x2)
    assert created, f"CreateSymbolicLinkW failed: {getattr(ctypes, 'get_last_error')()}"


@requires_windows
def test_windows_admission_uses_live_opened_objects_and_spawn_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, slot_root = _install_active_slot(tmp_path, monkeypatch)
    _make_executable_writable(slot_root)
    path_reader_calls: list[Path] = []
    executable_inspector_calls: list[Path] = []
    popen_calls: list[object] = []

    def path_reader_must_not_run(_root: Path, path: Path, **_kwargs: object) -> bytes:
        path_reader_calls.append(path)
        pytest.fail("Windows admission must not use the closed-descriptor path reader")

    def path_inspector_must_not_run(_root: Path, path: Path, _ref: object) -> str:
        executable_inspector_calls.append(path)
        pytest.fail("Windows admission must not use the path executable inspector")

    monkeypatch.setattr(
        installed_release,
        "_read_stable_regular_file",
        path_reader_must_not_run,
    )
    monkeypatch.setattr(
        installed_release,
        "_inspect_executable",
        path_inspector_must_not_run,
    )
    monkeypatch.setattr(
        owned_process.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)),
    )

    lease = _acquire(root)
    manifest_path = lease.manifest_path
    executable_path = lease.executable_path
    moved_slot = slot_root.with_name("A-moved")
    moved_manifest = manifest_path.with_name("release-manifest-moved.json")

    with pytest.raises(OSError):
        slot_root.rename(moved_slot)
    with pytest.raises(OSError):
        manifest_path.rename(moved_manifest)
    from ctypes import wintypes

    writer_api = windows_native.windows_api()
    writer = writer_api.CreateFileW(
        str(executable_path),
        windows_native.GENERIC_READ | windows_native.GENERIC_WRITE,
        windows_native.FILE_SHARE_READ | windows_native.FILE_SHARE_WRITE,
        None,
        windows_native.OPEN_EXISTING,
        0,
        None,
    )
    assert writer == wintypes.HANDLE(-1).value
    assert getattr(ctypes, "get_last_error")() == windows_binding.ERROR_SHARING_VIOLATION
    assert path_reader_calls == []
    assert executable_inspector_calls == []

    with pytest.raises(WindowsLaunchBindingError) as raised:
        spawn_owned_sidecar(lease)
    assert raised.value.reason == WindowsLaunchBindingReason.CREATE_PROCESS_FAILED
    assert popen_calls == []

    slot_root.rename(moved_slot)
    moved_slot.rename(slot_root)
    manifest_path.rename(moved_manifest)
    moved_manifest.rename(manifest_path)
    next_lease = _acquire(root)
    next_lease.close()


@requires_windows
def test_preexisting_executable_writer_fails_before_child_and_releases_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, slot_root = _install_active_slot(tmp_path, monkeypatch)
    executable = _make_executable_writable(slot_root)
    api, writer = _open_writer(executable)
    try:
        with pytest.raises(WindowsLaunchBindingError) as raised:
            _acquire(root)
        assert raised.value.reason == WindowsLaunchBindingReason.EXECUTABLE_SHARE_MODE_CONFLICT
        assert raised.value.winerror == windows_binding.ERROR_SHARING_VIOLATION
    finally:
        api.CloseHandle(writer)

    lease = _acquire(root)
    lease.close()


@requires_windows
def test_reparse_release_component_is_rejected_by_product_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, slot_root = _install_active_slot(tmp_path, monkeypatch)
    release = slot_root / "release"
    real_release = slot_root / "real-release"
    release.rename(real_release)
    _create_directory_symlink(release, real_release)
    try:
        with pytest.raises(WindowsLaunchBindingError) as raised:
            _acquire(root)
        assert raised.value.reason == WindowsLaunchBindingReason.SLOT_REPARSE_POINT_REJECTED
    finally:
        release.unlink()
        real_release.rename(release)

    lease = _acquire(root)
    lease.close()


@requires_windows
def test_hardlinked_executable_is_rejected_and_releases_lifecycle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, slot_root = _install_active_slot(tmp_path, monkeypatch)
    executable = _make_executable_writable(slot_root)
    hardlink = executable.with_name("sidecar-hardlink.bin")
    os.link(executable, hardlink)
    try:
        with pytest.raises(InstalledReleaseError) as raised:
            _acquire(root)
        assert raised.value.reason == InstalledReleaseReason.HARDLINK
        assert raised.value.path == executable
    finally:
        hardlink.unlink()

    lease = _acquire(root)
    lease.close()


@requires_windows
def test_native_handle_identity_matches_ntfs_file_and_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, slot_root = _install_active_slot(tmp_path, monkeypatch)
    executable = _executable_path(slot_root)
    handle = windows_native.open_windows_object(executable, directory=False)
    try:
        identity = windows_native.query_windows_file_identity(handle, executable)
    finally:
        windows_native.close_windows_handle(handle)

    assert identity.final_path == windows_native.normalize_windows_path(str(executable))
    assert identity.volume_serial_number > 0
    assert len(identity.file_id) == 16
    assert any(identity.file_id)
    assert identity.size == executable.stat().st_size
    assert identity.link_count == 1
    assert identity.directory is False


@requires_windows
def test_windows_opened_authority_is_factory_only_live_and_nonserializable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        WindowsOpenedInstalledRelease()
    forged = object.__new__(WindowsOpenedInstalledRelease)
    with pytest.raises(TypeError):
        forged.read_regular(root / "anything", 1)

    lease = _acquire(root)
    live_state = installed_slot._find_live_lease_state(lease)
    assert live_state is not None
    authority = live_state.windows_opened_release
    assert authority is not None
    with pytest.raises(TypeError):
        copy.copy(authority)
    with pytest.raises(TypeError):
        copy.deepcopy(authority)
    with pytest.raises(TypeError):
        pickle.dumps(authority)
    assert not hasattr(authority, "handle")
    assert not hasattr(authority, "handles")
    lease.close()
    with pytest.raises(TypeError):
        authority.read_regular(root / "anything", 1)


@requires_windows
def test_unsupported_filesystem_is_causal_and_releases_lifecycle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    original = windows_binding.require_supported_local_ntfs
    monkeypatch.setattr(
        windows_binding,
        "require_supported_local_ntfs",
        lambda path: (_ for _ in ()).throw(
            WindowsLaunchBindingError(
                WindowsLaunchBindingReason.SLOT_FILESYSTEM_UNSUPPORTED,
                path,
            )
        ),
    )
    with pytest.raises(WindowsLaunchBindingError) as raised:
        _acquire(root)
    assert raised.value.reason == WindowsLaunchBindingReason.SLOT_FILESYSTEM_UNSUPPORTED

    monkeypatch.setattr(windows_binding, "require_supported_local_ntfs", original)
    lease = _acquire(root)
    lease.close()


@pytest.mark.skipif(os.name == "nt", reason="non-Windows fail-closed contract")
def test_non_windows_factory_fails_closed_before_native_api_lookup(tmp_path: Path) -> None:
    with pytest.raises(WindowsLaunchBindingError) as raised:
        windows_binding.acquire_windows_opened_installed_release(
            tmp_path.resolve(),
            (tmp_path / "slots" / "A").resolve(),
        )
    assert raised.value.reason == WindowsLaunchBindingReason.LAUNCH_BINDING_UNSUPPORTED


def test_windows_final_path_normalization_is_case_and_prefix_stable() -> None:
    assert windows_native.normalize_windows_path(r"\\?\C:\SeekTalent\Slots\A") == (
        windows_native.normalize_windows_path(r"c:\seektalent\slots\a")
    )
    assert windows_native.normalize_windows_path(r"\\?\UNC\server\share\slot") == (
        windows_native.normalize_windows_path(r"\\server\share\slot")
    )


def test_child_image_failures_have_distinct_causal_reasons() -> None:
    assert WindowsLaunchBindingReason.CHILD_IMAGE_UNAVAILABLE.value == "child_image_unavailable"
    assert WindowsLaunchBindingReason.CHILD_IMAGE_MISMATCH.value == "child_image_mismatch"
    assert WindowsLaunchBindingReason.PIPE_SETUP_FAILED.value == "pipe_setup_failed"
    assert WindowsLaunchBindingReason.CREATE_PROCESS_FAILED.value == "create_process_failed"
    assert WindowsLaunchBindingReason.RESUME_THREAD_FAILED.value == "resume_thread_failed"
