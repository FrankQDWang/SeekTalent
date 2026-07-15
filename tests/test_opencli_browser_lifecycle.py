from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from seektalent.opencli_browser.contracts import (
    BrowserControlScope,
    OpenCliOwnedTab,
    OpenCliTabCloseResult,
)
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonResult
from seektalent.opencli_browser.lifecycle import (
    OPENCLI_RECLAIM_CLOSE_TIMEOUT_SECONDS,
    BrowserControlLifecycle,
    OpenCliDaemonOwnedTabCloser,
)
from seektalent.opencli_browser.lifecycle_registry import BrowserControlRegistry


def _scope(scope_id: str = "scope-1") -> BrowserControlScope:
    return BrowserControlScope(scope_id=scope_id, control_key="private-lane-key", fence_token=1)


def _tab(token: str = "tab-1", *, page: str = "page-1") -> OpenCliOwnedTab:
    return OpenCliOwnedTab(
        tab_token=token,
        session=f"session-{token}",
        page_id=page,
        tab_kind="detail",
        idle_deadline_at=123456,
    )


def _record_owned(registry: BrowserControlRegistry, scope: BrowserControlScope, tab: OpenCliOwnedTab) -> None:
    registry.record_scope(scope)
    registry.record_tab_allocation(
        scope,
        tab_token=tab.tab_token,
        session=tab.session,
        tab_kind=tab.tab_kind,
    )
    registry.record_owned_tab(scope, tab)


def _submit_owned(lifecycle: BrowserControlLifecycle, scope: BrowserControlScope, tab: OpenCliOwnedTab) -> None:
    lifecycle.record_scope(scope)
    lifecycle.record_tab_allocation(
        scope,
        tab_token=tab.tab_token,
        session=tab.session,
        tab_kind=tab.tab_kind,
    )
    lifecycle.record_owned_tab(scope, tab)


def _wait_until(condition, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met before timeout")
        time.sleep(0.005)


def _tab_state(path: Path, token: str) -> tuple[object, ...] | None:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT state, close_outcome, last_error_code FROM browser_owned_tabs WHERE tab_token = ?",
            (token,),
        ).fetchone()


class SuccessfulCloser:
    def __init__(self) -> None:
        self.calls: list[OpenCliOwnedTab] = []

    def close_tab(self, tab: OpenCliOwnedTab) -> OpenCliTabCloseResult:
        self.calls.append(tab)
        return OpenCliTabCloseResult(tab_token=tab.tab_token, outcome="closed", verified=True)


def test_registry_records_only_hashed_lane_identity_and_no_url(tmp_path: Path) -> None:
    path = tmp_path / "browser-control.sqlite3"
    registry = BrowserControlRegistry(path)
    scope = _scope()
    tab = _tab()

    _record_owned(registry, scope, tab)
    registry.record_idle_deadline(
        OpenCliOwnedTab(
            tab_token=tab.tab_token,
            session=tab.session,
            page_id=tab.page_id,
            tab_kind=tab.tab_kind,
            idle_deadline_at=654321,
        )
    )

    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
    assert registry.ready is True
    assert "private-lane-key" not in dump
    assert "https://" not in dump
    assert "654321" in dump


def test_registry_failure_is_fail_open(tmp_path: Path) -> None:
    invalid_path = tmp_path / "registry-directory"
    invalid_path.mkdir()
    registry = BrowserControlRegistry(invalid_path)

    registry.record_scope(_scope())
    registry.record_idle_deadline(_tab())

    assert registry.ready is False
    assert registry.pending_tabs() == ()


