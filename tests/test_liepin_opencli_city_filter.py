from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult
from seektalent.providers.liepin import liepin_city_picker as city_picker
from seektalent.providers.liepin.liepin_site_parsing import _liepin_city_picker_state_probe_script
from tests.test_liepin_opencli_browser import EvalCommands, FakeCommands, _runner


class SequenceEvalCommands(FakeCommands):
    def __init__(
        self,
        *,
        eval_outputs: list[str | BaseException],
        outputs: dict[tuple[str, ...], str | list[str]] | None = None,
    ) -> None:
        super().__init__(outputs=outputs)
        self.eval_outputs = eval_outputs

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: int,
        env: Mapping[str, str] | None = None,
    ) -> str:
        call = tuple(argv)
        if len(call) >= 4 and call[3] == "eval":
            script = call[4] if len(call) > 4 else ""
            if "seektalent.liepin_city_picker.v1" not in script:
                return super().run(argv, timeout=timeout, env=env)
            del timeout
            self.calls.append(call)
            self.envs.append(env)
            return self._resolve_output(self.eval_outputs)
        return super().run(argv, timeout=timeout, env=env)


def _closed_picker_probe(*, control_ref: str = "23") -> str:
    return json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": control_ref,
            "open": False,
            "searchValue": "",
            "candidates": [],
            "selectedCities": [],
            "confirmRefs": [],
        },
        ensure_ascii=False,
    )


def _read_action_trace(tmp_path: Path, source_run_id: str) -> dict[str, object]:
    paths = list(
        (tmp_path / "protected" / "pi-trace" / source_run_id).glob(
            "**/action-trace.json"
        )
    )
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_liepin_city_fill_rejects_an_explicitly_unverified_result() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "fill",
                "60",
                "常州",
            ): '{"filled":true,"verified":false}'
        }
    )

    with pytest.raises(OpenCliBrowserError) as raised:
        _runner(commands).fill(target="60", text="常州")

    assert raised.value.safe_reason_code == "liepin_opencli_fill_verification_failed"


def test_liepin_city_picker_uses_semantic_readiness_when_title_wait_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    state_picker_without_title = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text />
    """
    state_picker_loading = """
    <div aria-busy=true />
    SENTINEL_DOM_BODY_CANDIDATE_DATA
    """
    state_current_suggestion = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    <div class=suggest-list>
      [64]<div>中国 · <span>上海</span></div>
    </div>
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    [50]<label title=期望城市 />
      <span>上海</span>
    """
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_picker_loading,
                state_picker_without_title,
                state_current_suggestion,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "60", "上海"): (
                '{"filled":true,"verified":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    def unavailable_title_wait(_text: str) -> None:
        raise OpenCliBrowserError("liepin_opencli_status_unavailable")

    monkeypatch.setattr(runner, "_wait_for_text_condition", unavailable_title_wait)
    events: list[dict[str, object]] = []

    result = runner._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=events,
    )

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "上海") in commands.calls
    assert any(event.get("action_kind") == "observe_native_filter_menu" for event in events)
    assert "SENTINEL_DOM_BODY_CANDIDATE_DATA" not in json.dumps(events, ensure_ascii=False)


def test_liepin_city_picker_readiness_tolerates_open_probe_before_input_ref(
    tmp_path: Path,
) -> None:
    state_loading = "<div aria-busy=true />"
    probe_open_without_input = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchValue": "",
            "candidates": [],
            "selectedCities": [],
            "confirmRefs": [],
        },
        ensure_ascii=False,
    )
    probe_ready = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "",
            "candidates": [],
            "selectedCities": [],
            "confirmRefs": [],
        },
        ensure_ascii=False,
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_open_without_input,
            probe_ready,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_loading,
                state_loading,
                state_loading,
            ],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
        },
    )
    events: list[dict[str, object]] = []

    result = city_picker.observe_picker_ready(
        _runner(commands, lease_dir=tmp_path),
        section="expected",
        label="上海",
        events=events,
    )

    assert result.ok is True
    readiness = [
        event
        for event in events
        if event.get("phase") == "city_picker_readiness"
    ]
    assert [event["attempt"] for event in readiness] == [1, 2, 3]
    assert [event["reason"] for event in readiness] == [
        "city_picker_not_ready",
        "city_picker_probe_incomplete",
        "city_search_input_ready",
    ]


