from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from seektalent.diagnostics_schema import parse_failure_envelope
from seektalent.source_port.operation_dispatch import (
    OperationIdentityV1,
    RelativeMonotonicDeadlineV1,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionResultV1,
    VerifySessionUserActionV1,
)
from seektalent.user_action import (
    USER_ACTION_INSTRUCTIONS,
    USER_ACTION_SCOPES,
    UserActionV1,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeRunRecord
from seektalent_runtime_control.needs_attention import (
    admit_action_satisfaction,
    admit_needs_attention,
)
from seektalent_runtime_control.checkpoint_participant import (
    write_checkpoint_participant,
)
from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore
from seektalent_runtime_control.user_action_mapping import (
    map_verify_session_user_action,
)
from tests.test_diagnostics_schema import _failure


def test_needs_attention_apis_have_zero_production_callers() -> None:
    root = Path(__file__).parents[1] / "src"
    api_names = {
        "commit_needs_attention",
        "resolve_needs_attention",
        "cancel_needs_attention",
        "fail_needs_attention",
        "write_checkpoint_for_recovery",
    }
    callers: list[str] = []
    for path in root.rglob("*.py"):
        if (
            path.parent.name == "seektalent_runtime_control"
            and path.name in {"store.py", "needs_attention_store.py"}
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if name in api_names:
                callers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert callers == []


RUN_ID = "3" * 32
OPERATION_ID = "4" * 32
CHECKPOINT_ID = "8" * 32
ACTION_ID = "9" * 32
ENTERED_AT = "2026-07-27T04:00:00Z"
RESOLVED_AT = "2026-07-27T04:05:00Z"


def _action(code: str = "open_liepin_host") -> UserActionV1:
    return map_verify_session_user_action(
        VerifySessionUserActionV1(
            code={
                "open_liepin_host": "liepin_host_tab_missing",
                "complete_identity_check": "liepin_opencli_identity_intercept",
                "log_in_to_liepin": "liepin_opencli_login_required",
                "complete_liepin_risk_check": "liepin_opencli_risk_page",
                "resolve_liepin_modal": "liepin_opencli_unknown_modal",
            }[code],
            instruction_key={
                "open_liepin_host": "verify_session.open_liepin_host",
                "complete_identity_check": "verify_session.complete_identity_check",
                "log_in_to_liepin": "verify_session.log_in",
                "complete_liepin_risk_check": "verify_session.complete_risk_check",
                "resolve_liepin_modal": "verify_session.dismiss_or_resolve_modal",
            }[code],
        ),
        affected_scope_ref="6" * 32,
    )


def _verify_result(
    *,
    ready: bool,
    code: str = "open_liepin_host",
) -> VerifySessionResultV1:
    source_action = None
    if not ready:
        canonical = _action(code)
        source_action = VerifySessionUserActionV1(
            code={
                "open_liepin_host": "liepin_host_tab_missing",
                "complete_identity_check": "liepin_opencli_identity_intercept",
                "log_in_to_liepin": "liepin_opencli_login_required",
                "complete_liepin_risk_check": "liepin_opencli_risk_page",
                "resolve_liepin_modal": "liepin_opencli_unknown_modal",
            }[canonical.code],
            instruction_key={
                "open_liepin_host": "verify_session.open_liepin_host",
                "complete_identity_check": "verify_session.complete_identity_check",
                "log_in_to_liepin": "verify_session.log_in",
                "complete_liepin_risk_check": "verify_session.complete_risk_check",
                "resolve_liepin_modal": "verify_session.dismiss_or_resolve_modal",
            }[canonical.code],
        )
    identity = OperationIdentityV1(
        run_id=RUN_ID,
        operation_id=OPERATION_ID,
        attempt_no=1,
        source="liepin",
        operation_kind="verify_session",
        request_hash="a" * 64,
        idempotency_key="verify-session-action",
        correlation_id="2" * 32,
        accepted_requirement_revision_id="reqapproved_test",
        runtime_attempt_fence_ref="b" * 64,
        profile_binding_generation=1,
        browser_control_scope_id="6" * 32,
        deadline=RelativeMonotonicDeadlineV1(
            value=30_000,
            clock="relative_monotonic",
            unit="milliseconds",
        ),
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
    )
    return VerifySessionResultV1(
        contract_version="seektalent.source.verify-session.result/v1",
        identity=identity,
        process_readiness="ready",
        bridge_readiness="ready",
        extension_readiness="ready",
        profile_lock_readiness="ready",
        account_readiness="ready",
        search_surface_readiness="ready" if ready else "not_ready",
        risk_state="clear",
        session_readiness="ready" if ready else "not_ready",
        actual_profile_binding_ref="5" * 32,
        actual_provider_account_ref="4" * 32,
        actual_profile_binding_generation=2,
        safe_reason_code=None if ready else source_action.code,
        user_action=source_action,
        component_receipt_refs=(),
    )


def _envelope(
    *,
    outcome: str = "needs_attention",
    action: UserActionV1 | None = None,
    failure_id: str = "7" * 32,
    revision: int = 1,
    occurred_at: str = ENTERED_AT,
):
    payload = _failure()
    payload.update(
        {
            "run_id": RUN_ID,
            "operation_id": OPERATION_ID,
            "failure_id": failure_id,
            "revision": revision,
            "current_outcome": outcome,
            "reason_code": "user_action_required" if outcome == "needs_attention" else "source_operation_failed",
            "component": "main",
            "phase": "observe" if outcome == "needs_attention" else "execute",
            "domain": "user_action" if outcome == "needs_attention" else "source",
            "failure_kind": "operation_failure",
            "detail": {} if outcome == "needs_attention" else payload["detail"],
            "occurred_at": occurred_at,
            "observed_at": occurred_at,
            "user_action": None if action is None else action.model_dump(mode="json"),
        }
    )
    return parse_failure_envelope(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )


def _checkpoint() -> RuntimeCheckpoint:
    return RuntimeCheckpoint(
        checkpoint_id=CHECKPOINT_ID,
        runtime_run_id=RUN_ID,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        run_state={"round": 1},
        source_plan={"sourceIds": ["liepin"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-07-27T03:59:00Z",
    )


def _store(tmp_path: Path, *, status: str = "resume_requested") -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=RUN_ID,
            run_intent_id=f"intent_{RUN_ID}",
            start_idempotency_key=f"start_{RUN_ID}",
            approved_requirement_revision_id="reqapproved_test",
            status=status,
            current_stage=status,
            source_ids=["liepin"],
            created_at="2026-07-27T03:00:00Z",
            updated_at="2026-07-27T03:00:00Z",
        )
    )
    return store


def _entry_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)
    checkpoint = _checkpoint()

    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(93)

    store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admit_needs_attention(
            result=_verify_result(ready=False),
            checkpoint_id=CHECKPOINT_ID,
            frozen_required_source_ids=("liepin",),
            reconciliation_evidence_ref="a" * 64,
        ),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
        statement_hook=crash,
    )


