from __future__ import annotations

import ast
from pathlib import Path

from seektalent.backup_group import product_database_specs
from tests.settings_factory import make_settings


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def test_core_seektalent_package_does_not_import_workbench_v2_or_ui() -> None:
    forbidden = {
        "seektalent_workbench_v2",
        "seektalent_ui",
    }

    violations = _forbidden_imports(SRC_ROOT / "seektalent", forbidden)

    assert violations == []


def test_workbench_v2_package_does_not_import_legacy_workbench_projection_paths() -> None:
    forbidden = {
        "seektalent_ui.agent_workbench_transcript",
        "seektalent_ui.agent_workbench_response",
        "seektalent_ui.agent_workbench_projection",
        "seektalent_conversation_agent.first_turn_store",
    }

    violations = _forbidden_imports(SRC_ROOT / "seektalent_workbench_v2", forbidden)

    assert violations == []


def test_workbench_v2_route_shim_does_not_import_old_projection_or_runtime_layers() -> None:
    route_file = SRC_ROOT / "seektalent_ui" / "agent_workbench_v2_routes.py"
    forbidden = {
        "seektalent_ui.agent_workbench_transcript",
        "seektalent_ui.agent_workbench_response",
        "seektalent_ui.agent_workbench_projection",
        "seektalent_conversation_agent.first_turn_store",
        "seektalent_workbench_v2.agent_loop",
        "seektalent_workbench_v2.runtime_service",
    }

    assert route_file.exists()
    imported = _imported_modules(route_file)

    assert [(route_file, module) for module in imported for target in forbidden if _matches(module, target)] == []


def test_workbench_v2_database_is_in_product_backup_group(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path))

    specs = {spec.name: spec.path for spec in product_database_specs(settings)}

    assert specs["workbench_v2"] == tmp_path / ".seektalent" / "workbench_v2.sqlite3"


def _forbidden_imports(root: Path, forbidden: set[str]) -> list[tuple[Path, str]]:
    violations: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*.py")):
        imported = _imported_modules(path)
        for module in sorted(imported):
            if any(_matches(module, target) for target in forbidden):
                violations.append((path.relative_to(REPO_ROOT), module))
    return violations


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def _matches(module: str, target: str) -> bool:
    return module == target or module.startswith(f"{target}.")
