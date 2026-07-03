from __future__ import annotations

import pytest

from seektalent.providers.liepin.opencli_filter_planning import (
    liepin_filter_actions,
    native_filter_city_search_input_ref,
    native_filter_control_ref_in_section,
    native_filter_option_ref_in_section,
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


def test_liepin_filter_selection_detects_checked_option_in_section() -> None:
    state_text = """
    <span>期望城市：</span>
    [22]<label class=ant-checkbox-wrapper ant-checkbox-wrapper-checked>上海</label>
    <span>工作年限：</span>
    """

    assert native_filter_selection_applied(state_text, section="expected", label="上海") is True


def test_liepin_filter_selection_detects_section_summary_value() -> None:
    state_text = """
    <span>期望城市：上海</span>
    <span>工作年限：</span>
    """

    assert native_filter_selection_applied(state_text, section="expected", label="上海") is True


def test_liepin_filter_planning_uses_other_city_picker_for_secondary_city() -> None:
    state_text = """
    [20]<label>期望城市：</label>
    [21]<label>北京</label>
    [22]<label>上海</label>
    [23]<label>其他</label>
    """

    assert native_filter_control_ref_in_section(state_text, section="expected") == "23"


def test_liepin_filter_planning_uses_span_other_city_picker_for_secondary_city() -> None:
    state_text = """
    <span>期望城市：</span>
    [87]<span>不限</span>
    [88]<label>北京</label>
    [89]<label>上海</label>
    [90]<label>佛山</label>
    [91]<label>西安</label>
    [92]<label>深圳</label>
    [93]<label>武汉</label>
    [94]<label>合肥</label>
    [95]<label>杭州</label>
    [96]<span>其他</span>
    <span>工作年限：</span>
    """

    assert native_filter_control_ref_in_section(state_text, section="expected") == "96"


def test_liepin_filter_planning_does_not_use_page_text_as_other_city_picker() -> None:
    state_text = """
    [20]<label>期望城市：</label>
    [21]<label>北京</label>
    [80]<a>其他项目经历</a>
    """

    assert native_filter_control_ref_in_section(state_text, section="expected") is None


def test_liepin_filter_planning_finds_city_option_after_city_search() -> None:
    state_text = """
    [20]<label>期望城市：</label>
    [21]<label>北京</label>
    [30]<label>工作年限：</label>
    [31]<label>3-5年</label>
    [60]<input role=combobox placeholder=搜索城市 />
    [61]<div>苏州工业园区</div>
    [62]<div>江苏 · <span>苏州</span></div>
    """

    assert native_filter_city_search_input_ref(state_text) == "60"
    assert native_filter_option_ref_in_section(state_text, section="expected", label="苏州") == "62"


def test_liepin_filter_planning_does_not_use_page_text_as_city_option() -> None:
    state_text = """
    [20]<label>期望城市：</label>
    [21]<label>北京</label>
    [30]<label>工作年限：</label>
    [31]<label>3-5年</label>
    [60]<input role=combobox placeholder=搜索城市 />
    [80]<a>求职期望：苏州 数据开发专家</a>
    """

    assert native_filter_option_ref_in_section(state_text, section="expected", label="苏州") is None


def test_liepin_filter_planning_prefers_exact_city_result() -> None:
    state_text = """
    [60]<input role=combobox placeholder=搜索城市 />
    [61]<div>江苏 · <span>苏州</span></div>
    [62]<div><span>苏州</span></div>
    """

    assert native_filter_option_ref_in_section(state_text, section="expected", label="苏州") == "62"


def test_liepin_filter_planning_prefers_final_whole_city_over_city_picker_navigation() -> None:
    state_text = """
    <span>请选择城市</span>
    [294]<input autocomplete=off placeholder=搜索城市 type=text />
    [298]<div>国内</div>
    <ul role=menu tabindex=0 />
    [302]<li role=menuitem tabindex=-1 />
      <span>上海</span>
    <p />
      [334]<span>热门城市</span>
      [335]<span>/</span>
      [336]<span>上海</span>
    <div />
      <ul />
        <li />
          [337]<span>全上海</span>
    <i>已选（0/9）</i>
    """

    assert native_filter_option_ref_in_section(state_text, section="expected", label="上海") == "337"


@pytest.mark.parametrize(("city_name", "city_ref"), [("苏州", "74"), ("宁波", "75")])
def test_liepin_filter_planning_uses_visible_city_from_other_city_section(city_name: str, city_ref: str) -> None:
    state_text = """
    <span>目前城市：</span>
    [70]<span>不限</span>
    [71]<label>北京</label>
    [72]<label>上海</label>
    [73]<label>广州</label>
    [74]<label>苏州</label>
    [75]<label>宁波</label>
    <span>期望城市：</span>
    [76]<span>不限</span>
    [77]<label>北京</label>
    [78]<label>上海</label>
    [79]<label>佛山</label>
    [80]<label>西安</label>
    [81]<label>深圳</label>
    <span>工作年限：</span>
    """

    assert native_filter_option_ref_in_section(state_text, section="expected", label=city_name) == city_ref


@pytest.mark.parametrize("city_name", ["苏州", "宁波"])
def test_liepin_filter_selection_accepts_applied_city_from_other_city_section(city_name: str) -> None:
    state_text = f"""
    已选 目前城市{city_name}
    <span>目前城市：</span>
    [74]<label>{city_name}</label>
    <span>期望城市：</span>
    [78]<label>上海</label>
    """

    assert native_filter_selection_applied(state_text, section="expected", label=city_name) is True
