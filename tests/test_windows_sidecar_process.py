from __future__ import annotations

import copy
import ctypes
import io
import os
import pickle
from pathlib import Path

import pytest

import seektalent.installed_release as installed_release
import seektalent.installed_slot as installed_slot
import seektalent.owned_sidecar_process as owned_process
import seektalent.windows_installed_binding as windows_binding
import seektalent.windows_sidecar_process as windows_sidecar
from seektalent.installed_slot import (
    ActiveSlotPointerV1,
    InstalledSlotError,
    InstalledSlotReason,
    acquire_installed_sidecar_launch_lease,
    canonical_active_slot_pointer_bytes,
)
from seektalent.owned_sidecar_process import spawn_owned_sidecar
from seektalent.release_manifest import parse_release_manifest
from seektalent.windows_installed_binding import WindowsLaunchBindingError, WindowsLaunchBindingReason
from tests.test_installed_release import _install_slot
from tests.test_release_manifest import TARGETS
from tests.test_release_signing import VERIFICATION_TIME, _policy, _signed
from tests.test_installed_slot_lease import _install_active_slot


class _FakeProcessApi:
    def __init__(self, wait_results: list[int]) -> None:
        self.wait_results = wait_results
        self.resume_calls: list[int] = []
        self.terminate_calls: list[int] = []
        self.closed_handles: list[int] = []

    def ResumeThread(self, handle: int) -> int:
        self.resume_calls.append(handle)
        return 1

    def CloseHandle(self, handle: int) -> int:
        self.closed_handles.append(handle)
        return 1

    def TerminateProcess(self, handle: int, _exit_code: int) -> int:
        self.terminate_calls.append(handle)
        return 1

    def WaitForSingleObject(self, _handle: int, _timeout: int) -> int:
        return self.wait_results.pop(0)

    def GetExitCodeProcess(self, _handle: int, pointer: object) -> int:
        from ctypes import wintypes

        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
        return 1


def _pending(api: _FakeProcessApi) -> windows_sidecar._WindowsPendingSidecar:
    child = windows_sidecar._WindowsChildProcess(api, 41, 42, 43, ["sidecar.exe"])
    return windows_sidecar._WindowsPendingSidecar(
        child,
        io.BytesIO(),
        io.BytesIO(),
        io.BytesIO(),
    )


def test_private_windows_child_owner_cannot_be_copied_or_serialized() -> None:
    owner = object.__new__(windows_sidecar._WindowsPendingSidecar)

    with pytest.raises(TypeError):
        copy.copy(owner)
    with pytest.raises(TypeError):
        copy.deepcopy(owner)
    with pytest.raises(TypeError):
        pickle.dumps(owner)


def test_windows_adapter_has_no_public_pending_process_name() -> None:
    assert not hasattr(windows_sidecar, "PendingOwnedSidecarProcess")


def test_private_pending_resumes_exactly_once() -> None:
    api = _FakeProcessApi([windows_sidecar.WAIT_OBJECT_0])
    owner = _pending(api)

    owner.resume(Path("C:/sidecar.exe"))

    assert api.resume_calls == [42]
    with pytest.raises(WindowsLaunchBindingError) as raised:
        owner.resume(Path("C:/sidecar.exe"))
    assert raised.value.reason == WindowsLaunchBindingReason.RESUME_THREAD_FAILED
    assert api.resume_calls == [42]


def test_private_pending_cleanup_retains_process_handle_until_reap_is_proven() -> None:
    api = _FakeProcessApi([windows_sidecar.WAIT_FAILED, windows_sidecar.WAIT_OBJECT_0])
    owner = _pending(api)

    first = owner.cleanup()

    assert first.child_reaped is False
    assert first.handles_closed is False
    assert api.terminate_calls == [41]
    assert owner.child is not None
    assert owner.child._process_handle == 41

    second = owner.cleanup()

    assert second.child_reaped is True
    assert second.handles_closed is True
    assert api.terminate_calls == [41, 41]


class _FakeOpenedAuthority:
    def close(self) -> None:
        return None


