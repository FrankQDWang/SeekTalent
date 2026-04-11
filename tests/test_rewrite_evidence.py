from __future__ import annotations

from seektalent import rewrite_evidence
from seektalent.models import (
    HardConstraints,
    RequirementPreferences,
    RequirementSheet,
    RetrievedCandidate_t,
    ScoredCandidate_t,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchObservation,
    SearchPageStatistics,
    SearchScoringResult_t,
    TopThreeStatistics,
)
from seektalent.rewrite_evidence import build_rewrite_term_pool


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Agent Engineer",
        role_summary="Build retrieval and ranking systems.",
        must_have_capabilities=["python backend", "ranking"],
        preferred_capabilities=["workflow"],
        exclusion_signals=[],
        hard_constraints=HardConstraints(),
        preferences=RequirementPreferences(),
        scoring_rationale="must-have first",
    )


def _candidate(
    candidate_id: str,
    *,
    project_names: list[str],
    work_summaries: list[str],
    work_experience_summaries: list[str] | None = None,
    search_text: str = "",
    title: str = "Python Engineer",
) -> RetrievedCandidate_t:
    return RetrievedCandidate_t(
        candidate_id=candidate_id,
        now_location="上海",
        expected_location="上海",
        years_of_experience_raw=6,
        education_summaries=[],
        work_experience_summaries=work_experience_summaries or [],
        project_names=project_names,
        work_summaries=work_summaries,
        search_text=search_text or " ".join(project_names + work_summaries),
        raw_payload={"expectedJobCategory": title},
    )


def _scored(candidate_id: str, *, fit: int, fusion_score: float) -> ScoredCandidate_t:
    return ScoredCandidate_t(
        candidate_id=candidate_id,
        fit=fit,
        rerank_raw=1.0,
        rerank_normalized=0.8,
        must_have_match_score_raw=100 if fit else 0,
        must_have_match_score=1.0 if fit else 0.0,
        preferred_match_score_raw=0,
        preferred_match_score=0.0,
        risk_score_raw=0,
        risk_score=0.0,
        risk_flags=[],
        fusion_score=fusion_score,
    )


def test_build_rewrite_term_pool_extracts_supported_terms_and_filters_junk() -> None:
    execution_result = SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[
            _candidate(
                "c-1",
                project_names=["RAG platform"],
                work_summaries=["ranking", "负责优化", "DeepSpeed"],
                search_text="RAG ranking DeepSpeed",
            ),
            _candidate(
                "c-2",
                project_names=["RAG platform"],
                work_summaries=["ranking", "推进落地", "DeepSpeed"],
                search_text="RAG ranking DeepSpeed",
            ),
            _candidate(
                "c-3",
                project_names=["React dashboard"],
                work_summaries=["frontend"],
                search_text="React frontend",
            ),
        ],
        scoring_candidates=[],
        search_page_statistics=SearchPageStatistics(
            pages_fetched=1,
            duplicate_rate=0.0,
            latency_ms=5,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=["c-1", "c-2", "c-3"],
            shortage_after_last_page=False,
        ),
    )
    scoring_result = SearchScoringResult_t(
        scored_candidates=[
            _scored("c-1", fit=1, fusion_score=0.95),
            _scored("c-2", fit=1, fusion_score=0.85),
            _scored("c-3", fit=0, fusion_score=0.70),
        ],
        node_shortlist_candidate_ids=["c-1", "c-2"],
        explanation_candidate_ids=["c-1"],
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=0.83),
    )
    plan = SearchExecutionPlan_t.model_validate(
        {
            "query_terms": ["python backend"],
            "projected_filters": {},
            "runtime_only_constraints": {
                "must_have_keywords": ["python backend", "ranking"],
                "negative_keywords": [],
            },
            "target_new_candidate_count": 10,
            "semantic_hash": "hash",
            "knowledge_pack_ids": ["llm_agent_rag_engineering"],
            "child_frontier_node_stub": {
                "frontier_node_id": "child",
                "parent_frontier_node_id": "seed",
                "selected_operator_name": "vocabulary_bridge",
            },
        }
    )

    pool = build_rewrite_term_pool(
        _requirement_sheet(),
        plan,
        execution_result,
        scoring_result,
    )

    assert [candidate.term for candidate in pool.accepted] == ["ranking", "RAG", "RAG platform"]
    assert pool.accepted[0].support_count == 2
    assert pool.accepted[0].accepted_term_score > pool.accepted[1].accepted_term_score
    assert pool.accepted[0].score_breakdown.must_have_bonus == 1.5
    assert pool.accepted[1].score_breakdown.pack_bonus == 0.5
    assert any(item.term == "负责优化" and item.reason == "generic_junk" for item in pool.rejected)
    assert any(item.term == "DeepSpeed" and item.reason == "topic_drift" for item in pool.rejected)
    assert any(item.term == "React" for item in pool.rejected) is False


