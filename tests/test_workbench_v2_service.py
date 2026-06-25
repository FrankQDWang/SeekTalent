from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Sequence

import seektalent_workbench_v2.service as service_module
from seektalent_runtime_control.requirements import RequirementDraft, RequirementDraftItem, RequirementDraftSection
from seektalent_workbench_v2.agent_loop import WorkbenchV2AgentOutput, WorkbenchV2RuntimeInput
from seektalent_workbench_v2.models import WorkbenchV2TranscriptEvent, WorkbenchV2TranscriptEventInput
from seektalent_workbench_v2.service import WorkbenchV2Service
from seektalent_workbench_v2.store import WorkbenchV2Store


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
    def __init__(self, draft: object | None = None) -> None:
        self.draft = draft or _draft_payload()
        self.extract_calls: list[dict[str, object]] = []

    def extract_requirements(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> object:
        self.extract_calls.append({"conversation_id": conversation_id, "runtime_input": runtime_input})
        return self.draft


def test_service_does_not_import_legacy_workbench_modules() -> None:
    source = inspect.getsource(service_module)

    assert "seektalent_ui" not in source
    assert "seektalent_conversation_agent" not in source
    assert "first_turn" not in source
    assert "outbox" not in source
    assert "projection" not in source


def test_create_pure_chat_conversation_does_not_extract_requirements(tmp_path: Path) -> None:
    store = _store(tmp_path)
    agent = FakeAgentLoop(_agent_output(intent="chat", message="你好，我可以帮你处理招聘需求。"))
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("先随便聊聊", idempotency_key="create-chat"))

    assert [event.type for event in view.transcriptEvents] == ["user_message", "assistant_message"]
    assert view.transcriptEvents[0].payload == {"text": "先随便聊聊"}
    assert view.transcriptEvents[1].payload == {"text": "你好，我可以帮你处理招聘需求。"}
    assert runtime.extract_calls == []
    assert view.requirementForm is None
    assert "transcriptGroups" not in view.model_dump(mode="json")


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
    assert view.requirementForm == form_event.payload
    assert runtime.extract_calls == [
        {
            "conversation_id": view.conversation.id,
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
    store.append_context_summary(first_view.conversation.id, summary="用户正在招聘 AI 平台工程师，偏杭州。")

    asyncio.run(service.submit_message(first_view.conversation.id, "补充：需要 RAG 经验", idempotency_key="submit-1"))

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

    assert view.schemaVersion == "agent.workbench.v2"
    assert [conversation.id for conversation in view.conversations] == [second.id, first.id]
    assert view.conversations[0].runtimeState == "idle"
    assert "transcriptGroups" not in view.model_dump(mode="json")


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
            )
        ],
        created_at="2026-06-25T01:02:03.000004+00:00",
        latest=True,
        can_confirm=True,
        unresolved_review_item_count=0,
        amendment=None,
    )
