from __future__ import annotations

from types import SimpleNamespace

import pytest

from seektalent.models import (
    BranchEvaluationDraft_t,
    BranchEvaluation_t,
    CandidateEvidenceCard_t,
    FrontierNode_t,
    FrontierState_t,
    FrontierState_t1,
    HardConstraints,
    NodeRewardBreakdown_t,
    RequirementPreferences,
    RequirementSheet,
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
    _final_candidate_cards,
    _reviewer_summary,
    build_effective_stop_guard,
    compute_node_reward_breakdown,
    evaluate_branch_outcome,
    evaluate_stop_condition,
    finalize_search_run,
    update_frontier_state,
)
from seektalent.runtime_budget import build_runtime_budget_state


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
                branch_role="root_anchor",
                root_anchor_frontier_node_id="seed",
                node_shortlist_candidate_ids=["legacy-a", "legacy-b"],
                node_shortlist_score_snapshot={"legacy-a": 0.92, "legacy-b": 0.81},
                status="open",
            ),
            "sibling": FrontierNode_t(
                frontier_node_id="sibling",
                selected_operator_name="core_precision",
                node_query_term_pool=["ranking"],
                knowledge_pack_ids=["finance_risk_control_ai"],
                branch_role="repair_hypothesis",
                root_anchor_frontier_node_id="sibling",
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
            "pack_bridge": {"average_reward": 0.0, "times_selected": 0},
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
                "branch_role": "repair_hypothesis",
                "root_anchor_frontier_node_id": "seed",
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
        candidate_evidence_cards=[
            CandidateEvidenceCard_t(
                candidate_id="new-fit",
                review_recommendation="advance",
                must_have_matrix=[],
                preferred_evidence=[],
                gap_signals=[],
                risk_signals=[],
                card_summary="new-fit looks strong",
            )
        ],
        top_three_statistics=TopThreeStatistics(
            average_fusion_score_top_three=top_three_average
        ),
    )


def test_evaluate_branch_outcome_preserves_legal_draft_values() -> None:
    result = evaluate_branch_outcome(
        _requirement_sheet(),
        _frontier_state(),
        _plan(),
        _execution_result(),
        _scoring_result(),
        BranchEvaluationDraft_t(
            novelty_score=0.8,
            usefulness_score=0.4,
            branch_exhausted=False,
            repair_operator_hint="pack_bridge",
            evaluation_notes="  too broad  ",
        ),
    )

    assert result == BranchEvaluation_t(
        novelty_score=0.8,
        usefulness_score=0.4,
        branch_exhausted=False,
        repair_operator_hint="pack_bridge",
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


def test_update_frontier_state_keeps_root_anchor_open_and_sorts_run_shortlist() -> None:
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

    assert updated.frontier_nodes["seed"].status == "open"
    assert updated.frontier_nodes["seed"].previous_branch_evaluation == branch_evaluation
    assert updated.frontier_nodes["seed"].reward_breakdown == reward
    assert updated.frontier_nodes["seed"].node_shortlist_candidate_ids == ["new-fit", "legacy-c"]
    assert updated.frontier_nodes["child_seed_hash"].status == "open"
    assert updated.frontier_nodes["child_seed_hash"].branch_role == "repair_hypothesis"
    assert updated.frontier_nodes["child_seed_hash"].root_anchor_frontier_node_id == "seed"
    assert updated.frontier_nodes["child_seed_hash"].knowledge_pack_ids == [
        "llm_agent_rag_engineering"
    ]
    assert updated.open_frontier_node_ids == ["seed", "sibling", "child_seed_hash"]
    assert updated.closed_frontier_node_ids == []
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

    assert updated.frontier_nodes["seed"].status == "open"
    assert updated.frontier_nodes["child_seed_hash"].status == "closed"
    assert updated.closed_frontier_node_ids == ["child_seed_hash"]


def test_update_frontier_state_closes_repair_parent_and_opens_child() -> None:
    base_state = _frontier_state()
    updated = update_frontier_state(
        base_state.model_copy(
            update={
                "frontier_nodes": {
                    **base_state.frontier_nodes,
                    "seed": base_state.frontier_nodes["seed"].model_copy(
                        update={"branch_role": "repair_hypothesis"}
                    ),
                }
            }
        ),
        _plan(),
        _scoring_result(),
        BranchEvaluation_t(
            novelty_score=0.3,
            usefulness_score=0.4,
            branch_exhausted=False,
            repair_operator_hint="core_precision",
            evaluation_notes="continue",
        ),
        NodeRewardBreakdown_t(
            delta_top_three=0.0,
            must_have_gain=0.0,
            new_fit_yield=0.0,
            novelty=0.3,
            usefulness=0.4,
            diversity=0.0,
            stability_risk_penalty=0.0,
            hard_constraint_violation=0.0,
            duplicate_penalty=0.0,
            cost_penalty=0.0,
            reward_score=1.0,
        ),
    )

    assert updated.frontier_nodes["seed"].status == "closed"
    assert updated.frontier_nodes["child_seed_hash"].status == "open"
    assert updated.open_frontier_node_ids == ["sibling", "child_seed_hash"]
    assert updated.closed_frontier_node_ids == ["seed"]


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


def test_evaluate_stop_condition_respects_priority_and_phase_gates() -> None:
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
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=3,
            remaining_budget=0,
        ),
    ) == ("budget_exhausted", False)
    assert evaluate_stop_condition(
        state.model_copy(update={"open_frontier_node_ids": []}),
        "stop",
        branch_evaluation,
        reward,
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=3,
            remaining_budget=2,
        ),
    ) == ("no_open_node", False)
    assert evaluate_stop_condition(
        state,
        "search_cts",
        branch_evaluation,
        reward,
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=1,
            remaining_budget=3,
        ),
    ) == (None, True)
    assert evaluate_stop_condition(
        state,
        "stop",
        None,
        None,
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=1,
            remaining_budget=3,
        ),
    ) == (None, True)
    assert evaluate_stop_condition(
        state,
        "stop",
        None,
        None,
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=2,
            remaining_budget=2,
        ),
    ) == ("controller_stop", False)
    assert evaluate_stop_condition(
        state,
        "search_cts",
        branch_evaluation,
        reward,
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=4,
            remaining_budget=1,
        ),
    ) == ("exhausted_low_gain", False)


