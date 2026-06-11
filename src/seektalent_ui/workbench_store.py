from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from seektalent.models import RequirementSheet
from seektalent_ui import workbench_store_helpers as _workbench_store_helpers
from seektalent_ui.models import WorkbenchNoteKind, WorkbenchNoteStatusHint
from seektalent_ui.redaction import redact_event_payload, redact_text
from seektalent_ui.workbench_db import connect_workbench_db
from seektalent_ui.workbench_schema import initialize_workbench_schema
from seektalent_ui.workbench_security_audit_store import _append_security_audit_event_conn
from seektalent_ui.workbench_store_helpers import (
    attr as _attr,
    bounded_text as _bounded_text,
    first as _first,
    int_or_none as _int_or_none,
    iso as _iso,
    json_list as _json_list,
    json_to_dict as _json_to_dict,
    json_to_list as _json_to_list,
    mapping_get as _mapping_get,
    now_iso as _now_iso,
    object_list as _object_list,
    parse_iso as _parse_iso,
    safe_candidate_text as _safe_candidate_text,
    sha256_text as _sha256_text,
    stable_id as _stable_id,
)
from seektalent_ui.workbench_store_types import (
    BootstrapAlreadyCompleteError,
    CandidateEvidenceLevel,
    CandidateReviewStatus,
    DEFAULT_TENANT_ID,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    DETAIL_OPEN_LEASE_SECONDS,
    DetailOpenLedgerStatus,
    DetailOpenMode,
    DetailOpenRequestStatus,
    GraphCandidateRecoveryState,
    LIEPIN_AUTO_DETAIL_REQUEST_LIMIT,
    LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD,
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    LIEPIN_DAILY_DETAIL_OPEN_LIMIT,
    LOGIN_ATTEMPT_EMAIL_MAX,
    LOGIN_ATTEMPT_IP_MAX,
    LOGIN_ATTEMPT_REASON_MAX,
    LOGIN_ATTEMPT_USER_AGENT_MAX,
    LOGIN_LOCKOUT_FAILURE_LIMIT,
    LOGIN_LOCKOUT_WINDOW_SECONDS,
    RuntimeLinkRepairStatus,
    RuntimeSourceCountProjection,
    SESSION_TTL_HOURS,
    SOURCE_CONNECTION_WARNING_MAX,
    SourceConnectionStatus,
    UserSessionTokens,
    WorkbenchCandidateEvidence,
    WorkbenchCandidateReviewItem,
    WorkbenchDetailOpenCandidateSnapshot,
    WorkbenchDetailOpenLedger,
    WorkbenchDetailOpenRequest,
    WorkbenchEvent,
    WorkbenchLiepinDetailOpenJobContext,
    WorkbenchProviderAction,
    WorkbenchRequirementReview,
    WorkbenchRuntimeCandidateIdentitySnapshot,
    WorkbenchRuntimeLinkRepairResult,
    WorkbenchRuntimeSourceLaneLatestState,
    WorkbenchRuntimeSourcingJob,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchSecurityAuditEvent,
    WorkbenchSession,
    WorkbenchSourceConnection,
    WorkbenchSourceRun,
    WorkbenchSourceRunJob,
    WorkbenchSourceRunJobContext,
    WorkbenchSourceRunPolicy,
    WorkbenchSourceRunRuntimeLink,
    WorkbenchUser,
    WorkbenchWorkspace,
)


