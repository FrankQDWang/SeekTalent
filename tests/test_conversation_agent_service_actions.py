from __future__ import annotations

from pathlib import Path


def test_agent_adapter_exposes_shared_service_action_names() -> None:
    from seektalent_conversation_agent.service_actions import AGENT_SERVICE_ACTION_NAMES

    assert AGENT_SERVICE_ACTION_NAMES == (
        "extract_requirements",
        "get_requirement_draft",
        "update_requirement_draft",
        "amend_requirement_draft_from_text",
        "resolve_requirement_review",
        "confirm_requirements",
        "start_workflow",
        "get_workflow_snapshot",
        "list_workflow_events",
        "request_pause",
        "request_cancel",
        "resume_workflow",
        "submit_next_round_requirement",
        "get_runtime_detail",
        "prepare_final_summary",
    )


def test_agent_service_action_adapter_reads_snapshot_and_events_through_runtime_control_store(tmp_path: Path) -> None:
    from seektalent_conversation_agent.service_actions import AgentServiceActionAdapter
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord, RuntimeRunSnapshot
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id="reqapproved_1",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-09T00:00:00.000000Z",
            updated_at="2026-06-09T00:00:00.000000Z",
            completed_at=None,
        )
    )
    event = store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_1",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_started",
            stage="runtime",
            round_no=1,
            source_id=None,
            status="completed",
            summary="run started",
            payload={"workbenchSessionId": "workbench_session_1"},
            workbench_event_global_seq=None,
            created_at="2026-06-09T00:00:01.000000Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_event_seq=1,
            snapshot={"progressSummary": "run started"},
            updated_at="2026-06-09T00:00:01.000000Z",
        ),
    )
    adapter = AgentServiceActionAdapter(runtime_store=store)

    snapshot = adapter.get_workflow_snapshot(runtime_run_id="runtime_run_1")
    events = adapter.list_workflow_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10)

    assert snapshot.runtime_run_id == "runtime_run_1"
    assert snapshot.latest_event_seq == event.event_seq
    assert events.events[0].event_id == event.event_id
    assert events.next_cursor == event.event_seq
