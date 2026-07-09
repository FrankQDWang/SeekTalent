from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import seektalent_workbench_v2.runtime_runner as runtime_runner_module
from seektalent_ui.server import _lifespan
from seektalent_workbench_v2.runtime_runner import WorkbenchV2RuntimeQueueRunner


class _AliveThread:
    def is_alive(self) -> bool:
        return True


class _FailingRuntimeExecutionWorker:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def run_once(self, *, runtime_run_id: str | None = None) -> object:
        raise RuntimeError(f"failed {runtime_run_id or 'next'}")


class _RecordingRuntimeExecutionWorker:
    calls: list[str] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def run_once(self, *, runtime_run_id: str | None = None) -> object:
        del runtime_run_id
        self.calls.append("run_once")
        return None


class _RecordingRuntimeRecoveryService:
    calls: list[str] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def recover_start_timeouts(self, *, resume_recoverable: bool = True) -> list[object]:
        self.calls.append(f"recover:{resume_recoverable}")
        return []


def test_runtime_runner_wake_respects_worker_count_for_specific_run_ids() -> None:
    runner = WorkbenchV2RuntimeQueueRunner(
        store=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        worker_count=1,
    )
    started_runtime_run_ids: list[str | None] = []

    def record_start(*, runtime_run_id: str | None) -> None:
        started_runtime_run_ids.append(runtime_run_id)
        runner._threads.append(_AliveThread())  # type: ignore[arg-type]

    runner._start_thread = record_start  # type: ignore[method-assign]

    runner.wake(runtime_run_id="rtrun_1")
    runner.wake(runtime_run_id="rtrun_2")

    assert started_runtime_run_ids == ["rtrun_1"]


def test_lifespan_does_not_auto_wake_workbench_v2_runner_on_startup() -> None:
    class _RecordingRunner:
        def __init__(self) -> None:
            self.wake_calls = 0

        def wake(self) -> None:
            self.wake_calls += 1

    runner = _RecordingRunner()
    app = SimpleNamespace(
        state=SimpleNamespace(
            workflow_start_outbox_runner=None,
            requirement_extraction_outbox_runner=None,
            workbench_v2_runtime_runner=runner,
        )
    )

    async def run_lifespan() -> None:
        async with _lifespan(app):  # type: ignore[arg-type]
            pass

    asyncio.run(run_lifespan())

    assert runner.wake_calls == 0


def test_runtime_runner_recovers_expired_leases_before_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = WorkbenchV2RuntimeQueueRunner(
        store=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
    )
    calls: list[str] = []
    _RecordingRuntimeRecoveryService.calls = calls
    _RecordingRuntimeExecutionWorker.calls = calls
    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _RecordingRuntimeRecoveryService, raising=False)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _RecordingRuntimeExecutionWorker)

    asyncio.run(runner._drain_queue(runtime_run_id=None))

    assert calls == ["recover:False", "run_once"]


def test_runtime_runner_logs_specific_run_failures(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = WorkbenchV2RuntimeQueueRunner(
        store=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _RecordingRuntimeRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _FailingRuntimeExecutionWorker)

    with caplog.at_level(logging.WARNING, logger="seektalent_workbench_v2.runtime_runner"):
        asyncio.run(runner._drain_queue(runtime_run_id="rtrun_1"))

    assert "workbench v2 runtime queue drain failed: RuntimeError: failed rtrun_1" in caplog.text
