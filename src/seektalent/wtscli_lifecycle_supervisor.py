"""The single SeekTalent-owned WTSCLI lifecycle supervisor."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from seektalent.config import AppSettings
from seektalent.opencli_browser.daemon_process import (
    connect_existing_opencli_daemon_read_only,
)
from seektalent.wtscli_runtime import (
    BootstrapError,
    WtsCliRuntime,
    ensure_wtscli_runtime,
    inspect_wtscli_runtime,
    runtime_requirement,
    wtscli_subprocess_env,
)


WTSCLI_SUPERVISOR_READY_TIMEOUT_SECONDS = 40.0
WTSCLI_SUPERVISOR_HEARTBEAT_TIMEOUT_SECONDS = 5.0
WTSCLI_SUPERVISOR_WATCHDOG_INTERVAL_SECONDS = 0.5
WTSCLI_SUPERVISOR_RESTART_BUDGET = 3
_SUPERVISOR_STATUS_FILENAME = "seektalent-wtscli-supervisor-status.json"
_SUPERVISOR_LOCK_FILENAME = "seektalent-wtscli-supervisor.lock"
_SUPERVISOR_CONTROL_FILENAME = "seektalent-wtscli-supervisor.control.json"
_SUPERVISOR_STATUS_SCHEMA = "seektalent.wtscli_supervisor_status.v1"
_SUPERVISOR_LOCK_SCHEMA = "seektalent.wtscli_supervisor_lock.v1"
_SIDECAR_FILENAME = "wtscli_lifecycle_sidecar.mjs"


LifecycleState = Literal[
    "stopped",
    "warming",
    "ready",
    "extension_not_connected",
    "profile_not_connected",
    "needs_attention",
]


class WtsCliLifecycleError(RuntimeError):
    def __init__(self, safe_reason_code: str) -> None:
        super().__init__(safe_reason_code)
        self.safe_reason_code = safe_reason_code


@dataclass(frozen=True, slots=True)
class WtsCliLifecycleStatus:
    state: LifecycleState
    bridge_build_id: str | None
    supervisor_pid: int | None
    daemon_pid: int | None
    daemon_owned: bool
    process_healthy: bool
    extension_connected: bool
    restart_count: int
    first_failure_code: str | None
    reason_code: str | None
    observed_at: str | None
    supervisor_restart_count: int = 0
    supervisor_first_failure_code: str | None = None

    @classmethod
    def from_payload(cls, payload: object) -> WtsCliLifecycleStatus | None:
        if not isinstance(payload, dict):
            return None
        if not all(isinstance(key, str) for key in payload):
            return None
        payload = {key: value for key, value in payload.items()}
        if payload.get("schemaVersion") != _SUPERVISOR_STATUS_SCHEMA:
            return None
        state = _lifecycle_state(payload.get("state"))
        if state is None:
            return None
        supervisor_pid = _positive_int_or_none(payload.get("supervisorPid"))
        daemon_pid = _positive_int_or_none(payload.get("daemonPid"))
        restart_count = payload.get("restartCount")
        supervisor_restart_count = payload.get("supervisorRestartCount", 0)
        if (
            type(restart_count) is not int
            or restart_count < 0
            or type(supervisor_restart_count) is not int
            or supervisor_restart_count < 0
        ):
            return None
        return cls(
            state=state,
            bridge_build_id=_string_or_none(payload.get("bridgeBuildId")),
            supervisor_pid=supervisor_pid,
            daemon_pid=daemon_pid,
            daemon_owned=payload.get("daemonOwned") is True,
            process_healthy=payload.get("processHealthy") is True,
            extension_connected=payload.get("extensionConnected") is True,
            restart_count=restart_count,
            first_failure_code=_string_or_none(payload.get("firstFailureCode")),
            reason_code=_string_or_none(payload.get("reasonCode")),
            observed_at=_string_or_none(payload.get("observedAt")),
            supervisor_restart_count=supervisor_restart_count,
            supervisor_first_failure_code=_string_or_none(
                payload.get("supervisorFirstFailureCode")
            ),
        )


ProcessFactory = Callable[..., subprocess.Popen[bytes]]


class WtsCliLifecycleSupervisor:
    """Own one foreground sidecar for the whole application lifetime.

    The Node sidecar is the only production code that starts or stops the
    WTSCLI daemon. This object owns the sidecar process handle, supervises its
    heartbeat, and exposes only read-only daemon connections to browser code.
    """

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        runtime: WtsCliRuntime | None = None,
        allow_start: bool = True,
        process_factory: ProcessFactory | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        sidecar_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._allow_start = allow_start
        self._process_factory = process_factory or subprocess.Popen
        self._monotonic_clock = monotonic_clock
        self._sleep = sleep
        self._sidecar_path = sidecar_path or Path(__file__).with_name(_SIDECAR_FILENAME)
        self._process: subprocess.Popen[bytes] | None = None
        self._windows_job: _WindowsJob | None = None
        self._started = False
        self._readonly_attach = False
        self._startup_error: str | None = None
        self._status_path: Path | None = None
        self._lock_path: Path | None = None
        self._control_path: Path | None = None
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._lifecycle_id = uuid.uuid4().hex
        self._started_at = 0.0
        self._sidecar_restart_count = 0
        self._sidecar_first_failure_code: str | None = None
        self._watchdog_failure_reason: str | None = None

    @classmethod
    def attach(cls, settings: AppSettings) -> WtsCliLifecycleSupervisor:
        """Create a read-only observer for a running application owner.

        This method never creates, adopts, or shuts down an owner. Production
        application composition must use :meth:`start`; this observer exists
        only for subordinate diagnostics and compatibility at a read-only
        boundary.
        """
        runtime = inspect_wtscli_runtime()
        supervisor = cls(settings, runtime=runtime, allow_start=False)
        supervisor._verify_bundle(runtime)
        supervisor._configure_paths(runtime)
        supervisor._readonly_attach = True
        supervisor._started = True
        return supervisor

    @property
    def runtime(self) -> WtsCliRuntime:
        if self._runtime is None:
            raise WtsCliLifecycleError("wtscli_supervisor_not_started")
        return self._runtime

    @property
    def status_path(self) -> Path | None:
        return self._status_path

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
            if not self._allow_start:
                raise WtsCliLifecycleError("wtscli_supervisor_start_forbidden")
            runtime = self._runtime or ensure_wtscli_runtime()
            self._verify_bundle(runtime)
            self._runtime = runtime
            self._configure_paths(runtime)
            owner = self._read_owner_lock()
            if owner is not None:
                if _process_alive(owner.get("supervisorPid")):
                    raise WtsCliLifecycleError("wtscli_supervisor_foreign_owner")
                self._remove_stale_owner_lock()
            self._sidecar_restart_count = 0
            self._sidecar_first_failure_code = None
            self._watchdog_failure_reason = None
            self._lifecycle_id = uuid.uuid4().hex
            self._watchdog_stop.clear()
            try:
                self._spawn_sidecar()
                self._started = True
                self._started_at = self._monotonic_clock()
                self._wait_for_sidecar_owner()
            except (OSError, RuntimeError, ValueError):
                process = self._process
                self._process = None
                self._started = False
                if process is not None:
                    self._stop_process(process, request_control=False)
                raise
            self._start_watchdog()

    def record_startup_failure(self, error: Exception) -> None:
        if isinstance(error, WtsCliLifecycleError):
            self._startup_error = error.safe_reason_code
        elif isinstance(error, BootstrapError):
            self._startup_error = _bootstrap_reason(error)
        else:
            self._startup_error = "wtscli_supervisor_start_failed"

    def shutdown(self) -> None:
        with self._state_lock:
            if not self._started and self._process is None:
                return
            self._watchdog_stop.set()
            watchdog = self._watchdog_thread
            self._watchdog_thread = None
            process = self._process
            self._process = None
            self._started = False
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=2)
        if process is not None:
            self._stop_process(process, request_control=not self._readonly_attach)
        self._close_windows_job()

    def ensure_ready(
        self,
        *,
        timeout_seconds: float = WTSCLI_SUPERVISOR_READY_TIMEOUT_SECONDS,
    ) -> WtsCliLifecycleStatus:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self._startup_error is not None:
            raise WtsCliLifecycleError(self._startup_error)
        if not self._started:
            raise WtsCliLifecycleError("wtscli_supervisor_not_started")
        deadline = self._monotonic_clock() + timeout_seconds
        last_status = self.status()
        while self._monotonic_clock() < deadline:
            if last_status.state == "ready":
                return last_status
            if last_status.state == "needs_attention":
                raise WtsCliLifecycleError(
                    last_status.reason_code or "wtscli_supervisor_needs_attention"
                )
            self._sleep(min(0.2, max(0.01, deadline - self._monotonic_clock())))
            last_status = self.status()
        raise WtsCliLifecycleError(_timeout_reason(last_status))

    def connect_existing(
        self,
        *,
        context_id: str | None = None,
        verify_timeout_seconds: float = 0.3,
    ):
        """Return a read-only client; this method never starts or repairs."""
        if not self._started:
            raise WtsCliLifecycleError("wtscli_supervisor_not_started")
        return connect_existing_opencli_daemon_read_only(
            self.runtime,
            context_id=context_id,
            verify_timeout_seconds=verify_timeout_seconds,
        )

    def status(self) -> WtsCliLifecycleStatus:
        status = self._read_status()
        if self._readonly_attach:
            return self._observer_status(status)
        if self._watchdog_failure_reason is not None:
            return self._attention_status(
                status,
                self._watchdog_failure_reason,
            )
        if status is not None and self._started:
            process = self._process
            if process is not None and process.poll() is not None:
                return self._attention_status(status, "wtscli_supervisor_exited")
            if _status_is_stale(status):
                return self._attention_status(status, "wtscli_supervisor_heartbeat_stale")
        if status is not None:
            return status
        if self._startup_error is not None:
            return self._attention_status(None, self._startup_error)
        if self._started:
            return WtsCliLifecycleStatus(
                state="warming",
                bridge_build_id=(
                    runtime_requirement(self._runtime).bridge_build_id
                    if self._runtime is not None
                    else None
                ),
                supervisor_pid=(
                    self._process.pid
                    if self._process is not None and self._process.poll() is None
                    else None
                ),
                daemon_pid=None,
                daemon_owned=True,
                process_healthy=False,
                extension_connected=False,
                restart_count=0,
                first_failure_code=None,
                reason_code=None,
                observed_at=None,
                supervisor_restart_count=self._sidecar_restart_count,
                supervisor_first_failure_code=self._sidecar_first_failure_code,
            )
        return WtsCliLifecycleStatus(
            state="stopped",
            bridge_build_id=None,
            supervisor_pid=None,
            daemon_pid=None,
            daemon_owned=False,
            process_healthy=False,
            extension_connected=False,
            restart_count=0,
            first_failure_code=None,
            reason_code=None,
            observed_at=None,
        )

    def health_snapshot(self) -> dict[str, object]:
        status = self.status()
        return {
            "component": "wtscli_lifecycle_supervisor",
            "status": "ready" if status.state == "ready" else "not_ready",
            "state": status.state,
            "processHealthy": status.process_healthy,
            "extensionConnected": status.extension_connected,
            "daemonPid": status.daemon_pid,
            "supervisorPid": status.supervisor_pid,
            "daemonOwned": status.daemon_owned,
            "restartCount": status.restart_count,
            "firstFailureCode": status.first_failure_code,
            "reasonCode": status.reason_code,
            "bridgeBuildId": status.bridge_build_id,
            "supervisorRestartCount": status.supervisor_restart_count,
            "supervisorFirstFailureCode": status.supervisor_first_failure_code,
        }

    def _verify_bundle(self, runtime: WtsCliRuntime) -> None:
        from seektalent.providers.liepin.browser_environment import (
            check_installed_browser_bridge_bundle,
        )

        manifest = runtime.bridge_manifest
        if manifest is None:
            raise WtsCliLifecycleError("wtscli_bundle_missing")
        status = check_installed_browser_bridge_bundle(
            install_root=manifest.parent.parent,
        )
        if not status.ok:
            raise WtsCliLifecycleError(status.reason_code)

    def _configure_paths(self, runtime: WtsCliRuntime) -> None:
        requirement = runtime_requirement(runtime)
        state_root = requirement.runtime_identity.state.resolve_root()
        state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._status_path = state_root / _SUPERVISOR_STATUS_FILENAME
        self._lock_path = state_root / _SUPERVISOR_LOCK_FILENAME
        self._control_path = state_root / _SUPERVISOR_CONTROL_FILENAME

    def _spawn_sidecar(self) -> None:
        if not self._sidecar_path.is_file():
            raise WtsCliLifecycleError("wtscli_supervisor_sidecar_missing")
        runtime = self.runtime
        requirement = runtime_requirement(runtime)
        package_dir = runtime.wtscli_main.parents[2]
        assert self._status_path is not None
        assert self._lock_path is not None
        if self._control_path is None:
            self._control_path = self._lock_path.with_name(_SUPERVISOR_CONTROL_FILENAME)
        env = wtscli_subprocess_env(
            node_bin_dir=runtime.node_bin_dir,
            requirement=requirement,
        )
        env["WTSCLI_DAEMON_PORT"] = str(requirement.runtime_identity.endpoint.port)
        command = [
            str(runtime.node),
            str(self._sidecar_path),
            "--package-dir",
            str(package_dir),
            "--status-path",
            str(self._status_path),
            "--lock-path",
            str(self._lock_path),
            "--control-path",
            str(self._control_path),
            "--bridge-build-id",
            requirement.bridge_build_id,
            "--parent-pid",
            str(os.getpid()),
            "--lifecycle-id",
            self._lifecycle_id,
            "--supervisor-restart-count",
            str(self._sidecar_restart_count),
        ]
        if self._sidecar_first_failure_code is not None:
            command.extend(
                [
                    "--supervisor-first-failure-code",
                    self._sidecar_first_failure_code,
                ]
            )
        self._process = self._process_factory(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=package_dir,
            env=env,
            start_new_session=os.name != "nt",
        )
        self._windows_job = _WindowsJob(self._process)

    def _wait_for_sidecar_owner(self) -> None:
        assert self._process is not None
        deadline = self._monotonic_clock() + 2.0
        while self._monotonic_clock() < deadline:
            owner = self._read_owner_lock()
            if owner is not None:
                owner_pid = owner.get("supervisorPid")
                if owner_pid == self._process.pid:
                    return
                if _process_alive(owner_pid):
                    raise WtsCliLifecycleError("wtscli_supervisor_foreign_owner")
                self._remove_stale_owner_lock()
            if self._process.poll() is not None:
                status = self._read_status()
                raise WtsCliLifecycleError(
                    (
                        status.reason_code
                        if status is not None
                        and status.state == "needs_attention"
                        and status.reason_code
                        else "wtscli_supervisor_exited"
                    )
                )
            self._sleep(0.05)
        raise WtsCliLifecycleError("wtscli_supervisor_owner_timeout")

    def _start_watchdog(self) -> None:
        if self._readonly_attach or self._watchdog_thread is not None:
            return
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="seektalent-wtscli-supervisor-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(WTSCLI_SUPERVISOR_WATCHDOG_INTERVAL_SECONDS):
            with self._state_lock:
                if not self._started or self._readonly_attach:
                    return
                process = self._process
            status = self._read_status()
            if process is None:
                self._recover_sidecar("wtscli_supervisor_missing")
            elif process.poll() is not None:
                self._recover_sidecar("wtscli_supervisor_exited")
            elif status is not None and _status_is_stale(status):
                self._recover_sidecar("wtscli_supervisor_heartbeat_stale")
            elif status is None and self._monotonic_clock() - self._started_at > 5.0:
                self._recover_sidecar("wtscli_supervisor_heartbeat_missing")

    def _recover_sidecar(self, reason_code: str) -> None:
        with self._state_lock:
            if not self._started or self._readonly_attach:
                return
            if self._sidecar_restart_count > WTSCLI_SUPERVISOR_RESTART_BUDGET:
                self._watchdog_failure_reason = (
                    "wtscli_supervisor_restart_budget_exhausted"
                )
                return
            process = self._process
            self._process = None
            self._sidecar_restart_count += 1
            if self._sidecar_first_failure_code is None:
                self._sidecar_first_failure_code = reason_code
            self._watchdog_failure_reason = reason_code
        if process is not None:
            self._stop_process(process, request_control=False)
        failed_process: subprocess.Popen[bytes] | None = None
        with self._state_lock:
            if not self._started or self._watchdog_stop.is_set():
                return
            if self._sidecar_restart_count > WTSCLI_SUPERVISOR_RESTART_BUDGET:
                self._watchdog_failure_reason = (
                    "wtscli_supervisor_restart_budget_exhausted"
                )
                return
            try:
                self._remove_stale_owner_lock()
                self._spawn_sidecar()
                self._started_at = self._monotonic_clock()
                self._wait_for_sidecar_owner()
                self._watchdog_failure_reason = None
            except (OSError, RuntimeError, ValueError) as exc:
                failed_process = self._process
                self._process = None
                self._watchdog_failure_reason = _safe_supervisor_reason(exc)
        if failed_process is not None:
            self._stop_process(failed_process, request_control=False)

    def _observer_status(
        self,
        status: WtsCliLifecycleStatus | None,
    ) -> WtsCliLifecycleStatus:
        owner = self._read_owner_lock()
        expected = (
            runtime_requirement(self.runtime).bridge_build_id
            if self._runtime is not None
            else None
        )
        if (
            status is None
            or owner is None
            or owner.get("bridgeBuildId") != expected
            or owner.get("supervisorPid") != status.supervisor_pid
            or not _process_alive(owner.get("supervisorPid"))
        ):
            return self._attention_status(status, "wtscli_supervisor_lost")
        if _status_is_stale(status):
            return self._attention_status(status, "wtscli_supervisor_heartbeat_stale")
        return status

    def _attention_status(
        self,
        status: WtsCliLifecycleStatus | None,
        reason_code: str,
    ) -> WtsCliLifecycleStatus:
        return WtsCliLifecycleStatus(
            state="needs_attention",
            bridge_build_id=status.bridge_build_id if status is not None else None,
            supervisor_pid=status.supervisor_pid if status is not None else None,
            daemon_pid=status.daemon_pid if status is not None else None,
            daemon_owned=False,
            process_healthy=False,
            extension_connected=False,
            restart_count=status.restart_count if status is not None else 0,
            first_failure_code=(
                status.first_failure_code if status is not None else None
            ),
            reason_code=reason_code,
            observed_at=status.observed_at if status is not None else None,
            supervisor_restart_count=(
                status.supervisor_restart_count
                if status is not None
                else self._sidecar_restart_count
            ),
            supervisor_first_failure_code=(
                status.supervisor_first_failure_code
                if status is not None
                else self._sidecar_first_failure_code
            ),
        )

    def _stop_process(
        self,
        process: subprocess.Popen[bytes] | None,
        *,
        request_control: bool,
    ) -> None:
        if process is None:
            self._close_windows_job()
            return
        if process.poll() is None and request_control:
            self._request_sidecar_shutdown()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        if process.poll() is None:
            with suppress(OSError):
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_process_group(process)
                with suppress(OSError):
                    process.kill()
                with suppress(subprocess.TimeoutExpired, OSError):
                    process.wait(timeout=5)
        self._close_windows_job()

    def _request_sidecar_shutdown(self) -> None:
        if self._control_path is None:
            return
        payload = {
            "schemaVersion": "seektalent.wtscli_supervisor_control.v1",
            "command": "shutdown",
            "lifecycleId": self._lifecycle_id,
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        }
        temporary = self._control_path.with_name(
            f"{self._control_path.name}.tmp-{os.getpid()}"
        )
        try:
            temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            os.replace(temporary, self._control_path)
        except OSError:
            with suppress(OSError):
                temporary.unlink()

    def _close_windows_job(self) -> None:
        if self._windows_job is not None:
            self._windows_job.close()
            self._windows_job = None

    def _read_owner_lock(self) -> dict[str, object] | None:
        if self._lock_path is None:
            return None
        if not self._lock_path.exists():
            return None
        try:
            payload = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WtsCliLifecycleError("wtscli_supervisor_foreign_owner") from exc
        if not isinstance(payload, dict):
            raise WtsCliLifecycleError("wtscli_supervisor_foreign_owner")
        if payload.get("schemaVersion") != _SUPERVISOR_LOCK_SCHEMA:
            raise WtsCliLifecycleError("wtscli_supervisor_foreign_owner")
        return payload

    def _remove_stale_owner_lock(self) -> None:
        if self._lock_path is None or not self._lock_path.exists():
            return
        owner = self._read_owner_lock()
        if owner is not None and _process_alive(owner.get("supervisorPid")):
            raise WtsCliLifecycleError("wtscli_supervisor_foreign_owner")
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise WtsCliLifecycleError("wtscli_supervisor_lock_unavailable") from exc

    def _read_status(self) -> WtsCliLifecycleStatus | None:
        if self._status_path is None or not self._status_path.is_file():
            return None
        try:
            payload = json.loads(self._status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return WtsCliLifecycleStatus.from_payload(payload)


def build_wtscli_lifecycle_supervisor(settings: AppSettings) -> WtsCliLifecycleSupervisor:
    return WtsCliLifecycleSupervisor(settings)


class _WindowsJob:
    """Kill-on-close containment for the sidecar and its daemon descendants."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._handle: int | None = None
        if os.name != "nt":
            return
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            return
        try:
            import ctypes

            class _BasicLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("per_process_user_time_limit", ctypes.c_longlong),
                    ("per_job_user_time_limit", ctypes.c_longlong),
                    ("limit_flags", ctypes.c_uint32),
                    ("minimum_working_set_size", ctypes.c_size_t),
                    ("maximum_working_set_size", ctypes.c_size_t),
                    ("active_process_limit", ctypes.c_uint32),
                    ("affinity", ctypes.c_void_p),
                    ("priority_class", ctypes.c_uint32),
                    ("scheduling_class", ctypes.c_uint32),
                ]

            class _IoCounters(ctypes.Structure):
                _fields_ = [
                    ("read_operation_count", ctypes.c_ulonglong),
                    ("write_operation_count", ctypes.c_ulonglong),
                    ("other_operation_count", ctypes.c_ulonglong),
                    ("read_transfer_count", ctypes.c_ulonglong),
                    ("write_transfer_count", ctypes.c_ulonglong),
                    ("other_transfer_count", ctypes.c_ulonglong),
                ]

            class _ExtendedLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("basic", _BasicLimitInformation),
                    ("io", _IoCounters),
                    ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t),
                    ("peak_job_memory", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            information = _ExtendedLimitInformation()
            information.basic.limit_flags = 0x2000
            if not kernel32.SetInformationJobObject(
                handle,
                9,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ) or not kernel32.AssignProcessToJobObject(handle, process_handle):
                kernel32.CloseHandle(handle)
                return
            self._handle = int(handle)
        except (OSError, AttributeError, TypeError):
            self._handle = None

    def close(self) -> None:
        if self._handle is None or os.name != "nt":
            return
        with suppress(OSError):
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
        self._handle = None


def _process_alive(pid: object) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        return
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return
    with suppress(OSError):
        os.killpg(pid, signal.SIGKILL)


def _status_is_stale(status: WtsCliLifecycleStatus) -> bool:
    if status.observed_at is None:
        return True
    try:
        observed = datetime.fromisoformat(status.observed_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (
        datetime.now(timezone.utc) - observed
    ).total_seconds() > WTSCLI_SUPERVISOR_HEARTBEAT_TIMEOUT_SECONDS


def _positive_int_or_none(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _lifecycle_state(value: object) -> LifecycleState | None:
    if value == "stopped":
        return "stopped"
    if value == "warming":
        return "warming"
    if value == "ready":
        return "ready"
    if value == "extension_not_connected":
        return "extension_not_connected"
    if value == "profile_not_connected":
        return "profile_not_connected"
    if value == "needs_attention":
        return "needs_attention"
    return None


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bootstrap_reason(error: BootstrapError) -> str:
    message = str(error)
    if message.startswith("domi_node_missing"):
        return "wtscli_node_missing"
    if message.startswith("opencli_offline_runtime_missing"):
        return "wtscli_bundle_missing"
    if "build_mismatch" in message:
        return "wtscli_runtime_build_mismatch"
    return "wtscli_bundle_integrity_failed"


def _safe_supervisor_reason(error: Exception) -> str:
    if isinstance(error, WtsCliLifecycleError):
        return error.safe_reason_code
    return "wtscli_supervisor_restart_failed"


def _timeout_reason(status: WtsCliLifecycleStatus) -> str:
    if status.state == "extension_not_connected":
        return "opencli_extension_disconnected"
    if status.state == "profile_not_connected":
        return "wtscli_profile_disconnected"
    if status.reason_code:
        return status.reason_code
    return "wtscli_readiness_timeout"


__all__ = [
    "WTSCLI_SUPERVISOR_READY_TIMEOUT_SECONDS",
    "WTSCLI_SUPERVISOR_HEARTBEAT_TIMEOUT_SECONDS",
    "WtsCliLifecycleError",
    "WtsCliLifecycleStatus",
    "WtsCliLifecycleSupervisor",
    "build_wtscli_lifecycle_supervisor",
]
