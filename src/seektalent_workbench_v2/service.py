from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from seektalent_workbench_v2.agent_loop import (
    WorkbenchV2AgentLoop,
    WorkbenchV2AgentOutput,
    WorkbenchV2RuntimeInput,
)
from seektalent_workbench_v2.models import (
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationView,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventInput,
)
from seektalent_workbench_v2.store import WorkbenchV2Store
from seektalent_workbench_v2.views import conversation_list_to_view, conversation_record_to_view


class WorkbenchV2RequirementRuntime(Protocol):
    def extract_requirements(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> object: ...


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
        if self._has_user_event(conversation.id, scope="create", idempotency_key=idempotency_key):
            return self.get_conversation(conversation.id)
        return await self._append_user_and_run_agent(
            conversation_id=conversation.id,
            message=message,
            scope="create",
            idempotency_key=idempotency_key,
        )

    async def submit_message(
        self,
        conversation_id: str,
        message: str,
        idempotency_key: str | None,
    ) -> WorkbenchV2ConversationView:
        if self._has_user_event(conversation_id, scope="submit", idempotency_key=idempotency_key):
            return self.get_conversation(conversation_id)
        return await self._append_user_and_run_agent(
            conversation_id=conversation_id,
            message=message,
            scope="submit",
            idempotency_key=idempotency_key,
        )

    def get_conversation(self, conversation_id: str) -> WorkbenchV2ConversationView:
        return conversation_record_to_view(self.store.get_conversation(conversation_id))

    def list_conversations(self) -> WorkbenchV2ConversationListView:
        return conversation_list_to_view(self.store.list_conversations())

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
        output = await self.agent_loop.run_turn(
            conversation_id=conversation_id,
            context_summary=record.conversation.context_summary,
            recent_events=record.events,
            user_text=message,
        )
        self._apply_agent_output(
            conversation_id=conversation_id,
            output=output,
            scope=scope,
            idempotency_key=idempotency_key,
        )
        return self.get_conversation(conversation_id)

    def _apply_agent_output(
        self,
        *,
        conversation_id: str,
        output: WorkbenchV2AgentOutput,
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
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="assistant_status",
                    role="assistant",
                    payload={"phase": "extract_requirements", "text": "正在整理需求表单。"},
                    dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="status"),
                ),
            )
            draft = self.runtime_service.extract_requirements(conversation_id, output.runtimeInput)
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="requirement_form",
                    role="assistant",
                    payload={
                        "runtimeInput": _dump_mapping(output.runtimeInput),
                        "draft": _dump_mapping(draft),
                    },
                    dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="requirement-form"),
                ),
            )
            self._append_assistant_message(
                conversation_id,
                text=output.message,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
            )
            return

        if output.intent == "get_runtime_status":
            self._append_runtime_status(conversation_id, scope=scope, idempotency_key=idempotency_key)

        self._append_assistant_message(
            conversation_id,
            text=output.message,
            payload_extra={"intent": output.intent} if output.intent != "confirm_requirements" else None,
            dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
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

    def _append_runtime_status(self, conversation_id: str, *, scope: str, idempotency_key: str | None) -> None:
        record = self.store.get_conversation(conversation_id)
        runtime_run_id = record.conversation.runtime_run_id
        if runtime_run_id is None:
            return
        get_status = getattr(self.runtime_service, "get_status", None)
        if not callable(get_status):
            return
        status_payload = cast("Mapping[str, object]", get_status(runtime_run_id))
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="runtime_progress",
                role="runtime",
                payload=dict(status_payload),
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="runtime-status"),
            ),
        )

    def _has_user_event(self, conversation_id: str, *, scope: str, idempotency_key: str | None) -> bool:
        dedupe_key = _dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="user")
        if dedupe_key is None:
            return False
        record = self.store.get_conversation(conversation_id)
        return any(event.type == "user_message" and event.dedupe_key == dedupe_key for event in record.events)


def _dedupe_key(*, scope: str, idempotency_key: str | None, suffix: str) -> str | None:
    if idempotency_key is None:
        return None
    return f"workbench-v2-service:{scope}:{idempotency_key}:{suffix}"


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
