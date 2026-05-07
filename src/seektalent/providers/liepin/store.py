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

    def create_compliance_gate(self, gate: ComplianceGate, *, purpose: str) -> str:
        gate_ref = f"gate_{uuid.uuid4().hex[:16]}"
        status = "approved" if gate.provider_account_hash and gate.status != "denied" else gate.status
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
                    gate.tenant_id,
                    gate.workspace_id,
                    gate.actor_id,
                    gate.provider_account_hash,
                    status,
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
        provider_account_identity_hint: str | None = None,
    ) -> str:
        connection_id = f"conn_{uuid.uuid4().hex[:16]}"
        gate = self.get_compliance_gate(
            gate_ref=compliance_gate_ref,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        provider_account_hash = gate.provider_account_hash if gate is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO liepin_connections (
                    connection_id, tenant_id, workspace_id, actor_id, compliance_gate_ref,
                    status, provider_account_hash, provider_account_identity_hint, created_at
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
                    provider_account_hash,
                    provider_account_identity_hint,
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
                SELECT provider_account_identity_hint, compliance_gate_ref
                FROM liepin_connections
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (tenant_id, workspace_id, actor_id, connection_id),
            ).fetchone()
            if row is None or not row["provider_account_identity_hint"]:
                return None
            account_hash = hmac_provider_account_hash(secret, row["provider_account_identity_hint"])
            gate = conn.execute(
                """
                SELECT provider_account_hash
                FROM liepin_compliance_gates
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (row["compliance_gate_ref"], tenant_id, workspace_id, actor_id),
            ).fetchone()
            if gate is None:
                return None
            if gate["provider_account_hash"] not in {None, account_hash}:
                return None
            conn.execute(
                """
                UPDATE liepin_compliance_gates
                SET provider_account_hash = ?, status = 'approved'
                WHERE gate_ref = ? AND tenant_id = ? AND workspace_id = ? AND actor_id = ?
                """,
                (account_hash, row["compliance_gate_ref"], tenant_id, workspace_id, actor_id),
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
                    provider_account_hash TEXT,
                    status TEXT NOT NULL,
                    candidate_personal_info_processing_basis TEXT NOT NULL,
                    personal_information_processor TEXT NOT NULL,
                    operator_audit_owner TEXT NOT NULL,
                    account_holder_authorized INTEGER NOT NULL,
                    human_initiated_recruiting INTEGER NOT NULL,
                    allowed_purposes_json TEXT NOT NULL CHECK(json_valid(allowed_purposes_json)),
                    retention_policy TEXT NOT NULL,
                    deletion_sla_days INTEGER NOT NULL,
                    deletion_path TEXT NOT NULL,
                    raw_payload_access_scope TEXT NOT NULL,
                    raw_detail_retention_allowed_after_debug INTEGER NOT NULL,
                    fixture_export_allowed INTEGER NOT NULL,
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
                    provider_account_identity_hint TEXT,
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
