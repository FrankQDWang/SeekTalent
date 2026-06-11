from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Literal, Protocol

from seektalent_ui.redaction import redact_event_payload
from seektalent_ui.workbench_store_helpers import (
    attr as _attr,
    bounded_text as _bounded_text,
    int_or_none as _int_or_none,
    iso as _iso,
    json_to_dict as _json_to_dict,
    now_iso as _now_iso,
    object_list as _object_list,
    parse_iso as _parse_iso,
    safe_candidate_text as _safe_candidate_text,
)
from seektalent_ui.workbench_store_types import (
    DEFAULT_TENANT_ID,
    DETAIL_OPEN_LEASE_SECONDS,
    LIEPIN_DAILY_DETAIL_OPEN_LIMIT,
    DetailOpenMode,
    DetailOpenRequestStatus,
    WorkbenchDetailOpenCandidateSnapshot,
    WorkbenchDetailOpenLedger,
    WorkbenchDetailOpenRequest,
    WorkbenchEvent,
    WorkbenchLiepinDetailOpenJobContext,
    WorkbenchProviderAction,
    WorkbenchRequirementReview,
    WorkbenchSecurityAuditEvent,
    WorkbenchSession,
    WorkbenchSourceRun,
    WorkbenchSourceRunJobContext,
    WorkbenchSourceRunPolicy,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]


class SessionExistsForUser(Protocol):
    def __call__(self, conn: sqlite3.Connection, *, user: WorkbenchUser, session_id: str) -> bool:
        raise NotImplementedError


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


class AppendRuntimeSourceLaneEvent(Protocol):
    def __call__(
        self,
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
        metadata: Mapping[str, object] | None = None,
        created_at: str | None = None,
    ) -> WorkbenchSecurityAuditEvent:
        raise NotImplementedError


class PersistLiepinDetailCandidateResults(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        result: object,
        now: str,
    ) -> list[str]:
        raise NotImplementedError


class DetailOpenCandidateSnapshotForReview(Protocol):
    def __call__(self, conn: sqlite3.Connection, review_item_id: str) -> WorkbenchDetailOpenCandidateSnapshot | None:
        raise NotImplementedError


class SourceRunsBySession(Protocol):
    def __call__(self, conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, list[WorkbenchSourceRun]]:
        raise NotImplementedError


class RequirementReviewsBySession(Protocol):
    def __call__(self, conn: sqlite3.Connection, session_ids: list[str]) -> dict[str, WorkbenchRequirementReview]:
        raise NotImplementedError


class SessionFromRow(Protocol):
    def __call__(
        self,
        row: sqlite3.Row,
        source_runs: list[WorkbenchSourceRun],
        requirement_review: WorkbenchRequirementReview,
    ) -> WorkbenchSession:
        raise NotImplementedError


class LeaseLiepinDetailOpenRequest(Protocol):
    def __call__(self, conn: sqlite3.Connection, *, request_id: str, now: str) -> str | None:
        raise NotImplementedError


class CreateAutoLiepinDetailOpenRequest(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchSourceRunJobContext,
        connection_id: str,
        evidence_id: str,
        review_item_id: str,
        provider_key_hash: str,
        policy: WorkbenchSourceRunPolicy,
        decision_note: str,
        detail_candidates_json: str | None,
        now: str,
    ) -> str | None:
        raise NotImplementedError


