from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, get_args

import pytest
from seektalent.models import HardConstraintSlots, QueryTermCandidate, RequirementSheet
import seektalent_workbench_v2.service as service_module
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.requirements import RequirementDraft, RequirementDraftItem, RequirementDraftSection
from seektalent_workbench_v2.agent_loop import WorkbenchV2AgentOutput, WorkbenchV2RuntimeInput
from seektalent_workbench_v2.models import (
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationView,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventInput,
)
from seektalent_workbench_v2.service import WorkbenchV2Service
from seektalent_workbench_v2.store import WorkbenchV2Store


@dataclass(frozen=True)
class FakeRequirementExtraction:
    draft: RequirementDraft
    requirement_sheet: RequirementSheet


class FakeAgentLoop:
    def __init__(self, *outputs: WorkbenchV2AgentOutput) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    async def run_turn(
        self,
        *,
        conversation_id: str,
        context_summary: str | None,
        recent_events: Sequence[WorkbenchV2TranscriptEvent],
        user_text: str,
    ) -> WorkbenchV2AgentOutput:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "context_summary": context_summary,
                "recent_events": list(recent_events),
                "user_text": user_text,
            }
        )
        return self.outputs.pop(0)


class FakeRuntimeService:
    def __init__(
        self,
        draft: object | None = None,
        *,
        requirement_sheet: RequirementSheet | None = None,
        extract_errors: Sequence[Exception] = (),
        start_errors: Sequence[Exception] = (),
    ) -> None:
        self.draft = draft or _draft_payload()
        self.requirement_sheet = requirement_sheet or _requirement_sheet_payload()
        self.extract_errors = list(extract_errors)
        self.start_errors = list(start_errors)
        self.extract_calls: list[dict[str, object]] = []
        self.start_calls: list[dict[str, object]] = []
        self.status_payloads: dict[str, dict[str, object]] = {}

    def extract_requirement_bundle(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> FakeRequirementExtraction:
        self.extract_calls.append({"conversation_id": conversation_id, "runtime_input": runtime_input})
        if self.extract_errors:
            raise self.extract_errors.pop(0)
        if not isinstance(self.draft, RequirementDraft):
            raise TypeError("fake draft must be a RequirementDraft")
        return FakeRequirementExtraction(draft=self.draft, requirement_sheet=self.requirement_sheet)

    def extract_requirements(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> object:
        return self.extract_requirement_bundle(conversation_id, runtime_input).draft

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
    ) -> RuntimeRunRecord:
        self.start_calls.append(
            {
                "conversation_id": conversation_id,
                "runtime_input": runtime_input,
                "requirement_sheet": requirement_sheet,
                "idempotency_key": idempotency_key,
                "draft_revision_id": draft_revision_id,
                "selected_item_ids": selected_item_ids,
                "deselected_item_ids": deselected_item_ids,
            }
        )
        return self._runtime_run(conversation_id, idempotency_key=idempotency_key)

    def start_run_from_runtime_input(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
        *,
        idempotency_key: str | None = None,
        draft_revision_id: str | None = None,
        selected_item_ids: list[str] | None = None,
        deselected_item_ids: list[str] | None = None,
    ) -> RuntimeRunRecord:
        self.start_calls.append(
            {
                "conversation_id": conversation_id,
                "runtime_input": runtime_input,
                "idempotency_key": idempotency_key,
                "draft_revision_id": draft_revision_id,
                "selected_item_ids": selected_item_ids,
                "deselected_item_ids": deselected_item_ids,
            }
        )
        self.extract_requirement_bundle(conversation_id, runtime_input)
        return self._runtime_run(conversation_id, idempotency_key=idempotency_key)

    def get_status(self, runtime_run_id: str) -> dict[str, object]:
        return self.status_payloads[runtime_run_id]

    def _runtime_run(self, conversation_id: str, *, idempotency_key: str | None) -> RuntimeRunRecord:
        if self.start_errors:
            raise self.start_errors.pop(0)
        run_index = len(self.start_calls)
        return RuntimeRunRecord(
            runtime_run_id=f"rtrun_{run_index}",
            run_intent_id=None,
            start_idempotency_key=idempotency_key,
            run_kind="primary",
            agent_conversation_id=conversation_id,
            workbench_session_id=None,
            approved_requirement_revision_id=f"reqapproved_{run_index}",
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin"],
            stop_reason_code=None,
            created_at="2026-06-25T01:02:03.000004+00:00",
            updated_at="2026-06-25T01:02:03.000004+00:00",
            completed_at=None,
        )


def test_service_does_not_import_legacy_workbench_modules() -> None:
    source = inspect.getsource(service_module)

    assert "seektalent_ui" not in source
    assert "seektalent_conversation_agent" not in source
    assert "first_turn" not in source
    assert "outbox" not in source
    assert "projection" not in source


def test_public_view_schema_versions_are_literal_contracts() -> None:
    assert get_args(WorkbenchV2ConversationView.model_fields["schemaVersion"].annotation) == ("agent.workbench.v2",)
    assert get_args(WorkbenchV2ConversationListView.model_fields["schemaVersion"].annotation) == (
        "agent.workbench.v2.list",
    )


def test_create_pure_chat_conversation_does_not_extract_requirements(tmp_path: Path) -> None:
    store = _store(tmp_path)
    agent = FakeAgentLoop(_agent_output(intent="chat", message="你好，我可以帮你处理招聘需求。"))
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("先随便聊聊", idempotency_key="create-chat"))
    payload = view.model_dump(mode="json")

    assert [event.type for event in view.transcriptEvents] == ["user_message", "assistant_message"]
    assert view.transcriptEvents[0].payload == {"text": "先随便聊聊"}
    assert view.transcriptEvents[1].payload == {"text": "你好，我可以帮你处理招聘需求。"}
    assert runtime.extract_calls == []
    assert view.requirementForm is None
    assert payload["schemaVersion"] == "agent.workbench.v2"
    assert set(payload) == {"schemaVersion", "conversation", "transcriptEvents", "requirementForm", "runtime"}
    assert set(payload["conversation"]) == {
        "conversationId",
        "title",
        "runtimeState",
        "runtimeRunId",
        "createdAt",
        "updatedAt",
    }
    assert payload["runtime"] is None
    assert set(payload["transcriptEvents"][0]) == {"eventId", "step", "type", "role", "status", "payload", "createdAt"}
    assert "conversation_id" not in payload["transcriptEvents"][0]
    assert "dedupe_key" not in payload["transcriptEvents"][0]
    assert "parent_event_id" not in payload["transcriptEvents"][0]
    assert "created_at" not in payload["transcriptEvents"][0]
    assert "transcriptGroups" not in payload


