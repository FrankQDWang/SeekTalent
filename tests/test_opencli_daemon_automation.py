from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliTabKind,
)
from seektalent.opencli_browser.controlled_tab_lock import (
    CONTROLLED_TAB_HELPER_TIMEOUT_SECONDS,
    install_script,
)
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonResult
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_OWNED_TAB_MISSING,
    OPENCLI_PAGE_NOT_READY,
    OPENCLI_SELECTOR_NOT_FOUND,
    OPENCLI_TIMEOUT,
)


class NoSubprocessCommands:
    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        raise AssertionError(f"unexpected subprocess command: {tuple(argv)}")


class RecordingDaemon:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], float]] = []
        self.closed = False
        self.tab_count = 0
        self.session_pages: dict[str, str] = {}

    def close(self) -> None:
        self.closed = True

    def verify_bridge(self, *, timeout_seconds: float = 2.0) -> Mapping[str, object]:
        return {"ok": True, "extensionConnected": True}

    def command(
        self,
        action: str,
        params: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> OpenCliDaemonResult:
        payload = dict(params)
        self.calls.append((action, payload, timeout_seconds))
        if action == "control":
            return OpenCliDaemonResult(
                "control-1",
                data={"controlKey": payload["controlKey"], "fenceToken": 7},
            )
        if action == "browser-operation":
            operation = payload["operation"]
            page = str(payload.get("page") or "page-1")
            if operation == "state":
                return OpenCliDaemonResult(
                    "state-1",
                    data='URL: https://h.liepin.com/resume/search\n\n[7] button "搜索"',
                    page=page,
                )
            if operation == "find-semantic":
                return OpenCliDaemonResult(
                    "find-1",
                    data={"matches_n": 2, "entries": [{"ref": 7}, {"ref": 8}]},
                    page=page,
                )
            if operation == "evaluate":
                return OpenCliDaemonResult("eval-1", data={"ok": True}, page=page, idle_deadline_at=234567)
            if operation == "find-css":
                return OpenCliDaemonResult(
                    "find-css-1",
                    data={"matches_n": 1, "entries": [{"ref": 9}]},
                    page=page,
                )
            return OpenCliDaemonResult("operation-1", data={"ok": True}, page=page)
        if action == "navigate":
            return OpenCliDaemonResult("navigate-1", data={"url": payload["url"]}, page=str(payload.get("page") or "page-1"))
        if action == "tabs":
            if payload["op"] == "find":
                return OpenCliDaemonResult(
                    "tabs-find-1",
                    data=[
                        {
                            "page": "host-1",
                            "url": "https://h.liepin.com/",
                            "windowId": 11,
                            "active": True,
                            "windowFocused": True,
                        }
                    ],
                )
            if payload["op"] == "list":
                session = str(payload["session"])
                page = self.session_pages.get(session)
                return OpenCliDaemonResult(
                    "tabs-1",
                    data=(
                        [
                            {
                                "page": page,
                                "url": "https://h.liepin.com/resume/search",
                                "active": False,
                            }
                        ]
                        if page is not None
                        else []
                    ),
                    idle_deadline_at=123456 if page is not None else None,
                )
            if payload["op"] == "new":
                self.tab_count += 1
                page = f"owned-{self.tab_count}"
                self.session_pages[str(payload["session"])] = page
                return OpenCliDaemonResult(
                    "tabs-new-1",
                    data={"active": False, "placement": "borrowed-host-window"},
                    page=page,
                    idle_deadline_at=123456,
                )
            self.session_pages.pop(str(payload["session"]), None)
            return OpenCliDaemonResult("tabs-1", data={"outcome": "closed"}, page=str(payload["page"]))
        raise AssertionError(f"unexpected daemon action: {action}")


def automation(daemon: RecordingDaemon) -> OpenCliBrowserAutomation:
    return OpenCliBrowserAutomation(
        config=OpenCliBrowserConfig(
            command=("seektalent-opencli",),
            session="seektalent-liepin",
            timeout_seconds=30,
            pacing_enabled=False,
        ),
        commands=NoSubprocessCommands(),
        daemon=daemon,  # type: ignore[arg-type]
    )


def test_daemon_automation_uses_verified_keepalive_transport_for_normal_actions() -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)

    assert browser.status().ok is True
    assert browser.run_browser_command("state", ()).startswith("URL: https://h.liepin.com")
    assert browser.run_browser_command("click", ("--role", "button", "--name", "搜索"))
    assert browser.run_browser_command("fill", ("--role", "textbox", "--nth", "1", "Python"))
    assert browser.run_browser_command("open", ("https://h.liepin.com/resume/search",))
    assert browser.run_browser_command("tab", ("select", "page-1"))
    assert browser.run_browser_command("tab", ("list",)).startswith("[")
    assert browser.find_css("#resultList .card", limit=20, text_max=1200)
    assert browser.readonly_eval("location.href") == '{"ok": true}'

    actions = [action for action, _params, _timeout in daemon.calls]
    assert actions == [
        "browser-operation",
        "browser-operation",
        "browser-operation",
        "browser-operation",
        "browser-operation",
        "navigate",
        "tabs",
        "tabs",
        "browser-operation",
        "browser-operation",
    ]
    semantic_find = daemon.calls[1][1]
    assert semantic_find["semantic"] == {"role": "button", "name": "搜索"}
    click = daemon.calls[2][1]
    assert click["target"] == "7"
    fill = daemon.calls[4][1]
    assert fill["target"] == "8"
    assert fill["text"] == "Python"


