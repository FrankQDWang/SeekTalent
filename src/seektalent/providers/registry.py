from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.providers.cts import CTSProviderAdapter
from seektalent.providers.liepin import LiepinProviderAdapter
from seektalent.providers.liepin.client import build_liepin_worker_client
from seektalent.providers.liepin.store import LiepinStore


def get_provider_adapter(settings: AppSettings) -> ProviderAdapter:
    if settings.provider_name == "cts":
        return CTSProviderAdapter(settings)
    if settings.provider_name == "liepin":
        if settings.liepin_worker_mode == "disabled":
            raise ValueError("Liepin provider cannot be selected while liepin_worker_mode is disabled.")
        store = None
        if settings.liepin_worker_mode in {"managed_local", "external_http"}:
            store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
        return LiepinProviderAdapter(
            settings,
            worker_client=build_liepin_worker_client(settings),
            store=store,
        )
    raise ValueError(f"Unsupported provider_name: {settings.provider_name}")
