from __future__ import annotations

import asyncio
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
)
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
