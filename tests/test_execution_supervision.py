from __future__ import annotations

from types import SimpleNamespace
import sqlite3
import threading

from seektalent_ui.workflow_start_outbox_runner import (
    OutboxDispatchUnknownError,
    RequirementExtractionOutboxRunner,
    RetryableOutboxError,
    WorkflowStartOutboxRunner,
)


class _OutboxStore:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self.items = items
        self.pending_retries: list[str] = []
        self.quarantined: list[tuple[str, str]] = []
        self.waiting: list[tuple[str, str]] = []
        self.done: list[str] = []

    def list_claimable_items(self, **_kwargs: object) -> list[SimpleNamespace]:
        items, self.items = self.items, []
        return items

    def get(self, _outbox_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            attempt_count=1,
            aggregate_id="aggregate-1",
        )

    def mark_pending_retry(self, outbox_id: str, **_kwargs: object) -> None:
        self.pending_retries.append(outbox_id)

    def mark_quarantined(
        self,
        outbox_id: str,
        *,
        reason_code: str,
        **_kwargs: object,
    ) -> None:
        self.quarantined.append((outbox_id, reason_code))

    def mark_waiting_reconciliation(
        self,
        outbox_id: str,
        *,
        reason_code: str,
        **_kwargs: object,
    ) -> None:
        self.waiting.append((outbox_id, reason_code))

    def mark_done(self, outbox_id: str, **_kwargs: object) -> None:
        self.done.append(outbox_id)


class _Service:
    def __init__(self, store: _OutboxStore) -> None:
        self.outbox_store = store

    def now(self) -> str:
        return "2026-07-30T00:00:10.000000Z"


class _UnknownFailureRunner(WorkflowStartOutboxRunner):
    def _process_item(self, outbox_id: str) -> object:
        del outbox_id
        raise KeyError("PRIVATE_OUTBOX_FAILURE")


class _BoundaryFailureRunner(WorkflowStartOutboxRunner):
    error: Exception

    def _process_item(self, outbox_id: str) -> object:
        del outbox_id
        raise self.error


class _RuntimeStore:
    def __init__(self, run: object | None) -> None:
        self.run = run

    def get_run_by_start_idempotency_key(
        self,
        _key: str,
    ) -> object | None:
        return self.run


class _IntentStore:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []

    def get(self, aggregate_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            workflow_start_intent_id=aggregate_id,
            deterministic_run_key="deterministic-run-key",
        )

    def mark_started(
        self,
        intent_id: str,
        *,
        runtime_run_id: str,
        **_kwargs: object,
    ) -> None:
        self.started.append((intent_id, runtime_run_id))


class _BoundaryAwareService(_Service):
    def __init__(
        self,
        store: _OutboxStore,
        *,
        run: object | None,
    ) -> None:
        super().__init__(store)
        self.workflow_start_intent_store = _IntentStore()
        self.service_action_adapter = SimpleNamespace(
            runtime_store=_RuntimeStore(run)
        )
        self.links: list[str] = []

    def _link_started_workflow_run(
        self,
        _intent: object,
        *,
        runtime_run_id: str,
    ) -> None:
        self.links.append(runtime_run_id)


def test_unknown_outbox_failure_is_quarantined_without_retry() -> None:
    item = SimpleNamespace(
        outbox_id="outbox-1",
        aggregate_id="aggregate-1",
        status="pending",
        attempt_count=1,
        updated_at="2026-07-30T00:00:00.000000Z",
    )
    store = _OutboxStore([item])
    runner = _UnknownFailureRunner(
        service=_Service(store),  # type: ignore[arg-type]
    )

    assert runner.run_once() == 1
    assert store.pending_retries == []
    assert store.quarantined == [
        ("outbox-1", "outbox_unexpected_failure")
    ]
    health = runner.health_snapshot()
    assert health.first_failure_type == "KeyError"
    assert health.failure_count == 1


def test_requirement_outbox_wake_rebuilds_a_dead_thread() -> None:
    runner = RequirementExtractionOutboxRunner(
        service=_Service(_OutboxStore([])),  # type: ignore[arg-type]
        poll_interval_seconds=60,
    )
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    runner._thread = dead

    runner.wake()

    replacement = runner._thread
    assert replacement is not None
    assert replacement is not dead
    assert replacement.is_alive()
    runner.stop(timeout=1)


def test_pre_dispatch_proven_safe_failure_gets_bounded_retry() -> None:
    item = SimpleNamespace(
        outbox_id="outbox-safe",
        aggregate_id="aggregate-safe",
        status="pending",
        attempt_count=1,
        updated_at="2026-07-30T00:00:00.000000Z",
    )
    store = _OutboxStore([item])
    runner = _BoundaryFailureRunner(
        service=_Service(store),  # type: ignore[arg-type]
    )
    runner.error = RetryableOutboxError("pre_dispatch_no_effect")

    assert runner.run_once() == 1
    assert store.pending_retries == ["outbox-safe"]
    assert store.waiting == []
    assert store.quarantined == []


def test_post_dispatch_unknown_waits_for_reconciliation_without_retry() -> None:
    item = SimpleNamespace(
        outbox_id="outbox-unknown",
        aggregate_id="aggregate-unknown",
        status="pending",
        attempt_count=1,
        updated_at="2026-07-30T00:00:00.000000Z",
    )
    store = _OutboxStore([item])
    runner = _BoundaryFailureRunner(
        service=_Service(store),  # type: ignore[arg-type]
    )
    runner.error = OutboxDispatchUnknownError("dispatch_intent_persisted")

    assert runner.run_once() == 1
    assert store.pending_retries == []
    assert store.waiting == [
        ("outbox-unknown", "outbox_dispatch_unknown")
    ]
    assert store.quarantined == []


def test_transient_failure_retries_only_when_durable_boundary_proves_no_run() -> None:
    item = SimpleNamespace(
        outbox_id="outbox-no-effect",
        aggregate_id="aggregate-1",
        status="pending",
        attempt_count=1,
        updated_at="2026-07-30T00:00:00.000000Z",
    )
    store = _OutboxStore([item])
    service = _BoundaryAwareService(store, run=None)
    runner = _BoundaryFailureRunner(service=service)  # type: ignore[arg-type]
    runner.error = sqlite3.OperationalError("database is busy")

    assert runner.run_once() == 1
    assert store.pending_retries == ["outbox-no-effect"]
    assert store.waiting == []
    assert store.quarantined == []


def test_committed_downstream_run_is_reused_after_local_outbox_failure() -> None:
    item = SimpleNamespace(
        outbox_id="outbox-committed",
        aggregate_id="aggregate-1",
        status="pending",
        attempt_count=1,
        updated_at="2026-07-30T00:00:00.000000Z",
    )
    store = _OutboxStore([item])
    service = _BoundaryAwareService(
        store,
        run=SimpleNamespace(runtime_run_id="runtime-committed"),
    )
    runner = _BoundaryFailureRunner(service=service)  # type: ignore[arg-type]
    runner.error = OSError("local projection failed")

    assert runner.run_once() == 1
    assert store.done == ["outbox-committed"]
    assert service.links == ["runtime-committed"]
    assert service.workflow_start_intent_store.started == [
        ("aggregate-1", "runtime-committed")
    ]
    assert store.pending_retries == []
