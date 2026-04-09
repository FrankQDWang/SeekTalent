from __future__ import annotations

import json
from hashlib import sha1
from typing import Any, Sequence

from seektalent.models import (
    BusinessPolicyPack,
    FitGateConstraints,
    FusionWeights,
    GroundingDraft,
    GroundingEvidenceCard,
    GroundingKnowledgeBaseSnapshot,
    GroundingKnowledgeCard,
    GroundingOutput,
    FrontierNode_t,
    FrontierSeedSpecification,
    FrontierState_t,
    KnowledgeRetrievalBudget,
    KnowledgeRetrievalResult,
    OperatorStatistics,
    PenaltyWeights,
    RequirementSheet,
    RerankerCalibration,
    RuntimeSearchBudget,
    ScoringPolicy,
    stable_deduplicate,
)


ROUND0_OPERATORS = {"must_have_alias", "strict_core", "domain_company"}
ALLOWED_EVIDENCE_TYPES = {
    "title_alias",
    "query_term",
    "must_have_link",
    "preferred_link",
    "generic_requirement",
}
CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
DEGREE_RANK = {
    "大专及以上": 1,
    "本科及以上": 2,
    "硕士及以上": 3,
    "博士及以上": 4,
}


def retrieve_grounding_knowledge(
    requirement_sheet: RequirementSheet,
    business_policy_pack: BusinessPolicyPack,
    knowledge_base_snapshot: GroundingKnowledgeBaseSnapshot,
    knowledge_retrieval_budget: KnowledgeRetrievalBudget,
    *,
    knowledge_cards: Sequence[GroundingKnowledgeCard],
) -> KnowledgeRetrievalResult:
    explicit_domain_pack_ids = stable_deduplicate(business_policy_pack.domain_pack_ids)
    if len(explicit_domain_pack_ids) > 2:
        raise ValueError("too_many_explicit_domain_packs")
    if any(pack_id not in knowledge_base_snapshot.domain_pack_ids for pack_id in explicit_domain_pack_ids):
        raise ValueError("unknown_domain_pack_id")

    card_pool = [card for card in knowledge_cards if card.card_id in knowledge_base_snapshot.card_ids]
    ranked_domain_packs = [
        {
            "domain_pack_id": pack_id,
            "score": _pack_score(pack_id, requirement_sheet, card_pool),
            "supported_must_haves": _supported_must_haves(pack_id, requirement_sheet, card_pool),
        }
        for pack_id in knowledge_base_snapshot.domain_pack_ids
    ]
    ranked_domain_packs = sorted(
        ranked_domain_packs,
        key=lambda row: row["score"],
        reverse=True,
    )[: knowledge_retrieval_budget.max_inferred_domain_packs]

    top1 = ranked_domain_packs[0] if ranked_domain_packs else None
    top2 = ranked_domain_packs[1] if len(ranked_domain_packs) > 1 else None
    single_inferred = bool(
        top1
        and top1["score"] >= 5
        and (top2 is None or top1["score"] - top2["score"] >= 2)
    )
    dual_inferred = bool(
        top1
        and top2
        and top1["score"] >= 5
        and top2["score"] >= 5
        and top1["score"] - top2["score"] < 2
        and _normalized_overlap_diff(top1["supported_must_haves"], top2["supported_must_haves"])
        and _normalized_overlap_diff(top2["supported_must_haves"], top1["supported_must_haves"])
    )
    if explicit_domain_pack_ids:
        routing_mode = "explicit_domain"
        selected_domain_pack_ids = explicit_domain_pack_ids
    elif single_inferred:
        routing_mode = "inferred_domain"
        selected_domain_pack_ids = [str(top1["domain_pack_id"])]
    elif dual_inferred:
        routing_mode = "inferred_domain"
        selected_domain_pack_ids = [str(top1["domain_pack_id"]), str(top2["domain_pack_id"])]
    else:
        routing_mode = "generic_fallback"
        selected_domain_pack_ids = []

    if routing_mode == "explicit_domain":
        routing_confidence = 1.0
    elif routing_mode == "inferred_domain" and len(selected_domain_pack_ids) == 1:
        routing_confidence = 0.8
    elif routing_mode == "inferred_domain":
        routing_confidence = 0.7
    else:
        routing_confidence = 0.3

    candidate_cards = [
        card
        for card in card_pool
        if card.domain_id in selected_domain_pack_ids
    ] if routing_mode != "generic_fallback" else []
    matched_cards = [
        {
            "card": card,
            "score": _card_score(card, requirement_sheet),
            "confidence_rank": CONFIDENCE_RANK[card.confidence],
        }
        for card in candidate_cards
    ]
    matched_cards.sort(key=lambda row: row["card"].card_id)
    matched_cards.sort(key=lambda row: row["card"].freshness_date, reverse=True)
    matched_cards.sort(key=lambda row: row["confidence_rank"], reverse=True)
    matched_cards.sort(key=lambda row: row["score"], reverse=True)
    matched_cards = matched_cards[: knowledge_retrieval_budget.max_cards]
    retrieved_cards = [row["card"] for row in matched_cards]
    negative_signal_terms = (
        stable_deduplicate(requirement_sheet.exclusion_signals)
        if routing_mode == "generic_fallback"
        else stable_deduplicate(
            [
                signal
                for card in retrieved_cards
                if card.confidence != "low"
                for signal in card.negative_signals
            ]
        )
    )
    return KnowledgeRetrievalResult(
        knowledge_base_snapshot_id=knowledge_base_snapshot.snapshot_id,
        routing_mode=routing_mode,
        selected_domain_pack_ids=selected_domain_pack_ids,
        routing_confidence=routing_confidence,
        fallback_reason=None if routing_mode != "generic_fallback" else "no_domain_pack_scored_above_threshold",
        retrieved_cards=retrieved_cards,
        negative_signal_terms=negative_signal_terms,
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
    hard_constraint_phrase = "; ".join(
        part
        for part in [
            _phrase("location", truth_gate.locations),
            _range_phrase("min", truth_gate.min_years, "years"),
            _range_phrase("max", truth_gate.max_years, "years"),
        ]
        if part
    )
    must_have_phrase = ", ".join(requirement_sheet.must_have_capabilities)
    preferred_phrase = ", ".join(requirement_sheet.preferred_capabilities)
    rerank_query_text = _normalize_text(
        requirement_sheet.role_title
        + f"; must-have: {must_have_phrase}"
        + (f"; {hard_constraint_phrase}" if hard_constraint_phrase else "")
        + (f"; preferred: {preferred_phrase}" if preferred_phrase else "")
    )
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
            "Rank candidate resumes for hiring relevance. Prioritize must-have capabilities first, "
            "use preferred capabilities as secondary evidence, and do not hard-reject on soft risk signals."
        ),
        rerank_query_text=rerank_query_text,
        reranker_calibration_snapshot=reranker_calibration,
        ranking_audit_notes=_normalize_text(
            f"{requirement_sheet.scoring_rationale} must-have 优先于 preferred；低置信度稳定性风险不处罚。"
        ),
    )


