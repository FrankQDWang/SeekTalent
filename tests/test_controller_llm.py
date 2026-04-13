from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel

import seektalent.controller_llm as controller_llm_module
from seektalent.controller_llm import request_search_controller_decision_draft
from seektalent.frontier_ops import generate_search_controller_decision
from seektalent.models import (
    FitGateConstraints,
    RewriteFitnessWeights,
    RewriteTermCandidate,
    RewriteTermScoreBreakdown,
    SearchControllerContext_t,
    search_controller_decision_draft_json_schema,
    validate_search_controller_decision_draft,
)
from seektalent.runtime_budget import build_runtime_budget_state


def _context(
    *,
    node_query_term_pool: list[str] | None = None,
    max_query_terms: int = 4,
    allowed_operator_names: list[str] | None = None,
    rewrite_term_candidates: list[RewriteTermCandidate] | None = None,
) -> SearchControllerContext_t:
    return SearchControllerContext_t.model_validate(
        {
            "role_title": "Senior Python Agent Engineer",
            "role_summary": "Build ranking systems.",
            "active_frontier_node_summary": {
                "frontier_node_id": "seed_agent_core",
                "selected_operator_name": "must_have_alias",
                "node_query_term_pool": (
                    ["agent engineer", "python", "workflow", "backend"]
                    if node_query_term_pool is None
                    else node_query_term_pool
                ),
                "node_shortlist_candidate_ids": ["c1"],
            },
            "donor_candidate_node_summaries": [
                {
                    "frontier_node_id": "child_search_domain_01",
                    "shared_anchor_terms": ["python"],
                    "expected_incremental_coverage": ["ranking"],
                    "reward_score": 2.5,
                }
            ],
            "frontier_head_summary": {
                "open_node_count": 2,
                "remaining_budget": 2,
                "highest_selection_score": 3.4,
            },
            "active_selection_breakdown": {
                "search_phase": "explore",
                "operator_exploitation_score": 0.0,
                "operator_exploration_bonus": 1.48,
                "coverage_opportunity_score": 0.5,
                "incremental_value_score": 0.0,
                "fresh_node_bonus": 1.0,
                "redundancy_penalty": 0.0,
                "final_selection_score": 3.4,
            },
            "selection_ranking": [
                {
                    "frontier_node_id": "seed_agent_core",
                    "selected_operator_name": "must_have_alias",
                    "breakdown": {
                        "search_phase": "explore",
                        "operator_exploitation_score": 0.0,
                        "operator_exploration_bonus": 1.48,
                        "coverage_opportunity_score": 0.5,
                        "incremental_value_score": 0.0,
                        "fresh_node_bonus": 1.0,
                        "redundancy_penalty": 0.0,
                        "final_selection_score": 3.4,
                    },
                }
            ],
            "unmet_requirement_weights": [
                {"capability": "python", "weight": 0.3},
                {"capability": "ranking", "weight": 1.0},
            ],
            "operator_statistics_summary": {
                "must_have_alias": {"average_reward": 0.0, "times_selected": 0}
            },
            "allowed_operator_names": allowed_operator_names
            or [
                "must_have_alias",
                "core_precision",
                "crossover_compose",
            ],
            "operator_surface_override_reason": "none",
            "operator_surface_unmet_must_haves": ["ranking"],
            "rewrite_term_candidates": [
                candidate.model_dump(mode="python")
                for candidate in (rewrite_term_candidates or [])
            ],
            "max_query_terms": max_query_terms,
            "fit_gate_constraints": FitGateConstraints().model_dump(mode="python"),
            "runtime_budget_state": build_runtime_budget_state(
                initial_round_budget=5,
                runtime_round_index=0,
                remaining_budget=2,
            ).model_dump(mode="python"),
        }
    )


