from __future__ import annotations

import asyncio
import logging

import pytest

import seektalent_workbench_v2.runtime_runner as runtime_runner_module
from seektalent_workbench_v2.runtime_runner import WorkbenchV2RuntimeQueueRunner


class _AliveThread:
    def is_alive(self) -> bool:
        return True


class _FailingRuntimeExecutionWorker:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def run_once(self, *, runtime_run_id: str | None = None) -> object:
        raise RuntimeError(f"failed {runtime_run_id or 'next'}")


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

    assert started_runtime_run_ids == [None]


def test_runtime_runner_logs_specific_run_failures(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = WorkbenchV2RuntimeQueueRunner(
        store=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _FailingRuntimeExecutionWorker)

    with caplog.at_level(logging.WARNING, logger="seektalent_workbench_v2.runtime_runner"):
        asyncio.run(runner._drain_queue(runtime_run_id="rtrun_1"))

    assert "workbench v2 runtime queue drain failed: failed rtrun_1" in caplog.text
