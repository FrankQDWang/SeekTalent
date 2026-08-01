from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MAIN_PATH_BOUNDARY_INVENTORY = {
    "runtime_execution": {
        "path": Path("src/seektalent_ui/runtime_execution.py"),
        "owns": "The single production runtime-control store, command service, and executor composition.",
    },
    "workbench_v2_routes": {
        "path": Path("src/seektalent_ui/agent_workbench_v2_routes.py"),
        "owns": "The active V2 request boundary and typed response projection.",
    },
    "runtime_liepin_contract": {
        "path": Path("src/seektalent/runtime/source_lanes.py"),
        "owns": "Runtime source-lane public payload and typed source context contracts.",
    },
    "liepin_source_lane": {
        "path": Path("src/seektalent/sources/liepin/runtime_lane.py"),
        "owns": "Liepin source-adapter lane execution and provider request projection.",
    },
}


def test_main_path_boundary_inventory_references_existing_files() -> None:
    missing = [
        str(item["path"])
        for item in MAIN_PATH_BOUNDARY_INVENTORY.values()
        if not (ROOT / item["path"]).exists()
    ]

    assert missing == []


def test_v2_routes_do_not_import_legacy_product_surfaces() -> None:
    route_path = ROOT / MAIN_PATH_BOUNDARY_INVENTORY["workbench_v2_routes"]["path"]
    source = route_path.read_text(encoding="utf-8")
    assert "seektalent_conversation_agent" not in source
    assert "seektalent_agent_memory" not in source
    assert "workbench_routes" not in source


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append((node.lineno, node.module))
    return imports
