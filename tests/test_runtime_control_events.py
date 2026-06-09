from __future__ import annotations

from pathlib import Path

import pytest


def test_executor_lease_rejects_second_active_lease_and_accepts_heartbeat(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _initialized_store(tmp_path)
    _create_run(store)

    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )

    assert lease.attempt_no == 1
    assert lease.status == "active"

    with pytest.raises(RuntimeControlError) as exc_info:
        store.acquire_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_2",
            acquired_at="2026-06-08T00:00:01.000000Z",
            lease_expires_at="2026-06-08T00:01:01.000000Z",
        )

    assert exc_info.value.reason_code == "runtime_executor_lease_active"

    refreshed = store.heartbeat_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        heartbeat_at="2026-06-08T00:00:30.000000Z",
        lease_expires_at="2026-06-08T00:01:30.000000Z",
    )

    assert refreshed.heartbeat_at == "2026-06-08T00:00:30.000000Z"
    assert refreshed.lease_expires_at == "2026-06-08T00:01:30.000000Z"


def test_executor_event_rejects_stale_executor_without_advancing_cursor(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _initialized_store(tmp_path)
    _create_run(store)
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.append_executor_event(
            _event("rtevt_stale"),
            executor_id="executor_stale",
            run_status="running",
        )

    assert exc_info.value.reason_code == "runtime_executor_stale"
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == []
    assert store.get_run("runtime_run_1").latest_event_seq == 0

    event = store.append_executor_event(_event("rtevt_started"), executor_id="executor_1", run_status="running")

    assert event.event_seq == 1
    assert store.get_run("runtime_run_1").status == "running"


def _initialized_store(tmp_path: Path):
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    return store


def _create_run(store) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status="starting",
            current_stage="startup",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00.000000Z",
            updated_at="2026-06-08T00:00:00.000000Z",
            completed_at=None,
        )
    )


def _event(event_id: str):
    from seektalent_runtime_control.models import RuntimeControlEventInput

    return RuntimeControlEventInput(
        event_id=event_id,
        runtime_run_id="runtime_run_1",
        event_type="runtime_executor_started",
        stage="startup",
        round_no=None,
        source_id=None,
        status="completed",
        summary="executor started",
        payload={"executorId": "executor_1"},
        workbench_event_global_seq=None,
        created_at="2026-06-08T00:00:01.000000Z",
    )
