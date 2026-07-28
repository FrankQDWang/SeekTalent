from __future__ import annotations

from dataclasses import dataclass

from seektalent.user_action import (
    USER_ACTION_INSTRUCTIONS,
    USER_ACTION_SCOPES,
    UserActionCode,
    UserActionV1,
)


@dataclass(frozen=True, slots=True)
class PublicSourceProblem:
    code: str
    message_template: str | None
    user_facing: bool = True


@dataclass(frozen=True, slots=True)
class LiepinFailurePolicy:
    internal_reason: str
    public_problem_code: str
    user_action_code: UserActionCode | None = None
    legacy_lane_retryable_metadata: bool = True


@dataclass(frozen=True, slots=True)
class LiepinRecoveryGuidance:
    failure_cause_code: str
    reason: str
    action: str


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
        _problem("source_cleanup_pending", None, user_facing=False),
        _problem(
            "source_browser_host_required",
            "{source_label}需要先在 Chrome 中打开任意 h.liepin.com 页面，然后再开始检索。",
        ),
        _problem(
            "source_browser_window_ambiguous",
            "检测到多个可用的猎聘窗口，请只保留一个猎聘窗口后重试。",
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
            "source_configuration_invalid",
            "{source_label}检索配置无效或不完整，请检查本机配置并重新启动 SeekTalent；若仍失败，请重新安装应用。",
        ),
        _problem(
            "source_runtime_unavailable",
            "{source_label}检索组件未正确启动，请重新启动 SeekTalent 后重试。",
        ),
        _problem(
            "source_removed_cleanup_config",
            "检测到已移除的旧即时回收/cleanup 配置，请删除旧配置后重试；标签页正常回收仅由 60 秒空闲到期负责。",
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
            "source_browser_page_not_operable",
            "{source_label}页面当前不可操作，请切换到 Chrome 查看并处理。",
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
            "{source_label}检索失败，但暂时无法确定具体原因，请稍后重试；若仍失败，请联系支持。",
        ),
    )
}
PUBLIC_SOURCE_REASON_CODES = frozenset(PUBLIC_SOURCE_PROBLEMS)


def _policy(
    internal_reason: str,
    public_problem_code: str,
    *,
    user_action_code: UserActionCode | None = None,
    legacy_lane_retryable_metadata: bool = True,
) -> LiepinFailurePolicy:
    return LiepinFailurePolicy(
        internal_reason=internal_reason,
        public_problem_code=public_problem_code,
        user_action_code=user_action_code,
        legacy_lane_retryable_metadata=legacy_lane_retryable_metadata,
    )


