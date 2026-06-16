from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sse_starlette import EventSourceResponse

from seektalent.source_adapters import public_source_reason_code
from seektalent_ui.models import (
    SourceKind,
    WorkbenchEventNestedReasonResponse,
    WorkbenchEventListResponse,
    WorkbenchEventPayloadResponse,
    WorkbenchEventWarningResponse,
    WorkbenchEventResponse,
    WorkbenchNoteCreatedPayload,
    WorkbenchRuntimePublicCountsResponse,
)
from seektalent_ui.workbench_local_actor import get_workbench_store, local_workbench_read_user
from seektalent_ui.workbench_store import WorkbenchEvent, WorkbenchStore, WorkbenchUser


router = APIRouter()


@router.get("/api/workbench/events", response_model=WorkbenchEventListResponse, response_model_exclude_none=True)
def list_events(
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: WorkbenchUser = Depends(local_workbench_read_user),
) -> WorkbenchEventListResponse:
    store = get_workbench_store(request)
    return WorkbenchEventListResponse(
        events=[_event_response(event) for event in store.list_workbench_events(user=user, after_seq=after_seq, limit=limit)]
    )


@router.get(
    "/api/workbench/sessions/{session_id}/events",
    response_model=WorkbenchEventListResponse,
    response_model_exclude_none=True,
)
def list_session_events(
    session_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: WorkbenchUser = Depends(local_workbench_read_user),
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
    after_seq: int | None = Query(default=None, ge=0),
    user: WorkbenchUser = Depends(local_workbench_read_user),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    if any(_is_forbidden_query_param(name) for name in request.query_params):
        raise HTTPException(status_code=400, detail="Auth and token query parameters are not accepted.")
    store = get_workbench_store(request)
    sequence = _stream_start_sequence(
        store=store,
        user=user,
        after_seq=after_seq,
        last_event_id=last_event_id,
        workbench_session_id=None,
    )
    return EventSourceResponse(
        _event_generator(request=request, user=user, after_seq=sequence),
        ping=15,
        send_timeout=5,
    )


@router.get("/api/workbench/sessions/{workbench_session_id}/events/stream")
def stream_session_events(
    workbench_session_id: str,
    request: Request,
    after_seq: int | None = Query(default=None, ge=0),
    user: WorkbenchUser = Depends(local_workbench_read_user),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    if any(_is_forbidden_query_param(name) for name in request.query_params):
        raise HTTPException(status_code=400, detail="Auth and token query parameters are not accepted.")
    store = get_workbench_store(request)
    if store.get_workbench_session(user=user, session_id=workbench_session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    sequence = _stream_start_sequence(
        store=store,
        user=user,
        after_seq=after_seq,
        last_event_id=last_event_id,
        workbench_session_id=workbench_session_id,
    )
    return EventSourceResponse(
        _event_generator(
            request=request,
            user=user,
            after_seq=sequence,
            workbench_session_id=workbench_session_id,
        ),
        ping=15,
        send_timeout=5,
    )


def _stream_start_sequence(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    after_seq: int | None,
    last_event_id: str | None,
    workbench_session_id: str | None,
) -> int:
    header_sequence = _sequence_from_header(last_event_id)
    if after_seq is not None:
        return max(after_seq, header_sequence)
    if header_sequence > 0:
        return header_sequence
    return store.latest_workbench_event_seq(user=user, session_id=workbench_session_id)


async def _event_generator(
    *,
    request: Request,
    user: WorkbenchUser,
    after_seq: int,
    workbench_session_id: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    sequence = after_seq
    store = get_workbench_store(request)
    while not await request.is_disconnected():
        if workbench_session_id is None:
            events = store.list_workbench_events(user=user, after_seq=sequence, limit=100)
        else:
            events = store.list_session_workbench_events(
                user=user,
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
    payload = _project_event_payload(event)
    return {
        "globalSeq": event.global_seq,
        "sessionSeq": event.session_seq,
        "sessionId": event.session_id,
        "sourceRunId": event.source_run_id,
        "sourceKind": event.source_kind,
        "eventName": event.event_name,
        "schemaVersion": event.schema_version,
        "idempotencyKey": event.idempotency_key,
        "payload": payload.model_dump(exclude_none=True),
        "occurredAt": event.occurred_at,
        "createdAt": event.created_at,
    }


def _project_event_payload(event: WorkbenchEvent) -> WorkbenchNoteCreatedPayload | WorkbenchEventPayloadResponse:
    if event.event_name == "workbench_note_created":
        return _note_created_payload(event)
    projected = _drop_broad_runtime_fields(event.payload)
    return _event_payload_response(projected)


def _event_payload_response(value: object) -> WorkbenchEventPayloadResponse:
    if not isinstance(value, Mapping):
        return WorkbenchEventPayloadResponse(value=str(value))
    mapping = cast(Mapping[object, object], value)
    payload: dict[str, object] = {}
    _copy_event_payload_fields(payload, mapping)
    nested_payload = mapping.get("payload")
    if isinstance(nested_payload, Mapping):
        _copy_event_payload_fields(payload, cast(Mapping[object, object], nested_payload))
    return WorkbenchEventPayloadResponse.model_validate(payload)


def _copy_event_payload_fields(target: dict[str, object], source: Mapping[object, object]) -> None:
    for field in WorkbenchEventPayloadResponse.model_fields:
        if field in target or field not in source:
            continue
        item = source[field]
        if item is None:
            continue
        if field == "sourceKinds":
            source_kinds = _source_kind_list(item)
            if source_kinds:
                target[field] = source_kinds
            continue
        if field == "counts":
            counts = _runtime_public_counts(item)
            if counts is not None:
                target[field] = counts
            continue
        if field == "nested":
            nested = _nested_reason_response(item)
            if nested is not None:
                target[field] = nested
            continue
        if isinstance(item, str | int | float | bool):
            target[field] = item


def _source_kind_list(value: object) -> list[SourceKind]:
    if not isinstance(value, list | tuple):
        return []
    source_kinds: list[SourceKind] = []
    for item in value:
        if item == "cts" or item == "liepin":
            source_kinds.append(cast(SourceKind, item))
    return source_kinds


def _runtime_public_counts(value: object) -> WorkbenchRuntimePublicCountsResponse | None:
    if not isinstance(value, Mapping):
        return None
    mapping = cast(Mapping[object, object], value)
    payload: dict[str, int] = {}
    for field in WorkbenchRuntimePublicCountsResponse.model_fields:
        item = mapping.get(field)
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            payload[field] = item
    if not payload:
        return None
    return WorkbenchRuntimePublicCountsResponse.model_validate(payload)


def _nested_reason_response(value: object) -> WorkbenchEventNestedReasonResponse | None:
    if not isinstance(value, Mapping):
        return None
    mapping = cast(Mapping[object, object], value)
    payload: dict[str, object] = {}
    blocked_reason_code = mapping.get("blocked_reason_code")
    if isinstance(blocked_reason_code, str):
        payload["blocked_reason_code"] = blocked_reason_code
    events = mapping.get("events")
    if isinstance(events, list | tuple):
        warnings = []
        for event in events:
            if not isinstance(event, Mapping):
                continue
            event_mapping = cast(Mapping[object, object], event)
            warning_code = event_mapping.get("warningCode")
            if isinstance(warning_code, str):
                warnings.append(WorkbenchEventWarningResponse(warningCode=warning_code))
        if warnings:
            payload["events"] = warnings
    if not payload:
        return None
    return WorkbenchEventNestedReasonResponse.model_validate(payload)


def _note_created_payload(event: WorkbenchEvent) -> WorkbenchNoteCreatedPayload:
    payload = dict(event.payload)
    payload["eventSeq"] = _event_seq_value(payload.get("eventSeq"), fallback=event.global_seq)
    payload["createdAt"] = str(payload.get("createdAt") or event.created_at)
    payload["noteId"] = str(payload.get("noteId") or "")
    payload["text"] = str(payload.get("text") or "")
    payload["statusHint"] = str(payload.get("statusHint") or "unknown")
    payload["noteKind"] = str(payload.get("noteKind") or "progress")
    return WorkbenchNoteCreatedPayload.model_validate(payload)


def _event_seq_value(value: object, *, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(str(value))
    except ValueError:
        return fallback


def _drop_broad_runtime_fields(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if _is_broad_runtime_field(str(key)):
                continue
            projected = _drop_broad_runtime_fields(item)
            if _is_source_reason_code_field(str(key)):
                projected = _public_source_reason_value(projected)
            result[str(key)] = projected
        return result
    if isinstance(value, list):
        return [_drop_broad_runtime_fields(item) for item in value]
    return value


def _public_source_reason_value(value: object) -> object:
    public_code = public_source_reason_code(value)
    return public_code if public_code is not None else value


def _is_source_reason_code_field(key: str) -> bool:
    compact = "".join(character for character in key.casefold() if character.isalnum())
    return compact in {
        "blockedreasoncode",
        "connectionwarningcode",
        "reasoncode",
        "safereasoncode",
        "stopreasoncode",
        "warningcode",
    }


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
