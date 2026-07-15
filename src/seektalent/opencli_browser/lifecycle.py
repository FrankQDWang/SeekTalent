from __future__ import annotations

import hashlib
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from seektalent.browser_bridge_manifest import BrowserBridgeRequirement
from seektalent.opencli_browser.contracts import (
    BrowserControlScope,
    OpenCliBrowserError,
    OpenCliOwnedTab,
    OpenCliTabCloseOutcome,
    OpenCliTabCloseResult,
    OpenCliTabKind,
)
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonClient
from seektalent.opencli_browser.fault_isolation import isolated_call
from seektalent.opencli_browser.lifecycle_registry import BrowserControlRegistry
from seektalent.opencli_browser.reason_codes import OPENCLI_STATUS_UNAVAILABLE


OPENCLI_OWNED_TAB_IDLE_SECONDS = 60
OPENCLI_RECLAIM_CLOSE_TIMEOUT_SECONDS = 2.0

_LIFECYCLE_QUEUE_CAPACITY = 256
_LOGGER = logging.getLogger(__name__)
_SHARED_LIFECYCLES: dict[
    tuple[Path, BrowserBridgeRequirement],
    BrowserControlLifecycle,
] = {}
_SHARED_LIFECYCLES_LOCK = threading.Lock()


def browser_control_key(
    *,
    source_kind: str,
    browser_profile_id: str,
    provider_account_hash: str,
) -> str:
    parts = (source_kind.strip(), browser_profile_id.strip(), provider_account_hash.strip())
    if not all(parts):
        raise ValueError("browser control lane identity must be complete")
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"seektalent-browser-control-{digest}"


class OwnedTabCloser(Protocol):
    def close_tab(self, tab: OpenCliOwnedTab) -> OpenCliTabCloseResult: ...


class OpenCliDaemonOwnedTabCloser:
    def __init__(self, daemon: OpenCliDaemonClient) -> None:
        self._daemon = daemon

    def close_tab(self, tab: OpenCliOwnedTab) -> OpenCliTabCloseResult:
        try:
            response = self._daemon.command(
                "tabs",
                {
                    "op": "close",
                    "session": tab.session,
                    "surface": "browser",
                    "page": tab.page_id,
                },
                timeout_seconds=OPENCLI_RECLAIM_CLOSE_TIMEOUT_SECONDS,
            )
        except OpenCliBrowserError as exc:
            return _failed_close(tab, exc.safe_reason_code)

        payload = _string_key_mapping(response.data)
        if payload is None:
            return _failed_close(tab, OPENCLI_STATUS_UNAVAILABLE)
        outcome = _close_outcome(payload.get("outcome"))
        verified = payload.get("verified") is True
        if (
            outcome is None
            or payload.get("requested") != tab.page_id
            or (outcome != "failed" and not verified)
        ):
            return _failed_close(tab, OPENCLI_STATUS_UNAVAILABLE)
        error_code = payload.get("errorCode")
        return OpenCliTabCloseResult(
            tab_token=tab.tab_token,
            outcome=outcome,
            verified=verified,
            error_code=str(error_code) if error_code else None,
        )


class BrowserControlLifecycle:
    """Records ownership and submits best-effort close work off the run path."""

    def __init__(self, *, registry: BrowserControlRegistry, closer: OwnedTabCloser) -> None:
        self._registry = registry
        self._closer = closer
        self._work: queue.Queue[Callable[[], None]] = queue.Queue(maxsize=_LIFECYCLE_QUEUE_CAPACITY)
        self._thread = threading.Thread(
            target=self._run_worker,
            name="seektalent-browser-lifecycle",
            daemon=True,
        )
        self._thread.start()
        self._submit(self._recover_owned_tabs)

    @classmethod
    def from_daemon(cls, *, registry_path: Path, daemon: OpenCliDaemonClient) -> BrowserControlLifecycle:
        return cls(
            registry=BrowserControlRegistry(registry_path),
            closer=OpenCliDaemonOwnedTabCloser(daemon.new_connection()),
        )

    @classmethod
    def shared_from_daemon(
        cls,
        *,
        registry_path: Path,
        daemon: OpenCliDaemonClient,
    ) -> BrowserControlLifecycle:
        key = (registry_path.expanduser().resolve(strict=False), daemon.requirement)
        with _SHARED_LIFECYCLES_LOCK:
            lifecycle = _SHARED_LIFECYCLES.get(key)
            if lifecycle is None:
                lifecycle = cls.from_daemon(registry_path=key[0], daemon=daemon)
                _SHARED_LIFECYCLES[key] = lifecycle
        return lifecycle

    def record_scope(self, scope: BrowserControlScope) -> None:
        self._submit(lambda: self._registry.record_scope(scope))

    def record_tab_allocation(
        self,
        scope: BrowserControlScope,
        *,
        tab_token: str,
        session: str,
        tab_kind: OpenCliTabKind,
    ) -> None:
        self._submit(
            lambda: self._registry.record_tab_allocation(
                scope,
                tab_token=tab_token,
                session=session,
                tab_kind=tab_kind,
            )
        )

    def record_owned_tab(self, scope: BrowserControlScope, tab: OpenCliOwnedTab) -> None:
        self._submit(lambda: self._registry.record_owned_tab(scope, tab))

    def record_idle_deadline(self, tab: OpenCliOwnedTab) -> None:
        self._submit(lambda: self._registry.record_idle_deadline(tab))

    def request_reclaim(self, scope: BrowserControlScope, tabs: tuple[OpenCliOwnedTab, ...]) -> None:
        self._submit(lambda: self._reclaim(scope.scope_id, tabs))

    def _recover_owned_tabs(self) -> None:
        by_scope: dict[str, list[OpenCliOwnedTab]] = {}
        for recovered in self._registry.pending_tabs():
            by_scope.setdefault(recovered.scope_id, []).append(recovered.tab)
        for scope_id, tabs in by_scope.items():
            self._reclaim(scope_id, tuple(tabs))

    def _submit(self, action: Callable[[], None]) -> None:
        isolated_call(lambda: self._work.put_nowait(action), self._report_failure)

    def _run_worker(self) -> None:
        while True:
            action = self._work.get()
            isolated_call(action, self._report_failure)
            self._work.task_done()

    def _reclaim(self, scope_id: str, tabs: tuple[OpenCliOwnedTab, ...]) -> None:
        self._registry.record_reclaim_requested(scope_id, tabs)
        if not tabs:
            self._registry.record_empty_scope_reclaimed(scope_id)
            return
        for tab in tabs:
            result = isolated_call(lambda tab=tab: self._closer.close_tab(tab), self._report_failure)
            self._registry.record_reclaim_result(
                scope_id,
                result or _failed_close(tab, OPENCLI_STATUS_UNAVAILABLE),
            )

    def _report_failure(self, exc: Exception) -> None:
        _LOGGER.warning("browser_control_reclaimer_failed error=%s", type(exc).__name__)


def _failed_close(tab: OpenCliOwnedTab, error_code: str) -> OpenCliTabCloseResult:
    return OpenCliTabCloseResult(
        tab_token=tab.tab_token,
        outcome="failed",
        verified=False,
        error_code=error_code,
    )


def _string_key_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    payload: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        payload[key] = item
    return payload


def _close_outcome(value: object) -> OpenCliTabCloseOutcome | None:
    if value == "closed":
        return "closed"
    if value == "already_missing":
        return "already_missing"
    if value == "failed":
        return "failed"
    return None
