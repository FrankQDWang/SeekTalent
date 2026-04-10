from __future__ import annotations

from seektalent.models import (
    BranchEvaluationDraft_t,
    BranchEvaluation_t,
    FrontierNode_t,
    FrontierState_t,
    FrontierState_t1,
    NodeRewardBreakdown_t,
    OperatorStatistics,
    RequirementSheet,
    RuntimeRoundState,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchRunResult,
    SearchRunSummaryDraft_t,
    SearchScoringResult_t,
    StopGuardThresholds,
    stable_deduplicate,
)


def evaluate_branch_outcome(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t,
    plan: SearchExecutionPlan_t,
    execution_result: SearchExecutionResult_t,
    scoring_result: SearchScoringResult_t,
    draft: BranchEvaluationDraft_t,
) -> BranchEvaluation_t:
    del requirement_sheet, execution_result
    parent_node = frontier_state.frontier_nodes.get(
        plan.child_frontier_node_stub.parent_frontier_node_id
    )
    if parent_node is None:
        raise ValueError(
            f"unknown_parent_frontier_node_id: {plan.child_frontier_node_stub.parent_frontier_node_id}"
        )
    allowed_repair_operator_names = {
        "core_precision",
        "must_have_alias",
        "relaxed_floor",
        "generic_expansion",
        "crossover_compose",
    }
    if plan.knowledge_pack_ids:
        allowed_repair_operator_names.update({"pack_expansion", "cross_pack_bridge"})
    repair_operator_hint = _normalized_text(draft.repair_operator_hint)
    return BranchEvaluation_t(
        novelty_score=_clamp_score(draft.novelty_score),
        usefulness_score=_clamp_score(draft.usefulness_score),
        branch_exhausted=(
            draft.branch_exhausted or not scoring_result.node_shortlist_candidate_ids
        ),
        repair_operator_hint=(
            repair_operator_hint
            if repair_operator_hint in allowed_repair_operator_names
            else None
        ),
        evaluation_notes=_normalized_text(draft.evaluation_notes),
    )


def compute_node_reward_breakdown(
    frontier_state: FrontierState_t,
    plan: SearchExecutionPlan_t,
    execution_result: SearchExecutionResult_t,
    scoring_result: SearchScoringResult_t,
    branch_evaluation: BranchEvaluation_t,
) -> NodeRewardBreakdown_t:
    parent_node = frontier_state.frontier_nodes.get(
        plan.child_frontier_node_stub.parent_frontier_node_id
    )
    if parent_node is None:
        raise ValueError(
            f"unknown_parent_frontier_node_id: {plan.child_frontier_node_stub.parent_frontier_node_id}"
        )
    parent_scores = sorted(
        parent_node.node_shortlist_score_snapshot.values(),
        reverse=True,
    )[:3]
    parent_baseline_top_three_average = (
        0.0
        if not parent_scores
        else sum(parent_scores) / len(parent_scores)
    )
    node_shortlist_ids = set(scoring_result.node_shortlist_candidate_ids)
    parent_shortlist_ids = set(parent_node.node_shortlist_candidate_ids)
    run_shortlist_ids = set(frontier_state.run_shortlist_candidate_ids)
    net_new_rows = [
        row
        for row in scoring_result.scored_candidates
        if row.candidate_id in node_shortlist_ids - parent_shortlist_ids
    ]
    shortlist_rows = [
        row
        for row in scoring_result.scored_candidates
        if row.candidate_id in node_shortlist_ids
    ]
    delta_top_three = (
        scoring_result.top_three_statistics.average_fusion_score_top_three
        - parent_baseline_top_three_average
    )
    must_have_gain = (
        0.0
        if not net_new_rows
        else sum(row.must_have_match_score for row in net_new_rows) / len(net_new_rows)
    )
    new_fit_yield = float(
        len(node_shortlist_ids - run_shortlist_ids)
    )
    diversity = (
        len(node_shortlist_ids - run_shortlist_ids)
        / max(1, len(scoring_result.node_shortlist_candidate_ids))
    )
    stability_risk_penalty = (
        0.0
        if not shortlist_rows
        else sum(row.risk_score for row in shortlist_rows) / len(shortlist_rows)
    )
    hard_constraint_violation = (
        0.0
        if not scoring_result.scored_candidates
        else sum(1 for row in scoring_result.scored_candidates if row.fit == 0)
        / len(scoring_result.scored_candidates)
    )
    duplicate_penalty = execution_result.search_page_statistics.duplicate_rate
    cost_penalty = min(
        1.0,
        0.15 * execution_result.search_page_statistics.pages_fetched,
    )
    reward_score = (
        2.0 * delta_top_three
        + 1.5 * must_have_gain
        + 0.6 * new_fit_yield
        + 0.5 * branch_evaluation.novelty_score
        + 0.5 * branch_evaluation.usefulness_score
        + 0.4 * diversity
        - 0.8 * stability_risk_penalty
        - 1.0 * hard_constraint_violation
        - 0.6 * duplicate_penalty
        - 0.4 * cost_penalty
    )
    return NodeRewardBreakdown_t(
        delta_top_three=delta_top_three,
        must_have_gain=must_have_gain,
        new_fit_yield=new_fit_yield,
        novelty=branch_evaluation.novelty_score,
        usefulness=branch_evaluation.usefulness_score,
        diversity=diversity,
        stability_risk_penalty=stability_risk_penalty,
        hard_constraint_violation=hard_constraint_violation,
        duplicate_penalty=duplicate_penalty,
        cost_penalty=cost_penalty,
        reward_score=reward_score,
    )


