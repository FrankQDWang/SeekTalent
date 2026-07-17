from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import Literal, Protocol, cast
from uuid import uuid4

from anyio import to_thread
from seektalent.models import RequirementSheet
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.requirements import (
    RequirementDraft,
    requirement_sheet_from_draft,
)
from seektalent_workbench_v2.agent_loop import (
    WorkbenchV2AgentLoop,
    WorkbenchV2AgentOutput,
    WorkbenchV2RequirementPatch,
    WorkbenchV2RuntimeInput,
)
from seektalent_workbench_v2.models import (
    WorkbenchV2CandidateDetailView,
    WorkbenchV2CandidateSummaryView,
    WorkbenchV2ConversationEventsView,
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationRecord,
    WorkbenchV2ConversationView,
    WorkbenchV2RuntimeState,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventInput,
)
from seektalent_workbench_v2.runtime_display import (
    COMPLETED_RESULT_SUMMARY,
    IDLE_RESULT_SUMMARY,
    PENDING_RESULT_SUMMARY,
    normalize_runtime_progress_payload,
    normalize_runtime_result_payload,
    runtime_progress_visible_summary as display_runtime_progress_visible_summary,
    runtime_result_summary,
)
from seektalent_workbench_v2.store import WorkbenchV2Store
from seektalent_workbench_v2.views import conversation_events_to_view, conversation_list_to_view, conversation_record_to_view


WorkbenchV2RequirementAction = Literal["set_selected", "add_other", "confirm"]
AGENT_PATCH_ERROR_MESSAGES = {
    "workbench_v2_requirement_form_required": "当前没有可更新的需求表单，请先发送完整招聘需求。",
    "workbench_v2_requirement_form_readonly": "需求已确认并开始运行，无法再修改。",
    "workbench_v2_requirement_draft_required": "需求表单缺少 draft，无法更新需求。",
    "workbench_v2_requirement_sheet_required": "需求表单缺少 requirementSheet，无法更新需求。",
    "workbench_v2_runtime_input_required": "需求表单缺少 runtimeInput，无法更新需求。",
    "workbench_v2_requirement_item_not_found": "未找到要修改的需求项，无法更新需求。",
    "workbench_v2_requirement_amendment_failed": "补充需求整理失败，请稍后重试。",
}
REQUIREMENT_FORM_READY_MESSAGE = "已根据你的输入生成需求确认表单，请检查、取消不需要的条件，或补充其他要求。"
POST_CONFIRM_SUPPLEMENTAL_SUMMARY = "已记录补充要求，将在下一轮检索时使用。"
POST_CONFIRM_SUPPLEMENTAL_NEXT_RUN_SUMMARY = "本次运行已结束，补充要求已记录为后续重新运行或下一次检索参考。"
REQUIREMENT_EXTRACT_FAILED_MESSAGE = "需求整理失败，请稍后重试。"
RUNTIME_STATUS_UNAVAILABLE_MESSAGE = "暂时无法读取运行状态，请稍后重试。"
RUNTIME_RESULTS_UNAVAILABLE_MESSAGE = "暂时无法读取运行结果，请稍后重试。"
SERVICE_BOUNDARY_ERRORS = (RuntimeControlError, RuntimeError, ValueError, TypeError, KeyError, AttributeError)
logger = logging.getLogger(__name__)
TURN_OPERATION_HEARTBEAT_SECONDS = 30.0


class CandidateSummaryReadError(Exception):
    pass


def _duration_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


class WorkbenchV2RequirementExtraction(Protocol):
    @property
    def draft(self) -> object: ...

    @property
    def requirement_sheet(self) -> RequirementSheet: ...


@dataclass(frozen=True)
class WorkbenchV2RuntimeSubmission:
    summary: str
    assistant_override: str | None
    payload: dict[str, object]