def test_windows_identity_failure_never_resumes_and_releases_the_slot_after_reap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    state = installed_slot._find_live_lease_state(lease)
    assert state is not None
    state.windows_opened_release = _FakeOpenedAuthority()  # type: ignore[assignment]
    api = _FakeProcessApi([windows_sidecar.WAIT_OBJECT_0])
    pending = _pending(api)
    create_calls: list[object] = []

    with monkeypatch.context() as context:
        context.setattr(owned_process.os, "name", "nt")
        context.setattr(
            owned_process,
            "_create_windows_suspended_sidecar",
            lambda *_: (create_calls.append(object()), pending)[1],
        )
        context.setattr(
            owned_process,
            "_verify_suspended_child_image",
            lambda *_: (_ for _ in ()).throw(
                WindowsLaunchBindingError(WindowsLaunchBindingReason.CHILD_IMAGE_MISMATCH)
            ),
        )
        with pytest.raises(WindowsLaunchBindingError) as raised:
            spawn_owned_sidecar(lease)

    assert raised.value.reason == WindowsLaunchBindingReason.CHILD_IMAGE_MISMATCH
    assert len(create_calls) == 1
    assert api.resume_calls == []
    assert api.terminate_calls == [41]
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def test_windows_unreaped_cleanup_owner_keeps_slot_conflicted_until_explicit_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    state = installed_slot._find_live_lease_state(lease)
    assert state is not None
    state.windows_opened_release = _FakeOpenedAuthority()  # type: ignore[assignment]
    api = _FakeProcessApi([windows_sidecar.WAIT_FAILED, windows_sidecar.WAIT_OBJECT_0])
    pending = _pending(api)

    with monkeypatch.context() as context:
        context.setattr(owned_process.os, "name", "nt")
        context.setattr(owned_process, "_create_windows_suspended_sidecar", lambda *_: pending)
        context.setattr(
            owned_process,
            "_verify_suspended_child_image",
            lambda *_: (_ for _ in ()).throw(
                WindowsLaunchBindingError(WindowsLaunchBindingReason.CHILD_IMAGE_UNAVAILABLE)
            ),
        )
        with pytest.raises(owned_process.SidecarSpawnCleanupError) as raised:
            spawn_owned_sidecar(lease)

    cleanup_error = raised.value
    assert cleanup_error.direct_child_reaped is False
    with pytest.raises(InstalledSlotError) as conflict:
        acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    assert conflict.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    assert cleanup_error.reap() is True
    assert cleanup_error.direct_child_reaped is True
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def _windows_launch_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
    source_slot, _, _ = _install_slot(
        tmp_path,
        monkeypatch,
        target=TARGETS[0],
        executable_bytes=cmd.read_bytes(),
    )
    root = tmp_path / "installation"
    slot_root = root / "slots" / "A"
    slot_root.parent.mkdir(parents=True)
    source_slot.rename(slot_root)
    manifest_path = slot_root / installed_release.INSTALLED_MANIFEST_RELATIVE_PATH
    manifest = parse_release_manifest(manifest_path.read_bytes())
    _, signature_payload = _signed(manifest)
    signature_path = slot_root / installed_release.INSTALLED_SIGNATURE_RELATIVE_PATH
    signature_path.parent.mkdir()
    signature_path.write_text(
        __import__("json").dumps(signature_payload, separators=(",", ":")),
        encoding="utf-8",
    )
    control = root / "control"
    control.mkdir()
    control.joinpath("installation-id").write_bytes(b"test-installation-1")
    control.joinpath("active-slot.lock").write_bytes(b"0")
    control.joinpath("slot-A.lock").write_bytes(b"0")
    control.joinpath("slot-B.lock").write_bytes(b"0")
    pointer = ActiveSlotPointerV1.model_construct(
        schema_version="seektalent.active-slot/v1",
        installation_id="test-installation-1",
        physical_slot="A",
        pointer_generation=1,
        product_build_id=manifest.product_build_id,
        release_manifest_sha256=installed_release.release_manifest_digest(manifest),
        committed_at="2026-07-20T12:00:00Z",
    )
    control.joinpath("active-slot.json").write_bytes(canonical_active_slot_pointer_bytes(pointer))
    return acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)


@pytest.mark.skipif(os.name != "nt", reason="real Windows suspended child boundary")
def test_windows_real_suspended_child_uses_no_popen_and_has_only_parent_pipe_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _windows_launch_lease(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "seektalent.owned_sidecar_process.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("Windows sidecar must use CreateProcessW directly"),
    )

    process = spawn_owned_sidecar(lease)
    process.protocol_writer.write(b"echo suspended-child-ok\r\nexit /b 0\r\n")
    process.protocol_writer.flush()
    process.close_stdin()
    output = process.protocol_reader.read()

    assert b"suspended-child-ok" in output
    assert process.wait(5) == 0
    process.close_readers()


@pytest.mark.skipif(os.name != "nt", reason="real Windows suspended child boundary")
def test_windows_real_child_image_mismatch_never_resumes_and_is_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _windows_launch_lease(tmp_path, monkeypatch)
    state = installed_slot._find_live_lease_state(lease)
    assert state is not None
    authority = state.windows_opened_release
    assert authority is not None
    pending = windows_sidecar._create_windows_suspended_sidecar(
        Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe",
        lease.manifest_path.parent,
    )
    try:
        assert pending.child is not None
        assert pending.child._process_handle is not None
        with pytest.raises(WindowsLaunchBindingError) as raised:
            windows_binding._verify_suspended_child_image(
                authority,
                pending.child._process_handle,
                lease.executable_path,
            )
        assert raised.value.reason == WindowsLaunchBindingReason.CHILD_IMAGE_MISMATCH
        assert pending.resumed is False
    finally:
        cleanup = pending.cleanup()
        assert cleanup.child_reaped is True
        assert cleanup.handles_closed is True
        lease.close()
