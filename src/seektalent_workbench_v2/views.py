from __future__ import annotations

from seektalent_workbench_v2.models import (
    WorkbenchV2Conversation,
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationRecord,
    WorkbenchV2ConversationSummary,
    WorkbenchV2ConversationView,
    WorkbenchV2TranscriptEvent,
)


def conversation_to_summary(conversation: WorkbenchV2Conversation) -> WorkbenchV2ConversationSummary:
    return WorkbenchV2ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        createdAt=conversation.created_at,
        updatedAt=conversation.updated_at,
        runtimeRunId=conversation.runtime_run_id,
        runtimeState=conversation.runtime_state,
    )


def conversation_record_to_view(record: WorkbenchV2ConversationRecord) -> WorkbenchV2ConversationView:
    transcript_events = _visible_transcript_events(record.events)
    return WorkbenchV2ConversationView(
        conversation=conversation_to_summary(record.conversation),
        transcriptEvents=transcript_events,
        requirementForm=_latest_requirement_form(transcript_events),
    )


def conversation_list_to_view(conversations: list[WorkbenchV2Conversation]) -> WorkbenchV2ConversationListView:
    return WorkbenchV2ConversationListView(
        conversations=[conversation_to_summary(conversation) for conversation in conversations]
    )


def _visible_transcript_events(events: list[WorkbenchV2TranscriptEvent]) -> list[WorkbenchV2TranscriptEvent]:
    return [event for event in events if event.type != "context_summary"]


def _latest_requirement_form(events: list[WorkbenchV2TranscriptEvent]) -> dict[str, object] | None:
    for event in reversed(events):
        if event.type == "requirement_form":
            return event.payload
    return None
