from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import FinalResult, ScoredCandidate
from cv_match.prompting import LoadedPrompt, json_block


class Finalizer:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.agent: Agent[None, FinalResult] | None = None

    def _get_agent(self) -> Agent[None, FinalResult]:
        if self.agent is None:
            self.agent = Agent(
                model=OpenAIResponsesModel(self.settings.finalize_model),
                output_type=FinalResult,
                system_prompt=self.prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=self.settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )
        return self.agent

    def finalize(
        self,
        *,
        run_id: str,
        run_dir: str,
        rounds_executed: int,
        stop_reason: str,
        ranked_candidates: list[ScoredCandidate],
    ) -> FinalResult:
        return asyncio.run(
            self._finalize_live(
                run_id=run_id,
                run_dir=run_dir,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                ranked_candidates=ranked_candidates,
            )
        )

    async def _finalize_live(
        self,
        *,
        run_id: str,
        run_dir: str,
        rounds_executed: int,
        stop_reason: str,
        ranked_candidates: list[ScoredCandidate],
    ) -> FinalResult:
        payload = {
            "run_id": run_id,
            "run_dir": run_dir,
            "rounds_executed": rounds_executed,
            "stop_reason": stop_reason,
            "ranked_candidates": [item.model_dump(mode="json") for item in ranked_candidates],
        }
        result = await asyncio.wait_for(
            self._get_agent().run(json_block("FINALIZATION_CONTEXT", payload)),
            timeout=90,
        )
        return result.output
