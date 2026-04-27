import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import (
    CTSQuery,
    FinalCandidate,
    FinalResult,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    PoolDecision,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RequirementExtractionDraft,
    RequirementSheet,
    ResumeCandidate,
    RetrievalState,
    RoundRetrievalPlan,
    RoundState,
    RuntimeConstraint,
    ScoredCandidate,
    ScoringPolicy,
    ScoringFailure,
    SearchObservation,
    SearchControllerDecision,
    SentQueryRecord,
    StopControllerDecision,
    RunState,
)
from seektalent.retrieval import build_location_execution_plan, build_round_retrieval_plan
from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult, RetrievalRuntime
from seektalent.runtime.runtime_reports import render_round_review as render_round_review_direct
from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


def _sample_inputs() -> tuple[str, str, str]:
    return (
        "Senior Python Engineer",
        "Senior Python Engineer responsible for resume matching workflows.",
        "Prefer retrieval experience and shipping production AI features.",
    )


def _make_candidate(resume_id: str, *, source_round: int = 1) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        dedup_key=resume_id,
        source_round=source_round,
        now_location="上海",
        expected_location="上海",
        expected_job_category="Python Engineer",
        work_year=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=["Example Co | Python Engineer | Built retrieval workflows."],
        project_names=["Resume search"],
        work_summaries=["python", "retrieval", "trace"],
        search_text="python retrieval trace resume search",
        raw={"resume_id": resume_id, "candidate_name": resume_id},
    )


class SequenceController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return SearchControllerDecision(
            thought_summary="Round 2 widens the domain surface.",
            action="search_cts",
            decision_rationale="Add one reflection term while keeping the same filter shape.",
            proposed_query_terms=["python", "resume matching", "trace"],
            proposed_filter_plan=ProposedFilterPlan(),
            response_to_reflection="Accepted the added trace term and left location execution to runtime.",
        )


class StubRequirementExtractor:
    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            jd_query_terms=["resume matching", "trace"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=[
                QueryTermCandidate(
                    term="python",
                    source="job_title",
                    category="role_anchor",
                    priority=1,
                    evidence="Job title",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="resume matching",
                    source="jd",
                    category="domain",
                    priority=2,
                    evidence="JD body",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                ),
            ],
            scoring_rationale="Score Python fit first.",
        )


class SingleFamilyRequirementExtractor:
    def __init__(self, *, include_reserve: bool) -> None:
        self.include_reserve = include_reserve

    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        pool = [
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="resume matching",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ]
        if self.include_reserve:
            pool.append(
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                    active=False,
                )
            )
        draft = RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            jd_query_terms=["resume matching"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=pool,
            scoring_rationale="Score Python fit first.",
        )


class SequenceReflection:
    def __init__(self) -> None:
        self.calls = 0

    async def reflect(self, *, context) -> ReflectionAdvice:
        self.calls += 1
        if self.calls == 1:
            return ReflectionAdvice(
                keyword_advice=ReflectionKeywordAdvice(suggested_keep_terms=["trace"]),
                filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
                suggest_stop=False,
                reflection_summary="Continue with one extra tracing term.",
            )
        return ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
            suggest_stop=True,
            suggested_stop_reason="reflection_stop",
            reflection_summary="Stop after round 2.",
        )


class MutationAttemptReflection:
    async def reflect(self, *, context) -> ReflectionAdvice:
        del context
        return ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(
                suggested_activate_terms=["trace"],
                suggested_drop_terms=["resume matching"],
                suggested_deprioritize_terms=["resume matching"],
            ),
            filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
            suggest_stop=False,
            reflection_summary="Attempt to mutate the query term pool.",
        )


class StubScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []
        for context in contexts:
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=context.normalized_resume.resume_id,
                branch_id=f"r{context.round_no}-{context.normalized_resume.resume_id}",
                model="stub-scorer",
                summary="stub score",
                payload={},
            )
            scored.append(
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=90 if context.round_no == 1 else 91,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="Stub scorer accepted the candidate.",
                    evidence=["python", "retrieval"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["resume matching"],
                    negative_signals=[],
                    strengths=["Strong backend match."],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
            )
        return scored, failures


class LowQualityScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        return (
            [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=65,
                    must_have_match_score=60,
                    preferred_match_score=50,
                    risk_score=40,
                    risk_flags=[],
                    reasoning_summary="Usable but not a strong fit.",
                    evidence=["python"],
                    confidence="medium",
                    matched_must_haves=["python"],
                    missing_must_haves=["resume matching"],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=["Some Python signal."],
                    weaknesses=["Weak retrieval-specific evidence."],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
                for context in contexts
            ],
            [],
        )


class StubFinalizer:
    last_validator_retry_count = 0
    last_validator_retry_reasons: list[str] = []

    async def finalize(self, *, run_id, run_dir, rounds_executed, stop_reason, ranked_candidates) -> FinalResult:
        return FinalResult(
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            summary=f"Returned {len(ranked_candidates)} candidates after {rounds_executed} rounds.",
            candidates=[
                FinalCandidate(
                    resume_id=item.resume_id,
                    rank=index,
                    final_score=item.overall_score,
                    fit_bucket=item.fit_bucket,
                    match_summary="stub match summary",
                    strengths=item.strengths,
                    weaknesses=item.weaknesses,
                    matched_must_haves=item.matched_must_haves,
                    matched_preferences=item.matched_preferences,
                    risk_flags=item.risk_flags,
                    why_selected=item.reasoning_summary,
                    source_round=item.source_round,
                )
                for index, item in enumerate(ranked_candidates, start=1)
            ],
        )


class StopAfterSecondRoundController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        if self.calls == 2:
            return SearchControllerDecision(
                thought_summary="Round 2 widens the domain surface.",
                action="search_cts",
                decision_rationale="Add one reflection term while keeping the same filter shape.",
                proposed_query_terms=["python", "resume matching", "trace"],
                proposed_filter_plan=ProposedFilterPlan(),
                response_to_reflection="Accepted the added trace term and left location execution to runtime.",
            )
        return StopControllerDecision(
            thought_summary="Stop after two completed retrieval rounds.",
            action="stop",
            decision_rationale="The top pool has stabilized and the next search is unlikely to add fit candidates.",
            response_to_reflection="The latest reflection confirms low marginal value.",
            stop_reason="controller_stop",
        )


class StopOnSecondRoundController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return StopControllerDecision(
            thought_summary="Stop before trying all admitted families.",
            action="stop",
            decision_rationale="The current pool seems stable enough.",
            response_to_reflection="Acknowledged the latest reflection.",
            stop_reason="controller_stop",
        )


class SearchThenStopController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the active family.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return StopControllerDecision(
            thought_summary="Stop despite low-quality exhaustion.",
            action="stop",
            decision_rationale="Controller wants to stop.",
            response_to_reflection="Acknowledged the latest reflection.",
            stop_reason="controller_stop",
        )


class StubCompanyDiscovery:
    async def discover_web(self, *, requirement_sheet, round_no, trigger_reason):
        del requirement_sheet, round_no
        from seektalent.company_discovery.models import (
            CompanyDiscoveryResult,
            CompanyEvidence,
            CompanySearchTask,
            PageReadResult,
            SearchRerankResult,
            TargetCompanyCandidate,
            TargetCompanyPlan,
            WebSearchResult,
        )

        evidence = CompanyEvidence(
            title="source",
            url="https://example.com",
            snippet="Concrete company evidence.",
            source_type="web",
        )
        company = TargetCompanyCandidate(
            name="火山引擎",
            aliases=["Volcengine"],
            source="web_inferred",
            intent="target",
            confidence=0.91,
            fit_axes=["cloud", "ai_platform"],
            search_usage="keyword_term",
            evidence=[evidence],
            rationale="Evidence-backed source company.",
        )
        plan = TargetCompanyPlan(
            inferred_targets=[company],
            web_discovery_attempted=True,
            stop_reason="completed",
        )
        return CompanyDiscoveryResult(
            plan=plan,
            search_tasks=[
                CompanySearchTask(
                    query_id="q1",
                    query="大模型平台 推理服务 公司",
                    intent="market_map",
                    rationale="Find source companies.",
                )
            ],
            search_results=[
                WebSearchResult(
                    rank=1,
                    title="火山引擎大模型服务平台",
                    url="https://example.com/ai-infra",
                    snippet="火山引擎 has AI platform teams.",
                )
            ],
            reranked_results=[
                SearchRerankResult(
                    rank=1,
                    source_index=0,
                    score=0.91,
                    title="火山引擎大模型服务平台",
                    url="https://example.com/ai-infra",
                )
            ],
            page_reads=[
                PageReadResult(
                    url="https://example.com/ai-infra",
                    title="AI infra company map",
                    text="火山引擎 provides AI platform services.",
                )
            ],
            trigger_reason=trigger_reason,
            evidence_candidates=[company],
        )


def _install_runtime_stubs(runtime: WorkflowRuntime, *, controller: object, resume_scorer: object) -> None:
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = StubRequirementExtractor()
    runtime_any.controller = controller
    runtime_any.reflection_critic = SequenceReflection()
    runtime_any.resume_scorer = resume_scorer
    runtime_any.finalizer = StubFinalizer()


def _install_broaden_stubs(runtime: WorkflowRuntime, *, include_reserve: bool) -> None:
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = SingleFamilyRequirementExtractor(include_reserve=include_reserve)
    runtime_any.controller = SearchThenStopController()
    runtime_any.reflection_critic = SequenceReflection()
    runtime_any.resume_scorer = LowQualityScorer()
    runtime_any.finalizer = StubFinalizer()


def _round_review_fixture() -> dict[str, object]:
    return {
        "round_no": 2,
        "controller_decision": SearchControllerDecision(
            thought_summary="Round 2 widened the term surface.",
            action="search_cts",
            decision_rationale="The first round produced one strong hit but left domain coverage thin.",
            proposed_query_terms=["python", "resume matching", "trace"],
            proposed_filter_plan=ProposedFilterPlan(),
        ),
        "retrieval_plan": RoundRetrievalPlan(
            plan_version=1,
            round_no=2,
            query_terms=["python", "resume matching", "trace"],
            keyword_query="python resume matching trace",
            projected_provider_filters={"position": "Python Engineer"},
            runtime_only_constraints=[
                RuntimeConstraint(
                    field="work_content",
                    normalized_value="resume matching",
                    source="jd",
                    rationale="Keep the retrieval workflow signal explicit.",
                    blocking=False,
                )
            ],
            location_execution_plan=LocationExecutionPlan(
                mode="balanced_all",
                allowed_locations=["上海", "杭州"],
                preferred_locations=["上海"],
                priority_order=["上海", "杭州"],
                balanced_order=["上海", "杭州"],
                rotation_offset=1,
                target_new=8,
            ),
            target_new=8,
            rationale="Expand with one reflection term while keeping city coverage balanced.",
        ),
        "observation": SearchObservation(
            round_no=2,
            requested_count=8,
            raw_candidate_count=5,
            unique_new_count=3,
            shortage_count=5,
            fetch_attempt_count=2,
            exhausted_reason="max_pages_reached",
            adapter_notes=["city dispatch rotated to 杭州 first"],
        ),
        "newly_scored_count": 3,
        "pool_decisions": [
            PoolDecision(
                resume_id="resume-1",
                round_no=2,
                decision="selected",
                rank_in_round=1,
                reasons_for_selection=["Highest score in the round."],
                compared_against_pool_summary="Entered the top pool with the strongest evidence mix.",
            ),
            PoolDecision(
                resume_id="resume-2",
                round_no=2,
                decision="retained",
                rank_in_round=2,
                reasons_for_selection=["Still strong enough to stay in the pool."],
                compared_against_pool_summary="Held rank against the new candidates.",
            ),
            PoolDecision(
                resume_id="resume-3",
                round_no=2,
                decision="dropped",
                rank_in_round=3,
                reasons_for_rejection=["Replaced by higher-ranked resumes in the global scored set."],
                compared_against_pool_summary="Fell behind the refreshed pool.",
            ),
        ],
        "top_candidates": [
            ScoredCandidate(
                resume_id="resume-1",
                fit_bucket="fit",
                overall_score=92,
                must_have_match_score=90,
                preferred_match_score=80,
                risk_score=10,
                risk_flags=[],
                reasoning_summary="Strong retrieval and Python evidence.",
                evidence=["python", "resume matching"],
                confidence="high",
                matched_must_haves=["python"],
                missing_must_haves=[],
                matched_preferences=["trace"],
                negative_signals=[],
                strengths=["Strong retrieval depth."],
                weaknesses=[],
                source_round=2,
            ),
            ScoredCandidate(
                resume_id="resume-2",
                fit_bucket="fit",
                overall_score=88,
                must_have_match_score=86,
                preferred_match_score=72,
                risk_score=14,
                risk_flags=[],
                reasoning_summary="Consistent with previous round strength.",
                evidence=["python"],
                confidence="medium",
                matched_must_haves=["python"],
                missing_must_haves=[],
                matched_preferences=[],
                negative_signals=[],
                strengths=["Stable backend signal."],
                weaknesses=["Less trace depth."],
                source_round=1,
            ),
        ],
        "dropped_candidates": [
            ScoredCandidate(
                resume_id="resume-3",
                fit_bucket="not_fit",
                overall_score=70,
                must_have_match_score=72,
                preferred_match_score=50,
                risk_score=30,
                risk_flags=[],
                reasoning_summary="Good enough to review but no longer competitive.",
                evidence=["python"],
                confidence="medium",
                matched_must_haves=["python"],
                missing_must_haves=["resume matching"],
                matched_preferences=[],
                negative_signals=["weak retrieval evidence"],
                strengths=["Some Python signal."],
                weaknesses=["Weak retrieval evidence."],
                source_round=1,
            )
        ],
        "reflection": ReflectionAdvice(
            reflection_summary="Continue with one extra tracing term.",
            reflection_rationale="The pool still lacks enough retrieval-specialist resumes.",
            suggest_stop=False,
        ),
        "next_step": "continue to controller round 3",
    }


def test_runtime_reports_round_review_matches_legacy_renderer() -> None:
    runtime = WorkflowRuntime(make_settings())
    payload = _round_review_fixture()

    direct = render_round_review_direct(**payload)
    legacy = runtime._render_round_review(**payload)

    assert direct == legacy
    assert direct == (
        "# Round 2 Review\n"
        "\n"
        "## Controller\n"
        "\n"
        "- Thought summary: Round 2 widened the term surface.\n"
        "- Decision rationale: The first round produced one strong hit but left domain coverage thin.\n"
        "- Query terms: python, resume matching, trace\n"
        "- Keyword query: `python resume matching trace`\n"
        "- Projected provider filters: position='Python Engineer'\n"
        "- Runtime-only constraints: work_content='resume matching'\n"
        "\n"
        "## Location Execution\n"
        "\n"
        "- Mode: `balanced_all`\n"
        "- Allowed locations: 上海, 杭州\n"
        "- Preferred locations: 上海\n"
        "- Priority order: 上海, 杭州\n"
        "- Balanced order: 上海, 杭州\n"
        "- Rotation offset: `1`\n"
        "\n"
        "## Search Outcome\n"
        "\n"
        "- Requested new candidates: `8`\n"
        "- Unique new candidates: `3`\n"
        "- Shortage: `5`\n"
        "- Fetch attempts: `2`\n"
        "- Exhausted reason: `max_pages_reached`\n"
        "- Adapter notes: city dispatch rotated to 杭州 first\n"
        "\n"
        "## City Dispatches\n"
        "\n"
        "- None\n"
        "\n"
        "## Pool Review\n"
        "\n"
        "- Newly scored this round: `3`\n"
        "- Current global top pool: resume-1, resume-2\n"
        "- Newly selected: resume-1\n"
        "- Retained: resume-2\n"
        "- Dropped from global top pool: resume-3\n"
        "- Common drop reasons: Replaced by higher-ranked resumes in the global scored set. x1\n"
        "- Dropped candidates reviewed: `1`\n"
        "\n"
        "## Reflection\n"
        "\n"
        "- Reflection summary: Continue with one extra tracing term.\n"
        "- Reflection rationale: The pool still lacks enough retrieval-specialist resumes.\n"
        "- Reflection decision: `continue`\n"
        "\n"
        "- Next step: `continue to controller round 3`\n"
    )


def test_workflow_runtime_search_once_delegates_to_retrieval_runtime(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    captured: dict[str, object] = {}

    class FakeRetrievalRuntime:
        async def search_once(
            self,
            *,
            attempt_query,
            runtime_constraints,
            round_no,
            attempt_no,
            tracer,
        ) -> SearchResult:
            captured["attempt_query"] = attempt_query
            captured["runtime_constraints"] = runtime_constraints
            captured["round_no"] = round_no
            captured["attempt_no"] = attempt_no
            captured["tracer"] = tracer
            return SearchResult(
                candidates=[_make_candidate("resume-1")],
                diagnostics=["provider search"],
                request_payload={"page": 2, "pageSize": 5},
                raw_candidate_count=1,
                latency_ms=7,
            )

    runtime.retrieval_runtime = FakeRetrievalRuntime()
    tracer = RunTracer(tmp_path / "trace-runtime-search")
    attempt_query = CTSQuery(
        query_role="exploit",
        query_terms=["python", "resume matching"],
        keyword_query="python resume matching",
        native_filters={"age": 3},
        page=2,
        page_size=5,
        rationale="runtime seam test",
        adapter_notes=["runtime location dispatch: 上海"],
    )

    try:
        result = asyncio.run(
            runtime._search_once(
                attempt_query=attempt_query,
                runtime_constraints=[],
                round_no=1,
                attempt_no=2,
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert captured["attempt_query"] is attempt_query
    assert captured["round_no"] == 1
    assert captured["attempt_no"] == 2
    assert result.raw_candidate_count == 1


def test_workflow_runtime_uses_retrieval_runtime_for_round_search(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    tracer = RunTracer(tmp_path / "trace-round-search")
    query_states: list[object] = []
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        keyword_query="python",
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="single",
            allowed_locations=["上海"],
            preferred_locations=[],
            priority_order=[],
            balanced_order=["上海"],
            rotation_offset=0,
            target_new=2,
        ),
        target_new=2,
        rationale="delegation test",
    )
    captured: dict[str, object] = {}

    class FakeRetrievalRuntime:
        async def execute_round_search(
            self,
            *,
            round_no,
            retrieval_plan,
            query_states,
            base_adapter_notes,
            target_new,
            seen_resume_ids,
            seen_dedup_keys,
            tracer,
        ) -> RetrievalExecutionResult:
            captured["round_no"] = round_no
            captured["retrieval_plan"] = retrieval_plan
            captured["query_states"] = query_states
            captured["base_adapter_notes"] = base_adapter_notes
            captured["target_new"] = target_new
            return RetrievalExecutionResult(
                cts_queries=[],
                sent_query_records=[],
                new_candidates=[],
                search_observation=SearchObservation(
                    round_no=1,
                    requested_count=2,
                    raw_candidate_count=0,
                    unique_new_count=0,
                    shortage_count=2,
                    fetch_attempt_count=0,
                ),
                search_attempts=[],
            )

    runtime.retrieval_runtime = FakeRetrievalRuntime()

    try:
        result = asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=2,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert captured["retrieval_plan"] is retrieval_plan
    assert captured["base_adapter_notes"] == []
    assert result[2] == []


def test_runtime_round_search_uses_cts_builder_for_non_location_query(tmp_path: Path, monkeypatch) -> None:
    from seektalent.providers.cts.query_builder import CTSQueryBuildInput

    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    tracer = RunTracer(tmp_path / "trace-builder")
    captured: list[CTSQueryBuildInput] = []

    def fake_build_cts_query(input: CTSQueryBuildInput) -> CTSQuery:
        captured.append(input)
        return CTSQuery(
            query_role=input.query_role,
            query_terms=input.query_terms,
            keyword_query=input.keyword_query,
            native_filters=dict(input.base_filters),
            page=input.page,
            page_size=input.page_size,
            rationale=input.rationale,
            adapter_notes=list(input.adapter_notes),
        )

    monkeypatch.setattr("seektalent.runtime.retrieval_runtime.build_cts_query", fake_build_cts_query)

    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        keyword_query="python",
        projected_provider_filters={"age": 3},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="none",
            allowed_locations=[],
            preferred_locations=[],
            priority_order=[],
            balanced_order=[],
            rotation_offset=0,
            target_new=1,
        ),
        target_new=1,
        rationale="builder seam test",
    )
    query_states = runtime._build_round_query_states(
        round_no=1,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=["python"],
        query_term_pool=[],
        sent_query_history=[],
    )

    try:
        asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=["projection: age mapped to CTS code 3"],
                target_new=1,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert len(captured) == 1
    assert captured[0].base_filters == {"age": 3}
    assert captured[0].city is None


def test_runtime_city_dispatch_passes_city_to_cts_builder(tmp_path: Path, monkeypatch) -> None:
    from seektalent.providers.cts.query_builder import CTSQueryBuildInput

    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    tracer = RunTracer(tmp_path / "trace-city-builder")
    captured: list[CTSQueryBuildInput] = []

    def fake_build_cts_query(input: CTSQueryBuildInput) -> CTSQuery:
        captured.append(input)
        return CTSQuery(
            query_role=input.query_role,
            query_terms=input.query_terms,
            keyword_query=input.keyword_query,
            native_filters={"location": [input.city]} if input.city is not None else {},
            page=input.page,
            page_size=input.page_size,
            rationale=input.rationale,
            adapter_notes=list(input.adapter_notes),
        )

    monkeypatch.setattr("seektalent.runtime.retrieval_runtime.build_cts_query", fake_build_cts_query)

    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        keyword_query="python",
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="single",
            allowed_locations=["上海"],
            preferred_locations=[],
            priority_order=[],
            balanced_order=["上海"],
            rotation_offset=0,
            target_new=1,
        ),
        target_new=1,
        rationale="city builder seam test",
    )
    query_states = runtime._build_round_query_states(
        round_no=1,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=["python"],
        query_term_pool=[],
        sent_query_history=[],
    )

    try:
        asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=1,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert any(input.city == "上海" and input.base_filters == {} for input in captured)


