from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from seektalent_agent_memory.extraction import Stage1CandidateOutput, Stage1ModelOutput
from seektalent_agent_memory.privacy import MemoryPrivacyError
from seektalent_agent_memory.service import MemoryService
from seektalent_agent_memory.store import MemoryStore
from seektalent_agent_memory.transcript import MemoryTranscriptItem


def test_recall_is_advisory_and_persists_usage_with_summary_and_fact_ids(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(
        store=store,
        now=lambda: "2026-06-10T00:00:00.000000Z",
        usage_id_factory=lambda: "memusage_1",
    )
    candidate = service.create_candidate(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_source",
        category="summary_style",
        text="候选人总结先讲业务匹配，再讲风险。",
        source_message_ids=["agent_msg_1"],
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
        source_stage1_conversation_ids=["agent_conv_source"],
        created_at="2026-06-10T00:00:00.000000Z",
    )

    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_active",
        turn_id="agent_turn_1",
    )
    usage = store.list_usage(conversation_id="agent_conv_active")[0]

    assert recalled.summary_id == "memsummary_1"
    assert recalled.fact_ids == [fact.fact_id]
    assert "先讲业务匹配" in recalled.context_text
    assert usage.summary_id == "memsummary_1"
    assert usage.fact_ids == [fact.fact_id]
    assert usage.agent_turn_id == "agent_turn_1"


def test_stage1_model_input_excludes_raw_payloads_and_runtime_truth(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")
    extractor = CapturingStage1Extractor()

    asyncio.run(
        service.extract_stage1_from_items(
            extractor=extractor,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            source_updated_at="2026-06-10T00:00:00.000000Z",
            items=[
                MemoryTranscriptItem(
                    item_id="msg_raw_jd",
                    item_kind="message",
                    role="user",
                    text="JD原文：需要 Python API 和 Kafka",
                    payload={"jobTitle": "Python 平台负责人", "jdText": "完整 JD 不应进入 memory"},
                    created_at="2026-06-10T00:00:01.000000Z",
                ),
                MemoryTranscriptItem(
                    item_id="activity_runtime",
                    item_kind="activity",
                    role="tool",
                    text="CTS 返回 3 个候选人。",
                    payload={
                        "runtimeEvent": {"eventPayload": {"candidateScores": [91]}},
                        "providerResponse": {"cookie": "sessionid=abc"},
                    },
                    created_at="2026-06-10T00:00:02.000000Z",
                ),
                MemoryTranscriptItem(
                    item_id="msg_safe",
                    item_kind="message",
                    role="user",
                    text="以后候选人总结先讲业务匹配，再讲风险。",
                    created_at="2026-06-10T00:00:03.000000Z",
                ),
            ],
        )
    )

    assert extractor.serialized_transcript is not None
    assert "完整 JD" not in extractor.serialized_transcript
    assert "runtimeEvent" not in extractor.serialized_transcript
    assert "candidateScores" not in extractor.serialized_transcript
    assert "providerResponse" not in extractor.serialized_transcript
    assert "sessionid" not in extractor.serialized_transcript
    assert "先讲业务匹配" in extractor.serialized_transcript


def test_memory_rejects_runtime_commands_and_candidate_scores_as_facts(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")

    with pytest.raises(MemoryPrivacyError):
        service.create_candidate(
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            category="recruiting_preferences",
            text="请记住：以后自动启动检索。",
            source_message_ids=["agent_msg_1"],
        )

    with pytest.raises(MemoryPrivacyError):
        service.create_candidate(
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            category="recruiting_preferences",
            text="候选人张三 final_score 91，排名第一。",
            source_message_ids=["agent_msg_2"],
        )

    with pytest.raises(MemoryPrivacyError):
        service.create_candidate(
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            category="recruiting_preferences",
            text="运行 runtime command: start_search",
            source_message_ids=["agent_msg_3"],
        )


def test_stage1_review_required_keeps_candidates_pending_until_human_accepts(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")
    service.update_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        memory_enabled=True,
        generation_enabled=True,
        recall_enabled=True,
        review_required=True,
    )

    asyncio.run(
        service.extract_stage1_from_items(
            extractor=CandidateStage1Extractor(),
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            source_updated_at="2026-06-10T00:00:00.000000Z",
            items=[
                MemoryTranscriptItem(
                    item_id="msg_safe",
                    item_kind="message",
                    role="user",
                    text="以后候选人总结先讲业务匹配，再讲风险。",
                    created_at="2026-06-10T00:00:03.000000Z",
                )
            ],
        )
    )

    candidates = store.list_candidates(owner_user_id="user_1", workspace_id="workspace_1")
    facts = store.list_facts(owner_user_id="user_1", workspace_id="workspace_1")
    assert len(candidates) == 1
    assert candidates[0].status == "pending_review"
    assert candidates[0].reason_code == "agent_memory_review_required"
    assert facts == []


class CapturingStage1Extractor:
    def __init__(self) -> None:
        self.serialized_transcript: str | None = None

    async def extract(self, request):
        self.serialized_transcript = request.serialized_transcript
        return Stage1ModelOutput(raw_memory="", rollout_summary="", rollout_slug=None, candidates=[])


class CandidateStage1Extractor:
    async def extract(self, _request):
        return Stage1ModelOutput(
            raw_memory="用户偏好候选人总结先讲业务匹配，再讲风险。",
            rollout_summary="summary style preference",
            rollout_slug="summary-style",
            candidates=[
                Stage1CandidateOutput(
                    category="summary_style",
                    text="候选人总结先讲业务匹配，再讲风险。",
                    confidence=0.9,
                    evidence_message_ids=["msg_safe"],
                    evidence_activity_ids=[],
                )
            ],
        )
