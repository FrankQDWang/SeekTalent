from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from seektalent_ui.redaction import redact_event_payload, redact_text


DEFAULT_TENANT_ID = "local"
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_WORKSPACE_NAME = "Default Workspace"
SESSION_TTL_HOURS = 12
LOGIN_LOCKOUT_FAILURE_LIMIT = 5
LOGIN_LOCKOUT_WINDOW_SECONDS = 300
LOGIN_ATTEMPT_EMAIL_MAX = 254
LOGIN_ATTEMPT_REASON_MAX = 64
LOGIN_ATTEMPT_IP_MAX = 64
LOGIN_ATTEMPT_USER_AGENT_MAX = 512
SOURCE_CONNECTION_WARNING_MAX = 500


class BootstrapAlreadyCompleteError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkbenchUser:
    user_id: str
    email: str
    display_name: str
    role: Literal["admin", "member"]
    workspace_id: str


@dataclass(frozen=True)
class WorkbenchWorkspace:
    workspace_id: str
    name: str


@dataclass(frozen=True)
class WorkbenchSourceRun:
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    status: Literal["queued", "blocked", "running", "completed", "failed"]
    auth_state: Literal["not_required", "login_required"]
    warning_code: str | None
    warning_message: str | None
    cards_scanned_count: int = 0
    unique_candidates_count: int = 0


SourceConnectionStatus = Literal[
    "login_required",
    "login_in_progress",
    "verification_required",
    "connected",
    "expired",
    "blocked",
    "disconnected",
]


@dataclass(frozen=True)
class WorkbenchSourceConnection:
    connection_id: str
    source_kind: Literal["liepin"]
    status: SourceConnectionStatus
    warning_code: str | None
    warning_message: str | None
    provider_account_hash: str | None
    created_at: str
    updated_at: str
    connected_at: str | None


@dataclass(frozen=True)
class WorkbenchRequirementTriage:
    session_id: str
    status: Literal["draft", "approved"]
    must_haves: list[str]
    nice_to_haves: list[str]
    synonyms: list[str]
    seniority_filters: list[str]
    exclusions: list[str]
    generated_query_hints: list[str]
    created_at: str
    updated_at: str
    approved_at: str | None


@dataclass(frozen=True)
class WorkbenchSession:
    session_id: str
    workspace_id: str
    owner_user_id: str
    job_title: str
    jd_text: str
    notes: str
    status: Literal["draft"]
    source_runs: list[WorkbenchSourceRun]
    requirement_triage: WorkbenchRequirementTriage


@dataclass(frozen=True)
class WorkbenchSourceRunJob:
    job_id: str
    source_run_id: str
    session_id: str
    source_kind: Literal["cts", "liepin"]
    status: Literal["queued", "running", "completed", "failed"]
    attempt_count: int
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchEvent:
    global_seq: int
    session_seq: int | None
    session_id: str | None
    source_run_id: str | None
    source_kind: Literal["cts", "liepin"] | None
    event_name: str
    payload: dict[str, object]
    created_at: str


CandidateEvidenceLevel = Literal["card", "detail", "final"]
CandidateReviewStatus = Literal["new", "promising", "rejected"]


@dataclass(frozen=True)
class WorkbenchCandidateEvidence:
    evidence_id: str
    review_item_id: str
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    evidence_level: CandidateEvidenceLevel
    resume_id: str
    score: int | None
    fit_bucket: str | None
    matched_must_haves: list[str]
    matched_preferences: list[str]
    missing_risks: list[str]
    strengths: list[str]
    weaknesses: list[str]
    created_at: str


@dataclass(frozen=True)
class WorkbenchCandidateReviewItem:
    review_item_id: str
    session_id: str
    status: CandidateReviewStatus
    note: str
    display_name: str
    title: str
    company: str
    location: str
    summary: str
    aggregate_score: int | None
    fit_bucket: str | None
    source_badges: list[str]
    evidence_level: CandidateEvidenceLevel
    matched_must_haves: list[str]
    matched_preferences: list[str]
    missing_risks: list[str]
    strengths: list[str]
    weaknesses: list[str]
    evidence: list[WorkbenchCandidateEvidence]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchSourceRunJobContext:
    job: WorkbenchSourceRunJob
    session: WorkbenchSession
    triage: WorkbenchRequirementTriage


@dataclass(frozen=True)
class UserSessionTokens:
    session_token: str
    csrf_token: str


class WorkbenchStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._initialized = False

    def bootstrap_admin(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
    ) -> tuple[WorkbenchUser, WorkbenchWorkspace]:
        email = _normalize_email(email)
        display_name = display_name.strip()
        if not email or not display_name or not password_hash:
            raise ValueError("Bootstrap requires email, display name, and password hash.")
        now = _now_iso()
        user_id = f"user_{uuid.uuid4().hex[:16]}"
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            if existing is not None:
                raise BootstrapAlreadyCompleteError("Bootstrap admin already exists.")
            conn.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at) VALUES (?, ?, ?)",
                (DEFAULT_TENANT_ID, "Local", now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO workspaces (workspace_id, tenant_id, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (DEFAULT_WORKSPACE_ID, DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_NAME, now),
            )
            conn.execute(
                """
                INSERT INTO users (user_id, email, display_name, password_hash, disabled_at, created_at)
                VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (user_id, email, display_name, password_hash, now),
            )
            conn.execute(
                """
                INSERT INTO workspace_memberships (workspace_id, user_id, role, created_at)
                VALUES (?, ?, 'admin', ?)
                """,
                (DEFAULT_WORKSPACE_ID, user_id, now),
            )
        return (
            WorkbenchUser(
                user_id=user_id,
                email=email,
                display_name=display_name,
                role="admin",
                workspace_id=DEFAULT_WORKSPACE_ID,
            ),
            WorkbenchWorkspace(workspace_id=DEFAULT_WORKSPACE_ID, name=DEFAULT_WORKSPACE_NAME),
        )

    def get_user_for_login(self, *, email: str) -> tuple[WorkbenchUser, str, bool] | None:
        self._initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.user_id, u.email, u.display_name, u.password_hash, u.disabled_at,
                       m.workspace_id, m.role
                FROM users AS u
                JOIN workspace_memberships AS m ON m.user_id = u.user_id
                WHERE u.email = ?
                ORDER BY m.created_at ASC
                LIMIT 1
                """,
                (_normalize_email(email),),
            ).fetchone()
        if row is None:
            return None
        return _user_from_row(row), row["password_hash"], row["disabled_at"] is not None

    def record_login_attempt(
        self,
        *,
        email: str,
        success: bool,
        reason: str,
        user_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self._initialize()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO login_attempts (
                    attempt_id, email, success, reason, user_id, ip_address, user_agent, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"attempt_{uuid.uuid4().hex[:16]}",
                    _bounded_text(_normalize_email(email), LOGIN_ATTEMPT_EMAIL_MAX) or "unknown",
                    int(success),
                    _bounded_text(reason, LOGIN_ATTEMPT_REASON_MAX) or "unknown",
                    user_id,
                    _bounded_text(ip_address, LOGIN_ATTEMPT_IP_MAX),
                    _bounded_text(user_agent, LOGIN_ATTEMPT_USER_AGENT_MAX),
                    _now_iso(),
                ),
            )

    def is_login_locked(self, *, email: str, ip_address: str | None) -> bool:
        self._initialize()
        safe_email = _bounded_text(_normalize_email(email), LOGIN_ATTEMPT_EMAIL_MAX) or "unknown"
        safe_ip = _bounded_text(ip_address, LOGIN_ATTEMPT_IP_MAX)
        cutoff = _iso(_now() - timedelta(seconds=LOGIN_LOCKOUT_WINDOW_SECONDS))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS failed_count
                FROM login_attempts
                WHERE email = ?
                  AND success = 0
                  AND created_at >= ?
                  AND ((ip_address IS NULL AND ? IS NULL) OR ip_address = ?)
                """,
                (safe_email, cutoff, safe_ip, safe_ip),
            ).fetchone()
        return row is not None and row["failed_count"] >= LOGIN_LOCKOUT_FAILURE_LIMIT

    def create_user_session(self, *, user_id: str, workspace_id: str) -> UserSessionTokens:
        session_token = secrets.token_urlsafe(32)
        session_digest = _session_digest(session_token)
        csrf_token = secrets.token_urlsafe(32)
        csrf_digest = _session_digest(csrf_token)
        now = _now()
        expires_at = now + timedelta(hours=SESSION_TTL_HOURS)
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE user_sessions
                SET revoked_at = ?
                WHERE user_id = ? AND workspace_id = ? AND revoked_at IS NULL
                """,
                (_iso(now), user_id, workspace_id),
            )
            conn.execute(
                """
                INSERT INTO user_sessions (
                    session_id, user_id, workspace_id, csrf_token_digest,
                    issued_at, expires_at, revoked_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (session_digest, user_id, workspace_id, csrf_digest, _iso(now), _iso(expires_at), _iso(now)),
            )
        return UserSessionTokens(session_token=session_token, csrf_token=csrf_token)

    def get_user_by_session(self, *, session_digest: str | None) -> WorkbenchUser | None:
        return self._get_user_by_session(session_digest=session_digest, touch_last_seen=True)

    def get_user_by_session_readonly(self, *, session_digest: str | None) -> WorkbenchUser | None:
        return self._get_user_by_session(session_digest=session_digest, touch_last_seen=False)

    def _get_user_by_session(self, *, session_digest: str | None, touch_last_seen: bool) -> WorkbenchUser | None:
        if not session_digest:
            return None
        self._initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.expires_at, s.revoked_at, s.last_seen_at,
                       u.user_id, u.email, u.display_name, u.disabled_at,
                       m.workspace_id, m.role
                FROM user_sessions AS s
                JOIN users AS u ON u.user_id = s.user_id
                JOIN workspace_memberships AS m
                  ON m.user_id = u.user_id AND m.workspace_id = s.workspace_id
                WHERE s.session_id = ?
                """,
                (session_digest,),
            ).fetchone()
            if row is None:
                return None
            if row["revoked_at"] is not None or row["disabled_at"] is not None:
                return None
            if _parse_iso(row["expires_at"]) <= _now():
                return None
            if touch_last_seen and _parse_iso(row["last_seen_at"]) <= _now() - timedelta(seconds=60):
                conn.execute(
                    "UPDATE user_sessions SET last_seen_at = ? WHERE session_id = ?",
                    (_now_iso(), session_digest),
                )
        return _user_from_row(row)

    def revoke_user_session(self, *, session_digest: str | None) -> None:
        if not session_digest:
            return
        self._initialize()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE user_sessions
                SET revoked_at = ?
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (_now_iso(), session_digest),
            )

    def rotate_session_csrf(self, *, session_digest: str) -> str:
        csrf_token = secrets.token_urlsafe(32)
        self._initialize()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE user_sessions
                SET csrf_token_digest = ?
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (_session_digest(csrf_token), session_digest),
            )
        return csrf_token

    def verify_session_csrf(self, *, session_digest: str, csrf_token: str | None) -> bool:
        if not csrf_token:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT csrf_token_digest
                FROM user_sessions
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (session_digest,),
            ).fetchone()
        if row is None or row["csrf_token_digest"] is None:
            return False
        return secrets.compare_digest(row["csrf_token_digest"], _session_digest(csrf_token))

    def create_workbench_session(
        self,
        *,
        user: WorkbenchUser,
        job_title: str,
        jd_text: str,
        notes: str,
    ) -> WorkbenchSession:
        now = _now_iso()
        session_id = f"session_{uuid.uuid4().hex[:16]}"
        source_runs = [
            WorkbenchSourceRun(
                source_run_id=f"src_{uuid.uuid4().hex[:16]}",
                source_kind="cts",
                status="queued",
                auth_state="not_required",
                warning_code=None,
                warning_message=None,
            ),
            WorkbenchSourceRun(
                source_run_id=f"src_{uuid.uuid4().hex[:16]}",
                source_kind="liepin",
                status="blocked",
                auth_state="login_required",
                warning_code="login_required",
                warning_message="Liepin login is not connected yet.",
            ),
        ]
        triage = WorkbenchRequirementTriage(
            session_id=session_id,
            status="draft",
            must_haves=[],
            nice_to_haves=[],
            synonyms=[],
            seniority_filters=[],
            exclusions=[],
            generated_query_hints=[],
            created_at=now,
            updated_at=now,
            approved_at=None,
        )
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, tenant_id, workspace_id, user_id, job_title, jd_text, notes,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    session_id,
                    DEFAULT_TENANT_ID,
                    user.workspace_id,
                    user.user_id,
                    job_title,
                    jd_text,
                    notes,
                    now,
                    now,
                ),
            )
            for source_run in source_runs:
                conn.execute(
                    """
                    INSERT INTO source_runs (
                        source_run_id, session_id, tenant_id, workspace_id, user_id, source_kind,
                        status, auth_state, health_state, warning_code, warning_message, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?)
                    """,
                    (
                        source_run.source_run_id,
                        session_id,
                        DEFAULT_TENANT_ID,
                        user.workspace_id,
                        user.user_id,
                        source_run.source_kind,
                        source_run.status,
                        source_run.auth_state,
                        source_run.warning_code,
                        source_run.warning_message,
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO session_requirement_triage (
                    session_id, tenant_id, workspace_id, user_id, status,
                    must_haves_json, nice_to_haves_json, synonyms_json,
                    seniority_filters_json, exclusions_json, generated_query_hints_json,
                    created_at, updated_at, approved_at
                )
                VALUES (?, ?, ?, ?, 'draft', '[]', '[]', '[]', '[]', '[]', '[]', ?, ?, NULL)
                """,
                (session_id, DEFAULT_TENANT_ID, user.workspace_id, user.user_id, now, now),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="session_created",
                payload={"sessionId": session_id},
            )
        return WorkbenchSession(
            session_id=session_id,
            workspace_id=user.workspace_id,
            owner_user_id=user.user_id,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            status="draft",
            source_runs=source_runs,
            requirement_triage=triage,
        )

    def list_workbench_sessions(self, *, user: WorkbenchUser) -> list[WorkbenchSession]:
        self._initialize()
        self.reconcile_expired_running_jobs()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE workspace_id = ? AND user_id = ?
                ORDER BY created_at DESC, session_id DESC
                """,
                (user.workspace_id, user.user_id),
            ).fetchall()
            session_ids = [row["session_id"] for row in rows]
            runs_by_session = _source_runs_by_session(conn, session_ids)
            triage_by_session = _triage_by_session(conn, session_ids)
        return [
            _session_from_row(row, runs_by_session.get(row["session_id"], []), triage_by_session[row["session_id"]])
            for row in rows
        ]

    def get_workbench_session(self, *, user: WorkbenchUser, session_id: str) -> WorkbenchSession | None:
        self._initialize()
        self.reconcile_expired_running_jobs()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE workspace_id = ? AND user_id = ? AND session_id = ?
                """,
                (user.workspace_id, user.user_id, session_id),
            ).fetchone()
            if row is None:
                return None
            source_runs = _source_runs_by_session(conn, [session_id]).get(session_id, [])
            triage = _triage_by_session(conn, [session_id])[session_id]
        return _session_from_row(row, source_runs, triage)

    def list_source_connections(self, *, user: WorkbenchUser) -> list[WorkbenchSourceConnection]:
        self._initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ?
                ORDER BY source_kind ASC, created_at ASC
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id),
            ).fetchall()
        return [_source_connection_from_row(row) for row in rows]

    def get_source_connection(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
    ) -> WorkbenchSourceConnection | None:
        self._initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND connection_id = ?
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, connection_id),
            ).fetchone()
        return _source_connection_from_row(row) if row is not None else None

    def get_or_create_liepin_source_connection(
        self,
        *,
        user: WorkbenchUser,
    ) -> tuple[WorkbenchSourceConnection, bool]:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND source_kind = 'liepin'
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id),
            ).fetchone()
            if existing is not None:
                return _source_connection_from_row(existing), False
            connection_id = f"conn_{uuid.uuid4().hex[:16]}"
            warning_message = "Liepin login has not been connected yet."
            conn.execute(
                """
                INSERT INTO source_connections (
                    connection_id, tenant_id, workspace_id, user_id, source_kind, status,
                    warning_code, warning_message, created_at, updated_at, connected_at
                )
                VALUES (?, ?, ?, ?, 'liepin', 'login_required', 'login_required', ?, ?, ?, NULL)
                """,
                (connection_id, DEFAULT_TENANT_ID, user.workspace_id, user.user_id, warning_message, now, now),
            )
            _append_connection_status_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                connection_id=connection_id,
                source_kind="liepin",
                status="login_required",
                event_name="source_connection_created",
                payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "login_required"},
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=None,
                source_run_id=None,
                source_kind="liepin",
                event_name="source_connection_status_changed",
                payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "login_required"},
            )
            row = conn.execute("SELECT * FROM source_connections WHERE connection_id = ?", (connection_id,)).fetchone()
        return _source_connection_from_row(row), True

    def start_liepin_login_handoff(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        provider_account_hash: str | None = None,
        warning_code: str | None = "relay_pending_worker",
        warning_message: str | None = (
            "Isolated server-side login relay is prepared, but the managed browser interaction bridge is not connected in this slice."
        ),
    ) -> WorkbenchSourceConnection | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND connection_id = ? AND source_kind = 'liepin'
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, connection_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE source_connections
                SET status = 'login_in_progress',
                    warning_code = ?,
                    warning_message = ?,
                    provider_account_hash = COALESCE(?, provider_account_hash),
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (warning_code, warning_message, provider_account_hash, now, connection_id),
            )
            _append_connection_status_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                connection_id=connection_id,
                source_kind="liepin",
                status="login_in_progress",
                event_name="source_connection_login_started",
                payload={
                    "connectionId": connection_id,
                    "sourceKind": "liepin",
                    "status": "login_in_progress",
                    "warningCode": warning_code,
                },
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=None,
                source_run_id=None,
                source_kind="liepin",
                event_name="source_connection_status_changed",
                payload={
                    "connectionId": connection_id,
                    "sourceKind": "liepin",
                    "status": "login_in_progress",
                    "warningCode": warning_code,
                },
            )
            updated = conn.execute("SELECT * FROM source_connections WHERE connection_id = ?", (connection_id,)).fetchone()
        return _source_connection_from_row(updated)

    def mark_liepin_connection_connected(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        provider_account_hash: str | None,
    ) -> WorkbenchSourceConnection | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND connection_id = ? AND source_kind = 'liepin'
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, connection_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE source_connections
                SET status = 'connected',
                    warning_code = NULL,
                    warning_message = NULL,
                    provider_account_hash = COALESCE(?, provider_account_hash),
                    connected_at = ?,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (provider_account_hash, now, now, connection_id),
            )
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'queued',
                    auth_state = 'not_required',
                    warning_code = NULL,
                    warning_message = NULL
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND user_id = ?
                  AND source_kind = 'liepin'
                  AND status = 'blocked'
                  AND auth_state = 'login_required'
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id),
            )
            _append_connection_status_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                connection_id=connection_id,
                source_kind="liepin",
                status="connected",
                event_name="source_connection_login_completed",
                payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "connected"},
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=None,
                source_run_id=None,
                source_kind="liepin",
                event_name="source_connection_status_changed",
                payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "connected"},
            )
            updated = conn.execute("SELECT * FROM source_connections WHERE connection_id = ?", (connection_id,)).fetchone()
        return _source_connection_from_row(updated)

    def get_liepin_source_connection_for_job_context(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
    ) -> WorkbenchSourceConnection | None:
        self._initialize()
        user = WorkbenchUser(
            user_id=context.session.owner_user_id,
            email="",
            display_name="",
            role="member",
            workspace_id=context.session.workspace_id,
        )
        with self._connect() as conn:
            row = _liepin_connection_for_user_conn(conn, user=user)
        return _source_connection_from_row(row) if row is not None else None

    def get_requirement_triage(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchRequirementTriage | None:
        self._initialize()
        with self._connect() as conn:
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            triage = _triage_by_session(conn, [session_id]).get(session_id)
        return triage

    def update_requirement_triage(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        must_haves: list[str],
        nice_to_haves: list[str],
        synonyms: list[str],
        seniority_filters: list[str],
        exclusions: list[str],
        generated_query_hints: list[str],
    ) -> WorkbenchRequirementTriage | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            conn.execute(
                """
                UPDATE session_requirement_triage
                SET status = 'draft',
                    must_haves_json = ?,
                    nice_to_haves_json = ?,
                    synonyms_json = ?,
                    seniority_filters_json = ?,
                    exclusions_json = ?,
                    generated_query_hints_json = ?,
                    updated_at = ?,
                    approved_at = NULL
                WHERE session_id = ? AND workspace_id = ? AND user_id = ?
                """,
                (
                    _json_list(must_haves),
                    _json_list(nice_to_haves),
                    _json_list(synonyms),
                    _json_list(seniority_filters),
                    _json_list(exclusions),
                    _json_list(generated_query_hints),
                    now,
                    session_id,
                    user.workspace_id,
                    user.user_id,
                ),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="requirement_triage_updated",
                payload={
                    "sessionId": session_id,
                    "mustHaveCount": len(must_haves),
                    "niceToHaveCount": len(nice_to_haves),
                    "synonymCount": len(synonyms),
                    "seniorityFilterCount": len(seniority_filters),
                    "exclusionCount": len(exclusions),
                    "generatedQueryHintCount": len(generated_query_hints),
                },
            )
            triage = _triage_by_session(conn, [session_id])[session_id]
        return triage

    def approve_requirement_triage(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchRequirementTriage | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            conn.execute(
                """
                UPDATE session_requirement_triage
                SET status = 'approved', updated_at = ?, approved_at = ?
                WHERE session_id = ? AND workspace_id = ? AND user_id = ?
                """,
                (now, now, session_id, user.workspace_id, user.user_id),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="requirement_triage_approved",
                payload={"sessionId": session_id},
            )
            triage = _triage_by_session(conn, [session_id])[session_id]
        return triage

    def start_source_run_job(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_run_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[WorkbenchSourceRun, WorkbenchSourceRunJob, bool] | None:
        self._initialize()
        self.reconcile_expired_running_jobs()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run_row = conn.execute(
                """
                SELECT sr.*
                FROM source_runs AS sr
                JOIN sessions AS s ON s.session_id = sr.session_id
                WHERE sr.source_run_id = ?
                  AND sr.session_id = ?
                  AND sr.workspace_id = ?
                  AND sr.user_id = ?
                  AND s.user_id = ?
                """,
                (source_run_id, session_id, user.workspace_id, user.user_id, user.user_id),
            ).fetchone()
            if run_row is None:
                return None
            source_run = _source_run_from_row(run_row)
            if source_run.source_kind == "liepin":
                connection = _liepin_connection_for_user_conn(conn, user=user)
                if connection is None or connection["status"] != "connected":
                    conn.execute(
                        """
                        UPDATE source_runs
                        SET status = 'blocked',
                            auth_state = 'login_required',
                            warning_code = 'login_required',
                            warning_message = 'Liepin login is not connected yet.'
                        WHERE source_run_id = ?
                        """,
                        (source_run_id,),
                    )
                    raise PermissionError("liepin_connection_not_connected")
                source_run = WorkbenchSourceRun(
                    source_run_id=source_run.source_run_id,
                    source_kind=source_run.source_kind,
                    status=source_run.status,
                    auth_state="not_required",
                    warning_code=None,
                    warning_message=None,
                    cards_scanned_count=source_run.cards_scanned_count,
                    unique_candidates_count=source_run.unique_candidates_count,
                )
            elif source_run.source_kind != "cts":
                raise ValueError("source_not_implemented")
            triage = _triage_by_session(conn, [session_id])[session_id]
            if triage.status != "approved":
                raise PermissionError("requirement_triage_not_approved")
            existing = conn.execute(
                """
                SELECT *
                FROM source_run_jobs
                WHERE source_run_id = ?
                  AND (status IN ('queued', 'running') OR (? IS NOT NULL AND idempotency_key = ?))
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (source_run_id, idempotency_key, idempotency_key),
            ).fetchone()
            if existing is not None:
                return source_run, _job_from_row(existing), False
            if source_run.status in {"completed", "failed"}:
                raise RuntimeError("source_run_already_terminal")
            job_id = f"job_{uuid.uuid4().hex[:16]}"
            conn.execute(
                """
                INSERT INTO source_run_jobs (
                    job_id, tenant_id, workspace_id, user_id, session_id, source_run_id, source_kind,
                    status, lease_owner, lease_expires_at, idempotency_key, attempt_count,
                    error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, 0, NULL, ?, ?)
                """,
                (
                    job_id,
                    DEFAULT_TENANT_ID,
                    user.workspace_id,
                    user.user_id,
                    session_id,
                    source_run_id,
                    source_run.source_kind,
                    _bounded_text(idempotency_key, 128),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'queued', auth_state = ?, warning_code = NULL, warning_message = NULL
                WHERE source_run_id = ?
                """,
                (source_run.auth_state, source_run_id),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind=source_run.source_kind,
                event_name="source_run_queued",
                payload={"sourceRunId": source_run_id, "sourceKind": source_run.source_kind},
            )
            job = _job_from_row(
                conn.execute("SELECT * FROM source_run_jobs WHERE job_id = ?", (job_id,)).fetchone()
            )
            updated_run = _source_run_from_row(
                conn.execute("SELECT * FROM source_runs WHERE source_run_id = ?", (source_run_id,)).fetchone()
            )
        return updated_run, job, True

    def claim_next_source_run_job(
        self,
        *,
        owner_id: str,
        lease_expires_at: str,
    ) -> WorkbenchSourceRunJobContext | None:
        self._initialize()
        self.reconcile_expired_running_jobs()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM source_run_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC, job_id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE source_run_jobs
                SET status = 'running',
                    lease_owner = ?,
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (owner_id, lease_expires_at, now, row["job_id"]),
            )
            if conn.total_changes <= 0:
                return None
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'running', warning_code = NULL, warning_message = NULL
                WHERE source_run_id = ?
                """,
                (row["source_run_id"],),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                source_run_id=row["source_run_id"],
                source_kind=row["source_kind"],
                event_name="source_run_started",
                payload={"sourceRunId": row["source_run_id"], "sourceKind": row["source_kind"]},
            )
            job = _job_from_row(conn.execute("SELECT * FROM source_run_jobs WHERE job_id = ?", (row["job_id"],)).fetchone())
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (row["session_id"],)).fetchone()
            source_runs = _source_runs_by_session(conn, [row["session_id"]]).get(row["session_id"], [])
            triage = _triage_by_session(conn, [row["session_id"]])[row["session_id"]]
        return WorkbenchSourceRunJobContext(
            job=job,
            session=_session_from_row(session_row, source_runs, triage),
            triage=triage,
        )

    def mark_source_run_completed(self, *, job: WorkbenchSourceRunJob) -> None:
        self._finish_source_run_job(job=job, status="completed", error_message=None, event_name="source_run_completed")

    def mark_source_run_failed(self, *, job: WorkbenchSourceRunJob, error_message: str) -> None:
        safe_error_message = redact_text(_bounded_text(error_message, 500)) or "Source run failed."
        self._finish_source_run_job(
            job=job,
            status="failed",
            error_message=safe_error_message,
            event_name="source_run_failed",
        )

    def extend_source_run_job_lease(self, *, job_id: str, owner_id: str, lease_expires_at: str) -> bool:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE source_run_jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND status = 'running'
                """,
                (lease_expires_at, _now_iso(), job_id, owner_id),
            )
        return cursor.rowcount == 1

    def reconcile_expired_running_jobs(self) -> int:
        self._initialize()
        now = _now_iso()
        safe_error_message = "Source run job lease expired."
        reconciled = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM source_run_jobs
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at ASC, job_id ASC
                """,
                (now,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE source_run_jobs
                    SET status = 'failed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        error_message = ?,
                        updated_at = ?
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (safe_error_message, now, row["job_id"]),
                )
                conn.execute(
                    """
                    UPDATE source_runs
                    SET status = 'failed',
                        warning_code = 'job_lease_expired',
                        warning_message = ?
                    WHERE source_run_id = ?
                    """,
                    (safe_error_message, row["source_run_id"]),
                )
                _append_workbench_event_conn(
                    conn,
                    tenant_id=row["tenant_id"],
                    workspace_id=row["workspace_id"],
                    user_id=row["user_id"],
                    session_id=row["session_id"],
                    source_run_id=row["source_run_id"],
                    source_kind=row["source_kind"],
                    event_name="source_run_failed",
                    payload={
                        "sourceRunId": row["source_run_id"],
                        "sourceKind": row["source_kind"],
                        "status": "failed",
                        "errorMessage": safe_error_message,
                        "reason": "job_lease_expired",
                    },
                )
                reconciled += 1
        return reconciled

    def append_workbench_event(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str | None,
        source_run_id: str | None,
        source_kind: Literal["cts", "liepin"] | None,
        event_name: str,
        payload: dict[str, object],
    ) -> WorkbenchEvent:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return _append_workbench_event_conn(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind=source_kind,
                event_name=event_name,
                payload=payload,
            )

    def list_workbench_events(
        self,
        *,
        user: WorkbenchUser,
        after_seq: int,
        limit: int = 100,
    ) -> list[WorkbenchEvent]:
        self._initialize()
        safe_limit = min(max(limit, 1), 200)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM session_events
                WHERE workspace_id = ? AND user_id = ? AND global_seq > ?
                ORDER BY global_seq ASC
                LIMIT ?
                """,
                (user.workspace_id, user.user_id, max(after_seq, 0), safe_limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def persist_cts_candidate_results(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        artifacts: object,
    ) -> list[WorkbenchCandidateReviewItem]:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            review_item_ids = self._persist_cts_candidate_results_conn(
                conn,
                context=context,
                artifacts=artifacts,
                now=now,
            )
        return self._list_candidate_review_items_by_ids(
            user=WorkbenchUser(
                user_id=context.session.owner_user_id,
                email="",
                display_name="",
                role="member",
                workspace_id=context.session.workspace_id,
            ),
            session_id=context.session.session_id,
            review_item_ids=review_item_ids,
        )

    def complete_cts_source_run_with_candidate_results(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        artifacts: object,
    ) -> list[WorkbenchCandidateReviewItem]:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM source_run_jobs WHERE job_id = ?", (context.job.job_id,)).fetchone()
            if row is None or row["status"] != "running":
                return []
            review_item_ids = self._persist_cts_candidate_results_conn(
                conn,
                context=context,
                artifacts=artifacts,
                now=now,
            )
            self._finish_source_run_job_conn(
                conn,
                row=row,
                status="completed",
                error_message=None,
                event_name="source_run_completed",
                now=now,
            )
        return self._list_candidate_review_items_by_ids(
            user=WorkbenchUser(
                user_id=context.session.owner_user_id,
                email="",
                display_name="",
                role="member",
                workspace_id=context.session.workspace_id,
            ),
            session_id=context.session.session_id,
            review_item_ids=review_item_ids,
        )

    def complete_liepin_card_source_run_with_search_result(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        result: object,
    ) -> list[WorkbenchCandidateReviewItem]:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM source_run_jobs WHERE job_id = ?", (context.job.job_id,)).fetchone()
            if row is None or row["status"] != "running":
                return []
            review_item_ids = self._persist_liepin_card_candidate_results_conn(
                conn,
                context=context,
                result=result,
                now=now,
            )
            raw_candidate_count = _int_or_none(_attr(result, "raw_candidate_count"))
            cards_scanned_count = raw_candidate_count if raw_candidate_count is not None else len(review_item_ids)
            conn.execute(
                """
                UPDATE source_runs
                SET cards_scanned_count = ?,
                    unique_candidates_count = ?
                WHERE source_run_id = ?
                """,
                (cards_scanned_count, len(review_item_ids), context.job.source_run_id),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.job.source_run_id,
                source_kind="liepin",
                event_name="liepin_card_search_completed",
                payload={
                    "sourceRunId": context.job.source_run_id,
                    "sourceKind": "liepin",
                    "cardsScannedCount": cards_scanned_count,
                    "uniqueCandidatesCount": len(review_item_ids),
                },
            )
            self._finish_source_run_job_conn(
                conn,
                row=row,
                status="completed",
                error_message=None,
                event_name="source_run_completed",
                now=now,
            )
        return self._list_candidate_review_items_by_ids(
            user=WorkbenchUser(
                user_id=context.session.owner_user_id,
                email="",
                display_name="",
                role="member",
                workspace_id=context.session.workspace_id,
            ),
            session_id=context.session.session_id,
            review_item_ids=review_item_ids,
        )

    def _persist_cts_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchSourceRunJobContext,
        artifacts: object,
        now: str,
    ) -> list[str]:
        final_result = getattr(artifacts, "final_result", None)
        final_candidates = list(getattr(final_result, "candidates", []) or [])
        if not final_candidates:
            return []
        candidate_store = getattr(artifacts, "candidate_store", {}) or {}
        normalized_store = getattr(artifacts, "normalized_store", {}) or {}
        review_item_ids: list[str] = []
        for candidate in final_candidates:
            provider_resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 128)
            if not provider_resume_id:
                continue
            workbench_resume_id = _stable_id("candidate", context.session.session_id, provider_resume_id)
            normalized = _mapping_get(normalized_store, provider_resume_id)
            raw_candidate = _mapping_get(candidate_store, provider_resume_id)
            review_item_id = _stable_id("review", context.session.session_id, provider_resume_id)
            evidence_id = _stable_id("evidence", context.job.source_run_id, provider_resume_id, "final")
            display_name = _safe_candidate_text(_attr(normalized, "candidate_name"), 160)
            if not display_name:
                display_name = f"Candidate {workbench_resume_id[-8:]}"
            title = _safe_candidate_text(_attr(normalized, "current_title"), 240)
            if not title:
                title = _safe_candidate_text(_attr(normalized, "headline"), 240) or ""
            company = _safe_candidate_text(_attr(normalized, "current_company"), 240) or ""
            location = _safe_candidate_text(_first(_attr(normalized, "locations")), 160) or ""
            summary = (
                _safe_candidate_text(_attr(candidate, "match_summary"), 1000)
                or _safe_candidate_text(_attr(candidate, "why_selected"), 1000)
                or ""
            )
            score = _int_or_none(_attr(candidate, "final_score"))
            fit_bucket = _safe_candidate_text(_attr(candidate, "fit_bucket"), 64)
            matched_must_haves = _safe_list(_attr(candidate, "matched_must_haves"), 20, 240)
            matched_preferences = _safe_list(_attr(candidate, "matched_preferences"), 20, 240)
            strengths = _safe_list(_attr(candidate, "strengths"), 12, 300)
            weaknesses = _safe_list(_attr(candidate, "weaknesses"), 12, 300)
            risk_flags = _safe_list(_attr(candidate, "risk_flags"), 12, 300)
            missing_risks = [*weaknesses, *risk_flags]
            provider_key_hash = _sha256_text(
                _safe_candidate_text(_attr(raw_candidate, "source_resume_id"), 256) or provider_resume_id
            )
            conn.execute(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, summary,
                    aggregate_score, fit_bucket, review_status, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
                ON CONFLICT(review_item_id) DO UPDATE SET
                    primary_evidence_id = excluded.primary_evidence_id,
                    display_name = excluded.display_name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    summary = excluded.summary,
                    aggregate_score = excluded.aggregate_score,
                    fit_bucket = excluded.fit_bucket,
                    updated_at = excluded.updated_at
                """,
                (
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    summary,
                    score,
                    fit_bucket,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    missing_risks_json = excluded.missing_risks_json,
                    strengths_json = excluded.strengths_json,
                    weaknesses_json = excluded.weaknesses_json
                """,
                (
                    evidence_id,
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    context.job.source_run_id,
                    context.job.source_kind,
                    provider_key_hash,
                    workbench_resume_id,
                    score,
                    fit_bucket,
                    _json_list(matched_must_haves),
                    _json_list(matched_preferences),
                    _json_list(missing_risks),
                    _json_list(strengths),
                    _json_list(weaknesses),
                    now,
                ),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.job.source_run_id,
                source_kind=context.job.source_kind,
                event_name="candidate_review_item_upserted",
                payload={
                    "reviewItemId": review_item_id,
                    "sourceRunId": context.job.source_run_id,
                    "sourceKind": context.job.source_kind,
                    "candidateId": workbench_resume_id,
                    "score": score,
                },
            )
            review_item_ids.append(review_item_id)
        return review_item_ids

    def _persist_liepin_card_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchSourceRunJobContext,
        result: object,
        now: str,
    ) -> list[str]:
        candidates = list(_attr(result, "candidates") or [])
        snapshots = list(_attr(result, "provider_snapshots") or [])
        review_item_ids: list[str] = []
        for index, candidate in enumerate(candidates):
            provider_resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 128)
            provider_key = (
                _safe_candidate_text(_attr(candidate, "source_resume_id"), 256)
                or _safe_candidate_text(_attr(candidate, "dedup_key"), 256)
                or provider_resume_id
            )
            if not provider_resume_id or not provider_key:
                continue
            workbench_resume_id = _stable_id("candidate", context.session.session_id, "liepin", provider_key)
            review_item_id = _stable_id("review", context.session.session_id, "liepin", provider_key)
            evidence_id = _stable_id("evidence", context.job.source_run_id, provider_key, "card")
            snapshot = snapshots[index] if index < len(snapshots) else None
            payload = _snapshot_payload(snapshot)
            display_name, title, company, location, summary = _liepin_card_display_fields(
                candidate=candidate,
                payload=payload,
                workbench_resume_id=workbench_resume_id,
            )
            matched_must_haves = _matched_terms(context.triage.must_haves, summary)
            matched_preferences = _matched_terms([*context.triage.nice_to_haves, *context.triage.synonyms], summary)
            strengths = _unique_list([*matched_must_haves[:6], *matched_preferences[:6]])
            missing_risks = ["Detail page not opened yet."]
            provider_key_hash = _sha256_text(provider_key)
            conn.execute(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, summary,
                    aggregate_score, fit_bucket, review_status, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'card', 'new', '', ?, ?)
                ON CONFLICT(review_item_id) DO UPDATE SET
                    primary_evidence_id = excluded.primary_evidence_id,
                    display_name = excluded.display_name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    summary = excluded.summary,
                    fit_bucket = excluded.fit_bucket,
                    updated_at = excluded.updated_at
                """,
                (
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    summary,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'card', ?, ?, NULL, 'card', ?, ?, ?, ?, '[]', ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    missing_risks_json = excluded.missing_risks_json,
                    strengths_json = excluded.strengths_json
                """,
                (
                    evidence_id,
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    context.job.source_run_id,
                    provider_key_hash,
                    workbench_resume_id,
                    _json_list(matched_must_haves),
                    _json_list(matched_preferences),
                    _json_list(missing_risks),
                    _json_list(strengths),
                    now,
                ),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.job.source_run_id,
                source_kind="liepin",
                event_name="candidate_review_item_upserted",
                payload={
                    "reviewItemId": review_item_id,
                    "sourceRunId": context.job.source_run_id,
                    "sourceKind": "liepin",
                    "candidateId": workbench_resume_id,
                    "evidenceLevel": "card",
                },
            )
            review_item_ids.append(review_item_id)
        return review_item_ids

    def list_candidate_review_items(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> list[WorkbenchCandidateReviewItem] | None:
        self._initialize()
        with self._connect() as conn:
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            rows = conn.execute(
                """
                SELECT *
                FROM candidate_review_items
                WHERE workspace_id = ? AND user_id = ? AND session_id = ?
                ORDER BY COALESCE(aggregate_score, -1) DESC, created_at ASC, review_item_id ASC
                """,
                (user.workspace_id, user.user_id, session_id),
            ).fetchall()
            evidence_by_review = _evidence_by_review_item(conn, [row["review_item_id"] for row in rows])
        return [_review_item_from_row(row, evidence_by_review.get(row["review_item_id"], [])) for row in rows]

    def update_candidate_review_item(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
        review_status: CandidateReviewStatus | None,
        note: str | None,
    ) -> WorkbenchCandidateReviewItem | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM candidate_review_items
                WHERE workspace_id = ? AND user_id = ? AND session_id = ? AND review_item_id = ?
                """,
                (user.workspace_id, user.user_id, session_id, review_item_id),
            ).fetchone()
            if row is None:
                return None
            next_status = review_status or row["review_status"]
            next_note = _safe_candidate_text(note if note is not None else row["note"], 2000) or ""
            if next_status == row["review_status"] and next_note == (row["note"] or ""):
                evidence = _evidence_by_review_item(conn, [review_item_id]).get(review_item_id, [])
                return _review_item_from_row(row, evidence)
            conn.execute(
                """
                UPDATE candidate_review_items
                SET review_status = ?, note = ?, updated_at = ?
                WHERE workspace_id = ? AND user_id = ? AND session_id = ? AND review_item_id = ?
                """,
                (next_status, next_note, now, user.workspace_id, user.user_id, session_id, review_item_id),
            )
            conn.execute(
                """
                INSERT INTO candidate_actions (
                    action_id, tenant_id, workspace_id, user_id, session_id,
                    review_item_id, action_kind, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"action_{uuid.uuid4().hex[:16]}",
                    DEFAULT_TENANT_ID,
                    user.workspace_id,
                    user.user_id,
                    session_id,
                    review_item_id,
                    next_status,
                    next_note,
                    now,
                ),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="candidate_review_item_updated",
                payload={"reviewItemId": review_item_id, "reviewStatus": next_status},
            )
            refreshed = conn.execute(
                "SELECT * FROM candidate_review_items WHERE review_item_id = ?",
                (review_item_id,),
            ).fetchone()
            evidence = _evidence_by_review_item(conn, [review_item_id]).get(review_item_id, [])
        return _review_item_from_row(refreshed, evidence)

    def _list_candidate_review_items_by_ids(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_ids: list[str],
    ) -> list[WorkbenchCandidateReviewItem]:
        if not review_item_ids:
            return []
        self._initialize()
        placeholders = ",".join("?" for _ in review_item_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM candidate_review_items
                WHERE workspace_id = ? AND user_id = ? AND session_id = ?
                  AND review_item_id IN ({placeholders})
                ORDER BY COALESCE(aggregate_score, -1) DESC, created_at ASC, review_item_id ASC
                """,
                (user.workspace_id, user.user_id, session_id, *review_item_ids),
            ).fetchall()
            evidence_by_review = _evidence_by_review_item(conn, [row["review_item_id"] for row in rows])
        return [_review_item_from_row(row, evidence_by_review.get(row["review_item_id"], [])) for row in rows]

    def _finish_source_run_job(
        self,
        *,
        job: WorkbenchSourceRunJob,
        status: Literal["completed", "failed"],
        error_message: str | None,
        event_name: str,
    ) -> None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM source_run_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            if row is None:
                return
            if row["status"] != "running":
                return
            self._finish_source_run_job_conn(
                conn,
                row=row,
                status=status,
                error_message=error_message,
                event_name=event_name,
                now=now,
            )

    def _finish_source_run_job_conn(
        self,
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        status: Literal["completed", "failed"],
        error_message: str | None,
        event_name: str,
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE source_run_jobs
            SET status = ?, lease_owner = NULL, lease_expires_at = NULL, error_message = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (status, error_message, now, row["job_id"]),
        )
        conn.execute(
            """
            UPDATE source_runs
            SET status = ?, warning_code = ?, warning_message = ?
            WHERE source_run_id = ?
            """,
            (
                status,
                "runtime_failed" if status == "failed" else None,
                redact_text(error_message),
                row["source_run_id"],
            ),
        )
        _append_workbench_event_conn(
            conn,
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            source_run_id=row["source_run_id"],
            source_kind=row["source_kind"],
            event_name=event_name,
            payload={
                "sourceRunId": row["source_run_id"],
                "sourceKind": row["source_kind"],
                "status": status,
                "errorMessage": redact_text(error_message),
            },
        )

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._connect() as conn:
            conn.executescript(
                """
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

                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    csrf_token_digest TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
                );

                CREATE INDEX IF NOT EXISTS idx_user_sessions_user_workspace
                ON user_sessions(user_id, workspace_id, revoked_at);

                CREATE TABLE IF NOT EXISTS login_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK(success IN (0, 1)),
                    reason TEXT NOT NULL,
                    user_id TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_login_attempts_email_created
                ON login_attempts(email, created_at);

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
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

                CREATE TABLE IF NOT EXISTS session_requirement_triage (
                    session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('draft', 'approved')),
                    must_haves_json TEXT NOT NULL,
                    nice_to_haves_json TEXT NOT NULL,
                    synonyms_json TEXT NOT NULL,
                    seniority_filters_json TEXT NOT NULL,
                    exclusions_json TEXT NOT NULL,
                    generated_query_hints_json TEXT NOT NULL,
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
                    warning_code TEXT,
                    warning_message TEXT,
                    cards_scanned_count INTEGER NOT NULL DEFAULT 0,
                    unique_candidates_count INTEGER NOT NULL DEFAULT 0,
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
                    payload_redacted_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_session_events_global
                ON session_events(tenant_id, workspace_id, global_seq);

                CREATE INDEX IF NOT EXISTS idx_session_events_session
                ON session_events(tenant_id, workspace_id, session_id, session_seq);

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
                """
            )
            _ensure_column(conn, "user_sessions", "csrf_token_digest", "TEXT")
            _ensure_column(conn, "source_run_jobs", "idempotency_key", "TEXT")
            _ensure_column(conn, "source_connections", "provider_account_hash", "TEXT")
            _ensure_column(conn, "source_runs", "cards_scanned_count", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "source_runs", "unique_candidates_count", "INTEGER NOT NULL DEFAULT 0")
        self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn


def _source_runs_by_session(
    conn: sqlite3.Connection,
    session_ids: list[str],
) -> dict[str, list[WorkbenchSourceRun]]:
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM source_runs
        WHERE session_id IN ({placeholders})
        ORDER BY CASE source_kind WHEN 'cts' THEN 0 ELSE 1 END
        """,
        session_ids,
    ).fetchall()
    runs_by_session: dict[str, list[WorkbenchSourceRun]] = {}
    for row in rows:
        runs_by_session.setdefault(row["session_id"], []).append(_source_run_from_row(row))
    return runs_by_session


def _triage_by_session(
    conn: sqlite3.Connection,
    session_ids: list[str],
) -> dict[str, WorkbenchRequirementTriage]:
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM session_requirement_triage
        WHERE session_id IN ({placeholders})
        """,
        session_ids,
    ).fetchall()
    return {row["session_id"]: _triage_from_row(row) for row in rows}


