from __future__ import annotations

import asyncio
from time import perf_counter

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import (
    NormalizedResume,
    ScoredCandidate,
    ScoringContext,
    ScoringFailure,
)
from cv_match.prompting import LoadedPrompt, json_block


class ResumeScorer:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.agent: Agent[None, ScoredCandidate] | None = None

    def _get_agent(self) -> Agent[None, ScoredCandidate]:
        if self.agent is None:
            self.agent = Agent(
                model=OpenAIResponsesModel(self.settings.scoring_model),
                output_type=ScoredCandidate,
                system_prompt=self.prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=self.settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )
        return self.agent

    def score_candidates_parallel(
        self,
        *,
        candidates: list[NormalizedResume],
        context: ScoringContext,
        tracer: object,
    ) -> tuple[list[ScoredCandidate], list[ScoringFailure]]:
        return asyncio.run(self._score_candidates_parallel(candidates=candidates, context=context, tracer=tracer))

    async def _score_candidates_parallel(
        self,
        *,
        candidates: list[NormalizedResume],
        context: ScoringContext,
        tracer: object,
    ) -> tuple[list[ScoredCandidate], list[ScoringFailure]]:
        semaphore = asyncio.Semaphore(self.settings.scoring_max_concurrency)
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []

        async def worker(index: int, candidate: NormalizedResume) -> None:
            branch_id = f"r{context.round_no}-b{index + 1}-{candidate.resume_id}"
            tracer.emit(
                "score_branch_started",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                model=self.settings.scoring_model,
                summary=candidate.compact_summary(),
            )
            async with semaphore:
                result, failure = await self._score_one_with_retry(
                    candidate=candidate,
                    context=context,
                    branch_id=branch_id,
                    tracer=tracer,
                )
            if result is not None:
                scored.append(result)
            if failure is not None:
                failures.append(failure)

        await asyncio.gather(*(worker(index, candidate) for index, candidate in enumerate(candidates)))
        return scored, failures

    async def _score_one_with_retry(
        self,
        *,
        candidate: NormalizedResume,
        context: ScoringContext,
        branch_id: str,
        tracer: object,
    ) -> tuple[ScoredCandidate | None, ScoringFailure | None]:
        for attempt in (1, 2):
            started_at = perf_counter()
            try:
                result = await self._score_one_live(candidate=candidate, context=context, attempt=attempt)
                result = result.model_copy(
                    update={
                        "resume_id": candidate.resume_id,
                        "source_round": candidate.source_round or context.round_no,
                        "retry_count": attempt - 1,
                    }
                )
                latency_ms = max(1, int((perf_counter() - started_at) * 1000))
                tracer.emit(
                    "score_branch_completed",
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model=self.settings.scoring_model,
                    latency_ms=latency_ms,
                    summary=result.reasoning_summary,
                    payload={
                        "fit_bucket": result.fit_bucket,
                        "overall_score": result.overall_score,
                        "risk_score": result.risk_score,
                        "confidence": result.confidence,
                        "reasoning_summary": result.reasoning_summary,
                        "retry_count": result.retry_count,
                        "retried": result.retry_count > 0,
                        "final_failure": False,
                        "missing_must_haves": result.missing_must_haves,
                        "negative_signals": result.negative_signals,
                        "risk_flags": result.risk_flags,
                    },
                )
                return result, None
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    latency_ms = max(1, int((perf_counter() - started_at) * 1000))
                    failure = ScoringFailure(
                        resume_id=candidate.resume_id,
                        branch_id=branch_id,
                        round_no=context.round_no,
                        attempts=attempt,
                        error_message=str(exc),
                        retried=True,
                        final_failure=True,
                        latency_ms=latency_ms,
                    )
                    tracer.emit(
                        "score_branch_failed",
                        round_no=context.round_no,
                        resume_id=candidate.resume_id,
                        branch_id=branch_id,
                        model=self.settings.scoring_model,
                        latency_ms=latency_ms,
                        summary=str(exc),
                        payload={
                            "attempts": attempt,
                            "retry_count": attempt - 1,
                            "retried": True,
                            "final_failure": True,
                        },
                    )
                    return None, failure
        return None, None

    async def _score_one_live(
        self,
        *,
        candidate: NormalizedResume,
        context: ScoringContext,
        attempt: int,
    ) -> ScoredCandidate:
        prompt = "\n\n".join(
            [
                json_block("SCORING_CONTEXT", context.model_dump(mode="json")),
                json_block("NORMALIZED_RESUME", candidate.model_dump(mode="json")),
                json_block("CALL_METADATA", {"attempt": attempt}),
            ]
        )
        result = await self._get_agent().run(prompt)
        return result.output
