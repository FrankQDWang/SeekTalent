"""Production composition root for the Liepin WTSCLI verify-session gate."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import secrets
import time
from collections import deque
from hashlib import sha256
from pathlib import Path
from typing import Literal

from seektalent.config import AppSettings
from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_process import connect_installed_opencli_daemon
from seektalent.opencli_browser.lifecycle import browser_control_key
from seektalent.opencli_launcher import BootstrapError, ensure_opencli_runtime, runtime_requirement
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.source_port import authenticated_history_frames as history_frames
from seektalent.source_port import authenticated_verify_session_frames as verify_frames
from seektalent.source_port.authenticated_source_port_session import PostHandshakeSourcePortSession
from seektalent.source_port.command_journal import create_command_journal, open_command_journal
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader
from seektalent.source_port.sidecar_transport import SourcePortEndpoint, _register_source_port_endpoint
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent.verify_session_closed_loop import (
    VerifySessionLiveAuthority,
    VerifySessionMainLoopError,
    accept_verify_session_operation,
    deliver_verify_session_outbox,
)
from seektalent.wtscli_connection_supervisor import (
    InstalledWtsCliConnectionSupervisor,
    WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS,
    WtsCliConnectionError,
    WtsCliConnectionReceipt,
)
from seektalent.wtscli_verify_session_classification import WtsCliCurrentProfileSnapshot
from seektalent.wtscli_verify_session_composition import create_wtscli_verify_session_composition


_VERIFY_CAPABILITIES = (
    "account",
    "bridge",
    "extension",
    "process",
    "profile_lock",
    "risk_state",
    "search_surface",
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
    "sidecar_not_ready": "猎聘会话校验 authority 已失效，请重新启动本次检索。",
    "exchange_deadline_expired": "猎聘会话校验超时，请确认页面可用后重试。",
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
        except VerifySessionMainLoopError as exc:
            _raise_reason(_normalized_boundary_reason(exc.reason_code))
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
    runtime_run_id: str,
    source_lane_run_id: str,
) -> None:
    runtime = ensure_opencli_runtime()
    requirement = runtime_requirement(runtime)
    deadline_milliseconds = min(
        900_000,
        max(1, round(settings.liepin_opencli_timeout_seconds * 1000)),
    )
    supervisor = InstalledWtsCliConnectionSupervisor(runtime)
    connection_receipt = supervisor.await_ready(
        timeout_seconds=min(
            WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS,
            deadline_milliseconds / 1000,
        ),
    )
    profile_digest = sha256(
        (
            f"{requirement.bridge_build_id}\0"
            f"{requirement.runtime_identity.state.root_dir}\0existing_profile"
        ).encode()
    ).hexdigest()
    profile_binding_ref = f"wtscli-profile:{profile_digest}"
    profile_binding_generation = int(
        connection_receipt.ownership_ref.removeprefix("sha256:")[:12],
        16,
    ) + 1
    unique = secrets.token_hex(12)
    operation_id = f"verify-{sha256(f'{source_lane_run_id}:{unique}'.encode()).hexdigest()[:40]}"
    idempotency_key = f"verify:{operation_id}"
    correlation_id = f"verify-{unique}"
    browser_control_scope_id = f"scope-{unique}"
    runtime_attempt_fence_token = secrets.token_urlsafe(32)
    source_operation_acceptance_ref = f"accept:{operation_id}"
    dispatch_intent_id = f"dispatch:{operation_id}"
    control_key = browser_control_key(
        source_kind="liepin",
        browser_profile_id=profile_binding_ref,
        provider_account_hash="wtscli-profile-binding",
    )
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    live_authority = VerifySessionLiveAuthority(
        runtime_attempt_fence_token=runtime_attempt_fence_token,
        profile_binding_ref=profile_binding_ref,
        provider_account_ref=None,
        required_capabilities=_VERIFY_CAPABILITIES,
        user_interaction_policy="observe_only",
        verify_search_surface=True,
    )
    provisional = accept_verify_session_operation(
        store=store,
        runtime_run_id=runtime_run_id,
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        runtime_attempt_fence_token=runtime_attempt_fence_token,
        profile_binding_generation=profile_binding_generation,
        browser_control_scope_id=browser_control_scope_id,
        deadline_milliseconds=deadline_milliseconds,
        dispatch_intent_id=dispatch_intent_id,
        source_operation_acceptance_ref=source_operation_acceptance_ref,
        live_authority=live_authority,
    )
    journal_path = _journal_path(settings)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal = (
        open_command_journal(journal_path)
        if journal_path.exists()
        else create_command_journal(journal_path)
    )
    with ExitStack() as resources:
        resources.callback(journal.close)
        daemon = connect_installed_opencli_daemon(runtime)
        resources.callback(daemon.close)
        snapshot = WtsCliCurrentProfileSnapshot(
            runtime_attempt_fence_ref=provisional.identity.runtime_attempt_fence_ref,
            profile_binding_ref=profile_binding_ref,
            profile_binding_generation=profile_binding_generation,
            provider_account_ref=None,
            browser_control_scope_id=browser_control_scope_id,
        )
        main, sidecar = _session_pair()
        composition = create_wtscli_verify_session_composition(
            command_journal_session=journal.start(),
            frame_session=sidecar,
            daemon=daemon,
            bridge_requirement=requirement,
            current_profile_snapshot=lambda: snapshot,
            control_key=control_key,
            monotonic_clock=time.monotonic,
            poll_wait=time.sleep,
        )
        resources.callback(composition.close)
        endpoint = _InProcessEndpoint(
            main=main,
            sidecar=sidecar,
            composition=composition,
            reader=SourceHistorySQLiteReader(journal_path),
        )
        now = _utc_now()
        result = deliver_verify_session_outbox(
            store=store,
            endpoint=endpoint,
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            live_authority=live_authority,
            delivery_mode="initial",
            correlation_id=correlation_id,
            deadline_milliseconds=deadline_milliseconds,
            acknowledged_at=now,
            committed_at=now,
            timeout=deadline_milliseconds / 1000,
            connection_supervisor=_ReadyConnectionSupervisor(connection_receipt),
        )
        terminal = result.exchange.terminal
        if isinstance(terminal, verify_frames.ReceivedVerifySessionResult):
            payload = terminal.payload
            if payload.session_readiness == "ready":
                return
            _raise_reason(_normalized_boundary_reason(str(payload.safe_reason_code or "")))
        payload = getattr(terminal, "payload", None)
        reason = getattr(payload, "failure_reason", None)
        _raise_reason(_normalized_boundary_reason(str(reason or "")))


class _InProcessEndpoint(SourcePortEndpoint):
    __slots__ = (
        "__weakref__",
        "_busy",
        "_composition",
        "_main",
        "_outbound",
        "_reader",
        "_sidecar",
        "_usable",
    )

    def __init__(self, *, main, sidecar, composition, reader) -> None:
        self._main = main
        self._sidecar = sidecar
        self._composition = composition
        self._reader = reader
        self._outbound: deque[bytes] = deque()
        self._busy = False
        self._usable = True
        _register_source_port_endpoint(self)

    def source_port_session(self):
        return self._main

    def _send_source_port_frame(self, frame: bytes, deadline: float) -> None:
        for message in self._sidecar.feed(frame):
            if isinstance(message, verify_frames.ReceivedVerifySessionSubmit):
                exchange = self._composition.handle_submit(message)
                self._outbound.extend(exchange.outbound_frames)
                if exchange.pending_effect is not None:
                    self._outbound.extend(exchange.pending_effect.consume().outbound_frames)
            elif isinstance(message, history_frames.ReceivedHistoryQuery):
                result = self._reader.query(message.payload, deadline=deadline)
                self._outbound.append(
                    self._sidecar.encode_history_result(
                        message_id=secrets.token_hex(16),
                        reply_to=message.message_id,
                        payload=result,
                    )
                )
            else:
                raise TypeError("source_port_unexpected_direction")

    def _receive_source_port_messages(self, deadline: float):
        if not self._outbound:
            if time.monotonic() >= deadline:
                raise TimeoutError("source_port_deadline_expired")
            return ()
        return self._main.feed(self._outbound.popleft())

    def _begin_source_port_exchange(self) -> Literal["acquired", "in_flight", "unusable"]:
        if not self._usable:
            return "unusable"
        if self._busy:
            return "in_flight"
        self._busy = True
        return "acquired"

    def _finish_source_port_exchange(self, *, succeeded: bool) -> None:
        self._busy = False
        if not succeeded:
            self._usable = False


class _ReadyConnectionSupervisor:
    def __init__(self, receipt: WtsCliConnectionReceipt) -> None:
        self._receipt = receipt

    def await_ready(self, *, timeout_seconds: float) -> WtsCliConnectionReceipt:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        return self._receipt


def _session_pair() -> tuple[PostHandshakeSourcePortSession, PostHandshakeSourcePortSession]:
    session_id = secrets.token_hex(16)
    main_key = secrets.token_bytes(32)
    sidecar_key = secrets.token_bytes(32)
    return (
        PostHandshakeSourcePortSession.for_main(
            session_id=session_id,
            protocol_minor=0,
            main_to_sidecar_key=main_key,
            sidecar_to_main_key=sidecar_key,
        ),
        PostHandshakeSourcePortSession.for_sidecar(
            session_id=session_id,
            protocol_minor=0,
            main_to_sidecar_key=main_key,
            sidecar_to_main_key=sidecar_key,
        ),
    )


def _journal_path(settings: AppSettings) -> Path:
    return settings.runtime_control_path.parent / "sidecar" / "command-journal.sqlite3"


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
        "sidecar_not_ready": "liepin_opencli_stale_ref",
        "exchange_deadline_expired": "liepin_opencli_timeout",
    }
    return aliases.get(code, "liepin_opencli_status_unavailable")


def _bootstrap_reason(error: BootstrapError) -> str:
    text = str(error)
    if "integrity" in text or "manifest" in text or "package" in text:
        return "liepin_opencli_bridge_integrity_failed"
    return "liepin_opencli_bootstrap_failed"


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "ProductionLiepinVerifySessionGate",
    "create_production_liepin_verify_session_gate",
]
