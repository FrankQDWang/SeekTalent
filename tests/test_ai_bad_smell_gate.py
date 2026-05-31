from tools.check_ai_bad_smells import AddedLine, check_added_lines, parse_added_lines


def test_ai_bad_smell_gate_flags_broad_exception_handlers() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 10, "except Exception as exc:"),
            AddedLine("src/seektalent/providers/example.py", 11, "except:"),
        ],
        changed_paths=["src/seektalent/runtime/example.py"],
    )

    assert [finding.rule_id for finding in findings] == [
        "broad-exception-handler",
        "broad-exception-handler",
    ]


def test_ai_bad_smell_gate_flags_type_escape_hatches() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 20, "from typing import Any"),
            AddedLine("src/seektalent/runtime/example.py", 21, "payload: Any = {}"),
            AddedLine("src/seektalent/runtime/example.py", 22, "value = cast(dict[str, str], payload)"),
            AddedLine("src/seektalent/runtime/example.py", 23, "return payload  # type: ignore[return-value]"),
            AddedLine("src/seektalent/runtime/example.py", 24, "payloads: list[dict[str, Any]] = []"),
        ],
        changed_paths=["src/seektalent/runtime/example.py"],
    )

    assert [finding.rule_id for finding in findings] == [
        "typing-any",
        "typing-any",
        "typing-cast",
        "type-ignore",
        "typing-any",
    ]


def test_ai_bad_smell_gate_flags_noqa_suppressions_without_false_positive() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 26, "import unused  # noqa: F401"),
            AddedLine("src/seektalent/runtime/example.py", 27, 'text = "sequoia"'),
        ],
        changed_paths=["src/seektalent/runtime/example.py"],
    )

    assert [finding.rule_id for finding in findings] == ["ruff-noqa"]


def test_ai_bad_smell_gate_flags_import_path_mutation_without_string_false_positive() -> None:
    findings = check_added_lines(
        [
            AddedLine("scripts/local_setup.py", 28, "sys.path.insert(0, str(PROJECT_ROOT))"),
            AddedLine("tools/bootstrap.py", 29, "sys.path.append(str(ROOT))"),
            AddedLine("src/seektalent/runtime/example.py", 30, 'text = "sys.path.insert(0, root)"'),
        ],
        changed_paths=[
            "scripts/local_setup.py",
            "tools/bootstrap.py",
            "src/seektalent/runtime/example.py",
        ],
    )

    assert [finding.rule_id for finding in findings] == [
        "import-path-mutation",
        "import-path-mutation",
    ]


def test_ai_bad_smell_gate_flags_fallback_without_tests() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 30, "return fallback_result"),
            AddedLine("src/seektalent/runtime/example.py", 31, 'logger.info("best effort parse succeeded")'),
            AddedLine("src/seektalent/runtime/example.py", 32, "backup_path = path.with_suffix('.bak')"),
        ],
        changed_paths=["src/seektalent/runtime/example.py"],
    )

    assert [finding.rule_id for finding in findings] == [
        "untested-fallback-path",
        "untested-fallback-path",
        "untested-fallback-path",
    ]


def test_ai_bad_smell_gate_allows_fallback_when_tests_change() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 40, "return fallback_result"),
        ],
        changed_paths=[
            "src/seektalent/runtime/example.py",
            "tests/test_runtime_example.py",
        ],
    )

    assert findings == []


def test_ai_bad_smell_gate_ignores_tests_and_generated_paths() -> None:
    findings = check_added_lines(
        [
            AddedLine("tests/test_example.py", 50, "except Exception as exc:"),
            AddedLine("apps/liepin-worker/tests/example.test.ts", 51, "value = cast(dict, payload)"),
            AddedLine("docs/superpowers/plan.md", 52, "best effort fallback"),
        ],
        changed_paths=["tests/test_example.py"],
    )

    assert findings == []


def test_parse_added_lines_tracks_new_line_numbers() -> None:
    diff = """diff --git a/src/seektalent/runtime/example.py b/src/seektalent/runtime/example.py
--- a/src/seektalent/runtime/example.py
+++ b/src/seektalent/runtime/example.py
@@ -4,0 +5,2 @@
+except Exception as exc:
+safe_line()
"""

    assert parse_added_lines(diff) == [
        AddedLine("src/seektalent/runtime/example.py", 5, "except Exception as exc:"),
        AddedLine("src/seektalent/runtime/example.py", 6, "safe_line()"),
    ]
