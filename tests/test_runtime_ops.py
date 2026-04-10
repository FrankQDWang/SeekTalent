from __future__ import annotations

import pytest

from seektalent.models import (
    BranchEvaluationDraft_t,
    BranchEvaluation_t,
    FrontierNode_t,
    FrontierState_t,
    FrontierState_t1,
    HardConstraints,
    NodeRewardBreakdown_t,
    RequirementPreferences,
    RequirementSheet,
    RuntimeRoundState,
    ScoredCandidate_t,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchObservation,
    SearchPageStatistics,
    SearchRunSummaryDraft_t,
    SearchScoringResult_t,
    StopGuardThresholds,
    TopThreeStatistics,
)
from seektalent.runtime_ops import (
    compute_node_reward_breakdown,
    evaluate_branch_outcome,
    evaluate_stop_condition,
    finalize_search_run,
    update_frontier_state,
)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        role_summary="Build ranking systems.",
        must_have_capabilities=["python", "ranking"],
        preferred_capabilities=["workflow"],
        exclusion_signals=[],
        hard_constraints=HardConstraints(),
        preferences=RequirementPreferences(),
        scoring_rationale="must-have first",
    )


def _frontier_state() -> FrontierState_t:
    return FrontierState_t(
        frontier_nodes={
            "seed": FrontierNode_t(
                frontier_node_id="seed",
                selected_operator_name="must_have_alias",
                node_query_term_pool=["python"],
                knowledge_pack_ids=["llm_agent_rag_engineering"],
                node_shortlist_candidate_ids=["legacy-a", "legacy-b"],
                node_shortlist_score_snapshot={"legacy-a": 0.92, "legacy-b": 0.81},
                status="open",
            ),
            "sibling": FrontierNode_t(
                frontier_node_id="sibling",
                selected_operator_name="core_precision",
                node_query_term_pool=["ranking"],
                knowledge_pack_ids=["search_ranking_retrieval_engineering"],
                node_shortlist_candidate_ids=["legacy-c"],
                node_shortlist_score_snapshot={"legacy-c": 0.88},
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
                    reward_score=1.8,
                ),
                status="open",
            ),
        },
        open_frontier_node_ids=["seed", "sibling"],
        closed_frontier_node_ids=[],
        run_term_catalog=["python", "ranking"],
        run_shortlist_candidate_ids=["legacy-a", "legacy-c"],
        semantic_hashes_seen=["hash-seed"],
        operator_statistics={
            "must_have_alias": {"average_reward": 0.4, "times_selected": 1},
            "core_precision": {"average_reward": 0.6, "times_selected": 2},
            "pack_expansion": {"average_reward": 0.0, "times_selected": 0},
            "crossover_compose": {"average_reward": 0.0, "times_selected": 0},
        },
        remaining_budget=3,
    )


def _plan() -> SearchExecutionPlan_t:
    return SearchExecutionPlan_t.model_validate(
        {
            "query_terms": ["python", "ranking", "workflow"],
            "projected_filters": {},
            "runtime_only_constraints": {
                "must_have_keywords": ["python", "ranking", "workflow"],
                "negative_keywords": ["frontend"],
            },
            "target_new_candidate_count": 10,
            "semantic_hash": "hash-child",
            "knowledge_pack_ids": ["llm_agent_rag_engineering"],
            "child_frontier_node_stub": {
                "frontier_node_id": "child_seed_hash",
                "parent_frontier_node_id": "seed",
                "donor_frontier_node_id": None,
                "selected_operator_name": "core_precision",
            },
        }
    )


def _execution_result(*, pages_fetched: int = 2, duplicate_rate: float = 0.25) -> SearchExecutionResult_t:
    return SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[],
        scoring_candidates=[],
        search_page_statistics=SearchPageStatistics(
            pages_fetched=pages_fetched,
            duplicate_rate=duplicate_rate,
            latency_ms=7,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=["new-fit", "legacy-c"],
            shortage_after_last_page=True,
        ),
    )


