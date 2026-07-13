import asyncio
from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Callable, cast

import pytest

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.models import (
    PoolDecision,
    ReflectionAdvice,
    ReflectionContext,
    ScoringFailure,
    RunState,
)
import seektalent.runtime.orchestrator as orchestrator_module
from seektalent.runtime.candidate_intake import (
    normalize_runtime_candidates,
)
from seektalent.runtime.source_round_dispatch import (
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
)
from seektalent.source_contracts.first_page_expansion import (
    SourceFirstPageExpansionRequest,
    SourceFirstPageExpansionResult,
)
from seektalent.runtime.source_round_dispatch import RuntimeSourceInvariantError
from seektalent.runtime.source_lanes import (
    RuntimeQueryCandidateAttribution,
    SourceQueryExecutionOutcome,
    build_runtime_source_plan,
    rebuild_candidate_identities,
)
from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.orchestrator import RuntimeSourceRoundContext
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


from tests.test_runtime_state_flow import (
    SequenceController,
    SequenceReflection,
    StubScorer,
    _detail_open_claim_ledger,
    _install_runtime_stubs,
    _make_candidate,
    _scored_candidate,
    _workflow_runtime,
)


class RecordingExpansionReflection(SequenceReflection):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[ReflectionContext] = []

    async def reflect(self, *, context: ReflectionContext) -> ReflectionAdvice:
        self.contexts.append(context)
        return await super().reflect(context=context)


def test_expansion_candidates_are_scored_and_visible_to_reflection_in_the_same_round(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        provider_name="liepin",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    cast(Any, runtime)._require_live_llm_config = lambda: None
    runtime_any = cast(Any, runtime)
    runtime_any._require_live_llm_config = lambda: None
    reflection = RecordingExpansionReflection()
    runtime_any.reflection_critic = reflection

    def adapters(_runtime: WorkflowRuntime, context: RuntimeSourceRoundContext):
        async def liepin(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            candidates, outcomes, attributions, continuations = [], [], [], []
            for intent in request.source_query_intents_by_source["liepin"]:
                for index in range(intent.requested_count):
                    candidate = _make_candidate(
                        f"baseline-{intent.query_instance_id}-{index}", source_round=context.round_no
                    )
                    candidates.append(candidate)
                    attributions.append(
                        RuntimeQueryCandidateAttribution(
                            source_kind="liepin",
                            query_instance_id=intent.query_instance_id,
                            resume_id=candidate.resume_id,
                            dedup_key=candidate.dedup_key,
                        )
                    )
                outcomes.append(
                    SourceQueryExecutionOutcome(
                        query_instance_id=intent.query_instance_id,
                        status="completed",
                        dispatch_started=True,
                        raw_candidate_count=intent.requested_count,
                        unique_candidate_count=intent.requested_count,
                    )
                )
                continuations.append(
                    ProviderSearchContinuation(
                        kind="first_page_detail_expansion",
                        continuation_id=f"target-{intent.query_instance_id}",
                        opaque_ref=f"artifact://protected/{intent.query_instance_id}",
                        source_kind="liepin",
                        round_no=context.round_no,
                        query_instance_id=intent.query_instance_id,
                        visible_candidate_count=intent.requested_count + 2,
                        eligible_candidate_count=intent.requested_count + 2,
                        initial_opened_count=intent.requested_count,
                    )
                )
            return SourceRoundAdapterResult(
                source="liepin",
                status="completed",
                candidates=tuple(candidates),
                raw_candidate_count=len(candidates),
                query_execution_outcomes=tuple(outcomes),
                candidate_query_attributions=tuple(attributions),
                private_first_page_continuations=tuple(continuations),
            )

        return {"liepin": liepin}

    expanded_ids: list[str] = []

    async def expand(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        if request.action == "discard":
            return SourceFirstPageExpansionResult(
                source_kind="liepin",
                query_instance_id=request.query_instance_id,
                continuation_id=request.continuation_id,
                status="completed",
                first_page_visible_count=request.continuation.visible_candidate_count,
                first_page_eligible_count=request.continuation.eligible_candidate_count,
                initial_opened_count=request.continuation.initial_opened_count,
                continuation_deleted=True,
            )
        resume_id = f"expanded-{request.query_instance_id}"
        expanded_ids.append(resume_id)
        candidate = _make_candidate(resume_id, source_round=request.round_no)
        return SourceFirstPageExpansionResult(
            source_kind="liepin",
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            candidates=(candidate,),
            candidate_query_attributions=(
                RuntimeQueryCandidateAttribution(
                    source_kind="liepin",
                    query_instance_id=request.query_instance_id,
                    resume_id=candidate.resume_id,
                    dedup_key=candidate.dedup_key,
                ),
            ),
            first_page_visible_count=request.continuation.visible_candidate_count,
            first_page_eligible_count=request.continuation.eligible_candidate_count,
            initial_opened_count=request.continuation.initial_opened_count,
            expansion_opened_count=1,
            continuation_deleted=True,
        )

    runtime_any.source_round_adapter_provider = adapters
    runtime_any.source_first_page_expander_provider = lambda _runtime, _ledger: {"liepin": expand}
    artifacts = runtime.run(
        source_kinds=["liepin"], job_title="AI Agent Engineer", jd="Build production agent systems.", notes=""
    )

    assert expanded_ids
    assert artifacts.run_state is not None
    assert set(expanded_ids) <= set(artifacts.run_state.scorecards_by_resume_id)
    assert set(expanded_ids) <= {item.resume_id for context in reflection.contexts for item in context.top_candidates}
    assert all(context.search_observation.raw_candidate_count > 0 for context in reflection.contexts)
    cleanup_audits = list(artifacts.run_dir.glob("rounds/*/retrieval/first_page_continuation_cleanup.json"))
    assert len(cleanup_audits) == 1
    assert json.loads(cleanup_audits[0].read_text(encoding="utf-8")) == {
        "attempted_count": 0,
        "deleted_count": 0,
        "failure_count": 0,
        "safe_reason_codes": [],
    }
    [events_path] = list(artifacts.run_dir.glob("**/events.jsonl"))
    trace_events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    scoring_batches = [
        (item["event_type"], item.get("payload", {}).get("batch_kind"))
        for item in trace_events
        if item["event_type"].startswith("scoring_batch_")
    ]
    assert scoring_batches == [
        ("scoring_batch_started", "baseline"),
        ("scoring_batch_completed", "baseline"),
        ("scoring_batch_started", "first_page_expansion"),
        ("scoring_batch_completed", "first_page_expansion"),
    ]


def test_source_plan_public_payload_does_not_disclose_private_continuation_capability() -> None:
    plan = build_runtime_source_plan(source_kinds=["liepin"], settings=object(), runtime_run_id="run")[0]
    serialized = json.dumps(plan.to_public_payload(), ensure_ascii=False)
    assert "produces_private_first_page_continuations" not in serialized
    assert "continuation" not in serialized
    assert "opaque_ref" not in serialized


def _integrated_expansion_runtime(tmp_path: Path, *, scorer: object | None = None):
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        provider_name="liepin",
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=scorer or StubScorer())
    runtime_any = cast(Any, runtime)
    runtime_any._require_live_llm_config = lambda: None
    actions: list[tuple[str, str]] = []

    def adapters(_runtime: WorkflowRuntime, context: RuntimeSourceRoundContext):
        async def liepin(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            candidates, outcomes, attributions, continuations = [], [], [], []
            for intent in request.source_query_intents_by_source["liepin"]:
                for index in range(intent.requested_count):
                    candidate = _make_candidate(
                        f"matrix-{intent.query_instance_id}-{index}", source_round=context.round_no
                    )
                    candidates.append(candidate)
                    attributions.append(
                        RuntimeQueryCandidateAttribution(
                            source_kind="liepin",
                            query_instance_id=intent.query_instance_id,
                            resume_id=candidate.resume_id,
                            dedup_key=candidate.dedup_key,
                        )
                    )
                outcomes.append(
                    SourceQueryExecutionOutcome(
                        query_instance_id=intent.query_instance_id,
                        status="completed",
                        dispatch_started=True,
                        raw_candidate_count=intent.requested_count,
                        unique_candidate_count=intent.requested_count,
                    )
                )
                continuations.append(
                    ProviderSearchContinuation(
                        kind="first_page_detail_expansion",
                        continuation_id=f"matrix-{intent.query_instance_id}",
                        opaque_ref=f"artifact://protected/{intent.query_instance_id}",
                        source_kind="liepin",
                        round_no=context.round_no,
                        query_instance_id=intent.query_instance_id,
                        visible_candidate_count=intent.requested_count + 2,
                        eligible_candidate_count=intent.requested_count + 2,
                        initial_opened_count=intent.requested_count,
                    )
                )
            return SourceRoundAdapterResult(
                source="liepin",
                status="completed",
                candidates=tuple(candidates),
                raw_candidate_count=len(candidates),
                query_execution_outcomes=tuple(outcomes),
                candidate_query_attributions=tuple(attributions),
                private_first_page_continuations=tuple(continuations),
            )

        return {"liepin": liepin}

    async def expander(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        actions.append((request.continuation_id, request.action))
        if request.action == "expand":
            candidate = _make_candidate(f"expanded-{request.query_instance_id}", source_round=request.round_no)
            return SourceFirstPageExpansionResult(
                source_kind=request.source_kind,
                query_instance_id=request.query_instance_id,
                continuation_id=request.continuation_id,
                status="completed",
                candidates=(candidate,),
                candidate_query_attributions=(
                    RuntimeQueryCandidateAttribution(
                        source_kind=request.source_kind,
                        query_instance_id=request.query_instance_id,
                        resume_id=candidate.resume_id,
                        dedup_key=candidate.dedup_key,
                    ),
                ),
                first_page_visible_count=request.continuation.visible_candidate_count,
                first_page_eligible_count=request.continuation.eligible_candidate_count,
                initial_opened_count=request.continuation.initial_opened_count,
                expansion_opened_count=1,
                continuation_deleted=True,
            )
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            first_page_visible_count=request.continuation.visible_candidate_count,
            first_page_eligible_count=request.continuation.eligible_candidate_count,
            initial_opened_count=request.continuation.initial_opened_count,
            continuation_deleted=True,
        )

    runtime_any.source_round_adapter_provider = adapters
    runtime_any.source_first_page_expander_provider = lambda _runtime, _ledger: {"liepin": expander}
    return runtime, runtime_any, actions


def _matrix_cleanup_audits(tmp_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "runs").glob("**/first_page_continuation_cleanup.json")
    ]


def _run_integrated_round(
    runtime: WorkflowRuntime,
    tmp_path: Path,
    *,
    prepare_run_state: Callable[[RunState], None] | None = None,
    progress_callback: Callable[[Any], None] | None = None,
) -> RunTracer:
    tracer = RunTracer(tmp_path / "trace-runs")
    run_state = asyncio.run(
        runtime._build_run_state(
            job_title="AI Agent Engineer",
            jd="Build agents.",
            notes="",
            tracer=tracer,
        )
    )
    if prepare_run_state is not None:
        prepare_run_state(run_state)
    source_plan = build_runtime_source_plan(
        source_kinds=["liepin"],
        settings=runtime.settings,
        runtime_run_id=tracer.run_id,
    )
    try:
        asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=_detail_open_claim_ledger(run_state),
                tracer=tracer,
                source_plan=source_plan,
                progress_callback=progress_callback,
            )
        )
    except BaseException:
        cast(Any, runtime)._test_last_run_state = run_state
        tracer.close(status="failed")
        raise
    cast(Any, runtime)._test_last_run_state = run_state
    tracer.close(status="completed")
    return tracer


