from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from seektalent.clients.cts_client import CTSFetchResult
from seektalent.models import (
    CareerStabilityProfile,
    NodeRewardBreakdown_t,
    CrossoverGuardThresholds,
    FitGateConstraints,
    FrontierNode_t,
    FrontierState_t,
    FusionWeights,
    HardConstraints,
    PenaltyWeights,
    RequirementPreferences,
    RetrievedCandidate_t,
    RerankerCalibration,
    RuntimeSearchBudget,
    RuntimeTermBudgetPolicy,
    ScoringCandidate_t,
    ScoringPolicy,
    SearchControllerDecision_t,
    SearchExecutionResult_t,
    SearchObservation,
    SearchPageStatistics,
)
from seektalent.search_ops import (
    execute_search_plan,
    execute_search_plan_sidecar,
    materialize_search_execution_plan,
    score_search_results,
)
from seektalent_rerank.models import RerankResponse, RerankResult


def _requirement_sheet():
    from seektalent.models import RequirementSheet

    return RequirementSheet(
        role_title="Senior Python Agent Engineer",
        role_summary="Build ranking systems.",
        must_have_capabilities=["Python backend", "retrieval", "ranking", "LLM application", "workflow orchestration"],
        preferred_capabilities=["to-b delivery"],
        exclusion_signals=[],
        hard_constraints=HardConstraints(),
        preferences=RequirementPreferences(),
        scoring_rationale="must-have first",
    )


def _frontier_state(*, remaining_budget: int = 5) -> FrontierState_t:
    parent = FrontierNode_t(
        frontier_node_id="seed_agent_core",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python", "rag", "agent", "workflow", "backend"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        negative_terms=["frontend"],
        status="open",
    )
    donor = FrontierNode_t(
        frontier_node_id="child_search_domain_01",
        parent_frontier_node_id="root",
        donor_frontier_node_id=None,
        selected_operator_name="core_precision",
        node_query_term_pool=["rag", "retrieval engineer", "ranking"],
        knowledge_pack_ids=["search_ranking_retrieval_engineering"],
        negative_terms=["sales"],
        reward_breakdown=NodeRewardBreakdown_t(
            delta_top_three=0.0,
            must_have_gain=0.0,
            new_fit_yield=0.0,
            novelty=0.0,
            usefulness=0.0,
            diversity=0.0,
            stability_risk_penalty=0.0,
            hard_constraint_violation=0.0,
            duplicate_penalty=0.0,
            cost_penalty=0.0,
            reward_score=2.0,
        ),
        status="open",
    )
    return FrontierState_t(
        frontier_nodes={
            parent.frontier_node_id: parent,
            donor.frontier_node_id: donor,
        },
        open_frontier_node_ids=[parent.frontier_node_id, donor.frontier_node_id],
        remaining_budget=remaining_budget,
    )


def _decision(*, selected_operator_name: str = "core_precision", operator_args: dict | None = None) -> SearchControllerDecision_t:
    return SearchControllerDecision_t(
        action="search_cts",
        target_frontier_node_id="seed_agent_core",
        selected_operator_name=selected_operator_name,
        operator_args=operator_args or {"additional_terms": ["ranking"]},
        expected_gain_hypothesis="Expand relevant coverage.",
    )


