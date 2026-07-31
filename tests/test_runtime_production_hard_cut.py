from __future__ import annotations

import inspect
import time
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    create_command_journal,
)
from seektalent_conversation_agent.factory import build_agent_service
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.executor import _event
from seektalent_runtime_control.browser_lane import LIEPIN_BROWSER_LANE
from seektalent_runtime_control.checkpoint_v2 import (
    checkpoint_projection,
)
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.recovery import RuntimeRecoveryService
from seektalent_ui.server import create_app
from seektalent_ui.workbench_paths import workbench_db_path
from seektalent.sources.liepin.runtime_lane import (
    run_liepin_first_page_expansion,
    run_liepin_source_lane,
)
from seektalent.runtime.source_lanes import RuntimeSourceLaneRequest
from seektalent_workbench_v2.runtime_service import WorkbenchV2RequirementExtractor
from seektalent.source_contracts.detail_open_claims import (
    DetailOpenClaimLedger,
)
from seektalent.source_contracts.first_page_expansion import (
    SourceFirstPageExpansionRequest,
)
from tests.settings_factory import make_settings
from tests.conversation_agent_test_support import sample_requirement_sheet
from tests.test_runtime_control_checkpoint_v2 import (
    _state_with_round_and_finalization,
)


class _NoopRuntime:
    def __init__(
        self,
        _settings: AppSettings,
        *,
        source_operation_executor: object | None = None,
    ) -> None:
        self.source_operation_executor = source_operation_executor

    async def run_async(self, **_kwargs: object) -> object:
        return object()

    def extract_requirements(self, **_kwargs: object) -> object:
        raise AssertionError("request must not reach the legacy runtime")


class _ProductionTopologyRuntime:
    workflow_calls = 0

    def __init__(
        self,
        _settings: AppSettings,
        *,
        source_operation_executor: object | None = None,
    ) -> None:
        self.source_operation_executor = source_operation_executor

    def extract_requirements(self, **_kwargs: object) -> object:
        return sample_requirement_sheet(job_title="Python 平台负责人")

    async def run_async(self, **_kwargs: object) -> object:
        type(self).workflow_calls += 1
        raise AssertionError(
            "after_finalization_commit must not rebuild or replay runtime"
        )


@pytest.fixture(autouse=True)
def _isolated_production_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SEEKTALENT_INSTALL_HOME",
        str(tmp_path / "installed-home"),
    )