def test_create_jd_conversation_appends_requirement_form(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州，5 年以上经验。",
    }
    agent = FakeAgentLoop(
        _agent_output(
            intent="extract_requirements",
            message="我已整理需求，请确认表单。",
            runtimeInput=runtime_input,
        )
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-jd"))

    assert [event.type for event in view.transcriptEvents] == [
        "user_message",
        "assistant_status",
        "requirement_form",
        "assistant_message",
    ]
    form_event = view.transcriptEvents[2]
    assert form_event.payload["runtimeInput"] == runtime_input
    assert form_event.payload["draft"]["draft_revision_id"] == "reqdraft_1"
    assert form_event.payload["requirementSheet"] == _requirement_sheet_payload().model_dump(mode="json")
    assert view.requirementForm == form_event.payload
    assert runtime.extract_calls == [
        {
            "conversation_id": view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(runtime_input),
        }
    ]


def test_vague_recruitment_input_asks_clarification_and_does_not_start(tmp_path: Path) -> None:
    store = _store(tmp_path)
    agent = FakeAgentLoop(
        _agent_output(
            intent="extract_requirements",
            message="我需要先确认岗位信息。",
            needsClarification=True,
            clarifyingQuestion="你要招聘的岗位名称是什么？",
        )
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("帮我招个人", idempotency_key="create-vague"))

    assert [event.type for event in view.transcriptEvents] == ["user_message", "assistant_message"]
    assert view.transcriptEvents[-1].payload == {
        "text": "你要招聘的岗位名称是什么？",
        "needsClarification": True,
    }
    assert runtime.extract_calls == []
    assert view.requirementForm is None


def test_submit_message_passes_recent_events_and_context_summary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    agent = FakeAgentLoop(
        _agent_output(intent="chat", message="已收到第一句。"),
        _agent_output(intent="chat", message="已收到补充。"),
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())
    first_view = asyncio.run(service.create_conversation("你好", idempotency_key="create-submit"))
    store.append_context_summary(first_view.conversation.conversationId, summary="用户正在招聘 AI 平台工程师，偏杭州。")

    asyncio.run(
        service.submit_message(first_view.conversation.conversationId, "补充：需要 RAG 经验", idempotency_key="submit-1")
    )

    second_call = agent.calls[1]
    recent_events = second_call["recent_events"]
    assert second_call["context_summary"] == "用户正在招聘 AI 平台工程师，偏杭州。"
    assert [event.type for event in recent_events] == [
        "user_message",
        "assistant_message",
        "context_summary",
        "user_message",
    ]
    assert recent_events[-1].payload == {"text": "补充：需要 RAG 经验"}


def test_conversation_view_filters_context_summary_and_keeps_flat_events(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(first_user_text="长对话", idempotency_key="manual")
    store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(type="user_message", role="user", payload={"text": "长对话"}),
    )
    store.append_context_summary(conversation.id, summary="内部摘要，不应该作为转录事件返回。")
    store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(type="assistant_message", role="assistant", payload={"text": "继续。"}),
    )
    service = WorkbenchV2Service(
        store=store,
        agent_loop=FakeAgentLoop(),
        runtime_service=FakeRuntimeService(),
    )

    view = service.get_conversation(conversation.id)

    assert [event.type for event in view.transcriptEvents] == ["user_message", "assistant_message"]
    assert [event.step for event in view.transcriptEvents] == [1, 3]
    assert "transcriptGroups" not in view.model_dump(mode="json")


