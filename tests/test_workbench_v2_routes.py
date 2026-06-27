from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent_ui.agent_request_models import (
    MAX_AGENT_MESSAGE_CHARS,
    MAX_IDEMPOTENCY_KEY_CHARS,
    MAX_REQUEST_ID_CHARS,
    MAX_REQUIREMENT_TEXT_CHARS,
)
from seektalent_ui.server import create_app
from seektalent_workbench_v2.models import (
    WorkbenchV2CandidateDetailSectionView,
    WorkbenchV2CandidateDetailView,
    WorkbenchV2ConversationListSummary,
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationPublic,
    WorkbenchV2ConversationEventsView,
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
        self.requirement_action_calls: list[dict[str, object]] = []
        self.get_calls: list[str] = []
        self.event_calls: list[tuple[str, int, int]] = []
        self.detail_calls: list[tuple[str, str]] = []
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
        if idempotency_key == "same-key" and message == "different message":
            raise ValueError("workbench_v2_idempotency_conflict")
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

    def list_events(
        self,
        conversation_id: str,
        *,
        after_step: int,
        limit: int,
    ) -> WorkbenchV2ConversationEventsView:
        self.event_calls.append((conversation_id, after_step, limit))
        if conversation_id == "missing":
            raise KeyError(conversation_id)
        events = [
            _event_view(event_id="agentv2_event_1", step=1, user_text="hello"),
            _event_view(event_id="agentv2_event_2", step=2, user_text="progress"),
        ]
        return WorkbenchV2ConversationEventsView(
            conversationId=conversation_id,
            afterStep=after_step,
            latestStep=2,
            events=[event for event in events if event.step > after_step][:limit],
        )

    def get_candidate_detail(self, conversation_id: str, candidate_id: str) -> WorkbenchV2CandidateDetailView:
        self.detail_calls.append((conversation_id, candidate_id))
        if conversation_id == "missing":
            raise KeyError(conversation_id)
        return WorkbenchV2CandidateDetailView(
            candidateId=candidate_id,
            displayName="吴所谓",
            headline="资深体验设计工程师 · 平安集团",
            sourceKinds=["liepin"],
            matchScore=86,
            sections=[
                WorkbenchV2CandidateDetailSectionView(
                    title="匹配程度",
                    items=["推荐理由：做过复杂 B 端业务流程。"],
                )
            ],
            evidence=["来源：猎聘 detail 证据"],
            detailAvailability="available",
            accessState="allowed",
            evidenceLevel="detail",
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
        if idempotency_key == "same-key" and message == "different message":
            raise ValueError("workbench_v2_idempotency_conflict")
        return _conversation_view(
            conversation_id=conversation_id,
            title="Existing conversation",
            event_id="agentv2_event_submit",
            user_text=message,
        ).model_dump(mode="json")

    async def apply_requirement_action(
        self,
        conversation_id: str,
        *,
        action: str,
        item_id: str | None = None,
        selected: bool | None = None,
        text: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkbenchV2ConversationView:
        self.requirement_action_calls.append(
            {
                "conversation_id": conversation_id,
                "action": action,
                "item_id": item_id,
                "selected": selected,
                "text": text,
                "idempotency_key": idempotency_key,
            }
        )
        if conversation_id == "missing":
            raise KeyError(conversation_id)
        if idempotency_key == "same-key":
            raise ValueError("workbench_v2_idempotency_conflict")
        if item_id == "readonly-item":
            raise ValueError("workbench_v2_requirement_form_readonly")
        if item_id == "missing-item":
            raise ValueError("workbench_v2_requirement_item_not_found")
        return _conversation_view(
            conversation_id=conversation_id,
            title="Existing conversation",
            event_id=f"agentv2_event_{action}",
            user_text=action,
        )


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


def test_list_events_returns_incremental_v2_events(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.get("/api/agent/workbench/v2/conversations/agentv2_existing/events?afterStep=1&limit=1")

    assert response.status_code == 200, response.text
    assert response.json() == WorkbenchV2ConversationEventsView(
        conversationId="agentv2_existing",
        afterStep=1,
        latestStep=2,
        events=[_event_view(event_id="agentv2_event_2", step=2, user_text="progress")],
    ).model_dump(mode="json")
    assert fake.event_calls == [("agentv2_existing", 1, 1)]


def test_candidate_detail_route_returns_runtime_backed_v2_detail(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.get(
        "/api/agent/workbench/v2/conversations/agentv2_existing/candidates/identity_1/detail"
    )

    assert response.status_code == 200, response.text
    assert response.json()["candidateId"] == "identity_1"
    assert response.json()["displayName"] == "吴所谓"
    assert response.json()["sections"] == [
        {"title": "匹配程度", "items": ["推荐理由：做过复杂 B 端业务流程。"]}
    ]
    assert fake.detail_calls == [("agentv2_existing", "identity_1")]


def test_missing_list_events_returns_public_reason_code(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.get("/api/agent/workbench/v2/conversations/missing/events")

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": {"reasonCode": "workbench_v2_conversation_not_found"}}
    assert fake.event_calls == [("missing", 0, 100)]


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


def test_idempotency_conflict_returns_409_problem_details(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations",
        json={"message": "different message", "idempotencyKey": "same-key"},
    )

    assert response.status_code == 409, response.text
    payload = response.json()
    assert payload["status"] == 409
    assert payload["reasonCode"] == "workbench_v2_idempotency_conflict"
    assert payload["type"].endswith("/workbench_v2_idempotency_conflict")
    assert fake.create_calls == [("different message", "same-key")]


def test_blank_messages_are_rejected_before_service_call(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    created = client.post(
        "/api/agent/workbench/v2/conversations",
        json={"message": "   ", "idempotencyKey": "blank-create"},
    )
    submitted = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/messages",
        json={"message": "\n\t ", "idempotencyKey": "blank-submit"},
    )

    assert created.status_code == 400, created.text
    assert submitted.status_code == 400, submitted.text
    assert created.json()["reasonCode"] == "agent_request_invalid"
    assert submitted.json()["reasonCode"] == "agent_request_invalid"
    assert fake.create_calls == []
    assert fake.submit_calls == []


def test_overlong_message_and_key_are_rejected_before_service_call(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    overlong_message = "x" * (MAX_AGENT_MESSAGE_CHARS + 1)
    overlong_key = "k" * (MAX_IDEMPOTENCY_KEY_CHARS + 1)
    created = client.post(
        "/api/agent/workbench/v2/conversations",
        json={"message": overlong_message, "idempotencyKey": "length-create"},
    )
    submitted = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/messages",
        json={"message": "continue", "idempotencyKey": overlong_key},
    )

    assert created.status_code == 400, created.text
    assert submitted.status_code == 400, submitted.text
    assert fake.create_calls == []
    assert fake.submit_calls == []


def test_message_and_idempotency_key_are_trimmed_before_service_call(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    created = client.post(
        "/api/agent/workbench/v2/conversations",
        json={"message": "  hello  ", "idempotencyKey": "  create-key  "},
    )
    submitted = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/messages",
        json={"message": "\tcontinue\n", "idempotencyKey": "  submit-key  "},
    )

    assert created.status_code == 201, created.text
    assert submitted.status_code == 200, submitted.text
    assert fake.create_calls == [("hello", "create-key")]
    assert fake.submit_calls == [("agentv2_existing", "continue", "submit-key")]


def test_requirement_actions_call_service_and_return_view(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    set_selected = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={
            "action": "set_selected",
            "itemId": "  must_have_capabilities_1  ",
            "selected": False,
            "idempotencyKey": "  select-1  ",
        },
    )
    add_other = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "add_other", "text": "\t熟悉 LangGraph\n", "idempotencyKey": "other-1"},
    )
    confirm = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "confirm", "idempotencyKey": "confirm-1"},
    )

    assert set_selected.status_code == 200, set_selected.text
    assert add_other.status_code == 200, add_other.text
    assert confirm.status_code == 200, confirm.text
    assert set_selected.json()["conversation"]["conversationId"] == "agentv2_existing"
    assert add_other.json()["transcriptEvents"][0]["payload"] == {"text": "add_other"}
    assert confirm.json()["transcriptEvents"][0]["payload"] == {"text": "confirm"}
    assert fake.requirement_action_calls == [
        {
            "conversation_id": "agentv2_existing",
            "action": "set_selected",
            "item_id": "must_have_capabilities_1",
            "selected": False,
            "text": None,
            "idempotency_key": "select-1",
        },
        {
            "conversation_id": "agentv2_existing",
            "action": "add_other",
            "item_id": None,
            "selected": None,
            "text": "熟悉 LangGraph",
            "idempotency_key": "other-1",
        },
        {
            "conversation_id": "agentv2_existing",
            "action": "confirm",
            "item_id": None,
            "selected": None,
            "text": None,
            "idempotency_key": "confirm-1",
        },
    ]


def test_invalid_requirement_action_payload_rejected_before_service_call(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    missing_item = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "set_selected", "selected": True, "idempotencyKey": "select-1"},
    )
    blank_text = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "add_other", "text": "   ", "idempotencyKey": "other-1"},
    )
    extra_field = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "confirm", "unexpected": True},
    )
    overlong_item = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "set_selected", "itemId": "i" * (MAX_REQUEST_ID_CHARS + 1), "selected": True},
    )
    overlong_text = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "add_other", "text": "x" * (MAX_REQUIREMENT_TEXT_CHARS + 1)},
    )

    assert missing_item.status_code == 400, missing_item.text
    assert blank_text.status_code == 400, blank_text.text
    assert extra_field.status_code == 400, extra_field.text
    assert overlong_item.status_code == 400, overlong_item.text
    assert overlong_text.status_code == 400, overlong_text.text
    assert fake.requirement_action_calls == []


