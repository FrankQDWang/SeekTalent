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
    AgentWorkbenchQueryExecutionResponse,
    AgentWorkbenchQueryGroupResponse,
    AgentWorkbenchRequirementDraftItemResponse,
    AgentWorkbenchRequirementDraftResponse,
    AgentWorkbenchRequirementDraftSectionResponse,
    AgentWorkbenchRequirementDraftStatus,
    AgentWorkbenchRequirementItemStatus,
    AgentWorkbenchRunFinalizationResponse,
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
from seektalent_ui.agent_workbench_rounds import (
    AgentWorkbenchQueryGroupProjection,
    AgentWorkbenchRoundSummaryProjection,
)
from seektalent_ui.agent_workbench_transcript import (
    build_transcript_groups,
    filter_completed_requirement_progress_messages,
)
from seektalent_ui.workbench_observability import (
    record_requirement_snapshot_invalid,
    record_workbench_payload_bytes,
)


MAX_WORKBENCH_MESSAGES = 100
MAX_WORKBENCH_ACTIVITIES = 100
MAX_WORKBENCH_OPERATION_AUDITS = 100
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
    messages = _latest(
        filter_completed_requirement_progress_messages(input.messages, input.operation_audit_records),
        MAX_WORKBENCH_MESSAGES,
    )
    activities = [_activity_response(activity) for activity in bounded_input.activity_items]
    reason_code = state.reason_code
    runtime_run_id = _workflow_runtime_run_id(state.runtime_run_id, input.workflow_start_intent)
    if (
        input.requirement_draft_missing or (runtime_run_id is not None and input.runtime is None)
    ) and reason_code is None:
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
                    status=_status(link.status),
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
        runtime=input.runtime,
        strategyGraph=_strategy_graph(bounded_input),
        thinkingProcess=_thinking_process(bounded_input),
        sourceConnections=list(_latest(input.source_connections, MAX_WORKBENCH_SOURCE_CONNECTIONS)),
        candidates=list(input.candidates[:MAX_WORKBENCH_CANDIDATES]),
        detailApprovals=list(_latest(input.detail_approvals, MAX_WORKBENCH_DETAIL_APPROVALS)),
        reviewArtifacts=list(_latest(input.review_artifacts, MAX_WORKBENCH_REVIEW_ARTIFACTS)),
        runtimeFinalization=_runtime_finalization_response(input.deterministic_finalization),
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


def _runtime_finalization_response(finalization: object | None) -> AgentWorkbenchRunFinalizationResponse | None:
    if finalization is None:
        return None
    return AgentWorkbenchRunFinalizationResponse(
        selectedIdentityCount=_int_or_none(_attr(finalization, "selected_identity_count")),
        revision=_int_or_none(_attr(finalization, "revision")),
        reasonCode=_str_or_none(_attr(finalization, "reason_code")),
        status=_status(_str_or_none(_attr(finalization, "status"))),
    )


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


def _workflow_start_reason_code(
    workflow_start_intent: AgentWorkbenchWorkflowStartIntentProjection | None,
) -> str | None:
    return workflow_start_intent.reason_code if workflow_start_intent is not None else None


