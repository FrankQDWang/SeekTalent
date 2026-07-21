"""Windows opened-object evidence for one installed release slot."""

from __future__ import annotations

import ctypes
import os
import weakref
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Never, SupportsIndex

from seektalent.installed_filesystem import InstalledReleaseError, InstalledReleaseReason
from seektalent.windows_native_files import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    WindowsFileIdentity,
    close_windows_handle,
    hash_windows_file,
    normalize_windows_path,
    open_windows_object,
    query_windows_file_identity,
    read_windows_file,
    require_supported_local_ntfs,
    windows_api,
    windows_error_code,
)


ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33


class WindowsLaunchBindingReason(StrEnum):
    SLOT_FILESYSTEM_UNSUPPORTED = "slot_filesystem_unsupported"
    SLOT_PATH_COMPONENT_CHANGED = "slot_path_component_changed"
    SLOT_REPARSE_POINT_REJECTED = "slot_reparse_point_rejected"
    EXECUTABLE_IDENTITY_CHANGED = "executable_identity_changed"
    EXECUTABLE_SHARE_MODE_CONFLICT = "executable_share_mode_conflict"
    PIPE_SETUP_FAILED = "pipe_setup_failed"
    CREATE_PROCESS_FAILED = "create_process_failed"
    CHILD_IMAGE_UNAVAILABLE = "child_image_unavailable"
    CHILD_IMAGE_MISMATCH = "child_image_mismatch"
    RESUME_THREAD_FAILED = "resume_thread_failed"
    LAUNCH_BINDING_UNSUPPORTED = "launch_binding_unsupported"
    NATIVE_HANDLE_RELEASE_FAILED = "native_handle_release_failed"


class WindowsLaunchBindingError(ValueError):
    def __init__(
        self,
        reason: WindowsLaunchBindingReason,
        path: Path | None = None,
        *,
        winerror: int | None = None,
    ) -> None:
        self.reason = reason
        self.path = path
        self.winerror = winerror
        message = reason.value if winerror is None else f"{reason.value}: winerror={winerror}"
        super().__init__(message)


@dataclass(slots=True)
class _OpenedWindowsObject:
    path: Path
    handle: int
    identity: WindowsFileIdentity


@dataclass(slots=True)
class _WindowsOpenedReleaseState:
    installation_root: Path
    slot_root: Path
    opened: dict[str, _OpenedWindowsObject]
    order: list[str]
    closed: bool = False

    def require_slot_root(self, slot_root: Path) -> None:
        if slot_root != self.slot_root:
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED,
                slot_root,
            )

    def open_directory_chain(self, path: Path) -> None:
        relative = _require_descendant(self.installation_root, path)
        current = self.installation_root
        self._open_object(current, directory=True, executable=False)
        for part in relative.parts:
            current /= part
            self._open_object(current, directory=True, executable=False)

    def read_regular(self, path: Path, limit: int) -> bytes:
        opened = self._open_regular(path, executable=False)
        if opened.identity.size > limit:
            raise InstalledReleaseError(InstalledReleaseReason.FILE_SIZE_LIMIT_EXCEEDED, path)
        before = _query_identity(opened.handle, path, executable=False)
        _require_identity(opened.identity, before, path, executable=False)
        try:
            content = read_windows_file(opened.handle, before.size, path)
        except (OSError, EOFError) as exc:
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED,
                path,
                winerror=_native_error_code(exc),
            ) from exc
        after = _query_identity(opened.handle, path, executable=False)
        _require_identity(before, after, path, executable=False)
        return content

    def inspect_executable(
        self,
        path: Path,
        *,
        expected_size: int,
        expected_sha256: str,
        limit: int,
    ) -> str:
        opened = self._open_regular(path, executable=True)
        before = _query_identity(opened.handle, path, executable=True)
        _require_identity(opened.identity, before, path, executable=True)
        if before.size != expected_size:
            raise InstalledReleaseError(InstalledReleaseReason.FILE_SIZE_MISMATCH, path)
        if before.size > limit:
            raise InstalledReleaseError(InstalledReleaseReason.FILE_SIZE_LIMIT_EXCEEDED, path)
        try:
            actual = hash_windows_file(opened.handle, before.size, path)
        except (OSError, EOFError) as exc:
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.EXECUTABLE_IDENTITY_CHANGED,
                path,
                winerror=_native_error_code(exc),
            ) from exc
        after = _query_identity(opened.handle, path, executable=True)
        _require_identity(before, after, path, executable=True)
        if actual != expected_sha256:
            raise InstalledReleaseError(InstalledReleaseReason.FILE_DIGEST_MISMATCH, path)
        return actual

    def _open_regular(self, path: Path, *, executable: bool) -> _OpenedWindowsObject:
        relative = _require_descendant(self.installation_root, path)
        current = self.installation_root
        self._open_object(current, directory=True, executable=False)
        for part in relative.parts[:-1]:
            current /= part
            self._open_object(current, directory=True, executable=False)
        return self._open_object(path, directory=False, executable=executable)

    def _open_object(
        self,
        path: Path,
        *,
        directory: bool,
        executable: bool,
    ) -> _OpenedWindowsObject:
        key = normalize_windows_path(str(path))
        existing = self.opened.get(key)
        if existing is not None:
            if existing.identity.directory != directory:
                raise WindowsLaunchBindingError(
                    WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED,
                    path,
                )
            return existing
        try:
            native_handle = open_windows_object(path, directory=directory)
        except OSError as exc:
            error = windows_error_code(exc)
            if error == ERROR_ACCESS_DENIED:
                raise InstalledReleaseError(InstalledReleaseReason.FILE_ACCESS_DENIED, path) from exc
            if error in {ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION}:
                reason = (
                    WindowsLaunchBindingReason.EXECUTABLE_SHARE_MODE_CONFLICT
                    if executable
                    else WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED
                )
                raise WindowsLaunchBindingError(reason, path, winerror=error) from exc
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED,
                path,
                winerror=error,
            ) from exc
        try:
            identity = _query_identity(native_handle, path, executable=executable)
            if identity.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise WindowsLaunchBindingError(
                    WindowsLaunchBindingReason.SLOT_REPARSE_POINT_REJECTED,
                    path,
                )
            if identity.directory != directory:
                raise InstalledReleaseError(InstalledReleaseReason.NOT_REGULAR_FILE, path)
            if not directory and identity.link_count != 1:
                raise InstalledReleaseError(InstalledReleaseReason.HARDLINK, path)
            if identity.final_path != key:
                raise WindowsLaunchBindingError(
                    WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED,
                    path,
                )
        except BaseException:
            with suppress(OSError):
                close_windows_handle(native_handle)
            raise
        opened = _OpenedWindowsObject(path=path, handle=native_handle, identity=identity)
        self.opened[key] = opened
        self.order.append(key)
        return opened

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failures: list[OSError] = []
        for key in reversed(self.order):
            opened = self.opened.pop(key)
            try:
                close_windows_handle(opened.handle)
            except OSError as exc:
                failures.append(exc)
        self.order.clear()
        if failures:
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.NATIVE_HANDLE_RELEASE_FAILED,
                self.slot_root,
                winerror=windows_error_code(failures[0]),
            )


