from __future__ import annotations

from pathlib import Path


def test_agent_memory_summary_read_path_eval_uses_bounded_active_summary(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")
    store.save_summary(
        summary_id="memsummary_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        summary_text="v1\n\n用户偏好候选人总结先讲业务匹配，再讲风险。" * 20,
        fact_ids=[],
        created_at="2026-06-10T00:00:00.000000Z",
        token_estimate=20,
    )

    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_2",
        turn_id="turn_1",
    )

    assert recalled.summary_id == "memsummary_1"
    assert "先讲业务匹配" in recalled.context_text
    assert len(recalled.context_text) <= 5000
