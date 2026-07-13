from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from seektalent.source_references import SourceReference
from seektalent.sqlite_migrations import (
    SQLiteMigrationError,
    SQLiteMigrationStep,
    backup_sqlite_before_migration,
    require_supported_version,
    run_ordered_migrations,
    run_sqlite_integrity_checks,
)
from seektalent_runtime_control.candidates import candidate_truth_from_run_state
from seektalent_runtime_control.clock import max_iso_timestamp, timestamp_lte
from seektalent_runtime_control.errors import RuntimeControlError, RuntimeControlLookupError
from seektalent_runtime_control.fsm import require_run_transition
from seektalent_runtime_control.models import (
    RuntimeCheckpoint,
    RuntimeControlCandidateEvidence,
    RuntimeControlCandidateFinalizationRevision,
    RuntimeControlCandidateIdentity,
    RuntimeCommand,
    RuntimeControlEvent,
    RuntimeControlEventInput,
    RuntimeControlEventPage,
    RuntimeExecutorLease,
    RuntimeFinalSummary,
    RuntimeRunRecord,
    RuntimeRunSnapshot,
    RuntimeStageOutput,
    RuntimeStageOutputInput,
    RuntimeWorkerClaim,
)
from seektalent_runtime_control.requirements import (
    ApprovedRequirementRevision,
    RequirementAmendment,
    RequirementDraft,
    ReviewItem,
)
from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload


RUNTIME_CONTROL_SCHEMA_VERSION = 7
RUNTIME_CHECKPOINT_SCHEMA_VERSION = "runtime-control-checkpoint/v1"
RUNTIME_CONTROL_EVENT_SCHEMA_VERSION = "runtime-control-event/v1"
MAX_RUNTIME_CONTROL_JSON_BYTES = 16 * 1024
_RUNTIME_STAGE_OUTPUT_ARTIFACT_KIND = "runtime_stage_output"
_RUNTIME_STAGE_OUTPUT_ARTIFACT_DIR = "runtime_control_artifacts/stage_outputs"
_TERMINAL_RUN_STATUSES = ("cancelled", "completed", "failed")
_REQUIRED_STAGE_OUTPUT_KINDS = {
    "audit",
    "audit_summary",
    "candidate_evidence",
    "candidate_identity",
    "final_candidates",
    "final_shortlist",
    "final_summary",
    "runtime_public_round_query",
    "runtime_public_source_result",
    "runtime_public_merge",
    "runtime_public_scoring",
    "runtime_public_feedback",
    "runtime_public_finalization",
    "shortlist",
}


@dataclass(frozen=True)
class RuntimeCheckpointLoadFailure:
    checkpoint_id: str
    reason_code: str


