from __future__ import annotations

from pathlib import Path

from seektalent.models import (
    BootstrapRoutingResult,
    DomainKnowledgePack,
    FitGateConstraints,
    FrontierNode_t,
    FrontierState_t1,
    HardConstraints,
    NodeRewardBreakdown_t,
    RequirementPreferences,
    RequirementSheet,
    RewriteTermCandidate,
    RewriteTermScoreBreakdown,
    ScoredCandidate_t,
    SearchControllerContext_t,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchInputTruth,
    SearchObservation,
    SearchPageStatistics,
    SearchScoringResult_t,
    TopThreeStatistics,
)
from seektalent.prompt_surfaces import (
    build_bootstrap_keyword_generation_prompt_surface,
    build_branch_evaluation_prompt_surface,
    build_controller_prompt_surface,
    build_requirement_extraction_prompt_surface,
    build_search_run_finalization_prompt_surface,
)
from seektalent.runtime_budget import build_runtime_budget_state


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Agent Engineer",
        role_summary="Build deterministic ranking systems.",
        must_have_capabilities=["python", "ranking"],
        preferred_capabilities=["workflow"],
        exclusion_signals=["frontend"],
        hard_constraints=HardConstraints(locations=["Shanghai"]),
        preferences=RequirementPreferences(),
        scoring_rationale="must-have first",
    )


