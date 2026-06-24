from __future__ import annotations

import sqlite3


def migrate_wts_control_plane(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wts_job_request_revisions (
            job_request_revision_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            jd_text TEXT NOT NULL,
            user_job_title TEXT,
            extracted_job_title TEXT,
            notes TEXT,
            source_kinds_json TEXT NOT NULL,
            workspace_source_policy_id TEXT,
            request_hash TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, conversation_id, idempotency_key),
            UNIQUE(workspace_id, conversation_id, request_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_wts_job_requests_owner_workspace
            ON wts_job_request_revisions(owner_user_id, workspace_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_wts_job_requests_conversation
            ON wts_job_request_revisions(conversation_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS wts_conversation_start_requests (
            start_request_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            job_request_revision_id TEXT NOT NULL REFERENCES wts_job_request_revisions(job_request_revision_id)
                ON DELETE CASCADE,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, owner_user_id, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_wts_conversation_start_requests_conversation
            ON wts_conversation_start_requests(conversation_id);

        CREATE TABLE IF NOT EXISTS wts_requirement_draft_job_requests (
            draft_revision_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            job_request_revision_id TEXT NOT NULL REFERENCES wts_job_request_revisions(job_request_revision_id)
                ON DELETE CASCADE,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_wts_requirement_draft_job_requests_job_request
            ON wts_requirement_draft_job_requests(job_request_revision_id);

        CREATE TABLE IF NOT EXISTS wts_confirm_requirement_requests (
            confirm_request_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            draft_revision_id TEXT NOT NULL,
            expected_draft_revision_id TEXT NOT NULL,
            job_request_revision_id TEXT NOT NULL REFERENCES wts_job_request_revisions(job_request_revision_id),
            approved_requirement_revision_id TEXT,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, conversation_id, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_wts_confirm_requirement_requests_draft
            ON wts_confirm_requirement_requests(workspace_id, conversation_id, draft_revision_id);

        CREATE TABLE IF NOT EXISTS wts_workflow_start_intents (
            workflow_start_intent_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            draft_revision_id TEXT NOT NULL,
            approved_requirement_revision_id TEXT NOT NULL,
            job_request_revision_id TEXT NOT NULL REFERENCES wts_job_request_revisions(job_request_revision_id),
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            deterministic_run_key TEXT NOT NULL,
            status TEXT NOT NULL,
            runtime_run_id TEXT,
            reason_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, conversation_id, idempotency_key),
            UNIQUE(workspace_id, deterministic_run_key)
        );

        CREATE INDEX IF NOT EXISTS idx_wts_workflow_start_intents_job_request
            ON wts_workflow_start_intents(job_request_revision_id);

        CREATE TABLE IF NOT EXISTS wts_outbox (
            outbox_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_wts_outbox_status
            ON wts_outbox(status, updated_at);

        CREATE INDEX IF NOT EXISTS idx_wts_outbox_workflow_aggregate
            ON wts_outbox(event_type, aggregate_id, created_at);

        CREATE TABLE IF NOT EXISTS wts_requirement_transcript_snapshots (
            transcript_message_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES agent_conversations(conversation_id) ON DELETE CASCADE,
            draft_revision_id TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(draft_revision_id)
        );
        """
    )