def _fit_scorecard(
    resume_id: str,
    *,
    overall_score: int,
    must_have_match_score: int,
    risk_score: int,
    reasoning_summary: str,
    evidence: list[str],
    matched_must_haves: list[str],
    strengths: list[str],
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=overall_score,
        must_have_match_score=must_have_match_score,
        preferred_match_score=60,
        risk_score=risk_score,
        risk_flags=[],
        reasoning_summary=reasoning_summary,
        evidence=evidence,
        confidence="high",
        matched_must_haves=matched_must_haves,
        missing_must_haves=[],
        matched_preferences=[],
        negative_signals=[],
        strengths=strengths,
        weaknesses=[],
        source_round=1,
    )


def _python_feedback_seed_scorecards() -> dict[str, ScoredCandidate]:
    return {
        "fit-1": _fit_scorecard(
            "fit-1",
            overall_score=90,
            must_have_match_score=82,
            risk_score=15,
            reasoning_summary="python",
            evidence=["python", "resume matching"],
            matched_must_haves=["python"],
            strengths=["python"],
        ),
        "fit-2": _fit_scorecard(
            "fit-2",
            overall_score=88,
            must_have_match_score=80,
            risk_score=18,
            reasoning_summary="python",
            evidence=["python", "resume matching"],
            matched_must_haves=["python"],
            strengths=["python"],
        ),
    }


