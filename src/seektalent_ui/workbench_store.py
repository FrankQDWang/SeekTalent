from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from seektalent.models import RequirementSheet
from seektalent.runtime.public_events import normalize_runtime_public_event, runtime_public_event_name
from seektalent_ui.models import WorkbenchNoteCreatedPayload, WorkbenchNoteKind, WorkbenchNoteStatusHint
from seektalent_ui.redaction import redact_event_payload, redact_text
from seektalent_ui.workbench_db import connect_workbench_db
from seektalent_ui.workbench_schema import initialize_workbench_schema
from seektalent_ui.workbench_store_helpers import (
    attr as _attr,
    bounded_text as _bounded_text,
    first as _first,
    int_or_none as _int_or_none,
    iso as _iso,
    json_list as _json_list,
    json_to_dict as _json_to_dict,
    json_to_list as _json_to_list,
    like_prefix as _like_prefix,
    mapping_get as _mapping_get,
    now as _now,  # noqa: F401 - compatibility export for Workbench helpers.
    now_iso as _now_iso,
    object_list as _object_list,
    parse_iso as _parse_iso,
    safe_candidate_text as _safe_candidate_text,
    safe_list as _safe_list,
    sha256_text as _sha256_text,
    stable_id as _stable_id,
)


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
DETAIL_OPEN_LEASE_SECONDS = 600
LIEPIN_DAILY_DETAIL_OPEN_LIMIT = 100
LIEPIN_AUTO_DETAIL_REQUEST_LIMIT = 5
LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD = 55


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
    detail_open_used_count: int = 0
    detail_open_blocked_count: int = 0


@dataclass(frozen=True)
class WorkbenchSourceRunRuntimeLink:
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    runtime_run_id: str | None


@dataclass(frozen=True)
class WorkbenchRuntimeSourceLaneLatestState:
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    runtime_run_id: str | None
    source_lane_run_id: str
    attempt: int
    event_seq: int
    event_type: str
    status: str | None
    payload: dict[str, object]


RuntimeLinkRepairStatus = Literal["attached", "already_attached", "runtime_link_missing"]
GraphCandidateRecoveryState = Literal["ready", "recoverable_empty"]
NOTE_STATUS_HINTS: set[WorkbenchNoteStatusHint] = {
    "new_progress",
    "waiting",
    "human_action_required",
    "completed",
    "failed",
    "canceled",
    "unknown",
}
NOTE_KINDS: set[WorkbenchNoteKind] = {"progress", "waiting", "human_action", "terminal"}
LIEPIN_BROWSER_LOGIN_REQUIRED_CODE = "liepin_browser_login_required"
LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE = "liepin_browser_probe_unavailable"
LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE = "liepin_browser_account_mismatch"
LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE = "请在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。"
LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE = "浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。"
LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE = "当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。"


@dataclass(frozen=True)
class WorkbenchRuntimeLinkRepairResult:
    status: RuntimeLinkRepairStatus
    graph_candidate_state: GraphCandidateRecoveryState
    runtime_run_id: str | None
    reason: str | None = None


def _new_source_run(
    source_kind: Literal["cts", "liepin"],
    *,
    liepin_connection_connected: bool = False,
) -> WorkbenchSourceRun:
    if source_kind == "cts":
        return WorkbenchSourceRun(
            source_run_id=f"src_{uuid.uuid4().hex[:16]}",
            source_kind="cts",
            status="queued",
            auth_state="not_required",
            warning_code=None,
            warning_message=None,
        )
    if liepin_connection_connected:
        return WorkbenchSourceRun(
            source_run_id=f"src_{uuid.uuid4().hex[:16]}",
            source_kind="liepin",
            status="queued",
            auth_state="not_required",
            warning_code=None,
            warning_message=None,
        )
    return WorkbenchSourceRun(
        source_run_id=f"src_{uuid.uuid4().hex[:16]}",
        source_kind="liepin",
        status="blocked",
        auth_state="login_required",
        warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
        warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    )


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
    compliance_gate_ref: str | None
    created_at: str
    updated_at: str
    connected_at: str | None


@dataclass(frozen=True)
class WorkbenchRequirementReview:
    session_id: str
    status: Literal["draft", "approved"]
    requirement_sheet: RequirementSheet | None
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
    requirement_review: WorkbenchRequirementReview


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
class WorkbenchRuntimeSourcingJob:
    job_id: str
    session_id: str
    status: Literal["queued", "running", "completed", "failed"]
    source_kinds: tuple[Literal["cts", "liepin"], ...]
    source_run_ids: tuple[str, ...]
    runtime_run_id: str | None
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
    schema_version: str
    idempotency_key: str | None
    payload: dict[str, object]
    occurred_at: str
    created_at: str


@dataclass(frozen=True)
class RuntimeSourceCountProjection:
    source_kind: Literal["cts", "liepin"]
    status: str | None
    warning_code: str | None
    cards_scanned_count: int | None
    unique_candidates_count: int | None
    event_seq: int


@dataclass
class _RuntimeSourceCountProjectionState:
    status_seq: int = -1
    count_seq: int = -1
    status: str | None = None
    warning_code: str | None = None
    cards_scanned_count: int | None = None
    unique_candidates_count: int | None = None


CandidateEvidenceLevel = Literal["card", "detail", "final"]
CandidateReviewStatus = Literal["new", "promising", "rejected"]


@dataclass(frozen=True)
class WorkbenchCandidateEvidence:
    evidence_id: str
    review_item_id: str
    source_run_id: str
    source_kind: Literal["cts", "liepin"]
    evidence_level: CandidateEvidenceLevel
    provider_candidate_key_hash: str
    runtime_identity_id: str | None
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
class WorkbenchRuntimeCandidateIdentitySnapshot:
    identity_id: str
    canonical_resume_id: str
    merged_resume_ids: list[str]
    source_evidence_ids: list[str]


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
    why_selected: str
    source_round: int | None
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


DetailOpenMode = Literal["human_confirm", "bypass_confirm"]
DetailOpenRequestStatus = Literal["pending", "approved", "rejected", "bypassed", "blocked", "expired"]
DetailOpenLedgerStatus = Literal["planned", "leased", "opened", "skipped", "blocked", "failed", "maybe_used"]


@dataclass(frozen=True)
class WorkbenchSourceRunPolicy:
    session_id: str
    source_kind: Literal["liepin"]
    detail_open_mode: DetailOpenMode
    updated_at: str


@dataclass(frozen=True)
class WorkbenchProviderAction:
    action_kind: Literal["managed_browser"]
    source_kind: Literal["liepin"]
    connection_id: str
    review_item_id: str
    budget_impact: Literal["none", "reserved"]
    message: str


@dataclass(frozen=True)
class WorkbenchDetailOpenLedger:
    ledger_id: str
    status: DetailOpenLedgerStatus
    budget_day: str
    lease_expires_at: str | None


@dataclass(frozen=True)
class WorkbenchDetailOpenCandidateSnapshot:
    review_item_id: str
    display_name: str
    title: str
    company: str
    location: str
    summary: str
    aggregate_score: int | None
    evidence_level: CandidateEvidenceLevel
    source_badges: list[str]
    matched_must_haves: list[str]
    matched_preferences: list[str]
    missing_risks: list[str]


@dataclass(frozen=True)
class WorkbenchDetailOpenRequest:
    request_id: str
    session_id: str
    review_item_id: str
    status: DetailOpenRequestStatus
    detail_open_mode: DetailOpenMode
    decision_note: str | None
    candidate: WorkbenchDetailOpenCandidateSnapshot | None
    blocked_reason: str | None
    ledger: WorkbenchDetailOpenLedger | None
    provider_action: WorkbenchProviderAction | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchSecurityAuditEvent:
    audit_id: int
    actor_user_id: str | None
    actor_role: str | None
    workspace_id: str
    request_ip: str | None
    user_agent: str | None
    target_type: str
    target_id: str | None
    action: str
    result: str
    reason_code: str | None
    metadata: dict[str, object]
    created_at: str


@dataclass(frozen=True)
class WorkbenchSourceRunJobContext:
    job: WorkbenchSourceRunJob
    session: WorkbenchSession
    requirement_review: WorkbenchRequirementReview


@dataclass(frozen=True)
class WorkbenchRuntimeSourcingJobContext:
    job: WorkbenchRuntimeSourcingJob
    session: WorkbenchSession
    requirement_review: WorkbenchRequirementReview


@dataclass(frozen=True)
class WorkbenchLiepinDetailOpenJobContext:
    intent_id: str
    idempotency_key: str
    request_id: str
    ledger_id: str
    review_item_id: str
    candidate_evidence_id: str
    candidate_resume_id: str | None
    provider_candidate_key_hash: str
    connection_id: str
    compliance_gate_ref: str
    provider_account_hash: str
    detail_candidates_json: str
    budget_day: str
    lease_expires_at: str | None
    source_run_id: str
    runtime_run_id: str | None
    session: WorkbenchSession
    requirement_review: WorkbenchRequirementReview


@dataclass(frozen=True)
class UserSessionTokens:
    session_token: str
    csrf_token: str


