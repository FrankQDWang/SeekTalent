from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult
from seektalent.providers.liepin.liepin_site_parsing import _opencli_result_text
from seektalent.providers.liepin.opencli_filter_planning import (
    native_filter_selection_applied,
)

if TYPE_CHECKING:
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter


class CityPickerControlNoEffect(OpenCliBrowserError):
    """The picker control was clicked but remained conclusively closed."""

    def __init__(self) -> None:
        super().__init__("liepin_opencli_filter_unapplied")


def observe_picker_ready(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    events: list[dict[str, object]],
    timeout_seconds: float,
) -> OpenCliBrowserResult:
    deadline = _deadline(timeout_seconds)
    attempt = 0
    readiness_reasons: list[str] = []
    while True:
        attempt += 1
        state = site.state()
        reason = state.safe_reason_code
        probe_evidence: dict[str, object] = {
            "probe_status": "not_observed",
            "probe_search_input_present": None,
            "probe_search_input_visible": None,
            "probe_city_surface_present": None,
            "probe_confirm_present": None,
        }
        if state.ok:
            state_text = _opencli_result_text(state)
            if native_filter_selection_applied(state_text, section=section, label=label):
                reason = "requested_city_already_applied"
            else:
                picker_state, probe_evidence = _picker_state_for_readiness(
                    site,
                    section=section,
                )
                if picker_state is None:
                    reason = "city_picker_probe_unavailable"
                else:
                    decision, _ref = decide_picker_action(picker_state, label=label)
                    reason = {
                        "confirm_selection": "requested_city_selected",
                        "fill_search": "city_search_input_ready",
                        "select_candidate": "requested_city_option_ready",
                        "no_exact_match": "city_picker_no_exact_match",
                        "closed": "city_picker_not_ready",
                        "wait": "city_picker_probe_incomplete",
                    }[decision]
        events.append(
            {
                "action_kind": "observe_native_filter_menu",
                "filter": "city",
                "section": section,
                "ok": state.ok,
                "phase": "city_picker_readiness",
                "attempt": attempt,
                "reason": reason,
                **probe_evidence,
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
        if reason not in {
            "city_picker_not_ready",
            "city_picker_probe_incomplete",
            "city_picker_probe_unavailable",
            "city_picker_no_exact_match",
        }:
            return state
        readiness_reasons.append(str(reason))
        if not _wait_for_next_observation(deadline):
            break
    if readiness_reasons and all(
        reason == "city_picker_not_ready" for reason in readiness_reasons
    ):
        raise CityPickerControlNoEffect()
    raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")


def find_liepin_city_filter_option(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    current_state: OpenCliBrowserResult,
    events: list[dict[str, object]],
    before_effect: Callable[[], None],
    timeout_seconds: float,
) -> tuple[OpenCliBrowserResult, str | None]:
    state = current_state
    decision, ref = decide_picker_action(
        _read_picker_state(site, section=section),
        label=label,
    )
    if decision == "select_candidate" and ref is not None:
        return state, ref
    if decision == "no_exact_match":
        raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
    if decision == "fill_search" and ref is not None:
        before_effect()
        site.fill(target=ref, text=label)
        events.append(
            {"action_kind": "fill_native_city_filter_search", "filter": "city", "value": label, "ok": True}
        )
        state, picker_state = _observe_city_option(
            site,
            section=section,
            label=label,
            phase="search",
            events=events,
            timeout_seconds=timeout_seconds,
        )
        decision, ref = decide_picker_action(picker_state, label=label)
        if decision == "select_candidate" and ref is not None:
            return state, ref
        if decision == "no_exact_match":
            raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
    raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")


def _observe_city_option(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    phase: str,
    events: list[dict[str, object]],
    timeout_seconds: float,
) -> tuple[OpenCliBrowserResult, dict[str, object]]:
    deadline = _deadline(timeout_seconds)
    attempt = 0
    while True:
        attempt += 1
        state = site.state()
        picker_state: dict[str, object] | None = None
        decision = "wait"
        try:
            picker_state = _read_picker_state(site, section=section)
            decision, _ref = decide_picker_action(picker_state, label=label)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_status_unavailable":
                raise
        events.append(
            {
                "action_kind": f"observe_native_city_filter_{phase}",
                "filter": "city",
                "ok": state.ok,
                "attempt": attempt,
                "decision": decision,
            }
        )
        if not state.ok:
            raise OpenCliBrowserError(state.safe_reason_code)
        if decision == "no_exact_match":
            raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
        if picker_state is not None and decision == "select_candidate":
            return state, picker_state
        if not _wait_for_next_observation(deadline):
            break
    raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")


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


def decide_picker_action(
    payload: dict[str, object],
    *,
    label: str,
) -> tuple[
    Literal[
        "closed",
        "wait",
        "fill_search",
        "select_candidate",
        "confirm_selection",
        "no_exact_match",
    ],
    str | None,
]:
    """Decide the next city-picker action from a focused probe payload.

    Priority when open: confirm selected city, else click an exact candidate
    (including hot-list tags), else fill search when the input is still empty for
    this label. If search already targets the label but no exact candidate exists,
    return no_exact_match — never click a non-matching first row.
    """
    if payload.get("pickerPhase") in {"input_hidden", "input_visible_root_incomplete"}:
        return "wait", None
    if payload.get("open") is not True:
        return "closed", None
    if picker_selection_contains(payload, label=label):
        confirm_ref = picker_confirm_ref(payload)
        return ("confirm_selection", confirm_ref) if confirm_ref is not None else ("wait", None)
    candidate_ref = _picker_candidate_ref(payload, label=label)
    if candidate_ref is not None:
        return "select_candidate", candidate_ref
    if _search_value_targets_label(payload, label=label):
        return "no_exact_match", None
    input_ref = payload.get("searchInputRef")
    if isinstance(input_ref, str):
        return "fill_search", input_ref
    return "wait", None


def picker_control_ref(site: LiepinSiteAdapter, *, section: str) -> str:
    payload = _read_picker_state(site, section=section)
    control_ref = payload.get("controlRef")
    if not isinstance(control_ref, str):
        raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
    return control_ref


def picker_chip_ref(site: LiepinSiteAdapter, *, section: str, label: str) -> str | None:
    """Return the focused-probe ref for a visible quick city chip, if any."""
    payload = _read_picker_state(site, section=section, allow_incomplete_open=True)
    chips = payload.get("chips")
    if not isinstance(chips, list):
        return None
    matches = [
        chip["ref"]
        for chip in chips
        if isinstance(chip, Mapping)
        and isinstance(chip.get("ref"), str)
        and isinstance(chip.get("label"), str)
        and _city_label_matches(str(chip["label"]), label)
    ]
    unique_refs = tuple(dict.fromkeys(matches))
    return unique_refs[0] if len(unique_refs) == 1 else None


def pending_confirm_ref(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
) -> tuple[bool, str | None]:
    decision, ref = decide_picker_action(
        _read_picker_state(site, section=section),
        label=label,
    )
    if decision == "confirm_selection":
        return True, ref
    return False, None


def resolve_picker_action(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    state: OpenCliBrowserResult,
    events: list[dict[str, object]],
    before_effect: Callable[[], None],
    timeout_seconds: float,
) -> tuple[OpenCliBrowserResult, str | None, bool, str | None]:
    pending_confirm, confirm_ref = pending_confirm_ref(
        site,
        section=section,
        label=label,
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
        timeout_seconds=timeout_seconds,
    )
    return state, option_ref, False, None


def reconcile_city_filter_effect(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    events: list[dict[str, object]],
    allow_pending_confirm: bool,
    timeout_seconds: float,
) -> tuple[
    OpenCliBrowserResult,
    Literal["applied", "selected", "open_unselected"],
    str | None,
]:
    last_state: OpenCliBrowserResult | None = None
    reasons: list[str] = []
    unavailable_reason = "liepin_opencli_status_unavailable"
    deadline = _deadline(timeout_seconds)
    attempt = 0
    while True:
        attempt += 1
        state = site.state()
        last_state = state
        reason = state.safe_reason_code
        confirm_ref: str | None = None
        if state.ok:
            state_text = _opencli_result_text(state)
            if native_filter_selection_applied(state_text, section=section, label=label):
                reason = "requested_city_applied"
            else:
                try:
                    decision, decision_ref = decide_picker_action(
                        _read_picker_state(site, section=section),
                        label=label,
                    )
                except OpenCliBrowserError as exc:
                    if exc.safe_reason_code != "liepin_opencli_status_unavailable":
                        raise
                    decision = "wait"
                    decision_ref = None
                    reason = "city_picker_probe_unavailable"
                else:
                    if decision == "confirm_selection":
                        confirm_ref = decision_ref
                        reason = "requested_city_selected"
                    elif decision == "closed":
                        reason = "city_picker_closed_unapplied"
                    else:
                        reason = "requested_city_not_selected"
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
                if not _wait_for_next_observation(deadline):
                    break
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
            if not _wait_for_next_observation(deadline):
                break
            continue
        reasons.append(str(reason))
        if not _wait_for_next_observation(deadline):
            break
    if last_state is None:
        raise AssertionError("unreachable")
    if reasons and all(
        reason == "requested_city_not_selected" for reason in reasons
    ):
        return last_state, "open_unselected", None
    if all(reason == "city_picker_probe_unavailable" for reason in reasons):
        raise OpenCliBrowserError("liepin_opencli_status_unavailable")
    if all(reason == "city_picker_observation_unavailable" for reason in reasons):
        raise OpenCliBrowserError(unavailable_reason)
    raise OpenCliBrowserError("liepin_opencli_filter_unapplied")


def _deadline(timeout_seconds: float) -> float:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return time.monotonic() + timeout_seconds


def _wait_for_next_observation(deadline: float) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(1.0, remaining))
    return True


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
    chips = parsed.get("chips")
    if chips is None:
        payload["chips"] = []
    else:
        if not isinstance(chips, list) or len(chips) > 24:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        safe_chips: list[dict[str, str]] = []
        for chip in chips:
            if not isinstance(chip, Mapping):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            ref = chip.get("ref")
            label = chip.get("label")
            if (
                not isinstance(ref, str)
                or not _is_safe_ref(ref)
                or not isinstance(label, str)
                or not label.strip()
                or len(label) > 80
            ):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            safe_chips.append({"ref": ref, "label": label.strip()})
        payload["chips"] = safe_chips
    evidence_keys = (
        "pickerPhase",
        "searchInputPresent",
        "searchInputVisible",
        "citySurfacePresent",
        "confirmPresent",
    )
    present_evidence_keys = tuple(key for key in evidence_keys if key in parsed)
    if present_evidence_keys and len(present_evidence_keys) != len(evidence_keys):
        raise OpenCliBrowserError("liepin_opencli_malformed_state")
    if present_evidence_keys:
        picker_phase = parsed["pickerPhase"]
        readiness_evidence = (
            parsed["searchInputPresent"],
            parsed["searchInputVisible"],
            parsed["citySurfacePresent"],
            parsed["confirmPresent"],
        )
        evidence_is_consistent = (
            picker_phase == "closed"
            and readiness_evidence == (False, False, False, False)
            or picker_phase == "input_hidden"
            and readiness_evidence == (True, False, False, False)
            or picker_phase == "input_visible_root_incomplete"
            and readiness_evidence[:2] == (True, True)
            and readiness_evidence[2:] != (True, True)
            or picker_phase == "open"
            and readiness_evidence == (True, True, True, True)
        )
        if (
            picker_phase
            not in {
                "closed",
                "input_hidden",
                "input_visible_root_incomplete",
                "open",
            }
            or not all(isinstance(value, bool) for value in readiness_evidence)
            or parsed["open"] != (picker_phase == "open")
            or not evidence_is_consistent
        ):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        payload["pickerPhase"] = picker_phase
        for key, value in zip(evidence_keys[1:], readiness_evidence, strict=True):
            payload[key] = value
    else:
        is_open = parsed["open"]
        payload["pickerPhase"] = "open" if is_open else "closed"
        payload["searchInputPresent"] = is_open
        payload["searchInputVisible"] = is_open
        payload["citySurfacePresent"] = is_open
        payload["confirmPresent"] = is_open
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


def _picker_state_for_readiness(
    site: LiepinSiteAdapter,
    *,
    section: str,
) -> tuple[dict[str, object] | None, dict[str, object]]:
    try:
        payload = _read_picker_state(
            site,
            section=section,
            allow_incomplete_open=True,
        )
    except OpenCliBrowserError as exc:
        if exc.safe_reason_code == "liepin_opencli_status_unavailable":
            return None, {
                "probe_status": "unavailable",
                "probe_search_input_present": None,
                "probe_search_input_visible": None,
                "probe_city_surface_present": None,
                "probe_confirm_present": None,
            }
        raise
    probe_status = str(payload["pickerPhase"])
    if payload.get("readinessIncomplete") is True:
        probe_status = "open_incomplete"
    return payload, {
        "probe_status": probe_status,
        "probe_search_input_present": payload["searchInputPresent"],
        "probe_search_input_visible": payload["searchInputVisible"],
        "probe_city_surface_present": payload["citySurfacePresent"],
        "probe_confirm_present": payload["confirmPresent"],
    }


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


def _search_value_targets_label(payload: dict[str, object], *, label: str) -> bool:
    search_value = payload.get("searchValue")
    if not isinstance(search_value, str):
        return False
    normalized_search = _normalized_city(search_value)
    normalized_label = _normalized_city(label)
    if not normalized_search or not normalized_label:
        return False
    return normalized_search == normalized_label or _city_label_matches(search_value, label)


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
