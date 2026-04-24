import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from seektalent.models import (
    ReflectionAdvice,
    ReflectionAdviceDraft,
    ReflectionFilterAdvice,
    ReflectionFilterAdviceDraft,
    ReflectionKeywordAdvice,
    ReflectionKeywordAdviceDraft,
    QueryTermCandidate,
)
from seektalent.prompting import LoadedPrompt
from seektalent.reflection.critic import (
    ReflectionCritic,
    materialize_reflection_advice,
    repair_reflection_stop_fields,
    validate_reflection_draft,
)
from seektalent.tracing import ProviderUsageSnapshot
from tests.settings_factory import make_settings


def _context(
    *,
    round_no: int,
    unique_new_count: int,
    query_term_pool: list[QueryTermCandidate] | None = None,
    initial_query_term_pool: list[QueryTermCandidate] | None = None,
    sent_query_terms: list[list[str]] | None = None,
    top_candidates: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        round_no=round_no,
        search_observation=SimpleNamespace(unique_new_count=unique_new_count),
        requirement_sheet=SimpleNamespace(initial_query_term_pool=initial_query_term_pool or query_term_pool or []),
        query_term_pool=query_term_pool or [],
        sent_query_history=[
            SimpleNamespace(query_terms=query_terms)
            for query_terms in (sent_query_terms or [])
        ],
        top_candidates=top_candidates or [],
    )


def _scored_candidate(
    *,
    fit_bucket: str = "fit",
    overall_score: int = 80,
    must_have_match_score: int = 70,
    risk_score: int = 20,
) -> SimpleNamespace:
    return SimpleNamespace(
        fit_bucket=fit_bucket,
        overall_score=overall_score,
        must_have_match_score=must_have_match_score,
        risk_score=risk_score,
    )


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


def test_reflection_advice_requires_stop_reason_when_stopping() -> None:
    with pytest.raises(ValidationError):
        ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(),
            suggest_stop=True,
            reflection_summary="Stop here.",
        )


def test_reflection_advice_rejects_stop_reason_when_continuing() -> None:
    with pytest.raises(ValidationError):
        ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(),
            suggest_stop=False,
            suggested_stop_reason="reflection_stop",
            reflection_summary="Continue.",
        )


def test_reflection_filter_advice_rejects_unknown_filter_fields() -> None:
    with pytest.raises(ValidationError):
        ReflectionFilterAdvice.model_validate({"suggested_add_filter_fields": ["unsupported_field"]})


def test_reflection_advice_rejects_removed_prose_fields() -> None:
    with pytest.raises(ValidationError):
        ReflectionAdvice.model_validate(
            {
                "strategy_assessment": "No more value.",
                "quality_assessment": "Top pool is strong.",
                "coverage_assessment": "Coverage is enough.",
                "keyword_advice": {},
                "filter_advice": {},
                "suggest_stop": False,
                "reflection_summary": "Continue.",
            }
        )


def test_reflection_keyword_advice_rejects_persisted_critique() -> None:
    with pytest.raises(ValidationError):
        ReflectionKeywordAdvice.model_validate({"critique": "Keep one term."})


def test_reflection_filter_advice_rejects_persisted_critique() -> None:
    with pytest.raises(ValidationError):
        ReflectionFilterAdvice.model_validate({"critique": "Keep position."})


def test_reflection_prompt_requires_untried_term_stop_discipline() -> None:
    prompt = Path("src/seektalent/prompts/reflection.md").read_text(encoding="utf-8")
    assert "`suggest_stop` is advisory only" in prompt
    assert "Do not dismiss unused concrete terms as unlikely without first trying them" in prompt


def test_reflection_rationale_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(),
            filter_advice=ReflectionFilterAdviceDraft(),
            suggest_stop=False,
            reflection_rationale="a" * 2001,
        )


def test_reflection_advice_rationale_has_generous_length_limit() -> None:
    with pytest.raises(ValidationError):
        ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(),
            reflection_rationale="a" * 2001,
            suggest_stop=False,
            reflection_summary="Continue.",
        )


def test_reflection_advice_draft_stop_field_validation_is_deferred_for_repair() -> None:
    draft = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=True,
        reflection_rationale="Top pool is good enough to stop.",
    )

    assert validate_reflection_draft(draft) == "suggested_stop_reason is required when suggest_stop is true"


def test_repair_reflection_stop_fields_nulls_reason_when_continue() -> None:
    draft = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=False,
        suggested_stop_reason="Keep searching.",
        reflection_rationale="Need more evidence.",
    )

    repaired = repair_reflection_stop_fields(draft)

    assert repaired.suggest_stop is False
    assert repaired.suggested_stop_reason is None
    assert draft.suggested_stop_reason == "Keep searching."


