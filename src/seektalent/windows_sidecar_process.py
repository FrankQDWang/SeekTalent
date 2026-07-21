"""Private Win32 suspended-child creation for the installed sidecar boundary."""

from __future__ import annotations

import ctypes
import io
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import IO, Never, Protocol, SupportsIndex

from seektalent.windows_installed_binding import WindowsLaunchBindingError, WindowsLaunchBindingReason


CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
FAILED_SPAWN_REAP_TIMEOUT_SECONDS = 5.0


class _WindowsProcessApi(Protocol):
    def CreatePipe(self, *args: object) -> int: ...

    def SetHandleInformation(self, *args: object) -> int: ...

    def InitializeProcThreadAttributeList(self, *args: object) -> int: ...

    def UpdateProcThreadAttribute(self, *args: object) -> int: ...

    def DeleteProcThreadAttributeList(self, *args: object) -> None: ...

    def CreateProcessW(self, *args: object) -> int: ...

    def ResumeThread(self, *args: object) -> int: ...

    def TerminateProcess(self, *args: object) -> int: ...

    def WaitForSingleObject(self, *args: object) -> int: ...

    def GetExitCodeProcess(self, *args: object) -> int: ...

    def CloseHandle(self, *args: object) -> int: ...


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", ctypes.c_ulong),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_ulong),
        ("dwY", ctypes.c_ulong),
        ("dwXSize", ctypes.c_ulong),
        ("dwYSize", ctypes.c_ulong),
        ("dwXCountChars", ctypes.c_ulong),
        ("dwYCountChars", ctypes.c_ulong),
        ("dwFillAttribute", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("wShowWindow", ctypes.c_ushort),
        ("cbReserved2", ctypes.c_ushort),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_ulong),
        ("dwThreadId", ctypes.c_ulong),
    ]


@cache
def _windows_api() -> _WindowsProcessApi:
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


@dataclass(slots=True, eq=False)
class _WindowsChildProcess:
    _api: _WindowsProcessApi
    _process_handle: int | None
    _thread_handle: int | None
    pid: int
    args: list[str]
    returncode: int | None = None
    _reaped: bool = False
    _resumed: bool = False

    @property
    def child_reaped(self) -> bool:
        return self._reaped

    @property
    def handles_closed(self) -> bool:
        return self._process_handle is None and self._thread_handle is None

    def resume(self, executable_path: Path) -> None:
        handle = self._thread_handle
        if handle is None or self._resumed:
            raise WindowsLaunchBindingError(WindowsLaunchBindingReason.RESUME_THREAD_FAILED, executable_path)
        previous_suspend_count = int(self._api.ResumeThread(handle))
        if previous_suspend_count == WAIT_FAILED:
            raise _native_error(WindowsLaunchBindingReason.RESUME_THREAD_FAILED, executable_path)
        self._resumed = True
        try:
            self._close_thread_handle()
        except OSError as exc:
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.RESUME_THREAD_FAILED,
                executable_path,
                winerror=_error_code(exc),
            ) from exc
        if previous_suspend_count != 1:
            raise WindowsLaunchBindingError(WindowsLaunchBindingReason.RESUME_THREAD_FAILED, executable_path)

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        handle = self._require_process_handle()
        result = int(self._api.WaitForSingleObject(handle, 0))
        if result == WAIT_TIMEOUT:
            return None
        if result != WAIT_OBJECT_0:
            raise _last_error("WaitForSingleObject failed")
        return self._record_reap()

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        handle = self._require_process_handle()
        milliseconds = INFINITE if timeout is None else _milliseconds(timeout)
        result = int(self._api.WaitForSingleObject(handle, milliseconds))
        if result == WAIT_TIMEOUT:
            if timeout is None:
                raise AssertionError("an infinite Windows wait cannot time out")
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != WAIT_OBJECT_0:
            raise _last_error("WaitForSingleObject failed")
        return self._record_reap()

    def terminate(self) -> None:
        if self.returncode is not None:
            return
        handle = self._require_process_handle()
        if not self._api.TerminateProcess(handle, 1):
            raise _last_error("TerminateProcess failed")

    def kill(self) -> None:
        self.terminate()

    def close_thread_handle(self) -> None:
        self._close_thread_handle()

    def close_process_handle(self) -> None:
        if self._process_handle is None:
            return
        if not self._api.CloseHandle(self._process_handle):
            raise _last_error("CloseHandle(process) failed")
        self._process_handle = None

    def _record_reap(self) -> int:
        handle = self._require_process_handle()
        from ctypes import wintypes

        exit_code = wintypes.DWORD()
        if not self._api.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise _last_error("GetExitCodeProcess failed")
        self.returncode = int(exit_code.value)
        self._reaped = True
        self.close_process_handle()
        return self.returncode

    def _close_thread_handle(self) -> None:
        if self._thread_handle is None:
            return
        if not self._api.CloseHandle(self._thread_handle):
            raise _last_error("CloseHandle(thread) failed")
        self._thread_handle = None

    def _require_process_handle(self) -> int:
        if self._process_handle is None:
            raise ChildProcessError("direct child process handle is closed")
        return self._process_handle


