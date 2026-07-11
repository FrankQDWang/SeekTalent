from __future__ import annotations

import asyncio
import json
import logging
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, get_args

import pytest
from seektalent.models import HardConstraintSlots, QueryTermCandidate, RequirementSheet
import seektalent_workbench_v2.service as service_module
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.requirements import RequirementDraft, RequirementDraftItem, RequirementDraftSection
from seektalent_workbench_v2.agent_loop import WorkbenchV2AgentOutput, WorkbenchV2RuntimeInput
from seektalent_workbench_v2.models import (
    WorkbenchV2ConversationEventsView,
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
        self.status_errors: list[Exception] = []
        self.results_payloads: dict[str, dict[str, object]] = {}
        self.results_errors: list[Exception] = []
        self.progress_payloads: dict[str, list[dict[str, object]]] = {}
        self.candidate_payloads: dict[str, list[dict[str, object]]] = {}
        self.candidate_errors: list[Exception] = []
        self.candidate_detail_payloads: dict[tuple[str, str], dict[str, object]] = {}
        self.next_round_requirement_calls: list[dict[str, object]] = []
        self.next_round_requirement_errors: list[Exception] = []
        self.next_round_requirement_results: list[dict[str, object]] = []
        self.amend_requirement_calls: list[dict[str, object]] = []
        self.amend_requirement_results: list[FakeRequirementExtraction] = []

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

    def amend_requirement_bundle(
        self,
        conversation_id: str,
        *,
        base_draft: RequirementDraft,
        base_requirement_sheet: RequirementSheet,
        text: str,
        idempotency_key: str,
    ) -> FakeRequirementExtraction:
        self.amend_requirement_calls.append(
            {
                "conversation_id": conversation_id,
                "base_draft_revision_id": base_draft.draft_revision_id,
                "base_requirement_sheet": base_requirement_sheet,
                "text": text,
                "idempotency_key": idempotency_key,
            }
        )
        if self.amend_requirement_results:
            return self.amend_requirement_results.pop(0)
        effective_base_sheet = service_module.requirement_sheet_from_draft(base_draft, base_requirement_sheet)
        amended_sheet = effective_base_sheet.model_copy(
            update={
                "must_have_capabilities": [
                    *effective_base_sheet.must_have_capabilities,
                    f"抽取后：{text}",
                ]
            }
        )
        amended_draft = base_draft.model_copy(
            deep=True,
            update={
                "draft_revision_id": f"reqdraft_amended_{len(self.amend_requirement_calls)}",
                "base_revision_id": base_draft.draft_revision_id,
            },
        )
        must_have_section = amended_draft.section("must_have_capabilities")
        must_have_section.items.append(
            RequirementDraftItem(
                item_id=f"reqitem_amended_{len(self.amend_requirement_calls)}",
                selected=True,
                enabled=True,
                editable=True,
                text=f"抽取后：{text}",
                value=f"抽取后：{text}",
                source="workbench_v2_agent",
                status="resolved",
                review_item_id=None,
                amendment_id=f"reqamend_{len(self.amend_requirement_calls)}",
                source_span_refs=[],
                sort_order=(len(must_have_section.items) + 1) * 10,
                allowed_actions=[],
            )
        )
        return FakeRequirementExtraction(draft=amended_draft, requirement_sheet=amended_sheet)

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
        if self.status_errors:
            raise self.status_errors.pop(0)
        return self.status_payloads[runtime_run_id]

    def get_results(self, runtime_run_id: str) -> dict[str, object]:
        if self.results_errors:
            raise self.results_errors.pop(0)
        return self.results_payloads[runtime_run_id]

    def list_progress_events(self, runtime_run_id: str, *, after_seq: int, limit: int = 200) -> list[dict[str, object]]:
        return [
            payload
            for payload in self.progress_payloads.get(runtime_run_id, [])
            if isinstance(payload.get("runtimeEventSeq"), int) and payload["runtimeEventSeq"] > after_seq
        ][:limit]

    def list_candidate_summaries(self, runtime_run_id: str, *, limit: int = 20) -> list[dict[str, object]]:
        if self.candidate_errors:
            raise self.candidate_errors.pop(0)
        return self.candidate_payloads.get(runtime_run_id, [])[:limit]

    def get_candidate_detail(self, runtime_run_id: str, candidate_id: str) -> dict[str, object]:
        return self.candidate_detail_payloads[(runtime_run_id, candidate_id)]

    def submit_next_round_requirement(
        self,
        runtime_run_id: str,
        text: str,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        self.next_round_requirement_calls.append(
            {
                "runtime_run_id": runtime_run_id,
                "text": text,
                "idempotency_key": idempotency_key,
            }
        )
        if self.next_round_requirement_errors:
            raise self.next_round_requirement_errors.pop(0)
        if self.next_round_requirement_results:
            return self.next_round_requirement_results.pop(0)
        return {
            "amendmentId": f"reqamend_{len(self.next_round_requirement_calls)}",
            "status": "pending_target_round",
            "targetRoundNo": 2,
            "effectiveBoundary": "before_round_controller",
            "approvedRequirementRevisionId": f"reqapproved_next_{len(self.next_round_requirement_calls)}",
            "reviewRequired": False,
        }

    def _runtime_run(self, conversation_id: str, *, idempotency_key: str | None) -> RuntimeRunRecord:
        if self.start_errors:
            raise self.start_errors.pop(0)
        run_index = len(self.start_calls)
        runtime_run_id = f"rtrun_{run_index}"
        self.status_payloads.setdefault(
            runtime_run_id,
            {
                "runtimeRunId": runtime_run_id,
                "status": "queued",
                "stage": "queued",
                "summary": "招聘流程已排队，等待开始。",
            },
        )
        return RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
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


class AsyncioRunRequirementRuntime(FakeRuntimeService):
    def extract_requirement_bundle(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> FakeRequirementExtraction:
        asyncio.run(_empty_async_step())
        return super().extract_requirement_bundle(conversation_id, runtime_input)

    def amend_requirement_bundle(
        self,
        conversation_id: str,
        *,
        base_draft: RequirementDraft,
        base_requirement_sheet: RequirementSheet,
        text: str,
        idempotency_key: str,
    ) -> FakeRequirementExtraction:
        asyncio.run(_empty_async_step())
        return super().amend_requirement_bundle(
            conversation_id,
            base_draft=base_draft,
            base_requirement_sheet=base_requirement_sheet,
            text=text,
            idempotency_key=idempotency_key,
        )


async def _empty_async_step() -> None:
    return None


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
    assert set(payload) == {
        "schemaVersion",
        "conversation",
        "transcriptEvents",
        "requirementForm",
        "runtime",
        "strategyGraph",
        "thinkingProcess",
        "candidates",
    }
    assert payload["strategyGraph"] == {"nodes": [], "edges": []}
    assert payload["thinkingProcess"] == {"activeRoundNo": None, "rounds": []}
    assert payload["candidates"] == []
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


def test_create_jd_conversation_logs_safe_timing_diagnostics(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="seektalent_workbench_v2.service")
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
    user_text = "招一个 AI 平台工程师，负责 Agent 工作流和 Python 后端。"

    view = asyncio.run(service.create_conversation(user_text, idempotency_key="create-jd-logs"))

    event_names = [getattr(record, "event_name", None) for record in caplog.records]
    assert "workbench_v2_user_message_persisted" in event_names
    assert "workbench_v2_agent_loop_completed" in event_names
    assert "workbench_v2_requirement_extract_started" in event_names
    assert "workbench_v2_requirement_extract_completed" in event_names
    agent_completed = next(
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "workbench_v2_agent_loop_completed"
    )
    assert agent_completed.conversation_id == view.conversation.conversationId
    assert agent_completed.intent == "extract_requirements"
    assert agent_completed.duration_ms >= 0
    extract_completed = next(
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "workbench_v2_requirement_extract_completed"
    )
    assert extract_completed.duration_ms >= 0
    serialized_records = json.dumps([record.__dict__ for record in caplog.records], ensure_ascii=False, default=str)
    assert user_text not in serialized_records
    assert runtime_input["jd"] not in serialized_records
    assert runtime_input["notes"] not in serialized_records


def test_create_jd_with_runtime_words_still_uses_agent_intent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "淘天集团-数据科学专家",
        "jd": "负责数据体系、AB 实验、SQL/Python 分析、因果推断和机器学习建模。",
        "notes": "数据驱动和结果导向；面试流程：业务+1-业务+2-hrg。",
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
    jd_text = (
        "淘天集团-数据科学专家\n"
        "岗位职责：负责数据体系建设，数据驱动和结果导向，熟悉 SQL/Python、A/B Testing、因果推断。\n"
        "岗位要求：5年以上经验，杭州。\n"
        "面试流程：业务+1-业务+2-hrg。"
    )

    view = asyncio.run(service.create_conversation(jd_text, idempotency_key="create-jd-runtime-words"))

    assert len(agent.calls) == 1
    assert [event.type for event in view.transcriptEvents] == [
        "user_message",
        "assistant_status",
        "requirement_form",
        "assistant_message",
    ]
    assert not [event for event in view.transcriptEvents if event.type == "runtime_result"]
    assert view.requirementForm is not None
    expected_runtime_input = {
        **runtime_input,
        "jd": jd_text,
    }
    assert view.requirementForm["runtimeInput"] == expected_runtime_input
    assert runtime.extract_calls == [
        {
            "conversation_id": view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(expected_runtime_input),
        }
    ]


def test_agent_must_not_send_abbreviated_jd_to_runtime(tmp_path: Path) -> None:
    store = _store(tmp_path)
    full_jd_text = (
        "淘天集团-数据科学专家\n"
        "岗位职责：负责指标体系、AB 实验、SQL/Python 数据分析、因果推断和机器学习建模。\n"
        "岗位要求：5年以上经验，杭州，知名互联网或 AI 高科技公司背景。"
    )
    runtime_input = {
        "jobTitle": "数据科学专家",
        "jd": "淘天集团-数据科学专家...",
        "notes": "工作城市：杭州；必备条件：知名互联网或 AI 高科技公司背景。",
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

    view = asyncio.run(service.create_conversation(full_jd_text, idempotency_key="create-abbreviated-jd"))

    assert view.requirementForm is not None
    assert view.requirementForm["runtimeInput"] == {
        **runtime_input,
        "jd": full_jd_text,
    }
    assert runtime.extract_calls == [
        {
            "conversation_id": view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(
                {
                    **runtime_input,
                    "jd": full_jd_text,
                }
            ),
        }
    ]


def test_agent_must_not_send_compacted_jd_to_runtime(tmp_path: Path) -> None:
    store = _store(tmp_path)
    full_jd_text = (
        "淘天集团-数据科学专家\n"
        "岗位职责：\n"
        "1. 为淘天核心业务策略、创新商业策略建立完整、科学的指标监控、预测、评估、洞察的数据体系；\n"
        "2. 数据驱动和结果导向，设计合适的分析和数科解决方案，利用运筹优化、博弈论、因果推断、机器学习建模等方法提升业务增长；\n"
        "3. 数据产品建设，负责科学分析能力产品落地，主要包括多维分析、归因分析、智能 tips 等数据科学组件。\n"
        "岗位要求：\n"
        "1. 统计、数学、计算机、大数据相关专业，本科及以上学历，至少 5 年以上数据分析/数据挖掘/数据科学工作经验；\n"
        "2. 熟练运用 SQL 和 Python，熟悉 A/B Testing 实验理论和流程；\n"
        "3. 喜欢并善于从海量数据中发现规律，能基于业务目标确定指标体系；\n"
        "4. 有管理咨询、商业分析、应用算法进行数据挖掘经验者优先。\n"
        "工作城市：杭州。必备条件：知名互联网或 AI 高科技公司背景，五年三跳不要。"
    )
    compacted_runtime_input = {
        "jobTitle": "数据科学专家",
        "jd": "负责指标体系建设、AB 实验、SQL/Python 数据分析、因果推断、机器学习建模和业务增长分析。",
        "notes": "杭州；知名互联网或 AI 高科技公司背景；五年三跳不要。",
    }
    agent = FakeAgentLoop(
        _agent_output(
            intent="extract_requirements",
            message="我已整理需求，请确认表单。",
            runtimeInput=compacted_runtime_input,
        )
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation(full_jd_text, idempotency_key="create-compacted-jd"))

    expected_runtime_input = {
        **compacted_runtime_input,
        "jd": full_jd_text,
    }
    assert view.requirementForm is not None
    assert view.requirementForm["runtimeInput"] == expected_runtime_input
    assert runtime.extract_calls == [
        {
            "conversation_id": view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(expected_runtime_input),
        }
    ]


def test_create_jd_conversation_normalizes_form_ready_assistant_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(
            intent="extract_requirements",
            message="我将为您生成招聘需求表单，请稍候。",
            runtimeInput=runtime_input,
        )
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())

    view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-jd-message"))

    assert [event.type for event in view.transcriptEvents] == [
        "user_message",
        "assistant_status",
        "requirement_form",
        "assistant_message",
    ]
    assert view.transcriptEvents[-1].payload == {
        "text": "已根据你的输入生成需求确认表单，请检查、取消不需要的条件，或补充其他要求。"
    }


def test_create_jd_runs_sync_runtime_extraction_outside_route_event_loop(tmp_path: Path) -> None:
    store = _store(tmp_path)
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
    runtime = AsyncioRunRequirementRuntime()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)

    view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-threaded"))

    assert view.requirementForm is not None
    assert view.requirementForm["runtimeInput"] == runtime_input
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
        service.submit_message(
            first_view.conversation.conversationId, "补充：需要 RAG 经验", idempotency_key="submit-1"
        )
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


def test_v2_strategy_graph_does_not_show_final_shortlist_before_runtime_result(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 11,
            "runtimeEventType": "runtime_round_query_ready",
            "status": "completed",
            "stage": "round_query",
            "roundNo": 1,
            "summary": "第 1 轮查询策略已生成。",
            "details": {
                "queryGroups": [
                    _v2_query_group(
                        query_instance_id="query-1",
                        term_group_key="group-1",
                        query_role="exploit",
                        lane_type="exploit",
                        query_terms=["数据科学家", "A/B Testing"],
                        keyword_query='数据科学家 "A/B Testing"',
                    )
                ],
            },
            "state": "running",
        },
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 20,
            "runtimeEventType": "runtime_round_source_result",
            "status": "completed",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": "liepin",
            "summary": "第 1 轮猎聘检索完成。",
            "details": {},
            "state": "running",
        },
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 24,
            "runtimeEventType": "runtime_round_merge_completed",
            "status": "completed",
            "stage": "merge",
            "roundNo": 1,
            "summary": "第 1 轮候选人合并完成。",
            "details": {},
            "state": "running",
        },
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 25,
            "runtimeEventType": "runtime_round_scoring_completed",
            "status": "completed",
            "stage": "scoring",
            "roundNo": 1,
            "summary": "第 1 轮评分完成。",
            "details": {
                "resumeQualityComment": "本轮简历质量偏低，候选人缺少搜索推荐系统落地经验。",
            },
            "state": "running",
        },
    ]

    view = service.get_conversation(conversation_id)
    payload = view.model_dump(mode="json")

    assert [node["label"] for node in payload["strategyGraph"]["nodes"]] == [
        "需求拆解",
        "第 1 轮 · 查询包",
        "第 1 轮 · 猎聘检索",
        "第 1 轮 · 去重合并",
        "第 1 轮 · Top Pool",
    ]
    assert not any(node["kind"] == "final" for node in payload["strategyGraph"]["nodes"])
    assert payload["thinkingProcess"]["activeRoundNo"] == 1
    assert payload["thinkingProcess"]["rounds"][0] == {
        "roundNo": 1,
        "status": "completed",
        "queryGroups": [
            _v2_query_group(
                query_instance_id="query-1",
                term_group_key="group-1",
                query_role="exploit",
                lane_type="exploit",
                query_terms=["数据科学家", "A/B Testing"],
                keyword_query='数据科学家 "A/B Testing"',
            )
        ],
        "cards": [
            {
                "title": "observation",
                "text": "本轮简历质量偏低，候选人缺少搜索推荐系统落地经验。",
                "terms": [],
            }
        ],
    }
    assert payload["candidates"] == []


