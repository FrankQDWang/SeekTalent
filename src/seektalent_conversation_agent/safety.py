from __future__ import annotations

import hashlib
import re

from seektalent_conversation_agent.errors import ConversationAgentError


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_AUTH_MARKERS = (
    "coo" "kie",
    "authori" "zation",
    "bear" "er",
    "session" "id",
    "set-" "coo" "kie",
)
_AUTH_RE = re.compile(r"\b(?:" + "|".join(re.escape(marker) for marker in _AUTH_MARKERS) + r")\b", re.IGNORECASE)
_SECRET_RE = re.compile(r"\b(?:sk|ak|api[_-]?key|secret|token)[-_A-Za-z0-9]{8,}\b", re.IGNORECASE)
_RESUME_RE = re.compile(r"(?:BEGIN RESUME|简历原文|教育经历|工作经历).{0,40}(?:姓名|电话|邮箱)", re.IGNORECASE | re.DOTALL)
_SUMMARY_INSTRUCTION_RE = re.compile(
    r"(?:忽略|无视|覆盖|绕过).{0,24}(?:系统|开发者|规则|指令)"
    r"|直接确认需求|自动确认需求"
    r"|ignore.{0,40}(?:system|developer|previous).{0,40}(?:rule|instruction)"
    r"|disregard.{0,40}(?:rule|instruction)"
    r"|system prompt|developer message",
    re.IGNORECASE | re.DOTALL,
)
_SUMMARY_FILTER_MARKER = "[filtered_summary_fragment]"
MAX_REQUIREMENT_TEXT_CHARS = 2000
MAX_SECTION_HINT_CHARS = 120


def screen_requirement_text(text: str) -> str:
    clean = text.strip()
    if not clean:
        raise ConversationAgentError("agent_free_text_empty")
    if len(clean) > MAX_REQUIREMENT_TEXT_CHARS:
        raise ConversationAgentError(
            "agent_free_text_too_long",
            payload={"maxChars": MAX_REQUIREMENT_TEXT_CHARS, "actualChars": len(clean)},
        )
    if _EMAIL_RE.search(clean) or _PHONE_RE.search(clean):
        _raise_rejected("agent_free_text_candidate_pii", clean)
    if _AUTH_RE.search(clean) or _SECRET_RE.search(clean):
        _raise_rejected("agent_free_text_auth_material", clean)
    if _RESUME_RE.search(clean):
        _raise_rejected("agent_free_text_raw_resume", clean)
    return clean


def screen_target_section_hint(hint: str | None) -> str | None:
    if hint is None:
        return None
    clean = hint.strip()
    if not clean:
        return None
    if len(clean) > MAX_SECTION_HINT_CHARS:
        raise ConversationAgentError(
            "agent_target_section_hint_too_long",
            payload={"maxChars": MAX_SECTION_HINT_CHARS, "actualChars": len(clean)},
        )
    return clean


def sanitize_summary_text(text: str) -> str:
    clean = text.strip()
    if not clean:
        return clean
    sanitized = _SUMMARY_INSTRUCTION_RE.sub(_SUMMARY_FILTER_MARKER, clean)
    for pattern in (_EMAIL_RE, _PHONE_RE, _AUTH_RE, _SECRET_RE, _RESUME_RE):
        sanitized = pattern.sub(_SUMMARY_FILTER_MARKER, sanitized)
    return re.sub(
        rf"(?:{re.escape(_SUMMARY_FILTER_MARKER)}\s*){{2,}}",
        f"{_SUMMARY_FILTER_MARKER} ",
        sanitized,
    ).strip()


def _raise_rejected(reason_code: str, text: str) -> None:
    raise ConversationAgentError(
        reason_code,
        payload={"fragmentHash": hashlib.sha256(text.encode("utf-8")).hexdigest()},
    )
