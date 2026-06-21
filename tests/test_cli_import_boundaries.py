from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys

import seektalent.cli as cli


FORBIDDEN_TOP_LEVEL_IMPORTS = {
    "seektalent.api",
    "seektalent.corpus",
    "seektalent.evaluation",
    "seektalent.flywheel",
    "seektalent.liepin_smoke_cli",
    "seektalent.product_env",
    "seektalent.providers",
    "seektalent.runtime.lifecycle",
}


def _top_level_imports(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_cli_module_does_not_import_behavior_subsystems_at_top_level() -> None:
    imports = _top_level_imports(cli)

    assert not FORBIDDEN_TOP_LEVEL_IMPORTS & imports


def test_cli_import_does_not_load_behavior_subsystems_in_fresh_process() -> None:
    script = """
import json
import sys

import seektalent.cli

print(json.dumps(sorted(sys.modules), separators=(",", ":")))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    imported_modules = set(json.loads(completed.stdout))

    violations = sorted(
        module
        for module in imported_modules
        for forbidden in FORBIDDEN_TOP_LEVEL_IMPORTS
        if module == forbidden or module.startswith(f"{forbidden}.")
    )
    assert violations == []
