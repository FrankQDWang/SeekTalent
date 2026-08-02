from __future__ import annotations

import json

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.providers.liepin.liepin_city_picker import (
    parse_picker_probe_output,
    picker_confirm_ref,
    picker_selection_contains,
)


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
