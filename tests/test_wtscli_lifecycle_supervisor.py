from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent.wtscli_runtime import WtsCliRuntime
from seektalent_ui.server import create_app
from seektalent.wtscli_lifecycle_supervisor import (
    WtsCliLifecycleError,
    WtsCliLifecycleSupervisor,
)
import seektalent.wtscli_lifecycle_supervisor as lifecycle_module
from tests.browser_bridge_bundle_fixtures import exact_browser_bridge_requirement
from tests.settings_factory import make_settings


class _FakeProcess:
    _next_pid = 41000

    def __init__(self) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, *, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _runtime() -> WtsCliRuntime:
    return WtsCliRuntime(
        node=Path("/domi/node"),
        wtscli_main=Path("/bundle/node_modules/wtscli/dist/src/main.js"),
        requirement=exact_browser_bridge_requirement(),
    )


def _isolated_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    process_factory=None,
) -> tuple[WtsCliLifecycleSupervisor, list[object]]:
    runtime = _runtime()
    process_calls: list[object] = []

    def configure(_runtime: WtsCliRuntime) -> None:
        state_root = tmp_path / "wtscli-state"
        state_root.mkdir(parents=True, exist_ok=True)
        supervisor._status_path = state_root / "status.json"
        supervisor._lock_path = state_root / "owner.lock"

    def create_process(command, **kwargs):
        process = process_factory() if process_factory is not None else _FakeProcess()
        process_calls.append((process, command, kwargs))
        return process

    supervisor = WtsCliLifecycleSupervisor(
        runtime=runtime,
        process_factory=create_process,
    )
    monkeypatch.setattr(supervisor, "_verify_bundle", lambda _runtime: None)
    monkeypatch.setattr(supervisor, "_configure_paths", configure)
    monkeypatch.setattr(supervisor, "_wait_for_sidecar_owner", lambda: None)
    monkeypatch.setattr(lifecycle_module, "wtscli_subprocess_env", lambda **_kwargs: {})
    return supervisor, process_calls


def test_supervisor_starts_one_sidecar_and_shutdowns_only_its_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, process_calls = _isolated_supervisor(tmp_path, monkeypatch)

    supervisor.start()
    supervisor.start()

    assert len(process_calls) == 1
    process, command, kwargs = process_calls[0]
    assert command[0] == "/domi/node"
    assert command[1].endswith("wtscli_lifecycle_sidecar.mjs")
    assert kwargs["start_new_session"] is True
    assert supervisor.status().state == "warming"

    supervisor.shutdown()

    assert process.terminated is False
    assert supervisor.status().state == "stopped"


