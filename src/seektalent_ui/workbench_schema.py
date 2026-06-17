from __future__ import annotations

import sqlite3
from pathlib import Path

from seektalent.product_database_versions import WORKBENCH_SCHEMA_VERSION
from seektalent.sqlite_migrations import (
    backup_sqlite_before_migration,
    has_user_tables,
    require_supported_version,
    run_sqlite_integrity_checks,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    disabled_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_memberships (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    runtime_run_id TEXT,
    job_title TEXT NOT NULL,
    jd_text TEXT NOT NULL,
    notes TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_owner
ON sessions(workspace_id, user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_sessions_workspace_updated
ON sessions(tenant_id, workspace_id, updated_at DESC, session_id);

CREATE INDEX IF NOT EXISTS idx_sessions_user_updated
ON sessions(tenant_id, workspace_id, user_id, updated_at DESC, session_id);

CREATE TABLE IF NOT EXISTS session_requirement_reviews (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'approved')),
    requirement_sheet_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approved_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS source_runs (
    source_run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('cts', 'liepin')),
    status TEXT NOT NULL CHECK(status IN ('queued', 'blocked', 'running', 'completed', 'failed')),
    auth_state TEXT NOT NULL CHECK(auth_state IN ('not_required', 'login_required')),
    health_state TEXT NOT NULL,
    runtime_run_id TEXT,
    warning_code TEXT,
    warning_message TEXT,
    cards_scanned_count INTEGER NOT NULL DEFAULT 0,
    unique_candidates_count INTEGER NOT NULL DEFAULT 0,
    detail_open_used_count INTEGER NOT NULL DEFAULT 0,
    detail_open_blocked_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_source_runs_session
ON source_runs(session_id, source_kind);

CREATE INDEX IF NOT EXISTS idx_source_runs_source_card
ON source_runs(tenant_id, workspace_id, session_id, source_kind, status);

CREATE INDEX IF NOT EXISTS idx_source_runs_status
ON source_runs(tenant_id, workspace_id, status, created_at);

CREATE TABLE IF NOT EXISTS source_connections (
    connection_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('liepin')),
    status TEXT NOT NULL CHECK(
        status IN (
            'login_required',
            'login_in_progress',
            'verification_required',
            'connected',
            'expired',
            'blocked',
            'disconnected'
        )
    ),
    warning_code TEXT,
    warning_message TEXT,
    provider_account_hash TEXT,
    compliance_gate_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    connected_at TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_connections_user_source
ON source_connections(tenant_id, workspace_id, user_id, source_kind);

CREATE INDEX IF NOT EXISTS idx_source_connections_scope
ON source_connections(tenant_id, workspace_id, user_id, connection_id);

CREATE TABLE IF NOT EXISTS connection_status_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('liepin')),
    status TEXT NOT NULL,
    event_name TEXT NOT NULL,
    payload_redacted_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (connection_id) REFERENCES source_connections(connection_id)
);

CREATE INDEX IF NOT EXISTS idx_connection_status_events_connection
ON connection_status_events(tenant_id, workspace_id, connection_id, event_id);

CREATE TABLE IF NOT EXISTS security_audit_events (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    actor_user_id TEXT,
    actor_role TEXT,
    request_ip TEXT,
    user_agent TEXT,
    target_type TEXT NOT NULL,
    target_id TEXT,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    reason_code TEXT,
    metadata_redacted_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_security_audit_events_scope
ON security_audit_events(tenant_id, workspace_id, audit_id);

CREATE INDEX IF NOT EXISTS idx_security_audit_events_action
ON security_audit_events(tenant_id, workspace_id, action, created_at);

CREATE TABLE IF NOT EXISTS source_run_policies (
    session_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('liepin')),
    detail_open_mode TEXT NOT NULL CHECK(detail_open_mode IN ('human_confirm', 'bypass_confirm')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, source_kind),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_source_run_policies_scope
ON source_run_policies(tenant_id, workspace_id, user_id, session_id, source_kind);

CREATE TABLE IF NOT EXISTS source_run_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('cts', 'liepin')),
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed')),
    lease_owner TEXT,
    lease_expires_at TEXT,
    idempotency_key TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id)
);

