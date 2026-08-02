"""Production composition root for the Liepin WTSCLI verify-session gate."""

from __future__ import annotations

import asyncio
import time

from seektalent.config import AppSettings
from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_process import (
    connect_existing_opencli_daemon_read_only,
)
from seektalent.wtscli_runtime import (
    BootstrapError,
    inspect_wtscli_runtime,
    runtime_requirement,
)
from seektalent.providers.liepin.client import LiepinWorkerModeError
from seektalent.providers.liepin.browser_environment import (
    BrowserBridgeEnvironmentStatus,
    check_browser_bridge_environment,
    check_installed_browser_bridge_bundle,
)
from seektalent.failure_interpretation import public_source_problem_message
from seektalent.wtscli_verify_session_classification import (
    WtsCliReadinessProbe,
    apply_bridge_status,
    safe_liepin_reason,
)
from seektalent.wtscli_verify_session_adapter import (
    WtsCliCurrentProfileSnapshot,
    run_wtscli_verify_session_effect,
)
from seektalent.source_port.authenticated_verify_session_frames import (
    VerifySessionFailureV1,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestV1,
    VerifySessionResultV1,
)
from seektalent.providers.liepin.liepin_opencli_policy import (
    LIEPIN_BROWSER_CONTROL_KEY,
    LIEPIN_DETAIL_TAB_SESSION,
    LIEPIN_SEARCH_TAB_SESSION,
)
from seektalent.wtscli_lifecycle_supervisor import (
    WtsCliLifecycleError,
    WtsCliLifecycleSupervisor,
)


class ProductionLiepinVerifySessionGate:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    async def verify(self) -> None:
        try:
            await asyncio.to_thread(
                _observe_session,
                self._settings,
            )
        except LiepinWorkerModeError:
            raise
        except OpenCliBrowserError as exc:
            _raise_reason(_normalized_boundary_reason(exc.safe_reason_code))
        except BootstrapError as exc:
            _raise_reason(_bootstrap_reason(exc))

def create_production_liepin_verify_session_gate(
    settings: AppSettings,
) -> ProductionLiepinVerifySessionGate:
    return ProductionLiepinVerifySessionGate(settings)


def _observe_session(settings: AppSettings) -> None:
    started_at = time.monotonic()
    runtime = inspect_wtscli_runtime()
    environment_status = _check_environment(runtime)
    if not environment_status.ok:
        _raise_reason(_environment_reason(environment_status))
    requirement = runtime_requirement(runtime)
    timeout_seconds = min(
        900.0,
        max(0.001, settings.liepin_opencli_timeout_seconds),
    )
    deadline_at = started_at + timeout_seconds
    remaining = deadline_at - time.monotonic()
    if remaining <= 0:
        _raise_reason("liepin_opencli_timeout")
    daemon = connect_existing_opencli_daemon_read_only(
        runtime,
        verify_timeout_seconds=remaining,
    )
    try:
        probe = WtsCliReadinessProbe(binding=None)
        status = daemon.verify_bridge(
            timeout_seconds=max(0.001, deadline_at - time.monotonic()),
            validate=False,
        )
        if not apply_bridge_status(probe, status, requirement):
            _raise_reason(safe_liepin_reason(probe.safe_reason))
        status = daemon.verify_bridge(
            timeout_seconds=max(0.001, deadline_at - time.monotonic()),
        )
        if not apply_bridge_status(probe, status, requirement):
            _raise_reason(safe_liepin_reason(probe.safe_reason))
    finally:
        daemon.close()


def _orphaned_owned_tab_absent(
    settings: AppSettings,
    operation_kind: str,
) -> bool:
    session = {
        "cards": LIEPIN_SEARCH_TAB_SESSION,
        "details": LIEPIN_DETAIL_TAB_SESSION,
    }.get(operation_kind)
    if session is None:
        raise ValueError("orphaned_browser_operation_kind_invalid")
    runtime = inspect_wtscli_runtime()
    environment_status = _check_environment(runtime)
    if not environment_status.ok:
        _raise_reason(_environment_reason(environment_status))
    timeout_seconds = min(
        2.0,
        max(0.001, settings.liepin_opencli_timeout_seconds),
    )
    daemon = connect_existing_opencli_daemon_read_only(
        runtime,
        verify_timeout_seconds=timeout_seconds,
    )
    try:
        result = daemon.command(
            "tabs",
            {
                "op": "list",
                "session": session,
                "surface": "browser",
                "windowMode": settings.liepin_opencli_window_mode,
            },
            timeout_seconds=timeout_seconds,
        )
    finally:
        daemon.close()
    if not isinstance(result.data, list):
        raise OpenCliBrowserError("liepin_opencli_status_unavailable")
    return len(result.data) == 0


