"""Small store surface for the production-unreachable needs-attention lifecycle."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3

from seektalent.diagnostics_event_models import FailureEnvelopeV1
from seektalent.source_port.authenticated_verify_session_frames import (
    ReceivedVerifySessionResult,
)
from seektalent_runtime_control.checkpoint_participant import (
    write_checkpoint_participant,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.failed_outcome import (
    require_run_truth_mutable,
    validate_failed_outcome_row,
)
from seektalent_runtime_control.models import (
    RuntimeCheckpoint,
    RuntimeRunRecord,
    RuntimeUserAction,
)
from seektalent_runtime_control.needs_attention import (
    ActionSatisfactionAdmission,
    NeedsAttentionAdmission,
    admit_action_satisfaction as _admit_action_satisfaction,
    admit_needs_attention as _admit_needs_attention,
    cancel_needs_attention,
    commit_needs_attention,
    fail_needs_attention,
    resolve_needs_attention,
    validate_needs_attention_row,
)


class NeedsAttentionStoreMixin:
    """Public store methods kept out of the already oversized core module."""

    def _connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    def get_run(self, runtime_run_id: str) -> RuntimeRunRecord:
        raise NotImplementedError

    def admit_needs_attention(
        self,
        *,
        received: ReceivedVerifySessionResult,
        checkpoint: RuntimeCheckpoint,
    ) -> NeedsAttentionAdmission:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                admission = _admit_needs_attention(
                    conn,
                    received=received,
                    checkpoint=checkpoint,
                )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return admission

    def admit_action_satisfaction(
        self,
        *,
        action_id: str,
        received: ReceivedVerifySessionResult,
    ) -> ActionSatisfactionAdmission:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                admission = _admit_action_satisfaction(
                    conn,
                    action_id=action_id,
                    received=received,
                )
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return admission

    def commit_needs_attention(
        self,
        *,
        runtime_run_id: str,
        action_id: str,
        admission: NeedsAttentionAdmission,
        checkpoint: RuntimeCheckpoint,
        envelope: FailureEnvelopeV1 | bytes,
        expected_state_revision: int,
        entered_at: str,
        executor_id: str | None = None,
        attempt_no: int | None = None,
        statement_hook: Callable[[int, str], None] | None = None,
    ) -> RuntimeRunRecord:
        with self._connect() as conn:
            commit_needs_attention(
                conn,
                runtime_run_id=runtime_run_id,
                action_id=action_id,
                admission=admission,
                checkpoint=checkpoint,
                envelope=envelope,
                expected_state_revision=expected_state_revision,
                entered_at=entered_at,
                executor_id=executor_id,
                attempt_no=attempt_no,
                statement_hook=statement_hook,
            )
        return self.get_run(runtime_run_id)

    def resolve_needs_attention(
        self,
        *,
        runtime_run_id: str,
        action_id: str,
        admission: ActionSatisfactionAdmission,
        expected_state_revision: int,
        resolved_at: str,
        statement_hook: Callable[[int, str], None] | None = None,
    ) -> RuntimeRunRecord:
        with self._connect() as conn:
            resolve_needs_attention(
                conn,
                runtime_run_id=runtime_run_id,
                action_id=action_id,
                admission=admission,
                expected_state_revision=expected_state_revision,
                resolved_at=resolved_at,
                statement_hook=statement_hook,
            )
        return self.get_run(runtime_run_id)

    def cancel_needs_attention(
        self,
        *,
        runtime_run_id: str,
        action_id: str,
        expected_state_revision: int,
        cancelled_at: str,
        cancellation_evidence_ref: str,
        statement_hook: Callable[[int, str], None] | None = None,
    ) -> RuntimeRunRecord:
        with self._connect() as conn:
            cancel_needs_attention(
                conn,
                runtime_run_id=runtime_run_id,
                action_id=action_id,
                expected_state_revision=expected_state_revision,
                cancelled_at=cancelled_at,
                cancellation_evidence_ref=cancellation_evidence_ref,
                statement_hook=statement_hook,
            )
        return self.get_run(runtime_run_id)

    def fail_needs_attention(
        self,
        *,
        runtime_run_id: str,
        action_id: str,
        envelope: FailureEnvelopeV1 | bytes,
        expected_state_revision: int,
        terminal_reason_code: str,
        terminal_at: str,
        statement_hook: Callable[[int, str], None] | None = None,
    ) -> RuntimeRunRecord:
        with self._connect() as conn:
            fail_needs_attention(
                conn,
                runtime_run_id=runtime_run_id,
                action_id=action_id,
                envelope=envelope,
                expected_state_revision=expected_state_revision,
                terminal_reason_code=terminal_reason_code,
                terminal_at=terminal_at,
                statement_hook=statement_hook,
            )
        return self.get_run(runtime_run_id)

    def list_user_actions(
        self,
        *,
        runtime_run_id: str,
    ) -> list[RuntimeUserAction]:
        with self._connect() as conn:
            run = conn.execute(
                "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
                (runtime_run_id,),
            ).fetchone()
            if run is None:
                raise RuntimeControlError("runtime_run_not_found")
            validate_needs_attention_row(conn, run)
            validate_failed_outcome_row(conn, run)
            rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_user_actions
                WHERE runtime_run_id = ?
                ORDER BY created_at, action_id
                """,
                (runtime_run_id,),
            ).fetchall()
        try:
            return [_user_action_from_row(row) for row in rows]
        except (TypeError, ValueError):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            ) from None

    def write_checkpoint_for_recovery(
        self,
        checkpoint: RuntimeCheckpoint,
    ) -> RuntimeCheckpoint:
        """Persist no-owner recovery truth without creating an executor."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _write_checkpoint_for_recovery_participant(conn, checkpoint)
                conn.commit()
            except (RuntimeControlError, sqlite3.Error, TypeError, ValueError):
                conn.rollback()
                raise
        return checkpoint


def _write_checkpoint_for_recovery_participant(
    conn: sqlite3.Connection,
    checkpoint: RuntimeCheckpoint,
) -> None:
    """Write no-owner recovery truth inside the caller-owned transaction."""
    run = conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (checkpoint.runtime_run_id,),
    ).fetchone()
    active_lease = conn.execute(
        """
        SELECT 1 FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND status = 'active'
        """,
        (checkpoint.runtime_run_id,),
    ).fetchone()
    if run is None or run["status"] != "resume_requested" or active_lease is not None:
        raise RuntimeControlError("runtime_checkpoint_recovery_authority_rejected")
    require_run_truth_mutable(run)
    write_checkpoint_participant(conn, checkpoint)
    updated = conn.execute(
        """
        UPDATE runtime_control_runs
        SET latest_checkpoint_id = ?, current_stage = ?, current_round = ?,
            updated_at = ?, state_revision = state_revision + 1
        WHERE runtime_run_id = ? AND state_revision = ?
          AND status = 'resume_requested'
          AND product_outcome IS NULL
          AND current_failure_id IS NULL
          AND current_failure_revision IS NULL
          AND current_failure_owner_lease_id IS NULL
          AND current_failure_authority_mode IS NULL
          AND current_action_id IS NULL
        """,
        (
            checkpoint.checkpoint_id,
            checkpoint.stage,
            checkpoint.round_no,
            checkpoint.created_at,
            checkpoint.runtime_run_id,
            run["state_revision"],
        ),
    )
    if updated.rowcount != 1:
        raise RuntimeControlError("runtime_checkpoint_recovery_authority_rejected")


def _user_action_from_row(row: sqlite3.Row) -> RuntimeUserAction:
    return RuntimeUserAction(
        action_id=row["action_id"],
        runtime_run_id=row["runtime_run_id"],
        action_code=row["action_code"],
        instruction_key=row["instruction_key"],
        action_scope=row["action_scope"],
        affected_scope_ref=row["affected_scope_ref"],
        operation_id=row["operation_id"],
        checkpoint_id=row["checkpoint_id"],
        checkpoint_hash=row["checkpoint_hash"],
        candidate_truth_hash=row["candidate_truth_hash"],
        entry_observation_ref=row["entry_observation_ref"],
        entry_observation_digest=row["entry_observation_digest"],
        accepted_requirement_revision_id=row["accepted_requirement_revision_id"],
        runtime_attempt_no=int(row["runtime_attempt_no"]),
        runtime_attempt_fence_ref=row["runtime_attempt_fence_ref"],
        request_hash=row["request_hash"],
        profile_binding_generation=int(row["profile_binding_generation"]),
        browser_control_scope_id=row["browser_control_scope_id"],
        source_ledger_revision=int(row["source_ledger_revision"]),
        source_reconciliation_revision=int(
            row["source_reconciliation_revision"]
        ),
        dispatch_intent_id=row["dispatch_intent_id"],
        dispatch_intent_digest=row["dispatch_intent_digest"],
        source_operation_acceptance_ref=(
            row["source_operation_acceptance_ref"]
        ),
        reconciliation_id=row["reconciliation_id"],
        reconciliation_digest=row["reconciliation_digest"],
        failure_id=row["failure_id"],
        failure_revision=int(row["failure_revision"]),
        status=row["status"],
        resolution_evidence_ref=row["resolution_evidence_ref"],
        resolution_binding_digest=row["resolution_binding_digest"],
        resolution_at=row["resolution_at"],
        authority_mode=row["authority_mode"],
        owner_lease_id=row["owner_lease_id"],
        created_at=row["created_at"],
    )
