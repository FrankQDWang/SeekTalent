from __future__ import annotations

from collections.abc import Mapping, Sequence

from seektalent.public_payload_safety import public_source_identifier, public_text


COMPLETED_PROGRESS_SUMMARY = "招聘流程已完成。"
COMPLETED_RESULT_SUMMARY = "招聘流程已完成，最终候选人列表已生成。"
FINALIZATION_SUMMARY = "最终短名单已生成。"
IDLE_PROGRESS_SUMMARY = "当前还没有开始运行。"
QUEUED_PROGRESS_SUMMARY = "招聘流程已排队，等待开始。"
IDLE_RESULT_SUMMARY = "当前还没有运行结果。"
PENDING_RESULT_SUMMARY = "当前招聘流程尚未完成，还没有最终结果可供总结。"
FAILED_RESULT_SUMMARY = "招聘流程失败，请查看运行详情。"
CANCELLED_RESULT_SUMMARY = "招聘流程已取消。"

_DETAIL_TEXT_KEYS = {
    "resumeQualityComment",
    "reflectionSummary",
    "suggestedStopReason",
    "finalizationReasonCode",
}
_DETAIL_INT_KEYS = {"finalizationRevision"}
_DETAIL_BOOL_KEYS = {"suggestStop"}
_DETAIL_LIST_KEYS = {
    "suggestedActivateTerms",
    "suggestedAddFilterFields",
    "suggestedDeprioritizeTerms",
    "suggestedDropFilterFields",
    "suggestedDropTerms",
    "suggestedKeepFilterFields",
    "suggestedKeepTerms",
}
_DETAIL_QUERY_GROUP_KEYS = {"queryGroups"}
_QUERY_GROUP_LIFECYCLES = {"planned", "executed"}
_QUERY_EXECUTION_STATUSES = {"completed", "partial", "blocked", "failed"}
_QUERY_GROUP_LIFECYCLE_BY_STAGE = {
    "round_query": "planned",
    "feedback": "executed",
}
_PUBLIC_SOURCE_REASON_CODES = {
    "job_lease_expired",
    "relay_pending_worker",
    "runtime_failed",
    "source_login_required",
    "source_account_mismatch",
    "source_browser_timeout",
    "source_browser_backend_unavailable",
    "source_browser_reference_stale",
    "source_browser_extension_disconnected",
    "source_browser_policy_blocked",
    "source_risk_or_verification_required",
    "source_browser_interaction_required",
    "source_budget_exhausted",
    "source_filter_applied",
    "source_filter_partial",
    "source_filter_unavailable",
    "source_filter_unsupported",
    "source_filter_degraded",
    "source_location_filter_unsupported",
    "source_age_filter_unsupported",
    "source_provider_failed",
    "source_partial",
    "source_unknown",
}
_PUBLIC_REASON_MAP = {
    "blocked_backend_unavailable": "source_browser_backend_unavailable",
    "blocked_login_required": "source_login_required",
    "failed_provider_error": "source_provider_failed",
    "login_required": "source_login_required",
    "partial_timeout": "source_browser_timeout",
    "runtime_failed": "source_provider_failed",
    "cancelled_by_user": "source_unknown",
    "source_location_filter_partial": "source_filter_partial",
    "source_age_filter_unsupported": "source_filter_unavailable",
    "source_location_filter_unsupported": "source_filter_unavailable",
    "source_filter_unsupported": "source_filter_unavailable",
    "source_filter_applied": "source_filter_applied",
}
_RUNTIME_EVENT_STATUSES = {
    "pending",
    "queued",
    "starting",
    "running",
    "pause_requested",
    "paused",
    "resume_requested",
    "cancellation_requested",
    "cancelled",
    "completed",
    "partial",
    "blocked",
    "failed",
}
_RUNTIME_STATES = {"idle", "queued", "running", "completed", "failed", "cancelled"}
_PUBLIC_RUNTIME_EVENT_TYPES = {
    "runtime_run_started",
    "runtime_requirements_started",
    "runtime_requirements_completed",
    "runtime_controller_started",
    "runtime_search_started",
    "runtime_round_query_ready",
    "runtime_round_source_dispatch",
    "runtime_round_source_result",
    "runtime_round_merge_completed",
    "runtime_search_completed",
    "runtime_scoring_started",
    "runtime_scoring_completed",
    "runtime_round_scoring_completed",
    "runtime_round_first_page_expansion",
    "runtime_resume_quality_comment_completed",
    "runtime_reflection_started",
    "runtime_reflection_completed",
    "runtime_round_feedback_completed",
    "runtime_round_completed",
    "runtime_search_failed",
    "runtime_run_failed",
    "runtime_run_completed",
    "runtime_finalization_completed",
}
_PUBLIC_RUNTIME_STAGE_LABELS = {
    "queued": "排队中",
    "starting": "启动中",
    "startup": "启动中",
    "requirements": "岗位需求解析",
    "controller": "检索策略规划",
    "runtime": "运行中",
    "round": "检索轮次",
    "round_query": "查询策略",
    "source_dispatch": "发起来源检索",
    "source_result": "来源检索",
    "source_lanes": "来源检索",
    "source_search": "候选人检索",
    "source": "来源检索",
    "search": "候选人检索",
    "merge": "候选人合并",
    "scoring": "候选人评分",
    "first_page_expansion": "优质召回扩展",
    "resume_quality": "简历质量评估",
    "reflection": "检索复盘",
    "feedback": "检索复盘",
    "finalization": "结果汇总",
    "resume": "恢复运行",
    "command": "指令处理",
    "worker": "任务执行",
    "rescue": "检索恢复",
    "corpus_ingest": "语料准备",
    "completed": "已完成",
}
_PUBLIC_SOURCE_LABELS = {"cts": "CTS", "liepin": "猎聘"}
_COUNT_KEYS = {
    "roundReturned",
    "roundIdentities",
    "sourceCumulativeReturned",
    "sourceCumulativeIdentities",
    "roundUniqueIdentities",
    "mergedIdentities",
    "topPoolCount",
    "selectedIdentityCount",
    "feedbackCandidateCount",
    "qualifiedLaneCount",
    "expandedCandidateCount",
    "skippedSeenCount",
    "terminalFailureCount",
    "scoringFailureCount",
}


