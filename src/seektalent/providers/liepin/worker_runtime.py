from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import request

from pydantic import ValidationError

from seektalent.config import AppSettings
from seektalent.providers.liepin.worker_contracts import (
    LiepinWorkerModeError,
    decode_redacted_diagnostics,
    decode_worker_health,
)


EventCallback = Callable[[str, dict[str, object]], None]
HttpGet = Callable[[str], dict[str, object]]

_DEFAULT_BUN = object()


@dataclass(frozen=True)
class LiepinWorkerRuntimeHandle:
    internal_base_url: str
    host: str
    port: int


class ManagedLiepinWorkerRuntime:
    _shared: dict[tuple[str, int, str], "ManagedLiepinWorkerRuntime"] = {}

    def __init__(
        self,
        settings: AppSettings,
        *,
        worker_package_dir: Path | None = None,
        bun_executable: str | None | object = _DEFAULT_BUN,
        process_factory: Callable[..., Any] | None = None,
        http_get: Callable[..., dict[str, object]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.worker_package_dir = worker_package_dir or settings.project_root / "apps" / "liepin-worker"
        self.bun_executable = shutil.which("bun") if bun_executable is _DEFAULT_BUN else bun_executable
        self.process_factory = process_factory or subprocess.Popen
        self.http_get = http_get or _default_http_get
        self.monotonic = monotonic
        self.sleep = sleep
        self._process: Any | None = None
        self._handle: LiepinWorkerRuntimeHandle | None = None
        atexit.register(self.stop)

    @classmethod
    def shared(
        cls,
        settings: AppSettings,
        *,
        worker_package_dir: Path | None = None,
        bun_executable: str | None | object = _DEFAULT_BUN,
        process_factory: Callable[..., Any] | None = None,
        http_get: Callable[..., dict[str, object]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> "ManagedLiepinWorkerRuntime":
        package_dir = worker_package_dir or settings.project_root / "apps" / "liepin-worker"
        key = (settings.liepin_worker_host, settings.liepin_worker_port, str(package_dir.resolve()))
        runtime = cls._shared.get(key)
        if runtime is None:
            runtime = cls(
                settings,
                worker_package_dir=package_dir,
                bun_executable=bun_executable,
                process_factory=process_factory,
                http_get=http_get,
                monotonic=monotonic,
                sleep=sleep,
            )
            cls._shared[key] = runtime
        return runtime

    @classmethod
    def reset_shared(cls) -> None:
        for runtime in cls._shared.values():
            runtime.stop()
        cls._shared.clear()

    def ensure_started(self, *, on_event: EventCallback | None = None) -> LiepinWorkerRuntimeHandle:
        if self._handle is not None and self._process_is_running():
            return self._handle

        self._validate_prerequisites()
        session_store_key = _require_session_store_key()
        host = self.settings.liepin_worker_host
        port = self._resolve_port(host)
        base_url = f"http://{host}:{port}"
        command = [
            str(self.bun_executable),
            "run",
            "start",
            "--host",
            host,
            "--port",
            str(port),
        ]
        env = {
            **os.environ,
            "SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN": self.settings.liepin_api_token,
            "SEEKTALENT_LIEPIN_WORKER_HOST": host,
            "SEEKTALENT_LIEPIN_WORKER_PORT": str(port),
            "SEEKTALENT_LIEPIN_SESSION_STORE_DIR": str(
                self.settings.resolve_workspace_path(self.settings.liepin_session_store_dir)
            ),
            "SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID": self.settings.liepin_session_store_key_id,
            "SEEKTALENT_LIEPIN_SESSION_STORE_KEY": session_store_key,
        }
        self._process = self.process_factory(
            command,
            cwd=str(self.worker_package_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        try:
            self._wait_for_health(base_url=base_url, on_event=on_event)
        except LiepinWorkerModeError:
            self.stop()
            raise
        self._handle = LiepinWorkerRuntimeHandle(internal_base_url=base_url, host=host, port=port)
        return self._handle

    def stop(self) -> None:
        process = self._process
        self._process = None
        self._handle = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def _validate_prerequisites(self) -> None:
        if not self.bun_executable:
            raise LiepinWorkerModeError(
                "Missing Bun executable required for liepin managed_local worker.",
                setup_status="missing_bun",
            )
        if not self.worker_package_dir.exists() or not (self.worker_package_dir / "package.json").exists():
            raise LiepinWorkerModeError(
                f"Missing Liepin worker package at {self.worker_package_dir}.",
                setup_status="missing_worker_package",
            )

    def _resolve_port(self, host: str) -> int:
        configured_port = self.settings.liepin_worker_port
        if configured_port == 0:
            return _choose_free_port(host)
        if not _port_is_available(host, configured_port):
            raise LiepinWorkerModeError(
                f"Liepin managed_local worker port {configured_port} is already in use.",
                setup_status="port_unavailable",
            )
        return configured_port

    def _wait_for_health(self, *, base_url: str, on_event: EventCallback | None) -> None:
        deadline = self.monotonic() + self.settings.liepin_worker_startup_timeout_seconds
        while True:
            self._raise_if_process_failed(on_event=on_event)
            if self._health_is_ok(base_url):
                return

            if self.monotonic() >= deadline:
                payload = {"mode": "managed_local", "setup_status": "timeout"}
                if on_event is not None:
                    on_event("worker_start_timeout", payload)
                raise LiepinWorkerModeError("worker_start_timeout", setup_status="timeout")
            self.sleep(0.05)

    def _health_is_ok(self, base_url: str) -> bool:
        try:
            health = decode_worker_health(
                self.http_get(
                    f"{base_url}/internal/health",
                    headers={"Authorization": f"Bearer {self.settings.liepin_api_token}"},
                    timeout=self.settings.liepin_worker_timeout_seconds,
                )
            )
        except (OSError, TimeoutError, ValidationError, ValueError):
            return False
        return health.status == "ok"

    def _raise_if_process_failed(self, *, on_event: EventCallback | None) -> None:
        if self._process is None:
            return
        returncode = self._process.poll()
        if returncode is None:
            return
        diagnostics = decode_redacted_diagnostics(
            {
                "code": "worker_failed",
                "message": f"Liepin worker exited with status {returncode}.",
                "stdout": "[redacted]",
                "stderr": "[redacted]",
            }
        ).model_dump()
        payload = {"mode": "managed_local", "setup_status": "worker_failed", "diagnostics": diagnostics}
        if on_event is not None:
            on_event("worker_failed", payload)
        raise LiepinWorkerModeError("worker_failed", setup_status="worker_failed")

    def _process_is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None


def _choose_free_port(host: str) -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _require_session_store_key() -> str:
    session_store_key = os.environ.get("SEEKTALENT_LIEPIN_SESSION_STORE_KEY")
    if not session_store_key:
        raise LiepinWorkerModeError(
            "Missing SEEKTALENT_LIEPIN_SESSION_STORE_KEY required for liepin managed_local session store.",
            setup_status="missing_session_store_key",
        )
    return session_store_key


def _default_http_get(url: str, *, headers: dict[str, str], timeout: float) -> dict[str, object]:
    req = request.Request(url, headers=headers)
    with request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("Liepin worker response must be a JSON object")
    return decoded