def test_list_conversations_returns_v2_schema(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_conversation(first_user_text="第一个需求", idempotency_key="first")
    second = store.create_conversation(first_user_text="第二个需求", idempotency_key="second")
    store.append_event(
        second.id,
        WorkbenchV2TranscriptEventInput(type="assistant_message", role="assistant", payload={"text": "已收到。"}),
    )
    service = WorkbenchV2Service(
        store=store,
        agent_loop=FakeAgentLoop(),
        runtime_service=FakeRuntimeService(),
    )

    view = service.list_conversations()
    payload = view.model_dump(mode="json")

    assert payload["schemaVersion"] == "agent.workbench.v2.list"
    assert [conversation["conversationId"] for conversation in payload["conversations"]] == [second.id, first.id]
    assert set(payload["conversations"][0]) == {"conversationId", "title", "status", "updatedAt"}
    assert payload["conversations"][0]["status"] == "idle"
    assert "transcriptGroups" not in payload


def test_create_replay_with_only_deduped_user_event_continues_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(first_user_text="招一个 AI 平台工程师", idempotency_key="create-replay")
    store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="user_message",
            role="user",
            payload={"text": "招一个 AI 平台工程师"},
            dedupe_key="workbench-v2-service:create:create-replay:user",
        ),
    )
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(
            intent="extract_requirements",
            message="我已整理需求，请确认表单。",
            runtimeInput=runtime_input,
        )
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-replay"))
    payload = view.model_dump(mode="json")

    assert len(agent.calls) == 1
    assert [event["type"] for event in payload["transcriptEvents"]] == [
        "user_message",
        "assistant_status",
        "requirement_form",
        "assistant_message",
    ]
    assert payload["requirementForm"]["runtimeInput"] == runtime_input


