from __future__ import annotations

import json
from pathlib import Path

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.providers.liepin.liepin_city_picker import (
    CityPickerControlNoEffect,
    decide_picker_action,
    observe_picker_ready,
    parse_picker_probe_output,
    picker_chip_applied,
    picker_chip_ref,
    picker_confirm_ref,
    picker_control_ref,
    pending_confirm_ref,
    picker_selection_contains,
    reconcile_city_filter_effect,
)
from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin import liepin_city_picker as city_picker_module


_REPLAY_FIXTURE = Path(__file__).parent / "fixtures" / "liepin" / "city-picker-observations-v1.json"


def _probe(**overrides: object) -> str:
    payload: dict[str, object] = {
        "schema_version": "seektalent.liepin_city_picker.v1",
        "section": "expected",
        "open": True,
        "controlRef": "23",
        "searchInputRef": "60",
        "searchValue": "上海",
        "candidates": [
            {"ref": "64", "kind": "suggestion", "label": "中国 · 上海"},
        ],
        "selectedCities": ["上海"],
        "confirmRefs": ["66"],
        "pickerPhase": "open",
        "searchInputPresent": True,
        "searchInputVisible": True,
        "citySurfacePresent": True,
        "confirmPresent": True,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_city_picker_probe_accepts_bounded_focused_state() -> None:
    payload = parse_picker_probe_output(_probe(), section="expected")

    assert payload["searchInputRef"] == "60"
    assert payload["candidates"] == [
        {"ref": "64", "kind": "suggestion", "label": "中国 · 上海"},
    ]
    assert payload["chips"] == []
    assert picker_selection_contains(payload, label="上海")
    assert picker_confirm_ref(payload) == "66"


def test_city_picker_probe_accepts_quick_city_chips() -> None:
    payload = parse_picker_probe_output(
        _probe(
            open=False,
            searchInputRef=None,
            searchValue="",
            candidates=[],
            selectedCities=[],
            confirmRefs=[],
            pickerPhase="closed",
            searchInputPresent=False,
            searchInputVisible=False,
            citySurfacePresent=False,
            confirmPresent=False,
            chips=[
                {"ref": "21", "label": "北京", "selected": False},
                {"ref": "22", "label": "上海", "selected": True},
            ],
        ),
        section="expected",
    )

    assert payload["chips"] == [
        {"ref": "21", "label": "北京", "selected": False},
        {"ref": "22", "label": "上海", "selected": True},
    ]


def test_picker_chip_ref_requires_one_exact_focused_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Site:
        pass

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: {
            "chips": [
                {"ref": "21", "label": "北京", "selected": False},
                {"ref": "22", "label": "上海", "selected": False},
            ]
        },
    )
    assert picker_chip_ref(_Site(), section="expected", label="北京") == "21"
    assert picker_chip_ref(_Site(), section="expected", label="苏州") is None

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: {
            "chips": [
                {"ref": "21", "label": "北京", "selected": False},
                {"ref": "99", "label": "北京", "selected": False},
            ]
        },
    )
    assert picker_chip_ref(_Site(), section="expected", label="北京") is None


def test_picker_chip_applied_requires_unique_selected_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Site:
        pass

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: {
            "chips": [
                {"ref": "21", "label": "北京", "selected": True},
                {"ref": "22", "label": "上海", "selected": False},
            ]
        },
    )
    assert picker_chip_applied(_Site(), section="expected", label="北京") is True
    assert picker_chip_applied(_Site(), section="expected", label="上海") is False

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: {
            "chips": [
                {"ref": "21", "label": "北京", "selected": False},
            ]
        },
    )
    assert picker_chip_applied(_Site(), section="expected", label="北京") is False

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: {
            "chips": [
                {"ref": "21", "label": "北京", "selected": True},
                {"ref": "99", "label": "北京", "selected": True},
            ]
        },
    )
    assert picker_chip_applied(_Site(), section="expected", label="北京") is False


def test_city_picker_probe_rejects_chip_without_selected_bool() -> None:
    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_malformed_state"):
        parse_picker_probe_output(
            _probe(
                open=False,
                searchInputRef=None,
                searchValue="",
                candidates=[],
                selectedCities=[],
                confirmRefs=[],
                pickerPhase="closed",
                searchInputPresent=False,
                searchInputVisible=False,
                citySurfacePresent=False,
                confirmPresent=False,
                chips=[{"ref": "21", "label": "北京"}],
            ),
            section="expected",
        )


