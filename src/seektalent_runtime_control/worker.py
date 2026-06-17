from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import (
    RuntimeControlEventInput,
    RuntimeExecutorLease,
    RuntimeRunRecord,
    RuntimeRunSnapshot,
)
from seektalent_runtime_control.store import RuntimeControlStore


class ClaimedRunExecutor(Protocol):
    async def execute_claimed_run(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
        job_title: str | None,
        jd_text: str | None,
        notes: str | None,
        source_ids: Sequence[str],
    ) -> RuntimeRunRecord: ...


@dataclass(frozen=True)
class RuntimeWorkerResult:
    runtime_run: RuntimeRunRecord | None

    @property
    def status(self) -> str | None:
        return self.runtime_run.status if self.runtime_run is not None else None


class RuntimeExecutionWorker:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        executor: ClaimedRunExecutor,
        executor_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
        lease_seconds: float = 60,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.executor_id_factory = executor_id_factory or (lambda: f"rtexec_worker_{uuid4().hex[:12]}")
        self.now = now or _now
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds if heartbeat_interval_seconds is not None else max(1.0, lease_seconds / 3)
        )

    async def run_once(self, *, now: str | None = None, runtime_run_id: str | None = None) -> RuntimeRunRecord | None:
        executor_id = self.executor_id_factory()
        claimed_at = now or self.now()
        claim = self.store.claim_next_runnable_run(
            executor_id=executor_id,
            claimed_at=claimed_at,
            lease_expires_at=_plus_seconds(claimed_at, self.lease_seconds),
            runtime_run_id=runtime_run_id,
        )
        if claim is None:
            return None

        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_claimed_lease(
                runtime_run_id=claim.runtime_run.runtime_run_id,
                executor_id=claim.lease.executor_id,
                attempt_no=claim.lease.attempt_no,
                stop_event=stop_heartbeat,
            )
        )
        executor_task: asyncio.Task[RuntimeRunRecord] | None = None
        try:
            self._heartbeat_lease(
                runtime_run_id=claim.runtime_run.runtime_run_id,
                executor_id=claim.lease.executor_id,
                attempt_no=claim.lease.attempt_no,
            )
            workflow_input = _workflow_input(self.store.get_snapshot(runtime_run_id=claim.runtime_run.runtime_run_id))
            executor_task = asyncio.create_task(
                self.executor.execute_claimed_run(
                    runtime_run_id=claim.runtime_run.runtime_run_id,
                    executor_id=claim.lease.executor_id,
                    attempt_no=claim.lease.attempt_no,
                    job_title=_text(workflow_input.get("jobTitle")),
                    jd_text=_text(workflow_input.get("jdText")),
                    notes=_text(workflow_input.get("notes")),
                    source_ids=_source_ids(workflow_input.get("sourceIds"), fallback=claim.runtime_run.source_ids),
                )
            )
            done, _pending = await asyncio.wait(
                {executor_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                try:
                    heartbeat_task.result()
                except RuntimeControlError as exc:
                    raise RuntimeControlError("runtime_executor_heartbeat_failed", str(exc)) from exc
                raise RuntimeControlError("runtime_executor_heartbeat_stopped")
            return await executor_task
        except (RuntimeControlError, RuntimeError, ValueError, TypeError, OSError) as exc:
            if executor_task is not None and not executor_task.done():
                executor_task.cancel()
                with suppress(asyncio.CancelledError):
                    await executor_task
            if _executor_finalized_failure(exc):
                raise
            reason_code = _worker_failure_reason(exc)
            self._record_worker_failure(
                runtime_run=claim.runtime_run,
                lease=claim.lease,
                reason_code=reason_code,
                summary=str(exc) or type(exc).__name__,
            )
            raise
        finally:
            stop_heartbeat.set()
            if heartbeat_task.done():
                with suppress(Exception):
                    heartbeat_task.result()
            else:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task

    def heartbeat_active_leases(self, *, now: str | None = None, executor_id: str | None = None) -> int:
        list_active = getattr(self.store, "list_active_executor_leases", None)
        if not callable(list_active):
            return 0
        owner_executor_id = executor_id or self.executor_id_factory()
        heartbeat_at = now or self.now()
        count = 0
        for lease in list_active(executor_id=owner_executor_id):
            self.store.heartbeat_executor_lease(
                runtime_run_id=lease.runtime_run_id,
                executor_id=lease.executor_id,
                attempt_no=lease.attempt_no,
                heartbeat_at=heartbeat_at,
                lease_expires_at=_plus_seconds(heartbeat_at, self.lease_seconds),
            )
            count += 1
        return count

    def recover_expired_leases(self, *, now: str | None = None) -> list[RuntimeExecutorLease]:
        return self.store.expire_executor_leases(now=now or self.now())

    async def _heartbeat_claimed_lease(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
        stop_event: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_interval_seconds)
            except TimeoutError:
                self._heartbeat_lease(runtime_run_id=runtime_run_id, executor_id=executor_id, attempt_no=attempt_no)
                continue
            return

    def _heartbeat_lease(self, *, runtime_run_id: str, executor_id: str, attempt_no: int) -> None:
        heartbeat_at = self.now()
        self.store.heartbeat_executor_lease(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            heartbeat_at=heartbeat_at,
            lease_expires_at=_plus_seconds(heartbeat_at, self.lease_seconds),
        )

    def _record_worker_failure(
        self,
        *,
        runtime_run: RuntimeRunRecord,
        lease: RuntimeExecutorLease,
        reason_code: str,
        summary: str,
    ) -> None:
        failed_at = self.now()
        self.store.append_executor_event(
            RuntimeControlEventInput(
                event_id=f"rtevt_{uuid4().hex}",
                runtime_run_id=runtime_run.runtime_run_id,
                event_type=reason_code,
                stage="worker",
                round_no=runtime_run.current_round,
                source_id=None,
                status="failed",
                summary=summary,
                payload={
                    "reasonCode": reason_code,
                    "executorId": lease.executor_id,
                    "attemptNo": lease.attempt_no,
                },
                schema_version="runtime-control-event/v1",
                visibility="developer",
                idempotency_key=f"{reason_code}:{lease.lease_id}",
                payload_kind="compact",
                created_at=failed_at,
            ),
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
            run_status="failed",
            stop_reason_code=reason_code,
            completed_at=failed_at,
        )
        self.store.release_executor_lease(
            runtime_run_id=runtime_run.runtime_run_id,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
            released_at=self.now(),
            status="failed",
            reason_code=reason_code,
        )


def _workflow_input(snapshot: RuntimeRunSnapshot | None) -> dict[str, object]:
    if snapshot is None:
        return {}
    value = snapshot.snapshot.get("workflowInput")
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _source_ids(value: object, *, fallback: Sequence[str]) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return list(fallback)
    parsed = [item for item in value if isinstance(item, str) and item.strip()]
    return parsed or list(fallback)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _worker_failure_reason(exc: Exception) -> str:
    if isinstance(exc, RuntimeControlError) and exc.reason_code == "runtime_executor_heartbeat_stopped":
        return "runtime_executor_heartbeat_failed"
    if not isinstance(exc, RuntimeControlError):
        return "runtime_worker_failed"
    if exc.reason_code.startswith("runtime_executor_heartbeat"):
        return "runtime_executor_heartbeat_failed"
    return "runtime_worker_failed"


def _executor_finalized_failure(exc: Exception) -> bool:
    if not isinstance(exc, RuntimeControlError):
        return False
    return exc.reason_code in {
        "runtime_resume_checkpoint_missing",
        "runtime_checkpoint_corrupt",
        "runtime_checkpoint_schema_unsupported",
    }


def _plus_seconds(value: str, seconds: float) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
