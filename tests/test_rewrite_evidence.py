from __future__ import annotations

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
        raw_payload={"expectedJobCategory": "Python Engineer"},
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
                "selected_operator_name": "generic_expansion",
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
