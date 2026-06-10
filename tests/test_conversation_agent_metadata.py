from __future__ import annotations

from pathlib import Path

import pytest

from tests.conversation_agent_test_support import build_service


def test_metadata_list_reopen_rename_archive_and_unarchive_are_backend_owned(tmp_path: Path) -> None:
    service, _conversation_store, _runtime_store = build_service(tmp_path)
    created = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )

    renamed = service.rename_conversation(
        conversation_id=created.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
    )
    archived = service.archive_conversation(
        conversation_id=created.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )
    reopened = service.reopen_conversation(
        conversation_id=created.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )

    assert renamed.title == "Python 平台负责人"
    assert archived.is_archived is True
    assert service.list_conversations(owner_user_id="user_1", workspace_id="workspace_1") == []
    assert reopened.conversation_reopen_state.title == "Python 平台负责人"
    assert reopened.conversation_reopen_state.is_archived is True
    assert reopened.conversation_reopen_state.allowed_actions == ["unarchive"]
    unarchived = service.unarchive_conversation(
        conversation_id=created.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )
    assert unarchived.is_archived is False
    assert service.list_conversations(owner_user_id="user_1", workspace_id="workspace_1")[0].title == (
        "Python 平台负责人"
    )


def test_active_running_conversation_cannot_be_archived(tmp_path: Path) -> None:
    service, _conversation_store, _runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )
    service.store.update_conversation_status(
        conversation_id=conversation.conversation_id,
        status="running",
        updated_at="2026-06-09T00:00:10.000000Z",
    )

    with pytest.raises(Exception) as exc_info:
        service.archive_conversation(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
        )

    assert getattr(exc_info.value, "reason_code") == "conversation_archive_active_runtime"
