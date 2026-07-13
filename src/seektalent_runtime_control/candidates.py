from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import re

from seektalent_runtime_control.models import (
    RuntimeControlCandidateEvidence,
    RuntimeControlCandidateFinalizationRevision,
    RuntimeControlCandidateIdentity,
)
from seektalent.source_references import SourceReference


@dataclass(frozen=True)
class RuntimeControlCandidateTruth:
    identities: list[RuntimeControlCandidateIdentity]
    evidence: list[RuntimeControlCandidateEvidence]
    finalization_revisions: list[RuntimeControlCandidateFinalizationRevision]


def candidate_truth_from_run_state(
    *,
    runtime_run_id: str,
    run_state: Mapping[str, object],
    source_checkpoint_id: str | None,
    observed_at: str,
) -> RuntimeControlCandidateTruth:
    identities_payload = _mapping(run_state.get("candidate_identities"))
    identity_by_resume_id = _string_mapping(run_state.get("candidate_identity_by_resume_id"))
    canonical_by_identity = _mapping(run_state.get("canonical_resume_by_identity_id"))
    source_evidence_by_identity = _mapping(run_state.get("source_evidence_by_identity_id"))
    candidate_store = _mapping(run_state.get("candidate_store"))
    normalized_store = _mapping(run_state.get("normalized_store"))
    scorecards_by_resume_id = _mapping(run_state.get("scorecards_by_resume_id"))

    identities: list[RuntimeControlCandidateIdentity] = []
    evidence: list[RuntimeControlCandidateEvidence] = []
    seen_evidence_ids: set[str] = set()
    for identity_id in sorted(identities_payload):
        identity_payload = _mapping(identities_payload.get(identity_id))
        canonical_resume_id = _canonical_resume_id(
            identity_id=identity_id,
            identity_payload=identity_payload,
            canonical_by_identity=canonical_by_identity,
            identity_by_resume_id=identity_by_resume_id,
        )
        if canonical_resume_id is None:
            continue
        merged_resume_ids = _string_list(identity_payload.get("resume_ids")) or [canonical_resume_id]
        canonical_selection = _mapping(canonical_by_identity.get(identity_id))
        identity_evidence_payloads = _mapping_list(source_evidence_by_identity.get(identity_id))
        source_evidence_ids = [
            evidence_id
            for payload in identity_evidence_payloads
            if (evidence_id := _text(payload.get("evidence_id")))
        ]
        for evidence_payload in identity_evidence_payloads:
            evidence_id = _text(evidence_payload.get("evidence_id"))
            if evidence_id is None or evidence_id in seen_evidence_ids:
                continue
            seen_evidence_ids.add(evidence_id)
            resume_id = _text(evidence_payload.get("candidate_resume_id")) or canonical_resume_id
            scorecard = _mapping(scorecards_by_resume_id.get(resume_id))
            evidence.append(
                _candidate_evidence(
                    runtime_run_id=runtime_run_id,
                    identity_id=identity_id,
                    resume_id=resume_id,
                    evidence_payload=evidence_payload,
                    candidate=_mapping(candidate_store.get(resume_id)),
                    normalized=_mapping(normalized_store.get(resume_id)),
                    scorecard=scorecard,
                    observed_at=observed_at,
                )
            )
        identities.append(
            _candidate_identity(
                runtime_run_id=runtime_run_id,
                identity_id=identity_id,
                canonical_resume_id=canonical_resume_id,
                merged_resume_ids=merged_resume_ids,
                source_evidence_ids=source_evidence_ids,
                equivalent_latest_resume_ids=_string_list(
                    canonical_selection.get("equivalent_latest_resume_ids")
                ),
                display_source_evidence_ids=_string_list(
                    canonical_selection.get("display_source_evidence_ids")
                ),
                conflicting_resume_ids=_string_list(canonical_selection.get("conflicting_resume_ids")),
                incomparable_resume_ids=_string_list(canonical_selection.get("incomparable_resume_ids")),
                content_version_key=_safe_text(
                    canonical_selection.get("content_version_key"), max_length=256
                )
                or "",
                safe_reason_codes=_string_list(canonical_selection.get("safe_reason_codes")),
                candidate=_mapping(candidate_store.get(canonical_resume_id)),
                normalized=_mapping(normalized_store.get(canonical_resume_id)),
                scorecard=_mapping(scorecards_by_resume_id.get(canonical_resume_id)),
                observed_at=observed_at,
            )
        )
    revisions = [
        _finalization_revision(
            runtime_run_id=runtime_run_id,
            revision_payload=revision_payload,
            source_checkpoint_id=source_checkpoint_id,
            observed_at=observed_at,
        )
        for revision_payload in _mapping_list(run_state.get("finalization_revisions"))
        if _int_or_none(revision_payload.get("revision")) is not None
    ]
    return RuntimeControlCandidateTruth(
        identities=identities,
        evidence=evidence,
        finalization_revisions=revisions,
    )