def _resolution_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)
    action = _action()

    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(94)

    store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admit_action_satisfaction(
            action=action,
            result=_verify_result(ready=True),
            checkpoint_id=CHECKPOINT_ID,
            authenticated_evidence_ref="b" * 64,
        ),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        resolved_at=RESOLVED_AT,
        statement_hook=crash,
    )


def _cancel_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)

    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(95)

    store.cancel_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        cancelled_at=RESOLVED_AT,
        cancellation_evidence_ref="c" * 64,
        statement_hook=crash,
    )


def _failure_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)

    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(96)

    store.fail_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        envelope=_envelope(
            outcome="failed",
            action=None,
            failure_id="d" * 32,
            occurred_at=RESOLVED_AT,
        ),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        terminal_reason_code="source_operation_failed",
        terminal_at=RESOLVED_AT,
        statement_hook=crash,
    )


def test_verify_session_mapping_is_total_closed_and_scope_bound() -> None:
    expected = {
        "liepin_host_tab_missing": "open_liepin_host",
        "liepin_opencli_identity_intercept": "complete_identity_check",
        "liepin_opencli_login_required": "log_in_to_liepin",
        "liepin_opencli_risk_page": "complete_liepin_risk_check",
        "liepin_opencli_unknown_modal": "resolve_liepin_modal",
    }
    for source_code, canonical_code in expected.items():
        source_action = VerifySessionUserActionV1(
            code=source_code,
            instruction_key={
                "liepin_host_tab_missing": "verify_session.open_liepin_host",
                "liepin_opencli_identity_intercept": "verify_session.complete_identity_check",
                "liepin_opencli_login_required": "verify_session.log_in",
                "liepin_opencli_risk_page": "verify_session.complete_risk_check",
                "liepin_opencli_unknown_modal": "verify_session.dismiss_or_resolve_modal",
            }[source_code],
        )
        mapped = map_verify_session_user_action(
            source_action,
            affected_scope_ref="6" * 32,
        )
        assert mapped.code == canonical_code
        assert mapped.instruction_key == USER_ACTION_INSTRUCTIONS[canonical_code]
        assert mapped.scope == USER_ACTION_SCOPES[canonical_code]
        assert mapped.affected_scope_ref == "6" * 32

    with pytest.raises((TypeError, ValueError)):
        map_verify_session_user_action(object(), affected_scope_ref="6" * 32)


