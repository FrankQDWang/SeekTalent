from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents.run import get_output_schema
from pydantic import ValidationError

from seektalent_workbench_v2.agent_loop import (
    BailianStrictWorkbenchV2AgentLoop,
    WorkbenchV2AgentOutput,
    WorkbenchV2MemoryRead,
    WorkbenchV2MemoryWrite,
    WorkbenchV2RequirementPatch,
    WorkbenchV2RuntimeInput,
    _render_turn_prompt,
)
from seektalent_workbench_v2.models import WorkbenchV2TranscriptEvent
from tests.settings_factory import make_settings


class SchemaPreparingRunner:
    def __init__(self) -> None:
        self.output_schema = None

    async def run(self, agent, prompt: str) -> object:
        self.output_schema = get_output_schema(agent)
        return SimpleNamespace(
            final_output={
                "intent": "chat",
                "message": "已收到。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_agent_output_validates_pure_chat_without_runtime_input() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "chat",
            "message": "你好，我可以帮你处理招聘需求，也可以回答当前流程问题。",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": None,
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        }
    )

    assert output.intent == "chat"
    assert output.runtimeInput is None


def test_agent_output_requires_explicit_contract_fields() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate({"intent": "chat", "message": "hi"})


def test_agent_output_requires_runtime_input_key_even_when_null() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "chat",
                "message": "hi",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_runtime_input_requires_explicit_notes_key() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2RuntimeInput.model_validate({"jobTitle": "数据科学家", "jd": "JD"})


def test_requirement_patch_requires_explicit_keys() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2RequirementPatch.model_validate({"selectedItemIds": ["sql"]})


def test_agent_output_validates_recruitment_input() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "extract_requirements",
            "message": "我已识别到这是一个数据科学家招聘需求，先整理需求供你确认。",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": {
                "jobTitle": "数据科学家",
                "jd": "负责指标体系、A/B Testing、SQL 和 Python 分析。",
                "notes": "杭州，5 年以上经验。",
            },
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        }
    )

    assert output.runtimeInput == WorkbenchV2RuntimeInput(
        jobTitle="数据科学家",
        jd="负责指标体系、A/B Testing、SQL 和 Python 分析。",
        notes="杭州，5 年以上经验。",
    )


def test_render_turn_prompt_bounds_recent_context_and_payloads() -> None:
    events = [
        WorkbenchV2TranscriptEvent(
            id=f"event_{index}",
            conversation_id="conversation_1",
            step=index,
            created_at="2026-06-25T00:00:00.000000Z",
            type="user_message",
            role="user",
            payload={"text": f"event-payload-{index}-" + ("x" * 3000)},
            status="completed",
            parent_event_id=None,
            dedupe_key=None,
        )
        for index in range(25)
    ]

    prompt = _render_turn_prompt(
        conversation_id="conversation_1",
        context_summary="context-" + ("a" * 5000),
        recent_events=events,
        user_text="user-" + ("b" * 5000),
    )
    payload = json.loads(prompt.split("\n", 1)[1].rsplit("\n", 1)[0])

    assert len(payload["recentEvents"]) == 20
    assert payload["recentEvents"][0]["id"] == "event_5"
    assert payload["recentEvents"][-1]["id"] == "event_24"
    assert payload["contextSummary"].endswith("...[truncated]")
    assert payload["currentUserText"].endswith("...[truncated]")
    assert "payloadJson" in payload["recentEvents"][0]
    assert "payload" not in payload["recentEvents"][0]
    assert payload["recentEvents"][0]["payloadJson"].endswith("...[truncated]")
    assert len(payload["contextSummary"]) < 5000
    assert len(payload["currentUserText"]) < 5000
    assert len(payload["recentEvents"][0]["payloadJson"]) < 3000


def test_extract_requirements_without_runtime_input_fails_when_not_clarifying() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "extract_requirements",
                "message": "我已识别到招聘需求。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_chat_rejects_runtime_input_payload() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "chat",
                "message": "你好。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": {
                    "jobTitle": "数据科学家",
                    "jd": "负责数据分析。",
                    "notes": None,
                },
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_clarifying_question_is_rejected_when_not_clarifying() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "chat",
                "message": "你好。",
                "needsClarification": False,
                "clarifyingQuestion": "你想招聘什么岗位？",
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_get_runtime_status_rejects_requirement_patch_payload() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "get_runtime_status",
                "message": "我会查看进度。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": {
                    "selectedItemIds": ["sql"],
                    "deselectedItemIds": [],
                    "otherNotes": None,
                },
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_write_memory_rejects_runtime_input_payload() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "write_memory",
                "message": "我会记录这条记忆。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": {
                    "jobTitle": "数据科学家",
                    "jd": "负责数据分析。",
                    "notes": None,
                },
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": {
                    "source": "用户明确说明",
                    "content": "偏好 AI 平台经验。",
                },
            }
        )