def test_repair_reflection_stop_fields_keeps_missing_stop_reason_for_model_repair() -> None:
    draft = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=True,
        reflection_rationale="Top pool is stable.",
    )

    repaired = repair_reflection_stop_fields(draft)

    assert repaired.suggest_stop is True
    assert repaired.suggested_stop_reason is None


def test_materialized_reflection_preserves_rationale_for_trace() -> None:
    context = _context(round_no=2, unique_new_count=10)
    advice = materialize_reflection_advice(
        context=cast(Any, context),
        draft=ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(suggested_activate_terms=["LangChain"]),
            filter_advice=ReflectionFilterAdviceDraft(),
            suggest_stop=False,
            reflection_rationale=(
                "Round 1 produced several plausible AI Agent candidates, but coverage is still narrow. "
                "Trying LangChain next should test the highest-signal unused framework term."
            ),
        ),
    )

    assert advice.reflection_rationale.startswith("Round 1 produced several plausible")
    assert "LangChain next" in advice.reflection_rationale


def test_materialized_reflection_prose_mentions_only_structured_activate_terms() -> None:
    context = _context(round_no=3, unique_new_count=0)
    advice = materialize_reflection_advice(
        context=cast(Any, context),
        draft=ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(
                suggested_activate_terms=["AutoGen"],
                suggested_deprioritize_terms=["LangChain"],
            ),
            filter_advice=ReflectionFilterAdviceDraft(suggested_drop_filter_fields=["position"]),
            suggest_stop=False,
            reflection_rationale="The search returned no new candidates, so adjust terms and remove stale filters.",
        ),
    )

    assert "AutoGen" in advice.reflection_summary
    assert "LangChain" in advice.reflection_summary
    assert "position" in advice.reflection_summary


def test_materialized_reflection_prose_does_not_invent_terms() -> None:
    context = _context(round_no=2, unique_new_count=1)
    advice = materialize_reflection_advice(
        context=cast(Any, context),
        draft=ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(),
            filter_advice=ReflectionFilterAdviceDraft(),
            suggest_stop=False,
            reflection_rationale="One new candidate is not enough evidence to change the plan.",
        ),
    )

    assert "AutoGen" not in advice.reflection_summary
    assert advice.reflection_summary == "Round 2 yielded 1 new candidate. Keywords: No keyword changes. Filters: no filter changes. Continue."


def test_materialized_reflection_forces_continue_when_untried_admitted_terms_remain() -> None:
    context = _context(
        round_no=3,
        unique_new_count=7,
        query_term_pool=[
            QueryTermCandidate(
                term="Flink",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="Paimon",
                source="jd",
                category="tooling",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        sent_query_terms=[["Flink"]],
        top_candidates=[_scored_candidate() for _ in range(6)],
    )

    advice = materialize_reflection_advice(
        context=cast(Any, context),
        draft=ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(suggested_keep_terms=["Flink"]),
            filter_advice=ReflectionFilterAdviceDraft(),
            suggest_stop=True,
            suggested_stop_reason="Search is saturated.",
            reflection_rationale="The pool is not yet strong enough and one admitted term remains untried.",
        ),
    )

    assert advice.suggest_stop is False
    assert advice.suggested_stop_reason is None
    assert "Paimon" in advice.reflection_summary
    assert "Continue: untried admitted reserve terms remain" in advice.reflection_summary


def test_materialized_reflection_uses_runtime_term_pool_for_stop_suppression() -> None:
    context = _context(
        round_no=3,
        unique_new_count=7,
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Flink",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            )
        ],
        query_term_pool=[
            QueryTermCandidate(
                term="Flink",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="Paimon",
                source="reflection",
                category="tooling",
                priority=2,
                evidence="Reflection advice",
                first_added_round=2,
            ),
        ],
        sent_query_terms=[["Flink"]],
        top_candidates=[_scored_candidate() for _ in range(6)],
    )

    advice = materialize_reflection_advice(
        context=cast(Any, context),
        draft=ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(suggested_keep_terms=["Flink"]),
            filter_advice=ReflectionFilterAdviceDraft(),
            suggest_stop=True,
            suggested_stop_reason="Search is saturated.",
            reflection_rationale="The pool is not yet strong enough and one runtime term remains untried.",
        ),
    )

    assert advice.suggest_stop is False
    assert advice.suggested_stop_reason is None
    assert "Paimon" in advice.reflection_summary