class RuntimeControlStore:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            try:
                version = require_supported_version(
                    conn,
                    supported_version=RUNTIME_CONTROL_SCHEMA_VERSION,
                    store_name="runtime-control",
                )
            except SQLiteMigrationError as exc:
                raise RuntimeControlError(
                    "runtime_control_schema_unsupported",
                    str(exc),
                ) from exc
            if version == RUNTIME_CONTROL_SCHEMA_VERSION:
                return
            if version > 0:
                backup_sqlite_before_migration(
                    self.path,
                    backup_root=self.path.parent / "migration_backups",
                    store_name="runtime-control",
                    now=_migration_now(),
                )
            with conn:
                if version in {1, 2, 3, 4, 5, 6}:
                    run_ordered_migrations(
                        conn,
                        from_version=version,
                        to_version=RUNTIME_CONTROL_SCHEMA_VERSION,
                        migrations={
                            1: SQLiteMigrationStep(1, 2, _migrate_v1_to_v2),
                            2: SQLiteMigrationStep(2, 3, _migrate_v2_to_v3),
                            3: SQLiteMigrationStep(3, 4, _migrate_v3_to_v4),
                            4: SQLiteMigrationStep(4, 5, _migrate_v4_to_v5),
                            5: SQLiteMigrationStep(5, 6, _migrate_v5_to_v6),
                            6: SQLiteMigrationStep(6, 7, _migrate_v6_to_v7),
                        },
                        store_name="runtime-control",
                    )
                else:
                    _create_schema(conn)
                    conn.execute(f"PRAGMA user_version = {RUNTIME_CONTROL_SCHEMA_VERSION}")
                run_sqlite_integrity_checks(conn, store_name="runtime-control", foreign_keys=False)

    def create_run(self, run: RuntimeRunRecord) -> RuntimeRunRecord:
        stored = _normalize_run_record(run)
        with self._connect() as conn, conn:
            existing_by_intent = _run_row_by_run_intent(conn, stored.run_intent_id)
            existing_by_start_key = _run_row_by_start_idempotency_key(conn, stored.start_idempotency_key)
            if existing_by_intent is not None and existing_by_start_key is not None:
                if existing_by_intent["runtime_run_id"] != existing_by_start_key["runtime_run_id"]:
                    raise RuntimeControlError("runtime_run_start_idempotency_conflict")
                return _run_from_row(existing_by_intent)
            if existing_by_intent is not None:
                return _run_from_row(existing_by_intent)
            if existing_by_start_key is not None:
                return _run_from_row(existing_by_start_key)
            try:
                conn.execute(
                    """
                    INSERT INTO runtime_control_runs (
                        runtime_run_id, run_intent_id, start_idempotency_key, run_kind,
                        agent_conversation_id, workbench_session_id,
                        approved_requirement_revision_id, status, current_stage, current_round,
                        latest_checkpoint_id, latest_event_seq, source_ids_json, stop_reason_code,
                        created_at, updated_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stored.runtime_run_id,
                        stored.run_intent_id,
                        stored.start_idempotency_key,
                        stored.run_kind,
                        stored.agent_conversation_id,
                        stored.workbench_session_id,
                        stored.approved_requirement_revision_id,
                        stored.status,
                        stored.current_stage,
                        stored.current_round,
                        stored.latest_checkpoint_id,
                        stored.latest_event_seq,
                        _json(stored.source_ids),
                        stored.stop_reason_code,
                        stored.created_at,
                        stored.updated_at,
                        stored.completed_at,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = _run_row_by_run_intent(conn, stored.run_intent_id)
                if existing is None:
                    existing = _run_row_by_start_idempotency_key(conn, stored.start_idempotency_key)
                if existing is not None:
                    return _run_from_row(existing)
                raise
        return stored

    def get_run(self, runtime_run_id: str) -> RuntimeRunRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        return _run_from_row(row)

    def get_run_by_approved_requirement_revision(
        self,
        approved_requirement_revision_id: str,
    ) -> RuntimeRunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_control_runs
                WHERE approved_requirement_revision_id = ?
                ORDER BY created_at DESC, runtime_run_id DESC
                LIMIT 1
                """,
                (approved_requirement_revision_id,),
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def get_run_by_run_intent_id(self, run_intent_id: str) -> RuntimeRunRecord | None:
        with self._connect() as conn:
            row = _run_row_by_run_intent(conn, run_intent_id)
        return _run_from_row(row) if row is not None else None

    def get_run_by_start_idempotency_key(self, start_idempotency_key: str) -> RuntimeRunRecord | None:
        with self._connect() as conn:
            row = _run_row_by_start_idempotency_key(conn, start_idempotency_key)
        return _run_from_row(row) if row is not None else None

    def link_workbench_session(
        self,
        *,
        runtime_run_id: str,
        workbench_session_id: str,
        updated_at: str,
    ) -> RuntimeRunRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET workbench_session_id = ?, updated_at = ?
                WHERE runtime_run_id = ?
                """,
                (workbench_session_id, updated_at, runtime_run_id),
            )
            row = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        return _run_from_row(row)

    def update_run_status(
        self,
        *,
        runtime_run_id: str,
        status: str,
        updated_at: str,
        current_stage: str | None = None,
        current_round: int | None = None,
        stop_reason_code: str | None = None,
        completed_at: str | None = None,
        latest_checkpoint_id: str | None = None,
    ) -> RuntimeRunRecord:
        with self._connect() as conn, conn:
            row = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
            if row is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            require_run_transition(row["status"], status)
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET status = ?, current_stage = ?, current_round = ?, updated_at = ?,
                    stop_reason_code = COALESCE(?, stop_reason_code),
                    completed_at = COALESCE(?, completed_at),
                    latest_checkpoint_id = COALESCE(?, latest_checkpoint_id)
                WHERE runtime_run_id = ?
                """,
                (
                    status,
                    current_stage if current_stage is not None else row["current_stage"],
                    current_round if current_round is not None else row["current_round"],
                    updated_at,
                    stop_reason_code,
                    completed_at,
                    latest_checkpoint_id,
                    runtime_run_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
        return _run_from_row(updated)

    def acquire_executor_lease(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        acquired_at: str,
        lease_expires_at: str,
    ) -> RuntimeExecutorLease:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if _run_row(conn, runtime_run_id) is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                active = _active_lease_row(conn, runtime_run_id)
                if active is not None:
                    raise RuntimeControlError("runtime_executor_lease_active")
                attempt_row = conn.execute(
                    """
                    SELECT COALESCE(MAX(attempt_no), 0) AS latest_attempt
                    FROM runtime_control_executor_leases
                    WHERE runtime_run_id = ?
                    """,
                    (runtime_run_id,),
                ).fetchone()
                attempt_no = int(attempt_row["latest_attempt"]) + 1
                lease = RuntimeExecutorLease(
                    lease_id=f"rtlease_{uuid4().hex}",
                    runtime_run_id=runtime_run_id,
                    executor_id=executor_id,
                    attempt_no=attempt_no,
                    status="active",
                    acquired_at=acquired_at,
                    heartbeat_at=None,
                    lease_expires_at=lease_expires_at,
                    released_at=None,
                    reason_code=None,
                )
                conn.execute(
                    """
                    INSERT INTO runtime_control_executor_leases (
                        lease_id, runtime_run_id, executor_id, attempt_no, status,
                        acquired_at, heartbeat_at, lease_expires_at, released_at, reason_code
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease.lease_id,
                        lease.runtime_run_id,
                        lease.executor_id,
                        lease.attempt_no,
                        lease.status,
                        lease.acquired_at,
                        lease.heartbeat_at,
                        lease.lease_expires_at,
                        lease.released_at,
                        lease.reason_code,
                    ),
                )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return lease

    def heartbeat_executor_lease(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        heartbeat_at: str,
        lease_expires_at: str,
    ) -> RuntimeExecutorLease:
        with self._connect() as conn, conn:
            lease_row = _require_active_executor(conn, runtime_run_id, executor_id, attempt_no=attempt_no)
            stored_heartbeat_at = max_iso_timestamp(heartbeat_at, lease_row["heartbeat_at"], lease_row["acquired_at"])
            stored_lease_expires_at = max_iso_timestamp(lease_expires_at, lease_row["lease_expires_at"])
            conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE lease_id = ?
                """,
                (stored_heartbeat_at, stored_lease_expires_at, lease_row["lease_id"]),
            )
            updated = conn.execute(
                "SELECT * FROM runtime_control_executor_leases WHERE lease_id = ?",
                (lease_row["lease_id"],),
            ).fetchone()
        return _lease_from_row(updated)

    def release_executor_lease(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        released_at: str,
        status: str = "released",
        reason_code: str | None = None,
    ) -> RuntimeExecutorLease:
        with self._connect() as conn, conn:
            lease_row = _require_active_executor(conn, runtime_run_id, executor_id, attempt_no=attempt_no)
            stored_released_at = max_iso_timestamp(released_at, lease_row["heartbeat_at"], lease_row["acquired_at"])
            conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET status = ?, released_at = ?, reason_code = ?
                WHERE lease_id = ?
                """,
                (status, stored_released_at, reason_code, lease_row["lease_id"]),
            )
            updated = conn.execute(
                "SELECT * FROM runtime_control_executor_leases WHERE lease_id = ?",
                (lease_row["lease_id"],),
            ).fetchone()
        return _lease_from_row(updated)

    def list_active_executor_leases(self, *, executor_id: str | None = None) -> list[RuntimeExecutorLease]:
        clauses = ["status = 'active'"]
        params: list[object] = []
        if executor_id is not None:
            clauses.append("executor_id = ?")
            params.append(executor_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM runtime_control_executor_leases
                WHERE {' AND '.join(clauses)}
                ORDER BY acquired_at ASC, attempt_no ASC
                """,
                params,
            ).fetchall()
        return [_lease_from_row(row) for row in rows]

    def expire_executor_leases(self, *, now: str, batch_size: int = 100) -> list[RuntimeExecutorLease]:
        if batch_size < 1:
            raise ValueError("runtime_executor_lease_expiry_batch_size_invalid")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM runtime_control_executor_leases
                    WHERE status = 'active' AND lease_expires_at <= ?
                    ORDER BY lease_expires_at ASC, attempt_no ASC
                    LIMIT ?
                    """,
                    (now, batch_size),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        """
                        UPDATE runtime_control_executor_leases
                        SET status = 'expired', released_at = ?, reason_code = 'runtime_executor_lease_expired'
                        WHERE lease_id = ?
                        """,
                        (now, row["lease_id"]),
                    )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return [
            RuntimeExecutorLease(
                lease_id=row["lease_id"],
                runtime_run_id=row["runtime_run_id"],
                executor_id=row["executor_id"],
                attempt_no=row["attempt_no"],
                status="expired",
                acquired_at=row["acquired_at"],
                heartbeat_at=row["heartbeat_at"],
                lease_expires_at=row["lease_expires_at"],
                released_at=now,
                reason_code="runtime_executor_lease_expired",
            )
            for row in rows
        ]

    def save_requirement_draft(
        self,
        draft: RequirementDraft,
        *,
        extracted_requirement_sheet_json: dict[str, object],
        idempotency_key: str,
    ) -> RequirementDraft:
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO runtime_requirement_drafts (
                    draft_revision_id, agent_conversation_id, base_revision_id, status,
                    sections_json, extracted_requirement_sheet_json, idempotency_key, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_revision_id,
                    draft.conversation_id,
                    draft.base_revision_id,
                    draft.status,
                    _json([section.model_dump(mode="json") for section in draft.sections]),
                    _json(extracted_requirement_sheet_json),
                    idempotency_key,
                    draft.created_at,
                ),
            )
        return draft

    def get_requirement_draft(self, draft_revision_id: str) -> RequirementDraft | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_requirement_drafts WHERE draft_revision_id = ?",
                (draft_revision_id,),
            ).fetchone()
        return _draft_from_row(row) if row is not None else None

    def get_requirement_draft_by_idempotency(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> RequirementDraft | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_requirement_drafts
                WHERE agent_conversation_id = ? AND idempotency_key = ?
                """,
                (conversation_id, idempotency_key),
            ).fetchone()
        return _draft_from_row(row) if row is not None else None

    def get_latest_requirement_draft(self, *, conversation_id: str) -> RequirementDraft | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_requirement_drafts
                WHERE agent_conversation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return _draft_from_row(row) if row is not None else None

    def get_extracted_requirement_sheet_json(self, draft_revision_id: str) -> dict[str, object]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT extracted_requirement_sheet_json FROM runtime_requirement_drafts WHERE draft_revision_id = ?",
                (draft_revision_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("requirement_draft_not_found")
        payload = json.loads(row["extracted_requirement_sheet_json"])
        if not isinstance(payload, dict):
            raise RuntimeControlError("requirement_draft_invalid")
        return payload

    def save_requirement_amendment(self, amendment: RequirementAmendment) -> RequirementAmendment:
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO runtime_requirement_amendments (
                    amendment_id, agent_conversation_id, runtime_run_id, base_draft_revision_id,
                    result_draft_revision_id, base_approved_requirement_revision_id,
                    result_approved_requirement_revision_id, target_round_no, effective_boundary,
                    applied_event_id, input_text, target_section_hint, status, normalized_patch_json,
                    rejected_fragments_json, review_items_json, provenance_json, resolved_patch_json,
                    superseded_by_amendment_id, resolved_at, idempotency_key, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    amendment.amendment_id,
                    amendment.agent_conversation_id,
                    amendment.runtime_run_id,
                    amendment.base_draft_revision_id,
                    amendment.result_draft_revision_id,
                    amendment.base_approved_requirement_revision_id,
                    amendment.result_approved_requirement_revision_id,
                    amendment.target_round_no,
                    amendment.effective_boundary,
                    amendment.applied_event_id,
                    amendment.input_text,
                    amendment.target_section_hint,
                    amendment.status,
                    _json(amendment.normalized_patch),
                    _json(amendment.rejected_fragments),
                    _json([item.model_dump(mode="json") for item in amendment.review_items]),
                    _json(amendment.provenance),
                    _json(amendment.resolved_patch) if amendment.resolved_patch is not None else None,
                    amendment.superseded_by_amendment_id,
                    amendment.resolved_at,
                    amendment.idempotency_key,
                    amendment.created_at,
                ),
            )
        return amendment

    def get_requirement_amendment_by_idempotency(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> RequirementAmendment | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_requirement_amendments
                WHERE agent_conversation_id = ? AND idempotency_key = ?
                """,
                (conversation_id, idempotency_key),
            ).fetchone()
        return _amendment_from_row(row) if row is not None else None

    def get_requirement_amendment(self, amendment_id: str) -> RequirementAmendment | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_requirement_amendments WHERE amendment_id = ?",
                (amendment_id,),
            ).fetchone()
        return _amendment_from_row(row) if row is not None else None

    def save_approved_requirement(
        self,
        approved: ApprovedRequirementRevision,
        *,
        idempotency_key: str,
    ) -> ApprovedRequirementRevision:
        try:
            with self._connect() as conn, conn:
                conn.execute(
                    """
                    INSERT INTO runtime_approved_requirements (
                        approved_requirement_revision_id, draft_revision_id,
                        base_approved_requirement_revision_id, source_amendment_id,
                        agent_conversation_id, requirement_sheet_json,
                        selected_item_ids_json, deselected_item_ids_json, idempotency_key, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approved.approved_requirement_revision_id,
                        approved.draft_revision_id,
                        approved.base_approved_requirement_revision_id,
                        approved.source_amendment_id,
                        approved.agent_conversation_id,
                        _json(approved.requirement_sheet.model_dump(mode="json")),
                        _json(approved.selected_item_ids),
                        _json(approved.deselected_item_ids),
                        idempotency_key,
                        approved.created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            existing = self.get_approved_requirement_by_idempotency(
                conversation_id=approved.agent_conversation_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                if existing.draft_revision_id != approved.draft_revision_id:
                    raise RuntimeControlError("idempotency_key_conflict") from exc
                return existing
            raise
        return approved

    def get_approved_requirement_by_idempotency(
        self,
        *,
        conversation_id: str,
        idempotency_key: str,
    ) -> ApprovedRequirementRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_approved_requirements
                WHERE agent_conversation_id = ? AND idempotency_key = ?
                """,
                (conversation_id, idempotency_key),
            ).fetchone()
        return _approved_from_row(row) if row is not None else None

    def get_approved_requirement(self, approved_requirement_revision_id: str) -> ApprovedRequirementRevision:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_approved_requirements
                WHERE approved_requirement_revision_id = ?
                """,
                (approved_requirement_revision_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("requirement_not_confirmed")
        return _approved_from_row(row)

    def save_command(self, command: RuntimeCommand) -> RuntimeCommand:
        with self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO runtime_control_commands (
                    command_id, runtime_run_id, command_type, payload_json, status,
                    conflict_group, supersedes_command_id, superseded_by_command_id,
                    target_round_no, idempotency_key, requested_by, requested_at,
                    applied_at, rejected_reason_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.command_id,
                    command.runtime_run_id,
                    command.command_type,
                    _json(command.payload),
                    command.status,
                    command.conflict_group,
                    command.supersedes_command_id,
                    command.superseded_by_command_id,
                    command.target_round_no,
                    command.idempotency_key,
                    command.requested_by,
                    command.requested_at,
                    command.applied_at,
                    command.rejected_reason_code,
                ),
            )
        return command

    def get_command(self, command_id: str) -> RuntimeCommand:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_control_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("runtime_command_not_found")
        return _command_from_row(row)

    def get_command_by_idempotency(self, *, runtime_run_id: str, idempotency_key: str) -> RuntimeCommand | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_control_commands
                WHERE runtime_run_id = ? AND idempotency_key = ?
                """,
                (runtime_run_id, idempotency_key),
            ).fetchone()
        return _command_from_row(row) if row is not None else None

    def list_commands(
        self,
        *,
        runtime_run_id: str,
        conflict_group: str | None = None,
        statuses: set[str] | None = None,
    ) -> list[RuntimeCommand]:
        clauses = ["runtime_run_id = ?"]
        params: list[object] = [runtime_run_id]
        if conflict_group is not None:
            clauses.append("conflict_group = ?")
            params.append(conflict_group)
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(sorted(statuses))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM runtime_control_commands
                WHERE {' AND '.join(clauses)}
                ORDER BY requested_at ASC, rowid ASC
                """,
                params,
            ).fetchall()
        return [_command_from_row(row) for row in rows]

    def update_command_status(
        self,
        *,
        command_id: str,
        status: str,
        applied_at: str | None = None,
        rejected_reason_code: str | None = None,
        superseded_by_command_id: str | None = None,
    ) -> RuntimeCommand:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE runtime_control_commands
                SET status = ?,
                    applied_at = COALESCE(?, applied_at),
                    rejected_reason_code = COALESCE(?, rejected_reason_code),
                    superseded_by_command_id = COALESCE(?, superseded_by_command_id)
                WHERE command_id = ?
                """,
                (status, applied_at, rejected_reason_code, superseded_by_command_id, command_id),
            )
            row = conn.execute(
                "SELECT * FROM runtime_control_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("runtime_command_not_found")
        return _command_from_row(row)

    def list_runtime_requirement_amendments(
        self,
        *,
        runtime_run_id: str,
        target_round_no: int | None = None,
        statuses: set[str] | None = None,
    ) -> list[RequirementAmendment]:
        clauses = ["runtime_run_id = ?"]
        params: list[object] = [runtime_run_id]
        if target_round_no is not None:
            clauses.append("target_round_no = ?")
            params.append(target_round_no)
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(sorted(statuses))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM runtime_requirement_amendments
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at ASC, rowid ASC
                """,
                params,
            ).fetchall()
        return [_amendment_from_row(row) for row in rows]

    def update_requirement_amendment_status(
        self,
        *,
        amendment_id: str,
        status: str,
        applied_event_id: str | None = None,
        superseded_by_amendment_id: str | None = None,
        resolved_at: str | None = None,
    ) -> RequirementAmendment:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE runtime_requirement_amendments
                SET status = ?,
                    applied_event_id = COALESCE(?, applied_event_id),
                    superseded_by_amendment_id = COALESCE(?, superseded_by_amendment_id),
                    resolved_at = COALESCE(?, resolved_at)
                WHERE amendment_id = ?
                """,
                (status, applied_event_id, superseded_by_amendment_id, resolved_at, amendment_id),
            )
            row = conn.execute(
                "SELECT * FROM runtime_requirement_amendments WHERE amendment_id = ?",
                (amendment_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("requirement_draft_not_found")
        return _amendment_from_row(row)

    def resolve_runtime_requirement_amendment(
        self,
        *,
        amendment_id: str,
        status: str,
        target_round_no: int,
        result_approved_requirement_revision_id: str,
        resolved_patch: dict[str, object],
        resolved_at: str,
    ) -> RequirementAmendment:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE runtime_requirement_amendments
                SET status = ?,
                    target_round_no = ?,
                    result_approved_requirement_revision_id = ?,
                    resolved_patch_json = ?,
                    resolved_at = ?
                WHERE amendment_id = ?
                """,
                (
                    status,
                    target_round_no,
                    result_approved_requirement_revision_id,
                    _json(resolved_patch),
                    resolved_at,
                    amendment_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM runtime_requirement_amendments WHERE amendment_id = ?",
                (amendment_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("requirement_draft_not_found")
        return _amendment_from_row(row)

    def complete_runtime_requirement_amendment_extraction(
        self,
        *,
        amendment_id: str,
        status: str,
        result_approved_requirement_revision_id: str | None,
        normalized_patch: dict[str, object],
        rejected_fragments: list[object],
        review_items: list[ReviewItem],
        resolved_at: str,
    ) -> RequirementAmendment:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE runtime_requirement_amendments
                SET status = ?,
                    result_approved_requirement_revision_id = ?,
                    normalized_patch_json = ?,
                    rejected_fragments_json = ?,
                    review_items_json = ?,
                    resolved_at = ?
                WHERE amendment_id = ?
                """,
                (
                    status,
                    result_approved_requirement_revision_id,
                    _json(normalized_patch),
                    _json(rejected_fragments),
                    _json([item.model_dump(mode="json") for item in review_items]),
                    resolved_at,
                    amendment_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM runtime_requirement_amendments WHERE amendment_id = ?",
                (amendment_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("requirement_draft_not_found")
        return _amendment_from_row(row)

    def activate_run_requirement_revision(
        self,
        *,
        runtime_run_id: str,
        approved_requirement_revision_id: str,
        updated_at: str,
    ) -> RuntimeRunRecord:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET approved_requirement_revision_id = ?, updated_at = ?
                WHERE runtime_run_id = ?
                """,
                (approved_requirement_revision_id, updated_at, runtime_run_id),
            )
            row = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        return _run_from_row(row)

    def has_event(self, *, runtime_run_id: str, event_type: str, round_no: int | None = None) -> bool:
        clauses = ["runtime_run_id = ?", "event_type = ?"]
        params: list[object] = [runtime_run_id, event_type]
        if round_no is None:
            clauses.append("round_no IS NULL")
        else:
            clauses.append("round_no = ?")
            params.append(round_no)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM runtime_control_events WHERE {' AND '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        return row is not None

    def compact_terminal_event_payloads(self, *, older_than: str, batch_size: int) -> int:
        safe_limit = max(1, min(batch_size, 1000))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT e.event_id, e.runtime_run_id, e.source_id
                    FROM runtime_control_events e
                    JOIN runtime_control_runs r ON r.runtime_run_id = e.runtime_run_id
                    WHERE r.status IN ('cancelled', 'completed', 'failed')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM runtime_control_executor_leases active_lease
                        WHERE active_lease.runtime_run_id = r.runtime_run_id
                          AND active_lease.status = 'active'
                      )
                      AND e.created_at < ?
                      AND e.visibility <> 'public'
                      AND e.payload_json NOT LIKE '%"compacted":true%'
                    ORDER BY e.created_at ASC, e.rowid ASC
                    LIMIT ?
                    """,
                    (older_than, safe_limit),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        """
                        UPDATE runtime_control_events
                        SET payload_json = ?
                        WHERE runtime_run_id = ? AND event_id = ?
                        """,
                        (
                            _json({"compacted": True, "sourceId": row["source_id"]}),
                            row["runtime_run_id"],
                            row["event_id"],
                        ),
                    )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return len(rows)

    def delete_terminal_checkpoints(self, *, older_than: str, batch_size: int) -> int:
        safe_limit = max(1, min(batch_size, 1000))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT checkpoint.checkpoint_id
                    FROM runtime_control_checkpoints AS checkpoint
                    JOIN runtime_control_runs AS run
                      ON run.runtime_run_id = checkpoint.runtime_run_id
                    WHERE run.status IN ('cancelled', 'completed', 'failed')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM runtime_control_executor_leases active_lease
                        WHERE active_lease.runtime_run_id = run.runtime_run_id
                          AND active_lease.status = 'active'
                      )
                      AND checkpoint.created_at < ?
                    ORDER BY checkpoint.created_at ASC
                    LIMIT ?
                    """,
                    (older_than, safe_limit),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        """
                        UPDATE runtime_control_runs
                        SET latest_checkpoint_id = NULL
                        WHERE latest_checkpoint_id = ?
                        """,
                        (row["checkpoint_id"],),
                    )
                    conn.execute(
                        "DELETE FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
                        (row["checkpoint_id"],),
                    )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return len(rows)

    def delete_terminal_final_summaries(self, *, older_than: str, batch_size: int) -> int:
        safe_limit = max(1, min(batch_size, 1000))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT summary.summary_id
                    FROM runtime_control_final_summaries AS summary
                    JOIN runtime_control_runs AS run
                      ON run.runtime_run_id = summary.runtime_run_id
                    WHERE run.status IN ('cancelled', 'completed', 'failed')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM runtime_control_executor_leases active_lease
                        WHERE active_lease.runtime_run_id = run.runtime_run_id
                          AND active_lease.status = 'active'
                      )
                      AND summary.created_at < ?
                    ORDER BY summary.created_at ASC
                    LIMIT ?
                    """,
                    (older_than, safe_limit),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        "DELETE FROM runtime_control_final_summaries WHERE summary_id = ?",
                        (row["summary_id"],),
                    )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return len(rows)

    def append_event(
        self,
        event: RuntimeControlEventInput,
        *,
        snapshot: RuntimeRunSnapshot | None = None,
    ) -> RuntimeControlEvent:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                stored = _append_event_in_transaction(
                    conn,
                    event,
                    snapshot=snapshot,
                    run_status=None,
                    stop_reason_code=None,
                    completed_at=None,
                    latest_checkpoint_id=None,
                )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return stored

    def get_event(self, *, runtime_run_id: str, event_id: str) -> RuntimeControlEvent:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_control_events
                WHERE runtime_run_id = ? AND event_id = ?
                """,
                (runtime_run_id, event_id),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("runtime_event_not_found")
        return _event_from_row(row)

    def list_unprojected_public_events(self, *, runtime_run_id: str, limit: int) -> list[RuntimeControlEvent]:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_events
                WHERE runtime_run_id = ?
                  AND visibility = 'public'
                  AND workbench_event_global_seq IS NULL
                ORDER BY event_seq ASC
                LIMIT ?
                """,
                (runtime_run_id, safe_limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def mark_event_projection_success(
        self,
        *,
        runtime_run_id: str,
        event_id: str,
        workbench_event_global_seq: int,
        projected_at: str | None = None,
    ) -> RuntimeControlEvent:
        with self._connect() as conn, conn:
            existing = conn.execute(
                """
                SELECT workbench_event_global_seq
                FROM runtime_control_events
                WHERE runtime_run_id = ? AND event_id = ?
                """,
                (runtime_run_id, event_id),
            ).fetchone()
            if existing is not None and existing["workbench_event_global_seq"] is not None:
                existing_seq = int(existing["workbench_event_global_seq"])
                if existing_seq != workbench_event_global_seq:
                    raise RuntimeControlError(
                        "runtime_event_projection_conflict",
                        payload={
                            "existingWorkbenchEventGlobalSeq": existing_seq,
                            "workbenchEventGlobalSeq": workbench_event_global_seq,
                        },
                    )
            conn.execute(
                """
                UPDATE runtime_control_events
                SET workbench_event_global_seq = COALESCE(workbench_event_global_seq, ?),
                    projected_at = COALESCE(projected_at, ?),
                    last_projection_error_code = NULL
                WHERE runtime_run_id = ? AND event_id = ?
                """,
                (workbench_event_global_seq, projected_at, runtime_run_id, event_id),
            )
            row = conn.execute(
                """
                SELECT *
                FROM runtime_control_events
                WHERE runtime_run_id = ? AND event_id = ?
                """,
                (runtime_run_id, event_id),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("runtime_event_not_found")
        return _event_from_row(row)

    def mark_event_projection_failure(
        self,
        *,
        runtime_run_id: str,
        event_id: str,
        error_code: str,
    ) -> RuntimeControlEvent:
        with self._connect() as conn, conn:
            conn.execute(
                """
                UPDATE runtime_control_events
                SET projection_attempt_count = CASE
                        WHEN workbench_event_global_seq IS NULL THEN projection_attempt_count + 1
                        ELSE projection_attempt_count
                    END,
                    last_projection_error_code = CASE
                        WHEN workbench_event_global_seq IS NULL THEN ?
                        ELSE last_projection_error_code
                    END
                WHERE runtime_run_id = ? AND event_id = ?
                """,
                (error_code, runtime_run_id, event_id),
            )
            row = conn.execute(
                """
                SELECT *
                FROM runtime_control_events
                WHERE runtime_run_id = ? AND event_id = ?
                """,
                (runtime_run_id, event_id),
            ).fetchone()
        if row is None:
            raise RuntimeControlError("runtime_event_not_found")
        return _event_from_row(row)

    def mark_event_projected_to_workbench(
        self,
        *,
        runtime_run_id: str,
        event_id: str,
        workbench_event_global_seq: int,
        projected_at: str | None = None,
    ) -> RuntimeControlEvent:
        return self.mark_event_projection_success(
            runtime_run_id=runtime_run_id,
            event_id=event_id,
            workbench_event_global_seq=workbench_event_global_seq,
            projected_at=projected_at,
        )

    def append_executor_event(
        self,
        event: RuntimeControlEventInput,
        *,
        executor_id: str,
        snapshot: RuntimeRunSnapshot | None = None,
        run_status: str | None = None,
        stop_reason_code: str | None = None,
        completed_at: str | None = None,
        latest_checkpoint_id: str | None = None,
        attempt_no: int | None = None,
    ) -> RuntimeControlEvent:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _require_active_executor(
                    conn,
                    event.runtime_run_id,
                    executor_id,
                    attempt_no=attempt_no,
                    observed_at=event.created_at,
                )
                stored = _append_event_in_transaction(
                    conn,
                    event,
                    snapshot=snapshot,
                    run_status=run_status,
                    stop_reason_code=stop_reason_code,
                    completed_at=completed_at,
                    latest_checkpoint_id=latest_checkpoint_id,
                )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return stored

    def write_checkpoint(
        self,
        checkpoint: RuntimeCheckpoint,
        *,
        executor_id: str,
        attempt_no: int | None = None,
    ) -> RuntimeCheckpoint:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _require_active_executor(
                    conn,
                    checkpoint.runtime_run_id,
                    executor_id,
                    attempt_no=attempt_no,
                    observed_at=checkpoint.created_at,
                )
                conn.execute(
                    """
                    INSERT INTO runtime_control_checkpoints (
                        checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                        run_state_json, source_plan_json, pending_commands_json,
                        artifact_manifest_ref, schema_version, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.runtime_run_id,
                        checkpoint.stage,
                        checkpoint.round_no,
                        checkpoint.safe_boundary,
                        _json(checkpoint.run_state),
                        _json(checkpoint.source_plan),
                        _json(checkpoint.pending_commands),
                        checkpoint.artifact_manifest_ref,
                        checkpoint.schema_version,
                        checkpoint.created_at,
                    ),
                )
                conn.execute(
                    """
                    UPDATE runtime_control_runs
                    SET latest_checkpoint_id = ?, current_stage = ?, current_round = ?, updated_at = ?
                    WHERE runtime_run_id = ?
                    """,
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.stage,
                        checkpoint.round_no,
                        checkpoint.created_at,
                        checkpoint.runtime_run_id,
                    ),
                )
                _sync_candidate_truth_from_checkpoint(conn, checkpoint)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return checkpoint

    def get_latest_checkpoint(self, *, runtime_run_id: str) -> RuntimeCheckpoint | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_control_checkpoints
                WHERE runtime_run_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (runtime_run_id,),
            ).fetchone()
        return _checkpoint_from_row(row) if row is not None else None

    def get_latest_recoverable_checkpoint(
        self,
        *,
        runtime_run_id: str,
    ) -> RuntimeCheckpoint | RuntimeCheckpointLoadFailure | None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_checkpoints
                WHERE runtime_run_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (runtime_run_id,),
            ).fetchall()
        first_failure: RuntimeCheckpointLoadFailure | None = None
        for row in rows:
            checkpoint = _checkpoint_from_row_or_failure(row)
            if isinstance(checkpoint, RuntimeCheckpoint):
                return checkpoint
            if first_failure is None:
                first_failure = checkpoint
        return first_failure

    def get_checkpoint(self, *, runtime_run_id: str, checkpoint_id: str) -> RuntimeCheckpoint | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_control_checkpoints
                WHERE runtime_run_id = ? AND checkpoint_id = ?
                """,
                (runtime_run_id, checkpoint_id),
            ).fetchone()
        return _checkpoint_from_row(row) if row is not None else None

    def get_snapshot(self, *, runtime_run_id: str) -> RuntimeRunSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_control_snapshots WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def record_artifact_ref(
        self,
        *,
        artifact_ref_id: str,
        runtime_run_id: str,
        artifact_kind: str,
        safe_uri: str,
        visibility: str,
        metadata: dict[str, object],
        created_at: str,
    ) -> None:
        with self._connect() as conn, conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            conn.execute(
                """
                INSERT INTO runtime_control_artifact_refs (
                    artifact_ref_id, runtime_run_id, artifact_kind, safe_uri, visibility, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_ref_id) DO UPDATE SET
                    metadata_json = excluded.metadata_json,
                    created_at = excluded.created_at
                """,
                (
                    artifact_ref_id,
                    runtime_run_id,
                    artifact_kind,
                    safe_uri,
                    visibility,
                    _json(metadata),
                    created_at,
                ),
            )

    def save_final_summary(self, summary: RuntimeFinalSummary, *, idempotency_key: str) -> RuntimeFinalSummary:
        with self._connect() as conn, conn:
            try:
                conn.execute(
                    """
                    INSERT INTO runtime_control_final_summaries (
                        summary_id, runtime_run_id, idempotency_key, user_instruction,
                        summary_json, source_snapshot_event_seq, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary.summary_id,
                        summary.runtime_run_id,
                        idempotency_key,
                        summary.user_instruction,
                        _json(summary.model_dump(mode="json")),
                        summary.source_snapshot_event_seq,
                        summary.created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    """
                    SELECT summary_json
                    FROM runtime_control_final_summaries
                    WHERE runtime_run_id = ? AND idempotency_key = ?
                    """,
                    (summary.runtime_run_id, idempotency_key),
                ).fetchone()
                if row is not None:
                    return RuntimeFinalSummary.model_validate_json(row["summary_json"])
                raise
        return summary

    def get_final_summary_by_idempotency(
        self,
        *,
        runtime_run_id: str,
        idempotency_key: str,
    ) -> RuntimeFinalSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT summary_json
                FROM runtime_control_final_summaries
                WHERE runtime_run_id = ? AND idempotency_key = ?
                """,
                (runtime_run_id, idempotency_key),
            ).fetchone()
        return RuntimeFinalSummary.model_validate_json(row["summary_json"]) if row is not None else None

    def get_final_summary(self, *, summary_id: str) -> RuntimeFinalSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT summary_json
                FROM runtime_control_final_summaries
                WHERE summary_id = ?
                """,
                (summary_id,),
            ).fetchone()
        return RuntimeFinalSummary.model_validate_json(row["summary_json"]) if row is not None else None

    def list_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeControlEventPage:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT latest_event_seq FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
            if run_row is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_events
                WHERE runtime_run_id = ? AND event_seq > ?
                ORDER BY event_seq ASC
                LIMIT ?
                """,
                (runtime_run_id, after_seq, safe_limit),
            ).fetchall()
        if rows and int(rows[0]["event_seq"]) > after_seq + 1:
            return RuntimeControlEventPage(
                events=[],
                next_cursor=after_seq,
                reason_code="runtime_event_gap_detected",
            )
        if not rows and int(run_row["latest_event_seq"]) > after_seq:
            return RuntimeControlEventPage(
                events=[],
                next_cursor=after_seq,
                reason_code="runtime_event_gap_detected",
            )
        events = [_event_from_row(row) for row in rows]
        next_cursor = events[-1].event_seq if events else after_seq
        return RuntimeControlEventPage(events=events, next_cursor=next_cursor)

    def list_public_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeControlEventPage:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_events
                WHERE runtime_run_id = ? AND event_seq > ? AND visibility = 'public'
                ORDER BY event_seq ASC
                LIMIT ?
                """,
                (runtime_run_id, after_seq, safe_limit),
            ).fetchall()
        events = [_event_from_row(row) for row in rows]
        next_cursor = events[-1].event_seq if events else after_seq
        return RuntimeControlEventPage(events=events, next_cursor=next_cursor)

    def save_stage_output(
        self,
        output: RuntimeStageOutputInput,
        *,
        executor_id: str | None = None,
        attempt_no: int | None = None,
    ) -> RuntimeStageOutput:
        node_key = _node_key(output.node_id)
        round_key = _round_key(output.round_no)
        wrote_new_artifact_ref_id: str | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if _run_row(conn, output.runtime_run_id) is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                if executor_id is not None:
                    _require_active_executor(
                        conn,
                        output.runtime_run_id,
                        executor_id,
                        attempt_no=attempt_no,
                        observed_at=output.created_at,
                    )
                safe_output = sanitize_stage_output_payload(
                    output_kind=output.output_kind,
                    schema_version=output.schema_version,
                    output=output.output,
                    stage=output.stage,
                    round_no=output.round_no,
                    node_id=output.node_id,
                )
                payload_json = _json(safe_output)
                payload_size_bytes = len(payload_json.encode("utf-8"))
                payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
                existing = _stage_output_row(
                    conn,
                    runtime_run_id=output.runtime_run_id,
                    stage=output.stage,
                    node_key=node_key,
                    round_key=round_key,
                    output_kind=output.output_kind,
                    schema_version=output.schema_version,
                )
                if existing is not None:
                    if existing["payload_hash"] != payload_hash:
                        raise RuntimeControlError("runtime_stage_output_conflict")
                    conn.commit()
                    return _stage_output_from_row(existing, database_path=self.path)
                output_json = payload_json
                artifact_ref_id = output.artifact_ref_id
                if payload_size_bytes > MAX_RUNTIME_CONTROL_JSON_BYTES:
                    if output.artifact_ref_id is not None:
                        raise RuntimeControlError("runtime_stage_output_artifact_ref_external")
                    artifact_ref_id = output.artifact_ref_id or _stage_output_artifact_ref_id(
                        output_id=output.output_id,
                        payload_hash=payload_hash,
                    )
                    artifact_ref_existed = (
                        conn.execute(
                            "SELECT 1 FROM runtime_control_artifact_refs WHERE artifact_ref_id = ?",
                            (artifact_ref_id,),
                        ).fetchone()
                        is not None
                    )
                    _write_stage_output_artifact(self.path, artifact_ref_id=artifact_ref_id, payload_json=payload_json)
                    if not artifact_ref_existed:
                        wrote_new_artifact_ref_id = artifact_ref_id
                    output_json = _json(
                        {
                            "artifactKind": _RUNTIME_STAGE_OUTPUT_ARTIFACT_KIND,
                            "artifactRefId": artifact_ref_id,
                            "payloadHash": payload_hash,
                            "payloadSizeBytes": payload_size_bytes,
                            "storage": "file",
                        }
                    )
                    _record_stage_output_artifact_ref(
                        conn,
                        artifact_ref_id=artifact_ref_id,
                        runtime_run_id=output.runtime_run_id,
                        output_id=output.output_id,
                        stage=output.stage,
                        output_kind=output.output_kind,
                        schema_version=output.schema_version,
                        payload_hash=payload_hash,
                        payload_size_bytes=payload_size_bytes,
                        created_at=output.created_at,
                    )
                try:
                    conn.execute(
                        """
                        INSERT INTO runtime_control_stage_outputs (
                            output_id, runtime_run_id, stage, node_id, node_key, round_no, round_key,
                            output_kind, schema_version, output_json, payload_hash, payload_size_bytes,
                            source_event_id, source_checkpoint_id, artifact_ref_id, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            output.output_id,
                            output.runtime_run_id,
                            output.stage,
                            output.node_id,
                            node_key,
                            output.round_no,
                            round_key,
                            output.output_kind,
                            output.schema_version,
                            output_json,
                            payload_hash,
                            payload_size_bytes,
                            output.source_event_id,
                            output.source_checkpoint_id,
                            artifact_ref_id,
                            output.created_at,
                        ),
                    )
                except sqlite3.IntegrityError:
                    existing = _stage_output_row(
                        conn,
                        runtime_run_id=output.runtime_run_id,
                        stage=output.stage,
                        node_key=node_key,
                        round_key=round_key,
                        output_kind=output.output_kind,
                        schema_version=output.schema_version,
                    )
                    if existing is not None:
                        if existing["payload_hash"] != payload_hash:
                            raise RuntimeControlError("runtime_stage_output_conflict")
                        conn.commit()
                        return _stage_output_from_row(existing, database_path=self.path)
                    raise
                row = conn.execute(
                    "SELECT * FROM runtime_control_stage_outputs WHERE output_id = ?",
                    (output.output_id,),
                ).fetchone()
                conn.commit()
            except (OSError, sqlite3.Error):
                conn.rollback()
                if wrote_new_artifact_ref_id is not None:
                    _delete_stage_output_artifact_files(self.path, [wrote_new_artifact_ref_id])
                raise
        return _stage_output_from_row(row, database_path=self.path)

    def list_candidate_identities(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateIdentity]:
        with self._connect() as conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_candidate_identities
                WHERE runtime_run_id = ?
                ORDER BY identity_id ASC
                """,
                (runtime_run_id,),
            ).fetchall()
        return [_candidate_identity_from_row(row) for row in rows]

    def list_candidate_evidence(self, *, runtime_run_id: str) -> list[RuntimeControlCandidateEvidence]:
        with self._connect() as conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_candidate_evidence
                WHERE runtime_run_id = ?
                ORDER BY evidence_id ASC
                """,
                (runtime_run_id,),
            ).fetchall()
        return [_candidate_evidence_from_row(row) for row in rows]

    def list_candidate_finalization_revisions(
        self,
        *,
        runtime_run_id: str,
    ) -> list[RuntimeControlCandidateFinalizationRevision]:
        with self._connect() as conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_candidate_finalization_revisions
                WHERE runtime_run_id = ?
                ORDER BY revision ASC
                """,
                (runtime_run_id,),
            ).fetchall()
        return [_candidate_finalization_revision_from_row(row) for row in rows]

    def list_unprojected_candidate_finalization_revisions(
        self,
        *,
        runtime_run_id: str,
        projector: str,
        limit: int,
    ) -> list[RuntimeControlCandidateFinalizationRevision]:
        safe_limit = max(1, min(limit, 100))
        with self._connect() as conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            rows = conn.execute(
                """
                SELECT revision.*
                FROM runtime_control_candidate_finalization_revisions AS revision
                LEFT JOIN runtime_control_projection_marks AS mark
                  ON mark.runtime_run_id = revision.runtime_run_id
                 AND mark.target_kind = 'candidate_finalization_revision'
                 AND mark.target_id = CAST(revision.revision AS TEXT)
                 AND mark.projector = ?
                 AND mark.target_version = revision.payload_hash
                 AND mark.status = 'projected'
                WHERE revision.runtime_run_id = ?
                  AND mark.runtime_run_id IS NULL
                ORDER BY revision.revision ASC
                LIMIT ?
                """,
                (projector, runtime_run_id, safe_limit),
            ).fetchall()
        return [_candidate_finalization_revision_from_row(row) for row in rows]

    def mark_projection_success(
        self,
        *,
        runtime_run_id: str,
        target_kind: str,
        target_id: str,
        projector: str,
        target_version: str,
        projected_ref: str,
        projected_at: str,
    ) -> None:
        with self._connect() as conn, conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            conn.execute(
                """
                INSERT INTO runtime_control_projection_marks (
                    runtime_run_id, target_kind, target_id, projector, target_version,
                    status, projected_ref, attempt_count, last_error_code, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'projected', ?, 1, NULL, ?)
                ON CONFLICT(runtime_run_id, target_kind, target_id, projector) DO UPDATE SET
                    target_version = excluded.target_version,
                    status = 'projected',
                    projected_ref = excluded.projected_ref,
                    attempt_count = runtime_control_projection_marks.attempt_count + 1,
                    last_error_code = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    runtime_run_id,
                    target_kind,
                    target_id,
                    projector,
                    target_version,
                    projected_ref,
                    projected_at,
                ),
            )

    def mark_projection_failure(
        self,
        *,
        runtime_run_id: str,
        target_kind: str,
        target_id: str,
        projector: str,
        target_version: str,
        error_code: str,
        failed_at: str,
    ) -> None:
        with self._connect() as conn, conn:
            if _run_row(conn, runtime_run_id) is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            conn.execute(
                """
                INSERT INTO runtime_control_projection_marks (
                    runtime_run_id, target_kind, target_id, projector, target_version,
                    status, projected_ref, attempt_count, last_error_code, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'failed', NULL, 1, ?, ?)
                ON CONFLICT(runtime_run_id, target_kind, target_id, projector) DO UPDATE SET
                    target_version = excluded.target_version,
                    status = 'failed',
                    attempt_count = runtime_control_projection_marks.attempt_count + 1,
                    last_error_code = excluded.last_error_code,
                    updated_at = excluded.updated_at
                """,
                (
                    runtime_run_id,
                    target_kind,
                    target_id,
                    projector,
                    target_version,
                    error_code,
                    failed_at,
                ),
            )

    def get_stage_output(
        self,
        *,
        runtime_run_id: str,
        stage: str,
        output_kind: str,
        node_id: str | None = None,
        round_no: int | None = None,
        schema_version: str | None = None,
    ) -> RuntimeStageOutput | None:
        with self._connect() as conn:
            row = _stage_output_row(
                conn,
                runtime_run_id=runtime_run_id,
                stage=stage,
                node_key=_node_key(node_id),
                round_key=_round_key(round_no),
                output_kind=output_kind,
                schema_version=schema_version,
            )
        return _stage_output_from_row(row, database_path=self.path) if row is not None else None

    def list_stage_outputs(
        self,
        *,
        runtime_run_id: str,
        stage: str | None = None,
        round_no: int | None = None,
        output_kind: str | None = None,
    ) -> list[RuntimeStageOutput]:
        clauses = ["runtime_run_id = ?"]
        params: list[object] = [runtime_run_id]
        if stage is not None:
            clauses.append("stage = ?")
            params.append(stage)
        if round_no is not None:
            clauses.append("round_key = ?")
            params.append(_round_key(round_no))
        if output_kind is not None:
            clauses.append("output_kind = ?")
            params.append(output_kind)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM runtime_control_stage_outputs
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at ASC, rowid ASC
                """,
                params,
            ).fetchall()
        return [_stage_output_from_row(row, database_path=self.path) for row in rows]

    def delete_terminal_stage_outputs(self, *, older_than: str, batch_size: int) -> int:
        safe_limit = max(1, min(batch_size, 1000))
        placeholders = ",".join("?" for _ in _REQUIRED_STAGE_OUTPUT_KINDS)
        artifact_ref_ids: list[str] = []
        quarantined_artifacts: list[tuple[Path, Path]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    f"""
                    SELECT output.output_id, output.artifact_ref_id, output.output_json
                    FROM runtime_control_stage_outputs AS output
                    JOIN runtime_control_runs AS run
                      ON run.runtime_run_id = output.runtime_run_id
                    WHERE run.status IN ('cancelled', 'completed', 'failed')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM runtime_control_executor_leases active_lease
                        WHERE active_lease.runtime_run_id = run.runtime_run_id
                          AND active_lease.status = 'active'
                      )
                      AND output.created_at < ?
                      AND output.output_kind NOT IN ({placeholders})
                    ORDER BY output.created_at ASC, output.rowid ASC
                    LIMIT ?
                    """,
                    (older_than, *sorted(_REQUIRED_STAGE_OUTPUT_KINDS), safe_limit),
                ).fetchall()
                artifact_ref_ids = _stage_output_file_artifact_ref_ids(rows)
                quarantined_artifacts = _quarantine_stage_output_artifact_files(self.path, artifact_ref_ids)
                for row in rows:
                    conn.execute(
                        "DELETE FROM runtime_control_stage_outputs WHERE output_id = ?",
                        (row["output_id"],),
                    )
                _delete_rows_by_ids(conn, "runtime_control_artifact_refs", "artifact_ref_id", artifact_ref_ids)
                conn.commit()
            except (OSError, RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                _restore_quarantined_stage_output_artifacts(quarantined_artifacts)
                raise
        _delete_quarantined_stage_output_artifacts(
            self.path,
            quarantined_artifacts,
            reason_code="runtime_stage_output_retention",
        )
        return len(rows)

    def collect_runtime_control_retention_stats(
        self,
        *,
        terminal_run_older_than: str,
        developer_event_older_than: str,
        internal_event_older_than: str,
        checkpoint_older_than: str,
        lease_older_than: str,
        command_older_than: str,
        stage_output_older_than: str,
        final_summary_older_than: str,
    ) -> dict[str, int]:
        with self._connect() as conn:
            return _retention_counts(
                conn,
                    terminal_run_older_than=terminal_run_older_than,
                    developer_event_older_than=developer_event_older_than,
                    internal_event_older_than=internal_event_older_than,
                    checkpoint_older_than=checkpoint_older_than,
                    lease_older_than=lease_older_than,
                    command_older_than=command_older_than,
                    stage_output_older_than=stage_output_older_than,
                    final_summary_older_than=final_summary_older_than,
                    database_path=self.path,
                )

    def cleanup_runtime_control_retention(
        self,
        *,
        terminal_run_older_than: str,
        developer_event_older_than: str,
        internal_event_older_than: str,
        checkpoint_older_than: str,
        lease_older_than: str,
        command_older_than: str,
        stage_output_older_than: str,
        final_summary_older_than: str,
        batch_size: int,
        dry_run: bool = False,
    ) -> dict[str, int]:
        safe_limit = max(1, min(batch_size, 1000))
        stage_output_artifact_ref_ids: list[str] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                ids = _retention_candidate_ids(
                    conn,
                    terminal_run_older_than=terminal_run_older_than,
                    developer_event_older_than=developer_event_older_than,
                    internal_event_older_than=internal_event_older_than,
                    checkpoint_older_than=checkpoint_older_than,
                    lease_older_than=lease_older_than,
                    command_older_than=command_older_than,
                    stage_output_older_than=stage_output_older_than,
                    final_summary_older_than=final_summary_older_than,
                    limit=safe_limit,
                )
                deleted = {key: len(value) for key, value in ids.items()}
                quarantined_artifacts: list[tuple[Path, Path]] = []
                if not dry_run:
                    stage_output_artifact_ref_ids = _stage_output_file_artifact_ref_ids_for_output_ids(
                        conn,
                        ids["stage_output"],
                    )
                    quarantined_artifacts = _quarantine_stage_output_artifact_files(
                        self.path,
                        stage_output_artifact_ref_ids,
                    )
                    _delete_rows_by_ids(conn, "runtime_control_events", "event_id", ids["nonpublic_event"])
                    _clear_latest_checkpoint_refs(conn, ids["checkpoint"])
                    _delete_rows_by_ids(
                        conn,
                        "runtime_control_checkpoints",
                        "checkpoint_id",
                        ids["checkpoint"],
                    )
                    _delete_rows_by_ids(
                        conn,
                        "runtime_control_executor_leases",
                        "lease_id",
                        ids["executor_lease"],
                    )
                    _delete_rows_by_ids(conn, "runtime_control_commands", "command_id", ids["command"])
                    _delete_rows_by_ids(
                        conn,
                        "runtime_control_stage_outputs",
                        "output_id",
                        ids["stage_output"],
                    )
                    _delete_rows_by_ids(
                        conn,
                        "runtime_control_artifact_refs",
                        "artifact_ref_id",
                        stage_output_artifact_ref_ids,
                    )
                    _delete_rows_by_ids(
                        conn,
                        "runtime_control_final_summaries",
                        "summary_id",
                        ids["final_summary"],
                    )
                conn.commit()
            except (OSError, RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                if not dry_run:
                    _restore_quarantined_stage_output_artifacts(quarantined_artifacts)
                raise
        if not dry_run:
            _delete_quarantined_stage_output_artifacts(
                self.path,
                quarantined_artifacts,
                reason_code="runtime_control_retention",
            )
        return deleted

    def claim_next_runnable_run(
        self,
        *,
        executor_id: str,
        claimed_at: str,
        lease_expires_at: str,
        runtime_run_id: str | None = None,
    ) -> RuntimeWorkerClaim | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                run_row = _next_runnable_run_row(conn, runtime_run_id=runtime_run_id)
                if run_row is None:
                    conn.commit()
                    return None
                claim_reason = run_row["status"]
                attempt_row = conn.execute(
                    """
                    SELECT COALESCE(MAX(attempt_no), 0) AS latest_attempt
                    FROM runtime_control_executor_leases
                    WHERE runtime_run_id = ?
                    """,
                    (run_row["runtime_run_id"],),
                ).fetchone()
                attempt_no = int(attempt_row["latest_attempt"]) + 1
                lease = RuntimeExecutorLease(
                    lease_id=f"rtlease_{uuid4().hex}",
                    runtime_run_id=run_row["runtime_run_id"],
                    executor_id=executor_id,
                    attempt_no=attempt_no,
                    status="active",
                    acquired_at=claimed_at,
                    heartbeat_at=None,
                    lease_expires_at=lease_expires_at,
                    released_at=None,
                    reason_code=None,
                )
                conn.execute(
                    """
                    INSERT INTO runtime_control_executor_leases (
                        lease_id, runtime_run_id, executor_id, attempt_no, status,
                        acquired_at, heartbeat_at, lease_expires_at, released_at, reason_code
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease.lease_id,
                        lease.runtime_run_id,
                        lease.executor_id,
                        lease.attempt_no,
                        lease.status,
                        lease.acquired_at,
                        lease.heartbeat_at,
                        lease.lease_expires_at,
                        lease.released_at,
                        lease.reason_code,
                    ),
                )
                snapshot_row = conn.execute(
                    "SELECT snapshot_json FROM runtime_control_snapshots WHERE runtime_run_id = ?",
                    (run_row["runtime_run_id"],),
                ).fetchone()
                snapshot_payload = _json_object(snapshot_row["snapshot_json"]) if snapshot_row is not None else {}
                snapshot_payload.update(
                    {
                        "executorId": executor_id,
                        "leaseId": lease.lease_id,
                        "claimStatus": "starting",
                        "claimReason": claim_reason,
                    }
                )
                snapshot = RuntimeRunSnapshot(
                    runtime_run_id=run_row["runtime_run_id"],
                    status="starting",
                    current_stage="starting",
                    current_round=run_row["current_round"],
                    latest_event_seq=int(run_row["latest_event_seq"]) + 1,
                    snapshot=snapshot_payload,
                    updated_at=claimed_at,
                )
                claim_event = _append_event_in_transaction(
                    conn,
                    RuntimeControlEventInput(
                        event_id=f"rtevt_{uuid4().hex}",
                        runtime_run_id=run_row["runtime_run_id"],
                        event_type="runtime_worker_claimed",
                        stage="starting",
                        round_no=run_row["current_round"],
                        source_id=None,
                        status="completed",
                        summary="runtime worker claimed run",
                        payload={
                            "executorId": executor_id,
                            "leaseId": lease.lease_id,
                            "attemptNo": attempt_no,
                            "claimReason": claim_reason,
                        },
                        schema_version=RUNTIME_CONTROL_EVENT_SCHEMA_VERSION,
                        visibility="developer",
                        idempotency_key=f"runtime-claim:{lease.lease_id}",
                        payload_kind="compact",
                        workbench_event_global_seq=None,
                        created_at=claimed_at,
                    ),
                    snapshot=snapshot,
                    run_status="starting",
                    stop_reason_code=None,
                    completed_at=None,
                    latest_checkpoint_id=None,
                )
                updated_run = conn.execute(
                    "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                    (run_row["runtime_run_id"],),
                ).fetchone()
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return RuntimeWorkerClaim(
            runtime_run=_run_from_row(updated_run),
            lease=lease,
            claimed_event=claim_event,
            claim_reason=claim_reason,
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            yield conn
            conn.commit()
        except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
            conn.rollback()
            raise
        finally:
            conn.close()


def _migration_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_control_runs (
          runtime_run_id TEXT PRIMARY KEY,
          run_intent_id TEXT NOT NULL,
          start_idempotency_key TEXT NOT NULL,
          run_kind TEXT NOT NULL DEFAULT 'primary',
          agent_conversation_id TEXT,
          workbench_session_id TEXT,
          approved_requirement_revision_id TEXT NOT NULL,
          status TEXT NOT NULL,
          current_stage TEXT NOT NULL,
          current_round INTEGER,
          latest_checkpoint_id TEXT,
          latest_event_seq INTEGER NOT NULL DEFAULT 0,
          source_ids_json TEXT NOT NULL,
          stop_reason_code TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          completed_at TEXT,
          CHECK (run_kind IN ('primary', 'rerun', 'fork'))
        );

        CREATE TABLE IF NOT EXISTS runtime_requirement_drafts (
          draft_revision_id TEXT PRIMARY KEY,
          agent_conversation_id TEXT NOT NULL,
          base_revision_id TEXT,
          status TEXT NOT NULL,
          sections_json TEXT NOT NULL,
          extracted_requirement_sheet_json TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(agent_conversation_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS runtime_requirement_amendments (
          amendment_id TEXT PRIMARY KEY,
          agent_conversation_id TEXT NOT NULL,
          runtime_run_id TEXT,
          base_draft_revision_id TEXT,
          result_draft_revision_id TEXT,
          base_approved_requirement_revision_id TEXT,
          result_approved_requirement_revision_id TEXT,
          target_round_no INTEGER,
          effective_boundary TEXT,
          applied_event_id TEXT,
          input_text TEXT NOT NULL,
          target_section_hint TEXT,
          status TEXT NOT NULL,
          normalized_patch_json TEXT NOT NULL,
          rejected_fragments_json TEXT NOT NULL,
          review_items_json TEXT NOT NULL,
          provenance_json TEXT NOT NULL DEFAULT '{}',
          resolved_patch_json TEXT,
          superseded_by_amendment_id TEXT,
          resolved_at TEXT,
          idempotency_key TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(agent_conversation_id, idempotency_key),
          UNIQUE(runtime_run_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS runtime_approved_requirements (
          approved_requirement_revision_id TEXT PRIMARY KEY,
          draft_revision_id TEXT,
          base_approved_requirement_revision_id TEXT,
          source_amendment_id TEXT,
          agent_conversation_id TEXT NOT NULL,
          requirement_sheet_json TEXT NOT NULL,
          selected_item_ids_json TEXT NOT NULL,
          deselected_item_ids_json TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(agent_conversation_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_commands (
          command_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          command_type TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL,
          conflict_group TEXT NOT NULL,
          supersedes_command_id TEXT,
          superseded_by_command_id TEXT,
          target_round_no INTEGER,
          idempotency_key TEXT NOT NULL,
          requested_by TEXT,
          requested_at TEXT NOT NULL,
          applied_at TEXT,
          rejected_reason_code TEXT,
          UNIQUE(runtime_run_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_checkpoints (
          checkpoint_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          round_no INTEGER,
          safe_boundary TEXT NOT NULL,
          run_state_json TEXT NOT NULL,
          source_plan_json TEXT NOT NULL,
          pending_commands_json TEXT NOT NULL,
          artifact_manifest_ref TEXT,
          schema_version TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runtime_control_executor_leases (
          lease_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          executor_id TEXT NOT NULL,
          attempt_no INTEGER NOT NULL,
          status TEXT NOT NULL,
          acquired_at TEXT NOT NULL,
          heartbeat_at TEXT,
          lease_expires_at TEXT NOT NULL,
          released_at TEXT,
          reason_code TEXT,
          UNIQUE(runtime_run_id, attempt_no)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_events (
          event_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          event_seq INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          stage TEXT NOT NULL,
          round_no INTEGER,
          source_id TEXT,
          status TEXT NOT NULL,
          summary TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          schema_version TEXT NOT NULL DEFAULT 'runtime-control-event/v1',
          visibility TEXT NOT NULL DEFAULT 'internal',
          idempotency_key TEXT,
          payload_kind TEXT NOT NULL DEFAULT 'compact',
          payload_size_bytes INTEGER NOT NULL DEFAULT 0,
          projection_attempt_count INTEGER NOT NULL DEFAULT 0,
          last_projection_error_code TEXT,
          projected_at TEXT,
          workbench_event_global_seq INTEGER,
          created_at TEXT NOT NULL,
          UNIQUE(runtime_run_id, event_seq),
          UNIQUE(runtime_run_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_stage_outputs (
          output_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          node_id TEXT,
          node_key TEXT NOT NULL,
          round_no INTEGER,
          round_key INTEGER NOT NULL,
          output_kind TEXT NOT NULL,
          schema_version TEXT NOT NULL,
          output_json TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          payload_size_bytes INTEGER NOT NULL,
          source_event_id TEXT,
          source_checkpoint_id TEXT,
          artifact_ref_id TEXT,
          created_at TEXT NOT NULL,
          CHECK ((node_id IS NULL AND node_key = '') OR (node_id IS NOT NULL AND node_id <> '' AND node_key = node_id)),
          CHECK ((round_no IS NULL AND round_key = -1) OR (round_no IS NOT NULL AND round_no >= 0 AND round_key = round_no)),
          UNIQUE(runtime_run_id, stage, node_key, round_key, output_kind, schema_version)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_candidate_identities (
          runtime_run_id TEXT NOT NULL,
          identity_id TEXT NOT NULL,
          canonical_resume_id TEXT NOT NULL,
          merged_resume_ids_json TEXT NOT NULL,
          source_evidence_ids_json TEXT NOT NULL,
          equivalent_latest_resume_ids_json TEXT NOT NULL DEFAULT '[]',
          display_source_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
          conflicting_resume_ids_json TEXT NOT NULL DEFAULT '[]',
          incomparable_resume_ids_json TEXT NOT NULL DEFAULT '[]',
          content_version_key TEXT NOT NULL DEFAULT '',
          safe_reason_codes_json TEXT NOT NULL DEFAULT '[]',
          display_name TEXT NOT NULL,
          title TEXT NOT NULL,
          company TEXT NOT NULL,
          location TEXT NOT NULL,
          summary TEXT NOT NULL,
          score INTEGER,
          fit_bucket TEXT,
          source_round INTEGER,
          payload_hash TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(runtime_run_id, identity_id)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_candidate_evidence (
          runtime_run_id TEXT NOT NULL,
          evidence_id TEXT NOT NULL,
          identity_id TEXT NOT NULL,
          resume_id TEXT NOT NULL,
          source_kind TEXT NOT NULL,
          evidence_level TEXT NOT NULL,
          provider_candidate_key_hash TEXT NOT NULL,
          score INTEGER,
          fit_bucket TEXT,
          source_references_json TEXT NOT NULL DEFAULT '[]',
          payload_json TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(runtime_run_id, evidence_id)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_candidate_finalization_revisions (
          runtime_run_id TEXT NOT NULL,
          revision INTEGER NOT NULL,
          reason_code TEXT NOT NULL,
          candidate_identity_ids_json TEXT NOT NULL,
          coverage_summary_json TEXT NOT NULL,
          source_checkpoint_id TEXT,
          payload_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(runtime_run_id, revision)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_projection_marks (
          runtime_run_id TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          projector TEXT NOT NULL,
          target_version TEXT NOT NULL,
          status TEXT NOT NULL,
          projected_ref TEXT,
          attempt_count INTEGER NOT NULL DEFAULT 0,
          last_error_code TEXT,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(runtime_run_id, target_kind, target_id, projector)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_snapshots (
          runtime_run_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          current_stage TEXT NOT NULL,
          current_round INTEGER,
          latest_event_seq INTEGER NOT NULL,
          snapshot_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runtime_control_artifact_refs (
          artifact_ref_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          artifact_kind TEXT NOT NULL,
          safe_uri TEXT NOT NULL,
          visibility TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runtime_control_artifact_deletions (
          deletion_id TEXT PRIMARY KEY,
          artifact_ref_id TEXT NOT NULL,
          artifact_kind TEXT NOT NULL,
          original_path TEXT NOT NULL,
          quarantine_path TEXT NOT NULL,
          reason_code TEXT NOT NULL,
          status TEXT NOT NULL,
          attempt_count INTEGER NOT NULL DEFAULT 0,
          last_error_code TEXT,
          requested_at TEXT NOT NULL,
          last_attempt_at TEXT,
          metadata_json TEXT NOT NULL,
          CHECK (status IN ('pending', 'completed'))
        );

        CREATE TABLE IF NOT EXISTS runtime_control_final_summaries (
          summary_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          user_instruction TEXT,
          summary_json TEXT NOT NULL,
          source_snapshot_event_seq INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(runtime_run_id, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_runtime_events_run_seq
          ON runtime_control_events(runtime_run_id, event_seq);
        CREATE INDEX IF NOT EXISTS idx_runtime_commands_run_status
          ON runtime_control_commands(runtime_run_id, status);
        CREATE INDEX IF NOT EXISTS idx_runtime_drafts_conversation
          ON runtime_requirement_drafts(agent_conversation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_amendments_draft
          ON runtime_requirement_amendments(base_draft_revision_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_amendments_target_round
          ON runtime_requirement_amendments(runtime_run_id, target_round_no, status)
          WHERE runtime_run_id IS NOT NULL AND target_round_no IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_runtime_runs_conversation
          ON runtime_control_runs(agent_conversation_id, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_runs_run_intent
          ON runtime_control_runs(run_intent_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_runs_start_idempotency_key
          ON runtime_control_runs(start_idempotency_key);
        CREATE INDEX IF NOT EXISTS idx_runtime_runs_approved_requirement_created
          ON runtime_control_runs(approved_requirement_revision_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_runs_status_created
          ON runtime_control_runs(status, created_at, runtime_run_id);
        CREATE INDEX IF NOT EXISTS idx_runtime_events_workbench_seq
          ON runtime_control_events(workbench_event_global_seq)
          WHERE workbench_event_global_seq IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_events_run_idempotency_key
          ON runtime_control_events(runtime_run_id, idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_runtime_executor_leases_run_status
          ON runtime_control_executor_leases(runtime_run_id, status);
        CREATE INDEX IF NOT EXISTS idx_runtime_executor_leases_expiry
          ON runtime_control_executor_leases(status, lease_expires_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_stage_outputs_run_stage
          ON runtime_control_stage_outputs(runtime_run_id, stage);
        CREATE INDEX IF NOT EXISTS idx_runtime_stage_outputs_run_stage_round_kind
          ON runtime_control_stage_outputs(runtime_run_id, stage, round_key, output_kind);
        CREATE INDEX IF NOT EXISTS idx_runtime_candidate_evidence_run_identity
          ON runtime_control_candidate_evidence(runtime_run_id, identity_id);
        CREATE INDEX IF NOT EXISTS idx_runtime_candidate_finalization_run_revision
          ON runtime_control_candidate_finalization_revisions(runtime_run_id, revision DESC);
        CREATE INDEX IF NOT EXISTS idx_runtime_projection_marks_target
          ON runtime_control_projection_marks(runtime_run_id, target_kind, projector, status);
        CREATE INDEX IF NOT EXISTS idx_runtime_artifact_deletions_status
          ON runtime_control_artifact_deletions(status, requested_at);
        """
    )


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "runtime_control_runs"):
        conn.execute("ALTER TABLE runtime_control_runs RENAME TO runtime_control_runs_v1")
        conn.execute(
            """
            CREATE TABLE runtime_control_runs (
              runtime_run_id TEXT PRIMARY KEY,
              run_intent_id TEXT NOT NULL,
              start_idempotency_key TEXT NOT NULL,
              run_kind TEXT NOT NULL DEFAULT 'primary',
              agent_conversation_id TEXT,
              workbench_session_id TEXT,
              approved_requirement_revision_id TEXT NOT NULL,
              status TEXT NOT NULL,
              current_stage TEXT NOT NULL,
              current_round INTEGER,
              latest_checkpoint_id TEXT,
              latest_event_seq INTEGER NOT NULL DEFAULT 0,
              source_ids_json TEXT NOT NULL,
              stop_reason_code TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT,
              CHECK (run_kind IN ('primary', 'rerun', 'fork'))
            )
            """
        )
        conn.execute(
            """
            INSERT INTO runtime_control_runs (
              runtime_run_id, run_intent_id, start_idempotency_key, run_kind,
              agent_conversation_id, workbench_session_id, approved_requirement_revision_id,
              status, current_stage, current_round, latest_checkpoint_id, latest_event_seq,
              source_ids_json, stop_reason_code, created_at, updated_at, completed_at
            )
            SELECT
              runtime_run_id, runtime_run_id, runtime_run_id, 'primary',
              agent_conversation_id, workbench_session_id, approved_requirement_revision_id,
              status, current_stage, current_round, latest_checkpoint_id, latest_event_seq,
              source_ids_json, stop_reason_code, created_at, updated_at, completed_at
            FROM runtime_control_runs_v1
            """
        )
        conn.execute("DROP TABLE runtime_control_runs_v1")

    if _table_exists(conn, "runtime_control_events"):
        event_columns = _column_names(conn, "runtime_control_events")
        if "schema_version" not in event_columns:
            conn.execute(
                """
                ALTER TABLE runtime_control_events
                ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'runtime-control-event/v1'
                """
            )
        if "visibility" not in event_columns:
            conn.execute("ALTER TABLE runtime_control_events ADD COLUMN visibility TEXT NOT NULL DEFAULT 'internal'")
        if "idempotency_key" not in event_columns:
            conn.execute("ALTER TABLE runtime_control_events ADD COLUMN idempotency_key TEXT")
        if "payload_kind" not in event_columns:
            conn.execute("ALTER TABLE runtime_control_events ADD COLUMN payload_kind TEXT NOT NULL DEFAULT 'compact'")
        if "payload_size_bytes" not in event_columns:
            conn.execute("ALTER TABLE runtime_control_events ADD COLUMN payload_size_bytes INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                UPDATE runtime_control_events
                SET payload_size_bytes = length(CAST(payload_json AS BLOB))
                """
            )
        if "projection_attempt_count" not in event_columns:
            conn.execute(
                "ALTER TABLE runtime_control_events ADD COLUMN projection_attempt_count INTEGER NOT NULL DEFAULT 0"
            )
        if "last_projection_error_code" not in event_columns:
            conn.execute("ALTER TABLE runtime_control_events ADD COLUMN last_projection_error_code TEXT")
        if "projected_at" not in event_columns:
            conn.execute("ALTER TABLE runtime_control_events ADD COLUMN projected_at TEXT")

    _create_schema(conn)


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    _create_schema(conn)


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    _create_schema(conn)


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    _create_schema(conn)
    _ensure_requirement_amendment_provenance_column(conn)


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    _create_schema(conn)
    _ensure_candidate_identity_version_columns(conn)


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    _create_schema(conn)
    _ensure_candidate_evidence_source_references_column(conn)


def _ensure_candidate_evidence_source_references_column(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "runtime_control_candidate_evidence"):
        return
    if "source_references_json" not in _column_names(conn, "runtime_control_candidate_evidence"):
        conn.execute(
            "ALTER TABLE runtime_control_candidate_evidence "
            "ADD COLUMN source_references_json TEXT NOT NULL DEFAULT '[]'"
        )


def _ensure_candidate_identity_version_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "runtime_control_candidate_identities"):
        return
    columns = _column_names(conn, "runtime_control_candidate_identities")
    json_columns = (
        "equivalent_latest_resume_ids_json",
        "display_source_evidence_ids_json",
        "conflicting_resume_ids_json",
        "incomparable_resume_ids_json",
        "safe_reason_codes_json",
    )
    for column in json_columns:
        if column not in columns:
            conn.execute(
                f"ALTER TABLE runtime_control_candidate_identities "
                f"ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
            )
    if "content_version_key" not in columns:
        conn.execute(
            "ALTER TABLE runtime_control_candidate_identities "
            "ADD COLUMN content_version_key TEXT NOT NULL DEFAULT ''"
        )


def _ensure_requirement_amendment_provenance_column(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "runtime_requirement_amendments"):
        return
    if "provenance_json" not in _column_names(conn, "runtime_requirement_amendments"):
        conn.execute("ALTER TABLE runtime_requirement_amendments ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _replace_snapshot(
    conn: sqlite3.Connection,
    snapshot: RuntimeRunSnapshot,
    *,
    latest_event_seq: int,
) -> None:
    conn.execute(
        """
        INSERT INTO runtime_control_snapshots (
            runtime_run_id, status, current_stage, current_round,
            latest_event_seq, snapshot_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(runtime_run_id) DO UPDATE SET
            status = excluded.status,
            current_stage = excluded.current_stage,
            current_round = excluded.current_round,
            latest_event_seq = excluded.latest_event_seq,
            snapshot_json = excluded.snapshot_json,
            updated_at = excluded.updated_at
        """,
        (
            snapshot.runtime_run_id,
            snapshot.status,
            snapshot.current_stage,
            snapshot.current_round,
            latest_event_seq,
            _json(snapshot.snapshot),
            snapshot.updated_at,
        ),
    )


def _append_event_in_transaction(
    conn: sqlite3.Connection,
    event: RuntimeControlEventInput,
    *,
    snapshot: RuntimeRunSnapshot | None,
    run_status: str | None,
    stop_reason_code: str | None,
    completed_at: str | None,
    latest_checkpoint_id: str | None,
) -> RuntimeControlEvent:
    if event.idempotency_key is not None:
        existing = _event_row_by_idempotency_key(conn, event.runtime_run_id, event.idempotency_key)
        if existing is not None:
            return _event_from_row(existing)
    row = conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (event.runtime_run_id,),
    ).fetchone()
    if row is None:
        raise RuntimeControlLookupError("runtime_run_not_found")
    target_status = run_status if run_status is not None else row["status"]
    require_run_transition(row["status"], target_status)
    payload_json, payload_size_bytes = _json_with_size(
        event.payload,
        reason_code="runtime_event_payload_too_large",
    )
    event_seq = int(row["latest_event_seq"]) + 1
    try:
        conn.execute(
            """
            INSERT INTO runtime_control_events (
                event_id, runtime_run_id, event_seq, event_type, stage, round_no,
                source_id, status, summary, payload_json, schema_version, visibility,
                idempotency_key, payload_kind, payload_size_bytes, projection_attempt_count,
                last_projection_error_code, projected_at, workbench_event_global_seq, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.runtime_run_id,
                event_seq,
                event.event_type,
                event.stage,
                event.round_no,
                event.source_id,
                event.status,
                event.summary,
                payload_json,
                event.schema_version,
                event.visibility,
                event.idempotency_key,
                event.payload_kind,
                payload_size_bytes,
                event.projection_attempt_count,
                event.last_projection_error_code,
                event.projected_at,
                event.workbench_event_global_seq,
                event.created_at,
            ),
        )
    except sqlite3.IntegrityError:
        if event.idempotency_key is not None:
            existing = _event_row_by_idempotency_key(conn, event.runtime_run_id, event.idempotency_key)
            if existing is not None:
                return _event_from_row(existing)
        raise
    conn.execute(
        """
        UPDATE runtime_control_runs
        SET latest_event_seq = ?, status = ?, current_stage = ?, current_round = ?, updated_at = ?,
            stop_reason_code = COALESCE(?, stop_reason_code),
            completed_at = COALESCE(?, completed_at),
            latest_checkpoint_id = COALESCE(?, latest_checkpoint_id)
        WHERE runtime_run_id = ?
        """,
        (
            event_seq,
            target_status,
            event.stage,
            event.round_no,
            event.created_at,
            stop_reason_code,
            completed_at,
            latest_checkpoint_id,
            event.runtime_run_id,
        ),
    )
    if snapshot is not None:
        _replace_snapshot(conn, snapshot, latest_event_seq=event_seq)
    stored = conn.execute(
        """
        SELECT *
        FROM runtime_control_events
        WHERE runtime_run_id = ? AND event_id = ?
        """,
        (event.runtime_run_id, event.event_id),
    ).fetchone()
    return _event_from_row(stored)


def _run_row(conn: sqlite3.Connection, runtime_run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (runtime_run_id,),
    ).fetchone()


def _run_row_by_run_intent(conn: sqlite3.Connection, run_intent_id: str | None) -> sqlite3.Row | None:
    if run_intent_id is None:
        return None
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE run_intent_id = ?",
        (run_intent_id,),
    ).fetchone()


def _run_row_by_start_idempotency_key(
    conn: sqlite3.Connection,
    start_idempotency_key: str | None,
) -> sqlite3.Row | None:
    if start_idempotency_key is None:
        return None
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE start_idempotency_key = ?",
        (start_idempotency_key,),
    ).fetchone()


