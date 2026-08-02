from __future__ import annotations

from tools.check_agent_safety_gate import AddedLine, check_added_lines


def _line(path: str, text: str) -> AddedLine:
    return AddedLine(path=path, line_number=1, text=text)


def test_blocks_sensitive_runtime_trace_and_browser_storage() -> None:
    findings = check_added_lines(
        [
            _line("src/seektalent/runtime/example.py", "trace_payload['raw_resume'] = text"),
            _line(
                "apps/web-react/src/example.ts",
                "sessionStorage.setItem('token', token)",
            ),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "sensitive-runtime-trace",
        "sensitive-browser-storage",
    ]


def test_ignores_rule_definitions_tests_and_docs() -> None:
    lines = [
        _line(
            "tools/check_agent_safety_gate.py",
            r"trace.*(?:authorization|cookie|raw_resume|token)",
        ),
        _line("tests/test_example.py", "sessionStorage.setItem('token', token)"),
        _line("docs/example.md", "trace_payload['raw_resume'] = text"),
    ]

    assert check_added_lines(lines) == []


def test_allows_non_sensitive_product_state() -> None:
    lines = [
        _line("src/seektalent/runtime/example.py", "trace_payload['reason_code'] = reason"),
        _line(
            "apps/web-react/src/example.ts",
            "localStorage.setItem('pending-task', taskId)",
        ),
    ]

    assert check_added_lines(lines) == []
