from __future__ import annotations

from typing import Any, NoReturn, Protocol, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator

from seektalent_workbench_v2.models import WorkbenchV2ConversationListView, WorkbenchV2ConversationView
from seektalent_ui.agent_request_models import MAX_AGENT_MESSAGE_CHARS, MAX_IDEMPOTENCY_KEY_CHARS, RequestModel
from seektalent_ui.problem_details import ProblemDetails, problem_http_error_from_reason
from seektalent_ui.workbench_observability import correlation_id_from_request


router = APIRouter(prefix="/api/agent/workbench/v2")
CONVERSATION_NOT_FOUND = {"reasonCode": "workbench_v2_conversation_not_found"}
IDEMPOTENCY_CONFLICT = "workbench_v2_idempotency_conflict"
WORKBENCH_V2_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetails},
    409: {"model": ProblemDetails},
    503: {"model": ProblemDetails},
}
WORKBENCH_V2_NOT_FOUND_RESPONSE: dict[str, Any] = {
    "description": "Workbench v2 conversation not found.",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["detail"],
                "properties": {
                    "detail": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["reasonCode"],
                        "properties": {
                            "reasonCode": {
                                "type": "string",
                                "enum": ["workbench_v2_conversation_not_found"],
                            }
                        },
                    }
                },
            }
        }
    },
}
WORKBENCH_V2_RESPONSES_WITH_NOT_FOUND = {
    **WORKBENCH_V2_PROBLEM_RESPONSES,
    404: WORKBENCH_V2_NOT_FOUND_RESPONSE,
}


class WorkbenchV2MessageRequest(RequestModel):
    message: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_CHARS)
    idempotencyKey: str | None = Field(default=None, min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)

    @field_validator("message", "idempotencyKey", mode="before")
    @classmethod
    def trim_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


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


@router.get(
    "/conversations",
    response_model=WorkbenchV2ConversationListView,
    responses=WORKBENCH_V2_PROBLEM_RESPONSES,
)
def list_conversations(request: Request) -> WorkbenchV2ConversationListView | dict[str, object]:
    return _service(request).list_conversations()


@router.post(
    "/conversations",
    response_model=WorkbenchV2ConversationView,
    status_code=201,
    responses=WORKBENCH_V2_PROBLEM_RESPONSES,
)
async def create_conversation(
    payload: WorkbenchV2MessageRequest,
    request: Request,
) -> WorkbenchV2ConversationView | dict[str, object]:
    try:
        return await _service(request).create_conversation(payload.message, payload.idempotencyKey)
    except ValueError as exc:
        _raise_known_domain_error(exc, request)


@router.get(
    "/conversations/{conversation_id}",
    response_model=WorkbenchV2ConversationView,
    responses=WORKBENCH_V2_RESPONSES_WITH_NOT_FOUND,
)
def get_conversation(
    conversation_id: str,
    request: Request,
) -> WorkbenchV2ConversationView | dict[str, object]:
    try:
        return _service(request).get_conversation(conversation_id)
    except KeyError as exc:
        raise _not_found() from exc


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=WorkbenchV2ConversationView,
    responses=WORKBENCH_V2_RESPONSES_WITH_NOT_FOUND,
)
async def submit_message(
    conversation_id: str,
    payload: WorkbenchV2MessageRequest,
    request: Request,
) -> WorkbenchV2ConversationView | dict[str, object]:
    try:
        return await _service(request).submit_message(conversation_id, payload.message, payload.idempotencyKey)
    except KeyError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        _raise_known_domain_error(exc, request)


def _service(request: Request) -> WorkbenchV2RouteService:
    return cast("WorkbenchV2RouteService", request.app.state.workbench_v2_service)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND)


def _raise_known_domain_error(exc: ValueError, request: Request) -> NoReturn:
    if str(exc) == IDEMPOTENCY_CONFLICT:
        raise problem_http_error_from_reason(
            reason_code=IDEMPOTENCY_CONFLICT,
            status=409,
            request=request,
            correlation_id=correlation_id_from_request(request),
        ) from exc
    raise exc