def update_frontier_state(
    frontier_state: FrontierState_t,
    plan: SearchExecutionPlan_t,
    scoring_result: SearchScoringResult_t,
    branch_evaluation: BranchEvaluation_t,
    reward_breakdown: NodeRewardBreakdown_t,
) -> FrontierState_t1:
    parent_node = frontier_state.frontier_nodes.get(
        plan.child_frontier_node_stub.parent_frontier_node_id
    )
    if parent_node is None:
        raise ValueError(
            f"unknown_parent_frontier_node_id: {plan.child_frontier_node_stub.parent_frontier_node_id}"
        )
    selected_operator_name = plan.child_frontier_node_stub.selected_operator_name
    operator_stats = frontier_state.operator_statistics.get(selected_operator_name)
    if operator_stats is None:
        raise ValueError(f"unknown_operator_statistics_key: {selected_operator_name}")

    node_shortlist_set = set(scoring_result.node_shortlist_candidate_ids)
    current_snapshot = {
        row.candidate_id: row.fusion_score
        for row in scoring_result.scored_candidates
        if row.candidate_id in node_shortlist_set
    }
    best_fusion_scores: dict[str, float] = {}
    for node in frontier_state.frontier_nodes.values():
        for candidate_id, fusion_score in node.node_shortlist_score_snapshot.items():
            best_fusion_scores[candidate_id] = max(
                best_fusion_scores.get(candidate_id, fusion_score),
                fusion_score,
            )
    for candidate_id, fusion_score in current_snapshot.items():
        best_fusion_scores[candidate_id] = max(
            best_fusion_scores.get(candidate_id, fusion_score),
            fusion_score,
        )

    appended_candidates = [
        candidate_id
        for candidate_id in scoring_result.node_shortlist_candidate_ids
        if candidate_id not in set(frontier_state.run_shortlist_candidate_ids)
    ]
    candidate_first_seen_rank = {
        candidate_id: index
        for index, candidate_id in enumerate(
            frontier_state.run_shortlist_candidate_ids + appended_candidates
        )
    }
    child_node = FrontierNode_t(
        frontier_node_id=plan.child_frontier_node_stub.frontier_node_id,
        parent_frontier_node_id=plan.child_frontier_node_stub.parent_frontier_node_id,
        donor_frontier_node_id=plan.child_frontier_node_stub.donor_frontier_node_id,
        selected_operator_name=selected_operator_name,
        node_query_term_pool=stable_deduplicate(
            parent_node.node_query_term_pool + plan.query_terms
        ),
        knowledge_pack_ids=list(plan.knowledge_pack_ids),
        seed_rationale=None,
        negative_terms=list(plan.runtime_only_constraints.negative_keywords),
        parent_shortlist_candidate_ids=list(parent_node.node_shortlist_candidate_ids),
        node_shortlist_candidate_ids=list(scoring_result.node_shortlist_candidate_ids),
        node_shortlist_score_snapshot=current_snapshot,
        previous_branch_evaluation=branch_evaluation,
        reward_breakdown=reward_breakdown,
        status="closed" if branch_evaluation.branch_exhausted else "open",
    )

    updated_frontier_nodes = {
        node_id: (
            node.model_copy(update={"status": "closed"})
            if node_id == parent_node.frontier_node_id
            else node.model_copy()
        )
        for node_id, node in frontier_state.frontier_nodes.items()
    }
    updated_frontier_nodes[child_node.frontier_node_id] = child_node
    updated_run_shortlist = stable_deduplicate(
        frontier_state.run_shortlist_candidate_ids
        + scoring_result.node_shortlist_candidate_ids
    )
    updated_run_shortlist.sort(
        key=lambda candidate_id: (
            -best_fusion_scores[candidate_id],
            candidate_first_seen_rank[candidate_id],
        )
    )
    updated_operator_statistics = {
        operator_name: (
            OperatorStatistics(
                average_reward=(
                    (
                        stats.average_reward * stats.times_selected
                        + reward_breakdown.reward_score
                    )
                    / (stats.times_selected + 1)
                ),
                times_selected=stats.times_selected + 1,
            )
            if operator_name == selected_operator_name
            else stats.model_copy()
        )
        for operator_name, stats in frontier_state.operator_statistics.items()
    }
    closed_ids = stable_deduplicate(
        frontier_state.closed_frontier_node_ids
        + [parent_node.frontier_node_id]
        + ([child_node.frontier_node_id] if child_node.status == "closed" else [])
    )
    open_ids = stable_deduplicate(
        [
            node_id
            for node_id in frontier_state.open_frontier_node_ids
            if node_id != parent_node.frontier_node_id
        ]
        + ([child_node.frontier_node_id] if child_node.status == "open" else [])
    )
    return FrontierState_t1(
        frontier_nodes=updated_frontier_nodes,
        open_frontier_node_ids=open_ids,
        closed_frontier_node_ids=closed_ids,
        run_term_catalog=stable_deduplicate(
            frontier_state.run_term_catalog + plan.query_terms
        ),
        run_shortlist_candidate_ids=updated_run_shortlist,
        semantic_hashes_seen=stable_deduplicate(
            frontier_state.semantic_hashes_seen + [plan.semantic_hash]
        ),
        operator_statistics=updated_operator_statistics,
        remaining_budget=frontier_state.remaining_budget - 1,
    )


