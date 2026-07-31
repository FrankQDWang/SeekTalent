from __future__ import annotations

from pathlib import Path
import sqlite3

from seektalent.browser_lane_reconciliation import (
    BrowserLaneReconciliationCoordinator,
)
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    create_command_journal,
)
from seektalent_runtime_control.browser_lane import (
    LIEPIN_BROWSER_LANE,
)
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.recovery import RuntimeRecoveryService
from seektalent_runtime_control.store import RuntimeControlStore


def test_conclusive_no_effect_releases_expired_lane(
    tmp_path: Path,
) -> None:
    store, session = _store_and_journal(tmp_path)
    session.close()

    outcome = BrowserLaneReconciliationCoordinator(
        store=store,
    ).run_once()

    lane = store.get_browser_lane()
    operation = store.get_source_operation(
        "runtime-run-orphan",
        "operation-orphan",
    )
    assert outcome == "released"
    assert lane is not None
    assert lane.status == "failed"
    assert operation.retry_posture == "safe_retry"
    assert operation.source_operation_disposition is None


def test_dispatch_without_terminal_stays_fenced_without_revision_churn(
    tmp_path: Path,
) -> None:
    store, session = _store_and_journal(tmp_path)
    session.record_dispatch_intent(
        run_id="runtime-run-orphan",
        operation_id="operation-orphan",
        expected_head_journal_revision=1,
        durable_dispatch_intent_ref="dispatch://operation-orphan",
    )
    session.close()
    coordinator = BrowserLaneReconciliationCoordinator(store=store)

    assert coordinator.run_once() == "needs_attention"
    assert coordinator.run_once() == "needs_attention"

    lane = store.get_browser_lane()
    operation = store.get_source_operation(
        "runtime-run-orphan",
        "operation-orphan",
    )
    assert lane is not None
    assert lane.status == "active"
    assert operation.source_operation_disposition == (
        "reconciliation_unknown"
    )
    assert operation.retry_posture == "reconcile_first"
    with sqlite3.connect(store.path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM runtime_control_source_reconciliations
            WHERE runtime_run_id = 'runtime-run-orphan'
              AND operation_id = 'operation-orphan'
            """
        ).fetchone()[0]
    assert count == 1


def _store_and_journal(tmp_path: Path):
    store = RuntimeControlStore(
        tmp_path / "runtime-control.sqlite3",
    )
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime-run-orphan",
            run_intent_id="intent-orphan",
            start_idempotency_key="start-orphan",
            run_kind="primary",
            approved_requirement_revision_id="approved-orphan",
            status="running",
            current_stage="runtime",
            source_ids=["liepin"],
            created_at="2026-07-30T00:00:00Z",
            updated_at="2026-07-30T00:00:00Z",
        )
    )
    executor_lease = store.acquire_executor_lease(
        runtime_run_id="runtime-run-orphan",
        executor_id="executor-orphan",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:00:01Z",
    )
    store.accept_source_operation(
        runtime_run_id="runtime-run-orphan",
        operation_id="operation-orphan",
        source_id="liepin",
        operation_kind="cards",
        canonical_request_hash="a" * 64,
        idempotency_key="cards-orphan",
        accepted_requirement_revision_id="approved-orphan",
        runtime_attempt_no=executor_lease.attempt_no,
        runtime_attempt_authority_ref="authority-orphan",
        runtime_attempt_fence_ref="b" * 64,
        profile_binding_generation=1,
        browser_control_scope_id=None,
        controller_fence_ref=None,
        outbox_id="outbox-orphan",
        dispatch_intent_id="intent-orphan",
        dispatch_intent_revision=1,
        dispatch_intent_digest="c" * 64,
        dispatch_authorization_ordinal=1,
        source_operation_acceptance_ref=(
            "source-acceptance://operation-orphan"
        ),
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
    )
    lease = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-orphan",
        owner_process_id=123,
        process_boot_id="process-orphan",
        runtime_run_id="runtime-run-orphan",
        operation_id="operation-orphan",
        operation_kind="cards",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:00:01Z",
    )
    assert lease is not None
    journal = create_command_journal(
        store.path.parent
        / "source-port"
        / "liepin-cards-journal.sqlite3"
    )
    session = journal.start()
    receipt = session.record_accepted(
        AcceptedCommand(
            run_id="runtime-run-orphan",
            operation_id="operation-orphan",
            source="liepin",
            operation_kind="cards",
            idempotency_key="cards-orphan",
            request_hash="a" * 64,
            attempt_no=1,
            accepted_requirement_revision_id="approved-orphan",
            runtime_attempt_fence_ref="b" * 64,
            authorized_dispatch_intent_id="intent-orphan",
            authorized_dispatch_intent_revision=1,
            authorized_dispatch_intent_digest="c" * 64,
            profile_binding_generation=1,
            browser_control_scope_id=None,
            controller_fence_ref=None,
        )
    )
    assert receipt.revision == 1
    journal.close()
    RuntimeRecoveryService(store=store).recover_start_timeouts(
        resume_recoverable=True,
    )
    assert store.get_run("runtime-run-orphan").status == (
        "needs_attention"
    )
    return store, session
