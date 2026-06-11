from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Literal, Protocol

from seektalent_ui.redaction import redact_text
from seektalent_ui.workbench_store_helpers import bounded_text as _bounded_text
from seektalent_ui.workbench_store_helpers import json_to_list as _json_to_list
from seektalent_ui.workbench_store_helpers import now_iso as _now_iso
from seektalent_ui.workbench_store_types import (
    DEFAULT_TENANT_ID,
    WorkbenchEvent,
    WorkbenchRequirementReview,
    WorkbenchRuntimeSourcingJob,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchSession,
    WorkbenchSourceRun,
    WorkbenchSourceRunJob,
    WorkbenchSourceRunJobContext,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]
SourceRunsBySession = Callable[[sqlite3.Connection, list[str]], dict[str, list[WorkbenchSourceRun]]]
RequirementReviewsBySession = Callable[[sqlite3.Connection, list[str]], dict[str, WorkbenchRequirementReview]]
SessionFromRow = Callable[[sqlite3.Row, list[WorkbenchSourceRun], WorkbenchRequirementReview], WorkbenchSession]
PersistRuntimeFinalCandidateResults = Callable[..., dict[str, int]]
PersistCtsCandidateResults = Callable[..., list[str]]


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


class WorkbenchJobStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        append_workbench_event: AppendWorkbenchEvent,
        source_runs_by_session: SourceRunsBySession,
        requirement_reviews_by_session: RequirementReviewsBySession,
        session_from_row: SessionFromRow,
        persist_runtime_final_candidate_results_conn: PersistRuntimeFinalCandidateResults,
        persist_cts_candidate_results_conn: PersistCtsCandidateResults,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._append_workbench_event_conn = append_workbench_event
        self._source_runs_by_session = source_runs_by_session
        self._requirement_reviews_by_session = requirement_reviews_by_session
        self._session_from_row = session_from_row
        self._persist_runtime_final_candidate_results_conn = persist_runtime_final_candidate_results_conn
        self._persist_cts_candidate_results_conn = persist_cts_candidate_results_conn

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
            requirement_review = self._requirement_reviews_by_session(conn, [session_id])[session_id]
            if requirement_review.status != "approved":
                raise PermissionError("requirement_review_not_approved")
            if requirement_review.requirement_sheet is None:
                raise PermissionError("requirement_review_empty")
            source_runs = self._source_runs_by_session(conn, [session_id]).get(session_id, [])
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
            self._append_workbench_event_conn(
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
            source_runs = self._source_runs_by_session(conn, [row["session_id"]]).get(row["session_id"], [])
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
                self._append_workbench_event_conn(
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
            self._append_workbench_event_conn(
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
            source_runs = self._source_runs_by_session(conn, [row["session_id"]]).get(row["session_id"], [])
            requirement_review = self._requirement_reviews_by_session(conn, [row["session_id"]])[row["session_id"]]
        return WorkbenchRuntimeSourcingJobContext(
            job=job,
            session=self._session_from_row(session_row, source_runs, requirement_review),
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
            self._append_workbench_event_conn(
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
                self._append_workbench_event_conn(
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
                source_runs = self._source_runs_by_session(conn, [row["session_id"]]).get(row["session_id"], [])
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
                self._append_workbench_event_conn(
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


def _runtime_run_id_from_artifacts(artifacts: object) -> str | None:
    value = getattr(artifacts, "run_id", None)
    if not isinstance(value, str):
        return None
    runtime_run_id = value.strip()
    return runtime_run_id or None


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
        source_kind = _runtime_source_kind(value)
        if source_kind is not None:
            source_kinds.append(source_kind)
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
        source_kind = _runtime_source_kind(value)
        if source_kind is not None:
            source_kinds.append(source_kind)
    return _runtime_scoped_source_runs(
        source_runs=source_runs,
        source_run_ids=tuple(_json_to_list(row["source_run_ids_json"])),
        source_kinds=tuple(source_kinds),
    )


def _runtime_source_kind(value: object) -> Literal["cts", "liepin"] | None:
    if value == "cts":
        return "cts"
    if value == "liepin":
        return "liepin"
    return None
