from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path("src/seektalent/runtime")
PROVIDERS_ROOT = Path("src/seektalent/providers")
SOURCES_ROOT = Path("src/seektalent/sources")
RUNTIME_CONTROL_ROOT = Path("src/seektalent_runtime_control")

FORBIDDEN_RUNTIME_IMPORTS = (
    "seektalent.providers",
    "seektalent.clients.cts_client",
    "seektalent.sources.cts",
    "seektalent.sources.liepin",
    "seektalent.sources",
    "seektalent.source_adapters",
)
FORBIDDEN_PROVIDER_IMPORTS = (
    "seektalent.runtime",
)
FORBIDDEN_RUNTIME_CONTROL_SERVICE_IMPORTS = (
    "seektalent.providers",
)
FORBIDDEN_RUNTIME_CONTROL_NON_EXECUTOR_IMPORTS = (
    "seektalent.runtime.orchestrator",
    "seektalent.source_adapters",
)
RUNTIME_IMPORT_MESSAGES = {
    "seektalent.providers": "runtime must not import seektalent.providers",
    "seektalent.clients.cts_client": "runtime must not import CTS client",
    "seektalent.sources.cts": "runtime must not import concrete source implementation",
    "seektalent.sources.liepin": "runtime must not import concrete source implementation",
    "seektalent.sources": "runtime must not import source implementation layer",
    "seektalent.source_adapters": "runtime must not import concrete source adapter",
}
CONCRETE_SOURCE_IDS = {"cts", "liepin"}
DISPATCH_TARGET_WORDS = ("runner", "adapter", "builder", "dispatch", "source")
SOURCE_KIND_RE = re.compile(r"SourceKind\s*=\s*Literal\[\s*['\"]cts['\"]\s*,\s*['\"]liepin['\"]\s*\]")
SOURCE_BRANCH_RE = re.compile(r"\bif\s+[\w.]*source[\w.]*\s*==\s*['\"](?:cts|liepin)['\"]")
OPENCLI_RE = re.compile(r"liepin_opencli|opencli", re.IGNORECASE)
RUNTIME_SOURCE_SPECIFIC_NAME_RE = re.compile(
    r"\b(?:CTSQuery|CTSQueryBuildInput|build_cts_query|search_cts|cts_queries|cts_exhausted|"
    r"cts_base_url|cts_timeout_seconds|cts_spec_path|cts_credentials_configured|cts_raw_hits)\b"
)
FAILURE_LINE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<message> .+)$")


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_module_or_child(module_name: str, forbidden_module: str) -> bool:
    return module_name == forbidden_module or module_name.startswith(f"{forbidden_module}.")


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_set(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return {value for item in node.elts if (value := _string_literal(item)) is not None}
    value = _string_literal(node)
    return {value} if value is not None else set()


def _name_contains_source(value: str) -> bool:
    lowered = value.lower()
    return "source" in lowered or "provider_name" in lowered


def _name_contains_dispatch(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in DISPATCH_TARGET_WORDS)


def _expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _expr_name(node.value)
    if isinstance(node, ast.Call):
        return _expr_name(node.func)
    return ""


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in _target_names(item)]
    return []


def _failure_sort_key(message: str) -> tuple[str, int, str]:
    match = FAILURE_LINE_RE.match(message)
    if match is None:
        return (message, 0, "")
    return (match.group("path"), int(match.group("line")), match.group("message"))


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


def _runtime_import_failures(*, project_root: Path, path: Path) -> list[str]:
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
            for forbidden in FORBIDDEN_RUNTIME_IMPORTS:
                if _is_module_or_child(module_name, forbidden):
                    failures.append(f"{relative_path}:{line_no}: {RUNTIME_IMPORT_MESSAGES[forbidden]}")
                    break
    return failures


def _comparison_has_concrete_source(node: ast.Compare) -> bool:
    operands = [node.left, *node.comparators]
    names = {_expr_name(operand) for operand in operands}
    source_named = any(_name_contains_source(name) for name in names)
    if not source_named:
        return False
    operators = tuple(node.ops)
    if any(isinstance(operator, (ast.Eq, ast.NotEq)) for operator in operators):
        if any(_string_literal(operand) in CONCRETE_SOURCE_IDS for operand in operands):
            return True
    if any(isinstance(operator, (ast.In, ast.NotIn)) for operator in operators):
        if any(_string_set(operand) & CONCRETE_SOURCE_IDS for operand in operands):
            return True
    return False


def _dict_is_concrete_dispatch(node: ast.Dict, target_names: list[str]) -> bool:
    concrete_keys = {_string_literal(key) for key in node.keys if key is not None} & CONCRETE_SOURCE_IDS
    if not concrete_keys:
        return False
    if any(_name_contains_dispatch(name) for name in target_names):
        return True
    return any(isinstance(value, (ast.Name, ast.Attribute, ast.Lambda)) for value in node.values)


