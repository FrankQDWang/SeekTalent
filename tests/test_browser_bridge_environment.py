from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from seektalent.providers.liepin.browser_environment import (
    BrowserBridgeEnvironmentStatus,
    check_browser_bridge_environment,
)
from seektalent.browser_bridge_install import install_browser_bridge_bundle
from seektalent.cli import main
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
)
from tests.browser_bridge_bundle_fixtures import (
    WTSCLI_BUILD_ID,
    write_browser_bridge_bundle,
)


class _Client:
    def __init__(
        self,
        *,
        status: dict[str, object] | None = None,
        failure: tuple[str, str] | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status or {}
        self.failure = failure
        self.error = error
        self.closed = False

    def inspect_bridge_status(
        self,
        *,
        timeout_seconds: float,
    ) -> tuple[dict[str, object], tuple[str, str] | None]:
        assert timeout_seconds > 0
        if self.error is not None:
            from seektalent.opencli_browser.contracts import OpenCliBrowserError

            raise OpenCliBrowserError(self.error)
        return self.status, self.failure

    def close(self) -> None:
        self.closed = True


def _installed_pair(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle)
    install_root = tmp_path / "home" / ".seektalent"
    install_browser_bridge_bundle(
        bundle_dir=bundle,
        install_root=install_root,
        node=Path(sys.executable),
    )
    return install_root, bundle


def _check(
    install_root: Path,
    client: _Client,
    *,
    host_tabs: bool = True,
) -> BrowserBridgeEnvironmentStatus:
    return check_browser_bridge_environment(
        install_root=install_root,
        node=Path(sys.executable),
        client_factory=lambda _requirement: client,
        host_tab_probe=lambda _client: host_tabs,
    )


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("missing", "wtscli_bundle_missing"),
        ("corrupt_extension", "wtscli_bundle_corrupt"),
        ("wrong_pair", "wtscli_identity_mismatch"),
    ],
)
def test_environment_check_classifies_installed_pair_failures(
    tmp_path: Path,
    mutation: str,
    reason_code: str,
) -> None:
    install_root, _bundle = _installed_pair(tmp_path)
    if mutation == "missing":
        extension = install_root / "chrome-extension" / "wtscli"
        for path in sorted(extension.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        extension.rmdir()
    elif mutation == "corrupt_extension":
        (install_root / "chrome-extension" / "wtscli" / "dist" / "background.js").write_text(
            "corrupt",
            encoding="utf-8",
        )
    else:
        identity_path = (
            install_root
            / "wtscli-runtime"
            / "wtscli"
            / "0.1.0"
            / "node_modules"
            / "wtscli"
            / "bridge-identity.json"
        )
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity["bridgeBuildId"] = "seektalent-wtscli-0.1.0+wrong-pair"
        identity_path.write_text(json.dumps(identity), encoding="utf-8")

    result = _check(install_root, _Client())

    assert result.ok is False
    assert result.liepin_enabled is False
    assert result.reason_code == reason_code
    assert "重新运行当前 SeekTalent 安装包" in result.action


@pytest.mark.parametrize(
    ("client", "reason_code", "action_text"),
    [
        (
            _Client(error="opencli_daemon_not_running"),
            "wtscli_daemon_missing",
            "WTSCLI 服务",
        ),
        (
            _Client(error="opencli_foreign_owner"),
            "wtscli_daemon_wrong_owner",
            "不要停止 legacy OpenCLI",
        ),
        (
            _Client(
                failure=("extension", "opencli_extension_disconnected"),
            ),
            "wtscli_extension_not_loaded_or_disabled",
            "chrome://extensions",
        ),
        (
            _Client(
                failure=("extension", OPENCLI_BRIDGE_BUILD_MISMATCH),
            ),
            "wtscli_extension_stale_worker",
            "重新加载",
        ),
        (
            _Client(
                failure=("bridge", OPENCLI_BRIDGE_BUILD_MISMATCH),
            ),
            "wtscli_daemon_stale",
            "重新运行环境检查",
        ),
    ],
)
def test_environment_check_preserves_causal_daemon_and_extension_classification(
    tmp_path: Path,
    client: _Client,
    reason_code: str,
    action_text: str,
) -> None:
    install_root, _bundle = _installed_pair(tmp_path)

    result = _check(install_root, client)

    assert result.reason_code == reason_code
    assert result.liepin_enabled is False
    assert action_text in result.action
    assert client.closed is True


def test_environment_check_requires_a_usable_logged_in_liepin_host_tab(
    tmp_path: Path,
) -> None:
    install_root, _bundle = _installed_pair(tmp_path)

    result = _check(
        install_root,
        _Client(
            status={
                "bridgeBuildId": WTSCLI_BUILD_ID,
                "extensionBridgeBuildId": WTSCLI_BUILD_ID,
            }
        ),
        host_tabs=False,
    )

    assert result.reason_code == "liepin_login_or_host_tab_missing"
    assert "登录猎聘" in result.action
    assert result.liepin_enabled is False


def test_environment_check_ready_receipt_contains_only_safe_pair_identity(
    tmp_path: Path,
) -> None:
    install_root, _bundle = _installed_pair(tmp_path)

    result = _check(
        install_root,
        _Client(
            status={
                "bridgeBuildId": WTSCLI_BUILD_ID,
                "extensionBridgeBuildId": WTSCLI_BUILD_ID,
            }
        ),
    )

    assert result.ok is True
    assert result.liepin_enabled is True
    assert result.reason_code == "wtscli_ready"
    assert result.bridge_build_id == WTSCLI_BUILD_ID
    assert result.extension_dir == install_root / "chrome-extension" / "wtscli"


def test_browser_check_cli_emits_stable_json_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    node = tmp_path / "node"
    node.write_bytes(b"node")
    node.chmod(0o755)
    extension_dir = tmp_path / ".seektalent" / "chrome-extension" / "wtscli"
    monkeypatch.setattr("seektalent.domi_bootstrap.resolve_domi_node", lambda **_kwargs: node)
    monkeypatch.setattr(
        "seektalent.providers.liepin.browser_environment.check_browser_bridge_environment",
        lambda **_kwargs: BrowserBridgeEnvironmentStatus(
            ok=False,
            liepin_enabled=False,
            reason_code="wtscli_extension_stale_worker",
            message="旧 worker",
            action="重新加载扩展",
            extension_dir=extension_dir,
        ),
    )

    assert main(["browser-check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["reasonCode"] == "wtscli_extension_stale_worker"
    assert payload["liepinEnabled"] is False
    assert payload["action"] == "重新加载扩展"
