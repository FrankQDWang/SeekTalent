from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MAIN_PATH_BOUNDARY_INVENTORY = {
    "workbench_routes": {
        "path": Path("src/seektalent_ui/workbench_routes.py"),
        "owns": "HTTP request validation and response projection for the local Workbench API.",
        "must_not_import": {
            "seektalent.providers.liepin.compliance",
            "seektalent.providers.liepin.store",
        },
    },
    "workbench_liepin_boundary": {
        "path": Path("src/seektalent_ui/liepin_account_binding.py"),
        "owns": "Workbench-owned Liepin compliance gate and account binding orchestration.",
        "may_import": {
            "seektalent.providers.liepin.compliance",
            "seektalent.providers.liepin.store",
        },
    },
    "runtime_liepin_contract": {
        "path": Path("src/seektalent/runtime/source_lanes.py"),
        "owns": "Runtime source-lane public payload and typed source context contracts.",
    },
    "liepin_provider_lane": {
        "path": Path("src/seektalent/providers/liepin/runtime_lane.py"),
        "owns": "Liepin provider-specific source lane execution and provider request projection.",
    },
}


def test_main_path_boundary_inventory_references_existing_files() -> None:
    missing = [
        str(item["path"])
        for item in MAIN_PATH_BOUNDARY_INVENTORY.values()
        if not (ROOT / item["path"]).exists()
    ]

    assert missing == []


def test_workbench_routes_does_not_import_liepin_provider_persistence() -> None:
    route_path = ROOT / MAIN_PATH_BOUNDARY_INVENTORY["workbench_routes"]["path"]
    forbidden = MAIN_PATH_BOUNDARY_INVENTORY["workbench_routes"]["must_not_import"]

    offenders = [
        f"{route_path.relative_to(ROOT)}:{line_no}:{module}"
        for line_no, module in _imports(route_path)
        if module in forbidden
    ]

    assert offenders == []


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append((node.lineno, node.module))
    return imports
