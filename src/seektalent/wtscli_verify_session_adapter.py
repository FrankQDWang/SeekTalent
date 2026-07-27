"""WTSCLI readiness probe for one production Liepin profile."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
import math
import re
import time
from typing import Protocol
from urllib.parse import urlparse
import uuid

from seektalent.browser_bridge_manifest import BrowserBridgeRequirement
from seektalent.opencli_browser.contracts import BrowserHostTab, OpenCliBrowserError
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonAction, OpenCliDaemonResult
from seektalent.opencli_browser.lifecycle import OPENCLI_OWNED_TAB_IDLE_SECONDS
from seektalent.opencli_browser.reason_codes import OPENCLI_STATUS_UNAVAILABLE
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
from seektalent.wtscli_verify_session_classification import (
    VerifySessionEffectReply,
    VerifySessionRequestV1,
    WtsCliCurrentProfileSnapshot,
    WtsCliReadinessProbe,
    apply_bridge_status,
    apply_command_error,
    apply_site_state,
    failure_reply,
    is_concrete_navigation_url,
    result_reply,
    safe_liepin_reason,
    wtscli_probe_is_ready,
)


_SAFE_PAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_COMMAND_RESPONSE_MINIMUM_GRACE_SECONDS = 0.05
_PAGE_NAVIGATION_POLL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class _RequestFacts:
    runtime_attempt_fence_ref: str
    profile_binding_ref: str
    profile_binding_generation: int
    provider_account_ref: str | None
    browser_control_scope_id: str


class _WtsCliDaemon(Protocol):
    def verify_bridge(
        self,
        *,
        timeout_seconds: float,
        validate: bool = True,
    ) -> Mapping[str, object]: ...

    def command(
        self,
        action: OpenCliDaemonAction,
        params: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> OpenCliDaemonResult: ...


class _DeadlineExpired(RuntimeError):
    pass


class _BindingChanged(RuntimeError):
    pass


class _BrowserReadinessProbe:
    def __init__(
        self,
        *,
        daemon: _WtsCliDaemon,
        bridge_requirement: BrowserBridgeRequirement,
        control_key: str,
        require_current: Callable[[float], float],
        poll_wait: Callable[[float], None],
    ) -> None:
        self._daemon = daemon
        self._bridge_requirement = bridge_requirement
        self._control_key = control_key
        self._require_current = require_current
        self._poll_wait = poll_wait

    def run(self, deadline_at: float, probe: WtsCliReadinessProbe) -> None:
        status = self._verify_bridge(deadline_at, probe)
        if not apply_bridge_status(probe, status, self._bridge_requirement):
            return
        validated_status = self._validate_bridge(deadline_at, probe)
        if not apply_bridge_status(probe, validated_status, self._bridge_requirement):
            return

        control = self._command(
            deadline_at,
            probe,
            "control",
            {"op": "activate", "controlKey": self._control_key},
        )
        control_payload = _mapping(control.data)
        fence = None if control_payload is None else control_payload.get("fenceToken")
        if (
            control_payload is None
            or control_payload.get("controlKey") != self._control_key
            or type(fence) is not int
            or fence < 1
        ):
            probe.profile_lock = "not_ready"
            probe.safe_reason = "liepin_opencli_stale_control_fence"
            return
        probe.control_fence = fence
        probe.profile_lock = "ready"

        host_result = self._command(
            deadline_at,
            probe,
            "tabs",
            {
                **self._controlled_params(probe, "verify-host"),
                "op": "find",
                "urlPrefix": "https://h.liepin.com/",
            },
        )
        host, host_reason = _select_host_tab(host_result.data)
        if host is None:
            probe.safe_reason = host_reason
            return

        owned_session = f"st_verify_{uuid.uuid4().hex}"
        remaining = self._require_current(deadline_at)
        command_timeout = _command_timeout_within(remaining)
        if command_timeout is None:
            raise _DeadlineExpired
        probe.wtscli_called = True
        opened = self._daemon.command(
            "tabs",
            {
                **self._controlled_params(probe, owned_session),
                "op": "new",
                "hostPage": host.page_id,
                "url": LIEPIN_RECRUITER_SEARCH_URL,
                "active": False,
                "idleTimeout": OPENCLI_OWNED_TAB_IDLE_SECONDS,
            },
            timeout_seconds=command_timeout,
        )
        opened_payload = _mapping(opened.data)
        if isinstance(opened.page, str) and _SAFE_PAGE_ID.fullmatch(opened.page):
            probe.owned_page = opened.page
            probe.owned_session = owned_session
        if (
            probe.owned_page is None
            or opened_payload is None
            or opened_payload.get("active") is not False
            or opened_payload.get("placement") != "borrowed-host-window"
            or type(opened.idle_deadline_at) is not int
        ):
            probe.safe_reason = "liepin_opencli_tab_response_malformed"
            return

        page_params = self._controlled_params(probe, owned_session)
        page_params["page"] = probe.owned_page
        self._observe_owned_page(deadline_at, probe, page_params)

    def _observe_owned_page(
        self,
        deadline_at: float,
        probe: WtsCliReadinessProbe,
        page_params: Mapping[str, object],
    ) -> None:
        while True:
            url_result = self._command(
                deadline_at,
                probe,
                "browser-operation",
                {**page_params, "operation": "get-url"},
            )
            if (
                not isinstance(url_result.data, str)
                or (url_result.page is not None and url_result.page != probe.owned_page)
            ):
                probe.safe_reason = "liepin_opencli_malformed_state"
                return
            if not is_concrete_navigation_url(url_result.data):
                self._wait_for_navigation_poll(deadline_at)
                continue

            state_result = self._command(
                deadline_at,
                probe,
                "browser-operation",
                {**page_params, "operation": "state"},
            )
            if (
                not isinstance(state_result.data, str)
                or (state_result.page is not None and state_result.page != probe.owned_page)
            ):
                probe.safe_reason = "liepin_opencli_malformed_state"
                return
            disposition = apply_site_state(probe, url=url_result.data, text=state_result.data)
            if disposition != "retry":
                return
            self._wait_for_navigation_poll(deadline_at)

    def _wait_for_navigation_poll(self, deadline_at: float) -> None:
        remaining = self._require_current(deadline_at)
        self._poll_wait(min(_PAGE_NAVIGATION_POLL_SECONDS, remaining))
        self._require_current(deadline_at)

    def _verify_bridge(
        self,
        deadline_at: float,
        probe: WtsCliReadinessProbe,
    ) -> Mapping[str, object]:
        remaining = self._require_current(deadline_at)
        probe.wtscli_called = True
        return self._daemon.verify_bridge(timeout_seconds=remaining, validate=False)

    def _validate_bridge(
        self,
        deadline_at: float,
        probe: WtsCliReadinessProbe,
    ) -> Mapping[str, object]:
        remaining = self._require_current(deadline_at)
        probe.wtscli_called = True
        return self._daemon.verify_bridge(timeout_seconds=remaining)

    def _command(
        self,
        deadline_at: float,
        probe: WtsCliReadinessProbe,
        action: OpenCliDaemonAction,
        params: Mapping[str, object],
    ) -> OpenCliDaemonResult:
        remaining = self._require_current(deadline_at)
        timeout = _command_timeout_within(remaining)
        if timeout is None:
            raise _DeadlineExpired
        probe.wtscli_called = True
        return self._daemon.command(action, params, timeout_seconds=timeout)

    def _controlled_params(
        self,
        probe: WtsCliReadinessProbe,
        session: str,
    ) -> dict[str, object]:
        if probe.control_fence is None:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        return {
            "session": session,
            "surface": "browser",
            "windowMode": "background",
            "controlKey": self._control_key,
            "fenceToken": probe.control_fence,
        }


class _WtsCliVerifySessionEffect:
    __slots__ = (
        "_bridge_requirement",
        "_control_key",
        "_current_profile_snapshot",
        "_daemon",
        "_monotonic_clock",
        "_poll_wait",
    )

    def __init__(
        self,
        *,
        daemon: _WtsCliDaemon,
        bridge_requirement: BrowserBridgeRequirement,
        current_profile_snapshot: Callable[[], WtsCliCurrentProfileSnapshot],
        control_key: str,
        monotonic_clock: Callable[[], float],
        poll_wait: Callable[[float], None],
    ) -> None:
        self._daemon = daemon
        self._bridge_requirement = bridge_requirement
        self._current_profile_snapshot = current_profile_snapshot
        self._control_key = control_key
        self._monotonic_clock = monotonic_clock
        self._poll_wait = poll_wait

    def __call__(self, request: VerifySessionRequestV1, deadline_at: float) -> VerifySessionEffectReply:
        if type(request) is not VerifySessionRequestV1:
            raise TypeError("request must be a VerifySessionRequestV1")
        request_facts = _request_facts(request)
        if request_facts is None:
            raise TypeError("request does not expose valid verify-session facts")
        if not _valid_deadline(deadline_at):
            return failure_reply(request, "sidecar_not_ready")
        if self._remaining(deadline_at) <= 0:
            return failure_reply(request, "exchange_deadline_expired")

        binding = self._read_binding()
        if binding is None or not _binding_matches_request(binding, request_facts):
            return failure_reply(request, "sidecar_not_ready")
        if self._remaining(deadline_at) <= 0:
            return failure_reply(request, "exchange_deadline_expired")

        probe = WtsCliReadinessProbe(binding=binding)
        binding_changed = False
        try:
            _BrowserReadinessProbe(
                daemon=self._daemon,
                bridge_requirement=self._bridge_requirement,
                control_key=self._control_key,
                require_current=lambda deadline: self._require_current(
                    request_facts,
                    deadline,
                    binding,
                ),
                poll_wait=self._poll_wait,
            ).run(deadline_at, probe)
        except _DeadlineExpired:
            probe.safe_reason = "liepin_opencli_timeout"
        except _BindingChanged:
            binding_changed = True
        except OpenCliBrowserError as error:
            apply_command_error(probe, error.safe_reason_code)
        except (ArithmeticError, EOFError, OSError, RuntimeError, TypeError, ValueError):
            probe.safe_reason = "liepin_opencli_status_unavailable"

        if binding_changed or not self._binding_is_current(binding, request_facts):
            return failure_reply(request, "sidecar_not_ready")
        if self._remaining(deadline_at) <= 0:
            if not probe.wtscli_called:
                return failure_reply(request, "exchange_deadline_expired")
            probe.safe_reason = "liepin_opencli_timeout"
        return result_reply(request, probe)

    def __repr__(self) -> str:
        return "WtsCliVerifySessionEffect()"

    def _require_current(
        self,
        request: _RequestFacts,
        deadline_at: float,
        binding: WtsCliCurrentProfileSnapshot,
    ) -> float:
        if not self._binding_is_current(binding, request):
            raise _BindingChanged
        remaining = self._remaining(deadline_at)
        if remaining <= 0:
            raise _DeadlineExpired
        return remaining

    def _binding_is_current(
        self,
        binding: WtsCliCurrentProfileSnapshot,
        request: _RequestFacts,
    ) -> bool:
        current = self._read_binding()
        return current == binding and current is not None and _binding_matches_request(current, request)

    def _read_binding(self) -> WtsCliCurrentProfileSnapshot | None:
        snapshot: object | None = None
        with suppress(Exception):
            snapshot = self._current_profile_snapshot()
        return snapshot if type(snapshot) is WtsCliCurrentProfileSnapshot else None

    def _remaining(self, deadline_at: float) -> float:
        now: object | None = None
        with suppress(Exception):
            now = self._monotonic_clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            return -1.0
        return deadline_at - float(now)

def create_wtscli_verify_session_effect(
    *,
    daemon: _WtsCliDaemon,
    bridge_requirement: BrowserBridgeRequirement,
    current_profile_snapshot: Callable[[], WtsCliCurrentProfileSnapshot],
    control_key: str,
    monotonic_clock: Callable[[], float] = time.monotonic,
    poll_wait: Callable[[float], None] = time.sleep,
) -> Callable[[VerifySessionRequestV1, float], VerifySessionEffectReply]:
    """Create an explicit test/manual-only effect; no production route calls this factory."""
    if type(bridge_requirement) is not BrowserBridgeRequirement:
        raise TypeError("bridge_requirement must be a BrowserBridgeRequirement")
    if (
        not callable(getattr(daemon, "verify_bridge", None))
        or not callable(getattr(daemon, "command", None))
        or not callable(current_profile_snapshot)
        or not callable(monotonic_clock)
        or not callable(poll_wait)
    ):
        raise TypeError("WTSCLI adapter dependencies must be callable")
    if type(control_key) is not str or not control_key.strip() or len(control_key) > 256:
        raise ValueError("control_key is invalid")
    return _WtsCliVerifySessionEffect(
        daemon=daemon,
        bridge_requirement=bridge_requirement,
        current_profile_snapshot=current_profile_snapshot,
        control_key=control_key,
        monotonic_clock=monotonic_clock,
        poll_wait=poll_wait,
    )


def probe_wtscli_liepin_session(
    *,
    daemon: _WtsCliDaemon,
    bridge_requirement: BrowserBridgeRequirement,
    control_key: str,
    deadline_at: float,
    monotonic_clock: Callable[[], float] = time.monotonic,
    poll_wait: Callable[[float], None] = time.sleep,
) -> str | None:
    """Observe current Liepin browser readiness through the installed WTSCLI bridge."""
    _validate_adapter_dependencies(
        daemon=daemon,
        bridge_requirement=bridge_requirement,
        control_key=control_key,
        monotonic_clock=monotonic_clock,
        poll_wait=poll_wait,
    )
    probe = WtsCliReadinessProbe(binding=None)

    def require_current(deadline: float) -> float:
        remaining = _remaining(deadline, monotonic_clock)
        if remaining <= 0:
            raise _DeadlineExpired
        return remaining

    try:
        _BrowserReadinessProbe(
            daemon=daemon,
            bridge_requirement=bridge_requirement,
            control_key=control_key,
            require_current=require_current,
            poll_wait=poll_wait,
        ).run(deadline_at, probe)
    except _DeadlineExpired:
        probe.safe_reason = "liepin_opencli_timeout"
    except OpenCliBrowserError as error:
        apply_command_error(probe, error.safe_reason_code)
    except (ArithmeticError, EOFError, OSError, RuntimeError, TypeError, ValueError):
        probe.safe_reason = "liepin_opencli_status_unavailable"
    if _remaining(deadline_at, monotonic_clock) <= 0:
        probe.safe_reason = "liepin_opencli_timeout"
    return None if wtscli_probe_is_ready(probe) else safe_liepin_reason(probe.safe_reason)


def _validate_adapter_dependencies(
    *,
    daemon: _WtsCliDaemon,
    bridge_requirement: BrowserBridgeRequirement,
    control_key: str,
    monotonic_clock: Callable[[], float],
    poll_wait: Callable[[float], None],
) -> None:
    if type(bridge_requirement) is not BrowserBridgeRequirement:
        raise TypeError("bridge_requirement must be a BrowserBridgeRequirement")
    if (
        not callable(getattr(daemon, "verify_bridge", None))
        or not callable(getattr(daemon, "command", None))
        or not callable(monotonic_clock)
        or not callable(poll_wait)
    ):
        raise TypeError("WTSCLI adapter dependencies must be callable")
    if type(control_key) is not str or not control_key.strip() or len(control_key) > 256:
        raise ValueError("control_key is invalid")


def _remaining(deadline_at: float, monotonic_clock: Callable[[], float]) -> float:
    now: object | None = None
    with suppress(Exception):
        now = monotonic_clock()
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        return -1.0
    return deadline_at - float(now)


def _binding_matches_request(
    snapshot: WtsCliCurrentProfileSnapshot,
    request: _RequestFacts,
) -> bool:
    return (
        type(snapshot.runtime_attempt_fence_ref) is str
        and snapshot.runtime_attempt_fence_ref == request.runtime_attempt_fence_ref
        and type(snapshot.profile_binding_ref) is str
        and snapshot.profile_binding_ref == request.profile_binding_ref
        and type(snapshot.profile_binding_generation) is int
        and snapshot.profile_binding_generation == request.profile_binding_generation
        and type(snapshot.provider_account_ref) is str
        and snapshot.provider_account_ref == request.provider_account_ref
        and type(snapshot.provider_account_subject) is str
        and bool(snapshot.provider_account_subject.strip())
        and type(snapshot.browser_control_scope_id) is str
        and snapshot.browser_control_scope_id == request.browser_control_scope_id
    )


def _request_facts(value: object) -> _RequestFacts | None:
    try:
        identity = getattr(value, "identity")
        runtime_attempt_fence_ref = getattr(identity, "runtime_attempt_fence_ref")
        profile_binding_ref = getattr(value, "profile_binding_ref")
        profile_binding_generation = getattr(identity, "profile_binding_generation")
        provider_account_ref = getattr(value, "provider_account_ref")
        browser_control_scope_id = getattr(identity, "browser_control_scope_id")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    if (
        type(runtime_attempt_fence_ref) is not str
        or type(profile_binding_ref) is not str
        or type(profile_binding_generation) is not int
        or (provider_account_ref is not None and type(provider_account_ref) is not str)
        or type(browser_control_scope_id) is not str
    ):
        return None
    return _RequestFacts(
        runtime_attempt_fence_ref=runtime_attempt_fence_ref,
        profile_binding_ref=profile_binding_ref,
        profile_binding_generation=profile_binding_generation,
        provider_account_ref=provider_account_ref,
        browser_control_scope_id=browser_control_scope_id,
    )


def _select_host_tab(value: object) -> tuple[BrowserHostTab | None, str]:
    if not isinstance(value, list):
        return None, "liepin_opencli_malformed_state"
    candidates: list[BrowserHostTab] = []
    for item in value:
        payload = _mapping(item)
        if payload is None:
            return None, "liepin_opencli_malformed_state"
        page = payload.get("page")
        url = payload.get("url")
        window_id = payload.get("windowId")
        active = payload.get("active")
        focused = payload.get("windowFocused")
        if (
            not isinstance(page, str)
            or _SAFE_PAGE_ID.fullmatch(page) is None
            or not isinstance(url, str)
            or type(window_id) is not int
            or type(active) is not bool
            or type(focused) is not bool
        ):
            return None, "liepin_opencli_malformed_state"
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "h.liepin.com"
            or parsed.username is not None
            or parsed.password is not None
        ):
            continue
        candidates.append(
            BrowserHostTab(
                page_id=page,
                url=url,
                window_id=window_id,
                active=active,
                window_focused=focused,
            )
        )
    windows = {candidate.window_id for candidate in candidates}
    if not windows:
        return None, "liepin_host_tab_missing"
    if len(windows) == 1:
        selected_window = next(iter(windows))
    else:
        focused_windows = {candidate.window_id for candidate in candidates if candidate.window_focused}
        if len(focused_windows) != 1:
            return None, "liepin_host_window_ambiguous"
        selected_window = next(iter(focused_windows))
    in_window = [candidate for candidate in candidates if candidate.window_id == selected_window]
    active_candidates = [candidate for candidate in in_window if candidate.active]
    if len(active_candidates) == 1:
        return active_candidates[0], ""
    return min(in_window, key=lambda candidate: (candidate.url, candidate.page_id)), ""


def _mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    payload: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str:
            return None
        payload[key] = item
    return payload


def _command_timeout_within(remaining: float) -> float | None:
    """Reserve the daemon client's response grace inside the same absolute budget."""
    if not math.isfinite(remaining) or remaining <= _COMMAND_RESPONSE_MINIMUM_GRACE_SECONDS:
        return None
    if remaining <= 0.55:
        return remaining - _COMMAND_RESPONSE_MINIMUM_GRACE_SECONDS
    if remaining < 11:
        return remaining / 1.1
    return remaining - 1


def _valid_deadline(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


__all__ = [
    "WtsCliCurrentProfileSnapshot",
    "create_wtscli_verify_session_effect",
    "probe_wtscli_liepin_session",
]
