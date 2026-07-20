from __future__ import annotations

import base64
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

if os.name == "posix":
    import fcntl
else:
    fcntl = None

import seektalent.installed_release as installed_release
import seektalent.owned_sidecar_process as owned_process
from seektalent.installed_release import InstalledReleaseError, InstalledSidecarExecutableResolution
from seektalent.owned_sidecar_process import spawn_owned_sidecar
from seektalent.release_manifest import TargetV1


PROBE = Path(__file__).parent / "support" / "owned_sidecar_probe.py"
pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows native executable/handle matrix is explicitly unproved in issue #358",
)


@pytest.fixture
def resolution(tmp_path: Path) -> InstalledSidecarExecutableResolution:
    executable = tmp_path / "installed sidecar probe"
    executable.write_text(f"#!{sys.executable}\nexec(open({str(PROBE)!r}, 'rb').read())\n", encoding="utf-8")
    executable.chmod(0o500)
    return InstalledSidecarExecutableResolution(
        slot_root=tmp_path,
        manifest_path=tmp_path / "release" / "release-manifest.json",
        executable_path=executable,
        manifest_id="test-manifest",
        manifest_sha256="0" * 64,
        product_build_id="st1-" + "0" * 32,
        target=TargetV1(
            os="macos",
            arch="arm64",
            min_os_build="13.0",
            max_os_build="15.9",
        ),
        executable_size_bytes=executable.stat().st_size,
        executable_sha256="1" * 64,
    )


def _close_process_streams(process: owned_process.OwnedSidecarProcess) -> None:
    process.close_stdin()
    process.close_readers()


def test_spawn_requires_typed_resolution_before_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(TypeError):
        spawn_owned_sidecar(Path("/tmp/not-a-resolution"))  # type: ignore[arg-type]

    assert calls == []


