from __future__ import annotations

import asyncio
import logging
import threading
from uuid import uuid4

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.recovery import RuntimeRecoveryService
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.worker import RuntimeExecutionWorker


logger = logging.getLogger(__name__)


class WorkbenchV2RuntimeQueueRunner:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        executor: WorkflowRuntimeExecutor,
        worker_count: int = 1,
    ) -> None:
        self.store = store
        self.executor = executor
        self.worker_count = worker_count
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def wake(self, runtime_run_id: str | None = None) -> None:
        with self._lock:
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            while len(self._threads) < self.worker_count:
                self._start_thread(runtime_run_id=runtime_run_id)

    def _start_thread(self, *, runtime_run_id: str | None) -> None:
        thread = threading.Thread(
            target=self._run_until_idle,
            kwargs={"runtime_run_id": runtime_run_id},
            name=f"seektalent-workbench-v2-runtime-runner-{len(self._threads) + 1}",
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()

    def _run_until_idle(self, *, runtime_run_id: str | None) -> None:
        asyncio.run(self._drain_queue(runtime_run_id=runtime_run_id))

    async def _drain_queue(self, *, runtime_run_id: str | None) -> None:
        RuntimeRecoveryService(store=self.store).recover_start_timeouts(resume_recoverable=False)
        worker = RuntimeExecutionWorker(
            store=self.store,
            executor=self.executor,
            executor_id_factory=lambda: f"workbenchv2_{uuid4().hex[:12]}",
        )
        if runtime_run_id is not None:
            try:
                await worker.run_once(runtime_run_id=runtime_run_id)
            except (RuntimeControlError, RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.warning("workbench v2 runtime queue drain failed: %s: %s", type(exc).__name__, exc)
            return
        while True:
            try:
                if await worker.run_once() is None:
                    return
            except (RuntimeControlError, RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.warning("workbench v2 runtime queue drain failed: %s: %s", type(exc).__name__, exc)
                continue
