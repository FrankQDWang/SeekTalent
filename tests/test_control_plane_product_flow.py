from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from seektalent.models import RequirementSheet
from seektalent.privacy_erasure import erase_candidate_subject
from seektalent.progress import ProgressEvent
from seektalent.runtime.public_events import make_runtime_public_event
from seektalent_conversation_agent.store import ConversationStore
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.retention import RuntimeControlRetentionPolicy, RuntimeRetentionService
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.worker import RuntimeExecutionWorker
from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge
from seektalent_ui.workbench_store import WorkbenchStore
from tests.test_runtime_control_candidate_truth import _run_state_payload


def test_db_first_control_plane_product_flow_without_artifact_reconciliation(tmp_path: Path) -> None:
    runtime_store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    runtime_store.initialize()
    conversation_store = ConversationStore(tmp_path / "conversation_agent.sqlite3")
    conversation_store.initialize()
    workbench_store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = workbench_store.ensure_local_actor()
    conversation = conversation_store.create_conversation(
        conversation_id="agent_conv_product_flow",
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
        title="Data Platform Engineer",
        created_at="2026-06-17T00:00:00.000000Z",
    )
    approved = runtime_store.save_approved_requirement(
        _approved_requirement(conversation.conversation_id),
        idempotency_key="reqapproved_product_flow:save",
    )
    session = workbench_store.create_workbench_session(
        user=user,
        job_title="Data Platform Engineer",
        jd_text="Build data products.",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime_run_product_flow",
    )
    runtime = _ProductFlowRuntime()
    executor = WorkflowRuntimeExecutor(
        store=runtime_store,
        runtime_factory=lambda: runtime,
        runtime_run_id_factory=lambda: "runtime_run_product_flow",
        executor_id_factory=lambda: "executor_product_flow",
        checkpoint_id_factory=lambda: "rtcheckpoint_product_flow",
        now=_clock(),
        lease_seconds=60,
    )

    queued = executor.enqueue_workflow_run(
        conversation_id=conversation.conversation_id,
        workbench_session_id=session.session_id,
        approved_requirement=approved,
        job_title="Data Platform Engineer",
        jd_text="Build data products.",
        notes="Remote-friendly.",
        source_ids=["cts"],
        run_intent_id="intent_product_flow",
        start_idempotency_key="start_product_flow",
    )
    conversation_store.link_runtime_run(
        conversation_id=conversation.conversation_id,
        runtime_run_id=queued.runtime_run_id,
        workbench_session_id=session.session_id,
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-17T00:00:04.000000Z",
    )
    assert queued.status == "queued"

    worker = RuntimeExecutionWorker(
        store=runtime_store,
        executor=executor,
        executor_id_factory=lambda: "executor_product_flow",
        now=_clock("2026-06-17T00:01:00.000000Z"),
        lease_seconds=60,
        heartbeat_interval_seconds=0.01,
    )
    completed = asyncio.run(worker.run_once(runtime_run_id=queued.runtime_run_id))

    assert completed is not None
    assert completed.status == "completed"
    assert runtime.artifact_read_count == 0
    assert not runtime_store.list_active_executor_leases()
    reopened = conversation_store.reopen_conversation(
        conversation_id=conversation.conversation_id,
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
        opened_at="2026-06-17T00:01:30.000000Z",
    ).conversation_reopen_state
    assert reopened.runtime_run_id == queued.runtime_run_id

    public_events = runtime_store.list_public_events(runtime_run_id=queued.runtime_run_id, after_seq=0, limit=20).events
    assert [event.event_type for event in public_events] == [
        "runtime_round_source_result",
        "runtime_finalization_completed",
    ]
    assert [event.payload["runtimeRunId"] for event in public_events] == [
        queued.runtime_run_id,
        queued.runtime_run_id,
    ]
    assert [event.payload["eventId"] for event in public_events] == [
        "runtime_run_product_flow:1:source_result:cts",
        "runtime_run_product_flow:final:finalization:all",
    ]
    assert runtime_store.list_stage_outputs(runtime_run_id=queued.runtime_run_id)
    assert runtime_store.list_candidate_identities(runtime_run_id=queued.runtime_run_id)[0].display_name == "Alice Chen"

    projection = RuntimeControlProjectionService(
        runtime_store=runtime_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store),
        user=user,
        now=lambda: "2026-06-17T00:02:00.000000Z",
    )
    result = projection.project_unprojected_public_events(runtime_run_id=queued.runtime_run_id, limit=20)

    assert result.failed_count == 0
    with sqlite3.connect(workbench_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        session_events = conn.execute("SELECT event_name FROM session_events ORDER BY global_seq").fetchall()
        review_item = conn.execute("SELECT display_name, aggregate_score FROM candidate_review_items").fetchone()
    runtime_event_names = [
        row["event_name"] for row in session_events if str(row["event_name"]).startswith("runtime_")
    ]
    assert runtime_event_names == [
        "runtime_round_source_result",
        "runtime_finalization_completed",
    ]
    assert review_item["display_name"] == "Alice Chen"
    assert review_item["aggregate_score"] == 92

    retention = RuntimeRetentionService(
        store=runtime_store,
        now=lambda: "2026-06-17T00:03:00.000000Z",
        policy=RuntimeControlRetentionPolicy(terminal_run_min_age_days=30),
    ).cleanup(dry_run=True)
    assert retention.dry_run is True
    assert runtime_store.list_candidate_identities(runtime_run_id=queued.runtime_run_id)[0].display_name == "Alice Chen"

    erasure = erase_candidate_subject(
        resume_id="resume_1",
        erased_at="2026-06-17T00:04:00.000000Z",
        runtime_control_path=runtime_store.path,
        workbench_path=workbench_store.db_path,
    )

    assert erasure.total_count == 4
    assert runtime_store.list_candidate_identities(runtime_run_id=queued.runtime_run_id)[0].display_name == "Candidate erased"


class _ProductFlowRuntime:
    def __init__(self) -> None:
        self.artifact_read_count = 0

    async def run_async(self, **kwargs: object) -> object:
        runtime_start_callback = kwargs["runtime_start_callback"]
        progress_callback = kwargs["progress_callback"]
        checkpoint_callback = kwargs["runtime_checkpoint_callback"]
        assert callable(runtime_start_callback)
        assert callable(progress_callback)
        assert callable(checkpoint_callback)
        runtime_start_callback("workflow_product_flow")
        progress_callback(_public_progress(stage="source_result", event_seq=1, round_no=1, source_kind="cts"))
        checkpoint_callback(SimpleNamespace(run_state=_product_run_state()))
        progress_callback(_public_progress(stage="finalization", event_seq=2, round_no=None, source_kind=None))
        return _NoArtifactReads(self)


class _NoArtifactReads:
    def __init__(self, runtime: _ProductFlowRuntime) -> None:
        object.__setattr__(self, "_runtime", runtime)

    def __getattr__(self, name: str) -> object:
        self._runtime.artifact_read_count += 1
        raise AssertionError(f"artifact read was not expected: {name}")


def _public_progress(*, stage: str, event_seq: int, round_no: int | None, source_kind: str | None) -> ProgressEvent:
    return ProgressEvent(
        type="runtime_public_event",
        message=f"{stage} completed",
        timestamp=f"2026-06-17T00:01:0{event_seq}+00:00",
        round_no=round_no,
        payload=dict(
            make_runtime_public_event(
                runtime_run_id="workflow_product_flow",
                stage=stage,
                event_seq=event_seq,
                round_no=round_no,
                source_kind=source_kind,
                status="completed",
                counts={"roundReturned": 1} if stage == "source_result" else {"selectedIdentityCount": 1},
                created_at=f"2026-06-17T00:01:0{event_seq}.000000Z",
            )
        ),
    )


def _product_run_state() -> dict[str, object]:
    payload = _run_state_payload()
    revisions = payload["finalization_revisions"]
    assert isinstance(revisions, list)
    revisions[0] = {**dict(revisions[0]), "runtime_run_id": "runtime_run_product_flow"}
    return payload


def _approved_requirement(conversation_id: str) -> ApprovedRequirementRevision:
    return ApprovedRequirementRevision(
        approved_requirement_revision_id="reqapproved_product_flow",
        draft_revision_id=None,
        agent_conversation_id=conversation_id,
        requirement_sheet=RequirementSheet(
            job_title="Data Platform Engineer",
            title_anchor_terms=["Data Platform Engineer"],
            title_anchor_rationale="The role title is explicit.",
            role_summary="Build data products.",
            must_have_capabilities=["Python"],
            preferred_capabilities=["Search"],
            exclusion_signals=[],
            scoring_rationale="Prioritize data platform experience.",
        ),
        selected_item_ids=[],
        deselected_item_ids=[],
        created_at="2026-06-17T00:00:01.000000Z",
    )


def _clock(*values: str):
    items = list(values) or [f"2026-06-17T00:00:{index:02d}.000000Z" for index in range(2, 60)]
    last = items[-1]
    iterator = iter(items)

    def now() -> str:
        nonlocal last
        last = next(iterator, last)
        return last

    return now
