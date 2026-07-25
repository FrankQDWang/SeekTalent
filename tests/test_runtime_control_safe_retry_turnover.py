from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest


def test_v11_to_v12_preserves_ordinal_one_rows_and_reconciliation(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION
    from tests.test_runtime_control_source_operations import _ack
    from tests.test_runtime_control_source_reconciliation import (
        _conclusive_decision,
        _store_with_operation,
    )

    store = _store_with_operation(tmp_path)
    store.record_source_dispatch_ack(**_ack())
    store.commit_no_owner_source_reconciliation(_conclusive_decision())
    _downgrade_source_epochs_to_v11(store.path)
    before = _source_epoch_rows(store.path)

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11

    store.initialize()

    assert RUNTIME_CONTROL_SCHEMA_VERSION == 12
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        expectation = conn.execute(
            """
            SELECT dispatch_authorization_ordinal, runtime_attempt_no,
                   runtime_attempt_authority_ref
            FROM runtime_control_source_operation_admission_expectations
            """
        ).fetchone()
        outbox = conn.execute(
            """
            SELECT safe_retry_commit_ref
            FROM runtime_control_source_dispatch_outbox
            """
        ).fetchone()
    assert tuple(expectation) == (1, 1, "runtime_attempt_authority_ref_1")
    assert outbox["safe_retry_commit_ref"] is None
    assert _source_epoch_rows(store.path, legacy_columns=True) == before


@pytest.mark.parametrize(
    "point",
    (
        "after_validation",
        "after_outbox_create",
        "after_outbox_copy",
        "after_expectation_create",
        "after_expectation_copy",
        "after_trigger_drop",
        "after_legacy_drop",
        "after_table_rename",
        "after_schema_restore",
    ),
)
def test_v11_to_v12_faults_roll_back_schema_and_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    from seektalent_runtime_control import (
        source_epoch_schema as schema_module,
    )
    from tests.test_runtime_control_source_operations import _ack
    from tests.test_runtime_control_source_reconciliation import (
        _conclusive_decision,
        _store_with_operation,
    )

    store = _store_with_operation(tmp_path)
    store.record_source_dispatch_ack(**_ack())
    store.commit_no_owner_source_reconciliation(_conclusive_decision())
    _downgrade_source_epochs_to_v11(store.path)
    before = _source_epoch_rows(store.path)

    def fail(injected_point: str) -> None:
        if injected_point == point:
            raise RuntimeError(f"injected {point}")

    monkeypatch.setattr(
        schema_module,
        "_inject_source_epoch_migration_fault",
        fail,
    )
    with pytest.raises(RuntimeError, match=point):
        store.initialize()

    assert _source_epoch_version(store.path) == 11
    assert _source_epoch_rows(store.path) == before
    assert not _source_epoch_migration_tables(store.path)

    monkeypatch.setattr(
        schema_module,
        "_inject_source_epoch_migration_fault",
        lambda _point: None,
    )
    store.initialize()
    assert _source_epoch_version(store.path) == 12
    assert _source_epoch_rows(store.path, legacy_columns=True) == before


@pytest.mark.parametrize(
    "corruption",
    ("invalid_row", "unexpected_column", "partial_replacement"),
)
def test_v11_to_v12_rejects_invalid_or_partial_legacy_state_without_rewrite(
    tmp_path: Path,
    corruption: str,
) -> None:
    from seektalent.sqlite_migrations import SQLiteMigrationError
    from tests.test_runtime_control_source_reconciliation import (
        _store_with_operation,
    )

    store = _store_with_operation(tmp_path)
    _downgrade_source_epochs_to_v11(store.path)
    with sqlite3.connect(store.path) as conn:
        if corruption == "invalid_row":
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute(
                """
                UPDATE runtime_control_source_dispatch_outbox
                SET dispatch_authorization_ordinal = 2
                """
            )
        elif corruption == "unexpected_column":
            conn.execute(
                """
                ALTER TABLE runtime_control_source_dispatch_outbox
                ADD COLUMN unexpected_alias TEXT
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE runtime_control_source_dispatch_outbox_v12 (
                  partial INTEGER
                )
                """
            )
    before = _source_epoch_rows(store.path)
    before_tables = _source_epoch_migration_tables(store.path)

    with pytest.raises(SQLiteMigrationError) as exc_info:
        store.initialize()

    assert exc_info.value.reason_code == "runtime_control_source_dispatch_epoch_migration_invalid"
    assert _source_epoch_version(store.path) == 11
    assert _source_epoch_rows(store.path) == before
    assert _source_epoch_migration_tables(store.path) == before_tables


def test_v11_to_v12_rejects_duplicate_legacy_dispatch_aliases_without_rewrite(
    tmp_path: Path,
) -> None:
    from seektalent.sqlite_migrations import SQLiteMigrationError
    from tests.test_runtime_control_source_reconciliation import (
        _store_with_operation,
    )

    store = _store_with_operation(tmp_path)
    _downgrade_source_epochs_to_v11(store.path)
    with sqlite3.connect(store.path) as conn:
        conn.executescript(
            """
            ALTER TABLE runtime_control_source_dispatch_outbox
              RENAME TO runtime_control_source_dispatch_outbox_original;
            CREATE TABLE runtime_control_source_dispatch_outbox AS
              SELECT * FROM runtime_control_source_dispatch_outbox_original;
            INSERT INTO runtime_control_source_dispatch_outbox
              SELECT 'duplicate_outbox', runtime_run_id, operation_id,
                     canonical_request_hash, dispatch_intent_id,
                     dispatch_intent_revision, dispatch_intent_digest,
                     dispatch_authorization_ordinal,
                     source_operation_acceptance_ref,
                     expected_ledger_revision,
                     expected_reconciliation_revision, status,
                     outbox_revision, accepted_sidecar_generation,
                     accepted_sidecar_journal_revision, ack_ref,
                     ack_kind, acknowledged_at
              FROM runtime_control_source_dispatch_outbox_original;
            DROP TABLE runtime_control_source_dispatch_outbox_original;
            CREATE INDEX idx_runtime_source_dispatch_pending
              ON runtime_control_source_dispatch_outbox(status, outbox_id);
            """
        )
    before = _source_epoch_rows(store.path)
    assert len(before["outbox"]) == 2

    with pytest.raises(SQLiteMigrationError) as exc_info:
        store.initialize()

    assert exc_info.value.reason_code == "runtime_control_source_dispatch_epoch_migration_invalid"
    assert _source_epoch_version(store.path) == 11
    assert _source_epoch_rows(store.path) == before


def test_safe_retry_turnover_mints_canonical_current_epoch_atomically(tmp_path: Path) -> None:
    from seektalent.source_port.operation_dispatch import (
        DispatchAuthorizationV1,
        OperationIdentityV1,
        RelativeMonotonicDeadlineV1,
        canonical_dispatch_authorization_bytes,
        dispatch_authorization_digest,
    )

    store, authority = _store_with_safe_retry_and_authority(tmp_path)

    committed = store.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )

    assert committed.operation.runtime_attempt_no == 1
    assert committed.operation.runtime_attempt_authority_ref == "runtime_attempt_authority_ref_1"
    assert committed.operation.ledger_revision == 3
    assert committed.operation.reconciliation_revision == 1
    assert committed.operation.retry_posture == "no_retry"
    assert committed.expectation.dispatch_authorization_ordinal == 2
    assert committed.expectation.runtime_attempt_no == 2
    assert committed.expectation.runtime_attempt_authority_ref == "runtime_attempt_authority_ref_2"
    assert committed.expectation.runtime_attempt_fence_ref == "d" * 64
    assert committed.expectation.profile_binding_generation == 2
    assert committed.expectation.browser_control_scope_id == "browser_scope_2"
    assert committed.expectation.controller_fence_ref == "e" * 64
    assert committed.dispatch.dispatch_authorization_ordinal == 2
    assert committed.dispatch.safe_retry_commit_ref == "reconciliation_1"
    assert committed.dispatch.dispatch_intent_revision == 2
    assert committed.dispatch.expected_ledger_revision == 3
    assert committed.dispatch.expected_reconciliation_revision == 1
    assert store.list_pending_source_dispatches() == [committed.dispatch]

    identity = OperationIdentityV1(
        run_id="runtime_run_1",
        operation_id="source_operation_1",
        attempt_no=2,
        source="liepin",
        operation_kind="search",
        request_hash="a" * 64,
        idempotency_key="source-key-1",
        correlation_id="dispatch_intent_2",
        accepted_requirement_revision_id="reqapproved_1",
        runtime_attempt_fence_ref="d" * 64,
        profile_binding_generation=2,
        browser_control_scope_id="browser_scope_2",
        deadline=RelativeMonotonicDeadlineV1(
            value=1,
            clock="relative_monotonic",
            unit="milliseconds",
        ),
        expected_source_operation_ledger_revision=3,
        expected_reconciliation_revision=1,
    )
    authorization = DispatchAuthorizationV1.create_safe_retry(
        identity=identity,
        dispatch_intent_id="dispatch_intent_2",
        dispatch_intent_revision=2,
        dispatch_authorization_ordinal=2,
        safe_retry_commit_ref="reconciliation_1",
        source_operation_acceptance_ref="source_acceptance_ref_1",
    )
    assert committed.dispatch.dispatch_intent_digest == dispatch_authorization_digest(authorization)
    assert canonical_dispatch_authorization_bytes(authorization) == (
        b'{"attempt_no":2,"dispatch_authorization_ordinal":2,'
        b'"dispatch_intent_id":"dispatch_intent_2",'
        b'"dispatch_intent_revision":2,'
        b'"expected_reconciliation_revision":1,'
        b'"expected_source_operation_ledger_revision":3,'
        b'"operation_id":"source_operation_1",'
        b'"request_hash":"' + (b"a" * 64) + b'",'
        b'"run_id":"runtime_run_1",'
        b'"safe_retry_commit_ref":"reconciliation_1",'
        b'"source_operation_acceptance_ref":"source_acceptance_ref_1"}'
    )

    with sqlite3.connect(store.path) as conn:
        operation = conn.execute(
            """
            SELECT ledger_revision, reconciliation_revision, retry_posture
            FROM runtime_control_source_operations
            """
        ).fetchone()
        expectations = conn.execute(
            """
            SELECT dispatch_authorization_ordinal
            FROM runtime_control_source_operation_admission_expectations
            ORDER BY dispatch_authorization_ordinal
            """
        ).fetchall()
        outboxes = conn.execute(
            """
            SELECT dispatch_authorization_ordinal, status
            FROM runtime_control_source_dispatch_outbox
            ORDER BY dispatch_authorization_ordinal
            """
        ).fetchall()
    assert operation == (3, 1, "no_retry")
    assert expectations == [(1,), (2,)]
    assert outboxes == [(1, "pending"), (2, "pending")]

    reopened = type(store)(store.path)
    reopened.initialize()
    assert reopened.list_pending_source_dispatches() == [committed.dispatch]


