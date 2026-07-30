from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult
from seektalent.providers.liepin.liepin_site_parsing import _opencli_result_text
from seektalent.providers.liepin.opencli_filter_planning import (
    native_filter_city_confirm_ref,
    native_filter_city_picker_selection_contains,
    native_filter_city_overseas_tab_ref,
    native_filter_city_picker_option_visible,
    native_filter_city_search_input_ref,
    native_filter_city_search_input_matches,
    native_filter_option_visible_in_section,
    native_filter_selection_applied,
)

if TYPE_CHECKING:
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter


def observe_picker_ready(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    events: list[dict[str, object]],
) -> OpenCliBrowserResult:
    for attempt in range(1, 4):
        if attempt > 1:
            site.wait_time(seconds=1)
        state = site.state()
        reason = state.safe_reason_code
        if state.ok:
            state_text = _opencli_result_text(state)
            if native_filter_selection_applied(state_text, section=section, label=label):
                reason = "requested_city_already_applied"
            elif native_filter_city_search_input_ref(state_text) is not None:
                reason = "city_search_input_ready"
            elif native_filter_city_picker_option_visible(state_text, label=label):
                reason = "requested_city_option_ready"
            else:
                picker_state = _picker_state_for_readiness(site, section=section)
                if picker_state is not None and picker_state.get("readinessIncomplete") is True:
                    reason = "city_picker_probe_incomplete"
                elif picker_state is not None and _picker_selection_contains(picker_state, label=label):
                    reason = "requested_city_selected"
                elif picker_state is not None and _picker_candidate_ref(picker_state, label=label):
                    reason = "requested_city_option_ready"
                elif picker_state is not None and isinstance(
                    picker_state.get("searchInputRef"), str
                ):
                    reason = "city_search_input_ready"
                else:
                    reason = "city_picker_not_ready"
        events.append(
            {
                "action_kind": "observe_native_filter_menu",
                "filter": "city",
                "section": section,
                "ok": state.ok,
                "phase": "city_picker_readiness",
                "attempt": attempt,
                "reason": reason,
            }
        )
        if not state.ok:
            raise OpenCliBrowserError(state.safe_reason_code)
        if reason == "requested_city_already_applied":
            events.append(
                {
                    "action_kind": "verify_native_filter",
                    "filter": "city",
                    "section": section,
                    "value": label,
                    "ok": True,
                    "already_applied": True,
                }
            )
        if reason not in {"city_picker_not_ready", "city_picker_probe_incomplete"}:
            return state
    raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")


def find_liepin_city_filter_option(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    current_state: OpenCliBrowserResult,
    events: list[dict[str, object]],
    before_effect: Callable[[], None],
) -> tuple[OpenCliBrowserResult, str | None]:
    state = current_state
    state_text = _opencli_result_text(state)
    picker_state = _picker_state_or_none(site, section=section)
    if (
        picker_state is not None
        and _picker_search_matches(picker_state, label=label)
        and (candidate_ref := _picker_candidate_ref(picker_state, label=label)) is not None
    ):
        return state, candidate_ref
    if picker_state is None and native_filter_option_visible_in_section(
        state_text, section=section, label=label
    ):
        return state, None
    input_ref = None
    if picker_state is not None:
        probe_input_ref = picker_state.get("searchInputRef")
        input_ref = probe_input_ref if isinstance(probe_input_ref, str) else None
    else:
        input_ref = native_filter_city_search_input_ref(state_text)
    if input_ref is not None:
        before_effect()
        site.fill(target=input_ref, text=label)
        events.append(
            {"action_kind": "fill_native_city_filter_search", "filter": "city", "value": label, "ok": True}
        )
        state, picker_state = _observe_city_option(
            site,
            section=section,
            label=label,
            phase="search",
            events=events,
        )
        state_text = _opencli_result_text(state)
        if (
            picker_state is not None
            and _picker_search_matches(picker_state, label=label)
            and (candidate_ref := _picker_candidate_ref(picker_state, label=label)) is not None
        ):
            return state, candidate_ref
        if (
            picker_state is None
            and native_filter_city_search_input_matches(state_text, label=label)
            and native_filter_option_visible_in_section(state_text, section=section, label=label)
        ):
            return state, None
    if (overseas_ref := native_filter_city_overseas_tab_ref(state_text)) is not None:
        before_effect()
        site._click_native_filter_ref(overseas_ref)
        events.append(
            {"action_kind": "open_native_city_overseas_tab", "filter": "city", "value": label, "ok": True}
        )
        state, picker_state = _observe_city_option(
            site,
            section=section,
            label=label,
            phase="overseas",
            events=events,
        )
        state_text = _opencli_result_text(state)
        if (
            picker_state is not None
            and _picker_search_matches(picker_state, label=label)
            and (candidate_ref := _picker_candidate_ref(picker_state, label=label)) is not None
        ):
            return state, candidate_ref
        if (
            picker_state is None
            and native_filter_city_search_input_matches(state_text, label=label)
            and native_filter_option_visible_in_section(state_text, section=section, label=label)
        ):
            return state, None
    raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")