def test_prod_composition_uses_one_runtime_execution_authority(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    adapter = (
        app.state.agent_conversation_service.service_action_adapter
    )
    runtime_service = app.state.workbench_v2_service.runtime_service

    assert isinstance(adapter.workflow_executor, WorkflowRuntimeExecutor)
    assert app.state.workbench_v2_runtime_executor is adapter.workflow_executor
    assert app.state.workbench_v2_runtime_runner.executor is adapter.workflow_executor
    assert runtime_service._runtime_executor is adapter.workflow_executor
    assert app.state.runtime_control_store is adapter.runtime_store
    assert adapter.workflow_executor.store is adapter.runtime_store
    assert app.state.runtime_command_service is adapter.command_service
    assert runtime_service.command_service is adapter.command_service
    assert adapter.command_service.store is adapter.runtime_store
    assert app.state.workbench_job_runner is None


def test_prod_workbench_v2_injects_standalone_requirement_extractor(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="opencli",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )

    app = create_app(settings=settings, runtime_factory=_NoopRuntime)

    requirement_extractor = app.state.workbench_v2_requirement_extractor
    assert isinstance(requirement_extractor, WorkbenchV2RequirementExtractor)
    assert app.state.workbench_v2_service.runtime_service.requirement_extractor is requirement_extractor


def test_prod_legacy_write_returns_410_without_database_mutation(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    before = _table_counts(workbench_db_path(settings))

    response = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ).post(
        "/api/workbench/sessions",
        json={
            "jobTitle": "Backend Engineer",
            "jdText": "Python",
            "notes": "",
        },
    )

    assert response.status_code == 410
    assert response.json()["reasonCode"] == (
        "legacy_workbench_execution_removed"
    )
    assert _table_counts(workbench_db_path(settings)) == before


def test_execution_plane_readiness_reports_all_production_runners(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)

    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.get("/api/health/execution-ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert {
        component["name"] for component in payload["components"]
    } == {
        "runtime_runner",
        "workflow_start_requested",
        "requirement_extraction_requested",
    }
    assert all(component["alive"] for component in payload["components"])
    assert payload["browserLane"] is None


def test_execution_plane_readiness_rejects_alive_but_stale_components(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    stale = SimpleNamespace(
        name="alive_but_stuck",
        alive=True,
        last_heartbeat_at="2026-07-30T00:00:00Z",
        last_success_at=None,
        first_failure_at=None,
        first_failure_type=None,
        failure_count=0,
        restart_count=1,
        as_dict=lambda: {
            "name": "alive_but_stuck",
            "alive": True,
            "last_heartbeat_at": "2026-07-30T00:00:00Z",
            "last_success_at": None,
            "first_failure_at": None,
            "first_failure_type": None,
            "failure_count": 0,
            "restart_count": 1,
        },
    )
    for runner in (
        app.state.workbench_v2_runtime_runner,
        app.state.workflow_start_outbox_runner,
        app.state.requirement_extraction_outbox_runner,
    ):
        runner.health_snapshot = lambda: stale

    response = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ).get("/api/health/execution-ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert all(
        component["alive"] and component["stale"]
        for component in response.json()["components"]
    )


def test_execution_plane_readiness_reports_expired_browser_lane(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    lease = app.state.runtime_control_store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="readiness-expired-owner",
        owner_process_id=1,
        process_boot_id="readiness-expired-process",
        runtime_run_id=None,
        operation_id="readiness-expired-operation",
        operation_kind="prepare_readiness",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:00:01Z",
    )
    assert lease is not None

    response = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ).get("/api/health/execution-ready")

    assert response.status_code == 503
    assert response.json()["browserLane"] == {
        "laneKey": "liepin_browser",
        "status": "active",
        "operationKind": "prepare_readiness",
        "fencingToken": lease.fencing_token,
        "lastFailureCode": None,
        "expired": True,
    }


def test_execution_plane_readiness_rejects_unresolved_browser_effect(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    store = app.state.runtime_control_store
    lease = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="readiness-unresolved-owner",
        owner_process_id=1,
        process_boot_id="readiness-unresolved-process",
        runtime_run_id=None,
        operation_id="readiness-unresolved-operation",
        operation_kind="prepare_readiness",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2099-07-30T00:00:01Z",
    )
    assert lease is not None
    store.mark_browser_lane_unresolved(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
        failure_code="liepin_prepare_reconciliation_unknown",
        observed_at="2026-07-30T00:00:01Z",
    )

    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.get("/api/health/execution-ready")

    assert response.status_code == 503
    assert response.json()["browserLane"]["lastFailureCode"] == (
        "liepin_prepare_reconciliation_unknown"
    )


