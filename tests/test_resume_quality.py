from __future__ import annotations

from pathlib import Path
from typing import cast

from seektalent.models import NormalizedResume, ScoredCandidate
from seektalent.prompting import LoadedPrompt
from seektalent.resume_quality import ResumeQualityCommenter
from seektalent.resume_quality import build_quality_comment_payload, clean_quality_comment
from tests.settings_factory import make_settings


def _scored_candidate(resume_id: str, *, score: int) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=score,
        must_have_match_score=score - 2,
        preferred_match_score=70,
        risk_score=8,
        risk_flags=["跨度略大"] if score < 90 else [],
        reasoning_summary="Python、检索和 LLM 工程经验匹配。",
        evidence=["Python", "retrieval"],
        confidence="high",
        matched_must_haves=["Python"],
        missing_must_haves=[],
        matched_preferences=["LLM"],
        negative_signals=[],
        strengths=["有生产 AI 系统经验"],
        weaknesses=["候选人管理经验不明确"] if score < 90 else [],
        source_round=1,
    )


def _normalized_resume(resume_id: str) -> NormalizedResume:
    return NormalizedResume(
        resume_id=resume_id,
        dedup_key=resume_id,
        candidate_name=f"候选人 {resume_id}",
        current_title="AI 平台工程师",
        current_company="Example Co",
        years_of_experience=6,
        locations=["上海"],
        education_summary="复旦大学 计算机 本科",
        skills=["Python", "LLM", "检索"],
        key_achievements=["搭建过候选人搜索链路"],
        raw_text_excerpt="负责 AI agent、RAG 检索和业务工作流落地。",
        completeness_score=92,
        source_round=1,
    )


def test_default_tui_summary_model_reuses_scoring_model() -> None:
    settings = make_settings(scoring_model="openai-chat:deepseek-v3.2")

    assert settings.tui_summary_model is None
    assert settings.effective_tui_summary_model == "openai-chat:deepseek-v3.2"


def test_clean_quality_comment_returns_single_short_plain_text() -> None:
    comment = clean_quality_comment(
        "  **整体质量较好**\n\n但有两份简历信息不足，需要人工复核；"
        "其余候选人在 Python、检索和 LLM 工程上较贴合岗位，建议优先看高分 fit。"
    )

    assert "\n" not in comment
    assert "*" not in comment
    assert len(comment) <= 80
    assert comment.startswith("整体质量较好")


def test_quality_payload_keeps_top_five_scored_resume_context() -> None:
    candidates = [_scored_candidate(f"resume-{index}", score=95 - index) for index in range(6)]
    normalized_store = {candidate.resume_id: _normalized_resume(candidate.resume_id) for candidate in candidates}

    payload = build_quality_comment_payload(
        round_no=2,
        query_terms=["python", "llm"],
        candidates=candidates,
        normalized_store=normalized_store,
    )

    assert payload["round_no"] == 2
    assert payload["query_terms"] == ["python", "llm"]
    payload_candidates = cast(list[dict[str, object]], payload["candidates"])
    assert len(payload_candidates) == 5
    first_candidate = payload_candidates[0]
    assert first_candidate["resume_id"] == "resume-0"
    assert first_candidate["score"] == 95
    assert first_candidate["resume_summary"] == "AI 平台工程师 | Example Co | 上海 | 6y"
    assert first_candidate["skills"] == ["Python", "LLM", "检索"]


def test_resume_quality_commenter_uses_loaded_prompt() -> None:
    prompt = LoadedPrompt(name="tui_summary", path=Path("tui_summary.md"), content="summary prompt", sha256="hash")

    commenter = ResumeQualityCommenter(make_settings(), prompt)

    assert commenter.prompt is prompt
