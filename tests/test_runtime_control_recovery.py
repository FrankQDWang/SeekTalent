from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Event, Thread

import pytest


@pytest.mark.parametrize(
    "fault_point",
    [
        "after_lease_update",
        "after_first_event",
        "before_run_transition",
        "after_run_transition",
    ],
)
def test_recovery_fault_rolls_back_and_retry_settles_once(
    tmp_path: Path,
    fault_point: str,
) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )

    def fail_event(point: str) -> None:
        if point == fault_point:
            raise sqlite3.OperationalError("injected recovery event fault")

    with pytest.raises(sqlite3.OperationalError, match="injected recovery event fault"):
        RuntimeRecoveryService(
            store=store,
            now=lambda: "2026-06-08T00:00:06.000000Z",
            fault_injector=fail_event,
        ).recover_start_timeouts(resume_recoverable=False)

    [stored_lease] = store.list_active_executor_leases()
    assert stored_lease.lease_id == lease.lease_id
    assert store.get_run("runtime_run_1").status == "running"
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == []

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)
    replay = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.reason_code for decision in decisions] == ["runtime_executor_crash_timeout"]
    assert replay == []
    assert store.get_run("runtime_run_1").status == "failed"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_executor_crashed",
    ]
    assert store.list_active_executor_leases() == []
    with sqlite3.connect(store.path) as conn:
        lease_count = conn.execute(
            "SELECT COUNT(*) FROM runtime_control_executor_leases WHERE runtime_run_id = ?",
            ("runtime_run_1",),
        ).fetchone()[0]
    assert lease_count == 1


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
    ).recover_start_timeouts(resume_recoverable=False)

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
    ).recover_start_timeouts(resume_recoverable=False)

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
    ).recover_start_timeouts(resume_recoverable=True)

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


@pytest.mark.parametrize("resume_recoverable", [False, True])
def test_recovery_fail_closes_pending_pause_without_resuming(
    tmp_path: Path,
    resume_recoverable: bool,
) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
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
    RuntimeCommandService(
        store=store,
        now=lambda: "2026-06-08T00:00:04.000000Z",
    ).request_pause(
        runtime_run_id="runtime_run_1",
        requested_by="agent",
        idempotency_key="pause-before-crash",
    )
    assert store.get_run("runtime_run_1").status == "pause_requested"

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts(resume_recoverable=resume_recoverable)

    assert [decision.reason_code for decision in decisions] == [
        "runtime_pause_after_executor_lost"
    ]
    run = store.get_run("runtime_run_1")
    assert run.status == "failed"
    assert run.stop_reason_code == "runtime_pause_after_executor_lost"
    assert [event.event_type for event in store.list_events(
        runtime_run_id="runtime_run_1", after_seq=0, limit=10
    ).events] == [
        "runtime_command_accepted",
        "runtime_executor_lease_expired",
        "runtime_executor_crashed",
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
    ).recover_start_timeouts(resume_recoverable=True)

    assert [decision.reason_code for decision in decisions] == ["runtime_checkpoint_restored"]
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert events[-1].event_type == "runtime_checkpoint_restored"
    assert events[-1].payload["checkpointId"] == "rtcheckpoint_1"
    assert store.get_run("runtime_run_1").status == "resume_requested"


def test_recovery_can_fail_recoverable_checkpoint_instead_of_resuming(tmp_path: Path) -> None:
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
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.reason_code for decision in decisions] == ["runtime_executor_crash_timeout"]
    run = store.get_run("runtime_run_1")
    assert run.status == "failed"
    assert run.stop_reason_code == "runtime_executor_crash_timeout"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_executor_crashed",
    ]
    assert store.list_active_executor_leases() == []
    with sqlite3.connect(store.path) as conn:
        lease_count = conn.execute(
            "SELECT COUNT(*) FROM runtime_control_executor_leases WHERE runtime_run_id = ?",
            ("runtime_run_1",),
        ).fetchone()[0]
    assert lease_count == 1


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
    ).recover_start_timeouts(resume_recoverable=True)

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


