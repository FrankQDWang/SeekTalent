from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from seektalent.diagnostics_schema import parse_failure_envelope
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.store import RuntimeControlStore
from tests.test_diagnostics_schema import _failure


RUN_ID = "3" * 32
TERMINAL_AT = "2026-07-27T01:02:03Z"


def _envelope(
    *,
    run_id: str = RUN_ID,
    failure_id: str = "7" * 32,
    revision: int = 1,
    outcome: str = "failed",
    attempt_no: int | None = 1,
):
    payload = _failure()
    payload.update(
        {
            "run_id": run_id,
            "failure_id": failure_id,
            "revision": revision,
            "current_outcome": outcome,
            "attempt_no": attempt_no,
            "occurred_at": TERMINAL_AT,
            "observed_at": TERMINAL_AT,
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
        lease_expires_at="2026-07-27T02:00:00Z",
    )

    committed = store.commit_failed_outcome(
        runtime_run_id=RUN_ID,
        envelope=_envelope(attempt_no=lease.attempt_no),
        terminal_reason_code="source_operation_failed",
        terminal_at=TERMINAL_AT,
        expected_state_revision=1,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )

    assert committed.status == "failed"
    assert _failure_truth(store.path)[-1] == 0


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
        lease_expires_at="2026-07-27T02:00:00Z",
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=kwargs.get("expected_state_revision", 1),
            executor_id=kwargs.get("executor_id"),
            attempt_no=kwargs.get("attempt_no"),
        )

    assert exc_info.value.reason_code == reason
    assert _failure_truth(store.path) == ("starting", None, None, None, 1, 0, 1)


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
        lease_expires_at="2026-07-27T02:00:00Z",
    )
    with pytest.raises(RuntimeControlError, match="runtime_failed_outcome_authority_rejected"):
        store.commit_failed_outcome(
            runtime_run_id=RUN_ID,
            envelope=_envelope(),
            terminal_reason_code="source_operation_failed",
            terminal_at=TERMINAL_AT,
            expected_state_revision=1,
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