def test_safe_retry_turnover_exact_replay_returns_one_committed_epoch(
    tmp_path: Path,
) -> None:
    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    request = _turnover(authority=authority)

    first = store.mint_safe_retry_dispatch_epoch(**request)
    replayed = store.mint_safe_retry_dispatch_epoch(**request)

    assert replayed == first
    state = _source_epoch_state(store.path)
    assert len(state["expectation"]) == 2
    assert len(state["outbox"]) == 2
    assert state["operation"][0][-1] == 3


@pytest.mark.parametrize(
    "changes",
    (
        {"outbox_id": "source_outbox_conflict"},
        {"dispatch_intent_id": "dispatch_intent_conflict"},
        {"expected_reconciliation_ledger_revision": 3},
        {"expected_reconciliation_revision": 2},
        {"reconciliation_id": "different_reconciliation"},
    ),
)
def test_safe_retry_turnover_changed_replay_conflicts_without_writes(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    store.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )
    store.release_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        attempt_no=2,
        released_at="2026-07-19T00:00:09Z",
    )
    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    before = _source_epoch_state(store.path)

    _assert_turnover_rejected(
        reopened,
        _turnover(authority=authority, **changes),
        "source_safe_retry_idempotency_conflict",
    )

    assert _source_epoch_state(store.path) == before