def _controller_context(*, near_budget_end: bool = False) -> SearchControllerContext_t:
    budget_state = build_runtime_budget_state(
        initial_round_budget=10,
        runtime_round_index=8 if near_budget_end else 1,
        remaining_budget=2 if near_budget_end else 8,
    )
    return SearchControllerContext_t.model_validate(
        {
            "role_title": "Senior Python Agent Engineer",
            "role_summary": "Build ranking systems.",
            "active_frontier_node_summary": {
                "frontier_node_id": "seed_agent_core",
                "selected_operator_name": "must_have_alias",
                "node_query_term_pool": ["agent engineer", "python", "workflow"],
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
                "remaining_budget": budget_state.remaining_budget,
                "highest_selection_score": 3.4,
            },
            "active_selection_breakdown": {
                "search_phase": budget_state.search_phase,
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
                        "search_phase": budget_state.search_phase,
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
            "allowed_operator_names": [
                "must_have_alias",
                "core_precision",
                "crossover_compose",
            ],
            "operator_surface_override_reason": "none",
            "operator_surface_unmet_must_haves": ["ranking"],
            "max_query_terms": 4,
            "fit_gate_constraints": FitGateConstraints().model_dump(mode="python"),
            "runtime_budget_state": budget_state.model_dump(mode="python"),
        }
    )


def _parent_node() -> FrontierNode_t:
    return FrontierNode_t(
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


def test_requirement_extraction_prompt_surface_uses_fixed_sections() -> None:
    surface = build_requirement_extraction_prompt_surface(
        SearchInputTruth(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="",
            job_description_sha256="a",
            hiring_notes_sha256="b",
        ),
        instructions_text="extract",
    )

    assert [section.title for section in surface.sections] == [
        "Task Contract",
        "Job Description",
        "Hiring Notes",
        "Return Fields",
    ]
    assert surface.sections[2].body_text == "- None"
    assert surface.sections[1].source_paths == ["SearchInputTruth.job_description"]
    assert surface.input_text.startswith("## Task Contract")
    assert "Keep must-haves, preferred signals, exclusions, and hard constraints separated." in surface.sections[0].body_text
    assert "Do not upgrade ambiguous preferences into hard constraints." in surface.sections[0].body_text


def test_bootstrap_keyword_generation_prompt_surface_uses_fixed_sections() -> None:
    surface = build_bootstrap_keyword_generation_prompt_surface(
        _requirement_sheet(),
        BootstrapRoutingResult(
            routing_mode="generic_fallback",
            selected_knowledge_pack_ids=[],
            routing_confidence=0.3,
            fallback_reason="top1_confidence_below_floor",
            pack_scores={},
        ),
        [],
        instructions_text="bootstrap",
    )

    assert [section.title for section in surface.sections] == [
        "Task Contract",
        "Requirement Summary",
        "Routing Result",
        "Selected Knowledge Packs",
        "Return Fields",
    ]
    assert surface.sections[3].body_text == "- None"
    assert "Fallback reason: top1_confidence_below_floor" in surface.input_text


def test_controller_prompt_surface_orders_sections_and_delays_budget_warning() -> None:
    regular_surface = build_controller_prompt_surface(
        _controller_context(),
        instructions_text="controller",
    )
    warned_surface = build_controller_prompt_surface(
        _controller_context(near_budget_end=True),
        instructions_text="controller",
    )

    assert [section.title for section in regular_surface.sections] == [
        "Task Contract",
        "Role Summary",
        "Active Frontier Node",
        "Decision Snapshot",
        "Donor Candidates",
        "Allowed Operators",
        "Rewrite Evidence",
        "Operator Statistics",
        "Fit Gates And Unmet Requirements",
        "Runtime Budget State",
        "Decision Request",
    ]
    assert [section.title for section in warned_surface.sections] == [
        "Task Contract",
        "Role Summary",
        "Active Frontier Node",
        "Decision Snapshot",
        "Donor Candidates",
        "Allowed Operators",
        "Rewrite Evidence",
        "Operator Statistics",
        "Fit Gates And Unmet Requirements",
        "Runtime Budget State",
        "Budget Warning",
        "Decision Request",
    ]
    assert warned_surface.sections[10].source_paths == [
        "SearchControllerContext_t.runtime_budget_state.near_budget_end"
    ]
    assert "Must-have coverage in active query pool: 1/2" in regular_surface.sections[3].body_text
    assert "Legal donor count: 1" in regular_surface.sections[3].body_text
    assert "Operator surface override: none" in regular_surface.sections[5].body_text
    assert "Operator surface unmet must-haves: ranking" in regular_surface.sections[5].body_text
    assert "No rewrite evidence terms." in regular_surface.sections[6].body_text
    assert "CTS keyword terms are conjunctive. More terms tighten the search." in regular_surface.sections[8].body_text
    assert "Max query terms: 4" in regular_surface.sections[8].body_text
    assert "Phase progress:" in regular_surface.sections[9].body_text
    assert "Search phase:" in regular_surface.sections[9].body_text
    assert "selection_ranking" not in regular_surface.input_text


def test_controller_prompt_surface_keeps_rewrite_evidence_compact_but_informative() -> None:
    surface = build_controller_prompt_surface(
        _controller_context().model_copy(
            update={
                "rewrite_term_candidates": [
                    RewriteTermCandidate(
                        term="ranking",
                        source_candidate_ids=["c1", "c2"],
                        source_fields=["title", "project_names"],
                        support_count=2,
                        accepted_term_score=5.2,
                        score_breakdown=RewriteTermScoreBreakdown(
                            support_score=2.0,
                            candidate_quality_score=0.9,
                            field_weight_score=1.0,
                            must_have_bonus=1.5,
                            anchor_bonus=0.0,
                            pack_bonus=0.0,
                            generic_penalty=0.25,
                        ),
                    )
                ]
            }
        ),
        instructions_text="controller",
    )

    rewrite_body = surface.sections[6].body_text
    assert "support_count=2" in rewrite_body
    assert "signal=must_have+generic_penalty" in rewrite_body
    assert "accepted_term_score" not in rewrite_body


def test_branch_evaluation_prompt_surface_orders_sections_and_delays_budget_warning() -> None:
    regular_surface = build_branch_evaluation_prompt_surface(
        _requirement_sheet(),
        _parent_node(),
        _execution_plan(),
        _execution_result(),
        _scoring_result(),
        build_runtime_budget_state(
            initial_round_budget=10,
            runtime_round_index=1,
            remaining_budget=8,
        ),
        instructions_text="branch",
    )
    warned_surface = build_branch_evaluation_prompt_surface(
        _requirement_sheet(),
        _parent_node(),
        _execution_plan(),
        _execution_result(),
        _scoring_result(),
        build_runtime_budget_state(
            initial_round_budget=10,
            runtime_round_index=8,
            remaining_budget=2,
        ),
        instructions_text="branch",
    )

    assert [section.title for section in regular_surface.sections] == [
        "Evaluation Contract",
        "Role Summary",
        "Branch Facts",
        "Search And Scoring Summary",
        "Derived Outcome Signals",
        "Runtime Budget State",
        "Return Fields",
    ]
    assert [section.title for section in warned_surface.sections] == [
        "Evaluation Contract",
        "Role Summary",
        "Branch Facts",
        "Search And Scoring Summary",
        "Derived Outcome Signals",
        "Runtime Budget State",
        "Budget Warning",
        "Return Fields",
    ]
    assert "more conservative about marking the branch as still open" in warned_surface.input_text
    assert "Net new shortlist count: 0" in regular_surface.sections[4].body_text
    assert "Shortage after last page: yes" in regular_surface.sections[4].body_text
    assert "Shortlist fit-pass count: 1/1" in regular_surface.sections[4].body_text
    assert "Phase progress:" in regular_surface.sections[5].body_text
    assert "Search phase:" in regular_surface.sections[5].body_text


def test_search_run_finalization_prompt_surface_uses_fixed_sections() -> None:
    surface = build_search_run_finalization_prompt_surface(
        _requirement_sheet(),
        FrontierState_t1(
            frontier_nodes={},
            open_frontier_node_ids=["seed"],
            closed_frontier_node_ids=["child"],
            run_term_catalog=[],
            run_shortlist_candidate_ids=["c-1"],
            semantic_hashes_seen=[],
            operator_statistics={},
            remaining_budget=0,
        ),
        [],
        "controller_stop",
        instructions_text="finalize",
    )

    assert [section.title for section in surface.sections] == [
        "Task Contract",
        "Role Summary",
        "Run Facts",
        "Final Candidate State",
        "Stop Reason",
        "Return Fields",
    ]
    assert "Search round count: 0" in surface.sections[2].body_text
    assert "Last operator: None" in surface.sections[2].body_text
    assert "Rounds with net-new shortlist gain: 0" in surface.sections[2].body_text
    assert surface.sections[4].body_text == "- controller_stop"
    assert "search_text" not in surface.input_text


def test_prompt_path_regressions_removed_from_llm_modules() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for relpath in [
        "src/seektalent/bootstrap_llm.py",
        "src/seektalent/controller_llm.py",
        "src/seektalent/runtime_llm.py",
    ]:
        text = (repo_root / relpath).read_text(encoding="utf-8")
        assert "sort_keys=True" not in text
        assert "model_dump_json(" not in text
