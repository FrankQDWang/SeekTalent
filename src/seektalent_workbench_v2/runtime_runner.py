from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import cast
from uuid import uuid4

from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent.browser_lane_reconciliation import (
    BrowserLaneReconciliationCoordinator,
)
from seektalent_runtime_control.execution_health import (
    ExecutionComponentHealth,
    ExecutionHealthTracker,
)
from seektalent_runtime_control.models import RuntimeWorkerClaim
from seektalent_runtime_control.recovery import RuntimeRecoveryService
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.worker import RuntimeExecutionWorker


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_RECOVERY_INTERVAL_SECONDS = 30.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0
MAX_FAILURE_BACKOFF_SECONDS = 30.0


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
        prepare_readiness_probe: Callable[[], None] | None = None,
        orphaned_owned_tab_absent: Callable[[str], bool] | None = None,
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
        self._health = ExecutionHealthTracker(
            "runtime_runner",
            initial=store.get_component_health("runtime_runner"),
        )
        self._consecutive_failures = 0
        self._browser_lane_reconciliation = (
            BrowserLaneReconciliationCoordinator(
                store=store,
                prepare_readiness_probe=prepare_readiness_probe,
                orphaned_owned_tab_absent=orphaned_owned_tab_absent,
            )
        )

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
            self._health.restarted()
            self._persist_health(alive=True)

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
            self._persist_health(alive=False)
            return
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
        if thread.is_alive():
            logger.warning(
                "workbench v2 runtime runner did not stop within %.3f seconds; active execution remains lease-governed",
                timeout,
            )
        else:
            self._persist_health(alive=False)

    def wake(self, runtime_run_id: str | None = None) -> None:
        del runtime_run_id
        self.start()
        self._wake_event.set()

    def _run_in_thread(self) -> None:
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._run_loop())
                return
            except Exception as exc:
                self._record_failure(exc, boundary="thread")
                self._wait_after_failure()

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
            self._health.heartbeat()
            self._persist_health(alive=True)
            now = self._monotonic()
            if now >= next_recovery_at:
                recovery_failed = False
                try:
                    recovery.recover_start_timeouts(resume_recoverable=True)
                    self._browser_lane_reconciliation.run_once()
                except Exception as exc:
                    recovery_failed = True
                    self._record_failure(exc, boundary="recovery")
                next_recovery_at = now + self.recovery_interval_seconds
                if self._stop_event.is_set():
                    break
                if recovery_failed:
                    self._wait_after_failure()
                    continue

            if self._stop_event.is_set():
                break
            try:
                runtime_run = await worker.run_once()
            except Exception as exc:
                self._record_failure(exc, boundary="poll")
                self._wait_after_failure()
                continue
            self._consecutive_failures = 0
            self._health.success()
            self._persist_health(alive=True)
            if runtime_run is not None:
                if getattr(runtime_run, "status", None) == "resume_requested":
                    self._wait_for_work()
                continue
            self._wait_for_work()

    def _wait_for_work(self) -> None:
        if self._stop_event.is_set():
            return
        self._wake_event.wait(self.poll_interval_seconds)
        self._wake_event.clear()

    def _record_failure(self, error: Exception, *, boundary: str) -> None:
        self._health.failure(error)
        self._persist_health(alive=True)
        recorder = getattr(
            self.store,
            "record_execution_failure",
            None,
        )
        if callable(recorder):
            try:
                recorder(
                    runtime_run_id=None,
                    component="runtime_runner",
                    boundary=boundary,
                    safe_reason_code="runtime_runner_unexpected_failure",
                    error=error,
                    failure_role="primary",
                    occurred_at=self.store_now(),
                )
            except Exception as persistence_error:
                logger.debug(
                    "runtime failure persistence failed: %s",
                    type(persistence_error).__name__,
                )
        self._consecutive_failures += 1
        logger.warning(
            "workbench v2 runtime runner failure",
            extra={"boundary": boundary, "exception_type": type(error).__name__},
        )

    def store_now(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")

    def _wait_after_failure(self) -> None:
        backoff = min(
            self.poll_interval_seconds * (2 ** min(self._consecutive_failures - 1, 5)),
            MAX_FAILURE_BACKOFF_SECONDS,
        )
        self._wake_event.wait(backoff)
        self._wake_event.clear()

    def health_snapshot(self) -> ExecutionComponentHealth:
        thread = self._thread
        return self._health.snapshot(alive=thread is not None and thread.is_alive())

    def _persist_health(self, *, alive: bool) -> None:
        snapshot = self._health.snapshot(alive=alive)
        try:
            self.store.record_component_health(
                component=snapshot.name,
                alive=snapshot.alive,
                last_heartbeat_at=snapshot.last_heartbeat_at,
                last_success_at=snapshot.last_success_at,
                first_failure_at=snapshot.first_failure_at,
                first_failure_type=snapshot.first_failure_type,
                failure_count=snapshot.failure_count,
                restart_count=snapshot.restart_count,
                observed_at=self.store_now(),
            )
        except Exception:
            logger.debug(
                "runtime component health persistence failed",
                exc_info=True,
            )
