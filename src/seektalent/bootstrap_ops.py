from __future__ import annotations

import json
import math
from hashlib import sha1
from typing import Protocol, Sequence

from seektalent.models import (
    BootstrapKeywordDraft,
    BootstrapOutput,
    BootstrapRoutingResult,
    BusinessPolicyPack,
    DomainKnowledgePack,
    FitGateConstraints,
    FrontierNode_t,
    FrontierSeedSpecification,
    FrontierState_t,
    FusionWeights,
    OperatorStatistics,
    PenaltyWeights,
    RequirementSheet,
    RerankerCalibration,
    RuntimeSearchBudget,
    ScoringPolicy,
    stable_deduplicate,
)
from seektalent.rerank_text import build_rerank_query_text
from seektalent_rerank.models import RerankDocument, RerankRequest, RerankResponse

DEGREE_RANK = {
    "大专及以上": 1,
    "本科及以上": 2,
    "硕士及以上": 3,
    "博士及以上": 4,
}
ROUTING_CONFIDENCE_FLOOR = 0.55
ROUTING_MULTI_PACK_GAP = 0.08
MAX_ROUTED_PACKS = 2
ROUTING_INSTRUCTION = (
    "Given a hiring requirement, judge whether this domain knowledge pack is relevant "
    "for expanding round-0 search terms."
)
FINAL_SEED_COUNTS = {
    "generic_fallback": 4,
    "explicit_pack": 5,
    "inferred_single_pack": 5,
    "inferred_multi_pack": 5,
}
class AsyncRerankRequest(Protocol):
    async def __call__(self, request: RerankRequest) -> RerankResponse: ...


async def route_domain_knowledge_pack(
    requirement_sheet: RequirementSheet,
    business_policy_pack: BusinessPolicyPack,
    knowledge_packs: Sequence[DomainKnowledgePack],
    reranker_calibration: RerankerCalibration,
    rerank_request: AsyncRerankRequest,
) -> BootstrapRoutingResult:
    packs_by_id = {pack.knowledge_pack_id: pack for pack in knowledge_packs}
    override = _normalize_text(business_policy_pack.knowledge_pack_id_override)
    if override:
        selected_pack = packs_by_id.get(override)
        if selected_pack is None:
            raise ValueError(f"unknown_knowledge_pack_id_override: {override}")
        return BootstrapRoutingResult(
            routing_mode="explicit_pack",
            selected_knowledge_pack_ids=[selected_pack.knowledge_pack_id],
            routing_confidence=1.0,
            fallback_reason=None,
            pack_scores={selected_pack.knowledge_pack_id: 1.0},
        )

    request = RerankRequest(
        instruction=ROUTING_INSTRUCTION,
        query=_routing_query_text(requirement_sheet),
        documents=[
            RerankDocument(id=pack.knowledge_pack_id, text=pack.routing_text)
            for pack in knowledge_packs
        ],
    )
    response = await rerank_request(request)
    raw_scores = _rerank_scores_by_id(response, [pack.knowledge_pack_id for pack in knowledge_packs])
    pack_scores = {
        pack.knowledge_pack_id: _calibrate_rerank_score(
            raw_scores[pack.knowledge_pack_id],
            reranker_calibration,
        )
        for pack in knowledge_packs
    }
    ranked_packs = sorted(
        knowledge_packs,
        key=lambda pack: pack_scores[pack.knowledge_pack_id],
        reverse=True,
    )
    top1 = ranked_packs[0]
    top1_score = pack_scores[top1.knowledge_pack_id]
    top2_score = pack_scores[ranked_packs[1].knowledge_pack_id] if len(ranked_packs) > 1 else 0.0
    if top1_score < ROUTING_CONFIDENCE_FLOOR:
        return BootstrapRoutingResult(
            routing_mode="generic_fallback",
            selected_knowledge_pack_ids=[],
            routing_confidence=top1_score,
            fallback_reason="top1_confidence_below_floor",
            pack_scores=pack_scores,
        )
    selected_pack_ids = [top1.knowledge_pack_id]
    routing_mode = "inferred_single_pack"
    if (
        len(ranked_packs) > 1
        and top2_score >= ROUTING_CONFIDENCE_FLOOR
        and top1_score - top2_score <= ROUTING_MULTI_PACK_GAP
    ):
        selected_pack_ids.append(ranked_packs[1].knowledge_pack_id)
        routing_mode = "inferred_multi_pack"
    return BootstrapRoutingResult(
        routing_mode=routing_mode,
        selected_knowledge_pack_ids=selected_pack_ids[:MAX_ROUTED_PACKS],
        routing_confidence=top1_score,
        fallback_reason=None,
        pack_scores=pack_scores,
    )


