from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sqlite3

import pytest

import seektalent.sidecar_readiness as readiness
from seektalent.installed_slot import InstalledSidecarLaunchLease
from seektalent.source_history_reconciliation import (
    SourceHistoryReconciliationError,
    SourceHistoryReconciliationReason,
    commit_admitted_source_history_reconciliation,
)
from seektalent.source_port.history_contract import (
    AllAuthorizationsSelector,
    ExactAuthorizationSelector,
    SourceHistoryMatched,
    SourceHistoryQueryV1,
)
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader
from seektalent.source_port import sidecar_transport
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.store import RuntimeControlStore
from tests.support.source_history_sqlite_harness import (
    AcceptedHistoryInput,
    SourceHistorySQLiteHarness,
)
from tests.test_sidecar_readiness import (
    _connected_process,
    _identity,
    _serve_one_history_query,
    lease_factory as _readiness_lease_factory,
)


REQUEST_HASH = "a" * 64
RUNTIME_FENCE = "b" * 64
DISPATCH_DIGEST = "c" * 64
CONTROLLER_FENCE = "d" * 64
COMMITTED_AT = "2026-07-22T03:00:00.000000Z"
RETRY_COMMITTED_AT = "2026-07-22T03:00:01.000000Z"


@pytest.fixture
def ready_lease_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], InstalledSidecarLaunchLease]:
    return _readiness_lease_factory.__wrapped__(tmp_path, monkeypatch)


def test_same_snapshot_accepted_operation_context_returns_all_three_facts(tmp_path: Path) -> None:
    store = _store_with_operation(tmp_path, _query(), acknowledge=True)

    context = store.get_accepted_source_operation_context("runtime-run-1", "source-operation-1")

    assert context.operation.runtime_run_id == "runtime-run-1"
    assert context.expectation.runtime_attempt_fence_ref == RUNTIME_FENCE
    assert context.dispatch.status == "acknowledged"
    assert context.dispatch.accepted_sidecar_generation == 1
    assert context.dispatch.accepted_sidecar_journal_revision == 1


@pytest.mark.parametrize(
    ("history_kind", "expected_kind", "expected_outcome", "expected_conclusion", "expected_retry"),
    [
        ("not_found", "no_dispatch_proved", "not_found", None, "safe_retry"),
        ("accepted_no_dispatch", "no_dispatch_proved", "matched", "accepted_no_dispatch", "safe_retry"),
        (
            "dispatch_not_observed",
            "unresolved",
            "matched",
            "dispatch_not_observed",
            "reconcile_first",
        ),
        ("history_unavailable", "unresolved", "history_unavailable", None, "reconcile_first"),
    ],
)
def test_four_closed_mappings_commit_from_real_ready_session_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    history_kind: str,
    expected_kind: str,
    expected_outcome: str,
    expected_conclusion: str | None,
    expected_retry: str,
) -> None:
    case_root = tmp_path / history_kind
    harness, query, acknowledge, accepted_revision = _history_case(case_root, history_kind)
    store = _store_with_operation(
        case_root,
        query,
        acknowledge=acknowledge,
        accepted_journal_revision=accepted_revision,
    )
    if history_kind == "history_unavailable":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE runtime_control_source_operations SET dispatch_intent_ref = ?",
                ("current-durable-dispatch-ref",),
            )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    record = commit_admitted_source_history_reconciliation(
        admitted,
        store,
        committed_at=COMMITTED_AT,
    )

    assert record.decision_kind == expected_kind
    assert record.history_outcome == expected_outcome
    assert record.history_conclusion == expected_conclusion
    assert record.retry_posture == expected_retry
    assert record.history_result_ref == f"sha256:{record.history_result_digest}"
    assert record.reconciliation_id == f"source-history-{record.history_result_digest}"
    if history_kind == "dispatch_not_observed":
        assert record.dispatch_intent_ref == "durable-dispatch-ref"
    elif history_kind == "history_unavailable":
        assert record.dispatch_intent_ref == "current-durable-dispatch-ref"
    else:
        assert record.dispatch_intent_ref is None
    _close_exchange(session, child_thread, errors, ready_lease_factory)


