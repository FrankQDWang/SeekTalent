from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.opencli_browser import daemon_process
from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.reason_codes import OPENCLI_BRIDGE_INTEGRITY_FAILED
from seektalent.wtscli_runtime import WtsCliRuntime
from tests.browser_bridge_bundle_fixtures import (
    exact_browser_bridge_requirement,
    write_browser_bridge_bundle,
)


class FakeDaemonClient:
    def __init__(self) -> None:
        self.verify_calls: list[float] = []
        self.closed = False

    def verify_bridge(self, *, timeout_seconds: float) -> dict[str, object]:
        self.verify_calls.append(timeout_seconds)
        return {"ok": True}

    def close(self) -> None:
        self.closed = True


def _runtime(tmp_path: Path) -> WtsCliRuntime:
    bundle = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle)
    manifest = bundle / "bridge-manifest.json"
    node = tmp_path / "bin" / "node"
    main = tmp_path / "opencli" / "main.js"
    node.parent.mkdir(parents=True)
    main.parent.mkdir(parents=True)
    node.write_text("node", encoding="utf-8")
    main.write_text("opencli", encoding="utf-8")
    return WtsCliRuntime(
        node=node,
        wtscli_main=main,
        bridge_manifest=manifest,
        requirement=exact_browser_bridge_requirement(),
    )


def test_read_only_connection_verifies_existing_daemon_without_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeDaemonClient()
    monkeypatch.setattr(daemon_process, "OpenCliDaemonClient", lambda **_kwargs: client)

    connected = daemon_process.connect_existing_opencli_daemon_read_only(
        _runtime(tmp_path),
        context_id="chrome-profile",
        verify_timeout_seconds=0.5,
    )

    assert connected is client
    assert client.verify_calls == [0.5]
    assert client.closed is False


def test_read_only_connection_closes_client_when_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeDaemonClient()

    def fail_verify(*, timeout_seconds: float) -> dict[str, object]:
        del timeout_seconds
        raise OpenCliBrowserError("opencli_extension_disconnected")

    client.verify_bridge = fail_verify  # type: ignore[method-assign]
    monkeypatch.setattr(daemon_process, "OpenCliDaemonClient", lambda **_kwargs: client)

    with pytest.raises(OpenCliBrowserError, match="opencli_extension_disconnected"):
        daemon_process.connect_existing_opencli_daemon_read_only(_runtime(tmp_path))

    assert client.closed is True


def test_read_only_connection_requires_exact_installed_manifest(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.bridge_manifest.unlink()

    with pytest.raises(OpenCliBrowserError) as captured:
        daemon_process.connect_existing_opencli_daemon_read_only(runtime)

    assert captured.value.safe_reason_code == OPENCLI_BRIDGE_INTEGRITY_FAILED
