from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.bootstrap_ops import (
    freeze_scoring_policy,
    generate_bootstrap_output,
    initialize_frontier_state,
    route_domain_knowledge_pack,
)
from seektalent.models import (
    BootstrapKeywordDraft,
    BootstrapRoutingResult,
    FitGateConstraints,
    FusionWeightPreferences,
    HardConstraints,
    RequirementPreferences,
    RequirementSheet,
)
from seektalent_rerank.models import RerankResponse, RerankResult


def _requirement_sheet(
    *,
    role_title: str = "Senior Python / LLM Engineer",
    must_have: list[str] | None = None,
    preferred: list[str] | None = None,
    exclusion: list[str] | None = None,
) -> RequirementSheet:
    return RequirementSheet(
        role_title=role_title,
        role_summary="Build Python, LLM, and retrieval systems.",
        must_have_capabilities=must_have or [
            "Python backend",
            "LLM application",
            "retrieval pipeline",
        ],
        preferred_capabilities=preferred or ["workflow orchestration", "tool calling"],
        exclusion_signals=exclusion or ["frontend"],
        preferences=RequirementPreferences(),
        hard_constraints=HardConstraints(locations=["Shanghai"]),
        scoring_rationale="must-have 优先，偏好次之。",
    )


@dataclass
class FakeRerankRequest:
    response: RerankResponse
    seen_requests: list[object] = field(default_factory=list)

    async def __call__(self, request):
        self.seen_requests.append(request)
        return self.response


def _rerank_response(scores: dict[str, float]) -> RerankResponse:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return RerankResponse(
        model="test-reranker",
        results=[
            RerankResult(id=item_id, index=index, score=score, rank=index + 1)
            for index, (item_id, score) in enumerate(ranked)
        ],
    )


def test_route_domain_knowledge_pack_supports_explicit_override() -> None:
    assets = default_bootstrap_assets()
    business_policy = assets.business_policy_pack.model_copy(
        update={"knowledge_pack_id_override": "llm_agent_rag_engineering"}
    )
    rerank = FakeRerankRequest(_rerank_response({}))

    result = asyncio.run(
        route_domain_knowledge_pack(
            _requirement_sheet(),
            business_policy,
            assets.knowledge_packs,
            assets.reranker_calibration,
            rerank_request=rerank,
        )
    )

    assert result.routing_mode == "explicit_domain"
    assert result.selected_knowledge_pack_id == "llm_agent_rag_engineering"
    assert rerank.seen_requests == []


def test_route_domain_knowledge_pack_uses_reranker_top1() -> None:
    assets = default_bootstrap_assets()
    rerank = FakeRerankRequest(
        _rerank_response(
            {
                "llm_agent_rag_engineering": 1.2,
                "search_ranking_retrieval_engineering": 0.2,
                "finance_risk_control_ai": 0.1,
            }
        )
    )

    result = asyncio.run(
        route_domain_knowledge_pack(
            _requirement_sheet(),
            assets.business_policy_pack,
            assets.knowledge_packs,
            assets.reranker_calibration,
            rerank_request=rerank,
        )
    )

    assert result.routing_mode == "inferred_domain"
    assert result.selected_knowledge_pack_id == "llm_agent_rag_engineering"
    assert result.pack_scores["llm_agent_rag_engineering"] > 0.6


def test_route_domain_knowledge_pack_falls_back_when_top1_is_too_low() -> None:
    assets = default_bootstrap_assets()
    rerank = FakeRerankRequest(
        _rerank_response(
            {
                "llm_agent_rag_engineering": 0.2,
                "search_ranking_retrieval_engineering": 0.1,
                "finance_risk_control_ai": 0.0,
            }
        )
    )

    result = asyncio.run(
        route_domain_knowledge_pack(
            _requirement_sheet(role_title="Operations Manager", must_have=["stakeholder management"]),
            assets.business_policy_pack,
            assets.knowledge_packs,
            assets.reranker_calibration,
            rerank_request=rerank,
        )
    )

    assert result.routing_mode == "generic_fallback"
    assert result.selected_knowledge_pack_id is None
    assert result.fallback_reason == "top1_confidence_below_floor"


