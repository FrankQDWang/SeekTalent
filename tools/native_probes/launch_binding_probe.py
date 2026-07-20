"""Native-only evidence for the installed sidecar launch-binding decision.

This deliberately does not import product code.  It exercises the OS primitives
that a later production-unreachable implementation may depend on.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


class ProbeFailure(RuntimeError):
    """The native host did not provide the behavior the decision requires."""


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _run_executable(path: Path) -> dict[str, int | str]:
    completed = subprocess.run(
        [str(path)],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return {
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
    }


def _red_popen_toctou(root: Path, trusted_source: Path, replacement_source: Path) -> dict[str, object]:
    """Show that replacing an admitted path changes the image Popen starts."""
    candidate = root / f"candidate{trusted_source.suffix}"
    replacement = root / f"replacement{trusted_source.suffix}"
    shutil.copyfile(trusted_source, candidate)
    shutil.copyfile(replacement_source, replacement)
    candidate.chmod(candidate.stat().st_mode | 0o700)
    replacement.chmod(replacement.stat().st_mode | 0o700)

    before = _run_executable(candidate)
    admitted_digest = _sha256(candidate)
    os.replace(replacement, candidate)
    after = _run_executable(candidate)
    if before == after or admitted_digest == _sha256(candidate):
        raise ProbeFailure("Popen TOCTOU replacement reproducer did not distinguish the two images")
    return {
        "admitted_sha256": admitted_digest,
        "launched_sha256": _sha256(candidate),
        "before": before,
        "after": after,
        "path_replacement_changed_started_image": True,
    }


def _darwin_child_lock_attempt(path: Path) -> int:
    script = """
import errno
import fcntl
import os
import sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as exc:
    if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
        raise SystemExit(0)
    raise
raise SystemExit(1)
"""
    return subprocess.run([sys.executable, "-c", script, str(path)], check=False, timeout=10).returncode


def _darwin_noncooperative_write(path: Path) -> int:
    script = """
import os
import sys
with open(sys.argv[1], "r+b", buffering=0) as output:
    output.write(b"changed")
