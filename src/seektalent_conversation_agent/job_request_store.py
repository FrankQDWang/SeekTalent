from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.job_requests import (
    JobRequestRevision,
    RequirementDraftJobRequestLink,
    SourceKind,
    normalize_source_kinds,
)


class JobRequestStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = _normalize_busy_timeout_ms(busy_timeout_ms)

    def insert_job_request_revision(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        jd_text: str,
        user_job_title: str | None,
        extracted_job_title: str | None,
        notes: str | None,
        source_kinds: list[str],
        workspace_source_policy_id: str | None,
        idempotency_key: str,
        created_at: str,
    ) -> JobRequestRevision:
        normalized_source_kinds = normalize_source_kinds(source_kinds)
        request_hash = job_request_hash(
            user_job_title=user_job_title,
            jd_text=jd_text,
            notes=notes,
            source_kinds=normalized_source_kinds,
            workspace_source_policy_id=workspace_source_policy_id,
        )
        existing = self.get_job_request_revision_by_idempotency(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            _raise_if_idempotency_conflict(existing, request_hash)
            return existing
        existing = self.get_job_request_revision_by_request_hash(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            request_hash=request_hash,
        )
        if existing is not None:
            if existing.idempotency_key != idempotency_key:
                raise ConversationAgentError("idempotency_key_conflict")
            return existing

        revision = JobRequestRevision(
            job_request_revision_id=f"wts_jobreq_{uuid4().hex}",
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            jd_text=jd_text,
            user_job_title=user_job_title,
            extracted_job_title=extracted_job_title,
            notes=notes,
            source_kinds=normalized_source_kinds,
            workspace_source_policy_id=workspace_source_policy_id,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            created_at=created_at,
            updated_at=created_at,
        )
        with self._connect() as conn, conn:
            try:
                conn.execute(
                    """
                    INSERT INTO wts_job_request_revisions (
                        job_request_revision_id, workspace_id, owner_user_id, conversation_id,
                        jd_text, user_job_title, extracted_job_title, notes, source_kinds_json,
                        workspace_source_policy_id, request_hash, idempotency_key, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision.job_request_revision_id,
                        revision.workspace_id,
                        revision.owner_user_id,
                        revision.conversation_id,
                        revision.jd_text,
                        revision.user_job_title,
                        revision.extracted_job_title,
                        revision.notes,
                        _json(revision.source_kinds),
                        revision.workspace_source_policy_id,
                        revision.request_hash,
                        revision.idempotency_key,
                        revision.created_at,
                        revision.updated_at,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get_job_request_revision_by_idempotency(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    _raise_if_idempotency_conflict(existing, request_hash)
                    return existing
                existing = self.get_job_request_revision_by_request_hash(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    request_hash=request_hash,
                )
                if existing is not None:
                    if existing.idempotency_key != idempotency_key:
                        raise ConversationAgentError("idempotency_key_conflict")
                    return existing
                raise
        return revision

    def update_extracted_job_title(
        self,
        *,
        job_request_revision_id: str,
        extracted_job_title: str | None,
        updated_at: str,
    ) -> JobRequestRevision:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE wts_job_request_revisions
                SET extracted_job_title = ?, updated_at = ?
                WHERE job_request_revision_id = ?
                """,
                (extracted_job_title, updated_at, job_request_revision_id),
            )
        revision = self.get_job_request_revision(job_request_revision_id)
        if revision is None:
            raise ConversationAgentError("job_request_revision_not_found")
        return revision

    def get_job_request_revision(self, job_request_revision_id: str) -> JobRequestRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_job_request_revisions
                WHERE job_request_revision_id = ?
                """,
                (job_request_revision_id,),
            ).fetchone()
        return _job_request_revision_from_row(row) if row is not None else None

    def get_job_request_revision_by_idempotency(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        idempotency_key: str,
    ) -> JobRequestRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_job_request_revisions
                WHERE workspace_id = ? AND conversation_id = ? AND idempotency_key = ?
                """,
                (workspace_id, conversation_id, idempotency_key),
            ).fetchone()
        return _job_request_revision_from_row(row) if row is not None else None

    def get_job_request_revision_by_request_hash(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        request_hash: str,
    ) -> JobRequestRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_job_request_revisions
                WHERE workspace_id = ? AND conversation_id = ? AND request_hash = ?
                """,
                (workspace_id, conversation_id, request_hash),
            ).fetchone()
        return _job_request_revision_from_row(row) if row is not None else None

    def link_requirement_draft_job_request(
        self,
        *,
        draft_revision_id: str,
        workspace_id: str,
        job_request_revision_id: str,
        conversation_id: str,
        created_at: str,
    ) -> RequirementDraftJobRequestLink:
        existing = self.get_requirement_draft_job_request_link(draft_revision_id)
        if existing is not None:
            if existing.job_request_revision_id != job_request_revision_id:
                raise ConversationAgentError("requirement_draft_job_request_conflict")
            return existing
        link = RequirementDraftJobRequestLink(
            draft_revision_id=draft_revision_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            job_request_revision_id=job_request_revision_id,
            created_at=created_at,
        )
        with self._connect() as conn, conn:
            try:
                conn.execute(
                    """
                    INSERT INTO wts_requirement_draft_job_requests (
                        draft_revision_id, workspace_id, conversation_id, job_request_revision_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        link.draft_revision_id,
                        link.workspace_id,
                        link.conversation_id,
                        link.job_request_revision_id,
                        link.created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.get_requirement_draft_job_request_link(draft_revision_id)
                if existing is None:
                    raise
                if existing.job_request_revision_id != job_request_revision_id:
                    raise ConversationAgentError("requirement_draft_job_request_conflict")
                return existing
        return link

    def get_requirement_draft_job_request_link(
        self,
        draft_revision_id: str,
    ) -> RequirementDraftJobRequestLink | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_requirement_draft_job_requests
                WHERE draft_revision_id = ?
                """,
                (draft_revision_id,),
            ).fetchone()
        return _requirement_draft_job_request_link_from_row(row) if row is not None else None

    def get_requirement_draft_job_request_link_by_job_request(
        self,
        job_request_revision_id: str,
    ) -> RequirementDraftJobRequestLink | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wts_requirement_draft_job_requests
                WHERE job_request_revision_id = ?
                ORDER BY created_at DESC, draft_revision_id DESC
                LIMIT 1
                """,
                (job_request_revision_id,),
            ).fetchone()
        return _requirement_draft_job_request_link_from_row(row) if row is not None else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except (sqlite3.Error, ConversationAgentError, RuntimeError, TypeError, ValueError):
            conn.rollback()
            raise
        finally:
            conn.close()


