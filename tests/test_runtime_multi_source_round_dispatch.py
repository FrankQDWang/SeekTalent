from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from seektalent.core.retrieval.provider_contract import ProviderSearchError, ProviderSnapshot, SearchResult
from seektalent.corpus.store import DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_ID, CorpusStore
from seektalent.models import (
    CTSQuery,
    InputTruth,
    LocationExecutionPlan,
    ProposedFilterPlan,
    QueryResumeHit,
    RequirementSheet,
    ResumeCandidate,
    RuntimeCandidateIdentity,
    RetrievalState,
    RoundRetrievalPlan,
    RunState,
    RuntimeSourceEvidence,
    RuntimeSourceCoverageSummary,
    ScoredCandidate,
    ScoringPolicy,
    SearchAttempt,
    SearchObservation,
    SentQueryRecord,
    StopGuidance,
)
from seektalent.storage.json import sha256_json
from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.orchestrator import RunStageError, RuntimeSourceRoundContext
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch, build_logical_query_dispatches
from seektalent.runtime.rescue_router import RescueInputs, choose_rescue_lane
from seektalent.runtime.retrieval_runtime import (
    LogicalQueryState,
    RetrievalExecutionResult,
    RetrievalRuntime,
    allocate_initial_lane_targets,
)
from seektalent.source_adapters import default_source_query_policies, _run_cts_source_round, _run_liepin_source_round
from seektalent.runtime.source_round_dispatch import (
    RuntimeSourceInvariantError,
    SourceProviderFailed,
    SourceRoundAdapterResult,
    SourceRoundDispatchResult,
    SourceRoundDispatchRequest,
    dispatch_source_rounds,
)
from seektalent.runtime.source_lanes import RuntimeSourceBudgetPolicy, RuntimeSourceLanePlan, RuntimeSourceLaneResult
from seektalent.runtime.source_query_intent import RuntimeSourceQueryPolicy, build_runtime_source_query_intents
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


def test_retrieval_runtime_does_not_import_provider_modules() -> None:
    source = Path("src/seektalent/runtime/retrieval_runtime.py").read_text(encoding="utf-8")

    assert "seektalent.providers" not in source


def test_orchestrator_does_not_construct_cts_queries_directly() -> None:
    source = Path("src/seektalent/runtime/orchestrator.py").read_text(encoding="utf-8")

    assert "CTSQuery(" not in source


def test_orchestrator_does_not_import_provider_registry_for_retrieval_service() -> None:
    source = Path("src/seektalent/runtime/orchestrator.py").read_text(encoding="utf-8")

    assert "from seektalent.providers import get_provider_adapter" not in source


def _query_state(lane_type: str) -> LogicalQueryState:
    return LogicalQueryState(
        query_role="exploit" if lane_type == "exploit" else "explore",
        lane_type=lane_type,
        query_terms=["数据开发", lane_type],
        keyword_query=f"数据开发 {lane_type}",
        query_instance_id=f"query-{lane_type}",
        query_fingerprint=f"fingerprint-{lane_type}",
        term_group_key=f"term-group-{lane_type}",
    )


def _candidate(resume_id: str, source: str) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        dedup_key=f"dedup-{source}-{resume_id}",
        search_text="数据开发专家",
        raw={"source": source, "safe_summary_ref": f"artifact://public-summary/{resume_id}"},
    )


def test_liepin_filter_partial_reason_is_public_safe() -> None:
    from seektalent.source_adapters import public_source_reason_code

    assert public_source_reason_code("source_location_filter_partial") == "source_filter_partial"
    assert public_source_reason_code("source_filter_applied") == "source_filter_applied"
    assert public_source_reason_code("source_filter_unavailable") == "source_filter_unavailable"
    assert public_source_reason_code("source_browser_backend_unavailable") == "source_browser_backend_unavailable"
    assert public_source_reason_code("liepin_opencli_filter_unapplied") == "source_filter_unavailable"
    assert public_source_reason_code("liepin_opencli_search_input_unapplied") == "source_browser_backend_unavailable"


def test_public_runtime_filter_payload_does_not_expose_browser_terms() -> None:
    from seektalent.runtime.public_events import make_runtime_public_event

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=1,
        round_no=1,
        source_kind="liepin",
        status="partial",
        counts={"roundReturned": 1},
        safe_reason_code="source_location_filter_partial",
    )
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True)

    forbidden = (
        "OpenCLI",
        "DokoBot",
        "mcp",
        "pi_agent",
        "cookie",
        "authorization",
        "raw_provider_payload",
        "raw_resume",
    )
    assert all(term.lower() not in encoded.lower() for term in forbidden)