def test_recovery_commit_ack_loss_replay_is_idempotent(tmp_path: Path) -> None:
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

    def lose_ack(point: str) -> None:
        if point == "after_commit":
            raise ConnectionError("injected commit acknowledgement loss")

    with pytest.raises(ConnectionError, match="acknowledgement loss"):
        RuntimeRecoveryService(
            store=store,
            now=lambda: "2026-06-08T00:00:06.000000Z",
            fault_injector=lose_ack,
        ).recover_start_timeouts(resume_recoverable=False)

    committed_run = store.get_run("runtime_run_1")
    committed_events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    replay = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert replay == []
    assert store.get_run("runtime_run_1") == committed_run
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == committed_events


def test_recovery_converges_legacy_expired_ownerless_partial_state(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_legacy_expiry",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_lease_expired",
            stage="startup",
            status="failed",
            summary="legacy executor lease expired",
            payload={"executorId": "executor_1", "attemptNo": lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        )
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.reason_code for decision in decisions] == ["runtime_executor_crash_timeout"]
    assert store.get_run("runtime_run_1").status == "failed"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_executor_crashed",
    ]


@pytest.mark.parametrize(
    ("run_status", "event_type", "reason_code"),
    [
        ("running", "runtime_executor_crashed", "runtime_executor_crash_timeout"),
        ("starting", "runtime_executor_start_failed", "runtime_executor_start_timeout"),
    ],
)
def test_recovery_reuses_matching_legacy_decision_without_growing_event_sequence(
    tmp_path: Path,
    run_status: str,
    event_type: str,
    reason_code: str,
) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status=run_status)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    for event in (
        RuntimeControlEventInput(
            event_id="rtevt_legacy_expiry",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_lease_expired",
            stage="startup",
            status="failed",
            summary="legacy executor lease expired",
            payload={"executorId": "executor_1", "attemptNo": lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        ),
        RuntimeControlEventInput(
            event_id="rtevt_legacy_owner_decision",
            runtime_run_id="runtime_run_1",
            event_type=event_type,
            stage="startup",
            status="failed",
            summary="legacy executor lost",
            payload={
                "reasonCode": reason_code,
                "executorId": "executor_1",
            },
            created_at="2026-06-08T00:00:06.000000Z",
        ),
    ):
        store.append_event(event)
    before = store.get_run("runtime_run_1")
    before_events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.reason_code for decision in decisions] == [reason_code]
    run = store.get_run("runtime_run_1")
    assert run.status == "failed"
    assert run.latest_event_seq == before.latest_event_seq
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == before_events


def test_recovery_does_not_reinterpret_legacy_expired_lease_after_pause_and_continues_batch(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running", runtime_run_id="runtime_run_paired")
    paired_lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_paired",
        executor_id="executor_paired",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    for event in (
        RuntimeControlEventInput(
            event_id="rtevt_paired_expiry",
            runtime_run_id="runtime_run_paired",
            event_type="runtime_executor_lease_expired",
            stage="round",
            status="failed",
            summary="legacy expiry",
            payload={"executorId": "executor_paired", "attemptNo": paired_lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        ),
        RuntimeControlEventInput(
            event_id="rtevt_paired_restored",
            runtime_run_id="runtime_run_paired",
            event_type="runtime_checkpoint_restored",
            stage="round",
            status="completed",
            summary="legacy checkpoint restored",
            payload={"checkpointId": "rtcheckpoint_legacy"},
            created_at="2026-06-08T00:00:06.100000Z",
        ),
    ):
        store.append_event(event)
    store.update_run_status(
        runtime_run_id="runtime_run_paired",
        status="resume_requested",
        updated_at="2026-06-08T00:00:06.200000Z",
    )
    RuntimeCommandService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.300000Z",
    ).request_pause(
        runtime_run_id="runtime_run_paired",
        requested_by="agent",
        idempotency_key="pause-after-legacy-restore",
    )
    paired_run_before = store.get_run("runtime_run_paired")
    paired_events_before = store.list_events(
        runtime_run_id="runtime_run_paired", after_seq=0, limit=10
    ).events

    _create_run(store, status="running", runtime_run_id="runtime_run_later")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_later",
        executor_id="executor_later",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:07.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:08.000000Z")

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:09.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.runtime_run_id for decision in decisions] == ["runtime_run_later"]
    assert store.get_run("runtime_run_paired") == paired_run_before
    assert store.list_events(
        runtime_run_id="runtime_run_paired", after_seq=0, limit=10
    ).events == paired_events_before
    assert store.get_run("runtime_run_later").status == "failed"


