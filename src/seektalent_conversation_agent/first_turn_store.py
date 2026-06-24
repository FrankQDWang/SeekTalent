from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import sqlite3
from pathlib import Path

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.job_request_store import job_request_hash
from seektalent_conversation_agent.job_requests import SourceKind, normalize_source_kinds


@dataclass(frozen=True)
class FirstTurnIds:
    conversation_id: str
    job_request_revision_id: str
    start_request_id: str
    user_message_id: str
    assistant_progress_message_id: str
    operation_id: str
    outbox_id: str


@dataclass(frozen=True)
class ConversationStartRequest:
    start_request_id: str
    workspace_id: str
    owner_user_id: str
    conversation_id: str
    job_request_revision_id: str
    idempotency_key: str
    request_hash: str
    status: str
    created_at: str
    updated_at: str


class FirstTurnStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def create_or_replay_first_turn(
        self,
        *,
        ids: FirstTurnIds,
        owner_user_id: str,
        workspace_id: str,
        title: str,
        jd_text: str,
        job_title: str | None,
        notes: str | None,
        source_kinds: Sequence[str] | None,
        idempotency_key: str,
        workspace_source_policy_id: str | None,
        now: str,
    ) -> ConversationStartRequest:
        normalized_source_kinds = normalize_source_kinds(list(source_kinds or []), allow_empty=True)
        request_hash = conversation_start_request_hash(
            jd_text=jd_text,
            job_title=job_title,
            notes=notes,
            source_kinds=normalized_source_kinds,
            workspace_source_policy_id=workspace_source_policy_id,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = _get_by_idempotency(
                    conn,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise ConversationAgentError("idempotency_key_conflict")
                    conn.commit()
                    return existing

                job_request_request_hash = job_request_hash(
                    user_job_title=job_title,
                    jd_text=jd_text,
                    notes=notes,
                    source_kinds=normalized_source_kinds,
                    workspace_source_policy_id=workspace_source_policy_id,
                )
                _insert_conversation(
                    conn,
                    conversation_id=ids.conversation_id,
                    owner_user_id=owner_user_id,
                    workspace_id=workspace_id,
                    title=title,
                    now=now,
                )
                conn.execute(
                    """
                    INSERT INTO wts_job_request_revisions (
                        job_request_revision_id, workspace_id, owner_user_id, conversation_id,
                        jd_text, user_job_title, extracted_job_title, notes, source_kinds_json,
                        workspace_source_policy_id, request_hash, idempotency_key, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ids.job_request_revision_id,
                        workspace_id,
                        owner_user_id,
                        ids.conversation_id,
                        jd_text,
                        job_title,
                        notes,
                        _json(normalized_source_kinds),
                        workspace_source_policy_id,
                        job_request_request_hash,
                        idempotency_key,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO wts_conversation_start_requests (
                        start_request_id, workspace_id, owner_user_id, conversation_id,
                        job_request_revision_id, idempotency_key, request_hash, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)
                    """,
                    (
                        ids.start_request_id,
                        workspace_id,
                        owner_user_id,
                        ids.conversation_id,
                        ids.job_request_revision_id,
                        idempotency_key,
                        request_hash,
                        now,
                        now,
                    ),
                )
                _insert_message(
                    conn,
                    message_id=ids.user_message_id,
                    conversation_id=ids.conversation_id,
                    message_seq=1,
                    role="user",
                    message_type="user_text",
                    text=jd_text,
                    payload={
                        "jobTitle": job_title,
                        "notes": notes,
                        "sourceKinds": list(normalized_source_kinds),
                        "jobRequestRevisionId": ids.job_request_revision_id,
                    },
                    idempotency_key=f"{idempotency_key}:user",
                    now=now,
                )
                _insert_message(
                    conn,
                    message_id=ids.assistant_progress_message_id,
                    conversation_id=ids.conversation_id,
                    message_seq=2,
                    role="assistant",
                    message_type="runtime_progress",
                    text="正在处理需求",
                    payload={"jobRequestRevisionId": ids.job_request_revision_id},
                    idempotency_key=f"{idempotency_key}:assistant-progress",
                    now=now,
                )
                conn.execute(
                    """
                    INSERT INTO agent_operation_audits (
                        operation_id, conversation_id, activity_id, runtime_run_id, operation_name,
                        execution_origin, status, args_json, result_json, reason_code, started_at, completed_at
                    )
                    VALUES (?, ?, NULL, NULL, 'extract_requirements', 'service', 'started', ?, NULL, NULL, ?, NULL)
                    """,
                    (
                        ids.operation_id,
                        ids.conversation_id,
                        _json(
                            {
                                "jobTitle": job_title,
                                "sourceKinds": list(normalized_source_kinds),
                                "jobRequestRevisionId": ids.job_request_revision_id,
                            }
                        ),
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO wts_outbox (
                        outbox_id, workspace_id, event_type, aggregate_id, payload_json,
                        status, attempt_count, created_at, updated_at
                    )
                    VALUES (?, ?, 'requirement_extraction_requested', ?, ?, 'held', 0, ?, ?)
                    """,
                    (
                        ids.outbox_id,
                        workspace_id,
                        ids.job_request_revision_id,
                        _json(
                            {
                                "startRequestId": ids.start_request_id,
                                "conversationId": ids.conversation_id,
                                "jobRequestRevisionId": ids.job_request_revision_id,
                            }
                        ),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    UPDATE agent_conversations
                    SET latest_message_seq = 2, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (now, ids.conversation_id),
                )
                conn.commit()
                return ConversationStartRequest(
                    start_request_id=ids.start_request_id,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    conversation_id=ids.conversation_id,
                    job_request_revision_id=ids.job_request_revision_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    status="accepted",
                    created_at=now,
                    updated_at=now,
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                return self._resolve_conflict(
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    exc=exc,
                )
            except (sqlite3.Error, ConversationAgentError, TypeError, ValueError):
                conn.rollback()
                raise

    def get_start_request(
        self,
        *,
        start_request_id: str,
        workspace_id: str,
        owner_user_id: str,
    ) -> ConversationStartRequest:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_conversation_start_requests
                WHERE start_request_id = ? AND workspace_id = ? AND owner_user_id = ?
                """,
                (start_request_id, workspace_id, owner_user_id),
            ).fetchone()
        if row is None:
            raise ConversationAgentError("conversation_start_request_not_found")
        return _start_request_from_row(row)

    def _resolve_conflict(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        idempotency_key: str,
        request_hash: str,
        exc: sqlite3.IntegrityError,
    ) -> ConversationStartRequest:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = _get_by_idempotency(
                    conn,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    idempotency_key=idempotency_key,
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ConversationAgentError("idempotency_key_conflict") from exc
            return existing
        raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield conn
        finally:
            conn.close()


def conversation_start_request_hash(
    *,
    jd_text: str,
    job_title: str | None,
    notes: str | None,
    source_kinds: list[SourceKind],
    workspace_source_policy_id: str | None,
) -> str:
    payload = {
        "schema": "wts.conversation_start_request.request_hash.v1",
        "jdText": jd_text,
        "jobTitle": job_title,
        "notes": notes,
        "sourceKinds": list(source_kinds),
        "workspaceSourcePolicyId": workspace_source_policy_id,
    }
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def _insert_conversation(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    owner_user_id: str,
    workspace_id: str,
    title: str,
    now: str,
) -> None:
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
        VALUES (?, ?, ?, 'starting', ?, ?, 0, NULL, NULL, ?, 0, 0, 0, NULL, NULL, NULL,
                NULL, NULL, NULL, 0, 0, 0, ?, ?, NULL)
        """,
        (conversation_id, owner_user_id, workspace_id, title, now, now, now, now),
    )


def _insert_message(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    conversation_id: str,
    message_seq: int,
    role: str,
    message_type: str,
    text: str,
    payload: dict[str, object],
    idempotency_key: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_transcript_messages (
            message_id, conversation_id, message_seq, role, message_type, text,
            payload_json, token_count, model_input_included, source_operation_id,
            source_runtime_run_id, source_runtime_event_seq, idempotency_key, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, ?, ?)
        """,
        (
            message_id,
            conversation_id,
            message_seq,
            role,
            message_type,
            text,
            _json(payload),
            int(message_type != "runtime_progress"),
            idempotency_key,
            now,
        ),
    )


def _get_by_idempotency(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    owner_user_id: str,
    idempotency_key: str,
) -> ConversationStartRequest | None:
    row = conn.execute(
        """
        SELECT *
        FROM wts_conversation_start_requests
        WHERE workspace_id = ? AND owner_user_id = ? AND idempotency_key = ?
        """,
        (workspace_id, owner_user_id, idempotency_key),
    ).fetchone()
    return _start_request_from_row(row) if row is not None else None


def _start_request_from_row(row: sqlite3.Row) -> ConversationStartRequest:
    return ConversationStartRequest(
        start_request_id=row["start_request_id"],
        workspace_id=row["workspace_id"],
        owner_user_id=row["owner_user_id"],
        conversation_id=row["conversation_id"],
        job_request_revision_id=row["job_request_revision_id"],
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
