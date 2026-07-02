from __future__ import annotations

from collections.abc import Mapping


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
