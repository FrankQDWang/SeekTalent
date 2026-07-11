from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from seektalent.models import (
    HardConstraintSlots,
    NormalizedResume,
    PreferenceSlots,
    ResumeCandidate,
    ScoredCandidate,
    ScoredCandidateDraft,
    ScoringContext,
    ScoringPolicy,
)
from seektalent.normalization import normalize_resume
from seektalent.prompting import LoadedPrompt
from seektalent.cache.exact_llm_cache import get_cached_json, put_cached_json
from seektalent.scoring.scorer import ResumeScorer, scoring_cache_key
from seektalent.scoring.weighted_score import (
    ScoreDimensionApplicability,
    calculate_overall_score,
    score_dimension_applicability,
)
from seektalent.tracing import ProviderUsageSnapshot, RunTracer
from tests.settings_factory import make_settings


def _prompt() -> LoadedPrompt:
    return LoadedPrompt(
        name="scoring",
        path=Path("scoring.md"),
        content="scoring system prompt",
        sha256="scoring-prompt-hash",
    )


def _settings(tmp_path: Path, **overrides: object):
    return make_settings(
        llm_cache_dir=str(tmp_path / "cache"),
        runs_dir=str(tmp_path / "runs"),
        **overrides,
    )


def _context() -> ScoringContext:
    return ScoringContext(
        round_no=1,
        scoring_policy=ScoringPolicy(
            job_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python"],
            preferred_capabilities=["retrieval"],
            exclusion_signals=[],
            hard_constraints=HardConstraintSlots(locations=["Shanghai"]),
            preferences=PreferenceSlots(),
            scoring_rationale="Score Python fit first.",
        ),
        normalized_resume=NormalizedResume(
            resume_id="resume-1",
            dedup_key="resume-1",
            candidate_name="Alice",
            current_title="Python Engineer",
            current_company="Example Co",
            years_of_experience=5,
            locations=["Shanghai"],
            education_summary="BS",
            skills=["python", "retrieval"],
            raw_text_excerpt="Built retrieval ranking services.",
            completeness_score=90,
            source_round=1,
        ),
        requirement_sheet_sha256="requirement-sheet-hash",
    )


def _draft() -> ScoredCandidateDraft:
    return ScoredCandidateDraft(
        fit_bucket="fit",
        overall_score=88,
        must_have_match_score=92,
        preferred_match_score=80,
        risk_score=20,
        risk_flags=[],
        reasoning_summary="Strong fit for Python retrieval role.",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["retrieval"],
        negative_signals=[],
    )


def _scored_candidate() -> ScoredCandidate:
    return ScoredCandidate(
        resume_id="resume-1",
        source_round=1,
        fit_bucket="fit",
        overall_score=88,
        must_have_match_score=92,
        preferred_match_score=80,
        risk_score=20,
        risk_flags=[],
        reasoning_summary="Strong fit for Python retrieval role.",
        evidence=["python", "retrieval"],
        confidence="high",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["retrieval"],
        negative_signals=[],
        strengths=["Matched must-have: python", "Matched preference: retrieval"],
        weaknesses=[],
    )


def _provider_usage() -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        input_tokens=20,
        output_tokens=6,
        total_tokens=26,
        cache_read_tokens=11,
        cache_write_tokens=2,
        details={"reasoning_tokens": 4},
    )


def _policy(*, preferred: bool, risk: bool) -> ScoringPolicy:
    return ScoringPolicy(
        job_title="AI Agent 工程师",
        role_summary="构建生产级 Agent 系统",
        must_have_capabilities=["Multi-Agent 架构"],
        preferred_capabilities=["B2B 电商"] if preferred else [],
        exclusion_signals=["没有软件工程经验"] if risk else [],
        preferences=PreferenceSlots(),
        scoring_rationale="必须项优先",
    )


@pytest.mark.parametrize(
    ("preferred", "risk", "expected"),
    [
        (True, True, ScoreDimensionApplicability(preferred=True, risk=True)),
        (True, False, ScoreDimensionApplicability(preferred=True, risk=False)),
        (False, True, ScoreDimensionApplicability(preferred=False, risk=True)),
        (False, False, ScoreDimensionApplicability(preferred=False, risk=False)),
    ],
)
def test_requirement_sheet_controls_dimension_applicability(preferred, risk, expected) -> None:
    assert score_dimension_applicability(_policy(preferred=preferred, risk=risk)) == expected


@pytest.mark.parametrize(
    ("preferred_capabilities", "preferences"),
    [
        (["B2B 电商"], PreferenceSlots()),
        ([], PreferenceSlots(preferred_locations=["上海"])),
        ([], PreferenceSlots(preferred_companies=["甲骨文"])),
        ([], PreferenceSlots(preferred_domains=["跨境电商"])),
        ([], PreferenceSlots(preferred_backgrounds=["创业团队"])),
    ],
)
def test_each_preference_source_independently_enables_preferred_dimension(
    preferred_capabilities,
    preferences,
) -> None:
    policy = _policy(preferred=False, risk=False).model_copy(
        update={
            "preferred_capabilities": preferred_capabilities,
            "preferences": preferences,
        }
    )

    assert score_dimension_applicability(policy).preferred is True


