from __future__ import annotations

import math

from seektalent.runtime_budget import derive_max_query_terms
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
    PhaseSelectionWeights,
    RequirementSheet,
    RuntimeBudgetState,
    RuntimeSelectionPolicy,
    RuntimeTermBudgetPolicy,
    RewriteChoiceScoreBreakdown,
    RewriteChoiceTrace,
    RewriteFitnessWeights,
    RewriteTermCandidate,
    ScoringPolicy,
    SearchControllerContext_t,
    SearchControllerDecisionDraft_t,
    SearchControllerDecision_t,
    UnmetRequirementWeight,
    stable_deduplicate,
)
from seektalent.query_terms import query_terms_hit


def select_active_frontier_node(
    frontier_state: FrontierState_t,
    requirement_sheet: RequirementSheet,
    scoring_policy: ScoringPolicy,
    crossover_thresholds: CrossoverGuardThresholds,
    term_budget_policy: RuntimeTermBudgetPolicy,
    runtime_budget_state: RuntimeBudgetState,
    selection_policy: RuntimeSelectionPolicy | None = None,
) -> SearchControllerContext_t:
    open_nodes = [
        frontier_state.frontier_nodes[node_id]
        for node_id in frontier_state.open_frontier_node_ids
        if frontier_state.frontier_nodes[node_id].status == "open"
    ]
    if not open_nodes:
        raise ValueError("frontier_state has no open frontier nodes")

    selection_ranking = _selection_ranking(
        open_nodes,
        frontier_state,
        requirement_sheet,
        runtime_budget_state,
        selection_policy or RuntimeSelectionPolicy(),
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
    unmet_must_haves = _unmet_must_haves(active_node, requirement_sheet)
    allowed_operator_names = _allowed_operator_names(
        runtime_budget_state.search_phase,
        has_pack=bool(active_node.knowledge_pack_ids),
        has_legal_donors=bool(donor_candidates),
        unmet_must_haves=unmet_must_haves,
    )
    override_reason = (
        "harvest_unmet_must_have_repair"
        if runtime_budget_state.search_phase == "harvest" and unmet_must_haves
        else "none"
    )

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
                weight=1.0 if capability in unmet_must_haves else 0.3,
            )
            for capability in requirement_sheet.must_have_capabilities
        ],
        operator_statistics_summary=dict(frontier_state.operator_statistics),
        allowed_operator_names=allowed_operator_names,
        operator_surface_override_reason=override_reason,
        operator_surface_unmet_must_haves=unmet_must_haves,
        rewrite_term_candidates=list(active_node.rewrite_term_candidates),
        max_query_terms=derive_max_query_terms(
            runtime_budget_state,
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
    rewrite_fitness_weights: RewriteFitnessWeights | None = None,
) -> SearchControllerDecision_t:
    return generate_search_controller_decision_with_trace(
        context,
        draft,
        rewrite_fitness_weights,
    )[0]


def generate_search_controller_decision_with_trace(
    context: SearchControllerContext_t,
    draft: SearchControllerDecisionDraft_t,
    rewrite_fitness_weights: RewriteFitnessWeights | None = None,
) -> tuple[SearchControllerDecision_t, RewriteChoiceTrace | None]:
    action = "stop" if _normalized_text(draft.action) == "stop" else "search_cts"
    active_node = context.active_frontier_node_summary
    active_operator_name = active_node.selected_operator_name
    rewrite_trace: RewriteChoiceTrace | None = None
    normalized_operator_name = _normalized_operator_name(
        draft.selected_operator_name,
        context.allowed_operator_names,
        active_operator_name,
    )

    if action == "stop":
        operator_args: dict[str, object] = {}
    elif normalized_operator_name != "crossover_compose":
        query_terms, rewrite_trace = _normalized_non_crossover_query(
            normalized_operator_name,
            context,
            _operator_args(draft).get("query_terms"),
            rewrite_fitness_weights or RewriteFitnessWeights(),
        )
        operator_args = {
            "query_terms": query_terms,
        }
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

    return (
        SearchControllerDecision_t(
            action=action,
            target_frontier_node_id=active_node.frontier_node_id,
            selected_operator_name=normalized_operator_name,
            operator_args=operator_args,
            expected_gain_hypothesis=_normalized_text(draft.expected_gain_hypothesis),
        ),
        rewrite_trace,
    )


