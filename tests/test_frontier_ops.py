from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from seektalent.clients.cts_client import CTSFetchResult
from seektalent.frontier_ops import (
    carry_forward_frontier_state,
    generate_search_controller_decision,
    select_active_frontier_node,
)
from seektalent.models import (
    CareerStabilityProfile,
    CrossoverGuardThresholds,
    FitGateConstraints,
    FrontierNode_t,
    FrontierState_t,
    FusionWeights,
    HardConstraints,
    NodeRewardBreakdown_t,
    OperatorStatistics,
    PenaltyWeights,
    RequirementPreferences,
    RequirementSheet,
    RetrievedCandidate_t,
    RerankerCalibration,
    RuntimeSearchBudget,
    RuntimeTermBudgetPolicy,
    ScoringPolicy,
    SearchControllerDecisionDraft_t,
)
from seektalent.search_ops import (
    execute_search_plan,
    materialize_search_execution_plan,
    score_search_results,
)
from seektalent_rerank.models import RerankResponse, RerankResult


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Agent Engineer",
        role_summary="Build ranking systems.",
        must_have_capabilities=["python", "ranking"],
        preferred_capabilities=["workflow"],
        exclusion_signals=[],
        hard_constraints=HardConstraints(),
        preferences=RequirementPreferences(),
        scoring_rationale="must-have first",
    )


