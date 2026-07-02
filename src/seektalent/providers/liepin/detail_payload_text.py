from __future__ import annotations

from collections.abc import Mapping
from typing import cast


PROHIBITED_LIEPIN_WHOLE_PAGE_TEXT_KEYS = frozenset(
    {
        "fullText",
        "full_text",
        "rawText",
        "raw_text",
        "page_text",
        "pageText",
        "resumeText",
        "resume_text",
        "resume_free_text",
        "detailBody",
        "detail_body",
        "profile",
        "summary",
    }
)

STRUCTURED_LIEPIN_DETAIL_TEXT_MAX_CHARS = 4000
_STRUCTURED_SUMMARY_LIST_KEYS = frozenset({"workExperienceList", "projectExperienceList", "educationList"})


def find_liepin_whole_page_text_alias_paths(payload: Mapping[str, object]) -> tuple[str, ...]:
    paths: list[str] = []

    def collect(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                current_path = (*path, key_text)
                if _is_prohibited_payload_key(key_text, parent_path=path):
                    paths.append(_format_path(current_path))
                    continue
                collect(item, current_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect(item, (*path, f"[{index}]"))

    collect(payload, ())
    return tuple(paths)


def sanitize_liepin_provider_payload(payload: Mapping[str, object]) -> dict[str, object]:
    return _sanitize_payload_mapping(payload, ())


def _sanitize_payload_mapping(value: Mapping[str, object], path: tuple[str, ...]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if _is_prohibited_payload_key(key_text, parent_path=path):
            continue
        sanitized[key_text] = _sanitize_payload_value(item, (*path, key_text))
    return sanitized


def _sanitize_payload_value(value: object, path: tuple[str, ...]) -> object:
    if isinstance(value, Mapping):
        return _sanitize_payload_mapping(cast("Mapping[str, object]", value), path)
    if isinstance(value, list):
        return [_sanitize_payload_value(item, (*path, f"[{index}]")) for index, item in enumerate(value)]
    return value


def _is_prohibited_payload_key(key: str, *, parent_path: tuple[str, ...]) -> bool:
    if key not in PROHIBITED_LIEPIN_WHOLE_PAGE_TEXT_KEYS:
        return False
    if key == "summary" and _is_structured_list_item_path(parent_path):
        return False
    return True


def _is_structured_list_item_path(path: tuple[str, ...]) -> bool:
    return len(path) >= 2 and path[-1].startswith("[") and path[-2] in _STRUCTURED_SUMMARY_LIST_KEYS


def _format_path(path: tuple[str, ...]) -> str:
    rendered = ""
    for part in path:
        if part.startswith("["):
            rendered += part
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = part
    return rendered


def structured_liepin_detail_text(
    payload: Mapping[str, object],
    *,
    max_chars: int = STRUCTURED_LIEPIN_DETAIL_TEXT_MAX_CHARS,
) -> str:
    parts: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        text: str | None = None
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, int) and not isinstance(value, bool):
            text = str(value)
        if not text:
            return
        key = " ".join(text.split())
        if key in seen:
            return
        seen.add(key)
        parts.append(text)

    for key in (
        "candidate_name",
        "candidateName",
        "activeStatus",
        "jobStatus",
        "gender",
        "age",
        "city",
        "education",
        "workYears",
        "currentTitle",
        "currentCompany",
    ):
        add(payload.get(key))
    job_intention = payload.get("jobIntention")
    if isinstance(job_intention, Mapping):
        job_intention = cast("Mapping[str, object]", job_intention)
        for key in ("expectedRole", "expectedSalary", "expectedCity", "expectedIndustry"):
            add(job_intention.get(key))
    for list_key, item_keys in (
        ("workExperienceList", ("company", "title", "duration", "dateRange", "summary", "description")),
        ("projectExperienceList", ("name", "role", "company", "duration", "dateRange", "summary", "description")),
        ("educationList", ("school", "major", "degree", "duration", "dateRange", "summary")),
    ):
        value = payload.get(list_key)
        if not isinstance(value, list):
            continue
        for item in value[:8]:
            if not isinstance(item, Mapping):
                continue
            for key in item_keys:
                add(item.get(key))
    skills = payload.get("skills")
    if isinstance(skills, list):
        for skill in skills[:20]:
            add(skill)
    text = " ".join(parts)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()
