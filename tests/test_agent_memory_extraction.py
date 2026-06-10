from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TranscriptLine:
    message_id: str
    role: str
    text: str


class TranscriptReader:
    def read_completed_transcript(self, *, conversation_id: str) -> list[TranscriptLine]:
        return [
            TranscriptLine("agent_msg_1", "user", "请记住：我偏好 toB SaaS 平台经验"),
            TranscriptLine("agent_msg_2", "assistant", "已记录为建议，后续仍需确认。"),
        ]


def test_memory_extraction_uses_transcript_reader_protocol_and_privacy_filter(tmp_path: Path) -> None:
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

    assert result.candidates[0].category == "recruiting_preferences"
    assert result.candidates[0].status == "accepted"
    assert result.candidates[0].reason_code == "agent_memory_policy_accepted"
    assert result.candidates[0].source_message_ids == ["agent_msg_1"]
    assert len(store.list_facts(owner_user_id="user_1", workspace_id="workspace_1")) == 1
