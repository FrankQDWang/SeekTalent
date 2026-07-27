from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap
import threading

import pytest

from seektalent.diagnostics_schema import parse_failure_envelope
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import (
    RuntimeCheckpoint,
    RuntimeControlEventInput,
    RuntimeRunRecord,
)
from seektalent_runtime_control.store import RuntimeControlStore
from tests.test_diagnostics_schema import _failure


RUN_ID = "3" * 32
TERMINAL_AT = "2026-07-27T01:02:03Z"
LIVE_LEASE_EXPIRES_AT = "2099-01-01T00:00:00Z"


def _envelope(
    *,
    run_id: str = RUN_ID,
    failure_id: str = "7" * 32,
    revision: int = 1,
    outcome: str = "failed",
    attempt_no: int | None = 1,
    occurred_at: str = TERMINAL_AT,
):
    payload = _failure()
    payload.update(
        {
            "run_id": run_id,
            "failure_id": failure_id,
            "revision": revision,
            "current_outcome": outcome,
            "attempt_no": attempt_no,
            "occurred_at": occurred_at,
            "observed_at": occurred_at,
        }
    )
    if attempt_no is None:
        payload["operation_id"] = None
    return parse_failure_envelope(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )


def _store(tmp_path: Path, *, status: str = "queued") -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=RUN_ID,
            run_intent_id=f"intent_{RUN_ID}",
            start_idempotency_key=f"start_{RUN_ID}",
            run_kind="primary",
            approved_requirement_revision_id="reqapproved_test",
            status=status,
            current_stage=status,
            source_ids=["liepin"],
            created_at="2026-07-27T00:00:00Z",
            updated_at="2026-07-27T00:00:00Z",
        )
    )
    return store


