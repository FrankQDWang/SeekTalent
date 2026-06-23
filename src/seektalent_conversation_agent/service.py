from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from seektalent_conversation_agent.budget import AgentBudgetPolicy
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.job_request_store import JobRequestStore
from seektalent_conversation_agent.job_requests import (
    JobRequestRevision,
    RequirementDraftJobRequestLink,
    normalize_source_kinds,
)
from seektalent_conversation_agent.models import (
    OperationAuditRecord,
    ConversationAgentResponse,
    ConversationRecord,
    ConversationRuntimeRunLink,
    ConversationThreadView,
    TranscriptActivityItem,
    TranscriptMessage,
)
from seektalent_conversation_agent.projection import project_runtime_event
from seektalent_conversation_agent.runtime import (
    AgentRunner,
    AgentRuntime,
    ModelInputTranscriptMessage,
    _model_input_json,
    build_cache_ready_model_input,
)
from seektalent_conversation_agent.safety import (
    MAX_REQUIREMENT_TEXT_CHARS,
    MAX_SECTION_HINT_CHARS,
    sanitize_summary_text,
    screen_requirement_text,
    screen_target_section_hint,
)
from seektalent_conversation_agent.source_selection import (
    RuntimeSourceSelectionResolver,
    SourceSelectionError,
)
from seektalent_conversation_agent.store import ConversationStore
from seektalent_conversation_agent.submit_jd_recovery import (
    assistant_message_idempotency_key as _assistant_message_idempotency_key,
    extracted_job_title_from_runtime_control as _extracted_job_title_from_runtime_control,
    normalize_optional_job_title as _normalize_optional_job_title,
    requirement_review_message_idempotency_key as _requirement_review_message_idempotency_key,
    requirement_review_payload as _requirement_review_payload,
    should_repair_submit_replay_status as _should_repair_submit_replay_status,
    operation_audit_draft_revision_id as _operation_audit_draft_revision_id,
)
from seektalent_conversation_agent.service_actions import AgentServiceActionAdapter
from seektalent_conversation_agent.workflow_start_intents import (
    WorkbenchOutboxStore,
    WorkflowConfirmRequestStore,
    WorkflowStartIntent,
    WorkflowStartIntentStore,
    workflow_confirm_request_hash,
    workflow_start_request_hash,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeFinalSummary, RuntimeRunSnapshot
from seektalent_runtime_control.requirements import (
    ApprovedRequirementRevision,
    DraftOperation,
    RequirementDraft,
    ReviewResolutionOperation,
)


_TERMINAL_RUN_STATUS_TO_CONVERSATION = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}
_RUNTIME_FINAL_SUMMARY_IDEMPOTENCY_PREFIX = "runtime-final-summary:"
_FINAL_SUMMARY_MESSAGE_IDEMPOTENCY_PREFIX = "final-summary-message:"
_WORKFLOW_COMMAND_MESSAGE_IDEMPOTENCY_PREFIX = "workflow-command-message:"
_RUNTIME_PROGRESS_MESSAGE_IDEMPOTENCY_PREFIX = "runtime-progress-message:"
_AGENT_TURN_STARTED_STALE_SECONDS = 15 * 60
_WORKFLOW_START_OUTBOX_CLAIM_TIMEOUT_SECONDS = 60
_SummaryItemT = TypeVar("_SummaryItemT")


class DraftProtocol(Protocol):
    draft_revision_id: str
    unresolved_review_item_count: int

    def model_dump(self, *, mode: str = "python") -> dict[str, object]: ...


class AdvisoryMemoryContextProtocol(Protocol):
    context_text: str


class MemoryServiceProtocol(Protocol):
    def recall_for_conversation(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        conversation_id: str,
        turn_id: str,
    ) -> AdvisoryMemoryContextProtocol: ...


class ConversationAgentIntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["read_only_question", "next_round_requirement", "unsupported_write"]
    requirement_text: str | None = Field(default=None, max_length=MAX_REQUIREMENT_TEXT_CHARS)
    target_section_hint: str | None = Field(default=None, max_length=MAX_SECTION_HINT_CHARS)
    rationale: str | None = Field(default=None)


@dataclass(frozen=True)
class CompletedMemoryConversation:
    conversation_id: str
    updated_at: str


@dataclass(frozen=True)
class RuntimeRunTarget:
    conversation: ConversationRecord
    link: ConversationRuntimeRunLink

    @property
    def runtime_run_id(self) -> str:
        return self.link.runtime_run_id