@pytest.mark.parametrize(
    ("remaining_budget", "expected_terms"),
    [
        (5, ["python", "rag", "agent", "workflow", "backend", "ranking"]),
        (2, ["python", "rag", "agent", "workflow", "backend"]),
        (1, ["python", "rag", "agent", "workflow"]),
    ],
)
def test_materialize_search_execution_plan_clamps_terms_by_budget(
    remaining_budget: int,
    expected_terms: list[str],
) -> None:
    plan = materialize_search_execution_plan(
        _frontier_state(remaining_budget=remaining_budget),
        _requirement_sheet(),
        _decision(operator_args={"additional_terms": ["ranking", "python"], "target_new_candidate_count": 50}),
        RuntimeTermBudgetPolicy(),
        RuntimeSearchBudget(),
        CrossoverGuardThresholds(),
    )

    assert plan.query_terms == expected_terms
    assert plan.target_new_candidate_count == 20
    assert plan.runtime_only_constraints.must_have_keywords[:5] == [
        "Python backend",
        "retrieval",
        "ranking",
        "LLM application",
        "workflow orchestration",
    ]
    assert plan.runtime_only_constraints.negative_keywords == ["frontend"]
    assert plan.derived_position == "Senior Python Agent Engineer"
    assert plan.derived_work_content == "Python backend | retrieval | ranking | LLM application"
    assert plan.child_frontier_node_stub.frontier_node_id == f"child_seed_agent_core_{plan.semantic_hash[:8]}"


def test_materialize_search_execution_plan_supports_crossover_compose() -> None:
    plan = materialize_search_execution_plan(
        _frontier_state(),
        _requirement_sheet(),
        _decision(
            selected_operator_name="crossover_compose",
            operator_args={
                "donor_frontier_node_id": "child_search_domain_01",
                "shared_anchor_terms": ["rag", "missing"],
                "donor_terms_used": ["retrieval engineer", "ranking", "python"],
            },
        ),
        RuntimeTermBudgetPolicy(),
        RuntimeSearchBudget(),
        CrossoverGuardThresholds(),
    )

    assert plan.query_terms == ["rag", "retrieval engineer", "ranking"]
    assert plan.knowledge_pack_ids == ["llm_agent_rag_engineering"]
    assert plan.runtime_only_constraints.negative_keywords == ["frontend", "sales"]
    assert plan.child_frontier_node_stub.donor_frontier_node_id == "child_search_domain_01"


def test_materialize_search_execution_plan_fails_without_shared_anchor() -> None:
    with pytest.raises(ValueError, match="crossover_requires_shared_anchor"):
        materialize_search_execution_plan(
            _frontier_state(),
            _requirement_sheet(),
            _decision(
                selected_operator_name="crossover_compose",
                operator_args={
                    "donor_frontier_node_id": "child_search_domain_01",
                    "shared_anchor_terms": [],
                    "donor_terms_used": ["retrieval engineer", "ranking"],
                },
            ),
            RuntimeTermBudgetPolicy(),
            RuntimeSearchBudget(),
            CrossoverGuardThresholds(),
        )


def _candidate(
    candidate_id: str,
    *,
    search_text: str,
    work_summaries: list[str] | None = None,
    work_experience_list: list[dict[str, str]] | None = None,
) -> RetrievedCandidate_t:
    work_experience_list = work_experience_list or []
    return RetrievedCandidate_t(
        candidate_id=candidate_id,
        now_location="上海",
        expected_location="上海",
        years_of_experience_raw=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=[
            " | ".join(part for part in [item.get("company"), item.get("title"), item.get("summary")] if part)
            for item in work_experience_list
        ]
        or ["TestCo | Python Engineer | Built retrieval ranking flows."],
        project_names=["retrieval platform"],
        work_summaries=work_summaries or ["python", "ranking"],
        search_text=search_text,
        raw_payload={"title": "Python Engineer", "workExperienceList": work_experience_list},
    )


@dataclass
class FakeCTSClient:
    result: CTSFetchResult
    seen_plans: list[object] = field(default_factory=list)

    async def search(self, plan, *, trace_id: str = "") -> CTSFetchResult:
        del trace_id
        self.seen_plans.append(plan)
        return self.result


