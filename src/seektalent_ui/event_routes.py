from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sse_starlette import EventSourceResponse

from seektalent_ui.auth import get_session_cookie, get_workbench_store, require_current_user_readonly, session_token_digest
from seektalent_ui.models import WorkbenchEventListResponse, WorkbenchEventResponse, WorkbenchNoteCreatedPayload
from seektalent_ui.workbench_store import WorkbenchEvent, WorkbenchUser


router = APIRouter()


@router.get("/api/workbench/events", response_model=WorkbenchEventListResponse)
def list_events(
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> WorkbenchEventListResponse:
    store = get_workbench_store(request)
    return WorkbenchEventListResponse(
        events=[_event_response(event) for event in store.list_workbench_events(user=user, after_seq=after_seq, limit=limit)]
    )


@router.get("/api/workbench/sessions/{session_id}/events", response_model=WorkbenchEventListResponse)
def list_session_events(
    session_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> WorkbenchEventListResponse:
    store = get_workbench_store(request)
    if store.get_workbench_session(user=user, session_id=session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return WorkbenchEventListResponse(
        events=[
            _event_response(event)
            for event in store.list_session_workbench_events(user=user, session_id=session_id, after_seq=after_seq, limit=limit)
        ]
    )


@router.get("/api/workbench/events/stream")
def stream_events(
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    user: WorkbenchUser = Depends(require_current_user_readonly),
    session_id: str | None = Depends(get_session_cookie),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    if session_id is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if any(_is_forbidden_query_param(name) for name in request.query_params):
        raise HTTPException(status_code=400, detail="Auth and token query parameters are not accepted.")
    sequence = max(after_seq, _sequence_from_header(last_event_id))
    return EventSourceResponse(
        _event_generator(request=request, user=user, session_digest=session_token_digest(session_id), after_seq=sequence),
        ping=15,
        send_timeout=5,
    )


@router.get("/api/workbench/sessions/{workbench_session_id}/events/stream")
def stream_session_events(
    workbench_session_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    user: WorkbenchUser = Depends(require_current_user_readonly),
    session_id: str | None = Depends(get_session_cookie),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    if session_id is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    store = get_workbench_store(request)
    if store.get_workbench_session(user=user, session_id=workbench_session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if any(_is_forbidden_query_param(name) for name in request.query_params):
        raise HTTPException(status_code=400, detail="Auth and token query parameters are not accepted.")
    sequence = max(after_seq, _sequence_from_header(last_event_id))
    return EventSourceResponse(
        _event_generator(
            request=request,
            user=user,
            session_digest=session_token_digest(session_id),
            after_seq=sequence,
            workbench_session_id=workbench_session_id,
        ),
        ping=15,
        send_timeout=5,
    )


async def _event_generator(
    *,
    request: Request,
    user: WorkbenchUser,
    session_digest: str,
    after_seq: int,
    workbench_session_id: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    sequence = after_seq
    store = get_workbench_store(request)
    while not await request.is_disconnected():
        current_user = store.get_user_by_session_readonly(session_digest=session_digest)
        if (
            current_user is None
            or current_user.user_id != user.user_id
            or current_user.workspace_id != user.workspace_id
        ):
            return
        if workbench_session_id is None:
            events = store.list_workbench_events(user=current_user, after_seq=sequence, limit=100)
        else:
            events = store.list_session_workbench_events(
                user=current_user,
                session_id=workbench_session_id,
                after_seq=sequence,
                limit=100,
            )
        if events:
            for event in events:
                sequence = event.global_seq
                data = json.dumps(_event_data(event), sort_keys=True, separators=(",", ":"))
                yield {
                    "id": str(event.global_seq),
                    "event": "workbench_event",
                    "data": data,
                }
                yield {
                    "id": str(event.global_seq),
                    "event": event.event_name,
                    "data": data,
                }
            continue
        await asyncio.sleep(0.25)


def _event_response(event: WorkbenchEvent) -> WorkbenchEventResponse:
    return WorkbenchEventResponse(
        globalSeq=event.global_seq,
        sessionSeq=event.session_seq,
        sessionId=event.session_id,
        sourceRunId=event.source_run_id,
        sourceKind=event.source_kind,
        eventName=event.event_name,
        schemaVersion=event.schema_version,
        idempotencyKey=event.idempotency_key,
        payload=_project_event_payload(event),
        occurredAt=event.occurred_at,
        createdAt=event.created_at,
    )


def _event_data(event: WorkbenchEvent) -> dict[str, object]:
    return {
        "globalSeq": event.global_seq,
        "sessionSeq": event.session_seq,
        "sessionId": event.session_id,
        "sourceRunId": event.source_run_id,
        "sourceKind": event.source_kind,
        "eventName": event.event_name,
        "schemaVersion": event.schema_version,
        "idempotencyKey": event.idempotency_key,
        "payload": _project_event_payload(event),
        "occurredAt": event.occurred_at,
        "createdAt": event.created_at,
    }


def _project_event_payload(event: WorkbenchEvent) -> dict[str, object]:
    if event.event_name == "workbench_note_created":
        return _note_created_payload(event).model_dump()
    projected = _drop_broad_runtime_fields(event.payload)
    if isinstance(projected, dict):
        return projected
    return {"value": projected}


def _note_created_payload(event: WorkbenchEvent) -> WorkbenchNoteCreatedPayload:
    payload = dict(event.payload)
    payload["eventSeq"] = int(payload.get("eventSeq") or event.global_seq)
    payload["createdAt"] = str(payload.get("createdAt") or event.created_at)
    payload["noteId"] = str(payload.get("noteId") or "")
    payload["text"] = str(payload.get("text") or "")
    payload["statusHint"] = str(payload.get("statusHint") or "unknown")
    payload["noteKind"] = str(payload.get("noteKind") or "progress")
    return WorkbenchNoteCreatedPayload.model_validate(payload)


def _drop_broad_runtime_fields(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if _is_broad_runtime_field(str(key)):
                continue
            result[str(key)] = _drop_broad_runtime_fields(item)
        return result
    if isinstance(value, list):
        return [_drop_broad_runtime_fields(item) for item in value]
    return value


def _is_broad_runtime_field(key: str) -> bool:
    compact = "".join(character for character in key.casefold() if character.isalnum())
    return compact.startswith("redacted") or compact in {
        "artifactpath",
        "cookie",
        "providerresponse",
        "rawcontext",
        "rawpayload",
        "stacktrace",
    }


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
