from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic_ai.models.test import TestModel

from seektalent.config import AppSettings
from seektalent.controller.react_controller import ReActController
from seektalent.finalize.finalizer import Finalizer
from seektalent.llm import build_model
from seektalent.models import (
    ControllerContext,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    NormalizedResume,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionContext,
    ReflectionAdviceDraft,
    ReflectionFilterAdviceDraft,
    ReflectionKeywordAdviceDraft,
    RequirementSheet,
    RoundRetrievalPlan,
    ScoringContext,
    ScoringPolicy,
    SearchObservation,
    SearchControllerDecision,
    StopGuidance,
)
from seektalent.prompting import LoadedPrompt
from seektalent.repair import repair_controller_decision, repair_reflection_draft
from seektalent.requirements import RequirementExtractor
from seektalent.reflection.critic import ReflectionCritic
from seektalent.scoring.scorer import ResumeScorer
from tests.settings_factory import make_settings


def _prompt(name: str) -> LoadedPrompt:
    return LoadedPrompt(name=name, path=Path(f"{name}.md"), content=f"{name} prompt", sha256="hash")


def _settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return make_settings(llm_cache_dir=f".seektalent/cache-test-{uuid4().hex}")


def _test_model(output_text: str) -> TestModel:
    real = build_model("openai-responses:gpt-5.4-mini")
    return TestModel(custom_output_text=output_text, profile=real.profile)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_term="python",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python"],
        preferred_capabilities=["trace"],
        hard_constraints=HardConstraintSlots(locations=["上海市"]),
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
                term="resume matching",
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
    requirement_sheet = _requirement_sheet()
    return ControllerContext(
        full_jd="jd",
        full_notes="notes",
        requirement_sheet=requirement_sheet,
        round_no=1,
        min_rounds=1,
        max_rounds=3,
        retrieval_rounds_completed=0,
        rounds_remaining_after_current=2,
        budget_used_ratio=1 / 3,
        near_budget_limit=False,
        is_final_allowed_round=False,
        target_new=10,
        stop_guidance=StopGuidance(
            can_stop=True,
            reason="stop allowed by test fixture.",
            top_pool_strength="usable",
        ),
        query_term_pool=requirement_sheet.initial_query_term_pool,
    )


def _reflection_context() -> ReflectionContext:
    requirement_sheet = _requirement_sheet()
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python", "resume matching"],
        keyword_query='python "resume matching"',
        projected_cts_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="single",
            allowed_locations=["上海市"],
            preferred_locations=[],
            priority_order=[],
            balanced_order=["上海市"],
            rotation_offset=0,
            target_new=10,
        ),
        target_new=10,
        rationale="test retrieval plan",
    )
    search_observation = SearchObservation(
        round_no=1,
        requested_count=10,
        raw_candidate_count=0,
        unique_new_count=0,
        shortage_count=10,
        fetch_attempt_count=1,
        exhausted_reason="cts_exhausted",
        new_resume_ids=[],
        new_candidate_summaries=[],
        adapter_notes=[],
    )
    return ReflectionContext(
        round_no=1,
        full_jd="jd",
        full_notes="notes",
        requirement_sheet=requirement_sheet,
        current_retrieval_plan=retrieval_plan,
        search_observation=search_observation,
    )


def _scoring_context() -> ScoringContext:
    return ScoringContext(
        round_no=1,
        scoring_policy=ScoringPolicy(
            role_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python"],
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
        requirement_sheet_sha256="requirement-sheet-hash",
    )


def test_controller_decide_raises_live_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = ReActController(_settings(monkeypatch), _prompt("controller"))
    stub_agent = type("StubAgent", (), {"run": lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("controller boom"))})()
    monkeypatch.setattr(controller, "_get_agent", lambda: stub_agent)

    with pytest.raises(RuntimeError, match="controller boom"):
        asyncio.run(controller.decide(context=_controller_context()))


