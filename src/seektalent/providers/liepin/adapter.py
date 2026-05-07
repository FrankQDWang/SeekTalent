from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderCapabilities
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult


class LiepinProviderAdapter:
    name = "liepin"

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def describe_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_structured_filters=True,
            supports_detail_fetch=True,
            supports_fetch_mode_summary=True,
            supports_fetch_mode_detail=True,
            paging_mode="cursor",
            recommended_max_concurrency=1,
            has_stable_external_id=True,
            has_stable_dedup_key=True,
        )

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
        raise NotImplementedError("Liepin provider search is not implemented in Task 1.")