def test_materialized_reflection_allows_stop_when_top_pool_is_strong() -> None:
    context = _context(
        round_no=3,
        unique_new_count=7,
        query_term_pool=[
            QueryTermCandidate(
                term="Flink",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="Paimon",
                source="jd",
                category="tooling",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        sent_query_terms=[["Flink"]],
        top_candidates=[
            *[_scored_candidate() for _ in range(5)],
            *[_scored_candidate(overall_score=70, must_have_match_score=65) for _ in range(5)],
        ],
    )

    advice = materialize_reflection_advice(
        context=cast(Any, context),
        draft=ReflectionAdviceDraft(
            keyword_advice=ReflectionKeywordAdviceDraft(suggested_keep_terms=["Flink"]),
            filter_advice=ReflectionFilterAdviceDraft(),
            suggest_stop=True,
            suggested_stop_reason="Search is saturated.",
            reflection_rationale="The top pool is strong enough and the remaining term is unlikely to change the result.",
        ),
    )

    assert advice.suggest_stop is True
    assert advice.suggested_stop_reason == "Search is saturated."


def test_reflection_critic_repairs_stop_reason_with_model(monkeypatch: pytest.MonkeyPatch) -> None:
    critic = ReflectionCritic(
        make_settings(),
        LoadedPrompt(name="reflection", path=Path("reflection.md"), content="reflection prompt", sha256="hash"),
    )
    context = cast(Any, _context(round_no=2, unique_new_count=6))
    monkeypatch.setattr("seektalent.reflection.critic.render_reflection_prompt", lambda context: "VISIBLE REFLECTION PROMPT")
    invalid = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=True,
        reflection_rationale="Top pool is stable.",
    )
    repaired = invalid.model_copy(update={"suggested_stop_reason": "Search is saturated."})

    async def fake_reflect_live(*, context, prompt_cache_key=None, source_user_prompt=None):  # noqa: ANN001
        del context, prompt_cache_key, source_user_prompt
        return invalid

    async def fake_repair_reflection_draft(settings, prompt, source_user_prompt, draft, reason):  # noqa: ANN001
        del settings, prompt, source_user_prompt, draft, reason
        return repaired, None

    monkeypatch.setattr(critic, "_reflect_live", fake_reflect_live)
    monkeypatch.setattr("seektalent.reflection.critic.repair_reflection_draft", fake_repair_reflection_draft)

    advice = asyncio.run(critic.reflect(context=context))

    assert advice.suggest_stop is True
    assert advice.suggested_stop_reason == "Search is saturated."
    assert critic.last_validator_retry_count == 1
    assert critic.last_validator_retry_reasons == ["suggested_stop_reason is required when suggest_stop is true"]
    assert critic.last_repair_attempt_count == 1
    assert critic.last_repair_succeeded is True
    assert critic.last_repair_reason == "suggested_stop_reason is required when suggest_stop is true"
    assert critic.last_full_retry_count == 0


def test_reflection_critic_deterministic_cleanup_does_not_count_as_model_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    critic = ReflectionCritic(
        make_settings(),
        LoadedPrompt(name="reflection", path=Path("reflection.md"), content="reflection prompt", sha256="hash"),
    )
    context = cast(Any, _context(round_no=2, unique_new_count=6))
    monkeypatch.setattr("seektalent.reflection.critic.render_reflection_prompt", lambda context: "VISIBLE REFLECTION PROMPT")
    draft = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=False,
        suggested_stop_reason="Continue searching.",
        reflection_rationale="Need more evidence.",
    )

    async def fake_reflect_live(*, context, prompt_cache_key=None, source_user_prompt=None):  # noqa: ANN001
        del context, prompt_cache_key, source_user_prompt
        return draft

    async def fail_if_model_repair_called(settings, prompt, source_user_prompt, draft, reason):  # noqa: ANN001
        del settings, prompt, source_user_prompt, draft, reason
        raise AssertionError("model repair should not run for deterministic stop-field cleanup")

    monkeypatch.setattr(critic, "_reflect_live", fake_reflect_live)
    monkeypatch.setattr("seektalent.reflection.critic.repair_reflection_draft", fail_if_model_repair_called)

    advice = asyncio.run(critic.reflect(context=context))

    assert advice.suggest_stop is False
    assert advice.suggested_stop_reason is None
    assert critic.last_validator_retry_count == 1
    assert critic.last_validator_retry_reasons == ["suggested_stop_reason must be null when suggest_stop is false"]
    assert critic.last_repair_attempt_count == 0
    assert critic.last_repair_succeeded is False
    assert critic.last_repair_reason is None
    assert critic.last_full_retry_count == 0