def _failure_truth(path: Path) -> tuple[object, ...]:
    with sqlite3.connect(path) as conn:
        run = conn.execute(
            """
            SELECT status, product_outcome, current_failure_id,
                   current_failure_revision, state_revision
            FROM runtime_control_runs
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        ).fetchone()
        envelope_count = conn.execute(
            "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
        ).fetchone()[0]
        active_count = conn.execute(
            """
            SELECT COUNT(*) FROM runtime_control_executor_leases
            WHERE runtime_run_id = ? AND status = 'active'
            """,
            (RUN_ID,),
        ).fetchone()[0]
    return (*run, envelope_count, active_count)


def test_no_owner_commit_is_atomic_and_exact_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    envelope = _envelope(attempt_no=None)

    created = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    replay = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )

    assert created == replay
    assert created.status == "failed"
    assert created.product_outcome == "failed"
    assert created.current_failure_id == envelope.failure_id
    assert created.current_failure_revision == envelope.revision
    assert created.current_failure_owner_lease_id is None
    assert created.current_failure_authority_mode == "no_owner"
    assert created.state_revision == 1
    assert _failure_truth(store.path) == (
        "failed",
        "failed",
        envelope.failure_id,
        1,
        1,
        1,
        0,
    )


def test_no_owner_commit_can_preserve_historical_attempt_context(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    store.release_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
        released_at="2026-07-27T00:01:00Z",
        reason_code="normal_release",
    )
    expected_revision = store.get_run(RUN_ID).state_revision
    envelope = _envelope(attempt_no=lease.attempt_no)

    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
    )
    readback = store.get_run(RUN_ID)
    replay = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
    )

    assert committed == readback == replay
    assert committed.current_failure_owner_lease_id is None
    assert committed.current_failure_authority_mode == "no_owner"
    with pytest.raises(RuntimeControlError) as masquerade_exc:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=envelope,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=expected_revision,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
        )
    assert (
        masquerade_exc.value.reason_code
        == "runtime_failed_outcome_replay_conflict"
    )
    for replay_kwargs in (
        {"executor_id": lease.executor_id},
        {"attempt_no": lease.attempt_no},
    ):
        with pytest.raises(RuntimeControlError) as partial_exc:
            store.commit_failed_outcome(
                runtime_run_id=RUN_ID,
                envelope=envelope,
                terminal_reason_code="source_operation_failed",
                terminal_at=TERMINAL_AT,
                expected_state_revision=expected_revision,
                **replay_kwargs,
            )
        assert (
            partial_exc.value.reason_code
            == "runtime_failed_outcome_replay_conflict"
        )


def test_run_creation_rejects_input_canonical_truth_and_returns_durable_readback(
    tmp_path: Path,
) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    forged = RuntimeRunRecord(
        runtime_run_id=RUN_ID,
        run_intent_id=f"intent_{RUN_ID}",
        start_idempotency_key=f"start_{RUN_ID}",
        approved_requirement_revision_id="reqapproved_test",
        status="failed",
        current_stage="failed",
        source_ids=["liepin"],
        created_at="2026-07-27T00:00:00Z",
        updated_at="2026-07-27T00:00:00Z",
        product_outcome="failed",
        current_failure_id="7" * 32,
        current_failure_revision=1,
        current_failure_owner_lease_id="rtlease_forged",
        current_failure_authority_mode="active_owner",
        state_revision=42,
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.create_run(forged)
    assert (
        exc_info.value.reason_code
        == "runtime_run_initial_canonical_truth_forbidden"
    )
    with pytest.raises(RuntimeControlError, match="runtime_run_not_found"):
        store.get_run(RUN_ID)

    initial = forged.model_copy(
        update={
            "status": "queued",
            "current_stage": "queued",
            "product_outcome": None,
            "current_failure_id": None,
            "current_failure_revision": None,
            "current_failure_owner_lease_id": None,
            "current_failure_authority_mode": None,
            "state_revision": 0,
        }
    )
    created = store.create_run(initial)
    durable = store.get_run(RUN_ID)
    replay = store.create_run(initial)
    assert created == durable == replay


def test_active_owner_commit_releases_exact_current_authority(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )

    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=lease.attempt_no),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )

    assert committed.status == "failed"
    assert committed.current_failure_owner_lease_id == lease.lease_id
    assert committed.current_failure_authority_mode == "active_owner"
    assert _failure_truth(store.path)[-1] == 0


def test_active_owner_replay_requires_complete_owner_argument_shape(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    envelope = _envelope(attempt_no=lease.attempt_no)
    expected_revision = store.get_run(RUN_ID).state_revision
    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )

    exact = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    assert exact == committed
    for replay_kwargs in (
        {},
        {"executor_id": lease.executor_id},
        {"attempt_no": lease.attempt_no},
    ):
        with pytest.raises(RuntimeControlError) as exc_info:
            store.commit_failed_outcome(
                runtime_run_id=RUN_ID,
                envelope=envelope,
                terminal_reason_code="source_operation_failed",
                terminal_at=TERMINAL_AT,
                expected_state_revision=expected_revision,
                **replay_kwargs,
            )
        assert (
            exc_info.value.reason_code
            == "runtime_failed_outcome_replay_conflict"
        )


@pytest.mark.parametrize(
    "tamper_sql",
    (
        """
        UPDATE runtime_control_runs
        SET current_failure_owner_lease_id = NULL
        WHERE runtime_run_id = ?
        """,
        """
        UPDATE runtime_control_runs
        SET current_failure_authority_mode = 'no_owner'
        WHERE runtime_run_id = ?
        """,
    ),
)
def test_active_owner_mode_and_reference_cannot_be_downgraded_individually(
    tmp_path: Path,
    tamper_sql: str,
) -> None:
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=lease.attempt_no),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )

    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(tamper_sql, (RUN_ID,))

    assert store.get_run(RUN_ID) == committed


def test_no_owner_mode_cannot_be_changed_without_owner_reference(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=None),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )

    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET current_failure_authority_mode = 'active_owner'
                WHERE runtime_run_id = ?
                """,
                (RUN_ID,),
            )

    assert store.get_run(RUN_ID) == committed


def test_no_owner_fake_active_provenance_fails_closed_on_readback_and_replay(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    envelope = _envelope(attempt_no=None)
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET current_failure_authority_mode = 'active_owner',
                current_failure_owner_lease_id = 'rtlease_fake'
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        )

    with pytest.raises(RuntimeControlError) as read_exc_info:
        store.get_run(RUN_ID)
    assert (
        read_exc_info.value.reason_code
        == "runtime_failed_outcome_integrity_failed"
    )
    with pytest.raises(RuntimeControlError) as replay_exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=envelope,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )
    assert (
        replay_exc_info.value.reason_code
        == "runtime_failed_outcome_replay_conflict"
    )