def test_agents_sdk_prepares_strict_output_schema_without_network() -> None:
    runner = SchemaPreparingRunner()
    loop = BailianStrictWorkbenchV2AgentLoop(
        settings=make_settings(text_llm_api_key="test-key"),
        runner=runner,
    )

    output = asyncio.run(
        loop.run_turn(
            conversation_id="conversation_1",
            context_summary=None,
            recent_events=[],
            user_text="你好",
        )
    )

    assert output.intent == "chat"
    assert runner.output_schema is not None
    assert runner.output_schema.is_strict_json_schema() is True
    schema = runner.output_schema.json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["WorkbenchV2RequirementPatch"]["additionalProperties"] is False


def test_agent_output_strips_required_strings() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "write_memory",
            "message": "  已记录。  ",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": None,
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": {
                "source": "  用户明确说明  ",
                "content": "  偏好 AI 平台经验。  ",
            },
        }
    )

    assert output.message == "已记录。"
    assert output.memoryWrite == WorkbenchV2MemoryWrite(
        source="用户明确说明",
        content="偏好 AI 平台经验。",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "intent": "chat",
            "message": "   ",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": None,
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        },
        {"jobTitle": "   ", "jd": "负责数据分析。", "notes": None},
        {"jobTitle": "数据科学家", "jd": "   ", "notes": None},
        {"source": "   ", "content": "候选人偏好。"},
        {"source": "用户明确说明", "content": "   "},
        {"query": "   "},
    ],
)
def test_agent_contract_rejects_blank_required_strings(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        if "intent" in payload:
            WorkbenchV2AgentOutput.model_validate(payload)
        elif "jobTitle" in payload:
            WorkbenchV2RuntimeInput.model_validate(payload)
        elif "query" in payload:
            WorkbenchV2MemoryRead.model_validate(payload)
        else:
            WorkbenchV2MemoryWrite.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"selectedItemIds": ["sql", "sql"], "deselectedItemIds": [], "otherNotes": None},
        {"selectedItemIds": [], "deselectedItemIds": ["sql", "sql"], "otherNotes": None},
        {"selectedItemIds": [" sql "], "deselectedItemIds": ["sql"], "otherNotes": None},
        {"selectedItemIds": ["   "], "deselectedItemIds": [], "otherNotes": None},
    ],
)
def test_requirement_patch_rejects_duplicate_intersecting_or_blank_ids(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2RequirementPatch.model_validate(payload)


def test_start_runtime_intent_requires_runtime_input() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "start_runtime",
                "message": "开始运行。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_write_memory_requires_memory_write_payload() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "write_memory",
                "message": "我需要记录这条记忆。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_read_memory_requires_memory_read_payload() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "read_memory",
                "message": "我会查询相关记忆。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_update_requirements_requires_patch_or_runtime_input() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "update_requirements",
                "message": "我会更新需求。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_update_requirements_accepts_requirement_patch() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "update_requirements",
            "message": "我已更新当前需求。",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": None,
            "requirementPatch": {
                "selectedItemIds": ["sql"],
                "deselectedItemIds": [],
                "otherNotes": "  加强 SQL 要求。  ",
            },
            "memoryRead": None,
            "memoryWrite": None,
        }
    )

    assert output.requirementPatch == WorkbenchV2RequirementPatch(
        selectedItemIds=["sql"],
        deselectedItemIds=[],
        otherNotes="加强 SQL 要求。",
    )


def test_update_requirements_rejects_both_runtime_input_and_requirement_patch() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "update_requirements",
                "message": "我会更新当前需求。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": {
                    "jobTitle": "数据科学家",
                    "jd": "负责数据分析。",
                    "notes": None,
                },
                "requirementPatch": {
                    "selectedItemIds": ["sql"],
                    "deselectedItemIds": [],
                    "otherNotes": None,
                },
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_update_requirements_rejects_empty_requirement_patch() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "update_requirements",
                "message": "我会更新当前需求。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": {
                    "selectedItemIds": [],
                    "deselectedItemIds": [],
                    "otherNotes": "   ",
                },
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_update_requirements_accepts_runtime_input() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "update_requirements",
            "message": "我已根据补充说明更新当前需求。",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": {
                "jobTitle": "数据科学家",
                "jd": "负责指标体系和实验分析。",
                "notes": "  需要杭州候选人。  ",
            },
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        }
    )

    assert output.runtimeInput == WorkbenchV2RuntimeInput(
        jobTitle="数据科学家",
        jd="负责指标体系和实验分析。",
        notes="需要杭州候选人。",
    )


def test_confirm_requirements_does_not_require_runtime_input() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "confirm_requirements",
            "message": "已确认当前需求。",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": None,
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        }
    )

    assert output.intent == "confirm_requirements"
    assert output.runtimeInput is None


def test_clarification_rejects_action_payloads() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "extract_requirements",
                "message": "我需要确认岗位名称。",
                "needsClarification": True,
                "clarifyingQuestion": "岗位名称是什么？",
                "runtimeInput": {
                    "jobTitle": "数据科学家",
                    "jd": "负责数据分析。",
                    "notes": None,
                },
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_clarification_requires_question() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "extract_requirements",
                "message": "我需要确认岗位名称。",
                "needsClarification": True,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )
