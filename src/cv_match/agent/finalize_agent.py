from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import FinalCandidate, FinalResult, ScoredCandidate
from cv_match.prompting import LoadedPrompt, json_block


class FinalizeAgent:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.use_mock_backend = settings.llm_backend_mode != "openai-responses"
        self.agent: Agent[None, FinalResult] | None = None
        if not self.use_mock_backend:
            self.agent = Agent(
                model=OpenAIResponsesModel(settings.finalize_model),
                output_type=FinalResult,
                system_prompt=prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )

    def finalize(
        self,
        *,
        run_id: str,
        run_dir: str,
        rounds_executed: int,
        stop_reason: str,
        ranked_candidates: list[ScoredCandidate],
    ) -> FinalResult:
        if self.use_mock_backend:
            return self._finalize_mock(
                run_id=run_id,
                run_dir=run_dir,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                ranked_candidates=ranked_candidates,
            )
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
        assert self.agent is not None
        payload = {
            "run_id": run_id,
            "run_dir": run_dir,
            "rounds_executed": rounds_executed,
            "stop_reason": stop_reason,
            "ranked_candidates": [item.model_dump(mode="json") for item in ranked_candidates],
        }
        result = await self.agent.run(json_block("FINALIZATION_CONTEXT", payload))
        return result.output

    def _finalize_mock(
        self,
        *,
        run_id: str,
        run_dir: str,
        rounds_executed: int,
        stop_reason: str,
        ranked_candidates: list[ScoredCandidate],
    ) -> FinalResult:
        final_candidates = []
        for rank, candidate in enumerate(ranked_candidates[:5], start=1):
            final_candidates.append(
                FinalCandidate(
                    resume_id=candidate.resume_id,
                    rank=rank,
                    final_score=candidate.overall_score,
                    fit_bucket=candidate.fit_bucket,
                    match_summary=(
                        f"Must {candidate.must_have_match_score}/100, "
                        f"preferred {candidate.preferred_match_score}/100, risk {candidate.risk_score}/100."
                    ),
                    matched_must_haves=candidate.matched_must_haves,
                    matched_preferences=candidate.matched_preferences,
                    risk_flags=candidate.risk_flags,
                    why_selected=candidate.reasoning_summary,
                    source_round=candidate.source_round,
                )
            )
        summary = (
            f"Returned {len(final_candidates)} candidates after {rounds_executed} rounds. "
            f"Stop reason: {stop_reason}."
        )
        return FinalResult(
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            candidates=final_candidates,
            summary=summary,
        )