def _candidate_identity(
    *,
    runtime_run_id: str,
    identity_id: str,
    canonical_resume_id: str,
    merged_resume_ids: list[str],
    source_evidence_ids: list[str],
    equivalent_latest_resume_ids: list[str],
    display_source_evidence_ids: list[str],
    conflicting_resume_ids: list[str],
    incomparable_resume_ids: list[str],
    content_version_key: str,
    safe_reason_codes: list[str],
    candidate: Mapping[str, object],
    normalized: Mapping[str, object],
    scorecard: Mapping[str, object],
    observed_at: str,
) -> RuntimeControlCandidateIdentity:
    raw = _mapping(candidate.get("raw"))
    wts_detail = _wts_detail_payload(raw, candidate, normalized)
    display_name = (
        _safe_text(wts_detail.get("candidateName"), max_length=160)
        or _safe_text(raw.get("candidate_name"), max_length=160)
        or _safe_text(normalized.get("candidate_name"), max_length=160)
        or f"Candidate {identity_id[-8:]}"
    )
    title = (
        _safe_text(wts_detail.get("currentTitle"), max_length=240)
        or _safe_text(normalized.get("current_title"), max_length=240)
        or _safe_text(candidate.get("expected_job_category"), max_length=240)
        or ""
    )
    company = (
        _safe_text(wts_detail.get("currentCompany"), max_length=240)
        or _safe_text(normalized.get("current_company"), max_length=240)
        or ""
    )
    location = (
        _safe_text(wts_detail.get("city"), max_length=160)
        or _safe_text(_first(_string_list(normalized.get("locations"))), max_length=160)
        or _safe_text(candidate.get("now_location"), max_length=160)
        or ""
    )
    summary = (
        _safe_text(scorecard.get("reasoning_summary"), max_length=1000)
        or _safe_text(normalized.get("headline"), max_length=1000)
        or _safe_text(candidate.get("search_text"), max_length=1000)
        or ""
    )
    score = _int_or_none(scorecard.get("overall_score"))
    fit_bucket = _safe_text(scorecard.get("fit_bucket"), max_length=64)
    source_round = _int_or_none(scorecard.get("source_round")) or _int_or_none(candidate.get("source_round"))
    payload_hash = _hash_payload(
        {
            "identity_id": identity_id,
            "canonical_resume_id": canonical_resume_id,
            "merged_resume_ids": merged_resume_ids,
            "source_evidence_ids": source_evidence_ids,
            "equivalent_latest_resume_ids": equivalent_latest_resume_ids,
            "display_source_evidence_ids": display_source_evidence_ids,
            "conflicting_resume_ids": conflicting_resume_ids,
            "incomparable_resume_ids": incomparable_resume_ids,
            "content_version_key": content_version_key,
            "safe_reason_codes": safe_reason_codes,
            "display_name": display_name,
            "title": title,
            "company": company,
            "location": location,
            "summary": summary,
            "score": score,
            "fit_bucket": fit_bucket,
            "source_round": source_round,
        }
    )
    return RuntimeControlCandidateIdentity(
        runtime_run_id=runtime_run_id,
        identity_id=identity_id,
        canonical_resume_id=canonical_resume_id,
        merged_resume_ids=merged_resume_ids,
        source_evidence_ids=source_evidence_ids,
        equivalent_latest_resume_ids=equivalent_latest_resume_ids,
        display_source_evidence_ids=display_source_evidence_ids,
        conflicting_resume_ids=conflicting_resume_ids,
        incomparable_resume_ids=incomparable_resume_ids,
        content_version_key=content_version_key,
        safe_reason_codes=safe_reason_codes,
        display_name=display_name,
        title=title,
        company=company,
        location=location,
        summary=summary,
        score=score,
        fit_bucket=fit_bucket,
        source_round=source_round,
        payload_hash=payload_hash,
        updated_at=observed_at,
    )