@pytest.mark.parametrize(
    ("history_kind", "expected_kind", "expected_conclusion", "expected_retry"),
    [
        ("accepted_no_dispatch", "no_dispatch_proved", "accepted_no_dispatch", "safe_retry"),
        ("dispatch_not_observed", "unresolved", "dispatch_not_observed", "reconcile_first"),
    ],
)
def test_pending_outbox_matched_history_recovers_durable_sidecar_ack_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    history_kind: str,
    expected_kind: str,
    expected_conclusion: str,
    expected_retry: str,
) -> None:
    harness, query, _, accepted_revision = _history_case(tmp_path, history_kind)
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=False,
        accepted_journal_revision=accepted_revision,
    )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    record = commit_admitted_source_history_reconciliation(
        admitted,
        store,
        committed_at=COMMITTED_AT,
    )

    assert record.decision_kind == expected_kind
    assert record.history_conclusion == expected_conclusion
    assert record.retry_posture == expected_retry
    _close_exchange(session, child_thread, errors, ready_lease_factory)


def test_pending_outbox_ack_loss_rejects_wrong_authenticated_fact_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness, query, _, accepted_revision = _history_case(tmp_path, "accepted_no_dispatch")
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=False,
        accepted_journal_revision=accepted_revision,
        acceptance_changes={"runtime_attempt_fence_ref": "e" * 64},
    )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises(SourceHistoryReconciliationError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason is SourceHistoryReconciliationReason.CONTEXT_MISMATCH
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


@pytest.mark.parametrize("conclusion", ["observed_result", "observed_failure"])
def test_observed_history_requires_operation_specific_interpreter_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    conclusion: str,
) -> None:
    harness = _history_harness(tmp_path, 1, 2, 3)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    dispatch_revision = harness.record_dispatch_intent(
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        expected_head_journal_revision=accepted_revision,
        generation=2,
        durable_dispatch_intent_ref="durable-dispatch-ref",
    )
    if conclusion == "observed_result":
        harness.record_observed_result(
            run_id="runtime-run-1",
            operation_id="source-operation-1",
            expected_head_journal_revision=dispatch_revision,
            generation=3,
            result_ref="result-ref",
            result_hash="e" * 64,
        )
    else:
        harness.record_observed_failure(
            run_id="runtime-run-1",
            operation_id="source-operation-1",
            expected_head_journal_revision=dispatch_revision,
            generation=3,
            failure_ref="failure-ref",
            failure_hash="f" * 64,
        )
    query = _query(first_generation=1, last_generation=3, accepted_generation_hint=1)
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=True,
        accepted_journal_revision=accepted_revision,
    )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises(SourceHistoryReconciliationError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason is SourceHistoryReconciliationReason.OPERATION_INTERPRETATION_REQUIRED
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


def test_identity_conflict_is_typed_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness = _history_harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    query = _query(request_hash="e" * 64)
    store = _store_with_operation(tmp_path, query, acknowledge=False)
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises(SourceHistoryReconciliationError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason is SourceHistoryReconciliationReason.IDENTITY_CONFLICT
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


def test_raw_mapping_fake_closed_session_and_caller_decision_cannot_write_main_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness, query, _, _ = _history_case(tmp_path, "not_found")
    store = _store_with_operation(tmp_path, query, acknowledge=False)
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    forged_multiple = SourceHistoryMatched.model_construct(facts=(object(), object()))
    rejected: tuple[object, ...] = (
        admitted.payload,
        admitted.payload.model_dump(mode="json"),
        forged_multiple,
        object.__new__(sidecar_transport.AdmittedSourceHistoryResult),
    )
    for value in rejected:
        with pytest.raises(TypeError, match="live factory"):
            commit_admitted_source_history_reconciliation(value, store, committed_at=COMMITTED_AT)  # type: ignore[arg-type]
        _assert_no_reconciliation_write(store)
    with pytest.raises(TypeError):
        commit_admitted_source_history_reconciliation(  # type: ignore[call-arg]
            admitted,
            store,
            committed_at=COMMITTED_AT,
            decision={"retry_posture": "safe_retry"},
        )
    _assert_no_reconciliation_write(store)

    with pytest.raises(TypeError, match="real RuntimeControlStore"):
        commit_admitted_source_history_reconciliation(admitted, object(), committed_at=COMMITTED_AT)  # type: ignore[arg-type]
    _assert_no_reconciliation_write(store)

    _close_exchange(session, child_thread, errors, ready_lease_factory)
    with pytest.raises(TypeError, match="live factory"):
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)
    _assert_no_reconciliation_write(store)


def test_all_authorizations_selector_is_closed_reject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness = _history_harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    query = _query(
        authorization_selector=AllAuthorizationsSelector(kind="all"),
        accepted_generation_hint=1,
    )
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=True,
        accepted_journal_revision=accepted_revision,
    )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises(SourceHistoryReconciliationError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason is SourceHistoryReconciliationReason.AUTHORIZATION_SELECTOR_INVALID
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


@pytest.mark.parametrize(
    ("acceptance_changes", "acknowledge", "ack_changes"),
    [
        ({"source_id": "liepin", "operation_kind": "cards"}, True, {}),
        ({"canonical_request_hash": "e" * 64}, True, {}),
        ({"idempotency_key": "other-key"}, True, {}),
        ({"accepted_requirement_revision_id": "requirement-2"}, True, {}),
        ({"runtime_attempt_no": 2}, True, {}),
        ({"runtime_attempt_fence_ref": "e" * 64}, True, {}),
        ({"profile_binding_generation": 2}, True, {}),
        ({"browser_control_scope_id": "other-browser-scope"}, True, {}),
        ({"controller_fence_ref": "e" * 64}, True, {}),
        ({"dispatch_intent_id": "other-dispatch-intent"}, True, {}),
        ({"dispatch_intent_revision": 2}, True, {}),
        ({"dispatch_intent_digest": "e" * 64}, True, {}),
        ({}, True, {"accepted_sidecar_generation": 2}),
        ({}, True, {"accepted_sidecar_journal_revision": 2}),
    ],
)
def test_identity_fence_dispatch_and_ack_mismatch_matrix_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    acceptance_changes: dict[str, object],
    acknowledge: bool,
    ack_changes: dict[str, object],
) -> None:
    harness = _history_harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    query = _query(accepted_generation_hint=1)
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=acknowledge,
        accepted_journal_revision=accepted_revision,
        acceptance_changes=acceptance_changes,
        ack_changes=ack_changes,
    )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises(SourceHistoryReconciliationError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason is SourceHistoryReconciliationReason.CONTEXT_MISMATCH
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


@pytest.mark.parametrize(
    ("expected_ledger_revision", "expected_reconciliation_revision"),
    [(2, 0), (1, 1)],
)
def test_query_revision_mismatch_is_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    expected_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> None:
    harness = _history_harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    query = _query(
        accepted_generation_hint=1,
        expected_ledger_revision=expected_ledger_revision,
        expected_reconciliation_revision=expected_reconciliation_revision,
    )
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=True,
        accepted_journal_revision=accepted_revision,
    )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises(SourceHistoryReconciliationError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason is SourceHistoryReconciliationReason.CONTEXT_MISMATCH
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


def test_semantic_history_identity_is_stable_across_new_ready_session_and_exact_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness, query, _, accepted_revision = _history_case(tmp_path, "accepted_no_dispatch")
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=True,
        accepted_journal_revision=accepted_revision,
    )
    reader = SourceHistorySQLiteReader(harness.path)
    first, first_session, first_thread, first_errors = _exchange(
        reader,
        query,
        ready_lease_factory,
        monkeypatch,
    )
    committed = commit_admitted_source_history_reconciliation(first, store, committed_at=COMMITTED_AT)
    _close_exchange(first_session, first_thread, first_errors, ready_lease_factory)

    second, second_session, second_thread, second_errors = _exchange(
        reader,
        query,
        ready_lease_factory,
        monkeypatch,
    )
    replayed = commit_admitted_source_history_reconciliation(second, store, committed_at=COMMITTED_AT)

    assert replayed == committed
    assert second.session_id != first.session_id
    assert second.query_message_id != first.query_message_id
    assert replayed.history_result_digest == committed.history_result_digest
    assert replayed.history_result_ref == committed.history_result_ref
    _close_exchange(second_session, second_thread, second_errors, ready_lease_factory)