def test_v2_strategy_graph_adds_reflection_only_after_reflection_event(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 11,
            "runtimeEventType": "runtime_round_query_ready",
            "status": "completed",
            "stage": "round_query",
            "roundNo": 1,
            "summary": "第 1 轮查询策略已生成。",
            "details": {
                "queryGroups": [
                    _v2_query_group(
                        query_instance_id="query-1",
                        term_group_key="group-1",
                        query_role="exploit",
                        lane_type="exploit",
                        query_terms=["交互设计", "用户研究"],
                        keyword_query="交互设计 用户研究",
                    )
                ],
            },
            "state": "running",
        },
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 25,
            "runtimeEventType": "runtime_round_feedback_completed",
            "status": "completed",
            "stage": "reflection",
            "roundNo": 1,
            "summary": "第 1 轮复盘完成。",
            "details": {
                "reflectionSummary": "下一轮降低行业限制，扩大 B 端体验关键词。",
            },
            "state": "running",
        },
    ]

    view = service.get_conversation(conversation_id)
    payload = view.model_dump(mode="json")

    assert [node["label"] for node in payload["strategyGraph"]["nodes"]] == [
        "需求拆解",
        "第 1 轮 · 查询包",
        "第 1 轮 · 下一轮策略",
    ]
    assert payload["thinkingProcess"]["rounds"][0]["queryGroups"] == [
        _v2_query_group(
            query_instance_id="query-1",
            term_group_key="group-1",
            query_role="exploit",
            lane_type="exploit",
            query_terms=["交互设计", "用户研究"],
            keyword_query="交互设计 用户研究",
        )
    ]
    assert payload["thinkingProcess"]["rounds"][0]["cards"] == [
        {
            "title": "反思和下一轮变更",
            "text": "下一轮降低行业限制，扩大 B 端体验关键词。",
            "terms": [],
        }
    ]


def test_v2_feedback_observation_without_reflection_emits_only_observation(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 11,
            "runtimeEventType": "runtime_round_query_ready",
            "status": "completed",
            "stage": "round_query",
            "roundNo": 1,
            "summary": "第 1 轮查询策略已生成。",
            "details": {
                "queryGroups": [
                    _v2_query_group(
                        query_instance_id="query-1",
                        term_group_key="group-1",
                        query_role="exploit",
                        lane_type="exploit",
                        query_terms=["增长产品", "用户研究"],
                        keyword_query="增长产品 用户研究",
                    )
                ],
            },
            "state": "running",
        },
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 25,
            "runtimeEventType": "runtime_round_feedback_completed",
            "status": "completed",
            "stage": "feedback",
            "roundNo": 1,
            "summary": "第 1 轮复盘完成。",
            "details": {
                "resumeQualityComment": "候选人简历质量较高，但增长实验经验需要继续确认。",
            },
            "state": "running",
        },
    ]

    view = service.get_conversation(conversation_id)
    payload = view.model_dump(mode="json")

    assert [node["label"] for node in payload["strategyGraph"]["nodes"]] == [
        "需求拆解",
        "第 1 轮 · 查询包",
        "第 1 轮 · 下一轮策略",
    ]
    assert payload["thinkingProcess"]["rounds"][0]["queryGroups"] == [
        _v2_query_group(
            query_instance_id="query-1",
            term_group_key="group-1",
            query_role="exploit",
            lane_type="exploit",
            query_terms=["增长产品", "用户研究"],
            keyword_query="增长产品 用户研究",
        )
    ]
    assert payload["thinkingProcess"]["rounds"][0]["cards"] == [
        {
            "title": "observation",
            "text": "候选人简历质量较高，但增长实验经验需要继续确认。",
            "terms": [],
        }
    ]


def test_conversation_view_does_not_show_future_graph_nodes_before_events(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 11,
            "runtimeEventType": "runtime_round_query_ready",
            "status": "completed",
            "stage": "round_query",
            "roundNo": 1,
            "summary": "第 1 轮查询策略已生成。",
            "details": {
                "queryGroups": [
                    _v2_query_group(
                        query_instance_id="query-1",
                        term_group_key="group-1",
                        query_role="exploit",
                        lane_type="exploit",
                        query_terms=["数据科学家", "SQL"],
                        keyword_query="数据科学家 SQL",
                    )
                ],
            },
            "state": "running",
        }
    ]

    view = service.get_conversation(conversation_id)
    payload = view.model_dump(mode="json")

    assert [node["label"] for node in payload["strategyGraph"]["nodes"]] == [
        "需求拆解",
        "第 1 轮 · 查询包",
    ]
    assert not any("猎聘检索" in node["label"] for node in payload["strategyGraph"]["nodes"])
    assert not any("Top Pool" in node["label"] for node in payload["strategyGraph"]["nodes"])
    assert not any(node["kind"] == "final" for node in payload["strategyGraph"]["nodes"])
    assert payload["thinkingProcess"]["rounds"][0]["queryGroups"] == [
        _v2_query_group(
            query_instance_id="query-1",
            term_group_key="group-1",
            query_role="exploit",
            lane_type="exploit",
            query_terms=["数据科学家", "SQL"],
            keyword_query="数据科学家 SQL",
        )
    ]
    assert payload["thinkingProcess"]["rounds"][0]["cards"] == []


def test_v2_source_result_does_not_generate_observation_card(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 11,
            "runtimeEventType": "runtime_round_query_ready",
            "status": "completed",
            "stage": "round_query",
            "roundNo": 1,
            "summary": "第 1 轮查询策略已生成。",
            "details": {
                "queryGroups": [
                    _v2_query_group(
                        query_instance_id="query-1",
                        term_group_key="group-1",
                        query_role="exploit",
                        lane_type="exploit",
                        query_terms=["数据科学家", "SQL"],
                        keyword_query="数据科学家 SQL",
                    )
                ],
            },
            "state": "running",
        },
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 20,
            "runtimeEventType": "runtime_round_source_result",
            "status": "completed",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": "liepin",
            "summary": "第 1 轮检索完成。",
            "details": {
                "resumeQualityComment": "source result 里的候选人摘要不能当 observation。",
            },
            "state": "running",
        },
    ]

    view = service.get_conversation(conversation_id)
    payload = view.model_dump(mode="json")

    assert [node["label"] for node in payload["strategyGraph"]["nodes"]] == [
        "需求拆解",
        "第 1 轮 · 查询包",
        "第 1 轮 · 猎聘检索",
    ]
    assert payload["thinkingProcess"]["rounds"][0]["queryGroups"] == [
        _v2_query_group(
            query_instance_id="query-1",
            term_group_key="group-1",
            query_role="exploit",
            lane_type="exploit",
            query_terms=["数据科学家", "SQL"],
            keyword_query="数据科学家 SQL",
        )
    ]
    assert payload["thinkingProcess"]["rounds"][0]["cards"] == []


def test_v2_blocked_liepin_source_result_reports_actionable_reason(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 20,
            "runtimeEventType": "runtime_round_source_result",
            "status": "blocked",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": "liepin",
            "summary": "source_result",
            "counts": {"roundReturned": 0, "roundIdentities": 0},
            "safeReasonCode": "source_browser_extension_disconnected",
            "state": "running",
        }
    ]

    view = service.get_conversation(conversation_id)
    progress_events = [event for event in view.transcriptEvents if event.type == "runtime_progress"]

    assert progress_events[-1].payload["summary"] == (
        "第 1 轮猎聘检索受阻：猎聘浏览器桥扩展未连接，请确认扩展已连接后重试。"
    )
    assert "猎聘检索完成" not in progress_events[-1].payload["summary"]


def test_v2_blocked_liepin_source_result_reports_filter_failure(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 20,
            "runtimeEventType": "runtime_round_source_result",
            "status": "blocked",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": "liepin",
            "summary": "source_result",
            "counts": {"roundReturned": 0, "roundIdentities": 0},
            "safeReasonCode": "source_filter_unavailable",
            "state": "running",
        }
    ]

    view = service.get_conversation(conversation_id)
    progress_events = [event for event in view.transcriptEvents if event.type == "runtime_progress"]

    assert (
        progress_events[-1].payload["summary"] == "第 1 轮猎聘检索受阻：猎聘筛选条件未成功应用，请刷新页面后重试。"
    )


def test_v2_blocked_liepin_source_result_uses_canonical_summary_instead_of_raw_summary(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 20,
            "runtimeEventType": "runtime_round_source_result",
            "status": "blocked",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": "liepin",
            "summary": "第 1 轮猎聘检索受阻：source_result",
            "counts": {"roundReturned": 0, "roundIdentities": 0},
            "state": "running",
        }
    ]

    view = service.get_conversation(conversation_id)
    progress_events = [event for event in view.transcriptEvents if event.type == "runtime_progress"]

    assert progress_events[-1].payload["summary"] == "第 1 轮猎聘检索受阻：猎聘检索受阻，请稍后重试。"