def test_store_decision_time_rejects_backdated_expired_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent_runtime_control import failed_outcome as outcome_module

    decision_at = "2026-07-27T01:00:00Z"
    occurred_at = "2000-01-01T00:05:00Z"
    monkeypatch.setattr(
        outcome_module,
        "_store_decision_time",
        lambda: decision_at,
        raising=False,
    )
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2000-01-01T00:00:00Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2000-01-01T00:00:00Z",
        lease_expires_at="2000-01-01T00:10:00Z",
    )
    expected_revision = store.get_run(RUN_ID).state_revision

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(
                attempt_no=lease.attempt_no,
                occurred_at=occurred_at,
            ),
            terminal_reason_code="source_operation_failed",
            terminal_at=occurred_at,
            expected_state_revision=expected_revision,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
        )

    assert (
        exc_info.value.reason_code
        == "runtime_failed_outcome_authority_expired"
    )
    assert _failure_truth(store.path) == (
        "starting",
        None,
        None,
        None,
        expected_revision + 1,
        0,
        0,
    )
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            """
            SELECT status, released_at, reason_code
            FROM runtime_control_executor_leases
            WHERE lease_id = ?
            """,
            (lease.lease_id,),
        ).fetchone() == (
            "expired",
            decision_at,
            "runtime_executor_lease_expired",
        )


@pytest.mark.parametrize(
    "occurred_at",
    (
        "2000-01-01T00:05:00Z",
        "2100-01-01T00:05:00Z",
    ),
)
def test_producer_time_cannot_change_live_authority_and_replay_uses_store_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    occurred_at: str,
) -> None:
    from seektalent_runtime_control import failed_outcome as outcome_module

    decision_at = "2026-07-27T01:00:00Z"
    monkeypatch.setattr(
        outcome_module,
        "_store_decision_time",
        lambda: decision_at,
        raising=False,
    )
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:00Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:00Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    envelope = _envelope(
        attempt_no=lease.attempt_no,
        occurred_at=occurred_at,
    )
    expected_revision = store.get_run(RUN_ID).state_revision

    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=occurred_at,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    replay = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=occurred_at,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )

    assert replay == committed
    assert committed.updated_at == decision_at
    assert committed.completed_at == occurred_at
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            """
            SELECT status, released_at, reason_code
            FROM runtime_control_executor_leases
            WHERE lease_id = ?
            """,
            (lease.lease_id,),
        ).fetchone() == (
            "revoked",
            decision_at,
            "source_operation_failed",
        )
        conn.execute(
            """
            UPDATE runtime_control_executor_leases
            SET released_at = '2026-07-27T01:30:00Z'
            WHERE lease_id = ?
            """,
            (lease.lease_id,),
        )
    with pytest.raises(RuntimeControlError) as read_exc_info:
        store.get_run(RUN_ID)
    assert (
        read_exc_info.value.reason_code
        == "runtime_failed_outcome_integrity_failed"
    )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=envelope,
            terminal_reason_code="source_operation_failed",
            terminal_at=occurred_at,
            expected_state_revision=expected_revision,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
        )
    assert exc_info.value.reason_code == "runtime_failed_outcome_replay_conflict"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (
        ({"executor_id": "stale", "attempt_no": 1}, "runtime_failed_outcome_authority_rejected"),
        ({"expected_state_revision": 9}, "runtime_failed_outcome_revision_conflict"),
    ),
)
def test_stale_authority_and_cas_fail_without_partial_truth(
    tmp_path: Path,
    kwargs: dict[str, object],
    reason: str,
) -> None:
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    current_revision = store.get_run(RUN_ID).state_revision

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=kwargs.get(
                "expected_state_revision",
                current_revision,
            ),
            executor_id=kwargs.get("executor_id"),
            attempt_no=kwargs.get("attempt_no"),
        )

    assert exc_info.value.reason_code == reason
    assert _failure_truth(store.path) == ("starting", None, None, None, 2, 0, 1)


def test_no_owner_path_rejects_active_lease_and_cancellation_precedence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    with pytest.raises(RuntimeControlError, match="runtime_failed_outcome_authority_rejected"):
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=store.get_run(RUN_ID).state_revision,
        )

    other = _store(tmp_path / "cancel", status="cancellation_requested")
    with pytest.raises(RuntimeControlError, match="runtime_failed_outcome_cancellation_won"):
        other.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(attempt_no=None),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )


def test_state_changes_advance_the_no_owner_cas_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transitioned = store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    assert transitioned.state_revision == 1

    with pytest.raises(RuntimeControlError) as stale:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(attempt_no=None),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )
    assert stale.value.reason_code == "runtime_failed_outcome_revision_conflict"

    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=None),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=1,
    )
    assert committed.state_revision == 2


@pytest.mark.parametrize("failure_point", range(8))
def test_each_durable_statement_failure_rolls_back_complete_truth(
    tmp_path: Path,
    failure_point: int,
) -> None:
    store = _store(tmp_path)

    def fail_at(index: int, _phase: str) -> None:
        if index == failure_point:
            raise RuntimeError("injected_failed_outcome_statement")

    with pytest.raises(RuntimeError, match="injected_failed_outcome_statement"):
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(attempt_no=None),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
            statement_hook=fail_at,
        )

    expected = (
        ("failed", "failed", "7" * 32, 1, 1, 1, 0)
        if failure_point == 7
        else ("queued", None, None, None, 0, 0, 0)
    )
    assert _failure_truth(store.path) == expected


def test_begin_immediate_lock_contention_is_bounded_and_retryable(
    tmp_path: Path,
) -> None:
    store = RuntimeControlStore(
        tmp_path / "runtime_control.sqlite3",
        busy_timeout_ms=1,
    )
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=RUN_ID,
            run_intent_id=f"intent_{RUN_ID}",
            start_idempotency_key=f"start_{RUN_ID}",
            approved_requirement_revision_id="reqapproved_test",
            status="queued",
            current_stage="queued",
            source_ids=["liepin"],
            created_at="2026-07-27T00:00:00Z",
            updated_at="2026-07-27T00:00:00Z",
        )
    )
    envelope = _envelope(attempt_no=None)
    blocker = sqlite3.connect(store.path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeControlError) as exc_info:
            store.commit_failed_outcome(
                runtime_run_id=RUN_ID,
                envelope=envelope,
                terminal_reason_code="source_operation_failed",
                terminal_at=TERMINAL_AT,
                expected_state_revision=0,
            )
    finally:
        blocker.rollback()
        blocker.close()

    assert (
        exc_info.value.reason_code
        == "runtime_failed_outcome_storage_failed"
    )
    assert "locked" not in str(exc_info.value).lower()
    assert str(store.path) not in str(exc_info.value)
    assert _failure_truth(store.path) == (
        "queued",
        None,
        None,
        None,
        0,
        0,
        0,
    )

    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    assert committed.status == "failed"


@pytest.mark.parametrize(
    ("envelope", "reason"),
    (
        (_envelope(run_id="4" * 32, attempt_no=None), "runtime_failed_outcome_envelope_mismatch"),
        (_envelope(outcome="cancelled", attempt_no=None), "runtime_failed_outcome_envelope_mismatch"),
    ),
)
def test_cross_run_and_outcome_mismatch_fail_closed(
    tmp_path: Path,
    envelope,
    reason: str,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=envelope,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )
    assert exc_info.value.reason_code == reason
    assert _failure_truth(store.path) == ("queued", None, None, None, 0, 0, 0)


def test_changed_replay_and_terminal_rewrite_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = _envelope(attempt_no=None)
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=original,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    changed_payload = json.loads(
        original.model_dump_json()
    )
    changed_payload["support_action"] = None
    changed = parse_failure_envelope(
        json.dumps(changed_payload, separators=(",", ":"), sort_keys=True).encode()
    )
    with pytest.raises(RuntimeControlError) as conflict:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=changed,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )
    assert conflict.value.reason_code == "runtime_failed_outcome_replay_conflict"

    with pytest.raises(RuntimeControlError) as stale:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=original,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=1,
        )
    assert stale.value.reason_code == "runtime_failed_outcome_replay_conflict"


