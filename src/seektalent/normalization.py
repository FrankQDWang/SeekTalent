from __future__ import annotations

from typing import Any

from seektalent.locations import normalize_locations
from seektalent.models import (
    NormalizedExperience,
    NormalizedResume,
    ResumeCandidate,
    stable_fallback_resume_id,
    unique_strings,
)

EXCERPT_LIMIT = 700


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_strings(values: list[Any]) -> list[str]:
    cleaned = []
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                cleaned.append(text)
    return unique_strings(cleaned)


def _extract_name(raw: dict[str, Any]) -> str:
    return _first_text(
        raw.get("candidate_name"),
        raw.get("candidateName"),
        raw.get("name"),
        raw.get("fullName"),
    )


def _extract_recent_experiences(
    candidate: ResumeCandidate,
    *,
    normalization_notes: list[str],
) -> list[NormalizedExperience]:
    raw_items = candidate.raw.get("workExperienceList")
    experiences: list[NormalizedExperience] = []
    if isinstance(raw_items, list):
        for item in raw_items[:4]:
            if not isinstance(item, dict):
                continue
            experience = NormalizedExperience(
                title=_first_text(item.get("title")),
                company=_first_text(item.get("company")),
                duration=_first_text(item.get("duration"), item.get("startTime")),
                summary=_first_text(item.get("summary")),
            )
            if experience.title or experience.company or experience.summary:
                experiences.append(experience)
    if experiences:
        if len(raw_items or []) > 4:
            normalization_notes.append("Trimmed workExperienceList to the most recent 4 entries.")
        return experiences[:4]

    for summary in candidate.work_experience_summaries[:4]:
        parts = [part.strip() for part in summary.split("|")]
        experience = NormalizedExperience(
            company=parts[0] if len(parts) > 0 else "",
            title=parts[1] if len(parts) > 1 else "",
            summary=parts[2] if len(parts) > 2 else summary,
        )
        if experience.title or experience.company or experience.summary:
            experiences.append(experience)
    if experiences:
        normalization_notes.append("Built recent_experiences from flattened work experience summaries.")
    return experiences


def _extract_skills(
    candidate: ResumeCandidate,
    *,
    normalization_notes: list[str],
) -> list[str]:
    explicit = []
    for key in ("skills", "skillTags", "tags", "keywords"):
        value = candidate.raw.get(key)
        if isinstance(value, list):
            explicit.extend(value)
    skills = _normalize_strings(explicit)
    if skills:
        return skills[:12]

    derived = [
        item
        for item in candidate.work_summaries
        if isinstance(item, str) and item.strip() and len(item.strip()) <= 40 and len(item.split()) <= 4
    ]
    skills = _normalize_strings(derived)
    if skills:
        normalization_notes.append("Derived skills from short work summary tokens because explicit skill fields were absent.")
    return skills[:12]


def _extract_industry_tags(candidate: ResumeCandidate) -> list[str]:
    raw_values: list[Any] = []
    for key in ("industryTags", "expectedIndustryIds"):
        value = candidate.raw.get(key)
        if isinstance(value, list):
            raw_values.extend(value)
    raw_values.append(candidate.expected_industry)
    return _normalize_strings(raw_values)[:8]