def _event_row_by_idempotency_key(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_events
        WHERE runtime_run_id = ? AND idempotency_key = ?
        """,
        (runtime_run_id, idempotency_key),
    ).fetchone()


def _next_runnable_run_row(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str | None,
) -> sqlite3.Row | None:
    clauses = ["run.status IN ('queued', 'resume_requested')"]
    params: list[object] = []
    if runtime_run_id is not None:
        clauses.append("run.runtime_run_id = ?")
        params.append(runtime_run_id)
    return conn.execute(
        f"""
        SELECT run.*
        FROM runtime_control_runs AS run
        WHERE {' AND '.join(clauses)}
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases AS lease
            WHERE lease.runtime_run_id = run.runtime_run_id
              AND lease.status = 'active'
          )
        ORDER BY run.created_at ASC, run.runtime_run_id ASC
        LIMIT 1
        """,
        params,
    ).fetchone()


def _stage_output_row(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    stage: str,
    node_key: str,
    round_key: int,
    output_kind: str,
    schema_version: str | None,
) -> sqlite3.Row | None:
    schema_clause = "AND schema_version = ?" if schema_version is not None else ""
    params: list[object] = [runtime_run_id, stage, node_key, round_key, output_kind]
    if schema_version is not None:
        params.append(schema_version)
    return conn.execute(
        f"""
        SELECT *
        FROM runtime_control_stage_outputs
        WHERE runtime_run_id = ?
          AND stage = ?
          AND node_key = ?
          AND round_key = ?
          AND output_kind = ?
          {schema_clause}
        ORDER BY schema_version DESC, created_at DESC, rowid DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def _active_lease_row(conn: sqlite3.Connection, runtime_run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND status = 'active'
        ORDER BY attempt_no DESC
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()


def _require_active_executor(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    executor_id: str,
    *,
    attempt_no: int | None = None,
    observed_at: str | None = None,
) -> sqlite3.Row:
    attempt_clause = "AND attempt_no = ?" if attempt_no is not None else ""
    params: list[object] = [runtime_run_id, executor_id]
    if attempt_no is not None:
        params.append(attempt_no)
    row = conn.execute(
        f"""
        SELECT *
        FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND executor_id = ? AND status = 'active'
          {attempt_clause}
        ORDER BY attempt_no DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        raise RuntimeControlError("runtime_executor_stale")
    if observed_at is not None and timestamp_lte(row["lease_expires_at"], observed_at):
        raise RuntimeControlError("runtime_executor_lease_expired")
    return row


def _retention_counts(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    developer_event_older_than: str,
    internal_event_older_than: str,
    checkpoint_older_than: str,
    lease_older_than: str,
    command_older_than: str,
    stage_output_older_than: str,
    final_summary_older_than: str,
    database_path: Path,
) -> dict[str, int]:
    nonpublic_event_count, nonpublic_event_bytes = _retention_nonpublic_event_stats(
        conn,
        terminal_run_older_than=terminal_run_older_than,
        developer_event_older_than=developer_event_older_than,
        internal_event_older_than=internal_event_older_than,
    )
    checkpoint_count, checkpoint_bytes = _retention_checkpoint_stats(
        conn,
        terminal_run_older_than=terminal_run_older_than,
        checkpoint_older_than=checkpoint_older_than,
    )
    executor_lease_count, executor_lease_bytes = _retention_executor_lease_stats(
        conn,
        terminal_run_older_than=terminal_run_older_than,
        lease_older_than=lease_older_than,
    )
    command_count, command_bytes = _retention_command_stats(
        conn,
        terminal_run_older_than=terminal_run_older_than,
        command_older_than=command_older_than,
    )
    stage_output_count, stage_output_bytes = _retention_stage_output_stats(
        conn,
        terminal_run_older_than=terminal_run_older_than,
        stage_output_older_than=stage_output_older_than,
    )
    final_summary_count, final_summary_bytes = _retention_final_summary_stats(
        conn,
        terminal_run_older_than=terminal_run_older_than,
        final_summary_older_than=final_summary_older_than,
    )
    wal_path = Path(f"{database_path}-wal")
    return {
        "nonpublic_event": nonpublic_event_count,
        "checkpoint": checkpoint_count,
        "executor_lease": executor_lease_count,
        "command": command_count,
        "stage_output": stage_output_count,
        "final_summary": final_summary_count,
        "nonpublic_event_estimated_bytes": nonpublic_event_bytes,
        "checkpoint_estimated_bytes": checkpoint_bytes,
        "executor_lease_estimated_bytes": executor_lease_bytes,
        "command_estimated_bytes": command_bytes,
        "stage_output_estimated_bytes": stage_output_bytes,
        "final_summary_estimated_bytes": final_summary_bytes,
        "database_size_bytes": _file_size(database_path),
        "wal_size_bytes": _file_size(wal_path),
    }


def _retention_nonpublic_event_stats(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    developer_event_older_than: str,
    internal_event_older_than: str,
) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(
            e.payload_size_bytes
            + length(e.event_id)
            + length(e.event_type)
            + length(e.stage)
            + length(e.status)
            + length(e.summary)
          ), 0) AS estimated_bytes
        FROM runtime_control_events AS e
        JOIN runtime_control_runs AS r ON r.runtime_run_id = e.runtime_run_id
        WHERE r.status IN ('cancelled', 'completed', 'failed')
          AND r.completed_at IS NOT NULL
          AND r.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = r.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND e.visibility <> 'public'
          AND (
            (e.visibility = 'developer' AND e.created_at < ?)
            OR (e.visibility <> 'developer' AND e.created_at < ?)
          )
        """,
        (terminal_run_older_than, developer_event_older_than, internal_event_older_than),
    ).fetchone()
    return _count_and_bytes(row)


def _retention_checkpoint_stats(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    checkpoint_older_than: str,
) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(
            length(checkpoint.checkpoint_id)
            + length(checkpoint.stage)
            + length(checkpoint.safe_boundary)
            + length(checkpoint.run_state_json)
            + length(checkpoint.source_plan_json)
            + length(checkpoint.pending_commands_json)
            + COALESCE(length(checkpoint.artifact_manifest_ref), 0)
          ), 0) AS estimated_bytes
        FROM runtime_control_checkpoints AS checkpoint
        JOIN runtime_control_runs AS run ON run.runtime_run_id = checkpoint.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND checkpoint.created_at < ?
        """,
        (terminal_run_older_than, checkpoint_older_than),
    ).fetchone()
    return _count_and_bytes(row)


def _retention_executor_lease_stats(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    lease_older_than: str,
) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(
            length(lease.lease_id)
            + length(lease.runtime_run_id)
            + length(lease.executor_id)
            + length(lease.status)
            + length(lease.acquired_at)
            + COALESCE(length(lease.heartbeat_at), 0)
            + length(lease.lease_expires_at)
            + COALESCE(length(lease.released_at), 0)
            + COALESCE(length(lease.reason_code), 0)
          ), 0) AS estimated_bytes
        FROM runtime_control_executor_leases AS lease
        JOIN runtime_control_runs AS run ON run.runtime_run_id = lease.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND lease.status <> 'active'
          AND COALESCE(lease.released_at, lease.lease_expires_at, lease.acquired_at) < ?
        """,
        (terminal_run_older_than, lease_older_than),
    ).fetchone()
    return _count_and_bytes(row)


