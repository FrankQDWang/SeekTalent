from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from seektalent.models import RequirementSheet
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.models import RuntimeCheckpoint
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.worker import RuntimeExecutionWorker


def test_resume_reuses_same_runtime_run_with_new_attempt_and_checkpoint_context(tmp_path) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    approved = _approved_requirement()
    store.save_approved_requirement(approved, idempotency_key="approved")
    runtime = ResumeAwareRuntime()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: runtime,
        runtime_run_id_factory=lambda: "runtime-run-hitl",
        now=_clock(
            "2026-06-17T00:00:00.000000Z",
            "2026-06-17T00:00:01.000000Z",
            "2026-06-17T00:00:02.000000Z",
            "2026-06-17T00:00:03.000000Z",
            "2026-06-17T00:00:04.000000Z",
            "2026-06-17T00:00:05.000000Z",
            "2026-06-17T00:00:06.000000Z",
            "2026-06-17T00:00:07.000000Z",
            "2026-06-17T00:00:08.000000Z",
            "2026-06-17T00:00:09.000000Z",
        ),
    )
    run = executor.enqueue_workflow_run(
        conversation_id="agent-conv-hitl",
        workbench_session_id="session-hitl",
        approved_requirement=approved,
        job_title="Backend Engineer",
        jd_text="Build data products.",
        notes="Remote only.",
        source_ids=["cts"],
    )
    store.acquire_executor_lease(
        runtime_run_id=run.runtime_run_id,
        executor_id="executor-pause",
        acquired_at="2026-06-17T00:01:00.000000Z",
        lease_expires_at="2026-06-17T00:02:00.000000Z",
    )
    store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="starting",
        current_stage="startup",
        current_round=None,
        updated_at="2026-06-17T00:01:00.500000Z",
    )
    store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="running",
        current_stage="round",
        current_round=2,
        updated_at="2026-06-17T00:01:01.000000Z",
    )
    command_service = RuntimeCommandService(
        store=store,
        now=_clock(
            "2026-06-17T00:01:02.000000Z",
            "2026-06-17T00:01:03.000000Z",
            "2026-06-17T00:01:04.000000Z",
        ),
    )
    pause = command_service.request_pause(
        runtime_run_id=run.runtime_run_id,
        requested_by="agent",
        idempotency_key="pause-1",
    )

    applied = command_service.apply_lifecycle_command_at_safe_boundary(
        runtime_run_id=run.runtime_run_id,
        executor_id="executor-pause",
        safe_boundary="after_round_controller",
        checkpoint=RuntimeCheckpoint(
            checkpoint_id="checkpoint-pause-1",
            runtime_run_id=run.runtime_run_id,
            stage="round",
            round_no=2,
            safe_boundary="after_round_controller",
            run_state={"round": 2, "cursor": "after-controller"},
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-17T00:01:03.000000Z",
        ),
    )

    assert applied is not None
    assert applied.command_id == pause.command_id
    assert store.get_run(run.runtime_run_id).status == "paused"
    assert store.get_latest_checkpoint(runtime_run_id=run.runtime_run_id).checkpoint_id == "checkpoint-pause-1"
    assert not store.list_active_executor_leases()

    resume = command_service.resume_workflow(
        runtime_run_id=run.runtime_run_id,
        requested_by="agent",
        idempotency_key="resume-1",
    )
    worker = RuntimeExecutionWorker(
        store=store,
        executor=executor,
        executor_id_factory=lambda: "executor-resume",
        now=lambda: "2026-06-17T00:02:00.000000Z",
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )

    result = asyncio.run(worker.run_once(runtime_run_id=run.runtime_run_id))

    assert resume.command_type == "resume"
    assert result is not None
    assert result.runtime_run_id == run.runtime_run_id
    assert result.status == "completed"
    assert runtime.received["resume_checkpoint"]["checkpoint_id"] == "checkpoint-pause-1"
    assert runtime.received["resume_run_state"] == {"round": 2, "cursor": "after-controller"}
    leases = _executor_leases(store)
    assert [(lease["executor_id"], lease["attempt_no"], lease["status"]) for lease in leases] == [
        ("executor-pause", 1, "released"),
        ("executor-resume", 2, "released"),
    ]
    events = store.list_events(runtime_run_id=run.runtime_run_id, after_seq=0, limit=30).events
    assert "runtime_resumed" in [event.event_type for event in events]


