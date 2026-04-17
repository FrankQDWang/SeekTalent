from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from seektalent.config import AppSettings
from seektalent.controller.react_controller import ReActController
from seektalent.finalize.finalizer import Finalizer
from seektalent.models import (
    ControllerContext,
    FinalCandidate,
    FinalResult,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    NormalizedResume,
    PreferenceSlots,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionContext,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RoundRetrievalPlan,
    SearchObservation,
    RequirementExtractionDraft,
    RequirementSheet,
    ScoredCandidate,
    ScoringContext,
    ScoringPolicy,
)
from seektalent.prompting import LoadedPrompt
from seektalent.reflection.critic import ReflectionCritic
from seektalent.requirements.extractor import RequirementExtractor
from seektalent.scoring.scorer import ResumeScorer
from tests.settings_factory import make_settings


def _prompt(name: str) -> LoadedPrompt:
    return LoadedPrompt(name=name, path=Path(f"{name}.md"), content=f"{name} prompt", sha256="hash")


def _settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return make_settings()


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_term="python",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "retrieval"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="retrieval",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )


def _controller_context() -> ControllerContext:
    return ControllerContext(
        full_jd="JD text",
        full_notes="Notes text",
        requirement_sheet=_requirement_sheet(),
        round_no=1,
        min_rounds=1,
        max_rounds=3,
        is_final_allowed_round=False,
        target_new=5,
    )


def _reflection_context() -> ReflectionContext:
    return ReflectionContext(
        round_no=1,
        full_jd="JD text",
        full_notes="Notes text",
        requirement_sheet=_requirement_sheet(),
        current_retrieval_plan=RoundRetrievalPlan(
            plan_version=1,
            round_no=1,
            query_terms=["python", "retrieval"],
            keyword_query='python retrieval',
            projected_cts_filters={},
            runtime_only_constraints=[],
            location_execution_plan=LocationExecutionPlan(
                mode="single",
                allowed_locations=["上海"],
                preferred_locations=[],
                priority_order=[],
                balanced_order=["上海"],
                rotation_offset=0,
                target_new=5,
            ),
            target_new=5,
            rationale="Round 1 query.",
        ),
        search_observation=SearchObservation(
            round_no=1,
            requested_count=5,
            raw_candidate_count=2,
            unique_new_count=2,
            shortage_count=3,
            fetch_attempt_count=1,
            adapter_notes=[],
            new_candidate_summaries=[],
            city_search_summaries=[],
        ),
        search_attempts=[],
        top_candidates=[],
        dropped_candidates=[],
        scoring_failures=[],
        sent_query_history=[],
    )


def _scored_candidate() -> ScoredCandidate:
    return ScoredCandidate(
        resume_id="resume-1",
        source_round=1,
        fit_bucket="fit",
        overall_score=85,
        must_have_match_score=90,
        preferred_match_score=75,
        risk_score=20,
        confidence="high",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["retrieval"],
        negative_signals=[],
        strengths=["Strong Python"],
        weaknesses=[],
        risk_flags=[],
        reasoning_summary="Strong fit.",
    )


def _scoring_context() -> ScoringContext:
    return ScoringContext(
        round_no=1,
        scoring_policy=ScoringPolicy(
            role_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python"],
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            preferences=PreferenceSlots(),
            scoring_rationale="Score Python fit first.",
        ),
        normalized_resume=NormalizedResume(
            resume_id="resume-1",
            dedup_key="resume-1",
            current_title="Python Engineer",
            current_company="Example Co",
            locations=["上海"],
            skills=["python"],
            raw_text_excerpt="Python retrieval trace",
            completeness_score=90,
            source_round=1,
        ),
    )


class _StubAgent:
    def __init__(self, output) -> None:
        self.output = output
        self.calls = 0

    async def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls += 1
        return SimpleNamespace(output=self.output)


def test_requirement_extractor_uses_run_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    extractor = RequirementExtractor(_settings(monkeypatch), _prompt("requirements"))
    stub_agent = _StubAgent(
        RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            title_anchor_term="python",
            jd_query_terms=["retrieval"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python"],
            locations=["上海"],
            preferred_query_terms=["python"],
            scoring_rationale="Score Python fit first.",
        )
    )
    monkeypatch.setattr(extractor, "_get_agent", lambda: stub_agent)

    _, output = asyncio.run(
        extractor.extract_with_draft(
            input_truth=InputTruth(
                job_title="Senior Python Engineer",
                jd="jd",
                notes="notes",
                job_title_sha256="title-hash",
                jd_sha256="jd-hash",
                notes_sha256="notes-hash",
            )
        )
    )

    assert output.role_title == "Senior Python Engineer"
    assert stub_agent.calls == 1


