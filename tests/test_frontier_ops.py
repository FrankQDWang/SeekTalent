from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

import seektalent.frontier_ops as frontier_ops_module
from seektalent.clients.cts_client import CTSFetchResult
from seektalent.frontier_ops import (
    carry_forward_frontier_state,
    generate_search_controller_decision,
    select_active_frontier_node,
)
from seektalent.models import (
    BranchEvaluation_t,
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
    RewriteTermCandidate,
)
from seektalent.search_ops import (
    execute_search_plan,
    materialize_search_execution_plan,
    score_search_results,
)
from seektalent.runtime_budget import build_runtime_budget_state
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


def _branch_evaluation(*, exhausted: bool = False) -> BranchEvaluation_t:
    return BranchEvaluation_t(
        novelty_score=0.5,
        usefulness_score=0.5,
        branch_exhausted=exhausted,
        repair_operator_hint=None,
        evaluation_notes="",
    )


def _frontier_state(
    nodes: list[FrontierNode_t],
    *,
    run_shortlist_candidate_ids: list[str] | None = None,
    operator_statistics: dict[str, OperatorStatistics] | None = None,
    remaining_budget: int = 4,
) -> FrontierState_t:
    return FrontierState_t(
        frontier_nodes={node.frontier_node_id: node for node in nodes},
        open_frontier_node_ids=[node.frontier_node_id for node in nodes],
        closed_frontier_node_ids=[],
        run_term_catalog=[],
        run_shortlist_candidate_ids=run_shortlist_candidate_ids or [],
        semantic_hashes_seen=[],
        operator_statistics=operator_statistics or {},
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


def _runtime_budget_state(
    *,
    remaining_budget: int,
    initial_round_budget: int = 5,
    runtime_round_index: int = 0,
):
    return build_runtime_budget_state(
        initial_round_budget=initial_round_budget,
        runtime_round_index=runtime_round_index,
        remaining_budget=remaining_budget,
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


def test_select_active_frontier_node_prefers_partial_coverage_over_zero_and_full_hit() -> None:
    saturated = FrontierNode_t(
        frontier_node_id="seed_saturated",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        node_shortlist_candidate_ids=["c1"],
        status="open",
    )
    fresh = FrontierNode_t(
        frontier_node_id="seed_fresh",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
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
        _runtime_budget_state(remaining_budget=4),
    )

    assert context.active_frontier_node_summary.frontier_node_id == "seed_fresh"
    assert context.frontier_head_summary.highest_selection_score == pytest.approx(
        context.active_selection_breakdown.final_selection_score
    )
    assert context.active_selection_breakdown.coverage_opportunity_score == pytest.approx(0.5)
    assert context.selection_ranking[1].breakdown.coverage_opportunity_score == 0.0


def test_select_active_frontier_node_keeps_short_skill_must_haves_unmet_without_substring_false_positive() -> None:
    mongo = FrontierNode_t(
        frontier_node_id="seed_mongo",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["MongoDB"],
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([mongo]),
        _requirement_sheet().model_copy(update={"must_have_capabilities": ["Go"]}),
        _scoring_policy().model_copy(update={"must_have_capabilities_snapshot": ["Go"]}),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=1, runtime_round_index=4),
    )

    assert context.operator_surface_unmet_must_haves == ["Go"]
    assert [item.capability for item in context.unmet_requirement_weights] == ["Go"]


def test_select_active_frontier_node_keeps_root_seed_selection_stable_with_zero_operator_pulls() -> None:
    first = FrontierNode_t(
        frontier_node_id="seed_first",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        status="open",
    )
    second = FrontierNode_t(
        frontier_node_id="seed_second",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([first, second]),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=5, initial_round_budget=12, runtime_round_index=0),
    )

    assert context.active_frontier_node_summary.frontier_node_id == "seed_first"
    assert [item.frontier_node_id for item in context.selection_ranking] == [
        "seed_first",
        "seed_second",
    ]


