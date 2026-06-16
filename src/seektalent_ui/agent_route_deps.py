from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_conversation_agent.store import ConversationStore
from seektalent_ui.agent_workbench_stream_store import AgentWorkbenchStreamStore


AGENT_CONVERSATION_SCHEMA_VERSION = "agent.conversation.v1"


class AgentConversationErrorDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.conversation.v1"] = AGENT_CONVERSATION_SCHEMA_VERSION
    reasonCode: str
    validationErrorCount: int = 0


def get_agent_service(request: Request) -> ConversationAgentService:
    service = getattr(request.app.state, "agent_conversation_service", None)
    if not isinstance(service, ConversationAgentService):
        raise HTTPException(status_code=500, detail="Agent conversation service is not configured.")
    return service


def get_agent_conversation_store(request: Request) -> ConversationStore:
    store = getattr(request.app.state, "agent_conversation_store", None)
    if not isinstance(store, ConversationStore):
        raise HTTPException(status_code=500, detail="Agent conversation store is not configured.")
    return store


def get_runtime_control_store(request: Request) -> object:
    store = getattr(request.app.state, "runtime_control_store", None)
    if not callable(getattr(store, "get_run", None)) or not callable(getattr(store, "list_events", None)):
        raise HTTPException(status_code=500, detail="Runtime control store is not configured.")
    return store


def get_agent_workbench_stream_store(request: Request) -> AgentWorkbenchStreamStore:
    store = getattr(request.app.state, "agent_workbench_stream_store", None)
    if not isinstance(store, AgentWorkbenchStreamStore):
        raise HTTPException(status_code=500, detail="Agent workbench stream store is not configured.")
    return store


def agent_http_error(exc: ConversationAgentError) -> HTTPException:
    status = _agent_error_status(exc.reason_code)
    detail = AgentConversationErrorDetailResponse(
        reasonCode=exc.reason_code,
        validationErrorCount=_validation_error_count(exc.payload),
    )
    return HTTPException(
        status_code=status,
        detail=detail.model_dump(mode="json"),
    )


def _agent_error_status(reason_code: str) -> int:
    if reason_code == "agent_rate_limited":
        return 429
    if reason_code == "conversation_not_found":
        return 404
    return 400


def _validation_error_count(payload: dict[str, object]) -> int:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return 0
    return len(errors)