def _rewrite_candidate(
    term: str,
    *,
    source_candidate_ids: list[str],
    source_fields: list[str],
    accepted_term_score: float,
    must_have_bonus: float = 0.0,
    anchor_bonus: float = 0.0,
    pack_bonus: float = 0.0,
) -> RewriteTermCandidate:
    return RewriteTermCandidate(
        term=term,
        source_candidate_ids=source_candidate_ids,
        source_fields=source_fields,
        support_count=len(source_candidate_ids),
        accepted_term_score=accepted_term_score,
        score_breakdown=RewriteTermScoreBreakdown(
            support_score=min(3.0, float(len(source_candidate_ids))),
            candidate_quality_score=0.9,
            field_weight_score=1.0 if "title" in source_fields else 0.8,
            must_have_bonus=must_have_bonus,
            anchor_bonus=anchor_bonus,
            pack_bonus=pack_bonus,
            generic_penalty=0.0,
        ),
    )


def test_request_search_controller_decision_draft_records_prompt_surface_audit() -> None:
    draft, audit = asyncio.run(
        request_search_controller_decision_draft(
            _context(),
            rewrite_fitness_weights=RewriteFitnessWeights(),
            model=TestModel(
                custom_output_args={
                    "action": "search_cts",
                    "selected_operator_name": "core_precision",
                    "operator_args": {"query_terms": ["agent engineer", "python"]},
                    "expected_gain_hypothesis": "Tighten the query.",
                }
            ),
        )
    )

    assert draft.selected_operator_name == "core_precision"
    assert audit.output_mode == "NativeOutput(strict=True)"
    assert audit.retries == 0
    assert audit.output_retries == 1
    assert audit.validator_retry_count == 0
    assert audit.model_name == "test"
    assert audit.model_settings_snapshot == {
        "allow_text_output": False,
        "allow_image_output": False,
        "native_output_strict": True,
    }
    assert audit.prompt_surface.surface_id == "search_controller_decision"
    assert audit.prompt_surface.instructions_text
    assert "## 运行预算状态" in audit.prompt_surface.input_text
    assert audit.prompt_surface.sections[-1].title == "决策请求"


def test_request_search_controller_decision_draft_retries_once_for_empty_non_crossover_patch() -> None:
    draft, audit = asyncio.run(
        request_search_controller_decision_draft(
            _context(),
            rewrite_fitness_weights=RewriteFitnessWeights(),
            model=TestModel(
                custom_output_args=[
                    {
                        "action": "search_cts",
                        "selected_operator_name": "core_precision",
                        "operator_args": {"query_terms": ["", " "]},
                        "expected_gain_hypothesis": "Tighten the query.",
                    },
                    {
                        "action": "search_cts",
                        "selected_operator_name": "core_precision",
                        "operator_args": {"query_terms": ["agent engineer", "python"]},
                        "expected_gain_hypothesis": "Tighten the query.",
                    },
                ]
            ),
        )
    )

    assert draft.operator_args.model_dump(mode="python") == {
        "query_terms": ["agent engineer", "python"]
    }
    assert audit.validator_retry_count == 1


def test_request_search_controller_decision_draft_fails_after_single_validator_retry() -> None:
    with pytest.raises(ModelRetry, match="requires materializable non-empty query_terms"):
        asyncio.run(
            request_search_controller_decision_draft(
                _context(),
                rewrite_fitness_weights=RewriteFitnessWeights(),
                model=TestModel(
                    custom_output_args=[
                        {
                            "action": "search_cts",
                            "selected_operator_name": "core_precision",
                            "operator_args": {"query_terms": []},
                            "expected_gain_hypothesis": "Tighten the query.",
                        },
                        {
                            "action": "search_cts",
                            "selected_operator_name": "core_precision",
                            "operator_args": {"query_terms": [""]},
                            "expected_gain_hypothesis": "Tighten the query.",
                        },
                    ]
                ),
            )
        )


def test_search_controller_decision_draft_schema_requires_nested_operator_args_for_search_cts() -> None:
    schema = search_controller_decision_draft_json_schema()
    schema_text = str(schema)

    assert "query_terms" in schema_text
    assert "donor_frontier_node_id" in schema_text
    with pytest.raises(Exception):
        validate_search_controller_decision_draft(
            {
                "action": "search_cts",
                "selected_operator_name": "core_precision",
                "operator_args": {},
                "expected_gain_hypothesis": "Tighten the query.",
            }
        )


