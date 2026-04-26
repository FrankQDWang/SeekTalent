from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.core.retrieval.provider_contract import ProviderCapabilities
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult


class _PendingCTSProviderAdapter:
    # Phase-one placeholder until Task 4 adds the real CTS adapter.
    name = "cts"

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def describe_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_structured_filters=True,
            supports_detail_fetch=False,
            supports_fetch_mode_summary=False,
            supports_fetch_mode_detail=False,
            paging_mode="cursor",
            recommended_max_concurrency=1,
            has_stable_external_id=False,
            has_stable_dedup_key=False,
        )

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
        raise NotImplementedError("CTS provider adapter search is not implemented until Task 4.")


def get_provider_adapter(settings: AppSettings) -> ProviderAdapter:
    provider_name = getattr(settings, "retrieval_provider", "cts")
    if provider_name == "cts":
        return _PendingCTSProviderAdapter(settings)
    raise ValueError(f"Unsupported retrieval_provider: {provider_name}")
