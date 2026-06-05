from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.service import RetrievalService
from seektalent.providers import get_provider_adapter


def build_retrieval_service(settings: AppSettings) -> RetrievalService:
    return RetrievalService(provider=get_provider_adapter(settings))