def test_safe_retry_turnover_changed_authority_replay_conflicts_without_writes(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    store.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )
    changed_authority = store._mint_safe_retry_turnover_authority_for_test(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        attempt_no=2,
        observed_at="2026-07-19T00:00:09Z",
        runtime_attempt_authority_ref="runtime_attempt_authority_ref_changed",
        runtime_attempt_fence_ref="f" * 64,
        profile_binding_generation=3,
        browser_control_scope_id="browser_scope_changed",
        controller_fence_ref=None,
    )
    store.release_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        attempt_no=2,
        released_at="2026-07-19T00:00:10Z",
    )
    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    before = _source_epoch_state(store.path)

    _assert_turnover_rejected(
        reopened,
        _turnover(authority=changed_authority),
        "source_safe_retry_idempotency_conflict",
    )

    assert _source_epoch_state(store.path) == before


def test_safe_retry_turnover_corrupt_digest_replay_conflicts_without_writes(
    tmp_path: Path,
) -> None:
    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    store.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_source_dispatch_outbox
            SET dispatch_intent_digest = ?
            WHERE dispatch_authorization_ordinal = 2
            """,
            ("f" * 64,),
        )
    before = _source_epoch_state(store.path)

    _assert_turnover_rejected(
        store,
        _turnover(authority=authority),
        "source_safe_retry_idempotency_conflict",
    )

    assert _source_epoch_state(store.path) == before


@pytest.mark.parametrize(
    "point",
    (
        "before_expectation_insert",
        "after_expectation_insert",
        "before_outbox_insert",
        "after_outbox_insert",
        "before_operation_update",
        "after_operation_update",
        "before_commit",
    ),
)
def test_safe_retry_turnover_statement_faults_roll_back_every_write(
    tmp_path: Path,
    point: str,
) -> None:
    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    before = _source_epoch_state(store.path)

    def fail(injected_point: str) -> None:
        if injected_point == point:
            raise RuntimeError(f"injected {point}")

    with pytest.raises(RuntimeError, match=point):
        store.mint_safe_retry_dispatch_epoch(
            **_turnover(authority=authority, fault_injector=fail),
        )

    assert _source_epoch_state(store.path) == before


def test_safe_retry_turnover_ack_loss_after_commit_replays_exact_epoch(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store, authority = _store_with_safe_retry_and_authority(tmp_path)

    def lose_ack(point: str) -> None:
        if point == "after_commit":
            raise ConnectionError("lost safe-retry commit response")

    with pytest.raises(ConnectionError, match="lost safe-retry"):
        store.mint_safe_retry_dispatch_epoch(
            **_turnover(authority=authority, fault_injector=lose_ack),
        )
    committed_state = _source_epoch_state(store.path)

    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    replayed = reopened.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )

    assert replayed.dispatch.dispatch_authorization_ordinal == 2
    assert _source_epoch_state(store.path) == committed_state


@pytest.mark.parametrize(
    "lease_change",
    ("release", "expire", "newer_attempt"),
)
def test_safe_retry_turnover_committed_replay_ignores_later_lease_lifecycle(
    tmp_path: Path,
    lease_change: str,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    committed = store.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )

    if lease_change == "expire":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET lease_expires_at = '2026-07-19T00:00:08Z'
                WHERE runtime_run_id = 'runtime_run_1' AND status = 'active'
                """
            )
    else:
        store.release_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            attempt_no=2,
            released_at="2026-07-19T00:00:09Z",
        )
        if lease_change == "newer_attempt":
            store.acquire_executor_lease(
                runtime_run_id="runtime_run_1",
                executor_id="executor_1",
                acquired_at="2026-07-19T00:00:10Z",
                lease_expires_at="2026-07-19T00:01:00Z",
            )
    before = _source_epoch_state(store.path)

    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    replayed = reopened.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )

    assert replayed == committed
    assert _source_epoch_state(store.path) == before


def test_identical_concurrent_turnovers_return_the_same_single_epoch(
    tmp_path: Path,
) -> None:
    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    barrier = Barrier(2)

    def mint():
        barrier.wait()
        return store.mint_safe_retry_dispatch_epoch(
            **_turnover(authority=authority),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: mint(), range(2)))

    assert results[0] == results[1]
    state = _source_epoch_state(store.path)
    assert len(state["expectation"]) == 2
    assert len(state["outbox"]) == 2


def test_conflicting_concurrent_turnovers_commit_one_and_reject_one(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    barrier = Barrier(2)

    def mint(index: int):
        barrier.wait()
        try:
            return store.mint_safe_retry_dispatch_epoch(
                **_turnover(
                    authority=authority,
                    outbox_id=f"source_outbox_{index + 2}",
                    dispatch_intent_id=f"dispatch_intent_{index + 2}",
                ),
            )
        except RuntimeControlError as exc:
            return exc.reason_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(mint, range(2)))

    committed = [result for result in results if not isinstance(result, str)]
    rejected = [result for result in results if isinstance(result, str)]
    assert len(committed) == 1
    assert rejected == ["source_safe_retry_idempotency_conflict"]
    state = _source_epoch_state(store.path)
    assert len(state["expectation"]) == 2
    assert len(state["outbox"]) == 2