def test_runtime_updates_run_state_across_rounds(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=2,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        top_candidates, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=progress_events.append)
        )
    finally:
        tracer.close()

    assert rounds_executed == 2
    assert stop_reason == "max_rounds_reached"
    assert terminal_controller_round is None
    assert len(top_candidates) > 0
    assert run_state.retrieval_state.current_plan_version == 2
    assert [item.round_no for item in run_state.retrieval_state.sent_query_history] == [1, 2, 2]
    assert [item.city for item in run_state.retrieval_state.sent_query_history] == ["上海", "上海", "上海"]
    assert [item.query_role for item in run_state.retrieval_state.sent_query_history] == [
        "exploit",
        "exploit",
        "explore",
    ]
    assert run_state.retrieval_state.sent_query_history[1].query_terms == ["python", "resume matching", "trace"]
    assert run_state.retrieval_state.sent_query_history[2].query_terms == ["python", "trace"]
    assert all(
        sum(1 for term in item.query_terms if term == "python") == 1
        for item in run_state.retrieval_state.sent_query_history
    )
    assert all(len(item.query_terms) <= 3 for item in run_state.retrieval_state.sent_query_history)
    assert len(run_state.retrieval_state.reflection_keyword_advice_history) == 2
    assert len(run_state.retrieval_state.reflection_filter_advice_history) == 2
    assert [item.term for item in run_state.retrieval_state.query_term_pool] == ["python", "resume matching", "trace"]
    assert len(run_state.round_history) == 2
    assert run_state.round_history[0].reflection_advice is not None
    assert run_state.round_history[1].reflection_advice is not None
    assert run_state.round_history[1].reflection_advice.suggest_stop is True
    assert run_state.round_history[1].controller_decision.response_to_reflection
    round_02_queries = [
        CTSQuery.model_validate(item)
        for item in json.loads(
            (tracer.run_dir / "rounds" / "round_02" / "cts_queries.json").read_text(encoding="utf-8")
        )
    ]
    round_02_normalized = [
        json.loads(line)
        for line in (tracer.run_dir / "rounds" / "round_02" / "scoring_input_refs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item.query_role for item in round_02_queries] == ["exploit", "explore"]
    assert run_state.round_history[1].search_observation is not None
    assert run_state.round_history[1].search_observation.unique_new_count <= 10
    assert len(run_state.round_history[1].search_observation.new_resume_ids) <= 10
    assert len(round_02_normalized) == run_state.round_history[1].search_observation.unique_new_count
    round_01_top_id = run_state.round_history[0].top_candidates[0].resume_id
    assert run_state.scorecards_by_resume_id[round_01_top_id].overall_score == 90
    assert run_state.round_history[1].cts_queries == round_02_queries
    round_02_search_started = next(
        event for event in progress_events if event.type == "search_started" and event.round_no == 2
    )
    round_02_search_completed = next(
        event for event in progress_events if event.type == "search_completed" and event.round_no == 2
    )
    assert round_02_search_started.payload["planned_queries"] == [
        {
            "query_role": "exploit",
            "query_terms": ["python", "resume matching", "trace"],
            "keyword_query": 'python "resume matching" trace',
        },
        {"query_role": "explore", "query_terms": ["python", "trace"], "keyword_query": "python trace"},
    ]
    assert round_02_search_completed.payload["executed_queries"] == [
        {
            "query_role": "exploit",
            "query_terms": ["python", "resume matching", "trace"],
            "keyword_query": 'python "resume matching" trace',
        },
        {"query_role": "explore", "query_terms": ["python", "trace"], "keyword_query": "python trace"},
    ]


def test_runtime_reflection_does_not_mutate_query_term_pool(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = SingleFamilyRequirementExtractor(include_reserve=True)
    runtime_any.reflection_critic = MutationAttemptReflection()
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer))
    finally:
        tracer.close()

    terms = {item.term: item for item in run_state.retrieval_state.query_term_pool}
    assert terms["trace"].active is False
    assert terms["trace"].priority == 3
    assert terms["resume matching"].active is True
    assert terms["resume matching"].priority == 2
    assert len(run_state.retrieval_state.reflection_keyword_advice_history) == 1
    advice = run_state.retrieval_state.reflection_keyword_advice_history[0]
    assert advice.suggested_activate_terms == ["trace"]
    assert advice.suggested_drop_terms == ["resume matching"]
    assert advice.suggested_deprioritize_terms == ["resume matching"]


def test_runtime_builds_plan_for_reflection_backed_inactive_term(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=2,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = SingleFamilyRequirementExtractor(include_reserve=True)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer))
    finally:
        tracer.close()

    round_02_plan = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "retrieval_plan.json").read_text(encoding="utf-8")
    )
    assert round_02_plan["query_terms"] == ["python", "resume matching", "trace"]
    assert {item.term: item for item in run_state.retrieval_state.query_term_pool}["trace"].active is False


