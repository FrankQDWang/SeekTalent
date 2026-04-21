from __future__ import annotations

import asyncio
from datetime import datetime
from time import perf_counter
from typing import cast

from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec, model_provider
from seektalent.models import (
    ScoredCandidate,
    ScoredCandidateDraft,
    ScoringConfidence,
    ScoringFailure,
    ScoringContext,
    unique_strings,
)
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.tracing import LLMCallSnapshot, RunTracer
from seektalent.tracing import json_char_count, json_sha256


class ResumeScorer:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _build_agent(self) -> Agent[None, ScoredCandidateDraft]:
        model = build_model(self.settings.scoring_model)
        return cast(Agent[None, ScoredCandidateDraft], Agent(
            model=model,
            output_type=build_output_spec(self.settings.scoring_model, model, ScoredCandidateDraft),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(self.settings, self.settings.scoring_model),
            retries=0,
            output_retries=2,
        ))

    async def score_candidates_parallel(
        self,
        *,
        contexts: list[ScoringContext],
        tracer: RunTracer,
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
        tracer: RunTracer,
        agent: Agent[None, ScoredCandidateDraft],
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
        tracer: RunTracer,
        agent: Agent[None, ScoredCandidateDraft],
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
            draft = await self._score_one_live(context=context, agent=agent)
            result = _materialize_scored_candidate(
                draft=draft,
                resume_id=candidate.resume_id,
                source_round=candidate.source_round or context.round_no,
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
                    retries=0,
                    output_retries=2,
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="succeeded",
                    input_artifact_refs=[
                        f"rounds/round_{context.round_no:02d}/scoring_input_refs.jsonl",
                        f"resumes/{candidate.resume_id}.json",
                        "scoring_policy.json",
                    ],
                    output_artifact_refs=[
                        f"rounds/round_{context.round_no:02d}/scorecards.jsonl#resume_id={candidate.resume_id}"
                    ],
                    input_payload_sha256=json_sha256(user_payload),
                    structured_output_sha256=json_sha256(result.model_dump(mode="json")),
                    prompt_chars=len(self.prompt.content),
                    input_payload_chars=json_char_count(user_payload),
                    output_chars=json_char_count(result.model_dump(mode="json")),
                    input_summary=(
                        f"round={context.round_no}; resume_id={candidate.resume_id}; "
                        f"summary={candidate.compact_summary()}"
                    ),
                    output_summary=(
                        f"fit_bucket={result.fit_bucket}; score={result.overall_score}; "
                        f"risk={result.risk_score}"
                    ),
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
                payload={},
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
                    retries=0,
                    output_retries=2,
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="failed",
                    input_artifact_refs=[
                        f"rounds/round_{context.round_no:02d}/scoring_input_refs.jsonl",
                        f"resumes/{candidate.resume_id}.json",
                        "scoring_policy.json",
                    ],
                    output_artifact_refs=[],
                    input_payload_sha256=json_sha256(user_payload),
                    structured_output_sha256=None,
                    prompt_chars=len(self.prompt.content),
                    input_payload_chars=json_char_count(user_payload),
                    output_chars=0,
                    input_summary=(
                        f"round={context.round_no}; resume_id={candidate.resume_id}; "
                        f"summary={candidate.compact_summary()}"
                    ),
                    output_summary=None,
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
        agent: Agent[None, ScoredCandidateDraft],
    ) -> ScoredCandidateDraft:
        prompt = "\n\n".join(
            [
                json_block("SCORING_CONTEXT", context.model_dump(mode="json")),
                json_block("CALL_METADATA", {"attempt": 1}),
            ]
        )
        result = await agent.run(prompt)
        return result.output


def _materialize_scored_candidate(
    *,
    draft: ScoredCandidateDraft,
    resume_id: str,
    source_round: int,
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        source_round=source_round,
        fit_bucket=draft.fit_bucket,
        overall_score=draft.overall_score,
        must_have_match_score=draft.must_have_match_score,
        preferred_match_score=draft.preferred_match_score,
        risk_score=draft.risk_score,
        risk_flags=draft.risk_flags,
        reasoning_summary=draft.reasoning_summary,
        evidence=_derived_evidence(draft),
        confidence=_derived_confidence(draft),
        matched_must_haves=draft.matched_must_haves,
        missing_must_haves=draft.missing_must_haves,
        matched_preferences=draft.matched_preferences,
        negative_signals=draft.negative_signals,
        strengths=_derived_strengths(draft),
        weaknesses=_derived_weaknesses(draft),
    )


def _prefixed(prefix: str, values: list[str]) -> list[str]:
    return [f"{prefix}: {value}" for value in unique_strings(values)]


def _derived_evidence(draft: ScoredCandidateDraft) -> list[str]:
    return unique_strings(
        [
            *draft.matched_must_haves,
            *draft.matched_preferences,
            *draft.negative_signals,
            *draft.risk_flags,
        ]
    )[:8]


def _derived_confidence(draft: ScoredCandidateDraft) -> ScoringConfidence:
    score_gap = abs(draft.overall_score - draft.must_have_match_score)
    if draft.fit_bucket == "fit":
        if (
            draft.overall_score >= 75
            and draft.must_have_match_score >= 70
            and draft.risk_score <= 35
            and score_gap <= 25
        ):
            return "high"
        if draft.overall_score < 60 or draft.must_have_match_score < 50 or draft.risk_score >= 65 or score_gap > 35:
            return "low"
        return "medium"
    if draft.overall_score <= 55 or draft.must_have_match_score <= 50 or draft.risk_score >= 60:
        return "high"
    if draft.overall_score >= 75 and draft.must_have_match_score >= 70 and draft.risk_score <= 35:
        return "low"
    return "medium"


def _derived_strengths(draft: ScoredCandidateDraft) -> list[str]:
    strengths = [
        *_prefixed("Matched must-have", draft.matched_must_haves),
        *_prefixed("Matched preference", draft.matched_preferences),
    ]
    return strengths or ([draft.reasoning_summary] if draft.fit_bucket == "fit" else [])


def _derived_weaknesses(draft: ScoredCandidateDraft) -> list[str]:
    weaknesses = [
        *_prefixed("Missing must-have", draft.missing_must_haves),
        *_prefixed("Negative signal", draft.negative_signals),
        *_prefixed("Risk flag", draft.risk_flags),
    ]
    return weaknesses or ([draft.reasoning_summary] if draft.fit_bucket == "not_fit" else [])
