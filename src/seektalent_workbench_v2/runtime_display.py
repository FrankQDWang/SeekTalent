from __future__ import annotations

from collections.abc import Mapping, Sequence


COMPLETED_PROGRESS_SUMMARY = "招聘流程已完成。"
COMPLETED_RESULT_SUMMARY = "招聘流程已完成，最终候选人列表已生成。"
FINALIZATION_SUMMARY = "最终短名单已生成。"
IDLE_RESULT_SUMMARY = "当前还没有运行结果。"
PENDING_RESULT_SUMMARY = "当前招聘流程尚未完成，还没有最终结果可供总结。"

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
_PUBLIC_SOURCE_REASON_CODES = {
    "job_lease_expired",
    "relay_pending_worker",
    "runtime_failed",
    "source_login_required",
    "source_account_mismatch",
    "source_browser_timeout",
    "source_browser_backend_unavailable",
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
_PROGRESS_PAYLOAD_KEYS = {
    "runtimeRunId",
    "runtimeEventSeq",
    "runtimeEventType",
    "status",
    "stage",
    "summary",
    "state",
    "roundNo",
    "sourceId",
    "sourceKind",
    "safeReasonCode",
}
_RESULT_PAYLOAD_KEYS = {"runtimeRunId", "status", "state", "summary"}
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
}


def runtime_event_terminal_summary(event_type: object) -> str | None:
    if event_type == "runtime_finalization_completed":
        return FINALIZATION_SUMMARY
    if event_type == "runtime_run_completed":
        return COMPLETED_PROGRESS_SUMMARY
    return None


def normalize_runtime_progress_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = {key: payload[key] for key in _PROGRESS_PAYLOAD_KEYS if key in payload}
    details = safe_runtime_progress_details(payload.get("details"))
    if details:
        normalized["details"] = details
    counts = _safe_counts(payload.get("counts"))
    if counts:
        normalized["counts"] = counts
    blocked_source_result_summary = _blocked_source_result_summary(normalized)
    if blocked_source_result_summary is not None:
        normalized["summary"] = blocked_source_result_summary
        return normalized
    terminal_summary = runtime_event_terminal_summary(normalized.get("runtimeEventType"))
    if terminal_summary is not None:
        normalized["summary"] = terminal_summary
        return normalized
    summary = normalized.get("summary")
    if not _is_internal_runtime_summary(summary):
        return normalized
    if normalized.get("stage") == "finalization":
        normalized["summary"] = FINALIZATION_SUMMARY
    elif normalized.get("state") == "completed" or normalized.get("status") == "completed":
        normalized["summary"] = COMPLETED_PROGRESS_SUMMARY
    return normalized


def normalize_runtime_result_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = {key: payload[key] for key in _RESULT_PAYLOAD_KEYS if key in payload}
    state = str(normalized.get("state") or normalized.get("status") or "")
    normalized["summary"] = runtime_result_summary(normalized.get("summary"), state)
    facts = _safe_runtime_result_facts(payload.get("facts"))
    if facts:
        normalized["facts"] = facts
    return normalized


def runtime_result_summary(summary: object, state: str) -> str:
    if isinstance(summary, str) and summary.strip() and not _is_internal_runtime_summary(summary):
        return summary.strip()
    if state == "completed":
        return COMPLETED_RESULT_SUMMARY
    if state == "idle":
        return IDLE_RESULT_SUMMARY
    return PENDING_RESULT_SUMMARY


