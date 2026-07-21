from __future__ import annotations

import copy
import ctypes
import gc
import inspect
import io
import os
import pickle
import threading
import weakref
from pathlib import Path

import pytest

import seektalent.installed_release as installed_release
import seektalent.installed_slot as installed_slot
import seektalent.owned_sidecar_process as owned_process
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
    def __init__(
        self,
        wait_results: list[int],
        *,
        close_results: list[int] | None = None,
        resume_result: int = 1,
    ) -> None:
        self.wait_results = wait_results
        self.close_results = close_results or []
        self.resume_result = resume_result
        self.resume_calls: list[int] = []
        self.terminate_calls: list[int] = []
        self.closed_handles: list[int] = []
        self.wait_calls = 0
        self.wait_timeouts: list[int] = []
        self.wait_entered: threading.Event | None = None
        self.release_wait: threading.Event | None = None

    def ResumeThread(self, handle: int) -> int:
        self.resume_calls.append(handle)
        return self.resume_result

    def CloseHandle(self, handle: int) -> int:
        self.closed_handles.append(handle)
        return self.close_results.pop(0) if self.close_results else 1

    def TerminateProcess(self, handle: int, _exit_code: int) -> int:
        self.terminate_calls.append(handle)
        return 1

    def WaitForSingleObject(self, _handle: int, timeout: int) -> int:
        self.wait_calls += 1
        self.wait_timeouts.append(timeout)
        result = self.wait_results.pop(0)
        if self.wait_entered is not None:
            self.wait_entered.set()
        if self.release_wait is not None:
            assert self.release_wait.wait(timeout=5)
        return result

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


class _FakeCreationApi(_FakeProcessApi):
    def __init__(
        self,
        *,
        failure: str | None = None,
        pipe_failure_at: int | None = None,
        set_handle_failure_at: int | None = None,
        file_failure_at: int | None = None,
    ) -> None:
        super().__init__([windows_sidecar.WAIT_OBJECT_0])
        self.failure = failure
        self.pipe_failure_at = pipe_failure_at
        self.set_handle_failure_at = set_handle_failure_at
        self.file_failure_at = file_failure_at
        self.pipe_calls = 0
        self.set_handle_calls = 0
        self.set_handle_handles: list[int] = []
        self.file_calls = 0
        self.file_handles: list[int] = []
        self.attribute_delete_calls = 0
        self.updated_handle_lists: list[tuple[int, ...]] = []
        self.create_arguments: tuple[object, ...] | None = None
        self.startup_handles: tuple[int, int, int] | None = None

    def CreatePipe(self, read_pointer: object, write_pointer: object, *_: object) -> int:
        from ctypes import wintypes

        self.pipe_calls += 1
        if self.failure == "pipe" or self.pipe_failure_at == self.pipe_calls:
            return 0
        read_handle = 10 + self.pipe_calls * 2 - 1
        write_handle = read_handle + 1
        ctypes.cast(read_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = read_handle
        ctypes.cast(write_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = write_handle
        return 1

    def SetHandleInformation(self, handle: int, *_: object) -> int:
        self.set_handle_calls += 1
        self.set_handle_handles.append(handle)
        return 0 if self.set_handle_failure_at == self.set_handle_calls else 1

    def InitializeProcThreadAttributeList(self, attribute_list: object, *_args: object) -> int:
        size_pointer = _args[-1]
        ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_size_t)).contents.value = 64
        if attribute_list is None:
            return 0
        return 0 if self.failure == "attribute" else 1

    def UpdateProcThreadAttribute(self, _attributes: object, _flags: object, _attribute: object, value: object, *_: object) -> int:
        from ctypes import wintypes

        handles = ctypes.cast(value, ctypes.POINTER(wintypes.HANDLE * 3)).contents
        self.updated_handle_lists.append(tuple(int(handle) for handle in handles))
        return 0 if self.failure == "update" else 1

    def DeleteProcThreadAttributeList(self, *_: object) -> None:
        self.attribute_delete_calls += 1

    def CreateProcessW(self, *args: object) -> int:
        from ctypes import wintypes

        self.create_arguments = args
        startup = ctypes.cast(args[-2], ctypes.POINTER(windows_sidecar._StartupInfoEx)).contents
        self.startup_handles = (
            int(startup.StartupInfo.hStdInput),
            int(startup.StartupInfo.hStdOutput),
            int(startup.StartupInfo.hStdError),
        )
        if self.failure == "create":
            return 0
        information = ctypes.cast(args[-1], ctypes.POINTER(windows_sidecar._ProcessInformation)).contents
        information.hProcess = wintypes.HANDLE(51)
        information.hThread = wintypes.HANDLE(52)
        information.dwProcessId = 53
        information.dwThreadId = 54
        return 1