def _scoring_result(*, shortlist_ids: list[str] | None = None, top_three_average: float = 0.5) -> SearchScoringResult_t:
    shortlist_ids = ["new-fit", "legacy-c"] if shortlist_ids is None else shortlist_ids
    return SearchScoringResult_t(
        scored_candidates=[
            ScoredCandidate_t(
                candidate_id="new-fit",
                fit=1,
                rerank_raw=1.0,
                rerank_normalized=0.7,
                must_have_match_score_raw=100,
                must_have_match_score=1.0,
                preferred_match_score_raw=0,
                preferred_match_score=0.0,
                risk_score_raw=10,
                risk_score=0.1,
                risk_flags=[],
                fusion_score=0.95,
            ),
            ScoredCandidate_t(
                candidate_id="legacy-c",
                fit=1,
                rerank_raw=0.8,
                rerank_normalized=0.6,
                must_have_match_score_raw=50,
                must_have_match_score=0.5,
                preferred_match_score_raw=0,
                preferred_match_score=0.0,
                risk_score_raw=20,
                risk_score=0.2,
                risk_flags=[],
                fusion_score=0.75,
            ),
            ScoredCandidate_t(
                candidate_id="not-fit",
                fit=0,
                rerank_raw=0.5,
                rerank_normalized=0.5,
                must_have_match_score_raw=0,
                must_have_match_score=0.0,
                preferred_match_score_raw=0,
                preferred_match_score=0.0,
                risk_score_raw=0,
                risk_score=0.0,
                risk_flags=[],
                fusion_score=0.25,
            ),
        ],
        node_shortlist_candidate_ids=shortlist_ids,
        explanation_candidate_ids=["new-fit"],
        top_three_statistics=TopThreeStatistics(
            average_fusion_score_top_three=top_three_average
        ),
    )


def test_evaluate_branch_outcome_clamps_whitelists_and_forces_exhaustion() -> None:
    result = evaluate_branch_outcome(
        _requirement_sheet(),
        _frontier_state(),
        _plan().model_copy(update={"knowledge_pack_ids": []}),
        _execution_result(),
        _scoring_result(shortlist_ids=[]),
        BranchEvaluationDraft_t(
            novelty_score=1.5,
            usefulness_score=-1.0,
            branch_exhausted=False,
            repair_operator_hint="pack_expansion",
            evaluation_notes="  too broad  ",
        ),
    )

    assert result == BranchEvaluation_t(
        novelty_score=1.0,
        usefulness_score=0.0,
        branch_exhausted=True,
        repair_operator_hint=None,
        evaluation_notes="too broad",
    )


def test_compute_node_reward_breakdown_allows_negative_delta_top_three() -> None:
    branch_evaluation = BranchEvaluation_t(
        novelty_score=0.1,
        usefulness_score=0.2,
        branch_exhausted=False,
        repair_operator_hint=None,
        evaluation_notes="low gain",
    )

    reward = compute_node_reward_breakdown(
        _frontier_state(),
        _plan(),
        _execution_result(),
        _scoring_result(top_three_average=0.3),
        branch_evaluation,
    )

    assert reward.delta_top_three == pytest.approx(-0.565)
    assert reward.must_have_gain == pytest.approx(0.75)
    assert reward.new_fit_yield == 1.0
    assert reward.reward_score == pytest.approx(0.2216666666666668, rel=1e-3)


def test_update_frontier_state_closes_parent_and_sorts_run_shortlist() -> None:
    branch_evaluation = BranchEvaluation_t(
        novelty_score=0.7,
        usefulness_score=0.6,
        branch_exhausted=False,
        repair_operator_hint="core_precision",
        evaluation_notes="useful",
    )
    reward = NodeRewardBreakdown_t(
        delta_top_three=0.2,
        must_have_gain=1.0,
        new_fit_yield=1.0,
        novelty=0.7,
        usefulness=0.6,
        diversity=0.5,
        stability_risk_penalty=0.15,
        hard_constraint_violation=0.0,
        duplicate_penalty=0.25,
        cost_penalty=0.3,
        reward_score=2.4,
    )

    updated = update_frontier_state(
        _frontier_state(),
        _plan(),
        _scoring_result(),
        branch_evaluation,
        reward,
    )

    assert updated.frontier_nodes["seed"].status == "closed"
    assert updated.frontier_nodes["child_seed_hash"].status == "open"
    assert updated.frontier_nodes["child_seed_hash"].knowledge_pack_ids == [
        "llm_agent_rag_engineering"
    ]
    assert updated.open_frontier_node_ids == ["sibling", "child_seed_hash"]
    assert updated.closed_frontier_node_ids == ["seed"]
    assert updated.run_shortlist_candidate_ids == ["new-fit", "legacy-a", "legacy-c"]
    assert updated.semantic_hashes_seen == ["hash-seed", "hash-child"]
    assert updated.operator_statistics["core_precision"].average_reward == pytest.approx(
        (0.6 * 2 + 2.4) / 3
    )
    assert updated.operator_statistics["core_precision"].times_selected == 3
    assert updated.remaining_budget == 2