def test_reflection_critic_full_retry_after_failed_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    critic = ReflectionCritic(
        make_settings(),
        LoadedPrompt(name="reflection", path=Path("reflection.md"), content="reflection prompt", sha256="hash"),
    )
    context = cast(Any, _context(round_no=2, unique_new_count=6))
    monkeypatch.setattr("seektalent.reflection.critic.render_reflection_prompt", lambda context: "VISIBLE REFLECTION PROMPT")
    invalid = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=True,
        reflection_rationale="Top pool is stable.",
    )
    valid = invalid.model_copy(update={"suggested_stop_reason": "Search is saturated."})
    calls = {"count": 0}
    prompt_cache_keys: list[str | None] = []
    source_user_prompts: list[str | None] = []

    async def fake_reflect_live(*, context, prompt_cache_key=None, source_user_prompt=None):  # noqa: ANN001
        del context
        calls["count"] += 1
        prompt_cache_keys.append(prompt_cache_key)
        source_user_prompts.append(source_user_prompt)
        return invalid if calls["count"] == 1 else valid

    async def fake_repair_reflection_draft(settings, prompt, source_user_prompt, draft, reason):  # noqa: ANN001
        del settings, prompt, source_user_prompt, draft, reason
        return invalid, None

    monkeypatch.setattr(critic, "_reflect_live", fake_reflect_live)
    monkeypatch.setattr("seektalent.reflection.critic.repair_reflection_draft", fake_repair_reflection_draft)

    advice = asyncio.run(critic.reflect(context=context, prompt_cache_key="reflection-cache-key"))

    assert advice.suggest_stop is True
    assert advice.suggested_stop_reason == "Search is saturated."
    assert calls["count"] == 2
    assert prompt_cache_keys == ["reflection-cache-key", "reflection-cache-key"]
    assert source_user_prompts[0] is not None
    assert source_user_prompts == [source_user_prompts[0], source_user_prompts[0]]
    assert critic.last_validator_retry_count == 1
    assert critic.last_repair_attempt_count == 1
    assert critic.last_repair_succeeded is False
    assert critic.last_repair_reason == "suggested_stop_reason is required when suggest_stop is true"
    assert critic.last_full_retry_count == 1


def test_reflection_critic_aggregates_provider_usage_across_model_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    critic = ReflectionCritic(
        make_settings(),
        LoadedPrompt(name="reflection", path=Path("reflection.md"), content="reflection prompt", sha256="hash"),
    )
    context = cast(Any, _context(round_no=2, unique_new_count=6))
    monkeypatch.setattr("seektalent.reflection.critic.render_reflection_prompt", lambda context: "VISIBLE REFLECTION PROMPT")
    invalid = ReflectionAdviceDraft(
        keyword_advice=ReflectionKeywordAdviceDraft(),
        filter_advice=ReflectionFilterAdviceDraft(),
        suggest_stop=True,
        reflection_rationale="Top pool is stable.",
    )
    repaired = invalid.model_copy(update={"suggested_stop_reason": "Search is saturated."})
    live_usage = _provider_usage(
        input_tokens=9,
        output_tokens=4,
        cache_read_tokens=5,
        cache_write_tokens=1,
        reasoning_tokens=3,
    )
    repair_usage = _provider_usage(
        input_tokens=4,
        output_tokens=2,
        cache_read_tokens=1,
        cache_write_tokens=2,
        reasoning_tokens=1,
    )

    async def fake_reflect_live(*, context, prompt_cache_key=None, source_user_prompt=None):  # noqa: ANN001
        del context, prompt_cache_key, source_user_prompt
        critic.last_provider_usage = live_usage
        return invalid

    async def fake_repair_reflection_draft(settings, prompt, source_user_prompt, draft, reason):  # noqa: ANN001
        del settings, prompt, source_user_prompt, draft, reason
        return repaired, repair_usage

    monkeypatch.setattr(critic, "_reflect_live", fake_reflect_live)
    monkeypatch.setattr("seektalent.reflection.critic.repair_reflection_draft", fake_repair_reflection_draft)

    advice = asyncio.run(critic.reflect(context=context))

    assert advice.suggest_stop is True
    assert critic.last_provider_usage is not None
    assert critic.last_provider_usage.model_dump(mode="json") == {
        "input_tokens": 13,
        "output_tokens": 6,
        "total_tokens": 19,
        "cache_read_tokens": 6,
        "cache_write_tokens": 3,
        "details": {"reasoning_tokens": 4},
    }