def _retention_command_stats(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    command_older_than: str,
) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(
            length(command.command_id)
            + length(command.command_type)
            + length(command.payload_json)
            + length(command.status)
            + length(command.conflict_group)
            + COALESCE(length(command.requested_by), 0)
            + length(command.requested_at)
            + COALESCE(length(command.applied_at), 0)
            + COALESCE(length(command.rejected_reason_code), 0)
          ), 0) AS estimated_bytes
        FROM runtime_control_commands AS command
        JOIN runtime_control_runs AS run ON run.runtime_run_id = command.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND command.status IN ('applied', 'superseded', 'rejected')
          AND COALESCE(command.applied_at, command.requested_at) < ?
        """,
        (terminal_run_older_than, command_older_than),
    ).fetchone()
    return _count_and_bytes(row)


def _retention_stage_output_stats(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    stage_output_older_than: str,
) -> tuple[int, int]:
    placeholders = ",".join("?" for _ in _REQUIRED_STAGE_OUTPUT_KINDS)
    row = conn.execute(
        f"""
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(
            output.payload_size_bytes
            + length(output.output_id)
            + length(output.stage)
            + length(output.node_key)
            + length(output.output_kind)
            + length(output.schema_version)
          ), 0) AS estimated_bytes
        FROM runtime_control_stage_outputs AS output
        JOIN runtime_control_runs AS run ON run.runtime_run_id = output.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND output.created_at < ?
          AND output.output_kind NOT IN ({placeholders})
        """,
        (
            terminal_run_older_than,
            stage_output_older_than,
            *sorted(_REQUIRED_STAGE_OUTPUT_KINDS),
        ),
    ).fetchone()
    return _count_and_bytes(row)


def _retention_final_summary_stats(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    final_summary_older_than: str,
) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(
            length(summary.summary_id)
            + length(summary.runtime_run_id)
            + length(summary.idempotency_key)
            + COALESCE(length(summary.user_instruction), 0)
            + length(summary.summary_json)
          ), 0) AS estimated_bytes
        FROM runtime_control_final_summaries AS summary
        JOIN runtime_control_runs AS run ON run.runtime_run_id = summary.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND summary.created_at < ?
        """,
        (terminal_run_older_than, final_summary_older_than),
    ).fetchone()
    return _count_and_bytes(row)