def test_history_unavailable_preserves_acknowledged_current_dispatch_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness = _history_harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    query = _query(first_generation=1, last_generation=2, accepted_generation_hint=1)
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=True,
        accepted_journal_revision=accepted_revision,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE runtime_control_source_operations SET dispatch_intent_ref = ?",
            ("current-durable-dispatch-ref",),
        )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    record = commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert record.history_outcome == "history_unavailable"
    assert record.dispatch_intent_ref == "current-durable-dispatch-ref"
    _close_exchange(session, child_thread, errors, ready_lease_factory)


def test_unknown_dispatch_status_is_closed_reject_for_history_unavailable_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness = _history_harness(tmp_path, 1)
    query = _query(first_generation=1, last_generation=2, accepted_generation_hint=1)
    store = _store_with_operation(tmp_path, query, acknowledge=True)
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("UPDATE runtime_control_source_dispatch_outbox SET status = 'unknown'")
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises(SourceHistoryReconciliationError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason is SourceHistoryReconciliationReason.CONTEXT_MISMATCH
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


@pytest.mark.parametrize(
    ("setup", "reason_code"),
    [
        ("non_resumable", "source_reconciliation_run_not_resumable"),
        ("active_owner", "source_reconciliation_owner_conflict"),
        ("revision_race", "source_reconciliation_revision_conflict"),
        ("main_committed", "source_reconciliation_main_commit_conflict"),
        ("missing_expectation", "source_operation_acceptance_incomplete"),
        ("missing_dispatch", "source_operation_acceptance_incomplete"),
        ("corrupt_dispatch", SourceHistoryReconciliationReason.CONTEXT_MISMATCH.value),
        ("corrupt_source", SourceHistoryReconciliationReason.CONTEXT_MISMATCH.value),
    ],
)
def test_existing_no_owner_cas_and_acceptance_gates_remain_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    setup: str,
    reason_code: str,
) -> None:
    case_root = tmp_path / setup
    harness, query, _, _ = _history_case(case_root, "not_found")
    store = _store_with_operation(case_root, query, acknowledge=False)
    if setup == "non_resumable":
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE runtime_control_runs SET status = 'running'")
    elif setup == "active_owner":
        store.acquire_executor_lease(
            runtime_run_id=query.run_id,
            executor_id="executor-1",
            acquired_at="2026-07-22T02:03:00.000000Z",
            lease_expires_at="2026-07-22T02:04:00.000000Z",
        )
    elif setup == "revision_race":
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE runtime_control_source_operations SET ledger_revision = 2")
    elif setup == "main_committed":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """
                UPDATE runtime_control_source_operations
                SET operation_phase = 'main_committed', main_commit_ref = 'main-commit-ref'
                """
            )
    elif setup == "missing_expectation":
        with sqlite3.connect(store.path) as conn:
            _disable_expectation_immutability(conn)
            conn.execute("DELETE FROM runtime_control_source_operation_admission_expectations")
    elif setup == "missing_dispatch":
        with sqlite3.connect(store.path) as conn:
            conn.execute("DELETE FROM runtime_control_source_dispatch_outbox")
    elif setup == "corrupt_dispatch":
        with sqlite3.connect(store.path) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("UPDATE runtime_control_source_dispatch_outbox SET source_operation_acceptance_ref = ''")
    elif setup == "corrupt_source":
        with sqlite3.connect(store.path) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("UPDATE runtime_control_source_operations SET source_id = 'other'")
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    with pytest.raises((RuntimeControlError, SourceHistoryReconciliationError)) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason_code == reason_code
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0] == 0
    _close_exchange(session, child_thread, errors, ready_lease_factory)