def _dispatch(lane_type: str, requested_count: int) -> LogicalQueryDispatch:
    return LogicalQueryDispatch(
        round_no=1,
        query_role="exploit" if lane_type == "exploit" else "explore",
        lane_type=lane_type,
        query_terms=("数据开发", lane_type),
        keyword_query=f"数据开发 {lane_type}",
        query_instance_id=f"query-{lane_type}",
        query_fingerprint=f"fingerprint-{lane_type}",
        term_group_key=f"term-group-{lane_type}",
        requested_count=requested_count,
        source_plan_version="2",
    )


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=("AI Agent",),
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agentic retrieval workflows.",
        must_have_capabilities=("LangGraph", "RAG"),
        preferred_capabilities=("evaluation",),
        exclusion_signals=("pure frontend",),
        hard_constraints={},
        preferences={"preferred_query_terms": ["LangGraph", "RAG"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize agent workflow and retrieval evidence.",
    )


def _run_state() -> RunState:
    requirement_sheet = _requirement_sheet()
    return RunState(
        input_truth=InputTruth(
            job_title=requirement_sheet.job_title,
            jd="Build agentic retrieval workflows.",
            notes="",
            job_title_sha256="job-title",
            jd_sha256="jd",
            notes_sha256="notes",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=list(requirement_sheet.must_have_capabilities),
            preferred_capabilities=list(requirement_sheet.preferred_capabilities),
            exclusion_signals=list(requirement_sheet.exclusion_signals),
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(),
    )


def test_source_query_intents_keep_cts_10_and_cap_liepin_to_2_plus_1() -> None:
    dispatches = (
        _dispatch("exploit", 7),
        _dispatch("generic_explore", 3),
    )

    intents = build_runtime_source_query_intents(
        source_kinds=("cts", "liepin"),
        logical_dispatches=dispatches,
        filter_intents=(),
        location_intent=None,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(),
        source_query_policy={
            "liepin": RuntimeSourceQueryPolicy(
                requested_count_caps_by_lane={"exploit": 2, "generic_explore": 1},
                provider_scan_multiplier=3,
                provider_scan_cap=30,
            )
        },
    )

    assert [item.requested_count for item in intents["cts"]] == [7, 3]
    assert [item.requested_count for item in intents["liepin"]] == [2, 1]


def test_first_round_liepin_uses_exploit_only_budget() -> None:
    intents = build_runtime_source_query_intents(
        source_kinds=("liepin",),
        logical_dispatches=(_dispatch("exploit", 7),),
        filter_intents=(),
        location_intent=None,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(),
        source_query_policy={
            "liepin": RuntimeSourceQueryPolicy(
                requested_count_caps_by_lane={"exploit": 2, "generic_explore": 1},
                provider_scan_multiplier=3,
                provider_scan_cap=30,
            )
        },
    )

    assert [(item.lane_type, item.requested_count) for item in intents["liepin"]] == [("exploit", 2)]


def test_runtime_multi_source_round_uses_adapter_query_policy_for_liepin(tmp_path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        liepin_worker_mode="disabled",
        liepin_exploit_detail_target=2,
        liepin_explore_detail_target=1,
        liepin_opencli_max_cards_per_task=20,
    )
    observed: dict[str, list[tuple[str, int, int]]] = {}

    def source_round_adapters(runtime: WorkflowRuntime, context: RuntimeSourceRoundContext):
        del runtime

        def adapter_for(source: str):
            async def adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
                observed[source] = [
                    (intent.lane_type, intent.requested_count, intent.provider_scan_limit)
                    for intent in request.source_query_intents_by_source[source]
                ]
                return SourceRoundAdapterResult(source=source, status="completed")

            return adapter

        return {source: adapter_for(source) for source in context.source_plan_by_source}

    runtime = WorkflowRuntime(
        settings,
        source_round_adapter_provider=source_round_adapters,
        source_query_policy_provider=lambda source_plan: default_source_query_policies(
            settings=settings,
            source_plan=source_plan,
        ),
    )
    tracer = RunTracer(tmp_path / "trace")
    try:
        asyncio.run(
            runtime._execute_multi_source_round_search(
                round_no=1,
                retrieval_plan=_retrieval_plan(),
                proposed_filter_plan=ProposedFilterPlan(),
                query_states=(_query_state("exploit"), _query_state("generic_explore")),
                adapter_notes=(),
                target_new=10,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                run_state=_run_state(),
                source_plan=(
                    RuntimeSourceLanePlan(
                        source_plan_id="run-1:source:0:cts",
                        runtime_run_id="run-1",
                        source="cts",
                        label="CTS",
                    ),
                    RuntimeSourceLanePlan(
                        source_plan_id="run-1:source:1:liepin",
                        runtime_run_id="run-1",
                        source="liepin",
                        label="Liepin",
                    ),
                ),
                source_context={"status": "ready"},
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert observed["cts"] == [("exploit", 7, 7), ("generic_explore", 3, 3)]
    assert observed["liepin"] == [("exploit", 2, 6), ("generic_explore", 1, 3)]


def _retrieval_plan() -> RoundRetrievalPlan:
    return RoundRetrievalPlan(
        plan_version=2,
        round_no=1,
        query_terms=["数据开发"],
        keyword_query="数据开发",
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="none",
            allowed_locations=[],
            preferred_locations=[],
            priority_order=[],
            balanced_order=[],
            rotation_offset=0,
            target_new=10,
        ),
        target_new=10,
        rationale="dispatch regression",
    )


def _source_round_context(
    *,
    source_plan: RuntimeSourceLanePlan,
    tracer: RunTracer,
    source_context: dict[str, str] | None = None,
) -> RuntimeSourceRoundContext:
    return RuntimeSourceRoundContext(
        round_no=1,
        retrieval_plan=_retrieval_plan(),
        proposed_filter_plan=ProposedFilterPlan(),
        adapter_notes=(),
        target_new=10,
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        run_state=cast(
            Any,
            SimpleNamespace(
                input_truth=SimpleNamespace(job_title="AI Agent Engineer", jd="", notes=""),
                requirement_sheet=_requirement_sheet(),
            ),
        ),
        source_plan_by_source={source_plan.source: source_plan},
        source_context=source_context,
        tracer=tracer,
    )


def test_logical_query_dispatch_freezes_requested_count_and_identity() -> None:
    dispatches = build_logical_query_dispatches(
        round_no=2,
        query_states=(_query_state("exploit"), _query_state("generic_explore")),
        lane_requested_counts={"exploit": 7, "generic_explore": 3},
        source_plan_version="2",
    )

    assert [item.round_no for item in dispatches] == [2, 2]
    assert [(item.lane_type, item.requested_count) for item in dispatches] == [
        ("exploit", 7),
        ("generic_explore", 3),
    ]
    assert [item.query_instance_id for item in dispatches] == ["query-exploit", "query-generic_explore"]
    assert [item.query_fingerprint for item in dispatches] == [
        "fingerprint-exploit",
        "fingerprint-generic_explore",
    ]
    assert [item.term_group_key for item in dispatches] == [
        "term-group-exploit",
        "term-group-generic_explore",
    ]


def test_logical_query_dispatch_rejects_missing_requested_count() -> None:
    with pytest.raises(ValueError, match="^logical_query_dispatch_missing_requested_count$"):
        build_logical_query_dispatches(
            round_no=1,
            query_states=(_query_state("exploit"),),
            lane_requested_counts={},
            source_plan_version="2",
        )


def test_round_unique_identities_counts_only_new_identity_membership() -> None:
    from seektalent.runtime.orchestrator import _round_unique_identity_count

    run_state = SimpleNamespace(
        candidate_identity_by_resume_id={
            "old-merge-resume": "identity-a",
            "same-round-a": "identity-b",
            "same-round-b": "identity-b",
        },
        candidate_identities={
            "identity-a": RuntimeCandidateIdentity(
                identity_id="identity-a",
                canonical_identity_id="identity-a",
                resume_ids=["old-resume", "old-merge-resume"],
            ),
            "identity-b": RuntimeCandidateIdentity(
                identity_id="identity-b",
                canonical_identity_id="identity-b",
                resume_ids=["same-round-a", "same-round-b"],
            ),
        },
    )
    dispatch_result = SourceRoundDispatchResult(
        source_results=(),
        candidates=(
            _candidate("old-merge-resume", "liepin"),
            _candidate("same-round-a", "cts"),
            _candidate("same-round-b", "liepin"),
        ),
        raw_candidate_count=3,
    )

    assert (
        _round_unique_identity_count(
            dispatch_result=dispatch_result,
            run_state=run_state,
            pre_round_seen_resume_ids=frozenset({"old-resume"}),
        )
        == 1
    )


def test_multisource_uses_existing_70_30_query_allocation() -> None:
    assert allocate_initial_lane_targets(
        query_states=[_query_state("exploit"), _query_state("generic_explore")],
        target_new=10,
    ) == {
        "exploit": 7,
        "generic_explore": 3,
    }


def test_dispatch_waits_for_liepin_before_returning_when_cts_finishes_first() -> None:
    async def scenario() -> None:
        cts_finished = asyncio.Event()
        allow_liepin_finish = asyncio.Event()
        dispatch_returned = False

        request = SourceRoundDispatchRequest(
            runtime_run_id="run-1",
            round_no=1,
            logical_queries=(_dispatch("exploit", 7), _dispatch("generic_explore", 3)),
            selected_sources=("cts", "liepin"),
            seen_resume_ids=frozenset(),
            seen_dedup_keys=frozenset(),
            source_query_intents_by_source={},
            requirement_sheet=_requirement_sheet(),
        )

        async def cts_adapter(request):
            del request
            cts_finished.set()
            return SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(_candidate("cts-1", "cts"),),
                raw_candidate_count=1,
            )

        async def liepin_adapter(request):
            del request
            await cts_finished.wait()
            await allow_liepin_finish.wait()
            return SourceRoundAdapterResult(
                source="liepin",
                status="completed",
                candidates=(_candidate("liepin-1", "liepin"),),
                raw_candidate_count=1,
            )

        async def run_dispatch():
            nonlocal dispatch_returned
            result = await dispatch_source_rounds(
                request=request,
                source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
            )
            dispatch_returned = True
            return result

        task = asyncio.create_task(run_dispatch())
        await asyncio.wait_for(cts_finished.wait(), timeout=1)
        await asyncio.sleep(0)
        assert dispatch_returned is False

        allow_liepin_finish.set()
        result = await asyncio.wait_for(task, timeout=1)

        assert dispatch_returned is True
        assert {item.source for item in result.source_results} == {"cts", "liepin"}

    asyncio.run(scenario())


def test_candidate_feedback_remains_before_generic_fallback() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=StopGuidance(
                quality_gate_status="low_quality_exhausted",
                can_stop=False,
                reason="needs more candidates",
                top_pool_strength="weak",
            ),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=True,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "candidate_feedback"


def test_execute_logical_dispatch_search_uses_frozen_requested_counts(tmp_path) -> None:
    observed_page_sizes: list[int] = []

    class FakeRetrievalService:
        async def search(
            self,
            *,
            query_terms,
            query_role,
            keyword_query,
            adapter_notes,
            provider_filters,
            runtime_constraints,
            page_size,
            round_no,
            trace_id,
            fetch_mode="summary",
            cursor=None,
        ) -> SearchResult:
            del (
                query_terms,
                query_role,
                keyword_query,
                adapter_notes,
                provider_filters,
                runtime_constraints,
                round_no,
                trace_id,
                fetch_mode,
                cursor,
            )
            observed_page_sizes.append(page_size)
            return SearchResult(
                candidates=[],
                diagnostics=["empty fixture"],
                request_payload={"pageSize": page_size},
                raw_candidate_count=0,
            )

    async def score_for_query_outcome(candidates: list[ResumeCandidate]) -> list[ScoredCandidate]:
        del candidates
        return []

    runtime = RetrievalRuntime(
        settings=make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"),
        retrieval_service=FakeRetrievalService(),
    )
    retrieval_plan = RoundRetrievalPlan(
        plan_version=2,
        round_no=1,
        query_terms=["数据开发"],
        keyword_query="数据开发",
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="none",
            allowed_locations=[],
            preferred_locations=[],
            priority_order=[],
            balanced_order=[],
            rotation_offset=0,
            target_new=10,
        ),
        target_new=10,
        rationale="dispatch override regression",
    )
    tracer = RunTracer(tmp_path / "trace-logical-dispatch")

    try:
        result = asyncio.run(
            runtime.execute_logical_dispatch_search(
                round_no=1,
                retrieval_plan=retrieval_plan,
                logical_queries=(_dispatch("exploit", 6), _dispatch("generic_explore", 4)),
                base_adapter_notes=[],
                target_new=10,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
                score_for_query_outcome=score_for_query_outcome,
            )
        )
    finally:
        tracer.close()

    assert observed_page_sizes == [6, 4]
    assert [(record.lane_type, record.requested_count) for record in result.sent_query_records] == [
        ("exploit", 6),
        ("generic_explore", 4),
    ]
    assert [
        (query.query_instance_id, query.query_fingerprint, query.term_group_key)
        for query in result.executed_queries
    ] == [
        ("query-exploit", "fingerprint-exploit", "term-group-exploit"),
        ("query-generic_explore", "fingerprint-generic_explore", "term-group-generic_explore"),
    ]


def test_execute_logical_dispatch_search_preserves_identity_for_city_package(tmp_path) -> None:
    class EmptyRetrievalService:
        async def search(self, **kwargs) -> SearchResult:
            del kwargs
            return SearchResult(candidates=[], diagnostics=["empty fixture"], raw_candidate_count=0, exhausted=True)

    runtime = RetrievalRuntime(
        settings=make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"),
        retrieval_service=EmptyRetrievalService(),
    )
    retrieval_plan = _retrieval_plan().model_copy(
        update={
            "location_execution_plan": LocationExecutionPlan(
                mode="single",
                allowed_locations=["上海"],
                preferred_locations=[],
                priority_order=["上海"],
                balanced_order=["上海"],
                rotation_offset=0,
                target_new=2,
            )
        }
    )
    tracer = RunTracer(tmp_path / "trace-city-query-identity")
    try:
        result = asyncio.run(
            runtime.execute_logical_dispatch_search(
                round_no=1,
                retrieval_plan=retrieval_plan,
                logical_queries=(_dispatch("exploit", 2),),
                base_adapter_notes=[],
                target_new=2,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert [
        (query.query_instance_id, query.query_fingerprint, query.term_group_key)
        for query in result.executed_queries
    ] == [("query-exploit", "fingerprint-exploit", "term-group-exploit")]


def test_execute_logical_dispatch_search_preserves_term_group_key(tmp_path) -> None:
    captured_query_states: list[LogicalQueryState] = []

    class CapturingRetrievalRuntime(RetrievalRuntime):
        async def execute_round_search(self, **kwargs) -> RetrievalExecutionResult:
            captured_query_states.extend(kwargs["query_states"])
            return cast(RetrievalExecutionResult, None)

    runtime = CapturingRetrievalRuntime(
        settings=make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"),
        retrieval_service=cast(Any, object()),
    )
    tracer = RunTracer(tmp_path / "trace-term-group-key")
    try:
        asyncio.run(
            runtime.execute_logical_dispatch_search(
                round_no=1,
                retrieval_plan=_retrieval_plan(),
                logical_queries=(_dispatch("exploit", 1),),
                base_adapter_notes=[],
                target_new=1,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert [item.term_group_key for item in captured_query_states] == ["term-group-exploit"]


def test_round_search_result_from_source_dispatch_preserves_retrieval_metadata_without_source_branch(
    tmp_path,
) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    tracer = RunTracer(tmp_path / "trace-source-dispatch-result")
    candidate = _candidate("fixture-1", "fixture_source")
    cts_query = CTSQuery(
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-exploit",
        query_fingerprint="fingerprint-exploit",
        query_terms=["数据开发"],
        keyword_query="数据开发",
        native_filters={},
        page=1,
        page_size=6,
        rationale="metadata regression",
    )
    sent_query = SentQueryRecord(
        round_no=1,
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-exploit",
        query_fingerprint="fingerprint-exploit",
        batch_no=1,
        requested_count=6,
        query_terms=["数据开发"],
        keyword_query="数据开发",
        source_plan_version=2,
        rationale="metadata regression",
    )
    search_attempt = SearchAttempt(
        query_role="exploit",
        batch_no=1,
        attempt_no=1,
        requested_page=1,
        requested_page_size=6,
        raw_candidate_count=1,
        batch_duplicate_count=0,
        batch_unique_new_count=1,
        cumulative_unique_new_count=1,
        continue_refill=False,
        exhausted_reason="target_satisfied",
    )
    query_hit = QueryResumeHit(
        run_id="run-1",
        query_instance_id="query-exploit",
        query_fingerprint="fingerprint-exploit",
        hit_sequence_no=1,
        resume_id="fixture-1",
        round_no=1,
        lane_type="exploit",
        batch_no=1,
        rank_in_query=1,
        provider_name="fixture_source",
        was_new_to_pool=True,
        was_duplicate=False,
    )
    cts_result = RetrievalExecutionResult(
        executed_queries=[cts_query],
        sent_query_records=[sent_query],
        new_candidates=[candidate],
        search_observation=SearchObservation(
            round_no=1,
            requested_count=6,
            raw_candidate_count=1,
            unique_new_count=1,
            shortage_count=0,
            fetch_attempt_count=1,
            exhausted_reason="target_satisfied",
            new_resume_ids=["cts-1"],
            new_candidate_summaries=[candidate.compact_summary()],
            adapter_notes=["cts note"],
        ),
        search_attempts=[search_attempt],
        query_resume_hits=[query_hit],
        provider_returned_candidates=[],
    )
    dispatch_result = SourceRoundDispatchResult(
        source_results=(
            SourceRoundAdapterResult(
                source="fixture_source",
                status="completed",
                candidates=(candidate,),
                raw_candidate_count=1,
                retrieval_result=cts_result,
            ),
            SourceRoundAdapterResult(source="liepin", status="blocked", safe_reason_code="source_login_required"),
        ),
        candidates=(candidate,),
        raw_candidate_count=1,
    )
    retrieval_plan = _retrieval_plan()

    try:
        result = runtime._round_search_result_from_source_dispatch(
            round_no=1,
            retrieval_plan=retrieval_plan,
            query_states=(_query_state("exploit"),),
            dispatch_result=dispatch_result,
            tracer=tracer,
        )
    finally:
        tracer.close()

    assert result.executed_queries == [cts_query]
    assert result.sent_query_records == [sent_query]
    assert result.search_attempts == [search_attempt]
    assert result.query_resume_hits == [query_hit]
    assert result.new_candidates == [candidate]


def test_source_round_is_not_ready_when_selected_source_blocks_even_if_another_returns_candidates(tmp_path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    candidate = _candidate("cts-1", "cts")
    dispatch_result = SourceRoundDispatchResult(
        source_results=(
            SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(candidate,),
                raw_candidate_count=1,
            ),
            SourceRoundAdapterResult(
                source="liepin",
                status="blocked",
                safe_reason_code="liepin_opencli_filter_unapplied",
            ),
        ),
        candidates=(candidate,),
        raw_candidate_count=1,
    )
    coverage = RuntimeSourceCoverageSummary(
        status="degraded",
        selected_source_kinds=("cts", "liepin"),
        completed_source_kinds=("cts",),
        blocked_source_kinds=("liepin",),
        finalization_scope="available_sources_only",
    )

    assert (
        runtime._source_round_not_ready_reason(
            coverage_summary=coverage,
            dispatch_result=dispatch_result,
        )
        == "liepin_opencli_filter_unapplied"
    )


def test_source_round_can_finish_when_later_browser_round_blocks_after_prior_candidates(tmp_path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    dispatch_result = SourceRoundDispatchResult(
        source_results=(
            SourceRoundAdapterResult(
                source="liepin",
                status="blocked",
                safe_reason_code="source_browser_backend_unavailable",
            ),
        ),
        candidates=(),
        raw_candidate_count=0,
    )
    coverage = RuntimeSourceCoverageSummary(
        status="empty",
        selected_source_kinds=("liepin",),
        blocked_source_kinds=("liepin",),
        finalization_scope="available_sources_only",
    )

    assert (
        runtime._source_round_not_ready_reason(
            coverage_summary=coverage,
            dispatch_result=dispatch_result,
            has_prior_candidates=True,
        )
        is None
    )


def test_first_round_partial_browser_source_with_new_candidates_still_blocks(tmp_path) -> None:
    candidate = _candidate("liepin-1", "liepin")

    def source_round_adapters(runtime: WorkflowRuntime, context: RuntimeSourceRoundContext):
        del runtime, context

        async def adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            del request
            return SourceRoundAdapterResult(
                source="liepin",
                status="partial",
                safe_reason_code="source_browser_backend_unavailable",
                candidates=(candidate,),
                raw_candidate_count=1,
            )

        return {"liepin": adapter}

    runtime = WorkflowRuntime(
        make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"),
        source_round_adapter_provider=source_round_adapters,
    )
    tracer = RunTracer(tmp_path / "trace")
    try:
        with pytest.raises(RunStageError, match="source_browser_backend_unavailable"):
            asyncio.run(
                runtime._execute_multi_source_round_search(
                    round_no=1,
                    retrieval_plan=_retrieval_plan(),
                    proposed_filter_plan=ProposedFilterPlan(),
                    query_states=(_query_state("exploit"),),
                    adapter_notes=(),
                    target_new=10,
                    seen_resume_ids=set(),
                    seen_dedup_keys=set(),
                    run_state=_run_state(),
                    source_plan=(
                        RuntimeSourceLanePlan(
                            source_plan_id="run-1:source:0:liepin",
                            runtime_run_id="run-1",
                            source="liepin",
                            label="Liepin",
                        ),
                    ),
                    source_context={"status": "ready"},
                    tracer=tracer,
                )
            )
    finally:
        tracer.close()


def test_dispatch_sends_same_query_bundle_to_cts_and_liepin() -> None:
    seen: dict[str, list[str]] = {}
    requested_counts: dict[str, list[int]] = {}

    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        seen["cts"] = [query.query_fingerprint for query in request.logical_queries]
        requested_counts["cts"] = [query.requested_count for query in request.logical_queries]
        return SourceRoundAdapterResult(
            source="cts",
            status="completed",
            candidates=(_candidate("cts-1", "cts"),),
            raw_candidate_count=1,
        )

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        seen["liepin"] = [query.query_fingerprint for query in request.logical_queries]
        requested_counts["liepin"] = [query.requested_count for query in request.logical_queries]
        return SourceRoundAdapterResult(
            source="liepin",
            status="completed",
            candidates=(_candidate("liepin-1", "liepin"),),
            raw_candidate_count=1,
        )

    result = asyncio.run(
        dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id="run-1",
                round_no=1,
                logical_queries=(_dispatch("exploit", 7), _dispatch("generic_explore", 3)),
                selected_sources=("cts", "liepin"),
                seen_resume_ids=frozenset(),
                seen_dedup_keys=frozenset(),
                requirement_sheet=_requirement_sheet(),
            ),
            source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
        )
    )

    assert seen["cts"] == ["fingerprint-exploit", "fingerprint-generic_explore"]
    assert seen["liepin"] == ["fingerprint-exploit", "fingerprint-generic_explore"]
    assert requested_counts["cts"] == [7, 3]
    assert requested_counts["liepin"] == [7, 3]
    assert [item.source for item in result.source_results] == ["cts", "liepin"]
    assert [candidate.resume_id for candidate in result.candidates] == ["cts-1", "liepin-1"]


def test_dispatch_request_carries_requirement_sheet_to_sources() -> None:
    async def run_case() -> None:
        request = SourceRoundDispatchRequest(
            runtime_run_id="run-1",
            round_no=1,
            logical_queries=(_dispatch("exploit", 7),),
            selected_sources=("cts", "liepin"),
            seen_resume_ids=frozenset(),
            seen_dedup_keys=frozenset(),
            requirement_sheet=_requirement_sheet(),
        )
        seen: dict[str, str] = {}

        async def cts_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            seen["cts"] = source_request.requirement_sheet.job_title
            return SourceRoundAdapterResult(source="cts", status="completed")

        async def liepin_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            seen["liepin"] = source_request.requirement_sheet.job_title
            return SourceRoundAdapterResult(source="liepin", status="completed")

        await dispatch_source_rounds(
            request=request,
            source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
        )

        assert seen == {"cts": "AI Agent Engineer", "liepin": "AI Agent Engineer"}

    asyncio.run(run_case())


def test_dispatch_waits_for_liepin_terminal_state_after_cts_finishes_first() -> None:
    async def run_case() -> None:
        request = SourceRoundDispatchRequest(
            runtime_run_id="run-1",
            round_no=1,
            logical_queries=(_dispatch("exploit", 7),),
            selected_sources=("cts", "liepin"),
            seen_resume_ids=frozenset(),
            seen_dedup_keys=frozenset(),
            requirement_sheet=_requirement_sheet(),
        )
        cts_finished = asyncio.Event()
        allow_liepin_to_finish = asyncio.Event()

        async def cts_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            del source_request
            cts_finished.set()
            return SourceRoundAdapterResult(source="cts", status="completed")

        async def liepin_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            del source_request
            await cts_finished.wait()
            await allow_liepin_to_finish.wait()
            return SourceRoundAdapterResult(source="liepin", status="completed")

        dispatch_task = asyncio.create_task(
            dispatch_source_rounds(
                request=request,
                source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
            )
        )
        await asyncio.wait_for(cts_finished.wait(), timeout=1)
        await asyncio.sleep(0)

        assert not dispatch_task.done()

        allow_liepin_to_finish.set()
        result = await asyncio.wait_for(dispatch_task, timeout=1)
        assert [source_result.source for source_result in result.source_results] == ["cts", "liepin"]

    asyncio.run(run_case())


def test_dispatch_reports_each_source_result_as_soon_as_it_finishes() -> None:
    async def run_case() -> None:
        request = SourceRoundDispatchRequest(
            runtime_run_id="run-1",
            round_no=1,
            logical_queries=(_dispatch("exploit", 7),),
            selected_sources=("cts", "liepin"),
            seen_resume_ids=frozenset(),
            seen_dedup_keys=frozenset(),
            requirement_sheet=_requirement_sheet(),
        )
        cts_finished = asyncio.Event()
        allow_liepin_to_finish = asyncio.Event()
        observed: list[str] = []

        async def cts_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            del source_request
            cts_finished.set()
            return SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(_candidate("cts-1", "cts"),),
                raw_candidate_count=1,
            )

        async def liepin_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            del source_request
            await cts_finished.wait()
            await allow_liepin_to_finish.wait()
            return SourceRoundAdapterResult(source="liepin", status="completed")

        async def result_callback(result: SourceRoundAdapterResult) -> None:
            observed.append(result.source)

        dispatch_task = asyncio.create_task(
            dispatch_source_rounds(
                request=request,
                source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
                result_callback=result_callback,
            )
        )
        await asyncio.wait_for(cts_finished.wait(), timeout=1)
        await asyncio.sleep(0)

        assert observed == ["cts"]
        assert not dispatch_task.done()

        allow_liepin_to_finish.set()
        result = await asyncio.wait_for(dispatch_task, timeout=1)
        assert [source_result.source for source_result in result.source_results] == ["cts", "liepin"]
        assert observed == ["cts", "liepin"]

    asyncio.run(run_case())


def test_dispatch_starts_sources_concurrently() -> None:
    started: set[str] = set()

    async def run_case() -> object:
        release = asyncio.Event()

        async def adapter(source: str, request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
            del request
            started.add(source)
            if len(started) == 2:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=1)
            return SourceRoundAdapterResult(
                source=source,
                status="completed",
                candidates=(_candidate(f"{source}-1", source),),
                raw_candidate_count=1,
            )

        return await dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id="run-1",
                round_no=1,
                logical_queries=(_dispatch("exploit", 7),),
                selected_sources=("cts", "liepin"),
                seen_resume_ids=frozenset(),
                seen_dedup_keys=frozenset(),
                requirement_sheet=_requirement_sheet(),
            ),
            source_adapters={
                "cts": lambda request: adapter("cts", request),
                "liepin": lambda request: adapter("liepin", request),
            },
        )

    result = asyncio.run(run_case())

    assert started == {"cts", "liepin"}
    assert {item.source for item in result.source_results} == {"cts", "liepin"}


def test_dispatch_converts_liepin_provider_failure_to_source_result() -> None:
    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del request
        return SourceRoundAdapterResult(
            source="cts",
            status="completed",
            candidates=(_candidate("cts-1", "cts"),),
            raw_candidate_count=1,
        )

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del request
        raise SourceProviderFailed("browser closed")

    result = asyncio.run(
        dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id="run-1",
                round_no=1,
                logical_queries=(_dispatch("exploit", 7),),
                selected_sources=("cts", "liepin"),
                seen_resume_ids=frozenset(),
                seen_dedup_keys=frozenset(),
                requirement_sheet=_requirement_sheet(),
            ),
            source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
        )
    )

    assert [candidate.resume_id for candidate in result.candidates] == ["cts-1"]
    liepin = next(item for item in result.source_results if item.source == "liepin")
    assert liepin.status == "failed"
    assert liepin.safe_reason_code == "failed_provider_error"