@dataclass(frozen=True, slots=True)
class _WindowsCleanup:
    failures: tuple[BaseException, ...]
    child_reaped: bool
    handles_closed: bool


@dataclass(slots=True, eq=False)
class _WindowsPendingSidecar:
    """Private owner of a suspended child until identity verification and promotion."""

    child: _WindowsChildProcess | None
    protocol_writer: IO[bytes] | None
    protocol_reader: IO[bytes] | None
    stderr_reader: IO[bytes] | None
    extra_handles: list[int] = field(default_factory=list)
    resumed: bool = False
    promoted: bool = False

    def __copy__(self) -> Never:
        raise TypeError("private Windows pending sidecar cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("private Windows pending sidecar cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("private Windows pending sidecar cannot be serialized")

    def resume(self, executable_path: Path) -> None:
        if self.promoted or self.resumed or self.child is None:
            raise WindowsLaunchBindingError(WindowsLaunchBindingReason.RESUME_THREAD_FAILED, executable_path)
        self.child.resume(executable_path)
        self.resumed = True

    def resources(self) -> tuple[_WindowsChildProcess, IO[bytes], IO[bytes], IO[bytes]]:
        if not self.resumed or self.promoted:
            raise TypeError("private Windows pending sidecar is not promotable")
        if (
            self.child is None
            or self.protocol_writer is None
            or self.protocol_reader is None
            or self.stderr_reader is None
        ):
            raise TypeError("private Windows pending sidecar has no owned transport")
        return self.child, self.protocol_writer, self.protocol_reader, self.stderr_reader

    def mark_promoted(self) -> None:
        self.resources()
        self.promoted = True
        self.child = None
        self.protocol_writer = None
        self.protocol_reader = None
        self.stderr_reader = None

    def cleanup(self) -> _WindowsCleanup:
        failures: list[BaseException] = []
        for stream_name in ("protocol_writer", "protocol_reader", "stderr_reader"):
            stream = getattr(self, stream_name)
            if stream is None:
                continue
            try:
                stream.close()
            except OSError as exc:
                failures.append(exc)
            else:
                setattr(self, stream_name, None)
        for handle in self.extra_handles[:]:
            try:
                _close_handle(_windows_api(), handle)
            except OSError as exc:
                failures.append(exc)
            else:
                self.extra_handles.remove(handle)
        child = self.child
        if child is not None:
            try:
                child.close_thread_handle()
            except OSError as exc:
                failures.append(exc)
            try:
                child.kill()
            except OSError as exc:
                failures.append(exc)
            try:
                child.wait(FAILED_SPAWN_REAP_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append(exc)
            if child.child_reaped:
                try:
                    child.close_process_handle()
                except OSError as exc:
                    failures.append(exc)
        handles_closed = (
            not self.extra_handles
            and (child is None or child.handles_closed)
            and all(getattr(self, name) is None for name in ("protocol_writer", "protocol_reader", "stderr_reader"))
        )
        return _WindowsCleanup(tuple(failures), child is None or child.child_reaped, handles_closed)


@dataclass(slots=True)
class _WindowsPendingCreationError(Exception):
    primary_error: BaseException
    pending: _WindowsPendingSidecar
    failures: tuple[BaseException, ...]


def _create_windows_suspended_sidecar(
    executable_path: Path,
    working_directory: Path,
) -> _WindowsPendingSidecar:
    """Create one suspended child with an explicit three-handle inheritance list."""
    if os.name != "nt" or sys.platform != "win32":
        raise WindowsLaunchBindingError(WindowsLaunchBindingReason.LAUNCH_BINDING_UNSUPPORTED, executable_path)
    if not executable_path.is_absolute() or not working_directory.is_absolute():
        raise ValueError("Windows sidecar path and working directory must be absolute")
    api = _windows_api()
    raw_handles: list[int] = []
    streams: list[IO[bytes]] = []
    attribute_list: ctypes.Array[ctypes.c_char] | None = None
    inherited_handle_list: object | None = None
    attribute_initialized = False
    process: _WindowsChildProcess | None = None
    try:
        stdin_child, stdin_parent = _new_pipe(api)
        stdout_parent, stdout_child = _new_pipe(api)
        stderr_parent, stderr_child = _new_pipe(api)
        raw_handles.extend(
            [stdin_child, stdin_parent, stdout_parent, stdout_child, stderr_parent, stderr_child]
        )
        for parent_handle in (stdin_parent, stdout_parent, stderr_parent):
            _set_non_inheritable(api, parent_handle)

        for parent_handle, mode in (
            (stdin_parent, "wb"),
            (stdout_parent, "rb"),
            (stderr_parent, "rb"),
        ):
            streams.append(_file_from_handle(parent_handle, mode))
            raw_handles.remove(parent_handle)

        attribute_list, inherited_handle_list = _new_handle_list_attribute(
            api,
            [stdin_child, stdout_child, stderr_child],
        )
        attribute_initialized = True
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = stdin_child
        startup.StartupInfo.hStdOutput = stdout_child
        startup.StartupInfo.hStdError = stderr_child
        startup.lpAttributeList = ctypes.addressof(attribute_list)
        if inherited_handle_list is None:
            raise AssertionError("Windows inherited handle list was not retained")
        information = _ProcessInformation()
        flags = (
            CREATE_SUSPENDED
            | CREATE_NEW_PROCESS_GROUP
            | CREATE_UNICODE_ENVIRONMENT
            | EXTENDED_STARTUPINFO_PRESENT
        )
        environment = _unicode_environment()
        if not api.CreateProcessW(
            str(executable_path),
            None,
            None,
            None,
            True,
            flags,
            ctypes.addressof(environment),
            str(working_directory),
            ctypes.byref(startup),
            ctypes.byref(information),
        ):
            raise _native_error(WindowsLaunchBindingReason.CREATE_PROCESS_FAILED, executable_path)
        process = _WindowsChildProcess(
            api,
            int(information.hProcess),
            int(information.hThread),
            int(information.dwProcessId),
            [str(executable_path)],
        )
        close_failures = _close_and_remove(api, raw_handles)
        if attribute_initialized:
            api.DeleteProcThreadAttributeList(ctypes.addressof(attribute_list))
            attribute_initialized = False
        if close_failures:
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.PIPE_SETUP_FAILED,
                executable_path,
                winerror=_error_code(close_failures[0]),
            )
        return _WindowsPendingSidecar(process, streams[0], streams[1], streams[2])
    except (OSError, RuntimeError, TypeError, ValueError) as primary:
        if process is not None:
            pending = _WindowsPendingSidecar(
                process,
                streams[0] if len(streams) > 0 else None,
                streams[1] if len(streams) > 1 else None,
                streams[2] if len(streams) > 2 else None,
                raw_handles,
            )
            if attribute_initialized and attribute_list is not None:
                api.DeleteProcThreadAttributeList(ctypes.addressof(attribute_list))
            raise _WindowsPendingCreationError(primary, pending, ()) from primary
        cleanup_failures = _close_streams(streams) + _close_and_remove(api, raw_handles)
        if attribute_initialized and attribute_list is not None:
            api.DeleteProcThreadAttributeList(ctypes.addressof(attribute_list))
        for cleanup_error in cleanup_failures:
            primary.add_note(f"Windows pipe setup cleanup failed: {cleanup_error}")
        raise


def _new_pipe(api: _WindowsProcessApi) -> tuple[int, int]:
    from ctypes import wintypes

    attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
    read_handle = wintypes.HANDLE()
    write_handle = wintypes.HANDLE()
    if not api.CreatePipe(ctypes.byref(read_handle), ctypes.byref(write_handle), ctypes.byref(attributes), 0):
        raise _native_error(WindowsLaunchBindingReason.PIPE_SETUP_FAILED, None)
    if read_handle.value is None or write_handle.value is None:
        raise WindowsLaunchBindingError(WindowsLaunchBindingReason.PIPE_SETUP_FAILED)
    return int(read_handle.value), int(write_handle.value)


def _set_non_inheritable(api: _WindowsProcessApi, handle: int) -> None:
    if not api.SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0):
        raise _native_error(WindowsLaunchBindingReason.PIPE_SETUP_FAILED, None)


