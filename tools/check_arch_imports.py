from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = PROJECT_ROOT / "src" / "seektalent"
FORBIDDEN_ROOTS = ("seektalent_ui", "experiments")
INSTALLED_BOUNDARY_MODULES = {
    "installed_filesystem.py": "seektalent.installed_filesystem",
    "installed_release.py": "seektalent.installed_release",
    "installed_slot.py": "seektalent.installed_slot",
    "windows_installed_binding.py": "seektalent.windows_installed_binding",
    "windows_native_files.py": "seektalent.windows_native_files",
}
SLOT_LAYOUT_NAMES = {
    "INSTALLATION_ID_RELATIVE_PATH",
    "ACTIVE_SLOT_POINTER_RELATIVE_PATH",
    "ACTIVE_SLOT_LOCK_RELATIVE_PATH",
    "SLOT_LOCK_RELATIVE_PATHS",
    "SLOT_ROOT_RELATIVE_PATHS",
    "MAX_ACTIVE_SLOT_POINTER_BYTES",
    "_OPAQUE_TOKEN_RE",
    "_UTC_RFC3339_RE",
}


def _import_root(name: str) -> str:
    return name.split(".", 1)[0]


def _forbidden_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    failures: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _import_root(alias.name) in FORBIDDEN_ROOTS:
                    failures.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if _import_root(node.module) in FORBIDDEN_ROOTS:
                failures.append((node.lineno, node.module))
    return failures


def _installed_boundary_failures(path: Path) -> list[tuple[int, str]]:
    module_name = INSTALLED_BOUNDARY_MODULES.get(path.name)
    if module_name is None:
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    failures: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if path.name == "installed_release.py" and isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in SLOT_LAYOUT_NAMES:
                    failures.append((node.lineno, f"release layer owns slot lifecycle fact {target.id}"))
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if path.name == "installed_filesystem.py" and node.module.startswith("seektalent"):
            failures.append((node.lineno, f"filesystem layer imports {node.module}"))
        if (
            path.name == "windows_installed_binding.py"
            and node.module.startswith("seektalent")
            and node.module
            not in {
                "seektalent.installed_filesystem",
                "seektalent.windows_native_files",
            }
        ):
            failures.append((node.lineno, f"Windows opened-object layer imports {node.module}"))
        if path.name == "windows_native_files.py" and node.module.startswith("seektalent"):
            failures.append((node.lineno, f"Win32 primitive layer imports {node.module}"))
        if path.name == "installed_release.py" and node.module == "seektalent.installed_slot":
            failures.append((node.lineno, "release layer imports slot lifecycle"))
        if path.name == "installed_slot.py" and node.module == "seektalent.installed_release":
            private_names = [alias.name for alias in node.names if alias.name.startswith("_")]
            if private_names:
                failures.append(
                    (node.lineno, f"slot lifecycle imports release internals: {', '.join(private_names)}")
                )
    return failures


def main() -> int:
    failures: list[str] = []
    for path in sorted(CORE_SRC.rglob("*.py")):
        for line_no, module_name in _forbidden_imports(path):
            relative_path = path.relative_to(PROJECT_ROOT)
            failures.append(f"{relative_path}:{line_no}: forbidden import {module_name}")
        for line_no, detail in _installed_boundary_failures(path):
            relative_path = path.relative_to(PROJECT_ROOT)
            failures.append(f"{relative_path}:{line_no}: {detail}")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
