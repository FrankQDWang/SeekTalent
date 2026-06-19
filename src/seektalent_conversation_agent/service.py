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
from seektalent_conversation_agent.job_request_store import JobRequestStore
from seektalent_conversation_agent.job_requests import (
    JobRequestRevision,
    RequirementDraftJobRequestLink,
    normalize_source_kinds,
)
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
from seektalent_conversation_agent.source_selection import (
    RuntimeSourceSelectionResolver,
    SourceSelectionError,
)
from seektalent_conversation_agent.store import ConversationStore
from seektalent_conversation_agent.tools import AgentToolAdapter
from seektalent_conversation_agent.workflow_start_intents import (
    WorkbenchOutboxStore,
    WorkflowConfirmRequestStore,
    WorkflowStartIntent,
    WorkflowStartIntentStore,
    workflow_confirm_request_hash,
    workflow_start_request_hash,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeRunSnapshot
from seektalent_runtime_control.requirements import ApprovedRequirementRevision, DraftOperation, ReviewResolutionOperation


_TERMINAL_RUN_STATUS_TO_CONVERSATION = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}
_WORKFLOW_START_OUTBOX_CLAIM_TIMEOUT_SECONDS = 60


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
        source_selection_resolver: RuntimeSourceSelectionResolver | None = None,
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
        self.source_selection_resolver = source_selection_resolver or RuntimeSourceSelectionResolver(
            registered_runtime_source_ids={"cts", "liepin"}
        )

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
        source_kinds: list[str] | None = None,
        source_ids: list[str] | None = None,
        workspace_source_policy_id: str | None = None,
    ) -> ConversationAgentResponse:
        self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        raw_source_kinds = source_kinds if source_kinds is not None else source_ids
        if raw_source_kinds is None:
            raise ConversationAgentError("job_request_source_kinds_required")
        normalized_source_kinds = normalize_source_kinds(list(raw_source_kinds))
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
            draft = self.tool_adapter.get_requirement_draft(
                conversation_id=conversation_id,
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
        user_message = self.store.append_message(
            conversation_id=conversation_id,
            role="user",
            message_type="user_text",
            text=jd_text,
            payload={
                "jobTitle": user_job_title,
                "notes": notes,
                "sourceKinds": list(normalized_source_kinds),
                "jobRequestRevisionId": job_request.job_request_revision_id,
            },
            created_at=self.now(),
            message_id=self.message_id_factory(),
        )
        tool_call_id = self.tool_call_id_factory()
        self.store.save_tool_call(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            tool_name="extract_requirements",
            status="started",
            args={
                "jobTitle": user_job_title,
                "sourceKinds": list(normalized_source_kinds),
                "jobRequestRevisionId": job_request.job_request_revision_id,
            },
            result=None,
            reason_code=None,
            started_at=self.now(),
        )
        draft = self.tool_adapter.extract_requirements(
            conversation_id=conversation_id,
            job_title=user_job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=list(normalized_source_kinds),
            idempotency_key=idempotency_key,
        )
        extracted_job_title = _extracted_job_title_from_runtime_control(
            self.tool_adapter,
            draft_revision_id=draft.draft_revision_id,
        )
        job_request = self.job_request_store.update_extracted_job_title(
            job_request_revision_id=job_request.job_request_revision_id,
            extracted_job_title=extracted_job_title,
            updated_at=self.now(),
        )
        effective_job_title = job_request.effective_job_title
        if effective_job_title is None:
            self.store.save_tool_call(
                tool_call_id=tool_call_id,
                conversation_id=conversation_id,
                tool_name="extract_requirements",
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
        self.store.save_tool_call(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            tool_name="extract_requirements",
            status="completed",
            args={
                "jobTitle": user_job_title,
                "extractedJobTitle": extracted_job_title,
                "effectiveJobTitle": effective_job_title,
                "sourceKinds": list(normalized_source_kinds),
                "jobRequestRevisionId": job_request.job_request_revision_id,
            },
            result={"draftRevisionId": draft.draft_revision_id},
            reason_code=None,
            started_at=self.now(),
            completed_at=self.now(),
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
        assistant_message = self.store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            message_type="requirement_review",
            text="已拆解岗位需求，请确认后再启动检索。",
            payload=_requirement_review_payload(draft),
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
            job_request_revision_id=job_request.job_request_revision_id,
            requirement_draft_revision_id=draft.draft_revision_id,
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
        try:
            draft = self.tool_adapter.update_requirement_draft(
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
            draft = self.tool_adapter.amend_requirement_draft_from_text(
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
            draft = self.tool_adapter.resolve_requirement_review(
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
        if self.tool_adapter.runtime_store is not None:
            approved = self.tool_adapter.runtime_store.get_approved_requirement_by_idempotency(
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            )
            if approved is not None:
                if approved.draft_revision_id != draft_revision_id:
                    raise ConversationAgentError("idempotency_key_conflict")
                return approved
        try:
            return self.tool_adapter.confirm_requirements(
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

        if self.tool_adapter.runtime_store is None:
            raise ConversationAgentError("runtime_control_store_required")
        approved = self.tool_adapter.runtime_store.get_approved_requirement(intent.approved_requirement_revision_id)
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
                source_kinds=job_request.source_kinds,
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

        runtime_run = self.tool_adapter.start_workflow(
            conversation_id=intent.conversation_id,
            workbench_session_id=None,
            approved_requirement=approved,
            job_title=job_request.effective_job_title,
            jd_text=job_request.jd_text,
            notes=job_request.notes,
            source_ids=source_selection.runtime_source_ids,
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
            requirement_draft=self.tool_adapter.get_requirement_draft(
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
        job_title: str,
        jd_text: str,
        notes: str | None,
        source_ids: list[str],
    ) -> ConversationAgentResponse:
        conversation = self._require_conversation(conversation_id, owner_user_id=owner_user_id, workspace_id=workspace_id)
        if conversation.approved_requirement_revision_id is None:
            raise ConversationAgentError("requirement_not_confirmed")
        intent = self.workflow_start_intent_store.get_latest_for_conversation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        if (
            intent is not None
            and intent.approved_requirement_revision_id == conversation.approved_requirement_revision_id
            and intent.status in {"pending", "started"}
        ):
            if intent.status == "pending":
                outbox_item = self.outbox_store.get_for_aggregate(intent.workflow_start_intent_id)
                if outbox_item is None:
                    raise ConversationAgentError("workflow_start_outbox_not_found")
                intent = self.process_workflow_start_outbox_item(outbox_item.outbox_id)
            elif intent.runtime_run_id is not None:
                self._link_started_workflow_run(intent, runtime_run_id=intent.runtime_run_id)
            if intent.runtime_run_id is None:
                raise ConversationAgentError(intent.reason_code or "workflow_start_not_started")
            return self.poll_runtime_events(
                conversation_id=conversation_id,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                runtime_run_id=intent.runtime_run_id,
                limit=200,
            )
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
        try:
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
        try:
            result = self.tool_adapter.submit_next_round_requirement(
                runtime_run_id=target.runtime_run_id,
                text=safe_text,
                target_section_hint=target_section_hint,
                idempotency_key=idempotency_key,
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
            requirement_draft=draft,
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


def _conversation_error_from_runtime_control(exc: RuntimeControlError) -> ConversationAgentError:
    return ConversationAgentError(exc.reason_code, payload=exc.payload)


def _should_repair_submit_replay_status(conversation: ConversationRecord) -> bool:
    return conversation.runtime_run_id is None and conversation.status in {"draft", "awaiting_requirement_confirmation"}


def _normalize_optional_job_title(job_title: str | None) -> str | None:
    if job_title is None:
        return None
    normalized = job_title.strip()
    return normalized or None


def _extracted_job_title_from_runtime_control(
    tool_adapter: AgentToolAdapter,
    *,
    draft_revision_id: str,
) -> str | None:
    runtime_store = tool_adapter.runtime_store
    if runtime_store is None:
        return None
    payload = runtime_store.get_extracted_requirement_sheet_json(draft_revision_id)
    job_title = payload.get("job_title")
    if not isinstance(job_title, str):
        return None
    normalized = job_title.strip()
    return normalized or None


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


def _requirement_review_payload(draft: object) -> dict[str, object]:
    draft_revision_id = str(getattr(draft, "draft_revision_id"))
    return {
        "requirementDraft": {"draftRevisionId": draft_revision_id},
        "requirementDraftSnapshot": _requirement_draft_snapshot(draft),
    }


def _requirement_review_message_idempotency_key(
    *,
    draft_revision_id: str,
    idempotency_key: str | None,
) -> str | None:
    if not idempotency_key:
        return None
    return f"requirement_review:{draft_revision_id}:{idempotency_key}"


def _requirement_draft_snapshot(draft: object) -> dict[str, object]:
    sections = list(getattr(draft, "sections", ()) or ())
    return {
        "draftRevisionId": str(getattr(draft, "draft_revision_id")),
        "parentDraftRevisionId": _str_or_none(getattr(draft, "base_revision_id", None)),
        "status": str(getattr(draft, "status", "unknown")),
        "title": "需求确认",
        "summary": _requirement_draft_snapshot_summary(sections),
        "canConfirm": bool(getattr(draft, "can_confirm", False)),
        "unresolvedReviewItemCount": int(getattr(draft, "unresolved_review_item_count", 0) or 0),
        "sections": [_requirement_draft_section_snapshot(section) for section in sections],
        "otherInputPrompt": "其他",
    }


def _requirement_draft_section_snapshot(section: object) -> dict[str, object]:
    section_id = str(getattr(section, "section_id"))
    return {
        "sectionId": section_id,
        "displayName": str(getattr(section, "display_name", section_id)),
        "backendField": str(getattr(section, "backend_field", section_id)),
        "items": [_requirement_draft_item_snapshot(section_id, item) for item in getattr(section, "items", ()) or ()],
    }


def _requirement_draft_item_snapshot(section_id: str, item: object) -> dict[str, object]:
    status = str(getattr(item, "status", "unknown"))
    return {
        "itemId": str(getattr(item, "item_id")),
        "sectionId": section_id,
        "selected": bool(getattr(item, "selected", False)),
        "enabled": bool(getattr(item, "enabled", False)),
        "editable": bool(getattr(item, "editable", False)),
        "text": str(getattr(item, "text", "")),
        "status": status if status in {"resolved", "needs_review", "deleted", "moved", "rejected"} else "unknown",
        "source": str(getattr(item, "source", "unknown")),
        "allowedActions": [str(action) for action in getattr(item, "allowed_actions", ()) or ()],
    }


def _requirement_draft_snapshot_summary(sections: list[object]) -> str:
    selected_count = sum(
        1
        for section in sections
        for item in getattr(section, "items", ()) or ()
        if bool(getattr(item, "selected", False)) and getattr(item, "status", "") == "resolved"
    )
    return f"已生成 {selected_count} 条已选择需求，请确认后启动检索。"


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


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
