from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

from cv_match.clients.cts_client import CTSClient, CTSClientProtocol, CTSFetchResult, MockCTSClient
from cv_match.config import AppSettings
from cv_match.controller import ReActController
from cv_match.finalize.finalizer import Finalizer
from cv_match.llm import model_provider, preflight_models
from cv_match.models import (
    CTSQuery,
    CitySearchSummary,
    ControllerDecision,
    FinalResult,
    LocationExecutionPhase,
    NormalizedResume,
    PoolDecision,
    ReflectionAdvice,
    ResumeCandidate,
    QueryTermCandidate,
    RoundState,
    RunState,
    ScoredCandidate,
    ScoringPolicy,
    SearchControllerDecision,
    SearchAttempt,
    SearchObservation,
    SentQueryRecord,
    scored_candidate_sort_key,
    unique_strings,
)
from cv_match.normalization import normalize_resume
from cv_match.prompting import PromptRegistry
from cv_match.reflection.critic import ReflectionCritic
from cv_match.requirements import (
    RequirementExtractor,
    build_input_truth,
    build_scoring_policy,
)
from cv_match.retrieval import (
    allocate_balanced_city_targets,
    build_default_filter_plan,
    build_location_execution_plan,
    build_round_retrieval_plan,
    canonicalize_filter_plan,
    canonicalize_controller_query_terms,
    project_constraints_to_cts,
    select_query_terms,
)
from cv_match.runtime.context_builder import (
    build_controller_context,
    build_finalize_context,
    build_reflection_context,
    build_scoring_context,
    top_candidates,
)
from cv_match.scoring.scorer import ResumeScorer
from cv_match.tracing import LLMCallSnapshot, RunTracer

