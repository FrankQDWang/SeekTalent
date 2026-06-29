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
    candidate: Mapping[str, object],
    normalized: Mapping[str, object],
    scorecard: Mapping[str, object],
    observed_at: str,
) -> RuntimeControlCandidateIdentity:
    display_name = (
        _safe_text(normalized.get("candidate_name"), max_length=160)
        or _safe_text(_mapping(candidate.get("raw")).get("candidate_name"), max_length=160)
        or f"Candidate {identity_id[-8:]}"
    )
    title = (
        _safe_text(normalized.get("current_title"), max_length=240)
        or _safe_text(candidate.get("expected_job_category"), max_length=240)
        or ""
    )
    company = _safe_text(normalized.get("current_company"), max_length=240) or ""
    location = (
        _safe_text(_first(_string_list(normalized.get("locations"))), max_length=160)
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
        payload=payload,
        payload_hash=payload_hash,
        updated_at=observed_at,
    )


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
            "profile": _safe_text(raw.get("profile"), max_length=600),
            "summary": _safe_text(raw.get("summary"), max_length=800),
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
    full_text = _safe_text(raw.get("fullText"), max_length=20_000) or _safe_text(
        raw.get("page_text"), max_length=20_000
    )
    parsed = _parse_liepin_full_text(full_text or "")
    skills = (
        _split_tags(raw.get("skills"))[:24]
        or _split_tags(raw.get("skillTags"))[:24]
        or _split_tags(raw.get("tags"))[:24]
        or _split_tags(raw.get("keywords"))[:24]
        or _split_tags(normalized.get("skills"))[:24]
        or _object_list(parsed.get("skills"))[:24]
    )
    return _compact_mapping(
        {
            "candidateName": _safe_text(raw.get("candidate_name"), max_length=120)
            or _safe_text(raw.get("candidateName"), max_length=120)
            or _safe_text(normalized.get("candidate_name"), max_length=120)
            or _object_text(parsed.get("candidateName")),
            "activeStatus": _safe_text(raw.get("activeStatus"), max_length=120)
            or _object_text(parsed.get("activeStatus"))
            or _safe_text(candidate.get("active_status"), max_length=120),
            "jobStatus": _safe_text(raw.get("jobStatus"), max_length=120)
            or _object_text(parsed.get("jobStatus"))
            or _safe_text(candidate.get("job_state"), max_length=120),
            "gender": _safe_text(raw.get("gender"), max_length=24)
            or _object_text(parsed.get("gender"))
            or _safe_text(candidate.get("gender"), max_length=24),
            "age": _int_or_none(raw.get("age")) or parsed.get("age") or _int_or_none(candidate.get("age")),
            "city": _safe_text(raw.get("city"), max_length=120)
            or _object_text(parsed.get("city"))
            or _safe_text(candidate.get("now_location"), max_length=120)
            or _safe_text(_first(_string_list(normalized.get("locations"))), max_length=120),
            "education": _safe_text(raw.get("education"), max_length=120)
            or _safe_text(raw.get("educationLevel"), max_length=120)
            or _object_text(parsed.get("education"))
            or _first(_string_list(candidate.get("education_summaries"))),
            "workYears": _int_or_none(raw.get("workYears"))
            or _int_or_none(raw.get("work_years"))
            or parsed.get("workYears")
            or _int_or_none(candidate.get("work_year")),
            "currentTitle": _safe_text(raw.get("currentTitle"), max_length=180)
            or _safe_text(raw.get("current_title"), max_length=180)
            or _safe_text(normalized.get("current_title"), max_length=180)
            or _object_text(parsed.get("currentTitle")),
            "currentCompany": _safe_text(raw.get("currentCompany"), max_length=180)
            or _safe_text(raw.get("current_company"), max_length=180)
            or _safe_text(normalized.get("current_company"), max_length=180)
            or _object_text(parsed.get("currentCompany")),
            "jobIntention": _wts_job_intention(raw, candidate, parsed.get("jobIntention")),
            "workExperience": _wts_timeline_items(
                raw.get("workExperienceList"),
                fallback=parsed.get("workExperience"),
            ),
            "projectExperience": _wts_timeline_items(
                raw.get("projectExperienceList"),
                fallback=parsed.get("projectExperience"),
            ),
            "educationExperience": _wts_timeline_items(
                raw.get("educationList"),
                fallback=parsed.get("educationExperience"),
            ),
            "skills": skills,
            "sourceUrl": _safe_text(raw.get("sourceUrl"), max_length=500),
        }
    )


