from __future__ import annotations

from typing import TYPE_CHECKING

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.providers.plugins import ProviderAdapterBuildContext, build_default_provider_adapter_registry

if TYPE_CHECKING:
    from seektalent.providers.liepin.adapter import ProviderConnectionSafetyResolver
    from seektalent.providers.liepin.client import LiepinWorkerClient
    from seektalent.providers.liepin.store import LiepinStore


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
    registry = build_default_provider_adapter_registry()
    return registry.build_adapter(
        source,
        ProviderAdapterBuildContext(
            settings=settings,
            liepin_worker_client=liepin_worker_client,
            liepin_store=liepin_store,
            liepin_connection_safety_resolver=liepin_connection_safety_resolver,
        ),
    )
