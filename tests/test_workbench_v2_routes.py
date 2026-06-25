from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent_ui.server import create_app
from seektalent_workbench_v2.models import (
    WorkbenchV2ConversationListSummary,
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationPublic,
    WorkbenchV2ConversationView,
    WorkbenchV2TranscriptEventView,
)
from tests.settings_factory import make_settings


class NoopRouteRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings


class FakeWorkbenchV2Service:
    def __init__(self) -> None:
        self.create_calls: list[tuple[str, str | None]] = []
        self.submit_calls: list[tuple[str, str, str | None]] = []
        self.get_calls: list[str] = []
        self.list_calls = 0

    def list_conversations(self) -> WorkbenchV2ConversationListView:
        self.list_calls += 1
        return WorkbenchV2ConversationListView(
            conversations=[
                WorkbenchV2ConversationListSummary(
                    conversationId="agentv2_existing",
                    title="Existing conversation",
                    status="idle",
                    updatedAt="2026-06-25T01:02:03.000004+00:00",
                )
            ]
        )

    async def create_conversation(
        self,
        message: str,
        idempotency_key: str | None,
    ) -> WorkbenchV2ConversationView:
        self.create_calls.append((message, idempotency_key))
        return _conversation_view(
            conversation_id="agentv2_created",
            title=message,
            event_id="agentv2_event_created",
            user_text=message,
        )

    def get_conversation(self, conversation_id: str) -> WorkbenchV2ConversationView:
        self.get_calls.append(conversation_id)
        if conversation_id == "missing":
            raise KeyError(conversation_id)
        return _conversation_view(
            conversation_id=conversation_id,
            title="Existing conversation",
            event_id="agentv2_event_get",
            user_text="hello",
        )

    async def submit_message(
        self,
        conversation_id: str,
        message: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self.submit_calls.append((conversation_id, message, idempotency_key))
        if conversation_id == "missing":
            raise KeyError(conversation_id)
        return _conversation_view(
            conversation_id=conversation_id,
            title="Existing conversation",
            event_id="agentv2_event_submit",
            user_text=message,
        ).model_dump(mode="json")


def test_create_conversation_returns_201_public_v2_shape(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations",
        json={"message": "先聊一下候选人搜索", "idempotencyKey": "create-1"},
    )

    assert response.status_code == 201, response.text
    assert response.json() == _conversation_view(
        conversation_id="agentv2_created",
        title="先聊一下候选人搜索",
        event_id="agentv2_event_created",
        user_text="先聊一下候选人搜索",
    ).model_dump(mode="json")
    assert fake.create_calls == [("先聊一下候选人搜索", "create-1")]


def test_list_conversations_uses_replaceable_app_state_service(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.get("/api/agent/workbench/v2/conversations")

    assert response.status_code == 200, response.text
    assert response.json() == WorkbenchV2ConversationListView(
        conversations=[
            WorkbenchV2ConversationListSummary(
                conversationId="agentv2_existing",
                title="Existing conversation",
                status="idle",
                updatedAt="2026-06-25T01:02:03.000004+00:00",
            )
        ]
    ).model_dump(mode="json")
    assert fake.list_calls == 1


def test_missing_get_returns_public_reason_code(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.get("/api/agent/workbench/v2/conversations/missing")

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": {"reasonCode": "workbench_v2_conversation_not_found"}}
    assert fake.get_calls == ["missing"]


def test_missing_submit_returns_public_reason_code(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations/missing/messages",
        json={"message": "继续", "idempotencyKey": "submit-1"},
    )

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": {"reasonCode": "workbench_v2_conversation_not_found"}}
    assert fake.submit_calls == [("missing", "继续", "submit-1")]


def test_create_payload_rejects_extra_fields_before_service_call(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations",
        json={"message": "hello", "unexpected": True},
    )

    assert response.status_code == 400, response.text
    assert fake.create_calls == []


def _client(tmp_path: Path) -> tuple[TestClient, FakeWorkbenchV2Service]:
    settings = make_settings(
        local_data_root=str(tmp_path),
        workspace_root=str(tmp_path),
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
    )
    app = create_app(settings=settings, runtime_factory=NoopRouteRuntime)
    fake = FakeWorkbenchV2Service()
    app.state.workbench_v2_service = fake
    return (
        TestClient(
            app,
            base_url="http://localhost",
            client=("127.0.0.1", 50000),
        ),
        fake,
    )


def _conversation_view(
    *,
    conversation_id: str,
    title: str,
    event_id: str,
    user_text: str,
) -> WorkbenchV2ConversationView:
    return WorkbenchV2ConversationView(
        conversation=WorkbenchV2ConversationPublic(
            conversationId=conversation_id,
            title=title,
            runtimeState="idle",
            runtimeRunId=None,
            createdAt="2026-06-25T01:02:03.000004+00:00",
            updatedAt="2026-06-25T01:02:03.000004+00:00",
        ),
        transcriptEvents=[
            WorkbenchV2TranscriptEventView(
                eventId=event_id,
                step=1,
                type="user_message",
                role="user",
                status="completed",
                payload={"text": user_text},
                createdAt="2026-06-25T01:02:03.000004+00:00",
            )
        ],
        requirementForm=None,
        runtime=None,
    )
