"""Actionable, non-restarting checks for the installed WTSCLI browser bridge."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import urlparse

from seektalent.browser_bridge_manifest import (
    BrowserBridgeExtensionFile,
    BrowserBridgeManifestError,
    BrowserBridgeRequirement,
    load_browser_bridge_requirement,
    load_runtime_package_identity,
)
from seektalent.browser_bridge_runtime_receipt import (
    verify_installed_runtime_package,
)
from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
)
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonClient
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_BRIDGE_CAPABILITY_MISSING,
    OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
    OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_FOREIGN_OWNER,
)
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


@dataclass(frozen=True, slots=True)
class BrowserBridgeEnvironmentStatus:
    ok: bool
    liepin_enabled: bool
    reason_code: str
    message: str
    action: str
    extension_dir: Path
    bridge_build_id: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "liepinEnabled": self.liepin_enabled,
            "reasonCode": self.reason_code,
            "message": self.message,
            "action": self.action,
            "extensionDir": str(self.extension_dir),
            "bridgeBuildId": self.bridge_build_id,
        }


class _BridgeClient(Protocol):
    def inspect_bridge_status(
        self,
        *,
        timeout_seconds: float,
    ) -> tuple[Mapping[str, object], tuple[str, str] | None]: ...

    def close(self) -> None: ...


ClientFactory = Callable[[BrowserBridgeRequirement], _BridgeClient]
HostTabProbe = Callable[[_BridgeClient], bool]


def check_browser_bridge_environment(
    *,
    install_root: Path,
    node: Path,
    client_factory: ClientFactory | None = None,
    host_tab_probe: HostTabProbe | None = None,
    timeout_seconds: float = 2.0,
) -> BrowserBridgeEnvironmentStatus:
    """Classify the first causal WTSCLI/Liepin readiness failure."""
    del node  # The check verifies installed bytes and never starts a runtime process.
    root = install_root.expanduser().absolute()
    extension_dir = root / "chrome-extension" / "wtscli"
    manifest_path = root / "browser-bridge" / "bridge-manifest.json"
    runtime_root = root / "wtscli-runtime"
    if (
        not manifest_path.is_file()
        or not extension_dir.is_dir()
        or not runtime_root.is_dir()
    ):
        return _failure(
            "wtscli_bundle_missing",
            "SeekTalent 内置的 WTSCLI runtime、扩展或配对清单缺失。",
            "请重新运行当前 SeekTalent 安装包中的安装脚本；不要单独下载扩展。",
            extension_dir,
        )
    try:
        requirement = load_browser_bridge_requirement(manifest_path)
        runtime_dir = (
            runtime_root
            / requirement.runtime_identity.package.name
            / requirement.cli.version
        )
        _verify_runtime_identity(runtime_dir, requirement)
        _verify_installed_files(
            runtime_dir=runtime_dir,
            extension_dir=extension_dir,
            requirement=requirement,
        )
    except _IdentityMismatch:
        return _failure(
            "wtscli_identity_mismatch",
            "已安装的 WTSCLI runtime 与扩展清单不是同一个 exact build。",
            "请重新运行当前 SeekTalent 安装包中的安装脚本，成对更新 runtime 与扩展。",
            extension_dir,
        )
    except (BrowserBridgeManifestError, OSError, StrictJsonError, ValueError):
        return _failure(
            "wtscli_bundle_corrupt",
            "SeekTalent 内置的 WTSCLI runtime 或扩展文件已损坏。",
            "请重新运行当前 SeekTalent 安装包中的安装脚本；旧完整版本不会被安装失败覆盖。",
            extension_dir,
        )

    factory = client_factory or (lambda expected: OpenCliDaemonClient(requirement=expected))
    client = factory(requirement)
    try:
        try:
            status, failure = client.inspect_bridge_status(
                timeout_seconds=timeout_seconds,
            )
        except OpenCliBrowserError as exc:
            return _client_error(exc.safe_reason_code, extension_dir)
        if failure is not None:
            component, reason = failure
            return _status_failure(
                component=component,
                reason=reason,
                extension_dir=extension_dir,
            )
        try:
            if host_tab_probe is not None:
                has_host = host_tab_probe(client)
            else:
                if not isinstance(client, OpenCliDaemonClient):
                    raise TypeError("the default host probe requires OpenCliDaemonClient")
                has_host = _has_liepin_host_tab(client)
        except OpenCliBrowserError as exc:
            return _client_error(exc.safe_reason_code, extension_dir)
        if not has_host:
            return _failure(
                "liepin_login_or_host_tab_missing",
                "WTSCLI 已就绪，但没有可用的已登录猎聘 host tab。",
                "请在 Chrome 中登录猎聘并保留一个 h.liepin.com 页面，然后重新运行环境检查。",
                extension_dir,
                bridge_build_id=requirement.bridge_build_id,
            )
        daemon_build = status.get("bridgeBuildId")
        extension_build = status.get("extensionBridgeBuildId")
        if (
            daemon_build != requirement.bridge_build_id
            or extension_build != requirement.bridge_build_id
        ):
            return _failure(
                "wtscli_identity_mismatch",
                "WTSCLI runtime 与 Chrome 扩展的 exact build identity 不一致。",
                "请在 chrome://extensions 中重新加载 WTSCLI；若仍失败，请重启 Chrome 后重新运行环境检查。",
                extension_dir,
            )
        return BrowserBridgeEnvironmentStatus(
            ok=True,
            liepin_enabled=True,
            reason_code="wtscli_ready",
            message="WTSCLI runtime、Chrome 扩展和猎聘 host tab 已就绪。",
            action="无需操作。",
            extension_dir=extension_dir,
            bridge_build_id=requirement.bridge_build_id,
        )
    finally:
        client.close()


class _IdentityMismatch(RuntimeError):
    pass


def _verify_runtime_identity(
    runtime_dir: Path,
    requirement: BrowserBridgeRequirement,
) -> None:
    package_dir = (
        runtime_dir
        / "node_modules"
        / requirement.runtime_identity.package.name
    )
    identity_path = package_dir / "bridge-identity.json"
    raw_identity = strict_json_object_loads(identity_path.read_bytes())
    raw_build_id = raw_identity.get("bridgeBuildId")
    if isinstance(raw_build_id, str) and raw_build_id != requirement.bridge_build_id:
        raise _IdentityMismatch
    identity = load_runtime_package_identity(identity_path)
    if (
        identity.implementation != requirement.implementation
        or identity.bridge_build_id != requirement.bridge_build_id
        or identity.runtime_identity != requirement.runtime_identity
        or identity.protocol_version != requirement.protocol_version
        or identity.capabilities != requirement.capabilities
    ):
        raise _IdentityMismatch


def _verify_installed_files(
    *,
    runtime_dir: Path,
    extension_dir: Path,
    requirement: BrowserBridgeRequirement,
) -> None:
    package_dir = (
        runtime_dir
        / "node_modules"
        / requirement.runtime_identity.package.name
    )
    package_json = strict_json_object_loads((package_dir / "package.json").read_bytes())
    bin_mapping = package_json.get("bin")
    if (
        package_json.get("name") != requirement.cli.package
        or package_json.get("version") != requirement.cli.version
        or type(bin_mapping) is not dict
        or set(bin_mapping) != {requirement.cli.entrypoint}
    ):
        raise BrowserBridgeManifestError("integrity_failed")
    raw_entrypoint = next(iter(bin_mapping.values()))
    if type(raw_entrypoint) is not str:
        raise BrowserBridgeManifestError("integrity_failed")
    relative_entrypoint = PurePosixPath(raw_entrypoint)
    if (
        relative_entrypoint.is_absolute()
        or not relative_entrypoint.parts
        or any(part in {"", ".", ".."} for part in relative_entrypoint.parts)
    ):
        raise BrowserBridgeManifestError("integrity_failed")
    entrypoint = package_dir.joinpath(*relative_entrypoint.parts)
    if entrypoint.is_symlink() or not entrypoint.is_file():
        raise BrowserBridgeManifestError("integrity_failed")
    verify_installed_runtime_package(runtime_dir, requirement=requirement)
    if _extension_files(extension_dir) != requirement.extension.files:
        raise BrowserBridgeManifestError("integrity_failed")


def _extension_files(extension_dir: Path) -> tuple[BrowserBridgeExtensionFile, ...]:
    files: list[BrowserBridgeExtensionFile] = []
    for candidate in sorted(extension_dir.rglob("*")):
        if candidate.is_symlink():
            raise BrowserBridgeManifestError("integrity_failed")
        if not candidate.is_file():
            continue
        files.append(
            BrowserBridgeExtensionFile(
                path=candidate.relative_to(extension_dir).as_posix(),
                size=candidate.stat().st_size,
                sha256=_sha256(candidate),
            )
        )
    return tuple(files)


def _has_liepin_host_tab(client: OpenCliDaemonClient) -> bool:
    automation = OpenCliBrowserAutomation(
        config=OpenCliBrowserConfig(
            command=(),
            session="seektalent-environment-check",
            timeout_seconds=2,
            pacing_enabled=False,
        ),
        daemon=client,
    )
    for tab in automation.find_host_tabs("https://h.liepin.com/"):
        parsed = urlparse(tab.url)
        if parsed.scheme == "https" and parsed.hostname == "h.liepin.com":
            return True
    return False


def _client_error(
    reason: str,
    extension_dir: Path,
) -> BrowserBridgeEnvironmentStatus:
    if reason == OPENCLI_DAEMON_NOT_RUNNING:
        return _failure(
            "wtscli_daemon_missing",
            "未检测到 SeekTalent 自有的 WTSCLI 服务。",
            "请启动 Chrome，确认已加载 WTSCLI 扩展，再启动 WTSCLI 服务并重新运行环境检查。",
            extension_dir,
        )
    if reason == OPENCLI_FOREIGN_OWNER:
        return _failure(
            "wtscli_daemon_wrong_owner",
            "19826 端口或 WTSCLI ownership 不属于当前 SeekTalent exact bundle。",
            "请关闭错误的 WTSCLI 实例后重新运行环境检查；不要停止 legacy OpenCLI。",
            extension_dir,
        )
    return _failure(
        "wtscli_daemon_stale",
        "WTSCLI 服务未返回当前 exact bundle 的有效状态。",
        "请重新启动当前 SeekTalent 包内的 WTSCLI 服务，然后重新运行环境检查。",
        extension_dir,
    )


def _status_failure(
    *,
    component: str,
    reason: str,
    extension_dir: Path,
) -> BrowserBridgeEnvironmentStatus:
    if component == "extension" and reason == OPENCLI_EXTENSION_DISCONNECTED:
        return _failure(
            "wtscli_extension_not_loaded_or_disabled",
            "Chrome 中的 WTSCLI 扩展尚未加载、已禁用或未连接。",
            f"请打开 chrome://extensions，启用开发者模式并加载：{extension_dir}",
            extension_dir,
        )
    identity_reasons = {
        OPENCLI_BRIDGE_BUILD_MISMATCH,
        OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
        OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
        OPENCLI_BRIDGE_CAPABILITY_MISSING,
    }
    if component == "extension" and reason in identity_reasons:
        return _failure(
            "wtscli_extension_stale_worker",
            "Chrome 仍在运行旧的 WTSCLI extension worker/build。",
            "请在 chrome://extensions 中点击 WTSCLI 的“重新加载”；若仍失败，请完全退出并重启 Chrome，然后重新运行环境检查。",
            extension_dir,
        )
    if component == "bridge" and reason in identity_reasons:
        return _failure(
            "wtscli_daemon_stale",
            "正在运行的 WTSCLI 服务不是当前 SeekTalent 包内的 exact build。",
            "请只重启当前 SeekTalent 自有的 WTSCLI 服务，然后重新运行环境检查；不要停止 legacy OpenCLI。",
            extension_dir,
        )
    return _failure(
        "wtscli_daemon_stale",
        "WTSCLI 服务状态无效或已过期。",
        "请重新启动当前 SeekTalent 自有的 WTSCLI 服务，然后重新运行环境检查。",
        extension_dir,
    )


def _failure(
    reason_code: str,
    message: str,
    action: str,
    extension_dir: Path,
    *,
    bridge_build_id: str | None = None,
) -> BrowserBridgeEnvironmentStatus:
    return BrowserBridgeEnvironmentStatus(
        ok=False,
        liepin_enabled=False,
        reason_code=reason_code,
        message=message,
        action=action,
        extension_dir=extension_dir,
        bridge_build_id=bridge_build_id,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "BrowserBridgeEnvironmentStatus",
    "check_browser_bridge_environment",
]