def test_cancel_overrides_different_legacy_restore_decision(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    for event in (
        RuntimeControlEventInput(
            event_id="rtevt_cancel_precedence_expiry",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_lease_expired",
            stage="startup",
            status="failed",
            summary="legacy expiry",
            payload={"executorId": "executor_1", "attemptNo": lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        ),
        RuntimeControlEventInput(
            event_id="rtevt_cancel_precedence_restored",
            runtime_run_id="runtime_run_1",
            event_type="runtime_checkpoint_restored",
            stage="startup",
            status="completed",
            summary="legacy restore",
            payload={"checkpointId": "rtcheckpoint_legacy"},
            created_at="2026-06-08T00:00:06.100000Z",
        ),
    ):
        store.append_event(event)
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="resume_requested",
        updated_at="2026-06-08T00:00:06.200000Z",
    )
    RuntimeCommandService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.300000Z",
    ).request_cancel(
        runtime_run_id="runtime_run_1",
        requested_by="agent",
        idempotency_key="cancel-after-legacy-restore",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=True)

    assert [decision.reason_code for decision in decisions] == [
        "runtime_cancel_after_executor_lost"
    ]
    assert store.get_run("runtime_run_1").status == "cancelled"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert events[-1].event_type == "runtime_run_cancelled"
    assert events[-1].payload["reasonCode"] == "runtime_cancel_after_executor_lost"


def test_production_policy_overrides_different_legacy_restore_and_fails_running_run(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    for event in (
        RuntimeControlEventInput(
            event_id="rtevt_policy_expiry",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_lease_expired",
            stage="startup",
            status="failed",
            summary="legacy expiry",
            payload={"executorId": "executor_1", "attemptNo": lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        ),
        RuntimeControlEventInput(
            event_id="rtevt_policy_restored",
            runtime_run_id="runtime_run_1",
            event_type="runtime_checkpoint_restored",
            stage="startup",
            status="completed",
            summary="legacy restore",
            payload={"checkpointId": "rtcheckpoint_legacy"},
            created_at="2026-06-08T00:00:06.100000Z",
        ),
    ):
        store.append_event(event)

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.reason_code for decision in decisions] == [
        "runtime_executor_crash_timeout"
    ]
    assert store.get_run("runtime_run_1").status == "failed"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_checkpoint_restored",
        "runtime_executor_crashed",
    ]


def test_recovery_pairs_legacy_decision_across_intervening_non_recovery_event(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    for event in (
        RuntimeControlEventInput(
            event_id="rtevt_interleaved_expiry",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_lease_expired",
            stage="startup",
            status="failed",
            summary="legacy expiry",
            payload={"executorId": "executor_1", "attemptNo": lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        ),
        RuntimeControlEventInput(
            event_id="rtevt_interleaved_command",
            runtime_run_id="runtime_run_1",
            event_type="runtime_command_accepted",
            stage="command",
            status="completed",
            summary="interleaved command event",
            payload={"commandId": "rtcmd_interleaved"},
            created_at="2026-06-08T00:00:06.050000Z",
        ),
        RuntimeControlEventInput(
            event_id="rtevt_interleaved_crash",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_crashed",
            stage="startup",
            status="failed",
            summary="legacy crash",
            payload={
                "reasonCode": "runtime_executor_crash_timeout",
                "executorId": "executor_1",
            },
            created_at="2026-06-08T00:00:06.100000Z",
        ),
    ):
        store.append_event(event)
    events_before = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.reason_code for decision in decisions] == [
        "runtime_executor_crash_timeout"
    ]
    assert store.get_run("runtime_run_1").status == "failed"
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == events_before


def test_recovery_pairs_legacy_decision_after_more_than_one_hundred_intervening_events(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_many_interleaved_expiry",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_lease_expired",
            stage="startup",
            status="failed",
            summary="legacy expiry",
            payload={"executorId": "executor_1", "attemptNo": lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        )
    )
    for index in range(101):
        store.append_event(
            RuntimeControlEventInput(
                event_id=f"rtevt_many_interleaved_{index}",
                runtime_run_id="runtime_run_1",
                event_type="runtime_command_accepted",
                stage="command",
                status="completed",
                summary="interleaved command event",
                payload={"commandId": f"rtcmd_interleaved_{index}"},
                created_at="2026-06-08T00:00:06.050000Z",
            )
        )
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_many_interleaved_crash",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_crashed",
            stage="startup",
            status="failed",
            summary="legacy crash",
            payload={
                "reasonCode": "runtime_executor_crash_timeout",
                "executorId": "executor_1",
            },
            created_at="2026-06-08T00:00:06.100000Z",
        )
    )
    events_before = store.list_events(
        runtime_run_id="runtime_run_1", after_seq=0, limit=200
    ).events

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.reason_code for decision in decisions] == [
        "runtime_executor_crash_timeout"
    ]
    assert store.get_run("runtime_run_1").status == "failed"
    assert store.list_events(
        runtime_run_id="runtime_run_1", after_seq=0, limit=200
    ).events == events_before


