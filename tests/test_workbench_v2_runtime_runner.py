from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

import seektalent_workbench_v2.runtime_runner as runtime_runner_module
from seektalent.models import RequirementSheet
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeRunRecord
from seektalent_runtime_control.recovery import RuntimeRecoveryService
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_ui.server import _lifespan
from seektalent_workbench_v2.runtime_runner import WorkbenchV2RuntimeQueueRunner


_NOW = "2026-07-18T00:00:00.000000Z"


class _NoopRecoveryService:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def recover_start_timeouts(self, *, resume_recoverable: bool = True) -> list[object]:
        del resume_recoverable
        return []


class _WaitEvent:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.wait_entered = threading.Event()

    def set(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_entered.set()
        return self._event.wait(timeout)


class _RecordingExecutor:
    def __init__(self, store: RuntimeControlStore, *, expected_calls: int = 1) -> None:
        self.store = store
        self.expected_calls = expected_calls
        self.calls: list[str] = []
        self.completed = threading.Event()
        self._lock = threading.Lock()

    async def execute_claimed_run(self, *, runtime_run_id: str, **kwargs: object) -> RuntimeRunRecord:
        del kwargs
        with self._lock:
            self.calls.append(runtime_run_id)
            if len(self.calls) >= self.expected_calls:
                self.completed.set()
        return self.store.get_run(runtime_run_id)


class _BlockingFirstExecutor(_RecordingExecutor):
    def __init__(self, store: RuntimeControlStore) -> None:
        super().__init__(store, expected_calls=2)
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    async def execute_claimed_run(self, *, runtime_run_id: str, **kwargs: object) -> RuntimeRunRecord:
        del kwargs
        with self._lock:
            self.calls.append(runtime_run_id)
            call_count = len(self.calls)
            if call_count >= self.expected_calls:
                self.completed.set()
        if call_count == 1:
            self.first_started.set()
            await asyncio.to_thread(self.release_first.wait)
        return self.store.get_run(runtime_run_id)


class _ObservedStore:
    def __init__(self, store: RuntimeControlStore) -> None:
        self.store = store
        self.empty_poll_completed = threading.Event()

    def claim_next_runnable_run(self, **kwargs: object):
        claim = self.store.claim_next_runnable_run(**kwargs)
        if claim is None:
            self.empty_poll_completed.set()
        return claim

    def __getattr__(self, name: str):
        return getattr(self.store, name)


class _LifecycleRecorder:
    def __init__(self, name: str, calls: list[str], *, stop_error: Exception | None = None) -> None:
        self.name = name
        self.calls = calls
        self.stop_error = stop_error

    def start(self) -> None:
        self.calls.append(f"{self.name}.start")

    def stop(self) -> None:
        self.calls.append(f"{self.name}.stop")
        if self.stop_error is not None:
            raise self.stop_error

    def wake(self) -> None:
        self.calls.append(f"{self.name}.wake")


class _BlockingClaimLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._acquisition_count = 0
        self.boundary_entered = threading.Event()
        self.release_boundary = threading.Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        with self._counter_lock:
            self._acquisition_count += 1
            acquisition_count = self._acquisition_count
        if timeout < 0:
            acquired = self._lock.acquire(blocking)
        else:
            acquired = self._lock.acquire(blocking, timeout)
        if acquired and acquisition_count == 1:
            self.boundary_entered.set()
            self.release_boundary.wait()
        return acquired

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> _BlockingClaimLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


def test_lifespan_starts_runtime_runner_and_stops_in_reverse_producer_order() -> None:
    calls: list[str] = []
    app = _lifespan_app(calls)

    async def scenario() -> None:
        async with _lifespan(app):  # type: ignore[arg-type]
            calls.append("yield")

    asyncio.run(scenario())

    assert calls == [
        "runtime.start",
        "workflow.start",
        "workflow.wake",
        "extraction.start",
        "yield",
        "extraction.stop",
        "workflow.stop",
        "runtime.stop",
    ]


def test_lifespan_preserves_body_error_when_cleanup_also_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    app = _lifespan_app(calls, stop_errors={"extraction": RuntimeError("extraction stop failed")})

    async def scenario() -> None:
        with caplog.at_level(logging.ERROR, logger="seektalent_ui.server"):
            with pytest.raises(RuntimeError, match="lifespan failed"):
                async with _lifespan(app):  # type: ignore[arg-type]
                    raise RuntimeError("lifespan failed")

    asyncio.run(scenario())

    assert calls[-3:] == ["extraction.stop", "workflow.stop", "runtime.stop"]
    assert "requirement extraction runner failed during application lifespan cleanup" in caplog.text


def test_lifespan_attempts_all_cleanup_and_propagates_cleanup_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    app = _lifespan_app(
        calls,
        stop_errors={
            "extraction": RuntimeError("extraction stop failed"),
            "workflow": ValueError("workflow stop failed"),
        },
    )

    async def scenario() -> None:
        with caplog.at_level(logging.ERROR, logger="seektalent_ui.server"):
            with pytest.raises(ExceptionGroup) as exc_info:
                async with _lifespan(app):  # type: ignore[arg-type]
                    calls.append("yield")
        assert [str(error) for error in exc_info.value.exceptions] == [
            "extraction stop failed",
            "workflow stop failed",
        ]

    asyncio.run(scenario())

    assert calls[-3:] == ["extraction.stop", "workflow.stop", "runtime.stop"]
    assert "requirement extraction runner failed during application lifespan cleanup" in caplog.text
    assert "workflow start runner failed during application lifespan cleanup" in caplog.text


def test_cold_sqlite_queue_is_consumed_without_route_wake(tmp_path) -> None:
    store = _runtime_store(tmp_path)
    _enqueue_run(store, "runtime-cold", created_at="2026-07-18T00:00:00.000000Z")
    executor = _RecordingExecutor(store)
    runner = WorkbenchV2RuntimeQueueRunner(store=store, executor=executor, poll_interval_seconds=0.01)

    runner.start()
    _assert_set(executor.completed)
    runner.stop()

    assert executor.calls == ["runtime-cold"]
    assert store.get_run("runtime-cold").status == "starting"


def test_idle_poller_finds_later_sqlite_run_without_wake(tmp_path) -> None:
    store = _runtime_store(tmp_path)
    observed_store = _ObservedStore(store)
    executor = _RecordingExecutor(store)
    runner = WorkbenchV2RuntimeQueueRunner(
        store=observed_store,  # type: ignore[arg-type]
        executor=executor,
        poll_interval_seconds=0.01,
    )

    runner.start()
    _assert_set(observed_store.empty_poll_completed)
    _enqueue_run(store, "runtime-lost-wake", created_at="2026-07-18T00:00:01.000000Z")
    _assert_set(executor.completed)
    runner.stop()

    assert executor.calls == ["runtime-lost-wake"]


def test_busy_worker_consumes_second_run_enqueued_without_wake(tmp_path) -> None:
    store = _runtime_store(tmp_path)
    _enqueue_run(store, "runtime-first", created_at="2026-07-18T00:00:00.000000Z")
    executor = _BlockingFirstExecutor(store)
    runner = WorkbenchV2RuntimeQueueRunner(store=store, executor=executor, poll_interval_seconds=0.01)

    runner.start()
    _assert_set(executor.first_started)
    _enqueue_run(store, "runtime-second", created_at="2026-07-18T00:00:01.000000Z")
    executor.release_first.set()
    _assert_set(executor.completed)
    runner.stop()

    assert executor.calls == ["runtime-first", "runtime-second"]


def test_real_sqlite_claim_preserves_fifo_for_fully_accepted_runs(tmp_path) -> None:
    store = _runtime_store(tmp_path)
    _enqueue_run(store, "runtime-a", created_at="2026-07-18T00:00:00.000000Z")
    _enqueue_run(store, "runtime-b", created_at="2026-07-18T00:00:01.000000Z")
    executor = _RecordingExecutor(store, expected_calls=2)
    runner = WorkbenchV2RuntimeQueueRunner(store=store, executor=executor, poll_interval_seconds=0.01)

    runner.start()
    _assert_set(executor.completed)
    runner.stop()

    assert executor.calls == ["runtime-a", "runtime-b"]


def test_runtime_run_id_wake_never_reaches_worker_targeted_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_once_kwargs: list[dict[str, object]] = []
    first_poll = threading.Event()
    second_poll = threading.Event()

    class _RecordingWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self, **kwargs: object) -> None:
            run_once_kwargs.append(kwargs)
            if len(run_once_kwargs) == 1:
                first_poll.set()
            if len(run_once_kwargs) == 2:
                second_poll.set()
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _RecordingWorker)
    runner = _fake_runner()

    runner.start()
    _assert_set(first_poll)
    runner.wake(runtime_run_id="runtime-target-must-be-ignored")
    _assert_set(second_poll)
    runner.stop()

    assert run_once_kwargs == [{}, {}]