def _observe_city_option(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    phase: str,
    events: list[dict[str, object]],
) -> tuple[OpenCliBrowserResult, dict[str, object] | None]:
    for attempt in range(1, 3):
        if attempt > 1:
            site.wait_time(seconds=1)
        state = site.state()
        picker_state = _picker_state_or_none(site, section=section)
        events.append(
            {
                "action_kind": f"observe_native_city_filter_{phase}",
                "filter": "city",
                "ok": state.ok,
                "attempt": attempt,
            }
        )
        if not state.ok:
            raise OpenCliBrowserError(state.safe_reason_code)
        state_text = _opencli_result_text(state)
        probe_ready = (
            picker_state is not None
            and _picker_search_matches(picker_state, label=label)
            and _picker_candidate_ref(picker_state, label=label) is not None
        )
        state_fallback_ready = (
            picker_state is None
            and native_filter_city_search_input_matches(state_text, label=label)
            and native_filter_option_visible_in_section(state_text, section=section, label=label)
        )
        if probe_ready or state_fallback_ready or attempt == 2:
            return state, picker_state
    raise AssertionError("unreachable")


def picker_selection_contains(payload: dict[str, object], *, label: str) -> bool:
    return _picker_selection_contains(payload, label=label)


def picker_confirm_ref(payload: dict[str, object]) -> str | None:
    if payload.get("open") is not True:
        return None
    refs = payload.get("confirmRefs")
    if not isinstance(refs, list):
        return None
    unique_refs = tuple(dict.fromkeys(ref for ref in refs if isinstance(ref, str)))
    return unique_refs[0] if len(unique_refs) == 1 else None


def picker_control_ref(site: LiepinSiteAdapter, *, section: str) -> str | None:
    try:
        payload = _read_picker_state(site, section=section)
    except OpenCliBrowserError as exc:
        if exc.safe_reason_code == "liepin_opencli_status_unavailable":
            return None
        raise
    control_ref = payload.get("controlRef")
    if not isinstance(control_ref, str):
        raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
    return control_ref


def picker_open_state(
    site: LiepinSiteAdapter,
    *,
    section: str,
) -> Literal["open", "closed", "unavailable"]:
    try:
        payload = _read_picker_state(site, section=section)
    except OpenCliBrowserError as exc:
        if exc.safe_reason_code == "liepin_opencli_status_unavailable":
            return "unavailable"
        raise
    return "open" if payload.get("open") is True else "closed"


def pending_confirm_ref(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    state_text: str,
) -> tuple[bool, str | None]:
    payload = _picker_state_or_none(site, section=section)
    if payload is not None:
        if picker_selection_contains(payload, label=label):
            return True, picker_confirm_ref(payload)
        return False, None
    if native_filter_city_picker_selection_contains(state_text, label=label):
        return True, native_filter_city_confirm_ref(state_text)
    return False, None


def resolve_picker_action(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    state: OpenCliBrowserResult,
    state_text: str,
    events: list[dict[str, object]],
    before_effect: Callable[[], None],
) -> tuple[OpenCliBrowserResult, str | None, bool, str | None]:
    pending_confirm, confirm_ref = pending_confirm_ref(
        site,
        section=section,
        label=label,
        state_text=state_text,
    )
    if pending_confirm:
        if confirm_ref is None:
            raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
        return state, None, True, confirm_ref
    state, option_ref = find_liepin_city_filter_option(
        site,
        section=section,
        label=label,
        current_state=state,
        events=events,
        before_effect=before_effect,
    )
    return state, option_ref, False, None