class WorkbenchStore:
    def __init__(self, db_path: str | Path) -> None:
        from seektalent_ui.workbench_auth_store import WorkbenchAuthStore

        self.db_path = Path(db_path)
        self._initialized = False
        self._auth = WorkbenchAuthStore(
            connect=self._connect,
            initialize=self._initialize,
            user_from_row=_user_from_row,
            append_security_audit_event=_append_security_audit_event_conn,
            security_audit_event_from_row=_security_audit_event_from_row,
        )

    def bootstrap_admin(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
    ) -> tuple[WorkbenchUser, WorkbenchWorkspace]:
        return self._auth.bootstrap_admin(
            email=email,
            display_name=display_name,
            password_hash=password_hash,
        )

    def get_user_for_login(self, *, email: str) -> tuple[WorkbenchUser, str, bool] | None:
        return self._auth.get_user_for_login(email=email)

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
        self._auth.record_login_attempt(
            email=email,
            success=success,
            reason=reason,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def is_login_locked(self, *, email: str, ip_address: str | None) -> bool:
        return self._auth.is_login_locked(email=email, ip_address=ip_address)

    def create_user_session(self, *, user_id: str, workspace_id: str) -> UserSessionTokens:
        return self._auth.create_user_session(user_id=user_id, workspace_id=workspace_id)

    def get_user_by_session(self, *, session_digest: str | None) -> WorkbenchUser | None:
        return self._auth.get_user_by_session(session_digest=session_digest)

    def get_user_by_session_readonly(self, *, session_digest: str | None) -> WorkbenchUser | None:
        return self._auth.get_user_by_session_readonly(session_digest=session_digest)

    def revoke_user_session(self, *, session_digest: str | None, user: WorkbenchUser | None = None) -> None:
        self._auth.revoke_user_session(session_digest=session_digest, user=user)

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
        self._auth.record_security_audit_event(
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            workspace_id=workspace_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            result=result,
            reason_code=reason_code,
            metadata=metadata,
        )

    def list_security_audit_events(self) -> list[WorkbenchSecurityAuditEvent]:
        return self._auth.list_security_audit_events()

    def list_security_audit_events_for_user(
        self,
        *,
        user: WorkbenchUser,
        limit: int = 200,
    ) -> list[WorkbenchSecurityAuditEvent]:
        return self._auth.list_security_audit_events_for_user(user=user, limit=limit)

    def rotate_session_csrf(self, *, session_digest: str) -> str:
        return self._auth.rotate_session_csrf(session_digest=session_digest)

    def verify_session_csrf(self, *, session_digest: str, csrf_token: str | None) -> bool:
        return self._auth.verify_session_csrf(session_digest=session_digest, csrf_token=csrf_token)

    def create_workbench_session(
        self,
        *,
        user: WorkbenchUser,
        job_title: str,
        jd_text: str,
        notes: str,
        source_kinds: list[Literal["cts", "liepin"]] | None = None,
    ) -> WorkbenchSession:
        now = _now_iso()
        session_id = f"session_{uuid.uuid4().hex[:16]}"
        requested_source_kinds: list[Literal["cts", "liepin"]] = (
            source_kinds if source_kinds is not None else ["cts", "liepin"]
        )
        source_runs: list[WorkbenchSourceRun] = []
        requirement_review = WorkbenchRequirementReview(
            session_id=session_id,
            status="draft",
            requirement_sheet=None,
            created_at=now,
            updated_at=now,
            approved_at=None,
        )
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            liepin_connection_connected = False
            if "liepin" in requested_source_kinds:
                liepin_connection_connected = (
                    conn.execute(
                        """
                        SELECT 1
                        FROM source_connections
                        WHERE tenant_id = ?
                          AND workspace_id = ?
                          AND user_id = ?
                          AND source_kind = 'liepin'
                          AND status = 'connected'
                        LIMIT 1
                        """,
                        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id),
                    ).fetchone()
                    is not None
                )
            source_runs = [
                _new_source_run(source_kind, liepin_connection_connected=liepin_connection_connected)
                for source_kind in requested_source_kinds
            ]
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
                INSERT INTO session_requirement_reviews (
                    session_id, tenant_id, workspace_id, user_id, status,
                    requirement_sheet_json,
                    created_at, updated_at, approved_at
                )
                VALUES (?, ?, ?, ?, 'draft', NULL, ?, ?, NULL)
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
            requirement_review=requirement_review,
        )

    def list_workbench_sessions(self, *, user: WorkbenchUser) -> list[WorkbenchSession]:
        self._initialize()
        self.reconcile_expired_runtime_sourcing_jobs()
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
            review_by_session = _requirement_reviews_by_session(conn, session_ids)
        return [
            _session_from_row(row, runs_by_session.get(row["session_id"], []), review_by_session[row["session_id"]])
            for row in rows
        ]

    def get_workbench_session(self, *, user: WorkbenchUser, session_id: str) -> WorkbenchSession | None:
        self._initialize()
        self.reconcile_expired_runtime_sourcing_jobs()
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
            requirement_review = _requirement_reviews_by_session(conn, [session_id])[session_id]
        return _session_from_row(row, source_runs, requirement_review)

    def list_runtime_source_lane_latest_state(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> list[WorkbenchRuntimeSourceLaneLatestState]:
        self._initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_run_id, source_kind, runtime_run_id, source_lane_run_id,
                       attempt, event_seq, event_type, status, payload_json
                FROM runtime_source_lane_latest_state
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                ORDER BY source_kind ASC, source_lane_run_id ASC
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id),
            ).fetchall()
        return [_runtime_source_lane_latest_state_from_row(row) for row in rows]

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
            _append_security_audit_event_conn(
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
            _append_security_audit_event_conn(
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
            _append_security_audit_event_conn(
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
            _append_workbench_event_conn(
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
            _append_workbench_event_conn(
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
            _append_security_audit_event_conn(
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
            _append_security_audit_event_conn(
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

    def get_requirement_review(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchRequirementReview | None:
        self._initialize()
        with self._connect() as conn:
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            review = _requirement_reviews_by_session(conn, [session_id]).get(session_id)
        return review

    def update_requirement_review(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        requirement_sheet: RequirementSheet,
    ) -> WorkbenchRequirementReview | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session_row = conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE session_id = ? AND workspace_id = ? AND user_id = ?
                """,
                (session_id, user.workspace_id, user.user_id),
            ).fetchone()
            if session_row is None:
                return None
            source_runs = _source_runs_by_session(conn, [session_id]).get(session_id, [])
            session = _session_from_row(
                session_row,
                source_runs,
                _requirement_reviews_by_session(conn, [session_id])[session_id],
            )
            _validate_requirement_sheet_for_session(session, requirement_sheet)
            conn.execute(
                """
                UPDATE session_requirement_reviews
                SET status = 'draft',
                    requirement_sheet_json = ?,
                    updated_at = ?,
                    approved_at = NULL
                WHERE session_id = ? AND workspace_id = ? AND user_id = ?
                """,
                (
                    _requirement_sheet_json(requirement_sheet),
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
                event_name="requirement_review_updated",
                payload={
                    "sessionId": session_id,
                    "mustHaveCapabilityCount": len(requirement_sheet.must_have_capabilities),
                    "preferredCapabilityCount": len(requirement_sheet.preferred_capabilities),
                    "queryTermCount": len(requirement_sheet.initial_query_term_pool),
                },
            )
            review = _requirement_reviews_by_session(conn, [session_id])[session_id]
        return review

    def approve_requirement_review(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchRequirementReview | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            review = _requirement_reviews_by_session(conn, [session_id]).get(session_id)
            if review is None:
                return None
            if review.requirement_sheet is None:
                raise PermissionError("requirement_review_empty")
            conn.execute(
                """
                UPDATE session_requirement_reviews
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
                event_name="requirement_review_approved",
                payload={"sessionId": session_id},
            )
            review = _requirement_reviews_by_session(conn, [session_id])[session_id]
        return review

    def block_source_run_for_start_probe(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_run_id: str,
        warning_code: str,
        warning_message: str,
    ) -> WorkbenchSourceRun | None:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
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
            if row is None:
                return None
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
                row["source_kind"] != "liepin"
                or row["status"] in {"running", "completed", "failed"}
                or (row["status"] == "queued" and active_runtime_job is not None)
                or not (row["status"] in {"blocked", "queued"} or row["auth_state"] == "login_required")
            ):
                return _source_run_from_row(row)
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'blocked',
                    auth_state = 'login_required',
                    warning_code = ?,
                    warning_message = ?
                WHERE source_run_id = ?
                  AND session_id = ?
                  AND source_kind = 'liepin'
                  AND status NOT IN ('running', 'completed', 'failed')
                  AND (status IN ('blocked', 'queued') OR auth_state = 'login_required')
                """,
                (warning_code, warning_message, source_run_id, session_id),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind="liepin",
                event_name="source_run_blocked",
                payload={
                    "sessionId": session_id,
                    "sourceRunId": source_run_id,
                    "sourceKind": "liepin",
                    "warningCode": warning_code,
                },
            )
            updated = conn.execute("SELECT * FROM source_runs WHERE source_run_id = ?", (source_run_id,)).fetchone()
        return _source_run_from_row(updated)

    def start_runtime_sourcing_job(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[WorkbenchRuntimeSourcingJob, bool] | None:
        self._initialize()
        self.reconcile_expired_runtime_sourcing_jobs()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session_row = conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE session_id = ? AND workspace_id = ? AND user_id = ?
                """,
                (session_id, user.workspace_id, user.user_id),
            ).fetchone()
            if session_row is None:
                return None
            requirement_review = _requirement_reviews_by_session(conn, [session_id])[session_id]
            if requirement_review.status != "approved":
                raise PermissionError("requirement_review_not_approved")
            if requirement_review.requirement_sheet is None:
                raise PermissionError("requirement_review_empty")
            source_runs = _source_runs_by_session(conn, [session_id]).get(session_id, [])
            selected_source_kinds = tuple(source_run.source_kind for source_run in source_runs)
            if not selected_source_kinds:
                raise ValueError("source_kinds_required")
            runnable_source_runs = [
                source_run
                for source_run in source_runs
                if source_run.status not in {"blocked", "completed"}
            ]
            source_kinds = tuple(source_run.source_kind for source_run in runnable_source_runs)
            source_run_ids = tuple(source_run.source_run_id for source_run in runnable_source_runs)
            if not runnable_source_runs:
                if any(source_run.status == "blocked" for source_run in source_runs):
                    raise PermissionError("selected_source_blocked")
                raise RuntimeError("runtime_sourcing_already_terminal")
            existing = conn.execute(
                """
                SELECT *
                FROM runtime_sourcing_jobs
                WHERE session_id = ?
                  AND status IN ('queued', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if existing is not None:
                return _runtime_sourcing_job_from_row(existing), False
            job_id = f"rtjob_{uuid.uuid4().hex[:16]}"
            conn.execute(
                """
                INSERT INTO runtime_sourcing_jobs (
                    job_id, tenant_id, workspace_id, user_id, session_id, status,
                    source_kinds_json, source_run_ids_json, runtime_run_id, lease_owner, lease_expires_at,
                    idempotency_key, attempt_count, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, NULL, NULL, NULL, ?, 0, NULL, ?, ?)
                """,
                (
                    job_id,
                    DEFAULT_TENANT_ID,
                    user.workspace_id,
                    user.user_id,
                    session_id,
                    json.dumps(list(source_kinds), separators=(",", ":")),
                    json.dumps(list(source_run_ids), separators=(",", ":")),
                    _bounded_text(idempotency_key, 128),
                    now,
                    now,
                ),
            )
            placeholders = ",".join("?" for _ in source_run_ids)
            conn.execute(
                f"""
                UPDATE source_runs
                SET status = 'queued'
                WHERE source_run_id IN ({placeholders})
                """,
                source_run_ids,
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="runtime_sourcing_queued",
                payload={"runtimeJobId": job_id, "sourceKinds": list(source_kinds)},
            )
            job = _runtime_sourcing_job_from_row(
                conn.execute("SELECT * FROM runtime_sourcing_jobs WHERE job_id = ?", (job_id,)).fetchone()
            )
        return job, True

    def claim_next_runtime_sourcing_job(
        self,
        *,
        owner_id: str,
        lease_expires_at: str,
    ) -> WorkbenchRuntimeSourcingJobContext | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM runtime_sourcing_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC, job_id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE runtime_sourcing_jobs
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
            source_runs = _source_runs_by_session(conn, [row["session_id"]]).get(row["session_id"], [])
            scoped_source_runs = _runtime_scoped_source_runs_from_job_row(row=row, source_runs=source_runs)
            source_kinds = tuple(source_run.source_kind for source_run in scoped_source_runs)
            source_run_ids = tuple(source_run.source_run_id for source_run in scoped_source_runs)
            conn.execute(
                """
                UPDATE runtime_sourcing_jobs
                SET source_kinds_json = ?,
                    source_run_ids_json = ?
                WHERE job_id = ?
                """,
                (
                    json.dumps(list(source_kinds), separators=(",", ":")),
                    json.dumps(list(source_run_ids), separators=(",", ":")),
                    row["job_id"],
                ),
            )
            for source_run in scoped_source_runs:
                conn.execute(
                    """
                    UPDATE source_runs
                    SET status = 'running', warning_code = NULL, warning_message = NULL
                    WHERE source_run_id = ?
                    """,
                    (source_run.source_run_id,),
                )
                _append_workbench_event_conn(
                    conn,
                    tenant_id=row["tenant_id"],
                    workspace_id=row["workspace_id"],
                    user_id=row["user_id"],
                    session_id=row["session_id"],
                    source_run_id=source_run.source_run_id,
                    source_kind=source_run.source_kind,
                    event_name="source_run_started",
                    payload={"sourceRunId": source_run.source_run_id, "sourceKind": source_run.source_kind},
                )
            _append_workbench_event_conn(
                conn,
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                source_run_id=None,
                source_kind=None,
                event_name="runtime_sourcing_started",
                payload={"runtimeJobId": row["job_id"], "sourceKinds": list(source_kinds)},
            )
            job = _runtime_sourcing_job_from_row(
                conn.execute("SELECT * FROM runtime_sourcing_jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
            )
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (row["session_id"],)).fetchone()
            source_runs = _source_runs_by_session(conn, [row["session_id"]]).get(row["session_id"], [])
            requirement_review = _requirement_reviews_by_session(conn, [row["session_id"]])[row["session_id"]]
        return WorkbenchRuntimeSourcingJobContext(
            job=job,
            session=_session_from_row(session_row, source_runs, requirement_review),
            requirement_review=requirement_review,
        )

    def extend_runtime_sourcing_job_lease(self, *, job_id: str, owner_id: str, lease_expires_at: str) -> bool:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE runtime_sourcing_jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND status = 'running'
                """,
                (lease_expires_at, _now_iso(), job_id, owner_id),
            )
        return cursor.rowcount == 1

    def attach_runtime_sourcing_job_runtime_run_id(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        runtime_run_id: str,
    ) -> None:
        self._initialize()
        runtime_run_id = runtime_run_id.strip()
        if not runtime_run_id:
            raise RuntimeError("runtime_run_id_required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM runtime_sourcing_jobs WHERE job_id = ?",
                (context.job.job_id,),
            ).fetchone()
            if row is None:
                return
            existing = row["runtime_run_id"]
            if existing and existing != runtime_run_id:
                raise RuntimeError("runtime_run_id_conflict")
            conn.execute(
                """
                UPDATE runtime_sourcing_jobs
                SET runtime_run_id = ?, updated_at = ?
                WHERE job_id = ? AND runtime_run_id IS NULL
                """,
                (runtime_run_id, _now_iso(), context.job.job_id),
            )
            conn.execute(
                """
                UPDATE source_runs
                SET runtime_run_id = ?
                WHERE session_id = ? AND runtime_run_id IS NULL AND source_run_id IN (SELECT value FROM json_each(?))
                """,
                (runtime_run_id, context.session.session_id, json.dumps(list(context.job.source_run_ids))),
            )

    def complete_runtime_sourcing_job_with_artifacts(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
    ) -> None:
        self._finish_runtime_sourcing_job(context=context, status="completed", error_message=None, artifacts=artifacts)

    def refresh_runtime_candidate_index_with_artifacts(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
    ) -> None:
        self._initialize()
        now = _now_iso()
        runtime_run_id = _runtime_run_id_from_artifacts(artifacts)
        if runtime_run_id is None:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM runtime_sourcing_jobs WHERE job_id = ?",
                (context.job.job_id,),
            ).fetchone()
            if row is None or row["status"] != "running":
                return
            attached_runtime_run_id = row["runtime_run_id"]
            if attached_runtime_run_id and attached_runtime_run_id != runtime_run_id:
                raise RuntimeError("runtime_run_id_conflict")
            self._persist_runtime_final_candidate_results_conn(
                conn,
                context=context,
                artifacts=artifacts,
                now=now,
                runtime_run_id=runtime_run_id,
                write_finalization_revision=False,
                write_runtime_source_lane_events=False,
                write_detail_recommendations=False,
            )
            conn.execute(
                """
                UPDATE runtime_sourcing_jobs
                SET runtime_run_id = COALESCE(runtime_run_id, ?),
                    updated_at = ?
                WHERE job_id = ?
                """,
                (runtime_run_id, now, context.job.job_id),
            )
            conn.execute(
                """
                UPDATE source_runs
                SET runtime_run_id = COALESCE(runtime_run_id, ?)
                WHERE session_id = ? AND runtime_run_id IS NULL
                """,
                (runtime_run_id, context.session.session_id),
            )

    def fail_runtime_sourcing_job(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        error_message: str,
    ) -> None:
        safe_error_message = redact_text(_bounded_text(error_message, 500)) or "Runtime sourcing failed."
        self._finish_runtime_sourcing_job(
            context=context,
            status="failed",
            error_message=safe_error_message,
            artifacts=None,
        )

    def _finish_runtime_sourcing_job(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        status: Literal["completed", "failed"],
        error_message: str | None,
        artifacts: object | None,
    ) -> None:
        self._initialize()
        now = _now_iso()
        runtime_run_id = _runtime_run_id_from_artifacts(artifacts) if artifacts is not None else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM runtime_sourcing_jobs WHERE job_id = ?",
                (context.job.job_id,),
            ).fetchone()
            if row is None or row["status"] != "running":
                return
            attached_runtime_run_id = row["runtime_run_id"]
            if runtime_run_id is not None:
                if attached_runtime_run_id and attached_runtime_run_id != runtime_run_id:
                    raise RuntimeError("runtime_run_id_conflict")
                attached_runtime_run_id = runtime_run_id
            scoped_source_runs = _runtime_scoped_source_runs(
                source_runs=context.session.source_runs,
                source_run_ids=context.job.source_run_ids,
                source_kinds=context.job.source_kinds,
            )
            scoped_source_run_ids = tuple(source_run.source_run_id for source_run in scoped_source_runs)
            review_counts_by_source_run_id: dict[str, int] = {}
            if status == "completed" and artifacts is not None:
                review_counts_by_source_run_id = self._persist_runtime_final_candidate_results_conn(
                    conn,
                    context=context,
                    artifacts=artifacts,
                    now=now,
                    runtime_run_id=attached_runtime_run_id,
                )
                if not review_counts_by_source_run_id:
                    for source_run in scoped_source_runs:
                        if source_run.source_kind != "cts":
                            continue
                        projection_context = WorkbenchSourceRunJobContext(
                            job=WorkbenchSourceRunJob(
                                job_id=context.job.job_id,
                                source_run_id=source_run.source_run_id,
                                session_id=context.session.session_id,
                                source_kind="cts",
                                status="running",
                                attempt_count=context.job.attempt_count,
                                error_message=None,
                                created_at=context.job.created_at,
                                updated_at=context.job.updated_at,
                            ),
                            session=context.session,
                            requirement_review=context.requirement_review,
                        )
                        review_item_ids = self._persist_cts_candidate_results_conn(
                            conn,
                            context=projection_context,
                            artifacts=artifacts,
                            now=now,
                        )
                        review_counts_by_source_run_id[source_run.source_run_id] = len(review_item_ids)
            conn.execute(
                """
                UPDATE runtime_sourcing_jobs
                SET status = ?,
                    runtime_run_id = COALESCE(runtime_run_id, ?),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error_message = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (status, attached_runtime_run_id, error_message, now, context.job.job_id),
            )
            source_status = "completed" if status == "completed" else "failed"
            if scoped_source_run_ids:
                placeholders = ",".join("?" for _ in scoped_source_run_ids)
                conn.execute(
                    f"""
                    UPDATE source_runs
                    SET status = ?,
                        runtime_run_id = COALESCE(runtime_run_id, ?),
                        warning_code = ?,
                        warning_message = ?
                    WHERE source_run_id IN ({placeholders})
                    """,
                    (
                        source_status,
                        attached_runtime_run_id,
                        "runtime_failed" if status == "failed" else None,
                        error_message if status == "failed" else None,
                        *scoped_source_run_ids,
                    ),
                )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=None,
                source_kind=None,
                event_name=f"runtime_sourcing_{status}",
                payload={
                    "runtimeJobId": context.job.job_id,
                    "status": status,
                    "errorMessage": error_message,
                },
            )
            for source_run in scoped_source_runs:
                if source_run.source_run_id in review_counts_by_source_run_id:
                    review_count = review_counts_by_source_run_id[source_run.source_run_id]
                    conn.execute(
                        """
                        UPDATE source_runs
                        SET cards_scanned_count = ?,
                            unique_candidates_count = ?
                        WHERE source_run_id = ?
                        """,
                        (review_count, review_count, source_run.source_run_id),
                    )
                _append_workbench_event_conn(
                    conn,
                    tenant_id=DEFAULT_TENANT_ID,
                    workspace_id=context.session.workspace_id,
                    user_id=context.session.owner_user_id,
                    session_id=context.session.session_id,
                    source_run_id=source_run.source_run_id,
                    source_kind=source_run.source_kind,
                    event_name=f"source_run_{status}",
                    payload={
                        "sourceRunId": source_run.source_run_id,
                        "sourceKind": source_run.source_kind,
                        "status": source_status,
                        "errorMessage": error_message,
                    },
                )

    def reconcile_expired_runtime_sourcing_jobs(self) -> int:
        self._initialize()
        now = _now_iso()
        safe_error_message = "Runtime sourcing job lease expired."
        reconciled = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_sourcing_jobs
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at ASC, job_id ASC
                """,
                (now,),
            ).fetchall()
            for row in rows:
                source_runs = _source_runs_by_session(conn, [row["session_id"]]).get(row["session_id"], [])
                scoped_source_runs = _runtime_scoped_source_runs_from_job_row(row=row, source_runs=source_runs)
                scoped_source_run_ids = tuple(source_run.source_run_id for source_run in scoped_source_runs)
                conn.execute(
                    """
                    UPDATE runtime_sourcing_jobs
                    SET status = 'failed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        error_message = ?,
                        updated_at = ?
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (safe_error_message, now, row["job_id"]),
                )
                if scoped_source_run_ids:
                    placeholders = ",".join("?" for _ in scoped_source_run_ids)
                    conn.execute(
                        f"""
                        UPDATE source_runs
                        SET status = 'failed',
                            warning_code = 'job_lease_expired',
                            warning_message = ?
                        WHERE source_run_id IN ({placeholders})
                        """,
                        (safe_error_message, *scoped_source_run_ids),
                    )
                _append_workbench_event_conn(
                    conn,
                    tenant_id=row["tenant_id"],
                    workspace_id=row["workspace_id"],
                    user_id=row["user_id"],
                    session_id=row["session_id"],
                    source_run_id=None,
                    source_kind=None,
                    event_name="runtime_sourcing_failed",
                    payload={
                        "runtimeJobId": row["job_id"],
                        "status": "failed",
                        "errorMessage": safe_error_message,
                        "reason": "job_lease_expired",
                    },
                )
                reconciled += 1
        return reconciled

    def reconcile_expired_detail_open_leases(self) -> int:
        self._initialize()
        now = _now_iso()
        reconciled = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT ledger.*, requests.session_id, requests.review_item_id
                FROM detail_open_ledger AS ledger
                JOIN detail_open_requests AS requests ON requests.request_id = ledger.request_id
                WHERE ledger.status = 'leased'
                  AND ledger.lease_expires_at IS NOT NULL
                  AND ledger.lease_expires_at <= ?
                ORDER BY ledger.lease_expires_at ASC, ledger.ledger_id ASC
                """,
                (now,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE detail_open_ledger
                    SET status = 'maybe_used', updated_at = ?
                    WHERE ledger_id = ? AND status = 'leased'
                    """,
                    (now, row["ledger_id"]),
                )
                _append_workbench_event_conn(
                    conn,
                    tenant_id=row["tenant_id"],
                    workspace_id=row["workspace_id"],
                    user_id=row["actor_id"],
                    session_id=row["session_id"],
                    source_run_id=row["source_run_id"],
                    source_kind="liepin",
                    event_name="liepin_detail_open_lease_expired",
                    payload={
                        "requestId": row["request_id"],
                        "reviewItemId": row["review_item_id"],
                        "status": "maybe_used",
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
        schema_version: str = "workbench_event_v1",
        idempotency_key: str | None = None,
        occurred_at: str | None = None,
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
                schema_version=schema_version,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
            )

    def append_runtime_public_event_by_ids(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        source_kind: Literal["cts", "liepin"] | None,
        payload: Mapping[str, object],
    ) -> WorkbenchEvent:
        event_payload = normalize_runtime_public_event(payload)
        payload_source_kind = event_payload["sourceKind"]
        if source_kind is not None and payload_source_kind not in {None, source_kind}:
            raise ValueError("runtime_public_event_source_kind_mismatch")
        resolved_source_kind = _event_source_kind(payload_source_kind if payload_source_kind is not None else source_kind)
        event_name = runtime_public_event_name(event_payload["stage"])
        event_id = _bounded_text(event_payload["eventId"], 160)
        if not event_id:
            raise ValueError("Runtime public event idempotency key is required.")
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = _runtime_public_event_by_idempotency_conn(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                idempotency_key=event_id,
            )
            if existing is not None:
                return _event_from_row(existing)
            if not _session_exists_for_ids_conn(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
            ):
                raise ValueError("Workbench session does not exist.")
            try:
                return _append_workbench_event_conn(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                    source_run_id=None,
                    source_kind=resolved_source_kind,
                    event_name=event_name,
                    schema_version=event_payload["schemaVersion"],
                    idempotency_key=event_id,
                    occurred_at=event_payload["createdAt"],
                    payload={key: value for key, value in event_payload.items()},
                )
            except sqlite3.IntegrityError:
                existing = _runtime_public_event_by_idempotency_conn(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                    idempotency_key=event_id,
                )
                if existing is None:
                    raise
                return _event_from_row(existing)

    def reconcile_runtime_public_events_from_artifacts(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
    ) -> int:
        run_dir = getattr(artifacts, "run_dir", None)
        if not isinstance(run_dir, Path):
            try:
                run_dir = Path(run_dir) if run_dir is not None else None
            except TypeError:
                run_dir = None
        if run_dir is None:
            return 0
        event_path = run_dir / "runtime" / "public_events.jsonl"
        if not event_path.exists():
            return 0
        appended = 0
        for line in event_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            before = self.append_runtime_public_event_by_ids(
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_kind=_event_source_kind(payload.get("sourceKind")),
                payload=cast(dict[str, object], payload),
            )
            if before.global_seq:
                appended += 1
        return appended

    def latest_runtime_source_count_projection(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> dict[Literal["cts", "liepin"], RuntimeSourceCountProjection]:
        events = self.list_recent_session_events(user=user, session_id=session_id, event_prefix="runtime_", limit=200)
        working: dict[Literal["cts", "liepin"], _RuntimeSourceCountProjectionState] = {}
        for event in events:
            if event.schema_version != "runtime_public_event_v1":
                continue
            payload = event.payload
            source_kind = _event_source_kind(payload.get("sourceKind") or event.source_kind)
            if source_kind is None:
                continue
            state = working.setdefault(source_kind, _RuntimeSourceCountProjectionState())
            event_seq = _int_or_none(payload.get("eventSeq"))
            if event_seq is None:
                event_seq = event.global_seq
            status = _runtime_public_status(payload.get("status"))
            reason_code = _safe_candidate_text(payload.get("safeReasonCode"), 96)
            if status is not None and event_seq >= state.status_seq:
                state.status = status
                state.warning_code = reason_code
                state.status_seq = event_seq
            counts = payload.get("counts")
            if isinstance(counts, Mapping):
                counts_map = cast(Mapping[str, object], counts)
                returned = _int_or_none(counts_map.get("sourceCumulativeReturned"))
                identities = _int_or_none(counts_map.get("sourceCumulativeIdentities"))
                has_count = returned is not None or identities is not None
                if has_count and event_seq >= state.count_seq:
                    if returned is not None:
                        state.cards_scanned_count = max(returned, 0)
                    if identities is not None:
                        state.unique_candidates_count = max(identities, 0)
                    state.count_seq = event_seq
        return {
            source_kind: RuntimeSourceCountProjection(
                source_kind=source_kind,
                status=state.status,
                warning_code=state.warning_code,
                cards_scanned_count=state.cards_scanned_count,
                unique_candidates_count=state.unique_candidates_count,
                event_seq=max(state.status_seq, state.count_seq),
            )
            for source_kind, state in working.items()
        }

    def try_append_workbench_note(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        idempotency_key: str,
        text: str,
        status_hint: str,
        note_kind: str,
    ) -> WorkbenchEvent:
        safe_idempotency_key = _bounded_text(idempotency_key, 160)
        if not safe_idempotency_key:
            raise ValueError("Workbench note idempotency key is required.")
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = _workbench_note_event_by_idempotency_conn(
                conn,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                idempotency_key=safe_idempotency_key,
            )
            if existing is not None:
                return _event_from_row(existing)
            if not _session_exists_for_user_conn(conn, user=user, session_id=session_id):
                raise ValueError("Workbench session does not exist.")
            now = _now_iso()
            note_id = f"note_{uuid.uuid4().hex[:16]}"
            payload = WorkbenchNoteCreatedPayload(
                eventSeq=0,
                noteId=note_id,
                text=_safe_candidate_text(text, 5000) or "",
                statusHint=_workbench_note_status_hint(status_hint),
                noteKind=_workbench_note_kind(note_kind),
                createdAt=now,
            ).model_dump()
            event = _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="workbench_note_created",
                schema_version="workbench_note_v1",
                idempotency_key=safe_idempotency_key,
                payload=payload,
                occurred_at=now,
            )
            payload["eventSeq"] = event.global_seq
            safe_payload = WorkbenchNoteCreatedPayload.model_validate(payload).model_dump()
            conn.execute(
                """
                UPDATE session_events
                SET payload_redacted_json = ?
                WHERE global_seq = ?
                """,
                (json.dumps(safe_payload, sort_keys=True, separators=(",", ":")), event.global_seq),
            )
            return WorkbenchEvent(
                global_seq=event.global_seq,
                session_seq=event.session_seq,
                session_id=event.session_id,
                source_run_id=event.source_run_id,
                source_kind=event.source_kind,
                event_name=event.event_name,
                schema_version=event.schema_version,
                idempotency_key=event.idempotency_key,
                payload=safe_payload,
                occurred_at=event.occurred_at,
                created_at=event.created_at,
            )

    def claim_workbench_note_writer_lease(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        lease_owner: str,
        lease_expires_at: str,
        last_tick_slot: int | None = None,
        in_flight_started_at: str | None = None,
        now: str | None = None,
    ) -> bool:
        safe_owner = _bounded_text(lease_owner, 160)
        if not safe_owner:
            raise ValueError("Workbench note writer lease owner and expiration are required.")
        safe_expires_at, _ = _canonical_note_writer_lease_time(lease_expires_at)
        safe_now, now_at = _canonical_note_writer_lease_time(now or _now_iso())
        safe_in_flight_started_at, _ = _canonical_note_writer_lease_time(in_flight_started_at or safe_now)
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _session_exists_for_user_conn(conn, user=user, session_id=session_id):
                raise ValueError("Workbench session does not exist.")
            row = conn.execute(
                """
                SELECT *
                FROM workbench_note_writer_leases
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND session_id = ?
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id),
            ).fetchone()
            if row is not None and row["lease_owner"] != safe_owner and _parse_iso(row["lease_expires_at"]) > now_at:
                return False
            if row is None:
                conn.execute(
                    """
                    INSERT INTO workbench_note_writer_leases (
                        tenant_id, workspace_id, user_id, session_id,
                        lease_owner, lease_expires_at, last_tick_slot,
                        in_flight_started_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        DEFAULT_TENANT_ID,
                        user.workspace_id,
                        user.user_id,
                        session_id,
                        safe_owner,
                        safe_expires_at,
                        last_tick_slot,
                        safe_in_flight_started_at,
                        safe_now,
                        safe_now,
                    ),
                )
                return True
            conn.execute(
                """
                UPDATE workbench_note_writer_leases
                SET lease_owner = ?,
                    lease_expires_at = ?,
                    last_tick_slot = ?,
                    in_flight_started_at = ?,
                    updated_at = ?
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND session_id = ?
                """,
                (
                    safe_owner,
                    safe_expires_at,
                    last_tick_slot,
                    safe_in_flight_started_at,
                    safe_now,
                    DEFAULT_TENANT_ID,
                    user.workspace_id,
                    user.user_id,
                    session_id,
                ),
            )
            return True

    def release_workbench_note_writer_lease(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        lease_owner: str,
    ) -> bool:
        safe_owner = _bounded_text(lease_owner, 160)
        if not safe_owner:
            raise ValueError("Workbench note writer lease owner is required.")
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                DELETE FROM workbench_note_writer_leases
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ?
                  AND session_id = ? AND lease_owner = ?
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id, safe_owner),
            )
            return cursor.rowcount > 0

    def attach_source_run_runtime_run_id(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        runtime_run_id: str,
    ) -> None:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _attach_source_run_runtime_run_id_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.job.source_run_id,
                runtime_run_id=runtime_run_id,
            )

    def repair_cts_source_run_runtime_link(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_run_id: str,
        runtime_run_id: str | None = None,
    ) -> WorkbenchRuntimeLinkRepairResult:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _source_run_runtime_link_row_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
            )
            if row is None or row["source_kind"] != "cts":
                return WorkbenchRuntimeLinkRepairResult(
                    status="runtime_link_missing",
                    graph_candidate_state="recoverable_empty",
                    runtime_run_id=None,
                    reason="runtime_link_missing",
                )
            existing = row["runtime_run_id"]
            if existing:
                return WorkbenchRuntimeLinkRepairResult(
                    status="already_attached",
                    graph_candidate_state="ready",
                    runtime_run_id=existing,
                )
            if runtime_run_id is None:
                return WorkbenchRuntimeLinkRepairResult(
                    status="runtime_link_missing",
                    graph_candidate_state="recoverable_empty",
                    runtime_run_id=None,
                    reason="runtime_link_missing",
                )
            if row["status"] not in {"running", "completed", "failed"}:
                return WorkbenchRuntimeLinkRepairResult(
                    status="runtime_link_missing",
                    graph_candidate_state="recoverable_empty",
                    runtime_run_id=None,
                    reason="runtime_run_not_started",
                )
            _attach_source_run_runtime_run_id_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                runtime_run_id=runtime_run_id,
            )
            return WorkbenchRuntimeLinkRepairResult(
                status="attached",
                graph_candidate_state="ready",
                runtime_run_id=runtime_run_id,
            )

    def get_scoped_source_run_runtime_link(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_kind: Literal["cts", "liepin"],
    ) -> WorkbenchSourceRunRuntimeLink | None:
        self._initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT source_run_id, source_kind, runtime_run_id
                FROM source_runs
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                  AND source_kind = ?
                ORDER BY created_at ASC, source_run_id ASC
                LIMIT 1
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id, source_kind),
            ).fetchone()
        if row is None:
            return None
        return WorkbenchSourceRunRuntimeLink(
            source_run_id=row["source_run_id"],
            source_kind=row["source_kind"],
            runtime_run_id=row["runtime_run_id"],
        )

    def list_runtime_candidate_identity_snapshots(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        runtime_run_id: str,
    ) -> list[WorkbenchRuntimeCandidateIdentitySnapshot] | None:
        self._initialize()
        with self._connect() as conn:
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            rows = conn.execute(
                """
                SELECT identity_id, canonical_resume_id, merged_resume_ids_json, source_evidence_ids_json
                FROM runtime_candidate_identity_snapshots
                WHERE session_id = ? AND runtime_run_id = ?
                ORDER BY created_at ASC, identity_id ASC
                """,
                (session_id, runtime_run_id),
            ).fetchall()
        return [
            WorkbenchRuntimeCandidateIdentitySnapshot(
                identity_id=row["identity_id"],
                canonical_resume_id=row["canonical_resume_id"],
                merged_resume_ids=_json_to_list(row["merged_resume_ids_json"]),
                source_evidence_ids=_json_to_list(row["source_evidence_ids_json"]),
            )
            for row in rows
        ]

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

    def list_session_workbench_events(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
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
                WHERE workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                  AND global_seq > ?
                ORDER BY global_seq ASC
                LIMIT ?
                """,
                (user.workspace_id, user.user_id, session_id, max(after_seq, 0), safe_limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_all_session_workbench_events(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> list[WorkbenchEvent]:
        events: list[WorkbenchEvent] = []
        after_seq = 0
        while True:
            page = self.list_session_workbench_events(
                user=user,
                session_id=session_id,
                after_seq=after_seq,
                limit=200,
            )
            if not page:
                break
            events.extend(page)
            after_seq = page[-1].global_seq
            if len(page) < 200:
                break
        return events

    def latest_workbench_event_seq(self, *, user: WorkbenchUser, session_id: str | None = None) -> int:
        self._initialize()
        clauses = ["workspace_id = ?", "user_id = ?"]
        params: list[object] = [user.workspace_id, user.user_id]
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT MAX(global_seq) AS latest_seq
                FROM session_events
                WHERE {" AND ".join(clauses)}
                """,
                params,
            ).fetchone()
        return int(row["latest_seq"] or 0) if row is not None else 0

    def list_recent_workbench_notes(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        limit: int = 15,
    ) -> list[WorkbenchEvent]:
        self._initialize()
        safe_limit = min(max(limit, 1), 50)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM session_events
                WHERE workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                  AND event_name = 'workbench_note_created'
                ORDER BY global_seq DESC
                LIMIT ?
                """,
                (user.workspace_id, user.user_id, session_id, safe_limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_recent_session_events(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        event_prefix: str,
        limit: int = 100,
    ) -> list[WorkbenchEvent]:
        self._initialize()
        safe_limit = min(max(limit, 1), 200)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM session_events
                WHERE workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                  AND event_name LIKE ? ESCAPE '\\'
                ORDER BY global_seq DESC
                LIMIT ?
                """,
                (user.workspace_id, user.user_id, session_id, _like_prefix(event_prefix), safe_limit),
            ).fetchall()
        return [_event_from_row(row) for row in reversed(rows)]

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

    def _persist_runtime_final_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
        now: str,
        runtime_run_id: str | None,
        write_finalization_revision: bool = True,
        write_runtime_source_lane_events: bool = True,
        write_detail_recommendations: bool = True,
    ) -> dict[str, int]:
        run_state = getattr(artifacts, "run_state", None)
        if run_state is None or runtime_run_id is None:
            return {}
        ordered_identity_ids = _runtime_final_identity_order_from_artifacts(artifacts)
        persist_identity_ids = _runtime_persist_identity_order_from_artifacts(
            artifacts,
            ordered_identity_ids=ordered_identity_ids,
        )
        next_revision = int(
            conn.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM runtime_finalization_revisions WHERE session_id = ?",
                (context.session.session_id,),
            ).fetchone()[0]
            or 1
        )
        revision = next_revision
        if write_finalization_revision:
            conn.execute(
                """
                INSERT INTO runtime_finalization_revisions (
                    session_id, runtime_run_id, revision, reason_code,
                    ordered_candidate_identity_ids_json, coverage_summary_json, created_at
                )
                VALUES (?, ?, ?, 'runtime_finalized', ?, ?, ?)
                """,
                (
                    context.session.session_id,
                    runtime_run_id,
                    revision,
                    json.dumps(ordered_identity_ids, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(
                        _runtime_coverage_summary_payload(run_state),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
        source_run_by_kind: dict[str, str] = {
            source_run.source_kind: source_run.source_run_id for source_run in context.session.source_runs
        }
        source_counts: dict[str, int] = {source_run.source_run_id: 0 for source_run in context.session.source_runs}
        evidence_review_item_by_id: dict[str, str] = {}
        evidence_provider_hash_by_id: dict[str, str] = {}
        candidate_store = getattr(artifacts, "candidate_store", {}) or {}
        normalized_store = getattr(artifacts, "normalized_store", {}) or {}
        finalizer_candidate_by_resume_id = _finalizer_candidate_by_resume_id(artifacts)
        identity_snapshot_rows: list[tuple[object, ...]] = []
        review_item_rows: list[tuple[object, ...]] = []
        candidate_evidence_rows: list[tuple[object, ...]] = []
        for identity_id in persist_identity_ids:
            canonical_resume_id = _runtime_canonical_resume_id(run_state, identity_id)
            if not canonical_resume_id:
                continue
            merged_resume_ids = _runtime_merged_resume_ids(run_state, identity_id, canonical_resume_id)
            runtime_evidence = _runtime_source_evidence_for_identity(run_state, identity_id)
            source_evidence_ids = [
                evidence_id
                for evidence in runtime_evidence
                if (evidence_id := _safe_candidate_text(getattr(evidence, "evidence_id", None), 256))
            ]
            identity_snapshot_rows.append(
                (
                    context.session.session_id,
                    runtime_run_id,
                    identity_id,
                    canonical_resume_id,
                    json.dumps(merged_resume_ids, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(source_evidence_ids, ensure_ascii=False, separators=(",", ":")),
                    now,
                )
            )
            review_item_id = _stable_id("review", context.session.session_id, "identity", identity_id)
            primary_evidence_id = source_evidence_ids[0] if source_evidence_ids else _stable_id(
                "evidence",
                context.session.session_id,
                identity_id,
                "final",
            )
            raw_candidate = _mapping_get(candidate_store, canonical_resume_id)
            normalized = _mapping_get(normalized_store, canonical_resume_id)
            finalizer_candidate = finalizer_candidate_by_resume_id.get(canonical_resume_id)
            raw_payload = _attr(raw_candidate, "raw")
            display_name = (
                _safe_candidate_text(_attr(normalized, "candidate_name"), 160)
                or _safe_candidate_text(_attr(raw_payload, "candidate_name"), 160)
                or f"Candidate {review_item_id[-8:]}"
            )
            title = (
                _safe_candidate_text(_attr(normalized, "current_title"), 240)
                or _safe_candidate_text(_attr(raw_payload, "current_title"), 240)
                or _safe_candidate_text(_attr(raw_candidate, "expected_job_category"), 240)
                or ""
            )
            company = (
                _safe_candidate_text(_attr(normalized, "current_company"), 240)
                or _safe_candidate_text(_attr(raw_payload, "current_company"), 240)
                or ""
            )
            location = (
                _safe_candidate_text(_first(_attr(normalized, "locations")), 160)
                or _safe_candidate_text(_attr(raw_candidate, "now_location"), 160)
                or ""
            )
            score = _int_or_none(_attr(finalizer_candidate, "final_score"))
            scorecard = _mapping_get(getattr(run_state, "scorecards_by_resume_id", {}) or {}, canonical_resume_id)
            if score is None:
                score = _int_or_none(_attr(scorecard, "overall_score"))
            fit_bucket = _safe_candidate_text(_attr(finalizer_candidate, "fit_bucket"), 64) or _safe_candidate_text(
                _attr(scorecard, "fit_bucket"),
                64,
            )
            summary = (
                _safe_candidate_text(_attr(finalizer_candidate, "match_summary"), 1000)
                or _safe_candidate_text(_attr(finalizer_candidate, "why_selected"), 1000)
                or _safe_candidate_text(_attr(raw_candidate, "search_text"), 1000)
                or ""
            )
            why_selected = _safe_candidate_text(_attr(finalizer_candidate, "why_selected"), 1000) or ""
            source_round = _int_or_none(_attr(finalizer_candidate, "source_round"))
            if source_round is None:
                source_round = _int_or_none(_attr(raw_candidate, "source_round"))
            if source_round is None:
                source_round = _runtime_source_round_from_evidence_items(runtime_evidence)
            review_item_rows.append(
                (
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    primary_evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    summary,
                    score,
                    fit_bucket,
                    why_selected,
                    source_round,
                    now,
                    now,
                )
            )
            evidence_items = runtime_evidence or [
                _runtime_fallback_final_evidence(
                    identity_id=identity_id,
                    canonical_resume_id=canonical_resume_id,
                    source_kind="cts" if "cts" in source_run_by_kind else next(iter(source_run_by_kind)),
                    evidence_id=primary_evidence_id,
                )
            ]
            for evidence in evidence_items:
                source_kind = _safe_candidate_text(getattr(evidence, "source", None), 32)
                if source_kind not in source_run_by_kind:
                    continue
                source_kind = cast(Literal["cts", "liepin"], source_kind)
                source_run_id = source_run_by_kind[source_kind]
                source_counts[source_run_id] = source_counts.get(source_run_id, 0) + 1
                evidence_resume_id = (
                    _safe_candidate_text(getattr(evidence, "candidate_resume_id", None), 128) or canonical_resume_id
                )
                evidence_id = _safe_candidate_text(getattr(evidence, "evidence_id", None), 256) or _stable_id(
                    "evidence",
                    source_run_id,
                    identity_id,
                    source_kind,
                )
                provider_candidate_key_hash = _safe_candidate_text(
                    getattr(evidence, "provider_candidate_key_hash", None),
                    256,
                ) or _sha256_text(evidence_resume_id)
                candidate_evidence_rows.append(
                    (
                        evidence_id,
                        review_item_id,
                        DEFAULT_TENANT_ID,
                        context.session.workspace_id,
                        context.session.owner_user_id,
                        context.session.session_id,
                        source_run_id,
                        source_kind,
                        _safe_candidate_text(getattr(evidence, "evidence_level", None), 32) or "final",
                        provider_candidate_key_hash,
                        identity_id,
                        _stable_id("candidate", context.session.session_id, evidence_resume_id),
                        score,
                        fit_bucket,
                        _json_list(_safe_list(_attr(finalizer_candidate, "matched_must_haves"), 20, 240)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "matched_preferences"), 20, 240)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "risk_flags"), 12, 300)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "strengths"), 12, 300)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "weaknesses"), 12, 300)),
                        now,
                    )
                )
                evidence_review_item_by_id[evidence_id] = review_item_id
                evidence_provider_hash_by_id[evidence_id] = provider_candidate_key_hash
        if identity_snapshot_rows:
            conn.executemany(
                """
                INSERT INTO runtime_candidate_identity_snapshots (
                    session_id, runtime_run_id, identity_id, canonical_resume_id,
                    merged_resume_ids_json, source_evidence_ids_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, runtime_run_id, identity_id) DO UPDATE SET
                    canonical_resume_id = excluded.canonical_resume_id,
                    merged_resume_ids_json = excluded.merged_resume_ids_json,
                    source_evidence_ids_json = excluded.source_evidence_ids_json
                """,
                identity_snapshot_rows,
            )
        if review_item_rows:
            conn.executemany(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, summary,
                    aggregate_score, fit_bucket, why_selected, source_round, review_status, note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
                ON CONFLICT(review_item_id) DO UPDATE SET
                    primary_evidence_id = excluded.primary_evidence_id,
                    display_name = excluded.display_name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    summary = excluded.summary,
                    aggregate_score = excluded.aggregate_score,
                    fit_bucket = excluded.fit_bucket,
                    why_selected = excluded.why_selected,
                    source_round = excluded.source_round,
                    updated_at = excluded.updated_at
                """,
                review_item_rows,
            )
        if candidate_evidence_rows:
            conn.executemany(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    source_run_id = excluded.source_run_id,
                    source_kind = excluded.source_kind,
                    evidence_level = excluded.evidence_level,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    runtime_identity_id = excluded.runtime_identity_id,
                    resume_id = excluded.resume_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    missing_risks_json = excluded.missing_risks_json,
                    strengths_json = excluded.strengths_json,
                    weaknesses_json = excluded.weaknesses_json
                """,
                candidate_evidence_rows,
            )
        if write_runtime_source_lane_events:
            self._persist_runtime_source_lane_events_conn(
                conn,
                context=context,
                run_state=run_state,
                runtime_run_id=runtime_run_id,
                revision=revision,
                ordered_identity_ids=ordered_identity_ids,
                source_run_by_kind=source_run_by_kind,
                source_counts=source_counts,
            )
        if write_detail_recommendations:
            self._persist_runtime_liepin_detail_recommendations_conn(
                conn,
                context=context,
                run_state=run_state,
                source_run_by_kind=source_run_by_kind,
                candidate_store=candidate_store,
                normalized_store=normalized_store,
                evidence_review_item_by_id=evidence_review_item_by_id,
                evidence_provider_hash_by_id=evidence_provider_hash_by_id,
                now=now,
            )
        return {source_run_id: count for source_run_id, count in source_counts.items() if count}

    def _persist_runtime_source_lane_events_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        run_state: object,
        runtime_run_id: str,
        revision: int,
        ordered_identity_ids: list[str],
        source_run_by_kind: Mapping[str, str],
        source_counts: Mapping[str, int],
    ) -> None:
        coverage_payload = _runtime_coverage_summary_payload(run_state)
        finalization_payload = {
            "revision": revision,
            "reason_code": "runtime_finalized",
            "candidate_identity_ids": ordered_identity_ids[:10],
        }
        seen_sources: set[str] = set()
        for result_payload in _runtime_source_lane_result_payloads(run_state):
            source_kind = _safe_candidate_text(result_payload.get("source"), 32)
            if source_kind not in source_run_by_kind:
                continue
            seen_sources.add(source_kind)
            events = _runtime_source_lane_events_from_result_payload(result_payload)
            for event_payload in events:
                payload = _augment_runtime_source_lane_event_payload(
                    event_payload,
                    result_payload=result_payload,
                    coverage_payload=coverage_payload,
                    finalization_payload=finalization_payload,
                    runtime_run_id=runtime_run_id,
                    source_kind=source_kind,
                )
                _append_runtime_source_lane_event_conn(
                    conn,
                    tenant_id=DEFAULT_TENANT_ID,
                    workspace_id=context.session.workspace_id,
                    user_id=context.session.owner_user_id,
                    session_id=context.session.session_id,
                    source_run_id=source_run_by_kind[source_kind],
                    source_kind=cast(Literal["cts", "liepin"], source_kind),
                    event_name=_runtime_source_lane_event_name(payload),
                    schema_version=str(payload.get("schema_version") or "runtime_source_lane_event_v1"),
                    idempotency_key=_runtime_source_lane_event_idempotency_key(payload),
                    payload=payload,
                )
        for source_kind, source_run_id in source_run_by_kind.items():
            if source_kind in seen_sources:
                continue
            count = int(source_counts.get(source_run_id, 0))
            if count <= 0:
                continue
            payload: dict[str, object] = {
                "schema_version": "runtime_source_lane_event_v1",
                "runtime_run_id": runtime_run_id,
                "source_plan_id": f"{runtime_run_id}:workbench:{source_kind}",
                "source_lane_run_id": f"{runtime_run_id}:workbench:{source_kind}",
                "source": source_kind,
                "attempt": 1,
                "event_seq": 1,
                "event_type": "source_lane_completed",
                "status": "completed",
                "safe_counts": {"cards_seen": count, "candidates": count},
                "source_coverage_summary": coverage_payload,
                "finalization_revision": finalization_payload,
            }
            _append_runtime_source_lane_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=source_run_id,
                source_kind=cast(Literal["cts", "liepin"], source_kind),
                event_name="runtime_source_lane_completed",
                schema_version="runtime_source_lane_event_v1",
                idempotency_key=_runtime_source_lane_event_idempotency_key(payload),
                payload=payload,
            )

    def _persist_runtime_liepin_detail_recommendations_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        run_state: object,
        source_run_by_kind: Mapping[str, str],
        candidate_store: Mapping[object, object],
        normalized_store: Mapping[object, object],
        evidence_review_item_by_id: Mapping[str, str],
        evidence_provider_hash_by_id: Mapping[str, str],
        now: str,
    ) -> None:
        liepin_source_run_id = source_run_by_kind.get("liepin")
        if not liepin_source_run_id:
            return
        connection = _connected_liepin_connection_for_owner_conn(
            conn,
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
        )
        if connection is None:
            return
        policy = _source_run_policy_from_row(
            _source_run_policy_row_conn(
                conn,
                user=WorkbenchUser(
                    user_id=context.session.owner_user_id,
                    email="",
                    display_name="",
                    role="member",
                    workspace_id=context.session.workspace_id,
                ),
                session_id=context.session.session_id,
            ),
            session_id=context.session.session_id,
        )
        projection_context = WorkbenchSourceRunJobContext(
            job=WorkbenchSourceRunJob(
                job_id=context.job.job_id,
                source_run_id=liepin_source_run_id,
                session_id=context.session.session_id,
                source_kind="liepin",
                status="running",
                attempt_count=context.job.attempt_count,
                error_message=None,
                created_at=context.job.created_at,
                updated_at=context.job.updated_at,
            ),
            session=context.session,
            requirement_review=context.requirement_review,
        )
        created_count = 0
        for recommendation in _runtime_detail_recommendation_payloads(run_state):
            if created_count >= LIEPIN_AUTO_DETAIL_REQUEST_LIMIT:
                return
            source_evidence_id = _safe_candidate_text(recommendation.get("source_evidence_id"), 256)
            if not source_evidence_id:
                continue
            review_item_id = evidence_review_item_by_id.get(source_evidence_id)
            provider_key_hash = (
                _safe_candidate_text(recommendation.get("provider_candidate_key_hash"), 256)
                or evidence_provider_hash_by_id.get(source_evidence_id)
            )
            if not review_item_id:
                materialized = _ensure_runtime_liepin_recommended_card_review_item_conn(
                    conn,
                    context=context,
                    run_state=run_state,
                    source_run_id=liepin_source_run_id,
                    candidate_store=candidate_store,
                    normalized_store=normalized_store,
                    recommendation=recommendation,
                    source_evidence_id=source_evidence_id,
                    provider_key_hash=provider_key_hash,
                    now=now,
                )
                if materialized is None:
                    continue
                review_item_id, provider_key_hash = materialized
            if not provider_key_hash:
                continue
            auto_request_id = _create_auto_liepin_detail_open_request_conn(
                conn,
                context=projection_context,
                connection_id=str(connection["connection_id"]),
                evidence_id=source_evidence_id,
                review_item_id=review_item_id,
                provider_key_hash=provider_key_hash,
                policy=policy,
                decision_note=_runtime_detail_recommendation_note(recommendation),
                detail_candidates_json=_detail_candidates_json_from_runtime_recommendation(recommendation),
                now=now,
            )
            if auto_request_id is None:
                continue
            created_count += 1
            if policy.detail_open_mode == "bypass_confirm":
                self._lease_liepin_detail_open_request_conn(conn, request_id=auto_request_id, now=now)

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
        runtime_identity_by_resume_id = _runtime_identity_by_resume_id_from_artifacts(artifacts)
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
            why_selected = _safe_candidate_text(_attr(candidate, "why_selected"), 1000)
            summary = _safe_candidate_text(_attr(candidate, "match_summary"), 1000) or why_selected or ""
            score = _int_or_none(_attr(candidate, "final_score"))
            fit_bucket = _safe_candidate_text(_attr(candidate, "fit_bucket"), 64)
            source_round = _int_or_none(_attr(candidate, "source_round"))
            matched_must_haves = _safe_list(_attr(candidate, "matched_must_haves"), 20, 240)
            matched_preferences = _safe_list(_attr(candidate, "matched_preferences"), 20, 240)
            strengths = _safe_list(_attr(candidate, "strengths"), 12, 300)
            weaknesses = _safe_list(_attr(candidate, "weaknesses"), 12, 300)
            risk_flags = _safe_list(_attr(candidate, "risk_flags"), 12, 300)
            missing_risks = risk_flags
            provider_key_hash = _sha256_text(
                _safe_candidate_text(_attr(raw_candidate, "source_resume_id"), 256) or provider_resume_id
            )
            runtime_identity_id = runtime_identity_by_resume_id.get(provider_resume_id)
            conn.execute(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, summary,
                    aggregate_score, fit_bucket, why_selected, source_round, review_status, note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
                ON CONFLICT(review_item_id) DO UPDATE SET
                    primary_evidence_id = excluded.primary_evidence_id,
                    display_name = excluded.display_name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    summary = excluded.summary,
                    aggregate_score = excluded.aggregate_score,
                    fit_bucket = excluded.fit_bucket,
                    why_selected = excluded.why_selected,
                    source_round = excluded.source_round,
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
                    why_selected,
                    source_round,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    runtime_identity_id = excluded.runtime_identity_id,
                    resume_id = excluded.resume_id,
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
                    runtime_identity_id,
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
        candidates = _object_list(_attr(result, "candidates"))
        if not candidates:
            candidate_updates = _attr(result, "candidate_store_updates")
            if isinstance(candidate_updates, Mapping):
                candidates = list(candidate_updates.values())
        snapshots = _object_list(_attr(result, "provider_snapshots"))
        runtime_recommendations = _object_list(_attr(result, "detail_recommendations"))
        runtime_recommendation_by_provider_resume_id = {
            _safe_candidate_text(_attr(item, "candidate_resume_id"), 128): item
            for item in runtime_recommendations
            if _safe_candidate_text(_attr(item, "candidate_resume_id"), 128)
        }
        uses_runtime_detail_recommendations = hasattr(result, "source_evidence_updates") and hasattr(
            result, "detail_recommendations"
        )
        review_item_ids: list[str] = []
        policy = _source_run_policy_from_row(
            _source_run_policy_row_conn(
                conn,
                user=WorkbenchUser(
                    user_id=context.session.owner_user_id,
                    email="",
                    display_name="",
                    role="member",
                    workspace_id=context.session.workspace_id,
                ),
                session_id=context.session.session_id,
            ),
            session_id=context.session.session_id,
        )
        connection = _connected_liepin_connection_for_owner_conn(
            conn,
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
        )
        auto_detail_request_count = 0
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
            card_text = " ".join([display_name, title, company, location, summary])
            sheet = _requirement_sheet_for_projection(context)
            matched_must_haves = _matched_terms(sheet.must_have_capabilities, card_text)
            matched_preferences = _matched_terms(sheet.preferred_capabilities, card_text)
            strengths = _unique_list([*matched_must_haves[:6], *matched_preferences[:6]])
            auto_score, auto_reason = _liepin_card_auto_detail_decision(
                matched_must_haves=matched_must_haves,
                matched_preferences=matched_preferences,
                title=title,
                summary=summary,
            )
            should_request_detail = (
                connection is not None
                and auto_detail_request_count < LIEPIN_AUTO_DETAIL_REQUEST_LIMIT
                and auto_score >= LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD
            )
            runtime_recommendation = runtime_recommendation_by_provider_resume_id.get(provider_resume_id)
            if uses_runtime_detail_recommendations:
                should_request_detail = (
                    connection is not None
                    and runtime_recommendation is not None
                    and auto_detail_request_count < LIEPIN_AUTO_DETAIL_REQUEST_LIMIT
                )
                if runtime_recommendation is not None:
                    recommendation_score = _int_or_none(_attr(runtime_recommendation, "value_score"))
                    auto_score = recommendation_score if recommendation_score is not None else auto_score
                    auto_reason = (
                        _safe_candidate_text(_attr(runtime_recommendation, "safe_reason"), 500)
                        or _safe_candidate_text(_attr(runtime_recommendation, "reason_code"), 500)
                        or auto_reason
                    )
            missing_risks = ["Detail page not opened yet."]
            if should_request_detail:
                missing_risks.append("Agent recommends detail review before final outreach.")
            provider_key_hash = _sha256_text(provider_key)
            runtime_identity_id = None
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
                    auto_score,
                    "card_recommended" if should_request_detail else "card",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'card', ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    runtime_identity_id = excluded.runtime_identity_id,
                    resume_id = excluded.resume_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
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
                    runtime_identity_id,
                    workbench_resume_id,
                    auto_score,
                    "card_recommended" if should_request_detail else "card",
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
                    "autoDetailScore": auto_score,
                    "autoDetailRecommended": should_request_detail,
                },
            )
            if should_request_detail and connection is not None:
                auto_request_id = _create_auto_liepin_detail_open_request_conn(
                    conn,
                    context=context,
                    connection_id=connection["connection_id"],
                    evidence_id=evidence_id,
                    review_item_id=review_item_id,
                    provider_key_hash=provider_key_hash,
                    policy=policy,
                    decision_note=auto_reason,
                    detail_candidates_json=_detail_candidates_json(
                        candidate_id=provider_resume_id,
                        provider_candidate_key_hash=provider_key_hash,
                        value_score=auto_score,
                    ),
                    now=now,
                )
                if auto_request_id is not None:
                    auto_detail_request_count += 1
                    if policy.detail_open_mode == "bypass_confirm":
                        self._lease_liepin_detail_open_request_conn(conn, request_id=auto_request_id, now=now)
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

    def get_liepin_source_run_policy(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchSourceRunPolicy | None:
        self._initialize()
        with self._connect() as conn:
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            row = _source_run_policy_row_conn(conn, user=user, session_id=session_id)
        return _source_run_policy_from_row(row, session_id=session_id)

    def update_liepin_source_run_policy(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        detail_open_mode: DetailOpenMode,
    ) -> WorkbenchSourceRunPolicy | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _session_exists_for_user(conn, user=user, session_id=session_id):
                return None
            conn.execute(
                """
                INSERT INTO source_run_policies (
                    session_id, tenant_id, workspace_id, user_id, source_kind, detail_open_mode, updated_at
                )
                VALUES (?, ?, ?, ?, 'liepin', ?, ?)
                ON CONFLICT(session_id, source_kind) DO UPDATE SET
                    detail_open_mode = excluded.detail_open_mode,
                    updated_at = excluded.updated_at
                """,
                (session_id, DEFAULT_TENANT_ID, user.workspace_id, user.user_id, detail_open_mode, now),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind="liepin",
                event_name="liepin_detail_policy_updated",
                payload={"sessionId": session_id, "sourceKind": "liepin", "detailOpenMode": detail_open_mode},
            )
            _append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="source_run_policy",
                target_id=session_id,
                action="liepin_detail_policy_updated",
                result="success",
                reason_code=detail_open_mode,
                metadata={"sessionId": session_id, "sourceKind": "liepin", "detailOpenMode": detail_open_mode},
                created_at=now,
            )
            row = _source_run_policy_row_conn(conn, user=user, session_id=session_id)
        return _source_run_policy_from_row(row, session_id=session_id)

    def create_liepin_detail_open_request(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
        idempotency_key: str | None,
    ) -> WorkbenchDetailOpenRequest | None:
        self._initialize()
        self.reconcile_expired_detail_open_leases()
        now = _now_iso()
        blocked_reason: str | None = None
        request_id: str | None = None
        safe_idempotency_key = _detail_idempotency_key(
            session_id=session_id,
            review_item_id=review_item_id,
            idempotency_key=idempotency_key,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = _liepin_review_target_conn(conn, user=user, session_id=session_id, review_item_id=review_item_id)
            if target is None:
                return None
            if target["evidence_level"] == "detail":
                raise PermissionError("detail_open_not_required")
            existing = conn.execute(
                """
                SELECT *
                FROM detail_open_requests
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND idempotency_key = ?
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, safe_idempotency_key),
            ).fetchone()
            if existing is not None:
                return _detail_open_request_from_row_conn(conn, existing)
            policy = _source_run_policy_from_row(
                _source_run_policy_row_conn(conn, user=user, session_id=session_id),
                session_id=session_id,
            )
            connection = _connected_liepin_connection_conn(conn, user=user)
            if connection is None:
                raise PermissionError("liepin_connection_not_connected")
            request_id = f"dor_{uuid.uuid4().hex[:16]}"
            status: DetailOpenRequestStatus = "pending"
            if policy.detail_open_mode == "bypass_confirm":
                status = "bypassed"
            decision_note = "Manual detail request from workbench."
            detail_candidates_json = _detail_candidates_json(
                candidate_id=_safe_candidate_text(target["resume_id"], 256)
                or _safe_candidate_text(target["provider_candidate_key_hash"], 256)
                or review_item_id,
                provider_candidate_key_hash=_safe_candidate_text(target["provider_candidate_key_hash"], 256),
                value_score=None,
            )
            conn.execute(
                """
                INSERT INTO detail_open_requests (
                    request_id, tenant_id, workspace_id, user_id, session_id, source_run_id, connection_id,
                    candidate_evidence_id, review_item_id, provider_candidate_key_hash,
                    detail_candidates_json, detail_open_mode, status, idempotency_key, blocked_reason, decision_note,
                    ledger_id, decided_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?)
                """,
                (
                    request_id,
                    DEFAULT_TENANT_ID,
                    user.workspace_id,
                    user.user_id,
                    session_id,
                    target["source_run_id"],
                    connection["connection_id"],
                    target["evidence_id"],
                    review_item_id,
                    target["provider_candidate_key_hash"],
                    detail_candidates_json,
                    policy.detail_open_mode,
                    status,
                    safe_idempotency_key,
                    decision_note,
                    now if status == "bypassed" else None,
                    now,
                    now,
                ),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=target["source_run_id"],
                source_kind="liepin",
                event_name="liepin_detail_open_requested",
                payload={
                    "requestId": request_id,
                    "reviewItemId": review_item_id,
                    "status": status,
                    "detailOpenMode": policy.detail_open_mode,
                },
            )
            _append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="detail_open_request",
                target_id=request_id,
                action="liepin_detail_open_requested",
                result=status,
                reason_code=policy.detail_open_mode,
                metadata={
                    "sessionId": session_id,
                    "sourceRunId": target["source_run_id"],
                    "reviewItemId": review_item_id,
                    "detailOpenMode": policy.detail_open_mode,
                },
                created_at=now,
            )
            if status == "bypassed":
                blocked_reason = self._lease_liepin_detail_open_request_conn(
                    conn,
                    request_id=request_id,
                    now=now,
                )
            row = conn.execute("SELECT * FROM detail_open_requests WHERE request_id = ?", (request_id,)).fetchone()
            result = _detail_open_request_from_row_conn(conn, row)
        if blocked_reason is not None:
            raise PermissionError(blocked_reason)
        return result

    def approve_liepin_detail_open_request(
        self,
        *,
        user: WorkbenchUser,
        request_id: str,
    ) -> WorkbenchDetailOpenRequest | None:
        self._initialize()
        self.reconcile_expired_detail_open_leases()
        now = _now_iso()
        blocked_reason: str | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _detail_request_row_for_user_conn(conn, user=user, request_id=request_id)
            if row is None:
                return None
            if row["status"] != "pending":
                raise PermissionError("detail_open_request_not_approvable")
            conn.execute(
                """
                UPDATE detail_open_requests
                SET status = 'approved', decided_at = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (now, now, request_id),
            )
            _append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="detail_open_request",
                target_id=request_id,
                action="liepin_detail_open_approved",
                result="approved",
                reason_code="human_confirm",
                metadata={"sessionId": row["session_id"], "sourceRunId": row["source_run_id"]},
                created_at=now,
            )
            blocked_reason = self._lease_liepin_detail_open_request_conn(conn, request_id=request_id, now=now)
            refreshed = conn.execute("SELECT * FROM detail_open_requests WHERE request_id = ?", (request_id,)).fetchone()
            result = _detail_open_request_from_row_conn(conn, refreshed)
        if blocked_reason is not None:
            raise PermissionError(blocked_reason)
        return result

    def reject_liepin_detail_open_request(
        self,
        *,
        user: WorkbenchUser,
        request_id: str,
        reason: str,
    ) -> WorkbenchDetailOpenRequest | None:
        self._initialize()
        now = _now_iso()
        safe_reason = _safe_candidate_text(reason, 500) or ""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _detail_request_row_for_user_conn(conn, user=user, request_id=request_id)
            if row is None:
                return None
            if row["status"] != "pending":
                raise PermissionError("detail_open_request_not_rejectable")
            conn.execute(
                """
                UPDATE detail_open_requests
                SET status = 'rejected', decision_note = ?, decided_at = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (safe_reason, now, now, request_id),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=row["session_id"],
                source_run_id=row["source_run_id"],
                source_kind="liepin",
                event_name="liepin_detail_open_rejected",
                payload={"requestId": request_id, "reviewItemId": row["review_item_id"]},
            )
            _append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="detail_open_request",
                target_id=request_id,
                action="liepin_detail_open_rejected",
                result="success",
                reason_code="human_rejected",
                metadata={"sessionId": row["session_id"], "sourceRunId": row["source_run_id"]},
                created_at=now,
            )
            refreshed = conn.execute("SELECT * FROM detail_open_requests WHERE request_id = ?", (request_id,)).fetchone()
            return _detail_open_request_from_row_conn(conn, refreshed)

    def list_liepin_detail_open_requests(
        self,
        *,
        user: WorkbenchUser,
        session_id: str | None = None,
        status: DetailOpenRequestStatus | None = None,
        limit: int = 100,
    ) -> list[WorkbenchDetailOpenRequest]:
        self._initialize()
        self.reconcile_expired_detail_open_leases()
        safe_limit = min(max(limit, 1), 200)
        filters = ["tenant_id = ?", "workspace_id = ?", "user_id = ?"]
        params: list[object] = [DEFAULT_TENANT_ID, user.workspace_id, user.user_id]
        if session_id is not None:
            filters.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            filters.append("status = ?")
            params.append(status)
        params.append(safe_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM detail_open_requests
                WHERE {" AND ".join(filters)}
                ORDER BY CASE status
                            WHEN 'pending' THEN 0
                            WHEN 'blocked' THEN 1
                            WHEN 'approved' THEN 2
                            WHEN 'bypassed' THEN 2
                            ELSE 3
                         END,
                         created_at DESC,
                         request_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_detail_open_request_from_row_conn(conn, row) for row in rows]

    def claim_next_liepin_detail_open_intent(self) -> WorkbenchLiepinDetailOpenJobContext | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            intent = conn.execute(
                """
                SELECT *
                FROM external_write_intents
                WHERE target_kind = 'liepin_detail_attempt'
                  AND status = 'pending'
                ORDER BY updated_at ASC, intent_id ASC
                LIMIT 1
                """
            ).fetchone()
            if intent is None:
                return None
            cursor = conn.execute(
                """
                UPDATE external_write_intents
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE intent_id = ? AND status = 'pending'
                """,
                (now, intent["intent_id"]),
            )
            if cursor.rowcount <= 0:
                return None
            target_scope = _json_to_dict(intent["target_scope_json"])
            request_id = _safe_candidate_text(target_scope.get("requestId"), 128)
            ledger_id = _safe_candidate_text(target_scope.get("ledgerId"), 128)
            if not request_id or not ledger_id:
                _fail_external_write_intent_conn(
                    conn,
                    intent_id=intent["intent_id"],
                    error_code="malformed_detail_open_intent",
                    error_message="Detail-open intent is missing request or ledger.",
                    now=now,
                )
                return None
            request_row = conn.execute("SELECT * FROM detail_open_requests WHERE request_id = ?", (request_id,)).fetchone()
            ledger_row = conn.execute("SELECT * FROM detail_open_ledger WHERE ledger_id = ?", (ledger_id,)).fetchone()
            if request_row is None or ledger_row is None or ledger_row["status"] != "leased":
                _fail_external_write_intent_conn(
                    conn,
                    intent_id=intent["intent_id"],
                    error_code="detail_open_lease_not_available",
                    error_message="Detail-open lease is not available.",
                    now=now,
                )
                return None
            connection = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE connection_id = ?
                  AND workspace_id = ?
                  AND user_id = ?
                  AND source_kind = 'liepin'
                  AND status = 'connected'
                """,
                (request_row["connection_id"], request_row["workspace_id"], request_row["user_id"]),
            ).fetchone()
            if connection is None or not connection["provider_account_hash"] or not connection["compliance_gate_ref"]:
                _fail_external_write_intent_conn(
                    conn,
                    intent_id=intent["intent_id"],
                    error_code="liepin_connection_not_connected",
                    error_message="Liepin connection is not ready for detail open.",
                    now=now,
                )
                return None
            evidence_row = conn.execute(
                "SELECT * FROM candidate_evidence WHERE evidence_id = ?",
                (request_row["candidate_evidence_id"],),
            ).fetchone()
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (intent["session_id"],)).fetchone()
            if session_row is None:
                _fail_external_write_intent_conn(
                    conn,
                    intent_id=intent["intent_id"],
                    error_code="session_not_found",
                    error_message="Workbench session was not found.",
                    now=now,
                )
                return None
            source_runs = _source_runs_by_session(conn, [intent["session_id"]]).get(intent["session_id"], [])
            requirement_review = _requirement_reviews_by_session(conn, [intent["session_id"]])[intent["session_id"]]
            detail_candidates_json = _safe_candidate_text(request_row["detail_candidates_json"], 4000)
            if not detail_candidates_json:
                fallback_candidate_id = (
                    (_safe_candidate_text(evidence_row["resume_id"], 256) if evidence_row is not None else None)
                    or _safe_candidate_text(request_row["review_item_id"], 256)
                    or "liepin-candidate"
                )
                detail_candidates_json = _detail_candidates_json(
                    candidate_id=fallback_candidate_id,
                    provider_candidate_key_hash=request_row["provider_candidate_key_hash"],
                    value_score=None,
                )
            source_run_row = conn.execute(
                "SELECT runtime_run_id FROM source_runs WHERE source_run_id = ?",
                (intent["source_run_id"],),
            ).fetchone()
            runtime_run_id = source_run_row["runtime_run_id"] if source_run_row is not None else None
            context = WorkbenchLiepinDetailOpenJobContext(
                intent_id=intent["intent_id"],
                idempotency_key=intent["idempotency_key"],
                request_id=request_row["request_id"],
                ledger_id=ledger_row["ledger_id"],
                review_item_id=request_row["review_item_id"],
                candidate_evidence_id=request_row["candidate_evidence_id"],
                candidate_resume_id=evidence_row["resume_id"] if evidence_row is not None else None,
                provider_candidate_key_hash=request_row["provider_candidate_key_hash"],
                connection_id=request_row["connection_id"],
                compliance_gate_ref=connection["compliance_gate_ref"],
                provider_account_hash=connection["provider_account_hash"],
                detail_candidates_json=detail_candidates_json,
                budget_day=ledger_row["budget_day"],
                lease_expires_at=ledger_row["lease_expires_at"],
                source_run_id=intent["source_run_id"],
                runtime_run_id=runtime_run_id,
                session=_session_from_row(session_row, source_runs, requirement_review),
                requirement_review=requirement_review,
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=intent["tenant_id"],
                workspace_id=intent["workspace_id"],
                user_id=intent["user_id"],
                session_id=intent["session_id"],
                source_run_id=intent["source_run_id"],
                source_kind="liepin",
                event_name="liepin_detail_open_execution_started",
                payload={"requestId": request_id, "ledgerId": ledger_id},
            )
            return context

    def complete_liepin_detail_open_intent_with_lane_result(
        self,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        result: object,
    ) -> None:
        self._initialize()
        now = _now_iso()
        status = _safe_candidate_text(_attr(result, "status"), 64) or "failed"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            evidence_ids = self._persist_liepin_detail_candidate_results_conn(
                conn,
                context=context,
                result=result,
                now=now,
            )
            success = status == "completed" and bool(evidence_ids)
            ledger_status = "opened" if success else "maybe_used" if status == "partial" else "failed"
            conn.execute(
                """
                UPDATE detail_open_ledger
                SET status = ?, opened_at = CASE WHEN ? = 'opened' THEN ? ELSE opened_at END, updated_at = ?
                WHERE ledger_id = ?
                """,
                (ledger_status, ledger_status, now, now, context.ledger_id),
            )
            if success:
                conn.execute(
                    """
                    UPDATE external_write_intents
                    SET status = 'succeeded',
                        resolved_external_ref = ?,
                        last_error_code = NULL,
                        last_error_message = NULL,
                        updated_at = ?
                    WHERE intent_id = ?
                    """,
                    (evidence_ids[0], now, context.intent_id),
                )
            else:
                _fail_external_write_intent_conn(
                    conn,
                    intent_id=context.intent_id,
                    error_code=_safe_candidate_text(_attr(result, "blocked_reason_code"), 128)
                    or _safe_candidate_text(_attr(result, "stop_reason_code"), 128)
                    or "liepin_detail_open_failed",
                    error_message=_safe_candidate_text(_attr(result, "safe_error_summary"), 500)
                    or "Liepin detail open did not return detail evidence.",
                    now=now,
                )
            for event in _object_list(_attr(result, "events")):
                event_payload = _runtime_source_lane_event_payload(event)
                if event_payload is None:
                    continue
                event_type = str(event_payload["event_type"])
                _append_runtime_source_lane_event_conn(
                    conn,
                    tenant_id=DEFAULT_TENANT_ID,
                    workspace_id=context.session.workspace_id,
                    user_id=context.session.owner_user_id,
                    session_id=context.session.session_id,
                    source_run_id=context.source_run_id,
                    source_kind="liepin",
                    event_name=f"runtime_{event_type}",
                    schema_version=str(event_payload["schema_version"]),
                    idempotency_key=(
                        f"{event_payload['source_lane_run_id']}:{event_payload['attempt']}:{event_payload['event_seq']}"
                    ),
                    payload=event_payload,
                )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.source_run_id,
                source_kind="liepin",
                event_name="liepin_detail_open_completed" if success else "liepin_detail_open_failed",
                payload={
                    "requestId": context.request_id,
                    "ledgerId": context.ledger_id,
                    "detailEvidenceCount": len(evidence_ids),
                },
            )

    def _persist_liepin_detail_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        result: object,
        now: str,
    ) -> list[str]:
        evidence_updates = [
            evidence
            for evidence in _object_list(_attr(result, "source_evidence_updates"))
            if _safe_candidate_text(_attr(evidence, "source"), 32) == "liepin"
            and _safe_candidate_text(_attr(evidence, "evidence_level"), 32) == "detail"
        ]
        if not evidence_updates:
            return []
        candidate_updates = _attr(result, "candidate_store_updates")
        candidate_by_resume_id: dict[str, object] = {}
        if isinstance(candidate_updates, Mapping):
            candidate_by_resume_id = {
                str(key): value for key, value in cast(Mapping[object, object], candidate_updates).items()
            }
            for candidate in candidate_updates.values():
                candidate_resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 256)
                if candidate_resume_id:
                    candidate_by_resume_id[candidate_resume_id] = candidate
        existing = conn.execute(
            "SELECT * FROM candidate_review_items WHERE review_item_id = ?",
            (context.review_item_id,),
        ).fetchone()
        if existing is None:
            return []
        persisted: list[str] = []
        for index, evidence in enumerate(evidence_updates, start=1):
            evidence_resume_id = (
                _safe_candidate_text(_attr(evidence, "candidate_resume_id"), 256)
                or context.candidate_resume_id
                or context.review_item_id
            )
            candidate = candidate_by_resume_id.get(evidence_resume_id)
            raw = _attr(candidate, "raw")
            display_name = (
                _safe_candidate_text(_attr(raw, "candidate_name"), 160)
                or _safe_candidate_text(_attr(raw, "name"), 160)
                or existing["display_name"]
            )
            title = (
                _safe_candidate_text(_attr(raw, "current_title"), 240)
                or _safe_candidate_text(_attr(candidate, "expected_job_category"), 240)
                or existing["title"]
            )
            company = _safe_candidate_text(_attr(raw, "current_company"), 240) or existing["company"]
            location = _safe_candidate_text(_attr(candidate, "now_location"), 160) or existing["location"]
            summary = _safe_candidate_text(_attr(candidate, "search_text"), 1000) or existing["summary"]
            detail_text = " ".join([display_name, title, company, location, summary])
            sheet = _requirement_sheet_for_projection(context)
            matched_must_haves = _matched_terms(sheet.must_have_capabilities, detail_text)
            matched_preferences = _matched_terms(sheet.preferred_capabilities, detail_text)
            strengths = _unique_list([*matched_must_haves[:6], *matched_preferences[:6]])
            evidence_id = (
                _safe_candidate_text(_attr(evidence, "evidence_id"), 256)
                or _stable_id("evidence", context.source_run_id, evidence_resume_id, "detail", str(index))
            )
            provider_candidate_key_hash = (
                _safe_candidate_text(_attr(evidence, "provider_candidate_key_hash"), 256)
                or context.provider_candidate_key_hash
            )
            score = _int_or_none(_attr(evidence, "score_hint")) or existing["aggregate_score"]
            fit_bucket = _safe_candidate_text(_attr(evidence, "fit_bucket"), 64) or existing["fit_bucket"] or "detail"
            conn.execute(
                """
                UPDATE candidate_review_items
                SET primary_evidence_id = ?,
                    display_name = ?,
                    title = ?,
                    company = ?,
                    location = ?,
                    summary = ?,
                    aggregate_score = ?,
                    fit_bucket = ?,
                    updated_at = ?
                WHERE review_item_id = ?
                """,
                (
                    evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    summary,
                    score,
                    fit_bucket,
                    now,
                    context.review_item_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'detail', ?, NULL, ?, ?, ?, ?, ?, '[]', ?, '[]', ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    resume_id = excluded.resume_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    strengths_json = excluded.strengths_json
                """,
                (
                    evidence_id,
                    context.review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    context.source_run_id,
                    provider_candidate_key_hash,
                    _stable_id("candidate", context.session.session_id, evidence_resume_id),
                    score,
                    fit_bucket,
                    _json_list(matched_must_haves),
                    _json_list(matched_preferences),
                    _json_list(strengths),
                    now,
                ),
            )
            persisted.append(evidence_id)
        return persisted

    def fail_liepin_detail_open_intent(
        self,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        error_code: str,
        error_message: str,
    ) -> None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _fail_external_write_intent_conn(
                conn,
                intent_id=context.intent_id,
                error_code=_safe_candidate_text(error_code, 128) or "liepin_detail_open_failed",
                error_message=_safe_candidate_text(error_message, 500) or "Liepin detail open failed.",
                now=now,
            )
            conn.execute(
                """
                UPDATE detail_open_ledger
                SET status = 'failed', updated_at = ?
                WHERE ledger_id = ? AND status = 'leased'
                """,
                (now, context.ledger_id),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.source_run_id,
                source_kind="liepin",
                event_name="liepin_detail_open_failed",
                payload={"requestId": context.request_id, "ledgerId": context.ledger_id},
            )

    def build_liepin_provider_open_action(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
    ) -> WorkbenchProviderAction | None:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = _liepin_review_target_conn(conn, user=user, session_id=session_id, review_item_id=review_item_id)
            if target is None:
                return None
            connection = _connected_liepin_connection_conn(conn, user=user)
            if connection is None:
                raise PermissionError("liepin_connection_not_connected")
            budget_impact: Literal["none", "reserved"] = "none"
            if target["evidence_level"] != "detail":
                ledger = _reusable_detail_ledger_for_review_conn(
                    conn,
                    user=user,
                    session_id=session_id,
                    review_item_id=review_item_id,
                )
                if ledger is None:
                    raise PermissionError("detail_open_required")
                budget_impact = "reserved"
            action = _provider_action(
                connection_id=connection["connection_id"],
                review_item_id=review_item_id,
                budget_impact=budget_impact,
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=target["source_run_id"],
                source_kind="liepin",
                event_name="liepin_provider_action_requested",
                payload={"reviewItemId": review_item_id, "budgetImpact": budget_impact},
            )
            _append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="candidate_review_item",
                target_id=review_item_id,
                action="liepin_provider_action_requested",
                result="success",
                reason_code=budget_impact,
                metadata={"sessionId": session_id, "sourceRunId": target["source_run_id"], "budgetImpact": budget_impact},
            )
            return action

    def _lease_liepin_detail_open_request_conn(
        self,
        conn: sqlite3.Connection,
        *,
        request_id: str,
        now: str,
    ) -> str | None:
        row = conn.execute("SELECT * FROM detail_open_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            return "detail_open_request_not_found"
        active = conn.execute(
            """
            SELECT 1
            FROM detail_open_ledger
            WHERE connection_id = ?
              AND status = 'leased'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at > ?
            LIMIT 1
            """,
            (row["connection_id"], now),
        ).fetchone()
        if active is not None:
            _block_detail_open_request_conn(conn, row=row, reason="active_detail_open_lease", now=now)
            return "active_detail_open_lease"
        budget_day = _budget_day(now)
        budget_row = conn.execute(
            """
            SELECT COUNT(*) AS used_count
            FROM detail_open_ledger
            WHERE connection_id = ?
              AND budget_day = ?
              AND status IN ('leased', 'opened', 'maybe_used')
            """,
            (row["connection_id"], budget_day),
        ).fetchone()
        if int(budget_row["used_count"]) >= LIEPIN_DAILY_DETAIL_OPEN_LIMIT:
            _block_detail_open_request_conn(conn, row=row, reason="detail_budget_exhausted", now=now)
            return "detail_budget_exhausted"
        ledger_id = f"dol_{uuid.uuid4().hex[:16]}"
        lease_expires_at = _iso(_parse_iso(now) + timedelta(seconds=DETAIL_OPEN_LEASE_SECONDS))
        try:
            conn.execute(
                """
                INSERT INTO detail_open_ledger (
                    ledger_id, tenant_id, workspace_id, actor_id, connection_id, source_run_id,
                    request_id, candidate_evidence_id, provider_candidate_key_hash, status,
                    budget_day, idempotency_key, lease_expires_at, opened_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'leased', ?, ?, ?, NULL, ?, ?)
                """,
                (
                    ledger_id,
                    row["tenant_id"],
                    row["workspace_id"],
                    row["user_id"],
                    row["connection_id"],
                    row["source_run_id"],
                    request_id,
                    row["candidate_evidence_id"],
                    row["provider_candidate_key_hash"],
                    budget_day,
                    row["idempotency_key"],
                    lease_expires_at,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            _block_detail_open_request_conn(conn, row=row, reason="active_detail_open_lease", now=now)
            return "active_detail_open_lease"
        conn.execute(
            """
            UPDATE detail_open_requests
            SET ledger_id = ?, blocked_reason = NULL, updated_at = ?
            WHERE request_id = ?
            """,
            (ledger_id, now, request_id),
        )
        conn.execute(
            """
            UPDATE source_runs
            SET detail_open_used_count = detail_open_used_count + 1
            WHERE source_run_id = ?
            """,
            (row["source_run_id"],),
        )
        _queue_external_write_intent_conn(
            conn,
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            source_run_id=row["source_run_id"],
            target_kind="liepin_detail_attempt",
            idempotency_key=f"liepin_detail_attempt:{row['idempotency_key']}",
            target_scope={
                "ledgerId": ledger_id,
                "requestId": request_id,
                "connectionId": row["connection_id"],
                "candidateEvidenceId": row["candidate_evidence_id"],
                "providerCandidateKeyHash": row["provider_candidate_key_hash"],
                "budgetDay": budget_day,
            },
            now=now,
        )
        _append_workbench_event_conn(
            conn,
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            source_run_id=row["source_run_id"],
            source_kind="liepin",
            event_name="liepin_detail_open_leased",
            payload={"requestId": request_id, "reviewItemId": row["review_item_id"], "budgetImpact": "reserved"},
        )
        return None

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

    def list_runtime_final_top_review_items(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> tuple[int, list[WorkbenchCandidateReviewItem]] | None:
        self._initialize()
        with self._connect() as conn:
            revision_row = conn.execute(
                """
                SELECT *
                FROM runtime_finalization_revisions
                WHERE session_id = ?
                ORDER BY revision DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if revision_row is None:
                return None
            identity_ids = _json_to_list(revision_row["ordered_candidate_identity_ids_json"])[:10]
        review_item_ids = [_stable_id("review", session_id, "identity", identity_id) for identity_id in identity_ids]
        items = self._list_candidate_review_items_by_ids(
            user=user,
            session_id=session_id,
            review_item_ids=review_item_ids,
        )
        item_by_id = {item.review_item_id: item for item in items}
        ordered_items = [item_by_id[review_item_id] for review_item_id in review_item_ids if review_item_id in item_by_id]
        return int(revision_row["revision"]), ordered_items

    def has_active_runtime_sourcing_job(self, *, user: WorkbenchUser, session_id: str) -> bool:
        self._initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runtime_sourcing_jobs
                WHERE workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                  AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (user.workspace_id, user.user_id, session_id),
            ).fetchone()
        return row is not None

    def has_runtime_sourcing_job(self, *, user: WorkbenchUser, session_id: str) -> bool:
        self._initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runtime_sourcing_jobs
                WHERE workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                LIMIT 1
                """,
                (user.workspace_id, user.user_id, session_id),
            ).fetchone()
        return row is not None

    def _initialize(self) -> None:
        if self._initialized:
            return
        with self._connect() as conn:
            initialize_workbench_schema(conn, now=_now_iso())
        self._initialized = True

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with connect_workbench_db(self.db_path) as conn:
            yield conn


def _detail_idempotency_key(*, session_id: str, review_item_id: str, idempotency_key: str | None) -> str:
    explicit_key = _bounded_text(idempotency_key, 128)
    if explicit_key:
        return f"{session_id}:{explicit_key}"
    return f"{session_id}:{review_item_id}"


def _session_exists_for_user_conn(conn: sqlite3.Connection, *, user: WorkbenchUser, session_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sessions
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND session_id = ?
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id),
    ).fetchone()
    return row is not None


def _session_exists_for_ids_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sessions
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND session_id = ?
        """,
        (tenant_id, workspace_id, user_id, session_id),
    ).fetchone()
    return row is not None


def _runtime_public_event_by_idempotency_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM session_events
        WHERE tenant_id = ?
          AND workspace_id = ?
          AND user_id = ?
          AND session_id = ?
          AND schema_version = 'runtime_public_event_v1'
          AND idempotency_key = ?
        """,
        (tenant_id, workspace_id, user_id, session_id, idempotency_key),
    ).fetchone()


def _workbench_note_event_by_idempotency_conn(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    user_id: str,
    session_id: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM session_events
        WHERE tenant_id = ?
          AND workspace_id = ?
          AND user_id = ?
          AND session_id = ?
          AND event_name = 'workbench_note_created'
          AND idempotency_key = ?
        ORDER BY global_seq ASC
        LIMIT 1
        """,
        (DEFAULT_TENANT_ID, workspace_id, user_id, session_id, idempotency_key),
    ).fetchone()


def _source_run_policy_row_conn(
    conn: sqlite3.Connection,
    *,
    user: WorkbenchUser,
    session_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM source_run_policies
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ?
          AND session_id = ? AND source_kind = 'liepin'
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id),
    ).fetchone()


def _source_run_policy_from_row(row: sqlite3.Row | None, *, session_id: str) -> WorkbenchSourceRunPolicy:
    if row is None:
        return WorkbenchSourceRunPolicy(
            session_id=session_id,
            source_kind="liepin",
            detail_open_mode="human_confirm",
            updated_at=_now_iso(),
        )
    return WorkbenchSourceRunPolicy(
        session_id=row["session_id"],
        source_kind=row["source_kind"],
        detail_open_mode=row["detail_open_mode"],
        updated_at=row["updated_at"],
    )


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


def _liepin_card_auto_detail_decision(
    *,
    matched_must_haves: list[str],
    matched_preferences: list[str],
    title: str,
    summary: str,
) -> tuple[int, str]:
    score = 0
    if matched_must_haves:
        score += 45 + min(len(matched_must_haves), 3) * 12
    score += min(len(matched_preferences), 4) * 8
    if title.strip():
        score += 6
    if len(summary.strip()) >= 80:
        score += 5
    score = min(score, 100)
    if score >= LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD:
        reason_parts = ["Agent recommends opening detail after card review"]
        if matched_must_haves:
            reason_parts.append(f"must-have: {', '.join(matched_must_haves[:4])}")
        if matched_preferences:
            reason_parts.append(f"preference/synonym: {', '.join(matched_preferences[:4])}")
        reason_parts.append(f"card signal score: {score}")
        return score, "; ".join(reason_parts) + "."
    return score, f"Agent kept this at card level; card signal score {score} is below the detail threshold."


def _create_auto_liepin_detail_open_request_conn(
    conn: sqlite3.Connection,
    *,
    context: WorkbenchSourceRunJobContext,
    connection_id: str,
    evidence_id: str,
    review_item_id: str,
    provider_key_hash: str,
    policy: WorkbenchSourceRunPolicy,
    decision_note: str,
    detail_candidates_json: str | None = None,
    now: str,
) -> str | None:
    safe_idempotency_key = _detail_idempotency_key(
        session_id=context.session.session_id,
        review_item_id=review_item_id,
        idempotency_key=f"auto-detail:{review_item_id}",
    )
    existing = conn.execute(
        """
        SELECT 1
        FROM detail_open_requests
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND idempotency_key = ?
        """,
        (DEFAULT_TENANT_ID, context.session.workspace_id, context.session.owner_user_id, safe_idempotency_key),
    ).fetchone()
    if existing is not None:
        return None
    status: DetailOpenRequestStatus = "pending"
    decided_at: str | None = None
    if policy.detail_open_mode == "bypass_confirm":
        status = "bypassed"
        decided_at = now
    request_id = f"dor_{uuid.uuid4().hex[:16]}"
    safe_detail_candidates_json = detail_candidates_json or _detail_candidates_json(
        candidate_id=review_item_id,
        provider_candidate_key_hash=provider_key_hash,
        value_score=None,
    )
    conn.execute(
        """
        INSERT INTO detail_open_requests (
            request_id, tenant_id, workspace_id, user_id, session_id, source_run_id, connection_id,
            candidate_evidence_id, review_item_id, provider_candidate_key_hash,
            detail_candidates_json, detail_open_mode, status, idempotency_key, blocked_reason, decision_note,
            ledger_id, decided_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?)
        """,
        (
            request_id,
            DEFAULT_TENANT_ID,
            context.session.workspace_id,
            context.session.owner_user_id,
            context.session.session_id,
            context.job.source_run_id,
            connection_id,
            evidence_id,
            review_item_id,
            provider_key_hash,
            safe_detail_candidates_json,
            policy.detail_open_mode,
            status,
            safe_idempotency_key,
            _bounded_text(decision_note, 500),
            decided_at,
            now,
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
        event_name="liepin_detail_open_auto_recommended",
        payload={
            "requestId": request_id,
            "reviewItemId": review_item_id,
            "status": status,
            "detailOpenMode": policy.detail_open_mode,
        },
    )
    _append_security_audit_event_conn(
        conn,
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=context.session.workspace_id,
        actor_user_id=context.session.owner_user_id,
        actor_role=None,
        target_type="detail_open_request",
        target_id=request_id,
        action="liepin_detail_open_auto_recommended",
        result=status,
        reason_code=policy.detail_open_mode,
        metadata={
            "sessionId": context.session.session_id,
            "sourceRunId": context.job.source_run_id,
            "reviewItemId": review_item_id,
            "detailOpenMode": policy.detail_open_mode,
        },
        created_at=now,
    )
    return request_id


def _detail_candidates_json_from_runtime_recommendation(recommendation: Mapping[str, object]) -> str:
    return _detail_candidates_json(
        candidate_id=_safe_candidate_text(recommendation.get("candidate_resume_id"), 256)
        or _safe_candidate_text(recommendation.get("provider_candidate_key_hash"), 256)
        or "liepin-candidate",
        provider_candidate_key_hash=_safe_candidate_text(recommendation.get("provider_candidate_key_hash"), 256),
        value_score=_int_or_none(recommendation.get("value_score")),
    )


def _detail_candidates_json(
    *,
    candidate_id: str,
    provider_candidate_key_hash: str | None,
    value_score: int | None,
) -> str:
    safe_candidate_id = _safe_candidate_text(candidate_id, 256) or "liepin-candidate"
    safe_provider_hash = _safe_candidate_text(provider_candidate_key_hash, 256)
    card_value_score = float(value_score) if value_score is not None else 0.0
    payload = [
        {
            "candidate_id": safe_candidate_id,
            "stable_provider_id": safe_candidate_id,
            "weak_fingerprint": safe_provider_hash or safe_candidate_id,
            "card_value_score": max(0.0, min(100.0, card_value_score)),
        }
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _liepin_review_target_conn(
    conn: sqlite3.Connection,
    *,
    user: WorkbenchUser,
    session_id: str,
    review_item_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT ce.evidence_id,
               ce.source_run_id,
               ce.evidence_level,
               ce.provider_candidate_key_hash,
               ce.resume_id
        FROM candidate_review_items AS cri
        JOIN candidate_evidence AS ce ON ce.review_item_id = cri.review_item_id
        WHERE cri.tenant_id = ?
          AND cri.workspace_id = ?
          AND cri.user_id = ?
          AND cri.session_id = ?
          AND cri.review_item_id = ?
          AND ce.source_kind = 'liepin'
        ORDER BY CASE ce.evidence_level WHEN 'detail' THEN 0 WHEN 'card' THEN 1 ELSE 2 END,
                 ce.created_at DESC,
                 ce.evidence_id ASC
        LIMIT 1
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id, review_item_id),
    ).fetchone()


def _reusable_detail_ledger_for_review_conn(
    conn: sqlite3.Connection,
    *,
    user: WorkbenchUser,
    session_id: str,
    review_item_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT dol.*
        FROM detail_open_ledger AS dol
        JOIN detail_open_requests AS dor ON dor.ledger_id = dol.ledger_id
        WHERE dor.tenant_id = ?
          AND dor.workspace_id = ?
          AND dor.user_id = ?
          AND dor.session_id = ?
          AND dor.review_item_id = ?
          AND dol.status IN ('leased', 'opened', 'maybe_used')
        ORDER BY CASE dol.status WHEN 'opened' THEN 0 WHEN 'leased' THEN 1 ELSE 2 END,
                 dol.updated_at DESC,
                 dol.ledger_id ASC
        LIMIT 1
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, session_id, review_item_id),
    ).fetchone()


def _detail_request_row_for_user_conn(
    conn: sqlite3.Connection,
    *,
    user: WorkbenchUser,
    request_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM detail_open_requests
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND request_id = ?
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, request_id),
    ).fetchone()


def _detail_open_request_from_row_conn(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> WorkbenchDetailOpenRequest:
    ledger = None
    provider_action = None
    if row["ledger_id"] is not None:
        ledger_row = conn.execute("SELECT * FROM detail_open_ledger WHERE ledger_id = ?", (row["ledger_id"],)).fetchone()
        if ledger_row is not None:
            ledger = _detail_open_ledger_from_row(ledger_row)
            provider_action = _provider_action(
                connection_id=row["connection_id"],
                review_item_id=row["review_item_id"],
                budget_impact="reserved",
            )
    return WorkbenchDetailOpenRequest(
        request_id=row["request_id"],
        session_id=row["session_id"],
        review_item_id=row["review_item_id"],
        status=row["status"],
        detail_open_mode=row["detail_open_mode"],
        decision_note=row["decision_note"],
        candidate=_detail_open_candidate_snapshot_conn(conn, row["review_item_id"]),
        blocked_reason=row["blocked_reason"],
        ledger=ledger,
        provider_action=provider_action,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _detail_open_candidate_snapshot_conn(
    conn: sqlite3.Connection,
    review_item_id: str,
) -> WorkbenchDetailOpenCandidateSnapshot | None:
    row = conn.execute(
        """
        SELECT *
        FROM candidate_review_items
        WHERE review_item_id = ?
        """,
        (review_item_id,),
    ).fetchone()
    if row is None:
        return None
    evidence = _evidence_by_review_item(conn, [review_item_id]).get(review_item_id, [])
    item = _review_item_from_row(row, evidence)
    return WorkbenchDetailOpenCandidateSnapshot(
        review_item_id=item.review_item_id,
        display_name=item.display_name,
        title=item.title,
        company=item.company,
        location=item.location,
        summary=item.summary,
        aggregate_score=item.aggregate_score,
        evidence_level=item.evidence_level,
        source_badges=item.source_badges,
        matched_must_haves=item.matched_must_haves,
        matched_preferences=item.matched_preferences,
        missing_risks=item.missing_risks,
    )


def _detail_open_ledger_from_row(row: sqlite3.Row) -> WorkbenchDetailOpenLedger:
    return WorkbenchDetailOpenLedger(
        ledger_id=row["ledger_id"],
        status=row["status"],
        budget_day=row["budget_day"],
        lease_expires_at=row["lease_expires_at"],
    )


def _provider_action(
    *,
    connection_id: str,
    review_item_id: str,
    budget_impact: Literal["none", "reserved"],
) -> WorkbenchProviderAction:
    if budget_impact == "reserved":
        message = "Detail view lease is reserved. Continue in the managed Liepin browser."
    else:
        message = "Open an already-known Liepin detail view in the managed browser without reserving another budget slot."
    return WorkbenchProviderAction(
        action_kind="managed_browser",
        source_kind="liepin",
        connection_id=connection_id,
        review_item_id=review_item_id,
        budget_impact=budget_impact,
        message=message,
    )


def _queue_external_write_intent_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    source_run_id: str,
    target_kind: str,
    idempotency_key: str,
    target_scope: Mapping[str, object],
    now: str,
) -> None:
    target_scope_json = json.dumps(redact_event_payload(target_scope), sort_keys=True, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO external_write_intents (
            intent_id, tenant_id, workspace_id, user_id, session_id, source_run_id,
            target_kind, target_scope_json, status, attempt_count, idempotency_key,
            resolved_external_ref, last_error_code, last_error_message, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, NULL, ?, ?)
        ON CONFLICT(tenant_id, workspace_id, user_id, idempotency_key) DO UPDATE SET
            updated_at = excluded.updated_at
        """,
        (
            f"ewi_{uuid.uuid4().hex[:16]}",
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            source_run_id,
            target_kind,
            target_scope_json,
            idempotency_key,
            now,
            now,
        ),
    )


def _fail_external_write_intent_conn(
    conn: sqlite3.Connection,
    *,
    intent_id: str,
    error_code: str,
    error_message: str,
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE external_write_intents
        SET status = 'failed',
            last_error_code = ?,
            last_error_message = ?,
            updated_at = ?
        WHERE intent_id = ?
        """,
        (_bounded_text(error_code, 128), _bounded_text(error_message, 500), now, intent_id),
    )


def _block_detail_open_request_conn(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    reason: str,
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE detail_open_requests
        SET status = 'blocked', blocked_reason = ?, updated_at = ?
        WHERE request_id = ?
        """,
        (reason, now, row["request_id"]),
    )
    conn.execute(
        """
        UPDATE source_runs
        SET detail_open_blocked_count = detail_open_blocked_count + 1
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
        source_kind="liepin",
        event_name="liepin_detail_open_blocked",
        payload={"requestId": row["request_id"], "reviewItemId": row["review_item_id"], "reason": reason},
    )
    _append_security_audit_event_conn(
        conn,
        tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"],
        actor_user_id=row["user_id"],
        actor_role=None,
        target_type="detail_open_request",
        target_id=row["request_id"],
        action="liepin_detail_open_blocked",
        result="blocked",
        reason_code=reason,
        metadata={"sessionId": row["session_id"], "sourceRunId": row["source_run_id"]},
        created_at=now,
    )


def _budget_day(now: str) -> str:
    return _parse_iso(now).date().isoformat()


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


def _requirement_reviews_by_session(
    conn: sqlite3.Connection,
    session_ids: list[str],
) -> dict[str, WorkbenchRequirementReview]:
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM session_requirement_reviews
        WHERE session_id IN ({placeholders})
        """,
        session_ids,
    ).fetchall()
    return {row["session_id"]: _requirement_review_from_row(row) for row in rows}


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
        detail_open_used_count=row["detail_open_used_count"],
        detail_open_blocked_count=row["detail_open_blocked_count"],
    )


def _runtime_run_id_from_artifacts(artifacts: object) -> str | None:
    value = getattr(artifacts, "run_id", None)
    if not isinstance(value, str):
        return None
    runtime_run_id = value.strip()
    return runtime_run_id or None


def _source_run_runtime_link_row_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    source_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT source_run_id, source_kind, status, runtime_run_id
        FROM source_runs
        WHERE tenant_id = ?
          AND workspace_id = ?
          AND user_id = ?
          AND session_id = ?
          AND source_run_id = ?
        """,
        (tenant_id, workspace_id, user_id, session_id, source_run_id),
    ).fetchone()


def _validate_source_run_runtime_run_id_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    source_run_id: str,
    runtime_run_id: str,
) -> None:
    runtime_run_id = runtime_run_id.strip()
    if not runtime_run_id:
        raise RuntimeError("runtime_run_id_required")
    row = _source_run_runtime_link_row_conn(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        source_run_id=source_run_id,
    )
    if row is None or row["source_kind"] != "cts":
        raise RuntimeError("cts_source_run_not_found")
    existing = row["runtime_run_id"]
    if existing and existing != runtime_run_id:
        raise RuntimeError("runtime_run_id_conflict")


def _attach_source_run_runtime_run_id_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    source_run_id: str,
    runtime_run_id: str,
) -> None:
    runtime_run_id = runtime_run_id.strip()
    if not runtime_run_id:
        raise RuntimeError("runtime_run_id_required")
    row = _source_run_runtime_link_row_conn(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        source_run_id=source_run_id,
    )
    if row is None or row["source_kind"] != "cts":
        raise RuntimeError("cts_source_run_not_found")
    _validate_source_run_runtime_run_id_conn(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        source_run_id=source_run_id,
        runtime_run_id=runtime_run_id,
    )
    if row["runtime_run_id"] == runtime_run_id:
        return
    conn.execute(
        """
        UPDATE source_runs
        SET runtime_run_id = ?
        WHERE tenant_id = ?
          AND workspace_id = ?
          AND user_id = ?
          AND session_id = ?
          AND source_run_id = ?
          AND runtime_run_id IS NULL
        """,
        (runtime_run_id, tenant_id, workspace_id, user_id, session_id, source_run_id),
    )


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


def _requirement_review_from_row(row: sqlite3.Row) -> WorkbenchRequirementReview:
    return WorkbenchRequirementReview(
        session_id=row["session_id"],
        status=row["status"],
        requirement_sheet=_requirement_sheet_from_json(row["requirement_sheet_json"]),
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


def _runtime_sourcing_job_from_row(row: sqlite3.Row) -> WorkbenchRuntimeSourcingJob:
    source_kinds: list[Literal["cts", "liepin"]] = []
    for value in _json_to_list(row["source_kinds_json"]):
        if value in {"cts", "liepin"}:
            source_kinds.append(cast(Literal["cts", "liepin"], value))
    source_run_ids = tuple(_json_to_list(row["source_run_ids_json"]))
    return WorkbenchRuntimeSourcingJob(
        job_id=row["job_id"],
        session_id=row["session_id"],
        status=row["status"],
        source_kinds=tuple(source_kinds),
        source_run_ids=source_run_ids,
        runtime_run_id=row["runtime_run_id"],
        attempt_count=row["attempt_count"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _runtime_scoped_source_runs(
    *,
    source_runs: list[WorkbenchSourceRun],
    source_run_ids: tuple[str, ...],
    source_kinds: tuple[Literal["cts", "liepin"], ...],
) -> list[WorkbenchSourceRun]:
    if source_run_ids:
        scoped_ids = set(source_run_ids)
        return [
            source_run
            for source_run in source_runs
            if source_run.source_run_id in scoped_ids and source_run.status not in {"blocked", "completed"}
        ]
    scoped_kinds = set(source_kinds)
    return [
        source_run
        for source_run in source_runs
        if source_run.source_kind in scoped_kinds and source_run.status not in {"blocked", "completed"}
    ]


def _runtime_scoped_source_runs_from_job_row(
    *,
    row: sqlite3.Row,
    source_runs: list[WorkbenchSourceRun],
) -> list[WorkbenchSourceRun]:
    source_kinds: list[Literal["cts", "liepin"]] = []
    for value in _json_to_list(row["source_kinds_json"]):
        if value in {"cts", "liepin"}:
            source_kinds.append(cast(Literal["cts", "liepin"], value))
    return _runtime_scoped_source_runs(
        source_runs=source_runs,
        source_run_ids=tuple(_json_to_list(row["source_run_ids_json"])),
        source_kinds=tuple(source_kinds),
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
        schema_version=row["schema_version"] or "workbench_event_v1",
        idempotency_key=row["idempotency_key"],
        payload=payload,
        occurred_at=row["occurred_at"] or row["created_at"],
        created_at=row["created_at"],
    )


def _runtime_source_lane_latest_state_from_row(row: sqlite3.Row) -> WorkbenchRuntimeSourceLaneLatestState:
    return WorkbenchRuntimeSourceLaneLatestState(
        source_run_id=row["source_run_id"],
        source_kind=row["source_kind"],
        runtime_run_id=row["runtime_run_id"],
        source_lane_run_id=row["source_lane_run_id"],
        attempt=row["attempt"],
        event_seq=row["event_seq"],
        event_type=row["event_type"],
        status=row["status"],
        payload=_json_to_dict(row["payload_json"]),
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
        provider_candidate_key_hash=row["provider_candidate_key_hash"],
        runtime_identity_id=row["runtime_identity_id"],
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


def _source_badge_for_evidence(evidence: WorkbenchCandidateEvidence) -> str:
    if evidence.source_kind == "cts":
        return "CTS final" if evidence.evidence_level == "final" else "CTS"
    if evidence.evidence_level == "detail":
        return "Liepin detail"
    return "Liepin card"


def _review_item_from_row(
    row: sqlite3.Row,
    evidence: list[WorkbenchCandidateEvidence],
) -> WorkbenchCandidateReviewItem:
    source_badges = _unique_list(_source_badge_for_evidence(item) for item in evidence)
    if len({item.source_kind for item in evidence}) > 1:
        source_badges.append("Multiple sources")
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
        why_selected=row["why_selected"],
        source_round=row["source_round"],
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


def _liepin_lane_status(result: object) -> str:
    status = _safe_candidate_text(_attr(result, "status"), 64)
    return status or "completed"


def _liepin_job_status_from_lane_result(result: object) -> Literal["completed", "failed"]:
    return "failed" if _liepin_lane_status(result) in {"blocked", "failed", "cancelled"} else "completed"


def _liepin_source_run_status_from_lane_result(result: object) -> Literal["queued", "blocked", "running", "completed", "failed"]:
    status = _liepin_lane_status(result)
    if status == "blocked":
        return "blocked"
    if status in {"failed", "cancelled"}:
        return "failed"
    return "completed"


def _liepin_warning_code_from_lane_result(result: object) -> str | None:
    status = _liepin_lane_status(result)
    if status == "blocked":
        return _safe_candidate_text(_attr(result, "blocked_reason_code"), 128) or "blocked_backend_unavailable"
    if status == "partial":
        return _safe_candidate_text(_attr(result, "stop_reason_code"), 128) or "partial_timeout"
    if status == "cancelled":
        return "cancelled_by_user"
    if status == "failed":
        return "runtime_failed"
    return None


def _liepin_warning_message_from_lane_result(result: object) -> str | None:
    status = _liepin_lane_status(result)
    if status in {"completed"}:
        return None
    return redact_text(_safe_candidate_text(_attr(result, "safe_error_summary"), 500) or f"Liepin lane ended with status {status}.")


def _liepin_job_error_from_lane_result(result: object) -> str | None:
    if _liepin_job_status_from_lane_result(result) == "completed":
        return None
    return _liepin_warning_message_from_lane_result(result)


def _liepin_source_run_event_name_from_lane_result(result: object) -> str:
    status = _liepin_lane_status(result)
    if status == "blocked":
        return "source_run_blocked"
    if status in {"failed", "cancelled"}:
        return "source_run_failed"
    return "source_run_completed"


def _synthetic_runtime_source_lane_event(result: object) -> dict[str, object]:
    status = _liepin_lane_status(result)
    event_type_by_status = {
        "completed": "source_lane_completed",
        "blocked": "source_lane_blocked",
        "partial": "source_lane_partial",
        "failed": "source_lane_failed",
        "cancelled": "source_lane_cancelled",
    }
    raw_candidate_count = _int_or_none(_attr(result, "raw_candidate_count")) or 0
    return {
        "schema_version": "runtime_source_lane_event_v1",
        "runtime_run_id": _safe_candidate_text(_attr(result, "runtime_run_id"), 256) or "runtime",
        "source_plan_id": _safe_candidate_text(_attr(result, "source_plan_id"), 256) or "runtime:source:liepin",
        "source_lane_run_id": _safe_candidate_text(_attr(result, "source_lane_run_id"), 256) or "runtime:lane:liepin",
        "source": "liepin",
        "attempt": _int_or_none(_attr(result, "attempt")) or 1,
        "event_seq": 1,
        "event_type": event_type_by_status.get(status, "source_lane_completed"),
        "status": status,
        "safe_counts": {
            "cards_seen": raw_candidate_count,
            "candidates": len(_object_list(_attr(result, "candidates"))),
            "detail_recommendations": len(_object_list(_attr(result, "detail_recommendations"))),
        },
        "blocked_reason_code": _safe_candidate_text(_attr(result, "blocked_reason_code"), 128),
        "stop_reason_code": _safe_candidate_text(_attr(result, "stop_reason_code"), 128),
        "safe_reason_code": _liepin_warning_code_from_lane_result(result),
    }


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
    requirement_review: WorkbenchRequirementReview,
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
        requirement_review=requirement_review,
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
    schema_version: str = "workbench_event_v1",
    idempotency_key: str | None = None,
    occurred_at: str | None = None,
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
    safe_schema_version = _bounded_text(schema_version, 80) or "workbench_event_v1"
    safe_idempotency_key = _bounded_text(idempotency_key, 160)
    now = _now_iso()
    safe_occurred_at = _bounded_text(occurred_at, 80) or now
    cursor = conn.execute(
        """
        INSERT INTO session_events (
            tenant_id, workspace_id, user_id, session_id, session_seq,
            source_run_id, source_kind, event_name, schema_version, idempotency_key,
            payload_redacted_json, occurred_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            safe_schema_version,
            safe_idempotency_key,
            json.dumps(redacted_payload, sort_keys=True, separators=(",", ":")),
            safe_occurred_at,
            now,
        ),
    )
    return WorkbenchEvent(
        global_seq=int(cursor.lastrowid or 0),
        session_seq=session_seq,
        session_id=session_id,
        source_run_id=source_run_id,
        source_kind=source_kind,
        event_name=event_name,
        schema_version=safe_schema_version,
        idempotency_key=safe_idempotency_key,
        payload=redacted_payload,
        occurred_at=safe_occurred_at,
        created_at=now,
    )


def _append_runtime_source_lane_event_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    source_run_id: str,
    source_kind: Literal["cts", "liepin"],
    event_name: str,
    schema_version: str,
    idempotency_key: str,
    payload: dict[str, object],
) -> WorkbenchEvent:
    safe_idempotency_key = _bounded_text(idempotency_key, 160)
    if not safe_idempotency_key:
        raise ValueError("Runtime source lane event idempotency key is required.")
    existing = conn.execute(
        """
        SELECT *
        FROM session_events
        WHERE tenant_id = ?
          AND workspace_id = ?
          AND user_id = ?
          AND session_id = ?
          AND idempotency_key = ?
        """,
        (tenant_id, workspace_id, user_id, session_id, safe_idempotency_key),
    ).fetchone()
    if existing is not None:
        event = _event_from_row(existing)
    else:
        try:
            event = _append_workbench_event_conn(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind=source_kind,
                event_name=event_name,
                schema_version=schema_version,
                idempotency_key=safe_idempotency_key,
                payload=payload,
            )
        except sqlite3.IntegrityError:
            existing = conn.execute(
                """
                SELECT *
                FROM session_events
                WHERE tenant_id = ?
                  AND workspace_id = ?
                  AND user_id = ?
                  AND session_id = ?
                  AND idempotency_key = ?
                """,
                (tenant_id, workspace_id, user_id, session_id, safe_idempotency_key),
            ).fetchone()
            if existing is None:
                raise
            event = _event_from_row(existing)
    _upsert_runtime_source_lane_latest_state_conn(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        source_run_id=source_run_id,
        source_kind=source_kind,
        payload=event.payload,
    )
    return event