def test_concurrent_duplicate_start_and_wake_create_one_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_workers: list[object] = []
    polled = threading.Event()

    class _IdleWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            created_workers.append(self)

        async def run_once(self) -> None:
            polled.set()
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _IdleWorker)
    runner = _fake_runner()
    barrier = threading.Barrier(9)

    def start_or_wake(index: int) -> None:
        barrier.wait()
        if index % 2:
            runner.start()
        else:
            runner.wake(runtime_run_id=f"runtime-{index}")

    callers = [threading.Thread(target=start_or_wake, args=(index,)) for index in range(8)]
    for caller in callers:
        caller.start()
    barrier.wait()
    for caller in callers:
        caller.join(timeout=2)
        assert not caller.is_alive()

    runner.start()
    _assert_set(polled)
    thread = runner._thread
    runner.start()
    runner.wake()
    runner.stop()

    assert len(created_workers) == 1
    assert thread is not None
    assert runner._thread is thread


def test_empty_queue_waits_instead_of_exiting_or_hot_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class _IdleWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self) -> None:
            nonlocal calls
            calls += 1
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _IdleWorker)
    runner = _fake_runner()
    wait_event = _WaitEvent()
    runner._wake_event = wait_event  # type: ignore[assignment]

    runner.start()
    _assert_set(wait_event.wait_entered)

    assert calls == 1
    assert runner._thread is not None and runner._thread.is_alive()
    runner.stop()
    assert not runner._thread.is_alive()


