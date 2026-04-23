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
    ScoredCandidate,
    ScoredCandidateDraft,
    ScoringContext,
    ScoringPolicy,
)
from seektalent.prompting import LoadedPrompt
from seektalent.runtime.exact_llm_cache import get_cached_json, put_cached_json
from seektalent.scoring.scorer import ResumeScorer, scoring_cache_key
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
            role_title="Senior Python Engineer",
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
    snapshots = _read_jsonl(tracer.run_dir / "rounds/round_01/scoring_calls.jsonl")
    assert snapshots[0]["cache_hit"] is True
    assert snapshots[0]["cache_key"] == cache_key
    assert "provider_usage" not in snapshots[0]


def test_scoring_cache_key_changes_when_reasoning_effort_changes(tmp_path: Path) -> None:
    prompt = _prompt()
    context = _context()
    low_settings = _settings(tmp_path, reasoning_effort="low")
    high_settings = _settings(tmp_path, reasoning_effort="high")
    user_prompt = ResumeScorer(low_settings, prompt).rendered_prompt_for_cache(context)

    low_key = scoring_cache_key(low_settings, prompt, context, user_prompt)
    high_key = scoring_cache_key(high_settings, prompt, context, user_prompt)

    assert low_key != high_key


def test_scoring_prompt_cache_key_is_recorded_on_live_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
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
    snapshots = _read_jsonl(tracer.run_dir / "rounds/round_01/scoring_calls.jsonl")
    assert snapshots[0]["cache_hit"] is False
    assert snapshots[0]["prompt_cache_key"] == built_prompt_cache_keys[0]
    assert snapshots[0]["prompt_cache_retention"] == "12h"
    assert snapshots[0]["provider_usage"] == _provider_usage().model_dump(mode="json")
    assert snapshots[0]["cached_input_tokens"] == 11
