from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.providers.cts import CTSProviderAdapter
from seektalent.providers.liepin import LiepinProviderAdapter


def get_provider_adapter(settings: AppSettings) -> ProviderAdapter:
    if settings.provider_name == "cts":
        return CTSProviderAdapter(settings)
    if settings.provider_name == "liepin":
        if settings.liepin_worker_mode == "disabled":
            raise ValueError("Liepin provider cannot be selected while liepin_worker_mode is disabled.")
        return LiepinProviderAdapter(settings)
    raise ValueError(f"Unsupported provider_name: {settings.provider_name}")
