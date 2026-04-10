from __future__ import annotations

from seektalent.models import (
    CrossoverGuardThresholds,
    DonorCandidateNodeSummary,
    FitGateConstraints,
    FrontierHeadSummary,
    FrontierNode_t,
    FrontierState_t,
    FrontierState_t1,
    OperatorName,
    RequirementSheet,
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
) -> SearchControllerContext_t:
    open_nodes = [
        frontier_state.frontier_nodes[node_id]
        for node_id in frontier_state.open_frontier_node_ids
    ]
    if not open_nodes:
        raise ValueError("frontier_state has no open frontier nodes")

    active_node = open_nodes[0]
    active_score = _priority_score(active_node, frontier_state, requirement_sheet)
    for node in open_nodes[1:]:
        score = _priority_score(node, frontier_state, requirement_sheet)
        if score > active_score:
            active_node = node
            active_score = score

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
        active_frontier_node_summary={
            "frontier_node_id": active_node.frontier_node_id,
            "selected_operator_name": active_node.selected_operator_name,
            "node_query_term_pool": list(active_node.node_query_term_pool),
            "node_shortlist_candidate_ids": list(active_node.node_shortlist_candidate_ids),
        },
        donor_candidate_node_summaries=donor_candidates,
        frontier_head_summary=FrontierHeadSummary(
            open_node_count=len(frontier_state.open_frontier_node_ids),
            remaining_budget=frontier_state.remaining_budget,
            highest_priority_score=active_score,
        ),
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


def _priority_score(
    node: FrontierNode_t,
    frontier_state: FrontierState_t,
    requirement_sheet: RequirementSheet,
) -> float:
    return (
        _frontier_priority(node, frontier_state)
        + _unmet_requirement_bonus(node, requirement_sheet)
        - _saturation_penalty(node, frontier_state)
    )


def _frontier_priority(node: FrontierNode_t, frontier_state: FrontierState_t) -> float:
    seed_bonus = 1.5 if node.parent_frontier_node_id is None else 0.0
    operator_reward = frontier_state.operator_statistics.get(node.selected_operator_name)
    average_reward = 0.0 if operator_reward is None else operator_reward.average_reward
    no_previous_branch_bonus = 0.4 if node.previous_branch_evaluation is None else 0.0
    exhausted_penalty = (
        1.0
        if node.previous_branch_evaluation is not None
        and node.previous_branch_evaluation.branch_exhausted
        else 0.0
    )
    return seed_bonus + 0.8 * average_reward + no_previous_branch_bonus - exhausted_penalty


def _unmet_requirement_bonus(
    node: FrontierNode_t,
    requirement_sheet: RequirementSheet,
) -> float:
    return 0.6 * sum(
        1 - _query_pool_hit(node, capability)
        for capability in requirement_sheet.must_have_capabilities
    )


def _saturation_penalty(node: FrontierNode_t, frontier_state: FrontierState_t) -> float:
    if not node.node_shortlist_candidate_ids:
        return 0.0
    shortlist_ids = set(node.node_shortlist_candidate_ids)
    run_shortlist_ids = set(frontier_state.run_shortlist_candidate_ids)
    overlap_ratio = len(shortlist_ids & run_shortlist_ids) / len(node.node_shortlist_candidate_ids)
    return 1.2 * overlap_ratio


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
