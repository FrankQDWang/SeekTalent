from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json

from seektalent.progress import ProgressEvent
from seektalent.runtime.public_events import normalize_runtime_public_event, runtime_public_event_name
from seektalent_runtime_control.models import RuntimeControlEvent, RuntimeControlEventInput


PUBLIC_RUNTIME_EVENT_SCHEMA_VERSION = "runtime_public_event_v1"
RUNTIME_CONTROL_EVENT_SCHEMA_VERSION = "runtime-control-event/v1"

_REDACTED_SUMMARY = "runtime progress"
_SENSITIVE_KEY_FRAGMENTS = (
    "prompt",
    "provider",
    "resume",
    "auth",
    "token",
    "secret",
    "password",
    "cookie",
    "apikey",
    "rawstructuredoutput",
    "rawoutput",
    "rawresponse",
)
_SUMMARY_SENSITIVE_TERMS = (
    "authorization",
    "bearer",
    "cookie",
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "prompt",
    "provider",
    "resume",
)
_PUBLIC_SOURCE_LABELS = {"cts": "CTS", "liepin": "猎聘"}


def normalize_progress_event(progress: ProgressEvent, runtime_run_id: str, now: str) -> RuntimeControlEventInput:
    if progress.type == "runtime_public_event":
        return _normalize_public_progress_event(progress, runtime_run_id=runtime_run_id, now=now)
    return _normalize_internal_progress_event(progress, runtime_run_id=runtime_run_id, now=now)


def public_event_payload(event: RuntimeControlEvent | RuntimeControlEventInput) -> dict[str, object] | None:
    if event.visibility != "public":
        return None
    try:
        return _runtime_public_event_payload(
            normalize_runtime_public_event(event.payload),
            runtime_run_id=event.runtime_run_id,
        )
    except (TypeError, ValueError):
        return None


def _normalize_public_progress_event(
    progress: ProgressEvent,
    *,
    runtime_run_id: str,
    now: str,
) -> RuntimeControlEventInput:
    public_event = normalize_runtime_public_event(progress.payload)
    event_type = runtime_public_event_name(public_event["stage"])
    payload = _runtime_public_event_payload(public_event, runtime_run_id=runtime_run_id)
    idempotency_key = _text(progress.payload.get("idempotencyKey")) or _text(payload.get("eventId"))
    if idempotency_key is None:
        idempotency_key = _fallback_idempotency_key(
            runtime_run_id=runtime_run_id,
            progress_type=progress.type,
            round_no=public_event["roundNo"],
            timestamp=progress.timestamp,
            payload=payload,
        )
    return RuntimeControlEventInput(
        event_id=_event_id(runtime_run_id=runtime_run_id, idempotency_key=idempotency_key),
        runtime_run_id=runtime_run_id,
        event_type=event_type,
        stage=public_event["stage"],
        round_no=public_event["roundNo"],
        source_id=public_event["sourceKind"],
        status=public_event["status"],
        summary=_public_runtime_event_summary(public_event),
        payload=payload,
        schema_version=RUNTIME_CONTROL_EVENT_SCHEMA_VERSION,
        visibility="public",
        idempotency_key=idempotency_key,
        payload_kind="compact",
        workbench_event_global_seq=None,
        created_at=now,
    )


def _normalize_internal_progress_event(
    progress: ProgressEvent,
    *,
    runtime_run_id: str,
    now: str,
) -> RuntimeControlEventInput:
    payload = _redacted_dict(progress.payload)
    stage = _text(payload.get("stage")) or "runtime"
    round_no = progress.round_no if progress.round_no is not None else _non_negative_int(payload.get("roundNo"))
    idempotency_key = _text(progress.payload.get("eventId")) or _text(progress.payload.get("idempotencyKey"))
    compact_payload: dict[str, object] = {
        "progressType": progress.type,
        "occurredAt": progress.timestamp,
        **payload,
    }
    if idempotency_key is None:
        idempotency_key = _fallback_idempotency_key(
            runtime_run_id=runtime_run_id,
            progress_type=progress.type,
            round_no=round_no,
            timestamp=progress.timestamp,
            payload=compact_payload,
        )
    return RuntimeControlEventInput(
        event_id=_event_id(runtime_run_id=runtime_run_id, idempotency_key=idempotency_key),
        runtime_run_id=runtime_run_id,
        event_type=_progress_event_type(progress.type),
        stage=stage,
        round_no=round_no,
        source_id=_text(payload.get("sourceId")) or _text(payload.get("sourceKind")),
        status=_text(payload.get("status")) or "completed",
        summary=_safe_summary(progress.message),
        payload=compact_payload,
        schema_version=RUNTIME_CONTROL_EVENT_SCHEMA_VERSION,
        visibility="developer",
        idempotency_key=idempotency_key,
        payload_kind="compact",
        workbench_event_global_seq=None,
        created_at=now,
    )


def _runtime_public_event_payload(event: Mapping[str, object], *, runtime_run_id: str) -> dict[str, object]:
    return {
        "schemaVersion": PUBLIC_RUNTIME_EVENT_SCHEMA_VERSION,
        "runtimeRunId": runtime_run_id,
        "eventId": _runtime_public_event_id(event, runtime_run_id=runtime_run_id),
        "eventSeq": event["eventSeq"],
        "stage": event["stage"],
        "roundNo": event["roundNo"],
        "sourceKind": event["sourceKind"],
        "status": event["status"],
        "counts": _string_object_mapping(event.get("counts")),
        "details": _string_object_mapping(event.get("details")),
        "safeReasonCode": event["safeReasonCode"],
        "createdAt": event["createdAt"],
    }


