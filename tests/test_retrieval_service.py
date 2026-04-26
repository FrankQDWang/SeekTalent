from __future__ import annotations

import asyncio

from seektalent.core.retrieval.provider_contract import ProviderCapabilities
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.core.retrieval.service import RetrievalService
from seektalent.models import ResumeCandidate
from seektalent.models import RuntimeConstraint


def test_retrieval_service_builds_search_request_and_calls_provider() -> None:
    captured_request: SearchRequest | None = None

    class FakeProvider:
        name = "fake"

        def describe_capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                supports_structured_filters=False,
                supports_detail_fetch=False,
                supports_fetch_mode_summary=True,
                supports_fetch_mode_detail=False,
                paging_mode="cursor",
                recommended_max_concurrency=1,
                has_stable_external_id=True,
                has_stable_dedup_key=True,
            )

        async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
            nonlocal captured_request
            captured_request = request
            assert round_no == 2
            assert trace_id == "trace-2"
            return SearchResult(
                candidates=[
                    ResumeCandidate(
                        resume_id="resume-1",
                        source_resume_id="source-1",
                        snapshot_sha256="snap",
                        dedup_key="resume-1",
                        search_text="python engineer",
                        raw={"resumeId": "resume-1"},
                    )
                ],
                diagnostics=["provider search"],
                exhausted=False,
                next_cursor="2",
            )

    runtime_constraints = [
        RuntimeConstraint(
            field="age_requirement",
            normalized_value=["min=25", "max=35"],
            source="notes",
            rationale="Age note",
            blocking=False,
        )
    ]
    service = RetrievalService(provider=FakeProvider())

    result = asyncio.run(
        service.search(
            query_terms=["python", "backend"],
            query_role="primary",
            keyword_query="python OR backend",
            adapter_notes=["runtime note"],
            provider_filters={"age": 3},
            runtime_constraints=runtime_constraints,
            page_size=25,
            round_no=2,
            trace_id="trace-2",
        )
    )

    assert captured_request == SearchRequest(
        query_terms=["python", "backend"],
        query_role="primary",
        keyword_query="python OR backend",
        adapter_notes=["runtime note"],
        provider_filters={"age": 3},
        runtime_constraints=runtime_constraints,
        fetch_mode="summary",
        page_size=25,
        cursor=None,
    )
    assert result.candidates[0].resume_id == "resume-1"
    assert result.next_cursor == "2"


def test_retrieval_service_forwards_cursor_and_fetch_mode() -> None:
    captured_request: SearchRequest | None = None

    class FakeProvider:
        name = "fake"

        def describe_capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                supports_structured_filters=False,
                supports_detail_fetch=False,
                supports_fetch_mode_summary=True,
                supports_fetch_mode_detail=False,
                paging_mode="cursor",
                recommended_max_concurrency=1,
                has_stable_external_id=True,
                has_stable_dedup_key=True,
            )

        async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
            nonlocal captured_request
            captured_request = request
            assert round_no == 3
            assert trace_id == "trace-3"
            return SearchResult(exhausted=True)

    service = RetrievalService(provider=FakeProvider())

    result = asyncio.run(
        service.search(
            query_terms=["python"],
            query_role="expansion",
            keyword_query="python title:trace",
            adapter_notes=["city dispatch"],
            provider_filters={"location": ["上海"]},
            runtime_constraints=[],
            page_size=10,
            round_no=3,
            trace_id="trace-3",
            fetch_mode="detail",
            cursor="5",
        )
    )

    assert captured_request == SearchRequest(
        query_terms=["python"],
        query_role="expansion",
        keyword_query="python title:trace",
        adapter_notes=["city dispatch"],
        provider_filters={"location": ["上海"]},
        runtime_constraints=[],
        fetch_mode="detail",
        page_size=10,
        cursor="5",
    )
    assert result.exhausted is True
    assert result.raw_candidate_count == 0
