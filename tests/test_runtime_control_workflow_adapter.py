from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent.models import RequirementSheet
from seektalent_runtime_control.browser_lane import (
    BrowserLaneBusyError,
    BrowserLaneGuard,
    LIEPIN_BROWSER_LANE,
)
from seektalent.providers.liepin.runtime_context import local_opencli_liepin_source_context
from seektalent.providers.liepin.store import LiepinStore
from seektalent.progress import ProgressEvent
from tests.settings_factory import make_settings


def test_workflow_adapter_persists_run_and_runtime_callbacks(tmp_path: Path) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    runtime = CallbackRuntime()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda *, source_operation_executor=None: runtime,
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "executor_1",
        checkpoint_id_factory=lambda: "rtcheckpoint_1",
        now=_clock(
            "2026-06-08T00:00:00.000000Z",
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
            "2026-06-08T00:00:03.000000Z",
            "2026-06-08T00:00:04.000000Z",
            "2026-06-08T00:00:05.000000Z",
        ),
    )

    run = asyncio.run(
        executor.start_workflow(
            conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement=_approved_requirement(),
            job_title="Senior Python Engineer",
            jd_text="Build search systems.",
            notes="Remote.",
            source_ids=["cts", "custom_source"],
        )
    )

    assert run.runtime_run_id == "runtime_run_1"
    assert store.get_run("runtime_run_1").status == "completed"
    assert runtime.received["approved_requirement_sheet"] == _requirement_sheet()
    assert runtime.received["source_kinds"] == ["cts", "custom_source"]

    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=20).events
    assert [event.event_type for event in events] == [
        "runtime_run_queued",
        "runtime_worker_claimed",
        "runtime_executor_starting",
        "runtime_executor_started",
        "runtime_run_started",
        "runtime_checkpoint_written",
        "runtime_run_completed",
    ]
    assert events[3].payload["workflowRuntimeRunId"] == "workflow_run_1"
    assert events[5].payload["checkpointId"] == "rtcheckpoint_1"
    checkpoint = store.get_latest_checkpoint(
        runtime_run_id="runtime_run_1"
    )
    assert checkpoint is not None
    assert checkpoint.is_final_manifest is True
    assert checkpoint.run_state == {}


def test_workflow_adapter_persists_private_detail_claim_map_without_exposing_checkpoint_detail(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    claim_key = "a" * 64
    claim_map = {
        claim_key: {
            "status": "opened",
            "browser_open_attempt_count": 1,
            "last_safe_reason_code": None,
        }
    }
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda *, source_operation_executor=None: CallbackRuntime(
            checkpoint_run_state={"detail_open_claims_by_provider_key": claim_map}
        ),
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "executor_1",
        checkpoint_id_factory=lambda: "rtcheckpoint_1",
        now=_clock(
            "2026-06-08T00:00:00.000000Z",
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
            "2026-06-08T00:00:03.000000Z",
            "2026-06-08T00:00:04.000000Z",
            "2026-06-08T00:00:05.000000Z",
        ),
    )

    asyncio.run(
        executor.start_workflow(
            conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement=_approved_requirement(),
            job_title="Senior Python Engineer",
            jd_text="Build search systems.",
            notes=None,
            source_ids=["liepin"],
        )
    )

    checkpoint = store.get_latest_checkpoint(runtime_run_id="runtime_run_1")
    assert checkpoint is not None
    assert store.get_detail_claim_snapshot(
        runtime_run_id="runtime_run_1"
    ) == claim_map
    assert "detail_open_claims_by_provider_key" not in checkpoint.run_state

    detail = RuntimeDetailService(store=store).get_runtime_detail(
        runtime_run_id="runtime_run_1",
        kind="checkpoint",
        checkpoint_id="rtcheckpoint_1",
        include_artifacts=False,
    )
    assert claim_key not in detail.model_dump_json()
    assert "detail_open_claims_by_provider_key" not in detail.model_dump_json()