def test_city_picker_probe_allows_only_explicit_incomplete_open_readiness() -> None:
    output = _probe(
        searchInputRef=None,
        searchValue="",
        candidates=[],
        selectedCities=[],
        confirmRefs=[],
    )

    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_malformed_state"):
        parse_picker_probe_output(output, section="expected")

    payload = parse_picker_probe_output(
        output,
        section="expected",
        allow_incomplete_open=True,
    )
    assert payload["readinessIncomplete"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"section": "current"},
        {"controlRef": "bad/ref"},
        {"candidates": [{"ref": "64", "kind": "navigation", "label": "上海"}]},
        {"selectedCities": ["x" * 81]},
        {"confirmRefs": ["65", "66", "67"]},
        {"pickerPhase": "closed"},
    ],
)
def test_city_picker_probe_rejects_wrong_or_unbounded_state(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_malformed_state"):
        parse_picker_probe_output(_probe(**overrides), section="expected")


def test_city_picker_confirmation_requires_one_exact_ref() -> None:
    payload = parse_picker_probe_output(
        _probe(confirmRefs=["65", "66"]),
        section="expected",
    )

    assert picker_confirm_ref(payload) is None


def test_city_picker_replay_observations_have_one_deterministic_decision() -> None:
    fixture = json.loads(_REPLAY_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "seektalent.liepin_city_picker_replay.v1"

    for case in fixture["cases"]:
        payload = parse_picker_probe_output(
            json.dumps(case["observation"], ensure_ascii=False),
            section="expected",
            allow_incomplete_open=case.get("allow_incomplete_open") is True,
        )
        label = str(case.get("label") or "上海")
        assert decide_picker_action(payload, label=label) == (
            case["expected_decision"],
            case["expected_ref"],
        ), case["name"]


def test_decide_picker_action_prefers_hot_exact_over_fill_search() -> None:
    hot_open = parse_picker_probe_output(
        _probe(
            searchValue="",
            candidates=[
                {"ref": "71", "kind": "final", "label": "苏州"},
                {"ref": "72", "kind": "final", "label": "北京"},
            ],
            selectedCities=[],
        ),
        section="expected",
    )
    assert decide_picker_action(hot_open, label="北京") == ("select_candidate", "72")
    assert decide_picker_action(hot_open, label="福建") == ("fill_search", "60")


def test_decide_picker_action_search_results_require_exact_match() -> None:
    after_search = parse_picker_probe_output(
        _probe(
            searchValue="福建",
            candidates=[
                {"ref": "80", "kind": "suggestion", "label": "福州"},
                {"ref": "81", "kind": "suggestion", "label": "中国 · 福建"},
            ],
            selectedCities=[],
        ),
        section="expected",
    )
    assert decide_picker_action(after_search, label="福建") == ("select_candidate", "81")

    no_exact = parse_picker_probe_output(
        _probe(
            searchValue="福建",
            candidates=[{"ref": "80", "kind": "suggestion", "label": "福州"}],
            selectedCities=[],
        ),
        section="expected",
    )
    assert decide_picker_action(no_exact, label="福建") == ("no_exact_match", None)


def test_picker_chip_ref_tracks_variable_shortcut_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Site:
        pass

    expected_with_beijing = {
        "chips": [
            {"ref": "e1", "label": "北京", "selected": False},
            {"ref": "e2", "label": "上海", "selected": False},
            {"ref": "e3", "label": "福建", "selected": False},
        ]
    }
    expected_without_beijing = {
        "chips": [
            {"ref": "e2", "label": "上海", "selected": False},
            {"ref": "e3", "label": "福建", "selected": False},
        ]
    }
    empty_shortcuts = {"chips": []}
    current_only = {
        "chips": [
            {"ref": "c1", "label": "南京", "selected": False},
            {"ref": "c2", "label": "杭州", "selected": False},
        ]
    }

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: expected_with_beijing,
    )
    assert picker_chip_ref(_Site(), section="expected", label="北京") == "e1"

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: expected_without_beijing,
    )
    assert picker_chip_ref(_Site(), section="expected", label="北京") is None
    assert picker_chip_ref(_Site(), section="expected", label="福建") == "e3"

    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: empty_shortcuts,
    )
    assert picker_chip_ref(_Site(), section="expected", label="北京") is None

    def read_for_section(
        _site: object,
        *,
        section: str,
        allow_incomplete_open: bool = False,
    ) -> dict[str, object]:
        del allow_incomplete_open
        if section == "current":
            return current_only
        return expected_with_beijing

    monkeypatch.setattr(city_picker_module, "_read_picker_state", read_for_section)
    assert picker_chip_ref(_Site(), section="current", label="南京") == "c1"
    assert picker_chip_ref(_Site(), section="expected", label="南京") is None
    assert picker_chip_ref(_Site(), section="expected", label="北京") == "e1"