def _session_exists_for_user(conn: sqlite3.Connection, *, user: WorkbenchUser, session_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sessions
        WHERE session_id = ? AND workspace_id = ? AND user_id = ?
        """,
        (session_id, user.workspace_id, user.user_id),
    ).fetchone()
    return row is not None


def _liepin_connection_for_user_conn(conn: sqlite3.Connection, *, user: WorkbenchUser) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM source_connections
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND source_kind = 'liepin'
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id),
    ).fetchone()


def _user_from_row(row: sqlite3.Row) -> WorkbenchUser:
    return WorkbenchUser(
        user_id=row["user_id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        workspace_id=row["workspace_id"],
    )


def _source_run_from_row(row: sqlite3.Row) -> WorkbenchSourceRun:
    return WorkbenchSourceRun(
        source_run_id=row["source_run_id"],
        source_kind=row["source_kind"],
        status=row["status"],
        auth_state=row["auth_state"],
        warning_code=row["warning_code"],
        warning_message=row["warning_message"],
        cards_scanned_count=row["cards_scanned_count"],
        unique_candidates_count=row["unique_candidates_count"],
    )


def _source_connection_from_row(row: sqlite3.Row) -> WorkbenchSourceConnection:
    return WorkbenchSourceConnection(
        connection_id=row["connection_id"],
        source_kind=row["source_kind"],
        status=row["status"],
        warning_code=row["warning_code"],
        warning_message=row["warning_message"],
        provider_account_hash=row["provider_account_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        connected_at=row["connected_at"],
    )


def _triage_from_row(row: sqlite3.Row) -> WorkbenchRequirementTriage:
    return WorkbenchRequirementTriage(
        session_id=row["session_id"],
        status=row["status"],
        must_haves=_json_to_list(row["must_haves_json"]),
        nice_to_haves=_json_to_list(row["nice_to_haves_json"]),
        synonyms=_json_to_list(row["synonyms_json"]),
        seniority_filters=_json_to_list(row["seniority_filters_json"]),
        exclusions=_json_to_list(row["exclusions_json"]),
        generated_query_hints=_json_to_list(row["generated_query_hints_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        approved_at=row["approved_at"],
    )


def _job_from_row(row: sqlite3.Row) -> WorkbenchSourceRunJob:
    return WorkbenchSourceRunJob(
        job_id=row["job_id"],
        source_run_id=row["source_run_id"],
        session_id=row["session_id"],
        source_kind=row["source_kind"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_from_row(row: sqlite3.Row) -> WorkbenchEvent:
    payload = json.loads(row["payload_redacted_json"])
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return WorkbenchEvent(
        global_seq=row["global_seq"],
        session_seq=row["session_seq"],
        session_id=row["session_id"],
        source_run_id=row["source_run_id"],
        source_kind=row["source_kind"],
        event_name=row["event_name"],
        payload=payload,
        created_at=row["created_at"],
    )


def _evidence_by_review_item(
    conn: sqlite3.Connection,
    review_item_ids: list[str],
) -> dict[str, list[WorkbenchCandidateEvidence]]:
    if not review_item_ids:
        return {}
    placeholders = ",".join("?" for _ in review_item_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM candidate_evidence
        WHERE review_item_id IN ({placeholders})
        ORDER BY created_at ASC, evidence_id ASC
        """,
        review_item_ids,
    ).fetchall()
    evidence_by_review: dict[str, list[WorkbenchCandidateEvidence]] = {}
    for row in rows:
        evidence_by_review.setdefault(row["review_item_id"], []).append(_candidate_evidence_from_row(row))
    return evidence_by_review