class _FakeParentPipeStream(io.BytesIO):
    def __init__(self, api: _FakeProcessApi, handle: int) -> None:
        super().__init__()
        self._api = api
        self._handle = handle

    def close(self) -> None:
        if self.closed:
            return
        if not self._api.CloseHandle(self._handle):
            raise OSError("CloseHandle(parent pipe) failed")
        super().close()


def _fake_file_from_handle(api: _FakeCreationApi, handle: int, _mode: str) -> io.BytesIO:
    api.file_calls += 1
    api.file_handles.append(handle)
    if api.file_failure_at == api.file_calls:
        raise OSError("parent pipe wrapping failed")
    return _FakeParentPipeStream(api, handle)


def _create_fake_windows_pending(
    monkeypatch: pytest.MonkeyPatch,
    api: _FakeCreationApi,
) -> windows_sidecar._WindowsPendingSidecar:
    with monkeypatch.context() as context:
        context.setattr(windows_sidecar.os, "name", "nt")
        context.setattr(windows_sidecar.sys, "platform", "win32")
        context.setattr(windows_sidecar, "_windows_api", lambda: api)
        context.setattr(windows_sidecar, "_file_from_handle", lambda handle, mode: _fake_file_from_handle(api, handle, mode))
        context.setenv("SystemRoot", "C:\\Windows")
        return windows_sidecar._create_windows_suspended_sidecar(Path("C:/sidecar.exe"), Path("C:/working"))


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("pipe", WindowsLaunchBindingReason.PIPE_SETUP_FAILED),
        ("attribute", WindowsLaunchBindingReason.PIPE_SETUP_FAILED),
        ("update", WindowsLaunchBindingReason.PIPE_SETUP_FAILED),
        ("create", WindowsLaunchBindingReason.CREATE_PROCESS_FAILED),
    ],
)
def test_windows_create_process_setup_faults_are_causal_and_close_raw_child_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    reason: WindowsLaunchBindingReason,
) -> None:
    api = _FakeCreationApi(failure=failure)

    with pytest.raises(WindowsLaunchBindingError) as raised:
        _create_fake_windows_pending(monkeypatch, api)

    assert raised.value.reason == reason
    if failure != "pipe":
        assert api.closed_handles == [12, 13, 15, 11, 14, 16]
    assert api.attribute_delete_calls == (1 if failure in {"update", "create"} else 0)


def test_windows_create_process_uses_exact_three_child_handles_and_explicit_native_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeCreationApi()

    pending = _create_fake_windows_pending(monkeypatch, api)

    assert api.updated_handle_lists == [(11, 14, 16)]
    assert api.attribute_delete_calls == 1
    assert api.closed_handles == [11, 14, 16]
    assert api.create_arguments is not None
    application, command_line, _process_attributes, _thread_attributes, inherit_handles, flags, _environment, cwd, _startup_pointer, _information = api.create_arguments
    assert application == "C:\\sidecar.exe"
    assert command_line is None
    assert inherit_handles is True
    assert int(flags) == (
        windows_sidecar.CREATE_SUSPENDED
        | windows_sidecar.CREATE_NEW_PROCESS_GROUP
        | windows_sidecar.CREATE_UNICODE_ENVIRONMENT
        | windows_sidecar.EXTENDED_STARTUPINFO_PRESENT
    )
    assert cwd == "C:\\working"
    assert api.startup_handles == (11, 14, 16)
    assert pending.cleanup().child_reaped is True


