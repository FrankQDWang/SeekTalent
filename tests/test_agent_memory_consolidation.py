from __future__ import annotations

from pathlib import Path


def test_memory_consolidation_builds_summary_from_active_facts(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-09T00:00:00.000000Z")
    candidate = service.create_candidate(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        category="recruiting_preferences",
        text="偏好 toB SaaS 平台经验",
        source_message_ids=["agent_msg_1"],
    )
    service.accept_candidate(
        candidate_id=candidate.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        accepted_text=None,
    )

    summary = service.consolidate(owner_user_id="user_1", workspace_id="workspace_1")

    assert summary.summary_text == "recruiting_preferences: 偏好 toB SaaS 平台经验"
