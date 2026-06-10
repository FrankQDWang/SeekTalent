from __future__ import annotations

from pathlib import Path


def test_memory_recall_filters_scope_expiry_and_records_usage(tmp_path: Path) -> None:
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
    fact = service.accept_candidate(
        candidate_id=candidate.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        accepted_text=None,
    )

    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_2",
        turn_id="turn_1",
    )

    assert recalled.fact_ids == [fact.fact_id]
    assert "偏好 toB SaaS" in recalled.context_text
    assert store.list_usage(conversation_id="agent_conv_2")[0].fact_ids == [fact.fact_id]


def test_memory_recall_uses_active_summary_before_raw_facts(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")
    store.save_summary(
        summary_id="memsummary_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        summary_text="v1\n\n用户偏好候选人总结先讲业务匹配，再讲风险。",
        fact_ids=[],
        created_at="2026-06-10T00:00:00.000000Z",
    )

    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_2",
        turn_id="turn_1",
    )

    assert recalled.summary_id == "memsummary_1"
    assert "MEMORY_SUMMARY" not in recalled.context_text
    assert "先讲业务匹配" in recalled.context_text


def test_memory_recall_does_not_use_active_summary_when_referenced_fact_expired(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    now_value = {"value": "2026-06-09T00:00:00.000000Z"}
    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: now_value["value"])
    service.update_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        memory_enabled=True,
        generation_enabled=True,
        recall_enabled=True,
        review_required=False,
        candidate_retention_days=1,
    )
    candidate = service.create_candidate(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        category="summary_style",
        text="候选人总结先讲业务匹配，再讲风险。",
        source_message_ids=["m1"],
    )
    fact = service.accept_candidate(
        candidate_id=candidate.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        accepted_text=None,
    )
    store.save_summary(
        summary_id="memsummary_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        summary_text="v1\n\n候选人总结先讲业务匹配，再讲风险。",
        fact_ids=[fact.fact_id],
        created_at="2026-06-09T00:00:01.000000Z",
    )

    now_value["value"] = "2026-06-11T00:00:00.000000Z"
    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_2",
        turn_id="turn_1",
    )

    assert recalled.context_text == ""
    assert recalled.fact_ids == []
    assert recalled.summary_id is None
