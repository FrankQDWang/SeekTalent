from __future__ import annotations

from dataclasses import dataclass

from seektalent_workbench_v2.models import (
    WorkbenchV2Conversation,
    WorkbenchV2ConversationEventsView,
    WorkbenchV2ConversationListSummary,
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationPublic,
    WorkbenchV2ConversationRecord,
    WorkbenchV2ConversationView,
    WorkbenchV2GraphEdgeView,
    WorkbenchV2GraphNodeView,
    WorkbenchV2QueryGroupView,
    WorkbenchV2RuntimeView,
    WorkbenchV2StrategyGraphView,
    WorkbenchV2SurfaceStatus,
    WorkbenchV2ThinkingProcessCardView,
    WorkbenchV2ThinkingProcessRoundView,
    WorkbenchV2ThinkingProcessView,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventView,
)
from seektalent_workbench_v2.runtime_display import (
    normalize_runtime_progress_payload,
    normalize_runtime_result_payload,
)


_QUERY_GROUP_LIFECYCLE_BY_STAGE = {
    "round_query": "planned",
    "feedback": "executed",
}


@dataclass(frozen=True)
class _GraphNodeSpec:
    node_id: str
    kind: str
    label: str
    summary: str | None
    round_no: int | None
    phase: str | None
    stage: str | None
    status: WorkbenchV2SurfaceStatus
    source_kind: str | None

    def to_view(self) -> WorkbenchV2GraphNodeView:
        return WorkbenchV2GraphNodeView(
            nodeId=self.node_id,
            kind=self.kind,
            label=self.label,
            summary=self.summary,
            roundNo=self.round_no,
            laneType=None,
            phase=self.phase,
            stage=self.stage,
            status=self.status,
            sourceKind=self.source_kind,
            activityId=None,
            messageId=None,
        )


@dataclass(frozen=True)
class _ThinkingRoundState:
    status: WorkbenchV2SurfaceStatus
    query_groups: list[WorkbenchV2QueryGroupView]
    cards: list[WorkbenchV2ThinkingProcessCardView]


def conversation_to_public(conversation: WorkbenchV2Conversation) -> WorkbenchV2ConversationPublic:
    return WorkbenchV2ConversationPublic(
        conversationId=conversation.id,
        title=conversation.title,
        runtimeState=conversation.runtime_state,
        runtimeRunId=conversation.runtime_run_id,
        createdAt=conversation.created_at,
        updatedAt=conversation.updated_at,
    )


def conversation_to_list_summary(conversation: WorkbenchV2Conversation) -> WorkbenchV2ConversationListSummary:
    return WorkbenchV2ConversationListSummary(
        conversationId=conversation.id,
        title=conversation.title,
        status=conversation.runtime_state,
        updatedAt=conversation.updated_at,
    )


def conversation_to_runtime(conversation: WorkbenchV2Conversation) -> WorkbenchV2RuntimeView | None:
    if conversation.runtime_run_id is None:
        return None
    return WorkbenchV2RuntimeView(
        state=conversation.runtime_state,
        runtimeRunId=conversation.runtime_run_id,
    )


def conversation_record_to_view(record: WorkbenchV2ConversationRecord) -> WorkbenchV2ConversationView:
    transcript_events = _visible_transcript_events(record.events)
    requirement_form = _latest_requirement_form(record.events)
    return WorkbenchV2ConversationView(
        conversation=conversation_to_public(record.conversation),
        transcriptEvents=[event_to_view(event) for event in transcript_events],
        requirementForm=requirement_form,
        runtime=conversation_to_runtime(record.conversation),
        strategyGraph=_strategy_graph(record, transcript_events, requirement_form),
        thinkingProcess=_thinking_process(record, transcript_events, requirement_form),
        candidates=[],
    )


def conversation_events_to_view(
    record: WorkbenchV2ConversationRecord,
    *,
    after_step: int,
    limit: int,
) -> WorkbenchV2ConversationEventsView:
    transcript_events = _visible_transcript_events(record.events)
    latest_step = max((event.step for event in transcript_events), default=0)
    incremental_events = [event for event in transcript_events if event.step > after_step][:limit]
    return WorkbenchV2ConversationEventsView(
        conversationId=record.conversation.id,
        afterStep=after_step,
        latestStep=latest_step,
        events=[event_to_view(event) for event in incremental_events],
    )


def conversation_list_to_view(conversations: list[WorkbenchV2Conversation]) -> WorkbenchV2ConversationListView:
    return WorkbenchV2ConversationListView(
        conversations=[conversation_to_list_summary(conversation) for conversation in conversations]
    )