@pytest.mark.parametrize("fault_point", ["after_operation_update", "after_reconciliation_insert"])
def test_composition_statement_fault_rolls_back_without_partial_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    fault_point: str,
) -> None:
    harness, query, _, _ = _history_case(tmp_path, "not_found")
    store = _store_with_operation(tmp_path, query, acknowledge=False)
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    def fail(point: str) -> None:
        if point == fault_point:
            raise RuntimeError(f"injected {point}")

    with pytest.raises(RuntimeError, match=fault_point):
        commit_admitted_source_history_reconciliation(
            admitted,
            store,
            committed_at=COMMITTED_AT,
            fault_injector=fail,
        )

    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


def test_composition_after_commit_ack_loss_replays_same_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness, query, _, _ = _history_case(tmp_path, "not_found")
    store = _store_with_operation(tmp_path, query, acknowledge=False)
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )

    def lose_ack(point: str) -> None:
        if point == "after_commit":
            raise ConnectionError("commit acknowledgement lost")

    with pytest.raises(ConnectionError, match="acknowledgement lost"):
        commit_admitted_source_history_reconciliation(
            admitted,
            store,
            committed_at=COMMITTED_AT,
            fault_injector=lose_ack,
        )

    _close_exchange(session, child_thread, errors, ready_lease_factory)
    replay_admitted, replay_session, replay_thread, replay_errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )
    replayed = commit_admitted_source_history_reconciliation(
        replay_admitted,
        store,
        committed_at=RETRY_COMMITTED_AT,
    )
    assert replayed.committed_ledger_revision == 2
    assert replayed.committed_reconciliation_revision == 1
    assert replayed.committed_at == COMMITTED_AT
    _close_exchange(replay_session, replay_thread, replay_errors, ready_lease_factory)


