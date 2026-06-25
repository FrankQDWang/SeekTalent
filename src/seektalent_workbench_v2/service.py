from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, cast

from seektalent.models import RequirementSheet
from seektalent_workbench_v2.agent_loop import (
    WorkbenchV2AgentLoop,
    WorkbenchV2AgentOutput,
    WorkbenchV2RuntimeInput,
)
from seektalent_workbench_v2.models import (
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationView,
    WorkbenchV2RuntimeState,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventInput,
)
from seektalent_workbench_v2.store import WorkbenchV2Store
from seektalent_workbench_v2.views import conversation_list_to_view, conversation_record_to_view


class WorkbenchV2RequirementExtraction(Protocol):
    draft: object
    requirement_sheet: RequirementSheet


class WorkbenchV2RequirementRuntime(Protocol):
    def extract_requirements(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> object: ...

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
        if self._has_terminal_event(conversation.id, scope="create", idempotency_key=idempotency_key):
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
        if self._has_terminal_event(conversation_id, scope="submit", idempotency_key=idempotency_key):
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
            draft, requirement_sheet = self._extract_requirement_form(conversation_id, output.runtimeInput)
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="requirement_form",
                    role="assistant",
                    payload={
                        "runtimeInput": _dump_mapping(output.runtimeInput),
                        "draft": _dump_mapping(draft),
                        "requirementSheet": _dump_mapping(requirement_sheet),
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

        if output.intent == "confirm_requirements":
            self._confirm_requirements(
                conversation_id=conversation_id,
                message=output.message,
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return

        if output.intent == "start_runtime":
            self._start_runtime(
                conversation_id=conversation_id,
                runtime_input=output.runtimeInput,
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

        self._append_assistant_message(
            conversation_id,
            text=output.message,
            payload_extra={"intent": output.intent},
            dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="assistant"),
        )

    def _extract_requirement_form(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> tuple[object, RequirementSheet]:
        extract_bundle = getattr(self.runtime_service, "extract_requirement_bundle", None)
        if callable(extract_bundle):
            bundle = cast(
                "Callable[[str, WorkbenchV2RuntimeInput], WorkbenchV2RequirementExtraction]",
                extract_bundle,
            )(conversation_id, runtime_input)
            return bundle.draft, bundle.requirement_sheet
        raise RuntimeError("workbench_v2_requirement_bundle_unavailable")

    def _confirm_requirements(
        self,
        *,
        conversation_id: str,
        message: str,
        scope: str,
        idempotency_key: str | None,
    ) -> None:
        form_payload = _latest_requirement_form_payload(self.store.get_conversation(conversation_id).events)
        if form_payload is None:
            self._append_service_error(
                conversation_id,
                code="workbench_v2_requirement_form_required",
                message="当前没有可确认的需求表单，无法启动运行。",
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return
        runtime_input = _runtime_input_from_payload(form_payload.get("runtimeInput"))
        if runtime_input is None:
            self._append_service_error(
                conversation_id,
                code="workbench_v2_runtime_input_required",
                message="需求表单缺少 runtimeInput，无法启动运行。",
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return
        draft_payload = _mapping_or_none(form_payload.get("draft"))
        requirement_sheet = _requirement_sheet_from_payload(form_payload.get("requirementSheet"))
        if requirement_sheet is None:
            self._append_requirement_sheet_required_error(
                conversation_id,
                scope=scope,
                idempotency_key=idempotency_key,
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
        )

    def _start_runtime(
        self,
        *,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput | None,
        message: str,
        scope: str,
        idempotency_key: str | None,
    ) -> None:
        if runtime_input is None:
            self._append_service_error(
                conversation_id,
                code="workbench_v2_runtime_input_required",
                message="缺少 runtimeInput，无法启动运行。",
                scope=scope,
                idempotency_key=idempotency_key,
            )
            return
        form_payload = _latest_requirement_form_payload(self.store.get_conversation(conversation_id).events)
        draft_payload = _mapping_or_none(form_payload.get("draft")) if form_payload is not None else None
        confirmed_payload: dict[str, object]
        if form_payload is not None:
            requirement_sheet = _requirement_sheet_from_payload(form_payload.get("requirementSheet"))
            if requirement_sheet is None:
                self._append_requirement_sheet_required_error(
                    conversation_id,
                    scope=scope,
                    idempotency_key=idempotency_key,
                )
                return
            confirmed_payload = dict(form_payload)
            confirmed_payload["runtimeInput"] = _dump_mapping(runtime_input)
            self._start_runtime_from_requirement_sheet(
                conversation_id=conversation_id,
                runtime_input=runtime_input,
                requirement_sheet=requirement_sheet,
                confirmed_payload=confirmed_payload,
                message=message,
                scope=scope,
                idempotency_key=idempotency_key,
                draft_revision_id=_draft_revision_id(draft_payload),
                selected_item_ids=_selected_item_ids(draft_payload),
                deselected_item_ids=_deselected_item_ids(draft_payload),
            )
            return
        else:
            confirmed_payload = {"runtimeInput": _dump_mapping(runtime_input)}
        self._start_runtime_from_input(
            conversation_id=conversation_id,
            runtime_input=runtime_input,
            confirmed_payload=confirmed_payload,
            message=message,
            scope=scope,
            idempotency_key=idempotency_key,
            draft_revision_id=_draft_revision_id(draft_payload),
            selected_item_ids=_selected_item_ids(draft_payload),
            deselected_item_ids=_deselected_item_ids(draft_payload),
        )

    def _append_requirement_sheet_required_error(
        self,
        conversation_id: str,
        *,
        scope: str,
        idempotency_key: str | None,
    ) -> None:
        self._append_service_error(
            conversation_id,
            code="workbench_v2_requirement_sheet_required",
            message="需求表单缺少 requirementSheet，无法启动运行。",
            scope=scope,
            idempotency_key=idempotency_key,
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
    ) -> None:
        run = self.runtime_service.start_run(
            conversation_id,
            runtime_input,
            requirement_sheet,
            idempotency_key=idempotency_key,
            draft_revision_id=draft_revision_id,
            selected_item_ids=selected_item_ids,
            deselected_item_ids=deselected_item_ids,
        )
        self._append_started_runtime(
            conversation_id=conversation_id,
            run=run,
            confirmed_payload=confirmed_payload,
            message=message,
            scope=scope,
            idempotency_key=idempotency_key,
        )

    def _start_runtime_from_input(
        self,
        *,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
        confirmed_payload: dict[str, object],
        message: str,
        scope: str,
        idempotency_key: str | None,
        draft_revision_id: str | None,
        selected_item_ids: list[str] | None,
        deselected_item_ids: list[str] | None,
    ) -> None:
        run = self.runtime_service.start_run_from_runtime_input(
            conversation_id,
            runtime_input,
            idempotency_key=idempotency_key,
            draft_revision_id=draft_revision_id,
            selected_item_ids=selected_item_ids,
            deselected_item_ids=deselected_item_ids,
        )
        self._append_started_runtime(
            conversation_id=conversation_id,
            run=run,
            confirmed_payload=confirmed_payload,
            message=message,
            scope=scope,
            idempotency_key=idempotency_key,
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
                dedupe_key=_dedupe_key(
                    scope=scope,
                    idempotency_key=idempotency_key,
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

    def _append_service_error(
        self,
        conversation_id: str,
        *,
        code: str,
        message: str,
        scope: str,
        idempotency_key: str | None,
    ) -> None:
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="error",
                role="system",
                status="failed",
                payload={"code": code, "message": message},
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="error"),
            ),
        )
        self._append_assistant_message(
            conversation_id,
            text=message,
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
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="runtime_progress",
                    role="runtime",
                    payload={"state": "idle", "summary": "当前还没有开始运行。"},
                    dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="runtime-status"),
                ),
            )
            return
        get_status = getattr(self.runtime_service, "get_status", None)
        if not callable(get_status):
            return
        status_payload = cast("Mapping[str, object]", get_status(runtime_run_id))
        payload = dict(status_payload)
        runtime_state = _runtime_state_from_status_payload(payload)
        payload["state"] = runtime_state
        self.store.set_runtime(conversation_id, runtime_run_id=runtime_run_id, runtime_state=runtime_state)
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="runtime_progress",
                role="runtime",
                payload=payload,
                dedupe_key=_dedupe_key(scope=scope, idempotency_key=idempotency_key, suffix="runtime-status"),
            ),
        )

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


def _dedupe_key(*, scope: str, idempotency_key: str | None, suffix: str) -> str | None:
    if idempotency_key is None:
        return None
    return f"workbench-v2-service:{scope}:{idempotency_key}:{suffix}"


def _latest_requirement_form_payload(events: Sequence[WorkbenchV2TranscriptEvent]) -> dict[str, object] | None:
    for event in reversed(events):
        if event.type == "requirement_form":
            return dict(event.payload)
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
    if state in {"idle", "queued", "running", "completed", "failed", "cancelled"}:
        return cast("WorkbenchV2RuntimeState", state)
    return _runtime_state_from_run_status(payload.get("status"))


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
