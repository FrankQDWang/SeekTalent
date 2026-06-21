from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Literal, cast

from pydantic import ValidationError

from seektalent_conversation_agent.models import TranscriptActivityItem, TranscriptMessage
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchActivityPayloadResponse,
    AgentWorkbenchActivityResponse,
    AgentWorkbenchConversationResponse,
    AgentWorkbenchConversationSummaryResponse,
    AgentWorkbenchGraphEdgeResponse,
    AgentWorkbenchGraphNodeResponse,
    AgentWorkbenchLinkedRuntimeRunResponse,
    AgentWorkbenchMessagePayloadResponse,
    AgentWorkbenchMessageResponse,
    AgentWorkbenchPendingActionsResponse,
    AgentWorkbenchRequirementDraftItemResponse,
    AgentWorkbenchRequirementDraftResponse,
    AgentWorkbenchRequirementDraftSectionResponse,
    AgentWorkbenchRequirementDraftStatus,
    AgentWorkbenchRequirementItemStatus,
    AgentWorkbenchRuntimeResponse,
    AgentWorkbenchStatus,
    AgentWorkbenchStrategyGraphResponse,
    AgentWorkbenchStreamCursorResponse,
    AgentWorkbenchThinkingProcessCardResponse,
    AgentWorkbenchThinkingProcessResponse,
    AgentWorkbenchThinkingProcessRoundResponse,
)
from seektalent_ui.agent_workbench_projection import (
    AgentWorkbenchProjectionInput,
    AgentWorkbenchWorkflowStartIntentProjection,
)
from seektalent_ui.agent_workbench_transcript import build_transcript_groups
from seektalent_ui.workbench_observability import (
    record_requirement_snapshot_invalid,
    record_workbench_payload_bytes,
)


MAX_WORKBENCH_MESSAGES = 100
MAX_WORKBENCH_ACTIVITIES = 100
MAX_WORKBENCH_TOOL_CALLS = 100
MAX_WORKBENCH_CONTEXT_COMPACTIONS = 20
MAX_WORKBENCH_RUNTIME_EVENTS = 300
MAX_WORKBENCH_GRAPH_NODES = 80
MAX_WORKBENCH_GRAPH_EDGES = 120
MAX_WORKBENCH_THINKING_ROUNDS = 50
MAX_WORKBENCH_CANDIDATES = 10
MAX_WORKBENCH_DETAIL_APPROVALS = 50
MAX_WORKBENCH_REVIEW_ARTIFACTS = 20
MAX_WORKBENCH_SOURCE_CONNECTIONS = 20
MAX_WORKBENCH_LINKED_RUNTIME_RUNS = 20


