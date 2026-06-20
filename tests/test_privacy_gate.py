from tools.check_privacy_gate import AddedLine, check_added_lines, parse_added_lines


def test_privacy_gate_flags_sensitive_log_output() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 10, "logger.info(response.text)"),
            AddedLine("src/seektalent/providers/example.py", 11, 'logger.warning("token=%s", access_token)'),
        ]
    )

    assert [finding.rule_id for finding in findings] == ["sensitive-log-output", "sensitive-log-output"]


def test_privacy_gate_flags_provider_payload_and_header_log_output() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/providers/example.py", 12, 'logger.info("provider=%s", provider_response)'),
            AddedLine("src/seektalent/providers/example.py", 13, 'logger.debug("payload=%s", raw_payload)'),
            AddedLine("src/seektalent/providers/example.py", 14, 'logger.info("headers=%s", response.headers)'),
            AddedLine("src/seektalent/providers/example.py", 15, 'logger.info("provider summary redacted")'),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "sensitive-log-output",
        "sensitive-log-output",
        "sensitive-log-output",
    ]


def test_privacy_gate_flags_exception_detail_exposure() -> None:
    findings = check_added_lines(
        [
            AddedLine(
                "src/seektalent_ui/runtime_bridge.py",
                20,
                "raise HTTPException(status_code=500, detail=str(exc))",
            )
        ]
    )

    assert [finding.rule_id for finding in findings] == ["exception-detail-exposure"]


def test_privacy_gate_flags_raw_sensitive_payload_keys() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 30, '"raw_provider_payload": raw_payload,'),
            AddedLine("src/seektalent/runtime/example.py", 31, '"raw_resume": resume_text,'),
            AddedLine("src/seektalent/runtime/example.py", 32, '"raw_payload": payload,'),
            AddedLine("src/seektalent/runtime/example.py", 33, '"auth_headers": headers,'),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "raw-sensitive-payload-key",
        "raw-sensitive-payload-key",
        "raw-sensitive-payload-key",
        "raw-sensitive-payload-key",
    ]


def test_privacy_gate_allows_redacted_summaries() -> None:
    findings = check_added_lines(
        [
            AddedLine("src/seektalent/runtime/example.py", 40, 'logger.info("provider payload redacted")'),
            AddedLine("src/seektalent/runtime/example.py", 41, '"raw_provider_payload_redacted": True,'),
            AddedLine("src/seektalent/runtime/example.py", 42, '"token_hash": token_hash,'),
        ]
    )

    assert findings == []


def test_privacy_gate_ignores_tests_and_generated_paths() -> None:
    findings = check_added_lines(
        [
            AddedLine("tests/test_example.py", 50, "logger.info(response.text)"),
            AddedLine("apps/web-react/tests/example.test.ts", 51, "logger.info(response.text)"),
            AddedLine("docs/superpowers/plan.md", 52, "logger.info(response.text)"),
        ]
    )

    assert findings == []


def test_parse_added_lines_tracks_new_line_numbers() -> None:
    diff = """diff --git a/src/seektalent/runtime/example.py b/src/seektalent/runtime/example.py
--- a/src/seektalent/runtime/example.py
+++ b/src/seektalent/runtime/example.py
@@ -4,0 +5,2 @@
+logger.info(response.text)
+safe_line()
"""

    assert parse_added_lines(diff) == [
        AddedLine("src/seektalent/runtime/example.py", 5, "logger.info(response.text)"),
        AddedLine("src/seektalent/runtime/example.py", 6, "safe_line()"),
    ]