def test_list_events_returns_incremental_visible_events_and_refreshes_runtime(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    store = service.store
    store.append_context_summary(conversation_id, summary="内部摘要，不应该出现在增量事件里。")
    before_refresh = service.get_conversation(conversation_id)
    after_step = before_refresh.transcriptEvents[-1].step
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "running",
        "stage": "source_search",
        "summary": "正在检索候选人，进度 25%。",
    }

    events = service.list_events(conversation_id, after_step=after_step, limit=10)

    assert isinstance(events, WorkbenchV2ConversationEventsView)
    assert events.schemaVersion == "agent.workbench.v2.events"
    assert events.conversationId == conversation_id
    assert events.afterStep == after_step
    assert events.latestStep > after_step
    assert [event.type for event in events.events] == ["runtime_progress"]
    assert events.events[0].payload == {
        "runtimeRunId": "rtrun_1",
        "status": "running",
        "stage": "source_search",
        "summary": "招聘流程运行中，当前阶段：候选人检索。",
        "state": "running",
    }


def test_get_conversation_projects_runtime_events_by_runtime_seq(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 12,
            "runtimeEventType": "runtime_round_source_dispatch",
            "status": "running",
            "stage": "source_dispatch",
            "roundNo": 1,
            "summary": "已向猎聘发起候选人检索。",
            "state": "running",
        },
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 31,
            "runtimeEventType": "runtime_round_source_dispatch",
            "status": "running",
            "stage": "source_dispatch",
            "roundNo": 2,
            "summary": "已向猎聘发起候选人检索。",
            "state": "running",
        },
    ]
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "running",
        "stage": "source_dispatch",
        "summary": "已向猎聘发起候选人检索。",
    }

    first_refresh = service.get_conversation(conversation_id)
    second_refresh = service.get_conversation(conversation_id)

    progress_events = [event for event in second_refresh.transcriptEvents if event.type == "runtime_progress"]
    projected_seqs = [
        event.payload.get("runtimeEventSeq")
        for event in progress_events
        if event.payload.get("runtimeRunId") == "rtrun_1"
    ]
    assert projected_seqs.count(12) == 1
    assert projected_seqs.count(31) == 1
    projected_events = [
        event
        for event in first_refresh.transcriptEvents
        if event.type == "runtime_progress" and event.payload.get("runtimeEventSeq") in {12, 31}
    ]
    assert projected_events[-1].payload["runtimeEventSeq"] == 31
    assert projected_events[-1].payload["roundNo"] == 2
    assert len(second_refresh.transcriptEvents) == len(first_refresh.transcriptEvents)
    assert confirmed_view.conversation.runtimeRunId == "rtrun_1"
    assert first_refresh.thinkingProcess.rounds == []
    assert first_refresh.thinkingProcess.activeRoundNo is None


def test_conversation_view_includes_runtime_candidate_summaries(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.candidate_payloads["rtrun_1"] = [
        {
            "candidateId": "identity_1",
            "rank": 1,
            "displayName": "候选人 A",
            "headline": "数据科学家",
            "company": "某电商平台",
            "location": "杭州",
            "education": None,
            "experienceYears": None,
            "age": None,
            "gender": None,
            "activeStatus": None,
            "jobStatus": None,
            "sourceKinds": ["liepin"],
            "matchScore": 86,
            "matchSummary": "SQL/Python 和 A/B Testing 经验较匹配。",
            "status": "fit",
            "detailAvailability": "available",
            "accessState": "allowed",
            "evidenceLevel": "summary",
        }
    ]

    view = service.get_conversation(conversation_id)
    payload = view.model_dump(mode="json")

    assert payload["candidates"] == [
        {
            "candidateId": "identity_1",
            "rank": 1,
            "displayName": "候选人 A",
            "headline": "数据科学家",
            "company": "某电商平台",
            "location": "杭州",
            "education": None,
            "experienceYears": None,
            "age": None,
            "gender": None,
            "activeStatus": None,
            "jobStatus": None,
            "sourceKinds": ["liepin"],
            "matchScore": 86,
            "matchSummary": "SQL/Python 和 A/B Testing 经验较匹配。",
            "status": "fit",
            "detailAvailability": "available",
            "accessState": "allowed",
            "evidenceLevel": "summary",
        }
    ]


def test_conversation_view_surfaces_candidate_summary_read_errors(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.candidate_errors.append(RuntimeError("candidate store unavailable"))

    view = service.get_conversation(conversation_id)
    payload = view.model_dump(mode="json")

    assert payload["candidates"] == []
    assert payload["transcriptEvents"][-1]["type"] == "error"
    assert payload["transcriptEvents"][-1]["payload"] == {
        "code": "workbench_v2_candidate_summaries_unavailable",
        "message": "候选人列表读取失败，请稍后重试。",
    }


def test_get_candidate_detail_reads_runtime_candidate_detail_payload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(first_user_text="找数据科学家", idempotency_key="create-1")
    store.set_runtime(conversation.id, runtime_run_id="rtrun_1", runtime_state="completed")
    runtime = FakeRuntimeService()
    runtime.candidate_detail_payloads[("rtrun_1", "identity_1")] = {
        "candidateId": "identity_1",
        "displayName": "吴所谓",
        "headline": "资深体验设计工程师 · 平安集团",
        "sourceKinds": ["liepin"],
        "matchScore": 86,
        "sections": [
            {
                "title": "匹配程度",
                "items": ["推荐理由：做过复杂 B 端业务流程。"],
            },
            {
                "title": "工作经历",
                "items": ["2019.06-至今 平安好医｜用户体验设计专家"],
            },
        ],
        "evidence": ["来源：猎聘 detail 证据"],
        "detailAvailability": "available",
        "accessState": "allowed",
        "evidenceLevel": "detail",
        "reasonCode": None,
    }
    service = WorkbenchV2Service(
        store=store,
        agent_loop=FakeAgentLoop(),
        runtime_service=runtime,
    )

    detail = service.get_candidate_detail(conversation.id, "identity_1")
    payload = detail.model_dump(mode="json")

    assert payload["candidateId"] == "identity_1"
    assert payload["displayName"] == "吴所谓"
    assert payload["sections"][0]["title"] == "匹配程度"
    assert payload["sections"][1]["items"] == ["2019.06-至今 平安好医｜用户体验设计专家"]


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


def test_submit_idempotency_conflicts_when_replayed_message_changes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(first_user_text="你好", idempotency_key="create-submit-conflict")
    store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="user_message",
            role="user",
            payload={"text": "原始补充需求"},
            dedupe_key="workbench-v2-service:submit:submit-conflict:user",
        ),
    )
    service = WorkbenchV2Service(
        store=store,
        agent_loop=FakeAgentLoop(_agent_output(intent="chat", message="不应执行")),
        runtime_service=FakeRuntimeService(),
    )

    with pytest.raises(ValueError, match="workbench_v2_idempotency_conflict"):
        asyncio.run(service.submit_message(conversation.id, "另一个补充需求", idempotency_key="submit-conflict"))

    assert service.agent_loop.calls == []


def test_extract_failure_appends_terminal_error_without_request_failure(tmp_path: Path) -> None:
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

    failed_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-recover"))
    failed_payload = failed_view.model_dump(mode="json")

    assert [event["type"] for event in failed_payload["transcriptEvents"]] == [
        "user_message",
        "assistant_status",
        "error",
        "assistant_message",
    ]
    assert failed_payload["transcriptEvents"][-2]["payload"] == {
        "code": "workbench_v2_requirement_extract_failed",
        "message": "需求整理失败，请稍后重试。",
    }

    view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-recover"))

    assert len(agent.calls) == 1
    assert [event.type for event in view.transcriptEvents] == [
        "user_message",
        "assistant_status",
        "error",
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
        service.submit_message(
            first_view.conversation.conversationId, "确认需求，开始运行", idempotency_key="confirm-1"
        )
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


def test_requirement_action_confirm_with_supplement_reextracts_requirement_form_before_starting_runtime(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-add"))

    view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="confirm",
            text="熟悉 LangGraph",
            idempotency_key="other-1",
        )
    )
    payload = view.model_dump(mode="json")

    assert runtime.amend_requirement_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "base_draft_revision_id": first_view.requirementForm["draft"]["draft_revision_id"],
            "base_requirement_sheet": RequirementSheet.model_validate(first_view.requirementForm["requirementSheet"]),
            "text": "熟悉 LangGraph",
            "idempotency_key": "other-1",
        }
    ]
    assert payload["conversation"]["runtimeState"] == "queued"
    assert [event["type"] for event in payload["transcriptEvents"]][-6:] == [
        "user_message",
        "assistant_status",
        "requirement_form",
        "requirement_form_confirmed",
        "runtime_progress",
        "assistant_message",
    ]
    assert payload["transcriptEvents"][-6]["payload"] == {"text": "熟悉 LangGraph"}
    assert payload["transcriptEvents"][-5]["payload"] == {
        "phase": "requirement_amendment",
        "text": "正在根据补充要求更新需求，请稍候。",
    }
    form_events = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form"]
    assert len(form_events) == 2
    latest_form = form_events[-1]["payload"]
    must_have_texts = [item["text"] for item in latest_form["draft"]["sections"][0]["items"]]
    assert "熟悉 LangGraph" not in must_have_texts
    assert "抽取后：熟悉 LangGraph" in must_have_texts
    assert "抽取后：熟悉 LangGraph" in latest_form["requirementSheet"]["must_have_capabilities"]
    assert latest_form["runtimeInput"] == runtime_input
    assert runtime.start_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "runtime_input": WorkbenchV2RuntimeInput.model_validate(runtime_input),
            "requirement_sheet": RequirementSheet.model_validate(latest_form["requirementSheet"]),
            "idempotency_key": "other-1",
            "draft_revision_id": latest_form["draft"]["draft_revision_id"],
            "selected_item_ids": [item["item_id"] for item in latest_form["draft"]["sections"][0]["items"]],
            "deselected_item_ids": [],
        }
    ]
    confirmed = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form_confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0]["payload"]["requirementSheet"] == latest_form["requirementSheet"]
    assert confirmed[0]["payload"]["readonly"] is True
    assert view.requirementForm == confirmed[0]["payload"]
    assert payload["runtime"] == {"state": "queued", "runtimeRunId": "rtrun_1"}


def test_requirement_action_add_other_runs_amendment_outside_route_event_loop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    runtime = AsyncioRunRequirementRuntime()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-add-threaded"))

    view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="add_other",
            text="熟悉 Claude、Cursor、Codex",
            idempotency_key="add-other-threaded",
        )
    )

    assert runtime.amend_requirement_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "base_draft_revision_id": first_view.requirementForm["draft"]["draft_revision_id"],
            "base_requirement_sheet": RequirementSheet.model_validate(first_view.requirementForm["requirementSheet"]),
            "text": "熟悉 Claude、Cursor、Codex",
            "idempotency_key": "add-other-threaded",
        }
    ]
    assert view.requirementForm is not None
    assert view.requirementForm["draft"]["draft_revision_id"] == "reqdraft_amended_1"
    assert view.transcriptEvents[-1].type == "requirement_form"
    assert not any(event.type == "error" for event in view.transcriptEvents)


def test_requirement_action_confirm_with_supplement_runs_amendment_outside_route_event_loop(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    runtime = AsyncioRunRequirementRuntime()
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=runtime)
    first_view = asyncio.run(
        service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-confirm-threaded")
    )

    view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="confirm",
            text="熟悉 Claude、Cursor、Codex",
            idempotency_key="confirm-threaded",
        )
    )

    assert runtime.amend_requirement_calls == [
        {
            "conversation_id": first_view.conversation.conversationId,
            "base_draft_revision_id": first_view.requirementForm["draft"]["draft_revision_id"],
            "base_requirement_sheet": RequirementSheet.model_validate(first_view.requirementForm["requirementSheet"]),
            "text": "熟悉 Claude、Cursor、Codex",
            "idempotency_key": "confirm-threaded",
        }
    ]
    assert "抽取后：熟悉 Claude、Cursor、Codex" in runtime.start_calls[0]["requirement_sheet"].must_have_capabilities
    assert view.conversation.runtimeState == "queued"
    assert [event.type for event in view.transcriptEvents][-4:] == [
        "requirement_form",
        "requirement_form_confirmed",
        "runtime_progress",
        "assistant_message",
    ]
    assert not any(event.type == "error" for event in view.transcriptEvents)


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
    first_view = asyncio.run(
        service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-action-confirm")
    )
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