def _bounded_projection_input(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchProjectionInput:
    return replace(
        input,
        messages=tuple(_latest(input.messages, MAX_WORKBENCH_MESSAGES)),
        activity_items=tuple(_latest(input.activity_items, MAX_WORKBENCH_ACTIVITIES)),
        operation_audit_records=tuple(_latest(input.operation_audit_records, MAX_WORKBENCH_OPERATION_AUDITS)),
        context_compactions=tuple(_latest(input.context_compactions, MAX_WORKBENCH_CONTEXT_COMPACTIONS)),
        runtime_events=tuple(_latest(input.runtime_events, MAX_WORKBENCH_RUNTIME_EVENTS)),
        round_summaries=tuple(_latest(tuple(input.round_summaries), MAX_WORKBENCH_THINKING_ROUNDS)),
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
        rawCandidateCount=_int_or_none(payload.get("raw_candidate_count"))
        or _int_or_none(payload.get("rawCandidateCount")),
        uniqueNewCount=_int_or_none(payload.get("unique_new_count")) or _int_or_none(payload.get("uniqueNewCount")),
        newlyScoredCount=_int_or_none(payload.get("newly_scored_count"))
        or _int_or_none(payload.get("newlyScoredCount")),
        resumeQualityComment=_str_or_none(payload.get("resume_quality_comment"))
        or _str_or_none(payload.get("resumeQualityComment")),
        reflectionSummary=_str_or_none(payload.get("reflection_summary"))
        or _str_or_none(payload.get("reflectionSummary")),
        suggestedActivateTerms=_string_list(payload.get("suggested_activate_terms"))
        or _string_list(payload.get("suggestedActivateTerms")),
        suggestedKeepTerms=_string_list(payload.get("suggested_keep_terms"))
        or _string_list(payload.get("suggestedKeepTerms")),
        suggestedDeprioritizeTerms=_string_list(payload.get("suggested_deprioritize_terms"))
        or _string_list(payload.get("suggestedDeprioritizeTerms")),
        suggestedDropTerms=_string_list(payload.get("suggested_drop_terms"))
        or _string_list(payload.get("suggestedDropTerms")),
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


def _strategy_graph(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchStrategyGraphResponse:
    if input.requirement_draft is None and not input.round_summaries and not input.activity_items:
        return AgentWorkbenchStrategyGraphResponse(nodes=[], edges=[])
    activity_items = _latest(input.activity_items, MAX_WORKBENCH_GRAPH_NODES - 1)
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
    for summary in input.round_summaries:
        round_no = _int_or_none(_attr(summary, "round_no"))
        if round_no is None:
            continue
        round_entry_node_id: str | None = None
        last_round_node_id: str | None = None
        for lane_type, source_kind in _strategy_lane_sources(summary):
            lane_node_id = f"round:{round_no}:lane:{source_kind}:{lane_type}"
            nodes.append(
                AgentWorkbenchGraphNodeResponse(
                    nodeId=lane_node_id,
                    kind="lane",
                    label=_lane_graph_label(lane_type=lane_type, source_kind=source_kind),
                    summary="Planned or executed query lane.",
                    roundNo=round_no,
                    laneType=lane_type,
                    phase="lane",
                    stage="round_lane",
                    sourceKind=source_kind,
                    status=_round_lane_status(summary, source_kind=source_kind),
                )
            )
            edges.append(
                AgentWorkbenchGraphEdgeResponse(
                    edgeId=f"{previous_node_id}->{lane_node_id}",
                    fromNodeId=previous_node_id,
                    toNodeId=lane_node_id,
                )
            )
            round_entry_node_id = round_entry_node_id or lane_node_id
            last_round_node_id = lane_node_id
        seen_phase_nodes: set[str] = set()
        previous_phase_node_id = previous_node_id
        for stage_output in _sequence_or_empty(_attr(summary, "stage_outputs")):
            stage = _str_or_none(_attr(stage_output, "stage"))
            if stage is None:
                continue
            source_kind = _graph_source_kind(_attr(stage_output, "source_kind"))
            phase = _graph_phase(stage)
            phase_node_id = f"round:{round_no}:phase:{stage}:{source_kind}"
            if phase_node_id in seen_phase_nodes:
                continue
            seen_phase_nodes.add(phase_node_id)
            nodes.append(
                AgentWorkbenchGraphNodeResponse(
                    nodeId=phase_node_id,
                    kind="phase",
                    label=_stage_graph_label(stage=stage, source_kind=source_kind),
                    summary=f"Public runtime stage output for {stage}.",
                    roundNo=round_no,
                    phase=phase,
                    stage=stage,
                    sourceKind=source_kind,
                    status=_status(_str_or_none(_attr(stage_output, "status"))),
                )
            )
            edges.append(
                AgentWorkbenchGraphEdgeResponse(
                    edgeId=f"{previous_phase_node_id}->{phase_node_id}",
                    fromNodeId=previous_phase_node_id,
                    toNodeId=phase_node_id,
                )
            )
            round_entry_node_id = round_entry_node_id or phase_node_id
            previous_phase_node_id = phase_node_id
            last_round_node_id = phase_node_id
        if round_entry_node_id is not None and last_round_node_id is not None:
            previous_node_id = last_round_node_id
    for activity in activity_items:
        node_id = activity.activity_id
        nodes.append(
            AgentWorkbenchGraphNodeResponse(
                nodeId=node_id,
                kind="activity",
                label=activity.title,
                summary=activity.summary,
                status=_status(activity.status),
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
    bounded_nodes = nodes[:MAX_WORKBENCH_GRAPH_NODES]
    bounded_node_ids = {node.nodeId for node in bounded_nodes}
    bounded_edges = [
        edge for edge in edges if edge.fromNodeId in bounded_node_ids and edge.toNodeId in bounded_node_ids
    ][:MAX_WORKBENCH_GRAPH_EDGES]
    return AgentWorkbenchStrategyGraphResponse(nodes=bounded_nodes, edges=bounded_edges)


def _strategy_lane_sources(
    summary: AgentWorkbenchRoundSummaryProjection,
) -> list[tuple[str, Literal["cts", "liepin", "all"]]]:
    lanes: list[tuple[str, Literal["cts", "liepin", "all"]]] = []
    seen: set[tuple[str, Literal["cts", "liepin", "all"]]] = set()
    for group in summary.query_groups:
        lane_type = group.lane_type
        source_kinds: list[Literal["cts", "liepin", "all"]] = [
            _graph_source_kind(execution.source_kind) for execution in group.executions
        ]
        if not source_kinds:
            source_kinds = ["all"]
        for source_kind in source_kinds:
            key = (lane_type, source_kind)
            if key in seen:
                continue
            seen.add(key)
            lanes.append(key)
    return lanes


def _round_lane_status(
    summary: AgentWorkbenchRoundSummaryProjection,
    *,
    source_kind: Literal["cts", "liepin", "all"],
) -> AgentWorkbenchStatus:
    statuses = [
        stage_output.status
        for stage_output in summary.stage_outputs
        if _graph_source_kind(stage_output.source_kind) == source_kind
    ]
    return _status(_aggregate_graph_status(statuses, fallback=_str_or_none(_attr(summary, "status"))))


def _aggregate_graph_status(statuses: Sequence[str | None], *, fallback: str | None) -> str:
    status_set = {status for status in statuses if status}
    for candidate in ("failed", "cancelled", "blocked", "partial", "running", "pending"):
        if candidate in status_set:
            return _status(candidate)
    if "completed" in status_set:
        return "completed"
    return _status(fallback)


def _lane_graph_label(*, lane_type: str, source_kind: Literal["cts", "liepin", "all"]) -> str:
    if source_kind == "all":
        return lane_type
    return f"{source_kind} {lane_type}"


def _stage_graph_label(*, stage: str, source_kind: Literal["cts", "liepin", "all"]) -> str:
    if source_kind == "all":
        return stage
    return f"{source_kind} {stage}"


def _graph_phase(stage: str) -> str:
    if stage == "round_query":
        return "query"
    if stage == "source_result":
        return "source"
    return stage


def _graph_source_kind(value: object) -> Literal["cts", "liepin", "all"]:
    if value == "cts":
        return "cts"
    if value == "liepin":
        return "liepin"
    return "all"


def _thinking_process(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchThinkingProcessResponse:
    rounds: list[AgentWorkbenchThinkingProcessRoundResponse] = []
    for payload, status in _thinking_round_payloads(input):
        if payload.roundNo is None:
            continue
        cards: list[AgentWorkbenchThinkingProcessCardResponse] = []
        observation = _observation_text(payload)
        if observation is not None:
            cards.append(AgentWorkbenchThinkingProcessCardResponse(title="observation", text=observation))
        reflection = _reflection_text(payload)
        if reflection is not None:
            cards.append(
                AgentWorkbenchThinkingProcessCardResponse(
                    title="反思和下一轮变更",
                    text=reflection,
                    terms=[
                        *payload.suggestedActivateTerms,
                        *payload.suggestedKeepTerms,
                        *payload.suggestedDeprioritizeTerms,
                        *payload.suggestedDropTerms,
                    ],
                )
            )
        if not payload.queryGroups and not cards:
            continue
        rounds.append(
            AgentWorkbenchThinkingProcessRoundResponse(
                roundNo=payload.roundNo,
                status=_status(status),
                queryGroups=payload.queryGroups,
                cards=cards,
            )
        )
    rounds = _latest(rounds, MAX_WORKBENCH_THINKING_ROUNDS)
    active_round = rounds[-1].roundNo if rounds else None
    return AgentWorkbenchThinkingProcessResponse(activeRoundNo=active_round, rounds=rounds)


def _thinking_round_payloads(
    input: AgentWorkbenchProjectionInput,
) -> list[tuple[AgentWorkbenchActivityPayloadResponse, str | None]]:
    return [(_runtime_round_payload(summary), summary.status) for summary in input.round_summaries]


def _runtime_round_payload(summary: AgentWorkbenchRoundSummaryProjection) -> AgentWorkbenchActivityPayloadResponse:
    return AgentWorkbenchActivityPayloadResponse(
        kind="runtime_round",
        stage="runtime_summary",
        status=_status(summary.status),
        roundNo=summary.round_no,
        queryGroups=[_query_group_response(item) for item in summary.query_groups],
        rawCandidateCount=summary.raw_candidate_count,
        uniqueNewCount=summary.unique_new_count,
        totalMergedIdentityCount=summary.total_merged_identity_count,
        newlyScoredCount=summary.newly_scored_count,
        topPoolCount=summary.top_pool_count,
        resumeQualityComment=summary.resume_quality_comment,
        reflectionSummary=summary.reflection_summary,
        suggestedActivateTerms=list(summary.suggested_activate_terms),
        suggestedKeepTerms=list(summary.suggested_keep_terms),
        suggestedDeprioritizeTerms=list(summary.suggested_deprioritize_terms),
        suggestedDropTerms=list(summary.suggested_drop_terms),
        suggestedAddFilterFields=list(summary.suggested_add_filter_fields),
        suggestedKeepFilterFields=list(summary.suggested_keep_filter_fields),
        suggestedDropFilterFields=list(summary.suggested_drop_filter_fields),
    )


def _query_group_response(group: AgentWorkbenchQueryGroupProjection) -> AgentWorkbenchQueryGroupResponse:
    return AgentWorkbenchQueryGroupResponse(
        queryInstanceId=group.query_instance_id,
        termGroupKey=group.term_group_key,
        queryRole=group.query_role,
        laneType=group.lane_type,
        queryTerms=list(group.query_terms),
        keywordQuery=group.keyword_query,
        lifecycle=group.lifecycle,
        executionStatus=group.execution_status,
        attempted=group.attempted,
        rawCandidateCount=group.raw_candidate_count,
        uniqueCandidateCount=group.unique_candidate_count,
        duplicateCandidateCount=group.duplicate_candidate_count,
        executions=[
            AgentWorkbenchQueryExecutionResponse(
                sourceKind=execution.source_kind,
                status=execution.status,
                rawCandidateCount=execution.raw_candidate_count,
                uniqueCandidateCount=execution.unique_candidate_count,
                duplicateCandidateCount=execution.duplicate_candidate_count,
                safeReasonCode=execution.safe_reason_code,
            )
            for execution in group.executions
        ],
    )


def _observation_text(payload: AgentWorkbenchActivityPayloadResponse) -> str | None:
    counts = [
        _count_text("raw", payload.rawCandidateCount),
        _count_text("unique", payload.uniqueNewCount),
        _count_text("scored", payload.newlyScoredCount),
    ]
    count_text = ", ".join(item for item in counts if item)
    if count_text and payload.resumeQualityComment:
        return f"{payload.resumeQualityComment} ({count_text})"
    return payload.resumeQualityComment or count_text


def _reflection_text(payload: AgentWorkbenchActivityPayloadResponse) -> str | None:
    parts = [item for item in [payload.reflectionSummary] if item]
    parts.extend(_filter_suggestion_lines(payload))
    return " ".join(parts) if parts else None


def _filter_suggestion_lines(payload: AgentWorkbenchActivityPayloadResponse) -> list[str]:
    lines: list[str] = []
    if payload.suggestedAddFilterFields:
        lines.append("新增筛选: " + ", ".join(payload.suggestedAddFilterFields))
    if payload.suggestedKeepFilterFields:
        lines.append("保留筛选: " + ", ".join(payload.suggestedKeepFilterFields))
    if payload.suggestedDropFilterFields:
        lines.append("移除筛选: " + ", ".join(payload.suggestedDropFilterFields))
    return lines


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
        rawCandidateCount=_int_or_none(payload.get("raw_candidate_count"))
        or _int_or_none(payload.get("rawCandidateCount")),
        uniqueNewCount=_int_or_none(payload.get("unique_new_count")) or _int_or_none(payload.get("uniqueNewCount")),
        newlyScoredCount=_int_or_none(payload.get("newly_scored_count"))
        or _int_or_none(payload.get("newlyScoredCount")),
        resumeQualityComment=_str_or_none(payload.get("resume_quality_comment"))
        or _str_or_none(payload.get("resumeQualityComment")),
        reflectionSummary=_str_or_none(payload.get("reflection_summary"))
        or _str_or_none(payload.get("reflectionSummary")),
        suggestedActivateTerms=_string_list(payload.get("suggested_activate_terms"))
        or _string_list(payload.get("suggestedActivateTerms")),
        suggestedKeepTerms=_string_list(payload.get("suggested_keep_terms"))
        or _string_list(payload.get("suggestedKeepTerms")),
        suggestedDeprioritizeTerms=_string_list(payload.get("suggested_deprioritize_terms"))
        or _string_list(payload.get("suggestedDeprioritizeTerms")),
        suggestedDropTerms=_string_list(payload.get("suggested_drop_terms"))
        or _string_list(payload.get("suggestedDropTerms")),
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _sequence_or_empty(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return value
    return ()


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
    if normalized == "partial":
        return "partial"
    if normalized in {"blocked", "forbidden"}:
        return "blocked"
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
