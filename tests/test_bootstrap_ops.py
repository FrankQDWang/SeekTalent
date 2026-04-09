from __future__ import annotations

import pytest

from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.bootstrap_ops import (
    freeze_scoring_policy,
    generate_grounding_output,
    initialize_frontier_state,
    retrieve_grounding_knowledge,
)
from seektalent.models import (
    FitGateConstraints,
    GroundingDraft,
    GroundingEvidenceCard,
    GroundingKnowledgeBaseSnapshot,
    GroundingOutput,
    HardConstraints,
    FusionWeightPreferences,
    FrontierSeedSpecification,
    GroundingKnowledgeCard,
    KnowledgeRetrievalResult,
    RequirementPreferences,
    RequirementSheet,
)


def _requirement_sheet(
    *,
    role_title: str = "Senior Python / LLM Engineer",
    must_have: list[str] | None = None,
    preferred: list[str] | None = None,
    exclusion: list[str] | None = None,
    preferred_backgrounds: list[str] | None = None,
) -> RequirementSheet:
    return RequirementSheet(
        role_title=role_title,
        role_summary="Build Python, LLM, and retrieval systems.",
        must_have_capabilities=must_have or [
            "Python backend",
            "LLM application",
            "retrieval or ranking experience",
        ],
        preferred_capabilities=preferred or ["workflow orchestration", "to-b delivery"],
        exclusion_signals=exclusion or ["data analyst"],
        preferences=RequirementPreferences(
            preferred_domains=[],
            preferred_backgrounds=preferred_backgrounds or [],
        ),
        scoring_rationale="must-have 优先，偏好次之。",
    )


def test_retrieve_grounding_knowledge_supports_explicit_domain() -> None:
    assets = default_bootstrap_assets()
    requirement_sheet = _requirement_sheet()
    policy = assets.business_policy_pack.model_copy(update={"domain_pack_ids": ["llm_agent_rag_engineering"]})

    result = retrieve_grounding_knowledge(
        requirement_sheet,
        policy,
        assets.knowledge_base_snapshot,
        assets.knowledge_retrieval_budget,
        knowledge_cards=assets.knowledge_cards,
    )

    assert result.routing_mode == "explicit_domain"
    assert result.selected_domain_pack_ids == ["llm_agent_rag_engineering"]
    assert [card.domain_id for card in result.retrieved_cards] == ["llm_agent_rag_engineering", "llm_agent_rag_engineering"]
    assert "data analyst" in result.negative_signal_terms


def test_retrieve_grounding_knowledge_fails_for_unknown_explicit_domain() -> None:
    assets = default_bootstrap_assets()

    with pytest.raises(ValueError, match="unknown_domain_pack_id"):
        retrieve_grounding_knowledge(
            _requirement_sheet(),
            assets.business_policy_pack.model_copy(update={"domain_pack_ids": ["missing"]}),
            assets.knowledge_base_snapshot,
            assets.knowledge_retrieval_budget,
            knowledge_cards=assets.knowledge_cards,
        )


def test_retrieve_grounding_knowledge_fails_for_too_many_explicit_domains() -> None:
    assets = default_bootstrap_assets()

    with pytest.raises(ValueError, match="too_many_explicit_domain_packs"):
        retrieve_grounding_knowledge(
            _requirement_sheet(),
            assets.business_policy_pack.model_copy(
                update={
                    "domain_pack_ids": [
                        "llm_agent_rag_engineering",
                        "search_ranking_retrieval_engineering",
                        "finance_risk_control_ai",
                    ]
                }
            ),
            assets.knowledge_base_snapshot,
            assets.knowledge_retrieval_budget,
            knowledge_cards=assets.knowledge_cards,
        )


def test_retrieve_grounding_knowledge_supports_single_inferred_domain() -> None:
    assets = default_bootstrap_assets()
    requirement_sheet = _requirement_sheet(must_have=["Python backend", "LLM application"])

    result = retrieve_grounding_knowledge(
        requirement_sheet,
        assets.business_policy_pack,
        assets.knowledge_base_snapshot,
        assets.knowledge_retrieval_budget,
        knowledge_cards=assets.knowledge_cards,
    )

    assert result.routing_mode == "inferred_domain"
    assert result.selected_domain_pack_ids == ["llm_agent_rag_engineering"]


