from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from cv_match.clients.cts_client import CTSClient, CTSClientProtocol, CTSFetchResult, MockCTSClient
from cv_match.config import AppSettings
from cv_match.controller import ReActController
from cv_match.controller.strategy_bootstrap import bootstrap_search_strategy, build_cts_query_from_strategy
from cv_match.finalize.finalizer import Finalizer
from cv_match.models import (
    CTSQuery,
    ControllerDecision,
    ControllerStateView,
    FinalResult,
    NormalizedResume,
    PoolDecision,
    ReflectionDecision,
    ReflectionSummaryView,
    ResumeCandidate,
    ScoredCandidate,
    ScoringContext,
    SearchAttempt,
    SearchObservation,
    SearchObservationView,
    SearchStrategy,
    TopPoolEntryView,
    scored_candidate_sort_key,
    unique_strings,
)
from cv_match.normalization import ResumeNormalizer
from cv_match.prompting import PromptRegistry
from cv_match.reflection.critic import ReflectionCritic
from cv_match.scoring.scorer import ResumeScorer
from cv_match.tracing import RunTracer

TOOL_CAPABILITY_NOTES = [
    "Only one external tool is available to the controller: search_cts.",
    "CTS-safe filters are limited to company, position, school, work_content, and location.",
    "The runtime never forwards the full JD to CTS.",
    "Same-round refill uses pagination only; it does not mutate the query semantics.",
    "If CTS keeps returning duplicates, runtime stops refill with exhausted_reason=no_progress_repeated_results.",
]

CANONICAL_STOP_REASONS = {
    "enough_high_fit_candidates",
    "insufficient_new_candidates",
    "no_progress_repeated_results",
    "max_rounds_reached",
    "reflection_stop",
    "controller_stop",
    "target_satisfied",
    "cts_exhausted",
    "max_pages_reached",
    "max_attempts_reached",
}


@dataclass
class RunArtifacts:
    final_result: FinalResult
    final_markdown: str
    run_id: str
    run_dir: Path
    trace_log_path: Path


class RunStageError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_message = message


class WorkflowRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.prompts = PromptRegistry(settings.prompt_dir)
        prompt_map = self.prompts.load_many(["controller", "scoring", "reflection", "finalize"])
        self.controller = ReActController(settings, prompt_map["controller"])
        self.resume_scorer = ResumeScorer(settings, prompt_map["scoring"])
        self.reflection_critic = ReflectionCritic(settings, prompt_map["reflection"])
        self.finalizer = Finalizer(settings, prompt_map["finalize"])
        self.resume_normalizer = ResumeNormalizer()
        self.cts_client: CTSClientProtocol = MockCTSClient(settings) if settings.mock_cts else CTSClient(settings)

    def run(self, *, jd: str, notes: str) -> RunArtifacts:
        tracer = RunTracer(self.settings.runs_path)
        try:
            candidate_store: dict[str, ResumeCandidate] = {}
            normalized_store: dict[str, NormalizedResume] = {}
            seen_resume_ids: set[str] = set()
            seen_dedup_keys: set[str] = set()
            top_scored: list[ScoredCandidate] = []
            current_strategy = bootstrap_search_strategy(jd=jd, notes=notes)
            latest_search_observation: SearchObservation | None = None
            previous_reflection: ReflectionDecision | None = None
            stop_reason = "max_rounds_reached"
            consecutive_shortage_rounds = 0
            shortage_history: list[int] = []
            rounds_executed = 0

            tracer.write_json("run_config.json", self._build_public_run_config())
            input_snapshot = {
                "jd_chars": len(jd),
                "notes_chars": len(notes),
                "jd_sha256": hashlib.sha256(jd.encode("utf-8")).hexdigest(),
                "notes_sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
                "jd_preview": self._preview_text(jd, limit=180),
                "notes_preview": self._preview_text(notes, limit=180),
            }
            tracer.write_json("input_snapshot.json", input_snapshot)
            tracer.emit(
                "run_started",
                summary="Starting one-tool ReAct runtime.",
                payload={
                    "mock_cts": self.settings.mock_cts,
                    "llm_backend_mode": self.settings.llm_backend_mode,
                },
            )
            tracer.emit(
                "user_input_captured",
                summary=(
                    f"Captured sanitized input snapshot; jd_chars={len(jd)}, notes_chars={len(notes)}. "
                    f"JD preview: {input_snapshot['jd_preview']}"
                ),
                payload=input_snapshot,
            )
            self._require_live_llm_config()

            for round_no in range(1, self.settings.max_rounds + 1):
                target_new = 10 if round_no == 1 else 5
                state_view = ControllerStateView(
                    round_no=round_no,
                    min_rounds=self.settings.min_rounds,
                    max_rounds=self.settings.max_rounds,
                    target_new=target_new,
                    jd_summary=self._preview_text(jd, limit=240),
                    notes_summary=self._preview_text(notes, limit=240),
                    current_strategy=current_strategy,
                    current_top_pool=self._summarize_top_pool(top_scored),
                    latest_search_observation=self._summarize_search_observation(latest_search_observation),
                    previous_reflection=self._summarize_reflection(previous_reflection),
                    shortage_history=shortage_history,
                    consecutive_shortage_rounds=consecutive_shortage_rounds,
                    tool_capability_notes=TOOL_CAPABILITY_NOTES,
                )
                tracer.emit(
                    "react_step_started",
                    round_no=round_no,
                    model=self.settings.strategy_model,
                    summary=f"Planning round {round_no} action.",
                )
                try:
                    controller_decision = self.controller.decide(state_view=state_view)
                except Exception as exc:  # noqa: BLE001
                    raise RunStageError("controller", str(exc)) from exc
                controller_decision = self._sanitize_controller_decision(
                    decision=controller_decision,
                    current_strategy=current_strategy,
                    target_new=target_new,
                    seen_ids=sorted(seen_resume_ids),
                    round_no=round_no,
                )
                if controller_decision.action == "stop" and round_no <= self.settings.min_rounds:
                    tracer.emit(
                        "react_decision",
                        round_no=round_no,
                        model=self.settings.strategy_model,
                        summary="Controller attempted early stop before min_rounds; runtime will continue searching.",
                        payload=controller_decision.model_dump(mode="json"),
                    )
                    controller_decision = self._force_continue_decision(
                        current_strategy=current_strategy,
                        seen_ids=sorted(seen_resume_ids),
                        target_new=target_new,
                    )
                tracer.emit(
                    "react_decision",
                    round_no=round_no,
                    model=self.settings.strategy_model,
                    stop_reason=controller_decision.stop_reason if controller_decision.action == "stop" else None,
                    summary=controller_decision.thought_summary,
                    payload=controller_decision.model_dump(mode="json"),
                )
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/react_step.json",
                    {
                        "state_view": state_view.model_dump(mode="json"),
                        "controller_decision": controller_decision.model_dump(mode="json"),
                    },
                )

                if controller_decision.action == "stop":
                    stop_reason = self._normalize_stop_reason(
                        proposed=controller_decision.stop_reason,
                        top_candidates=top_scored,
                        shortage_count=0,
                        search_observation=latest_search_observation,
                    )
                    break

                current_strategy = controller_decision.working_strategy
                query = controller_decision.cts_query
                assert query is not None

                try:
                    new_candidates, search_observation, search_attempts, duplicate_count = self._execute_search_tool(
                        round_no=round_no,
                        query=query,
                        target_new=target_new,
                        seen_resume_ids=seen_resume_ids,
                        seen_dedup_keys=seen_dedup_keys,
                        tracer=tracer,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise RunStageError("search_cts", str(exc)) from exc
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/search_observation.json",
                    search_observation.model_dump(mode="json"),
                )
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/search_attempts.json",
                    [item.model_dump(mode="json") for item in search_attempts],
                )

                for candidate in new_candidates:
                    candidate_store[candidate.resume_id] = candidate
                seen_resume_ids.update(candidate.resume_id for candidate in new_candidates)
                seen_dedup_keys.update(candidate.dedup_key for candidate in new_candidates)

                shortage_count = search_observation.shortage_count
                shortage_history.append(shortage_count)
                consecutive_shortage_rounds = consecutive_shortage_rounds + 1 if shortage_count else 0
                latest_search_observation = search_observation

                scoring_pool = self._build_scoring_pool(
                    round_no=round_no,
                    top_scored=top_scored,
                    new_candidates=new_candidates,
                    candidate_store=candidate_store,
                )
                scoring_context = ScoringContext(
                    round_no=round_no,
                    must_have_keywords=current_strategy.must_have_keywords,
                    preferred_keywords=current_strategy.preferred_keywords,
                    negative_keywords=current_strategy.negative_keywords,
                    hard_filters=current_strategy.hard_filters,
                    soft_filters=current_strategy.soft_filters,
                    scoring_rationale=current_strategy.search_rationale,
                )
                normalized_scoring_pool = self._normalize_scoring_pool(
                    round_no=round_no,
                    scoring_pool=scoring_pool,
                    tracer=tracer,
                    normalized_store=normalized_store,
                )
                tracer.write_jsonl(
                    f"rounds/round_{round_no:02d}/normalized_resumes.jsonl",
                    [item.model_dump(mode="json") for item in normalized_scoring_pool],
                )

                tracer.emit(
                    "scoring_fanout_started",
                    round_no=round_no,
                    summary=(
                        f"Scoring {len(normalized_scoring_pool)} resumes with max_concurrency="
                        f"{self.settings.scoring_max_concurrency}."
                    ),
                )
                scored_candidates, scoring_failures = self.resume_scorer.score_candidates_parallel(
                    candidates=normalized_scoring_pool,
                    context=scoring_context,
                    tracer=tracer,
                )
                retried_successes = sum(1 for item in scored_candidates if item.retry_count > 0)
                tracer.emit(
                    "scoring_fanin_completed",
                    round_no=round_no,
                    summary=(
                        f"Scored {len(scored_candidates)} resumes; "
                        f"retried_successes={retried_successes}; failures={len(scoring_failures)}."
                    ),
                    payload={
                        "successful_scores": len(scored_candidates) - retried_successes,
                        "retried_successes": retried_successes,
                        "final_failures": len(scoring_failures),
                    },
                )
                if scoring_failures:
                    raise RunStageError(
                        "scoring",
                        self._format_scoring_failure_message(scoring_failures),
                    )

                ranked = sorted(scored_candidates, key=scored_candidate_sort_key)
                previous_top_ids = {candidate.resume_id for candidate in top_scored}
                top_scored = ranked[:5]
                pool_decisions = self._build_pool_decisions(
                    round_no=round_no,
                    ranked_candidates=ranked,
                    top_candidates=top_scored,
                    previous_top_ids=previous_top_ids,
                )
                tracer.emit(
                    "top_pool_updated",
                    round_no=round_no,
                    summary=", ".join(candidate.resume_id for candidate in top_scored) or "No scored resumes.",
                    payload={"top_pool_ids": [candidate.resume_id for candidate in top_scored]},
                )
                tracer.emit(
                    "pool_decision_recorded",
                    round_no=round_no,
                    summary=f"Recorded {len(pool_decisions)} pool decisions.",
                    payload={"decision_count": len(pool_decisions)},
                )
                tracer.write_jsonl(
                    f"rounds/round_{round_no:02d}/scorecards.jsonl",
                    [item.model_dump(mode="json") for item in ranked],
                )
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/selected_candidates.json",
                    [item.model_dump(mode="json") for item in pool_decisions if item.decision in {"selected", "retained"}],
                )
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/dropped_candidates.json",
                    [item.model_dump(mode="json") for item in pool_decisions if item.decision == "dropped"],
                )

                dropped_candidates = [
                    candidate
                    for candidate in ranked
                    if candidate.resume_id not in {item.resume_id for item in top_scored}
                ]
                reflection = None
                round_stop_reason = None
                if self.settings.enable_reflection:
                    tracer.emit(
                        "reflection_started",
                        round_no=round_no,
                        model=self.settings.reflection_model,
                        summary="Starting round reflection.",
                    )
                    try:
                        reflection = self.reflection_critic.reflect(
                            round_no=round_no,
                            strategy=current_strategy,
                            search_observation=search_observation,
                            search_attempts=search_attempts,
                            new_candidate_summaries=search_observation.new_candidate_summaries,
                            scored_candidates=ranked,
                            top_candidates=top_scored,
                            dropped_candidates=dropped_candidates,
                            shortage_count=shortage_count,
                            scoring_failure_count=len(scoring_failures),
                        )
                    except Exception as exc:  # noqa: BLE001
                        raise RunStageError("reflection", str(exc)) from exc
                    current_strategy, changes = self._apply_reflection(
                        strategy=current_strategy,
                        reflection=reflection,
                    )
                    reflection = reflection.model_copy(update={"strategy_changes": changes})
                    previous_reflection = reflection
                    effective_stop_reason = None
                    if round_no >= self.settings.min_rounds and reflection.decision == "stop":
                        stop_reason = self._normalize_stop_reason(
                            proposed=reflection.stop_reason,
                            top_candidates=top_scored,
                            shortage_count=shortage_count,
                            search_observation=search_observation,
                        )
                        round_stop_reason = stop_reason
                        effective_stop_reason = round_stop_reason
                    tracer.emit(
                        "reflection_decision",
                        round_no=round_no,
                        model=self.settings.reflection_model,
                        stop_reason=effective_stop_reason,
                        summary=reflection.reflection_summary,
                        payload=reflection.model_dump(mode="json"),
                    )
                else:
                    previous_reflection = None

                if (
                    round_no >= self.settings.min_rounds
                    and consecutive_shortage_rounds >= 2
                    and not (self.settings.enable_reflection and reflection is not None and reflection.decision == "stop")
                ):
                    stop_reason = latest_search_observation.exhausted_reason or "insufficient_new_candidates"
                    round_stop_reason = stop_reason

                tracer.write_text(
                    f"rounds/round_{round_no:02d}/round_review.md",
                    self._render_round_review(
                        round_no=round_no,
                        observation=search_observation,
                        pool_decisions=pool_decisions,
                        top_candidates=top_scored,
                        dropped_candidates=dropped_candidates,
                        reflection=reflection,
                        scoring_failures=scoring_failures,
                        stop_reason=round_stop_reason,
                    ),
                )

                rounds_executed = round_no
                if round_no >= self.settings.min_rounds:
                    if self.settings.enable_reflection and reflection is not None and reflection.decision == "stop":
                        break
                    if consecutive_shortage_rounds >= 2:
                        break

            if rounds_executed == 0:
                rounds_executed = (
                    min(self.settings.min_rounds, self.settings.max_rounds)
                    if stop_reason != "max_rounds_reached"
                    else 0
                )

            try:
                final_result = self.finalizer.finalize(
                    run_id=tracer.run_id,
                    run_dir=str(tracer.run_dir),
                    rounds_executed=rounds_executed,
                    stop_reason=stop_reason,
                    ranked_candidates=top_scored,
                )
            except Exception as exc:  # noqa: BLE001
                raise RunStageError("finalization", str(exc)) from exc
            final_markdown = self._render_final_markdown(final_result)
            tracer.write_json("final_candidates.json", final_result.model_dump(mode="json"))
            tracer.emit(
                "final_answer_created",
                summary=f"Prepared final shortlist with {len(final_result.candidates)} candidates.",
            )
            tracer.write_text("final_answer.md", final_markdown)
            tracer.emit(
                "run_finished",
                stop_reason=stop_reason,
                summary=f"Run completed after {rounds_executed} rounds.",
            )
            return RunArtifacts(
                final_result=final_result,
                final_markdown=final_markdown,
                run_id=tracer.run_id,
                run_dir=tracer.run_dir,
                trace_log_path=tracer.trace_log_path,
            )
        except Exception as exc:  # noqa: BLE001
            stage = exc.stage if isinstance(exc, RunStageError) else "runtime"
            tracer.emit(
                "run_failed",
                summary=str(exc),
                payload={
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise
        finally:
            tracer.close()

    def _build_public_run_config(self) -> dict[str, object]:
        return {
            "settings": {
                "cts_base_url": self.settings.cts_base_url,
                "cts_timeout_seconds": self.settings.cts_timeout_seconds,
                "cts_spec_path": self.settings.cts_spec_path,
                "cts_credentials_configured": bool(self.settings.cts_tenant_key and self.settings.cts_tenant_secret),
                "strategy_model": self.settings.strategy_model,
                "scoring_model": self.settings.scoring_model,
                "finalize_model": self.settings.finalize_model,
                "reflection_model": self.settings.reflection_model,
                "reasoning_effort": self.settings.reasoning_effort,
                "min_rounds": self.settings.min_rounds,
                "max_rounds": self.settings.max_rounds,
                "scoring_max_concurrency": self.settings.scoring_max_concurrency,
                "search_max_pages_per_round": self.settings.search_max_pages_per_round,
                "search_max_attempts_per_round": self.settings.search_max_attempts_per_round,
                "search_no_progress_limit": self.settings.search_no_progress_limit,
                "mock_cts": self.settings.mock_cts,
                "enable_reflection": self.settings.enable_reflection,
            },
            "llm_backend_mode": self.settings.llm_backend_mode,
            "selected_openapi_file": str(self.settings.spec_file),
            "prompt_hashes": self.prompts.prompt_hashes(),
            "prompt_files": self.prompts.prompt_files(),
            "tooling": {
                "controller_actions": ["search_cts", "stop"],
                "external_tools": ["search_cts"],
            },
        }

    def _require_live_llm_config(self) -> None:
        try:
            self.settings.require_openai_api_key()
        except ValueError as exc:
            raise RunStageError("llm_preflight", str(exc)) from exc

    def _format_scoring_failure_message(self, failures: list[object]) -> str:
        resume_ids = [getattr(item, "resume_id", "unknown") for item in failures]
        return (
            f"Scoring failed for {len(failures)} resume(s): {', '.join(resume_ids)}."
        )

    def _preview_text(self, text: str, *, limit: int) -> str:
        collapsed = re.sub(r"\s+", " ", text).strip()
        if len(collapsed) <= limit:
            return collapsed
        return f"{collapsed[:limit].rstrip()}..."

    def _summarize_top_pool(self, candidates: list[ScoredCandidate]) -> list[TopPoolEntryView]:
        return [
            TopPoolEntryView(
                resume_id=item.resume_id,
                fit_bucket=item.fit_bucket,
                overall_score=item.overall_score,
                must_have_match_score=item.must_have_match_score,
                risk_score=item.risk_score,
                matched_must_haves=item.matched_must_haves[:4],
                risk_flags=item.risk_flags[:4],
                reasoning_summary=item.reasoning_summary,
            )
            for item in candidates
        ]

    def _summarize_search_observation(self, observation: SearchObservation | None) -> SearchObservationView | None:
        if observation is None:
            return None
        return SearchObservationView(
            unique_new_count=observation.unique_new_count,
            shortage_count=observation.shortage_count,
            fetch_attempt_count=observation.fetch_attempt_count,
            exhausted_reason=observation.exhausted_reason,
            new_candidate_summaries=observation.new_candidate_summaries[:5],
            adapter_notes=observation.adapter_notes[:5],
        )

    def _summarize_reflection(self, reflection: ReflectionDecision | None) -> ReflectionSummaryView | None:
        if reflection is None:
            return None
        return ReflectionSummaryView(
            decision=reflection.decision,
            stop_reason=reflection.stop_reason,
            reflection_summary=reflection.reflection_summary,
            strategy_changes=reflection.strategy_changes[:5],
        )

    def _sanitize_controller_decision(
        self,
        *,
        decision: ControllerDecision,
        current_strategy: SearchStrategy,
        target_new: int,
        seen_ids: list[str],
        round_no: int,
    ) -> ControllerDecision:
        strategy = (decision.working_strategy or current_strategy).normalized()
        if decision.action == "stop":
            return decision.model_copy(update={"working_strategy": strategy})
        base_query = build_cts_query_from_strategy(
            strategy=strategy,
            target_new=target_new,
            exclude_ids=seen_ids,
            keywords=decision.cts_query.keywords if decision.cts_query is not None else None,
            rationale=decision.cts_query.rationale if decision.cts_query is not None else None,
            adapter_notes=decision.cts_query.adapter_notes if decision.cts_query is not None else None,
        )
        return decision.model_copy(
            update={
                "working_strategy": strategy,
                "cts_query": base_query,
                "stop_reason": None,
                "thought_summary": decision.thought_summary or f"Search round {round_no}.",
            }
        )

    def _force_continue_decision(
        self,
        *,
        current_strategy: SearchStrategy,
        seen_ids: list[str],
        target_new: int,
    ) -> ControllerDecision:
        query = build_cts_query_from_strategy(
            strategy=current_strategy,
            target_new=target_new,
            exclude_ids=seen_ids,
        )
        return ControllerDecision(
            thought_summary="Runtime override: continue until min_rounds is satisfied.",
            action="search_cts",
            decision_rationale="Early stop is not allowed before min_rounds.",
            working_strategy=current_strategy,
            cts_query=query,
        )

    def _normalize_stop_reason(
        self,
        *,
        proposed: str | None,
        top_candidates: list[ScoredCandidate],
        shortage_count: int,
        search_observation: SearchObservation | None,
    ) -> str:
        if proposed in CANONICAL_STOP_REASONS:
            return proposed
        if shortage_count > 0 and search_observation is not None:
            return search_observation.exhausted_reason or "insufficient_new_candidates"
        if sum(1 for item in top_candidates if item.fit_bucket == "fit") >= 5:
            return "enough_high_fit_candidates"
        return "reflection_stop"

    def _execute_search_tool(
        self,
        *,
        round_no: int,
        query: CTSQuery,
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
    ) -> tuple[list[ResumeCandidate], SearchObservation, list[SearchAttempt], int]:
        tracer.emit(
            "tool_called",
            round_no=round_no,
            tool_name="search_cts",
            summary=query.keyword_query,
            payload=query.model_dump(mode="json"),
        )
        all_new_candidates: list[ResumeCandidate] = []
        local_seen_keys = set(seen_dedup_keys)
        attempts: list[SearchAttempt] = []
        raw_candidate_count = 0
        duplicate_count = 0
        adapter_notes: list[str] = []
        cumulative_latency_ms = 0
        consecutive_zero_gain_attempts = 0
        exhausted_reason: str | None = None
        page = max(query.page, 1)
        attempt_no = 0

        while True:
            if attempt_no >= self.settings.search_max_attempts_per_round:
                exhausted_reason = "max_attempts_reached"
                break
            if page > self.settings.search_max_pages_per_round:
                exhausted_reason = "max_pages_reached"
                break

            remaining_gap = target_new - len(all_new_candidates)
            if remaining_gap <= 0:
                exhausted_reason = "target_satisfied"
                break

            attempt_no += 1
            attempt_query = query.model_copy(
                update={
                    "page": page,
                    "page_size": remaining_gap,
                    "exclude_ids": sorted(seen_resume_ids),
                }
            )
            fetch_result = self._search_once(
                attempt_query=attempt_query,
                round_no=round_no,
                attempt_no=attempt_no,
                tracer=tracer,
            )
            raw_candidate_count += fetch_result.raw_candidate_count
            cumulative_latency_ms += fetch_result.latency_ms or 0
            adapter_notes = unique_strings(adapter_notes + fetch_result.adapter_notes)
            batch_new, batch_duplicates = self._dedup_batch(
                candidates=fetch_result.candidates,
                local_seen_keys=local_seen_keys,
            )
            duplicate_count += batch_duplicates
            all_new_candidates.extend(batch_new)
            if batch_new:
                consecutive_zero_gain_attempts = 0
            else:
                consecutive_zero_gain_attempts += 1

            continue_refill = True
            if len(all_new_candidates) >= target_new:
                continue_refill = False
                exhausted_reason = "target_satisfied"
            elif fetch_result.raw_candidate_count == 0:
                continue_refill = False
                exhausted_reason = "cts_exhausted"
            elif consecutive_zero_gain_attempts >= self.settings.search_no_progress_limit:
                continue_refill = False
                exhausted_reason = "no_progress_repeated_results"
            elif attempt_no >= self.settings.search_max_attempts_per_round:
                continue_refill = False
                exhausted_reason = "max_attempts_reached"
            elif page >= self.settings.search_max_pages_per_round:
                continue_refill = False
                exhausted_reason = "max_pages_reached"

            attempt = SearchAttempt(
                attempt_no=attempt_no,
                requested_page=attempt_query.page,
                requested_page_size=attempt_query.page_size,
                raw_candidate_count=fetch_result.raw_candidate_count,
                batch_duplicate_count=batch_duplicates,
                batch_unique_new_count=len(batch_new),
                cumulative_unique_new_count=len(all_new_candidates),
                consecutive_zero_gain_attempts=consecutive_zero_gain_attempts,
                continue_refill=continue_refill,
                exhausted_reason=None if continue_refill else exhausted_reason,
                adapter_notes=fetch_result.adapter_notes,
                request_payload=fetch_result.request_payload,
            )
            attempts.append(attempt)
            tracer.emit(
                "search_refill_attempted",
                round_no=round_no,
                tool_name="search_cts",
                summary=(
                    f"attempt={attempt_no}, page={attempt.requested_page}, "
                    f"new={attempt.batch_unique_new_count}, cumulative_new={attempt.cumulative_unique_new_count}"
                ),
                payload=attempt.model_dump(mode="json"),
            )
            if not continue_refill:
                break
            page += 1

        search_observation = SearchObservation(
            round_no=round_no,
            requested_count=target_new,
            raw_candidate_count=raw_candidate_count,
            unique_new_count=len(all_new_candidates),
            shortage_count=max(0, target_new - len(all_new_candidates)),
            fetch_attempt_count=len(attempts),
            exhausted_reason=exhausted_reason,
            new_resume_ids=[candidate.resume_id for candidate in all_new_candidates],
            new_candidate_summaries=[candidate.compact_summary() for candidate in all_new_candidates],
            adapter_notes=adapter_notes,
        )
        tracer.emit(
            "tool_succeeded",
            round_no=round_no,
            tool_name="search_cts",
            latency_ms=cumulative_latency_ms or None,
            summary=(
                f"search_cts completed; raw_candidate_count={search_observation.raw_candidate_count}; "
                f"unique_new_count={search_observation.unique_new_count}; "
                f"shortage={search_observation.shortage_count}"
            ),
            stop_reason=search_observation.exhausted_reason if search_observation.shortage_count else None,
            payload=search_observation.model_dump(mode="json"),
        )
        tracer.emit(
            "dedup_applied",
            round_no=round_no,
            summary=(
                f"Removed {duplicate_count} duplicates after refill; "
                f"shortage={search_observation.shortage_count}."
            ),
            payload={
                "seen_resume_ids_before_round": sorted(seen_resume_ids),
                "new_resume_ids": search_observation.new_resume_ids,
                "fetch_attempt_count": search_observation.fetch_attempt_count,
                "exhausted_reason": search_observation.exhausted_reason,
            },
        )
        return all_new_candidates, search_observation, attempts, duplicate_count

    def _search_once(
        self,
        *,
        attempt_query: CTSQuery,
        round_no: int,
        attempt_no: int,
        tracer: RunTracer,
    ) -> CTSFetchResult:
        try:
            return self.cts_client.search(
                attempt_query,
                round_no=round_no,
                trace_id=f"{tracer.run_id}-r{round_no}-a{attempt_no}",
            )
        except Exception as exc:  # noqa: BLE001
            tracer.emit(
                "tool_failed",
                round_no=round_no,
                tool_name="search_cts",
                summary=str(exc),
                payload={
                    "attempt_no": attempt_no,
                    "page": attempt_query.page,
                    "page_size": attempt_query.page_size,
                },
            )
            raise

    def _dedup_batch(
        self,
        *,
        candidates: list[ResumeCandidate],
        local_seen_keys: set[str],
    ) -> tuple[list[ResumeCandidate], int]:
        batch_new: list[ResumeCandidate] = []
        duplicates = 0
        for candidate in candidates:
            if candidate.dedup_key in local_seen_keys:
                duplicates += 1
                continue
            local_seen_keys.add(candidate.dedup_key)
            batch_new.append(candidate)
        return batch_new, duplicates

    def _build_scoring_pool(
        self,
        *,
        round_no: int,
        top_scored: list[ScoredCandidate],
        new_candidates: list[ResumeCandidate],
        candidate_store: dict[str, ResumeCandidate],
    ) -> list[ResumeCandidate]:
        if round_no == 1:
            return list(new_candidates)
        pool: list[ResumeCandidate] = []
        used_ids: set[str] = set()
        for scored in top_scored:
            candidate = candidate_store.get(scored.resume_id)
            if candidate is None or candidate.resume_id in used_ids:
                continue
            pool.append(candidate)
            used_ids.add(candidate.resume_id)
        for candidate in new_candidates:
            if candidate.resume_id in used_ids:
                continue
            pool.append(candidate)
            used_ids.add(candidate.resume_id)
        return pool

    def _normalize_scoring_pool(
        self,
        *,
        round_no: int,
        scoring_pool: list[ResumeCandidate],
        tracer: RunTracer,
        normalized_store: dict[str, NormalizedResume],
    ) -> list[NormalizedResume]:
        normalized_pool: list[NormalizedResume] = []
        for candidate in scoring_pool:
            tracer.emit(
                "resume_normalization_started",
                round_no=round_no,
                resume_id=candidate.resume_id,
                summary=candidate.compact_summary(),
            )
            normalized = self.resume_normalizer.normalize(candidate)
            normalized_store[normalized.resume_id] = normalized
            tracer.write_json(
                f"resumes/{normalized.resume_id}.json",
                normalized.model_dump(mode="json"),
            )
            if (
                normalized.completeness_score < 70
                or normalized.used_fallback_id
                or normalized.missing_fields
                or normalized.normalization_notes
            ):
                tracer.emit(
                    "resume_normalization_warning",
                    round_no=round_no,
                    resume_id=normalized.resume_id,
                    summary=(
                        f"completeness={normalized.completeness_score}, "
                        f"missing={len(normalized.missing_fields)}, "
                        f"fallback_id={normalized.used_fallback_id}"
                    ),
                    payload={
                        "completeness_score": normalized.completeness_score,
                        "missing_fields_count": len(normalized.missing_fields),
                        "used_fallback_id": normalized.used_fallback_id,
                        "missing_fields": normalized.missing_fields,
                        "normalization_notes": normalized.normalization_notes,
                    },
                )
            tracer.emit(
                "resume_normalized",
                round_no=round_no,
                resume_id=normalized.resume_id,
                summary=normalized.compact_summary(),
                payload={
                    "completeness_score": normalized.completeness_score,
                    "missing_fields_count": len(normalized.missing_fields),
                    "used_fallback_id": normalized.used_fallback_id,
                },
            )
            normalized_pool.append(normalized)
        return normalized_pool

    def _build_pool_decisions(
        self,
        *,
        round_no: int,
        ranked_candidates: list[ScoredCandidate],
        top_candidates: list[ScoredCandidate],
        previous_top_ids: set[str],
    ) -> list[PoolDecision]:
        top_ids = {candidate.resume_id for candidate in top_candidates}
        decisions: list[PoolDecision] = []
        for rank, candidate in enumerate(top_candidates, start=1):
            decision_type = "retained" if candidate.resume_id in previous_top_ids else "selected"
            decisions.append(
                PoolDecision(
                    resume_id=candidate.resume_id,
                    round_no=round_no,
                    decision=decision_type,
                    rank_in_round=rank,
                    reasons_for_selection=(
                        candidate.strengths[:3]
                        or [f"Ranked into current top pool with score {candidate.overall_score}."]
                    ),
                    reasons_for_rejection=candidate.weaknesses[:2],
                    compared_against_pool_summary=(
                        f"Deterministically ranked #{rank} in the current scoring pool."
                    ),
                )
            )
        for rank, candidate in enumerate(ranked_candidates, start=1):
            if candidate.resume_id in top_ids:
                continue
            decisions.append(
                PoolDecision(
                    resume_id=candidate.resume_id,
                    round_no=round_no,
                    decision="dropped",
                    rank_in_round=rank,
                    reasons_for_selection=[],
                    reasons_for_rejection=(
                        candidate.weaknesses[:3]
                        or [f"Ranked below current top pool with score {candidate.overall_score}."]
                    ),
                    compared_against_pool_summary=(
                        f"Ranked #{rank}, outside the retained top pool boundary."
                    ),
                )
            )
        return decisions

    def _apply_reflection(
        self,
        *,
        strategy: SearchStrategy,
        reflection: ReflectionDecision,
    ) -> tuple[SearchStrategy, list[str]]:
        next_hard_filters = reflection.adjust_hard_filters or strategy.hard_filters
        next_soft_filters = reflection.adjust_soft_filters or strategy.soft_filters
        next_strategy = strategy.model_copy(
            update={
                "preferred_keywords": unique_strings(strategy.preferred_keywords + reflection.adjust_keywords),
                "negative_keywords": unique_strings(strategy.negative_keywords + reflection.adjust_negative_keywords),
                "hard_filters": next_hard_filters,
                "soft_filters": next_soft_filters,
                "strategy_version": strategy.strategy_version + 1,
            }
        ).normalized()
        changes: list[str] = []
        added_keywords = [item for item in next_strategy.preferred_keywords if item not in strategy.preferred_keywords]
        added_negatives = [item for item in next_strategy.negative_keywords if item not in strategy.negative_keywords]
        if added_keywords:
            changes.append(f"Added retrieval keywords: {', '.join(added_keywords)}.")
        if added_negatives:
            changes.append(f"Added negative keywords: {', '.join(added_negatives)}.")
        if next_strategy.hard_filters != strategy.hard_filters:
            changes.append("Updated hard filters.")
        if next_strategy.soft_filters != strategy.soft_filters:
            changes.append("Updated soft filters.")
        if not changes:
            changes.append("No strategy changes.")
        return next_strategy, changes

    def _render_round_review(
        self,
        *,
        round_no: int,
        observation: SearchObservation,
        pool_decisions: list[PoolDecision],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        reflection: ReflectionDecision | None,
        scoring_failures: list[object],
        stop_reason: str | None,
    ) -> str:
        selected = [item.resume_id for item in pool_decisions if item.decision == "selected"]
        retained = [item.resume_id for item in pool_decisions if item.decision == "retained"]
        dropped = [item.resume_id for item in pool_decisions if item.decision == "dropped"]
        drop_reason_counter = Counter(
            reason
            for item in pool_decisions
            if item.decision == "dropped"
            for reason in item.reasons_for_rejection
        )
        common_drop_reasons = ", ".join(
            f"{reason} x{count}" for reason, count in drop_reason_counter.most_common(3)
        ) or "None"
        failure_ids = [getattr(item, "resume_id", "unknown") for item in scoring_failures]
        lines = [
            f"# Round {round_no} Review",
            "",
            "## Search Outcome",
            "",
            f"- Requested new candidates: `{observation.requested_count}`",
            f"- Unique new candidates: `{observation.unique_new_count}`",
            f"- Shortage: `{observation.shortage_count}`",
            f"- Fetch attempts: `{observation.fetch_attempt_count}`",
            f"- Exhausted reason: `{observation.exhausted_reason or 'none'}`",
            "",
            "## Top Pool Delta",
            "",
            f"- Current top pool: {', '.join(candidate.resume_id for candidate in top_candidates) or 'None'}",
            f"- Newly selected: {', '.join(selected) or 'None'}",
            f"- Retained: {', '.join(retained) or 'None'}",
            f"- Dropped from pool: {', '.join(dropped) or 'None'}",
            "",
            "## Common Drop Reasons",
            "",
            f"- Themes: {common_drop_reasons}",
            f"- Dropped candidates reviewed: `{len(dropped_candidates)}`",
            "",
            "## Scoring Failures",
            "",
            f"- Failure count: `{len(scoring_failures)}`",
            f"- Failed resumes: {', '.join(failure_ids) or 'None'}",
            "",
            "## Reflection & Stop Signal",
            "",
        ]
        if reflection is not None:
            lines.extend(
                [
                    f"- Reflection summary: {reflection.reflection_summary}",
                    f"- Reflection decision: `{reflection.decision}`",
                    f"- Strategy changes: {', '.join(reflection.strategy_changes) or 'None'}",
                ]
            )
        else:
            lines.append("- Reflection summary: Reflection disabled.")
        lines.append(f"- Round stop signal: `{stop_reason or 'continue'}`")
        return "\n".join(lines).strip() + "\n"

    def _render_final_markdown(self, final_result: FinalResult) -> str:
        lines = [
            "# Final Shortlist",
            "",
            f"- Run ID: `{final_result.run_id}`",
            f"- Rounds: `{final_result.rounds_executed}`",
            f"- Stop reason: `{final_result.stop_reason}`",
            f"- Summary: {final_result.summary}",
            "",
        ]
        for candidate in final_result.candidates:
            lines.extend(
                [
                    f"## Rank {candidate.rank}: `{candidate.resume_id}`",
                    "",
                    f"- Score: `{candidate.final_score}`",
                    f"- Fit bucket: `{candidate.fit_bucket}`",
                    f"- Source round: `{candidate.source_round}`",
                    f"- Match summary: {candidate.match_summary}",
                    f"- Strengths: {', '.join(candidate.strengths) or 'None'}",
                    f"- Weaknesses: {', '.join(candidate.weaknesses) or 'None'}",
                    f"- Must-have hits: {', '.join(candidate.matched_must_haves) or 'None'}",
                    f"- Preference hits: {', '.join(candidate.matched_preferences) or 'None'}",
                    f"- Risk flags: {', '.join(candidate.risk_flags) or 'None'}",
                    f"- Why selected: {candidate.why_selected}",
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"
