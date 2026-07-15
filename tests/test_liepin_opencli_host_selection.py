from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from seektalent.opencli_browser.contracts import (
    BrowserControlScope,
    BrowserHostTab,
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
    OpenCliOwnedTab,
    OpenCliTabKind,
)
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
from seektalent.providers.liepin.liepin_site_adapter import (
    LiepinOpenCliSiteConfig,
    LiepinSiteAdapter,
)


@dataclass
class HostAutomation:
    candidates: tuple[BrowserHostTab, ...]
    daemon_enabled: bool = True
    scope_calls: int = 0
    opened: list[tuple[str, str, OpenCliTabKind]] = field(default_factory=list)

    def status(self) -> OpenCliBrowserResult:
        return OpenCliBrowserResult(ok=True, action="status")

    def activate_control_scope(self, control_key: str) -> BrowserControlScope:
        self.scope_calls += 1
        return BrowserControlScope(scope_id=f"scope-{self.scope_calls}", control_key=control_key, fence_token=1)

    def find_host_tabs(self, url_prefix: str) -> tuple[BrowserHostTab, ...]:
        assert url_prefix == "https://h.liepin.com/"
        return self.candidates

    def open_owned_tab(
        self,
        *,
        host_page: str,
        url: str,
        tab_kind: OpenCliTabKind,
    ) -> OpenCliOwnedTab:
        self.opened.append((host_page, url, tab_kind))
        index = len(self.opened)
        return OpenCliOwnedTab(
            tab_token=f"token-{index}",
            session=f"session-{index}",
            page_id=f"owned-{index}",
            tab_kind=tab_kind,
            idle_deadline_at=123456,
        )


def host(
    page_id: str,
    *,
    window_id: int,
    active: bool = False,
    focused: bool = False,
    url: str = "https://h.liepin.com/",
) -> BrowserHostTab:
    return BrowserHostTab(
        page_id=page_id,
        url=url,
        window_id=window_id,
        active=active,
        window_focused=focused,
    )


def site(tmp_path: Path, automation: HostAutomation) -> LiepinSiteAdapter:
    browser_config = OpenCliBrowserConfig(
        command=("seektalent-opencli",),
        session="seektalent-liepin",
        timeout_seconds=30,
        pacing_enabled=False,
    )
    return LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
            lease_dir=tmp_path,
        ),
        automation=automation,  # type: ignore[arg-type]
    )


def test_host_selection_allows_multiple_tabs_in_one_window_and_prefers_unique_active_tab(tmp_path: Path) -> None:
    automation = HostAutomation(
        (
            host("host-b", window_id=10, url="https://h.liepin.com/resume/detail"),
            host("host-a", window_id=10, active=True, url="https://h.liepin.com/"),
        )
    )

    selected = site(tmp_path, automation)._select_liepin_host_tab()

    assert selected.page_id == "host-a"


def test_host_selection_uses_the_only_focused_candidate_window(tmp_path: Path) -> None:
    automation = HostAutomation(
        (
            host("host-a", window_id=10),
            host("host-b", window_id=20, focused=True),
            host("host-c", window_id=20, active=True, focused=True),
        )
    )

    selected = site(tmp_path, automation)._select_liepin_host_tab()

    assert selected.page_id == "host-c"


def test_host_selection_uses_stable_url_and_page_order_without_an_active_candidate(tmp_path: Path) -> None:
    automation = HostAutomation(
        (
            host("host-z", window_id=10, url="https://h.liepin.com/z"),
            host("host-b", window_id=10, url="https://h.liepin.com/a"),
            host("host-a", window_id=10, url="https://h.liepin.com/a"),
        )
    )

    selected = site(tmp_path, automation)._select_liepin_host_tab()

    assert selected.page_id == "host-a"


@pytest.mark.parametrize(
    "candidates, reason",
    [
        ((), "liepin_host_tab_missing"),
        (
            (
                host("http-host", window_id=10, url="http://h.liepin.com/"),
                host("wrong-host", window_id=10, url="https://example.com/"),
            ),
            "liepin_host_tab_missing",
        ),
        (
            (host("host-a", window_id=10), host("host-b", window_id=20)),
            "liepin_host_window_ambiguous",
        ),
        (
            (
                host("host-a", window_id=10, focused=True),
                host("host-b", window_id=20, focused=True),
            ),
            "liepin_host_window_ambiguous",
        ),
    ],
)
def test_host_selection_fails_before_creating_any_tab(
    tmp_path: Path,
    candidates: tuple[BrowserHostTab, ...],
    reason: str,
) -> None:
    automation = HostAutomation(candidates)

    with pytest.raises(OpenCliBrowserError) as captured:
        site(tmp_path, automation)._begin_browser_control_scope()

    assert captured.value.safe_reason_code == reason
    assert automation.opened == []


def test_session_probe_is_read_only_and_missing_host_requires_login(tmp_path: Path) -> None:
    automation = HostAutomation(())

    result = site(tmp_path, automation).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash=None,
    )

    assert result.status == "login_required"
    assert result.safe_reason_code == "liepin_host_tab_missing"
    assert automation.scope_calls == 0
    assert automation.opened == []


def test_scope_uses_one_host_window_for_any_number_of_owned_tabs(tmp_path: Path) -> None:
    automation = HostAutomation((host("host-a", window_id=10, active=True, focused=True),))
    adapter = site(tmp_path, automation)

    adapter._begin_browser_control_scope()
    opened = [adapter.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)]
    opened.extend(
        OpenCliBrowserResult(
            ok=True,
            action="detail",
            private_output=adapter._open_new_liepin_tab(
                url=f"https://h.liepin.com/resume/showresumedetail?index={index}"
            )
            or "",
        )
        for index in range(3)
    )

    assert all(result.ok for result in opened)
    assert len(automation.opened) == 4
    assert all(host_page == "host-a" for host_page, _url, _kind in automation.opened)
    assert [kind for _host, _url, kind in automation.opened] == ["search", "detail", "detail", "detail"]