def runtime_event_terminal_summary(event_type: object) -> str | None:
    if event_type == "runtime_finalization_completed":
        return FINALIZATION_SUMMARY
    if event_type == "runtime_run_completed":
        return COMPLETED_PROGRESS_SUMMARY
    return None


def normalize_runtime_progress_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    if (runtime_run_id := _safe_runtime_identifier(payload.get("runtimeRunId"))) is not None:
        normalized["runtimeRunId"] = runtime_run_id
    if (event_seq := _non_negative_int(payload.get("runtimeEventSeq"))) is not None:
        normalized["runtimeEventSeq"] = event_seq
    if (event_type := _safe_runtime_event_type(payload.get("runtimeEventType"))) is not None:
        normalized["runtimeEventType"] = event_type
    if (status := _safe_runtime_status(payload.get("status"))) is not None:
        normalized["status"] = status
    if (stage := _safe_runtime_stage(payload.get("stage"))) is not None:
        normalized["stage"] = stage
    if (state := _safe_runtime_state(payload.get("state"))) is not None:
        normalized["state"] = state
    if (round_no := _non_negative_int(payload.get("roundNo"))) is not None:
        normalized["roundNo"] = round_no
    for key in ("sourceId", "sourceKind"):
        value = payload.get(key)
        if value is None:
            if key in payload:
                normalized[key] = None
        elif (source_kind := _safe_public_source_kind(value)) is not None:
            normalized[key] = source_kind
    if "safeReasonCode" in payload and payload.get("safeReasonCode") is None:
        normalized["safeReasonCode"] = None
    elif (safe_reason_code := safe_runtime_progress_reason_code(payload.get("safeReasonCode"))) is not None:
        normalized["safeReasonCode"] = safe_reason_code
    details = safe_runtime_progress_details(payload.get("details"), stage=normalized.get("stage"))
    if details:
        normalized["details"] = details
    counts = _safe_counts(payload.get("counts"))
    if counts:
        normalized["counts"] = counts
    summary = runtime_progress_summary(normalized)
    if summary is not None:
        normalized["summary"] = summary
    return normalized


