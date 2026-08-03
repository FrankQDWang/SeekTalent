from __future__ import annotations

import json
from pathlib import Path

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.providers.liepin.liepin_city_picker import (
    decide_picker_action,
    observe_picker_ready,
    parse_picker_probe_output,
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
    assert picker_selection_contains(payload, label="上海")
    assert picker_confirm_ref(payload) == "66"


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
        assert decide_picker_action(payload, label="上海") == (
            case["expected_decision"],
            case["expected_ref"],
        ), case["name"]


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
