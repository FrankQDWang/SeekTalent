"""Atomic main-owned needs-attention lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
import json
import sqlite3

from seektalent.diagnostics_event_models import FailureEnvelopeV1
from seektalent.diagnostics_schema import (
    canonical_diagnostics_bytes,
    parse_failure_envelope,
)
from seektalent.diagnostics_storage import (
    FailureEnvelopeStorageError,
    load_failure_envelope_revision,
    store_failure_envelope_revision,
)
from seektalent_runtime_control.checkpoint_participant import (
    write_checkpoint_participant,
)
from seektalent_runtime_control.clock import timestamp_lte
from seektalent_runtime_control.errors import (
    RuntimeControlError,
    RuntimeControlLookupError,
)
from seektalent_runtime_control.failed_outcome import validate_failed_outcome_row
from seektalent_runtime_control.models import RuntimeCheckpoint
from seektalent_runtime_control.needs_attention_admission import (
    ActionSatisfactionAdmission,
    ActionSatisfactionData as _ActionSatisfactionData,
    NeedsAttentionAdmission,
    NeedsAttentionAdmissionData as _NeedsAttentionAdmissionData,
    admit_action_satisfaction,
    admit_needs_attention,
    canonical_action_from_row as _canonical_action_from_row,
    entry_admission_data as _entry_admission,
    observation_row_by_ref as _observation_row,
    require_committed_entry_admission as _require_committed_entry_admission,
    require_committed_satisfaction_admission as _require_committed_satisfaction_admission,
    satisfaction_admission_data as _satisfaction_admission,
    satisfaction_binding_digest as _satisfaction_binding_digest,
)
from seektalent_runtime_control.needs_attention_schema import (
    NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS,
    migrate_needs_attention_v14_to_v15,
    validate_needs_attention_schema,
)


StatementHook = Callable[[int, str], None]
_MAX_SAFE_INTEGER = 9007199254740991


def commit_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    admission: NeedsAttentionAdmission,
    checkpoint: RuntimeCheckpoint,
    envelope: FailureEnvelopeV1 | bytes,
    expected_state_revision: int,
    entered_at: str,
    executor_id: str | None,
    attempt_no: int | None,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    data = _entry_admission(admission)
    admitted_envelope = _admit_envelope(envelope)
    _require_revision(expected_state_revision)
    _require_timestamp(entered_at)
    if (
        runtime_run_id != data.runtime_run_id
        or checkpoint.runtime_run_id != runtime_run_id
        or checkpoint.checkpoint_id != data.checkpoint_id
        or not _opaque(action_id)
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_admission_mismatch"
        )
    _require_needs_attention_envelope(
        admitted_envelope,
        data=data,
        runtime_run_id=runtime_run_id,
        entered_at=entered_at,
        attempt_no=attempt_no,
    )
    hook = statement_hook or (lambda _index, _phase: None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _run_row(conn, runtime_run_id)
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        if row["status"] == "needs_attention":
            replay = _require_entry_replay(
                conn,
                row=row,
                action_id=action_id,
                data=data,
                checkpoint=checkpoint,
                envelope=admitted_envelope,
                expected_state_revision=expected_state_revision,
                executor_id=executor_id,
                attempt_no=attempt_no,
            )
            conn.commit()
            return replay
        if row["status"] in {
            "cancellation_requested",
            "cancelled",
        }:
            raise RuntimeControlError(
                "runtime_needs_attention_cancellation_won"
            )
        if row["status"] in {"completed", "failed"}:
            raise RuntimeControlError(
                "runtime_needs_attention_terminal_immutable"
            )
        if int(row["state_revision"]) != expected_state_revision:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        if any(
            row[name] is not None
            for name in (
                "product_outcome",
                "current_failure_id",
                "current_failure_revision",
                "current_failure_owner_lease_id",
                "current_failure_authority_mode",
                "current_action_id",
            )
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_truth_conflict"
            )
        active_lease = _active_lease_row(conn, runtime_run_id)
        owner_supplied = type(executor_id) is str and type(attempt_no) is int
        if (executor_id is None) != (attempt_no is None):
            raise RuntimeControlError(
                "runtime_needs_attention_authority_rejected"
            )
        if owner_supplied:
            if (
                row["status"]
                not in {"starting", "running", "pause_requested"}
                or active_lease is None
                or active_lease["executor_id"] != executor_id
                or int(active_lease["attempt_no"]) != attempt_no
                or timestamp_lte(active_lease["lease_expires_at"], entered_at)
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_authority_rejected"
                )
        else:
            if row["status"] != "resume_requested" or active_lease is not None:
                raise RuntimeControlError(
                    "runtime_needs_attention_authority_rejected"
                )
            _require_no_reconcile_first(conn, runtime_run_id)
            if row["latest_checkpoint_id"] != checkpoint.checkpoint_id:
                raise RuntimeControlError(
                    "runtime_needs_attention_checkpoint_mismatch"
                )
        owner_lease_id = (
            active_lease["lease_id"]
            if owner_supplied and active_lease is not None
            else None
        )
        _require_committed_entry_admission(
            conn,
            data=data,
            authority_mode="active_owner" if owner_supplied else "no_owner",
            owner_lease_id=owner_lease_id,
        )

        hook(0, "before_checkpoint")
        write_checkpoint_participant(conn, checkpoint)
        hook(1, "after_checkpoint")
        checkpoint_hash = _checkpoint_hash(checkpoint)
        candidate_truth_hash = _candidate_truth_hash(checkpoint)

        hook(2, "before_failure_envelope")
        stored = store_failure_envelope_revision(conn, admitted_envelope)
        hook(3, "after_failure_envelope")

        hook(4, "before_action")
        conn.execute(
            """
            INSERT INTO runtime_control_user_actions (
                action_id, runtime_run_id, action_code, instruction_key,
                action_scope, affected_scope_ref, operation_id, checkpoint_id,
                checkpoint_hash, candidate_truth_hash, entry_observation_ref,
                entry_observation_digest, accepted_requirement_revision_id,
                runtime_attempt_no, runtime_attempt_fence_ref, request_hash,
                entry_request_semantic_digest,
                profile_binding_generation, browser_control_scope_id,
                source_ledger_revision, source_reconciliation_revision,
                entry_dispatch_authorization_ordinal,
                dispatch_intent_id, dispatch_intent_digest,
                source_operation_acceptance_ref,
                reconciliation_id, reconciliation_digest, failure_id,
                failure_revision, status, resolution_evidence_ref,
                resolution_binding_digest, resolution_operation_id,
                resolution_result_digest,
                resolution_request_hash,
                resolution_request_semantic_digest,
                resolution_runtime_attempt_fence_ref,
                resolution_dispatch_authorization_ordinal,
                resolution_reconciliation_id,
                resolution_reconciliation_digest,
                resolution_source_ledger_revision,
                resolution_source_reconciliation_revision,
                resolution_at, authority_mode,
                owner_lease_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    'pending', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL, NULL, ?, ?, ?)
            """,
            (
                action_id,
                runtime_run_id,
                data.action.code,
                data.action.instruction_key,
                data.action.scope,
                data.action.affected_scope_ref,
                data.operation_id,
                checkpoint.checkpoint_id,
                checkpoint_hash,
                candidate_truth_hash,
                data.entry_observation_ref,
                data.entry_observation_digest,
                data.accepted_requirement_revision_id,
                data.runtime_attempt_no,
                data.runtime_attempt_fence_ref,
                data.request_hash,
                data.request_semantic_digest,
                data.profile_binding_generation,
                data.browser_control_scope_id,
                data.source_ledger_revision,
                data.source_reconciliation_revision,
                data.dispatch_authorization_ordinal,
                data.dispatch_intent_id,
                data.dispatch_intent_digest,
                data.source_operation_acceptance_ref,
                data.reconciliation_id,
                data.reconciliation_digest,
                stored.ref.failure_id,
                stored.ref.revision,
                "active_owner" if owner_supplied else "no_owner",
                owner_lease_id,
                entered_at,
            ),
        )
        hook(5, "after_action")

        hook(6, "before_authority_release")
        if owner_lease_id is not None:
            released = conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET status = 'revoked', released_at = ?,
                    reason_code = 'runtime_needs_attention'
                WHERE lease_id = ? AND runtime_run_id = ?
                  AND executor_id = ? AND attempt_no = ? AND status = 'active'
                """,
                (
                    entered_at,
                    owner_lease_id,
                    runtime_run_id,
                    executor_id,
                    attempt_no,
                ),
            )
            if released.rowcount != 1:
                raise RuntimeControlError(
                    "runtime_needs_attention_authority_rejected"
                )
        hook(7, "after_authority_release")

        hook(8, "before_run_update")
        updated = conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = 'needs_attention',
                current_stage = ?,
                current_round = ?,
                latest_checkpoint_id = ?,
                product_outcome = 'needs_attention',
                current_failure_id = ?,
                current_failure_revision = ?,
                current_failure_owner_lease_id = ?,
                current_failure_authority_mode = ?,
                current_action_id = ?,
                stop_reason_code = ?,
                updated_at = ?,
                completed_at = NULL,
                state_revision = state_revision + 1
            WHERE runtime_run_id = ? AND state_revision = ?
              AND status = ?
              AND product_outcome IS NULL
              AND current_failure_id IS NULL
              AND current_failure_revision IS NULL
              AND current_failure_owner_lease_id IS NULL
              AND current_failure_authority_mode IS NULL
              AND current_action_id IS NULL
            """,
            (
                checkpoint.stage,
                checkpoint.round_no,
                checkpoint.checkpoint_id,
                stored.ref.failure_id,
                stored.ref.revision,
                owner_lease_id,
                "active_owner" if owner_supplied else "no_owner",
                action_id,
                admitted_envelope.reason_code,
                entered_at,
                runtime_run_id,
                expected_state_revision,
                row["status"],
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        hook(9, "after_run_update")
        committed = _run_row(conn, runtime_run_id)
        if committed is None:
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        validate_needs_attention_row(conn, committed)
        hook(10, "before_commit")
        conn.commit()
        hook(11, "after_commit")
        return committed
    except FailureEnvelopeStorageError:
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None
    except RuntimeControlError:
        _rollback(conn)
        raise
    except (sqlite3.Error, TypeError, ValueError):
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_storage_failed"
        ) from None
    except RuntimeError:
        _rollback(conn)
        raise


def resolve_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    admission: ActionSatisfactionAdmission,
    expected_state_revision: int,
    resolved_at: str,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    data = _satisfaction_admission(admission)
    if runtime_run_id != data.runtime_run_id or not _opaque(action_id):
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_mismatch"
        )
    return _exit_needs_attention(
        conn,
        runtime_run_id=runtime_run_id,
        action_id=action_id,
        expected_state_revision=expected_state_revision,
        resolved_at=resolved_at,
        target_status="resume_requested",
        resolution_evidence_ref=data.authenticated_evidence_ref,
        satisfaction=data,
        failed_envelope=None,
        terminal_reason_code=None,
        statement_hook=statement_hook,
    )


def cancel_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    expected_state_revision: int,
    cancelled_at: str,
    cancellation_evidence_ref: str,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    if not _sha256_hex(cancellation_evidence_ref):
        raise RuntimeControlError(
            "runtime_needs_attention_cancellation_rejected"
        )
    return _exit_needs_attention(
        conn,
        runtime_run_id=runtime_run_id,
        action_id=action_id,
        expected_state_revision=expected_state_revision,
        resolved_at=cancelled_at,
        target_status="cancelled",
        resolution_evidence_ref=cancellation_evidence_ref,
        satisfaction=None,
        failed_envelope=None,
        terminal_reason_code=None,
        statement_hook=statement_hook,
    )


def fail_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    envelope: FailureEnvelopeV1 | bytes,
    expected_state_revision: int,
    terminal_reason_code: str,
    terminal_at: str,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    admitted = _admit_envelope(envelope)
    if (
        admitted.run_id != runtime_run_id
        or admitted.current_outcome != "failed"
        or admitted.user_action is not None
        or admitted.reason_code != terminal_reason_code
        or admitted.occurred_at != terminal_at
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_failed_envelope_mismatch"
        )
    return _exit_needs_attention(
        conn,
        runtime_run_id=runtime_run_id,
        action_id=action_id,
        expected_state_revision=expected_state_revision,
        resolved_at=terminal_at,
        target_status="failed",
        resolution_evidence_ref=f"sha256:{sha256(canonical_diagnostics_bytes(admitted)).hexdigest()}",
        satisfaction=None,
        failed_envelope=admitted,
        terminal_reason_code=terminal_reason_code,
        statement_hook=statement_hook,
    )


def _exit_needs_attention(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    action_id: str,
    expected_state_revision: int,
    resolved_at: str,
    target_status: str,
    resolution_evidence_ref: str,
    satisfaction: _ActionSatisfactionData | None,
    failed_envelope: FailureEnvelopeV1 | None,
    terminal_reason_code: str | None,
    statement_hook: StatementHook | None,
) -> sqlite3.Row:
    _require_revision(expected_state_revision)
    _require_timestamp(resolved_at)
    if target_status not in {"resume_requested", "cancelled", "failed"}:
        raise RuntimeControlError(
            "runtime_needs_attention_resolution_rejected"
        )
    if not (
        _sha256_hex(resolution_evidence_ref)
        or (
            resolution_evidence_ref.startswith("sha256:")
            and _sha256_hex(resolution_evidence_ref[7:])
        )
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_resolution_rejected"
        )
    hook = statement_hook or (lambda _index, _phase: None)
    resolution_binding_digest = (
        satisfaction.resolution_binding_digest
        if satisfaction is not None
        else sha256(
            _canonical_json(
                {
                    "actionId": action_id,
                    "runtimeRunId": runtime_run_id,
                    "targetStatus": target_status,
                    "resolutionEvidenceRef": resolution_evidence_ref,
                    "resolvedAt": resolved_at,
                }
            )
        ).hexdigest()
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _run_row(conn, runtime_run_id)
        action_row = _action_row(conn, action_id)
        if row is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        if (
            row["status"] == target_status
            and action_row is not None
            and action_row["status"]
            == {
                "resume_requested": "resolved",
                "cancelled": "cancelled",
                "failed": "failed",
            }[target_status]
        ):
            replay = _require_exit_replay(
                conn,
                row=row,
                action_row=action_row,
                expected_state_revision=expected_state_revision,
                target_status=target_status,
                resolution_evidence_ref=resolution_evidence_ref,
                resolution_binding_digest=resolution_binding_digest,
                resolved_at=resolved_at,
                failed_envelope=failed_envelope,
                satisfaction=satisfaction,
            )
            conn.commit()
            return replay
        if row["status"] != "needs_attention":
            raise RuntimeControlError(
                "runtime_needs_attention_resolution_conflict"
            )
        validate_needs_attention_row(conn, row)
        if (
            int(row["state_revision"]) != expected_state_revision
            or row["current_action_id"] != action_id
            or action_row is None
            or action_row["runtime_run_id"] != runtime_run_id
            or action_row["status"] != "pending"
            or _active_lease_row(conn, runtime_run_id) is not None
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        checkpoint = _checkpoint_from_action(conn, action_row)
        if satisfaction is not None:
            if (
                satisfaction.action
                != _canonical_action_from_row(action_row)
                or satisfaction.operation_id == action_row["operation_id"]
                or satisfaction.checkpoint_id != action_row["checkpoint_id"]
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_satisfaction_mismatch"
                )
            _require_no_reconcile_first(conn, runtime_run_id)
            _require_committed_satisfaction_admission(
                conn,
                action_row=action_row,
                data=satisfaction,
            )
        _require_checkpoint_binding(conn, action_row, checkpoint)

        new_failure_id: str | None = None
        new_failure_revision: int | None = None
        if failed_envelope is not None:
            hook(0, "before_failure_envelope")
            stored = store_failure_envelope_revision(conn, failed_envelope)
            new_failure_id = stored.ref.failure_id
            new_failure_revision = stored.ref.revision
            hook(1, "after_failure_envelope")

        hook(2, "before_action_resolution")
        action_status = {
            "resume_requested": "resolved",
            "cancelled": "cancelled",
            "failed": "failed",
        }[target_status]
        resolved = conn.execute(
            """
            UPDATE runtime_control_user_actions
            SET status = ?, resolution_evidence_ref = ?,
                resolution_binding_digest = ?,
                resolution_operation_id = ?,
                resolution_result_digest = ?,
                resolution_request_hash = ?,
                resolution_request_semantic_digest = ?,
                resolution_runtime_attempt_fence_ref = ?,
                resolution_dispatch_authorization_ordinal = ?,
                resolution_reconciliation_id = ?,
                resolution_reconciliation_digest = ?,
                resolution_source_ledger_revision = ?,
                resolution_source_reconciliation_revision = ?,
                resolution_at = ?
            WHERE action_id = ? AND runtime_run_id = ? AND status = 'pending'
            """,
            (
                action_status,
                resolution_evidence_ref,
                resolution_binding_digest,
                None if satisfaction is None else satisfaction.operation_id,
                None if satisfaction is None else satisfaction.result_digest,
                None if satisfaction is None else satisfaction.request_hash,
                (
                    None
                    if satisfaction is None
                    else satisfaction.request_semantic_digest
                ),
                (
                    None
                    if satisfaction is None
                    else satisfaction.runtime_attempt_fence_ref
                ),
                (
                    None
                    if satisfaction is None
                    else satisfaction.dispatch_authorization_ordinal
                ),
                (
                    None
                    if satisfaction is None
                    else satisfaction.reconciliation_id
                ),
                (
                    None
                    if satisfaction is None
                    else satisfaction.reconciliation_digest
                ),
                (
                    None
                    if satisfaction is None
                    else satisfaction.source_ledger_revision
                ),
                (
                    None
                    if satisfaction is None
                    else satisfaction.source_reconciliation_revision
                ),
                resolved_at,
                action_id,
                runtime_run_id,
            ),
        )
        if resolved.rowcount != 1:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        hook(3, "after_action_resolution")

        hook(4, "before_run_update")
        updated = conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = ?,
                current_stage = ?,
                product_outcome = ?,
                current_failure_id = ?,
                current_failure_revision = ?,
                current_failure_owner_lease_id = NULL,
                current_failure_authority_mode = ?,
                current_action_id = NULL,
                stop_reason_code = ?,
                updated_at = ?,
                completed_at = ?,
                state_revision = state_revision + 1
            WHERE runtime_run_id = ?
              AND state_revision = ?
              AND status = 'needs_attention'
              AND product_outcome = 'needs_attention'
              AND current_action_id = ?
            """,
            (
                target_status,
                target_status if target_status != "resume_requested" else checkpoint.stage,
                None if target_status == "resume_requested" else target_status,
                new_failure_id,
                new_failure_revision,
                "no_owner" if target_status == "failed" else None,
                terminal_reason_code,
                resolved_at,
                resolved_at if target_status in {"cancelled", "failed"} else None,
                runtime_run_id,
                expected_state_revision,
                action_id,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError(
                "runtime_needs_attention_revision_conflict"
            )
        hook(5, "after_run_update")
        committed = _run_row(conn, runtime_run_id)
        if committed is None:
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        validate_needs_attention_row(conn, committed)
        validate_failed_outcome_row(conn, committed)
        hook(6, "before_commit")
        conn.commit()
        hook(7, "after_commit")
        return committed
    except FailureEnvelopeStorageError:
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None
    except RuntimeControlError:
        _rollback(conn)
        raise
    except (sqlite3.Error, TypeError, ValueError):
        _rollback(conn)
        raise RuntimeControlError(
            "runtime_needs_attention_storage_failed"
        ) from None
    except RuntimeError:
        _rollback(conn)
        raise


def validate_needs_attention_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> None:
    _validate_action_history(conn, row)
    if row["status"] != "needs_attention":
        foreign_failure_truth = (
            row["product_outcome"] != "failed"
            and any(
                row[name] is not None
                for name in (
                    "current_failure_id",
                    "current_failure_revision",
                    "current_failure_owner_lease_id",
                    "current_failure_authority_mode",
                )
            )
        )
        if (
            row["product_outcome"] == "needs_attention"
            or row["current_action_id"] is not None
            or foreign_failure_truth
        ):
            raise RuntimeControlError(
                (
                    "runtime_failed_outcome_integrity_failed"
                    if foreign_failure_truth
                    else "runtime_needs_attention_integrity_failed"
                )
            )
        return
    state_revision = row["state_revision"]
    if (
        type(state_revision) is not int
        or state_revision < 0
        or state_revision > _MAX_SAFE_INTEGER
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        )
    if row["status"] == "needs_attention":
        if (
            row["product_outcome"] != "needs_attention"
            or row["current_action_id"] is None
            or row["current_failure_id"] is None
            or row["current_failure_revision"] is None
            or row["current_failure_authority_mode"]
            not in {"no_owner", "active_owner"}
            or _active_lease_row(conn, row["runtime_run_id"]) is not None
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        action_row = _action_row(conn, row["current_action_id"])
        if (
            action_row is None
            or action_row["runtime_run_id"] != row["runtime_run_id"]
            or action_row["status"] != "pending"
            or action_row["failure_id"] != row["current_failure_id"]
            or action_row["failure_revision"]
            != row["current_failure_revision"]
            or action_row["checkpoint_id"] != row["latest_checkpoint_id"]
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        envelope = load_failure_envelope_revision(
            conn,
            failure_id=row["current_failure_id"],
            revision=row["current_failure_revision"],
        )
        if (
            envelope.run_id != row["runtime_run_id"]
            or envelope.current_outcome != "needs_attention"
            or envelope.user_action != _canonical_action_from_row(action_row)
            or envelope.operation_id != action_row["operation_id"]
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        checkpoint = _checkpoint_from_action(conn, action_row)
        _require_checkpoint_binding(conn, action_row, checkpoint)
        if row["current_failure_authority_mode"] != action_row["authority_mode"]:
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        if (
            row["current_failure_owner_lease_id"]
            != action_row["owner_lease_id"]
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        if action_row["authority_mode"] == "active_owner":
            lease = conn.execute(
                """
                SELECT * FROM runtime_control_executor_leases
                WHERE lease_id = ?
                """,
                (action_row["owner_lease_id"],),
            ).fetchone()
            if (
                lease is None
                or lease["runtime_run_id"] != row["runtime_run_id"]
                or lease["status"] != "revoked"
                or lease["reason_code"] != "runtime_needs_attention"
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_integrity_failed"
                )
        return


def _validate_action_history(
    conn: sqlite3.Connection,
    run_row: sqlite3.Row,
) -> None:
    try:
        actions = conn.execute(
            """
            SELECT * FROM runtime_control_user_actions
            WHERE runtime_run_id = ?
            ORDER BY created_at, action_id
            """,
            (run_row["runtime_run_id"],),
        ).fetchall()
        if not actions:
            return
        source_ids = json.loads(run_row["source_ids_json"])
        if source_ids != ["liepin"]:
            raise RuntimeControlError(
                "runtime_needs_attention_integrity_failed"
            )
        for action_row in actions:
            status = action_row["status"]
            resolution_common = (
                action_row["resolution_evidence_ref"],
                action_row["resolution_binding_digest"],
                action_row["resolution_at"],
            )
            satisfaction_values = (
                action_row["resolution_operation_id"],
                action_row["resolution_result_digest"],
                action_row["resolution_request_hash"],
                action_row["resolution_request_semantic_digest"],
                action_row["resolution_runtime_attempt_fence_ref"],
                action_row["resolution_dispatch_authorization_ordinal"],
                action_row["resolution_reconciliation_id"],
                action_row["resolution_reconciliation_digest"],
                action_row["resolution_source_ledger_revision"],
                action_row["resolution_source_reconciliation_revision"],
            )
            if (
                status not in {"pending", "resolved", "cancelled", "failed"}
                or (
                    status == "pending"
                    and any(
                        value is not None
                        for value in (*resolution_common, *satisfaction_values)
                    )
                )
                or (
                    status == "resolved"
                    and any(
                        value is None
                        for value in (*resolution_common, *satisfaction_values)
                    )
                )
                or (
                    status in {"cancelled", "failed"}
                    and (
                        any(value is None for value in resolution_common)
                        or any(
                            value is not None for value in satisfaction_values
                        )
                    )
                )
                or action_row["authority_mode"]
                not in {"no_owner", "active_owner"}
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_integrity_failed"
                )
            action = _canonical_action_from_row(action_row)
            operation = conn.execute(
                """
                SELECT * FROM runtime_control_source_operations
                WHERE runtime_run_id = ? AND operation_id = ?
                """,
                (
                    action_row["runtime_run_id"],
                    action_row["operation_id"],
                ),
            ).fetchone()
            expectation = conn.execute(
                """
                SELECT *
                FROM runtime_control_source_operation_admission_expectations
                WHERE runtime_run_id = ? AND operation_id = ?
                  AND dispatch_authorization_ordinal = ?
                """,
                (
                    action_row["runtime_run_id"],
                    action_row["operation_id"],
                    action_row["entry_dispatch_authorization_ordinal"],
                ),
            ).fetchone()
            dispatch = conn.execute(
                """
                SELECT * FROM runtime_control_source_dispatch_outbox
                WHERE runtime_run_id = ? AND operation_id = ?
                  AND dispatch_authorization_ordinal = ?
                """,
                (
                    action_row["runtime_run_id"],
                    action_row["operation_id"],
                    action_row["entry_dispatch_authorization_ordinal"],
                ),
            ).fetchone()
            if (
                action.affected_scope_ref
                != action_row["browser_control_scope_id"]
                or run_row["approved_requirement_revision_id"]
                != action_row["accepted_requirement_revision_id"]
                or operation is None
                or expectation is None
                or dispatch is None
                or expectation["dispatch_authorization_ordinal"]
                != action_row["entry_dispatch_authorization_ordinal"]
                or dispatch["dispatch_authorization_ordinal"]
                != action_row["entry_dispatch_authorization_ordinal"]
                or operation["source_id"] != "liepin"
                or operation["operation_kind"] != "verify_session"
                or operation["canonical_request_hash"]
                != action_row["request_hash"]
                or operation["accepted_requirement_revision_id"]
                != action_row["accepted_requirement_revision_id"]
                or operation["ledger_revision"]
                != action_row["source_ledger_revision"]
                or operation["reconciliation_revision"]
                != action_row["source_reconciliation_revision"]
                or expectation["runtime_attempt_no"]
                != action_row["runtime_attempt_no"]
                or expectation["runtime_attempt_fence_ref"]
                != action_row["runtime_attempt_fence_ref"]
                or expectation["profile_binding_generation"]
                != action_row["profile_binding_generation"]
                or expectation["browser_control_scope_id"]
                != action_row["browser_control_scope_id"]
                or dispatch["canonical_request_hash"]
                != action_row["request_hash"]
                or dispatch["dispatch_intent_id"]
                != action_row["dispatch_intent_id"]
                or dispatch["dispatch_intent_digest"]
                != action_row["dispatch_intent_digest"]
                or dispatch["source_operation_acceptance_ref"]
                != action_row["source_operation_acceptance_ref"]
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_integrity_failed"
                )
            checkpoint = _checkpoint_from_action(conn, action_row)
            _require_checkpoint_binding(conn, action_row, checkpoint)
            observation = _observation_row(
                conn,
                action_row["entry_observation_ref"],
            )
            if (
                observation is None
                or observation["result_digest"]
                != action_row["entry_observation_digest"]
                or observation["runtime_run_id"]
                != action_row["runtime_run_id"]
                or observation["operation_id"] != action_row["operation_id"]
                or observation["source_id"] != "liepin"
                or observation["operation_kind"] != "verify_session"
                or observation["idempotency_key"]
                != operation["idempotency_key"]
                or observation["accepted_requirement_revision_id"]
                != action_row["accepted_requirement_revision_id"]
                or observation["runtime_attempt_no"]
                != action_row["runtime_attempt_no"]
                or observation["runtime_attempt_fence_ref"]
                != action_row["runtime_attempt_fence_ref"]
                or observation["request_hash"] != action_row["request_hash"]
                or observation["request_semantic_digest"]
                != action_row["entry_request_semantic_digest"]
                or observation["profile_binding_generation"]
                != action_row["profile_binding_generation"]
                or observation["browser_control_scope_id"]
                != action_row["browser_control_scope_id"]
                or observation["action_digest"]
                != sha256(
                    _canonical_json(action.model_dump(mode="json"))
                ).hexdigest()
                or observation["session_readiness"] != "not_ready"
                or observation["dispatch_authorization_ordinal"]
                != action_row["entry_dispatch_authorization_ordinal"]
                or observation["dispatch_intent_id"]
                != action_row["dispatch_intent_id"]
                or observation["dispatch_intent_digest"]
                != action_row["dispatch_intent_digest"]
                or observation["source_operation_acceptance_ref"]
                != action_row["source_operation_acceptance_ref"]
                or dispatch["expected_ledger_revision"]
                != observation["expected_ledger_revision"]
                or dispatch["expected_reconciliation_revision"]
                != observation["expected_reconciliation_revision"]
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_integrity_failed"
                )
            envelope = load_failure_envelope_revision(
                conn,
                failure_id=action_row["failure_id"],
                revision=action_row["failure_revision"],
            )
            if (
                envelope.run_id != action_row["runtime_run_id"]
                or envelope.operation_id != action_row["operation_id"]
                or envelope.attempt_no != action_row["runtime_attempt_no"]
                or envelope.current_outcome != "needs_attention"
                or envelope.user_action != action
                or envelope.reason_code != "user_action_required"
                or envelope.occurred_at != action_row["created_at"]
            ):
                raise RuntimeControlError(
                    "runtime_needs_attention_integrity_failed"
                )
            if action_row["authority_mode"] == "active_owner":
                lease = conn.execute(
                    """
                    SELECT * FROM runtime_control_executor_leases
                    WHERE lease_id = ?
                    """,
                    (action_row["owner_lease_id"],),
                ).fetchone()
                if (
                    lease is None
                    or lease["runtime_run_id"]
                    != action_row["runtime_run_id"]
                    or lease["attempt_no"]
                    != action_row["runtime_attempt_no"]
                    or lease["status"] != "revoked"
                    or lease["reason_code"] != "runtime_needs_attention"
                ):
                    raise RuntimeControlError(
                        "runtime_needs_attention_integrity_failed"
                    )
            else:
                reconciliation = conn.execute(
                    """
                    SELECT * FROM runtime_control_source_reconciliations
                    WHERE reconciliation_id = ?
                    """,
                    (action_row["reconciliation_id"],),
                ).fetchone()
                if (
                    reconciliation is None
                    or action_row["reconciliation_digest"]
                    != sha256(
                        _canonical_json(dict(reconciliation))
                    ).hexdigest()
                    or reconciliation["history_result_ref"]
                    != "sha256:" + reconciliation["history_result_digest"]
                    or reconciliation["reconciliation_id"]
                    != "source-history-"
                    + reconciliation["history_result_digest"]
                    or reconciliation["conclusive_observation_ref"]
                    != action_row["entry_observation_digest"]
                    or reconciliation["runtime_run_id"]
                    != action_row["runtime_run_id"]
                    or reconciliation["operation_id"]
                    != action_row["operation_id"]
                    or reconciliation["canonical_request_hash"]
                    != action_row["request_hash"]
                    or reconciliation["accepted_requirement_revision_id"]
                    != action_row["accepted_requirement_revision_id"]
                    or reconciliation["runtime_attempt_no"]
                    != action_row["runtime_attempt_no"]
                    or reconciliation["source_operation_disposition"]
                    != "user_action_required"
                    or reconciliation["retry_posture"] != "no_retry"
                    or reconciliation["committed_ledger_revision"]
                    != action_row["source_ledger_revision"]
                    or reconciliation["committed_reconciliation_revision"]
                    != action_row["source_reconciliation_revision"]
                ):
                    raise RuntimeControlError(
                        "runtime_needs_attention_integrity_failed"
                    )
            if action_row["status"] == "resolved":
                resolution_observation = _observation_row(
                    conn,
                    action_row["resolution_evidence_ref"],
                )
                resolution_operation = conn.execute(
                    """
                    SELECT * FROM runtime_control_source_operations
                    WHERE runtime_run_id = ? AND operation_id = ?
                    """,
                    (
                        action_row["runtime_run_id"],
                        action_row["resolution_operation_id"],
                    ),
                ).fetchone()
                resolution_expectation = conn.execute(
                    """
                    SELECT *
                    FROM runtime_control_source_operation_admission_expectations
                    WHERE runtime_run_id = ? AND operation_id = ?
                      AND dispatch_authorization_ordinal = ?
                    """,
                    (
                        action_row["runtime_run_id"],
                        action_row["resolution_operation_id"],
                        action_row[
                            "resolution_dispatch_authorization_ordinal"
                        ],
                    ),
                ).fetchone()
                resolution_dispatch = conn.execute(
                    """
                    SELECT * FROM runtime_control_source_dispatch_outbox
                    WHERE runtime_run_id = ? AND operation_id = ?
                      AND dispatch_authorization_ordinal = ?
                    """,
                    (
                        action_row["runtime_run_id"],
                        action_row["resolution_operation_id"],
                        action_row[
                            "resolution_dispatch_authorization_ordinal"
                        ],
                    ),
                ).fetchone()
                resolution_reconciliation = conn.execute(
                    """
                    SELECT * FROM runtime_control_source_reconciliations
                    WHERE reconciliation_id = ?
                    """,
                    (action_row["resolution_reconciliation_id"],),
                ).fetchone()
                if (
                    resolution_observation is None
                    or resolution_operation is None
                    or resolution_expectation is None
                    or resolution_dispatch is None
                    or resolution_reconciliation is None
                    or resolution_observation["session_readiness"] != "ready"
                    or resolution_observation["action_digest"] is not None
                    or resolution_observation["runtime_run_id"]
                    != action_row["runtime_run_id"]
                    or resolution_observation["operation_id"]
                    != action_row["resolution_operation_id"]
                    or resolution_observation["operation_id"]
                    == action_row["operation_id"]
                    or resolution_observation["result_digest"]
                    != action_row["resolution_result_digest"]
                    or resolution_observation[
                        "dispatch_authorization_ordinal"
                    ]
                    != action_row["resolution_dispatch_authorization_ordinal"]
                    or resolution_observation["request_hash"]
                    != action_row["resolution_request_hash"]
                    or resolution_observation["request_semantic_digest"]
                    != action_row["resolution_request_semantic_digest"]
                    or resolution_observation["request_semantic_digest"]
                    != action_row["entry_request_semantic_digest"]
                    or resolution_observation[
                        "accepted_requirement_revision_id"
                    ]
                    != action_row["accepted_requirement_revision_id"]
                    or resolution_observation["runtime_attempt_no"]
                    != action_row["runtime_attempt_no"]
                    or resolution_observation["runtime_attempt_fence_ref"]
                    != action_row["resolution_runtime_attempt_fence_ref"]
                    or resolution_observation[
                        "profile_binding_generation"
                    ]
                    != action_row["profile_binding_generation"]
                    or resolution_observation["browser_control_scope_id"]
                    != action_row["browser_control_scope_id"]
                    or resolution_observation[
                        "actual_profile_binding_ref"
                    ]
                    != observation["actual_profile_binding_ref"]
                    or resolution_observation[
                        "actual_profile_binding_generation"
                    ]
                    < observation["actual_profile_binding_generation"]
                    or resolution_operation["operation_phase"] != "reconciled"
                    or resolution_operation["canonical_request_hash"]
                    != action_row["resolution_request_hash"]
                    or resolution_operation["source_operation_disposition"]
                    != "completed"
                    or resolution_operation["retry_posture"] != "no_retry"
                    or resolution_operation["ledger_revision"]
                    != action_row["resolution_source_ledger_revision"]
                    or resolution_operation["reconciliation_revision"]
                    != action_row[
                        "resolution_source_reconciliation_revision"
                    ]
                    or resolution_expectation["runtime_attempt_fence_ref"]
                    != action_row["resolution_runtime_attempt_fence_ref"]
                    or resolution_expectation["profile_binding_generation"]
                    != action_row["profile_binding_generation"]
                    or resolution_expectation["browser_control_scope_id"]
                    != action_row["browser_control_scope_id"]
                    or resolution_dispatch["dispatch_intent_id"]
                    != resolution_observation["dispatch_intent_id"]
                    or resolution_dispatch["canonical_request_hash"]
                    != action_row["resolution_request_hash"]
                    or resolution_dispatch["dispatch_intent_digest"]
                    != resolution_observation["dispatch_intent_digest"]
                    or resolution_dispatch["source_operation_acceptance_ref"]
                    != resolution_observation[
                        "source_operation_acceptance_ref"
                    ]
                    or resolution_reconciliation["runtime_run_id"]
                    != action_row["runtime_run_id"]
                    or resolution_reconciliation["operation_id"]
                    != action_row["resolution_operation_id"]
                    or resolution_reconciliation["canonical_request_hash"]
                    != action_row["resolution_request_hash"]
                    or resolution_reconciliation[
                        "source_operation_disposition"
                    ]
                    != "completed"
                    or resolution_reconciliation["retry_posture"] != "no_retry"
                    or resolution_reconciliation[
                        "conclusive_observation_ref"
                    ]
                    != action_row["resolution_result_digest"]
                    or resolution_reconciliation["history_result_ref"]
                    != "sha256:"
                    + resolution_reconciliation["history_result_digest"]
                    or resolution_reconciliation["reconciliation_id"]
                    != "source-history-"
                    + resolution_reconciliation["history_result_digest"]
                    or sha256(
                        _canonical_json(dict(resolution_reconciliation))
                    ).hexdigest()
                    != action_row["resolution_reconciliation_digest"]
                    or _satisfaction_binding_digest(
                        action_row=action_row,
                        observation_row=resolution_observation,
                        reconciliation_row=resolution_reconciliation,
                    )
                    != action_row["resolution_binding_digest"]
                ):
                    raise RuntimeControlError(
                        "runtime_needs_attention_integrity_failed"
                    )
            elif action_row["status"] in {"cancelled", "failed"}:
                target = action_row["status"]
                expected_digest = sha256(
                    _canonical_json(
                        {
                            "actionId": action_row["action_id"],
                            "runtimeRunId": action_row["runtime_run_id"],
                            "targetStatus": target,
                            "resolutionEvidenceRef": (
                                action_row["resolution_evidence_ref"]
                            ),
                            "resolvedAt": action_row["resolution_at"],
                        }
                    )
                ).hexdigest()
                if (
                    action_row["resolution_binding_digest"]
                    != expected_digest
                ):
                    raise RuntimeControlError(
                        "runtime_needs_attention_integrity_failed"
                    )
                if target == "failed":
                    failed_envelope = load_failure_envelope_revision(
                        conn,
                        failure_id=run_row["current_failure_id"],
                        revision=run_row["current_failure_revision"],
                    )
                    failed_ref = (
                        "sha256:"
                        + sha256(
                            canonical_diagnostics_bytes(failed_envelope)
                        ).hexdigest()
                    )
                    if (
                        run_row["status"] != "failed"
                        or failed_envelope.run_id
                        != action_row["runtime_run_id"]
                        or failed_envelope.operation_id
                        != action_row["operation_id"]
                        or failed_envelope.current_outcome != "failed"
                        or failed_envelope.user_action is not None
                        or failed_envelope.occurred_at
                        != action_row["resolution_at"]
                        or action_row["resolution_evidence_ref"]
                        != failed_ref
                    ):
                        raise RuntimeControlError(
                            "runtime_needs_attention_integrity_failed"
                        )
    except RuntimeControlError as exc:
        if exc.reason_code == "runtime_needs_attention_integrity_failed":
            raise
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None
    except FailureEnvelopeStorageError:
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None
    except sqlite3.Error:
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None


def _require_needs_attention_envelope(
    envelope: FailureEnvelopeV1,
    *,
    data: _NeedsAttentionAdmissionData,
    runtime_run_id: str,
    entered_at: str,
    attempt_no: int | None,
) -> None:
    if (
        envelope.run_id != runtime_run_id
        or envelope.operation_id != data.operation_id
        or envelope.current_outcome != "needs_attention"
        or envelope.user_action != data.action
        or envelope.reason_code != "user_action_required"
        or envelope.occurred_at != entered_at
        or envelope.attempt_no != data.runtime_attempt_no
        or (attempt_no is not None and attempt_no != data.runtime_attempt_no)
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_envelope_mismatch"
        )


def _require_entry_replay(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    action_id: str,
    data: _NeedsAttentionAdmissionData,
    checkpoint: RuntimeCheckpoint,
    envelope: FailureEnvelopeV1,
    expected_state_revision: int,
    executor_id: str | None,
    attempt_no: int | None,
) -> sqlite3.Row:
    action_row = _action_row(conn, action_id)
    try:
        stored_envelope = load_failure_envelope_revision(
            conn,
            failure_id=envelope.failure_id,
            revision=envelope.revision,
        )
    except FailureEnvelopeStorageError:
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        ) from None
    owner_supplied = type(executor_id) is str and type(attempt_no) is int
    if not owner_supplied and (
        executor_id is not None or attempt_no is not None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    if (
        int(row["state_revision"]) != expected_state_revision + 1
        or row["current_action_id"] != action_id
        or action_row is None
        or action_row["status"] != "pending"
        or _canonical_action_from_row(action_row) != data.action
        or action_row["operation_id"] != data.operation_id
        or action_row["checkpoint_id"] != checkpoint.checkpoint_id
        or action_row["checkpoint_hash"] != _checkpoint_hash(checkpoint)
        or action_row["failure_id"] != envelope.failure_id
        or action_row["failure_revision"] != envelope.revision
        or canonical_diagnostics_bytes(stored_envelope)
        != canonical_diagnostics_bytes(envelope)
        or action_row["entry_observation_ref"]
        != data.entry_observation_ref
        or action_row["entry_observation_digest"]
        != data.entry_observation_digest
        or action_row["accepted_requirement_revision_id"]
        != data.accepted_requirement_revision_id
        or action_row["runtime_attempt_no"] != data.runtime_attempt_no
        or action_row["runtime_attempt_fence_ref"]
        != data.runtime_attempt_fence_ref
        or action_row["request_hash"] != data.request_hash
        or action_row["profile_binding_generation"]
        != data.profile_binding_generation
        or action_row["browser_control_scope_id"]
        != data.browser_control_scope_id
        or action_row["source_ledger_revision"]
        != data.source_ledger_revision
        or action_row["source_reconciliation_revision"]
        != data.source_reconciliation_revision
        or action_row["dispatch_intent_id"] != data.dispatch_intent_id
        or action_row["dispatch_intent_digest"]
        != data.dispatch_intent_digest
        or action_row["source_operation_acceptance_ref"]
        != data.source_operation_acceptance_ref
        or action_row["reconciliation_id"] != data.reconciliation_id
        or action_row["reconciliation_digest"]
        != data.reconciliation_digest
        or (action_row["authority_mode"] == "active_owner")
        != owner_supplied
        or _active_lease_row(conn, row["runtime_run_id"]) is not None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    if owner_supplied:
        lease = conn.execute(
            """
            SELECT * FROM runtime_control_executor_leases
            WHERE lease_id = ? AND runtime_run_id = ?
            """,
            (action_row["owner_lease_id"], row["runtime_run_id"]),
        ).fetchone()
        if (
            lease is None
            or lease["executor_id"] != executor_id
            or lease["attempt_no"] != attempt_no
            or lease["status"] != "revoked"
            or lease["reason_code"] != "runtime_needs_attention"
        ):
            raise RuntimeControlError(
                "runtime_needs_attention_replay_conflict"
            )
    validate_needs_attention_row(conn, row)
    validate_failed_outcome_row(conn, row)
    return row


def _require_exit_replay(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    action_row: sqlite3.Row,
    expected_state_revision: int,
    target_status: str,
    resolution_evidence_ref: str,
    resolution_binding_digest: str,
    resolved_at: str,
    failed_envelope: FailureEnvelopeV1 | None,
    satisfaction: _ActionSatisfactionData | None,
) -> sqlite3.Row:
    expected_outcome = None if target_status == "resume_requested" else target_status
    if (
        int(row["state_revision"]) != expected_state_revision + 1
        or row["product_outcome"] != expected_outcome
        or row["current_action_id"] is not None
        or action_row["resolution_evidence_ref"]
        != resolution_evidence_ref
        or action_row["resolution_binding_digest"]
        != resolution_binding_digest
        or action_row["resolution_at"] != resolved_at
        or _active_lease_row(conn, row["runtime_run_id"]) is not None
        or (
            satisfaction is not None
            and (
                action_row["resolution_operation_id"]
                != satisfaction.operation_id
                or action_row["resolution_result_digest"]
                != satisfaction.result_digest
                or action_row["resolution_request_hash"]
                != satisfaction.request_hash
                or action_row["resolution_request_semantic_digest"]
                != satisfaction.request_semantic_digest
                or action_row["resolution_runtime_attempt_fence_ref"]
                != satisfaction.runtime_attempt_fence_ref
                or action_row["resolution_dispatch_authorization_ordinal"]
                != satisfaction.dispatch_authorization_ordinal
                or action_row["resolution_reconciliation_id"]
                != satisfaction.reconciliation_id
                or action_row["resolution_reconciliation_digest"]
                != satisfaction.reconciliation_digest
                or action_row["resolution_source_ledger_revision"]
                != satisfaction.source_ledger_revision
                or action_row["resolution_source_reconciliation_revision"]
                != satisfaction.source_reconciliation_revision
            )
        )
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    if failed_envelope is not None and (
        row["current_failure_id"] != failed_envelope.failure_id
        or row["current_failure_revision"] != failed_envelope.revision
        or load_failure_envelope_revision(
            conn,
            failure_id=failed_envelope.failure_id,
            revision=failed_envelope.revision,
        )
        != failed_envelope
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_replay_conflict"
        )
    validate_needs_attention_row(conn, row)
    validate_failed_outcome_row(conn, row)
    return row


def _require_checkpoint_binding(
    conn: sqlite3.Connection,
    action_row: sqlite3.Row,
    checkpoint: RuntimeCheckpoint,
) -> None:
    if (
        _checkpoint_hash(checkpoint) != action_row["checkpoint_hash"]
        or _candidate_truth_hash(checkpoint)
        != action_row["candidate_truth_hash"]
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        )
    from seektalent_runtime_control.store import (
        _candidate_truth_matches_checkpoint,
    )

    if not _candidate_truth_matches_checkpoint(conn, checkpoint):
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        )


def _checkpoint_from_action(
    conn: sqlite3.Connection,
    action_row: sqlite3.Row,
) -> RuntimeCheckpoint:
    row = conn.execute(
        """
        SELECT * FROM runtime_control_checkpoints
        WHERE checkpoint_id = ? AND runtime_run_id = ?
        """,
        (action_row["checkpoint_id"], action_row["runtime_run_id"]),
    ).fetchone()
    if row is None:
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        )
    try:
        return RuntimeCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            runtime_run_id=row["runtime_run_id"],
            stage=row["stage"],
            round_no=row["round_no"],
            safe_boundary=row["safe_boundary"],
            run_state=json.loads(row["run_state_json"]),
            source_plan=json.loads(row["source_plan_json"]),
            pending_commands=json.loads(row["pending_commands_json"]),
            artifact_manifest_ref=row["artifact_manifest_ref"],
            schema_version=row["schema_version"],
            created_at=row["created_at"],
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeControlError(
            "runtime_needs_attention_checkpoint_mismatch"
        ) from None


def _admit_envelope(
    envelope: FailureEnvelopeV1 | bytes,
) -> FailureEnvelopeV1:
    try:
        if type(envelope) is bytes:
            return parse_failure_envelope(envelope)
        if type(envelope) is FailureEnvelopeV1:
            return parse_failure_envelope(
                canonical_diagnostics_bytes(envelope)
            )
    except ValueError:
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        ) from None
    raise RuntimeControlError(
        "runtime_needs_attention_admission_rejected"
    )


def _require_no_reconcile_first(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> None:
    if conn.execute(
        """
        SELECT 1
        FROM runtime_control_source_operations
        WHERE runtime_run_id = ? AND retry_posture = 'reconcile_first'
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone() is not None:
        raise RuntimeControlError(
            "runtime_needs_attention_reconciliation_unresolved"
        )


def _checkpoint_hash(checkpoint: RuntimeCheckpoint) -> str:
    payload = checkpoint.model_dump(mode="json")
    return sha256(_canonical_json(payload)).hexdigest()


def _candidate_truth_hash(checkpoint: RuntimeCheckpoint) -> str:
    from seektalent_runtime_control.candidates import (
        candidate_truth_from_run_state,
    )

    truth = candidate_truth_from_run_state(
        runtime_run_id=checkpoint.runtime_run_id,
        run_state=checkpoint.run_state,
        source_checkpoint_id=checkpoint.checkpoint_id,
        observed_at=checkpoint.created_at,
    )
    payload = {
        "identities": [
            item.model_dump(mode="json") for item in truth.identities
        ],
        "evidence": [
            item.model_dump(mode="json") for item in truth.evidence
        ],
        "finalization_revisions": [
            item.model_dump(mode="json")
            for item in truth.finalization_revisions
        ],
    }
    return sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _run_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (runtime_run_id,),
    ).fetchone()


def _action_row(
    conn: sqlite3.Connection,
    action_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_user_actions WHERE action_id = ?",
        (action_id,),
    ).fetchone()


def _active_lease_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND status = 'active'
        ORDER BY attempt_no DESC
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()


def _require_revision(value: int) -> None:
    if (
        type(value) is not int
        or value < 0
        or value > _MAX_SAFE_INTEGER
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_revision_conflict"
        )


def _require_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (AttributeError, TypeError, ValueError):
        raise RuntimeControlError(
            "runtime_needs_attention_time_invalid"
        ) from None


def _sha256_hex(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _opaque(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value.encode("utf-8")) <= 96
        and value.strip() == value
        and "\x00" not in value
    )


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        return


__all__ = [
    "ActionSatisfactionAdmission",
    "NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS",
    "NeedsAttentionAdmission",
    "admit_action_satisfaction",
    "admit_needs_attention",
    "cancel_needs_attention",
    "commit_needs_attention",
    "fail_needs_attention",
    "migrate_needs_attention_v14_to_v15",
    "resolve_needs_attention",
    "validate_needs_attention_row",
    "validate_needs_attention_schema",
]