def test_execute_search_plan_uses_existing_candidate_projection_flow() -> None:
    client = FakeCTSClient(
        result=CTSFetchResult(
            request_payload={},
            candidates=[
                _candidate("keep", search_text="python retrieval ranking"),
                _candidate("drop", search_text="frontend react only", work_summaries=["frontend"]),
                _candidate("keep", search_text="python retrieval ranking duplicate"),
            ],
            raw_candidate_count=3,
            latency_ms=11,
        )
    )
    plan = materialize_search_execution_plan(
        _frontier_state(),
        _requirement_sheet(),
        _decision(operator_args={"additional_terms": ["ranking"], "target_new_candidate_count": 2}),
        RuntimeTermBudgetPolicy(),
        RuntimeSearchBudget(),
        CrossoverGuardThresholds(),
    )

    result = asyncio.run(execute_search_plan(plan, client))

    assert client.seen_plans == [plan]
    assert [candidate.candidate_id for candidate in result.raw_candidates] == ["keep", "drop", "keep"]
    assert [candidate.candidate_id for candidate in result.deduplicated_candidates] == ["keep"]
    assert [candidate.candidate_id for candidate in result.scoring_candidates] == ["keep"]
    assert result.search_page_statistics.pages_fetched == 2
    assert result.search_page_statistics.duplicate_rate == pytest.approx(2 / 3)
    assert result.search_observation.shortage_after_last_page is True
    assert result.scoring_candidates[0].career_stability_profile.confidence_score > 0


def test_execute_search_plan_fails_when_cts_latency_is_missing() -> None:
    client = FakeCTSClient(
        result=CTSFetchResult(
            request_payload={},
            candidates=[],
            raw_candidate_count=0,
            latency_ms=None,
        )
    )
    plan = materialize_search_execution_plan(
        _frontier_state(),
        _requirement_sheet(),
        _decision(),
        RuntimeTermBudgetPolicy(),
        RuntimeSearchBudget(),
        CrossoverGuardThresholds(),
    )

    with pytest.raises(ValueError, match="latency_ms"):
        asyncio.run(execute_search_plan(plan, client))


def test_execute_search_plan_sidecar_preserves_runtime_audit_and_school_type_fallback() -> None:
    client = FakeCTSClient(
        result=CTSFetchResult(
            request_payload={},
            candidates=[
                _candidate("keep", search_text="python retrieval ranking", work_summaries=["python", "ranking"]),
                _candidate(
                    "drop",
                    search_text="python retrieval ranking",
                    work_summaries=["python", "ranking"],
                ).model_copy(update={"education_summaries": ["普通学校 计算机 本科"]}),
                _candidate("keep", search_text="python retrieval ranking duplicate", work_summaries=["python", "ranking"]),
            ],
            raw_candidate_count=3,
            latency_ms=13,
        )
    )
    requirement_sheet = _requirement_sheet().model_copy(
        update={"hard_constraints": HardConstraints(school_type_requirement=["985", "海外"])}
    )
    plan = materialize_search_execution_plan(
        _frontier_state(),
        requirement_sheet,
        _decision(operator_args={"additional_terms": ["ranking"], "target_new_candidate_count": 2}),
        RuntimeTermBudgetPolicy(),
        RuntimeSearchBudget(),
        CrossoverGuardThresholds(),
    )

    sidecar = asyncio.run(execute_search_plan_sidecar(plan, client))

    assert [candidate.candidate_id for candidate in sidecar.execution_result.deduplicated_candidates] == ["keep"]
    assert sidecar.runtime_audit_tags == {"keep": ["retrieval", "ranking", "python"]}
    assert sidecar.execution_result.search_page_statistics.pages_fetched == 2


def test_execute_search_plan_sidecar_counts_empty_cts_request_as_zero_pages() -> None:
    client = FakeCTSClient(
        result=CTSFetchResult(
            request_payload={},
            candidates=[],
            raw_candidate_count=0,
            latency_ms=5,
        )
    )
    plan = materialize_search_execution_plan(
        _frontier_state(),
        _requirement_sheet(),
        _decision(operator_args={"additional_terms": ["ranking"], "target_new_candidate_count": 10}),
        RuntimeTermBudgetPolicy(),
        RuntimeSearchBudget(),
        CrossoverGuardThresholds(),
    )

    sidecar = asyncio.run(execute_search_plan_sidecar(plan, client))

    assert sidecar.execution_result.search_page_statistics.pages_fetched == 0


