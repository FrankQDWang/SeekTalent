import asyncio
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from seektalent.controller.react_controller import ReActController, validate_controller_decision
from seektalent.models import (
    CTSQuery,
    ControllerDecision,
    ControllerContext,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    ReflectionSummaryView,
    RequirementSheet,
    RetrievalState,
    RoundRetrievalPlan,
    RoundState,
    RunState,
    ScoringPolicy,
    SearchControllerDecision,
    StopControllerDecision,
    StopGuidance,
)
from seektalent.prompting import LoadedPrompt
from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import ProviderUsageSnapshot
from tests.settings_factory import make_settings


def test_controller_prompt_requires_atomic_search_terms() -> None:
    prompt = Path("src/seektalent/prompts/controller.md").read_text(encoding="utf-8")
    assert "Prefer atomic resume-side terms" in prompt
    assert 'Bad: `["Agent训推", "强化学习"]`; good: `["Agent", "强化学习"]`' in prompt
    assert 'Bad: `["Python后端开发", "高并发系统建设"]`; good: `["Python", "高并发"]`' in prompt


def test_controller_decision_rationale_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        SearchControllerDecision.model_validate(
            {
                "thought_summary": "Search.",
                "action": "search_cts",
                "decision_rationale": "a" * 2001,
                "proposed_query_terms": ["python", "resume matching"],
                "proposed_filter_plan": {},
            }
        )


def test_controller_thought_summary_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        SearchControllerDecision.model_validate(
            {
                "thought_summary": "a" * 501,
                "action": "search_cts",
                "decision_rationale": "Need recall.",
                "proposed_query_terms": ["python", "resume matching"],
                "proposed_filter_plan": {},
            }
        )


def test_controller_response_to_reflection_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        SearchControllerDecision.model_validate(
            {
                "thought_summary": "Search.",
                "action": "search_cts",
                "decision_rationale": "Need recall.",
                "proposed_query_terms": ["python", "resume matching"],
                "proposed_filter_plan": {},
                "response_to_reflection": "b" * 2001,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thought_summary", "a" * 501),
        ("decision_rationale", "a" * 2001),
        ("response_to_reflection", "b" * 2001),
    ],
)
def test_stop_controller_decision_uses_same_length_limits(field: str, value: str) -> None:
    payload = {
        "thought_summary": "Stop.",
        "action": "stop",
        "decision_rationale": "Enough signal.",
        "response_to_reflection": "Addressed previous reflection.",
        "stop_reason": "controller_stop",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        StopControllerDecision.model_validate(payload)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_term="python",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "retrieval"],
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
            QueryTermCandidate(
                term="retrieval",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="trace",
                source="jd",
                category="tooling",
                priority=4,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )


def _agent_requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="AI Agent工程师",
        title_anchor_term="AI Agent工程师",
        role_summary="Build Agent systems.",
        must_have_capabilities=["AI Agent", "LangChain"],
        hard_constraints=HardConstraintSlots(locations=["上海市"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="AI Agent",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="role_anchor",
                queryability="admitted",
                family="role.agent",
            ),
            QueryTermCandidate(
                term="LangChain",
                source="jd",
                category="tooling",
                priority=2,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="framework_tool",
                queryability="admitted",
                family="framework.langchain",
            ),
            QueryTermCandidate(
                term="AgentLoop调优",
                source="jd",
                category="expansion",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                active=False,
                retrieval_role="score_only",
                queryability="blocked",
                family="blocked.agentloop调优",
            ),
        ],
        scoring_rationale="Score Agent fit first.",
    )


def _fake_usage_result(output: ControllerDecision):
    class FakeUsage:
        input_tokens = 14
        output_tokens = 5
        total_tokens = 19
        cache_read_tokens = 9
        cache_write_tokens = 1
        details = {"reasoning_tokens": 7}

    class FakeResult:
        def __init__(self, output: ControllerDecision) -> None:
            self.output = output

        def usage(self) -> FakeUsage:
            return FakeUsage()

    return FakeResult(output)


def _provider_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        details={"reasoning_tokens": reasoning_tokens} if reasoning_tokens else {},
    )


