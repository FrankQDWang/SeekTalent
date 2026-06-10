from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from seektalent_agent_memory.extraction import Stage1CandidateOutput, Stage1ModelOutput


def test_stage1_claim_is_lease_based_and_idempotent(tmp_path: Path) -> None:
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()

    claim = store.try_claim_stage1_job(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        worker_id="agent_conv_active",
        source_updated_at="2026-06-10T01:00:00.000000Z",
        now="2026-06-10T08:00:00.000000Z",
        lease_seconds=120,
        max_running_jobs=4,
    )

    assert claim.status == "claimed"
    assert claim.ownership_token

    running = store.try_claim_stage1_job(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        worker_id="agent_conv_other",
        source_updated_at="2026-06-10T01:00:00.000000Z",
        now="2026-06-10T08:01:00.000000Z",
        lease_seconds=120,
        max_running_jobs=4,
    )

    assert running.status == "skipped_running"


def test_stage1_claim_skips_when_output_is_up_to_date(tmp_path: Path) -> None:
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    store.save_stage1_output(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        source_updated_at="2026-06-10T01:00:00.000000Z",
        raw_memory="用户偏好候选人总结先讲业务匹配。",
        rollout_summary="summary style",
        rollout_slug="summary-style",
        generated_at="2026-06-10T08:00:00.000000Z",
        privacy_review_json={},
        source_message_ids=["m1"],
        source_activity_ids=[],
    )

    claim = store.try_claim_stage1_job(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        worker_id="worker_1",
        source_updated_at="2026-06-10T01:00:00.000000Z",
        now="2026-06-10T08:10:00.000000Z",
        lease_seconds=120,
        max_running_jobs=4,
    )

    assert claim.status == "skipped_up_to_date"


def test_stage1_claim_is_scoped_and_stale_token_cannot_complete(tmp_path: Path) -> None:
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    claim = store.try_claim_stage1_job(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        worker_id="worker_1",
        source_updated_at="2026-06-10T01:00:00.000000Z",
        now="2026-06-10T08:00:00.000000Z",
        lease_seconds=120,
        max_running_jobs=4,
    )
    scoped = store.try_claim_stage1_job(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_2",
        worker_id="worker_2",
        source_updated_at="2026-06-10T01:00:00.000000Z",
        now="2026-06-10T08:01:00.000000Z",
        lease_seconds=120,
        max_running_jobs=4,
    )
    reclaimed = store.try_claim_stage1_job(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        worker_id="worker_3",
        source_updated_at="2026-06-10T01:00:00.000000Z",
        now="2026-06-10T08:03:00.000000Z",
        lease_seconds=120,
        max_running_jobs=4,
    )

    assert scoped.status == "claimed"
    assert reclaimed.status == "claimed"
    assert claim.ownership_token is not None
    assert not store.mark_stage1_job_succeeded_no_output(
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        ownership_token=claim.ownership_token,
        source_updated_at="2026-06-10T01:00:00.000000Z",
        now="2026-06-10T08:04:00.000000Z",
    )


@dataclass(frozen=True)
class CompletedMemoryConversation:
    conversation_id: str
    updated_at: str


def completed_conversation(conversation_id: str, *, updated_at: str) -> CompletedMemoryConversation:
    return CompletedMemoryConversation(conversation_id=conversation_id, updated_at=updated_at)


class FakeConversationMemoryReader:
    def __init__(self, *, conversations: list[CompletedMemoryConversation]) -> None:
        self.conversations = conversations

    def eligible_completed_conversations(self, **_kwargs: object) -> list[CompletedMemoryConversation]:
        return self.conversations

    def read_memory_transcript_items(self, *, conversation_id: str) -> list[object]:
        return []


class FakeExtractor:
    async def extract(self, request):
        return Stage1ModelOutput(
            raw_memory="用户偏好候选人总结先讲业务匹配，再讲风险。",
            rollout_summary="summary style preference",
            rollout_slug="summary-style-preference",
            candidates=[
                Stage1CandidateOutput(
                    category="summary_style",
                    text="候选人总结先讲业务匹配，再讲风险。",
                    confidence=0.82,
                    evidence_message_ids=[],
                    evidence_activity_ids=[],
                )
            ],
        )


def test_phase1_pipeline_claims_bounded_eligible_conversations(tmp_path: Path) -> None:
    from seektalent_agent_memory.pipeline import MemoryPipeline
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    transcript_reader = FakeConversationMemoryReader(
        conversations=[
            completed_conversation("agent_conv_old", updated_at="2026-06-09T00:00:00.000000Z"),
            completed_conversation("agent_conv_active", updated_at="2026-06-10T07:59:00.000000Z"),
        ]
    )

    pipeline = MemoryPipeline(
        store=store,
        transcript_reader=transcript_reader,
        extractor=FakeExtractor(),
        now=lambda: "2026-06-10T08:00:00.000000Z",
    )

    result = asyncio.run(
        pipeline.run_phase1_startup(
            owner_user_id="user_1",
            workspace_id="workspace_1",
            current_conversation_id="agent_conv_active",
        )
    )

    assert result.claimed == 1
    assert result.succeeded_with_output == 1
    assert result.failed == 0
