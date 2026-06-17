from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from seektalent.sqlite_migrations import (
    backup_sqlite_before_migration,
    has_user_tables,
    require_supported_version,
    run_sqlite_integrity_checks,
)
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.models import LiepinConnectionRow, LiepinEventRow, LiepinRunRow, SubjectType
from seektalent.providers.liepin.security import hmac_provider_account_hash


UNSAFE_PAYLOAD_KEYS = {
    "auth",
    "authHeader",
    "auth_header",
    "authHeaders",
    "auth_headers",
    "authorization",
    "authUrl",
    "auth_url",
    "accountSubject",
    "account_subject",
    "browserContext",
    "browser_context",
    "cdpUrl",
    "cdp_url",
    "cookie",
    "rawProviderPayload",
    "raw_provider_payload",
    "rawPayload",
    "raw_payload",
    "providerPayload",
    "provider_payload",
    "providerAccountSubject",
    "provider_account_subject",
    "observedProviderAccountSubject",
    "observed_provider_account_subject",
    "remoteDebuggingPort",
    "remote_debugging_port",
    "debugWebsocketUrl",
    "debug_websocket_url",
    "playwright",
    "wsEndpoint",
    "ws_endpoint",
    "workerBaseUrl",
    "worker_base_url",
    "cookies",
    "localStorage",
    "local_storage",
    "sessionStorage",
    "session_storage",
    "storageState",
    "storage_state",
    "workerUrl",
    "worker_url",
    "token",
    "streamToken",
    "stream_token",
}


DetailAttemptState = Literal[
    "approved_not_started",
    "started",
    "provider_page_loaded",
    "detail_payload_seen",
    "completed",
    "blocked_by_risk_control",
    "failed_before_consumption",
    "failed_after_possible_consumption",
    "unknown",
]
DetailConsumptionState = Literal["not_consumed", "consumed", "possibly_consumed", "unknown"]

DETAIL_ATTEMPT_STATES = {
    "approved_not_started",
    "started",
    "provider_page_loaded",
    "detail_payload_seen",
    "completed",
    "blocked_by_risk_control",
    "failed_before_consumption",
    "failed_after_possible_consumption",
    "unknown",
}
DETAIL_CONSUMPTION_STATES = {"not_consumed", "consumed", "possibly_consumed", "unknown"}
LIEPIN_SCHEMA_VERSION = 1
DETAIL_ALLOWED_TRANSITIONS = {
    "approved_not_started": {"started", "failed_before_consumption", "unknown"},
    "started": {
        "provider_page_loaded",
        "detail_payload_seen",
        "completed",
        "blocked_by_risk_control",
        "failed_before_consumption",
        "failed_after_possible_consumption",
        "unknown",
    },
    "provider_page_loaded": {
        "detail_payload_seen",
        "blocked_by_risk_control",
        "failed_after_possible_consumption",
        "unknown",
    },
    "detail_payload_seen": {"completed", "failed_after_possible_consumption", "unknown"},
    "completed": set(),
    "blocked_by_risk_control": set(),
    "failed_before_consumption": set(),
    "failed_after_possible_consumption": set(),
    "unknown": set(),
}


@dataclass(frozen=True)
class LiepinDetailAttemptRow:
    attempt_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    provider_account_hash: str
    candidate_provider_id: str
    idempotency_key: str
    state: str
    consumption_state: str
    started_at: str | None
    completed_at: str | None
    worker_command_id: str | None
    raw_evidence_ref: str | None
    budget_date: str
    provider_day_key: str
    timezone: str
    created_at: str
    updated_at: str


class LiepinStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_compliance_gate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        gate: ComplianceGate,
        purpose: str,
    ) -> str:
        gate_ref = f"gate_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO liepin_compliance_gates (
                    gate_ref, tenant_id, workspace_id, actor_id, provider_account_hash, status,
                    candidate_personal_info_processing_basis, personal_information_processor,
                    operator_audit_owner, account_holder_authorized, human_initiated_recruiting,
                    allowed_purposes_json, retention_policy, deletion_sla_days, deletion_path,
                    raw_payload_access_scope, raw_detail_retention_allowed_after_debug,
                    fixture_export_allowed, policy_ref, requested_purpose, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gate_ref,
                    tenant_id,
                    workspace_id,
                    actor_id,
                    gate.provider_account_hash,
                    gate.status,
                    gate.candidate_personal_info_processing_basis,
                    gate.personal_information_processor,
                    gate.operator_audit_owner,
                    int(gate.account_holder_authorized),
                    int(gate.human_initiated_recruiting),
                    json.dumps(gate.allowed_purposes),
                    gate.retention_policy,
                    gate.deletion_sla_days,
                    gate.deletion_path,
                    gate.raw_payload_access_scope,
                    int(gate.raw_detail_retention_allowed_after_debug),
                    int(gate.fixture_export_allowed),
                    gate.policy_ref,
                    purpose,
                    _now_iso(),
                ),
            )
        return gate_ref

    def get_compliance_gate(
        self,
        *,
        gate_ref: str,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
    ) -> ComplianceGate | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM liepin_compliance_gates
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (gate_ref, tenant_id, workspace_id, actor_id),
            ).fetchone()
        return _gate_from_row(row) if row is not None else None

    def create_connection(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        compliance_gate_ref: str,
        connection_id: str | None = None,
    ) -> str:
        connection_id = connection_id or f"conn_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO liepin_connections (
                    connection_id, tenant_id, workspace_id, actor_id, compliance_gate_ref,
                    status, provider_account_hash, observed_provider_account_subject, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    tenant_id,
                    workspace_id,
                    actor_id,
                    compliance_gate_ref,
                    "pending_login",
                    None,
                    None,
                    _now_iso(),
                ),
            )
        return connection_id

    def get_connection(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
    ) -> LiepinConnectionRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT connection_id, tenant_id, workspace_id, actor_id, compliance_gate_ref, status,
                       provider_account_hash
                FROM liepin_connections
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (tenant_id, workspace_id, actor_id, connection_id),
            ).fetchone()
        if row is None:
            return None
        return LiepinConnectionRow(**dict(row))

    def record_connection_account_subject(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        observed_provider_account_subject: str,
    ) -> bool:
        if not observed_provider_account_subject:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE liepin_connections
                SET observed_provider_account_subject = ?, status = 'login_ready'
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                  AND status = 'pending_login' AND provider_account_hash IS NULL
                """,
                (observed_provider_account_subject, tenant_id, workspace_id, actor_id, connection_id),
            )
        return cursor.rowcount == 1

    def bind_connection_account(
        self,
        *,
        gate_ref: str,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        secret: str,
    ) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT compliance_gate_ref, status, provider_account_hash, observed_provider_account_subject
                FROM liepin_connections
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (tenant_id, workspace_id, actor_id, connection_id),
            ).fetchone()
            if row is None:
                return None
            if row["compliance_gate_ref"] != gate_ref:
                return None
            if row["status"] == "connected" and row["provider_account_hash"] is not None:
                account_hash = row["provider_account_hash"]
            elif row["status"] == "login_ready" and row["observed_provider_account_subject"]:
                account_hash = hmac_provider_account_hash(secret, row["observed_provider_account_subject"])
            else:
                return None
            gate = conn.execute(
                """
                SELECT provider_account_hash, status
                FROM liepin_compliance_gates
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (gate_ref, tenant_id, workspace_id, actor_id),
            ).fetchone()
            if gate is None:
                return None
            if gate["status"] == "pending_account_binding":
                pass
            elif gate["status"] == "approved" and gate["provider_account_hash"] == account_hash:
                pass
            else:
                return None
            if gate["provider_account_hash"] not in {None, account_hash}:
                return None
            conn.execute(
                """
                UPDATE liepin_compliance_gates
                SET provider_account_hash = ?, status = 'approved'
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (account_hash, gate_ref, tenant_id, workspace_id, actor_id),
            )
            conn.execute(
                """
                UPDATE liepin_connections
                SET provider_account_hash = ?, status = 'connected'
                WHERE connection_id = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (account_hash, connection_id, tenant_id, workspace_id, actor_id),
            )
        return account_hash

    def approve_connection_account_hash(
        self,
        *,
        gate_ref: str,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        provider_account_hash: str,
    ) -> bool:
        if not provider_account_hash:
            return False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection = conn.execute(
                "SELECT compliance_gate_ref FROM liepin_connections "
                "WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ? "
                "AND status = 'connected' AND provider_account_hash = ?",
                (tenant_id, workspace_id, actor_id, connection_id, provider_account_hash),
            ).fetchone()
            if connection is None or connection["compliance_gate_ref"] != gate_ref:
                return False
            gate = conn.execute(
                """
                SELECT status
                FROM liepin_compliance_gates
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (gate_ref, tenant_id, workspace_id, actor_id),
            ).fetchone()
            if gate is None or gate["status"] in {"denied", "expired"}:
                return False
            conn.execute(
                """
                UPDATE liepin_compliance_gates
                SET provider_account_hash = ?, status = 'approved'
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (provider_account_hash, gate_ref, tenant_id, workspace_id, actor_id),
            )
            conn.execute(
                """
                UPDATE liepin_connections
                SET provider_account_hash = ?, status = 'connected'
                WHERE connection_id = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (provider_account_hash, connection_id, tenant_id, workspace_id, actor_id),
            )
        return True

    def record_session_metadata(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        provider_account_hash: str,
        session_store_key_id: str,
        encrypted_state_sha256: str,
    ) -> dict[str, object] | None:
        if not provider_account_hash or not session_store_key_id or not encrypted_state_sha256:
            return None
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE liepin_connections
                SET provider_account_hash = ?,
                    status = 'connected',
                    session_store_key_id = ?,
                    encrypted_state_sha256 = ?,
                    session_updated_at = ?,
                    revoked_at = NULL
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (
                    provider_account_hash,
                    session_store_key_id,
                    encrypted_state_sha256,
                    _now_iso(),
                    tenant_id,
                    workspace_id,
                    actor_id,
                    connection_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_session_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            connection_id=connection_id,
        )

    def get_session_metadata(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
    ) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT connection_id, tenant_id, workspace_id, actor_id, status,
                       provider_account_hash, session_store_key_id, encrypted_state_sha256,
                       session_updated_at, revoked_at
                FROM liepin_connections
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (tenant_id, workspace_id, actor_id, connection_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def revoke_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        reason: str,
    ) -> bool:
        safe_reason = _safe_revoke_reason(reason)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            cursor = conn.execute(
                """
                UPDATE liepin_connections
                SET status = 'revoked',
                    provider_account_hash = NULL,
                    observed_provider_account_subject = NULL,
                    session_store_key_id = NULL,
                    encrypted_state_sha256 = NULL,
                    session_updated_at = NULL,
                    revoked_at = ?
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (now, tenant_id, workspace_id, actor_id, connection_id),
            )
            if cursor.rowcount != 1:
                return False
            _append_event(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                subject_type="connection",
                subject_id=connection_id,
                event_name="session_revoked",
                payload={"connectionId": connection_id, "reason": safe_reason},
                redaction_state="domain",
            )
        return True

    def create_run(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        compliance_gate_ref: str,
    ) -> str:
        run_id = f"liepin_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection = conn.execute(
                """
                SELECT c.provider_account_hash AS connection_provider_account_hash,
                       g.*
                FROM liepin_connections AS c
                JOIN liepin_compliance_gates AS g
                  ON g.gate_ref = c.compliance_gate_ref
                 AND g.tenant_id = c.tenant_id
                 AND g.workspace_id = c.workspace_id
                 AND g.actor_id = c.actor_id
                WHERE c.tenant_id = ? AND c.workspace_id = ? AND c.actor_id = ?
                  AND c.connection_id = ? AND c.compliance_gate_ref = ?
                  AND c.status = 'connected'
                """,
                (tenant_id, workspace_id, actor_id, connection_id, compliance_gate_ref),
            ).fetchone()
            if connection is None:
                raise ValueError("Liepin connection does not belong to compliance gate.")
            if (
                connection["connection_provider_account_hash"] is None
                or connection["connection_provider_account_hash"] != connection["provider_account_hash"]
            ):
                raise ValueError("Liepin connection is not bound to the compliance gate account.")
            gate = _gate_from_row(connection)
            if not gate.allows_live_search(
                provider_account_hash=connection["connection_provider_account_hash"],
                purpose="search",
            ):
                raise ValueError("Liepin compliance gate does not allow live search.")
            conn.execute(
                """
                INSERT INTO liepin_runs (
                    run_id, tenant_id, workspace_id, actor_id, connection_id, compliance_gate_ref, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, tenant_id, workspace_id, actor_id, connection_id, compliance_gate_ref, "queued", _now_iso()),
            )
        return run_id

    def get_run(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        run_id: str,
    ) -> LiepinRunRow | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, tenant_id, workspace_id, actor_id, connection_id, compliance_gate_ref, status
                FROM liepin_runs
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND run_id = ?
                """,
                (tenant_id, workspace_id, actor_id, run_id),
            ).fetchone()
        if row is None:
            return None
        return LiepinRunRow(**dict(row))

    def reserve_detail_attempt(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        provider_account_hash: str,
        candidate_provider_id: str,
        budget_date: str,
        provider_day_key: str,
        timezone: str,
        idempotency_key: str,
    ) -> LiepinDetailAttemptRow:
        if not all(
            [
                provider_account_hash,
                candidate_provider_id,
                budget_date,
                provider_day_key,
                timezone,
                idempotency_key,
            ]
        ):
            raise ValueError("Liepin detail reservation requires account, candidate, day, timezone, and key.")
        attempt_id = f"detail_{uuid.uuid4().hex[:16]}"
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = _fetch_detail_attempt_by_key(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                provider_account_hash=provider_account_hash,
                budget_date=budget_date,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                _validate_detail_reservation_replay(
                    existing,
                    candidate_provider_id=candidate_provider_id,
                    provider_day_key=provider_day_key,
                    timezone=timezone,
                )
                return _detail_attempt_from_row(existing)
            conn.execute(
                """
                INSERT INTO liepin_detail_attempts (
                    attempt_id, tenant_id, workspace_id, actor_id, provider_account_hash,
                    candidate_provider_id, idempotency_key, state, consumption_state,
                    budget_date, provider_day_key, timezone, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    tenant_id,
                    workspace_id,
                    actor_id,
                    provider_account_hash,
                    candidate_provider_id,
                    idempotency_key,
                    "approved_not_started",
                    "not_consumed",
                    budget_date,
                    provider_day_key,
                    timezone,
                    now,
                    now,
                ),
            )
            row = _fetch_detail_attempt_by_key(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                provider_account_hash=provider_account_hash,
                budget_date=budget_date,
                idempotency_key=idempotency_key,
            )
        if row is None:
            raise RuntimeError("Liepin detail reservation was not persisted.")
        return _detail_attempt_from_row(row)

    def transition_detail_attempt(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        attempt_id: str,
        state: DetailAttemptState,
        consumption_state: DetailConsumptionState,
        worker_command_id: str | None = None,
        raw_evidence_ref: str | None = None,
    ) -> LiepinDetailAttemptRow:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _fetch_detail_attempt(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                attempt_id=attempt_id,
            )
            if row is None:
                raise ValueError("Liepin detail attempt not found.")
            return _update_detail_attempt(
                conn,
                row=row,
                state=state,
                consumption_state=consumption_state,
                worker_command_id=worker_command_id,
                raw_evidence_ref=raw_evidence_ref,
            )

    def apply_detail_worker_response(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        attempt_id: str,
        worker_response_id: str,
        state: DetailAttemptState,
        consumption_state: DetailConsumptionState,
        worker_command_id: str | None = None,
        raw_evidence_ref: str | None = None,
    ) -> LiepinDetailAttemptRow:
        if not worker_response_id:
            raise ValueError("Liepin detail worker response requires a response id.")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                """
                SELECT attempt_id
                FROM liepin_detail_worker_responses
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND worker_response_id = ?
                """,
                (tenant_id, workspace_id, actor_id, worker_response_id),
            ).fetchone()
            if duplicate is not None:
                if duplicate["attempt_id"] != attempt_id:
                    raise ValueError("Liepin detail worker response attempt mismatch.")
                row = _fetch_detail_attempt(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    attempt_id=duplicate["attempt_id"],
                )
                if row is None:
                    raise RuntimeError("Liepin detail worker response references a missing attempt.")
                return _detail_attempt_from_row(row)
            row = _fetch_detail_attempt(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                attempt_id=attempt_id,
            )
            if row is None:
                raise ValueError("Liepin detail attempt not found.")
            updated = _update_detail_attempt(
                conn,
                row=row,
                state=state,
                consumption_state=consumption_state,
                worker_command_id=worker_command_id,
                raw_evidence_ref=raw_evidence_ref,
            )
            conn.execute(
                """
                INSERT INTO liepin_detail_worker_responses (
                    tenant_id, workspace_id, actor_id, worker_response_id, attempt_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tenant_id, workspace_id, actor_id, worker_response_id, attempt_id, _now_iso()),
            )
        return updated

    def count_detail_budget_consumed(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        provider_account_hash: str,
        provider_day_key: str,
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS consumed_count
                FROM liepin_detail_attempts
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ?
                  AND provider_account_hash = ? AND provider_day_key = ?
                  AND consumption_state IN ('consumed', 'possibly_consumed', 'unknown')
                """,
                (tenant_id, workspace_id, actor_id, provider_account_hash, provider_day_key),
            ).fetchone()
        return int(row["consumed_count"])

    def delete_terminal_detail_attempts(
        self,
        *,
        updated_before: str,
        budget_date_before: str,
        batch_size: int = 500,
        dry_run: bool = False,
    ) -> int:
        safe_batch_size = max(1, min(batch_size, 5000))
        terminal_states = (
            "completed",
            "blocked_by_risk_control",
            "failed_before_consumption",
            "failed_after_possible_consumption",
            "unknown",
        )
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT attempt_id
                FROM liepin_detail_attempts
                WHERE updated_at < ?
                  AND budget_date < ?
                  AND state IN ({", ".join("?" for _ in terminal_states)})
                ORDER BY updated_at ASC, attempt_id ASC
                LIMIT ?
                """,
                (updated_before, budget_date_before, *terminal_states, safe_batch_size),
            ).fetchall()
            attempt_ids = [str(row["attempt_id"]) for row in rows]
            if dry_run or not attempt_ids:
                return len(attempt_ids)
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "DELETE FROM liepin_detail_worker_responses WHERE attempt_id = ?",
                [(attempt_id,) for attempt_id in attempt_ids],
            )
            conn.executemany(
                "DELETE FROM liepin_detail_attempts WHERE attempt_id = ?",
                [(attempt_id,) for attempt_id in attempt_ids],
            )
            conn.commit()
        return len(attempt_ids)

    def append_event(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        subject_type: SubjectType,
        subject_id: str,
        event_name: str,
        payload: dict[str, object],
        redaction_state: str = "domain",
    ) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return _append_event(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                subject_type=subject_type,
                subject_id=subject_id,
                event_name=event_name,
                payload=payload,
                redaction_state=redaction_state,
            )

    def iter_events_after(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        subject_type: SubjectType,
        subject_id: str,
        after_sequence: int,
        limit: int = 100,
    ) -> list[LiepinEventRow]:
        limit = max(1, min(limit, 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tenant_id, workspace_id, actor_id, subject_type, subject_id, sequence,
                       event_name, payload_json, redaction_state, created_at
                FROM liepin_events
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ?
                  AND subject_type = ? AND subject_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (tenant_id, workspace_id, actor_id, subject_type, subject_id, after_sequence, limit),
            ).fetchall()
        return [
            LiepinEventRow(
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                actor_id=row["actor_id"],
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                sequence=row["sequence"],
                event_name=row["event_name"],
                payload=json.loads(row["payload_json"]),
                redaction_state=row["redaction_state"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connect() as conn:
            version = require_supported_version(
                conn,
                supported_version=LIEPIN_SCHEMA_VERSION,
                store_name="liepin",
            )
            if version == 0 and has_user_tables(conn):
                backup_sqlite_before_migration(
                    self.db_path,
                    backup_root=self.db_path.parent / "migration_backups",
                    store_name="liepin",
                    now=_liepin_migration_now(),
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS liepin_compliance_gates (
                    gate_ref TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    provider_account_hash TEXT,
                    status TEXT NOT NULL CHECK(status IN ('pending_account_binding', 'approved', 'denied', 'expired')),
                    candidate_personal_info_processing_basis TEXT NOT NULL,
                    personal_information_processor TEXT NOT NULL,
                    operator_audit_owner TEXT NOT NULL,
                    account_holder_authorized INTEGER NOT NULL CHECK(account_holder_authorized IN (0, 1)),
                    human_initiated_recruiting INTEGER NOT NULL CHECK(human_initiated_recruiting IN (0, 1)),
                    allowed_purposes_json TEXT NOT NULL CHECK(json_valid(allowed_purposes_json)),
                    retention_policy TEXT NOT NULL CHECK(
                        retention_policy IN ('run_debug_short', 'workspace_recruiting_record', 'forbidden_persist')
                    ),
                    deletion_sla_days INTEGER NOT NULL,
                    deletion_path TEXT NOT NULL,
                    raw_payload_access_scope TEXT NOT NULL CHECK(
                        raw_payload_access_scope IN ('run_only', 'workspace', 'admin_only')
                    ),
                    raw_detail_retention_allowed_after_debug INTEGER NOT NULL
                        CHECK(raw_detail_retention_allowed_after_debug IN (0, 1)),
                    fixture_export_allowed INTEGER NOT NULL CHECK(fixture_export_allowed IN (0, 1)),
                    policy_ref TEXT NOT NULL,
                    requested_purpose TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_liepin_gates_scope
                ON liepin_compliance_gates(tenant_id, workspace_id, actor_id, gate_ref);

                CREATE TABLE IF NOT EXISTS liepin_connections (
                    connection_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    compliance_gate_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_account_hash TEXT,
                    observed_provider_account_subject TEXT,
                    session_store_key_id TEXT,
                    encrypted_state_sha256 TEXT,
                    session_updated_at TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_liepin_connections_scope
                ON liepin_connections(tenant_id, workspace_id, actor_id, connection_id);

                CREATE TABLE IF NOT EXISTS liepin_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    connection_id TEXT NOT NULL,
                    compliance_gate_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_liepin_runs_scope
                ON liepin_runs(tenant_id, workspace_id, actor_id, run_id);

                CREATE TABLE IF NOT EXISTS liepin_events (
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    subject_type TEXT NOT NULL CHECK(subject_type IN ('connection', 'run')),
                    subject_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
                    redaction_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, workspace_id, subject_type, subject_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_liepin_events_scope_subject
                ON liepin_events(tenant_id, workspace_id, actor_id, subject_type, subject_id, sequence);

                CREATE INDEX IF NOT EXISTS idx_liepin_events_cleanup
                ON liepin_events(created_at);

                CREATE TABLE IF NOT EXISTS liepin_detail_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    provider_account_hash TEXT NOT NULL,
                    candidate_provider_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(
                        state IN (
                            'approved_not_started',
                            'started',
                            'provider_page_loaded',
                            'detail_payload_seen',
                            'completed',
                            'blocked_by_risk_control',
                            'failed_before_consumption',
                            'failed_after_possible_consumption',
                            'unknown'
                        )
                    ),
                    consumption_state TEXT NOT NULL CHECK(
                        consumption_state IN ('not_consumed', 'consumed', 'possibly_consumed', 'unknown')
                    ),
                    started_at TEXT,
                    completed_at TEXT,
                    worker_command_id TEXT,
                    raw_evidence_ref TEXT,
                    budget_date TEXT NOT NULL,
                    provider_day_key TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (
                        tenant_id,
                        workspace_id,
                        actor_id,
                        provider_account_hash,
                        budget_date,
                        idempotency_key
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_liepin_detail_attempts_budget
                ON liepin_detail_attempts(
                    tenant_id,
                    workspace_id,
                    actor_id,
                    provider_account_hash,
                    provider_day_key,
                    consumption_state
                );

                CREATE TABLE IF NOT EXISTS liepin_detail_worker_responses (
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    worker_response_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, workspace_id, actor_id, worker_response_id)
                );
                """
            )
            if version == 0:
                _ensure_columns(
                    conn,
                    table_name="liepin_connections",
                    columns={
                        "provider_account_hash": "TEXT",
                        "observed_provider_account_subject": "TEXT",
                        "session_store_key_id": "TEXT",
                        "encrypted_state_sha256": "TEXT",
                        "session_updated_at": "TEXT",
                        "revoked_at": "TEXT",
                    },
                )
                _ensure_columns(
                    conn,
                    table_name="liepin_compliance_gates",
                    columns={"created_at": "TEXT NOT NULL DEFAULT ''"},
                )
                _set_user_version(conn, LIEPIN_SCHEMA_VERSION)
            run_sqlite_integrity_checks(conn, store_name="liepin", foreign_keys=False)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()


def _gate_from_row(row: sqlite3.Row) -> ComplianceGate:
    return ComplianceGate(
        tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"],
        actor_id=row["actor_id"],
        provider_account_hash=row["provider_account_hash"],
        status=row["status"],
        candidate_personal_info_processing_basis=row["candidate_personal_info_processing_basis"],
        personal_information_processor=row["personal_information_processor"],
        operator_audit_owner=row["operator_audit_owner"],
        account_holder_authorized=bool(row["account_holder_authorized"]),
        human_initiated_recruiting=bool(row["human_initiated_recruiting"]),
        allowed_purposes=json.loads(row["allowed_purposes_json"]),
        retention_policy=row["retention_policy"],
        deletion_sla_days=row["deletion_sla_days"],
        deletion_path=row["deletion_path"],
        raw_payload_access_scope=row["raw_payload_access_scope"],
        raw_detail_retention_allowed_after_debug=bool(row["raw_detail_retention_allowed_after_debug"]),
        fixture_export_allowed=bool(row["fixture_export_allowed"]),
        policy_ref=row["policy_ref"],
    )


def _fetch_detail_attempt(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    attempt_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM liepin_detail_attempts
        WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND attempt_id = ?
        """,
        (tenant_id, workspace_id, actor_id, attempt_id),
    ).fetchone()


def _fetch_detail_attempt_by_key(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    provider_account_hash: str,
    budget_date: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM liepin_detail_attempts
        WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ?
          AND provider_account_hash = ? AND budget_date = ? AND idempotency_key = ?
        """,
        (tenant_id, workspace_id, actor_id, provider_account_hash, budget_date, idempotency_key),
    ).fetchone()


def _update_detail_attempt(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    state: str,
    consumption_state: str,
    worker_command_id: str | None,
    raw_evidence_ref: str | None,
) -> LiepinDetailAttemptRow:
    _validate_detail_transition(row["state"], state, consumption_state)
    now = _now_iso()
    started_at = row["started_at"]
    if state == "started" and started_at is None:
        started_at = now
    completed_at = row["completed_at"]
    if state == "completed" and completed_at is None:
        completed_at = now
    next_worker_command_id = worker_command_id if worker_command_id is not None else row["worker_command_id"]
    next_raw_evidence_ref = raw_evidence_ref if raw_evidence_ref is not None else row["raw_evidence_ref"]
    conn.execute(
        """
        UPDATE liepin_detail_attempts
        SET state = ?,
            consumption_state = ?,
            started_at = ?,
            completed_at = ?,
            worker_command_id = ?,
            raw_evidence_ref = ?,
            updated_at = ?
        WHERE attempt_id = ?
        """,
        (
            state,
            consumption_state,
            started_at,
            completed_at,
            next_worker_command_id,
            next_raw_evidence_ref,
            now,
            row["attempt_id"],
        ),
    )
    updated = _fetch_detail_attempt(
        conn,
        tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"],
        actor_id=row["actor_id"],
        attempt_id=row["attempt_id"],
    )
    if updated is None:
        raise RuntimeError("Liepin detail attempt update lost its row.")
    return _detail_attempt_from_row(updated)


def _validate_detail_reservation_replay(
    row: sqlite3.Row,
    *,
    candidate_provider_id: str,
    provider_day_key: str,
    timezone: str,
) -> None:
    mismatched_fields = [
        field_name
        for field_name, expected in [
            ("candidate_provider_id", candidate_provider_id),
            ("provider_day_key", provider_day_key),
            ("timezone", timezone),
        ]
        if row[field_name] != expected
    ]
    if mismatched_fields:
        fields = ", ".join(mismatched_fields)
        raise ValueError(f"Liepin detail idempotency replay mismatch: {fields}")


def _validate_detail_transition(current_state: str, next_state: str, consumption_state: str) -> None:
    if next_state not in DETAIL_ATTEMPT_STATES:
        raise ValueError(f"invalid Liepin detail attempt state: {next_state}")
    if consumption_state not in DETAIL_CONSUMPTION_STATES:
        raise ValueError(f"invalid Liepin detail consumption state: {consumption_state}")
    if next_state not in DETAIL_ALLOWED_TRANSITIONS[current_state]:
        raise ValueError(f"invalid Liepin detail attempt transition: {current_state} -> {next_state}")
    if consumption_state not in _valid_consumption_states_for_detail_state(next_state):
        raise ValueError(
            f"invalid Liepin detail state/consumption pair: {next_state} + {consumption_state}"
        )
    if next_state == "completed" and consumption_state != "consumed":
        raise ValueError("completed Liepin detail attempts must be consumed.")
    if next_state == "failed_before_consumption" and consumption_state != "not_consumed":
        raise ValueError("pre-consumption Liepin detail failures must not consume budget.")
    if next_state == "failed_after_possible_consumption" and consumption_state != "possibly_consumed":
        raise ValueError("post-dispatch Liepin detail failures must be possibly consumed.")
    if next_state == "unknown" and consumption_state not in {"unknown", "possibly_consumed"}:
        raise ValueError("unknown Liepin detail attempts must use unknown or possibly_consumed consumption.")


def _valid_consumption_states_for_detail_state(state: str) -> set[str]:
    if state == "completed":
        return {"consumed"}
    if state == "failed_after_possible_consumption":
        return {"possibly_consumed"}
    if state == "unknown":
        return {"unknown", "possibly_consumed"}
    return {"not_consumed"}


def _detail_attempt_from_row(row: sqlite3.Row) -> LiepinDetailAttemptRow:
    return LiepinDetailAttemptRow(
        attempt_id=row["attempt_id"],
        tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"],
        actor_id=row["actor_id"],
        provider_account_hash=row["provider_account_hash"],
        candidate_provider_id=row["candidate_provider_id"],
        idempotency_key=row["idempotency_key"],
        state=row["state"],
        consumption_state=row["consumption_state"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        worker_command_id=row["worker_command_id"],
        raw_evidence_ref=row["raw_evidence_ref"],
        budget_date=row["budget_date"],
        provider_day_key=row["provider_day_key"],
        timezone=row["timezone"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def has_unsafe_payload(value: object) -> bool:
    return _has_unsafe_payload(value, parent_key=None)


def _has_unsafe_payload(value: object, *, parent_key: str | None) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = _normalize_payload_key(str(key))
            if normalized_key in {_normalize_payload_key(unsafe_key) for unsafe_key in UNSAFE_PAYLOAD_KEYS}:
                return True
            if "token" in normalized_key or "cookie" in normalized_key:
                return True
            if _has_unsafe_payload(child, parent_key=normalized_key):
                return True
    if isinstance(value, list):
        return any(_has_unsafe_payload(child, parent_key=parent_key) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        if parent_key == "redactionstate" and lowered == "raw_provider_payload":
            return False
        if (
            "authorization:" in lowered
            or "bearer " in lowered
            or lowered.startswith("basic ")
            or lowered.startswith("token ")
            or lowered.startswith("digest ")
            or "authorization basic " in lowered
            or "authorization token " in lowered
            or "authorization digest " in lowered
        ):
            return True
        if any(
            marker in lowered
            for marker in [
                "authorization: basic",
                "browsercontext=",
                "cdp://",
                "devtools/browser",
                "internal-worker-observed-account",
                "observedprovideraccountsubject",
                "devtools/page",
                "provider account subject",
                "provideraccountsubject",
                "remote debugging port",
                "storage_state",
                "rawproviderpayload",
                "raw_provider_payload",
                "providerpayload",
                "provider_payload",
                "workerurl=",
            ]
        ):
            return True
        if lowered.startswith(("ws://", "wss://")) and any(
            marker in lowered for marker in ["devtools/browser", "devtools/page", "playwright"]
        ):
            return True
        if ("127.0.0.1" in lowered or "localhost" in lowered) and any(
            marker in lowered for marker in [":9222", "/json/version", "/devtools/", "/internal", ":9999"]
        ):
            return True
        if ":9222" in lowered and any(marker in lowered for marker in ["/json/version", "/devtools/"]):
            return True
        if "worker" in lowered and "/internal" in lowered:
            return True
        if "liepin.com" in lowered and any(marker in lowered for marker in ["token=", "cookie=", "auth=", "sid="]):
            return True
    return False


def _append_event(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    subject_type: SubjectType,
    subject_id: str,
    event_name: str,
    payload: dict[str, object],
    redaction_state: str,
) -> int:
    if has_unsafe_payload(payload):
        raise ValueError("unsafe Liepin event payload")
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    row = conn.execute(
        """
        SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
        FROM liepin_events
        WHERE tenant_id = ? AND workspace_id = ? AND subject_type = ? AND subject_id = ?
        """,
        (tenant_id, workspace_id, subject_type, subject_id),
    ).fetchone()
    sequence = int(row["next_sequence"])
    conn.execute(
        """
        INSERT INTO liepin_events (
            tenant_id, workspace_id, actor_id, subject_type, subject_id, sequence,
            event_name, payload_json, redaction_state, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            workspace_id,
            actor_id,
            subject_type,
            subject_id,
            sequence,
            event_name,
            payload_json,
            redaction_state,
            _now_iso(),
        ),
    )
    return sequence


def _safe_revoke_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        return "unspecified"
    unsafe_keys = {_normalize_payload_key(unsafe_key) for unsafe_key in UNSAFE_PAYLOAD_KEYS}
    normalized_key = _normalize_revoke_reason_marker(normalized)
    if (
        has_unsafe_payload(normalized)
        or normalized_key in unsafe_keys
        or any(marker in normalized_key for marker in _UNSAFE_REVOKE_REASON_MARKERS)
    ):
        return "unsafe_reason_redacted"
    return normalized


_UNSAFE_REVOKE_REASON_MARKERS = {
    "access",
    "authorization",
    "authheader",
    "bearer",
    "basic",
    "cdp",
    "cookie",
    "debugwebsocket",
    "digest",
    "localstorage",
    "refresh",
    "sessionstorage",
    "storagestate",
    "token",
    "websocket",
}


def _normalize_payload_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").lower()


def _normalize_revoke_reason_marker(reason: str) -> str:
    return _normalize_payload_key(reason).replace(" ", "")


def _ensure_columns(conn: sqlite3.Connection, *, table_name: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    for column_name, column_type in columns.items():
        if column_name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {version}")


def _liepin_migration_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