class RecordingScorer:
    def __init__(self) -> None:
        self.resume_ids: list[str] = []
        self.runtime_only_constraints: list[list[RuntimeConstraint]] = []

    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        self.resume_ids.extend(context.normalized_resume.resume_id for context in contexts)
        self.runtime_only_constraints.extend(context.runtime_only_constraints for context in contexts)
        return (
            [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=95,
                    must_have_match_score=90,
                    preferred_match_score=70,
                    risk_score=5,
                    risk_flags=[],
                    reasoning_summary="Fresh candidate scored once.",
                    evidence=["python"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["resume matching"],
                    negative_signals=[],
                    strengths=["Fresh strong match."],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
                for context in contexts
            ],
            [],
        )


def test_score_round_keeps_existing_scorecards_and_only_scores_new_resumes(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    cast(Any, runtime).resume_scorer = RecordingScorer()
    _, requirement_sheet = asyncio.run(StubRequirementExtractor().extract_with_draft(input_truth=None))
    existing = ScoredCandidate(
        resume_id="seen",
        fit_bucket="fit",
        overall_score=80,
        must_have_match_score=78,
        preferred_match_score=60,
        risk_score=12,
        risk_flags=[],
        reasoning_summary="Existing score should stay untouched.",
        evidence=["python"],
        confidence="high",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["resume matching"],
        negative_signals=[],
        strengths=["Existing top match."],
        weaknesses=[],
        source_round=1,
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Senior Python Engineer",
            jd="JD",
            notes="Notes",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        candidate_store={"seen": _make_candidate("seen", source_round=1)},
        scorecards_by_resume_id={"seen": existing},
        top_pool_ids=["seen"],
    )
    tracer = RunTracer(tmp_path / "trace-runs")
    runtime_only_constraints = [
        RuntimeConstraint(
            field="age_requirement",
            normalized_value=["max=35"],
            source="notes",
            rationale="Age not projected to CTS.",
            blocking=False,
        )
    ]

    try:
        top_candidates, pool_decisions, dropped_candidates = asyncio.run(
            runtime._score_round(
                round_no=2,
                new_candidates=[_make_candidate("seen", source_round=2), _make_candidate("fresh", source_round=2)],
                run_state=run_state,
                tracer=tracer,
                runtime_only_constraints=runtime_only_constraints,
            )
        )
    finally:
        tracer.close()

    assert cast(Any, runtime).resume_scorer.resume_ids == ["fresh"]
    assert cast(Any, runtime).resume_scorer.runtime_only_constraints == [runtime_only_constraints]
    assert run_state.scorecards_by_resume_id["seen"].overall_score == 80
    assert run_state.scorecards_by_resume_id["fresh"].overall_score == 95
    assert [item.resume_id for item in top_candidates] == ["fresh", "seen"]
    assert [item.decision for item in pool_decisions] == ["selected", "retained"]
    assert dropped_candidates == []


def test_materialize_candidates_requires_candidate_store_entry(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    scored = ScoredCandidate(
        resume_id="missing",
        fit_bucket="fit",
        overall_score=90,
        must_have_match_score=88,
        preferred_match_score=70,
        risk_score=8,
        reasoning_summary="Scored candidate without source resume.",
        confidence="high",
        source_round=1,
    )

    with pytest.raises(KeyError, match="missing"):
        runtime._materialize_candidates(scored_candidates=[scored], candidate_store={})


def test_workflow_runtime_uses_retrieval_runtime_module_for_retrieval_execution(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))

    assert isinstance(runtime.retrieval_runtime, RetrievalRuntime)
    assert runtime.retrieval_runtime.settings is runtime.settings
    assert runtime.retrieval_runtime.retrieval_service is runtime.retrieval_service


def test_workflow_runtime_retrieval_service_rebind_syncs_retrieval_runtime(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    fake_retrieval_service = cast(Any, object())

    runtime.retrieval_service = fake_retrieval_service

    assert runtime.retrieval_service is fake_retrieval_service
    assert runtime.retrieval_runtime.retrieval_service is fake_retrieval_service


def test_workflow_runtime_retrieval_runtime_rejects_direct_rebinding(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))

    with pytest.raises(FrozenInstanceError):
        runtime.retrieval_runtime.retrieval_service = cast(Any, object())


def test_runtime_records_terminal_controller_round_separately(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=3,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopAfterSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    assert rounds_executed == 2
    assert stop_reason == "controller_stop"
    assert len(run_state.round_history) == 2
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 3
    assert terminal_controller_round.controller_decision.action == "stop"
    assert terminal_controller_round.stop_guidance.can_stop is True
    assert (tracer.run_dir / "rounds" / "round_03" / "controller_decision.json").exists()
    assert not (tracer.run_dir / "rounds" / "round_03" / "retrieval_plan.json").exists()
    assert not (tracer.run_dir / "rounds" / "round_03" / "search_observation.json").exists()
    assert not (tracer.run_dir / "rounds" / "round_03" / "reflection_advice.json").exists()


def test_runtime_forces_continue_when_stop_guidance_blocks_stop(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=3,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopOnSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = _python_feedback_seed_scorecards()
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )

    assert round_02_decision["action"] == "search_cts"
    assert "admitted families remain untried" in round_02_decision["decision_rationale"]
    assert round_02_decision["proposed_query_terms"] == ["python", "trace", "resume matching"]
    assert rounds_executed == 2
    assert stop_reason == "controller_stop"
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 3
    assert terminal_controller_round.stop_guidance.can_stop is True


def test_runtime_forces_broaden_with_inactive_admitted_reserve_term(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=True)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_02_context = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_context.json").read_text(encoding="utf-8")
    )
    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )
    round_02_plan = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "retrieval_plan.json").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "rescue_decision.json").read_text(encoding="utf-8")
    )

    assert round_02_context["stop_guidance"]["quality_gate_status"] == "broaden_required"
    assert rescue_decision["selected_lane"] == "reserve_broaden"
    assert rescue_decision["forced_query_terms"] == ["python", "trace"]
    assert round_02_decision["action"] == "search_cts"
    assert "Runtime broaden" in round_02_decision["decision_rationale"]
    assert round_02_decision["proposed_query_terms"] == ["python", "trace"]
    assert round_02_plan["query_terms"] == ["python", "trace"]
    assert [item.term for item in run_state.retrieval_state.query_term_pool if item.active] == [
        "python",
        "resume matching",
        "trace",
    ]
    assert stop_reason == "controller_stop"
    assert rounds_executed == 3
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 4
    assert terminal_controller_round.stop_guidance.quality_gate_status == "low_quality_exhausted"
    assert terminal_controller_round.stop_guidance.broadening_attempted is True