def _count_and_bytes(row: sqlite3.Row) -> tuple[int, int]:
    return int(row["row_count"]), int(row["estimated_bytes"])


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _retention_candidate_ids(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    developer_event_older_than: str,
    internal_event_older_than: str,
    checkpoint_older_than: str,
    lease_older_than: str,
    command_older_than: str,
    stage_output_older_than: str,
    final_summary_older_than: str,
    limit: int,
) -> dict[str, list[str]]:
    return {
        "nonpublic_event": _retention_nonpublic_event_ids(
            conn,
            terminal_run_older_than=terminal_run_older_than,
            developer_event_older_than=developer_event_older_than,
            internal_event_older_than=internal_event_older_than,
            limit=limit,
        ),
        "checkpoint": _retention_checkpoint_ids(
            conn,
            terminal_run_older_than=terminal_run_older_than,
            checkpoint_older_than=checkpoint_older_than,
            limit=limit,
        ),
        "executor_lease": _retention_executor_lease_ids(
            conn,
            terminal_run_older_than=terminal_run_older_than,
            lease_older_than=lease_older_than,
            limit=limit,
        ),
        "command": _retention_command_ids(
            conn,
            terminal_run_older_than=terminal_run_older_than,
            command_older_than=command_older_than,
            limit=limit,
        ),
        "stage_output": _retention_stage_output_ids(
            conn,
            terminal_run_older_than=terminal_run_older_than,
            stage_output_older_than=stage_output_older_than,
            limit=limit,
        ),
        "final_summary": _retention_final_summary_ids(
            conn,
            terminal_run_older_than=terminal_run_older_than,
            final_summary_older_than=final_summary_older_than,
            limit=limit,
        ),
    }


