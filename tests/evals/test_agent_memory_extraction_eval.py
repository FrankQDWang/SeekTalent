from __future__ import annotations

import asyncio

from tests.test_agent_memory_extraction import TranscriptReader
from seektalent_agent_memory.extraction import Stage1CandidateOutput, Stage1ModelOutput
from seektalent_agent_memory.transcript import MemoryTranscriptItem


def test_memory_extraction_eval_keeps_safe_preference_as_review_candidate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-09T00:00:00.000000Z")

    result = service.extract_candidates(
        transcript_reader=TranscriptReader(),
        conversation_id="agent_conv_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].category == "recruiting_preferences"


class CorrectionExtractor:
    async def extract(self, request):
        if "不对" not in request.serialized_transcript:
            return Stage1ModelOutput(raw_memory="", rollout_summary="", rollout_slug=None, candidates=[])
        return Stage1ModelOutput(
            raw_memory="用户纠正了候选人总结顺序。",
            rollout_summary="summary ordering preference",
            rollout_slug="summary-ordering",
            candidates=[
                Stage1CandidateOutput(
                    category="summary_style",
                    text="候选人总结先讲业务匹配，再讲风险。",
                    confidence=0.87,
                    evidence_message_ids=["m2"],
                    evidence_activity_ids=[],
                )
            ],
        )


def test_memory_extraction_eval_promotes_repeated_correction_without_marker(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")

    asyncio.run(
        service.extract_stage1_from_items(
            extractor=CorrectionExtractor(),
            owner_user_id="user_1",
            workspace_id="workspace_1",
            conversation_id="agent_conv_1",
            source_updated_at="2026-06-10T00:00:00.000000Z",
            items=[
                MemoryTranscriptItem(
                    item_id="m2",
                    item_kind="message",
                    role="user",
                    text="不对，候选人总结先讲业务匹配，再讲风险。",
                    created_at="2026-06-10T00:00:00.000000Z",
                )
            ],
        )
    )

    candidates = store.list_candidates(owner_user_id="user_1", workspace_id="workspace_1")
    assert candidates[0].category == "summary_style"