def test_requirement_action_idempotency_conflicts_on_different_selected(tmp_path: Path) -> None:
    service, conversation_id, item_id = _service_with_requirement_form(tmp_path)
    asyncio.run(
        service.apply_requirement_action(
            conversation_id,
            action="set_selected",
            item_id=item_id,
            selected=False,
            idempotency_key="action-conflict",
        )
    )

    with pytest.raises(ValueError, match="workbench_v2_idempotency_conflict"):
        asyncio.run(
            service.apply_requirement_action(
                conversation_id,
                action="set_selected",
                item_id=item_id,
                selected=True,
                idempotency_key="action-conflict",
            )
        )


def test_agent_update_requirements_patch_updates_current_form(tmp_path: Path) -> None:
    service, conversation_id, item_id = _service_with_requirement_form(tmp_path)
    service.agent_loop.outputs.append(
        _agent_output(
            intent="update_requirements",
            message="我已更新当前需求。",
            requirementPatch={
                "selectedItemIds": [],
                "deselectedItemIds": [item_id],
                "otherNotes": "熟悉 LangGraph",
            },
        )
    )

    view = asyncio.run(service.submit_message(conversation_id, "去掉 Python，补充 LangGraph", idempotency_key="patch"))

    assert view.requirementForm is not None
    must_have_items = view.requirementForm["draft"]["sections"][0]["items"]
    existing_item = next(item for item in must_have_items if item["item_id"] == item_id)
    added_item = next(item for item in must_have_items if item["text"] == "抽取后：熟悉 LangGraph")
    assert existing_item["selected"] is False
    assert added_item["selected"] is True
    assert view.requirementForm["requirementSheet"]["must_have_capabilities"] == ["抽取后：熟悉 LangGraph"]
    assert [event.type for event in view.transcriptEvents][-3:] == [
        "assistant_status",
        "requirement_form",
        "assistant_message",
    ]
    assert view.transcriptEvents[-1].type == "assistant_message"
    assert view.transcriptEvents[-1].payload == {"text": "我已更新当前需求。"}


def test_agent_update_requirements_patch_replay_after_form_append_does_not_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, conversation_id, item_id = _service_with_requirement_form(tmp_path)
    patch = {
        "selectedItemIds": [],
        "deselectedItemIds": [item_id],
        "otherNotes": "熟悉 LangGraph",
    }
    service.agent_loop.outputs.append(
        _agent_output(intent="update_requirements", message="我已更新当前需求。", requirementPatch=patch)
    )
    original_append_assistant = service._append_assistant_message

    def crash_after_form(*args: object, **kwargs: object) -> object:
        raise RuntimeError("assistant append failed")

    monkeypatch.setattr(service, "_append_assistant_message", crash_after_form)
    with pytest.raises(RuntimeError, match="assistant append failed"):
        asyncio.run(service.submit_message(conversation_id, "去掉 Python，补充 LangGraph", idempotency_key="patch"))

    failed_view = service.get_conversation(conversation_id)
    assert len([event for event in failed_view.transcriptEvents if event.type == "requirement_form"]) == 2

    monkeypatch.setattr(service, "_append_assistant_message", original_append_assistant)
    service.agent_loop.outputs.append(
        _agent_output(intent="update_requirements", message="我已更新当前需求。", requirementPatch=patch)
    )

    view = asyncio.run(service.submit_message(conversation_id, "去掉 Python，补充 LangGraph", idempotency_key="patch"))

    form_events = [event for event in view.transcriptEvents if event.type == "requirement_form"]
    assert len(form_events) == 2
    assert view.transcriptEvents[-1].type == "assistant_message"
    assert view.transcriptEvents[-1].payload == {"text": "我已更新当前需求。"}


def test_agent_update_requirements_patch_replay_with_different_patch_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, conversation_id, item_id = _service_with_requirement_form(tmp_path)
    first_patch = {
        "selectedItemIds": [],
        "deselectedItemIds": [item_id],
        "otherNotes": None,
    }
    second_patch = {
        "selectedItemIds": [item_id],
        "deselectedItemIds": [],
        "otherNotes": None,
    }
    service.agent_loop.outputs.append(
        _agent_output(intent="update_requirements", message="我已更新当前需求。", requirementPatch=first_patch)
    )
    original_append_assistant = service._append_assistant_message
    monkeypatch.setattr(
        service,
        "_append_assistant_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("assistant append failed")),
    )
    with pytest.raises(RuntimeError, match="assistant append failed"):
        asyncio.run(service.submit_message(conversation_id, "调整需求", idempotency_key="patch-conflict"))

    monkeypatch.setattr(service, "_append_assistant_message", original_append_assistant)
    service.agent_loop.outputs.append(
        _agent_output(intent="update_requirements", message="我已更新另一版需求。", requirementPatch=second_patch)
    )

    with pytest.raises(ValueError, match="workbench_v2_idempotency_conflict"):
        asyncio.run(service.submit_message(conversation_id, "调整需求", idempotency_key="patch-conflict"))

    view = service.get_conversation(conversation_id)
    assert len([event for event in view.transcriptEvents if event.type == "requirement_form"]) == 2


def test_agent_update_requirements_without_form_appends_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    agent = FakeAgentLoop(
        _agent_output(
            intent="update_requirements",
            message="我会更新当前需求。",
            requirementPatch={
                "selectedItemIds": [],
                "deselectedItemIds": [],
                "otherNotes": "熟悉 LangGraph",
            },
        )
    )
    service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())

    view = asyncio.run(service.create_conversation("补充 LangGraph", idempotency_key="patch-no-form"))
    payload = view.model_dump(mode="json")

    assert [event["type"] for event in payload["transcriptEvents"]] == ["user_message", "error", "assistant_message"]
    assert payload["transcriptEvents"][-2]["payload"]["code"] == "workbench_v2_requirement_form_required"
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "当前没有可更新的需求表单，请先发送完整招聘需求。"}


def test_agent_update_requirements_after_confirm_records_next_round_requirement(tmp_path: Path) -> None:
    service, runtime, conversation_id, item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    before_view = service.get_conversation(conversation_id)
    before_form_count = len([event for event in before_view.transcriptEvents if event.type == "requirement_form"])
    service.agent_loop.outputs.append(
        _agent_output(
            intent="update_requirements",
            message="已记录补充要求，会在下一轮检索时使用。",
            requirementPatch={
                "selectedItemIds": [],
                "deselectedItemIds": [],
                "otherNotes": "候选人优先有天猫或淘宝业务经验。",
            },
        )
    )

    view = asyncio.run(
        service.submit_message(
            conversation_id,
            "补充一个额外需求：候选人优先有天猫或淘宝业务经验，下一轮检索请加入这个条件。",
            idempotency_key="patch-readonly",
        )
    )
    payload = view.model_dump(mode="json")

    assert len(runtime.start_calls) == 1
    assert len([event for event in view.transcriptEvents if event.type == "requirement_form"]) == before_form_count
    assert "error" not in [event["type"] for event in payload["transcriptEvents"][-3:]]
    assert payload["transcriptEvents"][-2]["type"] == "assistant_status"
    assert payload["transcriptEvents"][-2]["payload"] == {
        "amendmentId": "reqamend_1",
        "phase": "supplemental_requirement",
        "runtimeRunId": "rtrun_1",
        "runtimeSubmissionStatus": "pending_target_round",
        "targetRoundNo": 2,
        "text": "已记录补充要求，将在第 2 轮检索前生效。",
        "supplementalRequirement": "候选人优先有天猫或淘宝业务经验。",
    }
    assert runtime.next_round_requirement_calls[0]["runtime_run_id"] == "rtrun_1"
    assert runtime.next_round_requirement_calls[0]["text"] == "候选人优先有天猫或淘宝业务经验。"
    assert str(runtime.next_round_requirement_calls[0]["idempotency_key"]).startswith(
        "workbench-v2-service:submit:patch-readonly:runtime-next-round:"
    )
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "已记录补充要求，会在下一轮检索时使用。"}
    assert view.requirementForm is not None
    assert view.requirementForm["readonly"] is True


def test_agent_update_requirements_needs_review_overrides_assistant_message(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.next_round_requirement_results.append(
        {
            "amendmentId": "reqamend_review_1",
            "status": "needs_review",
            "targetRoundNo": 3,
            "effectiveBoundary": "before_round_controller",
            "approvedRequirementRevisionId": None,
            "reviewRequired": True,
        }
    )
    service.agent_loop.outputs.append(
        _agent_output(
            intent="update_requirements",
            message="已记录补充要求，会在下一轮检索时使用。",
            requirementPatch={
                "selectedItemIds": [],
                "deselectedItemIds": [],
                "otherNotes": "候选人必须有出海业务经验。",
            },
        )
    )

    view = asyncio.run(
        service.submit_message(
            conversation_id,
            "补充：候选人必须有出海业务经验。",
            idempotency_key="patch-needs-review",
        )
    )
    payload = view.model_dump(mode="json")

    assert payload["transcriptEvents"][-2]["payload"] == {
        "amendmentId": "reqamend_review_1",
        "phase": "supplemental_requirement",
        "runtimeRunId": "rtrun_1",
        "runtimeSubmissionStatus": "needs_review",
        "targetRoundNo": 3,
        "text": "补充要求已记录，需要复核后才能在后续检索轮次生效。",
        "supplementalRequirement": "候选人必须有出海业务经验。",
    }
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "补充要求已记录，需要复核后才能在后续检索轮次生效。"}


def test_agent_update_requirements_after_runtime_completed_records_next_run_note(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.next_round_requirement_errors.append(RuntimeControlError("runtime_command_conflict"))
    service.agent_loop.outputs.append(
        _agent_output(
            intent="update_requirements",
            message="已记录补充要求，会在下一轮检索时使用。",
            requirementPatch={
                "selectedItemIds": [],
                "deselectedItemIds": [],
                "otherNotes": "候选人必须杭州本地。",
            },
        )
    )

    view = asyncio.run(
        service.submit_message(
            conversation_id,
            "补充：候选人必须杭州本地。",
            idempotency_key="patch-after-complete",
        )
    )
    payload = view.model_dump(mode="json")

    assert payload["transcriptEvents"][-2]["payload"] == {
        "phase": "supplemental_requirement",
        "runtimeRunId": "rtrun_1",
        "runtimeSubmissionStatus": "not_applied",
        "reasonCode": "runtime_command_conflict",
        "text": "本次运行已结束，补充要求已记录为后续重新运行或下一次检索参考。",
        "supplementalRequirement": "候选人必须杭州本地。",
    }
    assert payload["transcriptEvents"][-1]["payload"] == {
        "text": "本次运行已结束，补充要求已记录为后续重新运行或下一次检索参考。"
    }


def test_agent_update_requirements_missing_draft_or_sheet_appends_error(tmp_path: Path) -> None:
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }

    for missing_field, expected_code, expected_text in [
        ("draft", "workbench_v2_requirement_draft_required", "需求表单缺少 draft，无法更新需求。"),
        (
            "requirementSheet",
            "workbench_v2_requirement_sheet_required",
            "需求表单缺少 requirementSheet，无法更新需求。",
        ),
    ]:
        store = _store(tmp_path / missing_field)
        conversation = store.create_conversation(first_user_text="招一个 AI 平台工程师", idempotency_key=missing_field)
        form_payload = {
            "runtimeInput": runtime_input,
            "draft": _draft_payload().model_dump(mode="json"),
            "requirementSheet": _requirement_sheet_payload().model_dump(mode="json"),
        }
        del form_payload[missing_field]
        store.append_event(
            conversation.id,
            WorkbenchV2TranscriptEventInput(type="requirement_form", role="assistant", payload=form_payload),
        )
        agent = FakeAgentLoop(
            _agent_output(
                intent="update_requirements",
                message="我会更新当前需求。",
                requirementPatch={
                    "selectedItemIds": [],
                    "deselectedItemIds": [],
                    "otherNotes": "熟悉 LangGraph",
                },
            )
        )
        service = WorkbenchV2Service(store=store, agent_loop=agent, runtime_service=FakeRuntimeService())

        view = asyncio.run(
            service.submit_message(conversation.id, "补充 LangGraph", idempotency_key=f"{missing_field}-patch")
        )
        payload = view.model_dump(mode="json")

        assert payload["transcriptEvents"][-2]["type"] == "error"
        assert payload["transcriptEvents"][-2]["payload"]["code"] == expected_code
        assert payload["transcriptEvents"][-1]["payload"] == {"text": expected_text}


