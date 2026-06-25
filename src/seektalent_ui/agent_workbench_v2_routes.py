from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from seektalent_workbench_v2.models import WorkbenchV2ConversationListView, WorkbenchV2ConversationView


router = APIRouter(prefix="/api/agent/workbench/v2")
CONVERSATION_NOT_FOUND = {"reasonCode": "workbench_v2_conversation_not_found"}


class WorkbenchV2MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    idempotencyKey: str | None = None


class WorkbenchV2RouteService(Protocol):
    def list_conversations(self) -> WorkbenchV2ConversationListView | dict[str, object]: ...

    def get_conversation(self, conversation_id: str) -> WorkbenchV2ConversationView | dict[str, object]: ...

    async def create_conversation(
        self,
        message: str,
        idempotency_key: str | None,
    ) -> WorkbenchV2ConversationView | dict[str, object]: ...

    async def submit_message(
        self,
        conversation_id: str,
        message: str,
        idempotency_key: str | None,
    ) -> WorkbenchV2ConversationView | dict[str, object]: ...


@router.get("/conversations", response_model=WorkbenchV2ConversationListView)
def list_conversations(request: Request) -> WorkbenchV2ConversationListView | dict[str, object]:
    return _service(request).list_conversations()


@router.post("/conversations", response_model=WorkbenchV2ConversationView, status_code=201)
async def create_conversation(
    payload: WorkbenchV2MessageRequest,
    request: Request,
) -> WorkbenchV2ConversationView | dict[str, object]:
    return await _service(request).create_conversation(payload.message, payload.idempotencyKey)


@router.get("/conversations/{conversation_id}", response_model=WorkbenchV2ConversationView)
def get_conversation(
    conversation_id: str,
    request: Request,
) -> WorkbenchV2ConversationView | dict[str, object]:
    try:
        return _service(request).get_conversation(conversation_id)
    except KeyError as exc:
        raise _not_found() from exc


@router.post("/conversations/{conversation_id}/messages", response_model=WorkbenchV2ConversationView)
async def submit_message(
    conversation_id: str,
    payload: WorkbenchV2MessageRequest,
    request: Request,
) -> WorkbenchV2ConversationView | dict[str, object]:
    try:
        return await _service(request).submit_message(conversation_id, payload.message, payload.idempotencyKey)
    except KeyError as exc:
        raise _not_found() from exc


def _service(request: Request) -> WorkbenchV2RouteService:
    return cast("WorkbenchV2RouteService", request.app.state.workbench_v2_service)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND)