@pytest.mark.parametrize(
    ("checkpoint_mode", "expected_reason_code"),
    [
        ("missing", "runtime_resume_checkpoint_missing"),
        ("corrupt", "runtime_checkpoint_corrupt"),
    ],
)
def test_resume_failure_releases_lease_without_calling_runtime_when_checkpoint_is_unavailable(
    tmp_path,
    checkpoint_mode: str,
    expected_reason_code: str,
) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    approved = _approved_requirement()
    store.save_approved_requirement(approved, idempotency_key=f"approved-{checkpoint_mode}")
    runtime = RuntimeMustNotRun()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: runtime,
        runtime_run_id_factory=lambda: f"runtime-run-{checkpoint_mode}",
        now=_clock(
            "2026-06-17T01:00:00.000000Z",
            "2026-06-17T01:00:01.000000Z",
            "2026-06-17T01:00:02.000000Z",
            "2026-06-17T01:00:03.000000Z",
            "2026-06-17T01:00:04.000000Z",
            "2026-06-17T01:00:05.000000Z",
            "2026-06-17T01:00:06.000000Z",
            "2026-06-17T01:00:07.000000Z",
            "2026-06-17T01:00:08.000000Z",
            "2026-06-17T01:00:09.000000Z",
        ),
    )
    run = executor.enqueue_workflow_run(
        conversation_id="agent-conv-hitl",
        workbench_session_id="session-hitl",
        approved_requirement=approved,
        job_title="Backend Engineer",
        jd_text="Build data products.",
        notes="Remote only.",
        source_ids=["cts"],
    )
    store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="starting",
        current_stage="startup",
        current_round=None,
        updated_at="2026-06-17T01:00:09.000000Z",
    )
    store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="running",
        current_stage="round",
        current_round=1,
        updated_at="2026-06-17T01:00:09.500000Z",
    )
    store.update_run_status(
        runtime_run_id=run.runtime_run_id,
        status="paused",
        current_stage="round",
        current_round=1,
        updated_at="2026-06-17T01:00:10.000000Z",
    )
    if checkpoint_mode == "corrupt":
        _insert_corrupt_checkpoint(store, runtime_run_id=run.runtime_run_id)
    RuntimeCommandService(
        store=store,
        now=_clock("2026-06-17T01:00:11.000000Z", "2026-06-17T01:00:12.000000Z"),
    ).resume_workflow(
        runtime_run_id=run.runtime_run_id,
        requested_by="agent",
        idempotency_key=f"resume-{checkpoint_mode}",
    )
    worker = RuntimeExecutionWorker(
        store=store,
        executor=executor,
        executor_id_factory=lambda: f"executor-resume-{checkpoint_mode}",
        now=lambda: "2026-06-17T01:01:00.000000Z",
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        asyncio.run(worker.run_once(runtime_run_id=run.runtime_run_id))

    assert exc_info.value.reason_code == expected_reason_code
    assert runtime.call_count == 0
    failed = store.get_run(run.runtime_run_id)
    assert failed.status == "failed"
    assert failed.stop_reason_code == expected_reason_code
    assert not store.list_active_executor_leases()
    leases = _executor_leases(store)
    assert leases == [
        {
            "executor_id": f"executor-resume-{checkpoint_mode}",
            "attempt_no": 1,
            "status": "failed",
            "reason_code": expected_reason_code,
        }
    ]
    events = store.list_events(runtime_run_id=run.runtime_run_id, after_seq=0, limit=30).events
    assert [event.event_type for event in events if event.event_type == "runtime_resume_failed"] == [
        "runtime_resume_failed"
    ]
    assert events[-1].event_type == "runtime_resume_failed"
    assert events[-1].payload["reasonCode"] == expected_reason_code


class ResumeAwareRuntime:
    supports_resume_context = True

    def __init__(self) -> None:
        self.received: dict[str, object] = {}

    async def run_async(self, **kwargs):
        self.received = dict(kwargs)
        kwargs["runtime_start_callback"]("workflow-resumed")
        return SimpleNamespace(run_id="workflow-resumed")


class RuntimeMustNotRun:
    def __init__(self) -> None:
        self.call_count = 0

    async def run_async(self, **kwargs):
        del kwargs
        self.call_count += 1
        raise AssertionError("runtime must not be called without a recoverable resume checkpoint")


def _executor_leases(store: RuntimeControlStore) -> list[dict[str, object]]:
    import sqlite3

    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT executor_id, attempt_no, status, reason_code
                FROM runtime_control_executor_leases
                ORDER BY attempt_no ASC
                """
            ).fetchall()
        ]


def _insert_corrupt_checkpoint(store: RuntimeControlStore, *, runtime_run_id: str) -> None:
    with store._connect() as conn, conn:
        checkpoint_id = f"checkpoint-corrupt-{runtime_run_id}"
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                run_state_json, source_plan_json, pending_commands_json,
                artifact_manifest_ref, schema_version, created_at
            )
            VALUES (?, ?, 'round', 1, 'after_round_controller', '{not json', '{}', '[]', NULL, ?, ?)
            """,
            (
                checkpoint_id,
                runtime_run_id,
                "runtime-control-checkpoint/v1",
                "2026-06-17T01:00:10.500000Z",
            ),
        )
        conn.execute(
            "UPDATE runtime_control_runs SET latest_checkpoint_id = ? WHERE runtime_run_id = ?",
            (checkpoint_id, runtime_run_id),
        )


def _approved_requirement() -> ApprovedRequirementRevision:
    return ApprovedRequirementRevision(
        approved_requirement_revision_id="approved-hitl",
        draft_revision_id="draft-hitl",
        agent_conversation_id="agent-conv-hitl",
        requirement_sheet=RequirementSheet(
            job_title="Backend Engineer",
            title_anchor_terms=["Backend Engineer"],
            title_anchor_rationale="The title is explicit.",
            role_summary="Build data products.",
            must_have_capabilities=["Python"],
            preferred_capabilities=["Search"],
            exclusion_signals=[],
            scoring_rationale="Prioritize backend data products.",
        ),
        selected_item_ids=[],
        deselected_item_ids=[],
        created_at="2026-06-17T00:00:00.000000Z",
    )


def _clock(*values: str):
    iterator = iter(values)
    last = values[-1]

    def now() -> str:
        nonlocal last
        last = next(iterator, last)
        return last

    return now
