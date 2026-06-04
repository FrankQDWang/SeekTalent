from __future__ import annotations

import json
from pathlib import Path

from tests.test_liepin_opencli_browser import FakeCommands, _runner


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
