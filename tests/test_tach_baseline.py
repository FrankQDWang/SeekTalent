import json
from pathlib import Path
import tomllib

from tools.check_tach_baseline import compare_violations, extract_failures, normalize_failure


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_normalize_failure_removes_line_numbers() -> None:
    assert (
        normalize_failure("[FAIL] src/a.py:123: Cannot use x")
        == "[FAIL] src/a.py: Cannot use x"
    )


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
    payload = tomllib.loads((PROJECT_ROOT / "tach.toml").read_text(encoding="utf-8"))
    dependencies_by_module = {module["path"]: module.get("depends_on", []) for module in payload["modules"]}

    assert "seektalent.sources" in dependencies_by_module
    assert "seektalent.sources" in dependencies_by_module["seektalent.providers"]
    assert "seektalent.sources" in dependencies_by_module["seektalent.runtime"]
    assert "seektalent.runtime" not in dependencies_by_module["seektalent.providers"]
    assert "seektalent.providers" not in dependencies_by_module["seektalent.runtime"]