def test_cts_adapter_converts_provider_timeout_to_source_result(tmp_path) -> None:
    class TimeoutRetrievalRuntime:
        async def execute_logical_dispatch_search(self, **kwargs) -> object:
            del kwargs
            raise httpx.ReadTimeout("CTS provider timed out")

    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    runtime.retrieval_runtime = TimeoutRetrievalRuntime()  # type: ignore[assignment]
    tracer = RunTracer(tmp_path / "trace-cts-timeout")
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 7),),
        selected_sources=("cts",),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
    )
    source_plan = RuntimeSourceLanePlan(
        source_plan_id="plan-cts",
        runtime_run_id="run-1",
        source="cts",
        label="CTS",
    )

    try:
        result = asyncio.run(
            _run_cts_source_round(
                runtime=runtime,
                context=RuntimeSourceRoundContext(
                    round_no=1,
                    retrieval_plan=_retrieval_plan(),
                    proposed_filter_plan=ProposedFilterPlan(),
                    adapter_notes=(),
                    target_new=10,
                    seen_resume_ids=frozenset(),
                    seen_dedup_keys=frozenset(),
                    run_state=cast(
                        Any,
                        SimpleNamespace(
                            input_truth=SimpleNamespace(job_title="AI Agent Engineer", jd="", notes=""),
                            requirement_sheet=_requirement_sheet(),
                        ),
                    ),
                    source_plan_by_source={"cts": source_plan},
                    source_context=None,
                    tracer=tracer,
                ),
                request=request,
                source_id="cts",
            )
        )
    finally:
        tracer.close()

    assert result.source == "cts"
    assert result.status == "failed"
    assert result.safe_reason_code == "source_provider_failed"
    assert result.candidates == ()
    assert result.raw_candidate_count == 0