def test_submit_replay_with_only_deduped_user_event_continues_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(first_user_text="你好", idempotency_key="create-submit-replay")
    store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="user_message",
            role="user",
            payload={"text": "补充：需要 RAG 经验"},
            dedupe_key="workbench-v2-service:submit:submit-replay:user",
        ),
    )
    agent = FakeAgentLoop(_agent_output(intent="chat", message="已补充到当前需求上下文。"))
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())

    view = asyncio.run(service.submit_message(conversation.id, "补充：需要 RAG 经验", idempotency_key="submit-replay"))

    assert len(agent.calls) == 1
    assert [event.type for event in view.transcriptEvents] == ["user_message", "assistant_message"]
    assert view.transcriptEvents[-1].payload == {"text": "已补充到当前需求上下文。"}


def test_extract_failure_replay_recovers_incomplete_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    runtime = FakeRuntimeService(extract_errors=[RuntimeError("extract failed")])
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    try:
        asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-recover"))
    except RuntimeError as exc:
        assert str(exc) == "extract failed"
    else:
        raise AssertionError("extract failure should propagate")
    failed_conversation = store.create_conversation(first_user_text="招一个 AI 平台工程师", idempotency_key="create-recover")
    failed_record = store.get_conversation(failed_conversation.id)
    assert [event.type for event in failed_record.events] == ["user_message", "assistant_status"]

    view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-recover"))

    assert len(agent.calls) == 2
    assert [event.type for event in view.transcriptEvents] == [
        "user_message",
        "assistant_status",
        "requirement_form",
        "assistant_message",
    ]


def test_confirm_requirements_starts_runtime_from_current_form(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
        _agent_output(intent="confirm_requirements", message="已确认，开始运行。"),
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-confirm"))

    view = asyncio.run(
        service.submit_message(first_view.conversation.conversationId, "确认需求，开始运行", idempotency_key="confirm-1")
    )
    payload = view.model_dump(mode="json")

    assert runtime.start_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(runtime_input),
            "requirement_sheet": RequirementSheet.model_validate(first_view.requirementForm["requirementSheet"]),
            "idempotency_key": "confirm-1",
            "draft_revision_id": "reqdraft_1",
            "selected_item_ids": ["must_have_capabilities_1"],
            "deselected_item_ids": [],
        }
    ]
    assert runtime.extract_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(runtime_input),
        }
    ]
    assert payload["conversation"]["runtimeRunId"] == "rtrun_1"
    assert payload["conversation"]["runtimeState"] == "queued"
    assert payload["runtime"] == {"state": "queued", "runtimeRunId": "rtrun_1"}
    assert payload["requirementForm"]["readonly"] is True
    assert payload["requirementForm"]["runtimeRunId"] == "rtrun_1"
    confirmed = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form_confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0]["payload"]["runtimeInput"] == runtime_input
    assert confirmed[0]["payload"]["draft"]["draft_revision_id"] == "reqdraft_1"
    assert confirmed[0]["payload"]["readonly"] is True
    assert confirmed[0]["payload"]["runtimeRunId"] == "rtrun_1"
    progress = [event for event in payload["transcriptEvents"] if event["type"] == "runtime_progress"]
    assert progress[-1]["payload"] == {
        "state": "queued",
        "runtimeRunId": "rtrun_1",
        "summary": "招聘流程已排队，等待开始。",
    }