def test_supervisor_waits_for_control_shutdown_before_force_termination(
    tmp_path: Path,
) -> None:
    supervisor = WtsCliLifecycleSupervisor(runtime=_runtime())
    supervisor._control_path = tmp_path / "control.json"

    class SlowProcess(_FakeProcess):
        def __init__(self) -> None:
            super().__init__()
            self.wait_calls = 0

        def wait(self, *, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("wtscli-sidecar", timeout)
            return super().wait(timeout=timeout)

    process = SlowProcess()
    supervisor._stop_process(process, request_control=True)

    assert process.wait_calls == 2
    assert process.terminated is True
    assert json.loads(supervisor._control_path.read_text(encoding="utf-8"))["command"] == "shutdown"


def test_two_run_readiness_requests_share_one_lifecycle_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, process_calls = _isolated_supervisor(tmp_path, monkeypatch)
    supervisor.start()
    process = process_calls[0][0]
    ready = lifecycle_module.WtsCliLifecycleStatus(
        state="ready",
        bridge_build_id=exact_browser_bridge_requirement().bridge_build_id,
        supervisor_pid=process.pid,
        daemon_pid=42001,
        daemon_owned=True,
        process_healthy=True,
        extension_connected=True,
        restart_count=0,
        first_failure_code=None,
        reason_code=None,
        observed_at="2026-07-31T00:00:00Z",
    )
    monkeypatch.setattr(supervisor, "status", lambda: ready)

    assert supervisor.ensure_ready(timeout_seconds=0.1) is ready
    assert supervisor.ensure_ready(timeout_seconds=0.1) is ready
    assert len(process_calls) == 1


def test_supervisor_rejects_foreign_or_mismatched_owner_without_killing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, process_calls = _isolated_supervisor(tmp_path, monkeypatch)
    supervisor._configure_paths(_runtime())
    assert supervisor._lock_path is not None
    supervisor._lock_path.write_text(
        json.dumps(
            {
                "schemaVersion": "seektalent.wtscli_supervisor_lock.v1",
                "supervisorPid": os.getpid(),
                "bridgeBuildId": "foreign-build",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WtsCliLifecycleError, match="wtscli_supervisor_foreign_owner"):
        supervisor.start()

    assert process_calls == []


def test_supervisor_rejects_a_second_same_build_owner_without_sharing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, process_calls = _isolated_supervisor(tmp_path, monkeypatch)
    supervisor._configure_paths(_runtime())
    assert supervisor._lock_path is not None
    supervisor._lock_path.write_text(
        json.dumps(
            {
                "schemaVersion": "seektalent.wtscli_supervisor_lock.v1",
                "supervisorPid": os.getpid(),
                "bridgeBuildId": exact_browser_bridge_requirement().bridge_build_id,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WtsCliLifecycleError, match="wtscli_supervisor_foreign_owner"):
        supervisor.start()

    assert process_calls == []


def test_readonly_attach_observes_a_healthy_owner_without_adopting_or_stopping_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    state_root = tmp_path / "wtscli-state"

    def configure(supervisor: WtsCliLifecycleSupervisor, _runtime: WtsCliRuntime) -> None:
        state_root.mkdir(parents=True, exist_ok=True)
        supervisor._status_path = state_root / "status.json"
        supervisor._lock_path = state_root / "owner.lock"
        supervisor._control_path = state_root / "control.json"

    monkeypatch.setattr(lifecycle_module, "inspect_wtscli_runtime", lambda: runtime)
    monkeypatch.setattr(
        WtsCliLifecycleSupervisor,
        "_verify_bundle",
        lambda _self, _runtime: None,
    )
    monkeypatch.setattr(WtsCliLifecycleSupervisor, "_configure_paths", configure)
    observer = WtsCliLifecycleSupervisor.attach(make_settings(workspace_root=str(tmp_path)))
    assert observer.status_path is not None
    observer._lock_path.write_text(
        json.dumps(
            {
                "schemaVersion": "seektalent.wtscli_supervisor_lock.v1",
                "supervisorPid": os.getpid(),
                "bridgeBuildId": exact_browser_bridge_requirement().bridge_build_id,
            }
        ),
        encoding="utf-8",
    )
    observer.status_path.write_text(
        json.dumps(
            {
                "schemaVersion": "seektalent.wtscli_supervisor_status.v1",
                "state": "ready",
                "bridgeBuildId": exact_browser_bridge_requirement().bridge_build_id,
                "supervisorPid": os.getpid(),
                "daemonPid": os.getpid(),
                "daemonOwned": True,
                "processHealthy": True,
                "extensionConnected": True,
                "restartCount": 0,
                "firstFailureCode": None,
                "reasonCode": None,
                "observedAt": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    assert observer.status().state == "ready"
    assert observer._process is None
    observer.shutdown()
    assert observer._lock_path.exists()


def test_supervisor_reports_extension_not_connected_as_bounded_readiness_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, _process_calls = _isolated_supervisor(tmp_path, monkeypatch)
    supervisor._configure_paths(_runtime())
    assert supervisor._status_path is not None
    supervisor._status_path.write_text(
        json.dumps(
            {
                "schemaVersion": "seektalent.wtscli_supervisor_status.v1",
                "state": "extension_not_connected",
                "bridgeBuildId": exact_browser_bridge_requirement().bridge_build_id,
                "supervisorPid": 41001,
                "daemonPid": 41002,
                "daemonOwned": True,
                "processHealthy": True,
                "extensionConnected": False,
                "restartCount": 0,
                "firstFailureCode": None,
                "reasonCode": None,
                "observedAt": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    supervisor._started = True
    clock = iter((0.0, 0.0, 1.0))
    supervisor._monotonic_clock = lambda: next(clock, 1.0)
    supervisor._sleep = lambda _seconds: None

    with pytest.raises(WtsCliLifecycleError, match="opencli_extension_disconnected"):
        supervisor.ensure_ready(timeout_seconds=0.1)

    snapshot = supervisor.health_snapshot()
    assert snapshot["processHealthy"] is True
    assert snapshot["extensionConnected"] is False


def test_readiness_does_not_restart_a_dead_sidecar_at_run_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, process_calls = _isolated_supervisor(tmp_path, monkeypatch)
    supervisor.start()
    first_process = process_calls[0][0]
    first_process.returncode = 70

    monkeypatch.setattr(
        supervisor,
        "status",
        lambda: lifecycle_module.WtsCliLifecycleStatus(
            state="needs_attention",
            bridge_build_id=exact_browser_bridge_requirement().bridge_build_id,
            supervisor_pid=None,
            daemon_pid=None,
            daemon_owned=False,
            process_healthy=False,
            extension_connected=False,
            restart_count=0,
            first_failure_code=None,
            reason_code="wtscli_supervisor_exited",
            observed_at=None,
        ),
    )

    with pytest.raises(WtsCliLifecycleError, match="wtscli_supervisor_exited"):
        supervisor.ensure_ready(timeout_seconds=0.1)

    assert len(process_calls) == 1
    assert process_calls[0][0] is first_process
    supervisor.shutdown()


def test_watchdog_replaces_a_hung_sidecar_with_stale_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, process_calls = _isolated_supervisor(tmp_path, monkeypatch)
    monkeypatch.setattr(
        lifecycle_module,
        "WTSCLI_SUPERVISOR_WATCHDOG_INTERVAL_SECONDS",
        0.01,
    )
    stale = lifecycle_module.WtsCliLifecycleStatus(
        state="ready",
        bridge_build_id=exact_browser_bridge_requirement().bridge_build_id,
        supervisor_pid=41001,
        daemon_pid=41002,
        daemon_owned=True,
        process_healthy=True,
        extension_connected=True,
        restart_count=0,
        first_failure_code=None,
        reason_code=None,
        observed_at="2020-01-01T00:00:00+00:00",
    )
    reads = 0

    def read_status() -> lifecycle_module.WtsCliLifecycleStatus:
        nonlocal reads
        reads += 1
        if reads == 1:
            return stale
        return replace(
            stale,
            supervisor_pid=process_calls[-1][0].pid,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )

    monkeypatch.setattr(supervisor, "_read_status", read_status)
    supervisor.start()
    deadline = time.monotonic() + 2
    while len(process_calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(process_calls) == 2
    assert process_calls[0][0].terminated is True
    assert supervisor._sidecar_restart_count == 1
    supervisor.shutdown()


def _write_fake_sidecar_package(root: Path) -> Path:
    package_dir = root / "package"
    browser_dir = package_dir / "dist" / "src" / "browser"
    browser_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    (package_dir / "daemon-child.mjs").write_text(
        """
import fs from 'node:fs';
const path = process.env.FAKE_HEALTH_PATH;
fs.writeFileSync(path, JSON.stringify({state: 'ready', status: {pid: process.pid}}));
if (process.env.FAKE_EXIT_IMMEDIATELY === '1') {
  fs.writeFileSync(path, JSON.stringify({state: 'stopped', status: null}));
  process.exit(Number(process.env.FAKE_EXIT_CODE || '2'));
}
process.on('SIGTERM', () => process.exit(0));
setInterval(() => {}, 1000);
""",
        encoding="utf-8",
    )
    (browser_dir / "daemon-lifecycle.js").write_text(
        """
export function resolveDaemonLaunchSpec() {
  return {binary: process.execPath, args: [process.env.FAKE_CHILD_SCRIPT]};
}
""",
        encoding="utf-8",
    )
    (browser_dir / "daemon-ownership.js").write_text(
        """
export const DAEMON_OWNERSHIP_TOKEN_ENV = 'WTSCLI_DAEMON_OWNERSHIP_TOKEN';
let record = null;
export function prepareDaemonOwnership() {
  record = {token: 'a'.repeat(64)};
  return record;
}
export function bindDaemonOwnershipPid(_token, pid) {
  if (process.env.FAKE_BIND_FAIL === '1') throw new Error('wtscli_bind_failed');
  record = {...record, pid};
}
export function loadDaemonOwnershipRecord() { return record; }
export function removeDaemonOwnershipRecord() { record = null; }
""",
        encoding="utf-8",
    )
    (browser_dir / "daemon-transport.js").write_text(
        """
import fs from 'node:fs';
let readyObserved = false;
let transientInjected = false;
function readHealth() {
  if (process.env.FAKE_ORPHAN_DAEMON === '1') {
    return {state: 'ready', status: {pid: process.pid, bridgeBuildId: process.env.FAKE_ORPHAN_BUILD_ID || 'exact-build'}};
  }
  try { return JSON.parse(fs.readFileSync(process.env.FAKE_HEALTH_PATH, 'utf8')); }
  catch { return {state: 'stopped', status: null}; }
}
export async function getDaemonHealth() {
  const health = readHealth();
  if (process.env.FAKE_TRANSIENT_AFTER_READY === '1' && health.state === 'ready') {
    if (!readyObserved) {
      readyObserved = true;
    } else if (!transientInjected) {
      transientInjected = true;
      fs.writeFileSync(process.env.FAKE_TRANSIENT_MARKER, 'injected');
      throw new Error('transient_health_timeout');
    }
  }
  return health;
}
export async function requestDaemonShutdown() {
  const health = readHealth();
  if (process.env.FAKE_SHUTDOWN_MARKER) fs.writeFileSync(process.env.FAKE_SHUTDOWN_MARKER, 'called');
  fs.writeFileSync(process.env.FAKE_HEALTH_PATH, JSON.stringify({state: 'stopped', status: null}));
  if (health.status?.pid) process.kill(health.status.pid, 'SIGTERM');
  return true;
}
""",
        encoding="utf-8",
    )
    return package_dir


def _wait_for_status(path: Path, *, predicate, timeout: float = 8.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.05)
            continue
        if predicate(payload):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for sidecar status at {path}")


def test_sidecar_restarts_the_same_foreground_daemon_after_child_crash(tmp_path: Path) -> None:
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    child_script = package_dir / "daemon-child.mjs"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    node = Path(node_text)
    sidecar = Path(__file__).parents[1] / "src" / "seektalent" / "wtscli_lifecycle_sidecar.mjs"
    env = {
        **os.environ,
        "FAKE_HEALTH_PATH": str(health_path),
        "FAKE_CHILD_SCRIPT": str(child_script),
    }
    process = subprocess.Popen(
        [
            str(node),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        first = _wait_for_status(status_path, predicate=lambda value: value.get("state") == "ready")
        first_pid = first["daemonPid"]
        health_path.write_text(json.dumps({"state": "stopped", "status": None}), encoding="utf-8")
        os.kill(int(first_pid), signal.SIGKILL)
        restarted = _wait_for_status(
            status_path,
            predicate=lambda value: value.get("state") == "ready"
            and value.get("restartCount") == 1
            and value.get("daemonPid") != first_pid,
        )
        assert restarted["daemonOwned"] is True
        assert restarted["firstFailureCode"] is not None
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        assert process.returncode == 0
        assert json.loads(status_path.read_text(encoding="utf-8"))["state"] == "stopped"


def test_sidecar_does_not_latch_foreign_owner_after_owned_health_timeout(
    tmp_path: Path,
) -> None:
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    transient_marker = tmp_path / "transient-injected"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    sidecar = (
        Path(__file__).parents[1]
        / "src"
        / "seektalent"
        / "wtscli_lifecycle_sidecar.mjs"
    )
    process = subprocess.Popen(
        [
            str(Path(node_text)),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
        ],
        env={
            **os.environ,
            "FAKE_HEALTH_PATH": str(health_path),
            "FAKE_CHILD_SCRIPT": str(package_dir / "daemon-child.mjs"),
            "FAKE_TRANSIENT_AFTER_READY": "1",
            "FAKE_TRANSIENT_MARKER": str(transient_marker),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        initial = _wait_for_status(
            status_path,
            predicate=lambda value: value.get("state") == "ready",
        )
        daemon_pid = initial["daemonPid"]
        _wait_for_status(
            status_path,
            predicate=lambda value: transient_marker.exists()
            and value.get("state") == "ready",
            timeout=3,
        )
        recovered = json.loads(status_path.read_text(encoding="utf-8"))
        assert recovered["daemonPid"] == daemon_pid
        assert recovered["reasonCode"] is None
        assert recovered["restartCount"] == 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        assert process.returncode == 0


def test_sidecar_stops_after_bounded_restart_budget_and_keeps_first_cause(
    tmp_path: Path,
) -> None:
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    env = {
        **os.environ,
        "FAKE_HEALTH_PATH": str(health_path),
        "FAKE_CHILD_SCRIPT": str(package_dir / "daemon-child.mjs"),
        "FAKE_EXIT_IMMEDIATELY": "1",
    }
    sidecar = Path(__file__).parents[1] / "src" / "seektalent" / "wtscli_lifecycle_sidecar.mjs"
    process = subprocess.Popen(
        [
            str(Path(node_text)),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        failed = _wait_for_status(
            status_path,
            predicate=lambda value: (
                value.get("state") == "needs_attention"
                and value.get("restartCount") == 4
            ),
        )
        assert failed["firstFailureCode"] is not None
        assert failed["reasonCode"] == "wtscli_daemon_restart_budget_exhausted"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        assert process.returncode == 0


def test_sidecar_counts_unexpected_exit_zero_against_restart_budget(
    tmp_path: Path,
) -> None:
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    env = {
        **os.environ,
        "FAKE_HEALTH_PATH": str(health_path),
        "FAKE_CHILD_SCRIPT": str(package_dir / "daemon-child.mjs"),
        "FAKE_EXIT_IMMEDIATELY": "1",
        "FAKE_EXIT_CODE": "0",
    }
    sidecar = Path(__file__).parents[1] / "src" / "seektalent" / "wtscli_lifecycle_sidecar.mjs"
    process = subprocess.Popen(
        [
            str(Path(node_text)),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        failed = _wait_for_status(
            status_path,
            predicate=lambda value: (
                value.get("state") == "needs_attention"
                and value.get("restartCount") == 4
            ),
        )
        assert failed["firstFailureCode"] == "wtscli_daemon_exit_0"
        assert failed["reasonCode"] == "wtscli_daemon_restart_budget_exhausted"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        assert process.returncode == 0


def test_sidecar_rejects_a_same_build_orphan_daemon_without_spawning_one(
    tmp_path: Path,
) -> None:
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    env = {
        **os.environ,
        "FAKE_HEALTH_PATH": str(health_path),
        "FAKE_CHILD_SCRIPT": str(package_dir / "daemon-child.mjs"),
        "FAKE_ORPHAN_DAEMON": "1",
    }
    sidecar = Path(__file__).parents[1] / "src" / "seektalent" / "wtscli_lifecycle_sidecar.mjs"
    process = subprocess.Popen(
        [
            str(Path(node_text)),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        failed = _wait_for_status(
            status_path,
            predicate=lambda value: value.get("state") == "needs_attention",
        )
        assert failed["reasonCode"] == "wtscli_daemon_existing_without_owner"
        assert failed["daemonPid"] is None
        assert failed["restartCount"] == 0
    finally:
        process.wait(timeout=5)
        assert process.returncode == 70


def test_sidecar_rejects_a_mismatched_daemon_without_killing_it(tmp_path: Path) -> None:
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    shutdown_marker = tmp_path / "shutdown-called"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    env = {
        **os.environ,
        "FAKE_HEALTH_PATH": str(health_path),
        "FAKE_CHILD_SCRIPT": str(package_dir / "daemon-child.mjs"),
        "FAKE_ORPHAN_DAEMON": "1",
        "FAKE_ORPHAN_BUILD_ID": "foreign-build",
        "FAKE_SHUTDOWN_MARKER": str(shutdown_marker),
    }
    sidecar = Path(__file__).parents[1] / "src" / "seektalent" / "wtscli_lifecycle_sidecar.mjs"
    process = subprocess.Popen(
        [
            str(Path(node_text)),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        failed = _wait_for_status(
            status_path,
            predicate=lambda value: value.get("state") == "needs_attention",
        )
        assert failed["reasonCode"] == "wtscli_foreign_owner"
        assert failed["daemonPid"] is None
        assert not health_path.exists()
        assert not shutdown_marker.exists()
        process.wait(timeout=5)
        assert process.returncode == 70
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_sidecar_top_level_failure_kills_a_child_before_releasing_ownership(
    tmp_path: Path,
) -> None:
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    env = {
        **os.environ,
        "FAKE_HEALTH_PATH": str(health_path),
        "FAKE_CHILD_SCRIPT": str(package_dir / "daemon-child.mjs"),
        "FAKE_BIND_FAIL": "1",
    }
    sidecar = Path(__file__).parents[1] / "src" / "seektalent" / "wtscli_lifecycle_sidecar.mjs"
    process = subprocess.Popen(
        [
            str(Path(node_text)),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid: int | None = None
    try:
        _wait_for_status(
            status_path,
            predicate=lambda value: value.get("state") == "needs_attention",
        )
        child_pid = json.loads(health_path.read_text(encoding="utf-8"))["status"]["pid"]
        process.wait(timeout=8)
        assert process.returncode == 70
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert child_pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_sidecar_reaps_owned_daemon_when_its_host_parent_dies(
    tmp_path: Path,
) -> None:
    if os.name == "nt" or shutil.which("sleep") is None:
        pytest.skip("the parent-death process probe is POSIX-only")
    package_dir = _write_fake_sidecar_package(tmp_path)
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "lock.json"
    health_path = tmp_path / "health.json"
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    parent = subprocess.Popen(["sleep", "30"])
    env = {
        **os.environ,
        "FAKE_HEALTH_PATH": str(health_path),
        "FAKE_CHILD_SCRIPT": str(package_dir / "daemon-child.mjs"),
    }
    sidecar = Path(__file__).parents[1] / "src" / "seektalent" / "wtscli_lifecycle_sidecar.mjs"
    process = subprocess.Popen(
        [
            str(Path(node_text)),
            str(sidecar),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
            "--bridge-build-id",
            "exact-build",
            "--parent-pid",
            str(parent.pid),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid: int | None = None
    try:
        _wait_for_status(status_path, predicate=lambda value: value.get("state") == "ready")
        child_pid = json.loads(health_path.read_text(encoding="utf-8"))["status"]["pid"]
        parent.terminate()
        parent.wait(timeout=5)
        process.wait(timeout=8)
        assert process.returncode == 0
        assert json.loads(status_path.read_text(encoding="utf-8"))["state"] == "stopped"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert child_pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


class _NoopRuntime:
    def __init__(
        self,
        _settings: AppSettings,
        *,
        source_operation_executor: object | None = None,
        wtscli_lifecycle_supervisor: object | None = None,
    ) -> None:
        self.source_operation_executor = source_operation_executor
        self.wtscli_lifecycle_supervisor = wtscli_lifecycle_supervisor

    def extract_requirements(self, **_kwargs: object) -> object:
        raise AssertionError("requirement extraction is not part of lifecycle startup")


def test_create_app_lifespan_starts_and_stops_the_single_supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeSupervisor:
        def start(self) -> None:
            events.append("start")

        def shutdown(self) -> None:
            events.append("shutdown")

        def health_snapshot(self) -> dict[str, object]:
            return {
                "component": "wtscli_lifecycle_supervisor",
                "status": "ready",
                "state": "ready",
                "processHealthy": True,
                "extensionConnected": True,
            }

    fake = FakeSupervisor()
    monkeypatch.setattr(
        "seektalent_ui.server.build_wtscli_lifecycle_supervisor",
        lambda _settings: fake,
    )
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="opencli",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )

    with TestClient(create_app(settings=settings, runtime_factory=_NoopRuntime)):
        assert events == ["start"]
    assert events == ["start", "shutdown"]
