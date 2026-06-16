from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from seektalent_conversation_agent.models import (
    AgentToolCallRecord,
    ContextCompactionRecord,
    TranscriptActivityItem,
    TranscriptMessage,
)
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchStatus,
    AgentWorkbenchStreamKind,
    AgentWorkbenchTranscriptEventResponse,
    AgentWorkbenchTranscriptGroupResponse,
    AgentWorkbenchTranscriptPayloadResponse,
)
from seektalent_ui.agent_workbench_projection import AgentWorkbenchProjectionInput


def build_transcript_groups(input: AgentWorkbenchProjectionInput) -> list[AgentWorkbenchTranscriptGroupResponse]:
    conversation_id = input.conversation_reopen_state.conversation_id
    facts = sorted(_transcript_facts(input), key=lambda fact: fact.sort_key)
    groups: list[AgentWorkbenchTranscriptGroupResponse] = []
    segment_no = 0
    active_group_id: str | None = None
    active_events: list[AgentWorkbenchTranscriptEventResponse] = []

    def flush_active() -> None:
        nonlocal active_events, active_group_id
        if active_group_id is None or not active_events:
            active_group_id = None
            active_events = []
            return
        status = _group_status(active_events)
        groups.append(
            AgentWorkbenchTranscriptGroupResponse(
                groupId=active_group_id,
                title="已处理",
                status=status,
                startedAt=active_events[0].createdAt,
                completedAt=active_events[-1].createdAt if status == "completed" else None,
                events=active_events,
            )
        )
        active_group_id = None
        active_events = []

    for fact in facts:
        if fact.event.kind == "context.compacted":
            flush_active()
            groups.append(_context_group(fact.event))
            continue
        if active_group_id is None or fact.starts_segment:
            flush_active()
            segment_no += 1
            active_group_id = f"conversation:{conversation_id}:segment:{segment_no}"
        active_events.append(fact.event)
    flush_active()
    return groups


@dataclass(frozen=True)
class _TranscriptFact:
    sort_key: tuple[str, int, str]
    starts_segment: bool
    event: AgentWorkbenchTranscriptEventResponse


def _transcript_facts(input: AgentWorkbenchProjectionInput) -> Iterable[_TranscriptFact]:
    runtime_coverages = tuple(_activity_runtime_coverages(input.activity_items))
    for message in input.messages:
        event = _message_event(message)
        yield _TranscriptFact(
            sort_key=(event.createdAt, _event_kind_rank(event.kind, message.role), event.eventId),
            starts_segment=message.role == "user",
            event=event,
        )
    for tool in input.tool_call_records:
        event = _tool_event(tool)
        yield _TranscriptFact(
            sort_key=(event.createdAt, _event_kind_rank(event.kind), event.eventId),
            starts_segment=False,
            event=event,
        )
    for activity in input.activity_items:
        event = _activity_event(activity)
        yield _TranscriptFact(
            sort_key=(event.createdAt, _event_kind_rank(event.kind), event.eventId),
            starts_segment=False,
            event=event,
        )
    for runtime_event in input.runtime_events:
        if _runtime_event_is_materialized(runtime_event, runtime_coverages):
            continue
        event = _runtime_event(runtime_event)
        yield _TranscriptFact(
            sort_key=(event.createdAt, _event_kind_rank(event.kind), event.eventId),
            starts_segment=False,
            event=event,
        )
    for compaction in input.context_compactions:
        event = _context_event(compaction)
        yield _TranscriptFact(
            sort_key=(event.createdAt, _event_kind_rank(event.kind), event.eventId),
            starts_segment=False,
            event=event,
        )


def _message_event(message: TranscriptMessage) -> AgentWorkbenchTranscriptEventResponse:
    return AgentWorkbenchTranscriptEventResponse(
        eventId=f"message:{message.message_id}:completed",
        itemId=message.message_id,
        kind="message.completed",
        status="completed",
        label=_message_label(message),
        summary=message.text,
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId=message.message_id),
        createdAt=message.created_at,
    )