def test_requirement_action_domain_error_returns_400_problem_details(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "set_selected", "itemId": "missing-item", "selected": True},
    )

    assert response.status_code == 400, response.text
    payload = response.json()
    assert payload["status"] == 400
    assert payload["reasonCode"] == "workbench_v2_requirement_item_not_found"
    assert payload["type"].endswith("/workbench_v2_requirement_item_not_found")
    assert fake.requirement_action_calls == [
        {
            "conversation_id": "agentv2_existing",
            "action": "set_selected",
            "item_id": "missing-item",
            "selected": True,
            "text": None,
            "idempotency_key": None,
        }
    ]


def test_requirement_action_idempotency_conflict_returns_409_problem_details(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={
            "action": "set_selected",
            "itemId": "must_have_capabilities_1",
            "selected": True,
            "idempotencyKey": "same-key",
        },
    )

    assert response.status_code == 409, response.text
    payload = response.json()
    assert payload["status"] == 409
    assert payload["reasonCode"] == "workbench_v2_idempotency_conflict"
    assert payload["type"].endswith("/workbench_v2_idempotency_conflict")
    assert fake.requirement_action_calls == [
        {
            "conversation_id": "agentv2_existing",
            "action": "set_selected",
            "item_id": "must_have_capabilities_1",
            "selected": True,
            "text": None,
            "idempotency_key": "same-key",
        }
    ]