def generate_grounding_output(
    requirement_sheet: RequirementSheet,
    knowledge_retrieval_result: KnowledgeRetrievalResult,
    grounding_draft: GroundingDraft,
) -> GroundingOutput:
    routing_mode = knowledge_retrieval_result.routing_mode
    selected_domain_pack_ids = knowledge_retrieval_result.selected_domain_pack_ids
    support_cards = _supporting_cards(
        knowledge_retrieval_result.retrieved_cards,
        requirement_sheet.must_have_capabilities,
        requirement_sheet.preferred_capabilities,
        routing_mode,
    )
    support_card_ids = {card.card_id for card in support_cards}
    if routing_mode == "generic_fallback":
        evidence_cards = _generic_evidence_cards(requirement_sheet)
        normalized_seed_specs = _generic_seed_specs(
            requirement_sheet,
            knowledge_retrieval_result.negative_signal_terms,
        )
    else:
        evidence_cards = [
            card
            for card in grounding_draft.grounding_evidence_cards
            if card.source_card_id in support_card_ids and card.evidence_type in ALLOWED_EVIDENCE_TYPES
        ]
        if not evidence_cards and support_cards:
            top_support_card = support_cards[0]
            evidence_cards = [
                GroundingEvidenceCard(
                    source_card_id=top_support_card.card_id,
                    label=top_support_card.title,
                    rationale="auto-filled from highest-ranked supporting card",
                    evidence_type="title_alias",
                    confidence=top_support_card.confidence,
                )
            ]
        normalized_seed_specs = [
            FrontierSeedSpecification(
                operator_name=seed_spec.operator_name,
                seed_terms=stable_deduplicate(seed_spec.seed_terms)[:4],
                seed_rationale=_normalize_text(seed_spec.seed_rationale),
                source_card_ids=[
                    source_card_id
                    for source_card_id in seed_spec.source_card_ids
                    if source_card_id in support_card_ids
                ],
                expected_coverage=(
                    stable_deduplicate(seed_spec.expected_coverage)
                    if seed_spec.expected_coverage
                    else _linked_coverage(seed_spec.source_card_ids, support_cards)
                ),
                negative_terms=stable_deduplicate(
                    seed_spec.negative_terms + knowledge_retrieval_result.negative_signal_terms
                ),
                target_location=seed_spec.target_location,
            )
            for seed_spec in grounding_draft.frontier_seed_specifications
            if seed_spec.operator_name in ROUND0_OPERATORS
            and all(source_card_id in support_card_ids for source_card_id in seed_spec.source_card_ids)
        ]
    ranked_seed_specs = (
        normalized_seed_specs
        if routing_mode == "generic_fallback"
        else sorted(
            normalized_seed_specs,
            key=lambda seed_spec: (
                _coverage_count(seed_spec.expected_coverage, requirement_sheet.must_have_capabilities),
                _coverage_count(seed_spec.expected_coverage, requirement_sheet.preferred_capabilities),
                -_source_card_rank(seed_spec.source_card_ids, support_cards, selected_domain_pack_ids),
                1 if _normalize_text(seed_spec.seed_rationale) else 0,
            ),
            reverse=True,
        )
    )
    bounded_seed_specs = _unique_seed_specs(
        [
            FrontierSeedSpecification(
                operator_name=seed_spec.operator_name,
                seed_terms=stable_deduplicate(seed_spec.seed_terms)[:4],
                seed_rationale=seed_spec.seed_rationale,
                source_card_ids=list(seed_spec.source_card_ids),
                expected_coverage=list(seed_spec.expected_coverage),
                negative_terms=list(seed_spec.negative_terms),
                target_location=(
                    requirement_sheet.hard_constraints.locations[0]
                    if len(requirement_sheet.hard_constraints.locations) == 1
                    else None
                ),
            )
            for seed_spec in ranked_seed_specs
            if len(stable_deduplicate(seed_spec.seed_terms)) >= 2
        ][:5]
    )
    if len(bounded_seed_specs) < 3:
        raise ValueError("insufficient_seed_specifications")
    return GroundingOutput(
        grounding_evidence_cards=evidence_cards,
        frontier_seed_specifications=bounded_seed_specs,
    )