def test_late_outbox_ack_cannot_commit_safe_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    harness, query, _, accepted_revision = _history_case(tmp_path, "not_found")
    store = _store_with_operation(tmp_path, query, acknowledge=False)
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        ready_lease_factory,
        monkeypatch,
    )
    original_commit = RuntimeControlStore.commit_no_owner_source_reconciliation

    def commit_after_late_ack(self, decision, fault_injector=None, *, dispatch_precondition=None):
        _acknowledge_pending_dispatch(
            self,
            query,
            accepted_journal_revision=accepted_revision,
            ack_ref="late-ack-ref",
        )
        return original_commit(
            self,
            decision,
            fault_injector,
            dispatch_precondition=dispatch_precondition,
        )

    monkeypatch.setattr(RuntimeControlStore, "commit_no_owner_source_reconciliation", commit_after_late_ack)

    with pytest.raises(RuntimeControlError) as exc_info:
        commit_admitted_source_history_reconciliation(admitted, store, committed_at=COMMITTED_AT)

    assert exc_info.value.reason_code == "source_reconciliation_dispatch_conflict"
    dispatch = store.get_accepted_source_operation_context(query.run_id, query.operation_id).dispatch
    assert (dispatch.status, dispatch.outbox_revision, dispatch.ack_ref) == ("acknowledged", 2, "late-ack-ref")
    _assert_no_reconciliation_write(store)
    _close_exchange(session, child_thread, errors, ready_lease_factory)


def test_new_composition_has_no_production_caller() -> None:
    callers = []
    for path in Path("src").rglob("*.py"):
        if path.name == "source_history_reconciliation.py":
            continue
        if "commit_admitted_source_history_reconciliation(" in path.read_text(encoding="utf-8"):
            callers.append(path)
    assert callers == []


def _history_case(
    root: Path,
    history_kind: str,
) -> tuple[SourceHistorySQLiteHarness, SourceHistoryQueryV1, bool, int]:
    if history_kind == "history_unavailable":
        harness = _history_harness(root, 1)
        return harness, _query(first_generation=1, last_generation=2), False, 1
    harness = _history_harness(root, 1, *((2,) if history_kind == "dispatch_not_observed" else ()))
    if history_kind == "not_found":
        return harness, _query(), False, 1
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    if history_kind == "dispatch_not_observed":
        harness.record_dispatch_intent(
            run_id="runtime-run-1",
            operation_id="source-operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=2,
            durable_dispatch_intent_ref="durable-dispatch-ref",
        )
        return (
            harness,
            _query(first_generation=1, last_generation=2, accepted_generation_hint=1),
            True,
            accepted_revision,
        )
    if history_kind == "accepted_no_dispatch":
        return harness, _query(accepted_generation_hint=1), True, accepted_revision
    raise AssertionError(f"unknown history kind: {history_kind}")