def test_runtime_forces_anchor_only_broaden_when_no_reserve_term_remains(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )
    round_02_plan = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "retrieval_plan.json").read_text(encoding="utf-8")
    )
    round_02_queries = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "cts_queries.json").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "rescue_decision.json").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "anchor_only"
    assert rescue_decision["forced_query_terms"] == ["python"]
    assert round_02_decision["proposed_query_terms"] == ["python"]
    assert round_02_plan["query_terms"] == ["python"]
    assert [item["query_role"] for item in round_02_queries] == ["exploit"]
    assert run_state.retrieval_state.sent_query_history[-1].query_terms == ["python"]
    assert stop_reason == "controller_stop"
    assert rounds_executed == 2
    assert terminal_controller_round is not None
    assert terminal_controller_round.stop_guidance.quality_gate_status == "low_quality_exhausted"
    assert terminal_controller_round.stop_guidance.broadening_attempted is True


def test_runtime_falls_back_to_anchor_only_when_candidate_feedback_has_no_safe_term(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
        candidate_feedback_enabled=True,
        company_discovery_enabled=False,
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = _python_feedback_seed_scorecards()
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer))
    finally:
        tracer.close()

    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "rescue_decision.json").read_text(encoding="utf-8")
    )
    feedback_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "candidate_feedback_decision.json").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "anchor_only"
    assert {"lane": "candidate_feedback", "reason": "no_safe_feedback_term"} in rescue_decision["skipped_lanes"]
    assert {"lane": "web_company_discovery", "reason": "disabled"} in rescue_decision["skipped_lanes"]
    assert feedback_decision["accepted_term"] is None
    assert round_02_decision["proposed_query_terms"] == ["python"]


