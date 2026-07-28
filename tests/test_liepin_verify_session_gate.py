from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import seektalent.liepin_verify_session_gate as gate_module
from seektalent.liepin_verify_session_gate import (
    ProductionLiepinVerifySessionGate,
    _normalized_boundary_reason,
    _raise_reason,
)
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.sources.liepin.reason_codes import public_source_problem_message
from tests.browser_bridge_bundle_fixtures import exact_browser_bridge_requirement
from tests.settings_factory import make_settings


class _Daemon:
    def __init__(self) -> None:
        self.closed = False

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
    monkeypatch.setattr(gate_module, "ensure_opencli_runtime", lambda: runtime)
    monkeypatch.setattr(
        gate_module,
        "runtime_requirement",
        lambda actual: exact_browser_bridge_requirement() if actual is runtime else None,
    )
    def connect(actual: object, *, verify_timeout_seconds: float) -> _Daemon:
        connect_calls.append((actual, verify_timeout_seconds))
        assert actual is runtime
        return daemon

    monkeypatch.setattr(gate_module, "connect_installed_opencli_daemon", connect)

    def probe(**kwargs: object) -> str | None:
        probe_calls.append(kwargs)
        return reason

    monkeypatch.setattr(gate_module, "probe_wtscli_liepin_session", probe)
    return runtime, daemon, probe_calls, connect_calls


def test_production_gate_executes_direct_wtscli_success_path(
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
    assert len(probe_calls) == 1
    assert probe_calls[0]["daemon"] is daemon
    assert probe_calls[0]["bridge_requirement"] == exact_browser_bridge_requirement()
    assert probe_calls[0]["deadline_at"] > 0
    assert daemon.closed is True


def test_production_gate_maps_direct_wtscli_failure_and_closes_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, daemon, probe_calls, connect_calls = _install_bounded_runtime(
        monkeypatch,
        reason="liepin_opencli_login_required",
    )
    gate = ProductionLiepinVerifySessionGate(make_settings())

    with pytest.raises(LiepinWorkerModeError, match="需要登录") as raised:
        asyncio.run(gate.verify())

    assert raised.value.code == "liepin_opencli_login_required"
    assert len(connect_calls) == 1
    assert len(probe_calls) == 1
    assert daemon.closed is True


def test_production_gate_has_no_source_port_or_runtime_control_side_effects() -> None:
    source = gate_module.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")

    assert "seektalent.source_port" not in text
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
