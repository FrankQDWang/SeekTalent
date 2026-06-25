from __future__ import annotations

from seektalent_workbench_v2.models import (
    WorkbenchV2Conversation,
    WorkbenchV2ConversationListSummary,
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationPublic,
    WorkbenchV2ConversationRecord,
    WorkbenchV2ConversationView,
    WorkbenchV2RuntimeView,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventView,
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
    return WorkbenchV2ConversationView(
        conversation=conversation_to_public(record.conversation),
        transcriptEvents=[event_to_view(event) for event in transcript_events],
        requirementForm=_latest_requirement_form(record.events),
        runtime=conversation_to_runtime(record.conversation),
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
        payload=event.payload,
        createdAt=event.created_at,
    )


def _visible_transcript_events(events: list[WorkbenchV2TranscriptEvent]) -> list[WorkbenchV2TranscriptEvent]:
    return [event for event in events if event.type != "context_summary"]


def _latest_requirement_form(events: list[WorkbenchV2TranscriptEvent]) -> dict[str, object] | None:
    for event in reversed(events):
        if event.type == "requirement_form_confirmed":
            payload = dict(event.payload)
            payload["readonly"] = True
            return payload
        if event.type == "requirement_form":
            return dict(event.payload)
    return None
