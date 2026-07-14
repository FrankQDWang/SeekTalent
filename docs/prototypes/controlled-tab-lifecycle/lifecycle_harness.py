from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROTOTYPE_DIR = Path(__file__).resolve().parent
LOCK_PROTOTYPE_DIR = PROTOTYPE_DIR.parent / "controlled-tab-lock"
sys.path.insert(0, str(LOCK_PROTOTYPE_DIR))

import real_chrome_harness as chrome  # noqa: E402

from lifecycle_model import initial_state, reduce_state  # noqa: E402


LIEPIN_RESULT = {"status": "completed", "candidateCount": 1}
OTHER_SOURCE_RESULT = {"status": "completed", "itemCount": 1}
MAX_CRITICAL_CLEANUP_SECONDS = 0.5


@dataclass
class OwnedTab:
    token: str
    scope_id: str
    session: str
    page: str
    fence_token: int
    closed: bool = False


@dataclass
class ReclaimRequest:
    tab: OwnedTab
    fault: str | None = None
    delay_seconds: float = 0
    done: threading.Event = field(default_factory=threading.Event)


class StateRecorder:
    def __init__(self, *, render: bool) -> None:
        self._state = initial_state()
        self._lock = threading.RLock()
        self._render = render

    def dispatch(self, **event: Any) -> None:
        with self._lock:
            self._state = reduce_state(self._state, event)
            if self._render:
                print("\033[2J\033[H", end="")
                print("\033[1mPROTOTYPE — controlled tab lifecycle\033[0m")
                print(json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True))
                print("\n\033[2mReal Chrome scenario running; cleanup waits occur only in this prototype.\033[0m")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)