def test_find_liepin_city_filter_option_clicks_hot_exact_without_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.providers.liepin.liepin_city_picker import find_liepin_city_filter_option

    class Site:
        def fill(self, *, target: str, text: str) -> None:
            raise AssertionError(f"should not search when hot exact exists: {target}/{text}")

    state = OpenCliBrowserResult(ok=True, action="state")
    monkeypatch.setattr(
        city_picker_module,
        "_read_picker_state",
        lambda *_args, **_kwargs: {
            "open": True,
            "pickerPhase": "open",
            "searchInputRef": "60",
            "candidates": [{"ref": "71", "kind": "final", "label": "苏州"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        },
    )
    before_calls = 0

    def before_effect() -> None:
        nonlocal before_calls
        before_calls += 1

    result, ref = find_liepin_city_filter_option(
        Site(),  # type: ignore[arg-type]
        section="expected",
        label="苏州",
        current_state=state,
        events=[],
        before_effect=before_effect,
        timeout_seconds=1,
    )

    assert result is state
    assert ref == "71"
    assert before_calls == 0


def test_find_liepin_city_filter_option_searches_then_requires_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.providers.liepin.liepin_city_picker import find_liepin_city_filter_option

    fills: list[tuple[str, str]] = []
    probe_phase = {"n": 0}

    class Site:
        def fill(self, *, target: str, text: str) -> None:
            fills.append((target, text))

        def state(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="state")

        def wait_time(self, *, seconds: int) -> OpenCliBrowserResult:
            del seconds
            return OpenCliBrowserResult(ok=True, action="wait_time")

    def read_state(*_args: object, **_kwargs: object) -> dict[str, object]:
        probe_phase["n"] += 1
        if probe_phase["n"] == 1:
            return {
                "open": True,
                "pickerPhase": "open",
                "searchInputRef": "60",
                "candidates": [{"ref": "71", "kind": "final", "label": "苏州"}],
                "selectedCities": [],
                "confirmRefs": ["66"],
            }
        return {
            "open": True,
            "pickerPhase": "open",
            "searchInputRef": "60",
            "candidates": [
                {"ref": "80", "kind": "suggestion", "label": "福州"},
                {"ref": "81", "kind": "suggestion", "label": "中国 · 福建"},
            ],
            "selectedCities": [],
            "confirmRefs": ["66"],
        }

    monkeypatch.setattr(city_picker_module, "_read_picker_state", read_state)
    monkeypatch.setattr(city_picker_module.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(city_picker_module.time, "sleep", lambda _seconds: None)

    events: list[dict[str, object]] = []
    before_calls = 0

    def before_effect() -> None:
        nonlocal before_calls
        before_calls += 1

    result, ref = find_liepin_city_filter_option(
        Site(),  # type: ignore[arg-type]
        section="expected",
        label="福建",
        current_state=OpenCliBrowserResult(ok=True, action="state"),
        events=events,
        before_effect=before_effect,
        timeout_seconds=2,
    )

    assert fills == [("60", "福建")]
    assert before_calls == 1
    assert ref == "81"
    assert result.ok is True


def test_find_liepin_city_filter_option_unavailable_without_exact_after_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.providers.liepin.liepin_city_picker import find_liepin_city_filter_option

    fills: list[tuple[str, str]] = []
    probe_phase = {"n": 0}

    class Site:
        def fill(self, *, target: str, text: str) -> None:
            fills.append((target, text))

        def state(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="state")

        def wait_time(self, *, seconds: int) -> OpenCliBrowserResult:
            del seconds
            return OpenCliBrowserResult(ok=True, action="wait_time")

    def read_state(*_args: object, **_kwargs: object) -> dict[str, object]:
        probe_phase["n"] += 1
        if probe_phase["n"] == 1:
            return {
                "open": True,
                "pickerPhase": "open",
                "searchInputRef": "60",
                "searchValue": "",
                "candidates": [{"ref": "71", "kind": "final", "label": "苏州"}],
                "selectedCities": [],
                "confirmRefs": ["66"],
            }
        return {
            "open": True,
            "pickerPhase": "open",
            "searchInputRef": "60",
            "searchValue": "福建",
            "candidates": [{"ref": "80", "kind": "suggestion", "label": "福州"}],
            "selectedCities": [],
            "confirmRefs": ["66"],
        }

    monkeypatch.setattr(city_picker_module, "_read_picker_state", read_state)
    monkeypatch.setattr(city_picker_module.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(city_picker_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_filter_option_unavailable"):
        find_liepin_city_filter_option(
            Site(),  # type: ignore[arg-type]
            section="expected",
            label="福建",
            current_state=OpenCliBrowserResult(ok=True, action="state"),
            events=[],
            before_effect=lambda: None,
            timeout_seconds=2,
        )

    assert fills == [("60", "福建")]


def test_city_picker_probe_unavailable_never_falls_back_to_page_text() -> None:
    class UnavailableProbeSite:
        def _run_fixed_readonly_eval_probe(self, **_kwargs: object) -> str:
            raise OpenCliBrowserError("liepin_opencli_status_unavailable")

    site = UnavailableProbeSite()

    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_status_unavailable"):
        pending_confirm_ref(
            site,  # type: ignore[arg-type]
            section="expected",
            label="上海",
        )
    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_status_unavailable"):
        picker_control_ref(site, section="expected")  # type: ignore[arg-type]


def test_city_picker_has_one_action_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    picker_source = (root / "src/seektalent/providers/liepin/liepin_city_picker.py").read_text(
        encoding="utf-8"
    )
    adapter_source = (root / "src/seektalent/providers/liepin/liepin_site_adapter.py").read_text(
        encoding="utf-8"
    )

    for fallback_name in (
        "native_filter_city_confirm_ref",
        "native_filter_city_picker_selection_contains",
        "native_filter_city_overseas_tab_ref",
        "native_filter_city_picker_option_visible",
        "native_filter_city_search_input_ref",
        "native_filter_city_search_input_matches",
    ):
        assert fallback_name not in picker_source
    assert 'control_authority = "state_fallback"' not in adapter_source


def test_city_picker_readiness_uses_the_configured_deadline_instead_of_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed = 0.0

    class Site:
        def state(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="state", private_output="")

        def wait_time(self, *, seconds: int) -> OpenCliBrowserResult:
            nonlocal elapsed
            elapsed += seconds
            return OpenCliBrowserResult(ok=True, action="wait_time")

    monkeypatch.setattr(
        city_picker_module,
        "native_filter_selection_applied",
        lambda *_args, **_kwargs: False,
    )

    def picker_state(*_args: object, **_kwargs: object):
        decision = "fill_search" if elapsed >= 3 else "closed"
        payload = {
            "open": decision == "fill_search",
            "searchInputRef": "60" if decision == "fill_search" else None,
            "candidates": [],
            "selectedCities": [],
            "confirmRefs": [],
        }
        return payload, {
            "probe_status": "observed",
            "probe_search_input_present": decision == "fill_search",
            "probe_search_input_visible": decision == "fill_search",
            "probe_city_surface_present": decision == "fill_search",
            "probe_confirm_present": False,
        }

    monkeypatch.setattr(city_picker_module, "_picker_state_for_readiness", picker_state)
    monkeypatch.setattr(city_picker_module.time, "monotonic", lambda: elapsed)

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(city_picker_module.time, "sleep", sleep)

    events: list[dict[str, object]] = []
    result = observe_picker_ready(
        Site(),  # type: ignore[arg-type]
        section="expected",
        label="Shanghai",
        events=events,
        timeout_seconds=4,
    )

    assert result.ok is True
    assert elapsed == 3
    assert len(events) == 4


def test_city_picker_reconciliation_transient_observation_stops_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed = 0.0

    class Site:
        def state(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_timeout",
            )

    monkeypatch.setattr(city_picker_module.time, "monotonic", lambda: elapsed)

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(city_picker_module.time, "sleep", sleep)
    events: list[dict[str, object]] = []

    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_timeout"):
        reconcile_city_filter_effect(
            Site(),  # type: ignore[arg-type]
            section="expected",
            label="Shanghai",
            events=events,
            allow_pending_confirm=False,
            timeout_seconds=2,
        )

    assert elapsed == 2
    assert len(events) == 3


def test_city_picker_readiness_classifies_observed_closed_as_control_no_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed = 0.0

    class Site:
        def state(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="state", private_output="")

    monkeypatch.setattr(
        city_picker_module,
        "native_filter_selection_applied",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        city_picker_module,
        "_picker_state_for_readiness",
        lambda *_args, **_kwargs: (
            {
                "open": False,
                "pickerPhase": "closed",
                "searchInputPresent": False,
                "searchInputVisible": False,
                "citySurfacePresent": False,
                "confirmPresent": False,
            },
            {
                "probe_status": "closed",
                "probe_search_input_present": False,
                "probe_search_input_visible": False,
                "probe_city_surface_present": False,
                "probe_confirm_present": False,
            },
        ),
    )
    monkeypatch.setattr(city_picker_module.time, "monotonic", lambda: elapsed)

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(city_picker_module.time, "sleep", sleep)
    events: list[dict[str, object]] = []

    with pytest.raises(CityPickerControlNoEffect):
        observe_picker_ready(
            Site(),  # type: ignore[arg-type]
            section="expected",
            label="Shanghai",
            events=events,
            timeout_seconds=2,
        )

    assert elapsed == 2
    assert [event["reason"] for event in events] == [
        "city_picker_not_ready",
        "city_picker_not_ready",
        "city_picker_not_ready",
    ]
