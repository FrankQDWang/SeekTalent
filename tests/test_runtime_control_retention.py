from __future__ import annotations

from pathlib import Path

import pytest

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import (
    RuntimeCheckpoint,
    RuntimeCommand,
    RuntimeControlEventInput,
    RuntimeFinalSummary,
    RuntimeRunRecord,
    RuntimeRunSnapshot,
    RuntimeStageOutputInput,
)
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.retention import RuntimeControlRetentionPolicy, RuntimeRetentionService
from seektalent_runtime_control.store import RuntimeControlStore
from tests.test_runtime_control_requirements import requirement_sheet


def test_retention_dry_run_reports_deletable_debris_without_mutating_product_state(tmp_path: Path) -> None:
    store = _store_with_terminal_run(tmp_path, runtime_run_id="runtime_run_1")
    _seed_terminal_debris(store, runtime_run_id="runtime_run_1")

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        policy=RuntimeControlRetentionPolicy(
            terminal_run_min_age_days=7,
            developer_event_ttl_days=7,
            internal_event_ttl_days=7,
            checkpoint_ttl_days=7,
            lease_ttl_days=7,
            command_ttl_days=7,
            non_required_stage_output_ttl_days=7,
            database_budget_bytes=1,
        ),
    ).cleanup(dry_run=True)

    assert result.dry_run is True
    assert result.deleted_nonpublic_event_count == 0
    assert result.stats.nonpublic_event_count == 2
    assert result.stats.checkpoint_count == 2
    assert result.stats.executor_lease_count == 1
    assert result.stats.command_count == 1
    assert result.stats.stage_output_count == 1
    assert result.stats.final_summary_count == 0
    assert result.stats.nonpublic_event_estimated_bytes > 0
    assert result.stats.stage_output_estimated_bytes > 0
    assert result.stats.total_estimated_bytes > 0
    assert result.stats.database_size_bytes > 0
    assert result.stats.database_budget_bytes == 1
    assert result.stats.over_database_budget is True
    assert store.get_event(runtime_run_id="runtime_run_1", event_id="rtevt_developer_old").payload == {
        "debug": True
    }
    assert store.get_final_summary_by_idempotency(
        runtime_run_id="runtime_run_1",
        idempotency_key="summary-1",
    ) is not None


def test_retention_prunes_nonproduct_rows_but_preserves_public_timeline_and_terminal_summary(tmp_path: Path) -> None:
    store = _store_with_terminal_run(tmp_path, runtime_run_id="runtime_run_1")
    _seed_terminal_debris(store, runtime_run_id="runtime_run_1")
    store.save_approved_requirement(
        ApprovedRequirementRevision(
            approved_requirement_revision_id="reqapproved_runtime_run_1",
            draft_revision_id=None,
            base_approved_requirement_revision_id=None,
            source_amendment_id=None,
            agent_conversation_id="agent_conv_1",
            requirement_sheet=requirement_sheet(),
            selected_item_ids=[],
            deselected_item_ids=[],
            created_at="2026-05-01T00:00:00.000000Z",
        ),
        idempotency_key="approved-runtime-run-1",
    )
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_snapshot",
            runtime_run_id="runtime_run_1",
            event_type="runtime_snapshot",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="snapshot stays",
            payload={},
            visibility="public",
            created_at="2026-05-01T00:00:11.000000Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_event_seq=4,
            snapshot={"finalCandidateIds": ["candidate_1"]},
            updated_at="2026-05-01T00:00:11.000000Z",
        ),
    )

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        policy=RuntimeControlRetentionPolicy(
            terminal_run_min_age_days=7,
            developer_event_ttl_days=7,
            internal_event_ttl_days=7,
            checkpoint_ttl_days=7,
            lease_ttl_days=7,
            command_ttl_days=7,
            non_required_stage_output_ttl_days=7,
            batch_size=100,
        ),
    ).cleanup()

    assert result.deleted_nonpublic_event_count == 2
    assert result.deleted_checkpoint_count == 2
    assert result.deleted_executor_lease_count == 1
    assert result.deleted_command_count == 1
    assert result.deleted_stage_output_count == 1
    assert store.list_public_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events[0].event_id == (
        "rtevt_public_old"
    )
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_1") is None
    assert store.get_checkpoint(runtime_run_id="runtime_run_1", checkpoint_id="rtcheckpoint_old") is None
    assert store.get_final_summary_by_idempotency(
        runtime_run_id="runtime_run_1",
        idempotency_key="summary-1",
    ) is not None
    assert store.get_snapshot(runtime_run_id="runtime_run_1").snapshot == {"finalCandidateIds": ["candidate_1"]}
    assert store.get_approved_requirement("reqapproved_runtime_run_1").requirement_sheet.job_title == "Python 后端工程师"
    assert [item.output_id for item in store.list_stage_outputs(runtime_run_id="runtime_run_1")] == [
        "rtoutput_required"
    ]
    with pytest.raises(RuntimeControlError):
        store.get_command("rtcmd_old_applied")

    second = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        policy=RuntimeControlRetentionPolicy(batch_size=100),
    ).cleanup()
    assert second.total_deleted_count == 0


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "starting",
        "running",
        "pause_requested",
        "paused",
        "resume_requested",
        "cancellation_requested",
    ],
)
def test_retention_never_cleans_active_or_nonterminal_runs(tmp_path: Path, status: str) -> None:
    store = _store_with_active_run(tmp_path, runtime_run_id="runtime_run_active", status=status)
    _seed_active_debris(store, runtime_run_id="runtime_run_active")

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        policy=RuntimeControlRetentionPolicy(
            terminal_run_min_age_days=0,
            developer_event_ttl_days=0,
            internal_event_ttl_days=0,
            checkpoint_ttl_days=0,
            lease_ttl_days=0,
            command_ttl_days=0,
            non_required_stage_output_ttl_days=0,
        ),
    ).cleanup()

    assert result.total_deleted_count == 0
    assert store.get_event(runtime_run_id="runtime_run_active", event_id="rtevt_active_debug").payload == {
        "debug": True
    }
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_active").checkpoint_id == "rtcheckpoint_active"
    assert store.get_command("rtcmd_active_pending").status == "accepted"