def test_requirement_action_set_selected_appends_form_and_updates_sheet(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    runtime = FakeRuntimeService(
        requirement_sheet=_requirement_sheet_payload().model_copy(
            update={"must_have_capabilities": ["Python 后端开发"]}
        )
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-set"))
    item_id = first_view.requirementForm["draft"]["sections"][0]["items"][0]["item_id"]

    view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="set_selected",
            item_id=item_id,
            selected=False,
            idempotency_key="select-1",
        )
    )
    payload = view.model_dump(mode="json")

    form_events = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form"]
    assert len(form_events) == 2
    latest_form = form_events[-1]["payload"]
    latest_item = latest_form["draft"]["sections"][0]["items"][0]
    assert latest_item["item_id"] == item_id
    assert latest_item["selected"] is False
    assert latest_form["draft"]["base_revision_id"] == "reqdraft_1"
    assert latest_form["draft"]["draft_revision_id"] != "reqdraft_1"
    assert latest_form["runtimeInput"] == runtime_input
    assert latest_form["requirementSheet"]["must_have_capabilities"] == []
    assert view.requirementForm == latest_form


def test_requirement_action_add_other_appends_form_and_updates_sheet(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-add"))

    view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="add_other",
            text="熟悉 LangGraph",
            idempotency_key="other-1",
        )
    )
    payload = view.model_dump(mode="json")

    form_events = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form"]
    assert len(form_events) == 2
    latest_form = form_events[-1]["payload"]
    must_have_items = latest_form["draft"]["sections"][0]["items"]
    new_item = must_have_items[-1]
    assert new_item["text"] == "熟悉 LangGraph"
    assert new_item["value"] == "熟悉 LangGraph"
    assert new_item["selected"] is True
    assert new_item["enabled"] is True
    assert new_item["editable"] is True
    assert new_item["source"] == "workbench_v2_user"
    assert new_item["status"] == "resolved"
    assert new_item["allowed_actions"] == ["select", "edit", "delete", "move_to_preferred_capabilities"]
    assert new_item["sort_order"] > must_have_items[-2]["sort_order"]
    assert "熟悉 LangGraph" in latest_form["requirementSheet"]["must_have_capabilities"]
    assert latest_form["runtimeInput"] == runtime_input


def test_requirement_action_confirm_after_deselect_starts_runtime_from_updated_form(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    runtime = FakeRuntimeService(
        requirement_sheet=_requirement_sheet_payload().model_copy(
            update={"must_have_capabilities": ["Python 后端开发"]}
        )
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-action-confirm"))
    item_id = first_view.requirementForm["draft"]["sections"][0]["items"][0]["item_id"]
    deselected_view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="set_selected",
            item_id=item_id,
            selected=False,
            idempotency_key="select-before-confirm",
        )
    )

    view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="confirm",
            idempotency_key="confirm-action",
        )
    )
    payload = view.model_dump(mode="json")

    assert runtime.start_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(runtime_input),
            "requirement_sheet": RequirementSheet.model_validate(deselected_view.requirementForm["requirementSheet"]),
            "idempotency_key": "confirm-action",
            "draft_revision_id": deselected_view.requirementForm["draft"]["draft_revision_id"],
            "selected_item_ids": [],
            "deselected_item_ids": [item_id],
        }
    ]
    assert runtime.start_calls[0]["requirement_sheet"].must_have_capabilities == []
    confirmed = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form_confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0]["payload"]["requirementSheet"]["must_have_capabilities"] == []
    assert confirmed[0]["payload"]["readonly"] is True
    assert payload["runtime"] == {"state": "queued", "runtimeRunId": "rtrun_1"}


def test_requirement_action_idempotency_replay_does_not_append_duplicate_events(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-idem-action"))
    item_id = first_view.requirementForm["draft"]["sections"][0]["items"][0]["item_id"]

    first_select = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="set_selected",
            item_id=item_id,
            selected=False,
            idempotency_key="select-idem",
        )
    )
    second_select = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="set_selected",
            item_id=item_id,
            selected=False,
            idempotency_key="select-idem",
        )
    )
    first_confirm = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="confirm",
            idempotency_key="confirm-idem",
        )
    )
    second_confirm = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="confirm",
            idempotency_key="confirm-idem",
        )
    )

    assert len([event for event in second_select.transcriptEvents if event.type == "requirement_form"]) == 2
    assert first_select.requirementForm == second_select.requirementForm
    assert len([event for event in second_confirm.transcriptEvents if event.type == "requirement_form_confirmed"]) == 1
    assert first_confirm.requirementForm == second_confirm.requirementForm


