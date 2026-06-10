from __future__ import annotations

import pytest

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.safety import screen_requirement_text


@pytest.mark.parametrize(
    ("text", "reason_code"),
    [
        ("候选人邮箱 zhang@example.com 可以联系", "agent_free_text_candidate_pii"),
        ("候选人手机 13812345678", "agent_free_text_candidate_pii"),
        ("Cookie: sessionid=abc", "agent_free_text_auth_material"),
        ("Authorization: Bearer sk-test", "agent_free_text_auth_material"),
        ("-----BEGIN RESUME-----\n姓名 张三", "agent_free_text_raw_resume"),
    ],
)
def test_free_text_requirement_safety_rejects_sensitive_fragments(text: str, reason_code: str) -> None:
    with pytest.raises(ConversationAgentError) as exc_info:
        screen_requirement_text(text)

    assert exc_info.value.reason_code == reason_code
    assert "fragmentHash" in exc_info.value.payload
    assert text not in str(exc_info.value.payload)


def test_free_text_requirement_safety_allows_ordinary_hiring_criteria() -> None:
    assert screen_requirement_text("另外希望做过 toB SaaS，频繁跳槽的不要") == (
        "另外希望做过 toB SaaS，频繁跳槽的不要"
    )