def test_requirement_action_idempotency_conflicts_on_different_item_id(tmp_path: Path) -> None:
    service, conversation_id, item_id = _service_with_requirement_form(tmp_path)
    asyncio.run(
        service.apply_requirement_action(
            conversation_id,
            action="set_selected",
            item_id=item_id,
            selected=False,
            idempotency_key="action-conflict",
        )
    )

    with pytest.raises(ValueError, match="workbench_v2_idempotency_conflict"):
        asyncio.run(
            service.apply_requirement_action(
                conversation_id,
                action="set_selected",
                item_id="must_have_capabilities_missing",
                selected=False,
                idempotency_key="action-conflict",
            )
        )


def test_requirement_action_idempotency_conflicts_on_different_text(tmp_path: Path) -> None:
    service, conversation_id, _item_id = _service_with_requirement_form(tmp_path)
    asyncio.run(
        service.apply_requirement_action(
            conversation_id,
            action="add_other",
            text="熟悉 LangGraph",
            idempotency_key="action-conflict",
        )
    )

    with pytest.raises(ValueError, match="workbench_v2_idempotency_conflict"):
        asyncio.run(
            service.apply_requirement_action(
                conversation_id,
                action="add_other",
                text="熟悉 LlamaIndex",
                idempotency_key="action-conflict",
            )
        )


def test_requirement_action_idempotency_conflicts_on_different_action(tmp_path: Path) -> None:
    service, conversation_id, item_id = _service_with_requirement_form(tmp_path)
    asyncio.run(
        service.apply_requirement_action(
            conversation_id,
            action="set_selected",
            item_id=item_id,
            selected=False,
            idempotency_key="action-conflict",
        )
    )

    with pytest.raises(ValueError, match="workbench_v2_idempotency_conflict"):
        asyncio.run(
            service.apply_requirement_action(
                conversation_id,
                action="add_other",
                text="熟悉 LangGraph",
                idempotency_key="action-conflict",
            )
        )


def test_requirement_action_set_and_add_after_confirm_are_rejected_and_keep_readonly_form(tmp_path: Path) -> None:
    service, runtime, conversation_id, item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    before_view = service.get_conversation(conversation_id)
    before_form_count = len([event for event in before_view.transcriptEvents if event.type == "requirement_form"])

    with pytest.raises(ValueError, match="workbench_v2_requirement_form_readonly"):
        asyncio.run(
            service.apply_requirement_action(
                conversation_id,
                action="set_selected",
                item_id=item_id,
                selected=True,
                idempotency_key="set-after-confirm",
            )
        )
    with pytest.raises(ValueError, match="workbench_v2_requirement_form_readonly"):
        asyncio.run(
            service.apply_requirement_action(
                conversation_id,
                action="add_other",
                text="熟悉 LangGraph",
                idempotency_key="add-after-confirm",
            )
        )

    view = service.get_conversation(conversation_id)
    assert len(runtime.start_calls) == 1
    assert len([event for event in view.transcriptEvents if event.type == "requirement_form"]) == before_form_count
    assert len([event for event in view.transcriptEvents if event.type == "requirement_form_confirmed"]) == 1
    assert view.requirementForm is not None
    assert view.requirementForm["readonly"] is True
    assert view.requirementForm["runtimeRunId"] == "rtrun_1"


def test_requirement_action_set_after_runtime_started_is_rejected_without_appending_form(tmp_path: Path) -> None:
    service, conversation_id, item_id = _service_with_requirement_form(tmp_path)
    service.store.set_runtime(conversation_id, runtime_run_id="rtrun_existing", runtime_state="running")
    before_view = service.get_conversation(conversation_id)
    before_form_count = len([event for event in before_view.transcriptEvents if event.type == "requirement_form"])

    with pytest.raises(ValueError, match="workbench_v2_requirement_form_readonly"):
        asyncio.run(
            service.apply_requirement_action(
                conversation_id,
                action="set_selected",
                item_id=item_id,
                selected=False,
                idempotency_key="set-after-runtime",
            )
        )

    view = service.get_conversation(conversation_id)
    assert len([event for event in view.transcriptEvents if event.type == "requirement_form"]) == before_form_count
    assert view.runtime is not None
    assert view.runtime.runtimeRunId == "rtrun_existing"


def test_requirement_action_repeated_confirm_after_confirm_returns_current_view_without_second_runtime_start(
    tmp_path: Path,
) -> None:
    service, runtime, conversation_id, _item_id, confirmed_view = _confirmed_requirement_conversation(tmp_path)

    second_key_view = asyncio.run(
        service.apply_requirement_action(
            conversation_id,
            action="confirm",
            idempotency_key="confirm-second-key",
        )
    )
    no_key_view = asyncio.run(service.apply_requirement_action(conversation_id, action="confirm"))

    assert len(runtime.start_calls) == 1
    assert len([event for event in no_key_view.transcriptEvents if event.type == "requirement_form_confirmed"]) == 1
    assert second_key_view.requirementForm == confirmed_view.requirementForm
    assert no_key_view.requirementForm == confirmed_view.requirementForm
    assert no_key_view.requirementForm is not None
    assert no_key_view.requirementForm["readonly"] is True


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


def test_agent_schema_rejects_start_runtime_intent(tmp_path: Path) -> None:
    del tmp_path
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": None,
    }
    with pytest.raises(Exception):
        _agent_output(intent="start_runtime", message="开始运行。", runtimeInput=runtime_input)


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
        "summary": "最终短名单已生成。" if status == "completed" else "招聘流程失败，请查看运行详情。",
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
        "summary": "最终短名单已生成。" if status == "completed" else "招聘流程失败，请查看运行详情。",
        "state": status,
    }


def test_runtime_status_question_uses_agent_intent_to_read_runtime_status(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "queued",
        "stage": "queued",
        "summary": "招聘流程已排队，等待开始。",
    }
    service.agent_loop.outputs.append(
        _agent_output(
            intent="get_runtime_status",
            message="当前招聘流程已排队等待开始，请稍后查看最新进度。",
        )
    )
    agent_call_count = len(service.agent_loop.calls)

    view = asyncio.run(service.submit_message(conversation_id, "现在进度如何？", idempotency_key="status-guard"))
    payload = view.model_dump(mode="json")

    assert [event["type"] for event in payload["transcriptEvents"][-2:]] == ["user_message", "assistant_message"]
    assert payload["transcriptEvents"][-2]["payload"] == {"text": "现在进度如何？"}
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "当前招聘流程已排队等待开始，请稍后查看最新进度。"}
    progress_events = [event for event in payload["transcriptEvents"] if event["type"] == "runtime_progress"]
    assert [
        {"state": event["payload"].get("state"), "summary": event["payload"].get("summary")}
        for event in progress_events
    ].count({"state": "queued", "summary": "招聘流程已排队，等待开始。"}) == 1
    assert len(service.agent_loop.calls) == agent_call_count + 1


def test_get_conversation_refreshes_active_runtime_status_without_duplicate_progress(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "running",
        "stage": "source_search",
        "summary": "正在检索候选人，进度 25%。",
    }

    first_refresh = service.get_conversation(conversation_id)
    second_refresh = service.get_conversation(conversation_id)

    assert first_refresh.conversation.runtimeState == "running"
    assert second_refresh.conversation.runtimeState == "running"
    progress_events = [event for event in second_refresh.transcriptEvents if event.type == "runtime_progress"]
    assert progress_events[-1].payload == {
        "runtimeRunId": "rtrun_1",
        "status": "running",
        "stage": "source_search",
        "summary": "招聘流程运行中，当前阶段：候选人检索。",
        "state": "running",
    }
    assert [event.payload for event in progress_events].count(progress_events[-1].payload) == 1

    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "stage": "completed",
        "summary": "本次运行完成，筛选出 2 位候选人。",
    }
    runtime.results_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "summary": "最终推荐 2 位候选人。",
        "facts": [{"label": "候选人", "value": "张三、李四"}],
    }

    completed_refresh = service.get_conversation(conversation_id)
    repeated_completed_refresh = service.get_conversation(conversation_id)

    assert repeated_completed_refresh.conversation.runtimeState == "completed"
    assert repeated_completed_refresh.runtime is not None
    assert repeated_completed_refresh.runtime.state == "completed"
    assert [event.type for event in completed_refresh.transcriptEvents[-3:]] == [
        "runtime_progress",
        "runtime_result",
        "assistant_message",
    ]
    assert completed_refresh.transcriptEvents[-3].payload == {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "stage": "completed",
        "summary": "招聘流程已完成。",
        "state": "completed",
    }
    assert completed_refresh.transcriptEvents[-2].payload == {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "summary": "招聘流程已完成，最终候选人列表已生成。",
        "facts": [{"label": "候选人", "value": "张三、李四"}],
        "state": "completed",
    }
    assert completed_refresh.transcriptEvents[-1].payload == {
        "text": "招聘流程已完成，最终候选人列表已生成。本次最终推荐：张三、李四。你可以在右侧查看候选人详情。"
    }
    result_events = [event for event in repeated_completed_refresh.transcriptEvents if event.type == "runtime_result"]
    assistant_events = [
        event for event in repeated_completed_refresh.transcriptEvents if event.type == "assistant_message"
    ]
    assert [event.payload for event in result_events].count(completed_refresh.transcriptEvents[-2].payload) == 1
    assert [event.payload for event in assistant_events].count(completed_refresh.transcriptEvents[-1].payload) == 1


def test_get_conversation_sanitizes_internal_terminal_runtime_summaries(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    service.store.append_event(
        conversation_id,
        WorkbenchV2TranscriptEventInput(
            type="runtime_progress",
            role="runtime",
            payload={
                "runtimeRunId": "rtrun_1",
                "runtimeEventSeq": 17,
                "runtimeEventType": "runtime_finalization_completed",
                "status": "completed",
                "stage": "finalization",
                "summary": "Selected 10 final candidates by deterministic runtime ranking.",
                "state": "completed",
            },
        ),
    )
    service.store.append_event(
        conversation_id,
        WorkbenchV2TranscriptEventInput(
            type="runtime_result",
            role="runtime",
            payload={
                "runtimeRunId": "rtrun_1",
                "status": "completed",
                "summary": "Run status: completed. completed",
                "state": "completed",
            },
        ),
    )
    service.store.append_event(
        conversation_id,
        WorkbenchV2TranscriptEventInput(
            type="runtime_progress",
            role="runtime",
            payload={
                "runtimeRunId": "rtrun_1",
                "status": "completed",
                "stage": "finalization",
                "summary": "finalization",
                "state": "completed",
            },
        ),
    )
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "stage": "completed",
        "summary": "Run completed after 7 retrieval rounds; controller stopped in round 8.",
    }
    runtime.results_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "summary": "Run status: completed. completed",
    }

    view = service.get_conversation(conversation_id)
    serialized = view.model_dump_json()

    assert "Selected 10 final candidates" not in serialized
    assert "Run status: completed" not in serialized
    assert "Run completed after" not in serialized
    terminal_events = [
        event
        for event in view.transcriptEvents
        if event.type in {"runtime_progress", "runtime_result"} and event.payload.get("state") == "completed"
    ]
    assert [event.payload.get("summary") for event in terminal_events][-2:] == [
        "最终短名单已生成。",
        "招聘流程已完成，最终候选人列表已生成。",
    ]
    terminal_summaries = [event.payload.get("summary") for event in terminal_events]
    assert terminal_summaries.count("最终短名单已生成。") == 1
    assert terminal_summaries.count("招聘流程已完成，最终候选人列表已生成。") == 1
    assert view.strategyGraph.nodes[-1].summary == "招聘流程已完成，最终候选人列表已生成。"


