"""Small Win32 file-handle primitives with no SeekTalent domain dependencies."""

from __future__ import annotations

import ctypes
import ntpath
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from pathlib import Path
from typing import Protocol


FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_READ_ATTRIBUTES = 0x00000080
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
DRIVE_FIXED = 3
FILE_BEGIN = 0
READ_CHUNK_SIZE = 1024 * 1024


class WindowsApi(Protocol):
    def CreateFileW(self, *args: object) -> int: ...

    def CloseHandle(self, *args: object) -> int: ...

    def GetFileInformationByHandleEx(self, *args: object) -> int: ...

    def GetFinalPathNameByHandleW(self, *args: object) -> int: ...

    def SetFilePointerEx(self, *args: object) -> int: ...

    def ReadFile(self, *args: object) -> int: ...

    def GetVolumePathNameW(self, *args: object) -> int: ...

    def GetVolumeInformationW(self, *args: object) -> int: ...

    def GetDriveTypeW(self, *args: object) -> int: ...

    def QueryFullProcessImageNameW(self, *args: object) -> int: ...


class _FileIdInfo(ctypes.Structure):
    _fields_ = [
        ("volume_serial_number", ctypes.c_ulonglong),
        ("file_id", ctypes.c_ubyte * 16),
    ]


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("file_attributes", ctypes.c_ulong),
    ]


class _FileStandardInfo(ctypes.Structure):
    _fields_ = [
        ("allocation_size", ctypes.c_longlong),
        ("end_of_file", ctypes.c_longlong),
        ("number_of_links", ctypes.c_ulong),
        ("delete_pending", ctypes.c_ubyte),
        ("directory", ctypes.c_ubyte),
    ]


@dataclass(frozen=True, slots=True)
class WindowsFileIdentity:
    final_path: str
    volume_serial_number: int
    file_id: bytes
    size: int
    link_count: int
    creation_time: int
    last_write_time: int
    change_time: int
    file_attributes: int
    directory: bool


@cache
def windows_api() -> WindowsApi:
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
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
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
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    return kernel32


def open_windows_object(path: Path, *, directory: bool) -> int:
    handle = windows_api().CreateFileW(
        str(path),
        FILE_READ_ATTRIBUTES if directory else GENERIC_READ,
        FILE_SHARE_READ,
        None,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | (FILE_FLAG_BACKUP_SEMANTICS if directory else 0),
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise _windows_error(f"CreateFileW failed for {path}")
    return int(handle)


def close_windows_handle(handle: int) -> None:
    if not windows_api().CloseHandle(handle):
        raise _windows_error("CloseHandle failed")


def query_windows_file_identity(handle: int, path: Path) -> WindowsFileIdentity:
    api = windows_api()
    file_id = _FileIdInfo()
    basic = _FileBasicInfo()
    standard = _FileStandardInfo()
    for info_class, value in ((18, file_id), (0, basic), (1, standard)):
        if not api.GetFileInformationByHandleEx(
            handle,
            info_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            raise _windows_error(f"GetFileInformationByHandleEx failed for {path}")
    required = api.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if required == 0:
        raise _windows_error(f"GetFinalPathNameByHandleW size query failed for {path}")
    buffer = ctypes.create_unicode_buffer(required + 1)
    returned = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if returned == 0 or returned >= len(buffer):
        raise _windows_error(f"GetFinalPathNameByHandleW failed for {path}")
    return WindowsFileIdentity(
        final_path=normalize_windows_path(buffer.value),
        volume_serial_number=int(file_id.volume_serial_number),
        file_id=bytes(file_id.file_id),
        size=int(standard.end_of_file),
        link_count=int(standard.number_of_links),
        creation_time=int(basic.creation_time),
        last_write_time=int(basic.last_write_time),
        change_time=int(basic.change_time),
        file_attributes=int(basic.file_attributes),
        directory=bool(standard.directory),
    )


def read_windows_file(handle: int, size: int, path: Path) -> bytes:
    _seek_start(handle, path)
    content = bytearray()
    remaining = size
    while remaining:
        chunk = _read(handle, min(remaining, READ_CHUNK_SIZE), path)
        if not chunk:
            raise EOFError(f"installed file became shorter while reading: {path}")
        content.extend(chunk)
        remaining -= len(chunk)
    if _read(handle, 1, path):
        raise EOFError(f"installed file became longer while reading: {path}")
    return bytes(content)


def hash_windows_file(handle: int, size: int, path: Path) -> str:
    _seek_start(handle, path)
    digest = sha256()
    remaining = size
    while remaining:
        chunk = _read(handle, min(remaining, READ_CHUNK_SIZE), path)
        if not chunk:
            raise EOFError(f"installed executable became shorter while hashing: {path}")
        digest.update(chunk)
        remaining -= len(chunk)
    if _read(handle, 1, path):
        raise EOFError(f"installed executable became longer while hashing: {path}")
    return digest.hexdigest()


def require_supported_local_ntfs(root: Path) -> None:
    from ctypes import wintypes

    api = windows_api()
    volume_path = ctypes.create_unicode_buffer(32768)
    if not api.GetVolumePathNameW(str(root), volume_path, len(volume_path)):
        raise _windows_error(f"GetVolumePathNameW failed for {root}")
    filesystem_name = ctypes.create_unicode_buffer(261)
    serial_number = wintypes.DWORD()
    if not api.GetVolumeInformationW(
        volume_path.value,
        None,
        0,
        ctypes.byref(serial_number),
        None,
        None,
        filesystem_name,
        len(filesystem_name),
    ):
        raise _windows_error(f"GetVolumeInformationW failed for {root}")
    if filesystem_name.value.upper() != "NTFS" or int(api.GetDriveTypeW(volume_path.value)) != DRIVE_FIXED:
        raise ValueError("installed root is not on a fixed local NTFS volume")


def normalize_windows_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def windows_error_code(error: OSError) -> int:
    winerror = getattr(error, "winerror", None)
    return int(winerror if winerror is not None else error.errno or 0)


def _seek_start(handle: int, path: Path) -> None:
    if not windows_api().SetFilePointerEx(handle, 0, None, FILE_BEGIN):
        raise _windows_error(f"SetFilePointerEx failed for {path}")


def _read(handle: int, size: int, path: Path) -> bytes:
    from ctypes import wintypes

    buffer = ctypes.create_string_buffer(size)
    read = wintypes.DWORD()
    if not windows_api().ReadFile(handle, buffer, size, ctypes.byref(read), None):
        raise _windows_error(f"ReadFile failed for {path}")
    return buffer.raw[: read.value]


def _windows_error(message: str) -> OSError:
    error = int(getattr(ctypes, "get_last_error")())
    return OSError(error, message)
