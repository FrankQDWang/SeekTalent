from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
import sqlite3

import pytest


REQUEST_HASH = "a" * 64
DISPATCH_DIGEST = "b" * 64
SQLITE_INTEGER_MAX = 2**63 - 1
LONE_SURROGATE = json.loads(r'"\ud800"')


def test_acceptance_commits_initial_ledger_and_pending_dispatch_atomically(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path)

    accepted = store.accept_source_operation(**_acceptance())

    assert accepted.operation == store.get_source_operation("runtime_run_1", "source_operation_1")
    assert accepted.operation.operation_phase == "accepted"
    assert accepted.operation.ledger_revision == 1
    assert accepted.operation.reconciliation_revision == 0
    assert accepted.operation.source_operation_disposition is None
    assert accepted.operation.retry_posture == "no_retry"
    assert accepted.operation.dispatch_intent_ref is None
    assert accepted.operation.conclusive_observation_ref is None
    assert accepted.operation.main_commit_ref is None
    assert store.list_pending_source_dispatches() == [accepted.dispatch]
    assert accepted.dispatch.status == "pending"
    assert accepted.dispatch.outbox_revision == 1
    assert accepted.dispatch.dispatch_authorization_ordinal == 1
    assert accepted.dispatch.expected_ledger_revision == 1
    assert accepted.dispatch.expected_reconciliation_revision == 0


def test_operation_insert_fault_rolls_back_operation_and_outbox(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path)

    def fail_after_operation(point: str) -> None:
        if point == "after_operation_insert":
            raise sqlite3.OperationalError("injected source-operation fault")

    with pytest.raises(sqlite3.OperationalError, match="injected source-operation fault"):
        store.accept_source_operation(**_acceptance(), fault_injector=fail_after_operation)

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_operations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_dispatch_outbox").fetchone()[0] == 0


def test_commit_ack_loss_exact_replay_returns_same_rows(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path)

    def lose_ack(point: str) -> None:
        if point == "after_commit":
            raise ConnectionError("injected commit acknowledgement loss")

    with pytest.raises(ConnectionError, match="acknowledgement loss"):
        store.accept_source_operation(**_acceptance(), fault_injector=lose_ack)

    replayed = store.accept_source_operation(**_acceptance())
    assert replayed.operation == store.get_source_operation("runtime_run_1", "source_operation_1")
    assert store.list_pending_source_dispatches() == [replayed.dispatch]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_operations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_dispatch_outbox").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({}, None),
        ({"canonical_request_hash": "c" * 64}, "idempotency_conflict"),
        ({"operation_id": "source_operation_other"}, "idempotency_conflict"),
        ({"operation_id": "source_operation_1", "idempotency_key": "source-key-other"}, "identity_conflict"),
        (
            {
                "operation_id": "source_operation_1",
                "idempotency_key": "source-key-other",
                "canonical_request_hash": "c" * 64,
            },
            "identity_conflict",
        ),
        ({"operation_kind": "cards"}, "identity_conflict"),
        ({"runtime_attempt_no": 2}, "identity_conflict"),
        ({"runtime_attempt_authority_ref": "runtime_attempt_authority_ref_2"}, "identity_conflict"),
        ({"outbox_id": "source_outbox_2"}, "identity_conflict"),
        ({"dispatch_intent_id": "dispatch_intent_2"}, "identity_conflict"),
        ({"dispatch_intent_revision": 2}, "identity_conflict"),
        ({"dispatch_intent_digest": "c" * 64}, "identity_conflict"),
        ({"source_operation_acceptance_ref": "source_acceptance_ref_2"}, "identity_conflict"),
    ],
)
def test_exact_replay_and_identity_conflict_matrix(
    tmp_path: Path,
    changes: dict[str, object],
    reason_code: str | None,
) -> None:
    store = _store_with_run(tmp_path)
    first = store.accept_source_operation(**_acceptance())
    replay = _acceptance()
    replay.update(changes)

    if reason_code is None:
        assert store.accept_source_operation(**replay) == first
    else:
        from seektalent_runtime_control.errors import RuntimeControlError

        with pytest.raises(RuntimeControlError) as exc_info:
            store.accept_source_operation(**replay)
        assert exc_info.value.reason_code == reason_code

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_operations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_dispatch_outbox").fetchone()[0] == 1