def _controller_context(
    *,
    requirement_sheet: RequirementSheet | None = None,
    round_no: int = 1,
    min_rounds: int = 1,
    max_rounds: int = 3,
    previous_reflection: ReflectionSummaryView | None = None,
) -> ControllerContext:
    sheet = requirement_sheet or _requirement_sheet()
    return ControllerContext(
        full_jd="JD text",
        full_notes="Notes text",
        requirement_sheet=sheet,
        query_term_pool=sheet.initial_query_term_pool,
        round_no=round_no,
        min_rounds=min_rounds,
        max_rounds=max_rounds,
        retrieval_rounds_completed=max(0, round_no - 1),
        rounds_remaining_after_current=max(0, max_rounds - round_no),
        budget_used_ratio=round_no / max_rounds,
        near_budget_limit=(round_no / max_rounds) >= 0.8,
        is_final_allowed_round=round_no >= max_rounds,
        target_new=5,
        stop_guidance=StopGuidance(
            can_stop=True,
            reason="stop allowed by test fixture.",
            top_pool_strength="usable",
        ),
        previous_reflection=previous_reflection,
    )


def _run_state_with_previous_reflection() -> RunState:
    requirement_sheet = _requirement_sheet()
    return RunState(
        input_truth=InputTruth(
            job_title="Senior Python Engineer",
            jd="JD text",
            notes="Notes text",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        round_history=[
            RoundState(
                round_no=1,
                controller_decision=SearchControllerDecision(
                    thought_summary="Round 1 search.",
                    action="search_cts",
                    decision_rationale="Need initial recall.",
                    proposed_query_terms=["python", "resume matching"],
                    proposed_filter_plan=ProposedFilterPlan(),
                ),
                retrieval_plan=RoundRetrievalPlan(
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
                    rationale="Round 1 query.",
                ),
                cts_queries=[
                    CTSQuery(
                        query_terms=["python", "resume matching"],
                        keyword_query='python "resume matching"',
                        native_filters={"location": ["上海市"]},
                        rationale="Round 1 query.",
                    )
                ],
                reflection_advice=ReflectionAdvice(
                    keyword_advice=ReflectionKeywordAdvice(suggested_keep_terms=["trace"]),
                    filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
                    suggest_stop=False,
                    reflection_summary="Continue and widen the domain surface.",
                ),
            )
        ],
    )


def test_controller_decision_requires_proposals_for_search() -> None:
    with pytest.raises(ValidationError):
        SearchControllerDecision.model_validate(
            {
                "thought_summary": "Search.",
                "action": "search_cts",
                "decision_rationale": "Need recall.",
            }
        )


def test_controller_prompt_mentions_schema_budget_and_few_shot_term_rules() -> None:
    prompt = Path("src/seektalent/prompts/controller.md").read_text(encoding="utf-8")

    assert "few-shot terms are examples only" in prompt
    assert "current active admitted term bank" in prompt
    assert "thought_summary should stay short within schema budget" in prompt
    assert "decision_rationale should be a concise audit summary within schema budget" in prompt
    assert "not a step-by-step reasoning transcript" in prompt


def test_controller_decision_rejects_stop_with_search_fields() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ControllerDecision).validate_python(
            {
                "thought_summary": "Stop.",
                "action": "stop",
                "decision_rationale": "Enough signal.",
                "stop_reason": "controller_stop",
                "proposed_query_terms": ["python"],
            }
        )


def test_controller_decision_rejects_unknown_filter_fields() -> None:
    with pytest.raises(ValidationError):
        ProposedFilterPlan.model_validate({"optional_filters": {"unsupported_field": ["custom"]}})


def test_runtime_requires_response_to_reflection_after_previous_round() -> None:
    settings = make_settings(runs_dir=str(Path.cwd() / ".tmp-runs"), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    run_state = _run_state_with_previous_reflection()
    decision = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Add one more term.",
        proposed_query_terms=["python", "resume matching", "trace"],
        proposed_filter_plan=ProposedFilterPlan(optional_filters={"position": "Senior Python Engineer"}),
    )

    with pytest.raises(ValueError, match="response_to_reflection"):
        runtime._sanitize_controller_decision(decision=decision, run_state=run_state, round_no=2)