def _parse_liepin_full_text(text: str) -> dict[str, object]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {}

    result: dict[str, object] = {}
    candidate_name = _candidate_name_from_first_line(lines[0])
    if candidate_name is not None:
        result["candidateName"] = candidate_name
    for line in lines[:8]:
        job_status = _job_status_from_line(line)
        if job_status is not None:
            result["jobStatus"] = job_status
        active_status = _active_status_from_line(line)
        if active_status is not None:
            result["activeStatus"] = active_status
        age = _extract_number_before(line, "岁")
        if age is not None:
            result["age"] = age
        work_years = _extract_number_after(line, "工作")
        if work_years is not None:
            result["workYears"] = work_years
        gender = _gender_from_line(line)
        if gender is not None:
            result["gender"] = gender
        education = _education_from_line(line)
        if education is not None:
            result["education"] = education
        city = _city_from_line(line)
        if city is not None:
            result["city"] = city
    for line in lines[:10]:
        current = _split_pair(line, separators=(" · ", "·"))
        if current is not None:
            title, company = current
            result["currentTitle"] = title
            result["currentCompany"] = company
            break

    result["jobIntention"] = _parse_labeled_section(lines, "求职意向")
    result["workExperience"] = _parse_timeline_section(lines, "工作经历")
    result["projectExperience"] = _parse_timeline_section(lines, "项目经历")
    result["educationExperience"] = _parse_timeline_section(lines, "教育经历")
    result["skills"] = _parse_skill_section(lines)
    return _compact_mapping(result)


def _candidate_name_from_first_line(line: str) -> str | None:
    if _is_section_header(line) or "://" in line or "页面导航" in line or "新手任务" in line:
        return None
    token = line.split()[0]
    if any(marker in token for marker in ("：", ":", "岁", "工作")):
        return None
    return _safe_text(token, max_length=120)


def _job_status_from_line(line: str) -> str | None:
    if "活跃" in line:
        return None
    markers = ("在职", "离职", "新机会", "暂不考虑", "已离职")
    if any(marker in line for marker in markers):
        return _safe_text(line, max_length=120)
    return None


def _active_status_from_line(line: str) -> str | None:
    match = re.search(r"(近\d+天内活跃|今日活跃|本周活跃|本月活跃|刚刚活跃|最近活跃)", line)
    if match is None:
        return None
    return match.group(1)


def _gender_from_line(line: str) -> str | None:
    match = re.search(r"(?:^|\s)(男|女)(?:\s|$)", line)
    if match is None:
        return None
    return match.group(1)


def _education_from_line(line: str) -> str | None:
    for education in ("博士", "硕士", "本科", "大专", "高中", "中专"):
        if education in line:
            return education
    return None


def _city_from_line(line: str) -> str | None:
    for city in ("北京", "上海", "杭州", "深圳", "广州", "成都", "南京", "苏州", "武汉", "西安", "重庆"):
        if city in line:
            return city
    return None


def _extract_number_before(line: str, marker: str) -> int | None:
    match = re.search(rf"(\d{{1,2}})\s*{re.escape(marker)}", line)
    if match is None:
        return None
    return _int_or_none(match.group(1))


def _extract_number_after(line: str, marker: str) -> int | None:
    match = re.search(rf"{re.escape(marker)}\s*(\d{{1,2}})\s*年", line)
    if match is None:
        return None
    return _int_or_none(match.group(1))


def _parse_labeled_section(lines: Sequence[str], header: str) -> dict[str, object]:
    section = _section_lines(lines, header)
    if not section:
        return {}
    values: dict[str, object] = {}
    label_keys = {
        "期望岗位": "expectedTitle",
        "期望行业": "expectedIndustry",
        "期望地点": "expectedCity",
        "期望薪资": "expectedSalary",
    }
    for line in section:
        label, value = _split_label_value(line)
        if label is None or value is None:
            continue
        key = label_keys.get(label)
        if key is not None:
            values[key] = value
    return _compact_mapping(values)


def _parse_timeline_section(lines: Sequence[str], header: str) -> list[dict[str, object]]:
    section = _section_lines(lines, header)
    if not section:
        return []
    items: list[dict[str, object]] = []
    index = 0
    while index < len(section):
        line = section[index]
        if not _is_timeline_line(line):
            index += 1
            continue
        duration = line
        index += 1
        if index >= len(section) or _is_timeline_line(section[index]):
            continue
        title_line = section[index]
        index += 1
        body: list[str] = []
        while index < len(section) and not _is_timeline_line(section[index]):
            body.append(section[index])
            index += 1
        item = _timeline_item_from_lines(header=header, duration=duration, title_line=title_line, body=body)
        if item:
            items.append(item)
    return items[:8]


def _timeline_item_from_lines(
    *,
    header: str,
    duration: str,
    title_line: str,
    body: Sequence[str],
) -> dict[str, object]:
    summary = _timeline_summary(body)
    if header == "工作经历":
        company, title = _split_pair(title_line, separators=("｜", "|", " · ", "·")) or (None, None)
        return _compact_mapping(
            {
                "company": company,
                "title": title,
                "duration": duration,
                "summary": summary,
            }
        )
    if header == "项目经历":
        name, role_text = _split_pair(title_line, separators=("｜", "|", " · ", "·")) or (title_line, None)
        role = None
        if role_text is not None:
            label, value = _split_label_value(role_text)
            role = value if label == "项目职务" and value != "-" else role_text
        return _compact_mapping(
            {
                "name": name,
                "role": role,
                "duration": duration,
                "summary": summary,
            }
        )
    if header == "教育经历":
        return _education_item_from_line(title_line, duration=duration)
    return {}