def test_select_active_frontier_node_uses_operator_ucb_in_explore_phase() -> None:
    tried = FrontierNode_t(
        frontier_node_id="seed_tried",
        selected_operator_name="core_precision",
        node_query_term_pool=["python"],
        status="open",
    )
    untried = FrontierNode_t(
        frontier_node_id="seed_untried",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state(
            [tried, untried],
            operator_statistics={
                "core_precision": OperatorStatistics(average_reward=3.0, times_selected=8),
                "must_have_alias": OperatorStatistics(average_reward=0.0, times_selected=0),
            },
            remaining_budget=10,
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=10, initial_round_budget=12, runtime_round_index=0),
    )

    assert context.runtime_budget_state.search_phase == "explore"
    assert context.active_frontier_node_summary.frontier_node_id == "seed_untried"
    assert (
        context.selection_ranking[0].breakdown.operator_exploration_bonus
        > context.selection_ranking[1].breakdown.operator_exploration_bonus
    )


def test_select_active_frontier_node_prefers_exploitation_and_incremental_value_in_harvest() -> None:
    exploratory = FrontierNode_t(
        frontier_node_id="seed_exploratory",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        status="open",
    )
    productive = FrontierNode_t(
        frontier_node_id="child_productive",
        parent_frontier_node_id="seed_exploratory",
        selected_operator_name="core_precision",
        node_query_term_pool=["python"],
        reward_breakdown=NodeRewardBreakdown_t(
            delta_top_three=0.0,
            must_have_gain=0.0,
            new_fit_yield=4.0,
            novelty=0.0,
            usefulness=0.0,
            diversity=0.8,
            stability_risk_penalty=0.0,
            hard_constraint_violation=0.0,
            duplicate_penalty=0.0,
            cost_penalty=0.0,
            reward_score=3.5,
        ),
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state(
            [exploratory, productive],
            operator_statistics={
                "core_precision": OperatorStatistics(average_reward=3.5, times_selected=5),
                "must_have_alias": OperatorStatistics(average_reward=0.0, times_selected=0),
            },
            remaining_budget=2,
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=2, initial_round_budget=5, runtime_round_index=4),
    )

    assert context.runtime_budget_state.search_phase == "harvest"
    assert context.active_frontier_node_summary.frontier_node_id == "child_productive"
    assert (
        context.active_selection_breakdown.incremental_value_score
        > context.selection_ranking[1].breakdown.incremental_value_score
    )


def test_select_active_frontier_node_keeps_high_yield_branch_despite_overlap() -> None:
    overlap_heavy = FrontierNode_t(
        frontier_node_id="child_overlap_heavy",
        parent_frontier_node_id="seed",
        selected_operator_name="core_precision",
        node_query_term_pool=["python"],
        node_shortlist_candidate_ids=["c1", "c2"],
        reward_breakdown=NodeRewardBreakdown_t(
            delta_top_three=0.0,
            must_have_gain=0.0,
            new_fit_yield=10.0,
            novelty=0.0,
            usefulness=0.0,
            diversity=0.5,
            stability_risk_penalty=0.0,
            hard_constraint_violation=0.0,
            duplicate_penalty=0.0,
            cost_penalty=0.0,
            reward_score=3.0,
        ),
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )
    clean_but_empty = FrontierNode_t(
        frontier_node_id="child_clean_but_empty",
        parent_frontier_node_id="seed",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state(
            [overlap_heavy, clean_but_empty],
            run_shortlist_candidate_ids=["c1"],
            operator_statistics={
                "core_precision": OperatorStatistics(average_reward=3.0, times_selected=3),
                "must_have_alias": OperatorStatistics(average_reward=0.0, times_selected=0),
            },
            remaining_budget=2,
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=2, initial_round_budget=5, runtime_round_index=4),
    )

    assert context.active_frontier_node_summary.frontier_node_id == "child_overlap_heavy"
    assert context.active_selection_breakdown.redundancy_penalty == pytest.approx(0.5)
    assert context.active_selection_breakdown.incremental_value_score > 0.0


