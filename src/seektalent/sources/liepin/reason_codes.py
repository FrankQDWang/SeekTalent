from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seektalent.source_contracts.liepin_reason_codes import (
    LIEPIN_WORKER_SAFE_REASON_CODES,
)
from seektalent.user_action import (
    USER_ACTION_INSTRUCTIONS,
    USER_ACTION_SCOPES,
    UserActionCode,
    UserActionV1,
)


SourceOperation = Literal[
    "verify_session",
    "search",
    "cards",
    "details",
    "continuation",
    "cleanup",
]
SourceOperationDisposition = Literal[
    "completed",
    "partial",
    "user_action_required",
    "incompatible",
    "failed",
    "cancelled",
    "reconciliation_unknown",
]


@dataclass(frozen=True, slots=True)
class PublicSourceProblem:
    code: str
    message_template: str | None
    user_facing: bool = True


@dataclass(frozen=True, slots=True)
class LiepinFailurePolicy:
    internal_reason: str
    public_problem_code: str
    source_lane_reason_code: str
    source_operation_disposition: SourceOperationDisposition
    user_action_code: UserActionCode | None = None


@dataclass(frozen=True, slots=True)
class FailureInterpretation:
    internal_reason: str
    operation: SourceOperation
    public_problem_code: str
    source_lane_reason_code: str
    source_operation_disposition: SourceOperationDisposition
    user_action: UserActionV1 | None


def _problem(
    code: str,
    message_template: str | None,
    *,
    user_facing: bool = True,
) -> PublicSourceProblem:
    return PublicSourceProblem(
        code=code,
        message_template=message_template,
        user_facing=user_facing,
    )


PUBLIC_SOURCE_PROBLEMS = {
    problem.code: problem
    for problem in (
        _problem("job_lease_expired", None, user_facing=False),
        _problem("relay_pending_worker", None, user_facing=False),
        _problem(
            "source_browser_host_required",
            "{source_label}需要先在 Chrome 中打开人才搜索页，然后再开始检索。",
        ),
        _problem(
            "source_login_required",
            "{source_label}账号需要登录后才能继续检索。",
        ),
        _problem(
            "source_identity_confirmation_required",
            "{source_label}需要先确认当前身份或企业，然后再继续检索。",
        ),
        _problem(
            "source_account_mismatch",
            "{source_label}账号与当前检索任务不匹配，请确认账号后重试。",
        ),
        _problem(
            "source_browser_timeout",
            "{source_label}页面响应超时，请稍后重试。",
        ),
        _problem(
            "source_browser_backend_unavailable",
            "{source_label}浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。",
        ),
        _problem(
            "source_browser_backend_incompatible",
            "{source_label}浏览器运行组件版本或能力不兼容，请更新应用并重新启用浏览器扩展后重试。",
        ),
        _problem(
            "source_browser_installation_invalid",
            "{source_label}浏览器运行组件安装不完整，请重新安装应用后重试。",
        ),
        _problem(
            "source_browser_reference_stale",
            "{source_label}页面引用已失效，请刷新人才搜索页后重试。",
        ),
        _problem(
            "source_browser_extension_disconnected",
            "{source_label}浏览器扩展未连接，请确认扩展已启用并连接后重试。",
        ),
        _problem(
            "source_browser_policy_blocked",
            "{source_label}浏览器操作不符合来源安全策略，本次检索已停止。",
        ),
        _problem(
            "source_risk_or_verification_required",
            "{source_label}需要完成页面验证后才能继续检索。",
        ),
        _problem(
            "source_browser_interaction_required",
            "{source_label}需要人工完成页面操作后才能继续检索。",
        ),
        _problem(
            "source_budget_exhausted",
            "{source_label}本轮检索额度已用尽。",
        ),
        _problem("source_filter_applied", None, user_facing=False),
        _problem(
            "source_filter_partial",
            "{source_label}仅应用了部分筛选条件，结果可能比预期更宽。",
        ),
        _problem(
            "source_filter_unavailable",
            "{source_label}筛选条件未成功应用，请刷新页面后重试。",
        ),
        _problem(
            "source_filter_unsupported",
            "{source_label}暂不支持当前检索中的部分筛选条件。",
        ),
        _problem(
            "source_filter_degraded",
            "{source_label}已降级应用筛选条件，结果可能比预期更宽。",
        ),
        _problem(
            "source_provider_failed",
            "{source_label}检索未完整完成，请稍后重试或切换来源。",
        ),
        _problem(
            "source_partial",
            "{source_label}检索仅部分完成，部分结果暂不可用。",
        ),
        _problem(
            "source_cancelled",
            "{source_label}检索已取消。",
        ),
        _problem(
            "source_unknown",
            "{source_label}检索结果暂时无法确认，系统需要先核对本次操作状态。",
        ),
    )
}
PUBLIC_SOURCE_REASON_CODES = frozenset(PUBLIC_SOURCE_PROBLEMS)


