from __future__ import annotations

import asyncio
import json
from datetime import datetime
from time import perf_counter
from typing import Literal, cast

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
import httpx

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec, resolve_stage_model_config
from seektalent.models import (
    NormalizedResume,
    ScoredCandidate,
    ScoredCandidateDraft,
    ScoringConfidence,
    ScoringFailure,
    ScoringContext,
    ScoringPolicy,
    unique_strings,
)
from seektalent.candidate_quality import risk_at_or_above, risk_at_or_below
from seektalent.scoring.weighted_score import (
    ScoreDimensionApplicability,
    calculate_overall_score,
    score_dimension_applicability,
)
from seektalent.protected_attributes import PROTECTED_ATTRIBUTE_FIELDS, PROTECTED_ATTRIBUTE_SCORING_TEXT
from seektalent.prompt_safety import render_template_version_block, render_untrusted_text_block
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.cache.exact_llm_cache import get_cached_json, put_cached_json, stable_cache_key
from seektalent.tracing import LLMCallSnapshot, RunTracer
from seektalent.tracing import ProviderUsageSnapshot, provider_usage_from_result
from seektalent.tracing import json_char_count, json_sha256, text_char_count, text_sha256

SCORING_CACHE_SCHEMA_VERSION = "scored_candidate.v2"
ScoringFailureKind = Literal[
    "transport_error",
    "provider_error",
    "response_validation_error",
    "score_applicability_error",
]
ScoringProviderFailureKind = Literal[
    "provider_auth_error",
    "provider_access_denied",
    "provider_rate_limited",
    "provider_model_not_found",
    "provider_invalid_request",
    "provider_unknown_error",
]


def _scoring_failure_category(
    exc: Exception,
) -> tuple[ScoringFailureKind, ScoringProviderFailureKind | None]:
    if isinstance(exc, ValueError) and str(exc) in {
        "preferred_match_score_required",
        "preferred_match_score_not_applicable",
        "risk_score_required",
        "risk_score_not_applicable",
    }:
        return "score_applicability_error", None
    if isinstance(exc, ModelHTTPError):
        provider_kind_by_status: dict[int, ScoringProviderFailureKind] = {
            400: "provider_invalid_request",
            401: "provider_auth_error",
            403: "provider_access_denied",
            404: "provider_model_not_found",
            429: "provider_rate_limited",
        }
        provider_kind = provider_kind_by_status.get(exc.status_code, "provider_unknown_error")
        return "provider_error", provider_kind
    if isinstance(exc, ModelAPIError):
        return "provider_error", "provider_unknown_error"
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return "transport_error", None
    return "response_validation_error", None


def _validate_scoring_draft_applicability(
    draft: ScoredCandidateDraft,
    applicability: ScoreDimensionApplicability,
) -> ScoredCandidateDraft:
    if applicability.preferred != (draft.preferred_match_score is not None):
        expected = "a score" if applicability.preferred else "null"
        raise ModelRetry(f"preferred_match_score must be {expected} for this scoring policy")
    if applicability.risk != (draft.risk_score is not None):
        expected = "a score" if applicability.risk else "null"
        raise ModelRetry(f"risk_score must be {expected} for this scoring policy")
    return draft


def _round_artifact(round_no: int, subsystem: str, name: str, *, extension: str = "json") -> str:
    return f"rounds/{round_no:02d}/{subsystem}/{name}.{extension}"


def _lines(values: list[str], *, limit: int | None = None) -> str:
    items = values[:limit] if limit is not None else values
    return "\n".join(f"- {value}" for value in items) if items else "- (none)"


def _prompt_safe_constraints(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in PROTECTED_ATTRIBUTE_FIELDS}


def _structured_scoring_evidence_payload(resume: NormalizedResume) -> dict[str, object]:
    return resume.structured_evidence.to_scoring_evidence().model_dump(mode="json", exclude_defaults=True)


def _structured_scoring_evidence_json(resume: NormalizedResume) -> str:
    payload = _structured_scoring_evidence_payload(resume)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ": "))


