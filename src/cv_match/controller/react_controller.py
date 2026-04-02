from __future__ import annotations

import asyncio

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from cv_match.config import AppSettings
from cv_match.llm import build_model, build_model_settings, build_output_spec
from cv_match.models import ControllerContext, ControllerDecision
from cv_match.prompting import LoadedPrompt, json_block


class ReActController:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.agent: Agent[ControllerContext, ControllerDecision] | None = None
        self.last_validator_retry_count = 0

    def _get_agent(self) -> Agent[ControllerContext, ControllerDecision]:
        if self.agent is None:
            model = build_model(self.settings.controller_model)
            agent = Agent(
                model=model,
                output_type=build_output_spec(self.settings.controller_model, model, ControllerDecision),
                system_prompt=self.prompt.content,
                deps_type=ControllerContext,
                model_settings=build_model_settings(self.settings, self.settings.controller_model),
                retries=0,
                output_retries=1,
            )

            @agent.output_validator
            def validate_output(
                ctx: RunContext[ControllerContext],
                output: ControllerDecision,
            ) -> ControllerDecision:
                if output.action == "search_cts" and not output.proposed_query_terms:
                    self.last_validator_retry_count += 1
                    raise ModelRetry("proposed_query_terms must contain at least one term.")
                if ctx.deps.previous_reflection is not None and not (output.response_to_reflection or "").strip():
                    self.last_validator_retry_count += 1
                    raise ModelRetry("response_to_reflection is required when previous_reflection exists.")
                return output

            self.agent = agent
        return self.agent

    def decide(self, *, context: ControllerContext) -> ControllerDecision:
        return asyncio.run(self._decide_live(context=context))

    async def _decide_live(self, *, context: ControllerContext) -> ControllerDecision:
        self.last_validator_retry_count = 0
        result = await asyncio.wait_for(
            self._get_agent().run(
                json_block("CONTROLLER_CONTEXT", context.model_dump(mode="json")),
                deps=context,
            ),
            timeout=90,
        )
        return result.output