def test_concurrent_identical_acceptance_has_one_durable_winner(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path)
    barrier = Barrier(2)

    def accept() -> object:
        barrier.wait(timeout=5)
        return store.accept_source_operation(**_acceptance())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=5) for future in [executor.submit(accept), executor.submit(accept)]]

    assert results[0] == results[1]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_operations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_dispatch_outbox").fetchone()[0] == 1


def test_concurrent_conflict_has_one_winner_and_one_typed_conflict(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    barrier = Barrier(2)

    def accept(request_hash: str) -> str:
        barrier.wait(timeout=5)
        try:
            store.accept_source_operation(**_acceptance(canonical_request_hash=request_hash))
        except RuntimeControlError as exc:
            return exc.reason_code
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=5)
            for future in [executor.submit(accept, REQUEST_HASH), executor.submit(accept, "c" * 64)]
        ]

    assert sorted(outcomes) == ["accepted", "idempotency_conflict"]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_operations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_dispatch_outbox").fetchone()[0] == 1


@pytest.mark.parametrize("status", ["queued", "paused", "resume_requested", "cancelled", "completed", "failed"])
def test_acceptance_rejects_non_dispatchable_run_states(tmp_path: Path, status: str) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path, status=status)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(**_acceptance())

    assert exc_info.value.reason_code == "source_operation_run_not_dispatchable"