def test_cts_adapter_converts_business_error_to_failed_source_result(tmp_path) -> None:
    class BusinessErrorRetrievalRuntime:
        async def execute_logical_dispatch_search(self, **kwargs) -> object:
            del kwargs
            raise ProviderSearchError(
                reason_code="cts_auth_failed",
                message="CTS search returned business error code=10001 status='error'.",
            )

    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    runtime.retrieval_runtime = BusinessErrorRetrievalRuntime()  # type: ignore[assignment]
    tracer = RunTracer(tmp_path / "trace-cts-business-error")
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 7),),
        selected_sources=("cts",),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
    )
    source_plan = RuntimeSourceLanePlan(
        source_plan_id="plan-cts",
        runtime_run_id="run-1",
        source="cts",
        label="CTS",
    )

    try:
        result = asyncio.run(
            _run_cts_source_round(
                runtime=runtime,
                context=_source_round_context(source_plan=source_plan, tracer=tracer),
                request=request,
                source_id="cts",
            )
        )
    finally:
        tracer.close()

    assert result.source == "cts"
    assert result.status == "failed"
    assert result.safe_reason_code == "cts_auth_failed"
    assert result.diagnostics == ("CTS search returned business error code=10001 status='error'.",)
    assert result.candidates == ()


def test_liepin_backend_blocked_stays_blocked_when_cts_is_also_selected(monkeypatch, tmp_path) -> None:
    async def blocked_liepin_bundle(**kwargs) -> RuntimeSourceLaneResult:
        return RuntimeSourceLaneResult(
            runtime_run_id=kwargs["runtime_run_id"],
            source_plan_id=kwargs["source_plan_id"],
            source_lane_run_id=f"{kwargs['source_plan_id']}:blocked",
            source="liepin",
            lane_mode="card",
            attempt=1,
            status="blocked",
            blocked_reason_code="blocked_backend_unavailable",
        )

    monkeypatch.setattr("seektalent.source_adapters.run_liepin_logical_query_bundle", blocked_liepin_bundle)
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    tracer = RunTracer(tmp_path / "trace-liepin-dual")
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 7),),
        selected_sources=("cts", "liepin"),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
    )

    try:
        result = asyncio.run(
            _run_liepin_source_round(
                runtime=runtime,
                context=_source_round_context(
                    source_plan=RuntimeSourceLanePlan(
                        source_plan_id="plan-liepin",
                        runtime_run_id="run-1",
                        source="liepin",
                        label="Liepin",
                    ),
                    tracer=tracer,
                    source_context={"backend_mode": "opencli"},
                ),
                request=request,
                source_id="liepin",
            )
        )
    finally:
        tracer.close()

    assert result.status == "blocked"
    assert result.safe_reason_code == "blocked_backend_unavailable"


