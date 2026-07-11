import json
from pathlib import Path
import tomllib

from tools.check_tach_baseline import compare_violations, extract_failures, normalize_failure


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_BOUNDARY_MODULES = {
    "seektalent.runtime",
    "seektalent.source_contracts",
    "seektalent.source_adapters",
    "seektalent.sources",
    "seektalent.providers",
    "seektalent.opencli_browser",
}


def _dependencies_by_module() -> dict[str, list[str]]:
    payload = tomllib.loads((PROJECT_ROOT / "tach.toml").read_text(encoding="utf-8"))
    return {module["path"]: list(module.get("depends_on", [])) for module in payload["modules"]}


def _source_boundary_cycles(graph: dict[str, list[str]]) -> list[tuple[str, ...]]:
    cycles: set[tuple[str, ...]] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in path:
            cycle = (*path[path.index(node) :], node)
            if any(item in SOURCE_BOUNDARY_MODULES for item in cycle):
                cycles.add(cycle)
            return
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency, (*path, node))

    for module in graph:
        visit(module, ())
    return sorted(cycles)


def test_normalize_failure_removes_line_numbers() -> None:
    assert normalize_failure("[FAIL] src/a.py:123: Cannot use x") == "[FAIL] src/a.py: Cannot use x"


def test_extract_failures_keeps_only_fail_lines() -> None:
    output = """Configuration
[WARN] ignored
Internal Dependencies
[FAIL] src/a.py:1: Cannot use x
[FAIL] src/b.py:2: Cannot use y
"""

    assert extract_failures(output) == [
        "[FAIL] src/a.py: Cannot use x",
        "[FAIL] src/b.py: Cannot use y",
    ]


def test_compare_violations_fails_on_new_failure() -> None:
    result = compare_violations(
        current=["[FAIL] src/a.py: Cannot use x", "[FAIL] src/b.py: Cannot use y"],
        baseline=["[FAIL] src/a.py: Cannot use x"],
    )

    assert result == ["[FAIL] src/b.py: Cannot use y"]


def test_tach_baseline_has_no_accepted_failures() -> None:
    payload = json.loads((PROJECT_ROOT / "tools/tach_baseline.json").read_text(encoding="utf-8"))

    assert payload["accepted_failures"] == []


def test_tach_config_disallows_runtime_provider_dual_dependency() -> None:
    dependencies_by_module = _dependencies_by_module()

    assert "seektalent.sources" in dependencies_by_module
    assert "seektalent.source_contracts" in dependencies_by_module["seektalent.sources"]
    assert "seektalent.source_contracts" in dependencies_by_module["seektalent.providers"]
    assert "seektalent.sources" not in dependencies_by_module["seektalent.providers"]
    assert "seektalent.sources" not in dependencies_by_module["seektalent.runtime"]
    assert "seektalent.runtime" not in dependencies_by_module["seektalent.providers"]
    assert "seektalent.providers" not in dependencies_by_module["seektalent.runtime"]
    assert "seektalent.runtime" in dependencies_by_module["seektalent.source_adapters"]
    assert "seektalent.sources" in dependencies_by_module["seektalent.source_adapters"]
    assert "seektalent.providers" in dependencies_by_module["seektalent.source_adapters"]


def test_tach_config_has_no_runtime_source_provider_cycle() -> None:
    cycles = _source_boundary_cycles(_dependencies_by_module())

    assert cycles == []


def test_tach_config_models_opencli_browser_package_boundary() -> None:
    dependencies_by_module = _dependencies_by_module()

    assert "seektalent.opencli_browser" in dependencies_by_module
    assert "seektalent.opencli_browser" in dependencies_by_module["seektalent.providers"]
    assert "seektalent.providers" not in dependencies_by_module["seektalent.opencli_browser"]
    assert "seektalent.sources" not in dependencies_by_module["seektalent.opencli_browser"]
    assert "seektalent.runtime" not in dependencies_by_module["seektalent.opencli_browser"]
    assert "seektalent.source_adapters" not in dependencies_by_module["seektalent.opencli_browser"]


def test_tach_config_governs_shared_candidate_quality_policy() -> None:
    dependencies_by_module = _dependencies_by_module()

    assert dependencies_by_module["seektalent.candidate_quality"] == []
    for consumer in (
        "seektalent.candidate_feedback",
        "seektalent.reflection",
        "seektalent.scoring",
        "seektalent.runtime",
        "seektalent_ui",
        "seektalent_workbench_v2",
    ):
        assert "seektalent.candidate_quality" in dependencies_by_module[consumer]
