from __future__ import annotations

from seektalent.providers.liepin.opencli_filter_planning import (
    liepin_filter_actions,
    native_filter_is_required,
    native_filter_selection_applied,
    skipped_liepin_filter_names,
)


def test_liepin_filter_planning_derives_legacy_range_labels() -> None:
    filters = {
        "experience": {"minYears": 5, "maxYears": 10},
        "age": {"min": 28, "max": 40},
        "degree": {"section": "education", "label": "本科"},
    }

    assert liepin_filter_actions(filters) == (
        ("experience", "legacy", "5-10年"),
        ("age", "legacy", "28-40岁"),
        ("degree", "education", "本科"),
    )


def test_liepin_filter_planning_handles_required_and_skipped_names() -> None:
    filters = {
        "city": "上海",
        "sourceTarget": "search",
        "unexpected": True,
        "requiredFilterNames": ["city"],
        "optionalFilterNames": ["age"],
    }

    assert native_filter_is_required(filters, "city") is True
    assert native_filter_is_required(filters, "age") is False
    assert skipped_liepin_filter_names(filters) == ("unexpected",)


def test_liepin_filter_selection_detects_selected_chip_in_section() -> None:
    state_text = """
    已选 期望城市北京 本科 统招
    """

    assert native_filter_selection_applied(state_text, section="recruitment_type", label="统招本科") is True