def event_to_view(event: WorkbenchV2TranscriptEvent) -> WorkbenchV2TranscriptEventView:
    return WorkbenchV2TranscriptEventView(
        eventId=event.id,
        step=event.step,
        type=event.type,
        role=event.role,
        status=event.status,
        payload=_event_payload_for_view(event),
        createdAt=event.created_at,
    )


def _visible_transcript_events(events: list[WorkbenchV2TranscriptEvent]) -> list[WorkbenchV2TranscriptEvent]:
    visible_events: list[WorkbenchV2TranscriptEvent] = []
    seen_terminal_runtime_keys: set[tuple[object, ...]] = set()
    for event in events:
        if event.type == "context_summary":
            continue
        view_event = _event_with_view_payload(event)
        runtime_key = _terminal_runtime_event_key(view_event)
        if runtime_key is not None:
            if runtime_key in seen_terminal_runtime_keys:
                continue
            seen_terminal_runtime_keys.add(runtime_key)
        visible_events.append(view_event)
    return visible_events


def _event_with_view_payload(event: WorkbenchV2TranscriptEvent) -> WorkbenchV2TranscriptEvent:
    payload = _event_payload_for_view(event)
    if payload == event.payload:
        return event
    return event.model_copy(update={"payload": payload})


def _event_payload_for_view(event: WorkbenchV2TranscriptEvent) -> dict[str, object]:
    if event.type == "runtime_progress":
        return normalize_runtime_progress_payload(event.payload)
    if event.type == "runtime_result":
        return normalize_runtime_result_payload(event.payload)
    return event.payload


def _terminal_runtime_event_key(event: WorkbenchV2TranscriptEvent) -> tuple[object, ...] | None:
    if event.type not in {"runtime_progress", "runtime_result"}:
        return None
    payload = event.payload
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary:
        return None
    if payload.get("state") != "completed" and payload.get("status") != "completed":
        return None
    runtime_run_id = payload.get("runtimeRunId")
    if event.type == "runtime_result":
        return ("runtime_result", runtime_run_id, summary)
    if payload.get("roundNo") is not None:
        return None
    return ("runtime_progress", runtime_run_id, summary)


def _latest_requirement_form(events: list[WorkbenchV2TranscriptEvent]) -> dict[str, object] | None:
    for event in reversed(events):
        if event.type == "requirement_form_confirmed":
            payload = dict(event.payload)
            payload["readonly"] = True
            return payload
        if event.type == "requirement_form":
            return dict(event.payload)
    return None


def _strategy_graph(
    record: WorkbenchV2ConversationRecord,
    events: list[WorkbenchV2TranscriptEvent],
    requirement_form: dict[str, object] | None,
) -> WorkbenchV2StrategyGraphView:
    if not _workflow_surface_active(record, events):
        return WorkbenchV2StrategyGraphView()

    requirement_summary = _requirement_summary(record, requirement_form)
    node_order: list[str] = []
    node_specs: dict[str, _GraphNodeSpec] = {}
    _upsert_graph_node(
        node_order,
        node_specs,
        "v2-requirements",
        kind="requirements",
        label="需求拆解",
        summary=requirement_summary,
        roundNo=None,
        phase=None,
        stage=None,
        status="completed",
        sourceKind="all",
    )

    for event in _runtime_progress_events(events):
        event_type = _string_or_none(event.payload.get("runtimeEventType"))
        stage = _string_or_none(event.payload.get("stage"))
        round_no = _positive_int_or_none(event.payload.get("roundNo"))
        summary = _string_or_none(event.payload.get("summary")) or "运行进度已更新。"
        status = _surface_status_from_event_payload(event.payload)
        if round_no is None:
            if _is_final_event(event, event_type):
                _upsert_graph_node(
                    node_order,
                    node_specs,
                    "v2-final-summary",
                    kind="final",
                    label="最终短名单",
                    summary=summary,
                    roundNo=None,
                    phase="final_summary",
                    stage=stage or "final_summary",
                    status=status,
                    sourceKind="all",
                )
            continue

        if _is_graph_query_event(event_type, stage):
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-query",
                kind="phase",
                label=f"第 {round_no} 轮 · 查询包",
                summary=_keyword_query_from_payload(event.payload) or summary,
                roundNo=round_no,
                phase="query",
                stage="round_query",
                status=status,
                sourceKind="all",
            )
        if _is_graph_source_event(event_type, stage):
            source_kind = _graph_source_kind(event.payload)
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-source-{source_kind}",
                kind="phase",
                label=f"第 {round_no} 轮 · {_source_graph_title(source_kind)}检索",
                summary=summary,
                roundNo=round_no,
                phase="source",
                stage="source_result",
                status=status,
                sourceKind=source_kind,
            )
        if _is_graph_merge_event(event_type, stage):
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-merge",
                kind="phase",
                label=f"第 {round_no} 轮 · 去重合并",
                summary=summary,
                roundNo=round_no,
                phase="merge",
                stage="merge",
                status=status,
                sourceKind="all",
            )
        if _is_graph_scoring_event(event_type, stage):
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-top-pool",
                kind="phase",
                label=f"第 {round_no} 轮 · Top Pool",
                summary=summary,
                roundNo=round_no,
                phase="top_pool",
                stage="scoring",
                status=status,
                sourceKind="all",
            )
        if _is_graph_feedback_event(event_type, stage):
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-next-strategy",
                kind="phase",
                label=f"第 {round_no} 轮 · 下一轮策略",
                summary=_reflection_text_from_payload(event.payload) or summary,
                roundNo=round_no,
                phase="reflection",
                stage="feedback",
                status=status,
                sourceKind="all",
            )

    edges = [
        WorkbenchV2GraphEdgeView(
            edgeId=f"{left}->{right}",
            fromNodeId=left,
            toNodeId=right,
        )
        for left, right in zip(node_order, node_order[1:], strict=False)
    ]
    return WorkbenchV2StrategyGraphView(
        nodes=[node_specs[node_id].to_view() for node_id in node_order],
        edges=edges,
    )


