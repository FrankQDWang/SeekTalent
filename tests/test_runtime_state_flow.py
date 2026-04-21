import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from seektalent.models import (
    CTSQuery,
    FinalCandidate,
    FinalResult,
    HardConstraintSlots,
    InputTruth,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RequirementExtractionDraft,
    RequirementSheet,
    ResumeCandidate,
    RetrievalState,
    ScoredCandidate,
    ScoringPolicy,
    ScoringFailure,
    SearchControllerDecision,
    SentQueryRecord,
    StopControllerDecision,
    RunState,
)
from seektalent.retrieval import build_location_execution_plan, build_round_retrieval_plan
from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


def _sample_inputs() -> tuple[str, str, str]:
    return (
        "Senior Python Engineer",
        "Senior Python Engineer responsible for resume matching workflows.",
        "Prefer retrieval experience and shipping production AI features.",
    )


def _make_candidate(resume_id: str, *, source_round: int = 1) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        dedup_key=resume_id,
        source_round=source_round,
        now_location="上海",
        expected_location="上海",
        expected_job_category="Python Engineer",
        work_year=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=["Example Co | Python Engineer | Built retrieval workflows."],
        project_names=["Resume search"],
        work_summaries=["python", "retrieval", "trace"],
        search_text="python retrieval trace resume search",
        raw={"resume_id": resume_id, "candidate_name": resume_id},
    )


class SequenceController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return SearchControllerDecision(
            thought_summary="Round 2 widens the domain surface.",
            action="search_cts",
            decision_rationale="Add one reflection term while keeping the same filter shape.",
            proposed_query_terms=["python", "resume matching", "trace"],
            proposed_filter_plan=ProposedFilterPlan(),
            response_to_reflection="Accepted the added trace term and left location execution to runtime.",
        )


class StubRequirementExtractor:
    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            title_anchor_term="python",
            jd_query_terms=["resume matching", "trace"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_term="python",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
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
                    term="resume matching",
                    source="jd",
                    category="domain",
                    priority=2,
                    evidence="JD body",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                ),
            ],
            scoring_rationale="Score Python fit first.",
        )


class SingleFamilyRequirementExtractor:
    def __init__(self, *, include_reserve: bool) -> None:
        self.include_reserve = include_reserve

    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        pool = [
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
        ]
        if self.include_reserve:
            pool.append(
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                    active=False,
                )
            )
        draft = RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            title_anchor_term="python",
            jd_query_terms=["resume matching"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_term="python",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=pool,
            scoring_rationale="Score Python fit first.",
        )


class SequenceReflection:
    def __init__(self) -> None:
        self.calls = 0

    async def reflect(self, *, context) -> ReflectionAdvice:
        self.calls += 1
        if self.calls == 1:
            return ReflectionAdvice(
                keyword_advice=ReflectionKeywordAdvice(suggested_keep_terms=["trace"]),
                filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
                suggest_stop=False,
                reflection_summary="Continue with one extra tracing term.",
            )
        return ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
            suggest_stop=True,
            suggested_stop_reason="reflection_stop",
            reflection_summary="Stop after round 2.",
        )


class StubScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []
        for context in contexts:
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=context.normalized_resume.resume_id,
                branch_id=f"r{context.round_no}-{context.normalized_resume.resume_id}",
                model="stub-scorer",
                summary="stub score",
                payload={},
            )
            scored.append(
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=90 if context.round_no == 1 else 91,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="Stub scorer accepted the candidate.",
                    evidence=["python", "retrieval"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["resume matching"],
                    negative_signals=[],
                    strengths=["Strong backend match."],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
            )
        return scored, failures


class LowQualityScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        return (
            [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=65,
                    must_have_match_score=60,
                    preferred_match_score=50,
                    risk_score=40,
                    risk_flags=[],
                    reasoning_summary="Usable but not a strong fit.",
                    evidence=["python"],
                    confidence="medium",
                    matched_must_haves=["python"],
                    missing_must_haves=["resume matching"],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=["Some Python signal."],
                    weaknesses=["Weak retrieval-specific evidence."],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
                for context in contexts
            ],
            [],
        )


class StubFinalizer:
    last_validator_retry_count = 0

    async def finalize(self, *, run_id, run_dir, rounds_executed, stop_reason, ranked_candidates) -> FinalResult:
        return FinalResult(
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            summary=f"Returned {len(ranked_candidates)} candidates after {rounds_executed} rounds.",
            candidates=[
                FinalCandidate(
                    resume_id=item.resume_id,
                    rank=index,
                    final_score=item.overall_score,
                    fit_bucket=item.fit_bucket,
                    match_summary="stub match summary",
                    strengths=item.strengths,
                    weaknesses=item.weaknesses,
                    matched_must_haves=item.matched_must_haves,
                    matched_preferences=item.matched_preferences,
                    risk_flags=item.risk_flags,
                    why_selected=item.reasoning_summary,
                    source_round=item.source_round,
                )
                for index, item in enumerate(ranked_candidates, start=1)
            ],
        )


class StopAfterSecondRoundController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        if self.calls == 2:
            return SearchControllerDecision(
                thought_summary="Round 2 widens the domain surface.",
                action="search_cts",
                decision_rationale="Add one reflection term while keeping the same filter shape.",
                proposed_query_terms=["python", "resume matching", "trace"],
                proposed_filter_plan=ProposedFilterPlan(),
                response_to_reflection="Accepted the added trace term and left location execution to runtime.",
            )
        return StopControllerDecision(
            thought_summary="Stop after two completed retrieval rounds.",
            action="stop",
            decision_rationale="The top pool has stabilized and the next search is unlikely to add fit candidates.",
            response_to_reflection="The latest reflection confirms low marginal value.",
            stop_reason="controller_stop",
        )


class StopOnSecondRoundController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return StopControllerDecision(
            thought_summary="Stop before trying all admitted families.",
            action="stop",
            decision_rationale="The current pool seems stable enough.",
            response_to_reflection="Acknowledged the latest reflection.",
            stop_reason="controller_stop",
        )


class SearchThenStopController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the active family.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return StopControllerDecision(
            thought_summary="Stop despite low-quality exhaustion.",
            action="stop",
            decision_rationale="Controller wants to stop.",
            response_to_reflection="Acknowledged the latest reflection.",
            stop_reason="controller_stop",
        )


def _install_runtime_stubs(runtime: WorkflowRuntime, *, controller: object, resume_scorer: object) -> None:
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = StubRequirementExtractor()
    runtime_any.controller = controller
    runtime_any.reflection_critic = SequenceReflection()
    runtime_any.resume_scorer = resume_scorer
    runtime_any.finalizer = StubFinalizer()


def _install_broaden_stubs(runtime: WorkflowRuntime, *, include_reserve: bool) -> None:
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = SingleFamilyRequirementExtractor(include_reserve=include_reserve)
    runtime_any.controller = SearchThenStopController()
    runtime_any.reflection_critic = SequenceReflection()
    runtime_any.resume_scorer = LowQualityScorer()
    runtime_any.finalizer = StubFinalizer()


def test_runtime_updates_run_state_across_rounds(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=2,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        top_candidates, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    assert rounds_executed == 2
    assert stop_reason == "max_rounds_reached"
    assert terminal_controller_round is None
    assert len(top_candidates) > 0
    assert run_state.retrieval_state.current_plan_version == 2
    assert [item.round_no for item in run_state.retrieval_state.sent_query_history] == [1, 2, 2]
    assert [item.city for item in run_state.retrieval_state.sent_query_history] == ["上海", "上海", "上海"]
    assert [item.query_role for item in run_state.retrieval_state.sent_query_history] == [
        "exploit",
        "exploit",
        "explore",
    ]
    assert run_state.retrieval_state.sent_query_history[1].query_terms == ["python", "resume matching", "trace"]
    assert run_state.retrieval_state.sent_query_history[2].query_terms == ["python", "trace"]
    assert all(
        sum(1 for term in item.query_terms if term == "python") == 1
        for item in run_state.retrieval_state.sent_query_history
    )
    assert all(len(item.query_terms) <= 3 for item in run_state.retrieval_state.sent_query_history)
    assert len(run_state.retrieval_state.reflection_keyword_advice_history) == 2
    assert len(run_state.retrieval_state.reflection_filter_advice_history) == 2
    assert [item.term for item in run_state.retrieval_state.query_term_pool] == ["python", "resume matching", "trace"]
    assert len(run_state.round_history) == 2
    assert run_state.round_history[0].reflection_advice is not None
    assert run_state.round_history[1].reflection_advice is not None
    assert run_state.round_history[1].reflection_advice.suggest_stop is True
    assert run_state.round_history[1].controller_decision.response_to_reflection
    round_02_queries = [
        CTSQuery.model_validate(item)
        for item in json.loads(
            (tracer.run_dir / "rounds" / "round_02" / "cts_queries.json").read_text(encoding="utf-8")
        )
    ]
    round_02_normalized = [
        json.loads(line)
        for line in (tracer.run_dir / "rounds" / "round_02" / "scoring_input_refs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item.query_role for item in round_02_queries] == ["exploit", "explore"]
    assert run_state.round_history[1].search_observation is not None
    assert run_state.round_history[1].search_observation.unique_new_count <= 10
    assert len(run_state.round_history[1].search_observation.new_resume_ids) <= 10
    assert len(round_02_normalized) == run_state.round_history[1].search_observation.unique_new_count
    round_01_top_id = run_state.round_history[0].top_candidates[0].resume_id
    assert run_state.scorecards_by_resume_id[round_01_top_id].overall_score == 90
    assert run_state.round_history[1].cts_queries == round_02_queries


class RecordingScorer:
    def __init__(self) -> None:
        self.resume_ids: list[str] = []

    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        self.resume_ids.extend(context.normalized_resume.resume_id for context in contexts)
        return (
            [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=95,
                    must_have_match_score=90,
                    preferred_match_score=70,
                    risk_score=5,
                    risk_flags=[],
                    reasoning_summary="Fresh candidate scored once.",
                    evidence=["python"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["resume matching"],
                    negative_signals=[],
                    strengths=["Fresh strong match."],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
                for context in contexts
            ],
            [],
        )


def test_score_round_keeps_existing_scorecards_and_only_scores_new_resumes(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    cast(Any, runtime).resume_scorer = RecordingScorer()
    _, requirement_sheet = asyncio.run(StubRequirementExtractor().extract_with_draft(input_truth=None))
    existing = ScoredCandidate(
        resume_id="seen",
        fit_bucket="fit",
        overall_score=80,
        must_have_match_score=78,
        preferred_match_score=60,
        risk_score=12,
        risk_flags=[],
        reasoning_summary="Existing score should stay untouched.",
        evidence=["python"],
        confidence="high",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["resume matching"],
        negative_signals=[],
        strengths=["Existing top match."],
        weaknesses=[],
        source_round=1,
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Senior Python Engineer",
            jd="JD",
            notes="Notes",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        candidate_store={"seen": _make_candidate("seen", source_round=1)},
        scorecards_by_resume_id={"seen": existing},
        top_pool_ids=["seen"],
    )
    tracer = RunTracer(tmp_path / "trace-runs")

    try:
        top_candidates, pool_decisions, dropped_candidates = asyncio.run(
            runtime._score_round(
                round_no=2,
                new_candidates=[_make_candidate("seen", source_round=2), _make_candidate("fresh", source_round=2)],
                run_state=run_state,
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert cast(Any, runtime).resume_scorer.resume_ids == ["fresh"]
    assert run_state.scorecards_by_resume_id["seen"].overall_score == 80
    assert run_state.scorecards_by_resume_id["fresh"].overall_score == 95
    assert [item.resume_id for item in top_candidates] == ["fresh", "seen"]
    assert [item.decision for item in pool_decisions] == ["selected", "retained"]
    assert dropped_candidates == []


def test_materialize_candidates_requires_candidate_store_entry(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    scored = ScoredCandidate(
        resume_id="missing",
        fit_bucket="fit",
        overall_score=90,
        must_have_match_score=88,
        preferred_match_score=70,
        risk_score=8,
        reasoning_summary="Scored candidate without source resume.",
        confidence="high",
        source_round=1,
    )

    with pytest.raises(KeyError, match="missing"):
        runtime._materialize_candidates(scored_candidates=[scored], candidate_store={})


def test_runtime_records_terminal_controller_round_separately(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=3,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopAfterSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    assert rounds_executed == 2
    assert stop_reason == "controller_stop"
    assert len(run_state.round_history) == 2
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 3
    assert terminal_controller_round.controller_decision.action == "stop"
    assert terminal_controller_round.stop_guidance.can_stop is True
    assert (tracer.run_dir / "rounds" / "round_03" / "controller_decision.json").exists()
    assert not (tracer.run_dir / "rounds" / "round_03" / "retrieval_plan.json").exists()
    assert not (tracer.run_dir / "rounds" / "round_03" / "search_observation.json").exists()
    assert not (tracer.run_dir / "rounds" / "round_03" / "reflection_advice.json").exists()


def test_runtime_forces_continue_when_stop_guidance_blocks_stop(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=3,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopOnSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )

    assert round_02_decision["action"] == "search_cts"
    assert "admitted families remain untried" in round_02_decision["decision_rationale"]
    assert round_02_decision["proposed_query_terms"] == ["python", "trace", "resume matching"]
    assert rounds_executed == 2
    assert stop_reason == "controller_stop"
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 3
    assert terminal_controller_round.stop_guidance.can_stop is True


def test_runtime_forces_broaden_with_inactive_admitted_reserve_term(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=True)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_02_context = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_context.json").read_text(encoding="utf-8")
    )
    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )
    round_02_plan = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "retrieval_plan.json").read_text(encoding="utf-8")
    )

    assert round_02_context["stop_guidance"]["quality_gate_status"] == "broaden_required"
    assert round_02_decision["action"] == "search_cts"
    assert "Runtime broaden" in round_02_decision["decision_rationale"]
    assert round_02_decision["proposed_query_terms"] == ["python", "trace"]
    assert round_02_plan["query_terms"] == ["python", "trace"]
    assert [item.term for item in run_state.retrieval_state.query_term_pool if item.active] == [
        "python",
        "resume matching",
        "trace",
    ]
    assert stop_reason == "controller_stop"
    assert rounds_executed == 2
    assert terminal_controller_round is not None
    assert terminal_controller_round.stop_guidance.quality_gate_status == "low_quality_exhausted"
    assert terminal_controller_round.stop_guidance.broadening_attempted is True


def test_runtime_forces_anchor_only_broaden_when_no_reserve_term_remains(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=10,
    )
    runtime = WorkflowRuntime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_02_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "controller_decision.json").read_text(encoding="utf-8")
    )
    round_02_plan = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "retrieval_plan.json").read_text(encoding="utf-8")
    )
    round_02_queries = json.loads(
        (tracer.run_dir / "rounds" / "round_02" / "cts_queries.json").read_text(encoding="utf-8")
    )

    assert round_02_decision["proposed_query_terms"] == ["python"]
    assert round_02_plan["query_terms"] == ["python"]
    assert [item["query_role"] for item in round_02_queries] == ["exploit"]
    assert run_state.retrieval_state.sent_query_history[-1].query_terms == ["python"]
    assert stop_reason == "controller_stop"
    assert rounds_executed == 2
    assert terminal_controller_round is not None
    assert terminal_controller_round.stop_guidance.quality_gate_status == "low_quality_exhausted"
    assert terminal_controller_round.stop_guidance.broadening_attempted is True


def test_runtime_min_rounds_count_completed_retrieval_rounds(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=3,
        max_rounds=4,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopAfterSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, tracer=tracer)
        )
    finally:
        tracer.close()

    round_03_context = json.loads(
        (tracer.run_dir / "rounds" / "round_03" / "controller_context.json").read_text(encoding="utf-8")
    )
    round_03_decision = json.loads(
        (tracer.run_dir / "rounds" / "round_03" / "controller_decision.json").read_text(encoding="utf-8")
    )

    assert round_03_context["budget"]["retrieval_rounds_completed"] == 2
    assert round_03_context["stop_guidance"]["can_stop"] is False
    assert "2 retrieval rounds completed" in round_03_context["stop_guidance"]["reason"]
    assert round_03_decision["action"] == "search_cts"
    assert "2 retrieval rounds completed" in round_03_decision["decision_rationale"]
    assert (tracer.run_dir / "rounds" / "round_03" / "retrieval_plan.json").exists()
    assert rounds_executed == 3
    assert len(run_state.round_history) == 3
    assert stop_reason == "controller_stop"
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 4
    assert terminal_controller_round.stop_guidance.can_stop is True


def test_runtime_query_pool_can_activate_reserve_term_without_losing_all_active_terms(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    pool = [
        QueryTermCandidate(
            term="python",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="Job title",
            first_added_round=0,
        ),
        QueryTermCandidate(
            term="rag",
            source="jd",
            category="domain",
            priority=2,
            evidence="JD body",
            first_added_round=0,
            active=True,
        ),
        QueryTermCandidate(
            term="langchain",
            source="notes",
            category="tooling",
            priority=3,
            evidence="Notes body",
            first_added_round=0,
            active=False,
        ),
    ]

    updated = runtime._update_query_term_pool(
        pool,
        ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(
                suggested_activate_terms=["langchain"],
                suggested_drop_terms=["rag"],
            ),
            filter_advice=ReflectionFilterAdvice(),
            reflection_summary="Swap to the reserve framework term.",
        ),
        round_no=2,
    )

    assert [item.term for item in updated if item.active and item.source != "job_title"] == ["langchain"]


def test_runtime_query_pool_keeps_one_active_non_anchor_term(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    pool = [
        QueryTermCandidate(
            term="python",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="Job title",
            first_added_round=0,
        ),
        QueryTermCandidate(
            term="rag",
            source="jd",
            category="domain",
            priority=2,
            evidence="JD body",
            first_added_round=0,
            active=True,
        ),
    ]

    updated = runtime._update_query_term_pool(
        pool,
        ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(suggested_drop_terms=["rag"]),
            filter_advice=ReflectionFilterAdvice(),
            reflection_summary="Attempted to drop the only active non-anchor term.",
        ),
        round_no=2,
    )

    assert [item.term for item in updated if item.active and item.source != "job_title"] == ["rag"]


def test_runtime_degrades_to_single_query_when_no_distinct_explore_query_exists(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    requirement_sheet = RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_term="python",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "resume matching"],
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
    retrieval_plan = build_round_retrieval_plan(
        plan_version=2,
        round_no=2,
        query_terms=["python", "resume matching"],
        title_anchor_term=requirement_sheet.title_anchor_term,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_cts_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=requirement_sheet.hard_constraints.locations,
            preferred_locations=requirement_sheet.preferences.preferred_locations,
            round_no=2,
            target_new=10,
        ),
        target_new=10,
        rationale="single query fallback",
    )

    query_states = runtime._build_round_query_states(
        round_no=2,
        retrieval_plan=retrieval_plan,
        title_anchor_term=requirement_sheet.title_anchor_term,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["python", "resume matching"],
                keyword_query='python "resume matching"',
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )

    assert [item.query_role for item in query_states] == ["exploit"]
