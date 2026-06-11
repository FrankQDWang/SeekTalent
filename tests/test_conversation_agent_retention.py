from __future__ import annotations

from pathlib import Path

from tests.conversation_agent_test_support import build_service


def test_archive_and_compaction_do_not_delete_transcript_or_activity_state(tmp_path: Path) -> None:
    service, _conversation_store, _runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )
    service.store.append_message(
        conversation_id=conversation.conversation_id,
        role="user",
        message_type="user_text",
        text="需要 Python API。",
        payload={},
        created_at="2026-06-09T00:00:01.000000Z",
    )
    service.compact_context(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        trigger_reason_code="agent_compaction_trigger_budget",
    )
    service.archive_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )

    reopened = service.reopen_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )

    assert reopened.conversation_reopen_state.is_archived is True
    assert len(reopened.messages) == 1
    assert reopened.activity_items[0].activity_type == "context_compaction"