def freeze_scoring_policy(
    requirement_sheet: RequirementSheet,
    business_policy_pack: BusinessPolicyPack,
    reranker_calibration: RerankerCalibration,
) -> ScoringPolicy:
    truth_gate = requirement_sheet.hard_constraints
    override_gate = business_policy_pack.fit_gate_overrides
    fit_gate = FitGateConstraints(
        locations=_merged_allowlist(truth_gate.locations, override_gate.locations),
        min_years=_merged_min(truth_gate.min_years, override_gate.min_years),
        max_years=_merged_max(truth_gate.max_years, override_gate.max_years),
        company_names=_merged_allowlist(truth_gate.company_names, override_gate.company_names),
        school_names=_merged_allowlist(truth_gate.school_names, override_gate.school_names),
        degree_requirement=_merged_degree_requirement(
            truth_gate.degree_requirement,
            override_gate.degree_requirement,
        ),
        gender_requirement=_merged_gender_requirement(
            truth_gate.gender_requirement,
            override_gate.gender_requirement,
        ),
        min_age=_merged_min(truth_gate.min_age, override_gate.min_age),
        max_age=_merged_max(truth_gate.max_age, override_gate.max_age),
    )
    weights = business_policy_pack.fusion_weight_preferences
    raw_rerank = _normalized_weight(weights.rerank, 0.55)
    raw_must_have = _normalized_weight(weights.must_have, 0.25)
    raw_preferred = _normalized_weight(weights.preferred, 0.10)
    raw_risk = _normalized_weight(weights.risk_penalty, 0.10)
    raw_sum = raw_rerank + raw_must_have + raw_preferred + raw_risk
    if raw_sum <= 0:
        raise ValueError("fusion_weights_sum_must_be_positive")
    fusion_weights = FusionWeights(
        rerank=raw_rerank / raw_sum,
        must_have=raw_must_have / raw_sum,
        preferred=raw_preferred / raw_sum,
        risk_penalty=raw_risk / raw_sum,
    )
    stability_policy = business_policy_pack.stability_policy
    top_n = business_policy_pack.explanation_preferences.top_n_for_explanation or 5
    rerank_query_text = build_rerank_query_text(requirement_sheet)
    return ScoringPolicy(
        fit_gate_constraints=fit_gate,
        must_have_capabilities_snapshot=list(requirement_sheet.must_have_capabilities),
        preferred_capabilities_snapshot=list(requirement_sheet.preferred_capabilities),
        fusion_weights=fusion_weights,
        penalty_weights=PenaltyWeights(
            job_hop=(
                1.0 if stability_policy.penalty_weight is None else stability_policy.penalty_weight
            ),
            job_hop_confidence_floor=(
                0.6 if stability_policy.confidence_floor is None else stability_policy.confidence_floor
            ),
        ),
        top_n_for_explanation=top_n,
        rerank_instruction=(
            "Given a hiring requirement, judge how well the candidate resume matches the role. "
            "Prioritize must-have capabilities, use preferred capabilities as secondary evidence, "
            "and do not over-penalize weak soft-risk signals."
        ),
        rerank_query_text=rerank_query_text,
        reranker_calibration_snapshot=reranker_calibration,
        ranking_audit_notes=_normalize_text(
            f"{requirement_sheet.scoring_rationale} must-have 优先于 preferred；低置信度稳定性风险不处罚。"
        ),
    )