@pytest.mark.parametrize(
    "builder,prompt_name",
    [
        (RequirementExtractor, "requirements"),
        (ReActController, "controller"),
        (ReflectionCritic, "reflection"),
        (Finalizer, "finalize"),
    ],
)
def test_sync_stages_build_fresh_agents(builder, prompt_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    component = builder(_settings(monkeypatch), _prompt(prompt_name))

    assert component._get_agent() is not component._get_agent()


def test_repeated_async_stage_calls_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = ReActController(_settings(monkeypatch), _prompt("controller"))
    controller_agent = _StubAgent(
        {
            "thought_summary": "Search.",
            "action": "search_cts",
            "decision_rationale": "Need recall.",
            "proposed_query_terms": ["python", "retrieval"],
            "proposed_filter_plan": {},
        }
    )
    monkeypatch.setattr(controller, "_get_agent", lambda: controller_agent)
    asyncio.run(controller.decide(context=_controller_context()))
    asyncio.run(controller.decide(context=_controller_context()))
    assert controller_agent.calls == 2

    critic = ReflectionCritic(_settings(monkeypatch), _prompt("reflection"))
    reflection_agent = _StubAgent(
        ReflectionAdvice(
            strategy_assessment="Stay the course.",
            quality_assessment="Pool quality is acceptable.",
            coverage_assessment="Coverage is improving.",
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(),
            suggest_stop=False,
            reflection_summary="Continue.",
        )
    )
    monkeypatch.setattr(critic, "_get_agent", lambda: reflection_agent)
    asyncio.run(critic.reflect(context=_reflection_context()))
    asyncio.run(critic.reflect(context=_reflection_context()))
    assert reflection_agent.calls == 2

    finalizer = Finalizer(_settings(monkeypatch), _prompt("finalize"))
    finalizer_agent = _StubAgent(
        FinalResult(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=1,
            stop_reason="controller_stop",
            summary="Shortlist ready.",
            candidates=[
                FinalCandidate(
                    resume_id="resume-1",
                    rank=1,
                    final_score=85,
                    fit_bucket="fit",
                    match_summary="Strong fit.",
                    strengths=["Strong Python"],
                    weaknesses=[],
                    matched_must_haves=["python"],
                    matched_preferences=[],
                    risk_flags=[],
                    why_selected="Strong fit.",
                    source_round=1,
                )
            ],
        )
    )
    monkeypatch.setattr(finalizer, "_get_agent", lambda: finalizer_agent)
    asyncio.run(
        finalizer.finalize(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=1,
            stop_reason="controller_stop",
            ranked_candidates=[_scored_candidate()],
        )
    )
    asyncio.run(
        finalizer.finalize(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=1,
            stop_reason="controller_stop",
            ranked_candidates=[_scored_candidate()],
        )
    )
    assert finalizer_agent.calls == 2


def test_scorer_builds_one_agent_per_parallel_call(monkeypatch: pytest.MonkeyPatch) -> None:
    scorer = ResumeScorer(_settings(monkeypatch), _prompt("scoring"))
    created_agents: list[object] = []
    used_agents: list[object] = []

    def build_agent() -> object:
        agent = object()
        created_agents.append(agent)
        return agent

    async def fake_score_candidates_parallel(*, contexts, tracer, agent):  # noqa: ANN001, ARG001
        used_agents.append(agent)
        return [], []

    monkeypatch.setattr(scorer, "_build_agent", build_agent)
    monkeypatch.setattr(scorer, "_score_candidates_parallel", fake_score_candidates_parallel)

    asyncio.run(scorer.score_candidates_parallel(contexts=[_scoring_context()], tracer=cast(Any, object())))
    asyncio.run(scorer.score_candidates_parallel(contexts=[_scoring_context()], tracer=cast(Any, object())))

    assert len(created_agents) == 2
    assert used_agents == created_agents
    assert created_agents[0] is not created_agents[1]
