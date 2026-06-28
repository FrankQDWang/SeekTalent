from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, datetime
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


def test_store_lists_conversations_by_latest_update_under_rapid_same_second_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamps = iter(
        [
            datetime(2026, 6, 25, 10, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 6, 25, 10, 0, 0, 2, tzinfo=UTC),
            datetime(2026, 6, 25, 10, 0, 0, 3, tzinfo=UTC),
        ]
    )

    class FakeDateTime:
        @classmethod
        def now(cls, tz: object) -> datetime:
            assert tz is UTC
            return next(timestamps)

    monkeypatch.setattr(store_module, "datetime", FakeDateTime)

    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()

    first = store.create_conversation(first_user_text="第一个需求", idempotency_key="first")
    second = store.create_conversation(first_user_text="第二个需求", idempotency_key="second")
    store.append_event(
        second.id,
        WorkbenchV2TranscriptEventInput(
            type="assistant_status",
            role="assistant",
            payload={"status": "working"},
            status="completed",
        ),
    )

    conversations = store.list_conversations()

    assert [conversation.id for conversation in conversations] == [second.id, first.id]
    assert conversations[0].updated_at > conversations[1].updated_at


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


def test_store_sets_runtime_link_and_state(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    conversation = store.create_conversation(first_user_text="开始运行", idempotency_key="create-runtime")

    updated = store.set_runtime(conversation.id, runtime_run_id="rtrun_1", runtime_state="queued")

    assert updated.runtime_run_id == "rtrun_1"
    assert updated.runtime_state == "queued"
    refreshed = store.get_conversation(conversation.id)
    assert refreshed.conversation.runtime_run_id == "rtrun_1"
    assert refreshed.conversation.runtime_state == "queued"
    assert refreshed.conversation.updated_at >= conversation.updated_at


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


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_event_input_rejects_non_finite_float_payload(score: float) -> None:
    with pytest.raises(ValidationError) as exc_info:
        WorkbenchV2TranscriptEventInput(
            type="user_message",
            role="user",
            payload={"score": score},
            status="completed",
        )

    assert "payload must be JSON-serializable" in str(exc_info.value)


def test_store_serialization_rejects_non_finite_float_payload_backstop(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    conversation = store.create_conversation(first_user_text="你好", idempotency_key="create-1")
    event = WorkbenchV2TranscriptEventInput.model_construct(
        type="user_message",
        role="user",
        payload={"score": float("nan")},
        status="completed",
        parent_event_id=None,
        dedupe_key=None,
    )

    with pytest.raises(ValueError):
        store.append_event(conversation.id, event)