def _policy(
    internal_reason: str,
    public_problem_code: str,
    disposition: SourceOperationDisposition,
    *,
    user_action_code: UserActionCode | None = None,
) -> LiepinFailurePolicy:
    return LiepinFailurePolicy(
        internal_reason=internal_reason,
        public_problem_code=public_problem_code,
        source_lane_reason_code=internal_reason,
        source_operation_disposition=disposition,
        user_action_code=user_action_code,
    )


_LIEPIN_FAILURE_POLICY_ENTRIES = (
    _policy(
        "liepin_host_tab_missing",
        "source_browser_host_required",
        "user_action_required",
        user_action_code="open_liepin_host",
    ),
    _policy(
        "liepin_host_window_ambiguous",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_connection_not_connected",
        "source_login_required",
        "user_action_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "liepin_browser_login_required",
        "source_login_required",
        "user_action_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "liepin_browser_probe_unavailable",
        "source_browser_backend_unavailable",
        "failed",
    ),
    _policy(
        "liepin_browser_account_mismatch",
        "source_account_mismatch",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_backend_disabled",
        "source_browser_backend_unavailable",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_command_missing",
        "source_browser_installation_invalid",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_extension_disconnected",
        "source_browser_extension_disconnected",
        "failed",
    ),
    _policy(
        "liepin_opencli_bridge_build_mismatch",
        "source_browser_backend_incompatible",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_bridge_capability_missing",
        "source_browser_backend_incompatible",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_bridge_integrity_failed",
        "source_browser_installation_invalid",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_bridge_protocol_mismatch",
        "source_browser_backend_incompatible",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_bridge_wrong_implementation",
        "source_browser_backend_incompatible",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_daemon_not_running",
        "source_browser_backend_unavailable",
        "failed",
    ),
    _policy(
        "liepin_opencli_daemon_stale",
        "source_browser_backend_unavailable",
        "failed",
    ),
    _policy(
        "liepin_opencli_status_unavailable",
        "source_browser_backend_unavailable",
        "failed",
    ),
    _policy(
        "liepin_opencli_bootstrap_failed",
        "source_browser_installation_invalid",
        "failed",
    ),
    _policy(
        "liepin_opencli_forbidden_command",
        "source_browser_policy_blocked",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_forbidden_text",
        "source_browser_policy_blocked",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_host_blocked",
        "source_browser_policy_blocked",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_start_url_blocked",
        "source_browser_policy_blocked",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_window_policy_blocked",
        "source_browser_policy_blocked",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_budget_exhausted",
        "source_budget_exhausted",
        "partial",
    ),
    _policy(
        "liepin_opencli_timeout",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "liepin_opencli_detail_not_opened",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "liepin_opencli_detail_open_retry_exhausted",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "liepin_opencli_login_required",
        "source_login_required",
        "user_action_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "liepin_opencli_identity_intercept",
        "source_identity_confirmation_required",
        "user_action_required",
        user_action_code="complete_identity_check",
    ),
    _policy(
        "liepin_opencli_risk_page",
        "source_risk_or_verification_required",
        "user_action_required",
        user_action_code="complete_liepin_risk_check",
    ),
    _policy(
        "liepin_opencli_unknown_modal",
        "source_browser_interaction_required",
        "user_action_required",
        user_action_code="resolve_liepin_modal",
    ),
    _policy(
        "liepin_opencli_source_policy_missing",
        "source_browser_policy_blocked",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_config_invalid",
        "source_browser_backend_incompatible",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_removed_config",
        "source_browser_backend_incompatible",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_helper_empty_output",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_helper_invalid_input",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_helper_invalid_output",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_helper_output_too_large",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_malformed_state",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_lease_malformed",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_owned_marker_malformed",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_tab_response_malformed",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_filter_unapplied",
        "source_filter_unavailable",
        "incompatible",
    ),
    _policy(
        "liepin_opencli_search_input_unapplied",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "liepin_opencli_search_not_ready",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "liepin_opencli_results_not_ready",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "liepin_opencli_stale_ref",
        "source_browser_reference_stale",
        "failed",
    ),
    _policy(
        "liepin_opencli_stale_control_fence",
        "source_browser_reference_stale",
        "failed",
    ),
    _policy(
        "liepin_opencli_selector_not_found",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_selector_ambiguous",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_target_not_found",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_opencli_terminal_state",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "liepin_owned_tab_missing",
        "source_browser_reference_stale",
        "failed",
    ),
    _policy(
        "cancelled_by_user",
        "source_cancelled",
        "cancelled",
    ),
    _policy(
        "blocked_approval_missing",
        "source_browser_policy_blocked",
        "incompatible",
    ),
    _policy(
        "blocked_backend_unavailable",
        "source_browser_backend_unavailable",
        "failed",
    ),
    _policy(
        "blocked_budget_exhausted",
        "source_budget_exhausted",
        "partial",
    ),
    _policy(
        "blocked_login_required",
        "source_login_required",
        "user_action_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "blocked_compliance",
        "source_risk_or_verification_required",
        "user_action_required",
        user_action_code="complete_liepin_risk_check",
    ),
    _policy(
        "blocked_permission_required",
        "source_risk_or_verification_required",
        "user_action_required",
        user_action_code="complete_liepin_risk_check",
    ),
    _policy(
        "connection_safety_expired",
        "source_login_required",
        "user_action_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "extraction_failure",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "failed_internal_error",
        "source_unknown",
        "failed",
    ),
    _policy(
        "failed_malformed_output",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "failed_provider_error",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "login_expired",
        "source_login_required",
        "user_action_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "login_required",
        "source_login_required",
        "user_action_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "no_live_action_backend",
        "source_browser_backend_unavailable",
        "incompatible",
    ),
    _policy(
        "opencli_bootstrap_failed",
        "source_browser_installation_invalid",
        "failed",
    ),
    _policy(
        "page_timeout",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "partial_timeout",
        "source_browser_timeout",
        "partial",
    ),
    _policy(
        "partial_budget_exhausted",
        "source_budget_exhausted",
        "partial",
    ),
    _policy(
        "provider_connection_locked",
        "source_browser_backend_unavailable",
        "failed",
    ),
    _policy(
        "risk_control",
        "source_risk_or_verification_required",
        "user_action_required",
        user_action_code="complete_liepin_risk_check",
    ),
    _policy(
        "runtime_failed",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "selector_drift",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "source_age_filter_unsupported",
        "source_filter_unsupported",
        "incompatible",
    ),
    _policy(
        "source_backend_unavailable",
        "source_browser_backend_unavailable",
        "failed",
    ),
    _policy(
        "source_location_filter_partial",
        "source_filter_partial",
        "partial",
    ),
    _policy(
        "source_location_filter_unsupported",
        "source_filter_unsupported",
        "incompatible",
    ),
    _policy(
        "source_provider_error",
        "source_provider_failed",
        "failed",
    ),
    _policy(
        "source_risk_challenge",
        "source_risk_or_verification_required",
        "user_action_required",
        user_action_code="complete_liepin_risk_check",
    ),
    _policy(
        "source_timeout",
        "source_browser_timeout",
        "failed",
    ),
    _policy(
        "unknown_reason",
        "source_unknown",
        "failed",
    ),
    _policy(
        "verification_required",
        "source_risk_or_verification_required",
        "user_action_required",
        user_action_code="complete_liepin_risk_check",
    ),
)
LIEPIN_FAILURE_POLICIES = {policy.internal_reason: policy for policy in _LIEPIN_FAILURE_POLICY_ENTRIES}