@pytest.mark.parametrize(
    ("applicability", "preferred", "risk", "expected"),
    [
        (ScoreDimensionApplicability(preferred=True, risk=True), 80, 20, 77),
        (ScoreDimensionApplicability(preferred=True, risk=False), 80, None, 76),
        (ScoreDimensionApplicability(preferred=False, risk=True), None, 20, 76),
        (ScoreDimensionApplicability(preferred=False, risk=False), None, None, 75),
    ],
)
def test_total_score_renormalizes_only_applicable_dimensions(applicability, preferred, risk, expected) -> None:
    assert calculate_overall_score(
        must_have_match_score=75,
        preferred_match_score=preferred,
        risk_score=risk,
        applicability=applicability,
    ) == expected


def test_total_score_rejects_missing_or_extra_dimension_values() -> None:
    with pytest.raises(ValueError, match="preferred_score_required"):
        calculate_overall_score(
            must_have_match_score=80,
            preferred_match_score=None,
            risk_score=None,
            applicability=ScoreDimensionApplicability(preferred=True, risk=False),
        )
    with pytest.raises(ValueError, match="risk_score_not_applicable"):
        calculate_overall_score(
            must_have_match_score=80,
            preferred_match_score=None,
            risk_score=10,
            applicability=ScoreDimensionApplicability(preferred=False, risk=False),
        )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_scoring_cache_miss_calls_provider_and_stores_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prompt = _prompt()
    context = _context()
    scorer = ResumeScorer(settings, prompt)
    provider_calls = 0

    async def fake_score_one_live(*, prompt: str, agent):  # noqa: ANN001
        nonlocal provider_calls
        del prompt, agent
        provider_calls += 1
        return _draft(), _provider_usage()

    monkeypatch.setattr(scorer, "_score_one_live", fake_score_one_live)

    tracer = RunTracer(tmp_path / "runs")
    try:
        scored, failures = asyncio.run(
            scorer._score_candidates_parallel(
                contexts=[context],
                tracer=tracer,
                agent=cast(Any, object()),
            )
        )
    finally:
        tracer.close()

    assert provider_calls == 1
    assert failures == []
    assert [item.resume_id for item in scored] == ["resume-1"]

    user_prompt = scorer.rendered_prompt_for_cache(context)
    cache_key = scoring_cache_key(settings, prompt, context, user_prompt)
    cached = get_cached_json(settings, namespace="scoring", key=cache_key)
    assert cached == scored[0].model_dump(mode="json")