def _extract_language_tags(raw: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("languageTags", "languages"):
        value = raw.get(key)
        if isinstance(value, list):
            values.extend(value)
    return _normalize_strings(values)[:8]


def _extract_education_summary(
    candidate: ResumeCandidate,
    *,
    normalization_notes: list[str],
) -> str:
    if candidate.education_summaries:
        trimmed = unique_strings(candidate.education_summaries)[:2]
        if len(candidate.education_summaries) > 2:
            normalization_notes.append("Trimmed education history to the top 2 entries.")
        return " ; ".join(trimmed)
    raw_items = candidate.raw.get("educationList")
    if isinstance(raw_items, list):
        summary = []
        for item in raw_items[:2]:
            if not isinstance(item, dict):
                continue
            summary.append(
                " ".join(
                    part
                    for part in [
                        _first_text(item.get("school")),
                        _first_text(item.get("speciality")),
                        _first_text(item.get("degree")),
                    ]
                    if part
                )
            )
        return " ; ".join(_normalize_strings(summary))
    return ""


def _build_excerpt(
    candidate: ResumeCandidate,
    recent_experiences: list[NormalizedExperience],
    *,
    normalization_notes: list[str],
) -> str:
    raw_text = _first_text(
        candidate.raw.get("fullText"),
        candidate.raw.get("rawText"),
        candidate.raw.get("profile"),
        candidate.raw.get("summary"),
    )
    chunks = [
        raw_text,
        " ".join(candidate.work_summaries),
        " ".join(candidate.project_names[:4]),
        " ".join(item.summary for item in recent_experiences if item.summary),
        " ".join(candidate.education_summaries[:2]),
    ]
    excerpt = " ".join(chunk.strip() for chunk in chunks if isinstance(chunk, str) and chunk.strip())
    if len(excerpt) > EXCERPT_LIMIT:
        excerpt = excerpt[:EXCERPT_LIMIT].rstrip() + "..."
        normalization_notes.append(f"Truncated raw_text_excerpt to {EXCERPT_LIMIT} characters.")
    return excerpt


def _build_key_achievements(
    candidate: ResumeCandidate,
    recent_experiences: list[NormalizedExperience],
    *,
    normalization_notes: list[str],
) -> list[str]:
    items = unique_strings(
        [
            *candidate.project_names[:3],
            *(item.summary for item in recent_experiences if item.summary),
        ]
    )
    trimmed = items[:4]
    if len(items) > 4:
        normalization_notes.append("Trimmed key_achievements to 4 items.")
    return trimmed


def _completeness_score(
    *,
    candidate_name: str,
    current_title: str,
    current_company: str,
    years_of_experience: int | None,
    locations: list[str],
    education_summary: str,
    skills: list[str],
    recent_experiences: list[NormalizedExperience],
    raw_text_excerpt: str,
) -> tuple[int, list[str]]:
    score = 0
    missing_fields: list[str] = []
    weighted_checks = [
        ("candidate_name", bool(candidate_name), 8),
        ("current_title", bool(current_title), 14),
        ("current_company", bool(current_company), 12),
        ("years_of_experience", years_of_experience is not None, 12),
        ("locations", bool(locations), 10),
        ("education_summary", bool(education_summary), 10),
        ("skills", bool(skills), 10),
        ("recent_experiences", bool(recent_experiences), 16),
        ("raw_text_excerpt", len(raw_text_excerpt) >= 60, 8),
    ]
    for field_name, present, weight in weighted_checks:
        if present:
            score += weight
        else:
            missing_fields.append(field_name)
    return min(100, score), missing_fields


def normalize_resume(candidate: ResumeCandidate) -> NormalizedResume:
    raw = candidate.raw
    normalization_notes: list[str] = []
    if candidate.used_fallback_id:
        normalization_notes.append("CTS did not expose a stable resume id; a deterministic fallback fingerprint was used.")

    candidate_name = _extract_name(raw)
    current_title = _first_text(
        raw.get("current_title"),
        raw.get("currentTitle"),
        raw.get("title"),
        candidate.expected_job_category,
    )
    current_company = _first_text(
        raw.get("current_company"),
        raw.get("currentCompany"),
        raw.get("company"),
    )
    recent_experiences = _extract_recent_experiences(candidate, normalization_notes=normalization_notes)
    if not current_company and recent_experiences:
        current_company = _first_text(recent_experiences[0].company)
        if current_company:
            normalization_notes.append("Filled current_company from the first recent experience.")
    headline = _first_text(raw.get("headline"))
    if not headline and current_title:
        headline = current_title
        normalization_notes.append("Filled headline from current_title.")
    years_of_experience = candidate.work_year
    raw_locations = raw.get("locations")
    extra_locations = [item if isinstance(item, str) else None for item in raw_locations] if isinstance(raw_locations, list) else []
    locations = normalize_locations(
        [
            candidate.now_location,
            candidate.expected_location,
            *extra_locations,
            raw.get("nowLocation"),
            raw.get("expectedLocation"),
        ]
    )[:4]
    education_summary = _extract_education_summary(candidate, normalization_notes=normalization_notes)
    skills = _extract_skills(candidate, normalization_notes=normalization_notes)
    industry_tags = _extract_industry_tags(candidate)
    language_tags = _extract_language_tags(raw)
    key_achievements = _build_key_achievements(
        candidate,
        recent_experiences,
        normalization_notes=normalization_notes,
    )
    raw_text_excerpt = _build_excerpt(
        candidate,
        recent_experiences,
        normalization_notes=normalization_notes,
    )
    completeness_score, missing_fields = _completeness_score(
        candidate_name=candidate_name,
        current_title=current_title,
        current_company=current_company,
        years_of_experience=years_of_experience,
        locations=locations,
        education_summary=education_summary,
        skills=skills,
        recent_experiences=recent_experiences,
        raw_text_excerpt=raw_text_excerpt,
    )

    if candidate.used_fallback_id and not candidate.resume_id.startswith("fallback-"):
        reconstructed = stable_fallback_resume_id(
            {
                "candidate_name": candidate_name,
                "current_title": current_title,
                "current_company": current_company,
                "locations": locations,
                "recent_experiences": [item.model_dump(mode="json") for item in recent_experiences[:2]],
            }
        )
        normalization_notes.append(
            f"Reference fallback fingerprint basis confirmed via deterministic hash seed {reconstructed}."
        )

    if completeness_score < 60:
        normalization_notes.append("Raw resume content is incomplete; scoring should treat gaps as risk.")

    return NormalizedResume(
        resume_id=candidate.resume_id,
        dedup_key=candidate.dedup_key,
        used_fallback_id=candidate.used_fallback_id,
        candidate_name=candidate_name,
        headline=headline,
        current_title=current_title,
        current_company=current_company,
        years_of_experience=years_of_experience,
        locations=locations,
        education_summary=education_summary,
        skills=skills,
        industry_tags=industry_tags,
        language_tags=language_tags,
        recent_experiences=recent_experiences,
        key_achievements=key_achievements,
        raw_text_excerpt=raw_text_excerpt,
        completeness_score=completeness_score,
        missing_fields=missing_fields,
        normalization_notes=normalization_notes,
        source_round=candidate.source_round,
    )


__all__ = ["normalize_resume"]