def _scoring_policy() -> ScoringPolicy:
    return ScoringPolicy(
        fit_gate_constraints=FitGateConstraints(
            locations=["上海"],
            min_years=5,
            degree_requirement="硕士及以上",
            company_names=["TestCo"],
        ),
        must_have_capabilities_snapshot=["python", "retrieval"],
        preferred_capabilities_snapshot=["to-b delivery"],
        fusion_weights=FusionWeights(rerank=0.55, must_have=0.25, preferred=0.10, risk_penalty=0.10),
        penalty_weights=PenaltyWeights(job_hop=1.0, job_hop_confidence_floor=0.6),
        top_n_for_explanation=2,
        rerank_instruction="Rank resumes for relevance.",
        rerank_query_text="Senior Python retrieval engineer in Shanghai.",
        reranker_calibration_snapshot=RerankerCalibration(
            model_id="test-reranker",
            normalization="sigmoid",
            temperature=2.0,
            offset=0.0,
            clip_min=-12,
            clip_max=12,
            calibration_version="test-v1",
        ),
        ranking_audit_notes="audit",
    )


def _scoring_candidate(
    candidate_id: str,
    *,
    scoring_text: str,
    capability_signals: list[str],
    years_of_experience: int | None = 6,
    age: int | None = None,
    gender: str | None = None,
    location_signals: list[str] | None = None,
    work_experience_summaries: list[str] | None = None,
    education_summaries: list[str] | None = None,
    profile: CareerStabilityProfile | None = None,
) -> ScoringCandidate_t:
    return ScoringCandidate_t(
        candidate_id=candidate_id,
        scoring_text=scoring_text,
        capability_signals=capability_signals,
        years_of_experience=years_of_experience,
        age=age,
        gender=gender,
        location_signals=location_signals or ["上海"],
        work_experience_summaries=work_experience_summaries or ["TestCo | Senior Engineer | Built ranking"],
        education_summaries=education_summaries or ["复旦大学 计算机 硕士"],
        career_stability_profile=profile
        or CareerStabilityProfile(
            job_count_last_5y=2,
            short_tenure_count=0,
            median_tenure_months=24,
            current_tenure_months=12,
            parsed_experience_count=2,
            confidence_score=1.0,
        ),
    )


def _execution_result(candidates: list[ScoringCandidate_t]) -> SearchExecutionResult_t:
    return SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[],
        scoring_candidates=candidates,
        search_page_statistics=SearchPageStatistics(pages_fetched=1, duplicate_rate=0.0, latency_ms=1),
        search_observation=SearchObservation(unique_candidate_ids=[candidate.candidate_id for candidate in candidates], shortage_after_last_page=False),
    )


@dataclass
class FakeRerankRequest:
    response: RerankResponse
    seen_requests: list[object] = field(default_factory=list)

    async def __call__(self, request):
        self.seen_requests.append(request)
        return self.response


