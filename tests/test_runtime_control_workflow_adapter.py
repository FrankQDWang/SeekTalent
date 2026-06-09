from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent.models import RequirementSheet
from seektalent.progress import ProgressEvent


def test_workflow_adapter_persists_run_and_runtime_callbacks(tmp_path: Path) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    runtime = CallbackRuntime()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: runtime,
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
        "runtime_executor_starting",
        "runtime_executor_started",
        "runtime_run_started",
        "runtime_checkpoint_written",
        "runtime_run_completed",
    ]
    assert events[1].payload["workflowRuntimeRunId"] == "workflow_run_1"
    assert events[3].payload["checkpointId"] == "rtcheckpoint_1"
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_1").run_state == {"round": 1}


def test_workflow_adapter_records_failed_event_before_reraising_runtime_error(tmp_path: Path) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: FailingRuntime(),
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
        runtime_factory=lambda: PostStartFailingRuntime(),
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
        "runtime_executor_starting",
        "runtime_executor_started",
        "runtime_run_failed",
    ]
    assert events[-1].payload == {
        "reasonCode": "runtime_run_failed",
        "message": "runtime failed after start ack",
    }


class CallbackRuntime:
    def __init__(self) -> None:
        self.received: dict[str, object] = {}

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
        kwargs["runtime_checkpoint_callback"](
            SimpleNamespace(
                run_id="workflow_run_1",
                run_state=SimpleNamespace(model_dump=lambda mode="json": {"round": 1}),
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