def test_canonical_terminal_run_rejects_legacy_status_and_event_mutators(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=None),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    before = _failure_truth(store.path)

    with pytest.raises(RuntimeControlError):
        store.update_run_status(
            runtime_run_id=RUN_ID,
            status="failed",
            current_stage="tampered",
            stop_reason_code="different_reason",
            completed_at="2026-07-27T01:03:00Z",
            updated_at="2026-07-27T01:03:00Z",
        )
    with pytest.raises(RuntimeControlError):
        store.append_event(
            RuntimeControlEventInput(
                event_id="event_after_canonical_terminal",
                runtime_run_id=RUN_ID,
                event_type="legacy_terminal_event",
                stage="tampered",
                status="failed",
                summary="must not mutate canonical terminal truth",
                created_at="2026-07-27T01:03:00Z",
            )
        )

    assert _failure_truth(store.path) == before
    run = store.get_run(RUN_ID)
    assert run.current_stage == "failed"
    assert run.stop_reason_code == "source_operation_failed"
    assert run.latest_event_seq == 0


@pytest.mark.parametrize(
    ("lookup", "value"),
    (
        ("get_run_by_approved_requirement_revision", "reqapproved_test"),
        ("get_run_by_run_intent_id", f"intent_{RUN_ID}"),
        ("get_run_by_start_idempotency_key", f"start_{RUN_ID}"),
    ),
)
def test_all_run_lookup_surfaces_reject_dangling_canonical_failure_ref(
    tmp_path: Path,
    lookup: str,
    value: str,
) -> None:
    store = _store(tmp_path)
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=None),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET current_failure_id = ?
            WHERE runtime_run_id = ?
            """,
            ("8" * 32, RUN_ID),
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        getattr(store, lookup)(value)
    assert exc_info.value.reason_code == "runtime_failed_outcome_integrity_failed"


def test_no_owner_cas_rejects_completed_authority_checkpoint_epoch(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    stale_revision = store.get_run(RUN_ID).state_revision
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="checkpoint_new",
            runtime_run_id=RUN_ID,
            stage="round",
            round_no=1,
            safe_boundary="after_round_controller",
            run_state={"round": 1},
            source_plan={"sourceIds": ["liepin"]},
            pending_commands=[],
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-07-27T00:00:02Z",
        ),
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    store.release_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
        released_at="2026-07-27T00:00:03Z",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(attempt_no=None),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=stale_revision,
        )
    assert exc_info.value.reason_code == "runtime_failed_outcome_revision_conflict"
    assert _failure_truth(store.path)[0:4] == ("queued", None, None, None)


def test_failed_commit_cannot_overwrite_needs_attention_truth(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = 'needs_attention',
                current_stage = 'needs_attention',
                product_outcome = 'needs_attention',
                current_failure_id = ?,
                current_failure_revision = 1,
                current_failure_authority_mode = 'no_owner'
            WHERE runtime_run_id = ?
            """,
            ("8" * 32, RUN_ID),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET current_failure_authority_mode = 'active_owner'
                WHERE runtime_run_id = ?
                """,
                (RUN_ID,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET current_failure_owner_lease_id = 'rtlease_fake'
                WHERE runtime_run_id = ?
                """,
                (RUN_ID,),
            )
        before_shape = conn.execute(
            """
            SELECT status, product_outcome, current_failure_id,
                   current_failure_revision,
                   current_failure_authority_mode,
                   current_failure_owner_lease_id, state_revision
            FROM runtime_control_runs
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        ).fetchone()
    before = _failure_truth(store.path)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(attempt_no=None),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )

    assert (
        exc_info.value.reason_code
        == "runtime_failed_outcome_terminal_immutable"
    )
    assert _failure_truth(store.path) == before
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            """
            SELECT status, product_outcome, current_failure_id,
                   current_failure_revision,
                   current_failure_authority_mode,
                   current_failure_owner_lease_id, state_revision
            FROM runtime_control_runs
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        ).fetchone() == before_shape


def test_failed_commit_rejects_poisoned_nonterminal_owner_provenance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET current_failure_owner_lease_id = 'rtlease_poisoned'
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        )
    before = _failure_truth(store.path)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(attempt_no=None),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )

    assert (
        exc_info.value.reason_code
        == "runtime_failed_outcome_terminal_immutable"
    )
    assert _failure_truth(store.path) == before
    with pytest.raises(RuntimeControlError) as read_exc_info:
        store.get_run(RUN_ID)
    assert (
        read_exc_info.value.reason_code
        == "runtime_failed_outcome_integrity_failed"
    )


