from __future__ import annotations

from types import SimpleNamespace
import threading

from seektalent_ui.workflow_start_outbox_runner import (
    RequirementExtractionOutboxRunner,
    WorkflowStartOutboxRunner,
)


class _OutboxStore:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self.items = items
        self.pending_retries: list[str] = []

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


class _Service:
    def __init__(self, store: _OutboxStore) -> None:
        self.outbox_store = store

    def now(self) -> str:
        return "2026-07-30T00:00:10.000000Z"


class _UnknownFailureRunner(WorkflowStartOutboxRunner):
    def _process_item(self, outbox_id: str) -> object:
        del outbox_id
        raise KeyError("PRIVATE_OUTBOX_FAILURE")


def test_unknown_outbox_failure_is_recorded_and_bounded_retry_remains_available() -> None:
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
    assert store.pending_retries == ["outbox-1"]
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