def test_evaluate_stop_condition_returns_no_productive_open_path() -> None:
    base_state = _frontier_state()
    state = FrontierState_t1.model_validate(
        base_state.model_copy(
            update={
                "frontier_nodes": {
                    "seed": base_state.frontier_nodes["seed"].model_copy(
                        update={
                            "previous_branch_evaluation": BranchEvaluation_t(
                                novelty_score=0.1,
                                usefulness_score=0.1,
                                branch_exhausted=True,
                                repair_operator_hint=None,
                                evaluation_notes="done",
                            ),
                            "reward_breakdown": NodeRewardBreakdown_t(
                                delta_top_three=0.0,
                                must_have_gain=0.0,
                                new_fit_yield=0.0,
                                novelty=0.1,
                                usefulness=0.1,
                                diversity=0.0,
                                stability_risk_penalty=0.0,
                                hard_constraint_violation=0.0,
                                duplicate_penalty=0.0,
                                cost_penalty=0.0,
                                reward_score=0.5,
                            ),
                        }
                    ),
                    "sibling": base_state.frontier_nodes["sibling"].model_copy(
                        update={
                            "previous_branch_evaluation": BranchEvaluation_t(
                                novelty_score=0.1,
                                usefulness_score=0.1,
                                branch_exhausted=True,
                                repair_operator_hint=None,
                                evaluation_notes="done",
                            ),
                            "reward_breakdown": NodeRewardBreakdown_t(
                                delta_top_three=0.0,
                                must_have_gain=0.0,
                                new_fit_yield=0.0,
                                novelty=0.1,
                                usefulness=0.1,
                                diversity=0.0,
                                stability_risk_penalty=0.0,
                                hard_constraint_violation=0.0,
                                duplicate_penalty=0.0,
                                cost_penalty=0.0,
                                reward_score=0.5,
                            ),
                        }
                    ),
                }
            }
        ).model_dump(mode="python")
    )

    assert evaluate_stop_condition(
        state,
        "search_cts",
        BranchEvaluation_t(
            novelty_score=0.1,
            usefulness_score=0.1,
            branch_exhausted=True,
            repair_operator_hint=None,
            evaluation_notes="done",
        ),
        NodeRewardBreakdown_t(
            delta_top_three=0.0,
            must_have_gain=0.0,
            new_fit_yield=0.0,
            novelty=0.1,
            usefulness=0.1,
            diversity=0.0,
            stability_risk_penalty=0.0,
            hard_constraint_violation=0.0,
            duplicate_penalty=0.0,
            cost_penalty=0.0,
            reward_score=0.5,
        ),
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=2,
            remaining_budget=2,
        ),
    ) == ("no_productive_open_path", False)


