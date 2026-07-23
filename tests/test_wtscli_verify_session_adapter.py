from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import json
import logging
import math
from pathlib import Path

import pytest

from seektalent.browser_bridge_manifest import BrowserBridgeRequirement
from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonClient, OpenCliDaemonResult
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_DAEMON_STALE,
    OPENCLI_TARGET_NOT_FOUND,
    OPENCLI_TIMEOUT,
)
from seektalent.source_port.authenticated_verify_session_frames import VerifySessionFailureV1
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1, VerifySessionResultV1
from seektalent.wtscli_verify_session_adapter import (
    WtsCliCurrentProfileSnapshot,
    create_wtscli_verify_session_effect,
)


RAW_RUNTIME_FENCE = "wtscli-adapter-runtime-fence-" + "r" * 64
CONTROL_KEY = "wtscli-adapter-control-key-" + "c" * 64
CONTROL_FENCE = 918273645
SEARCH_URL = "https://h.liepin.com/search/getConditionItem#session"
RAW_DOM = "DOM-CANARY confidential visible account content"
RAW_STDERR = "STDERR-CANARY extension diagnostic"

BRIDGE_REQUIREMENT = BrowserBridgeRequirement(
    implementation="seektalent-opencli",
    bridge_build_id="seektalent-opencli-0.1.0+wtscli.1",
    protocol_major=1,
    protocol_minor=0,
    capabilities=frozenset(
        {
            "browser.operation-deadline.v1",
            "browser.operations.v1",
            "control-fence.v1",
            "tab.close-verified.v1",
            "tab.create-in-existing-window.v1",
            "tab.find.v1",
            "tab.idle-deadline.v1",
        }
    ),
)


def _request(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "run_id": "run-wtscli-1",
        "operation_id": "verify-wtscli-1",
        "attempt_no": 1,
        "idempotency_key": "verify-wtscli-key-1",
        "correlation_id": "verify-wtscli-correlation-1",
        "accepted_requirement_revision_id": "requirement-wtscli-1",
        "runtime_attempt_fence_token": RAW_RUNTIME_FENCE,
        "profile_binding_generation": 7,
        "browser_control_scope_id": "browser-scope-wtscli-1",
        "deadline_value": 60_000,
        "expected_source_operation_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
        "delivery_mode": "initial",
        "dispatch_intent_id": "dispatch-intent-wtscli-1",
        "dispatch_intent_revision": 1,
        "source_operation_acceptance_ref": "source-acceptance-wtscli-1",
        "profile_binding_ref": "profile-binding-wtscli-1",
        "provider_account_ref": "provider-account-wtscli-1",
        "required_capabilities": (
            "account",
            "bridge",
            "extension",
            "process",
            "profile_lock",
            "risk_state",
            "search_surface",
        ),
        "user_interaction_policy": "observe_only",
        "verify_search_surface": True,
        "component_receipt_refs": ("component-receipt-wtscli-1",),
    }
    values.update(updates)
    return VerifySessionRequestV1.create(**values)


def _snapshot(request: VerifySessionRequestV1 | None = None, **updates: object) -> WtsCliCurrentProfileSnapshot:
    request = request or _request()
    values: dict[str, object] = {
        "runtime_attempt_fence_ref": request.identity.runtime_attempt_fence_ref,
        "profile_binding_ref": request.profile_binding_ref,
        "profile_binding_generation": request.identity.profile_binding_generation,
        "provider_account_ref": request.provider_account_ref,
        "provider_account_subject": "liepin-opencli-local-browser-profile",
        "browser_control_scope_id": request.identity.browser_control_scope_id,
    }
    values.update(updates)
    return WtsCliCurrentProfileSnapshot(**values)  # type: ignore[arg-type]