def test_runtime_accepts_reflection_backed_inactive_term_in_controller_sanitizer() -> None:
    settings = make_settings(runs_dir=str(Path.cwd() / ".tmp-runs"), mock_cts=True)
    runtime = WorkflowRuntime(settings)
    run_state = _run_state_with_previous_reflection()
    run_state.retrieval_state.query_term_pool = [
        item.model_copy(update={"active": False}) if item.term == "retrieval" else item
        for item in run_state.retrieval_state.query_term_pool
    ]
    assert run_state.round_history[0].reflection_advice is not None
    run_state.round_history[0].reflection_advice.keyword_advice = ReflectionKeywordAdvice(
        suggested_activate_terms=["  retrieval  "],
        suggested_keep_terms=["retrieval"],
    )
    decision = SearchControllerDecision(
        thought_summary="Use reflected term.",
        action="search_cts",
        decision_rationale="Use retrieval because previous reflection advised it.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
        response_to_reflection="Accepted the reflected retrieval advice.",
    )

    sanitized = runtime._sanitize_controller_decision(decision=decision, run_state=run_state, round_no=2)

    assert isinstance(sanitized, SearchControllerDecision)
    assert sanitized.proposed_query_terms == ["python", "retrieval"]


def test_controller_decision_discriminated_union_accepts_stop_payload() -> None:
    decision = TypeAdapter(ControllerDecision).validate_python(
        {
            "thought_summary": "Stop.",
            "action": "stop",
            "decision_rationale": "Enough strong candidates.",
            "stop_reason": "controller_stop",
        }
    )

    assert isinstance(decision, StopControllerDecision)
    assert decision.stop_reason == "controller_stop"