def test_requirement_action_readonly_error_returns_400_problem_details(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations/agentv2_existing/requirement-actions",
        json={"action": "set_selected", "itemId": "readonly-item", "selected": True},
    )

    assert response.status_code == 400, response.text
    payload = response.json()
    assert payload["status"] == 400
    assert payload["reasonCode"] == "workbench_v2_requirement_form_readonly"
    assert payload["type"].endswith("/workbench_v2_requirement_form_readonly")
    assert fake.requirement_action_calls == [
        {
            "conversation_id": "agentv2_existing",
            "action": "set_selected",
            "item_id": "readonly-item",
            "selected": True,
            "text": None,
            "idempotency_key": None,
        }
    ]


def test_missing_requirement_action_conversation_returns_public_reason_code(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)

    response = client.post(
        "/api/agent/workbench/v2/conversations/missing/requirement-actions",
        json={"action": "confirm", "idempotencyKey": "confirm-1"},
    )

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": {"reasonCode": "workbench_v2_conversation_not_found"}}
    assert fake.requirement_action_calls == [
        {
            "conversation_id": "missing",
            "action": "confirm",
            "item_id": None,
            "selected": None,
            "text": None,
            "idempotency_key": "confirm-1",
        }
    ]


def test_openapi_declares_v2_error_responses(tmp_path: Path) -> None:
    client, _fake = _client(tmp_path)

    paths = client.app.openapi()["paths"]
    create_responses = paths["/api/agent/workbench/v2/conversations"]["post"]["responses"]
    get_responses = paths["/api/agent/workbench/v2/conversations/{conversation_id}"]["get"]["responses"]
    event_responses = paths["/api/agent/workbench/v2/conversations/{conversation_id}/events"]["get"]["responses"]
    submit_responses = paths["/api/agent/workbench/v2/conversations/{conversation_id}/messages"]["post"][
        "responses"
    ]
    action_responses = paths[
        "/api/agent/workbench/v2/conversations/{conversation_id}/requirement-actions"
    ]["post"]["responses"]

    assert {"201", "400", "409", "503"}.issubset(create_responses)
    assert {"404"}.issubset(get_responses)
    assert {"404"}.issubset(event_responses)
    assert {"404"}.issubset(submit_responses)
    assert {"400", "404", "409", "503"}.issubset(action_responses)
    assert create_responses["400"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProblemDetails")
    assert create_responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProblemDetails")
    assert create_responses["503"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProblemDetails")
    assert action_responses["400"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProblemDetails")
    assert action_responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProblemDetails")
    assert action_responses["503"]["content"]["application/json"]["schema"]["$ref"].endswith("/ProblemDetails")
    assert "reasonCode" in get_responses["404"]["content"]["application/json"]["schema"]["properties"]["detail"][
        "properties"
    ]


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
            _event_view(event_id=event_id, step=1, user_text=user_text)
        ],
        requirementForm=None,
        runtime=None,
    )


def _event_view(*, event_id: str, step: int, user_text: str) -> WorkbenchV2TranscriptEventView:
    return WorkbenchV2TranscriptEventView(
        eventId=event_id,
        step=step,
        type="user_message",
        role="user",
        status="completed",
        payload={"text": user_text},
        createdAt="2026-06-25T01:02:03.000004+00:00",
    )
