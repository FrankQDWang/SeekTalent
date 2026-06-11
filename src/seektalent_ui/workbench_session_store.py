from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Literal, Protocol

from seektalent.models import RequirementSheet
from seektalent_ui.workbench_store_helpers import now_iso as _now_iso
from seektalent_ui.workbench_store_types import (
    DEFAULT_TENANT_ID,
    LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    WorkbenchEvent,
    WorkbenchRequirementReview,
    WorkbenchRuntimeLinkRepairResult,
    WorkbenchSession,
    WorkbenchSourceRun,
    WorkbenchSourceRunJobContext,
    WorkbenchSourceRunRuntimeLink,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]


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


class WorkbenchSessionStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        append_workbench_event: AppendWorkbenchEvent,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._append_workbench_event_conn = append_workbench_event

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
            self._append_workbench_event_conn(
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
            self._append_workbench_event_conn(
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
            self._append_workbench_event_conn(
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
            self._append_workbench_event_conn(
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


def _session_exists_for_user_conn(
    conn: sqlite3.Connection,
    *,
    user: WorkbenchUser,
    session_id: str,
) -> bool:
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