def test_integrated_baseline_scoring_failure_discards_all_and_audits_once(tmp_path: Path) -> None:
    class FailingScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            return [], [
                ScoringFailure(
                    resume_id=contexts[0].normalized_resume.resume_id,
                    branch_id="baseline",
                    round_no=1,
                    attempts=1,
                    error_message="baseline failed",
                )
            ]

    runtime, runtime_any, actions = _integrated_expansion_runtime(tmp_path, scorer=FailingScorer())
    writer_counts = {"query_hits": 0, "flywheel": 0}
    original_hits = runtime_any._write_query_resume_hits
    original_flywheel = runtime_any._record_flywheel_retrieval_rows

    def counted_hits(**kwargs):
        writer_counts["query_hits"] += 1
        return original_hits(**kwargs)

    def counted_flywheel(**kwargs):
        writer_counts["flywheel"] += 1
        return original_flywheel(**kwargs)

    runtime_any._write_query_resume_hits = counted_hits
    runtime_any._record_flywheel_retrieval_rows = counted_flywheel
    with pytest.raises(orchestrator_module.RunStageError):
        _run_integrated_round(runtime, tmp_path)
    assert actions and {action for _, action in actions} == {"discard"}
    audits = [
        json.loads(path.read_text())
        for path in (tmp_path / "trace-runs").glob("**/first_page_continuation_cleanup.json")
    ]
    assert audits == [
        {
            "attempted_count": len(actions),
            "deleted_count": len(actions),
            "failure_count": 0,
            "safe_reason_codes": [],
        }
    ]
    assert writer_counts == {"query_hits": 1, "flywheel": 1}
    assert len(list((tmp_path / "trace-runs").glob("**/query_resume_hits.json"))) == 1
    assert len(list((tmp_path / "trace-runs").glob("**/replay_snapshot.json"))) == 1