"""
    return subprocess.run([sys.executable, "-c", script, str(path)], check=False, timeout=10).returncode


def _darwin_lock_and_rename_limits(root: Path) -> dict[str, object]:
    import fcntl

    payload = root / "payload"
    staged = root / "staged"
    payload.write_bytes(b"original")
    staged.write_bytes(b"replacement")
    descriptor = os.open(payload, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cooperative_lock_blocked = _darwin_child_lock_attempt(payload) == 0
        noncooperative_write_succeeded = _darwin_noncooperative_write(payload) == 0
        os.replace(staged, payload)
        replace_succeeded = payload.read_bytes() == b"replacement"
    finally:
        os.close(descriptor)

    slot = root / "slot"
    slot.mkdir()
    slot_descriptor = os.open(slot, os.O_RDONLY)
    try:
        fcntl.flock(slot_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        moved_slot = root / "slot-moved"
        slot.rename(moved_slot)
        directory_rename_succeeded = moved_slot.is_dir()
    finally:
        os.close(slot_descriptor)

    if not cooperative_lock_blocked or not noncooperative_write_succeeded or not replace_succeeded:
        raise ProbeFailure("Darwin flock did not show its expected advisory-only behavior")
    if not directory_rename_succeeded:
        raise ProbeFailure("Darwin open directory descriptor unexpectedly prevented rename")
    return {
        "flock_blocks_cooperating_locker": cooperative_lock_blocked,
        "flock_does_not_block_noncooperative_write": noncooperative_write_succeeded,
        "open_file_and_flock_do_not_block_replace": replace_succeeded,
        "open_directory_and_flock_do_not_block_rename": directory_rename_succeeded,
        "cpython_has_fexecve": hasattr(os, "fexecve"),
        "cpython_has_posix_spawn": hasattr(os, "posix_spawn"),
    }


def _darwin_dynamic_code_identity(root: Path) -> dict[str, object]:
    helper_source = Path(__file__).with_name("macos_dynamic_code_identity.c")
    helper = root / "macos_dynamic_code_identity"
    compiled = subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Werror",
            "-framework",
            "Security",
            "-framework",
            "CoreFoundation",
            str(helper_source),
            "-o",
            str(helper),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if compiled.returncode != 0:
        raise ProbeFailure(f"could not compile Security.framework probe: {compiled.stderr.strip()}")

    system_child = subprocess.Popen(["/bin/sleep", "10"])
    try:
        trusted = _run_identity_helper(helper, system_child.pid)
    finally:
        _stop_child(system_child)

    marker = root / "untrusted-child-ran"
    suspended = _run_suspended_identity_helper(helper, marker)

    if trusted["guest_status"] != 0 or trusted["dynamic_validity_status"] != 0:
        raise ProbeFailure("Security.framework could not dynamically validate a live Apple-signed process")
    if trusted["apple_requirement_status"] != 0:
        raise ProbeFailure("Security.framework rejected /bin/sleep for the Apple anchor requirement")
    if (
        suspended["guest_status"] != 0
        or suspended["apple_requirement_status"] == 0
        or not suspended["marker_absent_before_resume"]
        or not suspended["marker_absent_after_reap"]
        or not suspended["child_killed_without_resume"]
    ):
        raise ProbeFailure("suspended Security.framework gate did not kill the unauthorized local child")
    return {
        "apple_signed_child": trusted,
        "suspended_local_child": suspended,
        "pid_dynamic_identity_is_available": True,
        "apple_requirement_fails_closed_before_resume": True,
    }


def _run_identity_helper(helper: Path, pid: int) -> dict[str, int]:
    completed = subprocess.run([str(helper), str(pid)], check=False, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        raise ProbeFailure(f"Security.framework helper failed: {completed.stderr.strip()}")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or not all(isinstance(item, int) for item in value.values()):
        raise ProbeFailure("Security.framework helper returned an invalid result")
    return value


def _run_suspended_identity_helper(helper: Path, marker: Path) -> dict[str, int | bool]:
    completed = subprocess.run(
        [str(helper), "--spawn-suspended-and-reject", str(marker)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise ProbeFailure(f"suspended Security.framework helper failed: {completed.stderr.strip()}")
    value = json.loads(completed.stdout)
    expected = {
        "marker_absent_before_resume",
        "marker_absent_after_reap",
        "child_killed_without_resume",
        "guest_status",
        "dynamic_validity_status",
        "apple_requirement_status",
    }
    if set(value) != expected or not all(isinstance(item, (bool, int)) for item in value.values()):
        raise ProbeFailure("suspended Security.framework helper returned an invalid result")
    return value


def _stop_child(child: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def _darwin_probe(root: Path) -> dict[str, object]:
    trusted = root / "trusted-sidecar"
    replacement = root / "replacement-sidecar"
    trusted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    replacement.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    trusted.chmod(0o700)
    replacement.chmod(0o700)
    return {
        "popen_toctou": _red_popen_toctou(root, trusted, replacement),
        "fd_and_flock_limits": _darwin_lock_and_rename_limits(root),
        "security_framework": _darwin_dynamic_code_identity(root),
    }


def _windows_api():
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.MoveFileExW.restype = wintypes.BOOL
    kernel32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
    kernel32.DeleteFileW.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
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
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    return kernel32


def _windows_last_error() -> int:
    return int(getattr(ctypes, "get_last_error")())


def _windows_set_last_error(value: int) -> None:
    getattr(ctypes, "set_last_error")(value)


class _WindowsFileIdInfo(ctypes.Structure):
    _fields_ = [("volume_serial_number", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]


class _WindowsStartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("lp_reserved", ctypes.c_wchar_p),
        ("lp_desktop", ctypes.c_wchar_p),
        ("lp_title", ctypes.c_wchar_p),
        ("dw_x", ctypes.c_ulong),
        ("dw_y", ctypes.c_ulong),
        ("dw_x_size", ctypes.c_ulong),
        ("dw_y_size", ctypes.c_ulong),
        ("dw_x_count_chars", ctypes.c_ulong),
        ("dw_y_count_chars", ctypes.c_ulong),
        ("dw_fill_attribute", ctypes.c_ulong),
        ("dw_flags", ctypes.c_ulong),
        ("w_show_window", ctypes.c_ushort),
        ("cb_reserved2", ctypes.c_ushort),
        ("lp_reserved2", ctypes.POINTER(ctypes.c_byte)),
        ("h_std_input", ctypes.c_void_p),
        ("h_std_output", ctypes.c_void_p),
        ("h_std_error", ctypes.c_void_p),
    ]


class _WindowsProcessInformation(ctypes.Structure):
    _fields_ = [
        ("process", ctypes.c_void_p),
        ("thread", ctypes.c_void_p),
        ("process_id", ctypes.c_ulong),
        ("thread_id", ctypes.c_ulong),
    ]


def _windows_handle(kernel32, path: Path, *, desired_access: int, directory: bool = False):
    from ctypes import wintypes

    file_share_read = 0x00000001
    open_existing = 3
    backup_semantics = 0x02000000 if directory else 0
    handle = kernel32.CreateFileW(
        str(path),
        desired_access,
        file_share_read,
        None,
        open_existing,
        backup_semantics,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        raise ProbeFailure(f"CreateFileW failed for {path}: {_windows_last_error()}")
    return handle


def _windows_file_identity(kernel32, handle) -> dict[str, int | str]:
    file_id_info = _WindowsFileIdInfo()
    file_id_info_class = 18
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        file_id_info_class,
        ctypes.byref(file_id_info),
        ctypes.sizeof(file_id_info),
    ):
        raise ProbeFailure(f"GetFileInformationByHandleEx(FILE_ID_INFO) failed: {_windows_last_error()}")
    required_size = kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if required_size == 0:
        raise ProbeFailure(f"GetFinalPathNameByHandleW size query failed: {_windows_last_error()}")
    path_buffer = ctypes.create_unicode_buffer(required_size + 1)
    returned_size = kernel32.GetFinalPathNameByHandleW(handle, path_buffer, len(path_buffer), 0)
    if returned_size == 0 or returned_size >= len(path_buffer):
        raise ProbeFailure(f"GetFinalPathNameByHandleW failed: {_windows_last_error()}")
    return {
        "final_path": path_buffer.value,
        "volume_serial_number": int(file_id_info.volume_serial_number),
        "file_id": bytes(file_id_info.file_id).hex(),
    }


def _windows_create_suspended(
    kernel32,
    executable: Path,
    working_directory: Path,
    expected_identity: dict[str, int | str],
) -> dict[str, int | str]:
    from ctypes import wintypes

    create_suspended = 0x00000004
    create_new_process_group = 0x00000200
    wait_object_0 = 0
    wait_failed = 0xFFFFFFFF
    startup = _WindowsStartupInfo()
    startup.cb = ctypes.sizeof(startup)
    process = _WindowsProcessInformation()
    created = kernel32.CreateProcessW(
        str(executable),
        None,
        None,
        None,
        False,
        create_suspended | create_new_process_group,
        None,
        str(working_directory),
        ctypes.byref(startup),
        ctypes.byref(process),
    )
    if not created:
        raise ProbeFailure(f"CreateProcessW(CREATE_SUSPENDED) failed: {_windows_last_error()}")
    try:
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_length = wintypes.DWORD(len(image_buffer))
        if not kernel32.QueryFullProcessImageNameW(process.process, 0, image_buffer, ctypes.byref(image_length)):
            raise ProbeFailure(f"QueryFullProcessImageNameW failed: {_windows_last_error()}")
        child_image_path = image_buffer.value
        child_image = Path(child_image_path)
        if not child_image.is_absolute():
            kernel32.TerminateProcess(process.process, 1)
            kernel32.WaitForSingleObject(process.process, 10_000)
            raise ProbeFailure("QueryFullProcessImageNameW returned a non-absolute path")
        child_image_handle = _windows_handle(kernel32, child_image, desired_access=0x80000000)
        try:
            child_identity = _windows_file_identity(kernel32, child_image_handle)
        finally:
            kernel32.CloseHandle(child_image_handle)
        if child_identity != expected_identity:
            kernel32.TerminateProcess(process.process, 1)
            kernel32.WaitForSingleObject(process.process, 10_000)
            raise ProbeFailure(
                "suspended Windows child image did not match the admitted path and file identity: "
                f"raw_process_image_path={child_image_path!r}, "
                f"expected_identity={expected_identity!r}, observed_identity={child_identity!r}"
            )
        if kernel32.ResumeThread(process.thread) == 0xFFFFFFFF:
            raise ProbeFailure(f"ResumeThread failed: {_windows_last_error()}")
        wait_result = kernel32.WaitForSingleObject(process.process, 10_000)
        if wait_result == wait_failed:
            raise ProbeFailure(f"WaitForSingleObject failed: {_windows_last_error()}")
        if wait_result != wait_object_0:
            kernel32.TerminateProcess(process.process, 1)
            kernel32.WaitForSingleObject(process.process, 10_000)
            raise ProbeFailure("suspended Windows child did not exit within the probe timeout")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process.process, ctypes.byref(exit_code)):
            raise ProbeFailure(f"GetExitCodeProcess failed: {_windows_last_error()}")
        return {
            "raw_process_image_path": child_image_path,
            "admitted_final_path": str(expected_identity["final_path"]),
            "observed_final_path": str(child_identity["final_path"]),
            "child_file_id": str(child_identity["file_id"]),
            "child_exit_code": int(exit_code.value),
            "created_suspended": True,
        }
    finally:
        kernel32.CloseHandle(process.thread)
        kernel32.CloseHandle(process.process)


def _windows_attempt(kernel32, operation: Callable[[], object]) -> dict[str, int | bool]:
    del kernel32
    _windows_set_last_error(0)
    succeeded = bool(operation())
    return {"succeeded": succeeded, "error": _windows_last_error()}


def _windows_file_lease(root: Path, source: Path, replacement_source: Path) -> dict[str, object]:
    kernel32 = _windows_api()
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    movefile_replace_existing = 0x00000001
    payload = root / "payload.exe"
    staged = root / "staged.exe"
    shutil.copyfile(source, payload)
    shutil.copyfile(replacement_source, staged)

    lease = _windows_handle(kernel32, payload, desired_access=generic_read)
    try:
        admitted_identity = _windows_file_identity(kernel32, lease)

        def open_for_write() -> bool:
            handle = kernel32.CreateFileW(str(payload), generic_write, 0x00000007, None, 3, 0, None)
            if handle == wintypes.HANDLE(-1).value:
                return False
            kernel32.CloseHandle(handle)
            return True

        write_attempt = _windows_attempt(
            kernel32,
            open_for_write,
        )
        replace_attempt = _windows_attempt(
            kernel32,
            lambda: kernel32.MoveFileExW(str(staged), str(payload), movefile_replace_existing),
        )
        delete_attempt = _windows_attempt(kernel32, lambda: kernel32.DeleteFileW(str(payload)))
        started = _windows_create_suspended(kernel32, payload, root, admitted_identity)
    finally:
        kernel32.CloseHandle(lease)

    after_release_replace = _windows_attempt(
        kernel32,
        lambda: kernel32.MoveFileExW(str(staged), str(payload), movefile_replace_existing),
    )
    if write_attempt["succeeded"] or replace_attempt["succeeded"] or delete_attempt["succeeded"]:
        raise ProbeFailure("FILE_SHARE_READ lease permitted a write, replace, or delete")
    if started["child_exit_code"] != 0 or not started["created_suspended"]:
        raise ProbeFailure("CreateProcessW could not start and resume a file under FILE_SHARE_READ lease")
    if not after_release_replace["succeeded"]:
        raise ProbeFailure("replacement did not become possible after closing the Windows file lease")
    return {
        "write_while_leased": write_attempt,
        "replace_while_leased": replace_attempt,
        "delete_while_leased": delete_attempt,
        "create_process_under_file_lease": started,
        "replace_after_release": after_release_replace,
    }


def _windows_component_lease(root: Path) -> dict[str, dict[str, int | bool]]:
    kernel32 = _windows_api()
    generic_read = 0x80000000
    movefile_replace_existing = 0x00000001
    slot = root / "slot"
    release = slot / "release"
    component = release / "component"
    component.mkdir(parents=True)
    outcomes: dict[str, dict[str, int | bool]] = {}
    for path in (slot, release, component):
        lease = _windows_handle(kernel32, path, desired_access=generic_read, directory=True)
        try:
            destination = path.with_name(f"{path.name}-moved")
            outcome = _windows_attempt(
                kernel32,
                lambda: kernel32.MoveFileExW(str(path), str(destination), movefile_replace_existing),
            )
            outcomes[path.name] = outcome
        finally:
            kernel32.CloseHandle(lease)
    if any(value["succeeded"] for value in outcomes.values()):
        raise ProbeFailure("a Windows component lease permitted its directory to be renamed")
    return outcomes


def _windows_preexisting_writer(root: Path) -> dict[str, object]:
    kernel32 = _windows_api()
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    open_existing = 3
    file_share_read = 0x00000001
    file_share_read_write = 0x00000003
    payload = root / "preexisting-writer.bin"
    payload.write_bytes(b"original")
    writer = kernel32.CreateFileW(str(payload), generic_write, file_share_read_write, None, open_existing, 0, None)
    if writer == wintypes.HANDLE(-1).value:
        raise ProbeFailure(f"could not create simulated preexisting writer: {_windows_last_error()}")
    try:
        lease = kernel32.CreateFileW(
            str(payload),
            generic_read,
            file_share_read,
            None,
            open_existing,
            0,
            None,
        )
        if lease != wintypes.HANDLE(-1).value:
            kernel32.CloseHandle(lease)
            raise ProbeFailure("share-deny lease unexpectedly coexisted with a preexisting writer")
        error = _windows_last_error()
    finally:
        kernel32.CloseHandle(writer)
    if error != 32:
        raise ProbeFailure(f"preexisting writer did not produce ERROR_SHARING_VIOLATION: {error}")
    return {"preexisting_writer_causes_share_mode_conflict": True, "error": error}


def _windows_probe(root: Path) -> dict[str, object]:
    system_root = Path(os.environ["SystemRoot"])
    return {
        "popen_toctou": _red_popen_toctou(
            root,
            system_root / "System32" / "hostname.exe",
            system_root / "System32" / "where.exe",
        ),
        "createfile_file_lease": _windows_file_lease(
            root,
            system_root / "System32" / "hostname.exe",
            system_root / "System32" / "where.exe",
        ),
        "createfile_component_leases": _windows_component_lease(root),
        "preexisting_writer_limit": _windows_preexisting_writer(root),
    }


def run_probe() -> dict[str, object]:
    if os.name == "nt":
        platform_result = "windows"
    elif sys.platform == "darwin":
        platform_result = "macos"
    else:
        raise ProbeFailure(f"native evidence is only defined for Windows or macOS, not {sys.platform}")
    with tempfile.TemporaryDirectory(prefix="seektalent-launch-binding-") as temporary:
        root = Path(temporary)
        evidence = _windows_probe(root) if platform_result == "windows" else _darwin_probe(root)
    return {
        "schema_version": "seektalent.native_launch_binding_probe.v1",
        "platform": platform_result,
        "architecture": platform.machine().lower(),
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    try:
        result = run_probe()
    except ProbeFailure as exc:
        print(f"native launch-binding probe failed: {exc}", file=sys.stderr)
        return 1
    if arguments.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
