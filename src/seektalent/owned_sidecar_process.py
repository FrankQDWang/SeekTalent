from __future__ import annotations

import math
import os
import signal
import stat
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from seektalent.installed_release import INSTALLED_MANIFEST_RELATIVE_PATH, InstalledSidecarExecutableResolution
from seektalent.installed_slot import (
    InstalledSidecarLaunchLease,
    InstalledSlotError,
    _InstalledSidecarLeaseState,
)
from seektalent.windows_installed_binding import (
    WindowsLaunchBindingError,
    WindowsLaunchBindingReason,
)


@dataclass(slots=True)
class OwnedSidecarProcess:
    """Main-owned direct child and its three anonymous one-way stdio pipes.

    The exact Popen handle is private: callers must use this wrapper for every
    lifecycle operation so observation, reaping, and retained process-group
    signaling remain serialized. On POSIX, a numeric process-group ID is not a
    durable ownership handle after the direct child is reaped.
    """

    _process: subprocess.Popen[bytes]
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
        self._lease_state = None
        state.close()

    def close_protocol_reader(self) -> None:
        if not self.protocol_reader.closed:
            self.protocol_reader.close()

    def close_stderr_reader(self) -> None:
        if not self.stderr_reader.closed:
            self.stderr_reader.close()

    def close_readers(self) -> None:
        self.close_protocol_reader()
        self.close_stderr_reader()


def spawn_owned_sidecar(lease: InstalledSidecarLaunchLease) -> OwnedSidecarProcess:
    """Spawn one authenticated sidecar while transferring its live slot lease to the child."""
    if not isinstance(lease, InstalledSidecarLaunchLease):
        raise TypeError("lease must be InstalledSidecarLaunchLease")
    lease_state = lease._take_for_spawn()
    process: subprocess.Popen[bytes] | None = None
    try:
        if os.name == "nt":
            raise WindowsLaunchBindingError(
                WindowsLaunchBindingReason.LAUNCH_BINDING_UNSUPPORTED,
                lease_state.admission.executable_path,
            )
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
            raise SidecarSpawnCleanupError(primary_error, owner, cleanup_failures) from primary_error
        for cleanup_error in cleanup_failures:
            primary_error.add_note(
                f"failed spawn cleanup: {type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise


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


def _terminate_owned_process(process: subprocess.Popen[bytes], process_group_id: int | None) -> None:
    if process.returncode is not None:
        return
    if process_group_id is not None:
        _signal_process_group(process_group_id, signal.SIGTERM)
        return
    process.terminate()


def _kill_owned_process(process: subprocess.Popen[bytes], process_group_id: int | None) -> None:
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


class SidecarSpawnCleanupError(RuntimeError):
    """A failed spawn whose direct child needs explicit reaping before slot release."""

    def __init__(
        self,
        primary_error: BaseException,
        owner: _UnreapedFailedSpawn,
        cleanup_failures: tuple[BaseException, ...],
    ) -> None:
        self.primary_error = primary_error
        self._owner = owner
        super().__init__("failed sidecar spawn cleanup could not confirm direct-child reap")
        self.add_note(f"primary failed spawn error: {type(primary_error).__name__}: {primary_error}")
        for cleanup_error in cleanup_failures:
            self.add_note(
                f"failed spawn cleanup: {type(cleanup_error).__name__}: {cleanup_error}"
            )

    @property
    def direct_child_reaped(self) -> bool:
        return self._owner.child_reaped

    @property
    def lease_released(self) -> bool:
        return self._owner.lease_released

    def reap(self) -> bool:
        """Retry the explicit cleanup owner once; release the lease only after reap."""
        cleanup = self._owner.reap()
        for cleanup_error in cleanup.failures:
            self.add_note(
                f"failed spawn cleanup retry: {type(cleanup_error).__name__}: {cleanup_error}"
            )
        return cleanup.reaped


def _reap_failed_spawn(
    process: subprocess.Popen[bytes],
    process_group_id: int | None,
) -> _FailedSpawnCleanup:
    """Close every endpoint, then kill and reap before a failed spawn releases its lease."""
    failures: list[BaseException] = []
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError as exc:
                failures.append(exc)
    try:
        _kill_owned_process(process, process_group_id)
    except OSError as exc:
        failures.append(exc)
    try:
        process.wait()
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