def test_canonical_failed_readback_rejects_active_authority(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=None),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_executor_leases (
                lease_id, runtime_run_id, executor_id, attempt_no, status,
                acquired_at, heartbeat_at, lease_expires_at,
                released_at, reason_code
            )
            VALUES (?, ?, ?, ?, 'active', ?, NULL, ?, NULL, NULL)
            """,
            (
                "rtlease_resurrected",
                RUN_ID,
                "executor-a",
                1,
                "2026-07-27T00:00:01Z",
                LIVE_LEASE_EXPIRES_AT,
            ),
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.get_run(RUN_ID)
    assert exc_info.value.reason_code == "runtime_failed_outcome_integrity_failed"


def test_fractional_state_revision_fails_closed_without_truncation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET state_revision = 1.5
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        )
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET state_revision = 1.5
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.get_run(RUN_ID)
    assert exc_info.value.reason_code == "runtime_failed_outcome_integrity_failed"


@pytest.mark.parametrize(
    "tamper_sql",
    (
        """
        UPDATE runtime_control_executor_leases
        SET status = 'released'
        WHERE lease_id = ?
        """,
        "DELETE FROM runtime_control_executor_leases WHERE lease_id = ?",
    ),
)
def test_active_owner_replay_requires_exact_revoked_authority_record(
    tmp_path: Path,
    tamper_sql: str,
) -> None:
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    envelope = _envelope(attempt_no=lease.attempt_no)
    expected_revision = store.get_run(RUN_ID).state_revision
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(tamper_sql, (lease.lease_id,))

    with pytest.raises(RuntimeControlError) as read_exc_info:
        store.get_run(RUN_ID)
    assert (
        read_exc_info.value.reason_code
        == "runtime_failed_outcome_integrity_failed"
    )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=envelope,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=expected_revision,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
        )
    assert exc_info.value.reason_code == "runtime_failed_outcome_replay_conflict"


def test_retention_preserves_active_owner_replay_authority_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent_runtime_control import failed_outcome as outcome_module

    decision_at = "2026-07-27T01:00:00Z"
    monkeypatch.setattr(
        outcome_module,
        "_store_decision_time",
        lambda: decision_at,
    )
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    envelope = _envelope(attempt_no=lease.attempt_no)
    expected_revision = store.get_run(RUN_ID).state_revision
    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    cutoffs = {
        "terminal_run_older_than": "2027-01-01T00:00:00Z",
        "developer_event_older_than": "2027-01-01T00:00:00Z",
        "internal_event_older_than": "2027-01-01T00:00:00Z",
        "checkpoint_older_than": "2027-01-01T00:00:00Z",
        "lease_older_than": "2027-01-01T00:00:00Z",
        "command_older_than": "2027-01-01T00:00:00Z",
        "stage_output_older_than": "2027-01-01T00:00:00Z",
        "final_summary_older_than": "2027-01-01T00:00:00Z",
    }

    stats = store.collect_runtime_control_retention_stats(**cutoffs)
    dry_run = store.cleanup_runtime_control_retention(
        **cutoffs,
        batch_size=100,
        dry_run=True,
    )
    cleaned = store.cleanup_runtime_control_retention(
        **cutoffs,
        batch_size=100,
    )

    assert stats["executor_lease"] == 0
    assert dry_run["executor_lease"] == 0
    assert cleaned["executor_lease"] == 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            """
            SELECT status, released_at, reason_code
            FROM runtime_control_executor_leases
            WHERE lease_id = ?
            """,
            (lease.lease_id,),
        ).fetchone() == (
            "revoked",
            decision_at,
            "source_operation_failed",
        )
    replay = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    assert replay == committed


def test_stale_release_cannot_overwrite_a_concurrent_failed_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent_runtime_control import failed_outcome as outcome_module

    decision_at = "2026-07-27T01:00:00Z"
    monkeypatch.setattr(
        outcome_module,
        "_store_decision_time",
        lambda: decision_at,
    )
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    envelope = _envelope(attempt_no=lease.attempt_no)
    expected_revision = store.get_run(RUN_ID).state_revision
    release_paused = threading.Event()
    failed_committed = threading.Event()

    class CoordinatedConnection(sqlite3.Connection):
        paused = False

        def execute(self, sql, parameters=()):
            normalized = " ".join(sql.upper().split())
            should_pause = (
                threading.current_thread().name == "stale-release"
                and not self.paused
                and (
                    normalized == "BEGIN IMMEDIATE"
                    or normalized.startswith(
                        "UPDATE RUNTIME_CONTROL_EXECUTOR_LEASES"
                    )
                )
            )
            if should_pause:
                self.paused = True
                release_paused.set()
                assert failed_committed.wait(timeout=5)
            return super().execute(sql, parameters)

    @contextmanager
    def coordinated_connect():
        connection_factory = (
            CoordinatedConnection
            if threading.current_thread().name == "stale-release"
            else sqlite3.Connection
        )
        conn = sqlite3.connect(
            store.path,
            timeout=store.busy_timeout_ms / 1000,
            factory=connection_factory,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {store.busy_timeout_ms}")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setattr(store, "_connect", coordinated_connect)
    release_errors: list[RuntimeControlError] = []

    def stale_release() -> None:
        try:
            store.release_executor_lease(
                runtime_run_id=RUN_ID,
                executor_id=lease.executor_id,
                attempt_no=lease.attempt_no,
                released_at="2026-07-27T01:03:00Z",
                reason_code="normal_release",
            )
        except RuntimeControlError as exc:
            release_errors.append(exc)

    thread = threading.Thread(target=stale_release, name="stale-release")
    thread.start()
    assert release_paused.wait(timeout=5)
    try:
        committed = store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=envelope,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=expected_revision,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
        )
    finally:
        failed_committed.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert [error.reason_code for error in release_errors] == [
        "runtime_executor_stale"
    ]
    assert store.get_run(RUN_ID) == committed
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            """
            SELECT status, released_at, reason_code
            FROM runtime_control_executor_leases
            WHERE lease_id = ?
            """,
            (lease.lease_id,),
        ).fetchone() == (
            "revoked",
            decision_at,
            "source_operation_failed",
        )
    replay = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    assert replay == committed


def test_release_rolls_back_both_writes_and_normal_release_still_succeeds(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="starting",
        updated_at="2026-07-27T00:00:01Z",
    )
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T00:00:01Z",
        lease_expires_at=LIVE_LEASE_EXPIRES_AT,
    )
    revision_before = store.get_run(RUN_ID).state_revision
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_release_run_revision
            BEFORE UPDATE OF state_revision ON runtime_control_runs
            BEGIN
              SELECT RAISE(ABORT, 'injected_release_failure');
            END
            """
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.release_executor_lease(
            runtime_run_id=RUN_ID,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
            released_at="2026-07-27T01:03:00Z",
            reason_code="normal_release",
        )
    assert exc_info.value.reason_code == "runtime_executor_release_failed"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            """
            SELECT status, released_at, reason_code
            FROM runtime_control_executor_leases
            WHERE lease_id = ?
            """,
            (lease.lease_id,),
        ).fetchone() == ("active", None, None)
        assert conn.execute(
            """
            SELECT state_revision
            FROM runtime_control_runs
            WHERE runtime_run_id = ?
            """,
            (RUN_ID,),
        ).fetchone() == (revision_before,)
        conn.execute("DROP TRIGGER fail_release_run_revision")

    released = store.release_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
        released_at="2026-07-27T01:03:00Z",
        reason_code="normal_release",
    )
    assert released.status == "released"
    assert released.reason_code == "normal_release"
    assert store.get_run(RUN_ID).state_revision == revision_before + 1