class ConversationAgentService:
    def __init__(
        self,
        *,
        store: ConversationStore,
        service_action_adapter: AgentServiceActionAdapter,
        now: Callable[[], str],
        conversation_id_factory: Callable[[], str] | None = None,
        message_id_factory: Callable[[], str] | None = None,
        activity_id_factory: Callable[[], str] | None = None,
        operation_id_factory: Callable[[], str] | None = None,
        summary_id_factory: Callable[[], str] | None = None,
        compaction_id_factory: Callable[[], str] | None = None,
        memory_service: MemoryServiceProtocol | None = None,
        agent_model_name: str = "gpt-4.1-mini",
        agent_instructions: str = "",
        agent_runner: AgentRunner | None = None,
        budget_policy: AgentBudgetPolicy | None = None,
        source_selection_resolver: RuntimeSourceSelectionResolver | None = None,
    ) -> None:
        self.store = store
        self.service_action_adapter = service_action_adapter
        self.now = now
        self.conversation_id_factory = conversation_id_factory or (lambda: f"agent_conv_{uuid4().hex}")
        self.message_id_factory = message_id_factory or (lambda: f"agent_msg_{uuid4().hex}")
        self.activity_id_factory = activity_id_factory or (lambda: f"agent_activity_{uuid4().hex}")
        self.operation_id_factory = operation_id_factory or (lambda: f"agent_operation_audit_{uuid4().hex}")
        self.summary_id_factory = summary_id_factory or (lambda: f"agent_context_summary_{uuid4().hex}")
        self.compaction_id_factory = compaction_id_factory or (lambda: f"agent_compaction_{uuid4().hex}")
        self.memory_service = memory_service
        self.agent_model_name = agent_model_name
        self.agent_instructions = agent_instructions
        self.agent_runner = agent_runner
        self.budget_policy = budget_policy
        self.job_request_store = JobRequestStore(store.path, busy_timeout_ms=store.busy_timeout_ms)
        self.workflow_start_intent_store = WorkflowStartIntentStore(
            store.path,
            busy_timeout_ms=store.busy_timeout_ms,
        )
        self.workflow_confirm_request_store = WorkflowConfirmRequestStore(
            store.path,
            busy_timeout_ms=store.busy_timeout_ms,
        )
        self.outbox_store = WorkbenchOutboxStore(store.path, busy_timeout_ms=store.busy_timeout_ms)
        self.source_selection_resolver = source_selection_resolver or RuntimeSourceSelectionResolver()

    def create_conversation(self, *, owner_user_id: str, workspace_id: str, title: str) -> ConversationRecord:
        return self.store.create_conversation(
            conversation_id=self.conversation_id_factory(),
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            title=title,
            created_at=self.now(),
        )

    async def run_agent_turn(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        user_message: str,
        idempotency_key: str | None = None,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        runtime_target = self._maybe_active_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        if idempotency_key is not None:
            existing = self.store.get_message_by_idempotency(
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if existing.role != "user" or existing.text != user_message:
                    raise ConversationAgentError("idempotency_key_conflict")
                return await self._replay_idempotent_agent_turn(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    user_message_record=existing,
                    idempotency_key=idempotency_key,
                )
        user_token_estimate = _rough_token_estimate(user_message)
        try:
            message = self.store.append_message(
                conversation_id=conversation_id,
                role="user",
                message_type="user_text",
                text=user_message,
                payload={},
                created_at=self.now(),
                message_id=self.message_id_factory(),
                token_count=user_token_estimate,
                source_runtime_run_id=runtime_target.runtime_run_id if runtime_target is not None else None,
                idempotency_key=idempotency_key,
            )
        except sqlite3.IntegrityError:
            if idempotency_key is not None:
                existing = self.store.get_message_by_idempotency(
                    conversation_id=conversation_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    if existing.role != "user" or existing.text != user_message:
                        raise ConversationAgentError("idempotency_key_conflict")
                    return await self._replay_idempotent_agent_turn(
                        conversation_id=conversation_id,
                        owner_user_id=owner_user_id,
                        workspace_id=workspace_id,
                        user_message_record=existing,
                        idempotency_key=idempotency_key,
                    )
            raise
        if runtime_target is not None:
            return await self._run_routed_runtime_user_turn(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                message=message,
                user_message=user_message,
                idempotency_key=idempotency_key,
                target=runtime_target,
            )
        return await self._run_agent_model_after_user_message(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            message=message,
            user_message=user_message,
            idempotency_key=idempotency_key,
        )

    async def _run_agent_model_after_user_message(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        message: TranscriptMessage,
        user_message: str,
        idempotency_key: str | None,
        source_runtime_run_id: str | None = None,
        runtime_task: str | None = None,
        runtime_facts: dict[str, object] | None = None,
    ) -> ConversationAgentResponse:
        prior_messages = [
            item
            for item in self.store.get_messages(conversation_id=conversation_id)
            if item.message_seq < message.message_seq
        ]
        conversation_tokens_before_turn = _conversation_token_count(prior_messages)
        advisory_context = ""
        if self.memory_service is not None:
            memory_context = self.memory_service.recall_for_conversation(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn_id=message.message_id,
            )
            advisory_context = memory_context.context_text
        latest_summary = self.store.get_latest_context_summary(conversation_id=conversation_id)
        included_recent_messages = [item for item in prior_messages if item.model_input_included]
        model_input = build_cache_ready_model_input(
            latest_context_summary=latest_summary.summary_text if latest_summary is not None else None,
            recent_transcript=[
                ModelInputTranscriptMessage(
                    message_seq=item.message_seq,
                    role=item.role,
                    message_type=item.message_type,
                    text=item.text,
                )
                for item in included_recent_messages
            ],
            advisory_memory_context=advisory_context,
            current_user_message=user_message,
            runtime_task=runtime_task,
            runtime_facts=runtime_facts,
        )
        estimated_input_tokens = _model_input_content_token_estimate(
            instructions_text=self.agent_instructions,
            latest_context_summary=latest_summary.summary_text if latest_summary is not None else None,
            recent_messages=included_recent_messages,
            advisory_context=advisory_context,
            current_user_message=user_message,
            runtime_task=runtime_task,
            runtime_facts=runtime_facts,
        )
        operation_id = self.operation_id_factory()
        started_at = self.now()
        self.store.save_operation_audit(
            operation_id=operation_id,
            conversation_id=conversation_id,
            operation_name="agent_model_run",
            execution_origin="model",
            status="started",
            args={
                "modelName": self.agent_model_name,
                "estimatedInputTokens": estimated_input_tokens,
                "conversationTokensBeforeTurn": conversation_tokens_before_turn,
                "idempotencyKey": idempotency_key,
            },
            result=None,
            reason_code=None,
            started_at=started_at,
        )
        if self.budget_policy is not None:
            monthly_cost_cents_before_turn = self._monthly_cost_cents(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            try:
                self.budget_policy.check_turn(
                    estimated_input_tokens=estimated_input_tokens,
                    estimated_output_tokens=self.budget_policy.turn_output_token_budget,
                    conversation_tokens_before_turn=conversation_tokens_before_turn,
                    monthly_cost_cents_before_turn=monthly_cost_cents_before_turn,
                )
            except ConversationAgentError as exc:
                self._mark_agent_model_run_failed(
                    operation_id=operation_id,
                    conversation_id=conversation_id,
                    started_at=started_at,
                    reason_code=exc.reason_code,
                    result={"estimatedInputTokens": estimated_input_tokens},
                )
                raise
        runtime = AgentRuntime(
            model_name=self.agent_model_name,
            instructions=self.agent_instructions,
            runner=self.agent_runner,
        )
        try:
            result = await runtime.run(model_input)
            result_text = _agent_result_text(result)
            usage = _extract_provider_usage(result)
            estimated_output_tokens = _rough_token_estimate(result_text)
            if not usage["hasProviderUsage"]:
                usage["inputTokens"] = estimated_input_tokens
                usage["outputTokens"] = estimated_output_tokens
                usage["totalTokens"] = estimated_input_tokens + estimated_output_tokens
            usage_result: dict[str, object] = {
                "assistantText": result_text,
                "assistantPayload": _object_payload(result),
                "estimatedInputTokens": estimated_input_tokens,
                "estimatedOutputTokens": estimated_output_tokens,
                "reportedInputTokens": usage["inputTokens"],
                "reportedOutputTokens": usage["outputTokens"],
                "reportedTotalTokens": usage["totalTokens"],
                "estimatedCostCents": usage["estimatedCostCents"],
                "reportedCostCents": usage["costCents"],
                "hasProviderUsage": usage["hasProviderUsage"],
            }
            if self.budget_policy is not None:
                self.budget_policy.check_provider_report(
                    estimated_input_tokens=estimated_input_tokens,
                    estimated_output_tokens=estimated_output_tokens,
                    reported_input_tokens=usage["inputTokens"],
                    reported_output_tokens=usage["outputTokens"],
                    estimated_cost_cents=usage["estimatedCostCents"],
                    reported_cost_cents=usage["costCents"],
                )
                self.budget_policy.check_monthly_cost_after_turn(
                    monthly_cost_cents_before_turn=monthly_cost_cents_before_turn,
                    reported_turn_cost_cents=int(usage["costCents"]),
                )
        except ConversationAgentError as exc:
            self._mark_agent_model_run_failed(
                operation_id=operation_id,
                conversation_id=conversation_id,
                started_at=started_at,
                reason_code=exc.reason_code,
                result=locals().get("usage_result", {"estimatedInputTokens": estimated_input_tokens}),
            )
            raise
        self.store.save_operation_audit(
            operation_id=operation_id,
            conversation_id=conversation_id,
            operation_name="agent_model_run",
            execution_origin="model",
            status="completed",
            args={
                "modelName": self.agent_model_name,
                "estimatedInputTokens": estimated_input_tokens,
                "conversationTokensBeforeTurn": conversation_tokens_before_turn,
                "idempotencyKey": idempotency_key,
            },
            result=usage_result,
            reason_code=None,
            started_at=started_at,
            completed_at=self.now(),
        )
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="assistant_text",
            text=result_text,
            payload={"agentResult": _object_payload(result)},
            created_at=self.now(),
            message_id=self.message_id_factory(),
            token_count=estimated_output_tokens,
            source_operation_id=operation_id,
            source_runtime_run_id=source_runtime_run_id,
            idempotency_key=_assistant_message_idempotency_key(idempotency_key),
            return_existing_on_idempotency=idempotency_key is not None,
        )
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
        )

    async def _replay_idempotent_agent_turn(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        user_message_record: TranscriptMessage,
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        operation_audit = self._agent_model_run_by_idempotency(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if operation_audit is None:
            assistant_idempotency_key = _assistant_message_idempotency_key(idempotency_key)
            if assistant_idempotency_key is None:
                raise ConversationAgentError("agent_idempotency_key_required")
            assistant_replay = self.store.get_message_by_idempotency(
                conversation_id=conversation_id,
                idempotency_key=assistant_idempotency_key,
            )
            if assistant_replay is not None:
                return self._reopened_response(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                )
            target = self._maybe_active_runtime_run_target(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            if target is not None:
                return await self._run_routed_runtime_user_turn(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    message=user_message_record,
                    user_message=user_message_record.text,
                    idempotency_key=idempotency_key,
                    target=target,
                )
            return await self._run_agent_model_after_user_message(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                message=user_message_record,
                user_message=user_message_record.text,
                idempotency_key=idempotency_key,
            )
        if operation_audit.status == "started":
            if self._agent_operation_audit_is_stale(operation_audit):
                self._mark_agent_model_run_failed(
                    operation_id=operation_audit.operation_id,
                    conversation_id=conversation_id,
                    started_at=operation_audit.started_at,
                    reason_code="agent_request_stale",
                    result={"idempotencyKey": idempotency_key},
                )
                raise ConversationAgentError("agent_request_stale")
            raise ConversationAgentError("agent_request_in_progress")
        if operation_audit.status == "failed":
            raise ConversationAgentError(
                operation_audit.reason_code or "agent_request_failed",
                payload=operation_audit.result or {},
            )
        if operation_audit.status == "completed" and self._assistant_message_exists(
            conversation_id=conversation_id,
            operation_id=operation_audit.operation_id,
        ):
            return self._reopened_response(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
        if operation_audit.status == "completed" and self._restore_assistant_message_from_operation_audit(
            conversation_id=conversation_id,
            operation_audit=operation_audit,
            idempotency_key=idempotency_key,
        ):
            return self._reopened_response(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
        self._mark_agent_model_run_failed(
            operation_id=operation_audit.operation_id,
            conversation_id=conversation_id,
            started_at=operation_audit.started_at,
            reason_code="agent_request_recovery_failed",
            result={"idempotencyKey": idempotency_key},
        )
        raise ConversationAgentError("agent_request_recovery_failed")

    def _agent_model_run_by_idempotency(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
    ):
        matching = [
            call
            for call in self.store.list_operation_audits(conversation_id=conversation_id)
            if call.operation_name == "agent_model_run" and call.args.get("idempotencyKey") == idempotency_key
        ]
        return matching[-1] if matching else None

    def _agent_intent_route_by_idempotency(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> OperationAuditRecord | None:
        matching = [
            call
            for call in self.store.list_operation_audits(conversation_id=conversation_id)
            if call.operation_name == "agent_intent_route" and call.args.get("idempotencyKey") == idempotency_key
        ]
        return matching[-1] if matching else None

    def _assistant_message_exists(self, *, conversation_id: str, operation_id: str) -> bool:
        return any(
            message.role == "assistant" and message.source_operation_id == operation_id
            for message in self.store.get_messages(conversation_id=conversation_id)
        )

    def _restore_assistant_message_from_operation_audit(
        self,
        *,
        conversation_id: str,
        operation_audit: OperationAuditRecord,
        idempotency_key: str,
    ) -> bool:
        result = operation_audit.result or {}
        assistant_text = result.get("assistantText")
        if not isinstance(assistant_text, str):
            return False
        assistant_payload = result.get("assistantPayload")
        token_count = result.get("estimatedOutputTokens")
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="assistant_text",
            text=assistant_text,
            payload={"agentResult": assistant_payload if isinstance(assistant_payload, dict) else {}},
            created_at=self.now(),
            message_id=self.message_id_factory(),
            token_count=token_count if isinstance(token_count, int) else _rough_token_estimate(assistant_text),
            source_operation_id=operation_audit.operation_id,
            idempotency_key=_assistant_message_idempotency_key(idempotency_key),
            return_existing_on_idempotency=True,
        )
        return True

    def _agent_operation_audit_is_stale(self, operation_audit: OperationAuditRecord) -> bool:
        return _parse_time(self.now()) - _parse_time(operation_audit.started_at) > timedelta(
            seconds=_AGENT_TURN_STARTED_STALE_SECONDS
        )

    def _reopened_response(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
    ) -> ConversationAgentResponse:
        final_summary = None
        target = self._maybe_active_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        if target is not None:
            final_summary = self._ensure_final_summary_for_runtime(
                target=target,
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                display_instruction=None,
            )
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
            final_summary=final_summary,
        )

    def _mark_agent_model_run_failed(
        self,
        *,
        operation_id: str,
        conversation_id: str,
        started_at: str,
        reason_code: str,
        result: dict[str, object] | None,
    ) -> None:
        self.store.save_operation_audit(
            operation_id=operation_id,
            conversation_id=conversation_id,
            operation_name="agent_model_run",
            execution_origin="model",
            status="failed",
            args={"modelName": self.agent_model_name},
            result=result,
            reason_code=reason_code,
            started_at=started_at,
            completed_at=self.now(),
        )

    def _mark_agent_intent_route_failed(
        self,
        *,
        operation_id: str,
        conversation_id: str,
        started_at: str,
        reason_code: str,
        result: dict[str, object] | None,
        args: dict[str, object] | None = None,
        runtime_run_id: str | None = None,
    ) -> None:
        default_args: dict[str, object] = {"modelName": self.agent_model_name, "runtimeRunId": runtime_run_id}
        self.store.save_operation_audit(
            operation_id=operation_id,
            conversation_id=conversation_id,
            operation_name="agent_intent_route",
            execution_origin="service",
            status="failed",
            args=args or default_args,
            result=result,
            reason_code=reason_code,
            started_at=started_at,
            completed_at=self.now(),
            runtime_run_id=runtime_run_id,
        )

    def _monthly_cost_cents(self, *, owner_user_id: str, workspace_id: str) -> int:
        total = 0
        for conversation in self.store.list_conversations(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            include_archived=True,
        ):
            for call in self.store.list_operation_audits(conversation_id=conversation.conversation_id):
                if call.operation_name != "agent_model_run" or call.status != "completed" or call.result is None:
                    continue
                value = call.result.get("reportedCostCents")
                if isinstance(value, int):
                    total += value
        return total

    def list_conversations(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        include_archived: bool = False,
    ) -> list[ConversationRecord]:
        return self.store.list_conversations(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            include_archived=include_archived,
        )

    def eligible_completed_conversations(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        max_age_days: int,
        min_idle_hours: int,
        now: str,
        limit: int,
    ) -> list[CompletedMemoryConversation]:
        conversations = self.store.list_conversations(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            include_archived=True,
        )
        cutoff_old = _format_time(_parse_time(now) - timedelta(days=max_age_days))
        cutoff_idle = _format_time(_parse_time(now) - timedelta(hours=min_idle_hours))
        eligible: list[CompletedMemoryConversation] = []
        for conversation in conversations:
            if conversation.created_at < cutoff_old:
                continue
            terminal = conversation.status in {"completed", "failed", "cancelled"}
            idle_or_archived = conversation.is_archived or conversation.updated_at <= cutoff_idle
            if not terminal and not idle_or_archived:
                continue
            source_updated_at = self._memory_source_updated_at(conversation.conversation_id)
            if source_updated_at is None:
                continue
            eligible.append(
                CompletedMemoryConversation(
                    conversation_id=conversation.conversation_id,
                    updated_at=source_updated_at,
                )
            )
        return eligible[:limit]

    def read_memory_transcript_items(self, *, conversation_id: str) -> list[dict[str, object]]:
        messages = self.store.get_messages(conversation_id=conversation_id)
        activities = self.store.get_activity_items(conversation_id=conversation_id)
        items: list[dict[str, object]] = [
            {
                "item_id": message.message_id,
                "item_kind": "message",
                "role": message.role,
                "text": message.text,
                "payload": message.payload,
                "created_at": message.created_at,
            }
            for message in messages
        ]
        for activity in activities:
            items.append(
                {
                "item_id": activity.activity_id,
                "item_kind": "activity",
                "role": "activity",
                "text": activity.summary,
                "payload": activity.payload,
                "created_at": activity.updated_at,
                }
            )
        return sorted(items, key=lambda item: str(item["created_at"]))

    def reopen_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
    ) -> ConversationThreadView:
        thread = self.store.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            opened_at=self.now(),
        )
        intent = self.workflow_start_intent_store.get_latest_for_conversation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        if intent is None:
            return thread
        return thread.model_copy(
            update={
                "conversation_reopen_state": thread.conversation_reopen_state.model_copy(
                    update={"workflow_start_intent_id": intent.workflow_start_intent_id}
                )
            }
        )

    def rename_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        title: str,
    ) -> ConversationRecord:
        return self.store.rename_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            title=title,
            updated_at=self.now(),
        )

    def archive_conversation(self, *, conversation_id: str, owner_user_id: str, workspace_id: str) -> ConversationRecord:
        return self.store.archive_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            archived_at=self.now(),
        )

    def unarchive_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
    ) -> ConversationRecord:
        return self.store.unarchive_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            updated_at=self.now(),
        )

    def get_job_request_revision(self, job_request_revision_id: str) -> JobRequestRevision:
        revision = self.job_request_store.get_job_request_revision(job_request_revision_id)
        if revision is None:
            raise ConversationAgentError("job_request_revision_not_found")
        return revision

    def get_requirement_draft_job_request_link(
        self,
        requirement_draft_revision_id: str,
    ) -> RequirementDraftJobRequestLink:
        link = self.job_request_store.get_requirement_draft_job_request_link(requirement_draft_revision_id)
        if link is None:
            raise ConversationAgentError("requirement_draft_job_request_not_found")
        return link

    def submit_jd(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
        idempotency_key: str,
        source_kinds: Sequence[str] | None = None,
        source_ids: Sequence[str] | None = None,
        workspace_source_policy_id: str | None = None,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        normalized_source_kinds = _resolve_submit_jd_source_kinds(
            source_kinds=source_kinds,
            source_ids=source_ids,
        )
        user_job_title = _normalize_optional_job_title(job_title)
        job_request = self.job_request_store.insert_job_request_revision(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            jd_text=jd_text,
            user_job_title=user_job_title,
            extracted_job_title=None,
            notes=notes,
            source_kinds=list(normalized_source_kinds),
            workspace_source_policy_id=workspace_source_policy_id,
            idempotency_key=idempotency_key,
            created_at=self.now(),
        )
        existing_link = self.job_request_store.get_requirement_draft_job_request_link_by_job_request(
            job_request.job_request_revision_id
        )
        if existing_link is not None:
            draft = self.service_action_adapter.get_requirement_draft(
                conversation_id=conversation_id,
                draft_revision_id=existing_link.draft_revision_id,
            )
            operation_audit = self._ensure_submit_jd_extract_operation_audit_completed(
                conversation_id=conversation_id,
                job_request=job_request,
                draft_revision_id=existing_link.draft_revision_id,
            )
            conversation_after_draft_repair = self.store.link_requirement_draft(
                conversation_id=conversation_id,
                draft_revision_id=existing_link.draft_revision_id,
                pending_requirement_review_count=draft.unresolved_review_item_count,
                updated_at=self.now(),
            )
            if _should_repair_submit_replay_status(conversation_after_draft_repair):
                self.store.update_conversation_status(
                    conversation_id=conversation_id,
                    status="awaiting_requirement_confirmation",
                    updated_at=self.now(),
                )
            self._ensure_requirement_review_message(
                conversation_id=conversation_id,
                draft=draft,
                source_operation_id=operation_audit.operation_id,
                idempotency_key=idempotency_key,
            )
            reopened = self.reopen_conversation(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            return ConversationAgentResponse(
                conversation_reopen_state=reopened.conversation_reopen_state,
                messages=reopened.messages,
                activity_items=reopened.activity_items,
                requirement_draft=draft,
                job_request_revision_id=job_request.job_request_revision_id,
                requirement_draft_revision_id=existing_link.draft_revision_id,
            )
        user_message = self._ensure_submit_jd_user_message(
            conversation_id=conversation_id,
            job_request=job_request,
            idempotency_key=idempotency_key,
        )
        operation_audit = self._extract_requirements_operation_audit_for_job_request(
            conversation_id=conversation_id,
            job_request=job_request,
        )
        draft = self._extract_or_recover_requirement_draft(
            conversation_id=conversation_id,
            job_request=job_request,
            operation_audit=operation_audit,
            idempotency_key=idempotency_key,
        )
        operation_id = operation_audit.operation_id
        extracted_job_title = _extracted_job_title_from_runtime_control(
            self.service_action_adapter,
            draft_revision_id=draft.draft_revision_id,
        )
        job_request = self.job_request_store.update_extracted_job_title(
            job_request_revision_id=job_request.job_request_revision_id,
            extracted_job_title=extracted_job_title,
            updated_at=self.now(),
        )
        effective_job_title = job_request.effective_job_title
        if effective_job_title is None:
            self.store.save_operation_audit(
                operation_id=operation_id,
                conversation_id=conversation_id,
                operation_name="extract_requirements",
                execution_origin="service",
                status="failed",
                args={
                    "jobTitle": user_job_title,
                    "sourceKinds": list(normalized_source_kinds),
                    "jobRequestRevisionId": job_request.job_request_revision_id,
                },
                result={"draftRevisionId": draft.draft_revision_id},
                reason_code="job_request_title_required",
                started_at=self.now(),
                completed_at=self.now(),
            )
            raise ConversationAgentError("job_request_title_required")
        self._mark_submit_jd_extract_operation_audit_completed(
            conversation_id=conversation_id,
            job_request=job_request,
            operation_id=operation_id,
            extracted_job_title=extracted_job_title,
            effective_job_title=effective_job_title,
            draft_revision_id=draft.draft_revision_id,
        )
        self.job_request_store.link_requirement_draft_job_request(
            draft_revision_id=draft.draft_revision_id,
            workspace_id=workspace_id,
            job_request_revision_id=job_request.job_request_revision_id,
            conversation_id=conversation_id,
            created_at=self.now(),
        )
        self.store.link_requirement_draft(
            conversation_id=conversation_id,
            draft_revision_id=draft.draft_revision_id,
            pending_requirement_review_count=draft.unresolved_review_item_count,
            updated_at=self.now(),
        )
        self.store.update_conversation_status(
            conversation_id=conversation_id,
            status="awaiting_requirement_confirmation",
            updated_at=self.now(),
        )
        assistant_message = self._ensure_requirement_review_message(
            conversation_id=conversation_id,
            source_operation_id=operation_id,
            draft=draft,
            idempotency_key=idempotency_key,
        )
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=[user_message, assistant_message],
            activity_items=reopened.activity_items,
            requirement_draft=_requirement_draft_response(draft),
            job_request_revision_id=job_request.job_request_revision_id,
            requirement_draft_revision_id=draft.draft_revision_id,
        )

    def _ensure_submit_jd_user_message(
        self,
        *,
        conversation_id: str,
        job_request: JobRequestRevision,
        idempotency_key: str,
    ) -> TranscriptMessage:
        existing = self._submit_jd_user_message_for_job_request(
            conversation_id=conversation_id,
            job_request_revision_id=job_request.job_request_revision_id,
        )
        if existing is not None:
            return existing
        return self.store.append_message(
            conversation_id=conversation_id,
            role="user",
            message_type="user_text",
            text=job_request.jd_text,
            payload={
                "jobTitle": job_request.user_job_title,
                "notes": job_request.notes,
                "sourceKinds": list(job_request.source_kinds),
                "jobRequestRevisionId": job_request.job_request_revision_id,
            },
            created_at=self.now(),
            message_id=self.message_id_factory(),
            idempotency_key=f"{idempotency_key}:user",
            return_existing_on_idempotency=True,
        )

    def _submit_jd_user_message_for_job_request(
        self,
        *,
        conversation_id: str,
        job_request_revision_id: str,
    ) -> TranscriptMessage | None:
        for message in self.store.get_messages(conversation_id=conversation_id):
            if message.role != "user" or message.message_type != "user_text":
                continue
            if message.payload.get("jobRequestRevisionId") == job_request_revision_id:
                return message
        return None

    def _extract_requirements_operation_audit_for_job_request(
        self,
        *,
        conversation_id: str,
        job_request: JobRequestRevision,
    ) -> OperationAuditRecord:
        existing = self._find_extract_requirements_operation_audit(
            conversation_id=conversation_id,
            job_request_revision_id=job_request.job_request_revision_id,
        )
        if existing is not None:
            return existing
        return self.store.save_operation_audit(
            operation_id=self.operation_id_factory(),
            conversation_id=conversation_id,
            operation_name="extract_requirements",
            execution_origin="service",
            status="started",
            args={
                "jobTitle": job_request.user_job_title,
                "sourceKinds": list(job_request.source_kinds),
                "jobRequestRevisionId": job_request.job_request_revision_id,
            },
            result=None,
            reason_code=None,
            started_at=self.now(),
        )

    def _find_extract_requirements_operation_audit(
        self,
        *,
        conversation_id: str,
        job_request_revision_id: str,
    ) -> OperationAuditRecord | None:
        matching = [
            call
            for call in self.store.list_operation_audits(conversation_id=conversation_id)
            if call.operation_name == "extract_requirements"
            and call.args.get("jobRequestRevisionId") == job_request_revision_id
        ]
        if not matching:
            return None
        completed = [call for call in matching if call.status == "completed"]
        return (completed or matching)[-1]

    def _extract_or_recover_requirement_draft(
        self,
        *,
        conversation_id: str,
        job_request: JobRequestRevision,
        operation_audit: OperationAuditRecord,
        idempotency_key: str,
    ) -> DraftProtocol:
        if operation_audit.status == "completed":
            draft_revision_id = _operation_audit_draft_revision_id(operation_audit)
            if draft_revision_id is not None:
                return self.service_action_adapter.get_requirement_draft(
                    conversation_id=conversation_id,
                    draft_revision_id=draft_revision_id,
                )
        return self.service_action_adapter.extract_requirements(
            conversation_id=conversation_id,
            job_title=job_request.user_job_title,
            jd_text=job_request.jd_text,
            notes=job_request.notes,
            source_ids=list(job_request.source_kinds),
            idempotency_key=idempotency_key,
        )

    def _ensure_submit_jd_extract_operation_audit_completed(
        self,
        *,
        conversation_id: str,
        job_request: JobRequestRevision,
        draft_revision_id: str,
    ) -> OperationAuditRecord:
        operation_audit = self._extract_requirements_operation_audit_for_job_request(
            conversation_id=conversation_id,
            job_request=job_request,
        )
        if operation_audit.status == "completed" and _operation_audit_draft_revision_id(operation_audit) == draft_revision_id:
            return operation_audit
        effective_job_title = job_request.effective_job_title
        if effective_job_title is None:
            effective_job_title = _extracted_job_title_from_runtime_control(
                self.service_action_adapter,
                draft_revision_id=draft_revision_id,
            )
        if effective_job_title is None:
            effective_job_title = job_request.user_job_title
        if effective_job_title is None:
            return operation_audit
        return self._mark_submit_jd_extract_operation_audit_completed(
            conversation_id=conversation_id,
            job_request=job_request,
            operation_id=operation_audit.operation_id,
            extracted_job_title=job_request.extracted_job_title,
            effective_job_title=effective_job_title,
            draft_revision_id=draft_revision_id,
        )

    def _mark_submit_jd_extract_operation_audit_completed(
        self,
        *,
        conversation_id: str,
        job_request: JobRequestRevision,
        operation_id: str,
        extracted_job_title: str | None,
        effective_job_title: str,
        draft_revision_id: str,
    ) -> OperationAuditRecord:
        return self.store.save_operation_audit(
            operation_id=operation_id,
            conversation_id=conversation_id,
            operation_name="extract_requirements",
            execution_origin="service",
            status="completed",
            args={
                "jobTitle": job_request.user_job_title,
                "extractedJobTitle": extracted_job_title,
                "effectiveJobTitle": effective_job_title,
                "sourceKinds": list(job_request.source_kinds),
                "jobRequestRevisionId": job_request.job_request_revision_id,
            },
            result={"draftRevisionId": draft_revision_id},
            reason_code=None,
            started_at=self.now(),
            completed_at=self.now(),
        )

    def _ensure_requirement_review_message(
        self,
        *,
        conversation_id: str,
        draft: DraftProtocol,
        source_operation_id: str,
        idempotency_key: str,
    ) -> TranscriptMessage:
        existing = self._requirement_review_message_for_draft(
            conversation_id=conversation_id,
            draft_revision_id=str(getattr(draft, "draft_revision_id")),
        )
        if existing is not None:
            self.store.ensure_requirement_transcript_snapshot(message_id=existing.message_id)
            return existing
        message = self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="requirement_review",
            text="已拆解岗位需求，请确认后再启动检索。",
            payload=_requirement_review_payload(draft),
            source_operation_id=source_operation_id,
            created_at=self.now(),
            message_id=self.message_id_factory(),
            idempotency_key=f"{idempotency_key}:requirement-review",
            return_existing_on_idempotency=True,
        )
        self.store.ensure_requirement_transcript_snapshot(message_id=message.message_id)
        return message

    def _requirement_review_message_for_draft(
        self,
        *,
        conversation_id: str,
        draft_revision_id: str,
    ) -> TranscriptMessage | None:
        for message in self.store.get_messages(conversation_id=conversation_id):
            if message.role != "assistant" or message.message_type != "requirement_review":
                continue
            payload_draft_id = message.payload.get("requirementDraftId")
            snapshot = message.payload.get("requirementDraftSnapshot")
            snapshot_draft_id = _mapping_value(snapshot, "draftRevisionId")
            if payload_draft_id == draft_revision_id or snapshot_draft_id == draft_revision_id:
                return message
        return None

    def update_requirement_draft(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        draft_revision_id: str,
        base_revision_id: str,
        operations: list[dict[str, object] | DraftOperation],
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        try:
            parsed = TypeAdapter(list[DraftOperation]).validate_python(operations)
        except ValidationError as exc:
            raise ConversationAgentError("agent_request_invalid", payload={"errors": exc.errors()}) from exc
        try:
            draft = self.service_action_adapter.update_requirement_draft(
                draft_revision_id=draft_revision_id,
                base_revision_id=base_revision_id,
                operations=parsed,
                idempotency_key=idempotency_key,
            )
        except RuntimeControlError as exc:
            raise _conversation_error_from_runtime_control(exc) from exc
        return self._persist_draft_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            draft=draft,
            source_draft_revision_id=draft_revision_id,
            idempotency_key=idempotency_key,
        )

    def amend_requirement_draft_from_text(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        draft_revision_id: str,
        base_revision_id: str,
        text: str,
        target_section_hint: str | None,
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        safe_text = screen_requirement_text(text)
        try:
            draft = self.service_action_adapter.amend_requirement_draft_from_text(
                draft_revision_id=draft_revision_id,
                base_revision_id=base_revision_id,
                text=safe_text,
                target_section_hint=target_section_hint,
                idempotency_key=idempotency_key,
            )
        except RuntimeControlError as exc:
            raise _conversation_error_from_runtime_control(exc) from exc
        return self._persist_draft_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            draft=draft,
            source_draft_revision_id=draft_revision_id,
            idempotency_key=idempotency_key,
        )

    def resolve_requirement_review(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        draft_revision_id: str,
        base_revision_id: str,
        amendment_id: str,
        operations: list[dict[str, object] | ReviewResolutionOperation],
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        try:
            parsed = TypeAdapter(list[ReviewResolutionOperation]).validate_python(operations)
        except ValidationError as exc:
            raise ConversationAgentError("agent_request_invalid", payload={"errors": exc.errors()}) from exc
        try:
            draft = self.service_action_adapter.resolve_requirement_review(
                draft_revision_id=draft_revision_id,
                base_revision_id=base_revision_id,
                amendment_id=amendment_id,
                operations=parsed,
                idempotency_key=idempotency_key,
            )
        except RuntimeControlError as exc:
            raise _conversation_error_from_runtime_control(exc) from exc
        return self._persist_draft_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            draft=draft,
            source_draft_revision_id=draft_revision_id,
            idempotency_key=idempotency_key,
        )

    def confirm_requirements(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        draft_revision_id: str,
        base_revision_id: str | None = None,
        expected_draft_revision_id: str | None = None,
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        conversation = self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        expected_revision_id = expected_draft_revision_id or base_revision_id
        if expected_revision_id is None:
            raise ConversationAgentError("requirement_draft_base_revision_required")
        existing_confirm_request = self.workflow_confirm_request_store.get_by_idempotency_key(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing_confirm_request is not None and existing_confirm_request.draft_revision_id != draft_revision_id:
            raise ConversationAgentError("idempotency_key_conflict")
        job_request = self._require_confirmable_job_request(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            draft_revision_id=draft_revision_id,
        )
        confirm_request_hash = workflow_confirm_request_hash(
            draft_revision_id=draft_revision_id,
            expected_draft_revision_id=expected_revision_id,
            job_request_revision_id=job_request.job_request_revision_id,
            job_request_request_hash=job_request.request_hash,
            source_kinds=job_request.source_kinds,
            workspace_source_policy_id=job_request.workspace_source_policy_id,
        )
        if existing_confirm_request is not None and existing_confirm_request.request_hash != confirm_request_hash:
            raise ConversationAgentError("idempotency_key_conflict")
        existing_by_key = self.workflow_start_intent_store.get_by_idempotency_key(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing_by_key is not None:
            if existing_by_key.draft_revision_id != draft_revision_id:
                raise ConversationAgentError("idempotency_key_conflict")
            request_hash = workflow_start_request_hash(
                draft_revision_id=draft_revision_id,
                expected_draft_revision_id=expected_revision_id,
                approved_requirement_revision_id=existing_by_key.approved_requirement_revision_id,
                job_request_revision_id=job_request.job_request_revision_id,
                job_request_request_hash=job_request.request_hash,
                source_kinds=job_request.source_kinds,
                workspace_source_policy_id=job_request.workspace_source_policy_id,
            )
            if existing_by_key.request_hash != request_hash:
                raise ConversationAgentError("idempotency_key_conflict")
            if existing_confirm_request is None:
                self.workflow_confirm_request_store.create_or_get(
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    conversation_id=conversation_id,
                    draft_revision_id=draft_revision_id,
                    expected_draft_revision_id=expected_revision_id,
                    job_request_revision_id=job_request.job_request_revision_id,
                    idempotency_key=idempotency_key,
                    request_hash=confirm_request_hash,
                    approved_requirement_revision_id=existing_by_key.approved_requirement_revision_id,
                    status="intent_created",
                    now=self.now(),
                )
            return self._confirmed_intent_response(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                draft_revision_id=draft_revision_id,
                job_request_revision_id=existing_by_key.job_request_revision_id,
                workflow_start_intent_id=existing_by_key.workflow_start_intent_id,
            )
        existing_by_draft = self.workflow_start_intent_store.get_by_draft(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            draft_revision_id=draft_revision_id,
        )
        if existing_by_draft is not None:
            if (
                existing_confirm_request is None
                and (conversation.latest_draft_revision_id != draft_revision_id or expected_revision_id != draft_revision_id)
            ):
                raise ConversationAgentError(
                    "requirement_draft_stale",
                    payload={"latestDraftRevisionId": conversation.latest_draft_revision_id},
                )
            self.workflow_confirm_request_store.create_or_get(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                draft_revision_id=draft_revision_id,
                expected_draft_revision_id=expected_revision_id,
                job_request_revision_id=job_request.job_request_revision_id,
                idempotency_key=idempotency_key,
                request_hash=confirm_request_hash,
                approved_requirement_revision_id=existing_by_draft.approved_requirement_revision_id,
                status="intent_created",
                now=self.now(),
            )
            return self._confirmed_intent_response(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                draft_revision_id=draft_revision_id,
                job_request_revision_id=existing_by_draft.job_request_revision_id,
                workflow_start_intent_id=existing_by_draft.workflow_start_intent_id,
            )
        if (
            existing_confirm_request is None
            and (conversation.latest_draft_revision_id != draft_revision_id or expected_revision_id != draft_revision_id)
        ):
            raise ConversationAgentError(
                "requirement_draft_stale",
                payload={"latestDraftRevisionId": conversation.latest_draft_revision_id},
            )
        if existing_confirm_request is not None:
            confirm_request = existing_confirm_request
        else:
            confirm_request = self.workflow_confirm_request_store.create_or_get(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                conversation_id=conversation_id,
                draft_revision_id=draft_revision_id,
                expected_draft_revision_id=expected_revision_id,
                job_request_revision_id=job_request.job_request_revision_id,
                idempotency_key=idempotency_key,
                request_hash=confirm_request_hash,
                now=self.now(),
            )
        approved = self._confirm_or_recover_approved_requirement(
            conversation_id=conversation.conversation_id,
            draft_revision_id=draft_revision_id,
            base_revision_id=expected_revision_id,
            idempotency_key=idempotency_key,
        )
        self.workflow_confirm_request_store.mark_approved(
            confirm_request.confirm_request_id,
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            updated_at=self.now(),
        )
        request_hash = workflow_start_request_hash(
            draft_revision_id=draft_revision_id,
            expected_draft_revision_id=expected_revision_id,
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            job_request_revision_id=job_request.job_request_revision_id,
            job_request_request_hash=job_request.request_hash,
            source_kinds=job_request.source_kinds,
            workspace_source_policy_id=job_request.workspace_source_policy_id,
        )
        intent = self.workflow_start_intent_store.create_or_get_confirmed_draft_intent(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            conversation_id=conversation_id,
            draft_revision_id=draft_revision_id,
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            job_request_revision_id=job_request.job_request_revision_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            now=self.now(),
        )
        self.workflow_confirm_request_store.mark_intent_created(
            confirm_request.confirm_request_id,
            updated_at=self.now(),
        )
        return self._confirmed_intent_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            draft_revision_id=draft_revision_id,
            job_request_revision_id=job_request.job_request_revision_id,
            workflow_start_intent_id=intent.workflow_start_intent_id,
        )

    def _require_confirmable_job_request(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        draft_revision_id: str,
    ) -> JobRequestRevision:
        job_request_link = self.job_request_store.get_requirement_draft_job_request_link(draft_revision_id)
        if job_request_link is None:
            raise ConversationAgentError("requirement_draft_job_request_not_found")
        if job_request_link.workspace_id != workspace_id or job_request_link.conversation_id != conversation_id:
            raise ConversationAgentError("requirement_draft_job_request_not_found")
        job_request = self.job_request_store.get_job_request_revision(job_request_link.job_request_revision_id)
        if job_request is None:
            raise ConversationAgentError("job_request_revision_not_found")
        if job_request.effective_job_title is None:
            raise ConversationAgentError("job_title_missing")
        return job_request

    def _confirm_or_recover_approved_requirement(
        self,
        *,
        conversation_id: str,
        draft_revision_id: str,
        base_revision_id: str,
        idempotency_key: str,
    ) -> ApprovedRequirementRevision:
        if self.service_action_adapter.runtime_store is not None:
            approved = self.service_action_adapter.runtime_store.get_approved_requirement_by_idempotency(
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            )
            if approved is not None:
                if approved.draft_revision_id != draft_revision_id:
                    raise ConversationAgentError("idempotency_key_conflict")
                return approved
        try:
            return self.service_action_adapter.confirm_requirements(
                draft_revision_id=draft_revision_id,
                base_revision_id=base_revision_id,
                idempotency_key=idempotency_key,
            )
        except RuntimeControlError as exc:
            raise _conversation_error_from_runtime_control(exc) from exc

    def process_workflow_start_outbox_item(self, outbox_id: str) -> WorkflowStartIntent:
        current_item = self.outbox_store.get(outbox_id)
        if current_item.event_type != "workflow_start_requested":
            raise ConversationAgentError("workbench_outbox_event_type_invalid")
        if current_item.status == "done":
            return self.workflow_start_intent_store.get(current_item.aggregate_id)
        claimed_at = self.now()
        reclaim_before = _format_time(
            _parse_time(claimed_at) - timedelta(seconds=_WORKFLOW_START_OUTBOX_CLAIM_TIMEOUT_SECONDS)
        )
        item = self.outbox_store.claim_for_processing(
            outbox_id,
            claimed_at=claimed_at,
            reclaim_before=reclaim_before,
        )
        if item is None:
            return self.workflow_start_intent_store.get(current_item.aggregate_id)
        intent = self.workflow_start_intent_store.get(item.aggregate_id)
        if intent.status == "started":
            if intent.runtime_run_id is not None:
                self._link_started_workflow_run(intent, runtime_run_id=intent.runtime_run_id)
            self.outbox_store.mark_done(outbox_id, updated_at=self.now())
            return self.workflow_start_intent_store.get(intent.workflow_start_intent_id)
        if intent.status in {"failed", "cancelled"}:
            self.outbox_store.mark_done(outbox_id, updated_at=self.now())
            return intent

        if self.service_action_adapter.runtime_store is None:
            raise ConversationAgentError("runtime_control_store_required")
        approved = self.service_action_adapter.runtime_store.get_approved_requirement(intent.approved_requirement_revision_id)
        job_request = self.job_request_store.get_job_request_revision(intent.job_request_revision_id)
        if job_request is None or job_request.effective_job_title is None:
            failed = self.workflow_start_intent_store.mark_failed(
                intent.workflow_start_intent_id,
                reason_code="job_request_missing",
                updated_at=self.now(),
            )
            self.outbox_store.mark_done(outbox_id, updated_at=self.now())
            return failed

        try:
            source_selection = self.source_selection_resolver.resolve_runtime_source_selection(
                source_kinds=job_request.source_kinds or None,
                workspace_source_policy_id=job_request.workspace_source_policy_id,
            )
        except SourceSelectionError as exc:
            failed = self.workflow_start_intent_store.mark_failed(
                intent.workflow_start_intent_id,
                reason_code=exc.reason_code,
                updated_at=self.now(),
            )
            self.outbox_store.mark_done(outbox_id, updated_at=self.now())
            return failed

        runtime_run = self.service_action_adapter.start_workflow(
            conversation_id=intent.conversation_id,
            workbench_session_id=None,
            approved_requirement=approved,
            job_title=job_request.effective_job_title,
            jd_text=job_request.jd_text,
            notes=job_request.notes,
            source_ids=list(source_selection.runtime_source_ids),
            run_intent_id=intent.deterministic_run_key,
            start_idempotency_key=intent.deterministic_run_key,
        )
        self._link_started_workflow_run(intent, runtime_run_id=runtime_run.runtime_run_id)
        started = self.workflow_start_intent_store.mark_started(
            intent.workflow_start_intent_id,
            runtime_run_id=runtime_run.runtime_run_id,
            updated_at=self.now(),
        )
        self.outbox_store.mark_done(outbox_id, updated_at=self.now())
        return started

    def _confirmed_intent_response(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        draft_revision_id: str,
        job_request_revision_id: str,
        workflow_start_intent_id: str,
    ) -> ConversationAgentResponse:
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
            requirement_draft=self.service_action_adapter.get_requirement_draft(
                conversation_id=conversation_id,
                draft_revision_id=draft_revision_id,
            ),
            job_request_revision_id=job_request_revision_id,
            requirement_draft_revision_id=draft_revision_id,
            workflow_start_intent_id=workflow_start_intent_id,
        )

    def _link_started_workflow_run(self, intent: WorkflowStartIntent, *, runtime_run_id: str) -> None:
        self.store.link_runtime_run(
            conversation_id=intent.conversation_id,
            runtime_run_id=runtime_run_id,
            workbench_session_id=None,
            approved_requirement_revision_id=intent.approved_requirement_revision_id,
            run_intent_id=intent.deterministic_run_key,
            run_kind="primary",
            link_reason="start",
            linked_at=self.now(),
        )

    def start_workflow(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
    ) -> ConversationAgentResponse:
        conversation = self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        if conversation.approved_requirement_revision_id is None:
            raise ConversationAgentError("requirement_not_confirmed")
        intent = self.workflow_start_intent_store.get_latest_for_conversation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        if intent is None or intent.approved_requirement_revision_id != conversation.approved_requirement_revision_id:
            raise ConversationAgentError("workflow_start_intent_not_found")

        if intent.status == "pending":
            outbox_item = self.outbox_store.get_for_aggregate(intent.workflow_start_intent_id)
            if outbox_item is None:
                raise ConversationAgentError("workflow_start_outbox_not_found")
            intent = self.process_workflow_start_outbox_item(outbox_item.outbox_id)
        elif intent.status == "started" and intent.runtime_run_id is not None:
            self._link_started_workflow_run(intent, runtime_run_id=intent.runtime_run_id)

        if intent.status == "failed":
            raise ConversationAgentError(intent.reason_code or "workflow_start_failed")
        if intent.status == "cancelled":
            raise ConversationAgentError(intent.reason_code or "workflow_start_cancelled")
        if intent.status != "started" or intent.runtime_run_id is None:
            raise ConversationAgentError(intent.reason_code or "workflow_start_not_started")

        return self.poll_runtime_events(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=intent.runtime_run_id,
            limit=200,
        )

    def poll_runtime_events(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str | None,
        limit: int,
    ) -> ConversationAgentResponse:
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        page = self.service_action_adapter.list_workflow_events(
            runtime_run_id=target.runtime_run_id,
            after_seq=target.link.latest_event_seq,
            limit=limit,
        )
        if page.reason_code == "runtime_event_gap_detected":
            if target.link.is_active:
                self._sync_status_from_runtime(conversation_id=conversation_id, runtime_run_id=target.runtime_run_id)
            final_summary = self._ensure_final_summary_for_runtime(
                target=target,
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                display_instruction=None,
            )
            reopened = self.reopen_conversation(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            return ConversationAgentResponse(
                conversation_reopen_state=reopened.conversation_reopen_state,
                messages=reopened.messages,
                activity_items=reopened.activity_items,
                final_summary=final_summary,
                reason_code=page.reason_code,
            )
        for event in page.events:
            projected = project_runtime_event(
                conversation_id=conversation_id,
                event=event,
                activity_id=self.activity_id_factory(),
            )
            self.store.upsert_activity_item(
                activity_id=projected.activity_id,
                conversation_id=conversation_id,
                activity_key=projected.activity_key,
                activity_type=projected.activity_type,
                status=projected.status,
                title=projected.title,
                summary=projected.summary,
                payload=projected.payload,
                source_runtime_run_id=projected.source_runtime_run_id,
                source_event_id_latest=projected.source_event_id_latest,
                source_event_seq_start=projected.source_event_seq_start,
                source_event_seq_latest=projected.source_event_seq_latest,
                started_at=projected.started_at,
                updated_at=projected.updated_at,
                completed_at=projected.completed_at,
                created_at=projected.created_at,
            )
            self._append_runtime_progress_once(
                conversation_id=conversation_id,
                runtime_run_id=target.runtime_run_id,
                event_seq=event.event_seq,
                text=event.summary,
                payload={"eventId": event.event_id, "eventType": event.event_type, "status": event.status},
                created_at=event.created_at,
            )
            self.store.update_rendered_runtime_cursor(
                conversation_id=conversation_id,
                runtime_run_id=target.runtime_run_id,
                latest_event_seq=event.event_seq,
                updated_at=self.now(),
            )
        if target.link.is_active:
            self._sync_status_from_runtime(conversation_id=conversation_id, runtime_run_id=target.runtime_run_id)
        final_summary = self._ensure_final_summary_for_runtime(
            target=target,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            display_instruction=None,
        )
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
            final_summary=final_summary,
        )

    def prepare_final_summary(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str | None,
        user_instruction: str | None,
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        del idempotency_key
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        summary = self._ensure_final_summary_for_runtime(
            target=target,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            display_instruction=user_instruction,
        )
        if summary is None:
            raise ConversationAgentError("runtime_run_not_completed")
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
            final_summary=summary,
        )

    def _ensure_final_summary_for_runtime(
        self,
        *,
        target: RuntimeRunTarget,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        display_instruction: str | None,
    ) -> RuntimeFinalSummary | None:
        del display_instruction, owner_user_id, workspace_id
        runtime_store = self.service_action_adapter.runtime_store
        if runtime_store is None:
            return None
        run = runtime_store.get_run(target.runtime_run_id)
        if run.status not in _TERMINAL_RUN_STATUS_TO_CONVERSATION:
            return None
        if target.conversation.final_summary_id is not None:
            existing_summary = runtime_store.get_final_summary(summary_id=target.conversation.final_summary_id)
            if existing_summary is not None and existing_summary.runtime_run_id == target.runtime_run_id:
                return self._record_final_summary_for_runtime(
                    target=target,
                    conversation_id=conversation_id,
                    summary=existing_summary,
                )
        source_snapshot_event_seq = self._latest_runtime_snapshot_seq(target.runtime_run_id)
        summary = self.service_action_adapter.prepare_final_summary(
            runtime_run_id=target.runtime_run_id,
            user_instruction=None,
            source_snapshot_event_seq=source_snapshot_event_seq,
            idempotency_key=_runtime_final_summary_idempotency_key(target.runtime_run_id),
        )
        if summary.summary_id is None:
            return None
        return self._record_final_summary_for_runtime(
            target=target,
            conversation_id=conversation_id,
            summary=summary,
        )

    def _record_final_summary_for_runtime(
        self,
        *,
        target: RuntimeRunTarget,
        conversation_id: str,
        summary: RuntimeFinalSummary,
    ) -> RuntimeFinalSummary:
        safe_summary = summary.model_copy(
            update={
                "summary": sanitize_summary_text(summary.summary),
                "user_instruction": None,
            }
        )
        if target.link.is_active and safe_summary.summary_id is not None:
            self.store.set_final_summary(
                conversation_id=conversation_id,
                final_summary_id=safe_summary.summary_id,
                updated_at=self.now(),
            )
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="final_summary",
            text=safe_summary.summary,
            payload=safe_summary.model_dump(mode="json"),
            source_runtime_run_id=target.runtime_run_id,
            created_at=self.now(),
            message_id=self.message_id_factory(),
            idempotency_key=_final_summary_message_idempotency_key(conversation_id, target.runtime_run_id),
            return_existing_on_idempotency=True,
        )
        return safe_summary

    def request_workflow_command(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str | None,
        command_type: str,
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        try:
            if command_type == "pause":
                command = self.service_action_adapter.request_pause(
                    runtime_run_id=target.runtime_run_id,
                    requested_by=owner_user_id,
                    idempotency_key=idempotency_key,
                )
            elif command_type == "cancel":
                command = self.service_action_adapter.request_cancel(
                    runtime_run_id=target.runtime_run_id,
                    requested_by=owner_user_id,
                    idempotency_key=idempotency_key,
                )
            elif command_type == "resume":
                command = self.service_action_adapter.resume_workflow(
                    runtime_run_id=target.runtime_run_id,
                    requested_by=owner_user_id,
                    idempotency_key=idempotency_key,
                )
            else:
                raise ConversationAgentError("agent_command_type_invalid")
        except RuntimeControlError as exc:
            raise _conversation_error_from_runtime_control(exc) from exc
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="command_state",
            text=f"命令已记录：{command.command_type}，状态 {command.status}。",
            payload={"command": command.model_dump(mode="json")},
            source_runtime_run_id=target.runtime_run_id,
            created_at=self.now(),
            message_id=self.message_id_factory(),
            idempotency_key=_workflow_command_message_idempotency_key(
                conversation_id=conversation_id,
                runtime_run_id=target.runtime_run_id,
                idempotency_key=idempotency_key,
            ),
            return_existing_on_idempotency=True,
        )
        if target.link.is_active:
            self._sync_status_from_runtime(conversation_id=conversation_id, runtime_run_id=target.runtime_run_id)
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
        )

    def submit_next_round_requirement(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str | None,
        text: str,
        target_section_hint: str | None,
        idempotency_key: str,
        provenance: dict[str, object] | None = None,
    ) -> ConversationAgentResponse:
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        safe_text = screen_requirement_text(text)
        safe_target_section_hint = screen_target_section_hint(target_section_hint)
        try:
            result = self.service_action_adapter.submit_next_round_requirement(
                runtime_run_id=target.runtime_run_id,
                text=safe_text,
                target_section_hint=safe_target_section_hint,
                idempotency_key=idempotency_key,
                provenance=provenance,
            )
        except RuntimeControlError as exc:
            raise _conversation_error_from_runtime_control(exc) from exc
        target_round_no = getattr(result, "target_round_no")
        status = str(getattr(result, "status"))
        review_required = bool(getattr(result, "review_required", False))
        text_out = (
            f"下一轮需求等待确认，目标第 {target_round_no} 轮。"
            if review_required
            else f"已记录，将在第 {target_round_no} 轮开始前生效。"
        )
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="command_state",
            text=text_out,
            payload={"nextRoundRequirement": _object_payload(result), "status": status},
            source_runtime_run_id=target.runtime_run_id,
            created_at=self.now(),
            message_id=self.message_id_factory(),
            idempotency_key=_assistant_message_idempotency_key(idempotency_key),
            return_existing_on_idempotency=True,
        )
        return self.poll_runtime_events(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=target.runtime_run_id,
            limit=200,
        )

    async def _run_routed_runtime_user_turn(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        message: TranscriptMessage,
        user_message: str,
        idempotency_key: str | None,
        target: RuntimeRunTarget,
    ) -> ConversationAgentResponse:
        runtime_facts = self._runtime_fact_context(target)
        decision = await self._run_or_replay_runtime_intent_route(
            conversation_id=conversation_id,
            user_message=user_message,
            runtime_facts=runtime_facts,
            idempotency_key=idempotency_key,
            target=target,
        )
        if decision.intent == "read_only_question":
            return await self._run_agent_model_after_user_message(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                message=message,
                user_message=user_message,
                idempotency_key=idempotency_key,
                source_runtime_run_id=target.runtime_run_id,
                runtime_task=(
                    "Use the registered Conversation Agent instructions to answer this read-only runtime question. "
                    "Use only the active runtime facts below. If the facts are insufficient, say what is not available."
                ),
                runtime_facts=runtime_facts,
            )
        if decision.intent == "next_round_requirement":
            requirement_text = user_message.strip()
            normalized_requirement_text = (decision.requirement_text or "").strip()
            if not normalized_requirement_text:
                self.store.append_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    message_type="assistant_text",
                    text="我没有把这条消息记录为下一轮需求。请明确写出要新增的需求内容。",
                    payload={
                        "intentDecision": decision.model_dump(mode="json"),
                        "runtimeRunId": target.runtime_run_id,
                    },
                    source_runtime_run_id=target.runtime_run_id,
                    created_at=self.now(),
                    message_id=self.message_id_factory(),
                    idempotency_key=_assistant_message_idempotency_key(idempotency_key),
                    return_existing_on_idempotency=idempotency_key is not None,
                )
                return self._reopened_response(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                )
            provenance: dict[str, object] = {
                "originalUserText": user_message,
                "intentDecision": {"intent": decision.intent},
                "sourceMessageId": message.message_id,
                "runtimeRunId": target.runtime_run_id,
            }
            provenance["normalizedRequirementText"] = normalized_requirement_text
            return await asyncio.to_thread(
                self.submit_next_round_requirement,
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                runtime_run_id=target.runtime_run_id,
                text=requirement_text,
                target_section_hint=decision.target_section_hint,
                idempotency_key=f"{idempotency_key or message.message_id}:next-round-requirement",
                provenance=provenance,
            )
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="assistant_text",
            text="这个请求会修改 workflow。当前对话只支持只读问题或新增下一轮需求。",
            payload={
                "intentDecision": decision.model_dump(mode="json"),
                "runtimeRunId": target.runtime_run_id,
            },
            source_runtime_run_id=target.runtime_run_id,
            created_at=self.now(),
            message_id=self.message_id_factory(),
            idempotency_key=_assistant_message_idempotency_key(idempotency_key),
            return_existing_on_idempotency=idempotency_key is not None,
        )
        return self._reopened_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )

    async def _run_or_replay_runtime_intent_route(
        self,
        *,
        conversation_id: str,
        user_message: str,
        runtime_facts: dict[str, object],
        idempotency_key: str | None,
        target: RuntimeRunTarget,
    ) -> ConversationAgentIntentDecision:
        if idempotency_key is not None:
            existing = self._agent_intent_route_by_idempotency(
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if existing.status == "started":
                    if self._agent_operation_audit_is_stale(existing):
                        self._mark_agent_intent_route_failed(
                            operation_id=existing.operation_id,
                            conversation_id=conversation_id,
                            started_at=existing.started_at,
                            reason_code="agent_request_stale",
                            result={"idempotencyKey": idempotency_key},
                            args=existing.args,
                            runtime_run_id=target.runtime_run_id,
                        )
                        raise ConversationAgentError("agent_request_stale")
                    raise ConversationAgentError("agent_request_in_progress")
                if existing.status == "failed":
                    raise ConversationAgentError(
                        existing.reason_code or "agent_intent_route_failed",
                        payload=existing.result or {},
                    )
                if existing.status == "completed":
                    return self._intent_decision_from_completed_route(existing)

        prompt_text = _intent_routing_prompt(user_message=user_message, runtime_facts=runtime_facts)
        estimated_input_tokens = _rough_token_estimate(prompt_text)
        operation_id = self.operation_id_factory()
        started_at = self.now()
        args: dict[str, object] = {
            "modelName": self.agent_model_name,
            "runtimeRunId": target.runtime_run_id,
            "estimatedInputTokens": estimated_input_tokens,
            "idempotencyKey": idempotency_key,
        }
        self.store.save_operation_audit(
            operation_id=operation_id,
            conversation_id=conversation_id,
            operation_name="agent_intent_route",
            execution_origin="service",
            status="started",
            args=args,
            result=None,
            reason_code=None,
            started_at=started_at,
            runtime_run_id=target.runtime_run_id,
        )
        runtime = AgentRuntime(
            model_name=self.agent_model_name,
            instructions=self.agent_instructions,
            runner=self.agent_runner,
        )
        try:
            decision_payload = await runtime.run_structured(
                prompt_text,
                name="SeekTalent Conversation Intent Router",
                output_type=ConversationAgentIntentDecision,
            )
            decision = ConversationAgentIntentDecision.model_validate(decision_payload)
        except ConversationAgentError as exc:
            self._mark_agent_intent_route_failed(
                operation_id=operation_id,
                conversation_id=conversation_id,
                started_at=started_at,
                reason_code=exc.reason_code,
                result={"estimatedInputTokens": estimated_input_tokens},
                args=args,
                runtime_run_id=target.runtime_run_id,
            )
            raise
        except ValidationError as exc:
            self._mark_agent_intent_route_failed(
                operation_id=operation_id,
                conversation_id=conversation_id,
                started_at=started_at,
                reason_code="agent_intent_route_invalid",
                result={"estimatedInputTokens": estimated_input_tokens},
                args=args,
                runtime_run_id=target.runtime_run_id,
            )
            raise ConversationAgentError("agent_intent_route_invalid") from exc
        self.store.save_operation_audit(
            operation_id=operation_id,
            conversation_id=conversation_id,
            operation_name="agent_intent_route",
            execution_origin="service",
            status="completed",
            args=args,
            result={
                "intentDecision": decision.model_dump(mode="json"),
                "estimatedInputTokens": estimated_input_tokens,
            },
            reason_code=None,
            started_at=started_at,
            completed_at=self.now(),
            runtime_run_id=target.runtime_run_id,
        )
        return decision

    def _intent_decision_from_completed_route(self, operation_audit: OperationAuditRecord) -> ConversationAgentIntentDecision:
        result = operation_audit.result or {}
        decision_payload = result.get("intentDecision")
        try:
            return ConversationAgentIntentDecision.model_validate(decision_payload)
        except ValidationError as exc:
            self._mark_agent_intent_route_failed(
                operation_id=operation_audit.operation_id,
                conversation_id=operation_audit.conversation_id,
                started_at=operation_audit.started_at,
                reason_code="agent_intent_route_recovery_failed",
                result=result,
                args=operation_audit.args,
                runtime_run_id=operation_audit.runtime_run_id,
            )
            raise ConversationAgentError("agent_intent_route_recovery_failed") from exc

    def get_runtime_detail(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str | None,
        kind: str,
        round_no: int | None = None,
        event_id: str | None = None,
        command_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> ConversationAgentResponse:
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        detail = self.service_action_adapter.get_runtime_detail(
            runtime_run_id=target.runtime_run_id,
            kind=kind,
            round_no=round_no,
            event_id=event_id,
            command_id=command_id,
            checkpoint_id=checkpoint_id,
        )
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="detail_answer",
            text=detail.summary,
            payload={"detail": detail.model_dump(mode="json")},
            source_runtime_run_id=target.runtime_run_id,
            created_at=self.now(),
            message_id=self.message_id_factory(),
        )
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
        )

    def get_workflow_snapshot(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str | None,
    ) -> RuntimeRunSnapshot:
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        return self.service_action_adapter.get_workflow_snapshot(runtime_run_id=target.runtime_run_id)

    def _mark_context_compaction_failed(
        self,
        *,
        conversation: ConversationRecord,
        compaction_id: str,
        activity_key: str,
        trigger_reason_code: str,
        created_at: str,
        reason_code: str,
        summary: str,
    ) -> None:
        failed_at = self.now()
        self.store.save_context_compaction(
            compaction_id=compaction_id,
            conversation_id=conversation.conversation_id,
            status="failed",
            trigger_reason_code=trigger_reason_code,
            created_at=created_at,
            completed_at=failed_at,
            failed_reason_code=reason_code,
        )
        self.store.upsert_activity_item(
            activity_id=self.activity_id_factory(),
            conversation_id=conversation.conversation_id,
            activity_key=activity_key,
            activity_type="context_compaction",
            status="failed",
            title="上下文压缩",
            summary=summary,
            payload={
                "compactionId": compaction_id,
                "triggerReasonCode": trigger_reason_code,
                "reasonCode": reason_code,
            },
            source_runtime_run_id=conversation.runtime_run_id,
            source_event_id_latest=None,
            source_event_seq_start=None,
            source_event_seq_latest=None,
            started_at=created_at,
            updated_at=failed_at,
            completed_at=failed_at,
            created_at=created_at,
        )

    def compact_context(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        trigger_reason_code: str,
    ) -> ConversationAgentResponse:
        conversation = self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        compaction_id = self.compaction_id_factory()
        activity_key = f"{conversation_id}:context_compaction:{compaction_id}"
        created_at = self.now()
        self.store.save_context_compaction(
            compaction_id=compaction_id,
            conversation_id=conversation_id,
            status="in_progress",
            trigger_reason_code=trigger_reason_code,
            created_at=created_at,
        )
        self.store.upsert_activity_item(
            activity_id=self.activity_id_factory(),
            conversation_id=conversation_id,
            activity_key=activity_key,
            activity_type="context_compaction",
            status="started",
            title="上下文压缩",
            summary="正在生成模型输入摘要，原始 transcript 保持不变。",
            payload={"compactionId": compaction_id, "triggerReasonCode": trigger_reason_code},
            source_runtime_run_id=conversation.runtime_run_id,
            source_event_id_latest=None,
            source_event_seq_start=None,
            source_event_seq_latest=None,
            started_at=created_at,
            updated_at=created_at,
            created_at=created_at,
        )
        try:
            messages = self.store.get_messages(conversation_id=conversation_id)
            if not messages:
                raise ConversationAgentError("agent_compaction_quality_failed")
            summary_id = self.summary_id_factory()
            first_seq = messages[0].message_seq
            last_seq = messages[-1].message_seq
            activity_items = self.store.get_activity_items(conversation_id=conversation_id)
            evidence: dict[str, object] = {
                "coveredMessageSeqStart": first_seq,
                "coveredMessageSeqEnd": last_seq,
                "latestRenderedRuntimeEventSeq": conversation.latest_rendered_runtime_event_seq,
                "activeActivityItemIds": [
                    item.activity_id
                    for item in activity_items
                    if item.status not in {"completed", "failed", "cancelled", "superseded"}
                ],
            }
            summary_text = _compact_summary_text(
                conversation=conversation,
                messages=messages,
                activity_items=activity_items,
            )
            compaction = self.store.complete_context_compaction(
                summary_id=summary_id,
                compaction_id=compaction_id,
                conversation_id=conversation_id,
                trigger_reason_code=trigger_reason_code,
                source_message_seq_start=first_seq,
                source_message_seq_end=last_seq,
                source_activity_seq_start=activity_items[0].activity_seq if activity_items else None,
                source_activity_seq_end=activity_items[-1].activity_seq if activity_items else None,
                latest_rendered_runtime_event_seq=conversation.latest_rendered_runtime_event_seq,
                summary_text=summary_text,
                quality_status="passed",
                quality_evidence=evidence,
                token_count=_rough_token_estimate(summary_text),
                quality_reason_code="agent_compaction_quality_passed",
                compaction_created_at=created_at,
                summary_created_at=self.now(),
                completed_at=self.now(),
            )
            self.store.upsert_activity_item(
                activity_id=self.activity_id_factory(),
                conversation_id=conversation_id,
                activity_key=activity_key,
                activity_type="context_compaction",
                status="completed",
                title="上下文压缩",
                summary="已生成模型输入摘要，原始 transcript 保持不变。",
                payload={
                    "compactionId": compaction_id,
                    "summaryId": summary_id,
                    "coveredMessageSeqEnd": last_seq,
                    "triggerReasonCode": trigger_reason_code,
                },
                source_runtime_run_id=conversation.runtime_run_id,
                source_event_id_latest=None,
                source_event_seq_start=None,
                source_event_seq_latest=None,
                started_at=created_at,
                updated_at=self.now(),
                completed_at=self.now(),
                created_at=created_at,
            )
        except (ConversationAgentError, sqlite3.Error, TypeError, ValueError) as exc:
            reason_code = exc.reason_code if isinstance(exc, ConversationAgentError) else "agent_compaction_failed"
            summary = (
                "上下文压缩失败：没有可压缩的 transcript。"
                if reason_code == "agent_compaction_quality_failed"
                else "上下文压缩失败：未能生成模型输入摘要。"
            )
            self._mark_context_compaction_failed(
                conversation=conversation,
                compaction_id=compaction_id,
                activity_key=activity_key,
                trigger_reason_code=trigger_reason_code,
                created_at=created_at,
                reason_code=reason_code,
                summary=summary,
            )
            raise
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
            compaction=compaction,
        )

    def _persist_draft_response(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        draft: DraftProtocol,
        source_draft_revision_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ConversationAgentResponse:
        unresolved = int(getattr(draft, "unresolved_review_item_count", 0))
        draft_revision_id = str(getattr(draft, "draft_revision_id"))
        job_request_link = (
            self._inherit_requirement_draft_job_request_link(
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                source_draft_revision_id=source_draft_revision_id,
                target_draft_revision_id=draft_revision_id,
            )
            if source_draft_revision_id is not None
            else None
        )
        self.store.link_requirement_draft(
            conversation_id=conversation_id,
            draft_revision_id=draft_revision_id,
            pending_requirement_review_count=unresolved,
            updated_at=self.now(),
        )
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="requirement_review",
            text="需求草稿已更新，请继续确认。",
            payload=_requirement_review_payload(draft),
            created_at=self.now(),
            message_id=self.message_id_factory(),
            idempotency_key=_requirement_review_message_idempotency_key(
                draft_revision_id=draft_revision_id,
                idempotency_key=idempotency_key,
            ),
            return_existing_on_idempotency=True,
        )
        reopened = self.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        return ConversationAgentResponse(
            conversation_reopen_state=reopened.conversation_reopen_state,
            messages=reopened.messages,
            activity_items=reopened.activity_items,
            requirement_draft=_requirement_draft_response(draft),
            job_request_revision_id=job_request_link.job_request_revision_id if job_request_link is not None else None,
            requirement_draft_revision_id=draft_revision_id if job_request_link is not None else None,
        )

    def _inherit_requirement_draft_job_request_link(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        source_draft_revision_id: str,
        target_draft_revision_id: str,
    ) -> RequirementDraftJobRequestLink:
        source_link = self.job_request_store.get_requirement_draft_job_request_link(source_draft_revision_id)
        if source_link is None:
            raise ConversationAgentError("requirement_draft_job_request_not_found")
        if source_link.workspace_id != workspace_id or source_link.conversation_id != conversation_id:
            raise ConversationAgentError("requirement_draft_job_request_not_found")
        return self.job_request_store.link_requirement_draft_job_request(
            draft_revision_id=target_draft_revision_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            job_request_revision_id=source_link.job_request_revision_id,
            created_at=self.now(),
        )

    def _append_runtime_progress_once(
        self,
        *,
        conversation_id: str,
        runtime_run_id: str,
        event_seq: int,
        text: str,
        payload: dict[str, object],
        created_at: str,
    ) -> None:
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="runtime_progress",
            text=text,
            payload=payload,
            source_runtime_run_id=runtime_run_id,
            source_runtime_event_seq=event_seq,
            created_at=created_at,
            message_id=self.message_id_factory(),
            idempotency_key=_runtime_progress_message_idempotency_key(
                conversation_id=conversation_id,
                runtime_run_id=runtime_run_id,
                event_seq=event_seq,
            ),
            return_existing_on_idempotency=True,
        )

    def _sync_status_from_runtime(self, *, conversation_id: str, runtime_run_id: str) -> None:
        if self.service_action_adapter.runtime_store is None:
            return
        run = self.service_action_adapter.runtime_store.get_run(runtime_run_id)
        self.store.update_conversation_status(
            conversation_id=conversation_id,
            status=_conversation_status_from_run(run.status),
            updated_at=self.now(),
            completed_at=run.completed_at,
        )

    def _maybe_active_runtime_run_target(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
    ) -> RuntimeRunTarget | None:
        try:
            target = self._resolve_runtime_run_target(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                runtime_run_id=None,
            )
        except ConversationAgentError as exc:
            if exc.reason_code == "agent_runtime_run_not_linked":
                return None
            raise
        return target if target.link.is_active else None

    def _runtime_fact_context(self, target: RuntimeRunTarget) -> dict[str, object]:
        facts: dict[str, object] = {
            "runtimeRunId": target.runtime_run_id,
            "conversationId": target.conversation.conversation_id,
            "link": {
                "runKind": target.link.run_kind,
                "linkReason": target.link.link_reason,
                "isActive": target.link.is_active,
                "linkedAt": target.link.linked_at,
            },
        }
        runtime_store = self.service_action_adapter.runtime_store
        if runtime_store is None:
            return facts
        run = runtime_store.get_run(target.runtime_run_id)
        facts.update(
            {
                "run": {
                    "status": run.status,
                    "currentStage": run.current_stage,
                    "currentRound": run.current_round,
                    "latestEventSeq": run.latest_event_seq,
                    "sourceIds": run.source_ids,
                    "approvedRequirementRevisionId": run.approved_requirement_revision_id,
                    "stopReasonCode": run.stop_reason_code,
                }
            }
        )
        snapshot = runtime_store.get_snapshot(runtime_run_id=target.runtime_run_id)
        latest_event_seq = run.latest_event_seq
        if snapshot is not None:
            latest_event_seq = max(latest_event_seq, snapshot.latest_event_seq)
            facts["snapshot"] = {
                "status": snapshot.status,
                "currentStage": snapshot.current_stage,
                "currentRound": snapshot.current_round,
                "latestEventSeq": snapshot.latest_event_seq,
                "summary": snapshot.snapshot.get("summary"),
                "facts": snapshot.snapshot.get("facts"),
            }
        events = runtime_store.list_events(
            runtime_run_id=target.runtime_run_id,
            after_seq=max(0, latest_event_seq - 20),
            limit=20,
        ).events
        facts["recentEvents"] = [
            {
                "eventSeq": event.event_seq,
                "eventType": event.event_type,
                "stage": event.stage,
                "roundNo": event.round_no,
                "status": event.status,
                "summary": event.summary,
            }
            for event in events
        ]
        return facts

    def _latest_runtime_snapshot_seq(self, runtime_run_id: str) -> int:
        try:
            snapshot_seq = self.service_action_adapter.get_workflow_snapshot(runtime_run_id=runtime_run_id).latest_event_seq
        except RuntimeControlError as exc:
            if exc.reason_code != "runtime_snapshot_not_found" or self.service_action_adapter.runtime_store is None:
                raise
            return self.service_action_adapter.runtime_store.get_run(runtime_run_id).latest_event_seq
        if self.service_action_adapter.runtime_store is None:
            return snapshot_seq
        return max(snapshot_seq, self.service_action_adapter.runtime_store.get_run(runtime_run_id).latest_event_seq)

    def _memory_source_updated_at(self, conversation_id: str) -> str | None:
        timestamps: list[str] = []
        timestamps.extend(message.created_at for message in self.store.get_messages(conversation_id=conversation_id))
        timestamps.extend(activity.updated_at for activity in self.store.get_activity_items(conversation_id=conversation_id))
        return max(timestamps) if timestamps else None

    def _require_conversation(
        self,
        conversation_id: str,
        *,
        owner_user_id: str,
        workspace_id: str,
    ) -> ConversationRecord:
        conversation = self.store.get_conversation(conversation_id)
        if conversation.owner_user_id != owner_user_id or conversation.workspace_id != workspace_id:
            raise ConversationAgentError("conversation_not_found")
        return conversation

    def _resolve_runtime_run_target(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str | None,
    ) -> RuntimeRunTarget:
        conversation = self._require_conversation(
            conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
        )
        target_runtime_run_id = runtime_run_id or conversation.runtime_run_id
        if target_runtime_run_id is None:
            raise ConversationAgentError(
                "agent_runtime_run_not_linked",
                payload={"runtimeRunId": runtime_run_id},
            )
        links = self.store.list_runtime_links(conversation_id=conversation_id)
        link = next((item for item in links if item.runtime_run_id == target_runtime_run_id), None)
        if link is None:
            raise ConversationAgentError(
                "agent_runtime_run_not_linked",
                payload={"runtimeRunId": target_runtime_run_id},
            )
        runtime_store = self.service_action_adapter.runtime_store
        if runtime_store is not None:
            try:
                run = runtime_store.get_run(target_runtime_run_id)
            except RuntimeControlError as exc:
                raise ConversationAgentError(
                    "agent_runtime_run_not_linked",
                    payload={"runtimeRunId": target_runtime_run_id},
                ) from exc
            if run.agent_conversation_id not in {None, conversation_id}:
                raise ConversationAgentError(
                    "agent_runtime_run_not_linked",
                    payload={"runtimeRunId": target_runtime_run_id},
                )
        return RuntimeRunTarget(conversation=conversation, link=link)

    def _require_runtime_run_link(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        runtime_run_id: str,
    ) -> ConversationRecord:
        return self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        ).conversation