def test_integrated_execute_failure_cleanup(tmp_path: Path) -> None:
    runtime, runtime_any, actions = _integrated_expansion_runtime(tmp_path)

    async def broken_execute(**_kwargs):
        raise RuntimeError("execute seam failed")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(orchestrator_module, "execute_first_page_decisions", broken_execute)
    try:
        with pytest.raises(RuntimeError, match="execute seam failed"):
            _run_integrated_round(runtime, tmp_path)
    finally:
        monkeypatch.undo()
    assert actions and {action for _, action in actions} == {"discard"}
    assert len(list((tmp_path / "trace-runs").glob("**/first_page_continuation_cleanup.json"))) == 1


def test_integrated_merge_failure_cleanup(tmp_path: Path) -> None:
    runtime, runtime_any, actions = _integrated_expansion_runtime(tmp_path)
    runtime_any._merge_expansion_candidates = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("merge seam failed"))
    with pytest.raises(RuntimeError, match="merge seam failed"):
        _run_integrated_round(runtime, tmp_path)
    assert len(list((tmp_path / "trace-runs").glob("**/first_page_continuation_cleanup.json"))) == 1
    assert actions


def test_integrated_finalize_failure_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _runtime_any, actions = _integrated_expansion_runtime(tmp_path)
    monkeypatch.setattr(
        orchestrator_module,
        "finalize_round_pool",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("finalize seam failed")),
    )
    with pytest.raises(RuntimeError, match="finalize seam failed"):
        _run_integrated_round(runtime, tmp_path)
    assert actions
    assert len(list((tmp_path / "trace-runs").glob("**/first_page_continuation_cleanup.json"))) == 1


def test_integrated_runtime_cancellation_cleanup_and_reraises_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _runtime_any, actions = _integrated_expansion_runtime(tmp_path)

    async def cancelled(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(orchestrator_module, "execute_first_page_decisions", cancelled)
    with pytest.raises(asyncio.CancelledError):
        _run_integrated_round(runtime, tmp_path)
    assert actions and {action for _, action in actions} == {"discard"}
    assert len(list((tmp_path / "trace-runs").glob("**/first_page_continuation_cleanup.json"))) == 1


def test_integrated_reflection_failure_pending_empty_and_audit_once(tmp_path: Path) -> None:
    runtime, runtime_any, actions = _integrated_expansion_runtime(tmp_path)

    class BrokenReflection:
        async def reflect(self, *, context):
            del context
            raise RuntimeError("reflection seam failed")

    runtime_any.reflection_critic = BrokenReflection()
    with pytest.raises(orchestrator_module.RunStageError, match="reflection seam failed"):
        _run_integrated_round(runtime, tmp_path)
    assert actions
    assert len(list((tmp_path / "trace-runs").glob("**/first_page_continuation_cleanup.json"))) == 1


def test_integrated_cleanup_writer_failure_preserves_primary_exception(tmp_path: Path) -> None:
    class FailingScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            return [], [
                ScoringFailure(
                    resume_id=contexts[0].normalized_resume.resume_id,
                    branch_id="baseline",
                    round_no=1,
                    attempts=1,
                    error_message="primary scoring failure",
                )
            ]

    runtime, runtime_any, actions = _integrated_expansion_runtime(tmp_path, scorer=FailingScorer())
    runtime_any._write_query_resume_hits = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("secondary writer failure")
    )
    with pytest.raises(orchestrator_module.RunStageError) as caught:
        _run_integrated_round(runtime, tmp_path)
    assert "Scoring failed" in str(caught.value)
    assert "secondary writer failure" not in str(caught.value)
    assert actions and {action for _, action in actions} == {"discard"}


