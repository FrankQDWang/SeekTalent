from __future__ import annotations

import pytest

from seektalent.liepin_verify_session_gate import (
    _normalized_boundary_reason,
    _raise_reason,
)
from seektalent.providers.liepin.client import LiepinWorkerModeError


@pytest.mark.parametrize(
    ("boundary_reason", "liepin_reason"),
    [
        ("opencli_extension_disconnected", "liepin_opencli_extension_disconnected"),
        ("opencli_daemon_not_running", "liepin_opencli_daemon_not_running"),
        ("opencli_daemon_stale", "liepin_opencli_daemon_stale"),
        ("opencli_bridge_build_mismatch", "liepin_opencli_bridge_build_mismatch"),
        ("opencli_bridge_protocol_mismatch", "liepin_opencli_bridge_protocol_mismatch"),
        ("opencli_bridge_capability_missing", "liepin_opencli_bridge_capability_missing"),
        ("sidecar_not_ready", "liepin_opencli_stale_ref"),
        ("exchange_deadline_expired", "liepin_opencli_timeout"),
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
    assert any(token in str(raised.value) for token in ("请", "重试", "重新"))
