from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.settings_factory import make_settings


def test_settings_resolves_conversation_agent_db_path_under_workspace_root(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path))

    assert settings.conversation_agent_path == tmp_path / ".seektalent" / "conversation_agent.sqlite3"


def test_conversation_store_initializes_empty_db_and_reopens_idempotently(tmp_path: Path) -> None:
    from seektalent_conversation_agent.store import CONVERSATION_AGENT_SCHEMA_VERSION, ConversationStore

    db_path = tmp_path / "nested" / "conversation_agent.sqlite3"
    store = ConversationStore(db_path)

    store.initialize()
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'agent_%'"
            )
        }

    assert version == CONVERSATION_AGENT_SCHEMA_VERSION
    assert {
        "agent_conversations",
        "agent_transcript_messages",
        "agent_transcript_activity_items",
        "agent_tool_calls",
        "agent_runtime_links",
        "agent_context_summaries",
        "agent_context_compactions",
    } <= tables


def test_conversation_create_reopen_rename_archive_and_messages_are_persisted(tmp_path: Path) -> None:
    from seektalent_conversation_agent.store import ConversationStore

    store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    store.initialize()

    conversation = store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
        created_at="2026-06-09T00:00:00.000000Z",
    )
    message = store.append_message(
        conversation_id=conversation.conversation_id,
        role="user",
        message_type="user_text",
        text="需要 Python API 和 Kafka",
        payload={"source": "jd"},
        created_at="2026-06-09T00:00:01.000000Z",
    )
    renamed = store.rename_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
        updated_at="2026-06-09T00:00:02.000000Z",
    )
    archived = store.archive_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        archived_at="2026-06-09T00:00:03.000000Z",
    )
    reopened = store.reopen_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        opened_at="2026-06-09T00:00:04.000000Z",
    )

    assert message.message_seq == 1
    assert renamed.title == "Python 平台负责人"
    assert archived.is_archived is True
    assert reopened.conversation_reopen_state.title == "Python 平台负责人"
    assert reopened.conversation_reopen_state.is_archived is True
    assert reopened.conversation_reopen_state.latest_message_seq == 1
    assert [item.message_id for item in reopened.messages] == [message.message_id]
    assert store.list_conversations(owner_user_id="user_1", workspace_id="workspace_1") == []
    assert store.list_conversations(owner_user_id="user_1", workspace_id="workspace_1", include_archived=True)[0].title == (
        "Python 平台负责人"
    )


def test_conversation_store_rejects_empty_title_and_future_schema_version(tmp_path: Path) -> None:
    from seektalent_conversation_agent.errors import ConversationAgentError
    from seektalent_conversation_agent.store import CONVERSATION_AGENT_SCHEMA_VERSION, ConversationStore

    db_path = tmp_path / "conversation_agent.sqlite3"
    store = ConversationStore(db_path)
    store.initialize()
    conversation = store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="有效标题",
        created_at="2026-06-09T00:00:00.000000Z",
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        store.rename_conversation(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            title="   ",
            updated_at="2026-06-09T00:00:01.000000Z",
        )

    assert exc_info.value.reason_code == "conversation_title_invalid"

    future_db = tmp_path / "future.sqlite3"
    with sqlite3.connect(future_db) as conn:
        conn.execute(f"PRAGMA user_version = {CONVERSATION_AGENT_SCHEMA_VERSION + 1}")

    with pytest.raises(ConversationAgentError) as schema_exc:
        ConversationStore(future_db).initialize()

    assert schema_exc.value.reason_code == "conversation_agent_schema_unsupported"