def generate_bootstrap_output(
    requirement_sheet: RequirementSheet,
    routing_result: BootstrapRoutingResult,
    selected_knowledge_packs: Sequence[DomainKnowledgePack],
    keyword_draft: BootstrapKeywordDraft,
    max_seed_terms: int,
) -> BootstrapOutput:
    negative_terms = stable_deduplicate(
        list(requirement_sheet.exclusion_signals)
        + [
            keyword
            for pack in selected_knowledge_packs
            for keyword in pack.exclude_keywords
        ]
        + list(keyword_draft.negative_keywords)
    )
    target_location = (
        requirement_sheet.hard_constraints.locations[0]
        if len(requirement_sheet.hard_constraints.locations) == 1
        else None
    )
    candidate_seed_specs = [
        seed_spec
        for seed_spec in (
            _materialize_seed_spec(
                routing_result,
                intent_index=index,
                negative_terms=negative_terms,
                target_location=target_location,
                selected_knowledge_pack_ids=routing_result.selected_knowledge_pack_ids,
                candidate_seed=candidate_seed,
                max_seed_terms=max_seed_terms,
            )
            for index, candidate_seed in enumerate(keyword_draft.candidate_seeds)
        )
        if seed_spec is not None
    ]
    seed_specs = _select_seed_specs(candidate_seed_specs, routing_mode=routing_result.routing_mode)
    return BootstrapOutput(
        frontier_seed_specifications=[
            seed_spec
            for seed_spec in _unique_seed_specs(seed_specs)
            if len(seed_spec.seed_terms) >= 1
        ]
    )


def initialize_frontier_state(
    bootstrap_output: BootstrapOutput,
    runtime_search_budget: RuntimeSearchBudget,
    operator_catalog: Sequence[str],
) -> FrontierState_t:
    frontier_nodes = {
        frontier_node.frontier_node_id: frontier_node
        for frontier_node in (
            FrontierNode_t(
                frontier_node_id=_seed_id(seed_spec),
                parent_frontier_node_id=None,
                donor_frontier_node_id=None,
                selected_operator_name=seed_spec.operator_name,
                node_query_term_pool=stable_deduplicate(seed_spec.seed_terms),
                knowledge_pack_ids=list(seed_spec.knowledge_pack_ids),
                seed_rationale=seed_spec.seed_rationale,
                negative_terms=list(seed_spec.negative_terms),
                parent_shortlist_candidate_ids=[],
                node_shortlist_candidate_ids=[],
                node_shortlist_score_snapshot={},
                previous_branch_evaluation=None,
                reward_breakdown=None,
                status="open",
            )
            for seed_spec in bootstrap_output.frontier_seed_specifications
        )
    }
    return FrontierState_t(
        frontier_nodes=frontier_nodes,
        open_frontier_node_ids=list(frontier_nodes),
        closed_frontier_node_ids=[],
        run_term_catalog=stable_deduplicate(
            [
                term
                for frontier_node in frontier_nodes.values()
                for term in frontier_node.node_query_term_pool
            ]
        ),
        run_shortlist_candidate_ids=[],
        semantic_hashes_seen=[],
        operator_statistics={
            operator_name: OperatorStatistics(average_reward=0.0, times_selected=0)
            for operator_name in operator_catalog
        },
        remaining_budget=runtime_search_budget.initial_round_budget,
    )


def _routing_query_text(requirement_sheet: RequirementSheet) -> str:
    parts = [f"Hiring for {requirement_sheet.role_title}"]
    role_summary = _normalize_text(requirement_sheet.role_summary)
    if role_summary and role_summary.casefold() != _normalize_text(requirement_sheet.role_title).casefold():
        parts.append(f"Role focus: {role_summary}")
    must_have = stable_deduplicate(list(requirement_sheet.must_have_capabilities))
    if must_have:
        parts.append(f"Must have {', '.join(must_have)}")
    preferred = stable_deduplicate(list(requirement_sheet.preferred_capabilities))
    if preferred:
        parts.append(f"Preferred {', '.join(preferred)}")
    return _join_sentences(parts)