def test_request_search_controller_decision_draft_accepts_budget_clamped_non_crossover_query() -> None:
    context = _context(
        node_query_term_pool=["agent engineer", "python", "workflow", "backend"],
        max_query_terms=2,
    )
    draft, audit = asyncio.run(
        request_search_controller_decision_draft(
            context,
            rewrite_fitness_weights=RewriteFitnessWeights(),
            model=TestModel(
                custom_output_args={
                    "action": "search_cts",
                    "selected_operator_name": "core_precision",
                    "operator_args": {"query_terms": ["agent engineer", "python", "workflow"]},
                    "expected_gain_hypothesis": "Keep the current core query intact.",
                }
            ),
        )
    )

    normalized = generate_search_controller_decision(context, draft)

    assert normalized.operator_args == {"query_terms": ["agent engineer", "python"]}
    assert audit.validator_retry_count == 0


def test_request_search_controller_decision_draft_uses_explicit_rewrite_fitness_weights() -> None:
    context = _context(
        node_query_term_pool=["python backend", "workflow", "agent"],
        allowed_operator_names=["vocabulary_bridge", "core_precision", "crossover_compose"],
        rewrite_term_candidates=[
            _rewrite_candidate(
                "ranking",
                source_candidate_ids=["shared-1", "shared-2"],
                source_fields=["title"],
                accepted_term_score=4.8,
                must_have_bonus=1.5,
            ),
            _rewrite_candidate(
                "retrieval",
                source_candidate_ids=["shared-1", "shared-2"],
                source_fields=["project_names"],
                accepted_term_score=4.5,
                anchor_bonus=0.75,
            ),
            _rewrite_candidate(
                "rag",
                source_candidate_ids=["mixed-1"],
                source_fields=["search_text"],
                accepted_term_score=4.9,
                anchor_bonus=0.75,
            ),
        ],
    )
    draft, _audit = asyncio.run(
        request_search_controller_decision_draft(
            context,
            rewrite_fitness_weights=RewriteFitnessWeights(rewrite_coherence=4.0),
            model=TestModel(
                custom_output_args={
                    "action": "search_cts",
                    "selected_operator_name": "vocabulary_bridge",
                    "operator_args": {"query_terms": ["python backend", "ranking", "rag"]},
                    "expected_gain_hypothesis": "Prefer the most coherent rewrite.",
                }
            ),
        )
    )

    normalized = generate_search_controller_decision(
        context,
        draft,
        RewriteFitnessWeights(rewrite_coherence=4.0),
    )

    assert normalized.operator_args == {
        "query_terms": ["python backend", "ranking", "retrieval"]
    }


def test_request_search_controller_decision_draft_surfaces_last_validator_error() -> None:
    context = _context()
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
                    "action": "search_cts",
                    "selected_operator_name": "core_precision",
                    "operator_args": {"query_terms": [""]},
                    "expected_gain_hypothesis": "Tighten the query.",
                },
                {
                    "action": "search_cts",
                    "selected_operator_name": "core_precision",
                    "operator_args": {"query_terms": []},
                    "expected_gain_hypothesis": "Tighten the query.",
                },
            ]:
                draft = validate_search_controller_decision_draft(payload)
                try:
                    assert self._validator is not None
                    self._validator(draft)
                except ModelRetry:
                    continue
            raise UnexpectedModelBehavior("Exceeded maximum retries (1) for output validation")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(controller_llm_module, "Agent", FakeAgent)
    try:
        with pytest.raises(
            RuntimeError,
            match="controller_output_invalid: search_cts requires materializable non-empty query_terms",
        ):
            asyncio.run(
                request_search_controller_decision_draft(
                    context,
                    rewrite_fitness_weights=RewriteFitnessWeights(),
                    model=dummy_model,
                    env_file=None,
                )
            )
    finally:
        monkeypatch.undo()