def test_liepin_city_picker_uses_focused_probe_when_state_omits_ready_modal(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    state_without_picker_semantics = """
    <div aria-busy=false />
    SENTINEL_DOM_BODY_CANDIDATE_DATA
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    [50]<label class=tag-item selected>上海</label>
    """
    probe_ready = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "",
            "candidates": [],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_current_suggestion = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_ready,
            probe_ready,
            probe_ready,
            probe_current_suggestion,
            probe_selected,
            probe_selected,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_without_picker_semantics,
                state_without_picker_semantics,
                state_without_picker_semantics,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "60", "上海"): (
                '{"filled":true,"verified":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)
    events: list[dict[str, object]] = []

    result = runner._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=events,
    )

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "上海") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "64") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "66") in commands.calls
    assert "SENTINEL_DOM_BODY_CANDIDATE_DATA" not in json.dumps(events, ensure_ascii=False)


def test_liepin_city_picker_prefers_focused_probe_control_over_ambiguous_state_ref(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    <div class=suggest-list>
      [64]<div>中国 · <span>上海</span></div>
    </div>
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    <span class=ant-tag-checkable-checked>上海</span>
    """
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(control_ref="24"),
            OpenCliBrowserError("liepin_opencli_status_unavailable"),
            OpenCliBrowserError("liepin_opencli_status_unavailable"),
            OpenCliBrowserError("liepin_opencli_status_unavailable"),
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                picker_state,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "24"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "click", "24") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "23") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "64") in commands.calls


@pytest.mark.parametrize(
    ("probe_output", "reason"),
    [
        (
            '{"schema_version":"seektalent.liepin_city_picker.v1",'
            '"section":"current","controlRef":"24","open":false}',
            "liepin_opencli_malformed_state",
        ),
        (
            '{"schema_version":"seektalent.liepin_city_picker.v1",'
            '"section":"expected","open":false,"searchValue":"",'
            '"candidates":[],"selectedCities":[],"confirmRefs":[]}',
            "liepin_opencli_filter_option_unavailable",
        ),
    ],
)
def test_liepin_city_picker_control_probe_fails_closed_without_exact_authority(
    tmp_path: Path,
    probe_output: str,
    reason: str,
) -> None:
    commands = EvalCommands(
        eval_output=probe_output,
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
        },
    )

    with pytest.raises(OpenCliBrowserError) as raised:
        _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
            filter_name="city",
            section="expected",
            label="上海",
            current_state=OpenCliBrowserResult(
                ok=True,
                action="state",
                private_output='<span>期望城市：</span>\n[23]<span>其他</span>',
            ),
            events=[],
        )

    assert raised.value.safe_reason_code == reason
    assert ("opencli", "browser", "seektalent-liepin", "click", "23") not in commands.calls


def test_liepin_city_picker_prefers_focused_probe_candidate_over_ambiguous_state_ref(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    state_without_picker_semantics = "<div aria-busy=false />"
    ambiguous_state_suggestion = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    [99]<div>上海</div>
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    [50]<label class=tag-item selected>上海</label>
    """
    probe_ready = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "",
            "candidates": [],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_current_suggestion = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_ready,
            probe_ready,
            probe_current_suggestion,
            probe_selected,
            probe_selected,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_without_picker_semantics,
                ambiguous_state_suggestion,
                state_without_picker_semantics,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "60", "上海"): (
                '{"filled":true,"verified":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "99"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "click", "64") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "99") not in commands.calls


def test_liepin_city_picker_adapter_cannot_bypass_focused_probe_with_ambiguous_state_ref(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    ambiguous_picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    [99]<div>上海</div>
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    <span class=ant-tag-checkable-checked>上海</span>
    """
    probe_candidate = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": [],
            "confirmRefs": [],
        },
        ensure_ascii=False,
    )
    commands = EvalCommands(
        eval_output=probe_candidate,
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                ambiguous_picker_state,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "99"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert any(len(call) >= 4 and call[3] == "eval" for call in commands.calls)
    assert ("opencli", "browser", "seektalent-liepin", "click", "64") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "99") not in commands.calls


def test_liepin_city_picker_confirms_probe_selected_city_without_clicking_candidate_again(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    selected_picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    [99]<div>上海</div>
    <i>已选（1/9）</i>
    [91]<button><span>确认</span></button>
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    <span class=ant-tag-checkable-checked>上海</span>
    """
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    commands = EvalCommands(
        eval_output=probe_selected,
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                selected_picker_state,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "99"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "click", "66") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "上海") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "64") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "99") not in commands.calls


