from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import (
    ReflectionDecision,
    ScoredCandidate,
    SearchAttempt,
    SearchObservation,
    SearchStrategy,
)
from cv_match.prompting import LoadedPrompt, json_block


class ReflectionCritic:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.agent: Agent[None, ReflectionDecision] | None = None

    def _get_agent(self) -> Agent[None, ReflectionDecision]:
        if self.agent is None:
            self.agent = Agent(
                model=OpenAIResponsesModel(self.settings.reflection_model),
                output_type=ReflectionDecision,
                system_prompt=self.prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=self.settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )
        return self.agent

    def reflect(
        self,
        *,
        round_no: int,
        strategy: SearchStrategy,
        search_observation: SearchObservation,
        search_attempts: list[SearchAttempt],
        new_candidate_summaries: list[str],
        scored_candidates: list[ScoredCandidate],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        shortage_count: int,
        scoring_failure_count: int,
    ) -> ReflectionDecision:
        return asyncio.run(
            self._reflect_live(
                round_no=round_no,
                strategy=strategy,
                search_observation=search_observation,
                search_attempts=search_attempts,
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
        search_observation: SearchObservation,
        search_attempts: list[SearchAttempt],
        new_candidate_summaries: list[str],
        scored_candidates: list[ScoredCandidate],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        shortage_count: int,
        scoring_failure_count: int,
    ) -> ReflectionDecision:
        prompt = "\n\n".join(
            [
                json_block(
                    "ROUND_CONTEXT",
                    {
                        "round_no": round_no,
                        "strategy": strategy.model_dump(mode="json"),
                        "search_observation": search_observation.model_dump(mode="json"),
                        "search_attempts": [item.model_dump(mode="json") for item in search_attempts],
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
        result = await asyncio.wait_for(self._get_agent().run(prompt), timeout=120)
        return result.output