class WorkbenchV2RequirementRuntime(Protocol):
    def extract_requirement_bundle(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> WorkbenchV2RequirementExtraction: ...

    def extract_requirements(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> object: ...

    def amend_requirement_bundle(
        self,
        conversation_id: str,
        *,
        base_draft: RequirementDraft,
        base_requirement_sheet: RequirementSheet,
        text: str,
        idempotency_key: str,
    ) -> WorkbenchV2RequirementExtraction: ...

    def start_run(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput | None,
        requirement_sheet: RequirementSheet,
        *,
        idempotency_key: str | None = None,
        draft_revision_id: str | None = None,
        selected_item_ids: list[str] | None = None,
        deselected_item_ids: list[str] | None = None,
    ) -> object: ...

    def start_run_from_runtime_input(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
        *,
        idempotency_key: str | None = None,
        draft_revision_id: str | None = None,
        selected_item_ids: list[str] | None = None,
        deselected_item_ids: list[str] | None = None,
    ) -> object: ...

    def get_status(self, runtime_run_id: str) -> Mapping[str, object]: ...

    def get_results(self, runtime_run_id: str) -> Mapping[str, object]: ...

    def list_progress_events(self, runtime_run_id: str, *, after_seq: int, limit: int = 200) -> Sequence[Mapping[str, object]]: ...

    def list_candidate_summaries(self, runtime_run_id: str, *, limit: int = 20) -> Sequence[Mapping[str, object]]: ...

    def get_candidate_detail(self, runtime_run_id: str, candidate_id: str) -> Mapping[str, object]: ...

    def submit_next_round_requirement(
        self,
        runtime_run_id: str,
        text: str,
        *,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...


class WorkbenchV2Service:
    def __init__(
        self,
        *,
        store: WorkbenchV2Store,
        agent_loop: WorkbenchV2AgentLoop,
        runtime_service: WorkbenchV2RequirementRuntime,
    ) -> None:
        self.store = store
        self.agent_loop = agent_loop
        self.runtime_service = runtime_service

    async def create_conversation(self, message: str, idempotency_key: str | None) -> WorkbenchV2ConversationView:
        conversation = self.store.create_conversation(first_user_text=message, idempotency_key=idempotency_key)
        return await self._run_idempotent_turn(
            conversation_id=conversation.id,
            message=message,
            scope="create",
            idempotency_key=idempotency_key,
        )

    async def _run_idempotent_turn(
        self,
        *,
        conversation_id: str,
        message: str,
        scope: str,
        idempotency_key: str | None,
    ) -> WorkbenchV2ConversationView:
        if idempotency_key is None:
            return await self._append_user_and_run_agent(
                conversation_id=conversation_id,
                message=message,
                scope=scope,
                idempotency_key=None,
            )
        operation_key = f"{conversation_id}:{scope}:{idempotency_key}"
        owner_token = uuid4().hex
        while True:
            claim = self.store.try_claim_turn_operation(
                operation_key=operation_key,
                conversation_id=conversation_id,
                owner_token=owner_token,
            )
            if claim == "completed":
                self._raise_if_user_event_conflicts(
                    conversation_id,
                    message=message,
                    scope=scope,
                    idempotency_key=idempotency_key,
                )
                return self.get_conversation(conversation_id)
            if claim == "claimed":
                break
            await asyncio.sleep(0.05)
        owner_task = asyncio.current_task()
        heartbeat = asyncio.create_task(
            self._renew_turn_operation_lease(
                operation_key=operation_key,
                owner_token=owner_token,
                owner_task=owner_task,
            )
        )
        try:
            self._raise_if_user_event_conflicts(
                conversation_id,
                message=message,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            if self._has_terminal_event(conversation_id, scope=scope, idempotency_key=idempotency_key):
                result = self.get_conversation(conversation_id)
            else:
                result = await self._append_user_and_run_agent(
                    conversation_id=conversation_id,
                    message=message,
                    scope=scope,
                    idempotency_key=idempotency_key,
                )
            self.store.complete_turn_operation(operation_key=operation_key, owner_token=owner_token)
            return result
        except BaseException:
            self.store.release_turn_operation(operation_key=operation_key, owner_token=owner_token)
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _renew_turn_operation_lease(
        self,
        *,
        operation_key: str,
        owner_token: str,
        owner_task: asyncio.Task[object] | None,
    ) -> None:
        while True:
            await asyncio.sleep(TURN_OPERATION_HEARTBEAT_SECONDS)
            if self.store.renew_turn_operation(operation_key=operation_key, owner_token=owner_token):
                continue
            if owner_task is not None:
                owner_task.cancel()
            return

    async def submit_message(
        self,
        conversation_id: str,
        message: str,
        idempotency_key: str | None,
    ) -> WorkbenchV2ConversationView:
        return await self._run_idempotent_turn(
            conversation_id=conversation_id,
            message=message,
            scope="submit",
            idempotency_key=idempotency_key,
        )

    def get_conversation(self, conversation_id: str) -> WorkbenchV2ConversationView:
        self._refresh_active_runtime_status(conversation_id)
        return self._conversation_record_to_view(self.store.get_conversation(conversation_id))

    def list_events(
        self,
        conversation_id: str,
        *,
        after_step: int = 0,
        limit: int = 100,
    ) -> WorkbenchV2ConversationEventsView:
        self._refresh_active_runtime_status(conversation_id)
        return conversation_events_to_view(
            self.store.get_conversation(conversation_id),
            after_step=after_step,
            limit=limit,
        )

    def list_conversations(self) -> WorkbenchV2ConversationListView:
        return conversation_list_to_view(self.store.list_conversations())

    def get_candidate_detail(self, conversation_id: str, candidate_id: str) -> WorkbenchV2CandidateDetailView:
        self._refresh_active_runtime_status(conversation_id)
        record = self.store.get_conversation(conversation_id)
        runtime_run_id = record.conversation.runtime_run_id
        if runtime_run_id is None:
            raise KeyError(candidate_id)
        payload = self.runtime_service.get_candidate_detail(runtime_run_id, candidate_id)
        return WorkbenchV2CandidateDetailView.model_validate(payload)

    def _conversation_record_to_view(self, record: WorkbenchV2ConversationRecord) -> WorkbenchV2ConversationView:
        view = conversation_record_to_view(record)
        runtime_run_id = record.conversation.runtime_run_id
        if runtime_run_id is None:
            return view
        try:
            candidates = self._candidate_summaries(runtime_run_id)
        except CandidateSummaryReadError:
            self.store.append_event(
                record.conversation.id,
                WorkbenchV2TranscriptEventInput(
                    type="error",
                    role="system",
                    payload={
                        "code": "workbench_v2_candidate_summaries_unavailable",
                        "message": "候选人列表读取失败，请稍后重试。",
                    },
                    status="failed",
                    dedupe_key=f"workbench-v2-candidates:{runtime_run_id}:summary-read-error",
                ),
            )
            view = conversation_record_to_view(self.store.get_conversation(record.conversation.id))
            candidates = []
        if not candidates:
            return view
        return view.model_copy(update={"candidates": candidates})

    def _candidate_summaries(self, runtime_run_id: str) -> list[WorkbenchV2CandidateSummaryView]:
        list_candidate_summaries = getattr(self.runtime_service, "list_candidate_summaries", None)
        if not callable(list_candidate_summaries):
            return []
        try:
            payloads = list_candidate_summaries(runtime_run_id, limit=20)
        except SERVICE_BOUNDARY_ERRORS:
            raise CandidateSummaryReadError from None
        try:
            return [WorkbenchV2CandidateSummaryView.model_validate(payload) for payload in payloads]
        except ValueError:
            raise CandidateSummaryReadError from None

    async def apply_requirement_action(
        self,
        conversation_id: str,
        *,
        action: WorkbenchV2RequirementAction,
        item_id: str | None = None,
        selected: bool | None = None,
        text: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkbenchV2ConversationView:
        scope = _requirement_action_scope()
        action_digest = _requirement_action_payload_digest(
            conversation_id=conversation_id,
            action=action,
            item_id=item_id,
            selected=selected,
            text=text,
        )
        if self._has_requirement_action_terminal_event(
            conversation_id,
            scope=scope,
            action_digest=action_digest,
            idempotency_key=idempotency_key,
        ):
            return self.get_conversation(conversation_id)

        if action == "set_selected":
            if item_id is None or selected is None:
                raise ValueError("workbench_v2_requirement_action_invalid")
            self._raise_if_requirement_form_readonly(conversation_id)
            self._set_requirement_selected(
                conversation_id,
                item_id=item_id,
                selected=selected,
                scope=scope,
                action_digest=action_digest,
                idempotency_key=idempotency_key,
            )
            return self.get_conversation(conversation_id)

        if action == "add_other":
            if text is None or not text:
                raise ValueError("workbench_v2_requirement_action_invalid")
            self._raise_if_requirement_form_readonly(conversation_id)
            await to_thread.run_sync(
                partial(
                    self._amend_requirement_form_from_text,
                    conversation_id,
                    text=text,
                    scope=scope,
                    action_digest=action_digest,
                    idempotency_key=idempotency_key,
                )
            )
            return self.get_conversation(conversation_id)

        if action == "confirm":
            if self._requirement_form_is_readonly(conversation_id):
                return self.get_conversation(conversation_id)
            supplemental_text = str(text or "").strip()
            if supplemental_text:
                amended = await to_thread.run_sync(
                    partial(
                        self._amend_requirement_form_from_text,
                        conversation_id,
                        text=supplemental_text,
                        scope=scope,
                        action_digest=action_digest,
                        idempotency_key=idempotency_key,
                    )
                )
                if not amended:
                    return self.get_conversation(conversation_id)
            await to_thread.run_sync(
                partial(
                    self._confirm_requirements,
                    conversation_id=conversation_id,
                    message="已确认需求，开始运行。",
                    scope=scope,
                    idempotency_key=idempotency_key,
                    raise_domain_errors=True,
                    action_digest=action_digest,
                )
            )
            return self.get_conversation(conversation_id)

        raise ValueError("workbench_v2_requirement_action_invalid")

    async def _append_user_and_run_agent(
        self,
        *,
        conversation_id: str,
        message: str,
        scope: str,
        idempotency_key: str | None,
    ) -> WorkbenchV2ConversationView:
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="user_message",
                role="user",
                payload={"text": message},
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="user"),
            ),
        )
        record = self.store.get_conversation(conversation_id)
        logger.info(
            "Workbench v2 user message persisted.",
            extra={
                "event_name": "workbench_v2_user_message_persisted",
                "conversation_id": conversation_id,
                "scope": scope,
                "event_count": len(record.events),
                "user_text_chars": len(message),
                "context_summary_chars": len(record.conversation.context_summary or ""),
            },
        )
        agent_started_at = perf_counter()
        try:
            output = await self.agent_loop.run_turn(
                conversation_id=conversation_id,
                context_summary=record.conversation.context_summary,
                recent_events=record.events,
                user_text=message,
            )
        except Exception as exc:
            logger.warning(
                "Workbench v2 agent loop failed.",
                extra={
                    "event_name": "workbench_v2_agent_loop_failed",
                    "conversation_id": conversation_id,
                    "scope": scope,
                    "duration_ms": _duration_ms(agent_started_at),
                    "error_type": exc.__class__.__name__,
                },
            )
            raise
        logger.info(
            "Workbench v2 agent loop completed.",
            extra={
                "event_name": "workbench_v2_agent_loop_completed",
                "conversation_id": conversation_id,
                "scope": scope,
                "duration_ms": _duration_ms(agent_started_at),
                "intent": output.intent,
                "needs_clarification": output.needsClarification,
            },
        )
        await to_thread.run_sync(
            partial(
                self._apply_agent_output,
                conversation_id=conversation_id,
                output=output,
                user_text=message,
                scope=scope,
                idempotency_key=idempotency_key,
            )
        )
        return self.get_conversation(conversation_id)

    def _apply_agent_output(
        self,
        *,
        conversation_id: str,
        output: WorkbenchV2AgentOutput,
        user_text: str,
        scope: str,
        idempotency_key: str | None,
    ) -> None:
        if output.needsClarification:
            self._append_assistant_message(
                conversation_id,
                text=output.clarifyingQuestion or output.message,
                payload_extra={"needsClarification": True},
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
            )
            return

        if output.intent == "chat":
            self._append_assistant_message(
                conversation_id,
                text=output.message,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
            )
            return

        if output.intent in {"extract_requirements", "update_requirements"} and output.runtimeInput is not None:
            runtime_input = _runtime_input_with_full_jd_fallback(output.runtimeInput, user_text)
            if self._requirement_form_is_readonly(conversation_id):
                assistant_text_override = self._append_post_confirm_runtime_input(
                    conversation_id,
                    runtime_input=runtime_input,
                    scope=scope,
                    idempotency_key=idempotency_key,
                )
                self._append_assistant_message(
                    conversation_id,
                    text=assistant_text_override or output.message,
                    dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
                )
                return
            self._append_requirement_form_from_runtime_input(
                conversation_id,
                runtime_input=runtime_input,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return

        if output.intent == "update_requirements" and output.requirementPatch is not None:
            patch_digest = _agent_requirement_patch_payload_digest(
                conversation_id=conversation_id,
                patch=output.requirementPatch,
            )
            if not self._has_agent_patch_event(
                conversation_id,
                scope=scope,
                patch_digest=patch_digest,
                idempotency_key=idempotency_key,
            ):
                if self._requirement_form_is_readonly(conversation_id):
                    assistant_text_override = self._append_post_confirm_requirement_patch(
                        conversation_id,
                        patch=output.requirementPatch,
                        scope=scope,
                        action_digest=patch_digest,
                        idempotency_key=idempotency_key,
                    )
                    self._append_assistant_message(
                        conversation_id,
                        text=assistant_text_override or output.message,
                        dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
                    )
                    return
                try:
                    self._apply_requirement_patch(
                        conversation_id=conversation_id,
                        patch=output.requirementPatch,
                        scope=scope,
                        action_digest=patch_digest,
                        idempotency_key=idempotency_key,
                    )
                except ValueError as exc:
                    if str(exc) == "workbench_v2_idempotency_conflict":
                        raise
                    error_message = AGENT_PATCH_ERROR_MESSAGES.get(str(exc))
                    if error_message is None:
                        raise
                    self._append_service_error(
                        conversation_id,
                        code=str(exc),
                        message=error_message,
                        scope=scope,
                        idempotency_key=idempotency_key,
                    )
                    return
            self._append_assistant_message(
                conversation_id,
                text=output.message,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
            )
            return

        if output.intent == "confirm_requirements":
            self._confirm_requirements(
                conversation_id=conversation_id,
                message=output.message,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return

        if output.intent == "get_runtime_status":
            self._append_runtime_status(conversation_id, scope=scope, idempotency_key=idempotency_key)
            self._append_assistant_message(
                conversation_id,
                text=output.message,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
            )
            return

        if output.intent == "get_runtime_results":
            result_payload = self._append_runtime_results(
                conversation_id,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            self._append_assistant_message(
                conversation_id,
                text=_runtime_result_question_reply(result_payload),
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
            )
            return

        self._append_assistant_message(
            conversation_id,
            text=output.message,
            payload_extra={"intent": output.intent},
            dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
        )

    def _append_requirement_form_from_runtime_input(
        self,
        conversation_id: str,
        *,
        runtime_input: WorkbenchV2RuntimeInput,
        scope: str,
        idempotency_key: str | None,
    ) -> None:
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="assistant_status",
                role="assistant",
                payload={"phase": "extract_requirements", "text": "正在整理需求表单。"},
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="status"),
            ),
        )
        extract_started_at = perf_counter()
        extract_log_extra = {
            "conversation_id": conversation_id,
            "scope": scope,
            "job_title_chars": len(runtime_input.jobTitle),
            "jd_chars": len(runtime_input.jd),
            "notes_chars": len(runtime_input.notes or ""),
        }
        logger.info(
            "Workbench v2 requirement extract started.",
            extra={**extract_log_extra, "event_name": "workbench_v2_requirement_extract_started"},
        )
        try:
            draft, requirement_sheet = self._extract_requirement_form(conversation_id, runtime_input)
        except SERVICE_BOUNDARY_ERRORS as exc:
            logger.warning(
                "Workbench v2 requirement extract failed.",
                extra={
                    **extract_log_extra,
                    "event_name": "workbench_v2_requirement_extract_failed",
                    "duration_ms": _duration_ms(extract_started_at),
                    "error_type": exc.__class__.__name__,
                },
            )
            self._append_service_error(
                conversation_id,
                code="workbench_v2_requirement_extract_failed",
                message=REQUIREMENT_EXTRACT_FAILED_MESSAGE,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return
        logger.info(
            "Workbench v2 requirement extract completed.",
            extra={
                **extract_log_extra,
                "event_name": "workbench_v2_requirement_extract_completed",
                "duration_ms": _duration_ms(extract_started_at),
            },
        )
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="requirement_form",
                role="assistant",
                payload={
                    "runtimeInput": _dump_mapping(runtime_input),
                    "draft": _dump_mapping(draft),
                    "requirementSheet": _dump_mapping(requirement_sheet),
                },
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="requirement-form"),
            ),
        )
        self._append_assistant_message(
            conversation_id,
            text=REQUIREMENT_FORM_READY_MESSAGE,
            dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
        )

    def _extract_requirement_form(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> tuple[object, RequirementSheet]:
        bundle = self.runtime_service.extract_requirement_bundle(conversation_id, runtime_input)
        return bundle.draft, bundle.requirement_sheet

    def _confirm_requirements(
        self,
        *,
        conversation_id: str,
        message: str,
        scope: str,
        idempotency_key: str | None,
        raise_domain_errors: bool = False,
        action_digest: str | None = None,
    ) -> None:
        if self._requirement_form_is_readonly(conversation_id):
            return
        form_payload = _latest_requirement_form_payload(self.store.get_conversation(conversation_id).events)
        if form_payload is None:
            if raise_domain_errors:
                raise ValueError("workbench_v2_requirement_form_required")
            self._append_service_error(
                conversation_id,
                code="workbench_v2_requirement_form_required",
                message="当前没有可确认的需求表单，无法启动运行。",
                scope=scope,
                idempotency_key=idempotency_key,
                action_digest=action_digest,
            )
            return
        runtime_input = _runtime_input_from_payload(form_payload.get("runtimeInput"))
        if runtime_input is None:
            if raise_domain_errors:
                raise ValueError("workbench_v2_runtime_input_required")
            self._append_service_error(
                conversation_id,
                code="workbench_v2_runtime_input_required",
                message="需求表单缺少 runtimeInput，无法启动运行。",
                scope=scope,
                idempotency_key=idempotency_key,
                action_digest=action_digest,
            )
            return
        draft_payload = _mapping_or_none(form_payload.get("draft"))
        requirement_sheet = _requirement_sheet_from_payload(form_payload.get("requirementSheet"))
        if requirement_sheet is None:
            self._append_requirement_sheet_required_error(
                conversation_id,
                scope=scope,
                idempotency_key=idempotency_key,
                raise_domain_error=raise_domain_errors,
                action_digest=action_digest,
            )
            return
        self._start_runtime_from_requirement_sheet(
            conversation_id=conversation_id,
            runtime_input=runtime_input,
            requirement_sheet=requirement_sheet,
            confirmed_payload=dict(form_payload),
            message=message,
            scope=scope,
            idempotency_key=idempotency_key,
            draft_revision_id=_draft_revision_id(draft_payload),
            selected_item_ids=_selected_item_ids(draft_payload),
            deselected_item_ids=_deselected_item_ids(draft_payload),
            action_digest=action_digest,
        )

    def _append_requirement_sheet_required_error(
        self,
        conversation_id: str,
        *,
        scope: str,
        idempotency_key: str | None,
        raise_domain_error: bool = False,
        action_digest: str | None = None,
    ) -> None:
        if raise_domain_error:
            raise ValueError("workbench_v2_requirement_sheet_required")
        self._append_service_error(
            conversation_id,
            code="workbench_v2_requirement_sheet_required",
            message="需求表单缺少 requirementSheet，无法启动运行。",
            scope=scope,
            idempotency_key=idempotency_key,
            action_digest=action_digest,
        )

    def _raise_if_requirement_form_readonly(self, conversation_id: str) -> None:
        if self._requirement_form_is_readonly(conversation_id):
            raise ValueError("workbench_v2_requirement_form_readonly")

    def _requirement_form_is_readonly(self, conversation_id: str) -> bool:
        record = self.store.get_conversation(conversation_id)
        return record.conversation.runtime_run_id is not None or any(
            event.type == "requirement_form_confirmed" for event in record.events
        )

    def _set_requirement_selected(
        self,
        conversation_id: str,
        *,
        item_id: str,
        selected: bool,
        scope: str,
        action_digest: str,
        idempotency_key: str | None,
    ) -> None:
        form_payload, draft, requirement_sheet = self._current_requirement_form_bundle(conversation_id)
        _set_draft_item_selected(draft, item_id=item_id, selected=selected)
        self._append_updated_requirement_form(
            conversation_id,
            form_payload=form_payload,
            draft=_with_new_draft_revision(draft),
            requirement_sheet=requirement_sheet,
            scope=scope,
            action_digest=action_digest,
            idempotency_key=idempotency_key,
        )

    def _apply_requirement_patch(
        self,
        *,
        conversation_id: str,
        patch: WorkbenchV2RequirementPatch,
        scope: str,
        action_digest: str,
        idempotency_key: str | None,
    ) -> None:
        form_payload, draft, requirement_sheet = self._current_requirement_form_bundle(conversation_id)
        for item_id in patch.selectedItemIds:
            _set_draft_item_selected(draft, item_id=item_id, selected=True)
        for item_id in patch.deselectedItemIds:
            _set_draft_item_selected(draft, item_id=item_id, selected=False)
        if patch.otherNotes:
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="assistant_status",
                    role="assistant",
                    payload={"phase": "requirement_amendment", "text": "正在根据补充要求更新需求，请稍候。"},
                    dedupe_key=_action_event_dedupe_key(
                        scope=scope,
                        idempotency_key=idempotency_key,
                        action_digest=action_digest,
                        suffix="supplement-status",
                    ),
                ),
            )
            try:
                amendment = self.runtime_service.amend_requirement_bundle(
                    conversation_id,
                    base_draft=draft,
                    base_requirement_sheet=requirement_sheet,
                    text=patch.otherNotes,
                    idempotency_key=idempotency_key or f"workbench-v2-requirement-amend:{conversation_id}:{action_digest}",
                )
            except SERVICE_BOUNDARY_ERRORS as exc:
                raise ValueError("workbench_v2_requirement_amendment_failed") from exc
            if not isinstance(amendment.draft, RequirementDraft):
                raise ValueError("workbench_v2_requirement_draft_required")
            draft = amendment.draft
            requirement_sheet = amendment.requirement_sheet
        self._append_updated_requirement_form(
            conversation_id,
            form_payload=form_payload,
            draft=(_with_new_draft_revision(draft) if not patch.otherNotes else draft),
            requirement_sheet=requirement_sheet,
            scope=scope,
            action_digest=action_digest,
            idempotency_key=idempotency_key,
        )

    def _amend_requirement_form_from_text(
        self,
        conversation_id: str,
        *,
        text: str,
        scope: str,
        action_digest: str,
        idempotency_key: str | None,
    ) -> bool:
        form_payload, draft, requirement_sheet = self._current_requirement_form_bundle(conversation_id)
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="user_message",
                role="user",
                payload={"text": text},
                dedupe_key=_action_event_dedupe_key(
                    scope=scope,
                    idempotency_key=idempotency_key,
                    action_digest=action_digest,
                    suffix="supplement-user",
                ),
            ),
        )
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="assistant_status",
                role="assistant",
                payload={"phase": "requirement_amendment", "text": "正在根据补充要求更新需求，请稍候。"},
                dedupe_key=_action_event_dedupe_key(
                    scope=scope,
                    idempotency_key=idempotency_key,
                    action_digest=action_digest,
                    suffix="supplement-status",
                ),
            ),
        )
        try:
            amendment = self.runtime_service.amend_requirement_bundle(
                conversation_id,
                base_draft=draft,
                base_requirement_sheet=requirement_sheet,
                text=text,
                idempotency_key=idempotency_key or f"workbench-v2-requirement-amend:{conversation_id}:{action_digest}",
            )
        except SERVICE_BOUNDARY_ERRORS:
            self._append_service_error(
                conversation_id,
                code="workbench_v2_requirement_amendment_failed",
                message="补充需求整理失败，请稍后重试。",
                scope=scope,
                idempotency_key=idempotency_key,
                action_digest=action_digest,
            )
            return False
        if not isinstance(amendment.draft, RequirementDraft):
            self._append_service_error(
                conversation_id,
                code="workbench_v2_requirement_draft_required",
                message=AGENT_PATCH_ERROR_MESSAGES["workbench_v2_requirement_draft_required"],
                scope=scope,
                idempotency_key=idempotency_key,
                action_digest=action_digest,
            )
            return False
        self._append_updated_requirement_form(
            conversation_id,
            form_payload=form_payload,
            draft=amendment.draft,
            requirement_sheet=amendment.requirement_sheet,
            scope=scope,
            action_digest=action_digest,
            idempotency_key=idempotency_key,
        )
        return True

    def _append_post_confirm_requirement_patch(
        self,
        conversation_id: str,
        *,
        patch: WorkbenchV2RequirementPatch,
        scope: str,
        action_digest: str,
        idempotency_key: str | None,
    ) -> str | None:
        record = self.store.get_conversation(conversation_id)
        supplemental_requirement = _post_confirm_requirement_text(patch)
        runtime_submission = self._submit_next_round_requirement(
            record.conversation.runtime_run_id,
            supplemental_requirement,
            scope=scope,
            idempotency_key=idempotency_key,
            action_digest=action_digest,
        )
        payload: dict[str, object] = {
            "phase": "supplemental_requirement",
            "text": runtime_submission.summary,
            "supplementalRequirement": supplemental_requirement,
        }
        if record.conversation.runtime_run_id is not None:
            payload["runtimeRunId"] = record.conversation.runtime_run_id
        payload.update(runtime_submission.payload)
        if patch.selectedItemIds:
            payload["selectedItemIds"] = list(patch.selectedItemIds)
        if patch.deselectedItemIds:
            payload["deselectedItemIds"] = list(patch.deselectedItemIds)
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="assistant_status",
                role="assistant",
                payload=payload,
                dedupe_key=_action_event_dedupe_key(
                    scope=scope,
                    idempotency_key=idempotency_key,
                    action_digest=action_digest,
                    suffix="post-confirm-requirement",
                ),
            ),
        )
        return runtime_submission.assistant_override

    def _append_post_confirm_runtime_input(
        self,
        conversation_id: str,
        *,
        runtime_input: WorkbenchV2RuntimeInput,
        scope: str,
        idempotency_key: str | None,
    ) -> str | None:
        record = self.store.get_conversation(conversation_id)
        supplemental_requirement = _post_confirm_runtime_input_text(runtime_input)
        runtime_submission = self._submit_next_round_requirement(
            record.conversation.runtime_run_id,
            supplemental_requirement,
            scope=scope,
            idempotency_key=idempotency_key,
            action_digest=_service_payload_digest({"runtimeInput": runtime_input.model_dump(mode="json")}),
        )
        payload: dict[str, object] = {
            "phase": "supplemental_requirement",
            "text": runtime_submission.summary,
            "supplementalRequirement": supplemental_requirement,
        }
        if record.conversation.runtime_run_id is not None:
            payload["runtimeRunId"] = record.conversation.runtime_run_id
        payload.update(runtime_submission.payload)
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="assistant_status",
                role="assistant",
                payload=payload,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="post-confirm-runtime-input"),
            ),
        )
        return runtime_submission.assistant_override

    def _current_requirement_form_bundle(
        self,
        conversation_id: str,
    ) -> tuple[dict[str, object], RequirementDraft, RequirementSheet]:
        form_payload = _latest_requirement_form_payload(self.store.get_conversation(conversation_id).events)
        if form_payload is None:
            raise ValueError("workbench_v2_requirement_form_required")
        if not isinstance(form_payload.get("runtimeInput"), Mapping):
            raise ValueError("workbench_v2_runtime_input_required")
        draft = _requirement_draft_from_payload(form_payload.get("draft"))
        if draft is None:
            raise ValueError("workbench_v2_requirement_draft_required")
        requirement_sheet = _requirement_sheet_from_payload(form_payload.get("requirementSheet"))
        if requirement_sheet is None:
            raise ValueError("workbench_v2_requirement_sheet_required")
        return form_payload, draft.model_copy(deep=True), requirement_sheet

    def _append_updated_requirement_form(
        self,
        conversation_id: str,
        *,
        form_payload: Mapping[str, object],
        draft: RequirementDraft,
        requirement_sheet: RequirementSheet,
        scope: str,
        action_digest: str,
        idempotency_key: str | None,
    ) -> None:
        updated_requirement_sheet = requirement_sheet_from_draft(draft, requirement_sheet)
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="requirement_form",
                role="assistant",
                payload={
                    "runtimeInput": _dump(form_payload["runtimeInput"]),
                    "draft": _dump_mapping(draft),
                    "requirementSheet": _dump_mapping(updated_requirement_sheet),
                },
                dedupe_key=_action_event_dedupe_key(
                    scope=scope,
                    idempotency_key=idempotency_key,
                    action_digest=action_digest,
                    suffix="requirement-form",
                ),
            ),
        )

    def _submit_next_round_requirement(
        self,
        runtime_run_id: str | None,
        supplemental_requirement: str,
        *,
        scope: str,
        idempotency_key: str | None,
        action_digest: str,
    ) -> WorkbenchV2RuntimeSubmission:
        if runtime_run_id is None:
            return WorkbenchV2RuntimeSubmission(
                summary=POST_CONFIRM_SUPPLEMENTAL_NEXT_RUN_SUMMARY,
                assistant_override=POST_CONFIRM_SUPPLEMENTAL_NEXT_RUN_SUMMARY,
                payload={"runtimeSubmissionStatus": "not_started"},
            )
        try:
            runtime_idempotency_key = _dedupe_key(
                scope=scope,
                idempotency_key=idempotency_key,
                suffix=f"runtime-next-round:{action_digest}",
            )
            if runtime_idempotency_key is None:
                runtime_idempotency_key = f"workbench-v2-runtime-next-round:{runtime_run_id}:{action_digest}"
            result = self.runtime_service.submit_next_round_requirement(
                runtime_run_id,
                supplemental_requirement,
                idempotency_key=runtime_idempotency_key,
            )
        except RuntimeControlError as exc:
            if str(exc) not in {"runtime_command_conflict", "runtime_no_future_round_available"}:
                raise
            return WorkbenchV2RuntimeSubmission(
                summary=POST_CONFIRM_SUPPLEMENTAL_NEXT_RUN_SUMMARY,
                assistant_override=POST_CONFIRM_SUPPLEMENTAL_NEXT_RUN_SUMMARY,
                payload={"runtimeSubmissionStatus": "not_applied", "reasonCode": str(exc)},
            )
        target_round_no = result.get("targetRoundNo")
        status = str(result.get("status") or "submitted")
        summary = (
            f"已记录补充要求，将在第 {target_round_no} 轮检索前生效。"
            if isinstance(target_round_no, int)
            else POST_CONFIRM_SUPPLEMENTAL_SUMMARY
        )
        assistant_override: str | None = None
        if status == "needs_review":
            summary = "补充要求已记录，需要复核后才能在后续检索轮次生效。"
            assistant_override = summary
        return WorkbenchV2RuntimeSubmission(
            summary=summary,
            assistant_override=assistant_override,
            payload={
                "runtimeSubmissionStatus": status,
                "targetRoundNo": target_round_no,
                "amendmentId": result.get("amendmentId"),
            },
        )

    def _start_runtime_from_requirement_sheet(
        self,
        *,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
        requirement_sheet: RequirementSheet,
        confirmed_payload: dict[str, object],
        message: str,
        scope: str,
        idempotency_key: str | None,
        draft_revision_id: str | None,
        selected_item_ids: list[str] | None,
        deselected_item_ids: list[str] | None,
        action_digest: str | None = None,
    ) -> None:
        try:
            run = self.runtime_service.start_run(
                conversation_id,
                runtime_input,
                requirement_sheet,
                idempotency_key=idempotency_key,
                draft_revision_id=draft_revision_id,
                selected_item_ids=selected_item_ids,
                deselected_item_ids=deselected_item_ids,
            )
        except SERVICE_BOUNDARY_ERRORS:
            self._append_runtime_start_failed_error(
                conversation_id,
                scope=scope,
                idempotency_key=idempotency_key,
                action_digest=action_digest,
            )
            return
        self._append_started_runtime(
            conversation_id=conversation_id,
            run=run,
            confirmed_payload=confirmed_payload,
            message=message,
            scope=scope,
            idempotency_key=idempotency_key,
            action_digest=action_digest,
        )

    def _append_started_runtime(
        self,
        *,
        conversation_id: str,
        run: object,
        confirmed_payload: dict[str, object],
        message: str,
        scope: str,
        idempotency_key: str | None,
        action_digest: str | None = None,
    ) -> None:
        runtime_run_id = _required_text_attr(run, "runtime_run_id")
        runtime_state = _runtime_state_from_run_status(getattr(run, "status", None))
        self.store.set_runtime(conversation_id, runtime_run_id=runtime_run_id, runtime_state=runtime_state)
        confirmed_payload.update({"readonly": True, "runtimeRunId": runtime_run_id})
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="requirement_form_confirmed",
                role="assistant",
                payload=confirmed_payload,
                dedupe_key=_action_event_dedupe_key(
                    scope=scope,
                    idempotency_key=idempotency_key,
                    action_digest=action_digest,
                    suffix="requirement-form-confirmed",
                ),
            ),
        )
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="runtime_progress",
                role="runtime",
                payload={
                    "state": runtime_state,
                    "runtimeRunId": runtime_run_id,
                    "summary": "招聘流程已排队，等待开始。",
                },
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="runtime-progress"),
            ),
        )
        self._append_assistant_message(
            conversation_id,
            text=message,
            dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
        )

    def _append_runtime_start_failed_error(
        self,
        conversation_id: str,
        *,
        scope: str,
        idempotency_key: str | None,
        action_digest: str | None = None,
    ) -> None:
        self._append_service_error(
            conversation_id,
            code="workbench_v2_runtime_start_failed",
            message="运行启动失败，请稍后重试。",
            scope=scope,
            idempotency_key=idempotency_key,
            action_digest=action_digest,
        )

    def _append_service_error(
        self,
        conversation_id: str,
        *,
        code: str,
        message: str,
        scope: str,
        idempotency_key: str | None,
        action_digest: str | None = None,
    ) -> None:
        self._append_error_event(
            conversation_id,
            code=code,
            message=message,
            scope=scope,
            idempotency_key=idempotency_key,
            action_digest=action_digest,
        )
        self._append_assistant_message(
            conversation_id,
            text=message,
            dedupe_key=_action_event_dedupe_key(
                scope=scope,
                idempotency_key=idempotency_key,
                action_digest=action_digest,
                suffix="assistant",
            ),
        )

    def _append_error_event(
        self,
        conversation_id: str,
        *,
        code: str,
        message: str,
        scope: str,
        idempotency_key: str | None,
        action_digest: str | None = None,
    ) -> None:
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="error",
                role="system",
                status="failed",
                payload={"code": code, "message": message},
                dedupe_key=_action_event_dedupe_key(
                    scope=scope,
                    idempotency_key=idempotency_key,
                    action_digest=action_digest,
                    suffix="error",
                ),
            ),
        )

    def _append_assistant_message(
        self,
        conversation_id: str,
        *,
        text: str,
        dedupe_key: str | None,
        payload_extra: dict[str, object] | None = None,
    ) -> WorkbenchV2TranscriptEvent:
        payload: dict[str, object] = {"text": text}
        if payload_extra is not None:
            payload.update(payload_extra)
        return self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="assistant_message",
                role="assistant",
                payload=payload,
                dedupe_key=dedupe_key,
            ),
        )

    def _append_runtime_status(
        self, conversation_id: str, *, scope: str, idempotency_key: str | None
    ) -> dict[str, object] | None:
        record = self.store.get_conversation(conversation_id)
        runtime_run_id = record.conversation.runtime_run_id
        if runtime_run_id is None:
            payload: dict[str, object] = {"state": "idle", "summary": "当前还没有开始运行。"}
            if _runtime_progress_visible_signature(
                _latest_runtime_progress_payload(record.events)
            ) == _runtime_progress_visible_signature(payload):
                return payload
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="runtime_progress",
                    role="runtime",
                    payload=payload,
                    dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="runtime-status"),
                ),
            )
            return payload
        get_status = getattr(self.runtime_service, "get_status", None)
        if not callable(get_status):
            self._append_error_event(
                conversation_id,
                code="workbench_v2_runtime_status_unavailable",
                message=RUNTIME_STATUS_UNAVAILABLE_MESSAGE,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return None
        try:
            status_payload = _required_mapping(get_status(runtime_run_id), "runtime status payload")
        except SERVICE_BOUNDARY_ERRORS:
            self._append_error_event(
                conversation_id,
                code="workbench_v2_runtime_status_unavailable",
                message=RUNTIME_STATUS_UNAVAILABLE_MESSAGE,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return None
        payload = dict(status_payload)
        runtime_state = _runtime_state_from_status_payload(payload)
        payload["state"] = runtime_state
        payload = normalize_runtime_progress_payload(payload)
        self.store.set_runtime(conversation_id, runtime_run_id=runtime_run_id, runtime_state=runtime_state)
        visible_summary = _runtime_progress_visible_summary(payload)
        if _runtime_progress_visible_signature(
            _latest_runtime_progress_payload(record.events)
        ) == _runtime_progress_visible_signature(payload) or _has_runtime_progress_visible_summary(
            record.events,
            runtime_run_id=runtime_run_id,
            visible_summary=visible_summary,
        ):
            return payload
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="runtime_progress",
                role="runtime",
                payload=payload,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="runtime-status"),
            ),
        )
        return payload

    def _append_runtime_results(
        self, conversation_id: str, *, scope: str, idempotency_key: str | None
    ) -> dict[str, object] | None:
        record = self.store.get_conversation(conversation_id)
        runtime_run_id = record.conversation.runtime_run_id
        if runtime_run_id is None:
            payload: dict[str, object] = {"state": "idle", "summary": "当前还没有运行结果。"}
            return payload
        get_results = getattr(self.runtime_service, "get_results", None)
        if not callable(get_results):
            self._append_error_event(
                conversation_id,
                code="workbench_v2_runtime_results_unavailable",
                message=RUNTIME_RESULTS_UNAVAILABLE_MESSAGE,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return None
        try:
            results_payload = _required_mapping(get_results(runtime_run_id), "runtime results payload")
        except SERVICE_BOUNDARY_ERRORS:
            self._append_error_event(
                conversation_id,
                code="workbench_v2_runtime_results_unavailable",
                message=RUNTIME_RESULTS_UNAVAILABLE_MESSAGE,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return None
        payload = dict(results_payload)
        runtime_state = _runtime_state_from_status_payload(payload)
        payload["state"] = runtime_state
        payload.setdefault("runtimeRunId", runtime_run_id)
        payload = normalize_runtime_result_payload(payload)
        self.store.set_runtime(conversation_id, runtime_run_id=runtime_run_id, runtime_state=runtime_state)
        if runtime_state != "completed":
            return payload
        if _has_runtime_result_visible_summary(
            record.events,
            runtime_run_id=runtime_run_id,
            visible_summary=str(payload["summary"]),
        ):
            return payload
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="runtime_result",
                role="runtime",
                payload=payload,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="runtime-results"),
            ),
        )
        return payload

    def _refresh_active_runtime_status(self, conversation_id: str) -> None:
        record = self.store.get_conversation(conversation_id)
        runtime_run_id = record.conversation.runtime_run_id
        if runtime_run_id is None:
            return
        projected_runtime_events = self._append_runtime_progress_events(
            conversation_id,
            runtime_run_id=runtime_run_id,
            record=record,
        )
        if projected_runtime_events:
            record = self.store.get_conversation(conversation_id)
        get_status = getattr(self.runtime_service, "get_status", None)
        if not callable(get_status):
            self._append_runtime_refresh_error(conversation_id, runtime_run_id=runtime_run_id)
            return
        try:
            status_payload = _required_mapping(get_status(runtime_run_id), "runtime status payload")
        except SERVICE_BOUNDARY_ERRORS:
            self._append_runtime_refresh_error(conversation_id, runtime_run_id=runtime_run_id)
            return
        payload = dict(status_payload)
        runtime_state = _runtime_state_from_status_payload(payload)
        payload["state"] = runtime_state
        payload = normalize_runtime_progress_payload(payload)
        self.store.set_runtime(conversation_id, runtime_run_id=runtime_run_id, runtime_state=runtime_state)
        if projected_runtime_events:
            if runtime_state == "completed":
                self._append_completed_runtime_result_if_available(conversation_id, runtime_run_id=runtime_run_id)
            return
        if runtime_state == "completed" and _has_runtime_result_for_run(record.events, runtime_run_id=runtime_run_id):
            self._append_completed_runtime_result_if_available(conversation_id, runtime_run_id=runtime_run_id)
            return
        latest_progress = _latest_runtime_progress_payload(record.events)
        visible_signature = _runtime_progress_visible_signature(payload)
        visible_summary = _runtime_progress_visible_summary(payload)
        if (
            latest_progress == payload
            or _runtime_progress_visible_signature(latest_progress) == visible_signature
            or _has_runtime_progress_visible_signature(
                record.events,
                runtime_run_id=runtime_run_id,
                visible_signature=visible_signature,
            )
            or _has_runtime_progress_visible_summary(
                record.events,
                runtime_run_id=runtime_run_id,
                visible_summary=visible_summary,
            )
        ):
            if runtime_state == "completed":
                self._append_completed_runtime_result_if_available(conversation_id, runtime_run_id=runtime_run_id)
            return
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="runtime_progress",
                role="runtime",
                payload=payload,
                dedupe_key=(
                    "workbench-v2-service:runtime-refresh:"
                    f"{runtime_run_id}:{_service_payload_digest(payload)}:runtime-status"
                ),
            ),
        )
        if runtime_state == "completed":
            self._append_completed_runtime_result_if_available(conversation_id, runtime_run_id=runtime_run_id)

    def _append_runtime_progress_events(
        self,
        conversation_id: str,
        *,
        runtime_run_id: str,
        record: object,
    ) -> bool:
        list_progress_events = getattr(self.runtime_service, "list_progress_events", None)
        if not callable(list_progress_events):
            return False
        after_seq = _latest_projected_runtime_event_seq(getattr(record, "events", []), runtime_run_id=runtime_run_id)
        try:
            progress_events = list_progress_events(runtime_run_id, after_seq=after_seq, limit=200)
        except SERVICE_BOUNDARY_ERRORS:
            self._append_runtime_refresh_error(conversation_id, runtime_run_id=runtime_run_id)
            return False
        appended = False
        for progress_payload in progress_events:
            payload = normalize_runtime_progress_payload(progress_payload)
            runtime_event_seq = payload.get("runtimeEventSeq")
            if not isinstance(runtime_event_seq, int):
                continue
            payload.setdefault("runtimeRunId", runtime_run_id)
            payload.setdefault("state", "running")
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="runtime_progress",
                    role="runtime",
                    payload=payload,
                    dedupe_key=(
                        "workbench-v2-service:runtime-event:"
                        f"{runtime_run_id}:{runtime_event_seq}:runtime-progress"
                    ),
                ),
            )
            appended = True
        return appended

    def _append_completed_runtime_result_if_available(self, conversation_id: str, *, runtime_run_id: str) -> None:
        get_results = getattr(self.runtime_service, "get_results", None)
        if not callable(get_results):
            return
        try:
            results_payload = _required_mapping(get_results(runtime_run_id), "runtime results payload")
        except SERVICE_BOUNDARY_ERRORS:
            return
        payload = dict(results_payload)
        runtime_state = _runtime_state_from_status_payload(payload)
        if runtime_state != "completed":
            return
        payload["state"] = runtime_state
        payload.setdefault("runtimeRunId", runtime_run_id)
        payload = normalize_runtime_result_payload(payload)
        record = self.store.get_conversation(conversation_id)
        result_exists = _has_runtime_result_visible_summary(
            record.events,
            runtime_run_id=runtime_run_id,
            visible_summary=str(payload["summary"]),
        )
        payload_digest = _service_payload_digest(payload)
        if not result_exists:
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="runtime_result",
                    role="runtime",
                    payload=payload,
                    dedupe_key=(
                        "workbench-v2-service:runtime-refresh:"
                        f"{runtime_run_id}:{payload_digest}:runtime-results"
                    ),
                ),
            )
        self._append_assistant_message(
            conversation_id,
            text=_runtime_final_assistant_reply(payload),
            dedupe_key=(
                "workbench-v2-service:runtime-refresh:"
                f"{runtime_run_id}:{payload_digest}:assistant-final-summary"
            ),
        )

    def _append_runtime_refresh_error(self, conversation_id: str, *, runtime_run_id: str) -> None:
        self._append_service_error(
            conversation_id,
            code="workbench_v2_runtime_status_unavailable",
            message=RUNTIME_STATUS_UNAVAILABLE_MESSAGE,
            scope="runtime-refresh",
            idempotency_key=runtime_run_id,
        )

    def _raise_if_user_event_conflicts(
        self,
        conversation_id: str,
        *,
        message: str,
        scope: str,
        idempotency_key: str | None,
    ) -> None:
        user_key = _dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="user")
        if user_key is None:
            return
        record = self.store.get_conversation(conversation_id)
        for event in record.events:
            if event.type != "user_message" or event.dedupe_key != user_key:
                continue
            if event.payload.get("text") != message:
                raise ValueError("workbench_v2_idempotency_conflict")
            return

    def _has_terminal_event(self, conversation_id: str, *, scope: str, idempotency_key: str | None) -> bool:
        assistant_key = _dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant")
        error_key = _dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="error")
        if assistant_key is None or error_key is None:
            return False
        record = self.store.get_conversation(conversation_id)
        return any(
            (event.type == "assistant_message" and event.dedupe_key == assistant_key)
            or (event.type == "error" and event.dedupe_key == error_key)
            for event in record.events
        )

    def _has_agent_patch_event(
        self,
        conversation_id: str,
        *,
        scope: str,
        patch_digest: str,
        idempotency_key: str | None,
    ) -> bool:
        if idempotency_key is None:
            return False
        prefix = _action_event_dedupe_prefix(scope=scope, idempotency_key=idempotency_key)
        record = self.store.get_conversation(conversation_id)
        for event in record.events:
            if event.type != "requirement_form":
                continue
            existing_digest = _action_digest_from_dedupe_key(event.dedupe_key, prefix=prefix)
            if existing_digest is None:
                continue
            if existing_digest != patch_digest:
                raise ValueError("workbench_v2_idempotency_conflict")
            return True
        return False

    def _has_requirement_action_terminal_event(
        self,
        conversation_id: str,
        *,
        scope: str,
        action_digest: str,
        idempotency_key: str | None,
    ) -> bool:
        if idempotency_key is None:
            return False
        prefix = _action_event_dedupe_prefix(scope=scope, idempotency_key=idempotency_key)
        record = self.store.get_conversation(conversation_id)
        for event in record.events:
            if event.type not in {"requirement_form", "requirement_form_confirmed", "error"}:
                continue
            existing_digest = _action_digest_from_dedupe_key(event.dedupe_key, prefix=prefix)
            if existing_digest is None:
                continue
            if existing_digest != action_digest:
                raise ValueError("workbench_v2_idempotency_conflict")
            return True
        return False


