from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import seektalent.liepin_verify_session_gate as gate_module
from seektalent.liepin_verify_session_gate import (
    ProductionLiepinVerifySessionGate,
    _normalized_boundary_reason,
    _raise_reason,
)
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.providers.liepin.browser_environment import (
    BrowserBridgeEnvironmentStatus,
)
from seektalent.sources.liepin.reason_codes import public_source_problem_message
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestV1,
    VerifySessionResultV1,
)
from seektalent.wtscli_verify_session_adapter import WtsCliCurrentProfileSnapshot
from tests.browser_bridge_bundle_fixtures import exact_browser_bridge_requirement
from tests.settings_factory import make_settings


class _Daemon:
    def __init__(self) -> None:
        self.closed = False
        self.verify_calls: list[bool] = []

    def verify_bridge(
        self,
        *,
        timeout_seconds: float,
        validate: bool = True,
    ) -> dict[str, object]:
        assert timeout_seconds > 0
        self.verify_calls.append(validate)
        requirement = exact_browser_bridge_requirement()
        return {
            "ok": True,
            "pid": 41001,
            "daemonVersion": "0.1.0",
            "implementation": requirement.implementation,
            "bridgeBuildId": requirement.bridge_build_id,
            "protocolVersion": {
                "major": requirement.protocol_major,
                "minor": requirement.protocol_minor,
            },
            "transportProtocol": {
                "name": requirement.runtime_identity.transport.protocol.name,
                "version": {
                    "major": requirement.protocol_major,
                    "minor": requirement.protocol_minor,
                },
            },
            "ownerTokenHash": "0" * 64,
            "capabilities": sorted(requirement.capabilities),
            "port": requirement.runtime_identity.endpoint.port,
            "extensionConnected": True,
            "extensionVersion": requirement.extension.version,
            "extensionImplementation": requirement.implementation,
            "extensionBridgeBuildId": requirement.bridge_build_id,
            "extensionProtocolVersion": {
                "major": requirement.protocol_major,
                "minor": requirement.protocol_minor,
            },
            "extensionCapabilities": sorted(requirement.capabilities),
        }

    def command(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("observe readiness must not issue browser commands")

    def close(self) -> None:
        self.closed = True


def _install_bounded_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reason: str | None,
) -> tuple[
    object,
    _Daemon,
    list[dict[str, object]],
    list[tuple[object, float]],
]:
    runtime = object()
    daemon = _Daemon()
    probe_calls: list[dict[str, object]] = []
    connect_calls: list[tuple[object, float]] = []
    monkeypatch.setattr(gate_module, "inspect_wtscli_runtime", lambda: runtime)
    monkeypatch.setattr(
        gate_module,
        "_check_environment",
        lambda actual: (
            BrowserBridgeEnvironmentStatus(
                ok=True,
                liepin_enabled=True,
                reason_code="wtscli_ready",
                message="ready",
                action="continue",
                extension_dir=Path("/installed/chrome-extension/wtscli"),
                bridge_build_id="bridge-build",
            )
            if actual is runtime
            else None
        ),
    )
    monkeypatch.setattr(
        gate_module,
        "runtime_requirement",
        lambda actual: exact_browser_bridge_requirement() if actual is runtime else None,
    )

    def connect(actual: object, *, verify_timeout_seconds: float) -> _Daemon:
        connect_calls.append((actual, verify_timeout_seconds))
        assert actual is runtime
        return daemon

    monkeypatch.setattr(
        gate_module,
        "connect_existing_opencli_daemon_read_only",
        connect,
    )
    del reason
    return runtime, daemon, probe_calls, connect_calls


def test_production_gate_observes_bridge_without_browser_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, daemon, probe_calls, connect_calls = _install_bounded_runtime(
        monkeypatch,
        reason=None,
    )
    gate = ProductionLiepinVerifySessionGate(
        make_settings(liepin_opencli_timeout_seconds=11)
    )

    asyncio.run(gate.verify())

    assert len(connect_calls) == 1
    assert connect_calls[0][0] is runtime
    assert 10 < connect_calls[0][1] <= 11
    assert probe_calls == []
    assert daemon.verify_calls == [False, True]
    assert daemon.closed is True