def test_select_active_frontier_node_rejects_open_exhausted_nodes() -> None:
    with pytest.raises(ValueError, match="open_frontier_node_marked_exhausted"):
        select_active_frontier_node(
            _frontier_state(
                [
                    FrontierNode_t(
                        frontier_node_id="child_exhausted",
                        parent_frontier_node_id="seed",
                        selected_operator_name="core_precision",
                        node_query_term_pool=["python"],
                        previous_branch_evaluation=_branch_evaluation(exhausted=True),
                        status="open",
                    )
                ]
            ),
            _requirement_sheet(),
            _scoring_policy(),
            CrossoverGuardThresholds(),
            RuntimeTermBudgetPolicy(),
            _runtime_budget_state(remaining_budget=3),
        )


def test_select_active_frontier_node_uses_explore_surface_for_generic_provenance() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_generic",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_ids=[],
        status="open",
    )
    legal_donor = FrontierNode_t(
        frontier_node_id="child_legal",
        parent_frontier_node_id="root",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        reward_breakdown=_reward(2.5),
        status="open",
    )
    missing_reward = FrontierNode_t(
        frontier_node_id="child_missing_reward",
        parent_frontier_node_id="root",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        status="open",
    )
    no_anchor = FrontierNode_t(
        frontier_node_id="child_no_anchor",
        parent_frontier_node_id="root",
        selected_operator_name="core_precision",
        node_query_term_pool=["ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        reward_breakdown=_reward(2.5),
        status="open",
    )
    no_increment = FrontierNode_t(
        frontier_node_id="child_no_increment",
        parent_frontier_node_id="root",
        selected_operator_name="core_precision",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        reward_breakdown=_reward(2.5),
        status="open",
    )
    closed = FrontierNode_t(
        frontier_node_id="child_closed",
        parent_frontier_node_id="root",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        reward_breakdown=_reward(2.5),
        status="closed",
    )

    context = select_active_frontier_node(
        _frontier_state([active, legal_donor, missing_reward, no_anchor, no_increment, closed]),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=4),
    )

    assert context.active_frontier_node_summary.frontier_node_id == "seed_generic"
    assert [donor.frontier_node_id for donor in context.donor_candidate_node_summaries] == ["child_legal"]
    assert context.allowed_operator_names == [
        "must_have_alias",
        "generic_expansion",
        "core_precision",
        "relaxed_floor",
    ]
    assert context.operator_surface_override_reason == "none"
    assert context.operator_surface_unmet_must_haves == ["ranking"]
    assert [item.capability for item in context.unmet_requirement_weights] == ["python", "ranking"]
    assert [item.weight for item in context.unmet_requirement_weights] == [0.3, 1.0]


def test_select_active_frontier_node_uses_explore_surface_for_pack_provenance() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_pack",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([active]),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=10, initial_round_budget=12, runtime_round_index=0),
    )

    assert context.runtime_budget_state.search_phase == "explore"
    assert context.allowed_operator_names == [
        "must_have_alias",
        "generic_expansion",
        "core_precision",
        "relaxed_floor",
        "pack_expansion",
        "cross_pack_bridge",
    ]


def test_select_active_frontier_node_appends_crossover_only_with_legal_donor_in_balance() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_balance",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        status="open",
    )
    donor = FrontierNode_t(
        frontier_node_id="child_balance_donor",
        parent_frontier_node_id="seed_balance",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        reward_breakdown=_reward(2.5),
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )

    context_with_donor = select_active_frontier_node(
        _frontier_state([active, donor], remaining_budget=6),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=6, initial_round_budget=12, runtime_round_index=5),
    )
    context_without_donor = select_active_frontier_node(
        _frontier_state([active], remaining_budget=6),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=6, initial_round_budget=12, runtime_round_index=5),
    )

    assert context_with_donor.runtime_budget_state.search_phase == "balance"
    assert context_with_donor.allowed_operator_names == [
        "core_precision",
        "must_have_alias",
        "relaxed_floor",
        "generic_expansion",
        "pack_expansion",
        "cross_pack_bridge",
        "crossover_compose",
    ]
    assert context_without_donor.allowed_operator_names == [
        "core_precision",
        "must_have_alias",
        "relaxed_floor",
        "generic_expansion",
        "pack_expansion",
        "cross_pack_bridge",
    ]