def test_needs_attention_envelope_requires_one_canonical_action() -> None:
    action = _action()
    assert _envelope(action=action).user_action == action
    with pytest.raises(ValueError):
        _envelope(action=None)
    with pytest.raises(ValueError):
        _envelope(outcome="failed", action=action)


def test_runtime_control_v15_fresh_schema_has_action_history_and_pointer(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        run_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(runtime_control_runs)")
        }
        action_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runtime_control_user_actions'"
        ).fetchone()
    assert RUNTIME_CONTROL_SCHEMA_VERSION == version == 15
    assert "current_action_id" in run_columns
    assert action_sql is not None


def test_no_owner_entry_and_resolution_retain_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    action = _action()
    admission = admit_needs_attention(
        result=_verify_result(ready=False),
        checkpoint_id=CHECKPOINT_ID,
        frozen_required_source_ids=("liepin",),
        reconciliation_evidence_ref="a" * 64,
    )
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admission,
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    assert entered.status == "needs_attention"
    assert entered.product_outcome == "needs_attention"
    assert entered.current_action_id == ACTION_ID

    satisfaction = admit_action_satisfaction(
        action=action,
        result=_verify_result(ready=True),
        checkpoint_id=CHECKPOINT_ID,
        authenticated_evidence_ref="b" * 64,
    )
    resumed = store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=satisfaction,
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    replay = store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=satisfaction,
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    assert replay == resumed
    assert resumed.status == "resume_requested"
    assert resumed.product_outcome is None
    assert resumed.current_action_id is None
    assert resumed.current_failure_id is None
    [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
    assert historical.status == "resolved"
    assert historical.resolution_evidence_ref == "b" * 64


def test_cancellation_and_failure_terminal_exits_retain_action_history(
    tmp_path: Path,
) -> None:
    for exit_kind in ("cancelled", "failed"):
        case_path = tmp_path / exit_kind
        store = _store(case_path)
        checkpoint = _checkpoint()
        store.write_checkpoint_for_recovery(checkpoint)
        action = _action()
        entered = store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admit_needs_attention(
                result=_verify_result(ready=False),
                checkpoint_id=CHECKPOINT_ID,
                frozen_required_source_ids=("liepin",),
                reconciliation_evidence_ref="a" * 64,
            ),
            checkpoint=checkpoint,
            envelope=_envelope(action=action),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
        )
        if exit_kind == "cancelled":
            terminal = store.cancel_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                expected_state_revision=entered.state_revision,
                cancelled_at=RESOLVED_AT,
                cancellation_evidence_ref="c" * 64,
            )
            replay = store.cancel_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                expected_state_revision=entered.state_revision,
                cancelled_at=RESOLVED_AT,
                cancellation_evidence_ref="c" * 64,
            )
        else:
            terminal = store.fail_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                envelope=_envelope(
                    outcome="failed",
                    action=None,
                    failure_id="d" * 32,
                    occurred_at=RESOLVED_AT,
                ),
                expected_state_revision=entered.state_revision,
                terminal_reason_code="source_operation_failed",
                terminal_at=RESOLVED_AT,
            )
            replay = store.fail_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                envelope=_envelope(
                    outcome="failed",
                    action=None,
                    failure_id="d" * 32,
                    occurred_at=RESOLVED_AT,
                ),
                expected_state_revision=entered.state_revision,
                terminal_reason_code="source_operation_failed",
                terminal_at=RESOLVED_AT,
            )
        assert replay == terminal
        assert terminal.status == exit_kind
        assert terminal.product_outcome == exit_kind
        assert terminal.current_action_id is None
        [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
        assert historical.status == exit_kind


def test_action_admissions_are_factory_only_and_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError):
        type(admit_needs_attention(
            result=_verify_result(ready=False),
            checkpoint_id=CHECKPOINT_ID,
            frozen_required_source_ids=("liepin",),
            reconciliation_evidence_ref="a" * 64,
        ))()

    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admit_needs_attention(
                result=_verify_result(ready=False),
                checkpoint_id=CHECKPOINT_ID,
                frozen_required_source_ids=("liepin",),
                reconciliation_evidence_ref="a" * 64,
            ),
            checkpoint=checkpoint,
            envelope=_envelope(action=_action("log_in_to_liepin")),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
        )
    assert exc_info.value.reason_code == "runtime_needs_attention_envelope_mismatch"


