from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sse_starlette import EventSourceResponse

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_ui.agent_route_deps import (
    agent_http_error,
    get_agent_conversation_store,
    get_agent_service,
    get_agent_workbench_stream_store,
    get_runtime_control_store,
)
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchConversationListResponse,
    AgentWorkbenchConversationSummaryResponse,
    AgentWorkbenchConversationResponse,
    AgentWorkbenchStreamReplayResponse,
)
from seektalent_ui.agent_workbench_projection import RuntimeProjectionStore, build_agent_workbench_projection_input
from seektalent_ui.agent_workbench_response import project_agent_workbench_view
from seektalent_ui.agent_workbench_stream import encode_sse_event, replay_stream_envelopes
from seektalent_ui.agent_workbench_stream_projection import append_projected_stream_events
from seektalent_ui.agent_workbench_stream_store import AgentWorkbenchStreamStore
from seektalent_ui.workbench_local_actor import get_workbench_store, local_workbench_read_user
from seektalent_ui.workbench_store import WorkbenchUser


router = APIRouter(prefix="/api/agent/workbench")
logger = logging.getLogger(__name__)


@router.get("/conversations", response_model=AgentWorkbenchConversationListResponse)
def list_agent_workbench_conversations(
    request: Request,
    includeArchived: bool = False,
    user: WorkbenchUser = Depends(local_workbench_read_user),
) -> AgentWorkbenchConversationListResponse:
    conversations = get_agent_service(request).list_conversations(
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
        include_archived=includeArchived,
    )
    return AgentWorkbenchConversationListResponse(
        conversations=[
            AgentWorkbenchConversationSummaryResponse(
                conversationId=conversation.conversation_id,
                title=conversation.title,
                status=conversation.status,
                isArchived=conversation.is_archived,
                runtimeRunId=conversation.runtime_run_id,
                workbenchSessionId=conversation.workbench_session_id,
                updatedAt=conversation.updated_at,
            )
            for conversation in conversations
        ]
    )


@router.get("/conversations/{conversation_id}", response_model=AgentWorkbenchConversationResponse)
def get_agent_workbench_view(
    conversation_id: str,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_read_user),
) -> AgentWorkbenchConversationResponse:
    response = _build_agent_workbench_view(request=request, conversation_id=conversation_id, user=user)
    stream_store = get_agent_workbench_stream_store(request)
    response.streamCursor.latestStreamSeq = stream_store.latest_seq(conversation_id=conversation_id)
    return response


@router.get(
    "/conversations/{conversation_id}/events",
    response_model=AgentWorkbenchStreamReplayResponse,
)
def list_agent_workbench_events(
    conversation_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user: WorkbenchUser = Depends(local_workbench_read_user),
) -> AgentWorkbenchStreamReplayResponse:
    _ensure_conversation_access(request=request, conversation_id=conversation_id, user=user)
    stream_store = get_agent_workbench_stream_store(request)
    _append_current_projection_events(
        request=request,
        user=user,
        stream_store=stream_store,
        conversation_id=conversation_id,
    )
    replayed = list(
        replay_stream_envelopes(
            stream_store,
            conversation_id=conversation_id,
            after_seq=after_seq,
            limit=limit + 1,
        )
    )
    events = replayed[:limit]
    has_more = len(replayed) > limit
    return AgentWorkbenchStreamReplayResponse(
        conversationId=conversation_id,
        events=events,
        latestSeq=stream_store.latest_seq(conversation_id=conversation_id),
        hasMore=has_more,
        nextAfterSeq=events[-1].seq if has_more and events else None,
    )


def _build_agent_workbench_view(
    *,
    request: Request,
    conversation_id: str,
    user: WorkbenchUser,
) -> AgentWorkbenchConversationResponse:
    try:
        projection_input = build_agent_workbench_projection_input(
            service=get_agent_service(request),
            conversation_store=get_agent_conversation_store(request),
            runtime_store=cast(RuntimeProjectionStore, get_runtime_control_store(request)),
            workbench_store=get_workbench_store(request),
            conversation_id=conversation_id,
            user=user,
        )
    except ConversationAgentError as exc:
        raise agent_http_error(exc) from exc
    return project_agent_workbench_view(projection_input)