def _upsert_runtime_source_lane_latest_state_conn(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    source_run_id: str,
    source_kind: Literal["cts", "liepin"],
    payload: dict[str, object],
) -> None:
    source_lane_run_id = _safe_candidate_text(payload.get("source_lane_run_id"), 256)
    if not source_lane_run_id:
        return
    attempt = _int_or_none(payload.get("attempt")) or 0
    event_seq = _int_or_none(payload.get("event_seq")) or 0
    runtime_run_id = _safe_candidate_text(payload.get("runtime_run_id"), 256)
    event_type = _safe_candidate_text(payload.get("event_type"), 128) or "unknown"
    status = _safe_candidate_text(payload.get("status"), 64)
    redacted_payload = redact_event_payload(payload)
    if not isinstance(redacted_payload, dict):
        redacted_payload = {"value": redacted_payload}
    existing = conn.execute(
        """
        SELECT attempt, event_seq
        FROM runtime_source_lane_latest_state
        WHERE tenant_id = ?
          AND workspace_id = ?
          AND user_id = ?
          AND session_id = ?
          AND source_run_id = ?
          AND source_lane_run_id = ?
        """,
        (tenant_id, workspace_id, user_id, session_id, source_run_id, source_lane_run_id),
    ).fetchone()
    if existing is not None and (int(existing["attempt"]), int(existing["event_seq"])) > (attempt, event_seq):
        return
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO runtime_source_lane_latest_state (
            tenant_id, workspace_id, user_id, session_id, source_run_id, source_kind,
            runtime_run_id, source_lane_run_id, attempt, event_seq, event_type, status,
            payload_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, workspace_id, user_id, session_id, source_run_id, source_lane_run_id)
        DO UPDATE SET
            source_kind = excluded.source_kind,
            runtime_run_id = excluded.runtime_run_id,
            attempt = excluded.attempt,
            event_seq = excluded.event_seq,
            event_type = excluded.event_type,
            status = excluded.status,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            source_run_id,
            source_kind,
            runtime_run_id,
            source_lane_run_id,
            attempt,
            event_seq,
            event_type,
            status,
            json.dumps(redacted_payload, sort_keys=True, separators=(",", ":")),
            now,
        ),
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
            _bounded_text(redact_text(request_ip), LOGIN_ATTEMPT_IP_MAX),
            _bounded_text(redact_text(user_agent), LOGIN_ATTEMPT_USER_AGENT_MAX),
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
        request_ip=_bounded_text(redact_text(request_ip), LOGIN_ATTEMPT_IP_MAX),
        user_agent=_bounded_text(redact_text(user_agent), LOGIN_ATTEMPT_USER_AGENT_MAX),
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


