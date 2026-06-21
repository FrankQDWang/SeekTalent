from __future__ import annotations

from pydantic import ValidationError

from seektalent.clients.cts_models import Candidate, CandidateSearchResponse
from seektalent.evaluation import snapshot_sha256
from seektalent.locations import normalize_location
from seektalent.models import ResumeCandidate, stable_fallback_resume_id

_CTS_SUCCESS_STATUSES = {"ok", "success", "succeeded", "0"}


class CTSResponseError(RuntimeError):
    def __init__(self, *, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.safe_message = message


def parse_cts_search_response_body(body: object) -> CandidateSearchResponse:
    try:
        response = CandidateSearchResponse.model_validate(body)
    except ValidationError as exc:
        raise CTSResponseError(
            reason_code="cts_response_schema_invalid",
            message="CTS search response did not match the expected schema.",
        ) from exc
    validate_cts_search_response_success(response)
    return response


def validate_cts_search_response_success(response: CandidateSearchResponse) -> None:
    status = response.status.strip().casefold()
    if response.code == 0 and status in _CTS_SUCCESS_STATUSES:
        return
    raise CTSResponseError(
        reason_code=_cts_business_error_reason_code(response),
        message=f"CTS search returned business error code={response.code} status={response.status!r}.",
    )


def _cts_business_error_reason_code(response: CandidateSearchResponse) -> str:
    status = response.status.strip().casefold()
    message = response.message.strip().casefold()
    combined = f"{status} {message}"
    if any(token in combined for token in ("auth", "unauthor", "forbid", "permission", "tenant", "credential", "密钥", "权限")):
        return "cts_auth_failed"
    if any(token in combined for token in ("rate", "limit", "quota", "throttle", "限流", "频率")):
        return "cts_rate_limited"
    if response.code != 0:
        return "cts_business_error"
    return "cts_status_error"


def normalize_cts_response_candidates(response: CandidateSearchResponse, *, round_no: int) -> list[ResumeCandidate]:
    if response.data is None:
        return []
    return [normalize_cts_candidate(item, round_no=round_no) for item in response.data.candidates]


def normalize_cts_candidate(candidate: Candidate, *, round_no: int) -> ResumeCandidate:
    education_summaries = [
        " ".join(part for part in [item.school, item.speciality, item.degree] if part)
        for item in candidate.educationList
    ]
    work_experience_summaries = [
        " | ".join(part for part in [item.company, item.title, item.summary] if part)
        for item in candidate.workExperienceList
    ]
    raw_payload = candidate.model_dump(mode="python", exclude_none=False)
    raw_payload["provider"] = "cts"
    raw_payload["source"] = "cts"
    search_text = " ".join(
        [
            candidate.expectedJobCategory or "",
            candidate.expectedIndustry or "",
            candidate.expectedLocation or "",
            candidate.nowLocation or "",
            *candidate.projectNameAll,
            *candidate.workSummariesAll,
            *education_summaries,
            *work_experience_summaries,
        ]
    )
    resume_id, used_fallback_id = extract_resume_id(candidate)
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=source_resume_id(candidate),
        snapshot_sha256=snapshot_sha256(raw_payload),
        dedup_key=resume_id,
        used_fallback_id=used_fallback_id,
        source_round=round_no,
        age=candidate.age,
        gender=candidate.gender,
        now_location=normalize_location(candidate.nowLocation),
        work_year=candidate.workYear,
        expected_location=normalize_location(candidate.expectedLocation),
        expected_job_category=candidate.expectedJobCategory,
        expected_industry=candidate.expectedIndustry,
        expected_salary=candidate.expectedSalary,
        active_status=candidate.activeStatus,
        job_state=candidate.jobState,
        education_summaries=education_summaries,
        work_experience_summaries=work_experience_summaries,
        project_names=candidate.projectNameAll,
        work_summaries=candidate.workSummariesAll,
        search_text=search_text,
        raw=raw_payload,
    )


def extract_resume_id(candidate: Candidate) -> tuple[str, bool]:
    source_id = source_resume_id(candidate)
    if source_id is not None:
        return source_id, False
    return stable_fallback_resume_id(_fallback_resume_seed(candidate)), True


def source_resume_id(candidate: Candidate) -> str | None:
    extra = candidate.model_extra or {}
    for key in ("resume_id", "resumeId", "id", "candidate_id", "candidateId"):
        value = extra.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    return None


def _fallback_resume_seed(candidate: Candidate) -> dict[str, object]:
    extra = candidate.model_extra or {}
    recent_experiences = [
        {
            "company": item.company,
            "title": item.title,
            "summary": item.summary,
        }
        for item in candidate.workExperienceList[:2]
    ]
    return {
        "candidate_name": extra.get("candidateName") or extra.get("candidate_name") or "",
        "current_title": candidate.expectedJobCategory,
        "current_company": candidate.workExperienceList[0].company if candidate.workExperienceList else None,
        "locations": [item for item in [candidate.nowLocation, candidate.expectedLocation] if item],
        "recent_experiences": recent_experiences,
    }
