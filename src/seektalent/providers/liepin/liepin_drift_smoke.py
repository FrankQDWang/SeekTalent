from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL


class LiepinDriftSite(Protocol):
    def status(self) -> OpenCliBrowserResult: ...

    def open_liepin_tab(self, url: str) -> OpenCliBrowserResult: ...

    def state(self) -> OpenCliBrowserResult: ...

    def apply_liepin_native_filters(
        self,
        *,
        source_run_id: str,
        native_filters: Mapping[str, object],
    ) -> OpenCliBrowserResult: ...

    def extract_visible_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult: ...

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult: ...

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult: ...


def _string_key_mapping_or_none(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True)
class LiepinDriftProbe:
    name: str
    status: str
    safe_reason_code: str


@dataclass(frozen=True)
class LiepinDriftSmokeReport:
    status: str
    safe_reason_code: str
    first_failed_probe: str | None
    probes: tuple[LiepinDriftProbe, ...]


def run_liepin_drift_smoke(
    *,
    site: LiepinDriftSite,
    include_filter_probe: bool = False,
    filter_probe_native_filters: Mapping[str, object] | None = None,
    include_detail_probe: bool = False,
    source_run_id: str = "liepin-drift-smoke",
    max_cards: int = 3,
) -> LiepinDriftSmokeReport:
    probes: list[LiepinDriftProbe] = []

    status = site.status()
    if not status.ok:
        return _failed("opencli_status", _map_reason(status.safe_reason_code), probes)
    probes.append(LiepinDriftProbe("opencli_status", "passed", "configured"))

    opened = site.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)
    if not opened.ok:
        return _failed("liepin_search_page", _map_reason(opened.safe_reason_code), probes)
    state = site.state()
    if not state.ok:
        return _failed("liepin_search_page", _map_reason(state.safe_reason_code), probes)
    probes.append(LiepinDriftProbe("liepin_search_page", "passed", "configured"))

    if include_filter_probe:
        filtered = site.apply_liepin_native_filters(
            source_run_id=source_run_id,
            native_filters=dict(filter_probe_native_filters or {}),
        )
        if not filtered.ok:
            return _failed("liepin_filter", _map_filter_reason(filtered.safe_reason_code), probes)
        probes.append(LiepinDriftProbe("liepin_filter", "passed", "configured"))

    cards = site.extract_visible_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)
    if not cards.ok:
        return _failed("liepin_card_extraction", _map_card_reason(cards.safe_reason_code), probes)
    card_count = int(cards.counts.get("cards") or 0)
    if card_count < 1:
        return _failed("liepin_card_extraction", "liepin_card_extraction_changed", probes)
    probes.append(LiepinDriftProbe("liepin_card_extraction", "passed", "configured"))

    if include_detail_probe:
        raw_cards = cards.observation.get("cards")
        first: object = raw_cards[0] if isinstance(raw_cards, list) and raw_cards else None
        first_card = _string_key_mapping_or_none(first)
        ref = first_card.get("ref") if first_card is not None else None
        if not isinstance(ref, str) or not ref:
            return _failed("liepin_detail", "liepin_detail_changed", probes)
        detail = site.open_liepin_detail(source_run_id=source_run_id, ref=ref, rank=1)
        if not detail.ok:
            return _failed("liepin_detail", _map_detail_reason(detail.safe_reason_code), probes)
        captured = site.capture_liepin_detail_resume(source_run_id=source_run_id, rank=1)
        if not captured.ok:
            return _failed("liepin_detail", _map_detail_reason(captured.safe_reason_code), probes)
        probes.append(LiepinDriftProbe("liepin_detail", "passed", "configured"))

    return LiepinDriftSmokeReport(
        status="passed",
        safe_reason_code="configured",
        first_failed_probe=None,
        probes=tuple(probes),
    )


def _failed(
    probe_name: str,
    safe_reason_code: str,
    prior_probes: list[LiepinDriftProbe],
) -> LiepinDriftSmokeReport:
    probes = [*prior_probes, LiepinDriftProbe(probe_name, "failed", safe_reason_code)]
    return LiepinDriftSmokeReport(
        status="failed",
        safe_reason_code=safe_reason_code,
        first_failed_probe=probe_name,
        probes=tuple(probes),
    )


def _map_reason(reason: str) -> str:
    if reason == "liepin_opencli_extension_disconnected":
        return "opencli_extension_disconnected"
    if reason in {
        "liepin_opencli_status_unavailable",
        "liepin_opencli_daemon_not_running",
        "liepin_opencli_daemon_stale",
    }:
        return "opencli_status_unavailable"
    if reason in {"liepin_browser_login_required", "liepin_opencli_login_required"}:
        return "liepin_login_required"
    if reason == "liepin_opencli_risk_page":
        return "liepin_risk_page"
    if reason in {"liepin_opencli_selector_not_found", "liepin_opencli_target_not_found"}:
        return "liepin_search_page_changed"
    return reason or "failed_provider_error"


def _map_filter_reason(reason: str) -> str:
    if reason in {
        "liepin_opencli_selector_not_found",
        "liepin_opencli_target_not_found",
        "liepin_opencli_selector_ambiguous",
        "liepin_opencli_filter_option_unavailable",
        "liepin_opencli_filter_unapplied",
        "liepin_opencli_forbidden_command",
    }:
        return "liepin_filter_changed"
    return _map_reason(reason)


def _map_card_reason(reason: str) -> str:
    if reason in {
        "liepin_opencli_selector_not_found",
        "liepin_opencli_target_not_found",
        "liepin_opencli_selector_ambiguous",
        "liepin_opencli_stale_ref",
    }:
        return "liepin_card_extraction_changed"
    return _map_reason(reason)


def _map_detail_reason(reason: str) -> str:
    mapped = _map_reason(reason)
    if mapped in {
        "opencli_status_unavailable",
        "opencli_extension_disconnected",
        "liepin_login_required",
        "liepin_risk_page",
    }:
        return mapped
    return "liepin_detail_changed"
