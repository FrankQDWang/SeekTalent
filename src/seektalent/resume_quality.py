from __future__ import annotations

import re

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model
from seektalent.models import NormalizedResume, ScoredCandidate
from seektalent.prompting import LoadedPrompt, json_block


def clean_quality_comment(text: str) -> str:
    cleaned = re.sub(r"[*_`#>]+", "", text)
    cleaned = " ".join(cleaned.split()).strip(" -:：;；,，。\"'“”")
    if len(cleaned) <= 80:
        return cleaned
    return cleaned[:80].rstrip(" ，,；;、")


def build_quality_comment_payload(
    *,
    round_no: int,
    query_terms: list[str],
    candidates: list[ScoredCandidate],
    normalized_store: dict[str, NormalizedResume],
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for candidate in candidates[:5]:
        resume = normalized_store.get(candidate.resume_id)
        entries.append(
            {
                "resume_id": candidate.resume_id,
                "score": candidate.overall_score,
                "fit_bucket": candidate.fit_bucket,
                "resume_summary": resume.compact_summary() if resume is not None else "",
                "skills": resume.skills[:8] if resume is not None else [],
                "reasoning_summary": candidate.reasoning_summary,
                "strengths": candidate.strengths[:3],
                "weaknesses": candidate.weaknesses[:3],
                "risk_flags": candidate.risk_flags[:3],
            }
        )
    return {"round_no": round_no, "query_terms": query_terms, "candidates": entries}


class ResumeQualityCommenter:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _build_agent(self) -> Agent[None, str]:
        return Agent(
            model=build_model(self.settings.effective_tui_summary_model),
            output_type=str,
            system_prompt=self.prompt.content,
            retries=0,
            output_retries=0,
        )

    async def comment(
        self,
        *,
        round_no: int,
        query_terms: list[str],
        candidates: list[ScoredCandidate],
        normalized_store: dict[str, NormalizedResume],
    ) -> str:
        payload = build_quality_comment_payload(
            round_no=round_no,
            query_terms=query_terms,
            candidates=candidates,
            normalized_store=normalized_store,
        )
        if not payload["candidates"]:
            return ""
        result = await self._build_agent().run(json_block("ROUND_RESUME_QUALITY_CONTEXT", payload))
        return clean_quality_comment(result.output)


__all__ = ["ResumeQualityCommenter", "build_quality_comment_payload", "clean_quality_comment"]