def carry_forward_frontier_state(frontier_state: FrontierState_t) -> FrontierState_t1:
    return FrontierState_t1.model_validate(frontier_state.model_dump(mode="python"))


def _selection_ranking(
    open_nodes: list[FrontierNode_t],
    frontier_state: FrontierState_t,
    requirement_sheet: RequirementSheet,
    runtime_budget_state: RuntimeBudgetState,
    selection_policy: RuntimeSelectionPolicy,
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
                selection_policy,
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
    selection_policy: RuntimeSelectionPolicy,
) -> FrontierSelectionBreakdown:
    weights = _selection_phase_weights(
        runtime_budget_state.search_phase,
        selection_policy,
    )
    operator_exploitation_score = _operator_exploitation_score(node, frontier_state)
    operator_exploration_bonus = _operator_exploration_bonus(node, frontier_state)
    coverage_opportunity_score = _coverage_opportunity_score(node, requirement_sheet)
    incremental_value_score = _incremental_value_score(node)
    fresh_node_bonus = 1.0 if node.previous_branch_evaluation is None else 0.0
    redundancy_penalty = _redundancy_penalty(node, frontier_state)
    final_selection_score = (
        weights.exploit * operator_exploitation_score
        + weights.explore * operator_exploration_bonus
        + weights.coverage * coverage_opportunity_score
        + weights.incremental * incremental_value_score
        + weights.fresh * fresh_node_bonus
        - weights.redundancy * redundancy_penalty
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


def _selection_phase_weights(
    search_phase: str,
    selection_policy: RuntimeSelectionPolicy,
) -> PhaseSelectionWeights:
    if search_phase == "explore":
        return selection_policy.explore
    if search_phase == "harvest":
        return selection_policy.harvest
    return selection_policy.balance


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


def _unmet_must_haves(
    node: FrontierNode_t,
    requirement_sheet: RequirementSheet,
) -> list[str]:
    return [
        capability
        for capability in requirement_sheet.must_have_capabilities
        if _query_pool_hit(node, capability) == 0
    ]


def _normalized_non_crossover_query(
    operator_name: OperatorName,
    context: SearchControllerContext_t,
    raw_query_terms: object,
    rewrite_fitness_weights: RewriteFitnessWeights,
) -> tuple[list[str], RewriteChoiceTrace | None]:
    query_terms = _normalized_string_list(raw_query_terms)[: context.max_query_terms]
    if not query_terms:
        raise ValueError("search_cts requires materializable non-empty query_terms")
    _validate_non_crossover_query_terms(operator_name, context, query_terms)
    if operator_name not in _REWRITE_OPERATORS or not context.rewrite_term_candidates:
        return query_terms, None
    return _ga_lite_query_rewrite(
        operator_name,
        context,
        query_terms,
        rewrite_fitness_weights,
    )


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
    return query_terms_hit(node.node_query_term_pool, capability)


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
        for capability in _unmet_must_haves(active_node, requirement_sheet)
        if _query_pool_hit(donor_node, capability) == 1
    ]


def _allowed_operator_names(
    search_phase: str,
    *,
    has_pack: bool,
    has_legal_donors: bool,
    unmet_must_haves: list[str],
) -> list[OperatorName]:
    if search_phase == "explore":
        operators: list[OperatorName] = [
            "must_have_alias",
            "vocabulary_bridge",
            "core_precision",
            "relaxed_floor",
        ]
        if has_pack:
            operators.append("pack_bridge")
        return operators

    if search_phase == "harvest":
        operators = ["core_precision"]
        if has_legal_donors:
            operators.append("crossover_compose")
        if unmet_must_haves:
            operators.extend(["must_have_alias", "vocabulary_bridge"])
        return operators

    operators = [
        "core_precision",
        "must_have_alias",
        "relaxed_floor",
        "vocabulary_bridge",
    ]
    if has_pack:
        operators.append("pack_bridge")
    if has_legal_donors:
        operators.append("crossover_compose")
    return operators


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


