from __future__ import annotations

import json
import math
from hashlib import sha1
from typing import Protocol

from seektalent.clients.cts_client import CTSClientProtocol
from seektalent.models import (
    CrossoverGuardThresholds,
    FitGateConstraints,
    FrontierState_t,
    RequirementSheet,
    RuntimeSearchBudget,
    RuntimeTermBudgetPolicy,
    ScoredCandidate_t,
    ScoringCandidate_t,
    ScoringPolicy,
    SearchControllerDecision_t,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchScoringResult_t,
    TopThreeStatistics,
    stable_deduplicate,
)
from seektalent.retrieval import build_search_execution_result
from seektalent_rerank.models import RerankDocument, RerankRequest, RerankResponse


DEGREE_RANKS = {
    "大专": 1,
    "大专及以上": 1,
    "专科": 1,
    "本科": 2,
    "本科及以上": 2,
    "学士": 2,
    "硕士": 3,
    "硕士及以上": 3,
    "研究生": 3,
    "博士": 4,
    "博士及以上": 4,
}


class AsyncRerankRequest(Protocol):
    async def __call__(self, request: RerankRequest) -> RerankResponse: ...


def materialize_search_execution_plan(
    frontier_state: FrontierState_t,
    requirement_sheet: RequirementSheet,
    decision: SearchControllerDecision_t,
    term_budget_policy: RuntimeTermBudgetPolicy,
    search_budget: RuntimeSearchBudget,
    crossover_thresholds: CrossoverGuardThresholds,
) -> SearchExecutionPlan_t:
    if decision.action != "search_cts":
        raise ValueError("materialize_search_execution_plan only supports action=search_cts")

    parent_node = frontier_state.frontier_nodes.get(decision.target_frontier_node_id)
    if parent_node is None:
        raise ValueError(f"unknown_target_frontier_node_id: {decision.target_frontier_node_id}")

    if frontier_state.remaining_budget >= 4:
        _, max_terms = term_budget_policy.high_budget_range
    elif frontier_state.remaining_budget >= 2:
        _, max_terms = term_budget_policy.medium_budget_range
    else:
        _, max_terms = term_budget_policy.low_budget_range

    donor_frontier_node_id: str | None = None
    source_card_ids = list(parent_node.source_card_ids)
    donor_negative_terms: list[str] = []
    if decision.selected_operator_name == "crossover_compose":
        donor_frontier_node_id = _required_text(decision.operator_args.get("donor_frontier_node_id"), "donor_frontier_node_id")
        donor_node = frontier_state.frontier_nodes.get(donor_frontier_node_id)
        if donor_node is None:
            raise ValueError(f"unknown_donor_frontier_node_id: {donor_frontier_node_id}")
        shared_anchor_terms = [
            term
            for term in stable_deduplicate(_required_string_list(decision.operator_args.get("shared_anchor_terms"), "shared_anchor_terms"))
            if term in set(parent_node.node_query_term_pool) & set(donor_node.node_query_term_pool)
        ]
        donor_terms = [
            term
            for term in stable_deduplicate(_required_string_list(decision.operator_args.get("donor_terms_used"), "donor_terms_used"))
            if term in set(donor_node.node_query_term_pool) - set(parent_node.node_query_term_pool)
        ]
        if len(shared_anchor_terms) < crossover_thresholds.min_shared_anchor_terms:
            raise ValueError("crossover_requires_shared_anchor")
        query_terms = stable_deduplicate(shared_anchor_terms + donor_terms)[:max_terms]
        source_card_ids = stable_deduplicate(parent_node.source_card_ids + donor_node.source_card_ids)
        donor_negative_terms = donor_node.negative_terms
    else:
        query_terms = stable_deduplicate(
            parent_node.node_query_term_pool
            + _required_string_list(decision.operator_args.get("additional_terms"), "additional_terms")
        )[:max_terms]

    runtime_only_constraints = {
        "must_have_keywords": stable_deduplicate(requirement_sheet.must_have_capabilities + query_terms),
        "negative_keywords": stable_deduplicate(parent_node.negative_terms + donor_negative_terms),
    }
    target_new_candidate_count = decision.operator_args.get("target_new_candidate_count")
    if target_new_candidate_count is None:
        target_new_candidate_count = search_budget.default_target_new_candidate_count
    elif not isinstance(target_new_candidate_count, int):
        raise ValueError("target_new_candidate_count must be an integer.")
    if target_new_candidate_count <= 0:
        raise ValueError("target_new_candidate_count must be positive.")
    target_new_candidate_count = min(target_new_candidate_count, search_budget.max_target_new_candidate_count)

    semantic_hash = sha1(
        json.dumps(
            {
                "selected_operator_name": decision.selected_operator_name,
                "query_terms": query_terms,
                "projected_filters": requirement_sheet.hard_constraints.model_dump(mode="python"),
                "runtime_only_constraints": runtime_only_constraints,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return SearchExecutionPlan_t(
        query_terms=query_terms,
        projected_filters=requirement_sheet.hard_constraints,
        runtime_only_constraints=runtime_only_constraints,
        target_new_candidate_count=target_new_candidate_count,
        semantic_hash=semantic_hash,
        source_card_ids=source_card_ids,
        child_frontier_node_stub={
            "frontier_node_id": f"child_{parent_node.frontier_node_id}_{semantic_hash[:8]}",
            "parent_frontier_node_id": parent_node.frontier_node_id,
            "donor_frontier_node_id": donor_frontier_node_id,
            "selected_operator_name": decision.selected_operator_name,
        },
        derived_position=_non_empty_text(requirement_sheet.role_title),
        derived_work_content=_derived_work_content(requirement_sheet.must_have_capabilities),
    )


async def execute_search_plan(
    plan: SearchExecutionPlan_t,
    cts_client: CTSClientProtocol,
) -> SearchExecutionResult_t:
    if plan.target_new_candidate_count <= 0:
        raise ValueError("target_new_candidate_count must be positive.")
    cts_result = await cts_client.search(plan)
    if cts_result.latency_ms is None:
        raise ValueError("cts_result.latency_ms must not be null.")
    raw_count = len(cts_result.candidates)
    pages_fetched = 0 if raw_count == 0 else math.ceil(raw_count / plan.target_new_candidate_count)
    return build_search_execution_result(
        cts_result.candidates,
        runtime_negative_keywords=plan.runtime_only_constraints.negative_keywords,
        runtime_must_have_keywords=plan.runtime_only_constraints.must_have_keywords,
        pages_fetched=pages_fetched,
        target_new_candidate_count=plan.target_new_candidate_count,
        latency_ms=cts_result.latency_ms,
    )


async def score_search_results(
    execution_result: SearchExecutionResult_t,
    scoring_policy: ScoringPolicy,
    rerank_request: AsyncRerankRequest,
) -> SearchScoringResult_t:
    candidates = execution_result.scoring_candidates
    if not candidates:
        return SearchScoringResult_t(
            scored_candidates=[],
            node_shortlist_candidate_ids=[],
            explanation_candidate_ids=[],
            top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=0.0),
        )

    calibration = scoring_policy.reranker_calibration_snapshot
    if calibration.normalization != "sigmoid":
        raise ValueError(f"unsupported_reranker_calibration_normalization: {calibration.normalization}")

    request = RerankRequest(
        instruction=scoring_policy.rerank_instruction,
        query=scoring_policy.rerank_query_text,
        documents=[
            RerankDocument(id=candidate.candidate_id, text=candidate.scoring_text)
            for candidate in candidates
        ],
    )
    rerank_response = await rerank_request(request)
    rerank_scores = _rerank_scores_by_candidate_id(rerank_response, candidates)

    scored_candidates = [
        _score_candidate(candidate, rerank_scores[candidate.candidate_id], scoring_policy)
        for candidate in candidates
    ]
    ranked_candidates = sorted(scored_candidates, key=lambda candidate: candidate.fusion_score, reverse=True)
    node_shortlist_candidate_ids = [
        candidate.candidate_id
        for candidate in ranked_candidates
        if candidate.fit == 1
    ]
    explanation_candidate_ids = [
        candidate.candidate_id
        for candidate in ranked_candidates[: scoring_policy.top_n_for_explanation]
    ]
    top_three = ranked_candidates[:3]
    average_top_three = 0.0 if not top_three else sum(candidate.fusion_score for candidate in top_three) / len(top_three)
    return SearchScoringResult_t(
        scored_candidates=ranked_candidates,
        node_shortlist_candidate_ids=node_shortlist_candidate_ids,
        explanation_candidate_ids=explanation_candidate_ids,
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=average_top_three),
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    clean = " ".join(value.split()).strip()
    if not clean:
        raise ValueError(f"{field_name} must not be empty.")
    return clean


def _required_string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings.")
    return value


def _non_empty_text(value: str) -> str | None:
    clean = " ".join(value.split()).strip()
    return clean or None


def _derived_work_content(must_have_capabilities: list[str]) -> str | None:
    terms = stable_deduplicate(must_have_capabilities)[:4]
    if not terms:
        return None
    return " | ".join(terms)


def _rerank_scores_by_candidate_id(
    rerank_response: RerankResponse,
    candidates: list[ScoringCandidate_t],
) -> dict[str, float]:
    expected_ids = [candidate.candidate_id for candidate in candidates]
    seen_scores: dict[str, float] = {}
    for result in rerank_response.results:
        if result.id in seen_scores:
            raise ValueError(f"duplicate_rerank_result_id: {result.id}")
        seen_scores[result.id] = result.score
    if set(seen_scores) != set(expected_ids):
        missing = [candidate_id for candidate_id in expected_ids if candidate_id not in seen_scores]
        extra = [candidate_id for candidate_id in seen_scores if candidate_id not in set(expected_ids)]
        raise ValueError(f"rerank_results_must_cover_all_candidates: missing={missing}, extra={extra}")
    return seen_scores


def _score_candidate(
    candidate: ScoringCandidate_t,
    rerank_raw: float,
    scoring_policy: ScoringPolicy,
) -> ScoredCandidate_t:
    rerank_normalized = _calibrate_rerank_score(rerank_raw, scoring_policy)
    must_raw = round(100 * _match_fraction(candidate, scoring_policy.must_have_capabilities_snapshot))
    preferred_raw = round(100 * _match_fraction(candidate, scoring_policy.preferred_capabilities_snapshot))
    risk_score, risk_raw, risk_flags = _risk_score(candidate, scoring_policy)
    fit = _fit_score(candidate, scoring_policy.fit_gate_constraints)
    fusion_score = (
        scoring_policy.fusion_weights.rerank * rerank_normalized
        + scoring_policy.fusion_weights.must_have * (must_raw / 100)
        + scoring_policy.fusion_weights.preferred * (preferred_raw / 100)
        - scoring_policy.fusion_weights.risk_penalty * risk_score
    )
    return ScoredCandidate_t(
        candidate_id=candidate.candidate_id,
        fit=fit,
        rerank_raw=rerank_raw,
        rerank_normalized=rerank_normalized,
        must_have_match_score_raw=must_raw,
        must_have_match_score=must_raw / 100,
        preferred_match_score_raw=preferred_raw,
        preferred_match_score=preferred_raw / 100,
        risk_score_raw=risk_raw,
        risk_score=risk_score,
        risk_flags=risk_flags,
        fusion_score=fusion_score,
    )


def _calibrate_rerank_score(rerank_raw: float, scoring_policy: ScoringPolicy) -> float:
    calibration = scoring_policy.reranker_calibration_snapshot
    clipped = min(
        calibration.clip_max,
        max(calibration.clip_min, rerank_raw + calibration.offset),
    )
    return 1.0 / (1.0 + math.exp(-(clipped / calibration.temperature)))


def _match_fraction(candidate: ScoringCandidate_t, allowlist: list[str]) -> float:
    normalized_allowlist = stable_deduplicate(allowlist)
    if not normalized_allowlist:
        return 0.0
    matched = sum(1 for term in normalized_allowlist if _capability_hit(candidate, term))
    return matched / max(1, len(normalized_allowlist))


def _capability_hit(candidate: ScoringCandidate_t, term: str) -> int:
    normalized_term = _normalized_text(term)
    if normalized_term in _normalized_text(candidate.scoring_text):
        return 1
    return _allowlist_match(candidate.capability_signals, [term])


def _allowlist_match(text_set: list[str], allowlist: list[str]) -> int:
    normalized_texts = [_normalized_text(text) for text in text_set if _normalized_text(text)]
    normalized_allowlist = [_normalized_text(term) for term in allowlist if _normalized_text(term)]
    for text in normalized_texts:
        for allow_term in normalized_allowlist:
            if allow_term in text or text in allow_term:
                return 1
    return 0


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).strip().casefold()


def _risk_score(
    candidate: ScoringCandidate_t,
    scoring_policy: ScoringPolicy,
) -> tuple[float, int, list[str]]:
    profile = candidate.career_stability_profile
    confidence_floor = scoring_policy.penalty_weights.job_hop_confidence_floor
    if profile.confidence_score < confidence_floor:
        return 0.0, 0, []
    penalty = min(
        1.0,
        min(
            1.0,
            profile.short_tenure_count / 3 + max(0, 18 - profile.median_tenure_months) / 18,
        )
        * scoring_policy.penalty_weights.job_hop,
    )
    risk_raw = round(100 * penalty)
    risk_flags = ["frequent_job_changes"] if risk_raw > 0 else []
    return risk_raw / 100, risk_raw, risk_flags


def _fit_score(candidate: ScoringCandidate_t, fit_gates: FitGateConstraints) -> int:
    checks = [
        _allowlist_match(candidate.location_signals, fit_gates.locations) if fit_gates.locations else 1,
        1 if fit_gates.min_years is None or candidate.years_of_experience is None or candidate.years_of_experience >= fit_gates.min_years else 0,
        1 if fit_gates.max_years is None or candidate.years_of_experience is None or candidate.years_of_experience <= fit_gates.max_years else 0,
        1 if fit_gates.min_age is None or candidate.age is None or candidate.age >= fit_gates.min_age else 0,
        1 if fit_gates.max_age is None or candidate.age is None or candidate.age <= fit_gates.max_age else 0,
        1
        if fit_gates.gender_requirement is None or candidate.gender is None
        else int(_normalized_text(candidate.gender) == _normalized_text(fit_gates.gender_requirement)),
        _allowlist_match(candidate.work_experience_summaries, fit_gates.company_names) if fit_gates.company_names else 1,
        _allowlist_match(candidate.education_summaries, fit_gates.school_names) if fit_gates.school_names else 1,
        _degree_fit(candidate.education_summaries, fit_gates.degree_requirement),
    ]
    return 1 if all(checks) else 0


def _degree_fit(education_summaries: list[str], degree_requirement: str | None) -> int:
    if degree_requirement is None:
        return 1
    required_rank = DEGREE_RANKS.get(degree_requirement)
    if required_rank is None:
        return 1
    observed_ranks = [
        rank
        for summary in education_summaries
        for degree, rank in DEGREE_RANKS.items()
        if degree in summary
    ]
    if not observed_ranks:
        return 1
    return 1 if max(observed_ranks) >= required_rank else 0


__all__ = [
    "execute_search_plan",
    "materialize_search_execution_plan",
    "score_search_results",
]