def _candidate_evidence(
    *,
    runtime_run_id: str,
    identity_id: str,
    resume_id: str,
    evidence_payload: Mapping[str, object],
    candidate: Mapping[str, object],
    normalized: Mapping[str, object],
    scorecard: Mapping[str, object],
    observed_at: str,
) -> RuntimeControlCandidateEvidence:
    raw = _mapping(candidate.get("raw"))
    payload: dict[str, object] = {
        "providerRank": _int_or_none(evidence_payload.get("provider_rank")),
        "queryFingerprint": _safe_text(evidence_payload.get("query_fingerprint"), max_length=256),
        "reasonCode": _safe_text(evidence_payload.get("reason_code"), max_length=128),
        "safeReasonCodes": _string_list(evidence_payload.get("safe_reason_codes")),
        "candidateProfile": _candidate_profile_payload(candidate),
        "normalizedProfile": _normalized_profile_payload(normalized),
        "safeSummary": _safe_summary_payload(raw),
        "safeDetail": _safe_detail_payload(raw),
        "match": _match_payload(scorecard),
        "wtsDetail": _wts_detail_payload(raw, candidate, normalized),
    }
    payload_hash = _hash_payload(payload)
    return RuntimeControlCandidateEvidence(
        runtime_run_id=runtime_run_id,
        evidence_id=_text(evidence_payload.get("evidence_id")) or f"{identity_id}:{resume_id}",
        identity_id=identity_id,
        resume_id=resume_id,
        source_kind=_safe_text(evidence_payload.get("source"), max_length=32) or "unknown",
        evidence_level=_safe_text(evidence_payload.get("evidence_level"), max_length=64) or "unknown",
        provider_candidate_key_hash=_safe_text(evidence_payload.get("provider_candidate_key_hash"), max_length=256)
        or "",
        score=_int_or_none(scorecard.get("overall_score")),
        fit_bucket=_safe_text(scorecard.get("fit_bucket"), max_length=64),
        source_references=_source_references(evidence_payload.get("source_references")),
        payload=payload,
        payload_hash=payload_hash,
        updated_at=observed_at,
    )


def _source_references(value: object) -> list[SourceReference]:
    references: list[SourceReference] = []
    for item in _mapping_list(value):
        source_kind = _safe_text(item.get("source_kind"), max_length=64)
        display_label = _safe_text(item.get("display_label"), max_length=120)
        url = _safe_text(item.get("url"), max_length=2048)
        if source_kind is None or display_label is None or url is None:
            continue
        references.append(
            SourceReference(
                source_kind=source_kind,
                display_label=display_label,
                url=url,
            )
        )
    return references


def _candidate_profile_payload(candidate: Mapping[str, object]) -> dict[str, object]:
    return _compact_mapping(
        {
            "age": _int_or_none(candidate.get("age")),
            "gender": _safe_text(candidate.get("gender"), max_length=24),
            "nowLocation": _safe_text(candidate.get("now_location"), max_length=120),
            "workYear": _int_or_none(candidate.get("work_year")),
            "expectedLocation": _safe_text(candidate.get("expected_location"), max_length=160),
            "expectedJobCategory": _safe_text(candidate.get("expected_job_category"), max_length=180),
            "expectedIndustry": _safe_text(candidate.get("expected_industry"), max_length=180),
            "expectedSalary": _safe_text(candidate.get("expected_salary"), max_length=120),
            "activeStatus": _safe_text(candidate.get("active_status"), max_length=120),
            "jobState": _safe_text(candidate.get("job_state"), max_length=120),
            "educationSummaries": _string_list(candidate.get("education_summaries"))[:4],
            "workExperienceSummaries": _string_list(candidate.get("work_experience_summaries"))[:5],
            "projectNames": _string_list(candidate.get("project_names"))[:5],
            "workSummaries": _string_list(candidate.get("work_summaries"))[:5],
        }
    )


