from __future__ import annotations

import math

from seektalent.models import (
    CrossoverGuardThresholds,
    DonorCandidateNodeSummary,
    FitGateConstraints,
    FrontierHeadSummary,
    FrontierNode_t,
    FrontierSelectionBreakdown,
    FrontierSelectionCandidateSummary,
    FrontierState_t,
    FrontierState_t1,
    OperatorName,
    RequirementSheet,
    RuntimeBudgetState,
    RuntimeTermBudgetPolicy,
    ScoringPolicy,
    SearchControllerContext_t,
    SearchControllerDecisionDraft_t,
    SearchControllerDecision_t,
    UnmetRequirementWeight,
    stable_deduplicate,
)


def select_active_frontier_node(
    frontier_state: FrontierState_t,
    requirement_sheet: RequirementSheet,
    scoring_policy: ScoringPolicy,
    crossover_thresholds: CrossoverGuardThresholds,
    term_budget_policy: RuntimeTermBudgetPolicy,
    runtime_budget_state: RuntimeBudgetState,
) -> SearchControllerContext_t:
    open_nodes = [
        frontier_state.frontier_nodes[node_id]
        for node_id in frontier_state.open_frontier_node_ids
        if frontier_state.frontier_nodes[node_id].status == "open"
    ]
    if not open_nodes:
        raise ValueError("frontier_state has no open frontier nodes")
    if any(
        node.previous_branch_evaluation is not None
        and node.previous_branch_evaluation.branch_exhausted
        for node in open_nodes
    ):
        raise ValueError("open_frontier_node_marked_exhausted")

    selection_ranking = _selection_ranking(
        open_nodes,
        frontier_state,
        requirement_sheet,
        runtime_budget_state,
    )
    active_candidate = selection_ranking[0]
    active_node = next(
        node
        for node in open_nodes
        if node.frontier_node_id == active_candidate.frontier_node_id
    )

    donor_candidates = _donor_candidate_summaries(
        active_node,
        open_nodes,
        requirement_sheet,
        crossover_thresholds,
    )
    allowed_operator_names: list[OperatorName] = [
        "core_precision",
        "must_have_alias",
        "relaxed_floor",
        "generic_expansion",
        "crossover_compose",
    ]
    if active_node.knowledge_pack_ids:
        allowed_operator_names[4:4] = [
            "pack_expansion",
            "cross_pack_bridge",
        ]

    return SearchControllerContext_t(
        role_title=requirement_sheet.role_title,
        role_summary=requirement_sheet.role_summary,
        active_frontier_node_summary={
            "frontier_node_id": active_node.frontier_node_id,
            "selected_operator_name": active_node.selected_operator_name,
            "node_query_term_pool": list(active_node.node_query_term_pool),
            "node_shortlist_candidate_ids": list(active_node.node_shortlist_candidate_ids),
        },
        donor_candidate_node_summaries=donor_candidates,
        frontier_head_summary=FrontierHeadSummary(
            open_node_count=len(open_nodes),
            remaining_budget=frontier_state.remaining_budget,
            highest_selection_score=active_candidate.breakdown.final_selection_score,
        ),
        active_selection_breakdown=active_candidate.breakdown,
        selection_ranking=selection_ranking,
        unmet_requirement_weights=[
            UnmetRequirementWeight(
                capability=capability,
                weight=1.0 if _query_pool_hit(active_node, capability) == 0 else 0.3,
            )
            for capability in requirement_sheet.must_have_capabilities
        ],
        operator_statistics_summary=dict(frontier_state.operator_statistics),
        allowed_operator_names=allowed_operator_names,
        term_budget_range=_term_budget_range(
            frontier_state.remaining_budget,
            term_budget_policy,
        ),
        fit_gate_constraints=FitGateConstraints.model_validate(
            scoring_policy.fit_gate_constraints.model_dump(mode="python")
        ),
        runtime_budget_state=runtime_budget_state,
    )


def generate_search_controller_decision(
    context: SearchControllerContext_t,
    draft: SearchControllerDecisionDraft_t,
) -> SearchControllerDecision_t:
    action = "stop" if _normalized_text(draft.action) == "stop" else "search_cts"
    active_node = context.active_frontier_node_summary
    active_operator_name = active_node.selected_operator_name
    normalized_operator_name = _normalized_operator_name(
        draft.selected_operator_name,
        context.allowed_operator_names,
        active_operator_name,
    )

    if action == "stop":
        operator_args: dict[str, object] = {}
    elif normalized_operator_name != "crossover_compose":
        requested_terms = stable_deduplicate(
            _string_list(_operator_args(draft).get("additional_terms"))
        )
        max_additional_terms = max(
            0,
            context.term_budget_range[1] - len(active_node.node_query_term_pool),
        )
        operator_args = {"additional_terms": requested_terms[:max_additional_terms]}
    else:
        donor_candidate_ids = {
            donor.frontier_node_id
            for donor in context.donor_candidate_node_summaries
        }
        donor_frontier_node_id = _normalized_text(
            _operator_args(draft).get("donor_frontier_node_id")
        )
        operator_args = {
            "donor_frontier_node_id": (
                donor_frontier_node_id
                if donor_frontier_node_id in donor_candidate_ids
                else None
            ),
            "crossover_rationale": _normalized_text(
                _operator_args(draft).get("crossover_rationale")
            ),
            "shared_anchor_terms": stable_deduplicate(
                _string_list(_operator_args(draft).get("shared_anchor_terms"))
            ),
            "donor_terms_used": stable_deduplicate(
                _string_list(_operator_args(draft).get("donor_terms_used"))
            ),
        }

    return SearchControllerDecision_t(
        action=action,
        target_frontier_node_id=active_node.frontier_node_id,
        selected_operator_name=normalized_operator_name,
        operator_args=operator_args,
        expected_gain_hypothesis=_normalized_text(draft.expected_gain_hypothesis),
    )


