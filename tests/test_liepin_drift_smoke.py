from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from seektalent.opencli_browser.contracts import OpenCliBrowserResult


@dataclass
class FakeSite:
    status_result: OpenCliBrowserResult
    state_result: OpenCliBrowserResult
    cards_result: OpenCliBrowserResult | None = None
    filter_result: OpenCliBrowserResult | None = None
    detail_result: OpenCliBrowserResult | None = None
    detail_capture_result: OpenCliBrowserResult | None = None

    def status(self) -> OpenCliBrowserResult:
        return self.status_result

    def open_liepin_tab(self, url: str) -> OpenCliBrowserResult:
        del url
        return OpenCliBrowserResult(ok=True, action="open_liepin_tab")

    def state(self) -> OpenCliBrowserResult:
        return self.state_result

    def apply_liepin_native_filters(
        self,
        *,
        source_run_id: str,
        native_filters: Mapping[str, object],
    ) -> OpenCliBrowserResult:
        del source_run_id, native_filters
        return self.filter_result or OpenCliBrowserResult(ok=True, action="apply_liepin_filters")

    def extract_visible_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        del source_run_id, max_cards
        return self.cards_result or OpenCliBrowserResult(
            ok=True,
            action="extract_visible_liepin_cards",
            counts={"cards": 1},
            observation={
                "cards": [
                    {
                        "provider_rank": 1,
                        "ref": "70",
                        "current_or_recent_title": "Python engineer",
                        "skill_tags": ["Python"],
                        "experience_preview": [{"title": "Python engineer"}],
                    }
                ]
            },
        )

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        del source_run_id, ref, rank
        return self.detail_result or OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": 1})

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        del source_run_id, rank
        return self.detail_capture_result or OpenCliBrowserResult(
            ok=True,
            action="capture_liepin_detail_resume",
            counts={"details": 1},
        )


def test_drift_smoke_classifies_opencli_status_failure() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_extension_disconnected",
            ),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "opencli_status"
    assert report.safe_reason_code == "opencli_extension_disconnected"


def test_drift_smoke_classifies_opencli_status_unavailable() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_daemon_not_running",
            ),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "opencli_status"
    assert report.safe_reason_code == "opencli_status_unavailable"


def test_drift_smoke_classifies_stale_opencli_daemon_as_status_unavailable() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_daemon_stale",
            ),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "opencli_status"
    assert report.safe_reason_code == "opencli_status_unavailable"


def test_drift_smoke_classifies_liepin_login_required() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_login_required",
            ),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_search_page"
    assert report.safe_reason_code == "liepin_login_required"


def test_drift_smoke_classifies_liepin_risk_page() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_risk_page",
            ),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_search_page"
    assert report.safe_reason_code == "liepin_risk_page"


def test_drift_smoke_classifies_liepin_search_page_changed() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_selector_not_found",
            ),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_search_page"
    assert report.safe_reason_code == "liepin_search_page_changed"


def test_drift_smoke_classifies_liepin_card_extraction_change() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
            cards_result=OpenCliBrowserResult(
                ok=True,
                action="extract_visible_liepin_cards",
                counts={"cards": 0},
                observation={"cards": []},
            ),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_card_extraction"
    assert report.safe_reason_code == "liepin_card_extraction_changed"


def test_drift_smoke_classifies_liepin_card_selector_failure_as_card_extraction_change() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
            cards_result=OpenCliBrowserResult(
                ok=False,
                action="extract_visible_liepin_cards",
                safe_reason_code="liepin_opencli_selector_not_found",
            ),
        ),
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_card_extraction"
    assert report.safe_reason_code == "liepin_card_extraction_changed"


def test_drift_smoke_skips_detail_probe_by_default() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
        ),
        include_detail_probe=False,
    )

    assert report.status == "passed"
    assert [probe.name for probe in report.probes] == [
        "opencli_status",
        "liepin_search_page",
        "liepin_card_extraction",
    ]


def test_drift_smoke_classifies_liepin_filter_change_when_filter_probe_enabled() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
            filter_result=OpenCliBrowserResult(
                ok=False,
                action="apply_liepin_filters",
                safe_reason_code="liepin_opencli_selector_not_found",
            ),
        ),
        include_filter_probe=True,
        filter_probe_native_filters={"city": ["北京"]},
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_filter"
    assert report.safe_reason_code == "liepin_filter_changed"


def test_drift_smoke_classifies_liepin_filter_unapplied_as_filter_change() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
            filter_result=OpenCliBrowserResult(
                ok=False,
                action="apply_liepin_filters",
                safe_reason_code="liepin_opencli_filter_unapplied",
            ),
        ),
        include_filter_probe=True,
        filter_probe_native_filters={"city": ["北京"]},
        include_detail_probe=False,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_filter"
    assert report.safe_reason_code == "liepin_filter_changed"


def test_drift_smoke_classifies_liepin_detail_capture_change_when_detail_probe_enabled() -> None:
    from seektalent.providers.liepin.liepin_drift_smoke import run_liepin_drift_smoke

    report = run_liepin_drift_smoke(
        site=FakeSite(
            status_result=OpenCliBrowserResult(ok=True, action="status"),
            state_result=OpenCliBrowserResult(ok=True, action="state"),
            detail_capture_result=OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code="liepin_opencli_selector_not_found",
            ),
        ),
        include_detail_probe=True,
    )

    assert report.status == "failed"
    assert report.first_failed_probe == "liepin_detail"
    assert report.safe_reason_code == "liepin_detail_changed"
