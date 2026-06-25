from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from seektalent_ui.workbench_store_helpers import now_iso
from seektalent_workbench_v2.models import (
    WorkbenchV2Conversation,
    WorkbenchV2ConversationRecord,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventInput,
)


class WorkbenchV2Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def create_conversation(self, *, first_user_text: str, idempotency_key: str | None) -> WorkbenchV2Conversation:
        digest = _payload_digest({"firstUserText": first_user_text})
        now = now_iso()
        with self._connect() as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT conversation_id, payload_digest FROM workbench_v2_idempotency WHERE key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row is not None:
                    if row["payload_digest"] != digest:
                        raise ValueError("workbench_v2_idempotency_conflict")
                    return self.get_conversation(row["conversation_id"]).conversation
            conversation_id = f"agentv2_{uuid4().hex}"
            title = _title_from_text(first_user_text)
            conn.execute(
                """
                INSERT INTO workbench_v2_conversations (
                    id, title, created_at, updated_at, runtime_state
                ) VALUES (?, ?, ?, ?, 'idle')
                """,
                (conversation_id, title, now, now),
            )
            if idempotency_key:
                conn.execute(
                    """
                    INSERT INTO workbench_v2_idempotency (key, conversation_id, payload_digest, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (idempotency_key, conversation_id, digest, now),
                )
            conn.commit()
            return self.get_conversation(conversation_id).conversation

    def append_event(
        self,
        conversation_id: str,
        event: WorkbenchV2TranscriptEventInput,
    ) -> WorkbenchV2TranscriptEvent:
        now = now_iso()
        event_id = f"agentv2_event_{uuid4().hex}"
        payload_json = json.dumps(event.payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event.dedupe_key:
                row = conn.execute(
                    "SELECT id FROM workbench_v2_transcript_events WHERE conversation_id = ? AND dedupe_key = ?",
                    (conversation_id, event.dedupe_key),
                ).fetchone()
                if row is not None:
                    conn.commit()
                    return self.get_event(row["id"])
            next_step = int(
                conn.execute(
                    "SELECT COALESCE(MAX(step), 0) + 1 AS next_step FROM workbench_v2_transcript_events WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()["next_step"]
            )
            conn.execute(
                """
                INSERT INTO workbench_v2_transcript_events (
                    id, conversation_id, step, type, role, payload_json, status,
                    parent_event_id, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    next_step,
                    event.type,
                    event.role,
                    payload_json,
                    event.status,
                    event.parent_event_id,
                    event.dedupe_key,
                    now,
                ),
            )
            conn.execute(
                "UPDATE workbench_v2_conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            conn.commit()
        return self.get_event(event_id)

    def append_context_summary(self, conversation_id: str, *, summary: str) -> WorkbenchV2TranscriptEvent:
        event = self.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="context_summary",
                role="system",
                payload={"summary": summary},
                status="completed",
            ),
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE workbench_v2_conversations SET context_summary = ?, updated_at = ? WHERE id = ?",
                (summary, event.created_at, conversation_id),
            )
        return event

    def get_event(self, event_id: str) -> WorkbenchV2TranscriptEvent:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workbench_v2_transcript_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return _event_from_row(row)

    def get_conversation(self, conversation_id: str) -> WorkbenchV2ConversationRecord:
        with self._connect() as conn:
            conversation_row = conn.execute(
                "SELECT * FROM workbench_v2_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation_row is None:
                raise KeyError(conversation_id)
            event_rows = conn.execute(
                "SELECT * FROM workbench_v2_transcript_events WHERE conversation_id = ? ORDER BY step",
                (conversation_id,),
            ).fetchall()
        return WorkbenchV2ConversationRecord(
            conversation=_conversation_from_row(conversation_row),
            events=[_event_from_row(row) for row in event_rows],
        )

    def list_conversations(self) -> list[WorkbenchV2Conversation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workbench_v2_conversations ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [_conversation_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_v2_conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    runtime_run_id TEXT,
    runtime_state TEXT NOT NULL CHECK(runtime_state IN ('idle','queued','running','completed','failed','cancelled')),
    context_summary TEXT
);

CREATE TABLE IF NOT EXISTS workbench_v2_transcript_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES workbench_v2_conversations(id) ON DELETE CASCADE,
    step INTEGER NOT NULL,
    type TEXT NOT NULL,
    role TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    parent_event_id TEXT,
    dedupe_key TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, step),
    UNIQUE(conversation_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS workbench_v2_idempotency (
    key TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES workbench_v2_conversations(id) ON DELETE CASCADE,
    payload_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workbench_v2_events_conversation_step
ON workbench_v2_transcript_events(conversation_id, step);
"""


def _payload_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _title_from_text(text: str) -> str:
    stripped = " ".join(text.strip().split())
    return stripped[:40] if stripped else "新对话"


def _conversation_from_row(row: sqlite3.Row) -> WorkbenchV2Conversation:
    return WorkbenchV2Conversation(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        runtime_run_id=row["runtime_run_id"],
        runtime_state=row["runtime_state"],
        context_summary=row["context_summary"],
    )


def _event_from_row(row: sqlite3.Row) -> WorkbenchV2TranscriptEvent:
    return WorkbenchV2TranscriptEvent(
        id=row["id"],
        conversation_id=row["conversation_id"],
        step=row["step"],
        type=row["type"],
        role=row["role"],
        payload=json.loads(row["payload_json"]),
        status=row["status"],
        parent_event_id=row["parent_event_id"],
        dedupe_key=row["dedupe_key"],
        created_at=row["created_at"],
    )
