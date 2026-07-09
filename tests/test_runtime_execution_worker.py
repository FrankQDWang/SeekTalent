from __future__ import annotations

import asyncio
from collections.abc import Sequence
from multiprocessing import get_context
from queue import Empty
from types import SimpleNamespace

import pytest

from seektalent.models import RequirementSheet
from seektalent_runtime_control.models import (
    RuntimeControlEvent,
    RuntimeExecutorLease,
    RuntimeRunRecord,
    RuntimeRunSnapshot,
    RuntimeWorkerClaim,
)
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.worker import RuntimeExecutionWorker


_NOW = "2026-06-17T00:00:00.000000Z"


def test_run_once_claims_next_run_and_executes_claimed_runtime_with_snapshot_input() -> None:
    run = _run(source_ids=["cts"])
    claim = _claim(run=run, executor_id="worker-exec-1")
    store = _FakeRuntimeControlStore(
        claims=[claim],
        snapshots={
            run.runtime_run_id: RuntimeRunSnapshot(
                runtime_run_id=run.runtime_run_id,
                status="queued",
                current_stage="queued",
                current_round=None,
                latest_event_seq=0,
                snapshot={
                    "workflowInput": {
                        "jobTitle": "Backend Engineer",
                        "jdText": "Build data products.",
                        "notes": "Remote only.",
                        "sourceIds": ["cts"],
                    }
                },
                updated_at=_NOW,
            )
        },
    )
    executor = _FakeRuntimeExecutor(result=_run(status="completed", source_ids=["cts"]))
    worker = RuntimeExecutionWorker(
        store=store,
        executor=executor,
        executor_id_factory=lambda: "worker-exec-1",
        now=lambda: _NOW,
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )

    result = asyncio.run(worker.run_once())

    assert result is not None
    assert result.status == "completed"
    assert store.claim_calls == [
        {
            "executor_id": "worker-exec-1",
            "claimed_at": _NOW,
            "lease_expires_at": "2026-06-17T00:00:30.000000Z",
            "runtime_run_id": None,
        }
    ]
    assert executor.calls == [
        {
            "runtime_run_id": "runtime-run-1",
            "executor_id": "worker-exec-1",
            "attempt_no": 1,
            "job_title": "Backend Engineer",
            "jd_text": "Build data products.",
            "notes": "Remote only.",
            "source_ids": ["cts"],
        }
    ]


def test_run_once_returns_none_when_no_run_is_runnable() -> None:
    store = _FakeRuntimeControlStore(claims=[])
    executor = _FakeRuntimeExecutor()
    worker = RuntimeExecutionWorker(
        store=store,
        executor=executor,
        executor_id_factory=lambda: "worker-exec-1",
        now=lambda: _NOW,
    )

    result = asyncio.run(worker.run_once())

    assert result is None
    assert executor.calls == []
    assert store.heartbeats == []


def test_run_once_renews_claimed_lease_while_executor_is_blocked_longer_than_ttl() -> None:
    async def scenario() -> None:
        run = _run()
        store = _FakeRuntimeControlStore(claims=[_claim(run=run, executor_id="worker-exec-ttl")])
        executor = _BlockingRuntimeExecutor(result=_run(status="completed"))
        worker = RuntimeExecutionWorker(
            store=store,
            executor=executor,
            executor_id_factory=lambda: "worker-exec-ttl",
            lease_seconds=0.03,
            heartbeat_interval_seconds=0.005,
        )

        task = asyncio.create_task(worker.run_once())
        await executor.started.wait()
        await _wait_until(lambda: len(store.heartbeats) >= 2)

        executor.release.set()
        result = await task

        heartbeat_count_after_completion = len(store.heartbeats)
        await asyncio.sleep(0.02)

        assert result is not None
        assert result.status == "completed"
        assert heartbeat_count_after_completion >= 2
        assert len(store.heartbeats) == heartbeat_count_after_completion
        assert {heartbeat["runtime_run_id"] for heartbeat in store.heartbeats} == {"runtime-run-1"}
        assert {heartbeat["executor_id"] for heartbeat in store.heartbeats} == {"worker-exec-ttl"}

    asyncio.run(scenario())