def _scoring_cache_resume_payload(resume: NormalizedResume) -> dict[str, object]:
    return {
        "resume_id": resume.resume_id,
        "source_round": resume.source_round,
        "title": resume.current_title or resume.headline,
        "company": resume.current_company,
        "years_of_experience": resume.years_of_experience,
        "locations": resume.locations,
        "skills": resume.skills,
        "completeness_score": resume.completeness_score,
        "structured_scoring_evidence": _structured_scoring_evidence_payload(resume),
    }


def render_scoring_prompt(context: ScoringContext) -> str:
    policy = context.scoring_policy
    resume = context.normalized_resume
    exact_data = {
        "round_no": context.round_no,
        "resume_id": resume.resume_id,
        "source_round": resume.source_round,
    }
    runtime_only_constraints = [
        item.model_dump(mode="json")
        for item in context.runtime_only_constraints
        if item.field not in PROTECTED_ATTRIBUTE_FIELDS
    ]
    scoring_policy_text = (
        f"- Job Title: {policy.job_title}\n"
        f"- Summary: {policy.role_summary}\n"
        f"- Must have:\n{_lines(policy.must_have_capabilities)}\n"
        f"- Preferred:\n{_lines(policy.preferred_capabilities)}\n"
        f"- Exclusions:\n{_lines(policy.exclusion_signals)}\n"
        f"- Hard constraints: {_prompt_safe_constraints(policy.hard_constraints.model_dump(mode='json'))}\n"
        f"- Preferences: {policy.preferences.model_dump(mode='json')}\n"
        f"- Runtime-only constraints: {runtime_only_constraints or '(none)'}\n"
        f"- Protected attributes: {PROTECTED_ATTRIBUTE_SCORING_TEXT}\n"
        f"- Rationale: {policy.scoring_rationale}"
    )
    resume_card_text = (
        f"- Title: {resume.current_title or resume.headline or '(unknown)'}\n"
        f"- Company: {resume.current_company or '(unknown)'}\n"
        f"- Experience: {resume.years_of_experience if resume.years_of_experience is not None else '(unknown)'} years\n"
        f"- Locations: {', '.join(resume.locations) or '(none)'}\n"
        "- Education: (excluded from LLM scoring; protected attributes are handled by deterministic runtime policy)\n"
        f"- Skills:\n{_lines(resume.skills, limit=16)}\n"
        f"- Completeness: {resume.completeness_score}"
    )
    return "\n\n".join(
        [
            render_template_version_block("scoring"),
            "TASK\nScore this one resume against the role. Return one ScoredCandidateDraft.",
            "SCORING POLICY\n" + render_untrusted_text_block("SCORING_POLICY_TEXT", scoring_policy_text),
            "RESUME CARD\n" + render_untrusted_text_block("RESUME_CARD_TEXT", resume_card_text),
            "STRUCTURED RESUME EVIDENCE\n"
            + render_untrusted_text_block(
                "STRUCTURED_RESUME_EVIDENCE",
                _structured_scoring_evidence_json(resume),
            ),
            json_block("EXACT DATA", exact_data),
        ]
    )


def scoring_cache_key(
    settings: AppSettings,
    prompt: LoadedPrompt,
    context: ScoringContext,
    user_prompt: str,
) -> str:
    model_config = resolve_stage_model_config(settings, stage="scoring")
    return stable_cache_key(
        [
            SCORING_CACHE_SCHEMA_VERSION,
            model_config.protocol_family,
            model_config.endpoint_kind,
            model_config.endpoint_region,
            model_config.model_id,
            model_config.reasoning_effort,
            prompt.sha256,
            json_sha256(context.scoring_policy.model_dump(mode="json")),
            context.requirement_sheet_sha256,
            json_sha256(_scoring_cache_resume_payload(context.normalized_resume)),
            text_sha256(user_prompt),
        ]
    )


