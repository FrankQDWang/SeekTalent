import json
from pathlib import Path

from cv_match.config import AppSettings
from cv_match.models import (
    CTSQuery,
    FinalCandidate,
    FinalResult,
    HardConstraintSlots,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RequirementExtractionDraft,
    RequirementSheet,
    ScoredCandidate,
    ScoringFailure,
    SearchControllerDecision,
)
from cv_match.runtime import WorkflowRuntime
from cv_match.tracing import RunTracer


def _sample_inputs() -> tuple[str, str]:
    return (
        "Senior Python Engineer responsible for resume matching workflows.",
        "Prefer retrieval experience and shipping production AI features.",
    )


class SequenceController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0

    def decide(self, *, context):
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
    def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, self.extract(input_truth=None)

    def extract(self, *, input_truth) -> RequirementSheet:
        del input_truth
        return RequirementSheet(
            role_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=[
                QueryTermCandidate(
                    term="python",
                    source="jd",
                    category="role_anchor",
                    priority=1,
                    evidence="JD title",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="resume matching",
                    source="notes",
                    category="domain",
                    priority=2,
                    evidence="Notes mention resume matching.",
                    first_added_round=0,
                ),
            ],
            scoring_rationale="Score Python fit first.",
        )


class SequenceReflection:
    def __init__(self) -> None:
        self.calls = 0

    def reflect(self, *, context) -> ReflectionAdvice:
        self.calls += 1
        if self.calls == 1:
            return ReflectionAdvice(
                strategy_assessment="Current anchors are aligned with the role.",
                quality_assessment="Top pool has signal but still lacks breadth.",
                coverage_assessment="Coverage is narrow after the first pass.",
                keyword_advice=ReflectionKeywordAdvice(
                    suggested_add_terms=["trace"],
                    critique="Add one tracing term next round.",
                ),
                filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
                suggest_stop=False,
                reflection_summary="Continue with one extra tracing term.",
            )
        return ReflectionAdvice(
            strategy_assessment="Second round is aligned.",
            quality_assessment="Top pool quality is now stable.",
            coverage_assessment="Marginal recall is low.",
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
            suggest_stop=True,
            suggested_stop_reason="reflection_stop",
            reflection_summary="Stop after round 2.",
        )


class StubScorer:
    def score_candidates_parallel(self, *, contexts, tracer):
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


class StubFinalizer:
    last_validator_retry_count = 0

    def finalize(self, *, run_id, run_dir, rounds_executed, stop_reason, ranked_candidates) -> FinalResult:
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


def test_runtime_updates_run_state_across_rounds(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=2,
    )
    runtime = WorkflowRuntime(settings)
    runtime.requirement_extractor = StubRequirementExtractor()
    runtime.controller = SequenceController()
    runtime.reflection_critic = SequenceReflection()
    runtime.resume_scorer = StubScorer()
    runtime.finalizer = StubFinalizer()
    tracer = RunTracer(tmp_path / "trace-runs")
    jd, notes = _sample_inputs()

    try:
        run_state = runtime._build_run_state(jd=jd, notes=notes, tracer=tracer)
        top_candidates, stop_reason, rounds_executed = runtime._run_rounds(run_state=run_state, tracer=tracer)
    finally:
        tracer.close()

    assert rounds_executed == 2
    assert stop_reason == "max_rounds_reached"
    assert len(top_candidates) > 0
    assert run_state.retrieval_state.current_plan_version == 2
    assert [item.round_no for item in run_state.retrieval_state.sent_query_history] == [1, 2]
    assert [item.city for item in run_state.retrieval_state.sent_query_history] == ["上海", "上海"]
    assert run_state.retrieval_state.sent_query_history[1].query_terms == ["python", "resume matching", "trace"]
    assert len(run_state.retrieval_state.reflection_keyword_advice_history) == 2
    assert len(run_state.retrieval_state.reflection_filter_advice_history) == 2
    assert any(item.term == "trace" and item.active for item in run_state.retrieval_state.query_term_pool)
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
    assert run_state.round_history[1].cts_queries == round_02_queries
