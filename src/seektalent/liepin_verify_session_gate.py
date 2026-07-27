"""Production composition root for the Liepin WTSCLI verify-session gate."""

from __future__ import annotations

import asyncio
import time
from hashlib import sha256

from seektalent.config import AppSettings
from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_process import connect_installed_opencli_daemon
from seektalent.opencli_browser.lifecycle import browser_control_key
from seektalent.opencli_launcher import BootstrapError, ensure_opencli_runtime, runtime_requirement
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.wtscli_connection_supervisor import (
    InstalledWtsCliConnectionSupervisor,
    WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS,
    WtsCliConnectionError,
)
from seektalent.wtscli_verify_session_adapter import (
    probe_wtscli_liepin_session,
)
_REASON_MESSAGES = {
    "liepin_host_tab_missing": "请在 Chrome 中打开任意 h.liepin.com 页面后重试。",
    "liepin_host_window_ambiguous": "检测到多个可用的猎聘窗口，请只保留一个猎聘窗口后重试。",
    "liepin_opencli_login_required": "请在当前 Chrome 中登录猎聘后重试。",
    "liepin_opencli_identity_intercept": "猎聘要求完成身份验证，请在 Chrome 中处理后重试。",
    "liepin_opencli_risk_page": "猎聘触发了风控或验证页面，请在 Chrome 中完成验证后重试。",
    "liepin_opencli_unknown_modal": "猎聘页面存在未知阻断弹窗，请在 Chrome 中处理后重试。",
    "liepin_opencli_search_not_ready": "猎聘搜索页面尚未就绪，请等待页面加载完成后重试。",
    "liepin_opencli_bridge_build_mismatch": "WTSCLI runtime 与扩展版本不匹配，请重载扩展或重启 Chrome 后重试。",
    "liepin_opencli_bridge_protocol_mismatch": "WTSCLI runtime 与扩展协议不匹配，请重载扩展或重启 Chrome 后重试。",
    "liepin_opencli_bridge_capability_missing": "WTSCLI 扩展能力不完整，请重载扩展或重启 Chrome 后重试。",
    "liepin_opencli_bridge_wrong_implementation": "当前浏览器桥接不是 SeekTalent WTSCLI，请启用随应用安装的 WTSCLI 扩展后重试。",
    "liepin_opencli_bridge_integrity_failed": "WTSCLI 安装完整性校验失败，请重新安装 SeekTalent 后重试。",
    "liepin_opencli_extension_disconnected": "WTSCLI 扩展未连接，请确认扩展已启用并重载后重试。",
    "liepin_opencli_daemon_not_running": "WTSCLI 后台服务未就绪，请重新启动 SeekTalent 后重试。",
    "liepin_opencli_daemon_stale": "WTSCLI 后台服务版本已过期，请重新启动 SeekTalent 后重试。",
    "liepin_opencli_bootstrap_failed": "WTSCLI runtime 未安装完整，请重新安装或重新启动 SeekTalent 后重试。",
    "liepin_opencli_status_unavailable": "WTSCLI 状态暂不可用，请确认扩展已启用并重新启动 SeekTalent 后重试。",
    "liepin_opencli_timeout": "猎聘会话校验超时，请等待页面加载完成后重试。",
    "liepin_opencli_stale_control_fence": "猎聘浏览器控制权已失效，请重新启动本次检索。",
    "liepin_opencli_stale_ref": "猎聘浏览器 profile 或运行凭据已失效，请重新启动本次检索。",
    "liepin_opencli_tab_response_malformed": "WTSCLI 未能建立安全的猎聘校验标签页，请重载扩展后重试。",
    "liepin_owned_tab_missing": "猎聘校验标签页已失效，请重新启动本次检索。",
}


