from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from seektalent.clients.cts_models import Candidate, CandidateSearchRequest, CandidateSearchResponse
from seektalent.config import AppSettings
from seektalent.locations import normalize_location, normalize_locations
from seektalent.mock_data import load_mock_resume_corpus
from seektalent.models import RetrievedCandidate_t, SearchExecutionPlan_t, stable_fallback_resume_id
from seektalent.retrieval import project_search_plan_to_cts

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
DEGREE_ORDER = {
    "大专": 1,
    "专科": 1,
    "本科": 2,
    "学士": 2,
    "硕士": 3,
    "研究生": 3,
    "博士": 4,
}
SCHOOL_TYPE_SCHOOLS = {
    1: {"复旦大学", "上海交通大学", "浙江大学", "北京邮电大学"},
    2: {"复旦大学", "上海交通大学", "浙江大学", "北京邮电大学"},
    3: {"复旦大学", "上海交通大学", "浙江大学"},
}
WORK_EXPERIENCE_BUCKETS = {
    1: (0, 1),
    2: (1, 3),
    3: (3, 5),
    4: (5, 10),
    5: (10, None),
}
AGE_BUCKETS = {
    1: (20, 25),
    2: (25, 30),
    3: (30, 35),
    4: (35, 40),
    5: (40, 45),
    6: (45, None),
}
GENDER_CODES = {
    1: "男",
    2: "女",
}


class CTSFetchResult(BaseModel):
    request_payload: dict[str, Any]
    candidates: list[RetrievedCandidate_t]
    total: int | None = None
    raw_candidate_count: int = 0
    adapter_notes: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    response_message: str | None = None


class CTSClientProtocol(Protocol):
    async def search(self, plan: SearchExecutionPlan_t, *, trace_id: str = "") -> CTSFetchResult: ...


class BaseCTSClient:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def build_request_payload(self, plan: SearchExecutionPlan_t) -> tuple[dict[str, Any], list[str]]:
        native_filters, notes = project_search_plan_to_cts(plan)
        payload: dict[str, Any] = {
            "keyword": _serialize_keyword_query(plan.query_terms),
            "page": 1,
            "pageSize": plan.target_new_candidate_count,
        }
        for field, value in native_filters.items():
            if field not in ALLOWED_NATIVE_FILTERS:
                raise ValueError(f"Unsupported native filter: {field}")
            if field != "location" and isinstance(value, list):
                raise ValueError(f"Native filter `{field}` must not be a list.")
            if field in {"degree", "schoolType", "workExperienceRange", "gender", "age"} and not isinstance(value, int):
                raise ValueError(f"Native filter `{field}` must be an integer code.")
            payload[field] = value
        request = CandidateSearchRequest.model_validate(payload)
        return request.model_dump(exclude_none=True), notes

    def _extract_candidate_id(self, candidate: Candidate) -> str:
        extra = candidate.model_extra or {}
        for key in ("resume_id", "resumeId", "id", "candidate_id", "candidateId"):
            value = extra.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
        return stable_fallback_resume_id(
            {
                "candidate_name": extra.get("candidateName") or extra.get("candidate_name") or "",
                "current_title": candidate.expectedJobCategory,
                "current_company": candidate.workExperienceList[0].company if candidate.workExperienceList else None,
                "locations": [item for item in [candidate.nowLocation, candidate.expectedLocation] if item],
            }
        )

    def _normalize_candidate(self, candidate: Candidate) -> RetrievedCandidate_t:
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
        return RetrievedCandidate_t(
            candidate_id=self._extract_candidate_id(candidate),
            age=candidate.age,
            gender=candidate.gender,
            now_location=normalize_location(candidate.nowLocation),
            expected_location=normalize_location(candidate.expectedLocation),
            years_of_experience_raw=candidate.workYear,
            education_summaries=education_summaries,
            work_experience_summaries=work_experience_summaries,
            project_names=candidate.projectNameAll,
            work_summaries=candidate.workSummariesAll,
            search_text=search_text,
            raw_payload=raw_payload,
        )


