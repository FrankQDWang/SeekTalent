"""Windows-only native evidence for the immutable-slot decision."""

from __future__ import annotations

import ctypes
import os
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from launch_binding_common import ProbeFailure, red_popen_toctou


RACE_ITERATIONS = 1_000
RACE_BUDGET_SECONDS = 180
FILE_SHARE_READ = 0x00000001
FILE_SHARE_READ_WRITE = 0x00000003
FILE_SHARE_ALL = 0x00000007
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
MOVEFILE_REPLACE_EXISTING = 0x00000001
ERROR_SHARING_VIOLATION = 32
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
SYMBOLIC_LINK_FLAG_DIRECTORY = 0x1
SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE = 0x2
DRIVE_FIXED = 3


def _api():
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
    kernel32.RemoveDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel32.RemoveDirectoryW.restype = wintypes.BOOL
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
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetVolumePathNameW.restype = wintypes.BOOL
    kernel32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetFileAttributesW.restype = wintypes.DWORD
    kernel32.CreateSymbolicLinkW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.CreateSymbolicLinkW.restype = wintypes.BOOLEAN
    return kernel32


def _last_error() -> int:
    return int(getattr(ctypes, "get_last_error")())


def _set_last_error(value: int) -> None:
    getattr(ctypes, "set_last_error")(value)


class _FileIdInfo(ctypes.Structure):
    _fields_ = [("volume_serial_number", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("file_attributes", ctypes.c_ulong), ("reparse_tag", ctypes.c_ulong)]


class ReparsePointRejected(ProbeFailure):
    """A concrete slot path component was a reparse point."""


class _StartupInfo(ctypes.Structure):
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


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("process", ctypes.c_void_p),
        ("thread", ctypes.c_void_p),
        ("process_id", ctypes.c_ulong),
        ("thread_id", ctypes.c_ulong),
    ]


def _handle(kernel32, path: Path, *, desired_access: int, directory: bool = False):
    from ctypes import wintypes

    handle = kernel32.CreateFileW(
        str(path),
        desired_access,
        FILE_SHARE_READ,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | (FILE_FLAG_BACKUP_SEMANTICS if directory else 0),
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ProbeFailure(f"CreateFileW failed for {path}: {_last_error()}")
    attribute_tag_info = _FileAttributeTagInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        9,
        ctypes.byref(attribute_tag_info),
        ctypes.sizeof(attribute_tag_info),
    ):
        error = _last_error()
        kernel32.CloseHandle(handle)
        raise ProbeFailure(f"GetFileInformationByHandleEx(FileAttributeTagInfo) failed for {path}: {error}")
    if attribute_tag_info.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        kernel32.CloseHandle(handle)
        raise ReparsePointRejected(
            f"slot_reparse_point_rejected for {path}: tag={attribute_tag_info.reparse_tag}"
        )
    return handle


def _identity(kernel32, handle) -> dict[str, int | str]:
    file_id_info = _FileIdInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        18,
        ctypes.byref(file_id_info),
        ctypes.sizeof(file_id_info),
    ):
        raise ProbeFailure(f"GetFileInformationByHandleEx(FILE_ID_INFO) failed: {_last_error()}")
    required_size = kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if required_size == 0:
        raise ProbeFailure(f"GetFinalPathNameByHandleW size query failed: {_last_error()}")
    path_buffer = ctypes.create_unicode_buffer(required_size + 1)
    returned_size = kernel32.GetFinalPathNameByHandleW(handle, path_buffer, len(path_buffer), 0)
    if returned_size == 0 or returned_size >= len(path_buffer):
        raise ProbeFailure(f"GetFinalPathNameByHandleW failed: {_last_error()}")
    return {
        "final_path": path_buffer.value,
        "volume_serial_number": int(file_id_info.volume_serial_number),
        "file_id": bytes(file_id_info.file_id).hex(),
    }


def _terminate_and_reap(kernel32, process) -> dict[str, int | bool]:
    terminate_succeeded = bool(kernel32.TerminateProcess(process, 1))
    terminate_error = 0 if terminate_succeeded else _last_error()
    wait_result = kernel32.WaitForSingleObject(process, 10_000)
    wait_error = _last_error() if wait_result == 0xFFFFFFFF else 0
    return {
        "terminate_succeeded": terminate_succeeded,
        "terminate_error": terminate_error,
        "wait_result": int(wait_result),
        "wait_error": wait_error,
        "reaped": wait_result == 0,
    }


