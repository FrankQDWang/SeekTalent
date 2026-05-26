from __future__ import annotations

from seektalent_ui.runtime_graph import safe_natural_text, section_from_facts


def test_safe_natural_text_serializes_nested_business_values() -> None:
    text = safe_natural_text(
        {
            "hard_constraints": {"location": "上海", "age": "30-40"},
            "must_have_capabilities": ["Python 后端", "分布式系统"],
            "empty": [],
            "none": None,
        }
    )

    assert "hard_constraints：location=上海；age=30-40" in text
    assert "must_have_capabilities：Python 后端、分布式系统" in text
    assert "empty" not in text
    assert "none" not in text


def test_safe_natural_text_redacts_technical_and_secret_fields() -> None:
    text = safe_natural_text(
        {
            "runtimeRunId": "run_secret",
            "cookie": "secret-cookie",
            "artifact_path": "/Users/frank/private.json",
            "summary": "第 1 轮完成。",
            "counts": {"topPoolCount": 10},
        }
    )

    assert "第 1 轮完成" in text
    assert "topPoolCount=10" in text
    assert "run_secret" not in text
    assert "secret-cookie" not in text
    assert "/Users/frank" not in text


def test_section_from_facts_omits_empty_values() -> None:
    section = section_from_facts(
        "评分",
        [
            ("进入评分", "18 人"),
            ("空值", ""),
            ("无值", None),
        ],
    )

    assert section.heading == "评分"
    assert section.kind == "facts"
    assert [(fact.label, fact.value) for fact in section.facts] == [("进入评分", "18 人")]