@pytest.mark.parametrize(
    ("kind", "failure_at", "expected_closed"),
    [
        ("pipe", 2, [11, 12]),
        ("pipe", 3, [11, 12, 13, 14]),
        ("set", 1, [11, 12, 13, 14, 15, 16]),
        ("set", 2, [11, 12, 13, 14, 15, 16]),
        ("set", 3, [11, 12, 13, 14, 15, 16]),
        ("file", 1, [11, 12, 13, 14, 15, 16]),
        ("file", 2, [12, 11, 13, 14, 15, 16]),
        ("file", 3, [12, 13, 11, 14, 15, 16]),
    ],
)
def test_windows_partial_pipe_setup_failures_close_each_created_handle_once(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    failure_at: int,
    expected_closed: list[int],
) -> None:
    api = _FakeCreationApi(
        pipe_failure_at=failure_at if kind == "pipe" else None,
        set_handle_failure_at=failure_at if kind == "set" else None,
        file_failure_at=failure_at if kind == "file" else None,
    )

    with pytest.raises(WindowsLaunchBindingError) as raised:
        _create_fake_windows_pending(monkeypatch, api)

    assert raised.value.reason == WindowsLaunchBindingReason.PIPE_SETUP_FAILED
    assert api.closed_handles == expected_closed
    assert api.create_arguments is None
    assert api.attribute_delete_calls == 0
    if kind == "pipe":
        assert api.pipe_calls == failure_at
        assert api.set_handle_handles == []
        assert api.file_handles == []
    elif kind == "set":
        assert api.pipe_calls == 3
        assert api.set_handle_handles == [12, 13, 15][:failure_at]
        assert api.file_handles == []
    else:
        assert api.pipe_calls == 3
        assert api.set_handle_handles == [12, 13, 15]
        assert api.file_handles == [12, 13, 15][:failure_at]


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


def test_windows_public_surface_has_no_child_evidence_promotion_or_authenticated_authority() -> None:
    public_names = {name for name in dir(owned_process) if not name.startswith("_")}

    assert "ChildImageEvidence" not in public_names
    assert "PendingOwnedSidecarProcess" not in public_names
    assert tuple(inspect.signature(spawn_owned_sidecar).parameters) == ("lease",)
    assert not hasattr(owned_process.OwnedSidecarProcess, "promote")
    assert "authenticated" not in (owned_process.OwnedSidecarProcess.__doc__ or "").lower()


def test_private_pending_resumes_exactly_once() -> None:
    api = _FakeProcessApi([windows_sidecar.WAIT_OBJECT_0])
    owner = _pending(api)

    owner.resume(Path("C:/sidecar.exe"))

    assert api.resume_calls == [42]
    with pytest.raises(WindowsLaunchBindingError) as raised:
        owner.resume(Path("C:/sidecar.exe"))
    assert raised.value.reason == WindowsLaunchBindingReason.RESUME_THREAD_FAILED
    assert api.resume_calls == [42]


def test_private_pending_reports_resume_thread_fault_without_marking_the_child_resumed() -> None:
    api = _FakeProcessApi([], resume_result=windows_sidecar.WAIT_FAILED)
    owner = _pending(api)

    with pytest.raises(WindowsLaunchBindingError) as raised:
        owner.resume(Path("C:/sidecar.exe"))

    assert raised.value.reason == WindowsLaunchBindingReason.RESUME_THREAD_FAILED
    assert owner.resumed is False
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


def test_windows_child_reap_retries_process_handle_close_before_reporting_success() -> None:
    api = _FakeProcessApi(
        [windows_sidecar.WAIT_OBJECT_0, windows_sidecar.WAIT_OBJECT_0],
        close_results=[0, 1],
    )
    child = windows_sidecar._WindowsChildProcess(api, 41, None, 43, ["sidecar.exe"])

    with pytest.raises(OSError, match="CloseHandle\\(process\\) failed"):
        child.poll()

    assert child.returncode is None
    assert child.child_reaped is False
    assert child._process_handle == 41

    child.terminate()
    child.kill()

    assert api.terminate_calls == [41, 41]
    assert child.wait(1) == 0
    assert child.child_reaped is True
    assert child.handles_closed is True
    assert api.closed_handles == [41, 41]


def test_private_pending_cleanup_retries_transient_process_handle_close_failure() -> None:
    api = _FakeProcessApi(
        [windows_sidecar.WAIT_OBJECT_0, windows_sidecar.WAIT_OBJECT_0],
        close_results=[1, 0, 1],
    )
    owner = _pending(api)

    first = owner.cleanup()

    assert first.child_reaped is False
    assert first.handles_closed is False
    assert owner.child is not None
    assert owner.child._process_handle == 41

    second = owner.cleanup()

    assert second.child_reaped is True
    assert second.handles_closed is True
    assert api.closed_handles == [42, 41, 41]


