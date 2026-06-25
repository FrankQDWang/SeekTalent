from __future__ import annotations

from typing import Any, Literal, NoReturn, Protocol, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator, model_validator

from seektalent_workbench_v2.models import WorkbenchV2ConversationListView, WorkbenchV2ConversationView
from seektalent_ui.agent_request_models import (
    MAX_AGENT_MESSAGE_CHARS,
    MAX_IDEMPOTENCY_KEY_CHARS,
    MAX_REQUEST_ID_CHARS,
    MAX_REQUIREMENT_TEXT_CHARS,
    RequestModel,
)
from seektalent_ui.problem_details import ProblemDetails, problem_http_error_from_reason
from seektalent_ui.workbench_observability import correlation_id_from_request


router = APIRouter(prefix="/api/agent/workbench/v2")
CONVERSATION_NOT_FOUND = {"reasonCode": "workbench_v2_conversation_not_found"}
IDEMPOTENCY_CONFLICT = "workbench_v2_idempotency_conflict"
WORKBENCH_V2_BAD_REQUEST_REASON_CODES = {
    "workbench_v2_requirement_action_invalid",
    "workbench_v2_requirement_draft_required",
    "workbench_v2_requirement_form_required",
    "workbench_v2_requirement_form_readonly",
    "workbench_v2_requirement_item_not_found",
    "workbench_v2_requirement_sheet_required",
    "workbench_v2_runtime_input_required",
}
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


class WorkbenchV2RequirementActionRequest(RequestModel):
    action: Literal["set_selected", "add_other", "confirm"]
    itemId: str | None = Field(default=None, min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    selected: bool | None = None
    text: str | None = Field(default=None, min_length=1, max_length=MAX_REQUIREMENT_TEXT_CHARS)
    idempotencyKey: str | None = Field(default=None, min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)

    @model_validator(mode="after")
    def validate_action_payload(self) -> WorkbenchV2RequirementActionRequest:
        if self.action == "set_selected":
            if self.itemId is None:
                raise ValueError("itemId is required for set_selected")
            if self.selected is None:
                raise ValueError("selected is required for set_selected")
            if self.text is not None:
                raise ValueError("text is not allowed for set_selected")
        if self.action == "add_other":
            if self.text is None:
                raise ValueError("text is required for add_other")
            if self.itemId is not None or self.selected is not None:
                raise ValueError("itemId and selected are not allowed for add_other")
        if self.action == "confirm" and (
            self.itemId is not None or self.selected is not None or self.text is not None
        ):
            raise ValueError("itemId, selected, and text are not allowed for confirm")
        return self


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

    async def apply_requirement_action(
        self,
        conversation_id: str,
        *,
        action: str,
        item_id: str | None = None,
        selected: bool | None = None,
        text: str | None = None,
        idempotency_key: str | None = None,
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


@router.post(
    "/conversations/{conversation_id}/requirement-actions",
    response_model=WorkbenchV2ConversationView,
    responses=WORKBENCH_V2_RESPONSES_WITH_NOT_FOUND,
)
async def apply_requirement_action(
    conversation_id: str,
    payload: WorkbenchV2RequirementActionRequest,
    request: Request,
) -> WorkbenchV2ConversationView | dict[str, object]:
    try:
        return await _service(request).apply_requirement_action(
            conversation_id,
            action=payload.action,
            item_id=payload.itemId,
            selected=payload.selected,
            text=payload.text,
            idempotency_key=payload.idempotencyKey,
        )
    except KeyError as exc:
        raise _not_found() from exc
    except ValueError as exc:
        _raise_known_domain_error(exc, request)


def _service(request: Request) -> WorkbenchV2RouteService:
    return cast("WorkbenchV2RouteService", request.app.state.workbench_v2_service)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND)


def _raise_known_domain_error(exc: ValueError, request: Request) -> NoReturn:
    reason_code = str(exc)
    if reason_code == IDEMPOTENCY_CONFLICT:
        raise problem_http_error_from_reason(
            reason_code=IDEMPOTENCY_CONFLICT,
            status=409,
            request=request,
            correlation_id=correlation_id_from_request(request),
        ) from exc
    if reason_code in WORKBENCH_V2_BAD_REQUEST_REASON_CODES:
        raise problem_http_error_from_reason(
            reason_code=reason_code,
            status=400,
            request=request,
            correlation_id=correlation_id_from_request(request),
        ) from exc
    raise exc
