from __future__ import annotations

from typing import TYPE_CHECKING

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult
from seektalent.providers.liepin.liepin_site_parsing import _opencli_result_text
from seektalent.providers.liepin.opencli_filter_planning import (
    native_filter_city_overseas_tab_ref,
    native_filter_city_search_input_ref,
    native_filter_option_visible_in_section,
)

if TYPE_CHECKING:
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter


def find_liepin_city_filter_option(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    current_state: OpenCliBrowserResult,
    events: list[dict[str, object]],
) -> OpenCliBrowserResult:
    state = current_state
    state_text = _opencli_result_text(state)
    if native_filter_option_visible_in_section(state_text, section=section, label=label):
        return state
    if (input_ref := native_filter_city_search_input_ref(state_text)) is not None:
        site.fill(target=input_ref, text=label)
        events.append(
            {"action_kind": "fill_native_city_filter_search", "filter": "city", "value": label, "ok": True}
        )
        state = _observe_city_option(site, section=section, label=label, phase="search", events=events)
        state_text = _opencli_result_text(state)
        if native_filter_option_visible_in_section(state_text, section=section, label=label):
            return state
    if (overseas_ref := native_filter_city_overseas_tab_ref(state_text)) is not None:
        site._click_native_filter_ref(overseas_ref)
        events.append(
            {"action_kind": "open_native_city_overseas_tab", "filter": "city", "value": label, "ok": True}
        )
        state = _observe_city_option(site, section=section, label=label, phase="overseas", events=events)
        if native_filter_option_visible_in_section(
            _opencli_result_text(state), section=section, label=label
        ):
            return state
    raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")


def _observe_city_option(
    site: LiepinSiteAdapter,
    *,
    section: str,
    label: str,
    phase: str,
    events: list[dict[str, object]],
) -> OpenCliBrowserResult:
    for attempt in range(1, 3):
        if attempt > 1:
            site.wait_time(seconds=1)
        state = site.state()
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
        if native_filter_option_visible_in_section(
            _opencli_result_text(state), section=section, label=label
        ) or attempt == 2:
            return state
    raise AssertionError("unreachable")