class _FakeOpenedAuthority:
    def close(self) -> None:
        return None


def _spawn_unreaped_windows_cleanup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api: _FakeProcessApi,
) -> tuple[Path, owned_process.SidecarSpawnCleanupError, windows_sidecar._WindowsPendingSidecar]:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    state = installed_slot._find_live_lease_state(lease)
    assert state is not None
    state.windows_opened_release = _FakeOpenedAuthority()  # type: ignore[assignment]
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

    return root, raised.value, pending


def _spawn_windows_creation_fault(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    api: _FakeCreationApi,
) -> WindowsLaunchBindingError:
    lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    state = installed_slot._find_live_lease_state(lease)
    assert state is not None
    state.windows_opened_release = _FakeOpenedAuthority()  # type: ignore[assignment]
    with monkeypatch.context() as context:
        context.setattr(owned_process.os, "name", "nt")
        context.setattr(windows_sidecar.os, "name", "nt")
        context.setattr(windows_sidecar.sys, "platform", "win32")
        context.setattr(windows_sidecar, "_windows_api", lambda: api)
        context.setattr(windows_sidecar, "_file_from_handle", lambda handle, mode: _fake_file_from_handle(api, handle, mode))
        context.setenv("SystemRoot", "C:\\Windows")
        with pytest.raises(WindowsLaunchBindingError) as raised:
            spawn_owned_sidecar(lease)
    return raised.value