def reconcile_city_filter_effect(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    events: list[dict[str, object]],
    allow_pending_confirm: bool,
) -> tuple[
    OpenCliBrowserResult,
    Literal["applied", "selected", "open_unselected"],
    str | None,
]:
    last_state: OpenCliBrowserResult | None = None
    reasons: list[str] = []
    unavailable_reason = "liepin_opencli_status_unavailable"
    for attempt in range(1, 4):
        if attempt > 1:
            site.wait_time(seconds=1)
        state = site.state()
        last_state = state
        reason = state.safe_reason_code
        confirm_ref: str | None = None
        if state.ok:
            state_text = _opencli_result_text(state)
            if native_filter_selection_applied(state_text, section=section, label=label):
                reason = "requested_city_applied"
            else:
                picker_state = _picker_state_or_none(site, section=section)
                if picker_state is None:
                    if native_filter_city_picker_selection_contains(state_text, label=label):
                        confirm_ref = native_filter_city_confirm_ref(state_text)
                        reason = "requested_city_selected"
                    else:
                        reason = "city_picker_probe_unavailable"
                elif _picker_selection_contains(picker_state, label=label):
                    confirm_ref = picker_confirm_ref(picker_state)
                    reason = "requested_city_selected"
                elif picker_state.get("open") is True:
                    reason = "requested_city_not_selected"
                else:
                    reason = "city_picker_closed_unapplied"
        events.append(
            {
                "action_kind": "observe_after_native_city_filter_effect",
                "filter": "city",
                "section": section,
                "ok": state.ok,
                "phase": "city_picker_effect_reconciliation",
                "attempt": attempt,
                "reason": reason,
            }
        )
        if not state.ok:
            unavailable_reason = state.safe_reason_code
            if state.safe_reason_code in {
                "liepin_opencli_status_unavailable",
                "liepin_opencli_timeout",
            }:
                reasons.append("city_picker_observation_unavailable")
                continue
            raise OpenCliBrowserError(state.safe_reason_code)
        if reason == "requested_city_applied":
            return state, "applied", None
        if reason == "requested_city_selected":
            if confirm_ref is None:
                raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
            if allow_pending_confirm:
                return state, "selected", confirm_ref
            reasons.append("requested_city_selected")
            continue
        reasons.append(str(reason))
    if last_state is None:
        raise AssertionError("unreachable")
    if reasons == ["requested_city_not_selected"] * 3:
        return last_state, "open_unselected", None
    if all(reason == "city_picker_probe_unavailable" for reason in reasons):
        raise OpenCliBrowserError("liepin_opencli_status_unavailable")
    if all(reason == "city_picker_observation_unavailable" for reason in reasons):
        raise OpenCliBrowserError(unavailable_reason)
    raise OpenCliBrowserError("liepin_opencli_filter_unapplied")


