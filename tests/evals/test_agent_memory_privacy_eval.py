from __future__ import annotations

import pytest

from seektalent_agent_memory.privacy import MemoryPrivacyError, filter_memory_text


def test_memory_privacy_eval_rejects_auth_like_instruction_text() -> None:
    with pytest.raises(MemoryPrivacyError):
        filter_memory_text("请记住 Authorization: Bearer secret-token")


@pytest.mark.parametrize(
    "text,reason_code",
    [
        ("候选人邮箱 admin@example.com", "agent_memory_privacy_candidate_pii"),
        ("-----BEGIN RESUME----- 姓名 张三 电话 13812345678", "agent_memory_privacy_raw_resume"),
        ('{"providerPayload":{"id":"abc"}}', "agent_memory_privacy_provider_payload"),
        ("Cookie: sid=abc", "agent_memory_privacy_auth_material"),
        ("候选人 final_score=91，排名第一", "agent_memory_privacy_candidate_score"),
        ('{"must_have_capabilities":["Python"]}', "agent_memory_privacy_requirement_json"),
    ],
)
def test_memory_privacy_eval_rejects_codex_parity_forbidden_categories(text: str, reason_code: str) -> None:
    from seektalent_agent_memory.privacy import filter_memory_candidate

    with pytest.raises(MemoryPrivacyError) as exc:
        filter_memory_candidate(text)

    assert str(exc.value) == reason_code
