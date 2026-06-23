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
        "agent_operation_audits",
        "agent_runtime_links",
        "agent_context_summaries",
        "agent_context_compactions",
    } <= tables
    assert "agent_" + "tool_calls" not in tables

    with sqlite3.connect(db_path) as conn:
        operation_audit_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(agent_operation_audits)").fetchall()
        }
        message_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_transcript_messages)").fetchall()}

    assert {
        "operation_id",
        "conversation_id",
        "activity_id",
        "runtime_run_id",
        "operation_name",
        "execution_origin",
        "status",
        "args_json",
        "result_json",
        "reason_code",
        "started_at",
        "completed_at",
    } <= operation_audit_columns
    assert "source_operation_id" in message_columns
    assert "source_" + "tool" + "_call_id" not in message_columns


def test_conversation_store_persists_operation_audit_records(tmp_path: Path) -> None:
    from seektalent_conversation_agent.models import OperationAuditRecord
    from seektalent_conversation_agent.store import ConversationStore

    store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    store.initialize()
    conversation = store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
        created_at="2026-06-09T00:00:00.000000Z",
    )

    saved = store.save_operation_audit(
        operation_id="operation_1",
        conversation_id=conversation.conversation_id,
        operation_name="agent_model_run",
        execution_origin="model",
        status="completed",
        args={"idempotencyKey": "idem_1"},
        result={"assistantMessageId": "msg_1"},
        reason_code=None,
        started_at="2026-06-09T00:00:01.000000Z",
        completed_at="2026-06-09T00:00:02.000000Z",
    )

    assert isinstance(saved, OperationAuditRecord)
    assert saved.operation_id == "operation_1"
    assert saved.operation_name == "agent_model_run"
    assert saved.execution_origin == "model"
    assert not hasattr(saved, "tool" + "_call_id")
    assert not hasattr(saved, "tool" + "_name")
    assert store.list_operation_audits(conversation_id=conversation.conversation_id) == [saved]


def test_conversation_store_does_not_own_runtime_control_tables_or_progression_state(tmp_path: Path) -> None:
    from seektalent_conversation_agent.store import ConversationStore

    db_path = tmp_path / "conversation_agent.sqlite3"
    ConversationStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        table_columns = {
            table: {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in tables
            if table.startswith("agent_")
        }

    assert "runtime_control_runs" not in tables
    assert "runtime_control_events" not in tables
    assert "runtime_control_stage_outputs" not in tables
    assert "runtime_control_commands" not in tables
    assert table_columns["agent_conversations"] & {
        "run_intent_id",
        "start_idempotency_key",
        "run_kind",
        "current_stage",
        "current_round",
        "latest_checkpoint_id",
        "stop_reason_code",
    } == set()
    assert {
        "run_kind",
        "workbench_session_id",
        "approved_requirement_revision_id",
        "run_intent_id",
        "link_reason",
        "latest_event_seq",
        "active_at",
        "superseded_at",
        "completed_at",
    } <= table_columns["agent_runtime_links"]
    assert table_columns["agent_runtime_links"] & {
        "start_idempotency_key",
        "current_stage",
        "current_round",
        "latest_checkpoint_id",
        "stop_reason_code",
        "event_type",
        "stage",
        "output_kind",
    } == set()


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


def test_conversation_reopen_exposes_runtime_link_history_and_active_pointer(tmp_path: Path) -> None:
    from seektalent_conversation_agent.store import ConversationStore

    store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    store.initialize()
    conversation = store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
        created_at="2026-06-09T00:00:00.000000Z",
    )

    store.link_runtime_run(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_primary",
        workbench_session_id="workbench_session_primary",
        approved_requirement_revision_id="reqapproved_1",
        run_intent_id="start:agent_conv_1:reqapproved_1",
        run_kind="primary",
        link_reason="start",
        linked_at="2026-06-09T00:00:01.000000Z",
    )
    store.link_runtime_run(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_rerun",
        workbench_session_id="workbench_session_rerun",
        approved_requirement_revision_id="reqapproved_1",
        run_intent_id="rerun:agent_conv_1:reqapproved_1:2",
        run_kind="rerun",
        link_reason="rerun",
        make_active=False,
        linked_at="2026-06-09T00:00:02.000000Z",
    )
    store.update_rendered_runtime_cursor(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_rerun",
        latest_event_seq=17,
        updated_at="2026-06-09T00:00:03.000000Z",
    )

    reopened = store.reopen_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        opened_at="2026-06-09T00:00:04.000000Z",
    ).conversation_reopen_state

    assert reopened.runtime_run_id == "runtime_run_primary"
    assert [link.runtime_run_id for link in reopened.linked_runtime_runs] == [
        "runtime_run_primary",
        "runtime_run_rerun",
    ]
    assert [link.is_active for link in reopened.linked_runtime_runs] == [True, False]
    assert reopened.linked_runtime_runs[1].run_kind == "rerun"
    assert reopened.linked_runtime_runs[1].run_intent_id == "rerun:agent_conv_1:reqapproved_1:2"
    assert reopened.linked_runtime_runs[1].link_reason == "rerun"
    assert reopened.linked_runtime_runs[1].latest_event_seq == 17

    store.activate_runtime_run(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_rerun",
        activated_at="2026-06-09T00:00:05.000000Z",
    )
    reopened_after_switch = store.reopen_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        opened_at="2026-06-09T00:00:06.000000Z",
    ).conversation_reopen_state

    assert reopened_after_switch.runtime_run_id == "runtime_run_rerun"
    assert [link.is_active for link in reopened_after_switch.linked_runtime_runs] == [False, True]
    assert reopened_after_switch.linked_runtime_runs[0].superseded_at == "2026-06-09T00:00:05.000000Z"
    assert reopened_after_switch.linked_runtime_runs[1].active_at == "2026-06-09T00:00:05.000000Z"


