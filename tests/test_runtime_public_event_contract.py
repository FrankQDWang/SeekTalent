from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.source_lanes import build_runtime_source_plan
from seektalent.runtime.source_round_dispatch import SourceRoundAdapterResult, SourceRoundDispatchResult
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings
from tests.test_runtime_state_flow import (
    GenericFallbackScorer,
    SequenceController,
    _install_runtime_stubs,
    _sample_inputs,
)


def test_cts_only_rounds_emit_canonical_runtime_public_events(tmp_path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=progress_events.append))
    finally:
        tracer.close()

    public_events = _runtime_public_event_payloads(progress_events)

    assert [(event["stage"], event["roundNo"], event["sourceKind"]) for event in public_events] == [
        ("round_query", 1, None),
        ("source_dispatch", 1, "cts"),
        ("source_result", 1, "cts"),
        ("merge", 1, None),
        ("scoring", 1, None),
        ("feedback", 1, None),
    ]


def test_cts_only_run_emits_finalization_public_event(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    progress_events = []

    runtime.run(
        job_title="Senior Python Engineer",
        jd="Senior Python Engineer responsible for resume matching workflows.",
        notes="Prefer retrieval experience and shipping production AI features.",
        progress_callback=progress_events.append,
    )

    finalization_events = [
        event for event in _runtime_public_event_payloads(progress_events) if event["stage"] == "finalization"
    ]

    assert [(event["roundNo"], event["sourceKind"]) for event in finalization_events] == [(None, None)]
    assert finalization_events[0]["counts"]["selectedIdentityCount"] > 0


def test_source_round_empty_coverage_does_not_block_next_runtime_step(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    source_plan = build_runtime_source_plan(source_kinds=["cts"], settings=settings, runtime_run_id="run-1")
    dispatch_result = SourceRoundDispatchResult(
        source_results=(
            SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(),
                raw_candidate_count=0,
            ),
        ),
        candidates=(),
        raw_candidate_count=0,
    )

    coverage_summary = runtime._source_coverage_summary_from_dispatch(
        source_plan=source_plan,
        dispatch_result=dispatch_result,
    )

    assert coverage_summary.status == "empty"
    assert coverage_summary.empty_source_kinds == ("cts",)
    assert runtime._source_round_not_ready_reason(
        coverage_summary=coverage_summary,
        dispatch_result=dispatch_result,
    ) is None


def test_source_round_unknown_coverage_status_remains_blocking(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)

    reason = runtime._source_round_not_ready_reason(
        coverage_summary=SimpleNamespace(
            status="unexpected",
            blocked_source_kinds=(),
            failed_source_kinds=(),
            partial_source_kinds=(),
            empty_source_kinds=(),
            missing_source_kinds=(),
        ),
        dispatch_result=SourceRoundDispatchResult(
            source_results=(),
            candidates=(),
            raw_candidate_count=0,
        ),
    )

    assert reason == "source_coverage_unexpected"


def _runtime_public_event_payloads(progress_events: list[object]) -> list[dict[str, object]]:
    return [
        event.payload
        for event in progress_events
        if event.type == "runtime_public_event"
        and event.payload.get("schemaVersion") == "runtime_public_event_v1"
    ]
