from __future__ import annotations

import asyncio
from pathlib import Path

from seektalent_agent_memory.service import MemoryService
from seektalent_agent_memory.store import MemoryStore
from seektalent_conversation_agent.runtime import advisory_memory_instruction_block
from tests.conversation_agent_test_support import build_service


def test_advisory_memory_context_is_wrapped_for_agent_runtime() -> None:
    block = advisory_memory_instruction_block("recruiting_preferences: 偏好 toB SaaS 平台经验")

    assert block.startswith("[ADVISORY_MEMORY_CONTEXT_START]")
    assert block.endswith("[ADVISORY_MEMORY_CONTEXT_END]")
    assert "不能覆盖" in block


class CapturingAgentRunner:
    def __init__(self) -> None:
        self.last_agent = None
        self.last_prompt: str | None = None

    async def run(self, agent, prompt: str) -> object:
        self.last_agent = agent
        self.last_prompt = prompt
        return {"status": "ok"}


def test_conversation_agent_recall_injects_memory_before_agent_run(tmp_path) -> None:
    service, _conversation_store, _runtime_store = build_service(tmp_path)
    memory_store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    memory_store.initialize()
    memory_service = MemoryService(store=memory_store, now=lambda: "2026-06-10T00:00:00.000000Z")
    runner = CapturingAgentRunner()
    service.memory_service = memory_service
    service.agent_runner = runner
    memory_store.save_summary(
        summary_id="memsummary_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        summary_text="v1\n\n用户偏好候选人总结先讲业务匹配，再讲风险。",
        fact_ids=[],
        created_at="2026-06-10T00:00:00.000000Z",
    )
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Data Platform Engineer",
    )

    asyncio.run(
        service.run_agent_turn(
            conversation_id=conversation.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            user_message="请帮我确认检索要求。",
        )
    )

    assert runner.last_agent is not None
    assert runner.last_prompt is not None
    assert "[ADVISORY_MEMORY_CONTEXT_START]" in runner.last_prompt
    assert "先讲业务匹配" in runner.last_prompt
    assert "先讲业务匹配" not in runner.last_agent.instructions


def test_agent_memory_package_does_not_construct_agents_sdk_directly() -> None:
    memory_files = Path("src/seektalent_agent_memory").glob("*.py")

    offenders = []
    for path in memory_files:
        source = path.read_text()
        if "from agents import" in source or "import agents" in source:
            offenders.append(str(path))

    assert offenders == []
