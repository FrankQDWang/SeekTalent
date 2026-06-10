from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from seektalent_agent_memory.models import (
    MemoryCandidate,
    MemoryClearResult,
    MemoryFact,
    MemoryJob,
    MemoryJobClaim,
    MemoryRetentionCleanupResult,
    MemorySettings,
    MemorySummary,
    MemoryUsage,
    Stage1Output,
)


AGENT_MEMORY_SCHEMA_VERSION = 2
_PHASE2_JOB_KEY = "__global__"


class MemoryStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > AGENT_MEMORY_SCHEMA_VERSION:
                raise RuntimeError("agent_memory_schema_unsupported")
            with conn:
                if version == 0:
                    _create_schema(conn)
                elif version == 1:
                    _migrate_v1_to_v2(conn)
                else:
                    _ensure_v2_columns(conn)
                conn.execute(f"PRAGMA user_version = {AGENT_MEMORY_SCHEMA_VERSION}")

    def get_settings(self, *, owner_user_id: str, workspace_id: str, now: str) -> MemorySettings:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM agent_memory_settings
                WHERE owner_user_id = ? AND workspace_id = ?
                """,
                (owner_user_id, workspace_id),
            ).fetchone()
        if row is not None:
            return _settings_from_row(row)
        return MemorySettings(owner_user_id=owner_user_id, workspace_id=workspace_id, updated_at=now)

    def update_settings(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        memory_enabled: bool,
        review_required: bool,
        updated_at: str,
        generation_enabled: bool | None = None,
        recall_enabled: bool | None = None,
        candidate_retention_days: int | None = None,
        rejected_retention_days: int | None = None,
        source_excerpt_retention_days: int | None = None,
    ) -> MemorySettings:
        current = self.get_settings(owner_user_id=owner_user_id, workspace_id=workspace_id, now=updated_at)
        settings = current.model_copy(
            update={
                "memory_enabled": memory_enabled,
                "generation_enabled": current.generation_enabled if generation_enabled is None else generation_enabled,
                "recall_enabled": current.recall_enabled if recall_enabled is None else recall_enabled,
                "review_required": review_required,
                "candidate_retention_days": (
                    current.candidate_retention_days
                    if candidate_retention_days is None
                    else candidate_retention_days
                ),
                "rejected_retention_days": (
                    current.rejected_retention_days if rejected_retention_days is None else rejected_retention_days
                ),
                "source_excerpt_retention_days": (
                    current.source_excerpt_retention_days
                    if source_excerpt_retention_days is None
                    else source_excerpt_retention_days
                ),
                "updated_at": updated_at,
            }
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_memory_settings (
                    owner_user_id, workspace_id, memory_enabled, generation_enabled, recall_enabled,
                    review_required, max_rollouts_per_startup, max_rollout_age_days, min_rollout_idle_hours,
                    max_stage1_outputs_for_phase2, max_unused_days, summary_token_budget,
                    candidate_retention_days, rejected_retention_days, source_excerpt_retention_days, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, workspace_id) DO UPDATE SET
                    memory_enabled = excluded.memory_enabled,
                    generation_enabled = excluded.generation_enabled,
                    recall_enabled = excluded.recall_enabled,
                    review_required = excluded.review_required,
                    max_rollouts_per_startup = excluded.max_rollouts_per_startup,
                    max_rollout_age_days = excluded.max_rollout_age_days,
                    min_rollout_idle_hours = excluded.min_rollout_idle_hours,
                    max_stage1_outputs_for_phase2 = excluded.max_stage1_outputs_for_phase2,
                    max_unused_days = excluded.max_unused_days,
                    summary_token_budget = excluded.summary_token_budget,
                    candidate_retention_days = excluded.candidate_retention_days,
                    rejected_retention_days = excluded.rejected_retention_days,
                    source_excerpt_retention_days = excluded.source_excerpt_retention_days,
                    updated_at = excluded.updated_at
                """,
                (
                    settings.owner_user_id,
                    settings.workspace_id,
                    int(settings.memory_enabled),
                    int(settings.generation_enabled),
                    int(settings.recall_enabled),
                    int(settings.review_required),
                    settings.max_rollouts_per_startup,
                    settings.max_rollout_age_days,
                    settings.min_rollout_idle_hours,
                    settings.max_stage1_outputs_for_phase2,
                    settings.max_unused_days,
                    settings.summary_token_budget,
                    settings.candidate_retention_days,
                    settings.rejected_retention_days,
                    settings.source_excerpt_retention_days,
                    settings.updated_at,
                ),
            )
        return settings

    def try_claim_stage1_job(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        worker_id: str,
        source_updated_at: str,
        now: str,
        lease_seconds: int,
        max_running_jobs: int,
    ) -> MemoryJobClaim:
        if self._stage1_output_up_to_date(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            source_updated_at=source_updated_at,
        ):
            return MemoryJobClaim(status="skipped_up_to_date", reason_code="agent_memory_stage1_up_to_date")
        with self._connect_immediate() as conn:
            try:
                row = _job_row(conn, "stage1", conversation_id, owner_user_id, workspace_id)
                if row is not None:
                    claim = _claim_blocked_by_existing_job(row, now=now)
                    if claim is not None:
                        conn.rollback()
                        return claim
                running_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM agent_memory_jobs
                    WHERE kind = 'stage1' AND owner_user_id = ? AND workspace_id = ?
                      AND status = 'running' AND lease_until > ?
                    """,
                    (owner_user_id, workspace_id, now),
                ).fetchone()[0]
                if int(running_count) >= max_running_jobs:
                    conn.rollback()
                    return MemoryJobClaim(status="skipped_running_cap", reason_code="agent_memory_job_running")
                ownership_token = uuid4().hex
                lease_until = _add_seconds(now, lease_seconds)
                retry_remaining = int(row["retry_remaining"]) if row is not None else 3
                conn.execute(
                    """
                    INSERT INTO agent_memory_jobs (
                        kind, job_key, owner_user_id, workspace_id, status, worker_id, ownership_token,
                        started_at, finished_at, lease_until, retry_at, retry_remaining, last_error_code,
                        input_watermark, last_success_watermark
                    )
                    VALUES (?, ?, ?, ?, 'running', ?, ?, ?, NULL, ?, NULL, ?, NULL, ?, ?)
                    ON CONFLICT(kind, job_key, owner_user_id, workspace_id) DO UPDATE SET
                        status = 'running',
                        worker_id = excluded.worker_id,
                        ownership_token = excluded.ownership_token,
                        started_at = excluded.started_at,
                        finished_at = NULL,
                        lease_until = excluded.lease_until,
                        retry_at = NULL,
                        retry_remaining = excluded.retry_remaining,
                        last_error_code = NULL,
                        input_watermark = excluded.input_watermark
                    """,
                    (
                        "stage1",
                        conversation_id,
                        owner_user_id,
                        workspace_id,
                        worker_id,
                        ownership_token,
                        now,
                        lease_until,
                        retry_remaining,
                        source_updated_at,
                        row["last_success_watermark"] if row is not None else None,
                    ),
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
        return MemoryJobClaim(status="claimed", ownership_token=ownership_token)

    def mark_stage1_job_succeeded(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        ownership_token: str,
        source_updated_at: str,
        now: str,
    ) -> bool:
        return self._mark_job_terminal(
            kind="stage1",
            job_key=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            ownership_token=ownership_token,
            status="succeeded",
            now=now,
            last_success_watermark=source_updated_at,
            last_error_code=None,
        )

    def mark_stage1_job_succeeded_no_output(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        ownership_token: str,
        source_updated_at: str,
        now: str,
    ) -> bool:
        return self._mark_job_terminal(
            kind="stage1",
            job_key=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            ownership_token=ownership_token,
            status="succeeded_no_output",
            now=now,
            last_success_watermark=source_updated_at,
            last_error_code="agent_memory_stage1_no_output",
        )

    def mark_stage1_job_failed(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        ownership_token: str,
        error_code: str,
        now: str,
        retry_delay_seconds: int,
    ) -> bool:
        with self._connect() as conn, conn:
            row = _job_row(conn, "stage1", conversation_id, owner_user_id, workspace_id)
            if row is None or row["ownership_token"] != ownership_token:
                return False
            retry_remaining = max(0, int(row["retry_remaining"]) - 1)
            conn.execute(
                """
                UPDATE agent_memory_jobs
                SET status = 'failed', finished_at = ?, ownership_token = NULL, worker_id = NULL,
                    lease_until = NULL, retry_at = ?, retry_remaining = ?, last_error_code = ?
                WHERE kind = 'stage1' AND job_key = ? AND owner_user_id = ? AND workspace_id = ?
                """,
                (
                    now,
                    _add_seconds(now, retry_delay_seconds),
                    retry_remaining,
                    error_code,
                    conversation_id,
                    owner_user_id,
                    workspace_id,
                ),
            )
        return True

    def try_claim_phase2_job(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        worker_id: str,
        now: str,
        lease_seconds: int,
    ) -> MemoryJobClaim:
        with self._connect_immediate() as conn:
            try:
                row = _job_row(conn, "phase2", _PHASE2_JOB_KEY, owner_user_id, workspace_id)
                if row is not None:
                    claim = _claim_blocked_by_existing_job(row, now=now)
                    if claim is not None:
                        conn.rollback()
                        return claim
                ownership_token = uuid4().hex
                conn.execute(
                    """
                    INSERT INTO agent_memory_jobs (
                        kind, job_key, owner_user_id, workspace_id, status, worker_id, ownership_token,
                        started_at, finished_at, lease_until, retry_at, retry_remaining, last_error_code,
                        input_watermark, last_success_watermark
                    )
                    VALUES ('phase2', ?, ?, ?, 'running', ?, ?, ?, NULL, ?, NULL, 3, NULL, NULL, NULL)
                    ON CONFLICT(kind, job_key, owner_user_id, workspace_id) DO UPDATE SET
                        status = 'running',
                        worker_id = excluded.worker_id,
                        ownership_token = excluded.ownership_token,
                        started_at = excluded.started_at,
                        finished_at = NULL,
                        lease_until = excluded.lease_until,
                        retry_at = NULL,
                        last_error_code = NULL
                    """,
                    (
                        _PHASE2_JOB_KEY,
                        owner_user_id,
                        workspace_id,
                        worker_id,
                        ownership_token,
                        now,
                        _add_seconds(now, lease_seconds),
                    ),
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
        return MemoryJobClaim(status="claimed", ownership_token=ownership_token)

    def heartbeat_phase2_job(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        ownership_token: str,
        now: str,
        lease_seconds: int,
    ) -> bool:
        with self._connect() as conn, conn:
            cursor = conn.execute(
                """
                UPDATE agent_memory_jobs
                SET lease_until = ?
                WHERE kind = 'phase2' AND job_key = ? AND owner_user_id = ? AND workspace_id = ?
                  AND status = 'running' AND ownership_token = ?
                """,
                (_add_seconds(now, lease_seconds), _PHASE2_JOB_KEY, owner_user_id, workspace_id, ownership_token),
            )
        return cursor.rowcount == 1

    def mark_phase2_job_succeeded(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        ownership_token: str,
        now: str,
    ) -> bool:
        return self._mark_job_terminal(
            kind="phase2",
            job_key=_PHASE2_JOB_KEY,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            ownership_token=ownership_token,
            status="succeeded",
            now=now,
            last_success_watermark=now,
            last_error_code=None,
        )

    def mark_phase2_job_failed(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        ownership_token: str,
        error_code: str,
        now: str,
    ) -> bool:
        return self._mark_job_terminal(
            kind="phase2",
            job_key=_PHASE2_JOB_KEY,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            ownership_token=ownership_token,
            status="failed",
            now=now,
            last_success_watermark=None,
            last_error_code=error_code,
        )

    def list_jobs(self, *, owner_user_id: str, workspace_id: str) -> list[MemoryJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_memory_jobs
                WHERE owner_user_id = ? AND workspace_id = ?
                ORDER BY kind ASC, job_key ASC
                """,
                (owner_user_id, workspace_id),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def save_stage1_output(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        source_updated_at: str,
        raw_memory: str,
        rollout_summary: str,
        rollout_slug: str | None,
        generated_at: str,
        privacy_review_json: dict[str, object],
        source_message_ids: list[str],
        source_activity_ids: list[str],
    ) -> Stage1Output:
        output = Stage1Output(
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            source_updated_at=source_updated_at,
            raw_memory=raw_memory,
            rollout_summary=rollout_summary,
            rollout_slug=rollout_slug,
            generated_at=generated_at,
            privacy_review_json=privacy_review_json,
            source_message_ids=source_message_ids,
            source_activity_ids=source_activity_ids,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_memory_stage1_outputs (
                    conversation_id, owner_user_id, workspace_id, source_updated_at, raw_memory,
                    rollout_summary, rollout_slug, generated_at, usage_count, last_usage,
                    selected_for_phase2, selected_for_phase2_source_updated_at, privacy_review_json,
                    source_message_ids_json, source_activity_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 0, NULL, ?, ?, ?)
                ON CONFLICT(conversation_id, owner_user_id, workspace_id) DO UPDATE SET
                    source_updated_at = excluded.source_updated_at,
                    raw_memory = excluded.raw_memory,
                    rollout_summary = excluded.rollout_summary,
                    rollout_slug = excluded.rollout_slug,
                    generated_at = excluded.generated_at,
                    privacy_review_json = excluded.privacy_review_json,
                    source_message_ids_json = excluded.source_message_ids_json,
                    source_activity_ids_json = excluded.source_activity_ids_json
                """,
                (
                    output.conversation_id,
                    output.owner_user_id,
                    output.workspace_id,
                    output.source_updated_at,
                    output.raw_memory,
                    output.rollout_summary,
                    output.rollout_slug,
                    output.generated_at,
                    _json(output.privacy_review_json),
                    _json(output.source_message_ids),
                    _json(output.source_activity_ids),
                ),
            )
        return output

    def get_phase2_input_selection(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        limit: int,
    ) -> list[Stage1Output]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_memory_stage1_outputs
                WHERE owner_user_id = ? AND workspace_id = ?
                  AND (raw_memory != '' OR rollout_summary != '')
                  AND selected_for_phase2 = 0
                ORDER BY usage_count DESC,
                    COALESCE(last_usage, source_updated_at) DESC,
                    source_updated_at DESC,
                    conversation_id DESC
                LIMIT ?
                """,
                (owner_user_id, workspace_id, limit),
            ).fetchall()
        return sorted((_stage1_from_row(row) for row in rows), key=lambda item: item.conversation_id)

    def mark_stage1_outputs_selected_for_phase2(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        conversation_ids: list[str],
        selected_at: str,
    ) -> None:
        if not conversation_ids:
            return
        sql_marks = ",".join("?" for _ in conversation_ids)
        with self._connect() as conn, conn:
            conn.execute(
                f"""
                UPDATE agent_memory_stage1_outputs
                SET selected_for_phase2 = 1, selected_for_phase2_source_updated_at = source_updated_at
                WHERE owner_user_id = ? AND workspace_id = ? AND conversation_id IN ({sql_marks})
                """,
                [owner_user_id, workspace_id, *conversation_ids],
            )

    def prune_stage1_outputs_for_retention(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        max_unused_days: int,
        limit: int,
        now: str,
    ) -> int:
        cutoff = _add_days(now, -max_unused_days)
        with self._connect() as conn, conn:
            rows = conn.execute(
                """
                SELECT conversation_id FROM agent_memory_stage1_outputs
                WHERE owner_user_id = ? AND workspace_id = ?
                  AND selected_for_phase2 = 0
                  AND COALESCE(last_usage, source_updated_at) < ?
                ORDER BY COALESCE(last_usage, source_updated_at) ASC, conversation_id ASC
                LIMIT ?
                """,
                (owner_user_id, workspace_id, cutoff, limit),
            ).fetchall()
            ids = [row["conversation_id"] for row in rows]
            if ids:
                sql_marks = ",".join("?" for _ in ids)
                conn.execute(
                    f"""
                    DELETE FROM agent_memory_stage1_outputs
                    WHERE owner_user_id = ? AND workspace_id = ? AND conversation_id IN ({sql_marks})
                    """,
                    [owner_user_id, workspace_id, *ids],
                )
        return len(ids)

    def save_candidate(
        self,
        *,
        candidate_id: str,
        owner_user_id: str,
        workspace_id: str,
        conversation_id: str,
        category: str,
        text: str,
        safe_excerpt: str,
        source_message_ids: list[str],
        status: str,
        reason_code: str | None,
        created_at: str,
        raw_candidate_hash: str | None = None,
        safe_candidate_text: str | None = None,
        safe_evidence_excerpt: str | None = None,
        privacy_review_json: dict[str, object] | None = None,
        confidence: float | None = None,
        source_stage1_conversation_id: str | None = None,
        source_activity_ids: list[str] | None = None,
        expires_at: str | None = None,
    ) -> MemoryCandidate:
        candidate = MemoryCandidate(
            candidate_id=candidate_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            category=category,
            text=text,
            safe_excerpt=safe_excerpt,
            source_message_ids=source_message_ids,
            status=status,
            reason_code=reason_code,
            created_at=created_at,
            raw_candidate_hash=raw_candidate_hash,
            safe_candidate_text=safe_candidate_text or text,
            safe_evidence_excerpt=safe_evidence_excerpt or safe_excerpt,
            privacy_review_json=privacy_review_json or {},
            confidence=confidence,
            source_stage1_conversation_id=source_stage1_conversation_id,
            source_activity_ids=source_activity_ids or [],
            expires_at=expires_at,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_memory_candidates (
                    candidate_id, owner_user_id, workspace_id, conversation_id, category, text,
                    safe_excerpt, source_message_ids_json, status, reason_code, created_at, reviewed_at,
                    accepted_fact_id, raw_candidate_hash, safe_candidate_text, safe_evidence_excerpt,
                    privacy_review_json, confidence, source_stage1_conversation_id,
                    source_activity_ids_json, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    candidate.owner_user_id,
                    candidate.workspace_id,
                    candidate.conversation_id,
                    candidate.category,
                    candidate.text,
                    candidate.safe_excerpt,
                    _json(candidate.source_message_ids),
                    candidate.status,
                    candidate.reason_code,
                    candidate.created_at,
                    candidate.reviewed_at,
                    candidate.accepted_fact_id,
                    candidate.raw_candidate_hash,
                    candidate.safe_candidate_text,
                    candidate.safe_evidence_excerpt,
                    _json(candidate.privacy_review_json),
                    candidate.confidence,
                    candidate.source_stage1_conversation_id,
                    _json(candidate.source_activity_ids),
                    candidate.expires_at,
                ),
            )
        return candidate

    def list_candidates(self, *, owner_user_id: str, workspace_id: str, status: str | None = None) -> list[MemoryCandidate]:
        sql = """
            SELECT * FROM agent_memory_candidates
            WHERE owner_user_id = ? AND workspace_id = ?
        """
        params: list[object] = [owner_user_id, workspace_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC, candidate_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_candidate_from_row(row) for row in rows]

    def accept_candidate(
        self,
        *,
        candidate_id: str,
        owner_user_id: str,
        workspace_id: str,
        accepted_text: str,
        accepted_at: str,
        fact_id: str | None = None,
        expires_at: str | None = None,
    ) -> MemoryFact:
        with self._connect() as conn, conn:
            row = _candidate_row(conn, candidate_id, owner_user_id, workspace_id)
            if row is None:
                raise RuntimeError("agent_memory_candidate_not_found")
            fact = MemoryFact(
                fact_id=fact_id or f"memfact_{uuid4().hex}",
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                category=row["category"],
                text=accepted_text,
                source_candidate_id=candidate_id,
                source_conversation_ids=[row["conversation_id"]],
                source_message_ids=_loads_list(row["source_message_ids_json"]),
                created_at=accepted_at,
                updated_at=accepted_at,
                expires_at=expires_at,
                confidence=row["confidence"],
                safe_evidence_excerpt=row["safe_evidence_excerpt"],
                source_stage1_conversation_ids=[row["source_stage1_conversation_id"]]
                if row["source_stage1_conversation_id"]
                else [],
            )
            conn.execute(
                """
                INSERT INTO agent_memory_facts (
                    fact_id, owner_user_id, workspace_id, category, text, source_candidate_id,
                    source_conversation_ids_json, source_message_ids_json, status, created_at,
                    updated_at, expires_at, deleted_at, confidence, safe_evidence_excerpt,
                    source_stage1_conversation_ids_json, last_used_at, usage_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.fact_id,
                    fact.owner_user_id,
                    fact.workspace_id,
                    fact.category,
                    fact.text,
                    fact.source_candidate_id,
                    _json(fact.source_conversation_ids),
                    _json(fact.source_message_ids),
                    fact.status,
                    fact.created_at,
                    fact.updated_at,
                    fact.expires_at,
                    fact.deleted_at,
                    fact.confidence,
                    fact.safe_evidence_excerpt,
                    _json(fact.source_stage1_conversation_ids),
                    fact.last_used_at,
                    fact.usage_count,
                ),
            )
            conn.execute(
                """
                UPDATE agent_memory_candidates
                SET status = 'accepted', reviewed_at = ?, accepted_fact_id = ?
                WHERE candidate_id = ?
                """,
                (accepted_at, fact.fact_id, candidate_id),
            )
        self.invalidate_active_summaries(owner_user_id=owner_user_id, workspace_id=workspace_id, invalidated_at=accepted_at)
        return fact

    def reject_candidate(
        self,
        *,
        candidate_id: str,
        owner_user_id: str,
        workspace_id: str,
        rejected_at: str,
        expires_at: str | None = None,
    ) -> MemoryCandidate:
        with self._connect() as conn, conn:
            row = _candidate_row(conn, candidate_id, owner_user_id, workspace_id)
            if row is None:
                raise RuntimeError("agent_memory_candidate_not_found")
            conn.execute(
                """
                UPDATE agent_memory_candidates
                SET status = 'rejected', reviewed_at = ?, expires_at = ?
                WHERE candidate_id = ?
                """,
                (rejected_at, expires_at, candidate_id),
            )
            updated = _candidate_row(conn, candidate_id, owner_user_id, workspace_id)
        return _candidate_from_row(updated)

    def run_retention_cleanup(
        self,
        *,
        owner_user_id: str,
        workspace_id: str,
        fact_expiry_cutoff: str,
        rejected_candidate_cutoff: str,
        excerpt_cutoff: str,
        cleaned_at: str,
        limit: int,
    ) -> MemoryRetentionCleanupResult:
        with self._connect() as conn, conn:
            expired_fact_rows = conn.execute(
                """
                SELECT fact_id FROM agent_memory_facts
                WHERE owner_user_id = ? AND workspace_id = ? AND status = 'active'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY expires_at ASC, fact_id ASC
                LIMIT ?
                """,
                (owner_user_id, workspace_id, fact_expiry_cutoff, limit),
            ).fetchall()
            expired_fact_ids = [row["fact_id"] for row in expired_fact_rows]
            if expired_fact_ids:
                sql_marks = ",".join("?" for _ in expired_fact_ids)
                conn.execute(
                    f"""
                    UPDATE agent_memory_facts
                    SET status = 'deleted', deleted_at = ?, updated_at = ?, safe_evidence_excerpt = NULL
                    WHERE owner_user_id = ? AND workspace_id = ? AND fact_id IN ({sql_marks})
                    """,
                    [cleaned_at, cleaned_at, owner_user_id, workspace_id, *expired_fact_ids],
                )

            rejected_rows = conn.execute(
                """
                SELECT candidate_id FROM agent_memory_candidates
                WHERE owner_user_id = ? AND workspace_id = ? AND status = 'rejected'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY expires_at ASC, candidate_id ASC
                LIMIT ?
                """,
                (owner_user_id, workspace_id, rejected_candidate_cutoff, limit),
            ).fetchall()
            rejected_ids = [row["candidate_id"] for row in rejected_rows]
            if rejected_ids:
                sql_marks = ",".join("?" for _ in rejected_ids)
                conn.execute(
                    f"""
                    DELETE FROM agent_memory_candidates
                    WHERE owner_user_id = ? AND workspace_id = ? AND candidate_id IN ({sql_marks})
                    """,
                    [owner_user_id, workspace_id, *rejected_ids],
                )

            fact_excerpt_rows = conn.execute(
                """
                SELECT fact_id FROM agent_memory_facts
                WHERE owner_user_id = ? AND workspace_id = ?
                  AND safe_evidence_excerpt IS NOT NULL AND updated_at <= ?
                ORDER BY updated_at ASC, fact_id ASC
                LIMIT ?
                """,
                (owner_user_id, workspace_id, excerpt_cutoff, limit),
            ).fetchall()
            fact_excerpt_ids = [row["fact_id"] for row in fact_excerpt_rows]
            if fact_excerpt_ids:
                sql_marks = ",".join("?" for _ in fact_excerpt_ids)
                conn.execute(
                    f"""
                    UPDATE agent_memory_facts
                    SET safe_evidence_excerpt = NULL
                    WHERE owner_user_id = ? AND workspace_id = ? AND fact_id IN ({sql_marks})
                    """,
                    [owner_user_id, workspace_id, *fact_excerpt_ids],
                )

            candidate_excerpt_rows = conn.execute(
                """
                SELECT candidate_id FROM agent_memory_candidates
                WHERE owner_user_id = ? AND workspace_id = ?
                  AND safe_evidence_excerpt IS NOT NULL AND created_at <= ?
                ORDER BY created_at ASC, candidate_id ASC
                LIMIT ?
                """,
                (owner_user_id, workspace_id, excerpt_cutoff, limit),
            ).fetchall()
            candidate_excerpt_ids = [row["candidate_id"] for row in candidate_excerpt_rows]
            if candidate_excerpt_ids:
                sql_marks = ",".join("?" for _ in candidate_excerpt_ids)
                conn.execute(
                    f"""
                    UPDATE agent_memory_candidates
                    SET safe_evidence_excerpt = NULL
                    WHERE owner_user_id = ? AND workspace_id = ? AND candidate_id IN ({sql_marks})
                    """,
                    [owner_user_id, workspace_id, *candidate_excerpt_ids],
                )
        if expired_fact_ids:
            self.invalidate_active_summaries(
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                invalidated_at=cleaned_at,
            )
        return MemoryRetentionCleanupResult(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            deleted_fact_count=len(expired_fact_ids),
            purged_rejected_candidate_count=len(rejected_ids),
            cleared_fact_excerpt_count=len(fact_excerpt_ids),
            cleared_candidate_excerpt_count=len(candidate_excerpt_ids),
            cleaned_at=cleaned_at,
        )

    def list_facts(self, *, owner_user_id: str, workspace_id: str, include_deleted: bool = False) -> list[MemoryFact]:
        sql = """
            SELECT * FROM agent_memory_facts
            WHERE owner_user_id = ? AND workspace_id = ?
        """
        params: list[object] = [owner_user_id, workspace_id]
        if not include_deleted:
            sql += " AND status = 'active'"
        sql += " ORDER BY updated_at ASC, fact_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_fact_from_row(row) for row in rows]

    def delete_fact(self, *, fact_id: str, owner_user_id: str, workspace_id: str, deleted_at: str) -> MemoryFact:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_memory_facts
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE fact_id = ? AND owner_user_id = ? AND workspace_id = ?
                """,
                (deleted_at, deleted_at, fact_id, owner_user_id, workspace_id),
            )
            row = conn.execute(
                """
                SELECT * FROM agent_memory_facts
                WHERE fact_id = ? AND owner_user_id = ? AND workspace_id = ?
                """,
                (fact_id, owner_user_id, workspace_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("agent_memory_fact_not_found")
        self.invalidate_active_summaries(owner_user_id=owner_user_id, workspace_id=workspace_id, invalidated_at=deleted_at)
        return _fact_from_row(row)

    def update_fact_text(
        self,
        *,
        fact_id: str,
        owner_user_id: str,
        workspace_id: str,
        text: str,
        updated_at: str,
    ) -> MemoryFact:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_memory_facts
                SET text = ?, updated_at = ?
                WHERE fact_id = ? AND owner_user_id = ? AND workspace_id = ? AND status = 'active'
                """,
                (text, updated_at, fact_id, owner_user_id, workspace_id),
            )
            row = conn.execute(
                """
                SELECT * FROM agent_memory_facts
                WHERE fact_id = ? AND owner_user_id = ? AND workspace_id = ?
                """,
                (fact_id, owner_user_id, workspace_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("agent_memory_fact_not_found")
        self.invalidate_active_summaries(owner_user_id=owner_user_id, workspace_id=workspace_id, invalidated_at=updated_at)
        return _fact_from_row(row)

    def clear_scope(self, *, owner_user_id: str, workspace_id: str, cleared_at: str) -> MemoryClearResult:
        with self._connect() as conn, conn:
            active_rows = conn.execute(
                """
                SELECT fact_id FROM agent_memory_facts
                WHERE owner_user_id = ? AND workspace_id = ? AND status = 'active'
                """,
                (owner_user_id, workspace_id),
            ).fetchall()
            conn.execute(
                """
                UPDATE agent_memory_facts
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE owner_user_id = ? AND workspace_id = ? AND status = 'active'
                """,
                (cleared_at, cleared_at, owner_user_id, workspace_id),
            )
        self.invalidate_active_summaries(owner_user_id=owner_user_id, workspace_id=workspace_id, invalidated_at=cleared_at)
        return MemoryClearResult(
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            deleted_fact_count=len(active_rows),
            cleared_at=cleared_at,
        )

    def save_summary(
        self,
        *,
        summary_id: str,
        owner_user_id: str,
        workspace_id: str,
        summary_text: str,
        fact_ids: list[str],
        created_at: str,
        token_estimate: int | None = None,
        source_stage1_conversation_ids: list[str] | None = None,
        summary_kind: str = "consolidated",
    ) -> MemorySummary:
        summary = MemorySummary(
            summary_id=summary_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            summary_text=summary_text,
            fact_ids=fact_ids,
            created_at=created_at,
            token_estimate=token_estimate,
            source_stage1_conversation_ids=source_stage1_conversation_ids or [],
            source_fact_ids=fact_ids,
            summary_kind=summary_kind,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_memory_summaries
                SET status = 'superseded', invalidated_at = ?
                WHERE owner_user_id = ? AND workspace_id = ? AND status = 'active' AND invalidated_at IS NULL
                """,
                (created_at, owner_user_id, workspace_id),
            )
            conn.execute(
                """
                INSERT INTO agent_memory_summaries (
                    summary_id, owner_user_id, workspace_id, summary_text, fact_ids_json,
                    created_at, invalidated_at, schema_version, summary_kind, status, token_estimate,
                    source_stage1_conversation_ids_json, source_fact_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.summary_id,
                    summary.owner_user_id,
                    summary.workspace_id,
                    summary.summary_text,
                    _json(summary.fact_ids),
                    summary.created_at,
                    summary.invalidated_at,
                    summary.schema_version,
                    summary.summary_kind,
                    summary.status,
                    summary.token_estimate,
                    _json(summary.source_stage1_conversation_ids),
                    _json(summary.source_fact_ids),
                ),
            )
        return summary

    def get_active_summary(self, *, owner_user_id: str, workspace_id: str) -> MemorySummary | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM agent_memory_summaries
                WHERE owner_user_id = ? AND workspace_id = ? AND status = 'active' AND invalidated_at IS NULL
                ORDER BY created_at DESC, summary_id DESC
                LIMIT 1
                """,
                (owner_user_id, workspace_id),
            ).fetchone()
        if row is None:
            return None
        return _summary_from_row(row)

    def list_summaries(self, *, owner_user_id: str, workspace_id: str) -> list[MemorySummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_memory_summaries
                WHERE owner_user_id = ? AND workspace_id = ?
                ORDER BY created_at DESC, summary_id DESC
                """,
                (owner_user_id, workspace_id),
            ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def invalidate_active_summaries(self, *, owner_user_id: str, workspace_id: str, invalidated_at: str) -> None:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE agent_memory_summaries
                SET status = 'invalidated', invalidated_at = ?
                WHERE owner_user_id = ? AND workspace_id = ? AND status = 'active' AND invalidated_at IS NULL
                """,
                (invalidated_at, owner_user_id, workspace_id),
            )

    def save_usage(
        self,
        *,
        usage_id: str,
        owner_user_id: str,
        workspace_id: str,
        conversation_id: str,
        turn_id: str,
        fact_ids: list[str],
        created_at: str,
        summary_id: str | None = None,
        reason_code: str | None = None,
    ) -> MemoryUsage:
        usage = MemoryUsage(
            usage_id=usage_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            agent_turn_id=turn_id,
            fact_ids=fact_ids,
            summary_id=summary_id,
            reason_code=reason_code,
            created_at=created_at,
        )
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO agent_memory_usage (
                    usage_id, owner_user_id, workspace_id, conversation_id, turn_id, agent_turn_id,
                    summary_id, fact_ids_json, reason_code, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage.usage_id,
                    usage.owner_user_id,
                    usage.workspace_id,
                    usage.conversation_id,
                    usage.turn_id,
                    usage.agent_turn_id,
                    usage.summary_id,
                    _json(usage.fact_ids),
                    usage.reason_code,
                    usage.created_at,
                ),
            )
            if usage.fact_ids:
                sql_marks = ",".join("?" for _ in usage.fact_ids)
                conn.execute(
                    f"""
                    UPDATE agent_memory_facts
                    SET usage_count = usage_count + 1, last_used_at = ?
                    WHERE owner_user_id = ? AND workspace_id = ? AND fact_id IN ({sql_marks})
                    """,
                    [created_at, owner_user_id, workspace_id, *usage.fact_ids],
                )
            if summary_id is not None:
                summary = conn.execute(
                    """
                    SELECT source_stage1_conversation_ids_json FROM agent_memory_summaries
                    WHERE summary_id = ? AND owner_user_id = ? AND workspace_id = ?
                    """,
                    (summary_id, owner_user_id, workspace_id),
                ).fetchone()
                if summary is not None:
                    ids = _loads_list(summary["source_stage1_conversation_ids_json"])
                    if ids:
                        sql_marks = ",".join("?" for _ in ids)
                        conn.execute(
                            f"""
                            UPDATE agent_memory_stage1_outputs
                            SET usage_count = usage_count + 1, last_usage = ?
                            WHERE owner_user_id = ? AND workspace_id = ? AND conversation_id IN ({sql_marks})
                            """,
                            [created_at, owner_user_id, workspace_id, *ids],
                        )
        return usage

    def list_usage(
        self,
        *,
        conversation_id: str | None = None,
        owner_user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[MemoryUsage]:
        sql = "SELECT * FROM agent_memory_usage WHERE 1 = 1"
        params: list[object] = []
        if conversation_id is not None:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)
        if owner_user_id is not None:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        if workspace_id is not None:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        sql += " ORDER BY created_at ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_usage_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return conn

    def _connect_immediate(self) -> sqlite3.Connection:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def _stage1_output_up_to_date(
        self,
        *,
        conversation_id: str,
        owner_user_id: str,
        workspace_id: str,
        source_updated_at: str,
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT source_updated_at FROM agent_memory_stage1_outputs
                WHERE conversation_id = ? AND owner_user_id = ? AND workspace_id = ?
                """,
                (conversation_id, owner_user_id, workspace_id),
            ).fetchone()
        return row is not None and str(row["source_updated_at"]) >= source_updated_at

    def _mark_job_terminal(
        self,
        *,
        kind: str,
        job_key: str,
        owner_user_id: str,
        workspace_id: str,
        ownership_token: str,
        status: str,
        now: str,
        last_success_watermark: str | None,
        last_error_code: str | None,
    ) -> bool:
        with self._connect() as conn, conn:
            cursor = conn.execute(
                """
                UPDATE agent_memory_jobs
                SET status = ?, finished_at = ?, worker_id = NULL, ownership_token = NULL,
                    lease_until = NULL, retry_at = NULL, last_success_watermark = COALESCE(?, last_success_watermark),
                    last_error_code = ?
                WHERE kind = ? AND job_key = ? AND owner_user_id = ? AND workspace_id = ?
                  AND status = 'running' AND ownership_token = ?
                """,
                (
                    status,
                    now,
                    last_success_watermark,
                    last_error_code,
                    kind,
                    job_key,
                    owner_user_id,
                    workspace_id,
                    ownership_token,
                ),
            )
        return cursor.rowcount == 1


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_memory_settings (
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            memory_enabled INTEGER NOT NULL,
            generation_enabled INTEGER NOT NULL DEFAULT 1,
            recall_enabled INTEGER NOT NULL DEFAULT 1,
            review_required INTEGER NOT NULL,
            max_rollouts_per_startup INTEGER NOT NULL DEFAULT 4,
            max_rollout_age_days INTEGER NOT NULL DEFAULT 30,
            min_rollout_idle_hours INTEGER NOT NULL DEFAULT 6,
            max_stage1_outputs_for_phase2 INTEGER NOT NULL DEFAULT 20,
            max_unused_days INTEGER NOT NULL DEFAULT 180,
            summary_token_budget INTEGER NOT NULL DEFAULT 1200,
            candidate_retention_days INTEGER NOT NULL DEFAULT 180,
            rejected_retention_days INTEGER NOT NULL DEFAULT 30,
            source_excerpt_retention_days INTEGER NOT NULL DEFAULT 30,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(owner_user_id, workspace_id)
        );

        CREATE TABLE IF NOT EXISTS agent_memory_jobs (
            kind TEXT NOT NULL,
            job_key TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            status TEXT NOT NULL,
            worker_id TEXT,
            ownership_token TEXT,
            started_at TEXT,
            finished_at TEXT,
            lease_until TEXT,
            retry_at TEXT,
            retry_remaining INTEGER NOT NULL DEFAULT 3,
            last_error_code TEXT,
            input_watermark TEXT,
            last_success_watermark TEXT,
            PRIMARY KEY(kind, job_key, owner_user_id, workspace_id)
        );

        CREATE INDEX IF NOT EXISTS idx_agent_memory_jobs_scope_status
            ON agent_memory_jobs(owner_user_id, workspace_id, kind, status, lease_until);

        CREATE TABLE IF NOT EXISTS agent_memory_stage1_outputs (
            conversation_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            source_updated_at TEXT NOT NULL,
            raw_memory TEXT NOT NULL,
            rollout_summary TEXT NOT NULL,
            rollout_slug TEXT,
            generated_at TEXT NOT NULL,
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_usage TEXT,
            selected_for_phase2 INTEGER NOT NULL DEFAULT 0,
            selected_for_phase2_source_updated_at TEXT,
            privacy_review_json TEXT NOT NULL DEFAULT '{}',
            source_message_ids_json TEXT NOT NULL DEFAULT '[]',
            source_activity_ids_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY(conversation_id, owner_user_id, workspace_id)
        );

        CREATE TABLE IF NOT EXISTS agent_memory_candidates (
            candidate_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            safe_excerpt TEXT NOT NULL,
            source_message_ids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_code TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            accepted_fact_id TEXT,
            raw_candidate_hash TEXT,
            safe_candidate_text TEXT,
            safe_evidence_excerpt TEXT,
            privacy_review_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL,
            source_stage1_conversation_id TEXT,
            source_activity_ids_json TEXT NOT NULL DEFAULT '[]',
            expires_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_agent_memory_candidates_scope_status
            ON agent_memory_candidates(owner_user_id, workspace_id, status, created_at);

        CREATE TABLE IF NOT EXISTS agent_memory_facts (
            fact_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            source_candidate_id TEXT NOT NULL,
            source_conversation_ids_json TEXT NOT NULL,
            source_message_ids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT,
            deleted_at TEXT,
            confidence REAL,
            safe_evidence_excerpt TEXT,
            source_stage1_conversation_ids_json TEXT NOT NULL DEFAULT '[]',
            last_used_at TEXT,
            usage_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_agent_memory_facts_scope_status_category
            ON agent_memory_facts(owner_user_id, workspace_id, status, category, updated_at);

        CREATE TABLE IF NOT EXISTS agent_memory_summaries (
            summary_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            fact_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            invalidated_at TEXT,
            schema_version TEXT NOT NULL DEFAULT 'agent.memory.summary.v1',
            summary_kind TEXT NOT NULL DEFAULT 'consolidated',
            status TEXT NOT NULL DEFAULT 'active',
            token_estimate INTEGER,
            source_stage1_conversation_ids_json TEXT NOT NULL DEFAULT '[]',
            source_fact_ids_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS agent_memory_usage (
            usage_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            agent_turn_id TEXT,
            summary_id TEXT,
            fact_ids_json TEXT NOT NULL,
            reason_code TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_memory_workspace_files (
            owner_user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content TEXT NOT NULL,
            baseline_hash TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(owner_user_id, workspace_id, path)
        );
        """
    )
    _ensure_v2_columns(conn)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    _create_schema(conn)


def _ensure_v2_columns(conn: sqlite3.Connection) -> None:
    _ensure_columns(
        conn,
        "agent_memory_settings",
        {
            "generation_enabled": "INTEGER NOT NULL DEFAULT 1",
            "recall_enabled": "INTEGER NOT NULL DEFAULT 1",
            "max_rollouts_per_startup": "INTEGER NOT NULL DEFAULT 4",
            "max_rollout_age_days": "INTEGER NOT NULL DEFAULT 30",
            "min_rollout_idle_hours": "INTEGER NOT NULL DEFAULT 6",
            "max_stage1_outputs_for_phase2": "INTEGER NOT NULL DEFAULT 20",
            "max_unused_days": "INTEGER NOT NULL DEFAULT 180",
            "summary_token_budget": "INTEGER NOT NULL DEFAULT 1200",
            "candidate_retention_days": "INTEGER NOT NULL DEFAULT 180",
            "rejected_retention_days": "INTEGER NOT NULL DEFAULT 30",
            "source_excerpt_retention_days": "INTEGER NOT NULL DEFAULT 30",
        },
    )
    _ensure_columns(
        conn,
        "agent_memory_candidates",
        {
            "raw_candidate_hash": "TEXT",
            "safe_candidate_text": "TEXT",
            "safe_evidence_excerpt": "TEXT",
            "privacy_review_json": "TEXT NOT NULL DEFAULT '{}'",
            "confidence": "REAL",
            "source_stage1_conversation_id": "TEXT",
            "source_activity_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "expires_at": "TEXT",
        },
    )
    _ensure_columns(
        conn,
        "agent_memory_facts",
        {
            "confidence": "REAL",
            "safe_evidence_excerpt": "TEXT",
            "source_stage1_conversation_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "last_used_at": "TEXT",
            "usage_count": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_columns(
        conn,
        "agent_memory_summaries",
        {
            "schema_version": "TEXT NOT NULL DEFAULT 'agent.memory.summary.v1'",
            "summary_kind": "TEXT NOT NULL DEFAULT 'consolidated'",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "token_estimate": "INTEGER",
            "source_stage1_conversation_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_fact_ids_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    )
    _ensure_columns(
        conn,
        "agent_memory_usage",
        {
            "agent_turn_id": "TEXT",
            "summary_id": "TEXT",
            "reason_code": "TEXT",
        },
    )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _job_row(
    conn: sqlite3.Connection,
    kind: str,
    job_key: str,
    owner_user_id: str,
    workspace_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM agent_memory_jobs
        WHERE kind = ? AND job_key = ? AND owner_user_id = ? AND workspace_id = ?
        """,
        (kind, job_key, owner_user_id, workspace_id),
    ).fetchone()


def _claim_blocked_by_existing_job(row: sqlite3.Row, *, now: str) -> MemoryJobClaim | None:
    if row["status"] == "running" and row["lease_until"] is not None and row["lease_until"] > now:
        return MemoryJobClaim(status="skipped_running", reason_code="agent_memory_job_running")
    if int(row["retry_remaining"]) <= 0:
        return MemoryJobClaim(status="skipped_retry_exhausted", reason_code="agent_memory_job_retry_exhausted")
    if row["retry_at"] is not None and row["retry_at"] > now:
        return MemoryJobClaim(status="skipped_retry_backoff", reason_code="agent_memory_job_retry_backoff")
    return None


def _candidate_row(conn: sqlite3.Connection, candidate_id: str, owner_user_id: str, workspace_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM agent_memory_candidates
        WHERE candidate_id = ? AND owner_user_id = ? AND workspace_id = ?
        """,
        (candidate_id, owner_user_id, workspace_id),
    ).fetchone()


def _settings_from_row(row: sqlite3.Row) -> MemorySettings:
    return MemorySettings(
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        memory_enabled=bool(row["memory_enabled"]),
        generation_enabled=bool(row["generation_enabled"]),
        recall_enabled=bool(row["recall_enabled"]),
        review_required=bool(row["review_required"]),
        max_rollouts_per_startup=int(row["max_rollouts_per_startup"]),
        max_rollout_age_days=int(row["max_rollout_age_days"]),
        min_rollout_idle_hours=int(row["min_rollout_idle_hours"]),
        max_stage1_outputs_for_phase2=int(row["max_stage1_outputs_for_phase2"]),
        max_unused_days=int(row["max_unused_days"]),
        summary_token_budget=int(row["summary_token_budget"]),
        candidate_retention_days=int(row["candidate_retention_days"]),
        rejected_retention_days=int(row["rejected_retention_days"]),
        source_excerpt_retention_days=int(row["source_excerpt_retention_days"]),
        updated_at=row["updated_at"],
    )


def _job_from_row(row: sqlite3.Row) -> MemoryJob:
    return MemoryJob(
        kind=row["kind"],
        job_key=row["job_key"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        status=row["status"],
        worker_id=row["worker_id"],
        ownership_token=row["ownership_token"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        lease_until=row["lease_until"],
        retry_at=row["retry_at"],
        retry_remaining=int(row["retry_remaining"]),
        last_error_code=row["last_error_code"],
        input_watermark=row["input_watermark"],
        last_success_watermark=row["last_success_watermark"],
    )


def _stage1_from_row(row: sqlite3.Row) -> Stage1Output:
    return Stage1Output(
        conversation_id=row["conversation_id"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        source_updated_at=row["source_updated_at"],
        raw_memory=row["raw_memory"],
        rollout_summary=row["rollout_summary"],
        rollout_slug=row["rollout_slug"],
        generated_at=row["generated_at"],
        usage_count=int(row["usage_count"]),
        last_usage=row["last_usage"],
        selected_for_phase2=bool(row["selected_for_phase2"]),
        selected_for_phase2_source_updated_at=row["selected_for_phase2_source_updated_at"],
        privacy_review_json=_loads_dict(row["privacy_review_json"]),
        source_message_ids=_loads_list(row["source_message_ids_json"]),
        source_activity_ids=_loads_list(row["source_activity_ids_json"]),
    )


def _candidate_from_row(row: sqlite3.Row | None) -> MemoryCandidate:
    if row is None:
        raise RuntimeError("agent_memory_candidate_not_found")
    return MemoryCandidate(
        candidate_id=row["candidate_id"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        conversation_id=row["conversation_id"],
        category=row["category"],
        text=row["text"],
        safe_excerpt=row["safe_excerpt"],
        source_message_ids=_loads_list(row["source_message_ids_json"]),
        status=row["status"],
        reason_code=row["reason_code"],
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
        accepted_fact_id=row["accepted_fact_id"],
        raw_candidate_hash=row["raw_candidate_hash"],
        safe_candidate_text=row["safe_candidate_text"],
        safe_evidence_excerpt=row["safe_evidence_excerpt"],
        privacy_review_json=_loads_dict(row["privacy_review_json"]),
        confidence=row["confidence"],
        source_stage1_conversation_id=row["source_stage1_conversation_id"],
        source_activity_ids=_loads_list(row["source_activity_ids_json"]),
        expires_at=row["expires_at"],
    )


def _fact_from_row(row: sqlite3.Row) -> MemoryFact:
    return MemoryFact(
        fact_id=row["fact_id"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        category=row["category"],
        text=row["text"],
        source_candidate_id=row["source_candidate_id"],
        source_conversation_ids=_loads_list(row["source_conversation_ids_json"]),
        source_message_ids=_loads_list(row["source_message_ids_json"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        deleted_at=row["deleted_at"],
        confidence=row["confidence"],
        safe_evidence_excerpt=row["safe_evidence_excerpt"],
        source_stage1_conversation_ids=_loads_list(row["source_stage1_conversation_ids_json"]),
        last_used_at=row["last_used_at"],
        usage_count=int(row["usage_count"]),
    )


def _summary_from_row(row: sqlite3.Row) -> MemorySummary:
    return MemorySummary(
        summary_id=row["summary_id"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        summary_text=row["summary_text"],
        fact_ids=_loads_list(row["fact_ids_json"]),
        created_at=row["created_at"],
        invalidated_at=row["invalidated_at"],
        schema_version=row["schema_version"],
        summary_kind=row["summary_kind"],
        status=row["status"],
        token_estimate=row["token_estimate"],
        source_stage1_conversation_ids=_loads_list(row["source_stage1_conversation_ids_json"]),
        source_fact_ids=_loads_list(row["source_fact_ids_json"]),
    )


def _usage_from_row(row: sqlite3.Row) -> MemoryUsage:
    return MemoryUsage(
        usage_id=row["usage_id"],
        owner_user_id=row["owner_user_id"],
        workspace_id=row["workspace_id"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        agent_turn_id=row["agent_turn_id"],
        summary_id=row["summary_id"],
        fact_ids=_loads_list(row["fact_ids_json"]),
        reason_code=row["reason_code"],
        created_at=row["created_at"],
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if item is not None]


def _loads_dict(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        return {}
    return {str(key): item for key, item in loaded.items()}


def _add_seconds(value: str, seconds: int) -> str:
    return _format_time(_parse_time(value) + timedelta(seconds=seconds))


def _add_days(value: str, days: int) -> str:
    return _format_time(_parse_time(value) + timedelta(days=days))


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
