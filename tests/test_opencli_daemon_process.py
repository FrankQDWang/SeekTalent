from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.opencli_browser import daemon_process
from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_STATUS_UNAVAILABLE,
)
from seektalent.opencli_launcher import OpenCliRuntime
from tests.browser_bridge_bundle_fixtures import (
    exact_browser_bridge_requirement,
    write_browser_bridge_bundle,
)


class FakeDaemonClient:
    def __init__(self, outcomes: list[str | None]) -> None:
        self.outcomes = outcomes
        self.verify_calls: list[float] = []

    def verify_bridge(self, *, timeout_seconds: float) -> dict[str, object]:
        self.verify_calls.append(timeout_seconds)
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise OpenCliBrowserError(outcome)
        return {"ok": True}


def _runtime(tmp_path: Path) -> OpenCliRuntime:
    bundle = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle)
    manifest = bundle / "bridge-manifest.json"
    node = tmp_path / "bin" / "node"
    main = tmp_path / "opencli" / "main.js"
    node.parent.mkdir(parents=True)
    main.parent.mkdir(parents=True)
    node.write_text("node", encoding="utf-8")
    main.write_text("opencli", encoding="utf-8")
    return OpenCliRuntime(
        node=node,
        opencli_main=main,
        bridge_manifest=manifest,
        requirement=exact_browser_bridge_requirement(),
    )


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeDaemonClient,
) -> list[dict[str, object]]:
    constructor_calls: list[dict[str, object]] = []

    def build_client(**kwargs: object) -> FakeDaemonClient:
        constructor_calls.append(kwargs)
        return client

    monkeypatch.setattr(daemon_process, "OpenCliDaemonClient", build_client)
    return constructor_calls


def test_connect_reuses_ready_daemon_without_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeDaemonClient([None])
    calls = _install_fake_client(monkeypatch, client)
    monkeypatch.setattr(
        daemon_process,
        "_restart_installed_daemon",
        lambda _runtime: pytest.fail("ready daemon must not restart"),
    )

    connected = daemon_process.connect_installed_opencli_daemon(
        _runtime(tmp_path), context_id="chrome-profile"
    )

    assert connected is client
    assert calls[0]["context_id"] == "chrome-profile"
    assert len(client.verify_calls) == 1


def test_connect_keeps_running_daemon_when_extension_is_disconnected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeDaemonClient([OPENCLI_EXTENSION_DISCONNECTED])
    _install_fake_client(monkeypatch, client)
    monkeypatch.setattr(
        daemon_process,
        "_restart_installed_daemon",
        lambda _runtime: pytest.fail("extension setup errors must not restart the daemon"),
    )

    connected = daemon_process.connect_installed_opencli_daemon(_runtime(tmp_path))

    assert connected is client


@pytest.mark.parametrize("reason", [OPENCLI_DAEMON_NOT_RUNNING, OPENCLI_BRIDGE_BUILD_MISMATCH])
def test_connect_restarts_missing_or_stale_daemon_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
) -> None:
    client = FakeDaemonClient([reason, None])
    _install_fake_client(monkeypatch, client)
    restart_calls: list[OpenCliRuntime] = []
    monkeypatch.setattr(daemon_process, "_restart_installed_daemon", restart_calls.append)
    runtime = _runtime(tmp_path)

    connected = daemon_process.connect_installed_opencli_daemon(runtime)

    assert connected is client
    assert restart_calls == [runtime]
    assert len(client.verify_calls) == 2


def test_connect_does_not_restart_unknown_status_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeDaemonClient([OPENCLI_STATUS_UNAVAILABLE])
    _install_fake_client(monkeypatch, client)
    monkeypatch.setattr(
        daemon_process,
        "_restart_installed_daemon",
        lambda _runtime: pytest.fail("unknown failures must not trigger recovery"),
    )

    with pytest.raises(OpenCliBrowserError) as captured:
        daemon_process.connect_installed_opencli_daemon(_runtime(tmp_path))

    assert captured.value.safe_reason_code == OPENCLI_STATUS_UNAVAILABLE


def test_restart_uses_installed_runtime_with_sanitized_bounded_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    home = tmp_path / "home"
    legacy_paths = (
        home / ".opencli" / "sentinel",
        home / ".seektalent" / "opencli-runtime" / "sentinel",
        home / ".seektalent" / "chrome-extension" / "opencli" / "sentinel",
    )
    for sentinel in legacy_paths:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("legacy-untouched", encoding="utf-8")
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(argv: object, **kwargs: object) -> Completed:
        captured["argv"] = argv
        captured.update(kwargs)
        return Completed()

    monkeypatch.setenv("SEEKTALENT_DOMI_JWT", "secret")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENCLI_CONFIG_DIR", str(home / ".opencli"))
    monkeypatch.setenv("OPENCLI_DAEMON_PORT", "19825")
    monkeypatch.setattr(daemon_process.subprocess, "run", fake_run)

    daemon_process._restart_installed_daemon(runtime)

    assert captured["argv"] == (
        str(runtime.node),
        str(runtime.opencli_main),
        "daemon",
        "restart",
    )
    assert captured["timeout"] == daemon_process.OPENCLI_DAEMON_RESTART_TIMEOUT_SECONDS
    assert "SEEKTALENT_DOMI_JWT" not in captured["env"]
    assert not any(str(name).startswith("OPENCLI_") for name in captured["env"])
    assert all(path.read_text(encoding="utf-8") == "legacy-untouched" for path in legacy_paths)


def test_restart_failure_is_source_safe_daemon_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Completed:
        returncode = 1

    monkeypatch.setattr(daemon_process.subprocess, "run", lambda *_args, **_kwargs: Completed())

    with pytest.raises(OpenCliBrowserError) as captured:
        daemon_process._restart_installed_daemon(_runtime(tmp_path))

    assert captured.value.safe_reason_code == OPENCLI_DAEMON_NOT_RUNNING