def _conversation_status_from_run(run_status: str) -> str:
    if run_status in _TERMINAL_RUN_STATUS_TO_CONVERSATION:
        return _TERMINAL_RUN_STATUS_TO_CONVERSATION[run_status]
    if run_status == "paused":
        return "paused"
    return "running"


def _runtime_final_summary_idempotency_key(runtime_run_id: str) -> str:
    return f"{_RUNTIME_FINAL_SUMMARY_IDEMPOTENCY_PREFIX}{runtime_run_id}"


def _final_summary_message_idempotency_key(conversation_id: str, runtime_run_id: str) -> str:
    return f"{_FINAL_SUMMARY_MESSAGE_IDEMPOTENCY_PREFIX}{conversation_id}:{runtime_run_id}"


def _workflow_command_message_idempotency_key(
    *,
    conversation_id: str,
    runtime_run_id: str,
    idempotency_key: str,
) -> str:
    return f"{_WORKFLOW_COMMAND_MESSAGE_IDEMPOTENCY_PREFIX}{conversation_id}:{runtime_run_id}:{idempotency_key}"


def _runtime_progress_message_idempotency_key(
    *,
    conversation_id: str,
    runtime_run_id: str,
    event_seq: int,
) -> str:
    return f"{_RUNTIME_PROGRESS_MESSAGE_IDEMPOTENCY_PREFIX}{conversation_id}:{runtime_run_id}:{event_seq}"


