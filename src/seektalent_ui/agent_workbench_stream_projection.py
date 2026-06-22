from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchConversationResponse,
    AgentWorkbenchStreamKind,
    AgentWorkbenchTranscriptPayloadResponse,
)


@dataclass(frozen=True)
class AgentWorkbenchStreamAppend:
    kind: AgentWorkbenchStreamKind
    payload: AgentWorkbenchTranscriptPayloadResponse
    source_fact_key: str
    created_at: str
    source_kind: str | None = None
    source_id: str | None = None
    source_seq: int | None = None


def project_agent_workbench_stream_events(
    response: AgentWorkbenchConversationResponse,
) -> list[AgentWorkbenchStreamAppend]:
    """Project a snapshot into idempotent semantic BFF stream events."""
    events: list[AgentWorkbenchStreamAppend] = []
    events.extend(_transcript_events(response))
    events.extend(_requirement_events(response))
    events.append(_strategy_graph_event(response))
    events.extend(_source_connection_events(response))
    events.extend(_candidate_events(response))
    events.extend(_detail_approval_events(response))
    events.extend(_artifact_events(response))
    events.extend(_final_summary_events(response))
    events.extend(_runtime_finalization_events(response))
    events.append(_pending_action_event(response))
    events.extend(_thinking_process_events(response))
    return events


def append_projected_stream_events(stream_store, response: AgentWorkbenchConversationResponse) -> None:
    conversation_id = response.conversation.conversationId
    for event in project_agent_workbench_stream_events(response):
        stream_store.append_event(
            conversation_id=conversation_id,
            kind=event.kind,
            payload=event.payload,
            source_fact_key=event.source_fact_key,
            created_at=event.created_at,
            source_kind=event.source_kind,
            source_id=event.source_id,
            source_seq=event.source_seq,
        )


def _transcript_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    events: list[AgentWorkbenchStreamAppend] = []
    for group in response.transcriptGroups:
        for event in group.events:
            signature = _signature(event.model_dump(mode="json"))
            events.append(
                AgentWorkbenchStreamAppend(
                    kind=event.kind,
                    payload=event.payload,
                    source_fact_key=f"{event.eventId}:{signature}",
                    created_at=event.createdAt,
                )
            )
    return events


def _requirement_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    draft = response.requirementDraft
    if draft is None:
        return []
    return [
        AgentWorkbenchStreamAppend(
            kind="requirement.updated",
            payload=AgentWorkbenchTranscriptPayloadResponse(
                kind="artifact",
                itemId=draft.draftRevisionId,
                summary=draft.summary,
            ),
            source_fact_key=f"requirement:{draft.draftRevisionId}",
            created_at=_created_at(response),
            source_kind="requirement",
            source_id=draft.draftRevisionId,
        )
    ]


def _strategy_graph_event(response: AgentWorkbenchConversationResponse) -> AgentWorkbenchStreamAppend:
    signature = _signature(
        {
            "nodes": [node.model_dump(mode="json") for node in response.strategyGraph.nodes],
            "edges": [edge.model_dump(mode="json") for edge in response.strategyGraph.edges],
        }
    )
    return AgentWorkbenchStreamAppend(
        kind="strategyGraph.changed",
        payload=AgentWorkbenchTranscriptPayloadResponse(
            kind="strategy_graph",
            itemId=signature,
            summary=f"{len(response.strategyGraph.nodes)} nodes, {len(response.strategyGraph.edges)} edges",
        ),
        source_fact_key=f"strategy_graph:{signature}",
        created_at=_created_at(response),
        source_kind="strategy_graph",
        source_id=response.conversation.conversationId,
    )


def _source_connection_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    return [
        AgentWorkbenchStreamAppend(
            kind="sourceConnection.changed",
            payload=AgentWorkbenchTranscriptPayloadResponse(
                kind="source_connection",
                itemId=connection.sourceKind,
                summary=connection.status,
            ),
            source_fact_key=f"source_connection:{connection.sourceKind}:{connection.status}",
            created_at=connection.lastCheckedAt or _created_at(response),
            source_kind="source_connection",
            source_id=connection.sourceKind,
        )
        for connection in response.sourceConnections
    ]


def _candidate_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    events: list[AgentWorkbenchStreamAppend] = []
    for candidate in response.candidates:
        signature = _signature(candidate.model_dump(mode="json"))
        events.append(
            AgentWorkbenchStreamAppend(
                kind="candidate.upserted",
                payload=AgentWorkbenchTranscriptPayloadResponse(
                    kind="candidate",
                    itemId=candidate.candidateId,
                    summary=candidate.matchSummary or candidate.displayName,
                ),
                source_fact_key=f"candidate:{candidate.candidateId}:{candidate.status}:{signature}",
                created_at=_created_at(response),
                source_kind="candidate",
                source_id=candidate.candidateId,
            )
        )
    return events