def test_run_once_stops_heartbeat_and_records_visible_failure_when_executor_raises() -> None:
    async def scenario() -> None:
        run = _run()
        store = _FakeRuntimeControlStore(claims=[_claim(run=run, executor_id="worker-exec-fail")])
        executor = _FailingRuntimeExecutor(RuntimeError("runtime exploded"))
        worker = RuntimeExecutionWorker(
            store=store,
            executor=executor,
            executor_id_factory=lambda: "worker-exec-fail",
            lease_seconds=0.03,
            heartbeat_interval_seconds=0.005,
        )

        task = asyncio.create_task(worker.run_once())
        await executor.started.wait()
        await _wait_until(lambda: len(store.heartbeats) >= 1)

        with pytest.raises(RuntimeError, match="runtime exploded"):
            await task

        heartbeat_count_after_failure = len(store.heartbeats)
        await asyncio.sleep(0.02)

        assert len(store.heartbeats) == heartbeat_count_after_failure
        assert store.failure_events[-1]["event_type"] == "runtime_worker_failed"
        assert store.failure_events[-1]["summary"] == "runtime exploded"
        assert store.releases == [
            {
                "runtime_run_id": "runtime-run-1",
                "executor_id": "worker-exec-fail",
                "attempt_no": 1,
                "status": "failed",
                "reason_code": "runtime_worker_failed",
            }
        ]

    asyncio.run(scenario())


def test_heartbeat_failure_aborts_blocked_executor_and_records_visible_failure() -> None:
    async def scenario() -> None:
        run = _run()
        store = _HeartbeatFailingStore(claims=[_claim(run=run, executor_id="worker-exec-db-locked")])
        executor = _BlockingRuntimeExecutor(result=_run(status="completed"))
        worker = RuntimeExecutionWorker(
            store=store,
            executor=executor,
            executor_id_factory=lambda: "worker-exec-db-locked",
            lease_seconds=0.03,
            heartbeat_interval_seconds=0.005,
        )

        task = asyncio.create_task(worker.run_once())
        await executor.started.wait()

        with pytest.raises(RuntimeControlError) as exc_info:
            await task

        assert exc_info.value.reason_code == "runtime_executor_heartbeat_failed"
        assert executor.release.is_set() is False
        assert store.failure_events[-1]["event_type"] == "runtime_executor_heartbeat_failed"
        assert store.releases[-1]["reason_code"] == "runtime_executor_heartbeat_failed"

    from seektalent_runtime_control.errors import RuntimeControlError

    asyncio.run(scenario())


def test_heartbeat_active_leases_only_renews_current_worker_executor(tmp_path) -> None:
    del tmp_path
    own_lease = _lease(executor_id="worker-exec-owned")
    other_lease = _lease(executor_id="worker-exec-other")
    store = _FakeRuntimeControlStore(claims=[], active_leases=[own_lease, other_lease])
    worker = RuntimeExecutionWorker(
        store=store,
        executor=_FakeRuntimeExecutor(),
        executor_id_factory=lambda: "worker-exec-owned",
        now=lambda: _NOW,
        lease_seconds=30,
    )

    renewed = worker.heartbeat_active_leases()

    assert renewed == 1
    assert store.list_active_calls == ["worker-exec-owned"]
    assert [(heartbeat["executor_id"], heartbeat["attempt_no"]) for heartbeat in store.heartbeats] == [
        ("worker-exec-owned", 1)
    ]


def test_recover_expired_leases_delegates_to_store() -> None:
    expired = _lease(executor_id="expired-exec", status="expired")
    store = _FakeRuntimeControlStore(claims=[], expired_leases=[expired])
    worker = RuntimeExecutionWorker(store=store, executor=_FakeRuntimeExecutor(), now=lambda: _NOW)

    assert worker.recover_expired_leases() == [expired]
    assert store.expire_calls == [_NOW]


def test_worker_claims_real_store_run_and_executes_real_executor(tmp_path) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    approved = _approved_requirement()
    store.save_approved_requirement(approved, idempotency_key="approved-real")
    runtime = _CallbackRuntime()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: runtime,
        runtime_run_id_factory=lambda: "runtime-run-real",
        now=lambda: _NOW,
    )
    queued = executor.enqueue_workflow_run(
        conversation_id="agent-conv-real",
        workbench_session_id="session-real",
        approved_requirement=approved,
        job_title="Backend Engineer",
        jd_text="Build data products.",
        notes="Remote only.",
        source_ids=["cts"],
    )
    worker = RuntimeExecutionWorker(
        store=store,
        executor=executor,
        executor_id_factory=lambda: "worker-exec-real",
        now=lambda: _NOW,
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )

    result = asyncio.run(worker.run_once(runtime_run_id=queued.runtime_run_id))

    assert result is not None
    assert result.status == "completed"
    assert runtime.received["job_title"] == "Backend Engineer"
    assert runtime.received["jd"] == "Build data products."
    assert runtime.received["notes"] == "Remote only."
    assert runtime.received["source_kinds"] == ["cts"]
    assert not store.list_active_executor_leases()
    events = store.list_events(runtime_run_id=queued.runtime_run_id, after_seq=0, limit=20).events
    assert [event.event_type for event in events] == [
        "runtime_run_queued",
        "runtime_worker_claimed",
        "runtime_executor_starting",
        "runtime_executor_started",
        "runtime_run_completed",
    ]