def _requirement_draft_response(draft: DraftProtocol) -> RequirementDraft:
    if isinstance(draft, RequirementDraft):
        return draft
    return RequirementDraft.model_validate(draft.model_dump(mode="python"))


def _conversation_error_from_runtime_control(exc: RuntimeControlError) -> ConversationAgentError:
    return ConversationAgentError(exc.reason_code, payload=exc.payload)


def _resolve_submit_jd_source_kinds(
    *,
    source_kinds: Sequence[str] | None,
    source_ids: Sequence[str] | None,
) -> list[str]:
    if source_kinds is None and source_ids is None:
        return []
    if source_kinds is None:
        return list(normalize_source_kinds(list(source_ids or []), allow_empty=True))
    normalized_source_kinds = normalize_source_kinds(list(source_kinds), allow_empty=True)
    if source_ids is None:
        return list(normalized_source_kinds)
    normalized_source_ids = normalize_source_kinds(list(source_ids), allow_empty=True)
    if normalized_source_kinds != normalized_source_ids:
        raise ConversationAgentError(
            "job_request_source_kinds_conflict",
            payload={
                "sourceKinds": list(normalized_source_kinds),
                "sourceIds": list(normalized_source_ids),
            },
        )
    return list(normalized_source_kinds)


_CONTEXT_SUMMARY_SCHEMA_VERSION = "conversation-context-summary/v1"
_CONTEXT_SUMMARY_ACTIVE_ACTIVITY_CAP = 6
_CONTEXT_SUMMARY_REQUIREMENT_REVIEW_CAP = 4
_CONTEXT_SUMMARY_COMMAND_STATE_CAP = 6
_CONTEXT_SUMMARY_FINAL_SUMMARY_CAP = 3
_CONTEXT_SUMMARY_RECENT_MESSAGE_CAP = 6
_CONTEXT_SUMMARY_TEXT_FIELD_CHAR_CAP = 180
_CONTEXT_SUMMARY_PAYLOAD_FIELD_CAP = 4
_CONTEXT_SUMMARY_PAYLOAD_LIST_CAP = 6
_CONTEXT_SUMMARY_TRUNCATION_RECORD_CAP = 48
_CONTEXT_SUMMARY_MAX_SERIALIZED_BYTES = 12_000
_TERMINAL_ACTIVITY_STATUSES = {"completed", "failed", "cancelled", "superseded"}
_PROMPT_SECTION_MARKERS = tuple(
    f"[{section}_{boundary}]"
    for section in (
        "ADVISORY_MEMORY_CONTEXT",
        "CONVERSATION_AGENT_MODEL_INPUT",
        "CURRENT_USER_MESSAGE",
        "LATEST_CONTEXT_SUMMARY",
        "RECENT_TRANSCRIPT",
        "REGISTERED_PROMPT",
        "RUNTIME_FACTS",
        "RUNTIME_TASK",
        "USER_MESSAGE",
    )
    for boundary in ("START", "END")
)