def _candidate_evidence_from_row(row: sqlite3.Row) -> WorkbenchCandidateEvidence:
    return WorkbenchCandidateEvidence(
        evidence_id=row["evidence_id"],
        review_item_id=row["review_item_id"],
        source_run_id=row["source_run_id"],
        source_kind=row["source_kind"],
        evidence_level=row["evidence_level"],
        resume_id=row["resume_id"],
        score=row["score"],
        fit_bucket=row["fit_bucket"],
        matched_must_haves=_json_to_list(row["matched_must_haves_json"]),
        matched_preferences=_json_to_list(row["matched_preferences_json"]),
        missing_risks=_json_to_list(row["missing_risks_json"]),
        strengths=_json_to_list(row["strengths_json"]),
        weaknesses=_json_to_list(row["weaknesses_json"]),
        created_at=row["created_at"],
    )


def _review_item_from_row(
    row: sqlite3.Row,
    evidence: list[WorkbenchCandidateEvidence],
) -> WorkbenchCandidateReviewItem:
    source_badges = sorted({"CTS" if item.source_kind == "cts" else "Liepin" for item in evidence})
    evidence_level = _strongest_evidence_level(evidence)
    matched_must_haves = _unique_list(value for item in evidence for value in item.matched_must_haves)
    matched_preferences = _unique_list(value for item in evidence for value in item.matched_preferences)
    missing_risks = _unique_list(value for item in evidence for value in item.missing_risks)
    strengths = _unique_list(value for item in evidence for value in item.strengths)
    weaknesses = _unique_list(value for item in evidence for value in item.weaknesses)
    return WorkbenchCandidateReviewItem(
        review_item_id=row["review_item_id"],
        session_id=row["session_id"],
        status=row["review_status"],
        note=row["note"],
        display_name=row["display_name"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        summary=row["summary"],
        aggregate_score=row["aggregate_score"],
        fit_bucket=row["fit_bucket"],
        source_badges=source_badges,
        evidence_level=evidence_level,
        matched_must_haves=matched_must_haves,
        matched_preferences=matched_preferences,
        missing_risks=missing_risks,
        strengths=strengths,
        weaknesses=weaknesses,
        evidence=evidence,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _strongest_evidence_level(evidence: list[WorkbenchCandidateEvidence]) -> CandidateEvidenceLevel:
    rank = {"card": 0, "detail": 1, "final": 2}
    strongest: CandidateEvidenceLevel = "card"
    for item in evidence:
        if rank[item.evidence_level] > rank[strongest]:
            strongest = item.evidence_level
    return strongest


def _unique_list(values) -> list[str]:  # noqa: ANN001
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _session_from_row(
    row: sqlite3.Row,
    source_runs: list[WorkbenchSourceRun],
    requirement_triage: WorkbenchRequirementTriage,
) -> WorkbenchSession:
    return WorkbenchSession(
        session_id=row["session_id"],
        workspace_id=row["workspace_id"],
        owner_user_id=row["user_id"],
        job_title=row["job_title"],
        jd_text=row["jd_text"],
        notes=row["notes"],
        status=row["status"],
        source_runs=source_runs,
        requirement_triage=requirement_triage,
    )


def _append_workbench_event_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    source_run_id: str | None,
    source_kind: Literal["cts", "liepin"] | None,
    event_name: str,
    payload: dict[str, object],
) -> WorkbenchEvent:
    session_seq = None
    if session_id is not None:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(session_seq), 0) + 1 AS next_seq
            FROM session_events
            WHERE tenant_id = ? AND workspace_id = ? AND session_id = ?
            """,
            (tenant_id, workspace_id, session_id),
        ).fetchone()
        session_seq = int(row["next_seq"])
    redacted_payload = redact_event_payload(payload)
    if not isinstance(redacted_payload, dict):
        redacted_payload = {"value": redacted_payload}
    now = _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO session_events (
            tenant_id, workspace_id, user_id, session_id, session_seq,
            source_run_id, source_kind, event_name, payload_redacted_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            session_seq,
            source_run_id,
            source_kind,
            event_name,
            json.dumps(redacted_payload, sort_keys=True, separators=(",", ":")),
            now,
        ),
    )
    return WorkbenchEvent(
        global_seq=int(cursor.lastrowid),
        session_seq=session_seq,
        session_id=session_id,
        source_run_id=source_run_id,
        source_kind=source_kind,
        event_name=event_name,
        payload=redacted_payload,
        created_at=now,
    )


def _append_connection_status_event_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    connection_id: str,
    source_kind: Literal["liepin"],
    status: SourceConnectionStatus,
    event_name: str,
    payload: dict[str, object],
) -> None:
    redacted_payload = redact_event_payload(payload)
    if not isinstance(redacted_payload, dict):
        redacted_payload = {"value": redacted_payload}
    conn.execute(
        """
        INSERT INTO connection_status_events (
            tenant_id, workspace_id, user_id, connection_id, source_kind,
            status, event_name, payload_redacted_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            connection_id,
            source_kind,
            status,
            event_name,
            json.dumps(redacted_payload, sort_keys=True, separators=(",", ":")),
            _now_iso(),
        ),
    )


