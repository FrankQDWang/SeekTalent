from __future__ import annotations

from pathlib import Path


def test_retention_compacts_old_terminal_event_payloads_without_losing_event_metadata(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_runtime_control.retention import RuntimeRetentionService

    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_1", status="completed")
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_old",
            runtime_run_id="runtime_run_1",
            event_type="runtime_source_result",
            stage="source_result",
            round_no=1,
            source_id="cts",
            status="completed",
            summary="source result summary",
            payload={"rawProviderPayload": "secret", "candidateCount": 3},
            workbench_event_global_seq=None,
            created_at="2026-05-01T00:00:00.000000Z",
        )
    )

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        event_payload_retention_days=7,
    ).cleanup(batch_size=100)

    event = store.get_event(runtime_run_id="runtime_run_1", event_id="rtevt_old")
    assert result.compacted_event_payload_count == 1
    assert event.event_type == "runtime_source_result"
    assert event.summary == "source result summary"
    assert event.payload == {"compacted": True, "sourceId": "cts"}


def test_retention_preserves_active_runs_pending_commands_and_latest_checkpoint(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeControlEventInput
    from seektalent_runtime_control.retention import RuntimeRetentionService

    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_1", status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-05-01T00:00:00.000000Z",
        lease_expires_at="2026-07-01T00:00:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_latest",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="after_scoring",
            run_state={"round": 2},
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-05-01T00:00:00.000000Z",
        ),
        executor_id="executor_1",
    )
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_active_old",
            runtime_run_id="runtime_run_1",
            event_type="runtime_source_result",
            stage="source_result",
            round_no=2,
            source_id="cts",
            status="completed",
            summary="active payload must remain",
            payload={"rawProviderPayload": "still-needed"},
            workbench_event_global_seq=None,
            created_at="2026-05-01T00:00:00.000000Z",
        )
    )
    command = RuntimeCommandService(store=store, now=lambda: "2026-05-01T00:00:01.000000Z").request_pause(
        runtime_run_id="runtime_run_1",
        requested_by="agent",
        idempotency_key="pause-1",
    )

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        event_payload_retention_days=7,
        checkpoint_retention_days=7,
    ).cleanup(batch_size=100)

    assert result.compacted_event_payload_count == 0
    assert store.get_event(runtime_run_id="runtime_run_1", event_id="rtevt_active_old").payload == {
        "rawProviderPayload": "still-needed"
    }
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_1").checkpoint_id == "rtcheckpoint_latest"
    assert store.get_command(command.command_id).status == "accepted"


def test_retention_deletes_old_terminal_checkpoints_and_final_summaries(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeControlEventInput, RuntimeRunSnapshot
    from seektalent_runtime_control.retention import RuntimeRetentionService

    store = _store_with_run(tmp_path, runtime_run_id="runtime_run_1", status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-05-01T00:00:00.000000Z",
        lease_expires_at="2026-07-01T00:00:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_old_terminal",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="after_scoring",
            run_state={"round": 2},
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-05-01T00:00:00.000000Z",
        ),
        executor_id="executor_1",
    )
    event = store.append_executor_event(
        RuntimeControlEventInput(
            event_id="rtevt_completed",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_completed",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="run completed",
            payload={},
            workbench_event_global_seq=None,
            created_at="2026-05-01T00:00:01.000000Z",
        ),
        executor_id="executor_1",
        run_status="completed",
        completed_at="2026-05-01T00:00:01.000000Z",
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_event_seq=1,
            snapshot={},
            updated_at="2026-05-01T00:00:01.000000Z",
        ),
    )
    RuntimeDetailService(
        store=store,
        summary_id_factory=lambda: "rtfinalsummary_old",
        now=lambda: "2026-05-01T00:00:02.000000Z",
    ).prepare_final_summary(
        runtime_run_id="runtime_run_1",
        user_instruction=None,
        source_snapshot_event_seq=event.event_seq,
        idempotency_key="summary-old",
    )
    store.release_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        released_at="2026-05-01T00:00:03.000000Z",
    )

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        checkpoint_retention_days=7,
        final_summary_retention_days=7,
    ).cleanup(batch_size=100)

    assert result.deleted_checkpoint_count == 1
    assert result.deleted_final_summary_count == 1
    assert store.get_checkpoint(
        runtime_run_id="runtime_run_1",
        checkpoint_id="rtcheckpoint_old_terminal",
    ) is None
    assert store.get_final_summary_by_idempotency(
        runtime_run_id="runtime_run_1",
        idempotency_key="summary-old",
    ) is None


def _store_with_run(tmp_path: Path, *, runtime_run_id: str, status: str):
    from seektalent_runtime_control.models import RuntimeRunRecord
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id=f"reqapproved_{runtime_run_id}",
            status=status,
            current_stage="finalization" if status == "completed" else "round",
            current_round=None if status == "completed" else 2,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-05-01T00:00:00.000000Z",
            updated_at="2026-05-01T00:00:00.000000Z",
            completed_at="2026-05-01T00:00:00.000000Z" if status == "completed" else None,
        )
    )
    return store
