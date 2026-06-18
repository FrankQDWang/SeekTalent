from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageType: Literal["submitJd", "userText"]
    text: str = Field(min_length=1, max_length=20000)
    jobTitle: str | None = Field(default=None, min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=5000)
    sourceIds: list[str] = Field(default_factory=lambda: ["cts"], min_length=1, max_length=2)
    sourceKinds: list[str] | None = Field(default=None, min_length=1, max_length=2)
    idempotencyKey: str = Field(min_length=1, max_length=160)


class WorkbenchSubmitJdMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageType: Literal["submitJd"]
    text: str = Field(min_length=1, max_length=20000)
    jobTitle: str | None = Field(default=None, min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=5000)
    sourceKinds: list[str] = Field(min_length=1, max_length=2)
    idempotencyKey: str = Field(min_length=1, max_length=160)


class WorkbenchUserTextMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageType: Literal["userText"]
    text: str = Field(min_length=1, max_length=20000)
    idempotencyKey: str = Field(min_length=1, max_length=160)


WorkbenchAgentMessageRequest = Annotated[
    WorkbenchSubmitJdMessageRequest | WorkbenchUserTextMessageRequest,
    Field(discriminator="messageType"),
]


class RequirementDraftOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["set_selected", "edit_text", "delete_item", "move_item", "set_enabled"]
    itemId: str
    selected: bool | None = None
    text: str | None = None
    targetSection: str | None = None
    enabled: bool | None = None

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


class RequirementOperationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str
    baseRevisionId: str
    operations: list[RequirementDraftOperationRequest]
    idempotencyKey: str = Field(min_length=1, max_length=160)


class WorkbenchRequirementOperationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1)
    expectedDraftRevisionId: str = Field(min_length=1)
    operations: list[RequirementDraftOperationRequest] = Field(min_length=1)
    idempotencyKey: str = Field(min_length=1, max_length=160)


class ReviewResolutionOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["accept_candidate", "edit_candidate", "move_candidate", "reject_candidate", "reject_fragment"]
    reviewItemId: str
    targetSection: str | None = None
    text: str | None = None
    reasonCode: str | None = None

    def to_runtime_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"op": self.op, "review_item_id": self.reviewItemId}
        if self.targetSection is not None:
            payload["target_section"] = self.targetSection
        if self.text is not None:
            payload["text"] = self.text
        if self.reasonCode is not None:
            payload["reason_code"] = self.reasonCode
        return payload


class RequirementAmendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str
    baseRevisionId: str
    text: str = Field(min_length=1, max_length=2000)
    targetSectionHint: str | None = None
    idempotencyKey: str = Field(min_length=1, max_length=160)


class WorkbenchRequirementAmendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1)
    expectedDraftRevisionId: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)
    targetSectionHint: str | None = None
    idempotencyKey: str = Field(min_length=1, max_length=160)


class RequirementReviewResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str
    baseRevisionId: str
    amendmentId: str
    operations: list[ReviewResolutionOperationRequest]
    idempotencyKey: str = Field(min_length=1, max_length=160)


class RequirementConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str
    baseRevisionId: str
    idempotencyKey: str = Field(min_length=1, max_length=160)


class WorkbenchRequirementConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draftRevisionId: str = Field(min_length=1)
    expectedDraftRevisionId: str = Field(min_length=1)
    idempotencyKey: str = Field(min_length=1, max_length=160)


class WorkflowCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtimeRunId: str | None = None
    commandType: Literal["pause", "cancel", "resume", "nextRoundRequirement"]
    idempotencyKey: str = Field(min_length=1, max_length=160)
    text: str | None = Field(default=None, max_length=2000)
    targetSectionHint: str | None = None