def _thinking_process(
    record: WorkbenchV2ConversationRecord,
    events: list[WorkbenchV2TranscriptEvent],
    requirement_form: dict[str, object] | None,
) -> WorkbenchV2ThinkingProcessView:
    if not _workflow_surface_active(record, events):
        return WorkbenchV2ThinkingProcessView()

    round_states = _runtime_thinking_round_states(events)
    if not round_states:
        return WorkbenchV2ThinkingProcessView()

    status = _surface_status(record.conversation.runtime_state)
    active_round = max(round_states) if status in {"pending", "running"} else None
    return WorkbenchV2ThinkingProcessView(
        activeRoundNo=active_round,
        rounds=[
            WorkbenchV2ThinkingProcessRoundView(
                roundNo=round_no,
                status=round_state.status,
                queryGroups=round_state.query_groups,
                cards=round_state.cards,
            )
            for round_no, round_state in sorted(round_states.items())
        ],
    )


def _upsert_graph_node(
    node_order: list[str],
    node_specs: dict[str, _GraphNodeSpec],
    node_id: str,
    *,
    kind: str,
    label: str,
    summary: str | None,
    roundNo: int | None,
    phase: str | None,
    stage: str | None,
    status: WorkbenchV2SurfaceStatus,
    sourceKind: str | None,
) -> None:
    if node_id not in node_specs:
        node_order.append(node_id)
    node_specs[node_id] = _GraphNodeSpec(
        node_id=node_id,
        kind=kind,
        label=label,
        summary=summary,
        round_no=roundNo,
        phase=phase,
        stage=stage,
        status=status,
        source_kind=sourceKind,
    )


def _runtime_progress_events(events: list[WorkbenchV2TranscriptEvent]) -> list[WorkbenchV2TranscriptEvent]:
    runtime_events = [event for event in events if event.type in {"runtime_progress", "runtime_result"}]
    return sorted(runtime_events, key=lambda event: (_runtime_event_seq(event), event.step))


def _runtime_event_seq(event: WorkbenchV2TranscriptEvent) -> int:
    seq = event.payload.get("runtimeEventSeq")
    if isinstance(seq, int):
        return seq
    return event.step + 1_000_000