def test_expected_poll_error_waits_then_continues_on_next_hint(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    processed = threading.Event()

    class _FailOnceWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self) -> object | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient poll failure")
            processed.set()
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _FailOnceWorker)
    runner = _fake_runner()
    wait_event = _WaitEvent()
    runner._wake_event = wait_event  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="seektalent_workbench_v2.runtime_runner"):
        runner.start()
        _assert_set(wait_event.wait_entered)
        assert calls == 1
        runner.wake(runtime_run_id="ignored")
        _assert_set(processed)
        runner.stop()

    assert calls == 2
    assert "workbench v2 runtime poll failed: RuntimeError: transient poll failure" in caplog.text


def test_poll_operational_error_waits_then_processes_later_task(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    processed = threading.Event()

    class _OperationalErrorWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self) -> object | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("database is locked")
            processed.set()
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _OperationalErrorWorker)
    runner = _fake_runner()
    wait_event = _WaitEvent()
    runner._wake_event = wait_event  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="seektalent_workbench_v2.runtime_runner"):
        runner.start()
        _assert_set(wait_event.wait_entered)
        assert calls == 1
        runner.wake()
        _assert_set(processed)
        runner.stop()

    assert calls == 2
    assert "workbench v2 runtime poll failed: OperationalError: database is locked" in caplog.text


def test_recovery_operational_error_waits_then_poller_processes_task(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recovery_calls = 0
    processed = threading.Event()

    class _OperationalErrorRecovery:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def recover_start_timeouts(self, *, resume_recoverable: bool = True) -> list[object]:
            nonlocal recovery_calls
            recovery_calls += 1
            assert resume_recoverable is False
            raise sqlite3.OperationalError("recovery database is locked")

    class _ProcessingWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self) -> None:
            processed.set()
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _OperationalErrorRecovery)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _ProcessingWorker)
    runner = WorkbenchV2RuntimeQueueRunner(
        store=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        monotonic=iter((0.0, 1.0)).__next__,
    )
    wait_event = _WaitEvent()
    runner._wake_event = wait_event  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="seektalent_workbench_v2.runtime_runner"):
        runner.start()
        _assert_set(wait_event.wait_entered)
        assert not processed.is_set()
        runner.wake()
        _assert_set(processed)
        runner.stop()

    assert recovery_calls == 1
    assert "workbench v2 runtime recovery failed: OperationalError: recovery database is locked" in caplog.text