def job_request_hash(
    *,
    user_job_title: str | None,
    jd_text: str,
    notes: str | None,
    source_kinds: list[SourceKind],
    workspace_source_policy_id: str | None,
) -> str:
    payload = {
        "schema": "wts.job_request_revision.request_hash.v1",
        "userJobTitle": user_job_title,
        "jdText": jd_text,
        "notes": notes,
        "sourceKinds": list(source_kinds),
        "workspaceSourcePolicyId": workspace_source_policy_id,
    }
    return sha256(_json(payload).encode("utf-8")).hexdigest()


def _raise_if_idempotency_conflict(existing: JobRequestRevision, request_hash: str) -> None:
    if existing.request_hash != request_hash:
        raise ConversationAgentError(
            "idempotency_key_conflict",
            payload={"jobRequestRevisionId": existing.job_request_revision_id},
        )


def _job_request_revision_from_row(row: sqlite3.Row) -> JobRequestRevision:
    source_kinds = normalize_source_kinds(_json_list(row["source_kinds_json"]))
    return JobRequestRevision(
        job_request_revision_id=row["job_request_revision_id"],
        conversation_id=row["conversation_id"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        jd_text=row["jd_text"],
        user_job_title=row["user_job_title"],
        extracted_job_title=row["extracted_job_title"],
        notes=row["notes"],
        source_kinds=source_kinds,
        workspace_source_policy_id=row["workspace_source_policy_id"],
        request_hash=row["request_hash"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _requirement_draft_job_request_link_from_row(row: sqlite3.Row) -> RequirementDraftJobRequestLink:
    return RequirementDraftJobRequestLink(
        draft_revision_id=row["draft_revision_id"],
        workspace_id=row["workspace_id"],
        conversation_id=row["conversation_id"],
        job_request_revision_id=row["job_request_revision_id"],
        created_at=row["created_at"],
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_busy_timeout_ms(value: int) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("busy_timeout_ms must be an integer") from exc
    if timeout < 0:
        raise ValueError("busy_timeout_ms must be non-negative")
    return timeout


def _json_list(value: str) -> list[str]:
    payload = json.loads(value)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ConversationAgentError("job_request_source_kinds_invalid")
    return payload