def test_confirm_requirements_without_current_form_appends_deterministic_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    agent = FakeAgentLoop(
        _agent_output(intent="chat", message="你好。"),
        _agent_output(intent="confirm_requirements", message="开始运行。"),
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("你好", idempotency_key="create-no-form"))

    view = asyncio.run(
        service.submit_message(first_view.conversation.conversationId, "确认需求", idempotency_key="confirm-no-form")
    )
    payload = view.model_dump(mode="json")

    assert runtime.start_calls == []
    error_events = [event for event in payload["transcriptEvents"] if event["type"] == "error"]
    assert error_events[-1]["payload"] == {
        "code": "workbench_v2_requirement_form_required",
        "message": "当前没有可确认的需求表单，无法启动运行。",
    }
    assert payload["transcriptEvents"][-1]["type"] == "assistant_message"
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "当前没有可确认的需求表单，无法启动运行。"}


def test_confirm_requirements_with_form_missing_sheet_appends_deterministic_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(first_user_text="招一个 AI 平台工程师", idempotency_key="missing-sheet")
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="requirement_form",
            role="assistant",
            payload={"runtimeInput": runtime_input, "draft": _draft_payload().model_dump(mode="json")},
        ),
    )
    agent = FakeAgentLoop(_agent_output(intent="confirm_requirements", message="确认，开始运行。"))
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.submit_message(conversation.id, "确认需求", idempotency_key="confirm-missing-sheet"))
    payload = view.model_dump(mode="json")

    assert runtime.extract_calls == []
    assert runtime.start_calls == []
    error_events = [event for event in payload["transcriptEvents"] if event["type"] == "error"]
    assert error_events[-1]["payload"] == {
        "code": "workbench_v2_requirement_sheet_required",
        "message": "需求表单缺少 requirementSheet，无法启动运行。",
    }


def test_start_runtime_with_runtime_input_starts_without_current_form(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(intent="start_runtime", message="开始运行。", runtimeInput=runtime_input),
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("直接开始运行", idempotency_key="start-runtime"))
    payload = view.model_dump(mode="json")

    assert runtime.start_calls == [
        {
            "conversation_id": view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(runtime_input),
            "idempotency_key": "start-runtime",
            "draft_revision_id": None,
            "selected_item_ids": None,
            "deselected_item_ids": None,
        }
    ]
    confirmed = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form_confirmed"]
    assert confirmed[-1]["payload"] == {
        "runtimeInput": runtime_input,
        "readonly": True,
        "runtimeRunId": "rtrun_1",
    }
    assert payload["requirementForm"] == confirmed[-1]["payload"]
    assert payload["runtime"] == {"state": "queued", "runtimeRunId": "rtrun_1"}


def test_start_runtime_with_current_form_confirms_output_runtime_input(tmp_path: Path) -> None:
    store = _store(tmp_path)
    form_runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "旧 JD。",
        "notes": None,
    }
    start_runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "新的完整 JD。",
        "notes": "上海",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=form_runtime_input),
        _agent_output(intent="start_runtime", message="开始运行。", runtimeInput=start_runtime_input),
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-start-form"))
    new_sheet = _requirement_sheet_payload().model_copy(
        update={
            "role_summary": "Build production AI platform systems in Shanghai.",
            "hard_constraints": HardConstraintSlots(locations=["上海"]),
        }
    )
    runtime.requirement_sheet = new_sheet
    runtime.draft = _draft_payload().model_copy(update={"draft_revision_id": "reqdraft_2"})

    view = asyncio.run(
        service.submit_message(
            first_view.conversation.conversationId,
            "用这份新 JD 直接开始",
            idempotency_key="start-from-form",
        )
    )
    payload = view.model_dump(mode="json")
    confirmed = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form_confirmed"]

    assert runtime.start_calls[-1]["runtime_input"] == WorkbenchV2RuntimeInput.model_validate(start_runtime_input)
    assert runtime.start_calls[-1]["requirement_sheet"] == new_sheet
    assert runtime.extract_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(form_runtime_input),
        },
        {
            "conversation_id": first_view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(start_runtime_input),
        },
    ]
    assert confirmed[-1]["payload"]["runtimeInput"] == start_runtime_input
    assert confirmed[-1]["payload"]["draft"]["draft_revision_id"] == "reqdraft_2"
    assert confirmed[-1]["payload"]["requirementSheet"] == new_sheet.model_dump(mode="json")
    assert confirmed[-1]["payload"]["requirementSheet"] != first_view.requirementForm["requirementSheet"]