def _prepare_session_mutating(
    settings: AppSettings,
    *,
    request: VerifySessionRequestV1,
    current_profile_snapshot: WtsCliCurrentProfileSnapshot,
    lifecycle_supervisor: WtsCliLifecycleSupervisor | None,
    on_effect_started=None,
    on_effect_completed=None,
) -> None:
    """Prepare browser readiness through the already-running lifecycle owner."""
    if type(request) is not VerifySessionRequestV1:
        raise TypeError("strict verify-session request required")
    if type(current_profile_snapshot) is not WtsCliCurrentProfileSnapshot:
        raise TypeError("strict current-profile snapshot required")
    if lifecycle_supervisor is None:
        raise WtsCliLifecycleError("wtscli_supervisor_not_started")
    started_at = time.monotonic()
    runtime = lifecycle_supervisor.runtime
    bridge_manifest = runtime.bridge_manifest
    if bridge_manifest is None:
        raise BootstrapError("opencli_bridge_integrity_failed: Missing installed WTSCLI manifest")
    bundle_status = check_installed_browser_bridge_bundle(
        install_root=bridge_manifest.parent.parent,
    )
    if not bundle_status.ok:
        _raise_reason(_environment_reason(bundle_status))
    requirement = runtime_requirement(runtime)
    deadline_at = started_at + min(
        900.0,
        max(0.001, settings.liepin_opencli_timeout_seconds),
    )
    lifecycle_supervisor.ensure_ready(
        timeout_seconds=max(
            0.001,
            deadline_at - time.monotonic(),
        )
    )
    daemon = lifecycle_supervisor.connect_existing(
        verify_timeout_seconds=max(0.001, deadline_at - time.monotonic()),
    )
    try:
        environment_status = _check_environment(runtime)
        if not environment_status.ok:
            _raise_reason(_environment_reason(environment_status))
        effect = run_wtscli_verify_session_effect(
            daemon=daemon,
            bridge_requirement=requirement,
            current_profile_snapshot=lambda: (
                current_profile_snapshot
            ),
            control_key=LIEPIN_BROWSER_CONTROL_KEY,
            monotonic_clock=time.monotonic,
            poll_wait=time.sleep,
        )
        if on_effect_started is not None:
            on_effect_started()
        result = effect(request, deadline_at)
        if on_effect_completed is not None:
            on_effect_completed()
    finally:
        daemon.close()
    if isinstance(result, VerifySessionResultV1):
        if result.session_readiness == "ready":
            return
        _raise_reason(
            _normalized_boundary_reason(
                result.safe_reason_code
                or "liepin_opencli_status_unavailable"
            )
        )
    if isinstance(result, VerifySessionFailureV1):
        aliases = {
            "exchange_deadline_expired": "liepin_opencli_timeout",
            "sidecar_not_ready": "liepin_opencli_status_unavailable",
            "session_closed": "liepin_opencli_status_unavailable",
        }
        _raise_reason(aliases[result.failure_reason])
    _raise_reason("liepin_opencli_status_unavailable")


def _check_environment(runtime: object) -> BrowserBridgeEnvironmentStatus:
    bridge_manifest = getattr(runtime, "bridge_manifest", None)
    node = getattr(runtime, "node", None)
    if bridge_manifest is None or node is None:
        raise BootstrapError("opencli_bridge_integrity_failed: Missing installed WTSCLI paths")
    return check_browser_bridge_environment(
        install_root=bridge_manifest.parent.parent,
        node=node,
    )


def _environment_reason(status: BrowserBridgeEnvironmentStatus) -> str:
    if status.bridge_failure_reason is not None:
        return _normalized_boundary_reason(status.bridge_failure_reason)
    aliases = {
        "liepin_host_tab_missing": "liepin_host_tab_missing",
        "wtscli_bundle_missing": "liepin_wtscli_bundle_missing",
        "wtscli_bundle_corrupt": "liepin_opencli_bridge_integrity_failed",
        "wtscli_daemon_missing": "liepin_opencli_daemon_not_running",
        "wtscli_daemon_stale": "liepin_opencli_daemon_stale",
        "wtscli_daemon_wrong_owner": "liepin_opencli_daemon_stale",
        "wtscli_extension_not_loaded_or_disabled": "liepin_opencli_extension_disconnected",
        "wtscli_extension_stale_worker": "liepin_opencli_bridge_build_mismatch",
        "wtscli_identity_mismatch": "liepin_opencli_bridge_build_mismatch",
    }
    return aliases.get(
        status.reason_code,
        "liepin_opencli_status_unavailable",
    )


def _raise_reason(reason: object) -> None:
    code = str(reason or "liepin_opencli_status_unavailable")
    message = public_source_problem_message(
        code,
        source_label="猎聘",
    )
    raise LiepinWorkerModeError(
        message or "猎聘会话校验失败，请确认浏览器检索通道可用后重试。",
        code=code,
    )


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
        "wtscli_supervisor_not_started": "liepin_opencli_daemon_not_running",
        "wtscli_runtime_build_mismatch": "liepin_opencli_bridge_build_mismatch",
        "wtscli_foreign_owner": "liepin_opencli_daemon_stale",
        "wtscli_daemon_restart_budget_exhausted": "liepin_opencli_daemon_stale",
        "wtscli_readiness_timeout": "liepin_opencli_timeout",
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
