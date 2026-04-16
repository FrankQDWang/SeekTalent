from __future__ import annotations

from typing import Any

from seektalent.models import FinalCandidate, FinalResult, NormalizedExperience, NormalizedResume, ResumeCandidate
from seektalent_ui.models import (
    AgentShortlistCandidate,
    CandidateCard,
    CandidateDetailResponse,
    CandidateResumeView,
    ResumeAnalysis,
    ResumeEducationItem,
    ResumeProjection,
    ResumeWorkExperienceItem,
)


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _truncate(text: str, limit: int = 180) -> str:
    clean = " ".join(text.split()).strip()
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit].rstrip()}..."


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _fallback_title(candidate: ResumeCandidate, normalized: NormalizedResume) -> str:
    title = _first_text(normalized.current_title, normalized.headline)
    if title:
        return title
    if normalized.recent_experiences:
        return _first_text(normalized.recent_experiences[0].title)
    return _first_text(
        candidate.expected_job_category,
        candidate.raw.get("title"),
    )


def _fallback_company(candidate: ResumeCandidate, normalized: NormalizedResume) -> str:
    company = _first_text(normalized.current_company)
    if company:
        return company
    if normalized.recent_experiences:
        return _first_text(normalized.recent_experiences[0].company)
    raw_company = _first_text(candidate.raw.get("current_company"), candidate.raw.get("currentCompany"))
    if raw_company:
        return raw_company
    raw_work_experience = candidate.raw.get("workExperienceList")
    if isinstance(raw_work_experience, list):
        for item in raw_work_experience:
            if isinstance(item, dict):
                company = _first_text(item.get("company"))
                if company:
                    return company
    if candidate.work_experience_summaries:
        first_item = candidate.work_experience_summaries[0]
        parts = [part.strip() for part in first_item.split("|")]
        if parts:
            return parts[0]
    return ""


def _fallback_location(candidate: ResumeCandidate, normalized: NormalizedResume) -> str:
    if normalized.locations:
        return normalized.locations[0]
    return _first_text(candidate.now_location, candidate.expected_location)


def _fallback_name(candidate: ResumeCandidate, normalized: NormalizedResume, resume_id: str) -> str:
    name = _first_text(normalized.candidate_name)
    if name:
        return name
    name = _first_text(
        candidate.raw.get("candidate_name"),
        candidate.raw.get("candidateName"),
        candidate.raw.get("name"),
    )
    if name:
        return name
    return resume_id


def _build_candidate_summary(
    final_candidate: FinalCandidate,
    candidate: ResumeCandidate,
    normalized: NormalizedResume,
) -> str:
    summary = _first_text(final_candidate.match_summary)
    if summary:
        return summary
    fallback = _first_text(normalized.raw_text_excerpt, normalized.compact_summary())
    if fallback:
        return _truncate(fallback)
    fallback = _first_text(candidate.search_text, candidate.compact_summary())
    if fallback:
        return _truncate(fallback)
    return ""


def _education_from_string(raw_value: str) -> ResumeEducationItem:
    tokens = [part for part in raw_value.split() if part]
    if not tokens:
        return ResumeEducationItem(school="", degree="", major="")
    if len(tokens) == 1:
        return ResumeEducationItem(school=tokens[0], degree="", major="")
    if len(tokens) == 2:
        return ResumeEducationItem(school=tokens[0], degree=tokens[1], major="")
    return ResumeEducationItem(
        school=tokens[0],
        major=" ".join(tokens[1:-1]),
        degree=tokens[-1],
    )


def _map_education(candidate: ResumeCandidate, normalized: NormalizedResume) -> list[ResumeEducationItem]:
    raw_items = candidate.raw.get("educationList")
    if isinstance(raw_items, list):
        mapped: list[ResumeEducationItem] = []
        for item in raw_items:
            if isinstance(item, dict):
                mapped.append(
                    ResumeEducationItem(
                        school=_first_text(item.get("school")),
                        degree=_first_text(item.get("degree")),
                        major=_first_text(item.get("major"), item.get("speciality")),
                        startTime=_first_text(item.get("startTime")) or None,
                        endTime=_first_text(item.get("endTime")) or None,
                    )
                )
            elif isinstance(item, str):
                mapped.append(_education_from_string(item))
        compact = [item for item in mapped if item.school or item.degree or item.major]
        if compact:
            return compact
    if normalized.education_summary:
        return [
            _education_from_string(item)
            for item in normalized.education_summary.split(";")
            if item.strip()
        ]
    return []