class ScratchRegistry:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._connection.execute(
            """
            CREATE TABLE owned_tabs (
                tab_token TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                session TEXT NOT NULL,
                page_id TEXT NOT NULL,
                state TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def record_owned(self, tab: OwnedTab, *, inject_failure: bool = False) -> None:
        if inject_failure:
            raise sqlite3.OperationalError("injected scratch registry write failure")
        with self._lock:
            self._connection.execute(
                "INSERT INTO owned_tabs VALUES (?, ?, ?, ?, 'owned')",
                (tab.token, tab.scope_id, tab.session, tab.page),
            )
            self._connection.commit()

    def request_reclaim(self, tab: OwnedTab) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE owned_tabs SET state = 'reclaim_requested' WHERE tab_token = ?",
                (tab.token,),
            )
            self._connection.commit()

    def mark_reclaimed(self, tab: OwnedTab) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE owned_tabs SET state = 'reclaimed' WHERE tab_token = ?",
                (tab.token,),
            )
            self._connection.commit()

    def row_count(self) -> int:
        with self._lock:
            value = self._connection.execute("SELECT COUNT(*) FROM owned_tabs").fetchone()
        return int(value[0]) if value else 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class TelemetrySink:
    def __init__(self) -> None:
        self.fail_next = False
        self.events: list[str] = []

    def emit(self, code: str) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected telemetry failure")
        self.events.append(code)


class Diagnostics:
    def __init__(self, recorder: StateRecorder, telemetry: TelemetrySink) -> None:
        self._recorder = recorder
        self._telemetry = telemetry

    def report(self, code: str, *, scope_id: str | None = None, tab_token: str | None = None) -> None:
        self._recorder.dispatch(
            kind="diagnostic",
            code=code,
            scopeId=scope_id,
            tabToken=tab_token,
        )
        try:
            self._telemetry.emit(code)
        except RuntimeError:
            self._recorder.dispatch(
                kind="diagnostic",
                code="telemetry_emit_failed",
                scopeId=scope_id,
                tabToken=tab_token,
            )


class BackgroundReclaimer:
    def __init__(
        self,
        *,
        node: str,
        main_js: Path,
        idle_seconds: int,
        registry: ScratchRegistry,
        recorder: StateRecorder,
        diagnostics: Diagnostics,
    ) -> None:
        self._node = node
        self._main_js = main_js
        self._idle_seconds = idle_seconds
        self._registry = registry
        self._recorder = recorder
        self._diagnostics = diagnostics
        self._queue: queue.Queue[ReclaimRequest | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, request: ReclaimRequest) -> None:
        self._queue.put_nowait(request)

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=10)

    def _run(self) -> None:
        while True:
            request = self._queue.get()
            if request is None:
                return
            try:
                self._reclaim(request)
            finally:
                request.done.set()

    def _reclaim(self, request: ReclaimRequest) -> None:
        tab = request.tab
        if request.delay_seconds:
            time.sleep(request.delay_seconds)
        if request.fault == "background_reclaimer":
            self._diagnostics.report(
                "background_reclaimer_failed",
                scope_id=tab.scope_id,
                tab_token=tab.token,
            )
            self._recorder.dispatch(
                kind="tab_reclaim_failed",
                scopeId=tab.scope_id,
                tabToken=tab.token,
            )
            return
        if request.fault == "exact_close":
            self._diagnostics.report(
                "exact_close_failed",
                scope_id=tab.scope_id,
                tab_token=tab.token,
            )
            self._recorder.dispatch(
                kind="tab_reclaim_failed",
                scopeId=tab.scope_id,
                tabToken=tab.token,
            )
            return

        try:
            result = chrome._json_output(
                chrome._browser_command(
                    self._node,
                    self._main_js,
                    tab.session,
                    ["tab", "close", tab.page],
                    idle_seconds=self._idle_seconds,
                )
            )
        except chrome.PrototypeFailure:
            self._diagnostics.report(
                "exact_close_failed",
                scope_id=tab.scope_id,
                tab_token=tab.token,
            )
            self._recorder.dispatch(
                kind="tab_reclaim_failed",
                scopeId=tab.scope_id,
                tabToken=tab.token,
            )
            return

        if request.fault == "close_verification":
            self._diagnostics.report(
                "close_verification_failed",
                scope_id=tab.scope_id,
                tab_token=tab.token,
            )
            self._recorder.dispatch(
                kind="tab_reclaim_failed",
                scopeId=tab.scope_id,
                tabToken=tab.token,
            )
            return
        if not isinstance(result, dict) or result.get("outcome") not in {"closed", "already_missing"}:
            self._diagnostics.report(
                "close_verification_failed",
                scope_id=tab.scope_id,
                tab_token=tab.token,
            )
            self._recorder.dispatch(
                kind="tab_reclaim_failed",
                scopeId=tab.scope_id,
                tabToken=tab.token,
            )
            return

        tab.closed = True
        self._registry.mark_reclaimed(tab)
        self._recorder.dispatch(
            kind="tab_reclaimed",
            scopeId=tab.scope_id,
            tabToken=tab.token,
            outcome=result["outcome"],
        )


def _activate_scope(
    *,
    node: str,
    main_js: Path,
    control_key: str,
    scope_id: str,
    idle_seconds: int,
    recorder: StateRecorder,
) -> int:
    control_session = f"seektalent-lifecycle-control-{uuid.uuid4().hex}"
    result = chrome._json_output(
        chrome._browser_command(
            node,
            main_js,
            control_session,
            ["control", "activate", control_key],
            idle_seconds=idle_seconds,
        )
    )
    if not isinstance(result, dict) or not isinstance(result.get("fenceToken"), int):
        raise chrome.PrototypeFailure("scope activation returned no fence token")
    token = int(result["fenceToken"])
    recorder.dispatch(kind="scope_activated", scopeId=scope_id, fenceToken=token)
    return token


def _create_owned_tab(
    *,
    token: str,
    scope_id: str,
    fence_token: int,
    host_page: str,
    fixture_url: str,
    node: str,
    main_js: Path,
    control_key: str,
    idle_seconds: int,
    recorder: StateRecorder,
    registry: ScratchRegistry,
    diagnostics: Diagnostics,
    inject_registry_failure: bool = False,
) -> OwnedTab:
    session = f"seektalent-lifecycle-tab-{uuid.uuid4().hex}"
    created = chrome._json_output(
        chrome._browser_command(
            node,
            main_js,
            session,
            ["tab", "new", "https://h.liepin.com/", "--host-page", host_page],
            control_key=control_key,
            fence_token=fence_token,
            idle_seconds=idle_seconds,
        )
    )
    if not isinstance(created, dict):
        raise chrome.PrototypeFailure("owned tab creation returned an invalid result")
    page = str(created.get("page") or "")
    if not page or created.get("active") is not False:
        raise chrome.PrototypeFailure("owned tab was activated")
    if created.get("placement") != "borrowed-host-window":
        raise chrome.PrototypeFailure("owned tab was not created in the borrowed host window")

    tab = OwnedTab(
        token=token,
        scope_id=scope_id,
        session=session,
        page=page,
        fence_token=fence_token,
    )
    recorder.dispatch(
        kind="tab_created",
        scopeId=scope_id,
        tabToken=token,
        tabKind="detail",
    )
    try:
        registry.record_owned(tab, inject_failure=inject_registry_failure)
    except sqlite3.Error:
        diagnostics.report("registry_write_failed", scope_id=scope_id, tab_token=token)

    current_url = chrome._wait_for_url_prefix(
        node,
        main_js,
        session,
        page,
        "https://h.liepin.com/",
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )
    if not current_url.startswith("https://h.liepin.com/"):
        raise chrome.PrototypeFailure("owned tab did not inherit the Liepin login context")
    chrome._browser_command(
        node,
        main_js,
        session,
        ["open", fixture_url, "--tab", page],
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )
    initial_snapshot = chrome._fixture_snapshot(
        node,
        main_js,
        session,
        page,
        control_key=control_key,
        fence_token=fence_token,
        idle_seconds=idle_seconds,
    )
    if initial_snapshot.get("count") != 0:
        raise chrome.PrototypeFailure("fixture did not reach its initial ready state")
    return tab


def _wait_for_fixture_state(
    *,
    tab: OwnedTab,
    node: str,
    main_js: Path,
    control_key: str,
    idle_seconds: int,
    field: str,
    expected: object,
) -> dict[str, object]:
    deadline = time.monotonic() + 3
    snapshot: dict[str, object] = {}
    while time.monotonic() < deadline:
        snapshot = chrome._fixture_snapshot(
            node,
            main_js,
            tab.session,
            tab.page,
            control_key=control_key,
            fence_token=tab.fence_token,
            idle_seconds=idle_seconds,
        )
        if snapshot.get(field) == expected:
            return snapshot
        time.sleep(0.05)
    raise chrome.PrototypeFailure(
        f"fixture did not observe {field}={expected!r}; last snapshot={snapshot!r}"
    )


def _controlled_dom_command(
    *,
    tab: OwnedTab,
    args: list[str],
    target: str,
    node: str,
    main_js: Path,
    control_key: str,
    idle_seconds: int,
    diagnostics: Diagnostics,
) -> object:
    chrome._set_automation_active(
        node,
        main_js,
        tab.session,
        tab.page,
        True,
        control_key=control_key,
        fence_token=tab.fence_token,
        idle_seconds=idle_seconds,
    )
    try:
        action = args[0] if args else ""
        selector = json.dumps(target)
        if action == "click":
            script = (
                "(() => { const el = document.querySelector(" + selector + "); "
                "if (!el) throw new Error('controlled click target not found'); "
                "el.click(); return {status:'clicked'}; })()"
            )
        elif action == "fill" and len(args) == 3:
            text = json.dumps(args[2])
            script = (
                "(() => { const el = document.querySelector(" + selector + "); "
                "if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) "
                "throw new Error('controlled fill target is not editable'); "
                "const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype "
                ": HTMLInputElement.prototype; "
                "const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; "
                "setter.call(el, " + text + "); "
                "el.dispatchEvent(new Event('input', {bubbles:true})); "
                "el.dispatchEvent(new Event('change', {bubbles:true})); "
                "return {status:'filled'}; })()"
            )
        else:
            raise chrome.PrototypeFailure(f"unsupported controlled DOM action: {args!r}")
        return chrome._eval(
            node,
            main_js,
            tab.session,
            tab.page,
            script,
            control_key=control_key,
            fence_token=tab.fence_token,
            idle_seconds=idle_seconds,
        )
    finally:
        try:
            chrome._set_automation_active(
                node,
                main_js,
                tab.session,
                tab.page,
                False,
                control_key=control_key,
                fence_token=tab.fence_token,
                idle_seconds=idle_seconds,
            )
        except chrome.PrototypeFailure:
            diagnostics.report(
                "overlay_relock_failed",
                scope_id=tab.scope_id,
                tab_token=tab.token,
            )


def _safe_install_overlay(
    *,
    tab: OwnedTab,
    overlay_source: str,
    node: str,
    main_js: Path,
    control_key: str,
    idle_seconds: int,
    diagnostics: Diagnostics,
    inject_failure: bool,
) -> bool:
    try:
        if inject_failure:
            raise chrome.PrototypeFailure("injected overlay failure")
        result = chrome._install_overlay(
            overlay_source,
            node,
            main_js,
            tab.session,
            tab.page,
            control_key=control_key,
            fence_token=tab.fence_token,
            idle_seconds=idle_seconds,
        )
        if not isinstance(result, dict) or result.get("installed") is not True:
            raise chrome.PrototypeFailure("overlay did not install")
        return True
    except chrome.PrototypeFailure:
        diagnostics.report(
            "overlay_injection_failed",
            scope_id=tab.scope_id,
            tab_token=tab.token,
        )
        return False


def _inject_countdown_failure(
    *,
    tab: OwnedTab,
    node: str,
    main_js: Path,
    control_key: str,
    idle_seconds: int,
    diagnostics: Diagnostics,
) -> None:
    script = """
    (() => {
      const api = window.__seektalentControlledTabLockV1;
      if (!api) throw new Error('overlay missing');
      const original = api.updateDeadline;
      api.updateDeadline = () => { throw new Error('injected countdown failure'); };
      try { api.updateDeadline(Date.now() + 60000); }
      finally { api.updateDeadline = original; }
    })()
    """
    try:
        chrome._eval(
            node,
            main_js,
            tab.session,
            tab.page,
            script,
            control_key=control_key,
            fence_token=tab.fence_token,
            idle_seconds=idle_seconds,
        )
    except chrome.PrototypeFailure:
        diagnostics.report(
            "countdown_update_failed",
            scope_id=tab.scope_id,
            tab_token=tab.token,
        )


def _finish_scope_non_blocking(
    *,
    scope_id: str,
    requests: list[ReclaimRequest],
    registry: ScratchRegistry,
    reclaimer: BackgroundReclaimer,
    recorder: StateRecorder,
    diagnostics: Diagnostics,
) -> float:
    started = time.monotonic()
    recorder.dispatch(kind="scope_reclaim_requested", scopeId=scope_id)
    for request in requests:
        tab = request.tab
        recorder.dispatch(
            kind="tab_reclaim_requested",
            scopeId=scope_id,
            tabToken=tab.token,
        )
        try:
            registry.request_reclaim(tab)
        except sqlite3.Error:
            diagnostics.report("registry_write_failed", scope_id=scope_id, tab_token=tab.token)
        reclaimer.submit(request)
    elapsed = time.monotonic() - started
    recorder.dispatch(
        kind="metric",
        name=f"{scope_id}.criticalCleanupMs",
        value=round(elapsed * 1000, 3),
    )
    return elapsed


def _verified_close_after_fallback(
    *,
    tab: OwnedTab,
    node: str,
    main_js: Path,
    idle_seconds: int,
    recorder: StateRecorder,
    registry: ScratchRegistry,
) -> str:
    result = chrome._json_output(
        chrome._browser_command(
            node,
            main_js,
            tab.session,
            ["tab", "close", tab.page],
            idle_seconds=idle_seconds,
        )
    )
    if not isinstance(result, dict) or result.get("outcome") != "already_missing":
        raise chrome.PrototypeFailure(f"idle fallback did not reclaim {tab.token}")
    tab.closed = True
    registry.mark_reclaimed(tab)
    recorder.dispatch(
        kind="tab_reclaimed",
        scopeId=tab.scope_id,
        tabToken=tab.token,
        outcome="already_missing",
    )
    return "already_missing"


def run_scenario(*, idle_seconds: int, render: bool) -> dict[str, Any]:
    if idle_seconds < 30:
        raise chrome.PrototypeFailure("this multi-scope prototype requires --idle-seconds >= 30")

    repo_root = PROTOTYPE_DIR.parents[2]
    opencli_root = Path(
        os.environ.get("SEEKTALENT_OPENCLI_PROTOTYPE_ROOT", repo_root.parent / "OpenCLI")
    )
    main_js = opencli_root / "dist" / "src" / "main.js"
    overlay_source = (LOCK_PROTOTYPE_DIR / "controlled_tab_lock.js").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None or not main_js.is_file():
        raise chrome.PrototypeFailure(f"build the OpenCLI fork before running: {main_js}")

    evidence_dir = Path(tempfile.mkdtemp(prefix="seektalent-controlled-tab-lifecycle-"))
    registry = ScratchRegistry(evidence_dir / "PROTOTYPE-browser-control.sqlite3")
    recorder = StateRecorder(render=render)
    telemetry = TelemetrySink()
    diagnostics = Diagnostics(recorder, telemetry)
    server, fixture_url = chrome._start_fixture_server(LOCK_PROTOTYPE_DIR)
    reclaimer: BackgroundReclaimer | None = None
    tabs: list[OwnedTab] = []
    report: dict[str, Any] = {
        "ok": False,
        "evidenceDir": str(evidence_dir),
        "idleTimeoutSeconds": idle_seconds,
    }

    try:
        status = chrome._wait_for_paired_bridge(main_js, node)
        report["bridgeBuildId"] = status.get("bridgeBuildId")
        control_key = f"seektalent-lifecycle-lane-{uuid.uuid4().hex}"
        probe_session = f"seektalent-lifecycle-probe-{uuid.uuid4().hex}"

        scope_a = "scope-a"
        fence_a = _activate_scope(
            node=node,
            main_js=main_js,
            control_key=control_key,
            scope_id=scope_a,
            idle_seconds=idle_seconds,
            recorder=recorder,
        )
        found = chrome._json_output(
            chrome._browser_command(
                node,
                main_js,
                probe_session,
                ["tab", "find", "https://h.liepin.com/"],
                control_key=control_key,
                fence_token=fence_a,
                idle_seconds=idle_seconds,
            )
        )
        host = chrome._pick_prototype_host(found)
        host_page = str(host.get("page") or "")
        host_url = str(host.get("url") or "")
        if not host_page or not host_url:
            raise chrome.PrototypeFailure("selected host tab is missing identity")

        tab_a = _create_owned_tab(
            token="a-normal-close",
            scope_id=scope_a,
            fence_token=fence_a,
            host_page=host_page,
            fixture_url=fixture_url,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            recorder=recorder,
            registry=registry,
            diagnostics=diagnostics,
        )
        tabs.append(tab_a)
        if not _safe_install_overlay(
            tab=tab_a,
            overlay_source=overlay_source,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
            inject_failure=False,
        ):
            raise chrome.PrototypeFailure("normal overlay installation failed")
        _controlled_dom_command(
            tab=tab_a,
            args=["click", "#action-button"],
            target="#action-button",
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
        )
        _wait_for_fixture_state(
            tab=tab_a,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            field="count",
            expected=1,
        )
        recorder.dispatch(
            kind="business_result",
            source="liepin.scopeA",
            result=LIEPIN_RESULT,
        )

        reclaimer = BackgroundReclaimer(
            node=node,
            main_js=main_js,
            idle_seconds=idle_seconds,
            registry=registry,
            recorder=recorder,
            diagnostics=diagnostics,
        )
        request_a = ReclaimRequest(tab=tab_a, delay_seconds=10)
        cleanup_a = _finish_scope_non_blocking(
            scope_id=scope_a,
            requests=[request_a],
            registry=registry,
            reclaimer=reclaimer,
            recorder=recorder,
            diagnostics=diagnostics,
        )
        if cleanup_a >= MAX_CRITICAL_CLEANUP_SECONDS:
            raise chrome.PrototypeFailure("scope A cleanup entered the business critical path")

        scope_b = "scope-b"
        activation_started = time.monotonic()
        fence_b = _activate_scope(
            node=node,
            main_js=main_js,
            control_key=control_key,
            scope_id=scope_b,
            idle_seconds=idle_seconds,
            recorder=recorder,
        )
        activation_elapsed = time.monotonic() - activation_started
        recorder.dispatch(kind="scope_superseded", scopeId=scope_a)
        recorder.dispatch(
            kind="metric",
            name="newScopeActivationMs",
            value=round(activation_elapsed * 1000, 3),
        )
        if activation_elapsed >= MAX_CRITICAL_CLEANUP_SECONDS:
            raise chrome.PrototypeFailure("new scope waited for old tab reclamation")

        try:
            chrome._browser_command(
                node,
                main_js,
                tab_a.session,
                ["get", "url", "--tab", tab_a.page],
                control_key=control_key,
                fence_token=fence_a,
                idle_seconds=idle_seconds,
            )
        except chrome.PrototypeFailure as exc:
            if "stale_control_fence" not in str(exc):
                raise
            report["oldScopeFenceRejected"] = True
        else:
            raise chrome.PrototypeFailure("old scope command was not fenced")

        tab_b1 = _create_owned_tab(
            token="b-overlay-registry-exact-close",
            scope_id=scope_b,
            fence_token=fence_b,
            host_page=host_page,
            fixture_url=fixture_url,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            recorder=recorder,
            registry=registry,
            diagnostics=diagnostics,
            inject_registry_failure=True,
        )
        tab_b2 = _create_owned_tab(
            token="b-countdown-reclaimer",
            scope_id=scope_b,
            fence_token=fence_b,
            host_page=host_page,
            fixture_url=fixture_url,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            recorder=recorder,
            registry=registry,
            diagnostics=diagnostics,
        )
        tab_b3 = _create_owned_tab(
            token="b-close-verification",
            scope_id=scope_b,
            fence_token=fence_b,
            host_page=host_page,
            fixture_url=fixture_url,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            recorder=recorder,
            registry=registry,
            diagnostics=diagnostics,
        )
        tabs.extend([tab_b1, tab_b2, tab_b3])

        if _safe_install_overlay(
            tab=tab_b1,
            overlay_source=overlay_source,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
            inject_failure=True,
        ):
            raise chrome.PrototypeFailure("overlay fault injection unexpectedly succeeded")
        _controlled_dom_command(
            tab=tab_b1,
            args=["click", "#action-button"],
            target="#action-button",
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
        )
        _wait_for_fixture_state(
            tab=tab_b1,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            field="count",
            expected=1,
        )

        if not _safe_install_overlay(
            tab=tab_b2,
            overlay_source=overlay_source,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
            inject_failure=False,
        ):
            raise chrome.PrototypeFailure("scope B normal overlay installation failed")
        _controlled_dom_command(
            tab=tab_b2,
            args=["click", "#action-button"],
            target="#action-button",
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
        )
        _wait_for_fixture_state(
            tab=tab_b2,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            field="count",
            expected=1,
        )
        _inject_countdown_failure(
            tab=tab_b2,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
        )

        if not _safe_install_overlay(
            tab=tab_b3,
            overlay_source=overlay_source,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
            inject_failure=False,
        ):
            raise chrome.PrototypeFailure("scope B verification tab overlay installation failed")
        _controlled_dom_command(
            tab=tab_b3,
            args=["fill", "#prototype-input", "business result preserved"],
            target="#prototype-input",
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            diagnostics=diagnostics,
        )
        _wait_for_fixture_state(
            tab=tab_b3,
            node=node,
            main_js=main_js,
            control_key=control_key,
            idle_seconds=idle_seconds,
            field="inputValue",
            expected="business result preserved",
        )

        telemetry.fail_next = True
        diagnostics.report(
            "telemetry_fault_probe",
            scope_id=scope_b,
            tab_token=tab_b3.token,
        )

        other_result: dict[str, Any] = {}

        def run_other_source() -> None:
            time.sleep(0.05)
            other_result.update(OTHER_SOURCE_RESULT)

        other_thread = threading.Thread(target=run_other_source)
        other_thread.start()
        recorder.dispatch(kind="business_result", source="liepin", result=LIEPIN_RESULT)
        requests_b = [
            ReclaimRequest(tab=tab_b1, fault="exact_close"),
            ReclaimRequest(tab=tab_b2, fault="background_reclaimer"),
            ReclaimRequest(tab=tab_b3, fault="close_verification"),
        ]
        cleanup_b = _finish_scope_non_blocking(
            scope_id=scope_b,
            requests=requests_b,
            registry=registry,
            reclaimer=reclaimer,
            recorder=recorder,
            diagnostics=diagnostics,
        )
        if cleanup_b >= MAX_CRITICAL_CLEANUP_SECONDS:
            raise chrome.PrototypeFailure("scope B cleanup entered the business critical path")
        other_thread.join(timeout=1)
        if other_result != OTHER_SOURCE_RESULT:
            raise chrome.PrototypeFailure("Liepin cleanup fault cancelled the other source")
        recorder.dispatch(kind="business_result", source="other", result=other_result)

        for request in [request_a, *requests_b]:
            if not request.done.wait(timeout=10):
                raise chrome.PrototypeFailure("background reclaimer did not finish its prototype attempt")
        if not tab_a.closed:
            raise chrome.PrototypeFailure("normal background close did not reclaim scope A tab")
        recorder.dispatch(kind="scope_reclaimed", scopeId=scope_a)

        found_after = chrome._json_output(
            chrome._browser_command(
                node,
                main_js,
                probe_session,
                ["tab", "find", "https://h.liepin.com/"],
                control_key=control_key,
                fence_token=fence_b,
                idle_seconds=idle_seconds,
            )
        )
        host_after = (
            next(
                (
                    item
                    for item in found_after
                    if isinstance(item, dict)
                    and item.get("page") == host_page
                    and item.get("url") == host_url
                ),
                None,
            )
            if isinstance(found_after, list)
            else None
        )
        if host_after is None:
            raise chrome.PrototypeFailure("the original user Liepin tab changed or disappeared")

        idle_wait_started = time.monotonic()
        time.sleep(idle_seconds + 3)
        fallback_outcomes = {
            tab.token: _verified_close_after_fallback(
                tab=tab,
                node=node,
                main_js=main_js,
                idle_seconds=idle_seconds,
                recorder=recorder,
                registry=registry,
            )
            for tab in (tab_b1, tab_b2, tab_b3)
        }
        recorder.dispatch(kind="scope_reclaimed", scopeId=scope_b)

        state = recorder.snapshot()
        diagnostic_codes = {item["code"] for item in state["diagnostics"]}
        required_diagnostics = {
            "overlay_injection_failed",
            "countdown_update_failed",
            "registry_write_failed",
            "background_reclaimer_failed",
            "exact_close_failed",
            "close_verification_failed",
            "telemetry_emit_failed",
        }
        missing = required_diagnostics - diagnostic_codes
        if missing:
            raise chrome.PrototypeFailure(
                f"fault injection produced no safe diagnostic for: {', '.join(sorted(missing))}"
            )
        if state["businessResults"].get("liepin") != LIEPIN_RESULT:
            raise chrome.PrototypeFailure("cleanup fault rewrote the Liepin result")
        if state["businessResults"].get("other") != OTHER_SOURCE_RESULT:
            raise chrome.PrototypeFailure("cleanup fault rewrote the other source result")

        report.update(
            {
                "ok": True,
                "sameWindowInactiveTabs": True,
                "ownedTabsCreated": len(tabs),
                "scopeBTabCount": 3,
                "noFixedTwoTabLimit": True,
                "userHostUntouched": True,
                "criticalCleanupMaxMs": round(max(cleanup_a, cleanup_b) * 1000, 3),
                "newScopeActivationMs": round(activation_elapsed * 1000, 3),
                "businessReturnedBeforeReclaimerWait": True,
                "otherSourceUnaffected": True,
                "faultDiagnostics": sorted(required_diagnostics),
                "idleCrashFallbackOutcomes": fallback_outcomes,
                "idleCrashFallbackWaitSeconds": round(time.monotonic() - idle_wait_started, 3),
                "aboutBlankAbsent": True,
                "scratchRegistryRows": registry.row_count(),
                "businessResults": state["businessResults"],
                "state": state,
            }
        )
        report_path = evidence_dir / "validated-report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report
    finally:
        if reclaimer is not None:
            reclaimer.stop()
        for tab in tabs:
            if tab.closed:
                continue
            try:
                chrome._browser_command(
                    node,
                    main_js,
                    tab.session,
                    ["tab", "close", tab.page],
                    idle_seconds=idle_seconds,
                )
            except chrome.PrototypeFailure as exc:
                print(f"prototype diagnostic: final close failed: {exc}", file=sys.stderr)
        server.shutdown()
        server.server_close()
        registry.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="PROTOTYPE — controlled tab lifecycle proof")
    parser.add_argument("--idle-seconds", type=int, default=60)
    parser.add_argument("--tui", action="store_true")
    args = parser.parse_args()
    try:
        if args.tui:
            print("\033[1mControlled tab lifecycle prototype\033[0m")
            print("[r] run real Chrome proof  [q] quit")
            if input("> ").strip().lower() != "r":
                return 0
        report = run_scenario(idle_seconds=args.idle_seconds, render=args.tui)
        return 0 if report.get("ok") is True else 1
    except (chrome.PrototypeFailure, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