def initialize_frontier_state(
    grounding_output: GroundingOutput,
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
                source_card_ids=list(seed_spec.source_card_ids),
                seed_rationale=seed_spec.seed_rationale,
                negative_terms=list(seed_spec.negative_terms),
                parent_shortlist_candidate_ids=[],
                node_shortlist_candidate_ids=[],
                node_shortlist_score_snapshot={},
                previous_branch_evaluation=None,
                reward_breakdown=None,
                status="open",
            )
            for seed_spec in grounding_output.frontier_seed_specifications
        )
    }
    open_frontier_node_ids = list(frontier_nodes)
    return FrontierState_t(
        frontier_nodes=frontier_nodes,
        open_frontier_node_ids=open_frontier_node_ids,
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


def _pack_score(
    pack_id: str,
    requirement_sheet: RequirementSheet,
    card_pool: Sequence[GroundingKnowledgeCard],
) -> int:
    return (
        4 * _title_alias_hit_any(pack_id, requirement_sheet.role_title, card_pool)
        + 3 * len(_supported_must_haves(pack_id, requirement_sheet, card_pool))
        + _preferred_link_hits(pack_id, requirement_sheet, card_pool)
        - 3 * _exclusion_conflicts(pack_id, requirement_sheet, card_pool)
    )


def _title_alias_hit_any(pack_id: str, role_title: str, card_pool: Sequence[GroundingKnowledgeCard]) -> int:
    return int(
        any(
            _lexical_hit(role_title, [card.title, *card.aliases, *card.canonical_terms])
            for card in card_pool
            if card.domain_id == pack_id
        )
    )


def _supported_must_haves(
    pack_id: str,
    requirement_sheet: RequirementSheet,
    card_pool: Sequence[GroundingKnowledgeCard],
) -> list[str]:
    pack_terms = [
        term
        for card in card_pool
        if card.domain_id == pack_id
        for term in [card.title, *card.aliases, *card.canonical_terms, *card.must_have_links, *card.query_terms]
    ]
    return [
        must_have
        for must_have in requirement_sheet.must_have_capabilities
        if _lexical_hit(must_have, pack_terms)
    ]


def _preferred_link_hits(
    pack_id: str,
    requirement_sheet: RequirementSheet,
    card_pool: Sequence[GroundingKnowledgeCard],
) -> int:
    pack_terms = [
        term
        for card in card_pool
        if card.domain_id == pack_id
        for term in [*card.preferred_links, *card.query_terms, *card.canonical_terms]
    ]
    return _distinct_hits(
        [
            *requirement_sheet.preferred_capabilities,
            *requirement_sheet.preferences.preferred_domains,
            *requirement_sheet.preferences.preferred_backgrounds,
        ],
        pack_terms,
    )


def _exclusion_conflicts(
    pack_id: str,
    requirement_sheet: RequirementSheet,
    card_pool: Sequence[GroundingKnowledgeCard],
) -> int:
    pack_terms = [
        term
        for card in card_pool
        if card.domain_id == pack_id and card.confidence != "low"
        for term in [*card.positive_signals, *card.query_terms]
    ]
    return _distinct_hits(requirement_sheet.exclusion_signals, pack_terms)


def _card_score(card: GroundingKnowledgeCard, requirement_sheet: RequirementSheet) -> int:
    role_query_terms = stable_deduplicate(
        [
            requirement_sheet.role_title,
            *requirement_sheet.must_have_capabilities,
            *requirement_sheet.preferred_capabilities,
            *requirement_sheet.preferences.preferred_domains,
            *requirement_sheet.preferences.preferred_backgrounds,
        ]
    )
    return (
        4 * int(_lexical_hit(requirement_sheet.role_title, [card.title, *card.aliases, *card.canonical_terms]))
        + 2 * _distinct_hits(
            requirement_sheet.must_have_capabilities,
            [*card.must_have_links, *card.query_terms, *card.canonical_terms],
        )
        + _distinct_hits(
            [
                *requirement_sheet.preferred_capabilities,
                *requirement_sheet.preferences.preferred_domains,
                *requirement_sheet.preferences.preferred_backgrounds,
            ],
            card.preferred_links,
        )
        + _distinct_hits(role_query_terms, card.query_terms)
        - 2 * _distinct_hits(requirement_sheet.exclusion_signals, [*card.positive_signals, *card.query_terms])
    )


def _supporting_cards(
    retrieved_cards: Sequence[GroundingKnowledgeCard],
    must_have: Sequence[str],
    preferred: Sequence[str],
    routing_mode: str,
) -> list[GroundingKnowledgeCard]:
    if routing_mode == "generic_fallback":
        return []
    ranked_rows = sorted(
        (
            {
                "card": card,
                "must_cover": _exact_overlap_count(card.must_have_links, must_have),
                "pref_cover": _exact_overlap_count(card.preferred_links, preferred),
                "confidence_rank": CONFIDENCE_RANK[card.confidence],
            }
            for card in retrieved_cards
            if card.confidence != "low"
        ),
        key=lambda row: (row["must_cover"], row["pref_cover"], row["confidence_rank"]),
        reverse=True,
    )
    return [row["card"] for row in ranked_rows[:4]]


def _generic_evidence_cards(requirement_sheet: RequirementSheet) -> list[GroundingEvidenceCard]:
    evidence_cards = [
        GroundingEvidenceCard(
            source_card_id="generic.requirement.role_title",
            label=requirement_sheet.role_title,
            rationale="generic fallback role title anchor",
            evidence_type="generic_requirement",
            confidence="high",
        )
    ]
    if requirement_sheet.must_have_capabilities:
        evidence_cards.append(
            GroundingEvidenceCard(
                source_card_id="generic.requirement.must_have.0",
                label=requirement_sheet.must_have_capabilities[0],
                rationale="generic fallback first must-have",
                evidence_type="generic_requirement",
                confidence="high",
            )
        )
    if len(requirement_sheet.must_have_capabilities) >= 2:
        evidence_cards.append(
            GroundingEvidenceCard(
                source_card_id="generic.requirement.must_have.1",
                label=requirement_sheet.must_have_capabilities[1],
                rationale="generic fallback second must-have",
                evidence_type="generic_requirement",
                confidence="high",
            )
        )
    return evidence_cards


def _generic_seed_specs(
    requirement_sheet: RequirementSheet,
    negative_signal_terms: Sequence[str],
) -> list[FrontierSeedSpecification]:
    title_token_terms = _split_role_title(requirement_sheet.role_title)[:2]
    summary_anchor = _normalize_text(requirement_sheet.role_summary) or requirement_sheet.role_title
    generic_anchor_terms = stable_deduplicate(
        [
            requirement_sheet.role_title,
            summary_anchor,
            *title_token_terms,
            *requirement_sheet.preferred_capabilities[:1],
        ]
    )[:4]
    must_have = requirement_sheet.must_have_capabilities
    preferred = requirement_sheet.preferred_capabilities
    seed_specs = [
        FrontierSeedSpecification(
            operator_name="must_have_alias",
            seed_terms=stable_deduplicate(
                [requirement_sheet.role_title, *title_token_terms[:1], *must_have[:1]]
            )[:4],
            seed_rationale="role_title_anchor",
            source_card_ids=[],
            expected_coverage=stable_deduplicate(must_have[:1]),
            negative_terms=stable_deduplicate(list(negative_signal_terms)),
            target_location=None,
        ),
        FrontierSeedSpecification(
            operator_name="must_have_alias",
            seed_terms=stable_deduplicate([*must_have[:2], *preferred[:1], *generic_anchor_terms])[:4],
            seed_rationale="must_have_core",
            source_card_ids=[],
            expected_coverage=stable_deduplicate(must_have[:2]),
            negative_terms=stable_deduplicate(list(negative_signal_terms)),
            target_location=None,
        ),
        FrontierSeedSpecification(
            operator_name="strict_core",
            seed_terms=stable_deduplicate([*must_have[2:4], *generic_anchor_terms])[:4],
            seed_rationale="coverage_repair",
            source_card_ids=[],
            expected_coverage=stable_deduplicate(must_have[2:4]),
            negative_terms=stable_deduplicate(list(negative_signal_terms)),
            target_location=None,
        ),
    ]
    seed_specs.extend(
        FrontierSeedSpecification(
            operator_name="strict_core",
            seed_terms=stable_deduplicate([repair_target, *generic_anchor_terms])[:4],
            seed_rationale="must_have_repair",
            source_card_ids=[],
            expected_coverage=[repair_target],
            negative_terms=stable_deduplicate(list(negative_signal_terms)),
            target_location=None,
        )
        for repair_target in must_have[4:6]
    )
    return seed_specs


def _linked_coverage(
    source_card_ids: Sequence[str],
    support_cards: Sequence[GroundingKnowledgeCard],
) -> list[str]:
    source_ids = {_normalized(term) for term in source_card_ids}
    return stable_deduplicate(
        [
            term
            for card in support_cards
            if _normalized(card.card_id) in source_ids
            for term in [*card.must_have_links, *card.preferred_links]
        ]
    )


def _coverage_count(expected_coverage: Sequence[str], target_terms: Sequence[str]) -> int:
    target_lookup = {_normalized(term) for term in target_terms}
    return len({_normalized(term) for term in expected_coverage if _normalized(term) in target_lookup})


def _source_card_rank(
    source_card_ids: Sequence[str],
    support_cards: Sequence[GroundingKnowledgeCard],
    selected_domain_pack_ids: Sequence[str],
) -> int:
    support_by_id = {card.card_id: card for card in support_cards}
    for index, pack_id in enumerate(selected_domain_pack_ids):
        if any(
            card is not None and card.domain_id == pack_id
            for source_card_id in source_card_ids
            for card in [support_by_id.get(source_card_id)]
        ):
            return index
    return 999


def _unique_seed_specs(seed_specs: Sequence[FrontierSeedSpecification]) -> list[FrontierSeedSpecification]:
    seen: set[tuple[Any, ...]] = set()
    output: list[FrontierSeedSpecification] = []
    for seed_spec in seed_specs:
        key = (
            seed_spec.operator_name,
            tuple(seed_spec.seed_terms),
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


def _range_phrase(label: str, value: int | None, suffix: str) -> str:
    if value is None:
        return ""
    return f"{label} {value} {suffix}"


def _phrase(label: str, values: Sequence[str]) -> str:
    items = stable_deduplicate(list(values))
    if not items:
        return ""
    return f"{label}: {', '.join(items)}"


def _split_role_title(value: str) -> list[str]:
    text = value
    for separator in ("/", ",", "(", ")", "-", "|"):
        text = text.replace(separator, "\n")
    return stable_deduplicate([part for part in text.splitlines() if _normalize_text(part)])


def _normalized_intersection(left: Sequence[str], right: Sequence[str]) -> list[str]:
    right_lookup = {_normalized(value) for value in right}
    return [value for value in left if _normalized(value) in right_lookup]


def _normalized_overlap_diff(left: Sequence[str], right: Sequence[str]) -> list[str]:
    right_lookup = {_normalized(value) for value in right}
    return [value for value in left if _normalized(value) not in right_lookup]


def _exact_overlap_count(left: Sequence[str], right: Sequence[str]) -> int:
    return len(_normalized_intersection(left, right))


def _distinct_hits(values: Sequence[str], terms: Sequence[str]) -> int:
    return len({value for value in values if _lexical_hit(value, terms)})


def _lexical_hit(text: str, terms: Sequence[str]) -> bool:
    haystack = _normalized(text)
    if not haystack:
        return False
    return any(term_norm and term_norm in haystack for term_norm in (_normalized(term) for term in terms))


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _normalized(value: str) -> str:
    return _normalize_text(value).casefold()


__all__ = [
    "freeze_scoring_policy",
    "generate_grounding_output",
    "initialize_frontier_state",
    "retrieve_grounding_knowledge",
]
