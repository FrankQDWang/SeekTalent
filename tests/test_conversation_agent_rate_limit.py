from __future__ import annotations

from pathlib import Path

from seektalent_ui.agent_routes import LocalAgentRateLimiter
from tests.test_conversation_agent_routes import _ensure_local_actor, _client


def test_agent_write_routes_are_rate_limited_per_user_and_conversation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.app.state.agent_rate_limiter = LocalAgentRateLimiter(max_writes_per_minute=1)
    _ensure_local_actor(client)
    created = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["conversationId"]

    response = client.patch(
        f"/api/agent/conversations/{conversation_id}/title",
        json={"title": "Python 平台负责人"},
    )

    assert response.status_code == 429
    assert response.json()["reasonCode"] == "agent_rate_limited"