def test_update_frontier_state_adds_closed_child_when_branch_is_exhausted() -> None:
    updated = update_frontier_state(
        _frontier_state(),
        _plan(),
        _scoring_result(shortlist_ids=[]),
        BranchEvaluation_t(
            novelty_score=0.0,
            usefulness_score=0.0,
            branch_exhausted=True,
            repair_operator_hint=None,
            evaluation_notes="done",
        ),
        NodeRewardBreakdown_t(
            delta_top_three=0.0,
            must_have_gain=0.0,
            new_fit_yield=0.0,
            novelty=0.0,
            usefulness=0.0,
            diversity=0.0,
            stability_risk_penalty=0.0,
            hard_constraint_violation=0.0,
            duplicate_penalty=0.0,
            cost_penalty=0.15,
            reward_score=0.0,
        ),
    )

    assert updated.frontier_nodes["child_seed_hash"].status == "closed"
    assert updated.closed_frontier_node_ids == ["seed", "child_seed_hash"]


def test_update_frontier_state_fails_for_missing_operator_statistics_key() -> None:
    with pytest.raises(ValueError, match="unknown_operator_statistics_key"):
        update_frontier_state(
            _frontier_state().model_copy(
                update={
                    "operator_statistics": {
                        "must_have_alias": {"average_reward": 0.4, "times_selected": 1}
                    }
                }
            ),
            _plan(),
            _scoring_result(),
            BranchEvaluation_t(
                novelty_score=0.0,
                usefulness_score=0.0,
                branch_exhausted=False,
                repair_operator_hint=None,
                evaluation_notes="",
            ),
            NodeRewardBreakdown_t(
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
                reward_score=0.0,
            ),
        )


def test_evaluate_stop_condition_respects_priority_and_controller_guard() -> None:
    state = FrontierState_t1.model_validate(_frontier_state().model_dump(mode="python"))
    branch_evaluation = BranchEvaluation_t(
        novelty_score=0.1,
        usefulness_score=0.1,
        branch_exhausted=True,
        repair_operator_hint=None,
        evaluation_notes="low gain",
    )
    reward = NodeRewardBreakdown_t(
        delta_top_three=0.0,
        must_have_gain=0.0,
        new_fit_yield=0.0,
        novelty=0.1,
        usefulness=0.1,
        diversity=0.0,
        stability_risk_penalty=0.0,
        hard_constraint_violation=0.0,
        duplicate_penalty=0.0,
        cost_penalty=0.15,
        reward_score=0.5,
    )

    assert evaluate_stop_condition(
        state.model_copy(update={"remaining_budget": 0}),
        "stop",
        branch_evaluation,
        reward,
        StopGuardThresholds(min_round_index=0),
        RuntimeRoundState(runtime_round_index=3),
    ) == ("budget_exhausted", False)
    assert evaluate_stop_condition(
        state.model_copy(update={"open_frontier_node_ids": []}),
        "stop",
        branch_evaluation,
        reward,
        StopGuardThresholds(min_round_index=0),
        RuntimeRoundState(runtime_round_index=3),
    ) == ("no_open_node", False)
    assert evaluate_stop_condition(
        state,
        "search_cts",
        branch_evaluation,
        reward,
        StopGuardThresholds(),
        RuntimeRoundState(runtime_round_index=1),
    ) == ("exhausted_low_gain", False)
    assert evaluate_stop_condition(
        state,
        "stop",
        None,
        None,
        StopGuardThresholds(min_round_index=2),
        RuntimeRoundState(runtime_round_index=1),
    ) == (None, True)
    assert evaluate_stop_condition(
        state,
        "stop",
        None,
        None,
        StopGuardThresholds(min_round_index=1),
        RuntimeRoundState(runtime_round_index=1),
    ) == ("controller_stop", False)


def test_finalize_search_run_preserves_shortlist_fact() -> None:
    result = finalize_search_run(
        _requirement_sheet(),
        FrontierState_t1.model_validate(
            _frontier_state().model_copy(
                update={"run_shortlist_candidate_ids": ["c-2", "c-1"]}
            ).model_dump(mode="python")
        ),
        "controller_stop",
        SearchRunSummaryDraft_t(run_summary="  shortlist ready  "),
    )

    assert result.model_dump(mode="python") == {
        "final_shortlist_candidate_ids": ["c-2", "c-1"],
        "run_summary": "shortlist ready",
        "stop_reason": "controller_stop",
    }