def test_prod_runner_reconciles_expired_lane_from_conclusive_evidence(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_control_path=str(
            tmp_path / "runtime-control.sqlite3"
        ),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    store = app.state.runtime_control_store
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="rtrun-orphan-reconcile",
            run_intent_id="intent-orphan-reconcile",
            start_idempotency_key="start-orphan-reconcile",
            run_kind="primary",
            approved_requirement_revision_id="approved-orphan",
            status="completed",
            current_stage="finalized",
            source_ids=["liepin"],
            created_at="2026-07-30T00:00:00Z",
            updated_at="2026-07-30T00:00:00Z",
            completed_at="2026-07-30T00:00:00Z",
        )
    )
    lease = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="orphan-owner",
        owner_process_id=123,
        process_boot_id="orphan-process",
        runtime_run_id="rtrun-orphan-reconcile",
        operation_id="operation-orphan-reconcile",
        operation_kind="cards",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:00:01Z",
    )
    assert lease is not None
    with store._connect() as connection:
        connection.execute(
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
            VALUES (?, ?, 'liepin', 'cards', ?, ?, ?, 1, ?,
                    'reconciled', ?, ?, 'completed', 'no_retry',
                    1, NULL, 2)
            """,
            (
                "rtrun-orphan-reconcile",
                "operation-orphan-reconcile",
                "a" * 64,
                "idempotency-orphan",
                "approved-orphan",
                "authority-orphan",
                "dispatch://operation-orphan-reconcile",
                "artifact://operation-orphan-reconcile/" + "c" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_control_source_reconciliations (
              reconciliation_id, runtime_run_id, operation_id, source_id,
              operation_kind, canonical_request_hash, idempotency_key,
              accepted_requirement_revision_id, runtime_attempt_no,
              runtime_attempt_authority_ref, history_result_ref,
              history_result_digest, history_outcome, history_conclusion,
              decision_kind, dispatch_intent_ref,
              conclusive_observation_ref, source_operation_disposition,
              retry_posture, expected_ledger_revision,
              expected_reconciliation_revision, committed_at,
              committed_operation_phase, committed_ledger_revision,
              committed_reconciliation_revision
            )
            VALUES (
              'reconciliation-orphan', 'rtrun-orphan-reconcile',
              'operation-orphan-reconcile', 'liepin', 'cards', ?, ?,
              'approved-orphan', 1, 'authority-orphan',
              ?, ?, 'matched', 'observed_result',
              'conclusive_observation',
              'dispatch://operation-orphan-reconcile',
              ?, 'completed', 'no_retry',
              1, 0, '2026-07-30T00:00:02Z',
              'reconciled', 2, 1
            )
            """,
            (
                "a" * 64,
                "idempotency-orphan",
                "sha256:" + "b" * 64,
                "b" * 64,
                "artifact://operation-orphan-reconcile/" + "c" * 64,
            ),
        )

    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            lane = store.get_browser_lane()
            if lane is not None and lane.status != "active":
                break
            time.sleep(0.01)

    lane = store.get_browser_lane()
    assert lane is not None
    assert lane.status == "failed"
    assert lane.last_failure_code == "liepin_browser_lane_reconciled"


def test_prod_runner_reads_sidecar_history_to_reconcile_crashed_owner(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_control_path=str(
            tmp_path / "runtime-control.sqlite3"
        ),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    store = app.state.runtime_control_store
    run_id = "rtrun-orphan-sidecar-history"
    operation_id = "operation-orphan-sidecar-history"
    request_hash = "a" * 64
    dispatch_digest = "b" * 64
    runtime_fence = "c" * 64
    result_hash = "d" * 64
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=run_id,
            run_intent_id="intent-orphan-sidecar-history",
            start_idempotency_key="start-orphan-sidecar-history",
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
        runtime_run_id=run_id,
        executor_id="orphan-sidecar-executor",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:10:00Z",
    )
    accepted = store.accept_source_operation(
        runtime_run_id=run_id,
        operation_id=operation_id,
        source_id="liepin",
        operation_kind="cards",
        canonical_request_hash=request_hash,
        idempotency_key="cards-orphan-sidecar-history",
        accepted_requirement_revision_id="approved-orphan",
        runtime_attempt_no=executor_lease.attempt_no,
        runtime_attempt_authority_ref="authority-orphan-sidecar",
        runtime_attempt_fence_ref=runtime_fence,
        profile_binding_generation=1,
        browser_control_scope_id=None,
        controller_fence_ref=None,
        outbox_id="outbox-orphan-sidecar-history",
        dispatch_intent_id="intent-orphan-sidecar-history",
        dispatch_intent_revision=1,
        dispatch_intent_digest=dispatch_digest,
        dispatch_authorization_ordinal=1,
        source_operation_acceptance_ref=(
            "source-acceptance://orphan-sidecar-history"
        ),
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
    )
    store.record_source_dispatch_ack(
        runtime_run_id=run_id,
        operation_id=operation_id,
        outbox_id=accepted.dispatch.outbox_id,
        canonical_request_hash=request_hash,
        dispatch_intent_id=accepted.dispatch.dispatch_intent_id,
        dispatch_intent_revision=1,
        dispatch_intent_digest=dispatch_digest,
        dispatch_authorization_ordinal=1,
        expected_outbox_revision=1,
        accepted_sidecar_generation=1,
        accepted_sidecar_journal_revision=1,
        ack_ref="sha256:" + "e" * 64,
        ack_kind="new_logical_operation",
        acknowledged_at="2026-07-30T00:00:01Z",
    )
    lane = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="orphan-sidecar-owner",
        owner_process_id=123,
        process_boot_id="orphan-sidecar-process",
        runtime_run_id=run_id,
        operation_id=operation_id,
        operation_kind="cards",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2026-07-30T00:10:00Z",
    )
    assert lane is not None

    journal = create_command_journal(
        store.path.parent
        / "source-port"
        / "liepin-cards-journal.sqlite3"
    )
    session = journal.start()
    accepted_receipt = session.record_accepted(
        AcceptedCommand(
            run_id=run_id,
            operation_id=operation_id,
            source="liepin",
            operation_kind="cards",
            idempotency_key="cards-orphan-sidecar-history",
            request_hash=request_hash,
            attempt_no=1,
            accepted_requirement_revision_id="approved-orphan",
            runtime_attempt_fence_ref=runtime_fence,
            authorized_dispatch_intent_id=(
                "intent-orphan-sidecar-history"
            ),
            authorized_dispatch_intent_revision=1,
            authorized_dispatch_intent_digest=dispatch_digest,
            profile_binding_generation=1,
            browser_control_scope_id=None,
            controller_fence_ref=None,
        )
    )
    dispatch_receipt = session.record_dispatch_intent(
        run_id=run_id,
        operation_id=operation_id,
        expected_head_journal_revision=accepted_receipt.revision,
        durable_dispatch_intent_ref=(
            "dispatch://orphan-sidecar-history"
        ),
    )
    session.record_observed_result(
        run_id=run_id,
        operation_id=operation_id,
        expected_head_journal_revision=dispatch_receipt.revision,
        result_ref="artifact://orphan-sidecar-history",
        result_hash=result_hash,
    )
    session.close()
    journal.close()

    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            lane_snapshot = store.get_browser_lane()
            operation = store.get_source_operation(
                run_id,
                operation_id,
            )
            if (
                lane_snapshot is not None
                and lane_snapshot.status != "active"
                and operation.conclusive_observation_ref
                == "artifact://orphan-sidecar-history"
            ):
                break
            time.sleep(0.01)

    lane_snapshot = store.get_browser_lane()
    operation = store.get_source_operation(run_id, operation_id)
    assert lane_snapshot is not None
    assert lane_snapshot.status == "failed"
    assert (
        lane_snapshot.last_failure_code
        == "liepin_browser_lane_reconciled"
    )
    assert operation.operation_phase == "reconciled"
    assert operation.source_operation_disposition == "completed"
    assert (
        operation.conclusive_observation_ref
        == "artifact://orphan-sidecar-history"
    )


