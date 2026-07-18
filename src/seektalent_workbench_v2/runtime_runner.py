from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from uuid import uuid4

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.recovery import RuntimeRecoveryService
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.worker import RuntimeExecutionWorker


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_RECOVERY_INTERVAL_SECONDS = 30.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0
_EXPECTED_RUNNER_ERRORS = (RuntimeControlError, RuntimeError, ValueError, TypeError, OSError)


class WorkbenchV2RuntimeQueueRunner:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        executor: WorkflowRuntimeExecutor,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        recovery_interval_seconds: float = DEFAULT_RECOVERY_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if recovery_interval_seconds <= 0:
            raise ValueError("recovery_interval_seconds must be positive")
        self.store = store
        self.executor = executor
        self.poll_interval_seconds = poll_interval_seconds
        self.recovery_interval_seconds = recovery_interval_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(
                target=self._run_in_thread,
                name="seektalent-workbench-v2-runtime-runner",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return
        thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            logger.warning(
                "workbench v2 runtime runner did not stop within %.3f seconds; active execution remains lease-governed",
                timeout,
            )

    def wake(self, runtime_run_id: str | None = None) -> None:
        del runtime_run_id
        self._wake_event.set()

    def _run_in_thread(self) -> None:
        asyncio.run(self._run_loop())

    async def _run_loop(self) -> None:
        recovery = RuntimeRecoveryService(store=self.store)
        worker = RuntimeExecutionWorker(
            store=self.store,
            executor=self.executor,
            executor_id_factory=lambda: f"workbenchv2_{uuid4().hex[:12]}",
        )
        next_recovery_at = 0.0
        while not self._stop_event.is_set():
            now = self._monotonic()
            if now >= next_recovery_at:
                try:
                    recovery.recover_start_timeouts(resume_recoverable=False)
                except _EXPECTED_RUNNER_ERRORS as exc:
                    logger.warning(
                        "workbench v2 runtime recovery failed: %s: %s",
                        type(exc).__name__,
                        exc,
                    )
                next_recovery_at = now + self.recovery_interval_seconds

            if self._stop_event.is_set():
                break
            try:
                runtime_run = await worker.run_once()
            except _EXPECTED_RUNNER_ERRORS as exc:
                logger.warning(
                    "workbench v2 runtime poll failed: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                self._wait_for_work()
                continue
            if runtime_run is not None:
                continue
            self._wait_for_work()

    def _wait_for_work(self) -> None:
        if self._stop_event.is_set():
            return
        self._wake_event.wait(self.poll_interval_seconds)
        self._wake_event.clear()