class CTSClient(BaseCTSClient):
    async def search(self, plan: SearchExecutionPlan_t, *, trace_id: str = "") -> CTSFetchResult:
        self.settings.require_cts_credentials()
        payload, notes = self.build_request_payload(plan)
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
        candidates: list[RetrievedCandidate_t] = []
        if parsed.data is not None:
            candidates = [self._normalize_candidate(item) for item in parsed.data.candidates]
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

    def _candidate_field_text(self, candidate: RetrievedCandidate_t, field: str) -> str:
        mapping = {
            "location": " ".join([candidate.now_location or "", candidate.expected_location or ""]),
            "position": _raw_text(candidate, "title"),
            "company": " ".join(candidate.work_experience_summaries),
            "school": " ".join(candidate.education_summaries),
            "workContent": " ".join(candidate.work_summaries + candidate.work_experience_summaries),
        }
        return mapping.get(field, candidate.search_text)

    def _matches_filter(self, candidate: RetrievedCandidate_t, field: str, value: str | int | list[str]) -> bool:
        if field == "location":
            candidate_locations = normalize_locations([candidate.now_location, candidate.expected_location])
            if isinstance(value, list):
                return any(normalize_location(str(item)) in candidate_locations for item in value)
            return normalize_location(str(value)) in candidate_locations
        if isinstance(value, int):
            return _matches_native_code(candidate, field, value)
        haystack = self._candidate_field_text(candidate, field).casefold()
        if isinstance(value, str) and "|" in value:
            parts = [part.strip() for part in value.split("|") if part.strip()]
            return any(part.casefold() in haystack for part in parts)
        if isinstance(value, list):
            return any(str(item).casefold() in haystack for item in value)
        return str(value).casefold() in haystack

    def _retrieval_score(self, candidate: RetrievedCandidate_t, plan: SearchExecutionPlan_t) -> int:
        text = candidate.search_text.casefold()
        score = 0
        for keyword in plan.query_terms:
            if keyword.casefold() in text:
                score += 6
        native_filters, _ = project_search_plan_to_cts(plan)
        for field, value in native_filters.items():
            if not self._matches_filter(candidate, field, value):
                return -999
            score += 4
        return score

    async def search(self, plan: SearchExecutionPlan_t, *, trace_id: str = "") -> CTSFetchResult:
        del trace_id
        payload, notes = self.build_request_payload(plan)
        scored = [
            (self._retrieval_score(candidate, plan), index, candidate)
            for index, candidate in enumerate(self.corpus)
        ]
        scored = [item for item in scored if item[0] > -999]
        scored.sort(key=lambda item: (-item[0], item[1], item[2].candidate_id))
        selected = [candidate for _, _, candidate in scored[: plan.target_new_candidate_count]]
        return CTSFetchResult(
            request_payload=payload,
            candidates=selected,
            total=len(scored),
            raw_candidate_count=len(selected),
            adapter_notes=notes,
            latency_ms=1,
            response_message="mock search completed",
        )


def _raw_text(candidate: RetrievedCandidate_t, key: str) -> str:
    value = candidate.raw_payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _serialize_keyword_query(terms: list[str]) -> str:
    serialized: list[str] = []
    for term in terms:
        clean = " ".join(term.split()).strip()
        if not clean:
            continue
        if " " in clean or "\t" in clean:
            serialized.append(f'"{clean.replace("\\", "\\\\").replace("\"", "\\\"")}"')
            continue
        serialized.append(clean)
    return " ".join(serialized)


def _matches_native_code(candidate: RetrievedCandidate_t, field: str, code: int) -> bool:
    if field == "degree":
        return _candidate_degree_rank(candidate) >= code
    if field == "schoolType":
        schools = SCHOOL_TYPE_SCHOOLS.get(code, set())
        return any(school in summary for school in schools for summary in candidate.education_summaries)
    if field == "workExperienceRange":
        return _matches_bucket(candidate.years_of_experience_raw, WORK_EXPERIENCE_BUCKETS.get(code))
    if field == "gender":
        expected = GENDER_CODES.get(code)
        return bool(expected and candidate.gender == expected)
    if field == "age":
        return _matches_bucket(candidate.age, AGE_BUCKETS.get(code))
    return False


def _candidate_degree_rank(candidate: RetrievedCandidate_t) -> int:
    rank = 0
    for summary in candidate.education_summaries:
        for keyword, value in DEGREE_ORDER.items():
            if keyword in summary:
                rank = max(rank, value)
    return rank


def _matches_bucket(value: int | None, bucket: tuple[int, int | None] | None) -> bool:
    if value is None or bucket is None:
        return False
    lower, upper = bucket
    if upper is None:
        return value >= lower
    return lower <= value <= upper