def test_production_gate_has_no_public_mutating_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, daemon, probe_calls, connect_calls = _install_bounded_runtime(
        monkeypatch,
        reason="liepin_opencli_login_required",
    )
    gate = ProductionLiepinVerifySessionGate(make_settings())

    assert not hasattr(gate, "prepare")
    assert connect_calls == []
    assert probe_calls == []
    assert daemon.closed is False


def test_mutating_prepare_connects_daemon_before_full_environment_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = VerifySessionRequestV1.create(
        run_id="run-prepare-order",
        operation_id="verify-prepare-order",
        attempt_no=1,
        idempotency_key="verify-prepare-order-key",
        correlation_id="verify-prepare-order-correlation",
        accepted_requirement_revision_id="requirement-prepare-order",
        runtime_attempt_fence_token="prepare-order-fence-" + "f" * 64,
        profile_binding_generation=1,
        browser_control_scope_id="browser-scope-prepare-order",
        deadline_value=60_000,
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
        delivery_mode="initial",
        dispatch_intent_id="dispatch-prepare-order",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-prepare-order",
        profile_binding_ref="profile-binding-prepare-order",
        provider_account_ref="provider-account-prepare-order",
        required_capabilities=("bridge", "extension", "profile_lock", "search_surface"),
        user_interaction_policy="observe_only",
        verify_search_surface=True,
        component_receipt_refs=("receipt-prepare-order",),
    )
    snapshot = WtsCliCurrentProfileSnapshot(
        runtime_attempt_fence_ref=request.identity.runtime_attempt_fence_ref,
        profile_binding_ref=request.profile_binding_ref,
        profile_binding_generation=request.identity.profile_binding_generation,
        provider_account_ref=request.provider_account_ref,
        provider_account_subject="liepin-opencli-local-browser-profile",
        browser_control_scope_id=request.identity.browser_control_scope_id,
    )
    runtime = SimpleNamespace(
        bridge_manifest=Path("/installed/.seektalent/browser-bridge/bridge-manifest.json"),
        node=Path("/installed/node"),
    )
    events: list[str] = []

    monkeypatch.setattr(
        gate_module,
        "check_installed_browser_bridge_bundle",
        lambda **kwargs: (
            events.append("bundle"),
            BrowserBridgeEnvironmentStatus(
                ok=True,
                liepin_enabled=False,
                reason_code="wtscli_bundle_ready",
                message="bundle",
                action="continue",
                extension_dir=Path("/installed/.seektalent/chrome-extension/wtscli"),
                bridge_build_id="bridge-build",
            ),
        )[1],
    )
    monkeypatch.setattr(
        gate_module,
        "runtime_requirement",
        lambda _runtime: (events.append("requirement"), exact_browser_bridge_requirement())[1],
    )

    class _PreparedDaemon:
        def close(self) -> None:
            events.append("close")

    class _Supervisor:
        def __init__(self) -> None:
            self.runtime = runtime

        def ensure_ready(self, *, timeout_seconds: float) -> None:
            assert timeout_seconds > 0
            events.append("ensure")

        def connect_existing(self, *, verify_timeout_seconds: float) -> _PreparedDaemon:
            assert verify_timeout_seconds > 0
            events.append("connect")
            return _PreparedDaemon()

    def full_environment_probe(_runtime: object) -> BrowserBridgeEnvironmentStatus:
        assert events[-1] == "connect"
        events.append("environment")
        return BrowserBridgeEnvironmentStatus(
            ok=True,
            liepin_enabled=True,
            reason_code="wtscli_ready",
            message="ready",
            action="continue",
            extension_dir=Path("/installed/.seektalent/chrome-extension/wtscli"),
            bridge_build_id="bridge-build",
        )

    monkeypatch.setattr(gate_module, "_check_environment", full_environment_probe)
    monkeypatch.setattr(
        gate_module,
        "run_wtscli_verify_session_effect",
        lambda **_kwargs: (
            events.append("effect"),
            lambda _request, _deadline: VerifySessionResultV1.model_validate(
                {
                    "contract_version": "seektalent.source.verify-session.result/v1",
                    "identity": request.identity,
                    "process_readiness": "ready",
                    "bridge_readiness": "ready",
                    "extension_readiness": "ready",
                    "profile_lock_readiness": "ready",
                    "account_readiness": "ready",
                    "search_surface_readiness": "ready",
                    "risk_state": "clear",
                    "session_readiness": "ready",
                    "actual_profile_binding_ref": request.profile_binding_ref,
                    "actual_provider_account_ref": request.provider_account_ref,
                    "actual_profile_binding_generation": request.identity.profile_binding_generation,
                    "safe_reason_code": None,
                    "user_action": None,
                    "component_receipt_refs": ("receipt-prepare-order",),
                }
            ),
        )[1],
    )

    gate_module._prepare_session_mutating(
        make_settings(liepin_opencli_timeout_seconds=11),
        request=request,
        lifecycle_supervisor=_Supervisor(),
        current_profile_snapshot=snapshot,
    )

    assert events == [
        "bundle",
        "requirement",
        "ensure",
        "connect",
        "environment",
        "effect",
        "close",
    ]