def test_execution_plane_readiness_reports_expired_executor_lease(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    store = app.state.runtime_control_store
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="rtrun-readiness-expired",
            approved_requirement_revision_id="approved-readiness",
            status="queued",
            current_stage="queued",
            source_ids=["cts"],
            created_at="2026-07-30T00:00:00Z",
            updated_at="2026-07-30T00:00:00Z",
        )
    )
    with store._connect() as connection:
        connection.execute(
            """
            INSERT INTO runtime_control_executor_leases (
              lease_id, runtime_run_id, executor_id, attempt_no,
              status, acquired_at, heartbeat_at, lease_expires_at,
              released_at, reason_code
            )
            VALUES (
              'rtlease-readiness-expired',
              'rtrun-readiness-expired',
              'readiness-expired-executor',
              1, 'active',
              '2026-07-30T00:00:00Z', NULL,
              '2026-07-30T00:00:01Z', NULL, NULL
            )
            """
        )

    response = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ).get("/api/health/execution-ready")

    assert response.status_code == 503
    assert response.json()["expiredExecutorLeaseCount"] == 1


def test_execution_plane_readiness_reports_stale_outbox_backlog(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    app.state.agent_conversation_service.outbox_store.insert_once(
        workspace_id="default",
        event_type="workflow_start_requested",
        aggregate_id="readiness-stale-backlog",
        payload={"workflowStartIntentId": "intent-stale"},
        initial_status="pending",
        now="2026-07-30T00:00:00Z",
    )

    response = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ).get("/api/health/execution-ready")

    assert response.status_code == 503
    assert response.json()["oldestBacklogAt"] == "2026-07-30T00:00:00Z"
    assert response.json()["backlogStale"] is True