def _retention_nonpublic_event_ids(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    developer_event_older_than: str,
    internal_event_older_than: str,
    limit: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT e.event_id
        FROM runtime_control_events AS e
        JOIN runtime_control_runs AS r ON r.runtime_run_id = e.runtime_run_id
        WHERE r.status IN ('cancelled', 'completed', 'failed')
          AND r.completed_at IS NOT NULL
          AND r.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = r.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND e.visibility <> 'public'
          AND (
            (e.visibility = 'developer' AND e.created_at < ?)
            OR (e.visibility <> 'developer' AND e.created_at < ?)
          )
        ORDER BY e.created_at ASC, e.rowid ASC
        LIMIT ?
        """,
        (terminal_run_older_than, developer_event_older_than, internal_event_older_than, limit),
    ).fetchall()
    return [row["event_id"] for row in rows]


def _retention_checkpoint_ids(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    checkpoint_older_than: str,
    limit: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT checkpoint.checkpoint_id
        FROM runtime_control_checkpoints AS checkpoint
        JOIN runtime_control_runs AS run ON run.runtime_run_id = checkpoint.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND checkpoint.created_at < ?
        ORDER BY checkpoint.created_at ASC, checkpoint.rowid ASC
        LIMIT ?
        """,
        (terminal_run_older_than, checkpoint_older_than, limit),
    ).fetchall()
    return [row["checkpoint_id"] for row in rows]