def _runtime_ast_failures(project_root: Path, path: Path) -> list[str]:
    relative_path = _relative(project_root, path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{relative_path}:{exc.lineno or 1}: syntax error prevents boundary scan"]

    failures: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and _comparison_has_concrete_source(node):
            failures.add(f"{relative_path}:{node.lineno}: runtime must not compare against concrete source ids")
        elif isinstance(node, ast.Match):
            for case in node.cases:
                pattern = case.pattern
                if isinstance(pattern, ast.MatchValue) and _string_literal(pattern.value) in CONCRETE_SOURCE_IDS:
                    line_no = getattr(pattern, "lineno", node.lineno)
                    failures.add(f"{relative_path}:{line_no}: runtime must not compare against concrete source ids")
        elif isinstance(node, ast.Assign):
            target_names = [name for target in node.targets for name in _target_names(target)]
            if isinstance(node.value, ast.Dict) and _dict_is_concrete_dispatch(node.value, target_names):
                failures.add(
                    f"{relative_path}:{node.lineno}: runtime must not dispatch through concrete source id maps"
                )
            for target_name in target_names:
                if target_name.startswith(("cts_", "liepin_")):
                    failures.add(
                        f"{relative_path}:{node.lineno}: "
                        "runtime must not contain source-specific runtime budget/detail/reason leakage"
                    )
            if _string_set(node.value) == CONCRETE_SOURCE_IDS:
                failures.add(
                    f"{relative_path}:{node.lineno}: "
                    "runtime must not contain source-specific runtime budget/detail/reason leakage"
                )
        elif isinstance(node, ast.AnnAssign):
            target_names = _target_names(node.target)
            for target_name in target_names:
                if target_name.startswith(("cts_", "liepin_")):
                    failures.add(
                        f"{relative_path}:{node.lineno}: "
                        "runtime must not contain source-specific runtime budget/detail/reason leakage"
                    )
            if node.value is not None and _string_set(node.value) == CONCRETE_SOURCE_IDS:
                failures.add(
                    f"{relative_path}:{node.lineno}: "
                    "runtime must not contain source-specific runtime budget/detail/reason leakage"
                )
        elif isinstance(node, ast.Subscript):
            if _string_literal(node.slice) in CONCRETE_SOURCE_IDS and _name_contains_dispatch(_expr_name(node.value)):
                failures.add(
                    f"{relative_path}:{node.lineno}: runtime must not index source plans by concrete source id"
                )
        elif isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and _string_literal(node.args[0]) in CONCRETE_SOURCE_IDS
                and _name_contains_dispatch(_expr_name(node.func.value))
            ):
                failures.add(
                    f"{relative_path}:{node.lineno}: runtime must not index source plans by concrete source id"
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "_cts_" in node.name or "_liepin_" in node.name:
                failures.add(
                    f"{relative_path}:{node.lineno}: "
                    "runtime must not contain source-specific runtime budget/detail/reason leakage"
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "RuntimeApprovedDetailLease currently supports only liepin" in node.value:
                failures.add(
                    f"{relative_path}:{node.lineno}: "
                    "runtime must not contain source-specific runtime budget/detail/reason leakage"
                )
    return sorted(failures, key=_failure_sort_key)


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
        if RUNTIME_SOURCE_SPECIFIC_NAME_RE.search(line):
            failures.append(
                f"{relative_path}:{line_no}: runtime must not contain source-specific runtime budget/detail/reason leakage"
            )
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


def _runtime_control_import_failures(project_root: Path, path: Path) -> list[str]:
    relative_path = _relative(project_root, path)
    failures: list[str] = []
    failures.extend(
        _import_failures(
            project_root=project_root,
            path=path,
            forbidden_modules=FORBIDDEN_RUNTIME_CONTROL_SERVICE_IMPORTS,
            message="runtime-control service modules must not import seektalent.providers",
        )
    )
    if path.name != "executor.py":
        failures.extend(
            _import_failures(
                project_root=project_root,
                path=path,
                forbidden_modules=FORBIDDEN_RUNTIME_CONTROL_NON_EXECUTOR_IMPORTS,
                message="only runtime-control executor adapter may import WorkflowRuntime",
            )
        )
        for index, failure in enumerate(failures):
            if failure.endswith("only runtime-control executor adapter may import WorkflowRuntime") and "source_adapters" in (
                path.read_text(encoding="utf-8").splitlines()[int(failure.split(":", 2)[1]) - 1]
            ):
                failures[index] = f"{relative_path}:{failure.split(':', 2)[1]}: only runtime-control executor adapter may import source adapters"
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
    if "seektalent.sources" in runtime_dependencies:
        failures.append("tach.toml: seektalent.runtime must not depend on seektalent.sources")
    if "seektalent.providers" in runtime_dependencies:
        failures.append("tach.toml: seektalent.runtime must not depend on seektalent.providers")
    return failures


def collect_source_boundary_failures(project_root: Path = PROJECT_ROOT) -> list[str]:
    failures: list[str] = []
    runtime_root = project_root / RUNTIME_ROOT
    providers_root = project_root / PROVIDERS_ROOT
    sources_root = project_root / SOURCES_ROOT
    runtime_control_root = project_root / RUNTIME_CONTROL_ROOT

    for path in _python_files(runtime_root):
        failures.extend(_runtime_import_failures(project_root=project_root, path=path))
        failures.extend(_runtime_text_failures(project_root, path))
        failures.extend(_runtime_ast_failures(project_root, path))

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

    for path in _python_files(runtime_control_root):
        failures.extend(_runtime_control_import_failures(project_root, path))

    failures.extend(_tach_boundary_failures(project_root))
    return sorted(failures, key=_failure_sort_key)


def main() -> int:
    failures = collect_source_boundary_failures(PROJECT_ROOT)
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
