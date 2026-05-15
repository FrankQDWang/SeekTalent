from __future__ import annotations

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.providers.cts import CTSProviderAdapter
from seektalent.providers.liepin import LiepinProviderAdapter
from seektalent.providers.liepin.client import build_liepin_worker_client
from seektalent.providers.liepin.client import LiepinWorkerClient
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.adapter import ProviderConnectionSafetyResolver


def get_provider_adapter(settings: AppSettings) -> ProviderAdapter:
    return get_provider_adapter_for_source(settings, settings.provider_name)


def get_provider_adapter_for_source(
    settings: AppSettings,
    source: str,
    *,
    liepin_worker_client: LiepinWorkerClient | None = None,
    liepin_store: LiepinStore | None = None,
    liepin_connection_safety_resolver: ProviderConnectionSafetyResolver | None = None,
) -> ProviderAdapter:
    if source == "cts":
        return CTSProviderAdapter(settings)
    if source == "liepin":
        if settings.liepin_worker_mode == "disabled":
            raise ValueError("Liepin provider cannot be selected while liepin_worker_mode is disabled.")
        store = liepin_store
        if store is None and settings.liepin_worker_mode in {"managed_local", "external_http"}:
            store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
        return LiepinProviderAdapter(
            settings,
            worker_client=liepin_worker_client or build_liepin_worker_client(settings),
            store=store,
            connection_safety_resolver=liepin_connection_safety_resolver,
        )
    raise ValueError(f"Unsupported source: {source}")