def test_liepin_city_picker_probe_applies_domestic_whole_city_candidate(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    """
    state_after_selected = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    <i>已选（1/9）</i>
    [340]<span>全上海</span>
    [341]<button><span>确认</span></button>
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    <span class=ant-tag-checkable-checked>上海</span>
    """
    probe_candidate = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "337", "kind": "final", "label": "全上海"}],
            "selectedCities": [],
            "confirmRefs": ["341"],
        },
        ensure_ascii=False,
    )
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "337", "kind": "final", "label": "全上海"}],
            "selectedCities": ["全上海"],
            "confirmRefs": ["341"],
        },
        ensure_ascii=False,
    )
    commands = SequenceEvalCommands(
        eval_outputs=[_closed_picker_probe(), probe_candidate, probe_candidate, probe_selected],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                picker_state,
                state_after_selected,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "337"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "341"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "click", "337") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "341") in commands.calls


def test_liepin_city_picker_whole_city_match_does_not_accept_arbitrary_suffix() -> None:
    whole_city_payload: dict[str, object] = {"open": True, "selectedCities": ["全上海"]}
    district_payload: dict[str, object] = {"open": True, "selectedCities": ["苏州工业园区"]}

    assert city_picker.picker_selection_contains(whole_city_payload, label="上海") is True
    assert city_picker.picker_selection_contains(district_payload, label="苏州") is False


def test_liepin_city_picker_retry_reconciles_open_selected_picker_before_new_effect(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    """
    selected_picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    <i>已选（1/9）</i>
    [66]<button><span>确认</span></button>
    """
    state_after_expected_city = """
    <span>期望城市：</span>
    <span class=ant-tag-checkable-checked>上海</span>
    """
    probe_candidate = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    status_unavailable = subprocess.CalledProcessError(
        1,
        ["opencli"],
        stderr="status unavailable",
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_candidate,
            probe_candidate,
            probe_selected,
            probe_selected,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                picker_state,
                status_unavailable,
                selected_picker_state,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "23")) == 1
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "64")) == 1
    assert ("opencli", "browser", "seektalent-liepin", "click", "66") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "上海") not in commands.calls


def test_liepin_city_picker_filter_unapplied_reconciles_selected_before_retry_effect(
    tmp_path: Path,
) -> None:
    state_before = '<span>期望城市：</span>\n[23]<span>其他</span>'
    picker_state = '[60]<input placeholder=搜索城市 value=上海 />'
    selected_picker_state = (
        '[60]<input placeholder=搜索城市 value=上海 />\n'
        '<i>已选（1/9）</i>\n[66]<button><span>确认</span></button>'
    )
    applied_state = (
        '<span>期望城市：</span>\n'
        '<span class=ant-tag-checkable-checked>上海</span>'
    )
    probe_candidate = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [
                {"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}
            ],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_candidate,
            probe_candidate,
            probe_candidate,
            probe_selected,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                picker_state,
                picker_state,
                selected_picker_state,
                applied_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "64"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "66"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
        },
    )
    events: list[dict[str, object]] = []

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(
            ok=True,
            action="state",
            private_output=state_before,
        ),
        events=events,
    )

    assert result.ok is True
    assert commands.calls.count(
        ("opencli", "browser", "seektalent-liepin", "click", "23")
    ) == 1
    assert commands.calls.count(
        ("opencli", "browser", "seektalent-liepin", "click", "64")
    ) == 1
    assert commands.calls.count(
        ("opencli", "browser", "seektalent-liepin", "click", "66")
    ) == 1
    assert any(
        event.get("phase") == "city_picker_effect_reconciliation"
        and event.get("reason") == "requested_city_selected"
        for event in events
    )


def test_liepin_city_picker_retry_probe_unavailable_does_not_assume_picker_closed(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    """
    selected_picker_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    <i>已选（1/9）</i>
    [66]<button><span>确认</span></button>
    """
    probe_candidate = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    status_unavailable = OpenCliBrowserError("liepin_opencli_status_unavailable")
    state_status_unavailable = subprocess.CalledProcessError(
        1,
        ["opencli"],
        stderr="status unavailable",
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_candidate,
            probe_candidate,
            status_unavailable,
            status_unavailable,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                picker_state,
                state_status_unavailable,
                selected_picker_state,
                selected_picker_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
        },
    )

    with pytest.raises(OpenCliBrowserError) as raised:
        _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
            filter_name="city",
            section="expected",
            label="上海",
            current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
            events=[],
        )

    assert raised.value.safe_reason_code == "liepin_opencli_status_unavailable"
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "23")) == 1
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "64")) == 1
    assert ("opencli", "browser", "seektalent-liepin", "click", "66") not in commands.calls