def evaluate_stop_condition(
    frontier_state: FrontierState_t1,
    decision_action: str,
    branch_evaluation: BranchEvaluation_t | None,
    reward_breakdown: NodeRewardBreakdown_t | None,
    stop_guard_thresholds: StopGuardThresholds,
    runtime_round_state: RuntimeRoundState,
) -> tuple[str | None, bool]:
    if frontier_state.remaining_budget <= 0:
        return "budget_exhausted", False
    if not frontier_state.open_frontier_node_ids:
        return "no_open_node", False
    if (
        branch_evaluation is not None
        and reward_breakdown is not None
        and branch_evaluation.branch_exhausted
        and branch_evaluation.novelty_score < stop_guard_thresholds.novelty_floor
        and branch_evaluation.usefulness_score < stop_guard_thresholds.usefulness_floor
        and reward_breakdown.reward_score < stop_guard_thresholds.reward_floor
    ):
        return "exhausted_low_gain", False
    if (
        decision_action == "stop"
        and runtime_round_state.runtime_round_index
        >= stop_guard_thresholds.min_round_index
    ):
        return "controller_stop", False
    return None, True


def finalize_search_run(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t1,
    stop_reason: str,
    draft: SearchRunSummaryDraft_t,
) -> SearchRunResult:
    del requirement_sheet
    return SearchRunResult(
        final_shortlist_candidate_ids=list(frontier_state.run_shortlist_candidate_ids),
        run_summary=_normalized_text(draft.run_summary),
        stop_reason=stop_reason,
    )


def _clamp_score(value: float) -> float:
    return min(1.0, max(0.0, value))


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


__all__ = [
    "compute_node_reward_breakdown",
    "evaluate_branch_outcome",
    "evaluate_stop_condition",
    "finalize_search_run",
    "update_frontier_state",
]