def _requirement_sheet_json(requirement_sheet: RequirementSheet | None) -> str | None:
    if requirement_sheet is None:
        return None
    return json.dumps(requirement_sheet.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _requirement_sheet_from_json(value: object) -> RequirementSheet | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return RequirementSheet.model_validate(json.loads(value))


def _validate_requirement_sheet_for_session(session: WorkbenchSession, requirement_sheet: RequirementSheet) -> None:
    if requirement_sheet.job_title != session.job_title:
        raise ValueError("requirement_sheet_job_title_mismatch")


def _workbench_note_status_hint(value: str) -> WorkbenchNoteStatusHint:
    text = _bounded_text(value, 64)
    if text in NOTE_STATUS_HINTS:
        return cast(WorkbenchNoteStatusHint, text)
    return "unknown"


def _workbench_note_kind(value: str) -> WorkbenchNoteKind:
    text = _bounded_text(value, 64)
    if text in NOTE_KINDS:
        return cast(WorkbenchNoteKind, text)
    return "progress"


def _runtime_identity_by_resume_id_from_artifacts(artifacts: object) -> dict[str, str]:
    run_state = getattr(artifacts, "run_state", None)
    value = getattr(run_state, "candidate_identity_by_resume_id", None)
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for resume_id, identity_id in value.items():
        safe_resume_id = _safe_candidate_text(resume_id, 128)
        safe_identity_id = _safe_candidate_text(identity_id, 256)
        if safe_resume_id and safe_identity_id:
            result[safe_resume_id] = safe_identity_id
    return result


@dataclass(frozen=True)
class _RuntimeFallbackEvidence:
    evidence_id: str
    source: str
    evidence_level: str
    candidate_resume_id: str
    provider_candidate_key_hash: str


def _runtime_final_identity_order_from_artifacts(artifacts: object) -> list[str]:
    run_state = getattr(artifacts, "run_state", None)
    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    result: list[str] = []
    for resume_id in list(getattr(run_state, "top_pool_ids", []) or []):
        safe_resume_id = _safe_candidate_text(resume_id, 128)
        if not safe_resume_id:
            continue
        identity_id = _safe_candidate_text(_mapping_get(identity_by_resume_id, safe_resume_id), 256) or safe_resume_id
        if identity_id not in result:
            result.append(identity_id)
        if len(result) >= 10:
            return result
    revision = getattr(artifacts, "finalization_revision", None)
    for identity_id in list(getattr(revision, "candidate_identity_ids", []) or []):
        safe_identity_id = _safe_candidate_text(identity_id, 256)
        if safe_identity_id and safe_identity_id not in result:
            result.append(safe_identity_id)
        if len(result) >= 10:
            break
    return result


def _runtime_persist_identity_order_from_artifacts(
    artifacts: object,
    *,
    ordered_identity_ids: list[str],
) -> list[str]:
    run_state = getattr(artifacts, "run_state", None)
    result = list(ordered_identity_ids)

    identities = getattr(run_state, "candidate_identities", {}) or {}
    if isinstance(identities, Mapping):
        for identity_id_value in identities:
            identity_id = _safe_candidate_text(identity_id_value, 256)
            if identity_id and identity_id not in result:
                result.append(identity_id)

    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    if isinstance(identity_by_resume_id, Mapping):
        for identity_id_value in identity_by_resume_id.values():
            identity_id = _safe_candidate_text(identity_id_value, 256)
            if identity_id and identity_id not in result:
                result.append(identity_id)

    evidence_by_identity = getattr(run_state, "source_evidence_by_identity_id", {}) or {}
    if isinstance(evidence_by_identity, Mapping):
        for identity_id_value in evidence_by_identity:
            identity_id = _safe_candidate_text(identity_id_value, 256)
            if identity_id and identity_id not in result:
                result.append(identity_id)

    return result


def _runtime_canonical_resume_id(run_state: object, identity_id: str) -> str | None:
    canonical_by_identity = getattr(run_state, "canonical_resume_by_identity_id", {}) or {}
    canonical = _mapping_get(canonical_by_identity, identity_id)
    resume_id = _safe_candidate_text(_attr(canonical, "canonical_resume_id"), 128)
    if resume_id:
        return resume_id
    identities = getattr(run_state, "candidate_identities", {}) or {}
    identity = _mapping_get(identities, identity_id)
    for resume_id_value in _object_list(_attr(identity, "resume_ids")):
        resume_id = _safe_candidate_text(resume_id_value, 128)
        if resume_id:
            return resume_id
    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    if isinstance(identity_by_resume_id, Mapping):
        for resume_id_value, mapped_identity_id in identity_by_resume_id.items():
            if _safe_candidate_text(mapped_identity_id, 256) == identity_id:
                return _safe_candidate_text(resume_id_value, 128)
    return None


def _runtime_merged_resume_ids(run_state: object, identity_id: str, canonical_resume_id: str) -> list[str]:
    identities = getattr(run_state, "candidate_identities", {}) or {}
    identity = _mapping_get(identities, identity_id)
    result: list[str] = []
    for resume_id_value in _object_list(_attr(identity, "resume_ids")):
        resume_id = _safe_candidate_text(resume_id_value, 128)
        if resume_id and resume_id not in result:
            result.append(resume_id)
    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    if isinstance(identity_by_resume_id, Mapping):
        for resume_id_value, mapped_identity_id in identity_by_resume_id.items():
            resume_id = _safe_candidate_text(resume_id_value, 128)
            if resume_id and _safe_candidate_text(mapped_identity_id, 256) == identity_id and resume_id not in result:
                result.append(resume_id)
    if canonical_resume_id not in result:
        result.insert(0, canonical_resume_id)
    return result


def _runtime_source_evidence_for_identity(run_state: object, identity_id: str) -> list[object]:
    evidence_by_identity = getattr(run_state, "source_evidence_by_identity_id", {}) or {}
    value = _mapping_get(evidence_by_identity, identity_id)
    if isinstance(value, list | tuple):
        return list(value)
    return []


def _runtime_source_evidence_for_resume(run_state: object, resume_id: str) -> list[object]:
    evidence_by_resume = getattr(run_state, "source_evidence_by_resume_id", {}) or {}
    value = _mapping_get(evidence_by_resume, resume_id)
    if isinstance(value, list | tuple):
        return list(value)
    return []


def _runtime_source_round_for_recommendation(
    run_state: object,
    recommendation: Mapping[str, object],
) -> int | None:
    source_round = _int_or_none(recommendation.get("source_round"))
    if source_round is not None:
        return source_round
    source_round = _source_round_from_lane_run_id(recommendation.get("source_lane_run_id"))
    if source_round is not None:
        return source_round

    source_evidence_id = _safe_candidate_text(recommendation.get("source_evidence_id"), 256)
    candidate_resume_id = _safe_candidate_text(recommendation.get("candidate_resume_id"), 128)
    candidates: list[object] = []
    if candidate_resume_id:
        candidates.extend(_runtime_source_evidence_for_resume(run_state, candidate_resume_id))
        identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
        identity_id = _safe_candidate_text(_mapping_get(identity_by_resume_id, candidate_resume_id), 256)
        if identity_id:
            candidates.extend(_runtime_source_evidence_for_identity(run_state, identity_id))

    evidence_by_identity = getattr(run_state, "source_evidence_by_identity_id", {}) or {}
    if isinstance(evidence_by_identity, Mapping):
        for value in evidence_by_identity.values():
            if isinstance(value, list | tuple):
                candidates.extend(value)

    seen: set[str] = set()
    for evidence in candidates:
        evidence_id = _safe_candidate_text(_attr(evidence, "evidence_id"), 256)
        resume_id = _safe_candidate_text(_attr(evidence, "candidate_resume_id"), 128)
        if source_evidence_id and evidence_id != source_evidence_id:
            continue
        if not source_evidence_id and candidate_resume_id and resume_id != candidate_resume_id:
            continue
        key = evidence_id or f"resume:{resume_id}:{_safe_candidate_text(_attr(evidence, 'source_lane_run_id'), 256)}"
        if key in seen:
            continue
        seen.add(key)
        source_round = _source_round_from_lane_run_id(_attr(evidence, "source_lane_run_id"))
        if source_round is not None:
            return source_round
    return None


def _runtime_source_round_from_evidence_items(evidence_items: list[object]) -> int | None:
    for evidence in evidence_items:
        source_round = _source_round_from_lane_run_id(_attr(evidence, "source_lane_run_id"))
        if source_round is not None:
            return source_round
    return None


def _source_round_from_lane_run_id(value: object) -> int | None:
    text = _safe_candidate_text(value, 512)
    if not text:
        return None
    match = re.search(r"(?:^|:)round:(\d+)(?::|$)", text)
    if match is None:
        return None
    return _int_or_none(match.group(1))


def _runtime_coverage_summary_payload(run_state: object) -> dict[str, object]:
    coverage_summary = getattr(run_state, "source_coverage_summary", None)
    to_public_payload = getattr(coverage_summary, "to_public_payload", None)
    if callable(to_public_payload):
        payload = to_public_payload()
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


def _runtime_source_lane_result_payloads(run_state: object) -> list[dict[str, object]]:
    values = getattr(run_state, "runtime_source_lane_results", None)
    if values is None:
        return []
    result: list[dict[str, object]] = []
    for value in list(values or []):
        if isinstance(value, Mapping):
            result.append({str(key): item for key, item in value.items()})
            continue
        to_public_payload = getattr(value, "to_public_payload", None)
        if callable(to_public_payload):
            payload = to_public_payload()
            if isinstance(payload, Mapping):
                result.append({str(key): item for key, item in payload.items()})
    return result


def _runtime_source_lane_events_from_result_payload(result_payload: Mapping[str, object]) -> list[dict[str, object]]:
    raw_events = result_payload.get("events")
    events: list[dict[str, object]] = []
    if isinstance(raw_events, list | tuple):
        for item in raw_events:
            if isinstance(item, Mapping):
                events.append({str(key): value for key, value in item.items()})
    if events:
        return events
    source_kind = _safe_candidate_text(result_payload.get("source"), 32) or "cts"
    candidate_count = _int_or_none(result_payload.get("candidate_count")) or 0
    raw_candidate_count = _int_or_none(result_payload.get("raw_candidate_count")) or candidate_count
    detail_count = _int_or_none(result_payload.get("detail_recommendation_count")) or len(
        _object_list(result_payload.get("detail_recommendations"))
    )
    safe_counts: dict[str, int] = {"cards_seen": raw_candidate_count, "candidates": candidate_count}
    event_type = "source_lane_completed"
    if detail_count:
        safe_counts = {"detail_recommendations": detail_count}
        event_type = "detail_recommended"
    return [
        {
            "schema_version": "runtime_source_lane_event_v1",
            "runtime_run_id": result_payload.get("runtime_run_id"),
            "source_plan_id": result_payload.get("source_plan_id"),
            "source_lane_run_id": result_payload.get("source_lane_run_id"),
            "source": source_kind,
            "attempt": result_payload.get("attempt") or 1,
            "event_seq": 1,
            "event_type": event_type,
            "status": result_payload.get("status") or "completed",
            "safe_counts": safe_counts,
            "safe_reason_code": result_payload.get("stop_reason_code") or result_payload.get("blocked_reason_code"),
        }
    ]


def _augment_runtime_source_lane_event_payload(
    event_payload: Mapping[str, object],
    *,
    result_payload: Mapping[str, object],
    coverage_payload: Mapping[str, object],
    finalization_payload: Mapping[str, object],
    runtime_run_id: str,
    source_kind: str,
) -> dict[str, object]:
    payload = {str(key): value for key, value in event_payload.items()}
    payload["schema_version"] = payload.get("schema_version") or "runtime_source_lane_event_v1"
    payload["runtime_run_id"] = _safe_candidate_text(payload.get("runtime_run_id"), 256) or runtime_run_id
    payload["source_plan_id"] = _safe_candidate_text(payload.get("source_plan_id"), 256) or _safe_candidate_text(
        result_payload.get("source_plan_id"),
        256,
    ) or f"{runtime_run_id}:source:{source_kind}"
    payload["source_lane_run_id"] = _safe_candidate_text(
        payload.get("source_lane_run_id"),
        256,
    ) or _safe_candidate_text(result_payload.get("source_lane_run_id"), 256) or f"{runtime_run_id}:lane:{source_kind}"
    payload["source"] = source_kind
    payload["attempt"] = _int_or_none(payload.get("attempt")) or _int_or_none(result_payload.get("attempt")) or 1
    payload["event_seq"] = _int_or_none(payload.get("event_seq")) or 1
    payload["event_type"] = _safe_candidate_text(payload.get("event_type"), 128) or "source_lane_completed"
    payload["status"] = _safe_candidate_text(payload.get("status"), 64) or _safe_candidate_text(
        result_payload.get("status"),
        64,
    ) or "completed"
    if not isinstance(payload.get("safe_counts"), Mapping):
        candidate_count = _int_or_none(result_payload.get("candidate_count")) or 0
        raw_candidate_count = _int_or_none(result_payload.get("raw_candidate_count")) or candidate_count
        payload["safe_counts"] = {"cards_seen": raw_candidate_count, "candidates": candidate_count}
    if coverage_payload:
        payload["source_coverage_summary"] = dict(coverage_payload)
    payload["finalization_revision"] = dict(finalization_payload)
    return payload


def _runtime_source_lane_event_name(payload: Mapping[str, object]) -> str:
    event_type = _safe_candidate_text(payload.get("event_type"), 128) or "source_lane_completed"
    safe_event_type = "_".join(part for part in event_type.lower().split("_") if part)
    return f"runtime_{safe_event_type or 'source_lane_completed'}"


def _runtime_source_lane_event_idempotency_key(payload: Mapping[str, object]) -> str:
    runtime_run_id = _safe_candidate_text(payload.get("runtime_run_id"), 256) or "runtime"
    source_kind = _safe_candidate_text(payload.get("source"), 32) or "source"
    source_lane_run_id = _safe_candidate_text(payload.get("source_lane_run_id"), 256) or "lane"
    attempt = _int_or_none(payload.get("attempt")) or 0
    event_seq = _int_or_none(payload.get("event_seq")) or 0
    event_type = _safe_candidate_text(payload.get("event_type"), 128) or "event"
    return f"{runtime_run_id}:{source_kind}:{source_lane_run_id}:{attempt}:{event_seq}:{event_type}"


def _runtime_detail_recommendation_payloads(run_state: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for lane_payload in _runtime_source_lane_result_payloads(run_state):
        if _safe_candidate_text(lane_payload.get("source"), 32) != "liepin":
            continue
        for item in _object_list(lane_payload.get("detail_recommendations")):
            if isinstance(item, Mapping):
                result.append({str(key): value for key, value in item.items()})
            else:
                to_public_payload = getattr(item, "to_public_payload", None)
                if callable(to_public_payload):
                    payload = to_public_payload()
                    if isinstance(payload, Mapping):
                        result.append({str(key): value for key, value in payload.items()})
    return result


def _ensure_runtime_liepin_recommended_card_review_item_conn(
    conn: sqlite3.Connection,
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    run_state: object,
    source_run_id: str,
    candidate_store: Mapping[object, object],
    normalized_store: Mapping[object, object],
    recommendation: Mapping[str, object],
    source_evidence_id: str,
    provider_key_hash: str | None,
    now: str,
) -> tuple[str, str] | None:
    candidate_resume_id = _safe_candidate_text(recommendation.get("candidate_resume_id"), 128)
    if not candidate_resume_id:
        return None
    safe_provider_key_hash = (
        provider_key_hash
        or _safe_candidate_text(recommendation.get("provider_candidate_key_hash"), 256)
        or _sha256_text(candidate_resume_id)
    )
    candidate = _mapping_get(candidate_store, candidate_resume_id)
    normalized = _mapping_get(normalized_store, candidate_resume_id)
    raw_payload = _attr(candidate, "raw")
    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    identity_id = _safe_candidate_text(_mapping_get(identity_by_resume_id, candidate_resume_id), 256) or candidate_resume_id
    review_item_id = _stable_id("review", context.session.session_id, "identity", identity_id)
    workbench_resume_id = _stable_id("candidate", context.session.session_id, candidate_resume_id)
    display_name = (
        _safe_candidate_text(_attr(normalized, "candidate_name"), 160)
        or _safe_candidate_text(_attr(raw_payload, "candidate_name"), 160)
        or _safe_candidate_text(_attr(raw_payload, "name"), 160)
        or f"Candidate {review_item_id[-8:]}"
    )
    title = (
        _safe_candidate_text(_attr(normalized, "current_title"), 240)
        or _safe_candidate_text(_attr(raw_payload, "current_title"), 240)
        or _safe_candidate_text(_attr(raw_payload, "title"), 240)
        or _safe_candidate_text(_attr(candidate, "expected_job_category"), 240)
        or "Liepin candidate card"
    )
    company = (
        _safe_candidate_text(_attr(normalized, "current_company"), 240)
        or _safe_candidate_text(_attr(raw_payload, "current_company"), 240)
        or _safe_candidate_text(_attr(raw_payload, "company"), 240)
        or ""
    )
    location = (
        _safe_candidate_text(_first(_attr(normalized, "locations")), 160)
        or _safe_candidate_text(_attr(candidate, "now_location"), 160)
        or _safe_candidate_text(_attr(raw_payload, "location"), 160)
        or _safe_candidate_text(_attr(raw_payload, "city"), 160)
        or ""
    )
    summary = (
        _safe_candidate_text(_attr(candidate, "search_text"), 1000)
        or _safe_candidate_text(_attr(raw_payload, "summary"), 1000)
        or ""
    )
    card_text = " ".join([display_name, title, company, location, summary])
    sheet = _requirement_sheet_for_projection(context)
    matched_must_haves = _matched_terms(sheet.must_have_capabilities, card_text)
    matched_preferences = _matched_terms(sheet.preferred_capabilities, card_text)
    missing_risks = ["Detail page not opened yet.", "Agent recommends detail review before final outreach."]
    strengths = _unique_list([*matched_must_haves[:6], *matched_preferences[:6]])
    score = _int_or_none(recommendation.get("value_score"))
    source_round = _runtime_source_round_for_recommendation(run_state, recommendation)
    existing = conn.execute(
        """
        SELECT 1
        FROM candidate_review_items
        WHERE review_item_id = ?
        """,
        (review_item_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO candidate_review_items (
                review_item_id, tenant_id, workspace_id, user_id, session_id,
                primary_evidence_id, display_name, title, company, location, summary,
                aggregate_score, fit_bucket, source_round, review_status, note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'card_recommended', ?, 'new', '', ?, ?)
            """,
            (
                review_item_id,
                DEFAULT_TENANT_ID,
                context.session.workspace_id,
                context.session.owner_user_id,
                context.session.session_id,
                source_evidence_id,
                display_name,
                title,
                company,
                location,
                summary,
                score,
                source_round,
                now,
                now,
            ),
        )
    elif source_round is not None:
        conn.execute(
            """
            UPDATE candidate_review_items
            SET source_round = COALESCE(source_round, ?), updated_at = ?
            WHERE review_item_id = ?
            """,
            (source_round, now, review_item_id),
        )
    conn.execute(
        """
        INSERT INTO candidate_evidence (
            evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
            source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
            runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
            matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'card', ?, ?, ?, ?, 'card_recommended', ?, ?, ?, ?, '[]', ?)
        ON CONFLICT(evidence_id) DO UPDATE SET
            review_item_id = excluded.review_item_id,
            provider_candidate_key_hash = excluded.provider_candidate_key_hash,
            runtime_identity_id = excluded.runtime_identity_id,
            resume_id = excluded.resume_id,
            score = excluded.score,
            fit_bucket = excluded.fit_bucket,
            matched_must_haves_json = excluded.matched_must_haves_json,
            matched_preferences_json = excluded.matched_preferences_json,
            missing_risks_json = excluded.missing_risks_json,
            strengths_json = excluded.strengths_json
        """,
        (
            source_evidence_id,
            review_item_id,
            DEFAULT_TENANT_ID,
            context.session.workspace_id,
            context.session.owner_user_id,
            context.session.session_id,
            source_run_id,
            safe_provider_key_hash,
            identity_id,
            workbench_resume_id,
            score,
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
        source_run_id=source_run_id,
        source_kind="liepin",
        event_name="candidate_review_item_upserted",
        payload={
            "reviewItemId": review_item_id,
            "sourceRunId": source_run_id,
            "sourceKind": "liepin",
            "candidateId": workbench_resume_id,
            "evidenceLevel": "card",
            "autoDetailRecommended": True,
        },
    )
    return review_item_id, safe_provider_key_hash


def _runtime_detail_recommendation_note(recommendation: Mapping[str, object]) -> str:
    score = _int_or_none(recommendation.get("value_score"))
    reason_codes: list[str] = []
    for value in _object_list(recommendation.get("safe_reason_codes")):
        reason_code = _safe_candidate_text(value, 80)
        if reason_code:
            reason_codes.append(reason_code)
    parts = ["Agent recommends opening detail before outreach."]
    if score is not None:
        parts.append(f"value score: {score}.")
    if reason_codes:
        parts.append(f"reasons: {', '.join(reason_codes[:4])}.")
    return " ".join(parts)


def _finalizer_candidate_by_resume_id(artifacts: object) -> dict[str, object]:
    final_result = getattr(artifacts, "final_result", None)
    result: dict[str, object] = {}
    for candidate in list(getattr(final_result, "candidates", []) or []):
        resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 128)
        if resume_id:
            result[resume_id] = candidate
    return result


def _runtime_fallback_final_evidence(
    *,
    identity_id: str,
    canonical_resume_id: str,
    source_kind: str,
    evidence_id: str,
) -> _RuntimeFallbackEvidence:
    return _RuntimeFallbackEvidence(
        evidence_id=evidence_id,
        source=source_kind,
        evidence_level="final",
        candidate_resume_id=canonical_resume_id,
        provider_candidate_key_hash=_sha256_text(f"{identity_id}:{canonical_resume_id}"),
    )


def _cts_cards_scanned_count(*, artifacts: object, fallback: int) -> int:
    candidate_store = getattr(artifacts, "candidate_store", None)
    if isinstance(candidate_store, Mapping):
        return len(candidate_store)
    return fallback


def _runtime_source_lane_event_payload(event: object) -> dict[str, object] | None:
    serializer = getattr(event, "to_public_payload", None)
    payload = serializer() if callable(serializer) else event
    if not isinstance(payload, Mapping):
        return None
    required_keys = {
        "schema_version",
        "source_lane_run_id",
        "event_seq",
        "event_type",
        "attempt",
    }
    if not required_keys.issubset(payload):
        return None
    safe_payload = redact_event_payload(dict(payload))
    return safe_payload if isinstance(safe_payload, dict) else None


def _snapshot_payload(snapshot: object) -> Mapping[str, object]:
    payload = _attr(snapshot, "raw_payload")
    if not isinstance(payload, Mapping):
        return {}
    return {str(key): value for key, value in payload.items()}


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


def _requirement_sheet_for_projection(
    context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext | WorkbenchLiepinDetailOpenJobContext,
) -> RequirementSheet:
    sheet = context.requirement_review.requirement_sheet
    if sheet is None:
        raise PermissionError("requirement_review_empty")
    return sheet


def _matched_terms(terms: list[str], text: str) -> list[str]:
    normalized = text.casefold()
    return _unique_list(term for term in terms if term.casefold() in normalized)


def _event_source_kind(value: object) -> Literal["cts", "liepin"] | None:
    if value == "cts":
        return "cts"
    if value == "liepin":
        return "liepin"
    return None


def _runtime_public_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    status = value.strip()
    if status in {"pending", "running", "completed", "partial", "blocked", "failed", "cancelled"}:
        return status
    return None


def _canonical_note_writer_lease_time(value: str) -> tuple[str, datetime]:
    parsed = _parse_iso(value)
    return _iso(parsed), parsed
