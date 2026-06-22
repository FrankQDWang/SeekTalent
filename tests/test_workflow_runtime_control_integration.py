from __future__ import annotations

import asyncio
from pathlib import Path

from seektalent.models import QueryTermCandidate, RequirementSheet
from seektalent.progress import ProgressEvent
from seektalent.runtime.public_events import make_runtime_public_event


def test_executor_progress_callback_persists_public_events_and_stage_outputs_without_artifact_reads(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    runtime = PublicProgressRuntime()
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: runtime,
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "executor_1",
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
            "2026-06-17T00:00:10.000000Z",
        ),
    )

    run = asyncio.run(
        executor.start_workflow(
            conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement=_approved_requirement(),
            job_title="Senior Python Engineer",
            jd_text="Build search systems.",
            notes=None,
            source_ids=["cts"],
        )
    )

    assert run.status == "completed"
    assert runtime.artifact_read_count == 0
    assert store.get_latest_checkpoint(runtime_run_id="runtime_run_1") is None

    events = store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=20).events
    assert [event.event_type for event in events] == [
        "runtime_run_queued",
        "runtime_worker_claimed",
        "runtime_executor_starting",
        "runtime_executor_started",
        "runtime_round_source_result",
        "runtime_round_merge_completed",
        "runtime_round_scoring_completed",
        "runtime_finalization_completed",
        "runtime_run_completed",
    ]

    public_events = store.list_public_events(runtime_run_id="runtime_run_1", after_seq=0, limit=20).events
    assert [event.event_type for event in public_events] == [
        "runtime_round_source_result",
        "runtime_round_merge_completed",
        "runtime_round_scoring_completed",
        "runtime_finalization_completed",
    ]
    assert [event.idempotency_key for event in public_events] == [
        "runtime_run_1:1:source_result:cts",
        "runtime_run_1:1:merge:all",
        "runtime_run_1:1:scoring:all",
        "runtime_run_1:final:finalization:all",
    ]
    assert [event.payload["runtimeRunId"] for event in public_events] == [
        "runtime_run_1",
        "runtime_run_1",
        "runtime_run_1",
        "runtime_run_1",
    ]
    assert [event.payload["eventId"] for event in public_events] == [
        "runtime_run_1:1:source_result:cts",
        "runtime_run_1:1:merge:all",
        "runtime_run_1:1:scoring:all",
        "runtime_run_1:final:finalization:all",
    ]
    assert public_events[0].payload["counts"] == {
        "roundReturned": 5,
        "roundIdentities": 4,
        "sourceCumulativeReturned": 5,
        "sourceCumulativeIdentities": 4,
    }
    assert "provider" not in str(public_events)
    assert "resume" not in str(public_events)
    assert "rawStructuredOutput" not in str(public_events)

    stage_outputs = store.list_stage_outputs(runtime_run_id="runtime_run_1")
    assert [(output.stage, output.output_kind, output.round_no, output.node_id) for output in stage_outputs] == [
        ("source_result", "runtime_public_source_result", 1, "cts"),
        ("merge", "runtime_public_merge", 1, None),
        ("scoring", "runtime_public_scoring", 1, None),
        ("finalization", "runtime_public_finalization", None, None),
    ]
    assert stage_outputs[0].source_event_id == public_events[0].event_id
    assert stage_outputs[0].output["counts"]["roundReturned"] == 5
    assert stage_outputs[1].output["counts"]["mergedIdentities"] == 4
    assert stage_outputs[2].output["counts"]["topPoolCount"] == 3
    assert stage_outputs[3].output["counts"]["selectedIdentityCount"] == 2
    assert all(output.artifact_ref_id is None for output in stage_outputs)


def test_executor_applies_next_round_requirement_at_runtime_round_boundary(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    command_service = RuntimeCommandService(
        store=store,
        requirement_extractor=BoundaryRequirementExtractor(),
        amendment_id_factory=lambda: "reqamend_boundary",
        approved_requirement_id_factory=lambda: "reqapproved_boundary",
        now=_clock(
            "2026-06-17T00:00:01.000000Z",
            "2026-06-17T00:00:02.000000Z",
            "2026-06-17T00:00:03.000000Z",
            "2026-06-17T00:00:04.000000Z",
            "2026-06-17T00:00:05.000000Z",
        ),
    )
    runtime = BoundaryCallbackRuntime(command_service)
    approved_requirement = _approved_requirement()
    store.save_approved_requirement(approved_requirement, idempotency_key="approved-boundary")
    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=lambda: runtime,
        runtime_run_id_factory=lambda: "runtime_run_1",
        executor_id_factory=lambda: "executor_1",
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
        ),
        command_service=command_service,
    )

    run = asyncio.run(
        executor.start_workflow(
            conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement=approved_requirement,
            job_title="Senior Python Engineer",
            jd_text="Build search systems.",
            notes=None,
            source_ids=["cts"],
        )
    )

    assert run.status == "completed"
    assert runtime.boundary_sheet is not None
    assert runtime.boundary_sheet.must_have_capabilities == ["Python", "Kafka 生产经验"]
    assert store.get_run("runtime_run_1").approved_requirement_revision_id == "reqapproved_boundary"
    assert store.get_requirement_amendment("reqamend_boundary").status == "applied"