def normalize_runtime_result_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    if (runtime_run_id := _safe_runtime_identifier(payload.get("runtimeRunId"))) is not None:
        normalized["runtimeRunId"] = runtime_run_id
    if (status := _safe_runtime_status(payload.get("status"))) is not None:
        normalized["status"] = status
    if (state := _safe_runtime_state(payload.get("state"))) is not None:
        normalized["state"] = state
    state = normalized.get("state") or normalized.get("status") or ""
    if not isinstance(state, str):
        state = ""
    normalized["summary"] = runtime_result_summary(None, state)
    facts = _safe_runtime_result_facts(payload.get("facts"))
    if facts:
        normalized["facts"] = facts
    return normalized


def runtime_result_summary(summary: object, state: str) -> str:
    del summary
    if state == "completed":
        return COMPLETED_RESULT_SUMMARY
    if state == "idle":
        return IDLE_RESULT_SUMMARY
    if state == "failed":
        return FAILED_RESULT_SUMMARY
    if state == "cancelled":
        return CANCELLED_RESULT_SUMMARY
    return PENDING_RESULT_SUMMARY


def runtime_progress_visible_summary(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    summary = normalize_runtime_progress_payload(payload).get("summary")
    if not isinstance(summary, str):
        return None
    stripped = summary.strip()
    return stripped or None


def runtime_progress_summary(payload: Mapping[str, object]) -> str | None:
    event_type = _safe_runtime_event_type(payload.get("runtimeEventType"))
    stage = _safe_runtime_stage(payload.get("stage"))
    status = _safe_runtime_status(payload.get("status"))
    state = _safe_runtime_state(payload.get("state"))
    effective_status = status or state
    round_prefix = _round_prefix(payload.get("roundNo"))
    source_label = _source_label(payload.get("sourceKind"))
    reason = safe_runtime_progress_reason_code(payload.get("safeReasonCode"))
    counts = _safe_counts(payload.get("counts"))

    if event_type is not None:
        event_summary = _runtime_event_progress_summary(
            event_type=event_type,
            stage=stage,
            status=effective_status,
            round_prefix=round_prefix,
            source_label=source_label,
            reason=reason,
            counts=counts,
        )
        if event_summary is not None:
            return event_summary
    return _runtime_stage_progress_summary(
        stage=stage,
        status=effective_status,
        round_prefix=round_prefix,
        source_label=source_label,
        reason=reason,
        counts=counts,
    )


def _runtime_event_progress_summary(
    *,
    event_type: str,
    stage: str | None,
    status: str | None,
    round_prefix: str,
    source_label: str,
    reason: str | None,
    counts: Mapping[str, int],
) -> str | None:
    terminal_summary = runtime_event_terminal_summary(event_type)
    if terminal_summary is not None:
        return terminal_summary
    if event_type == "runtime_run_started":
        return "招聘流程已开始。"
    if event_type == "runtime_requirements_started":
        return "正在解析确认后的岗位需求。"
    if event_type == "runtime_requirements_completed":
        return "岗位需求解析完成。"
    if event_type == "runtime_controller_started":
        return f"{round_prefix}正在规划检索策略。"
    if event_type == "runtime_search_started":
        return f"{round_prefix}开始检索候选人。"
    if event_type == "runtime_round_query_ready":
        return f"{round_prefix}查询策略已生成。"
    if event_type == "runtime_round_source_dispatch":
        if source_label == "来源":
            return "已发起候选人检索。"
        return f"已向{source_label}发起候选人检索。"
    if event_type == "runtime_round_source_result":
        return _source_result_summary(
            status=status,
            round_prefix=round_prefix,
            source_label=source_label,
            reason=reason,
            counts=counts,
        )
    if event_type == "runtime_round_merge_completed":
        merged = counts.get("mergedIdentities")
        if merged is not None:
            return f"{round_prefix}候选人合并完成：新增 {merged} 位候选人。"
        return f"{round_prefix}候选人合并完成。"
    if event_type == "runtime_search_completed":
        return f"{round_prefix}检索完成。"
    if event_type == "runtime_scoring_started":
        return f"{round_prefix}开始候选人评分。"
    if event_type == "runtime_scoring_completed":
        return f"{round_prefix}候选人评分完成。"
    if event_type == "runtime_round_scoring_completed":
        top_pool_count = counts.get("topPoolCount")
        if top_pool_count is not None:
            return f"{round_prefix}评分完成，{top_pool_count} 位候选人进入 Top Pool。"
        return f"{round_prefix}评分完成。"
    if event_type == "runtime_round_first_page_expansion":
        return (
            f"{round_prefix}优质召回扩展完成：新增 {counts.get('expandedCandidateCount', 0)} 位，"
            f"跳过重复 {counts.get('skippedSeenCount', 0)} 位。"
        )
    if event_type == "runtime_resume_quality_comment_completed":
        return f"{round_prefix}简历质量评估完成。"
    if event_type == "runtime_reflection_started":
        return f"{round_prefix}开始复盘检索效果。"
    if event_type in {"runtime_reflection_completed", "runtime_round_feedback_completed"}:
        return f"{round_prefix}复盘完成，准备调整下一轮检索策略。"
    if event_type == "runtime_round_completed":
        return f"{round_prefix}完成。"
    if event_type == "runtime_search_failed":
        return f"{round_prefix}检索失败：{_search_failure_reason(reason, source_label=source_label)}"
    if event_type == "runtime_run_failed":
        return FAILED_RESULT_SUMMARY
    return _runtime_stage_progress_summary(
        stage=stage,
        status=status,
        round_prefix=round_prefix,
        source_label=source_label,
        reason=reason,
        counts=counts,
    )


def _runtime_stage_progress_summary(
    *,
    stage: str | None,
    status: str | None,
    round_prefix: str,
    source_label: str,
    reason: str | None,
    counts: Mapping[str, int],
) -> str | None:
    if stage == "source_result":
        return _source_result_summary(
            status=status,
            round_prefix=round_prefix,
            source_label=source_label,
            reason=reason,
            counts=counts,
        )
    if stage == "round_query":
        if status in {"blocked", "failed"}:
            return f"{round_prefix}查询策略未能生成。"
        if status == "completed":
            return f"{round_prefix}查询策略已生成。"
        return f"{round_prefix}正在生成查询策略。"
    if stage == "source_dispatch":
        if source_label == "来源":
            return "已发起候选人检索。"
        return f"已向{source_label}发起候选人检索。"
    if status == "idle":
        return IDLE_PROGRESS_SUMMARY
    if status == "queued":
        return QUEUED_PROGRESS_SUMMARY
    if status == "starting":
        if stage is None:
            return "招聘流程正在启动。"
        return f"招聘流程正在启动，当前阶段：{_PUBLIC_RUNTIME_STAGE_LABELS[stage]}。"
    if status == "completed":
        if stage == "finalization":
            return FINALIZATION_SUMMARY
        return COMPLETED_PROGRESS_SUMMARY
    if status == "failed":
        return FAILED_RESULT_SUMMARY
    if status == "cancelled":
        return CANCELLED_RESULT_SUMMARY
    if status == "blocked":
        return "招聘流程已阻塞，请查看运行详情。"
    if status == "paused":
        return "招聘流程已暂停。"
    if status == "pause_requested":
        return "招聘流程正在暂停。"
    if status == "resume_requested":
        return "招聘流程正在恢复。"
    if status == "cancellation_requested":
        return "招聘流程正在取消。"
    if stage is not None:
        return f"招聘流程运行中，当前阶段：{_PUBLIC_RUNTIME_STAGE_LABELS[stage]}。"
    if status in {"pending", "queued", "starting", "running", "partial"}:
        return "招聘流程运行中。"
    return None


def _source_result_summary(
    *,
    status: str | None,
    round_prefix: str,
    source_label: str,
    reason: str | None,
    counts: Mapping[str, int],
) -> str:
    if status == "blocked":
        failure_reason = _failure_reason(reason, source_label=source_label, blocked=True)
        return f"{round_prefix}{source_label}检索受阻：{failure_reason}"
    if status == "failed":
        failure_reason = _failure_reason(reason, source_label=source_label, blocked=False)
        return f"{round_prefix}{source_label}检索失败：{failure_reason}"
    returned = counts.get("roundReturned")
    identities = counts.get("roundIdentities")
    if returned is not None and identities is not None:
        if status == "partial":
            return f"{round_prefix}{source_label}检索部分完成：返回 {returned} 条，新增 {identities} 位候选人。"
        return f"{round_prefix}{source_label}检索完成：返回 {returned} 条，新增 {identities} 位候选人。"
    if status == "partial":
        return f"{round_prefix}{source_label}检索部分完成。"
    return f"{round_prefix}{source_label}检索结果已更新。"


def _failure_reason(reason: str | None, *, source_label: str, blocked: bool) -> str:
    if reason == "source_browser_extension_disconnected":
        return f"{source_label}浏览器桥扩展未连接，请确认扩展已连接后重试。"
    if reason == "source_browser_backend_unavailable":
        return f"{source_label}浏览器桥暂不可用，系统会先尝试恢复连接；如果仍失败，请稍后重试。"
    if reason == "source_browser_reference_stale":
        return f"{source_label}页面引用持续失效，系统已尝试重开搜索页；请刷新猎聘页面后重试。"
    if reason in {"source_filter_unavailable", "source_filter_partial", "source_filter_unsupported"}:
        return f"{source_label}筛选条件未成功应用，请刷新页面后重试。"
    if reason == "source_browser_timeout":
        return f"{source_label}检索超时，请稍后重试。"
    if reason == "source_login_required":
        return f"{source_label}账号需要登录后才能继续检索。"
    if reason == "source_account_mismatch":
        return f"{source_label}账号与当前检索任务不匹配，请确认账号后重试。"
    if reason in {"source_browser_policy_blocked", "source_risk_or_verification_required"}:
        return f"{source_label}需要完成页面验证后才能继续检索。"
    if reason == "source_browser_interaction_required":
        return f"{source_label}需要人工完成页面操作后才能继续检索。"
    if reason == "source_budget_exhausted":
        return f"{source_label}本轮检索额度已用尽。"
    if reason in {"source_filter_applied", "source_filter_degraded"}:
        return f"{source_label}筛选条件已降级处理。"
    if reason in {"source_location_filter_unsupported", "source_age_filter_unsupported"}:
        return f"{source_label}暂不支持部分筛选条件。"
    if blocked:
        return f"{source_label}检索受阻，请稍后重试。"
    return "运行失败，请查看详情。"


def _search_failure_reason(reason: str | None, *, source_label: str) -> str:
    if reason is None:
        return "检索失败，请稍后重试。"
    return _failure_reason(reason, source_label=source_label, blocked=False)


def _round_prefix(value: object) -> str:
    round_no = _non_negative_int(value)
    return f"第 {round_no} 轮" if round_no is not None else "本轮"


def _source_label(value: object) -> str:
    source_kind = _safe_public_source_kind(value)
    if source_kind is None:
        return "来源"
    return _PUBLIC_SOURCE_LABELS.get(source_kind, "来源")


def safe_runtime_progress_details(value: object, *, stage: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    details: dict[str, object] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str):
            continue
        if key in _DETAIL_TEXT_KEYS:
            text = _safe_detail_text(raw_value, max_length=2000)
            if text is not None:
                details[key] = text
        elif key in _DETAIL_INT_KEYS:
            parsed = _non_negative_int(raw_value)
            if parsed is not None:
                details[key] = parsed
        elif key in _DETAIL_BOOL_KEYS:
            if isinstance(raw_value, bool):
                details[key] = raw_value
        elif key in _DETAIL_LIST_KEYS:
            values = _safe_detail_list(raw_value)
            if values:
                details[key] = values
        elif key in _DETAIL_QUERY_GROUP_KEYS:
            groups = _safe_query_groups(raw_value, expected_lifecycle=_query_group_lifecycle_for_stage(stage))
            if groups:
                details[key] = groups
    return details


def _safe_query_groups(
    value: object,
    *,
    expected_lifecycle: str | None,
) -> list[dict[str, object]]:
    if expected_lifecycle is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    groups: list[dict[str, object]] = []
    seen_query_instance_ids: set[str] = set()
    for item in value:
        group = _safe_query_group(item, expected_lifecycle=expected_lifecycle)
        if group is None:
            continue
        query_instance_id = group.get("queryInstanceId")
        if not isinstance(query_instance_id, str) or query_instance_id in seen_query_instance_ids:
            continue
        seen_query_instance_ids.add(query_instance_id)
        groups.append(group)
        if len(groups) >= 2:
            break
    return groups


def _safe_query_group(
    value: object,
    *,
    expected_lifecycle: str,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    item = {key: raw_value for key, raw_value in value.items() if isinstance(key, str)}
    query_instance_id = _safe_query_text(item.get("queryInstanceId"), max_length=160)
    term_group_key = _safe_query_text(item.get("termGroupKey"), max_length=160)
    query_role = _safe_query_text(item.get("queryRole"), max_length=80)
    lane_type = _safe_query_text(item.get("laneType"), max_length=80)
    query_terms = _safe_query_terms(item.get("queryTerms"))
    keyword_query = _safe_query_text(item.get("keywordQuery"), max_length=2000)
    lifecycle = _safe_query_text(item.get("lifecycle"), max_length=32)
    if (
        query_instance_id is None
        or term_group_key is None
        or query_role is None
        or lane_type is None
        or not query_terms
        or keyword_query is None
        or lifecycle not in _QUERY_GROUP_LIFECYCLES
        or lifecycle != expected_lifecycle
    ):
        return None
    group: dict[str, object] = {
        "queryInstanceId": query_instance_id,
        "termGroupKey": term_group_key,
        "queryRole": query_role,
        "laneType": lane_type,
        "queryTerms": query_terms,
        "keywordQuery": keyword_query,
        "lifecycle": lifecycle,
    }
    if lifecycle == "planned":
        group.update(
            executionStatus=None,
            attempted=False,
            rawCandidateCount=0,
            uniqueCandidateCount=0,
            duplicateCandidateCount=0,
            executions=[],
        )
        return group

    execution_status = _safe_query_text(item.get("executionStatus"), max_length=32)
    attempted = item.get("attempted")
    if execution_status not in _QUERY_EXECUTION_STATUSES or not isinstance(attempted, bool):
        return None
    group.update(
        executionStatus=execution_status,
        attempted=attempted,
        rawCandidateCount=_non_negative_int(item.get("rawCandidateCount")) or 0,
        uniqueCandidateCount=_non_negative_int(item.get("uniqueCandidateCount")) or 0,
        duplicateCandidateCount=_non_negative_int(item.get("duplicateCandidateCount")) or 0,
        executions=_safe_query_executions(item.get("executions")),
    )
    return group


def _safe_query_executions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    executions: list[dict[str, object]] = []
    seen_sources: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        entry = {key: raw_value for key, raw_value in item.items() if isinstance(key, str)}
        source_kind = _safe_public_source_kind(entry.get("sourceKind"))
        status = _safe_query_text(entry.get("status"), max_length=32)
        if source_kind is None or source_kind in seen_sources or status not in _QUERY_EXECUTION_STATUSES:
            continue
        execution: dict[str, object] = {
            "sourceKind": source_kind,
            "status": status,
            "rawCandidateCount": _non_negative_int(entry.get("rawCandidateCount")) or 0,
            "uniqueCandidateCount": _non_negative_int(entry.get("uniqueCandidateCount")) or 0,
            "duplicateCandidateCount": _non_negative_int(entry.get("duplicateCandidateCount")) or 0,
        }
        safe_reason_code = safe_runtime_progress_reason_code(entry.get("safeReasonCode"))
        if safe_reason_code is not None:
            execution["safeReasonCode"] = safe_reason_code
        executions.append(execution)
        seen_sources.add(source_kind)
        if len(executions) >= 2:
            break
    return executions


def _query_group_lifecycle_for_stage(stage: object) -> str | None:
    return _QUERY_GROUP_LIFECYCLE_BY_STAGE.get(stage) if isinstance(stage, str) else None


def _safe_query_terms(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    values: list[str] = []
    for item in value:
        text = _safe_query_text(item, max_length=160)
        if text is not None and text not in values:
            values.append(text)
        if len(values) >= 40:
            break
    return values


def _safe_query_text(value: object, *, max_length: int) -> str | None:
    return public_text(value, max_length=max_length)


def _safe_runtime_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 160:
        return None
    if any(not (character.isascii() and (character.isalnum() or character in "_-:.")) for character in text):
        return None
    return text


def _safe_runtime_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    status = value.strip()
    return status if status in _RUNTIME_EVENT_STATUSES else None


def _safe_runtime_state(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    state = value.strip()
    return state if state in _RUNTIME_STATES else None


def _safe_runtime_event_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text in _PUBLIC_RUNTIME_EVENT_TYPES else None


def _safe_runtime_stage(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stage = value.strip()
    return stage if stage in _PUBLIC_RUNTIME_STAGE_LABELS else None


def _safe_public_source_kind(value: object) -> str | None:
    return public_source_identifier(value)


def _safe_detail_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    values: list[str] = []
    for item in value:
        text = _safe_detail_text(item, max_length=160)
        if text is not None and text not in values:
            values.append(text)
        if len(values) >= 40:
            break
    return values


def _safe_detail_text(value: object, *, max_length: int) -> str | None:
    return public_text(value, max_length=max_length)


def _safe_runtime_result_facts(value: object) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    facts: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        item_map = {str(key): item_value for key, item_value in item.items() if isinstance(key, str)}
        label = _safe_detail_text(item_map.get("label"), max_length=80)
        fact_value = _safe_detail_text(item_map.get("value"), max_length=2000)
        if label is None or fact_value is None:
            continue
        facts.append({"label": label, "value": fact_value})
        if len(facts) >= 20:
            break
    return facts


def _safe_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, raw_count in value.items():
        if not isinstance(key, str) or key not in _COUNT_KEYS:
            continue
        count = _non_negative_int(raw_count)
        if count is not None:
            counts[key] = count
    return counts


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def safe_runtime_progress_reason_code(value: object) -> str | None:
    text = _safe_query_text(value, max_length=120)
    if text in _PUBLIC_SOURCE_REASON_CODES:
        return text
    mapped = _PUBLIC_REASON_MAP.get(text) if text is not None else None
    return mapped if mapped in _PUBLIC_SOURCE_REASON_CODES else None