def _education_item_from_line(line: str, *, duration: str) -> dict[str, object]:
    parts = [part for part in re.split(r"\s+", line) if part]
    if not parts:
        return {}
    degree = next((part for part in reversed(parts) if _education_from_line(part) == part), None)
    school = parts[0]
    major_parts = parts[1:]
    if degree is not None and major_parts and major_parts[-1] == degree:
        major_parts = major_parts[:-1]
    return _compact_mapping(
        {
            "school": school,
            "major": " ".join(major_parts) if major_parts else None,
            "degree": degree,
            "duration": duration,
        }
    )


def _timeline_summary(lines: Sequence[str]) -> str | None:
    cleaned = []
    for line in lines:
        label, value = _split_label_value(line)
        cleaned.append(value if value is not None else line)
    return _safe_text(" ".join(cleaned), max_length=1000)


def _parse_skill_section(lines: Sequence[str]) -> list[str]:
    section = _section_lines(lines, "技能标签") or _section_lines(lines, "技能")
    if not section:
        return []
    return _split_tags(" ".join(section))[:24]


def _section_lines(lines: Sequence[str], header: str) -> list[str]:
    try:
        start = list(lines).index(header) + 1
    except ValueError:
        return []
    result: list[str] = []
    for line in lines[start:]:
        if _is_section_header(line):
            break
        result.append(line)
    return result


def _is_section_header(line: str) -> bool:
    return line in {
        "求职意向",
        "工作经历",
        "项目经历",
        "教育经历",
        "技能",
        "技能标签",
        "自我评价",
        "任职经历",
    }


def _is_timeline_line(line: str) -> bool:
    return re.match(r"^\d{4}[./]\d{1,2}\s*[-至~－]\s*(?:\d{4}[./]\d{1,2}|至今|今)", line) is not None


def _split_label_value(line: str) -> tuple[str | None, str | None]:
    for separator in ("：", ":"):
        if separator not in line:
            continue
        label, value = line.split(separator, 1)
        label = label.strip()
        value = value.strip()
        if label and value:
            return label, value
    return None, None


def _split_pair(line: str, *, separators: Sequence[str]) -> tuple[str, str] | None:
    for separator in separators:
        if separator not in line:
            continue
        left, right = line.split(separator, 1)
        left = left.strip()
        right = right.strip()
        if left and right:
            return left, right
    return None


def _wts_job_intention(
    raw: Mapping[str, object],
    candidate: Mapping[str, object],
    parsed: object,
) -> dict[str, object]:
    structured = _mapping(raw.get("jobIntention")) or _mapping(raw.get("job_intention"))
    parsed_mapping = _mapping(parsed)
    return _compact_mapping(
        {
            "expectedTitle": _safe_text(structured.get("expectedTitle"), max_length=180)
            or _safe_text(structured.get("expected_title"), max_length=180)
            or _safe_text(parsed_mapping.get("expectedTitle"), max_length=180)
            or _safe_text(candidate.get("expected_job_category"), max_length=180),
            "expectedIndustry": _safe_text(structured.get("expectedIndustry"), max_length=180)
            or _safe_text(structured.get("expected_industry"), max_length=180)
            or _safe_text(parsed_mapping.get("expectedIndustry"), max_length=180)
            or _safe_text(candidate.get("expected_industry"), max_length=180),
            "expectedCity": _safe_text(structured.get("expectedCity"), max_length=120)
            or _safe_text(structured.get("expected_city"), max_length=120)
            or _safe_text(parsed_mapping.get("expectedCity"), max_length=120)
            or _safe_text(candidate.get("expected_location"), max_length=120),
            "expectedSalary": _safe_text(structured.get("expectedSalary"), max_length=120)
            or _safe_text(structured.get("expected_salary"), max_length=120)
            or _safe_text(parsed_mapping.get("expectedSalary"), max_length=120)
            or _safe_text(candidate.get("expected_salary"), max_length=120),
        }
    )


def _wts_timeline_items(value: object, *, fallback: object) -> list[dict[str, object]]:
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
    if items:
        return items
    return [dict(item) for item in _mapping_list(fallback)[:8] if item]


def _split_tags(value: object) -> list[str]:
    if isinstance(value, str):
        return [item for item in re.split(r"[\s,，、/]+", value.strip()) if item]
    return _string_list(value)


def _object_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _object_list(value: object) -> list[str]:
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


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
