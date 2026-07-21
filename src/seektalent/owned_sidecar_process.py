from __future__ import annotations

import math
import os
import signal
import stat
import subprocess
import threading
import weakref
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from pathlib import Path
from typing import IO, Never, Protocol, SupportsIndex

from seektalent.installed_release import INSTALLED_MANIFEST_RELATIVE_PATH, InstalledSidecarExecutableResolution
from seektalent.installed_slot import (
    InstalledSidecarLaunchLease,
    InstalledSlotError,
    _InstalledSidecarLeaseState,
)
from seektalent.windows_installed_binding import (
    WindowsLaunchBindingError,
    WindowsLaunchBindingReason,
    WindowsOpenedInstalledRelease,
    _verify_suspended_child_image,
)
from seektalent.windows_sidecar_process import (
    FAILED_SPAWN_REAP_TIMEOUT_SECONDS,
    _WindowsPendingCreationError,
    _WindowsPendingSidecar,
    _create_windows_suspended_sidecar,
)


FAILED_SPAWN_CLEANUP_MAX_NATIVE_ATTEMPTS = 4

__all__ = [
    "OwnedSidecarProcess",
    "SidecarCleanupMaintenanceResult",
    "SidecarSpawnCleanupError",
    "maintain_abandoned_sidecar_spawns",
    "spawn_owned_sidecar",
]


