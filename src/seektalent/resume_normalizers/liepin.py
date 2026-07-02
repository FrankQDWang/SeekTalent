from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from seektalent.locations import normalize_locations
from seektalent.models import (
    NormalizedExperience,
    NormalizedResume,
    ResumeCandidate,
    StructuredResumeEvidence,
    StructuredResumeTimelineItem,
    stable_fallback_resume_id,
    unique_strings,
)

PROHIBITED_LIEPIN_TEXT_KEYS = frozenset(
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

LEGACY_EXCERPT_LIMIT = 4000


def normalize_liepin_resume(candidate: ResumeCandidate) -> NormalizedResume:
    raw = candidate.raw
    prohibited = sorted(key for key in PROHIBITED_LIEPIN_TEXT_KEYS if key in raw)
    if prohibited:
        raise ValueError(f"Liepin raw payload must not include whole-page text fields: {', '.join(prohibited)}")

    safe_card = _mapping(raw.get("safe_card_summary") or raw.get("safeCardSummary"))
    candidate_name = _first_text(raw.get("candidate_name"), raw.get("candidateName"))
    current_title = _first_text(
        raw.get("currentTitle"),
        raw.get("current_title"),
        safe_card.get("current_or_recent_title"),
        safe_card.get("display_title"),
    )
    current_company = _first_text(
        raw.get("currentCompany"),
        raw.get("current_company"),
        safe_card.get("current_or_recent_company"),
    )
    work_years = _int_or_none(raw.get("workYears"))
    if work_years is None:
        work_years = _int_or_none(safe_card.get("work_years"))
    if work_years is None:
        work_years = candidate.work_year

    location_values: list[str | None] = [
        _text(raw.get("city")),
        candidate.now_location,
        candidate.expected_location,
        _text(safe_card.get("city")),
        _text(safe_card.get("expected_city")),
        *_string_list(raw.get("locations")),
    ]
    locations = normalize_locations(location_values)[:4]
    work_items = _timeline_items(raw.get("workExperienceList"))
    if not work_items:
        work_items = _safe_card_work_items(safe_card)
    project_items = _timeline_items(raw.get("projectExperienceList"))
    education_items = _timeline_items(raw.get("educationList"))
    skills = unique_strings(
        [
            *_string_list(raw.get("skills")),
            *_string_list(raw.get("skillTags")),
            *_string_list(raw.get("tags")),
            *_string_list(raw.get("keywords")),
            *_string_list(safe_card.get("skill_tags")),
        ]
    )[:24]
    recent_experiences = [
        NormalizedExperience(title=item.title, company=item.company, duration=item.duration, summary=item.summary)
        for item in work_items[:4]
    ]
    structured = StructuredResumeEvidence(
        identity=_compact(
            {
                "candidateName": candidate_name,
                "age": _int_or_none(raw.get("age")),
                "gender": _text(raw.get("gender")),
            }
        ),
        current_role=_compact({"title": current_title, "company": current_company, "workYears": work_years}),
        status=_compact({"activeStatus": _text(raw.get("activeStatus")), "jobStatus": _text(raw.get("jobStatus"))}),
        job_intention=_job_intention(raw.get("jobIntention")),
        work_experience=work_items,
        project_experience=project_items,
        education_experience=education_items,
        skills=skills,
        source_metadata=_compact(
            {
                "sourceUrl": _text(raw.get("sourceUrl")),
                "scoreEvidenceSource": _text(raw.get("score_evidence_source")),
            }
        ),
    )
    resume_id = candidate.resume_id
    if candidate.used_fallback_id and not resume_id.startswith("fallback-"):
        resume_id = stable_fallback_resume_id(
            {
                "candidate_name": candidate_name,
                "current_title": current_title,
                "current_company": current_company,
                "locations": locations,
                "recent_experiences": [item.model_dump(mode="json") for item in recent_experiences[:2]],
            }
        )
    education_summary = _education_summary(raw, education_items, safe_card)
    raw_text_excerpt = _structured_text_for_legacy_consumers(structured)
    completeness_score, missing_fields = _completeness_score(
        candidate_name=candidate_name,
        current_title=current_title,
        current_company=current_company,
        work_years=work_years,
        locations=locations,
        education_summary=education_summary,
        skills=skills,
        recent_experiences=recent_experiences,
        raw_text_excerpt=raw_text_excerpt,
    )
    return NormalizedResume(
        resume_id=resume_id,
        dedup_key=candidate.dedup_key,
        used_fallback_id=candidate.used_fallback_id,
        source_provider="liepin",
        candidate_name=candidate_name,
        headline=current_title,
        current_title=current_title,
        current_company=current_company,
        years_of_experience=work_years,
        locations=locations,
        education_summary=education_summary,
        skills=skills,
        industry_tags=[],
        language_tags=[],
        recent_experiences=recent_experiences,
        key_achievements=[item.summary for item in [*work_items, *project_items] if item.summary][:4],
        structured_evidence=structured,
        raw_text_excerpt=raw_text_excerpt,
        completeness_score=completeness_score,
        missing_fields=missing_fields,
        normalization_notes=["Normalized from Liepin structured detail."],
        source_round=candidate.source_round,
        score_evidence_source=_text(raw.get("score_evidence_source")),
        card_scorecard_ref=_text(raw.get("card_scorecard_ref")),
        detail_scorecard_ref=_text(raw.get("detail_scorecard_ref")),
        score_delta=_int_or_none(raw.get("score_delta")),
        detail_open_reason=_text(raw.get("detail_open_reason")),
        detail_open_policy_version=_text(raw.get("detail_open_policy_version")),
    )


def _timeline_items(value: object) -> list[StructuredResumeTimelineItem]:
    items: list[StructuredResumeTimelineItem] = []
    if not isinstance(value, list):
        return items
    for raw_item in value[:8]:
        if not isinstance(raw_item, Mapping):
            continue
        item = StructuredResumeTimelineItem(
            company=_first_text(raw_item.get("company"), raw_item.get("companyName")),
            title=_first_text(raw_item.get("title"), raw_item.get("position"), raw_item.get("positionName")),
            name=_first_text(raw_item.get("name"), raw_item.get("projectName")),
            school=_first_text(raw_item.get("school"), raw_item.get("schoolName")),
            major=_first_text(raw_item.get("major"), raw_item.get("majorName"), raw_item.get("speciality")),
            degree=_first_text(raw_item.get("degree"), raw_item.get("education"), raw_item.get("educationLevel")),
            duration=_first_text(
                raw_item.get("duration"),
                raw_item.get("time"),
                raw_item.get("dateRange"),
                raw_item.get("startEndTime"),
            ),
            summary=_first_text(
                raw_item.get("summary"),
                raw_item.get("description"),
                raw_item.get("workContent"),
                raw_item.get("content"),
            ),
        )
        if any(item.model_dump(mode="json").values()):
            items.append(item)
    return items


def _safe_card_work_items(safe_card: Mapping[str, object]) -> list[StructuredResumeTimelineItem]:
    title = _first_text(safe_card.get("current_or_recent_title"), safe_card.get("display_title"))
    company = _text(safe_card.get("current_or_recent_company"))
    work_years = _int_or_none(safe_card.get("work_years"))
    summary = _first_text(safe_card.get("recent_experience_text"), safe_card.get("normalized_card_text"))
    item = StructuredResumeTimelineItem(
        company=company,
        title=title,
        duration=f"{work_years}y" if work_years is not None else "",
        summary=summary,
    )
    return [item] if any(item.model_dump(mode="json").values()) else []


def _structured_text_for_legacy_consumers(evidence: StructuredResumeEvidence) -> str:
    parts: list[str] = []
    parts.extend(str(value) for value in evidence.current_role.values() if str(value).strip())
    parts.extend(evidence.skills)
    for item in [*evidence.work_experience, *evidence.project_experience]:
        parts.extend(part for part in [item.company, item.title, item.name, item.duration, item.summary] if part)
    return " ".join(parts)[:LEGACY_EXCERPT_LIMIT]


def _education_summary(
    raw: Mapping[str, object], education_items: list[StructuredResumeTimelineItem], safe_card: Mapping[str, object]
) -> str:
    if education_items:
        first = education_items[0]
        return " ".join(part for part in [first.school, first.major, first.degree] if part)
    safe_card_summary = " ".join(
        part
        for part in [
            *_string_list(safe_card.get("school_names"))[:2],
            *_string_list(safe_card.get("major_names"))[:2],
            _text(safe_card.get("education_level")),
        ]
        if part
    )
    if safe_card_summary:
        return safe_card_summary
    return _text(raw.get("education"))


def _job_intention(value: object) -> dict[str, str | int]:
    if not isinstance(value, Mapping):
        return {}
    value_mapping = cast(Mapping[str, object], value)
    return _compact(
        {
            "expectedRole": _first_text(value_mapping.get("expectedRole"), value_mapping.get("expectedTitle")),
            "expectedIndustry": _text(value_mapping.get("expectedIndustry")),
            "expectedCity": _first_text(value_mapping.get("expectedCity"), value_mapping.get("expectedLocation")),
            "expectedSalary": _text(value_mapping.get("expectedSalary")),
        }
    )


def _completeness_score(
    *,
    candidate_name: str,
    current_title: str,
    current_company: str,
    work_years: int | None,
    locations: list[str],
    education_summary: str,
    skills: list[str],
    recent_experiences: list[NormalizedExperience],
    raw_text_excerpt: str,
) -> tuple[int, list[str]]:
    checks = {
        "candidate_name": bool(candidate_name),
        "current_title": bool(current_title),
        "current_company": bool(current_company),
        "years_of_experience": work_years is not None,
        "locations": bool(locations),
        "education_summary": bool(education_summary),
        "skills": bool(skills),
        "recent_experiences": bool(recent_experiences),
        "raw_text_excerpt": bool(raw_text_excerpt),
    }
    missing_fields = [field for field, present in checks.items() if not present]
    return max(0, 100 - len(missing_fields) * 12), missing_fields


def _compact(value: Mapping[str, object | None]) -> dict[str, str | int]:
    compacted: dict[str, str | int] = {}
    for key, item in value.items():
        if isinstance(item, str) and item.strip():
            compacted[key] = item.strip()
        elif isinstance(item, int) and not isinstance(item, bool):
            compacted[key] = item
    return compacted


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _string_list(value: object) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