CANONICAL_STOP_REASONS = {
    "enough_high_fit_candidates",
    "insufficient_new_candidates",
    "no_progress_repeated_results",
    "max_rounds_reached",
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
    candidate_store: dict[str, ResumeCandidate]
    normalized_store: dict[str, NormalizedResume]


class RunStageError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_message = message


@dataclass
class _CityExecutionState:
    next_page: int = 1
    exhausted: bool = False


class WorkflowRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.prompts = PromptRegistry(settings.prompt_dir)
        prompt_map = self.prompts.load_many(["requirements", "controller", "scoring", "reflection", "finalize"])
        self.requirement_extractor = RequirementExtractor(settings, prompt_map["requirements"])
        self.controller = ReActController(settings, prompt_map["controller"])
        self.resume_scorer = ResumeScorer(settings, prompt_map["scoring"])
        self.reflection_critic = ReflectionCritic(settings, prompt_map["reflection"])
        self.finalizer = Finalizer(settings, prompt_map["finalize"])
        self.cts_client: CTSClientProtocol = MockCTSClient(settings) if settings.mock_cts else CTSClient(settings)

    def run(self, *, jd: str, notes: str) -> RunArtifacts:
        tracer = RunTracer(self.settings.runs_path)
        try:
            self._write_run_preamble(tracer=tracer, jd=jd, notes=notes)
            self._require_live_llm_config()
            run_state = self._build_run_state(jd=jd, notes=notes, tracer=tracer)
            top_scored, stop_reason, rounds_executed = self._run_rounds(run_state=run_state, tracer=tracer)
            finalize_context = build_finalize_context(
                run_state=run_state,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                run_id=tracer.run_id,
                run_dir=str(tracer.run_dir),
            )
            tracer.write_json("finalizer_context.json", finalize_context.model_dump(mode="json"))
            finalizer_call_id = "finalizer"
            finalizer_payload = {
                "FINALIZATION_CONTEXT": {
                    "run_id": tracer.run_id,
                    "run_dir": str(tracer.run_dir),
                    "rounds_executed": rounds_executed,
                    "stop_reason": stop_reason,
                    "ranked_candidates": [item.model_dump(mode="json") for item in top_scored],
                }
            }
            finalizer_artifacts = [
                "finalizer_context.json",
                "finalizer_call.json",
                "final_candidates.json",
                "final_answer.md",
            ]
            finalizer_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            finalizer_started_clock = perf_counter()
            self._emit_llm_event(
                tracer=tracer,
                event_type="finalizer_started",
                call_id=finalizer_call_id,
                model_id=self.settings.finalize_model,
                status="started",
                summary="Generating final shortlist output.",
                artifact_paths=finalizer_artifacts,
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
                latency_ms = max(1, int((perf_counter() - finalizer_started_clock) * 1000))
                tracer.write_json(
                    "finalizer_call.json",
                    self._build_llm_call_snapshot(
                        stage="finalize",
                        call_id=finalizer_call_id,
                        model_id=self.settings.finalize_model,
                        prompt_name="finalize",
                        user_payload=finalizer_payload,
                        started_at=finalizer_started_at,
                        latency_ms=latency_ms,
                        status="failed",
                        error_message=str(exc),
                        validator_retry_count=self.finalizer.last_validator_retry_count,
                    ).model_dump(mode="json"),
                )
                self._emit_llm_event(
                    tracer=tracer,
                    event_type="finalizer_failed",
                    call_id=finalizer_call_id,
                    model_id=self.settings.finalize_model,
                    status="failed",
                    summary=str(exc),
                    artifact_paths=["finalizer_call.json", "finalizer_context.json"],
                    latency_ms=latency_ms,
                    error_message=str(exc),
                )
                raise RunStageError("finalization", str(exc)) from exc
            latency_ms = max(1, int((perf_counter() - finalizer_started_clock) * 1000))
            tracer.write_json(
                "finalizer_call.json",
                self._build_llm_call_snapshot(
                    stage="finalize",
                    call_id=finalizer_call_id,
                    model_id=self.settings.finalize_model,
                    prompt_name="finalize",
                    user_payload=finalizer_payload,
                    started_at=finalizer_started_at,
                    latency_ms=latency_ms,
                    status="succeeded",
                    structured_output=final_result.model_dump(mode="json"),
                    validator_retry_count=self.finalizer.last_validator_retry_count,
                ).model_dump(mode="json"),
            )
            final_markdown = self._render_final_markdown(final_result)
            tracer.write_json("final_candidates.json", final_result.model_dump(mode="json"))
            tracer.write_text("final_answer.md", final_markdown)
            tracer.write_json(
                "judge_packet.json",
                self._build_judge_packet(
                    tracer=tracer,
                    run_state=run_state,
                    final_result=final_result,
                    rounds_executed=rounds_executed,
                    stop_reason=stop_reason,
                ),
            )
            tracer.write_text(
                "run_summary.md",
                self._render_run_summary(run_state=run_state, final_result=final_result),
            )
            self._emit_llm_event(
                tracer=tracer,
                event_type="finalizer_completed",
                call_id=finalizer_call_id,
                model_id=self.settings.finalize_model,
                status="succeeded",
                summary=final_result.summary,
                artifact_paths=finalizer_artifacts + ["judge_packet.json", "run_summary.md"],
                latency_ms=latency_ms,
            )
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
                candidate_store=run_state.candidate_store,
                normalized_store=run_state.normalized_store,
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

    def _build_run_state(self, *, jd: str, notes: str, tracer: RunTracer) -> RunState:
        input_truth = build_input_truth(jd=jd, notes=notes)
        call_id = "requirements"
        call_payload = {"INPUT_TRUTH": input_truth.model_dump(mode="json")}
        artifact_paths = [
            "requirement_extraction_draft.json",
            "requirements_call.json",
            "requirement_sheet.json",
        ]
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        started_clock = perf_counter()
        self._emit_llm_event(
            tracer=tracer,
            event_type="requirements_started",
            call_id=call_id,
            model_id=self.settings.requirements_model,
            status="started",
            summary="Extracting requirement truth from JD and notes.",
            artifact_paths=artifact_paths,
        )
        try:
            requirement_draft, requirement_sheet = self.requirement_extractor.extract_with_draft(input_truth=input_truth)
        except Exception as exc:  # noqa: BLE001
            latency_ms = max(1, int((perf_counter() - started_clock) * 1000))
            tracer.write_json(
                "requirements_call.json",
                self._build_llm_call_snapshot(
                    stage="requirements",
                    call_id=call_id,
                    model_id=self.settings.requirements_model,
                    prompt_name="requirements",
                    user_payload=call_payload,
                    started_at=started_at,
                    latency_ms=latency_ms,
                    status="failed",
                    error_message=str(exc),
                ).model_dump(mode="json"),
            )
            self._emit_llm_event(
                tracer=tracer,
                event_type="requirements_failed",
                call_id=call_id,
                model_id=self.settings.requirements_model,
                status="failed",
                summary=str(exc),
                artifact_paths=["requirements_call.json"],
                latency_ms=latency_ms,
                error_message=str(exc),
            )
            raise RunStageError("requirement_extraction", str(exc)) from exc
        latency_ms = max(1, int((perf_counter() - started_clock) * 1000))
        tracer.write_json("requirement_extraction_draft.json", requirement_draft.model_dump(mode="json"))
        tracer.write_json(
            "requirements_call.json",
            self._build_llm_call_snapshot(
                stage="requirements",
                call_id=call_id,
                model_id=self.settings.requirements_model,
                prompt_name="requirements",
                user_payload=call_payload,
                started_at=started_at,
                latency_ms=latency_ms,
                status="succeeded",
                structured_output=requirement_draft.model_dump(mode="json"),
            ).model_dump(mode="json"),
        )
        scoring_policy = build_scoring_policy(requirement_sheet)
        run_state = RunState(
            input_truth=input_truth,
            requirement_sheet=requirement_sheet,
            scoring_policy=scoring_policy,
            retrieval_state={
                "current_plan_version": 0,
                "query_term_pool": requirement_sheet.initial_query_term_pool,
            },
        )
        tracer.write_json("input_truth.json", input_truth.model_dump(mode="json"))
        tracer.write_json("requirement_sheet.json", requirement_sheet.model_dump(mode="json"))
        tracer.write_json("scoring_policy.json", scoring_policy.model_dump(mode="json"))
        self._emit_llm_event(
            tracer=tracer,
            event_type="requirements_completed",
            call_id=call_id,
            model_id=self.settings.requirements_model,
            status="succeeded",
            summary=requirement_sheet.role_title,
            artifact_paths=artifact_paths,
            latency_ms=latency_ms,
        )
        return run_state

    def _write_run_preamble(self, *, tracer: RunTracer, jd: str, notes: str) -> None:
        tracer.write_json("run_config.json", self._build_public_run_config())
        self._write_prompt_snapshots(tracer)
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
            summary="Starting v0.2 single-controller runtime.",
            payload={
                "mock_cts": self.settings.mock_cts,
                "configured_models": {
                    "requirements": self.settings.requirements_model,
                    "controller": self.settings.controller_model,
                    "scoring": self.settings.scoring_model,
                    "reflection": self.settings.reflection_model,
                    "finalize": self.settings.finalize_model,
                },
                "configured_providers": self._configured_providers(),
            },
        )

    def _run_rounds(
        self,
        *,
        run_state: RunState,
        tracer: RunTracer,
    ) -> tuple[list[ScoredCandidate], str, int]:
        seen_dedup_keys: set[str] = set()
        stop_reason = "max_rounds_reached"
        rounds_executed = 0

        for round_no in range(1, self.settings.max_rounds + 1):
            target_new = 10 if round_no == 1 else 5
            controller_context = build_controller_context(
                run_state=run_state,
                round_no=round_no,
                min_rounds=self.settings.min_rounds,
                max_rounds=self.settings.max_rounds,
                target_new=target_new,
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/controller_context.json",
                controller_context.model_dump(mode="json"),
            )
            controller_call_id = f"controller-r{round_no:02d}"
            controller_call_payload = {"CONTROLLER_CONTEXT": controller_context.model_dump(mode="json")}
            controller_artifacts = [
                f"rounds/round_{round_no:02d}/controller_context.json",
                f"rounds/round_{round_no:02d}/controller_call.json",
                f"rounds/round_{round_no:02d}/controller_decision.json",
            ]
            controller_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            controller_started_clock = perf_counter()
            self._emit_llm_event(
                tracer=tracer,
                event_type="controller_started",
                round_no=round_no,
                call_id=controller_call_id,
                model_id=self.settings.controller_model,
                status="started",
                summary=f"Planning round {round_no} action.",
                artifact_paths=controller_artifacts,
            )
            try:
                controller_decision = self.controller.decide(context=controller_context)
            except Exception as exc:  # noqa: BLE001
                latency_ms = max(1, int((perf_counter() - controller_started_clock) * 1000))
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/controller_call.json",
                    self._build_llm_call_snapshot(
                        stage="controller",
                        call_id=controller_call_id,
                        model_id=self.settings.controller_model,
                        prompt_name="controller",
                        user_payload=controller_call_payload,
                        started_at=controller_started_at,
                        latency_ms=latency_ms,
                        status="failed",
                        error_message=str(exc),
                        round_no=round_no,
                        validator_retry_count=self.controller.last_validator_retry_count,
                    ).model_dump(mode="json"),
                )
                self._emit_llm_event(
                    tracer=tracer,
                    event_type="controller_failed",
                    round_no=round_no,
                    call_id=controller_call_id,
                    model_id=self.settings.controller_model,
                    status="failed",
                    summary=str(exc),
                    artifact_paths=controller_artifacts[:2],
                    latency_ms=latency_ms,
                    error_message=str(exc),
                )
                raise RunStageError("controller", str(exc)) from exc
            controller_decision = self._sanitize_controller_decision(
                decision=controller_decision,
                run_state=run_state,
                round_no=round_no,
            )
            if controller_decision.action == "stop" and round_no < self.settings.min_rounds:
                controller_decision = self._force_continue_decision(run_state=run_state, round_no=round_no)
            tracer.write_json(
                f"rounds/round_{round_no:02d}/controller_decision.json",
                controller_decision.model_dump(mode="json"),
            )
            latency_ms = max(1, int((perf_counter() - controller_started_clock) * 1000))
            tracer.write_json(
                f"rounds/round_{round_no:02d}/controller_call.json",
                self._build_llm_call_snapshot(
                    stage="controller",
                    call_id=controller_call_id,
                    model_id=self.settings.controller_model,
                    prompt_name="controller",
                    user_payload=controller_call_payload,
                    started_at=controller_started_at,
                    latency_ms=latency_ms,
                    status="succeeded",
                    structured_output=controller_decision.model_dump(mode="json"),
                    round_no=round_no,
                    validator_retry_count=self.controller.last_validator_retry_count,
                ).model_dump(mode="json"),
            )
            self._emit_llm_event(
                tracer=tracer,
                event_type="controller_completed",
                round_no=round_no,
                call_id=controller_call_id,
                model_id=self.settings.controller_model,
                status="succeeded",
                summary=controller_decision.decision_rationale,
                artifact_paths=controller_artifacts,
                latency_ms=latency_ms,
            )
            if controller_decision.action == "stop":
                stop_reason = self._normalize_stop_reason(
                    proposed=controller_decision.stop_reason,
                )
                break

            projection_result = project_constraints_to_cts(
                requirement_sheet=run_state.requirement_sheet,
                filter_plan=controller_decision.proposed_filter_plan,
            )
            location_execution_plan = build_location_execution_plan(
                allowed_locations=run_state.requirement_sheet.hard_constraints.locations,
                preferred_locations=run_state.requirement_sheet.preferences.preferred_locations,
                round_no=round_no,
                target_new=target_new,
            )
            retrieval_plan = build_round_retrieval_plan(
                plan_version=run_state.retrieval_state.current_plan_version + 1,
                round_no=round_no,
                query_terms=controller_decision.proposed_query_terms or [],
                projected_cts_filters=projection_result.cts_native_filters,
                runtime_only_constraints=projection_result.runtime_only_constraints,
                location_execution_plan=location_execution_plan,
                target_new=target_new,
                rationale=controller_decision.decision_rationale,
            )
            run_state.retrieval_state.current_plan_version = retrieval_plan.plan_version
            run_state.retrieval_state.last_projection_result = projection_result
            tracer.write_json(
                f"rounds/round_{round_no:02d}/retrieval_plan.json",
                retrieval_plan.model_dump(mode="json"),
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/constraint_projection_result.json",
                projection_result.model_dump(mode="json"),
            )

            try:
                (
                    cts_queries,
                    sent_query_records,
                    new_candidates,
                    search_observation,
                    search_attempts,
                ) = self._execute_location_search_plan(
                    round_no=round_no,
                    retrieval_plan=retrieval_plan,
                    base_adapter_notes=projection_result.adapter_notes,
                    target_new=target_new,
                    seen_resume_ids=set(run_state.seen_resume_ids),
                    seen_dedup_keys=seen_dedup_keys,
                    tracer=tracer,
                )
            except Exception as exc:  # noqa: BLE001
                raise RunStageError("search_cts", str(exc)) from exc
            run_state.retrieval_state.sent_query_history.extend(sent_query_records)
            tracer.write_json(
                "sent_query_history.json",
                [item.model_dump(mode="json") for item in run_state.retrieval_state.sent_query_history],
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/sent_query_records.json",
                [item.model_dump(mode="json") for item in sent_query_records],
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/cts_queries.json",
                [item.model_dump(mode="json") for item in cts_queries],
            )

            for candidate in new_candidates:
                run_state.candidate_store[candidate.resume_id] = candidate
            run_state.seen_resume_ids.extend(
                item.resume_id for item in new_candidates if item.resume_id not in run_state.seen_resume_ids
            )
            seen_dedup_keys.update(item.dedup_key for item in new_candidates)

            current_top_candidates, pool_decisions, dropped_candidates = self._score_round(
                round_no=round_no,
                new_candidates=new_candidates,
                run_state=run_state,
                tracer=tracer,
            )
            round_state = RoundState(
                round_no=round_no,
                controller_decision=controller_decision,
                retrieval_plan=retrieval_plan,
                constraint_projection_result=projection_result,
                cts_queries=cts_queries,
                search_observation=search_observation,
                search_attempts=search_attempts,
                top_candidates=current_top_candidates,
                dropped_candidates=dropped_candidates,
                top_pool_ids=run_state.top_pool_ids,
                dropped_candidate_ids=[candidate.resume_id for candidate in dropped_candidates],
            )
            run_state.round_history.append(round_state)
            reflection_context = build_reflection_context(run_state=run_state, round_state=round_state)
            tracer.write_json(
                f"rounds/round_{round_no:02d}/reflection_context.json",
                reflection_context.model_dump(mode="json"),
            )
            reflection_call_id = f"reflection-r{round_no:02d}"
            reflection_call_payload = {"REFLECTION_CONTEXT": reflection_context.model_dump(mode="json")}
            reflection_artifacts = [
                f"rounds/round_{round_no:02d}/reflection_context.json",
                f"rounds/round_{round_no:02d}/reflection_call.json",
                f"rounds/round_{round_no:02d}/reflection_advice.json",
            ]
            reflection_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            reflection_started_clock = perf_counter()
            self._emit_llm_event(
                tracer=tracer,
                event_type="reflection_started",
                round_no=round_no,
                call_id=reflection_call_id,
                model_id=self.settings.reflection_model,
                status="started",
                summary="Starting round reflection.",
                artifact_paths=reflection_artifacts,
            )
            try:
                reflection_advice = self._reflect_round(context=reflection_context, run_state=run_state)
            except Exception as exc:  # noqa: BLE001
                latency_ms = max(1, int((perf_counter() - reflection_started_clock) * 1000))
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/reflection_call.json",
                    self._build_llm_call_snapshot(
                        stage="reflection",
                        call_id=reflection_call_id,
                        model_id=self.settings.reflection_model,
                        prompt_name="reflection",
                        user_payload=reflection_call_payload,
                        started_at=reflection_started_at,
                        latency_ms=latency_ms,
                        status="failed",
                        error_message=str(exc),
                        round_no=round_no,
                    ).model_dump(mode="json"),
                )
                self._emit_llm_event(
                    tracer=tracer,
                    event_type="reflection_failed",
                    round_no=round_no,
                    call_id=reflection_call_id,
                    model_id=self.settings.reflection_model,
                    status="failed",
                    summary=str(exc),
                    artifact_paths=reflection_artifacts[:2],
                    latency_ms=latency_ms,
                    error_message=str(exc),
                )
                raise
            round_state.reflection_advice = reflection_advice
            tracer.write_json(
                f"rounds/round_{round_no:02d}/reflection_advice.json",
                reflection_advice.model_dump(mode="json"),
            )
            latency_ms = max(1, int((perf_counter() - reflection_started_clock) * 1000))
            tracer.write_json(
                f"rounds/round_{round_no:02d}/reflection_call.json",
                self._build_llm_call_snapshot(
                    stage="reflection",
                    call_id=reflection_call_id,
                    model_id=self.settings.reflection_model,
                    prompt_name="reflection",
                    user_payload=reflection_call_payload,
                    started_at=reflection_started_at,
                    latency_ms=latency_ms,
                    status="succeeded",
                    structured_output=reflection_advice.model_dump(mode="json"),
                    round_no=round_no,
                ).model_dump(mode="json"),
            )
            self._emit_llm_event(
                tracer=tracer,
                event_type="reflection_completed",
                round_no=round_no,
                call_id=reflection_call_id,
                model_id=self.settings.reflection_model,
                status="succeeded",
                summary=reflection_advice.reflection_summary,
                artifact_paths=reflection_artifacts,
                latency_ms=latency_ms,
            )
            tracer.write_text(
                f"rounds/round_{round_no:02d}/round_review.md",
                self._render_round_review(
                    round_no=round_no,
                    controller_decision=controller_decision,
                    retrieval_plan=retrieval_plan,
                    observation=search_observation,
                    pool_decisions=pool_decisions,
                    top_candidates=current_top_candidates,
                    dropped_candidates=dropped_candidates,
                    reflection=reflection_advice,
                    stop_reason=None,
                ),
            )

            rounds_executed = round_no

        return top_candidates(run_state), stop_reason, rounds_executed

    def _sanitize_controller_decision(
        self,
        *,
        decision: ControllerDecision,
        run_state: RunState,
        round_no: int,
    ) -> ControllerDecision:
        previous_reflection = run_state.round_history[-1].reflection_advice if run_state.round_history else None
        if previous_reflection is not None and not (decision.response_to_reflection or "").strip():
            raise ValueError("response_to_reflection is required after a reflection round")
        if decision.action == "stop":
            return decision
        query_terms = canonicalize_controller_query_terms(decision.proposed_query_terms, round_no)
        filter_plan = canonicalize_filter_plan(
            requirement_sheet=run_state.requirement_sheet,
            filter_plan=decision.proposed_filter_plan,
        )
        return decision.model_copy(
            update={
                "proposed_query_terms": query_terms,
                "proposed_filter_plan": filter_plan,
                "stop_reason": None,
            }
        )

    def _force_continue_decision(self, *, run_state: RunState, round_no: int) -> ControllerDecision:
        return SearchControllerDecision(
            thought_summary="Runtime override: continue until min_rounds is satisfied.",
            action="search_cts",
            decision_rationale="Early stop is not allowed before min_rounds.",
            proposed_query_terms=select_query_terms(run_state.retrieval_state.query_term_pool, round_no),
            proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
            response_to_reflection="Runtime override before min_rounds.",
        )

    def _reflect_round(
        self,
        *,
        context,
        run_state: RunState,
    ) -> ReflectionAdvice:
        if not self.settings.enable_reflection:
            advice = ReflectionAdvice(
                strategy_assessment="Reflection disabled.",
                quality_assessment="Reflection disabled.",
                coverage_assessment="Reflection disabled.",
                reflection_summary="Reflection disabled.",
            )
            return advice
        try:
            advice = self.reflection_critic.reflect(context=context)
        except Exception as exc:  # noqa: BLE001
            raise RunStageError("reflection", str(exc)) from exc
        run_state.retrieval_state.reflection_keyword_advice_history.append(advice.keyword_advice)
        run_state.retrieval_state.reflection_filter_advice_history.append(advice.filter_advice)
        run_state.retrieval_state.query_term_pool = self._update_query_term_pool(
            run_state.retrieval_state.query_term_pool,
            advice,
            context.round_no,
        )
        return advice

    def _update_query_term_pool(self, pool, advice: ReflectionAdvice, round_no: int):
        drop_terms = {item.casefold() for item in advice.keyword_advice.suggested_drop_terms}
        deprioritize_terms = {item.casefold() for item in advice.keyword_advice.suggested_deprioritize_terms}
        updated: list[QueryTermCandidate] = []
        existing = set()
        next_priority = 1
        for item in pool:
            candidate = item
            key = candidate.term.casefold()
            existing.add(key)
            if key in drop_terms:
                candidate = candidate.model_copy(update={"active": False})
            elif key in deprioritize_terms:
                candidate = candidate.model_copy(update={"priority": candidate.priority + 100})
            updated.append(candidate)
            next_priority = max(next_priority, candidate.priority + 1)
        for term in advice.keyword_advice.suggested_add_terms:
            clean = " ".join(term.split()).strip()
            if not clean or clean.casefold() in existing:
                continue
            updated.append(
                QueryTermCandidate(
                    term=clean,
                    source="reflection",
                    category="expansion",
                    priority=next_priority,
                    evidence=advice.keyword_advice.critique or "Added from reflection advice.",
                    first_added_round=round_no,
                    active=True,
                )
            )
            next_priority += 1
        return updated

    def _score_round(
        self,
        *,
        round_no: int,
        new_candidates: list[ResumeCandidate],
        run_state: RunState,
        tracer: RunTracer,
    ) -> tuple[list[ScoredCandidate], list[PoolDecision], list[ScoredCandidate]]:
        current_top_candidates = top_candidates(run_state)
        scoring_pool = self._build_scoring_pool(
            round_no=round_no,
            top_scored=current_top_candidates,
            new_candidates=new_candidates,
            candidate_store=run_state.candidate_store,
        )
        normalized_scoring_pool = self._normalize_scoring_pool(
            round_no=round_no,
            scoring_pool=scoring_pool,
            tracer=tracer,
            normalized_store=run_state.normalized_store,
        )
        tracer.write_jsonl(
            f"rounds/round_{round_no:02d}/normalized_resumes.jsonl",
            [item.model_dump(mode="json") for item in normalized_scoring_pool],
        )
        scoring_contexts = [
            build_scoring_context(
                run_state=run_state,
                round_no=round_no,
                normalized_resume=item,
            )
            for item in normalized_scoring_pool
        ]
        scored_candidates, scoring_failures = self.resume_scorer.score_candidates_parallel(
            contexts=scoring_contexts,
            tracer=tracer,
        )
        if scoring_failures:
            raise RunStageError("scoring", self._format_scoring_failure_message(scoring_failures))
        for candidate in scored_candidates:
            run_state.scorecards_by_resume_id[candidate.resume_id] = candidate
        ranked_candidates = sorted(scored_candidates, key=scored_candidate_sort_key)
        previous_top_ids = set(run_state.top_pool_ids)
        current_top_candidates = ranked_candidates[:5]
        run_state.top_pool_ids = [item.resume_id for item in current_top_candidates]
        pool_decisions = self._build_pool_decisions(
            round_no=round_no,
            ranked_candidates=ranked_candidates,
            top_candidates=current_top_candidates,
            previous_top_ids=previous_top_ids,
        )
        tracer.write_jsonl(
            f"rounds/round_{round_no:02d}/scorecards.jsonl",
            [item.model_dump(mode="json") for item in ranked_candidates],
        )
        dropped_candidates = [
            candidate
            for candidate in ranked_candidates
            if candidate.resume_id not in {item.resume_id for item in current_top_candidates}
        ]
        return current_top_candidates, pool_decisions, dropped_candidates

    def _build_public_run_config(self) -> dict[str, object]:
        return {
            "settings": {
                "cts_base_url": self.settings.cts_base_url,
                "cts_timeout_seconds": self.settings.cts_timeout_seconds,
                "cts_spec_path": self.settings.cts_spec_path,
                "cts_credentials_configured": bool(self.settings.cts_tenant_key and self.settings.cts_tenant_secret),
                "requirements_model": self.settings.requirements_model,
                "controller_model": self.settings.controller_model,
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
            "configured_providers": self._configured_providers(),
            "selected_openapi_file": str(self.settings.spec_file),
            "prompt_hashes": self.prompts.prompt_hashes(),
            "prompt_files": self.prompts.prompt_files(),
        }

    def _prompt_snapshot_path(self, prompt_name: str) -> str:
        return f"prompt_snapshots/{prompt_name}.md"

    def _write_prompt_snapshots(self, tracer: RunTracer) -> None:
        for prompt in self.prompts.loaded_prompts().values():
            tracer.write_text(self._prompt_snapshot_path(prompt.name), prompt.content)

    def _build_llm_call_snapshot(
        self,
        *,
        stage: str,
        call_id: str,
        model_id: str,
        prompt_name: str,
        user_payload: dict[str, object],
        started_at: str,
        latency_ms: int | None,
        status: str,
        structured_output: dict[str, object] | None = None,
        error_message: str | None = None,
        round_no: int | None = None,
        resume_id: str | None = None,
        branch_id: str | None = None,
        validator_retry_count: int = 0,
    ) -> LLMCallSnapshot:
        prompt = self.prompts.load(prompt_name)
        return LLMCallSnapshot(
            stage=stage,
            call_id=call_id,
            round_no=round_no,
            resume_id=resume_id,
            branch_id=branch_id,
            model_id=model_id,
            provider=model_provider(model_id),
            prompt_hash=prompt.sha256,
            prompt_snapshot_path=self._prompt_snapshot_path(prompt_name),
            started_at=started_at,
            latency_ms=latency_ms,
            status=status,
            user_payload=user_payload,
            structured_output=structured_output,
            error_message=error_message,
            validator_retry_count=validator_retry_count,
        )

    def _emit_llm_event(
        self,
        *,
        tracer: RunTracer,
        event_type: str,
        call_id: str,
        model_id: str,
        status: str,
        summary: str,
        artifact_paths: list[str],
        round_no: int | None = None,
        resume_id: str | None = None,
        branch_id: str | None = None,
        latency_ms: int | None = None,
        error_message: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        tracer.emit(
            event_type,
            round_no=round_no,
            resume_id=resume_id,
            branch_id=branch_id,
            model=model_id,
            call_id=call_id,
            status=status,
            latency_ms=latency_ms,
            summary=summary,
            error_message=error_message,
            artifact_paths=artifact_paths,
            payload=payload,
        )

    def _build_judge_packet(
        self,
        *,
        tracer: RunTracer,
        run_state: RunState,
        final_result: FinalResult,
        rounds_executed: int,
        stop_reason: str,
    ) -> dict[str, object]:
        return {
            "schema_version": "v0.2",
            "run": {
                "run_id": tracer.run_id,
                "rounds_executed": rounds_executed,
                "stop_reason": stop_reason,
                "models": {
                    "requirements": self.settings.requirements_model,
                    "controller": self.settings.controller_model,
                    "scoring": self.settings.scoring_model,
                    "reflection": self.settings.reflection_model,
                    "finalize": self.settings.finalize_model,
                },
                "prompt_hashes": self.prompts.prompt_hashes(),
            },
            "requirements": {
                "input_truth": run_state.input_truth.model_dump(mode="json"),
                "requirement_sheet": run_state.requirement_sheet.model_dump(mode="json"),
                "scoring_policy": run_state.scoring_policy.model_dump(mode="json"),
            },
            "rounds": [
                {
                    "round_no": round_state.round_no,
                    "controller_decision": round_state.controller_decision.model_dump(mode="json"),
                    "retrieval_plan": round_state.retrieval_plan.model_dump(mode="json"),
                    "constraint_projection_result": (
                        round_state.constraint_projection_result.model_dump(mode="json")
                        if round_state.constraint_projection_result is not None
                        else None
                    ),
                    "sent_query_records": [
                        item.model_dump(mode="json")
                        for item in run_state.retrieval_state.sent_query_history
                        if item.round_no == round_state.round_no
                    ],
                    "search_observation": (
                        round_state.search_observation.model_dump(mode="json")
                        if round_state.search_observation is not None
                        else None
                    ),
                    "top_candidates": [item.model_dump(mode="json") for item in round_state.top_candidates],
                    "dropped_candidates": [item.model_dump(mode="json") for item in round_state.dropped_candidates],
                    "reflection_advice": (
                        round_state.reflection_advice.model_dump(mode="json")
                        if round_state.reflection_advice is not None
                        else None
                    ),
                }
                for round_state in run_state.round_history
            ],
            "final": {"final_result": final_result.model_dump(mode="json")},
        }

    def _render_run_summary(
        self,
        *,
        run_state: RunState,
        final_result: FinalResult,
    ) -> str:
        lines = [
            "# Run Summary",
            "",
            f"- Run ID: `{final_result.run_id}`",
            f"- Rounds executed: `{final_result.rounds_executed}`",
            f"- Stop reason: `{final_result.stop_reason}`",
            f"- Models: requirements=`{self.settings.requirements_model}`, controller=`{self.settings.controller_model}`, scoring=`{self.settings.scoring_model}`, reflection=`{self.settings.reflection_model}`, finalize=`{self.settings.finalize_model}`",
            f"- Judge packet: `judge_packet.json`",
            f"- Final candidates: `final_candidates.json`",
            "",
            "## Prompt Hashes",
            "",
        ]
        for name, digest in sorted(self.prompts.prompt_hashes().items()):
            lines.append(f"- `{name}`: `{digest}`")
        lines.extend(["", "## Round Index", ""])
        for round_state in run_state.round_history:
            observation = round_state.search_observation
            reflection = round_state.reflection_advice
            lines.append(
                "- "
                f"Round {round_state.round_no}: "
                f"queries=`{len(round_state.cts_queries)}`, "
                f"new=`{observation.unique_new_count if observation else 0}`, "
                f"shortage=`{observation.shortage_count if observation else 0}`, "
                f"top=`{', '.join(item.resume_id for item in round_state.top_candidates) or 'None'}`, "
                f"reflection=`{reflection.reflection_summary if reflection else 'none'}`"
            )
        lines.extend(["", "## Final Shortlist", ""])
        for candidate in final_result.candidates:
            lines.append(
                f"- Rank {candidate.rank}: `{candidate.resume_id}` score=`{candidate.final_score}` source_round=`{candidate.source_round}`"
            )
        return "\n".join(lines).strip() + "\n"

    def _require_live_llm_config(self) -> None:
        try:
            preflight_models(self.settings)
        except Exception as exc:  # noqa: BLE001
            raise RunStageError("llm_preflight", str(exc)) from exc

    def _configured_providers(self) -> list[str]:
        providers: list[str] = []
        seen: set[str] = set()
        for model_id in (
            self.settings.requirements_model,
            self.settings.controller_model,
            self.settings.scoring_model,
            self.settings.reflection_model,
            self.settings.finalize_model,
        ):
            provider = model_provider(model_id)
            if provider in seen:
                continue
            providers.append(provider)
            seen.add(provider)
        return providers

    def _format_scoring_failure_message(self, failures: list[object]) -> str:
        resume_ids = [getattr(item, "resume_id", "unknown") for item in failures]
        return f"Scoring failed for {len(failures)} resume(s): {', '.join(resume_ids)}."

    def _preview_text(self, text: str, *, limit: int) -> str:
        collapsed = re.sub(r"\s+", " ", text).strip()
        if len(collapsed) <= limit:
            return collapsed
        return f"{collapsed[:limit].rstrip()}..."

    def _normalize_stop_reason(
        self,
        *,
        proposed: str | None,
    ) -> str:
        if proposed in CANONICAL_STOP_REASONS:
            return proposed
        return "controller_stop"

    def _latest_search_observation(self, run_state: RunState) -> SearchObservation | None:
        if not run_state.round_history:
            return None
        return run_state.round_history[-1].search_observation

    def _execute_location_search_plan(
        self,
        *,
        round_no: int,
        retrieval_plan,
        base_adapter_notes: list[str],
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
    ) -> tuple[list[CTSQuery], list[SentQueryRecord], list[ResumeCandidate], SearchObservation, list[SearchAttempt]]:
        location_plan = retrieval_plan.location_execution_plan
        if location_plan.mode == "none":
            query = CTSQuery(
                query_terms=retrieval_plan.query_terms,
                keyword_query=retrieval_plan.keyword_query,
                native_filters=dict(retrieval_plan.projected_cts_filters),
                page=1,
                page_size=target_new,
                rationale=retrieval_plan.rationale,
                adapter_notes=list(base_adapter_notes),
            )
            sent_query_record = SentQueryRecord(
                round_no=round_no,
                batch_no=1,
                requested_count=target_new,
                query_terms=retrieval_plan.query_terms,
                keyword_query=retrieval_plan.keyword_query,
                source_plan_version=retrieval_plan.plan_version,
                rationale=retrieval_plan.rationale,
            )
            new_candidates, search_observation, search_attempts, _ = self._execute_search_tool(
                round_no=round_no,
                query=query,
                target_new=target_new,
                seen_resume_ids=seen_resume_ids,
                seen_dedup_keys=seen_dedup_keys,
                tracer=tracer,
                write_round_artifacts=False,
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/search_observation.json",
                search_observation.model_dump(mode="json"),
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/search_attempts.json",
                [item.model_dump(mode="json") for item in search_attempts],
            )
            return [query], [sent_query_record], new_candidates, search_observation, search_attempts

        city_states = {
            city: _CityExecutionState()
            for city in location_plan.allowed_locations
        }
        global_seen_resume_ids = set(seen_resume_ids)
        global_seen_dedup_keys = set(seen_dedup_keys)
        cts_queries: list[CTSQuery] = []
        sent_query_records: list[SentQueryRecord] = []
        all_new_candidates: list[ResumeCandidate] = []
        all_search_attempts: list[SearchAttempt] = []
        city_search_summaries: list[CitySearchSummary] = []
        adapter_notes = list(base_adapter_notes)
        raw_candidate_count = 0
        batch_no = 0

        def run_dispatches(
            *,
            phase: LocationExecutionPhase,
            city_targets: list[tuple[str, int]],
        ) -> None:
            nonlocal batch_no, raw_candidate_count
            if not city_targets:
                return
            batch_no += 1
            for city, requested_count in city_targets:
                dispatch = self._run_city_dispatch(
                    round_no=round_no,
                    retrieval_plan=retrieval_plan,
                    city=city,
                    phase=phase,
                    batch_no=batch_no,
                    requested_count=requested_count,
                    city_state=city_states[city],
                    base_adapter_notes=adapter_notes,
                    seen_resume_ids=global_seen_resume_ids,
                    seen_dedup_keys=global_seen_dedup_keys,
                    tracer=tracer,
                )
                cts_queries.append(dispatch["cts_query"])
                sent_query_records.append(dispatch["sent_query_record"])
                all_new_candidates.extend(dispatch["new_candidates"])
                all_search_attempts.extend(dispatch["search_attempts"])
                city_search_summaries.append(dispatch["city_summary"])
                raw_candidate_count += dispatch["search_observation"].raw_candidate_count
                adapter_notes[:] = unique_strings(adapter_notes + dispatch["search_observation"].adapter_notes)
                for candidate in dispatch["new_candidates"]:
                    global_seen_resume_ids.add(candidate.resume_id)
                    global_seen_dedup_keys.add(candidate.dedup_key)

        if location_plan.mode == "single":
            run_dispatches(
                phase="balanced",
                city_targets=[(location_plan.allowed_locations[0], target_new)],
            )
        else:
            if location_plan.mode == "priority_then_fallback":
                for city in location_plan.priority_order:
                    remaining_gap = target_new - len(all_new_candidates)
                    if remaining_gap <= 0:
                        break
                    run_dispatches(
                        phase="priority",
                        city_targets=[(city, remaining_gap)],
                    )
            while True:
                remaining_gap = target_new - len(all_new_candidates)
                if remaining_gap <= 0:
                    break
                active_cities = [
                    city
                    for city in location_plan.balanced_order
                    if city in city_states and not city_states[city].exhausted
                ]
                if not active_cities:
                    break
                city_targets = allocate_balanced_city_targets(
                    ordered_cities=active_cities,
                    target_new=remaining_gap,
                )
                if not city_targets:
                    break
                run_dispatches(
                    phase="balanced",
                    city_targets=city_targets,
                )

        search_observation = SearchObservation(
            round_no=round_no,
            requested_count=target_new,
            raw_candidate_count=raw_candidate_count,
            unique_new_count=len(all_new_candidates),
            shortage_count=max(0, target_new - len(all_new_candidates)),
            fetch_attempt_count=len(all_search_attempts),
            exhausted_reason=self._final_exhausted_reason(
                target_new=target_new,
                new_candidate_count=len(all_new_candidates),
                city_search_summaries=city_search_summaries,
            ),
            new_resume_ids=[candidate.resume_id for candidate in all_new_candidates],
            new_candidate_summaries=[candidate.compact_summary() for candidate in all_new_candidates],
            adapter_notes=adapter_notes,
            city_search_summaries=city_search_summaries,
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/search_observation.json",
            search_observation.model_dump(mode="json"),
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/search_attempts.json",
            [item.model_dump(mode="json") for item in all_search_attempts],
        )
        return cts_queries, sent_query_records, all_new_candidates, search_observation, all_search_attempts

    def _run_city_dispatch(
        self,
        *,
        round_no: int,
        retrieval_plan,
        city: str,
        phase: LocationExecutionPhase,
        batch_no: int,
        requested_count: int,
        city_state: _CityExecutionState,
        base_adapter_notes: list[str],
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
    ) -> dict[str, object]:
        cts_query = CTSQuery(
            query_terms=retrieval_plan.query_terms,
            keyword_query=retrieval_plan.keyword_query,
            native_filters={**retrieval_plan.projected_cts_filters, "location": [city]},
            page=city_state.next_page,
            page_size=requested_count,
            rationale=retrieval_plan.rationale,
            adapter_notes=unique_strings([*base_adapter_notes, f"runtime location dispatch: {city}"]),
        )
        sent_query_record = SentQueryRecord(
            round_no=round_no,
            city=city,
            phase=phase,
            batch_no=batch_no,
            requested_count=requested_count,
            query_terms=retrieval_plan.query_terms,
            keyword_query=retrieval_plan.keyword_query,
            source_plan_version=retrieval_plan.plan_version,
            rationale=retrieval_plan.rationale,
        )
        new_candidates, search_observation, search_attempts, _ = self._execute_search_tool(
            round_no=round_no,
            query=cts_query,
            target_new=requested_count,
            seen_resume_ids=seen_resume_ids,
            seen_dedup_keys=seen_dedup_keys,
            tracer=tracer,
            city=city,
            phase=phase,
            batch_no=batch_no,
            write_round_artifacts=False,
        )
        if search_attempts:
            city_state.next_page = search_attempts[-1].requested_page + 1
        if search_observation.exhausted_reason != "target_satisfied":
            city_state.exhausted = True
        city_summary = CitySearchSummary(
            city=city,
            phase=phase,
            batch_no=batch_no,
            requested_count=requested_count,
            unique_new_count=search_observation.unique_new_count,
            shortage_count=search_observation.shortage_count,
            start_page=cts_query.page,
            next_page=city_state.next_page,
            fetch_attempt_count=search_observation.fetch_attempt_count,
            exhausted_reason=search_observation.exhausted_reason,
        )
        return {
            "cts_query": cts_query,
            "sent_query_record": sent_query_record,
            "new_candidates": new_candidates,
            "search_observation": search_observation,
            "search_attempts": search_attempts,
            "city_summary": city_summary,
        }

    def _final_exhausted_reason(
        self,
        *,
        target_new: int,
        new_candidate_count: int,
        city_search_summaries: list[CitySearchSummary],
    ) -> str | None:
        if new_candidate_count >= target_new:
            return "target_satisfied"
        if not city_search_summaries:
            return "cts_exhausted"
        for city_summary in reversed(city_search_summaries):
            if city_summary.exhausted_reason:
                return city_summary.exhausted_reason
        return "cts_exhausted"

    def _execute_search_tool(
        self,
        *,
        round_no: int,
        query: CTSQuery,
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
        city: str | None = None,
        phase: LocationExecutionPhase | None = None,
        batch_no: int | None = None,
        write_round_artifacts: bool = True,
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
            attempt_query = query.model_copy(update={"page": page, "page_size": remaining_gap})
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
            batch_new = [item for item in batch_new if item.resume_id not in seen_resume_ids]
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
            attempts.append(
                SearchAttempt(
                    city=city,
                    phase=phase,
                    batch_no=batch_no,
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
        if write_round_artifacts:
            tracer.write_json(
                f"rounds/round_{round_no:02d}/search_observation.json",
                search_observation.model_dump(mode="json"),
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/search_attempts.json",
                [item.model_dump(mode="json") for item in attempts],
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
            normalized = normalize_resume(candidate)
            normalized_store[normalized.resume_id] = normalized
            tracer.write_json(
                f"resumes/{normalized.resume_id}.json",
                normalized.model_dump(mode="json"),
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
                    compared_against_pool_summary=f"Deterministically ranked #{rank} in the current scoring pool.",
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
                    compared_against_pool_summary=f"Ranked #{rank}, outside the retained top pool boundary.",
                )
            )
        return decisions

    def _render_round_review(
        self,
        *,
        round_no: int,
        controller_decision,
        retrieval_plan,
        observation: SearchObservation,
        pool_decisions: list[PoolDecision],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        reflection: ReflectionAdvice | None,
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
        projected_filters = (
            ", ".join(
                f"{field}={value!r}"
                for field, value in retrieval_plan.projected_cts_filters.items()
            )
            or "None"
        )
        runtime_constraints = (
            ", ".join(
                f"{item.field}={item.normalized_value!r}"
                for item in retrieval_plan.runtime_only_constraints
            )
            or "None"
        )
        lines = [
            f"# Round {round_no} Review",
            "",
            "## Controller",
            "",
            f"- Thought summary: {controller_decision.thought_summary}",
            f"- Decision rationale: {controller_decision.decision_rationale}",
            f"- Query terms: {', '.join(retrieval_plan.query_terms) or 'None'}",
            f"- Keyword query: `{retrieval_plan.keyword_query}`",
            f"- Non-location filters: {projected_filters}",
            f"- Runtime-only constraints: {runtime_constraints}",
            "",
            "## Location Execution",
            "",
            f"- Mode: `{retrieval_plan.location_execution_plan.mode}`",
            f"- Allowed locations: {', '.join(retrieval_plan.location_execution_plan.allowed_locations) or 'None'}",
            f"- Preferred locations: {', '.join(retrieval_plan.location_execution_plan.preferred_locations) or 'None'}",
            f"- Priority order: {', '.join(retrieval_plan.location_execution_plan.priority_order) or 'None'}",
            f"- Balanced order: {', '.join(retrieval_plan.location_execution_plan.balanced_order) or 'None'}",
            f"- Rotation offset: `{retrieval_plan.location_execution_plan.rotation_offset}`",
            "",
            "## Search Outcome",
            "",
            f"- Requested new candidates: `{observation.requested_count}`",
            f"- Unique new candidates: `{observation.unique_new_count}`",
            f"- Shortage: `{observation.shortage_count}`",
            f"- Fetch attempts: `{observation.fetch_attempt_count}`",
            f"- Exhausted reason: `{observation.exhausted_reason or 'none'}`",
            f"- Adapter notes: {', '.join(observation.adapter_notes) or 'None'}",
            "",
            "## City Dispatches",
            "",
        ]
        if observation.city_search_summaries:
            for city_summary in observation.city_search_summaries:
                lines.append(
                    "- "
                    f"{city_summary.city} "
                    f"(phase=`{city_summary.phase}`, batch=`{city_summary.batch_no}`): "
                    f"requested=`{city_summary.requested_count}`, "
                    f"new=`{city_summary.unique_new_count}`, "
                    f"shortage=`{city_summary.shortage_count}`, "
                    f"next_page=`{city_summary.next_page}`, "
                    f"reason=`{city_summary.exhausted_reason or 'none'}`"
                )
        else:
            lines.append("- None")
        lines.extend(
            [
                "",
                "## Pool Review",
                "",
            ]
        )
        lines.extend(
            [
                f"- Current top pool: {', '.join(candidate.resume_id for candidate in top_candidates) or 'None'}",
                f"- Newly selected: {', '.join(selected) or 'None'}",
                f"- Retained: {', '.join(retained) or 'None'}",
                f"- Dropped from pool: {', '.join(dropped) or 'None'}",
                f"- Common drop reasons: {common_drop_reasons}",
                f"- Dropped candidates reviewed: `{len(dropped_candidates)}`",
            ]
        )
        if reflection is not None:
            lines.extend(
                [
                    "",
                    "## Reflection",
                    "",
                    f"- Reflection summary: {reflection.reflection_summary}",
                    f"- Strategy assessment: {reflection.strategy_assessment}",
                    f"- Quality assessment: {reflection.quality_assessment}",
                    f"- Coverage assessment: {reflection.coverage_assessment}",
                    f"- Reflection decision: `{'stop' if reflection.suggest_stop else 'continue'}`",
                ]
            )
        else:
            lines.extend(["", "## Reflection", "", "- Reflection summary: Reflection disabled."])
        lines.extend(["", f"- Round stop signal: `{stop_reason or 'continue'}`"])
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