def test_daemon_automation_does_not_fall_back_to_bind_or_unbind_cli_commands() -> None:
    browser = automation(RecordingDaemon())

    for command in ("bind", "unbind"):
        with pytest.raises(OpenCliBrowserError) as raised:
            browser.run_browser_command(command, ())
        assert raised.value.safe_reason_code == "opencli_forbidden_command"


def test_daemon_automation_requires_host_scoped_tab_creation() -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)

    with pytest.raises(OpenCliBrowserError) as raised:
        browser.run_browser_command("tab", ("new", "https://h.liepin.com/resume/detail"))

    assert raised.value.safe_reason_code == "opencli_forbidden_command"
    assert daemon.calls == []


@pytest.mark.parametrize("detail_count", (3, 5, 10))
def test_daemon_automation_reuses_one_detail_tab_in_the_existing_host_window(
    detail_count: int,
) -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)

    scope = browser.activate_control_scope("lane-key")
    host = browser.find_host_tabs("https://h.liepin.com/")[0]
    search_tab, search_reused = browser.acquire_owned_tab(
        host_page=host.page_id,
        url="https://h.liepin.com/resume/search",
        tab_kind="search",
    )
    detail_tabs = [
        browser.acquire_owned_tab(
            host_page=host.page_id,
            url=f"https://h.liepin.com/resume/detail?index={index}",
            tab_kind="detail",
        )
        for index in range(detail_count)
    ]
    browser.readonly_eval("location.href")
    listed = browser.run_browser_command("tab", ("list",))
    calls_before_select = len(daemon.calls)
    browser.run_browser_command("tab", ("select", search_tab.page_id))
    assert len(daemon.calls) == calls_before_select
    browser.readonly_eval("location.href")

    assert scope.fence_token == 7
    assert search_reused is False
    assert [reused for _tab, reused in detail_tabs] == [False] + [True] * (detail_count - 1)
    assert len({tab.page_id for tab, _reused in detail_tabs}) == 1
    assert len({tab.session for tab, _reused in detail_tabs}) == 1
    assert len({tab.tab_token for tab, _reused in detail_tabs}) == 1
    new_tab_calls = [params for action, params, _timeout in daemon.calls if action == "tabs" and params["op"] == "new"]
    assert len(new_tab_calls) == 2
    assert all(params["hostPage"] == "host-1" and params["active"] is False for params in new_tab_calls)
    assert all(params["idleTimeout"] == 60 for params in new_tab_calls)
    assert len(json.loads(listed)) == 2
    assert daemon.calls[-1][1]["page"] == search_tab.page_id
    assert daemon.calls[-1][1]["session"] == search_tab.session
    assert daemon.calls[-1][1]["controlKey"] == "lane-key"
    assert daemon.calls[-1][1]["fenceToken"] == 7