class _OwnedChildProcess(Protocol):
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(slots=True, eq=False)
class _WindowsPendingOwner:
    """Private authority over the suspended child, held release, and lifecycle lease."""

    pending: _WindowsPendingSidecar
    lease_state: _InstalledSidecarLeaseState | None
    opened_release: WindowsOpenedInstalledRelease | None

    def __copy__(self) -> Never:
        raise TypeError("private Windows pending owner cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("private Windows pending owner cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("private Windows pending owner cannot be serialized")

    def verify_child_image(self, executable: Path) -> None:
        if self.opened_release is None or self.pending.child is None or self.pending.child._process_handle is None:
            raise WindowsLaunchBindingError(WindowsLaunchBindingReason.CHILD_IMAGE_UNAVAILABLE, executable)
        _verify_suspended_child_image(self.opened_release, self.pending.child._process_handle, executable)

    def transfer_lease_after_promotion(self) -> _InstalledSidecarLeaseState:
        state = self.lease_state
        if state is None:
            raise TypeError("private Windows pending owner has already transferred its lease")
        self.pending.mark_promoted()
        self.lease_state = None
        self.opened_release = None
        return state


@dataclass(slots=True, eq=False)
class OwnedSidecarProcess:
    """Main-owned direct child and its three anonymous one-way stdio pipes.

    The exact child handle is private: callers must use this wrapper for every
    lifecycle operation so observation, reaping, and retained process-group
    signaling remain serialized. On POSIX, a numeric process-group ID is not a
    durable ownership handle after the direct child is reaped.
    """

    _process: _OwnedChildProcess
    protocol_writer: IO[bytes]
    protocol_reader: IO[bytes]
    stderr_reader: IO[bytes]
    _process_group_id: int | None
    _lease_state: _InstalledSidecarLeaseState | None = field(default=None, repr=False)
    _lifecycle_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        with self._lifecycle_lock:
            return self._process.returncode

    def close_stdin(self) -> None:
        if not self.protocol_writer.closed:
            self.protocol_writer.close()

    def poll(self) -> int | None:
        with self._lifecycle_lock:
            return self._poll_locked()

    def wait(self, timeout: float) -> int:
        normalized_timeout = _bounded_timeout(timeout)
        with self._lifecycle_lock:
            return self._wait_locked(normalized_timeout)

    def terminate(self, timeout: float) -> int:
        normalized_timeout = _bounded_timeout(timeout)
        with self._lifecycle_lock:
            self._clear_process_group_if_exited_locked()
            _terminate_owned_process(self._process, self._process_group_id)
            try:
                return self._wait_locked(normalized_timeout)
            except subprocess.TimeoutExpired:
                _kill_owned_process(self._process, self._process_group_id)
                return self._wait_locked(normalized_timeout)

    def kill(self, timeout: float) -> int:
        normalized_timeout = _bounded_timeout(timeout)
        with self._lifecycle_lock:
            self._clear_process_group_if_exited_locked()
            _kill_owned_process(self._process, self._process_group_id)
            return self._wait_locked(normalized_timeout)

    def _wait_locked(self, timeout: float) -> int:
        return_code = self._process.wait(timeout=timeout)
        self._process_group_id = None
        self._release_lease_locked()
        return return_code

    def _clear_process_group_if_exited_locked(self) -> None:
        if self._process.returncode is not None:
            self._process_group_id = None
            self._release_lease_locked()

    def _poll_locked(self) -> int | None:
        return_code = self._process.poll()
        if return_code is not None:
            self._process_group_id = None
            self._release_lease_locked()
        return return_code

    def _release_lease_locked(self) -> None:
        state = self._lease_state
        if state is None:
            return
        state.close()
        if not state.released:
            raise AssertionError("successful lease close did not confirm native resource disposition")
        self._lease_state = None

    def _retain_reaped_cleanup(self, primary_error: BaseException) -> "SidecarSpawnCleanupError":
        """Move a reaped child with an unreleased lease into its one retry capability."""
        with self._lifecycle_lock:
            if self._process.returncode is None:
                raise AssertionError("reaped cleanup retention requires a confirmed child exit")
            lease_state = self._lease_state
            if lease_state is None:
                raise AssertionError("reaped cleanup retention lost its lifecycle lease")
            self._lease_state = None
            owner = _UnreapedOwnedSidecar(
                self._process,
                None,
                (self.protocol_writer, self.protocol_reader, self.stderr_reader),
                lease_state,
                child_reaped=True,
            )
            return _new_sidecar_spawn_cleanup_error(primary_error, owner, ())

    def close_protocol_reader(self) -> None:
        if not self.protocol_reader.closed:
            self.protocol_reader.close()

    def close_stderr_reader(self) -> None:
        if not self.stderr_reader.closed:
            self.stderr_reader.close()

    def close_readers(self) -> None:
        self.close_protocol_reader()
        self.close_stderr_reader()

    def _cleanup_after_handshake_failure(
        self,
        primary_error: BaseException,
    ) -> SidecarSpawnCleanupError | None:
        """Retain exact child cleanup authority when readiness cannot confirm a reap."""
        with self._lifecycle_lock:
            streams = (self.protocol_writer, self.protocol_reader, self.stderr_reader)
            process_group_id = self._process_group_id
            cleanup = _reap_owned_child(self._process, process_group_id, streams)
            cleanup_failures = cleanup.failures
            lease_state = self._lease_state
            if cleanup.reaped:
                self._process_group_id = None
            if cleanup.reaped and lease_state is not None:
                try:
                    lease_state.close()
                except (InstalledSlotError, WindowsLaunchBindingError) as cleanup_error:
                    cleanup_failures += (cleanup_error,)
                else:
                    self._lease_state = None
                    return None
            elif cleanup.reaped:
                return None

            if lease_state is None:
                raise AssertionError("unreaped readiness cleanup lost its lifecycle lease")
            self._lease_state = None
            owner = _UnreapedOwnedSidecar(
                self._process,
                None if cleanup.reaped else process_group_id,
                streams,
                lease_state,
                child_reaped=cleanup.reaped,
            )
            return _new_sidecar_spawn_cleanup_error(primary_error, owner, cleanup_failures)

    def __copy__(self) -> Never:
        raise TypeError("OwnedSidecarProcess cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("OwnedSidecarProcess cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("OwnedSidecarProcess cannot be serialized")


def spawn_owned_sidecar(lease: InstalledSidecarLaunchLease) -> OwnedSidecarProcess:
    """Spawn one admitted direct child while transferring its live slot lease."""
    if not isinstance(lease, InstalledSidecarLaunchLease):
        raise TypeError("lease must be InstalledSidecarLaunchLease")
    lease_state = lease._take_for_spawn()
    if os.name == "nt":
        return _spawn_windows_owned_sidecar(lease_state)
    process: subprocess.Popen[bytes] | None = None
    try:
        resolution = lease_state.admission.resolution
        executable = resolution.executable_path
        if not executable.is_absolute():
            raise ValueError("resolved executable path must be absolute")
        working_directory = _installed_release_working_directory(resolution)

        if os.name == "posix":
            process = subprocess.Popen(
                [str(executable)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                text=False,
                bufsize=0,
                close_fds=True,
                cwd=str(working_directory),
                env=_bounded_environment(),
                pass_fds=(),
                start_new_session=True,
            )
        else:
            raise OSError(f"unsupported process platform: {os.name}")

        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("subprocess did not create all three stdio pipes")
        return OwnedSidecarProcess(
            _process=process,
            protocol_writer=process.stdin,
            protocol_reader=process.stdout,
            stderr_reader=process.stderr,
            _process_group_id=process.pid if os.name == "posix" else None,
            _lease_state=lease_state,
        )
    except BaseException as primary_error:
        cleanup_failures: tuple[BaseException, ...] = ()
        reaped = process is None
        if process is not None:
            cleanup = _reap_failed_spawn(process, process.pid if os.name == "posix" else None)
            cleanup_failures = cleanup.failures
            reaped = cleanup.reaped
        if reaped:
            try:
                lease_state.close()
            except InstalledSlotError as cleanup_error:
                cleanup_failures += (cleanup_error,)
        else:
            if process is None:
                raise AssertionError("unreaped failed spawn requires a direct child")
            owner = _UnreapedFailedSpawn(process, process.pid if os.name == "posix" else None, lease_state)
            raise _new_sidecar_spawn_cleanup_error(primary_error, owner, cleanup_failures) from primary_error
        for cleanup_error in cleanup_failures:
            primary_error.add_note(
                f"failed spawn cleanup: {type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise


def _spawn_windows_owned_sidecar(
    lease_state: _InstalledSidecarLeaseState,
) -> OwnedSidecarProcess:
    resolution = lease_state.admission.resolution
    executable = resolution.executable_path
    owner: _WindowsPendingOwner | None = None
    cleanup_failures: tuple[BaseException, ...] = ()
    try:
        if not executable.is_absolute():
            raise ValueError("resolved executable path must be absolute")
        opened_release = lease_state.windows_opened_release
        if opened_release is None:
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.LAUNCH_BINDING_UNSUPPORTED,
                executable,
            )
        pending = _create_windows_suspended_sidecar(
            executable,
            _installed_release_working_directory(resolution),
        )
        owner = _WindowsPendingOwner(pending, lease_state, opened_release)
        owner.verify_child_image(executable)
        owner.pending.resume(executable)
        child, protocol_writer, protocol_reader, stderr_reader = owner.pending.resources()
        process = OwnedSidecarProcess(
            _process=child,
            protocol_writer=protocol_writer,
            protocol_reader=protocol_reader,
            stderr_reader=stderr_reader,
            _process_group_id=None,
            _lease_state=lease_state,
        )
        owner.transfer_lease_after_promotion()
        return process
    except _WindowsPendingCreationError as creation_error:
        primary_error = creation_error.primary_error
        owner = _WindowsPendingOwner(creation_error.pending, lease_state, opened_release)
        cleanup_failures = creation_error.failures
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        primary_error = error

    if owner is None:
        try:
            lease_state.close()
        except (InstalledSlotError, WindowsLaunchBindingError) as cleanup_error:
            cleanup_failures += (cleanup_error,)
        for cleanup_error in cleanup_failures:
            primary_error.add_note(
                f"failed Windows spawn cleanup: {type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise primary_error

    cleanup = owner.pending.cleanup()
    cleanup_failures += cleanup.failures
    if cleanup.child_reaped and cleanup.handles_closed:
        try:
            lease_state.close()
        except (InstalledSlotError, WindowsLaunchBindingError) as cleanup_error:
            cleanup_failures += (cleanup_error,)
        for cleanup_error in cleanup_failures:
            primary_error.add_note(
                f"failed Windows spawn cleanup: {type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise primary_error

    cleanup_owner = _UnreapedWindowsFailedSpawn(owner, cleanup.child_reaped)
    raise _new_sidecar_spawn_cleanup_error(primary_error, cleanup_owner, cleanup_failures) from primary_error


def _installed_release_working_directory(
    resolution: InstalledSidecarExecutableResolution,
) -> Path:
    expected_manifest = resolution.slot_root / INSTALLED_MANIFEST_RELATIVE_PATH
    if (
        not resolution.slot_root.is_absolute()
        or not resolution.manifest_path.is_absolute()
        or resolution.manifest_path != expected_manifest
    ):
        raise ValueError("resolved manifest must be the fixed absolute installed manifest")
    working_directory = resolution.manifest_path.parent
    try:
        value = os.lstat(working_directory)
    except OSError as exc:
        raise ValueError("installed release working directory must exist") from exc
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise ValueError("installed release working directory must be a real directory")
    return working_directory


def _bounded_environment() -> dict[str, str]:
    if os.name != "nt":
        return {}
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise OSError("SystemRoot is required to spawn the Windows sidecar")
    return {"SystemRoot": system_root}


def _bounded_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a finite positive number")
    value = float(timeout)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a finite positive number")
    return value


def _terminate_owned_process(process: _OwnedChildProcess, process_group_id: int | None) -> None:
    if process.returncode is not None:
        return
    if process_group_id is not None:
        _signal_process_group(process_group_id, signal.SIGTERM)
        return
    process.terminate()


def _kill_owned_process(process: _OwnedChildProcess, process_group_id: int | None) -> None:
    if process.returncode is not None:
        return
    if process_group_id is not None:
        _signal_process_group(process_group_id, signal.SIGKILL)
        return
    process.kill()


def _signal_process_group(process_group_id: int, sig: signal.Signals) -> None:
    try:
        os.killpg(process_group_id, sig)
    except ProcessLookupError:
        return


@dataclass(frozen=True, slots=True)
class _FailedSpawnCleanup:
    failures: tuple[BaseException, ...]
    reaped: bool


@dataclass(slots=True)
class _UnreapedFailedSpawn:
    process: subprocess.Popen[bytes]
    process_group_id: int | None
    lease_state: _InstalledSidecarLeaseState
    child_reaped: bool = False
    lease_released: bool = False

    def reap(self) -> _FailedSpawnCleanup:
        cleanup = _reap_failed_spawn(self.process, self.process_group_id)
        if cleanup.reaped:
            self.child_reaped = True
        if self.child_reaped and not self.lease_released:
            try:
                self.lease_state.close()
            finally:
                self.lease_released = self.lease_state.released
        return cleanup


@dataclass(slots=True)
class _UnreapedOwnedSidecar:
    """The original child and lease retained after a failed readiness handshake."""

    process: _OwnedChildProcess
    process_group_id: int | None
    streams: tuple[IO[bytes], IO[bytes], IO[bytes]]
    lease_state: _InstalledSidecarLeaseState
    child_reaped: bool = False
    lease_released: bool = False

    def reap(self) -> _FailedSpawnCleanup:
        cleanup = _reap_owned_child(self.process, self.process_group_id, self.streams)
        if cleanup.reaped:
            self.child_reaped = True
        if self.child_reaped and not self.lease_released:
            try:
                self.lease_state.close()
            finally:
                self.lease_released = self.lease_state.released
        return cleanup


@dataclass(slots=True)
class _UnreapedWindowsFailedSpawn:
    owner: _WindowsPendingOwner
    child_reaped: bool = False
    lease_released: bool = False

    def reap(self) -> _FailedSpawnCleanup:
        cleanup = self.owner.pending.cleanup()
        self.child_reaped = cleanup.child_reaped
        if cleanup.child_reaped and cleanup.handles_closed and not self.lease_released:
            lease_state = self.owner.lease_state
            if lease_state is None:
                raise AssertionError("private Windows pending owner lost its lifecycle lease")
            try:
                lease_state.close()
            finally:
                self.lease_released = lease_state.released
        return _FailedSpawnCleanup(cleanup.failures, cleanup.child_reaped)


class _CleanupRetryState(Enum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    TERMINAL_FAILURE = "terminal_failure"


@dataclass(slots=True)
class _FailedSpawnCleanupCapability:
    owner: _UnreapedFailedSpawn | _UnreapedOwnedSidecar | _UnreapedWindowsFailedSpawn
    native_attempts: int = 1
    state: _CleanupRetryState = _CleanupRetryState.ACTIVE
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reap(self) -> tuple[bool, tuple[BaseException, ...]]:
        with self.lock:
            if self.state is _CleanupRetryState.SUCCEEDED:
                return True, ()
            if self.state is _CleanupRetryState.TERMINAL_FAILURE:
                return False, ()
            if self.native_attempts >= FAILED_SPAWN_CLEANUP_MAX_NATIVE_ATTEMPTS:
                self.state = _CleanupRetryState.TERMINAL_FAILURE
                return False, ()
            self.native_attempts += 1
            try:
                cleanup = self.owner.reap()
            except (InstalledSlotError, WindowsLaunchBindingError):
                if self.native_attempts >= FAILED_SPAWN_CLEANUP_MAX_NATIVE_ATTEMPTS:
                    self.state = _CleanupRetryState.TERMINAL_FAILURE
                raise
            if cleanup.reaped and self.owner.lease_released:
                self.state = _CleanupRetryState.SUCCEEDED
                return True, cleanup.failures
            if self.native_attempts >= FAILED_SPAWN_CLEANUP_MAX_NATIVE_ATTEMPTS:
                self.state = _CleanupRetryState.TERMINAL_FAILURE
            return False, cleanup.failures


@dataclass(slots=True)
class _SpawnCleanupOwner:
    error_reference: weakref.ReferenceType["SidecarSpawnCleanupError"]
    capability: _FailedSpawnCleanupCapability
    abandoned: bool = False


@dataclass(frozen=True, slots=True)
class SidecarCleanupMaintenanceResult:
    """The bounded disposition of abandoned failed-spawn cleanup owners."""

    abandoned: int
    reaped: int
    terminal: int


_SPAWN_CLEANUP_OWNERS: dict[int, _SpawnCleanupOwner] = {}
_SPAWN_CLEANUP_OWNERS_LOCK = threading.Lock()
_SPAWN_CLEANUP_OWNER_IDS = count(1)


def _mark_spawn_cleanup_abandoned(owner_id: int) -> None:
    with _SPAWN_CLEANUP_OWNERS_LOCK:
        owner = _SPAWN_CLEANUP_OWNERS.get(owner_id)
        if owner is not None and owner.error_reference() is None:
            owner.abandoned = True


def maintain_abandoned_sidecar_spawns() -> SidecarCleanupMaintenanceResult:
    """Run one bounded cleanup attempt for every unreachable failed-spawn owner."""
    with _SPAWN_CLEANUP_OWNERS_LOCK:
        abandoned = tuple(
            (owner_id, owner)
            for owner_id, owner in _SPAWN_CLEANUP_OWNERS.items()
            if owner.abandoned
        )

    reaped = 0
    terminal = 0
    for owner_id, owner in abandoned:
        confirmed, _ = owner.capability.reap()
        if confirmed:
            with _SPAWN_CLEANUP_OWNERS_LOCK:
                if _SPAWN_CLEANUP_OWNERS.get(owner_id) is owner:
                    _SPAWN_CLEANUP_OWNERS.pop(owner_id)
                    reaped += 1
        elif owner.capability.state is _CleanupRetryState.TERMINAL_FAILURE:
            terminal += 1
    return SidecarCleanupMaintenanceResult(len(abandoned), reaped, terminal)


class SidecarSpawnCleanupError(RuntimeError):
    """A factory-issued failed spawn cleanup capability with bounded retries."""

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("SidecarSpawnCleanupError is factory-only")

    @property
    def direct_child_reaped(self) -> bool:
        completed = getattr(self, "_completed_cleanup_result", None)
        if completed is not None:
            return completed[0]
        return _spawn_cleanup_capability(self).owner.child_reaped

    @property
    def lease_released(self) -> bool:
        completed = getattr(self, "_completed_cleanup_result", None)
        if completed is not None:
            return completed[1]
        return _spawn_cleanup_capability(self).owner.lease_released

    @property
    def cleanup_terminally_failed(self) -> bool:
        if getattr(self, "_completed_cleanup_result", None) is not None:
            return False
        return _spawn_cleanup_capability(self).state is _CleanupRetryState.TERMINAL_FAILURE

    def reap(self) -> bool:
        """Run one bounded cleanup retry, then cache a confirmed successful result."""
        if getattr(self, "_completed_cleanup_result", None) is not None:
            return True
        capability = _spawn_cleanup_capability(self)
        reaped, failures = capability.reap()
        for cleanup_error in failures:
            self.add_note(
                f"failed spawn cleanup retry: {type(cleanup_error).__name__}: {cleanup_error}"
            )
        if reaped:
            self._completed_cleanup_result = (
                capability.owner.child_reaped,
                capability.owner.lease_released,
            )
            with _SPAWN_CLEANUP_OWNERS_LOCK:
                _SPAWN_CLEANUP_OWNERS.pop(_spawn_cleanup_owner_id(self), None)
        return reaped

    def abandon(self) -> None:
        """Transfer an unreachable cleanup capability to bounded maintenance."""
        owner_id = _spawn_cleanup_owner_id(self)
        with _SPAWN_CLEANUP_OWNERS_LOCK:
            owner = _SPAWN_CLEANUP_OWNERS.get(owner_id)
            if owner is None or owner.error_reference() is not self:
                raise TypeError("SidecarSpawnCleanupError must be a live factory cleanup error")
            owner.abandoned = True

    def __copy__(self) -> Never:
        raise TypeError("SidecarSpawnCleanupError cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("SidecarSpawnCleanupError cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("SidecarSpawnCleanupError cannot be serialized")


def _new_sidecar_spawn_cleanup_error(
    primary_error: BaseException,
    owner: _UnreapedFailedSpawn | _UnreapedOwnedSidecar | _UnreapedWindowsFailedSpawn,
    cleanup_failures: tuple[BaseException, ...],
) -> SidecarSpawnCleanupError:
    error = RuntimeError.__new__(SidecarSpawnCleanupError)
    RuntimeError.__init__(error, "failed sidecar spawn cleanup could not confirm direct-child reap")
    owner_id = next(_SPAWN_CLEANUP_OWNER_IDS)
    error._spawn_cleanup_owner_id = owner_id
    error._completed_cleanup_result: tuple[bool, bool] | None = None
    error.primary_error = primary_error
    error.add_note(f"primary failed spawn error: {type(primary_error).__name__}: {primary_error}")
    for cleanup_error in cleanup_failures:
        error.add_note(
            f"failed spawn cleanup: {type(cleanup_error).__name__}: {cleanup_error}"
        )
    with _SPAWN_CLEANUP_OWNERS_LOCK:
        _SPAWN_CLEANUP_OWNERS[owner_id] = _SpawnCleanupOwner(
            weakref.ref(error),
            _FailedSpawnCleanupCapability(owner),
        )
    weakref.finalize(error, _mark_spawn_cleanup_abandoned, owner_id)
    return error


def _spawn_cleanup_capability(error: SidecarSpawnCleanupError) -> _FailedSpawnCleanupCapability:
    owner_id = _spawn_cleanup_owner_id(error)

    with _SPAWN_CLEANUP_OWNERS_LOCK:
        owner = _SPAWN_CLEANUP_OWNERS.get(owner_id)
        if owner is None or owner.error_reference() is not error:
            raise TypeError("SidecarSpawnCleanupError must be a live factory cleanup error")
        return owner.capability


def _spawn_cleanup_owner_id(error: SidecarSpawnCleanupError) -> int:
    owner_id = getattr(error, "_spawn_cleanup_owner_id", None)
    if not isinstance(owner_id, int):
        raise TypeError("SidecarSpawnCleanupError must be a live factory cleanup error")
    return owner_id


def _reap_failed_spawn(
    process: subprocess.Popen[bytes],
    process_group_id: int | None,
) -> _FailedSpawnCleanup:
    """Close every endpoint, then kill and reap before a failed spawn releases its lease."""
    return _reap_owned_child(
        process,
        process_group_id,
        tuple(stream for stream in (process.stdin, process.stdout, process.stderr) if stream is not None),
    )


def _reap_owned_child(
    process: _OwnedChildProcess,
    process_group_id: int | None,
    streams: tuple[IO[bytes], ...],
) -> _FailedSpawnCleanup:
    """Close every endpoint, then kill and reap an already-owned direct child."""
    failures: list[BaseException] = []
    for stream in streams:
        try:
            stream.close()
        except (OSError, ValueError) as exc:
            failures.append(exc)
    try:
        _kill_owned_process(process, process_group_id)
    except OSError as exc:
        failures.append(exc)
    try:
        process.wait(timeout=FAILED_SPAWN_REAP_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        failures.append(exc)
        try:
            reaped = process.poll() is not None
        except (OSError, subprocess.SubprocessError) as poll_error:
            failures.append(poll_error)
            reaped = False
    else:
        reaped = True
    return _FailedSpawnCleanup(tuple(failures), reaped)