def test_workflow_adapter_supplies_liepin_source_context(tmp_path: Path) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    runtime = CallbackRuntime()
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="opencli",
        liepin_session_store_key_id="unit-session-key",
    )
    executor = WorkflowRuntimeExecutor(
        store=store,
        settings=settings,
        runtime_factory=lambda *, source_operation_executor=None: runtime,
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "executor_1",
        checkpoint_id_factory=lambda: "rtcheckpoint_1",
        source_context_provider=local_opencli_liepin_source_context,
        now=_clock(
            "2026-06-08T00:00:00.000000Z",
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
            "2026-06-08T00:00:03.000000Z",
            "2026-06-08T00:00:04.000000Z",
            "2026-06-08T00:00:05.000000Z",
        ),
    )

    asyncio.run(
        executor.start_workflow(
            conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement=_approved_requirement(),
            job_title="Senior Python Engineer",
            jd_text="Build search systems.",
            notes=None,
            source_ids=["liepin"],
        )
    )

    assert runtime.received["source_context"] == {
        "actor_id": "local",
        "backend_mode": "opencli",
        "compliance_gate_ref": runtime.received["source_context"]["compliance_gate_ref"],
        "connection_id": "liepin-opencli",
        "provider_account_hash": "liepin-opencli-local",
        "tenant_id": "local",
        "workspace_id": "default",
    }
    gate_ref = runtime.received["source_context"]["compliance_gate_ref"]
    assert isinstance(gate_ref, str)
    assert gate_ref.startswith("gate_")

    liepin_store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    gate = liepin_store.get_compliance_gate(
        gate_ref=gate_ref,
        tenant_id="local",
        workspace_id="default",
        actor_id="local",
    )
    assert gate is not None
    assert gate.denial_reason(provider_account_hash="liepin-opencli-local", purpose="search") is None
    session = liepin_store.get_session_metadata(
        tenant_id="local",
        workspace_id="default",
        actor_id="local",
        connection_id="liepin-opencli",
    )
    assert session is not None
    assert session["status"] == "connected"
    assert session["provider_account_hash"] == "liepin-opencli-local"


def test_workflow_adapter_records_failed_event_before_reraising_runtime_error(tmp_path: Path) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda *, source_operation_executor=None: FailingRuntime(),
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "executor_1",
        now=_clock(
            "2026-06-08T00:00:00.000000Z",
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
        ),
    )

    with pytest.raises(RuntimeError, match="runtime failed before start ack"):
        asyncio.run(
            executor.start_workflow(
                conversation_id="agent_conv_1",
                workbench_session_id=None,
                approved_requirement=_approved_requirement(),
                job_title="Senior Python Engineer",
                jd_text="Build search systems.",
                notes=None,
                source_ids=["cts"],
            )
        )

    assert store.get_run("runtime_run_1").status == "failed"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=20).events
    assert [event.event_type for event in events] == [
        "runtime_run_queued",
        "runtime_worker_claimed",
        "runtime_executor_starting",
        "runtime_executor_start_failed",
    ]
    assert events[-1].payload["reasonCode"] == "runtime_executor_start_failed"


def test_workflow_adapter_records_runtime_run_failed_after_start_ack(tmp_path: Path) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda *, source_operation_executor=None: PostStartFailingRuntime(),
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "executor_1",
        now=_clock(
            "2026-06-08T00:00:00.000000Z",
            "2026-06-08T00:00:01.000000Z",
            "2026-06-08T00:00:02.000000Z",
            "2026-06-08T00:00:03.000000Z",
            "2026-06-08T00:00:04.000000Z",
            "2026-06-08T00:00:05.000000Z",
        ),
    )

    with pytest.raises(RuntimeError, match="runtime failed after start ack"):
        asyncio.run(
            executor.start_workflow(
                conversation_id="agent_conv_1",
                workbench_session_id=None,
                approved_requirement=_approved_requirement(),
                job_title="Senior Python Engineer",
                jd_text="Build search systems.",
                notes=None,
                source_ids=["cts"],
            )
        )

    run = store.get_run("runtime_run_1")
    assert run.status == "failed"
    assert run.stop_reason_code == "runtime_run_failed"
    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=20).events
    assert [event.event_type for event in events] == [
        "runtime_run_queued",
        "runtime_worker_claimed",
        "runtime_executor_starting",
        "runtime_executor_started",
        "runtime_run_failed",
    ]
    assert events[-1].payload == {
        "reasonCode": "runtime_run_failed",
        "exceptionType": "RuntimeError",
    }


