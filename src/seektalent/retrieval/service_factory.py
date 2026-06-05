from __future__ import annotations

from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.core.retrieval.service import RetrievalService


def build_retrieval_service(provider: ProviderAdapter) -> RetrievalService:
    return RetrievalService(provider=provider)