def _dedupe_key(*, scope: str, idempotency_key: str | None, suffix: str) -> str | None:
    if idempotency_key is None:
        return None
    return f"workbench-v2-service:{scope}:{idempotency_key}:{suffix}"


def _action_event_dedupe_key(
    *,
    scope: str,
    idempotency_key: str | None,
    action_digest: str | None,
    suffix: str,
) -> str | None:
    if action_digest is None:
        return _dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix=suffix)
    return _dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix=f"{action_digest}:{suffix}")


def _action_event_dedupe_prefix(*, scope: str, idempotency_key: str) -> str:
    return f"workbench-v2-service:{scope}:{idempotency_key}:"


def _action_digest_from_dedupe_key(dedupe_key: str | None, *, prefix: str) -> str | None:
    if dedupe_key is None or not dedupe_key.startswith(prefix):
        return None
    digest, separator, _suffix = dedupe_key[len(prefix) :].partition(":")
    if not separator or len(digest) != 64:
        return None
    return digest


def _requirement_action_scope() -> str:
    return "requirement-action"


def _requirement_action_payload_digest(
    *,
    conversation_id: str,
    action: WorkbenchV2RequirementAction,
    item_id: str | None,
    selected: bool | None,
    text: str | None,
) -> str:
    payload = {
        "conversationId": conversation_id,
        "action": action,
        "itemId": item_id,
        "selected": selected,
        "text": text,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _agent_requirement_patch_payload_digest(
    *,
    conversation_id: str,
    patch: WorkbenchV2RequirementPatch,
) -> str:
    payload = {
        "conversationId": conversation_id,
        "patch": patch.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _set_draft_item_selected(draft: RequirementDraft, *, item_id: str, selected: bool) -> None:
    for section in draft.sections:
        for item in section.items:
            if item.item_id == item_id:
                item.selected = selected
                return
    raise ValueError("workbench_v2_requirement_item_not_found")


def _latest_requirement_form_payload(events: Sequence[WorkbenchV2TranscriptEvent]) -> dict[str, object] | None:
    for event in reversed(events):
        if event.type == "requirement_form":
            return dict(event.payload)
    return None


def _latest_runtime_progress_payload(events: Sequence[WorkbenchV2TranscriptEvent]) -> dict[str, object] | None:
    for event in reversed(events):
        if event.type == "runtime_progress":
            return dict(event.payload)
    return None


def _latest_projected_runtime_event_seq(
    events: Sequence[WorkbenchV2TranscriptEvent],
    *,
    runtime_run_id: str,
) -> int:
    latest_seq = 0
    for event in events:
        if event.type != "runtime_progress":
            continue
        if event.payload.get("runtimeRunId") != runtime_run_id:
            continue
        runtime_event_seq = event.payload.get("runtimeEventSeq")
        if isinstance(runtime_event_seq, int):
            latest_seq = max(latest_seq, runtime_event_seq)
    return latest_seq


def _has_runtime_progress_visible_signature(
    events: Sequence[WorkbenchV2TranscriptEvent],
    *,
    runtime_run_id: str,
    visible_signature: tuple[object, object] | None,
) -> bool:
    if visible_signature is None:
        return False
    for event in events:
        if event.type != "runtime_progress":
            continue
        if event.payload.get("runtimeRunId") != runtime_run_id:
            continue
        if _runtime_progress_visible_signature(event.payload) == visible_signature:
            return True
    return False


def _has_runtime_progress_visible_summary(
    events: Sequence[WorkbenchV2TranscriptEvent],
    *,
    runtime_run_id: str,
    visible_summary: str | None,
) -> bool:
    if visible_summary is None:
        return False
    for event in events:
        if event.type != "runtime_progress":
            continue
        if event.payload.get("runtimeRunId") != runtime_run_id:
            continue
        if _runtime_progress_visible_summary(event.payload) == visible_summary:
            return True
    return False


def _runtime_progress_visible_signature(payload: Mapping[str, object] | None) -> tuple[object, object] | None:
    if payload is None:
        return None
    return payload.get("state"), payload.get("summary")


def _runtime_progress_visible_summary(payload: Mapping[str, object] | None) -> str | None:
    return display_runtime_progress_visible_summary(payload)


def _has_runtime_result_visible_summary(
    events: Sequence[WorkbenchV2TranscriptEvent],
    *,
    runtime_run_id: str,
    visible_summary: str | None,
) -> bool:
    if visible_summary is None:
        return False
    for event in events:
        if event.type != "runtime_result":
            continue
        if event.payload.get("runtimeRunId") != runtime_run_id:
            continue
        payload = dict(event.payload)
        payload.setdefault("runtimeRunId", runtime_run_id)
        if normalize_runtime_result_payload(payload).get("summary") == visible_summary:
            return True
    return False


def _has_runtime_result_for_run(
    events: Sequence[WorkbenchV2TranscriptEvent],
    *,
    runtime_run_id: str,
) -> bool:
    for event in events:
        if event.type == "runtime_result" and event.payload.get("runtimeRunId") == runtime_run_id:
            return True
    return False


def _service_payload_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _requirement_draft_from_payload(payload: object) -> RequirementDraft | None:
    if not isinstance(payload, Mapping):
        return None
    try:
        return RequirementDraft.model_validate(payload)
    except ValueError:
        return None


def _runtime_input_from_payload(payload: object) -> WorkbenchV2RuntimeInput | None:
    if not isinstance(payload, Mapping):
        return None
    try:
        return WorkbenchV2RuntimeInput.model_validate(payload)
    except ValueError:
        return None


def _requirement_sheet_from_payload(payload: object) -> RequirementSheet | None:
    if not isinstance(payload, Mapping):
        return None
    try:
        return RequirementSheet.model_validate(payload)
    except ValueError:
        return None


def _runtime_input_payload_matches(runtime_input: WorkbenchV2RuntimeInput, payload: object) -> bool:
    payload_mapping = _mapping_or_none(payload)
    return payload_mapping == _dump_mapping(runtime_input)


def _mapping_or_none(payload: object) -> Mapping[str, object] | None:
    if isinstance(payload, Mapping):
        return {str(key): value for key, value in payload.items()}
    return None


def _draft_revision_id(draft_payload: Mapping[str, object] | None) -> str | None:
    if draft_payload is None:
        return None
    value = draft_payload.get("draft_revision_id")
    if isinstance(value, str) and value:
        return value
    return None


def _selected_item_ids(draft_payload: Mapping[str, object] | None) -> list[str] | None:
    if draft_payload is None:
        return None
    selected = [
        item_id
        for item in _draft_items(draft_payload)
        if (item_id := _draft_item_id(item)) is not None
        and item.get("selected") is True
        and item.get("status") == "resolved"
    ]
    return selected


def _deselected_item_ids(draft_payload: Mapping[str, object] | None) -> list[str] | None:
    if draft_payload is None:
        return None
    deselected = [
        item_id
        for item in _draft_items(draft_payload)
        if (item_id := _draft_item_id(item)) is not None
        and (item.get("selected") is not True or item.get("status") in {"deleted", "moved", "rejected"})
    ]
    return deselected


def _draft_items(draft_payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    sections = draft_payload.get("sections")
    if not isinstance(sections, list):
        return []
    items: list[Mapping[str, object]] = []
    for section in sections:
        section_payload = _mapping_or_none(section)
        if section_payload is None:
            continue
        section_items = section_payload.get("items")
        if not isinstance(section_items, list):
            continue
        items.extend(item for item in (_mapping_or_none(item) for item in section_items) if item is not None)
    return items


def _draft_item_id(item: Mapping[str, object]) -> str | None:
    value = item.get("item_id")
    if isinstance(value, str) and value:
        return value
    return None


def _with_new_draft_revision(draft: RequirementDraft) -> RequirementDraft:
    old_revision_id = draft.draft_revision_id
    draft.base_revision_id = old_revision_id
    draft.draft_revision_id = f"reqdraft_{uuid4().hex}"
    return draft


def _post_confirm_requirement_text(patch: WorkbenchV2RequirementPatch) -> str:
    if patch.otherNotes:
        return patch.otherNotes
    if patch.selectedItemIds or patch.deselectedItemIds:
        return "已记录对下一轮需求条件的选择调整。"
    return "已记录下一轮补充要求。"


def _post_confirm_runtime_input_text(runtime_input: WorkbenchV2RuntimeInput) -> str:
    parts = [
        f"职位名称：{runtime_input.jobTitle}",
        f"补充 JD：{runtime_input.jd}",
    ]
    if runtime_input.notes:
        parts.append(f"补充说明：{runtime_input.notes}")
    return "；".join(parts)


def _runtime_input_with_full_jd_fallback(
    runtime_input: WorkbenchV2RuntimeInput,
    user_text: str,
) -> WorkbenchV2RuntimeInput:
    source_jd = user_text.strip()
    if not _runtime_jd_should_use_source_text(runtime_input.jd, source_jd):
        return runtime_input
    return runtime_input.model_copy(update={"jd": source_jd})


def _runtime_jd_should_use_source_text(jd: str, source_jd: str) -> bool:
    candidate = jd.strip()
    if not candidate or len(source_jd) <= len(candidate):
        return False
    placeholder_phrases = ("如上", "同上", "见上文", "同用户输入", "省略")
    has_placeholder = any(phrase in candidate for phrase in placeholder_phrases)
    has_ellipsis = "..." in candidate or "…" in candidate
    if has_placeholder or has_ellipsis:
        return True
    return _source_text_looks_like_jd(source_jd) and len(candidate) < int(len(source_jd) * 0.9)


def _source_text_looks_like_jd(text: str) -> bool:
    if len(text) < 80:
        return False
    jd_markers = (
        "JD",
        "职位",
        "岗位",
        "职责",
        "要求",
        "任职",
        "工作城市",
        "薪资",
        "面试",
        "招聘",
    )
    return "\n" in text or any(marker in text for marker in jd_markers)


def _runtime_result_question_reply(result_payload: Mapping[str, object] | None) -> str:
    if result_payload is None:
        return RUNTIME_RESULTS_UNAVAILABLE_MESSAGE
    state = result_payload.get("state")
    if state == "idle":
        return "当前还没有运行结果。"
    if state != "completed":
        return "当前招聘流程尚未完成，还没有最终结果可供总结。请稍后再查询最新进度。"
    return _runtime_final_assistant_reply(result_payload)


def _runtime_final_assistant_reply(result_payload: Mapping[str, object]) -> str:
    fact_values = _runtime_result_fact_values(result_payload.get("facts"))
    if fact_values:
        recommended = "；".join(fact_values)
        return f"招聘流程已完成，最终候选人列表已生成。本次最终推荐：{recommended}。你可以在右侧查看候选人详情。"
    return runtime_result_summary(result_payload.get("summary"), "completed")


def _runtime_result_fact_values(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    fact_values: list[str] = []
    for fact in value:
        if not isinstance(fact, Mapping):
            continue
        fact_value = cast(Mapping[str, object], fact).get("value")
        if not isinstance(fact_value, str):
            continue
        stripped = fact_value.strip().rstrip("。")
        if stripped:
            fact_values.append(stripped)
    return fact_values


def _runtime_result_summary_for_state(state: WorkbenchV2RuntimeState) -> str:
    if state == "completed":
        return COMPLETED_RESULT_SUMMARY
    if state == "idle":
        return IDLE_RESULT_SUMMARY
    return PENDING_RESULT_SUMMARY


def _runtime_state_from_run_status(status: object) -> WorkbenchV2RuntimeState:
    if status == "queued":
        return "queued"
    if status in {"completed"}:
        return "completed"
    if status in {"failed"}:
        return "failed"
    if status in {"cancelled"}:
        return "cancelled"
    return "running"


def _runtime_state_from_status_payload(payload: Mapping[str, object]) -> WorkbenchV2RuntimeState:
    state = payload.get("state")
    if state == "idle":
        return "idle"
    if state == "queued":
        return "queued"
    if state == "running":
        return "running"
    if state == "completed":
        return "completed"
    if state == "failed":
        return "failed"
    if state == "cancelled":
        return "cancelled"
    return _runtime_state_from_run_status(payload.get("status"))


def _required_mapping(value: object, label: str) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise TypeError(f"{label} must be a mapping")


def _required_text_attr(value: object, attribute: str) -> str:
    text = getattr(value, attribute, None)
    if not isinstance(text, str) or not text:
        raise TypeError(f"{attribute} is required")
    return text


def _dump_mapping(value: object) -> dict[str, object]:
    dumped = _dump(value)
    if not isinstance(dumped, Mapping):
        raise TypeError("expected mapping payload")
    return {str(key): item for key, item in dumped.items()}


def _dump(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_dump(item) for item in value]
    return value
