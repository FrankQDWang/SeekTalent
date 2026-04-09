from __future__ import annotations

import asyncio
from dataclasses import replace
import inspect

import pytest
from pydantic_ai.models.test import TestModel

from seektalent.bootstrap import bootstrap_round0, bootstrap_round0_async
from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent_rerank.models import RerankResponse, RerankResult


def _requirement_draft_payload(
    *,
    role_title: str,
    role_summary: str,
    must_have: list[str],
    preferred: list[str],
    exclusion: list[str],
) -> dict[str, object]:
    return {
        "role_title_candidate": role_title,
        "role_summary_candidate": role_summary,
        "must_have_capability_candidates": must_have,
        "preferred_capability_candidates": preferred,
        "exclusion_signal_candidates": exclusion,
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": [],
        },
        "hard_constraint_candidates": {
            "locations": ["Shanghai"],
            "min_years": 6,
            "max_years": 10,
        },
        "scoring_rationale_candidate": "Prioritize core must-have fit.",
    }


def _bootstrap_keyword_draft_payload(
    *,
    expansion: list[str] | None = None,
    negative: list[str] | None = None,
) -> dict[str, object]:
    return {
        "core_keywords": ["agent engineer", "rag", "python backend"],
        "must_have_keywords": ["llm application", "retrieval pipeline"],
        "expansion_keywords": expansion or ["workflow orchestration", "tool calling"],
        "negative_keywords": negative or ["prompt operation"],
    }


class FakeRerankRequest:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.seen_requests: list[object] = []

    async def __call__(self, request):
        self.seen_requests.append(request)
        ranked = sorted(self.scores.items(), key=lambda item: item[1], reverse=True)
        return RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(id=item_id, index=index, score=score, rank=index + 1)
                for index, (item_id, score) in enumerate(ranked)
            ],
        )


def test_bootstrap_round0_async_supports_explicit_domain_override() -> None:
    assets = replace(
        default_bootstrap_assets(),
        business_policy_pack=default_bootstrap_assets().business_policy_pack.model_copy(
            update={"knowledge_pack_id_override": "llm_agent_rag_engineering"}
        ),
    )
    artifacts = asyncio.run(
        bootstrap_round0_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
            assets=assets,
            requirement_extraction_model=TestModel(
                custom_output_args=_requirement_draft_payload(
                    role_title="Senior Python / LLM Engineer",
                    role_summary="Build Python, LLM, and retrieval systems.",
                    must_have=["Python backend", "LLM application", "retrieval pipeline"],
                    preferred=["workflow orchestration"],
                    exclusion=["frontend"],
                )
            ),
            bootstrap_keyword_generation_model=TestModel(
                custom_output_args=_bootstrap_keyword_draft_payload()
            ),
        )
    )

    assert artifacts.routing_result.routing_mode == "explicit_domain"
    assert artifacts.routing_result.selected_knowledge_pack_id == "llm_agent_rag_engineering"
    assert artifacts.bootstrap_keyword_generation_audit.model_name == "test"
    assert [seed.operator_name for seed in artifacts.bootstrap_output.frontier_seed_specifications] == [
        "strict_core",
        "must_have_alias",
        "domain_company",
    ]


def test_bootstrap_round0_async_requires_rerank_when_no_override() -> None:
    with pytest.raises(ValueError, match="requires rerank_request"):
        asyncio.run(
            bootstrap_round0_async(
                job_description="Senior Python / LLM Engineer",
                hiring_notes="Shanghai preferred",
                requirement_extraction_model=TestModel(
                    custom_output_args=_requirement_draft_payload(
                        role_title="Senior Python / LLM Engineer",
                        role_summary="Build Python and LLM systems.",
                        must_have=["Python backend", "LLM application"],
                        preferred=["workflow orchestration"],
                        exclusion=["frontend"],
                    )
                ),
                bootstrap_keyword_generation_model=TestModel(
                    custom_output_args=_bootstrap_keyword_draft_payload()
                ),
            )
        )


