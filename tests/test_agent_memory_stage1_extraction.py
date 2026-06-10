from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from seektalent_agent_memory.extraction import (
    ALLOWED_MEMORY_CATEGORIES,
    Stage1CandidateOutput,
    Stage1ModelOutput,
)
from seektalent_agent_memory.transcript import MemoryTranscriptItem


class FakeExtractor:
    async def extract(self, request):
        return Stage1ModelOutput(
            raw_memory="用户反复纠正：候选人总结先讲业务匹配，再讲风险。",
            rollout_summary="summary style preference from corrections",
            rollout_slug="summary-style-preference",
            candidates=[
                Stage1CandidateOutput(
                    category="summary_style",
                    text="候选人总结先讲业务匹配，再讲风险。",
                    confidence=0.83,
                    evidence_message_ids=["m2"],
                    evidence_activity_ids=[],
                )
            ],
        )


def test_stage1_extraction_does_not_require_explicit_memory_marker(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")

    output = asyncio.run(
        service.extract_stage1_from_items(
            extractor=FakeExtractor(),
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            source_updated_at="2026-06-10T00:00:00.000000Z",
            items=[
                MemoryTranscriptItem(
                    item_id="m1",
                    item_kind="message",
                    role="assistant",
                    text="我会先列风险。",
                    created_at="2026-06-10T00:00:00.000000Z",
                ),
                MemoryTranscriptItem(
                    item_id="m2",
                    item_kind="message",
                    role="user",
                    text="不对，总结先说业务匹配，再说风险。",
                    created_at="2026-06-10T00:01:00.000000Z",
                ),
            ],
        )
    )

    assert output.rollout_slug == "summary-style-preference"
    candidates = store.list_candidates(owner_user_id="user_1", workspace_id="workspace_1")
    facts = store.list_facts(owner_user_id="user_1", workspace_id="workspace_1")
    assert candidates[0].category == "summary_style"
    assert candidates[0].status == "accepted"
    assert candidates[0].reason_code == "agent_memory_policy_accepted"
    assert len(facts) == 1
    assert facts[0].source_candidate_id == candidates[0].candidate_id


def test_allowed_memory_categories_match_goal_pack_contract() -> None:
    assert ALLOWED_MEMORY_CATEGORIES == {
        "recruiting_preferences",
        "requirement_patterns",
        "user_corrections",
        "team_context",
        "summary_style",
        "terminology",
        "source_usage_preferences",
    }


def test_create_candidate_rejects_legacy_category_aliases(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")

    with pytest.raises(RuntimeError) as exc_info:
        service.create_candidate(
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            category="hiring_preference",
            text="偏好 toB SaaS 平台经验",
            source_message_ids=["m1"],
        )

    assert str(exc_info.value) == "agent_memory_category_invalid"
