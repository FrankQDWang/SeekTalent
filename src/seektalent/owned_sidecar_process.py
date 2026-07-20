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

from seektalent.installed_release import (
    INSTALLED_MANIFEST_RELATIVE_PATH,
    InstalledSidecarExecutableResolution,
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
    _lifecycle_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        with self._lifecycle_lock:
            self._clear_process_group_if_exited_locked()
            return self._process.returncode

    def close_stdin(self) -> None:
        if not self.protocol_writer.closed:
            self.protocol_writer.close()

    def poll(self) -> int | None:
        with self._lifecycle_lock:
            return_code = self._process.poll()
            if return_code is not None:
                self._process_group_id = None
            return return_code

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
        return return_code

    def _clear_process_group_if_exited_locked(self) -> None:
        if self._process.returncode is not None:
            self._process_group_id = None

    def close_protocol_reader(self) -> None:
        if not self.protocol_reader.closed:
            self.protocol_reader.close()

    def close_stderr_reader(self) -> None:
        if not self.stderr_reader.closed:
            self.stderr_reader.close()

    def close_readers(self) -> None:
        self.close_protocol_reader()
        self.close_stderr_reader()


def spawn_owned_sidecar(resolution: InstalledSidecarExecutableResolution) -> OwnedSidecarProcess:
    """Spawn the resolved absolute executable without adding arguments or secrets."""
    if not isinstance(resolution, InstalledSidecarExecutableResolution):
        raise TypeError("resolution must be InstalledSidecarExecutableResolution")
    executable = resolution.executable_path
    if not executable.is_absolute():
        raise ValueError("resolved executable path must be absolute")
    working_directory = _installed_release_working_directory(resolution)

    if os.name == "posix":
        process: subprocess.Popen[bytes] = subprocess.Popen(
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
    elif os.name == "nt":
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
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP"),
        )
    else:
        raise OSError(f"unsupported process platform: {os.name}")

    if process.stdin is None or process.stdout is None or process.stderr is None:
        _reap_failed_spawn(process, process.pid if os.name == "posix" else None)
        raise RuntimeError("subprocess did not create all three stdio pipes")
    return OwnedSidecarProcess(
        _process=process,
        protocol_writer=process.stdin,
        protocol_reader=process.stdout,
        stderr_reader=process.stderr,
        _process_group_id=process.pid if os.name == "posix" else None,
    )


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


def _reap_failed_spawn(process: subprocess.Popen[bytes], process_group_id: int | None) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    _kill_owned_process(process, process_group_id)
    process.wait()