def test_liepin_backend_blocked_stays_blocked_when_liepin_is_only_selected_source(monkeypatch, tmp_path) -> None:
    async def blocked_liepin_bundle(**kwargs) -> RuntimeSourceLaneResult:
        return RuntimeSourceLaneResult(
            runtime_run_id=kwargs["runtime_run_id"],
            source_plan_id=kwargs["source_plan_id"],
            source_lane_run_id=f"{kwargs['source_plan_id']}:blocked",
            source="liepin",
            lane_mode="card",
            attempt=1,
            status="blocked",
            blocked_reason_code="blocked_backend_unavailable",
        )

    monkeypatch.setattr("seektalent.source_adapters.run_liepin_logical_query_bundle", blocked_liepin_bundle)
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    tracer = RunTracer(tmp_path / "trace-liepin-single")
    dispatches = (_dispatch("exploit", 7),)
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=dispatches,
        selected_sources=("liepin",),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
        source_query_intents_by_source=build_runtime_source_query_intents(
            source_kinds=("liepin",),
            logical_dispatches=dispatches,
            filter_intents=(),
            location_intent=None,
            age_intent=None,
            source_budget_policy=RuntimeSourceBudgetPolicy(),
        ),
    )

    try:
        result = asyncio.run(
            _run_liepin_source_round(
                runtime=runtime,
                context=_source_round_context(
                    source_plan=RuntimeSourceLanePlan(
                        source_plan_id="plan-liepin",
                        runtime_run_id="run-1",
                        source="liepin",
                        label="Liepin",
                    ),
                    tracer=tracer,
                    source_context={"backend_mode": "opencli"},
                ),
                request=request,
                source_id="liepin",
            )
        )
    finally:
        tracer.close()

    assert result.status == "blocked"
    assert result.safe_reason_code == "blocked_backend_unavailable"
    assert result.executed_query_packages == ()


