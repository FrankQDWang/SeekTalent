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


def test_current_readiness_releases_only_expired_prepare_lane_from_failed_run(
    tmp_path: Path,
) -> None:
    store = _failed_unknown_store(
        tmp_path,
        source_operation_kind="verify_session",
        lane_operation_kind="prepare_readiness",
    )
    probes: list[str] = []

    outcome = BrowserLaneReconciliationCoordinator(
        store=store,
        prepare_readiness_probe=lambda: probes.append("ready"),
    ).run_once()

    lane = store.get_browser_lane()
    operation = store.get_source_operation(
        "runtime-run-failed",
        "operation-failed",
    )
    assert outcome == "released"
    assert probes == ["ready"]
    assert lane is not None
    assert lane.status == "failed"
    assert operation.source_operation_disposition == "partial"
    assert operation.retry_posture == "no_retry"
    assert store.get_run("runtime-run-failed").status == "failed"


def test_current_readiness_releases_prepare_lane_after_accepted_no_dispatch(
    tmp_path: Path,
) -> None:
    store = _failed_unknown_store(
        tmp_path,
        source_operation_kind="verify_session",
        lane_operation_kind="prepare_readiness",
    )
    journal = create_command_journal(
        store.path.parent
        / "source-port"
        / "liepin-cards-journal.sqlite3"
    )
    session = journal.start()
    session.record_accepted(
        AcceptedCommand(
            run_id="runtime-run-failed",
            operation_id="operation-failed",
            source="liepin",
            operation_kind="verify_session",
            idempotency_key="operation-failed",
            request_hash="a" * 64,
            attempt_no=1,
            accepted_requirement_revision_id="approved-failed",
            runtime_attempt_fence_ref="b" * 64,
            authorized_dispatch_intent_id="intent-failed",
            authorized_dispatch_intent_revision=1,
            authorized_dispatch_intent_digest="c" * 64,
            profile_binding_generation=1,
            browser_control_scope_id="browser-scope-failed",
            controller_fence_ref=None,
        )
    )
    session.close()
    journal.close()
    probes: list[str] = []

    outcome = BrowserLaneReconciliationCoordinator(
        store=store,
        prepare_readiness_probe=lambda: probes.append("ready"),
    ).run_once()

    lane = store.get_browser_lane()
    operation = store.get_source_operation(
        "runtime-run-failed",
        "operation-failed",
    )
    assert outcome == "released"
    assert probes == ["ready"]
    assert lane is not None
    assert lane.status == "failed"
    assert operation.dispatch_intent_ref == (
        "source-dispatch://operation-failed/1"
    )
    assert operation.source_operation_disposition == "partial"
    assert operation.retry_posture == "no_retry"


def test_current_readiness_does_not_release_cards_unknown(
    tmp_path: Path,
) -> None:
    store = _failed_unknown_store(
        tmp_path,
        source_operation_kind="cards",
        lane_operation_kind="cards",
    )
    probes: list[str] = []

    outcome = BrowserLaneReconciliationCoordinator(
        store=store,
        prepare_readiness_probe=lambda: probes.append("ready"),
    ).run_once()

    lane = store.get_browser_lane()
    operation = store.get_source_operation(
        "runtime-run-failed",
        "operation-failed",
    )
    assert outcome == "needs_attention"
    assert probes == []
    assert lane is not None
    assert lane.status == "active"
    assert operation.source_operation_disposition == (
        "reconciliation_unknown"
    )
    assert operation.retry_posture == "reconcile_first"


def test_expired_detail_unknown_releases_after_owned_tab_is_conclusively_absent(
    tmp_path: Path,
) -> None:
    store = _failed_unknown_store(
        tmp_path,
        source_operation_kind="details",
        lane_operation_kind="details",
    )
    journal = create_command_journal(
        store.path.parent
        / "source-port"
        / "liepin-cards-journal.sqlite3"
    )
    session = journal.start()
    accepted = session.record_accepted(
        AcceptedCommand(
            run_id="runtime-run-failed",
            operation_id="operation-failed",
            source="liepin",
            operation_kind="details",
            idempotency_key="operation-failed",
            request_hash="a" * 64,
            attempt_no=1,
            accepted_requirement_revision_id="approved-failed",
            runtime_attempt_fence_ref="b" * 64,
            authorized_dispatch_intent_id="intent-failed",
            authorized_dispatch_intent_revision=1,
            authorized_dispatch_intent_digest="c" * 64,
            profile_binding_generation=1,
            browser_control_scope_id="browser-scope-failed",
            controller_fence_ref=None,
        )
    )
    session.record_dispatch_intent(
        run_id="runtime-run-failed",
        operation_id="operation-failed",
        expected_head_journal_revision=accepted.revision,
        durable_dispatch_intent_ref="source-dispatch://operation-failed/1",
    )
    session.close()
    journal.close()
    probes: list[str] = []

    outcome = BrowserLaneReconciliationCoordinator(
        store=store,
        orphaned_owned_tab_absent=lambda operation_kind: (
            probes.append(operation_kind) or True
        ),
    ).run_once()

    lane = store.get_browser_lane()
    operation = store.get_source_operation(
        "runtime-run-failed",
        "operation-failed",
    )
    assert outcome == "released"
    assert probes == ["details"]
    assert lane is not None
    assert lane.status == "failed"
    assert operation.source_operation_disposition == "failed"
    assert operation.retry_posture == "no_retry"
    assert operation.conclusive_observation_ref is not None


