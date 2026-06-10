from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from seektalent_agent_memory.privacy import is_recall_safe


class MemoryTranscriptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    item_kind: str
    role: str
    text: str
    created_at: str
    payload: dict[str, object] = Field(default_factory=dict)


def serialize_filtered_transcript_items(items: list[MemoryTranscriptItem], *, max_chars: int) -> str:
    safe_items = [_item_payload(item) for item in items if _include_item(item)]
    rendered = json.dumps(safe_items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(rendered) <= max_chars:
        return rendered
    keep = max_chars // 2
    return rendered[:keep] + "\n[transcript truncated]\n" + rendered[-keep:]


def _include_item(item: MemoryTranscriptItem) -> bool:
    if item.role in {"developer", "system"}:
        return False
    text = item.text.strip()
    if not text:
        return False
    if item.role == "user" and _excluded_contextual_user_fragment(text):
        return False
    if _looks_like_raw_jd(item):
        return False
    if _text_has_forbidden_memory_truth(text):
        return False
    if not is_recall_safe(text):
        return False
    if _payload_has_forbidden_memory_truth(item.payload):
        return False
    return True


def _excluded_contextual_user_fragment(text: str) -> bool:
    stripped_start = text.lstrip()
    stripped_end = text.rstrip()
    return (
        stripped_start.lower().startswith("# agents.md instructions for ")
        and stripped_end.lower().endswith("</instructions>")
    ) or (stripped_start.startswith("<skill>") and stripped_end.endswith("</skill>"))


def _looks_like_raw_jd(item: MemoryTranscriptItem) -> bool:
    if item.role != "user":
        return False
    text = item.text.strip()
    if text.startswith(("JD原文", "岗位描述原文")):
        return True
    payload_keys = {_normalize_key(key) for key in item.payload}
    return "jobtitle" in payload_keys


def _payload_has_forbidden_memory_truth(payload: dict[str, object]) -> bool:
    return _contains_forbidden_key(payload)


_FORBIDDEN_TEXT_RE = re.compile(
    r"("
    r"\b(requirementDraft|must_have_capabilities|preferred_capabilities|hard_constraints)\b"
    r"|\b(final_score|candidateScores|ranking|rank)\b"
    r"|\b(runtimeEvent|runtime_event|eventPayload|checkpointPayload)\b"
    r"|\b(providerPayload|provider_payload|rawProvider|sourcePayload)\b"
    r"|\b(fullJd|full_jd|jdText|jobDescription)\b"
    r"|JD原文|岗位描述原文"
    r")",
    re.IGNORECASE,
)


def _text_has_forbidden_memory_truth(text: str) -> bool:
    return _FORBIDDEN_TEXT_RE.search(text) is not None


def _contains_forbidden_key(value: object) -> bool:
    forbidden = {
        "requirementdraft",
        "musthavecapabilities",
        "preferredcapabilities",
        "exclusionsignals",
        "hardconstraints",
        "initialquerytermpool",
        "scoringrationale",
        "finalscore",
        "score",
        "rank",
        "ranking",
        "candidatescores",
        "runtimeevent",
        "providerpayload",
        "rawprovider",
        "sourcepayload",
        "checkpointevent",
        "checkpointpayload",
        "eventpayload",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalize_key(key)
            if normalized in forbidden:
                return True
            if _contains_forbidden_key(item):
                return True
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _normalize_key(value: object) -> str:
    return str(value).replace("_", "").replace("-", "").lower()


def _item_payload(item: MemoryTranscriptItem) -> dict[str, object]:
    return {
        "id": item.item_id,
        "kind": item.item_kind,
        "role": item.role,
        "text": item.text.strip(),
        "createdAt": item.created_at,
    }
