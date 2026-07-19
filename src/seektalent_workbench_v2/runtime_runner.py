from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import cast
from uuid import uuid4

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.recovery import RuntimeRecoveryService
from seektalent_runtime_control.models import RuntimeWorkerClaim
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.worker import RuntimeExecutionWorker


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_RECOVERY_INTERVAL_SECONDS = 30.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0
_EXPECTED_RUNNER_ERRORS = (RuntimeControlError, sqlite3.Error, RuntimeError, ValueError, TypeError, OSError)


class _StopAwareRuntimeControlStore:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        stop_event: threading.Event,
        claim_lock: threading.Lock,
    ) -> None:
        self._store = store
        self._stop_event = stop_event
        self._claim_lock = claim_lock

    def claim_next_runnable_run(
        self,
        *,
        executor_id: str,
        claimed_at: str,
        lease_expires_at: str,
        runtime_run_id: str | None = None,
    ) -> RuntimeWorkerClaim | None:
        with self._claim_lock:
            if self._stop_event.is_set():
                return None
            return self._store.claim_next_runnable_run(
                executor_id=executor_id,
                claimed_at=claimed_at,
                lease_expires_at=lease_expires_at,
                runtime_run_id=runtime_run_id,
            )

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)


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
        self._claim_lock = threading.Lock()
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
        bounded_timeout = max(0.0, timeout)
        started_at = time.monotonic()
        deadline = started_at + bounded_timeout
        with self._lock:
            self._stop_event.set()
            self._wake_event.set()
            thread = self._thread
        claim_boundary_reached = self._claim_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        if claim_boundary_reached:
            self._claim_lock.release()
        else:
            logger.warning(
                "workbench v2 runtime runner stop timed out waiting for the claim boundary after %.3f seconds",
                timeout,
            )
        if thread is None or not thread.is_alive():
            return
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
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
        worker_store = cast(
            RuntimeControlStore,
            _StopAwareRuntimeControlStore(
                store=self.store,
                stop_event=self._stop_event,
                claim_lock=self._claim_lock,
            ),
        )
        worker = RuntimeExecutionWorker(
            store=worker_store,
            executor=self.executor,
            executor_id_factory=lambda: f"workbenchv2_{uuid4().hex[:12]}",
        )
        next_recovery_at = 0.0
        while not self._stop_event.is_set():
            now = self._monotonic()
            if now >= next_recovery_at:
                recovery_failed = False
                try:
                    recovery.recover_start_timeouts(resume_recoverable=False)
                except _EXPECTED_RUNNER_ERRORS as exc:
                    recovery_failed = True
                    logger.warning(
                        "workbench v2 runtime recovery failed: %s: %s",
                        type(exc).__name__,
                        exc,
                    )
                next_recovery_at = now + self.recovery_interval_seconds
                if self._stop_event.is_set():
                    break
                if recovery_failed:
                    self._wait_for_work()
                    continue

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
