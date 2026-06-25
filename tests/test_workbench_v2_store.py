from __future__ import annotations

from pathlib import Path

from seektalent_workbench_v2.models import WorkbenchV2TranscriptEventInput
from seektalent_workbench_v2.store import WorkbenchV2Store


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