def test_get_conversation_sanitizes_legacy_raw_runtime_details_from_graph_and_thinking(
    tmp_path: Path,
) -> None:
    service, _runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    marker = "INTERNAL_PROVIDER_QUERY_SHOULD_NOT_RENDER"
    service.store.append_event(
        conversation_id,
        WorkbenchV2TranscriptEventInput(
            type="runtime_progress",
            role="runtime",
            payload={
                "runtimeRunId": "rtrun_1",
                "runtimeEventSeq": 11,
                "runtimeEventType": "runtime_round_query_ready",
                "status": "completed",
                "stage": "round_query",
                "roundNo": 1,
                "provider": "internal-provider-top-level",
                "rawPrompt": "top-level secret prompt text",
                "summary": "第 1 轮查询策略已生成。",
                "details": {
                    "keywordQuery": marker,
                    "queryTerms": [marker, "SQL"],
                    "provider": "internal-provider",
                    "rawPrompt": "secret prompt text",
                    "plannedQueries": [
                        {
                            "keywordQuery": marker,
                            "queryTerms": [marker],
                            "provider": "internal-provider",
                        }
                    ],
                },
                "state": "running",
            },
        ),
    )
    service.store.append_event(
        conversation_id,
        WorkbenchV2TranscriptEventInput(
            type="runtime_result",
            role="runtime",
            payload={
                "runtimeRunId": "rtrun_1",
                "status": "completed",
                "state": "completed",
                "summary": "招聘流程已完成，最终候选人列表已生成。",
                "facts": [{"label": "raw", "value": marker}],
                "details": {"rawPrompt": "result secret prompt text"},
                "provider": "internal-provider-result",
            },
        ),
    )

    view = service.get_conversation(conversation_id)
    serialized = view.model_dump_json()

    assert marker not in serialized
    assert "internal-provider" not in serialized
    assert "rawPrompt" not in serialized
    assert "secret prompt text" not in serialized


def test_get_conversation_preserves_legitimate_public_prompt_and_internal_terms(tmp_path: Path) -> None:
    service, _runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    service.store.append_event(
        conversation_id,
        WorkbenchV2TranscriptEventInput(
            type="runtime_progress",
            role="runtime",
            payload={
                "runtimeRunId": "rtrun_1",
                "runtimeEventSeq": 11,
                "runtimeEventType": "runtime_round_query_ready",
                "status": "completed",
                "stage": "round_query",
                "roundNo": 1,
                "summary": "第 1 轮查询策略已生成。",
                "details": {
                    "queryGroups": [
                        _v2_query_group(
                            query_instance_id="query-1",
                            term_group_key="group-1",
                            query_role="exploit",
                            lane_type="exploit",
                            query_terms=["Prompt Engineering", "internal platform"],
                            keyword_query="Prompt Engineer internal tools",
                        )
                    ],
                },
                "state": "running",
            },
        ),
    )
    service.store.append_event(
        conversation_id,
        WorkbenchV2TranscriptEventInput(
            type="runtime_progress",
            role="runtime",
            payload={
                "runtimeRunId": "rtrun_1",
                "runtimeEventSeq": 25,
                "runtimeEventType": "runtime_round_feedback_completed",
                "status": "completed",
                "stage": "feedback",
                "roundNo": 1,
                "summary": "第 1 轮复盘完成。",
                "details": {
                    "resumeQualityComment": "Prompt history mentions internal tools.",
                    "reflectionSummary": "Keep internal platform prompt terms.",
                },
                "state": "running",
            },
        ),
    )

    view = service.get_conversation(conversation_id)
    serialized = view.model_dump_json()

    assert "Prompt Engineer internal tools" in serialized
    assert "Prompt Engineering" in serialized
    assert "internal platform" in serialized
    assert "Prompt history mentions internal tools." in serialized
    assert "Keep internal platform prompt terms." in serialized


def test_get_conversation_does_not_duplicate_same_visible_queued_progress(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "queued",
        "stage": "queued",
        "summary": "招聘流程已排队，等待开始。",
    }

    refreshed = service.get_conversation(conversation_id)

    progress_events = [event for event in refreshed.transcriptEvents if event.type == "runtime_progress"]
    visible_payloads = [
        {
            "state": event.payload.get("state"),
            "summary": event.payload.get("summary"),
        }
        for event in progress_events
    ]
    assert visible_payloads.count({"state": "queued", "summary": "招聘流程已排队，等待开始。"}) == 1


def test_get_conversation_does_not_duplicate_canonical_runtime_failure_as_status_snapshot(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    raw_summary = "招聘流程失败：猎聘浏览器桥扩展未连接，请确认扩展已连接后重试。"
    runtime.progress_payloads["rtrun_1"] = [
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 17,
            "runtimeEventType": "runtime_run_failed",
            "status": "completed",
            "stage": "source_lanes",
            "state": "completed",
            "summary": raw_summary,
        },
    ]
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "failed",
        "stage": "runtime",
        "summary": raw_summary,
    }

    first_refresh = service.get_conversation(conversation_id)
    second_refresh = service.get_conversation(conversation_id)

    assert first_refresh.conversation.runtimeState == "failed"
    assert second_refresh.conversation.runtimeState == "failed"
    progress_summaries = [
        event.payload.get("summary") for event in second_refresh.transcriptEvents if event.type == "runtime_progress"
    ]
    assert progress_summaries.count("招聘流程失败，请查看运行详情。") == 1
    assert raw_summary not in progress_summaries


def test_get_conversation_ignores_raw_terminal_summary_changes(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "failed",
        "stage": "runtime",
        "summary": "招聘流程失败，请查看运行详情。",
    }

    failed_refresh = service.get_conversation(conversation_id)
    assert failed_refresh.conversation.runtimeState == "failed"

    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "failed",
        "stage": "source",
        "summary": "招聘流程失败：source_browser_backend_unavailable",
    }

    detailed_refresh = service.get_conversation(conversation_id)

    progress_events = [event for event in detailed_refresh.transcriptEvents if event.type == "runtime_progress"]
    assert progress_events[-1].payload == {
        "runtimeRunId": "rtrun_1",
        "status": "failed",
        "stage": "runtime",
        "summary": "招聘流程失败，请查看运行详情。",
        "state": "failed",
    }


def test_get_conversation_runtime_status_failure_appends_error_without_duplicate_spam(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.status_errors.extend([RuntimeError("status backend unavailable"), RuntimeError("still down")])

    service.get_conversation(conversation_id)
    second_refresh = service.get_conversation(conversation_id)

    assert second_refresh.conversation.runtimeState == "queued"
    error_events = [event for event in second_refresh.transcriptEvents if event.type == "error"]
    assistant_messages = [event for event in second_refresh.transcriptEvents if event.type == "assistant_message"]
    assert [event.payload for event in error_events].count(
        {
            "code": "workbench_v2_runtime_status_unavailable",
            "message": "暂时无法读取运行状态，请稍后重试。",
        }
    ) == 1
    assert [event.payload for event in assistant_messages].count({"text": "暂时无法读取运行状态，请稍后重试。"}) == 1


def test_runtime_status_failure_appends_error_in_transcript_instead_of_raising(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.status_errors.append(RuntimeError("status backend unavailable"))
    service.agent_loop.outputs.append(
        _agent_output(intent="get_runtime_status", message="暂时无法读取运行状态，请稍后重试。")
    )

    view = asyncio.run(service.submit_message(conversation_id, "现在进度如何？", idempotency_key="status-fail"))
    payload = view.model_dump(mode="json")

    assert [event["type"] for event in payload["transcriptEvents"][-3:]] == [
        "user_message",
        "error",
        "assistant_message",
    ]
    assert payload["transcriptEvents"][-2]["payload"] == {
        "code": "workbench_v2_runtime_status_unavailable",
        "message": "暂时无法读取运行状态，请稍后重试。",
    }
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "暂时无法读取运行状态，请稍后重试。"}


def test_runtime_summary_question_uses_agent_intent_and_reads_runtime_results(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.results_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "state": "completed",
        "summary": "招聘流程已完成，最终候选人列表已生成。",
        "facts": [{"label": "Candidate", "value": "张三：匹配 Python 和 Agent 经验"}],
    }
    service.agent_loop.outputs.append(_agent_output(intent="get_runtime_results", message="我读取了运行结果。"))
    agent_call_count = len(service.agent_loop.calls)

    view = asyncio.run(
        service.submit_message(conversation_id, "请总结这次 run 的结果。", idempotency_key="summary-results")
    )
    payload = view.model_dump(mode="json")

    assert [event["type"] for event in payload["transcriptEvents"][-3:]] == [
        "user_message",
        "runtime_result",
        "assistant_message",
    ]
    assert payload["transcriptEvents"][-2]["payload"] == {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "state": "completed",
        "summary": "招聘流程已完成，最终候选人列表已生成。",
        "facts": [{"label": "Candidate", "value": "张三：匹配 Python 和 Agent 经验"}],
    }
    assert payload["transcriptEvents"][-1]["payload"] == {
        "text": "招聘流程已完成，最终候选人列表已生成。本次最终推荐：张三：匹配 Python 和 Agent 经验。你可以在右侧查看候选人详情。"
    }
    assert len(service.agent_loop.calls) == agent_call_count + 1


def test_get_runtime_results_does_not_duplicate_existing_completed_result(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.status_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "stage": "completed",
        "summary": "招聘流程已完成。",
    }
    runtime.results_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "summary": "最终推荐 2 位候选人。",
    }
    service.get_conversation(conversation_id)
    service.agent_loop.outputs.append(_agent_output(intent="get_runtime_results", message="我读取了运行结果。"))

    view = asyncio.run(service.submit_message(conversation_id, "总结结果", idempotency_key="summary-after-refresh"))
    stored_record = service.store.get_conversation(conversation_id)

    assert [event.type for event in view.transcriptEvents[-2:]] == ["user_message", "assistant_message"]
    assert view.transcriptEvents[-1].payload == {"text": "招聘流程已完成，最终候选人列表已生成。"}
    assert len([event for event in stored_record.events if event.type == "runtime_result"]) == 1
    assert len([event for event in view.transcriptEvents if event.type == "runtime_result"]) == 1


def test_runtime_summary_question_reports_no_results_when_queued(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.results_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "queued",
        "state": "queued",
        "stage": "queued",
        "summary": "招聘流程已排队，等待开始。",
    }
    service.agent_loop.outputs.append(_agent_output(intent="get_runtime_results", message="我读取了运行结果。"))
    agent_call_count = len(service.agent_loop.calls)

    view = asyncio.run(
        service.submit_message(conversation_id, "请总结这次 run 的结果。", idempotency_key="summary-guard")
    )
    payload = view.model_dump(mode="json")

    assert [event["type"] for event in payload["transcriptEvents"][-2:]] == [
        "user_message",
        "assistant_message",
    ]
    assert payload["transcriptEvents"][-1]["payload"] == {
        "text": "当前招聘流程尚未完成，还没有最终结果可供总结。请稍后再查询最新进度。"
    }
    assert len(service.agent_loop.calls) == agent_call_count + 1


def test_get_runtime_results_intent_appends_runtime_result(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, _confirmed_view = _confirmed_requirement_conversation(tmp_path)
    runtime.results_payloads["rtrun_1"] = {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "summary": "本次运行完成，筛选出 1 位候选人。",
    }
    service.agent_loop.outputs.append(_agent_output(intent="get_runtime_results", message="我读取了运行结果。"))

    view = asyncio.run(service.submit_message(conversation_id, "候选人详情怎么样？", idempotency_key="results-intent"))
    payload = view.model_dump(mode="json")

    assert [event["type"] for event in payload["transcriptEvents"][-3:]] == [
        "user_message",
        "runtime_result",
        "assistant_message",
    ]
    assert payload["transcriptEvents"][-2]["payload"]["summary"] == "招聘流程已完成，最终候选人列表已生成。"
    assert payload["transcriptEvents"][-2]["payload"]["state"] == "completed"
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "招聘流程已完成，最终候选人列表已生成。"}