def _compact_summary_text(
    *,
    conversation: ConversationRecord,
    messages: list[TranscriptMessage],
    activity_items: list[TranscriptActivityItem],
) -> str:
    ordered_messages = sorted(messages, key=lambda message: message.message_seq)
    ordered_activities = sorted(activity_items, key=lambda item: item.activity_seq)
    truncation: list[dict[str, object]] = []
    requirement_reviews = _last_n(
        [message for message in ordered_messages if message.message_type == "requirement_review"],
        _CONTEXT_SUMMARY_REQUIREMENT_REVIEW_CAP,
        "$.requirementReviews",
        truncation,
    )
    command_states = _last_n(
        [message for message in ordered_messages if message.message_type == "command_state"],
        _CONTEXT_SUMMARY_COMMAND_STATE_CAP,
        "$.commandStates",
        truncation,
    )
    final_summaries = _last_n(
        [message for message in ordered_messages if message.message_type == "final_summary"],
        _CONTEXT_SUMMARY_FINAL_SUMMARY_CAP,
        "$.finalSummaries",
        truncation,
    )
    recent_messages = _last_n(
        ordered_messages,
        _CONTEXT_SUMMARY_RECENT_MESSAGE_CAP,
        "$.recentMessages",
        truncation,
    )
    active_activities = _last_n(
        [item for item in ordered_activities if item.status not in _TERMINAL_ACTIVITY_STATUSES],
        _CONTEXT_SUMMARY_ACTIVE_ACTIVITY_CAP,
        "$.activeActivities",
        truncation,
    )
    summary: dict[str, object] = {
        "schemaVersion": _CONTEXT_SUMMARY_SCHEMA_VERSION,
        "coveredMessageSeqStart": ordered_messages[0].message_seq,
        "coveredMessageSeqEnd": ordered_messages[-1].message_seq,
        "coveredActivitySeqStart": ordered_activities[0].activity_seq if ordered_activities else None,
        "coveredActivitySeqEnd": ordered_activities[-1].activity_seq if ordered_activities else None,
        "conversationStatus": _bounded_summary_string(
            conversation.status,
            "$.conversationStatus",
            truncation,
        ),
        "activeRuntimeRunId": _bounded_optional_summary_string(
            conversation.runtime_run_id,
            "$.activeRuntimeRunId",
            truncation,
        ),
        "latestRenderedRuntimeEventSeq": conversation.latest_rendered_runtime_event_seq,
        "activeActivities": [
            _activity_summary_entry(item, f"$.activeActivities[{index}]", truncation)
            for index, item in enumerate(active_activities)
        ],
        "requirementReviews": [
            _message_summary_entry(message, f"$.requirementReviews[{index}]", truncation, include_payload=True)
            for index, message in enumerate(requirement_reviews)
        ],
        "commandStates": [
            _message_summary_entry(message, f"$.commandStates[{index}]", truncation, include_payload=True)
            for index, message in enumerate(command_states)
        ],
        "finalSummaries": [
            _message_summary_entry(message, f"$.finalSummaries[{index}]", truncation, include_payload=True)
            for index, message in enumerate(final_summaries)
        ],
        "recentMessages": [
            _message_summary_entry(message, f"$.recentMessages[{index}]", truncation, include_payload=False)
            for index, message in enumerate(recent_messages)
        ],
        "truncation": truncation,
    }
    return _serialize_context_summary(summary)