def _ready_status(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "ok": True,
        "pid": 41001,
        "daemonVersion": "0.1.0",
        "implementation": BRIDGE_REQUIREMENT.implementation,
        "bridgeBuildId": BRIDGE_REQUIREMENT.bridge_build_id,
        "protocolVersion": {
            "major": BRIDGE_REQUIREMENT.protocol_major,
            "minor": BRIDGE_REQUIREMENT.protocol_minor,
        },
        "capabilities": sorted(BRIDGE_REQUIREMENT.capabilities),
        "extensionConnected": True,
        "extensionImplementation": BRIDGE_REQUIREMENT.implementation,
        "extensionBridgeBuildId": BRIDGE_REQUIREMENT.bridge_build_id,
        "extensionProtocolVersion": {
            "major": BRIDGE_REQUIREMENT.protocol_major,
            "minor": BRIDGE_REQUIREMENT.protocol_minor,
        },
        "extensionCapabilities": sorted(BRIDGE_REQUIREMENT.capabilities),
    }
    values.update(updates)
    return values


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _SnapshotSource:
    def __init__(self, value: WtsCliCurrentProfileSnapshot, *, clock: _Clock | None = None) -> None:
        self.value = value
        self.clock = clock
        self.calls = 0
        self.advance_on_call: dict[int, float] = {}

    def __call__(self) -> WtsCliCurrentProfileSnapshot:
        self.calls += 1
        if self.clock is not None:
            self.clock.advance(self.advance_on_call.get(self.calls, 0.0))
        return self.value


