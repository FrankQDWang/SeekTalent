from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from seektalent.providers.liepin.browser_boundary_patterns import (
    BOUNDARY_PATTERN_DECLARATION_PATHS,
    PYTHON_FORBIDDEN_IMPORTS,
    PYTHON_FORBIDDEN_OPERATION_MARKERS,
)

_PYTHON_SCAN_ROOTS = (
    Path("src/seektalent/providers/liepin"),
)
_LOCAL_WORKER_TRANSPORT_FILES = {
    "src/seektalent/providers/liepin/client.py",
    "src/seektalent/providers/liepin/worker_runtime.py",
}
_FORBIDDEN_IMPORTS = set(PYTHON_FORBIDDEN_IMPORTS)
_FORBIDDEN_OPERATION_MARKERS: tuple[str, ...] = tuple(
    sorted(PYTHON_FORBIDDEN_OPERATION_MARKERS, key=lambda marker: len(marker), reverse=True)
)
_CALL_MARKERS_WITH_FIRST_ARG = {
    "page.on": {
        "request": "page.on(request)",
        "response": "page.on(response)",
    },
}


def collect_python_boundary_scan_files(root: Path = Path(".")) -> dict[str, str]:
    files: dict[str, str] = {}
    for scan_root in _PYTHON_SCAN_ROOTS:
        absolute_root = root / scan_root
        if not absolute_root.exists():
            continue
        for path in absolute_root.rglob("*.py"):
            relative_path = path.relative_to(root).as_posix()
            if relative_path in BOUNDARY_PATTERN_DECLARATION_PATHS:
                continue
            if relative_path in _LOCAL_WORKER_TRANSPORT_FILES:
                continue
            files[relative_path] = path.read_text(encoding="utf-8")
    return files


def find_forbidden_python_boundary_patterns(files: dict[str, str]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for path, text in files.items():
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            findings.append((path, f"syntax_error:{exc.lineno}"))
            continue
        findings.extend(_scan_tree(path, tree))
    return _dedupe_findings(findings)


def _scan_tree(path: str, tree: ast.AST) -> list[tuple[str, str]]:
    scanner = _PythonBoundaryScanner(path)
    scanner.visit(tree)
    return scanner.findings


def _dedupe_findings(findings: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for finding in findings:
        if finding in seen:
            continue
        seen.add(finding)
        deduped.append(finding)
    return deduped


class _PythonBoundaryScanner(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[tuple[str, str]] = []
        self.aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            marker = _forbidden_import_marker(alias.name)
            if marker:
                self._add(marker)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        if module == "urllib":
            for alias in node.names:
                if alias.name == "request":
                    self._add("urllib.request")
        elif module.startswith("playwright."):
            for alias in node.names:
                if alias.name == "APIRequestContext":
                    self._add("APIRequestContext")
        else:
            marker = _forbidden_import_marker(module)
            if marker:
                self._add(marker)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        marker = _forbidden_marker_for_expression(node.value)
        alias_value = marker or _attribute_chain(node.value)
        if alias_value:
            if marker:
                self._add(marker)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases[target.id] = alias_value
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            marker = _forbidden_marker_for_expression(node.value)
            alias_value = marker or _attribute_chain(node.value)
            if alias_value:
                if marker:
                    self._add(marker)
                if isinstance(node.target, ast.Name):
                    self.aliases[node.target.id] = alias_value
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        marker = _forbidden_marker_for_call(node, self.aliases)
        if marker:
            self._add(marker)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        marker = _forbidden_marker_for_expression(node)
        if marker:
            self._add(marker)
        self.generic_visit(node)

    def _add(self, marker: str) -> None:
        self.findings.append((self.path, marker))


def _forbidden_import_marker(module: str) -> str | None:
    if module in _FORBIDDEN_IMPORTS:
        return module
    for forbidden in _FORBIDDEN_IMPORTS:
        if module.startswith(f"{forbidden}."):
            return forbidden
    return None


def _forbidden_marker_for_call(
    node: ast.Call,
    aliases: dict[str, str],
) -> str | None:
    call_chain = _expand_alias_chain(_attribute_chain(node.func), aliases)
    if call_chain in _CALL_MARKERS_WITH_FIRST_ARG:
        first_arg = node.args[0] if node.args else None
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            marker = _CALL_MARKERS_WITH_FIRST_ARG[call_chain].get(first_arg.value)
            if marker:
                return marker

    return _forbidden_marker_for_chain(call_chain)


def _forbidden_marker_for_expression(expression: ast.AST) -> str | None:
    chain = _attribute_chain(expression)
    return _forbidden_marker_for_chain(chain)


def _forbidden_marker_for_chain(chain: str | None) -> str | None:
    if not chain:
        return None
    for marker in _FORBIDDEN_OPERATION_MARKERS:
        if _chain_matches_marker(chain, marker):
            return marker
    return None


def _chain_matches_marker(chain: str, marker: str) -> bool:
    return chain == marker or chain.startswith(f"{marker}.")


def _attribute_chain(expression: ast.AST) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        owner = _attribute_chain(expression.value)
        if owner:
            return f"{owner}.{expression.attr}"
    if isinstance(expression, ast.Subscript):
        owner = _attribute_chain(expression.value)
        key = _string_subscript_key(expression.slice)
        if owner and key:
            return f"{owner}.{key}"
    return None


def _string_subscript_key(expression: ast.AST) -> str | None:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    return None


def _expand_alias_chain(chain: str | None, aliases: dict[str, str]) -> str | None:
    if not chain:
        return None
    root, _, suffix = chain.partition(".")
    if root not in aliases:
        return chain
    aliased = aliases[root]
    return f"{aliased}.{suffix}" if suffix else aliased


def main() -> int:
    findings = find_forbidden_python_boundary_patterns(collect_python_boundary_scan_files())
    for path, marker in findings:
        print(f"{path}: forbidden Liepin browser boundary operation {marker}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
