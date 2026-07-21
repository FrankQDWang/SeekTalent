from __future__ import annotations

import base64
import copy
import io
import json
import os
import pickle
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import IO, Callable, cast

import pytest

if os.name == "posix":
    import fcntl
else:
    fcntl = None

import seektalent.installed_release as installed_release
import seektalent.owned_sidecar_process as owned_process
from seektalent.installed_release import AuthenticatedInstalledSidecarLaunch, InstalledReleaseError
from seektalent.installed_slot import (
    ActiveSlotPointerV1,
    InstalledSidecarLaunchLease,
    acquire_installed_sidecar_launch_lease,
    canonical_active_slot_pointer_bytes,
)
from seektalent.owned_sidecar_process import spawn_owned_sidecar
from tests.test_installed_release import _install_slot
from tests.test_release_signing import VERIFICATION_TIME, _policy, _signed
from seektalent.release_manifest import parse_release_manifest
from seektalent.windows_installed_binding import (
    WindowsLaunchBindingError,
    WindowsLaunchBindingReason,
)


PROBE = Path(__file__).parent / "support" / "owned_sidecar_probe.py"
requires_native_probe = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows child creation is intentionally fail-closed until issue #369",
)
INVALID_TIMEOUTS = [
    pytest.param(None, TypeError, id="none"),
    pytest.param(False, TypeError, id="false"),
    pytest.param(True, TypeError, id="true"),
    pytest.param(0, ValueError, id="zero"),
    pytest.param(-1, ValueError, id="negative"),
    pytest.param(float("nan"), ValueError, id="nan"),
    pytest.param(float("inf"), ValueError, id="positive-infinity"),
    pytest.param(float("-inf"), ValueError, id="negative-infinity"),
]


class _FakePopen:
    def __init__(self) -> None:
        self.args = ["fake-sidecar"]
        self.pid = 12345
        self.returncode: int | None = None
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


class _RetryableCloseStream:
    def __init__(self, failures: int | None) -> None:
        self._failures = failures
        self.close_calls = 0
        self.closed = False

    def close(self) -> None:
        self.close_calls += 1
        if self._failures is None or self.close_calls <= self._failures:
            raise OSError("injected stream close failure")
        self.closed = True


def _fake_owned_process(*, process_group_id: int | None = None) -> tuple[owned_process.OwnedSidecarProcess, _FakePopen]:
    fake = _FakePopen()
    process = owned_process.OwnedSidecarProcess(
        _process=cast(subprocess.Popen[bytes], fake),
        protocol_writer=fake.stdin,
        protocol_reader=fake.stdout,
        stderr_reader=fake.stderr,
        _process_group_id=process_group_id,
    )
    return process, fake


@pytest.fixture
def lease_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], InstalledSidecarLaunchLease]:
    probe = f"#!{sys.executable}\nexec(open({str(PROBE)!r}, 'rb').read())\n".encode()
    source_slot, _, _ = _install_slot(tmp_path, monkeypatch, executable_bytes=probe)
    installation_root = tmp_path / "installation"
    slot_root = installation_root / "slots" / "A"
    slot_root.parent.mkdir(parents=True)
    source_slot.rename(slot_root)
    manifest_path = slot_root / "release" / "release-manifest.json"
    manifest = parse_release_manifest(manifest_path.read_bytes())
    _, signature_payload = _signed(manifest)
    manifest_path.parent.joinpath("signatures").mkdir()
    manifest_path.parent.joinpath("signatures", "release-manifest.sig").write_text(
        __import__("json").dumps(signature_payload, separators=(",", ":")), encoding="utf-8"
    )
    control = installation_root / "control"
    control.mkdir()
    control.joinpath("installation-id").write_bytes(b"test-installation-1")
    control.joinpath("active-slot.lock").write_bytes(b"0")
    control.joinpath("slot-A.lock").write_bytes(b"0")
    control.joinpath("slot-B.lock").write_bytes(b"0")
    pointer = ActiveSlotPointerV1.model_construct(
        schema_version="seektalent.active-slot/v1",
        installation_id="test-installation-1",
        physical_slot="A",
        pointer_generation=1,
        product_build_id=manifest.product_build_id,
        release_manifest_sha256=installed_release.release_manifest_digest(manifest),
        committed_at="2026-07-20T12:00:00Z",
    )
    control.joinpath("active-slot.json").write_bytes(canonical_active_slot_pointer_bytes(pointer))

    return lambda: acquire_installed_sidecar_launch_lease(installation_root, _policy(), VERIFICATION_TIME)


