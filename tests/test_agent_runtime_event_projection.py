from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.store import ConversationStore
from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord, RuntimeRunSnapshot

from tests.conversation_agent_test_support import build_service, save_approved_requirement


def test_runtime_event_projection_is_idempotent_and_advances_cursor(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )
    approved = save_approved_requirement(runtime_store, conversation_id=conversation.conversation_id)
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_projection_1",
            agent_conversation_id=conversation.conversation_id,
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-09T00:00:20.000000Z",
            updated_at="2026-06-09T00:00:20.000000Z",
            completed_at=None,
        )
    )
    service.store.link_runtime_run(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_projection_1",
        workbench_session_id="workbench_session_1",
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-09T00:00:20.000000Z",
    )
    event = runtime_store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_projection_1",
            runtime_run_id="runtime_run_projection_1",
            event_type="runtime_source_result",
            stage="source",
            round_no=1,
            source_id="cts",
            status="completed",
            summary="CTS 返回 3 个候选人。",
            payload={"candidateCount": 3},
            workbench_event_global_seq=None,
            created_at="2026-06-09T00:00:21.000000Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_projection_1",
            status="running",
            current_stage="source",
            current_round=1,
            latest_event_seq=1,
            snapshot={"progressSummary": "CTS 返回 3 个候选人。"},
            updated_at="2026-06-09T00:00:21.000000Z",
        ),
    )

    first = service.poll_runtime_events(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id="runtime_run_projection_1",
        limit=10,
    )
    second = service.poll_runtime_events(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id="runtime_run_projection_1",
        limit=10,
    )

    assert first.conversation_reopen_state.latest_rendered_runtime_event_seq == event.event_seq
    assert [item.activity_type for item in first.activity_items] == ["source_result"]
    assert [message.message_type for message in first.messages if message.message_type == "runtime_progress"] == [
        "runtime_progress"
    ]
    assert second.messages == first.messages
    assert second.activity_items == first.activity_items


def test_runtime_event_gap_records_recoverable_sync_state_without_advancing_cursor(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )
    approved = save_approved_requirement(
        runtime_store,
        conversation_id=conversation.conversation_id,
        approved_requirement_revision_id="reqapproved_gap_1",
    )
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_gap_1",
            agent_conversation_id=conversation.conversation_id,
            workbench_session_id=None,
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=2,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-09T00:00:20.000000Z",
            updated_at="2026-06-09T00:00:20.000000Z",
            completed_at=None,
        )
    )
    service.store.link_runtime_run(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_gap_1",
        workbench_session_id=None,
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-09T00:00:20.000000Z",
    )
    runtime_store.update_run_status(
        runtime_run_id="runtime_run_gap_1",
        status="running",
        updated_at="2026-06-09T00:00:21.000000Z",
    )

    response = service.poll_runtime_events(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id="runtime_run_gap_1",
        limit=10,
    )

    assert response.reason_code == "runtime_event_gap_detected"
    assert response.conversation_reopen_state.latest_rendered_runtime_event_seq == 0


def test_runtime_event_projection_rejects_unlinked_runtime_run(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    first_conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="第一个任务",
    )
    second_conversation = service.store.create_conversation(
        conversation_id="agent_conv_2",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="第二个任务",
        created_at="2026-06-09T00:00:10.000000Z",
    )
    approved = save_approved_requirement(
        runtime_store,
        conversation_id=second_conversation.conversation_id,
        approved_requirement_revision_id="reqapproved_second_1",
    )
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_second_1",
            agent_conversation_id=second_conversation.conversation_id,
            workbench_session_id="workbench_session_2",
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-09T00:00:20.000000Z",
            updated_at="2026-06-09T00:00:20.000000Z",
            completed_at=None,
        )
    )
    service.store.link_runtime_run(
        conversation_id=second_conversation.conversation_id,
        runtime_run_id="runtime_run_second_1",
        workbench_session_id="workbench_session_2",
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-09T00:00:20.000000Z",
    )
    runtime_store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_second_1",
            runtime_run_id="runtime_run_second_1",
            event_type="runtime_source_result",
            stage="source",
            round_no=1,
            source_id="cts",
            status="completed",
            summary="第二个任务的 CTS 结果。",
            payload={"candidateCount": 3},
            workbench_event_global_seq=None,
            created_at="2026-06-09T00:00:21.000000Z",
        )
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        service.poll_runtime_events(
            conversation_id=first_conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            runtime_run_id="runtime_run_second_1",
            limit=10,
        )

    assert exc_info.value.reason_code == "agent_runtime_run_not_linked"
    reopened = service.reopen_conversation(
        conversation_id=first_conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )
    assert reopened.conversation_reopen_state.latest_rendered_runtime_event_seq == 0
    assert all("第二个任务" not in message.text for message in reopened.messages)