def test_integrated_missing_expander_preflight_zero_provider_calls_or_files(tmp_path: Path) -> None:
    runtime, runtime_any, _actions = _integrated_expansion_runtime(tmp_path)
    provider_calls: list[str] = []
    original_provider = runtime_any.source_round_adapter_provider

    def recording_provider(*args, **kwargs):
        provider_calls.append("provider")
        return original_provider(*args, **kwargs)

    runtime_any.source_round_adapter_provider = recording_provider
    runtime_any.source_first_page_expander_provider = lambda _runtime, _ledger: {}
    with pytest.raises(RuntimeSourceInvariantError, match="first_page_expander_unavailable"):
        _run_integrated_round(runtime, tmp_path)
    assert provider_calls == []
    assert list(tmp_path.glob("**/*protected*")) == []


def test_integrated_baseline_and_expansion_scorecard_jsonl_batches(tmp_path: Path) -> None:
    runtime, _runtime_any, actions = _integrated_expansion_runtime(tmp_path)
    tracer = _run_integrated_round(runtime, tmp_path)
    [path] = list(tracer.run_dir.glob("rounds/*/scoring/scorecards.jsonl"))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert {row["batch_kind"] for row in rows} == {"baseline", "first_page_expansion"}
    assert sum(row["batch_kind"] == "first_page_expansion" for row in rows) == 1
    [input_path] = list(tracer.run_dir.glob("rounds/*/scoring/scoring_input_refs.jsonl"))
    input_rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line]
    assert {(row["resume_id"], row["batch_kind"]) for row in input_rows} == {
        (row["resume_id"], row["batch_kind"]) for row in rows
    }
    assert len(input_rows) == len({row["resume_id"] for row in input_rows})
    assert all(row["normalized_resume_ref"].endswith(f"{row['resume_id']}.json") for row in input_rows)
    assert any(action == "expand" for _, action in actions)


def test_integrated_public_events_exact_counts_no_private_sentinels(tmp_path: Path) -> None:
    runtime, _runtime_any, _actions = _integrated_expansion_runtime(tmp_path)
    tracer = _run_integrated_round(runtime, tmp_path)
    [path] = list(tracer.run_dir.glob("runtime/public_events.jsonl"))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    [event] = [row for row in rows if row.get("stage") == "first_page_expansion"]
    assert event["counts"] == {
        "qualifiedLaneCount": 1,
        "expandedCandidateCount": 1,
        "skippedSeenCount": 0,
        "terminalFailureCount": 0,
        "scoringFailureCount": 0,
    }
    serialized = json.dumps(event)
    assert "artifact://protected" not in serialized
    assert "continuation" not in serialized.lower()
    assert "providerCandidateId" not in serialized