def test_runtime_uses_candidate_feedback_before_anchor_only(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
        candidate_feedback_enabled=True,
        company_discovery_enabled=False,
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = {
            "fit-1": _fit_scorecard(
                "fit-1",
                overall_score=90,
                must_have_match_score=82,
                risk_score=15,
                reasoning_summary="Built LangGraph workflow orchestration.",
                evidence=["LangGraph workflow orchestration and tool calling."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph", "tool calling"],
            ),
            "fit-2": _fit_scorecard(
                "fit-2",
                overall_score=88,
                must_have_match_score=80,
                risk_score=18,
                reasoning_summary="Used LangGraph for Agent workflow.",
                evidence=["LangGraph and RAG workflow implementation."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph"],
            ),
        }
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=progress_events.append)
        )
    finally:
        tracer.close()

    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "rescue_decision.json").read_text(encoding="utf-8")
    )
    feedback_terms = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "candidate_feedback_terms.json").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "candidate_feedback"
    assert round_02_decision["proposed_query_terms"] == ["python", "LangGraph"]
    assert feedback_terms["accepted_term"]["term"] == "LangGraph"
    assert any(
        event.type == "rescue_lane_completed" and event.payload.get("accepted_term") == "LangGraph"
        for event in progress_events
    )
    assert run_state.retrieval_state.candidate_feedback_attempted is True
    assert stop_reason == "controller_stop"
    assert rounds_executed == 3
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 4


def test_runtime_uses_company_discovery_after_feedback_unavailable(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
        candidate_feedback_enabled=True,
        company_discovery_enabled=True,
        bocha_api_key="bocha-key",
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    runtime_any = cast(Any, runtime)
    runtime_any.company_discovery = StubCompanyDiscovery()
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = _python_feedback_seed_scorecards()
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=progress_events.append)
        )
    finally:
        tracer.close()

    rescue_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "rescue_decision.json").read_text(encoding="utf-8")
    )
    controller_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )
    discovery_artifact = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "company_discovery_result.json").read_text(encoding="utf-8")
    )
    round_dir = tracer.run_dir / "rounds" / "round_02"
    search_queries = json.loads((round_dir / "company_search_queries.json").read_text(encoding="utf-8"))
    search_results = json.loads((round_dir / "company_search_results.json").read_text(encoding="utf-8"))
    reranked_results = json.loads((round_dir / "company_search_rerank.json").read_text(encoding="utf-8"))
    page_reads = json.loads((round_dir / "company_page_reads.json").read_text(encoding="utf-8"))
    evidence_cards = json.loads((round_dir / "company_evidence_cards.json").read_text(encoding="utf-8"))
    cts_queries = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "cts_queries.json").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "web_company_discovery"
    assert {"lane": "candidate_feedback", "reason": "no_safe_feedback_term"} in rescue_decision["skipped_lanes"]
    assert controller_decision["proposed_query_terms"] == ["python", "火山引擎"]
    assert [item["query_role"] for item in cts_queries] == ["exploit"]
    assert cts_queries[0]["query_terms"] == ["python", "火山引擎"]
    assert any(
        event.type == "company_discovery_completed"
        and event.payload.get("accepted_company_count") == 1
        and event.payload.get("search_queries") == ["大模型平台 推理服务 公司"]
        and event.payload.get("reranked_pages") == ["0.91 火山引擎大模型服务平台"]
        and event.payload.get("page_titles") == ["AI infra company map"]
        for event in progress_events
    )
    assert discovery_artifact["plan"]["inferred_targets"][0]["name"] == "火山引擎"
    assert search_queries[0]["query"] == "大模型平台 推理服务 公司"
    assert search_results[0]["title"] == "火山引擎大模型服务平台"
    assert reranked_results[0]["score"] == 0.91
    assert page_reads[0]["title"] == "AI infra company map"
    assert evidence_cards[0]["name"] == "火山引擎"
    assert run_state.retrieval_state.company_discovery_attempted is True
    assert run_state.retrieval_state.target_company_plan is not None
    assert stop_reason == "controller_stop"
    assert rounds_executed == 3
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 4


def test_runtime_min_rounds_count_completed_retrieval_rounds(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=3,
        max_rounds=4,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopAfterSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_03_context = json.loads(
        (tracer.run_dir / "rounds" / "round_03" / "controller_context.json").read_text(encoding="utf-8")
    )
    round_03_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_03" / "controller_decision.json").read_text(encoding="utf-8")
    )

    assert round_03_context["budget"]["retrieval_rounds_completed"] == 2
    assert round_03_context["stop_guidance"]["can_stop"] is False
    assert "2 retrieval rounds completed" in round_03_context["stop_guidance"]["reason"]
    assert round_03_decision["action"] == "search_cts"
    assert "2 retrieval rounds completed" in round_03_decision["decision_rationale"]
    assert (tracer.run_dir / "rounds" / "round_03" / "retrieval_plan.json").exists()
    assert rounds_executed == 3
    assert len(run_state.round_history) == 3
    assert stop_reason == "controller_stop"
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 4
    assert terminal_controller_round.stop_guidance.can_stop is True


def test_runtime_degrades_to_single_query_when_no_distinct_explore_query_exists(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    requirement_sheet = RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "resume matching"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="resume matching",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )
    retrieval_plan = build_round_retrieval_plan(
        plan_version=2,
        round_no=2,
        query_terms=["python", "resume matching"],
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=requirement_sheet.hard_constraints.locations,
            preferred_locations=requirement_sheet.preferences.preferred_locations,
            round_no=2,
            target_new=10,
        ),
        target_new=10,
        rationale="single query fallback",
    )

    query_states = runtime._build_round_query_states(
        round_no=2,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["python", "resume matching"],
                keyword_query='python "resume matching"',
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )

    assert [item.query_role for item in query_states] == ["exploit"]


