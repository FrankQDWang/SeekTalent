from __future__ import annotations

from typing import cast

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import FinalCandidate, FinalResult, FinalResultDraft, FinalizeContext, ScoredCandidate
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.tracing import ProviderUsageSnapshot, provider_usage_from_result


def render_finalize_prompt(
    *,
    run_id: str,
    run_dir: str,
    rounds_executed: int,
    stop_reason: str,
    ranked_candidates: list[ScoredCandidate],
) -> str:
    candidate_lines = [
        (
            f"{rank}. {candidate.resume_id}: score={candidate.overall_score}, "
            f"fit={candidate.fit_bucket}, must={candidate.must_have_match_score}, "
            f"risk={candidate.risk_score}; {candidate.reasoning_summary}; "
            f"matched_must_haves={candidate.matched_must_haves[:4]}; "
            f"matched_preferences={candidate.matched_preferences[:4]}; "
            f"strengths={candidate.strengths[:3]}; "
            f"weaknesses={candidate.weaknesses[:3]}; "
            f"risk_flags={candidate.risk_flags[:3]}"
        )
        for rank, candidate in enumerate(ranked_candidates, start=1)
    ]
    exact_data = {
        "run_id": run_id,
        "run_dir": run_dir,
        "rounds_executed": rounds_executed,
        "stop_reason": stop_reason,
        "candidate_order": [candidate.resume_id for candidate in ranked_candidates],
    }
    return "\n\n".join(
        [
            "TASK\nWrite final shortlist presentation text. Return one FinalResultDraft.",
            (
                "FINALIZATION STATE\n"
                f"- Run id: {run_id}\n"
                f"- Rounds executed: {rounds_executed}\n"
                f"- Stop reason: {stop_reason}"
            ),
            "RANKED CANDIDATES\n" + ("\n".join(candidate_lines) if candidate_lines else "- (none)"),
            json_block("EXACT DATA", exact_data),
        ]
    )


class Finalizer:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []
        self.last_provider_usage: ProviderUsageSnapshot | None = None
        self.last_draft_output: FinalResultDraft | None = None

    def _record_retry(self, reason: str) -> ModelRetry:
        self.last_validator_retry_count += 1
        self.last_validator_retry_reasons.append(reason)
        return ModelRetry(reason)

    def _get_agent(self) -> Agent[FinalizeContext, FinalResultDraft]:
        model = build_model(self.settings.finalize_model)
        agent = cast(
            Agent[FinalizeContext, FinalResultDraft],
            Agent(
                model=model,
                output_type=build_output_spec(self.settings.finalize_model, model, FinalResultDraft),
                system_prompt=self.prompt.content,
                deps_type=FinalizeContext,
                model_settings=build_model_settings(self.settings, self.settings.finalize_model),
                retries=0,
                output_retries=2,
            ),
        )

        @agent.output_validator
        def validate_output(
            ctx: RunContext[FinalizeContext],
            output: FinalResultDraft,
        ) -> FinalResultDraft:
            allowed_ids = {candidate.resume_id for candidate in ctx.deps.top_candidates}
            expected_ids = [candidate.resume_id for candidate in ctx.deps.top_candidates]
            actual_ids: list[str] = []
            seen: set[str] = set()
            for candidate in output.candidates:
                if candidate.resume_id not in allowed_ids:
                    raise self._record_retry(f"Unknown resume_id {candidate.resume_id!r} in final candidates.")
                if candidate.resume_id in seen:
                    raise self._record_retry(f"Duplicate resume_id {candidate.resume_id!r} in final candidates.")
                seen.add(candidate.resume_id)
                actual_ids.append(candidate.resume_id)
            if len(actual_ids) != len(expected_ids):
                raise self._record_retry("Final candidates count must equal runtime top candidate count.")
            if actual_ids != expected_ids:
                raise self._record_retry("Final candidates must preserve runtime ranking order.")
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
        self.last_validator_retry_reasons = []
        self.last_provider_usage = None
        self.last_draft_output = None
        deps = FinalizeContext(
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            top_candidates=ranked_candidates,
        )
        result = await self._get_agent().run(
            render_finalize_prompt(
                run_id=run_id,
                run_dir=run_dir,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                ranked_candidates=ranked_candidates,
            ),
            deps=deps,
        )
        self.last_provider_usage = provider_usage_from_result(result)
        self.last_draft_output = result.output
        return _materialize_final_result(
            draft=result.output,
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            ranked_candidates=ranked_candidates,
        )


def _materialize_final_result(
    *,
    draft: FinalResultDraft,
    run_id: str,
    run_dir: str,
    rounds_executed: int,
    stop_reason: str,
    ranked_candidates: list[ScoredCandidate],
) -> FinalResult:
    candidates = [
        FinalCandidate(
            resume_id=source.resume_id,
            rank=rank,
            final_score=source.overall_score,
            fit_bucket=source.fit_bucket,
            match_summary=draft_candidate.match_summary,
            strengths=source.strengths,
            weaknesses=source.weaknesses,
            matched_must_haves=source.matched_must_haves,
            matched_preferences=source.matched_preferences,
            risk_flags=source.risk_flags,
            why_selected=draft_candidate.why_selected,
            source_round=source.source_round,
        )
        for rank, (source, draft_candidate) in enumerate(zip(ranked_candidates, draft.candidates, strict=True), start=1)
    ]
    return FinalResult(
        run_id=run_id,
        run_dir=run_dir,
        rounds_executed=rounds_executed,
        stop_reason=stop_reason,
        candidates=candidates,
        summary=draft.summary,
    )