@pytest.mark.parametrize(
    ("kind", "failure_at"),
    [
        ("pipe", 2),
        ("pipe", 3),
        ("set", 1),
        ("set", 2),
        ("set", 3),
        ("file", 1),
        ("file", 2),
        ("file", 3),
    ],
)
def test_windows_partial_creation_failure_releases_slot_without_returning_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    failure_at: int,
) -> None:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    api = _FakeCreationApi(
        pipe_failure_at=failure_at if kind == "pipe" else None,
        set_handle_failure_at=failure_at if kind == "set" else None,
        file_failure_at=failure_at if kind == "file" else None,
    )

    error = _spawn_windows_creation_fault(root, monkeypatch, api)

    assert error.reason == WindowsLaunchBindingReason.PIPE_SETUP_FAILED
    assert api.create_arguments is None
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def test_cleanup_error_is_factory_only_and_forged_instances_have_no_cleanup_authority() -> None:
    with pytest.raises(TypeError, match="factory-only"):
        owned_process.SidecarSpawnCleanupError(ValueError("primary"), object(), ())  # type: ignore[call-arg]

    forged = RuntimeError.__new__(owned_process.SidecarSpawnCleanupError)
    with pytest.raises(TypeError, match="live factory cleanup error"):
        forged.reap()
    with pytest.raises(TypeError):
        object.__new__(owned_process.SidecarSpawnCleanupError)


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
    api = _FakeProcessApi(
        [windows_sidecar.WAIT_OBJECT_0, windows_sidecar.WAIT_OBJECT_0],
        close_results=[1, 0, 1],
    )
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
    assert cleanup_error.reap() is True
    assert api.terminate_calls == [41, 41]
    assert api.closed_handles == [42, 41, 41]
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def test_cleanup_error_serializes_concurrent_retry_and_caches_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeProcessApi([windows_sidecar.WAIT_FAILED])
    root, cleanup_error, _ = _spawn_unreaped_windows_cleanup_error(tmp_path, monkeypatch, api)
    api.wait_results.append(windows_sidecar.WAIT_OBJECT_0)
    api.wait_entered = threading.Event()
    api.release_wait = threading.Event()
    results: list[bool] = []
    errors: list[BaseException] = []

    def retry() -> None:
        try:
            results.append(cleanup_error.reap())
        except (IndexError, OSError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(exc)

    first = threading.Thread(target=retry)
    second = threading.Thread(target=retry)
    first.start()
    assert api.wait_entered.wait(timeout=5)
    second.start()
    api.release_wait.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert results == [True, True]
    assert api.terminate_calls == [41, 41]
    assert api.wait_calls == 2
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is True
    with pytest.raises(TypeError):
        copy.copy(cleanup_error)
    with pytest.raises(TypeError):
        copy.deepcopy(cleanup_error)
    with pytest.raises(TypeError):
        pickle.dumps(cleanup_error)
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def test_cleanup_error_stops_after_its_bounded_retry_budget_without_releasing_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeProcessApi([windows_sidecar.WAIT_FAILED])
    root, cleanup_error, _ = _spawn_unreaped_windows_cleanup_error(tmp_path, monkeypatch, api)
    assert owned_process.FAILED_SPAWN_CLEANUP_RETRY_BUDGET == 3
    api.wait_results.extend([windows_sidecar.WAIT_FAILED] * owned_process.FAILED_SPAWN_CLEANUP_RETRY_BUDGET)

    for _ in range(owned_process.FAILED_SPAWN_CLEANUP_RETRY_BUDGET):
        assert cleanup_error.reap() is False

    assert cleanup_error.cleanup_terminally_failed is True
    assert cleanup_error.direct_child_reaped is False
    assert cleanup_error.lease_released is False
    assert api.wait_timeouts == [5_000] * (1 + owned_process.FAILED_SPAWN_CLEANUP_RETRY_BUDGET)
    native_calls = (list(api.terminate_calls), api.wait_calls, list(api.closed_handles))
    assert cleanup_error.reap() is False
    assert (api.terminate_calls, api.wait_calls, api.closed_handles) == native_calls
    with pytest.raises(InstalledSlotError) as conflict:
        acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    assert conflict.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT


def test_discarded_cleanup_error_collects_while_its_orphan_owner_can_reap_and_release_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gc.collect()
    existing_orphan_ids = set(owned_process._abandoned_spawn_cleanup_ids())
    api = _FakeProcessApi([windows_sidecar.WAIT_FAILED, windows_sidecar.WAIT_OBJECT_0])
    root, cleanup_error, pending = _spawn_unreaped_windows_cleanup_error(tmp_path, monkeypatch, api)
    error_reference = weakref.ref(cleanup_error)

    del cleanup_error
    gc.collect()

    assert error_reference() is None
    orphan_ids = set(owned_process._abandoned_spawn_cleanup_ids()) - existing_orphan_ids
    assert len(orphan_ids) == 1
    with pytest.raises(InstalledSlotError) as conflict:
        acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    assert conflict.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    assert owned_process._retry_abandoned_spawn_cleanup(orphan_ids.pop()) is True
    assert set(owned_process._abandoned_spawn_cleanup_ids()) == existing_orphan_ids
    assert pending.child is not None
    assert pending.child.child_reaped is True
    assert pending.child.handles_closed is True
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def test_terminal_cleanup_owner_survives_error_collection_until_private_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gc.collect()
    existing_orphan_ids = set(owned_process._abandoned_spawn_cleanup_ids())
    api = _FakeProcessApi([windows_sidecar.WAIT_FAILED])
    root, cleanup_error, _ = _spawn_unreaped_windows_cleanup_error(tmp_path, monkeypatch, api)
    api.wait_results.extend([windows_sidecar.WAIT_FAILED] * owned_process.FAILED_SPAWN_CLEANUP_RETRY_BUDGET)
    for _ in range(owned_process.FAILED_SPAWN_CLEANUP_RETRY_BUDGET):
        assert cleanup_error.reap() is False
    assert cleanup_error.cleanup_terminally_failed is True
    error_reference = weakref.ref(cleanup_error)

    del cleanup_error
    gc.collect()

    assert error_reference() is None
    orphan_ids = set(owned_process._abandoned_spawn_cleanup_ids()) - existing_orphan_ids
    assert len(orphan_ids) == 1
    with pytest.raises(InstalledSlotError):
        acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)

    api.wait_results.append(windows_sidecar.WAIT_OBJECT_0)
    assert owned_process._retry_abandoned_spawn_cleanup(orphan_ids.pop()) is True
    assert set(owned_process._abandoned_spawn_cleanup_ids()) == existing_orphan_ids
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def test_windows_resume_thread_close_failure_kills_reaps_and_releases_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    state = installed_slot._find_live_lease_state(lease)
    assert state is not None
    state.windows_opened_release = _FakeOpenedAuthority()  # type: ignore[assignment]
    api = _FakeProcessApi(
        [windows_sidecar.WAIT_OBJECT_0],
        close_results=[0, 1, 1],
    )
    pending = _pending(api)

    with monkeypatch.context() as context:
        context.setattr(owned_process.os, "name", "nt")
        context.setattr(owned_process, "_create_windows_suspended_sidecar", lambda *_: pending)
        context.setattr(owned_process, "_verify_suspended_child_image", lambda *_: None)
        with pytest.raises(WindowsLaunchBindingError) as raised:
            spawn_owned_sidecar(lease)

    assert raised.value.reason == WindowsLaunchBindingReason.RESUME_THREAD_FAILED
    assert pending.resumed is False
    assert api.resume_calls == [42]
    assert api.terminate_calls == [41]
    assert api.closed_handles == [42, 42, 41]
    assert pending.child is not None
    assert pending.child.child_reaped is True
    assert pending.child.handles_closed is True
    next_lease = acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)
    next_lease.close()