def _scoring_policy() -> ScoringPolicy:
    return ScoringPolicy(
        fit_gate_constraints=FitGateConstraints(locations=["上海"], min_years=5),
        must_have_capabilities_snapshot=["python", "ranking"],
        preferred_capabilities_snapshot=["workflow"],
        fusion_weights=FusionWeights(rerank=0.55, must_have=0.25, preferred=0.10, risk_penalty=0.10),
        penalty_weights=PenaltyWeights(job_hop=1.0, job_hop_confidence_floor=0.6),
        top_n_for_explanation=2,
        rerank_instruction="Rank resumes for relevance.",
        rerank_query_text="Senior Python ranking engineer in Shanghai.",
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


def _reward(score: float) -> NodeRewardBreakdown_t:
    return NodeRewardBreakdown_t(
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
        reward_score=score,
    )


def _frontier_state(
    nodes: list[FrontierNode_t],
    *,
    run_shortlist_candidate_ids: list[str] | None = None,
    remaining_budget: int = 4,
) -> FrontierState_t:
    return FrontierState_t(
        frontier_nodes={node.frontier_node_id: node for node in nodes},
        open_frontier_node_ids=[node.frontier_node_id for node in nodes],
        closed_frontier_node_ids=[],
        run_term_catalog=[],
        run_shortlist_candidate_ids=run_shortlist_candidate_ids or [],
        semantic_hashes_seen=[],
        operator_statistics={},
        remaining_budget=remaining_budget,
    )


def _candidate(candidate_id: str, *, search_text: str) -> RetrievedCandidate_t:
    return RetrievedCandidate_t(
        candidate_id=candidate_id,
        now_location="上海",
        expected_location="上海",
        years_of_experience_raw=6,
        education_summaries=["复旦大学 计算机 硕士"],
        work_experience_summaries=["TestCo | Python Engineer | Built ranking systems."],
        project_names=["ranking platform"],
        work_summaries=["python", "ranking"],
        search_text=search_text,
        raw_payload={"title": "Python Engineer", "workExperienceList": []},
    )


@dataclass
class FakeCTSClient:
    result: CTSFetchResult
    seen_plans: list[object] = field(default_factory=list)

    async def search(self, plan, *, trace_id: str = "") -> CTSFetchResult:
        del trace_id
        self.seen_plans.append(plan)
        return self.result


@dataclass
class FakeRerankRequest:
    response: RerankResponse
    seen_requests: list[object] = field(default_factory=list)

    async def __call__(self, request):
        self.seen_requests.append(request)
        return self.response


def test_select_active_frontier_node_applies_saturation_penalty() -> None:
    saturated = FrontierNode_t(
        frontier_node_id="seed_saturated",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_id="llm_agent_rag_engineering",
        node_shortlist_candidate_ids=["c1"],
        status="open",
    )
    fresh = FrontierNode_t(
        frontier_node_id="seed_fresh",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_id="llm_agent_rag_engineering",
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state(
            [saturated, fresh],
            run_shortlist_candidate_ids=["c1"],
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )

    assert context.active_frontier_node_summary.frontier_node_id == "seed_fresh"
    assert context.frontier_head_summary.highest_priority_score == pytest.approx(2.5)


def test_select_active_frontier_node_filters_donors_and_disables_domain_company_for_generic_provenance() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_generic",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_id=None,
        status="open",
    )
    legal_donor = FrontierNode_t(
        frontier_node_id="child_legal",
        parent_frontier_node_id="root",
        selected_operator_name="strict_core",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_id="llm_agent_rag_engineering",
        reward_breakdown=_reward(2.5),
        status="open",
    )
    missing_reward = FrontierNode_t(
        frontier_node_id="child_missing_reward",
        parent_frontier_node_id="root",
        selected_operator_name="strict_core",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_id="llm_agent_rag_engineering",
        status="open",
    )
    no_anchor = FrontierNode_t(
        frontier_node_id="child_no_anchor",
        parent_frontier_node_id="root",
        selected_operator_name="strict_core",
        node_query_term_pool=["ranking"],
        knowledge_pack_id="llm_agent_rag_engineering",
        reward_breakdown=_reward(2.5),
        status="open",
    )
    no_increment = FrontierNode_t(
        frontier_node_id="child_no_increment",
        parent_frontier_node_id="root",
        selected_operator_name="strict_core",
        node_query_term_pool=["python"],
        knowledge_pack_id="llm_agent_rag_engineering",
        reward_breakdown=_reward(2.5),
        status="open",
    )
    closed = FrontierNode_t(
        frontier_node_id="child_closed",
        parent_frontier_node_id="root",
        selected_operator_name="strict_core",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_id="llm_agent_rag_engineering",
        reward_breakdown=_reward(2.5),
        status="closed",
    )

    context = select_active_frontier_node(
        _frontier_state([active, legal_donor, missing_reward, no_anchor, no_increment, closed]),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )

    assert context.active_frontier_node_summary.frontier_node_id == "seed_generic"
    assert [donor.frontier_node_id for donor in context.donor_candidate_node_summaries] == ["child_legal"]
    assert context.allowed_operator_names == [
        "must_have_alias",
        "strict_core",
        "crossover_compose",
    ]
    assert [item.capability for item in context.unmet_requirement_weights] == ["python", "ranking"]
    assert [item.weight for item in context.unmet_requirement_weights] == [0.3, 1.0]


@pytest.mark.parametrize(
    ("remaining_budget", "expected_range"),
    [(4, (2, 6)), (2, (2, 5)), (1, (2, 4))],
)
def test_select_active_frontier_node_freezes_term_budget_ranges(
    remaining_budget: int,
    expected_range: tuple[int, int],
) -> None:
    node = FrontierNode_t(
        frontier_node_id="seed",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_id="llm_agent_rag_engineering",
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([node], remaining_budget=remaining_budget),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )

    assert context.term_budget_range == expected_range


def test_generate_search_controller_decision_normalizes_stop_and_falls_back_to_active_operator() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python"],
                    knowledge_pack_id="llm_agent_rag_engineering",
                    status="open",
                )
            ]
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )

    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="stop",
            selected_operator_name="unknown_operator",
            operator_args={"additional_terms": ["ranking"]},
            expected_gain_hypothesis="Enough coverage.",
        ),
    )

    assert decision.action == "stop"
    assert decision.selected_operator_name == "must_have_alias"
    assert decision.operator_args == {}


def test_generate_search_controller_decision_clamps_non_crossover_terms() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python", "agent", "workflow", "backend"],
                    knowledge_pack_id="llm_agent_rag_engineering",
                    status="open",
                )
            ],
            remaining_budget=2,
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )

    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="strict_core",
            operator_args={"additional_terms": [" ranking ", "", "ranking", "python"]},
            expected_gain_hypothesis="Tighten ranking coverage.",
        ),
    )

    assert decision.action == "search_cts"
    assert decision.selected_operator_name == "strict_core"
    assert decision.operator_args == {"additional_terms": ["ranking"]}


