from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from seektalent.product_database_versions import AGENT_WORKBENCH_STREAM_SCHEMA_VERSION
from seektalent.sqlite_migrations import (
    SQLiteMigrationError,
    backup_sqlite_before_migration,
    require_supported_version,
    run_sqlite_integrity_checks,
)
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchGapStreamPayloadResponse,
    AgentWorkbenchStreamKind,
    AgentWorkbenchStreamPayloadResponse,
    AgentWorkbenchTranscriptPayloadResponse,
    normalize_agent_workbench_stream_payload,
)
from seektalent_ui.agent_workbench_stream import build_stream_envelope


_STREAM_PAYLOAD_ADAPTER = TypeAdapter(AgentWorkbenchStreamPayloadResponse)
logger = logging.getLogger(__name__)
_STREAM_EVENTS_TABLE = "agent_workbench_stream_events"


@dataclass(frozen=True)
class AgentWorkbenchStreamSnapshotBoundary:
    snapshot_seq: int
    view_revision: int


class AgentWorkbenchStreamStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.initialize()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            _migrate_stream_schema(conn, database_path=self.path)

    def snapshot_boundary(self, *, conversation_id: str) -> AgentWorkbenchStreamSnapshotBoundary:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(seq), 0) AS snapshot_seq
                FROM agent_workbench_stream_events
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        snapshot_seq = int(row["snapshot_seq"])
        return AgentWorkbenchStreamSnapshotBoundary(snapshot_seq=snapshot_seq, view_revision=snapshot_seq)

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

    def minimum_replay_seq(self, *, conversation_id: str) -> int:
        first_seq = self.first_seq(conversation_id=conversation_id)
        if first_seq is None:
            return 0
        return max(0, first_seq - 1)

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

    def prune_closed_conversation_events(
        self,
        conversation_ids: Iterable[str],
        *,
        created_before: str,
        retain_last: int = 1,
        batch_size: int = 500,
        dry_run: bool = False,
    ) -> int:
        eligible_conversation_ids = sorted({conversation_id for conversation_id in conversation_ids if conversation_id})
        if not eligible_conversation_ids:
            return 0
        safe_retain_last = max(1, retain_last)
        safe_batch_size = max(1, min(batch_size, 5000))
        candidates: list[tuple[str, int]] = []
        with self._connect() as conn:
            for conversation_id in eligible_conversation_ids:
                remaining = safe_batch_size - len(candidates)
                if remaining <= 0:
                    break
                rows = conn.execute(
                    """
                    SELECT seq
                    FROM agent_workbench_stream_events
                    WHERE conversation_id = ?
                      AND created_at < ?
                      AND seq NOT IN (
                          SELECT seq
                          FROM agent_workbench_stream_events
                          WHERE conversation_id = ?
                          ORDER BY seq DESC
                          LIMIT ?
                      )
                    ORDER BY seq ASC
                    LIMIT ?
                    """,
                    (conversation_id, created_before, conversation_id, safe_retain_last, remaining),
                ).fetchall()
                candidates.extend((conversation_id, int(row["seq"])) for row in rows)
            if dry_run or not candidates:
                return len(candidates)
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                """
                DELETE FROM agent_workbench_stream_events
                WHERE conversation_id = ? AND seq = ?
                """,
                candidates,
            )
            conn.commit()
        return len(candidates)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield conn
            conn.commit()
        except (sqlite3.Error, SQLiteMigrationError, ValidationError, RuntimeError, TypeError, ValueError):
            conn.rollback()
            raise
        finally:
            conn.close()


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


def _migrate_stream_schema(conn: sqlite3.Connection, *, database_path: Path) -> None:
    version = require_supported_version(
        conn,
        supported_version=AGENT_WORKBENCH_STREAM_SCHEMA_VERSION,
        store_name="Agent Workbench stream DB",
    )
    if version == 0 and _table_exists(conn, _STREAM_EVENTS_TABLE):
        backup_sqlite_before_migration(
            database_path,
            backup_root=database_path.parent / "migration_backups",
            store_name="agent-workbench-stream",
            now=_stream_migration_now(),
        )
        columns = _table_columns(conn, _STREAM_EVENTS_TABLE)
        if not {"source_kind", "source_id", "source_seq", "idempotency_key"} <= columns:
            _migrate_stream_events_v0(conn)
        else:
            _create_stream_schema(conn)
        _set_user_version(conn, AGENT_WORKBENCH_STREAM_SCHEMA_VERSION)
        run_sqlite_integrity_checks(conn, store_name="Agent Workbench stream DB", foreign_keys=False)
        return
    _create_stream_schema(conn)
    if version == 0:
        _set_user_version(conn, AGENT_WORKBENCH_STREAM_SCHEMA_VERSION)
    run_sqlite_integrity_checks(conn, store_name="Agent Workbench stream DB", foreign_keys=False)


def _create_stream_schema(conn: sqlite3.Connection) -> None:
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


def _migrate_stream_events_v0(conn: sqlite3.Connection) -> None:
    legacy_table = "agent_workbench_stream_events_v0"
    if _table_exists(conn, legacy_table):
        raise RuntimeError(
            "Agent Workbench stream DB migration cannot start because "
            f"temporary table {legacy_table!r} already exists."
        )
    conn.execute(f"ALTER TABLE {_STREAM_EVENTS_TABLE} RENAME TO {legacy_table}")
    _create_stream_schema(conn)
    conn.execute(
        f"""
        INSERT INTO {_STREAM_EVENTS_TABLE} (
            conversation_id, seq, event_id, kind, payload_json,
            source_kind, source_id, source_seq, idempotency_key, created_at
        )
        SELECT
            conversation_id,
            seq,
            event_id,
            kind,
            payload_json,
            'legacy',
            event_id,
            seq,
            'legacy:' || event_id,
            created_at
        FROM {legacy_table}
        ORDER BY conversation_id, seq
        """
    )
    conn.execute(f"DROP TABLE {legacy_table}")


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {version}")


def _stream_migration_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _stream_kind(value: object) -> AgentWorkbenchStreamKind:
    allowed = AgentWorkbenchStreamKind.__args__
    if isinstance(value, str) and value in allowed:
        return cast(AgentWorkbenchStreamKind, value)
    raise ValueError(f"Unknown Agent Workbench stream kind: {value!r}")


def _stream_payload(value: str, kind: AgentWorkbenchStreamKind) -> AgentWorkbenchStreamPayloadResponse:
    try:
        return _STREAM_PAYLOAD_ADAPTER.validate_json(value)
    except ValidationError:
        logger.warning("Decoded legacy agent workbench stream payload.", extra={"stream_kind": kind})
        legacy_payload = AgentWorkbenchTranscriptPayloadResponse.model_validate_json(value)
        return normalize_agent_workbench_stream_payload(legacy_payload, kind)
