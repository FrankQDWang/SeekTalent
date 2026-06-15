from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchGapStreamPayloadResponse,
    AgentWorkbenchStreamKind,
    AgentWorkbenchStreamPayloadResponse,
    AgentWorkbenchTranscriptPayloadResponse,
    normalize_agent_workbench_stream_payload,
)
from seektalent_ui.agent_workbench_stream import build_stream_envelope


_STREAM_PAYLOAD_ADAPTER = TypeAdapter(AgentWorkbenchStreamPayloadResponse)


class AgentWorkbenchStreamStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.initialize()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_workbench_stream_events (
                    conversation_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_seq INTEGER,
                    idempotency_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, seq),
                    UNIQUE(conversation_id, event_id),
                    UNIQUE(conversation_id, idempotency_key)
                );

                CREATE INDEX IF NOT EXISTS idx_agent_workbench_stream_events_replay
                    ON agent_workbench_stream_events(conversation_id, seq);

                CREATE INDEX IF NOT EXISTS idx_agent_workbench_stream_events_source
                    ON agent_workbench_stream_events(conversation_id, source_kind, source_id, source_seq);
                """
            )

    def append_event(
        self,
        *,
        conversation_id: str,
        kind: AgentWorkbenchStreamKind,
        payload: AgentWorkbenchStreamPayloadResponse | AgentWorkbenchTranscriptPayloadResponse,
        source_fact_key: str,
        created_at: str,
        source_kind: str | None = None,
        source_id: str | None = None,
        source_seq: int | None = None,
        idempotency_key: str | None = None,
    ):
        stream_payload = normalize_agent_workbench_stream_payload(payload, kind)
        source_ref = _source_ref(
            source_fact_key=source_fact_key,
            source_kind=source_kind,
            source_id=source_id,
            source_seq=source_seq,
            idempotency_key=idempotency_key,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT seq, kind, payload_json, created_at
                FROM agent_workbench_stream_events
                WHERE conversation_id = ? AND idempotency_key = ?
                """,
                (conversation_id, source_ref.idempotency_key),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return build_stream_envelope(
                    conversation_id=conversation_id,
                    seq=int(existing["seq"]),
                    kind=_stream_kind(existing["kind"]),
                    payload=_stream_payload(existing["payload_json"], _stream_kind(existing["kind"])),
                    created_at=existing["created_at"],
                )
            row = conn.execute(
                """
                SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
                FROM agent_workbench_stream_events
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            seq = int(row["next_seq"])
            conn.execute(
                """
                INSERT INTO agent_workbench_stream_events (
                    conversation_id, seq, event_id, kind, payload_json,
                    source_kind, source_id, source_seq, idempotency_key, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    seq,
                    source_ref.event_id,
                    kind,
                    stream_payload.model_dump_json(exclude_none=True),
                    source_ref.source_kind,
                    source_ref.source_id,
                    source_ref.source_seq,
                    source_ref.idempotency_key,
                    created_at,
                ),
            )
            conn.commit()
        return build_stream_envelope(
            conversation_id=conversation_id,
            seq=seq,
            kind=kind,
            payload=stream_payload,
            created_at=created_at,
        )

    def replay_stream_envelopes(self, *, conversation_id: str, after_seq: int, limit: int = 100):
        safe_limit = max(1, min(limit, 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT seq, kind, payload_json, created_at
                FROM agent_workbench_stream_events
                WHERE conversation_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (conversation_id, after_seq, safe_limit),
            ).fetchall()
        return [
            build_stream_envelope(
                conversation_id=conversation_id,
                seq=int(row["seq"]),
                kind=_stream_kind(row["kind"]),
                payload=_stream_payload(row["payload_json"], _stream_kind(row["kind"])),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def latest_seq(self, *, conversation_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(seq), 0) AS latest_seq
                FROM agent_workbench_stream_events
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return int(row["latest_seq"])

    def first_seq(self, *, conversation_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(seq) AS first_seq
                FROM agent_workbench_stream_events
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        if row is None or row["first_seq"] is None:
            return None
        return int(row["first_seq"])

    def build_gap_event(self, *, conversation_id: str, requested_after_seq: int, created_at: str):
        next_available_seq = self.latest_seq(conversation_id=conversation_id) + 1
        return build_stream_envelope(
            conversation_id=conversation_id,
            seq=max(0, next_available_seq - 1),
            kind="stream.gap",
            payload=AgentWorkbenchGapStreamPayloadResponse(
                payloadType="stream.gap",
                missingFromSeq=requested_after_seq + 1,
                nextAvailableSeq=next_available_seq,
            ),
            created_at=created_at,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return conn


class _SourceRef:
    def __init__(
        self,
        *,
        event_id: str,
        source_kind: str,
        source_id: str,
        source_seq: int | None,
        idempotency_key: str,
    ) -> None:
        self.event_id = event_id
        self.source_kind = source_kind
        self.source_id = source_id
        self.source_seq = source_seq
        self.idempotency_key = idempotency_key


def _source_ref(
    *,
    source_fact_key: str,
    source_kind: str | None,
    source_id: str | None,
    source_seq: int | None,
    idempotency_key: str | None,
) -> _SourceRef:
    parts = source_fact_key.split(":")
    inferred_source_kind = source_kind or (parts[0] if parts and parts[0] else "unknown")
    inferred_source_id = source_id or (parts[1] if len(parts) > 1 and parts[1] else source_fact_key)
    return _SourceRef(
        event_id=source_fact_key,
        source_kind=inferred_source_kind,
        source_id=inferred_source_id,
        source_seq=source_seq,
        idempotency_key=idempotency_key or source_fact_key,
    )


def _stream_kind(value: object) -> AgentWorkbenchStreamKind:
    allowed = AgentWorkbenchStreamKind.__args__
    if isinstance(value, str) and value in allowed:
        return cast(AgentWorkbenchStreamKind, value)
    raise ValueError(f"Unknown Agent Workbench stream kind: {value!r}")


def _stream_payload(value: str, kind: AgentWorkbenchStreamKind) -> AgentWorkbenchStreamPayloadResponse:
    try:
        return _STREAM_PAYLOAD_ADAPTER.validate_json(value)
    except ValidationError:
        legacy_payload = AgentWorkbenchTranscriptPayloadResponse.model_validate_json(value)
        return normalize_agent_workbench_stream_payload(legacy_payload, kind)