def test_liepin_city_picker_candidate_click_timeout_reconciles_selected_state(
    tmp_path: Path,
) -> None:
    state_before = '<span>期望城市：</span>\n[23]<span>其他</span>'
    picker_state = '[60]<input placeholder=搜索城市 value=上海 />'
    selected_picker_state = (
        '[60]<input placeholder=搜索城市 value=上海 />\n'
        '<i>已选（1/9）</i>\n[66]<button><span>确认</span></button>'
    )
    applied_state = '<span>期望城市：</span>\n<span class=ant-tag-checkable-checked>上海</span>'
    probe_candidate = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_candidate,
            probe_candidate,
            probe_selected,
            probe_selected,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                picker_state,
                selected_picker_state,
                applied_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "64"): subprocess.TimeoutExpired(
                ["opencli"],
                timeout=1,
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "23")) == 1
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "64")) == 1
    assert ("opencli", "browser", "seektalent-liepin", "click", "66") in commands.calls


def test_liepin_city_picker_control_click_timeout_reconciles_open_picker(
    tmp_path: Path,
) -> None:
    state_before = '<span>期望城市：</span>\n[23]<span>其他</span>'
    picker_state = '[60]<input placeholder=搜索城市 value=上海 />'
    selected_picker_state = (
        '[60]<input placeholder=搜索城市 value=上海 />\n'
        '<i>已选（1/9）</i>\n[66]<button><span>确认</span></button>'
    )
    applied_state = '<span>期望城市：</span>\n<span class=ant-tag-checkable-checked>上海</span>'
    probe_candidate = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    probe_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )
    commands = SequenceEvalCommands(
        eval_outputs=[
            _closed_picker_probe(),
            probe_candidate,
            probe_candidate,
            probe_candidate,
            probe_selected,
        ],
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                picker_state,
                selected_picker_state,
                applied_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): subprocess.TimeoutExpired(
                ["opencli"],
                timeout=1,
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
        },
    )

    result = _runner(commands, lease_dir=tmp_path)._select_liepin_native_filter(
        filter_name="city",
        section="expected",
        label="上海",
        current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
        events=[],
    )

    assert result.ok is True
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "23")) == 1
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "64")) == 1
    assert ("opencli", "browser", "seektalent-liepin", "click", "66") in commands.calls


