from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

import seektalent_workbench_v2.store as store_module
from seektalent_workbench_v2.models import WorkbenchV2TranscriptEventInput
from seektalent_workbench_v2.store import WorkbenchV2Store


def test_store_does_not_import_ui_workbench_helpers() -> None:
    assert "seektalent_ui" not in inspect.getsource(store_module)


def test_store_appends_events_with_monotonic_steps(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    conversation = store.create_conversation(first_user_text="你好", idempotency_key="create-1")

    first = store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="user_message",
            role="user",
            payload={"text": "你好"},
            status="completed",
        ),
    )
    second = store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="assistant_message",
            role="assistant",
            payload={"text": "你好，我可以帮你处理招聘需求。"},
            status="completed",
        ),
    )

    assert first.step == 1
    assert second.step == 2
    view = store.get_conversation(conversation.id)
    assert [event.step for event in view.events] == [1, 2]
    assert [event.type for event in view.events] == ["user_message", "assistant_message"]


def test_store_replays_create_by_idempotency_key(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()

    first = store.create_conversation(first_user_text="你好", idempotency_key="same-key")
    replay = store.create_conversation(first_user_text="你好", idempotency_key="same-key")

    assert first.id == replay.id
    assert first.title == "你好"


def test_store_lists_conversations_by_latest_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    timestamps = iter(
        [
            "2026-06-25T10:00:00+00:00",
            "2026-06-25T10:00:01+00:00",
            "2026-06-25T10:00:02+00:00",
        ]
    )
    monkeypatch.setattr(store_module, "_now_iso", lambda: next(timestamps))

    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()

    first = store.create_conversation(first_user_text="第一个需求", idempotency_key="first")
    second = store.create_conversation(first_user_text="第二个需求", idempotency_key="second")
    store.append_event(
        first.id,
        WorkbenchV2TranscriptEventInput(
            type="assistant_status",
            role="assistant",
            payload={"status": "working"},
            status="completed",
        ),
    )

    conversations = store.list_conversations()

    assert [conversation.id for conversation in conversations] == [first.id, second.id]


def test_store_rejects_idempotency_payload_conflict(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    store.create_conversation(first_user_text="你好", idempotency_key="same-key")

    try:
        store.create_conversation(first_user_text="另一个需求", idempotency_key="same-key")
    except ValueError as exc:
        assert str(exc) == "workbench_v2_idempotency_conflict"
    else:
        raise AssertionError("conflicting idempotency key should fail")


def test_store_keeps_context_summary_as_event_and_conversation_field(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    conversation = store.create_conversation(first_user_text="长对话", idempotency_key="create-summary")

    event = store.append_context_summary(conversation.id, summary="用户正在招聘数据科学家，偏杭州。")

    assert event.type == "context_summary"
    refreshed = store.get_conversation(conversation.id)
    assert refreshed.conversation.context_summary == "用户正在招聘数据科学家，偏杭州。"
    assert refreshed.events[-1].payload["summary"] == "用户正在招聘数据科学家，偏杭州。"


def test_context_summary_append_rolls_back_event_when_summary_update_fails(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench_v2.sqlite3"
    store = WorkbenchV2Store(database_path)
    store.initialize()
    conversation = store.create_conversation(first_user_text="长对话", idempotency_key="create-summary")
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_workbench_v2_context_summary_update
            BEFORE UPDATE OF context_summary ON workbench_v2_conversations
            BEGIN
                SELECT RAISE(ABORT, 'fail_context_summary_update');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="fail_context_summary_update"):
        store.append_context_summary(conversation.id, summary="用户正在招聘数据科学家，偏杭州。")

    refreshed = store.get_conversation(conversation.id)
    assert refreshed.conversation.context_summary is None
    assert refreshed.events == []


def test_event_input_rejects_non_json_serializable_payload() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WorkbenchV2TranscriptEventInput(
            type="user_message",
            role="user",
            payload={"bad": object()},
            status="completed",
        )

    assert "payload must be JSON-serializable" in str(exc_info.value)