def _tool_event(tool: AgentToolCallRecord) -> AgentWorkbenchTranscriptEventResponse:
    status = _status(tool.status)
    return AgentWorkbenchTranscriptEventResponse(
        eventId=f"tool:{tool.tool_call_id}:{_tool_kind(status)}",
        itemId=tool.tool_call_id,
        kind=_tool_kind(status),
        status=status,
        label=tool.tool_name,
        summary=_tool_summary(tool),
        payload=AgentWorkbenchTranscriptPayloadResponse(
            kind="tool",
            itemId=tool.tool_call_id,
            summary=_tool_summary(tool),
        ),
        createdAt=tool.started_at,
    )


def _activity_event(activity: TranscriptActivityItem) -> AgentWorkbenchTranscriptEventResponse:
    return AgentWorkbenchTranscriptEventResponse(
        eventId=f"activity:{activity.activity_id}:upserted",
        itemId=activity.activity_id,
        kind="activity.upserted",
        status=_status(activity.status),
        label=activity.title,
        summary=activity.summary,
        payload=AgentWorkbenchTranscriptPayloadResponse(
            kind="activity",
            activityId=activity.activity_id,
            activitySeq=activity.activity_seq,
            activityType=activity.activity_type,
            sourceRuntimeRunId=activity.source_runtime_run_id,
            summary=activity.summary,
        ),
        createdAt=activity.created_at,
    )


def _runtime_event(runtime_event: object) -> AgentWorkbenchTranscriptEventResponse:
    event_type = _str_or_none(_attr(runtime_event, "event_type")) or "runtime_event"
    event_id = _str_or_none(_attr(runtime_event, "event_id")) or event_type
    runtime_run_id = _str_or_none(_attr(runtime_event, "runtime_run_id")) or "runtime"
    event_seq = _int_or_none(_attr(runtime_event, "event_seq")) or 0
    summary = _str_or_none(_attr(runtime_event, "summary"))
    kind = _runtime_event_kind(event_type)
    return AgentWorkbenchTranscriptEventResponse(
        eventId=f"runtime:{runtime_run_id}:{event_seq}",
        itemId=event_id,
        kind=kind,
        status=_status(_str_or_none(_attr(runtime_event, "status"))),
        label=_str_or_none(_attr(runtime_event, "stage")) or event_type,
        summary=summary,
        payload=AgentWorkbenchTranscriptPayloadResponse(
            kind=_runtime_payload_kind(kind),
            itemId=event_id,
            sourceRuntimeRunId=runtime_run_id,
            summary=summary,
        ),
        createdAt=_str_or_none(_attr(runtime_event, "created_at")) or "",
    )


def _context_event(compaction: ContextCompactionRecord) -> AgentWorkbenchTranscriptEventResponse:
    return AgentWorkbenchTranscriptEventResponse(
        eventId=f"context:{compaction.compaction_id}:compacted",
        itemId=compaction.compaction_id,
        kind="context.compacted",
        status=_status(compaction.status),
        label="上下文已压缩",
        summary=compaction.trigger_reason_code,
        payload=AgentWorkbenchTranscriptPayloadResponse(
            kind="context",
            itemId=compaction.compaction_id,
            summary=compaction.trigger_reason_code,
        ),
        createdAt=compaction.created_at,
    )


@dataclass(frozen=True)
class _RuntimeCoverage:
    runtime_run_id: str
    event_seq_start: int | None
    event_seq_latest: int | None
    event_id_latest: str | None


def _activity_runtime_coverages(activities: Iterable[TranscriptActivityItem]) -> Iterable[_RuntimeCoverage]:
    for activity in activities:
        runtime_run_id = activity.source_runtime_run_id
        if runtime_run_id is None:
            continue
        yield _RuntimeCoverage(
            runtime_run_id=runtime_run_id,
            event_seq_start=activity.source_event_seq_start,
            event_seq_latest=activity.source_event_seq_latest,
            event_id_latest=activity.source_event_id_latest,
        )