def carry_forward_frontier_state(frontier_state: FrontierState_t) -> FrontierState_t1:
    return FrontierState_t1.model_validate(frontier_state.model_dump(mode="python"))


def _selection_ranking(
    open_nodes: list[FrontierNode_t],
    frontier_state: FrontierState_t,
    requirement_sheet: RequirementSheet,
    runtime_budget_state: RuntimeBudgetState,
) -> list[FrontierSelectionCandidateSummary]:
    indexed_nodes = list(enumerate(open_nodes))
    ranking = [
        FrontierSelectionCandidateSummary(
            frontier_node_id=node.frontier_node_id,
            selected_operator_name=node.selected_operator_name,
            breakdown=_selection_breakdown(
                node,
                frontier_state,
                requirement_sheet,
                runtime_budget_state,
            ),
        )
        for _, node in indexed_nodes
    ]
    node_index = {
        node.frontier_node_id: index
        for index, node in indexed_nodes
    }
    ranking.sort(
        key=lambda item: (
            -item.breakdown.final_selection_score,
            node_index[item.frontier_node_id],
        )
    )
    return ranking


def _selection_breakdown(
    node: FrontierNode_t,
    frontier_state: FrontierState_t,
    requirement_sheet: RequirementSheet,
    runtime_budget_state: RuntimeBudgetState,
) -> FrontierSelectionBreakdown:
    weights = _selection_phase_weights(runtime_budget_state.search_phase)
    operator_exploitation_score = _operator_exploitation_score(node, frontier_state)
    operator_exploration_bonus = _operator_exploration_bonus(node, frontier_state)
    coverage_opportunity_score = _coverage_opportunity_score(node, requirement_sheet)
    incremental_value_score = _incremental_value_score(node)
    fresh_node_bonus = 1.0 if node.previous_branch_evaluation is None else 0.0
    redundancy_penalty = _redundancy_penalty(node, frontier_state)
    final_selection_score = (
        weights["exploit"] * operator_exploitation_score
        + weights["explore"] * operator_exploration_bonus
        + weights["coverage"] * coverage_opportunity_score
        + weights["incremental"] * incremental_value_score
        + weights["fresh"] * fresh_node_bonus
        - weights["redundancy"] * redundancy_penalty
    )
    return FrontierSelectionBreakdown(
        search_phase=runtime_budget_state.search_phase,
        operator_exploitation_score=operator_exploitation_score,
        operator_exploration_bonus=operator_exploration_bonus,
        coverage_opportunity_score=coverage_opportunity_score,
        incremental_value_score=incremental_value_score,
        fresh_node_bonus=fresh_node_bonus,
        redundancy_penalty=redundancy_penalty,
        final_selection_score=final_selection_score,
    )


def _selection_phase_weights(search_phase: str) -> dict[str, float]:
    if search_phase == "explore":
        return {
            "exploit": 0.6,
            "explore": 1.6,
            "coverage": 1.2,
            "incremental": 0.2,
            "fresh": 0.8,
            "redundancy": 0.4,
        }
    if search_phase == "harvest":
        return {
            "exploit": 1.4,
            "explore": 0.3,
            "coverage": 0.2,
            "incremental": 1.2,
            "fresh": 0.0,
            "redundancy": 1.2,
        }
    return {
        "exploit": 1.0,
        "explore": 1.0,
        "coverage": 0.8,
        "incremental": 0.8,
        "fresh": 0.3,
        "redundancy": 0.8,
    }


def _operator_exploitation_score(node: FrontierNode_t, frontier_state: FrontierState_t) -> float:
    operator_reward = frontier_state.operator_statistics.get(node.selected_operator_name)
    average_reward = 0.0 if operator_reward is None else operator_reward.average_reward
    positive_avg_reward = max(average_reward, 0.0)
    return positive_avg_reward / (1.0 + positive_avg_reward)


def _operator_exploration_bonus(node: FrontierNode_t, frontier_state: FrontierState_t) -> float:
    total_operator_pulls = sum(
        stats.times_selected for stats in frontier_state.operator_statistics.values()
    )
    operator_reward = frontier_state.operator_statistics.get(node.selected_operator_name)
    operator_pulls = 0 if operator_reward is None else operator_reward.times_selected
    return math.sqrt(2.0 * math.log(total_operator_pulls + 2.0) / (operator_pulls + 1.0))


