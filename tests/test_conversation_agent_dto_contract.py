from __future__ import annotations

from pathlib import Path

from tests.test_conversation_agent_routes import _bootstrap_and_login, _client, _csrf_header


def test_agent_route_responses_are_camel_case_and_schema_versioned(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)

    response = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
        headers=_csrf_header(client),
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.conversation.v1"
    assert "conversationId" in payload["conversation"]
    assert "conversation_id" not in str(payload)


def test_agent_route_rejects_invalid_request_shape_with_stable_reason_code(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)

    response = client.post(
        "/api/agent/conversations",
        json={"title": "   ", "unknownField": True},
        headers=_csrf_header(client),
    )

    assert response.status_code == 400
    assert response.json()["schemaVersion"] == "agent.conversation.v1"
    assert response.json()["reasonCode"] == "agent_request_invalid"


def test_memory_route_rejects_invalid_request_shape_with_memory_schema_version(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)

    response = client.put(
        "/api/agent/memory/settings",
        json={"memoryEnabled": True, "unknownField": True},
        headers=_csrf_header(client),
    )

    assert response.status_code == 400
    assert response.json()["schemaVersion"] == "agent.memory.v2"
    assert response.json()["reasonCode"] == "agent_request_invalid"