@pytest.mark.parametrize("tab_kind", ("search", "detail"))
def test_daemon_automation_replaces_a_user_closed_tab_once(
    tab_kind: OpenCliTabKind,
) -> None:
    class UserClosedTabDaemon(RecordingDaemon):
        missing_once = True

        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "navigate" and self.missing_once:
                self.missing_once = False
                raise OpenCliBrowserError(OPENCLI_OWNED_TAB_MISSING)
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = UserClosedTabDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    first, _reused = browser.acquire_owned_tab(
        host_page="host-1",
        url=f"https://h.liepin.com/resume/{tab_kind}?index=1",
        tab_kind=tab_kind,
    )

    replacement, reused = browser.acquire_owned_tab(
        host_page="host-1",
        url=f"https://h.liepin.com/resume/{tab_kind}?index=2",
        tab_kind=tab_kind,
    )

    assert reused is False
    assert replacement.page_id != first.page_id
    assert daemon.tab_count == 2
    assert tuple(browser._owned_tabs) == (replacement.page_id,)  # noqa: SLF001


@pytest.mark.parametrize("reason", (OPENCLI_TIMEOUT, OPENCLI_EXTENSION_DISCONNECTED))
def test_daemon_automation_does_not_replace_detail_when_navigation_is_unknown(
    reason: str,
) -> None:
    class UnknownNavigationDaemon(RecordingDaemon):
        fail_navigation = False

        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "navigate" and self.fail_navigation:
                raise OpenCliBrowserError(reason)
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = UnknownNavigationDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    first, _reused = browser.acquire_owned_tab(
        host_page="host-1",
        url="https://h.liepin.com/resume/detail?index=1",
        tab_kind="detail",
    )
    daemon.fail_navigation = True

    with pytest.raises(OpenCliBrowserError) as raised:
        browser.acquire_owned_tab(
            host_page="host-1",
            url="https://h.liepin.com/resume/detail?index=2",
            tab_kind="detail",
        )

    assert raised.value.safe_reason_code == reason
    assert daemon.tab_count == 1
    assert browser._owned_tabs == {first.page_id: first}  # noqa: SLF001


def test_daemon_automation_keeps_failed_retirement_bounded() -> None:
    class DisconnectOnCloseDaemon(RecordingDaemon):
        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "tabs" and params.get("op") == "close":
                raise OpenCliBrowserError(OPENCLI_EXTENSION_DISCONNECTED)
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = DisconnectOnCloseDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    first, _reused = browser.acquire_owned_tab(
        host_page="host-1",
        url="https://h.liepin.com/resume/detail?index=1",
        tab_kind="detail",
    )

    with pytest.raises(OpenCliBrowserError):
        browser.retire_owned_tab(
            "detail",
            safe_reason_code=OPENCLI_PAGE_NOT_READY,
        )
    with pytest.raises(OpenCliBrowserError):
        browser.acquire_owned_tab(
            host_page="host-1",
            url="https://h.liepin.com/resume/detail?index=2",
            tab_kind="detail",
        )

    assert daemon.tab_count == 1
    assert browser._owned_tabs == {first.page_id: first}  # noqa: SLF001
    assert browser._retiring_tabs["detail"].safe_reason_code == OPENCLI_PAGE_NOT_READY  # noqa: SLF001


