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
    "auth",
    "authHeader",
    "auth_header",
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
                    fixture_export_allowed, policy_ref, requested_purpose
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                """
            )
            _ensure_columns(
                conn,
                table_name="liepin_connections",
                columns={
                    "session_store_key_id": "TEXT",
                    "encrypted_state_sha256": "TEXT",
                    "session_updated_at": "TEXT",
                    "revoked_at": "TEXT",
                },
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
            normalized_key = _normalize_payload_key(str(key))
            if normalized_key in {_normalize_payload_key(unsafe_key) for unsafe_key in UNSAFE_PAYLOAD_KEYS}:
                return True
            if "token" in normalized_key or "cookie" in normalized_key:
                return True
            if _has_unsafe_payload(child):
                return True
    if isinstance(value, list):
        return any(_has_unsafe_payload(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
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
    if _has_unsafe_payload(payload):
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
        _has_unsafe_payload(normalized)
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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