def _last_n(
    items: list[_SummaryItemT],
    cap: int,
    path: str,
    truncation: list[dict[str, object]],
) -> list[_SummaryItemT]:
    if len(items) <= cap:
        return items
    _append_truncation_record(truncation, path, original_length=len(items), truncated_length=cap, unit="items")
    return items[-cap:]


def _message_summary_entry(
    message: TranscriptMessage,
    path: str,
    truncation: list[dict[str, object]],
    *,
    include_payload: bool,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "messageSeq": message.message_seq,
        "messageId": _bounded_summary_string(message.message_id, f"{path}.messageId", truncation),
        "role": _bounded_summary_string(message.role, f"{path}.role", truncation),
        "messageType": _bounded_summary_string(message.message_type, f"{path}.messageType", truncation),
        "text": _bounded_summary_string(message.text, f"{path}.text", truncation),
        "sourceRuntimeRunId": _bounded_optional_summary_string(
            message.source_runtime_run_id,
            f"{path}.sourceRuntimeRunId",
            truncation,
        ),
        "sourceRuntimeEventSeq": message.source_runtime_event_seq,
    }
    if include_payload:
        entry["payload"] = _bounded_summary_value(message.payload, f"{path}.payload", truncation)
    return entry


def _activity_summary_entry(
    item: TranscriptActivityItem,
    path: str,
    truncation: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "activitySeq": item.activity_seq,
        "activityId": _bounded_summary_string(item.activity_id, f"{path}.activityId", truncation),
        "activityType": _bounded_summary_string(item.activity_type, f"{path}.activityType", truncation),
        "status": _bounded_summary_string(item.status, f"{path}.status", truncation),
        "title": _bounded_summary_string(item.title, f"{path}.title", truncation),
        "summary": _bounded_summary_string(item.summary, f"{path}.summary", truncation),
        "sourceRuntimeRunId": _bounded_optional_summary_string(
            item.source_runtime_run_id,
            f"{path}.sourceRuntimeRunId",
            truncation,
        ),
        "sourceEventSeqStart": item.source_event_seq_start,
        "sourceEventSeqLatest": item.source_event_seq_latest,
        "payload": _bounded_summary_value(item.payload, f"{path}.payload", truncation),
    }


