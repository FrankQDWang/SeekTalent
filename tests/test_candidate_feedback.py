from __future__ import annotations

import asyncio

from seektalent.candidate_feedback import (
    build_feedback_decision,
    extract_surface_terms,
    select_feedback_seed_resumes,
)
from seektalent.candidate_feedback.model_steps import CandidateFeedbackModelSteps
from seektalent.candidate_feedback.models import CandidateFeedbackModelRanking, FeedbackCandidateTerm
from seektalent.models import (
    FitBucket,
    QueryRetrievalRole,
    QueryTermCandidate,
    QueryTermCategory,
    QueryTermSource,
    Queryability,
    ScoredCandidate,
)
from tests.settings_factory import make_settings


def _scored_candidate(
    resume_id: str,
    *,
    fit_bucket: FitBucket = "fit",
    overall_score: int = 80,
    must_have_match_score: int = 70,
    risk_score: int = 20,
    reasoning_summary: str = "Seed summary.",
    evidence: list[str] | None = None,
    strengths: list[str] | None = None,
    weaknesses: list[str] | None = None,
    negative_signals: list[str] | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket=fit_bucket,
        overall_score=overall_score,
        must_have_match_score=must_have_match_score,
        preferred_match_score=65,
        risk_score=risk_score,
        risk_flags=[],
        reasoning_summary=reasoning_summary,
        evidence=evidence or [],
        confidence="high",
        matched_must_haves=[],
        missing_must_haves=[],
        matched_preferences=[],
        negative_signals=negative_signals or [],
        strengths=strengths or [],
        weaknesses=weaknesses or [],
        source_round=1,
    )


def _query_term(
    term: str,
    *,
    source: QueryTermSource = "jd",
    category: QueryTermCategory = "domain",
    retrieval_role: QueryRetrievalRole = "domain_context",
    queryability: Queryability = "admitted",
    active: bool = True,
    family: str | None = None,
) -> QueryTermCandidate:
    return QueryTermCandidate(
        term=term,
        source=source,
        category=category,
        priority=1,
        evidence="Seed evidence.",
        first_added_round=1,
        active=active,
        retrieval_role=retrieval_role,
        queryability=queryability,
        family=family or f"feedback.{term.casefold().replace(' ', '').replace('.', '')}",
    )


def test_select_feedback_seed_resumes_selects_only_strict_fit_seeds() -> None:
    selected = select_feedback_seed_resumes(
        [
            _scored_candidate("weak-fit", overall_score=74, must_have_match_score=80, risk_score=20),
            _scored_candidate("best-fit", overall_score=91, must_have_match_score=88, risk_score=18),
            _scored_candidate("mid-fit", overall_score=84, must_have_match_score=77, risk_score=45),
            _scored_candidate("too-risky", overall_score=88, must_have_match_score=80, risk_score=46),
            _scored_candidate("not-fit", fit_bucket="not_fit", overall_score=99, must_have_match_score=99, risk_score=1),
            _scored_candidate("lower-fit", overall_score=83, must_have_match_score=70, risk_score=20),
        ],
        limit=2,
    )

    assert [item.resume_id for item in selected] == ["best-fit", "mid-fit"]


def test_select_feedback_seed_resumes_never_returns_more_than_five() -> None:
    selected = select_feedback_seed_resumes(
        [_scored_candidate(f"fit-{index}", overall_score=90 - index) for index in range(6)],
        limit=10,
    )

    assert [item.resume_id for item in selected] == ["fit-0", "fit-1", "fit-2", "fit-3", "fit-4"]


def test_extract_surface_terms_preserves_technical_and_mixed_shapes() -> None:
    terms = extract_surface_terms(
        [
            "LangGraph, RAG, tool calling, C++, Node.js, Flink CDC, 实时数仓, 任务编排, 平台, 系统, 开发",
            "We use LangGraph for tool calling in Node.js.",
        ]
    )

    for term in ["LangGraph", "RAG", "tool calling", "C++", "Node.js", "Flink CDC", "实时数仓", "任务编排"]:
        assert term in terms
    for term in ["平台", "系统", "开发"]:
        assert term not in terms