def test_build_rewrite_term_pool_keeps_short_skill_repairs_out_of_existing_substrings() -> None:
    requirement_sheet = _requirement_sheet().model_copy(update={"must_have_capabilities": ["Go"]})
    execution_result = SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[
            _candidate(
                "c-go",
                project_names=["Go service"],
                work_summaries=["Go", "backend"],
                search_text="Go backend service",
            )
        ],
        scoring_candidates=[],
        search_page_statistics=SearchPageStatistics(
            pages_fetched=1,
            duplicate_rate=0.0,
            latency_ms=5,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=["c-go"],
            shortage_after_last_page=False,
        ),
    )
    scoring_result = SearchScoringResult_t(
        scored_candidates=[_scored("c-go", fit=1, fusion_score=0.95)],
        node_shortlist_candidate_ids=["c-go"],
        explanation_candidate_ids=["c-go"],
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=0.95),
    )
    plan = SearchExecutionPlan_t.model_validate(
        {
            "query_terms": ["MongoDB"],
            "projected_filters": {},
            "runtime_only_constraints": {
                "must_have_keywords": ["Go"],
                "negative_keywords": [],
            },
            "target_new_candidate_count": 10,
            "semantic_hash": "hash-go",
            "knowledge_pack_ids": [],
            "child_frontier_node_stub": {
                "frontier_node_id": "child-go",
                "parent_frontier_node_id": "seed",
                "selected_operator_name": "must_have_alias",
            },
        }
    )

    pool = build_rewrite_term_pool(
        requirement_sheet,
        plan,
        execution_result,
        scoring_result,
    )

    assert "Go" in [candidate.term for candidate in pool.accepted]
    assert any(item.term == "Go" and item.reason == "already_in_query" for item in pool.rejected) is False


def test_build_rewrite_term_pool_keeps_high_signal_single_source_title_term() -> None:
    execution_result = SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[
            _candidate(
                "c-title",
                title="Ranking Engineer",
                project_names=[],
                work_summaries=["delivery"],
                search_text="ranking delivery",
            )
        ],
        scoring_candidates=[],
        search_page_statistics=SearchPageStatistics(
            pages_fetched=1,
            duplicate_rate=0.0,
            latency_ms=5,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=["c-title"],
            shortage_after_last_page=False,
        ),
    )
    scoring_result = SearchScoringResult_t(
        scored_candidates=[_scored("c-title", fit=1, fusion_score=0.95)],
        node_shortlist_candidate_ids=["c-title"],
        explanation_candidate_ids=["c-title"],
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=0.95),
    )
    plan = SearchExecutionPlan_t.model_validate(
        {
            "query_terms": ["python backend"],
            "projected_filters": {},
            "runtime_only_constraints": {
                "must_have_keywords": ["python backend", "ranking"],
                "negative_keywords": [],
            },
            "target_new_candidate_count": 10,
            "semantic_hash": "hash-title",
            "knowledge_pack_ids": [],
            "child_frontier_node_stub": {
                "frontier_node_id": "child-title",
                "parent_frontier_node_id": "seed",
                "selected_operator_name": "must_have_alias",
            },
        }
    )

    pool = build_rewrite_term_pool(
        _requirement_sheet(),
        plan,
        execution_result,
        scoring_result,
    )

    ranking_engineer = next(
        candidate for candidate in pool.accepted if candidate.term == "Ranking Engineer"
    )
    assert ranking_engineer.support_count == 1
    assert ranking_engineer.score_breakdown.field_weight_score == 1.0
    assert ranking_engineer.score_breakdown.candidate_quality_score == 0.95


def test_single_source_work_summary_term_without_must_have_or_pack_signal_is_not_allowed() -> None:
    assert (
        rewrite_evidence._allow_single_source_term(
            "backend orchestration",
            source_fields=["work_summaries"],
            source_candidate_ids=["c-1"],
            fusion_score_lookup={"c-1": 0.95},
            pack_terms=[],
        )
        is False
    )