def test_failed_current_readiness_probe_keeps_prepare_lane_fenced(
    tmp_path: Path,
) -> None:
    store = _failed_unknown_store(
        tmp_path,
        source_operation_kind="verify_session",
        lane_operation_kind="prepare_readiness",
    )

    def fail_probe() -> None:
        raise RuntimeError("not ready")

    outcome = BrowserLaneReconciliationCoordinator(
        store=store,
        prepare_readiness_probe=fail_probe,
    ).run_once()

    lane = store.get_browser_lane()
    operation = store.get_source_operation(
        "runtime-run-failed",
        "operation-failed",
    )
    assert outcome == "needs_attention"
    assert lane is not None
    assert lane.status == "active"
    assert operation.source_operation_disposition == (
        "reconciliation_unknown"
    )
    assert operation.retry_posture == "reconcile_first"


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


def _failed_unknown_store(
    tmp_path: Path,
    *,
    source_operation_kind: str,
    lane_operation_kind: str,
) -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime-control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime-run-failed",
            run_intent_id="intent-failed",
            start_idempotency_key="start-failed",
            run_kind="primary",
            approved_requirement_revision_id="approved-failed",
            status="running",
            current_stage="runtime",
            source_ids=["liepin"],
            created_at="2026-07-30T00:00:00Z",
            updated_at="2026-07-30T00:00:00Z",
        )
    )
    executor_lease = store.acquire_executor_lease(
        runtime_run_id="runtime-run-failed",
        executor_id="executor-failed",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:00:10Z",
    )
    accepted = store.accept_source_operation(
        runtime_run_id="runtime-run-failed",
        operation_id="operation-failed",
        source_id="liepin",
        operation_kind=source_operation_kind,
        canonical_request_hash="a" * 64,
        idempotency_key="operation-failed",
        accepted_requirement_revision_id="approved-failed",
        runtime_attempt_no=executor_lease.attempt_no,
        runtime_attempt_authority_ref="authority-failed",
        runtime_attempt_fence_ref="b" * 64,
        profile_binding_generation=1,
        browser_control_scope_id="browser-scope-failed",
        controller_fence_ref=None,
        outbox_id="outbox-failed",
        dispatch_intent_id="intent-failed",
        dispatch_intent_revision=1,
        dispatch_intent_digest="c" * 64,
        dispatch_authorization_ordinal=1,
        source_operation_acceptance_ref=(
            "source-acceptance://operation-failed"
        ),
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
    )
    store.record_source_dispatch_ack(
        runtime_run_id="runtime-run-failed",
        operation_id="operation-failed",
        outbox_id=accepted.dispatch.outbox_id,
        canonical_request_hash="a" * 64,
        dispatch_intent_id="intent-failed",
        dispatch_intent_revision=1,
        dispatch_intent_digest="c" * 64,
        dispatch_authorization_ordinal=1,
        expected_outbox_revision=1,
        accepted_sidecar_generation=1,
        accepted_sidecar_journal_revision=1,
        ack_ref="sha256:" + "d" * 64,
        ack_kind="new_logical_operation",
        acknowledged_at="2026-07-30T00:00:01Z",
    )
    lane = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-failed",
        owner_process_id=123,
        process_boot_id="process-failed",
        runtime_run_id="runtime-run-failed",
        operation_id="operation-failed",
        operation_kind=lane_operation_kind,
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:00:03Z",
    )
    assert lane is not None
    store.record_owned_source_reconciliation_unknown(
        runtime_run_id="runtime-run-failed",
        operation_id="operation-failed",
        executor_id="executor-failed",
        attempt_no=executor_lease.attempt_no,
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
        history_result_ref="sha256:" + "e" * 64,
        history_result_digest="e" * 64,
        history_outcome="history_unavailable",
        history_conclusion=None,
        dispatch_intent_ref="source-dispatch://operation-failed/1",
        committed_at="2026-07-30T00:00:02Z",
    )
    store.mark_browser_lane_unresolved(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-failed",
        fencing_token=lane.fencing_token,
        failure_code="liepin_browser_lane_reconciliation_required",
        observed_at="2026-07-30T00:00:02Z",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE runtime_control_runs
            SET status = 'failed', current_stage = 'runtime',
                stop_reason_code = 'runtime_run_failed',
                completed_at = '2026-07-30T00:00:04Z',
                updated_at = '2026-07-30T00:00:04Z'
            WHERE runtime_run_id = 'runtime-run-failed'
            """
        )
        connection.execute(
            """
            UPDATE runtime_control_executor_leases
            SET status = 'failed',
                lease_expires_at = '2026-07-30T00:00:03Z',
                released_at = '2026-07-30T00:00:04Z',
                reason_code = 'runtime_run_failed'
            WHERE runtime_run_id = 'runtime-run-failed'
            """
        )
    return store
