from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata

from seektalent.models import NormalizedResume, StructuredResumeTimelineItem


_ENGLISH_CURRENT_MARKER_PATTERN = re.compile(r"\b(?:present|current|now)\b")
_ENGLISH_NOT_CURRENT_PATTERN = re.compile(r"\b(?:not\s+current|not\s+presently\s+employed)\b")
_CHINESE_NOT_CURRENT_PATTERNS = (
    re.compile(r"目前不在职"),
    re.compile(r"现在不在职"),
    re.compile(r"不在职"),
)
_CHINESE_CURRENT_MARKER_PATTERNS = (
    re.compile(r"至今"),
    re.compile(r"现在"),
    re.compile(r"目前"),
    re.compile(r"(?<!不)在职"),
)
_DATE_PATTERN = re.compile(r"(?<!\d)(?P<year>(?:19|20)\d{2})(?:\s*(?:[-/.年])\s*(?P<month>1[0-2]|0?[1-9])月?)?")


@dataclass(frozen=True)
class ResumeContentVersion:
    resume_id: str
    freshness: tuple[int, int] | None
    content_key: str
    current_company: str
    current_title: str
    work_items: tuple[tuple[tuple[int, int, int], str, str], ...]


def resume_content_version(resume_id: str, normalized: NormalizedResume | None) -> ResumeContentVersion:
    if normalized is None:
        payload: dict[str, object] = {"current_role": {}, "work_experience": []}
        return ResumeContentVersion(
            resume_id=resume_id,
            freshness=None,
            content_key=_content_key(payload),
            current_company="",
            current_title="",
            work_items=(),
        )

    structured = normalized.structured_evidence
    current_company = _text(structured.current_role.get("company"))
    current_title = _text(structured.current_role.get("title"))
    work_items = tuple(sorted(_work_item(item) for item in structured.work_experience))
    freshness_values = [_work_freshness(item.duration) for item in structured.work_experience]
    known_freshness = [value for value in freshness_values if value is not None]
    freshness = max(known_freshness) if known_freshness else None
    payload = {
        "current_role": {"company": current_company, "title": current_title},
        "work_experience": [
            {"period": list(period), "company": company, "title": title}
            for period, company, title in work_items
        ],
    }
    return ResumeContentVersion(
        resume_id=resume_id,
        freshness=freshness,
        content_key=_content_key(payload),
        current_company=current_company,
        current_title=current_title,
        work_items=work_items,
    )


def materially_consistent(left: ResumeContentVersion, right: ResumeContentVersion) -> bool | None:
    overlap = False
    for left_value, right_value in (
        (left.current_company, right.current_company),
        (left.current_title, right.current_title),
    ):
        if left_value and right_value:
            overlap = True
            if left_value != right_value:
                return False

    for left_period, left_company, left_title in left.work_items:
        for right_period, right_company, right_title in right.work_items:
            if not _periods_overlap(left_period, right_period):
                continue
            for left_value, right_value in (
                (left_company, right_company),
                (left_title, right_title),
            ):
                if left_value and right_value:
                    overlap = True
                    if left_value != right_value:
                        return False
    return True if overlap else None


def _work_item(item: StructuredResumeTimelineItem) -> tuple[tuple[int, int, int], str, str]:
    return (_period_key(item.duration), _normalize_text(item.company), _normalize_text(item.title))


def _work_freshness(duration: str) -> tuple[int, int] | None:
    normalized_duration = _normalize_text(duration)
    dates = _dates(normalized_duration)
    if _has_current_marker(normalized_duration):
        return (1, 0)
    if dates:
        return (0, max(dates))
    return None


def _period_key(duration: str) -> tuple[int, int, int]:
    normalized_duration = _normalize_text(duration)
    dates = _dates(normalized_duration)
    current = int(_has_current_marker(normalized_duration))
    return (dates[0] if dates else 0, dates[-1] if dates else 0, current)


def _dates(value: str) -> tuple[int, ...]:
    return tuple(
        int(match.group("year")) * 100 + int(match.group("month") or 1)
        for match in _DATE_PATTERN.finditer(value)
    )


def _has_current_marker(value: str) -> bool:
    if _ENGLISH_NOT_CURRENT_PATTERN.search(value) or any(
        pattern.search(value) for pattern in _CHINESE_NOT_CURRENT_PATTERNS
    ):
        return False
    return bool(
        _ENGLISH_CURRENT_MARKER_PATTERN.search(value)
        or any(pattern.search(value) for pattern in _CHINESE_CURRENT_MARKER_PATTERNS)
    )


def _periods_overlap(left: tuple[int, int, int], right: tuple[int, int, int]) -> bool:
    left_start, left_end, left_current = left
    right_start, right_end, right_current = right
    if not left_start or not right_start:
        return False
    left_effective_end = 999912 if left_current else left_end
    right_effective_end = 999912 if right_current else right_end
    if not left_effective_end or not right_effective_end:
        return False
    return max(left_start, right_start) <= min(left_effective_end, right_effective_end)


def _text(value: object) -> str:
    return _normalize_text(str(value)) if isinstance(value, str | int) else ""


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _content_key(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "work-v1:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