class _FakeDaemon:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.status_payload = _ready_status()
        self.host_tabs: list[dict[str, object]] = [
            {
                "page": "user-host-page",
                "url": "https://h.liepin.com/",
                "windowId": 17,
                "active": True,
                "windowFocused": True,
            }
        ]
        self.page_url = SEARCH_URL
        self.state_text = "URL: https://h.liepin.com/search/getConditionItem#session\n找简历"
        self.calls: list[tuple[str, dict[str, object], float, float]] = []
        self.fail_at: dict[str, BaseException] = {}
        self.fail_after: dict[str, BaseException] = {}
        self.advance_after: dict[str, float] = {}
        self.close_payload: dict[str, object] = {
            "requested": "owned-search-page",
            "outcome": "closed",
            "verified": True,
            "errorCode": None,
        }
        self.user_tabs = {"user-host-page", "user-other-page"}
        self.owned_tabs: set[str] = set()
        self._idle_deadlines: dict[str, float] = {}

    def verify_bridge(
        self,
        *,
        timeout_seconds: float,
        validate: bool = True,
    ) -> dict[str, object]:
        label = "status.validate" if validate else "status"
        self._record(label, {}, timeout_seconds)
        self._raise_if_planned(label)
        self._advance_after(label)
        return dict(self.status_payload)

    def command(
        self,
        action: str,
        params: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> OpenCliDaemonResult:
        label = self._label(action, params)
        self._record(label, params, timeout_seconds)
        self._raise_if_planned(label)
        if label == "control.activate":
            result = OpenCliDaemonResult(
                "control-1",
                data={"controlKey": params["controlKey"], "fenceToken": CONTROL_FENCE},
            )
        elif label == "tabs.find":
            result = OpenCliDaemonResult("tabs-find-1", data=list(self.host_tabs))
        elif label == "tabs.new":
            page = "owned-search-page"
            self.owned_tabs.add(page)
            idle_timeout = params.get("idleTimeout")
            assert isinstance(idle_timeout, (int, float)) and not isinstance(idle_timeout, bool)
            self._idle_deadlines[page] = self.clock() + float(idle_timeout)
            result = OpenCliDaemonResult(
                "tabs-new-1",
                data={"active": False, "placement": "borrowed-host-window"},
                page=page,
                idle_deadline_at=round(self._idle_deadlines[page] * 1000),
            )
        elif label == "browser.get-url":
            result = OpenCliDaemonResult("get-url-1", data=self.page_url, page="owned-search-page")
        elif label == "browser.state":
            result = OpenCliDaemonResult("state-1", data=self.state_text, page="owned-search-page")
        elif label == "tabs.close":
            result = OpenCliDaemonResult("tabs-close-1", data=dict(self.close_payload))
            if self.close_payload.get("outcome") in {"closed", "already_missing"}:
                self.owned_tabs.discard(str(params.get("page")))
                self._idle_deadlines.pop(str(params.get("page")), None)
        else:
            raise AssertionError(f"unexpected fake daemon call: {label}")
        self._advance_after(label)
        failure = self.fail_after.pop(label, None)
        if failure is not None:
            raise failure
        return result

    def _record(self, label: str, params: dict[str, object], timeout_seconds: float) -> None:
        assert math.isfinite(timeout_seconds)
        assert timeout_seconds > 0
        self.calls.append((label, dict(params), timeout_seconds, self.clock()))

    def _raise_if_planned(self, label: str) -> None:
        failure = self.fail_at.pop(label, None)
        if failure is not None:
            raise failure

    def _advance_after(self, label: str) -> None:
        self.clock.advance(self.advance_after.get(label, 0.0))
        self.expire_owned_tabs()

    def expire_owned_tabs(self) -> None:
        expired = [page for page, deadline in self._idle_deadlines.items() if self.clock() >= deadline]
        for page in expired:
            self.owned_tabs.discard(page)
            self._idle_deadlines.pop(page, None)

    @staticmethod
    def _label(action: str, params: dict[str, object]) -> str:
        if action == "control" and params.get("op") == "activate":
            return "control.activate"
        if action == "tabs":
            return f"tabs.{params.get('op')}"
        if action == "browser-operation":
            return f"browser.{params.get('operation')}"
        return action


def _effect(
    daemon: _FakeDaemon,
    snapshots: _SnapshotSource,
    clock: _Clock,
):
    raw_effect = create_wtscli_verify_session_effect(
        daemon=daemon,
        bridge_requirement=BRIDGE_REQUIREMENT,
        current_profile_snapshot=snapshots,
        control_key=CONTROL_KEY,
        monotonic_clock=clock,
    )

    def effect(
        request: VerifySessionRequestV1,
        deadline_at: float,
    ) -> VerifySessionResultV1 | VerifySessionFailureV1:
        return _closed_reply(request, raw_effect(request, deadline_at))

    return effect


def _closed_reply(
    request: VerifySessionRequestV1,
    outcome: dict[str, object],
) -> VerifySessionResultV1 | VerifySessionFailureV1:
    payload = dict(outcome)
    kind = payload.pop("kind", None)
    if kind == "failure":
        return VerifySessionFailureV1.model_validate(
            {
                "contract_version": "seektalent.source.verify-session.failure/v1",
                "identity": request.identity,
                **payload,
            },
            strict=True,
        )
    assert kind == "result"
    return VerifySessionResultV1.model_validate(
        {
            "contract_version": "seektalent.source.verify-session.result/v1",
            "identity": request.identity,
            **payload,
            "component_receipt_refs": request.component_receipt_refs,
        },
        strict=True,
    )


def _run_ready(
    *,
    daemon: _FakeDaemon | None = None,
    clock: _Clock | None = None,
    snapshots: _SnapshotSource | None = None,
    deadline_seconds: float = 10.0,
) -> tuple[VerifySessionResultV1 | VerifySessionFailureV1, _FakeDaemon, _Clock, _SnapshotSource]:
    clock = clock or _Clock()
    daemon = daemon or _FakeDaemon(clock)
    snapshots = snapshots or _SnapshotSource(_snapshot(), clock=clock)
    result = _effect(daemon, snapshots, clock)(_request(), clock() + deadline_seconds)
    return result, daemon, clock, snapshots


def test_effect_consumes_the_supplied_absolute_deadline_without_reanchoring_from_request() -> None:
    request = _request(deadline_value=60_000)
    clock = _Clock(500.0)
    daemon = _FakeDaemon(clock)
    snapshots = _SnapshotSource(_snapshot(request), clock=clock)
    deadline_at = 500.75

    result = _effect(daemon, snapshots, clock)(request, deadline_at)

    assert isinstance(result, VerifySessionResultV1)
    assert result.session_readiness == "ready"
    assert daemon.calls
    for _label, _params, timeout_seconds, started_at in daemon.calls:
        assert timeout_seconds <= deadline_at - started_at
    assert max(call[2] for call in daemon.calls) < 1
    assert request.identity.deadline.value == 60_000


def test_daemon_client_can_return_unvalidated_status_for_component_level_closed_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = _ready_status(extensionBridgeBuildId="different-extension-build")

    def read_status(
        _client: OpenCliDaemonClient,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None,
        timeout_seconds: float,
    ) -> tuple[int, dict[str, object]]:
        assert (method, path, body) == ("GET", "/status", None)
        assert timeout_seconds > 0
        return 200, status

    monkeypatch.setattr(OpenCliDaemonClient, "_request_json", read_status)
    client = OpenCliDaemonClient(requirement=BRIDGE_REQUIREMENT)

    assert client.verify_bridge(timeout_seconds=1, validate=False) == status
    with pytest.raises(OpenCliBrowserError) as mismatch:
        client.verify_bridge(timeout_seconds=1)
    assert mismatch.value.safe_reason_code == OPENCLI_BRIDGE_BUILD_MISMATCH


def test_expired_at_entry_returns_a_closed_failure_without_any_wtscli_call() -> None:
    request = _request()
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    snapshots = _SnapshotSource(_snapshot(request), clock=clock)

    result = _effect(daemon, snapshots, clock)(request, clock())

    assert isinstance(result, VerifySessionFailureV1)
    assert result.failure_fact == "no_effect_performed"
    assert result.failure_reason == "exchange_deadline_expired"
    assert daemon.calls == []


def test_expiry_inside_the_binding_hook_stops_before_the_first_wtscli_call() -> None:
    request = _request()
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    snapshots = _SnapshotSource(_snapshot(request), clock=clock)
    snapshots.advance_on_call[1] = 0.050

    result = _effect(daemon, snapshots, clock)(request, clock() + 0.010)

    assert isinstance(result, VerifySessionFailureV1)
    assert result.failure_reason == "exchange_deadline_expired"
    assert daemon.calls == []


def test_expiry_between_wtscli_calls_stops_every_later_command() -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    daemon.advance_after["status"] = 0.050
    snapshots = _SnapshotSource(_snapshot(), clock=clock)

    result = _effect(daemon, snapshots, clock)(_request(), clock() + 0.010)

    assert isinstance(result, VerifySessionResultV1)
    assert result.session_readiness == "not_ready"
    assert result.safe_reason_code == "liepin_opencli_timeout"
    assert [call[0] for call in daemon.calls] == ["status"]


def test_expiry_before_return_stops_close_and_uses_the_owned_tab_idle_deadline() -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    daemon.advance_after["browser.state"] = 0.100
    snapshots = _SnapshotSource(_snapshot(), clock=clock)
    before_user_tabs = set(daemon.user_tabs)

    result = _effect(daemon, snapshots, clock)(_request(), clock() + 0.050)

    assert isinstance(result, VerifySessionResultV1)
    assert result.session_readiness == "not_ready"
    assert result.safe_reason_code == "liepin_opencli_timeout"
    assert "tabs.close" not in [call[0] for call in daemon.calls]
    daemon.expire_owned_tabs()
    assert daemon.owned_tabs == set()
    assert daemon.user_tabs == before_user_tabs


@pytest.mark.parametrize(
    ("snapshot_updates", "request_updates"),
    [
        ({"runtime_attempt_fence_ref": "f" * 64}, {}),
        ({"profile_binding_ref": "stale-profile-binding"}, {}),
        ({"profile_binding_generation": 8}, {}),
        ({"provider_account_ref": "different-provider-account"}, {}),
        ({"browser_control_scope_id": "different-browser-scope"}, {}),
        ({"provider_account_subject": ""}, {}),
        ({"provider_account_ref": None}, {}),
        ({}, {"provider_account_ref": None}),
    ],
)
def test_stale_binding_or_scope_mismatch_is_closed_before_wtscli(
    snapshot_updates: dict[str, object],
    request_updates: dict[str, object],
) -> None:
    request = _request(**request_updates)
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    snapshots = _SnapshotSource(_snapshot(request, **snapshot_updates), clock=clock)

    result = _effect(daemon, snapshots, clock)(request, clock() + 10)

    assert isinstance(result, VerifySessionFailureV1)
    assert result.failure_fact == "no_effect_performed"
    assert result.failure_reason == "sidecar_not_ready"
    assert daemon.calls == []


def test_provider_account_subject_is_revalidated_before_every_subsequent_command() -> None:
    request = _request()
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    snapshots = _SnapshotSource(_snapshot(request), clock=clock)

    def change_subject_after_status(
        *,
        timeout_seconds: float,
        validate: bool = True,
    ) -> dict[str, object]:
        result = _FakeDaemon.verify_bridge(
            daemon,
            timeout_seconds=timeout_seconds,
            validate=validate,
        )
        snapshots.value = replace(snapshots.value, provider_account_subject="different-current-subject")
        return result

    daemon.verify_bridge = change_subject_after_status  # type: ignore[method-assign]
    result = _effect(daemon, snapshots, clock)(request, clock() + 10)

    assert isinstance(result, VerifySessionFailureV1)
    assert result.failure_reason == "sidecar_not_ready"
    assert [call[0] for call in daemon.calls] == ["status"]


@pytest.mark.parametrize(
    (
        "case",
        "safe_reason",
        "process",
        "bridge",
        "extension",
        "profile_lock",
        "account",
        "search_surface",
        "risk_state",
        "session",
    ),
    [
        (
            "daemon_absent",
            "liepin_opencli_daemon_not_running",
            "not_ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "daemon_stale",
            "liepin_opencli_daemon_stale",
            "not_ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "bridge_build",
            "liepin_opencli_status_unavailable",
            "ready",
            "not_ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "bridge_protocol",
            "liepin_opencli_status_unavailable",
            "ready",
            "not_ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "bridge_capability",
            "liepin_opencli_status_unavailable",
            "ready",
            "not_ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "extension_disconnected",
            "liepin_opencli_extension_disconnected",
            "ready",
            "ready",
            "not_ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "extension_build",
            "liepin_opencli_status_unavailable",
            "ready",
            "ready",
            "not_ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "host_missing",
            "liepin_host_tab_missing",
            "ready",
            "ready",
            "ready",
            "ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "host_ambiguous",
            "liepin_host_window_ambiguous",
            "ready",
            "ready",
            "ready",
            "ready",
            "not_observed",
            "not_observed",
            "not_observed",
            "not_ready",
        ),
        (
            "login_required",
            "liepin_opencli_login_required",
            "ready",
            "ready",
            "ready",
            "ready",
            "login_required",
            "not_ready",
            "not_observed",
            "not_ready",
        ),
        (
            "identity_intercept",
            "liepin_opencli_identity_intercept",
            "ready",
            "ready",
            "ready",
            "ready",
            "not_ready",
            "not_ready",
            "not_observed",
            "not_ready",
        ),
        (
            "risk_page",
            "liepin_opencli_risk_page",
            "ready",
            "ready",
            "ready",
            "ready",
            "not_ready",
            "not_ready",
            "risk_page",
            "not_ready",
        ),
        (
            "unknown_modal",
            "liepin_opencli_unknown_modal",
            "ready",
            "ready",
            "ready",
            "ready",
            "not_ready",
            "not_ready",
            "clear",
            "not_ready",
        ),
        (
            "search_not_ready",
            "liepin_opencli_search_not_ready",
            "ready",
            "ready",
            "ready",
            "ready",
            "ready",
            "not_ready",
            "clear",
            "not_ready",
        ),
        ("ready", None, "ready", "ready", "ready", "ready", "ready", "ready", "clear", "ready"),
    ],
)
def test_closed_component_mapping(
    case: str,
    safe_reason: str | None,
    process: str,
    bridge: str,
    extension: str,
    profile_lock: str,
    account: str,
    search_surface: str,
    risk_state: str,
    session: str,
) -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    if case == "daemon_absent":
        daemon.fail_at["status"] = OpenCliBrowserError(OPENCLI_DAEMON_NOT_RUNNING)
    elif case == "daemon_stale":
        daemon.fail_at["status"] = OpenCliBrowserError(OPENCLI_DAEMON_STALE)
    elif case == "bridge_build":
        daemon.status_payload["bridgeBuildId"] = "wrong-daemon-build"
    elif case == "bridge_protocol":
        daemon.status_payload["protocolVersion"] = {"major": 2, "minor": 0}
    elif case == "bridge_capability":
        daemon.status_payload["capabilities"] = ["browser.operations.v1"]
    elif case == "extension_disconnected":
        daemon.status_payload["extensionConnected"] = False
    elif case == "extension_build":
        daemon.status_payload["extensionBridgeBuildId"] = "wrong-extension-build"
    elif case == "host_missing":
        daemon.host_tabs = []
    elif case == "host_ambiguous":
        daemon.host_tabs = [
            {
                "page": "user-host-a",
                "url": "https://h.liepin.com/",
                "windowId": 17,
                "active": True,
                "windowFocused": False,
            },
            {
                "page": "user-host-b",
                "url": "https://h.liepin.com/resume/search",
                "windowId": 18,
                "active": True,
                "windowFocused": False,
            },
        ]
    elif case == "login_required":
        daemon.state_text = "请登录后继续"
    elif case == "identity_intercept":
        daemon.page_url = "https://lpt.liepin.com/"
        daemon.state_text = "请选择招聘身份"
    elif case == "risk_page":
        daemon.page_url = "https://safe.liepin.com/v/intercept/verifysms"
        daemon.state_text = "安全验证"
    elif case == "unknown_modal":
        daemon.page_url = "https://www.liepin.com/resume/detail/123"
        daemon.state_text = "候选人详情"
    elif case == "search_not_ready":
        daemon.page_url = "https://h.liepin.com/"
        daemon.state_text = "猎聘首页"
    snapshots = _SnapshotSource(_snapshot(), clock=clock)
    before_user_tabs = set(daemon.user_tabs)

    result = _effect(daemon, snapshots, clock)(_request(), clock() + 10)

    assert isinstance(result, VerifySessionResultV1)
    assert (
        result.safe_reason_code,
        result.process_readiness,
        result.bridge_readiness,
        result.extension_readiness,
        result.profile_lock_readiness,
        result.account_readiness,
        result.search_surface_readiness,
        result.risk_state,
        result.session_readiness,
    ) == (safe_reason, process, bridge, extension, profile_lock, account, search_surface, risk_state, session)
    assert result.actual_profile_binding_ref == "profile-binding-wtscli-1"
    assert result.actual_profile_binding_generation == 7
    assert result.actual_provider_account_ref == "provider-account-wtscli-1"
    assert result.component_receipt_refs == ("component-receipt-wtscli-1",)
    assert daemon.user_tabs == before_user_tabs
    assert daemon.owned_tabs == set()
    for label, params, _timeout, _started in daemon.calls:
        if label == "tabs.close":
            assert "controlKey" not in params
            assert "fenceToken" not in params


def test_a_host_tab_without_account_search_and_risk_proof_is_never_ready() -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    daemon.fail_at["tabs.new"] = OpenCliBrowserError(OPENCLI_TARGET_NOT_FOUND)
    snapshots = _SnapshotSource(_snapshot(), clock=clock)

    result = _effect(daemon, snapshots, clock)(_request(), clock() + 10)

    assert isinstance(result, VerifySessionResultV1)
    assert result.session_readiness == "not_ready"
    assert result.account_readiness == "not_observed"
    assert result.search_surface_readiness == "not_observed"
    assert result.risk_state == "not_observed"
    assert result.safe_reason_code == "liepin_opencli_target_not_found"


@pytest.mark.parametrize(
    "failure",
    [
        OpenCliBrowserError(OPENCLI_TIMEOUT),
        RuntimeError(f"{RAW_DOM} {RAW_STDERR}"),
        EOFError(f"{SEARCH_URL} EOF"),
        ConnectionResetError("sidecar process closed"),
    ],
)
def test_owned_tab_is_reclaimed_after_typed_failure_exception_eof_or_process_close(
    failure: BaseException,
) -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    daemon.fail_at["browser.state"] = failure
    snapshots = _SnapshotSource(_snapshot(), clock=clock)
    before_user_tabs = set(daemon.user_tabs)

    result = _effect(daemon, snapshots, clock)(_request(), clock() + 10)

    assert isinstance(result, VerifySessionResultV1)
    assert result.session_readiness == "not_ready"
    assert [call[0] for call in daemon.calls][-1] == "tabs.close"
    assert daemon.owned_tabs == set()
    assert daemon.user_tabs == before_user_tabs


def test_lost_tabs_new_response_leaves_only_the_deadline_bounded_idle_reclaim() -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    daemon.fail_after["tabs.new"] = EOFError("response lost after owned tab creation")
    snapshots = _SnapshotSource(_snapshot(), clock=clock)
    before_user_tabs = set(daemon.user_tabs)

    result = _effect(daemon, snapshots, clock)(_request(), clock() + 10)

    assert isinstance(result, VerifySessionResultV1)
    assert result.session_readiness == "not_ready"
    assert result.safe_reason_code == "liepin_opencli_status_unavailable"
    assert daemon.owned_tabs == {"owned-search-page"}
    assert "tabs.close" not in [call[0] for call in daemon.calls]
    clock.advance(10)
    daemon.expire_owned_tabs()
    assert daemon.owned_tabs == set()
    assert daemon.user_tabs == before_user_tabs


def test_unverified_close_is_closed_not_ready_and_retains_the_idle_reclaim() -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    daemon.close_payload = {
        "requested": "owned-search-page",
        "outcome": "failed",
        "verified": False,
        "errorCode": "tab_close_failed",
    }
    snapshots = _SnapshotSource(_snapshot(), clock=clock)
    before_user_tabs = set(daemon.user_tabs)

    result = _effect(daemon, snapshots, clock)(_request(), clock() + 10)

    assert isinstance(result, VerifySessionResultV1)
    assert result.session_readiness == "not_ready"
    assert result.safe_reason_code == "liepin_owned_tab_missing"
    assert daemon.owned_tabs == {"owned-search-page"}
    clock.advance(10)
    daemon.expire_owned_tabs()
    assert daemon.owned_tabs == set()
    assert daemon.user_tabs == before_user_tabs


def test_control_bearers_url_dom_and_stderr_never_escape_results_errors_repr_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _Clock()
    daemon = _FakeDaemon(clock)
    daemon.page_url = f"{SEARCH_URL}?secret=URL-CANARY"
    daemon.fail_at["browser.state"] = RuntimeError(
        f"{RAW_RUNTIME_FENCE} {CONTROL_KEY} {CONTROL_FENCE} {RAW_DOM} {RAW_STDERR}"
    )
    snapshots = _SnapshotSource(_snapshot(), clock=clock)
    effect = _effect(daemon, snapshots, clock)

    with caplog.at_level(logging.DEBUG):
        result = effect(_request(), clock() + 10)

    assert isinstance(result, VerifySessionResultV1)
    surfaces = "\n".join((result.model_dump_json(), str(result), repr(result), str(effect), repr(effect), caplog.text))
    for secret in (
        RAW_RUNTIME_FENCE,
        CONTROL_KEY,
        str(CONTROL_FENCE),
        SEARCH_URL,
        "URL-CANARY",
        RAW_DOM,
        RAW_STDERR,
    ):
        assert secret not in surfaces


def test_adapter_module_has_zero_production_callers_and_does_not_import_worker_or_provider_composition() -> None:
    project_root = Path(__file__).parents[1]
    adapter_path = project_root / "src" / "seektalent" / "wtscli_verify_session_adapter.py"
    source = adapter_path.read_text(encoding="utf-8")
    callers = [
        path.relative_to(project_root).as_posix()
        for path in (project_root / "src").rglob("*.py")
        if path != adapter_path and "wtscli_verify_session_adapter" in path.read_text(encoding="utf-8")
    ]
    packaged_builder = (project_root / "tools" / "build_packaged_sidecar.py").read_text(encoding="utf-8")
    packaged_bootstrap = (project_root / "src" / "seektalent" / "sidecar_bootstrap.py").read_text(encoding="utf-8")

    assert callers == []
    assert "LiepinOpenCliWorkerClient" not in source
    assert "OpenCliRetriever" not in source
    assert "LiepinSiteAdapter" not in source
    assert "seektalent.source_port" not in source
    assert "wtscli-placeholder" in packaged_builder
    assert "wtscli_verify_session_adapter" not in packaged_builder
    assert "wtscli_verify_session_adapter" not in packaged_bootstrap
    assert "deterministic test facts without WTSCLI" in packaged_bootstrap


def test_adapter_result_is_strictly_closed_data_without_raw_daemon_payload() -> None:
    result, daemon, _clock, _snapshots = _run_ready()

    assert isinstance(result, VerifySessionResultV1)
    payload = json.loads(result.model_dump_json())
    assert set(payload) == set(VerifySessionResultV1.model_fields)
    serialized = json.dumps(payload, sort_keys=True)
    assert "pid" not in serialized
    assert "daemonVersion" not in serialized
    assert "extensionImplementation" not in serialized
    assert "user-host-page" not in serialized
    assert "owned-search-page" not in serialized
    assert SEARCH_URL not in serialized
    assert daemon.owned_tabs == set()
