from __future__ import annotations

import sqlite3
from pathlib import Path


def test_recovery_marks_expired_starting_lease_failed_without_silent_running(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="starting")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts()

    assert [decision.reason_code for decision in decisions] == ["runtime_executor_start_timeout"]
    assert store.get_run("runtime_run_1").status == "failed"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_executor_start_failed",
    ]
    assert events[-1].payload["reasonCode"] == "runtime_executor_start_timeout"


def test_recovery_marks_expired_running_lease_as_crash_not_start_timeout(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts()

    assert [decision.reason_code for decision in decisions] == ["runtime_executor_crash_timeout"]
    run = store.get_run("runtime_run_1")
    assert run.status == "failed"
    assert run.stop_reason_code == "runtime_executor_crash_timeout"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_executor_crashed",
    ]
    assert events[-1].payload["reasonCode"] == "runtime_executor_crash_timeout"


def test_recovery_applies_pending_cancel_when_executor_disappears(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="cancellation_requested")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts()

    assert [decision.reason_code for decision in decisions] == ["runtime_cancel_after_executor_lost"]
    run = store.get_run("runtime_run_1")
    assert run.status == "cancelled"
    assert run.stop_reason_code == "runtime_cancel_after_executor_lost"
    assert run.completed_at == "2026-06-08T00:00:06.000000Z"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_run_cancelled",
    ]


def test_recovery_restores_latest_checkpoint_before_new_runtime_events(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_1",
            runtime_run_id="runtime_run_1",
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
        executor_id="executor_1",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts()

    assert [decision.reason_code for decision in decisions] == ["runtime_checkpoint_restored"]
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert events[-1].event_type == "runtime_checkpoint_restored"
    assert events[-1].payload["checkpointId"] == "rtcheckpoint_1"
    assert store.get_run("runtime_run_1").status == "resume_requested"


def test_recovery_marks_run_failed_when_latest_checkpoint_is_corrupt(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store, status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                run_state_json, source_plan_json, pending_commands_json,
                artifact_manifest_ref, schema_version, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rtcheckpoint_corrupt",
                "runtime_run_1",
                "round",
                1,
                "after_round_controller",
                "{not json",
                "{}",
                "[]",
                None,
                "runtime-control-checkpoint/v1",
                "2026-06-08T00:00:03.000000Z",
            ),
        )
        conn.execute(
            "UPDATE runtime_control_runs SET latest_checkpoint_id = ? WHERE runtime_run_id = ?",
            ("rtcheckpoint_corrupt", "runtime_run_1"),
        )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts()

    assert [decision.reason_code for decision in decisions] == ["runtime_checkpoint_corrupt"]
    run = store.get_run("runtime_run_1")
    assert run.status == "failed"
    assert run.stop_reason_code == "runtime_checkpoint_corrupt"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_checkpoint_restore_failed",
    ]
    assert events[-1].payload["checkpointId"] == "rtcheckpoint_corrupt"


def _create_run(store, *, status: str) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status=status,
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