def test_retention_skips_terminal_runs_that_still_have_active_leases(tmp_path: Path) -> None:
    store = _store_with_terminal_run(tmp_path, runtime_run_id="runtime_run_leased")
    _seed_terminal_debris(store, runtime_run_id="runtime_run_leased")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_leased",
        executor_id="executor_active",
        acquired_at="2026-05-01T00:00:00.000000Z",
        lease_expires_at="2026-07-01T00:00:00.000000Z",
    )

    result = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        policy=RuntimeControlRetentionPolicy(
            terminal_run_min_age_days=7,
            developer_event_ttl_days=7,
            internal_event_ttl_days=7,
            checkpoint_ttl_days=7,
            lease_ttl_days=7,
            command_ttl_days=7,
            non_required_stage_output_ttl_days=7,
        ),
    ).cleanup()

    assert result.total_deleted_count == 0
    assert store.get_event(runtime_run_id="runtime_run_leased", event_id="rtevt_developer_old") is not None
    assert store.get_checkpoint(runtime_run_id="runtime_run_leased", checkpoint_id="rtcheckpoint_old") is not None
    assert store.get_command("rtcmd_old_applied").status == "applied"


def test_legacy_cleanup_helpers_respect_explicit_cutoffs(tmp_path: Path) -> None:
    store = _store_with_terminal_run(tmp_path, runtime_run_id="runtime_run_legacy")
    _seed_terminal_debris(store, runtime_run_id="runtime_run_legacy")

    compacted = store.compact_terminal_event_payloads(older_than="2026-06-08T00:00:00.000000Z", batch_size=100)
    deleted_checkpoints = store.delete_terminal_checkpoints(older_than="2026-06-08T00:00:00.000000Z", batch_size=100)
    deleted_summaries = store.delete_terminal_final_summaries(older_than="2026-06-08T00:00:00.000000Z", batch_size=100)
    deleted_outputs = store.delete_terminal_stage_outputs(older_than="2026-06-08T00:00:00.000000Z", batch_size=100)

    assert compacted == 2
    assert deleted_checkpoints == 2
    assert deleted_summaries == 1
    assert deleted_outputs == 1
    assert store.get_event(runtime_run_id="runtime_run_legacy", event_id="rtevt_public_old").payload == {
        "candidateCount": 3
    }
    assert store.get_event(runtime_run_id="runtime_run_legacy", event_id="rtevt_developer_old").payload == {
        "compacted": True,
        "sourceId": None,
    }
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_legacy") is None
    assert store.get_final_summary_by_idempotency(
        runtime_run_id="runtime_run_legacy",
        idempotency_key="summary-1",
    ) is None
    assert [item.output_id for item in store.list_stage_outputs(runtime_run_id="runtime_run_legacy")] == [
        "rtoutput_required"
    ]