def test_conversation_store_migrates_v3_runtime_links_to_metadata_history(tmp_path: Path) -> None:
    from seektalent_conversation_agent.store import CONVERSATION_AGENT_SCHEMA_VERSION, ConversationStore

    db_path = tmp_path / "conversation_agent_v3.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE agent_conversations (
                conversation_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                title_updated_at TEXT,
                is_archived INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT,
                archive_reason_code TEXT,
                last_opened_at TEXT,
                latest_message_seq INTEGER NOT NULL DEFAULT 0,
                latest_activity_seq INTEGER NOT NULL DEFAULT 0,
                latest_rendered_runtime_event_seq INTEGER NOT NULL DEFAULT 0,
                runtime_run_id TEXT,
                workbench_session_id TEXT,
                latest_draft_revision_id TEXT,
                approved_requirement_revision_id TEXT,
                final_summary_id TEXT,
                pending_user_action TEXT,
                pending_command_count INTEGER NOT NULL DEFAULT 0,
                pending_requirement_review_count INTEGER NOT NULL DEFAULT 0,
                pending_memory_review_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE agent_transcript_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                message_type TEXT NOT NULL,
                text TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                token_count INTEGER,
                model_input_included INTEGER NOT NULL DEFAULT 1,
                source_tool_call_id TEXT,
                source_runtime_run_id TEXT,
                source_runtime_event_seq INTEGER,
                idempotency_key TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_transcript_activity_items (
                activity_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                activity_seq INTEGER NOT NULL,
                activity_key TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_runtime_run_id TEXT,
                source_event_id_latest TEXT,
                source_event_seq_start INTEGER,
                source_event_seq_latest INTEGER,
                payload_json TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_tool_calls (
                tool_call_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                activity_id TEXT,
                runtime_run_id TEXT,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                args_json TEXT NOT NULL,
                result_json TEXT,
                reason_code TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE agent_runtime_links (
                conversation_id TEXT NOT NULL,
                runtime_run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                latest_event_seq INTEGER NOT NULL DEFAULT 0,
                linked_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(conversation_id, runtime_run_id)
            );
            CREATE TABLE agent_context_summaries (
                summary_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                source_message_seq_start INTEGER NOT NULL,
                source_message_seq_end INTEGER NOT NULL,
                source_activity_seq_start INTEGER,
                source_activity_seq_end INTEGER,
                latest_rendered_runtime_event_seq INTEGER NOT NULL,
                summary_text TEXT NOT NULL,
                quality_status TEXT NOT NULL,
                quality_evidence_json TEXT NOT NULL,
                token_count INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_context_compactions (
                compaction_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger_reason_code TEXT NOT NULL,
                summary_id TEXT,
                source_message_seq_start INTEGER,
                source_message_seq_end INTEGER,
                source_activity_seq_start INTEGER,
                source_activity_seq_end INTEGER,
                quality_reason_code TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                failed_reason_code TEXT
            );
            INSERT INTO agent_conversations (
                conversation_id, owner_user_id, workspace_id, status, title,
                runtime_run_id, workbench_session_id, approved_requirement_revision_id,
                created_at, updated_at
            ) VALUES (
                'agent_conv_1', 'user_1', 'workspace_1', 'running', 'Python 平台负责人',
                'runtime_run_1', 'workbench_session_1', 'reqapproved_1',
                '2026-06-09T00:00:00.000000Z', '2026-06-09T00:00:00.000000Z'
            );
            INSERT INTO agent_runtime_links (
                conversation_id, runtime_run_id, status, latest_event_seq, linked_at, updated_at
            ) VALUES (
                'agent_conv_1', 'runtime_run_1', 'linked', 9,
                '2026-06-09T00:00:01.000000Z', '2026-06-09T00:00:02.000000Z'
            );
            PRAGMA user_version = 3;
            """
        )

    store = ConversationStore(db_path)
    store.initialize()
    reopened = store.reopen_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        opened_at="2026-06-09T00:00:03.000000Z",
    ).conversation_reopen_state

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert version == CONVERSATION_AGENT_SCHEMA_VERSION
    assert reopened.linked_runtime_runs[0].status == "active"
    assert reopened.linked_runtime_runs[0].workbench_session_id == "workbench_session_1"
    assert reopened.linked_runtime_runs[0].approved_requirement_revision_id == "reqapproved_1"
    assert reopened.linked_runtime_runs[0].latest_event_seq == 9
    assert reopened.linked_runtime_runs[0].is_active is True


def test_conversation_store_migrates_v7_tool_calls_to_operation_audits(tmp_path: Path) -> None:
    from seektalent_conversation_agent.store import CONVERSATION_AGENT_SCHEMA_VERSION, ConversationStore

    db_path = tmp_path / "conversation_agent_v7.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE agent_conversations (
                conversation_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                title_updated_at TEXT,
                is_archived INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT,
                archive_reason_code TEXT,
                last_opened_at TEXT,
                latest_message_seq INTEGER NOT NULL DEFAULT 0,
                latest_activity_seq INTEGER NOT NULL DEFAULT 0,
                latest_rendered_runtime_event_seq INTEGER NOT NULL DEFAULT 0,
                runtime_run_id TEXT,
                workbench_session_id TEXT,
                latest_draft_revision_id TEXT,
                approved_requirement_revision_id TEXT,
                final_summary_id TEXT,
                pending_user_action TEXT,
                pending_command_count INTEGER NOT NULL DEFAULT 0,
                pending_requirement_review_count INTEGER NOT NULL DEFAULT 0,
                pending_memory_review_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE agent_transcript_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                message_type TEXT NOT NULL,
                text TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                token_count INTEGER,
                model_input_included INTEGER NOT NULL DEFAULT 1,
                source_tool_call_id TEXT,
                source_runtime_run_id TEXT,
                source_runtime_event_seq INTEGER,
                idempotency_key TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_transcript_activity_items (
                activity_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                activity_seq INTEGER NOT NULL,
                activity_key TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_runtime_run_id TEXT,
                source_event_id_latest TEXT,
                source_event_seq_start INTEGER,
                source_event_seq_latest INTEGER,
                payload_json TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_tool_calls (
                tool_call_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                activity_id TEXT,
                runtime_run_id TEXT,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                args_json TEXT NOT NULL,
                result_json TEXT,
                reason_code TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE agent_runtime_links (
                conversation_id TEXT NOT NULL,
                runtime_run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                run_kind TEXT NOT NULL DEFAULT 'primary',
                workbench_session_id TEXT,
                approved_requirement_revision_id TEXT NOT NULL DEFAULT '',
                run_intent_id TEXT,
                link_reason TEXT NOT NULL DEFAULT 'start',
                latest_event_seq INTEGER NOT NULL DEFAULT 0,
                linked_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                active_at TEXT,
                superseded_at TEXT,
                completed_at TEXT,
                PRIMARY KEY(conversation_id, runtime_run_id)
            );
            CREATE TABLE agent_context_summaries (
                summary_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                source_message_seq_start INTEGER NOT NULL,
                source_message_seq_end INTEGER NOT NULL,
                source_activity_seq_start INTEGER,
                source_activity_seq_end INTEGER,
                latest_rendered_runtime_event_seq INTEGER NOT NULL,
                summary_text TEXT NOT NULL,
                quality_status TEXT NOT NULL,
                quality_evidence_json TEXT NOT NULL,
                token_count INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_context_compactions (
                compaction_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger_reason_code TEXT NOT NULL,
                summary_id TEXT,
                source_message_seq_start INTEGER,
                source_message_seq_end INTEGER,
                source_activity_seq_start INTEGER,
                source_activity_seq_end INTEGER,
                quality_reason_code TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                failed_reason_code TEXT
            );
            INSERT INTO agent_conversations (
                conversation_id, owner_user_id, workspace_id, status, title,
                latest_message_seq, created_at, updated_at
            ) VALUES (
                'agent_conv_1', 'user_1', 'workspace_1', 'draft', 'Python 平台负责人',
                1, '2026-06-09T00:00:00.000000Z', '2026-06-09T00:00:00.000000Z'
            );
            INSERT INTO agent_tool_calls (
                tool_call_id, conversation_id, activity_id, runtime_run_id, tool_name, status,
                args_json, result_json, reason_code, started_at, completed_at
            ) VALUES (
                'legacy_tool_call_1', 'agent_conv_1', NULL, NULL, 'agent_model_run', 'completed',
                '{"idempotencyKey":"idem_1"}', '{"assistantMessageId":"msg_1"}', NULL,
                '2026-06-09T00:00:01.000000Z', '2026-06-09T00:00:02.000000Z'
            ), (
                'legacy_tool_call_2', 'agent_conv_1', NULL, 'runtime_run_1', 'extract_requirements', 'completed',
                '{"idempotencyKey":"idem_2"}', '{"draftRevisionId":"draft_1"}', NULL,
                '2026-06-09T00:00:03.000000Z', '2026-06-09T00:00:04.000000Z'
            );
            INSERT INTO agent_transcript_messages (
                message_id, conversation_id, message_seq, role, message_type, text,
                payload_json, source_tool_call_id, created_at
            ) VALUES (
                'msg_1', 'agent_conv_1', 1, 'assistant', 'assistant_text', '已完成',
                '{}', 'legacy_tool_call_1', '2026-06-09T00:00:02.000000Z'
            );
            PRAGMA user_version = 7;
            """
        )

    store = ConversationStore(db_path)
    store.initialize()

    audits = store.list_operation_audits(conversation_id="agent_conv_1")
    messages = store.get_messages(conversation_id="agent_conv_1")
    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        message_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_transcript_messages)")}

    assert version == CONVERSATION_AGENT_SCHEMA_VERSION
    assert [audit.operation_id for audit in audits] == ["legacy_tool_call_1", "legacy_tool_call_2"]
    assert audits[0].operation_name == "agent_model_run"
    assert audits[0].execution_origin == "model"
    assert audits[0].args == {"idempotencyKey": "idem_1"}
    assert audits[0].result == {"assistantMessageId": "msg_1"}
    assert audits[1].operation_name == "extract_requirements"
    assert audits[1].execution_origin == "service"
    assert audits[1].runtime_run_id == "runtime_run_1"
    assert audits[1].args == {"idempotencyKey": "idem_2"}
    assert audits[1].result == {"draftRevisionId": "draft_1"}
    assert messages[0].source_operation_id == "legacy_tool_call_1"
    assert "agent_operation_audits" in tables
    assert "agent_tool_calls" not in tables
    assert "source_operation_id" in message_columns
    assert "source_tool_call_id" not in message_columns


def test_conversation_store_rejects_runtime_run_linked_to_multiple_conversations(tmp_path: Path) -> None:
    from seektalent_conversation_agent.errors import ConversationAgentError
    from seektalent_conversation_agent.store import ConversationStore

    store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    store.initialize()
    first = store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
        created_at="2026-06-09T00:00:00.000000Z",
    )
    second = store.create_conversation(
        conversation_id="agent_conv_2",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Go 平台负责人",
        created_at="2026-06-09T00:00:00.000000Z",
    )

    store.link_runtime_run(
        conversation_id=first.conversation_id,
        runtime_run_id="runtime_run_shared",
        workbench_session_id=None,
        approved_requirement_revision_id="reqapproved_1",
        linked_at="2026-06-09T00:00:01.000000Z",
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        store.link_runtime_run(
            conversation_id=second.conversation_id,
            runtime_run_id="runtime_run_shared",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_2",
            linked_at="2026-06-09T00:00:02.000000Z",
        )

    assert exc_info.value.reason_code == "agent_runtime_run_already_linked"


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
