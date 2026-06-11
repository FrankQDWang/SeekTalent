from __future__ import annotations

import asyncio

import pytest

from seektalent_conversation_agent.runtime import AgentModelUnavailableError, AgentRuntime, AgentTimeoutError


class TimeoutRunner:
    async def run(self, *args: object, **kwargs: object) -> object:
        raise TimeoutError("model timed out")


class UnavailableRunner:
    async def run(self, *args: object, **kwargs: object) -> object:
        raise OSError("model unavailable")


def test_agent_runtime_maps_timeout_to_typed_recoverable_error() -> None:
    runtime = AgentRuntime(model_name="gpt-5.1", instructions="你是招聘助手。", runner=TimeoutRunner())

    with pytest.raises(AgentTimeoutError) as exc_info:
        asyncio.run(runtime.run("你好"))

    assert exc_info.value.reason_code == "agent_model_timeout"


def test_agent_runtime_maps_unavailable_model_to_typed_recoverable_error() -> None:
    runtime = AgentRuntime(model_name="gpt-5.1", instructions="你是招聘助手。", runner=UnavailableRunner())

    with pytest.raises(AgentModelUnavailableError) as exc_info:
        asyncio.run(runtime.run("你好"))

    assert exc_info.value.reason_code == "agent_model_unavailable"