def test_select_active_frontier_node_harvest_surface_stays_convergent_without_repair() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_harvest_full_hit",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([active], remaining_budget=1),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=1, initial_round_budget=5, runtime_round_index=4),
    )

    assert context.runtime_budget_state.search_phase == "harvest"
    assert context.allowed_operator_names == ["core_precision"]
    assert context.operator_surface_override_reason == "none"
    assert context.operator_surface_unmet_must_haves == []


def test_select_active_frontier_node_harvest_surface_allows_repair_and_crossover_when_needed() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_harvest_partial_hit",
        selected_operator_name="core_precision",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )
    donor = FrontierNode_t(
        frontier_node_id="child_harvest_donor",
        parent_frontier_node_id="seed_harvest_partial_hit",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        reward_breakdown=_reward(2.5),
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([active, donor], remaining_budget=1),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=1, initial_round_budget=5, runtime_round_index=4),
    )

    assert context.allowed_operator_names == [
        "core_precision",
        "crossover_compose",
        "must_have_alias",
        "generic_expansion",
    ]
    assert context.operator_surface_override_reason == "harvest_unmet_must_have_repair"
    assert context.operator_surface_unmet_must_haves == ["ranking"]


def test_select_active_frontier_node_harvest_repair_never_reopens_pack_expansion() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_harvest_pack",
        selected_operator_name="core_precision",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([active], remaining_budget=1),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=1, initial_round_budget=5, runtime_round_index=4),
    )

    assert context.allowed_operator_names == [
        "core_precision",
        "must_have_alias",
        "generic_expansion",
    ]
    assert "pack_expansion" not in context.allowed_operator_names
    assert "cross_pack_bridge" not in context.allowed_operator_names


def test_select_active_frontier_node_keeps_coverage_and_repair_semantics_same_source() -> None:
    partial_hit = FrontierNode_t(
        frontier_node_id="seed_partial_hit",
        selected_operator_name="core_precision",
        node_query_term_pool=["python"],
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )
    full_hit = FrontierNode_t(
        frontier_node_id="seed_full_hit",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        previous_branch_evaluation=_branch_evaluation(),
        status="open",
    )

    partial_context = select_active_frontier_node(
        _frontier_state([partial_hit], remaining_budget=1),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=1, initial_round_budget=5, runtime_round_index=4),
    )
    full_context = select_active_frontier_node(
        _frontier_state([full_hit], remaining_budget=1),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=1, initial_round_budget=5, runtime_round_index=4),
    )

    assert frontier_ops_module._coverage_opportunity_score(  # noqa: SLF001
        partial_hit,
        _requirement_sheet(),
    ) > 0.0
    assert partial_context.operator_surface_override_reason == "harvest_unmet_must_have_repair"
    assert frontier_ops_module._coverage_opportunity_score(full_hit, _requirement_sheet()) == 0.0  # noqa: SLF001
    assert full_context.operator_surface_override_reason == "none"


@pytest.mark.parametrize(
    ("runtime_budget_state", "remaining_budget", "expected_max_query_terms"),
    [
        (_runtime_budget_state(remaining_budget=10, initial_round_budget=12, runtime_round_index=0), 10, 3),
        (_runtime_budget_state(remaining_budget=6, initial_round_budget=12, runtime_round_index=5), 6, 4),
        (_runtime_budget_state(remaining_budget=4, initial_round_budget=5, runtime_round_index=4), 4, 6),
    ],
)
def test_select_active_frontier_node_freezes_max_query_terms_by_phase(
    runtime_budget_state,
    remaining_budget: int,
    expected_max_query_terms: int,
) -> None:
    node = FrontierNode_t(
        frontier_node_id="seed",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([node], remaining_budget=remaining_budget),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        runtime_budget_state,
    )

    assert context.max_query_terms == expected_max_query_terms


