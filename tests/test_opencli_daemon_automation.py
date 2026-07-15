from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import OpenCliBrowserConfig, OpenCliBrowserError
from seektalent.opencli_browser.controlled_tab_lock import (
    CONTROLLED_TAB_HELPER_TIMEOUT_SECONDS,
    install_script,
)
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonResult
from seektalent.opencli_browser.reason_codes import OPENCLI_SELECTOR_NOT_FOUND


class NoSubprocessCommands:
    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        raise AssertionError(f"unexpected subprocess command: {tuple(argv)}")


class RecordingDaemon:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], float]] = []
        self.closed = False
        self.tab_count = 0

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
                page = str(payload.get("page") or "page-1")
                return OpenCliDaemonResult(
                    "tabs-1",
                    data=[{"page": page, "url": "https://h.liepin.com/resume/search", "active": False}],
                )
            if payload["op"] == "new":
                self.tab_count += 1
                return OpenCliDaemonResult(
                    "tabs-new-1",
                    data={"active": False, "placement": "borrowed-host-window"},
                    page=f"owned-{self.tab_count}",
                    idle_deadline_at=123456,
                )
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


def test_daemon_automation_creates_each_owned_tab_in_the_existing_host_window() -> None:
    daemon = RecordingDaemon()
    browser = automation(daemon)

    scope = browser.activate_control_scope("lane-key")
    host = browser.find_host_tabs("https://h.liepin.com/")[0]
    owned_tabs = [
        browser.open_owned_tab(
            host_page=host.page_id,
            url=(
                "https://h.liepin.com/resume/search"
                if index == 0
                else f"https://h.liepin.com/resume/detail?index={index}"
            ),
            tab_kind="search" if index == 0 else "detail",
        )
        for index in range(4)
    ]
    browser.readonly_eval("location.href")
    listed = browser.run_browser_command("tab", ("list",))
    calls_before_select = len(daemon.calls)
    browser.run_browser_command("tab", ("select", owned_tabs[0].page_id))
    assert len(daemon.calls) == calls_before_select
    browser.readonly_eval("location.href")

    assert scope.fence_token == 7
    assert len({tab.session for tab in owned_tabs}) == 4
    assert len({tab.tab_token for tab in owned_tabs}) == 4
    new_tab_calls = [params for action, params, _timeout in daemon.calls if action == "tabs" and params["op"] == "new"]
    assert len(new_tab_calls) == 4
    assert all(params["hostPage"] == "host-1" and params["active"] is False for params in new_tab_calls)
    assert all(params["idleTimeout"] == 60 for params in new_tab_calls)
    assert len(json.loads(listed)) == 4
    assert daemon.calls[-1][1]["page"] == owned_tabs[0].page_id
    assert daemon.calls[-1][1]["session"] == owned_tabs[0].session
    assert daemon.calls[-1][1]["controlKey"] == "lane-key"
    assert daemon.calls[-1][1]["fenceToken"] == 7


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