def _detail_approval_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    return [
        AgentWorkbenchStreamAppend(
            kind="detailApproval.changed",
            payload=AgentWorkbenchTranscriptPayloadResponse(
                kind="approval",
                itemId=approval.approvalId,
                summary=approval.reason,
            ),
            source_fact_key=f"detail_approval:{approval.approvalId}:{approval.status}",
            created_at=_created_at(response),
            source_kind="detail_approval",
            source_id=approval.approvalId,
        )
        for approval in response.detailApprovals
    ]


def _artifact_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    return [
        AgentWorkbenchStreamAppend(
            kind="runtime.eventProjected",
            payload=AgentWorkbenchTranscriptPayloadResponse(
                kind="artifact",
                itemId=artifact.artifactId,
                summary=artifact.safeSummary,
            ),
            source_fact_key=f"artifact:{artifact.artifactId}",
            created_at=_created_at(response),
            source_kind="artifact",
            source_id=artifact.artifactId,
        )
        for artifact in response.reviewArtifacts
    ]


def _final_summary_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    summary = response.finalSummary
    if summary is None:
        return []
    return [
        AgentWorkbenchStreamAppend(
            kind="finalSummary.updated",
            payload=AgentWorkbenchTranscriptPayloadResponse(
                kind="final_summary",
                itemId=summary.summaryId,
                summary=summary.text,
            ),
            source_fact_key=f"final_summary:{summary.summaryId}",
            created_at=_created_at(response),
            source_kind="final_summary",
            source_id=summary.summaryId,
        )
    ]


def _runtime_finalization_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    finalization = response.runtimeFinalization
    if finalization is None:
        return []
    signature = _signature(
        {
            "selectedIdentityCount": finalization.selectedIdentityCount,
            "revision": finalization.revision,
            "reasonCode": finalization.reasonCode,
            "status": finalization.status,
        }
    )
    return [
        AgentWorkbenchStreamAppend(
            kind="runtimeFinalization.changed",
            payload=AgentWorkbenchTranscriptPayloadResponse(
                kind="runtime_finalization",
                itemId="runtimeFinalization",
                summary=finalization.reasonCode or finalization.status,
            ),
            source_fact_key=f"runtime_finalization:{signature}",
            created_at=_created_at(response),
            source_kind="runtime_finalization",
            source_id=response.conversation.conversationId,
            source_seq=finalization.revision or 0,
        )
    ]


def _pending_action_event(response: AgentWorkbenchConversationResponse) -> AgentWorkbenchStreamAppend:
    pending = response.pendingActions
    signature = _signature(
        {
            "primary": pending.primary,
            "allowed": pending.allowed,
            "pendingCommandCount": pending.pendingCommandCount,
            "pendingRequirementReviewCount": pending.pendingRequirementReviewCount,
            "pendingMemoryReviewCount": pending.pendingMemoryReviewCount,
        }
    )
    return AgentWorkbenchStreamAppend(
        kind="pendingAction.changed",
        payload=AgentWorkbenchTranscriptPayloadResponse(
            kind="pending_action",
            itemId=pending.primary or "none",
            summary=pending.primary,
        ),
        source_fact_key=f"pending_action:{signature}",
        created_at=_created_at(response),
        source_kind="pending_action",
        source_id=response.conversation.conversationId,
    )


def _thinking_process_events(response: AgentWorkbenchConversationResponse) -> list[AgentWorkbenchStreamAppend]:
    rounds = response.thinkingProcess.rounds
    if not rounds:
        return []
    latest_round = rounds[-1]
    signature = _signature(latest_round.model_dump(mode="json"))
    return [
        AgentWorkbenchStreamAppend(
            kind="thinkingProcess.changed",
            payload=AgentWorkbenchTranscriptPayloadResponse(
                kind="thinking_process",
                itemId=f"round:{latest_round.roundNo}",
                summary=latest_round.cards[-1].text if latest_round.cards else None,
            ),
            source_fact_key=f"thinking_process:{signature}",
            created_at=_created_at(response),
            source_kind="thinking_process",
            source_id=response.conversation.conversationId,
            source_seq=latest_round.roundNo,
        )
    ]


def _created_at(response: AgentWorkbenchConversationResponse) -> str:
    return response.conversation.updatedAt or ""


def _signature(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
