from __future__ import annotations

from pathlib import Path

from seektalent_runtime_control.models import RuntimeRunRecord
from tests.conversation_agent_test_support import build_service, save_approved_requirement


def test_command_and_next_round_requirement_transcript_is_grounded_in_runtime_control(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )
    approved = save_approved_requirement(runtime_store, conversation_id=conversation.conversation_id)
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id=conversation.conversation_id,
            workbench_session_id=None,
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
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
    service.store.link_runtime_run(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_1",
        workbench_session_id=None,
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-09T00:00:01.000000Z",
    )

    command = service.request_workflow_command(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id="runtime_run_1",
        command_type="pause",
        idempotency_key="pause-1",
    )
    next_round = service.submit_next_round_requirement(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id="runtime_run_1",
        text="增加平台治理经验",
        target_section_hint="must_have_capabilities",
        idempotency_key="next-round-1",
    )

    assert command.messages[-1].payload["command"]["status"] == "accepted"
    assert any("第 2 轮开始前生效" in message.text for message in next_round.messages)