def test_liepin_adapter_exposes_public_filter_failure_reason(monkeypatch, tmp_path) -> None:
    async def blocked_liepin_bundle(**kwargs) -> RuntimeSourceLaneResult:
        return RuntimeSourceLaneResult(
            runtime_run_id=kwargs["runtime_run_id"],
            source_plan_id=kwargs["source_plan_id"],
            source_lane_run_id=f"{kwargs['source_plan_id']}:blocked",
            source="liepin",
            lane_mode="card",
            attempt=1,
            status="blocked",
            blocked_reason_code="liepin_opencli_filter_unapplied",
            stop_reason_code="liepin_opencli_filter_unapplied",
        )

    monkeypatch.setattr("seektalent.source_adapters.run_liepin_logical_query_bundle", blocked_liepin_bundle)
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    tracer = RunTracer(tmp_path / "trace-liepin-filter")
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 7),),
        selected_sources=("liepin",),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
    )

    try:
        result = asyncio.run(
            _run_liepin_source_round(
                runtime=runtime,
                context=_source_round_context(
                    source_plan=RuntimeSourceLanePlan(
                        source_plan_id="plan-liepin",
                        runtime_run_id="run-1",
                        source="liepin",
                        label="Liepin",
                    ),
                    tracer=tracer,
                    source_context={"backend_mode": "opencli"},
                ),
                request=request,
                source_id="liepin",
            )
        )
    finally:
        tracer.close()

    assert result.status == "blocked"
    assert result.safe_reason_code == "source_filter_unavailable"
    assert result.lane_result is not None
    assert result.lane_result.blocked_reason_code == "liepin_opencli_filter_unapplied"