def test_retrieve_grounding_knowledge_supports_dual_inferred_domain() -> None:
    assets = default_bootstrap_assets()

    result = retrieve_grounding_knowledge(
        _requirement_sheet(preferred=[], exclusion=[]),
        assets.business_policy_pack,
        assets.knowledge_base_snapshot,
        assets.knowledge_retrieval_budget,
        knowledge_cards=assets.knowledge_cards,
    )

    assert result.routing_mode == "inferred_domain"
    assert result.selected_domain_pack_ids == [
        "llm_agent_rag_engineering",
        "search_ranking_retrieval_engineering",
    ]


def test_retrieve_grounding_knowledge_falls_back_to_generic() -> None:
    assets = default_bootstrap_assets()
    requirement_sheet = _requirement_sheet(
        role_title="People Operations Manager",
        must_have=["stakeholder management"],
        preferred=["hiring operations"],
        exclusion=["sales"],
    )

    result = retrieve_grounding_knowledge(
        requirement_sheet,
        assets.business_policy_pack,
        assets.knowledge_base_snapshot,
        assets.knowledge_retrieval_budget,
        knowledge_cards=assets.knowledge_cards,
    )

    assert result.routing_mode == "generic_fallback"
    assert result.selected_domain_pack_ids == []
    assert result.retrieved_cards == []
    assert result.negative_signal_terms == ["sales"]


def test_retrieve_grounding_knowledge_uses_card_id_ascending_as_last_tiebreaker() -> None:
    assets = default_bootstrap_assets()
    result = retrieve_grounding_knowledge(
        _requirement_sheet(role_title="Agent Engineer", must_have=["Python"]),
        assets.business_policy_pack.model_copy(update={"domain_pack_ids": ["pack-a"]}),
        GroundingKnowledgeBaseSnapshot(
            snapshot_id="kb-1",
            domain_pack_ids=["pack-a"],
            compiled_report_ids=["report-1"],
            card_ids=["b-card", "a-card"],
            compiled_at="2026-04-07",
        ),
        assets.knowledge_retrieval_budget,
        knowledge_cards=[
            GroundingKnowledgeCard(
                card_id="b-card",
                domain_id="pack-a",
                report_type="role_family",
                card_type="role_alias",
                title="Agent Engineer",
                summary="Agent role.",
                canonical_terms=["Agent Engineer"],
                aliases=[],
                positive_signals=[],
                negative_signals=[],
                query_terms=["Python"],
                must_have_links=["Python"],
                preferred_links=[],
                confidence="high",
                source_report_ids=["report-1"],
                source_model_votes=1,
                freshness_date="2026-04-07",
            ),
            GroundingKnowledgeCard(
                card_id="a-card",
                domain_id="pack-a",
                report_type="role_family",
                card_type="role_alias",
                title="Agent Engineer",
                summary="Agent role.",
                canonical_terms=["Agent Engineer"],
                aliases=[],
                positive_signals=[],
                negative_signals=[],
                query_terms=["Python"],
                must_have_links=["Python"],
                preferred_links=[],
                confidence="high",
                source_report_ids=["report-1"],
                source_model_votes=1,
                freshness_date="2026-04-07",
            ),
        ],
    )

    assert [card.card_id for card in result.retrieved_cards] == ["a-card", "b-card"]


def test_freeze_scoring_policy_only_tightens_truth_gate_and_normalizes_weights() -> None:
    assets = default_bootstrap_assets()
    requirement_sheet = _requirement_sheet()
    requirement_sheet = requirement_sheet.model_copy(
        update={
            "hard_constraints": HardConstraints(
                locations=["上海"],
                min_years=5,
                max_years=10,
                company_names=["阿里巴巴"],
                school_names=["复旦大学"],
                degree_requirement="本科及以上",
                gender_requirement="男",
                min_age=28,
                max_age=35,
            ),
        }
    )
    policy = assets.business_policy_pack.model_copy(
        update={
            "fit_gate_overrides": FitGateConstraints(
                locations=["上海", "杭州"],
                min_years=3,
                max_years=12,
                company_names=["阿里巴巴", "蚂蚁集团"],
                school_names=["复旦大学", "上海交通大学"],
                degree_requirement="硕士及以上",
                gender_requirement="女",
                min_age=25,
                max_age=40,
            ),
            "fusion_weight_preferences": FusionWeightPreferences(rerank=0.4, must_have=0.3, preferred=0.2, risk_penalty=0.1),
        }
    )

    scoring_policy = freeze_scoring_policy(
        requirement_sheet,
        policy,
        assets.reranker_calibration,
    )

    assert scoring_policy.fit_gate_constraints.locations == ["上海"]
    assert scoring_policy.fit_gate_constraints.min_years == 5
    assert scoring_policy.fit_gate_constraints.max_years == 10
    assert scoring_policy.fit_gate_constraints.company_names == ["阿里巴巴"]
    assert scoring_policy.fit_gate_constraints.school_names == ["复旦大学"]
    assert scoring_policy.fit_gate_constraints.degree_requirement == "硕士及以上"
    assert scoring_policy.fit_gate_constraints.gender_requirement == "男"
    assert scoring_policy.penalty_weights.job_hop_confidence_floor == pytest.approx(0.6)
    assert sum(scoring_policy.fusion_weights.model_dump().values()) == pytest.approx(1.0)
    assert "location: 上海" in scoring_policy.rerank_query_text
    assert "must-have:" in scoring_policy.rerank_query_text