def test_retention_stats_are_exact_when_cleanup_batch_is_smaller_than_candidates(tmp_path: Path) -> None:
    store = _store_with_terminal_run(tmp_path, runtime_run_id="runtime_run_many")
    _seed_terminal_debris(store, runtime_run_id="runtime_run_many")
    for index in range(3):
        store.append_event(
            RuntimeControlEventInput(
                event_id=f"rtevt_extra_internal_{index}",
                runtime_run_id="runtime_run_many",
                event_type="runtime_internal_note",
                stage="debug",
                round_no=None,
                source_id=None,
                status="completed",
                summary="internal payload can go",
                payload={"internal": index},
                visibility="internal",
                created_at=f"2026-05-01T00:00:2{index}.000000Z",
            )
        )
    service = RuntimeRetentionService(
        store=store,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        policy=RuntimeControlRetentionPolicy(
            terminal_run_min_age_days=7,
            developer_event_ttl_days=7,
            internal_event_ttl_days=7,
            checkpoint_ttl_days=7,
            lease_ttl_days=7,
            command_ttl_days=7,
            non_required_stage_output_ttl_days=7,
            batch_size=2,
        ),
    )

    first = service.cleanup(dry_run=True)
    second = service.cleanup()
    third = service.cleanup()
    fourth = service.cleanup()
    fifth = service.cleanup()

    assert first.stats.nonpublic_event_count == 5
    assert second.deleted_nonpublic_event_count == 2
    assert third.deleted_nonpublic_event_count == 2
    assert fourth.deleted_nonpublic_event_count == 1
    assert fifth.deleted_nonpublic_event_count == 0


def _store_with_terminal_run(tmp_path: Path, *, runtime_run_id: str) -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id=f"reqapproved_{runtime_run_id}",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_checkpoint_id="rtcheckpoint_latest",
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-05-01T00:00:00.000000Z",
            updated_at="2026-05-01T00:00:00.000000Z",
            completed_at="2026-05-01T00:00:00.000000Z",
        )
    )
    return store


def _store_with_active_run(tmp_path: Path, *, runtime_run_id: str, status: str = "running") -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id=f"reqapproved_{runtime_run_id}",
            status=status,
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-05-01T00:00:00.000000Z",
            updated_at="2026-05-01T00:00:00.000000Z",
            completed_at=None,
        )
    )
    return store


