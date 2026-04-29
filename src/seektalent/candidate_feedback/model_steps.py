from __future__ import annotations

import json
from typing import cast

from pydantic_ai import Agent

from seektalent.candidate_feedback.models import CandidateFeedbackModelRanking, FeedbackCandidateTerm
from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec, resolve_stage_model_config
from seektalent.prompting import LoadedPrompt


class CandidateFeedbackModelSteps:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    async def rank_terms(
        self,
        *,
        role_title: str,
        must_have_capabilities: list[str],
        existing_terms: list[str],
        candidates: list[FeedbackCandidateTerm],
    ) -> CandidateFeedbackModelRanking:
        result = await self._agent().run(
            _rank_prompt(
                role_title=role_title,
                must_have_capabilities=must_have_capabilities,
                existing_terms=existing_terms,
                candidates=candidates,
            )
        )
        ranking = result.output
        accepted = ranking.accepted_from(candidates)
        return ranking.model_copy(update={"accepted_terms": accepted})

    def _agent(self) -> Agent[None, CandidateFeedbackModelRanking]:
        config = resolve_stage_model_config(self.settings, stage="candidate_feedback")
        model = build_model(config)
        return cast(
            Agent[None, CandidateFeedbackModelRanking],
            Agent(
                model=model,
                output_type=build_output_spec(config, model, CandidateFeedbackModelRanking),
                system_prompt=self.prompt.content,
                model_settings=build_model_settings(config),
                retries=0,
                output_retries=2,
            ),
        )


def _rank_prompt(
    *,
    role_title: str,
    must_have_capabilities: list[str],
    existing_terms: list[str],
    candidates: list[FeedbackCandidateTerm],
) -> str:
    payload = {
        "role_title": role_title,
        "must_have_capabilities": must_have_capabilities,
        "existing_terms": existing_terms,
        "candidate_terms": [item.model_dump(mode="json") for item in candidates],
    }
    return (
        "Rank only the candidate terms that already exist in candidate_terms. "
        "accepted_terms must be copied exactly from candidate_terms[*].term. "
        "Do not generate new terms or query rewrites. "
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