def runtime_progress_visible_summary(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    summary = normalize_runtime_progress_payload(payload).get("summary")
    if not isinstance(summary, str):
        return None
    stripped = summary.strip()
    return stripped or None


def _blocked_source_result_summary(payload: Mapping[str, object]) -> str | None:
    if payload.get("runtimeEventType") != "runtime_round_source_result":
        return None
    if str(payload.get("status") or "") != "blocked":
        return None
    round_no = payload.get("roundNo")
    round_prefix = f"第 {round_no} 轮" if isinstance(round_no, int) else "本轮"
    reason = _safe_detail_text(
        payload.get("safeReasonCode") or payload.get("summary"),
        max_length=500,
    )
    if _is_formatted_liepin_blocked_summary(reason):
        return reason
    return f"{round_prefix}猎聘检索受阻：{_runtime_failure_reason(reason)}"


def _runtime_failure_reason(reason: str | None) -> str:
    if reason == "liepin_opencli_stale_ref":
        return "猎聘页面引用已失效，需要刷新检索页面后重试。"
    if reason in {"liepin_opencli_extension_disconnected", "source_browser_extension_disconnected"}:
        return "猎聘浏览器桥扩展未连接，请确认扩展已连接后重试。"
    if reason in {
        "liepin_opencli_daemon_not_running",
        "liepin_opencli_daemon_stale",
        "liepin_opencli_status_unavailable",
        "source_browser_backend_unavailable",
    }:
        return "猎聘浏览器桥暂不可用，系统会先尝试恢复连接；如果仍失败，请稍后重试。"
    if reason in {"liepin_opencli_filter_unapplied", "source_filter_unavailable", "source_filter_partial"}:
        return "猎聘筛选条件未成功应用，请刷新猎聘页面后重试。"
    return reason or "猎聘检索受阻，请稍后重试。"


def _is_formatted_liepin_blocked_summary(reason: str | None) -> bool:
    return isinstance(reason, str) and "猎聘检索受阻：" in reason


def safe_runtime_progress_details(value: object) -> dict[str, object]:
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
            details[key] = _safe_query_groups(raw_value)
    return details


def _is_internal_runtime_summary(summary: object) -> bool:
    if not isinstance(summary, str):
        return True
    value = summary.strip()
    if not value:
        return True
    lowered = value.lower()
    if lowered in {"completed", "finalization", "run completed"}:
        return True
    if lowered.startswith("run status:"):
        return True
    if lowered.startswith("run completed after "):
        return True
    if "deterministic runtime ranking" in lowered:
        return True
    if lowered.startswith("selected ") and " final candidates" in lowered:
        return True
    return False


def _safe_query_groups(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    groups: list[dict[str, object]] = []
    seen_query_instance_ids: set[str] = set()
    for item in value:
        group = _safe_query_group(item)
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


def _safe_query_group(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    item = {key: raw_value for key, raw_value in value.items() if isinstance(key, str)}
    query_instance_id = _safe_detail_text(item.get("queryInstanceId"), max_length=160)
    term_group_key = _safe_detail_text(item.get("termGroupKey"), max_length=160)
    query_role = _safe_detail_text(item.get("queryRole"), max_length=80)
    lane_type = _safe_detail_text(item.get("laneType"), max_length=80)
    query_terms = _safe_detail_list(item.get("queryTerms"))
    keyword_query = _safe_detail_text(item.get("keywordQuery"), max_length=2000)
    lifecycle = _safe_detail_text(item.get("lifecycle"), max_length=32)
    if (
        query_instance_id is None
        or term_group_key is None
        or query_role is None
        or lane_type is None
        or not query_terms
        or keyword_query is None
        or lifecycle not in _QUERY_GROUP_LIFECYCLES
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

    execution_status = _safe_detail_text(item.get("executionStatus"), max_length=32)
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
        source_kind = _safe_detail_text(entry.get("sourceKind"), max_length=80)
        status = _safe_detail_text(entry.get("status"), max_length=32)
        if source_kind is None or source_kind in seen_sources or status not in _QUERY_EXECUTION_STATUSES:
            continue
        execution: dict[str, object] = {
            "sourceKind": source_kind,
            "status": status,
            "rawCandidateCount": _non_negative_int(entry.get("rawCandidateCount")) or 0,
            "uniqueCandidateCount": _non_negative_int(entry.get("uniqueCandidateCount")) or 0,
            "duplicateCandidateCount": _non_negative_int(entry.get("duplicateCandidateCount")) or 0,
        }
        safe_reason_code = _safe_reason_code(entry.get("safeReasonCode"))
        if safe_reason_code is not None:
            execution["safeReasonCode"] = safe_reason_code
        executions.append(execution)
        seen_sources.add(source_kind)
        if len(executions) >= 2:
            break
    return executions


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
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _looks_like_internal_marker(text):
        return None
    return text[:max_length]


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


def _looks_like_internal_marker(text: str) -> bool:
    stripped = text.strip()
    upper = stripped.upper()
    lower = stripped.lower()
    if "SHOULD_NOT_RENDER" in upper:
        return True
    if upper.startswith("INTERNAL_"):
        return True
    if lower.startswith(("bearer ", "authorization:")):
        return True
    return any(pattern in lower for pattern in ("api_key=", "apikey=", "token=", "cookie=", "password="))


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _safe_reason_code(value: object) -> str | None:
    text = _safe_detail_text(value, max_length=120)
    return text if text in _PUBLIC_SOURCE_REASON_CODES else None