def _coverage_opportunity_score(
    node: FrontierNode_t,
    requirement_sheet: RequirementSheet,
) -> float:
    must_total = max(1, len(requirement_sheet.must_have_capabilities))
    hit_count = sum(
        _query_pool_hit(node, capability)
        for capability in requirement_sheet.must_have_capabilities
    )
    coverage_ratio = hit_count / must_total
    if 0.0 < coverage_ratio < 1.0:
        return coverage_ratio
    return 0.0


def _incremental_value_score(node: FrontierNode_t) -> float:
    if node.reward_breakdown is None:
        return 0.0
    bounded_new_fit_yield = (
        node.reward_breakdown.new_fit_yield / (1.0 + node.reward_breakdown.new_fit_yield)
    )
    return 0.7 * bounded_new_fit_yield + 0.3 * node.reward_breakdown.diversity


def _redundancy_penalty(node: FrontierNode_t, frontier_state: FrontierState_t) -> float:
    if not node.node_shortlist_candidate_ids:
        return 0.0
    shortlist_ids = set(node.node_shortlist_candidate_ids)
    run_shortlist_ids = set(frontier_state.run_shortlist_candidate_ids)
    return len(shortlist_ids & run_shortlist_ids) / len(node.node_shortlist_candidate_ids)


def _query_pool_hit(node: FrontierNode_t, capability: str) -> int:
    normalized_capability = _normalized_text(capability)
    for term in node.node_query_term_pool:
        normalized_term = _normalized_text(term)
        if (
            normalized_term
            and normalized_capability
            and (
                normalized_term in normalized_capability
                or normalized_capability in normalized_term
            )
        ):
            return 1
    return 0


def _donor_candidate_summaries(
    active_node: FrontierNode_t,
    open_nodes: list[FrontierNode_t],
    requirement_sheet: RequirementSheet,
    crossover_thresholds: CrossoverGuardThresholds,
) -> list[DonorCandidateNodeSummary]:
    donor_candidates = []
    for donor in open_nodes:
        if donor.frontier_node_id == active_node.frontier_node_id:
            continue
        if donor.status != "open" or donor.reward_breakdown is None:
            continue
        shared_anchor_terms = _shared_anchor_terms(active_node, donor)
        expected_incremental_coverage = _unmet_must_haves_supported_by(
            donor,
            active_node,
            requirement_sheet,
        )
        if donor.reward_breakdown.reward_score < crossover_thresholds.min_reward_score:
            continue
        if len(shared_anchor_terms) < crossover_thresholds.min_shared_anchor_terms:
            continue
        if not expected_incremental_coverage:
            continue
        donor_candidates.append(
            DonorCandidateNodeSummary(
                frontier_node_id=donor.frontier_node_id,
                shared_anchor_terms=shared_anchor_terms,
                expected_incremental_coverage=expected_incremental_coverage,
                reward_score=donor.reward_breakdown.reward_score,
            )
        )
    donor_candidates.sort(
        key=lambda donor: (
            len(donor.expected_incremental_coverage),
            donor.reward_score,
        ),
        reverse=True,
    )
    return donor_candidates[: crossover_thresholds.max_donor_candidates]


def _shared_anchor_terms(
    active_node: FrontierNode_t,
    donor_node: FrontierNode_t,
) -> list[str]:
    donor_terms = set(donor_node.node_query_term_pool)
    return stable_deduplicate(
        [term for term in active_node.node_query_term_pool if term in donor_terms]
    )


def _unmet_must_haves_supported_by(
    donor_node: FrontierNode_t,
    active_node: FrontierNode_t,
    requirement_sheet: RequirementSheet,
) -> list[str]:
    return [
        capability
        for capability in requirement_sheet.must_have_capabilities
        if _query_pool_hit(active_node, capability) == 0
        and _query_pool_hit(donor_node, capability) == 1
    ]


def _term_budget_range(
    remaining_budget: int,
    term_budget_policy: RuntimeTermBudgetPolicy,
) -> tuple[int, int]:
    if remaining_budget >= 4:
        return term_budget_policy.high_budget_range
    if remaining_budget >= 2:
        return term_budget_policy.medium_budget_range
    return term_budget_policy.low_budget_range


def _normalized_operator_name(
    requested_operator_name: str,
    allowed_operator_names: list[OperatorName],
    fallback: OperatorName,
) -> OperatorName:
    clean = _normalized_text(requested_operator_name)
    for operator_name in allowed_operator_names:
        if operator_name == clean:
            return operator_name
    return fallback


def _operator_args(draft: SearchControllerDecisionDraft_t) -> dict[str, object]:
    return draft.operator_args if isinstance(draft.operator_args, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


__all__ = [
    "carry_forward_frontier_state",
    "generate_search_controller_decision",
    "select_active_frontier_node",
]