def _normalized_string_list(value: object) -> list[str]:
    return stable_deduplicate(
        [
            clean
            for clean in (_normalized_text(item) for item in _string_list(value))
            if clean
        ]
    )


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


_REWRITE_OPERATORS = {
    "must_have_alias",
    "vocabulary_bridge",
    "pack_bridge",
}


def _validate_non_crossover_query_terms(
    operator_name: OperatorName,
    context: SearchControllerContext_t,
    query_terms: list[str],
) -> None:
    active_terms = _normalized_string_list(
        context.active_frontier_node_summary.node_query_term_pool
    )
    active_term_set = set(active_terms)
    query_term_set = set(query_terms)
    shared_terms = active_term_set & query_term_set
    new_terms = query_term_set - active_term_set
    dropped_terms = active_term_set - query_term_set

    if operator_name == "core_precision":
        if new_terms or not shared_terms:
            raise ValueError("core_precision_requires_non_empty_active_query_subset")
        return

    if operator_name == "relaxed_floor":
        if new_terms or not shared_terms or not dropped_terms:
            raise ValueError("relaxed_floor_requires_non_empty_strict_active_query_subset")
        return

    if operator_name == "must_have_alias" and not context.operator_surface_unmet_must_haves:
        raise ValueError("must_have_alias_requires_unmet_must_have")

    if not shared_terms or not new_terms or not dropped_terms:
        raise ValueError(f"{operator_name}_requires_query_rewrite_with_shared_new_and_dropped_terms")


def _ga_lite_query_rewrite(
    operator_name: OperatorName,
    context: SearchControllerContext_t,
    seed_query_terms: list[str],
    rewrite_fitness_weights: RewriteFitnessWeights,
) -> tuple[list[str], RewriteChoiceTrace | None]:
    candidates = _rewrite_population(
        context,
        seed_query_terms,
        rewrite_fitness_weights,
    )[:6]
    legal_candidates: list[list[str]] = []
    for candidate in candidates:
        try:
            _validate_non_crossover_query_terms(operator_name, context, candidate)
        except ValueError:
            continue
        legal_candidates.append(candidate)
    if not legal_candidates:
        return seed_query_terms, None
    scored = sorted(
        [
            _rewrite_scored_candidate(
                candidate,
                context,
                rewrite_fitness_weights,
                seed_query_terms=seed_query_terms,
            )
            for candidate in legal_candidates
        ],
        key=lambda item: (
            -item[1],
            item[0],
        ),
    )
    selected_query_terms, selected_total_score, selected_breakdown = scored[0]
    runner_up_query_terms: list[str] | None = None
    runner_up_total_score: float | None = None
    if len(scored) > 1:
        runner_up_query_terms = scored[1][0]
        runner_up_total_score = scored[1][1]
    return selected_query_terms, RewriteChoiceTrace(
        seed_query_terms=seed_query_terms,
        selected_query_terms=selected_query_terms,
        candidate_count=len(scored),
        selected_total_score=selected_total_score,
        selected_breakdown=selected_breakdown,
        runner_up_query_terms=runner_up_query_terms,
        runner_up_total_score=runner_up_total_score,
    )


def _rewrite_population(
    context: SearchControllerContext_t,
    seed_query_terms: list[str],
    rewrite_fitness_weights: RewriteFitnessWeights,
) -> list[list[str]]:
    evidence_terms = [
        candidate.term
        for candidate in context.rewrite_term_candidates
        if candidate.term not in set(seed_query_terms)
    ][:3]
    if not evidence_terms:
        return [seed_query_terms]
    active_terms = _normalized_string_list(
        context.active_frontier_node_summary.node_query_term_pool
    )
    anchor_terms = [term for term in seed_query_terms if term in set(active_terms)] or seed_query_terms[:1]
    replaceable_terms = [
        term for term in seed_query_terms if term not in set(anchor_terms)
    ] or seed_query_terms[-1:]
    population = [seed_query_terms]
    for evidence_term in evidence_terms:
        for dropped_term in replaceable_terms[:2]:
            candidate = stable_deduplicate(
                [term for term in seed_query_terms if term != dropped_term]
                + [evidence_term]
            )[: context.max_query_terms]
            if candidate:
                population.append(candidate)
    top_seed_candidates = sorted(
        _deduplicate_term_lists(population),
        key=lambda candidate: (
            -_rewrite_fitness(
                candidate,
                context,
                rewrite_fitness_weights,
                seed_query_terms=seed_query_terms,
            ),
            candidate,
        ),
    )[:2]
    for candidate in top_seed_candidates:
        for evidence_term in evidence_terms:
            if evidence_term in set(candidate):
                continue
            dropped_term = next(
                (
                    term
                    for term in reversed(candidate)
                    if term not in set(anchor_terms)
                ),
                candidate[-1],
            )
            population.append(
                stable_deduplicate(
                    [term for term in candidate if term != dropped_term]
                    + [evidence_term]
                )[: context.max_query_terms]
            )
    return _deduplicate_term_lists(population)