def test_agent_runtime_input_after_confirm_records_next_round_requirement_without_new_form(tmp_path: Path) -> None:
    service, runtime, conversation_id, _item_id, confirmed_view = _confirmed_requirement_conversation(tmp_path)
    confirmed_form_count = len([event for event in confirmed_view.transcriptEvents if event.type == "requirement_form"])
    supplemental_runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "补充：候选人优先有天猫或淘宝业务经验。",
        "notes": "下一轮生效",
    }
    service.agent_loop.outputs.append(
        _agent_output(
            intent="update_requirements",
            message="已记录补充要求，将在下一轮检索时使用。",
            runtimeInput=supplemental_runtime_input,
        )
    )

    view = asyncio.run(
        service.submit_message(
            conversation_id,
            "补充：候选人优先有天猫或淘宝业务经验。",
            idempotency_key="post-confirm-runtime-input",
        )
    )
    payload = view.model_dump(mode="json")

    forms = [event for event in payload["transcriptEvents"] if event["type"] == "requirement_form"]
    assert len(forms) == confirmed_form_count
    assert runtime.extract_calls == [
        {
            "conversation_id": conversation_id,
            "runtime_input": WorkbenchV2RuntimeInput(
                jobTitle="AI 平台工程师",
                jd="负责 Agent 工作流和 Python 后端。",
                notes="杭州",
            ),
        }
    ]
    assert payload["transcriptEvents"][-2]["type"] == "assistant_status"
    assert payload["transcriptEvents"][-2]["payload"] == {
        "amendmentId": "reqamend_1",
        "phase": "supplemental_requirement",
        "runtimeRunId": "rtrun_1",
        "runtimeSubmissionStatus": "pending_target_round",
        "targetRoundNo": 2,
        "text": "已记录补充要求，将在第 2 轮检索前生效。",
        "supplementalRequirement": "职位名称：AI 平台工程师；补充 JD：补充：候选人优先有天猫或淘宝业务经验。；补充说明：下一轮生效",
    }
    assert runtime.next_round_requirement_calls[0]["runtime_run_id"] == "rtrun_1"
    assert (
        runtime.next_round_requirement_calls[0]["text"]
        == "职位名称：AI 平台工程师；补充 JD：补充：候选人优先有天猫或淘宝业务经验。；补充说明：下一轮生效"
    )
    assert str(runtime.next_round_requirement_calls[0]["idempotency_key"]).startswith(
        "workbench-v2-service:submit:post-confirm-runtime-input:runtime-next-round:"
    )
    assert payload["transcriptEvents"][-1]["payload"] == {"text": "已记录补充要求，将在下一轮检索时使用。"}


def test_v2_thinking_process_merges_safe_query_groups_and_rejects_identity_drift() -> None:
    from seektalent_workbench_v2.models import WorkbenchV2Conversation, WorkbenchV2ConversationRecord
    from seektalent_workbench_v2.views import conversation_record_to_view

    primary = {
        "queryInstanceId": "query-primary",
        "termGroupKey": "group-primary",
        "queryRole": "exploit",
        "laneType": "exploit",
        "queryTerms": ["AI agent", "Python"],
        "keywordQuery": "AI agent Python",
        "lifecycle": "planned",
        "executionStatus": None,
        "attempted": False,
        "rawCandidateCount": 0,
        "uniqueCandidateCount": 0,
        "duplicateCandidateCount": 0,
        "executions": [],
    }
    explore = {
        "queryInstanceId": "query-explore",
        "termGroupKey": "group-explore",
        "queryRole": "explore",
        "laneType": "generic_explore",
        "queryTerms": ["AI platform", "Rust"],
        "keywordQuery": "AI platform Rust",
        "lifecycle": "planned",
        "executionStatus": None,
        "attempted": False,
        "rawCandidateCount": 0,
        "uniqueCandidateCount": 0,
        "duplicateCandidateCount": 0,
        "executions": [],
    }
    executed_primary = {
        **primary,
        "lifecycle": "executed",
        "executionStatus": "completed",
        "attempted": True,
        "rawCandidateCount": 6,
        "uniqueCandidateCount": 3,
        "duplicateCandidateCount": 3,
        "executions": [
            {
                "sourceKind": "cts",
                "status": "completed",
                "rawCandidateCount": 4,
                "uniqueCandidateCount": 2,
                "duplicateCandidateCount": 2,
            },
            {
                "sourceKind": "liepin",
                "status": "completed",
                "rawCandidateCount": 2,
                "uniqueCandidateCount": 1,
                "duplicateCandidateCount": 1,
            },
        ],
    }
    conversation = WorkbenchV2Conversation(
        id="conversation-1",
        title="Find AI platform engineers",
        created_at="2026-07-11T00:00:00Z",
        updated_at="2026-07-11T00:00:00Z",
        runtime_run_id="runtime-1",
        runtime_state="running",
    )
    planned_event = WorkbenchV2TranscriptEvent(
        id="event-planned",
        conversation_id=conversation.id,
        step=1,
        type="runtime_progress",
        role="runtime",
        payload={
            "runtimeRunId": "runtime-1",
            "runtimeEventSeq": 1,
            "runtimeEventType": "runtime_round_query_ready",
            "status": "completed",
            "stage": "round_query",
            "roundNo": 2,
            "summary": "Round two query groups.",
            "details": {"queryGroups": [primary, explore]},
            "state": "running",
        },
        created_at="2026-07-11T00:00:01Z",
    )
    feedback_event = WorkbenchV2TranscriptEvent(
        id="event-feedback",
        conversation_id=conversation.id,
        step=2,
        type="runtime_progress",
        role="runtime",
        payload={
            "runtimeRunId": "runtime-1",
            "runtimeEventSeq": 2,
            "runtimeEventType": "runtime_round_feedback_completed",
            "status": "completed",
            "stage": "feedback",
            "roundNo": 2,
            "summary": "Round two feedback.",
            "details": {"queryGroups": [executed_primary]},
            "state": "running",
        },
        created_at="2026-07-11T00:00:02Z",
    )

    view = conversation_record_to_view(
        WorkbenchV2ConversationRecord(conversation=conversation, events=[planned_event, feedback_event])
    )

    [round_view] = view.thinkingProcess.rounds
    assert [group.queryInstanceId for group in round_view.queryGroups] == ["query-primary", "query-explore"]
    assert round_view.queryGroups[0].lifecycle == "executed"
    assert round_view.queryGroups[0].uniqueCandidateCount == 3
    assert [execution.sourceKind for execution in round_view.queryGroups[0].executions] == ["cts", "liepin"]
    assert round_view.cards == []

    drift_event = feedback_event.model_copy(
        update={
            "payload": {
                **feedback_event.payload,
                "details": {"queryGroups": [{**executed_primary, "termGroupKey": "changed"}]},
            }
        }
    )
    with pytest.raises(ValueError, match="workbench_v2_query_group_identity_mismatch"):
        conversation_record_to_view(
            WorkbenchV2ConversationRecord(conversation=conversation, events=[planned_event, drift_event])
        )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("queryInstanceId", {"providerUrl": "https://provider.example/private/raw-identity"}),
        ("termGroupKey", ["https://provider.example/private/raw-identity"]),
        ("queryRole", {"rawIdentity": "https://provider.example/private/raw-identity"}),
        ("laneType", ["raw-identity", "https://provider.example/private"]),
        ("keywordQuery", {"providerUrl": "https://provider.example/private/raw-identity"}),
        ("lifecycle", ["planned", "https://provider.example/private/raw-identity"]),
    ],
)
def test_v2_runtime_display_drops_non_string_query_group_scalars(field: str, bad_value: object) -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    secret = "https://provider.example/private/raw-identity"
    group = _v2_query_group(
        query_instance_id="query-1",
        term_group_key="group-1",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["safe term"],
        keyword_query="safe term",
    )
    group[field] = bad_value

    payload = normalize_runtime_progress_payload(
        {
            "stage": "round_query",
            "details": {"queryGroups": [group]},
        }
    )

    assert "details" not in payload
    assert secret not in repr(payload)


@pytest.mark.parametrize(
    "field",
    ["queryInstanceId", "termGroupKey", "queryRole", "laneType", "keywordQuery"],
)
def test_v2_runtime_display_drops_sensitive_required_query_text(field: str) -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    secret = "https://provider.example/private/raw-identity"
    group = _v2_query_group(
        query_instance_id="query-1",
        term_group_key="group-1",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["safe term"],
        keyword_query="safe term",
    )
    group[field] = secret

    payload = normalize_runtime_progress_payload(
        {
            "stage": "round_query",
            "details": {"queryGroups": [group]},
        }
    )

    assert "details" not in payload
    assert secret not in repr(payload)


def test_v2_runtime_display_drops_non_string_execution_status() -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    secret = "https://provider.example/private/raw-identity"
    group = _v2_query_group(
        query_instance_id="query-1",
        term_group_key="group-1",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["safe term"],
        keyword_query="safe term",
        lifecycle="executed",
        execution_status="completed",
        attempted=True,
    )
    group["executionStatus"] = {"providerUrl": secret}

    payload = normalize_runtime_progress_payload(
        {
            "stage": "feedback",
            "details": {"queryGroups": [group]},
        }
    )

    assert "details" not in payload
    assert secret not in repr(payload)


def test_v2_runtime_display_scrubs_non_string_query_terms_and_execution_scalars() -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    secret = "https://provider.example/private/raw-identity"
    group = _v2_query_group(
        query_instance_id="query-1",
        term_group_key="group-1",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["safe term"],
        keyword_query="safe term",
        lifecycle="executed",
        execution_status="completed",
        attempted=True,
        executions=[
            {
                "sourceKind": "cts",
                "status": "completed",
                "safeReasonCode": {"providerUrl": secret},
            },
            {
                "sourceKind": {"providerUrl": secret},
                "status": "completed",
            },
            {
                "sourceKind": "liepin",
                "status": ["completed", secret],
            },
            {
                "sourceKind": secret,
                "status": "completed",
            },
        ],
    )
    group["queryTerms"] = [
        "safe term",
        {"providerUrl": secret},
        ["rawIdentity", secret],
        7,
        True,
        secret,
        "Authorization=Bearer private-token",
    ]

    payload = normalize_runtime_progress_payload(
        {
            "stage": "feedback",
            "details": {"queryGroups": [group]},
        }
    )

    [sanitized] = payload["details"]["queryGroups"]
    assert sanitized["queryTerms"] == ["safe term"]
    assert sanitized["executions"] == [
        {
            "sourceKind": "cts",
            "status": "completed",
            "rawCandidateCount": 0,
            "uniqueCandidateCount": 0,
            "duplicateCandidateCount": 0,
        }
    ]
    assert secret not in repr(payload)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "note: Authorization: Bearer private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_v2_runtime_display_drops_shared_unsafe_query_text(unsafe_text: str) -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    group = _v2_query_group(
        query_instance_id="query-1",
        term_group_key="group-1",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["safe term", unsafe_text],
        keyword_query="safe term",
        lifecycle="executed",
        execution_status="completed",
        attempted=True,
    )
    payload = normalize_runtime_progress_payload(
        {
            "stage": "feedback",
            "details": {"queryGroups": [group]},
        }
    )

    [sanitized] = payload["details"]["queryGroups"]
    assert sanitized["queryTerms"] == ["safe term"]
    assert unsafe_text not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "note: Authorization: Bearer private-token",
        "debug secret=private-token",
        "api-key=private-token",
        "api key=private-token",
        "API Key: private-token",
        "X-API-Key: private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_v2_runtime_display_drops_shared_unsafe_text_from_all_public_query_fields(unsafe_text: str) -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    group = _v2_query_group(
        query_instance_id="query-1",
        term_group_key="group-1",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["safe term", unsafe_text],
        keyword_query="safe term",
        lifecycle="executed",
        execution_status="completed",
        attempted=True,
        executions=[
            {"sourceKind": "cts", "status": "completed"},
            {"sourceKind": unsafe_text, "status": "completed"},
        ],
    )
    unsafe_keyword_group = _v2_query_group(
        query_instance_id="query-unsafe-keyword",
        term_group_key="group-unsafe-keyword",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["safe term"],
        keyword_query=unsafe_text,
        lifecycle="executed",
        execution_status="completed",
        attempted=True,
    )

    payload = normalize_runtime_progress_payload(
        {
            "stage": "feedback",
            "sourceKind": "internal_referrals",
            "details": {
                "queryGroups": [group, unsafe_keyword_group],
                "resumeQualityComment": unsafe_text,
                "reflectionSummary": unsafe_text,
                "suggestedActivateTerms": ["safe detail", unsafe_text],
            },
        }
    )

    [sanitized] = payload["details"]["queryGroups"]
    assert sanitized["queryInstanceId"] == "query-1"
    assert sanitized["termGroupKey"] == "group-1"
    assert sanitized["queryTerms"] == ["safe term"]
    assert sanitized["keywordQuery"] == "safe term"
    assert sanitized["executions"] == [
        {
            "sourceKind": "cts",
            "status": "completed",
            "rawCandidateCount": 0,
            "uniqueCandidateCount": 0,
            "duplicateCandidateCount": 0,
        }
    ]
    assert payload["sourceKind"] == "internal_referrals"
    assert payload["details"] == {
        "queryGroups": [sanitized],
        "suggestedActivateTerms": ["safe detail"],
    }
    assert unsafe_text not in json.dumps(payload, ensure_ascii=False)