def _json_list(values: list[str]) -> str:
    return json.dumps([_bounded_text(value, 500) or "" for value in values], ensure_ascii=False)


def _json_to_list(raw_value: str) -> list[str]:
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _bounded_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length]


def _safe_candidate_text(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    redacted = redact_text(str(value).strip())
    if redacted is None:
        return None
    return _bounded_text(redacted, max_length)


def _safe_list(value: object, max_items: int, max_length: int) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    result: list[str] = []
    for item in value[:max_items]:
        text = _safe_candidate_text(item, max_length)
        if text:
            result.append(text)
    return result


def _snapshot_payload(snapshot: object) -> Mapping[str, object]:
    payload = _attr(snapshot, "raw_payload")
    return payload if isinstance(payload, Mapping) else {}


def _liepin_card_display_fields(
    *,
    candidate: object,
    payload: Mapping[str, object],
    workbench_resume_id: str,
) -> tuple[str, str, str, str, str]:
    display_name = (
        _safe_candidate_text(payload.get("name"), 160)
        or _safe_candidate_text(payload.get("candidateName"), 160)
        or f"Candidate {workbench_resume_id[-8:]}"
    )
    title = (
        _safe_candidate_text(payload.get("title"), 240)
        or _safe_candidate_text(_attr(candidate, "expected_job_category"), 240)
        or "Liepin candidate card"
    )
    company = _safe_candidate_text(payload.get("company"), 240) or ""
    location = _safe_candidate_text(payload.get("location"), 160) or _safe_candidate_text(_attr(candidate, "now_location"), 160) or ""
    summary = (
        _safe_candidate_text(payload.get("summary"), 1000)
        or _safe_candidate_text(_attr(candidate, "search_text"), 1000)
        or ""
    )
    return display_name, title, company, location, summary


def _matched_terms(terms: list[str], text: str) -> list[str]:
    normalized = text.casefold()
    return _unique_list(term for term in terms if term.casefold() in normalized)


def _attr(value: object, name: str) -> object | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _mapping_get(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _first(value: object) -> object | None:
    if isinstance(value, list | tuple) and value:
        return value[0]
    return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _session_digest(session_token: str) -> str:
    return "sha256$" + hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _iso(_now())


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