def test_browser_lane_contention_yields_same_durable_run_then_completes(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    runtimes = iter([BrowserBusyRuntime(), CallbackRuntime()])
    approved = _approved_requirement()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: next(runtimes),
        runtime_run_id_factory=lambda: "runtime_run_lane_wait",
        executor_id_factory=lambda: "executor_unused",
        checkpoint_id_factory=lambda: "checkpoint_lane_wait",
        now=lambda: "2026-07-30T00:00:02.000000Z",
    )
    queued = executor.enqueue_workflow_run(
        conversation_id="agent_lane_wait",
        workbench_session_id=None,
        approved_requirement=approved,
        job_title="Senior Python Engineer",
        jd_text="Build search systems.",
        notes=None,
        source_ids=["liepin"],
    )
    first_claim = store.claim_next_runnable_run(
        executor_id="executor_lane_wait_1",
        claimed_at="2026-07-30T00:00:00.000000Z",
        lease_expires_at="2026-07-30T00:01:00.000000Z",
        runtime_run_id=queued.runtime_run_id,
    )
    assert first_claim is not None

    waiting = asyncio.run(
        executor.execute_claimed_run(
            runtime_run_id=first_claim.runtime_run.runtime_run_id,
            executor_id=first_claim.lease.executor_id,
            attempt_no=first_claim.lease.attempt_no,
            approved_requirement=approved,
        )
    )

    assert waiting.runtime_run_id == queued.runtime_run_id
    assert waiting.status == "resume_requested"
    assert store.claim_next_runnable_run(
        executor_id="executor_lane_wait_too_soon",
        claimed_at="2026-07-30T00:00:02.100000Z",
        lease_expires_at="2026-07-30T00:01:02.100000Z",
        runtime_run_id=queued.runtime_run_id,
    ) is None
    second_claim = store.claim_next_runnable_run(
        executor_id="executor_lane_wait_2",
        claimed_at="2026-07-30T00:00:03.000000Z",
        lease_expires_at="2026-07-30T00:01:03.000000Z",
        runtime_run_id=queued.runtime_run_id,
    )
    assert second_claim is not None
    assert second_claim.claim_reason == "resource_wait"

    completed = asyncio.run(
        executor.execute_claimed_run(
            runtime_run_id=second_claim.runtime_run.runtime_run_id,
            executor_id=second_claim.lease.executor_id,
            attempt_no=second_claim.lease.attempt_no,
            approved_requirement=approved,
        )
    )

    assert completed.runtime_run_id == queued.runtime_run_id
    assert completed.status == "completed"
    assert second_claim.lease.attempt_no == 2
    events = store.list_events(
        runtime_run_id=queued.runtime_run_id,
        after_seq=0,
        limit=30,
    ).events
    assert [event.event_type for event in events].count(
        "runtime_resource_waiting"
    ) == 1
    assert [event.event_type for event in events].count(
        "runtime_run_completed"
    ) == 1


