from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_liepin_opencli_browser import EvalCommands, FakeCommands, _runner


def test_search_liepin_cards_uses_other_city_picker_for_expected_city(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[22]<label>上海</label>
[23]<span>其他</span>
王** 男 34岁 工作5年 硕士 上海
"""
    state_city_picker = """
[60]<input role=combobox placeholder=搜索城市 />
"""
    state_city_search_results = """
[60]<input role=combobox placeholder=搜索城市 />
[61]<div>江苏 · <span>苏州</span></div>
"""
    state_after_expected_city = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[22]<label>上海</label>
[23]<label>其他</label>
[50]<label title=期望城市 />
  <span>苏州</span>
王** 男 34岁 工作5年 硕士 苏州
求职期望：苏州 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_city_picker,
                state_city_search_results,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "60", "苏州"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "61"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "苏州"}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "23") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "苏州") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "61") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "fill_native_city_filter_search",
        "filter": "city",
        "value": "苏州",
        "ok": True,
    } in trace["events"]
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "expected",
        "value": "苏州",
        "ok": True,
    } in trace["events"]


def test_search_liepin_cards_selects_hot_city_from_other_city_picker(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[22]<label>上海</label>
[23]<label>其他</label>
王** 男 34岁 工作5年 硕士 上海
"""
    state_city_picker = """
[60]<input role=combobox placeholder=搜索城市 />
[61]<label>苏州</label>
"""
    state_after_expected_city = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[22]<label>上海</label>
[23]<label>其他</label>
[50]<label title=期望城市 />
  <span>苏州</span>
王** 男 34岁 工作5年 硕士 苏州
求职期望：苏州 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_city_picker,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "61"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "苏州"}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "23") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "61") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "苏州") not in commands.calls


def test_search_liepin_cards_selects_overseas_expected_city_from_city_picker(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[21]<label>美国</label>
[22]<label>北京</label>
[23]<label>上海</label>
王** 男 34岁 工作5年 硕士 上海
"""
    state_after_quick_city_unapplied = state_after_search
    state_city_picker = """
<span>请选择城市</span>
[60]<input autocomplete=off placeholder=搜索城市 type=text />
[61]<div>国内</div>
[62]<div>海外</div>
[63]<span>热门城市</span>
"""
    state_city_search_empty = """
<span>请选择城市</span>
[60]<input autocomplete=off placeholder=搜索城市 type=text value=美国 />
[61]<div>国内</div>
[62]<div>海外</div>
没找到相关匹配项
"""
    state_overseas_picker = """
<span>请选择城市</span>
[60]<input autocomplete=off placeholder=搜索城市 type=text value=美国 />
[61]<div>国内</div>
[62]<div>海外</div>
[69]<span>热门国家</span>
[70]<span>美国</span>
[71]<span>加拿大</span>
"""
    state_after_us_selected = """
<span>请选择城市</span>
[60]<input autocomplete=off placeholder=搜索城市 type=text value=美国 />
[62]<div>海外</div>
[70]<span>美国</span>
<i>已选（1/9）</i>
[90]<span>北美洲·美国</span>
[91]<button />
  <span>确认</span>
"""
    state_after_expected_city = """
[20]<label>期望城市：</label>
[21]<label>美国</label>
[22]<label>北京</label>
[23]<label>上海</label>
[24]<label>其他</label>
[50]<label title=期望城市 />
  <span>美国</span>
王** 男 34岁 工作5年 硕士 美国-洛杉矶
求职期望：美国 CFO/财务VP
某美国公司 · CFO首席财务官 2021.01-至今
"""
    commands = EvalCommands(
        eval_output='"24"',
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_after_quick_city_unapplied,
                state_after_quick_city_unapplied,
                state_city_picker,
                state_city_search_empty,
                state_overseas_picker,
                state_after_us_selected,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "CFO 首席财务官"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "24"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "60", "美国"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "62"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "91"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="CFO 首席财务官",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "美国"}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "21") in commands.calls
    assert any(call[:4] == ("opencli", "browser", "seektalent-liepin", "eval") for call in commands.calls)
    assert ("opencli", "browser", "seektalent-liepin", "click", "24") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "美国") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "62") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "70") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "91") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "confirm_native_city_filter",
        "filter": "city",
        "section": "expected",
        "value": "美国",
        "ok": True,
    } in trace["events"]


@pytest.mark.parametrize(("city_name", "city_ref"), [("苏州", "74"), ("宁波", "75")])
def test_search_liepin_cards_uses_visible_current_city_when_expected_city_missing(
    tmp_path: Path, city_name: str, city_ref: str
) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
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
王** 男 34岁 工作5年 硕士 上海
"""
    state_after_current_city = f"""
已选 目前城市{city_name}
<span>目前城市：</span>
[70]<span>不限</span>
[{city_ref}]<label>{city_name}</label>
<span>期望城市：</span>
[76]<span>不限</span>
[78]<label>上海</label>
王** 男 34岁 工作5年 硕士 {city_name}
求职期望：{city_name} 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_after_current_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", city_ref): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": city_name}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", city_ref) in commands.calls
    assert (
        "opencli",
        "browser",
        "seektalent-liepin",
        "click",
        "--role",
        "button",
        "--name",
        "期望城市",
    ) not in commands.calls