@pytest.mark.parametrize(
    ("setup", "reason_code"),
    (
        ("missing_reconciliation", "source_safe_retry_reconciliation_not_found"),
        ("conclusive_reconciliation", "source_safe_retry_reconciliation_conflict"),
        ("unresolved_reconciliation", "source_safe_retry_reconciliation_conflict"),
        ("stale_ledger", "source_safe_retry_revision_conflict"),
        ("stale_reconciliation", "source_safe_retry_revision_conflict"),
        ("logical_identity", "source_safe_retry_identity_conflict"),
        ("missing_lease", "source_safe_retry_lease_missing"),
        ("expired_lease", "source_safe_retry_lease_expired"),
        ("stale_authority", "source_safe_retry_authority_stale"),
        ("stale_attempt", "source_safe_retry_attempt_stale"),
        ("outbox_identity", "source_safe_retry_dispatch_identity_conflict"),
        ("intent_identity", "source_safe_retry_dispatch_identity_conflict"),
    ),
)
def test_safe_retry_turnover_rejection_matrix_is_zero_write(
    tmp_path: Path,
    setup: str,
    reason_code: str,
) -> None:
    from tests.test_runtime_control_source_reconciliation import (
        _conclusive_decision,
        _store_with_operation,
        _unresolved_decision,
    )

    request_changes: dict[str, object] = {}
    if setup in {"conclusive_reconciliation", "unresolved_reconciliation"}:
        store = _store_with_operation(tmp_path)
        decision = _conclusive_decision() if setup == "conclusive_reconciliation" else _unresolved_decision()
        store.commit_no_owner_source_reconciliation(decision)
        authority = _acquire_turnover_authority(store, first_attempt=True)
    elif setup == "missing_reconciliation":
        store = _store_with_operation(tmp_path)
        authority = _acquire_turnover_authority(store, first_attempt=True)
    elif setup == "stale_attempt":
        store = _store_with_operation(tmp_path)
        from tests.test_runtime_control_source_reconciliation import _decision

        store.commit_no_owner_source_reconciliation(_decision())
        authority = _acquire_turnover_authority(store, first_attempt=True)
    else:
        store, authority = _store_with_safe_retry_and_authority(tmp_path)

    if setup == "missing_reconciliation":
        pass
    elif setup == "stale_ledger":
        request_changes["expected_reconciliation_ledger_revision"] = 1
    elif setup == "stale_reconciliation":
        request_changes["expected_reconciliation_revision"] = 2
    elif setup == "logical_identity":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """
                UPDATE runtime_control_source_operations
                SET canonical_request_hash = ?
                """,
                ("f" * 64,),
            )
    elif setup == "missing_lease":
        store.release_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            attempt_no=2,
            released_at="2026-07-19T00:00:09Z",
        )
    elif setup == "expired_lease":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """
                UPDATE runtime_control_executor_leases
                SET lease_expires_at = '2026-07-19T00:00:08Z'
                WHERE runtime_run_id = 'runtime_run_1' AND status = 'active'
                """
            )
    elif setup == "stale_authority":
        store.release_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            attempt_no=2,
            released_at="2026-07-19T00:00:09Z",
        )
        store.acquire_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            acquired_at="2026-07-19T00:00:10Z",
            lease_expires_at="2026-07-19T00:01:00Z",
        )
    elif setup == "outbox_identity":
        request_changes["outbox_id"] = "source_outbox_1"
    elif setup == "intent_identity":
        request_changes["dispatch_intent_id"] = "dispatch_intent_1"

    before = _source_epoch_state(store.path)
    _assert_turnover_rejected(
        store,
        _turnover(authority=authority, **request_changes),
        reason_code,
    )
    assert _source_epoch_state(store.path) == before


