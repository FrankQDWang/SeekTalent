from __future__ import annotations

import asyncio
import inspect

from pydantic_ai.models.test import TestModel

from seektalent.bootstrap import bootstrap_round0, bootstrap_round0_async
from seektalent.bootstrap_assets import default_bootstrap_assets


def _requirement_draft_payload(
    *,
    role_title: str,
    role_summary: str,
    must_have: list[str],
    preferred: list[str],
    exclusion: list[str],
    preferred_backgrounds: list[str] | None = None,
) -> dict[str, object]:
    return {
        "role_title_candidate": role_title,
        "role_summary_candidate": role_summary,
        "must_have_capability_candidates": must_have,
        "preferred_capability_candidates": preferred,
        "exclusion_signal_candidates": exclusion,
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": preferred_backgrounds or [],
        },
        "hard_constraint_candidates": {
            "locations": ["Shanghai"],
            "min_years": 6,
            "max_years": 10,
        },
        "scoring_rationale_candidate": "Prioritize core must-have fit.",
    }


def _grounding_draft_payload(*, primary_card_id: str, secondary_card_id: str | None = None) -> dict[str, object]:
    source_card_ids = [primary_card_id]
    second_source_ids = [secondary_card_id] if secondary_card_id is not None else [primary_card_id]
    return {
        "grounding_evidence_cards": [
            {
                "source_card_id": primary_card_id,
                "label": "agent engineer",
                "rationale": "title alias match",
                "evidence_type": "title_alias",
                "confidence": "high",
            }
        ],
        "frontier_seed_specifications": [
            {
                "operator_name": "must_have_alias",
                "seed_terms": ["agent engineer", "rag", "python"],
                "seed_rationale": "cover role anchor",
                "source_card_ids": [primary_card_id],
                "expected_coverage": ["Python backend", "LLM application"],
                "negative_terms": ["frontend"],
                "target_location": None,
            },
            {
                "operator_name": "strict_core",
                "seed_terms": ["retrieval engineer", "ranking", "python"],
                "seed_rationale": "cover retrieval branch",
                "source_card_ids": second_source_ids,
                "expected_coverage": ["retrieval or ranking experience"],
                "negative_terms": [],
                "target_location": None,
            },
            {
                "operator_name": "domain_company",
                "seed_terms": ["workflow orchestration", "to-b", "python"],
                "seed_rationale": "cover business delivery",
                "source_card_ids": [primary_card_id],
                "expected_coverage": ["workflow orchestration"],
                "negative_terms": [],
                "target_location": None,
            },
        ],
    }


def test_bootstrap_round0_async_supports_explicit_domain() -> None:
    assets = default_bootstrap_assets()
    assets = assets.__class__(
        business_policy_pack=assets.business_policy_pack.model_copy(update={"domain_pack_ids": ["llm_agent_rag_engineering"]}),
        knowledge_base_snapshot=assets.knowledge_base_snapshot,
        knowledge_cards=assets.knowledge_cards,
        reranker_calibration=assets.reranker_calibration,
        knowledge_retrieval_budget=assets.knowledge_retrieval_budget,
        runtime_search_budget=assets.runtime_search_budget,
        operator_catalog=assets.operator_catalog,
    )
    primary_card_id = "role_alias.llm_agent_rag_engineering.backend_agent_engineer"

    artifacts = asyncio.run(
        bootstrap_round0_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
            assets=assets,
            requirement_extraction_model=TestModel(
                custom_output_args=_requirement_draft_payload(
                    role_title="Senior Python / LLM Engineer",
                    role_summary="Build Python, LLM, and retrieval systems.",
                    must_have=["Python backend", "LLM application", "retrieval or ranking experience"],
                    preferred=["workflow orchestration"],
                    exclusion=["data analyst"],
                )
            ),
            grounding_generation_model=TestModel(
                custom_output_args=_grounding_draft_payload(primary_card_id=primary_card_id)
            ),
        )
    )

    assert artifacts.knowledge_retrieval_result.routing_mode == "explicit_domain"
    assert artifacts.knowledge_retrieval_result.selected_domain_pack_ids == ["llm_agent_rag_engineering"]
    assert artifacts.requirement_extraction_audit.output_mode == "NativeOutput(strict=True)"
    assert artifacts.requirement_extraction_audit.model_name == "test"
    assert artifacts.requirement_extraction_audit.message_history_mode == "fresh"
    assert artifacts.requirement_extraction_audit.tools_enabled is False
    assert artifacts.requirement_extraction_audit.model_settings_snapshot == {
        "allow_text_output": False,
        "allow_image_output": False,
        "native_output_strict": True,
    }
    assert artifacts.grounding_generation_audit.model_name == "test"
    assert artifacts.scoring_policy.top_n_for_explanation == 5
    assert len(artifacts.grounding_output.frontier_seed_specifications) == 3
    assert len(artifacts.frontier_state.open_frontier_node_ids) == 3