def parse_picker_probe_output(
    output: str,
    *,
    section: str,
    allow_incomplete_open: bool = False,
) -> dict[str, object]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
    if (
        not isinstance(parsed, Mapping)
        or parsed.get("schema_version") != "seektalent.liepin_city_picker.v1"
        or parsed.get("section") != section
        or not isinstance(parsed.get("open"), bool)
    ):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")

    payload: dict[str, object] = {
        "schema_version": "seektalent.liepin_city_picker.v1",
        "section": section,
        "open": parsed["open"],
    }
    for key in ("controlRef", "searchInputRef"):
        value = parsed.get(key)
        if value is not None:
            if not isinstance(value, str) or not _is_safe_ref(value):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            payload[key] = value
    search_value = parsed.get("searchValue")
    if not isinstance(search_value, str) or len(search_value) > 80:
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    payload["searchValue"] = search_value

    candidates = parsed.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 24:
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    safe_candidates: list[dict[str, str]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        ref = candidate.get("ref")
        kind = candidate.get("kind")
        label = candidate.get("label")
        if (
            not isinstance(ref, str)
            or not _is_safe_ref(ref)
            or kind not in {"suggestion", "final"}
            or not isinstance(label, str)
            or not label.strip()
            or len(label) > 80
        ):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        safe_candidates.append({"ref": ref, "kind": str(kind), "label": label.strip()})
    payload["candidates"] = safe_candidates

    selected_cities = parsed.get("selectedCities")
    if (
        not isinstance(selected_cities, list)
        or len(selected_cities) > 9
        or not all(
            isinstance(value, str) and value.strip() and len(value) <= 80
            for value in selected_cities
        )
    ):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    payload["selectedCities"] = list(selected_cities)

    confirm_refs = parsed.get("confirmRefs")
    if (
        not isinstance(confirm_refs, list)
        or len(confirm_refs) > 2
        or not all(isinstance(value, str) and _is_safe_ref(value) for value in confirm_refs)
    ):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    payload["confirmRefs"] = list(confirm_refs)
    if parsed["open"]:
        incomplete_open = (
            "searchInputRef" not in payload
            and not payload["searchValue"]
            and not payload["candidates"]
            and not payload["selectedCities"]
            and not payload["confirmRefs"]
        )
        if incomplete_open and allow_incomplete_open:
            payload["readinessIncomplete"] = True
        elif "searchInputRef" not in payload:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
    elif (
        "searchInputRef" in payload
        or payload["searchValue"]
        or payload["candidates"]
        or payload["selectedCities"]
        or payload["confirmRefs"]
    ):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    return payload


def _picker_state_or_none(
    site: LiepinSiteAdapter,
    *,
    section: str,
) -> dict[str, object] | None:
    try:
        return _read_picker_state(site, section=section)
    except OpenCliBrowserError as exc:
        if exc.safe_reason_code == "liepin_opencli_status_unavailable":
            return None
        raise


def _picker_state_for_readiness(
    site: LiepinSiteAdapter,
    *,
    section: str,
) -> dict[str, object] | None:
    try:
        return _read_picker_state(
            site,
            section=section,
            allow_incomplete_open=True,
        )
    except OpenCliBrowserError as exc:
        if exc.safe_reason_code == "liepin_opencli_status_unavailable":
            return None
        raise


def _read_picker_state(
    site: LiepinSiteAdapter,
    *,
    section: str,
    allow_incomplete_open: bool = False,
) -> dict[str, object]:
    if section not in {"current", "expected"}:
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    output = site._run_fixed_readonly_eval_probe(
        probe_name="liepin_city_picker_state",
        ref=section,
    ).strip()
    return parse_picker_probe_output(
        output,
        section=section,
        allow_incomplete_open=allow_incomplete_open,
    )


def _picker_search_matches(payload: dict[str, object], *, label: str) -> bool:
    if payload.get("open") is not True:
        return False
    value = payload.get("searchValue")
    return isinstance(value, str) and _normalized_city(value) == _normalized_city(label)


def _picker_selection_contains(payload: dict[str, object], *, label: str) -> bool:
    if payload.get("open") is not True:
        return False
    selected = payload.get("selectedCities")
    return isinstance(selected, list) and any(
        isinstance(value, str) and _city_label_matches(value, label)
        for value in selected
    )


def _picker_candidate_ref(payload: dict[str, object], *, label: str) -> str | None:
    if payload.get("open") is not True:
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        ref: object = None
        visible_label: object = None
        for key, value in candidate.items():
            if key == "ref":
                ref = value
            elif key == "label":
                visible_label = value
        if (
            isinstance(ref, str)
            and isinstance(visible_label, str)
            and _city_label_matches(visible_label, label)
        ):
            return ref
    return None


def _city_label_matches(visible_label: str, requested_label: str) -> bool:
    visible = _normalized_city(visible_label)
    requested = _normalized_city(requested_label)
    if not visible or not requested:
        return False
    return visible in {requested, f"全{requested}"} or visible.endswith(f"·{requested}")


def _normalized_city(value: str) -> str:
    return re.sub(r"\s+", "", value.strip()).replace("•", "·")


def _is_safe_ref(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None