def _normalized_profile_payload(normalized: Mapping[str, object]) -> dict[str, object]:
    recent_experiences = []
    for item in _mapping_list(normalized.get("recent_experiences"))[:5]:
        recent = _compact_mapping(
            {
                "title": _safe_text(item.get("title"), max_length=160),
                "company": _safe_text(item.get("company"), max_length=160),
                "duration": _safe_text(item.get("duration"), max_length=120),
                "summary": _safe_text(item.get("summary"), max_length=600),
            }
        )
        if recent:
            recent_experiences.append(recent)
    return _compact_mapping(
        {
            "candidateName": _safe_text(normalized.get("candidate_name"), max_length=120),
            "headline": _safe_text(normalized.get("headline"), max_length=240),
            "currentTitle": _safe_text(normalized.get("current_title"), max_length=180),
            "currentCompany": _safe_text(normalized.get("current_company"), max_length=180),
            "yearsOfExperience": _int_or_none(normalized.get("years_of_experience")),
            "locations": _string_list(normalized.get("locations"))[:4],
            "educationSummary": _safe_text(normalized.get("education_summary"), max_length=260),
            "skills": _string_list(normalized.get("skills"))[:16],
            "industryTags": _string_list(normalized.get("industry_tags"))[:8],
            "languageTags": _string_list(normalized.get("language_tags"))[:8],
            "recentExperiences": recent_experiences,
            "keyAchievements": _string_list(normalized.get("key_achievements"))[:6],
            "rawTextExcerpt": _safe_text(normalized.get("raw_text_excerpt"), max_length=1200),
        }
    )


def _safe_summary_payload(raw: Mapping[str, object]) -> dict[str, object]:
    summary = _mapping(raw.get("safe_card_summary"))
    return _compact_mapping(
        {
            "displayTitle": _safe_text(summary.get("display_title"), max_length=180),
            "currentOrRecentCompany": _safe_text(summary.get("current_or_recent_company"), max_length=180),
            "currentOrRecentTitle": _safe_text(summary.get("current_or_recent_title"), max_length=180),
            "workYears": _int_or_none(summary.get("work_years")),
            "age": _int_or_none(summary.get("age")),
            "city": _safe_text(summary.get("city"), max_length=120),
            "expectedCity": _safe_text(summary.get("expected_city"), max_length=120),
            "educationLevel": _safe_text(summary.get("education_level"), max_length=120),
            "schoolNames": _string_list(summary.get("school_names"))[:4],
            "majorNames": _string_list(summary.get("major_names"))[:4],
            "skillTags": _string_list(summary.get("skill_tags"))[:16],
            "jobIntention": _safe_text(summary.get("job_intention"), max_length=260),
            "recentExperienceText": _safe_text(summary.get("recent_experience_text"), max_length=800),
            "maskedName": bool(summary.get("masked_name")) if "masked_name" in summary else None,
        }
    )


def _safe_detail_payload(raw: Mapping[str, object]) -> dict[str, object]:
    work_items = [
        _safe_detail_item(item, allowed_keys=_WORK_EXPERIENCE_DETAIL_KEYS)
        for item in _mapping_list(raw.get("workExperienceList"))[:5]
    ]
    education_items = [
        _safe_detail_item(item, allowed_keys=_EDUCATION_DETAIL_KEYS)
        for item in _mapping_list(raw.get("educationList"))[:4]
    ]
    return _compact_mapping(
        {
            "candidateName": _safe_text(raw.get("candidate_name"), max_length=120),
            "currentTitle": _safe_text(raw.get("currentTitle"), max_length=180),
            "currentCompany": _safe_text(raw.get("currentCompany"), max_length=180),
            "workExperienceList": [item for item in work_items if item],
            "educationList": [item for item in education_items if item],
            "skills": _string_list(raw.get("skills"))[:16],
            "skillTags": _string_list(raw.get("skillTags"))[:16],
            "tags": _string_list(raw.get("tags"))[:16],
            "keywords": _string_list(raw.get("keywords"))[:16],
            "locations": _string_list(raw.get("locations"))[:4],
        }
    )


