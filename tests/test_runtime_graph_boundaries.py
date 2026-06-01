from __future__ import annotations

from seektalent_ui.runtime_graph_sections import safe_natural_text, section_from_facts


def test_runtime_graph_sections_redact_sensitive_values() -> None:
    text = safe_natural_text(
        {
            "summary": "第 1 轮完成。",
            "token": "secret-token",
            "counts": {"returned": 3},
            "artifact_path": "/Users/frank/private.json",
        }
    )

    assert "第 1 轮完成" in text
    assert "returned=3" in text
    assert "secret-token" not in text
    assert "/Users/frank" not in text


def test_runtime_graph_sections_omit_empty_fact_values() -> None:
    section = section_from_facts("查询", [("关键词", "Python"), ("空值", ""), ("无值", None)])

    assert section.heading == "查询"
    assert section.kind == "facts"
    assert [(fact.label, fact.value) for fact in section.facts] == [("关键词", "Python")]