def _create_suspended(
    kernel32,
    executable: Path,
    working_directory: Path,
    expected_identity: dict[str, int | str],
) -> dict[str, object]:
    from ctypes import wintypes

    startup = _StartupInfo()
    startup.cb = ctypes.sizeof(startup)
    process = _ProcessInformation()
    created = kernel32.CreateProcessW(
        str(executable),
        None,
        None,
        None,
        False,
        0x00000004 | 0x00000200,
        None,
        str(working_directory),
        ctypes.byref(startup),
        ctypes.byref(process),
    )
    if not created:
        raise ProbeFailure(f"CreateProcessW(CREATE_SUSPENDED) failed: {_last_error()}")

    failure: ProbeFailure | None = None
    result: dict[str, object] = {"created_suspended": True}
    try:
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_length = wintypes.DWORD(len(image_buffer))
        if not kernel32.QueryFullProcessImageNameW(process.process, 0, image_buffer, ctypes.byref(image_length)):
            raise ProbeFailure(f"QueryFullProcessImageNameW failed: {_last_error()}")
        raw_process_image_path = image_buffer.value
        raw_process_image = Path(raw_process_image_path)
        if not raw_process_image.is_absolute():
            raise ProbeFailure("QueryFullProcessImageNameW returned a non-absolute path")
        raw_image_handle = _handle(kernel32, raw_process_image, desired_access=GENERIC_READ)
        try:
            observed_identity = _identity(kernel32, raw_image_handle)
        finally:
            kernel32.CloseHandle(raw_image_handle)
        identity_match = observed_identity == expected_identity
        result.update(
            {
                "raw_process_image_path": raw_process_image_path,
                "admitted_final_path": str(expected_identity["final_path"]),
                "observed_final_path": str(observed_identity["final_path"]),
                "admitted_file_id": str(expected_identity["file_id"]),
                "observed_file_id": str(observed_identity["file_id"]),
                "identity_match": identity_match,
                "raw_path_is_corroborating_name_evidence": True,
            }
        )
    except ProbeFailure as exc:
        failure = exc
    finally:
        cleanup = _terminate_and_reap(kernel32, process.process)
        kernel32.CloseHandle(process.thread)
        kernel32.CloseHandle(process.process)
    result["cleanup"] = cleanup
    if failure is not None:
        raise ProbeFailure(f"{failure}; suspended_child_cleanup={cleanup!r}")
    if not cleanup["terminate_succeeded"] or not cleanup["reaped"]:
        raise ProbeFailure(f"suspended Windows child cleanup failed: {cleanup!r}")
    result["terminated_while_suspended"] = True
    result["failed_closed"] = not bool(result["identity_match"])
    return result


def _attempt(operation: Callable[[], bool]) -> dict[str, int | bool]:
    _set_last_error(0)
    succeeded = bool(operation())
    return {"succeeded": succeeded, "error": _last_error()}


