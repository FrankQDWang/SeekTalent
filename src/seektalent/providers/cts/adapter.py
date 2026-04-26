from __future__ import annotations

from seektalent.clients.cts_client import CTSClient
from seektalent.clients.cts_client import CTSClientProtocol
from seektalent.clients.cts_client import MockCTSClient
from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderCapabilities
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import CTSQuery
from seektalent.providers.cts.mapper import build_provider_candidate

QUERY_ROLE_TO_CTS = {
    "primary": "exploit",
    "expansion": "explore",
}


class CTSProviderAdapter:
    name = "cts"

    def __init__(self, settings: AppSettings, client: CTSClientProtocol | None = None) -> None:
        self.settings = settings
        self.client = client or (MockCTSClient(settings) if settings.mock_cts else CTSClient(settings))

    def describe_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_structured_filters=True,
            supports_detail_fetch=False,
            supports_fetch_mode_summary=True,
            supports_fetch_mode_detail=False,
            paging_mode="cursor",
            recommended_max_concurrency=1,
            has_stable_external_id=False,
            has_stable_dedup_key=False,
        )

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
        if request.fetch_mode != "summary":
            raise ValueError("CTS provider does not support fetch_mode=detail.")

        page = _decode_cursor(request.cursor)
        cts_query_role = QUERY_ROLE_TO_CTS[request.query_role]
        cts_query = CTSQuery(
            query_role=cts_query_role,
            query_terms=request.query_terms,
            keyword_query=request.keyword_query,
            native_filters=dict(request.provider_filters),
            page=page,
            page_size=request.page_size,
            rationale=f"Provider adapter request for {request.query_role} query terms.",
            adapter_notes=[
                *request.adapter_notes,
                f"CTS query_role {cts_query_role} mapped from provider role {request.query_role}.",
            ],
        )
        result = await self.client.search(cts_query, round_no=round_no, trace_id=trace_id)
        candidates = [build_provider_candidate(candidate) for candidate in result.candidates]
        exhausted = len(candidates) < request.page_size
        return SearchResult(
            candidates=candidates,
            diagnostics=result.adapter_notes,
            exhausted=exhausted,
            next_cursor=None if exhausted else str(page + 1),
            request_payload=result.request_payload,
            raw_candidate_count=result.raw_candidate_count,
            latency_ms=result.latency_ms,
        )


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 1
    try:
        page = int(cursor)
    except ValueError as exc:
        raise ValueError(f"Invalid CTS cursor: {cursor!r}") from exc
    if page < 1:
        raise ValueError(f"Invalid CTS cursor: {cursor!r}")
    return page