def test_production_gate_stops_before_verify_session_when_environment_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, daemon, probe_calls, connect_calls = _install_bounded_runtime(
        monkeypatch,
        reason=None,
    )
    monkeypatch.setattr(
        gate_module,
        "_check_environment",
        lambda actual: (
            BrowserBridgeEnvironmentStatus(
                ok=False,
                liepin_enabled=False,
                reason_code="wtscli_extension_not_loaded_or_disabled",
                message="extension disconnected",
                action="reload extension",
                extension_dir=Path("/installed/chrome-extension/wtscli"),
            )
            if actual is runtime
            else None
        ),
    )

    with pytest.raises(LiepinWorkerModeError) as raised:
        asyncio.run(ProductionLiepinVerifySessionGate(make_settings()).verify())

    assert raised.value.code == "liepin_opencli_extension_disconnected"
    assert connect_calls == []
    assert probe_calls == []
    assert daemon.closed is False


def test_production_gate_preserves_verified_protocol_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _daemon, probe_calls, connect_calls = _install_bounded_runtime(
        monkeypatch,
        reason=None,
    )
    monkeypatch.setattr(
        gate_module,
        "_check_environment",
        lambda actual: (
            BrowserBridgeEnvironmentStatus(
                ok=False,
                liepin_enabled=False,
                reason_code="wtscli_extension_stale_worker",
                message="protocol mismatch",
                action="reload extension",
                extension_dir=Path("/installed/chrome-extension/wtscli"),
                bridge_failure_reason="opencli_bridge_protocol_mismatch",
            )
            if actual is runtime
            else None
        ),
    )

    with pytest.raises(LiepinWorkerModeError) as raised:
        asyncio.run(ProductionLiepinVerifySessionGate(make_settings()).verify())

    assert raised.value.code == "liepin_opencli_bridge_protocol_mismatch"
    assert connect_calls == []
    assert probe_calls == []


def test_production_observe_gate_has_no_runtime_control_side_effects() -> None:
    source = gate_module.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")

    assert "seektalent_runtime_control" not in text
    assert "_register_source_port_endpoint" not in text
    assert "_InProcessEndpoint" not in text
    assert "_session_pair" not in text
    assert "_ReadyConnectionSupervisor" not in text


@pytest.mark.parametrize(
    ("boundary_reason", "liepin_reason"),
    [
        ("opencli_extension_disconnected", "liepin_opencli_extension_disconnected"),
        ("opencli_daemon_not_running", "liepin_opencli_daemon_not_running"),
        ("opencli_daemon_stale", "liepin_opencli_daemon_stale"),
        ("opencli_bridge_build_mismatch", "liepin_opencli_bridge_build_mismatch"),
        ("opencli_bridge_protocol_mismatch", "liepin_opencli_bridge_protocol_mismatch"),
        ("opencli_bridge_capability_missing", "liepin_opencli_bridge_capability_missing"),
    ],
)
def test_boundary_failures_keep_distinct_liepin_safe_reasons(
    boundary_reason: str,
    liepin_reason: str,
) -> None:
    assert _normalized_boundary_reason(boundary_reason) == liepin_reason


@pytest.mark.parametrize(
    "reason",
    [
        "liepin_host_tab_missing",
        "liepin_opencli_login_required",
        "liepin_opencli_identity_intercept",
        "liepin_opencli_risk_page",
        "liepin_opencli_bridge_build_mismatch",
        "liepin_opencli_stale_control_fence",
    ],
)
def test_gate_failures_have_stable_actionable_chinese_messages(reason: str) -> None:
    with pytest.raises(LiepinWorkerModeError) as raised:
        _raise_reason(reason)

    assert raised.value.code == reason
    assert str(raised.value) == public_source_problem_message(
        reason,
        source_label="猎聘",
    )
