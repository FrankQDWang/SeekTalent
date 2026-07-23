"""Production-unreachable sidecar WTSCLI readiness probe for one Liepin profile."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
import math
import re
import time
from typing import Literal, Protocol, TypeAlias
from urllib.parse import unquote, urlparse
import uuid

from seektalent.browser_bridge_manifest import BrowserBridgeRequirement
from seektalent.opencli_browser.contracts import BrowserHostTab, OpenCliBrowserError
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonAction, OpenCliDaemonResult
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_BRIDGE_CAPABILITY_MISSING,
    OPENCLI_BRIDGE_INTEGRITY_FAILED,
    OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
    OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_DAEMON_STALE,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_STATUS_UNAVAILABLE,
)
from seektalent.providers.liepin.liepin_opencli_policy import (
    LIEPIN_RECRUITER_SEARCH_SURFACE_PATHS,
    LIEPIN_RECRUITER_SEARCH_URL,
    liepin_reason_from_opencli_reason,
)
from seektalent.providers.liepin.liepin_site_parsing import classify_liepin_state


_SAFE_PAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_COMMAND_RESPONSE_MINIMUM_GRACE_SECONDS = 0.05
_BRIDGE_REASONS = frozenset(
    {
        OPENCLI_BRIDGE_BUILD_MISMATCH,
        OPENCLI_BRIDGE_CAPABILITY_MISSING,
        OPENCLI_BRIDGE_INTEGRITY_FAILED,
        OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
        OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    }
)
_ALLOWED_RESULT_REASONS = frozenset(
    {
        "liepin_host_tab_missing",
        "liepin_host_window_ambiguous",
        "liepin_opencli_bootstrap_failed",
        "liepin_opencli_command_missing",
        "liepin_opencli_daemon_not_running",
        "liepin_opencli_daemon_stale",
        "liepin_opencli_extension_disconnected",
        "liepin_opencli_forbidden_command",
        "liepin_opencli_host_blocked",
        "liepin_opencli_identity_intercept",
        "liepin_opencli_login_required",
        "liepin_opencli_malformed_state",
        "liepin_opencli_risk_page",
        "liepin_opencli_search_not_ready",
        "liepin_opencli_selector_ambiguous",
        "liepin_opencli_selector_not_found",
        "liepin_opencli_stale_control_fence",
        "liepin_opencli_stale_ref",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_tab_response_malformed",
        "liepin_opencli_target_not_found",
        "liepin_opencli_terminal_state",
        "liepin_opencli_timeout",
        "liepin_opencli_unknown_modal",
        "liepin_opencli_window_policy_blocked",
        "liepin_owned_tab_missing",
    }
)
_USER_ACTIONS = {
    "liepin_host_tab_missing": "verify_session.open_liepin_host",
    "liepin_opencli_identity_intercept": "verify_session.complete_identity_check",
    "liepin_opencli_login_required": "verify_session.log_in",
    "liepin_opencli_risk_page": "verify_session.complete_risk_check",
    "liepin_opencli_unknown_modal": "verify_session.dismiss_or_resolve_modal",
}

_ComponentReadiness: TypeAlias = Literal["ready", "not_ready", "not_observed"]
_AccountReadiness: TypeAlias = Literal[
    "ready",
    "not_ready",
    "not_observed",
    "missing",
    "login_required",
    "revoked",
]
_RiskState: TypeAlias = Literal["clear", "risk_page", "not_observed"]
_EffectReply: TypeAlias = dict[str, object]


@dataclass(frozen=True, slots=True)
class WtsCliCurrentProfileSnapshot:
    """Current sidecar-owned binding facts, never reconstructed from a request."""

    runtime_attempt_fence_ref: str
    profile_binding_ref: str
    profile_binding_generation: int
    provider_account_ref: str | None
    provider_account_subject: str
    browser_control_scope_id: str


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


@dataclass(slots=True)
class _Probe:
    binding: WtsCliCurrentProfileSnapshot
    process: _ComponentReadiness = "not_observed"
    bridge: _ComponentReadiness = "not_observed"
    extension: _ComponentReadiness = "not_observed"
    profile_lock: _ComponentReadiness = "not_observed"
    account: _AccountReadiness = "not_observed"
    search_surface: _ComponentReadiness = "not_observed"
    risk_state: _RiskState = "not_observed"
    safe_reason: str | None = None
    control_fence: int | None = None
    owned_page: str | None = None
    owned_session: str | None = None
    wtscli_called: bool = False
    cleanup_failed: bool = False


class _DeadlineExpired(RuntimeError):
    pass


class _BindingChanged(RuntimeError):
    pass


class _WtsCliVerifySessionEffect:
    __slots__ = (
        "_bridge_requirement",
        "_control_key",
        "_current_profile_snapshot",
        "_daemon",
        "_monotonic_clock",
    )

    def __init__(
        self,
        *,
        daemon: _WtsCliDaemon,
        bridge_requirement: BrowserBridgeRequirement,
        current_profile_snapshot: Callable[[], WtsCliCurrentProfileSnapshot],
        control_key: str,
        monotonic_clock: Callable[[], float],
    ) -> None:
        self._daemon = daemon
        self._bridge_requirement = bridge_requirement
        self._current_profile_snapshot = current_profile_snapshot
        self._control_key = control_key
        self._monotonic_clock = monotonic_clock

    def __call__(self, request: object, deadline_at: float) -> _EffectReply:
        request_facts = _request_facts(request)
        if request_facts is None:
            raise TypeError("request does not expose valid verify-session facts")
        if not _valid_deadline(deadline_at):
            return _failure("sidecar_not_ready")
        if self._remaining(deadline_at) <= 0:
            return _failure("exchange_deadline_expired")

        binding = self._read_binding()
        if binding is None or not _binding_matches_request(binding, request_facts):
            return _failure("sidecar_not_ready")
        if self._remaining(deadline_at) <= 0:
            return _failure("exchange_deadline_expired")

        probe = _Probe(binding=binding)
        binding_changed = False
        try:
            self._probe(request_facts, deadline_at, probe)
        except _DeadlineExpired:
            probe.safe_reason = "liepin_opencli_timeout"
        except _BindingChanged:
            binding_changed = True
        except OpenCliBrowserError as error:
            self._apply_command_error(probe, error.safe_reason_code)
        except (ArithmeticError, EOFError, OSError, RuntimeError, TypeError, ValueError):
            probe.safe_reason = "liepin_opencli_status_unavailable"
        finally:
            self._reclaim_owned_tab(deadline_at, probe)

        if binding_changed or not self._binding_is_current(binding, request_facts):
            return _failure("sidecar_not_ready")
        if self._remaining(deadline_at) <= 0:
            if not probe.wtscli_called:
                return _failure("exchange_deadline_expired")
            probe.safe_reason = "liepin_opencli_timeout"
        elif probe.cleanup_failed:
            probe.safe_reason = "liepin_owned_tab_missing"
        return _result(probe)

    def __repr__(self) -> str:
        return "WtsCliVerifySessionEffect()"

    def _probe(self, request: _RequestFacts, deadline_at: float, probe: _Probe) -> None:
        status = self._verify_bridge(request, deadline_at, probe)
        if not _apply_bridge_status(probe, status, self._bridge_requirement):
            return
        validated_status = self._validate_bridge(request, deadline_at, probe)
        if not _apply_bridge_status(probe, validated_status, self._bridge_requirement):
            return

        control = self._command(
            request,
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
            request,
            deadline_at,
            probe,
            "tabs",
            {**self._controlled_params(probe, "verify-host"), "op": "find", "urlPrefix": "https://h.liepin.com/"},
        )
        host, host_reason = _select_host_tab(host_result.data)
        if host is None:
            probe.safe_reason = host_reason
            return

        owned_session = f"st_verify_{uuid.uuid4().hex}"
        remaining = self._require_current(request, deadline_at, probe.binding)
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
                "idleTimeout": command_timeout,
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
        url_result = self._command(
            request,
            deadline_at,
            probe,
            "browser-operation",
            {**page_params, "operation": "get-url"},
        )
        state_result = self._command(
            request,
            deadline_at,
            probe,
            "browser-operation",
            {**page_params, "operation": "state"},
        )
        if (
            not isinstance(url_result.data, str)
            or not isinstance(state_result.data, str)
            or (url_result.page is not None and url_result.page != probe.owned_page)
            or (state_result.page is not None and state_result.page != probe.owned_page)
        ):
            probe.safe_reason = "liepin_opencli_malformed_state"
            return
        _apply_site_state(probe, url=url_result.data, text=state_result.data)

    def _verify_bridge(
        self,
        request: _RequestFacts,
        deadline_at: float,
        probe: _Probe,
    ) -> Mapping[str, object]:
        remaining = self._require_current(request, deadline_at, probe.binding)
        probe.wtscli_called = True
        return self._daemon.verify_bridge(timeout_seconds=remaining, validate=False)

    def _validate_bridge(
        self,
        request: _RequestFacts,
        deadline_at: float,
        probe: _Probe,
    ) -> Mapping[str, object]:
        remaining = self._require_current(request, deadline_at, probe.binding)
        probe.wtscli_called = True
        return self._daemon.verify_bridge(timeout_seconds=remaining)

    def _command(
        self,
        request: _RequestFacts,
        deadline_at: float,
        probe: _Probe,
        action: OpenCliDaemonAction,
        params: Mapping[str, object],
    ) -> OpenCliDaemonResult:
        remaining = self._require_current(request, deadline_at, probe.binding)
        timeout = _command_timeout_within(remaining)
        if timeout is None:
            raise _DeadlineExpired
        probe.wtscli_called = True
        return self._daemon.command(action, params, timeout_seconds=timeout)

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

    def _controlled_params(self, probe: _Probe, session: str) -> dict[str, object]:
        if probe.control_fence is None:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        return {
            "session": session,
            "surface": "browser",
            "windowMode": "background",
            "controlKey": self._control_key,
            "fenceToken": probe.control_fence,
        }

    def _apply_command_error(self, probe: _Probe, reason: str) -> None:
        safe_reason = _safe_liepin_reason(reason)
        if not probe.wtscli_called:
            probe.safe_reason = safe_reason
            return
        if reason in {OPENCLI_DAEMON_NOT_RUNNING, OPENCLI_DAEMON_STALE}:
            probe.process = "not_ready"
        elif reason in _BRIDGE_REASONS:
            probe.process = "ready"
            probe.bridge = "not_ready"
        elif reason == OPENCLI_EXTENSION_DISCONNECTED:
            probe.process = "ready"
            probe.bridge = "ready"
            probe.extension = "not_ready"
        probe.safe_reason = safe_reason

    def _reclaim_owned_tab(self, deadline_at: float, probe: _Probe) -> None:
        page = probe.owned_page
        session = probe.owned_session
        if page is None or session is None:
            return
        remaining = self._remaining(deadline_at)
        timeout = _command_timeout_within(remaining)
        if timeout is None:
            return
        closed: object | None = None
        with suppress(Exception):
            probe.wtscli_called = True
            closed = self._daemon.command(
                "tabs",
                {"op": "close", "session": session, "surface": "browser", "page": page},
                timeout_seconds=timeout,
            )
        if type(closed) is OpenCliDaemonResult:
            payload = _mapping(closed.data)
            if (
                payload is None
                or payload.get("requested") != page
                or payload.get("outcome") not in {"closed", "already_missing"}
                or payload.get("verified") is not True
            ):
                probe.cleanup_failed = True
        else:
            probe.cleanup_failed = True


def create_wtscli_verify_session_effect(
    *,
    daemon: _WtsCliDaemon,
    bridge_requirement: BrowserBridgeRequirement,
    current_profile_snapshot: Callable[[], WtsCliCurrentProfileSnapshot],
    control_key: str,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> Callable[[object, float], _EffectReply]:
    """Create an explicit test/manual-only effect; no production route calls this factory."""
    if type(bridge_requirement) is not BrowserBridgeRequirement:
        raise TypeError("bridge_requirement must be a BrowserBridgeRequirement")
    if (
        not callable(getattr(daemon, "verify_bridge", None))
        or not callable(getattr(daemon, "command", None))
        or not callable(current_profile_snapshot)
        or not callable(monotonic_clock)
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
    )


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


def _apply_bridge_status(
    probe: _Probe,
    status: Mapping[str, object],
    requirement: BrowserBridgeRequirement,
) -> bool:
    pid = status.get("pid")
    if (
        status.get("ok") is not True
        or type(pid) is not int
        or pid < 1
        or not isinstance(status.get("daemonVersion"), str)
    ):
        probe.process = "not_ready"
        probe.safe_reason = "liepin_opencli_status_unavailable"
        return False
    probe.process = "ready"
    if not _identity_matches(
        status,
        requirement,
        implementation_key="implementation",
        build_key="bridgeBuildId",
        protocol_key="protocolVersion",
        capabilities_key="capabilities",
    ):
        probe.bridge = "not_ready"
        probe.safe_reason = "liepin_opencli_status_unavailable"
        return False
    probe.bridge = "ready"
    if status.get("extensionConnected") is not True:
        probe.extension = "not_ready"
        probe.safe_reason = "liepin_opencli_extension_disconnected"
        return False
    if not _identity_matches(
        status,
        requirement,
        implementation_key="extensionImplementation",
        build_key="extensionBridgeBuildId",
        protocol_key="extensionProtocolVersion",
        capabilities_key="extensionCapabilities",
    ):
        probe.extension = "not_ready"
        probe.safe_reason = "liepin_opencli_status_unavailable"
        return False
    probe.extension = "ready"
    return True


def _identity_matches(
    status: Mapping[str, object],
    requirement: BrowserBridgeRequirement,
    *,
    implementation_key: str,
    build_key: str,
    protocol_key: str,
    capabilities_key: str,
) -> bool:
    protocol = _mapping(status.get(protocol_key))
    capabilities = status.get(capabilities_key)
    if protocol is None or not isinstance(capabilities, list):
        return False
    major = protocol.get("major")
    minor = protocol.get("minor")
    capability_set = (
        frozenset(capabilities)
        if all(type(capability) is str and capability for capability in capabilities)
        else frozenset()
    )
    return (
        status.get(implementation_key) == requirement.implementation
        and status.get(build_key) == requirement.bridge_build_id
        and type(major) is int
        and type(minor) is int
        and major == requirement.protocol_major
        and minor >= requirement.protocol_minor
        and requirement.capabilities.issubset(capability_set)
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


def _apply_site_state(probe: _Probe, *, url: str, text: str) -> None:
    reason = classify_liepin_state(url=url, text=text)
    if reason == "liepin_opencli_login_required":
        probe.account = "login_required"
        probe.search_surface = "not_ready"
        probe.risk_state = "not_observed"
    elif reason == "liepin_opencli_risk_page":
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "risk_page"
    elif reason in {"liepin_opencli_identity_intercept", "liepin_opencli_host_blocked"}:
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "not_observed"
    elif reason == "liepin_opencli_unknown_modal":
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "clear"
    elif reason is not None:
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "not_observed"
    else:
        probe.account = "ready"
        probe.risk_state = "clear"
        probe.search_surface = "ready" if _is_search_surface(url) else "not_ready"
        if probe.search_surface == "not_ready":
            reason = "liepin_opencli_search_not_ready"
    probe.safe_reason = reason


def _is_search_surface(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() in {"h.liepin.com", "c.liepin.com"}
        and unquote(parsed.path or "").rstrip("/") in LIEPIN_RECRUITER_SEARCH_SURFACE_PATHS
        and parsed.username is None
        and parsed.password is None
    )


def _result(probe: _Probe) -> _EffectReply:
    reason = _safe_result_reason(probe.safe_reason)
    ready = (
        probe.process == "ready"
        and probe.bridge == "ready"
        and probe.extension == "ready"
        and probe.profile_lock == "ready"
        and probe.account == "ready"
        and probe.search_surface == "ready"
        and probe.risk_state == "clear"
        and reason is None
    )
    user_action = None
    if reason in _USER_ACTIONS:
        user_action = {"code": reason, "instruction_key": _USER_ACTIONS[reason]}
    return {
        "kind": "result",
        "process_readiness": probe.process,
        "bridge_readiness": probe.bridge,
        "extension_readiness": probe.extension,
        "profile_lock_readiness": probe.profile_lock,
        "account_readiness": probe.account,
        "search_surface_readiness": probe.search_surface,
        "risk_state": probe.risk_state,
        "session_readiness": "ready" if ready else "not_ready",
        "actual_profile_binding_ref": probe.binding.profile_binding_ref,
        "actual_provider_account_ref": probe.binding.provider_account_ref,
        "actual_profile_binding_generation": probe.binding.profile_binding_generation,
        "safe_reason_code": None if ready else reason,
        "user_action": user_action,
    }


def _failure(
    reason: Literal["exchange_deadline_expired", "sidecar_not_ready"],
) -> _EffectReply:
    return {
        "kind": "failure",
        "failure_fact": "no_effect_performed",
        "failure_reason": reason,
    }


def _safe_liepin_reason(reason: object) -> str:
    mapped = liepin_reason_from_opencli_reason(reason if isinstance(reason, str) else "")
    return mapped if mapped in _ALLOWED_RESULT_REASONS else "liepin_opencli_status_unavailable"


def _safe_result_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return reason if reason in _ALLOWED_RESULT_REASONS else "liepin_opencli_status_unavailable"


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
]