def _rewrite_fitness(
    query_terms: list[str],
    context: SearchControllerContext_t,
    rewrite_fitness_weights: RewriteFitnessWeights,
    *,
    seed_query_terms: list[str],
) -> float:
    return _rewrite_total_score(
        _rewrite_score_breakdown(
            query_terms,
            context,
            seed_query_terms=seed_query_terms,
        ),
        rewrite_fitness_weights,
    )


def _rewrite_scored_candidate(
    query_terms: list[str],
    context: SearchControllerContext_t,
    rewrite_fitness_weights: RewriteFitnessWeights,
    *,
    seed_query_terms: list[str],
) -> tuple[list[str], float, RewriteChoiceScoreBreakdown]:
    breakdown = _rewrite_score_breakdown(
        query_terms,
        context,
        seed_query_terms=seed_query_terms,
    )
    return (
        query_terms,
        _rewrite_total_score(breakdown, rewrite_fitness_weights),
        breakdown,
    )


def _rewrite_total_score(
    breakdown: RewriteChoiceScoreBreakdown,
    rewrite_fitness_weights: RewriteFitnessWeights,
) -> float:
    return (
        rewrite_fitness_weights.must_have_repair * breakdown.must_have_repair_score
        + rewrite_fitness_weights.anchor_preservation * breakdown.anchor_preservation_score
        + rewrite_fitness_weights.rewrite_coherence * breakdown.rewrite_coherence_score
        + rewrite_fitness_weights.provenance_coherence * breakdown.provenance_coherence_score
        - rewrite_fitness_weights.query_length_penalty * breakdown.query_length_penalty
        - rewrite_fitness_weights.redundancy_penalty * breakdown.redundancy_penalty
    )


def _rewrite_score_breakdown(
    query_terms: list[str],
    context: SearchControllerContext_t,
    *,
    seed_query_terms: list[str],
) -> RewriteChoiceScoreBreakdown:
    active_terms = _normalized_string_list(
        context.active_frontier_node_summary.node_query_term_pool
    )
    seed_terms = stable_deduplicate(seed_query_terms)
    active_term_set = set(active_terms)
    query_term_set = set(query_terms)
    evidence_lookup = {
        candidate.term: candidate
        for candidate in context.rewrite_term_candidates
    }
    new_terms = [term for term in query_terms if term not in active_term_set]
    seed_anchor_terms = [
        term for term in seed_terms if term in active_term_set
    ] or seed_terms[:1]
    return RewriteChoiceScoreBreakdown(
        must_have_repair_score=_must_have_repair_score(query_terms, context),
        anchor_preservation_score=min(
            1.0,
            len(set(seed_anchor_terms) & query_term_set) / max(1, len(seed_anchor_terms)),
        ),
        rewrite_coherence_score=_rewrite_coherence_score(new_terms, evidence_lookup),
        provenance_coherence_score=_provenance_coherence_score(new_terms, evidence_lookup),
        query_length_penalty=len(query_terms) / max(1, context.max_query_terms),
        redundancy_penalty=len(active_term_set & query_term_set)
        / max(1, len(query_term_set)),
    )


def _must_have_repair_score(
    query_terms: list[str],
    context: SearchControllerContext_t,
) -> float:
    unmet = context.operator_surface_unmet_must_haves
    if not unmet:
        return 0.0
    return sum(query_terms_hit(query_terms, capability) for capability in unmet) / len(unmet)


