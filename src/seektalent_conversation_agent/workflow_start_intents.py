from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
import json
import sqlite3
from pathlib import Path
from typing import Literal
from uuid import uuid4

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.job_requests import SourceKind


WorkflowConfirmRequestStatus = Literal["pending", "approved", "intent_created", "failed"]
WorkflowStartIntentStatus = Literal["pending", "started", "failed", "cancelled"]
WorkbenchOutboxStatus = Literal["held", "pending", "in_progress", "done"]


@dataclass(frozen=True)
class WorkflowConfirmRequest:
    confirm_request_id: str
    workspace_id: str
    owner_user_id: str
    conversation_id: str
    draft_revision_id: str
    expected_draft_revision_id: str
    job_request_revision_id: str
    approved_requirement_revision_id: str | None
    idempotency_key: str
    request_hash: str
    status: WorkflowConfirmRequestStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkflowStartIntent:
    workflow_start_intent_id: str
    workspace_id: str
    owner_user_id: str
    conversation_id: str
    draft_revision_id: str
    approved_requirement_revision_id: str
    job_request_revision_id: str
    idempotency_key: str
    request_hash: str
    deterministic_run_key: str
    status: WorkflowStartIntentStatus
    runtime_run_id: str | None
    reason_code: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchOutboxItem:
    outbox_id: str
    workspace_id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, object]
    status: WorkbenchOutboxStatus
    attempt_count: int
    created_at: str
    updated_at: str


class WorkflowConfirmRequestStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def create_or_get(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        draft_revision_id: str,
        expected_draft_revision_id: str,
        job_request_revision_id: str,
        idempotency_key: str,
        request_hash: str,
        approved_requirement_revision_id: str | None = None,
        status: WorkflowConfirmRequestStatus = "pending",
        now: str,
    ) -> WorkflowConfirmRequest:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = _get_confirm_request_by_idempotency_key(
                    conn,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    if existing.request_hash != request_hash or existing.draft_revision_id != draft_revision_id:
                        raise ConversationAgentError("idempotency_key_conflict")
                    conn.commit()
                    return existing
                confirm_request = WorkflowConfirmRequest(
                    confirm_request_id=f"wts_confirmreq_{uuid4().hex}",
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    conversation_id=conversation_id,
                    draft_revision_id=draft_revision_id,
                    expected_draft_revision_id=expected_draft_revision_id,
                    job_request_revision_id=job_request_revision_id,
                    approved_requirement_revision_id=approved_requirement_revision_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
                conn.execute(
                    """
                    INSERT INTO wts_confirm_requirement_requests (
                        confirm_request_id, workspace_id, owner_user_id, conversation_id,
                        draft_revision_id, expected_draft_revision_id, job_request_revision_id,
                        approved_requirement_revision_id, idempotency_key, request_hash,
                        status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        confirm_request.confirm_request_id,
                        confirm_request.workspace_id,
                        confirm_request.owner_user_id,
                        confirm_request.conversation_id,
                        confirm_request.draft_revision_id,
                        confirm_request.expected_draft_revision_id,
                        confirm_request.job_request_revision_id,
                        confirm_request.approved_requirement_revision_id,
                        confirm_request.idempotency_key,
                        confirm_request.request_hash,
                        confirm_request.status,
                        confirm_request.created_at,
                        confirm_request.updated_at,
                    ),
                )
                conn.commit()
                return confirm_request
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                existing = self.get_by_idempotency_key(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    if existing.request_hash != request_hash or existing.draft_revision_id != draft_revision_id:
                        raise ConversationAgentError("idempotency_key_conflict") from exc
                    return existing
                raise
            except (ConversationAgentError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise

    def get_by_idempotency_key(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        idempotency_key: str,
    ) -> WorkflowConfirmRequest | None:
        with self._connect() as conn:
            return _get_confirm_request_by_idempotency_key(
                conn,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            )

    def mark_approved(
        self,
        confirm_request_id: str,
        *,
        approved_requirement_revision_id: str,
        updated_at: str,
    ) -> WorkflowConfirmRequest:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT *
                    FROM wts_confirm_requirement_requests
                    WHERE confirm_request_id = ?
                    """,
                    (confirm_request_id,),
                ).fetchone()
                if row is None:
                    raise ConversationAgentError("confirm_request_not_found")
                existing = _confirm_request_from_row(row)
                if (
                    existing.approved_requirement_revision_id is not None
                    and existing.approved_requirement_revision_id != approved_requirement_revision_id
                ):
                    raise ConversationAgentError("idempotency_key_conflict")
                conn.execute(
                    """
                    UPDATE wts_confirm_requirement_requests
                    SET approved_requirement_revision_id = ?, status = 'approved', updated_at = ?
                    WHERE confirm_request_id = ? AND status IN ('pending', 'approved')
                    """,
                    (approved_requirement_revision_id, updated_at, confirm_request_id),
                )
                conn.commit()
            except (ConversationAgentError, sqlite3.Error):
                conn.rollback()
                raise
        return self.get(confirm_request_id)

    def mark_intent_created(self, confirm_request_id: str, *, updated_at: str) -> WorkflowConfirmRequest:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE wts_confirm_requirement_requests
                SET status = 'intent_created', updated_at = ?
                WHERE confirm_request_id = ? AND status IN ('pending', 'approved', 'intent_created')
                """,
                (updated_at, confirm_request_id),
            )
        return self.get(confirm_request_id)

    def get(self, confirm_request_id: str) -> WorkflowConfirmRequest:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_confirm_requirement_requests
                WHERE confirm_request_id = ?
                """,
                (confirm_request_id,),
            ).fetchone()
        if row is None:
            raise ConversationAgentError("confirm_request_not_found")
        return _confirm_request_from_row(row)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield conn
        finally:
            conn.close()


class WorkflowStartIntentStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def create_or_get_confirmed_draft_intent(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        conversation_id: str,
        draft_revision_id: str,
        approved_requirement_revision_id: str,
        job_request_revision_id: str,
        idempotency_key: str,
        request_hash: str,
        now: str,
    ) -> WorkflowStartIntent:
        deterministic_run_key = workflow_start_deterministic_run_key(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            draft_revision_id=draft_revision_id,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing_by_key = _get_by_idempotency_key(
                    conn,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    idempotency_key=idempotency_key,
                )
                if existing_by_key is not None:
                    if existing_by_key.request_hash != request_hash:
                        raise ConversationAgentError("idempotency_key_conflict")
                    _link_conversation_approved_requirement(
                        conn,
                        conversation_id=conversation_id,
                        approved_requirement_revision_id=existing_by_key.approved_requirement_revision_id,
                        updated_at=now,
                    )
                    conn.commit()
                    return existing_by_key

                existing_by_draft = _get_by_deterministic_run_key(
                    conn,
                    workspace_id=workspace_id,
                    deterministic_run_key=deterministic_run_key,
                )
                if existing_by_draft is not None:
                    _link_conversation_approved_requirement(
                        conn,
                        conversation_id=conversation_id,
                        approved_requirement_revision_id=existing_by_draft.approved_requirement_revision_id,
                        updated_at=now,
                    )
                    conn.commit()
                    return existing_by_draft

                intent = WorkflowStartIntent(
                    workflow_start_intent_id=f"wts_startintent_{uuid4().hex}",
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    conversation_id=conversation_id,
                    draft_revision_id=draft_revision_id,
                    approved_requirement_revision_id=approved_requirement_revision_id,
                    job_request_revision_id=job_request_revision_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    deterministic_run_key=deterministic_run_key,
                    status="pending",
                    runtime_run_id=None,
                    reason_code=None,
                    created_at=now,
                    updated_at=now,
                )
                conn.execute(
                    """
                    INSERT INTO wts_workflow_start_intents (
                        workflow_start_intent_id, workspace_id, owner_user_id, conversation_id,
                        draft_revision_id, approved_requirement_revision_id, job_request_revision_id,
                        idempotency_key, request_hash, deterministic_run_key, status, runtime_run_id,
                        reason_code, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.workflow_start_intent_id,
                        intent.workspace_id,
                        intent.owner_user_id,
                        intent.conversation_id,
                        intent.draft_revision_id,
                        intent.approved_requirement_revision_id,
                        intent.job_request_revision_id,
                        intent.idempotency_key,
                        intent.request_hash,
                        intent.deterministic_run_key,
                        intent.status,
                        intent.runtime_run_id,
                        intent.reason_code,
                        intent.created_at,
                        intent.updated_at,
                    ),
                )
                _insert_outbox_item(
                    conn,
                    workspace_id=workspace_id,
                    aggregate_id=intent.workflow_start_intent_id,
                    payload={
                        "workflowStartIntentId": intent.workflow_start_intent_id,
                        "conversationId": conversation_id,
                        "draftRevisionId": draft_revision_id,
                    },
                    now=now,
                )
                _link_conversation_approved_requirement(
                    conn,
                    conversation_id=conversation_id,
                    approved_requirement_revision_id=approved_requirement_revision_id,
                    updated_at=now,
                )
                conn.commit()
                return intent
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                return self._resolve_insert_conflict(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    draft_revision_id=draft_revision_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    deterministic_run_key=deterministic_run_key,
                    exc=exc,
                )
            except (ConversationAgentError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise

    def _resolve_insert_conflict(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        draft_revision_id: str,
        idempotency_key: str,
        request_hash: str,
        deterministic_run_key: str,
        exc: sqlite3.IntegrityError,
    ) -> WorkflowStartIntent:
        existing_by_key = self.get_by_idempotency_key(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing_by_key is not None:
            if existing_by_key.request_hash != request_hash:
                raise ConversationAgentError("idempotency_key_conflict") from exc
            return existing_by_key
        existing_by_draft = self.get_by_draft(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            draft_revision_id=draft_revision_id,
        )
        if existing_by_draft is not None:
            return existing_by_draft
        existing_by_run_key = self.get_by_deterministic_run_key(
            workspace_id=workspace_id,
            deterministic_run_key=deterministic_run_key,
        )
        if existing_by_run_key is not None:
            return existing_by_run_key
        raise ConversationAgentError("workflow_start_intent_conflict") from exc

    def get(self, workflow_start_intent_id: str) -> WorkflowStartIntent:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_workflow_start_intents
                WHERE workflow_start_intent_id = ?
                """,
                (workflow_start_intent_id,),
            ).fetchone()
        if row is None:
            raise ConversationAgentError("workflow_start_intent_not_found")
        return _intent_from_row(row)

    def get_by_idempotency_key(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        idempotency_key: str,
    ) -> WorkflowStartIntent | None:
        with self._connect() as conn:
            return _get_by_idempotency_key(
                conn,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                idempotency_key=idempotency_key,
            )

    def get_by_draft(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        draft_revision_id: str,
    ) -> WorkflowStartIntent | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_workflow_start_intents
                WHERE workspace_id = ? AND conversation_id = ? AND draft_revision_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (workspace_id, conversation_id, draft_revision_id),
            ).fetchone()
        return _intent_from_row(row) if row is not None else None

    def get_by_deterministic_run_key(
        self,
        *,
        workspace_id: str,
        deterministic_run_key: str,
    ) -> WorkflowStartIntent | None:
        with self._connect() as conn:
            return _get_by_deterministic_run_key(
                conn,
                workspace_id=workspace_id,
                deterministic_run_key=deterministic_run_key,
            )

    def get_latest_for_conversation(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
    ) -> WorkflowStartIntent | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_workflow_start_intents
                WHERE workspace_id = ? AND conversation_id = ?
                ORDER BY created_at DESC, workflow_start_intent_id DESC
                LIMIT 1
                """,
                (workspace_id, conversation_id),
            ).fetchone()
        return _intent_from_row(row) if row is not None else None

    def mark_started(
        self,
        workflow_start_intent_id: str,
        *,
        runtime_run_id: str,
        updated_at: str,
    ) -> WorkflowStartIntent:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE wts_workflow_start_intents
                SET status = 'started', runtime_run_id = ?, reason_code = NULL, updated_at = ?
                WHERE workflow_start_intent_id = ? AND status = 'pending'
                """,
                (runtime_run_id, updated_at, workflow_start_intent_id),
            )
        return self.get(workflow_start_intent_id)

    def mark_failed(
        self,
        workflow_start_intent_id: str,
        *,
        reason_code: str,
        updated_at: str,
    ) -> WorkflowStartIntent:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE wts_workflow_start_intents
                SET status = 'failed', reason_code = ?, updated_at = ?
                WHERE workflow_start_intent_id = ? AND status = 'pending'
                """,
                (reason_code, updated_at, workflow_start_intent_id),
            )
        return self.get(workflow_start_intent_id)

    def count_for_draft(self, draft_revision_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM wts_workflow_start_intents WHERE draft_revision_id = ?",
                (draft_revision_id,),
            ).fetchone()
        return int(row[0])

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield conn
        finally:
            conn.close()


class WorkbenchOutboxStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def get(self, outbox_id: str) -> WorkbenchOutboxItem:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_outbox
                WHERE outbox_id = ?
                """,
                (outbox_id,),
            ).fetchone()
        if row is None:
            raise ConversationAgentError("workbench_outbox_item_not_found")
        return _outbox_from_row(row)

    def claim_for_processing(
        self,
        outbox_id: str,
        *,
        claimed_at: str,
        reclaim_before: str | None,
    ) -> WorkbenchOutboxItem | None:
        status_predicate = "status = 'pending'"
        params: list[object] = [claimed_at, outbox_id]
        if reclaim_before is not None:
            status_predicate = "(status = 'pending' OR (status = 'in_progress' AND updated_at < ?))"
            params = [claimed_at, reclaim_before, outbox_id]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    f"""
                    UPDATE wts_outbox
                    SET status = 'in_progress', attempt_count = attempt_count + 1, updated_at = ?
                    WHERE {status_predicate} AND outbox_id = ?
                    """,
                    tuple(params),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return None
                row = conn.execute(
                    """
                    SELECT *
                    FROM wts_outbox
                    WHERE outbox_id = ?
                    """,
                    (outbox_id,),
                ).fetchone()
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
        if row is None:
            raise ConversationAgentError("workbench_outbox_item_not_found")
        return _outbox_from_row(row)

    def insert_once(
        self,
        *,
        workspace_id: str,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, object],
        initial_status: Literal["held", "pending"],
        now: str,
    ) -> WorkbenchOutboxItem:
        existing = self.get_for_aggregate(event_type=event_type, aggregate_id=aggregate_id)
        if existing is not None:
            return existing
        outbox_id = f"wts_outbox_{uuid4().hex}"
        with self._connect() as conn, conn:
            try:
                conn.execute(
                    """
                    INSERT INTO wts_outbox (
                        outbox_id, workspace_id, event_type, aggregate_id, payload_json,
                        status, attempt_count, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (outbox_id, workspace_id, event_type, aggregate_id, _json(payload), initial_status, now, now),
                )
            except sqlite3.IntegrityError:
                existing = self.get_for_aggregate(event_type=event_type, aggregate_id=aggregate_id)
                if existing is not None:
                    return existing
                raise
        return self.get(outbox_id)

    def get_for_aggregate(
        self,
        aggregate_id: str | None = None,
        *,
        event_type: str = "workflow_start_requested",
    ) -> WorkbenchOutboxItem | None:
        if aggregate_id is None:
            raise TypeError("aggregate_id is required")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_outbox
                WHERE event_type = ? AND aggregate_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (event_type, aggregate_id),
            ).fetchone()
        return _outbox_from_row(row) if row is not None else None

    def list_claimable_items(
        self,
        *,
        event_type: str,
        reclaim_before: str,
        limit: int,
    ) -> list[WorkbenchOutboxItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wts_outbox
                WHERE event_type = ?
                  AND (
                    status = 'pending'
                    OR (status = 'in_progress' AND updated_at < ?)
                  )
                ORDER BY created_at ASC, outbox_id ASC
                LIMIT ?
                """,
                (event_type, reclaim_before, limit),
            ).fetchall()
        return [_outbox_from_row(row) for row in rows]

    def list_claimable_workflow_start_items(
        self,
        *,
        reclaim_before: str,
        limit: int,
    ) -> list[WorkbenchOutboxItem]:
        return self.list_claimable_items(
            event_type="workflow_start_requested",
            reclaim_before=reclaim_before,
            limit=limit,
        )

    def release_held_item(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        now: str,
    ) -> WorkbenchOutboxItem:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE wts_outbox
                SET status = 'pending', updated_at = ?
                WHERE event_type = ? AND aggregate_id = ? AND status = 'held'
                """,
                (now, event_type, aggregate_id),
            )
        item = self.get_for_aggregate(event_type=event_type, aggregate_id=aggregate_id)
        if item is None:
            raise ConversationAgentError("workbench_outbox_item_not_found")
        return item

    def mark_done(self, outbox_id: str, *, updated_at: str) -> WorkbenchOutboxItem:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE wts_outbox
                SET status = 'done', updated_at = ?
                WHERE outbox_id = ? AND status IN ('in_progress', 'done')
                """,
                (updated_at, outbox_id),
            )
        return self.get(outbox_id)

    def mark_pending_retry(self, outbox_id: str, *, updated_at: str) -> WorkbenchOutboxItem:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE wts_outbox
                SET status = 'pending', updated_at = ?
                WHERE outbox_id = ? AND status = 'in_progress'
                """,
                (updated_at, outbox_id),
            )
        return self.get(outbox_id)

    def count_for_aggregate(self, aggregate_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM wts_outbox WHERE aggregate_id = ?",
                (aggregate_id,),
            ).fetchone()
        return int(row[0])

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield conn
        finally:
            conn.close()


def workflow_start_deterministic_run_key(
    *,
    workspace_id: str,
    conversation_id: str,
    draft_revision_id: str,
) -> str:
    return f"wts:{workspace_id}:{conversation_id}:{draft_revision_id}"


def workflow_start_request_hash(
    *,
    draft_revision_id: str,
    expected_draft_revision_id: str,
    approved_requirement_revision_id: str,
    job_request_revision_id: str,
    job_request_request_hash: str,
    source_kinds: list[SourceKind],
    workspace_source_policy_id: str | None,
) -> str:
    payload = {
        "schema": "wts.workflow_start_intent.request_hash.v3",
        "draftRevisionId": draft_revision_id,
        "expectedDraftRevisionId": expected_draft_revision_id,
        "approvedRequirementRevisionId": approved_requirement_revision_id,
        "jobRequestRevisionId": job_request_revision_id,
        "jobRequestRequestHash": job_request_request_hash,
        "sourceKinds": list(source_kinds),
        "workspaceSourcePolicyId": workspace_source_policy_id,
    }
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def workflow_confirm_request_hash(
    *,
    draft_revision_id: str,
    expected_draft_revision_id: str,
    job_request_revision_id: str,
    job_request_request_hash: str,
    source_kinds: list[SourceKind],
    workspace_source_policy_id: str | None,
) -> str:
    payload = {
        "schema": "wts.confirm_requirement_request.request_hash.v1",
        "draftRevisionId": draft_revision_id,
        "expectedDraftRevisionId": expected_draft_revision_id,
        "jobRequestRevisionId": job_request_revision_id,
        "jobRequestRequestHash": job_request_request_hash,
        "sourceKinds": list(source_kinds),
        "workspaceSourcePolicyId": workspace_source_policy_id,
    }
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def _get_confirm_request_by_idempotency_key(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    conversation_id: str,
    idempotency_key: str,
) -> WorkflowConfirmRequest | None:
    row = conn.execute(
        """
        SELECT *
        FROM wts_confirm_requirement_requests
        WHERE workspace_id = ? AND conversation_id = ? AND idempotency_key = ?
        """,
        (workspace_id, conversation_id, idempotency_key),
    ).fetchone()
    return _confirm_request_from_row(row) if row is not None else None


def _get_by_idempotency_key(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    conversation_id: str,
    idempotency_key: str,
) -> WorkflowStartIntent | None:
    row = conn.execute(
        """
        SELECT *
        FROM wts_workflow_start_intents
        WHERE workspace_id = ? AND conversation_id = ? AND idempotency_key = ?
        """,
        (workspace_id, conversation_id, idempotency_key),
    ).fetchone()
    return _intent_from_row(row) if row is not None else None


def _get_by_deterministic_run_key(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    deterministic_run_key: str,
) -> WorkflowStartIntent | None:
    row = conn.execute(
        """
        SELECT *
        FROM wts_workflow_start_intents
        WHERE workspace_id = ? AND deterministic_run_key = ?
        """,
        (workspace_id, deterministic_run_key),
    ).fetchone()
    return _intent_from_row(row) if row is not None else None


def _insert_outbox_item(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    aggregate_id: str,
    payload: dict[str, object],
    now: str,
) -> None:
    existing = conn.execute(
        """
        SELECT outbox_id
        FROM wts_outbox
        WHERE event_type = 'workflow_start_requested' AND aggregate_id = ?
        LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO wts_outbox (
            outbox_id, workspace_id, event_type, aggregate_id, payload_json,
            status, attempt_count, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"wts_outbox_{uuid4().hex}",
            workspace_id,
            "workflow_start_requested",
            aggregate_id,
            _json(payload),
            "pending",
            0,
            now,
            now,
        ),
    )