def test_daemon_automation_requires_confirmed_close_before_replacement() -> None:
    class UnconfirmedCloseDaemon(RecordingDaemon):
        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "tabs" and params.get("op") == "close":
                return OpenCliDaemonResult(
                    "tabs-close-unknown",
                    data={"outcome": "unknown"},
                    page=str(params["page"]),
                )
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = UnconfirmedCloseDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    first, _reused = browser.acquire_owned_tab(
        host_page="host-1",
        url="https://h.liepin.com/resume/detail?index=1",
        tab_kind="detail",
    )

    with pytest.raises(OpenCliBrowserError):
        browser.retire_owned_tab(
            "detail",
            safe_reason_code=OPENCLI_PAGE_NOT_READY,
        )
    with pytest.raises(OpenCliBrowserError):
        browser.acquire_owned_tab(
            host_page="host-1",
            url="https://h.liepin.com/resume/detail?index=2",
            tab_kind="detail",
        )

    assert daemon.tab_count == 1
    assert browser._owned_tabs == {first.page_id: first}  # noqa: SLF001
    assert "detail" in browser._retiring_tabs  # noqa: SLF001


def test_daemon_automation_hundred_mixed_detail_cycles_stay_bounded() -> None:
    class MixedResultDaemon(RecordingDaemon):
        navigation_count = 0

        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "navigate":
                self.navigation_count += 1
                if self.navigation_count % 10 == 0:
                    raise OpenCliBrowserError(OPENCLI_TIMEOUT)
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = MixedResultDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    outcomes: list[str] = []

    for index in range(100):
        try:
            browser.acquire_owned_tab(
                host_page="host-1",
                url=f"https://h.liepin.com/resume/detail?index={index}",
                tab_kind="detail",
            )
        except OpenCliBrowserError as exc:
            outcomes.append(exc.safe_reason_code)
        else:
            outcomes.append("ok")

    assert outcomes.count(OPENCLI_TIMEOUT) == 9
    assert daemon.tab_count == 1
    assert len(browser._owned_tabs) == 1  # noqa: SLF001


def test_daemon_automation_waits_for_owned_page_navigation_url() -> None:
    class DelayedUrlDaemon(RecordingDaemon):
        urls = ["", "about:blank", "https://h.liepin.com/search/getConditionItem#session"]

        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "browser-operation" and params.get("operation") == "get-url":
                payload = dict(params)
                self.calls.append((action, payload, timeout_seconds))
                return OpenCliDaemonResult(
                    "get-url-1",
                    data=self.urls.pop(0),
                    page=str(payload["page"]),
                )
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = DelayedUrlDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    browser.open_owned_tab(
        host_page="host-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        tab_kind="search",
    )

    result = browser.wait_for_page_url(timeout_seconds=1, poll_seconds=0.001)

    assert result.ok is True
    assert result.private_output == "https://h.liepin.com/search/getConditionItem#session"
    get_url_calls = [
        params
        for action, params, _timeout in daemon.calls
        if action == "browser-operation" and params.get("operation") == "get-url"
    ]
    assert len(get_url_calls) == 3


def test_daemon_automation_reports_explicit_page_not_ready_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlankUrlDaemon(RecordingDaemon):
        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "browser-operation" and params.get("operation") == "get-url":
                payload = dict(params)
                self.calls.append((action, payload, timeout_seconds))
                return OpenCliDaemonResult("get-url-1", data="", page=str(payload["page"]))
            return super().command(action, params, timeout_seconds=timeout_seconds)

    moments = iter((0.0, 0.0, 1.0, 2.0))
    delays: list[float] = []
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.monotonic", lambda: next(moments))
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.sleep", delays.append)
    browser = automation(BlankUrlDaemon())
    browser.activate_control_scope("lane-key")
    browser.open_owned_tab(
        host_page="host-1",
        url="https://h.liepin.com/search/getConditionItem#session",
        tab_kind="search",
    )

    result = browser.wait_for_page_url(timeout_seconds=2, poll_seconds=0.1)

    assert result.ok is False
    assert result.safe_reason_code == OPENCLI_PAGE_NOT_READY
    assert delays == [0.1, 0.1]


def test_finishing_scope_keeps_sidecar_owned_tabs_without_requesting_tab_reclaim() -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)

    scope = browser.activate_control_scope("lane-key")
    tab = browser.open_owned_tab(
        host_page="host-1",
        url="https://example.com/search",
        tab_kind="search",
    )
    daemon.calls.clear()

    browser.finish_control_scope()

    assert daemon.calls == []
    assert scope.scope_id
    assert tab.page_id == "owned-1"
    assert browser._control_scope is None  # noqa: SLF001
    assert browser._owned_tabs == {tab.page_id: tab}  # noqa: SLF001