def _public_runtime_event_summary(event: Mapping[str, object]) -> str:
    stage = event["stage"]
    status = event["status"]
    round_prefix = _round_prefix(event.get("roundNo"))
    source_label = _public_source_label(event.get("sourceKind"))
    counts = _string_object_mapping(event.get("counts"))
    reason = event.get("safeReasonCode")
    reason = reason if isinstance(reason, str) else None

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
    if stage == "source_result":
        return _public_source_result_summary(
            status=status,
            round_prefix=round_prefix,
            source_label=source_label,
            reason=reason,
            counts=counts,
        )
    if stage == "merge":
        merged = _non_negative_int(counts.get("mergedIdentities"))
        if merged is not None:
            return f"{round_prefix}候选人合并完成：新增 {merged} 位候选人。"
        return f"{round_prefix}候选人合并完成。"
    if stage == "scoring":
        top_pool_count = _non_negative_int(counts.get("topPoolCount"))
        if top_pool_count is not None:
            return f"{round_prefix}评分完成，{top_pool_count} 位候选人进入 Top Pool。"
        return f"{round_prefix}评分完成。"
    if stage == "feedback":
        return f"{round_prefix}复盘完成，准备调整下一轮检索策略。"
    if stage == "finalization":
        return "最终短名单已生成。" if status == "completed" else "正在汇总最终候选人。"
    return "招聘流程状态已更新。"


def _public_source_result_summary(
    *,
    status: object,
    round_prefix: str,
    source_label: str,
    reason: str | None,
    counts: Mapping[str, object],
) -> str:
    if status == "blocked":
        return f"{round_prefix}{source_label}检索受阻：{_public_failure_reason(reason, source_label=source_label, blocked=True)}"
    if status == "failed":
        return f"{round_prefix}{source_label}检索失败：{_public_failure_reason(reason, source_label=source_label, blocked=False)}"
    returned = _non_negative_int(counts.get("roundReturned"))
    identities = _non_negative_int(counts.get("roundIdentities"))
    if returned is not None and identities is not None:
        if status == "partial":
            return f"{round_prefix}{source_label}检索部分完成：返回 {returned} 条，新增 {identities} 位候选人。"
        return f"{round_prefix}{source_label}检索完成：返回 {returned} 条，新增 {identities} 位候选人。"
    if status == "partial":
        return f"{round_prefix}{source_label}检索部分完成。"
    return f"{round_prefix}{source_label}检索结果已更新。"


def _public_failure_reason(reason: str | None, *, source_label: str, blocked: bool) -> str:
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


def _round_prefix(value: object) -> str:
    round_no = _non_negative_int(value)
    return f"第 {round_no} 轮" if round_no is not None else "本轮"


def _public_source_label(value: object) -> str:
    if not isinstance(value, str):
        return "来源"
    return _PUBLIC_SOURCE_LABELS.get(value, "来源")


def _runtime_public_event_id(event: Mapping[str, object], *, runtime_run_id: str) -> str:
    round_no = event["roundNo"]
    round_part = round_no if round_no is not None else "final"
    source_part = event["sourceKind"] or "all"
    return f"{runtime_run_id}:{round_part}:{event['stage']}:{source_part}"


def _string_object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _redacted_dict(value: Mapping[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or _is_sensitive_key(key):
            continue
        redacted[key] = _redacted_value(item)
    return redacted


def _redacted_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _redacted_dict(_string_object_mapping(value))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_redacted_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(char for char in key.lower() if char.isalnum())
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_summary(message: str) -> str:
    if not isinstance(message, str):
        return _REDACTED_SUMMARY
    text = message.strip()
    if not text:
        return _REDACTED_SUMMARY
    if _looks_like_unsafe_summary(text):
        return _REDACTED_SUMMARY
    return text[:300]


def _looks_like_unsafe_summary(text: str) -> bool:
    upper = text.strip().upper()
    lower = text.lower()
    if "SHOULD_NOT_RENDER" in upper or upper.startswith("INTERNAL_"):
        return True
    if lower.startswith(("bearer ", "authorization:", "authorization=")) or "authorization=" in lower:
        return True
    if "http://" in lower or "https://" in lower:
        return True
    if any(term in lower for term in _SUMMARY_SENSITIVE_TERMS):
        return True
    return any(pattern in lower for pattern in ("api_key=", "apikey=", "token=", "cookie=", "password="))


def _progress_event_type(event_type: str) -> str:
    if event_type.startswith("runtime_"):
        return event_type
    return f"runtime_{event_type}"


def _fallback_idempotency_key(
    *,
    runtime_run_id: str,
    progress_type: str,
    round_no: int | None,
    timestamp: str,
    payload: Mapping[str, object],
) -> str:
    digest = _digest(
        {
            "runtimeRunId": runtime_run_id,
            "progressType": progress_type,
            "roundNo": round_no,
            "timestamp": timestamp,
            "payload": payload,
        }
    )
    return f"{runtime_run_id}:{progress_type}:{round_no if round_no is not None else 'none'}:{digest[:16]}"


def _event_id(*, runtime_run_id: str, idempotency_key: str) -> str:
    return f"rtevt_{_digest({'runtimeRunId': runtime_run_id, 'idempotencyKey': idempotency_key})[:32]}"


def _digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