def test_bootstrap_round0_async_supports_inferred_domain() -> None:
    rerank = FakeRerankRequest(
        {
            "llm_agent_rag_engineering": 1.2,
            "search_ranking_retrieval_engineering": 0.2,
            "finance_risk_control_ai": 0.1,
        }
    )
    artifacts = asyncio.run(
        bootstrap_round0_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
            rerank_request=rerank,
            requirement_extraction_model=TestModel(
                custom_output_args=_requirement_draft_payload(
                    role_title="Senior Python / LLM Engineer",
                    role_summary="Build Python and LLM systems.",
                    must_have=["Python backend", "LLM application"],
                    preferred=["workflow orchestration"],
                    exclusion=["frontend"],
                )
            ),
            bootstrap_keyword_generation_model=TestModel(
                custom_output_args=_bootstrap_keyword_draft_payload()
            ),
        )
    )

    assert artifacts.routing_result.routing_mode == "inferred_domain"
    assert rerank.seen_requests
    assert artifacts.bootstrap_output.frontier_seed_specifications[0].knowledge_pack_id == "llm_agent_rag_engineering"
    assert artifacts.frontier_state.remaining_budget == 5


def test_bootstrap_round0_async_supports_generic_fallback() -> None:
    artifacts = asyncio.run(
        bootstrap_round0_async(
            job_description="People Operations Manager",
            hiring_notes="Shanghai preferred",
            rerank_request=FakeRerankRequest(
                {
                    "llm_agent_rag_engineering": 0.2,
                    "search_ranking_retrieval_engineering": 0.1,
                    "finance_risk_control_ai": 0.0,
                }
            ),
            requirement_extraction_model=TestModel(
                custom_output_args=_requirement_draft_payload(
                    role_title="People Operations Manager",
                    role_summary="Lead hiring operations and stakeholder management.",
                    must_have=["stakeholder management", "process design"],
                    preferred=["hiring operations"],
                    exclusion=["sales"],
                )
            ),
            bootstrap_keyword_generation_model=TestModel(
                custom_output_args=_bootstrap_keyword_draft_payload(
                    expansion=["should be ignored"],
                    negative=["sales"],
                )
            ),
        )
    )

    assert artifacts.routing_result.routing_mode == "generic_fallback"
    assert artifacts.routing_result.selected_knowledge_pack_id is None
    assert [seed.operator_name for seed in artifacts.bootstrap_output.frontier_seed_specifications] == [
        "strict_core",
        "must_have_alias",
    ]
    assert all(seed.knowledge_pack_id is None for seed in artifacts.bootstrap_output.frontier_seed_specifications)


def test_bootstrap_round0_sync_wrapper_works_with_test_models() -> None:
    artifacts = bootstrap_round0(
        job_description="Senior Python / LLM Engineer",
        hiring_notes="Shanghai preferred",
        rerank_request=FakeRerankRequest(
            {
                "llm_agent_rag_engineering": 1.2,
                "search_ranking_retrieval_engineering": 0.2,
                "finance_risk_control_ai": 0.1,
            }
        ),
        requirement_extraction_model=TestModel(
            custom_output_args=_requirement_draft_payload(
                role_title="Senior Python / LLM Engineer",
                role_summary="Build Python and LLM systems.",
                must_have=["Python backend", "LLM application"],
                preferred=["workflow orchestration"],
                exclusion=["frontend"],
            )
        ),
        bootstrap_keyword_generation_model=TestModel(
            custom_output_args=_bootstrap_keyword_draft_payload()
        ),
    )

    assert artifacts.requirement_sheet.role_title == "Senior Python / LLM Engineer"
    assert artifacts.frontier_state.open_frontier_node_ids


def test_bootstrap_public_api_no_longer_accepts_agent_injection() -> None:
    async_params = inspect.signature(bootstrap_round0_async).parameters
    sync_params = inspect.signature(bootstrap_round0).parameters

    assert "requirement_extraction_agent" not in async_params
    assert "grounding_generation_agent" not in async_params
    assert "requirement_extraction_agent" not in sync_params
    assert "grounding_generation_agent" not in sync_params