def test_binary_roundtrip_and_stderr_remain_separate(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    process = spawn_owned_sidecar(resolution)
    stdout_value = b"\x00protocol\xff"
    stderr_value = b"diagnostic-only\n"
    process.protocol_writer.write(b"ECHO " + base64.b64encode(stdout_value) + b"\n")
    process.protocol_writer.write(b"STDERR " + base64.b64encode(stderr_value) + b"\n")
    process.close_stdin()

    stdout = process.protocol_reader.read()
    stderr = process.stderr_reader.read()

    assert process.wait(5) == 0
    assert stdout == stdout_value + b"EOF\n"
    assert stderr == stderr_value
    process.close_readers()


def test_concurrent_drain_handles_stdout_and_stderr_above_pipe_capacity(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    process = spawn_owned_sidecar(resolution)
    size = 2 * 1024 * 1024
    process.protocol_writer.write(f"FLOOD {size}\n".encode())
    process.close_stdin()
    output: dict[str, bytes] = {}

    stdout_thread = threading.Thread(
        target=lambda: output.__setitem__("stdout", process.protocol_reader.read()),
    )
    stderr_thread = threading.Thread(
        target=lambda: output.__setitem__("stderr", process.stderr_reader.read()),
    )
    stdout_thread.start()
    stderr_thread.start()
    stdout_thread.join(timeout=10)
    stderr_thread.join(timeout=10)

    assert not stdout_thread.is_alive()
    assert not stderr_thread.is_alive()
    assert process.wait(5) == 0
    assert output["stdout"] == b"O" * size + b"EOF\n"
    assert output["stderr"] == b"E" * size
    process.close_readers()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor inheritance")
def test_inheritable_sentinel_fd_does_not_enter_child(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    assert fcntl is not None
    source_fd = os.open(PROBE, os.O_RDONLY)
    sentinel_fd = fcntl.fcntl(source_fd, fcntl.F_DUPFD, 200)
    os.close(source_fd)
    os.set_inheritable(sentinel_fd, True)
    try:
        process = spawn_owned_sidecar(resolution)
        process.protocol_writer.write(f"FD {sentinel_fd}\n".encode())
        process.close_stdin()
        assert process.protocol_reader.readline() == b"CLOSED\n"
        assert process.wait(5) == 0
        process.close_readers()
    finally:
        os.close(sentinel_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX CLOEXEC inspection")
def test_parent_pipe_endpoints_are_non_inheritable(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    process = spawn_owned_sidecar(resolution)

    assert not os.get_inheritable(process.protocol_writer.fileno())
    assert not os.get_inheritable(process.protocol_reader.fileno())
    assert not os.get_inheritable(process.stderr_reader.fileno())

    process.close_stdin()
    assert process.wait(5) == 0
    process.close_readers()


def test_close_stdin_delivers_eof_and_child_exit_closes_readers(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    process = spawn_owned_sidecar(resolution)
    process.close_stdin()

    assert process.protocol_reader.read() == b"EOF\n"
    assert process.stderr_reader.read() == b""
    assert process.wait(5) == 0
    assert process.protocol_reader.read() == b""
    assert process.stderr_reader.read() == b""
    process.close_readers()


def test_clean_exit_waits_and_reaps_direct_child(resolution: InstalledSidecarExecutableResolution) -> None:
    process = spawn_owned_sidecar(resolution)
    process.protocol_writer.write(b"EXIT 7\n")
    process.protocol_writer.flush()

    assert process.wait(5) == 7
    assert process.poll() == 7
    with pytest.raises(ChildProcessError):
        os.waitpid(process.process.pid, os.WNOHANG)
    _close_process_streams(process)


@pytest.mark.parametrize("method", ["terminate", "kill"])
def test_terminate_and_kill_wait_and_reap(
    resolution: InstalledSidecarExecutableResolution,
    method: str,
) -> None:
    process = spawn_owned_sidecar(resolution)
    return_code = getattr(process, method)(5)

    assert return_code < 0
    assert process.poll() == return_code
    with pytest.raises(ChildProcessError):
        os.waitpid(process.process.pid, os.WNOHANG)
    _close_process_streams(process)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-session ownership")
def test_spawned_process_is_direct_child_and_process_group_leader(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    process = spawn_owned_sidecar(resolution)
    process.protocol_writer.write(b"IDENTITY\n")
    process.protocol_writer.flush()
    identity = json.loads(process.protocol_reader.readline())

    assert identity["parent_pid"] == os.getpid()
    assert identity["pid"] == process.process.pid
    assert identity["process_group"] == process.process.pid
    assert identity["argv"] == [str(resolution.executable_path)]
    assert set(identity["env"]) <= {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}

    process.close_stdin()
    assert process.wait(5) == 0
    process.close_readers()


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned process-group signaling")
def test_terminate_signals_owned_process_group(
    resolution: InstalledSidecarExecutableResolution,
    tmp_path: Path,
) -> None:
    process = spawn_owned_sidecar(resolution)
    marker = tmp_path / "grandchild-signal"
    process.protocol_writer.write(b"GRANDCHILD " + base64.b64encode(str(marker).encode()) + b"\n")
    process.protocol_writer.flush()
    int(process.protocol_reader.readline())

    assert process.terminate(5) < 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if marker.exists() and marker.read_text() == "terminated":
            break
        time.sleep(0.02)
    else:
        pytest.fail("owned grandchild did not observe process-group terminate")
    _close_process_streams(process)


@pytest.mark.skipif(os.name != "posix", reason="POSIX retained process-group ownership")
@pytest.mark.parametrize("method", ["terminate", "kill"])
def test_group_signal_reaches_grandchild_after_direct_child_exits(
    resolution: InstalledSidecarExecutableResolution,
    method: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = spawn_owned_sidecar(resolution)
    marker = tmp_path / "parent-exited"
    process.protocol_writer.write(b"ORPHAN " + base64.b64encode(str(marker).encode()) + b"\n")
    process.protocol_writer.flush()
    int(process.protocol_reader.readline())
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.read_text() == "parent-exited"
    assert process.process.returncode is None
    reader_done = threading.Event()

    def read_to_eof() -> None:
        process.protocol_reader.read()
        reader_done.set()

    reader = threading.Thread(target=read_to_eof)
    reader.start()
    try:
        assert getattr(process, method)(5) == 0
        reader.join(timeout=5)
        assert reader_done.is_set()
        monkeypatch.setattr(
            owned_process.os,
            "killpg",
            lambda *_: pytest.fail("reaped process-group ID must not be signaled again"),
        )
        assert process.kill(5) == 0
    finally:
        if reader.is_alive():
            os.killpg(process.process.pid, signal.SIGKILL)
            reader.join(timeout=5)
        _close_process_streams(process)


def test_spawn_failure_and_repeated_spawn_do_not_leak_parent_fds(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    before = len(os.listdir("/dev/fd"))
    missing = installed_release.InstalledSidecarExecutableResolution(
        slot_root=resolution.slot_root,
        manifest_path=resolution.manifest_path,
        executable_path=resolution.slot_root / "missing",
        manifest_id=resolution.manifest_id,
        manifest_sha256=resolution.manifest_sha256,
        product_build_id=resolution.product_build_id,
        target=resolution.target,
        executable_size_bytes=0,
        executable_sha256="0" * 64,
    )
    with pytest.raises(OSError):
        spawn_owned_sidecar(missing)
    for _ in range(20):
        process = spawn_owned_sidecar(resolution)
        process.close_stdin()
        assert process.wait(5) == 0
        process.close_readers()

    assert len(os.listdir("/dev/fd")) <= before


def test_wait_requires_a_finite_positive_timeout(
    resolution: InstalledSidecarExecutableResolution,
) -> None:
    process = spawn_owned_sidecar(resolution)
    try:
        for timeout in (None, True, 0, -1, float("inf")):
            with pytest.raises((TypeError, ValueError)):
                process.wait(timeout)  # type: ignore[arg-type]
    finally:
        process.kill(5)
        _close_process_streams(process)


def test_new_primitives_have_no_production_import_config_or_entrypoint() -> None:
    project_root = Path(__file__).parents[1]
    production_files = [
        path
        for path in (project_root / "src").rglob("*.py")
        if path.name not in {"installed_release.py", "owned_sidecar_process.py"}
    ]
    references = [
        path
        for path in production_files
        if "installed_release" in path.read_text(encoding="utf-8")
        or "owned_sidecar_process" in path.read_text(encoding="utf-8")
    ]
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")

    assert references == []
    assert "installed_release" not in pyproject
    assert "owned_sidecar_process" not in pyproject


def test_resolver_failure_occurs_before_any_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(InstalledReleaseError):
        installed_release.resolve_installed_sidecar_executable(tmp_path / "missing-slot")

    assert calls == []