def test_worker_preserves_executor_finalized_runtime_failure(tmp_path) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    approved = _approved_requirement()
    store.save_approved_requirement(approved, idempotency_key="approved-runtime-failure")
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: _FailingAfterStartRuntime(RuntimeError("liepin search failed")),
        runtime_run_id_factory=lambda: "runtime-run-runtime-failure",
        now=lambda: _NOW,
    )
    queued = executor.enqueue_workflow_run(
        conversation_id="agent-conv-runtime-failure",
        workbench_session_id="session-runtime-failure",
        approved_requirement=approved,
        job_title="Backend Engineer",
        jd_text="Build data products.",
        notes="Remote only.",
        source_ids=["liepin"],
    )
    worker = RuntimeExecutionWorker(
        store=store,
        executor=executor,
        executor_id_factory=lambda: "worker-exec-runtime-failure",
        now=lambda: _NOW,
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )

    with pytest.raises(RuntimeError, match="liepin search failed"):
        asyncio.run(worker.run_once(runtime_run_id=queued.runtime_run_id))

    run = store.get_run(queued.runtime_run_id)
    assert run.status == "failed"
    assert run.stop_reason_code == "runtime_run_failed"
    assert not store.list_active_executor_leases()
    events = store.list_events(runtime_run_id=queued.runtime_run_id, after_seq=0, limit=20).events
    assert [event.event_type for event in events] == [
        "runtime_run_queued",
        "runtime_worker_claimed",
        "runtime_executor_starting",
        "runtime_executor_started",
        "runtime_run_failed",
    ]


def test_worker_claim_is_single_winner_across_processes(tmp_path) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    approved = _approved_requirement()
    store.save_approved_requirement(approved, idempotency_key="approved-multiprocess")
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: _CallbackRuntime(),
        runtime_run_id_factory=lambda: "runtime-run-multiprocess",
        now=lambda: _NOW,
    )
    queued = executor.enqueue_workflow_run(
        conversation_id="agent-conv-multiprocess",
        workbench_session_id="session-multiprocess",
        approved_requirement=approved,
        job_title="Backend Engineer",
        jd_text="Build data products.",
        notes="Remote only.",
        source_ids=["cts"],
    )
    ctx = get_context("spawn")
    start_event = ctx.Event()
    queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_claim_run_in_process,
            args=(
                str(store.path),
                queued.runtime_run_id,
                f"worker-exec-process-{index}",
                start_event,
                queue,
            ),
        )
        for index in range(6)
    ]
    for process in processes:
        process.start()
    start_event.set()
    try:
        results = [queue.get(timeout=15) for _ in processes]
    except Empty as exc:
        for process in processes:
            process.join(timeout=1)
        exitcodes = {process.name: process.exitcode for process in processes}
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        raise AssertionError(f"worker claim processes did not all report results: {exitcodes}") from exc
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    failures = [result for result in results if result.get("error")]
    assert failures == []
    winners = [result for result in results if result["claimed"]]

    assert len(winners) == 1
    assert winners[0]["attempt_no"] == 1
    assert len(store.list_active_executor_leases()) == 1


async def _wait_until(predicate, *, timeout_seconds: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.001)


