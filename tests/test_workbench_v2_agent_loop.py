from __future__ import annotations

import pytest
from pydantic import ValidationError

from seektalent_workbench_v2.agent_loop import WorkbenchV2AgentOutput, WorkbenchV2RuntimeInput


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