def test_safe_retry_authority_is_store_owned_identity_not_caller_value(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    before = _source_epoch_state(store.path)
    cloned = object.__new__(type(authority))
    object.__setattr__(
        cloned,
        "_issuer",
        object.__getattribute__(authority, "_issuer"),
    )
    object.__setattr__(
        cloned,
        "_facts",
        object.__getattribute__(authority, "_facts"),
    )

    for candidate_store, candidate in (
        (store, object()),
        (store, cloned),
        (RuntimeControlStore(store.path), authority),
    ):
        _assert_turnover_rejected(
            candidate_store,
            _turnover(authority=candidate),
            "source_safe_retry_authority_invalid",
        )

    assert _source_epoch_state(store.path) == before


def test_safe_retry_turnover_rejects_authority_for_another_active_run(
    tmp_path: Path,
) -> None:
    from tests.test_runtime_control_source_operations import _add_run

    store, _authority = _store_with_safe_retry_and_authority(tmp_path)
    _add_run(store, runtime_run_id="runtime_run_2")
    other_lease = store.acquire_executor_lease(
        runtime_run_id="runtime_run_2",
        executor_id="executor_2",
        acquired_at="2026-07-19T00:00:07Z",
        lease_expires_at="2026-07-19T00:01:00Z",
    )
    other_authority = store._mint_safe_retry_turnover_authority_for_test(
        runtime_run_id="runtime_run_2",
        executor_id="executor_2",
        attempt_no=other_lease.attempt_no,
        observed_at="2026-07-19T00:00:08Z",
        runtime_attempt_authority_ref="runtime_attempt_authority_ref_other",
        runtime_attempt_fence_ref="f" * 64,
        profile_binding_generation=1,
        browser_control_scope_id="browser_scope_other",
        controller_fence_ref=None,
    )
    before = _source_epoch_state(store.path)

    _assert_turnover_rejected(
        store,
        _turnover(authority=other_authority),
        "source_safe_retry_authority_conflict",
    )

    assert _source_epoch_state(store.path) == before


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    (
        (
            {"observed_at": "not-a-timestamp"},
            "source_safe_retry_authority_observed_at_invalid",
        ),
        (
            {"runtime_attempt_authority_ref": ""},
            "source_operation_runtime_attempt_authority_ref_invalid",
        ),
        (
            {"runtime_attempt_fence_ref": "short"},
            "source_operation_runtime_attempt_fence_ref_invalid",
        ),
        (
            {"profile_binding_generation": 0},
            "source_operation_profile_binding_generation_invalid",
        ),
        (
            {"browser_control_scope_id": None},
            "source_safe_retry_browser_control_scope_invalid",
        ),
        (
            {"controller_fence_ref": "F" * 64},
            "source_operation_controller_fence_ref_invalid",
        ),
    ),
)
def test_safe_retry_authority_factory_rejects_malformed_facts_without_writes(
    tmp_path: Path,
    changes: dict[str, object],
    reason_code: str,
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store, _authority = _store_with_safe_retry_and_authority(tmp_path)
    before = _source_epoch_state(store.path)
    values: dict[str, object] = {
        "runtime_run_id": "runtime_run_1",
        "executor_id": "executor_1",
        "attempt_no": 2,
        "observed_at": "2026-07-19T00:00:08Z",
        "runtime_attempt_authority_ref": ("runtime_attempt_authority_ref_2"),
        "runtime_attempt_fence_ref": "d" * 64,
        "profile_binding_generation": 2,
        "browser_control_scope_id": "browser_scope_2",
        "controller_fence_ref": "e" * 64,
    }
    values.update(changes)

    with pytest.raises(RuntimeControlError) as exc_info:
        store._mint_safe_retry_turnover_authority_for_test(**values)
    assert exc_info.value.reason_code == reason_code
    assert _source_epoch_state(store.path) == before


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    (
        (
            {"runtime_run_id": ""},
            "source_safe_retry_runtime_run_id_invalid",
        ),
        (
            {"operation_id": " operation "},
            "source_safe_retry_operation_id_invalid",
        ),
        (
            {"reconciliation_id": 1},
            "source_safe_retry_reconciliation_id_invalid",
        ),
        (
            {"expected_reconciliation_ledger_revision": True},
            ("source_safe_retry_expected_reconciliation_ledger_revision_invalid"),
        ),
        (
            {"expected_reconciliation_revision": 0},
            "source_safe_retry_expected_reconciliation_revision_invalid",
        ),
        (
            {"outbox_id": "x" * 97},
            "source_safe_retry_outbox_id_invalid",
        ),
        (
            {"dispatch_intent_id": "bad\nintent"},
            "source_safe_retry_dispatch_intent_id_invalid",
        ),
    ),
)
def test_safe_retry_turnover_rejects_malformed_request_without_writes(
    tmp_path: Path,
    changes: dict[str, object],
    reason_code: str,
) -> None:
    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    before = _source_epoch_state(store.path)

    _assert_turnover_rejected(
        store,
        _turnover(authority=authority, **changes),
        reason_code,
    )

    assert _source_epoch_state(store.path) == before


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    (
        ("ordinal", "source_safe_retry_ordinal_overflow"),
        (
            "dispatch_intent_revision",
            "source_safe_retry_dispatch_intent_revision_overflow",
        ),
        ("ledger_revision", "source_safe_retry_revision_overflow"),
    ),
)
def test_safe_retry_turnover_rejects_json_safe_counter_overflow_without_writes(
    tmp_path: Path,
    mutation: str,
    reason_code: str,
) -> None:
    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    if mutation == "ordinal":
        _insert_max_ordinal_epoch(store.path)
    elif mutation == "dispatch_intent_revision":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """
                UPDATE runtime_control_source_dispatch_outbox
                SET dispatch_intent_revision = 9007199254740991
                WHERE dispatch_authorization_ordinal = 1
                """
            )
    else:
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                """
                DROP TRIGGER runtime_control_source_reconciliations_no_update
                """
            )
            conn.execute(
                """
                UPDATE runtime_control_source_reconciliations
                SET expected_ledger_revision = 9007199254740990,
                    committed_ledger_revision = 9007199254740991
                """
            )
            conn.execute(
                """
                UPDATE runtime_control_source_operations
                SET ledger_revision = 9007199254740991
                """
            )
    before = _source_epoch_state(store.path)

    _assert_turnover_rejected(
        store,
        _turnover(
            authority=authority,
            expected_reconciliation_ledger_revision=(9007199254740991 if mutation == "ledger_revision" else 2),
        ),
        reason_code,
    )

    assert _source_epoch_state(store.path) == before


def test_safe_retry_epoch_cannot_use_legacy_direct_ack_path(tmp_path: Path) -> None:
    store, authority = _store_with_safe_retry_and_authority(tmp_path)
    committed = store.mint_safe_retry_dispatch_epoch(
        **_turnover(authority=authority),
    )
    before = _source_epoch_state(store.path)

    _assert_turnover_rejected(
        store,
        {
            "runtime_run_id": committed.dispatch.runtime_run_id,
            "operation_id": committed.dispatch.operation_id,
            "outbox_id": committed.dispatch.outbox_id,
            "canonical_request_hash": (committed.dispatch.canonical_request_hash),
            "dispatch_intent_id": committed.dispatch.dispatch_intent_id,
            "dispatch_intent_revision": (committed.dispatch.dispatch_intent_revision),
            "dispatch_intent_digest": (committed.dispatch.dispatch_intent_digest),
            "dispatch_authorization_ordinal": 2,
            "expected_outbox_revision": 1,
            "accepted_sidecar_generation": 2,
            "accepted_sidecar_journal_revision": 2,
            "ack_ref": "source_ack_ref_2",
            "ack_kind": "new_dispatch_authorization",
            "acknowledged_at": "2026-07-19T00:00:09Z",
        },
        "source_dispatch_authorization_ordinal_invalid",
        method="record_source_dispatch_ack",
    )

    assert _source_epoch_state(store.path) == before


