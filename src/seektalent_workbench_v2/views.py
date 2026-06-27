from __future__ import annotations

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
    node_specs: dict[str, dict[str, object]] = {}
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
            if event.type == "runtime_result" or event_type in {
                "runtime_finalization_completed",
                "runtime_run_completed",
            }:
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

        if event_type in {"runtime_round_query_ready", "runtime_search_started"} or stage == "round_query":
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-query",
                kind="phase",
                label=f"第 {round_no} 轮 · 查询包",
                summary=_keyword_query_from_payload(event.payload) or summary,
                roundNo=round_no,
                phase="round_query",
                stage="round_query",
                status=status,
                sourceKind="all",
            )
        elif event_type in {"runtime_round_source_dispatch", "runtime_round_source_result"} or stage in {
            "source_dispatch",
            "source_result",
        }:
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-source",
                kind="phase",
                label=f"第 {round_no} 轮 · 猎聘检索",
                summary=summary,
                roundNo=round_no,
                phase="source_result",
                stage=stage or "source_result",
                status=status,
                sourceKind=_source_kind_from_payload(event.payload),
            )
        elif event_type in {
            "runtime_scoring_started",
            "runtime_scoring_completed",
            "runtime_round_scoring_completed",
        } or stage == "scoring":
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-top-pool",
                kind="phase",
                label=f"第 {round_no} 轮 · Top Pool",
                summary=summary,
                roundNo=round_no,
                phase="scoring",
                stage="scoring",
                status=status,
                sourceKind="all",
            )
        elif event_type in {
            "runtime_round_feedback_completed",
            "runtime_reflection_completed",
            "runtime_round_completed",
        } or stage in {"feedback", "reflection"}:
            _upsert_graph_node(
                node_order,
                node_specs,
                f"v2-round-{round_no}-feedback",
                kind="phase",
                label=f"第 {round_no} 轮 · 下一轮策略",
                summary=_reflection_text_from_payload(event.payload) or summary,
                roundNo=round_no,
                phase="feedback",
                stage=stage or "feedback",
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
        nodes=[WorkbenchV2GraphNodeView(**node_specs[node_id]) for node_id in node_order],
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
                status=round_state["status"],
                cards=round_state["cards"],
            )
            for round_no, round_state in sorted(round_states.items())
        ],
    )


def _upsert_graph_node(
    node_order: list[str],
    node_specs: dict[str, dict[str, object]],
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
    node_specs[node_id] = {
        "nodeId": node_id,
        "kind": kind,
        "label": label,
        "summary": summary,
        "roundNo": roundNo,
        "laneType": None,
        "phase": phase,
        "stage": stage,
        "status": status,
        "sourceKind": sourceKind,
        "activityId": None,
        "messageId": None,
    }


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
) -> dict[int, dict[str, object]]:
    states: dict[int, dict[str, object]] = {}
    for event in _runtime_progress_events(events):
        if event.type != "runtime_progress":
            continue
        round_no = _positive_int_or_none(event.payload.get("roundNo"))
        if round_no is None:
            continue
        state = states.setdefault(round_no, {"status": "completed", "cards": {}})
        state["status"] = _surface_status_from_event_payload(event.payload)
        cards = state["cards"]
        if not isinstance(cards, dict):
            continue

        keyword_query = _keyword_query_from_payload(event.payload)
        if keyword_query is not None:
            cards["keywords"] = WorkbenchV2ThinkingProcessCardView(
                title="关键词",
                text=keyword_query,
                terms=_query_terms_from_payload(event.payload),
            )

        observation = _observation_text_from_payload(event.payload)
        if observation is not None:
            cards["observation"] = WorkbenchV2ThinkingProcessCardView(
                title="observation",
                text=observation,
                terms=[],
            )

        reflection = _reflection_text_from_payload(event.payload)
        if reflection is not None:
            cards["reflection"] = WorkbenchV2ThinkingProcessCardView(
                title="反思和下一轮变更",
                text=reflection,
                terms=[],
            )

    compact_states: dict[int, dict[str, object]] = {}
    for round_no, state in states.items():
        cards = state.get("cards")
        if not isinstance(cards, dict):
            continue
        ordered_cards = [
            cards[key]
            for key in ("keywords", "observation", "reflection")
            if isinstance(cards.get(key), WorkbenchV2ThinkingProcessCardView)
        ]
        compact_states[round_no] = {
            "status": state.get("status", "completed"),
            "cards": ordered_cards,
        }
    return compact_states


def _keyword_query_from_payload(payload: dict[str, object]) -> str | None:
    details = _runtime_details(payload)
    return _string_or_none(details.get("keywordQuery")) or _string_or_none(payload.get("keywordQuery"))


def _query_terms_from_payload(payload: dict[str, object]) -> list[str]:
    details = _runtime_details(payload)
    terms = _string_list(details.get("queryTerms"))
    if terms:
        return terms
    planned_queries = _list_or_empty(details.get("plannedQueries"))
    for query in planned_queries:
        query_record = _record_or_none(query)
        terms = _string_list((query_record or {}).get("queryTerms"))
        if terms:
            return terms
    return []


def _observation_text_from_payload(payload: dict[str, object]) -> str | None:
    details = _runtime_details(payload)
    return _string_or_none(details.get("resumeQualityComment"))


def _reflection_text_from_payload(payload: dict[str, object]) -> str | None:
    details = _runtime_details(payload)
    return _string_or_none(details.get("reflectionSummary")) or _string_or_none(details.get("reflectionRationale"))


def _runtime_details(payload: dict[str, object]) -> dict[str, object]:
    return _record_or_none(payload.get("details")) or {}


def _source_kind_from_payload(payload: dict[str, object]) -> str | None:
    return _string_or_none(payload.get("sourceKind")) or _string_or_none(payload.get("sourceId")) or "all"


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
    if status in {"failed", "blocked", "partial", "completed", "cancelled"}:
        return status
    if status == "running":
        return "running"
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