def test_integrated_non_liepin_no_expander_calls_or_files(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        provider_name="cts",
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    cast(Any, runtime)._require_live_llm_config = lambda: None
    calls: list[str] = []

    async def forbidden_expander(_request):
        calls.append("transport")
        raise AssertionError("non-Liepin expansion transport must not run")

    cast(Any, runtime).source_first_page_expander_provider = lambda _runtime, _ledger: {"cts": forbidden_expander}
    artifacts = runtime.run(
        source_kinds=["cts"],
        job_title="AI Agent Engineer",
        jd="Build production agents.",
        notes="",
    )
    assert artifacts.run_state is not None
    assert calls == []
    assert list(tmp_path.glob("**/*protected*")) == []


def test_integrated_fail_fast_partial_success_scorecards_persist(tmp_path: Path) -> None:
    class PartialBaselineScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            success_id = contexts[0].normalized_resume.resume_id
            failed_id = contexts[1].normalized_resume.resume_id
            return [_scored_candidate(success_id)], [
                ScoringFailure(
                    resume_id=failed_id,
                    branch_id="baseline-partial",
                    round_no=1,
                    attempts=1,
                    error_message="second candidate failed",
                )
            ]

    runtime, _runtime_any, _actions = _integrated_expansion_runtime(tmp_path, scorer=PartialBaselineScorer())
    with pytest.raises(orchestrator_module.RunStageError):
        _run_integrated_round(runtime, tmp_path)
    [path] = list((tmp_path / "trace-runs").glob("**/scorecards.jsonl"))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["batch_kind"] == "baseline"
    [input_path] = list((tmp_path / "trace-runs").glob("**/scoring_input_refs.jsonl"))
    input_rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(input_rows) > len(rows)
    assert sum(row["resume_id"] == rows[0]["resume_id"] for row in input_rows) == 1
    assert all(row["batch_kind"] == "baseline" for row in input_rows)


def test_integrated_partial_applicability_failure_continues_and_reaches_reflection(
    tmp_path: Path,
) -> None:
    class PartialApplicabilityScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            scored = [_scored_candidate(contexts[0].normalized_resume.resume_id)]
            if len(contexts) == 1:
                return scored, []
            return scored, [
                ScoringFailure(
                    resume_id=contexts[1].normalized_resume.resume_id,
                    branch_id="baseline-applicability",
                    round_no=1,
                    attempts=1,
                    error_message=(
                        "scoring applicability output retries exhausted: "
                        "risk_score_not_applicable"
                    ),
                    failure_kind="score_applicability_error",
                )
            ]

    runtime, runtime_any, _actions = _integrated_expansion_runtime(
        tmp_path,
        scorer=PartialApplicabilityScorer(),
    )
    reflection = RecordingExpansionReflection()
    runtime_any.reflection_critic = reflection
    progress_events: list[Any] = []

    tracer = _run_integrated_round(
        runtime,
        tmp_path,
        progress_callback=progress_events.append,
    )

    run_state = runtime_any._test_last_run_state
    [round_state] = run_state.round_history
    assert len(run_state.scorecards_by_resume_id) >= 1
    assert [failure.failure_kind for failure in round_state.scoring_failures] == [
        "score_applicability_error"
    ]
    assert reflection.contexts[0].scoring_failures == round_state.scoring_failures
    [events_path] = list(tracer.run_dir.glob("**/events.jsonl"))
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    baseline_completed = next(
        event
        for event in events
        if event["event_type"] == "scoring_batch_completed"
        and event["payload"]["batch_kind"] == "baseline"
    )
    assert baseline_completed["status"] == "partial"
    scoring_completed = next(event for event in progress_events if event.type == "scoring_completed")
    assert scoring_completed.payload["scoring_failure_count"] == 1
    [public_events_path] = list(tracer.run_dir.glob("runtime/public_events.jsonl"))
    public_events = [
        json.loads(line)
        for line in public_events_path.read_text(encoding="utf-8").splitlines()
    ]
    scoring_public_event = next(event for event in public_events if event["stage"] == "scoring")
    assert scoring_public_event["counts"]["scoringFailureCount"] == 1


def test_integrated_whole_batch_applicability_failure_still_aborts(
    tmp_path: Path,
) -> None:
    class ApplicabilityFailingScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            return [], [
                ScoringFailure(
                    resume_id=context.normalized_resume.resume_id,
                    branch_id=f"applicability-{index}",
                    round_no=1,
                    attempts=1,
                    error_message=(
                        "scoring applicability output retries exhausted: "
                        "risk_score_not_applicable"
                    ),
                    failure_kind="score_applicability_error",
                )
                for index, context in enumerate(contexts)
            ]

    runtime, _runtime_any, _actions = _integrated_expansion_runtime(
        tmp_path,
        scorer=ApplicabilityFailingScorer(),
    )

    with pytest.raises(orchestrator_module.RunStageError, match="score_applicability_error"):
        _run_integrated_round(runtime, tmp_path)


def test_integrated_dual_lane_completed_partial_receipts_observation_reflection(tmp_path: Path) -> None:
    class DualScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            scored, failures = [], []
            for context in contexts:
                resume_id = context.normalized_resume.resume_id
                if resume_id.startswith("partial-expanded-"):
                    failures.append(
                        ScoringFailure(
                            resume_id=resume_id,
                            branch_id="partial-expansion",
                            round_no=1,
                            attempts=1,
                            error_message="partial expansion scoring failure",
                        )
                    )
                else:
                    scored.append(_scored_candidate(resume_id))
            return scored, failures

    runtime, runtime_any, actions = _integrated_expansion_runtime(tmp_path, scorer=DualScorer())
    reflection = RecordingExpansionReflection()
    runtime_any.reflection_critic = reflection
    original_bundle = runtime_any._build_round_query_bundle

    def dual_bundle(**kwargs):
        states, decision = original_bundle(**kwargs)
        first = states[0]
        second = replace(
            first,
            query_role="explore",
            lane_type="generic_explore",
            query_instance_id=f"{first.query_instance_id}-partial",
            query_fingerprint=f"{first.query_fingerprint}-partial",
            identity=replace(
                first.identity,
                term_group_key=f"{first.identity.term_group_key}-partial",
                non_anchor_term_family_ids=("partial-family",),
            ),
        )
        return [first, second], decision

    runtime_any._build_round_query_bundle = dual_bundle
    registry = runtime_any.source_first_page_expander_provider(runtime, None)
    original = registry["liepin"]

    async def partial(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        if request.action == "discard":
            return await original(request)
        if not request.query_instance_id.endswith("-partial"):
            return await original(request)
        actions.append((request.continuation_id, request.action))
        candidate = _make_candidate(f"partial-expanded-{request.query_instance_id}", source_round=1)
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="partial",
            candidates=(candidate,),
            candidate_query_attributions=(
                RuntimeQueryCandidateAttribution(
                    source_kind=request.source_kind,
                    query_instance_id=request.query_instance_id,
                    resume_id=candidate.resume_id,
                    dedup_key=candidate.dedup_key,
                ),
            ),
            first_page_visible_count=request.continuation.visible_candidate_count,
            first_page_eligible_count=request.continuation.eligible_candidate_count,
            initial_opened_count=request.continuation.initial_opened_count,
            expansion_opened_count=1,
            expansion_skipped_seen_count=1,
            safe_reason_code="first_page_expansion_partial",
            continuation_deleted=True,
        )

    runtime_any.source_first_page_expander_provider = lambda _runtime, _ledger: {"liepin": partial}
    tracer = _run_integrated_round(runtime, tmp_path)
    assert reflection.contexts
    context = reflection.contexts[0]
    assert context.search_observation.exhausted_reason == "first_page_expansion_partial"
    assert len(context.query_outcomes) == 2
    completed, partial_outcome = context.query_outcomes
    assert completed.receipts[0].first_page_expansion_status == "completed"
    assert partial_outcome.receipts[0].first_page_expansion_status == "partial"
    assert partial_outcome.receipts[0].expansion_scoring_failure_count == 1
    assert partial_outcome.receipts[0].expansion_skipped_seen_count == 1
    assert completed.receipts[0].expansion_scoring_failure_count == 0
    assert tracer.run_dir.exists()


def test_integrated_alias_bridge_final_identity_counts(tmp_path: Path) -> None:
    runtime, runtime_any, _actions = _integrated_expansion_runtime(tmp_path)
    captured_decisions: list[PoolDecision] = []
    original_selector = orchestrator_module.select_qualified_first_page_expansions
    original_rebuild = orchestrator_module.rebuild_candidate_identities

    def bridge_rebuild(run_state, *, source_order):
        original_rebuild(run_state, source_order=source_order)
        winner = next((item for item in run_state.seen_resume_ids if item.startswith("z-winner-")), None)
        if winner is None or "prior-top" not in run_state.candidate_identity_by_resume_id:
            return
        identity_id = run_state.candidate_identity_by_resume_id["prior-top"]
        bridged_resumes = [item for item in run_state.seen_resume_ids if item.startswith("matrix-")][:2]
        for resume_id in (*bridged_resumes, winner):
            run_state.candidate_identity_by_resume_id[resume_id] = identity_id
        identity = run_state.candidate_identities[identity_id]
        run_state.candidate_identities[identity_id] = identity.model_copy(
            update={"resume_ids": sorted(set((*identity.resume_ids, *bridged_resumes, winner)))}
        )
        run_state.canonical_resume_by_identity_id[identity_id] = run_state.canonical_resume_by_identity_id[
            identity_id
        ].model_copy(update={"canonical_resume_id": winner})
        if "prior-top" in run_state.scorecards_by_resume_id:
            run_state.scorecards_by_resume_id[winner] = run_state.scorecards_by_resume_id["prior-top"].model_copy(
                update={"resume_id": winner}
            )

    orchestrator_module.rebuild_candidate_identities = bridge_rebuild

    def force_expansion(**kwargs):
        return [
            replace(item, expand=True, reason_code="baseline_quality_gate_passed")
            for item in original_selector(**kwargs)
        ]

    orchestrator_module.select_qualified_first_page_expansions = force_expansion
    original_reflection_stage = orchestrator_module.reflection_runtime.run_reflection_stage

    async def capture_reflection(**kwargs):
        captured_decisions.extend(kwargs["pool_decisions"])
        return await original_reflection_stage(**kwargs)

    orchestrator_module.reflection_runtime.run_reflection_stage = capture_reflection

    def seed_prior(run_state: RunState) -> None:
        prior = _make_candidate(
            "prior-top",
            source_round=0,
            raw={
                "resume_id": "prior-top",
                "candidate_name": "Prior Person",
            },
        ).model_copy(update={"work_experience_summaries": ["Prior Unique Co | Architect | 2012-2020"]})
        run_state.candidate_store[prior.resume_id] = prior
        run_state.seen_resume_ids.append(prior.resume_id)
        normalize_runtime_candidates(run_state=run_state, candidates=[prior], round_no=0, tracer=None)
        rebuild_candidate_identities(run_state, source_order={"liepin": 0})
        run_state.scorecards_by_resume_id[prior.resume_id] = _scored_candidate(prior.resume_id, source_round=0)
        run_state.top_pool_ids = [prior.resume_id]

    async def alias_expander(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        if request.action == "discard":
            return SourceFirstPageExpansionResult(
                source_kind=request.source_kind,
                query_instance_id=request.query_instance_id,
                continuation_id=request.continuation_id,
                status="completed",
                first_page_visible_count=request.continuation.visible_candidate_count,
                first_page_eligible_count=request.continuation.eligible_candidate_count,
                initial_opened_count=request.continuation.initial_opened_count,
                continuation_deleted=True,
            )
        candidate = _make_candidate(f"z-winner-{request.query_instance_id}", source_round=1).model_copy(
            update={
                "dedup_key": f"matrix-{request.query_instance_id}-0",
                "raw": {
                    "resume_id": f"z-winner-{request.query_instance_id}",
                    "candidate_name": "Prior Person",
                },
                "work_experience_summaries": ["Prior Unique Co | Architect | 2012-2020"],
            }
        )
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            candidates=(candidate,),
            candidate_query_attributions=(
                RuntimeQueryCandidateAttribution(
                    source_kind=request.source_kind,
                    query_instance_id=request.query_instance_id,
                    resume_id=candidate.resume_id,
                    dedup_key=candidate.dedup_key,
                ),
            ),
            first_page_visible_count=request.continuation.visible_candidate_count,
            first_page_eligible_count=request.continuation.eligible_candidate_count,
            initial_opened_count=request.continuation.initial_opened_count,
            expansion_opened_count=1,
            continuation_deleted=True,
        )

    runtime_any.source_first_page_expander_provider = lambda _runtime, _ledger: {"liepin": alias_expander}
    try:
        _run_integrated_round(runtime, tmp_path, prepare_run_state=seed_prior)
    finally:
        orchestrator_module.reflection_runtime.run_reflection_stage = original_reflection_stage
        orchestrator_module.select_qualified_first_page_expansions = original_selector
        orchestrator_module.rebuild_candidate_identities = original_rebuild
    state = runtime_any._test_last_run_state
    summary = state.latest_canonical_intake_summary
    assert summary is not None
    assert summary.identity_count == len(set(state.candidate_identity_by_resume_id.values()))
    assert summary.auto_merged_duplicate_count >= 1
    prior_identity = state.candidate_identity_by_resume_id["prior-top"]
    winner = state.canonical_resume_by_identity_id[prior_identity].canonical_resume_id
    assert winner.startswith("z-winner-")
    assert state.top_pool_ids.count(winner) == 1
    winner_decision = next(item for item in captured_decisions if item.resume_id == winner)
    assert winner_decision.decision == "retained"
    assert not any(item.resume_id == "prior-top" and item.decision == "dropped" for item in captured_decisions)