@pytest.mark.parametrize(
    ("ordinal", "safe_retry_ref", "ledger_revision", "reconciliation_revision"),
    (
        (1, "reconciliation_invalid", 1, 0),
        (2, None, 2, 1),
        (2, "reconciliation_invalid", 2, 0),
        (1, None, 2, 0),
    ),
)
def test_v12_outbox_closes_initial_and_safe_retry_epoch_matrix(
    tmp_path: Path,
    ordinal: int,
    safe_retry_ref: str | None,
    ledger_revision: int,
    reconciliation_revision: int,
) -> None:
    from tests.test_runtime_control_source_reconciliation import (
        _store_with_operation,
    )

    store = _store_with_operation(tmp_path)
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO runtime_control_source_dispatch_outbox (
                    outbox_id, runtime_run_id, operation_id,
                    canonical_request_hash, dispatch_intent_id,
                    dispatch_intent_revision, dispatch_intent_digest,
                    dispatch_authorization_ordinal,
                    safe_retry_commit_ref,
                    source_operation_acceptance_ref,
                    expected_ledger_revision,
                    expected_reconciliation_revision,
                    status, outbox_revision,
                    accepted_sidecar_generation,
                    accepted_sidecar_journal_revision,
                    ack_ref, ack_kind, acknowledged_at
                )
                VALUES (
                    'invalid_outbox', 'runtime_run_1',
                    'source_operation_1', ?, 'invalid_intent',
                    2, ?, ?, ?, 'source_acceptance_ref_1',
                    ?, ?, 'pending', 1,
                    NULL, NULL, NULL, NULL, NULL
                )
                """,
                (
                    "a" * 64,
                    "f" * 64,
                    ordinal,
                    safe_retry_ref,
                    ledger_revision,
                    reconciliation_revision,
                ),
            )


def _store_with_safe_retry_and_authority(tmp_path: Path):
    from tests.test_runtime_control_source_reconciliation import (
        _decision,
        _store_with_operation,
    )

    store = _store_with_operation(tmp_path)
    store.commit_no_owner_source_reconciliation(_decision())
    first = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-07-19T00:00:05Z",
        lease_expires_at="2026-07-19T00:01:00Z",
    )
    store.release_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        attempt_no=first.attempt_no,
        released_at="2026-07-19T00:00:06Z",
    )
    second = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-07-19T00:00:07Z",
        lease_expires_at="2026-07-19T00:01:00Z",
    )
    authority = store._mint_safe_retry_turnover_authority_for_test(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        attempt_no=second.attempt_no,
        observed_at="2026-07-19T00:00:08Z",
        runtime_attempt_authority_ref="runtime_attempt_authority_ref_2",
        runtime_attempt_fence_ref="d" * 64,
        profile_binding_generation=2,
        browser_control_scope_id="browser_scope_2",
        controller_fence_ref="e" * 64,
    )
    return store, authority


def _acquire_turnover_authority(store, *, first_attempt: bool):
    if not first_attempt:
        first = store.acquire_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            acquired_at="2026-07-19T00:00:05Z",
            lease_expires_at="2026-07-19T00:01:00Z",
        )
        store.release_executor_lease(
            runtime_run_id="runtime_run_1",
            executor_id="executor_1",
            attempt_no=first.attempt_no,
            released_at="2026-07-19T00:00:06Z",
        )
    current = store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        acquired_at="2026-07-19T00:00:07Z",
        lease_expires_at="2026-07-19T00:01:00Z",
    )
    return store._mint_safe_retry_turnover_authority_for_test(
        runtime_run_id="runtime_run_1",
        executor_id="executor_1",
        attempt_no=current.attempt_no,
        observed_at="2026-07-19T00:00:08Z",
        runtime_attempt_authority_ref=(f"runtime_attempt_authority_ref_{current.attempt_no}"),
        runtime_attempt_fence_ref="d" * 64,
        profile_binding_generation=current.attempt_no,
        browser_control_scope_id=(f"browser_scope_{current.attempt_no}"),
        controller_fence_ref="e" * 64,
    )


def _turnover(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "runtime_run_id": "runtime_run_1",
        "operation_id": "source_operation_1",
        "reconciliation_id": "reconciliation_1",
        "expected_reconciliation_ledger_revision": 2,
        "expected_reconciliation_revision": 1,
        "outbox_id": "source_outbox_2",
        "dispatch_intent_id": "dispatch_intent_2",
    }
    values.update(changes)
    return values


def _assert_turnover_rejected(
    store,
    request: dict[str, object],
    reason_code: str,
    *,
    method: str = "mint_safe_retry_dispatch_epoch",
) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    with pytest.raises(RuntimeControlError) as exc_info:
        getattr(store, method)(**request)
    assert exc_info.value.reason_code == reason_code


def _source_epoch_state(
    path: Path,
) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(path) as conn:
        return {
            "operation": conn.execute(
                """
                SELECT runtime_run_id, operation_id, source_id,
                       operation_kind, canonical_request_hash,
                       idempotency_key,
                       accepted_requirement_revision_id,
                       runtime_attempt_no,
                       runtime_attempt_authority_ref,
                       operation_phase, dispatch_intent_ref,
                       conclusive_observation_ref,
                       source_operation_disposition, retry_posture,
                       reconciliation_revision, main_commit_ref,
                       ledger_revision
                FROM runtime_control_source_operations
                ORDER BY runtime_run_id, operation_id
                """
            ).fetchall(),
            "expectation": conn.execute(
                """
                SELECT runtime_run_id, operation_id,
                       dispatch_authorization_ordinal,
                       runtime_attempt_no,
                       runtime_attempt_authority_ref,
                       runtime_attempt_fence_ref,
                       profile_binding_generation,
                       browser_control_scope_id,
                       controller_fence_ref
                FROM runtime_control_source_operation_admission_expectations
                ORDER BY runtime_run_id, operation_id,
                         dispatch_authorization_ordinal
                """
            ).fetchall(),
            "outbox": conn.execute(
                """
                SELECT outbox_id, runtime_run_id, operation_id,
                       canonical_request_hash, dispatch_intent_id,
                       dispatch_intent_revision,
                       dispatch_intent_digest,
                       dispatch_authorization_ordinal,
                       safe_retry_commit_ref,
                       source_operation_acceptance_ref,
                       expected_ledger_revision,
                       expected_reconciliation_revision,
                       status, outbox_revision,
                       accepted_sidecar_generation,
                       accepted_sidecar_journal_revision,
                       ack_ref, ack_kind, acknowledged_at
                FROM runtime_control_source_dispatch_outbox
                ORDER BY runtime_run_id, operation_id,
                         dispatch_authorization_ordinal
                """
            ).fetchall(),
            "reconciliation": conn.execute(
                """
                SELECT *
                FROM runtime_control_source_reconciliations
                ORDER BY reconciliation_id
                """
            ).fetchall(),
        }


def _insert_max_ordinal_epoch(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_source_operation_admission_expectations (
                runtime_run_id, operation_id,
                dispatch_authorization_ordinal,
                runtime_attempt_no,
                runtime_attempt_authority_ref,
                runtime_attempt_fence_ref,
                profile_binding_generation,
                browser_control_scope_id,
                controller_fence_ref
            )
            VALUES (
                'runtime_run_1', 'source_operation_1',
                9007199254740991, 1,
                'runtime_attempt_authority_ref_1',
                ?, 1, 'synthetic_scope', NULL
            )
            """,
            ("c" * 64,),
        )
        conn.execute(
            """
            INSERT INTO runtime_control_source_dispatch_outbox (
                outbox_id, runtime_run_id, operation_id,
                canonical_request_hash, dispatch_intent_id,
                dispatch_intent_revision, dispatch_intent_digest,
                dispatch_authorization_ordinal,
                safe_retry_commit_ref,
                source_operation_acceptance_ref,
                expected_ledger_revision,
                expected_reconciliation_revision,
                status, outbox_revision,
                accepted_sidecar_generation,
                accepted_sidecar_journal_revision,
                ack_ref, ack_kind, acknowledged_at
            )
            VALUES (
                'synthetic_max_outbox', 'runtime_run_1',
                'source_operation_1', ?, 'synthetic_max_intent',
                2, ?, 9007199254740991,
                'synthetic_retry_ref',
                'source_acceptance_ref_1',
                2, 1, 'pending', 1,
                NULL, NULL, NULL, NULL, NULL
            )
            """,
            ("a" * 64, "f" * 64),
        )