def _runtime_thinking_round_states(
    events: list[WorkbenchV2TranscriptEvent],
) -> dict[int, _ThinkingRoundState]:
    statuses: dict[int, WorkbenchV2SurfaceStatus] = {}
    query_group_states: dict[int, dict[str, WorkbenchV2QueryGroupView]] = {}
    card_states: dict[int, dict[str, WorkbenchV2ThinkingProcessCardView]] = {}
    for event in _runtime_progress_events(events):
        if event.type != "runtime_progress":
            continue
        round_no = _positive_int_or_none(event.payload.get("roundNo"))
        if round_no is None:
            continue
        event_type = _string_or_none(event.payload.get("runtimeEventType"))
        stage = _string_or_none(event.payload.get("stage"))
        status = _surface_status_from_event_payload(event.payload)

        query_groups = _query_groups_from_payload(
            event.payload,
            expected_lifecycle=_query_group_lifecycle_for_stage(stage),
        )
        if query_groups:
            statuses[round_no] = status
            groups = query_group_states.setdefault(round_no, {})
            for group in query_groups:
                _merge_query_group(groups, group)

        if _is_observation_event(event_type, stage) and (observation := _observation_text_from_payload(event.payload)):
            statuses[round_no] = status
            cards = card_states.setdefault(round_no, {})
            cards["observation"] = WorkbenchV2ThinkingProcessCardView(
                title="observation",
                text=observation,
                terms=[],
            )

        if _is_reflection_event(event_type, stage) and (reflection := _reflection_text_from_payload(event.payload)):
            statuses[round_no] = status
            cards = card_states.setdefault(round_no, {})
            cards["reflection"] = WorkbenchV2ThinkingProcessCardView(
                title="反思和下一轮变更",
                text=reflection,
                terms=[],
            )

    compact_states: dict[int, _ThinkingRoundState] = {}
    for round_no in sorted(set(query_group_states) | set(card_states)):
        cards = card_states.get(round_no, {})
        ordered_cards = [card for key in ("observation", "reflection") if (card := cards.get(key)) is not None]
        compact_states[round_no] = _ThinkingRoundState(
            status=statuses.get(round_no, "completed"),
            query_groups=list(query_group_states.get(round_no, {}).values()),
            cards=ordered_cards,
        )
    return compact_states


def _keyword_query_from_payload(payload: dict[str, object]) -> str | None:
    groups = _query_groups_from_payload(
        payload,
        expected_lifecycle=_query_group_lifecycle_for_stage(_string_or_none(payload.get("stage"))),
    )
    return groups[0].keywordQuery if groups else None


def _query_groups_from_payload(
    payload: dict[str, object],
    *,
    expected_lifecycle: str | None,
) -> list[WorkbenchV2QueryGroupView]:
    if expected_lifecycle is None:
        return []
    raw_groups = _runtime_details(payload).get("queryGroups")
    if not isinstance(raw_groups, list):
        return []
    groups: list[WorkbenchV2QueryGroupView] = []
    seen_query_instance_ids: set[str] = set()
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        group = WorkbenchV2QueryGroupView.model_validate(raw_group)
        if group.lifecycle != expected_lifecycle:
            continue
        if group.queryInstanceId in seen_query_instance_ids:
            continue
        seen_query_instance_ids.add(group.queryInstanceId)
        groups.append(group)
        if len(groups) >= 2:
            break
    return groups


def _query_group_lifecycle_for_stage(stage: str | None) -> str | None:
    return _QUERY_GROUP_LIFECYCLE_BY_STAGE.get(stage) if stage is not None else None


def _merge_query_group(
    groups: dict[str, WorkbenchV2QueryGroupView],
    incoming: WorkbenchV2QueryGroupView,
) -> None:
    existing = groups.get(incoming.queryInstanceId)
    if existing is None:
        groups[incoming.queryInstanceId] = incoming
        return
    if _query_group_identity(existing) != _query_group_identity(incoming):
        raise ValueError("workbench_v2_query_group_identity_mismatch")
    if existing.lifecycle == "executed" and incoming.lifecycle == "planned":
        return
    groups[incoming.queryInstanceId] = incoming


def _query_group_identity(group: WorkbenchV2QueryGroupView) -> tuple[object, ...]:
    return (
        group.termGroupKey,
        group.queryRole,
        group.laneType,
        tuple(group.queryTerms),
        group.keywordQuery,
    )


def _observation_text_from_payload(payload: dict[str, object]) -> str | None:
    details = _runtime_details(payload)
    return _string_or_none(details.get("resumeQualityComment"))


def _reflection_text_from_payload(payload: dict[str, object]) -> str | None:
    details = _runtime_details(payload)
    return _string_or_none(details.get("reflectionSummary"))


def _is_final_event(event: WorkbenchV2TranscriptEvent, event_type: str | None) -> bool:
    return event.type == "runtime_result" or event_type in {
        "runtime_finalization_completed",
        "runtime_run_completed",
    }


def _is_graph_query_event(event_type: str | None, stage: str | None) -> bool:
    return event_type == "runtime_round_query_ready" or stage == "round_query"


def _is_graph_source_event(event_type: str | None, stage: str | None) -> bool:
    return event_type in {
        "runtime_round_source_dispatch",
        "runtime_round_source_result",
    } or stage in {"source_dispatch", "source_result"}