def test_bootstrap_round0_async_supports_inferred_domain() -> None:
    primary_card_id = "role_alias.llm_agent_rag_engineering.backend_agent_engineer"

    artifacts = asyncio.run(
        bootstrap_round0_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
            requirement_extraction_model=TestModel(
                custom_output_args=_requirement_draft_payload(
                    role_title="Senior Python / LLM Engineer",
                    role_summary="Build Python and LLM systems.",
                    must_have=["Python backend", "LLM application"],
                    preferred=["workflow orchestration"],
                    exclusion=["data analyst"],
                )
            ),
            grounding_generation_model=TestModel(
                custom_output_args=_grounding_draft_payload(primary_card_id=primary_card_id)
            ),
        )
    )

    assert artifacts.knowledge_retrieval_result.routing_mode == "inferred_domain"
    assert artifacts.knowledge_retrieval_result.selected_domain_pack_ids == ["llm_agent_rag_engineering"]
    assert artifacts.frontier_state.remaining_budget == 5
    assert artifacts.frontier_state.run_term_catalog[:3] == ["agent engineer", "rag", "python"]


def test_bootstrap_round0_async_supports_generic_fallback() -> None:
    artifacts = asyncio.run(
        bootstrap_round0_async(
            job_description="People Operations Manager",
            hiring_notes="Shanghai preferred",
            requirement_extraction_model=TestModel(
                custom_output_args=_requirement_draft_payload(
                    role_title="People Operations Manager",
                    role_summary="Lead hiring operations and stakeholder management.",
                    must_have=["stakeholder management", "process design", "cross-functional collaboration"],
                    preferred=["hiring operations"],
                    exclusion=["sales"],
                    preferred_backgrounds=[],
                )
            ),
            grounding_generation_model=TestModel(
                custom_output_args={"grounding_evidence_cards": [], "frontier_seed_specifications": []}
            ),
        )
    )

    assert artifacts.knowledge_retrieval_result.routing_mode == "generic_fallback"
    assert artifacts.knowledge_retrieval_result.retrieved_cards == []
    assert len(artifacts.grounding_output.frontier_seed_specifications) == 3
    assert all(
        seed.operator_name in {"must_have_alias", "strict_core"}
        for seed in artifacts.grounding_output.frontier_seed_specifications
    )


def test_bootstrap_round0_sync_wrapper_works_with_test_models() -> None:
    primary_card_id = "role_alias.llm_agent_rag_engineering.backend_agent_engineer"

    artifacts = bootstrap_round0(
        job_description="Senior Python / LLM Engineer",
        hiring_notes="Shanghai preferred",
        requirement_extraction_model=TestModel(
            custom_output_args=_requirement_draft_payload(
                role_title="Senior Python / LLM Engineer",
                role_summary="Build Python and LLM systems.",
                must_have=["Python backend", "LLM application"],
                preferred=["workflow orchestration"],
                exclusion=["data analyst"],
            )
        ),
        grounding_generation_model=TestModel(
            custom_output_args=_grounding_draft_payload(primary_card_id=primary_card_id)
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