def test_liepin_city_picker_probe_rejects_confirm_when_only_other_city_is_selected(
    tmp_path: Path,
) -> None:
    misleading_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    <i>已选（1/9）</i>
    <span class=ant-tag-checkable-checked>北京</span>
    <ul class=ant-city-menu-list role=menu>
      [99]<li role=menuitem>上海</li>
    </ul>
    [91]<button><span>确认</span></button>
    """
    probe_other_city_selected = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": True,
            "searchInputRef": "60",
            "searchValue": "上海",
            "candidates": [{"ref": "64", "kind": "suggestion", "label": "中国 · 上海"}],
            "selectedCities": ["北京"],
            "confirmRefs": ["91"],
        },
        ensure_ascii=False,
    )
    commands = EvalCommands(eval_output=probe_other_city_selected)

    pending, confirm_ref = city_picker.pending_confirm_ref(
        _runner(commands, lease_dir=tmp_path),
        section="expected",
        label="上海",
        state_text=misleading_state,
    )

    assert pending is False
    assert confirm_ref is None
    assert ("opencli", "browser", "seektalent-liepin", "click", "91") not in commands.calls


@pytest.mark.parametrize(
    "probe_output",
    [
        '{"schema_version":"seektalent.liepin_city_picker.v1","section":"current"}',
        '{"schema_version":"seektalent.liepin_city_picker.v1","section":"expected","open":"yes"}',
    ],
)
def test_liepin_city_picker_malformed_probe_fails_closed_before_state_confirm_fallback(
    tmp_path: Path,
    probe_output: str,
) -> None:
    misleading_state = """
    [60]<input autocomplete=off placeholder=搜索城市 type=text value=上海 />
    <i>已选（1/9）</i>
    <span class=ant-tag-checkable-checked>北京</span>
    [99]<span>上海</span>
    [91]<button><span>确认</span></button>
    """
    commands = EvalCommands(eval_output=probe_output)

    with pytest.raises(OpenCliBrowserError) as raised:
        city_picker.pending_confirm_ref(
            _runner(commands, lease_dir=tmp_path),
            section="expected",
            label="上海",
            state_text=misleading_state,
        )

    assert raised.value.safe_reason_code == "liepin_opencli_malformed_state"
    assert ("opencli", "browser", "seektalent-liepin", "click", "91") not in commands.calls


def test_liepin_city_picker_probe_never_falls_back_to_document_scope() -> None:
    script = _liepin_city_picker_state_probe_script(section="expected")

    assert "modal || document" not in script
    assert "candidateRoot = pickerRoot" in script
    assert "open: Boolean(pickerRoot)" in script


def test_liepin_city_picker_closed_probe_rejects_base_page_modal_fields() -> None:
    closed_with_base_page_nodes = json.dumps(
        {
            "schema_version": "seektalent.liepin_city_picker.v1",
            "section": "expected",
            "controlRef": "23",
            "open": False,
            "searchInputRef": None,
            "searchValue": "",
            "candidates": [{"ref": "64", "kind": "final", "label": "上海"}],
            "selectedCities": ["上海"],
            "confirmRefs": ["66"],
        },
        ensure_ascii=False,
    )

    with pytest.raises(OpenCliBrowserError) as raised:
        city_picker.parse_picker_probe_output(closed_with_base_page_nodes, section="expected")

    assert raised.value.safe_reason_code == "liepin_opencli_malformed_state"


def test_liepin_city_picker_consumers_ignore_modal_fields_when_probe_is_closed() -> None:
    contradictory_payload: dict[str, object] = {
        "open": False,
        "candidates": [{"ref": "64", "kind": "final", "label": "上海"}],
        "selectedCities": ["上海"],
        "confirmRefs": ["66"],
    }

    assert city_picker.picker_selection_contains(contradictory_payload, label="上海") is False
    assert city_picker.picker_confirm_ref(contradictory_payload) is None


def test_liepin_city_picker_fails_closed_when_user_closes_picker(
    tmp_path: Path,
) -> None:
    state_before = """
    <span>期望城市：</span>
    [23]<span>其他</span>
    """
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_before,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as raised:
        runner._select_liepin_native_filter(
            filter_name="city",
            section="expected",
            label="上海",
            current_state=OpenCliBrowserResult(ok=True, action="state", private_output=state_before),
            events=[],
        )

    assert raised.value.safe_reason_code == "liepin_opencli_filter_option_unavailable"
    assert not any(call[3] == "fill" for call in commands.calls if len(call) > 3)


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
[60]<input role=combobox placeholder=搜索城市 value=苏州 />
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
    trace = _read_action_trace(tmp_path, "run-1")
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


def test_search_liepin_cards_waits_for_city_suggestions_to_match_current_input(tmp_path: Path) -> None:
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
<span>请选择城市</span>
[60]<input autocomplete=off placeholder=搜索城市 type=text />
[62]<div>国内</div>
[63]<div>海外</div>
"""
    state_stale_city_suggestion = """
<span>请选择城市</span>
[60]<input autocomplete=off placeholder=搜索城市 type=text value=苏州 />
[61]<li><p>江苏 · <span>常州</span></p></li>
[62]<div>国内</div>
[63]<div>海外</div>
"""
    state_current_city_suggestion = """
<span>请选择城市</span>
[60]<input autocomplete=off placeholder=搜索城市 type=text value=常州 />
[64]<li><p>江苏 · <span>常州</span></p></li>
[62]<div>国内</div>
[63]<div>海外</div>
"""
    state_after_expected_city = """
[20]<label>期望城市：</label>
[23]<label>其他</label>
[50]<label title=期望城市 />
  <span>常州</span>
王** 男 34岁 工作5年 硕士 常州
求职期望：常州 数据开发专家
"""
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "tab",
                "new",
                "https://h.liepin.com/search/getConditionItem#session",
            ): '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}',
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_city_picker,
                state_stale_city_suggestion,
                state_current_city_suggestion,
                state_after_expected_city,
            ],
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "fill",
                "26",
                "数据开发专家",
            ): '{"filled":true,"verified":true}',
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "fill",
                "60",
                "常州",
            ): '{"filled":true,"verified":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "64"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-delayed-city",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "常州"}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "63") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "61") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "64") in commands.calls


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
[22]<label>北京</label>
[23]<label>上海</label>
[24]<label>其他</label>
王** 男 34岁 工作5年 硕士 上海
"""
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
                state_city_search_empty,
                state_city_search_empty,
                state_overseas_picker,
                state_after_us_selected,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "CFO 首席财务官"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
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
    assert any(call[:4] == ("opencli", "browser", "seektalent-liepin", "eval") for call in commands.calls)
    assert ("opencli", "browser", "seektalent-liepin", "click", "24") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "60", "美国") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "62") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "70") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "91") in commands.calls
    trace = _read_action_trace(tmp_path, "run-1")
    assert {
        "action_kind": "confirm_native_city_filter",
        "filter": "city",
        "section": "expected",
        "value": "美国",
        "ok": True,
    } in trace["events"]


def test_search_liepin_cards_keeps_visible_expected_city_without_picker_retry(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
<span>期望城市：</span>
[21]<label>北京</label>
[22]<label>上海</label>
[23]<label>其他</label>
王** 男 34岁 工作5年 硕士 上海
"""
    state_after_expected_city = """
<span>期望城市：</span>
[21]<label>北京</label>
[22]<label class=ant-checkbox-wrapper ant-checkbox-wrapper-checked>上海</label>
[23]<label>其他</label>
王** 男 34岁 工作5年 硕士 上海
求职期望：上海 数据开发专家
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
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "22"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "23"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "上海"}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "22") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "23") not in commands.calls


def test_search_liepin_cards_selects_domestic_whole_city_from_city_picker(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[67]<label>美国</label>
[68]<label>杭州</label>
[74]<span>其他</span>
王** 男 34岁 工作5年 硕士 上海
"""
    state_city_picker = """
<span>请选择城市</span>
[294]<input autocomplete=off placeholder=搜索城市 type=text />
[298]<div>国内</div>
[299]<div>海外</div>
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
    state_after_shanghai_selected = """
