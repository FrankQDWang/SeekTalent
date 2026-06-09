from __future__ import annotations

from pathlib import Path

import pytest


def test_pause_idempotency_and_duplicate_pending_lifecycle_command_return_existing(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService

    store = _store_with_run(tmp_path, status="running")
    service = RuntimeCommandService(store=store, now=_clock("2026-06-08T00:00:01.000000Z"))

    first = service.request_pause(
        runtime_run_id="runtime_run_1",
        requested_by="agent",
        idempotency_key="pause-1",
    )
    replay = service.request_pause(
        runtime_run_id="runtime_run_1",
        requested_by="agent",
        idempotency_key="pause-1",
    )
    duplicate_pending = service.request_pause(
        runtime_run_id="runtime_run_1",
        requested_by="agent",
        idempotency_key="pause-2",
    )

    assert replay.command_id == first.command_id
    assert duplicate_pending.command_id == first.command_id
    assert [command.command_id for command in store.list_commands(runtime_run_id="runtime_run_1")] == [
        first.command_id
    ]
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == ["runtime_command_accepted"]


def test_cancel_supersedes_pending_pause_and_blocks_later_lifecycle_commands(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path, status="running")
    service = RuntimeCommandService(store=store, now=_clock("2026-06-08T00:00:01.000000Z"))

    pause = service.request_pause(runtime_run_id="runtime_run_1", requested_by="agent", idempotency_key="pause-1")
    cancel = service.request_cancel(runtime_run_id="runtime_run_1", requested_by="agent", idempotency_key="cancel-1")

    assert store.get_command(pause.command_id).status == "superseded"
    assert cancel.status == "accepted"

    with pytest.raises(RuntimeControlError) as exc_info:
        service.request_pause(runtime_run_id="runtime_run_1", requested_by="agent", idempotency_key="pause-2")

    assert exc_info.value.reason_code == "runtime_command_conflict"
    assert exc_info.value.payload["conflictingCommandId"] == cancel.command_id

    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_command_accepted",
        "runtime_command_superseded",
        "runtime_command_accepted",
    ]


def test_safe_boundary_applies_pause_with_checkpoint_and_resume_requires_paused_run(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeCheckpoint

    store = _store_with_run(tmp_path, status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    service = RuntimeCommandService(
        store=store,
        now=_clock(
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
            "2026-06-08T00:00:03.000000Z",
        ),
    )
    pause = service.request_pause(runtime_run_id="runtime_run_1", requested_by="agent", idempotency_key="pause-1")

    with pytest.raises(RuntimeControlError) as exc_info:
        service.resume_workflow(runtime_run_id="runtime_run_1", requested_by="agent", idempotency_key="resume-early")

    assert exc_info.value.reason_code == "runtime_run_not_paused"

    applied = service.apply_lifecycle_command_at_safe_boundary(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        safe_boundary="before_source_dispatch",
        checkpoint=RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_pause_1",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="before_source_dispatch",
            run_state={"round": 2},
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:02.000000Z",
        ),
    )

    assert applied.command_id == pause.command_id
    assert store.get_command(pause.command_id).status == "applied"
    assert store.get_run("runtime_run_1").status == "paused"
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_1").checkpoint_id == "rtcheckpoint_pause_1"

    resume = service.resume_workflow(runtime_run_id="runtime_run_1", requested_by="agent", idempotency_key="resume-1")

    assert resume.command_type == "resume"
    assert store.get_run("runtime_run_1").status == "resume_requested"


def test_safe_boundary_applies_cancel_with_checkpoint_and_completes_run(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.models import RuntimeCheckpoint

    store = _store_with_run(tmp_path, status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    service = RuntimeCommandService(
        store=store,
        now=_clock(
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
        ),
    )
    cancel = service.request_cancel(runtime_run_id="runtime_run_1", requested_by="agent", idempotency_key="cancel-1")

    applied = service.apply_lifecycle_command_at_safe_boundary(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        safe_boundary="before_source_dispatch",
        checkpoint=RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_cancel_1",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="before_source_dispatch",
            run_state={"round": 2},
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:02.000000Z",
        ),
    )

    assert applied.command_id == cancel.command_id
    assert store.get_command(cancel.command_id).status == "applied"
    run = store.get_run("runtime_run_1")
    assert run.status == "cancelled"
    assert run.completed_at == "2026-06-08T00:00:02.000000Z"
    assert run.latest_checkpoint_id == "rtcheckpoint_cancel_1"
    latest_checkpoint = store.get_latest_checkpoint(runtime_run_id="runtime_run_1")
    assert latest_checkpoint is not None
    assert latest_checkpoint.checkpoint_id == "rtcheckpoint_cancel_1"

    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_command_accepted",
        "runtime_command_applied",
        "runtime_run_cancelled",
    ]
    assert events[-1].payload["appliedEventId"] == events[-2].event_id


def _store_with_run(tmp_path: Path, *, status: str):
    from seektalent_runtime_control.models import RuntimeRunRecord
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id="reqapproved_1",
            status=status,
            current_stage="round",
            current_round=2,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00.000000Z",
            updated_at="2026-06-08T00:00:00.000000Z",
            completed_at=None,
        )
    )
    return store


def _clock(*values: str):
    iterator = iter(values)
    last = values[-1]

    def now() -> str:
        nonlocal last
        last = next(iterator, last)
        return last

    return now
