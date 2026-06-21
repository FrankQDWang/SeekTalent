from __future__ import annotations

import ast
import importlib
import inspect

import seektalent.source_adapters.runtime_factory as runtime_factory
import seektalent.runtime.composition as runtime_composition
from tests.settings_factory import make_settings


def _imported_modules(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_source_adapter_runtime_factory_is_not_the_composition_root() -> None:
    imports = _imported_modules(runtime_factory)

    assert "seektalent.providers" not in imports
    assert "seektalent.runtime.orchestrator" not in imports


def test_runtime_composition_contract_is_source_agnostic() -> None:
    imports = _imported_modules(runtime_composition)

    assert "seektalent.providers" not in imports
    assert not any(module.startswith("seektalent.source_adapters") for module in imports)


def test_source_adapter_composition_builds_explicit_runtime_dependencies() -> None:
    composition_module = importlib.import_module("seektalent.source_adapters.runtime_composition")
    settings = make_settings(mock_cts=True)

    composition = composition_module.build_runtime_composition(settings)

    assert composition.settings is settings
    assert composition.source_registry.get("cts").source_id == "cts"
    assert composition.source_round_adapter_provider is not None
    assert composition.source_query_policy_provider is not None
    assert composition.retrieval_service is not None