class ResumeScorer:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt
        self._model_config = resolve_stage_model_config(settings, stage="scoring")

    def _build_agent(
        self,
        *,
        applicability: ScoreDimensionApplicability,
        prompt_cache_key: str | None = None,
    ) -> Agent[None, ScoredCandidateDraft]:
        model = build_model(self._model_config)
        agent = cast(
            Agent[None, ScoredCandidateDraft],
            Agent(
                model=model,
                output_type=build_output_spec(self._model_config, model, ScoredCandidateDraft),
                system_prompt=self.prompt.content,
                model_settings=build_model_settings(
                    self._model_config,
                    prompt_cache_key=prompt_cache_key,
                ),
                retries=0,
                output_retries=2,
            ),
        )

        @agent.output_validator
        def validate_applicability(draft: ScoredCandidateDraft) -> ScoredCandidateDraft:
            return _validate_scoring_draft_applicability(draft, applicability)

        return agent

    def rendered_prompt_for_cache(self, context: ScoringContext) -> str:
        return render_scoring_prompt(context)

    def _batch_prompt_cache_key(self, *, contexts: list[ScoringContext]) -> str | None:
        if not (
            self._model_config.protocol_family == "openai_chat_completions_compatible"
            and self._model_config.openai_prompt_cache_enabled
        ):
            return None
        policy_hashes = sorted(
            {
                json_sha256(context.scoring_policy.model_dump(mode="json"))
                for context in contexts
            }
        )
        requirement_hashes = sorted(
            {context.requirement_sheet_sha256 for context in contexts}
        )
        return (
            f"scoring:{self._model_config.model_id}:"
            f"{stable_cache_key([self._model_config.protocol_family, self._model_config.model_id, self.prompt.sha256, policy_hashes, requirement_hashes])}"
        )

    async def score_candidates_parallel(
        self,
        *,
        contexts: list[ScoringContext],
        tracer: RunTracer,
    ) -> tuple[list[ScoredCandidate], list[ScoringFailure]]:
        if not contexts:
            return [], []
        applicability = score_dimension_applicability(contexts[0].scoring_policy)
        if any(
            score_dimension_applicability(context.scoring_policy) != applicability
            for context in contexts[1:]
        ):
            raise ValueError("mixed_scoring_dimension_applicability")
        prompt_cache_key = self._batch_prompt_cache_key(contexts=contexts)
        prompt_cache_retention = (
            self.settings.openai_prompt_cache_retention if prompt_cache_key is not None else None
        )
        agent = self._build_agent(
            applicability=applicability,
            prompt_cache_key=prompt_cache_key,
        )
        return await self._score_candidates_parallel(
            contexts=contexts,
            tracer=tracer,
            agent=agent,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
        )

    async def _score_candidates_parallel(
        self,
        *,
        contexts: list[ScoringContext],
        tracer: RunTracer,
        agent: Agent[None, ScoredCandidateDraft],
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
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
                model=self._model_config.model_id,
                call_id=call_id,
                status="started",
                summary=candidate.compact_summary(),
                artifact_paths=[
                    _round_artifact(context.round_no, "scoring", "scoring_calls", extension="jsonl"),
                    f"resumes/{candidate.resume_id}.json",
                ],
            )
            async with semaphore:
                result, failure = await self._score_one(
                    context=context,
                    branch_id=branch_id,
                    tracer=tracer,
                    agent=agent,
                    prompt_cache_key=prompt_cache_key,
                    prompt_cache_retention=prompt_cache_retention,
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
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
    ) -> tuple[ScoredCandidate | None, ScoringFailure | None]:
        candidate = context.normalized_resume
        call_id = f"scoring-r{context.round_no:02d}-{branch_id}"
        started_at_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        user_prompt = self.rendered_prompt_for_cache(context)
        cache_key = scoring_cache_key(self.settings, self.prompt, context, user_prompt)
        lookup_started = perf_counter()
        cached_payload = get_cached_json(self.settings, namespace="scoring", key=cache_key)
        cache_lookup_latency_ms = max(1, int((perf_counter() - lookup_started) * 1000))
        artifact_paths = [
            _round_artifact(context.round_no, "scoring", "scoring_calls", extension="jsonl"),
            f"resumes/{candidate.resume_id}.json",
        ]
        tracer.session.register_path(
            f"round.{context.round_no:02d}.scoring.scoring_calls",
            _round_artifact(context.round_no, "scoring", "scoring_calls", extension="jsonl"),
            content_type="application/jsonl",
            schema_version="v1",
        )
        started_at_clock = perf_counter()
        try:
            if cached_payload is not None:
                result = _attach_runtime_scoring_metadata(
                    ScoredCandidate.model_validate(cached_payload),
                    candidate=candidate,
                )
                latency_ms = max(1, int((perf_counter() - started_at_clock) * 1000))
                snapshot = LLMCallSnapshot(
                    stage="scoring",
                    call_id=call_id,
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model_id=self._model_config.model_id,
                    provider=self._model_config.provider_label,
                    protocol_family=self._model_config.protocol_family,  # ty:ignore[invalid-argument-type]
                    endpoint_kind=self._model_config.endpoint_kind,
                    endpoint_region=self._model_config.endpoint_region,
                    prompt_hash=self.prompt.sha256,
                    prompt_snapshot_path="assets/prompts/scoring.md",
                    structured_output_mode=self._model_config.structured_output_mode,
                    thinking_mode=self._model_config.thinking_mode,
                    reasoning_effort=self._model_config.reasoning_effort,
                    retries=0,
                    output_retries=2,
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="succeeded",
                    input_artifact_refs=[
                        f"round.{context.round_no:02d}.scoring.scoring_input_refs",
                        f"resumes/{candidate.resume_id}.json",
                        "input.scoring_policy",
                    ],
                    output_artifact_refs=[f"round.{context.round_no:02d}.scoring.scorecards"],
                    input_payload_sha256=text_sha256(user_prompt),
                    structured_output_sha256=json_sha256(result.model_dump(mode="json")),
                    prompt_chars=len(self.prompt.content),
                    input_payload_chars=text_char_count(user_prompt),
                    output_chars=json_char_count(result.model_dump(mode="json")),
                    input_summary=(
                        f"round={context.round_no}; resume_id={candidate.resume_id}; "
                        f"summary={candidate.compact_summary()}"
                    ),
                    output_summary=(
                        f"fit_bucket={result.fit_bucket}; overall={result.overall_score}; "
                        f"must={result.must_have_match_score}; preferred={result.preferred_match_score}; "
                        f"risk={result.risk_score}"
                    ),
                    cache_hit=True,
                    cache_key=cache_key,
                    cache_lookup_latency_ms=cache_lookup_latency_ms,
                    prompt_cache_key=prompt_cache_key,
                    prompt_cache_retention=prompt_cache_retention,
                ).model_dump(mode="json")
                snapshot.pop("provider_usage", None)
                tracer.append_jsonl(
                    f"round.{context.round_no:02d}.scoring.scoring_calls",
                    snapshot,
                )
                tracer.emit(
                    "score_branch_completed",
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model=self._model_config.model_id,
                    call_id=call_id,
                    status="succeeded",
                    latency_ms=latency_ms,
                    summary=result.reasoning_summary,
                    artifact_paths=artifact_paths,
                    payload={},
                )
                return result, None

            draft, provider_usage = await asyncio.wait_for(
                self._score_one_live(prompt=user_prompt, agent=agent),
                timeout=self.settings.scoring_timeout_seconds,
            )
            result = _materialize_scored_candidate(
                draft=draft,
                scoring_policy=context.scoring_policy,
                resume_id=candidate.resume_id,
                source_round=candidate.source_round or context.round_no,
                source_provider=candidate.source_provider,
                score_evidence_source=candidate.score_evidence_source,
                card_scorecard_ref=candidate.card_scorecard_ref,
                detail_scorecard_ref=candidate.detail_scorecard_ref,
                score_delta=candidate.score_delta,
                detail_open_reason=candidate.detail_open_reason,
                detail_open_policy_version=candidate.detail_open_policy_version,
            )
            put_cached_json(
                self.settings,
                namespace="scoring",
                key=cache_key,
                payload=result.model_dump(mode="json"),
            )
            latency_ms = max(1, int((perf_counter() - started_at_clock) * 1000))
            cached_input_tokens = (
                provider_usage.cache_read_tokens if provider_usage is not None else None
            )
            tracer.append_jsonl(
                f"round.{context.round_no:02d}.scoring.scoring_calls",
                LLMCallSnapshot(
                    stage="scoring",
                    call_id=call_id,
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model_id=self._model_config.model_id,
                    provider=self._model_config.provider_label,
                    protocol_family=self._model_config.protocol_family,  # ty:ignore[invalid-argument-type]
                    endpoint_kind=self._model_config.endpoint_kind,
                    endpoint_region=self._model_config.endpoint_region,
                    prompt_hash=self.prompt.sha256,
                    prompt_snapshot_path="assets/prompts/scoring.md",
                    structured_output_mode=self._model_config.structured_output_mode,
                    thinking_mode=self._model_config.thinking_mode,
                    reasoning_effort=self._model_config.reasoning_effort,
                    retries=0,
                    output_retries=2,
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="succeeded",
                    input_artifact_refs=[
                        f"round.{context.round_no:02d}.scoring.scoring_input_refs",
                        f"resumes/{candidate.resume_id}.json",
                        "input.scoring_policy",
                    ],
                    output_artifact_refs=[f"round.{context.round_no:02d}.scoring.scorecards"],
                    input_payload_sha256=text_sha256(user_prompt),
                    structured_output_sha256=json_sha256(result.model_dump(mode="json")),
                    prompt_chars=len(self.prompt.content),
                    input_payload_chars=text_char_count(user_prompt),
                    output_chars=json_char_count(result.model_dump(mode="json")),
                    input_summary=(
                        f"round={context.round_no}; resume_id={candidate.resume_id}; "
                        f"summary={candidate.compact_summary()}"
                    ),
                    output_summary=(
                        f"fit_bucket={result.fit_bucket}; overall={result.overall_score}; "
                        f"must={result.must_have_match_score}; preferred={result.preferred_match_score}; "
                        f"risk={result.risk_score}"
                    ),
                    cache_hit=False,
                    cache_key=cache_key,
                    cache_lookup_latency_ms=cache_lookup_latency_ms,
                    prompt_cache_key=prompt_cache_key,
                    prompt_cache_retention=prompt_cache_retention,
                    provider_usage=provider_usage,
                    cached_input_tokens=cached_input_tokens,
                ),
            )
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                model=self._model_config.model_id,
                call_id=call_id,
                status="succeeded",
                latency_ms=latency_ms,
                summary=result.reasoning_summary,
                artifact_paths=artifact_paths,
                payload={},
            )
            return result, None
        except TimeoutError:
            latency_ms = max(1, int((perf_counter() - started_at_clock) * 1000))
            error_message = f"scoring timed out after {self.settings.scoring_timeout_seconds:g}s"
            result = _timeout_scored_candidate(
                context=context,
                timeout_seconds=self.settings.scoring_timeout_seconds,
            )
            tracer.append_jsonl(
                f"round.{context.round_no:02d}.scoring.scoring_calls",
                LLMCallSnapshot(
                    stage="scoring",
                    call_id=call_id,
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model_id=self._model_config.model_id,
                    provider=self._model_config.provider_label,
                    protocol_family=self._model_config.protocol_family,  # ty:ignore[invalid-argument-type]
                    endpoint_kind=self._model_config.endpoint_kind,
                    endpoint_region=self._model_config.endpoint_region,
                    prompt_hash=self.prompt.sha256,
                    prompt_snapshot_path="assets/prompts/scoring.md",
                    structured_output_mode=self._model_config.structured_output_mode,
                    thinking_mode=self._model_config.thinking_mode,
                    reasoning_effort=self._model_config.reasoning_effort,
                    retries=0,
                    output_retries=2,
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="failed",
                    input_artifact_refs=[
                        f"round.{context.round_no:02d}.scoring.scoring_input_refs",
                        f"resumes/{candidate.resume_id}.json",
                        "input.scoring_policy",
                    ],
                    output_artifact_refs=[f"round.{context.round_no:02d}.scoring.scorecards"],
                    input_payload_sha256=text_sha256(user_prompt),
                    structured_output_sha256=json_sha256(result.model_dump(mode="json")),
                    prompt_chars=len(self.prompt.content),
                    input_payload_chars=text_char_count(user_prompt),
                    output_chars=json_char_count(result.model_dump(mode="json")),
                    input_summary=(
                        f"round={context.round_no}; resume_id={candidate.resume_id}; "
                        f"summary={candidate.compact_summary()}"
                    ),
                    output_summary=(
                        f"fit_bucket={result.fit_bucket}; overall={result.overall_score}; "
                        f"must={result.must_have_match_score}; preferred={result.preferred_match_score}; "
                        f"risk={result.risk_score}"
                    ),
                    error_message=error_message,
                    failure_kind="timeout",
                    provider_failure_kind="provider_timeout",
                    cache_hit=False,
                    cache_key=cache_key,
                    cache_lookup_latency_ms=cache_lookup_latency_ms,
                    prompt_cache_key=prompt_cache_key,
                    prompt_cache_retention=prompt_cache_retention,
                ),
            )
            tracer.emit(
                "score_branch_failed",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                model=self._model_config.model_id,
                call_id=call_id,
                status="failed",
                latency_ms=latency_ms,
                summary=error_message,
                error_message=error_message,
                artifact_paths=artifact_paths,
                payload={"attempts": 1, "failure_kind": "timeout"},
            )
            return result, None
        except Exception as exc:  # noqa: BLE001
            latency_ms = max(1, int((perf_counter() - started_at_clock) * 1000))
            failure_kind, provider_failure_kind = _scoring_failure_category(exc)
            failure = ScoringFailure(
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                round_no=context.round_no,
                attempts=1,
                error_message=str(exc),
                latency_ms=latency_ms,
                failure_kind=failure_kind,
                provider_failure_kind=provider_failure_kind,
            )
            tracer.append_jsonl(
                f"round.{context.round_no:02d}.scoring.scoring_calls",
                LLMCallSnapshot(
                    stage="scoring",
                    call_id=call_id,
                    round_no=context.round_no,
                    resume_id=candidate.resume_id,
                    branch_id=branch_id,
                    model_id=self._model_config.model_id,
                    provider=self._model_config.provider_label,
                    protocol_family=self._model_config.protocol_family,  # ty:ignore[invalid-argument-type]
                    endpoint_kind=self._model_config.endpoint_kind,
                    endpoint_region=self._model_config.endpoint_region,
                    prompt_hash=self.prompt.sha256,
                    prompt_snapshot_path="assets/prompts/scoring.md",
                    structured_output_mode=self._model_config.structured_output_mode,
                    thinking_mode=self._model_config.thinking_mode,
                    reasoning_effort=self._model_config.reasoning_effort,
                    retries=0,
                    output_retries=2,
                    started_at=started_at_iso,
                    latency_ms=latency_ms,
                    status="failed",
                    input_artifact_refs=[
                        f"round.{context.round_no:02d}.scoring.scoring_input_refs",
                        f"resumes/{candidate.resume_id}.json",
                        "input.scoring_policy",
                    ],
                    output_artifact_refs=[],
                    input_payload_sha256=text_sha256(user_prompt),
                    structured_output_sha256=None,
                    prompt_chars=len(self.prompt.content),
                    input_payload_chars=text_char_count(user_prompt),
                    output_chars=0,
                    input_summary=(
                        f"round={context.round_no}; resume_id={candidate.resume_id}; "
                        f"summary={candidate.compact_summary()}"
                    ),
                    output_summary=None,
                    error_message=str(exc),
                    failure_kind=failure_kind,
                    provider_failure_kind=provider_failure_kind,
                    cache_hit=False,
                    cache_key=cache_key,
                    cache_lookup_latency_ms=cache_lookup_latency_ms,
                    prompt_cache_key=prompt_cache_key,
                    prompt_cache_retention=prompt_cache_retention,
                ),
            )
            tracer.emit(
                "score_branch_failed",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=branch_id,
                model=self._model_config.model_id,
                call_id=call_id,
                status="failed",
                latency_ms=latency_ms,
                summary=str(exc),
                error_message=str(exc),
                artifact_paths=artifact_paths,
                payload={
                    "attempts": 1,
                    "failure_kind": failure_kind,
                    "provider_failure_kind": provider_failure_kind,
                },
            )
            return None, failure

    async def _score_one_live(
        self,
        *,
        prompt: str,
        agent: Agent[None, ScoredCandidateDraft],
    ) -> tuple[ScoredCandidateDraft, ProviderUsageSnapshot | None]:
        result = await agent.run(prompt)
        return result.output, provider_usage_from_result(result)