<span>请选择城市</span>
[294]<input autocomplete=off placeholder=搜索城市 type=text />
[298]<div>国内</div>
[299]<div>海外</div>
[337]<span>全上海</span>
<i>已选（1/9）</i>
[340]<span>全上海</span>
[341]<button />
  <span>确认</span>
"""
    state_after_expected_city = """
已选 期望城市上海
[20]<label>期望城市：</label>
[66]<label>上海</label>
[67]<label>美国</label>
[68]<label>杭州</label>
[74]<span>其他</span>
王** 男 34岁 工作5年 硕士 上海
求职期望：上海 AI技术负责人
某科技公司 · AI技术负责人 2021.01-至今
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
                state_after_shanghai_selected,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "AI 技术负责人"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "66"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "74"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "337"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "341"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="AI 技术负责人",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "上海"}},
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "337") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "341") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "302") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "336") not in commands.calls


@pytest.mark.parametrize(("city_name", "city_ref"), [("苏州", "74"), ("宁波", "75")])
def test_search_liepin_cards_uses_expected_city_picker_when_city_exists_only_in_current(
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
[82]<span>其他</span>
<span>工作年限：</span>
王** 男 34岁 工作5年 硕士 上海
"""
    state_city_picker = """
[83]<input autocomplete=off placeholder=搜索城市 type=text />
"""
    state_city_suggestion = f"""
[83]<input autocomplete=off placeholder=搜索城市 type=text value={city_name} />
<div class=suggest-list>
  [84]<div>中国 · <span>{city_name}</span></div>
</div>
"""
    state_after_expected_city = f"""
<span>目前城市：</span>
[70]<span>不限</span>
[{city_ref}]<label>{city_name}</label>
<span>期望城市：</span>
[76]<span>不限</span>
[78]<label>上海</label>
[82]<span>其他</span>
[85]<label title=期望城市 />
  <span>{city_name}</span>
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
                state_city_picker,
                state_city_suggestion,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "82"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "83", city_name): (
                '{"filled":true,"verified":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "84"): '{"clicked":true}',
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
    assert ("opencli", "browser", "seektalent-liepin", "click", city_ref) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "82") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "84") in commands.calls
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
