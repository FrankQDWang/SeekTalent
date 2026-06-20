from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sse_starlette import EventSourceResponse

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_ui.agent_rate_limit import check_agent_write_rate
from seektalent_ui.agent_request_models import (
    WorkbenchAgentMessageRequest,
    WorkbenchRequirementAmendRequest,
    WorkbenchRequirementConfirmRequest,
    WorkbenchRequirementOperationsRequest,
    WorkbenchSubmitJdMessageRequest,
    WorkflowCommandRequest,
)
from seektalent_ui.agent_route_deps import (
    get_agent_conversation_store,
    get_agent_service,
    get_agent_workbench_stream_store,
    get_runtime_control_store,
)
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchCandidateDetailResponse,
    AgentWorkbenchConversationListResponse,
    AgentWorkbenchConversationResponse,
    AgentWorkbenchStreamReplayResponse,
)
from seektalent_ui.agent_workbench_projection import (
    AgentWorkbenchWorkflowStartIntentProjection,
    RuntimeProjectionStore,
    build_agent_workbench_projection_input,
    candidate_detail_response_from_review_item,
)
from seektalent_ui.agent_workbench_response import (
    project_agent_workbench_conversation_summary,
    project_agent_workbench_view,
)
from seektalent_ui.agent_workbench_stream import encode_sse_event, replay_stream_envelopes
from seektalent_ui.agent_workbench_stream_projection import append_projected_stream_events
from seektalent_ui.agent_workbench_stream_store import AgentWorkbenchStreamStore
from seektalent_ui.problem_details import problem_http_error_from_conversation_error, problem_http_error_from_reason
from seektalent_ui.workbench_observability import (
    correlation_id_from_request,
    record_duplicate_run_prevented,
    record_idempotency_conflict,
    record_sse_replay_gap,
    record_workbench_audit_event,
)
from seektalent_ui.workbench_local_actor import get_workbench_store, local_workbench_read_user, local_workbench_write_user
from seektalent_ui.workbench_store import WorkbenchUser


router = APIRouter(prefix="/api/agent/workbench")
logger = logging.getLogger(__name__)
# Raw conversation routes keep agent_http_error; Workbench routes return Problem Details.
CANDIDATE_DETAIL_HEADERS = {"Cache-Control": "no-store"}