def test_route_domain_knowledge_pack_falls_back_when_gap_is_too_small() -> None:
    assets = default_bootstrap_assets()
    rerank = FakeRerankRequest(
        _rerank_response(
            {
                "llm_agent_rag_engineering": 0.7,
                "search_ranking_retrieval_engineering": 0.65,
                "finance_risk_control_ai": 0.1,
            }
        )
    )

    result = asyncio.run(
        route_domain_knowledge_pack(
            _requirement_sheet(must_have=["agent engineer", "ranking"]),
            assets.business_policy_pack,
            assets.knowledge_packs,
            assets.reranker_calibration,
            rerank_request=rerank,
        )
    )

    assert result.routing_mode == "generic_fallback"
    assert result.selected_knowledge_pack_id is None
    assert result.fallback_reason == "top1_top2_gap_below_floor"


def test_generate_bootstrap_output_projects_exclude_keywords_into_negative_terms() -> None:
    assets = default_bootstrap_assets()
    llm_pack = next(
        pack
        for pack in assets.knowledge_packs
        if pack.knowledge_pack_id == "llm_agent_rag_engineering"
    )
    output = generate_bootstrap_output(
        _requirement_sheet(),
        BootstrapRoutingResult(
            routing_mode="inferred_domain",
            selected_knowledge_pack_id=llm_pack.knowledge_pack_id,
            routing_confidence=0.61,
            pack_scores={llm_pack.knowledge_pack_id: 0.61},
        ),
        llm_pack,
        BootstrapKeywordDraft(
            core_keywords=["agent engineer", "rag", "python backend"],
            must_have_keywords=["llm application"],
            expansion_keywords=["workflow orchestration", "tool calling"],
            negative_keywords=["prompt operation"],
        ),
    )

    operators = [seed.operator_name for seed in output.frontier_seed_specifications]
    assert operators == ["strict_core", "must_have_alias", "domain_company"]
    assert all(seed.knowledge_pack_id == llm_pack.knowledge_pack_id for seed in output.frontier_seed_specifications)
    assert "frontend" in output.frontier_seed_specifications[0].negative_terms
    assert "prompt operation" in output.frontier_seed_specifications[0].negative_terms
    assert "pure algorithm research" in output.frontier_seed_specifications[0].negative_terms


def test_generate_bootstrap_output_keeps_generic_bootstrap_small() -> None:
    output = generate_bootstrap_output(
        _requirement_sheet(role_title="Operations Manager", must_have=["stakeholder management"]),
        BootstrapRoutingResult(
            routing_mode="generic_fallback",
            selected_knowledge_pack_id=None,
            routing_confidence=0.5,
            fallback_reason="top1_confidence_below_floor",
            pack_scores={},
        ),
        None,
        BootstrapKeywordDraft(
            core_keywords=["process design"],
            must_have_keywords=["stakeholder management"],
            expansion_keywords=["should be ignored"],
            negative_keywords=["sales"],
        ),
    )

    assert [seed.operator_name for seed in output.frontier_seed_specifications] == [
        "strict_core",
        "must_have_alias",
    ]
    assert all(seed.knowledge_pack_id is None for seed in output.frontier_seed_specifications)
    frontier_state = initialize_frontier_state(
        output,
        default_bootstrap_assets().runtime_search_budget,
        default_bootstrap_assets().operator_catalog,
    )
    assert frontier_state.open_frontier_node_ids
    assert all(
        frontier_state.frontier_nodes[node_id].knowledge_pack_id is None
        for node_id in frontier_state.open_frontier_node_ids
    )


def test_freeze_scoring_policy_only_tightens_truth_gate_and_normalizes_weights() -> None:
    assets = default_bootstrap_assets()
    requirement_sheet = _requirement_sheet().model_copy(
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
            )
        }
    )
    business_policy = assets.business_policy_pack.model_copy(
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
            "fusion_weight_preferences": FusionWeightPreferences(
                rerank=0.4,
                must_have=0.3,
                preferred=0.2,
                risk_penalty=0.1,
            ),
        }
    )

    scoring_policy = freeze_scoring_policy(
        requirement_sheet,
        business_policy,
        assets.reranker_calibration,
    )

    assert scoring_policy.fit_gate_constraints.locations == ["上海"]
    assert scoring_policy.fit_gate_constraints.min_years == 5
    assert scoring_policy.fit_gate_constraints.max_years == 10
    assert scoring_policy.fit_gate_constraints.company_names == ["阿里巴巴"]
    assert scoring_policy.fit_gate_constraints.school_names == ["复旦大学"]
    assert scoring_policy.fit_gate_constraints.degree_requirement == "硕士及以上"
    assert scoring_policy.fit_gate_constraints.gender_requirement == "男"
    assert sum(scoring_policy.fusion_weights.model_dump().values()) == pytest.approx(1.0)