def _match_payload(scorecard: Mapping[str, object]) -> dict[str, object]:
    return _compact_mapping(
        {
            "score": _int_or_none(scorecard.get("overall_score")),
            "fitBucket": _safe_text(scorecard.get("fit_bucket"), max_length=64),
            "reasoningSummary": _safe_text(scorecard.get("reasoning_summary"), max_length=1600),
            "strengths": _string_list(scorecard.get("strengths"))[:8],
            "weaknesses": _string_list(scorecard.get("weaknesses"))[:8],
            "sourceRound": _int_or_none(scorecard.get("source_round")),
        }
    )


def _wts_detail_payload(
    raw: Mapping[str, object],
    candidate: Mapping[str, object],
    normalized: Mapping[str, object],
) -> dict[str, object]:
    del candidate, normalized
    skills = (
        _split_tags(raw.get("skills"))[:24]
        or _split_tags(raw.get("skillTags"))[:24]
        or _split_tags(raw.get("tags"))[:24]
        or _split_tags(raw.get("keywords"))[:24]
    )
    return _compact_mapping(
        {
            "candidateName": _safe_text(raw.get("candidate_name"), max_length=120)
            or _safe_text(raw.get("candidateName"), max_length=120),
            "activeStatus": _safe_text(raw.get("activeStatus"), max_length=120),
            "jobStatus": _safe_text(raw.get("jobStatus"), max_length=120),
            "gender": _safe_text(raw.get("gender"), max_length=24),
            "age": _int_or_none(raw.get("age")),
            "city": _safe_text(raw.get("city"), max_length=120)
            or _safe_text(_first(_string_list(raw.get("locations"))), max_length=120),
            "education": _safe_text(raw.get("education"), max_length=120)
            or _safe_text(raw.get("educationLevel"), max_length=120),
            "workYears": _int_or_none(raw.get("workYears"))
            or _int_or_none(raw.get("work_years")),
            "currentTitle": _safe_text(raw.get("currentTitle"), max_length=180)
            or _safe_text(raw.get("current_title"), max_length=180),
            "currentCompany": _safe_text(raw.get("currentCompany"), max_length=180)
            or _safe_text(raw.get("current_company"), max_length=180),
            "jobIntention": _wts_job_intention(raw),
            "workExperience": _wts_timeline_items(raw.get("workExperienceList")),
            "projectExperience": _wts_timeline_items(raw.get("projectExperienceList")),
            "educationExperience": _wts_timeline_items(raw.get("educationList")),
            "skills": skills,
            "sourceUrl": _safe_text(raw.get("sourceUrl"), max_length=500),
        }
    )


def _wts_job_intention(raw: Mapping[str, object]) -> dict[str, object]:
    structured = _mapping(raw.get("jobIntention")) or _mapping(raw.get("job_intention"))
    return _compact_mapping(
        {
            "expectedRole": _safe_text(structured.get("expectedRole"), max_length=180)
            or _safe_text(structured.get("expectedTitle"), max_length=180)
            or _safe_text(structured.get("expected_role"), max_length=180)
            or _safe_text(structured.get("expected_title"), max_length=180),
            "expectedIndustry": _safe_text(structured.get("expectedIndustry"), max_length=180)
            or _safe_text(structured.get("expected_industry"), max_length=180),
            "expectedCity": _safe_text(structured.get("expectedCity"), max_length=120)
            or _safe_text(structured.get("expected_city"), max_length=120),
            "expectedSalary": _safe_text(structured.get("expectedSalary"), max_length=120)
            or _safe_text(structured.get("expected_salary"), max_length=120),
        }
    )


def _wts_timeline_items(value: object) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for item in _mapping_list(value)[:8]:
        timeline_item = _compact_mapping(
            {
                "company": _safe_text(item.get("company"), max_length=180)
                or _safe_text(item.get("companyName"), max_length=180),
                "title": _safe_text(item.get("title"), max_length=180)
                or _safe_text(item.get("position"), max_length=180)
                or _safe_text(item.get("positionName"), max_length=180),
                "name": _safe_text(item.get("name"), max_length=180)
                or _safe_text(item.get("projectName"), max_length=180),
                "school": _safe_text(item.get("school"), max_length=180)
                or _safe_text(item.get("schoolName"), max_length=180),
                "major": _safe_text(item.get("major"), max_length=180)
                or _safe_text(item.get("majorName"), max_length=180),
                "degree": _safe_text(item.get("degree"), max_length=120)
                or _safe_text(item.get("education"), max_length=120),
                "duration": _safe_text(item.get("duration"), max_length=160)
                or _safe_text(item.get("time"), max_length=160)
                or _safe_text(item.get("dateRange"), max_length=160)
                or _safe_text(item.get("startEndTime"), max_length=160),
                "summary": _safe_text(item.get("summary"), max_length=1000)
                or _safe_text(item.get("description"), max_length=1000)
                or _safe_text(item.get("workContent"), max_length=1000)
                or _safe_text(item.get("content"), max_length=1000),
            }
        )
        if timeline_item:
            items.append(timeline_item)
    return items