def test_build_feedback_decision_picks_one_supported_novel_term() -> None:
    seed_resumes = [
        _scored_candidate(
            "seed-1",
            overall_score=90,
            must_have_match_score=82,
            evidence=["LangGraph", "RAG"],
        ),
        _scored_candidate(
            "seed-2",
            overall_score=86,
            must_have_match_score=79,
            evidence=["LangGraph", "RAG"],
        ),
    ]
    negative_resumes: list[ScoredCandidate] = []
    existing_terms = [
        _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent"),
        _query_term("RAG", source="jd", category="domain", retrieval_role="domain_context", family="feedback.rag"),
    ]
    sent_query_terms = ["RAG"]

    decision = build_feedback_decision(
        seed_resumes=seed_resumes,
        negative_resumes=negative_resumes,
        existing_terms=existing_terms,
        sent_query_terms=sent_query_terms,
        round_no=4,
    )

    expected = QueryTermCandidate(
        term="LangGraph",
        source="candidate_feedback",
        category="expansion",
        priority=1,
        evidence="Supported by 2 seed resumes: seed-1, seed-2.",
        first_added_round=4,
        active=True,
        retrieval_role="core_skill",
        queryability="admitted",
        family="feedback.langgraph",
    )

    assert decision.skipped_reason is None
    assert decision.seed_resume_ids == ["seed-1", "seed-2"]
    assert decision.accepted_term == expected
    assert [item.term for item in decision.accepted_candidates] == ["LangGraph"]
    assert [item.term for item in decision.rejected_terms] == ["RAG"]
    assert decision.forced_query_terms == ["AI Agent", "LangGraph"]


def test_build_feedback_decision_ignores_seed_negative_fields() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["LangGraph"], negative_signals=["Missing Kubernetes"]),
            _scored_candidate("seed-2", evidence=["LangGraph"], weaknesses=["Missing Kubernetes"]),
        ],
        negative_resumes=[],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent")
        ],
        sent_query_terms=[],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "LangGraph"
    assert "Missing Kubernetes" not in {item.term for item in decision.candidate_terms}


def test_build_feedback_decision_does_not_let_tiny_negative_sample_suppress_seed_term() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["LangGraph"]),
            _scored_candidate("seed-2", evidence=["LangGraph"]),
        ],
        negative_resumes=[
            _scored_candidate("not-fit-1", fit_bucket="not_fit", evidence=["LangGraph"]),
        ],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent")
        ],
        sent_query_terms=[],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "LangGraph"


def test_build_feedback_decision_prefers_clean_term_over_narrative_phrase() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", reasoning_summary="Built LangGraph workflow orchestration with RAG.", evidence=["LangGraph"]),
            _scored_candidate("seed-2", reasoning_summary="Built LangGraph workflow orchestration with RAG.", evidence=["LangGraph"]),
        ],
        negative_resumes=[],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent"),
            _query_term("RAG", family="feedback.rag"),
        ],
        sent_query_terms=["RAG"],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "LangGraph"
    assert not decision.accepted_term.term.startswith("Built ")


def test_build_feedback_decision_prefers_shaped_term_over_plain_english_phrase() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", reasoning_summary="Delivered backend orchestration with Node.js.", evidence=["Node.js"]),
            _scored_candidate("seed-2", reasoning_summary="Delivered backend orchestration with Node.js.", evidence=["Node.js"]),
        ],
        negative_resumes=[],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent"),
        ],
        sent_query_terms=[],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "Node.js"


def test_candidate_feedback_model_ranking_forbids_unknown_terms() -> None:
    ranking = CandidateFeedbackModelRanking(
        accepted_terms=["langgraph", "InventedTerm"],
        rejected_terms={"平台": "generic"},
        rationale="Lowercase langgraph was not copied exactly.",
    )
    terms = [
        FeedbackCandidateTerm(term="LangGraph", supporting_resume_ids=["r1", "r2"]),
        FeedbackCandidateTerm(term="平台", supporting_resume_ids=["r1", "r2"]),
    ]

    assert ranking.accepted_from(terms) == []


def test_candidate_feedback_model_steps_filters_model_output_exactly(monkeypatch) -> None:
    class FakeResult:
        output = CandidateFeedbackModelRanking(
            accepted_terms=["langgraph", "InventedTerm"],
            rejected_terms={},
            rationale="No term was copied exactly.",
        )

    class FakeAgent:
        prompt = ""

        async def run(self, prompt: str) -> FakeResult:
            self.prompt = prompt
            return FakeResult()

    fake_agent = FakeAgent()
    steps = CandidateFeedbackModelSteps(make_settings())
    monkeypatch.setattr(steps, "_agent", lambda: fake_agent)

    async def run_rank() -> CandidateFeedbackModelRanking:
        return await steps.rank_terms(
            role_title="AI Agent Engineer",
            must_have_capabilities=["Agent workflow orchestration"],
            existing_terms=["AI Agent"],
            candidates=[FeedbackCandidateTerm(term="LangGraph", supporting_resume_ids=["r1", "r2"])],
        )

    ranking = asyncio.run(run_rank())

    assert ranking.accepted_terms == []
    assert "candidate_terms" in fake_agent.prompt
    assert "accepted_terms must be copied exactly" in fake_agent.prompt
