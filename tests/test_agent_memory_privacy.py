from __future__ import annotations

import pytest

from seektalent_agent_memory.privacy import MemoryPrivacyError, filter_memory_text


@pytest.mark.parametrize(
    "text",
    [
        "候选人电话 13812345678",
        "Authorization: Bearer sk-test",
        "-----BEGIN RESUME----- 姓名 张三 电话 13812345678",
    ],
)
def test_memory_privacy_filter_rejects_sensitive_text_before_persistence(text: str) -> None:
    with pytest.raises(MemoryPrivacyError):
        filter_memory_text(text)


def test_memory_privacy_filter_allows_safe_preference() -> None:
    assert filter_memory_text("偏好 toB SaaS 平台经验") == "偏好 toB SaaS 平台经验"


def test_privacy_filter_rejects_requirement_json_and_candidate_scores() -> None:
    from seektalent_agent_memory.privacy import filter_memory_candidate

    unsafe = '{"must_have_capabilities":["Python"],"final_score":91,"candidate":"张三"}'

    with pytest.raises(MemoryPrivacyError) as exc:
        filter_memory_candidate(unsafe)

    assert str(exc.value) == "agent_memory_privacy_requirement_json"


def test_privacy_filter_returns_hash_and_safe_excerpt_without_raw_secret() -> None:
    from seektalent_agent_memory.privacy import filter_memory_candidate

    result = filter_memory_candidate("用户偏好候选人总结先讲业务匹配，再讲风险。")

    assert result.safe_text == "用户偏好候选人总结先讲业务匹配，再讲风险。"
    assert result.safe_excerpt == result.safe_text
    assert len(result.raw_candidate_hash) == 64