class _FakeRuntimeControlStore:
    def __init__(
        self,
        *,
        claims: Sequence[RuntimeWorkerClaim],
        snapshots: dict[str, RuntimeRunSnapshot] | None = None,
        expired_leases: Sequence[RuntimeExecutorLease] = (),
        active_leases: Sequence[RuntimeExecutorLease] = (),
    ) -> None:
        self.claims = list(claims)
        self.snapshots = snapshots or {}
        self.expired_leases = list(expired_leases)
        self.active_leases = list(active_leases)
        self.claim_calls: list[dict[str, object]] = []
        self.list_active_calls: list[str | None] = []
        self.heartbeats: list[dict[str, object]] = []
        self.expire_calls: list[str] = []
        self.failure_events: list[dict[str, object]] = []
        self.releases: list[dict[str, object]] = []

    def claim_next_runnable_run(
        self,
        *,
        executor_id: str,
        claimed_at: str,
        lease_expires_at: str,
        runtime_run_id: str | None = None,
    ) -> RuntimeWorkerClaim | None:
        self.claim_calls.append(
            {
                "executor_id": executor_id,
                "claimed_at": claimed_at,
                "lease_expires_at": lease_expires_at,
                "runtime_run_id": runtime_run_id,
            }
        )
        if not self.claims:
            return None
        return self.claims.pop(0)

    def heartbeat_executor_lease(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        heartbeat_at: str,
        lease_expires_at: str,
    ) -> RuntimeExecutorLease:
        self.heartbeats.append(
            {
                "runtime_run_id": runtime_run_id,
                "executor_id": executor_id,
                "attempt_no": attempt_no,
                "heartbeat_at": heartbeat_at,
                "lease_expires_at": lease_expires_at,
            }
        )
        return _lease(runtime_run_id=runtime_run_id, executor_id=executor_id, heartbeat_at=heartbeat_at)

    def expire_executor_leases(self, *, now: str) -> list[RuntimeExecutorLease]:
        self.expire_calls.append(now)
        return list(self.expired_leases)

    def list_active_executor_leases(self, *, executor_id: str | None = None) -> list[RuntimeExecutorLease]:
        self.list_active_calls.append(executor_id)
        if executor_id is None:
            return list(self.active_leases)
        return [lease for lease in self.active_leases if lease.executor_id == executor_id]

    def get_snapshot(self, *, runtime_run_id: str) -> RuntimeRunSnapshot | None:
        return self.snapshots.get(runtime_run_id)

    def append_executor_event(
        self,
        event,
        *,
        executor_id: str,
        attempt_no: int | None = None,
        run_status: str | None = None,
        stop_reason_code: str | None = None,
        completed_at: str | None = None,
    ):
        self.failure_events.append(
            {
                "event_type": event.event_type,
                "summary": event.summary,
                "executor_id": executor_id,
                "attempt_no": attempt_no,
                "run_status": run_status,
                "stop_reason_code": stop_reason_code,
                "completed_at": completed_at,
            }
        )
        return event

    def release_executor_lease(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        released_at: str,
        status: str = "released",
        reason_code: str | None = None,
    ) -> RuntimeExecutorLease:
        del released_at
        self.releases.append(
            {
                "runtime_run_id": runtime_run_id,
                "executor_id": executor_id,
                "attempt_no": attempt_no,
                "status": status,
                "reason_code": reason_code,
            }
        )
        return _lease(runtime_run_id=runtime_run_id, executor_id=executor_id, status=status, reason_code=reason_code)


class _HeartbeatFailingStore(_FakeRuntimeControlStore):
    def heartbeat_executor_lease(self, **kwargs) -> RuntimeExecutorLease:
        if self.heartbeats:
            from seektalent_runtime_control.errors import RuntimeControlError

            raise RuntimeControlError("sqlite_database_locked")
        return super().heartbeat_executor_lease(**kwargs)


class _FakeRuntimeExecutor:
    def __init__(self, result: RuntimeRunRecord | None = None) -> None:
        self.result = result or _run(status="completed")
        self.calls: list[dict[str, object]] = []

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
    ) -> RuntimeRunRecord:
        self.calls.append(
            {
                "runtime_run_id": runtime_run_id,
                "executor_id": executor_id,
                "attempt_no": attempt_no,
                "job_title": job_title,
                "jd_text": jd_text,
                "notes": notes,
                "source_ids": list(source_ids),
            }
        )
        return self.result


class _BlockingRuntimeExecutor(_FakeRuntimeExecutor):
    def __init__(self, result: RuntimeRunRecord) -> None:
        super().__init__(result=result)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute_claimed_run(self, **kwargs) -> RuntimeRunRecord:
        self.calls.append({**kwargs, "source_ids": list(kwargs["source_ids"])})
        self.started.set()
        await self.release.wait()
        return self.result


class _FailingRuntimeExecutor(_FakeRuntimeExecutor):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error
        self.started = asyncio.Event()

    async def execute_claimed_run(self, **kwargs) -> RuntimeRunRecord:
        self.calls.append({**kwargs, "source_ids": list(kwargs["source_ids"])})
        self.started.set()
        raise self.error


