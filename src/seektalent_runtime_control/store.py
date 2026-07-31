from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from seektalent.diagnostics_event_models import FailureEnvelopeV1
from seektalent.diagnostics_storage import (
    create_failure_envelope_schema,
    migrate_failure_envelope_schema_v13_to_v14,
)
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
from seektalent_runtime_control.browser_lane import (
    BrowserLaneStoreMixin,
    create_browser_lane_schema,
)
from seektalent_runtime_control.execution_failures import (
    ExecutionFailureStoreMixin,
    create_execution_failure_schema,
)
from seektalent_runtime_control.checkpoint_recovery import (
    RUNTIME_CHECKPOINT_CORRUPT,
    RUNTIME_CHECKPOINT_MISSING,
    RUNTIME_CHECKPOINT_RUN_MISMATCH,
    RUNTIME_CHECKPOINT_SCHEMA_UNSUPPORTED,
    RUNTIME_SOURCE_OPERATION_UNRESOLVED,
    RuntimeCheckpointLoadFailure,
    RuntimeCheckpointValidationContext,
    RuntimeRecoveryDecision,
    RuntimeRecoveryPlan,
    RuntimeRecoverySettlement,
    decide_expired_lease_recovery,
    validate_recoverable_checkpoint,
)
from seektalent_runtime_control.checkpoint_participant import write_checkpoint_participant
from seektalent_runtime_control.checkpoint_v2 import (
    CheckpointProjection,
    RUNTIME_CHECKPOINT_SCHEMA_V1,
    RUNTIME_CHECKPOINT_SCHEMA_V2,
    V2_SAFE_BOUNDARIES,
    candidate_truth_hash,
    compact_round_state,
    detail_claim_hash,
    legacy_checkpoint_projection,
)
from seektalent_runtime_control.clock import max_iso_timestamp, timestamp_lte
from seektalent_runtime_control.errors import RuntimeControlError, RuntimeControlLookupError
from seektalent_runtime_control.failed_outcome import (
    commit_failed_outcome as _commit_failed_outcome,
    migrate_failed_outcome_v13_to_v14,
    require_run_truth_mutable,
    validate_failed_outcome_row,
    validate_failed_outcome_schema,
)
from seektalent_runtime_control.fsm import require_run_transition
from seektalent_runtime_control import needs_attention as _needs_attention
from seektalent_runtime_control import needs_attention_admission as _needs_admission
from seektalent_runtime_control.needs_attention_store import NeedsAttentionStoreMixin
from seektalent_runtime_control.models import (
    RuntimeCheckpoint,
    RuntimeCheckpointCompactionResult,
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
from seektalent_runtime_control.recovery_attention import (
    create_recovery_attention_schema,
    enter_recovery_attention,
    resolve_recovery_attention,
)
from seektalent_runtime_control.run_acceptance import (
    RUN_ACCEPTANCE_JOINS,
    accepted_run_row,
    existing_run_for_start,
    insert_run,
    normalize_run_record,
    validate_initial_run_truth,
    validate_run_acceptance,
)
from seektalent_runtime_control.safe_retry_turnover import (
    _SafeRetryTurnoverAuthorityIssuer,
    issue_safe_retry_turnover_authority,
    latest_source_dispatch_row as _latest_source_dispatch_row,
    mint_safe_retry_dispatch_epoch,
    require_safe_retry_dispatch_authorization as _require_safe_retry_dispatch_authorization,
    source_dispatch_identity_exists as _source_dispatch_identity_exists,
)
from seektalent_runtime_control.source_epoch_schema import (
    SOURCE_OPERATION_ADMISSION_EXPECTATION_V10_SCHEMA_STATEMENTS as _SOURCE_OPERATION_ADMISSION_EXPECTATION_V10_SCHEMA_STATEMENTS,
    SOURCE_OPERATION_V8_SCHEMA_STATEMENTS as _SOURCE_OPERATION_V8_SCHEMA_STATEMENTS,
    create_source_operation_admission_expectation_schema as _create_source_operation_admission_expectation_schema,
    create_source_operation_schema as _create_source_operation_schema,
    migrate_source_epochs_v11_to_v12 as _migrate_v11_to_v12,
)
from seektalent_runtime_control.source_operations import (
    AcceptedSourceOperation,
    SOURCE_OPERATION_DISPOSITIONS,
    SourceDispatchMetadata,
    SourceOperationAdmissionExpectation,
    SourceOperationRecord,
    dispatch_ack_matches,
    dispatch_matches_acceptance,
    dispatch_matches_operation,
    expectation_matches_acceptance,
    expectation_matches_operation,
    operation_matches_acceptance,
    source_dispatch_from_row,
    source_operation_admission_expectation_from_row,
    source_operation_from_row,
    validate_source_dispatch_ack,
    validate_source_operation_admission_expectation,
    validate_source_operation_acceptance,
)
from seektalent_runtime_control.source_reconciliation import (
    SOURCE_RECONCILIATION_SCHEMA_STATEMENTS as _SOURCE_RECONCILIATION_V11_SCHEMA_STATEMENTS,
    SOURCE_RECONCILIATION_V10_SCHEMA_STATEMENTS as _SOURCE_RECONCILIATION_SCHEMA_STATEMENTS,
    SourceOperationReconciliationDecision,
    SourceOperationReconciliationRecord,
    migrate_source_reconciliation_v10_to_v11,
    reconciliation_dispatch_ack_requires_update,
    reconciliation_dispatch_precondition_matches,
    source_dispatch_is_currently_deliverable,
    source_reconciliation_from_row,
    source_reconciliation_matches_decision,
    validate_source_operation_reconciliation_decision,
)
from seektalent_runtime_control.stage_outputs import sanitize_stage_output_payload


RUNTIME_CONTROL_SCHEMA_VERSION = 20
RUNTIME_CHECKPOINT_SCHEMA_VERSION = RUNTIME_CHECKPOINT_SCHEMA_V2
RUNTIME_CONTROL_EVENT_SCHEMA_VERSION = "runtime-control-event/v1"
MAX_RUNTIME_CONTROL_JSON_BYTES = 16 * 1024
_SQLITE_INTEGER_MAX = 2**63 - 1
_RUNTIME_STAGE_OUTPUT_ARTIFACT_KIND = "runtime_stage_output"
_RUNTIME_STAGE_OUTPUT_ARTIFACT_DIR = "runtime_control_artifacts/stage_outputs"
_TERMINAL_RUN_STATUSES = ("cancelled", "completed", "failed")
_LEASE_ONLY_CLEANUP_RUN_STATUSES = (*_TERMINAL_RUN_STATUSES, "queued", "paused", "resume_requested")
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

class RuntimeControlStore(
    BrowserLaneStoreMixin,
    ExecutionFailureStoreMixin,
    NeedsAttentionStoreMixin,
):
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self._safe_retry_authority_issuer = _SafeRetryTurnoverAuthorityIssuer()

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
                validate_failed_outcome_schema(conn)
                _needs_attention.validate_needs_attention_schema(conn)
                create_browser_lane_schema(conn)
                create_execution_failure_schema(conn)
                create_recovery_attention_schema(conn)
                self.compact_pending_terminal_checkpoints()
                return
            if version > 0:
                backup_sqlite_before_migration(
                    self.path,
                    backup_root=self.path.parent / "migration_backups",
                    store_name="runtime-control",
                    now=_migration_now(),
                )
            if version in {1, 2, 3, 4, 5, 6}:
                run_ordered_migrations(
                    conn,
                    from_version=version,
                    to_version=7,
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
                run_sqlite_integrity_checks(conn, store_name="runtime-control", foreign_keys=False)
                conn.commit()
                version = 7
            if version in {
                7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19
            }:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if version == 7:
                        _migrate_v7_to_v8(conn)
                        conn.execute("PRAGMA user_version = 8")
                        version = 8
                    if version == 8:
                        _migrate_v8_to_v9(conn)
                        conn.execute("PRAGMA user_version = 9")
                        version = 9
                    if version == 9:
                        _migrate_v9_to_v10(conn)
                        conn.execute("PRAGMA user_version = 10")
                        version = 10
                    if version == 10:
                        migrate_source_reconciliation_v10_to_v11(conn)
                        conn.execute("PRAGMA user_version = 11")
                        version = 11
                    if version == 11:
                        _migrate_v11_to_v12(conn)
                        conn.execute("PRAGMA user_version = 12")
                        version = 12
                    if version == 12:
                        create_failure_envelope_schema(conn)
                        conn.execute("PRAGMA user_version = 13")
                        version = 13
                    if version == 13:
                        migrate_failure_envelope_schema_v13_to_v14(conn)
                        migrate_failed_outcome_v13_to_v14(conn)
                        conn.execute("PRAGMA user_version = 14")
                        version = 14
                    if version == 14:
                        validate_failed_outcome_schema(conn)
                        _needs_attention.migrate_needs_attention_v14_to_v15(conn)
                        conn.execute("PRAGMA user_version = 15")
                        version = 15
                    if version == 15:
                        _migrate_v15_to_v16(conn)
                        conn.execute("PRAGMA user_version = 16")
                        version = 16
                    if version == 16:
                        create_browser_lane_schema(conn)
                        conn.execute("PRAGMA user_version = 17")
                        version = 17
                    if version == 17:
                        create_browser_lane_schema(conn)
                        conn.execute("PRAGMA user_version = 18")
                        version = 18
                    if version == 18:
                        create_execution_failure_schema(conn)
                        conn.execute("PRAGMA user_version = 19")
                        version = 19
                    if version == 19:
                        create_recovery_attention_schema(conn)
                        conn.execute("PRAGMA user_version = 20")
                    run_sqlite_integrity_checks(conn, store_name="runtime-control", foreign_keys=False)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            else:
                _create_schema(conn)
                _create_source_operation_schema(conn)
                _create_source_reconciliation_schema(conn)
                _create_source_operation_admission_expectation_schema(conn)
                create_browser_lane_schema(conn)
                create_execution_failure_schema(conn)
                create_recovery_attention_schema(conn)
                conn.execute("BEGIN IMMEDIATE")
                with conn:
                    create_failure_envelope_schema(conn)
                    migrate_failed_outcome_v13_to_v14(conn)
                    _needs_attention.migrate_needs_attention_v14_to_v15(conn)
                    conn.execute(f"PRAGMA user_version = {RUNTIME_CONTROL_SCHEMA_VERSION}")
                    run_sqlite_integrity_checks(conn, store_name="runtime-control", foreign_keys=False)
        self.compact_pending_terminal_checkpoints()

    def create_run(self, run: RuntimeRunRecord) -> RuntimeRunRecord:
        stored = normalize_run_record(run)
        validate_initial_run_truth(stored)
        with self._connect() as conn, conn:
            existing = existing_run_for_start(conn, stored)
            if existing is not None:
                return _validated_run_from_row(conn, existing)
            try:
                insert_run(conn, stored)
            except sqlite3.IntegrityError:
                existing = existing_run_for_start(conn, stored)
                if existing is not None:
                    return _validated_run_from_row(conn, existing)
                raise
            inserted = _run_row(conn, stored.runtime_run_id)
            if inserted is None:
                raise RuntimeControlError("runtime_run_creation_incomplete")
            return _validated_run_from_row(conn, inserted)

    def accept_run(
        self,
        run: RuntimeRunRecord,
        *,
        initial_event: RuntimeControlEventInput,
        snapshot: RuntimeRunSnapshot,
    ) -> RuntimeRunRecord:
        """Commit a new run and its initial acceptance evidence atomically."""
        stored = normalize_run_record(run)
        validate_initial_run_truth(stored)
        validate_run_acceptance(stored, initial_event=initial_event, snapshot=snapshot)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = existing_run_for_start(conn, stored)
                if existing is not None:
                    accepted = accepted_run_row(conn, existing["runtime_run_id"])
                    if accepted is None:
                        raise RuntimeControlError("runtime_run_acceptance_incomplete")
                    conn.commit()
                    return _validated_run_from_row(conn, accepted)
                insert_run(conn, stored)
                _append_event_in_transaction(
                    conn,
                    initial_event,
                    snapshot=snapshot,
                    run_status="queued",
                    stop_reason_code=None,
                    completed_at=None,
                    latest_checkpoint_id=None,
                )
                accepted = accepted_run_row(conn, stored.runtime_run_id)
                if accepted is None:
                    raise RuntimeControlError("runtime_run_acceptance_incomplete")
                accepted_run = _validated_run_from_row(conn, accepted)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return accepted_run

    def get_run(self, runtime_run_id: str) -> RuntimeRunRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
            if row is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            return _validated_run_from_row(conn, row)

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
            return _validated_run_from_row(conn, row) if row is not None else None

    def get_run_by_run_intent_id(self, run_intent_id: str) -> RuntimeRunRecord | None:
        with self._connect() as conn:
            row = _run_row_by_run_intent(conn, run_intent_id)
            return _validated_run_from_row(conn, row) if row is not None else None

    def get_run_by_start_idempotency_key(self, start_idempotency_key: str) -> RuntimeRunRecord | None:
        with self._connect() as conn:
            row = _run_row_by_start_idempotency_key(conn, start_idempotency_key)
            return _validated_run_from_row(conn, row) if row is not None else None

    def accept_source_operation(
        self,
        *,
        runtime_run_id: str,
        operation_id: str,
        source_id: str,
        operation_kind: str,
        canonical_request_hash: str,
        idempotency_key: str,
        accepted_requirement_revision_id: str,
        runtime_attempt_no: int,
        runtime_attempt_authority_ref: str,
        runtime_attempt_fence_ref: str,
        profile_binding_generation: int,
        browser_control_scope_id: str | None,
        controller_fence_ref: str | None,
        outbox_id: str,
        dispatch_intent_id: str,
        dispatch_intent_revision: int,
        dispatch_intent_digest: str,
        dispatch_authorization_ordinal: int,
        source_operation_acceptance_ref: str,
        expected_ledger_revision: int,
        expected_reconciliation_revision: int,
        fault_injector: Callable[[str], None] | None = None,
    ) -> AcceptedSourceOperation:
        validate_source_operation_acceptance(
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            source_id=source_id,
            operation_kind=operation_kind,
            canonical_request_hash=canonical_request_hash,
            idempotency_key=idempotency_key,
            accepted_requirement_revision_id=accepted_requirement_revision_id,
            runtime_attempt_no=runtime_attempt_no,
            runtime_attempt_authority_ref=runtime_attempt_authority_ref,
            runtime_attempt_fence_ref=runtime_attempt_fence_ref,
            profile_binding_generation=profile_binding_generation,
            browser_control_scope_id=browser_control_scope_id,
            controller_fence_ref=controller_fence_ref,
            outbox_id=outbox_id,
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=dispatch_intent_revision,
            dispatch_intent_digest=dispatch_intent_digest,
            dispatch_authorization_ordinal=dispatch_authorization_ordinal,
            source_operation_acceptance_ref=source_operation_acceptance_ref,
            expected_ledger_revision=expected_ledger_revision,
            expected_reconciliation_revision=expected_reconciliation_revision,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                run_row = _run_row(conn, runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                operation_by_id = _source_operation_row(conn, runtime_run_id, operation_id)
                operation_by_key = _source_operation_row_by_idempotency(conn, runtime_run_id, idempotency_key)
                operation = None
                expectation = None
                dispatch = None
                if operation_by_id is not None:
                    operation, expectation, dispatch = _source_operation_acceptance(conn, operation_by_id)
                if operation_by_key is not None and (
                    operation_by_id is None or operation_by_key["operation_id"] != operation_by_id["operation_id"]
                ):
                    _source_operation_acceptance(conn, operation_by_key)
                if operation is not None and expectation is not None and dispatch is not None:
                    if operation.idempotency_key != idempotency_key:
                        raise RuntimeControlError("identity_conflict")
                    if operation.canonical_request_hash != canonical_request_hash:
                        raise RuntimeControlError("idempotency_conflict")
                    if operation_by_key is None or operation_by_key["operation_id"] != operation_id:
                        raise RuntimeControlError("source_operation_acceptance_incomplete")
                    if not operation_matches_acceptance(
                        operation,
                        operation_id=operation_id,
                        source_id=source_id,
                        operation_kind=operation_kind,
                        canonical_request_hash=canonical_request_hash,
                        idempotency_key=idempotency_key,
                        accepted_requirement_revision_id=accepted_requirement_revision_id,
                        runtime_attempt_no=runtime_attempt_no,
                        runtime_attempt_authority_ref=runtime_attempt_authority_ref,
                    ):
                        raise RuntimeControlError("identity_conflict")
                    if not expectation_matches_acceptance(
                        expectation,
                        dispatch_authorization_ordinal=dispatch_authorization_ordinal,
                        runtime_attempt_no=runtime_attempt_no,
                        runtime_attempt_authority_ref=runtime_attempt_authority_ref,
                        runtime_attempt_fence_ref=runtime_attempt_fence_ref,
                        profile_binding_generation=profile_binding_generation,
                        browser_control_scope_id=browser_control_scope_id,
                        controller_fence_ref=controller_fence_ref,
                    ):
                        raise RuntimeControlError("identity_conflict")
                    if not dispatch_matches_acceptance(
                        dispatch,
                        outbox_id=outbox_id,
                        canonical_request_hash=canonical_request_hash,
                        dispatch_intent_id=dispatch_intent_id,
                        dispatch_intent_revision=dispatch_intent_revision,
                        dispatch_intent_digest=dispatch_intent_digest,
                        dispatch_authorization_ordinal=dispatch_authorization_ordinal,
                        safe_retry_commit_ref=None,
                        source_operation_acceptance_ref=source_operation_acceptance_ref,
                        expected_ledger_revision=expected_ledger_revision,
                        expected_reconciliation_revision=expected_reconciliation_revision,
                    ):
                        raise RuntimeControlError("identity_conflict")
                    conn.commit()
                    _inject_source_operation_fault(fault_injector, "after_commit")
                    return AcceptedSourceOperation(
                        operation=operation,
                        expectation=expectation,
                        dispatch=dispatch,
                    )
                if operation_by_key is not None:
                    raise RuntimeControlError("idempotency_conflict")
                if _source_dispatch_row_for_operation(conn, runtime_run_id, operation_id) is not None:
                    raise RuntimeControlError("source_operation_acceptance_incomplete")
                if _source_operation_admission_expectation_row(conn, runtime_run_id, operation_id) is not None:
                    raise RuntimeControlError("source_operation_acceptance_incomplete")
                needs_attention_evidence = (
                    run_row["status"] == "needs_attention"
                    and _needs_admission.needs_attention_evidence_acceptance_matches(
                        conn,
                        run_row,
                        operation_id,
                        source_id,
                        operation_kind,
                        accepted_requirement_revision_id,
                        runtime_attempt_no,
                        profile_binding_generation,
                        browser_control_scope_id,
                        dispatch_authorization_ordinal,
                        expected_ledger_revision,
                        expected_reconciliation_revision,
                    )
                )
                if (
                    run_row["status"] not in {"starting", "running"}
                    and not needs_attention_evidence
                ):
                    raise RuntimeControlError("source_operation_run_not_dispatchable")
                if run_row["approved_requirement_revision_id"] != accepted_requirement_revision_id:
                    raise RuntimeControlError("source_operation_requirement_revision_mismatch")
                if _source_dispatch_identity_exists(conn, outbox_id, dispatch_intent_id):
                    raise RuntimeControlError("identity_conflict")

                conn.execute(
                    """
                    INSERT INTO runtime_control_source_operations (
                        runtime_run_id, operation_id, source_id, operation_kind,
                        canonical_request_hash, idempotency_key,
                        accepted_requirement_revision_id, runtime_attempt_no,
                        runtime_attempt_authority_ref, operation_phase, dispatch_intent_ref,
                        conclusive_observation_ref, source_operation_disposition, retry_posture,
                        reconciliation_revision, main_commit_ref, ledger_revision
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', NULL, NULL, NULL,
                            'no_retry', 0, NULL, 1)
                    """,
                    (
                        runtime_run_id,
                        operation_id,
                        source_id,
                        operation_kind,
                        canonical_request_hash,
                        idempotency_key,
                        accepted_requirement_revision_id,
                        runtime_attempt_no,
                        runtime_attempt_authority_ref,
                    ),
                )
                _inject_source_operation_fault(fault_injector, "after_operation_insert")
                conn.execute(
                    """
                    INSERT INTO runtime_control_source_operation_admission_expectations (
                        runtime_run_id, operation_id, dispatch_authorization_ordinal,
                        runtime_attempt_no, runtime_attempt_authority_ref,
                        runtime_attempt_fence_ref, profile_binding_generation,
                        browser_control_scope_id, controller_fence_ref
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        runtime_run_id,
                        operation_id,
                        dispatch_authorization_ordinal,
                        runtime_attempt_no,
                        runtime_attempt_authority_ref,
                        runtime_attempt_fence_ref,
                        profile_binding_generation,
                        browser_control_scope_id,
                        controller_fence_ref,
                    ),
                )
                _inject_source_operation_fault(fault_injector, "after_expectation_insert")
                conn.execute(
                    """
                    INSERT INTO runtime_control_source_dispatch_outbox (
                        outbox_id, runtime_run_id, operation_id, canonical_request_hash,
                        dispatch_intent_id, dispatch_intent_revision, dispatch_intent_digest,
                        dispatch_authorization_ordinal, safe_retry_commit_ref,
                        source_operation_acceptance_ref,
                        expected_ledger_revision, expected_reconciliation_revision,
                        status, outbox_revision, accepted_sidecar_generation,
                        accepted_sidecar_journal_revision, ack_ref, ack_kind, acknowledged_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 'pending', 1,
                            NULL, NULL, NULL, NULL, NULL)
                    """,
                    (
                        outbox_id,
                        runtime_run_id,
                        operation_id,
                        canonical_request_hash,
                        dispatch_intent_id,
                        dispatch_intent_revision,
                        dispatch_intent_digest,
                        dispatch_authorization_ordinal,
                        source_operation_acceptance_ref,
                        expected_ledger_revision,
                        expected_reconciliation_revision,
                    ),
                )
                _inject_source_operation_fault(fault_injector, "after_outbox_insert")
                operation_row = _source_operation_row(conn, runtime_run_id, operation_id)
                if operation_row is None:
                    raise RuntimeControlError("source_operation_acceptance_incomplete")
                operation, expectation, dispatch = _source_operation_acceptance(conn, operation_row)
                conn.commit()
                _inject_source_operation_fault(fault_injector, "after_commit")
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return AcceptedSourceOperation(
            operation=operation,
            expectation=expectation,
            dispatch=dispatch,
        )

    def get_source_operation(self, runtime_run_id: str, operation_id: str) -> SourceOperationRecord:
        with self._connect() as conn:
            row = _source_operation_row(conn, runtime_run_id, operation_id)
        if row is None:
            raise RuntimeControlLookupError("source_operation_not_found")
        return source_operation_from_row(row)

    def has_unresolved_source_operations(
        self,
        runtime_run_id: str,
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runtime_control_source_operations
                WHERE runtime_run_id = ?
                  AND (
                    operation_phase != 'main_committed'
                    OR main_commit_ref IS NULL
                  )
                LIMIT 1
                """,
                (runtime_run_id,),
            ).fetchone()
        return row is not None

    def get_source_operation_admission_expectation(
        self,
        runtime_run_id: str,
        operation_id: str,
        dispatch_authorization_ordinal: int = 1,
    ) -> SourceOperationAdmissionExpectation:
        with self._connect() as conn:
            if (operation_row := _source_operation_row(conn, runtime_run_id, operation_id)) is None:
                raise RuntimeControlLookupError("source_operation_not_found")
            expectation_row = _source_operation_admission_expectation_row(
                conn,
                runtime_run_id,
                operation_id,
                dispatch_authorization_ordinal,
            )
            if expectation_row is None:
                raise RuntimeControlError("source_operation_acceptance_incomplete")
            expectation = _source_operation_admission_expectation_from_row(expectation_row)
            operation = source_operation_from_row(operation_row)
            if not expectation_matches_operation(expectation, operation):
                raise RuntimeControlError("source_operation_acceptance_incomplete")
        return expectation

    def get_accepted_source_operation_context(self, runtime_run_id: str, operation_id: str) -> AcceptedSourceOperation:
        with self._connect() as conn:
            conn.execute("BEGIN")
            operation_row = _source_operation_row(conn, runtime_run_id, operation_id)
            if operation_row is None:
                raise RuntimeControlLookupError("source_operation_not_found")
            return _source_operation_acceptance(conn, operation_row)

    def record_owned_source_operation_observation(
        self,
        *,
        runtime_run_id: str,
        operation_id: str,
        executor_id: str,
        attempt_no: int,
        expected_ledger_revision: int,
        dispatch_intent_ref: str,
        conclusive_observation_ref: str,
        source_operation_disposition: str,
        observed_at: str,
    ) -> SourceOperationRecord:
        """Commit one authenticated sidecar observation under the active executor fence."""
        if source_operation_disposition not in SOURCE_OPERATION_DISPOSITIONS:
            raise RuntimeControlError("source_operation_disposition_invalid")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _require_active_executor(
                    conn,
                    runtime_run_id,
                    executor_id,
                    attempt_no=attempt_no,
                    observed_at=observed_at,
                )
                row = _source_operation_row(conn, runtime_run_id, operation_id)
                if row is None:
                    raise RuntimeControlLookupError("source_operation_not_found")
                operation = source_operation_from_row(row)
                if (
                    operation.operation_phase == "observed"
                    and operation.dispatch_intent_ref == dispatch_intent_ref
                    and operation.conclusive_observation_ref == conclusive_observation_ref
                    and operation.source_operation_disposition
                    == source_operation_disposition
                ):
                    return operation
                if (
                    operation.operation_phase not in {"accepted", "reconciled"}
                    or operation.ledger_revision != expected_ledger_revision
                    or operation.main_commit_ref is not None
                    or operation.conclusive_observation_ref is not None
                ):
                    raise RuntimeControlError("source_operation_observation_conflict")
                dispatch_row = _source_dispatch_row_for_operation(
                    conn,
                    runtime_run_id,
                    operation_id,
                )
                if dispatch_row is None:
                    raise RuntimeControlError("source_operation_acceptance_incomplete")
                dispatch = source_dispatch_from_row(dispatch_row)
                if dispatch.status != "acknowledged":
                    raise RuntimeControlError("source_operation_dispatch_ack_missing")
                updated = conn.execute(
                    """
                    UPDATE runtime_control_source_operations
                    SET operation_phase = 'observed',
                        dispatch_intent_ref = ?,
                        conclusive_observation_ref = ?,
                        source_operation_disposition = ?,
                        retry_posture = 'no_retry',
                        ledger_revision = ledger_revision + 1
                    WHERE runtime_run_id = ? AND operation_id = ?
                      AND operation_phase = ?
                      AND ledger_revision = ?
                      AND main_commit_ref IS NULL
                      AND conclusive_observation_ref IS NULL
                    """,
                    (
                        dispatch_intent_ref,
                        conclusive_observation_ref,
                        source_operation_disposition,
                        runtime_run_id,
                        operation_id,
                        operation.operation_phase,
                        expected_ledger_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeControlError("source_operation_observation_conflict")
                committed_row = _source_operation_row(conn, runtime_run_id, operation_id)
                if committed_row is None:
                    raise RuntimeControlError("source_operation_observation_incomplete")
                committed = source_operation_from_row(committed_row)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return committed

    def record_owned_source_reconciliation_unknown(
        self,
        *,
        runtime_run_id: str,
        operation_id: str,
        executor_id: str,
        attempt_no: int,
        expected_ledger_revision: int,
        expected_reconciliation_revision: int,
        history_result_ref: str,
        history_result_digest: str,
        history_outcome: str,
        history_conclusion: str | None,
        dispatch_intent_ref: str | None,
        committed_at: str,
    ) -> SourceOperationRecord:
        """Persist an authenticated inconclusive history result under the live owner."""
        if history_outcome not in {
            "matched",
            "not_found",
            "history_unavailable",
        }:
            raise RuntimeControlError("source_reconciliation_history_outcome_invalid")
        if history_conclusion not in {
            None,
            "accepted_no_dispatch",
            "dispatch_not_observed",
        }:
            raise RuntimeControlError("source_reconciliation_history_conclusion_invalid")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _require_active_executor(
                    conn,
                    runtime_run_id,
                    executor_id,
                    attempt_no=attempt_no,
                    observed_at=committed_at,
                )
                row = _source_operation_row(conn, runtime_run_id, operation_id)
                if row is None:
                    raise RuntimeControlLookupError("source_operation_not_found")
                operation = source_operation_from_row(row)
                reconciliation_id = f"source-history-{history_result_digest}"
                existing = _source_reconciliation_row(conn, reconciliation_id)
                if existing is not None:
                    return source_operation_from_row(row)
                if (
                    operation.operation_phase != "accepted"
                    or operation.ledger_revision != expected_ledger_revision
                    or operation.reconciliation_revision
                    != expected_reconciliation_revision
                    or operation.main_commit_ref is not None
                ):
                    raise RuntimeControlError("source_reconciliation_revision_conflict")
                committed_ledger_revision = operation.ledger_revision + 1
                committed_reconciliation_revision = (
                    operation.reconciliation_revision + 1
                )
                updated = conn.execute(
                    """
                    UPDATE runtime_control_source_operations
                    SET operation_phase = 'reconciled',
                        dispatch_intent_ref = ?,
                        source_operation_disposition = 'reconciliation_unknown',
                        retry_posture = 'reconcile_first',
                        reconciliation_revision = ?,
                        ledger_revision = ?
                    WHERE runtime_run_id = ? AND operation_id = ?
                      AND operation_phase = 'accepted'
                      AND ledger_revision = ?
                      AND reconciliation_revision = ?
                      AND main_commit_ref IS NULL
                    """,
                    (
                        dispatch_intent_ref,
                        committed_reconciliation_revision,
                        committed_ledger_revision,
                        runtime_run_id,
                        operation_id,
                        expected_ledger_revision,
                        expected_reconciliation_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeControlError("source_reconciliation_revision_conflict")
                conn.execute(
                    """
                    INSERT INTO runtime_control_source_reconciliations (
                        reconciliation_id, runtime_run_id, operation_id,
                        source_id, operation_kind, canonical_request_hash,
                        idempotency_key, accepted_requirement_revision_id,
                        runtime_attempt_no, runtime_attempt_authority_ref,
                        history_result_ref, history_result_digest,
                        decision_kind, history_outcome, history_conclusion,
                        dispatch_intent_ref, conclusive_observation_ref,
                        source_operation_disposition, retry_posture,
                        expected_ledger_revision,
                        expected_reconciliation_revision, committed_at,
                        committed_operation_phase, committed_ledger_revision,
                        committed_reconciliation_revision
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'unresolved', ?, ?, ?, NULL,
                            'reconciliation_unknown', 'reconcile_first',
                            ?, ?, ?, 'reconciled', ?, ?)
                    """,
                    (
                        reconciliation_id,
                        operation.runtime_run_id,
                        operation.operation_id,
                        operation.source_id,
                        operation.operation_kind,
                        operation.canonical_request_hash,
                        operation.idempotency_key,
                        operation.accepted_requirement_revision_id,
                        operation.runtime_attempt_no,
                        operation.runtime_attempt_authority_ref,
                        history_result_ref,
                        history_result_digest,
                        history_outcome,
                        history_conclusion,
                        dispatch_intent_ref,
                        expected_ledger_revision,
                        expected_reconciliation_revision,
                        committed_at,
                        committed_ledger_revision,
                        committed_reconciliation_revision,
                    ),
                )
                committed_row = _source_operation_row(
                    conn,
                    runtime_run_id,
                    operation_id,
                )
                if committed_row is None:
                    raise RuntimeControlError(
                        "source_reconciliation_commit_incomplete"
                    )
                committed = source_operation_from_row(committed_row)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return committed

    def commit_no_owner_source_reconciliation(
        self,
        decision: SourceOperationReconciliationDecision,
        fault_injector: Callable[[str], None] | None = None,
        *,
        dispatch_precondition: SourceDispatchMetadata | None = None,
        dispatch_ack: SourceDispatchMetadata | None = None,
        expired_browser_lane_fencing_token: int | None = None,
    ) -> SourceOperationReconciliationRecord:
        """Commit a closed main-authored reconciliation when no executor owns the run."""
        validate_source_operation_reconciliation_decision(decision)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing_row = _source_reconciliation_row(conn, decision.reconciliation_id)
                if existing_row is not None:
                    existing = source_reconciliation_from_row(existing_row)
                    if not source_reconciliation_matches_decision(existing, decision):
                        raise RuntimeControlError("source_reconciliation_idempotency_conflict")
                    conn.commit()
                    _inject_source_reconciliation_fault(fault_injector, "after_commit")
                    return existing

                run_row = _run_row(conn, decision.runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                if (
                    run_row["status"] != "resume_requested"
                    and not _needs_admission.needs_attention_evidence_reconciliation_matches(
                        conn, run_row=run_row, decision=decision
                    )
                    and not _expired_browser_lane_reconciliation_matches(
                        conn,
                        run_row=run_row,
                        decision=decision,
                        fencing_token=(
                            expired_browser_lane_fencing_token
                        ),
                    )
                ):
                    raise RuntimeControlError("source_reconciliation_run_not_resumable")
                if _needs_admission.run_has_active_executor_lease(conn, decision.runtime_run_id):
                    raise RuntimeControlError("source_reconciliation_owner_conflict")

                operation_row = _source_operation_row(conn, decision.runtime_run_id, decision.operation_id)
                if operation_row is None:
                    raise RuntimeControlLookupError("source_operation_not_found")
                operation, dispatch = _source_operation_pair(conn, operation_row)
                if not _source_operation_matches_reconciliation(operation, decision):
                    raise RuntimeControlError("source_reconciliation_identity_conflict")
                if not reconciliation_dispatch_precondition_matches(dispatch, decision, dispatch_precondition):
                    raise RuntimeControlError("source_reconciliation_dispatch_conflict")
                update_dispatch_ack = reconciliation_dispatch_ack_requires_update(
                    dispatch,
                    dispatch_ack,
                    decision,
                )
                if operation.operation_phase == "main_committed" or operation.main_commit_ref is not None:
                    raise RuntimeControlError("source_reconciliation_main_commit_conflict")
                if (
                    operation.ledger_revision != decision.expected_ledger_revision
                    or operation.reconciliation_revision != decision.expected_reconciliation_revision
                ):
                    raise RuntimeControlError("source_reconciliation_revision_conflict")
                if (
                    operation.ledger_revision == _SQLITE_INTEGER_MAX
                    or operation.reconciliation_revision == _SQLITE_INTEGER_MAX
                ):
                    raise RuntimeControlError("source_reconciliation_revision_overflow")
                _require_source_reconciliation_transition(operation, decision)

                committed_ledger_revision = operation.ledger_revision + 1
                committed_reconciliation_revision = operation.reconciliation_revision + 1
                if update_dispatch_ack and dispatch_ack is not None:
                    _inject_source_reconciliation_fault(fault_injector, "before_outbox_update")
                    acknowledged = conn.execute(
                        """
                        UPDATE runtime_control_source_dispatch_outbox
                        SET status = 'acknowledged', outbox_revision = ?,
                            accepted_sidecar_generation = ?,
                            accepted_sidecar_journal_revision = ?,
                            ack_ref = ?, ack_kind = ?, acknowledged_at = ?
                        WHERE outbox_id = ? AND status = 'pending' AND outbox_revision = ?
                          AND accepted_sidecar_generation IS NULL
                          AND accepted_sidecar_journal_revision IS NULL
                          AND ack_ref IS NULL AND ack_kind IS NULL AND acknowledged_at IS NULL
                        """,
                        (
                            dispatch_ack.outbox_revision,
                            dispatch_ack.accepted_sidecar_generation,
                            dispatch_ack.accepted_sidecar_journal_revision,
                            dispatch_ack.ack_ref,
                            dispatch_ack.ack_kind,
                            dispatch_ack.acknowledged_at,
                            dispatch.outbox_id,
                            dispatch.outbox_revision,
                        ),
                    )
                    if acknowledged.rowcount != 1:
                        raise RuntimeControlError("source_reconciliation_dispatch_conflict")
                    _inject_source_reconciliation_fault(fault_injector, "after_outbox_update")
                _inject_source_reconciliation_fault(fault_injector, "before_operation_update")
                updated = conn.execute(
                    """
                    UPDATE runtime_control_source_operations
                    SET operation_phase = 'reconciled',
                        dispatch_intent_ref = ?,
                        conclusive_observation_ref = ?,
                        source_operation_disposition = ?,
                        retry_posture = ?,
                        reconciliation_revision = ?,
                        ledger_revision = ?
                    WHERE runtime_run_id = ? AND operation_id = ?
                      AND operation_phase != 'main_committed' AND main_commit_ref IS NULL
                      AND ledger_revision = ? AND reconciliation_revision = ?
                    """,
                    (
                        decision.dispatch_intent_ref,
                        decision.conclusive_observation_ref,
                        decision.source_operation_disposition,
                        decision.retry_posture,
                        committed_reconciliation_revision,
                        committed_ledger_revision,
                        decision.runtime_run_id,
                        decision.operation_id,
                        decision.expected_ledger_revision,
                        decision.expected_reconciliation_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeControlError("source_reconciliation_revision_conflict")
                _inject_source_reconciliation_fault(fault_injector, "after_operation_update")

                _inject_source_reconciliation_fault(fault_injector, "before_reconciliation_insert")
                conn.execute(
                    """
                    INSERT INTO runtime_control_source_reconciliations (
                        reconciliation_id, runtime_run_id, operation_id, source_id,
                        operation_kind, canonical_request_hash, idempotency_key,
                        accepted_requirement_revision_id, runtime_attempt_no,
                        runtime_attempt_authority_ref, history_result_ref,
                        history_result_digest, decision_kind, history_outcome,
                        history_conclusion, dispatch_intent_ref,
                        conclusive_observation_ref, source_operation_disposition,
                        retry_posture, expected_ledger_revision,
                        expected_reconciliation_revision, committed_at,
                        committed_operation_phase, committed_ledger_revision,
                        committed_reconciliation_revision
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'reconciled', ?, ?)
                    """,
                    (
                        decision.reconciliation_id,
                        decision.runtime_run_id,
                        decision.operation_id,
                        decision.source_id,
                        decision.operation_kind,
                        decision.canonical_request_hash,
                        decision.idempotency_key,
                        decision.accepted_requirement_revision_id,
                        decision.runtime_attempt_no,
                        decision.runtime_attempt_authority_ref,
                        decision.history_result_ref,
                        decision.history_result_digest,
                        decision.decision_kind,
                        decision.history_outcome,
                        decision.history_conclusion,
                        decision.dispatch_intent_ref,
                        decision.conclusive_observation_ref,
                        decision.source_operation_disposition,
                        decision.retry_posture,
                        decision.expected_ledger_revision,
                        decision.expected_reconciliation_revision,
                        decision.committed_at,
                        committed_ledger_revision,
                        committed_reconciliation_revision,
                    ),
                )
                _inject_source_reconciliation_fault(fault_injector, "after_reconciliation_insert")
                committed_row = _source_reconciliation_row(conn, decision.reconciliation_id)
                if committed_row is None:
                    raise RuntimeControlError("source_reconciliation_commit_incomplete")
                committed = source_reconciliation_from_row(committed_row)
                _inject_source_reconciliation_fault(fault_injector, "before_commit")
                conn.commit()
                _inject_source_reconciliation_fault(fault_injector, "after_commit")
            except Exception:
                conn.rollback()
                raise
        return committed

    def _mint_safe_retry_turnover_authority_for_test(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
        observed_at: str,
        runtime_attempt_authority_ref: str,
        runtime_attempt_fence_ref: str,
        profile_binding_generation: int,
        browser_control_scope_id: str,
        controller_fence_ref: str | None,
    ) -> object:
        """Issue a sealed test-only capability until a product authority source exists."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            return issue_safe_retry_turnover_authority(
                conn,
                self._safe_retry_authority_issuer,
                runtime_run_id=runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                observed_at=observed_at,
                runtime_attempt_authority_ref=runtime_attempt_authority_ref,
                runtime_attempt_fence_ref=runtime_attempt_fence_ref,
                profile_binding_generation=profile_binding_generation,
                browser_control_scope_id=browser_control_scope_id,
                controller_fence_ref=controller_fence_ref,
            )

    def mint_safe_retry_dispatch_epoch(
        self,
        *,
        runtime_run_id: str,
        operation_id: str,
        reconciliation_id: str,
        expected_reconciliation_ledger_revision: int,
        expected_reconciliation_revision: int,
        outbox_id: str,
        dispatch_intent_id: str,
        authority: object,
        fault_injector: Callable[[str], None] | None = None,
    ) -> AcceptedSourceOperation:
        with self._connect() as conn:
            return mint_safe_retry_dispatch_epoch(
                conn,
                self._safe_retry_authority_issuer,
                runtime_run_id=runtime_run_id,
                operation_id=operation_id,
                reconciliation_id=reconciliation_id,
                expected_reconciliation_ledger_revision=(expected_reconciliation_ledger_revision),
                expected_reconciliation_revision=(expected_reconciliation_revision),
                outbox_id=outbox_id,
                dispatch_intent_id=dispatch_intent_id,
                authority=authority,
                fault_injector=fault_injector,
            )

    def list_pending_source_dispatches(self, limit: int = 100) -> list[SourceDispatchMetadata]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("source_dispatch_limit_invalid")
        with self._connect() as conn:
            conn.execute("BEGIN")
            dispatches = []
            offset = 0
            while len(dispatches) < limit:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM runtime_control_source_dispatch_outbox
                    WHERE status = 'pending'
                    ORDER BY outbox_id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
                if not rows:
                    break
                offset += len(rows)
                for row in rows:
                    dispatch = source_dispatch_from_row(row)
                    operation = _require_source_dispatch_operation(conn, dispatch)
                    latest_row = _latest_source_dispatch_row(
                        conn,
                        dispatch.runtime_run_id,
                        dispatch.operation_id,
                    )
                    if latest_row is None:
                        raise RuntimeControlError("source_operation_acceptance_incomplete")
                    if source_dispatch_is_currently_deliverable(
                        dispatch,
                        operation,
                        latest_dispatch_authorization_ordinal=int(latest_row["dispatch_authorization_ordinal"]),
                    ):
                        dispatches.append(dispatch)
                        if len(dispatches) == limit:
                            break
        return dispatches

    def record_source_dispatch_ack(
        self,
        *,
        runtime_run_id: str,
        operation_id: str,
        outbox_id: str,
        canonical_request_hash: str,
        dispatch_intent_id: str,
        dispatch_intent_revision: int,
        dispatch_intent_digest: str,
        dispatch_authorization_ordinal: int,
        expected_outbox_revision: int,
        accepted_sidecar_generation: int,
        accepted_sidecar_journal_revision: int,
        ack_ref: str,
        ack_kind: str,
        acknowledged_at: str,
    ) -> SourceDispatchMetadata:
        validate_source_dispatch_ack(
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            outbox_id=outbox_id,
            canonical_request_hash=canonical_request_hash,
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=dispatch_intent_revision,
            dispatch_intent_digest=dispatch_intent_digest,
            dispatch_authorization_ordinal=dispatch_authorization_ordinal,
            expected_outbox_revision=expected_outbox_revision,
            accepted_sidecar_generation=accepted_sidecar_generation,
            accepted_sidecar_journal_revision=accepted_sidecar_journal_revision,
            ack_ref=ack_ref,
            ack_kind=ack_kind,
            acknowledged_at=acknowledged_at,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM runtime_control_source_dispatch_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeControlLookupError("source_dispatch_not_found")
                dispatch = source_dispatch_from_row(row)
                _require_source_dispatch_operation(conn, dispatch)
                if (
                    dispatch.runtime_run_id != runtime_run_id
                    or dispatch.operation_id != operation_id
                    or dispatch.canonical_request_hash != canonical_request_hash
                    or dispatch.dispatch_intent_id != dispatch_intent_id
                    or dispatch.dispatch_intent_revision != dispatch_intent_revision
                    or dispatch.dispatch_intent_digest != dispatch_intent_digest
                    or dispatch.dispatch_authorization_ordinal != dispatch_authorization_ordinal
                ):
                    raise RuntimeControlError("source_dispatch_identity_conflict")
                if dispatch.status == "acknowledged":
                    if expected_outbox_revision != 1:
                        raise RuntimeControlError("source_dispatch_outbox_revision_conflict")
                    if dispatch_ack_matches(
                        dispatch,
                        accepted_sidecar_generation=accepted_sidecar_generation,
                        accepted_sidecar_journal_revision=accepted_sidecar_journal_revision,
                        ack_ref=ack_ref,
                        ack_kind=ack_kind,
                        acknowledged_at=acknowledged_at,
                    ):
                        conn.commit()
                        return dispatch
                    raise RuntimeControlError("source_dispatch_ack_conflict")
                if dispatch.outbox_revision != expected_outbox_revision:
                    raise RuntimeControlError("source_dispatch_outbox_revision_conflict")
                if dispatch.status != "pending":
                    raise RuntimeControlError("source_dispatch_ack_conflict")
                updated = conn.execute(
                    """
                    UPDATE runtime_control_source_dispatch_outbox
                    SET status = 'acknowledged', outbox_revision = outbox_revision + 1,
                        accepted_sidecar_generation = ?, accepted_sidecar_journal_revision = ?,
                        ack_ref = ?, ack_kind = ?, acknowledged_at = ?
                    WHERE outbox_id = ? AND status = 'pending' AND outbox_revision = ?
                    """,
                    (
                        accepted_sidecar_generation,
                        accepted_sidecar_journal_revision,
                        ack_ref,
                        ack_kind,
                        acknowledged_at,
                        outbox_id,
                        expected_outbox_revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeControlError("source_dispatch_outbox_revision_conflict")
                updated_row = conn.execute(
                    "SELECT * FROM runtime_control_source_dispatch_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                if updated_row is None:
                    raise RuntimeControlError("source_operation_acceptance_incomplete")
                dispatch = source_dispatch_from_row(updated_row)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return dispatch

    def link_workbench_session(
        self,
        *,
        runtime_run_id: str,
        workbench_session_id: str,
        updated_at: str,
    ) -> RuntimeRunRecord:
        with self._connect() as conn, conn:
            existing = _run_row(conn, runtime_run_id)
            if existing is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            require_run_truth_mutable(existing)
            updated = conn.execute(
                """
                UPDATE runtime_control_runs
                SET workbench_session_id = ?, updated_at = ?,
                    state_revision = state_revision + 1
                WHERE runtime_run_id = ?
                  AND product_outcome IS NULL
                  AND current_failure_id IS NULL
                  AND current_failure_revision IS NULL
                  AND current_failure_owner_lease_id IS NULL
                  AND current_failure_authority_mode IS NULL
                """,
                (workbench_session_id, updated_at, runtime_run_id),
            )
            if updated.rowcount != 1:
                raise RuntimeControlError(
                    "runtime_failed_outcome_terminal_immutable"
                )
            row = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
            return _validated_run_from_row(conn, row)

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
            require_run_truth_mutable(row)
            require_run_transition(row["status"], status)
            changed = conn.execute(
                """
                UPDATE runtime_control_runs
                SET status = ?, current_stage = ?, current_round = ?, updated_at = ?,
                    stop_reason_code = COALESCE(?, stop_reason_code),
                    completed_at = COALESCE(?, completed_at),
                    latest_checkpoint_id = COALESCE(?, latest_checkpoint_id),
                    state_revision = state_revision + 1
                WHERE runtime_run_id = ?
                  AND product_outcome IS NULL
                  AND current_failure_id IS NULL
                  AND current_failure_revision IS NULL
                  AND current_failure_owner_lease_id IS NULL
                  AND current_failure_authority_mode IS NULL
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
            if changed.rowcount != 1:
                raise RuntimeControlError(
                    "runtime_failed_outcome_terminal_immutable"
                )
            updated = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
            return _validated_run_from_row(conn, updated)

    def commit_failed_outcome(
        self,
        *,
        runtime_run_id: str,
        envelope: FailureEnvelopeV1 | bytes,
        terminal_reason_code: str,
        terminal_at: str,
        expected_state_revision: int,
        executor_id: str | None = None,
        attempt_no: int | None = None,
        operation_id: str | None = None,
        statement_hook: Callable[[int, str], None] | None = None,
    ) -> RuntimeRunRecord:
        """Atomically commit one canonical failed outcome and revoke its owner."""

        with self._connect() as conn:
            row = _commit_failed_outcome(
                conn,
                runtime_run_id=runtime_run_id,
                envelope=envelope,
                terminal_reason_code=terminal_reason_code,
                terminal_at=terminal_at,
                expected_state_revision=expected_state_revision,
                executor_id=executor_id,
                attempt_no=attempt_no,
                operation_id=operation_id,
                statement_hook=statement_hook,
            )
            return _validated_run_from_row(conn, row)

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
                run_row = _run_row(conn, runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                require_run_truth_mutable(run_row)
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
                advanced = conn.execute(
                    """
                    UPDATE runtime_control_runs
                    SET state_revision = state_revision + 1
                    WHERE runtime_run_id = ?
                      AND product_outcome IS NULL
                      AND current_failure_id IS NULL
                      AND current_failure_revision IS NULL
                      AND current_failure_owner_lease_id IS NULL
                      AND current_failure_authority_mode IS NULL
                    """,
                    (runtime_run_id,),
                )
                if advanced.rowcount != 1:
                    raise RuntimeControlError(
                        "runtime_failed_outcome_terminal_immutable"
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
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                lease_row = _require_active_executor(
                    conn,
                    runtime_run_id,
                    executor_id,
                    attempt_no=attempt_no,
                )
                if timestamp_lte(lease_row["lease_expires_at"], heartbeat_at):
                    raise RuntimeControlError("runtime_executor_lease_expired")
                stored_heartbeat_at = max_iso_timestamp(
                    heartbeat_at,
                    lease_row["heartbeat_at"],
                    lease_row["acquired_at"],
                )
                stored_lease_expires_at = max_iso_timestamp(
                    lease_expires_at,
                    lease_row["lease_expires_at"],
                )
                conn.execute(
                    """
                    UPDATE runtime_control_executor_leases
                    SET heartbeat_at = ?, lease_expires_at = ?
                    WHERE lease_id = ? AND status = 'active'
                    """,
                    (stored_heartbeat_at, stored_lease_expires_at, lease_row["lease_id"]),
                )
                run_row = _run_row(conn, runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                require_run_truth_mutable(run_row)
                conn.execute(
                    """
                    UPDATE runtime_control_runs
                    SET state_revision = state_revision + 1
                    WHERE runtime_run_id = ?
                    """,
                    (runtime_run_id,),
                )
                updated = conn.execute(
                    "SELECT * FROM runtime_control_executor_leases WHERE lease_id = ?",
                    (lease_row["lease_id"],),
                ).fetchone()
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
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
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease_row = _require_active_executor(
                    conn,
                    runtime_run_id,
                    executor_id,
                    attempt_no=attempt_no,
                )
                run_row = _run_row(conn, runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                validate_failed_outcome_row(conn, run_row)
                require_run_truth_mutable(run_row)
                stored_released_at = max_iso_timestamp(
                    released_at,
                    lease_row["heartbeat_at"],
                    lease_row["acquired_at"],
                )
                released = conn.execute(
                    """
                    UPDATE runtime_control_executor_leases
                    SET status = ?, released_at = ?, reason_code = ?
                    WHERE lease_id = ?
                      AND runtime_run_id = ?
                      AND executor_id = ?
                      AND attempt_no = ?
                      AND status = 'active'
                    """,
                    (
                        status,
                        stored_released_at,
                        reason_code,
                        lease_row["lease_id"],
                        runtime_run_id,
                        executor_id,
                        lease_row["attempt_no"],
                    ),
                )
                if released.rowcount != 1:
                    raise RuntimeControlError("runtime_executor_stale")
                advanced = conn.execute(
                    """
                    UPDATE runtime_control_runs
                    SET state_revision = state_revision + 1
                    WHERE runtime_run_id = ?
                      AND state_revision = ?
                      AND product_outcome IS NULL
                      AND current_failure_id IS NULL
                      AND current_failure_revision IS NULL
                      AND current_failure_owner_lease_id IS NULL
                      AND current_failure_authority_mode IS NULL
                    """,
                    (runtime_run_id, run_row["state_revision"]),
                )
                if advanced.rowcount != 1:
                    raise RuntimeControlError("runtime_executor_stale")
                updated = conn.execute(
                    """
                    SELECT *
                    FROM runtime_control_executor_leases
                    WHERE lease_id = ?
                    """,
                    (lease_row["lease_id"],),
                ).fetchone()
                conn.commit()
            except RuntimeControlError:
                conn.rollback()
                raise
            except (sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise RuntimeControlError(
                    "runtime_executor_release_failed"
                ) from None
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
                    SELECT lease.*
                    FROM runtime_control_executor_leases AS lease
                    JOIN runtime_control_runs AS run
                      ON run.runtime_run_id = lease.runtime_run_id
                    WHERE lease.status = 'active'
                      AND lease.lease_expires_at <= ?
                      AND run.product_outcome IS NULL
                      AND run.current_failure_id IS NULL
                      AND run.current_failure_revision IS NULL
                      AND run.current_failure_owner_lease_id IS NULL
                      AND run.current_failure_authority_mode IS NULL
                    ORDER BY lease.lease_expires_at ASC, lease.attempt_no ASC
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
                    conn.execute(
                        """
                        UPDATE runtime_control_runs
                        SET state_revision = state_revision + 1
                        WHERE runtime_run_id = ?
                        """,
                        (row["runtime_run_id"],),
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

    def settle_next_expired_executor_lease(
        self,
        *,
        now: str,
        resume_recoverable: bool,
        fault_injector: Callable[[str], None] | None = None,
    ) -> RuntimeRecoverySettlement | None:
        """Atomically expire and settle one active or legacy-stranded lease."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                lease_row = _next_recovery_lease_row(conn, now=now)
                if lease_row is None:
                    conn.commit()
                    return None
                run_row = _run_row(conn, lease_row["runtime_run_id"])
                if run_row is None:
                    conn.commit()
                    return None
                require_run_truth_mutable(run_row)
                if lease_row["status"] == "active":
                    updated = conn.execute(
                        """
                        UPDATE runtime_control_executor_leases
                        SET status = 'expired', released_at = ?,
                            reason_code = 'runtime_executor_lease_expired'
                        WHERE lease_id = ? AND status = 'active' AND lease_expires_at <= ?
                        """,
                        (now, lease_row["lease_id"], now),
                    )
                    if updated.rowcount != 1:
                        conn.commit()
                        return None
                    conn.execute(
                        """
                        UPDATE runtime_control_runs
                        SET state_revision = state_revision + 1
                        WHERE runtime_run_id = ?
                        """,
                        (run_row["runtime_run_id"],),
                    )
                elif _active_lease_row(conn, lease_row["runtime_run_id"]) is not None:
                    conn.commit()
                    return None
                _inject_recovery_fault(fault_injector, "after_lease_update")

                if run_row["status"] in _LEASE_ONLY_CLEANUP_RUN_STATUSES:
                    conn.commit()
                    _inject_recovery_fault(fault_injector, "after_commit")
                    return RuntimeRecoverySettlement(decision=None)

                checkpoint = (
                    None
                    if run_row["status"] in {"cancellation_requested", "pause_requested"}
                    else _recoverable_checkpoint_from_run_row(conn, run_row)
                )
                plan = decide_expired_lease_recovery(
                    run_status=run_row["status"],
                    checkpoint=checkpoint,
                    resume_recoverable=resume_recoverable,
                )
                expiry_event = _append_recovery_expiry_event(
                    conn,
                    lease_row=lease_row,
                    run_row=run_row,
                    now=now,
                )
                _inject_recovery_fault(fault_injector, "after_first_event")
                _append_recovery_decision_event(
                    conn,
                    lease_row=lease_row,
                    run_row=run_row,
                    checkpoint=checkpoint,
                    plan=plan,
                    after_event_seq=expiry_event.event_seq,
                    now=now,
                )
                _inject_recovery_fault(fault_injector, "before_run_transition")
                require_run_transition(run_row["status"], plan.target_status)
                checkpoint_stage = (
                    checkpoint.stage
                    if isinstance(checkpoint, RuntimeCheckpoint)
                    and plan.target_status == "resume_requested"
                    else run_row["current_stage"]
                )
                checkpoint_round = (
                    checkpoint.round_no
                    if isinstance(checkpoint, RuntimeCheckpoint)
                    and plan.target_status == "resume_requested"
                    else run_row["current_round"]
                )
                terminal = plan.target_status in _TERMINAL_RUN_STATUSES
                if plan.target_status == "needs_attention":
                    enter_recovery_attention(
                        conn,
                        runtime_run_id=run_row["runtime_run_id"],
                        entered_at=now,
                    )
                conn.execute(
                    """
                    UPDATE runtime_control_runs
                    SET status = ?, current_stage = ?, current_round = ?, updated_at = ?,
                        stop_reason_code = CASE WHEN ? THEN ? ELSE stop_reason_code END,
                        completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE completed_at END,
                        state_revision = state_revision + 1
                    WHERE runtime_run_id = ? AND status = ?
                    """,
                    (
                        plan.target_status,
                        checkpoint_stage,
                        checkpoint_round,
                        now,
                        terminal,
                        plan.reason_code,
                        terminal,
                        now,
                        run_row["runtime_run_id"],
                        run_row["status"],
                    ),
                )
                _inject_recovery_fault(fault_injector, "after_run_transition")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        _inject_recovery_fault(fault_injector, "after_commit")
        return RuntimeRecoverySettlement(
            decision=RuntimeRecoveryDecision(
                runtime_run_id=run_row["runtime_run_id"],
                reason_code=plan.reason_code,
            )
        )

    def resolve_source_operation_recovery_attention(
        self,
        *,
        runtime_run_id: str,
        resolved_at: str,
    ) -> RuntimeRunRecord:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                run_row = _run_row(conn, runtime_run_id)
                if (
                    run_row is None
                    or run_row["status"] != "needs_attention"
                    or run_row["current_action_id"] is not None
                    or _active_lease_row(conn, runtime_run_id) is not None
                ):
                    raise RuntimeControlError(
                        "runtime_recovery_attention_not_active"
                    )
                resolve_recovery_attention(
                    conn,
                    runtime_run_id=runtime_run_id,
                    resolved_at=resolved_at,
                )
                require_run_transition("needs_attention", "resume_requested")
                conn.execute(
                    """
                    UPDATE runtime_control_runs
                    SET status = 'resume_requested',
                        product_outcome = NULL,
                        updated_at = ?,
                        state_revision = state_revision + 1
                    WHERE runtime_run_id = ?
                      AND status = 'needs_attention'
                      AND current_action_id IS NULL
                    """,
                    (resolved_at, runtime_run_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.get_run(runtime_run_id)

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
            existing = _run_row(conn, runtime_run_id)
            if existing is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            require_run_truth_mutable(existing)
            updated = conn.execute(
                """
                UPDATE runtime_control_runs
                SET approved_requirement_revision_id = ?, updated_at = ?,
                    state_revision = state_revision + 1
                WHERE runtime_run_id = ?
                  AND product_outcome IS NULL
                  AND current_failure_id IS NULL
                  AND current_failure_revision IS NULL
                  AND current_failure_owner_lease_id IS NULL
                  AND current_failure_authority_mode IS NULL
                """,
                (approved_requirement_revision_id, updated_at, runtime_run_id),
            )
            if updated.rowcount != 1:
                raise RuntimeControlError(
                    "runtime_failed_outcome_terminal_immutable"
                )
            row = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
            return _validated_run_from_row(conn, row)

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
                      AND run.product_outcome IS NULL
                      AND run.current_failure_id IS NULL
                      AND run.current_failure_revision IS NULL
                      AND run.current_failure_owner_lease_id IS NULL
                      AND run.current_failure_authority_mode IS NULL
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
                          AND product_outcome IS NULL
                          AND current_failure_id IS NULL
                          AND current_failure_revision IS NULL
                          AND current_failure_owner_lease_id IS NULL
                          AND current_failure_authority_mode IS NULL
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
        if checkpoint.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V1:
            if (
                checkpoint.safe_boundary == "after_round_controller"
                and checkpoint.run_state.get("round") != checkpoint.round_no
            ):
                raise RuntimeControlError(
                    "runtime_checkpoint_safe_boundary_invalid"
                )
            run = self.get_run(checkpoint.runtime_run_id)
            projection = legacy_checkpoint_projection(checkpoint.run_state)
            return self.write_checkpoint_v2(
                checkpoint_id=checkpoint.checkpoint_id,
                runtime_run_id=checkpoint.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                stage=checkpoint.stage,
                round_no=checkpoint.round_no,
                safe_boundary=checkpoint.safe_boundary,
                accepted_requirement_revision_id=run.approved_requirement_revision_id,
                source_ids=_object_string_list(
                    checkpoint.source_plan.get("sourceIds")
                ),
                projection=projection,
                detail_claim_revision=0,
                detail_claim_hash=None,
                created_at=checkpoint.created_at,
                artifact_manifest_ref=checkpoint.artifact_manifest_ref,
                pending_commands=checkpoint.pending_commands,
            )
        if checkpoint.schema_version != RUNTIME_CHECKPOINT_SCHEMA_V2:
            raise RuntimeControlError("runtime_checkpoint_schema_unsupported")
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
                run_row = _run_row(conn, checkpoint.runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                require_run_truth_mutable(run_row)
                write_checkpoint_participant(conn, checkpoint)
                updated = conn.execute(
                    """
                    UPDATE runtime_control_runs
                    SET latest_checkpoint_id = ?, current_stage = ?, current_round = ?,
                        updated_at = ?, state_revision = state_revision + 1
                    WHERE runtime_run_id = ?
                      AND product_outcome IS NULL
                      AND current_failure_id IS NULL
                      AND current_failure_revision IS NULL
                      AND current_failure_owner_lease_id IS NULL
                      AND current_failure_authority_mode IS NULL
                    """,
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.stage,
                        checkpoint.round_no,
                        checkpoint.created_at,
                        checkpoint.runtime_run_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeControlError(
                        "runtime_failed_outcome_terminal_immutable"
                    )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return checkpoint

    def write_checkpoint_v2(
        self,
        *,
        checkpoint_id: str,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None,
        stage: str,
        round_no: int | None,
        safe_boundary: str,
        accepted_requirement_revision_id: str,
        source_ids: list[str],
        projection: CheckpointProjection,
        detail_claim_revision: int,
        detail_claim_hash: str | None,
        created_at: str,
        artifact_manifest_ref: str | None = None,
        pending_commands: list[dict[str, object]] | None = None,
        continuation_cursor: dict[str, object] | None = None,
        source_operation_ids: tuple[str, ...] = (),
    ) -> RuntimeCheckpoint:
        if projection.schema_version != RUNTIME_CHECKPOINT_SCHEMA_V2:
            raise RuntimeControlError("runtime_checkpoint_schema_unsupported")
        if safe_boundary not in V2_SAFE_BOUNDARIES:
            raise RuntimeControlError("runtime_checkpoint_safe_boundary_unregistered")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _require_active_executor(
                    conn,
                    runtime_run_id,
                    executor_id,
                    attempt_no=attempt_no,
                    observed_at=created_at,
                )
                run_row = _run_row(conn, runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                require_run_truth_mutable(run_row)
                if (
                    run_row["approved_requirement_revision_id"]
                    != accepted_requirement_revision_id
                ):
                    raise RuntimeControlError(
                        "runtime_checkpoint_requirement_revision_mismatch"
                    )
                truth_revision, truth_hash = _sync_candidate_truth_v2(
                    conn,
                    runtime_run_id=runtime_run_id,
                    candidate_state=projection.candidate_state,
                    source_lane_results=projection.source_lane_results,
                    created_at=created_at,
                )
                _sync_round_states_v2(
                    conn,
                    runtime_run_id=runtime_run_id,
                    round_states=projection.round_states,
                    candidate_truth_revision=truth_revision,
                    created_at=created_at,
                )
                _sync_finalization_revisions_v2(
                    conn,
                    runtime_run_id=runtime_run_id,
                    candidate_state=projection.candidate_state,
                    finalization_revisions=projection.finalization_revisions,
                    checkpoint_id=checkpoint_id,
                    created_at=created_at,
                )
                detail_owner = _validated_detail_claim_owner(
                    conn,
                    runtime_run_id=runtime_run_id,
                )
                if (
                    detail_owner[0] != detail_claim_revision
                    or detail_owner[1] != detail_claim_hash
                ):
                    raise RuntimeControlError(
                        "runtime_checkpoint_detail_claim_binding_invalid"
                    )
                source_result_count, source_result_hash = (
                    _validated_source_result_owner(
                        conn,
                        runtime_run_id=runtime_run_id,
                    )
                )
                round_high_watermark, round_ledger_hash = (
                    _validated_round_owner(
                        conn,
                        runtime_run_id=runtime_run_id,
                        candidate_truth_revision=truth_revision,
                    )
                )
                finalization_revision, finalization_ledger_hash = (
                    _validated_finalization_owner(
                        conn,
                        runtime_run_id=runtime_run_id,
                    )
                )
                state_revision = int(run_row["state_revision"]) + 1
                durable_refs: dict[str, object] = {
                    "candidateTruth": f"runtime-candidate-truth://{runtime_run_id}/{truth_revision}",
                    "detailClaims": f"runtime-detail-claims://{runtime_run_id}/{detail_claim_revision}",
                    "roundLedgerHighWatermark": round_high_watermark,
                    "roundLedgerHash": round_ledger_hash,
                    "sourceResultCount": source_result_count,
                    "sourceResultHash": source_result_hash,
                    "finalizationRevision": finalization_revision,
                    "finalizationLedgerHash": finalization_ledger_hash,
                    "continuationCursor": _checkpoint_continuation_cursor(
                        safe_boundary=safe_boundary,
                        round_high_watermark=round_high_watermark,
                        supplied=continuation_cursor,
                    ),
                }
                checkpoint = RuntimeCheckpoint(
                    checkpoint_id=checkpoint_id,
                    runtime_run_id=runtime_run_id,
                    stage=stage,
                    round_no=round_no,
                    safe_boundary=safe_boundary,
                    run_state=projection.control_state,
                    source_plan={"sourceIds": list(source_ids)},
                    pending_commands=list(pending_commands or []),
                    artifact_manifest_ref=artifact_manifest_ref,
                    schema_version=RUNTIME_CHECKPOINT_SCHEMA_V2,
                    created_at=created_at,
                    state_revision=state_revision,
                    accepted_requirement_revision_id=accepted_requirement_revision_id,
                    control_state_hash=projection.control_state_hash,
                    candidate_truth_revision=truth_revision,
                    candidate_truth_hash=truth_hash,
                    detail_claim_revision=detail_claim_revision,
                    detail_claim_hash=detail_claim_hash,
                    durable_refs=durable_refs,
                    field_bytes=projection.field_bytes,
                    serialization_latency_ms=projection.serialization_latency_ms,
                    projection_latency_ms=projection.projection_latency_ms,
                    payload_size_bytes=projection.payload_size_bytes,
                )
                _commit_source_operations_with_checkpoint(
                    conn,
                    runtime_run_id=runtime_run_id,
                    checkpoint_id=checkpoint_id,
                    operation_ids=source_operation_ids,
                )
                write_checkpoint_participant(conn, checkpoint)
                self._update_checkpoint_pointer(conn, checkpoint)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return checkpoint

    def _update_checkpoint_pointer(
        self,
        conn: sqlite3.Connection,
        checkpoint: RuntimeCheckpoint,
    ) -> None:
        updated = conn.execute(
            """
            UPDATE runtime_control_runs
            SET latest_checkpoint_id = ?, current_stage = ?, current_round = ?,
                updated_at = ?, state_revision = ?
            WHERE runtime_run_id = ?
              AND product_outcome IS NULL
              AND current_failure_id IS NULL
              AND current_failure_revision IS NULL
              AND current_failure_owner_lease_id IS NULL
              AND current_failure_authority_mode IS NULL
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.stage,
                checkpoint.round_no,
                checkpoint.created_at,
                checkpoint.state_revision,
                checkpoint.runtime_run_id,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError("runtime_failed_outcome_terminal_immutable")

    def write_detail_claim_snapshot(
        self,
        *,
        runtime_run_id: str,
        claims: dict[str, object],
        expected_revision: int,
        updated_at: str,
    ) -> tuple[int, str]:
        payload_hash = detail_claim_hash(claims)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT revision, payload_hash
                    FROM runtime_control_detail_claim_state
                    WHERE runtime_run_id = ?
                    """,
                    (runtime_run_id,),
                ).fetchone()
                current_revision = int(row["revision"]) if row is not None else 0
                if current_revision != expected_revision:
                    raise RuntimeControlError("runtime_detail_claim_revision_conflict")
                if row is not None and row["payload_hash"] == payload_hash:
                    conn.commit()
                    return current_revision, payload_hash
                revision = current_revision + 1
                conn.execute(
                    "DELETE FROM runtime_control_detail_claims WHERE runtime_run_id = ?",
                    (runtime_run_id,),
                )
                for provider_key, raw_claim in sorted(claims.items()):
                    claim = _string_key_dict(raw_claim)
                    conn.execute(
                        """
                        INSERT INTO runtime_control_detail_claims (
                            runtime_run_id, provider_candidate_key_hash, status,
                            browser_open_attempt_count, last_safe_reason_code,
                            revision, payload_hash, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            runtime_run_id,
                            provider_key,
                            claim.get("status"),
                            _nonnegative_int(
                                claim.get("browser_open_attempt_count")
                            ),
                            claim.get("last_safe_reason_code"),
                            revision,
                            sha256(_json(claim).encode("utf-8")).hexdigest(),
                            updated_at,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO runtime_control_detail_claim_state (
                        runtime_run_id, revision, payload_hash, updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(runtime_run_id) DO UPDATE SET
                        revision = excluded.revision,
                        payload_hash = excluded.payload_hash,
                        updated_at = excluded.updated_at
                    """,
                    (runtime_run_id, revision, payload_hash, updated_at),
                )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return revision, payload_hash

    def get_detail_claim_revision(self, *, runtime_run_id: str) -> tuple[int, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT revision, payload_hash
                FROM runtime_control_detail_claim_state
                WHERE runtime_run_id = ?
                """,
                (runtime_run_id,),
            ).fetchone()
        if row is None:
            return 0, None
        return int(row["revision"]), str(row["payload_hash"])

    def get_detail_claim_snapshot(
        self,
        *,
        runtime_run_id: str,
    ) -> dict[str, object]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_detail_claims
                WHERE runtime_run_id = ?
                ORDER BY provider_candidate_key_hash
                """,
                (runtime_run_id,),
            ).fetchall()
        return {
            row["provider_candidate_key_hash"]: {
                "status": row["status"],
                "browser_open_attempt_count": int(
                    row["browser_open_attempt_count"]
                ),
                "last_safe_reason_code": row["last_safe_reason_code"],
            }
            for row in rows
        }

    def compact_terminal_checkpoints(
        self,
        *,
        runtime_run_id: str,
    ) -> RuntimeCheckpointCompactionResult:
        manifest_id = f"rtmanifest_{runtime_run_id}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                run_row = _run_row(conn, runtime_run_id)
                if run_row is None:
                    raise RuntimeControlLookupError("runtime_run_not_found")
                if run_row["status"] not in _TERMINAL_RUN_STATUSES:
                    raise RuntimeControlError(
                        "runtime_checkpoint_compaction_run_not_terminal"
                    )
                existing = conn.execute(
                    """
                    SELECT *
                    FROM runtime_control_checkpoints
                    WHERE runtime_run_id = ? AND is_final_manifest = 1
                    """,
                    (runtime_run_id,),
                ).fetchone()
                if existing is None:
                    latest_id = run_row["latest_checkpoint_id"]
                    latest = (
                        conn.execute(
                            """
                            SELECT *
                            FROM runtime_control_checkpoints
                            WHERE runtime_run_id = ? AND checkpoint_id = ?
                            """,
                            (runtime_run_id, latest_id),
                        ).fetchone()
                        if latest_id is not None
                        else None
                    )
                    if latest is None:
                        raise RuntimeControlError(
                            "runtime_checkpoint_compaction_source_missing"
                        )
                    source = _checkpoint_from_row(latest)
                    if source.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V1:
                        _upgrade_legacy_checkpoint_in_transaction(
                            conn,
                            source,
                        )
                    action_rows = conn.execute(
                        """
                        SELECT *
                        FROM runtime_control_user_actions
                        WHERE runtime_run_id = ?
                        ORDER BY created_at, action_id
                        """,
                        (runtime_run_id,),
                    ).fetchall()
                    for action in action_rows:
                        action_checkpoint_row = conn.execute(
                            """
                            SELECT *
                            FROM runtime_control_checkpoints
                            WHERE runtime_run_id = ? AND checkpoint_id = ?
                            """,
                            (runtime_run_id, action["checkpoint_id"]),
                        ).fetchone()
                        if action_checkpoint_row is None:
                            archived = conn.execute(
                                """
                                SELECT 1
                                FROM runtime_control_action_checkpoint_evidence
                                WHERE action_id = ?
                                  AND runtime_run_id = ?
                                  AND original_checkpoint_id = ?
                                """,
                                (
                                    action["action_id"],
                                    runtime_run_id,
                                    action["checkpoint_id"],
                                ),
                            ).fetchone()
                            if archived is not None:
                                continue
                            raise RuntimeControlError(
                                "runtime_checkpoint_compaction_action_source_missing"
                            )
                        action_checkpoint = _checkpoint_from_row(
                            action_checkpoint_row
                        )
                        _archive_action_checkpoint_evidence(
                            conn,
                            action=action,
                            checkpoint=action_checkpoint,
                            archived_at=source.created_at,
                        )
                    conn.execute(
                        """
                        UPDATE runtime_control_candidate_finalization_revisions
                        SET source_checkpoint_id = ?
                        WHERE runtime_run_id = ?
                        """,
                        (manifest_id, runtime_run_id),
                    )
                    finalization_revision, finalization_ledger_hash = (
                        _validated_finalization_owner(
                            conn,
                            runtime_run_id=runtime_run_id,
                        )
                    )
                    empty_control_hash = sha256(b"{}").hexdigest()
                    manifest = source.model_copy(
                        update={
                            "checkpoint_id": manifest_id,
                            "stage": "finalization",
                            "round_no": None,
                            "safe_boundary": "after_finalization_commit",
                            "run_state": {},
                            "control_state_hash": empty_control_hash,
                            "pending_commands": [],
                            "field_bytes": {},
                            "serialization_latency_ms": 0.0,
                            "projection_latency_ms": 0.0,
                            "payload_size_bytes": 2,
                            "is_final_manifest": True,
                            "durable_refs": {
                                **source.durable_refs,
                                "terminalStatus": run_row["status"],
                                "finalizationRevision": finalization_revision,
                                "finalizationLedgerHash": (
                                    finalization_ledger_hash
                                ),
                                "continuationCursor": {
                                    "nextPhase": "complete",
                                    "completedRounds": source.durable_refs.get(
                                        "roundLedgerHighWatermark",
                                        0,
                                    ),
                                    "stopReason": "terminal_manifest",
                                },
                            },
                            "schema_version": RUNTIME_CHECKPOINT_SCHEMA_V2,
                        }
                    )
                    if action_rows:
                        conn.execute(
                            "DROP TRIGGER "
                            "runtime_action_checkpoints_delete_forbidden"
                        )
                    conn.execute(
                        """
                        DELETE FROM runtime_control_checkpoints
                        WHERE runtime_run_id = ?
                        """,
                        (runtime_run_id,),
                    )
                    write_checkpoint_participant(conn, manifest)
                    if action_rows:
                        conn.execute(
                            _needs_attention_trigger_statement(
                                "runtime_action_checkpoints_delete_forbidden"
                            )
                        )
                    conn.execute(
                        """
                        UPDATE runtime_control_runs
                        SET latest_checkpoint_id = ?, updated_at = ?
                        WHERE runtime_run_id = ?
                        """,
                        (manifest_id, source.created_at, runtime_run_id),
                    )
                    existing = conn.execute(
                        """
                        SELECT *
                        FROM runtime_control_checkpoints
                        WHERE checkpoint_id = ?
                        """,
                        (manifest_id,),
                    ).fetchone()
                else:
                    action_rows = conn.execute(
                        """
                        SELECT *
                        FROM runtime_control_user_actions
                        WHERE runtime_run_id = ? AND checkpoint_id <> ?
                        ORDER BY created_at, action_id
                        """,
                        (runtime_run_id, manifest_id),
                    ).fetchall()
                    for action in action_rows:
                        action_checkpoint_row = conn.execute(
                            """
                            SELECT *
                            FROM runtime_control_checkpoints
                            WHERE runtime_run_id = ? AND checkpoint_id = ?
                            """,
                            (runtime_run_id, action["checkpoint_id"]),
                        ).fetchone()
                        if action_checkpoint_row is None:
                            archived = conn.execute(
                                """
                                SELECT 1
                                FROM runtime_control_action_checkpoint_evidence
                                WHERE action_id = ?
                                  AND runtime_run_id = ?
                                  AND original_checkpoint_id = ?
                                """,
                                (
                                    action["action_id"],
                                    runtime_run_id,
                                    action["checkpoint_id"],
                                ),
                            ).fetchone()
                            if archived is not None:
                                continue
                            raise RuntimeControlError(
                                "runtime_checkpoint_compaction_action_source_missing"
                            )
                        action_checkpoint = _checkpoint_from_row(
                            action_checkpoint_row
                        )
                        _archive_action_checkpoint_evidence(
                            conn,
                            action=action,
                            checkpoint=action_checkpoint,
                            archived_at=str(existing["created_at"]),
                        )
                    if action_rows:
                        conn.execute(
                            "DROP TRIGGER "
                            "runtime_action_checkpoints_delete_forbidden"
                        )
                    conn.execute(
                        """
                        DELETE FROM runtime_control_checkpoints
                        WHERE runtime_run_id = ? AND checkpoint_id <> ?
                        """,
                        (runtime_run_id, manifest_id),
                    )
                    if action_rows:
                        conn.execute(
                            _needs_attention_trigger_statement(
                                "runtime_action_checkpoints_delete_forbidden"
                            )
                        )
                    conn.execute(
                        """
                        UPDATE runtime_control_candidate_finalization_revisions
                        SET source_checkpoint_id = ?
                        WHERE runtime_run_id = ?
                        """,
                        (manifest_id, runtime_run_id),
                    )
                count, size = _checkpoint_count_and_bytes(
                    conn,
                    runtime_run_id=runtime_run_id,
                )
                result = RuntimeCheckpointCompactionResult(
                    runtime_run_id=runtime_run_id,
                    checkpoint_count=count,
                    checkpoint_bytes=size,
                    manifest_checkpoint_id=str(existing["checkpoint_id"]),
                )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return result

    def compact_pending_terminal_checkpoints(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT runtime_run_id
                FROM runtime_control_runs AS run
                WHERE run.status IN ('cancelled', 'completed', 'failed')
                  AND run.latest_checkpoint_id IS NOT NULL
                  AND (
                    (
                      SELECT COUNT(*)
                      FROM runtime_control_checkpoints AS checkpoint
                      WHERE checkpoint.runtime_run_id = run.runtime_run_id
                    ) <> 1
                    OR NOT EXISTS (
                      SELECT 1
                      FROM runtime_control_checkpoints AS checkpoint
                      WHERE checkpoint.runtime_run_id = run.runtime_run_id
                        AND checkpoint.is_final_manifest = 1
                    )
                  )
                ORDER BY run.completed_at, run.runtime_run_id
                """
            ).fetchall()
        for row in rows:
            self.compact_terminal_checkpoints(
                runtime_run_id=str(row["runtime_run_id"])
            )

    def checkpoint_storage_metrics(
        self,
        *,
        runtime_run_id: str,
    ) -> dict[str, object]:
        with self._connect() as conn:
            checkpoint_count, checkpoint_bytes = _checkpoint_count_and_bytes(
                conn,
                runtime_run_id=runtime_run_id,
            )
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            freelist_count = int(
                conn.execute("PRAGMA freelist_count").fetchone()[0]
            )
            rows = conn.execute(
                """
                SELECT checkpoint_id, payload_size_bytes, field_bytes_json,
                       serialization_latency_ms, projection_latency_ms,
                       (
                         length(checkpoint_id)
                         + length(runtime_run_id)
                         + length(stage)
                         + length(safe_boundary)
                         + length(run_state_json)
                         + length(source_plan_json)
                         + length(pending_commands_json)
                         + COALESCE(length(artifact_manifest_ref), 0)
                         + length(schema_version)
                         + length(created_at)
                         + COALESCE(length(control_state_hash), 0)
                         + COALESCE(length(candidate_truth_hash), 0)
                         + COALESCE(length(detail_claim_hash), 0)
                         + length(durable_refs_json)
                         + length(field_bytes_json)
                       ) AS checkpoint_bytes
                FROM runtime_control_checkpoints
                WHERE runtime_run_id = ?
                ORDER BY created_at, rowid
                """,
                (runtime_run_id,),
            ).fetchall()
        wal_path = self.path.with_name(f"{self.path.name}-wal")
        return {
            "checkpointCount": checkpoint_count,
            "checkpointBytes": checkpoint_bytes,
            "databaseBytes": page_size * page_count,
            "walBytes": wal_path.stat().st_size if wal_path.exists() else 0,
            "freelistBytes": page_size * freelist_count,
            "checkpoints": [
                {
                    "checkpointId": row["checkpoint_id"],
                    "checkpointBytes": int(row["checkpoint_bytes"]),
                    "controlPayloadBytes": int(row["payload_size_bytes"]),
                    "fieldBytes": _json_object(row["field_bytes_json"]),
                    "serializationLatencyMs": float(
                        row["serialization_latency_ms"]
                    ),
                    "projectionLatencyMs": float(
                        row["projection_latency_ms"]
                    ),
                }
                for row in rows
            ],
        }

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
            run_row = _run_row(conn, runtime_run_id)
            if run_row is None:
                raise RuntimeControlLookupError("runtime_run_not_found")
            return _recoverable_checkpoint_from_run_row(conn, run_row)

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
                run_row = _next_runnable_run_row(
                    conn,
                    runtime_run_id=runtime_run_id,
                    claimed_at=claimed_at,
                )
                if run_row is None:
                    conn.commit()
                    return None
                claim_reason = run_row["status"]
                if claim_reason == "resume_requested":
                    waiting_event = conn.execute(
                        """
                        SELECT event_type
                        FROM runtime_control_events
                        WHERE runtime_run_id = ?
                        ORDER BY event_seq DESC
                        LIMIT 1
                        """,
                        (run_row["runtime_run_id"],),
                    ).fetchone()
                    if (
                        waiting_event is not None
                        and waiting_event["event_type"]
                        == "runtime_resource_waiting"
                    ):
                        claim_reason = "resource_wait"
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
                runtime_run = _validated_run_from_row(conn, updated_run)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return RuntimeWorkerClaim(
            runtime_run=runtime_run,
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


def _commit_source_operations_with_checkpoint(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    checkpoint_id: str,
    operation_ids: tuple[str, ...],
) -> None:
    if len(operation_ids) != len(set(operation_ids)):
        raise RuntimeControlError("source_operation_checkpoint_duplicate")
    for operation_id in operation_ids:
        row = _source_operation_row(conn, runtime_run_id, operation_id)
        if row is None:
            raise RuntimeControlLookupError("source_operation_not_found")
        operation = source_operation_from_row(row)
        if (
            operation.operation_phase not in {"observed", "reconciled"}
            or operation.conclusive_observation_ref is None
            or operation.source_operation_disposition is None
        ):
            raise RuntimeControlError("source_operation_not_conclusive")
        updated = conn.execute(
            """
            UPDATE runtime_control_source_operations
            SET operation_phase = 'main_committed',
                main_commit_ref = ?,
                ledger_revision = ledger_revision + 1
            WHERE runtime_run_id = ? AND operation_id = ?
              AND operation_phase = ?
              AND ledger_revision = ?
              AND main_commit_ref IS NULL
              AND conclusive_observation_ref IS NOT NULL
              AND source_operation_disposition IS NOT NULL
            """,
            (
                checkpoint_id,
                runtime_run_id,
                operation_id,
                operation.operation_phase,
                operation.ledger_revision,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError("source_operation_checkpoint_conflict")


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
          created_at TEXT NOT NULL,
          state_revision INTEGER NOT NULL DEFAULT 0,
          accepted_requirement_revision_id TEXT,
          control_state_hash TEXT,
          candidate_truth_revision INTEGER NOT NULL DEFAULT 0,
          candidate_truth_hash TEXT,
          detail_claim_revision INTEGER NOT NULL DEFAULT 0,
          detail_claim_hash TEXT,
          durable_refs_json TEXT NOT NULL DEFAULT '{}',
          field_bytes_json TEXT NOT NULL DEFAULT '{}',
          serialization_latency_ms REAL NOT NULL DEFAULT 0,
          projection_latency_ms REAL NOT NULL DEFAULT 0,
          payload_size_bytes INTEGER NOT NULL DEFAULT 0,
          is_final_manifest INTEGER NOT NULL DEFAULT 0
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

        CREATE TABLE IF NOT EXISTS runtime_control_candidate_records (
          runtime_run_id TEXT NOT NULL,
          resume_id TEXT NOT NULL,
          identity_id TEXT,
          candidate_json TEXT NOT NULL,
          normalized_json TEXT,
          scorecard_json TEXT,
          payload_hash TEXT NOT NULL,
          truth_revision INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(runtime_run_id, resume_id)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_candidate_truth_state (
          runtime_run_id TEXT PRIMARY KEY,
          revision INTEGER NOT NULL,
          payload_hash TEXT NOT NULL,
          identity_payloads_json TEXT NOT NULL,
          identity_by_resume_id_json TEXT NOT NULL,
          aliases_json TEXT NOT NULL,
          conflicts_json TEXT NOT NULL,
          canonical_selections_json TEXT NOT NULL,
          source_evidence_by_resume_json TEXT NOT NULL,
          source_evidence_by_identity_json TEXT NOT NULL,
          source_lane_results_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runtime_control_detail_claims (
          runtime_run_id TEXT NOT NULL,
          provider_candidate_key_hash TEXT NOT NULL,
          status TEXT NOT NULL,
          browser_open_attempt_count INTEGER NOT NULL,
          last_safe_reason_code TEXT,
          revision INTEGER NOT NULL,
          payload_hash TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(runtime_run_id, provider_candidate_key_hash)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_detail_claim_state (
          runtime_run_id TEXT PRIMARY KEY,
          revision INTEGER NOT NULL,
          payload_hash TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runtime_control_round_states (
          runtime_run_id TEXT NOT NULL,
          round_no INTEGER NOT NULL,
          state_json TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          candidate_truth_revision INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(runtime_run_id, round_no)
        );

        CREATE TABLE IF NOT EXISTS runtime_control_action_checkpoint_evidence (
          action_id TEXT PRIMARY KEY,
          runtime_run_id TEXT NOT NULL,
          original_checkpoint_id TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          evidence_digest TEXT NOT NULL,
          checkpoint_hash TEXT NOT NULL,
          candidate_truth_hash TEXT NOT NULL,
          archived_at TEXT NOT NULL
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
    _ensure_action_checkpoint_evidence_schema(conn)


def _ensure_action_checkpoint_evidence_schema(
    conn: sqlite3.Connection,
) -> None:
    columns = {
        str(row[1])
        for row in conn.execute(
            """
            PRAGMA table_info(
                runtime_control_action_checkpoint_evidence
            )
            """
        ).fetchall()
    }
    if "evidence_digest" not in columns:
        conn.execute(
            """
            ALTER TABLE runtime_control_action_checkpoint_evidence
            ADD COLUMN evidence_digest TEXT NOT NULL DEFAULT ''
            """
        )
    rows = (
        conn.execute(
            """
            SELECT evidence.*
            FROM runtime_control_action_checkpoint_evidence AS evidence
            JOIN runtime_control_user_actions AS action
              ON action.action_id = evidence.action_id
             AND action.runtime_run_id = evidence.runtime_run_id
            WHERE evidence.evidence_digest = ''
            """
        ).fetchall()
        if _table_exists(conn, "runtime_control_user_actions")
        else []
    )
    for row in rows:
        legacy_metadata = _strict_json_object(row["evidence_json"])
        metadata = {
            "schemaVersion": legacy_metadata.get("schemaVersion"),
            "stage": legacy_metadata.get("stage"),
            "safeBoundary": legacy_metadata.get("safeBoundary"),
            "stateRevision": legacy_metadata.get("stateRevision"),
        }
        evidence = _needs_attention.action_checkpoint_evidence_payload(
            action_id=str(row["action_id"]),
            runtime_run_id=str(row["runtime_run_id"]),
            original_checkpoint_id=str(row["original_checkpoint_id"]),
            checkpoint_hash=str(row["checkpoint_hash"]),
            candidate_truth_hash=str(row["candidate_truth_hash"]),
            checkpoint_metadata=metadata,
        )
        conn.execute(
            """
            UPDATE runtime_control_action_checkpoint_evidence
            SET evidence_json = ?, evidence_digest = ?
            WHERE action_id = ?
            """,
            (
                _json(evidence),
                _needs_attention.action_checkpoint_evidence_digest(
                    evidence
                ),
                row["action_id"],
            ),
        )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS
        runtime_action_checkpoint_evidence_update_forbidden
        BEFORE UPDATE ON runtime_control_action_checkpoint_evidence
        BEGIN
          SELECT RAISE(
            ABORT,
            'runtime_action_checkpoint_evidence_immutable'
          );
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS
        runtime_action_checkpoint_evidence_delete_forbidden
        BEFORE DELETE ON runtime_control_action_checkpoint_evidence
        BEGIN
          SELECT RAISE(
            ABORT,
            'runtime_action_checkpoint_evidence_delete_forbidden'
          );
        END
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


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    for statement in _SOURCE_OPERATION_V8_SCHEMA_STATEMENTS:
        conn.execute(statement)


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    for statement in _SOURCE_RECONCILIATION_SCHEMA_STATEMENTS:
        conn.execute(statement)


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    for statement in _SOURCE_OPERATION_ADMISSION_EXPECTATION_V10_SCHEMA_STATEMENTS:
        conn.execute(statement)


def _needs_attention_trigger_statement(trigger_name: str) -> str:
    return next(
        statement
        for statement in (
            _needs_attention.NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS
        )
        if f"CREATE TRIGGER {trigger_name}" in statement
    )


def _archive_action_checkpoint_evidence(
    conn: sqlite3.Connection,
    *,
    action: sqlite3.Row,
    checkpoint: RuntimeCheckpoint,
    archived_at: str,
) -> None:
    metadata = {
        "schemaVersion": checkpoint.schema_version,
        "stage": checkpoint.stage,
        "safeBoundary": checkpoint.safe_boundary,
        "stateRevision": checkpoint.state_revision,
    }
    evidence = _needs_attention.action_checkpoint_evidence_payload(
        action_id=str(action["action_id"]),
        runtime_run_id=str(action["runtime_run_id"]),
        original_checkpoint_id=checkpoint.checkpoint_id,
        checkpoint_hash=str(action["checkpoint_hash"]),
        candidate_truth_hash=str(action["candidate_truth_hash"]),
        checkpoint_metadata=metadata,
    )
    values = (
        action["action_id"],
        action["runtime_run_id"],
        checkpoint.checkpoint_id,
        _json(evidence),
        _needs_attention.action_checkpoint_evidence_digest(evidence),
        action["checkpoint_hash"],
        action["candidate_truth_hash"],
        archived_at,
    )
    existing = conn.execute(
        """
        SELECT *
        FROM runtime_control_action_checkpoint_evidence
        WHERE action_id = ?
        """,
        (action["action_id"],),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO runtime_control_action_checkpoint_evidence (
                action_id, runtime_run_id, original_checkpoint_id,
                evidence_json, evidence_digest, checkpoint_hash,
                candidate_truth_hash, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        return
    if tuple(
        existing[name]
        for name in (
            "action_id",
            "runtime_run_id",
            "original_checkpoint_id",
            "evidence_json",
            "evidence_digest",
            "checkpoint_hash",
            "candidate_truth_hash",
            "archived_at",
        )
    ) != values:
        raise RuntimeControlError(
            "runtime_action_checkpoint_evidence_conflict"
        )


def _migrate_v15_to_v16(conn: sqlite3.Connection) -> None:
    _needs_attention.validate_needs_attention_schema(conn)
    rows = conn.execute(
        """
        SELECT checkpoint_id, run_state_json, source_plan_json, pending_commands_json
        FROM runtime_control_checkpoints
        WHERE schema_version = ?
        """,
        (RUNTIME_CHECKPOINT_SCHEMA_V1,),
    ).fetchall()
    for row in rows:
        try:
            _strict_json_object(row["run_state_json"])
            _strict_json_object(row["source_plan_json"])
            _strict_json_object_list(row["pending_commands_json"])
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeControlError(
                "runtime_checkpoint_v1_migration_invalid",
                str(row["checkpoint_id"]),
            ) from exc

    checkpoint_columns = _column_names(conn, "runtime_control_checkpoints")
    for name, definition in (
        ("state_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("accepted_requirement_revision_id", "TEXT"),
        ("control_state_hash", "TEXT"),
        ("candidate_truth_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("candidate_truth_hash", "TEXT"),
        ("detail_claim_revision", "INTEGER NOT NULL DEFAULT 0"),
        ("detail_claim_hash", "TEXT"),
        ("durable_refs_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("field_bytes_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("serialization_latency_ms", "REAL NOT NULL DEFAULT 0"),
        ("projection_latency_ms", "REAL NOT NULL DEFAULT 0"),
        ("payload_size_bytes", "INTEGER NOT NULL DEFAULT 0"),
        ("is_final_manifest", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in checkpoint_columns:
            conn.execute(
                f"ALTER TABLE runtime_control_checkpoints ADD COLUMN {name} {definition}"
            )
    _create_schema(conn)
    latest_rows = conn.execute(
        """
        SELECT checkpoint.*, run.approved_requirement_revision_id,
               run.source_ids_json, run.state_revision
        FROM runtime_control_checkpoints AS checkpoint
        JOIN runtime_control_runs AS run
          ON run.runtime_run_id = checkpoint.runtime_run_id
         AND run.latest_checkpoint_id = checkpoint.checkpoint_id
        WHERE checkpoint.schema_version = ?
        ORDER BY checkpoint.runtime_run_id
        """,
        (RUNTIME_CHECKPOINT_SCHEMA_V1,),
    ).fetchall()
    for row in latest_rows:
        action_rows = conn.execute(
            """
            SELECT *
            FROM runtime_control_user_actions
            WHERE runtime_run_id = ? AND checkpoint_id = ?
            """,
            (row["runtime_run_id"], row["checkpoint_id"]),
        ).fetchall()
        for action in action_rows:
            _archive_action_checkpoint_evidence(
                conn,
                action=action,
                checkpoint=_checkpoint_from_row(row),
                archived_at=str(row["created_at"]),
            )
        run_state = _strict_json_object(row["run_state_json"])
        projection = legacy_checkpoint_projection(run_state)
        truth_revision, truth_hash = _sync_candidate_truth_v2(
            conn,
            runtime_run_id=row["runtime_run_id"],
            candidate_state=projection.candidate_state,
            source_lane_results=projection.source_lane_results,
            created_at=row["created_at"],
        )
        detail_revision, claims_hash = _migrate_detail_claims_v1(
            conn,
            runtime_run_id=row["runtime_run_id"],
            claims=projection.detail_claims,
            updated_at=row["created_at"],
        )
        _sync_round_states_v2(
            conn,
            runtime_run_id=row["runtime_run_id"],
            round_states=projection.round_states,
            candidate_truth_revision=truth_revision,
            created_at=row["created_at"],
        )
        _sync_finalization_revisions_v2(
            conn,
            runtime_run_id=row["runtime_run_id"],
            candidate_state=projection.candidate_state,
            finalization_revisions=projection.finalization_revisions,
            checkpoint_id=row["checkpoint_id"],
            created_at=row["created_at"],
        )
        source_result_count, source_result_hash = (
            _validated_source_result_owner(
                conn,
                runtime_run_id=row["runtime_run_id"],
            )
        )
        round_high_watermark, round_ledger_hash = _validated_round_owner(
            conn,
            runtime_run_id=row["runtime_run_id"],
            candidate_truth_revision=truth_revision,
        )
        finalization_revision, finalization_ledger_hash = (
            _validated_finalization_owner(
                conn,
                runtime_run_id=row["runtime_run_id"],
            )
        )
        durable_refs = {
            "candidateTruth": (
                f"runtime-candidate-truth://{row['runtime_run_id']}/"
                f"{truth_revision}"
            ),
            "detailClaims": (
                f"runtime-detail-claims://{row['runtime_run_id']}/"
                f"{detail_revision}"
            ),
            "roundLedgerHighWatermark": round_high_watermark,
            "roundLedgerHash": round_ledger_hash,
            "sourceResultCount": source_result_count,
            "sourceResultHash": source_result_hash,
            "finalizationRevision": finalization_revision,
            "finalizationLedgerHash": finalization_ledger_hash,
            "continuationCursor": _checkpoint_continuation_cursor(
                safe_boundary=row["safe_boundary"],
                round_high_watermark=round_high_watermark,
                supplied=None,
            ),
        }
        action_checkpoint_trigger = (
            "runtime_action_checkpoints_update_forbidden"
        )
        if action_rows:
            conn.execute(
                f"DROP TRIGGER {action_checkpoint_trigger}"
            )
        conn.execute(
            """
            UPDATE runtime_control_checkpoints
            SET run_state_json = ?, schema_version = ?,
                state_revision = ?,
                accepted_requirement_revision_id = ?,
                control_state_hash = ?,
                candidate_truth_revision = ?,
                candidate_truth_hash = ?,
                detail_claim_revision = ?,
                detail_claim_hash = ?,
                durable_refs_json = ?,
                field_bytes_json = ?,
                serialization_latency_ms = ?,
                projection_latency_ms = ?,
                payload_size_bytes = ?
            WHERE checkpoint_id = ?
            """,
            (
                _json(projection.control_state),
                RUNTIME_CHECKPOINT_SCHEMA_V2,
                int(row["state_revision"]),
                row["approved_requirement_revision_id"],
                projection.control_state_hash,
                truth_revision,
                truth_hash,
                detail_revision,
                claims_hash,
                _json(durable_refs),
                _json(projection.field_bytes),
                projection.serialization_latency_ms,
                projection.projection_latency_ms,
                projection.payload_size_bytes,
                row["checkpoint_id"],
            ),
        )
        if action_rows:
            conn.execute(
                _needs_attention_trigger_statement(
                    action_checkpoint_trigger
                )
            )


def _upgrade_legacy_checkpoint_in_transaction(
    conn: sqlite3.Connection,
    checkpoint: RuntimeCheckpoint,
) -> None:
    if checkpoint.schema_version != RUNTIME_CHECKPOINT_SCHEMA_V1:
        return
    if checkpoint.safe_boundary not in V2_SAFE_BOUNDARIES:
        raise RuntimeControlError("runtime_checkpoint_safe_boundary_unregistered")
    run_row = _run_row(conn, checkpoint.runtime_run_id)
    if run_row is None:
        raise RuntimeControlLookupError("runtime_run_not_found")
    projection = legacy_checkpoint_projection(checkpoint.run_state)
    truth_revision, truth_hash = _sync_candidate_truth_v2(
        conn,
        runtime_run_id=checkpoint.runtime_run_id,
        candidate_state=projection.candidate_state,
        source_lane_results=projection.source_lane_results,
        created_at=checkpoint.created_at,
    )
    detail_row = conn.execute(
        """
        SELECT revision, payload_hash
        FROM runtime_control_detail_claim_state
        WHERE runtime_run_id = ?
        """,
        (checkpoint.runtime_run_id,),
    ).fetchone()
    if detail_row is None:
        detail_revision, claims_hash = _migrate_detail_claims_v1(
            conn,
            runtime_run_id=checkpoint.runtime_run_id,
            claims=projection.detail_claims,
            updated_at=checkpoint.created_at,
        )
    else:
        detail_revision = int(detail_row["revision"])
        claims_hash = str(detail_row["payload_hash"])
    _sync_round_states_v2(
        conn,
        runtime_run_id=checkpoint.runtime_run_id,
        round_states=projection.round_states,
        candidate_truth_revision=truth_revision,
        created_at=checkpoint.created_at,
    )
    _sync_finalization_revisions_v2(
        conn,
        runtime_run_id=checkpoint.runtime_run_id,
        candidate_state=projection.candidate_state,
        finalization_revisions=projection.finalization_revisions,
        checkpoint_id=checkpoint.checkpoint_id,
        created_at=checkpoint.created_at,
    )
    source_result_count, source_result_hash = _validated_source_result_owner(
        conn,
        runtime_run_id=checkpoint.runtime_run_id,
    )
    round_high_watermark, round_ledger_hash = _validated_round_owner(
        conn,
        runtime_run_id=checkpoint.runtime_run_id,
        candidate_truth_revision=truth_revision,
    )
    finalization_revision, finalization_ledger_hash = (
        _validated_finalization_owner(
            conn,
            runtime_run_id=checkpoint.runtime_run_id,
        )
    )
    durable_refs: dict[str, object] = {
        "candidateTruth": (
            f"runtime-candidate-truth://{checkpoint.runtime_run_id}/{truth_revision}"
        ),
        "detailClaims": (
            f"runtime-detail-claims://{checkpoint.runtime_run_id}/{detail_revision}"
        ),
        "roundLedgerHighWatermark": round_high_watermark,
        "roundLedgerHash": round_ledger_hash,
        "sourceResultCount": source_result_count,
        "sourceResultHash": source_result_hash,
        "finalizationRevision": finalization_revision,
        "finalizationLedgerHash": finalization_ledger_hash,
        "continuationCursor": _checkpoint_continuation_cursor(
            safe_boundary=checkpoint.safe_boundary,
            round_high_watermark=round_high_watermark,
            supplied=None,
        ),
    }
    upgraded = checkpoint.model_copy(
        update={
            "run_state": projection.control_state,
            "schema_version": RUNTIME_CHECKPOINT_SCHEMA_V2,
            "state_revision": int(run_row["state_revision"]) + 1,
            "accepted_requirement_revision_id": run_row[
                "approved_requirement_revision_id"
            ],
            "control_state_hash": projection.control_state_hash,
            "candidate_truth_revision": truth_revision,
            "candidate_truth_hash": truth_hash,
            "detail_claim_revision": detail_revision,
            "detail_claim_hash": claims_hash,
            "durable_refs": durable_refs,
            "field_bytes": projection.field_bytes,
            "serialization_latency_ms": projection.serialization_latency_ms,
            "projection_latency_ms": projection.projection_latency_ms,
            "payload_size_bytes": projection.payload_size_bytes,
        }
    )
    for field_name in RuntimeCheckpoint.model_fields:
        setattr(checkpoint, field_name, getattr(upgraded, field_name))


def _migrate_detail_claims_v1(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    claims: dict[str, object],
    updated_at: str,
) -> tuple[int, str | None]:
    if not claims:
        return 0, None
    claims_hash = detail_claim_hash(claims)
    revision = 1
    for provider_key, raw_claim in sorted(claims.items()):
        claim = _string_key_dict(raw_claim)
        conn.execute(
            """
            INSERT INTO runtime_control_detail_claims (
                runtime_run_id, provider_candidate_key_hash, status,
                browser_open_attempt_count, last_safe_reason_code,
                revision, payload_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                runtime_run_id,
                provider_key,
                claim.get("status"),
                _nonnegative_int(
                    claim.get("browser_open_attempt_count")
                ),
                claim.get("last_safe_reason_code"),
                revision,
                sha256(_json(claim).encode("utf-8")).hexdigest(),
                updated_at,
            ),
        )
    conn.execute(
        """
        INSERT INTO runtime_control_detail_claim_state (
            runtime_run_id, revision, payload_hash, updated_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (runtime_run_id, revision, claims_hash, updated_at),
    )
    return revision, claims_hash


def _create_source_reconciliation_schema(conn: sqlite3.Connection) -> None:
    for statement in _SOURCE_RECONCILIATION_V11_SCHEMA_STATEMENTS:
        conn.execute(statement)


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
    require_run_truth_mutable(row)
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
    updated = conn.execute(
        """
        UPDATE runtime_control_runs
        SET latest_event_seq = ?, status = ?, current_stage = ?, current_round = ?, updated_at = ?,
            stop_reason_code = COALESCE(?, stop_reason_code),
            completed_at = COALESCE(?, completed_at),
            latest_checkpoint_id = COALESCE(?, latest_checkpoint_id),
            state_revision = state_revision + 1
        WHERE runtime_run_id = ?
          AND product_outcome IS NULL
          AND current_failure_id IS NULL
          AND current_failure_revision IS NULL
          AND current_failure_owner_lease_id IS NULL
          AND current_failure_authority_mode IS NULL
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
    if updated.rowcount != 1:
        raise RuntimeControlError("runtime_failed_outcome_terminal_immutable")
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


def _next_recovery_lease_row(
    conn: sqlite3.Connection,
    *,
    now: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT lease.*
        FROM runtime_control_executor_leases AS lease
        JOIN runtime_control_runs AS run
          ON run.runtime_run_id = lease.runtime_run_id
        WHERE (
            lease.status = 'active'
            AND lease.lease_expires_at <= ?
            AND run.status IN (
              'queued', 'starting', 'running', 'pause_requested', 'paused',
              'resume_requested', 'cancellation_requested', 'cancelled', 'completed', 'failed'
            )
          )
          OR (
            lease.status = 'expired'
            AND run.status IN ('starting', 'running', 'cancellation_requested')
            AND lease.attempt_no = (
              SELECT MAX(latest_attempt.attempt_no)
              FROM runtime_control_executor_leases AS latest_attempt
              WHERE latest_attempt.runtime_run_id = lease.runtime_run_id
            )
            AND NOT EXISTS (
              SELECT 1
              FROM runtime_control_executor_leases AS active
              WHERE active.runtime_run_id = lease.runtime_run_id
                AND active.status = 'active'
            )
            AND NOT EXISTS (
              SELECT 1
              FROM runtime_control_events AS settled_event
              WHERE settled_event.runtime_run_id = lease.runtime_run_id
                AND settled_event.idempotency_key =
                    'runtime-recovery:' || lease.lease_id || ':decision'
            )
          )
        ORDER BY
          CASE lease.status WHEN 'active' THEN 0 ELSE 1 END,
          lease.lease_expires_at ASC,
          lease.attempt_no DESC
        LIMIT 1
        """,
        (now,),
    ).fetchone()


def _recoverable_checkpoint_from_run_row(
    conn: sqlite3.Connection,
    run_row: sqlite3.Row,
) -> RuntimeCheckpoint | RuntimeCheckpointLoadFailure | None:
    checkpoint_id = run_row["latest_checkpoint_id"]
    if not _all_source_operations_main_committed(
        conn,
        runtime_run_id=run_row["runtime_run_id"],
    ):
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=(
                checkpoint_id
                or f"source-operation:{run_row['runtime_run_id']}"
            ),
            reason_code=RUNTIME_SOURCE_OPERATION_UNRESOLVED,
        )
    if checkpoint_id is None:
        return None
    checkpoint_row = conn.execute(
        "SELECT * FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
        (checkpoint_id,),
    ).fetchone()
    if checkpoint_row is None:
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=checkpoint_id,
            reason_code=RUNTIME_CHECKPOINT_MISSING,
        )
    if checkpoint_row["runtime_run_id"] != run_row["runtime_run_id"]:
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=checkpoint_id,
            reason_code=RUNTIME_CHECKPOINT_RUN_MISMATCH,
        )
    checkpoint = _recoverable_checkpoint_from_row_or_failure(checkpoint_row)
    if isinstance(checkpoint, RuntimeCheckpointLoadFailure):
        return checkpoint
    run_source_ids, run_source_ids_valid = _strict_run_source_ids(run_row["source_ids_json"])
    candidate_truth_valid = (
        _candidate_truth_matches_checkpoint(conn, checkpoint)
        if checkpoint.safe_boundary
        in {
            "runtime_candidate_checkpoint",
            "after_source_result_commit",
            "after_round_controller",
            "before_finalization",
            "after_finalization_commit",
            "entering_pause",
            "entering_needs_attention",
        }
        else True
    )
    source_operations_main_committed = True
    invalid_reason = validate_recoverable_checkpoint(
        checkpoint,
        RuntimeCheckpointValidationContext(
            run_status=run_row["status"],
            run_stage=run_row["current_stage"],
            run_round_no=run_row["current_round"],
            run_source_ids=run_source_ids,
            run_source_ids_valid=run_source_ids_valid,
            candidate_truth_valid=candidate_truth_valid,
            source_operations_main_committed=(
                source_operations_main_committed
            ),
        ),
    )
    if invalid_reason is not None:
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=checkpoint_id,
            reason_code=invalid_reason,
        )
    return checkpoint


def _all_source_operations_main_committed(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM runtime_control_source_operations
        WHERE runtime_run_id = ?
          AND (
            operation_phase != 'main_committed'
            OR main_commit_ref IS NULL
          )
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()
    return row is None


def _checkpoint_continuation_cursor(
    *,
    safe_boundary: str,
    round_high_watermark: int,
    supplied: dict[str, object] | None,
) -> dict[str, object]:
    next_phase = {
        "after_source_result_commit": "rounds",
        "after_round_controller": "rounds",
        "before_finalization": "finalization",
        "after_finalization_commit": "complete",
        "entering_pause": "rounds",
        "entering_needs_attention": "rounds",
    }.get(safe_boundary)
    if next_phase is None:
        return {}
    cursor = dict(supplied or {})
    cursor.setdefault("nextPhase", next_phase)
    cursor.setdefault("completedRounds", round_high_watermark)
    cursor.setdefault("stopReason", "max_rounds_reached")
    completed_rounds = cursor.get("completedRounds")
    if (
        cursor.get("nextPhase") != next_phase
        or not isinstance(completed_rounds, int)
        or isinstance(completed_rounds, bool)
        or completed_rounds != round_high_watermark
        or not isinstance(cursor.get("stopReason"), str)
    ):
        raise RuntimeControlError(
            "runtime_checkpoint_continuation_cursor_invalid"
        )
    return cursor


def _validated_detail_claim_owner(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
) -> tuple[int, str | None]:
    state = conn.execute(
        """
        SELECT revision, payload_hash
        FROM runtime_control_detail_claim_state
        WHERE runtime_run_id = ?
        """,
        (runtime_run_id,),
    ).fetchone()
    rows = conn.execute(
        """
        SELECT *
        FROM runtime_control_detail_claims
        WHERE runtime_run_id = ?
        ORDER BY provider_candidate_key_hash
        """,
        (runtime_run_id,),
    ).fetchall()
    if state is None:
        if rows:
            raise RuntimeControlError("runtime_checkpoint_durable_owner_mismatch")
        return 0, None
    revision = int(state["revision"])
    claims: dict[str, object] = {}
    for row in rows:
        claim = {
            "status": row["status"],
            "browser_open_attempt_count": int(
                row["browser_open_attempt_count"]
            ),
            "last_safe_reason_code": row["last_safe_reason_code"],
        }
        if (
            int(row["revision"]) != revision
            or row["payload_hash"]
            != sha256(_json(claim).encode("utf-8")).hexdigest()
        ):
            raise RuntimeControlError("runtime_checkpoint_durable_owner_mismatch")
        claims[str(row["provider_candidate_key_hash"])] = claim
    payload_hash = detail_claim_hash(claims)
    if state["payload_hash"] != payload_hash:
        raise RuntimeControlError("runtime_checkpoint_durable_owner_mismatch")
    return revision, payload_hash


def _validated_source_result_owner(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
) -> tuple[int, str]:
    row = conn.execute(
        """
        SELECT source_lane_results_json
        FROM runtime_control_candidate_truth_state
        WHERE runtime_run_id = ?
        """,
        (runtime_run_id,),
    ).fetchone()
    if row is None:
        raise RuntimeControlError("runtime_checkpoint_durable_owner_mismatch")
    try:
        results = _strict_json_object_list(row["source_lane_results_json"])
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeControlError(
            "runtime_checkpoint_durable_owner_mismatch"
        ) from exc
    return (
        len(results),
        sha256(_json(results).encode("utf-8")).hexdigest(),
    )


def _validated_round_owner(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    candidate_truth_revision: int,
) -> tuple[int, str]:
    rows = conn.execute(
        """
        SELECT *
        FROM runtime_control_round_states
        WHERE runtime_run_id = ?
        ORDER BY round_no
        """,
        (runtime_run_id,),
    ).fetchall()
    ledger: list[dict[str, object]] = []
    expected_round_no = 1
    for row in rows:
        round_no = int(row["round_no"])
        try:
            state = _strict_json_object(row["state_json"])
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeControlError(
                "runtime_checkpoint_durable_owner_mismatch"
            ) from exc
        truth_revision = int(row["candidate_truth_revision"])
        payload_hash = sha256(_json(state).encode("utf-8")).hexdigest()
        if (
            round_no != expected_round_no
            or state.get("round_no") != round_no
            or row["payload_hash"] != payload_hash
            or truth_revision > candidate_truth_revision
        ):
            raise RuntimeControlError("runtime_checkpoint_durable_owner_mismatch")
        ledger.append(
            {
                "roundNo": round_no,
                "payloadHash": payload_hash,
                "candidateTruthRevision": truth_revision,
            }
        )
        expected_round_no += 1
    return (
        len(rows),
        sha256(_json(ledger).encode("utf-8")).hexdigest(),
    )


def _validated_finalization_owner(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
) -> tuple[int, str]:
    rows = conn.execute(
        """
        SELECT *
        FROM runtime_control_candidate_finalization_revisions
        WHERE runtime_run_id = ?
        ORDER BY revision
        """,
        (runtime_run_id,),
    ).fetchall()
    ledger: list[dict[str, object]] = []
    expected_revision = 1
    for row in rows:
        revision = int(row["revision"])
        try:
            candidate_ids = _strict_json_string_list(
                row["candidate_identity_ids_json"]
            )
            coverage = _strict_json_object(row["coverage_summary_json"])
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeControlError(
                "runtime_checkpoint_durable_owner_mismatch"
            ) from exc
        payload = {
            "revision": revision,
            "reason_code": row["reason_code"],
            "candidate_identity_ids": candidate_ids,
            "coverage_summary": coverage,
        }
        payload_hash = sha256(_json(payload).encode("utf-8")).hexdigest()
        if (
            revision != expected_revision
            or row["payload_hash"] != payload_hash
        ):
            raise RuntimeControlError("runtime_checkpoint_durable_owner_mismatch")
        ledger.append(
            {
                "revision": revision,
                "payloadHash": payload_hash,
                "sourceCheckpointId": row["source_checkpoint_id"],
            }
        )
        expected_revision += 1
    return (
        len(rows),
        sha256(_json(ledger).encode("utf-8")).hexdigest(),
    )


def _checkpoint_durable_owners_match(
    conn: sqlite3.Connection,
    checkpoint: RuntimeCheckpoint,
) -> bool:
    try:
        detail_revision, current_detail_hash = _validated_detail_claim_owner(
            conn,
            runtime_run_id=checkpoint.runtime_run_id,
        )
        source_count, source_hash = _validated_source_result_owner(
            conn,
            runtime_run_id=checkpoint.runtime_run_id,
        )
        round_high_watermark, round_hash = _validated_round_owner(
            conn,
            runtime_run_id=checkpoint.runtime_run_id,
            candidate_truth_revision=checkpoint.candidate_truth_revision,
        )
        finalization_revision, finalization_hash = (
            _validated_finalization_owner(
                conn,
                runtime_run_id=checkpoint.runtime_run_id,
            )
        )
    except (RuntimeControlError, TypeError, ValueError):
        return False
    return (
        detail_revision >= checkpoint.detail_claim_revision
        and (
            (
                checkpoint.detail_claim_revision == 0
                and checkpoint.detail_claim_hash is None
            )
            or (
                checkpoint.detail_claim_revision > 0
                and checkpoint.detail_claim_hash is not None
                and (
                    detail_revision > checkpoint.detail_claim_revision
                    or current_detail_hash == checkpoint.detail_claim_hash
                )
            )
        )
        and checkpoint.durable_refs.get("sourceResultCount") == source_count
        and checkpoint.durable_refs.get("sourceResultHash") == source_hash
        and checkpoint.durable_refs.get("roundLedgerHighWatermark")
        == round_high_watermark
        and checkpoint.durable_refs.get("roundLedgerHash") == round_hash
        and checkpoint.durable_refs.get("finalizationRevision")
        == finalization_revision
        and checkpoint.durable_refs.get("finalizationLedgerHash")
        == finalization_hash
    )


def _candidate_truth_matches_checkpoint(
    conn: sqlite3.Connection,
    checkpoint: RuntimeCheckpoint,
) -> bool:
    skip_finalization = False
    if checkpoint.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V2:
        truth_row = conn.execute(
            """
            SELECT *
            FROM runtime_control_candidate_truth_state
            WHERE runtime_run_id = ?
            """,
            (checkpoint.runtime_run_id,),
        ).fetchone()
        if (
            truth_row is None
            or int(truth_row["revision"]) != checkpoint.candidate_truth_revision
            or truth_row["payload_hash"] != checkpoint.candidate_truth_hash
        ):
            return False
        if not _checkpoint_durable_owners_match(conn, checkpoint):
            return False
        expected_finalization_revision = checkpoint.durable_refs.get(
            "finalizationRevision", 0
        )
        if (
            not isinstance(expected_finalization_revision, int)
            or isinstance(expected_finalization_revision, bool)
            or expected_finalization_revision < 0
        ):
            return False
        finalization_rows = conn.execute(
            """
            SELECT *
            FROM runtime_control_candidate_finalization_revisions
            WHERE runtime_run_id = ?
            ORDER BY revision
            """,
            (checkpoint.runtime_run_id,),
        ).fetchall()
        if expected_finalization_revision == 0:
            if finalization_rows:
                return False
        else:
            try:
                if (
                    not finalization_rows
                    or int(finalization_rows[-1]["revision"])
                    != expected_finalization_revision
                    or any(
                        not _candidate_finalization_row_has_strict_shapes(row)
                        for row in finalization_rows
                    )
                ):
                    return False
            except (TypeError, ValueError):
                return False
        try:
            records = conn.execute(
                """
                SELECT *
                FROM runtime_control_candidate_records
                WHERE runtime_run_id = ?
                """,
                (checkpoint.runtime_run_id,),
            ).fetchall()
            candidate_state = {
                "candidate_store": {
                    row["resume_id"]: _strict_json_object(row["candidate_json"])
                    for row in records
                },
                "normalized_store": {
                    row["resume_id"]: _strict_json_object(row["normalized_json"])
                    for row in records
                    if row["normalized_json"] is not None
                },
                "scorecards_by_resume_id": {
                    row["resume_id"]: _strict_json_object(row["scorecard_json"])
                    for row in records
                    if row["scorecard_json"] is not None
                },
                "source_evidence_by_resume_id": _strict_json_object(
                    truth_row["source_evidence_by_resume_json"]
                ),
                "source_evidence_by_identity_id": _strict_json_object(
                    truth_row["source_evidence_by_identity_json"]
                ),
                "candidate_identity_by_resume_id": _strict_json_object(
                    truth_row["identity_by_resume_id_json"]
                ),
                "candidate_identities": _strict_json_object(
                    truth_row["identity_payloads_json"]
                ),
                "identity_aliases_by_canonical_id": _strict_json_object(
                    truth_row["aliases_json"]
                ),
                "identity_conflicts": _strict_json_object_list(
                    truth_row["conflicts_json"]
                ),
                "canonical_resume_by_identity_id": _strict_json_object(
                    truth_row["canonical_selections_json"]
                ),
            }
            if (
                candidate_truth_hash(candidate_state)
                != checkpoint.candidate_truth_hash
            ):
                return False
            truth = candidate_truth_from_run_state(
                runtime_run_id=checkpoint.runtime_run_id,
                run_state=candidate_state,
                source_checkpoint_id=(
                    f"candidate-truth:{checkpoint.runtime_run_id}:"
                    f"{checkpoint.candidate_truth_revision}"
                ),
                observed_at=truth_row["updated_at"],
            )
            skip_finalization = True
        except (
            json.JSONDecodeError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            return False
    else:
        try:
            truth = candidate_truth_from_run_state(
                runtime_run_id=checkpoint.runtime_run_id,
                run_state=checkpoint.run_state,
                source_checkpoint_id=checkpoint.checkpoint_id,
                observed_at=checkpoint.created_at,
            )
        except (TypeError, ValueError, ValidationError):
            return False
    expected_identity_ids = {identity.identity_id for identity in truth.identities}
    expected_evidence_ids = {evidence.evidence_id for evidence in truth.evidence}
    expected_revisions = (
        set()
        if skip_finalization
        else {revision.revision for revision in truth.finalization_revisions}
    )
    stored_identity_ids = {
        row["identity_id"]
        for row in conn.execute(
            "SELECT identity_id FROM runtime_control_candidate_identities WHERE runtime_run_id = ?",
            (checkpoint.runtime_run_id,),
        ).fetchall()
    }
    stored_evidence_ids = {
        row["evidence_id"]
        for row in conn.execute(
            "SELECT evidence_id FROM runtime_control_candidate_evidence WHERE runtime_run_id = ?",
            (checkpoint.runtime_run_id,),
        ).fetchall()
    }
    stored_revisions = {
        row["revision"]
        for row in conn.execute(
            """
            SELECT revision
            FROM runtime_control_candidate_finalization_revisions
            WHERE runtime_run_id = ?
            """,
            (checkpoint.runtime_run_id,),
        ).fetchall()
    } if not skip_finalization else set()
    if (
        stored_identity_ids != expected_identity_ids
        or stored_evidence_ids != expected_evidence_ids
        or stored_revisions != expected_revisions
    ):
        return False
    for identity in truth.identities:
        row = conn.execute(
            """
            SELECT *
            FROM runtime_control_candidate_identities
            WHERE runtime_run_id = ? AND identity_id = ?
            """,
            (identity.runtime_run_id, identity.identity_id),
        ).fetchone()
        if row is None or not _candidate_identity_row_has_strict_shapes(row):
            return False
        try:
            stored_identity = _candidate_identity_from_row(row)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError, IndexError, KeyError):
            return False
        if stored_identity != identity:
            return False
    for evidence in truth.evidence:
        row = conn.execute(
            """
            SELECT *
            FROM runtime_control_candidate_evidence
            WHERE runtime_run_id = ? AND evidence_id = ?
            """,
            (evidence.runtime_run_id, evidence.evidence_id),
        ).fetchone()
        if row is None or not _candidate_evidence_row_has_strict_shapes(row):
            return False
        try:
            stored_evidence = _candidate_evidence_from_row(row)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError, IndexError, KeyError):
            return False
        if stored_evidence != evidence:
            return False
    for revision in (() if skip_finalization else truth.finalization_revisions):
        row = conn.execute(
            """
            SELECT *
            FROM runtime_control_candidate_finalization_revisions
            WHERE runtime_run_id = ? AND revision = ?
            """,
            (revision.runtime_run_id, revision.revision),
        ).fetchone()
        if row is None or not _candidate_finalization_row_has_strict_shapes(row):
            return False
        try:
            stored_revision = _candidate_finalization_revision_from_row(row)
        except (json.JSONDecodeError, TypeError, ValueError, ValidationError, IndexError, KeyError):
            return False
        if stored_revision != revision or stored_revision.source_checkpoint_id != checkpoint.checkpoint_id:
            return False
    return True


def _append_recovery_expiry_event(
    conn: sqlite3.Connection,
    *,
    lease_row: sqlite3.Row,
    run_row: sqlite3.Row,
    now: str,
) -> RuntimeControlEvent:
    legacy_event = _matching_legacy_expiry_event(conn, lease_row=lease_row)
    if legacy_event is not None:
        return legacy_event
    return _append_event_in_transaction(
        conn,
        RuntimeControlEventInput(
            event_id=_recovery_event_id(lease_row["lease_id"], "lease-expired"),
            runtime_run_id=run_row["runtime_run_id"],
            event_type="runtime_executor_lease_expired",
            stage=run_row["current_stage"],
            round_no=run_row["current_round"],
            source_id=None,
            status="failed",
            summary="executor lease expired",
            payload={
                "leaseId": lease_row["lease_id"],
                "executorId": lease_row["executor_id"],
                "attemptNo": lease_row["attempt_no"],
            },
            visibility="developer",
            idempotency_key=f"runtime-recovery:{lease_row['lease_id']}:lease-expired",
            created_at=now,
        ),
        snapshot=None,
        run_status=None,
        stop_reason_code=None,
        completed_at=None,
        latest_checkpoint_id=None,
    )


def _append_recovery_decision_event(
    conn: sqlite3.Connection,
    *,
    lease_row: sqlite3.Row,
    run_row: sqlite3.Row,
    checkpoint: RuntimeCheckpoint | RuntimeCheckpointLoadFailure | None,
    plan: RuntimeRecoveryPlan,
    after_event_seq: int,
    now: str,
) -> None:
    paired_event = _paired_legacy_decision_event(
        conn,
        lease_row=lease_row,
        after_event_seq=after_event_seq,
    )
    if paired_event is not None:
        if (
            paired_event.event_type == plan.event_type
            and paired_event.status == plan.event_status
            and _legacy_decision_payload_matches(
                paired_event.payload,
                lease_row=lease_row,
                plan=plan,
            )
        ):
            return
    payload: dict[str, object] = {
        "reasonCode": plan.reason_code,
        "leaseId": lease_row["lease_id"],
        "executorId": lease_row["executor_id"],
        "attemptNo": lease_row["attempt_no"],
    }
    if plan.checkpoint_id is not None:
        payload["checkpointId"] = plan.checkpoint_id
    event_stage = (
        checkpoint.stage
        if isinstance(checkpoint, RuntimeCheckpoint) and plan.target_status == "resume_requested"
        else run_row["current_stage"]
    )
    event_round = (
        checkpoint.round_no
        if isinstance(checkpoint, RuntimeCheckpoint) and plan.target_status == "resume_requested"
        else run_row["current_round"]
    )
    _append_event_in_transaction(
        conn,
        RuntimeControlEventInput(
            event_id=_recovery_event_id(lease_row["lease_id"], "decision"),
            runtime_run_id=run_row["runtime_run_id"],
            event_type=plan.event_type,
            stage=event_stage,
            round_no=event_round,
            source_id=None,
            status=plan.event_status,
            summary=plan.summary,
            payload=payload,
            visibility="developer",
            idempotency_key=f"runtime-recovery:{lease_row['lease_id']}:decision",
            created_at=now,
        ),
        snapshot=None,
        run_status=None,
        stop_reason_code=None,
        completed_at=None,
        latest_checkpoint_id=None,
    )


def _matching_legacy_expiry_event(
    conn: sqlite3.Connection,
    *,
    lease_row: sqlite3.Row,
) -> RuntimeControlEvent | None:
    rows = conn.execute(
        """
        SELECT *
        FROM runtime_control_events
        WHERE runtime_run_id = ? AND event_type = 'runtime_executor_lease_expired'
        ORDER BY event_seq DESC
        """,
        (lease_row["runtime_run_id"],),
    ).fetchall()
    for row in rows:
        payload = _recovery_event_payload(row["payload_json"])
        if payload is None:
            continue
        if (
            payload.get("executorId") == lease_row["executor_id"]
            and payload.get("attemptNo") == lease_row["attempt_no"]
        ):
            return _event_from_row(row)
    return None


def _paired_legacy_decision_event(
    conn: sqlite3.Connection,
    *,
    lease_row: sqlite3.Row,
    after_event_seq: int,
) -> RuntimeControlEvent | None:
    rows = conn.execute(
        """
        SELECT decision.*
        FROM runtime_control_events AS decision
        WHERE decision.runtime_run_id = ?
          AND decision.event_seq > ?
          AND decision.event_seq < COALESCE(
            (
              SELECT MIN(boundary.event_seq)
              FROM runtime_control_events AS boundary
              WHERE boundary.runtime_run_id = decision.runtime_run_id
                AND boundary.event_type = 'runtime_executor_lease_expired'
                AND boundary.event_seq > ?
            ),
            9223372036854775807
          )
          AND decision.event_type IN (
            'runtime_executor_crashed',
            'runtime_executor_start_failed',
            'runtime_run_cancelled',
            'runtime_checkpoint_restore_failed',
            'runtime_checkpoint_restored'
          )
          AND decision.idempotency_key IS NULL
        ORDER BY decision.event_seq ASC
        """,
        (lease_row["runtime_run_id"], after_event_seq, after_event_seq),
    ).fetchall()
    for row in rows:
        payload = _recovery_event_payload(row["payload_json"])
        if payload is not None and _legacy_decision_event_belongs_to_lease(
            row["event_type"],
            payload,
            lease_row=lease_row,
        ):
            return _event_from_row(row)
    return None


def _legacy_decision_event_belongs_to_lease(
    event_type: str,
    payload: dict[str, object],
    *,
    lease_row: sqlite3.Row,
) -> bool:
    for key, expected in (
        ("leaseId", lease_row["lease_id"]),
        ("attemptNo", lease_row["attempt_no"]),
    ):
        if key in payload and payload[key] != expected:
            return False
    executor_id = payload.get("executorId")
    if event_type in {
        "runtime_executor_crashed",
        "runtime_executor_start_failed",
        "runtime_run_cancelled",
    }:
        return executor_id == lease_row["executor_id"]
    return executor_id is None or executor_id == lease_row["executor_id"]


def _legacy_decision_payload_matches(
    payload: dict[str, object],
    *,
    lease_row: sqlite3.Row,
    plan: RuntimeRecoveryPlan,
) -> bool:
    if not _legacy_decision_event_belongs_to_lease(
        plan.event_type,
        payload,
        lease_row=lease_row,
    ):
        return False
    reason_code = payload.get("reasonCode")
    if plan.event_type == "runtime_checkpoint_restored":
        if reason_code is not None and reason_code != plan.reason_code:
            return False
    elif reason_code != plan.reason_code:
        return False
    if plan.checkpoint_id is None:
        return "checkpointId" not in payload
    return payload.get("checkpointId") == plan.checkpoint_id


def _recovery_event_payload(value: str) -> dict[str, object] | None:
    try:
        return _json_object(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _recovery_event_id(lease_id: str, kind: str) -> str:
    digest = sha256(f"{lease_id}:{kind}".encode()).hexdigest()[:24]
    return f"rtevt_recovery_{digest}"


def _inject_recovery_fault(
    fault_injector: Callable[[str], None] | None,
    point: str,
) -> None:
    if fault_injector is not None:
        fault_injector(point)


def _inject_source_operation_fault(fault_injector: Callable[[str], None] | None, point: str) -> None:
    if fault_injector is not None:
        fault_injector(point)


def _inject_source_reconciliation_fault(
    fault_injector: Callable[[str], None] | None,
    point: str,
) -> None:
    if fault_injector is not None:
        fault_injector(point)


def _run_row(conn: sqlite3.Connection, runtime_run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (runtime_run_id,),
    ).fetchone()


def _source_operation_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    operation_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_operations
        WHERE runtime_run_id = ? AND operation_id = ?
        """,
        (runtime_run_id, operation_id),
    ).fetchone()


def _source_operation_row_by_idempotency(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_operations
        WHERE runtime_run_id = ? AND idempotency_key = ?
        """,
        (runtime_run_id, idempotency_key),
    ).fetchone()


def _source_operation_admission_expectation_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    operation_id: str,
    dispatch_authorization_ordinal: int = 1,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_operation_admission_expectations
        WHERE runtime_run_id = ? AND operation_id = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (
            runtime_run_id,
            operation_id,
            dispatch_authorization_ordinal,
        ),
    ).fetchone()


def _source_reconciliation_row(
    conn: sqlite3.Connection,
    reconciliation_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_reconciliations
        WHERE reconciliation_id = ?
        """,
        (reconciliation_id,),
    ).fetchone()


def _source_operation_matches_reconciliation(
    operation: SourceOperationRecord,
    decision: SourceOperationReconciliationDecision,
) -> bool:
    return (
        operation.runtime_run_id == decision.runtime_run_id
        and operation.operation_id == decision.operation_id
        and operation.source_id == decision.source_id
        and operation.operation_kind == decision.operation_kind
        and operation.canonical_request_hash == decision.canonical_request_hash
        and operation.idempotency_key == decision.idempotency_key
        and operation.accepted_requirement_revision_id == decision.accepted_requirement_revision_id
        and operation.runtime_attempt_no == decision.runtime_attempt_no
        and operation.runtime_attempt_authority_ref == decision.runtime_attempt_authority_ref
    )


def _require_source_reconciliation_transition(
    operation: SourceOperationRecord,
    decision: SourceOperationReconciliationDecision,
) -> None:
    if operation.retry_posture == "safe_retry":
        raise RuntimeControlError("source_reconciliation_transition_conflict")
    if (
        operation.retry_posture == "no_retry"
        and operation.conclusive_observation_ref is not None
        and operation.source_operation_disposition is not None
    ):
        raise RuntimeControlError("source_reconciliation_transition_conflict")
    if (
        operation.dispatch_intent_ref is not None
        and operation.dispatch_intent_ref != decision.dispatch_intent_ref
    ):
        raise RuntimeControlError("source_reconciliation_transition_conflict")
    if (
        operation.conclusive_observation_ref is not None
        and operation.conclusive_observation_ref != decision.conclusive_observation_ref
    ):
        raise RuntimeControlError("source_reconciliation_transition_conflict")

    current_disposition = operation.source_operation_disposition
    target_disposition = decision.source_operation_disposition
    if current_disposition not in {None, "reconciliation_unknown", target_disposition}:
        raise RuntimeControlError("source_reconciliation_transition_conflict")

    if decision.decision_kind == "no_dispatch_proved":
        if operation.dispatch_intent_ref is not None or operation.conclusive_observation_ref is not None:
            raise RuntimeControlError("source_reconciliation_transition_conflict")
        if current_disposition != target_disposition:
            raise RuntimeControlError("source_reconciliation_transition_conflict")
    elif decision.decision_kind == "unresolved":
        if operation.conclusive_observation_ref is not None:
            raise RuntimeControlError("source_reconciliation_transition_conflict")
        if (
            decision.history_outcome == "history_unavailable"
            and decision.dispatch_intent_ref != operation.dispatch_intent_ref
        ):
            raise RuntimeControlError("source_reconciliation_transition_conflict")
        if current_disposition not in {None, "reconciliation_unknown"}:
            raise RuntimeControlError("source_reconciliation_transition_conflict")
    elif decision.decision_kind == "conclusive_observation":
        if current_disposition not in {None, "reconciliation_unknown", target_disposition}:
            raise RuntimeControlError("source_reconciliation_transition_conflict")
    else:
        raise RuntimeControlError("source_reconciliation_decision_kind_invalid")


def _expired_browser_lane_reconciliation_matches(
    conn: sqlite3.Connection,
    *,
    run_row: sqlite3.Row,
    decision: SourceOperationReconciliationDecision,
    fencing_token: int | None,
) -> bool:
    if (
        type(fencing_token) is not int
        or fencing_token < 1
        or run_row["status"] != "needs_attention"
        or _needs_admission.run_has_active_executor_lease(
            conn,
            decision.runtime_run_id,
        )
    ):
        return False
    lane = conn.execute(
        """
        SELECT runtime_run_id, operation_id, fencing_token,
               status, lease_expires_at
        FROM runtime_control_browser_lanes
        WHERE lane_key = 'liepin_browser'
        """
    ).fetchone()
    return (
        lane is not None
        and lane["status"] == "active"
        and int(lane["fencing_token"]) == fencing_token
        and lane["runtime_run_id"] == decision.runtime_run_id
        and lane["operation_id"] == decision.operation_id
        and lane["lease_expires_at"] is not None
        and lane["lease_expires_at"] <= decision.committed_at
    )


def _source_operation_pair(
    conn: sqlite3.Connection, operation_row: sqlite3.Row
) -> tuple[SourceOperationRecord, SourceDispatchMetadata]:
    operation = source_operation_from_row(operation_row)
    dispatch_row = _latest_source_dispatch_row(conn, operation.runtime_run_id, operation.operation_id)
    if dispatch_row is None or _run_row(conn, operation.runtime_run_id) is None:
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    dispatch = source_dispatch_from_row(dispatch_row)
    if not dispatch_matches_operation(dispatch, operation):
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    return operation, dispatch


def _source_operation_acceptance(conn: sqlite3.Connection, operation_row: sqlite3.Row) -> AcceptedSourceOperation:
    operation, dispatch = _source_operation_pair(conn, operation_row)
    expectation_row = _source_operation_admission_expectation_row(
        conn, operation.runtime_run_id, operation.operation_id, dispatch.dispatch_authorization_ordinal
    )
    if expectation_row is None:
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    expectation = _source_operation_admission_expectation_from_row(expectation_row)
    if (
        not expectation_matches_operation(expectation, operation)
        or expectation.dispatch_authorization_ordinal != dispatch.dispatch_authorization_ordinal
    ):
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    if dispatch.dispatch_authorization_ordinal == 1:
        if expectation.runtime_attempt_no != operation.runtime_attempt_no or (
            expectation.runtime_attempt_authority_ref != operation.runtime_attempt_authority_ref
        ):
            raise RuntimeControlError("source_operation_acceptance_incomplete")
    else:
        _require_safe_retry_dispatch_authorization(operation=operation, expectation=expectation, dispatch=dispatch)
    return AcceptedSourceOperation(operation=operation, expectation=expectation, dispatch=dispatch)


def _require_source_dispatch_operation(
    conn: sqlite3.Connection,
    dispatch: SourceDispatchMetadata,
) -> SourceOperationRecord:
    if _run_row(conn, dispatch.runtime_run_id) is None:
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    operation_row = _source_operation_row(conn, dispatch.runtime_run_id, dispatch.operation_id)
    if operation_row is None:
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    operation = source_operation_from_row(operation_row)
    expectation_row = _source_operation_admission_expectation_row(
        conn,
        operation.runtime_run_id,
        operation.operation_id,
        dispatch.dispatch_authorization_ordinal,
    )
    if expectation_row is None:
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    expectation = _source_operation_admission_expectation_from_row(expectation_row)
    if not expectation_matches_operation(expectation, operation):
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    if not dispatch_matches_operation(dispatch, operation):
        raise RuntimeControlError("source_operation_acceptance_incomplete")
    if dispatch.dispatch_authorization_ordinal == 1:
        if (
            expectation.runtime_attempt_no != operation.runtime_attempt_no
            or expectation.runtime_attempt_authority_ref != operation.runtime_attempt_authority_ref
        ):
            raise RuntimeControlError("source_operation_acceptance_incomplete")
    else:
        _require_safe_retry_dispatch_authorization(
            operation=operation,
            expectation=expectation,
            dispatch=dispatch,
        )
    return operation


def _source_operation_admission_expectation_from_row(row: sqlite3.Row) -> SourceOperationAdmissionExpectation:
    try:
        expectation = source_operation_admission_expectation_from_row(row)
        validate_source_operation_admission_expectation(
            runtime_run_id=expectation.runtime_run_id,
            operation_id=expectation.operation_id,
            dispatch_authorization_ordinal=(expectation.dispatch_authorization_ordinal),
            runtime_attempt_no=expectation.runtime_attempt_no,
            runtime_attempt_authority_ref=(expectation.runtime_attempt_authority_ref),
            runtime_attempt_fence_ref=expectation.runtime_attempt_fence_ref,
            profile_binding_generation=expectation.profile_binding_generation,
            browser_control_scope_id=expectation.browser_control_scope_id,
            controller_fence_ref=expectation.controller_fence_ref,
        )
    except (RuntimeControlError, TypeError, ValueError):
        raise RuntimeControlError("source_operation_acceptance_incomplete") from None
    return expectation


def _source_dispatch_row_for_operation(
    conn: sqlite3.Connection, runtime_run_id: str, operation_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ? AND dispatch_authorization_ordinal = 1
        """,
        (runtime_run_id, operation_id),
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
    claimed_at: str,
) -> sqlite3.Row | None:
    clauses = ["run.status IN ('queued', 'resume_requested')"]
    params: list[object] = []
    if runtime_run_id is not None:
        clauses.append("run.runtime_run_id = ?")
        params.append(runtime_run_id)
    params.append(claimed_at)
    return conn.execute(
        f"""
        SELECT run.*
        FROM runtime_control_runs AS run
        {RUN_ACCEPTANCE_JOINS}
        WHERE {' AND '.join(clauses)}
          AND run.latest_event_seq > 0
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_executor_leases AS lease
            WHERE lease.runtime_run_id = run.runtime_run_id
              AND lease.status = 'active'
          )
          AND (
            run.status != 'resume_requested'
            OR NOT EXISTS (
              SELECT 1
              FROM runtime_control_source_operations AS source_operation
              WHERE source_operation.runtime_run_id = run.runtime_run_id
                AND source_operation.retry_posture = 'reconcile_first'
            )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM runtime_control_events AS latest_event
            WHERE latest_event.runtime_run_id = run.runtime_run_id
              AND latest_event.event_seq = run.latest_event_seq
              AND latest_event.event_type = 'runtime_resource_waiting'
              AND (
                julianday(?) - julianday(latest_event.created_at)
              ) * 86400.0 < 0.5
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
          AND run.product_outcome IS NULL
          AND run.current_failure_id IS NULL
          AND run.current_failure_revision IS NULL
          AND run.current_failure_owner_lease_id IS NULL
          AND run.current_failure_authority_mode IS NULL
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


def _checkpoint_count_and_bytes(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COALESCE(SUM(
            length(checkpoint_id)
            + length(runtime_run_id)
            + length(stage)
            + length(safe_boundary)
            + length(run_state_json)
            + length(source_plan_json)
            + length(pending_commands_json)
            + COALESCE(length(artifact_manifest_ref), 0)
            + length(schema_version)
            + length(created_at)
            + COALESCE(length(control_state_hash), 0)
            + COALESCE(length(candidate_truth_hash), 0)
            + COALESCE(length(detail_claim_hash), 0)
            + length(durable_refs_json)
            + length(field_bytes_json)
          ), 0) AS estimated_bytes
        FROM runtime_control_checkpoints
        WHERE runtime_run_id = ?
        """,
        (runtime_run_id,),
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
          AND run.product_outcome IS NULL
          AND run.current_failure_id IS NULL
          AND run.current_failure_revision IS NULL
          AND run.current_failure_owner_lease_id IS NULL
          AND run.current_failure_authority_mode IS NULL
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
          AND run.product_outcome IS NULL
          AND run.current_failure_id IS NULL
          AND run.current_failure_revision IS NULL
          AND run.current_failure_owner_lease_id IS NULL
          AND run.current_failure_authority_mode IS NULL
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
          AND run.product_outcome IS NULL
          AND run.current_failure_id IS NULL
          AND run.current_failure_revision IS NULL
          AND run.current_failure_owner_lease_id IS NULL
          AND run.current_failure_authority_mode IS NULL
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
          AND product_outcome IS NULL
          AND current_failure_id IS NULL
          AND current_failure_revision IS NULL
          AND current_failure_owner_lease_id IS NULL
          AND current_failure_authority_mode IS NULL
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


def _sync_candidate_truth_v2(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    candidate_state: dict[str, object],
    source_lane_results: list[dict[str, object]],
    created_at: str,
) -> tuple[int, str]:
    payload_hash = candidate_truth_hash(candidate_state)
    current = conn.execute(
        """
        SELECT revision, payload_hash, source_lane_results_json
        FROM runtime_control_candidate_truth_state
        WHERE runtime_run_id = ?
        """,
        (runtime_run_id,),
    ).fetchone()
    source_lane_results_json = _json(source_lane_results)
    if (
        current is not None
        and current["payload_hash"] == payload_hash
        and current["source_lane_results_json"] == source_lane_results_json
    ):
        return int(current["revision"]), payload_hash
    revision = 1 if current is None else int(current["revision"]) + 1

    projection_checkpoint = RuntimeCheckpoint(
        checkpoint_id=f"candidate-truth:{runtime_run_id}:{revision}",
        runtime_run_id=runtime_run_id,
        stage="candidate_truth",
        safe_boundary="runtime_candidate_checkpoint",
        run_state=candidate_state,
        source_plan={},
        pending_commands=[],
        schema_version=RUNTIME_CHECKPOINT_SCHEMA_V2,
        created_at=created_at,
    )
    projected_truth = candidate_truth_from_run_state(
        runtime_run_id=runtime_run_id,
        run_state=candidate_state,
        source_checkpoint_id=projection_checkpoint.checkpoint_id,
        observed_at=created_at,
    )
    _sync_candidate_truth_from_checkpoint(conn, projection_checkpoint)
    identity_ids = [item.identity_id for item in projected_truth.identities]
    evidence_ids = [item.evidence_id for item in projected_truth.evidence]
    _delete_absent_candidate_rows(
        conn,
        table="runtime_control_candidate_identities",
        key_column="identity_id",
        runtime_run_id=runtime_run_id,
        keys=identity_ids,
    )
    _delete_absent_candidate_rows(
        conn,
        table="runtime_control_candidate_evidence",
        key_column="evidence_id",
        runtime_run_id=runtime_run_id,
        keys=evidence_ids,
    )

    candidate_store = _mapping(candidate_state.get("candidate_store"))
    normalized_store = _mapping(candidate_state.get("normalized_store"))
    scorecards = _mapping(candidate_state.get("scorecards_by_resume_id"))
    identity_by_resume_id = _mapping(candidate_state.get("candidate_identity_by_resume_id"))
    resume_ids = sorted(
        set(candidate_store)
        | set(normalized_store)
        | set(scorecards)
        | set(identity_by_resume_id)
    )
    for resume_id in resume_ids:
        candidate = _string_key_dict(candidate_store.get(resume_id))
        normalized = _string_key_dict(normalized_store.get(resume_id))
        scorecard = _string_key_dict(scorecards.get(resume_id))
        record_payload = {
            "candidate": candidate,
            "normalized": normalized,
            "scorecard": scorecard,
            "identityId": identity_by_resume_id.get(resume_id),
        }
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_records (
                runtime_run_id, resume_id, identity_id, candidate_json,
                normalized_json, scorecard_json, payload_hash,
                truth_revision, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime_run_id, resume_id) DO UPDATE SET
                identity_id = excluded.identity_id,
                candidate_json = excluded.candidate_json,
                normalized_json = excluded.normalized_json,
                scorecard_json = excluded.scorecard_json,
                payload_hash = excluded.payload_hash,
                truth_revision = excluded.truth_revision,
                updated_at = excluded.updated_at
            """,
            (
                runtime_run_id,
                resume_id,
                identity_by_resume_id.get(resume_id),
                _json(candidate),
                (
                    _json(normalized)
                    if resume_id in normalized_store
                    else None
                ),
                _json(scorecard) if resume_id in scorecards else None,
                sha256(_json(record_payload).encode("utf-8")).hexdigest(),
                revision,
                created_at,
            ),
        )
    if resume_ids:
        placeholders = ",".join("?" for _ in resume_ids)
        conn.execute(
            f"""
            DELETE FROM runtime_control_candidate_records
            WHERE runtime_run_id = ? AND resume_id NOT IN ({placeholders})
            """,
            (runtime_run_id, *resume_ids),
        )
    else:
        conn.execute(
            "DELETE FROM runtime_control_candidate_records WHERE runtime_run_id = ?",
            (runtime_run_id,),
        )

    conn.execute(
        """
        INSERT INTO runtime_control_candidate_truth_state (
            runtime_run_id, revision, payload_hash, identity_payloads_json,
            identity_by_resume_id_json, aliases_json, conflicts_json,
            canonical_selections_json, source_evidence_by_resume_json,
            source_evidence_by_identity_json, source_lane_results_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(runtime_run_id) DO UPDATE SET
            revision = excluded.revision,
            payload_hash = excluded.payload_hash,
            identity_payloads_json = excluded.identity_payloads_json,
            identity_by_resume_id_json = excluded.identity_by_resume_id_json,
            aliases_json = excluded.aliases_json,
            conflicts_json = excluded.conflicts_json,
            canonical_selections_json = excluded.canonical_selections_json,
            source_evidence_by_resume_json = excluded.source_evidence_by_resume_json,
            source_evidence_by_identity_json = excluded.source_evidence_by_identity_json,
            source_lane_results_json = excluded.source_lane_results_json,
            updated_at = excluded.updated_at
        """,
        (
            runtime_run_id,
            revision,
            payload_hash,
            _json(_mapping(candidate_state.get("candidate_identities"))),
            _json(identity_by_resume_id),
            _json(_mapping(candidate_state.get("identity_aliases_by_canonical_id"))),
            _json(_object_list(candidate_state.get("identity_conflicts"))),
            _json(_mapping(candidate_state.get("canonical_resume_by_identity_id"))),
            _json(_mapping(candidate_state.get("source_evidence_by_resume_id"))),
            _json(_mapping(candidate_state.get("source_evidence_by_identity_id"))),
            source_lane_results_json,
            created_at,
        ),
    )
    return revision, payload_hash


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [_mapping(item) for item in value if isinstance(item, dict)]


def _object_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _mapping_int_max(
    values: list[dict[str, object]],
    *,
    key: str,
    default: int,
) -> int:
    items = [
        value
        for item in values
        if isinstance((value := item.get(key)), int)
        and not isinstance(value, bool)
    ]
    return max(items, default=default)


def _nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeControlError("runtime_detail_claim_snapshot_invalid")
    return value


def _delete_absent_candidate_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    runtime_run_id: str,
    keys: list[str],
) -> None:
    if keys:
        placeholders = ",".join("?" for _ in keys)
        conn.execute(
            f"DELETE FROM {table} WHERE runtime_run_id = ? "
            f"AND {key_column} NOT IN ({placeholders})",
            (runtime_run_id, *keys),
        )
        return
    conn.execute(f"DELETE FROM {table} WHERE runtime_run_id = ?", (runtime_run_id,))


def _sync_round_states_v2(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    round_states: list[dict[str, object]],
    candidate_truth_revision: int,
    created_at: str,
) -> None:
    round_numbers: list[int] = []
    for raw_state in round_states:
        round_no = raw_state.get("round_no")
        if not isinstance(round_no, int) or isinstance(round_no, bool) or round_no < 1:
            raise RuntimeControlError("runtime_checkpoint_round_state_invalid")
        compact = compact_round_state(raw_state)
        payload_json = _json(compact)
        conn.execute(
            """
            INSERT INTO runtime_control_round_states (
                runtime_run_id, round_no, state_json, payload_hash,
                candidate_truth_revision, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime_run_id, round_no) DO UPDATE SET
                state_json = excluded.state_json,
                payload_hash = excluded.payload_hash,
                candidate_truth_revision = excluded.candidate_truth_revision,
                updated_at = excluded.updated_at
            """,
            (
                runtime_run_id,
                round_no,
                payload_json,
                sha256(payload_json.encode("utf-8")).hexdigest(),
                candidate_truth_revision,
                created_at,
            ),
        )
        round_numbers.append(round_no)
    if round_numbers:
        placeholders = ",".join("?" for _ in round_numbers)
        conn.execute(
            f"""
            DELETE FROM runtime_control_round_states
            WHERE runtime_run_id = ? AND round_no NOT IN ({placeholders})
            """,
            (runtime_run_id, *round_numbers),
        )
    else:
        conn.execute(
            "DELETE FROM runtime_control_round_states WHERE runtime_run_id = ?",
            (runtime_run_id,),
        )


def _sync_finalization_revisions_v2(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    candidate_state: dict[str, object],
    finalization_revisions: list[dict[str, object]],
    checkpoint_id: str,
    created_at: str,
) -> None:
    if not finalization_revisions:
        return
    run_state = dict(candidate_state)
    run_state["finalization_revisions"] = finalization_revisions
    truth = candidate_truth_from_run_state(
        runtime_run_id=runtime_run_id,
        run_state=run_state,
        source_checkpoint_id=checkpoint_id,
        observed_at=created_at,
    )
    for revision in truth.finalization_revisions:
        conn.execute(
            """
            INSERT INTO runtime_control_candidate_finalization_revisions (
                runtime_run_id, revision, reason_code, candidate_identity_ids_json,
                coverage_summary_json, source_checkpoint_id, payload_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime_run_id, revision) DO NOTHING
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
        stored = conn.execute(
            """
            SELECT payload_hash
            FROM runtime_control_candidate_finalization_revisions
            WHERE runtime_run_id = ? AND revision = ?
            """,
            (revision.runtime_run_id, revision.revision),
        ).fetchone()
        if stored is None or stored["payload_hash"] != revision.payload_hash:
            raise RuntimeControlError(
                "runtime_finalization_revision_conflict"
            )


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
        product_outcome=row["product_outcome"],
        current_failure_id=row["current_failure_id"],
        current_failure_revision=row["current_failure_revision"],
        current_failure_owner_lease_id=row[
            "current_failure_owner_lease_id"
        ],
        current_failure_authority_mode=row[
            "current_failure_authority_mode"
        ],
        current_action_id=row["current_action_id"],
        state_revision=int(row["state_revision"]),
    )


def _validated_run_from_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> RuntimeRunRecord:
    _needs_attention.validate_needs_attention_row(conn, row)
    validate_failed_outcome_row(conn, row)
    return _run_from_row(row)


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
        state_revision=int(row["state_revision"]),
        accepted_requirement_revision_id=row["accepted_requirement_revision_id"],
        control_state_hash=row["control_state_hash"],
        candidate_truth_revision=int(row["candidate_truth_revision"]),
        candidate_truth_hash=row["candidate_truth_hash"],
        detail_claim_revision=int(row["detail_claim_revision"]),
        detail_claim_hash=row["detail_claim_hash"],
        durable_refs=_json_object(row["durable_refs_json"]),
        field_bytes={
            key: int(value)
            for key, value in _json_object(row["field_bytes_json"]).items()
            if isinstance(value, int)
        },
        serialization_latency_ms=float(row["serialization_latency_ms"]),
        projection_latency_ms=float(row["projection_latency_ms"]),
        payload_size_bytes=int(row["payload_size_bytes"]),
        is_final_manifest=bool(row["is_final_manifest"]),
    )


def _recoverable_checkpoint_from_row_or_failure(
    row: sqlite3.Row,
) -> RuntimeCheckpoint | RuntimeCheckpointLoadFailure:
    checkpoint_id = row["checkpoint_id"]
    if row["schema_version"] not in {
        RUNTIME_CHECKPOINT_SCHEMA_V1,
        RUNTIME_CHECKPOINT_SCHEMA_V2,
    }:
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=checkpoint_id,
            reason_code=RUNTIME_CHECKPOINT_SCHEMA_UNSUPPORTED,
        )
    try:
        run_state = _strict_json_object(row["run_state_json"])
        source_plan = _strict_json_object(row["source_plan_json"])
        pending_commands = _strict_json_object_list(row["pending_commands_json"])
        checkpoint = RuntimeCheckpoint(
            checkpoint_id=checkpoint_id,
            runtime_run_id=row["runtime_run_id"],
            stage=row["stage"],
            round_no=row["round_no"],
            safe_boundary=row["safe_boundary"],
            run_state=run_state,
            source_plan=source_plan,
            pending_commands=pending_commands,
            artifact_manifest_ref=row["artifact_manifest_ref"],
            schema_version=row["schema_version"],
            created_at=row["created_at"],
            state_revision=int(row["state_revision"]),
            accepted_requirement_revision_id=row[
                "accepted_requirement_revision_id"
            ],
            control_state_hash=row["control_state_hash"],
            candidate_truth_revision=int(row["candidate_truth_revision"]),
            candidate_truth_hash=row["candidate_truth_hash"],
            detail_claim_revision=int(row["detail_claim_revision"]),
            detail_claim_hash=row["detail_claim_hash"],
            durable_refs=_strict_json_object(row["durable_refs_json"]),
            field_bytes={
                key: int(value)
                for key, value in _strict_json_object(
                    row["field_bytes_json"]
                ).items()
                if isinstance(value, int)
            },
            serialization_latency_ms=float(row["serialization_latency_ms"]),
            projection_latency_ms=float(row["projection_latency_ms"]),
            payload_size_bytes=int(row["payload_size_bytes"]),
            is_final_manifest=bool(row["is_final_manifest"]),
        )
        if checkpoint.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V2:
            if (
                checkpoint.control_state_hash
                != sha256(_json(checkpoint.run_state).encode("utf-8")).hexdigest()
                or checkpoint.accepted_requirement_revision_id is None
                or checkpoint.candidate_truth_revision < 1
                or checkpoint.candidate_truth_hash is None
            ):
                raise ValueError("runtime_checkpoint_v2_binding_invalid")
        return checkpoint
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return RuntimeCheckpointLoadFailure(
            checkpoint_id=checkpoint_id,
            reason_code=RUNTIME_CHECKPOINT_CORRUPT,
        )


def _strict_json_object(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("runtime_checkpoint_json_object_required")
    return payload


def _strict_json_object_list(value: str) -> list[dict[str, object]]:
    payload = json.loads(value)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("runtime_checkpoint_json_object_list_required")
    return payload


def _strict_json_string_list(value: str) -> list[str]:
    payload = json.loads(value)
    if not isinstance(payload, list) or not all(
        isinstance(item, str) and item for item in payload
    ):
        raise ValueError("runtime_json_string_list_required")
    return payload


def _strict_run_source_ids(value: str) -> tuple[tuple[str, ...], bool]:
    try:
        return tuple(_strict_json_string_list(value)), True
    except (json.JSONDecodeError, TypeError, ValueError):
        return (), False


def _candidate_identity_row_has_strict_shapes(row: sqlite3.Row) -> bool:
    try:
        for column in (
            "merged_resume_ids_json",
            "source_evidence_ids_json",
            "equivalent_latest_resume_ids_json",
            "display_source_evidence_ids_json",
            "conflicting_resume_ids_json",
            "incomparable_resume_ids_json",
            "safe_reason_codes_json",
        ):
            _strict_json_string_list(row[column])
    except (json.JSONDecodeError, TypeError, ValueError, IndexError, KeyError):
        return False
    return True


def _candidate_evidence_row_has_strict_shapes(row: sqlite3.Row) -> bool:
    try:
        source_references = _strict_json_object_list(row["source_references_json"])
        _strict_json_object(row["payload_json"])
        for raw_reference in source_references:
            reference = SourceReference.model_validate(raw_reference)
            if reference.model_dump(mode="json") != raw_reference:
                return False
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
        IndexError,
        KeyError,
    ):
        return False
    return True


def _candidate_finalization_row_has_strict_shapes(row: sqlite3.Row) -> bool:
    try:
        _strict_json_string_list(row["candidate_identity_ids_json"])
        _strict_json_object(row["coverage_summary_json"])
    except (json.JSONDecodeError, TypeError, ValueError, IndexError, KeyError):
        return False
    return True


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
