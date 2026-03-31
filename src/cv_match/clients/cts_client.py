from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from cv_match.clients.cts_models import Candidate, CandidateSearchRequest, CandidateSearchResponse
from cv_match.config import AppSettings
from cv_match.mock_data import load_mock_resume_corpus
from cv_match.models import CTSQuery, FilterCondition, ResumeCandidate, stable_fallback_resume_id

SAFE_FIELD_MAPPING = {
    "company": "company",
    "position": "position",
    "school": "school",
    "work_content": "workContent",
    "location": "location",
}


class CTSFetchResult(BaseModel):
    request_payload: dict[str, Any]
    candidates: list[ResumeCandidate]
    total: int | None = None
    raw_candidate_count: int = 0
    adapter_notes: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    response_message: str | None = None


class CTSClientProtocol(Protocol):
    def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult: ...


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
            "CTS OpenAPI does not publish exclude_ids support; seen ids are filtered locally.",
            "The project never forwards the full JD to CTS.",
        ]
        for filter_item in query.hard_filters + query.soft_filters:
            target = SAFE_FIELD_MAPPING.get(filter_item.field)
            if target is None:
                notes.append(
                    f"Filter `{filter_item.field}` was not mapped because the validated OpenAPI does not publish a safe request field."
                )
                continue
            if target in payload and payload[target] not in (None, "", []):
                continue
            payload[target] = filter_item.value
        request = CandidateSearchRequest.model_validate(payload)
        return request.model_dump(exclude_none=True), notes

    def _extract_resume_id(self, candidate: Candidate) -> tuple[str, bool]:
        extra = candidate.model_extra or {}
        for key in ("resume_id", "resumeId", "id", "candidate_id", "candidateId"):
            value = extra.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value), False
        canonical = candidate.model_dump(mode="python", exclude_none=True)
        return stable_fallback_resume_id(canonical), True

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
                candidate.expectedJobCategory,
                candidate.expectedIndustry,
                candidate.expectedLocation,
                candidate.nowLocation,
                *candidate.projectNameAll,
                *candidate.workSummariesAll,
                *education_summaries,
                *work_experience_summaries,
            ]
        )
        resume_id, used_fallback_id = self._extract_resume_id(candidate)
        return ResumeCandidate(
            resume_id=resume_id,
            dedup_key=resume_id,
            used_fallback_id=used_fallback_id,
            source_round=round_no,
            age=candidate.age,
            gender=candidate.gender,
            now_location=candidate.nowLocation,
            work_year=candidate.workYear,
            expected_location=candidate.expectedLocation,
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
    def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult:
        self.settings.require_cts_credentials()
        payload, notes = self.build_request_payload(query)
        headers = {
            "trace_id": trace_id,
            "tenant_key": self.settings.cts_tenant_key or "",
            "tenant_secret": self.settings.cts_tenant_secret or "",
        }
        start = perf_counter()
        with httpx.Client(base_url=self.settings.cts_base_url, timeout=self.settings.cts_timeout_seconds) as client:
            response = client.post("/thirdCooperate/search/candidate/cts", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        parsed = CandidateSearchResponse.model_validate(body)
        candidates = []
        if parsed.data is not None:
            candidates = [self._normalize_candidate(item, round_no=round_no) for item in parsed.data.candidates]
        return CTSFetchResult(
            request_payload=payload,
            candidates=candidates,
            total=parsed.data.total if parsed.data is not None else None,
            raw_candidate_count=len(candidates),
            adapter_notes=notes,
            latency_ms=int((perf_counter() - start) * 1000),
            response_message=parsed.message,
        )


class MockCTSClient(BaseCTSClient):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__(settings)
        self.corpus = load_mock_resume_corpus()

    def _candidate_field_text(self, candidate: ResumeCandidate, filter_item: FilterCondition) -> str:
        mapping = {
            "location": " ".join([candidate.now_location or "", candidate.expected_location or ""]),
            "position": candidate.expected_job_category or "",
            "company": " ".join(candidate.work_experience_summaries),
            "school": " ".join(candidate.education_summaries),
            "work_content": " ".join(candidate.work_summaries + candidate.work_experience_summaries),
        }
        return mapping.get(filter_item.field, candidate.search_text)

    def _matches_filter(self, candidate: ResumeCandidate, filter_item: FilterCondition) -> bool:
        haystack = self._candidate_field_text(candidate, filter_item).casefold()
        if isinstance(filter_item.value, list):
            return any(str(value).casefold() in haystack for value in filter_item.value)
        return str(filter_item.value).casefold() in haystack

    def _retrieval_score(self, candidate: ResumeCandidate, query: CTSQuery) -> int:
        text = candidate.search_text.casefold()
        score = 0
        for keyword in query.keywords:
            if keyword.casefold() in text:
                score += 6
        for filter_item in query.hard_filters:
            if not self._matches_filter(candidate, filter_item):
                return -999
            score += 4
        for filter_item in query.soft_filters:
            if self._matches_filter(candidate, filter_item):
                score += 2
        return score

    def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult:
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
        notes.append("Mock CTS also leaves exclude_ids to the runner so dedup and shortage paths are exercised.")
        return CTSFetchResult(
            request_payload=payload,
            candidates=selected,
            total=len(scored),
            raw_candidate_count=len(selected),
            adapter_notes=notes,
            latency_ms=1,
            response_message="mock search completed",
        )
