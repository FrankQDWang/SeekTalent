from __future__ import annotations

import asyncio

from seektalent.core.retrieval.provider_contract import ProviderCapabilities
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import ResumeCandidate
from seektalent.providers import get_provider_adapter
from tests.settings_factory import make_settings


def test_provider_registry_returns_cts_adapter() -> None:
    settings = make_settings()

    provider = get_provider_adapter(settings)

    assert provider.name == "cts"
    capabilities = provider.describe_capabilities()
    assert capabilities.supports_structured_filters is True
    assert capabilities.supports_fetch_mode_summary is True
    assert capabilities.paging_mode == "cursor"


def test_provider_contract_fake_provider_search() -> None:
    class FakeProvider:
        name = "fake"

        def describe_capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                supports_structured_filters=True,
                supports_detail_fetch=False,
                supports_fetch_mode_summary=True,
                supports_fetch_mode_detail=False,
                paging_mode="cursor",
                recommended_max_concurrency=2,
                has_stable_external_id=True,
                has_stable_dedup_key=True,
            )

        async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
            assert request.query_terms == ["python"]
            assert request.query_role == "primary"
            assert request.fetch_mode == "summary"
            assert round_no == 1
            assert trace_id == "trace-1"
            return SearchResult(
                candidates=[
                    ResumeCandidate(
                        resume_id="resume-1",
                        source_resume_id="source-1",
                        snapshot_sha256="snap-1",
                        dedup_key="resume-1",
                        search_text="candidate summary",
                        raw={"resumeId": "resume-1"},
                    )
                ],
                diagnostics=["used native location filter"],
                exhausted=False,
                next_cursor="page=2",
            )

    provider = FakeProvider()
    request = SearchRequest(
        query_terms=["python"],
        query_role="primary",
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=25,
    )
    result = asyncio.run(provider.search(request, round_no=1, trace_id="trace-1"))

    assert provider.name == "fake"
    capabilities = provider.describe_capabilities()
    assert capabilities.supports_structured_filters is True
    assert capabilities.paging_mode == "cursor"
    assert result.candidates[0].resume_id == "resume-1"
    assert result.next_cursor == "page=2"
