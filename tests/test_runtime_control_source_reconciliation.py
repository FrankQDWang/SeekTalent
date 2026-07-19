from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
import sqlite3

import pytest


REQUEST_HASH = "a" * 64
HISTORY_DIGEST = "c" * 64
SQLITE_INTEGER_MAX = 2**63 - 1


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"reconciliation_id": 1}, "source_reconciliation_reconciliation_id_invalid"),
        ({"canonical_request_hash": "A" * 64}, "source_reconciliation_canonical_request_hash_invalid"),
        ({"history_result_digest": "short"}, "source_reconciliation_history_result_digest_invalid"),
        ({"decision_kind": []}, "source_reconciliation_decision_kind_invalid"),
        ({"history_outcome": "identity_conflict"}, "source_reconciliation_history_outcome_invalid"),
        ({"expected_ledger_revision": True}, "source_reconciliation_expected_ledger_revision_invalid"),
        ({"expected_ledger_revision": 1.0}, "source_reconciliation_expected_ledger_revision_invalid"),
        (
            {"expected_reconciliation_revision": True},
            "source_reconciliation_expected_reconciliation_revision_invalid",
        ),
        (
            {"expected_reconciliation_revision": 1.0},
            "source_reconciliation_expected_reconciliation_revision_invalid",
        ),
        (
            {"expected_ledger_revision": SQLITE_INTEGER_MAX},
            "source_reconciliation_expected_ledger_revision_invalid",
        ),
        (
            {"expected_reconciliation_revision": SQLITE_INTEGER_MAX},
            "source_reconciliation_expected_reconciliation_revision_invalid",
        ),
        ({"history_outcome": "matched"}, "source_reconciliation_history_matrix_invalid"),
        ({"dispatch_intent_ref": "unexpected"}, "source_reconciliation_reference_matrix_invalid"),
        ({"retry_posture": "no_retry"}, "source_reconciliation_retry_posture_matrix_invalid"),
        (
            {
                "decision_kind": "unresolved",
                "history_outcome": "matched",
                "history_conclusion": "dispatch_not_observed",
                "source_operation_disposition": "reconciliation_unknown",
                "retry_posture": "reconcile_first",
            },
            "source_reconciliation_reference_matrix_invalid",
        ),
        (
            {
                "decision_kind": "conclusive_observation",
                "history_outcome": "matched",
                "history_conclusion": "observed_result",
                "dispatch_intent_ref": "dispatch_ref_1",
                "conclusive_observation_ref": "observation_ref_1",
                "source_operation_disposition": "cancelled",
                "retry_posture": "no_retry",
            },
            "source_reconciliation_disposition_matrix_invalid",
        ),
    ],
)
def test_decision_contract_rejects_non_strict_fields_and_invalid_matrix(
    changes: dict[str, object],
    reason_code: str,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.source_reconciliation import (
        SourceOperationReconciliationDecision,
        validate_source_operation_reconciliation_decision,
    )

    decision = SourceOperationReconciliationDecision(**(_decision_values() | changes))
    with pytest.raises(RuntimeControlError) as exc_info:
        validate_source_operation_reconciliation_decision(decision)
    assert exc_info.value.reason_code == reason_code


@pytest.mark.parametrize(
    "decision",
    [
        lambda: _decision(),
        lambda: _unresolved_decision(
            history_outcome="matched",
            history_conclusion="dispatch_not_observed",
            dispatch_intent_ref="dispatch_ref_1",
        ),
        lambda: _conclusive_decision(),
    ],
)
def test_three_closed_decisions_commit_immutable_head_and_record(tmp_path: Path, decision) -> None:
    store = _store_with_operation(tmp_path)
    submitted = decision()

    record = store.commit_no_owner_source_reconciliation(submitted)
    operation = store.get_source_operation("runtime_run_1", "source_operation_1")

    assert record.committed_operation_phase == "reconciled"
    assert record.committed_ledger_revision == 2
    assert record.committed_reconciliation_revision == 1
    assert operation.operation_phase == "reconciled"
    assert operation.dispatch_intent_ref == submitted.dispatch_intent_ref
    assert operation.conclusive_observation_ref == submitted.conclusive_observation_ref
    assert operation.source_operation_disposition == submitted.source_operation_disposition
    assert operation.retry_posture == submitted.retry_posture
    assert operation.ledger_revision == 2
    assert operation.reconciliation_revision == 1

    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="source_reconciliations_immutable"):
            conn.execute(
                "UPDATE runtime_control_source_reconciliations SET committed_at = 'changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="source_reconciliations_immutable"):
            conn.execute("DELETE FROM runtime_control_source_reconciliations")


def test_unknown_head_can_be_resolved_without_replacing_dispatch_truth(tmp_path: Path) -> None:
    store = _store_with_operation(tmp_path)
    first = _unresolved_decision(
        history_outcome="matched",
        history_conclusion="dispatch_not_observed",
        dispatch_intent_ref="dispatch_ref_1",
    )
    store.commit_no_owner_source_reconciliation(first)

    second = _conclusive_decision(
        reconciliation_id="reconciliation_2",
        expected_ledger_revision=2,
        expected_reconciliation_revision=1,
    )
    record = store.commit_no_owner_source_reconciliation(second)

    assert record.committed_ledger_revision == 3
    operation = store.get_source_operation("runtime_run_1", "source_operation_1")
    assert operation.dispatch_intent_ref == "dispatch_ref_1"
    assert operation.conclusive_observation_ref == "observation_ref_1"
    assert operation.source_operation_disposition == "completed"
    assert operation.retry_posture == "no_retry"


def test_new_decision_cannot_reopen_safe_retry_or_conclusive_head(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    for index, first in enumerate((_decision(), _conclusive_decision()), start=1):
        store = _store_with_operation(tmp_path / str(index))
        store.commit_no_owner_source_reconciliation(first)
        next_decision = replace(
            first,
            reconciliation_id="reconciliation_2",
            expected_ledger_revision=2,
            expected_reconciliation_revision=1,
        )

        with pytest.raises(RuntimeControlError) as exc_info:
            store.commit_no_owner_source_reconciliation(next_decision)
        assert exc_info.value.reason_code == "source_reconciliation_transition_conflict"


def test_history_unavailable_cannot_invent_clear_or_replace_dispatch_truth(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_operation(tmp_path)
    with pytest.raises(RuntimeControlError) as invented:
        store.commit_no_owner_source_reconciliation(
            _unresolved_decision(dispatch_intent_ref="invented_dispatch_ref")
        )
    assert invented.value.reason_code == "source_reconciliation_transition_conflict"

    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_source_operations
            SET dispatch_intent_ref = 'existing_dispatch_ref', retry_posture = 'reconcile_first'
            """
        )
    with pytest.raises(RuntimeControlError) as cleared:
        store.commit_no_owner_source_reconciliation(_unresolved_decision())
    assert cleared.value.reason_code == "source_reconciliation_transition_conflict"
    with pytest.raises(RuntimeControlError) as replaced:
        store.commit_no_owner_source_reconciliation(
            _unresolved_decision(dispatch_intent_ref="different_dispatch_ref")
        )
    assert replaced.value.reason_code == "source_reconciliation_transition_conflict"


@pytest.mark.parametrize(
    ("setup", "decision_changes", "reason_code"),
    [
        ("running", {}, "source_reconciliation_run_not_resumable"),
        ("active_lease", {}, "source_reconciliation_owner_conflict"),
        (
            "none",
            {"canonical_request_hash": "d" * 64},
            "source_reconciliation_identity_conflict",
        ),
        ("none", {"expected_ledger_revision": 2}, "source_reconciliation_revision_conflict"),
        (
            "none",
            {"expected_reconciliation_revision": 1},
            "source_reconciliation_revision_conflict",
        ),
        ("main_committed", {}, "source_reconciliation_main_commit_conflict"),
        ("missing_outbox", {}, "source_operation_acceptance_incomplete"),
    ],
)
def test_no_owner_identity_and_revision_gates_fail_closed(
    tmp_path: Path,
    setup: str,
    decision_changes: dict[str, object],
    reason_code: str,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_operation(tmp_path)
    if setup == "running":
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE runtime_control_runs SET status = 'running'")
    elif setup == "active_lease":
        store.acquire_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            acquired_at="2026-07-19T00:00:03Z",
            lease_expires_at="2020-01-01T00:00:00Z",
        )
    elif setup == "main_committed":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """
                UPDATE runtime_control_source_operations
                SET operation_phase = 'main_committed', main_commit_ref = 'main_commit_ref_1'
                """
            )
    elif setup == "missing_outbox":
        with sqlite3.connect(store.path) as conn:
            conn.execute("DELETE FROM runtime_control_source_dispatch_outbox")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_no_owner_source_reconciliation(_decision(**decision_changes))
    assert exc_info.value.reason_code == reason_code
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0] == 0


@pytest.mark.parametrize("point", ["after_operation_update", "after_reconciliation_insert"])
def test_statement_fault_rolls_back_head_and_ledger(tmp_path: Path, point: str) -> None:
    store = _store_with_operation(tmp_path)

    def fail(injected_point: str) -> None:
        if injected_point == point:
            raise RuntimeError(f"injected {point}")

    with pytest.raises(RuntimeError, match=point):
        store.commit_no_owner_source_reconciliation(_unresolved_decision(), fail)

    operation = store.get_source_operation("runtime_run_1", "source_operation_1")
    assert operation.operation_phase == "accepted"
    assert operation.ledger_revision == 1
    assert operation.reconciliation_revision == 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0] == 0


def test_no_dispatch_must_preserve_current_disposition_without_writes(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_operation(tmp_path)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_no_owner_source_reconciliation(
            _decision(source_operation_disposition="completed")
        )

    assert exc_info.value.reason_code == "source_reconciliation_transition_conflict"
    operation = store.get_source_operation("runtime_run_1", "source_operation_1")
    assert operation.operation_phase == "accepted"
    assert operation.source_operation_disposition is None
    assert operation.ledger_revision == 1
    assert operation.reconciliation_revision == 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0] == 0


def test_commit_ack_loss_exact_replay_precedes_owner_gate(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_operation(tmp_path)
    decision = _unresolved_decision()

    def lose_ack(point: str) -> None:
        if point == "after_commit":
            raise ConnectionError("commit acknowledgement lost")

    with pytest.raises(ConnectionError, match="acknowledgement lost"):
        store.commit_no_owner_source_reconciliation(decision, lose_ack)

    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_after_commit",
        acquired_at="2026-07-19T00:00:03Z",
        lease_expires_at="2026-07-19T00:01:03Z",
    )
    replayed = store.commit_no_owner_source_reconciliation(decision)
    assert replayed.committed_ledger_revision == 2
    with pytest.raises(RuntimeControlError) as conflict:
        store.commit_no_owner_source_reconciliation(
            replace(decision, history_result_ref="history_result_ref_other")
        )
    assert conflict.value.reason_code == "source_reconciliation_idempotency_conflict"


def test_concurrent_same_decision_is_one_commit_and_same_record(tmp_path: Path) -> None:
    store = _store_with_operation(tmp_path)
    decision = _unresolved_decision()
    barrier = Barrier(2)

    def commit():
        barrier.wait(timeout=5)
        return store.commit_no_owner_source_reconciliation(decision)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=5) for future in (executor.submit(commit), executor.submit(commit))]
    assert results[0] == results[1]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0] == 1


def test_concurrent_different_decisions_have_one_revision_winner(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_operation(tmp_path)
    barrier = Barrier(2)
    decisions = [
        _unresolved_decision(reconciliation_id="reconciliation_unresolved"),
        _conclusive_decision(reconciliation_id="reconciliation_conclusive"),
    ]

    def commit(decision) -> str:
        barrier.wait(timeout=5)
        try:
            store.commit_no_owner_source_reconciliation(decision)
        except RuntimeControlError as exc:
            return exc.reason_code
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [future.result(timeout=5) for future in [executor.submit(commit, item) for item in decisions]]
    assert sorted(outcomes) == ["committed", "source_reconciliation_revision_conflict"]


def test_reconcile_first_blocks_resume_claim_and_cas_wins_serialized_race(tmp_path: Path) -> None:
    store = _claimable_store_with_operation(tmp_path)
    operation_updated = Event()
    release_commit = Event()

    def hold_after_update(point: str) -> None:
        if point == "after_operation_update":
            operation_updated.set()
            assert release_commit.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        commit_future = executor.submit(
            store.commit_no_owner_source_reconciliation,
            _unresolved_decision(),
            hold_after_update,
        )
        assert operation_updated.wait(timeout=5)
        claim_future = executor.submit(
            store.claim_next_runnable_run,
            executor_id="executor_racing_claim",
            claimed_at="2026-07-19T00:00:05Z",
            lease_expires_at="2026-07-19T00:01:05Z",
            runtime_run_id="runtime_run_1",
        )
        release_commit.set()
        commit_future.result(timeout=5)
        assert claim_future.result(timeout=5) is None


@pytest.mark.parametrize(
    ("decision_kind", "queued_after_commit"),
    [
        ("safe_retry", False),
        ("conclusive_no_retry", False),
        ("reconcile_first", True),
    ],
)
def test_claim_blocker_does_not_park_other_postures_or_queued_runs(
    tmp_path: Path,
    decision_kind: str,
    queued_after_commit: bool,
) -> None:
    store = _claimable_store_with_operation(tmp_path)
    if decision_kind == "safe_retry":
        decision = _decision()
    elif decision_kind == "conclusive_no_retry":
        decision = _conclusive_decision()
    else:
        decision = _unresolved_decision()
    store.commit_no_owner_source_reconciliation(decision)
    if queued_after_commit:
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE runtime_control_runs SET status = 'queued' WHERE runtime_run_id = ?",
                ("runtime_run_1",),
            )

    claim = store.claim_next_runnable_run(
        executor_id=f"executor_{decision_kind}",
        claimed_at="2026-07-19T00:00:05Z",
        lease_expires_at="2026-07-19T00:01:05Z",
        runtime_run_id="runtime_run_1",
    )

    assert claim is not None


def test_claim_winner_makes_new_cas_fail_closed(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _claimable_store_with_operation(tmp_path)
    claim = store.claim_next_runnable_run(
        executor_id="executor_claim_winner",
        claimed_at="2026-07-19T00:00:05Z",
        lease_expires_at="2026-07-19T00:01:05Z",
        runtime_run_id="runtime_run_1",
    )
    assert claim is not None
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_no_owner_source_reconciliation(_unresolved_decision())
    assert exc_info.value.reason_code == "source_reconciliation_run_not_resumable"


def test_raw_history_result_is_not_mutation_authority_and_production_has_no_caller(tmp_path: Path) -> None:
    from seektalent.source_port.history_contract import SourceHistoryUnavailable
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_operation(tmp_path)
    raw_history = SourceHistoryUnavailable.model_validate(
        {
            "contract_version": "seektalent.source-port.query.result/v1",
            "run_id": "runtime_run_1",
            "operation_id": "source_operation_1",
            "source": "liepin",
            "operation_kind": "search",
            "idempotency_key": "source-key-1",
            "request_hash": REQUEST_HASH,
            "attempt_no": 1,
            "authorization_selector": {"kind": "exact", "ordinal": 1},
            "accepted_generation_hint": None,
            "searched_first_generation": 1,
            "searched_last_generation": 1,
            "expected_source_operation_ledger_revision": 1,
            "expected_reconciliation_revision": 0,
            "outcome": "history_unavailable",
            "reason": "busy",
            "oldest_retained_generation": None,
            "newest_known_generation": None,
        }
    )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_no_owner_source_reconciliation(raw_history)  # type: ignore[arg-type]
    assert exc_info.value.reason_code == "source_reconciliation_decision_invalid"

    production_calls = 0
    for path in Path("src").rglob("*.py"):
        production_calls += path.read_text(encoding="utf-8").count(
            ".commit_no_owner_source_reconciliation("
        )
    assert production_calls == 0
    runner = Path("src/seektalent_workbench_v2/runtime_runner.py").read_text(encoding="utf-8")
    assert "recover_start_timeouts(resume_recoverable=False)" in runner


def _store_with_operation(tmp_path: Path):
    from seektalent_runtime_control.models import RuntimeRunRecord
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            run_intent_id="intent_runtime_run_1",
            start_idempotency_key="start_runtime_run_1",
            run_kind="primary",
            agent_conversation_id="agent_conversation_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin"],
            stop_reason_code=None,
            created_at="2026-07-19T00:00:00Z",
            updated_at="2026-07-19T00:00:00Z",
            completed_at=None,
        )
    )
    store.accept_source_operation(**_acceptance())
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="resume_requested",
        updated_at="2026-07-19T00:00:02Z",
    )
    return store


def _claimable_store_with_operation(tmp_path: Path):
    from seektalent_runtime_control.models import (
        RuntimeControlEventInput,
        RuntimeRunRecord,
        RuntimeRunSnapshot,
    )
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    run = RuntimeRunRecord(
        runtime_run_id="runtime_run_1",
        run_intent_id="intent_runtime_run_1",
        start_idempotency_key="start_runtime_run_1",
        run_kind="primary",
        agent_conversation_id="agent_conversation_1",
        workbench_session_id=None,
        approved_requirement_revision_id="reqapproved_1",
        status="queued",
        current_stage="queued",
        current_round=None,
        latest_checkpoint_id=None,
        latest_event_seq=0,
        source_ids=["liepin"],
        stop_reason_code=None,
        created_at="2026-07-19T00:00:00Z",
        updated_at="2026-07-19T00:00:00Z",
        completed_at=None,
    )
    store.accept_run(
        run,
        initial_event=RuntimeControlEventInput(
            event_id="event_runtime_run_1",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_queued",
            stage="queued",
            round_no=None,
            source_id=None,
            status="queued",
            summary="workflow run queued",
            payload={"runIntentId": run.run_intent_id},
            idempotency_key="runtime-run-queued:runtime_run_1",
            workbench_event_global_seq=None,
            created_at="2026-07-19T00:00:01Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_event_seq=0,
            snapshot={"workflowInput": {"sourceIds": ["liepin"]}},
            updated_at="2026-07-19T00:00:01Z",
        ),
    )
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="starting",
        updated_at="2026-07-19T00:00:02Z",
    )
    store.accept_source_operation(**_acceptance())
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="running",
        updated_at="2026-07-19T00:00:02.500000Z",
    )
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="resume_requested",
        updated_at="2026-07-19T00:00:03Z",
    )
    return store


def _acceptance() -> dict[str, object]:
    return {
        "runtime_run_id": "runtime_run_1",
        "operation_id": "source_operation_1",
        "source_id": "liepin",
        "operation_kind": "search",
        "canonical_request_hash": REQUEST_HASH,
        "idempotency_key": "source-key-1",
        "accepted_requirement_revision_id": "reqapproved_1",
        "runtime_attempt_no": 1,
        "runtime_attempt_authority_ref": "runtime_attempt_authority_ref_1",
        "outbox_id": "source_outbox_1",
        "dispatch_intent_id": "dispatch_intent_1",
        "dispatch_intent_revision": 1,
        "dispatch_intent_digest": "b" * 64,
        "dispatch_authorization_ordinal": 1,
        "source_operation_acceptance_ref": "source_acceptance_ref_1",
        "expected_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
    }


def _decision_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "reconciliation_id": "reconciliation_1",
        "runtime_run_id": "runtime_run_1",
        "operation_id": "source_operation_1",
        "source_id": "liepin",
        "operation_kind": "search",
        "canonical_request_hash": REQUEST_HASH,
        "idempotency_key": "source-key-1",
        "accepted_requirement_revision_id": "reqapproved_1",
        "runtime_attempt_no": 1,
        "runtime_attempt_authority_ref": "runtime_attempt_authority_ref_1",
        "history_result_ref": "history_result_ref_1",
        "history_result_digest": HISTORY_DIGEST,
        "decision_kind": "no_dispatch_proved",
        "history_outcome": "not_found",
        "history_conclusion": None,
        "dispatch_intent_ref": None,
        "conclusive_observation_ref": None,
        "source_operation_disposition": None,
        "retry_posture": "safe_retry",
        "expected_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
        "committed_at": "2026-07-19T00:00:04Z",
    }
    values.update(changes)
    return values


def _decision(**changes: object):
    from seektalent_runtime_control.source_reconciliation import SourceOperationReconciliationDecision

    return SourceOperationReconciliationDecision(**_decision_values(**changes))


def _unresolved_decision(**changes: object):
    values: dict[str, object] = {
        "decision_kind": "unresolved",
        "history_outcome": "history_unavailable",
        "history_conclusion": None,
        "dispatch_intent_ref": None,
        "conclusive_observation_ref": None,
        "source_operation_disposition": "reconciliation_unknown",
        "retry_posture": "reconcile_first",
    }
    values.update(changes)
    return _decision(**values)


def _conclusive_decision(**changes: object):
    values: dict[str, object] = {
        "decision_kind": "conclusive_observation",
        "history_outcome": "matched",
        "history_conclusion": "observed_result",
        "dispatch_intent_ref": "dispatch_ref_1",
        "conclusive_observation_ref": "observation_ref_1",
        "source_operation_disposition": "completed",
        "retry_posture": "no_retry",
    }
    values.update(changes)
    return _decision(**values)