def test_acceptance_requires_exact_current_requirement_and_existing_run(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError, RuntimeControlLookupError

    store = _store_with_run(tmp_path)

    with pytest.raises(RuntimeControlError) as wrong_revision:
        store.accept_source_operation(**_acceptance(accepted_requirement_revision_id="reqapproved_old"))
    assert wrong_revision.value.reason_code == "source_operation_requirement_revision_mismatch"

    with pytest.raises(RuntimeControlLookupError) as missing_run:
        store.accept_source_operation(**_acceptance(runtime_run_id="runtime_run_missing"))
    assert missing_run.value.reason_code == "runtime_run_not_found"


def test_existing_operation_without_outbox_fails_closed(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM runtime_control_source_dispatch_outbox")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(**_acceptance())

    assert exc_info.value.reason_code == "source_operation_acceptance_incomplete"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_dispatch_outbox").fetchone()[0] == 0


def test_existing_operation_with_mismatched_outbox_fails_closed(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE runtime_control_source_dispatch_outbox SET canonical_request_hash = ?",
            ("c" * 64,),
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(**_acceptance())

    assert exc_info.value.reason_code == "source_operation_acceptance_incomplete"


@pytest.mark.parametrize("corruption", ["missing_parent", "missing", "request_hash_mismatch"])
def test_pending_read_and_ack_fail_closed_when_operation_pair_is_invalid(
    tmp_path: Path,
    corruption: str,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    with sqlite3.connect(store.path) as conn:
        if corruption == "missing_parent":
            conn.execute("DELETE FROM runtime_control_runs")
        elif corruption == "missing":
            conn.execute("DELETE FROM runtime_control_source_operations")
        else:
            conn.execute(
                "UPDATE runtime_control_source_operations SET canonical_request_hash = ?",
                ("c" * 64,),
            )

    with pytest.raises(RuntimeControlError) as pending_error:
        store.list_pending_source_dispatches()
    assert pending_error.value.reason_code == "source_operation_acceptance_incomplete"

    with pytest.raises(RuntimeControlError) as ack_error:
        store.record_source_dispatch_ack(**_ack())
    assert ack_error.value.reason_code == "source_operation_acceptance_incomplete"

    with sqlite3.connect(store.path) as conn:
        outbox = conn.execute(
            """
            SELECT status, outbox_revision, accepted_sidecar_generation,
                   accepted_sidecar_journal_revision, ack_ref, ack_kind, acknowledged_at
            FROM runtime_control_source_dispatch_outbox
            """
        ).fetchone()
    assert outbox == ("pending", 1, None, None, None, None, None)


def test_new_acceptance_rejects_scoped_orphan_outbox_before_insert(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    accepted = store.accept_source_operation(**_acceptance())
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM runtime_control_source_operations")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(
            **_acceptance(
                idempotency_key="source-key-new",
                outbox_id="source_outbox_new",
                dispatch_intent_id="dispatch_intent_new",
                source_operation_acceptance_ref="source_acceptance_ref_new",
            )
        )

    assert exc_info.value.reason_code == "source_operation_acceptance_incomplete"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_operations").fetchone()[0] == 0
        outbox = conn.execute(
            """
            SELECT outbox_id, dispatch_intent_id, status, outbox_revision
            FROM runtime_control_source_dispatch_outbox
            """
        ).fetchone()
    assert outbox == (
        accepted.dispatch.outbox_id,
        accepted.dispatch.dispatch_intent_id,
        "pending",
        1,
    )


def test_pending_read_is_deterministic_across_restarts(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store = _store_with_run(tmp_path)
    first = store.accept_source_operation(**_acceptance())
    _add_run(store, runtime_run_id="runtime_run_2")
    second = store.accept_source_operation(
        **_acceptance(
            runtime_run_id="runtime_run_2",
            operation_id="source_operation_2",
            idempotency_key="source-key-2",
            outbox_id="source_outbox_0",
            dispatch_intent_id="dispatch_intent_2",
            source_operation_acceptance_ref="source_acceptance_ref_2",
        )
    )

    first_read = store.list_pending_source_dispatches()
    reopened = RuntimeControlStore(store.path)
    reopened.initialize()

    assert first_read == reopened.list_pending_source_dispatches()
    assert first_read == [second.dispatch, first.dispatch]


def test_authenticated_ack_is_cas_idempotent_and_does_not_advance_operation(tmp_path: Path) -> None:
    store = _store_with_run(tmp_path)
    accepted = store.accept_source_operation(**_acceptance())
    ack = _ack()

    acknowledged = store.record_source_dispatch_ack(**ack)
    replayed = store.record_source_dispatch_ack(**ack)

    assert acknowledged == replayed
    assert acknowledged.status == "acknowledged"
    assert acknowledged.outbox_revision == 2
    assert store.list_pending_source_dispatches() == []
    operation = store.get_source_operation("runtime_run_1", "source_operation_1")
    assert operation == accepted.operation
    assert operation.operation_phase == "accepted"
    assert operation.dispatch_intent_ref is None
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_source_dispatch_outbox").fetchone()[0] == 1


def test_acceptance_replay_returns_current_mutable_heads_after_run_and_ledger_advance(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    acknowledged = store.record_source_dispatch_ack(**_ack())
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="completed",
        updated_at="2026-07-19T00:00:02.000000Z",
        completed_at="2026-07-19T00:00:02.000000Z",
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE runtime_control_runs SET approved_requirement_revision_id = 'reqapproved_2' "
            "WHERE runtime_run_id = 'runtime_run_1'"
        )
        conn.execute(
            """
            UPDATE runtime_control_source_operations
            SET operation_phase = 'main_committed',
                dispatch_intent_ref = 'dispatch_intent_ref_1',
                conclusive_observation_ref = 'observation_ref_1',
                source_operation_disposition = 'completed',
                retry_posture = 'no_retry',
                reconciliation_revision = 2,
                main_commit_ref = 'main_commit_ref_1',
                ledger_revision = 6
            WHERE runtime_run_id = 'runtime_run_1' AND operation_id = 'source_operation_1'
            """
        )

    replayed = store.accept_source_operation(**_acceptance())

    assert replayed.operation.operation_phase == "main_committed"
    assert replayed.operation.ledger_revision == 6
    assert replayed.operation.reconciliation_revision == 2
    assert replayed.operation.dispatch_intent_ref == "dispatch_intent_ref_1"
    assert replayed.dispatch == acknowledged
    assert replayed.dispatch.status == "acknowledged"
    assert replayed.dispatch.outbox_revision == 2

    with pytest.raises(RuntimeControlError) as changed_revision:
        store.accept_source_operation(**_acceptance(accepted_requirement_revision_id="reqapproved_2"))
    assert changed_revision.value.reason_code == "identity_conflict"
    with pytest.raises(RuntimeControlError) as changed_hash:
        store.accept_source_operation(**_acceptance(canonical_request_hash="c" * 64))
    assert changed_hash.value.reason_code == "idempotency_conflict"


def test_new_operation_after_existing_acceptance_still_rejects_terminal_run(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="completed",
        updated_at="2026-07-19T00:00:02.000000Z",
        completed_at="2026-07-19T00:00:02.000000Z",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(**_new_acceptance())

    assert exc_info.value.reason_code == "source_operation_run_not_dispatchable"


def test_new_operation_after_existing_acceptance_still_requires_current_requirement(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE runtime_control_runs SET approved_requirement_revision_id = 'reqapproved_2' "
            "WHERE runtime_run_id = 'runtime_run_1'"
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(**_new_acceptance())

    assert exc_info.value.reason_code == "source_operation_requirement_revision_mismatch"


@pytest.mark.parametrize("expected_outbox_revision", [2, 999])
def test_ack_exact_fact_with_nonoriginal_expected_revision_conflicts(
    tmp_path: Path,
    expected_outbox_revision: int,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    stored = store.record_source_dispatch_ack(**_ack())

    with pytest.raises(RuntimeControlError) as exc_info:
        store.record_source_dispatch_ack(**_ack(expected_outbox_revision=expected_outbox_revision))

    assert exc_info.value.reason_code == "source_dispatch_outbox_revision_conflict"
    assert stored.outbox_revision == 2


def test_concurrent_conflicting_acks_have_one_cas_winner(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    barrier = Barrier(2)

    def acknowledge(ack_ref: str) -> str:
        barrier.wait(timeout=5)
        try:
            store.record_source_dispatch_ack(**_ack(ack_ref=ack_ref))
        except RuntimeControlError as exc:
            return exc.reason_code
        return "acknowledged"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=5)
            for future in [
                executor.submit(acknowledge, "source_ack_ref_first"),
                executor.submit(acknowledge, "source_ack_ref_second"),
            ]
        ]

    assert sorted(outcomes) == ["acknowledged", "source_dispatch_ack_conflict"]
    with sqlite3.connect(store.path) as conn:
        row = conn.execute(
            "SELECT status, outbox_revision, ack_ref FROM runtime_control_source_dispatch_outbox"
        ).fetchone()
    assert row[0:2] == ("acknowledged", 2)
    assert row[2] in {"source_ack_ref_first", "source_ack_ref_second"}


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"runtime_run_id": "runtime_run_other"}, "source_dispatch_identity_conflict"),
        ({"canonical_request_hash": "c" * 64}, "source_dispatch_identity_conflict"),
        ({"operation_id": "source_operation_other"}, "source_dispatch_identity_conflict"),
        ({"dispatch_intent_id": "dispatch_intent_other"}, "source_dispatch_identity_conflict"),
        ({"dispatch_intent_revision": 2}, "source_dispatch_identity_conflict"),
        ({"dispatch_intent_digest": "c" * 64}, "source_dispatch_identity_conflict"),
        ({"dispatch_authorization_ordinal": 2}, "source_dispatch_authorization_ordinal_invalid"),
        ({"dispatch_authorization_ordinal": True}, "source_dispatch_authorization_ordinal_invalid"),
        ({"dispatch_authorization_ordinal": 1.0}, "source_dispatch_authorization_ordinal_invalid"),
        ({"expected_outbox_revision": SQLITE_INTEGER_MAX + 1}, "source_operation_expected_outbox_revision_invalid"),
        (
            {"accepted_sidecar_generation": SQLITE_INTEGER_MAX + 1},
            "source_operation_accepted_sidecar_generation_invalid",
        ),
        (
            {"accepted_sidecar_journal_revision": SQLITE_INTEGER_MAX + 1},
            "source_operation_accepted_sidecar_journal_revision_invalid",
        ),
        ({"ack_ref": LONE_SURROGATE}, "source_operation_ack_ref_invalid"),
        ({"ack_kind": []}, "source_dispatch_ack_kind_invalid"),
        ({"expected_outbox_revision": 2}, "source_dispatch_outbox_revision_conflict"),
    ],
)
def test_ack_identity_and_revision_conflicts_are_typed(
    tmp_path: Path,
    changes: dict[str, object],
    reason_code: str,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    ack = _ack()
    ack.update(changes)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.record_source_dispatch_ack(**ack)

    assert exc_info.value.reason_code == reason_code
    assert len(store.list_pending_source_dispatches()) == 1


def test_mismatched_ack_replay_conflicts_and_preserves_first_fact(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    first = store.record_source_dispatch_ack(**_ack())

    with pytest.raises(RuntimeControlError) as exc_info:
        store.record_source_dispatch_ack(**_ack(ack_ref="source_ack_ref_other"))

    assert exc_info.value.reason_code == "source_dispatch_ack_conflict"
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        stored = conn.execute("SELECT * FROM runtime_control_source_dispatch_outbox").fetchone()
    assert stored["ack_ref"] == first.ack_ref


def test_acknowledged_replay_with_wrong_dispatch_intent_revision_conflicts(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    store.accept_source_operation(**_acceptance())
    first = store.record_source_dispatch_ack(**_ack())

    with pytest.raises(RuntimeControlError) as exc_info:
        store.record_source_dispatch_ack(**_ack(dispatch_intent_revision=2))

    assert exc_info.value.reason_code == "source_dispatch_identity_conflict"
    with sqlite3.connect(store.path) as conn:
        stored = conn.execute(
            """
            SELECT status, outbox_revision, dispatch_intent_revision, ack_ref, ack_kind, acknowledged_at
            FROM runtime_control_source_dispatch_outbox
            """
        ).fetchone()
    assert stored == (
        "acknowledged",
        2,
        1,
        first.ack_ref,
        first.ack_kind,
        first.acknowledged_at,
    )


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"source_id": "other"}, "source_operation_source_invalid"),
        ({"operation_kind": "download"}, "source_operation_kind_invalid"),
        ({"operation_kind": []}, "source_operation_kind_invalid"),
        ({"canonical_request_hash": "A" * 64}, "source_operation_canonical_request_hash_invalid"),
        ({"dispatch_intent_digest": "short"}, "source_operation_dispatch_intent_digest_invalid"),
        ({"runtime_attempt_authority_ref": ""}, "source_operation_runtime_attempt_authority_ref_invalid"),
        (
            {"runtime_attempt_authority_ref": LONE_SURROGATE},
            "source_operation_runtime_attempt_authority_ref_invalid",
        ),
        ({"source_operation_acceptance_ref": "x" * 257}, "source_operation_source_operation_acceptance_ref_invalid"),
        ({"dispatch_authorization_ordinal": 2}, "source_dispatch_authorization_ordinal_invalid"),
        ({"dispatch_authorization_ordinal": True}, "source_dispatch_authorization_ordinal_invalid"),
        ({"dispatch_authorization_ordinal": 1.0}, "source_dispatch_authorization_ordinal_invalid"),
        ({"expected_ledger_revision": 2}, "source_operation_expected_ledger_revision_invalid"),
        ({"expected_ledger_revision": True}, "source_operation_expected_ledger_revision_invalid"),
        ({"expected_ledger_revision": 1.0}, "source_operation_expected_ledger_revision_invalid"),
        ({"expected_reconciliation_revision": 1}, "source_operation_expected_reconciliation_revision_invalid"),
        ({"expected_reconciliation_revision": False}, "source_operation_expected_reconciliation_revision_invalid"),
        ({"expected_reconciliation_revision": 0.0}, "source_operation_expected_reconciliation_revision_invalid"),
        ({"runtime_attempt_no": SQLITE_INTEGER_MAX + 1}, "source_operation_runtime_attempt_no_invalid"),
        (
            {"dispatch_intent_revision": SQLITE_INTEGER_MAX + 1},
            "source_operation_dispatch_intent_revision_invalid",
        ),
    ],
)
def test_closed_fields_hashes_and_opaque_refs_fail_closed(
    tmp_path: Path,
    changes: dict[str, object],
    reason_code: str,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path)
    values = _acceptance()
    values.update(changes)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(**values)

    assert exc_info.value.reason_code == reason_code


def test_sqlite_integer_max_is_accepted_for_persisted_positive_fields(tmp_path: Path) -> None:
    from seektalent_runtime_control.source_operations import validate_source_dispatch_ack

    store = _store_with_run(tmp_path)
    accepted = store.accept_source_operation(
        **_acceptance(
            runtime_attempt_no=SQLITE_INTEGER_MAX,
            dispatch_intent_revision=SQLITE_INTEGER_MAX,
        )
    )
    assert accepted.operation.runtime_attempt_no == SQLITE_INTEGER_MAX
    assert accepted.dispatch.dispatch_intent_revision == SQLITE_INTEGER_MAX

    validate_source_dispatch_ack(
        **_ack(
            dispatch_intent_revision=SQLITE_INTEGER_MAX,
            expected_outbox_revision=SQLITE_INTEGER_MAX,
            accepted_sidecar_generation=SQLITE_INTEGER_MAX,
            accepted_sidecar_journal_revision=SQLITE_INTEGER_MAX,
        )
    )
    acknowledged = store.record_source_dispatch_ack(
        **_ack(
            dispatch_intent_revision=SQLITE_INTEGER_MAX,
            accepted_sidecar_generation=SQLITE_INTEGER_MAX,
            accepted_sidecar_journal_revision=SQLITE_INTEGER_MAX,
        )
    )
    assert acknowledged.accepted_sidecar_generation == SQLITE_INTEGER_MAX
    assert acknowledged.accepted_sidecar_journal_revision == SQLITE_INTEGER_MAX


def _store_with_run(tmp_path: Path, *, status: str = "running"):
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _add_run(store, runtime_run_id="runtime_run_1", status=status)
    return store


def _add_run(store, *, runtime_run_id: str, status: str = "running") -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            run_intent_id=f"intent_{runtime_run_id}",
            start_idempotency_key=f"start_{runtime_run_id}",
            run_kind="primary",
            agent_conversation_id="agent_conversation_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status=status,
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin"],
            stop_reason_code=None,
            created_at="2026-07-19T00:00:00.000000Z",
            updated_at="2026-07-19T00:00:00.000000Z",
            completed_at="2026-07-19T00:00:01.000000Z" if status in {"cancelled", "completed", "failed"} else None,
        )
    )


