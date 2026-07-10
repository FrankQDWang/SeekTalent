from __future__ import annotations

from pathlib import Path

import pytest


def test_stale_executor_attempt_cannot_write_event_checkpoint_stage_output_or_completion(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeStageOutputInput

    store = _store(tmp_path)
    _create_run(store, status="running")
    first = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_same",
        acquired_at="2026-06-17T00:00:00.000000Z",
        lease_expires_at="2026-06-17T00:00:10.000000Z",
    )
    store.expire_executor_leases(now="2026-06-17T00:00:11.000000Z")
    second = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_same",
        acquired_at="2026-06-17T00:00:12.000000Z",
        lease_expires_at="2026-06-17T00:01:12.000000Z",
    )

    assert first.attempt_no == 1
    assert second.attempt_no == 2

    with pytest.raises(RuntimeControlError) as event_exc:
        store.append_executor_event(
            _event("rtevt_stale", created_at="2026-06-17T00:00:13.000000Z"),
            executor_id="executor_same",
            attempt_no=first.attempt_no,
            run_status="running",
        )
    with pytest.raises(RuntimeControlError) as checkpoint_exc:
        store.write_checkpoint(
            RuntimeCheckpoint(
                checkpoint_id="rtcheckpoint_stale",
                runtime_run_id="runtime_run_1",
                stage="round",
                round_no=1,
                safe_boundary="after_round_controller",
                run_state={"round": 1},
                source_plan={"sourceIds": ["cts"]},
                pending_commands=[],
                artifact_manifest_ref=None,
                schema_version="runtime-control-checkpoint/v1",
                created_at="2026-06-17T00:00:13.000000Z",
            ),
            executor_id="executor_same",
            attempt_no=first.attempt_no,
        )
    with pytest.raises(RuntimeControlError) as output_exc:
        store.save_stage_output(
            RuntimeStageOutputInput(
                output_id="rtoutput_stale",
                runtime_run_id="runtime_run_1",
                stage="source_result",
                node_id="cts",
                round_no=1,
                output_kind="runtime_public_source_result",
                schema_version="runtime-public-stage-output/v2",
                output={"candidateCount": 1},
                created_at="2026-06-17T00:00:13.000000Z",
            ),
            executor_id="executor_same",
            attempt_no=first.attempt_no,
        )
    with pytest.raises(RuntimeControlError) as completion_exc:
        store.append_executor_event(
            _event("rtevt_complete_stale", created_at="2026-06-17T00:00:13.000000Z"),
            executor_id="executor_same",
            attempt_no=first.attempt_no,
            run_status="completed",
            completed_at="2026-06-17T00:00:13.000000Z",
        )

    assert event_exc.value.reason_code == "runtime_executor_stale"
    assert checkpoint_exc.value.reason_code == "runtime_executor_stale"
    assert output_exc.value.reason_code == "runtime_executor_stale"
    assert completion_exc.value.reason_code == "runtime_executor_stale"
    assert store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events == []
    assert store.get_run("runtime_run_1").status == "running"

    event = store.append_executor_event(
        _event("rtevt_current", created_at="2026-06-17T00:00:14.000000Z"),
        executor_id="executor_same",
        attempt_no=second.attempt_no,
        run_status="running",
    )

    assert event.event_seq == 1


def test_clock_rollback_does_not_reanimate_expired_or_completed_leases(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store(tmp_path)
    _create_run(store, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-17T00:00:10.000000Z",
        lease_expires_at="2026-06-17T00:01:10.000000Z",
    )
    refreshed = store.heartbeat_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        attempt_no=lease.attempt_no,
        heartbeat_at="2026-06-17T00:00:05.000000Z",
        lease_expires_at="2026-06-17T00:00:35.000000Z",
    )

    assert refreshed.heartbeat_at == "2026-06-17T00:00:10.000000Z"
    assert refreshed.lease_expires_at == "2026-06-17T00:01:10.000000Z"

    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="completed",
        completed_at="2026-06-17T00:00:20.000000Z",
        updated_at="2026-06-17T00:00:20.000000Z",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.update_run_status(
            runtime_run_id="runtime_run_1",
            status="running",
            updated_at="2026-06-17T00:00:01.000000Z",
        )

    assert exc_info.value.reason_code == "runtime_run_invalid_transition"
    assert store.get_run("runtime_run_1").status == "completed"


def _store(tmp_path: Path):
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    return store


def _create_run(store, *, status: str) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            run_intent_id="intent_1",
            start_idempotency_key="start_1",
            run_kind="primary",
            agent_conversation_id="agent_conv_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status=status,
            current_stage=status,
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-17T00:00:00.000000Z",
            updated_at="2026-06-17T00:00:00.000000Z",
            completed_at=None,
        )
    )


def _event(event_id: str, *, created_at: str):
    from seektalent_runtime_control.models import RuntimeControlEventInput

    return RuntimeControlEventInput(
        event_id=event_id,
        runtime_run_id="runtime_run_1",
        event_type="runtime_progress",
        stage="runtime",
        round_no=None,
        source_id=None,
        status="completed",
        summary="progress",
        payload={},
        created_at=created_at,
    )