@router.get("/conversations", response_model=AgentWorkbenchConversationListResponse)
def list_agent_workbench_conversations(
    request: Request,
    includeArchived: bool = False,
    user: WorkbenchUser = Depends(local_workbench_read_user),
) -> AgentWorkbenchConversationListResponse:
    service = get_agent_service(request)
    conversations = service.list_conversations(
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
        include_archived=includeArchived,
    )
    return AgentWorkbenchConversationListResponse(
        conversations=[
            project_agent_workbench_conversation_summary(
                conversation,
                workflow_start_intent=_latest_workflow_start_intent(
                    service=service,
                    workspace_id=user.workspace_id,
                    conversation_id=conversation.conversation_id,
                ),
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
    return _build_agent_workbench_snapshot(request=request, conversation_id=conversation_id, user=user)


@router.get(
    "/conversations/{conversation_id}/candidates/{candidate_id}/detail",
    response_model=AgentWorkbenchCandidateDetailResponse,
)
def get_agent_workbench_candidate_detail(
    conversation_id: str,
    candidate_id: str,
    request: Request,
    response: Response,
    user: WorkbenchUser = Depends(local_workbench_read_user),
) -> AgentWorkbenchCandidateDetailResponse:
    view = _build_agent_workbench_snapshot(request=request, conversation_id=conversation_id, user=user)
    session_id = view.conversation.workbenchSessionId
    correlation_id = correlation_id_from_request(request)
    if session_id is None:
        raise problem_http_error_from_reason(
            reason_code="candidate_detail_unavailable",
            status=404,
            request=request,
            correlation_id=correlation_id,
            headers=CANDIDATE_DETAIL_HEADERS,
        )
    item = get_workbench_store(request).get_candidate_review_item(
        user=user,
        session_id=session_id,
        review_item_id=candidate_id,
    )
    if item is None:
        raise problem_http_error_from_reason(
            reason_code="candidate_detail_unavailable",
            status=404,
            request=request,
            correlation_id=correlation_id,
            headers=CANDIDATE_DETAIL_HEADERS,
        )
    detail = candidate_detail_response_from_review_item(item)
    record_workbench_audit_event(
        "candidate_detail_read",
        reason_code=detail.reasonCode,
        correlation_id=correlation_id,
        extra={"candidateId": candidate_id, "accessState": detail.accessState},
    )
    if detail.accessState == "denied":
        raise problem_http_error_from_reason(
            reason_code="permission_denied",
            status=403,
            request=request,
            correlation_id=correlation_id,
            headers=CANDIDATE_DETAIL_HEADERS,
        )
    response.headers["Cache-Control"] = "no-store"
    return detail


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AgentWorkbenchConversationResponse,
)
async def submit_agent_workbench_message(
    conversation_id: str,
    payload: WorkbenchAgentMessageRequest,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> AgentWorkbenchConversationResponse:
    service = get_agent_service(request)
    try:
        check_agent_write_rate(request, user=user, conversation_id=conversation_id)
        if isinstance(payload, WorkbenchSubmitJdMessageRequest):
            service.submit_jd(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                job_title=payload.jobTitle,
                jd_text=payload.text,
                notes=payload.notes,
                source_kinds=payload.sourceKinds,
                idempotency_key=payload.idempotencyKey,
            )
        else:
            await service.run_agent_turn(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                user_message=payload.text,
                idempotency_key=payload.idempotencyKey,
            )
    except ConversationAgentError as exc:
        raise _agent_workbench_error(exc, request) from exc
    return _build_agent_workbench_snapshot(request=request, conversation_id=conversation_id, user=user)


@router.post(
    "/conversations/{conversation_id}/requirements/operations",
    response_model=AgentWorkbenchConversationResponse,
)
def update_agent_workbench_requirement_draft(
    conversation_id: str,
    payload: WorkbenchRequirementOperationsRequest,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> AgentWorkbenchConversationResponse:
    try:
        check_agent_write_rate(request, user=user, conversation_id=conversation_id)
        get_agent_service(request).update_requirement_draft(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            draft_revision_id=payload.draftRevisionId,
            base_revision_id=payload.expectedDraftRevisionId,
            operations=[operation.to_runtime_payload() for operation in payload.operations],
            idempotency_key=payload.idempotencyKey,
        )
    except ConversationAgentError as exc:
        raise _agent_workbench_error(exc, request) from exc
    return _build_agent_workbench_snapshot(request=request, conversation_id=conversation_id, user=user)


@router.post(
    "/conversations/{conversation_id}/requirements/amend-from-text",
    response_model=AgentWorkbenchConversationResponse,
)
def amend_agent_workbench_requirement_from_text(
    conversation_id: str,
    payload: WorkbenchRequirementAmendRequest,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> AgentWorkbenchConversationResponse:
    try:
        check_agent_write_rate(request, user=user, conversation_id=conversation_id)
        get_agent_service(request).amend_requirement_draft_from_text(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            draft_revision_id=payload.draftRevisionId,
            base_revision_id=payload.expectedDraftRevisionId,
            text=payload.text,
            target_section_hint=payload.targetSectionHint,
            idempotency_key=payload.idempotencyKey,
        )
    except ConversationAgentError as exc:
        raise _agent_workbench_error(exc, request) from exc
    return _build_agent_workbench_snapshot(request=request, conversation_id=conversation_id, user=user)


@router.post(
    "/conversations/{conversation_id}/requirements/confirm",
    response_model=AgentWorkbenchConversationResponse,
)
def confirm_agent_workbench_requirements(
    conversation_id: str,
    payload: WorkbenchRequirementConfirmRequest,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> AgentWorkbenchConversationResponse:
    try:
        check_agent_write_rate(request, user=user, conversation_id=conversation_id)
        get_agent_service(request).confirm_requirements(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            draft_revision_id=payload.draftRevisionId,
            expected_draft_revision_id=payload.expectedDraftRevisionId,
            idempotency_key=payload.idempotencyKey,
    )
    except ConversationAgentError as exc:
        raise _agent_workbench_error(exc, request) from exc
    return _build_agent_workbench_snapshot(request=request, conversation_id=conversation_id, user=user)


@router.post(
    "/conversations/{conversation_id}/workflow/commands",
    response_model=AgentWorkbenchConversationResponse,
)
def submit_agent_workbench_workflow_command(
    conversation_id: str,
    payload: WorkflowCommandRequest,
    request: Request,
    user: WorkbenchUser = Depends(local_workbench_write_user),
) -> AgentWorkbenchConversationResponse:
    service = get_agent_service(request)
    try:
        check_agent_write_rate(request, user=user, conversation_id=conversation_id)
        if payload.commandType == "nextRoundRequirement":
            if payload.text is None:
                raise ConversationAgentError("agent_free_text_empty")
            service.submit_next_round_requirement(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                runtime_run_id=payload.runtimeRunId,
                text=payload.text,
                target_section_hint=payload.targetSectionHint,
                idempotency_key=payload.idempotencyKey,
            )
        else:
            service.request_workflow_command(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                runtime_run_id=payload.runtimeRunId,
                command_type=payload.commandType,
                idempotency_key=payload.idempotencyKey,
            )
    except ConversationAgentError as exc:
        raise _agent_workbench_error(exc, request) from exc
    return _build_agent_workbench_snapshot(request=request, conversation_id=conversation_id, user=user)


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
    _raise_if_replay_cursor_stale(
        request=request,
        stream_store=stream_store,
        conversation_id=conversation_id,
        after_seq=after_seq,
    )
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
    _raise_if_replay_has_gap(request=request, replayed=replayed)
    events = replayed[:limit]
    has_more = len(replayed) > limit
    return AgentWorkbenchStreamReplayResponse(
        conversationId=conversation_id,
        events=events,
        latestSeq=stream_store.latest_seq(conversation_id=conversation_id),
        hasMore=has_more,
        nextAfterSeq=events[-1].seq if has_more and events else None,
    )


def _latest_workflow_start_intent(
    *,
    service: ConversationAgentService,
    workspace_id: str,
    conversation_id: str,
) -> AgentWorkbenchWorkflowStartIntentProjection | None:
    intent = service.workflow_start_intent_store.get_latest_for_conversation(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    if intent is None:
        return None
    return AgentWorkbenchWorkflowStartIntentProjection(
        workflow_start_intent_id=intent.workflow_start_intent_id,
        status=intent.status,
        runtime_run_id=intent.runtime_run_id,
        reason_code=intent.reason_code,
    )


def _build_agent_workbench_snapshot(
    *,
    request: Request,
    conversation_id: str,
    user: WorkbenchUser,
) -> AgentWorkbenchConversationResponse:
    stream_store = get_agent_workbench_stream_store(request)
    boundary = stream_store.snapshot_boundary(conversation_id=conversation_id)
    response = _build_agent_workbench_view(request=request, conversation_id=conversation_id, user=user)
    response.streamCursor.snapshotSeq = boundary.snapshot_seq
    response.streamCursor.latestStreamSeq = boundary.snapshot_seq
    response.streamCursor.viewRevision = boundary.view_revision
    return response


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
        raise _agent_workbench_error(exc, request) from exc
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
        raise problem_http_error_from_reason(
            reason_code="agent_request_invalid",
            status=400,
            request=request,
            correlation_id=correlation_id_from_request(request),
            detail="Auth and token query parameters are not accepted.",
        )
    _ensure_conversation_access(request=request, conversation_id=conversation_id, user=user)
    sequence = _stream_start_sequence(request=request, after_seq=after_seq, last_event_id=last_event_id)
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
        raise _agent_workbench_error(exc, request) from exc


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
    correlation_id = correlation_id_from_request(request)
    if _replay_cursor_is_stale(
        stream_store=stream_store,
        conversation_id=conversation_id,
        after_seq=after_seq,
    ):
        record_sse_replay_gap(correlation_id=correlation_id)
        yield _terminal_error_event(
            conversation_id=conversation_id,
            reason_code="stream_replay_gap",
            status_code=410,
            correlation_id=correlation_id,
        )
        return
    sequence = after_seq
    while not await request.is_disconnected():
        emitted = False
        for event in replay_stream_envelopes(stream_store, conversation_id=conversation_id, after_seq=sequence):
            if event.kind == "stream.gap":
                record_sse_replay_gap(correlation_id=correlation_id)
                yield _terminal_error_event(
                    conversation_id=conversation_id,
                    reason_code="stream_replay_gap",
                    status_code=410,
                    correlation_id=correlation_id,
                )
                return
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
            record_workbench_audit_event(
                "projection_unavailable",
                reason_code="projection_unavailable",
                correlation_id=correlation_id,
            )
            logger.warning(
                "Agent workbench SSE projection catch-up failed.",
                extra={"conversation_ref": "redacted", "status_code": exc.status_code},
            )
            yield _terminal_error_event(
                conversation_id=conversation_id,
                reason_code="projection_unavailable",
                status_code=exc.status_code,
                correlation_id=correlation_id,
            )
            return
        for event in replay_stream_envelopes(stream_store, conversation_id=conversation_id, after_seq=sequence):
            if event.kind == "stream.gap":
                record_sse_replay_gap(correlation_id=correlation_id)
                yield _terminal_error_event(
                    conversation_id=conversation_id,
                    reason_code="stream_replay_gap",
                    status_code=410,
                    correlation_id=correlation_id,
                )
                return
            sequence = event.seq
            emitted = True
            yield encode_sse_event(event)
        if emitted:
            continue
        await asyncio.sleep(0.25)


def _stream_start_sequence(*, request: Request, after_seq: int | None, last_event_id: str | None) -> int:
    header_sequence = _sequence_from_header(request=request, last_event_id=last_event_id)
    if after_seq is not None:
        return max(after_seq, header_sequence)
    return header_sequence


def _sequence_from_header(*, request: Request, last_event_id: str | None) -> int:
    if last_event_id is None:
        return 0
    try:
        return max(0, int(last_event_id))
    except ValueError as exc:
        raise problem_http_error_from_reason(
            reason_code="agent_request_invalid",
            status=400,
            request=request,
            correlation_id=correlation_id_from_request(request),
            detail="Last-Event-ID must be an integer.",
        ) from exc


def _is_forbidden_query_param(name: str) -> bool:
    lowered = name.casefold()
    return "token" in lowered or "auth" in lowered


def _raise_if_replay_cursor_stale(
    *,
    request: Request,
    stream_store: AgentWorkbenchStreamStore,
    conversation_id: str,
    after_seq: int,
) -> None:
    if _replay_cursor_is_stale(stream_store=stream_store, conversation_id=conversation_id, after_seq=after_seq):
        raise _stream_replay_gap_error(request)


def _raise_if_replay_has_gap(*, request: Request, replayed: Sequence[object]) -> None:
    if any(getattr(event, "kind", None) == "stream.gap" for event in replayed):
        raise _stream_replay_gap_error(request)


def _replay_cursor_is_stale(
    *,
    stream_store: AgentWorkbenchStreamStore,
    conversation_id: str,
    after_seq: int,
) -> bool:
    return after_seq < stream_store.minimum_replay_seq(conversation_id=conversation_id)


def _stream_replay_gap_error(request: Request) -> HTTPException:
    correlation_id = correlation_id_from_request(request)
    record_sse_replay_gap(correlation_id=correlation_id)
    return problem_http_error_from_reason(
        reason_code="stream_replay_gap",
        status=410,
        request=request,
        correlation_id=correlation_id,
    )


def _agent_workbench_error(exc: ConversationAgentError, request: Request) -> HTTPException:
    correlation_id = correlation_id_from_request(request)
    if exc.reason_code == "idempotency_key_conflict":
        record_idempotency_conflict(correlation_id=correlation_id)
    elif exc.reason_code == "agent_request_in_progress":
        record_duplicate_run_prevented(correlation_id=correlation_id)
    return problem_http_error_from_conversation_error(
        exc=exc,
        request=request,
        correlation_id=correlation_id,
    )


def _terminal_error_event(
    *,
    conversation_id: str,
    reason_code: str,
    status_code: int,
    correlation_id: str,
) -> dict[str, str]:
    return {
        "event": "agent_workbench_error",
        "data": json.dumps(
            {
                "schemaVersion": "agent.workbench.stream.error.v1",
                "conversationId": conversation_id,
                "reasonCode": reason_code,
                "statusCode": status_code,
                "correlationId": correlation_id,
            },
            separators=(",", ":"),
        ),
    }
