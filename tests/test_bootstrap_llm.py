from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from seektalent.bootstrap_llm import request_bootstrap_keyword_draft
from seektalent.models import (
    BootstrapRoutingResult,
    DomainKnowledgePack,
    HardConstraints,
    RequirementPreferences,
    RequirementSheet,
)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python / LLM Engineer",
        role_summary="Build Python, LLM, and retrieval systems.",
        must_have_capabilities=["Python backend", "LLM application"],
        preferred_capabilities=["workflow orchestration"],
        exclusion_signals=["frontend"],
        hard_constraints=HardConstraints(locations=["Shanghai"]),
        preferences=RequirementPreferences(),
        scoring_rationale="must-have first",
    )


def _packs() -> list[DomainKnowledgePack]:
    return [
        DomainKnowledgePack(
            knowledge_pack_id="llm_agent_rag_engineering",
            label="LLM Agent / RAG Engineering",
            routing_text="agent engineer, rag, tool calling",
            include_keywords=["agent engineer", "tool calling"],
            exclude_keywords=["sales"],
        ),
        DomainKnowledgePack(
            knowledge_pack_id="search_ranking_retrieval_engineering",
            label="Search / Ranking / Retrieval",
            routing_text="search ranking retrieval engineer",
            include_keywords=["retrieval engineer", "ranking"],
            exclude_keywords=["crm"],
        ),
    ]


def _valid_single_pack_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["agent engineer", "rag", "python backend"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["llm application", "retrieval pipeline"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "retrieval"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["workflow orchestration", "tool calling"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "use pack hints",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["backend engineer", "agent workflow"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra route",
            },
        ],
        "negative_keywords": ["frontend"],
    }


def _valid_generic_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["operations manager", "process design"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["stakeholder management", "operations"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["operations", "manager"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["team operations", "workflow"],
                "source_knowledge_pack_ids": [],
                "reasoning": "generic expansion",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["process improvement", "hiring operations"],
                "source_knowledge_pack_ids": [],
                "reasoning": "secondary expansion",
            },
        ],
        "negative_keywords": ["sales"],
    }


def _valid_multi_pack_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["agent engineer", "ranking", "python backend"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["retrieval pipeline", "reranker"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "agent"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["workflow orchestration", "tool calling"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "use llm pack",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["agent ranking", "retrieval workflow"],
                "source_knowledge_pack_ids": [
                    "llm_agent_rag_engineering",
                    "search_ranking_retrieval_engineering",
                ],
                "reasoning": "bridge both packs",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["search backend", "precision recall"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra route",
            },
        ],
        "negative_keywords": ["sales"],
    }


def test_request_bootstrap_keyword_draft_retries_after_missing_relaxed_floor() -> None:
    routing_result = BootstrapRoutingResult(
        routing_mode="inferred_single_pack",
        selected_knowledge_pack_ids=["llm_agent_rag_engineering"],
        routing_confidence=0.7,
        pack_scores={"llm_agent_rag_engineering": 0.7},
    )
    invalid = _valid_single_pack_payload()
    invalid["candidate_seeds"] = [
        seed
        for seed in invalid["candidate_seeds"]
        if seed["intent_type"] != "relaxed_floor"
    ]

    draft, audit = asyncio.run(
        request_bootstrap_keyword_draft(
            _requirement_sheet(),
            routing_result,
            _packs()[:1],
            max_seed_terms=3,
            model=TestModel(custom_output_args=[invalid, _valid_single_pack_payload()]),
        )
    )

    assert any(seed.intent_type == "relaxed_floor" for seed in draft.candidate_seeds)
    assert audit.validator_retry_count == 1
    assert audit.prompt_surface.surface_id == "bootstrap_keyword_generation"
    assert audit.prompt_surface.instructions_text
    assert "## Selected Knowledge Packs" in audit.prompt_surface.input_text
    assert len(draft.candidate_seeds[0].keywords) <= 3
    assert audit.prompt_surface.sections[3].body_text.startswith(
        "- llm_agent_rag_engineering | LLM Agent / RAG Engineering"
    )


def test_request_bootstrap_keyword_draft_rejects_generic_pack_reference() -> None:
    routing_result = BootstrapRoutingResult(
        routing_mode="generic_fallback",
        selected_knowledge_pack_ids=[],
        routing_confidence=0.3,
        fallback_reason="top1_confidence_below_floor",
        pack_scores={},
    )
    invalid = _valid_generic_payload()
    invalid["candidate_seeds"][3]["source_knowledge_pack_ids"] = ["llm_agent_rag_engineering"]

    with pytest.raises(ModelRetry):
        asyncio.run(
            request_bootstrap_keyword_draft(
                _requirement_sheet(),
                routing_result,
                [],
                max_seed_terms=3,
                model=TestModel(custom_output_args=invalid),
            )
        )


def test_request_bootstrap_keyword_draft_rejects_multi_pack_bridge_without_two_packs() -> None:
    routing_result = BootstrapRoutingResult(
        routing_mode="inferred_multi_pack",
        selected_knowledge_pack_ids=[
            "llm_agent_rag_engineering",
            "search_ranking_retrieval_engineering",
        ],
        routing_confidence=0.7,
        pack_scores={
            "llm_agent_rag_engineering": 0.7,
            "search_ranking_retrieval_engineering": 0.68,
        },
    )
    invalid = _valid_multi_pack_payload()
    invalid["candidate_seeds"][4]["source_knowledge_pack_ids"] = ["llm_agent_rag_engineering"]

    with pytest.raises(ModelRetry):
        asyncio.run(
            request_bootstrap_keyword_draft(
                _requirement_sheet(),
                routing_result,
                _packs(),
                max_seed_terms=3,
                model=TestModel(custom_output_args=invalid),
            )
        )
