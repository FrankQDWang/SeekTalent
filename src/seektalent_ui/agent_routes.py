from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from seektalent.config import AppSettings
from seektalent_agent_memory.privacy import MemoryPrivacyError
from seektalent_agent_memory.extraction import AgentRuntimeStage1Extractor, STAGE1_INSTRUCTIONS
from seektalent_agent_memory.pipeline import AgentRuntimeMemoryConsolidator, MemoryPipeline, PHASE2_INSTRUCTIONS
from seektalent_agent_memory.service import MemoryService
from seektalent_agent_memory.store import MemoryStore
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.runtime import AgentRuntime
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_ui.auth import require_csrf_user, require_current_user_readonly
from seektalent_ui.workbench_store import WorkbenchUser


AGENT_CONVERSATION_SCHEMA_VERSION = "agent.conversation.v1"
AGENT_MEMORY_SCHEMA_VERSION = "agent.memory.v2"

router = APIRouter(prefix="/api/agent")


@dataclass
class LocalAgentRateLimiter:
    max_writes_per_minute: int = 60
    now: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, *, user_id: str, conversation_id: str) -> None:
        self._check_bucket(("user", user_id))
        if conversation_id != "new":
            self._check_bucket((user_id, conversation_id))

    def _check_bucket(self, key: tuple[str, str]) -> None:
        bucket = self._hits[key]
        current = self.now()
        while bucket and current - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= self.max_writes_per_minute:
            raise ConversationAgentError("agent_rate_limited")
        bucket.append(current)


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)


class ConversationTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messageType: Literal["submitJd", "userText"]
    text: str = Field(min_length=1, max_length=20000)
    jobTitle: str | None = Field(default=None, min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=5000)
    sourceIds: list[str] = Field(default_factory=lambda: ["cts"], min_length=1, max_length=2)
    idempotencyKey: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_submit_jd_fields(self) -> "AgentMessageRequest":
        if self.messageType == "submitJd" and self.jobTitle is None:
            raise ValueError("jobTitle is required for submitJd messages")
        return self


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


class WorkflowStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobTitle: str = Field(min_length=1, max_length=256)
    jdText: str = Field(min_length=1, max_length=20000)
    notes: str | None = Field(default=None, max_length=5000)
    sourceIds: list[str] = Field(default_factory=lambda: ["cts"], min_length=1, max_length=2)


class WorkflowCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtimeRunId: str
    commandType: Literal["pause", "cancel", "resume", "nextRoundRequirement"]
    idempotencyKey: str = Field(min_length=1, max_length=160)
    text: str | None = Field(default=None, max_length=2000)
    targetSectionHint: str | None = None


class FinalSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtimeRunId: str
    userInstruction: str | None = Field(default=None, max_length=2000)
    idempotencyKey: str = Field(min_length=1, max_length=160)


class MemorySettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memoryEnabled: bool
    generationEnabled: bool
    recallEnabled: bool
    reviewRequired: bool
    candidateRetentionDays: int | None = Field(default=None, ge=1)
    rejectedRetentionDays: int | None = Field(default=None, ge=1)
    sourceExcerptRetentionDays: int | None = Field(default=None, ge=1)


class MemoryTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)


class MemoryAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=1000)