def test_build_effective_stop_guard_uses_phase_gate_owner() -> None:
    explore_guard = build_effective_stop_guard(
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=0,
            remaining_budget=5,
        ),
    )
    harvest_guard = build_effective_stop_guard(
        StopGuardThresholds(),
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=4,
            remaining_budget=1,
        ),
    )

    assert explore_guard.search_phase == "explore"
    assert explore_guard.controller_stop_allowed is False
    assert explore_guard.exhausted_low_gain_allowed is False
    assert harvest_guard.search_phase == "harvest"
    assert harvest_guard.controller_stop_allowed is True
    assert harvest_guard.exhausted_low_gain_allowed is True


def test_finalize_search_run_returns_cards_only_public_result() -> None:
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
        "final_candidate_cards": [],
        "reviewer_summary": "No final shortlist candidate cards.",
        "run_summary": "shortlist ready",
        "stop_reason": "controller_stop",
    }


def test_reviewer_summary_uses_triage_language_and_counts() -> None:
    summary = _reviewer_summary(
        [
            CandidateEvidenceCard_t(
                candidate_id="c-1",
                review_recommendation="advance",
                must_have_matrix=[],
                preferred_evidence=[],
                gap_signals=[],
                risk_signals=[],
                card_summary="advance card",
            ),
            CandidateEvidenceCard_t(
                candidate_id="c-2",
                review_recommendation="hold",
                must_have_matrix=[],
                preferred_evidence=[],
                gap_signals=[
                    {
                        "signal": "retrieval",
                        "display_text": "Only weak evidence for retrieval",
                    }
                ],
                risk_signals=[
                    {
                        "signal": "min_years",
                        "display_text": "Below minimum years of experience",
                    }
                ],
                card_summary="hold card",
            ),
            CandidateEvidenceCard_t(
                candidate_id="c-3",
                review_recommendation="reject",
                must_have_matrix=[],
                preferred_evidence=[],
                gap_signals=[
                    {
                        "signal": "retrieval",
                        "display_text": "Only weak evidence for retrieval",
                    }
                ],
                risk_signals=[
                    {
                        "signal": "min_years",
                        "display_text": "Below minimum years of experience",
                    }
                ],
                card_summary="reject card",
            ),
        ]
    )

    assert summary == (
        "Reviewer summary: 1 advance-ready, 1 need manual review, 1 reject; "
        "Top gaps: Only weak evidence for retrieval (2); "
        "Top risks: Below minimum years of experience (2)"
    )


def test_final_candidate_cards_respects_top_k_limit() -> None:
    frontier_state = FrontierState_t1.model_validate(
        _frontier_state().model_copy(
            update={"run_shortlist_candidate_ids": [f"c-{index}" for index in range(12)]}
        ).model_dump(mode="python")
    )
    scoring_result = SearchScoringResult_t(
        scored_candidates=[
            ScoredCandidate_t(
                candidate_id=f"c-{index}",
                fit=1,
                rerank_raw=1.0,
                rerank_normalized=1.0,
                must_have_match_score_raw=100,
                must_have_match_score=1.0,
                preferred_match_score_raw=0,
                preferred_match_score=0.0,
                risk_score_raw=0,
                risk_score=0.0,
                risk_flags=[],
                fit_gate_failures=[],
                fusion_score=1.0,
            )
            for index in range(12)
        ],
        node_shortlist_candidate_ids=[f"c-{index}" for index in range(12)],
        explanation_candidate_ids=[f"c-{index}" for index in range(12)],
        candidate_evidence_cards=[
            CandidateEvidenceCard_t(
                candidate_id=f"c-{index}",
                review_recommendation="advance",
                must_have_matrix=[],
                preferred_evidence=[],
                gap_signals=[],
                risk_signals=[],
                card_summary=f"card-{index}",
            )
            for index in range(12)
        ],
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=1.0),
    )
    cards = _final_candidate_cards(
        frontier_state=frontier_state,
        rounds=[SimpleNamespace(scoring_result=scoring_result)],
        top_k=10,
    )
    assert len(cards) == 10
    assert [card.candidate_id for card in cards] == [f"c-{index}" for index in range(10)]
