from __future__ import annotations

from typing import cast

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import InputTruth, RequirementExtractionDraft, RequirementSheet
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.requirements.normalization import normalize_requirement_draft


class RequirementExtractor:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _get_agent(self) -> Agent[None, RequirementExtractionDraft]:
        model = build_model(self.settings.requirements_model)
        return cast(Agent[None, RequirementExtractionDraft], Agent(
            model=model,
            output_type=build_output_spec(self.settings.requirements_model, model, RequirementExtractionDraft),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(self.settings, self.settings.requirements_model),
            retries=0,
            output_retries=2,
        ))

    async def extract_with_draft(self, *, input_truth: InputTruth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        result = await self._get_agent().run(
            json_block("INPUT_TRUTH", input_truth.model_dump(mode="json")),
        )
        draft = result.output
        return draft, normalize_requirement_draft(draft, job_title=input_truth.job_title)