def _materialize_scored_candidate(
    *,
    draft: ScoredCandidateDraft,
    scoring_policy: ScoringPolicy,
    resume_id: str,
    source_round: int,
    source_provider: str | None = None,
    score_evidence_source: str | None = None,
    card_scorecard_ref: str | None = None,
    detail_scorecard_ref: str | None = None,
    score_delta: int | None = None,
    detail_open_reason: str | None = None,
    detail_open_policy_version: str | None = None,
) -> ScoredCandidate:
    applicability = score_dimension_applicability(scoring_policy)
    overall_score = calculate_overall_score(
        must_have_match_score=draft.must_have_match_score,
        preferred_match_score=draft.preferred_match_score,
        risk_score=draft.risk_score,
        applicability=applicability,
    )
    return ScoredCandidate(
        resume_id=resume_id,
        source_provider=source_provider,
        source_round=source_round,
        fit_bucket=draft.fit_bucket,
        overall_score=overall_score,
        must_have_match_score=draft.must_have_match_score,
        preferred_match_score=draft.preferred_match_score,
        risk_score=draft.risk_score,
        risk_flags=draft.risk_flags,
        reasoning_summary=draft.reasoning_summary,
        evidence=_derived_evidence(draft),
        confidence=_derived_confidence(draft=draft, overall_score=overall_score),
        matched_must_haves=draft.matched_must_haves,
        missing_must_haves=draft.missing_must_haves,
        matched_preferences=draft.matched_preferences,
        negative_signals=draft.negative_signals,
        strengths=_derived_strengths(draft),
        weaknesses=_derived_weaknesses(draft),
        score_evidence_source=score_evidence_source,
        card_scorecard_ref=card_scorecard_ref,
        detail_scorecard_ref=detail_scorecard_ref,
        score_delta=score_delta,
        detail_open_reason=detail_open_reason,
        detail_open_policy_version=detail_open_policy_version,
    )