def test_confirm_requirements_runtime_start_failure_appends_terminal_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
        _agent_output(intent="confirm_requirements", message="已确认，开始运行。"),
    )
    runtime = FakeRuntimeService(start_errors=[RuntimeError("boom")])
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-start-fail"))

    view = asyncio.run(
        service.submit_message(
            first_view.conversation.conversationId,
            "确认需求，开始运行",
            idempotency_key="confirm-start-fail",
        )
    )
    payload = view.model_dump(mode="json")

    assert len(runtime.start_calls) == 1
    assert payload["conversation"]["runtimeRunId"] is None
    assert payload["runtime"] is None
    assert "readonly" not in payload["requirementForm"]
    assert not [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form_confirmed"]
    assert not [event for event in payload["transcriptEvents"] if event["type"] == "runtime_progress"]
    assert payload["transcriptEvents"][-2]["type"] == "error"
    assert payload["transcriptEvents"][-2]["payload"] == {
        "code": "workbench_v2_runtime_start_failed",
        "message": "运行启动失败，请稍后重试。",
    }
    assert payload["transcriptEvents"][-1]["type"] == "assistant_message"
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "运行启动失败，请稍后重试。"}


def test_start_runtime_without_form_start_failure_appends_terminal_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(intent="start_runtime", message="开始运行。", runtimeInput=runtime_input),
    )
    runtime = FakeRuntimeService(start_errors=[RuntimeError("boom")])
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("直接开始运行", idempotency_key="start-runtime-fail"))
    payload = view.model_dump(mode="json")

    assert len(runtime.start_calls) == 1
    assert payload["conversation"]["runtimeRunId"] is None
    assert payload["runtime"] is None
    assert payload["requirementForm"] is None
    assert not [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form_confirmed"]
    assert not [event for event in payload["transcriptEvents"] if event["type"] == "runtime_progress"]
    assert payload["transcriptEvents"][-2]["type"] == "error"
    assert payload["transcriptEvents"][-2]["payload"] == {
        "code": "workbench_v2_runtime_start_failed",
        "message": "运行启动失败，请稍后重试。",
    }
    assert payload["transcriptEvents"][-1]["type"] == "assistant_message"
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "运行启动失败，请稍后重试。"}