def test_runtime_diagnostics_does_not_label_collapsed_multi_anchor_query_after_round_one(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    requirement_sheet = RequirementSheet(
        role_title="Backend Platform Engineer",
        title_anchor_terms=["Backend", "Platform"],
        title_anchor_rationale="Title contributes both backend and platform anchors.",
        role_summary="Build backend platform services.",
        must_have_capabilities=["Python"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Backend",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.backend",
            ),
            QueryTermCandidate(
                term="Platform",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.platform",
            ),
            QueryTermCandidate(
                term="Python",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="core_skill",
                queryability="admitted",
                family="skill.python",
            ),
        ],
        scoring_rationale="Prefer backend platform resumes with Python signal.",
    )
    round_state = RoundState(
        round_no=2,
        controller_decision=SearchControllerDecision(
            thought_summary="Round 2 search.",
            action="search_cts",
            decision_rationale="Used a collapsed primary-plus-domain query.",
            proposed_query_terms=["Backend", "Python"],
            proposed_filter_plan=ProposedFilterPlan(),
        ),
        retrieval_plan=RoundRetrievalPlan(
            plan_version=1,
            round_no=2,
            query_terms=["Backend", "Python"],
            keyword_query="Backend Python",
            projected_provider_filters={},
            runtime_only_constraints=[],
            location_execution_plan=LocationExecutionPlan(
                mode="single",
                allowed_locations=["上海"],
                preferred_locations=[],
                priority_order=[],
                balanced_order=["上海"],
                rotation_offset=0,
                target_new=10,
            ),
            target_new=10,
            rationale="round 2",
        ),
        search_observation=SearchObservation(
            round_no=2,
            requested_count=10,
            raw_candidate_count=0,
            unique_new_count=0,
            shortage_count=10,
            fetch_attempt_count=1,
        ),
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Backend Platform Engineer",
            jd="Build backend platform services.",
            notes="Prefer Python signal.",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        round_history=[round_state],
    )

    diagnostics = runtime._build_round_search_diagnostics(run_state=run_state, round_state=round_state)

    assert diagnostics["audit_labels"] == []


def test_runtime_helpers_use_primary_anchor_and_skip_secondary_title_anchor_reserve(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    retrieval_state = RetrievalState(
        current_plan_version=1,
        query_term_pool=[
            QueryTermCandidate(
                term="Backend",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.backend",
            ),
            QueryTermCandidate(
                term="Platform",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.platform",
            ),
            QueryTermCandidate(
                term="Python",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                active=False,
                retrieval_role="core_skill",
                queryability="admitted",
                family="skill.python",
            ),
        ],
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["Backend", "Platform"],
                keyword_query="Backend Platform",
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )

    assert runtime._active_admitted_anchor(retrieval_state.query_term_pool).term == "Backend"
    reserve = runtime._untried_admitted_non_anchor_reserve(retrieval_state)
    assert reserve is not None
    assert reserve.term == "Python"


def test_search_once_routes_through_retrieval_service_with_provider_filters(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    captured: dict[str, object] = {}
    runtime_constraints = [
        RuntimeConstraint(
            field="school_type_requirement",
            normalized_value=["985", "211"],
            source="jd",
            rationale="School type note",
            blocking=True,
        )
    ]

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
        ):
            captured.update(
                {
                    "query_terms": query_terms,
                    "query_role": query_role,
                    "keyword_query": keyword_query,
                    "adapter_notes": adapter_notes,
                    "provider_filters": provider_filters,
                    "runtime_constraints": runtime_constraints,
                    "page_size": page_size,
                    "round_no": round_no,
                    "trace_id": trace_id,
                    "fetch_mode": fetch_mode,
                    "cursor": cursor,
                }
            )
            return SearchResult(
                candidates=[_make_candidate("resume-1")],
                diagnostics=["provider search"],
                request_payload={"page": 2, "pageSize": 5, "age": 3},
                raw_candidate_count=1,
                latency_ms=7,
            )

    runtime.retrieval_service = FakeRetrievalService()
    attempt_query = CTSQuery(
        query_role="exploit",
        query_terms=["python", "resume matching"],
        keyword_query="python resume matching",
        native_filters={"age": 3},
        page=2,
        page_size=5,
        rationale="runtime seam test",
        adapter_notes=["runtime location dispatch: 上海"],
    )
    tracer = RunTracer(tmp_path / "trace-runtime-search")

    try:
        result = asyncio.run(
            runtime._search_once(
                attempt_query=attempt_query,
                runtime_constraints=runtime_constraints,
                round_no=1,
                attempt_no=2,
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert captured["query_terms"] == ["python", "resume matching"]
    assert captured["query_role"] == "primary"
    assert captured["keyword_query"] == "python resume matching"
    assert captured["adapter_notes"] == ["runtime location dispatch: 上海"]
    assert captured["provider_filters"] == {"age": 3}
    assert captured["runtime_constraints"] == runtime_constraints
    assert captured["page_size"] == 5
    assert captured["round_no"] == 1
    assert captured["fetch_mode"] == "summary"
    assert captured["cursor"] == "2"
    assert isinstance(captured["trace_id"], str)
    assert captured["trace_id"].endswith("-r1-a2")
    assert result.request_payload == {"page": 2, "pageSize": 5, "age": 3}
    assert result.raw_candidate_count == 1
    assert result.latency_ms == 7


def test_runtime_diagnostics_does_not_flag_compiled_short_title_anchors_as_collapsed(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    requirement_sheet = RequirementSheet(
        role_title="Backend Platform Engineer",
        title_anchor_terms=["Backend Engineer", "Platform Engineer"],
        title_anchor_rationale="Compiled short anchors preserve both backend and platform signals.",
        role_summary="Build backend platform services.",
        must_have_capabilities=["Python"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Backend",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.backend",
            ),
            QueryTermCandidate(
                term="Platform",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.platform",
            ),
        ],
        scoring_rationale="Prefer backend platform resumes with Python signal.",
    )
    round_state = RoundState(
        round_no=1,
        controller_decision=SearchControllerDecision(
            thought_summary="Round 1 search.",
            action="search_cts",
            decision_rationale="Used both compiled title anchors.",
            proposed_query_terms=["Backend", "Platform"],
            proposed_filter_plan=ProposedFilterPlan(),
        ),
        retrieval_plan=RoundRetrievalPlan(
            plan_version=1,
            round_no=1,
            query_terms=["Backend", "Platform"],
            keyword_query="Backend Platform",
            projected_provider_filters={},
            runtime_only_constraints=[],
            location_execution_plan=LocationExecutionPlan(
                mode="single",
                allowed_locations=["上海"],
                preferred_locations=[],
                priority_order=[],
                balanced_order=["上海"],
                rotation_offset=0,
                target_new=10,
            ),
            target_new=10,
            rationale="round 1",
        ),
        search_observation=SearchObservation(
            round_no=1,
            requested_count=10,
            raw_candidate_count=0,
            unique_new_count=0,
            shortage_count=10,
            fetch_attempt_count=1,
        ),
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Backend Platform Engineer",
            jd="Build backend platform services.",
            notes="Prefer Python signal.",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        round_history=[round_state],
    )

    diagnostics = runtime._build_round_search_diagnostics(run_state=run_state, round_state=round_state)

    assert diagnostics["audit_labels"] == []