class _CallbackRuntime:
    def __init__(self) -> None:
        self.received: dict[str, object] = {}

    async def run_async(self, **kwargs):
        self.received = dict(kwargs)
        kwargs["runtime_start_callback"]("workflow-real")
        return SimpleNamespace(run_id="workflow-real")


class _FailingAfterStartRuntime:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def run_async(self, **kwargs):
        kwargs["runtime_start_callback"]("workflow-failed")
        raise self.error


def _run(*, status: str = "queued", source_ids: Sequence[str] = ("cts",)) -> RuntimeRunRecord:
    return RuntimeRunRecord(
        runtime_run_id="runtime-run-1",
        run_intent_id="intent-1",
        start_idempotency_key="start-1",
        run_kind="primary",
        agent_conversation_id="agent-conv-1",
        workbench_session_id="session-1",
        approved_requirement_revision_id="approved-1",
        status=status,
        current_stage=status,
        current_round=None,
        latest_checkpoint_id=None,
        latest_event_seq=0,
        source_ids=list(source_ids),
        stop_reason_code=None,
        created_at=_NOW,
        updated_at=_NOW,
        completed_at=_NOW if status in {"completed", "failed", "cancelled"} else None,
    )


def _approved_requirement() -> ApprovedRequirementRevision:
    return ApprovedRequirementRevision(
        approved_requirement_revision_id="approved-real",
        draft_revision_id="draft-real",
        agent_conversation_id="agent-conv-real",
        requirement_sheet=RequirementSheet(
            job_title="Backend Engineer",
            title_anchor_terms=["Backend Engineer"],
            title_anchor_rationale="The title is explicit.",
            role_summary="Build data products.",
            must_have_capabilities=["Python"],
            preferred_capabilities=["Search"],
            exclusion_signals=[],
            scoring_rationale="Prioritize backend data products.",
        ),
        selected_item_ids=[],
        deselected_item_ids=[],
        created_at=_NOW,
    )


def _claim(*, run: RuntimeRunRecord, executor_id: str) -> RuntimeWorkerClaim:
    return RuntimeWorkerClaim(
        runtime_run=run,
        lease=_lease(runtime_run_id=run.runtime_run_id, executor_id=executor_id),
        claimed_event=RuntimeControlEvent(
            event_id="event-claim-1",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_worker_claimed",
            stage="starting",
            round_no=None,
            source_id=None,
            status="completed",
            summary="runtime worker claimed run",
            payload={},
            schema_version="runtime-control-event/v1",
            visibility="developer",
            idempotency_key="claim-1",
            payload_kind="compact",
            payload_size_bytes=2,
            projection_attempt_count=0,
            last_projection_error_code=None,
            projected_at=None,
            workbench_event_global_seq=None,
            created_at=_NOW,
            event_seq=1,
        ),
        claim_reason="queued",
    )


def _claim_run_in_process(db_path: str, runtime_run_id: str, executor_id: str, start_event, queue) -> None:
    import sqlite3
    import traceback

    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.store import RuntimeControlStore

    try:
        if not start_event.wait(timeout=15):
            raise TimeoutError("worker claim start event was not set")
        store = RuntimeControlStore(db_path)
        claim = store.claim_next_runnable_run(
            executor_id=executor_id,
            claimed_at="2026-06-17T00:00:01.000000Z",
            lease_expires_at="2026-06-17T00:01:01.000000Z",
            runtime_run_id=runtime_run_id,
        )
        queue.put(
            {
                "executor_id": executor_id,
                "claimed": claim is not None,
                "attempt_no": claim.lease.attempt_no if claim is not None else None,
                "error": None,
            }
        )
    except (RuntimeControlError, sqlite3.Error, TimeoutError, RuntimeError, ValueError, TypeError, OSError) as exc:
        queue.put(
            {
                "executor_id": executor_id,
                "claimed": False,
                "attempt_no": None,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )


def _lease(
    *,
    runtime_run_id: str = "runtime-run-1",
    executor_id: str = "worker-exec-1",
    status: str = "active",
    heartbeat_at: str | None = None,
    reason_code: str | None = None,
) -> RuntimeExecutorLease:
    return RuntimeExecutorLease(
        lease_id=f"lease-{executor_id}",
        runtime_run_id=runtime_run_id,
        executor_id=executor_id,
        attempt_no=1,
        status=status,
        acquired_at=_NOW,
        heartbeat_at=heartbeat_at,
        lease_expires_at="2026-06-17T00:01:00.000000Z",
        released_at=None,
        reason_code=reason_code,
    )
