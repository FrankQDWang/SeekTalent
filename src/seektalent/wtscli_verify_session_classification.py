"""Closed readiness classification for the production-unreachable WTSCLI probe."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias
from urllib.parse import unquote, urlparse

from seektalent.browser_bridge_manifest import BrowserBridgeRequirement
from seektalent.opencli_browser.daemon_transport import bridge_status_failure
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_BRIDGE_CAPABILITY_MISSING,
    OPENCLI_BRIDGE_INTEGRITY_FAILED,
    OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
    OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_DAEMON_STALE,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_STATUS_UNAVAILABLE,
)
from seektalent.providers.liepin.liepin_opencli_policy import (
    LIEPIN_RECRUITER_SEARCH_SURFACE_PATHS,
    liepin_reason_from_opencli_reason,
)
from seektalent.providers.liepin.liepin_site_parsing import classify_liepin_state
from seektalent.source_port.authenticated_verify_session_frames import VerifySessionFailureV1
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1, VerifySessionResultV1


_ALLOWED_RESULT_REASONS = frozenset(
    {
        "liepin_host_tab_missing",
        "liepin_host_window_ambiguous",
        "liepin_opencli_bootstrap_failed",
        "liepin_opencli_bridge_build_mismatch",
        "liepin_opencli_bridge_capability_missing",
        "liepin_opencli_bridge_integrity_failed",
        "liepin_opencli_bridge_protocol_mismatch",
        "liepin_opencli_bridge_wrong_implementation",
        "liepin_opencli_command_missing",
        "liepin_opencli_daemon_not_running",
        "liepin_opencli_daemon_stale",
        "liepin_opencli_extension_disconnected",
        "liepin_opencli_forbidden_command",
        "liepin_opencli_host_blocked",
        "liepin_opencli_identity_intercept",
        "liepin_opencli_login_required",
        "liepin_opencli_malformed_state",
        "liepin_opencli_risk_page",
        "liepin_opencli_search_not_ready",
        "liepin_opencli_selector_ambiguous",
        "liepin_opencli_selector_not_found",
        "liepin_opencli_stale_control_fence",
        "liepin_opencli_stale_ref",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_tab_response_malformed",
        "liepin_opencli_target_not_found",
        "liepin_opencli_terminal_state",
        "liepin_opencli_timeout",
        "liepin_opencli_unknown_modal",
        "liepin_opencli_window_policy_blocked",
        "liepin_owned_tab_missing",
    }
)
_BRIDGE_REASONS = frozenset(
    {
        OPENCLI_BRIDGE_BUILD_MISMATCH,
        OPENCLI_BRIDGE_CAPABILITY_MISSING,
        OPENCLI_BRIDGE_INTEGRITY_FAILED,
        OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
        OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    }
)
_USER_ACTIONS = {
    "liepin_host_tab_missing": "verify_session.open_liepin_host",
    "liepin_opencli_identity_intercept": "verify_session.complete_identity_check",
    "liepin_opencli_login_required": "verify_session.log_in",
    "liepin_opencli_risk_page": "verify_session.complete_risk_check",
    "liepin_opencli_unknown_modal": "verify_session.dismiss_or_resolve_modal",
}
_TRANSIENT_PAGE_MARKERS = (
    "ant-skeleton",
    "页面加载中",
    "正在加载",
    "skeleton",
)

ComponentReadiness: TypeAlias = Literal["ready", "not_ready", "not_observed"]
AccountReadiness: TypeAlias = Literal[
    "ready",
    "not_ready",
    "not_observed",
    "missing",
    "login_required",
    "revoked",
]
RiskState: TypeAlias = Literal["clear", "risk_page", "not_observed"]
SiteStateDisposition: TypeAlias = Literal["ready", "retry", "terminal"]
VerifySessionEffectReply: TypeAlias = VerifySessionResultV1 | VerifySessionFailureV1


@dataclass(frozen=True, slots=True)
class WtsCliCurrentProfileSnapshot:
    """Current sidecar-owned binding facts, never reconstructed from a request."""

    runtime_attempt_fence_ref: str
    profile_binding_ref: str
    profile_binding_generation: int
    provider_account_ref: str | None
    provider_account_subject: str
    browser_control_scope_id: str


@dataclass(slots=True)
class WtsCliReadinessProbe:
    binding: WtsCliCurrentProfileSnapshot
    process: ComponentReadiness = "not_observed"
    bridge: ComponentReadiness = "not_observed"
    extension: ComponentReadiness = "not_observed"
    profile_lock: ComponentReadiness = "not_observed"
    account: AccountReadiness = "not_observed"
    search_surface: ComponentReadiness = "not_observed"
    risk_state: RiskState = "not_observed"
    safe_reason: str | None = None
    control_fence: int | None = None
    owned_page: str | None = None
    owned_session: str | None = None
    wtscli_called: bool = False
    cleanup_failed: bool = False


def apply_bridge_status(
    probe: WtsCliReadinessProbe,
    status: Mapping[str, object],
    requirement: BrowserBridgeRequirement,
) -> bool:
    pid = status.get("pid")
    daemon_version = status.get("daemonVersion")
    if (
        status.get("ok") is not True
        or type(pid) is not int
        or pid < 1
        or not isinstance(daemon_version, str)
        or not daemon_version.strip()
    ):
        probe.process = "not_ready"
        probe.safe_reason = safe_liepin_reason(OPENCLI_STATUS_UNAVAILABLE)
        return False

    probe.process = "ready"
    failure = bridge_status_failure(status, requirement)
    if failure is None:
        probe.bridge = "ready"
        probe.extension = "ready"
        return True

    component, reason = failure
    if component == "process":
        probe.process = "not_ready"
    elif component == "bridge":
        probe.bridge = "not_ready"
    else:
        probe.bridge = "ready"
        probe.extension = "not_ready"
    probe.safe_reason = safe_liepin_reason(reason)
    return False


def apply_command_error(probe: WtsCliReadinessProbe, reason: str) -> None:
    safe_reason = safe_liepin_reason(reason)
    if not probe.wtscli_called:
        probe.safe_reason = safe_reason
        return
    if reason in {OPENCLI_DAEMON_NOT_RUNNING, OPENCLI_DAEMON_STALE}:
        probe.process = "not_ready"
    elif reason in _BRIDGE_REASONS:
        probe.process = "ready"
        probe.bridge = "not_ready"
    elif reason == OPENCLI_EXTENSION_DISCONNECTED:
        probe.process = "ready"
        probe.bridge = "ready"
        probe.extension = "not_ready"
    probe.safe_reason = safe_reason


def apply_site_state(
    probe: WtsCliReadinessProbe,
    *,
    url: str,
    text: str,
) -> SiteStateDisposition:
    reason = classify_liepin_state(url=url, text=text)
    if reason == "liepin_opencli_login_required":
        probe.account = "login_required"
        probe.search_surface = "not_ready"
        probe.risk_state = "not_observed"
    elif reason == "liepin_opencli_risk_page":
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "risk_page"
    elif reason in {"liepin_opencli_identity_intercept", "liepin_opencli_host_blocked"}:
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "not_observed"
    elif reason == "liepin_opencli_unknown_modal":
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "clear"
    elif reason is not None:
        probe.account = "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "not_observed"
    elif not _is_search_surface(url):
        probe.account = "ready" if _has_account_evidence(text) else "not_ready"
        probe.search_surface = "not_ready"
        probe.risk_state = "clear" if probe.account == "ready" else "not_observed"
        reason = "liepin_opencli_search_not_ready"
    else:
        account_ready = _has_account_evidence(text)
        search_ready = _has_search_surface_evidence(text)
        probe.account = "ready" if account_ready else "not_ready"
        probe.search_surface = "ready" if search_ready else "not_ready"
        probe.risk_state = "clear" if account_ready and search_ready else "not_observed"
        if account_ready and search_ready:
            probe.safe_reason = None
            return "ready"
        probe.safe_reason = "liepin_opencli_search_not_ready"
        return "retry"

    probe.safe_reason = reason
    return "terminal"


def is_concrete_navigation_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def result_reply(
    request: VerifySessionRequestV1,
    probe: WtsCliReadinessProbe,
) -> VerifySessionResultV1:
    reason = _safe_result_reason(probe.safe_reason)
    ready = (
        probe.process == "ready"
        and probe.bridge == "ready"
        and probe.extension == "ready"
        and probe.profile_lock == "ready"
        and probe.account == "ready"
        and probe.search_surface == "ready"
        and probe.risk_state == "clear"
        and reason is None
    )
    user_action = None
    if reason in _USER_ACTIONS:
        user_action = {"code": reason, "instruction_key": _USER_ACTIONS[reason]}
    return VerifySessionResultV1.model_validate(
        {
            "contract_version": "seektalent.source.verify-session.result/v1",
            "identity": request.identity,
            "process_readiness": probe.process,
            "bridge_readiness": probe.bridge,
            "extension_readiness": probe.extension,
            "profile_lock_readiness": probe.profile_lock,
            "account_readiness": probe.account,
            "search_surface_readiness": probe.search_surface,
            "risk_state": probe.risk_state,
            "session_readiness": "ready" if ready else "not_ready",
            "actual_profile_binding_ref": probe.binding.profile_binding_ref,
            "actual_provider_account_ref": probe.binding.provider_account_ref,
            "actual_profile_binding_generation": probe.binding.profile_binding_generation,
            "safe_reason_code": None if ready else reason,
            "user_action": user_action,
            "component_receipt_refs": request.component_receipt_refs,
        },
        strict=True,
    )


def failure_reply(
    request: VerifySessionRequestV1,
    reason: Literal["exchange_deadline_expired", "sidecar_not_ready"],
) -> VerifySessionFailureV1:
    return VerifySessionFailureV1.model_validate(
        {
            "contract_version": "seektalent.source.verify-session.failure/v1",
            "identity": request.identity,
            "failure_fact": "no_effect_performed",
            "failure_reason": reason,
        },
        strict=True,
    )


def safe_liepin_reason(reason: object) -> str:
    mapped = liepin_reason_from_opencli_reason(reason if isinstance(reason, str) else "")
    return mapped if mapped in _ALLOWED_RESULT_REASONS else "liepin_opencli_status_unavailable"


def _safe_result_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return reason if reason in _ALLOWED_RESULT_REASONS else "liepin_opencli_status_unavailable"


def _has_account_evidence(text: str) -> bool:
    return "安全退出" in text or "退出登录" in text


def _has_search_surface_evidence(text: str) -> bool:
    lowered = text.casefold()
    if any(marker in lowered for marker in _TRANSIENT_PAGE_MARKERS):
        return False
    has_label = "包含全部关键词" in text or "找简历" in text
    has_control = "<input" in lowered and "role=combobox" in lowered
    return has_label and has_control


def _is_search_surface(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() in {"h.liepin.com", "c.liepin.com"}
        and unquote(parsed.path or "").rstrip("/") in LIEPIN_RECRUITER_SEARCH_SURFACE_PATHS
        and parsed.username is None
        and parsed.password is None
    )