def test_build_rewrite_term_pool_prefers_discriminative_terms_over_weak_frequent_terms() -> None:
    requirement_sheet = _requirement_sheet().model_copy(update={"must_have_capabilities": ["ranking"]})
    execution_result = SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[
            _candidate(
                "c-1",
                title="Ranking Specialist",
                project_names=["RAG platform"],
                work_summaries=["RAG ops"],
                search_text="RAG ops",
            ),
            _candidate(
                "c-2",
                title="Python Engineer",
                project_names=["RAG platform"],
                work_summaries=["RAG ops"],
                search_text="RAG ops",
            ),
        ],
        scoring_candidates=[],
        search_page_statistics=SearchPageStatistics(
            pages_fetched=1,
            duplicate_rate=0.0,
            latency_ms=5,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=["c-1", "c-2"],
            shortage_after_last_page=False,
        ),
    )
    scoring_result = SearchScoringResult_t(
        scored_candidates=[
            _scored("c-1", fit=1, fusion_score=0.95),
            _scored("c-2", fit=1, fusion_score=0.80),
        ],
        node_shortlist_candidate_ids=["c-1", "c-2"],
        explanation_candidate_ids=["c-1"],
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=0.875),
    )
    plan = SearchExecutionPlan_t.model_validate(
        {
            "query_terms": ["python backend"],
            "projected_filters": {},
            "runtime_only_constraints": {
                "must_have_keywords": ["ranking"],
                "negative_keywords": [],
            },
            "target_new_candidate_count": 10,
            "semantic_hash": "hash-discriminative",
            "knowledge_pack_ids": ["llm_agent_rag_engineering"],
            "child_frontier_node_stub": {
                "frontier_node_id": "child-discriminative",
                "parent_frontier_node_id": "seed",
                "selected_operator_name": "vocabulary_bridge",
            },
        }
    )

    pool = build_rewrite_term_pool(
        requirement_sheet,
        plan,
        execution_result,
        scoring_result,
    )

    assert [candidate.term for candidate in pool.accepted[:2]] == [
        "Ranking",
        "Ranking Specialist",
    ]
    assert pool.accepted[1].accepted_term_score > pool.accepted[2].accepted_term_score


def test_build_rewrite_term_pool_applies_generic_penalty_to_mixed_terms() -> None:
    execution_result = SearchExecutionResult_t(
        raw_candidates=[],
        deduplicated_candidates=[
            _candidate(
                "c-1",
                title="Ranking 优化",
                project_names=[],
                work_summaries=[],
                search_text="Ranking 优化",
            ),
            _candidate(
                "c-2",
                title="Ranking 优化",
                project_names=[],
                work_summaries=[],
                search_text="Ranking 优化",
            ),
        ],
        scoring_candidates=[],
        search_page_statistics=SearchPageStatistics(
            pages_fetched=1,
            duplicate_rate=0.0,
            latency_ms=5,
        ),
        search_observation=SearchObservation(
            unique_candidate_ids=["c-1", "c-2"],
            shortage_after_last_page=False,
        ),
    )
    scoring_result = SearchScoringResult_t(
        scored_candidates=[
            _scored("c-1", fit=1, fusion_score=0.90),
            _scored("c-2", fit=1, fusion_score=0.88),
        ],
        node_shortlist_candidate_ids=["c-1", "c-2"],
        explanation_candidate_ids=["c-1"],
        top_three_statistics=TopThreeStatistics(average_fusion_score_top_three=0.89),
    )
    plan = SearchExecutionPlan_t.model_validate(
        {
            "query_terms": ["python backend"],
            "projected_filters": {},
            "runtime_only_constraints": {
                "must_have_keywords": ["ranking"],
                "negative_keywords": [],
            },
            "target_new_candidate_count": 10,
            "semantic_hash": "hash-generic-penalty",
            "knowledge_pack_ids": [],
            "child_frontier_node_stub": {
                "frontier_node_id": "child-generic-penalty",
                "parent_frontier_node_id": "seed",
                "selected_operator_name": "must_have_alias",
            },
        }
    )

    pool = build_rewrite_term_pool(
        _requirement_sheet(),
        plan,
        execution_result,
        scoring_result,
    )

    mixed_term = next(candidate for candidate in pool.accepted if candidate.term == "Ranking 优化")
    assert mixed_term.score_breakdown.generic_penalty == 0.25


def test_rewrite_evidence_field_weights_follow_expected_priority() -> None:
    assert rewrite_evidence._field_weight("title") > rewrite_evidence._field_weight("project_names")
    assert rewrite_evidence._field_weight("project_names") > rewrite_evidence._field_weight("work_summaries")
    assert rewrite_evidence._field_weight("work_summaries") > rewrite_evidence._field_weight(
        "work_experience_summaries"
    )
    assert rewrite_evidence._field_weight("work_experience_summaries") > rewrite_evidence._field_weight(
        "search_text"
    )


def test_rewrite_evidence_support_score_is_capped_at_three() -> None:
    breakdown = rewrite_evidence._accepted_term_score_breakdown(
        "ranking",
        source_fields=["title"],
        source_candidate_ids=["c-1", "c-2", "c-3", "c-4"],
        fusion_score_lookup={"c-1": 0.9, "c-2": 0.9, "c-3": 0.9, "c-4": 0.9},
        current_query_terms=["python backend"],
        unmet_must_haves=["ranking"],
        pack_terms=[],
    )

    assert breakdown.support_score == 3.0
