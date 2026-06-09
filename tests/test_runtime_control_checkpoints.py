from __future__ import annotations

from pathlib import Path

import pytest


def test_checkpoint_write_requires_active_executor_and_updates_latest_pointer(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_run(store)
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )

    checkpoint = RuntimeCheckpoint(
        checkpoint_id="rtcheckpoint_1",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=2,
        safe_boundary="after_round_controller",
        run_state={"round": 2, "privateState": "persisted but not exposed as RunState"},
        source_plan={"sourceIds": ["cts", "custom_source"]},
        pending_commands=[],
        artifact_manifest_ref="artifact_manifest_1",
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-06-08T00:00:02.000000Z",
    )

    saved = store.write_checkpoint(checkpoint, executor_id="executor_1")

    assert saved.checkpoint_id == "rtcheckpoint_1"
    assert store.get_run("runtime_run_1").latest_checkpoint_id == "rtcheckpoint_1"
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_1") == checkpoint

    with pytest.raises(RuntimeControlError) as exc_info:
        store.write_checkpoint(
            checkpoint.model_copy(update={"checkpoint_id": "rtcheckpoint_2"}),
            executor_id="executor_stale",
        )

    assert exc_info.value.reason_code == "runtime_executor_stale"


def _create_run(store) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status="running",
            current_stage="round",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts", "custom_source"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00.000000Z",
            updated_at="2026-06-08T00:00:00.000000Z",
            completed_at=None,
        )
    )