def _is_graph_merge_event(event_type: str | None, stage: str | None) -> bool:
    return event_type == "runtime_round_merge_completed" or stage == "merge"


def _is_graph_scoring_event(event_type: str | None, stage: str | None) -> bool:
    return event_type == "runtime_round_scoring_completed" or stage == "scoring"


def _is_graph_feedback_event(event_type: str | None, stage: str | None) -> bool:
    return event_type == "runtime_round_feedback_completed" or stage in {"feedback", "reflection"}


def _graph_source_kind(payload: dict[str, object]) -> str:
    source_kind = _string_or_none(payload.get("sourceKind")) or _string_or_none(payload.get("sourceId"))
    if source_kind in {"liepin", "cts"}:
        return source_kind
    return "all"


def _source_graph_title(source_kind: str | None) -> str:
    if source_kind == "liepin":
        return "猎聘"
    if source_kind == "cts":
        return "CTS"
    return ""


def _is_observation_event(event_type: str | None, stage: str | None) -> bool:
    return event_type in {
        "runtime_round_scoring_completed",
        "runtime_round_feedback_completed",
    } or stage in {"scoring", "feedback"}


def _is_reflection_event(event_type: str | None, stage: str | None) -> bool:
    return event_type in {
        "runtime_round_feedback_completed",
        "runtime_reflection_completed",
    } or stage in {"feedback", "reflection"}


def _runtime_details(payload: dict[str, object]) -> dict[str, object]:
    return _record_or_none(payload.get("details")) or {}


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _string_or_none(item)
        if text is not None and text not in items:
            items.append(text)
    return items


def _runtime_round_summaries(
    events: list[WorkbenchV2TranscriptEvent],
) -> dict[int, tuple[str, WorkbenchV2SurfaceStatus]]:
    rounds: dict[int, tuple[str, WorkbenchV2SurfaceStatus]] = {}
    for event in events:
        if event.type != "runtime_progress":
            continue
        round_no = event.payload.get("roundNo")
        summary = _string_or_none(event.payload.get("summary"))
        if not isinstance(round_no, int) or round_no <= 0 or summary is None:
            continue
        rounds[round_no] = (summary, _surface_status_from_event_payload(event.payload))
    return rounds


def _surface_status_from_event_payload(payload: dict[str, object]) -> WorkbenchV2SurfaceStatus:
    status = payload.get("status")
    if status == "running":
        return "running"
    if status == "failed":
        return "failed"
    if status == "blocked":
        return "blocked"
    if status == "partial":
        return "partial"
    if status == "cancelled":
        return "cancelled"
    return "completed"


def _workflow_surface_active(record: WorkbenchV2ConversationRecord, events: list[WorkbenchV2TranscriptEvent]) -> bool:
    return (
        record.conversation.runtime_run_id is not None
        or record.conversation.runtime_state != "idle"
        or any(event.type == "requirement_form_confirmed" for event in events)
    )


def _surface_status(state: str) -> WorkbenchV2SurfaceStatus:
    if state == "running":
        return "running"
    if state == "completed":
        return "completed"
    if state == "failed":
        return "failed"
    if state == "cancelled":
        return "cancelled"
    return "pending"


def _requirement_summary(
    record: WorkbenchV2ConversationRecord,
    requirement_form: dict[str, object] | None,
) -> str:
    runtime_input = _record_or_none((requirement_form or {}).get("runtimeInput"))
    job_title = _string_or_none((runtime_input or {}).get("jobTitle"))
    return job_title or record.conversation.title or "需求已确认。"


def _latest_runtime_summary(events: list[WorkbenchV2TranscriptEvent]) -> str | None:
    for event in reversed(events):
        if event.type not in {"runtime_progress", "runtime_result"}:
            continue
        summary = _string_or_none(event.payload.get("summary"))
        if summary and summary != "当前还没有运行结果。":
            return summary
    return None


def _selected_requirement_terms(requirement_form: dict[str, object] | None) -> list[str]:
    draft = _record_or_none((requirement_form or {}).get("draft"))
    sections = _list_or_empty((draft or {}).get("sections"))
    terms: list[str] = []
    for section in sections:
        section_record = _record_or_none(section)
        for item in _list_or_empty((section_record or {}).get("items")):
            item_record = _record_or_none(item)
            if item_record is None or item_record.get("selected") is not True:
                continue
            if item_record.get("status") == "deleted":
                continue
            text = _string_or_none(item_record.get("text"))
            if text:
                terms.append(text)
    return terms[:8]


def _record_or_none(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _list_or_empty(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