def test_new_scope_can_select_a_sidecar_lifecycle_owned_tab() -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    tab = browser.open_owned_tab(
        host_page="host-1",
        url="https://example.com/search",
        tab_kind="search",
    )
    browser.finish_control_scope()

    browser.activate_control_scope("lane-key")
    selected = browser.select_owned_tab("search")
    browser.readonly_eval("location.href")

    assert selected == tab
    assert daemon.calls[-1][1]["page"] == tab.page_id
    assert daemon.calls[-1][1]["session"] == tab.session


def test_new_sidecar_instance_recovers_only_the_live_extension_owned_tab() -> None:
    daemon = RecordingDaemon()
    first_instance = automation(daemon)
    first_instance.activate_control_scope("lane-key")
    first_instance.open_owned_tab(
        host_page="host-1",
        url="https://example.com/detail-1",
        tab_kind="detail",
    )
    first_instance.finish_control_scope()

    restarted_instance = automation(daemon)
    restarted_instance.activate_control_scope("lane-key")
    recovered = restarted_instance.select_owned_tab("detail")

    assert recovered is not None
    assert recovered.page_id == "owned-1"
    assert recovered.session == "st_owned_detail"
    assert restarted_instance._owned_tabs == {"owned-1": recovered}  # noqa: SLF001
    recover_calls = [
        params
        for action, params, _timeout in daemon.calls
        if action == "tabs" and params.get("op") == "list"
    ]
    assert recover_calls[-1]["session"] == "st_owned_detail"


def test_controlled_tab_lock_uses_dokobot_style_veil_and_double_line_countdown() -> None:
    script = install_script(123456)

    assert "rgb(29 34 39 / 58%)" in script
    assert '<span class="rail"></span>' in script
    assert '<span class="seconds">60s</span>' in script
    assert script.count('<span class="rail"></span>') == 2
    assert 'attachShadow({ mode: "closed" })' in script
    assert 'pointerEvents: "auto"' in script
    assert "123456" in script


def test_controlled_tab_lock_wraps_pointer_actions_and_tracks_extension_deadline() -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    owned_tab = browser.open_owned_tab(
        host_page="host-1",
        url="https://example.com/search",
        tab_kind="search",
    )
    daemon.calls.clear()

    assert owned_tab.idle_deadline_at == 234567

    assert browser.click_ref("7") == '{"ok": true}'

    operations = [
        (params["operation"], params.get("code"), timeout)
        for action, params, timeout in daemon.calls
        if action == "browser-operation"
    ]
    assert [operation for operation, _code, _timeout in operations] == ["evaluate", "click", "evaluate"]
    assert "setAutomationActive(true)" in str(operations[0][1])
    assert "setAutomationActive(false)" in str(operations[2][1])
    assert operations[0][2] == CONTROLLED_TAB_HELPER_TIMEOUT_SECONDS
    assert operations[1][2] == 30
    assert operations[2][2] == CONTROLLED_TAB_HELPER_TIMEOUT_SECONDS
    assert all(params["page"] == owned_tab.page_id for _action, params, _timeout in daemon.calls)
    assert browser._owned_tabs[owned_tab.page_id].idle_deadline_at == 234567  # noqa: SLF001


def test_controlled_tab_lock_is_reinstalled_after_navigation() -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    owned_tab = browser.open_owned_tab(
        host_page="host-1",
        url="https://example.com/search",
        tab_kind="search",
    )
    daemon.calls.clear()

    browser.run_browser_command("open", ("--tab", owned_tab.page_id, "https://example.com/detail"))

    assert [action for action, _params, _timeout in daemon.calls] == ["navigate", "browser-operation"]
    install_call = daemon.calls[1]
    assert install_call[1]["operation"] == "evaluate"
    assert "seektalent-controlled-tab-lock-v1" in str(install_call[1]["code"])
    assert install_call[2] == CONTROLLED_TAB_HELPER_TIMEOUT_SECONDS