@pytest.mark.parametrize("legacy_case", ["cancel", "restored", "restore_failed"])
def test_recovery_reuses_legacy_decision_payload_shapes(
    tmp_path: Path,
    legacy_case: str,
) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeControlEventInput
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    initial_status = "cancellation_requested" if legacy_case == "cancel" else "running"
    _create_run(store, status=initial_status)
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    if legacy_case == "restored":
        store.write_checkpoint(
            RuntimeCheckpoint(
                checkpoint_id="rtcheckpoint_legacy_shape",
                runtime_run_id="runtime_run_1",
                stage="startup",
                round_no=None,
                safe_boundary="runtime_candidate_checkpoint",
                run_state={},
                source_plan={"sourceIds": ["cts"]},
                pending_commands=[],
                artifact_manifest_ref=None,
                schema_version="runtime-control-checkpoint/v1",
                created_at="2026-06-08T00:00:04.000000Z",
            ),
            executor_id="executor_1",
            attempt_no=lease.attempt_no,
        )
    elif legacy_case == "restore_failed":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE runtime_control_runs SET latest_checkpoint_id = ? WHERE runtime_run_id = ?",
                ("rtcheckpoint_missing", "runtime_run_1"),
            )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    event_type, event_status, decision_payload, expected_reason, expected_status, resume = {
        "cancel": (
            "runtime_run_cancelled",
            "completed",
            {
                "reasonCode": "runtime_cancel_after_executor_lost",
                "executorId": "executor_1",
            },
            "runtime_cancel_after_executor_lost",
            "cancelled",
            True,
        ),
        "restored": (
            "runtime_checkpoint_restored",
            "completed",
            {"checkpointId": "rtcheckpoint_legacy_shape"},
            "runtime_checkpoint_restored",
            "resume_requested",
            True,
        ),
        "restore_failed": (
            "runtime_checkpoint_restore_failed",
            "failed",
            {
                "checkpointId": "rtcheckpoint_missing",
                "reasonCode": "runtime_checkpoint_missing",
            },
            "runtime_checkpoint_missing",
            "failed",
            False,
        ),
    }[legacy_case]
    for event in (
        RuntimeControlEventInput(
            event_id=f"rtevt_{legacy_case}_expiry",
            runtime_run_id="runtime_run_1",
            event_type="runtime_executor_lease_expired",
            stage="startup",
            status="failed",
            summary="legacy expiry",
            payload={"executorId": "executor_1", "attemptNo": lease.attempt_no},
            created_at="2026-06-08T00:00:06.000000Z",
        ),
        RuntimeControlEventInput(
            event_id=f"rtevt_{legacy_case}_decision",
            runtime_run_id="runtime_run_1",
            event_type=event_type,
            stage="startup",
            status=event_status,
            summary="legacy decision",
            payload=decision_payload,
            created_at="2026-06-08T00:00:06.100000Z",
        ),
    ):
        store.append_event(event)
    events_before = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:07.000000Z",
    ).recover_start_timeouts(resume_recoverable=resume)

    assert [decision.reason_code for decision in decisions] == [expected_reason]
    assert store.get_run("runtime_run_1").status == expected_status
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == events_before


def test_recovery_attributes_multiple_legacy_expired_attempts_to_latest_owner(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    old_lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_old",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    latest_lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_latest",
        acquired_at="2026-06-08T00:00:07.000000Z",
        lease_expires_at="2026-06-08T00:00:10.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:11.000000Z")

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:12.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert old_lease.attempt_no == 1
    assert latest_lease.attempt_no == 2
    assert [decision.reason_code for decision in decisions] == ["runtime_executor_crash_timeout"]
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert events[0].payload["leaseId"] == latest_lease.lease_id
    assert events[0].payload["executorId"] == "executor_latest"
    assert events[0].payload["attemptNo"] == 2


