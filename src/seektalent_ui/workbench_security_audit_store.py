from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager

from seektalent_ui.redaction import redact_event_payload, redact_text
from seektalent_ui.workbench_store_helpers import bounded_text as _bounded_text
from seektalent_ui.workbench_store_helpers import now_iso as _now_iso
from seektalent_ui.workbench_store_types import (
    DEFAULT_TENANT_ID,
    DEFAULT_WORKSPACE_ID,
    SECURITY_AUDIT_IP_MAX,
    SECURITY_AUDIT_USER_AGENT_MAX,
    WorkbenchSecurityAuditEvent,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]


class WorkbenchSecurityAuditStore:
    def __init__(self, *, connect: ConnectWorkbenchDb, initialize: InitializeWorkbenchStore) -> None:
        self._connect = connect
        self._initialize = initialize

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
            _append_security_audit_event_conn(
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
        return [_security_audit_event_from_row(row) for row in rows]

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
        return [_security_audit_event_from_row(row) for row in rows]


def _append_security_audit_event_conn(
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
    redacted_metadata = redact_event_payload(dict(metadata or {}))
    if not isinstance(redacted_metadata, dict):
        redacted_metadata = {"value": redacted_metadata}
    now = created_at or _now_iso()
    cursor = conn.execute(
        """
        INSERT INTO security_audit_events (
            tenant_id, workspace_id, actor_user_id, actor_role, request_ip, user_agent,
            target_type, target_id, action, result, reason_code, metadata_redacted_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _bounded_text(tenant_id, 64) or DEFAULT_TENANT_ID,
            _bounded_text(workspace_id, 128) or DEFAULT_WORKSPACE_ID,
            _bounded_text(redact_text(actor_user_id), 128),
            _bounded_text(redact_text(actor_role), 64),
            _bounded_text(redact_text(request_ip), SECURITY_AUDIT_IP_MAX),
            _bounded_text(redact_text(user_agent), SECURITY_AUDIT_USER_AGENT_MAX),
            _bounded_text(redact_text(target_type), 128) or "unknown",
            _bounded_text(redact_text(target_id), 256),
            _bounded_text(redact_text(action), 128) or "unknown",
            _bounded_text(redact_text(result), 64) or "unknown",
            _bounded_text(redact_text(reason_code), 128),
            json.dumps(redacted_metadata, sort_keys=True, separators=(",", ":")),
            now,
        ),
    )
    return WorkbenchSecurityAuditEvent(
        audit_id=int(cursor.lastrowid or 0),
        actor_user_id=_bounded_text(redact_text(actor_user_id), 128),
        actor_role=_bounded_text(redact_text(actor_role), 64),
        workspace_id=_bounded_text(workspace_id, 128) or DEFAULT_WORKSPACE_ID,
        request_ip=_bounded_text(redact_text(request_ip), SECURITY_AUDIT_IP_MAX),
        user_agent=_bounded_text(redact_text(user_agent), SECURITY_AUDIT_USER_AGENT_MAX),
        target_type=_bounded_text(redact_text(target_type), 128) or "unknown",
        target_id=_bounded_text(redact_text(target_id), 256),
        action=_bounded_text(redact_text(action), 128) or "unknown",
        result=_bounded_text(redact_text(result), 64) or "unknown",
        reason_code=_bounded_text(redact_text(reason_code), 128),
        metadata=redacted_metadata,
        created_at=now,
    )


def _security_audit_event_from_row(row: sqlite3.Row) -> WorkbenchSecurityAuditEvent:
    metadata = json.loads(row["metadata_redacted_json"])
    if not isinstance(metadata, dict):
        metadata = {"value": metadata}
    return WorkbenchSecurityAuditEvent(
        audit_id=row["audit_id"],
        actor_user_id=row["actor_user_id"],
        actor_role=row["actor_role"],
        workspace_id=row["workspace_id"],
        request_ip=row["request_ip"],
        user_agent=row["user_agent"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        action=row["action"],
        result=row["result"],
        reason_code=row["reason_code"],
        metadata=metadata,
        created_at=row["created_at"],
    )