def test_periodic_recovery_always_disables_recoverable_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_calls: list[bool] = []
    second_recovery = threading.Event()
    clock_values = iter((0.0, 1.0, 5.0))

    class _RecordingRecoveryService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def recover_start_timeouts(self, *, resume_recoverable: bool = True) -> list[object]:
            recovery_calls.append(resume_recoverable)
            if len(recovery_calls) == 2:
                second_recovery.set()
            return []

    class _BusyWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self) -> object:
            return object()

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _RecordingRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _BusyWorker)
    runner = WorkbenchV2RuntimeQueueRunner(
        store=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
        recovery_interval_seconds=5,
        monotonic=lambda: next(clock_values, 5.0),
    )

    runner.start()
    _assert_set(second_recovery)
    runner.stop()

    assert recovery_calls == [False, False]


def test_expired_recoverable_lease_fails_closed_in_runner(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _runtime_store(tmp_path)
    _create_nonqueued_run(store, "runtime-expired", status="running", created_at="2026-06-08T00:00:00.000000Z")
    store.acquire_executor_lease(
        runtime_run_id="runtime-expired",
        executor_id="executor-expired",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="checkpoint-expired",
            runtime_run_id="runtime-expired",
            stage="round",
            round_no=1,
            safe_boundary="after_round_controller",
            run_state={"round": 1},
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:03.000000Z",
        ),
        executor_id="executor-expired",
    )
    recovered = threading.Event()
    resume_flags: list[bool] = []

    class _ObservedRecovery(RuntimeRecoveryService):
        def recover_start_timeouts(self, *, resume_recoverable: bool = True):
            resume_flags.append(resume_recoverable)
            decisions = super().recover_start_timeouts(resume_recoverable=resume_recoverable)
            recovered.set()
            return decisions

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _ObservedRecovery)
    runner = WorkbenchV2RuntimeQueueRunner(
        store=store,
        executor=_RecordingExecutor(store),  # type: ignore[arg-type]
        poll_interval_seconds=60,
    )

    runner.start()
    _assert_set(recovered)
    runner.stop()

    run = store.get_run("runtime-expired")
    assert resume_flags == [False]
    assert run.status == "failed"
    assert run.stop_reason_code == "runtime_executor_crash_timeout"


def test_idle_stop_is_bounded_and_repeated_stop_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polled = threading.Event()

    class _IdleWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self) -> None:
            polled.set()
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _IdleWorker)
    runner = _fake_runner()

    runner.start()
    _assert_set(polled)
    runner.stop(timeout=1)
    runner.stop(timeout=1)

    assert runner._thread is not None
    assert not runner._thread.is_alive()


def test_active_stop_warns_without_cancelling_and_prevents_another_claim(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    class _BlockingWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self) -> object:
            nonlocal calls
            calls += 1
            started.set()
            await asyncio.to_thread(release.wait)
            return object()

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _BlockingWorker)
    runner = _fake_runner()

    with caplog.at_level(logging.WARNING, logger="seektalent_workbench_v2.runtime_runner"):
        runner.start()
        _assert_set(started)
        runner.stop(timeout=0.01)

    assert runner._thread is not None and runner._thread.is_alive()
    assert calls == 1
    assert "active execution remains lease-governed" in caplog.text

    release.set()
    runner._thread.join(timeout=2)
    assert not runner._thread.is_alive()
    assert calls == 1


def test_stop_deadline_linearizes_before_blocked_claim_without_creating_lease(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _runtime_store(tmp_path)
    _enqueue_run(store, "runtime-stop-boundary", created_at="2026-07-18T00:00:00.000000Z")
    executor = _RecordingExecutor(store)
    runner = WorkbenchV2RuntimeQueueRunner(store=store, executor=executor, poll_interval_seconds=60)
    claim_lock = _BlockingClaimLock()
    runner._claim_lock = claim_lock  # type: ignore[assignment]

    runner.start()
    _assert_set(claim_lock.boundary_entered)
    started_at = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="seektalent_workbench_v2.runtime_runner"):
        runner.stop(timeout=0.05)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.2
    assert "stop timed out waiting for the claim boundary" in caplog.text
    claim_lock.release_boundary.set()
    assert runner._thread is not None
    runner._thread.join(timeout=2)

    assert not runner._thread.is_alive()
    assert executor.calls == []
    assert store.get_run("runtime-stop-boundary").status == "queued"
    assert store.list_active_executor_leases() == []