def _rerank_scores_by_id(
    response: RerankResponse,
    expected_ids: list[str],
) -> dict[str, float]:
    seen_scores: dict[str, float] = {}
    for result in response.results:
        if result.id in seen_scores:
            raise ValueError(f"duplicate_rerank_result_id: {result.id}")
        seen_scores[result.id] = result.score
    if set(seen_scores) != set(expected_ids):
        missing = [item_id for item_id in expected_ids if item_id not in seen_scores]
        extra = [item_id for item_id in seen_scores if item_id not in set(expected_ids)]
        raise ValueError(f"rerank_results_must_cover_all_packs: missing={missing}, extra={extra}")
    return seen_scores


def _calibrate_rerank_score(rerank_raw: float, calibration: RerankerCalibration) -> float:
    if calibration.normalization != "sigmoid":
        raise ValueError(f"unsupported_reranker_calibration_normalization: {calibration.normalization}")
    clipped = min(
        calibration.clip_max,
        max(calibration.clip_min, rerank_raw + calibration.offset),
    )
    return 1.0 / (1.0 + math.exp(-(clipped / calibration.temperature)))


def _bounded_terms(terms: Sequence[str], max_seed_terms: int) -> list[str]:
    return stable_deduplicate(list(terms))[:max_seed_terms]


def _materialize_seed_spec(
    routing_result: BootstrapRoutingResult,
    *,
    intent_index: int,
    negative_terms: list[str],
    target_location: str | None,
    selected_knowledge_pack_ids: list[str],
    candidate_seed,
    max_seed_terms: int,
) -> FrontierSeedSpecification | None:
    seed_terms = _bounded_terms(list(candidate_seed.keywords), max_seed_terms)
    if not seed_terms:
        return None
    knowledge_pack_ids = _materialized_pack_ids(
        routing_result,
        selected_knowledge_pack_ids=selected_knowledge_pack_ids,
        source_knowledge_pack_ids=candidate_seed.source_knowledge_pack_ids,
    )
    return FrontierSeedSpecification(
        operator_name=candidate_seed.intent_type,
        seed_terms=seed_terms,
        seed_rationale=f"{intent_index:02d}:{candidate_seed.intent_type}",
        knowledge_pack_ids=knowledge_pack_ids,
        negative_terms=negative_terms,
        target_location=target_location,
    )


def _materialized_pack_ids(
    routing_result: BootstrapRoutingResult,
    *,
    selected_knowledge_pack_ids: list[str],
    source_knowledge_pack_ids: list[str],
) -> list[str]:
    if routing_result.routing_mode == "generic_fallback":
        return []
    source_ids = stable_deduplicate(list(source_knowledge_pack_ids))
    return source_ids or stable_deduplicate(list(selected_knowledge_pack_ids))


def _select_seed_specs(
    candidate_seed_specs: list[FrontierSeedSpecification],
    *,
    routing_mode: str,
) -> list[FrontierSeedSpecification]:
    target_count = FINAL_SEED_COUNTS[routing_mode]
    selected: list[FrontierSeedSpecification] = []
    forced_rationales = ["core_precision", "relaxed_floor"]
    if routing_mode == "generic_fallback":
        forced_rationales.append("generic_expansion")
    else:
        forced_rationales.append("pack_expansion")
    if routing_mode == "inferred_multi_pack":
        forced_rationales.append("cross_pack_bridge")

    for intent_name in forced_rationales:
        seed_spec = next(
            (
                candidate
                for candidate in candidate_seed_specs
                if candidate.seed_rationale.endswith(intent_name)
            ),
            None,
        )
        if seed_spec is None:
            raise ValueError(f"missing_required_seed_intent: {intent_name}")
        if not _has_seed_spec(selected, seed_spec):
            selected.append(seed_spec)

    while len(selected) < target_count:
        remaining = [
            candidate
            for candidate in candidate_seed_specs
            if not _has_seed_spec(selected, candidate)
        ]
        if not remaining:
            raise ValueError("insufficient_seed_candidates_after_prune")
        next_seed = min(
            remaining,
            key=lambda candidate: (
                _max_jaccard_overlap(candidate, selected),
                _seed_order(candidate),
            ),
        )
        selected.append(next_seed)
    return selected


