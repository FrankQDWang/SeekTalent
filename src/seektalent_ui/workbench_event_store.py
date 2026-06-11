from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from seektalent.runtime.public_events import normalize_runtime_public_event, runtime_public_event_name
from seektalent_ui.models import WorkbenchNoteCreatedPayload, WorkbenchNoteKind, WorkbenchNoteStatusHint
from seektalent_ui.redaction import redact_event_payload
from seektalent_ui.workbench_store_helpers import (
    bounded_text as _bounded_text,
    int_or_none as _int_or_none,
    iso as _iso,
    json_to_dict as _json_to_dict,
    like_prefix as _like_prefix,
    mapping_get as _mapping_get,
    now_iso as _now_iso,
    parse_iso as _parse_iso,
    safe_candidate_text as _safe_candidate_text,
)
from seektalent_ui.workbench_store_types import (
    DEFAULT_TENANT_ID,
    RuntimeSourceCountProjection,
    WorkbenchEvent,
    WorkbenchRuntimeSourceLaneLatestState,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]

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


@dataclass
class _RuntimeSourceCountProjectionState:
    status_seq: int = -1
    count_seq: int = -1
    status: str | None = None
    warning_code: str | None = None
    cards_scanned_count: int | None = None
    unique_candidates_count: int | None = None


class SessionExistsForIds(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
    ) -> bool:
        raise NotImplementedError


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


class RuntimePublicEventByIdempotency(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> sqlite3.Row | None:
        raise NotImplementedError


class WorkbenchEventStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        session_exists_for_ids: SessionExistsForIds,
        session_exists_for_user: SessionExistsForUser,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._session_exists_for_ids_conn = session_exists_for_ids
        self._session_exists_for_user_conn = session_exists_for_user

    @property
    def append_workbench_event_conn(self) -> AppendWorkbenchEvent:
        return _append_workbench_event_conn

    @property
    def append_runtime_source_lane_event_conn(self) -> AppendRuntimeSourceLaneEvent:
        return _append_runtime_source_lane_event_conn

    @property
    def runtime_public_event_by_idempotency_conn(self) -> RuntimePublicEventByIdempotency:
        return _runtime_public_event_by_idempotency_conn

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
                if not self._session_exists_for_ids_conn(
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
                event_payload = {str(key): value for key, value in payload.items()}
                before = self.append_runtime_public_event_by_ids(
                    tenant_id=DEFAULT_TENANT_ID,
                    workspace_id=context.session.workspace_id,
                    user_id=context.session.owner_user_id,
                    session_id=context.session.session_id,
                    source_kind=_event_source_kind(payload.get("sourceKind")),
                    payload=event_payload,
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
                    returned = _int_or_none(_mapping_get(counts, "sourceCumulativeReturned"))
                    identities = _int_or_none(_mapping_get(counts, "sourceCumulativeIdentities"))
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
                if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
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
                if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
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


def _workbench_note_status_hint(value: str) -> WorkbenchNoteStatusHint:
    text = _bounded_text(value, 64)
    if text == "new_progress":
        return "new_progress"
    if text == "waiting":
        return "waiting"
    if text == "human_action_required":
        return "human_action_required"
    if text == "completed":
        return "completed"
    if text == "failed":
        return "failed"
    if text == "canceled":
        return "canceled"
    return "unknown"


def _workbench_note_kind(value: str) -> WorkbenchNoteKind:
    text = _bounded_text(value, 64)
    if text == "waiting":
        return "waiting"
    if text == "human_action":
        return "human_action"
    if text == "terminal":
        return "terminal"
    return "progress"