def _map_work_experience_raw(candidate: ResumeCandidate) -> list[ResumeWorkExperienceItem]:
    raw_items = candidate.raw.get("workExperienceList")
    if not isinstance(raw_items, list):
        return []
    mapped: list[ResumeWorkExperienceItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        mapped.append(
            ResumeWorkExperienceItem(
                company=_first_text(item.get("company")),
                title=_first_text(item.get("title")),
                duration=_first_text(item.get("duration")) or None,
                startTime=_first_text(item.get("startTime")) or None,
                endTime=_first_text(item.get("endTime")) or None,
                summary=_first_text(item.get("summary")) or None,
            )
        )
    return [item for item in mapped if item.company or item.title or item.summary]


def _map_work_experience_normalized(items: list[NormalizedExperience]) -> list[ResumeWorkExperienceItem]:
    return [
        ResumeWorkExperienceItem(
            company=item.company,
            title=item.title,
            duration=item.duration or None,
            summary=item.summary or None,
        )
        for item in items
        if item.company or item.title or item.summary
    ]


def _build_shortlist_candidate(
    final_candidate: FinalCandidate,
    candidate: ResumeCandidate,
    normalized: NormalizedResume,
) -> AgentShortlistCandidate:
    candidate_id = final_candidate.resume_id
    return AgentShortlistCandidate(
        candidateId=candidate_id,
        externalIdentityId=candidate_id,
        name=_fallback_name(candidate, normalized, candidate_id),
        title=_fallback_title(candidate, normalized),
        company=_fallback_company(candidate, normalized),
        location=_fallback_location(candidate, normalized),
        summary=_build_candidate_summary(final_candidate, candidate, normalized),
        reason=final_candidate.why_selected,
        score=round(final_candidate.final_score / 100, 4),
        sourceRound=final_candidate.source_round,
    )


def _build_detail_response(
    shortlist_candidate: AgentShortlistCandidate,
    final_candidate: FinalCandidate,
    candidate: ResumeCandidate,
    normalized: NormalizedResume,
) -> CandidateDetailResponse:
    work_experience = _map_work_experience_raw(candidate)
    if not work_experience:
        work_experience = _map_work_experience_normalized(normalized.recent_experiences)

    work_year = candidate.work_year
    if work_year is None:
        work_year = normalized.years_of_experience

    current_location = candidate.now_location
    expected_location = candidate.expected_location
    if normalized.locations:
        current_location = current_location or normalized.locations[0]
        expected_location = expected_location or normalized.locations[0]

    work_summaries = candidate.work_summaries
    if not work_summaries:
        work_summaries = normalized.key_achievements

    project_names = candidate.project_names
    if not project_names:
        project_names = normalized.key_achievements

    return CandidateDetailResponse(
        candidate=CandidateCard(
            candidateId=shortlist_candidate.candidateId,
            externalIdentityId=shortlist_candidate.externalIdentityId,
            name=shortlist_candidate.name,
            title=shortlist_candidate.title,
            company=shortlist_candidate.company,
            location=shortlist_candidate.location,
            summary=shortlist_candidate.summary,
        ),
        resumeView=CandidateResumeView(
            projection=ResumeProjection(
                workYear=work_year,
                currentLocation=current_location,
                expectedLocation=expected_location,
                jobState=candidate.job_state,
                expectedSalary=candidate.expected_salary,
                age=candidate.age,
                education=_map_education(candidate, normalized),
                workExperience=work_experience,
                workSummaries=work_summaries,
                projectNames=project_names,
            ),
        ),
        aiAnalysis=ResumeAnalysis(
            status="completed",
            summary=final_candidate.why_selected,
            evidenceSpans=_dedupe_strings(final_candidate.matched_must_haves + final_candidate.matched_preferences),
            riskFlags=final_candidate.risk_flags,
        ),
    )


def build_ui_payloads(
    final_result: FinalResult,
    candidate_store: dict[str, ResumeCandidate],
    normalized_store: dict[str, NormalizedResume],
) -> tuple[list[AgentShortlistCandidate], dict[str, CandidateDetailResponse]]:
    shortlist: list[AgentShortlistCandidate] = []
    details: dict[str, CandidateDetailResponse] = {}
    for final_candidate in final_result.candidates:
        candidate = candidate_store[final_candidate.resume_id]
        normalized = normalized_store[final_candidate.resume_id]
        shortlist_candidate = _build_shortlist_candidate(final_candidate, candidate, normalized)
        shortlist.append(shortlist_candidate)
        details[final_candidate.resume_id] = _build_detail_response(
            shortlist_candidate,
            final_candidate,
            candidate,
            normalized,
        )
    return shortlist, details