CREATE INDEX IF NOT EXISTS idx_source_run_jobs_claim
ON source_run_jobs(status, lease_expires_at, job_id);

CREATE INDEX IF NOT EXISTS idx_source_run_jobs_source_status
ON source_run_jobs(source_run_id, status);

CREATE TABLE IF NOT EXISTS runtime_sourcing_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed')),
    source_kinds_json TEXT NOT NULL,
    source_run_ids_json TEXT NOT NULL DEFAULT '[]',
    runtime_run_id TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    idempotency_key TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_runtime_sourcing_jobs_claim
ON runtime_sourcing_jobs(status, lease_expires_at, job_id);

CREATE INDEX IF NOT EXISTS idx_runtime_sourcing_jobs_session_status
ON runtime_sourcing_jobs(session_id, status);

CREATE TABLE IF NOT EXISTS runtime_finalization_revisions (
    session_id TEXT NOT NULL,
    runtime_run_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    ordered_candidate_identity_ids_json TEXT NOT NULL,
    coverage_summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, runtime_run_id, revision),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_runtime_finalization_revisions_latest
ON runtime_finalization_revisions(session_id, revision DESC);

CREATE TABLE IF NOT EXISTS runtime_candidate_identity_snapshots (
    session_id TEXT NOT NULL,
    runtime_run_id TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    canonical_resume_id TEXT NOT NULL,
    merged_resume_ids_json TEXT NOT NULL,
    source_evidence_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, runtime_run_id, identity_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_runtime_candidate_identity_snapshots_session
ON runtime_candidate_identity_snapshots(session_id, runtime_run_id);

CREATE TABLE IF NOT EXISTS session_events (
    global_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT,
    session_seq INTEGER,
    source_run_id TEXT,
    source_kind TEXT CHECK(source_kind IN ('cts', 'liepin') OR source_kind IS NULL),
    event_name TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'workbench_event_v1',
    idempotency_key TEXT,
    payload_redacted_json TEXT NOT NULL,
    occurred_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_events_global
ON session_events(tenant_id, workspace_id, global_seq);

CREATE INDEX IF NOT EXISTS idx_session_events_session
ON session_events(tenant_id, workspace_id, session_id, session_seq);

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_workbench_note_idempotency
ON session_events(tenant_id, workspace_id, user_id, session_id, idempotency_key)
WHERE event_name = 'workbench_note_created' AND idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_runtime_source_lane_idempotency
ON session_events(tenant_id, workspace_id, user_id, session_id, idempotency_key)
WHERE idempotency_key IS NOT NULL
  AND event_name IN (
    'runtime_source_plan_created',
    'runtime_source_lane_started',
    'runtime_source_lane_completed',
    'runtime_source_lane_blocked',
    'runtime_source_lane_partial',
    'runtime_source_lane_failed',
    'runtime_source_lane_cancelled',
    'runtime_detail_recommended',
    'runtime_detail_approved',
    'runtime_detail_leased',
    'runtime_detail_completed',
    'runtime_detail_blocked'
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_runtime_public_idempotency
ON session_events(tenant_id, workspace_id, user_id, session_id, idempotency_key)
WHERE schema_version = 'runtime_public_event_v1' AND idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS runtime_source_lane_latest_state (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('cts', 'liepin')),
    runtime_run_id TEXT,
    source_lane_run_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    event_seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, user_id, session_id, source_run_id, source_lane_run_id)
);

CREATE INDEX IF NOT EXISTS idx_runtime_source_lane_latest_session
ON runtime_source_lane_latest_state(tenant_id, workspace_id, session_id, source_kind);

CREATE TABLE IF NOT EXISTS workbench_note_writer_leases (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    lease_owner TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    last_tick_slot INTEGER,
    in_flight_started_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, user_id, session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_workbench_note_writer_leases_expires
ON workbench_note_writer_leases(lease_expires_at, session_id);

CREATE TABLE IF NOT EXISTS candidate_review_items (
    review_item_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    primary_evidence_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    summary TEXT NOT NULL,
    aggregate_score INTEGER,
    fit_bucket TEXT,
    why_selected TEXT NOT NULL DEFAULT '',
    source_round INTEGER,
    review_status TEXT NOT NULL CHECK(review_status IN ('new', 'promising', 'rejected')),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_review_items_session
ON candidate_review_items(tenant_id, workspace_id, session_id, aggregate_score DESC, review_item_id);

CREATE TABLE IF NOT EXISTS candidate_evidence (
    evidence_id TEXT PRIMARY KEY,
    review_item_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('cts', 'liepin')),
    evidence_level TEXT NOT NULL CHECK(evidence_level IN ('card', 'detail', 'final')),
    provider_candidate_key_hash TEXT NOT NULL,
    runtime_identity_id TEXT,
    resume_id TEXT NOT NULL,
    score INTEGER,
    fit_bucket TEXT,
    matched_must_haves_json TEXT NOT NULL,
    matched_preferences_json TEXT NOT NULL,
    missing_risks_json TEXT NOT NULL,
    strengths_json TEXT NOT NULL,
    weaknesses_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (review_item_id) REFERENCES candidate_review_items(review_item_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_evidence_source
ON candidate_evidence(tenant_id, workspace_id, session_id, source_run_id, evidence_level);

CREATE TABLE IF NOT EXISTS candidate_actions (
    action_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    review_item_id TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (review_item_id) REFERENCES candidate_review_items(review_item_id)
);

CREATE TABLE IF NOT EXISTS detail_open_requests (
    request_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    candidate_evidence_id TEXT NOT NULL,
    review_item_id TEXT NOT NULL,
    provider_candidate_key_hash TEXT NOT NULL,
    detail_candidates_json TEXT,
    detail_open_mode TEXT NOT NULL CHECK(detail_open_mode IN ('human_confirm', 'bypass_confirm')),
    status TEXT NOT NULL CHECK(
        status IN ('pending', 'approved', 'rejected', 'bypassed', 'blocked', 'expired')
    ),
    idempotency_key TEXT NOT NULL,
    blocked_reason TEXT,
    decision_note TEXT,
    ledger_id TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id),
    FOREIGN KEY (connection_id) REFERENCES source_connections(connection_id),
    FOREIGN KEY (candidate_evidence_id) REFERENCES candidate_evidence(evidence_id),
    FOREIGN KEY (review_item_id) REFERENCES candidate_review_items(review_item_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_detail_open_requests_idempotency
ON detail_open_requests(tenant_id, workspace_id, user_id, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_detail_open_requests_queue
ON detail_open_requests(tenant_id, workspace_id, user_id, status, created_at);

CREATE TABLE IF NOT EXISTS detail_open_ledger (
    ledger_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    candidate_evidence_id TEXT NOT NULL,
    provider_candidate_key_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('planned', 'leased', 'opened', 'skipped', 'blocked', 'failed', 'maybe_used')
    ),
    budget_day TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    lease_expires_at TEXT,
    opened_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (connection_id) REFERENCES source_connections(connection_id),
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id),
    FOREIGN KEY (request_id) REFERENCES detail_open_requests(request_id),
    FOREIGN KEY (candidate_evidence_id) REFERENCES candidate_evidence(evidence_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_detail_open_ledger_idempotency
ON detail_open_ledger(tenant_id, workspace_id, actor_id, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_detail_open_ledger_active
ON detail_open_ledger(connection_id, status, lease_expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_detail_open_ledger_one_active_lease
ON detail_open_ledger(connection_id)
WHERE status = 'leased';

CREATE INDEX IF NOT EXISTS idx_detail_open_ledger_budget
ON detail_open_ledger(connection_id, budget_day, status);

CREATE TABLE IF NOT EXISTS external_write_intents (
    intent_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_scope_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'succeeded', 'failed', 'tombstoned')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL,
    resolved_external_ref TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_external_write_intents_idempotency
ON external_write_intents(tenant_id, workspace_id, user_id, idempotency_key);

CREATE INDEX IF NOT EXISTS idx_external_write_intents_pending
ON external_write_intents(tenant_id, workspace_id, status, updated_at, intent_id);
"""


def initialize_workbench_schema(conn: sqlite3.Connection, *, now: str, database_path: str | Path | None = None) -> None:
    version = require_supported_version(conn, supported_version=WORKBENCH_SCHEMA_VERSION, store_name="workbench")
    if version == WORKBENCH_SCHEMA_VERSION:
        return
    if version > 0 or has_user_tables(conn):
        if database_path is not None:
            path = Path(database_path)
            backup_sqlite_before_migration(
                path,
                backup_root=path.parent / "migration_backups",
                store_name="workbench",
                now=now,
            )
    conn.executescript(SCHEMA_SQL)
    ensure_column(conn, "sessions", "runtime_run_id", "TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_runtime_run_id
        ON sessions(runtime_run_id)
        WHERE runtime_run_id IS NOT NULL
        """
    )
    ensure_column(conn, "source_run_jobs", "idempotency_key", "TEXT")
    ensure_column(conn, "source_connections", "provider_account_hash", "TEXT")
    ensure_column(conn, "source_connections", "compliance_gate_ref", "TEXT")
    ensure_column(conn, "session_events", "schema_version", "TEXT NOT NULL DEFAULT 'workbench_event_v1'")
    ensure_column(conn, "session_events", "idempotency_key", "TEXT")
    ensure_column(conn, "session_events", "occurred_at", "TEXT")
    ensure_column(conn, "workbench_note_writer_leases", "last_tick_slot", "INTEGER")
    ensure_column(conn, "workbench_note_writer_leases", "in_flight_started_at", "TEXT")
    ensure_column(conn, "runtime_sourcing_jobs", "source_run_ids_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "source_runs", "runtime_run_id", "TEXT")
    ensure_column(conn, "source_runs", "cards_scanned_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "source_runs", "unique_candidates_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "source_runs", "detail_open_used_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "source_runs", "detail_open_blocked_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "candidate_review_items", "why_selected", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "candidate_review_items", "source_round", "INTEGER")
    ensure_column(conn, "candidate_evidence", "runtime_identity_id", "TEXT")
    ensure_column(conn, "detail_open_requests", "detail_candidates_json", "TEXT")
    conn.execute(
        """
        INSERT INTO session_requirement_reviews (
            session_id, tenant_id, workspace_id, user_id, status,
            requirement_sheet_json, created_at, updated_at, approved_at
        )
        SELECT s.session_id, s.tenant_id, s.workspace_id, s.user_id,
               'draft', NULL, ?, ?, NULL
        FROM sessions AS s
        WHERE NOT EXISTS (
            SELECT 1
            FROM session_requirement_reviews AS review
            WHERE review.session_id = s.session_id
        )
        """,
        (now, now),
    )
    backfill_completed_cts_source_run_counts(conn)
    conn.execute(f"PRAGMA user_version = {WORKBENCH_SCHEMA_VERSION}")
    run_sqlite_integrity_checks(conn, store_name="workbench", foreign_keys=True)


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")



def backfill_completed_cts_source_run_counts(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE source_runs
        SET cards_scanned_count = CASE
                WHEN cards_scanned_count = 0 THEN (
                    SELECT COUNT(DISTINCT evidence.review_item_id)
                    FROM candidate_evidence AS evidence
                    WHERE evidence.source_run_id = source_runs.source_run_id
                )
                ELSE cards_scanned_count
            END,
            unique_candidates_count = CASE
                WHEN unique_candidates_count = 0 THEN (
                    SELECT COUNT(DISTINCT evidence.review_item_id)
                    FROM candidate_evidence AS evidence
                    WHERE evidence.source_run_id = source_runs.source_run_id
                )
                ELSE unique_candidates_count
            END
        WHERE source_kind = 'cts'
          AND status = 'completed'
          AND (cards_scanned_count = 0 OR unique_candidates_count = 0)
          AND EXISTS (
              SELECT 1
              FROM candidate_evidence AS evidence
              WHERE evidence.source_run_id = source_runs.source_run_id
          )
        """
    )