def _timeout_scored_candidate(*, context: ScoringContext, timeout_seconds: float) -> ScoredCandidate:
    candidate = context.normalized_resume
    applicability = score_dimension_applicability(context.scoring_policy)
    return ScoredCandidate(
        resume_id=candidate.resume_id,
        fit_bucket="not_fit",
        overall_score=0,
        must_have_match_score=0,
        preferred_match_score=0 if applicability.preferred else None,
        risk_score=100 if applicability.risk else None,
        risk_flags=["scoring_timeout"],
        reasoning_summary=(
            f"Scoring timed out after {timeout_seconds:g}s before producing a reliable assessment; "
            "excluded from the ranked pool."
        ),
        evidence=[],
        confidence="low",
        matched_must_haves=[],
        missing_must_haves=context.scoring_policy.must_have_capabilities,
        matched_preferences=[],
        negative_signals=["scoring_timeout"],
        strengths=[],
        weaknesses=["Scoring did not complete within the configured timeout."],
        source_round=candidate.source_round or context.round_no,
        source_provider=candidate.source_provider,
        score_evidence_source=candidate.score_evidence_source,
        card_scorecard_ref=candidate.card_scorecard_ref,
        detail_scorecard_ref=candidate.detail_scorecard_ref,
        score_delta=candidate.score_delta,
        detail_open_reason=candidate.detail_open_reason,
        detail_open_policy_version=candidate.detail_open_policy_version,
    )