def _split_tags(value: object) -> list[str]:
    if isinstance(value, str):
        return [item for item in re.split(r"[\s,，、/]+", value.strip()) if item]
    return _string_list(value)


_WORK_EXPERIENCE_DETAIL_KEYS = frozenset(
    {
        "company",
        "companyName",
        "org",
        "organization",
        "title",
        "position",
        "positionName",
        "jobTitle",
        "duration",
        "time",
        "dateRange",
        "startEndTime",
        "summary",
        "description",
        "workContent",
        "content",
    }
)

_EDUCATION_DETAIL_KEYS = frozenset(
    {
        "school",
        "schoolName",
        "college",
        "university",
        "major",
        "majorName",
        "speciality",
        "degree",
        "education",
        "educationLevel",
        "duration",
        "time",
        "dateRange",
        "startEndTime",
    }
)


def _safe_detail_item(item: Mapping[str, object], *, allowed_keys: frozenset[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in item.items():
        if key not in allowed_keys:
            continue
        if isinstance(value, str):
            text = _safe_text(value, max_length=600)
            if text is not None:
                result[str(key)] = text
        elif isinstance(value, int) and not isinstance(value, bool):
            result[str(key)] = value
    return result


def _compact_mapping(payload: Mapping[str, object | None]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, list) and len(value) == 0:
            continue
        if isinstance(value, dict) and len(value) == 0:
            continue
        result[key] = value
    return result


def _finalization_revision(
    *,
    runtime_run_id: str,
    revision_payload: Mapping[str, object],
    source_checkpoint_id: str | None,
    observed_at: str,
) -> RuntimeControlCandidateFinalizationRevision:
    coverage_summary = _mapping(revision_payload.get("coverage_summary"))
    payload = {
        "revision": _int_or_none(revision_payload.get("revision")) or 0,
        "reason_code": _safe_text(revision_payload.get("reason_code"), max_length=128) or "runtime_finalized",
        "candidate_identity_ids": _string_list(revision_payload.get("candidate_identity_ids")),
        "coverage_summary": dict(coverage_summary),
    }
    return RuntimeControlCandidateFinalizationRevision(
        runtime_run_id=runtime_run_id,
        revision=int(payload["revision"]),
        reason_code=str(payload["reason_code"]),
        candidate_identity_ids=list(payload["candidate_identity_ids"]),
        coverage_summary=dict(coverage_summary),
        source_checkpoint_id=source_checkpoint_id,
        payload_hash=_hash_payload(payload),
        created_at=_safe_text(revision_payload.get("created_at"), max_length=64) or observed_at,
    )


def _canonical_resume_id(
    *,
    identity_id: str,
    identity_payload: Mapping[str, object],
    canonical_by_identity: Mapping[str, object],
    identity_by_resume_id: Mapping[str, str],
) -> str | None:
    canonical_payload = _mapping(canonical_by_identity.get(identity_id))
    selected = _text(canonical_payload.get("canonical_resume_id"))
    if selected is not None:
        return selected
    for resume_id, mapped_identity_id in sorted(identity_by_resume_id.items()):
        if mapped_identity_id == identity_id:
            return resume_id
    resume_ids = _string_list(identity_payload.get("resume_ids"))
    return resume_ids[0] if resume_ids else None


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items() if isinstance(key, str)}
    return {}


def _string_mapping(value: object) -> dict[str, str]:
    return {
        key: item.strip()
        for key, item in _mapping(value).items()
        if isinstance(item, str) and item.strip()
    }


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _safe_text(value: object, *, max_length: int) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return text[:max_length]


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _first(values: Sequence[str]) -> str | None:
    return values[0] if values else None


def _hash_payload(payload: Mapping[str, object]) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return sha256(text.encode("utf-8")).hexdigest()