@pytest.fixture
def lease(lease_factory: Callable[[], InstalledSidecarLaunchLease]) -> InstalledSidecarLaunchLease:
    return lease_factory()


def _close_process_streams(process: owned_process.OwnedSidecarProcess) -> None:
    if process.poll() is None:
        process.kill(5)
    process.close_stdin()
    process.close_readers()


def test_spawn_requires_live_typed_lease_before_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(TypeError):
        spawn_owned_sidecar(Path("/tmp/not-a-lease"))  # type: ignore[arg-type]

    assert calls == []


def test_spawn_rejects_caller_fabricated_admission_before_popen(
    lease: InstalledSidecarLaunchLease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = lease.admission
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(TypeError):
        AuthenticatedInstalledSidecarLaunch(
            resolution=admission.resolution,
            manifest_id=admission.manifest_id,
            manifest_sha256=admission.manifest_sha256,
            product_build_id=admission.product_build_id,
            main_application_build_id=admission.main_application_build_id,
            main_application_tree_sha256=admission.main_application_tree_sha256,
            sidecar_build_id=admission.sidecar_build_id,
            sidecar_tree_sha256=admission.sidecar_tree_sha256,
            sidecar_executable_sha256=admission.sidecar_executable_sha256,
            source_port_protocol=admission.source_port_protocol,
            signer_key_id=admission.signer_key_id,
            trust_policy_id=admission.trust_policy_id,
            trust_policy_revision=admission.trust_policy_revision,
        )
    with pytest.raises(TypeError):
        spawn_owned_sidecar({"resolution": lease.resolution})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        spawn_owned_sidecar(lease.resolution)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        replace(lease, manifest_id="caller-forged")
    assert calls == []


def test_authenticated_admission_copy_preserves_one_factory_capability(
    lease: InstalledSidecarLaunchLease,
) -> None:
    assert copy.copy(lease.admission) is lease.admission
    assert copy.deepcopy(lease.admission) is lease.admission
    assert not hasattr(installed_release, "_FACTORY_ADMISSIONS")


def test_invalid_lifecycle_timeout_has_no_fake_process_or_group_signal_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, fake = _fake_owned_process(process_group_id=12345)
    group_signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        owned_process,
        "_signal_process_group",
        lambda process_group_id, sig: group_signals.append((process_group_id, sig)),
    )

    for method in (process.terminate, process.kill):
        for timeout, error in [
            (None, TypeError),
            (False, TypeError),
            (True, TypeError),
            (0, ValueError),
            (-1, ValueError),
            (float("nan"), ValueError),
            (float("inf"), ValueError),
            (float("-inf"), ValueError),
        ]:
            with pytest.raises(error):
                method(timeout)  # type: ignore[arg-type]

    assert process.returncode is None
    assert fake.terminate_calls == 0
    assert fake.kill_calls == 0
    assert group_signals == []


def test_owned_sidecar_process_cannot_be_copied_or_serialized() -> None:
    process, _ = _fake_owned_process()

    with pytest.raises(TypeError):
        copy.copy(process)
    with pytest.raises(TypeError):
        copy.deepcopy(process)
    with pytest.raises(TypeError):
        pickle.dumps(process)