def test_liepin_source_adapter_records_provider_snapshots_to_corpus(monkeypatch, tmp_path) -> None:
    raw_payload = {
        "providerCandidateKeyHash": "liepin-provider-key-hash",
        "candidate_name": "李四",
        "currentTitle": "数据平台工程师",
        "workExperienceList": [{"company": "Example Data", "title": "数据平台工程师", "summary": "负责数据平台建设。"}],
    }
    snapshot_sha256 = sha256_json(raw_payload)
    candidate = ResumeCandidate(
        resume_id="liepin-provider-key-hash",
        source_resume_id="liepin-provider-key-hash",
        snapshot_sha256=snapshot_sha256,
        dedup_key="liepin-fingerprint-1",
        search_text="李四 数据平台",
        raw={"provider_candidate_key_hash": "liepin-provider-key-hash"},
    )
    provider_snapshot = ProviderSnapshot(
        provider_name="liepin",
        payload_kind="detail",
        raw_payload=raw_payload,
        normalized_text="李四 数据平台",
        provider_subject_id="liepin-provider-key-hash",
        provider_listing_id=None,
        synthetic_candidate_fingerprint="liepin-fingerprint-1",
        identity_confidence="provider_subject_id",
        extraction_source="test",
        extractor_version="test",
        pii_classification="no_direct_contact",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
        score_evidence_source="detail_enriched",
    )

    async def completed_liepin_bundle(**kwargs) -> RuntimeSourceLaneResult:
        return RuntimeSourceLaneResult(
            runtime_run_id=kwargs["runtime_run_id"],
            source_plan_id=kwargs["source_plan_id"],
            source_lane_run_id=f"{kwargs['source_plan_id']}:lane-1",
            source="liepin",
            lane_mode="card",
            attempt=1,
            status="completed",
            candidate_store_updates={candidate.resume_id: candidate},
            source_evidence_updates=(
                RuntimeSourceEvidence(
                    evidence_id="liepin-evidence-1",
                    source="liepin",
                    provider="liepin",
                    source_plan_id=kwargs["source_plan_id"],
                    source_lane_run_id=f"{kwargs['source_plan_id']}:lane-1",
                    evidence_level="detail",
                    candidate_resume_id=candidate.resume_id,
                    provider_candidate_key_hash="liepin-provider-key-hash",
                    provider_rank=1,
                    query_fingerprint="fingerprint-exploit",
                    collected_at="2026-05-26T00:00:00Z",
                ),
            ),
            provider_snapshots=(provider_snapshot,),
            raw_candidate_count=1,
        )

    monkeypatch.setattr("seektalent.source_adapters.run_liepin_logical_query_bundle", completed_liepin_bundle)
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        artifacts_path=str(tmp_path / "artifacts"),
        corpus_path=str(tmp_path / "corpus.sqlite3"),
        mock_cts=True,
        provider_name="cts",
    )
    runtime = WorkflowRuntime(settings)
    tracer = RunTracer(tmp_path / "trace-liepin-corpus")
    runtime._active_corpus_session = tracer.store.create_root(
        kind="corpus",
        display_name="test corpus ingest",
        producer="CorpusRuntime",
    )
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 2),),
        selected_sources=("liepin",),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
    )

    try:
        result = asyncio.run(
            _run_liepin_source_round(
                runtime=runtime,
                context=_source_round_context(
                    source_plan=RuntimeSourceLanePlan(
                        source_plan_id="plan-liepin",
                        runtime_run_id="run-1",
                        source="liepin",
                        label="Liepin",
                    ),
                    tracer=tracer,
                    source_context={"backend_mode": "opencli"},
                ),
                request=request,
                source_id="liepin",
            )
        )
    finally:
        tracer.close()

    assert result.status == "completed"
    docs = CorpusStore(settings.corpus_path).get_resume_documents_by_provider_candidate_id(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=DEFAULT_WORKSPACE_ID,
        provider_name="liepin",
        provider_candidate_ids=["liepin-provider-key-hash"],
    )
    assert docs["liepin-provider-key-hash"]["snapshot_sha256"] == snapshot_sha256


