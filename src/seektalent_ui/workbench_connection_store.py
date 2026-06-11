from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Literal, Protocol

from seektalent_ui.redaction import redact_event_payload
from seektalent_ui.workbench_store_helpers import now_iso as _now_iso
from seektalent_ui.workbench_store_types import (
    DEFAULT_TENANT_ID,
    SourceConnectionStatus,
    WorkbenchEvent,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchSecurityAuditEvent,
    WorkbenchSourceConnection,
    WorkbenchSourceRunJobContext,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]


class AppendWorkbenchEvent(Protocol):
    def __call__(
        self,
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
        schema_version: str = "workbench_event_v1",
        idempotency_key: str | None = None,
        occurred_at: str | None = None,
    ) -> WorkbenchEvent:
        raise NotImplementedError


class AppendSecurityAuditEvent(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str | None,
        actor_role: str | None,
        target_type: str,
        target_id: str | None,
        action: str,
        result: str,
        reason_code: str | None = None,
        request_ip: str | None = None,
        user_agent: str | None = None,
        metadata: Mapping[str, object] | None = None,
        created_at: str | None = None,
    ) -> WorkbenchSecurityAuditEvent:
        raise NotImplementedError


class ConnectedLiepinConnection(Protocol):
    def __call__(self, conn: sqlite3.Connection, *, user: WorkbenchUser) -> sqlite3.Row | None:
        raise NotImplementedError


class WorkbenchConnectionStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        append_security_audit_event: AppendSecurityAuditEvent,
        append_workbench_event: AppendWorkbenchEvent,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._append_security_audit_event_conn = append_security_audit_event
        self._append_workbench_event_conn = append_workbench_event

    @property
    def connected_liepin_connection_conn(self) -> ConnectedLiepinConnection:
        return _connected_liepin_connection_conn

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
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="source_connection",
                target_id=connection_id,
                action="source_connection_created",
                result="success",
                reason_code="liepin_connection_requested",
                metadata={"sourceKind": "liepin", "status": "login_required"},
                created_at=now,
            )
            self._append_workbench_event_conn(
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
        compliance_gate_ref: str | None = None,
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
                    compliance_gate_ref = COALESCE(?, compliance_gate_ref),
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (warning_code, warning_message, provider_account_hash, compliance_gate_ref, now, connection_id),
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
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="source_connection",
                target_id=connection_id,
                action="liepin_login_started",
                result="success",
                reason_code=warning_code,
                metadata={"sourceKind": "liepin", "status": "login_in_progress", "warningCode": warning_code},
                created_at=now,
            )
            self._append_workbench_event_conn(
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
        compliance_gate_ref: str | None = None,
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
                    compliance_gate_ref = COALESCE(?, compliance_gate_ref),
                    connected_at = ?,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (provider_account_hash, compliance_gate_ref, now, now, connection_id),
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
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="source_connection",
                target_id=connection_id,
                action="liepin_login_completed",
                result="success",
                reason_code="verified",
                metadata={"sourceKind": "liepin", "status": "connected"},
                created_at=now,
            )
            self._append_workbench_event_conn(
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

    def mark_liepin_connection_login_required(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        warning_code: str,
        warning_message: str,
        session_id: str | None = None,
        source_run_id: str | None = None,
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
            if session_id is not None and source_run_id is not None:
                source_run_row = conn.execute(
                    """
                    SELECT sr.*
                    FROM source_runs AS sr
                    JOIN sessions AS s ON s.session_id = sr.session_id
                    WHERE sr.source_run_id = ?
                      AND sr.session_id = ?
                      AND sr.source_kind = 'liepin'
                      AND sr.workspace_id = ?
                      AND sr.user_id = ?
                      AND s.user_id = ?
                    """,
                    (source_run_id, session_id, user.workspace_id, user.user_id, user.user_id),
                ).fetchone()
                active_runtime_job = conn.execute(
                    """
                    SELECT 1
                    FROM runtime_sourcing_jobs
                    WHERE session_id = ?
                      AND status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                if (
                    source_run_row is None
                    or source_run_row["status"] in {"running", "completed", "failed"}
                    or (source_run_row["status"] == "queued" and active_runtime_job is not None)
                    or not (
                        source_run_row["status"] in {"blocked", "queued"}
                        or source_run_row["auth_state"] == "login_required"
                    )
                ):
                    return None
            conn.execute(
                """
                UPDATE source_connections
                SET status = 'login_required',
                    warning_code = ?,
                    warning_message = ?,
                    connected_at = NULL,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (warning_code, warning_message, now, connection_id),
            )
            _append_connection_status_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                connection_id=connection_id,
                source_kind="liepin",
                status="login_required",
                event_name="source_connection_status_changed",
                payload={
                    "connectionId": connection_id,
                    "sourceKind": "liepin",
                    "status": "login_required",
                    "warningCode": warning_code,
                },
            )
            self._append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind="liepin",
                event_name="source_connection_status_changed",
                payload={
                    "connectionId": connection_id,
                    "sourceKind": "liepin",
                    "status": "login_required",
                    "warningCode": warning_code,
                },
            )
            updated = conn.execute("SELECT * FROM source_connections WHERE connection_id = ?", (connection_id,)).fetchone()
        return _source_connection_from_row(updated)

    def mark_liepin_connection_connected_for_source_run(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        session_id: str,
        source_run_id: str,
        provider_account_hash: str,
        compliance_gate_ref: str | None = None,
    ) -> WorkbenchSourceConnection | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection_row = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND connection_id = ? AND source_kind = 'liepin'
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, connection_id),
            ).fetchone()
            if connection_row is None:
                return None
            source_run_row = conn.execute(
                """
                SELECT sr.*
                FROM source_runs AS sr
                JOIN sessions AS s ON s.session_id = sr.session_id
                WHERE sr.source_run_id = ?
                  AND sr.session_id = ?
                  AND sr.source_kind = 'liepin'
                  AND sr.workspace_id = ?
                  AND sr.user_id = ?
                  AND s.user_id = ?
                """,
                (source_run_id, session_id, user.workspace_id, user.user_id, user.user_id),
            ).fetchone()
            if source_run_row is None:
                return None
            conn.execute(
                """
                UPDATE source_connections
                SET status = 'connected',
                    warning_code = NULL,
                    warning_message = NULL,
                    provider_account_hash = ?,
                    compliance_gate_ref = COALESCE(?, compliance_gate_ref),
                    connected_at = ?,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (provider_account_hash, compliance_gate_ref, now, now, connection_id),
            )
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'queued',
                    auth_state = 'not_required',
                    warning_code = NULL,
                    warning_message = NULL
                WHERE source_run_id = ?
                  AND session_id = ?
                  AND source_kind = 'liepin'
                  AND status = 'blocked'
                """,
                (source_run_id, session_id),
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
            self._append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind="liepin",
                event_name="source_connection_status_changed",
                payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "connected"},
            )
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="source_connection",
                target_id=connection_id,
                action="liepin_login_completed",
                result="success",
                reason_code="verified",
                metadata={"sourceKind": "liepin", "status": "connected"},
                created_at=now,
            )
            updated = conn.execute("SELECT * FROM source_connections WHERE connection_id = ?", (connection_id,)).fetchone()
        return _source_connection_from_row(updated)

    def mark_liepin_connection_connected_without_source_runs(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        provider_account_hash: str | None,
        compliance_gate_ref: str | None = None,
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
                    compliance_gate_ref = COALESCE(?, compliance_gate_ref),
                    connected_at = ?,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (provider_account_hash, compliance_gate_ref, now, now, connection_id),
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
            self._append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="source_connection",
                target_id=connection_id,
                action="liepin_login_completed",
                result="success",
                reason_code="verified",
                metadata={"sourceKind": "liepin", "status": "connected"},
                created_at=now,
            )
            self._append_workbench_event_conn(
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
        context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext,
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


def _connected_liepin_connection_conn(conn: sqlite3.Connection, *, user: WorkbenchUser) -> sqlite3.Row | None:
    row = _liepin_connection_for_user_conn(conn, user=user)
    if row is None or row["status"] != "connected" or not row["provider_account_hash"]:
        return None
    return row


def _connected_liepin_connection_for_owner_conn(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    user_id: str,
) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT *
        FROM source_connections
        WHERE tenant_id = ?
          AND workspace_id = ?
          AND user_id = ?
          AND source_kind = 'liepin'
          AND status = 'connected'
          AND provider_account_hash IS NOT NULL
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (DEFAULT_TENANT_ID, workspace_id, user_id),
    ).fetchone()
    return row


def _liepin_connection_for_user_conn(conn: sqlite3.Connection, *, user: WorkbenchUser) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM source_connections
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND source_kind = 'liepin'
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id),
    ).fetchone()


def _source_connection_from_row(row: sqlite3.Row) -> WorkbenchSourceConnection:
    return WorkbenchSourceConnection(
        connection_id=row["connection_id"],
        source_kind=row["source_kind"],
        status=row["status"],
        warning_code=row["warning_code"],
        warning_message=row["warning_message"],
        provider_account_hash=row["provider_account_hash"],
        compliance_gate_ref=row["compliance_gate_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        connected_at=row["connected_at"],
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
