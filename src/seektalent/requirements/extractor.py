from __future__ import annotations

from time import perf_counter
from typing import cast

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import InputTruth, RequirementExtractionDraft, RequirementSheet
from seektalent.prompting import LoadedPrompt
from seektalent.repair import repair_requirement_draft
from seektalent.requirements.normalization import normalize_requirement_draft
from seektalent.runtime.exact_llm_cache import get_cached_json, put_cached_json, stable_cache_key


def render_requirements_prompt(input_truth: InputTruth) -> str:
    notes = input_truth.notes.strip() or "(none)"
    return "\n\n".join(
        [
            "TASK\nExtract one RequirementExtractionDraft from the job title, JD, and sourcing notes.",
            f"JOB TITLE\n{input_truth.job_title}",
            f"JOB DESCRIPTION\n{input_truth.jd}",
            f"SOURCING NOTES\n{notes}",
        ]
    )


def requirement_cache_key(settings: AppSettings, *, prompt: LoadedPrompt, input_truth: InputTruth) -> str:
    return stable_cache_key(
        [
            "requirement_extraction_draft.v1",
            settings.requirements_model,
            settings.reasoning_effort,
            settings.requirements_enable_thinking,
            settings.structured_repair_model,
            settings.structured_repair_reasoning_effort,
            prompt.sha256,
            input_truth.job_title_sha256,
            input_truth.jd_sha256,
            input_truth.notes_sha256,
        ]
    )


class RequirementExtractor:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.last_cache_hit = False
        self.last_cache_key: str | None = None
        self.last_cache_lookup_latency_ms: int | None = None
        self.last_prompt_cache_key: str | None = None
        self.last_prompt_cache_retention: str | None = None
        self.last_repair_attempt_count = 0
        self.last_repair_succeeded = False
        self.last_repair_reason: str | None = None
        self.last_full_retry_count = 0

    def _reset_metadata(self) -> None:
        self.last_cache_hit = False
        self.last_cache_key = None
        self.last_cache_lookup_latency_ms = None
        self.last_prompt_cache_key = None
        self.last_prompt_cache_retention = None
        self.last_repair_attempt_count = 0
        self.last_repair_succeeded = False
        self.last_repair_reason = None
        self.last_full_retry_count = 0

    def _prompt_cache_key(self, *, cache_key: str) -> str | None:
        if not self.settings.openai_prompt_cache_enabled:
            return None
        return f"requirements:{self.settings.requirements_model}:{cache_key}"

    def _get_agent(self, prompt_cache_key: str | None = None) -> Agent[None, RequirementExtractionDraft]:
        model = build_model(self.settings.requirements_model)
        return cast(Agent[None, RequirementExtractionDraft], Agent(
            model=model,
            output_type=build_output_spec(self.settings.requirements_model, model, RequirementExtractionDraft),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(
                self.settings,
                self.settings.requirements_model,
                enable_thinking=self.settings.requirements_enable_thinking,
                prompt_cache_key=prompt_cache_key,
            ),
            retries=0,
            output_retries=2,
        ))

    async def _extract_live(
        self,
        *,
        input_truth: InputTruth,
        prompt_cache_key: str | None = None,
    ) -> RequirementExtractionDraft:
        result = await self._get_agent(prompt_cache_key=prompt_cache_key).run(render_requirements_prompt(input_truth))
        return result.output

    async def extract_with_draft(self, *, input_truth: InputTruth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        self._reset_metadata()
        key = requirement_cache_key(self.settings, prompt=self.prompt, input_truth=input_truth)
        self.last_cache_key = key
        lookup_started = perf_counter()
        cached_payload = get_cached_json(self.settings, namespace="requirements", key=key)
        self.last_cache_lookup_latency_ms = max(1, int((perf_counter() - lookup_started) * 1000))
        if cached_payload is not None:
            self.last_cache_hit = True
            cached_draft = RequirementExtractionDraft.model_validate(cached_payload)
            return cached_draft, normalize_requirement_draft(cached_draft, job_title=input_truth.job_title)

        prompt_cache_key = self._prompt_cache_key(cache_key=key)
        self.last_prompt_cache_key = prompt_cache_key
        if prompt_cache_key is not None:
            self.last_prompt_cache_retention = self.settings.openai_prompt_cache_retention
        draft = await self._extract_live(input_truth=input_truth, prompt_cache_key=prompt_cache_key)
        try:
            requirement_sheet = normalize_requirement_draft(draft, job_title=input_truth.job_title)
        except ValueError as exc:
            self.last_repair_attempt_count = 1
            self.last_repair_reason = str(exc)
            draft = await repair_requirement_draft(
                self.settings,
                self.prompt,
                input_truth,
                draft,
                self.last_repair_reason,
            )
            try:
                requirement_sheet = normalize_requirement_draft(draft, job_title=input_truth.job_title)
            except ValueError:
                self.last_full_retry_count = 1
                draft = await self._extract_live(input_truth=input_truth, prompt_cache_key=prompt_cache_key)
                requirement_sheet = normalize_requirement_draft(draft, job_title=input_truth.job_title)
            else:
                self.last_repair_succeeded = True
        put_cached_json(
            self.settings,
            namespace="requirements",
            key=key,
            payload=draft.model_dump(mode="json"),
        )
        return draft, requirement_sheet