def project_agent_workbench_view(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchConversationResponse:
    state = input.conversation_reopen_state
    bounded_input = _bounded_projection_input(input)
    messages = _latest(input.messages, MAX_WORKBENCH_MESSAGES)
    activities = [_activity_response(activity) for activity in bounded_input.activity_items]
    reason_code = state.reason_code
    runtime_run_id = _workflow_runtime_run_id(state.runtime_run_id, input.workflow_start_intent)
    if input.requirement_draft_missing and reason_code is None:
        reason_code = "runtime_projection_unavailable"
    response = AgentWorkbenchConversationResponse(
        conversation=AgentWorkbenchConversationSummaryResponse(
            conversationId=state.conversation_id,
            title=state.title,
            status=state.status,
            isArchived=state.is_archived,
            runtimeRunId=runtime_run_id,
            workbenchSessionId=state.workbench_session_id,
            workflowStartIntentId=_workflow_start_intent_id(input),
            workflowStartState=_workflow_start_state(
                runtime_run_id=runtime_run_id,
                linked_runtime_runs=state.linked_runtime_runs,
                workflow_start_intent=input.workflow_start_intent,
            ),
            workflowStartReasonCode=_workflow_start_reason_code(input.workflow_start_intent),
            linkedRuntimeRuns=[
                AgentWorkbenchLinkedRuntimeRunResponse(
                    runtimeRunId=link.runtime_run_id,
                    status=link.status,
                    runKind=link.run_kind,
                    workbenchSessionId=link.workbench_session_id,
                    approvedRequirementRevisionId=link.approved_requirement_revision_id,
                    runIntentId=link.run_intent_id,
                    linkReason=link.link_reason,
                    latestEventSeq=link.latest_event_seq,
                    linkedAt=link.linked_at,
                    updatedAt=link.updated_at,
                    activeAt=link.active_at,
                    supersededAt=link.superseded_at,
                    completedAt=link.completed_at,
                    isActive=link.is_active,
                )
                for link in _latest(state.linked_runtime_runs, MAX_WORKBENCH_LINKED_RUNTIME_RUNS)
            ],
            updatedAt=state.last_opened_at,
        ),
        messages=[_message_response(message) for message in messages],
        activities=activities,
        transcriptGroups=build_transcript_groups(bounded_input),
        requirementDraft=_requirement_draft(input),
        runtime=input.runtime or _runtime_from_state(input),
        strategyGraph=_strategy_graph(bounded_input.activity_items),
        thinkingProcess=_thinking_process(bounded_input),
        sourceConnections=list(_latest(input.source_connections, MAX_WORKBENCH_SOURCE_CONNECTIONS)),
        candidates=list(input.candidates[:MAX_WORKBENCH_CANDIDATES]),
        detailApprovals=list(_latest(input.detail_approvals, MAX_WORKBENCH_DETAIL_APPROVALS)),
        reviewArtifacts=list(_latest(input.review_artifacts, MAX_WORKBENCH_REVIEW_ARTIFACTS)),
        finalSummary=input.final_summary,
        pendingActions=AgentWorkbenchPendingActionsResponse(
            primary=state.pending_user_action,
            allowed=state.allowed_actions,
            pendingCommandCount=state.pending_command_count,
            pendingRequirementReviewCount=state.pending_requirement_review_count,
            pendingMemoryReviewCount=state.pending_memory_review_count,
        ),
        streamCursor=AgentWorkbenchStreamCursorResponse(
            latestMessageSeq=state.latest_message_seq,
            latestActivitySeq=state.latest_activity_seq,
            latestRuntimeEventSeq=state.latest_rendered_runtime_event_seq,
        ),
        reasonCode=reason_code,
    )
    record_workbench_payload_bytes(len(response.model_dump_json()))
    return response


def project_agent_workbench_conversation_summary(
    state: object,
    *,
    workflow_start_intent: AgentWorkbenchWorkflowStartIntentProjection | None = None,
) -> AgentWorkbenchConversationSummaryResponse:
    runtime_run_id = _workflow_runtime_run_id(
        cast(str | None, getattr(state, "runtime_run_id", None)),
        workflow_start_intent,
    )
    return AgentWorkbenchConversationSummaryResponse(
        conversationId=str(getattr(state, "conversation_id")),
        title=str(getattr(state, "title")),
        status=str(getattr(state, "status")),
        isArchived=bool(getattr(state, "is_archived")),
        runtimeRunId=runtime_run_id,
        workbenchSessionId=cast(str | None, getattr(state, "workbench_session_id", None)),
        workflowStartIntentId=_workflow_start_intent_id_from_state(state, workflow_start_intent),
        workflowStartState=_workflow_start_state(
            runtime_run_id=runtime_run_id,
            linked_runtime_runs=(),
            workflow_start_intent=workflow_start_intent,
        ),
        workflowStartReasonCode=_workflow_start_reason_code(workflow_start_intent),
        updatedAt=cast(str | None, getattr(state, "updated_at", None)),
    )


def _workflow_start_intent_id(input: AgentWorkbenchProjectionInput) -> str | None:
    return _workflow_start_intent_id_from_state(input.conversation_reopen_state, input.workflow_start_intent)


def _workflow_start_intent_id_from_state(
    state: object,
    workflow_start_intent: AgentWorkbenchWorkflowStartIntentProjection | None,
) -> str | None:
    if workflow_start_intent is not None:
        return workflow_start_intent.workflow_start_intent_id
    return cast(str | None, getattr(state, "workflow_start_intent_id", None))


def _workflow_start_state(
    *,
    runtime_run_id: str | None,
    linked_runtime_runs: Sequence[object],
    workflow_start_intent: AgentWorkbenchWorkflowStartIntentProjection | None,
) -> Literal["not_started", "queued", "starting", "running", "failed"]:
    intent = workflow_start_intent
    if runtime_run_id is not None or any(bool(getattr(link, "is_active", False)) for link in linked_runtime_runs):
        return "running"
    if intent is None:
        return "not_started"
    if intent.status == "pending":
        return "queued"
    if intent.status == "started":
        return "running" if intent.runtime_run_id is not None else "starting"
    if intent.status in {"failed", "cancelled"}:
        return "failed"
    return "not_started"


def _workflow_runtime_run_id(
    state_runtime_run_id: str | None,
    workflow_start_intent: AgentWorkbenchWorkflowStartIntentProjection | None,
) -> str | None:
    if state_runtime_run_id is not None:
        return state_runtime_run_id
    if workflow_start_intent is not None and workflow_start_intent.status == "started":
        return workflow_start_intent.runtime_run_id
    return None


def _workflow_start_reason_code(workflow_start_intent: AgentWorkbenchWorkflowStartIntentProjection | None) -> str | None:
    return workflow_start_intent.reason_code if workflow_start_intent is not None else None


def _bounded_projection_input(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchProjectionInput:
    return replace(
        input,
        messages=tuple(_latest(input.messages, MAX_WORKBENCH_MESSAGES)),
        activity_items=tuple(_latest(input.activity_items, MAX_WORKBENCH_ACTIVITIES)),
        tool_call_records=tuple(_latest(input.tool_call_records, MAX_WORKBENCH_TOOL_CALLS)),
        context_compactions=tuple(_latest(input.context_compactions, MAX_WORKBENCH_CONTEXT_COMPACTIONS)),
        runtime_events=tuple(_latest(input.runtime_events, MAX_WORKBENCH_RUNTIME_EVENTS)),
    )


def _message_response(message: TranscriptMessage) -> AgentWorkbenchMessageResponse:
    return AgentWorkbenchMessageResponse(
        messageId=message.message_id,
        seq=message.message_seq,
        role=_message_role(message.role),
        messageType=message.message_type,
        text=message.text,
        payload=_message_payload(message),
        createdAt=message.created_at,
    )


def _activity_response(activity: TranscriptActivityItem) -> AgentWorkbenchActivityResponse:
    return AgentWorkbenchActivityResponse(
        activityId=activity.activity_id,
        seq=activity.activity_seq,
        activityType=activity.activity_type,
        status=activity.status,
        title=activity.title,
        summary=activity.summary,
        sourceRuntimeRunId=activity.source_runtime_run_id,
        payload=_activity_payload(activity),
        updatedAt=activity.updated_at,
    )


AgentWorkbenchMessageRole = Literal["user", "assistant", "system"]


def _message_role(role: str) -> AgentWorkbenchMessageRole:
    if role == "user":
        return "user"
    if role == "assistant":
        return "assistant"
    return "system"


def _message_payload(message: TranscriptMessage) -> AgentWorkbenchMessagePayloadResponse:
    payload = message.payload
    if message.message_type == "user_text":
        job_title = _str_or_none(payload.get("jobTitle")) or _str_or_none(payload.get("job_title"))
        return AgentWorkbenchMessagePayloadResponse(
            kind="job_request",
            jobTitle=job_title,
            notes=_str_or_none(payload.get("notes")),
            sourceKinds=_source_kinds(payload.get("sourceKinds") or payload.get("source_kinds")),
        )
    if message.message_type == "requirement_review":
        draft = payload.get("requirementDraft")
        draft_id = _str_or_none(_mapping_get(draft, "draftRevisionId")) or _str_or_none(
            _mapping_get(draft, "draft_revision_id")
        )
        snapshot = _requirement_snapshot_payload(payload.get("requirementDraftSnapshot"))
        return AgentWorkbenchMessagePayloadResponse(
            kind="requirement_review",
            requirementDraftId=draft_id,
            requirementDraftSnapshot=snapshot,
        )
    return AgentWorkbenchMessagePayloadResponse()


def _activity_payload(activity: TranscriptActivityItem) -> AgentWorkbenchActivityPayloadResponse:
    payload = activity.payload
    return AgentWorkbenchActivityPayloadResponse(
        kind="runtime_round" if _int_or_none(payload.get("round_no")) is not None else "runtime_event",
        stage=_str_or_none(payload.get("stage")),
        sourceId=_str_or_none(payload.get("source_id")) or _str_or_none(payload.get("sourceId")),
        status=_status(activity.status),
        roundNo=_int_or_none(payload.get("round_no")) or _int_or_none(payload.get("roundNo")),
        queryTerms=_string_list(payload.get("query_terms")) or _string_list(payload.get("queryTerms")),
        keywordQuery=_str_or_none(payload.get("keyword_query")) or _str_or_none(payload.get("keywordQuery")),
        executedQueryTerms=_executed_query_terms(payload.get("executed_queries") or payload.get("executedQueries")),
        rawCandidateCount=_int_or_none(payload.get("raw_candidate_count")) or _int_or_none(payload.get("rawCandidateCount")),
        uniqueNewCount=_int_or_none(payload.get("unique_new_count")) or _int_or_none(payload.get("uniqueNewCount")),
        newlyScoredCount=_int_or_none(payload.get("newly_scored_count")) or _int_or_none(payload.get("newlyScoredCount")),
        resumeQualityComment=_str_or_none(payload.get("resume_quality_comment")) or _str_or_none(payload.get("resumeQualityComment")),
        reflectionSummary=_str_or_none(payload.get("reflection_summary")) or _str_or_none(payload.get("reflectionSummary")),
        reflectionRationale=_str_or_none(payload.get("reflection_rationale")) or _str_or_none(payload.get("reflectionRationale")),
        suggestedActivateTerms=_string_list(payload.get("suggested_activate_terms"))
        or _string_list(payload.get("suggestedActivateTerms")),
        suggestedKeepTerms=_string_list(payload.get("suggested_keep_terms")) or _string_list(payload.get("suggestedKeepTerms")),
        suggestedDeprioritizeTerms=_string_list(payload.get("suggested_deprioritize_terms"))
        or _string_list(payload.get("suggestedDeprioritizeTerms")),
        suggestedDropTerms=_string_list(payload.get("suggested_drop_terms")) or _string_list(payload.get("suggestedDropTerms")),
    )


def _requirement_draft(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchRequirementDraftResponse | None:
    if input.requirement_draft_missing:
        return None
    draft = input.requirement_draft
    if draft is None:
        return None
    return AgentWorkbenchRequirementDraftResponse(
        draftRevisionId=draft.draft_revision_id,
        parentDraftRevisionId=draft.base_revision_id,
        status=_requirement_draft_status(draft.status),
        title="需求确认",
        summary=_requirement_summary(draft),
        canConfirm=draft.can_confirm,
        unresolvedReviewItemCount=draft.unresolved_review_item_count,
        sections=[
            AgentWorkbenchRequirementDraftSectionResponse(
                sectionId=section.section_id,
                displayName=section.display_name,
                backendField=section.backend_field,
                items=[
                    AgentWorkbenchRequirementDraftItemResponse(
                        itemId=item.item_id,
                        sectionId=section.section_id,
                        selected=item.selected,
                        enabled=item.enabled,
                        editable=item.editable,
                        text=item.text,
                        status=_requirement_item_status(item.status),
                        source=item.source,
                        allowedActions=list(item.allowed_actions),
                    )
                    for item in section.items
                ],
            )
            for section in draft.sections
        ],
    )


def _requirement_summary(draft: object) -> str:
    sections = getattr(draft, "sections", ())
    selected_count = sum(
        1
        for section in sections
        for item in getattr(section, "items", ())
        if getattr(item, "selected", False) and getattr(item, "status", "") == "resolved"
    )
    return f"已生成 {selected_count} 条已选择需求，请确认后启动检索。"


def _requirement_item_status(status: object) -> AgentWorkbenchRequirementItemStatus:
    if status in {"resolved", "needs_review", "deleted", "moved", "rejected"}:
        return cast(AgentWorkbenchRequirementItemStatus, status)
    return "unknown"


def _requirement_draft_status(status: object) -> AgentWorkbenchRequirementDraftStatus:
    if status in {"draft_ready", "needs_review"}:
        return cast(AgentWorkbenchRequirementDraftStatus, status)
    return "unknown"


def _requirement_snapshot_payload(value: object) -> AgentWorkbenchRequirementDraftResponse | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return AgentWorkbenchRequirementDraftResponse.model_validate(value)
    except ValidationError as exc:
        record_requirement_snapshot_invalid(error_count=len(exc.errors()))
        return None


def _runtime_from_state(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchRuntimeResponse | None:
    state = input.conversation_reopen_state
    if state.runtime_run_id is None:
        return None
    latest_activity = next((activity for activity in reversed(input.activity_items) if activity.source_runtime_run_id), None)
    current_stage = _str_or_none(latest_activity.payload.get("stage")) if latest_activity is not None else None
    current_round = _int_or_none(latest_activity.payload.get("round_no")) if latest_activity is not None else None
    return AgentWorkbenchRuntimeResponse(
        runtimeRunId=state.runtime_run_id,
        status=state.status,
        currentStage=current_stage or "unknown",
        currentRound=current_round,
        latestEventSeq=state.latest_rendered_runtime_event_seq,
    )


def _strategy_graph(activity_items: Sequence[TranscriptActivityItem]) -> AgentWorkbenchStrategyGraphResponse:
    activity_items = _latest(activity_items, MAX_WORKBENCH_GRAPH_NODES - 1)
    nodes = [
        AgentWorkbenchGraphNodeResponse(
            nodeId="requirements",
            kind="requirements",
            label="Requirements",
            summary="Confirmed or draft hiring requirement.",
            status="completed",
        )
    ]
    edges: list[AgentWorkbenchGraphEdgeResponse] = []
    previous_node_id = "requirements"
    for activity in activity_items:
        node_id = activity.activity_id
        nodes.append(
            AgentWorkbenchGraphNodeResponse(
                nodeId=node_id,
                kind="activity",
                label=activity.title,
                summary=activity.summary,
                status=activity.status,
                activityId=activity.activity_id,
            )
        )
        edges.append(
            AgentWorkbenchGraphEdgeResponse(
                edgeId=f"{previous_node_id}->{node_id}",
                fromNodeId=previous_node_id,
                toNodeId=node_id,
            )
        )
        previous_node_id = node_id
    return AgentWorkbenchStrategyGraphResponse(
        nodes=nodes[:MAX_WORKBENCH_GRAPH_NODES],
        edges=edges[:MAX_WORKBENCH_GRAPH_EDGES],
    )


def _thinking_process(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchThinkingProcessResponse:
    rounds: list[AgentWorkbenchThinkingProcessRoundResponse] = []
    for payload, status in _thinking_round_payloads(input):
        if payload.roundNo is None:
            continue
        cards = [
            AgentWorkbenchThinkingProcessCardResponse(
                title="关键词",
                text=payload.keywordQuery or "No query has been projected yet.",
                terms=payload.queryTerms,
            ),
            AgentWorkbenchThinkingProcessCardResponse(
                title="observation",
                text=_observation_text(payload),
            ),
            AgentWorkbenchThinkingProcessCardResponse(
                title="反思和下一轮变更",
                text=_reflection_text(payload),
                terms=[
                    *payload.suggestedActivateTerms,
                    *payload.suggestedKeepTerms,
                    *payload.suggestedDeprioritizeTerms,
                    *payload.suggestedDropTerms,
                ],
            ),
        ]
        rounds.append(
            AgentWorkbenchThinkingProcessRoundResponse(
                roundNo=payload.roundNo,
                status=_status(status),
                cards=cards,
            )
        )
    rounds = _latest(rounds, MAX_WORKBENCH_THINKING_ROUNDS)
    active_round = rounds[-1].roundNo if rounds else None
    return AgentWorkbenchThinkingProcessResponse(activeRoundNo=active_round, rounds=rounds)


def _thinking_round_payloads(
    input: AgentWorkbenchProjectionInput,
) -> list[tuple[AgentWorkbenchActivityPayloadResponse, str | None]]:
    runtime_payloads: list[tuple[AgentWorkbenchActivityPayloadResponse, str | None]] = []
    for event in input.runtime_events:
        if _int_or_none(_attr(event, "round_no")) is None:
            continue
        status = _str_or_none(_attr(event, "status"))
        payload = {
            **_mapping_or_empty(_attr(event, "payload")),
            "stage": _str_or_none(_attr(event, "stage")),
            "source_id": _str_or_none(_attr(event, "source_id")),
            "round_no": _int_or_none(_attr(event, "round_no")),
        }
        runtime_payloads.append((_activity_payload_from_mapping(payload, status=status), status))
    if runtime_payloads:
        return runtime_payloads
    return [(_activity_payload(activity), activity.status) for activity in input.activity_items]


def _observation_text(payload: AgentWorkbenchActivityPayloadResponse) -> str:
    counts = [
        _count_text("raw", payload.rawCandidateCount),
        _count_text("unique", payload.uniqueNewCount),
        _count_text("scored", payload.newlyScoredCount),
    ]
    count_text = ", ".join(item for item in counts if item)
    if count_text and payload.resumeQualityComment:
        return f"{payload.resumeQualityComment} ({count_text})"
    return payload.resumeQualityComment or count_text or "No observation has been projected yet."


def _reflection_text(payload: AgentWorkbenchActivityPayloadResponse) -> str:
    parts = [item for item in [payload.reflectionSummary, payload.reflectionRationale] if item]
    return " ".join(parts) if parts else "No reflection has been projected yet."


def _count_text(label: str, value: int | None) -> str | None:
    return f"{label}: {value}" if value is not None else None


def _activity_payload_from_mapping(
    payload: Mapping[str, object],
    *,
    status: str | None = None,
) -> AgentWorkbenchActivityPayloadResponse:
    return AgentWorkbenchActivityPayloadResponse(
        kind="runtime_round" if _int_or_none(payload.get("round_no")) is not None else "runtime_event",
        stage=_str_or_none(payload.get("stage")),
        sourceId=_str_or_none(payload.get("source_id")) or _str_or_none(payload.get("sourceId")),
        status=_status(status),
        roundNo=_int_or_none(payload.get("round_no")) or _int_or_none(payload.get("roundNo")),
        queryTerms=_string_list(payload.get("query_terms")) or _string_list(payload.get("queryTerms")),
        keywordQuery=_str_or_none(payload.get("keyword_query")) or _str_or_none(payload.get("keywordQuery")),
        executedQueryTerms=_executed_query_terms(payload.get("executed_queries") or payload.get("executedQueries")),
        rawCandidateCount=_int_or_none(payload.get("raw_candidate_count")) or _int_or_none(payload.get("rawCandidateCount")),
        uniqueNewCount=_int_or_none(payload.get("unique_new_count")) or _int_or_none(payload.get("uniqueNewCount")),
        newlyScoredCount=_int_or_none(payload.get("newly_scored_count")) or _int_or_none(payload.get("newlyScoredCount")),
        resumeQualityComment=_str_or_none(payload.get("resume_quality_comment")) or _str_or_none(payload.get("resumeQualityComment")),
        reflectionSummary=_str_or_none(payload.get("reflection_summary")) or _str_or_none(payload.get("reflectionSummary")),
        reflectionRationale=_str_or_none(payload.get("reflection_rationale")) or _str_or_none(payload.get("reflectionRationale")),
        suggestedActivateTerms=_string_list(payload.get("suggested_activate_terms"))
        or _string_list(payload.get("suggestedActivateTerms")),
        suggestedKeepTerms=_string_list(payload.get("suggested_keep_terms")) or _string_list(payload.get("suggestedKeepTerms")),
        suggestedDeprioritizeTerms=_string_list(payload.get("suggested_deprioritize_terms"))
        or _string_list(payload.get("suggestedDeprioritizeTerms")),
        suggestedDropTerms=_string_list(payload.get("suggested_drop_terms")) or _string_list(payload.get("suggestedDropTerms")),
    )


def _executed_query_terms(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    result: list[list[str]] = []
    for item in value:
        terms = _string_list(_mapping_get(item, "query_terms")) or _string_list(_mapping_get(item, "queryTerms"))
        if terms:
            result.append(terms)
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _source_kinds(value: object) -> list[Literal["cts", "liepin"]]:
    if not isinstance(value, list):
        return []
    return [cast(Literal["cts", "liepin"], item) for item in value if item in {"cts", "liepin"}]


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _mapping_get(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return mapping.get(key)
    return None


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return cast(Mapping[str, object], value)


def _attr(value: object, name: str) -> object:
    return getattr(value, name, None)


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


def _latest[T](items: Sequence[T], limit: int) -> list[T]:
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    return list(items[-limit:])
