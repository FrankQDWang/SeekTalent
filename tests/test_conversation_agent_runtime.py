from __future__ import annotations

import importlib.util
from pathlib import Path
import tomllib

from tests.settings_factory import make_settings


def test_openai_agents_sdk_dependency_is_available() -> None:
    assert importlib.util.find_spec("agents") is not None


def test_conversation_agent_package_is_registered_for_build() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    modules = pyproject["tool"]["uv"]["build-backend"]["module-name"]

    assert "seektalent_conversation_agent" in modules


def test_agent_runtime_imports_agents_sdk_behind_runtime_boundary() -> None:
    from seektalent_conversation_agent.runtime import AgentRuntime

    runtime = AgentRuntime(model_name="gpt-5.1", instructions="你是招聘助手。")

    assert runtime.model_name == "gpt-5.1"
    assert runtime.instructions == "你是招聘助手。"


def test_agent_runtime_build_agent_has_no_tools_by_default() -> None:
    from seektalent_conversation_agent.runtime import AgentRuntime

    runtime = AgentRuntime(model_name="gpt-5.1", instructions="你是招聘助手。")
    agent = runtime.build_agent()

    assert agent.tools == []


def test_agent_budget_settings_have_fail_closed_defaults() -> None:
    settings = make_settings()

    assert settings.agent_turn_input_token_budget > 0
    assert settings.agent_turn_output_token_budget > 0
    assert settings.agent_conversation_token_budget >= settings.agent_turn_input_token_budget
    assert settings.agent_compaction_trigger_token_budget <= settings.agent_conversation_token_budget
    assert settings.agent_monthly_cost_budget_cents is None
    assert settings.agent_model_timeout_seconds > 0
    assert settings.agent_tool_timeout_seconds > 0
    assert settings.agent_stream_heartbeat_seconds > 0
