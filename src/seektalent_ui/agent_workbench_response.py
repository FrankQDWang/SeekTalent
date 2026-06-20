from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, cast

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
    AgentWorkbenchRequirementDraftResponse,
    AgentWorkbenchRuntimeResponse,
    AgentWorkbenchStatus,
    AgentWorkbenchStrategyGraphResponse,
    AgentWorkbenchStreamCursorResponse,
    AgentWorkbenchThinkingProcessCardResponse,
    AgentWorkbenchThinkingProcessResponse,
    AgentWorkbenchThinkingProcessRoundResponse,
)
from seektalent_ui.agent_workbench_projection import AgentWorkbenchProjectionInput
from seektalent_ui.agent_workbench_transcript import build_transcript_groups


def project_agent_workbench_view(input: AgentWorkbenchProjectionInput) -> AgentWorkbenchConversationResponse:
    state = input.conversation_reopen_state
    activities = [_activity_response(activity) for activity in input.activity_items]
    return AgentWorkbenchConversationResponse(
        conversation=AgentWorkbenchConversationSummaryResponse(
            conversationId=state.conversation_id,
            title=state.title,
            status=state.status,
            isArchived=state.is_archived,
            runtimeRunId=state.runtime_run_id,
            workbenchSessionId=state.workbench_session_id,
            workflowStartIntentId=state.workflow_start_intent_id,
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
                for link in state.linked_runtime_runs
            ],
            updatedAt=state.last_opened_at,
        ),
        messages=[_message_response(message) for message in input.messages],
        activities=activities,
        transcriptGroups=build_transcript_groups(input),
        requirementDraft=_requirement_draft(state.latest_draft_revision_id, input.messages),
        runtime=input.runtime or _runtime_from_state(input),
        strategyGraph=_strategy_graph(input.activity_items),
        thinkingProcess=_thinking_process(input),
        sourceConnections=list(input.source_connections),
        candidates=list(input.candidates),
        detailApprovals=list(input.detail_approvals),
        reviewArtifacts=list(input.review_artifacts),
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
        reasonCode=state.reason_code,
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
        return AgentWorkbenchMessagePayloadResponse(kind="job_request", jobTitle=job_title)
    if message.message_type == "requirement_review":
        draft = payload.get("requirementDraft")
        draft_id = _str_or_none(_mapping_get(draft, "draftRevisionId")) or _str_or_none(
            _mapping_get(draft, "draft_revision_id")
        )
        return AgentWorkbenchMessagePayloadResponse(kind="requirement_review", requirementDraftId=draft_id)
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


def _requirement_draft(
    latest_draft_revision_id: str | None, messages: Sequence[TranscriptMessage]
) -> AgentWorkbenchRequirementDraftResponse | None:
    if latest_draft_revision_id is None:
        return None
    review_text = next((message.text for message in reversed(messages) if message.message_type == "requirement_review"), "")
    return AgentWorkbenchRequirementDraftResponse(
        draftRevisionId=latest_draft_revision_id,
        title="Requirement draft",
        summary=review_text,
    )


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
    return AgentWorkbenchStrategyGraphResponse(nodes=nodes, edges=edges)


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
