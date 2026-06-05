from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path("src/seektalent/runtime")
PROVIDERS_ROOT = Path("src/seektalent/providers")
SOURCES_ROOT = Path("src/seektalent/sources")

FORBIDDEN_RUNTIME_IMPORTS = (
    "seektalent.providers",
    "seektalent.clients.cts_client",
)
FORBIDDEN_PROVIDER_IMPORTS = (
    "seektalent.runtime",
)
SOURCE_KIND_RE = re.compile(r"SourceKind\s*=\s*Literal\[\s*['\"]cts['\"]\s*,\s*['\"]liepin['\"]\s*\]")
SOURCE_BRANCH_RE = re.compile(r"\bif\s+[\w.]*source[\w.]*\s*==\s*['\"](?:cts|liepin)['\"]")
OPENCLI_RE = re.compile(r"liepin_opencli|opencli", re.IGNORECASE)


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_module_or_child(module_name: str, forbidden_module: str) -> bool:
    return module_name == forbidden_module or module_name.startswith(f"{forbidden_module}.")


def _import_failures(
    *,
    project_root: Path,
    path: Path,
    forbidden_modules: tuple[str, ...],
    message: str,
) -> list[str]:
    relative_path = _relative(project_root, path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{relative_path}:{exc.lineno or 1}: syntax error prevents boundary scan"]

    failures: list[str] = []
    for node in ast.walk(tree):
        imported_modules: list[tuple[int, str]] = []
        if isinstance(node, ast.Import):
            imported_modules.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append((node.lineno, node.module))
        for line_no, module_name in imported_modules:
            if any(_is_module_or_child(module_name, forbidden) for forbidden in forbidden_modules):
                failures.append(f"{relative_path}:{line_no}: {message}")
                break
    return failures


def _runtime_text_failures(project_root: Path, path: Path) -> list[str]:
    relative_path = _relative(project_root, path)
    failures: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if SOURCE_KIND_RE.search(line):
            failures.append(f"{relative_path}:{line_no}: runtime must not define CTS/Liepin-only SourceKind")
        if SOURCE_BRANCH_RE.search(line):
            failures.append(f"{relative_path}:{line_no}: runtime must not branch on concrete source ids")
        if OPENCLI_RE.search(line):
            failures.append(f"{relative_path}:{line_no}: runtime must not contain OpenCLI/Liepin reason codes")
    return failures


def _source_contract_text_failures(project_root: Path, path: Path) -> list[str]:
    relative_path = _relative(project_root, path)
    failures: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if SOURCE_KIND_RE.search(line):
            failures.append(
                f"{relative_path}:{line_no}: source contracts must not define CTS/Liepin-only SourceKind"
            )
    return failures


def _tach_boundary_failures(project_root: Path) -> list[str]:
    tach_path = project_root / "tach.toml"
    if not tach_path.exists():
        return []
    payload = tomllib.loads(tach_path.read_text(encoding="utf-8"))
    dependencies_by_module = {
        str(module["path"]): [str(dependency) for dependency in module.get("depends_on", [])]
        for module in payload.get("modules", [])
        if isinstance(module, dict) and "path" in module
    }

    failures: list[str] = []
    provider_dependencies = dependencies_by_module.get("seektalent.providers", [])
    runtime_dependencies = dependencies_by_module.get("seektalent.runtime", [])
    if "seektalent.runtime" in provider_dependencies:
        failures.append("tach.toml: seektalent.providers must not depend on seektalent.runtime")
    if "seektalent.providers" in runtime_dependencies:
        failures.append("tach.toml: seektalent.runtime must not depend on seektalent.providers")
    return failures


def collect_source_boundary_failures(project_root: Path = PROJECT_ROOT) -> list[str]:
    failures: list[str] = []
    runtime_root = project_root / RUNTIME_ROOT
    providers_root = project_root / PROVIDERS_ROOT
    sources_root = project_root / SOURCES_ROOT

    for path in _python_files(runtime_root):
        failures.extend(
            _import_failures(
                project_root=project_root,
                path=path,
                forbidden_modules=FORBIDDEN_RUNTIME_IMPORTS,
                message="runtime must not import seektalent.providers",
            )
        )
        failures.extend(_runtime_text_failures(project_root, path))

    for path in _python_files(providers_root):
        failures.extend(
            _import_failures(
                project_root=project_root,
                path=path,
                forbidden_modules=FORBIDDEN_PROVIDER_IMPORTS,
                message="providers must not import seektalent.runtime",
            )
        )

    for path in _python_files(sources_root):
        failures.extend(_source_contract_text_failures(project_root, path))

    failures.extend(_tach_boundary_failures(project_root))
    return sorted(failures)


def main() -> int:
    failures = collect_source_boundary_failures(PROJECT_ROOT)
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
