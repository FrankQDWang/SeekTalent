from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Event, Thread

import pytest


@pytest.mark.parametrize(
    "fault_point",
    ["after_lease_update", "after_first_event", "before_run_transition"],
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