@router.post("/conversations", status_code=201)
def create_conversation(
    payload: ConversationCreateRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id="new")
    service = get_agent_service(request)
    try:
        conversation = service.create_conversation(
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            title=payload.title,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response({"conversation": _camelize(conversation.model_dump(mode="json"))})


@router.get("/conversations")
def list_conversations(
    request: Request,
    includeArchived: bool = False,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_agent_service(request)
    conversations = service.list_conversations(
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
        include_archived=includeArchived,
    )
    return _response({"conversations": [_camelize(item.model_dump(mode="json")) for item in conversations]})


@router.get("/conversations/{conversation_id}")
def reopen_conversation(
    conversation_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_agent_service(request)
    try:
        view = service.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(view.model_dump(mode="json")))


@router.patch("/conversations/{conversation_id}/title")
def rename_conversation(
    conversation_id: str,
    payload: ConversationTitleRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        conversation = service.rename_conversation(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            title=payload.title,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response({"conversation": _camelize(conversation.model_dump(mode="json"))})


@router.post("/conversations/{conversation_id}/archive")
def archive_conversation(
    conversation_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        conversation = service.archive_conversation(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response({"conversation": _camelize(conversation.model_dump(mode="json"))})


@router.post("/conversations/{conversation_id}/unarchive")
def unarchive_conversation(
    conversation_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        conversation = service.unarchive_conversation(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response({"conversation": _camelize(conversation.model_dump(mode="json"))})


@router.post("/conversations/{conversation_id}/messages")
async def submit_message(
    conversation_id: str,
    payload: AgentMessageRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        if payload.messageType == "submitJd":
            if payload.jobTitle is None:
                raise ConversationAgentError("agent_request_invalid")
            response = service.submit_jd(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                job_title=payload.jobTitle,
                jd_text=payload.text,
                notes=payload.notes,
                source_ids=payload.sourceIds,
                idempotency_key=payload.idempotencyKey,
            )
        else:
            response = await service.run_agent_turn(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                user_message=payload.text,
                idempotency_key=payload.idempotencyKey,
            )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.post("/conversations/{conversation_id}/requirements/operations")
def update_requirement_draft(
    conversation_id: str,
    payload: RequirementOperationsRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        response = service.update_requirement_draft(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            draft_revision_id=payload.draftRevisionId,
            base_revision_id=payload.baseRevisionId,
            operations=[operation.to_runtime_payload() for operation in payload.operations],
            idempotency_key=payload.idempotencyKey,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.post("/conversations/{conversation_id}/requirements/amend-from-text")
def amend_requirement_from_text(
    conversation_id: str,
    payload: RequirementAmendRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        response = service.amend_requirement_draft_from_text(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            draft_revision_id=payload.draftRevisionId,
            base_revision_id=payload.baseRevisionId,
            text=payload.text,
            target_section_hint=payload.targetSectionHint,
            idempotency_key=payload.idempotencyKey,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.post("/conversations/{conversation_id}/requirements/resolve-review")
def resolve_requirement_review(
    conversation_id: str,
    payload: RequirementReviewResolveRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        response = service.resolve_requirement_review(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            draft_revision_id=payload.draftRevisionId,
            base_revision_id=payload.baseRevisionId,
            amendment_id=payload.amendmentId,
            operations=[operation.to_runtime_payload() for operation in payload.operations],
            idempotency_key=payload.idempotencyKey,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.post("/conversations/{conversation_id}/requirements/confirm")
def confirm_requirements(
    conversation_id: str,
    payload: RequirementConfirmRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        response = service.confirm_requirements(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            draft_revision_id=payload.draftRevisionId,
            base_revision_id=payload.baseRevisionId,
            idempotency_key=payload.idempotencyKey,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.post("/conversations/{conversation_id}/workflow/start")
def start_workflow(
    conversation_id: str,
    payload: WorkflowStartRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        response = service.start_workflow(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            job_title=payload.jobTitle,
            jd_text=payload.jdText,
            notes=payload.notes,
            source_ids=payload.sourceIds,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.post("/conversations/{conversation_id}/workflow/commands")
def workflow_command(
    conversation_id: str,
    payload: WorkflowCommandRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        if payload.commandType == "nextRoundRequirement":
            if payload.text is None:
                raise ConversationAgentError("agent_free_text_empty")
            response = service.submit_next_round_requirement(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                runtime_run_id=payload.runtimeRunId,
                text=payload.text,
                target_section_hint=payload.targetSectionHint,
                idempotency_key=payload.idempotencyKey,
            )
        else:
            response = service.request_workflow_command(
                conversation_id=conversation_id,
                owner_user_id=user.user_id,
                workspace_id=user.workspace_id,
                runtime_run_id=payload.runtimeRunId,
                command_type=payload.commandType,
                idempotency_key=payload.idempotencyKey,
            )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.get("/conversations/{conversation_id}/workflow/events")
def workflow_events(
    conversation_id: str,
    runtimeRunId: str,
    request: Request,
    limit: int = 100,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_agent_service(request)
    try:
        response = service.poll_runtime_events(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            runtime_run_id=runtimeRunId,
            limit=limit,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.get("/conversations/{conversation_id}/workflow/snapshot")
def workflow_snapshot(
    conversation_id: str,
    runtimeRunId: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_agent_service(request)
    try:
        snapshot = service.get_workflow_snapshot(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            runtime_run_id=runtimeRunId,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response({"snapshot": _camelize(snapshot.model_dump(mode="json"))})


@router.get("/conversations/{conversation_id}/workflow/detail")
def workflow_detail(
    conversation_id: str,
    runtimeRunId: str,
    kind: str,
    request: Request,
    roundNo: int | None = None,
    eventId: str | None = None,
    commandId: str | None = None,
    checkpointId: str | None = None,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_agent_service(request)
    try:
        response = service.get_runtime_detail(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            runtime_run_id=runtimeRunId,
            kind=kind,
            round_no=roundNo,
            event_id=eventId,
            command_id=commandId,
            checkpoint_id=checkpointId,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.post("/conversations/{conversation_id}/final-summary")
def final_summary(
    conversation_id: str,
    payload: FinalSummaryRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_write_rate(request, user=user, conversation_id=conversation_id)
    service = get_agent_service(request)
    try:
        response = service.prepare_final_summary(
            conversation_id=conversation_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            runtime_run_id=payload.runtimeRunId,
            user_instruction=payload.userInstruction,
            idempotency_key=payload.idempotencyKey,
        )
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc
    return _response(_camelize(response.model_dump(mode="json")))


@router.get("/memory/settings")
def get_memory_settings(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_memory_service(request)
    settings = service.get_settings(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"settings": _camelize(settings.model_dump(mode="json"))})


@router.put("/memory/settings")
def update_memory_settings(
    payload: MemorySettingsRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    service = get_memory_service(request)
    settings = service.update_settings(
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
        memory_enabled=payload.memoryEnabled,
        generation_enabled=payload.generationEnabled,
        recall_enabled=payload.recallEnabled,
        review_required=payload.reviewRequired,
        candidate_retention_days=payload.candidateRetentionDays,
        rejected_retention_days=payload.rejectedRetentionDays,
        source_excerpt_retention_days=payload.sourceExcerptRetentionDays,
    )
    return _memory_response({"settings": _camelize(settings.model_dump(mode="json"))})


@router.get("/memory/jobs")
def list_memory_jobs(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_memory_service(request)
    jobs = service.store.list_jobs(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"jobs": [_camelize(item.model_dump(mode="json")) for item in jobs]})


@router.post("/memory/jobs/run")
async def run_memory_jobs(
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    memory_service = get_memory_service(request)
    agent_service = get_agent_service(request)
    settings: AppSettings = request.app.state.settings
    pipeline = MemoryPipeline(
        store=memory_service.store,
        transcript_reader=agent_service,
        extractor=AgentRuntimeStage1Extractor(
            runtime=AgentRuntime(model_name=settings.controller_model_id, instructions=STAGE1_INSTRUCTIONS),
        ),
        consolidator=AgentRuntimeMemoryConsolidator(
            runtime=AgentRuntime(model_name=settings.controller_model_id, instructions=PHASE2_INSTRUCTIONS),
        ),
        workspace_root=settings.agent_memory_workspace_path,
        now=_now,
    )
    phase1 = await pipeline.run_phase1_startup(
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
        current_conversation_id=None,
    )
    phase2 = await pipeline.run_phase2(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    cleanup = memory_service.run_retention_cleanup(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response(
        {
            "phase1": _camelize(phase1.model_dump(mode="json")),
            "phase2": _camelize(phase2.model_dump(mode="json")),
            "cleanupResult": _camelize(cleanup.model_dump(mode="json")),
        }
    )


@router.get("/memory/candidates")
def list_memory_candidates(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_memory_service(request)
    candidates = service.store.list_candidates(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"candidates": [_camelize(item.model_dump(mode="json")) for item in candidates]})


@router.post("/memory/candidates/{candidate_id}/accept")
def accept_memory_candidate(
    candidate_id: str,
    payload: MemoryAcceptRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    service = get_memory_service(request)
    try:
        fact = service.accept_candidate(
            candidate_id=candidate_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            accepted_text=payload.text,
        )
    except (MemoryPrivacyError, RuntimeError) as exc:
        raise _memory_http_error(exc) from exc
    return _memory_response({"fact": _camelize(fact.model_dump(mode="json"))})


@router.post("/memory/candidates/{candidate_id}/reject")
def reject_memory_candidate(
    candidate_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    service = get_memory_service(request)
    try:
        candidate = service.reject_candidate(
            candidate_id=candidate_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
        )
    except RuntimeError as exc:
        raise _memory_http_error(exc) from exc
    return _memory_response({"candidate": _camelize(candidate.model_dump(mode="json"))})


@router.get("/memory/facts")
def list_memory_facts(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_memory_service(request)
    facts = service.store.list_facts(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"facts": [_camelize(item.model_dump(mode="json")) for item in facts]})


@router.get("/memory/summaries")
def list_memory_summaries(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_memory_service(request)
    summaries = service.store.list_summaries(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"summaries": [_camelize(item.model_dump(mode="json")) for item in summaries]})


@router.get("/memory/usage")
def list_memory_usage(
    request: Request,
    user: WorkbenchUser = Depends(require_current_user_readonly),
) -> dict[str, object]:
    service = get_memory_service(request)
    usage = service.store.list_usage(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"usage": [_camelize(item.model_dump(mode="json")) for item in usage]})


@router.patch("/memory/facts/{fact_id}")
def update_memory_fact(
    fact_id: str,
    payload: MemoryTextRequest,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    service = get_memory_service(request)
    try:
        fact = service.update_fact(
            fact_id=fact_id,
            owner_user_id=user.user_id,
            workspace_id=user.workspace_id,
            text=payload.text,
        )
    except (MemoryPrivacyError, RuntimeError) as exc:
        raise _memory_http_error(exc) from exc
    return _memory_response({"fact": _camelize(fact.model_dump(mode="json"))})


@router.delete("/memory/facts/{fact_id}")
def delete_memory_fact(
    fact_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    service = get_memory_service(request)
    try:
        fact = service.delete_fact(fact_id=fact_id, owner_user_id=user.user_id, workspace_id=user.workspace_id)
    except RuntimeError as exc:
        raise _memory_http_error(exc) from exc
    return _memory_response({"fact": _camelize(fact.model_dump(mode="json"))})


@router.post("/memory/clear")
def clear_memory(
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    service = get_memory_service(request)
    result = service.clear_scope(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"clearResult": _camelize(result.model_dump(mode="json"))})


@router.post("/memory/retention/run")
def run_memory_retention_cleanup(
    request: Request,
    user: WorkbenchUser = Depends(require_csrf_user),
) -> dict[str, object]:
    _check_memory_write_rate(request, user=user)
    service = get_memory_service(request)
    result = service.run_retention_cleanup(owner_user_id=user.user_id, workspace_id=user.workspace_id)
    return _memory_response({"cleanupResult": _camelize(result.model_dump(mode="json"))})


def build_memory_service(*, settings: AppSettings) -> MemoryService:
    store = MemoryStore(settings.agent_memory_path)
    store.initialize()
    return MemoryService(store=store, now=_now)


def get_agent_service(request: Request) -> ConversationAgentService:
    service = getattr(request.app.state, "agent_conversation_service", None)
    if not isinstance(service, ConversationAgentService):
        raise HTTPException(status_code=500, detail="Agent conversation service is not configured.")
    return service


def get_memory_service(request: Request) -> MemoryService:
    service = getattr(request.app.state, "agent_memory_service", None)
    if not isinstance(service, MemoryService):
        raise HTTPException(status_code=500, detail="Agent memory service is not configured.")
    return service


def _check_write_rate(request: Request, *, user: WorkbenchUser, conversation_id: str) -> None:
    limiter = getattr(request.app.state, "agent_rate_limiter", None)
    if not isinstance(limiter, LocalAgentRateLimiter):
        return
    try:
        limiter.check(user_id=user.user_id, conversation_id=conversation_id)
    except ConversationAgentError as exc:
        raise _agent_http_error(exc) from exc


def _check_memory_write_rate(request: Request, *, user: WorkbenchUser) -> None:
    limiter = getattr(request.app.state, "agent_rate_limiter", None)
    if not isinstance(limiter, LocalAgentRateLimiter):
        return
    try:
        limiter.check(user_id=user.user_id, conversation_id="memory")
    except ConversationAgentError as exc:
        raise _memory_http_error(exc, status_code=429) from exc


def _agent_http_error(exc: ConversationAgentError) -> HTTPException:
    status = 429 if exc.reason_code == "agent_rate_limited" else 400
    return HTTPException(
        status_code=status,
        detail={
            "schemaVersion": AGENT_CONVERSATION_SCHEMA_VERSION,
            "reasonCode": exc.reason_code,
            "payload": exc.payload,
        },
    )


def _response(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise TypeError("Agent response payload must be a dictionary.")
    response: dict[str, object] = {"schemaVersion": AGENT_CONVERSATION_SCHEMA_VERSION}
    response.update({str(key): item for key, item in payload.items()})
    return response


def _memory_response(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise TypeError("Agent memory response payload must be a dictionary.")
    response: dict[str, object] = {"schemaVersion": AGENT_MEMORY_SCHEMA_VERSION}
    response.update({str(key): item for key, item in payload.items()})
    return response


def _memory_http_error(exc: Exception, *, status_code: int = 400) -> HTTPException:
    reason_code = exc.reason_code if isinstance(exc, ConversationAgentError) else str(exc)
    payload = exc.payload if isinstance(exc, ConversationAgentError) else {}
    return HTTPException(
        status_code=status_code,
        detail={
            "schemaVersion": AGENT_MEMORY_SCHEMA_VERSION,
            "reasonCode": reason_code,
            "payload": payload,
        },
    )


def _camelize(value: object) -> object:
    if isinstance(value, dict):
        return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