def _acceptance(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
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
        "dispatch_intent_digest": DISPATCH_DIGEST,
        "dispatch_authorization_ordinal": 1,
        "source_operation_acceptance_ref": "source_acceptance_ref_1",
        "expected_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
    }
    values.update(changes)
    return values


def _ack(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "runtime_run_id": "runtime_run_1",
        "operation_id": "source_operation_1",
        "outbox_id": "source_outbox_1",
        "canonical_request_hash": REQUEST_HASH,
        "dispatch_intent_id": "dispatch_intent_1",
        "dispatch_intent_revision": 1,
        "dispatch_intent_digest": DISPATCH_DIGEST,
        "dispatch_authorization_ordinal": 1,
        "expected_outbox_revision": 1,
        "accepted_sidecar_generation": 1,
        "accepted_sidecar_journal_revision": 1,
        "ack_ref": "source_ack_ref_1",
        "ack_kind": "new_logical_operation",
        "acknowledged_at": "2026-07-19T00:00:01.000000Z",
    }
    values.update(changes)
    return values


def _new_acceptance() -> dict[str, object]:
    return _acceptance(
        operation_id="source_operation_2",
        idempotency_key="source-key-2",
        outbox_id="source_outbox_2",
        dispatch_intent_id="dispatch_intent_2",
        source_operation_acceptance_ref="source_acceptance_ref_2",
    )
