from __future__ import annotations

from seektalent_conversation_agent.models import TranscriptActivityItem
from seektalent_runtime_control.models import RuntimeControlEvent


_TERMINAL_EVENT_STATUSES = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "superseded": "superseded",
    "applied": "completed",
    "rejected": "failed",
}


def project_runtime_event(
    *,
    conversation_id: str,
    event: RuntimeControlEvent,
    activity_id: str,
) -> TranscriptActivityItem:
    activity_type = _activity_type(event)
    status = _activity_status(event)
    created_at = event.created_at
    return TranscriptActivityItem(
        activity_id=activity_id,
        conversation_id=conversation_id,
        activity_seq=0,
        activity_key=_activity_key(conversation_id=conversation_id, event=event, activity_type=activity_type),
        activity_type=activity_type,
        status=status,
        title=_activity_title(activity_type),
        summary=event.summary,
        source_runtime_run_id=event.runtime_run_id,
        source_event_id_latest=event.event_id,
        source_event_seq_start=event.event_seq,
        source_event_seq_latest=event.event_seq,
        payload=event.payload,
        started_at=created_at,
        updated_at=created_at,
        completed_at=created_at if status in {"completed", "failed", "cancelled", "superseded"} else None,
        created_at=created_at,
    )


def _activity_key(*, conversation_id: str, event: RuntimeControlEvent, activity_type: str) -> str:
    round_part = event.round_no if event.round_no is not None else "global"
    source_part = event.source_id or event.payload.get("sourceId") or event.payload.get("sourceKind") or "all"
    command_id = event.payload.get("commandId")
    amendment_id = event.payload.get("amendmentId")
    if command_id is not None:
        return f"{conversation_id}:{event.runtime_run_id}:command:{command_id}"
    if amendment_id is not None:
        return f"{conversation_id}:{event.runtime_run_id}:next_round_requirement:{amendment_id}"
    return f"{conversation_id}:{event.runtime_run_id}:{activity_type}:{round_part}:{source_part}"


def _activity_status(event: RuntimeControlEvent) -> str:
    if event.status in _TERMINAL_EVENT_STATUSES:
        return _TERMINAL_EVENT_STATUSES[event.status]
    if event.status == "started":
        return "started"
    if event.status in {"pending", "queued"}:
        return "queued"
    if event.status in {"needs_review"}:
        return "in_progress"
    return "in_progress"


def _activity_type(event: RuntimeControlEvent) -> str:
    event_type = event.event_type
    stage = event.stage
    if "requirement" in event_type and "next_round" not in event_type:
        return "requirement_extraction"
    if "next_round_requirement" in event_type:
        return "next_round_requirement"
    if event_type.startswith("runtime_command_"):
        return "command"
    if "source_dispatch" in event_type:
        return "source_dispatch"
    if "source" in event_type:
        return "source_result"
    if "query" in event_type:
        return "query_generation"
    if "score" in event_type or "scoring" in event_type:
        return "scoring"
    if "feedback" in event_type or "reflection" in event_type:
        return "feedback"
    if "final" in event_type or stage == "finalization":
        return "finalization"
    if "merge" in event_type:
        return "merge"
    if "round" in event_type or stage == "round":
        return "round_controller"
    if "executor" in event_type or "run_started" in event_type:
        return "workflow_start"
    return "round_controller"


def _activity_title(activity_type: str) -> str:
    titles = {
        "requirement_extraction": "需求拆解",
        "workflow_start": "启动工作流",
        "round_controller": "轮次控制",
        "query_generation": "生成检索词",
        "source_dispatch": "分发来源",
        "source_result": "来源返回",
        "merge": "合并结果",
        "scoring": "候选人评分",
        "feedback": "反馈反思",
        "command": "运行命令",
        "next_round_requirement": "下一轮需求",
        "detail_answer": "详情回答",
        "finalization": "最终汇总",
        "context_compaction": "上下文压缩",
        "memory_recall": "记忆召回",
        "memory_review": "记忆确认",
    }
    return titles.get(activity_type, "运行进度")
