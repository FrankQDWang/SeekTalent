from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from seektalent.clients.cts_models import Candidate, CandidateSearchRequest, CandidateSearchResponse
from seektalent.config import AppSettings
from seektalent.evaluation import snapshot_sha256
from seektalent.locations import normalize_location, normalize_locations
from seektalent.mock_data import load_mock_resume_corpus
from seektalent.models import CTSQuery, ResumeCandidate, stable_fallback_resume_id

ALLOWED_NATIVE_FILTERS = {
    "company",
    "position",
    "school",
    "workContent",
    "location",
    "degree",
    "schoolType",
    "workExperienceRange",
    "gender",
    "age",
}


class CTSFetchResult(BaseModel):
    request_payload: dict[str, Any]
    candidates: list[ResumeCandidate]
    raw_candidate_count: int = 0
    adapter_notes: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    response_message: str | None = None


class CTSClientProtocol(Protocol):
    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult: ...


class BaseCTSClient:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def build_request_payload(self, query: CTSQuery) -> tuple[dict[str, Any], list[str]]:
        payload: dict[str, Any] = {
            "keyword": query.keyword_query or None,
            "page": query.page,
            "pageSize": query.page_size,
        }
        notes = [
            "Dedup stays in runtime; CTS request does not receive seen ids.",
            "The project never forwards the full JD to CTS.",
            *query.adapter_notes,
        ]
        for field, value in query.native_filters.items():
            if field not in ALLOWED_NATIVE_FILTERS:
                raise ValueError(f"Unsupported native filter: {field}")
            if field != "location" and isinstance(value, list):
                raise ValueError(f"Native filter `{field}` must not be a list.")
            if field in {"degree", "schoolType", "workExperienceRange", "gender", "age"} and not isinstance(value, int):
                raise ValueError(f"Native filter `{field}` must be an integer code.")
            payload[field] = value
        request = CandidateSearchRequest.model_validate(payload)
        return request.model_dump(exclude_none=True), notes

    def _fallback_resume_seed(self, candidate: Candidate) -> dict[str, Any]:
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

    def _extract_resume_id(self, candidate: Candidate) -> tuple[str, bool]:
        extra = candidate.model_extra or {}
        for key in ("resume_id", "resumeId", "id", "candidate_id", "candidateId"):
            value = extra.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value), False
        return stable_fallback_resume_id(self._fallback_resume_seed(candidate)), True

    def _source_resume_id(self, candidate: Candidate) -> str | None:
        extra = candidate.model_extra or {}
        for key in ("resume_id", "resumeId", "id", "candidate_id", "candidateId"):
            value = extra.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
        return None

    def _normalize_candidate(self, candidate: Candidate, *, round_no: int) -> ResumeCandidate:
        education_summaries = [
            " ".join(part for part in [item.school, item.speciality, item.degree] if part)
            for item in candidate.educationList
        ]
        work_experience_summaries = [
            " | ".join(part for part in [item.company, item.title, item.summary] if part)
            for item in candidate.workExperienceList
        ]
        raw_payload = candidate.model_dump(mode="python", exclude_none=False)
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
        resume_id, used_fallback_id = self._extract_resume_id(candidate)
        return ResumeCandidate(
            resume_id=resume_id,
            source_resume_id=self._source_resume_id(candidate),
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


class CTSClient(BaseCTSClient):
    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult:
        self.settings.require_cts_credentials()
        payload, notes = self.build_request_payload(query)
        headers = {
            "trace_id": trace_id,
            "tenant_key": self.settings.cts_tenant_key or "",
            "tenant_secret": self.settings.cts_tenant_secret or "",
        }
        start = perf_counter()
        async with httpx.AsyncClient(
            base_url=self.settings.cts_base_url,
            timeout=self.settings.cts_timeout_seconds,
        ) as client:
            response = await client.post("/thirdCooperate/search/candidate/cts", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        parsed = CandidateSearchResponse.model_validate(body)
        candidates = []
        if parsed.data is not None:
            candidates = [self._normalize_candidate(item, round_no=round_no) for item in parsed.data.candidates]
        return CTSFetchResult(
            request_payload=payload,
            candidates=candidates,
            raw_candidate_count=len(candidates),
            adapter_notes=notes,
            latency_ms=int((perf_counter() - start) * 1000),
            response_message=parsed.message,
        )


class MockCTSClient(BaseCTSClient):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__(settings)
        self.corpus = load_mock_resume_corpus()

    def _candidate_field_text(self, candidate: ResumeCandidate, field: str) -> str:
        mapping = {
            "location": " ".join([candidate.now_location or "", candidate.expected_location or ""]),
            "position": candidate.expected_job_category or "",
            "company": " ".join(candidate.work_experience_summaries),
            "school": " ".join(candidate.education_summaries),
            "workContent": " ".join(candidate.work_summaries + candidate.work_experience_summaries),
        }
        return mapping.get(field, candidate.search_text)

    def _matches_filter(self, candidate: ResumeCandidate, field: str, value: str | int | list[str]) -> bool:
        if field == "location":
            candidate_locations = normalize_locations([candidate.now_location, candidate.expected_location])
            if isinstance(value, list):
                return any(normalize_location(str(item)) in candidate_locations for item in value)
            return normalize_location(str(value)) in candidate_locations
        haystack = self._candidate_field_text(candidate, field).casefold()
        if isinstance(value, str) and "|" in value:
            parts = [part.strip() for part in value.split("|") if part.strip()]
            return any(part.casefold() in haystack for part in parts)
        if isinstance(value, list):
            return any(str(item).casefold() in haystack for item in value)
        return str(value).casefold() in haystack

    def _retrieval_score(self, candidate: ResumeCandidate, query: CTSQuery) -> int:
        text = candidate.search_text.casefold()
        score = 0
        for keyword in query.query_terms:
            if keyword.casefold() in text:
                score += 6
        for field, value in query.native_filters.items():
            if not self._matches_filter(candidate, field, value):
                return -999
            score += 4
        return score

    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult:
        del trace_id
        payload, notes = self.build_request_payload(query)
        scored = [
            (self._retrieval_score(candidate, query), index, candidate)
            for index, candidate in enumerate(self.corpus)
        ]
        scored = [item for item in scored if item[0] > -999]
        scored.sort(key=lambda item: (-item[0], item[1], item[2].resume_id))
        page = max(query.page, 1)
        start = (page - 1) * query.page_size
        end = start + query.page_size
        selected = [
            candidate.model_copy(update={"source_round": round_no})
            for _, _, candidate in scored[start:end]
        ]
        return CTSFetchResult(
            request_payload=payload,
            candidates=selected,
            raw_candidate_count=len(selected),
            adapter_notes=notes,
            latency_ms=1,
            response_message="mock search completed",
        )