def test_select_active_frontier_node_max_query_terms_ignores_legacy_remaining_budget_thresholds() -> None:
    node = FrontierNode_t(
        frontier_node_id="seed",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        status="open",
    )

    context = select_active_frontier_node(
        _frontier_state([node], remaining_budget=4),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=4, initial_round_budget=5, runtime_round_index=4),
    )

    assert context.runtime_budget_state.search_phase == "harvest"
    assert context.max_query_terms == 6


def test_generate_search_controller_decision_normalizes_stop_and_falls_back_to_active_operator() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python"],
                    knowledge_pack_ids=["llm_agent_rag_engineering"],
                    status="open",
                )
            ]
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=4),
    )

    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="stop",
            selected_operator_name="unknown_operator",
            operator_args={"query_terms": ["ranking"]},
            expected_gain_hypothesis="Enough coverage.",
        ),
    )

    assert decision.action == "stop"
    assert decision.selected_operator_name == "must_have_alias"
    assert decision.operator_args == {}


def test_generate_search_controller_decision_clamps_non_crossover_query_terms() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python", "agent", "workflow", "backend"],
                    knowledge_pack_ids=["llm_agent_rag_engineering"],
                    status="open",
                )
            ],
            remaining_budget=6,
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=10, initial_round_budget=12, runtime_round_index=0),
    )

    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="core_precision",
            operator_args={"query_terms": [" backend ", "", "backend", "python", "workflow"]},
            expected_gain_hypothesis="Tighten ranking coverage.",
        ),
    )

    assert decision.action == "search_cts"
    assert decision.selected_operator_name == "core_precision"
    assert decision.operator_args == {"query_terms": ["backend", "python", "workflow"]}


def test_generate_search_controller_decision_rejects_core_precision_new_terms() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python", "agent", "workflow", "backend"],
                    knowledge_pack_ids=["llm_agent_rag_engineering"],
                    status="open",
                )
            ]
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=10, initial_round_budget=12, runtime_round_index=0),
    )

    with pytest.raises(ValueError, match="core_precision_requires_non_empty_active_query_subset"):
        generate_search_controller_decision(
            context,
            SearchControllerDecisionDraft_t(
                action="search_cts",
                selected_operator_name="core_precision",
                operator_args={"query_terms": ["python", "ranking"]},
                expected_gain_hypothesis="Add ranking.",
            ),
        )


def test_generate_search_controller_decision_requires_strict_relaxed_floor_subset() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python", "agent", "workflow"],
                    knowledge_pack_ids=["llm_agent_rag_engineering"],
                    status="open",
                )
            ]
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=10, initial_round_budget=12, runtime_round_index=0),
    )

    with pytest.raises(ValueError, match="relaxed_floor_requires_non_empty_strict_active_query_subset"):
        generate_search_controller_decision(
            context,
            SearchControllerDecisionDraft_t(
                action="search_cts",
                selected_operator_name="relaxed_floor",
                operator_args={"query_terms": ["python", "agent", "workflow"]},
                expected_gain_hypothesis="Keep everything.",
            ),
        )


def test_generate_search_controller_decision_requires_rewrite_for_must_have_alias() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python", "agent", "workflow", "backend"],
                    knowledge_pack_ids=["llm_agent_rag_engineering"],
                    status="open",
                )
            ]
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=10, initial_round_budget=12, runtime_round_index=0),
    )

    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="must_have_alias",
            operator_args={"query_terms": ["python", "backend", "ranking"]},
            expected_gain_hypothesis="Swap in ranking.",
        ),
    )

    assert decision.operator_args == {"query_terms": ["python", "backend", "ranking"]}

    with pytest.raises(
        ValueError,
        match="must_have_alias_requires_query_rewrite_with_shared_new_and_dropped_terms",
    ):
        generate_search_controller_decision(
            context,
            SearchControllerDecisionDraft_t(
                action="search_cts",
                selected_operator_name="must_have_alias",
                operator_args={"query_terms": ["python", "agent", "workflow", "backend", "ranking"]},
                expected_gain_hypothesis="Append ranking.",
            ),
        )