def _source_epoch_version(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _source_epoch_migration_tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (
                    'runtime_control_source_dispatch_outbox_v12',
                    'runtime_control_source_operation_admission_expectations_v12'
                  )
                """
            )
        }


def _source_epoch_rows(
    path: Path,
    *,
    legacy_columns: bool = False,
) -> dict[str, list[tuple[object, ...]]]:
    expectation_columns = (
        "runtime_run_id, operation_id, runtime_attempt_fence_ref, "
        "profile_binding_generation, browser_control_scope_id, controller_fence_ref"
    )
    outbox_columns = (
        "outbox_id, runtime_run_id, operation_id, canonical_request_hash, "
        "dispatch_intent_id, dispatch_intent_revision, dispatch_intent_digest, "
        "dispatch_authorization_ordinal, source_operation_acceptance_ref, "
        "expected_ledger_revision, expected_reconciliation_revision, status, "
        "outbox_revision, accepted_sidecar_generation, accepted_sidecar_journal_revision, "
        "ack_ref, ack_kind, acknowledged_at"
    )
    with sqlite3.connect(path) as conn:
        return {
            "operation": conn.execute(
                "SELECT * FROM runtime_control_source_operations ORDER BY runtime_run_id, operation_id"
            ).fetchall(),
            "expectation": conn.execute(
                f"""
                SELECT {expectation_columns}
                FROM runtime_control_source_operation_admission_expectations
                ORDER BY runtime_run_id, operation_id
                """
            ).fetchall(),
            "outbox": conn.execute(
                f"""
                SELECT {outbox_columns}
                FROM runtime_control_source_dispatch_outbox
                ORDER BY outbox_id
                """
            ).fetchall(),
            "reconciliation": conn.execute(
                "SELECT * FROM runtime_control_source_reconciliations ORDER BY reconciliation_id"
            ).fetchall(),
        }


def _downgrade_source_epochs_to_v11(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runtime_control_source_dispatch_outbox)")}
        if "safe_retry_commit_ref" not in columns:
            conn.execute("PRAGMA user_version = 11")
            return
        for trigger_name in (
            "trg_runtime_source_admission_expectation_no_update",
            "trg_runtime_source_admission_expectation_no_delete",
            "trg_runtime_source_admission_expectation_no_replace",
        ):
            conn.execute(f"DROP TRIGGER {trigger_name}")
        conn.executescript(
            """
            CREATE TABLE runtime_control_source_dispatch_outbox_v11 (
              outbox_id TEXT PRIMARY KEY,
              runtime_run_id TEXT NOT NULL,
              operation_id TEXT NOT NULL,
              canonical_request_hash TEXT NOT NULL,
              dispatch_intent_id TEXT NOT NULL,
              dispatch_intent_revision INTEGER NOT NULL,
              dispatch_intent_digest TEXT NOT NULL,
              dispatch_authorization_ordinal INTEGER NOT NULL,
              source_operation_acceptance_ref TEXT NOT NULL,
              expected_ledger_revision INTEGER NOT NULL,
              expected_reconciliation_revision INTEGER NOT NULL,
              status TEXT NOT NULL,
              outbox_revision INTEGER NOT NULL,
              accepted_sidecar_generation INTEGER,
              accepted_sidecar_journal_revision INTEGER,
              ack_ref TEXT,
              ack_kind TEXT,
              acknowledged_at TEXT,
              UNIQUE(runtime_run_id, operation_id, dispatch_authorization_ordinal),
              UNIQUE(runtime_run_id, dispatch_intent_id),
              CHECK (dispatch_intent_revision > 0),
              CHECK (dispatch_authorization_ordinal = 1),
              CHECK (expected_ledger_revision = 1),
              CHECK (expected_reconciliation_revision = 0),
              CHECK (status IN ('pending', 'acknowledged')),
              CHECK (outbox_revision > 0),
              CHECK (accepted_sidecar_generation IS NULL OR accepted_sidecar_generation > 0),
              CHECK (accepted_sidecar_journal_revision IS NULL OR accepted_sidecar_journal_revision > 0),
              CHECK (ack_kind IS NULL OR ack_kind IN (
                'new_logical_operation', 'new_dispatch_authorization', 'same_intent_replay'
              )),
              CHECK (
                (status = 'pending' AND outbox_revision = 1
                  AND accepted_sidecar_generation IS NULL
                  AND accepted_sidecar_journal_revision IS NULL
                  AND ack_ref IS NULL AND ack_kind IS NULL AND acknowledged_at IS NULL)
                OR
                (status = 'acknowledged' AND outbox_revision = 2
                  AND accepted_sidecar_generation IS NOT NULL
                  AND accepted_sidecar_journal_revision IS NOT NULL
                  AND ack_ref IS NOT NULL AND ack_kind IS NOT NULL AND acknowledged_at IS NOT NULL)
              )
            );
            INSERT INTO runtime_control_source_dispatch_outbox_v11
            SELECT outbox_id, runtime_run_id, operation_id,
                   canonical_request_hash, dispatch_intent_id,
                   dispatch_intent_revision, dispatch_intent_digest,
                   dispatch_authorization_ordinal,
                   source_operation_acceptance_ref,
                   expected_ledger_revision,
                   expected_reconciliation_revision, status,
                   outbox_revision, accepted_sidecar_generation,
                   accepted_sidecar_journal_revision, ack_ref,
                   ack_kind, acknowledged_at
            FROM runtime_control_source_dispatch_outbox;

            CREATE TABLE runtime_control_source_operation_admission_expectations_v11 (
              runtime_run_id TEXT NOT NULL,
              operation_id TEXT NOT NULL,
              runtime_attempt_fence_ref TEXT NOT NULL,
              profile_binding_generation INTEGER NOT NULL,
              browser_control_scope_id TEXT,
              controller_fence_ref TEXT,
              PRIMARY KEY(runtime_run_id, operation_id),
              FOREIGN KEY(runtime_run_id, operation_id)
                REFERENCES runtime_control_source_operations(runtime_run_id, operation_id),
              CHECK (
                length(runtime_attempt_fence_ref) = 64
                AND runtime_attempt_fence_ref NOT GLOB '*[^0-9a-f]*'
              ),
              CHECK (
                typeof(profile_binding_generation) = 'integer'
                AND profile_binding_generation BETWEEN 1 AND 9007199254740991
              ),
              CHECK (
                browser_control_scope_id IS NULL
                OR (
                  length(CAST(browser_control_scope_id AS BLOB)) BETWEEN 1 AND 96
                  AND browser_control_scope_id = trim(browser_control_scope_id)
                )
              ),
              CHECK (
                controller_fence_ref IS NULL
                OR (
                  length(controller_fence_ref) = 64
                  AND controller_fence_ref NOT GLOB '*[^0-9a-f]*'
                )
              )
            );
            INSERT INTO runtime_control_source_operation_admission_expectations_v11
            SELECT runtime_run_id, operation_id,
                   runtime_attempt_fence_ref,
                   profile_binding_generation,
                   browser_control_scope_id, controller_fence_ref
            FROM runtime_control_source_operation_admission_expectations;

            DROP TABLE runtime_control_source_operation_admission_expectations;
            DROP TABLE runtime_control_source_dispatch_outbox;
            ALTER TABLE runtime_control_source_dispatch_outbox_v11
              RENAME TO runtime_control_source_dispatch_outbox;
            ALTER TABLE runtime_control_source_operation_admission_expectations_v11
              RENAME TO runtime_control_source_operation_admission_expectations;
            CREATE INDEX idx_runtime_source_dispatch_pending
              ON runtime_control_source_dispatch_outbox(status, outbox_id);
            CREATE TRIGGER trg_runtime_source_admission_expectation_no_update
            BEFORE UPDATE ON runtime_control_source_operation_admission_expectations
            BEGIN
              SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
            END;
            CREATE TRIGGER trg_runtime_source_admission_expectation_no_delete
            BEFORE DELETE ON runtime_control_source_operation_admission_expectations
            BEGIN
              SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
            END;
            CREATE TRIGGER trg_runtime_source_admission_expectation_no_replace
            BEFORE INSERT ON runtime_control_source_operation_admission_expectations
            WHEN EXISTS (
              SELECT 1
              FROM runtime_control_source_operation_admission_expectations
              WHERE runtime_run_id = NEW.runtime_run_id
                AND operation_id = NEW.operation_id
            )
            BEGIN
              SELECT RAISE(ABORT, 'source_operation_admission_expectation_immutable');
            END;
            PRAGMA user_version = 11;
            """
        )