_LIVE_BINDINGS: dict[
    int,
    tuple[weakref.ReferenceType["WindowsOpenedInstalledRelease"], _WindowsOpenedReleaseState],
] = {}


class WindowsOpenedInstalledRelease:
    """Factory-only live authority over share-denied Windows installed objects."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("WindowsOpenedInstalledRelease is factory-only")

    def require_slot_root(self, slot_root: Path) -> None:
        _binding_state(self).require_slot_root(slot_root)

    def read_regular(self, path: Path, limit: int) -> bytes:
        return _binding_state(self).read_regular(path, limit)

    def inspect_executable(
        self,
        path: Path,
        *,
        expected_size: int,
        expected_sha256: str,
        limit: int,
    ) -> str:
        return _binding_state(self).inspect_executable(
            path,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            limit=limit,
        )

    def close(self) -> None:
        entry = _LIVE_BINDINGS.pop(id(self), None)
        if entry is not None and entry[0]() is self:
            entry[1].close()

    def __copy__(self) -> Never:
        raise TypeError("WindowsOpenedInstalledRelease cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("WindowsOpenedInstalledRelease cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("WindowsOpenedInstalledRelease cannot be serialized")


def acquire_windows_opened_installed_release(
    installation_root: Path,
    slot_root: Path,
) -> WindowsOpenedInstalledRelease:
    """Acquire complete installed-root-to-slot Windows handle evidence."""
    if os.name != "nt":
        raise WindowsLaunchBindingError(WindowsLaunchBindingReason.LAUNCH_BINDING_UNSUPPORTED)
    root = _require_absolute_root(installation_root)
    slot = _require_absolute_root(slot_root)
    _require_descendant(root, slot)
    try:
        require_supported_local_ntfs(root)
    except (OSError, ValueError) as exc:
        raise WindowsLaunchBindingError(
            WindowsLaunchBindingReason.SLOT_FILESYSTEM_UNSUPPORTED,
            root,
            winerror=_native_error_code(exc),
        ) from exc
    state = _WindowsOpenedReleaseState(root, slot, {}, [])
    try:
        state.open_directory_chain(slot)
    except BaseException as primary:
        try:
            state.close()
        except WindowsLaunchBindingError as cleanup:
            primary.add_note(f"Windows opened-object cleanup failed: {cleanup.reason.value}")
        raise
    binding = object.__new__(WindowsOpenedInstalledRelease)
    binding_id = id(binding)

    def close_if_unclaimed(reference: weakref.ReferenceType[WindowsOpenedInstalledRelease]) -> None:
        entry = _LIVE_BINDINGS.get(binding_id)
        if entry is not None and entry[0] is reference:
            _LIVE_BINDINGS.pop(binding_id, None)
            with suppress(WindowsLaunchBindingError):
                entry[1].close()

    reference = weakref.ref(binding, close_if_unclaimed)
    _LIVE_BINDINGS[binding_id] = (reference, state)
    return binding


def _binding_state(binding: WindowsOpenedInstalledRelease) -> _WindowsOpenedReleaseState:
    entry = _LIVE_BINDINGS.get(id(binding))
    if entry is None or entry[0]() is not binding:
        raise TypeError("Windows opened installed release must be a live factory authority")
    return entry[1]


def _verify_suspended_child_image(
    binding: WindowsOpenedInstalledRelease,
    process_handle: int,
    executable_path: Path,
) -> None:
    """Compare a suspended child's actual image with the held executable object."""
    state = _binding_state(binding)
    expected = state.opened.get(normalize_windows_path(str(executable_path)))
    if expected is None or expected.identity.directory:
        raise TypeError("Windows opened release has no held sidecar executable")

    try:
        image_path = _query_full_process_image_name(process_handle)
    except OSError as exc:
        raise WindowsLaunchBindingError(
            WindowsLaunchBindingReason.CHILD_IMAGE_UNAVAILABLE,
            executable_path,
            winerror=windows_error_code(exc),
        ) from exc
    if normalize_windows_path(image_path) != expected.identity.final_path:
        raise WindowsLaunchBindingError(
            WindowsLaunchBindingReason.CHILD_IMAGE_MISMATCH,
            executable_path,
        )

    child_image = Path(image_path)
    child_handle: int | None = None
    primary: WindowsLaunchBindingError | None = None
    try:
        child_handle = open_windows_object(child_image, directory=False)
        actual = query_windows_file_identity(child_handle, child_image)
        if (
            actual.volume_serial_number != expected.identity.volume_serial_number
            or actual.file_id != expected.identity.file_id
            or actual.final_path != expected.identity.final_path
        ):
            primary = WindowsLaunchBindingError(
                WindowsLaunchBindingReason.CHILD_IMAGE_MISMATCH,
                executable_path,
            )
    except OSError as exc:
        primary = WindowsLaunchBindingError(
            WindowsLaunchBindingReason.CHILD_IMAGE_UNAVAILABLE,
            executable_path,
            winerror=windows_error_code(exc),
        )
    finally:
        if child_handle is not None:
            try:
                close_windows_handle(child_handle)
            except OSError as exc:
                close_error = WindowsLaunchBindingError(
                    WindowsLaunchBindingReason.NATIVE_HANDLE_RELEASE_FAILED,
                    executable_path,
                    winerror=windows_error_code(exc),
                )
                if primary is None:
                    primary = close_error
                else:
                    primary.add_note(f"child image handle close failed: {close_error.winerror}")
    if primary is not None:
        raise primary