def test_cross_process_lane_contention_keeps_run_durable_until_one_effect(
    tmp_path: Path,
) -> None:
    import time

    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore
    from seektalent_workbench_v2.runtime_runner import (
        WorkbenchV2RuntimeQueueRunner,
    )

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_browser_lane,
        args=(str(store.path), ready, release),
    )
    holder.start()
    assert ready.wait(timeout=10)
    effects: list[str] = []
    approved = _approved_requirement()
    store.save_approved_requirement(
        approved,
        idempotency_key="cross-process-lane-approved",
    )
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda *, source_operation_executor=None: (
            _LaneGuardRuntime(store, effects)
        ),
        runtime_run_id_factory=lambda: "runtime_run_cross_process_lane",
        now=lambda: _iso_now(),
    )
    queued = executor.enqueue_workflow_run(
        conversation_id="agent-cross-process-lane",
        workbench_session_id=None,
        approved_requirement=approved,
        job_title="Senior Python Engineer",
        jd_text="Build search systems.",
        notes=None,
        source_ids=["liepin"],
    )
    runner = WorkbenchV2RuntimeQueueRunner(
        store=store,
        executor=executor,
        poll_interval_seconds=0.05,
        recovery_interval_seconds=60,
    )
    try:
        runner.start()
        runner.wake(queued.runtime_run_id)
        deadline = time.monotonic() + 5
        while (
            store.get_run(queued.runtime_run_id).status
            != "resume_requested"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        waiting_status = store.get_run(queued.runtime_run_id).status
        assert waiting_status == "resume_requested", {
            "status": waiting_status,
            "events": [
                event.event_type
                for event in store.list_events(
                    runtime_run_id=queued.runtime_run_id,
                    after_seq=0,
                    limit=100,
                ).events
            ],
            "failures": [
                (
                    failure.component,
                    failure.boundary,
                    failure.exception_type,
                    failure.safe_reason_code,
                )
                for failure in store.list_execution_failures()
            ],
        }
        assert holder.is_alive()
        assert effects == []

        release.set()
        holder.join(timeout=10)
        assert holder.exitcode == 0
        runner.wake(queued.runtime_run_id)
        deadline = time.monotonic() + 5
        while (
            store.get_run(queued.runtime_run_id).status != "completed"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
    finally:
        release.set()
        runner.stop(timeout=2)
        holder.join(timeout=2)

    assert store.get_run(queued.runtime_run_id).status == "completed"
    assert effects == ["browser-effect"]
    events = store.list_events(
        runtime_run_id=queued.runtime_run_id,
        after_seq=0,
        limit=100,
    ).events
    assert any(
        event.event_type == "runtime_resource_waiting"
        for event in events
    )
    assert [
        event.event_type for event in events
    ].count("runtime_run_completed") == 1


class CallbackRuntime:
    def __init__(self, *, checkpoint_run_state: dict[str, object] | None = None) -> None:
        self.received: dict[str, object] = {}
        self._checkpoint_run_state = checkpoint_run_state or {"round": 1}

    async def run_async(self, **kwargs):
        self.received = dict(kwargs)
        kwargs["runtime_start_callback"]("workflow_run_1")
        kwargs["progress_callback"](
            ProgressEvent(
                type="run_started",
                message="Starting SeekTalent run.",
                round_no=1,
                payload={"stage": "runtime"},
            )
        )
        detail_claims = self._checkpoint_run_state.get(
            "detail_open_claims_by_provider_key"
        )
        if detail_claims is not None:
            kwargs["runtime_detail_claim_callback"](detail_claims)
        kwargs["runtime_checkpoint_callback"](
            SimpleNamespace(
                run_id="workflow_run_1",
                run_state=SimpleNamespace(model_dump=lambda mode="json": self._checkpoint_run_state),
            )
        )
        return SimpleNamespace(run_id="workflow_run_1")


class FailingRuntime:
    async def run_async(self, **kwargs):
        raise RuntimeError("runtime failed before start ack")


class PostStartFailingRuntime:
    async def run_async(self, **kwargs):
        kwargs["runtime_start_callback"]("workflow_run_1")
        raise RuntimeError("runtime failed after start ack")


class BrowserBusyRuntime:
    async def run_async(self, **kwargs):
        kwargs["runtime_start_callback"]("workflow_run_lane_wait")
        raise BrowserLaneBusyError("liepin_browser_lane_busy")


class _LaneGuardRuntime:
    def __init__(self, store, effects: list[str]) -> None:
        self.store = store
        self.effects = effects

    async def run_async(self, **kwargs):
        kwargs["runtime_start_callback"]("workflow-cross-process-lane")
        with BrowserLaneGuard(
            store=self.store,
            runtime_run_id="runtime_run_cross_process_lane",
            operation_id="operation-cross-process-lane",
            operation_kind="cards",
            now=_iso_now,
            plus_seconds=_plus_seconds,
            wait_timeout_seconds=0.05,
            lease_seconds=1,
            poll_interval_seconds=0.01,
        ):
            self.effects.append("browser-effect")
        kwargs["runtime_checkpoint_callback"](
            SimpleNamespace(
                run_id="workflow-cross-process-lane",
                run_state=SimpleNamespace(
                    model_dump=lambda mode="json": {"round": 1}
                ),
            )
        )
        return SimpleNamespace(run_id="workflow-cross-process-lane")


def _hold_browser_lane(
    database_path: str,
    ready,
    release,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(database_path)
    store.initialize()
    lease = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="cross-process-holder",
        owner_process_id=1,
        process_boot_id="cross-process-holder-boot",
        runtime_run_id="runtime-run-holder",
        operation_id="operation-holder",
        operation_kind="details",
        acquired_at=_iso_now(),
        lease_expires_at="2099-01-01T00:00:00.000000Z",
    )
    if lease is None:
        raise RuntimeError("cross_process_holder_failed")
    ready.set()
    if not release.wait(timeout=15):
        raise RuntimeError("cross_process_holder_timeout")
    store.release_browser_lane(
        lane_key=lease.lane_key,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
        released_at=_iso_now(),
        status="completed",
    )


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _plus_seconds(value: str, seconds: float) -> str:
    current = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (current + timedelta(seconds=seconds)).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _approved_requirement():
    from seektalent_runtime_control.requirements import ApprovedRequirementRevision

    return ApprovedRequirementRevision(
        approved_requirement_revision_id="reqapproved_1",
        draft_revision_id="reqdraft_1",
        agent_conversation_id="agent_conv_1",
        requirement_sheet=_requirement_sheet(),
        selected_item_ids=["item_1"],
        deselected_item_ids=[],
        created_at="2026-06-08T00:00:00.000000Z",
    )


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="Senior Python Engineer",
        title_anchor_terms=["Python Engineer"],
        title_anchor_rationale="Title is explicit.",
        role_summary="Build search systems.",
        must_have_capabilities=["Python"],
        preferred_capabilities=["Search"],
        exclusion_signals=[],
        scoring_rationale="Relevant experience.",
    )


def _clock(*values: str):
    iterator = iter(values)
    last = values[-1]

    def now() -> str:
        nonlocal last
        last = next(iterator, last)
        return last

    return now