def _retention_executor_lease_ids(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    lease_older_than: str,
    limit: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT lease.lease_id
        FROM runtime_control_executor_leases AS lease
        JOIN runtime_control_runs AS run ON run.runtime_run_id = lease.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND lease.status <> 'active'
          AND COALESCE(lease.released_at, lease.lease_expires_at, lease.acquired_at) < ?
        ORDER BY COALESCE(lease.released_at, lease.lease_expires_at, lease.acquired_at) ASC, lease.rowid ASC
        LIMIT ?
        """,
        (terminal_run_older_than, lease_older_than, limit),
    ).fetchall()
    return [row["lease_id"] for row in rows]


def _retention_command_ids(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    command_older_than: str,
    limit: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT command.command_id
        FROM runtime_control_commands AS command
        JOIN runtime_control_runs AS run ON run.runtime_run_id = command.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND command.status IN ('applied', 'superseded', 'rejected')
          AND COALESCE(command.applied_at, command.requested_at) < ?
        ORDER BY COALESCE(command.applied_at, command.requested_at) ASC, command.rowid ASC
        LIMIT ?
        """,
        (terminal_run_older_than, command_older_than, limit),
    ).fetchall()
    return [row["command_id"] for row in rows]


def _retention_stage_output_ids(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    stage_output_older_than: str,
    limit: int,
) -> list[str]:
    placeholders = ",".join("?" for _ in _REQUIRED_STAGE_OUTPUT_KINDS)
    rows = conn.execute(
        f"""
        SELECT output.output_id
        FROM runtime_control_stage_outputs AS output
        JOIN runtime_control_runs AS run ON run.runtime_run_id = output.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND output.created_at < ?
          AND output.output_kind NOT IN ({placeholders})
        ORDER BY output.created_at ASC, output.rowid ASC
        LIMIT ?
        """,
        (
            terminal_run_older_than,
            stage_output_older_than,
            *sorted(_REQUIRED_STAGE_OUTPUT_KINDS),
            limit,
        ),
    ).fetchall()
    return [row["output_id"] for row in rows]


def _retention_final_summary_ids(
    conn: sqlite3.Connection,
    *,
    terminal_run_older_than: str,
    final_summary_older_than: str,
    limit: int,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT summary.summary_id
        FROM runtime_control_final_summaries AS summary
        JOIN runtime_control_runs AS run ON run.runtime_run_id = summary.runtime_run_id
        WHERE run.status IN ('cancelled', 'completed', 'failed')
          AND run.completed_at IS NOT NULL
          AND run.completed_at < ?
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases active_lease
            WHERE active_lease.runtime_run_id = run.runtime_run_id
              AND active_lease.status = 'active'
          )
          AND summary.created_at < ?
        ORDER BY summary.created_at ASC, summary.rowid ASC
        LIMIT ?
        """,
        (terminal_run_older_than, final_summary_older_than, limit),
    ).fetchall()
    return [row["summary_id"] for row in rows]


def _clear_latest_checkpoint_refs(conn: sqlite3.Connection, checkpoint_ids: list[str]) -> None:
    if not checkpoint_ids:
        return
    placeholders = ",".join("?" for _ in checkpoint_ids)
    conn.execute(
        f"""
        UPDATE runtime_control_runs
        SET latest_checkpoint_id = NULL
        WHERE latest_checkpoint_id IN ({placeholders})
        """,
        checkpoint_ids,
    )


def _delete_rows_by_ids(
    conn: sqlite3.Connection,
    table_name: str,
    id_column: str,
    ids: list[str],
) -> None:
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM {table_name} WHERE {id_column} IN ({placeholders})", ids)


def _record_pending_artifact_deletion(
    database_path: Path,
    *,
    artifact_ref_id: str,
    artifact_kind: str,
    original_path: Path,
    quarantine_path: Path,
    reason_code: str,
    error: OSError,
) -> None:
    now = _migration_now()
    deletion_id = "rtartifact_delete_" + sha256(str(quarantine_path).encode("utf-8")).hexdigest()[:32]
    with sqlite3.connect(database_path) as conn:
        _create_schema(conn)
        conn.execute(
            """
            INSERT INTO runtime_control_artifact_deletions (
                deletion_id, artifact_ref_id, artifact_kind, original_path, quarantine_path,
                reason_code, status, attempt_count, last_error_code,
                requested_at, last_attempt_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?)
            ON CONFLICT(deletion_id) DO UPDATE SET
                status = 'pending',
                attempt_count = runtime_control_artifact_deletions.attempt_count + 1,
                last_error_code = excluded.last_error_code,
                last_attempt_at = excluded.last_attempt_at,
                metadata_json = excluded.metadata_json
            """,
            (
                deletion_id,
                artifact_ref_id,
                artifact_kind,
                str(original_path),
                str(quarantine_path),
                reason_code,
                type(error).__name__,
                now,
                now,
                _json({"message": str(error)}),
            ),
        )


def _record_stage_output_artifact_ref(
    conn: sqlite3.Connection,
    *,
    artifact_ref_id: str,
    runtime_run_id: str,
    output_id: str,
    stage: str,
    output_kind: str,
    schema_version: str,
    payload_hash: str,
    payload_size_bytes: int,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO runtime_control_artifact_refs (
            artifact_ref_id, runtime_run_id, artifact_kind, safe_uri, visibility, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, 'internal', ?, ?)
        ON CONFLICT(artifact_ref_id) DO UPDATE SET
            runtime_run_id = excluded.runtime_run_id,
            artifact_kind = excluded.artifact_kind,
            safe_uri = excluded.safe_uri,
            visibility = excluded.visibility,
            metadata_json = excluded.metadata_json,
            created_at = excluded.created_at
        """,
        (
            artifact_ref_id,
            runtime_run_id,
            _RUNTIME_STAGE_OUTPUT_ARTIFACT_KIND,
            f"artifact://runtime-control/stage-output/{artifact_ref_id}.json",
            _json(
                {
                    "outputId": output_id,
                    "stage": stage,
                    "outputKind": output_kind,
                    "schemaVersion": schema_version,
                    "payloadHash": payload_hash,
                    "payloadSizeBytes": payload_size_bytes,
                }
            ),
            created_at,
        ),
    )


def _stage_output_file_artifact_ref_ids_for_output_ids(
    conn: sqlite3.Connection,
    output_ids: list[str],
) -> list[str]:
    if not output_ids:
        return []
    placeholders = ",".join("?" for _ in output_ids)
    rows = conn.execute(
        f"""
        SELECT artifact_ref_id, output_json
        FROM runtime_control_stage_outputs
        WHERE output_id IN ({placeholders})
        """,
        output_ids,
    ).fetchall()
    return _stage_output_file_artifact_ref_ids(rows)


def _stage_output_file_artifact_ref_ids(rows: list[sqlite3.Row]) -> list[str]:
    ref_ids: list[str] = []
    for row in rows:
        ref_id = row["artifact_ref_id"]
        if not isinstance(ref_id, str):
            continue
        if _is_stage_output_artifact_marker(_json_object(row["output_json"]), ref_id):
            ref_ids.append(ref_id)
    return list(dict.fromkeys(ref_ids))


def _delete_stage_output_artifact_files(database_path: Path, artifact_ref_ids: list[str]) -> None:
    for artifact_ref_id in artifact_ref_ids:
        _stage_output_artifact_path(database_path, artifact_ref_id).unlink(missing_ok=True)


def _quarantine_stage_output_artifact_files(
    database_path: Path,
    artifact_ref_ids: list[str],
) -> list[tuple[Path, Path]]:
    quarantined: list[tuple[Path, Path]] = []
    try:
        for artifact_ref_id in artifact_ref_ids:
            artifact_path = _stage_output_artifact_path(database_path, artifact_ref_id)
            if not artifact_path.exists():
                continue
            quarantine_path = artifact_path.with_name(f"{artifact_path.name}.delete-{uuid4().hex}")
            artifact_path.replace(quarantine_path)
            quarantined.append((quarantine_path, artifact_path))
    except OSError:
        _restore_quarantined_stage_output_artifacts(quarantined)
        raise
    return quarantined


def _delete_quarantined_stage_output_artifacts(
    database_path: Path,
    quarantined: list[tuple[Path, Path]],
    *,
    reason_code: str,
) -> None:
    failures: list[OSError] = []
    for quarantine_path, artifact_path in quarantined:
        try:
            quarantine_path.unlink(missing_ok=True)
        except OSError as exc:
            _record_pending_artifact_deletion(
                database_path,
                artifact_ref_id=artifact_path.stem,
                artifact_kind=_RUNTIME_STAGE_OUTPUT_ARTIFACT_KIND,
                original_path=artifact_path,
                quarantine_path=quarantine_path,
                reason_code=reason_code,
                error=exc,
            )
            failures.append(exc)
    if failures:
        raise failures[0]


def _restore_quarantined_stage_output_artifacts(quarantined: list[tuple[Path, Path]]) -> None:
    for quarantine_path, artifact_path in reversed(quarantined):
        if quarantine_path.exists():
            quarantine_path.replace(artifact_path)