def test_recovery_skips_legacy_expired_attempts_while_active_owner_exists(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_old",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    active = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_active",
        acquired_at="2026-06-08T00:00:07.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:12.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert decisions == []
    assert store.get_run("runtime_run_1").status == "running"
    assert store.list_active_executor_leases() == [active]


def test_recovery_does_not_attribute_old_expired_attempt_after_newer_released_attempt(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    old_lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_old",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.expire_executor_leases(now="2026-06-08T00:00:06.000000Z")
    latest_lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_latest",
        acquired_at="2026-06-08T00:00:07.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.release_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_latest",
        attempt_no=latest_lease.attempt_no,
        released_at="2026-06-08T00:00:08.000000Z",
        reason_code="runtime_executor_released",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:12.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert old_lease.attempt_no == 1
    assert latest_lease.attempt_no == 2
    assert decisions == []
    assert store.get_run("runtime_run_1").status == "running"
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == []
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == []


def test_heartbeat_commit_before_recovery_is_the_only_winner(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    heartbeat_has_lock = Event()
    allow_heartbeat_commit = Event()
    recovery_started = Event()
    recovery_result: list[object] = []

    def commit_heartbeat() -> None:
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE lease_id = ? AND status = 'active'
                """,
                (
                    "2026-06-08T00:00:04.000000Z",
                    "2026-06-08T00:00:10.000000Z",
                    lease.lease_id,
                ),
            )
            heartbeat_has_lock.set()
            assert allow_heartbeat_commit.wait(5)

    def recover() -> None:
        recovery_started.set()
        recovery_result.extend(
            RuntimeRecoveryService(
                store=store,
                now=lambda: "2026-06-08T00:00:06.000000Z",
            ).recover_start_timeouts(resume_recoverable=False)
        )

    heartbeat_thread = Thread(target=commit_heartbeat)
    heartbeat_thread.start()
    assert heartbeat_has_lock.wait(5)
    recovery_thread = Thread(target=recover)
    recovery_thread.start()
    assert recovery_started.wait(5)
    allow_heartbeat_commit.set()
    heartbeat_thread.join(5)
    recovery_thread.join(5)

    assert not heartbeat_thread.is_alive()
    assert not recovery_thread.is_alive()
    assert recovery_result == []
    [active] = store.list_active_executor_leases()
    assert active.lease_id == lease.lease_id
    assert active.heartbeat_at == "2026-06-08T00:00:04.000000Z"
    assert active.lease_expires_at == "2026-06-08T00:00:10.000000Z"


def test_recovery_write_lock_wins_and_late_heartbeat_cannot_revive_lease(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
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
    recovery_has_lock = Event()
    heartbeat_started = Event()
    allow_recovery_commit = Event()
    recovery_result: list[object] = []
    heartbeat_errors: list[RuntimeControlError] = []

    def hold_recovery(point: str) -> None:
        if point == "after_lease_update":
            recovery_has_lock.set()
            assert allow_recovery_commit.wait(5)

    def recover() -> None:
        recovery_result.extend(
            RuntimeRecoveryService(
                store=store,
                now=lambda: "2026-06-08T00:00:06.000000Z",
                fault_injector=hold_recovery,
            ).recover_start_timeouts(resume_recoverable=False)
        )

    def heartbeat() -> None:
        heartbeat_started.set()
        try:
            store.heartbeat_executor_lease(
                runtime_run_id="runtime_run_1",
                executor_id="executor_1",
                heartbeat_at="2026-06-08T00:00:04.000000Z",
                lease_expires_at="2026-06-08T00:00:10.000000Z",
            )
        except RuntimeControlError as exc:
            heartbeat_errors.append(exc)

    recovery_thread = Thread(target=recover)
    recovery_thread.start()
    assert recovery_has_lock.wait(5)
    heartbeat_thread = Thread(target=heartbeat)
    heartbeat_thread.start()
    assert heartbeat_started.wait(5)
    allow_recovery_commit.set()
    recovery_thread.join(5)
    heartbeat_thread.join(5)

    assert not recovery_thread.is_alive()
    assert not heartbeat_thread.is_alive()
    assert [decision.reason_code for decision in recovery_result] == ["runtime_executor_crash_timeout"]
    assert [error.reason_code for error in heartbeat_errors] == ["runtime_executor_stale"]
    assert store.list_active_executor_leases() == []
    assert store.get_run("runtime_run_1").status == "failed"


def test_heartbeat_at_existing_expiry_cannot_extend_lease(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.heartbeat_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            heartbeat_at="2026-06-08T00:00:05.000000Z",
            lease_expires_at="2026-06-08T00:00:10.000000Z",
        )

    assert exc_info.value.reason_code == "runtime_executor_lease_expired"
    [active] = store.list_active_executor_leases()
    assert active == lease


def test_durable_cancel_wins_even_when_checkpoint_is_corrupt_and_resume_allowed(tmp_path: Path) -> None:
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
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="cancellation_requested",
        updated_at="2026-06-08T00:00:04.000000Z",
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
    ).recover_start_timeouts(resume_recoverable=True)

    assert [decision.reason_code for decision in decisions] == ["runtime_cancel_after_executor_lost"]
    run = store.get_run("runtime_run_1")
    assert run.status == "cancelled"
    assert run.stop_reason_code == "runtime_cancel_after_executor_lost"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == [
        "runtime_executor_lease_expired",
        "runtime_run_cancelled",
    ]


def test_terminal_recovery_replay_preserves_terminal_truth(tmp_path: Path) -> None:
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
    service = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    )
    service.recover_start_timeouts(resume_recoverable=False)
    terminal_run = store.get_run("runtime_run_1")
    terminal_events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events

    replay = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:01:00.000000Z",
    ).recover_start_timeouts(resume_recoverable=True)

    assert replay == []
    assert store.get_run("runtime_run_1") == terminal_run
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == terminal_events


def test_recovery_revokes_expired_executor_authority_without_rewriting_terminal_run(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="completed",
        updated_at="2026-06-08T00:00:04.000000Z",
        completed_at="2026-06-08T00:00:04.000000Z",
    )
    terminal_run = store.get_run("runtime_run_1")
    terminal_events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events

    service = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    )
    assert service.recover_start_timeouts(resume_recoverable=True) == []
    assert service.recover_start_timeouts(resume_recoverable=False) == []

    assert store.get_run("runtime_run_1") == terminal_run
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == terminal_events
    assert store.list_active_executor_leases() == []
    with sqlite3.connect(store.path) as conn:
        stored = conn.execute(
            "SELECT status, released_at, reason_code FROM runtime_control_executor_leases WHERE lease_id = ?",
            (lease.lease_id,),
        ).fetchone()
    assert stored == ("expired", "2026-06-08T00:00:06.000000Z", "runtime_executor_lease_expired")


@pytest.mark.parametrize("status", ["queued", "paused", "resume_requested"])
def test_recovery_only_revokes_expired_lease_for_no_owner_nonterminal_state(
    tmp_path: Path,
    status: str,
) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status=status)
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_stale",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )
    run_before = store.get_run("runtime_run_1")

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts(resume_recoverable=True)

    assert decisions == []
    assert store.get_run("runtime_run_1") == run_before
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == []
    assert store.list_active_executor_leases() == []


def test_lease_only_cleanup_does_not_starve_later_owner_settlement(tmp_path: Path) -> None:
    from seektalent_runtime_control.recovery import RuntimeRecoveryService
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store, status="paused", runtime_run_id="runtime_run_paused")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_paused",
        executor_id="executor_stale",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:04.000000Z",
    )
    paused_before = store.get_run("runtime_run_paused")
    _create_run(store, status="running", runtime_run_id="runtime_run_owner")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_owner",
        executor_id="executor_owner",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:00:05.000000Z",
    )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-06-08T00:00:06.000000Z",
    ).recover_start_timeouts(resume_recoverable=False)

    assert [decision.runtime_run_id for decision in decisions] == ["runtime_run_owner"]
    assert store.get_run("runtime_run_paused") == paused_before
    assert store.get_run("runtime_run_owner").status == "failed"
    assert store.list_active_executor_leases() == []


def _create_run(store, *, status: str, runtime_run_id: str = "runtime_run_1") -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            agent_conversation_id=f"agent_conv_{runtime_run_id}",
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