def test_runtime_projection_cursor_does_not_move_backward_from_stale_poller(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    store.initialize()
    store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
        created_at="2026-06-09T00:00:01.000000Z",
    )
    store.link_runtime_run(
        conversation_id="agent_conv_1",
        runtime_run_id="runtime_run_1",
        workbench_session_id="workbench_session_1",
        approved_requirement_revision_id="reqapproved_1",
        linked_at="2026-06-09T00:00:02.000000Z",
    )
    store.update_rendered_runtime_cursor(
        conversation_id="agent_conv_1",
        runtime_run_id="runtime_run_1",
        latest_event_seq=5,
        updated_at="2026-06-09T00:00:05.000000Z",
    )

    conversation = store.update_rendered_runtime_cursor(
        conversation_id="agent_conv_1",
        runtime_run_id="runtime_run_1",
        latest_event_seq=3,
        updated_at="2026-06-09T00:00:06.000000Z",
    )

    assert conversation.latest_rendered_runtime_event_seq == 5
    with sqlite3.connect(store.path) as conn:
        row = conn.execute(
            """
            SELECT latest_event_seq
            FROM agent_runtime_links
            WHERE conversation_id = ? AND runtime_run_id = ?
            """,
            ("agent_conv_1", "runtime_run_1"),
        ).fetchone()
    assert row is not None
    assert row[0] == 5


def test_activity_item_update_ignores_stale_runtime_event_seq(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    store.initialize()
    store.create_conversation(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
        created_at="2026-06-09T00:00:01.000000Z",
    )
    store.upsert_activity_item(
        activity_id="agent_activity_1",
        conversation_id="agent_conv_1",
        activity_key="agent_conv_1:runtime_run_1:scoring:1:all",
        activity_type="scoring",
        status="completed",
        title="候选人评分",
        summary="第 1 轮评分完成。",
        payload={"candidateCount": 5},
        source_runtime_run_id="runtime_run_1",
        source_event_id_latest="rtevt_5",
        source_event_seq_start=2,
        source_event_seq_latest=5,
        started_at="2026-06-09T00:00:02.000000Z",
        updated_at="2026-06-09T00:00:05.000000Z",
        completed_at="2026-06-09T00:00:05.000000Z",
        created_at="2026-06-09T00:00:02.000000Z",
    )

    stale = store.upsert_activity_item(
        activity_id="agent_activity_2",
        conversation_id="agent_conv_1",
        activity_key="agent_conv_1:runtime_run_1:scoring:1:all",
        activity_type="scoring",
        status="in_progress",
        title="候选人评分",
        summary="第 1 轮评分进行中。",
        payload={"candidateCount": 2},
        source_runtime_run_id="runtime_run_1",
        source_event_id_latest="rtevt_3",
        source_event_seq_start=2,
        source_event_seq_latest=3,
        started_at="2026-06-09T00:00:02.000000Z",
        updated_at="2026-06-09T00:00:06.000000Z",
        completed_at=None,
        created_at="2026-06-09T00:00:03.000000Z",
    )

    assert stale.activity_id == "agent_activity_1"
    assert stale.status == "completed"
    assert stale.summary == "第 1 轮评分完成。"
    assert stale.payload == {"candidateCount": 5}
    assert stale.source_event_id_latest == "rtevt_5"
    assert stale.source_event_seq_latest == 5
    assert stale.completed_at == "2026-06-09T00:00:05.000000Z"