def _bounded_summary_value(value: object, path: str, truncation: list[dict[str, object]]) -> object | None:
    safe = _json_safe_value(value)
    if safe is None:
        return None
    if isinstance(safe, str):
        return _bounded_summary_string(safe, path, truncation)
    if isinstance(safe, bool | int | float):
        return safe
    if isinstance(safe, Mapping):
        items = sorted(safe.items(), key=lambda item: str(item[0]))
        selected_items = items[:_CONTEXT_SUMMARY_PAYLOAD_FIELD_CAP]
        if len(items) > len(selected_items):
            _append_truncation_record(
                truncation,
                path,
                original_length=len(items),
                truncated_length=len(selected_items),
                unit="fields",
            )
        bounded: dict[str, object] = {}
        for key, item in selected_items:
            bounded_key = _bounded_summary_string(str(key), f"{path}.*.key", truncation)
            key_path = f"{path}.{bounded_key}"
            bounded_item = _bounded_summary_value(item, key_path, truncation)
            if bounded_item is not None:
                bounded[bounded_key] = bounded_item
        return bounded
    if isinstance(safe, list | tuple):
        selected_items = list(safe[:_CONTEXT_SUMMARY_PAYLOAD_LIST_CAP])
        if len(safe) > len(selected_items):
            _append_truncation_record(
                truncation,
                path,
                original_length=len(safe),
                truncated_length=len(selected_items),
                unit="items",
            )
        return [
            bounded_item
            for index, item in enumerate(selected_items)
            if (bounded_item := _bounded_summary_value(item, f"{path}[{index}]", truncation)) is not None
        ]
    return _bounded_summary_string(str(safe), path, truncation)


