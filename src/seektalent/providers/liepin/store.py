from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.models import LiepinConnectionRow, LiepinEventRow, LiepinRunRow, SubjectType
from seektalent.providers.liepin.security import hmac_provider_account_hash


UNSAFE_PAYLOAD_KEYS = {
    "rawProviderPayload",
    "raw_provider_payload",
    "cookies",
    "storageState",
    "storage_state",
    "cdpUrl",
    "cdp_url",
    "workerUrl",
    "worker_url",
    "token",
    "streamToken",
    "stream_token",
}


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
                    gate_ref, tenant_id, workspace_id, actor_id, org_name, org_domain,
                    approved_purposes_json, search_keywords_json, retention_days, pii_policy,
                    operator_id, operator_name, created_at, approved_at, account_binding_hash,
                    requested_purpose
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gate_ref,
                    tenant_id,
                    workspace_id,
                    actor_id,
                    gate.org_name,
                    gate.org_domain,
                    json.dumps(gate.approved_purposes),
                    json.dumps(gate.search_keywords),
                    gate.retention_days,
                    gate.pii_policy,
                    gate.operator_id,
                    gate.operator_name,
                    gate.created_at,
                    gate.approved_at,
                    gate.account_binding_hash,
                    purpose,
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
    ) -> str:
        connection_id = f"conn_{uuid.uuid4().hex[:16]}"
        gate = self.get_compliance_gate(
            gate_ref=compliance_gate_ref,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        account_binding_hash = gate.account_binding_hash if gate is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO liepin_connections (
                    connection_id, tenant_id, workspace_id, actor_id, compliance_gate_ref,
                    status, account_binding_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    tenant_id,
                    workspace_id,
                    actor_id,
                    compliance_gate_ref,
                    "pending_login",
                    account_binding_hash,
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
                       account_binding_hash
                FROM liepin_connections
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (tenant_id, workspace_id, actor_id, connection_id),
            ).fetchone()
        if row is None:
            return None
        return LiepinConnectionRow(**dict(row))

    def bind_connection_account(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        secret: str,
    ) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT compliance_gate_ref
                FROM liepin_connections
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (tenant_id, workspace_id, actor_id, connection_id),
            ).fetchone()
            if row is None:
                return None
            account_hash = hmac_provider_account_hash(secret, connection_id)
            gate = conn.execute(
                """
                SELECT account_binding_hash
                FROM liepin_compliance_gates
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (row["compliance_gate_ref"], tenant_id, workspace_id, actor_id),
            ).fetchone()
            if gate is None:
                return None
            if gate["account_binding_hash"] not in {None, account_hash}:
                return None
            conn.execute(
                """
                UPDATE liepin_compliance_gates
                SET account_binding_hash = ?, approved_at = COALESCE(approved_at, ?)
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (account_hash, _now_iso(), row["compliance_gate_ref"], tenant_id, workspace_id, actor_id),
            )
            conn.execute(
                """
                UPDATE liepin_connections
                SET account_binding_hash = ?, status = 'connected'
                WHERE connection_id = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (account_hash, connection_id, tenant_id, workspace_id, actor_id),
            )
        return account_hash

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
        if _has_unsafe_payload(payload):
            raise ValueError("unsafe Liepin event payload")
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS liepin_compliance_gates (
                    gate_ref TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    org_name TEXT NOT NULL,
                    org_domain TEXT NOT NULL,
                    approved_purposes_json TEXT NOT NULL CHECK(json_valid(approved_purposes_json)),
                    search_keywords_json TEXT NOT NULL CHECK(json_valid(search_keywords_json)),
                    retention_days INTEGER NOT NULL,
                    pii_policy TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    account_binding_hash TEXT,
                    requested_purpose TEXT NOT NULL
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
                    account_binding_hash TEXT,
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
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn


def _gate_from_row(row: sqlite3.Row) -> ComplianceGate:
    return ComplianceGate(
        org_name=row["org_name"],
        org_domain=row["org_domain"],
        approved_purposes=json.loads(row["approved_purposes_json"]),
        search_keywords=json.loads(row["search_keywords_json"]),
        retention_days=row["retention_days"],
        pii_policy=row["pii_policy"],
        operator_id=row["operator_id"],
        operator_name=row["operator_name"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        account_binding_hash=row["account_binding_hash"],
    )


def _has_unsafe_payload(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in UNSAFE_PAYLOAD_KEYS:
                return True
            if isinstance(key, str) and ("token" in key.lower() or "cookie" in key.lower()):
                return True
            if _has_unsafe_payload(child):
                return True
    if isinstance(value, list):
        return any(_has_unsafe_payload(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in ["cdp://", "storage_state", "rawproviderpayload"])
    return False


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