if len(LIEPIN_FAILURE_POLICIES) != len(_LIEPIN_FAILURE_POLICY_ENTRIES):
    raise RuntimeError("liepin_failure_policy_registry_duplicate")
if not LIEPIN_WORKER_SAFE_REASON_CODES <= LIEPIN_FAILURE_POLICIES.keys():
    raise RuntimeError("liepin_failure_policy_registry_incomplete")
if not {policy.public_problem_code for policy in LIEPIN_FAILURE_POLICIES.values()} <= PUBLIC_SOURCE_PROBLEMS.keys():
    raise RuntimeError("liepin_failure_policy_public_problem_unknown")


def interpret_liepin_failure(
    internal_reason: object,
    *,
    operation: SourceOperation,
    cards_collected: bool = False,
    effect_unknown: bool = False,
    affected_scope_ref: str | None = None,
) -> FailureInterpretation:
    if operation not in {
        "verify_session",
        "search",
        "cards",
        "details",
        "continuation",
        "cleanup",
    }:
        raise ValueError("source_failure_operation_unsupported")
    reason = str(getattr(internal_reason, "value", internal_reason or "")).strip()
    policy = LIEPIN_FAILURE_POLICIES.get(reason)
    if policy is None:
        public_problem_code = "source_unknown"
        source_lane_reason_code = "failed_internal_error"
        disposition: SourceOperationDisposition = "failed"
        user_action_code = None
    else:
        public_problem_code = policy.public_problem_code
        source_lane_reason_code = policy.source_lane_reason_code
        disposition = policy.source_operation_disposition
        user_action_code = policy.user_action_code

    if effect_unknown:
        disposition = "reconciliation_unknown"
    elif cards_collected and disposition == "failed":
        disposition = "partial"
        if public_problem_code in {
            "source_browser_timeout",
            "source_provider_failed",
        }:
            source_lane_reason_code = "partial_timeout"

    user_action = None
    if user_action_code is not None and affected_scope_ref is not None:
        user_action = UserActionV1(
            code=user_action_code,
            instruction_key=USER_ACTION_INSTRUCTIONS[user_action_code],
            scope=USER_ACTION_SCOPES[user_action_code],
            affected_scope_ref=affected_scope_ref,
        )
    return FailureInterpretation(
        internal_reason=reason,
        operation=operation,
        public_problem_code=public_problem_code,
        source_lane_reason_code=source_lane_reason_code,
        source_operation_disposition=disposition,
        user_action=user_action,
    )