def test_controlled_tab_lock_recovers_when_an_action_replaces_the_document() -> None:
    class NavigatingDaemon(RecordingDaemon):
        relock_seen = False

        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if (
                action == "browser-operation"
                and params.get("operation") == "evaluate"
                and "setAutomationActive(false)" in str(params.get("code"))
            ):
                self.relock_seen = True
                payload = dict(params)
                self.calls.append((action, payload, timeout_seconds))
                return OpenCliDaemonResult(
                    "relock-1",
                    data={"installed": False},
                    page=str(payload["page"]),
                    idle_deadline_at=345678,
                )
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = NavigatingDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    browser.open_owned_tab(host_page="host-1", url="https://example.com/search", tab_kind="search")
    daemon.calls.clear()

    browser.click_ref("7")

    operations = [params for action, params, _timeout in daemon.calls if action == "browser-operation"]
    assert daemon.relock_seen is True
    assert [params["operation"] for params in operations] == ["evaluate", "click", "evaluate", "evaluate"]
    assert "seektalent-controlled-tab-lock-v1" in str(operations[-1]["code"])


def test_controlled_tab_lock_failures_do_not_change_the_primary_action_result() -> None:
    class BrokenLockDaemon(RecordingDaemon):
        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "browser-operation" and params.get("operation") == "evaluate":
                super().command(action, params, timeout_seconds=timeout_seconds)
                raise OpenCliBrowserError("opencli_timeout")
            return super().command(action, params, timeout_seconds=timeout_seconds)

    daemon = BrokenLockDaemon()
    browser = automation(daemon)
    browser.activate_control_scope("lane-key")
    owned_tab = browser.open_owned_tab(
        host_page="host-1",
        url="https://example.com/search",
        tab_kind="search",
    )
    daemon.calls.clear()

    assert browser.click_ref("7") == '{"ok": true}'
    assert [params["operation"] for action, params, _timeout in daemon.calls if action == "browser-operation"] == [
        "evaluate",
        "click",
        "evaluate",
    ]
    assert all(
        timeout <= CONTROLLED_TAB_HELPER_TIMEOUT_SECONDS
        for action, params, timeout in daemon.calls
        if action == "browser-operation" and params["operation"] == "evaluate"
    )
    assert browser._owned_tabs[owned_tab.page_id].idle_deadline_at == 123456  # noqa: SLF001


def test_controlled_tab_lock_failure_does_not_replace_the_primary_action_error() -> None:
    class BrokenActionAndLockDaemon(RecordingDaemon):
        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "browser-operation" and params.get("operation") == "evaluate":
                raise OpenCliBrowserError("opencli_timeout")
            if action == "browser-operation" and params.get("operation") == "click":
                raise OpenCliBrowserError(OPENCLI_SELECTOR_NOT_FOUND)
            return super().command(action, params, timeout_seconds=timeout_seconds)

    browser = automation(BrokenActionAndLockDaemon())
    browser.activate_control_scope("lane-key")
    browser.open_owned_tab(host_page="host-1", url="https://example.com/search", tab_kind="search")

    with pytest.raises(OpenCliBrowserError) as raised:
        browser.click_ref("7")

    assert raised.value.safe_reason_code == OPENCLI_SELECTOR_NOT_FOUND


def test_daemon_automation_rejects_malformed_semantic_find_results() -> None:
    class MissingTargetDaemon(RecordingDaemon):
        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            if action == "browser-operation" and params.get("operation") == "find-semantic":
                return OpenCliDaemonResult("find-1", data={"matches_n": 0, "entries": []}, page="page-1")
            return super().command(action, params, timeout_seconds=timeout_seconds)

    with pytest.raises(OpenCliBrowserError) as raised:
        automation(MissingTargetDaemon()).run_browser_command("click", ("--role", "button", "--name", "搜索"))

    assert raised.value.safe_reason_code == "opencli_status_unavailable"
