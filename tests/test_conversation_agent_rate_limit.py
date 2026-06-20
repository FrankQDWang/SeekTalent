from __future__ import annotations

from pathlib import Path

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_ui.agent_routes import LocalAgentRateLimiter
from tests.test_conversation_agent_routes import _ensure_local_actor, _client


def test_local_agent_rate_limiter_respects_window_and_bucket_isolation() -> None:
    current = 0.0

    def now() -> float:
        return current

    limiter = LocalAgentRateLimiter(max_writes_per_minute=2, now=now)

    limiter.check(user_id="user_1", conversation_id="conv_1")
    limiter.check(user_id="user_2", conversation_id="conv_1")
    limiter.check(user_id="user_1", conversation_id="conv_2")

    try:
        limiter.check(user_id="user_1", conversation_id="conv_1")
    except ConversationAgentError as exc:
        assert exc.reason_code == "agent_rate_limited"
    else:
        raise AssertionError("expected user bucket to be rate limited")

    current = 60.0
    limiter.check(user_id="user_1", conversation_id="conv_1")


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
