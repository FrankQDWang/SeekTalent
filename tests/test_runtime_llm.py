from __future__ import annotations

import asyncio

from pydantic_ai.models.test import TestModel

from seektalent.models import (
    BranchEvaluationDraft_t,
    FrontierNode_t,
    FrontierState_t,
    FrontierState_t1,
    HardConstraints,
    NodeRewardBreakdown_t,
    RequirementPreferences,
    RequirementSheet,
    ScoredCandidate_t,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchObservation,
    SearchPageStatistics,
    SearchRunSummaryDraft_t,
    SearchScoringResult_t,
    TopThreeStatistics,
)
from seektalent.runtime_budget import build_runtime_budget_state
from seektalent.runtime_llm import (
    request_branch_evaluation_draft,
    request_search_run_summary_draft,
)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Agent Engineer",
        role_summary="Build deterministic ranking systems.",
        must_have_capabilities=["python", "ranking"],
        preferred_capabilities=["workflow"],
        exclusion_signals=[],
        hard_constraints=HardConstraints(locations=["上海"]),
        preferences=RequirementPreferences(),
        scoring_rationale="must-have first",
    )


def _frontier_state() -> FrontierState_t:
    return FrontierState_t(
        frontier_nodes={
            "seed": FrontierNode_t(
                frontier_node_id="seed",
                selected_operator_name="must_have_alias",
                node_query_term_pool=["python"],
                knowledge_pack_ids=["llm_agent_rag_engineering"],
                node_shortlist_candidate_ids=["c-1"],
                node_shortlist_score_snapshot={"c-1": 0.7},
                reward_breakdown=NodeRewardBreakdown_t(
                    delta_top_three=0.2,
                    must_have_gain=0.8,
                    new_fit_yield=1.0,
                    novelty=0.9,
                    usefulness=0.9,
                    diversity=1.0,
                    stability_risk_penalty=0.0,
                    hard_constraint_violation=0.0,
                    duplicate_penalty=0.0,
                    cost_penalty=0.15,
                    reward_score=2.5,
                ),
                status="open",
            )
        },
        open_frontier_node_ids=["seed"],
        closed_frontier_node_ids=[],
        run_term_catalog=["python"],
        run_shortlist_candidate_ids=["c-1"],
        semantic_hashes_seen=[],
        operator_statistics={
            "must_have_alias": {"average_reward": 0.0, "times_selected": 0},
            "core_precision": {"average_reward": 0.0, "times_selected": 0},
            "pack_bridge": {"average_reward": 0.0, "times_selected": 0},
            "crossover_compose": {"average_reward": 0.0, "times_selected": 0},
        },
        remaining_budget=4,
    )


def _execution_plan() -> SearchExecutionPlan_t:
    return SearchExecutionPlan_t.model_validate(
        {
            "query_terms": ["python", "ranking"],
            "projected_filters": {},
            "runtime_only_constraints": {
                "must_have_keywords": ["python", "ranking"],
                "negative_keywords": ["frontend"],
            },
            "target_new_candidate_count": 10,
            "semantic_hash": "hash-1",
            "knowledge_pack_ids": ["llm_agent_rag_engineering"],
            "child_frontier_node_stub": {
                "frontier_node_id": "child_seed_hash1",
                "parent_frontier_node_id": "seed",
                "donor_frontier_node_id": None,
                "selected_operator_name": "core_precision",
            },
        }
    )


def _execution_result() -> SearchExecutionResult_t:
    return SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[],
        scoring_candidates=[],
        search_page_statistics=SearchPageStatistics(
            pages_fetched=1,
            duplicate_rate=0.0,
            latency_ms=5,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=["c-1"],
            shortage_after_last_page=True,
        ),
    )


def _scoring_result() -> SearchScoringResult_t:
    return SearchScoringResult_t(
        scored_candidates=[
            ScoredCandidate_t(
                candidate_id="c-1",
                fit=1,
                rerank_raw=1.0,
                rerank_normalized=0.7,
                must_have_match_score_raw=100,
                must_have_match_score=1.0,
                preferred_match_score_raw=0,
                preferred_match_score=0.0,
                risk_score_raw=0,
                risk_score=0.0,
                risk_flags=[],
                fusion_score=0.75,
            )
        ],
        node_shortlist_candidate_ids=["c-1"],
        explanation_candidate_ids=["c-1"],
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=0.75),
    )


def _runtime_budget_state(*, initial_round_budget: int = 10, runtime_round_index: int = 0, remaining_budget: int = 4):
    return build_runtime_budget_state(
        initial_round_budget=initial_round_budget,
        runtime_round_index=runtime_round_index,
        remaining_budget=remaining_budget,
    )


def test_request_branch_evaluation_draft_records_prompt_surface_audit() -> None:
    draft, audit = asyncio.run(
        request_branch_evaluation_draft(
            _requirement_sheet(),
            _frontier_state(),
            _execution_plan(),
            _execution_result(),
            _scoring_result(),
            _runtime_budget_state(),
            model=TestModel(
                custom_output_args={
                    "novelty_score": 0.4,
                    "usefulness_score": 0.6,
                    "branch_exhausted": False,
                    "repair_operator_hint": "core_precision",
                    "evaluation_notes": "Useful expansion.",
                }
            ),
        )
    )

    assert draft == BranchEvaluationDraft_t(
        novelty_score=0.4,
        usefulness_score=0.6,
        branch_exhausted=False,
        repair_operator_hint="core_precision",
        evaluation_notes="Useful expansion.",
    )
    assert audit.output_mode == "NativeOutput(strict=True)"
    assert audit.retries == 0
    assert audit.output_retries == 1
    assert audit.validator_retry_count == 0
    assert audit.model_name == "test"
    assert audit.prompt_surface.surface_id == "branch_outcome_evaluation"
    assert audit.prompt_surface.instructions_text
    assert "## Return Fields" in audit.prompt_surface.input_text


def test_request_branch_evaluation_draft_adds_budget_warning_near_budget_end() -> None:
    _, audit = asyncio.run(
        request_branch_evaluation_draft(
            _requirement_sheet(),
            _frontier_state(),
            _execution_plan(),
            _execution_result(),
            _scoring_result(),
            _runtime_budget_state(runtime_round_index=8, remaining_budget=2),
            model=TestModel(
                custom_output_args={
                    "novelty_score": 0.4,
                    "usefulness_score": 0.6,
                    "branch_exhausted": False,
                    "repair_operator_hint": "core_precision",
                    "evaluation_notes": "Useful expansion.",
                }
            ),
        )
    )

    assert [section.title for section in audit.prompt_surface.sections][-2:] == [
        "Budget Warning",
        "Return Fields",
    ]


def test_request_search_run_summary_draft_records_prompt_surface_audit() -> None:
    draft, audit = asyncio.run(
        request_search_run_summary_draft(
            _requirement_sheet(),
            FrontierState_t1.model_validate(_frontier_state().model_dump(mode="python")),
            [],
            "controller_stop",
            model=TestModel(
                custom_output_args={
                    "run_summary": "The shortlist is ready for review.",
                }
            ),
        )
    )

    assert draft == SearchRunSummaryDraft_t(
        run_summary="The shortlist is ready for review."
    )
    assert audit.output_mode == "NativeOutput(strict=True)"
    assert audit.retries == 0
    assert audit.output_retries == 1
    assert audit.validator_retry_count == 0
    assert audit.model_name == "test"
    assert audit.prompt_surface.surface_id == "search_run_finalization"
    assert audit.prompt_surface.sections[2].title == "Run Facts"
    assert audit.prompt_surface.sections[-1].title == "Return Fields"