def _history_harness(root: Path, *generations: int) -> SourceHistorySQLiteHarness:
    harness = SourceHistorySQLiteHarness.create(root / "source-history.sqlite3")
    for generation in generations:
        harness.register_generation(generation)
    return harness


def _accepted() -> AcceptedHistoryInput:
    return AcceptedHistoryInput(
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        source="liepin",
        operation_kind="search",
        idempotency_key="source-key-1",
        request_hash=REQUEST_HASH,
        attempt_no=1,
        accepted_requirement_revision_id="requirement-1",
        runtime_attempt_fence_ref=RUNTIME_FENCE,
        authorized_dispatch_intent_id="dispatch-intent-1",
        authorized_dispatch_intent_revision=1,
        authorized_dispatch_intent_digest=DISPATCH_DIGEST,
        profile_binding_generation=1,
        browser_control_scope_id="browser-scope-1",
        controller_fence_ref=CONTROLLER_FENCE,
    )


def _query(
    *,
    request_hash: str = REQUEST_HASH,
    authorization_selector: ExactAuthorizationSelector | AllAuthorizationsSelector | None = None,
    first_generation: int = 1,
    last_generation: int = 1,
    accepted_generation_hint: int | None = None,
    expected_ledger_revision: int = 1,
    expected_reconciliation_revision: int = 0,
) -> SourceHistoryQueryV1:
    return SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        source="liepin",
        operation_kind="search",
        idempotency_key="source-key-1",
        request_hash=request_hash,
        attempt_no=1,
        authorization_selector=authorization_selector or ExactAuthorizationSelector(kind="exact", ordinal=1),
        accepted_generation_hint=accepted_generation_hint,
        searched_first_generation=first_generation,
        searched_last_generation=last_generation,
        expected_source_operation_ledger_revision=expected_ledger_revision,
        expected_reconciliation_revision=expected_reconciliation_revision,
    )


def _store_with_operation(
    root: Path,
    query: SourceHistoryQueryV1,
    *,
    acknowledge: bool,
    accepted_journal_revision: int = 1,
    acceptance_changes: dict[str, object] | None = None,
    ack_changes: dict[str, object] | None = None,
) -> RuntimeControlStore:
    acceptance = {
        "runtime_run_id": query.run_id,
        "operation_id": query.operation_id,
        "source_id": query.source,
        "operation_kind": query.operation_kind,
        "canonical_request_hash": query.request_hash,
        "idempotency_key": query.idempotency_key,
        "accepted_requirement_revision_id": "requirement-1",
        "runtime_attempt_no": query.attempt_no,
        "runtime_attempt_authority_ref": "runtime-attempt-authority-1",
        "runtime_attempt_fence_ref": RUNTIME_FENCE,
        "profile_binding_generation": 1,
        "browser_control_scope_id": "browser-scope-1",
        "controller_fence_ref": CONTROLLER_FENCE,
        "outbox_id": "source-outbox-1",
        "dispatch_intent_id": "dispatch-intent-1",
        "dispatch_intent_revision": 1,
        "dispatch_intent_digest": DISPATCH_DIGEST,
        "dispatch_authorization_ordinal": 1,
        "source_operation_acceptance_ref": "source-acceptance-ref-1",
        "expected_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
    }
    acceptance.update(acceptance_changes or {})
    approved_revision = str(acceptance["accepted_requirement_revision_id"])
    store = RuntimeControlStore(root / "runtime-control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=query.run_id,
            run_intent_id="run-intent-1",
            start_idempotency_key="start-key-1",
            run_kind="primary",
            agent_conversation_id="conversation-1",
            workbench_session_id=None,
            approved_requirement_revision_id=approved_revision,
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin"],
            stop_reason_code=None,
            created_at="2026-07-22T02:00:00.000000Z",
            updated_at="2026-07-22T02:00:00.000000Z",
            completed_at=None,
        )
    )
    store.accept_source_operation(**acceptance)
    if acknowledge:
        ack = {
            "runtime_run_id": query.run_id,
            "operation_id": query.operation_id,
            "outbox_id": acceptance["outbox_id"],
            "canonical_request_hash": acceptance["canonical_request_hash"],
            "dispatch_intent_id": acceptance["dispatch_intent_id"],
            "dispatch_intent_revision": acceptance["dispatch_intent_revision"],
            "dispatch_intent_digest": acceptance["dispatch_intent_digest"],
            "dispatch_authorization_ordinal": 1,
            "expected_outbox_revision": 1,
            "accepted_sidecar_generation": 1,
            "accepted_sidecar_journal_revision": accepted_journal_revision,
            "ack_ref": "source-ack-ref-1",
            "ack_kind": "new_logical_operation",
            "acknowledged_at": "2026-07-22T02:01:00.000000Z",
        }
        ack.update(ack_changes or {})
        store.record_source_dispatch_ack(**ack)
    store.update_run_status(
        runtime_run_id=query.run_id,
        status="resume_requested",
        updated_at="2026-07-22T02:02:00.000000Z",
    )
    return store