def test_freeze_scoring_policy_preserves_explicit_zero_stability_settings() -> None:
    assets = default_bootstrap_assets()
    scoring_policy = freeze_scoring_policy(
        _requirement_sheet(),
        assets.business_policy_pack.model_copy(
            update={
                "stability_policy": assets.business_policy_pack.stability_policy.model_copy(
                    update={"penalty_weight": 0.0, "confidence_floor": 0.0}
                ),
            }
        ),
        assets.reranker_calibration,
    )

    assert scoring_policy.penalty_weights.job_hop == pytest.approx(0.0)
    assert scoring_policy.penalty_weights.job_hop_confidence_floor == pytest.approx(0.0)


def test_generate_grounding_output_whitelists_non_generic_cards_and_seeds() -> None:
    assets = default_bootstrap_assets()
    requirement_sheet = _requirement_sheet()
    knowledge_retrieval_result = retrieve_grounding_knowledge(
        requirement_sheet,
        assets.business_policy_pack.model_copy(update={"domain_pack_ids": ["llm_agent_rag_engineering"]}),
        assets.knowledge_base_snapshot,
        assets.knowledge_retrieval_budget,
        knowledge_cards=assets.knowledge_cards,
    )
    card_id = knowledge_retrieval_result.retrieved_cards[0].card_id
    grounding_draft = GroundingDraft(
        grounding_evidence_cards=[
            GroundingEvidenceCard(
                source_card_id=card_id,
                label="agent engineer",
                rationale="matches role",
                evidence_type="title_alias",
                confidence="high",
            ),
            GroundingEvidenceCard(
                source_card_id="missing.card",
                label="ignore me",
                rationale="invalid source",
                evidence_type="title_alias",
                confidence="high",
            ),
        ],
        frontier_seed_specifications=[
            FrontierSeedSpecification(
                operator_name="must_have_alias",
                seed_terms=["agent engineer", "rag", "python"],
                seed_rationale="cover core",
                source_card_ids=[card_id],
                expected_coverage=["Python backend", "LLM application"],
                negative_terms=["frontend"],
                target_location=None,
            ),
            FrontierSeedSpecification(
                operator_name="domain_company",
                seed_terms=["enterprise agent", "to-b", "python"],
                seed_rationale="company context",
                source_card_ids=[knowledge_retrieval_result.retrieved_cards[1].card_id],
                expected_coverage=["to-b delivery"],
                negative_terms=[],
                target_location=None,
            ),
            FrontierSeedSpecification(
                operator_name="strict_core",
                seed_terms=["agent engineer", "workflow orchestration"],
                seed_rationale="coverage repair",
                source_card_ids=[card_id],
                expected_coverage=["workflow orchestration"],
                negative_terms=[],
                target_location=None,
            ),
            FrontierSeedSpecification(
                operator_name="must_have_alias",
                seed_terms=["bad source", "ignore"],
                seed_rationale="invalid",
                source_card_ids=["missing.card"],
                expected_coverage=["Python backend"],
                negative_terms=[],
                target_location=None,
            ),
        ],
    )

    output = generate_grounding_output(
        requirement_sheet,
        knowledge_retrieval_result,
        grounding_draft,
    )

    assert [card.source_card_id for card in output.grounding_evidence_cards] == [card_id]
    assert len(output.frontier_seed_specifications) == 3
    assert all(len(seed.seed_terms) >= 2 for seed in output.frontier_seed_specifications)
    assert all(seed.target_location is None for seed in output.frontier_seed_specifications)