def _query_full_process_image_name(process_handle: int) -> str:
    from ctypes import wintypes

    api = windows_api()
    size = 32_768
    for _ in range(4):
        buffer = ctypes.create_unicode_buffer(size)
        length = wintypes.DWORD(size)
        if api.QueryFullProcessImageNameW(process_handle, 0, buffer, ctypes.byref(length)):
            return buffer.value
        if int(getattr(ctypes, "get_last_error")()) != 122:
            break
        size *= 2
    raise OSError(int(getattr(ctypes, "get_last_error")()), "QueryFullProcessImageNameW failed")


def _require_absolute_root(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise WindowsLaunchBindingError(WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED)
    return path


def _require_descendant(root: Path, path: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise WindowsLaunchBindingError(
            WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED,
            path,
        ) from exc
    if relative.is_absolute() or ".." in relative.parts:
        raise WindowsLaunchBindingError(
            WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED,
            path,
        )
    return relative


def _require_identity(
    expected: WindowsFileIdentity,
    actual: WindowsFileIdentity,
    path: Path,
    *,
    executable: bool,
) -> None:
    if expected != actual:
        reason = (
            WindowsLaunchBindingReason.EXECUTABLE_IDENTITY_CHANGED
            if executable
            else WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED
        )
        raise WindowsLaunchBindingError(reason, path)


def _query_identity(
    handle: int,
    path: Path,
    *,
    executable: bool,
) -> WindowsFileIdentity:
    try:
        return query_windows_file_identity(handle, path)
    except OSError as exc:
        reason = (
            WindowsLaunchBindingReason.EXECUTABLE_IDENTITY_CHANGED
            if executable
            else WindowsLaunchBindingReason.SLOT_PATH_COMPONENT_CHANGED
        )
        raise WindowsLaunchBindingError(
            reason,
            path,
            winerror=windows_error_code(exc),
        ) from exc


def _native_error_code(error: OSError | EOFError | ValueError) -> int | None:
    return windows_error_code(error) if isinstance(error, OSError) else None
