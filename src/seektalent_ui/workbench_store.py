from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from seektalent.models import RequirementSheet
from seektalent_ui import workbench_store_helpers as _workbench_store_helpers
from seektalent_ui.models import WorkbenchNoteKind, WorkbenchNoteStatusHint
from seektalent_ui.workbench_db import connect_workbench_db
from seektalent_ui.workbench_schema import initialize_workbench_schema
from seektalent_ui.workbench_security_audit_store import _append_security_audit_event_conn
from seektalent_ui.workbench_store_helpers import (
    now_iso as _now_iso,
)
from seektalent_ui.workbench_store_types import (
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
    RuntimeLinkRepairStatus,
    RuntimeSourceCountProjection,
    SOURCE_CONNECTION_WARNING_MAX,
    SourceConnectionStatus,
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
)


__all__ = [
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
    "RuntimeLinkRepairStatus",
    "RuntimeSourceCountProjection",
    "SOURCE_CONNECTION_WARNING_MAX",
    "SourceConnectionStatus",
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


class WorkbenchStore:
    def __init__(self, db_path: str | Path) -> None:
        from seektalent_ui.workbench_actor_store import WorkbenchActorStore
        from seektalent_ui.workbench_candidate_store import WorkbenchCandidateStore
        from seektalent_ui.workbench_connection_store import WorkbenchConnectionStore
        from seektalent_ui.workbench_detail_open_store import WorkbenchDetailOpenStore
        from seektalent_ui.workbench_event_store import WorkbenchEventStore
        from seektalent_ui.workbench_job_store import WorkbenchJobStore
        from seektalent_ui.workbench_security_audit_store import WorkbenchSecurityAuditStore
        from seektalent_ui.workbench_session_store import WorkbenchSessionStore

        self.db_path = Path(db_path)
        self._initialized = False
        self._actor = WorkbenchActorStore(
            connect=self._connect,
            initialize=self._initialize,
            user_from_row=_user_from_row,
        )
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
    def ensure_local_actor(self) -> WorkbenchUser:
        return self._actor.ensure_local_actor()

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

    def create_workbench_session(
        self,
        *,
        user: WorkbenchUser,
        job_title: str,
        jd_text: str,
        notes: str,
        source_kinds: list[Literal["cts", "liepin"]] | None = None,
        runtime_run_id: str | None = None,
    ) -> WorkbenchSession:
        return self._sessions.create_workbench_session(
            user=user,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_kinds=source_kinds,
            runtime_run_id=runtime_run_id,
        )

    def list_workbench_sessions(self, *, user: WorkbenchUser) -> list[WorkbenchSession]:
        return self._sessions.list_workbench_sessions(user=user)

    def get_workbench_session(self, *, user: WorkbenchUser, session_id: str) -> WorkbenchSession | None:
        return self._sessions.get_workbench_session(user=user, session_id=session_id)

    def get_workbench_session_by_runtime_run_id(
        self,
        *,
        user: WorkbenchUser,
        runtime_run_id: str,
    ) -> WorkbenchSession | None:
        return self._sessions.get_workbench_session_by_runtime_run_id(user=user, runtime_run_id=runtime_run_id)

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

    def complete_runtime_sourcing_job(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
    ) -> None:
        self._jobs.complete_runtime_sourcing_job(context=context)

    def complete_runtime_sourcing_job_with_runtime_result(
        self,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        runtime_result: object,
    ) -> None:
        self._jobs.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=runtime_result)

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

    def persist_runtime_candidate_truth_from_control(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        runtime_run_id: str,
        identities: Iterable[object],
        evidence: Iterable[object],
        finalization_revision: object,
        projected_at: str,
    ) -> str:
        return self._candidates.persist_runtime_candidate_truth_from_control(
            user=user,
            session_id=session_id,
            runtime_run_id=runtime_run_id,
            identities=identities,
            evidence=evidence,
            finalization_revision=finalization_revision,
            projected_at=projected_at,
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

    def list_recent_workbench_notes(
        self, *, user: WorkbenchUser, session_id: str, limit: int = 15
    ) -> list[WorkbenchEvent]:
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
            initialize_workbench_schema(conn, now=_now_iso(), database_path=self.db_path)
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


def _requirement_review_from_row(row: sqlite3.Row) -> WorkbenchRequirementReview:
    return WorkbenchRequirementReview(
        session_id=row["session_id"],
        status=row["status"],
        requirement_sheet=_requirement_sheet_from_json(row["requirement_sheet_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        approved_at=row["approved_at"],
    )


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
        runtime_run_id=row["runtime_run_id"],
    )


def _requirement_sheet_from_json(value: object) -> RequirementSheet | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return RequirementSheet.model_validate(json.loads(value))


def _validate_requirement_sheet_for_session(session: WorkbenchSession, requirement_sheet: RequirementSheet) -> None:
    if requirement_sheet.job_title != session.job_title:
        raise ValueError("requirement_sheet_job_title_mismatch")