def _open_for_write(kernel32, path: Path) -> bool:
    from ctypes import wintypes

    handle = kernel32.CreateFileW(str(path), GENERIC_WRITE, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        return False
    kernel32.CloseHandle(handle)
    return True


def _race_start_and_replace(
    kernel32,
    payload: Path,
    staged: Path,
    working_directory: Path,
    expected_identity: dict[str, int | str],
) -> dict[str, object]:
    replace_errors: Counter[int] = Counter()
    identity_verified = 0
    started_at = time.monotonic()
    for _ in range(RACE_ITERATIONS):
        if time.monotonic() - started_at > RACE_BUDGET_SECONDS:
            raise ProbeFailure(f"Windows bounded start-versus-replace race exceeded {RACE_BUDGET_SECONDS}s")
        barrier = threading.Barrier(2)
        raced: dict[str, dict[str, int | bool]] = {}

        def replace_path() -> None:
            barrier.wait(timeout=10)
            raced["result"] = _attempt(
                lambda: kernel32.MoveFileExW(str(staged), str(payload), MOVEFILE_REPLACE_EXISTING)
            )

        racer = threading.Thread(target=replace_path)
        racer.start()
        try:
            barrier.wait(timeout=10)
            started = _create_suspended(kernel32, payload, working_directory, expected_identity)
        finally:
            racer.join(timeout=1)
        outcome = raced.get("result")
        if racer.is_alive() or outcome is None:
            raise ProbeFailure("Windows start-versus-replace racer did not complete")
        if bool(outcome["succeeded"]):
            raise ProbeFailure("Windows lease allowed a replacement during the bounded start race")
        replace_errors[int(outcome["error"])] += 1
        if started["identity_match"] is not True or started["terminated_while_suspended"] is not True:
            raise ProbeFailure("Windows race started a child without verified identity and cleanup")
        identity_verified += 1
    if set(replace_errors) != {ERROR_SHARING_VIOLATION}:
        raise ProbeFailure(f"Windows start-versus-replace race had unexplained errors: {dict(replace_errors)!r}")
    return {
        "iterations": RACE_ITERATIONS,
        "runtime_budget_seconds": RACE_BUDGET_SECONDS,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "identity_verified_child_count": identity_verified,
        "unauthorized_child_count": 0,
        "unexplained_result_count": 0,
        "replace_error_histogram": {str(error): count for error, count in sorted(replace_errors.items())},
    }


def _full_slot_lease(root: Path, source: Path, replacement_source: Path) -> dict[str, object]:
    kernel32 = _api()
    installed_root = root / "installed-root"
    slots = installed_root / "slots"
    release = slots / "release-001"
    binary_directory = release / "bin"
    binary_directory.mkdir(parents=True)
    payload = binary_directory / "payload.exe"
    staged = root / "staged.exe"
    shutil.copyfile(source, payload)
    shutil.copyfile(replacement_source, staged)
    components = (installed_root, slots, release, binary_directory)
    leases: list[object] = []
    try:
        for component in components:
            leases.append(_handle(kernel32, component, desired_access=GENERIC_READ, directory=True))
        leaf_lease = _handle(kernel32, payload, desired_access=GENERIC_READ)
        leases.append(leaf_lease)
        admitted_identity = _identity(kernel32, leaf_lease)
        component_renames = {
            component.name: _attempt(
                lambda component=component: kernel32.MoveFileExW(
                    str(component),
                    str(component.with_name(f"{component.name}-moved")),
                    MOVEFILE_REPLACE_EXISTING,
                )
            )
            for component in components
        }
        component_deletes = {
            component.name: _attempt(lambda component=component: kernel32.RemoveDirectoryW(str(component)))
            for component in components
        }
        leaf_write = _attempt(lambda: _open_for_write(kernel32, payload))
        leaf_rename = _attempt(
            lambda: kernel32.MoveFileExW(str(payload), str(payload.with_name("payload-moved.exe")), 0)
        )
        leaf_delete = _attempt(lambda: kernel32.DeleteFileW(str(payload)))
        leaf_replace = _attempt(
            lambda: kernel32.MoveFileExW(str(staged), str(payload), MOVEFILE_REPLACE_EXISTING)
        )
        started = _create_suspended(kernel32, payload, binary_directory, admitted_identity)
        altered_identity = dict(admitted_identity)
        altered_identity["file_id"] = "00" * 16
        mismatch = _create_suspended(kernel32, payload, binary_directory, altered_identity)
        race = _race_start_and_replace(kernel32, payload, staged, binary_directory, admitted_identity)
    finally:
        for lease in reversed(leases):
            kernel32.CloseHandle(lease)

    controls = _after_release_controls(kernel32, payload, staged, source)
    failed_operations = [
        *component_renames.values(),
        *component_deletes.values(),
        leaf_write,
        leaf_rename,
        leaf_delete,
        leaf_replace,
    ]
    if any(bool(outcome["succeeded"]) for outcome in failed_operations):
        raise ProbeFailure("complete Windows slot lease permitted a protected path mutation")
    if started["identity_match"] is not True or started["terminated_while_suspended"] is not True:
        raise ProbeFailure("complete Windows slot lease could not bind and clean up the admitted image")
    if mismatch["identity_match"] is not False or mismatch["failed_closed"] is not True:
        raise ProbeFailure("Windows image mismatch control did not fail closed")
    if mismatch["cleanup"] != started["cleanup"]:
        raise ProbeFailure("Windows mismatch control did not use the same cleanup path")
    if not all(controls.values()):
        raise ProbeFailure("Windows controls did not become possible after complete lease release")
    return {
        "path_components": [str(component) for component in components],
        "all_chain_handles_live_through_identity_gate": True,
        "lease_handle_count": len(leases),
        "component_rename_while_leased": component_renames,
        "component_delete_while_leased": component_deletes,
        "write_while_leased": leaf_write,
        "rename_while_leased": leaf_rename,
        "delete_while_leased": leaf_delete,
        "replace_while_leased": leaf_replace,
        "create_process_under_full_lease": started,
        "identity_mismatch_cleanup": mismatch,
        "start_replace_race": race,
        "controls_after_release": controls,
    }


def _after_release_controls(kernel32, payload: Path, staged: Path, source: Path) -> dict[str, bool]:
    moved = payload.with_name("payload-moved.exe")
    write_succeeded = _open_for_write(kernel32, payload)
    rename_succeeded = bool(kernel32.MoveFileExW(str(payload), str(moved), 0))
    if rename_succeeded:
        rename_succeeded = bool(kernel32.MoveFileExW(str(moved), str(payload), 0))
    delete_succeeded = bool(kernel32.DeleteFileW(str(payload)))
    if delete_succeeded:
        shutil.copyfile(source, payload)
    replace_succeeded = bool(kernel32.MoveFileExW(str(staged), str(payload), MOVEFILE_REPLACE_EXISTING))
    return {
        "write": write_succeeded,
        "rename_round_trip": rename_succeeded,
        "delete": delete_succeeded,
        "replace": replace_succeeded,
    }


def _preexisting_writer(root: Path) -> dict[str, object]:
    from ctypes import wintypes

    kernel32 = _api()
    payload = root / "preexisting-writer.bin"
    payload.write_bytes(b"original")
    writer = kernel32.CreateFileW(
        str(payload),
        GENERIC_WRITE,
        FILE_SHARE_READ_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if writer == wintypes.HANDLE(-1).value:
        raise ProbeFailure(f"could not create simulated preexisting writer: {_last_error()}")
    try:
        lease = kernel32.CreateFileW(str(payload), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None)
        if lease != wintypes.HANDLE(-1).value:
            kernel32.CloseHandle(lease)
            raise ProbeFailure("share-deny lease unexpectedly coexisted with a preexisting writer")
        error = _last_error()
    finally:
        kernel32.CloseHandle(writer)
    if error != ERROR_SHARING_VIOLATION:
        raise ProbeFailure(f"preexisting writer did not produce ERROR_SHARING_VIOLATION: {error}")
    return {"preexisting_writer_causes_share_mode_conflict": True, "error": error}


def _supported_local_filesystem(root: Path) -> dict[str, int | str | bool]:
    from ctypes import wintypes

    kernel32 = _api()
    volume_path = ctypes.create_unicode_buffer(32768)
    if not kernel32.GetVolumePathNameW(str(root), volume_path, len(volume_path)):
        raise ProbeFailure(f"GetVolumePathNameW failed: {_last_error()}")
    filesystem_name = ctypes.create_unicode_buffer(261)
    serial_number = wintypes.DWORD()
    if not kernel32.GetVolumeInformationW(
        volume_path.value,
        None,
        0,
        ctypes.byref(serial_number),
        None,
        None,
        filesystem_name,
        len(filesystem_name),
    ):
        raise ProbeFailure(f"GetVolumeInformationW failed: {_last_error()}")
    drive_type = int(kernel32.GetDriveTypeW(volume_path.value))
    supported = filesystem_name.value.upper() == "NTFS" and drive_type == DRIVE_FIXED
    if not supported:
        raise ProbeFailure(
            f"unsupported Windows probe filesystem: {filesystem_name.value!r}, drive_type={drive_type}"
        )
    return {
        "volume_path": volume_path.value,
        "filesystem_name": filesystem_name.value,
        "drive_type": drive_type,
        "supported_local_filesystem": True,
    }


def _reparse_component_rejected(root: Path) -> dict[str, object]:
    kernel32 = _api()
    target = root / "reparse-target"
    reparse_component = root / "reparse-component"
    target.mkdir()
    created = kernel32.CreateSymbolicLinkW(
        str(reparse_component),
        str(target),
        SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE,
    )
    if not created:
        raise ProbeFailure(f"CreateSymbolicLinkW reparse control failed: {_last_error()}")
    try:
        try:
            handle = _handle(kernel32, reparse_component, desired_access=GENERIC_READ, directory=True)
        except ReparsePointRejected as exc:
            return {
                "opened_with_open_reparse_point": True,
                "handle_attribute_tag_checked": True,
                "rejected_before_lease_or_spawn": True,
                "spawn_attempted": False,
                "reason": str(exc),
            }
        kernel32.CloseHandle(handle)
        raise ProbeFailure("Windows reparse component was accepted by the real lease acquisition path")
    finally:
        os.rmdir(reparse_component)


def probe(root: Path) -> dict[str, object]:
    system_root = Path(os.environ["SystemRoot"])
    hostname = system_root / "System32" / "hostname.exe"
    replacement = system_root / "System32" / "where.exe"
    filesystem = _supported_local_filesystem(root)
    reparse = _reparse_component_rejected(root)
    full_lease = _full_slot_lease(root, hostname, replacement)
    return {
        "popen_toctou": red_popen_toctou(root, hostname, replacement),
        "supported_local_filesystem": filesystem,
        "reparse_component": reparse,
        "createfile_file_lease": full_lease,
        "createfile_component_leases": full_lease["component_rename_while_leased"],
        "preexisting_writer_limit": _preexisting_writer(root),
    }