class SourceRunPolicyForUser(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchSourceRunPolicy:
        raise NotImplementedError


class ConnectedLiepinConnectionForOwner(Protocol):
    def __call__(self, conn: sqlite3.Connection, *, workspace_id: str, user_id: str) -> sqlite3.Row | None:
        raise NotImplementedError


class DetailCandidatesJson(Protocol):
    def __call__(
        self,
        *,
        candidate_id: str,
        provider_candidate_key_hash: str | None,
        value_score: int | None,
    ) -> str:
        raise NotImplementedError


class DetailCandidatesJsonFromRuntimeRecommendation(Protocol):
    def __call__(self, recommendation: Mapping[str, object]) -> str:
        raise NotImplementedError


class WorkbenchDetailOpenStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        append_workbench_event: AppendWorkbenchEvent,
        append_runtime_source_lane_event: AppendRuntimeSourceLaneEvent,
        append_security_audit_event: AppendSecurityAuditEvent,
        session_exists_for_user: SessionExistsForUser,
        source_runs_by_session: SourceRunsBySession,
        requirement_reviews_by_session: RequirementReviewsBySession,
        session_from_row: SessionFromRow,
        persist_liepin_detail_candidate_results: PersistLiepinDetailCandidateResults,
        detail_open_candidate_snapshot: DetailOpenCandidateSnapshotForReview,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._append_workbench_event_conn = append_workbench_event
        self._append_runtime_source_lane_event_conn = append_runtime_source_lane_event
        self._append_security_audit_event_conn = append_security_audit_event
        self._session_exists_for_user_conn = session_exists_for_user
        self._source_runs_by_session = source_runs_by_session
        self._requirement_reviews_by_session = requirement_reviews_by_session
        self._session_from_row = session_from_row
        self._persist_liepin_detail_candidate_results_conn = persist_liepin_detail_candidate_results
        self._detail_open_candidate_snapshot_conn = detail_open_candidate_snapshot

    @property
    def lease_liepin_detail_open_request_conn(self) -> LeaseLiepinDetailOpenRequest:
        return self._lease_liepin_detail_open_request_conn

    @property
    def create_auto_liepin_detail_open_request_conn(self) -> CreateAutoLiepinDetailOpenRequest:
        return self._create_auto_liepin_detail_open_request_conn

    @property
    def source_run_policy_for_user_conn(self) -> SourceRunPolicyForUser:
        return _source_run_policy_for_user_conn

    @property
    def connected_liepin_connection_for_owner_conn(self) -> ConnectedLiepinConnectionForOwner:
        return _connected_liepin_connection_for_owner_conn

    @property
    def detail_candidates_json(self) -> DetailCandidatesJson:
        return _detail_candidates_json

    @property
    def detail_candidates_json_from_runtime_recommendation(self) -> DetailCandidatesJsonFromRuntimeRecommendation:
        return _detail_candidates_json_from_runtime_recommendation

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
                self._append_workbench_event_conn(
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


    def get_liepin_source_run_policy(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchSourceRunPolicy | None:
        self._initialize()
        with self._connect() as conn:
            if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
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
            if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
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
            self._append_workbench_event_conn(
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
            self._append_security_audit_event_conn(
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
                return self._detail_open_request_from_row_conn(conn, existing)
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
            self._append_workbench_event_conn(
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
            self._append_security_audit_event_conn(
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
            result = self._detail_open_request_from_row_conn(conn, row)
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
            self._append_security_audit_event_conn(
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
            result = self._detail_open_request_from_row_conn(conn, refreshed)
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
            self._append_workbench_event_conn(
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
            self._append_security_audit_event_conn(
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
            return self._detail_open_request_from_row_conn(conn, refreshed)


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
            return [self._detail_open_request_from_row_conn(conn, row) for row in rows]


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
            source_runs = self._source_runs_by_session(conn, [intent["session_id"]]).get(intent["session_id"], [])
            requirement_review = self._requirement_reviews_by_session(conn, [intent["session_id"]])[intent["session_id"]]
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
                session=self._session_from_row(session_row, source_runs, requirement_review),
                requirement_review=requirement_review,
            )
            self._append_workbench_event_conn(
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
                self._append_runtime_source_lane_event_conn(
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
            self._append_workbench_event_conn(
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
            self._append_workbench_event_conn(
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
            self._append_workbench_event_conn(
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
            self._append_security_audit_event_conn(
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
            self._block_detail_open_request_conn(conn, row=row, reason="active_detail_open_lease", now=now)
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
            self._block_detail_open_request_conn(conn, row=row, reason="detail_budget_exhausted", now=now)
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
            self._block_detail_open_request_conn(conn, row=row, reason="active_detail_open_lease", now=now)
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
        self._append_workbench_event_conn(
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


    def _create_auto_liepin_detail_open_request_conn(
        self,
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
        self._append_workbench_event_conn(
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
        self._append_security_audit_event_conn(
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


    def _detail_open_request_from_row_conn(
        self,
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
            candidate=self._detail_open_candidate_snapshot_conn(conn, row["review_item_id"]),
            blocked_reason=row["blocked_reason"],
            ledger=ledger,
            provider_action=provider_action,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


    def _block_detail_open_request_conn(
        self,
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
        self._append_workbench_event_conn(
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
        self._append_security_audit_event_conn(
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


def _detail_idempotency_key(*, session_id: str, review_item_id: str, idempotency_key: str | None) -> str:
    explicit_key = _bounded_text(idempotency_key, 128)
    if explicit_key:
        return f"{session_id}:{explicit_key}"
    return f"{session_id}:{review_item_id}"


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


def _source_run_policy_for_user_conn(
    conn: sqlite3.Connection,
    *,
    user: WorkbenchUser,
    session_id: str,
) -> WorkbenchSourceRunPolicy:
    return _source_run_policy_from_row(
        _source_run_policy_row_conn(conn, user=user, session_id=session_id),
        session_id=session_id,
    )


def _liepin_connection_for_user_conn(conn: sqlite3.Connection, *, user: WorkbenchUser) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM source_connections
        WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND source_kind = 'liepin'
        """,
        (DEFAULT_TENANT_ID, user.workspace_id, user.user_id),
    ).fetchone()


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


def _budget_day(now: str) -> str:
    return _parse_iso(now).date().isoformat()


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