def test_liepin_adapter_receives_selected_source_plan_without_source_scan() -> None:
    source = Path("src/seektalent/source_adapters/round_adapters.py").read_text(encoding="utf-8")
    body = source.split("async def _run_liepin_source_round", 1)[1].split(
        "def _source_filter_warning_reason",
        1,
    )[0]

    assert 'lane.source == "liepin"' not in body
    assert "missing_liepin_source_plan" not in body


def test_dispatch_propagates_runtime_invariant_errors() -> None:
    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del request
        raise RuntimeSourceInvariantError("bad logical query contract")

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del request
        return SourceRoundAdapterResult(source="liepin", status="completed")

    with pytest.raises(RuntimeSourceInvariantError):
        asyncio.run(
            dispatch_source_rounds(
                request=SourceRoundDispatchRequest(
                    runtime_run_id="run-1",
                    round_no=1,
                    logical_queries=(_dispatch("exploit", 7),),
                    selected_sources=("cts", "liepin"),
                    seen_resume_ids=frozenset(),
                    seen_dedup_keys=frozenset(),
                    requirement_sheet=_requirement_sheet(),
                ),
                source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
            )
        )


def test_dispatch_propagates_programmer_type_errors() -> None:
    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del request
        raise TypeError("adapter called with an invalid contract")

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del request
        return SourceRoundAdapterResult(source="liepin", status="completed")

    with pytest.raises(TypeError):
        asyncio.run(
            dispatch_source_rounds(
                request=SourceRoundDispatchRequest(
                    runtime_run_id="run-1",
                    round_no=1,
                    logical_queries=(_dispatch("exploit", 7),),
                    selected_sources=("cts", "liepin"),
                    seen_resume_ids=frozenset(),
                    seen_dedup_keys=frozenset(),
                    requirement_sheet=_requirement_sheet(),
                ),
                source_adapters={"cts": cts_adapter, "liepin": liepin_adapter},
            )
        )
