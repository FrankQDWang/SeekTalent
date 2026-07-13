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

type TimelinePeriod = tuple[int, int, int]
type WorkVersionItem = tuple[TimelinePeriod, str, str, str]
type DetailedVersionItem = tuple[TimelinePeriod, str, str, str, str]
type TimelineVersionItem = WorkVersionItem | DetailedVersionItem


@dataclass(frozen=True)
class ResumeContentVersion:
    resume_id: str
    freshness: tuple[int, int] | None
    content_key: str
    current_company: str
    current_title: str
    work_items: tuple[WorkVersionItem, ...]
    project_items: tuple[DetailedVersionItem, ...]
    education_items: tuple[DetailedVersionItem, ...]


def resume_content_version(resume_id: str, normalized: NormalizedResume | None) -> ResumeContentVersion:
    if normalized is None:
        payload: dict[str, object] = {
            "current_role": {},
            "work_experience": [],
            "project_experience": [],
            "education_experience": [],
        }
        return ResumeContentVersion(
            resume_id=resume_id,
            freshness=None,
            content_key=_content_key(payload),
            current_company="",
            current_title="",
            work_items=(),
            project_items=(),
            education_items=(),
        )

    structured = normalized.structured_evidence
    current_company = _text(structured.current_role.get("company"))
    current_title = _text(structured.current_role.get("title"))
    work_items = tuple(sorted(_work_item(item) for item in structured.work_experience))
    project_items = tuple(sorted(_project_item(item) for item in structured.project_experience))
    education_items = tuple(sorted(_education_item(item) for item in structured.education_experience))
    timeline_items = (
        *structured.work_experience,
        *structured.project_experience,
        *structured.education_experience,
    )
    freshness_values = [_timeline_freshness(item.duration) for item in timeline_items]
    known_freshness = [value for value in freshness_values if value is not None]
    freshness = max(known_freshness) if known_freshness else None
    payload = {
        "current_role": {"company": current_company, "title": current_title},
        "work_experience": [
            {"period": list(period), "company": company, "title": title, "summary": summary}
            for period, company, title, summary in work_items
        ],
        "project_experience": [
            {
                "period": list(period),
                "name": name,
                "company": company,
                "title": title,
                "summary": summary,
            }
            for period, name, company, title, summary in project_items
        ],
        "education_experience": [
            {
                "period": list(period),
                "school": school,
                "major": major,
                "degree": degree,
                "summary": summary,
            }
            for period, school, major, degree, summary in education_items
        ],
    }
    return ResumeContentVersion(
        resume_id=resume_id,
        freshness=freshness,
        content_key=_content_key(payload),
        current_company=current_company,
        current_title=current_title,
        work_items=work_items,
        project_items=project_items,
        education_items=education_items,
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

    for left_items, right_items, material_field_count in (
        (left.work_items, right.work_items, 2),
        (left.project_items, right.project_items, 3),
        (left.education_items, right.education_items, 3),
    ):
        item_consistency = _timeline_items_consistent(
            left_items,
            right_items,
            material_field_count=material_field_count,
        )
        if item_consistency is False:
            return False
        if item_consistency is True:
            overlap = True
    return True if overlap else None


def _work_item(item: StructuredResumeTimelineItem) -> WorkVersionItem:
    return (
        _period_key(item.duration),
        _normalize_text(item.company),
        _normalize_text(item.title),
        _normalize_text(item.summary),
    )


def _project_item(item: StructuredResumeTimelineItem) -> DetailedVersionItem:
    return (
        _period_key(item.duration),
        _normalize_text(item.name),
        _normalize_text(item.company),
        _normalize_text(item.title),
        _normalize_text(item.summary),
    )


def _education_item(item: StructuredResumeTimelineItem) -> DetailedVersionItem:
    return (
        _period_key(item.duration),
        _normalize_text(item.school),
        _normalize_text(item.major),
        _normalize_text(item.degree),
        _normalize_text(item.summary),
    )


def _timeline_freshness(duration: str) -> tuple[int, int] | None:
    normalized_duration = _normalize_text(duration)
    dates = _dates(normalized_duration)
    if _has_current_marker(normalized_duration):
        return (1, 0)
    if dates:
        return (0, max(dates))
    return None


def _timeline_items_consistent(
    left_items: tuple[TimelineVersionItem, ...],
    right_items: tuple[TimelineVersionItem, ...],
    *,
    material_field_count: int,
) -> bool | None:
    comparisons = tuple(
        tuple(
            _timeline_item_consistency(
                left_item,
                right_item,
                material_field_count=material_field_count,
            )
            for right_item in right_items
        )
        for left_item in left_items
    )
    if not comparisons or not right_items:
        return None
    forward = _directed_timeline_consistency(comparisons)
    reverse = _directed_timeline_consistency(tuple(zip(*comparisons, strict=True)))
    return forward if forward == reverse else None


def _directed_timeline_consistency(comparisons: tuple[tuple[bool | None, ...], ...]) -> bool | None:
    right_to_left: dict[int, int] = {}

    def match(left_index: int, seen_right: set[int]) -> bool:
        for right_index, consistency in enumerate(comparisons[left_index]):
            if consistency is not True or right_index in seen_right:
                continue
            seen_right.add(right_index)
            previous_left = right_to_left.get(right_index)
            if previous_left is None or match(previous_left, seen_right):
                right_to_left[right_index] = left_index
                return True
        return False

    for left_index in range(len(comparisons)):
        match(left_index, set())

    matched_left = set(right_to_left.values())
    matched_right = set(right_to_left)
    for left_index, row in enumerate(comparisons):
        if left_index in matched_left:
            continue
        for right_index, consistency in enumerate(row):
            if right_index not in matched_right and consistency is False:
                return False
    return True if matched_left else None


def _timeline_item_consistency(
    left_item: TimelineVersionItem,
    right_item: TimelineVersionItem,
    *,
    material_field_count: int,
) -> bool | None:
    if not _periods_overlap(left_item[0], right_item[0]):
        return None
    overlap = False
    for left_value, right_value in zip(
        left_item[1 : material_field_count + 1],
        right_item[1 : material_field_count + 1],
        strict=True,
    ):
        if left_value and right_value:
            overlap = True
            if left_value != right_value:
                return False
    return True if overlap else None


def _period_key(duration: str) -> TimelinePeriod:
    normalized_duration = _normalize_text(duration)
    dates = _dates(normalized_duration)
    current = int(_has_current_marker(normalized_duration))
    return (dates[0] if dates else 0, dates[-1] if dates else 0, current)


def _dates(value: str) -> tuple[int, ...]:
    return tuple(
        int(match.group("year")) * 100 + int(match.group("month") or 1) for match in _DATE_PATTERN.finditer(value)
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


def _periods_overlap(left: TimelinePeriod, right: TimelinePeriod) -> bool:
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
    return "structured-v2:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
