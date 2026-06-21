from __future__ import annotations

import importlib

import pytest

from tests.settings_factory import make_settings


def test_default_provider_plugins_are_explicitly_registered() -> None:
    plugins = importlib.import_module("seektalent.providers.plugins")

    registry = plugins.build_default_provider_adapter_registry()

    assert registry.source_ids == ("cts", "liepin")


def test_provider_plugin_registry_rejects_duplicate_sources() -> None:
    plugins = importlib.import_module("seektalent.providers.plugins")

    plugin = plugins.ProviderAdapterPlugin(
        source_id="demo",
        build_adapter=lambda context: object(),
    )

    with pytest.raises(ValueError, match="duplicate_provider_plugin:demo"):
        plugins.ProviderAdapterRegistry([plugin, plugin])


def test_provider_registry_uses_plugin_context_for_liepin_disabled_policy() -> None:
    plugins = importlib.import_module("seektalent.providers.plugins")
    settings = make_settings(liepin_worker_mode="disabled")

    registry = plugins.build_default_provider_adapter_registry()

    with pytest.raises(ValueError, match="Liepin provider cannot be selected"):
        registry.build_adapter(
            "liepin",
            plugins.ProviderAdapterBuildContext(settings=settings),
        )