def test_v2_runtime_display_drops_non_scalar_or_sensitive_detail_text() -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    provider_url = "https://provider.example/private/raw-identity"
    private_token = "private-token"
    payload = normalize_runtime_progress_payload(
        {
            "stage": "feedback",
            "details": {
                "resumeQualityComment": "Safe quality note.",
                "reflectionSummary": {"opaque": provider_url},
                "suggestedStopReason": f"Authorization=Bearer {private_token}",
                "suggestedActivateTerms": [
                    "safe term",
                    {"opaque": provider_url},
                    ["nested", provider_url],
                    7,
                    True,
                    provider_url,
                    f"Authorization=Bearer {private_token}",
                ],
            },
        }
    )

    assert payload["details"] == {
        "resumeQualityComment": "Safe quality note.",
        "suggestedActivateTerms": ["safe term"],
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert provider_url not in serialized
    assert private_token not in serialized


@pytest.mark.parametrize(
    "source_kind",
    [
        "https://provider.example/private/raw-identity",
        "note: Authorization: Bearer private-token",
        "debug secret=private-token",
        "api-key=private-token",
        "X-API-Key: private-token",
        "OpenCLI CDP target 98b37a browser session failed",
        "INTERNAL_PROVIDER_REFERENCE",
    ],
)
def test_v2_runtime_display_drops_unsafe_source_identifiers(source_kind: str) -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    payload = normalize_runtime_progress_payload(
        {
            "stage": "source_result",
            "sourceId": source_kind,
            "sourceKind": source_kind,
        }
    )

    assert "sourceId" not in payload
    assert "sourceKind" not in payload
    assert source_kind not in json.dumps(payload, ensure_ascii=False)


def test_v2_runtime_display_drops_unsafe_top_level_progress_values() -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    provider_url = "https://provider.example/private/raw-identity"
    private_token = "private-token"
    payload = normalize_runtime_progress_payload(
        {
            "runtimeRunId": provider_url,
            "runtimeEventSeq": {"opaque": provider_url},
            "runtimeEventType": f"Bearer {private_token}",
            "status": f"Bearer {private_token}",
            "stage": "source_result",
            "summary": provider_url,
            "state": f"Bearer {private_token}",
            "roundNo": [provider_url],
            "sourceId": provider_url,
            "sourceKind": provider_url,
        }
    )

    assert payload == {
        "stage": "source_result",
        "summary": "本轮来源检索结果已更新。",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert provider_url not in serialized
    assert private_token not in serialized


def test_v2_runtime_display_drops_unsafe_top_level_result_values() -> None:
    from seektalent_workbench_v2.runtime_display import PENDING_RESULT_SUMMARY, normalize_runtime_result_payload

    provider_url = "https://provider.example/private/raw-identity"
    private_token = "private-token"
    payload = normalize_runtime_result_payload(
        {
            "runtimeRunId": provider_url,
            "status": f"Bearer {private_token}",
            "state": provider_url,
            "summary": provider_url,
        }
    )

    assert payload == {"summary": PENDING_RESULT_SUMMARY}
    serialized = json.dumps(payload, ensure_ascii=False)
    assert provider_url not in serialized
    assert private_token not in serialized


def test_v2_runtime_display_uses_canonical_progress_summary_for_safe_looking_runtime_failure() -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    raw_summary = "OpenCLI CDP target 98b37a browser session failed"
    payload = normalize_runtime_progress_payload(
        {
            "runtimeEventType": "runtime_round_source_result",
            "status": "blocked",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": "liepin",
            "summary": raw_summary,
        }
    )

    assert payload["summary"] == "第 1 轮猎聘检索受阻：猎聘检索受阻，请稍后重试。"
    assert raw_summary not in json.dumps(payload, ensure_ascii=False)


def test_v2_runtime_display_drops_safe_looking_unknown_event_and_stage_values() -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    raw_summary = "OpenCLI CDP target 98b37a browser session failed"
    payload = normalize_runtime_progress_payload(
        {
            "runtimeEventType": "OpenCLI_internal_phase",
            "status": "running",
            "stage": "OpenCLI_internal_phase",
            "summary": raw_summary,
        }
    )

    assert payload == {
        "status": "running",
        "summary": "招聘流程运行中。",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "OpenCLI_internal_phase" not in serialized
    assert raw_summary not in serialized


def test_v2_runtime_display_uses_state_summary_instead_of_arbitrary_result_summary() -> None:
    from seektalent_workbench_v2.runtime_display import FAILED_RESULT_SUMMARY, normalize_runtime_result_payload

    raw_summary = "OpenCLI CDP target 98b37a browser session failed"
    payload = normalize_runtime_result_payload(
        {
            "status": "failed",
            "summary": raw_summary,
        }
    )

    assert payload == {
        "status": "failed",
        "summary": FAILED_RESULT_SUMMARY,
    }
    assert raw_summary not in json.dumps(payload, ensure_ascii=False)


def test_v2_runtime_display_uses_canonical_reason_for_search_failure() -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    raw_summary = "OpenCLI CDP target 98b37a browser session failed"
    payload = normalize_runtime_progress_payload(
        {
            "runtimeEventType": "runtime_search_failed",
            "status": "failed",
            "stage": "search",
            "roundNo": 1,
            "summary": raw_summary,
        }
    )

    assert payload["summary"] == "第 1 轮检索失败：检索失败，请稍后重试。"
    assert raw_summary not in json.dumps(payload, ensure_ascii=False)


def test_v2_runtime_display_preserves_safe_top_level_runtime_values() -> None:
    from seektalent_workbench_v2.runtime_display import (
        normalize_runtime_progress_payload,
        normalize_runtime_result_payload,
    )

    progress = normalize_runtime_progress_payload(
        {
            "runtimeRunId": "rtrun_1",
            "runtimeEventSeq": 1,
            "runtimeEventType": "runtime_round_source_result",
            "status": "blocked",
            "stage": "source_result",
            "summary": "本轮来源检索受阻。",
            "state": "running",
            "roundNo": 1,
            "sourceId": "internal_referrals",
            "sourceKind": "internal_referrals",
        }
    )
    result = normalize_runtime_result_payload(
        {
            "runtimeRunId": "rtrun_1",
            "status": "completed",
            "state": "completed",
            "summary": "最终候选人已生成。",
        }
    )

    assert progress == {
        "runtimeRunId": "rtrun_1",
        "runtimeEventSeq": 1,
        "runtimeEventType": "runtime_round_source_result",
        "status": "blocked",
        "stage": "source_result",
        "summary": "第 1 轮来源检索受阻：来源检索受阻，请稍后重试。",
        "state": "running",
        "roundNo": 1,
        "sourceId": "internal_referrals",
        "sourceKind": "internal_referrals",
    }
    assert result == {
        "runtimeRunId": "rtrun_1",
        "status": "completed",
        "state": "completed",
        "summary": "招聘流程已完成，最终候选人列表已生成。",
    }


@pytest.mark.parametrize(
    ("stage", "lifecycle"),
    [
        ("round_query", "executed"),
        ("feedback", "planned"),
        ("source_result", "planned"),
    ],
)
def test_v2_runtime_display_requires_query_group_stage_lifecycle(stage: str, lifecycle: str) -> None:
    from seektalent_workbench_v2.runtime_display import normalize_runtime_progress_payload

    payload = normalize_runtime_progress_payload(
        {
            "stage": stage,
            "details": {
                "queryGroups": [
                    _v2_query_group(
                        query_instance_id="query-1",
                        term_group_key="group-1",
                        query_role="exploit",
                        lane_type="exploit",
                        query_terms=["safe term"],
                        keyword_query="safe term",
                        lifecycle=lifecycle,
                        execution_status="completed" if lifecycle == "executed" else None,
                        attempted=lifecycle == "executed",
                    )
                ]
            },
        }
    )

    assert "details" not in payload


def _v2_query_group(
    *,
    query_instance_id: str,
    term_group_key: str,
    query_role: str,
    lane_type: str,
    query_terms: list[str],
    keyword_query: str,
    lifecycle: str = "planned",
    execution_status: str | None = None,
    attempted: bool = False,
    raw_candidate_count: int = 0,
    unique_candidate_count: int = 0,
    duplicate_candidate_count: int = 0,
    executions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "queryInstanceId": query_instance_id,
        "termGroupKey": term_group_key,
        "queryRole": query_role,
        "laneType": lane_type,
        "queryTerms": query_terms,
        "keywordQuery": keyword_query,
        "lifecycle": lifecycle,
        "executionStatus": execution_status,
        "attempted": attempted,
        "rawCandidateCount": raw_candidate_count,
        "uniqueCandidateCount": unique_candidate_count,
        "duplicateCandidateCount": duplicate_candidate_count,
        "executions": executions or [],
    }


def _store(tmp_path: Path) -> WorkbenchV2Store:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    return store


def _service_with_requirement_form(tmp_path: Path) -> tuple[WorkbenchV2Service, str, str]:
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    service = WorkbenchV2Service(
        store=_store(tmp_path),
        agent_loop=agent,
        runtime_service=FakeRuntimeService(),
    )
    view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-action-form"))
    assert view.requirementForm is not None
    item_id = view.requirementForm["draft"]["sections"][0]["items"][0]["item_id"]
    return service, view.conversation.conversationId, item_id


def _confirmed_requirement_conversation(
    tmp_path: Path,
) -> tuple[WorkbenchV2Service, FakeRuntimeService, str, str, WorkbenchV2ConversationView]:
    runtime_input = {
        "jobTitle": "AI 平台工程师",
        "jd": "负责 Agent 工作流和 Python 后端。",
        "notes": "杭州",
    }
    agent = FakeAgentLoop(
        _agent_output(intent="extract_requirements", message="我已整理需求，请确认表单。", runtimeInput=runtime_input),
    )
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(
        store=_store(tmp_path),
        agent_loop=agent,
        runtime_service=runtime,
    )
    first_view = asyncio.run(service.create_conversation("招一个 AI 平台工程师", idempotency_key="create-confirmed"))
    assert first_view.requirementForm is not None
    item_id = first_view.requirementForm["draft"]["sections"][0]["items"][0]["item_id"]
    confirmed_view = asyncio.run(
        service.apply_requirement_action(
            first_view.conversation.conversationId,
            action="confirm",
            idempotency_key="confirm-in-helper",
        )
    )
    return service, runtime, first_view.conversation.conversationId, item_id, confirmed_view


def _agent_output(
    *,
    intent: str,
    message: str,
    needsClarification: bool = False,
    clarifyingQuestion: str | None = None,
    runtimeInput: dict[str, object] | None = None,
    requirementPatch: dict[str, object] | None = None,
    memoryRead: dict[str, object] | None = None,
) -> WorkbenchV2AgentOutput:
    return WorkbenchV2AgentOutput.model_validate(
        {
            "intent": intent,
            "message": message,
            "needsClarification": needsClarification,
            "clarifyingQuestion": clarifyingQuestion,
            "runtimeInput": runtimeInput,
            "requirementPatch": requirementPatch,
            "memoryRead": memoryRead,
            "memoryWrite": None,
        }
    )


def _draft_payload(
    *,
    draft_revision_id: str = "reqdraft_1",
    base_revision_id: str | None = None,
    must_have_capabilities: list[str] | None = None,
) -> RequirementDraft:
    resolved_must_have_capabilities = must_have_capabilities or ["Python 后端开发"]
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
        draft_revision_id=draft_revision_id,
        base_revision_id=base_revision_id,
        status="draft_ready",
        sections=[
            RequirementDraftSection(
                section_id="must_have_capabilities",
                display_name="必须满足",
                backend_field="must_have_capabilities",
                items=[
                    RequirementDraftItem(
                        item_id=f"must_have_capabilities_{index}",
                        selected=True,
                        enabled=True,
                        editable=True,
                        text=capability,
                        value=capability,
                        source="workbench_v2_agent",
                        status="resolved",
                        review_item_id=None,
                        amendment_id=None,
                        source_span_refs=[],
                        sort_order=index * 10,
                        allowed_actions=[],
                    )
                    for index, capability in enumerate(resolved_must_have_capabilities, start=1)
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
