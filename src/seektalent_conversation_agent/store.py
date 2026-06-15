from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.models import (
    AgentToolCallRecord,
    CompactionSummaryCursor,
    ContextCompactionRecord,
    ContextSummaryRecord,
    ConversationRecord,
    ConversationReopenState,
    ConversationThreadView,
    TranscriptActivityItem,
    TranscriptMessage,
)


CONVERSATION_AGENT_SCHEMA_VERSION = 3

_ACTIVE_ARCHIVE_BLOCKING_STATUSES = {"starting", "running"}


class ConversationStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > CONVERSATION_AGENT_SCHEMA_VERSION:
                raise ConversationAgentError(
                    "conversation_agent_schema_unsupported",
                    f"conversation-agent schema version {version} is newer than supported version "
                    f"{CONVERSATION_AGENT_SCHEMA_VERSION}",
                )
            if version == CONVERSATION_AGENT_SCHEMA_VERSION:
                return
            if version == 0:
                with conn:
                    _create_schema(conn)
                    conn.execute(f"PRAGMA user_version = {CONVERSATION_AGENT_SCHEMA_VERSION}")
                return
            if version == 2:
                with conn:
                    _migrate_v2_to_v3(conn)
                    conn.execute(f"PRAGMA user_version = {CONVERSATION_AGENT_SCHEMA_VERSION}")
                return
            raise ConversationAgentError(
                "conversation_agent_schema_migration_required",
                f"conversation-agent schema version {version} requires explicit migration",
            )

    def create_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        title: str,
        created_at: str,
    ) -> ConversationRecord:
        safe_title = _normalize_title(title)
        record = ConversationRecord(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            title=safe_title,
            created_at=created_at,
            updated_at=created_at,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_conversations (
                    conversation_id, owner_user_id, workspace_id, status, title, title_updated_at,
                    is_archived, archived_at, archive_reason_code, last_opened_at,
                    latest_message_seq, latest_activity_seq, latest_rendered_runtime_event_seq,
                    runtime_run_id, workbench_session_id, latest_draft_revision_id,
                    approved_requirement_revision_id, final_summary_id, pending_user_action,
                    pending_command_count, pending_requirement_review_count, pending_memory_review_count,
                    created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.conversation_id,
                    record.owner_user_id,
                    record.workspace_id,
                    record.status,
                    record.title,
                    record.updated_at,
                    int(record.is_archived),
                    record.archived_at,
                    None,
                    record.last_opened_at,
                    record.latest_message_seq,
                    record.latest_activity_seq,
                    record.latest_rendered_runtime_event_seq,
                    record.runtime_run_id,
                    record.workbench_session_id,
                    record.latest_draft_revision_id,
                    record.approved_requirement_revision_id,
                    record.final_summary_id,
                    record.pending_user_action,
                    record.pending_command_count,
                    record.pending_requirement_review_count,
                    record.pending_memory_review_count,
                    record.created_at,
                    record.updated_at,
                    record.completed_at,
                ),
            )
        return record

    def get_conversation(self, conversation_id: str) -> ConversationRecord:
        with self._connect() as conn:
            row = _conversation_row(conn, conversation_id)
        return _conversation_from_row(row)

    def append_message(
        self,
        *,
        conversation_id: str,
        role: str,
        message_type: str,
        text: str,
        payload: dict[str, object],
        created_at: str,
        message_id: str | None = None,
        token_count: int | None = None,
        model_input_included: bool = True,
        source_tool_call_id: str | None = None,
        source_runtime_run_id: str | None = None,
        source_runtime_event_seq: int | None = None,
        idempotency_key: str | None = None,
    ) -> TranscriptMessage:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = _conversation_row(conn, conversation_id)
                if row is None:
                    raise ConversationAgentError("conversation_not_found")
                message_seq = int(row["latest_message_seq"]) + 1
                message = TranscriptMessage(
                    message_id=message_id or f"agent_msg_{uuid4().hex}",
                    conversation_id=conversation_id,
                    message_seq=message_seq,
                    role=role,
                    message_type=message_type,
                    text=text,
                    payload=payload,
                    token_count=token_count,
                    model_input_included=model_input_included,
                    source_tool_call_id=source_tool_call_id,
                    source_runtime_run_id=source_runtime_run_id,
                    source_runtime_event_seq=source_runtime_event_seq,
                    created_at=created_at,
                )
                conn.execute(
                    """
                    INSERT INTO agent_transcript_messages (
                        message_id, conversation_id, message_seq, role, message_type, text,
                        payload_json, token_count, model_input_included, source_tool_call_id,
                        source_runtime_run_id, source_runtime_event_seq, idempotency_key, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.message_id,
                        message.conversation_id,
                        message.message_seq,
                        message.role,
                        message.message_type,
                        message.text,
                        _json(message.payload),
                        message.token_count,
                        int(message.model_input_included),
                        message.source_tool_call_id,
                        message.source_runtime_run_id,
                        message.source_runtime_event_seq,
                        idempotency_key,
                        message.created_at,
                    ),
                )
                conn.execute(
                    """
                    UPDATE agent_conversations
                    SET latest_message_seq = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (message.message_seq, created_at, conversation_id),
                )
                conn.commit()
            except (sqlite3.Error, ConversationAgentError, TypeError, ValueError):
                conn.rollback()
                raise
        return message

    def get_message_by_idempotency(self, *, conversation_id: str, idempotency_key: str) -> TranscriptMessage | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM agent_transcript_messages
                WHERE conversation_id = ? AND idempotency_key = ?
                ORDER BY message_seq ASC
                LIMIT 1
                """,
                (conversation_id, idempotency_key),
            ).fetchone()
        return _message_from_row(row) if row is not None else None

    def get_messages(self, *, conversation_id: str) -> list[TranscriptMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM agent_transcript_messages
                WHERE conversation_id = ?
                ORDER BY message_seq ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def save_tool_call(
        self,
        *,
        tool_call_id: str,
        conversation_id: str,
        tool_name: str,
        status: str,
        args: dict[str, object],
        result: dict[str, object] | None,
        reason_code: str | None,
        started_at: str,
        completed_at: str | None = None,
        activity_id: str | None = None,
        runtime_run_id: str | None = None,
    ) -> AgentToolCallRecord:
        record = AgentToolCallRecord(
            tool_call_id=tool_call_id,
            conversation_id=conversation_id,
            activity_id=activity_id,
            runtime_run_id=runtime_run_id,
            tool_name=tool_name,
            status=status,
            args=args,
            result=result,
            reason_code=reason_code,
            started_at=started_at,
            completed_at=completed_at,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_tool_calls (
                    tool_call_id, conversation_id, activity_id, runtime_run_id, tool_name, status,
                    args_json, result_json, reason_code, started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tool_call_id) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    reason_code = excluded.reason_code,
                    completed_at = excluded.completed_at
                """,
                (
                    tool_call_id,
                    conversation_id,
                    activity_id,
                    runtime_run_id,
                    tool_name,
                    status,
                    _json(args),
                    _json(result) if result is not None else None,
                    reason_code,
                    started_at,
                    completed_at,
                ),
            )
        return record

    def list_tool_calls(self, *, conversation_id: str | None = None) -> list[AgentToolCallRecord]:
        sql = "SELECT * FROM agent_tool_calls"
        params: list[object] = []
        if conversation_id is not None:
            sql += " WHERE conversation_id = ?"
            params.append(conversation_id)
        sql += " ORDER BY started_at ASC, tool_call_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_tool_call_from_row(row) for row in rows]

    def list_context_compactions(self, *, conversation_id: str | None = None) -> list[ContextCompactionRecord]:
        sql = "SELECT * FROM agent_context_compactions"
        params: list[object] = []
        if conversation_id is not None:
            sql += " WHERE conversation_id = ?"
            params.append(conversation_id)
        sql += " ORDER BY created_at ASC, compaction_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_context_compaction_from_row(row) for row in rows]

    def upsert_activity_item(
        self,
        *,
        activity_id: str,
        conversation_id: str,
        activity_key: str,
        activity_type: str,
        status: str,
        title: str,
        summary: str,
        payload: dict[str, object],
        source_runtime_run_id: str | None,
        source_event_id_latest: str | None,
        source_event_seq_start: int | None,
        source_event_seq_latest: int | None,
        created_at: str,
        updated_at: str,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> TranscriptActivityItem:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT *
                    FROM agent_transcript_activity_items
                    WHERE conversation_id = ? AND activity_key = ?
                    """,
                    (conversation_id, activity_key),
                ).fetchone()
                if existing is None:
                    row = _conversation_row(conn, conversation_id)
                    if row is None:
                        raise ConversationAgentError("conversation_not_found")
                    activity_seq = int(row["latest_activity_seq"]) + 1
                    conn.execute(
                        """
                        INSERT INTO agent_transcript_activity_items (
                            activity_id, conversation_id, activity_seq, activity_key, activity_type, status,
                            title, summary, source_runtime_run_id, source_event_id_latest,
                            source_event_seq_start, source_event_seq_latest, payload_json,
                            started_at, updated_at, completed_at, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            activity_id,
                            conversation_id,
                            activity_seq,
                            activity_key,
                            activity_type,
                            status,
                            title,
                            summary,
                            source_runtime_run_id,
                            source_event_id_latest,
                            source_event_seq_start,
                            source_event_seq_latest,
                            _json(payload),
                            started_at,
                            updated_at,
                            completed_at,
                            created_at,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE agent_conversations
                        SET latest_activity_seq = ?, updated_at = ?
                        WHERE conversation_id = ?
                        """,
                        (activity_seq, updated_at, conversation_id),
                    )
                else:
                    existing_activity = _activity_from_row(existing)
                    existing_latest_seq = existing_activity.source_event_seq_latest
                    if (
                        existing_latest_seq is not None
                        and source_event_seq_latest is not None
                        and source_event_seq_latest < existing_latest_seq
                    ):
                        conn.commit()
                        return existing_activity
                    activity_seq = int(existing["activity_seq"])
                    conn.execute(
                        """
                        UPDATE agent_transcript_activity_items
                        SET status = ?, title = ?, summary = ?, source_event_id_latest = ?,
                            source_event_seq_latest = ?, payload_json = ?, updated_at = ?,
                            completed_at = COALESCE(?, completed_at)
                        WHERE activity_id = ?
                        """,
                        (
                            status,
                            title,
                            summary,
                            source_event_id_latest,
                            source_event_seq_latest,
                            _json(payload),
                            updated_at,
                            completed_at,
                            existing["activity_id"],
                        ),
                    )
                    activity_id = existing["activity_id"]
                    source_event_seq_start = existing["source_event_seq_start"]
                    created_at = existing["created_at"]
                    started_at = existing["started_at"]
                conn.commit()
            except (sqlite3.Error, ConversationAgentError, TypeError, ValueError):
                conn.rollback()
                raise
        return TranscriptActivityItem(
            activity_id=activity_id,
            conversation_id=conversation_id,
            activity_seq=activity_seq,
            activity_key=activity_key,
            activity_type=activity_type,
            status=status,
            title=title,
            summary=summary,
            source_runtime_run_id=source_runtime_run_id,
            source_event_id_latest=source_event_id_latest,
            source_event_seq_start=source_event_seq_start,
            source_event_seq_latest=source_event_seq_latest,
            payload=payload,
            started_at=started_at,
            updated_at=updated_at,
            completed_at=completed_at,
            created_at=created_at,
        )

    def get_activity_items(self, *, conversation_id: str) -> list[TranscriptActivityItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM agent_transcript_activity_items
                WHERE conversation_id = ?
                ORDER BY activity_seq ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [_activity_from_row(row) for row in rows]

    def rename_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        title: str,
        updated_at: str,
    ) -> ConversationRecord:
        safe_title = _normalize_title(title)
        with self._connect() as conn, conn:
            row = _scoped_conversation_row(conn, conversation_id, owner_user_id, workspace_id)
            if row is None:
                raise ConversationAgentError("conversation_not_found")
            conn.execute(
                """
                UPDATE agent_conversations
                SET title = ?, title_updated_at = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (safe_title, updated_at, updated_at, conversation_id),
            )
            updated = _conversation_row(conn, conversation_id)
        return _conversation_from_row(updated)

    def archive_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        archived_at: str,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            row = _scoped_conversation_row(conn, conversation_id, owner_user_id, workspace_id)
            if row is None:
                raise ConversationAgentError("conversation_not_found")
            if row["status"] in _ACTIVE_ARCHIVE_BLOCKING_STATUSES:
                raise ConversationAgentError("conversation_archive_active_runtime")
            conn.execute(
                """
                UPDATE agent_conversations
                SET is_archived = 1, archived_at = ?, archive_reason_code = NULL, updated_at = ?
                WHERE conversation_id = ?
                """,
                (archived_at, archived_at, conversation_id),
            )
            updated = _conversation_row(conn, conversation_id)
        return _conversation_from_row(updated)

    def unarchive_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        updated_at: str,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            row = _scoped_conversation_row(conn, conversation_id, owner_user_id, workspace_id)
            if row is None:
                raise ConversationAgentError("conversation_not_found")
            conn.execute(
                """
                UPDATE agent_conversations
                SET is_archived = 0, archived_at = NULL, archive_reason_code = NULL, updated_at = ?
                WHERE conversation_id = ?
                """,
                (updated_at, conversation_id),
            )
            updated = _conversation_row(conn, conversation_id)
        return _conversation_from_row(updated)

    def update_conversation_status(
        self,
        *,
        conversation_id: str,
        status: str,
        updated_at: str,
        completed_at: str | None = None,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_conversations
                SET status = ?, updated_at = ?, completed_at = COALESCE(?, completed_at)
                WHERE conversation_id = ?
                """,
                (status, updated_at, completed_at, conversation_id),
            )
            row = _conversation_row(conn, conversation_id)
        return _conversation_from_row(row)

    def link_requirement_draft(
        self,
        *,
        conversation_id: str,
        draft_revision_id: str,
        pending_requirement_review_count: int,
        updated_at: str,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_conversations
                SET latest_draft_revision_id = ?, pending_requirement_review_count = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (draft_revision_id, pending_requirement_review_count, updated_at, conversation_id),
            )
            row = _conversation_row(conn, conversation_id)
        return _conversation_from_row(row)

    def link_approved_requirement(
        self,
        *,
        conversation_id: str,
        approved_requirement_revision_id: str,
        updated_at: str,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_conversations
                SET approved_requirement_revision_id = ?, pending_requirement_review_count = 0, updated_at = ?
                WHERE conversation_id = ?
                """,
                (approved_requirement_revision_id, updated_at, conversation_id),
            )
            row = _conversation_row(conn, conversation_id)
        return _conversation_from_row(row)

    def link_runtime_run(
        self,
        *,
        conversation_id: str,
        runtime_run_id: str,
        workbench_session_id: str | None,
        approved_requirement_revision_id: str,
        linked_at: str,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_runtime_links (
                    conversation_id, runtime_run_id, status, latest_event_seq, linked_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, runtime_run_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, runtime_run_id, "linked", 0, linked_at, linked_at),
            )
            conn.execute(
                """
                UPDATE agent_conversations
                SET runtime_run_id = ?, workbench_session_id = ?, approved_requirement_revision_id = ?,
                    status = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    runtime_run_id,
                    workbench_session_id,
                    approved_requirement_revision_id,
                    "running",
                    linked_at,
                    conversation_id,
                ),
            )
            row = _conversation_row(conn, conversation_id)
        return _conversation_from_row(row)

    def runtime_run_is_linked(self, *, conversation_id: str, runtime_run_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM agent_runtime_links
                WHERE conversation_id = ? AND runtime_run_id = ?
                """,
                (conversation_id, runtime_run_id),
            ).fetchone()
        return row is not None

    def update_rendered_runtime_cursor(
        self,
        *,
        conversation_id: str,
        runtime_run_id: str,
        latest_event_seq: int,
        updated_at: str,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_conversations
                SET latest_rendered_runtime_event_seq = ?, updated_at = ?
                WHERE conversation_id = ?
                  AND runtime_run_id = ?
                  AND latest_rendered_runtime_event_seq < ?
                """,
                (latest_event_seq, updated_at, conversation_id, runtime_run_id, latest_event_seq),
            )
            conn.execute(
                """
                UPDATE agent_runtime_links
                SET latest_event_seq = ?, updated_at = ?
                WHERE conversation_id = ? AND runtime_run_id = ?
                  AND latest_event_seq < ?
                """,
                (latest_event_seq, updated_at, conversation_id, runtime_run_id, latest_event_seq),
            )
            row = _conversation_row(conn, conversation_id)
        return _conversation_from_row(row)

    def set_final_summary(
        self,
        *,
        conversation_id: str,
        final_summary_id: str,
        updated_at: str,
    ) -> ConversationRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_conversations
                SET final_summary_id = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (final_summary_id, updated_at, conversation_id),
            )
            row = _conversation_row(conn, conversation_id)
        return _conversation_from_row(row)

    def reopen_conversation(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        opened_at: str,
    ) -> ConversationThreadView:
        with self._connect() as conn, conn:
            row = _scoped_conversation_row(conn, conversation_id, owner_user_id, workspace_id)
            if row is None:
                raise ConversationAgentError("conversation_not_found")
            conn.execute(
                """
                UPDATE agent_conversations
                SET last_opened_at = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (opened_at, opened_at, conversation_id),
            )
            updated = _conversation_row(conn, conversation_id)
            message_rows = conn.execute(
                """
                SELECT *
                FROM agent_transcript_messages
                WHERE conversation_id = ?
                ORDER BY message_seq ASC
                """,
                (conversation_id,),
            ).fetchall()
            activity_rows = conn.execute(
                """
                SELECT *
                FROM agent_transcript_activity_items
                WHERE conversation_id = ?
                ORDER BY activity_seq ASC
                """,
                (conversation_id,),
            ).fetchall()
            summary_row = _latest_summary_row(conn, conversation_id)
        conversation = _conversation_from_row(updated)
        return ConversationThreadView(
            conversation_reopen_state=_reopen_state(
                conversation,
                opened_at=opened_at,
                compaction_summary_cursor=_summary_cursor(summary_row),
            ),
            messages=[_message_from_row(row) for row in message_rows],
            activity_items=[_activity_from_row(row) for row in activity_rows],
        )

    def list_conversations(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        include_archived: bool = False,
    ) -> list[ConversationRecord]:
        sql = """
            SELECT *
            FROM agent_conversations
            WHERE owner_user_id = ? AND workspace_id = ?
        """
        params: list[object] = [owner_user_id, workspace_id]
        if not include_archived:
            sql += " AND is_archived = 0"
        sql += " ORDER BY updated_at DESC, conversation_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_conversation_from_row(row) for row in rows]

    def create_context_summary(
        self,
        *,
        summary_id: str,
        conversation_id: str,
        source_message_seq_start: int,
        source_message_seq_end: int,
        source_activity_seq_start: int | None,
        source_activity_seq_end: int | None,
        latest_rendered_runtime_event_seq: int,
        summary_text: str,
        quality_status: str,
        quality_evidence: dict[str, object],
        token_count: int | None,
        created_at: str,
    ) -> ContextSummaryRecord:
        record = ContextSummaryRecord(
            summary_id=summary_id,
            conversation_id=conversation_id,
            source_message_seq_start=source_message_seq_start,
            source_message_seq_end=source_message_seq_end,
            source_activity_seq_start=source_activity_seq_start,
            source_activity_seq_end=source_activity_seq_end,
            latest_rendered_runtime_event_seq=latest_rendered_runtime_event_seq,
            summary_text=summary_text,
            quality_status=quality_status,
            quality_evidence=quality_evidence,
            token_count=token_count,
            created_at=created_at,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_context_summaries (
                    summary_id, conversation_id, source_message_seq_start, source_message_seq_end,
                    source_activity_seq_start, source_activity_seq_end, latest_rendered_runtime_event_seq,
                    summary_text, quality_status, quality_evidence_json, token_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.summary_id,
                    record.conversation_id,
                    record.source_message_seq_start,
                    record.source_message_seq_end,
                    record.source_activity_seq_start,
                    record.source_activity_seq_end,
                    record.latest_rendered_runtime_event_seq,
                    record.summary_text,
                    record.quality_status,
                    _json(record.quality_evidence),
                    record.token_count,
                    record.created_at,
                ),
            )
        return record

    def save_context_compaction(
        self,
        *,
        compaction_id: str,
        conversation_id: str,
        status: str,
        trigger_reason_code: str,
        created_at: str,
        summary_id: str | None = None,
        source_message_seq_start: int | None = None,
        source_message_seq_end: int | None = None,
        source_activity_seq_start: int | None = None,
        source_activity_seq_end: int | None = None,
        quality_reason_code: str | None = None,
        completed_at: str | None = None,
        failed_reason_code: str | None = None,
    ) -> ContextCompactionRecord:
        record = ContextCompactionRecord(
            compaction_id=compaction_id,
            conversation_id=conversation_id,
            status=status,
            trigger_reason_code=trigger_reason_code,
            summary_id=summary_id,
            source_message_seq_start=source_message_seq_start,
            source_message_seq_end=source_message_seq_end,
            source_activity_seq_start=source_activity_seq_start,
            source_activity_seq_end=source_activity_seq_end,
            quality_reason_code=quality_reason_code,
            created_at=created_at,
            completed_at=completed_at,
            failed_reason_code=failed_reason_code,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_context_compactions (
                    compaction_id, conversation_id, status, trigger_reason_code, summary_id,
                    source_message_seq_start, source_message_seq_end, source_activity_seq_start,
                    source_activity_seq_end, quality_reason_code, created_at, completed_at, failed_reason_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(compaction_id) DO UPDATE SET
                    status = excluded.status,
                    summary_id = excluded.summary_id,
                    source_message_seq_start = excluded.source_message_seq_start,
                    source_message_seq_end = excluded.source_message_seq_end,
                    source_activity_seq_start = excluded.source_activity_seq_start,
                    source_activity_seq_end = excluded.source_activity_seq_end,
                    quality_reason_code = excluded.quality_reason_code,
                    completed_at = excluded.completed_at,
                    failed_reason_code = excluded.failed_reason_code
                """,
                (
                    record.compaction_id,
                    record.conversation_id,
                    record.status,
                    record.trigger_reason_code,
                    record.summary_id,
                    record.source_message_seq_start,
                    record.source_message_seq_end,
                    record.source_activity_seq_start,
                    record.source_activity_seq_end,
                    record.quality_reason_code,
                    record.created_at,
                    record.completed_at,
                    record.failed_reason_code,
                ),
            )
        return record

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_conversations (
            conversation_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            title_updated_at TEXT,
            is_archived INTEGER NOT NULL DEFAULT 0,
            archived_at TEXT,
            archive_reason_code TEXT,
            last_opened_at TEXT,
            latest_message_seq INTEGER NOT NULL DEFAULT 0,
            latest_activity_seq INTEGER NOT NULL DEFAULT 0,
            latest_rendered_runtime_event_seq INTEGER NOT NULL DEFAULT 0,
            runtime_run_id TEXT,
            workbench_session_id TEXT,
            latest_draft_revision_id TEXT,
            approved_requirement_revision_id TEXT,
            final_summary_id TEXT,
            pending_user_action TEXT,
            pending_command_count INTEGER NOT NULL DEFAULT 0,
            pending_requirement_review_count INTEGER NOT NULL DEFAULT 0,
            pending_memory_review_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_agent_conversations_owner_workspace
            ON agent_conversations(owner_user_id, workspace_id, is_archived, updated_at DESC);

        CREATE TABLE IF NOT EXISTS agent_transcript_messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            message_seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            message_type TEXT NOT NULL,
            text TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            token_count INTEGER,
            model_input_included INTEGER NOT NULL DEFAULT 1,
            source_tool_call_id TEXT,
            source_runtime_run_id TEXT,
            source_runtime_event_seq INTEGER,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(conversation_id, message_seq),
            UNIQUE(conversation_id, source_runtime_run_id, source_runtime_event_seq)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_transcript_messages_idempotency
            ON agent_transcript_messages(conversation_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;

        CREATE TABLE IF NOT EXISTS agent_transcript_activity_items (
            activity_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            activity_seq INTEGER NOT NULL,
            activity_key TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_runtime_run_id TEXT,
            source_event_id_latest TEXT,
            source_event_seq_start INTEGER,
            source_event_seq_latest INTEGER,
            payload_json TEXT NOT NULL,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(conversation_id, activity_seq),
            UNIQUE(conversation_id, activity_key)
        );

        CREATE TABLE IF NOT EXISTS agent_tool_calls (
            tool_call_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            activity_id TEXT REFERENCES agent_transcript_activity_items(activity_id) ON DELETE SET NULL,
            runtime_run_id TEXT,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            args_json TEXT NOT NULL,
            result_json TEXT,
            reason_code TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_runtime_links (
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            runtime_run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            latest_event_seq INTEGER NOT NULL DEFAULT 0,
            linked_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(conversation_id, runtime_run_id)
        );

        CREATE TABLE IF NOT EXISTS agent_context_summaries (
            summary_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            source_message_seq_start INTEGER NOT NULL,
            source_message_seq_end INTEGER NOT NULL,
            source_activity_seq_start INTEGER,
            source_activity_seq_end INTEGER,
            latest_rendered_runtime_event_seq INTEGER NOT NULL,
            summary_text TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            quality_evidence_json TEXT NOT NULL,
            token_count INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_context_compactions (
            compaction_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            trigger_reason_code TEXT NOT NULL,
            summary_id TEXT REFERENCES agent_context_summaries(summary_id) ON DELETE SET NULL,
            source_message_seq_start INTEGER,
            source_message_seq_end INTEGER,
            source_activity_seq_start INTEGER,
            source_activity_seq_end INTEGER,
            quality_reason_code TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            failed_reason_code TEXT
        );
        """
    )


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    _ensure_columns(conn, "agent_transcript_messages", {"idempotency_key": "TEXT"})
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_transcript_messages_idempotency
            ON agent_transcript_messages(conversation_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """
    )


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")


def _normalize_title(title: str) -> str:
    normalized = title.strip()
    if not normalized or len(normalized) > 120:
        raise ConversationAgentError("conversation_title_invalid")
    return normalized


def _conversation_row(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM agent_conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()


def _scoped_conversation_row(
    conn: sqlite3.Connection,
    conversation_id: str,
    owner_user_id: str,
    workspace_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM agent_conversations
        WHERE conversation_id = ? AND owner_user_id = ? AND workspace_id = ?
        """,
        (conversation_id, owner_user_id, workspace_id),
    ).fetchone()


def _latest_summary_row(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM agent_context_summaries
        WHERE conversation_id = ?
        ORDER BY source_message_seq_end DESC, created_at DESC
        LIMIT 1
        """,
        (conversation_id,),
    ).fetchone()


def _conversation_from_row(row: sqlite3.Row | None) -> ConversationRecord:
    if row is None:
        raise ConversationAgentError("conversation_not_found")
    return ConversationRecord(
        conversation_id=row["conversation_id"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        status=row["status"],
        title=row["title"],
        is_archived=bool(row["is_archived"]),
        latest_message_seq=int(row["latest_message_seq"]),
        latest_activity_seq=int(row["latest_activity_seq"]),
        latest_rendered_runtime_event_seq=int(row["latest_rendered_runtime_event_seq"] or 0),
        runtime_run_id=row["runtime_run_id"],
        workbench_session_id=row["workbench_session_id"],
        latest_draft_revision_id=row["latest_draft_revision_id"],
        approved_requirement_revision_id=row["approved_requirement_revision_id"],
        final_summary_id=row["final_summary_id"],
        pending_user_action=row["pending_user_action"],
        pending_command_count=int(row["pending_command_count"] or 0),
        pending_requirement_review_count=int(row["pending_requirement_review_count"] or 0),
        pending_memory_review_count=int(row["pending_memory_review_count"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_opened_at=row["last_opened_at"],
        archived_at=row["archived_at"],
        completed_at=row["completed_at"],
    )


def _reopen_state(
    conversation: ConversationRecord,
    *,
    opened_at: str,
    compaction_summary_cursor: CompactionSummaryCursor,
) -> ConversationReopenState:
    return ConversationReopenState(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        status=conversation.status,
        is_archived=conversation.is_archived,
        latest_message_seq=conversation.latest_message_seq,
        latest_activity_seq=conversation.latest_activity_seq,
        latest_rendered_runtime_event_seq=conversation.latest_rendered_runtime_event_seq,
        runtime_run_id=conversation.runtime_run_id,
        workbench_session_id=conversation.workbench_session_id,
        latest_draft_revision_id=conversation.latest_draft_revision_id,
        approved_requirement_revision_id=conversation.approved_requirement_revision_id,
        final_summary_id=conversation.final_summary_id,
        pending_user_action=conversation.pending_user_action,
        pending_command_count=conversation.pending_command_count,
        pending_requirement_review_count=conversation.pending_requirement_review_count,
        pending_memory_review_count=conversation.pending_memory_review_count,
        compaction_summary_cursor=compaction_summary_cursor,
        allowed_actions=_allowed_actions(conversation),
        last_opened_at=opened_at,
    )


def _allowed_actions(conversation: ConversationRecord) -> list[str]:
    if conversation.is_archived:
        return ["unarchive"]
    actions = ["send_message", "rename", "archive"]
    if conversation.latest_draft_revision_id and not conversation.approved_requirement_revision_id:
        actions.extend(["edit_requirements", "confirm_requirements"])
    if conversation.approved_requirement_revision_id and not conversation.runtime_run_id:
        actions.append("start_workflow")
    if conversation.status in {"running", "starting"}:
        actions.extend(["request_pause", "request_cancel", "ask_detail"])
    if conversation.status == "paused":
        actions.extend(["resume_workflow", "request_cancel", "ask_detail"])
    if conversation.status in {"completed", "failed", "cancelled"}:
        actions.extend(["ask_detail", "prepare_final_summary"])
    return actions


def _summary_cursor(row: sqlite3.Row | None) -> CompactionSummaryCursor:
    if row is None:
        return CompactionSummaryCursor()
    return CompactionSummaryCursor(
        latest_summary_id=row["summary_id"],
        covered_message_seq_end=int(row["source_message_seq_end"]),
    )


def _message_from_row(row: sqlite3.Row) -> TranscriptMessage:
    return TranscriptMessage(
        message_id=row["message_id"],
        conversation_id=row["conversation_id"],
        message_seq=int(row["message_seq"]),
        role=row["role"],
        message_type=row["message_type"],
        text=row["text"],
        payload=_loads_dict(row["payload_json"]),
        token_count=row["token_count"],
        model_input_included=bool(row["model_input_included"]),
        source_tool_call_id=row["source_tool_call_id"],
        source_runtime_run_id=row["source_runtime_run_id"],
        source_runtime_event_seq=row["source_runtime_event_seq"],
        created_at=row["created_at"],
    )


def _tool_call_from_row(row: sqlite3.Row) -> AgentToolCallRecord:
    return AgentToolCallRecord(
        tool_call_id=row["tool_call_id"],
        conversation_id=row["conversation_id"],
        activity_id=row["activity_id"],
        runtime_run_id=row["runtime_run_id"],
        tool_name=row["tool_name"],
        status=row["status"],
        args=_loads_dict(row["args_json"]),
        result=_loads_dict(row["result_json"]) if row["result_json"] is not None else None,
        reason_code=row["reason_code"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _context_compaction_from_row(row: sqlite3.Row) -> ContextCompactionRecord:
    return ContextCompactionRecord(
        compaction_id=row["compaction_id"],
        conversation_id=row["conversation_id"],
        status=row["status"],
        trigger_reason_code=row["trigger_reason_code"],
        summary_id=row["summary_id"],
        source_message_seq_start=row["source_message_seq_start"],
        source_message_seq_end=row["source_message_seq_end"],
        source_activity_seq_start=row["source_activity_seq_start"],
        source_activity_seq_end=row["source_activity_seq_end"],
        quality_reason_code=row["quality_reason_code"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        failed_reason_code=row["failed_reason_code"],
    )


def _activity_from_row(row: sqlite3.Row) -> TranscriptActivityItem:
    return TranscriptActivityItem(
        activity_id=row["activity_id"],
        conversation_id=row["conversation_id"],
        activity_seq=int(row["activity_seq"]),
        activity_key=row["activity_key"],
        activity_type=row["activity_type"],
        status=row["status"],
        title=row["title"],
        summary=row["summary"],
        source_runtime_run_id=row["source_runtime_run_id"],
        source_event_id_latest=row["source_event_id_latest"],
        source_event_seq_start=row["source_event_seq_start"],
        source_event_seq_latest=row["source_event_seq_latest"],
        payload=_loads_dict(row["payload_json"]),
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_dict(value: str) -> dict[str, object]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ConversationAgentError("conversation_payload_invalid")
    return loaded