class PublicProgressRuntime:
    def __init__(self) -> None:
        self.artifact_read_count = 0

    async def run_async(self, **kwargs: object) -> object:
        progress_callback = kwargs["progress_callback"]
        runtime_start_callback = kwargs["runtime_start_callback"]
        assert callable(progress_callback)
        assert callable(runtime_start_callback)
        runtime_start_callback("workflow_run_1")
        for event in self._progress_events():
            progress_callback(event)
        return ArtifactReadTrap(self)

    def _progress_events(self) -> list[ProgressEvent]:
        source_result = _public_progress(
            stage="source_result",
            event_seq=1,
            round_no=1,
            source_kind="cts",
            counts={
                "roundReturned": 5,
                "roundIdentities": 4,
                "sourceCumulativeReturned": 5,
                "sourceCumulativeIdentities": 4,
            },
        )
        source_result_duplicate = ProgressEvent(
            type="runtime_public_event",
            message="CTS completed duplicate",
            timestamp="2026-06-17T00:00:04+00:00",
            round_no=1,
            payload=dict(source_result.payload),
        )
        return [
            source_result,
            source_result_duplicate,
            _public_progress(
                stage="merge",
                event_seq=2,
                round_no=1,
                source_kind=None,
                counts={"mergedIdentities": 4},
            ),
            _public_progress(
                stage="scoring",
                event_seq=3,
                round_no=1,
                source_kind=None,
                counts={"topPoolCount": 3},
            ),
            _public_progress(
                stage="finalization",
                event_seq=4,
                round_no=None,
                source_kind=None,
                counts={"selectedIdentityCount": 2},
            ),
        ]


class BoundaryCallbackRuntime:
    def __init__(self, command_service) -> None:  # type: ignore[no-untyped-def]
        self.command_service = command_service
        self.boundary_sheet: RequirementSheet | None = None

    async def run_async(self, **kwargs: object) -> object:
        runtime_start_callback = kwargs["runtime_start_callback"]
        runtime_round_boundary_callback = kwargs["runtime_round_boundary_callback"]
        assert callable(runtime_start_callback)
        assert callable(runtime_round_boundary_callback)
        runtime_start_callback("workflow_run_1")
        self.command_service.submit_next_round_requirement(
            runtime_run_id="runtime_run_1",
            text="下一轮必须补充 Kafka 生产经验",
            target_section_hint="must_have_capabilities",
            idempotency_key="amend-boundary",
        )
        self.boundary_sheet = runtime_round_boundary_callback(1)
        return object()


class BoundaryRequirementExtractor:
    def extract_requirements(self, *, job_title: str | None, jd_text: str, notes: str | None) -> RequirementSheet:
        del jd_text, notes
        return RequirementSheet(
            job_title=job_title or "Senior Python Engineer",
            title_anchor_terms=["Python Engineer"],
            title_anchor_rationale="Use the current runtime title.",
            role_summary="Supplemental runtime requirement.",
            must_have_capabilities=["Kafka 生产经验"],
            initial_query_term_pool=[
                QueryTermCandidate(
                    term="Kafka",
                    source="notes",
                    category="tooling",
                    priority=95,
                    evidence="User added Kafka production experience.",
                    first_added_round=0,
                )
            ],
            scoring_rationale="Supplement with Kafka production experience.",
        )


class ArtifactReadTrap:
    def __init__(self, runtime: PublicProgressRuntime) -> None:
        object.__setattr__(self, "_runtime", runtime)

    def __getattr__(self, name: str) -> object:
        self._runtime.artifact_read_count += 1
        raise AssertionError(f"artifact read was not expected: {name}")


def _public_progress(
    *,
    stage: str,
    event_seq: int,
    round_no: int | None,
    source_kind: str | None,
    counts: dict[str, int],
) -> ProgressEvent:
    payload = dict(
        make_runtime_public_event(
            runtime_run_id="workflow_run_1",
            stage=stage,
            event_seq=event_seq,
            round_no=round_no,
            source_kind=source_kind,
            status="completed",
            counts=counts,
            created_at=f"2026-06-17T00:00:0{event_seq + 2}.000000Z",
        )
    )
    payload.update(
        {
            "provider": "raw provider payload",
            "resume": {"text": "raw resume"},
            "rawStructuredOutput": {"unsafe": True},
        }
    )
    return ProgressEvent(
        type="runtime_public_event",
        message=f"{stage} completed",
        timestamp=f"2026-06-17T00:00:0{event_seq + 2}+00:00",
        round_no=round_no,
        payload=payload,
    )


def _approved_requirement():
    from seektalent_runtime_control.requirements import ApprovedRequirementRevision

    return ApprovedRequirementRevision(
        approved_requirement_revision_id="reqapproved_1",
        draft_revision_id="reqdraft_1",
        agent_conversation_id="agent_conv_1",
        requirement_sheet=RequirementSheet(
            job_title="Senior Python Engineer",
            title_anchor_terms=["Python Engineer"],
            title_anchor_rationale="Title is explicit.",
            role_summary="Build search systems.",
            must_have_capabilities=["Python"],
            scoring_rationale="Relevant experience.",
        ),
        selected_item_ids=["item_1"],
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