def test_runner_can_restart_after_completed_stop_without_leaking_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polls = [threading.Event(), threading.Event()]
    instance_count = 0

    class _IdleWorker:
        def __init__(self, *args: object, **kwargs: object) -> None:
            nonlocal instance_count
            self.index = instance_count
            instance_count += 1

        async def run_once(self) -> None:
            polls[self.index].set()
            return None

    monkeypatch.setattr(runtime_runner_module, "RuntimeRecoveryService", _NoopRecoveryService)
    monkeypatch.setattr(runtime_runner_module, "RuntimeExecutionWorker", _IdleWorker)
    runner = _fake_runner()

    runner.start()
    _assert_set(polls[0])
    first_thread = runner._thread
    runner.stop()
    runner.start()
    _assert_set(polls[1])
    second_thread = runner._thread
    runner.stop()

    assert instance_count == 2
    assert first_thread is not second_thread
    assert second_thread is not None and not second_thread.is_alive()


def _lifespan_app(
    calls: list[str],
    *,
    stop_errors: dict[str, Exception] | None = None,
) -> SimpleNamespace:
    errors = stop_errors or {}
    return SimpleNamespace(
        state=SimpleNamespace(
            workflow_start_outbox_runner=_LifecycleRecorder("workflow", calls, stop_error=errors.get("workflow")),
            requirement_extraction_outbox_runner=_LifecycleRecorder(
                "extraction",
                calls,
                stop_error=errors.get("extraction"),
            ),
            workbench_v2_runtime_runner=_LifecycleRecorder("runtime", calls, stop_error=errors.get("runtime")),
        )
    )


def _runtime_store(tmp_path) -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    return store


def _enqueue_run(
    store: RuntimeControlStore,
    runtime_run_id: str,
    *,
    created_at: str,
) -> RuntimeRunRecord:
    approved = ApprovedRequirementRevision(
        approved_requirement_revision_id=f"requirement-{runtime_run_id}",
        draft_revision_id=f"draft-{runtime_run_id}",
        agent_conversation_id=f"conversation-{runtime_run_id}",
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
        created_at=created_at,
    )
    store.save_approved_requirement(approved, idempotency_key=f"approved-{runtime_run_id}")
    enqueue_executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: object(),
        runtime_run_id_factory=lambda: runtime_run_id,
        now=lambda: created_at,
    )
    run = enqueue_executor.enqueue_workflow_run(
        conversation_id=approved.agent_conversation_id,
        workbench_session_id=None,
        approved_requirement=approved,
        job_title="Backend Engineer",
        jd_text="Build data products.",
        notes="Remote only.",
        source_ids=["cts"],
        run_intent_id=f"intent-{runtime_run_id}",
        start_idempotency_key=f"start-{runtime_run_id}",
    )
    snapshot = store.get_snapshot(runtime_run_id=runtime_run_id)
    assert run.latest_event_seq == 1
    assert snapshot is not None
    assert snapshot.snapshot["workflowInput"] == {
        "jobTitle": "Backend Engineer",
        "jdText": "Build data products.",
        "notes": "Remote only.",
        "sourceIds": ["cts"],
    }
    return run


def _create_nonqueued_run(
    store: RuntimeControlStore,
    runtime_run_id: str,
    *,
    status: str,
    created_at: str,
) -> None:
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            run_intent_id=f"intent-{runtime_run_id}",
            start_idempotency_key=f"start-{runtime_run_id}",
            run_kind="primary",
            agent_conversation_id=f"conversation-{runtime_run_id}",
            workbench_session_id=None,
            approved_requirement_revision_id=f"requirement-{runtime_run_id}",
            status=status,
            current_stage=status,
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at=created_at,
            updated_at=created_at,
            completed_at=None,
        )
    )


def _fake_runner() -> WorkbenchV2RuntimeQueueRunner:
    return WorkbenchV2RuntimeQueueRunner(
        store=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        poll_interval_seconds=60,
    )


def _assert_set(event: threading.Event, *, timeout: float = 3.0) -> None:
    assert event.wait(timeout), "timed out waiting for deterministic synchronization event"