def test_requirement_extractor_raises_live_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    extractor = RequirementExtractor(_settings(monkeypatch), _prompt("requirements"))
    stub_agent = type(
        "StubAgent",
        (),
        {"run": lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("requirements boom"))},
    )()
    monkeypatch.setattr(extractor, "_get_agent", lambda prompt_cache_key=None: stub_agent)

    with pytest.raises(RuntimeError, match="requirements boom"):
        asyncio.run(
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


def test_reflection_reflect_raises_live_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    critic = ReflectionCritic(_settings(monkeypatch), _prompt("reflection"))
    stub_agent = type("StubAgent", (), {"run": lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reflection boom"))})()
    monkeypatch.setattr(critic, "_get_agent", lambda: stub_agent)

    with pytest.raises(RuntimeError, match="reflection boom"):
        asyncio.run(critic.reflect(context=_reflection_context()))


def test_finalizer_uses_live_path_and_raises_for_empty_ranked_list(monkeypatch: pytest.MonkeyPatch) -> None:
    finalizer = Finalizer(_settings(monkeypatch), _prompt("finalize"))
    stub_agent = type("StubAgent", (), {"run": lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("finalizer boom"))})()
    monkeypatch.setattr(finalizer, "_get_agent", lambda: stub_agent)

    with pytest.raises(RuntimeError, match="finalizer boom"):
        asyncio.run(
            finalizer.finalize(
                run_id="run-1",
                run_dir="/tmp/run-1",
                rounds_executed=1,
                stop_reason="controller_stop",
                ranked_candidates=[],
            )
        )


def test_requirement_extractor_fails_after_two_output_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    extractor = RequirementExtractor(_settings(monkeypatch), _prompt("requirements"))
    monkeypatch.setattr("seektalent.requirements.extractor.build_model", lambda model_id: _test_model("{}"))

    with pytest.raises(Exception, match="Exceeded maximum retries \\(2\\) for output validation"):
        asyncio.run(
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


def test_controller_fails_after_two_output_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = ReActController(_settings(monkeypatch), _prompt("controller"))
    monkeypatch.setattr(
        "seektalent.controller.react_controller.build_model",
        lambda model_id: _test_model('{"action":"search_cts"}'),
    )

    with pytest.raises(Exception, match="Exceeded maximum retries \\(2\\) for output validation"):
        asyncio.run(controller.decide(context=_controller_context()))


def test_controller_repair_prompt_uses_source_user_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Need recall.",
        proposed_query_terms=["python"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    async def fake_repair_with_model(settings, **kwargs):  # noqa: ANN001, ANN003
        del settings
        captured["user_prompt"] = kwargs["user_prompt"]
        return decision, None

    monkeypatch.setattr("seektalent.repair._repair_with_model", fake_repair_with_model)

    repaired, _ = asyncio.run(
        repair_controller_decision(
            _settings(monkeypatch),
            _prompt("controller"),
            "VISIBLE CONTROLLER PROMPT",
            decision,
            "broken",
        )
    )

    assert repaired == decision
    assert "SOURCE_USER_PROMPT" in captured["user_prompt"]
    assert "VISIBLE CONTROLLER PROMPT" in captured["user_prompt"]
    assert "CONTROLLER_CONTEXT" not in captured["user_prompt"]


def test_reflection_fails_after_two_output_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    critic = ReflectionCritic(_settings(monkeypatch), _prompt("reflection"))
    monkeypatch.setattr("seektalent.reflection.critic.build_model", lambda model_id: _test_model("{}"))

    with pytest.raises(Exception, match="Exceeded maximum retries \\(2\\) for output validation"):
        asyncio.run(critic.reflect(context=_reflection_context()))


def test_reflection_repair_prompt_uses_source_user_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    draft = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=True,
        suggested_stop_reason="Search is saturated.",
        reflection_rationale="Enough signal.",
    )

    async def fake_repair_with_model(settings, **kwargs):  # noqa: ANN001, ANN003
        del settings
        captured["user_prompt"] = kwargs["user_prompt"]
        return draft, None

    monkeypatch.setattr("seektalent.repair._repair_with_model", fake_repair_with_model)

    repaired, _ = asyncio.run(
        repair_reflection_draft(
            _settings(monkeypatch),
            _prompt("reflection"),
            "VISIBLE REFLECTION PROMPT",
            draft,
            "broken",
        )
    )

    assert repaired == draft
    assert "SOURCE_USER_PROMPT" in captured["user_prompt"]
    assert "VISIBLE REFLECTION PROMPT" in captured["user_prompt"]
    assert "REFLECTION_CONTEXT" not in captured["user_prompt"]


def test_finalizer_fails_after_two_output_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    finalizer = Finalizer(_settings(monkeypatch), _prompt("finalize"))
    monkeypatch.setattr("seektalent.finalize.finalizer.build_model", lambda model_id: _test_model("{}"))

    with pytest.raises(Exception, match="Exceeded maximum retries \\(2\\) for output validation"):
        asyncio.run(
            finalizer.finalize(
                run_id="run-1",
                run_dir="/tmp/run-1",
                rounds_executed=1,
                stop_reason="controller_stop",
                ranked_candidates=[],
            )
        )


def test_scorer_returns_failure_after_two_output_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    scorer = ResumeScorer(_settings(monkeypatch), _prompt("scoring"))
    monkeypatch.setattr("seektalent.scoring.scorer.build_model", lambda model_id: _test_model("{}"))

    class StubTracer:
        def emit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def append_jsonl(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    scored, failures = asyncio.run(
        scorer.score_candidates_parallel(contexts=[_scoring_context()], tracer=cast(Any, StubTracer()))
    )

    assert scored == []
    assert len(failures) == 1
    assert failures[0].error_message == "Exceeded maximum retries (2) for output validation"
