from __future__ import annotations

from pydantic_ai import Agent

from cv_match.config import AppSettings
from cv_match.llm import build_model, build_model_settings, build_output_spec
from cv_match.models import InputTruth, RequirementExtractionDraft, RequirementSheet
from cv_match.prompting import LoadedPrompt, json_block
from cv_match.requirements.normalization import normalize_requirement_draft


class RequirementExtractor:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _get_agent(self) -> Agent[None, RequirementExtractionDraft]:
        model = build_model(self.settings.requirements_model)
        return Agent(
            model=model,
            output_type=build_output_spec(self.settings.requirements_model, model, RequirementExtractionDraft),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(self.settings, self.settings.requirements_model),
            retries=0,
            output_retries=1,
        )

    async def extract(self, *, input_truth: InputTruth) -> RequirementSheet:
        _, requirement_sheet = await self.extract_with_draft(input_truth=input_truth)
        return requirement_sheet

    async def extract_with_draft(self, *, input_truth: InputTruth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        result = await self._get_agent().run(
            json_block("INPUT_TRUTH", input_truth.model_dump(mode="json")),
        )
        draft = result.output
        return draft, normalize_requirement_draft(draft)