def test_windows_spawn_fails_closed_until_suspended_binding_exists(
    lease: InstalledSidecarLaunchLease,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    with monkeypatch.context() as context:
        context.setattr(owned_process.os, "name", "nt")
        context.setattr(
            owned_process.subprocess,
            "Popen",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        with pytest.raises(WindowsLaunchBindingError) as raised:
            spawn_owned_sidecar(lease)

    assert raised.value.reason == WindowsLaunchBindingReason.LAUNCH_BINDING_UNSUPPORTED
    assert calls == []
    next_lease = lease_factory()
    next_lease.close()


@pytest.mark.parametrize("working_directory_kind", ["missing", "file", "symlink"])
def test_spawn_rejects_invalid_fixed_working_directory_before_popen(
    lease: InstalledSidecarLaunchLease,
    monkeypatch: pytest.MonkeyPatch,
    working_directory_kind: str,
) -> None:
    release_directory = lease.manifest_path.parent
    original_directory = release_directory.with_name("original-release")
    release_directory.rename(original_directory)
    if working_directory_kind == "file":
        release_directory.write_text("not a directory", encoding="utf-8")
    elif working_directory_kind == "symlink":
        release_directory.symlink_to(original_directory, target_is_directory=True)
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ValueError):
        spawn_owned_sidecar(lease)

    assert calls == []


@requires_native_probe
def test_binary_roundtrip_and_stderr_remain_separate(
    lease: InstalledSidecarLaunchLease,
) -> None:
    process = spawn_owned_sidecar(lease)
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


@requires_native_probe
def test_spawn_cwd_is_fixed_when_main_has_different_ambient_cwd(
    lease: InstalledSidecarLaunchLease,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    monkeypatch.chdir(ambient)
    process = spawn_owned_sidecar(lease)
    line: list[bytes] = []
    reader = threading.Thread(target=lambda: line.append(process.protocol_reader.readline()), daemon=True)
    try:
        process.protocol_writer.write(b"IDENTITY\n")
        process.protocol_writer.flush()
        reader.start()
        reader.join(timeout=1)
        assert not reader.is_alive(), "sidecar did not return its identity before the test deadline"
        identity = json.loads(line[0])

        assert Path.cwd() == ambient
        assert identity["cwd"] == str(lease.manifest_path.parent)

        process.close_stdin()
        assert process.wait(5) == 0
    finally:
        if process.poll() is None:
            process.kill(1)
        process.close_readers()
        reader.join(timeout=1)


@requires_native_probe
def test_concurrent_drain_handles_stdout_and_stderr_above_pipe_capacity(
    lease: InstalledSidecarLaunchLease,
) -> None:
    process = spawn_owned_sidecar(lease)
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
    lease: InstalledSidecarLaunchLease,
) -> None:
    assert fcntl is not None
    source_fd = os.open(PROBE, os.O_RDONLY)
    sentinel_fd = fcntl.fcntl(source_fd, fcntl.F_DUPFD, 200)
    os.close(source_fd)
    os.set_inheritable(sentinel_fd, True)
    try:
        process = spawn_owned_sidecar(lease)
        process.protocol_writer.write(f"FD {sentinel_fd}\n".encode())
        process.close_stdin()
        assert process.protocol_reader.readline() == b"CLOSED\n"
        assert process.wait(5) == 0
        process.close_readers()
    finally:
        os.close(sentinel_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX CLOEXEC inspection")
def test_parent_pipe_endpoints_are_non_inheritable(
    lease: InstalledSidecarLaunchLease,
) -> None:
    process = spawn_owned_sidecar(lease)

    assert not os.get_inheritable(process.protocol_writer.fileno())
    assert not os.get_inheritable(process.protocol_reader.fileno())
    assert not os.get_inheritable(process.stderr_reader.fileno())

    process.close_stdin()
    assert process.wait(5) == 0
    process.close_readers()


@requires_native_probe
def test_close_stdin_delivers_eof_and_child_exit_closes_readers(
    lease: InstalledSidecarLaunchLease,
) -> None:
    process = spawn_owned_sidecar(lease)
    process.close_stdin()

    assert process.protocol_reader.read() == b"EOF\n"
    assert process.stderr_reader.read() == b""
    assert process.wait(5) == 0
    assert process.protocol_reader.read() == b""
    assert process.stderr_reader.read() == b""
    process.close_readers()


@pytest.mark.skipif(os.name != "posix", reason="POSIX direct-child reaping")
def test_clean_exit_waits_and_reaps_direct_child(lease: InstalledSidecarLaunchLease) -> None:
    process = spawn_owned_sidecar(lease)
    process.protocol_writer.write(b"EXIT 7\n")
    process.protocol_writer.flush()

    assert process.wait(5) == 7
    assert process.poll() == 7
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    _close_process_streams(process)


@pytest.mark.parametrize("method", ["terminate", "kill"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX direct-child reaping")
def test_terminate_and_kill_wait_and_reap(
    lease: InstalledSidecarLaunchLease,
    method: str,
) -> None:
    process = spawn_owned_sidecar(lease)
    return_code = getattr(process, method)(5)

    assert return_code < 0
    assert process.poll() == return_code
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    _close_process_streams(process)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-session ownership")
def test_spawned_process_is_direct_child_and_process_group_leader(
    lease: InstalledSidecarLaunchLease,
) -> None:
    process = spawn_owned_sidecar(lease)
    process.protocol_writer.write(b"IDENTITY\n")
    process.protocol_writer.flush()
    identity = json.loads(process.protocol_reader.readline())

    assert identity["parent_pid"] == os.getpid()
    assert identity["pid"] == process.pid
    assert identity["process_group"] == process.pid
    assert identity["argv"] == [str(lease.executable_path)]
    assert identity["cwd"] == str(lease.manifest_path.parent)
    assert set(identity["env"]) <= {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    assert not hasattr(process, "process")

    process.close_stdin()
    assert process.wait(5) == 0
    process.close_readers()


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned process-group signaling")
def test_terminate_signals_owned_process_group(
    lease: InstalledSidecarLaunchLease,
    tmp_path: Path,
) -> None:
    process = spawn_owned_sidecar(lease)
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
    lease: InstalledSidecarLaunchLease,
    method: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = spawn_owned_sidecar(lease)
    marker = tmp_path / "parent-exited"
    process.protocol_writer.write(b"ORPHAN " + base64.b64encode(str(marker).encode()) + b"\n")
    process.protocol_writer.flush()
    int(process.protocol_reader.readline())
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.read_text() == "parent-exited"
    assert process.returncode is None
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
            os.killpg(process.pid, signal.SIGKILL)
            reader.join(timeout=5)
        _close_process_streams(process)


@pytest.mark.skipif(os.name != "posix", reason="POSIX retained process-group ownership")
def test_lifecycle_lock_prevents_wait_from_reaping_during_group_signal(
    lease: InstalledSidecarLaunchLease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = spawn_owned_sidecar(lease)
    signal_entered = threading.Event()
    release_signal = threading.Event()
    wait_finished = threading.Event()
    signal_calls: list[tuple[int, signal.Signals]] = []
    results: dict[str, int] = {}
    errors: list[Exception] = []

    def blocked_group_signal(process_group_id: int, sig: signal.Signals) -> None:
        signal_entered.set()
        release_signal.wait(timeout=5)
        signal_calls.append((process_group_id, sig))

    def terminate() -> None:
        try:
            results["terminate"] = process.terminate(5)
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
            errors.append(exc)

    def wait() -> None:
        try:
            results["wait"] = process.wait(5)
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
            errors.append(exc)
        finally:
            wait_finished.set()

    terminate_thread = threading.Thread(target=terminate)
    wait_thread = threading.Thread(target=wait)
    monkeypatch.setattr(owned_process, "_signal_process_group", blocked_group_signal)
    try:
        terminate_thread.start()
        assert signal_entered.wait(timeout=5)
        process.protocol_writer.write(b"EXIT 0\n")
        process.protocol_writer.flush()
        wait_thread.start()

        assert not wait_finished.wait(timeout=0.2)
        release_signal.set()
        terminate_thread.join(timeout=5)
        wait_thread.join(timeout=5)

        assert not terminate_thread.is_alive()
        assert not wait_thread.is_alive()
        assert errors == []
        assert results == {"terminate": 0, "wait": 0}
        assert signal_calls == [(process.pid, signal.SIGTERM)]
        assert process._process_group_id is None
    finally:
        release_signal.set()
        terminate_thread.join(timeout=5)
        wait_thread.join(timeout=5)
        if process.returncode is None:
            process.kill(5)
        _close_process_streams(process)


@pytest.mark.skipif(os.name != "posix", reason="POSIX parent descriptor accounting")
def test_spawn_failure_and_repeated_spawn_do_not_leak_parent_fds(
    lease: InstalledSidecarLaunchLease,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    before = len(os.listdir("/dev/fd"))
    executable = lease.executable_path
    moved_executable = executable.with_name("temporarily-missing-sidecar")
    executable.rename(moved_executable)
    with pytest.raises(OSError):
        spawn_owned_sidecar(lease)
    moved_executable.rename(executable)
    for _ in range(20):
        process = spawn_owned_sidecar(lease_factory())
        process.close_stdin()
        assert process.wait(5) == 0
        process.close_readers()

    assert len(os.listdir("/dev/fd")) <= before


@requires_native_probe
def test_wait_requires_a_finite_positive_timeout(
    lease: InstalledSidecarLaunchLease,
) -> None:
    process = spawn_owned_sidecar(lease)
    try:
        for timeout in (None, True, 0, -1, float("inf")):
            with pytest.raises((TypeError, ValueError)):
                process.wait(timeout)  # type: ignore[arg-type]
    finally:
        process.kill(5)
        _close_process_streams(process)


@pytest.mark.parametrize("method", ["terminate", "kill"])
@pytest.mark.parametrize(("timeout", "error"), INVALID_TIMEOUTS)
@requires_native_probe
def test_terminate_and_kill_reject_invalid_timeout_before_signaling(
    lease: InstalledSidecarLaunchLease,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    timeout: object,
    error: type[Exception],
) -> None:
    process = spawn_owned_sidecar(lease)
    group_signals: list[tuple[int, signal.Signals]] = []
    try:
        with monkeypatch.context() as context:
            context.setattr(
                owned_process,
                "_signal_process_group",
                lambda process_group_id, sig: group_signals.append((process_group_id, sig)),
            )

            with pytest.raises(error):
                getattr(process, method)(timeout)

            assert process.returncode is None
            assert group_signals == []
    finally:
        process.kill(5)
        _close_process_streams(process)


@requires_native_probe
def test_terminate_normalizes_timeout_once_and_reuses_it_for_fallback_kill(
    lease: InstalledSidecarLaunchLease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = spawn_owned_sidecar(lease)
    normalized_inputs: list[float] = []
    wait_timeouts: list[float | None] = []
    original_bounded_timeout = owned_process._bounded_timeout
    original_wait = process._process.wait

    def normalize(timeout: float) -> float:
        normalized_inputs.append(timeout)
        return original_bounded_timeout(timeout)

    def wait(*, timeout: float | None = None) -> int:
        wait_timeouts.append(timeout)
        if len(wait_timeouts) == 1:
            raise subprocess.TimeoutExpired(process._process.args, timeout)
        return original_wait(timeout=timeout)

    try:
        with monkeypatch.context() as context:
            context.setattr(owned_process, "_bounded_timeout", normalize)
            context.setattr(owned_process, "_terminate_owned_process", lambda *_: None)
            context.setattr(process._process, "wait", wait)

            assert process.terminate(5) < 0

        assert normalized_inputs == [5]
        assert wait_timeouts == [5.0, 5.0]
    finally:
        if process.returncode is None:
            process.kill(5)
        _close_process_streams(process)


@requires_native_probe
def test_kill_propagates_wait_timeout_expired(
    lease: InstalledSidecarLaunchLease,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = spawn_owned_sidecar(lease)
    try:
        with monkeypatch.context() as context:
            context.setattr(
                process._process,
                "wait",
                lambda *, timeout=None: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(process._process.args, timeout)
                ),
            )

            with pytest.raises(subprocess.TimeoutExpired):
                process.kill(5)
    finally:
        process.wait(5)
        _close_process_streams(process)


def test_new_primitives_have_no_production_import_config_or_entrypoint() -> None:
    project_root = Path(__file__).parents[1]
    production_files = [
        path
        for path in (project_root / "src").rglob("*.py")
        if path.name
        not in {
            "installed_filesystem.py",
            "installed_release.py",
            "installed_slot.py",
            "owned_sidecar_process.py",
            "sidecar_bootstrap.py",
            "sidecar_readiness.py",
            "windows_installed_binding.py",
            "windows_native_files.py",
            "windows_sidecar_process.py",
        }
    ]
    references = [
        path
        for path in production_files
        if "installed_filesystem" in path.read_text(encoding="utf-8")
        or "installed_release" in path.read_text(encoding="utf-8")
        or "installed_slot" in path.read_text(encoding="utf-8")
        or "owned_sidecar_process" in path.read_text(encoding="utf-8")
        or "windows_installed_binding" in path.read_text(encoding="utf-8")
        or "windows_native_files" in path.read_text(encoding="utf-8")
        or "windows_sidecar_process" in path.read_text(encoding="utf-8")
    ]
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")

    assert references == []
    assert "installed_filesystem" not in pyproject
    assert "installed_release" not in pyproject
    assert "installed_slot" not in pyproject
    assert "owned_sidecar_process" not in pyproject
    assert "windows_installed_binding" not in pyproject
    assert "windows_native_files" not in pyproject
    assert "windows_sidecar_process" not in pyproject


def test_resolver_failure_occurs_before_any_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(InstalledReleaseError):
        spawn_owned_sidecar(
            installed_release.resolve_installed_sidecar_executable(tmp_path / "missing-slot")
        )

    assert calls == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX effective executable access")
def test_non_effective_executable_fails_in_resolver_before_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_installed_release import _install_slot

    slot_root, executable, _ = _install_slot(tmp_path, monkeypatch)
    executable.chmod(0o401)
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(InstalledReleaseError) as raised:
        spawn_owned_sidecar(installed_release.resolve_installed_sidecar_executable(slot_root))

    assert raised.value.reason == installed_release.InstalledReleaseReason.FILE_MODE_MISMATCH
    assert calls == []


def test_unreaped_readiness_cleanup_keeps_the_original_posix_group_for_its_retry(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    fake = _FakePopen()
    process = owned_process.OwnedSidecarProcess(
        _process=cast(subprocess.Popen[bytes], fake),
        protocol_writer=fake.stdin,
        protocol_reader=fake.stdout,
        stderr_reader=fake.stderr,
        _process_group_id=43210,
        _lease_state=lease._take_for_spawn(),
    )
    signals: list[tuple[int, signal.Signals]] = []
    attempts = 0

    def wait(*, timeout: float | None = None) -> int:
        nonlocal attempts
        del timeout
        attempts += 1
        if attempts == 1:
            raise subprocess.TimeoutExpired("sidecar", 1)
        fake.returncode = 0
        return 0

    monkeypatch.setattr(fake, "wait", wait)
    monkeypatch.setattr(
        owned_process,
        "_signal_process_group",
        lambda process_group_id, sig: signals.append((process_group_id, sig)),
    )

    cleanup_error = process._cleanup_after_handshake_failure(RuntimeError("readiness failed"))

    assert isinstance(cleanup_error, owned_process.SidecarSpawnCleanupError)
    assert cleanup_error.direct_child_reaped is False
    assert signals == [(43210, signal.SIGKILL)]
    assert cleanup_error.reap() is True
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is True
    assert signals == [(43210, signal.SIGKILL), (43210, signal.SIGKILL)]
    lease_factory().close()


def test_windows_lease_cleanup_failure_retains_primary_error_and_can_retry() -> None:
    class _LeaseState:
        released = False

        def __init__(self) -> None:
            self.close_attempts = 0

        def close(self) -> None:
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise WindowsLaunchBindingError(WindowsLaunchBindingReason.NATIVE_HANDLE_RELEASE_FAILED)
            self.released = True

    fake = _FakePopen()
    lease_state = _LeaseState()
    process = owned_process.OwnedSidecarProcess(
        _process=cast(subprocess.Popen[bytes], fake),
        protocol_writer=fake.stdin,
        protocol_reader=fake.stdout,
        stderr_reader=fake.stderr,
        _process_group_id=None,
        _lease_state=cast(owned_process._InstalledSidecarLeaseState, lease_state),
    )
    primary = RuntimeError("typed-readiness-primary")

    cleanup_error = process._cleanup_after_handshake_failure(primary)

    assert isinstance(cleanup_error, owned_process.SidecarSpawnCleanupError)
    assert cleanup_error.primary_error is primary
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is False
    assert cleanup_error.reap() is True
    assert cleanup_error.lease_released is True


def test_reaped_handshake_cleanup_retains_only_unclosed_pipes_until_each_closes() -> None:
    fake = _FakePopen()
    first = _RetryableCloseStream(1)
    second = _RetryableCloseStream(2)
    process = owned_process.OwnedSidecarProcess(
        _process=cast(subprocess.Popen[bytes], fake),
        protocol_writer=cast(IO[bytes], first),
        protocol_reader=cast(IO[bytes], second),
        stderr_reader=fake.stderr,
        _process_group_id=None,
    )

    cleanup_error = process._cleanup_after_handshake_failure(RuntimeError("readiness failed"))

    assert isinstance(cleanup_error, owned_process.SidecarSpawnCleanupError)
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is True
    assert first.close_calls == 1
    assert second.close_calls == 1
    assert cleanup_error.reap() is False
    assert first.closed is True
    assert second.closed is False
    assert first.close_calls == 2
    assert second.close_calls == 2
    assert cleanup_error.reap() is True
    assert second.closed is True
    assert second.close_calls == 3


def test_permanently_unclosed_pipe_stays_in_abandoned_cleanup_maintenance() -> None:
    fake = _FakePopen()
    stuck = _RetryableCloseStream(None)
    process = owned_process.OwnedSidecarProcess(
        _process=cast(subprocess.Popen[bytes], fake),
        protocol_writer=cast(IO[bytes], stuck),
        protocol_reader=fake.stdout,
        stderr_reader=fake.stderr,
        _process_group_id=None,
    )
    cleanup_error = process._cleanup_after_handshake_failure(RuntimeError("readiness failed"))

    assert isinstance(cleanup_error, owned_process.SidecarSpawnCleanupError)
    owner_id = owned_process._spawn_cleanup_owner_id(cleanup_error)
    try:
        cleanup_error.abandon()
        result = None
        for _ in range(owned_process.FAILED_SPAWN_CLEANUP_MAX_NATIVE_ATTEMPTS - 1):
            result = owned_process.maintain_abandoned_sidecar_spawns()
        assert result is not None
        assert result.terminal >= 1
        assert cleanup_error.cleanup_terminally_failed is True
        assert stuck.closed is False
    finally:
        # This test intentionally reaches an unrecoverable synthetic state;
        # remove its test-only owner so later custodian tests see only owners
        # that they created.
        with owned_process._SPAWN_CLEANUP_OWNERS_LOCK:
            owned_process._SPAWN_CLEANUP_OWNERS.pop(owner_id, None)
