from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent_ui.agent_workbench_models import AgentWorkbenchTranscriptPayloadResponse
from seektalent_ui.server import create_app
from tests.conversation_agent_test_support import sample_requirement_sheet
from tests.settings_factory import make_settings


class DeterministicRouteRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def extract_requirements(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        progress_callback=None,
        requirement_cache_scope: str | None = None,
    ) -> object:
        if callable(progress_callback):
            progress_callback(
                ProgressEvent(
                    type="requirements_completed",
                    message="岗位需求解析完成。",
                    payload={"stage": "requirements"},
                )
            )
        return sample_requirement_sheet(job_title=job_title)


@dataclass
class StreamingRequest:
    app: object

    async def is_disconnected(self) -> bool:
        return False


def test_workbench_view_snapshot_seq_does_not_skip_event_inserted_during_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import seektalent_ui.agent_workbench_routes as routes

    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = _create_conversation(client)
    stream_store = client.app.state.agent_workbench_stream_store
    first = _append_message_event(stream_store, conversation_id=conversation_id, message_id="msg_before")
    original_build_projection_input = routes.build_agent_workbench_projection_input
    injected = False

    def inject_event_during_build(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal injected
        projection_input = original_build_projection_input(*args, **kwargs)
        if not injected:
            injected = True
            _append_message_event(stream_store, conversation_id=conversation_id, message_id="msg_during")
        return projection_input

    monkeypatch.setattr(routes, "build_agent_workbench_projection_input", inject_event_during_build)

    response = client.get(f"/api/agent/workbench/conversations/{conversation_id}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.workbench.view.v2"
    assert payload["streamCursor"]["snapshotSeq"] == first.seq
    assert payload["streamCursor"]["latestStreamSeq"] == first.seq
    assert payload["streamCursor"]["viewRevision"] == first.seq
    replay = stream_store.replay_stream_envelopes(
        conversation_id=conversation_id,
        after_seq=payload["streamCursor"]["snapshotSeq"],
        limit=10,
    )
    assert [event.seq for event in replay] == [first.seq + 1]


def test_agent_workbench_event_replay_route_rejects_cursor_before_retention_window(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = _create_conversation(client)
    stream_store = client.app.state.agent_workbench_stream_store
    _append_retained_event_window(stream_store, conversation_id=conversation_id)

    response = client.get(f"/api/agent/workbench/conversations/{conversation_id}/events?after_seq=0")

    assert response.status_code == 410, response.text
    payload = response.json()
    assert payload["reasonCode"] == "stream_replay_gap"
    assert "correlationId" in payload


def test_agent_workbench_sse_generator_emits_stream_replay_gap_for_too_old_cursor(tmp_path: Path) -> None:
    from seektalent_ui.agent_workbench_routes import _event_generator

    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = _create_conversation(client)
    user = client.app.state.workbench_store.ensure_local_actor()
    assert user is not None
    stream_store = client.app.state.agent_workbench_stream_store
    _append_retained_event_window(stream_store, conversation_id=conversation_id)

    generator = _event_generator(
        request=StreamingRequest(app=client.app),
        user=user,
        stream_store=stream_store,
        conversation_id=conversation_id,
        after_seq=0,
    )

    async def consume() -> dict[str, str]:
        first = await asyncio.wait_for(anext(generator), timeout=0.5)
        await generator.aclose()
        return first

    first = asyncio.run(consume())
    payload = json.loads(first["data"])
    assert first["event"] == "agent_workbench_error"
    assert payload["reasonCode"] == "stream_replay_gap"
    assert payload["conversationId"] == conversation_id
    assert "correlationId" in payload


def _client(tmp_path: Path) -> TestClient:
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
    )
    return TestClient(
        create_app(settings=settings, runtime_factory=DeterministicRouteRuntime),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )


def _ensure_local_actor(client: TestClient) -> None:
    client.app.state.workbench_store.ensure_local_actor()


def _create_conversation(client: TestClient) -> str:
    response = client.post("/api/agent/conversations", json={"title": "资深 Python 后端"})
    assert response.status_code == 201, response.text
    return response.json()["conversation"]["conversationId"]


def _append_message_event(stream_store, *, conversation_id: str, message_id: str, created_at: str | None = None):  # type: ignore[no-untyped-def]
    return stream_store.append_event(
        conversation_id=conversation_id,
        kind="message.completed",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId=message_id),
        source_fact_key=f"message:{message_id}",
        created_at=created_at or _now(),
    )


def _append_retained_event_window(stream_store, *, conversation_id: str) -> None:  # type: ignore[no-untyped-def]
    for index in range(3):
        _append_message_event(
            stream_store,
            conversation_id=conversation_id,
            message_id=f"msg_retained_{index}",
            created_at=f"2026-05-0{index + 1}T00:00:00+00:00",
        )
    stream_store.prune_closed_conversation_events(
        [conversation_id],
        created_before="2026-06-01T00:00:00+00:00",
        retain_last=1,
    )
    assert stream_store.first_seq(conversation_id=conversation_id) == 3


def _now() -> str:
    return datetime(2026, 6, 12, 12, 0, tzinfo=UTC).isoformat()