def test_generate_search_controller_decision_normalizes_crossover_fields() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_generic",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_id=None,
        status="open",
    )
    donor = FrontierNode_t(
        frontier_node_id="child_legal",
        parent_frontier_node_id="root",
        selected_operator_name="strict_core",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_id="llm_agent_rag_engineering",
        reward_breakdown=_reward(2.5),
        status="open",
    )
    context = select_active_frontier_node(
        _frontier_state([active, donor]),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )

    invalid = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="crossover_compose",
            operator_args={
                "donor_frontier_node_id": "unknown",
                "shared_anchor_terms": ["python", "", "python"],
                "donor_terms_used": ["ranking", "", "ranking"],
                "crossover_rationale": " expand coverage ",
            },
            expected_gain_hypothesis="Use donor coverage.",
        ),
    )
    valid = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="crossover_compose",
            operator_args={
                "donor_frontier_node_id": "child_legal",
                "shared_anchor_terms": ["python", "", "python"],
                "donor_terms_used": ["ranking", "", "ranking"],
                "crossover_rationale": " expand coverage ",
            },
            expected_gain_hypothesis="Use donor coverage.",
        ),
    )

    assert invalid.operator_args["donor_frontier_node_id"] is None
    assert invalid.operator_args["shared_anchor_terms"] == ["python"]
    assert invalid.operator_args["donor_terms_used"] == ["ranking"]
    assert valid.operator_args["donor_frontier_node_id"] == "child_legal"
    assert valid.operator_args["crossover_rationale"] == "expand coverage"


def test_carry_forward_frontier_state_is_identity_projection() -> None:
    state = _frontier_state(
        [
            FrontierNode_t(
                frontier_node_id="seed",
                selected_operator_name="must_have_alias",
                node_query_term_pool=["python"],
                knowledge_pack_id="llm_agent_rag_engineering",
                status="open",
            )
        ],
        run_shortlist_candidate_ids=["c1"],
    ).model_copy(
        update={
            "run_term_catalog": ["python"],
            "semantic_hashes_seen": ["hash-1"],
            "operator_statistics": {
                "must_have_alias": OperatorStatistics(average_reward=1.0, times_selected=1)
            },
        }
    )

    carried = carry_forward_frontier_state(state)

    assert carried.model_dump(mode="python") == state.model_dump(mode="python")


def test_frontier_search_path_connects_to_phase3_ops() -> None:
    state = _frontier_state(
        [
            FrontierNode_t(
                frontier_node_id="seed",
                selected_operator_name="must_have_alias",
                node_query_term_pool=["python", "agent"],
                knowledge_pack_id="llm_agent_rag_engineering",
                negative_terms=["frontend"],
                status="open",
            )
        ]
    )
    context = select_active_frontier_node(
        state,
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )
    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="strict_core",
            operator_args={"additional_terms": ["ranking"]},
            expected_gain_hypothesis="Expand ranking coverage.",
        ),
    )
    plan = materialize_search_execution_plan(
        state,
        _requirement_sheet(),
        decision,
        RuntimeTermBudgetPolicy(),
        RuntimeSearchBudget(),
        CrossoverGuardThresholds(),
    )
    execution_result = asyncio.run(
        execute_search_plan(
            plan,
            FakeCTSClient(
                result=CTSFetchResult(
                    request_payload={},
                    candidates=[_candidate("keep", search_text="python ranking"), _candidate("keep", search_text="python ranking duplicate")],
                    raw_candidate_count=2,
                    latency_ms=5,
                )
            ),
        )
    )
    scoring_result = asyncio.run(
        score_search_results(
            execution_result,
            _scoring_policy(),
            FakeRerankRequest(
                response=RerankResponse(
                    model="test-reranker",
                    results=[RerankResult(id="keep", index=0, score=3.0, rank=1)],
                )
            ),
        )
    )

    assert decision.action == "search_cts"
    assert plan.query_terms[-1] == "ranking"
    assert scoring_result.node_shortlist_candidate_ids == ["keep"]


def test_frontier_stop_path_keeps_the_same_frontier_state() -> None:
    state = _frontier_state(
        [
            FrontierNode_t(
                frontier_node_id="seed",
                selected_operator_name="must_have_alias",
                node_query_term_pool=["python"],
                knowledge_pack_id="llm_agent_rag_engineering",
                status="open",
            )
        ]
    )
    context = select_active_frontier_node(
        state,
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
    )
    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="stop",
            selected_operator_name="strict_core",
            operator_args={},
            expected_gain_hypothesis="Shortlist is good enough.",
        ),
    )
    carried = carry_forward_frontier_state(state)

    assert decision.action == "stop"
    assert carried.model_dump(mode="python") == state.model_dump(mode="python")
