from __future__ import annotations

import hashlib
import json
import re

from seektalent_agent_memory.models import PrivacyReview


class MemoryPrivacyError(ValueError):
    pass


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_AUTH_RE = re.compile(r"\b(cookie|authorization|bearer|sessionid|set-cookie|x-api-key|api[_-]?key)\b", re.IGNORECASE)
_RESUME_RE = re.compile(r"(?:BEGIN RESUME|简历原文|教育经历|工作经历).{0,120}(?:姓名|电话|邮箱)", re.IGNORECASE | re.DOTALL)
_PROVIDER_PAYLOAD_RE = re.compile(r"\b(providerPayload|provider_payload|rawProvider|sourcePayload|profileUrl)\b", re.IGNORECASE)
_RUNTIME_PAYLOAD_RE = re.compile(r"\b(runtimeEvent|runtime_event|eventPayload|checkpointPayload)\b", re.IGNORECASE)
_REQUIREMENT_JSON_KEYS = {
    "must_have_capabilities",
    "preferred_capabilities",
    "exclusion_signals",
    "hard_constraints",
    "initial_query_term_pool",
    "scoring_rationale",
}
_CANDIDATE_SCORE_RE = re.compile(r"\b(final_score|score|rank|ranking|candidateScores)\b|评分|排名", re.IGNORECASE)
_FULL_JD_RE = re.compile(r"\b(fullJd|full_jd|jdText|jobDescription)\b|JD原文|岗位描述原文", re.IGNORECASE)
_INSTRUCTION_LIKE_RE = re.compile(
    r"("
    r"(忽略|无视|覆盖|绕过|删除|修改).{0,24}(系统|开发者|规则|指令|安全|隐私)"
    r"|直接确认需求|自动确认需求|启动检索并修改需求"
    r"|ignore.{0,24}(system|developer|previous).{0,24}(instruction|message|rule)"
    r"|disregard.{0,24}(system|developer|previous).{0,24}(instruction|message|rule)"
    r"|system prompt|developer message"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def filter_memory_text(text: str) -> str:
    return filter_memory_candidate(text).safe_text


def filter_memory_candidate(text: str) -> PrivacyReview:
    raw = text
    clean = raw.strip()
    if not clean:
        raise MemoryPrivacyError("agent_memory_empty")
    _reject_if_forbidden(clean)
    safe_excerpt = clean[:240]
    return PrivacyReview(
        safe_text=clean,
        safe_excerpt=safe_excerpt,
        raw_candidate_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def is_recall_safe(text: str) -> bool:
    try:
        filter_memory_candidate(text)
    except MemoryPrivacyError:
        return False
    return True


def _reject_if_forbidden(clean: str) -> None:
    if _looks_like_requirement_json(clean):
        raise MemoryPrivacyError("agent_memory_privacy_requirement_json")
    if _PROVIDER_PAYLOAD_RE.search(clean):
        raise MemoryPrivacyError("agent_memory_privacy_provider_payload")
    if _RUNTIME_PAYLOAD_RE.search(clean):
        raise MemoryPrivacyError("agent_memory_privacy_runtime_payload")
    if _FULL_JD_RE.search(clean):
        raise MemoryPrivacyError("agent_memory_privacy_requirement_json")
    if _INSTRUCTION_LIKE_RE.search(clean):
        raise MemoryPrivacyError("agent_memory_privacy_instruction")
    if _CANDIDATE_SCORE_RE.search(clean):
        raise MemoryPrivacyError("agent_memory_privacy_candidate_score")
    if _RESUME_RE.search(clean):
        raise MemoryPrivacyError("agent_memory_privacy_raw_resume")
    if _EMAIL_RE.search(clean) or _PHONE_RE.search(clean) or "profile_url" in clean:
        raise MemoryPrivacyError("agent_memory_privacy_candidate_pii")
    if _AUTH_RE.search(clean):
        raise MemoryPrivacyError("agent_memory_privacy_auth_material")


def _looks_like_requirement_json(clean: str) -> bool:
    if any(key in clean for key in _REQUIREMENT_JSON_KEYS):
        return True
    if not clean.startswith(("{", "[")):
        return False
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        return False
    return _json_has_requirement_key(parsed)


def _json_has_requirement_key(value: object) -> bool:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        if keys & _REQUIREMENT_JSON_KEYS:
            return True
        return any(_json_has_requirement_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_json_has_requirement_key(item) for item in value)
    return False