def test_active_owner_entry_revokes_exact_lease_and_exact_replay_is_read_only(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T03:30:00Z",
        lease_expires_at="2099-01-01T00:00:00Z",
    )
    checkpoint = _checkpoint()
    action = _action()
    expected_revision = store.get_run(RUN_ID).state_revision
    kwargs = {
        "runtime_run_id": RUN_ID,
        "action_id": ACTION_ID,
        "admission": admit_needs_attention(
            result=_verify_result(ready=False),
            checkpoint_id=CHECKPOINT_ID,
            frozen_required_source_ids=("liepin",),
            reconciliation_evidence_ref="a" * 64,
        ),
        "checkpoint": checkpoint,
        "envelope": _envelope(action=action),
        "expected_state_revision": expected_revision,
        "entered_at": ENTERED_AT,
        "executor_id": lease.executor_id,
        "attempt_no": lease.attempt_no,
    }
    entered = store.commit_needs_attention(**kwargs)
    replay = store.commit_needs_attention(**kwargs)
    assert entered == replay
    assert entered.current_failure_authority_mode == "active_owner"
    assert entered.current_failure_owner_lease_id == lease.lease_id
    with sqlite3.connect(store.path) as conn:
        persisted_lease = conn.execute(
            "SELECT status, reason_code FROM runtime_control_executor_leases WHERE lease_id = ?",
            (lease.lease_id,),
        ).fetchone()
    assert persisted_lease == ("revoked", "runtime_needs_attention")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            **{
                **kwargs,
                "executor_id": "executor-other",
            }
        )
    assert exc_info.value.reason_code == "runtime_needs_attention_replay_conflict"


def test_active_owner_entry_rejects_expired_lease_authority(tmp_path: Path) -> None:
    store = _store(tmp_path, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T03:30:00Z",
        lease_expires_at=ENTERED_AT,
    )
    checkpoint = _checkpoint()

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admit_needs_attention(
                result=_verify_result(ready=False),
                checkpoint_id=CHECKPOINT_ID,
                frozen_required_source_ids=("liepin",),
                reconciliation_evidence_ref="a" * 64,
            ),
            checkpoint=checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
        )

    assert exc_info.value.reason_code == "runtime_needs_attention_authority_rejected"
    assert store.get_run(RUN_ID).status == "running"