def test_reclaim_submission_returns_without_waiting_for_close(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingCloser:
        def close_tab(self, tab: OpenCliOwnedTab) -> OpenCliTabCloseResult:
            entered.set()
            release.wait()
            return OpenCliTabCloseResult(tab_token=tab.tab_token, outcome="closed", verified=True)

    registry = BrowserControlRegistry(tmp_path / "browser-control.sqlite3")
    lifecycle = BrowserControlLifecycle(registry=registry, closer=BlockingCloser())
    scope = _scope()
    tab = _tab()
    _submit_owned(lifecycle, scope, tab)

    started = time.perf_counter()
    lifecycle.request_reclaim(scope, (tab,))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05
    assert entered.wait(1)
    assert _tab_state(registry.path, tab.tab_token) == ("reclaim_requested", None, None)
    release.set()
    _wait_until(lambda: _tab_state(registry.path, tab.tab_token) == ("reclaimed", "closed", None))


def test_registry_work_is_also_off_the_run_path(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingRegistry(BrowserControlRegistry):
        def record_scope(self, scope: BrowserControlScope) -> None:
            del scope
            entered.set()
            release.wait()

    registry = BlockingRegistry(tmp_path / "browser-control.sqlite3")
    lifecycle = BrowserControlLifecycle(registry=registry, closer=SuccessfulCloser())

    started = time.perf_counter()
    lifecycle.record_scope(_scope())
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05
    assert entered.wait(1)
    release.set()


def test_one_close_failure_does_not_stop_the_next_close(tmp_path: Path) -> None:
    calls: list[str] = []

    class FirstCloseRaises:
        def close_tab(self, tab: OpenCliOwnedTab) -> OpenCliTabCloseResult:
            calls.append(tab.tab_token)
            if len(calls) == 1:
                raise RuntimeError("close implementation failed")
            return OpenCliTabCloseResult(tab_token=tab.tab_token, outcome="closed", verified=True)

    registry = BrowserControlRegistry(tmp_path / "browser-control.sqlite3")
    lifecycle = BrowserControlLifecycle(registry=registry, closer=FirstCloseRaises())
    scope = _scope()
    first = _tab("tab-1", page="page-1")
    second = _tab("tab-2", page="page-2")
    _submit_owned(lifecycle, scope, first)
    lifecycle.record_tab_allocation(
        scope,
        tab_token=second.tab_token,
        session=second.session,
        tab_kind=second.tab_kind,
    )
    lifecycle.record_owned_tab(scope, second)
    lifecycle.request_reclaim(scope, (first, second))

    _wait_until(lambda: len(calls) == 2)
    _wait_until(lambda: _tab_state(registry.path, second.tab_token) == ("reclaimed", "closed", None))
    assert _tab_state(registry.path, first.tab_token) == (
        "reclaim_failed",
        "failed",
        "opencli_status_unavailable",
    )


def test_failed_close_is_not_retried_by_the_same_lifecycle(tmp_path: Path) -> None:
    called = threading.Event()
    call_count = 0

    class FailingCloser:
        def close_tab(self, tab: OpenCliOwnedTab) -> OpenCliTabCloseResult:
            nonlocal call_count
            call_count += 1
            called.set()
            return OpenCliTabCloseResult(
                tab_token=tab.tab_token,
                outcome="failed",
                verified=False,
                error_code="close_failed",
            )

    registry = BrowserControlRegistry(tmp_path / "browser-control.sqlite3")
    lifecycle = BrowserControlLifecycle(registry=registry, closer=FailingCloser())
    scope = _scope()
    tab = _tab()
    _submit_owned(lifecycle, scope, tab)

    lifecycle.request_reclaim(scope, (tab,))

    assert called.wait(1)
    _wait_until(lambda: _tab_state(registry.path, tab.tab_token) == ("reclaim_failed", "failed", "close_failed"))
    time.sleep(0.05)
    assert call_count == 1


def test_startup_recovers_owned_tab_but_not_previous_failed_close(tmp_path: Path) -> None:
    path = tmp_path / "browser-control.sqlite3"
    registry = BrowserControlRegistry(path)
    owned_scope = _scope("owned-scope")
    failed_scope = BrowserControlScope(scope_id="failed-scope", control_key="other-lane", fence_token=2)
    owned = _tab("owned", page="owned-page")
    failed = _tab("failed", page="failed-page")
    _record_owned(registry, owned_scope, owned)
    _record_owned(registry, failed_scope, failed)
    registry.record_reclaim_result(
        failed_scope.scope_id,
        OpenCliTabCloseResult(
            tab_token=failed.tab_token,
            outcome="failed",
            verified=False,
            error_code="close_failed",
        ),
    )
    closer = SuccessfulCloser()

    BrowserControlLifecycle(registry=BrowserControlRegistry(path), closer=closer)

    _wait_until(lambda: [tab.tab_token for tab in closer.calls] == ["owned"])
    assert _tab_state(path, owned.tab_token) == ("reclaimed", "closed", None)
    assert _tab_state(path, failed.tab_token) == ("reclaim_failed", "failed", "close_failed")


def test_daemon_closer_sends_exact_owned_page_and_requires_verified_readback() -> None:
    class Daemon:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object], float]] = []

        def command(
            self,
            action: str,
            params: Mapping[str, object],
            *,
            timeout_seconds: float,
        ) -> OpenCliDaemonResult:
            self.calls.append((action, dict(params), timeout_seconds))
            return OpenCliDaemonResult(
                "close-1",
                data={"requested": "page-1", "outcome": "closed", "verified": True},
            )

    daemon = Daemon()
    result = OpenCliDaemonOwnedTabCloser(daemon).close_tab(_tab())  # type: ignore[arg-type]

    assert result == OpenCliTabCloseResult(tab_token="tab-1", outcome="closed", verified=True)
    assert daemon.calls == [
        (
            "tabs",
            {"op": "close", "session": "session-tab-1", "surface": "browser", "page": "page-1"},
            OPENCLI_RECLAIM_CLOSE_TIMEOUT_SECONDS,
        )
    ]
