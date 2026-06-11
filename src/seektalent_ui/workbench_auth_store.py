from __future__ import annotations

import secrets
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Any

from seektalent_ui.workbench_store import (
    BootstrapAlreadyCompleteError,
    DEFAULT_TENANT_ID,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    LOGIN_ATTEMPT_EMAIL_MAX,
    LOGIN_ATTEMPT_IP_MAX,
    LOGIN_ATTEMPT_REASON_MAX,
    LOGIN_ATTEMPT_USER_AGENT_MAX,
    LOGIN_LOCKOUT_FAILURE_LIMIT,
    LOGIN_LOCKOUT_WINDOW_SECONDS,
    SESSION_TTL_HOURS,
    UserSessionTokens,
    WorkbenchSecurityAuditEvent,
    WorkbenchUser,
    WorkbenchWorkspace,
)
from seektalent_ui.workbench_store_helpers import (
    bounded_text as _bounded_text,
    iso as _iso,
    normalize_email as _normalize_email,
    now as _now,
    now_iso as _now_iso,
    parse_iso as _parse_iso,
    session_digest as _session_digest,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]
UserFromRow = Callable[[sqlite3.Row], WorkbenchUser]
AppendSecurityAuditEvent = Callable[..., Any]
SecurityAuditEventFromRow = Callable[[sqlite3.Row], WorkbenchSecurityAuditEvent]


class WorkbenchAuthStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        user_from_row: UserFromRow,
        append_security_audit_event: AppendSecurityAuditEvent,
        security_audit_event_from_row: SecurityAuditEventFromRow,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._user_from_row = user_from_row
        self._append_security_audit_event_conn = append_security_audit_event
        self._security_audit_event_from_row = security_audit_event_from_row

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
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=DEFAULT_WORKSPACE_ID,
                actor_user_id=user_id,
                actor_role="admin",
                target_type="user",
                target_id=user_id,
                action="bootstrap_admin_created",
                result="success",
                reason_code="first_admin",
                metadata={"email": email},
                created_at=now,
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
        return self._user_from_row(row), row["password_hash"], row["disabled_at"] is not None

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
            now = _now_iso()
            safe_email = _bounded_text(_normalize_email(email), LOGIN_ATTEMPT_EMAIL_MAX) or "unknown"
            safe_reason = _bounded_text(reason, LOGIN_ATTEMPT_REASON_MAX) or "unknown"
            safe_ip = _bounded_text(ip_address, LOGIN_ATTEMPT_IP_MAX)
            safe_user_agent = _bounded_text(user_agent, LOGIN_ATTEMPT_USER_AGENT_MAX)
            conn.execute(
                """
                INSERT INTO login_attempts (
                    attempt_id, email, success, reason, user_id, ip_address, user_agent, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"attempt_{uuid.uuid4().hex[:16]}",
                    safe_email,
                    int(success),
                    safe_reason,
                    user_id,
                    safe_ip,
                    safe_user_agent,
                    now,
                ),
            )
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=DEFAULT_WORKSPACE_ID,
                actor_user_id=user_id,
                actor_role=None,
                target_type="auth",
                target_id=user_id,
                action="login",
                result="success" if success else "failed",
                reason_code=safe_reason,
                request_ip=safe_ip,
                user_agent=safe_user_agent,
                metadata={"email": safe_email},
                created_at=now,
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
        return self._user_from_row(row)

    def revoke_user_session(self, *, session_digest: str | None, user: WorkbenchUser | None = None) -> None:
        if not session_digest:
            return
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE user_sessions
                SET revoked_at = ?
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (_now_iso(), session_digest),
            )
            if user is not None:
                self._append_security_audit_event_conn(
                    conn,
                    tenant_id=DEFAULT_TENANT_ID,
                    workspace_id=user.workspace_id,
                    actor_user_id=user.user_id,
                    actor_role=user.role,
                    target_type="session",
                    target_id="current_session",
                    action="logout",
                    result="success",
                    reason_code="user_requested",
                )

    def record_security_audit_event(
        self,
        *,
        actor_user_id: str | None,
        actor_role: str | None,
        workspace_id: str,
        target_type: str,
        target_id: str | None,
        action: str,
        result: str,
        reason_code: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self._initialize()
        with self._connect() as conn:
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                target_type=target_type,
                target_id=target_id,
                action=action,
                result=result,
                reason_code=reason_code,
                metadata=metadata,
            )

    def list_security_audit_events(self) -> list[WorkbenchSecurityAuditEvent]:
        self._initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM security_audit_events
                ORDER BY audit_id ASC
                """
            ).fetchall()
        return [self._security_audit_event_from_row(row) for row in rows]

    def list_security_audit_events_for_user(
        self,
        *,
        user: WorkbenchUser,
        limit: int = 200,
    ) -> list[WorkbenchSecurityAuditEvent]:
        self._initialize()
        safe_limit = min(max(limit, 1), 500)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM security_audit_events
                WHERE tenant_id = ? AND workspace_id = ?
                ORDER BY audit_id DESC
                LIMIT ?
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, safe_limit),
            ).fetchall()
        return [self._security_audit_event_from_row(row) for row in rows]

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