def test_prod_outbox_claim_expiry_recovers_same_run_through_existing_runner(
    tmp_path: Path,
) -> None:
    _ProductionTopologyRuntime.workflow_calls = 0
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(
        settings=settings,
        runtime_factory=_ProductionTopologyRuntime,
    )
    service = app.state.agent_conversation_service
    conversation = service.create_conversation(
        owner_user_id="user-1",
        workspace_id="default",
        title="Python 平台负责人",
    )
    submitted = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user-1",
        workspace_id="default",
        job_title="Python 平台负责人",
        jd_text="需要 Python API 与平台工程经验。",
        notes=None,
        source_kinds=["cts"],
        idempotency_key="submit-production-topology",
    )
    confirmed = service.confirm_requirements(
        conversation_id=conversation.conversation_id,
        owner_user_id="user-1",
        workspace_id="default",
        draft_revision_id=submitted.requirement_draft_revision_id,
        base_revision_id=submitted.requirement_draft_revision_id,
        idempotency_key="confirm-production-topology",
    )

    assert app.state.workflow_start_outbox_runner.run_once() == 1
    intent = service.workflow_start_intent_store.get(
        confirmed.workflow_start_intent_id
    )
    assert intent.runtime_run_id is not None
    run_id = intent.runtime_run_id
    store = app.state.runtime_control_store
    first_claim = store.claim_next_runnable_run(
        executor_id="crashed-production-executor",
        claimed_at="2026-07-30T00:00:00.000000Z",
        lease_expires_at="2026-07-30T00:00:01.000000Z",
        runtime_run_id=run_id,
    )
    assert first_claim is not None
    store.append_executor_event(
        _event(
            runtime_run_id=run_id,
            event_type="runtime_executor_started",
            stage="startup",
            status="completed",
            summary="executor started before injected crash",
            payload={
                "executorId": first_claim.lease.executor_id,
                "workflowRuntimeRunId": "workflow-crashed",
            },
            created_at="2026-07-30T00:00:00.250000Z",
        ),
        executor_id=first_claim.lease.executor_id,
        attempt_no=first_claim.lease.attempt_no,
        run_status="running",
    )
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-production-topology-finalized",
        runtime_run_id=run_id,
        executor_id=first_claim.lease.executor_id,
        attempt_no=first_claim.lease.attempt_no,
        stage="finalization",
        round_no=None,
        safe_boundary="after_finalization_commit",
        accepted_requirement_revision_id=(
            store.get_run(run_id).approved_requirement_revision_id
        ),
        source_ids=["cts"],
        projection=checkpoint_projection(
            _state_with_round_and_finalization()
        ),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-30T00:00:00.500000Z",
        continuation_cursor={
            "nextPhase": "complete",
            "completedRounds": 1,
            "stopReason": "max_rounds_reached",
        },
    )
    with store._connect() as connection:
        connection.execute(
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
            VALUES (?, 'source-operation-committed', 'liepin', 'cards',
                    ?, 'source-idempotency-committed', ?, 1, ?,
                    'main_committed', 'dispatch://committed',
                    'artifact://committed/result', 'completed',
                    'no_retry', 0, ?, 4)
            """,
            (
                run_id,
                "a" * 64,
                store.get_run(run_id).approved_requirement_revision_id,
                f"executor-lease://{run_id}/1",
                f"checkpoint://{checkpoint.checkpoint_id}",
            ),
        )

    decisions = RuntimeRecoveryService(
        store=store,
        now=lambda: "2026-07-30T00:10:00.000000Z",
    ).recover_start_timeouts(resume_recoverable=True)
    assert decisions[0].runtime_run_id == run_id
    assert store.get_run(run_id).status == "resume_requested"

    app.state.workbench_v2_runtime_runner.start()
    app.state.workbench_v2_runtime_runner.wake(run_id)
    deadline = time.monotonic() + 5
    while (
        store.get_run(run_id).status != "completed"
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    app.state.workbench_v2_runtime_runner.stop(timeout=2)

    completed = store.get_run(run_id)
    assert completed.status == "completed"
    assert completed.runtime_run_id == run_id
    assert _ProductionTopologyRuntime.workflow_calls == 0
    assert store.get_source_operation(
        run_id,
        "source-operation-committed",
    ).operation_phase == "main_committed"
    with sqlite3.connect(store.path) as connection:
        attempts = connection.execute(
            """
            SELECT MAX(attempt_no), COUNT(*)
            FROM runtime_control_executor_leases
            WHERE runtime_run_id = ?
            """,
            (run_id,),
        ).fetchone()
    assert attempts[0] == 2


def test_source_operation_is_injected_during_runtime_construction(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="disabled",
    )
    service = build_agent_service(
        settings=settings,
        runtime_factory=_NoopRuntime,
    )
    executor = service.service_action_adapter.workflow_executor
    assert executor is not None
    operation_executor = object()

    runtime = executor._build_runtime(  # noqa: SLF001
        source_operation_executor=operation_executor,
    )

    assert isinstance(runtime, _NoopRuntime)
    assert runtime.source_operation_executor is operation_executor
    source = inspect.getsource(WorkflowRuntimeExecutor.execute_claimed_run)
    assert "isinstance(runtime, WorkflowRuntime)" not in source
    assert "runtime.source_operation_executor =" not in source


def test_live_liepin_lane_fails_before_building_direct_browser_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import asyncio
    import seektalent.sources.liepin.runtime_lane as runtime_lane

    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="opencli",
    )
    monkeypatch.setattr(
        runtime_lane,
        "build_liepin_worker_client",
        lambda *_args, **_kwargs: pytest.fail(
            "production live path reached direct browser client construction"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="liepin_source_operation_executor_required",
    ):
        asyncio.run(
            run_liepin_source_lane(
                settings=settings,
                request=RuntimeSourceLaneRequest(
                    source="liepin",
                    lane_mode="card",
                    job_title="Backend Engineer",
                    jd="Python",
                    notes="",
                    requirement_sheet=None,
                    runtime_run_id="rtrun-hard-cut",
                ),
            )
        )


def test_live_liepin_first_page_expansion_has_no_direct_browser_bypass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import asyncio
    import seektalent.sources.liepin.runtime_lane as runtime_lane

    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="opencli",
    )
    monkeypatch.setattr(
        runtime_lane,
        "build_liepin_worker_client",
        lambda *_args, **_kwargs: pytest.fail(
            "first-page expansion reached direct browser construction"
        ),
    )
    request = SourceFirstPageExpansionRequest(
        runtime_run_id="rtrun-first-page",
        round_no=1,
        source_kind="liepin",
        query_instance_id="query-first-page",
        continuation_id="continuation-first-page",
        continuation=object(),
        action="expand",
    )

    with pytest.raises(
        RuntimeError,
        match="liepin_source_operation_executor_required",
    ):
        asyncio.run(
            run_liepin_first_page_expansion(
                settings=settings,
                request=request,
                detail_open_claim_ledger=DetailOpenClaimLedger({}),
            )
        )


def test_live_liepin_provider_plugin_has_no_direct_browser_bypass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import seektalent.providers.plugins as plugins

    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="opencli",
    )
    monkeypatch.setattr(
        plugins,
        "build_liepin_worker_client",
        lambda *_args, **_kwargs: pytest.fail(
            "provider plugin reached direct browser construction"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="liepin_source_operation_executor_required",
    ):
        plugins.build_default_provider_adapter_registry().build_adapter(
            "liepin",
            plugins.ProviderAdapterBuildContext(settings=settings),
        )


def test_external_http_does_not_require_local_browser_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import asyncio
    import seektalent.sources.liepin.runtime_lane as runtime_lane

    class ExternalBoundaryReached(RuntimeError):
        pass

    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="external_http",
        liepin_worker_base_url="https://worker.example.test",
    )
    monkeypatch.setattr(
        runtime_lane,
        "build_liepin_worker_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExternalBoundaryReached
        ),
    )

    with pytest.raises(ExternalBoundaryReached):
        asyncio.run(
            run_liepin_source_lane(
                settings=settings,
                request=RuntimeSourceLaneRequest(
                    source="liepin",
                    lane_mode="card",
                    job_title="Backend Engineer",
                    jd="Python",
                    notes="",
                    requirement_sheet=None,
                    runtime_run_id="rtrun-external-http",
                ),
            )
        )


def test_runtime_factory_contract_has_no_capability_probing() -> None:
    factory_source = Path(
        "src/seektalent_conversation_agent/factory.py"
    ).read_text(encoding="utf-8")
    assert "inspect import" not in factory_source
    assert "signature(" not in factory_source
    runtime_builder_source = inspect.getsource(
        WorkflowRuntimeExecutor._build_runtime
    )
    assert "signature(" not in runtime_builder_source


def _table_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        tables = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        return {
            str(name): int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{name}"'
                ).fetchone()[0]
            )
            for (name,) in tables
        }