def _new_handle_list_attribute(
    api: _WindowsProcessApi,
    handles: list[int],
) -> tuple[ctypes.Array[ctypes.c_char], object]:
    from ctypes import wintypes

    size = ctypes.c_size_t()
    api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    if size.value == 0:
        raise _native_error(WindowsLaunchBindingReason.PIPE_SETUP_FAILED, None)
    attributes = ctypes.create_string_buffer(size.value)
    if not api.InitializeProcThreadAttributeList(ctypes.addressof(attributes), 1, 0, ctypes.byref(size)):
        raise _native_error(WindowsLaunchBindingReason.PIPE_SETUP_FAILED, None)
    handle_list = (wintypes.HANDLE * len(handles))(*handles)
    if not api.UpdateProcThreadAttribute(
        ctypes.addressof(attributes),
        0,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        ctypes.byref(handle_list),
        ctypes.sizeof(handle_list),
        None,
        None,
    ):
        api.DeleteProcThreadAttributeList(ctypes.addressof(attributes))
        raise _native_error(WindowsLaunchBindingReason.PIPE_SETUP_FAILED, None)
    return attributes, handle_list


def _file_from_handle(handle: int, mode: str) -> IO[bytes]:
    import msvcrt

    binary_flag = int(getattr(os, "O_BINARY", 0))
    open_osfhandle = getattr(msvcrt, "open_osfhandle")
    flags = binary_flag | (os.O_WRONLY if mode == "wb" else os.O_RDONLY)
    descriptor = open_osfhandle(handle, flags)
    try:
        return io.FileIO(descriptor, mode, closefd=True)
    except OSError:
        os.close(descriptor)
        raise


