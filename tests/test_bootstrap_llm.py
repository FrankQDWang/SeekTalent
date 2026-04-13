from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

import seektalent.bootstrap_llm as bootstrap_llm_module
from seektalent.bootstrap_llm import (
    request_bootstrap_keyword_draft,
    request_requirement_extraction_draft,
)
from seektalent.models import (
    BootstrapRoutingResult,
    DomainKnowledgePack,
    HardConstraints,
    RequirementPreferences,
    RequirementSheet,
)
from seektalent.requirements import build_input_truth


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
            knowledge_pack_id="finance_risk_control_ai",
            label="金融风控 AI",
            routing_text="面向金融风控与合规场景的 AI 工程，覆盖风控建模、规则策略、审核流程和智能决策支持。",
            include_keywords=["金融风控", "风控策略"],
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
                    "finance_risk_control_ai",
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
    assert "## 已选 Knowledge Packs" in audit.prompt_surface.input_text
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
            "finance_risk_control_ai",
        ],
        routing_confidence=0.7,
        pack_scores={
            "llm_agent_rag_engineering": 0.7,
            "finance_risk_control_ai": 0.68,
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


def test_request_requirement_extraction_draft_retries_after_empty_semantic_fields() -> None:
    draft, audit = asyncio.run(
        request_requirement_extraction_draft(
            build_input_truth(
                job_description="招聘 Senior Agent Engineer\n负责 Agent Runtime 和 tool calling。",
                hiring_notes="",
            ),
            model=TestModel(
                custom_output_args=[
                    {
                        "role_title_candidate": "Senior Agent Engineer",
                        "role_summary_candidate": "负责 Agent Runtime 和 tool calling。",
                        "must_have_capability_candidates": [],
                        "preferred_capability_candidates": [],
                        "exclusion_signal_candidates": [],
                        "preference_candidates": {},
                        "hard_constraint_candidates": {},
                        "scoring_rationale_candidate": "",
                    },
                    {
                        "role_title_candidate": "Senior Agent Engineer",
                        "role_summary_candidate": "负责 Agent Runtime 和 tool calling。",
                        "must_have_capability_candidates": [],
                        "preferred_capability_candidates": [],
                        "exclusion_signal_candidates": [],
                        "preference_candidates": {},
                        "hard_constraint_candidates": {},
                        "scoring_rationale_candidate": "must-have first",
                    },
                ]
            ),
        )
    )

    assert draft.role_title_candidate == "Senior Agent Engineer"
    assert audit.validator_retry_count == 1


def test_request_requirement_extraction_draft_surfaces_last_validator_error() -> None:
    dummy_model = object()

    class FakeAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            self._validator = None

        def output_validator(self, fn):
            self._validator = fn
            return fn

        async def run(self, *_args, **_kwargs):
            for payload in [
                {
                    "role_title_candidate": "Senior Agent Engineer",
                    "role_summary_candidate": "负责 Agent Runtime 和 tool calling。",
                    "must_have_capability_candidates": [],
                    "preferred_capability_candidates": [],
                    "exclusion_signal_candidates": [],
                    "preference_candidates": {},
                    "hard_constraint_candidates": {},
                    "scoring_rationale_candidate": "",
                },
                {
                    "role_title_candidate": "Senior Agent Engineer",
                    "role_summary_candidate": "负责 Agent Runtime 和 tool calling。",
                    "must_have_capability_candidates": [],
                    "preferred_capability_candidates": [],
                    "exclusion_signal_candidates": [],
                    "preference_candidates": {},
                    "hard_constraint_candidates": {},
                    "scoring_rationale_candidate": "",
                },
            ]:
                draft = bootstrap_llm_module.RequirementExtractionDraft.model_validate(payload)
                try:
                    assert self._validator is not None
                    self._validator(draft)
                except ModelRetry:
                    continue
            raise UnexpectedModelBehavior("Exceeded maximum retries (1) for output validation")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bootstrap_llm_module, "Agent", FakeAgent)
    try:
        with pytest.raises(
            RuntimeError,
            match="requirement_extraction_output_invalid: scoring_rationale must not be empty after normalization",
        ):
            asyncio.run(
                request_requirement_extraction_draft(
                    build_input_truth(
                        job_description="招聘 Senior Agent Engineer\n负责 Agent Runtime 和 tool calling。",
                        hiring_notes="",
                    ),
                    model=dummy_model,
                    env_file=None,
                )
            )
    finally:
        monkeypatch.undo()


def test_request_bootstrap_keyword_draft_surfaces_last_validator_error() -> None:
    routing_result = BootstrapRoutingResult(
        routing_mode="generic_fallback",
        selected_knowledge_pack_ids=[],
        routing_confidence=0.3,
        fallback_reason="top1_confidence_below_floor",
        pack_scores={},
    )
    invalid = _valid_generic_payload()
    invalid["candidate_seeds"][3]["source_knowledge_pack_ids"] = ["llm_agent_rag_engineering"]
    dummy_model = object()

    class FakeAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            self._validator = None

        def output_validator(self, fn):
            self._validator = fn
            return fn

        async def run(self, *_args, **_kwargs):
            for payload in [invalid, invalid]:
                draft = bootstrap_llm_module.BootstrapKeywordDraft.model_validate(payload)
                try:
                    assert self._validator is not None
                    self._validator(draft)
                except ModelRetry:
                    continue
            raise UnexpectedModelBehavior("Exceeded maximum retries (1) for output validation")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bootstrap_llm_module, "Agent", FakeAgent)
    try:
        with pytest.raises(
            RuntimeError,
            match="bootstrap_output_invalid: bootstrap seed source_knowledge_pack_ids must be selected packs",
        ):
            asyncio.run(
                request_bootstrap_keyword_draft(
                    _requirement_sheet(),
                    routing_result,
                    [],
                    max_seed_terms=3,
                    model=dummy_model,
                    env_file=None,
                )
            )
    finally:
        monkeypatch.undo()