def _acknowledge_pending_dispatch(
    store: RuntimeControlStore,
    query: SourceHistoryQueryV1,
    *,
    accepted_journal_revision: int,
    ack_ref: str = "source-ack-ref-1",
) -> None:
    dispatch = store.get_accepted_source_operation_context(query.run_id, query.operation_id).dispatch
    store.record_source_dispatch_ack(
        runtime_run_id=query.run_id,
        operation_id=query.operation_id,
        outbox_id=dispatch.outbox_id,
        canonical_request_hash=dispatch.canonical_request_hash,
        dispatch_intent_id=dispatch.dispatch_intent_id,
        dispatch_intent_revision=dispatch.dispatch_intent_revision,
        dispatch_intent_digest=dispatch.dispatch_intent_digest,
        dispatch_authorization_ordinal=dispatch.dispatch_authorization_ordinal,
        expected_outbox_revision=dispatch.outbox_revision,
        accepted_sidecar_generation=1,
        accepted_sidecar_journal_revision=accepted_journal_revision,
        ack_ref=ack_ref,
        ack_kind="new_logical_operation",
        acknowledged_at="2026-07-22T02:01:00.000000Z",
    )


def _exchange(
    reader: SourceHistorySQLiteReader,
    query: SourceHistoryQueryV1,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    sidecar_transport.AdmittedSourceHistoryResult,
    readiness.ReadySidecarSession,
    object,
    list[BaseException],
]:
    lease = ready_lease_factory()
    process, _, child_thread, errors, _ = _connected_process(
        lease,
        _identity(lease.admission),
        after_sidecar_result=lambda result: _serve_one_history_query(result, reader),
    )
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    admitted = sidecar_transport.exchange_source_history(session, query, timeout=1)
    return admitted, session, child_thread, errors


def _close_exchange(
    session: readiness.ReadySidecarSession,
    child_thread: object,
    errors: list[BaseException],
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    session.close(1)
    join = getattr(child_thread, "join")
    join(timeout=1)
    is_alive = getattr(child_thread, "is_alive")
    assert not is_alive()
    assert errors == []
    ready_lease_factory().close()


def _assert_no_reconciliation_write(store: RuntimeControlStore) -> None:
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0] == 0
        operation = conn.execute(
            "SELECT operation_phase, ledger_revision, reconciliation_revision FROM runtime_control_source_operations"
        ).fetchone()
    assert operation == ("accepted", 1, 0)


def _disable_expectation_immutability(conn: sqlite3.Connection) -> None:
    trigger_rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'trigger'
          AND tbl_name = 'runtime_control_source_operation_admission_expectations'
        """
    ).fetchall()
    for (trigger_name,) in trigger_rows:
        conn.execute(f'DROP TRIGGER "{trigger_name}"')