def _unicode_environment() -> ctypes.Array[ctypes.c_wchar]:
    system_root = os.environ.get("SystemRoot")
    if not system_root or "\x00" in system_root:
        raise WindowsLaunchBindingError(WindowsLaunchBindingReason.CREATE_PROCESS_FAILED)
    return ctypes.create_unicode_buffer(f"SystemRoot={system_root}\x00\x00")


def _milliseconds(timeout: float) -> int:
    value = float(timeout)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a finite positive number")
    if value > (INFINITE - 1) / 1000:
        raise ValueError("timeout is too large for Windows wait")
    return max(1, math.ceil(value * 1000))


def _close_streams(streams: list[IO[bytes]]) -> list[OSError]:
    failures: list[OSError] = []
    for stream in streams:
        try:
            stream.close()
        except OSError as exc:
            failures.append(exc)
    return failures


def _close_and_remove(api: _WindowsProcessApi, handles: list[int]) -> list[OSError]:
    failures: list[OSError] = []
    for handle in handles[:]:
        try:
            _close_handle(api, handle)
        except OSError as exc:
            failures.append(exc)
        else:
            handles.remove(handle)
    return failures


def _close_handle(api: _WindowsProcessApi, handle: int) -> None:
    if not api.CloseHandle(handle):
        raise _last_error("CloseHandle failed")


def _native_error(reason: WindowsLaunchBindingReason, path: Path | None) -> WindowsLaunchBindingError:
    return WindowsLaunchBindingError(
        reason,
        path,
        winerror=int(getattr(ctypes, "get_last_error", lambda: 0)()),
    )


def _last_error(message: str) -> OSError:
    return OSError(int(getattr(ctypes, "get_last_error", lambda: 0)()), message)


def _error_code(error: OSError) -> int:
    value = getattr(error, "winerror", None)
    return int(value if value is not None else error.errno or 0)