def _has_seed_spec(
    selected: Sequence[FrontierSeedSpecification],
    candidate: FrontierSeedSpecification,
) -> bool:
    return any(
        seed_spec.operator_name == candidate.operator_name
        and seed_spec.seed_terms == candidate.seed_terms
        and seed_spec.knowledge_pack_ids == candidate.knowledge_pack_ids
        for seed_spec in selected
    )


def _max_jaccard_overlap(
    candidate: FrontierSeedSpecification,
    selected: Sequence[FrontierSeedSpecification],
) -> float:
    if not selected:
        return 0.0
    candidate_terms = set(candidate.seed_terms)
    return max(
        _jaccard(candidate_terms, set(seed_spec.seed_terms))
        for seed_spec in selected
    )


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _seed_order(seed_spec: FrontierSeedSpecification) -> int:
    prefix, _, _ = seed_spec.seed_rationale.partition(":")
    return int(prefix)


def _unique_seed_specs(seed_specs: Sequence[FrontierSeedSpecification]) -> list[FrontierSeedSpecification]:
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...], str | None]] = set()
    output: list[FrontierSeedSpecification] = []
    for seed_spec in seed_specs:
        key = (
            seed_spec.operator_name,
            tuple(seed_spec.seed_terms),
            tuple(seed_spec.knowledge_pack_ids),
            seed_spec.target_location,
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(seed_spec)
    return output


def _seed_id(seed_spec: FrontierSeedSpecification) -> str:
    digest = sha1(
        json.dumps(
            {
                "knowledge_pack_ids": seed_spec.knowledge_pack_ids,
                "seed_terms": seed_spec.seed_terms,
                "target_location": seed_spec.target_location,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:8]
    return f"seed_{seed_spec.operator_name}_{digest}"


def _merged_allowlist(truth_values: Sequence[str], override_values: Sequence[str]) -> list[str]:
    truth = stable_deduplicate(list(truth_values))
    override = stable_deduplicate(list(override_values))
    if not truth:
        return override
    if not override:
        return truth
    overlap = _normalized_intersection(truth, override)
    return overlap or truth


def _merged_min(truth_value: int | None, override_value: int | None) -> int | None:
    if truth_value is not None and override_value is not None:
        return max(truth_value, override_value)
    return override_value if override_value is not None else truth_value


def _merged_max(truth_value: int | None, override_value: int | None) -> int | None:
    if truth_value is not None and override_value is not None:
        return min(truth_value, override_value)
    return override_value if override_value is not None else truth_value


def _merged_degree_requirement(truth_value: str | None, override_value: str | None) -> str | None:
    if override_value is None:
        return truth_value
    if truth_value is None:
        return override_value
    return truth_value if DEGREE_RANK[truth_value] >= DEGREE_RANK[override_value] else override_value


def _merged_gender_requirement(truth_value: str | None, override_value: str | None) -> str | None:
    if override_value is None:
        return truth_value
    if truth_value is None:
        return override_value
    return truth_value if truth_value == override_value else truth_value


def _normalized_weight(value: float | None, fallback: float) -> float:
    return value if value is not None and value >= 0 else fallback


def _join_sentences(parts: Sequence[str]) -> str:
    return " ".join(_sentence(part) for part in parts if _normalize_text(part))


def _sentence(value: str) -> str:
    clean = _normalize_text(value)
    if not clean:
        return ""
    if clean.endswith((".", "!", "?", "。", "！", "？")):
        return clean
    return f"{clean}."


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _normalized_intersection(left: Sequence[str], right: Sequence[str]) -> list[str]:
    right_lookup = {_normalize_text(value).casefold() for value in right}
    return [value for value in left if _normalize_text(value).casefold() in right_lookup]


__all__ = [
    "freeze_scoring_policy",
    "generate_bootstrap_output",
    "initialize_frontier_state",
    "route_domain_knowledge_pack",
]