@pytest.mark.parametrize("status", ("completed", "cancelled"))
def test_other_terminal_states_are_immutable(
    tmp_path: Path,
    status: str,
) -> None:
    store = _store(tmp_path, status=status)
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(attempt_no=None),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=0,
        )
    assert exc_info.value.reason_code == "runtime_failed_outcome_terminal_immutable"
    assert _failure_truth(store.path) == (status, None, None, None, 0, 0, 0)


@pytest.mark.parametrize(
    "corruption",
    ("dangling_ref", "hash", "projection", "terminal_reason"),
)
def test_restart_readback_rejects_dangling_or_corrupt_failure_truth(
    tmp_path: Path,
    corruption: str,
) -> None:
    store = _store(tmp_path)
    envelope = _envelope(attempt_no=None)
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=0,
    )
    with sqlite3.connect(store.path) as conn:
        if corruption == "dangling_ref":
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET current_failure_id = ?
                WHERE runtime_run_id = ?
                """,
                ("8" * 32, RUN_ID),
            )
        elif corruption == "terminal_reason":
            conn.execute(
                """
                UPDATE runtime_control_runs
                SET stop_reason_code = 'different_reason'
                WHERE runtime_run_id = ?
                """,
                (RUN_ID,),
            )
        else:
            conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
            field = "canonical_sha256" if corruption == "hash" else "run_id"
            value = "0" * 64 if corruption == "hash" else "4" * 32
            conn.execute(
                f"""
                UPDATE runtime_control_failure_envelope_revisions
                SET {field} = ?
                WHERE failure_id = ? AND revision = 1
                """,
                (value, envelope.failure_id),
            )

    restarted = RuntimeControlStore(store.path)
    restarted.initialize()
    with pytest.raises(RuntimeControlError) as exc_info:
        restarted.get_run(RUN_ID)
    assert exc_info.value.reason_code == "runtime_failed_outcome_integrity_failed"
    assert "sqlite" not in str(exc_info.value).lower()
    assert str(store.path) not in str(exc_info.value)


@pytest.mark.parametrize("active_owner", (False, True))
def test_missing_authority_table_is_bounded_on_readback_and_replay(
    tmp_path: Path,
    active_owner: bool,
) -> None:
    store = _store(tmp_path)
    executor_id = None
    attempt_no = None
    expected_revision = 0
    if active_owner:
        store.update_run_status(
            runtime_run_id=RUN_ID,
            status="starting",
            updated_at="2026-07-27T00:00:01Z",
        )
        lease = store.acquire_executor_lease(
            runtime_run_id=RUN_ID,
            executor_id="executor-a",
            acquired_at="2026-07-27T00:00:01Z",
            lease_expires_at=LIVE_LEASE_EXPIRES_AT,
        )
        executor_id = lease.executor_id
        attempt_no = lease.attempt_no
        expected_revision = store.get_run(RUN_ID).state_revision
    envelope = _envelope(attempt_no=attempt_no)
    store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=envelope,
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=expected_revision,
        executor_id=executor_id,
        attempt_no=attempt_no,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TABLE runtime_control_executor_leases")

    with pytest.raises(RuntimeControlError) as read_exc_info:
        store.get_run(RUN_ID)
    assert (
        read_exc_info.value.reason_code
        == "runtime_failed_outcome_integrity_failed"
    )
    with pytest.raises(RuntimeControlError) as replay_exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=envelope,
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=expected_revision,
            executor_id=executor_id,
            attempt_no=attempt_no,
        )
    assert (
        replay_exc_info.value.reason_code
        == "runtime_failed_outcome_storage_failed"
    )
    for error in (read_exc_info.value, replay_exc_info.value):
        rendered = str(error).lower()
        assert "sqlite" not in rendered
        assert "executor_leases" not in rendered
        assert "no such table" not in rendered
        assert str(store.path).lower() not in rendered


@pytest.mark.parametrize(
    ("crash_point", "expected"),
    (
        (6, ("queued", None, None, None, 0, 0, 0)),
        (7, ("failed", "failed", "7" * 32, 1, 1, 1, 0)),
    ),
)
def test_subprocess_crash_boundary_exposes_old_or_complete_new_truth(
    tmp_path: Path,
    crash_point: int,
    expected: tuple[object, ...],
) -> None:
    store = _store(tmp_path)
    payload = _failure()
    payload.update(
        {
            "run_id": RUN_ID,
            "current_outcome": "failed",
            "operation_id": None,
            "attempt_no": None,
            "occurred_at": TERMINAL_AT,
            "observed_at": TERMINAL_AT,
        }
    )
    script = textwrap.dedent(
        """
        import json
        import os
        import sys
        from seektalent.diagnostics_schema import parse_failure_envelope
        from seektalent_runtime_control.store import RuntimeControlStore

        path, point, payload = sys.argv[1], int(sys.argv[2]), sys.argv[3]
        envelope = parse_failure_envelope(payload.encode())
        def crash(index, _phase):
            if index == point:
                os._exit(71)
        RuntimeControlStore(path).commit_failed_outcome(
            runtime_run_id=envelope.run_id,
            envelope=envelope,
            terminal_reason_code=envelope.reason_code,
            terminal_at=envelope.occurred_at,
            expected_state_revision=0,
            statement_hook=crash,
        )
        """
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            os.fspath(store.path),
            str(crash_point),
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 71
    assert result.stdout == ""
    assert result.stderr == ""
    assert _failure_truth(store.path) == expected