def _sync_candidate_truth_from_checkpoint(conn: sqlite3.Connection, checkpoint: RuntimeCheckpoint) -> None:
    truth = candidate_truth_from_run_state(
        runtime_run_id=checkpoint.runtime_run_id,
        run_state=checkpoint.run_state,
        source_checkpoint_id=checkpoint.checkpoint_id,
        observed_at=checkpoint.created_at,
    )
    for identity in truth.identities:
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_identities (
                runtime_run_id, identity_id, canonical_resume_id, merged_resume_ids_json,
                source_evidence_ids_json, equivalent_latest_resume_ids_json,
                display_source_evidence_ids_json, conflicting_resume_ids_json,
                incomparable_resume_ids_json, content_version_key, safe_reason_codes_json,
                display_name, title, company, location, summary,
                score, fit_bucket, source_round, payload_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime_run_id, identity_id) DO UPDATE SET
                canonical_resume_id = excluded.canonical_resume_id,
                merged_resume_ids_json = excluded.merged_resume_ids_json,
                source_evidence_ids_json = excluded.source_evidence_ids_json,
                equivalent_latest_resume_ids_json = excluded.equivalent_latest_resume_ids_json,
                display_source_evidence_ids_json = excluded.display_source_evidence_ids_json,
                conflicting_resume_ids_json = excluded.conflicting_resume_ids_json,
                incomparable_resume_ids_json = excluded.incomparable_resume_ids_json,
                content_version_key = excluded.content_version_key,
                safe_reason_codes_json = excluded.safe_reason_codes_json,
                display_name = excluded.display_name,
                title = excluded.title,
                company = excluded.company,
                location = excluded.location,
                summary = excluded.summary,
                score = excluded.score,
                fit_bucket = excluded.fit_bucket,
                source_round = excluded.source_round,
                payload_hash = excluded.payload_hash,
                updated_at = excluded.updated_at
            """,
            (
                identity.runtime_run_id,
                identity.identity_id,
                identity.canonical_resume_id,
                _json(identity.merged_resume_ids),
                _json(identity.source_evidence_ids),
                _json(identity.equivalent_latest_resume_ids),
                _json(identity.display_source_evidence_ids),
                _json(identity.conflicting_resume_ids),
                _json(identity.incomparable_resume_ids),
                identity.content_version_key,
                _json(identity.safe_reason_codes),
                identity.display_name,
                identity.title,
                identity.company,
                identity.location,
                identity.summary,
                identity.score,
                identity.fit_bucket,
                identity.source_round,
                identity.payload_hash,
                identity.updated_at,
            ),
        )
    for evidence in truth.evidence:
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_evidence (
                runtime_run_id, evidence_id, identity_id, resume_id, source_kind, evidence_level,
                provider_candidate_key_hash, score, fit_bucket, source_references_json,
                payload_json, payload_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime_run_id, evidence_id) DO UPDATE SET
                identity_id = excluded.identity_id,
                resume_id = excluded.resume_id,
                source_kind = excluded.source_kind,
                evidence_level = excluded.evidence_level,
                provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                score = excluded.score,
                fit_bucket = excluded.fit_bucket,
                source_references_json = excluded.source_references_json,
                payload_json = excluded.payload_json,
                payload_hash = excluded.payload_hash,
                updated_at = excluded.updated_at
            """,
            (
                evidence.runtime_run_id,
                evidence.evidence_id,
                evidence.identity_id,
                evidence.resume_id,
                evidence.source_kind,
                evidence.evidence_level,
                evidence.provider_candidate_key_hash,
                evidence.score,
                evidence.fit_bucket,
                _json([reference.model_dump(mode="json") for reference in evidence.source_references]),
                _json(evidence.payload),
                evidence.payload_hash,
                evidence.updated_at,
            ),
        )
    for revision in truth.finalization_revisions:
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_finalization_revisions (
                runtime_run_id, revision, reason_code, candidate_identity_ids_json,
                coverage_summary_json, source_checkpoint_id, payload_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime_run_id, revision) DO UPDATE SET
                reason_code = excluded.reason_code,
                candidate_identity_ids_json = excluded.candidate_identity_ids_json,
                coverage_summary_json = excluded.coverage_summary_json,
                source_checkpoint_id = excluded.source_checkpoint_id,
                payload_hash = excluded.payload_hash,
                created_at = excluded.created_at
            """,
            (
                revision.runtime_run_id,
                revision.revision,
                revision.reason_code,
                _json(revision.candidate_identity_ids),
                _json(revision.coverage_summary),
                revision.source_checkpoint_id,
                revision.payload_hash,
                revision.created_at,
            ),
        )


def _normalize_run_record(run: RuntimeRunRecord) -> RuntimeRunRecord:
    return run.model_copy(
        update={
            "run_intent_id": run.run_intent_id or run.runtime_run_id,
            "start_idempotency_key": run.start_idempotency_key or run.run_intent_id or run.runtime_run_id,
            "run_kind": run.run_kind or "primary",
        }
    )


def _run_from_row(row: sqlite3.Row) -> RuntimeRunRecord:
    return RuntimeRunRecord(
        runtime_run_id=row["runtime_run_id"],
        run_intent_id=row["run_intent_id"],
        start_idempotency_key=row["start_idempotency_key"],
        run_kind=row["run_kind"],
        agent_conversation_id=row["agent_conversation_id"],
        workbench_session_id=row["workbench_session_id"],
        approved_requirement_revision_id=row["approved_requirement_revision_id"],
        status=row["status"],
        current_stage=row["current_stage"],
        current_round=row["current_round"],
        latest_checkpoint_id=row["latest_checkpoint_id"],
        latest_event_seq=int(row["latest_event_seq"]),
        source_ids=_json_string_list(row["source_ids_json"]),
        stop_reason_code=row["stop_reason_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _lease_from_row(row: sqlite3.Row) -> RuntimeExecutorLease:
    return RuntimeExecutorLease(
        lease_id=row["lease_id"],
        runtime_run_id=row["runtime_run_id"],
        executor_id=row["executor_id"],
        attempt_no=int(row["attempt_no"]),
        status=row["status"],
        acquired_at=row["acquired_at"],
        heartbeat_at=row["heartbeat_at"],
        lease_expires_at=row["lease_expires_at"],
        released_at=row["released_at"],
        reason_code=row["reason_code"],
    )


def _checkpoint_from_row(row: sqlite3.Row) -> RuntimeCheckpoint:
    return RuntimeCheckpoint(
        checkpoint_id=row["checkpoint_id"],
        runtime_run_id=row["runtime_run_id"],
        stage=row["stage"],
        round_no=row["round_no"],
        safe_boundary=row["safe_boundary"],
        run_state=_json_object(row["run_state_json"]),
        source_plan=_json_object(row["source_plan_json"]),
        pending_commands=[_string_key_dict(item) for item in _json_list(row["pending_commands_json"]) if _string_key_dict(item)],
        artifact_manifest_ref=row["artifact_manifest_ref"],
        schema_version=row["schema_version"],
        created_at=row["created_at"],
    )


def _checkpoint_from_row_or_failure(row: sqlite3.Row) -> RuntimeCheckpoint | RuntimeCheckpointLoadFailure:
    checkpoint_id = row["checkpoint_id"]
    if row["schema_version"] != RUNTIME_CHECKPOINT_SCHEMA_VERSION:
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=checkpoint_id,
            reason_code="runtime_checkpoint_schema_unsupported",
        )
    try:
        return _checkpoint_from_row(row)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=checkpoint_id,
            reason_code="runtime_checkpoint_corrupt",
        )


def _snapshot_from_row(row: sqlite3.Row) -> RuntimeRunSnapshot:
    return RuntimeRunSnapshot(
        runtime_run_id=row["runtime_run_id"],
        status=row["status"],
        current_stage=row["current_stage"],
        current_round=row["current_round"],
        latest_event_seq=int(row["latest_event_seq"]),
        snapshot=_json_object(row["snapshot_json"]),
        updated_at=row["updated_at"],
    )


def _command_from_row(row: sqlite3.Row) -> RuntimeCommand:
    return RuntimeCommand(
        command_id=row["command_id"],
        runtime_run_id=row["runtime_run_id"],
        command_type=row["command_type"],
        payload=_json_object(row["payload_json"]),
        status=row["status"],
        conflict_group=row["conflict_group"],
        supersedes_command_id=row["supersedes_command_id"],
        superseded_by_command_id=row["superseded_by_command_id"],
        target_round_no=row["target_round_no"],
        idempotency_key=row["idempotency_key"],
        requested_by=row["requested_by"],
        requested_at=row["requested_at"],
        applied_at=row["applied_at"],
        rejected_reason_code=row["rejected_reason_code"],
    )


def _event_from_row(row: sqlite3.Row) -> RuntimeControlEvent:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict):
        payload = {}
    return RuntimeControlEvent(
        event_id=row["event_id"],
        runtime_run_id=row["runtime_run_id"],
        event_seq=int(row["event_seq"]),
        event_type=row["event_type"],
        stage=row["stage"],
        round_no=row["round_no"],
        source_id=row["source_id"],
        status=row["status"],
        summary=row["summary"],
        payload=payload,
        schema_version=row["schema_version"],
        visibility=row["visibility"],
        idempotency_key=row["idempotency_key"],
        payload_kind=row["payload_kind"],
        payload_size_bytes=int(row["payload_size_bytes"]),
        projection_attempt_count=int(row["projection_attempt_count"]),
        last_projection_error_code=row["last_projection_error_code"],
        projected_at=row["projected_at"],
        workbench_event_global_seq=row["workbench_event_global_seq"],
        created_at=row["created_at"],
    )


def _stage_output_from_row(row: sqlite3.Row, *, database_path: Path) -> RuntimeStageOutput:
    output = _json_object(row["output_json"])
    artifact_ref_id = row["artifact_ref_id"]
    if isinstance(artifact_ref_id, str) and _is_stage_output_artifact_marker(output, artifact_ref_id):
        output = _read_stage_output_artifact(
            database_path,
            artifact_ref_id,
            expected_payload_hash=row["payload_hash"],
        )
    return RuntimeStageOutput(
        output_id=row["output_id"],
        runtime_run_id=row["runtime_run_id"],
        stage=row["stage"],
        node_id=row["node_id"],
        node_key=row["node_key"],
        round_no=row["round_no"],
        round_key=int(row["round_key"]),
        output_kind=row["output_kind"],
        schema_version=row["schema_version"],
        output=output,
        payload_hash=row["payload_hash"],
        payload_size_bytes=int(row["payload_size_bytes"]),
        source_event_id=row["source_event_id"],
        source_checkpoint_id=row["source_checkpoint_id"],
        artifact_ref_id=row["artifact_ref_id"],
        created_at=row["created_at"],
    )


def _candidate_identity_from_row(row: sqlite3.Row) -> RuntimeControlCandidateIdentity:
    return RuntimeControlCandidateIdentity(
        runtime_run_id=row["runtime_run_id"],
        identity_id=row["identity_id"],
        canonical_resume_id=row["canonical_resume_id"],
        merged_resume_ids=_json_string_list(row["merged_resume_ids_json"]),
        source_evidence_ids=_json_string_list(row["source_evidence_ids_json"]),
        equivalent_latest_resume_ids=_json_string_list(row["equivalent_latest_resume_ids_json"]),
        display_source_evidence_ids=_json_string_list(row["display_source_evidence_ids_json"]),
        conflicting_resume_ids=_json_string_list(row["conflicting_resume_ids_json"]),
        incomparable_resume_ids=_json_string_list(row["incomparable_resume_ids_json"]),
        content_version_key=row["content_version_key"],
        safe_reason_codes=_json_string_list(row["safe_reason_codes_json"]),
        display_name=row["display_name"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        summary=row["summary"],
        score=row["score"],
        fit_bucket=row["fit_bucket"],
        source_round=row["source_round"],
        payload_hash=row["payload_hash"],
        updated_at=row["updated_at"],
    )


def _candidate_evidence_from_row(row: sqlite3.Row) -> RuntimeControlCandidateEvidence:
    return RuntimeControlCandidateEvidence(
        runtime_run_id=row["runtime_run_id"],
        evidence_id=row["evidence_id"],
        identity_id=row["identity_id"],
        resume_id=row["resume_id"],
        source_kind=row["source_kind"],
        evidence_level=row["evidence_level"],
        provider_candidate_key_hash=row["provider_candidate_key_hash"],
        score=row["score"],
        fit_bucket=row["fit_bucket"],
        source_references=_source_references_from_json(row["source_references_json"]),
        payload=_json_object(row["payload_json"]),
        payload_hash=row["payload_hash"],
        updated_at=row["updated_at"],
    )


def _source_references_from_json(value: str) -> list[SourceReference]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [
        SourceReference(
            source_kind=item["source_kind"],
            display_label=item["display_label"],
            url=item["url"],
        )
        for item in parsed
        if isinstance(item, dict)
        and isinstance(item.get("source_kind"), str)
        and isinstance(item.get("display_label"), str)
        and isinstance(item.get("url"), str)
    ]


def _candidate_finalization_revision_from_row(row: sqlite3.Row) -> RuntimeControlCandidateFinalizationRevision:
    return RuntimeControlCandidateFinalizationRevision(
        runtime_run_id=row["runtime_run_id"],
        revision=int(row["revision"]),
        reason_code=row["reason_code"],
        candidate_identity_ids=_json_string_list(row["candidate_identity_ids_json"]),
        coverage_summary=_json_object(row["coverage_summary_json"]),
        source_checkpoint_id=row["source_checkpoint_id"],
        payload_hash=row["payload_hash"],
        created_at=row["created_at"],
    )


def _draft_from_row(row: sqlite3.Row) -> RequirementDraft:
    sections = json.loads(row["sections_json"])
    if not isinstance(sections, list):
        sections = []
    return RequirementDraft(
        conversation_id=row["agent_conversation_id"],
        draft_revision_id=row["draft_revision_id"],
        base_revision_id=row["base_revision_id"],
        status=row["status"],
        sections=sections,
        created_at=row["created_at"],
    )


def _amendment_from_row(row: sqlite3.Row) -> RequirementAmendment:
    provenance_json = row["provenance_json"] if "provenance_json" in row.keys() else "{}"
    return RequirementAmendment(
        amendment_id=row["amendment_id"],
        agent_conversation_id=row["agent_conversation_id"],
        runtime_run_id=row["runtime_run_id"],
        base_draft_revision_id=row["base_draft_revision_id"],
        result_draft_revision_id=row["result_draft_revision_id"],
        base_approved_requirement_revision_id=row["base_approved_requirement_revision_id"],
        result_approved_requirement_revision_id=row["result_approved_requirement_revision_id"],
        target_round_no=row["target_round_no"],
        effective_boundary=row["effective_boundary"],
        applied_event_id=row["applied_event_id"],
        input_text=row["input_text"],
        target_section_hint=row["target_section_hint"],
        status=row["status"],
        normalized_patch=_json_object(row["normalized_patch_json"]),
        rejected_fragments=_json_list(row["rejected_fragments_json"]),
        review_items=[ReviewItem.model_validate(item) for item in _json_list(row["review_items_json"]) if _string_key_dict(item)],
        provenance=_json_object(provenance_json),
        resolved_patch=_json_object(row["resolved_patch_json"]) if row["resolved_patch_json"] is not None else None,
        superseded_by_amendment_id=row["superseded_by_amendment_id"],
        resolved_at=row["resolved_at"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
    )


def _approved_from_row(row: sqlite3.Row) -> ApprovedRequirementRevision:
    from seektalent.models import RequirementSheet

    return ApprovedRequirementRevision(
        approved_requirement_revision_id=row["approved_requirement_revision_id"],
        draft_revision_id=row["draft_revision_id"],
        base_approved_requirement_revision_id=row["base_approved_requirement_revision_id"],
        source_amendment_id=row["source_amendment_id"],
        agent_conversation_id=row["agent_conversation_id"],
        requirement_sheet=RequirementSheet.model_validate_json(row["requirement_sheet_json"]),
        selected_item_ids=_json_string_list(row["selected_item_ids_json"]),
        deselected_item_ids=_json_string_list(row["deselected_item_ids_json"]),
        created_at=row["created_at"],
    )


def _json_object(value: str) -> dict[str, object]:
    payload = json.loads(value)
    return _string_key_dict(payload)


def _json_list(value: str) -> list[object]:
    payload = json.loads(value)
    return payload if isinstance(payload, list) else []


def _json_string_list(value: str) -> list[str]:
    return [item for item in _json_list(value) if isinstance(item, str)]


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_with_size(value: object, *, reason_code: str) -> tuple[str, int]:
    payload_json = _json(value)
    payload_size_bytes = len(payload_json.encode("utf-8"))
    if payload_size_bytes > MAX_RUNTIME_CONTROL_JSON_BYTES:
        raise RuntimeControlError(reason_code, payload={"payloadSizeBytes": payload_size_bytes})
    return payload_json, payload_size_bytes


def _stage_output_artifact_ref_id(*, output_id: str, payload_hash: str) -> str:
    digest = sha256(f"{output_id}:{payload_hash}".encode("utf-8")).hexdigest()[:32]
    return f"rtartifact_stage_{digest}"


def _write_stage_output_artifact(database_path: Path, *, artifact_ref_id: str, payload_json: str) -> None:
    path = _stage_output_artifact_path(database_path, artifact_ref_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        tmp_path.write_text(payload_json, encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _read_stage_output_artifact(
    database_path: Path,
    artifact_ref_id: str,
    *,
    expected_payload_hash: str,
) -> dict[str, object]:
    path = _stage_output_artifact_path(database_path, artifact_ref_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeControlError("runtime_stage_output_artifact_missing") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeControlError("runtime_stage_output_artifact_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeControlError("runtime_stage_output_artifact_invalid")
    payload_hash = sha256(_json(payload).encode("utf-8")).hexdigest()
    if payload_hash != expected_payload_hash:
        raise RuntimeControlError("runtime_stage_output_artifact_hash_mismatch")
    return payload


def _stage_output_artifact_path(database_path: Path, artifact_ref_id: str) -> Path:
    if not artifact_ref_id or any(
        not (character.isalnum() or character in {"_", "-", "."}) for character in artifact_ref_id
    ):
        raise RuntimeControlError("runtime_stage_output_artifact_ref_invalid")
    return database_path.parent / _RUNTIME_STAGE_OUTPUT_ARTIFACT_DIR / f"{artifact_ref_id}.json"


def _is_stage_output_artifact_marker(output: dict[str, object], artifact_ref_id: str) -> bool:
    return (
        output.get("storage") == "file"
        and output.get("artifactKind") == _RUNTIME_STAGE_OUTPUT_ARTIFACT_KIND
        and output.get("artifactRefId") == artifact_ref_id
    )


def _node_key(node_id: str | None) -> str:
    return node_id or ""


def _round_key(round_no: int | None) -> int:
    return round_no if round_no is not None else -1