def test_scoring_propagates_safe_score_metadata_from_resume_candidate_raw(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prompt = _prompt()
    candidate = ResumeCandidate(
        resume_id="resume-detail-1",
        dedup_key="resume-detail-1",
        source_round=2,
        search_text="Python backend engineer",
        raw={
            "title": "Python Engineer",
            "company": "Example Co",
            "skills": ["python", "retrieval"],
            "score_evidence_source": "detail_enriched",
            "card_scorecard_ref": "artifact:scorecards/card/resume-detail-1.json",
            "detail_scorecard_ref": "artifact:scorecards/detail/resume-detail-1.json",
            "score_delta": 11,
            "detail_open_reason": "detail_budget_available",
            "detail_open_policy_version": "detail-policy-v1",
            "detail_scorecard": {"raw": "must-not-propagate"},
        },
    )
    context = _context().model_copy(update={"normalized_resume": normalize_resume(candidate), "round_no": 2})
    scorer = ResumeScorer(settings, prompt)

    async def fake_score_one_live(*, prompt: str, agent):  # noqa: ANN001
        del prompt, agent
        return _draft(), _provider_usage()

    monkeypatch.setattr(scorer, "_score_one_live", fake_score_one_live)

    tracer = RunTracer(tmp_path / "runs")
    try:
        scored, failures = asyncio.run(
            scorer._score_candidates_parallel(
                contexts=[context],
                tracer=tracer,
                agent=cast(Any, object()),
            )
        )
    finally:
        tracer.close()

    assert failures == []
    scorecard = scored[0]
    assert scorecard.score_evidence_source == "detail_enriched"
    assert scorecard.card_scorecard_ref == "artifact:scorecards/card/resume-detail-1.json"
    assert scorecard.detail_scorecard_ref == "artifact:scorecards/detail/resume-detail-1.json"
    assert scorecard.score_delta == 11
    assert scorecard.detail_open_reason == "detail_budget_available"
    assert scorecard.detail_open_policy_version == "detail-policy-v1"
    assert "detail_scorecard" not in scorecard.model_dump(mode="json")


def test_scoring_cache_hit_skips_provider_and_writes_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    prompt = _prompt()
    context = _context()
    scorer = ResumeScorer(settings, prompt)

    user_prompt = scorer.rendered_prompt_for_cache(context)
    cache_key = scoring_cache_key(settings, prompt, context, user_prompt)
    put_cached_json(
        settings,
        namespace="scoring",
        key=cache_key,
        payload=_scored_candidate().model_dump(mode="json"),
    )

    async def fail_if_called(*, prompt: str, agent):  # noqa: ANN001
        del prompt, agent
        raise AssertionError("provider call should be skipped on scoring cache hit")

    monkeypatch.setattr(scorer, "_score_one_live", fail_if_called)

    tracer = RunTracer(tmp_path / "runs")
    try:
        scored, failures = asyncio.run(
            scorer._score_candidates_parallel(
                contexts=[context],
                tracer=tracer,
                agent=cast(Any, object()),
            )
        )
    finally:
        tracer.close()

    assert failures == []
    assert [item.resume_id for item in scored] == ["resume-1"]
    snapshots = _read_jsonl(tracer.run_dir / "rounds/01/scoring/scoring_calls.jsonl")
    assert snapshots[0]["cache_hit"] is True
    assert snapshots[0]["cache_key"] == cache_key
    assert "provider_usage" not in snapshots[0]


def test_scoring_cache_key_ignores_global_reasoning_effort_changes(tmp_path: Path) -> None:
    prompt = _prompt()
    context = _context()
    low_settings = _settings(tmp_path, reasoning_effort="low")
    high_settings = _settings(tmp_path, reasoning_effort="high")
    user_prompt = ResumeScorer(low_settings, prompt).rendered_prompt_for_cache(context)

    low_key = scoring_cache_key(low_settings, prompt, context, user_prompt)
    high_key = scoring_cache_key(high_settings, prompt, context, user_prompt)

    assert low_key == high_key


def test_scoring_build_agent_uses_resolved_stage_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    resolved_config = object()
    built: dict[str, object] = {}

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, item):  # noqa: ANN206, ANN001
            return cls

        def __init__(self, **kwargs):  # noqa: ANN003
            built.update(kwargs)

    monkeypatch.setattr("seektalent.scoring.scorer.resolve_stage_model_config", lambda settings, *, stage: resolved_config)
    monkeypatch.setattr("seektalent.scoring.scorer.build_model", lambda config: ("model", config))
    monkeypatch.setattr(
        "seektalent.scoring.scorer.build_output_spec",
        lambda config, model, output_type: ("output", config, model, output_type),
    )
    monkeypatch.setattr(
        "seektalent.scoring.scorer.build_model_settings",
        lambda config, *, prompt_cache_key=None: {"config": config, "prompt_cache_key": prompt_cache_key},
    )
    monkeypatch.setattr("seektalent.scoring.scorer.Agent", FakeAgent)

    scorer = ResumeScorer(settings, _prompt())
    scorer._build_agent(prompt_cache_key="prompt-cache-key")

    assert scorer._model_config is resolved_config
    assert built["model"] == ("model", resolved_config)
    assert built["output_type"] == (
        "output",
        resolved_config,
        ("model", resolved_config),
        ScoredCandidateDraft,
    )
    assert built["model_settings"] == {
        "config": resolved_config,
        "prompt_cache_key": "prompt-cache-key",
    }
    assert built["retries"] == 0
    assert built["output_retries"] == 2


def test_scoring_prompt_cache_key_is_recorded_on_live_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        text_llm_protocol_family="openai_chat_completions_compatible",
        text_llm_endpoint_kind="bailian_openai_chat_completions",
        openai_prompt_cache_enabled=True,
        openai_prompt_cache_retention="12h",
    )
    scorer = ResumeScorer(settings, _prompt())
    context = _context()
    built_prompt_cache_keys: list[str | None] = []

    def fake_build_agent(prompt_cache_key: str | None = None) -> object:
        built_prompt_cache_keys.append(prompt_cache_key)
        return object()

    async def fake_score_one_live(*, prompt: str, agent):  # noqa: ANN001
        del prompt, agent
        return _draft(), _provider_usage()

    monkeypatch.setattr(scorer, "_build_agent", fake_build_agent)
    monkeypatch.setattr(scorer, "_score_one_live", fake_score_one_live)

    tracer = RunTracer(tmp_path / "runs")
    try:
        scored, failures = asyncio.run(scorer.score_candidates_parallel(contexts=[context], tracer=tracer))
    finally:
        tracer.close()

    assert failures == []
    assert [item.resume_id for item in scored] == ["resume-1"]
    assert len(built_prompt_cache_keys) == 1
    assert built_prompt_cache_keys[0] is not None
    snapshots = _read_jsonl(tracer.run_dir / "rounds/01/scoring/scoring_calls.jsonl")
    assert snapshots[0]["cache_hit"] is False
    assert snapshots[0]["prompt_cache_key"] == built_prompt_cache_keys[0]
    assert snapshots[0]["prompt_cache_retention"] == "12h"
    assert snapshots[0]["provider_usage"] == _provider_usage().model_dump(mode="json")
    assert snapshots[0]["cached_input_tokens"] == 11
