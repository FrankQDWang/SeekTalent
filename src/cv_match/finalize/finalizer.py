from __future__ import annotations

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from cv_match.config import AppSettings
from cv_match.llm import build_model, build_model_settings, build_output_spec
from cv_match.models import FinalResult, FinalizeContext, ScoredCandidate
from cv_match.prompting import LoadedPrompt, json_block


class Finalizer:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.last_validator_retry_count = 0

    def _get_agent(self) -> Agent[FinalizeContext, FinalResult]:
        model = build_model(self.settings.finalize_model)
        agent = Agent(
            model=model,
            output_type=build_output_spec(self.settings.finalize_model, model, FinalResult),
            system_prompt=self.prompt.content,
            deps_type=FinalizeContext,
            model_settings=build_model_settings(self.settings, self.settings.finalize_model),
            retries=0,
            output_retries=1,
        )

        @agent.output_validator
        def validate_output(
            ctx: RunContext[FinalizeContext],
            output: FinalResult,
        ) -> FinalResult:
            allowed = {candidate.resume_id: candidate for candidate in ctx.deps.top_candidates}
            seen: set[str] = set()
            expected_rank = 1
            last_position = -1
            positions = {candidate.resume_id: index for index, candidate in enumerate(ctx.deps.top_candidates)}
            for candidate in output.candidates:
                source_candidate = allowed.get(candidate.resume_id)
                if source_candidate is None:
                    self.last_validator_retry_count += 1
                    raise ModelRetry(f"Unknown resume_id {candidate.resume_id!r} in final candidates.")
                if candidate.resume_id in seen:
                    self.last_validator_retry_count += 1
                    raise ModelRetry(f"Duplicate resume_id {candidate.resume_id!r} in final candidates.")
                if candidate.rank != expected_rank:
                    self.last_validator_retry_count += 1
                    raise ModelRetry("Candidate ranks must be contiguous and start at 1.")
                position = positions[candidate.resume_id]
                if position <= last_position:
                    self.last_validator_retry_count += 1
                    raise ModelRetry("Final candidates must preserve runtime ranking order.")
                if candidate.source_round != source_candidate.source_round:
                    self.last_validator_retry_count += 1
                    raise ModelRetry(f"source_round mismatch for resume_id {candidate.resume_id!r}.")
                seen.add(candidate.resume_id)
                last_position = position
                expected_rank += 1
            return output

        return agent

    async def finalize(
        self,
        *,
        run_id: str,
        run_dir: str,
        rounds_executed: int,
        stop_reason: str,
        ranked_candidates: list[ScoredCandidate],
    ) -> FinalResult:
        self.last_validator_retry_count = 0
        deps = FinalizeContext(
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            top_candidates=ranked_candidates,
        )
        payload = {
            "run_id": run_id,
            "run_dir": run_dir,
            "rounds_executed": rounds_executed,
            "stop_reason": stop_reason,
            "ranked_candidates": [item.model_dump(mode="json") for item in ranked_candidates],
        }
        result = await self._get_agent().run(
            json_block("FINALIZATION_CONTEXT", payload),
            deps=deps,
        )
        return result.output