def _bounded_optional_summary_string(
    value: str | None,
    path: str,
    truncation: list[dict[str, object]],
) -> str | None:
    if value is None:
        return None
    return _bounded_summary_string(value, path, truncation)


def _bounded_summary_string(value: str, path: str, truncation: list[dict[str, object]]) -> str:
    safe_text = sanitize_summary_text(value)
    if len(safe_text) <= _CONTEXT_SUMMARY_TEXT_FIELD_CHAR_CAP:
        return safe_text
    suffix = "..."
    truncated = safe_text[: _CONTEXT_SUMMARY_TEXT_FIELD_CHAR_CAP - len(suffix)].rstrip() + suffix
    _append_truncation_record(
        truncation,
        path,
        original_length=len(safe_text),
        truncated_length=len(truncated),
        unit="chars",
    )
    return truncated


def _append_truncation_record(
    truncation: list[dict[str, object]],
    path: str,
    *,
    original_length: int,
    truncated_length: int,
    unit: str,
) -> None:
    if original_length <= truncated_length or len(truncation) >= _CONTEXT_SUMMARY_TRUNCATION_RECORD_CAP:
        return
    truncation.append(
        {
            "path": path,
            "originalLength": original_length,
            "truncatedLength": truncated_length,
            "unit": unit,
        }
    )


def _serialize_context_summary(summary: dict[str, object]) -> str:
    serialized = _context_summary_json(summary)
    if len(serialized.encode("utf-8")) <= _CONTEXT_SUMMARY_MAX_SERIALIZED_BYTES:
        return serialized

    compacted = dict(summary)
    truncation = _summary_mapping_list(compacted.get("truncation"))
    for category in ("activeActivities", "requirementReviews", "commandStates", "finalSummaries", "recentMessages"):
        compacted[category] = [_without_payload(entry) for entry in _summary_mapping_list(compacted.get(category))]
    _append_truncation_record(
        truncation,
        "$",
        original_length=len(serialized.encode("utf-8")),
        truncated_length=_CONTEXT_SUMMARY_MAX_SERIALIZED_BYTES,
        unit="bytes",
    )
    compacted["truncation"] = truncation[:_CONTEXT_SUMMARY_TRUNCATION_RECORD_CAP]
    serialized = _context_summary_json(compacted)
    if len(serialized.encode("utf-8")) <= _CONTEXT_SUMMARY_MAX_SERIALIZED_BYTES:
        return serialized

    for category in ("recentMessages", "activeActivities", "commandStates", "requirementReviews", "finalSummaries"):
        items = _summary_mapping_list(compacted.get(category))
        if len(items) > 1:
            compacted[category] = items[-1:]
            _append_truncation_record(
                truncation,
                f"$.{category}",
                original_length=len(items),
                truncated_length=1,
                unit="items",
            )
    compacted["truncation"] = truncation[:_CONTEXT_SUMMARY_TRUNCATION_RECORD_CAP]
    serialized = _context_summary_json(compacted)
    if len(serialized.encode("utf-8")) <= _CONTEXT_SUMMARY_MAX_SERIALIZED_BYTES:
        return serialized

    minimal = {
        key: compacted[key]
        for key in (
            "schemaVersion",
            "coveredMessageSeqStart",
            "coveredMessageSeqEnd",
            "coveredActivitySeqStart",
            "coveredActivitySeqEnd",
            "conversationStatus",
            "activeRuntimeRunId",
            "latestRenderedRuntimeEventSeq",
        )
    }
    minimal.update(
        {
            "activeActivities": [],
            "requirementReviews": [],
            "commandStates": [],
            "finalSummaries": [],
            "recentMessages": [],
            "truncation": [
                {
                    "path": "$",
                    "originalLength": len(serialized.encode("utf-8")),
                    "truncatedLength": 0,
                    "unit": "bytes",
                }
            ],
        }
    )
    return _context_summary_json(minimal)


def _without_payload(entry: object) -> object:
    if not isinstance(entry, dict):
        return entry
    stripped = dict(entry)
    stripped.pop("payload", None)
    return stripped


def _summary_mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        result.append({str(key): mapped_value for key, mapped_value in item.items()})
    return result


def _context_summary_json(summary: Mapping[str, object]) -> str:
    return _escape_prompt_section_markers(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))


def _escape_prompt_section_markers(json_text: str) -> str:
    escaped = json_text
    for marker in _PROMPT_SECTION_MARKERS:
        escaped = escaped.replace(marker, marker.replace("[", "\\u005b").replace("]", "\\u005d"))
    return escaped


def _intent_routing_prompt(*, user_message: str, runtime_facts: dict[str, object]) -> str:
    return "\n".join(
        [
            "Use the registered Conversation Agent instructions to classify this active runtime user message.",
            "Host handoff contract:",
            "- read_only_question: no workflow mutation; the host may ask the agent to answer from runtime facts.",
            "- next_round_requirement: set requirement_text to the normalized requirement and target_section_hint only if clear; the host uses the original user message as canonical extraction input and records your normalized text as provenance.",
            "- unsupported_write: no service action will be executed; the host will refuse unsupported workflow mutation.",
            "Never claim that you executed a service action or called runtime-control yourself.",
            "Return only the structured output requested by the schema.",
            "[RUNTIME_FACTS_START]",
            _model_input_json(_json_safe_value(runtime_facts)),
            "[RUNTIME_FACTS_END]",
            "[USER_MESSAGE_START]",
            _model_input_json(user_message),
            "[USER_MESSAGE_END]",
        ]
    )


def _object_payload(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in ((key, _json_safe_value(item)) for key, item in value.items()) if item is not None}
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in ((key, _json_safe_value(item)) for key, item in vars(value).items())
            if not key.startswith("_") and item is not None
        }
    return {}


def _json_safe_value(value: object) -> object | None:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if callable(value):
        return None
    if isinstance(value, Mapping):
        return {
            str(key): item
            for key, item in ((key, _json_safe_value(item)) for key, item in value.items())
            if item is not None
        }
    if isinstance(value, list | tuple):
        return [item for item in (_json_safe_value(item) for item in value) if item is not None]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return _json_safe_value(dumped)
    return str(value)


def _agent_result_text(value: object) -> str:
    if isinstance(value, Mapping):
        value_map = {str(key): item for key, item in value.items()}
        status = value_map.get("status")
        return str(status) if status is not None else ""
    final_output = getattr(value, "final_output", None)
    if final_output is not None:
        return str(final_output)
    return str(value)


def _conversation_token_count(messages: list[TranscriptMessage]) -> int:
    return sum(message.token_count or _rough_token_estimate(message.text) for message in messages)


def _model_input_content_token_estimate(
    *,
    instructions_text: str,
    latest_context_summary: str | None,
    recent_messages: list[TranscriptMessage],
    advisory_context: str,
    current_user_message: str,
    runtime_task: str | None = None,
    runtime_facts: dict[str, object] | None = None,
) -> int:
    return (
        _rough_token_estimate(instructions_text)
        + _rough_token_estimate(latest_context_summary or "")
        + sum(message.token_count or _rough_token_estimate(message.text) for message in recent_messages)
        + _rough_token_estimate(advisory_context)
        + _rough_token_estimate(runtime_task or "")
        + _rough_token_estimate(json.dumps(_json_safe_value(runtime_facts), ensure_ascii=False) if runtime_facts else "")
        + _rough_token_estimate(current_user_message)
    )


def _rough_token_estimate(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _extract_provider_usage(result: object) -> dict[str, int | bool]:
    usage = _usage_object(result)
    if usage is None:
        return {
            "hasProviderUsage": False,
            "inputTokens": 0,
            "outputTokens": 0,
            "totalTokens": 0,
            "estimatedCostCents": 0,
            "costCents": 0,
        }
    input_tokens = _usage_int(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
    output_tokens = _usage_int(
        usage,
        "output_tokens",
        "outputTokens",
        "completion_tokens",
        "completionTokens",
    )
    total_tokens = _usage_int(usage, "total_tokens", "totalTokens") or input_tokens + output_tokens
    cost_cents = _usage_int(usage, "cost_cents", "costCents")
    return {
        "hasProviderUsage": True,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "estimatedCostCents": cost_cents,
        "costCents": cost_cents,
    }


def _usage_object(result: object) -> object | None:
    direct = _get_usage_member(result)
    if direct is not None:
        return direct
    context_wrapper = getattr(result, "context_wrapper", None)
    if context_wrapper is not None:
        return _get_usage_member(context_wrapper)
    if isinstance(result, dict):
        for key, item in result.items():
            if str(key) == "usage":
                return item
    return None


def _get_usage_member(value: object) -> object | None:
    member = getattr(value, "usage", None)
    if callable(member):
        return member()
    return member


def _usage_int(value: object, *field_names: str) -> int:
    for field_name in field_names:
        item = _dict_value(value, field_name)
        if item is None:
            item = getattr(value, field_name, None)
        if isinstance(item, int):
            return item
        if isinstance(item, float):
            return int(item)
    return 0


def _dict_value(value: object, field_name: str) -> object | None:
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        if str(key) == field_name:
            return item
    return None


def _mapping_value(value: object, field_name: str) -> object | None:
    if not isinstance(value, Mapping):
        return None
    for key, item in value.items():
        if str(key) == field_name:
            return item
    return None


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