def _seed_terminal_debris(store: RuntimeControlStore, *, runtime_run_id: str) -> None:
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_public_old",
            runtime_run_id=runtime_run_id,
            event_type="runtime_public_progress",
            stage="source",
            round_no=1,
            source_id="cts",
            status="completed",
            summary="public timeline stays",
            payload={"candidateCount": 3},
            visibility="public",
            created_at="2026-05-01T00:00:01.000000Z",
        )
    )
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_developer_old",
            runtime_run_id=runtime_run_id,
            event_type="runtime_debug_note",
            stage="debug",
            round_no=None,
            source_id=None,
            status="completed",
            summary="developer payload can go",
            payload={"debug": True},
            visibility="developer",
            created_at="2026-05-01T00:00:02.000000Z",
        )
    )
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_internal_old",
            runtime_run_id=runtime_run_id,
            event_type="runtime_internal_note",
            stage="debug",
            round_no=None,
            source_id=None,
            status="completed",
            summary="internal payload can go",
            payload={"internal": True},
            visibility="internal",
            created_at="2026-05-01T00:00:03.000000Z",
        )
    )
    _insert_checkpoint(store, runtime_run_id=runtime_run_id, checkpoint_id="rtcheckpoint_old", created_at="2026-05-01T00:00:04.000000Z")
    _insert_checkpoint(store, runtime_run_id=runtime_run_id, checkpoint_id="rtcheckpoint_latest", created_at="2026-05-01T00:00:05.000000Z")
    _insert_released_lease(store, runtime_run_id=runtime_run_id)
    store.save_command(
        RuntimeCommand(
            command_id="rtcmd_old_applied",
            runtime_run_id=runtime_run_id,
            command_type="pause",
            payload={},
            status="applied",
            conflict_group="lifecycle",
            idempotency_key="pause-old",
            requested_by="agent",
            requested_at="2026-05-01T00:00:06.000000Z",
            applied_at="2026-05-01T00:00:07.000000Z",
        )
    )
    store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtoutput_debug",
            runtime_run_id=runtime_run_id,
            stage="debug",
            output_kind="debug_trace",
            schema_version="debug_trace/v1",
            output={"debug": True},
            created_at="2026-05-01T00:00:08.000000Z",
        )
    )
    store.save_stage_output(
        RuntimeStageOutputInput(
            output_id="rtoutput_required",
            runtime_run_id=runtime_run_id,
            stage="finalization",
            output_kind="final_shortlist",
            schema_version="final_shortlist/v1",
            output={"candidateIds": ["candidate_1"]},
            created_at="2026-05-01T00:00:09.000000Z",
        )
    )
    store.save_final_summary(
        RuntimeFinalSummary(
            summary_id="rtfinalsummary_1",
            runtime_run_id=runtime_run_id,
            status="completed",
            summary="final summary stays",
            facts=[],
            source_event_ids=["rtevt_public_old"],
            source_snapshot_event_seq=3,
            latest_snapshot_event_seq=3,
            user_instruction=None,
            created_at="2026-05-01T00:00:10.000000Z",
        ),
        idempotency_key="summary-1",
    )


def _seed_active_debris(store: RuntimeControlStore, *, runtime_run_id: str) -> None:
    store.acquire_executor_lease(
        runtime_run_id=runtime_run_id,
        executor_id="executor_1",
        acquired_at="2026-05-01T00:00:00.000000Z",
        lease_expires_at="2026-07-01T00:00:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_active",
            runtime_run_id=runtime_run_id,
            stage="runtime",
            round_no=1,
            safe_boundary="after_source",
            run_state={"round": 1},
            source_plan={},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-05-01T00:00:01.000000Z",
        ),
        executor_id="executor_1",
    )
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_active_debug",
            runtime_run_id=runtime_run_id,
            event_type="runtime_debug_note",
            stage="debug",
            round_no=None,
            source_id=None,
            status="completed",
            summary="active debug stays",
            payload={"debug": True},
            visibility="developer",
            created_at="2026-05-01T00:00:02.000000Z",
        )
    )
    store.save_command(
        RuntimeCommand(
            command_id="rtcmd_active_pending",
            runtime_run_id=runtime_run_id,
            command_type="pause",
            payload={},
            status="accepted",
            conflict_group="lifecycle",
            idempotency_key="pause-active",
            requested_by="agent",
            requested_at="2026-05-01T00:00:03.000000Z",
        )
    )


def _insert_checkpoint(
    store: RuntimeControlStore,
    *,
    runtime_run_id: str,
    checkpoint_id: str,
    created_at: str,
) -> None:
    with store._connect() as conn, conn:
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                run_state_json, source_plan_json, pending_commands_json,
                artifact_manifest_ref, schema_version, created_at
            )
            VALUES (?, ?, 'runtime', 1, 'after_source', '{}', '{}', '[]', NULL, ?, ?)
            """,
            (checkpoint_id, runtime_run_id, "runtime-control-checkpoint/v1", created_at),
        )


def _insert_released_lease(store: RuntimeControlStore, *, runtime_run_id: str) -> None:
    with store._connect() as conn, conn:
        conn.execute(
            """
            INSERT INTO runtime_control_executor_leases (
                lease_id, runtime_run_id, executor_id, attempt_no, status,
                acquired_at, heartbeat_at, lease_expires_at, released_at, reason_code
            )
            VALUES (
                'rtlease_old', ?, 'executor_old', 1, 'released',
                '2026-05-01T00:00:00.000000Z', NULL,
                '2026-05-01T00:01:00.000000Z',
                '2026-05-01T00:02:00.000000Z', 'completed'
            )
            """,
            (runtime_run_id,),
        )
