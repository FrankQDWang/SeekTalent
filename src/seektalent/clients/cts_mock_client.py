from __future__ import annotations

from seektalent.clients.cts_contracts import CTSFetchResult
from seektalent.clients.cts_request import build_cts_request_payload
from seektalent.config import AppSettings
from seektalent.locations import normalize_location, normalize_locations
from seektalent.mock_data import load_mock_resume_corpus
from seektalent.models import CTSQuery, ResumeCandidate

KEYWORD_MATCH_SCORE = 6
FILTER_MATCH_SCORE = 4
FILTER_MISMATCH_SCORE = -999
MOCK_LATENCY_MS = 1


class MockCTSClient:
    def __init__(self, settings: AppSettings) -> None:
        del settings
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
                score += KEYWORD_MATCH_SCORE
        for field, value in query.native_filters.items():
            if not self._matches_filter(candidate, field, value):
                return FILTER_MISMATCH_SCORE
            score += FILTER_MATCH_SCORE
        return score

    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult:
        del trace_id
        payload, notes = build_cts_request_payload(query)
        scored = [
            (self._retrieval_score(candidate, query), index, candidate)
            for index, candidate in enumerate(self.corpus)
        ]
        scored = [item for item in scored if item[0] > FILTER_MISMATCH_SCORE]
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
            latency_ms=MOCK_LATENCY_MS,
            response_message="mock search completed",
        )