__all__ = [
    "BootstrapAlreadyCompleteError",
    "CandidateEvidenceLevel",
    "CandidateReviewStatus",
    "DEFAULT_TENANT_ID",
    "DEFAULT_WORKSPACE_ID",
    "DEFAULT_WORKSPACE_NAME",
    "DETAIL_OPEN_LEASE_SECONDS",
    "DetailOpenLedgerStatus",
    "DetailOpenMode",
    "DetailOpenRequestStatus",
    "GraphCandidateRecoveryState",
    "LIEPIN_AUTO_DETAIL_REQUEST_LIMIT",
    "LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD",
    "LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE",
    "LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE",
    "LIEPIN_BROWSER_LOGIN_REQUIRED_CODE",
    "LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE",
    "LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE",
    "LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE",
    "LIEPIN_DAILY_DETAIL_OPEN_LIMIT",
    "LOGIN_ATTEMPT_EMAIL_MAX",
    "LOGIN_ATTEMPT_IP_MAX",
    "LOGIN_ATTEMPT_REASON_MAX",
    "LOGIN_ATTEMPT_USER_AGENT_MAX",
    "LOGIN_LOCKOUT_FAILURE_LIMIT",
    "LOGIN_LOCKOUT_WINDOW_SECONDS",
    "RuntimeLinkRepairStatus",
    "RuntimeSourceCountProjection",
    "SESSION_TTL_HOURS",
    "SOURCE_CONNECTION_WARNING_MAX",
    "SourceConnectionStatus",
    "UserSessionTokens",
    "WorkbenchCandidateEvidence",
    "WorkbenchCandidateReviewItem",
    "WorkbenchDetailOpenCandidateSnapshot",
    "WorkbenchDetailOpenLedger",
    "WorkbenchDetailOpenRequest",
    "WorkbenchEvent",
    "WorkbenchLiepinDetailOpenJobContext",
    "WorkbenchProviderAction",
    "WorkbenchRequirementReview",
    "WorkbenchRuntimeCandidateIdentitySnapshot",
    "WorkbenchRuntimeLinkRepairResult",
    "WorkbenchRuntimeSourceLaneLatestState",
    "WorkbenchRuntimeSourcingJob",
    "WorkbenchRuntimeSourcingJobContext",
    "WorkbenchSecurityAuditEvent",
    "WorkbenchSession",
    "WorkbenchSourceConnection",
    "WorkbenchSourceRun",
    "WorkbenchSourceRunJob",
    "WorkbenchSourceRunJobContext",
    "WorkbenchSourceRunPolicy",
    "WorkbenchSourceRunRuntimeLink",
    "WorkbenchStore",
    "WorkbenchUser",
    "WorkbenchWorkspace",
]


_now = _workbench_store_helpers.now
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


@dataclass
class _RuntimeSourceCountProjectionState:
    status_seq: int = -1
    count_seq: int = -1
    status: str | None = None
    warning_code: str | None = None
    cards_scanned_count: int | None = None
    unique_candidates_count: int | None = None