def _rewrite_coherence_score(
    new_terms: list[str],
    evidence_lookup: dict[str, RewriteTermCandidate],
) -> float:
    if not new_terms:
        return 0.0
    candidates = [evidence_lookup.get(term) for term in new_terms]
    evidence_strength_score = sum(
        _rewrite_evidence_strength(candidate) for candidate in candidates
    ) / len(new_terms)
    term_alignment_score = sum(
        _rewrite_term_alignment_score(candidate) for candidate in candidates
    ) / len(new_terms)
    multi_term_agreement_score = _source_overlap_score(candidates)
    return (
        0.45 * evidence_strength_score
        + 0.35 * term_alignment_score
        + 0.20 * multi_term_agreement_score
    )


def _provenance_coherence_score(
    new_terms: list[str],
    evidence_lookup: dict[str, RewriteTermCandidate],
) -> float:
    if not new_terms:
        return 0.0
    candidates = [evidence_lookup.get(term) for term in new_terms]
    field_strength_score = sum(
        _rewrite_field_strength(candidate) for candidate in candidates
    ) / len(new_terms)
    support_strength_score = sum(
        _rewrite_support_strength(candidate) for candidate in candidates
    ) / len(new_terms)
    source_overlap_score = _source_overlap_score(candidates)
    return (
        0.40 * field_strength_score
        + 0.35 * support_strength_score
        + 0.25 * source_overlap_score
    )


def _rewrite_evidence_strength(candidate: RewriteTermCandidate | None) -> float:
    if candidate is None:
        return 0.0
    return min(1.0, candidate.accepted_term_score / 6.0)


def _rewrite_term_alignment_score(candidate: RewriteTermCandidate | None) -> float:
    if candidate is None:
        return 0.4
    breakdown = candidate.score_breakdown
    if breakdown.must_have_bonus > 0:
        return 1.0
    if breakdown.anchor_bonus > 0:
        return 0.85
    if breakdown.pack_bonus > 0:
        return 0.75
    if _rewrite_best_source_field(candidate) in {"title", "project_names"}:
        return 0.65
    return 0.4


def _rewrite_field_strength(candidate: RewriteTermCandidate | None) -> float:
    if candidate is None:
        return 0.0
    return max((_rewrite_field_weight(field) for field in candidate.source_fields), default=0.0)


def _rewrite_support_strength(candidate: RewriteTermCandidate | None) -> float:
    if candidate is None:
        return 0.0
    return min(1.0, candidate.support_count / 3.0)


def _rewrite_best_source_field(candidate: RewriteTermCandidate) -> str:
    return max(candidate.source_fields, key=_rewrite_field_weight, default="")


def _rewrite_field_weight(field_name: str) -> float:
    return {
        "title": 1.0,
        "project_names": 0.9,
        "work_summaries": 0.8,
        "work_experience_summaries": 0.7,
        "search_text": 0.4,
    }.get(field_name, 0.0)


def _source_overlap_score(candidates: list[RewriteTermCandidate | None]) -> float:
    if len(candidates) < 2:
        return 1.0
    overlaps: list[float] = []
    for index, left in enumerate(candidates[:-1]):
        for right in candidates[index + 1 :]:
            overlaps.append(_candidate_source_jaccard(left, right))
    if not overlaps:
        return 1.0
    return sum(overlaps) / len(overlaps)


def _candidate_source_jaccard(
    left: RewriteTermCandidate | None,
    right: RewriteTermCandidate | None,
) -> float:
    left_ids = set(left.source_candidate_ids) if left is not None else set()
    right_ids = set(right.source_candidate_ids) if right is not None else set()
    if not left_ids and not right_ids:
        return 0.0
    return len(left_ids & right_ids) / len(left_ids | right_ids)


def _deduplicate_term_lists(values: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    output: list[list[str]] = []
    for value in values:
        key = tuple(value)
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


__all__ = [
    "carry_forward_frontier_state",
    "generate_search_controller_decision",
    "generate_search_controller_decision_with_trace",
    "select_active_frontier_node",
]
