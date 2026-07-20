from __future__ import annotations

import math
import os
import signal
import subprocess
from dataclasses import dataclass
from typing import IO

from seektalent.installed_release import InstalledSidecarExecutableResolution


@dataclass(slots=True)
class OwnedSidecarProcess:
    """Main-owned direct child and its three anonymous one-way stdio pipes.

    On POSIX, group signaling remains owned only until poll or wait reaps the
    direct child. A numeric process-group ID is not a durable ownership handle
    after that boundary.
    """

    process: subprocess.Popen[bytes]
    protocol_writer: IO[bytes]
    protocol_reader: IO[bytes]
    stderr_reader: IO[bytes]
    _process_group_id: int | None

    def close_stdin(self) -> None:
        if not self.protocol_writer.closed:
            self.protocol_writer.close()

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self, timeout: float) -> int:
        return self.process.wait(timeout=_bounded_timeout(timeout))

    def terminate(self, timeout: float) -> int:
        _terminate_owned_process(self.process, self._process_group_id)
        try:
            return self.wait(timeout)
        except subprocess.TimeoutExpired:
            return self.kill(timeout)

    def kill(self, timeout: float) -> int:
        _kill_owned_process(self.process, self._process_group_id)
        return self.wait(timeout)

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
            env=_bounded_environment(),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP"),
        )
    else:
        raise OSError(f"unsupported process platform: {os.name}")

    if process.stdin is None or process.stdout is None or process.stderr is None:
        _reap_failed_spawn(process, process.pid if os.name == "posix" else None)
        raise RuntimeError("subprocess did not create all three stdio pipes")
    return OwnedSidecarProcess(
        process=process,
        protocol_writer=process.stdin,
        protocol_reader=process.stdout,
        stderr_reader=process.stderr,
        _process_group_id=process.pid if os.name == "posix" else None,
    )


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
