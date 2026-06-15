from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchGapStreamPayloadResponse,
    AgentWorkbenchStreamEnvelopeResponse,
    AgentWorkbenchStreamKind,
    AgentWorkbenchStreamPayloadResponse,
    AgentWorkbenchTranscriptPayloadResponse,
    normalize_agent_workbench_stream_payload,
)


def build_stream_envelope(
    *,
    conversation_id: str,
    seq: int,
    kind: AgentWorkbenchStreamKind,
    payload: AgentWorkbenchStreamPayloadResponse | AgentWorkbenchTranscriptPayloadResponse,
    created_at: str,
) -> AgentWorkbenchStreamEnvelopeResponse:
    if seq <= 0:
        raise ValueError("stream seq must be positive")
    return AgentWorkbenchStreamEnvelopeResponse(
        conversationId=conversation_id,
        seq=seq,
        kind=kind,
        payload=normalize_agent_workbench_stream_payload(payload, kind),
        createdAt=created_at,
    )


def encode_sse_event(envelope: AgentWorkbenchStreamEnvelopeResponse) -> dict[str, str]:
    return {
        "id": str(envelope.seq),
        "event": "agent_workbench_event",
        "data": envelope.model_dump_json(exclude_none=True),
    }


def replay_stream_envelopes(
    stream_store,
    *,
    conversation_id: str,
    after_seq: int,
) -> Iterable[AgentWorkbenchStreamEnvelopeResponse]:
    first_seq = stream_store.first_seq(conversation_id=conversation_id)
    if first_seq is not None and after_seq + 1 < first_seq:
        yield _gap_event(
            conversation_id=conversation_id,
            seq=first_seq - 1,
            missing_from_seq=after_seq + 1,
            next_available_seq=first_seq,
        )
        after_seq = first_seq - 1
    expected_seq = after_seq + 1
    for envelope in stream_store.replay_stream_envelopes(conversation_id=conversation_id, after_seq=after_seq):
        if envelope.seq != expected_seq:
            yield _gap_event(
                conversation_id=conversation_id,
                seq=envelope.seq - 1,
                missing_from_seq=expected_seq,
                next_available_seq=envelope.seq,
            )
        expected_seq = envelope.seq + 1
        yield envelope


def _gap_event(
    *,
    conversation_id: str,
    seq: int,
    missing_from_seq: int,
    next_available_seq: int,
) -> AgentWorkbenchStreamEnvelopeResponse:
    return AgentWorkbenchStreamEnvelopeResponse(
        conversationId=conversation_id,
        seq=max(0, seq),
        kind="stream.gap",
        payload=AgentWorkbenchGapStreamPayloadResponse(
            payloadType="stream.gap",
            summary="Stream replay gap detected.",
            missingFromSeq=missing_from_seq,
            nextAvailableSeq=next_available_seq,
        ),
        createdAt=datetime.now(UTC).isoformat(),
    )