def test_validate_controller_decision_rejects_missing_response_to_reflection() -> None:
    context = _controller_context(
        round_no=2,
        previous_reflection=ReflectionSummaryView(decision="continue", reflection_summary="Add one term."),
    )
    decision = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Add one more term.",
        proposed_query_terms=["python", "resume matching", "trace"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    assert validate_controller_decision(context=context, decision=decision) == (
        "response_to_reflection is required when previous_reflection exists."
    )


def test_validate_controller_decision_rejects_empty_query_terms() -> None:
    context = _controller_context()
    decision = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Need recall.",
        proposed_query_terms=[],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    assert validate_controller_decision(context=context, decision=decision) == (
        "proposed_query_terms must contain at least one term."
    )


def test_validate_controller_decision_accepts_compiled_anchor_alias_without_literal_title_anchor() -> None:
    requirement_sheet = _agent_requirement_sheet()
    context = _controller_context(requirement_sheet=requirement_sheet)
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Need Agent recall.",
        proposed_query_terms=["AI Agent", "LangChain"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    assert validate_controller_decision(context=context, decision=decision) is None


def test_validate_controller_decision_rejects_blocked_compiler_terms() -> None:
    requirement_sheet = _agent_requirement_sheet()
    context = _controller_context(requirement_sheet=requirement_sheet)
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Need Agent recall.",
        proposed_query_terms=["AI Agent", "AgentLoop调优"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    result = validate_controller_decision(context=context, decision=decision)
    assert result is not None
    assert "compiler-admitted" in result


def test_controller_rejects_inactive_term_without_reflection_advice() -> None:
    context = _controller_context()
    context.query_term_pool = [
        item.model_copy(update={"active": False}) if item.term == "retrieval" else item
        for item in context.query_term_pool
    ]
    decision = SearchControllerDecision(
        thought_summary="Try reserve term.",
        action="search_cts",
        decision_rationale="Use the inactive retrieval reserve term.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    reason = validate_controller_decision(context=context, decision=decision)

    assert reason is not None
    assert "non-anchor query terms must be active" in reason


def test_controller_accepts_inactive_term_when_previous_reflection_advised_it() -> None:
    context = _controller_context(
        previous_reflection=ReflectionSummaryView(
            decision="continue",
            reflection_summary="Activate retrieval.",
            reflection_rationale="The previous round had shortage.",
        )
    )
    context.query_term_pool = [
        item.model_copy(update={"active": False}) if item.term == "retrieval" else item
        for item in context.query_term_pool
    ]
    context.latest_reflection_keyword_advice = ReflectionKeywordAdvice(
        suggested_activate_terms=["retrieval"]
    )
    context.latest_reflection_filter_advice = ReflectionFilterAdvice()
    decision = SearchControllerDecision(
        thought_summary="Accept reflection advice.",
        action="search_cts",
        decision_rationale="Use retrieval because reflection suggested activating it.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
        response_to_reflection="Accepted the suggested retrieval activation.",
    )

    assert validate_controller_decision(context=context, decision=decision) is None


def test_controller_rejects_inactive_term_when_advice_has_no_previous_reflection() -> None:
    context = _controller_context()
    context.query_term_pool = [
        item.model_copy(update={"active": False}) if item.term == "retrieval" else item
        for item in context.query_term_pool
    ]
    context.latest_reflection_keyword_advice = ReflectionKeywordAdvice(
        suggested_activate_terms=["retrieval"]
    )
    decision = SearchControllerDecision(
        thought_summary="Reject stale advice.",
        action="search_cts",
        decision_rationale="Do not use invisible reflection advice.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    reason = validate_controller_decision(context=context, decision=decision)

    assert reason is not None
    assert "non-anchor query terms must be active" in reason


def test_controller_accepts_inactive_term_when_reflection_advised_keeping_it() -> None:
    context = _controller_context(
        previous_reflection=ReflectionSummaryView(
            decision="continue",
            reflection_summary="Keep retrieval.",
        )
    )
    context.query_term_pool = [
        item.model_copy(update={"active": False}) if item.term == "retrieval" else item
        for item in context.query_term_pool
    ]
    context.latest_reflection_keyword_advice = ReflectionKeywordAdvice(
        suggested_keep_terms=["retrieval"]
    )
    decision = SearchControllerDecision(
        thought_summary="Keep reflection term.",
        action="search_cts",
        decision_rationale="Use retrieval because reflection suggested keeping it.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
        response_to_reflection="Accepted the suggested retrieval keep.",
    )

    assert validate_controller_decision(context=context, decision=decision) is None


def test_controller_normalizes_reflection_advice_for_inactive_term_allow_list() -> None:
    context = _controller_context(
        previous_reflection=ReflectionSummaryView(
            decision="continue",
            reflection_summary="Activate retrieval.",
        )
    )
    context.query_term_pool = [
        item.model_copy(update={"active": False}) if item.term == "retrieval" else item
        for item in context.query_term_pool
    ]
    context.latest_reflection_keyword_advice = ReflectionKeywordAdvice(
        suggested_activate_terms=["  retrieval  "]
    )
    decision = SearchControllerDecision(
        thought_summary="Normalize reflection term.",
        action="search_cts",
        decision_rationale="Use retrieval because reflection suggested activating it.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
        response_to_reflection="Accepted the suggested retrieval activation.",
    )

    assert validate_controller_decision(context=context, decision=decision) is None


def test_validate_controller_decision_rejects_query_terms_over_budget() -> None:
    context = _controller_context()
    decision = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Need recall.",
        proposed_query_terms=["python", "resume matching", "trace", "ranking"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    result = validate_controller_decision(context=context, decision=decision)
    assert result is not None
    assert "must not exceed 3 terms" in result


def test_controller_repair_avoids_pydantic_output_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    controller = ReActController(
        make_settings(),
        LoadedPrompt(name="controller", path=Path("controller.md"), content="controller prompt", sha256="hash"),
    )
    context = _controller_context(
        round_no=2,
        previous_reflection=ReflectionSummaryView(decision="continue", reflection_summary="Add one term."),
    )
    invalid = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Add one more term.",
        proposed_query_terms=["python", "resume matching", "trace"],
        proposed_filter_plan=ProposedFilterPlan(),
    )
    repaired = SearchControllerDecision(
        thought_summary=invalid.thought_summary,
        action=invalid.action,
        decision_rationale=invalid.decision_rationale,
        proposed_query_terms=invalid.proposed_query_terms,
        proposed_filter_plan=invalid.proposed_filter_plan,
        response_to_reflection="Addressed previous reflection.",
    )

    async def fake_decide_live(
        *,
        context: ControllerContext,
        prompt_cache_key: str | None = None,
        source_user_prompt: str | None = None,
    ) -> ControllerDecision:
        del context, prompt_cache_key, source_user_prompt
        return invalid

    async def fake_repair_controller_decision(
        settings, prompt, source_user_prompt, decision, reason  # noqa: ANN001
    ) -> ControllerDecision:
        del settings, prompt, source_user_prompt, decision, reason
        return repaired, None

    monkeypatch.setattr(controller, "_decide_live", fake_decide_live)
    monkeypatch.setattr("seektalent.controller.react_controller.repair_controller_decision", fake_repair_controller_decision)

    result = asyncio.run(controller.decide(context=context))

    assert result == repaired
    assert controller.last_validator_retry_count == 1
    assert controller.last_repair_attempt_count == 1
    assert controller.last_repair_succeeded is True
    assert controller.last_full_retry_count == 0


def test_controller_records_provider_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    controller = ReActController(
        make_settings(),
        LoadedPrompt(name="controller", path=Path("controller.md"), content="controller prompt", sha256="hash"),
    )
    context = _controller_context()
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Need recall.",
        proposed_query_terms=["python", "resume matching"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    class FakeAgent:
        async def run(self, prompt: str, deps: ControllerContext):  # noqa: ANN001
            del prompt, deps
            return _fake_usage_result(decision)

    monkeypatch.setattr(controller, "_get_agent", lambda prompt_cache_key=None: FakeAgent())  # noqa: ARG005

    result = asyncio.run(controller._decide_live(context=context))

    assert result == decision
    assert controller.last_provider_usage is not None
    assert controller.last_provider_usage.model_dump(mode="json") == {
        "input_tokens": 14,
        "output_tokens": 5,
        "total_tokens": 19,
        "cache_read_tokens": 9,
        "cache_write_tokens": 1,
        "details": {"reasoning_tokens": 7},
    }


def test_controller_full_retry_after_failed_semantic_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    controller = ReActController(
        make_settings(),
        LoadedPrompt(name="controller", path=Path("controller.md"), content="controller prompt", sha256="hash"),
    )
    context = _controller_context(
        round_no=2,
        previous_reflection=ReflectionSummaryView(decision="continue", reflection_summary="Add one term."),
    )
    invalid = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Add one more term.",
        proposed_query_terms=["python", "resume matching", "trace"],
        proposed_filter_plan=ProposedFilterPlan(),
    )
    still_invalid = SearchControllerDecision(
        thought_summary=invalid.thought_summary,
        action=invalid.action,
        decision_rationale=invalid.decision_rationale,
        proposed_query_terms=invalid.proposed_query_terms,
        proposed_filter_plan=invalid.proposed_filter_plan,
        response_to_reflection=" ",
    )
    valid = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Retry with fixed response.",
        proposed_query_terms=["python", "resume matching", "trace"],
        proposed_filter_plan=ProposedFilterPlan(),
        response_to_reflection="Addressed previous reflection.",
    )
    calls = {"count": 0}
    prompt_cache_keys: list[str | None] = []
    source_user_prompts: list[str | None] = []

    async def fake_decide_live(
        *,
        context: ControllerContext,
        prompt_cache_key: str | None = None,
        source_user_prompt: str | None = None,
    ) -> ControllerDecision:
        del context
        calls["count"] += 1
        prompt_cache_keys.append(prompt_cache_key)
        source_user_prompts.append(source_user_prompt)
        return invalid if calls["count"] == 1 else valid

    async def fake_repair_controller_decision(
        settings, prompt, source_user_prompt, decision, reason  # noqa: ANN001
    ) -> ControllerDecision:
        del settings, prompt, source_user_prompt, decision, reason
        return still_invalid, None

    monkeypatch.setattr(controller, "_decide_live", fake_decide_live)
    monkeypatch.setattr("seektalent.controller.react_controller.repair_controller_decision", fake_repair_controller_decision)

    result = asyncio.run(controller.decide(context=context, prompt_cache_key="controller-cache-key"))

    assert result == valid
    assert calls["count"] == 2
    assert prompt_cache_keys == ["controller-cache-key", "controller-cache-key"]
    assert source_user_prompts[0] is not None
    assert source_user_prompts == [source_user_prompts[0], source_user_prompts[0]]
    assert controller.last_full_retry_count == 1


def test_controller_aggregates_provider_usage_across_repair_and_full_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    controller = ReActController(
        make_settings(),
        LoadedPrompt(name="controller", path=Path("controller.md"), content="controller prompt", sha256="hash"),
    )
    context = _controller_context(
        round_no=2,
        previous_reflection=ReflectionSummaryView(decision="continue", reflection_summary="Add one term."),
    )
    invalid = SearchControllerDecision(
        thought_summary="Search again.",
        action="search_cts",
        decision_rationale="Add one more term.",
        proposed_query_terms=["python", "resume matching", "trace"],
        proposed_filter_plan=ProposedFilterPlan(),
    )
    still_invalid = invalid.model_copy(update={"response_to_reflection": " "})
    valid = invalid.model_copy(update={"response_to_reflection": "Addressed previous reflection."})
    first_usage = _provider_usage(
        input_tokens=14,
        output_tokens=5,
        cache_read_tokens=9,
        cache_write_tokens=1,
        reasoning_tokens=7,
    )
    repair_usage = _provider_usage(
        input_tokens=3,
        output_tokens=2,
        cache_read_tokens=1,
        cache_write_tokens=0,
        reasoning_tokens=1,
    )
    retry_usage = _provider_usage(
        input_tokens=11,
        output_tokens=4,
        cache_read_tokens=6,
        cache_write_tokens=2,
        reasoning_tokens=5,
    )
    calls = {"count": 0}

    async def fake_decide_live(
        *,
        context: ControllerContext,
        prompt_cache_key: str | None = None,
        source_user_prompt: str | None = None,
    ) -> ControllerDecision:
        del context, prompt_cache_key, source_user_prompt
        calls["count"] += 1
        controller.last_provider_usage = first_usage if calls["count"] == 1 else retry_usage
        return invalid if calls["count"] == 1 else valid

    async def fake_repair_controller_decision(
        settings, prompt, source_user_prompt, decision, reason  # noqa: ANN001
    ) -> ControllerDecision:
        del settings, prompt, source_user_prompt, decision, reason
        return still_invalid, repair_usage

    monkeypatch.setattr(controller, "_decide_live", fake_decide_live)
    monkeypatch.setattr("seektalent.controller.react_controller.repair_controller_decision", fake_repair_controller_decision)

    result = asyncio.run(controller.decide(context=context))

    assert result == valid
    assert controller.last_provider_usage is not None
    assert controller.last_provider_usage.model_dump(mode="json") == {
        "input_tokens": 28,
        "output_tokens": 11,
        "total_tokens": 39,
        "cache_read_tokens": 16,
        "cache_write_tokens": 3,
        "details": {"reasoning_tokens": 13},
    }


def test_runtime_sanitizes_premature_max_round_claims_in_stop_decision() -> None:
    settings = make_settings(runs_dir=str(Path.cwd() / ".tmp-runs"), mock_cts=True, max_rounds=5)
    runtime = WorkflowRuntime(settings)
    run_state = _run_state_with_previous_reflection()
    decision = StopControllerDecision(
        thought_summary="Stop.",
        action="stop",
        decision_rationale="The search has reached the maximum rounds (10), and the pool is stable enough.",
        response_to_reflection="Agreed that the pool is stable.",
        stop_reason="Search is exhausted: max rounds reached, two strong fit candidates identified.",
    )

    sanitized = runtime._sanitize_controller_decision(decision=decision, run_state=run_state, round_no=2)

    assert isinstance(sanitized, StopControllerDecision)
    assert "maximum rounds" not in sanitized.decision_rationale.casefold()
    assert "max rounds" not in sanitized.stop_reason.casefold()
    assert "diminishing returns" in sanitized.decision_rationale.casefold()
    assert "diminishing returns" in sanitized.stop_reason.casefold()


def test_runtime_preserves_max_round_claims_on_final_allowed_round() -> None:
    settings = make_settings(runs_dir=str(Path.cwd() / ".tmp-runs"), mock_cts=True, max_rounds=5)
    runtime = WorkflowRuntime(settings)
    run_state = _run_state_with_previous_reflection()
    decision = StopControllerDecision(
        thought_summary="Stop.",
        action="stop",
        decision_rationale="The search has reached the maximum rounds (5), and the pool is stable enough.",
        response_to_reflection="Agreed that the pool is stable.",
        stop_reason="Search is exhausted: max rounds reached, two strong fit candidates identified.",
    )

    sanitized = runtime._sanitize_controller_decision(decision=decision, run_state=run_state, round_no=5)

    assert isinstance(sanitized, StopControllerDecision)
    assert sanitized.decision_rationale == decision.decision_rationale
    assert sanitized.stop_reason == decision.stop_reason
