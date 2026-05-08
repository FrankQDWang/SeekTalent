from __future__ import annotations

import subprocess
from pathlib import Path
from socket import socket
from typing import Any

import pytest

from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.providers.liepin.worker_runtime import ManagedLiepinWorkerRuntime
from tests.settings_factory import make_settings


class RecordingProcess:
    def __init__(self, *, returncode: int | None = None, stdout_text: str = "", stderr_text: str = "") -> None:
        self.returncode = returncode
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return 0 if self.returncode is None else self.returncode


class ProcessFactory:
    def __init__(self, process: RecordingProcess | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.process = process or RecordingProcess()

    def __call__(self, command: list[str], **kwargs: Any) -> RecordingProcess:
        self.calls.append({"command": command, **kwargs})
        return self.process


class SequencedProcessFactory:
    def __init__(self, processes: list[RecordingProcess]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.processes = processes

    def __call__(self, command: list[str], **kwargs: Any) -> RecordingProcess:
        self.calls.append({"command": command, **kwargs})
        return self.processes[len(self.calls) - 1]


class HttpGet:
    def __init__(self, *responses: dict[str, object] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, *, headers: dict[str, str], timeout: float) -> dict[str, object]:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        response = self.responses.pop(0) if self.responses else {"status": "ok", "workerVersion": "test-worker"}
        if isinstance(response, Exception):
            raise response
        return response


def _package_dir(tmp_path: Path) -> Path:
    package_dir = tmp_path / "worker"
    package_dir.mkdir(exist_ok=True)
    (package_dir / "package.json").write_text('{"scripts":{"start":"bun run server.ts"}}\n', encoding="utf-8")
    return package_dir


def test_managed_local_worker_starts_bun_selects_port_and_waits_for_health(tmp_path: Path) -> None:
    settings = make_settings(
        liepin_worker_mode="managed_local",
        liepin_worker_port=0,
        liepin_api_token="worker-token",
    )
    process_factory = ProcessFactory()
    http_get = HttpGet({"status": "ok", "workerVersion": "test-worker"})
    runtime = ManagedLiepinWorkerRuntime(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=process_factory,
        http_get=http_get,
        sleep=lambda _: None,
    )

    handle = runtime.ensure_started()

    assert handle.internal_base_url.startswith("http://127.0.0.1:")
    assert handle.port > 0
    assert process_factory.calls[0]["command"][0] == "/usr/local/bin/bun"
    assert process_factory.calls[0]["stdout"] is subprocess.DEVNULL
    assert process_factory.calls[0]["stderr"] is subprocess.DEVNULL
    assert process_factory.calls[0]["env"]["SEEKTALENT_LIEPIN_WORKER_AUTH_TOKEN"] == "worker-token"
    assert http_get.calls == [
        {
            "url": f"{handle.internal_base_url}/internal/health",
            "headers": {"Authorization": "Bearer worker-token"},
            "timeout": settings.liepin_worker_timeout_seconds,
        }
    ]


def test_managed_local_worker_passes_session_store_env_without_overwriting_key_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_SESSION_STORE_KEY", "inherited-secret-key")
    settings = make_settings(
        liepin_worker_mode="managed_local",
        liepin_session_store_dir=str(tmp_path / "sessions"),
        liepin_session_store_key_id="managed-key-id",
    )
    process_factory = ProcessFactory()
    runtime = ManagedLiepinWorkerRuntime(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=process_factory,
        http_get=HttpGet({"status": "ok", "workerVersion": "test-worker"}),
        sleep=lambda _: None,
    )

    runtime.ensure_started()

    env = process_factory.calls[0]["env"]
    assert env["SEEKTALENT_LIEPIN_SESSION_STORE_DIR"] == str(tmp_path / "sessions")
    assert env["SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID"] == "managed-key-id"
    assert env["SEEKTALENT_LIEPIN_SESSION_STORE_KEY"] == "inherited-secret-key"


def test_managed_local_runtime_is_reused_within_process(tmp_path: Path) -> None:
    settings = make_settings(liepin_worker_mode="managed_local", liepin_worker_port=0)
    process_factory = ProcessFactory()
    http_get = HttpGet({"status": "ok", "workerVersion": "test-worker"})

    first = ManagedLiepinWorkerRuntime.shared(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=process_factory,
        http_get=http_get,
        sleep=lambda _: None,
    )
    second = ManagedLiepinWorkerRuntime.shared(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=process_factory,
        http_get=http_get,
        sleep=lambda _: None,
    )

    first.ensure_started()
    second.ensure_started()

    assert first is second
    assert len(process_factory.calls) == 1


def test_startup_timeout_records_domain_event_before_search_dispatch(tmp_path: Path) -> None:
    settings = make_settings(
        liepin_worker_mode="managed_local",
        liepin_worker_startup_timeout_seconds=0.1,
    )
    events: list[tuple[str, dict[str, object]]] = []
    runtime = ManagedLiepinWorkerRuntime(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=ProcessFactory(),
        http_get=HttpGet({"status": "starting"}),
        monotonic=iter([0.0, 0.2]).__next__,
        sleep=lambda _: None,
    )

    with pytest.raises(LiepinWorkerModeError, match="worker_start_timeout"):
        runtime.ensure_started(on_event=lambda name, payload: events.append((name, payload)))

    assert events == [("worker_start_timeout", {"mode": "managed_local", "setup_status": "timeout"})]


def test_startup_timeout_stops_worker_and_retry_starts_fresh_process(tmp_path: Path) -> None:
    settings = make_settings(
        liepin_worker_mode="managed_local",
        liepin_worker_startup_timeout_seconds=0.1,
    )
    first_process = RecordingProcess()
    second_process = RecordingProcess()
    process_factory = SequencedProcessFactory([first_process, second_process])
    runtime = ManagedLiepinWorkerRuntime(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=process_factory,
        http_get=HttpGet({"status": "starting"}, {"status": "ok", "workerVersion": "test-worker"}),
        monotonic=iter([0.0, 0.2, 1.0]).__next__,
        sleep=lambda _: None,
    )

    with pytest.raises(LiepinWorkerModeError, match="worker_start_timeout"):
        runtime.ensure_started()

    assert first_process.terminated is True
    assert runtime._process is None
    assert runtime._handle is None

    runtime.ensure_started()

    assert len(process_factory.calls) == 2
    assert second_process.terminated is False


def test_missing_bun_or_worker_package_reports_prerequisite_without_node_fallback(tmp_path: Path) -> None:
    package_dir = _package_dir(tmp_path)
    settings = make_settings(liepin_worker_mode="managed_local")

    missing_bun = ManagedLiepinWorkerRuntime(
        settings,
        worker_package_dir=package_dir,
        bun_executable=None,
        process_factory=ProcessFactory(),
    )
    with pytest.raises(LiepinWorkerModeError, match="Bun executable") as bun_error:
        missing_bun.ensure_started()
    assert "Node.js" not in str(bun_error.value)

    missing_package = ManagedLiepinWorkerRuntime(
        settings,
        worker_package_dir=tmp_path / "missing-worker",
        bun_executable="/usr/local/bin/bun",
        process_factory=ProcessFactory(),
    )
    with pytest.raises(LiepinWorkerModeError, match="worker package"):
        missing_package.ensure_started()


def test_worker_crash_records_failed_event_and_redacts_output(tmp_path: Path) -> None:
    settings = make_settings(liepin_worker_mode="managed_local")
    process = RecordingProcess(
        returncode=1,
        stdout_text="stdout secret-token",
        stderr_text="stderr secret-token",
    )
    events: list[tuple[str, dict[str, object]]] = []
    runtime = ManagedLiepinWorkerRuntime(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=ProcessFactory(process),
        http_get=HttpGet({"status": "starting"}),
        sleep=lambda _: None,
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        runtime.ensure_started(on_event=lambda name, payload: events.append((name, payload)))

    assert events[0][0] == "worker_failed"
    assert events[0][1]["diagnostics"]["stdout"] == "[redacted]"
    assert events[0][1]["diagnostics"]["stderr"] == "[redacted]"
    assert "secret-token" not in str(error.value)


def test_configured_occupied_port_fails_but_port_zero_chooses_free_port(tmp_path: Path) -> None:
    package_dir = _package_dir(tmp_path)
    with socket() as bound:
        bound.bind(("127.0.0.1", 0))
        occupied_port = bound.getsockname()[1]

        settings = make_settings(liepin_worker_mode="managed_local", liepin_worker_port=occupied_port)
        runtime = ManagedLiepinWorkerRuntime(
            settings,
            worker_package_dir=package_dir,
            bun_executable="/usr/local/bin/bun",
            process_factory=ProcessFactory(),
        )
        with pytest.raises(LiepinWorkerModeError, match="port"):
            runtime.ensure_started()

        free_settings = make_settings(liepin_worker_mode="managed_local", liepin_worker_port=0)
        free_runtime = ManagedLiepinWorkerRuntime(
            free_settings,
            worker_package_dir=package_dir,
            bun_executable="/usr/local/bin/bun",
            process_factory=ProcessFactory(),
            http_get=HttpGet({"status": "ok", "workerVersion": "test-worker"}),
            sleep=lambda _: None,
        )
        handle = free_runtime.ensure_started()

    assert handle.port != occupied_port


def test_shared_runtime_cache_can_be_reset_for_tests(tmp_path: Path) -> None:
    settings = make_settings(liepin_worker_mode="managed_local", liepin_worker_port=0)
    process_factory = ProcessFactory()

    runtime = ManagedLiepinWorkerRuntime.shared(
        settings,
        worker_package_dir=_package_dir(tmp_path),
        bun_executable="/usr/local/bin/bun",
        process_factory=process_factory,
        http_get=HttpGet({"status": "ok", "workerVersion": "test-worker"}),
        sleep=lambda _: None,
    )
    runtime.ensure_started()

    ManagedLiepinWorkerRuntime.reset_shared()

    assert process_factory.process.terminated is True
