from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SourceKind = Literal["cts", "liepin"]

MAX_AGENT_MESSAGE_CHARS = 20000
MAX_IDEMPOTENCY_KEY_CHARS = 160
MAX_JOB_TITLE_CHARS = 256
MAX_NOTES_CHARS = 5000
MAX_REQUIREMENT_TEXT_CHARS = 2000
MAX_REQUIREMENT_OPERATION_COUNT = 50
MAX_REQUEST_ID_CHARS = 200
MAX_SECTION_ID_CHARS = 120
MAX_REASON_CODE_CHARS = 160


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "amendmentId",
        "baseRevisionId",
        "draftRevisionId",
        "expectedDraftRevisionId",
        "idempotencyKey",
        "itemId",
        "jobTitle",
        "notes",
        "reasonCode",
        "reviewItemId",
        "runtimeRunId",
        "targetSection",
        "targetSectionHint",
        "text",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def trim_string_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AgentMessageRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    messageType: Literal["submitJd", "userText"]
    text: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_CHARS)
    jobTitle: str | None = Field(default=None, min_length=1, max_length=MAX_JOB_TITLE_CHARS)
    notes: str | None = Field(default=None, max_length=MAX_NOTES_CHARS)
    sourceIds: list[str] | None = Field(default=None, min_length=1, max_length=2)
    sourceKinds: list[SourceKind] | None = Field(default=None, min_length=1, max_length=2)
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class WorkbenchSubmitJdMessageRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    messageType: Literal["submitJd"]
    text: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_CHARS)
    jobTitle: str | None = Field(default=None, min_length=1, max_length=MAX_JOB_TITLE_CHARS)
    notes: str | None = Field(default=None, max_length=MAX_NOTES_CHARS)
    sourceKinds: list[SourceKind] | None = Field(default=None, min_length=1, max_length=2)
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class WorkbenchUserTextMessageRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    messageType: Literal["userText"]
    text: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_CHARS)
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


WorkbenchAgentMessageRequest = Annotated[
    WorkbenchSubmitJdMessageRequest | WorkbenchUserTextMessageRequest,
    Field(discriminator="messageType"),
]


class RequirementDraftOperationRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["set_selected", "edit_text", "delete_item", "move_item", "set_enabled"]
    itemId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    selected: bool | None = None
    text: str | None = Field(default=None, min_length=1, max_length=MAX_REQUIREMENT_TEXT_CHARS)
    targetSection: str | None = Field(default=None, min_length=1, max_length=MAX_SECTION_ID_CHARS)
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_operation_payload(self) -> RequirementDraftOperationRequest:
        if self.op == "set_selected" and self.selected is None:
            raise ValueError("selected is required for set_selected")
        if self.op == "set_enabled" and self.enabled is None:
            raise ValueError("enabled is required for set_enabled")
        if self.op == "edit_text" and self.text is None:
            raise ValueError("text is required for edit_text")
        if self.op == "move_item" and self.targetSection is None:
            raise ValueError("targetSection is required for move_item")
        return self

    def to_runtime_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"op": self.op, "item_id": self.itemId}
        if self.selected is not None:
            payload["selected"] = self.selected
        if self.text is not None:
            payload["text"] = self.text
        if self.targetSection is not None:
            payload["target_section"] = self.targetSection
        if self.enabled is not None:
            payload["enabled"] = self.enabled
        return payload


class RequirementOperationsRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    baseRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    operations: list[RequirementDraftOperationRequest] = Field(
        min_length=1,
        max_length=MAX_REQUIREMENT_OPERATION_COUNT,
    )
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class WorkbenchRequirementOperationsRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    expectedDraftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    operations: list[RequirementDraftOperationRequest] = Field(
        min_length=1,
        max_length=MAX_REQUIREMENT_OPERATION_COUNT,
    )
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class ReviewResolutionOperationRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["accept_candidate", "edit_candidate", "move_candidate", "reject_candidate", "reject_fragment"]
    reviewItemId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    targetSection: str | None = Field(default=None, min_length=1, max_length=MAX_SECTION_ID_CHARS)
    text: str | None = Field(default=None, min_length=1, max_length=MAX_REQUIREMENT_TEXT_CHARS)
    reasonCode: str | None = Field(default=None, min_length=1, max_length=MAX_REASON_CODE_CHARS)

    def to_runtime_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"op": self.op, "review_item_id": self.reviewItemId}
        if self.targetSection is not None:
            payload["target_section"] = self.targetSection
        if self.text is not None:
            payload["text"] = self.text
        if self.reasonCode is not None:
            payload["reason_code"] = self.reasonCode
        return payload


class RequirementAmendRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    baseRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    text: str = Field(min_length=1, max_length=MAX_REQUIREMENT_TEXT_CHARS)
    targetSectionHint: str | None = Field(default=None, min_length=1, max_length=MAX_SECTION_ID_CHARS)
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class WorkbenchRequirementAmendRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    expectedDraftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    text: str = Field(min_length=1, max_length=MAX_REQUIREMENT_TEXT_CHARS)
    targetSectionHint: str | None = Field(default=None, min_length=1, max_length=MAX_SECTION_ID_CHARS)
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class RequirementReviewResolveRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    baseRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    amendmentId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    operations: list[ReviewResolutionOperationRequest] = Field(
        min_length=1,
        max_length=MAX_REQUIREMENT_OPERATION_COUNT,
    )
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class RequirementConfirmRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    baseRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class WorkbenchRequirementConfirmRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    expectedDraftRevisionId: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)


class WorkflowCommandRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    runtimeRunId: str | None = Field(default=None, min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    commandType: Literal["pause", "cancel", "resume", "nextRoundRequirement"]
    idempotencyKey: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_CHARS)
    text: str | None = Field(default=None, min_length=1, max_length=MAX_REQUIREMENT_TEXT_CHARS)
    targetSectionHint: str | None = Field(default=None, min_length=1, max_length=MAX_SECTION_ID_CHARS)