@router.get("/conversations/{conversation_id}/events/stream")
def stream_agent_workbench_events(
    conversation_id: str,
    request: Request,
    after_seq: int | None = Query(default=None, ge=0),
    user: WorkbenchUser = Depends(local_workbench_read_user),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    if any(_is_forbidden_query_param(name) for name in request.query_params):
        raise HTTPException(status_code=400, detail="Auth and token query parameters are not accepted.")
    _ensure_conversation_access(request=request, conversation_id=conversation_id, user=user)
    sequence = _stream_start_sequence(after_seq=after_seq, last_event_id=last_event_id)
    stream_store = get_agent_workbench_stream_store(request)
    return EventSourceResponse(
        _event_generator(
            request=request,
            user=user,
            stream_store=stream_store,
            conversation_id=conversation_id,
            after_seq=sequence,
        ),
        ping=15,
        send_timeout=5,
    )


def _ensure_conversation_access(*, request: Request, conversation_id: str, user: WorkbenchUser) -> None:
    try:
        get_agent_service(request).reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
        )
    except ConversationAgentError as exc:
        raise agent_http_error(exc) from exc


def _append_current_projection_events(
    *,
    request: Request,
    user: WorkbenchUser,
    stream_store: AgentWorkbenchStreamStore,
    conversation_id: str,
) -> None:
    response = _build_agent_workbench_view(request=request, conversation_id=conversation_id, user=user)
    append_projected_stream_events(stream_store, response)


async def _event_generator(
    *,
    request: Request,
    user: WorkbenchUser,
    stream_store: AgentWorkbenchStreamStore,
    conversation_id: str,
    after_seq: int,
) -> AsyncIterator[dict[str, str]]:
    sequence = after_seq
    while not await request.is_disconnected():
        emitted = False
        for event in replay_stream_envelopes(stream_store, conversation_id=conversation_id, after_seq=sequence):
            sequence = event.seq
            emitted = True
            yield encode_sse_event(event)
        if emitted:
            continue
        try:
            _append_current_projection_events(
                request=request,
                user=user,
                stream_store=stream_store,
                conversation_id=conversation_id,
            )
        except HTTPException as exc:
            logger.warning(
                "Agent workbench SSE projection catch-up failed.",
                extra={"conversation_ref": "redacted", "status_code": exc.status_code},
            )
            yield _terminal_error_event(conversation_id=conversation_id, status_code=exc.status_code)
            return
        for event in replay_stream_envelopes(stream_store, conversation_id=conversation_id, after_seq=sequence):
            sequence = event.seq
            emitted = True
            yield encode_sse_event(event)
        if emitted:
            continue
        await asyncio.sleep(0.25)


def _stream_start_sequence(*, after_seq: int | None, last_event_id: str | None) -> int:
    header_sequence = _sequence_from_header(last_event_id)
    if after_seq is not None:
        return max(after_seq, header_sequence)
    return header_sequence


def _sequence_from_header(last_event_id: str | None) -> int:
    if last_event_id is None:
        return 0
    try:
        return max(0, int(last_event_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer.") from exc


def _is_forbidden_query_param(name: str) -> bool:
    lowered = name.casefold()
    return "token" in lowered or "auth" in lowered


def _terminal_error_event(*, conversation_id: str, status_code: int) -> dict[str, str]:
    return {
        "event": "agent_workbench_error",
        "data": json.dumps(
            {
                "schemaVersion": "agent.workbench.stream.error.v1",
                "conversationId": conversation_id,
                "reasonCode": "projection_unavailable",
                "statusCode": status_code,
            },
            separators=(",", ":"),
        ),
    }
