from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import ProviderAdapter
from seektalent.providers.cts import CTSProviderAdapter
from seektalent.providers.liepin import LiepinProviderAdapter
from seektalent.providers.liepin.adapter import ProviderConnectionSafetyResolver
from seektalent.providers.liepin.client import (
    LiepinWorkerClient,
    build_liepin_worker_client,
    is_live_liepin_worker_mode,
)
from seektalent.providers.liepin.store import LiepinStore


@dataclass(frozen=True)
class ProviderAdapterBuildContext:
    settings: AppSettings
    liepin_worker_client: LiepinWorkerClient | None = None
    liepin_store: LiepinStore | None = None
    liepin_connection_safety_resolver: ProviderConnectionSafetyResolver | None = None


@dataclass(frozen=True)
class ProviderAdapterPlugin:
    source_id: str
    build_adapter: Callable[[ProviderAdapterBuildContext], ProviderAdapter]


class ProviderAdapterRegistry:
    def __init__(self, plugins: Iterable[ProviderAdapterPlugin]) -> None:
        providers_by_source: dict[str, ProviderAdapterPlugin] = {}
        for plugin in plugins:
            if plugin.source_id in providers_by_source:
                raise ValueError(f"duplicate_provider_plugin:{plugin.source_id}")
            providers_by_source[plugin.source_id] = plugin
        self._providers_by_source = providers_by_source

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self._providers_by_source)

    def build_adapter(
        self,
        source_id: str,
        context: ProviderAdapterBuildContext,
    ) -> ProviderAdapter:
        plugin = self._providers_by_source.get(source_id)
        if plugin is None:
            raise ValueError(f"Unsupported source: {source_id}")
        return plugin.build_adapter(context)


def build_default_provider_adapter_registry() -> ProviderAdapterRegistry:
    return ProviderAdapterRegistry(
        [
            ProviderAdapterPlugin(source_id="cts", build_adapter=_build_cts_provider_adapter),
            ProviderAdapterPlugin(source_id="liepin", build_adapter=_build_liepin_provider_adapter),
        ]
    )


def _build_cts_provider_adapter(context: ProviderAdapterBuildContext) -> ProviderAdapter:
    return CTSProviderAdapter(context.settings)


def _build_liepin_provider_adapter(context: ProviderAdapterBuildContext) -> ProviderAdapter:
    settings = context.settings
    if settings.liepin_worker_mode == "disabled":
        raise ValueError("Liepin provider cannot be selected while liepin_worker_mode is disabled.")
    store = context.liepin_store
    if store is None and is_live_liepin_worker_mode(settings.liepin_worker_mode):
        store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    return LiepinProviderAdapter(
        settings,
        worker_client=context.liepin_worker_client or build_liepin_worker_client(settings),
        store=store,
        connection_safety_resolver=context.liepin_connection_safety_resolver,
    )
