from __future__ import annotations

import asyncio
from collections import Counter

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import ReflectionDecision, ScoredCandidate, SearchStrategy, unique_strings
from cv_match.prompting import LoadedPrompt, json_block

REFLECTION_KEYWORDS = ("trace", "recruiting", "resume", "observability", "dedup")


class ReflectionAgent:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.use_mock_backend = settings.llm_backend_mode != "openai-responses"
        self.agent: Agent[None, ReflectionDecision] | None = None
        if not self.use_mock_backend:
            self.agent = Agent(
                model=OpenAIResponsesModel(settings.reflection_model),
                output_type=ReflectionDecision,
                system_prompt=prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )

    def reflect(
        self,
        *,
        round_no: int,
        strategy: SearchStrategy,
        new_candidate_summaries: list[str],
        scored_candidates: list[ScoredCandidate],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        shortage_count: int,
        scoring_failure_count: int,
    ) -> ReflectionDecision:
        if self.use_mock_backend:
            return self._reflect_mock(
                round_no=round_no,
                strategy=strategy,
                new_candidate_summaries=new_candidate_summaries,
                scored_candidates=scored_candidates,
                top_candidates=top_candidates,
                dropped_candidates=dropped_candidates,
                shortage_count=shortage_count,
                scoring_failure_count=scoring_failure_count,
            )
        return asyncio.run(
            self._reflect_live(
                round_no=round_no,
                strategy=strategy,
                new_candidate_summaries=new_candidate_summaries,
                scored_candidates=scored_candidates,
                top_candidates=top_candidates,
                dropped_candidates=dropped_candidates,
                shortage_count=shortage_count,
                scoring_failure_count=scoring_failure_count,
            )
        )

    async def _reflect_live(
        self,
        *,
        round_no: int,
        strategy: SearchStrategy,
        new_candidate_summaries: list[str],
        scored_candidates: list[ScoredCandidate],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        shortage_count: int,
        scoring_failure_count: int,
    ) -> ReflectionDecision:
        assert self.agent is not None
        prompt = "\n\n".join(
            [
                json_block(
                    "ROUND_CONTEXT",
                    {
                        "round_no": round_no,
                        "strategy": strategy.model_dump(mode="json"),
                        "new_candidate_summaries": new_candidate_summaries,
                        "scored_candidates": [item.model_dump(mode="json") for item in scored_candidates],
                        "top_candidates": [item.model_dump(mode="json") for item in top_candidates],
                        "dropped_candidates": [item.model_dump(mode="json") for item in dropped_candidates],
                        "shortage_count": shortage_count,
                        "scoring_failure_count": scoring_failure_count,
                    },
                )
            ]
        )
        result = await self.agent.run(prompt)
        return result.output

    def _reflect_mock(
        self,
        *,
        round_no: int,
        strategy: SearchStrategy,
        new_candidate_summaries: list[str],
        scored_candidates: list[ScoredCandidate],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        shortage_count: int,
        scoring_failure_count: int,
    ) -> ReflectionDecision:
        fit_count = sum(1 for item in top_candidates if item.fit_bucket == "fit")
        coverage = (
            f"Top pool has {fit_count} fit resumes; "
            f"{sum(len(item.matched_must_haves) for item in top_candidates)} total must-have hits across top candidates."
        )
        drop_counter = Counter(flag for item in dropped_candidates for flag in item.risk_flags)
        dropped_reason = ", ".join(f"{key}:{value}" for key, value in drop_counter.most_common(3)) or "no dominant reject pattern"
        additions = [
            keyword
            for keyword in REFLECTION_KEYWORDS
            if keyword not in strategy.preferred_keywords
            and keyword not in strategy.must_have_keywords
            and any(keyword in summary.casefold() for summary in new_candidate_summaries)
        ]
        negative_additions = [
            keyword
            for keyword in ("frontend", "sales", "research")
            if keyword not in strategy.negative_keywords
            and any(keyword in reason for reason in drop_counter)
        ]
        if round_no < self.settings.min_rounds:
            decision = "continue"
            stop_reason = None
        elif fit_count >= 5:
            decision = "stop"
            stop_reason = "enough_high_fit_candidates"
        elif shortage_count > 0:
            decision = "stop"
            stop_reason = "insufficient_new_candidates"
        elif round_no >= self.settings.max_rounds:
            decision = "stop"
            stop_reason = "max_rounds_reached"
        else:
            decision = "continue"
            stop_reason = None
        return ReflectionDecision(
            strategy_assessment=f"Current strategy retrieved {len(scored_candidates)} scored resumes with {fit_count} fit candidates in top5.",
            quality_assessment=f"Dropped candidate pattern: {dropped_reason}. Failures this round: {scoring_failure_count}.",
            coverage_assessment=coverage,
            adjust_keywords=unique_strings(additions[:2]),
            adjust_negative_keywords=unique_strings(negative_additions[:2]),
            adjust_hard_filters=list(strategy.hard_filters),
            adjust_soft_filters=list(strategy.soft_filters),
            decision=decision,
            stop_reason=stop_reason,
            reflection_summary=(
                "Keep hard filters stable, add missing retrieval hints, and stop only after enough strong fits "
                "or when new candidates dry up."
            ),
        )
