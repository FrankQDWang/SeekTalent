from __future__ import annotations

from dataclasses import dataclass

from seektalent.core.retrieval.provider_contract import FetchMode
from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.core.retrieval.provider_contract import QueryRole
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import ConstraintValue
from seektalent.models import RuntimeConstraint


@dataclass(frozen=True)
class RetrievalService:
    provider: ProviderAdapter

    async def search(
        self,
        *,
        query_terms: list[str],
        query_role: QueryRole,
        keyword_query: str,
        adapter_notes: list[str],
        provider_filters: dict[str, ConstraintValue],
        runtime_constraints: list[RuntimeConstraint],
        page_size: int,
        round_no: int,
        trace_id: str,
        fetch_mode: FetchMode = "summary",
        cursor: str | None = None,
    ) -> SearchResult:
        request = SearchRequest(
            query_terms=query_terms,
            query_role=query_role,
            keyword_query=keyword_query,
            adapter_notes=adapter_notes,
            provider_filters=provider_filters,
            runtime_constraints=runtime_constraints,
            fetch_mode=fetch_mode,
            page_size=page_size,
            cursor=cursor,
        )
        return await self.provider.search(request, round_no=round_no, trace_id=trace_id)