def test_entry_honours_cancellation_precedence_and_no_owner_reconciliation_gate(
    tmp_path: Path,
) -> None:
    cancelled_store = _store(tmp_path / "cancelled", status="cancellation_requested")
    with pytest.raises(RuntimeControlError) as cancel_exc:
        cancelled_store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admit_needs_attention(
                result=_verify_result(ready=False),
                checkpoint_id=CHECKPOINT_ID,
                frozen_required_source_ids=("liepin",),
                reconciliation_evidence_ref="a" * 64,
            ),
            checkpoint=_checkpoint(),
            envelope=_envelope(action=_action()),
            expected_state_revision=0,
            entered_at=ENTERED_AT,
        )
    assert cancel_exc.value.reason_code == "runtime_needs_attention_cancellation_won"

    store = _store(tmp_path / "reconcile")
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_source_operations (
                runtime_run_id, operation_id, source_id, operation_kind,
                canonical_request_hash, idempotency_key,
                accepted_requirement_revision_id, runtime_attempt_no,
                runtime_attempt_authority_ref, operation_phase,
                dispatch_intent_ref, conclusive_observation_ref,
                source_operation_disposition, retry_posture,
                reconciliation_revision, main_commit_ref, ledger_revision
            )
            VALUES (?, ?, 'liepin', 'verify_session', ?, ?, ?, 1, ?,
                    'accepted', NULL, NULL, 'reconciliation_unknown',
                    'reconcile_first', 1, NULL, 1)
            """,
            (
                RUN_ID,
                OPERATION_ID,
                "a" * 64,
                "needs-attention-reconcile",
                "reqapproved_test",
                "authority-ref",
            ),
        )
    with pytest.raises(RuntimeControlError) as reconcile_exc:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admit_needs_attention(
                result=_verify_result(ready=False),
                checkpoint_id=CHECKPOINT_ID,
                frozen_required_source_ids=("liepin",),
                reconciliation_evidence_ref="a" * 64,
            ),
            checkpoint=checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
        )
    assert (
        reconcile_exc.value.reason_code
        == "runtime_needs_attention_reconciliation_unresolved"
    )


def test_checkpoint_participant_never_owns_transaction_timing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(RuntimeControlError) as no_transaction:
            write_checkpoint_participant(conn, checkpoint)
        assert (
            no_transaction.value.reason_code
            == "runtime_checkpoint_transaction_required"
        )
        conn.execute("BEGIN IMMEDIATE")
        write_checkpoint_participant(conn, checkpoint)
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute(
            "SELECT 1 FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
            (CHECKPOINT_ID,),
        ).fetchone() is None


def test_v14_to_v15_migration_rejects_incomplete_truth_and_partial_schema(
    tmp_path: Path,
) -> None:
    for poisoning in ("incomplete_truth", "partial_schema"):
        path = tmp_path / poisoning / "runtime_control.sqlite3"
        store = RuntimeControlStore(path)
        store.initialize()
        if poisoning == "incomplete_truth":
            store.create_run(
                RuntimeRunRecord(
                    runtime_run_id=RUN_ID,
                    approved_requirement_revision_id="reqapproved_test",
                    status="queued",
                    current_stage="queued",
                    source_ids=["liepin"],
                    created_at="2026-07-27T03:00:00Z",
                    updated_at="2026-07-27T03:00:00Z",
                )
            )
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TRIGGER runtime_user_actions_delete_forbidden")
            conn.execute("DROP TRIGGER runtime_user_actions_one_way_resolution")
            conn.execute("DROP TRIGGER runtime_user_actions_immutable_binding")
            conn.execute("DROP INDEX idx_runtime_user_actions_run_created")
            conn.execute("DROP INDEX idx_runtime_user_actions_one_pending")
            conn.execute("DROP TABLE runtime_control_user_actions")
            conn.execute(
                "ALTER TABLE runtime_control_runs DROP COLUMN current_action_id"
            )
            conn.execute("PRAGMA user_version = 14")
            if poisoning == "incomplete_truth":
                conn.execute(
                    "UPDATE runtime_control_runs SET status = 'needs_attention'"
                )
            else:
                conn.execute(
                    "ALTER TABLE runtime_control_runs ADD COLUMN current_action_id TEXT"
                )
        with pytest.raises(RuntimeControlError) as exc_info:
            RuntimeControlStore(path).initialize()
        assert exc_info.value.reason_code in {
            "runtime_needs_attention_incomplete_migration",
            "runtime_needs_attention_schema_collision",
        }
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 14


@pytest.mark.parametrize("poisoning", ("extra", "reordered", "constraint"))
def test_claimed_v15_poisoned_action_schema_fails_closed(
    tmp_path: Path,
    poisoning: str,
) -> None:
    from seektalent_runtime_control import needs_attention as module

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    statements = module.NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER runtime_user_actions_delete_forbidden")
        conn.execute("DROP TRIGGER runtime_user_actions_one_way_resolution")
        conn.execute("DROP TRIGGER runtime_user_actions_immutable_binding")
        conn.execute("DROP INDEX idx_runtime_user_actions_run_created")
        conn.execute("DROP INDEX idx_runtime_user_actions_one_pending")
        conn.execute("DROP TABLE runtime_control_user_actions")
        conn.execute(
            "ALTER TABLE runtime_control_runs DROP COLUMN current_action_id"
        )
        conn.execute(statements[0])
        table_sql = statements[1]
        if poisoning == "extra":
            table_sql = table_sql.replace(
                "      created_at TEXT NOT NULL,",
                "      created_at TEXT NOT NULL,\n      poisoned TEXT,",
            )
        elif poisoning == "reordered":
            table_sql = table_sql.replace(
                "      action_code TEXT NOT NULL,\n"
                "      instruction_key TEXT NOT NULL,",
                "      instruction_key TEXT NOT NULL,\n"
                "      action_code TEXT NOT NULL,",
            )
        else:
            table_sql = table_sql.replace(
                "'pending', 'resolved', 'cancelled', 'failed'",
                "'pending', 'resolved', 'cancelled'",
            )
        conn.execute(table_sql)
        for statement in statements[2:]:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 15")

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeControlStore(path).initialize()
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_schema_collision"
    )


@pytest.mark.parametrize("completed_statements", range(0, 7))
def test_v14_to_v15_statement_failure_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_statements: int,
) -> None:
    from seektalent_runtime_control import needs_attention as module

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    statements = module.NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER runtime_user_actions_delete_forbidden")
        conn.execute("DROP TRIGGER runtime_user_actions_one_way_resolution")
        conn.execute("DROP TRIGGER runtime_user_actions_immutable_binding")
        conn.execute("DROP INDEX idx_runtime_user_actions_run_created")
        conn.execute("DROP INDEX idx_runtime_user_actions_one_pending")
        conn.execute("DROP TABLE runtime_control_user_actions")
        conn.execute(
            "ALTER TABLE runtime_control_runs DROP COLUMN current_action_id"
        )
        conn.execute("PRAGMA user_version = 14")
    monkeypatch.setattr(
        module,
        "NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS",
        (
            *statements[:completed_statements],
            "ALTER TABL runtime_control_runs injected_invalid_statement",
        ),
    )
    with pytest.raises(sqlite3.OperationalError):
        RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(runtime_control_runs)"
            )
        }
        assert "current_action_id" not in columns
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name LIKE '%user_actions%'"
        ).fetchone() is None
    monkeypatch.setattr(
        module,
        "NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS",
        statements,
    )
    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15


@pytest.mark.parametrize("hook_index", range(0, 11))
def test_entry_statement_failures_leave_only_old_truth(
    tmp_path: Path,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    before = store.get_run(RUN_ID)

    def fail(index: int, _phase: str) -> None:
        if index == hook_index:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admit_needs_attention(
                result=_verify_result(ready=False),
                checkpoint_id=CHECKPOINT_ID,
                frozen_required_source_ids=("liepin",),
                reconciliation_evidence_ref="a" * 64,
            ),
            checkpoint=checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=before.state_revision,
            entered_at=ENTERED_AT,
            statement_hook=fail,
        )
    assert store.get_run(RUN_ID) == before
    assert store.list_user_actions(runtime_run_id=RUN_ID) == []


def test_exit_rejects_stale_evidence_and_retains_checkpoint_envelope_action(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    action = _action()
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admit_needs_attention(
            result=_verify_result(ready=False),
            checkpoint_id=CHECKPOINT_ID,
            frozen_required_source_ids=("liepin",),
            reconciliation_evidence_ref="a" * 64,
        ),
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    with pytest.raises(RuntimeControlError) as stale:
        store.resolve_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admit_action_satisfaction(
                action=action,
                result=_verify_result(ready=True),
                checkpoint_id=CHECKPOINT_ID,
                authenticated_evidence_ref="b" * 64,
            ),
            expected_state_revision=entered.state_revision - 1,
            resolved_at=RESOLVED_AT,
        )
    assert stale.value.reason_code == "runtime_needs_attention_revision_conflict"
    terminal = store.cancel_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        expected_state_revision=entered.state_revision,
        cancelled_at=RESOLVED_AT,
        cancellation_evidence_ref="c" * 64,
    )
    assert terminal.status == "cancelled"
    assert store.delete_terminal_checkpoints(
        older_than="2099-01-01T00:00:00Z",
        batch_size=100,
    ) == 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT 1 FROM runtime_control_failure_envelope_revisions WHERE failure_id = ?",
            ("7" * 32,),
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
            (CHECKPOINT_ID,),
        ).fetchone() is not None


@pytest.mark.parametrize("hook_index", range(0, 12))
def test_entry_subprocess_crash_exposes_only_old_or_complete_new_truth(
    tmp_path: Path,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    before_revision = store.get_run(RUN_ID).state_revision
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.test_runtime_control_needs_attention "
                "import _entry_crash_child; "
                f"_entry_crash_child({str(store.path)!r}, {hook_index})"
            ),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=20,
    )
    assert completed.returncode == 93
    readback = store.get_run(RUN_ID)
    if hook_index < 11:
        assert readback.status == "resume_requested"
        assert readback.state_revision == before_revision
        assert store.list_user_actions(runtime_run_id=RUN_ID) == []
    else:
        assert readback.status == "needs_attention"
        assert readback.current_action_id == ACTION_ID
        assert len(store.list_user_actions(runtime_run_id=RUN_ID)) == 1


@pytest.mark.parametrize("hook_index", range(2, 8))
def test_resolution_subprocess_crash_exposes_only_old_or_complete_new_truth(
    tmp_path: Path,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    action = _action()
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admit_needs_attention(
            result=_verify_result(ready=False),
            checkpoint_id=CHECKPOINT_ID,
            frozen_required_source_ids=("liepin",),
            reconciliation_evidence_ref="a" * 64,
        ),
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.test_runtime_control_needs_attention "
                "import _resolution_crash_child; "
                f"_resolution_crash_child({str(store.path)!r}, {hook_index})"
            ),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=20,
    )
    assert completed.returncode == 94
    readback = store.get_run(RUN_ID)
    [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
    if hook_index < 7:
        assert readback == entered
        assert historical.status == "pending"
    else:
        assert readback.status == "resume_requested"
        assert readback.current_action_id is None
        assert historical.status == "resolved"


def test_action_history_bindings_cannot_be_updated_or_deleted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admit_needs_attention(
            result=_verify_result(ready=False),
            checkpoint_id=CHECKPOINT_ID,
            frozen_required_source_ids=("liepin",),
            reconciliation_evidence_ref="a" * 64,
        ),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE runtime_control_user_actions SET checkpoint_id = ? WHERE action_id = ?",
                ("poisoned", ACTION_ID),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM runtime_control_user_actions WHERE action_id = ?",
                (ACTION_ID,),
            )


@pytest.mark.parametrize(
    ("exit_kind", "hook_index"),
    [
        *(("cancelled", index) for index in range(2, 8)),
        *(("failed", index) for index in range(0, 8)),
    ],
)
def test_terminal_exit_subprocess_crash_exposes_only_old_or_complete_new_truth(
    tmp_path: Path,
    exit_kind: str,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    action = _action()
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admit_needs_attention(
            result=_verify_result(ready=False),
            checkpoint_id=CHECKPOINT_ID,
            frozen_required_source_ids=("liepin",),
            reconciliation_evidence_ref="a" * 64,
        ),
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    child = (
        "_cancel_crash_child"
        if exit_kind == "cancelled"
        else "_failure_crash_child"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.test_runtime_control_needs_attention "
                f"import {child}; "
                f"{child}({str(store.path)!r}, {hook_index})"
            ),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=20,
    )
    assert completed.returncode == (
        95 if exit_kind == "cancelled" else 96
    )
    readback = store.get_run(RUN_ID)
    [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
    after_commit_hook = 7
    if hook_index < after_commit_hook:
        assert readback == entered
        assert historical.status == "pending"
    else:
        assert readback.status == exit_kind
        assert readback.current_action_id is None
        assert historical.status == exit_kind