def test_windows_post_resume_wrapper_failure_kills_reaps_and_returns_no_authority(
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

    with monkeypatch.context() as context:
        context.setattr(owned_process.os, "name", "nt")
        context.setattr(owned_process, "_create_windows_suspended_sidecar", lambda *_: pending)
        context.setattr(owned_process, "_verify_suspended_child_image", lambda *_: None)
        context.setattr(
            owned_process,
            "OwnedSidecarProcess",
            lambda **_: (_ for _ in ()).throw(RuntimeError("wrapper construction failed")),
        )
        with pytest.raises(RuntimeError, match="wrapper construction failed"):
            spawn_owned_sidecar(lease)

    assert pending.resumed is True
    assert api.resume_calls == [42]
    assert api.terminate_calls == [41]
    assert pending.child is not None
    assert pending.child.child_reaped is True
    assert pending.child.handles_closed is True
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
def test_windows_real_product_boundary_child_image_mismatch_never_resumes_and_is_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _windows_launch_lease(tmp_path, monkeypatch)
    actual_child = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
    created: list[windows_sidecar._WindowsPendingSidecar] = []

    def create_different_real_image(_expected: Path, working_directory: Path) -> windows_sidecar._WindowsPendingSidecar:
        pending = windows_sidecar._create_windows_suspended_sidecar(actual_child, working_directory)
        created.append(pending)
        return pending

    monkeypatch.setattr(owned_process, "_create_windows_suspended_sidecar", create_different_real_image)
    with pytest.raises(WindowsLaunchBindingError) as raised:
        spawn_owned_sidecar(lease)

    assert raised.value.reason == WindowsLaunchBindingReason.CHILD_IMAGE_MISMATCH
    assert len(created) == 1
    pending = created[0]
    assert pending.resumed is False
    assert pending.child is not None
    assert pending.child.child_reaped is True
    assert pending.child.handles_closed is True
    next_lease = acquire_installed_sidecar_launch_lease(
        lease.manifest_path.parents[3],
        _policy(),
        VERIFICATION_TIME,
    )
    next_lease.close()


@pytest.mark.skipif(os.name != "nt", reason="real Windows inherited-handle inspection")
def test_windows_real_child_does_not_inherit_an_ambient_inheritable_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    duplicate_same_access = 0x00000002
    inherit_flag = windows_sidecar.HANDLE_FLAG_INHERIT
    sentinel = kernel32.CreateEventW(None, True, False, None)
    assert sentinel
    assert kernel32.SetHandleInformation(sentinel, inherit_flag, inherit_flag)
    process = _windows_launch_lease(tmp_path, monkeypatch)
    try:
        owned = spawn_owned_sidecar(process)
        child = owned._process
        assert isinstance(child, windows_sidecar._WindowsChildProcess)
        assert child._process_handle is not None
        duplicated = wintypes.HANDLE()
        inherited = kernel32.DuplicateHandle(
            child._process_handle,
            sentinel,
            kernel32.GetCurrentProcess(),
            ctypes.byref(duplicated),
            0,
            False,
            duplicate_same_access,
        )
        if inherited:
            kernel32.CloseHandle(duplicated)
        assert not inherited, "child inherited an ambient event outside the explicit stdio list"
        owned.protocol_writer.write(b"exit /b 0\r\n")
        owned.protocol_writer.flush()
        owned.close_stdin()
        assert owned.wait(5) == 0
        owned.close_readers()
    finally:
        assert kernel32.CloseHandle(sentinel)
