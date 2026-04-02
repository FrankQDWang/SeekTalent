from __future__ import annotations

import asyncio
from datetime import datetime
from time import perf_counter

from pydantic_ai import Agent

from cv_match.config import AppSettings
from cv_match.llm import build_model, build_model_settings, build_output_spec, model_provider
from cv_match.models import (
    ScoredCandidate,
    ScoringFailure,
    ScoringContext,
)
from cv_match.prompting import LoadedPrompt, json_block
from cv_match.tracing import LLMCallSnapshot


class ResumeScorer:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _build_agent(self) -> Agent[None, ScoredCandidate]:
        model = build_model(self.settings.scoring_model)
        return Agent(
            model=model,
            output_type=build_output_spec(self.settings.scoring_model, model, ScoredCandidate),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(self.settings, self.settings.scoring_model),
            retries=0,
            output_retries=1,
        )

    async def score_candidates_parallel(
        self,
        *,
        contexts: list[ScoringContext],
        tracer: object,
    ) -> tuple[list[ScoredCandidate], list[ScoringFailure]]:
        agent = self._build_agent()
        return await self._score_candidates_parallel(
            contexts=contexts,
            tracer=tracer,
            agent=agent,
        )

    async def _score_candidates_parallel(
        self,
        *,
        contexts: list[ScoringContext],
        tracer: object,
        agent: Agent[None, ScoredCandidate],
    ) -> tuple[list[ScoredCandidate], list[ScoringFailure]]:
        semaphore = asyncio.Semaphore(self.settings.scoring_max_concurrency)
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []

        async def worker(index: int, context: ScoringContext) -> None:
            candidate = context.normalized_resume
            branch_id = f"r{context.round_no}-b{index + 1}-{candidate.resume_id}"
            call_id = f"scoring-r{context.round_no:02d}-{branch_id}"
            tracer.emit(
                "score_branch_started",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                model=self.settings.scoring_model,
                call_id=call_id,
                status="started",
                summary=candidate.compact_summary(),
                artifact_paths=[
                    f"rounds/round_{context.round_no:02d}/scoring_calls.jsonl",
                    f"resumes/{candidate.resume_id}.json",
                ],
            )
            async with semaphore:
                result, failure = await self._score_one(
                    context=context,
                    branch_id=branch_id,
                    tracer=tracer,
                    agent=agent,
                )
            if result is not None:
                scored.append(result)
            if failure is not None:
                failures.append(failure)

        await asyncio.gather(*(worker(index, context) for index, context in enumerate(contexts)))
        return scored, failures

    async def _score_one(
        self,
        *,
        context: ScoringContext,
        branch_id: str,
        tracer: object,
        agent: Agent[None, ScoredCandidate],
    ) -> tuple[ScoredCandidate | None, ScoringFailure | None]:
        candidate = context.normalized_resume
        call_id = f"scoring-r{context.round_no:02d}-{branch_id}"
        started_at_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        user_payload = {
            "SCORING_CONTEXT": context.model_dump(mode="json"),
            "CALL_METADATA": {"attempt": 1},
        }
        artifact_paths = [
            f"rounds/round_{context.round_no:02d}/scoring_calls.jsonl",
            f"resumes/{candidate.resume_id}.json",
        ]
        started_at_clock = perf_counter()
        try:
            result = await self._score_one_live(context=context, agent=agent)
            result = result.model_copy(
                update={
                    "resume_id": candidate.resume_id,
                    "source_round": candidate.source_round or context.round_no,
                }
            )
            latency_ms = max(1, int((perf_counter() - started_at_clock) * 1000))
            tracer.append_jsonl(
                f"rounds/round_{context.round_no:02d}/scoring_calls.jsonl",
                LLMCallSnapshot(
                    stage="scoring",
                    call_id=call_id,
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model_id=self.settings.scoring_model,
                    provider=model_provider(self.settings.scoring_model),
                    prompt_hash=self.prompt.sha256,
                    prompt_snapshot_path="prompt_snapshots/scoring.md",
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="succeeded",
                    user_payload=user_payload,
                    structured_output=result.model_dump(mode="json"),
                ),
            )
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                model=self.settings.scoring_model,
                call_id=call_id,
                status="succeeded",
                latency_ms=latency_ms,
                summary=result.reasoning_summary,
                artifact_paths=artifact_paths,
                payload={
                    "fit_bucket": result.fit_bucket,
                    "overall_score": result.overall_score,
                    "risk_score": result.risk_score,
                    "confidence": result.confidence,
                    "reasoning_summary": result.reasoning_summary,
                    "missing_must_haves": result.missing_must_haves,
                    "negative_signals": result.negative_signals,
                    "risk_flags": result.risk_flags,
                },
            )
            return result, None
        except Exception as exc:  # noqa: BLE001
            latency_ms = max(1, int((perf_counter() - started_at_clock) * 1000))
            failure = ScoringFailure(
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                round_no=context.round_no,
                attempts=1,
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            tracer.append_jsonl(
                f"rounds/round_{context.round_no:02d}/scoring_calls.jsonl",
                LLMCallSnapshot(
                    stage="scoring",
                    call_id=call_id,
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model_id=self.settings.scoring_model,
                    provider=model_provider(self.settings.scoring_model),
                    prompt_hash=self.prompt.sha256,
                    prompt_snapshot_path="prompt_snapshots/scoring.md",
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="failed",
                    user_payload=user_payload,
                    structured_output=None,
                    error_message=str(exc),
                ),
            )
            tracer.emit(
                "score_branch_failed",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                model=self.settings.scoring_model,
                call_id=call_id,
                status="failed",
                latency_ms=latency_ms,
                summary=str(exc),
                error_message=str(exc),
                artifact_paths=artifact_paths,
                payload={"attempts": 1},
            )
            return None, failure

    async def _score_one_live(
        self,
        *,
        context: ScoringContext,
        agent: Agent[None, ScoredCandidate],
    ) -> ScoredCandidate:
        prompt = "\n\n".join(
            [
                json_block("SCORING_CONTEXT", context.model_dump(mode="json")),
                json_block("CALL_METADATA", {"attempt": 1}),
            ]
        )
        result = await agent.run(prompt)
        return result.output