_LIEPIN_FAILURE_POLICY_ENTRIES = (
    _policy(
        "liepin_host_tab_missing",
        "source_browser_host_required",
        user_action_code="open_liepin_host",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_host_window_ambiguous",
        "source_browser_window_ambiguous",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_connection_not_connected",
        "source_login_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy(
        "liepin_browser_login_required",
        "source_login_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy("liepin_browser_probe_unavailable", "source_browser_backend_unavailable"),
    _policy("liepin_browser_account_mismatch", "source_account_mismatch"),
    _policy(
        "liepin_opencli_backend_disabled",
        "source_browser_backend_unavailable",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_command_missing",
        "source_browser_installation_invalid",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_extension_disconnected",
        "source_browser_extension_disconnected",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_bridge_build_mismatch",
        "source_browser_backend_incompatible",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_bridge_capability_missing",
        "source_browser_backend_incompatible",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_bridge_integrity_failed",
        "source_browser_installation_invalid",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_bridge_protocol_mismatch",
        "source_browser_backend_incompatible",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_bridge_wrong_implementation",
        "source_browser_backend_incompatible",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_daemon_not_running",
        "source_browser_backend_unavailable",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_daemon_stale",
        "source_browser_backend_unavailable",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_status_unavailable",
        "source_browser_backend_unavailable",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_bootstrap_failed",
        "source_browser_installation_invalid",
        legacy_lane_retryable_metadata=False,
    ),
    _policy("liepin_verify_session_gate_missing", "source_runtime_unavailable"),
    _policy(
        "liepin_opencli_forbidden_command",
        "source_browser_policy_blocked",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_forbidden_text",
        "source_browser_policy_blocked",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_host_blocked",
        "source_browser_policy_blocked",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_start_url_blocked",
        "source_browser_policy_blocked",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_window_policy_blocked",
        "source_browser_policy_blocked",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_budget_exhausted",
        "source_budget_exhausted",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_timeout",
        "source_browser_timeout",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_detail_not_opened",
        "source_browser_timeout",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_detail_open_retry_exhausted",
        "source_browser_timeout",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_login_required",
        "source_login_required",
        user_action_code="log_in_to_liepin",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_identity_intercept",
        "source_identity_confirmation_required",
        user_action_code="complete_identity_check",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_risk_page",
        "source_risk_or_verification_required",
        user_action_code="complete_liepin_risk_check",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_unknown_modal",
        "source_browser_page_not_operable",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_config_invalid",
        "source_configuration_invalid",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_removed_config",
        "source_removed_cleanup_config",
        legacy_lane_retryable_metadata=False,
    ),
    _policy("liepin_protected_artifact_root_missing", "source_configuration_invalid"),
    _policy(
        "liepin_opencli_helper_empty_output",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_helper_invalid_input",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_helper_invalid_output",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_helper_output_too_large",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_malformed_state",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_lease_malformed",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_owned_marker_malformed",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_tab_response_malformed",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy("liepin_opencli_fill_verification_failed", "source_provider_failed"),
    _policy("liepin_opencli_filter_option_unavailable", "source_filter_unavailable"),
    _policy("liepin_opencli_filter_clear_failed", "source_filter_unavailable"),
    _policy(
        "liepin_opencli_filter_unapplied",
        "source_filter_unavailable",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_search_input_unapplied",
        "source_browser_timeout",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_search_not_ready",
        "source_browser_page_not_operable",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_results_not_ready",
        "source_browser_timeout",
        legacy_lane_retryable_metadata=False,
    ),
    _policy("liepin_opencli_search_restore_failed", "source_partial"),
    _policy(
        "liepin_opencli_stale_ref",
        "source_browser_reference_stale",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_stale_control_fence",
        "source_browser_reference_stale",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_candidate_identity_mismatch",
        "source_browser_reference_stale",
    ),
    _policy(
        "liepin_opencli_selector_not_found",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_selector_ambiguous",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "liepin_opencli_target_not_found",
        "source_provider_failed",
        legacy_lane_retryable_metadata=False,
    ),
    _policy("liepin_opencli_terminal_state", "source_provider_failed"),
    _policy("liepin_opencli_card_extract_failed", "source_provider_failed"),
    _policy("liepin_owned_tab_missing", "source_browser_reference_stale"),
    _policy("liepin_first_page_expansion_blocked", "source_provider_failed"),
    _policy("liepin_first_page_expansion_partial", "source_partial"),
    _policy(
        "liepin_first_page_continuation_cleanup_failed",
        "source_cleanup_pending",
    ),
    _policy("cancelled_by_user", "source_cancelled"),
    _policy("blocked_approval_missing", "source_browser_policy_blocked"),
    _policy("blocked_backend_unavailable", "source_browser_backend_unavailable"),
    _policy("blocked_budget_exhausted", "source_budget_exhausted"),
    _policy(
        "blocked_login_required",
        "source_login_required",
        user_action_code="log_in_to_liepin",
        legacy_lane_retryable_metadata=False,
    ),
    _policy(
        "blocked_compliance",
        "source_risk_or_verification_required",
        user_action_code="complete_liepin_risk_check",
    ),
    _policy(
        "connection_safety_expired",
        "source_login_required",
        user_action_code="log_in_to_liepin",
        legacy_lane_retryable_metadata=False,
    ),
    _policy("failed_internal_error", "source_unknown"),
    _policy("failed_provider_error", "source_provider_failed"),
    _policy("requirement_sheet_missing", "source_provider_failed"),
    _policy(
        "login_required",
        "source_login_required",
        user_action_code="log_in_to_liepin",
    ),
    _policy("no_live_action_backend", "source_browser_backend_unavailable"),
    _policy("opencli_bootstrap_failed", "source_browser_installation_invalid"),
    _policy("partial_timeout", "source_browser_timeout"),
    _policy("partial_budget_exhausted", "source_budget_exhausted"),
    _policy(
        "risk_control",
        "source_risk_or_verification_required",
        user_action_code="complete_liepin_risk_check",
        legacy_lane_retryable_metadata=False,
    ),
    _policy("runtime_failed", "source_provider_failed"),
    _policy("source_age_filter_unsupported", "source_filter_unsupported"),
    _policy("source_location_filter_unsupported", "source_filter_unsupported"),
    _policy("unknown_reason", "source_unknown"),
    _policy(
        "verification_required",
        "source_risk_or_verification_required",
        user_action_code="complete_liepin_risk_check",
        legacy_lane_retryable_metadata=False,
    ),
)
LIEPIN_FAILURE_POLICIES = {
    policy.internal_reason: policy for policy in _LIEPIN_FAILURE_POLICY_ENTRIES
}
LIEPIN_PRODUCTION_FAILURE_REASON_CODES = frozenset(LIEPIN_FAILURE_POLICIES)

_RECOVERY_GUIDANCE_ENTRIES = (
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_daemon_not_running",
        reason="SeekTalent 自有的 WTSCLI runtime/daemon 当前不可用。",
        action="请启动当前 SeekTalent 自有的 WTSCLI 服务。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_daemon_stale",
        reason="当前 WTSCLI runtime/daemon 未返回有效的当前构建状态。",
        action="请重新启动当前 SeekTalent 自有的 WTSCLI 服务。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_status_unavailable",
        reason="当前无法确认 WTSCLI runtime/daemon 的有效状态。",
        action="请重新启动当前 SeekTalent 自有的 WTSCLI 服务。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_bootstrap_failed",
        reason="当前 SeekTalent 内置的 WTSCLI runtime 无法启动。",
        action="请重新打开当前 SeekTalent 应用。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_command_missing",
        reason="当前 SeekTalent 安装中缺少可用的 WTSCLI runtime。",
        action="请重新安装当前 SeekTalent 安装包。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_extension_disconnected",
        reason="Chrome 中的 WTSCLI 扩展未连接；猎聘登录状态尚无法验证。",
        action="请在 Chrome 的 chrome://extensions 中启用或重新加载 WTSCLI 扩展。",
    ),
    *(
        LiepinRecoveryGuidance(
            failure_cause_code=code,
            reason="WTSCLI runtime 与 Chrome 扩展的 exact build 或协议不匹配。",
            action="请在 Chrome 的 chrome://extensions 中重新加载 WTSCLI 扩展。",
        )
        for code in (
            "liepin_opencli_bridge_build_mismatch",
            "liepin_opencli_bridge_capability_missing",
            "liepin_opencli_bridge_protocol_mismatch",
            "liepin_opencli_bridge_wrong_implementation",
        )
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_bridge_integrity_failed",
        reason="当前 SeekTalent 安装中的 WTSCLI runtime 或扩展文件不完整。",
        action="请重新安装当前 SeekTalent 安装包。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_host_tab_missing",
        reason="Chrome 中没有可用的猎聘 host tab。",
        action="请在 Chrome 中打开任意 https://h.liepin.com/ 页面并保持该页面打开。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_login_required",
        reason="Chrome 中的猎聘会话尚未登录。",
        action="请切换到 Chrome 并完成猎聘登录。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_browser_login_required",
        reason="Chrome 中的猎聘会话尚未登录。",
        action="请切换到 Chrome 并完成猎聘登录。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_identity_intercept",
        reason="猎聘正在等待选择招聘身份或企业。",
        action="请切换到 Chrome 并完成招聘身份或企业选择。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_risk_page",
        reason="猎聘页面已明确显示验证码、安全验证或风险提示。",
        action="请切换到 Chrome 并人工完成页面验证。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_search_not_ready",
        reason="猎聘页面当前不可操作，尚无法进入可用的搜索页。",
        action="请切换到 Chrome 查看并处理当前猎聘页面。",
    ),
    LiepinRecoveryGuidance(
        failure_cause_code="liepin_opencli_unknown_modal",
        reason="猎聘页面当前不可操作，具体原因尚无法确认。",
        action="请切换到 Chrome 查看并处理当前猎聘页面。",
    ),
)
LIEPIN_RECOVERY_GUIDANCE = {
    guidance.failure_cause_code: guidance
    for guidance in _RECOVERY_GUIDANCE_ENTRIES
}

if len(LIEPIN_RECOVERY_GUIDANCE) != len(_RECOVERY_GUIDANCE_ENTRIES):
    raise RuntimeError("liepin_recovery_guidance_duplicate")

if len(LIEPIN_FAILURE_POLICIES) != len(_LIEPIN_FAILURE_POLICY_ENTRIES):
    raise RuntimeError("liepin_failure_policy_registry_duplicate")
if not {
    policy.public_problem_code for policy in LIEPIN_FAILURE_POLICIES.values()
} <= PUBLIC_SOURCE_PROBLEMS.keys():
    raise RuntimeError("liepin_failure_policy_public_problem_unknown")


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


def legacy_lane_retryable_metadata(internal_reason: object) -> bool:
    """Return legacy lane metadata without granting retry authority."""
    text = str(
        getattr(internal_reason, "value", internal_reason or "")
    ).strip()
    policy = LIEPIN_FAILURE_POLICIES.get(text)
    if policy is None:
        return True
    return policy.legacy_lane_retryable_metadata


def user_action_for_liepin_failure(
    internal_reason: object,
    *,
    affected_scope_ref: str,
) -> UserActionV1 | None:
    text = str(getattr(internal_reason, "value", internal_reason or "")).strip()
    policy = LIEPIN_FAILURE_POLICIES.get(text)
    if policy is None or policy.user_action_code is None:
        return None
    code = policy.user_action_code
    return UserActionV1(
        code=code,
        instruction_key=USER_ACTION_INSTRUCTIONS[code],
        scope=USER_ACTION_SCOPES[code],
        affected_scope_ref=affected_scope_ref,
    )


def public_liepin_failure_cause_code(reason_code: object) -> str | None:
    text = str(getattr(reason_code, "value", reason_code or "")).strip()
    return text if text in LIEPIN_RECOVERY_GUIDANCE else None


def liepin_recovery_guidance(reason_code: object) -> LiepinRecoveryGuidance | None:
    code = public_liepin_failure_cause_code(reason_code)
    return LIEPIN_RECOVERY_GUIDANCE.get(code) if code is not None else None


LIEPIN_BACKEND_MODE_BY_WORKER_MODE = {
    "disabled": "blocked",
    "opencli": "opencli",
    "fake_fixture": "fake_fixture",
}


__all__ = [
    "LIEPIN_BACKEND_MODE_BY_WORKER_MODE",
    "LIEPIN_FAILURE_POLICIES",
    "LIEPIN_PRODUCTION_FAILURE_REASON_CODES",
    "LIEPIN_RECOVERY_GUIDANCE",
    "LiepinRecoveryGuidance",
    "PUBLIC_SOURCE_PROBLEMS",
    "PUBLIC_SOURCE_REASON_CODES",
    "legacy_lane_retryable_metadata",
    "liepin_recovery_guidance",
    "public_liepin_failure_cause_code",
    "public_source_problem_code",
    "public_source_problem_message",
    "user_action_for_liepin_failure",
]