def test_score_search_results_uses_text_only_request_and_keeps_stable_ties() -> None:
    execution_result = _execution_result(
        [
            _scoring_candidate("c-1", scoring_text="python retrieval", capability_signals=["python", "retrieval"], location_signals=["上海"]),
            _scoring_candidate("c-2", scoring_text="python retrieval", capability_signals=["python", "retrieval"], location_signals=["上海"]),
            _scoring_candidate(
                "c-3",
                scoring_text="python retrieval",
                capability_signals=["python", "retrieval"],
                location_signals=["上海"],
                education_summaries=["普通学校 本科"],
            ),
        ]
    )
    rerank = FakeRerankRequest(
        response=RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(id="c-1", index=0, score=4.0, rank=1),
                RerankResult(id="c-2", index=1, score=4.0, rank=2),
                RerankResult(id="c-3", index=2, score=4.0, rank=3),
            ],
        )
    )

    result = asyncio.run(score_search_results(execution_result, _scoring_policy(), rerank))

    assert rerank.seen_requests[0].instruction == "Rank resumes for relevance."
    assert rerank.seen_requests[0].query == "Senior Python retrieval engineer in Shanghai."
    assert [(document.id, document.text) for document in rerank.seen_requests[0].documents] == [
        ("c-1", "python retrieval"),
        ("c-2", "python retrieval"),
        ("c-3", "python retrieval"),
    ]
    assert [candidate.candidate_id for candidate in result.scored_candidates] == ["c-1", "c-2", "c-3"]
    assert result.node_shortlist_candidate_ids == ["c-1", "c-2"]
    assert result.explanation_candidate_ids == ["c-1", "c-2"]


def test_score_search_results_skips_risk_penalty_below_confidence_floor() -> None:
    execution_result = _execution_result(
        [
            _scoring_candidate(
                "low-confidence",
                scoring_text="python retrieval",
                capability_signals=["python", "retrieval"],
                profile=CareerStabilityProfile(
                    job_count_last_5y=4,
                    short_tenure_count=3,
                    median_tenure_months=3,
                    current_tenure_months=2,
                    parsed_experience_count=1,
                    confidence_score=0.2,
                ),
            ),
            _scoring_candidate(
                "high-confidence",
                scoring_text="python retrieval",
                capability_signals=["python", "retrieval"],
                profile=CareerStabilityProfile(
                    job_count_last_5y=4,
                    short_tenure_count=2,
                    median_tenure_months=6,
                    current_tenure_months=2,
                    parsed_experience_count=4,
                    confidence_score=1.0,
                ),
            ),
        ]
    )
    rerank = FakeRerankRequest(
        response=RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(id="low-confidence", index=0, score=3.0, rank=1),
                RerankResult(id="high-confidence", index=1, score=3.0, rank=2),
            ],
        )
    )

    result = asyncio.run(score_search_results(execution_result, _scoring_policy(), rerank))

    assert result.scored_candidates[0].candidate_id == "low-confidence"
    assert result.scored_candidates[0].risk_score_raw == 0
    assert result.scored_candidates[1].risk_score_raw == 100


def test_score_search_results_missing_candidate_signals_do_not_fail_fit_gate() -> None:
    execution_result = _execution_result(
        [
            _scoring_candidate(
                "missing-signals",
                scoring_text="python retrieval",
                capability_signals=["python", "retrieval"],
                years_of_experience=None,
                location_signals=["上海"],
                work_experience_summaries=[],
                education_summaries=[],
            ),
        ]
    )
    rerank = FakeRerankRequest(
        response=RerankResponse(
            model="test-reranker",
            results=[RerankResult(id="missing-signals", index=0, score=3.0, rank=1)],
        )
    )

    result = asyncio.run(score_search_results(execution_result, _scoring_policy(), rerank))

    assert result.node_shortlist_candidate_ids == ["missing-signals"]
    assert result.scored_candidates[0].fit == 1


def test_score_search_results_fails_when_rerank_results_do_not_cover_candidates() -> None:
    execution_result = _execution_result(
        [
            _scoring_candidate("c-1", scoring_text="python retrieval", capability_signals=["python", "retrieval"]),
            _scoring_candidate("c-2", scoring_text="python retrieval", capability_signals=["python", "retrieval"]),
        ]
    )
    rerank = FakeRerankRequest(
        response=RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(id="c-1", index=0, score=3.0, rank=1),
                RerankResult(id="c-1", index=1, score=2.0, rank=2),
            ],
        )
    )

    with pytest.raises(ValueError, match="duplicate_rerank_result_id"):
        asyncio.run(score_search_results(execution_result, _scoring_policy(), rerank))
