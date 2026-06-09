from __future__ import annotations

from pathlib import Path


def test_runtime_detail_reflection_cites_event_and_redacts_unsafe_artifacts(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.models import RuntimeControlEventInput

    store = _store_with_run(tmp_path, status="running")
    event = store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_reflection_1",
            runtime_run_id="runtime_run_1",
            event_type="runtime_reflection_completed",
            stage="reflection",
            round_no=2,
            source_id=None,
            status="completed",
            summary="Narrow keywords toward distributed systems.",
            payload={
                "facts": [{"label": "Reflection", "value": "Focus on distributed systems."}],
                "artifactRefs": [
                    {"artifactRefId": "artifact_safe_1", "visibility": "safe", "safeUri": "artifact://safe/1"},
                    {
                        "artifactRefId": "artifact_raw_1",
                        "visibility": "private",
                        "rawProviderPayload": "secret",
                    },
                ],
            },
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:01.000000Z",
        )
    )

    detail = RuntimeDetailService(store=store).get_runtime_detail(
        runtime_run_id="runtime_run_1",
        kind="reflection",
        round_no=2,
        include_artifacts=True,
    )

    assert detail.reason_code is None
    assert detail.source_event_ids == [event.event_id]
    assert detail.facts == [
        {
            "label": "Reflection",
            "value": "Focus on distributed systems.",
            "sourceEventId": "rtevt_reflection_1",
        }
    ]
    assert detail.artifact_refs == [
        {"artifactRefId": "artifact_safe_1", "safeUri": "artifact://safe/1", "visibility": "safe"}
    ]

    detail_without_artifacts = RuntimeDetailService(store=store).get_runtime_detail(
        runtime_run_id="runtime_run_1",
        kind="reflection",
        round_no=2,
        include_artifacts=False,
    )

    assert detail_without_artifacts.artifact_refs == []


def test_runtime_detail_checkpoint_cites_checkpoint_and_returns_missing_reason(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.models import RuntimeCheckpoint

    store = _store_with_run(tmp_path, status="running")
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-06-08T00:00:00.000000Z",
        lease_expires_at="2026-06-08T00:01:00.000000Z",
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_1",
            runtime_run_id="runtime_run_1",
            stage="round",
            round_no=2,
            safe_boundary="after_scoring",
            run_state={"round": 2},
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref="artifact_manifest_1",
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        executor_id="executor_1",
    )

    service = RuntimeDetailService(store=store)
    detail = service.get_runtime_detail(
        runtime_run_id="runtime_run_1",
        kind="checkpoint",
        checkpoint_id="rtcheckpoint_1",
        include_artifacts=False,
    )
    missing = service.get_runtime_detail(
        runtime_run_id="runtime_run_1",
        kind="checkpoint",
        checkpoint_id="rtcheckpoint_missing",
        include_artifacts=False,
    )

    assert detail.checkpoint_ids == ["rtcheckpoint_1"]
    assert detail.facts[0]["value"] == "after_scoring"
    assert detail.artifact_refs == []
    assert missing.reason_code == "runtime_checkpoint_not_found"


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