def public_source_problem_code(reason_code: object) -> str | None:
    text = str(getattr(reason_code, "value", reason_code or "")).strip()
    if not text:
        return None
    if text in PUBLIC_SOURCE_PROBLEMS:
        return text
    policy = LIEPIN_FAILURE_POLICIES.get(text)
    if policy is not None:
        return policy.public_problem_code
    return "source_unknown"


def public_source_problem_message(
    problem_code: object,
    *,
    source_label: str = "来源",
) -> str | None:
    code = public_source_problem_code(problem_code)
    if code is None:
        return None
    problem = PUBLIC_SOURCE_PROBLEMS[code]
    if not problem.user_facing or problem.message_template is None:
        return None
    label = source_label.strip() if isinstance(source_label, str) else ""
    if not label or len(label) > 24:
        label = "来源"
    return problem.message_template.format(source_label=label)


LIEPIN_BACKEND_MODE_BY_WORKER_MODE = {
    "disabled": "blocked",
    "opencli": "opencli",
    "fake_fixture": "fake_fixture",
}


__all__ = [
    "FailureInterpretation",
    "LIEPIN_BACKEND_MODE_BY_WORKER_MODE",
    "LIEPIN_FAILURE_POLICIES",
    "LIEPIN_WORKER_SAFE_REASON_CODES",
    "PUBLIC_SOURCE_PROBLEMS",
    "PUBLIC_SOURCE_REASON_CODES",
    "interpret_liepin_failure",
    "public_source_problem_code",
    "public_source_problem_message",
]
