from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "src" / "seektalent_ui" / "server.py"


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _function_names(tree: ast.Module) -> set[str]:
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_server_delegates_liepin_api_routes_to_router_module() -> None:
    tree = _module_tree(SERVER_PATH)
    modules = _imported_modules(tree)
    source = SERVER_PATH.read_text(encoding="utf-8")

    assert "seektalent_ui.liepin_routes" in modules
    assert "create_liepin_router" in source
    assert "/api/liepin" not in source
    assert not {
        "seektalent.providers.liepin.compliance",
        "seektalent.providers.liepin.models",
        "seektalent.providers.liepin.security",
        "seektalent.providers.liepin.store",
    } & modules
    assert not {
        "_gate_response",
        "_connection_response",
        "_scope_from_stream_cookie",
        "_event_generator",
        "_sequence_from_header",
        "_stream_cookie_secure",
        "_required_liepin_account_binding_secret",
        "_required_liepin_stream_token_secret",
    } & _function_names(tree)


def test_server_delegates_packaged_frontend_mounting() -> None:
    tree = _module_tree(SERVER_PATH)
    modules = _imported_modules(tree)

    assert "seektalent_ui.static_frontend" in modules
    assert "fastapi.staticfiles" not in modules
    assert "seektalent_ui.resources" not in modules
    assert "mount_packaged_frontend" not in _function_names(tree)


def test_server_delegates_workbench_database_path_policy() -> None:
    tree = _module_tree(SERVER_PATH)
    modules = _imported_modules(tree)

    assert "seektalent_ui.workbench_paths" in modules
    assert not {
        "_liepin_db_path",
        "_workbench_db_path",
        "_agent_workbench_stream_db_path",
    } & _function_names(tree)