def _attach_runtime_scoring_metadata(result: ScoredCandidate, *, candidate: NormalizedResume) -> ScoredCandidate:
    return result.model_copy(
        update={
            "source_provider": candidate.source_provider,
            "score_evidence_source": candidate.score_evidence_source,
            "card_scorecard_ref": candidate.card_scorecard_ref,
            "detail_scorecard_ref": candidate.detail_scorecard_ref,
            "score_delta": candidate.score_delta,
            "detail_open_reason": candidate.detail_open_reason,
            "detail_open_policy_version": candidate.detail_open_policy_version,
        }
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


def _derived_confidence(*, draft: ScoredCandidateDraft, overall_score: int) -> ScoringConfidence:
    score_gap = abs(overall_score - draft.must_have_match_score)
    high_risk = risk_at_or_above(draft.risk_score, 65)
    low_risk = risk_at_or_below(draft.risk_score, 35)
    if draft.fit_bucket == "fit":
        if (
            overall_score >= 75
            and draft.must_have_match_score >= 70
            and low_risk
            and score_gap <= 25
        ):
            return "high"
        if overall_score < 60 or draft.must_have_match_score < 50 or high_risk or score_gap > 35:
            return "low"
        return "medium"
    if overall_score <= 55 or draft.must_have_match_score <= 50 or risk_at_or_above(draft.risk_score, 60):
        return "high"
    if overall_score >= 75 and draft.must_have_match_score >= 70 and low_risk:
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