def test_generate_grounding_output_uses_fixed_generic_seed_order() -> None:
    requirement_sheet = _requirement_sheet(
        role_title="People Operations Manager",
        must_have=["stakeholder management", "cross-functional collaboration", "process design"],
        preferred=["hiring operations"],
        exclusion=["sales"],
    )
    knowledge_retrieval_result = KnowledgeRetrievalResult(
        knowledge_base_snapshot_id="kb-1",
        routing_mode="generic_fallback",
        selected_domain_pack_ids=[],
        routing_confidence=0.3,
        fallback_reason="no_domain_pack_scored_above_threshold",
        retrieved_cards=[],
        negative_signal_terms=["sales"],
    )

    output = generate_grounding_output(
        requirement_sheet,
        knowledge_retrieval_result,
        GroundingDraft(),
    )

    assert [seed.operator_name for seed in output.frontier_seed_specifications[:3]] == [
        "must_have_alias",
        "must_have_alias",
        "strict_core",
    ]
    assert all(seed.operator_name != "domain_company" for seed in output.frontier_seed_specifications)


def test_generate_grounding_output_fails_when_seed_count_is_below_three() -> None:
    requirement_sheet = _requirement_sheet()
    card = GroundingKnowledgeCard(
        card_id="card-1",
        domain_id="llm_agent_rag_engineering",
        report_type="role_family",
        card_type="role_alias",
        title="Agent Engineer",
        summary="Agent role.",
        canonical_terms=["agent engineer"],
        aliases=[],
        positive_signals=[],
        negative_signals=[],
        query_terms=["agent engineer"],
        must_have_links=["Python backend"],
        preferred_links=[],
        confidence="high",
        source_report_ids=["report-1"],
        source_model_votes=1,
        freshness_date="2026-04-07",
    )
    knowledge_retrieval_result = KnowledgeRetrievalResult(
        knowledge_base_snapshot_id="kb-1",
        routing_mode="explicit_domain",
        selected_domain_pack_ids=["llm_agent_rag_engineering"],
        routing_confidence=1.0,
        fallback_reason=None,
        retrieved_cards=[card],
        negative_signal_terms=[],
    )
    grounding_draft = GroundingDraft(
        frontier_seed_specifications=[
            FrontierSeedSpecification(
                operator_name="must_have_alias",
                seed_terms=["agent engineer", "python"],
                seed_rationale="only one",
                source_card_ids=["card-1"],
                expected_coverage=["Python backend"],
                negative_terms=[],
                target_location=None,
            )
        ]
    )

    with pytest.raises(ValueError, match="insufficient_seed_specifications"):
        generate_grounding_output(requirement_sheet, knowledge_retrieval_result, grounding_draft)


def test_initialize_frontier_state_builds_open_seed_nodes() -> None:
    grounding_output = GroundingOutput(
        grounding_evidence_cards=[],
        frontier_seed_specifications=[
            FrontierSeedSpecification(
                operator_name="must_have_alias",
                seed_terms=["agent engineer", "python"],
                seed_rationale="seed one",
                source_card_ids=["card-1"],
                expected_coverage=["Python backend"],
                negative_terms=["frontend"],
                target_location=None,
            ),
            FrontierSeedSpecification(
                operator_name="strict_core",
                seed_terms=["ranking", "search"],
                seed_rationale="seed two",
                source_card_ids=["card-2"],
                expected_coverage=["retrieval or ranking experience"],
                negative_terms=[],
                target_location=None,
            ),
            FrontierSeedSpecification(
                operator_name="domain_company",
                seed_terms=["to-b", "workflow orchestration"],
                seed_rationale="seed three",
                source_card_ids=["card-3"],
                expected_coverage=["to-b delivery"],
                negative_terms=[],
                target_location="Shanghai",
            ),
        ],
    )
    assets = default_bootstrap_assets()

    state = initialize_frontier_state(
        grounding_output,
        assets.runtime_search_budget,
        assets.operator_catalog,
    )

    second_state = initialize_frontier_state(
        grounding_output,
        assets.runtime_search_budget,
        assets.operator_catalog,
    )

    assert list(state.frontier_nodes) == list(second_state.frontier_nodes)
    assert state.open_frontier_node_ids == list(state.frontier_nodes)
    assert state.closed_frontier_node_ids == []
    assert state.remaining_budget == 5
    assert state.run_term_catalog == [
        "agent engineer",
        "python",
        "ranking",
        "search",
        "to-b",
        "workflow orchestration",
    ]
    assert all(node.status == "open" for node in state.frontier_nodes.values())
    assert all(node.parent_frontier_node_id is None for node in state.frontier_nodes.values())
    assert all(node.donor_frontier_node_id is None for node in state.frontier_nodes.values())
    assert all(node.reward_breakdown is None for node in state.frontier_nodes.values())