class ProductionLiepinVerifySessionGate:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    async def verify(
        self,
        *,
        runtime_run_id: str,
        source_lane_run_id: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                _verify_session,
                self._settings,
                runtime_run_id,
                source_lane_run_id,
            )
        except LiepinWorkerModeError:
            raise
        except OpenCliBrowserError as exc:
            _raise_reason(_normalized_boundary_reason(exc.safe_reason_code))
        except WtsCliConnectionError as exc:
            _raise_reason(_normalized_boundary_reason(exc.safe_reason_code))
        except BootstrapError as exc:
            _raise_reason(_bootstrap_reason(exc))


def create_production_liepin_verify_session_gate(
    settings: AppSettings,
) -> ProductionLiepinVerifySessionGate:
    return ProductionLiepinVerifySessionGate(settings)


def _verify_session(
    settings: AppSettings,
    _runtime_run_id: str,
    _source_lane_run_id: str,
) -> None:
    started_at = time.monotonic()
    runtime = ensure_opencli_runtime()
    requirement = runtime_requirement(runtime)
    timeout_seconds = min(900.0, max(0.001, settings.liepin_opencli_timeout_seconds))
    deadline_at = started_at + timeout_seconds
    supervisor = InstalledWtsCliConnectionSupervisor(runtime)
    supervisor.await_ready(
        timeout_seconds=min(
            WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS,
            timeout_seconds,
        ),
    )
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        raise WtsCliConnectionError("wtscli_readiness_deadline_exceeded")
    profile_digest = sha256(
        (
            f"{requirement.bridge_build_id}\0"
            f"{requirement.runtime_identity.state.root_dir}\0existing_profile"
        ).encode()
    ).hexdigest()
    control_key = browser_control_key(
        source_kind="liepin",
        browser_profile_id=f"wtscli-profile:{profile_digest}",
        provider_account_hash="unbound",
    )
    daemon = connect_installed_opencli_daemon(
        runtime,
        verify_timeout_seconds=min(
            WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS,
            remaining,
        ),
    )
    try:
        reason = probe_wtscli_liepin_session(
            daemon=daemon,
            bridge_requirement=requirement,
            control_key=control_key,
            deadline_at=deadline_at,
            monotonic_clock=time.monotonic,
            poll_wait=time.sleep,
        )
    finally:
        daemon.close()
    if reason is not None:
        _raise_reason(_normalized_boundary_reason(reason))


def _raise_reason(reason: object) -> None:
    code = str(reason or "liepin_opencli_status_unavailable")
    message = _REASON_MESSAGES.get(
        code,
        "猎聘会话校验失败，请确认 WTSCLI 扩展和猎聘页面可用后重试。",
    )
    raise LiepinWorkerModeError(message, code=code)


def _normalized_boundary_reason(reason: str) -> str:
    code = reason.removeprefix("verify_session_")
    if code.startswith("liepin_"):
        return code
    aliases = {
        "opencli_bridge_build_mismatch": "liepin_opencli_bridge_build_mismatch",
        "opencli_bridge_capability_missing": "liepin_opencli_bridge_capability_missing",
        "opencli_bridge_integrity_failed": "liepin_opencli_bridge_integrity_failed",
        "opencli_bridge_protocol_mismatch": "liepin_opencli_bridge_protocol_mismatch",
        "opencli_bridge_wrong_implementation": "liepin_opencli_bridge_wrong_implementation",
        "opencli_daemon_not_running": "liepin_opencli_daemon_not_running",
        "opencli_daemon_stale": "liepin_opencli_daemon_stale",
        "opencli_extension_disconnected": "liepin_opencli_extension_disconnected",
        "opencli_status_unavailable": "liepin_opencli_status_unavailable",
        "opencli_timeout": "liepin_opencli_timeout",
        "wtscli_readiness_deadline_exceeded": "liepin_opencli_timeout",
    }
    return aliases.get(code, "liepin_opencli_status_unavailable")


def _bootstrap_reason(error: BootstrapError) -> str:
    text = str(error)
    if "integrity" in text or "manifest" in text or "package" in text:
        return "liepin_opencli_bridge_integrity_failed"
    return "liepin_opencli_bootstrap_failed"


__all__ = [
    "ProductionLiepinVerifySessionGate",
    "create_production_liepin_verify_session_gate",
]