def test_get_runtime_status_without_run_appends_idle_progress(tmp_path: Path) -> None:
    store = _store(tmp_path)
    agent = FakeAgentLoop(
        _agent_output(intent="get_runtime_status", message="当前还没有开始运行。"),
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())

    view = asyncio.run(service.create_conversation("现在运行到哪了？", idempotency_key="status-idle"))
    payload = view.model_dump(mode="json")

    assert [event["type"] for event in payload["transcriptEvents"]] == [
        "user_message",
        "runtime_progress",
        "assistant_message",
    ]
    assert payload["transcriptEvents"][1]["payload"] == {"state": "idle", "summary": "当前还没有开始运行。"}
    assert payload["runtime"] is None


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_get_runtime_status_updates_top_level_state_to_match_progress(tmp_path: Path, status: str) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
        _agent_output(intent="confirm_requirements", message="已确认，开始运行。"),
        _agent_output(intent="get_runtime_status", message="已刷新运行状态。"),
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-status"))
    confirmed_view = asyncio.run(
        service.submit_message(first_view.conversation.conversationId, "确认需求", idempotency_key="confirm-status")
    )
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": status,
        "stage": "finalization",
        "summary": f"status is {status}",
    }

    view = asyncio.run(
        service.submit_message(
            confirmed_view.conversation.conversationId,
            "现在运行到哪了？",
            idempotency_key=f"status-{status}",
        )
    )
    payload = view.model_dump(mode="json")

    assert payload["conversation"]["runtimeState"] == status
    assert payload["runtime"] == {"state": status, "runtimeRunId": "rtrun_1"}
    progress = [event for event in payload["transcriptEvents"] if event["type"] == "runtime_progress"]
    assert progress[-1]["payload"] == {
        "runtimeRunId": "rtrun_1",
        "status": status,
        "stage": "finalization",
        "summary": f"status is {status}",
        "state": status,
    }


def _store(tmp_path: Path) -> WorkbenchV2Store:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    return store


def _agent_output(
    *,
    intent: str,
    message: str,
    needsClarification: bool = False,
    clarifyingQuestion: str | None = None,
    runtimeInput: dict[str, object] | None = None,
) -> WorkbenchV2AgentOutput:
    return WorkbenchV2AgentOutput.model_validate(
        {
            "intent": intent,
            "message": message,
            "needsClarification": needsClarification,
            "clarifyingQuestion": clarifyingQuestion,
            "runtimeInput": runtimeInput,
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        }
    )


def _draft_payload() -> RequirementDraft:
    empty_sections = [
        RequirementDraftSection(section_id=section_id, display_name=display_name, backend_field=backend_field, items=[])
        for section_id, display_name, backend_field in (
            ("preferred_capabilities", "加分项", "preferred_capabilities"),
            ("hard_constraints", "硬性筛选条件", "hard_constraints"),
            ("exclusion_signals", "排除信号", "exclusion_signals"),
            ("initial_query_term_pool", "检索关键词", "initial_query_term_pool[].term"),
        )
    ]
    return RequirementDraft(
        conversation_id="agentv2_1",
        draft_revision_id="reqdraft_1",
        base_revision_id=None,
        status="draft_ready",
        sections=[
            RequirementDraftSection(
                section_id="must_have_capabilities",
                display_name="必须满足",
                backend_field="must_have_capabilities",
                items=[
                    RequirementDraftItem(
                        item_id="must_have_capabilities_1",
                        selected=True,
                        enabled=True,
                        editable=True,
                        text="Python 后端开发",
                        value="Python 后端开发",
                        source="workbench_v2_agent",
                        status="resolved",
                        review_item_id=None,
                        amendment_id=None,
                        source_span_refs=[],
                        sort_order=0,
                        allowed_actions=[],
                    )
                ],
            ),
            *empty_sections,
        ],
        created_at="2026-06-25T01:02:03.000004+00:00",
        latest=True,
        can_confirm=True,
        unresolved_review_item_count=0,
        amendment=None,
    )


def _requirement_sheet_payload() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI 平台工程师",
        title_anchor_terms=["AI 平台工程师"],
        title_anchor_rationale="The job title names the platform role.",
        role_summary="Build AI agent platform systems.",
        must_have_capabilities=["Python 后端开发", "Agent 工作流经验"],
        preferred_capabilities=["RAG 经验"],
        exclusion_signals=["没有生产系统经验"],
        hard_constraints=HardConstraintSlots(locations=["杭州"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="AI 平台工程师",
                source="job_title",
                category="role_anchor",
                priority=100,
                evidence="岗位名称",
                first_added_round=0,
            )
        ],
        scoring_rationale="Prioritize platform engineering and agent workflow experience.",
    )