class WorkbenchStore:
    def __init__(self, db_path: str | Path) -> None:
        from seektalent_ui.workbench_auth_store import WorkbenchAuthStore
        from seektalent_ui.workbench_candidate_store import WorkbenchCandidateStore
        from seektalent_ui.workbench_connection_store import WorkbenchConnectionStore
        from seektalent_ui.workbench_detail_open_store import WorkbenchDetailOpenStore
        from seektalent_ui.workbench_event_store import WorkbenchEventStore
        from seektalent_ui.workbench_job_store import WorkbenchJobStore
        from seektalent_ui.workbench_security_audit_store import WorkbenchSecurityAuditStore
        from seektalent_ui.workbench_session_store import WorkbenchSessionStore

        self.db_path = Path(db_path)
        self._initialized = False
        self._security_audit = WorkbenchSecurityAuditStore(
            connect=self._connect,
            initialize=self._initialize,
        )
        self._events = WorkbenchEventStore(
            connect=self._connect,
            initialize=self._initialize,
            session_exists_for_ids=_session_exists_for_ids_conn,
            session_exists_for_user=_session_exists_for_user_conn,
        )
        self._sessions = WorkbenchSessionStore(
            connect=self._connect,
            initialize=self._initialize,
            append_workbench_event=self._events.append_workbench_event_conn,
        )
        self._connections = WorkbenchConnectionStore(
            connect=self._connect,
            initialize=self._initialize,
            append_security_audit_event=_append_security_audit_event_conn,
            append_workbench_event=self._events.append_workbench_event_conn,
        )

        def persist_liepin_detail_candidate_results(
            conn: sqlite3.Connection,
            *,
            context: WorkbenchLiepinDetailOpenJobContext,
            result: object,
            now: str,
        ) -> list[str]:
            return self._candidates.persist_liepin_detail_candidate_results_conn(
                conn,
                context=context,
                result=result,
                now=now,
            )

        def detail_open_candidate_snapshot(
            conn: sqlite3.Connection,
            review_item_id: str,
        ) -> WorkbenchDetailOpenCandidateSnapshot | None:
            return self._candidates.detail_open_candidate_snapshot_conn(conn, review_item_id)

        self._detail_open = WorkbenchDetailOpenStore(
            connect=self._connect,
            initialize=self._initialize,
            append_workbench_event=self._events.append_workbench_event_conn,
            append_runtime_source_lane_event=self._events.append_runtime_source_lane_event_conn,
            append_security_audit_event=_append_security_audit_event_conn,
            session_exists_for_user=_session_exists_for_user_conn,
            source_runs_by_session=_source_runs_by_session,
            requirement_reviews_by_session=_requirement_reviews_by_session,
            session_from_row=_session_from_row,
            persist_liepin_detail_candidate_results=persist_liepin_detail_candidate_results,
            detail_open_candidate_snapshot=detail_open_candidate_snapshot,
        )
        self._candidates = WorkbenchCandidateStore(
            connect=self._connect,
            initialize=self._initialize,
            append_workbench_event=self._events.append_workbench_event_conn,
            append_runtime_source_lane_event=self._events.append_runtime_source_lane_event_conn,
            session_exists_for_user=_session_exists_for_user_conn,
            source_run_policy_for_user=self._detail_open.source_run_policy_for_user_conn,
            connected_liepin_connection_for_owner=self._detail_open.connected_liepin_connection_for_owner_conn,
            create_auto_liepin_detail_open_request=self._detail_open.create_auto_liepin_detail_open_request_conn,
            lease_liepin_detail_open_request=self._detail_open.lease_liepin_detail_open_request_conn,
            detail_candidates_json=self._detail_open.detail_candidates_json,
            detail_candidates_json_from_runtime_recommendation=self._detail_open.detail_candidates_json_from_runtime_recommendation,
        )
        self._jobs = WorkbenchJobStore(
            connect=self._connect,
            initialize=self._initialize,
            append_workbench_event=self._events.append_workbench_event_conn,
            source_runs_by_session=_source_runs_by_session,
            requirement_reviews_by_session=_requirement_reviews_by_session,
            session_from_row=_session_from_row,
            persist_runtime_final_candidate_results_conn=self._candidates.persist_runtime_final_candidate_results_conn,
            persist_cts_candidate_results_conn=self._candidates.persist_cts_candidate_results_conn,
        )
        self._auth = WorkbenchAuthStore(
            connect=self._connect,
            initialize=self._initialize,
            user_from_row=_user_from_row,
            append_security_audit_event=_append_security_audit_event_conn,
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
        self._security_audit.record_security_audit_event(
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
        return self._security_audit.list_security_audit_events()

    def list_security_audit_events_for_user(
        self,
        *,
        user: WorkbenchUser,
        limit: int = 200,
    ) -> list[WorkbenchSecurityAuditEvent]:
        return self._security_audit.list_security_audit_events_for_user(user=user, limit=limit)

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
        return self._sessions.create_workbench_session(
            user=user,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_kinds=source_kinds,
        )

    def list_workbench_sessions(self, *, user: WorkbenchUser) -> list[WorkbenchSession]:
        self._jobs.reconcile_expired_runtime_sourcing_jobs()
        return self._sessions.list_workbench_sessions(user=user)

    def get_workbench_session(self, *, user: WorkbenchUser, session_id: str) -> WorkbenchSession | None:
        self._jobs.reconcile_expired_runtime_sourcing_jobs()
        return self._sessions.get_workbench_session(user=user, session_id=session_id)

    def list_runtime_source_lane_latest_state(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> list[WorkbenchRuntimeSourceLaneLatestState]:
        return self._events.list_runtime_source_lane_latest_state(user=user, session_id=session_id)

    def list_source_connections(self, *, user: WorkbenchUser) -> list[WorkbenchSourceConnection]:
        return self._connections.list_source_connections(user=user)

    def get_source_connection(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
    ) -> WorkbenchSourceConnection | None:
        return self._connections.get_source_connection(user=user, connection_id=connection_id)

    def get_or_create_liepin_source_connection(
        self,
        *,
        user: WorkbenchUser,
    ) -> tuple[WorkbenchSourceConnection, bool]:
        return self._connections.get_or_create_liepin_source_connection(user=user)

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
        return self._connections.start_liepin_login_handoff(
            user=user,
            connection_id=connection_id,
            provider_account_hash=provider_account_hash,
            compliance_gate_ref=compliance_gate_ref,
            warning_code=warning_code,
            warning_message=warning_message,
        )

    def mark_liepin_connection_connected(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        provider_account_hash: str | None,
        compliance_gate_ref: str | None = None,
    ) -> WorkbenchSourceConnection | None:
        return self._connections.mark_liepin_connection_connected(
            user=user,
            connection_id=connection_id,
            provider_account_hash=provider_account_hash,
            compliance_gate_ref=compliance_gate_ref,
        )

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
        return self._connections.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection_id,
            warning_code=warning_code,
            warning_message=warning_message,
            session_id=session_id,
            source_run_id=source_run_id,
        )

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
        return self._connections.mark_liepin_connection_connected_for_source_run(
            user=user,
            connection_id=connection_id,
            session_id=session_id,
            source_run_id=source_run_id,
            provider_account_hash=provider_account_hash,
            compliance_gate_ref=compliance_gate_ref,
        )

    def mark_liepin_connection_connected_without_source_runs(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        provider_account_hash: str | None,
        compliance_gate_ref: str | None = None,
    ) -> WorkbenchSourceConnection | None:
        return self._connections.mark_liepin_connection_connected_without_source_runs(
            user=user,
            connection_id=connection_id,
            provider_account_hash=provider_account_hash,
            compliance_gate_ref=compliance_gate_ref,
        )

    def get_liepin_source_connection_for_job_context(
        self,
        *,
        context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext,
    ) -> WorkbenchSourceConnection | None:
        return self._connections.get_liepin_source_connection_for_job_context(context=context)

    def get_requirement_review(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchRequirementReview | None:
        return self._sessions.get_requirement_review(user=user, session_id=session_id)

    def update_requirement_review(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        requirement_sheet: RequirementSheet,
    ) -> WorkbenchRequirementReview | None:
        return self._sessions.update_requirement_review(
            user=user,
            session_id=session_id,
            requirement_sheet=requirement_sheet,
        )

    def approve_requirement_review(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchRequirementReview | None:
        return self._sessions.approve_requirement_review(user=user, session_id=session_id)

    def block_source_run_for_start_probe(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_run_id: str,
        warning_code: str,
        warning_message: str,
    ) -> WorkbenchSourceRun | None:
        return self._sessions.block_source_run_for_start_probe(
            user=user,
            session_id=session_id,
            source_run_id=source_run_id,
            warning_code=warning_code,
            warning_message=warning_message,
        )

    def start_runtime_sourcing_job(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[WorkbenchRuntimeSourcingJob, bool] | None:
        return self._jobs.start_runtime_sourcing_job(
            user=user,
            session_id=session_id,
            idempotency_key=idempotency_key,
        )

    def claim_next_runtime_sourcing_job(
        self,
        *,
        owner_id: str,
        lease_expires_at: str,
    ) -> WorkbenchRuntimeSourcingJobContext | None:
        return self._jobs.claim_next_runtime_sourcing_job(
            owner_id=owner_id,
            lease_expires_at=lease_expires_at,
        )

    def extend_runtime_sourcing_job_lease(self, *, job_id: str, owner_id: str, lease_expires_at: str) -> bool:
        return self._jobs.extend_runtime_sourcing_job_lease(
            job_id=job_id,
            owner_id=owner_id,
            lease_expires_at=lease_expires_at,
        )

    def attach_runtime_sourcing_job_runtime_run_id(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        runtime_run_id: str,
    ) -> None:
        self._jobs.attach_runtime_sourcing_job_runtime_run_id(
            context=context,
            runtime_run_id=runtime_run_id,
        )

    def complete_runtime_sourcing_job_with_artifacts(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
    ) -> None:
        self._jobs.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)

    def refresh_runtime_candidate_index_with_artifacts(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
    ) -> None:
        self._jobs.refresh_runtime_candidate_index_with_artifacts(context=context, artifacts=artifacts)

    def fail_runtime_sourcing_job(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        error_message: str,
    ) -> None:
        self._jobs.fail_runtime_sourcing_job(context=context, error_message=error_message)

    def _finish_runtime_sourcing_job(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        status: Literal["completed", "failed"],
        error_message: str | None,
        artifacts: object | None,
    ) -> None:
        self._jobs._finish_runtime_sourcing_job(
            context=context,
            status=status,
            error_message=error_message,
            artifacts=artifacts,
        )

    def reconcile_expired_runtime_sourcing_jobs(self) -> int:
        return self._jobs.reconcile_expired_runtime_sourcing_jobs()

    def reconcile_expired_detail_open_leases(self) -> int:
        return self._detail_open.reconcile_expired_detail_open_leases()

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
        return self._events.append_workbench_event(
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
        return self._events.append_runtime_public_event_by_ids(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            source_kind=source_kind,
            payload=payload,
        )

    def reconcile_runtime_public_events_from_artifacts(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
    ) -> int:
        return self._events.reconcile_runtime_public_events_from_artifacts(context=context, artifacts=artifacts)

    def latest_runtime_source_count_projection(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> dict[Literal["cts", "liepin"], RuntimeSourceCountProjection]:
        return self._events.latest_runtime_source_count_projection(user=user, session_id=session_id)

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
        return self._events.try_append_workbench_note(
            user=user,
            session_id=session_id,
            idempotency_key=idempotency_key,
            text=text,
            status_hint=status_hint,
            note_kind=note_kind,
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
        return self._events.claim_workbench_note_writer_lease(
            user=user,
            session_id=session_id,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            now=now,
            in_flight_started_at=in_flight_started_at,
            last_tick_slot=last_tick_slot,
        )

    def release_workbench_note_writer_lease(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        lease_owner: str,
    ) -> bool:
        return self._events.release_workbench_note_writer_lease(
            user=user,
            session_id=session_id,
            lease_owner=lease_owner,
        )

    def attach_source_run_runtime_run_id(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        runtime_run_id: str,
    ) -> None:
        self._sessions.attach_source_run_runtime_run_id(context=context, runtime_run_id=runtime_run_id)

    def repair_cts_source_run_runtime_link(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_run_id: str,
        runtime_run_id: str | None = None,
    ) -> WorkbenchRuntimeLinkRepairResult:
        return self._sessions.repair_cts_source_run_runtime_link(
            user=user,
            session_id=session_id,
            source_run_id=source_run_id,
            runtime_run_id=runtime_run_id,
        )

    def get_scoped_source_run_runtime_link(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_kind: Literal["cts", "liepin"],
    ) -> WorkbenchSourceRunRuntimeLink | None:
        return self._sessions.get_scoped_source_run_runtime_link(
            user=user,
            session_id=session_id,
            source_kind=source_kind,
        )

    def list_runtime_candidate_identity_snapshots(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        runtime_run_id: str,
    ) -> list[WorkbenchRuntimeCandidateIdentitySnapshot] | None:
        return self._candidates.list_runtime_candidate_identity_snapshots(
            user=user,
            session_id=session_id,
            runtime_run_id=runtime_run_id,
        )

    def list_workbench_events(self, *, user: WorkbenchUser, after_seq: int, limit: int = 100) -> list[WorkbenchEvent]:
        return self._events.list_workbench_events(user=user, after_seq=after_seq, limit=limit)

    def list_session_workbench_events(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        after_seq: int,
        limit: int = 100,
    ) -> list[WorkbenchEvent]:
        return self._events.list_session_workbench_events(
            user=user,
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
        )

    def list_all_session_workbench_events(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> list[WorkbenchEvent]:
        return self._events.list_all_session_workbench_events(user=user, session_id=session_id)

    def latest_workbench_event_seq(self, *, user: WorkbenchUser, session_id: str | None = None) -> int:
        return self._events.latest_workbench_event_seq(user=user, session_id=session_id)

    def list_recent_workbench_notes(self, *, user: WorkbenchUser, session_id: str, limit: int = 15) -> list[WorkbenchEvent]:
        return self._events.list_recent_workbench_notes(user=user, session_id=session_id, limit=limit)

    def list_recent_session_events(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        event_prefix: str,
        limit: int = 100,
    ) -> list[WorkbenchEvent]:
        return self._events.list_recent_session_events(
            user=user,
            session_id=session_id,
            event_prefix=event_prefix,
            limit=limit,
        )

    def persist_cts_candidate_results(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        artifacts: object,
    ) -> list[WorkbenchCandidateReviewItem]:
        return self._candidates.persist_cts_candidate_results(context=context, artifacts=artifacts)






    def list_candidate_review_items(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> list[WorkbenchCandidateReviewItem] | None:
        return self._candidates.list_candidate_review_items(user=user, session_id=session_id)

    def update_candidate_review_item(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
        review_status: CandidateReviewStatus | None,
        note: str | None,
    ) -> WorkbenchCandidateReviewItem | None:
        return self._candidates.update_candidate_review_item(
            user=user,
            session_id=session_id,
            review_item_id=review_item_id,
            review_status=review_status,
            note=note,
        )

    def get_liepin_source_run_policy(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchSourceRunPolicy | None:
        return self._detail_open.get_liepin_source_run_policy(user=user, session_id=session_id)

    def update_liepin_source_run_policy(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        detail_open_mode: DetailOpenMode,
    ) -> WorkbenchSourceRunPolicy | None:
        return self._detail_open.update_liepin_source_run_policy(
            user=user,
            session_id=session_id,
            detail_open_mode=detail_open_mode,
        )

    def create_liepin_detail_open_request(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
        idempotency_key: str | None,
    ) -> WorkbenchDetailOpenRequest | None:
        return self._detail_open.create_liepin_detail_open_request(
            user=user,
            session_id=session_id,
            review_item_id=review_item_id,
            idempotency_key=idempotency_key,
        )

    def approve_liepin_detail_open_request(
        self,
        *,
        user: WorkbenchUser,
        request_id: str,
    ) -> WorkbenchDetailOpenRequest | None:
        return self._detail_open.approve_liepin_detail_open_request(user=user, request_id=request_id)

    def reject_liepin_detail_open_request(
        self,
        *,
        user: WorkbenchUser,
        request_id: str,
        reason: str,
    ) -> WorkbenchDetailOpenRequest | None:
        return self._detail_open.reject_liepin_detail_open_request(user=user, request_id=request_id, reason=reason)

    def list_liepin_detail_open_requests(
        self,
        *,
        user: WorkbenchUser,
        session_id: str | None = None,
        status: DetailOpenRequestStatus | None = None,
        limit: int = 100,
    ) -> list[WorkbenchDetailOpenRequest]:
        return self._detail_open.list_liepin_detail_open_requests(
            user=user,
            session_id=session_id,
            status=status,
            limit=limit,
        )

    def claim_next_liepin_detail_open_intent(self) -> WorkbenchLiepinDetailOpenJobContext | None:
        return self._detail_open.claim_next_liepin_detail_open_intent()

    def complete_liepin_detail_open_intent_with_lane_result(
        self,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        result: object,
    ) -> None:
        self._detail_open.complete_liepin_detail_open_intent_with_lane_result(context=context, result=result)


    def fail_liepin_detail_open_intent(
        self,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        error_code: str,
        error_message: str,
    ) -> None:
        self._detail_open.fail_liepin_detail_open_intent(
            context=context,
            error_code=error_code,
            error_message=error_message,
        )

    def build_liepin_provider_open_action(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
    ) -> WorkbenchProviderAction | None:
        return self._detail_open.build_liepin_provider_open_action(
            user=user,
            session_id=session_id,
            review_item_id=review_item_id,
        )



    def list_runtime_final_top_review_items(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> tuple[int, list[WorkbenchCandidateReviewItem]] | None:
        return self._candidates.list_runtime_final_top_review_items(user=user, session_id=session_id)

    def has_active_runtime_sourcing_job(self, *, user: WorkbenchUser, session_id: str) -> bool:
        return self._jobs.has_active_runtime_sourcing_job(user=user, session_id=session_id)

    def has_runtime_sourcing_job(self, *, user: WorkbenchUser, session_id: str) -> bool:
        return self._jobs.has_runtime_sourcing_job(user=user, session_id=session_id)

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
