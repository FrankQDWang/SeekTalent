from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_process import (
    OPENCLI_DAEMON_VERIFY_TIMEOUT_SECONDS,
)
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_FOREIGN_OWNER,
)
from seektalent.opencli_launcher import OpenCliRuntime
from seektalent import wtscli_connection_supervisor as supervisor
from tests.browser_bridge_bundle_fixtures import (
    exact_browser_bridge_requirement,
    write_browser_bridge_bundle,
)


def _runtime(tmp_path: Path) -> OpenCliRuntime:
    bundle = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle)
    node = tmp_path / "node"
    main = tmp_path / "main.js"
    node.write_text("node", encoding="utf-8")
    main.write_text("main", encoding="utf-8")
    return OpenCliRuntime(
        node=node,
        opencli_main=main,
        bridge_manifest=bundle / "bridge-manifest.json",
        requirement=exact_browser_bridge_requirement(),
    )


class _Client:
    def __init__(self, status: dict[str, object] | str) -> None:
        self.status = status
        self.requirement = exact_browser_bridge_requirement()
        self.closed = False

    def verify_bridge(self, *, timeout_seconds: float) -> dict[str, object]:
        assert 0 < timeout_seconds <= 40
        if isinstance(self.status, str):
            raise OpenCliBrowserError(self.status)
        return self.status

    def close(self) -> None:
        self.closed = True


class _SlowReceiptClient(_Client):
    def __init__(self, status: dict[str, object]) -> None:
        super().__init__(status)
        self.timeouts: list[float] = []

    def verify_bridge(self, *, timeout_seconds: float) -> dict[str, object]:
        self.timeouts.append(timeout_seconds)
        if timeout_seconds < 0.35:
            raise OpenCliBrowserError(OPENCLI_EXTENSION_DISCONNECTED)
        assert isinstance(self.status, dict)
        return self.status


def _status() -> dict[str, object]:
    build = exact_browser_bridge_requirement().bridge_build_id
    owner = hashlib.sha256(b"owned").hexdigest()
    return {
        "bridgeBuildId": build,
        "extensionBridgeBuildId": build,
        "ownerTokenHash": owner,
        "lastSeenAt": 1234,
    }


def test_readiness_deadline_matches_the_reconnect_envelope() -> None:
    assert supervisor.WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS == 40
    assert supervisor.WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS == OPENCLI_DAEMON_VERIFY_TIMEOUT_SECONDS


def test_supervisor_returns_only_privacy_safe_exact_connection_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _Client(_status())
    monkeypatch.setattr(
        supervisor,
        "connect_installed_opencli_daemon",
        lambda *_args, **_kwargs: client,
    )
    clock = iter((10.0, 10.010, 10.012))

    receipt = supervisor.InstalledWtsCliConnectionSupervisor(
        _runtime(tmp_path),
        monotonic_clock=lambda: next(clock),
    ).await_ready(timeout_seconds=40)

    assert receipt.daemon_build_id == exact_browser_bridge_requirement().bridge_build_id
    assert receipt.extension_build_id == receipt.daemon_build_id
    assert receipt.endpoint == "127.0.0.1:19826"
    assert receipt.ownership_ref.startswith("sha256:")
    assert receipt.elapsed_milliseconds == 12
    assert client.closed is True
    assert not hasattr(receipt, "url")
    assert not hasattr(receipt, "token")
    assert not hasattr(receipt, "account")


def test_supervisor_receipt_probe_can_use_more_than_300ms_within_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _SlowReceiptClient(_status())
    monkeypatch.setattr(
        supervisor,
        "connect_installed_opencli_daemon",
        lambda *_args, **_kwargs: client,
    )
    clock = iter((10.0, 10.1, 10.45))

    receipt = supervisor.InstalledWtsCliConnectionSupervisor(
        _runtime(tmp_path),
        monotonic_clock=lambda: next(clock),
    ).await_ready(timeout_seconds=1)

    assert client.timeouts == [pytest.approx(0.9)]
    assert receipt.elapsed_milliseconds == 450
    assert client.closed is True


@pytest.mark.parametrize(
    "reason",
    [
        OPENCLI_DAEMON_NOT_RUNNING,
        OPENCLI_EXTENSION_DISCONNECTED,
        OPENCLI_BRIDGE_BUILD_MISMATCH,
        OPENCLI_FOREIGN_OWNER,
    ],
)
def test_supervisor_preserves_exact_pre_dispatch_cause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
) -> None:
    def fail(*_args: object, **_kwargs: object):
        raise OpenCliBrowserError(reason)

    monkeypatch.setattr(
        supervisor,
        "connect_installed_opencli_daemon",
        fail,
    )

    with pytest.raises(supervisor.WtsCliConnectionError) as captured:
        supervisor.InstalledWtsCliConnectionSupervisor(
            _runtime(tmp_path),
        ).await_ready(timeout_seconds=0.1)

    assert captured.value.safe_reason_code == reason


def test_supervisor_converts_receipt_probe_failure_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _Client(OPENCLI_EXTENSION_DISCONNECTED)
    monkeypatch.setattr(
        supervisor,
        "connect_installed_opencli_daemon",
        lambda *_args, **_kwargs: client,
    )

    with pytest.raises(supervisor.WtsCliConnectionError) as captured:
        supervisor.InstalledWtsCliConnectionSupervisor(
            _runtime(tmp_path),
        ).await_ready(timeout_seconds=40)

    assert captured.value.safe_reason_code == OPENCLI_EXTENSION_DISCONNECTED
    assert client.closed is True


def test_supervisor_does_not_probe_past_main_owned_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _Client(_status())
    monkeypatch.setattr(
        supervisor,
        "connect_installed_opencli_daemon",
        lambda *_args, **_kwargs: client,
    )
    clock = iter((10.0, 50.0))

    with pytest.raises(supervisor.WtsCliConnectionError) as captured:
        supervisor.InstalledWtsCliConnectionSupervisor(
            _runtime(tmp_path),
            monotonic_clock=lambda: next(clock),
        ).await_ready(timeout_seconds=40)

    assert captured.value.safe_reason_code == "wtscli_readiness_deadline_exceeded"
    assert client.closed is True