def _runtime_event_is_materialized(runtime_event: object, coverages: Iterable[_RuntimeCoverage]) -> bool:
    runtime_run_id = _str_or_none(_attr(runtime_event, "runtime_run_id"))
    if runtime_run_id is None:
        return False
    event_seq = _int_or_none(_attr(runtime_event, "event_seq"))
    event_id = _str_or_none(_attr(runtime_event, "event_id"))
    for coverage in coverages:
        if coverage.runtime_run_id != runtime_run_id:
            continue
        if event_id is not None and event_id == coverage.event_id_latest:
            return True
        if (
            event_seq is not None
            and coverage.event_seq_start is not None
            and coverage.event_seq_latest is not None
            and coverage.event_seq_start <= event_seq <= coverage.event_seq_latest
        ):
            return True
    return False


def _context_group(event: AgentWorkbenchTranscriptEventResponse) -> AgentWorkbenchTranscriptGroupResponse:
    return AgentWorkbenchTranscriptGroupResponse(
        groupId=f"context:{event.itemId}",
        title="上下文已压缩",
        status=event.status or "completed",
        startedAt=event.createdAt,
        completedAt=event.createdAt,
        events=[event],
    )


def _group_status(events: Iterable[AgentWorkbenchTranscriptEventResponse]) -> AgentWorkbenchStatus:
    statuses = {event.status for event in events}
    if "failed" in statuses:
        return "failed"
    if "running" in statuses:
        return "running"
    if "pending" in statuses:
        return "pending"
    if "cancelled" in statuses:
        return "cancelled"
    return "completed"


def _message_label(message: TranscriptMessage) -> str:
    return "User message" if message.role == "user" else "Agent message"


def _tool_kind(status: AgentWorkbenchStatus) -> AgentWorkbenchStreamKind:
    if status == "failed":
        return "tool.failed"
    if status == "running":
        return "tool.started"
    return "tool.completed"


def _runtime_event_kind(event_type: str) -> AgentWorkbenchStreamKind:
    if event_type in {"source_search_started", "source_search"}:
        return "sourceSearch.started"
    if event_type in {"source_search_completed", "source_results"}:
        return "sourceSearch.completed"
    if event_type == "source_search_failed":
        return "sourceSearch.failed"
    if event_type in {"command_started", "exec_started"}:
        return "command.started"
    if event_type in {"command_output", "exec_output"}:
        return "command.outputDelta"
    if event_type in {"command_completed", "exec_completed"}:
        return "command.completed"
    if event_type in {"command_failed", "exec_failed"}:
        return "command.failed"
    return "runtime.stageChanged"


def _runtime_payload_kind(kind: AgentWorkbenchStreamKind) -> Literal["source_search", "command", "runtime_stage"]:
    if kind.startswith("sourceSearch"):
        return "source_search"
    if kind.startswith("command"):
        return "command"
    return "runtime_stage"


def _event_kind_rank(kind: AgentWorkbenchStreamKind, role: str | None = None) -> int:
    if kind == "message.completed" and role == "user":
        return 10
    if kind == "message.completed":
        return 20
    if kind in {"tool.started", "sourceSearch.started", "webSearch.started", "command.started"}:
        return 30
    if kind in {"tool.outputDelta", "command.outputDelta"}:
        return 40
    if kind in {"tool.completed", "sourceSearch.completed", "webSearch.completed", "command.completed"}:
        return 50
    if kind in {"tool.failed", "sourceSearch.failed", "webSearch.failed", "command.failed"}:
        return 60
    if kind in {"activity.upserted", "runtime.stageChanged"}:
        return 70
    if kind == "context.compacted":
        return 80
    return 90


def _tool_summary(tool: AgentToolCallRecord) -> str | None:
    if tool.result is not None:
        summary = tool.result.get("summary")
        if isinstance(summary, str) and summary:
            return summary
    return tool.reason_code


def _attr(value: object, name: str) -> object:
    return getattr(value, name, None)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _status(value: str | None) -> AgentWorkbenchStatus:
    normalized = (value or "").casefold()
    if normalized in {"completed", "complete", "succeeded", "success", "approved"}:
        return "completed"
    if normalized in {"running", "started", "in_progress", "queued"}:
        return "running"
    if normalized in {"failed", "error", "rejected", "denied"}:
        return "failed"
    if normalized in {"cancelled", "canceled", "superseded"}:
        return "cancelled"
    return "pending"