def _link_conversation_approved_requirement(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    approved_requirement_revision_id: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        UPDATE agent_conversations
        SET approved_requirement_revision_id = ?, pending_requirement_review_count = 0, updated_at = ?
        WHERE conversation_id = ?
        """,
        (approved_requirement_revision_id, updated_at, conversation_id),
    )


def _confirm_request_from_row(row: sqlite3.Row) -> WorkflowConfirmRequest:
    return WorkflowConfirmRequest(
        confirm_request_id=row["confirm_request_id"],
        workspace_id=row["workspace_id"],
        owner_user_id=row["owner_user_id"],
        conversation_id=row["conversation_id"],
        draft_revision_id=row["draft_revision_id"],
        expected_draft_revision_id=row["expected_draft_revision_id"],
        job_request_revision_id=row["job_request_revision_id"],
        approved_requirement_revision_id=row["approved_requirement_revision_id"],
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _intent_from_row(row: sqlite3.Row) -> WorkflowStartIntent:
    return WorkflowStartIntent(
        workflow_start_intent_id=row["workflow_start_intent_id"],
        workspace_id=row["workspace_id"],
        owner_user_id=row["owner_user_id"],
        conversation_id=row["conversation_id"],
        draft_revision_id=row["draft_revision_id"],
        approved_requirement_revision_id=row["approved_requirement_revision_id"],
        job_request_revision_id=row["job_request_revision_id"],
        idempotency_key=row["idempotency_key"],
        request_hash=row["request_hash"],
        deterministic_run_key=row["deterministic_run_key"],
        status=row["status"],
        runtime_run_id=row["runtime_run_id"],
        reason_code=row["reason_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _outbox_from_row(row: sqlite3.Row) -> WorkbenchOutboxItem:
    return WorkbenchOutboxItem(
        outbox_id=row["outbox_id"],
        workspace_id=row["workspace_id"],
        event_type=row["event_type"],
        aggregate_id=row["aggregate_id"],
        payload=_json_object(row["payload_json"]),
        status=row["status"],
        attempt_count=int(row["attempt_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ConversationAgentError("workbench_outbox_payload_invalid")
    return payload
