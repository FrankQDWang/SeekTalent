from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import ControllerDecision, ControllerStateView
from cv_match.prompting import LoadedPrompt, json_block


class ReActController:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.agent: Agent[None, ControllerDecision] | None = None

    def _get_agent(self) -> Agent[None, ControllerDecision]:
        if self.agent is None:
            self.agent = Agent(
                model=OpenAIResponsesModel(self.settings.strategy_model),
                output_type=ControllerDecision,
                system_prompt=self.prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=self.settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )
        return self.agent

    def decide(self, *, state_view: ControllerStateView) -> ControllerDecision:
        return asyncio.run(self._decide_live(state_view=state_view))

    async def _decide_live(self, *, state_view: ControllerStateView) -> ControllerDecision:
        result = await asyncio.wait_for(
            self._get_agent().run(json_block("STATE_VIEW", state_view.model_dump(mode="json"))),
            timeout=90,
        )
        return result.output
