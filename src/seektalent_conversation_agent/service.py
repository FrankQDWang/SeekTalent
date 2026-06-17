from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from seektalent_conversation_agent.budget import AgentBudgetPolicy
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.models import (
    ConversationAgentResponse,
    ConversationRecord,
    ConversationRuntimeRunLink,
    ConversationThreadView,
    TranscriptMessage,
)
from seektalent_conversation_agent.projection import project_runtime_event
from seektalent_conversation_agent.runtime import AgentRunner, AgentRuntime
from seektalent_conversation_agent.safety import sanitize_summary_text, screen_requirement_text
from seektalent_conversation_agent.store import ConversationStore
from seektalent_conversation_agent.tools import AgentToolAdapter
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeRunSnapshot
from seektalent_runtime_control.requirements import DraftOperation, ReviewResolutionOperation


_TERMINAL_RUN_STATUS_TO_CONVERSATION = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


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
        tool_adapter: AgentToolAdapter,
        now: Callable[[], str],
        conversation_id_factory: Callable[[], str] | None = None,
        message_id_factory: Callable[[], str] | None = None,
        activity_id_factory: Callable[[], str] | None = None,
        tool_call_id_factory: Callable[[], str] | None = None,
        summary_id_factory: Callable[[], str] | None = None,
        compaction_id_factory: Callable[[], str] | None = None,
        memory_service: MemoryServiceProtocol | None = None,
        agent_model_name: str = "gpt-4.1-mini",
        agent_instructions: str = "You are SeekTalent Assistant. Help the user operate the local recruiting workflow.",
        agent_runner: AgentRunner | None = None,
        budget_policy: AgentBudgetPolicy | None = None,
    ) -> None:
        self.store = store
        self.tool_adapter = tool_adapter
        self.now = now
        self.conversation_id_factory = conversation_id_factory or (lambda: f"agent_conv_{uuid4().hex}")
        self.message_id_factory = message_id_factory or (lambda: f"agent_msg_{uuid4().hex}")
        self.activity_id_factory = activity_id_factory or (lambda: f"agent_activity_{uuid4().hex}")
        self.tool_call_id_factory = tool_call_id_factory or (lambda: f"agent_tool_call_{uuid4().hex}")
        self.summary_id_factory = summary_id_factory or (lambda: f"agent_context_summary_{uuid4().hex}")
        self.compaction_id_factory = compaction_id_factory or (lambda: f"agent_compaction_{uuid4().hex}")
        self.memory_service = memory_service
        self.agent_model_name = agent_model_name
        self.agent_instructions = agent_instructions
        self.agent_runner = agent_runner
        self.budget_policy = budget_policy

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
        if idempotency_key is not None:
            existing = self.store.get_message_by_idempotency(
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return self._replay_idempotent_agent_turn(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    idempotency_key=idempotency_key,
                )
        conversation_tokens_before_turn = _conversation_token_count(
            self.store.get_messages(conversation_id=conversation_id)
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
                idempotency_key=idempotency_key,
            )
        except sqlite3.IntegrityError:
            if idempotency_key is not None and self.store.get_message_by_idempotency(
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            ):
                return self._replay_idempotent_agent_turn(
                    conversation_id=conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    idempotency_key=idempotency_key,
                )
            raise
        advisory_context = ""
        if self.memory_service is not None:
            memory_context = self.memory_service.recall_for_conversation(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn_id=message.message_id,
            )
            advisory_context = memory_context.context_text
        estimated_input_tokens = user_token_estimate + _rough_token_estimate(advisory_context)
        tool_call_id = self.tool_call_id_factory()
        started_at = self.now()
        self.store.save_tool_call(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            tool_name="agent_model_run",
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
                    tool_call_id=tool_call_id,
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
            result = await runtime.run(user_message, advisory_memory_context=advisory_context)
            result_text = _agent_result_text(result)
            usage = _extract_provider_usage(result)
            estimated_output_tokens = _rough_token_estimate(result_text)
            if not usage["hasProviderUsage"]:
                usage["inputTokens"] = estimated_input_tokens
                usage["outputTokens"] = estimated_output_tokens
                usage["totalTokens"] = estimated_input_tokens + estimated_output_tokens
            usage_result: dict[str, object] = {
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
                tool_call_id=tool_call_id,
                conversation_id=conversation_id,
                started_at=started_at,
                reason_code=exc.reason_code,
                result=locals().get("usage_result", {"estimatedInputTokens": estimated_input_tokens}),
            )
            raise
        self.store.save_tool_call(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            tool_name="agent_model_run",
            status="completed",
            args={
                "modelName": self.agent_model_name,
                "estimatedInputTokens": estimated_input_tokens,
                "conversationTokensBeforeTurn": conversation_tokens_before_turn,
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
            source_tool_call_id=tool_call_id,
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

    def _replay_idempotent_agent_turn(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        tool_call = self._agent_model_run_by_idempotency(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if tool_call is None or tool_call.status == "started":
            raise ConversationAgentError("agent_request_in_progress")
        if tool_call.status == "failed":
            raise ConversationAgentError(
                tool_call.reason_code or "agent_request_failed",
                payload=tool_call.result or {},
            )
        if tool_call.status == "completed" and self._assistant_message_exists(
            conversation_id=conversation_id,
            tool_call_id=tool_call.tool_call_id,
        ):
            return self._reopened_response(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
        raise ConversationAgentError("agent_request_in_progress")

    def _agent_model_run_by_idempotency(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
    ):
        matching = [
            call
            for call in self.store.list_tool_calls(conversation_id=conversation_id)
            if call.tool_name == "agent_model_run" and call.args.get("idempotencyKey") == idempotency_key
        ]
        return matching[-1] if matching else None

    def _assistant_message_exists(self, *, conversation_id: str, tool_call_id: str) -> bool:
        return any(
            message.role == "assistant" and message.source_tool_call_id == tool_call_id
            for message in self.store.get_messages(conversation_id=conversation_id)
        )

    def _reopened_response(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
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
        )

    def _mark_agent_model_run_failed(
        self,
        *,
        tool_call_id: str,
        conversation_id: str,
        started_at: str,
        reason_code: str,
        result: dict[str, object] | None,
    ) -> None:
        self.store.save_tool_call(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            tool_name="agent_model_run",
            status="failed",
            args={"modelName": self.agent_model_name},
            result=result,
            reason_code=reason_code,
            started_at=started_at,
            completed_at=self.now(),
        )

    def _monthly_cost_cents(self, *, owner_user_id: str, workspace_id: str) -> int:
        total = 0
        for conversation in self.store.list_conversations(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            include_archived=True,
        ):
            for call in self.store.list_tool_calls(conversation_id=conversation.conversation_id):
                if call.tool_name != "agent_model_run" or call.status != "completed" or call.result is None:
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
                "role": "tool",
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
        return self.store.reopen_conversation(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            opened_at=self.now(),
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

    def submit_jd(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        job_title: str,
        jd_text: str,
        notes: str | None,
        source_ids: list[str],
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        user_message = self.store.append_message(
            conversation_id=conversation_id,
            role="user",
            message_type="user_text",
            text=jd_text,
            payload={"jobTitle": job_title, "notes": notes, "sourceIds": source_ids},
            created_at=self.now(),
            message_id=self.message_id_factory(),
        )
        tool_call_id = self.tool_call_id_factory()
        self.store.save_tool_call(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            tool_name="extract_requirements",
            status="started",
            args={"jobTitle": job_title, "sourceIds": source_ids},
            result=None,
            reason_code=None,
            started_at=self.now(),
        )
        draft = self.tool_adapter.extract_requirements(
            conversation_id=conversation_id,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=source_ids,
            idempotency_key=idempotency_key,
        )
        self.store.save_tool_call(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            tool_name="extract_requirements",
            status="completed",
            args={"jobTitle": job_title, "sourceIds": source_ids},
            result={"draftRevisionId": draft.draft_revision_id},
            reason_code=None,
            started_at=self.now(),
            completed_at=self.now(),
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
        assistant_message = self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="requirement_review",
            text="已拆解岗位需求，请确认后再启动检索。",
            payload={"requirementDraft": draft.model_dump(mode="json")},
            source_tool_call_id=tool_call_id,
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
            messages=[user_message, assistant_message],
            activity_items=reopened.activity_items,
            requirement_draft=draft,
        )

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
        draft = self.tool_adapter.update_requirement_draft(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            operations=parsed,
            idempotency_key=idempotency_key,
        )
        return self._persist_draft_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            draft=draft,
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
        draft = self.tool_adapter.amend_requirement_draft_from_text(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            text=safe_text,
            target_section_hint=target_section_hint,
            idempotency_key=idempotency_key,
        )
        return self._persist_draft_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            draft=draft,
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
        draft = self.tool_adapter.resolve_requirement_review(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            amendment_id=amendment_id,
            operations=parsed,
            idempotency_key=idempotency_key,
        )
        return self._persist_draft_response(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            draft=draft,
        )

    def confirm_requirements(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        draft_revision_id: str,
        base_revision_id: str,
        idempotency_key: str,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        approved = self.tool_adapter.confirm_requirements(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            idempotency_key=idempotency_key,
        )
        self.store.link_approved_requirement(
            conversation_id=conversation_id,
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            updated_at=self.now(),
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
            requirement_draft=self.tool_adapter.get_requirement_draft(
                conversation_id=conversation_id,
                draft_revision_id=draft_revision_id,
            ),
        )

    def start_workflow(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        job_title: str,
        jd_text: str,
        notes: str | None,
        source_ids: list[str],
    ) -> ConversationAgentResponse:
        conversation = self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        if conversation.approved_requirement_revision_id is None:
            raise ConversationAgentError("requirement_not_confirmed")
        approved = self.tool_adapter._require_requirement_service().store.get_approved_requirement(
            conversation.approved_requirement_revision_id
        )
        run = self.tool_adapter.start_workflow(
            conversation_id=conversation_id,
            workbench_session_id=conversation.workbench_session_id,
            approved_requirement=approved,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=source_ids,
        )
        self.store.link_runtime_run(
            conversation_id=conversation_id,
            runtime_run_id=run.runtime_run_id,
            workbench_session_id=run.workbench_session_id,
            approved_requirement_revision_id=run.approved_requirement_revision_id,
            run_intent_id=run.run_intent_id,
            run_kind=run.run_kind,
            link_reason="start",
            linked_at=self.now(),
        )
        self.store.update_conversation_status(
            conversation_id=conversation_id,
            status=_conversation_status_from_run(run.status),
            updated_at=self.now(),
            completed_at=run.completed_at,
        )
        return self.poll_runtime_events(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=run.runtime_run_id,
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
        page = self.tool_adapter.list_workflow_events(
            runtime_run_id=target.runtime_run_id,
            after_seq=target.link.latest_event_seq,
            limit=limit,
        )
        if page.reason_code == "runtime_event_gap_detected":
            reopened = self.reopen_conversation(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
            )
            return ConversationAgentResponse(
                conversation_reopen_state=reopened.conversation_reopen_state,
                messages=reopened.messages,
                activity_items=reopened.activity_items,
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
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        source_snapshot_event_seq = self._latest_runtime_snapshot_seq(target.runtime_run_id)
        summary = self.tool_adapter.prepare_final_summary(
            runtime_run_id=target.runtime_run_id,
            user_instruction=user_instruction,
            source_snapshot_event_seq=source_snapshot_event_seq,
            idempotency_key=idempotency_key,
        )
        if summary.summary_id is None:
            raise ConversationAgentError(summary.reason_code or "runtime_final_summary_unavailable")
        summary_id = summary.summary_id
        safe_summary = summary.model_copy(
            update={
                "summary": sanitize_summary_text(summary.summary),
                "user_instruction": (
                    sanitize_summary_text(summary.user_instruction) if summary.user_instruction is not None else None
                ),
            }
        )
        if target.link.is_active:
            self.store.set_final_summary(
                conversation_id=conversation_id,
                final_summary_id=summary_id,
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
            final_summary=safe_summary,
        )

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
        if command_type == "pause":
            command = self.tool_adapter.request_pause(
                runtime_run_id=target.runtime_run_id,
                requested_by=owner_user_id,
                idempotency_key=idempotency_key,
            )
        elif command_type == "cancel":
            command = self.tool_adapter.request_cancel(
                runtime_run_id=target.runtime_run_id,
                requested_by=owner_user_id,
                idempotency_key=idempotency_key,
            )
        elif command_type == "resume":
            command = self.tool_adapter.resume_workflow(
                runtime_run_id=target.runtime_run_id,
                requested_by=owner_user_id,
                idempotency_key=idempotency_key,
            )
        else:
            raise ConversationAgentError("agent_command_type_invalid")
        self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="command_state",
            text=f"命令已记录：{command.command_type}，状态 {command.status}。",
            payload={"command": command.model_dump(mode="json")},
            source_runtime_run_id=target.runtime_run_id,
            created_at=self.now(),
            message_id=self.message_id_factory(),
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
    ) -> ConversationAgentResponse:
        target = self._resolve_runtime_run_target(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=runtime_run_id,
        )
        safe_text = screen_requirement_text(text)
        result = self.tool_adapter.submit_next_round_requirement(
            runtime_run_id=target.runtime_run_id,
            text=safe_text,
            target_section_hint=target_section_hint,
            idempotency_key=idempotency_key,
        )
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
        )
        return self.poll_runtime_events(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            runtime_run_id=target.runtime_run_id,
            limit=200,
        )

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
        detail = self.tool_adapter.get_runtime_detail(
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
        return self.tool_adapter.get_workflow_snapshot(runtime_run_id=target.runtime_run_id)

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
        messages = self.store.get_messages(conversation_id=conversation_id)
        if not messages:
            failed_at = self.now()
            self.store.save_context_compaction(
                compaction_id=compaction_id,
                conversation_id=conversation_id,
                status="failed",
                trigger_reason_code=trigger_reason_code,
                created_at=created_at,
                completed_at=failed_at,
                failed_reason_code="agent_compaction_quality_failed",
            )
            self.store.upsert_activity_item(
                activity_id=self.activity_id_factory(),
                conversation_id=conversation_id,
                activity_key=activity_key,
                activity_type="context_compaction",
                status="failed",
                title="上下文压缩",
                summary="上下文压缩失败：没有可压缩的 transcript。",
                payload={
                    "compactionId": compaction_id,
                    "triggerReasonCode": trigger_reason_code,
                    "reasonCode": "agent_compaction_quality_failed",
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
        self.store.create_context_summary(
            summary_id=summary_id,
            conversation_id=conversation_id,
            source_message_seq_start=first_seq,
            source_message_seq_end=last_seq,
            source_activity_seq_start=activity_items[0].activity_seq if activity_items else None,
            source_activity_seq_end=activity_items[-1].activity_seq if activity_items else None,
            latest_rendered_runtime_event_seq=conversation.latest_rendered_runtime_event_seq,
            summary_text=_compact_summary_text(messages),
            quality_status="passed",
            quality_evidence=evidence,
            token_count=sum(len(message.text) for message in messages),
            created_at=self.now(),
        )
        compaction = self.store.save_context_compaction(
            compaction_id=compaction_id,
            conversation_id=conversation_id,
            status="completed",
            trigger_reason_code=trigger_reason_code,
            summary_id=summary_id,
            source_message_seq_start=first_seq,
            source_message_seq_end=last_seq,
            source_activity_seq_start=activity_items[0].activity_seq if activity_items else None,
            source_activity_seq_end=activity_items[-1].activity_seq if activity_items else None,
            quality_reason_code="agent_compaction_quality_passed",
            created_at=created_at,
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
    ) -> ConversationAgentResponse:
        unresolved = int(getattr(draft, "unresolved_review_item_count", 0))
        draft_revision_id = str(getattr(draft, "draft_revision_id"))
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
            payload={"requirementDraft": draft.model_dump(mode="json")},
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
            requirement_draft=draft,
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
        try:
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
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" not in str(exc):
                raise

    def _sync_status_from_runtime(self, *, conversation_id: str, runtime_run_id: str) -> None:
        if self.tool_adapter.runtime_store is None:
            return
        run = self.tool_adapter.runtime_store.get_run(runtime_run_id)
        self.store.update_conversation_status(
            conversation_id=conversation_id,
            status=_conversation_status_from_run(run.status),
            updated_at=self.now(),
            completed_at=run.completed_at,
        )

    def _latest_runtime_snapshot_seq(self, runtime_run_id: str) -> int:
        try:
            return self.tool_adapter.get_workflow_snapshot(runtime_run_id=runtime_run_id).latest_event_seq
        except RuntimeControlError as exc:
            if exc.reason_code != "runtime_snapshot_not_found" or self.tool_adapter.runtime_store is None:
                raise
            return self.tool_adapter.runtime_store.get_run(runtime_run_id).latest_event_seq

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
        runtime_store = self.tool_adapter.runtime_store
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


def _compact_summary_text(messages: list[TranscriptMessage]) -> str:
    first = messages[0].text
    last = messages[-1].text
    return sanitize_summary_text(f"本段对话从“{first}”开始，最新补充为“{last}”。")


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


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
