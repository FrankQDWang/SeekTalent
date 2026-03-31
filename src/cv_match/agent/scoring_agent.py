from __future__ import annotations

import asyncio
import math
import threading
from time import perf_counter

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings

from cv_match.config import AppSettings
from cv_match.models import (
    NormalizedResume,
    ScoredCandidate,
    ScoringConfidence,
    ScoringContext,
    ScoringFailure,
)
from cv_match.prompting import LoadedPrompt, json_block


class ScoringAgent:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self.use_mock_backend = settings.llm_backend_mode != "openai-responses"
        self.agent: Agent[None, ScoredCandidate] | None = None
        if not self.use_mock_backend:
            self.agent = Agent(
                model=OpenAIResponsesModel(settings.scoring_model),
                output_type=ScoredCandidate,
                system_prompt=prompt.content,
                model_settings=OpenAIResponsesModelSettings(
                    openai_reasoning_effort=settings.reasoning_effort,
                    openai_reasoning_summary="concise",
                    openai_text_verbosity="low",
                ),
            )
        self._attempts: dict[str, int] = {}
        self._attempt_lock = threading.Lock()

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

        async def worker(index: int, candidate: ResumeCandidate) -> None:
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
                if self.use_mock_backend:
                    result = await asyncio.to_thread(
                        self._score_one_mock,
                        candidate,
                        context,
                        attempt,
                    )
                else:
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
        assert self.agent is not None
        prompt = "\n\n".join(
            [
                json_block("SCORING_CONTEXT", context.model_dump(mode="json")),
                json_block("NORMALIZED_RESUME", candidate.model_dump(mode="json")),
                json_block("CALL_METADATA", {"attempt": attempt}),
            ]
        )
        result = await self.agent.run(prompt)
        return result.output

    def _score_one_mock(
        self,
        candidate: NormalizedResume,
        context: ScoringContext,
        attempt: int,
    ) -> ScoredCandidate:
        with self._attempt_lock:
            prior_attempts = self._attempts.get(candidate.resume_id, 0) + 1
            self._attempts[candidate.resume_id] = prior_attempts
        failure_mode = {"mock-r009": "fail_once", "mock-r013": "fail_always"}.get(candidate.resume_id, "none")
        if failure_mode == "fail_once" and prior_attempts == 1:
            raise RuntimeError("Simulated transient scoring failure.")
        if failure_mode == "fail_always":
            raise RuntimeError("Simulated permanent scoring failure.")

        text = candidate.scoring_text.casefold()
        matched_must = [kw for kw in context.must_have_keywords if kw.casefold() in text]
        missing_must = [kw for kw in context.must_have_keywords if kw.casefold() not in text]
        matched_pref = [kw for kw in context.preferred_keywords if kw.casefold() in text]
        negative_signals = [kw for kw in context.negative_keywords if kw.casefold() in text]
        hard_misses = [
            f"{filter_item.field}:{filter_item.value}"
            for filter_item in context.hard_filters
            if str(filter_item.value).casefold() not in text
        ]
        info_gaps: list[str] = []
        if candidate.years_of_experience is None:
            info_gaps.append("unknown_work_year")
        if not candidate.recent_experiences:
            info_gaps.append("missing_work_experience_summary")
        if not candidate.education_summary:
            info_gaps.append("missing_education_summary")
        if candidate.completeness_score < 60:
            info_gaps.append("low_resume_completeness")
        must_score = int(100 * len(matched_must) / max(1, len(context.must_have_keywords)))
        preferred_score = (
            int(100 * len(matched_pref) / max(1, len(context.preferred_keywords)))
            if context.preferred_keywords
            else 50
        )
        risk_flags: list[str] = []
        risk = 0
        if hard_misses:
            risk += 35 * len(hard_misses)
            risk_flags.append("hard_filter_gap")
        if negative_signals:
            risk += 25 * len(negative_signals)
            risk_flags.extend(f"negative:{item}" for item in negative_signals)
        if info_gaps:
            risk += 12 * len(info_gaps)
            risk_flags.extend(info_gaps)
        if candidate.years_of_experience is not None and candidate.years_of_experience < 5:
            risk += 8
            risk_flags.append("lower_seniority")
        risk = max(0, min(100, risk))
        must_requirement = max(1, math.ceil(len(context.must_have_keywords) * 0.6)) if context.must_have_keywords else 0
        fatal_conflict = bool(hard_misses or negative_signals)
        fit_bucket = "fit" if len(matched_must) >= must_requirement and not fatal_conflict and len(info_gaps) <= 1 else "not_fit"
        experience_score = min((candidate.years_of_experience or 0) * 10, 100)
        if fit_bucket == "fit":
            overall = round(75 + 0.10 * must_score + 0.06 * preferred_score + 0.04 * experience_score - 0.18 * risk)
            overall = max(75, min(98, overall))
        elif must_score >= 40 and not fatal_conflict:
            overall = round(45 + 0.12 * must_score + 0.06 * preferred_score + 0.03 * experience_score - 0.16 * risk)
            overall = max(40, min(74, overall))
        else:
            overall = round(18 + 0.18 * must_score + 0.05 * preferred_score + 0.02 * experience_score - 0.20 * risk)
            overall = max(0, min(59, overall))
        evidence = []
        evidence_candidates = [
            *(item.summary for item in candidate.recent_experiences if item.summary),
            *(f"{item.title} {item.company}".strip() for item in candidate.recent_experiences if item.title or item.company),
            *candidate.key_achievements,
            *candidate.skills,
            candidate.education_summary,
        ]
        for item in evidence_candidates:
            lowered = item.casefold()
            if any(keyword.casefold() in lowered for keyword in matched_must + matched_pref):
                evidence.append(item)
            if len(evidence) >= 4:
                break
        if not evidence:
            evidence = [item for item in evidence_candidates if item][:3]
        strongest_match = matched_must[0] if matched_must else (matched_pref[0] if matched_pref else "no clear role signal")
        biggest_risk = hard_misses[0] if hard_misses else (
            negative_signals[0] if negative_signals else (missing_must[0] if missing_must else (info_gaps[0] if info_gaps else "low material risk"))
        )
        reasoning_summary = (
            f"{fit_bucket.upper()} for the current role because the resume matches {len(matched_must)}/{len(context.must_have_keywords)} must-haves. "
            f"Strongest support: {strongest_match}. "
            f"Main risk: {biggest_risk}."
        )
        confidence: ScoringConfidence
        if fatal_conflict or len(info_gaps) >= 2 or candidate.completeness_score < 50:
            confidence = "low"
        elif missing_must or len(info_gaps) == 1 or candidate.completeness_score < 75:
            confidence = "medium"
        else:
            confidence = "high"
        return ScoredCandidate(
            resume_id=candidate.resume_id,
            fit_bucket=fit_bucket,
            overall_score=overall,
            must_have_match_score=must_score,
            preferred_match_score=preferred_score,
            risk_score=risk,
            risk_flags=risk_flags,
            reasoning_summary=reasoning_summary,
            evidence=evidence,
            confidence=confidence,
            matched_must_haves=matched_must,
            missing_must_haves=missing_must,
            matched_preferences=matched_pref,
            negative_signals=negative_signals,
            source_round=candidate.source_round or context.round_no,
            retry_count=attempt - 1,
        )