def test_generate_search_controller_decision_uses_ga_lite_rewrite_terms() -> None:
    context = select_active_frontier_node(
        _frontier_state(
            [
                FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="must_have_alias",
                    node_query_term_pool=["python backend", "workflow", "agent"],
                    knowledge_pack_ids=["llm_agent_rag_engineering"],
                    rewrite_term_candidates=[
                        RewriteTermCandidate(
                            term="ranking",
                            source_candidate_ids=["c1", "c2"],
                            source_fields=["work_summaries"],
                        ),
                        RewriteTermCandidate(
                            term="rag",
                            source_candidate_ids=["c1"],
                            source_fields=["project_names"],
                        ),
                    ],
                    status="open",
                )
            ]
        ),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=5, runtime_round_index=4),
    )

    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="generic_expansion",
            operator_args={"query_terms": ["python backend", "workflow", "rag"]},
            expected_gain_hypothesis="Use the strongest supported rewrite.",
        ),
    )

    assert decision.operator_args == {
        "query_terms": ["python backend", "workflow", "ranking"]
    }


def test_generate_search_controller_decision_normalizes_crossover_fields() -> None:
    active = FrontierNode_t(
        frontier_node_id="seed_generic",
        selected_operator_name="must_have_alias",
        node_query_term_pool=["python"],
        knowledge_pack_ids=[],
        status="open",
    )
    donor = FrontierNode_t(
        frontier_node_id="child_legal",
        parent_frontier_node_id="root",
        selected_operator_name="core_precision",
        node_query_term_pool=["python", "ranking"],
        knowledge_pack_ids=["llm_agent_rag_engineering"],
        reward_breakdown=_reward(2.5),
        status="open",
    )
    context = select_active_frontier_node(
        _frontier_state([active, donor]),
        _requirement_sheet(),
        _scoring_policy(),
        CrossoverGuardThresholds(),
        RuntimeTermBudgetPolicy(),
        _runtime_budget_state(remaining_budget=6, initial_round_budget=12, runtime_round_index=5),
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
                knowledge_pack_ids=["llm_agent_rag_engineering"],
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
                knowledge_pack_ids=["llm_agent_rag_engineering"],
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
        _runtime_budget_state(remaining_budget=4),
    )
    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="search_cts",
            selected_operator_name="core_precision",
            operator_args={"query_terms": ["python"]},
            expected_gain_hypothesis="Tighten to the strongest anchor.",
        ),
    )
    plan = materialize_search_execution_plan(
        state,
        _requirement_sheet(),
        decision,
        context.max_query_terms,
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
    assert plan.query_terms == ["python"]
    assert scoring_result.node_shortlist_candidate_ids == ["keep"]


def test_frontier_stop_path_keeps_the_same_frontier_state() -> None:
    state = _frontier_state(
        [
            FrontierNode_t(
                frontier_node_id="seed",
                selected_operator_name="must_have_alias",
                node_query_term_pool=["python"],
                knowledge_pack_ids=["llm_agent_rag_engineering"],
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
        _runtime_budget_state(remaining_budget=4),
    )
    decision = generate_search_controller_decision(
        context,
        SearchControllerDecisionDraft_t(
            action="stop",
            selected_operator_name="core_precision",
            operator_args={},
            expected_gain_hypothesis="Shortlist is good enough.",
        ),
    )
    carried = carry_forward_frontier_state(state)

    assert decision.action == "stop"
    assert carried.model_dump(mode="python") == state.model_dump(mode="python")
