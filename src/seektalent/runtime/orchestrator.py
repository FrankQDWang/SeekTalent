from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, TypedDict

from seektalent.clients.cts_client import CTSClient, CTSClientProtocol, CTSFetchResult, MockCTSClient
from seektalent.candidate_feedback import build_feedback_decision, select_feedback_seed_resumes
from seektalent.company_discovery import (
    CompanyDiscoveryService,
    inject_target_company_terms,
    select_company_seed_terms,
)
from seektalent.config import AppSettings
from seektalent.controller import ReActController
from seektalent.controller.react_controller import render_controller_prompt
from seektalent.evaluation import TOP_K, EvaluationResult, evaluate_run
from seektalent.finalize.finalizer import Finalizer, render_finalize_prompt
from seektalent.llm import model_provider, preflight_models
from seektalent.models import (
    CTSQuery,
    CitySearchSummary,
    ControllerContext,
    ControllerDecision,
    FinalizeContext,
    FinalResult,
    LocationExecutionPhase,
    NormalizedResume,
    PoolDecision,
    ReflectionAdvice,
    ResumeCandidate,
    QueryTermCandidate,
    QueryRole,
    ReflectionContext,
    RetrievalState,
    RoundState,
    RunState,
    ScoredCandidate,
    SearchControllerDecision,
    SearchAttempt,
    SearchObservation,
    SentQueryRecord,
    StopControllerDecision,
    TerminalControllerRound,
    scored_candidate_sort_key,
    unique_strings,
)
from seektalent.normalization import normalize_resume
from seektalent.prompting import PromptRegistry
from seektalent.progress import ProgressCallback, ProgressEvent
from seektalent.reflection.critic import ReflectionCritic, render_reflection_prompt
from seektalent.requirements import (
    RequirementExtractor,
    build_input_truth,
    build_requirement_digest,
    build_scoring_policy,
)
from seektalent.requirements.extractor import render_requirements_prompt
from seektalent.retrieval import (
    allocate_balanced_city_targets,
    build_default_filter_plan,
    build_location_execution_plan,
    build_round_retrieval_plan,
    canonicalize_filter_plan,
    canonicalize_controller_query_terms,
    derive_explore_query_terms,
    project_constraints_to_cts,
    serialize_keyword_query,
    select_query_terms,
)
from seektalent.resume_quality import ResumeQualityCommenter
from seektalent.runtime.context_builder import (
    build_controller_context,
    build_finalize_context,
    build_reflection_context,
    build_scoring_context,
    top_candidates,
)
from seektalent.runtime.rescue_router import RescueDecision, RescueInputs, SkippedRescueLane, choose_rescue_lane
from seektalent.scoring.scorer import ResumeScorer
from seektalent.tracing import LLMCallSnapshot, RunTracer
from seektalent.tracing import json_char_count, json_sha256, text_char_count, text_sha256

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
    evaluation_result: EvaluationResult | None


@dataclass
class _TermSurfaceStats:
    used_rounds: set[int] = field(default_factory=set)
    sent_query_count: int = 0
    raw_candidate_count: int = 0
    unique_new_count: int = 0
    duplicate_count: int = 0


class RunStageError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_message = message


@dataclass
class _CityExecutionState:
    next_page: int = 1
    exhausted: bool = False


@dataclass
class _LogicalQueryState:
    query_role: QueryRole
    query_terms: list[str]
    keyword_query: str
    next_page: int = 1
    exhausted: bool = False
    adapter_notes: list[str] = field(default_factory=list)
    city_states: dict[str, _CityExecutionState] = field(default_factory=dict)


class _CityDispatchResult(TypedDict):
    cts_query: CTSQuery
    sent_query_record: SentQueryRecord
    new_candidates: list[ResumeCandidate]
    search_observation: SearchObservation
    search_attempts: list[SearchAttempt]
    city_summary: CitySearchSummary


class WorkflowRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.prompts = PromptRegistry(settings.prompt_dir)
        prompt_map = self.prompts.load_many(["requirements", "controller", "scoring", "reflection", "finalize", "judge"])
        self.requirement_extractor = RequirementExtractor(settings, prompt_map["requirements"])
        self.controller = ReActController(settings, prompt_map["controller"])
        self.resume_scorer = ResumeScorer(settings, prompt_map["scoring"])
        self.resume_quality_commenter = ResumeQualityCommenter(settings)
        self.reflection_critic = ReflectionCritic(settings, prompt_map["reflection"])
        self.finalizer = Finalizer(settings, prompt_map["finalize"])
        self.judge_prompt = prompt_map["judge"]
        self.evaluation_runner = evaluate_run
        self.cts_client: CTSClientProtocol = MockCTSClient(settings) if settings.mock_cts else CTSClient(settings)
        self.company_discovery = CompanyDiscoveryService(settings)

    def run(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        progress_callback: ProgressCallback | None = None,
    ) -> RunArtifacts:
        return asyncio.run(
            self.run_async(job_title=job_title, jd=jd, notes=notes, progress_callback=progress_callback)
        )

    async def run_async(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        progress_callback: ProgressCallback | None = None,
    ) -> RunArtifacts:
        tracer = RunTracer(self.settings.runs_path)
        try:
            self._write_run_preamble(tracer=tracer, job_title=job_title, jd=jd, notes=notes)
            self._emit_progress(
                progress_callback,
                "run_started",
                "Starting SeekTalent run.",
                payload={"stage": "runtime"},
            )
            self._require_live_llm_config()
            run_state = await self._build_run_state(
                job_title=job_title,
                jd=jd,
                notes=notes,
                tracer=tracer,
                progress_callback=progress_callback,
            )
            top_scored, stop_reason, rounds_executed, terminal_controller_round = await self._run_rounds(
                run_state=run_state,
                tracer=tracer,
                progress_callback=progress_callback,
            )
            finalize_context = build_finalize_context(
                run_state=run_state,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                run_id=tracer.run_id,
                run_dir=str(tracer.run_dir),
            )
            tracer.write_json("finalizer_context.json", self._slim_finalize_context(finalize_context))
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
            finalizer_prompt = render_finalize_prompt(
                run_id=tracer.run_id,
                run_dir=str(tracer.run_dir),
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                ranked_candidates=top_scored,
            )
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
            self._emit_progress(
                progress_callback,
                "finalizer_started",
                "正在整理最终候选人名单。",
                payload={"stage": "finalizer"},
            )
            try:
                final_result = await self.finalizer.finalize(
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
                        user_prompt_text=finalizer_prompt,
                        input_artifact_refs=["finalizer_context.json"],
                        output_artifact_refs=[],
                        started_at=finalizer_started_at,
                        latency_ms=latency_ms,
                        status="failed",
                        retries=0,
                        output_retries=2,
                        error_message=str(exc),
                        validator_retry_count=self.finalizer.last_validator_retry_count,
                        validator_retry_reasons=self.finalizer.last_validator_retry_reasons,
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
                self._emit_progress(
                    progress_callback,
                    "finalizer_failed",
                    str(exc),
                    payload={"stage": "finalizer", "error_type": type(exc).__name__},
                )
                raise RunStageError("finalization", str(exc)) from exc
            latency_ms = max(1, int((perf_counter() - finalizer_started_clock) * 1000))
            finalizer_structured_output = getattr(self.finalizer, "last_draft_output", None)
            tracer.write_json(
                "finalizer_call.json",
                self._build_llm_call_snapshot(
                    stage="finalize",
                    call_id=finalizer_call_id,
                    model_id=self.settings.finalize_model,
                    prompt_name="finalize",
                    user_payload=finalizer_payload,
                    user_prompt_text=finalizer_prompt,
                    input_artifact_refs=["finalizer_context.json"],
                    output_artifact_refs=["final_candidates.json"],
                    started_at=finalizer_started_at,
                    latency_ms=latency_ms,
                    status="succeeded",
                    retries=0,
                    output_retries=2,
                    structured_output=(
                        finalizer_structured_output.model_dump(mode="json")
                        if finalizer_structured_output is not None
                        else final_result.model_dump(mode="json")
                    ),
                    validator_retry_count=self.finalizer.last_validator_retry_count,
                    validator_retry_reasons=self.finalizer.last_validator_retry_reasons,
                ).model_dump(mode="json"),
            )
            final_markdown = self._render_final_markdown(final_result)
            tracer.write_json("final_candidates.json", final_result.model_dump(mode="json"))
            tracer.write_text("final_answer.md", final_markdown)
            finalizer_completed_artifacts = list(finalizer_artifacts)
            if self.settings.enable_eval:
                tracer.write_json(
                    "judge_packet.json",
                    self._build_judge_packet(
                        tracer=tracer,
                        run_state=run_state,
                        final_result=final_result,
                        rounds_executed=rounds_executed,
                        stop_reason=stop_reason,
                        terminal_controller_round=terminal_controller_round,
                    ),
                )
                finalizer_completed_artifacts.append("judge_packet.json")
            tracer.write_text(
                "run_summary.md",
                self._render_run_summary(
                    run_state=run_state,
                    final_result=final_result,
                    terminal_controller_round=terminal_controller_round,
                ),
            )
            tracer.write_json(
                "search_diagnostics.json",
                self._build_search_diagnostics(
                    tracer=tracer,
                    run_state=run_state,
                    final_result=final_result,
                    terminal_controller_round=terminal_controller_round,
                ),
            )
            finalizer_completed_artifacts.append("search_diagnostics.json")
            self._emit_llm_event(
                tracer=tracer,
                event_type="finalizer_completed",
                call_id=finalizer_call_id,
                model_id=self.settings.finalize_model,
                status="succeeded",
                summary=final_result.summary,
                artifact_paths=finalizer_completed_artifacts + ["run_summary.md"],
                latency_ms=latency_ms,
            )
            self._emit_progress(
                progress_callback,
                "finalizer_completed",
                final_result.summary,
                payload={
                    "stage": "finalizer",
                    "final_candidate_count": len(final_result.candidates),
                    "stop_reason": stop_reason,
                },
            )
            evaluation_result: EvaluationResult | None = None
            if self.settings.enable_eval:
                round_01_candidates = self._materialize_candidates(
                    scored_candidates=run_state.round_history[0].top_candidates if run_state.round_history else [],
                    candidate_store=run_state.candidate_store,
                )
                final_candidates = self._materialize_candidates(
                    scored_candidates=top_scored,
                    candidate_store=run_state.candidate_store,
                )
                evaluation_artifacts = await self.evaluation_runner(
                    settings=self.settings,
                    prompt=self.judge_prompt,
                    run_id=tracer.run_id,
                    run_dir=tracer.run_dir,
                    jd=run_state.input_truth.jd,
                    notes=run_state.input_truth.notes,
                    round_01_candidates=round_01_candidates,
                    final_candidates=final_candidates,
                    rounds_executed=rounds_executed,
                    terminal_stop_guidance=(
                        terminal_controller_round.stop_guidance if terminal_controller_round is not None else None
                    ),
                )
                evaluation_result = evaluation_artifacts.result
                tracer.emit(
                    "evaluation_completed",
                    model=self.settings.effective_judge_model,
                    status="succeeded",
                    summary=(
                        f"round_01 total={evaluation_result.round_01.total_score:.4f}; "
                        f"final total={evaluation_result.final.total_score:.4f}"
                    ),
                    artifact_paths=[str(evaluation_artifacts.path.relative_to(tracer.run_dir))],
                )
            else:
                tracer.emit(
                    "evaluation_skipped",
                    status="skipped",
                    summary="Eval disabled for this run.",
                )
            tracer.write_json(
                "term_surface_audit.json",
                self._build_term_surface_audit(
                    tracer=tracer,
                    run_state=run_state,
                    final_result=final_result,
                    evaluation_result=evaluation_result,
                ),
            )
            tracer.emit(
                "run_finished",
                stop_reason=stop_reason,
                summary=self._render_run_finished_summary(
                    rounds_executed=rounds_executed,
                    terminal_controller_round=terminal_controller_round,
                ),
            )
            self._emit_progress(
                progress_callback,
                "run_completed",
                self._render_run_finished_summary(
                    rounds_executed=rounds_executed,
                    terminal_controller_round=terminal_controller_round,
                ),
                payload={
                    "stage": "runtime",
                    "rounds_executed": rounds_executed,
                    "stop_reason": stop_reason,
                },
            )
            return RunArtifacts(
                final_result=final_result,
                final_markdown=final_markdown,
                run_id=tracer.run_id,
                run_dir=tracer.run_dir,
                trace_log_path=tracer.trace_log_path,
                candidate_store=run_state.candidate_store,
                normalized_store=run_state.normalized_store,
                evaluation_result=evaluation_result,
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
            self._emit_progress(
                progress_callback,
                "run_failed",
                str(exc),
                payload={
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise
        finally:
            tracer.close()

    async def _build_run_state(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None = None,
    ) -> RunState:
        input_truth = build_input_truth(job_title=job_title, jd=jd, notes=notes)
        call_id = "requirements"
        call_payload = {"INPUT_TRUTH": input_truth.model_dump(mode="json")}
        user_prompt = render_requirements_prompt(input_truth)
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
            summary="Extracting requirement truth from the job title, JD, and notes.",
            artifact_paths=artifact_paths,
        )
        self._emit_progress(
            progress_callback,
            "requirements_started",
            "正在分析岗位标题、JD 和 notes。",
            payload={"stage": "requirements"},
        )
        try:
            requirement_draft, requirement_sheet = await self.requirement_extractor.extract_with_draft(
                input_truth=input_truth
            )
        except Exception as exc:  # noqa: BLE001
            requirement_cache_hit = bool(getattr(self.requirement_extractor, "last_cache_hit", False))
            requirement_cache_key = getattr(self.requirement_extractor, "last_cache_key", None)
            requirement_cache_lookup_latency_ms = getattr(
                self.requirement_extractor,
                "last_cache_lookup_latency_ms",
                None,
            )
            requirement_prompt_cache_key = getattr(self.requirement_extractor, "last_prompt_cache_key", None)
            requirement_prompt_cache_retention = getattr(
                self.requirement_extractor,
                "last_prompt_cache_retention",
                None,
            )
            requirement_repair_attempt_count = int(getattr(self.requirement_extractor, "last_repair_attempt_count", 0))
            requirement_repair_succeeded = bool(getattr(self.requirement_extractor, "last_repair_succeeded", False))
            requirement_repair_reason = getattr(self.requirement_extractor, "last_repair_reason", None)
            requirement_full_retry_count = int(getattr(self.requirement_extractor, "last_full_retry_count", 0))
            repair_model = (
                self.settings.structured_repair_model
                if requirement_repair_attempt_count > 0
                else None
            )
            latency_ms = max(1, int((perf_counter() - started_clock) * 1000))
            tracer.write_json(
                "requirements_call.json",
                self._build_llm_call_snapshot(
                    stage="requirements",
                    call_id=call_id,
                    model_id=self.settings.requirements_model,
                    prompt_name="requirements",
                    user_payload=call_payload,
                    user_prompt_text=user_prompt,
                    input_artifact_refs=["input_truth.json", "input_snapshot.json"],
                    output_artifact_refs=[],
                    started_at=started_at,
                    latency_ms=latency_ms,
                    status="failed",
                    retries=0,
                    output_retries=2,
                    error_message=str(exc),
                    cache_hit=requirement_cache_hit,
                    cache_key=requirement_cache_key,
                    cache_lookup_latency_ms=requirement_cache_lookup_latency_ms,
                    prompt_cache_key=requirement_prompt_cache_key,
                    prompt_cache_retention=requirement_prompt_cache_retention,
                    repair_attempt_count=requirement_repair_attempt_count,
                    repair_succeeded=requirement_repair_succeeded,
                    repair_model=repair_model,
                    repair_reason=requirement_repair_reason,
                    full_retry_count=requirement_full_retry_count,
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
            self._emit_progress(
                progress_callback,
                "requirements_failed",
                str(exc),
                payload={"stage": "requirements", "error_type": type(exc).__name__},
            )
            raise RunStageError("requirement_extraction", str(exc)) from exc
        requirement_cache_hit = bool(getattr(self.requirement_extractor, "last_cache_hit", False))
        requirement_cache_key = getattr(self.requirement_extractor, "last_cache_key", None)
        requirement_cache_lookup_latency_ms = getattr(
            self.requirement_extractor,
            "last_cache_lookup_latency_ms",
            None,
        )
        requirement_prompt_cache_key = getattr(self.requirement_extractor, "last_prompt_cache_key", None)
        requirement_prompt_cache_retention = getattr(
            self.requirement_extractor,
            "last_prompt_cache_retention",
            None,
        )
        requirement_repair_attempt_count = int(getattr(self.requirement_extractor, "last_repair_attempt_count", 0))
        requirement_repair_succeeded = bool(getattr(self.requirement_extractor, "last_repair_succeeded", False))
        requirement_repair_reason = getattr(self.requirement_extractor, "last_repair_reason", None)
        requirement_full_retry_count = int(getattr(self.requirement_extractor, "last_full_retry_count", 0))
        repair_model = (
            self.settings.structured_repair_model
            if requirement_repair_attempt_count > 0
            else None
        )
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
                user_prompt_text=user_prompt,
                input_artifact_refs=["input_truth.json", "input_snapshot.json"],
                output_artifact_refs=["requirement_extraction_draft.json", "requirement_sheet.json"],
                started_at=started_at,
                latency_ms=latency_ms,
                status="succeeded",
                retries=0,
                output_retries=2,
                structured_output=requirement_draft.model_dump(mode="json"),
                cache_hit=requirement_cache_hit,
                cache_key=requirement_cache_key,
                cache_lookup_latency_ms=requirement_cache_lookup_latency_ms,
                prompt_cache_key=requirement_prompt_cache_key,
                prompt_cache_retention=requirement_prompt_cache_retention,
                repair_attempt_count=requirement_repair_attempt_count,
                repair_succeeded=requirement_repair_succeeded,
                repair_model=repair_model,
                repair_reason=requirement_repair_reason,
                full_retry_count=requirement_full_retry_count,
            ).model_dump(mode="json"),
        )
        scoring_policy = build_scoring_policy(requirement_sheet)
        run_state = RunState(
            input_truth=input_truth,
            requirement_sheet=requirement_sheet,
            scoring_policy=scoring_policy,
            retrieval_state=RetrievalState(
                current_plan_version=0,
                query_term_pool=requirement_sheet.initial_query_term_pool,
            ),
        )
        tracer.write_json("input_truth.json", input_truth.model_dump(mode="json"))
        tracer.write_json("requirement_sheet.json", requirement_sheet.model_dump(mode="json"))
        tracer.write_json("scoring_policy.json", scoring_policy.model_dump(mode="json"))
        tracer.write_json("sent_query_history.json", [])
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
        self._emit_progress(
            progress_callback,
            "requirements_completed",
            f"岗位需求解析完成：{requirement_sheet.role_title}",
            payload={
                "stage": "requirements",
                "role_title": requirement_sheet.role_title,
                "must_have_capabilities": requirement_sheet.must_have_capabilities,
                "preferred_capabilities": requirement_sheet.preferred_capabilities,
            },
        )
        return run_state

    def _write_run_preamble(self, *, tracer: RunTracer, job_title: str, jd: str, notes: str) -> None:
        tracer.write_json("run_config.json", self._build_public_run_config())
        self._write_prompt_snapshots(tracer)
        input_snapshot = {
            "job_title_chars": len(job_title),
            "jd_chars": len(jd),
            "notes_chars": len(notes),
            "job_title_sha256": hashlib.sha256(job_title.encode("utf-8")).hexdigest(),
            "jd_sha256": hashlib.sha256(jd.encode("utf-8")).hexdigest(),
            "notes_sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
            "job_title_preview": self._preview_text(job_title, limit=120),
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
                    "tui_summary": self.settings.effective_tui_summary_model,
                },
                "enable_eval": self.settings.enable_eval,
                "configured_providers": self._configured_providers(),
            },
        )

    async def _run_rounds(
        self,
        *,
        run_state: RunState,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[ScoredCandidate], str, int, TerminalControllerRound | None]:
        seen_dedup_keys: set[str] = set()
        stop_reason = "max_rounds_reached"
        rounds_executed = 0
        terminal_controller_round: TerminalControllerRound | None = None

        for round_no in range(1, self.settings.max_rounds + 1):
            target_new = TOP_K
            controller_context = build_controller_context(
                run_state=run_state,
                round_no=round_no,
                min_rounds=self.settings.min_rounds,
                max_rounds=self.settings.max_rounds,
                target_new=target_new,
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/controller_context.json",
                self._slim_controller_context(controller_context),
            )
            controller_call_id = f"controller-r{round_no:02d}"
            controller_call_payload = {"CONTROLLER_CONTEXT": controller_context.model_dump(mode="json")}
            controller_prompt = render_controller_prompt(controller_context)
            controller_prompt_cache_key = self._prompt_cache_key(
                stage="controller",
                model_id=self.settings.controller_model,
                input_hash=json_sha256(controller_context.requirement_sheet.model_dump(mode="json")),
            )
            controller_prompt_cache_retention = (
                self.settings.openai_prompt_cache_retention if controller_prompt_cache_key is not None else None
            )
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
            self._emit_progress(
                progress_callback,
                "controller_started",
                f"正在判断第 {round_no} 轮搜索策略。",
                round_no=round_no,
                payload={"stage": "controller"},
            )
            try:
                if isinstance(self.controller, ReActController):
                    controller_decision = await self.controller.decide(
                        context=controller_context,
                        prompt_cache_key=controller_prompt_cache_key,
                    )
                else:
                    controller_decision = await self.controller.decide(context=controller_context)
            except Exception as exc:  # noqa: BLE001
                latency_ms = max(1, int((perf_counter() - controller_started_clock) * 1000))
                controller_repair_attempt_count = int(getattr(self.controller, "last_repair_attempt_count", 0))
                controller_repair_model = (
                    self.settings.structured_repair_model if controller_repair_attempt_count > 0 else None
                )
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/controller_call.json",
                    self._build_llm_call_snapshot(
                        stage="controller",
                        call_id=controller_call_id,
                        model_id=self.settings.controller_model,
                        prompt_name="controller",
                        user_payload=controller_call_payload,
                        user_prompt_text=controller_prompt,
                        input_artifact_refs=[
                            f"rounds/round_{round_no:02d}/controller_context.json",
                            "requirement_sheet.json",
                            "sent_query_history.json",
                        ],
                        output_artifact_refs=[],
                        started_at=controller_started_at,
                        latency_ms=latency_ms,
                        status="failed",
                        retries=0,
                        output_retries=2,
                        error_message=str(exc),
                        round_no=round_no,
                        validator_retry_count=self.controller.last_validator_retry_count,
                        validator_retry_reasons=self.controller.last_validator_retry_reasons,
                        prompt_cache_key=controller_prompt_cache_key,
                        prompt_cache_retention=controller_prompt_cache_retention,
                        repair_attempt_count=controller_repair_attempt_count,
                        repair_succeeded=bool(getattr(self.controller, "last_repair_succeeded", False)),
                        repair_model=controller_repair_model,
                        repair_reason=getattr(self.controller, "last_repair_reason", None),
                        full_retry_count=int(getattr(self.controller, "last_full_retry_count", 0)),
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
                self._emit_progress(
                    progress_callback,
                    "controller_failed",
                    str(exc),
                    round_no=round_no,
                    payload={"stage": "controller", "error_type": type(exc).__name__},
                )
                raise RunStageError("controller", str(exc)) from exc
            rescue_decision: RescueDecision | None = None
            if controller_context.stop_guidance.quality_gate_status in {"broaden_required", "low_quality_exhausted"}:
                rescue_decision = self._choose_rescue_decision(
                    run_state=run_state,
                    controller_context=controller_context,
                    round_no=round_no,
                )
                if rescue_decision.selected_lane == "reserve_broaden":
                    controller_decision = self._force_broaden_decision(
                        run_state=run_state,
                        round_no=round_no,
                        reason=controller_context.stop_guidance.reason,
                    )
                elif rescue_decision.selected_lane == "candidate_feedback":
                    feedback_decision = self._force_candidate_feedback_decision(
                        run_state=run_state,
                        round_no=round_no,
                        reason=controller_context.stop_guidance.reason,
                        tracer=tracer,
                        progress_callback=progress_callback,
                    )
                    if feedback_decision is None:
                        rescue_decision, controller_decision = await self._continue_after_empty_feedback(
                            run_state=run_state,
                            controller_context=controller_context,
                            round_no=round_no,
                            tracer=tracer,
                            rescue_decision=rescue_decision,
                            progress_callback=progress_callback,
                        )
                    else:
                        controller_decision = feedback_decision
                elif rescue_decision.selected_lane == "web_company_discovery":
                    company_decision = await self._force_company_discovery_decision(
                        run_state=run_state,
                        round_no=round_no,
                        reason=controller_context.stop_guidance.reason,
                        tracer=tracer,
                        progress_callback=progress_callback,
                    )
                    if company_decision is None:
                        rescue_decision = self._select_anchor_only_after_failed_company_discovery(
                            run_state=run_state,
                            rescue_decision=rescue_decision,
                        )
                        controller_decision = self._force_anchor_only_decision(
                            run_state=run_state,
                            round_no=round_no,
                            reason=controller_context.stop_guidance.reason,
                        )
                    else:
                        controller_decision = company_decision
                elif rescue_decision.selected_lane == "anchor_only":
                    run_state.retrieval_state.anchor_only_broaden_attempted = True
                    controller_decision = self._force_anchor_only_decision(
                        run_state=run_state,
                        round_no=round_no,
                        reason=controller_context.stop_guidance.reason,
                    )
                else:
                    controller_decision = self._sanitize_controller_decision(
                        decision=controller_decision,
                        run_state=run_state,
                        round_no=round_no,
                    )
                    if isinstance(controller_decision, StopControllerDecision) and not controller_context.stop_guidance.can_stop:
                        controller_decision = self._force_continue_decision(
                            run_state=run_state,
                            round_no=round_no,
                            reason=controller_context.stop_guidance.reason,
                        )
            else:
                controller_decision = self._sanitize_controller_decision(
                    decision=controller_decision,
                    run_state=run_state,
                    round_no=round_no,
                )
                if isinstance(controller_decision, StopControllerDecision) and not controller_context.stop_guidance.can_stop:
                    controller_decision = self._force_continue_decision(
                        run_state=run_state,
                        round_no=round_no,
                        reason=controller_context.stop_guidance.reason,
                    )
            if (
                rescue_decision is not None
                and rescue_decision.selected_lane not in {"allow_stop", "continue_controller"}
                and isinstance(controller_decision, SearchControllerDecision)
            ):
                self._write_rescue_decision(
                    tracer=tracer,
                    round_no=round_no,
                    controller_context=controller_context,
                    decision=rescue_decision,
                    forced_query_terms=controller_decision.proposed_query_terms,
                )
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
                    user_prompt_text=controller_prompt,
                    input_artifact_refs=[
                        f"rounds/round_{round_no:02d}/controller_context.json",
                        "requirement_sheet.json",
                        "sent_query_history.json",
                    ],
                    output_artifact_refs=[f"rounds/round_{round_no:02d}/controller_decision.json"],
                    started_at=controller_started_at,
                    latency_ms=latency_ms,
                    status="succeeded",
                    retries=0,
                    output_retries=2,
                    structured_output=controller_decision.model_dump(mode="json"),
                    round_no=round_no,
                    validator_retry_count=self.controller.last_validator_retry_count,
                    validator_retry_reasons=self.controller.last_validator_retry_reasons,
                    prompt_cache_key=controller_prompt_cache_key,
                    prompt_cache_retention=controller_prompt_cache_retention,
                    repair_attempt_count=int(getattr(self.controller, "last_repair_attempt_count", 0)),
                    repair_succeeded=bool(getattr(self.controller, "last_repair_succeeded", False)),
                    repair_model=(
                        self.settings.structured_repair_model
                        if int(getattr(self.controller, "last_repair_attempt_count", 0)) > 0
                        else None
                    ),
                    repair_reason=getattr(self.controller, "last_repair_reason", None),
                    full_retry_count=int(getattr(self.controller, "last_full_retry_count", 0)),
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
            self._emit_progress(
                progress_callback,
                "controller_completed",
                controller_decision.decision_rationale,
                round_no=round_no,
                payload={
                    "stage": "controller",
                    "action": controller_decision.action,
                    "query_terms": (
                        controller_decision.proposed_query_terms
                        if isinstance(controller_decision, SearchControllerDecision)
                        else []
                    ),
                    "stop_reason": (
                        controller_decision.stop_reason
                        if isinstance(controller_decision, StopControllerDecision)
                        else None
                    ),
                },
            )
            if isinstance(controller_decision, StopControllerDecision):
                stop_reason = self._normalize_stop_reason(
                    proposed=controller_decision.stop_reason,
                )
                terminal_controller_round = TerminalControllerRound(
                    round_no=round_no,
                    controller_decision=controller_decision,
                    stop_guidance=controller_context.stop_guidance,
                )
                break

            assert isinstance(controller_decision, SearchControllerDecision)
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
                title_anchor_term=run_state.requirement_sheet.title_anchor_term,
                query_term_pool=run_state.retrieval_state.query_term_pool,
                projected_cts_filters=projection_result.cts_native_filters,
                runtime_only_constraints=projection_result.runtime_only_constraints,
                location_execution_plan=location_execution_plan,
                target_new=target_new,
                rationale=controller_decision.decision_rationale,
                allow_anchor_only_query=(
                    controller_context.stop_guidance.quality_gate_status == "broaden_required"
                    or run_state.retrieval_state.anchor_only_broaden_attempted
                ),
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
            query_states = self._build_round_query_states(
                round_no=round_no,
                retrieval_plan=retrieval_plan,
                title_anchor_term=run_state.requirement_sheet.title_anchor_term,
                query_term_pool=run_state.retrieval_state.query_term_pool,
                sent_query_history=run_state.retrieval_state.sent_query_history,
            )

            self._emit_progress(
                progress_callback,
                "search_started",
                f"第 {round_no} 轮开始检索：{retrieval_plan.keyword_query}",
                round_no=round_no,
                payload={
                    "stage": "search",
                    "query_terms": retrieval_plan.query_terms,
                    "keyword_query": retrieval_plan.keyword_query,
                    "planned_queries": self._logical_query_summaries(query_states),
                    "target_new": target_new,
                },
            )
            try:
                (
                    cts_queries,
                    sent_query_records,
                    new_candidates,
                    search_observation,
                    search_attempts,
                ) = await self._execute_location_search_plan(
                    round_no=round_no,
                    retrieval_plan=retrieval_plan,
                    query_states=query_states,
                    base_adapter_notes=projection_result.adapter_notes,
                    target_new=target_new,
                    seen_resume_ids=set(run_state.seen_resume_ids),
                    seen_dedup_keys=seen_dedup_keys,
                    tracer=tracer,
                )
            except Exception as exc:  # noqa: BLE001
                self._emit_progress(
                    progress_callback,
                    "search_failed",
                    str(exc),
                    round_no=round_no,
                    payload={"stage": "search", "error_type": type(exc).__name__},
                )
                raise RunStageError("search_cts", str(exc)) from exc
            self._emit_progress(
                progress_callback,
                "search_completed",
                (
                    f"第 {round_no} 轮检索完成：搜到 {search_observation.raw_candidate_count} 人，"
                    f"新增 {search_observation.unique_new_count} 人。"
                ),
                round_no=round_no,
                payload={
                    "stage": "search",
                    "query_terms": retrieval_plan.query_terms,
                    "executed_queries": self._executed_query_summaries(cts_queries),
                    "raw_candidate_count": search_observation.raw_candidate_count,
                    "unique_new_count": search_observation.unique_new_count,
                    "shortage_count": search_observation.shortage_count,
                    "fetch_attempt_count": search_observation.fetch_attempt_count,
                },
            )
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

            previous_scored_count = len(run_state.scorecards_by_resume_id)
            self._emit_progress(
                progress_callback,
                "scoring_started",
                f"第 {round_no} 轮开始评分：{len(new_candidates)} 位新增候选人。",
                round_no=round_no,
                payload={"stage": "scoring", "candidate_count": len(new_candidates)},
            )
            current_top_candidates, pool_decisions, dropped_candidates = await self._score_round(
                round_no=round_no,
                new_candidates=new_candidates,
                run_state=run_state,
                tracer=tracer,
            )
            newly_scored_count = len(run_state.scorecards_by_resume_id) - previous_scored_count
            scored_this_round = [
                candidate
                for candidate in run_state.scorecards_by_resume_id.values()
                if candidate.source_round == round_no
            ]
            self._emit_progress(
                progress_callback,
                "scoring_completed",
                (
                    f"第 {round_no} 轮评分完成：{newly_scored_count} 人进入评分，"
                    f"fit {sum(1 for item in scored_this_round if item.fit_bucket == 'fit')}。"
                ),
                round_no=round_no,
                payload={
                    "stage": "scoring",
                    "newly_scored_count": newly_scored_count,
                    "fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "fit"),
                    "not_fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "not_fit"),
                    "top_pool_count": len(current_top_candidates),
                },
            )
            resume_quality_comment: str | None = None
            resume_quality_comment_error: str | None = None
            try:
                resume_quality_comment = await self.resume_quality_commenter.comment(
                    round_no=round_no,
                    query_terms=retrieval_plan.query_terms,
                    candidates=sorted(scored_this_round, key=scored_candidate_sort_key)[:5],
                    normalized_store=run_state.normalized_store,
                ) or None
            except Exception as exc:  # noqa: BLE001
                resume_quality_comment_error = str(exc)
            if resume_quality_comment:
                self._emit_progress(
                    progress_callback,
                    "resume_quality_comment_completed",
                    f"本轮简历质量：{resume_quality_comment}",
                    round_no=round_no,
                    payload={"stage": "resume_quality", "resume_quality_comment": resume_quality_comment},
                )
            elif resume_quality_comment_error:
                self._emit_progress(
                    progress_callback,
                    "resume_quality_comment_failed",
                    "本轮简历质量短评生成失败，已继续 reflection。",
                    round_no=round_no,
                    payload={"stage": "resume_quality", "error": resume_quality_comment_error},
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
                self._slim_reflection_context(reflection_context),
            )
            reflection_call_id = f"reflection-r{round_no:02d}"
            reflection_call_payload = {"REFLECTION_CONTEXT": reflection_context.model_dump(mode="json")}
            reflection_prompt = render_reflection_prompt(reflection_context)
            reflection_prompt_cache_key = self._prompt_cache_key(
                stage="reflection",
                model_id=self.settings.reflection_model,
                input_hash=json_sha256(reflection_context.model_dump(mode="json")),
            )
            reflection_prompt_cache_retention = (
                self.settings.openai_prompt_cache_retention if reflection_prompt_cache_key is not None else None
            )
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
            self._emit_progress(
                progress_callback,
                "reflection_started",
                f"正在复盘第 {round_no} 轮关键词、候选人质量和下一步。",
                round_no=round_no,
                payload={"stage": "reflection"},
            )
            try:
                reflection_advice = await self._reflect_round(
                    context=reflection_context,
                    run_state=run_state,
                    prompt_cache_key=reflection_prompt_cache_key,
                )
            except Exception as exc:  # noqa: BLE001
                latency_ms = max(1, int((perf_counter() - reflection_started_clock) * 1000))
                reflection_repair_attempt_count = int(getattr(self.reflection_critic, "last_repair_attempt_count", 0))
                reflection_repair_model = (
                    self.settings.structured_repair_model if reflection_repair_attempt_count > 0 else None
                )
                tracer.write_json(
                    f"rounds/round_{round_no:02d}/reflection_call.json",
                    self._build_llm_call_snapshot(
                        stage="reflection",
                        call_id=reflection_call_id,
                        model_id=self.settings.reflection_model,
                        prompt_name="reflection",
                        user_payload=reflection_call_payload,
                        user_prompt_text=reflection_prompt,
                        input_artifact_refs=[
                            f"rounds/round_{round_no:02d}/reflection_context.json",
                            "requirement_sheet.json",
                            "sent_query_history.json",
                        ],
                        output_artifact_refs=[],
                        started_at=reflection_started_at,
                        latency_ms=latency_ms,
                        status="failed",
                        retries=0,
                        output_retries=2,
                        error_message=str(exc),
                        round_no=round_no,
                        validator_retry_count=int(getattr(self.reflection_critic, "last_validator_retry_count", 0)),
                        validator_retry_reasons=list(getattr(self.reflection_critic, "last_validator_retry_reasons", [])),
                        prompt_cache_key=reflection_prompt_cache_key,
                        prompt_cache_retention=reflection_prompt_cache_retention,
                        repair_attempt_count=reflection_repair_attempt_count,
                        repair_succeeded=bool(getattr(self.reflection_critic, "last_repair_succeeded", False)),
                        repair_model=reflection_repair_model,
                        repair_reason=getattr(self.reflection_critic, "last_repair_reason", None),
                        full_retry_count=int(getattr(self.reflection_critic, "last_full_retry_count", 0)),
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
                self._emit_progress(
                    progress_callback,
                    "reflection_failed",
                    str(exc),
                    round_no=round_no,
                    payload={"stage": "reflection", "error_type": type(exc).__name__},
                )
                raise
            round_state.reflection_advice = reflection_advice
            tracer.write_json(
                f"rounds/round_{round_no:02d}/reflection_advice.json",
                reflection_advice.model_dump(mode="json"),
            )
            latency_ms = max(1, int((perf_counter() - reflection_started_clock) * 1000))
            reflection_repair_attempt_count = int(getattr(self.reflection_critic, "last_repair_attempt_count", 0))
            tracer.write_json(
                f"rounds/round_{round_no:02d}/reflection_call.json",
                self._build_llm_call_snapshot(
                    stage="reflection",
                    call_id=reflection_call_id,
                    model_id=self.settings.reflection_model,
                    prompt_name="reflection",
                    user_payload=reflection_call_payload,
                    user_prompt_text=reflection_prompt,
                    input_artifact_refs=[
                        f"rounds/round_{round_no:02d}/reflection_context.json",
                        "requirement_sheet.json",
                        "sent_query_history.json",
                    ],
                    output_artifact_refs=[f"rounds/round_{round_no:02d}/reflection_advice.json"],
                    started_at=reflection_started_at,
                    latency_ms=latency_ms,
                    status="succeeded",
                    retries=0,
                    output_retries=2,
                    structured_output=reflection_advice.model_dump(mode="json"),
                    round_no=round_no,
                    validator_retry_count=int(getattr(self.reflection_critic, "last_validator_retry_count", 0)),
                    validator_retry_reasons=list(getattr(self.reflection_critic, "last_validator_retry_reasons", [])),
                    prompt_cache_key=reflection_prompt_cache_key,
                    prompt_cache_retention=reflection_prompt_cache_retention,
                    repair_attempt_count=reflection_repair_attempt_count,
                    repair_succeeded=bool(getattr(self.reflection_critic, "last_repair_succeeded", False)),
                    repair_model=(
                        self.settings.structured_repair_model if reflection_repair_attempt_count > 0 else None
                    ),
                    repair_reason=getattr(self.reflection_critic, "last_repair_reason", None),
                    full_retry_count=int(getattr(self.reflection_critic, "last_full_retry_count", 0)),
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
            self._emit_progress(
                progress_callback,
                "reflection_completed",
                reflection_advice.reflection_rationale or reflection_advice.reflection_summary,
                round_no=round_no,
                payload={
                    "stage": "reflection",
                    "reflection_summary": reflection_advice.reflection_summary,
                    "reflection_rationale": reflection_advice.reflection_rationale,
                    "suggest_stop": reflection_advice.suggest_stop,
                    "suggested_stop_reason": reflection_advice.suggested_stop_reason,
                },
            )
            tracer.write_text(
                f"rounds/round_{round_no:02d}/round_review.md",
                self._render_round_review(
                    round_no=round_no,
                    controller_decision=controller_decision,
                    retrieval_plan=retrieval_plan,
                    observation=search_observation,
                    newly_scored_count=newly_scored_count,
                    pool_decisions=pool_decisions,
                    top_candidates=current_top_candidates,
                    dropped_candidates=dropped_candidates,
                    reflection=reflection_advice,
                    next_step=self._next_step_after_round(round_no=round_no),
                ),
            )
            self._emit_progress(
                progress_callback,
                "round_completed",
                f"第 {round_no} 轮完成。",
                round_no=round_no,
                payload=self._build_round_progress_payload(
                    run_state=run_state,
                    round_no=round_no,
                    retrieval_plan=retrieval_plan,
                    cts_queries=cts_queries,
                    observation=search_observation,
                    newly_scored_count=newly_scored_count,
                    pool_decisions=pool_decisions,
                    reflection=reflection_advice,
                    resume_quality_comment=resume_quality_comment,
                    resume_quality_comment_error=resume_quality_comment_error,
                ),
            )

            rounds_executed = round_no

        return top_candidates(run_state), stop_reason, rounds_executed, terminal_controller_round

    def _build_round_progress_payload(
        self,
        *,
        run_state: RunState,
        round_no: int,
        retrieval_plan,
        cts_queries: list[CTSQuery],
        observation: SearchObservation,
        newly_scored_count: int,
        pool_decisions: list[PoolDecision],
        reflection: ReflectionAdvice | None,
        resume_quality_comment: str | None = None,
        resume_quality_comment_error: str | None = None,
    ) -> dict[str, object]:
        scored_this_round = [
            candidate
            for candidate in run_state.scorecards_by_resume_id.values()
            if candidate.source_round == round_no
        ]
        decision_counts = Counter(item.decision for item in pool_decisions)
        return {
            "round_no": round_no,
            "query_terms": retrieval_plan.query_terms,
            "keyword_query": retrieval_plan.keyword_query,
            "executed_queries": self._executed_query_summaries(cts_queries),
            "raw_candidate_count": observation.raw_candidate_count,
            "unique_new_count": observation.unique_new_count,
            "newly_scored_count": newly_scored_count,
            "fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "fit"),
            "not_fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "not_fit"),
            "top_pool_selected_count": decision_counts["selected"],
            "top_pool_retained_count": decision_counts["retained"],
            "top_pool_dropped_count": decision_counts["dropped"],
            "representative_candidates": self._representative_candidate_summaries(
                run_state=run_state,
                candidates=sorted(scored_this_round, key=scored_candidate_sort_key)[:5],
            ),
            "resume_quality_comment": resume_quality_comment,
            "resume_quality_comment_error": resume_quality_comment_error,
            "reflection_summary": reflection.reflection_summary if reflection is not None else "",
            "reflection_rationale": reflection.reflection_rationale if reflection is not None else "",
        }

    def _logical_query_summaries(self, query_states: list[_LogicalQueryState]) -> list[dict[str, object]]:
        return [
            {
                "query_role": query.query_role,
                "query_terms": query.query_terms,
                "keyword_query": query.keyword_query,
            }
            for query in query_states
        ]

    def _executed_query_summaries(self, cts_queries: list[CTSQuery]) -> list[dict[str, object]]:
        summaries: list[dict[str, object]] = []
        seen: set[tuple[str, tuple[str, ...], str]] = set()
        for query in cts_queries:
            key = (query.query_role, tuple(query.query_terms), query.keyword_query)
            if key in seen:
                continue
            seen.add(key)
            summaries.append(
                {
                    "query_role": query.query_role,
                    "query_terms": query.query_terms,
                    "keyword_query": query.keyword_query,
                }
            )
        return summaries

    def _representative_candidate_summaries(
        self,
        *,
        run_state: RunState,
        candidates: list[ScoredCandidate],
    ) -> list[str]:
        summaries: list[str] = []
        for candidate in candidates:
            resume = run_state.normalized_store.get(candidate.resume_id)
            resume_summary = resume.compact_summary() if resume is not None else ""
            if not resume_summary and candidate.resume_id in run_state.candidate_store:
                resume_summary = run_state.candidate_store[candidate.resume_id].compact_summary()
            parts = [
                candidate.resume_id,
                f"{candidate.overall_score} 分",
                candidate.fit_bucket,
                resume_summary,
                self._preview_text(candidate.reasoning_summary, limit=80),
            ]
            summaries.append(" · ".join(part for part in parts if part))
        return summaries

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
        if isinstance(decision, StopControllerDecision):
            return decision.model_copy(
                update={
                    "decision_rationale": self._sanitize_premature_max_round_claim(
                        decision.decision_rationale,
                        round_no=round_no,
                    ),
                    "stop_reason": self._sanitize_premature_max_round_claim(
                        decision.stop_reason,
                        round_no=round_no,
                    ),
                }
            )
        assert isinstance(decision, SearchControllerDecision)
        query_terms = canonicalize_controller_query_terms(
            decision.proposed_query_terms,
            round_no=round_no,
            title_anchor_term=run_state.requirement_sheet.title_anchor_term,
            query_term_pool=run_state.retrieval_state.query_term_pool,
        )
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

    def _sanitize_premature_max_round_claim(self, text: str, *, round_no: int) -> str:
        if round_no >= self.settings.max_rounds:
            return text
        lowered = text.casefold()
        if "max rounds" not in lowered and "maximum rounds" not in lowered:
            return text
        cleaned = re.sub(
            r"(?i)the search has reached the maximum rounds \(\d+\),\s*",
            "The search appears exhausted with diminishing returns, ",
            text,
        )
        cleaned = re.sub(
            r"(?i)search is exhausted:\s*max(?:imum)? rounds? reached,\s*",
            "Search is exhausted with diminishing returns; ",
            cleaned,
        )
        cleaned = re.sub(
            r"(?i)\bmax(?:imum)? rounds? reached\b[:,]?\s*",
            "diminishing returns, ",
            cleaned,
        )
        return " ".join(cleaned.split())

    def _force_continue_decision(self, *, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
        return SearchControllerDecision(
            thought_summary="Runtime override: stop guidance requires continuing.",
            action="search_cts",
            decision_rationale=f"Runtime stop guidance requires continuing: {reason}",
            proposed_query_terms=select_query_terms(
                run_state.retrieval_state.query_term_pool,
                round_no=round_no,
                title_anchor_term=run_state.requirement_sheet.title_anchor_term,
            ),
            proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
            response_to_reflection=f"Runtime override: {reason}",
        )

    def _choose_rescue_decision(self, *, run_state: RunState, controller_context: ControllerContext, round_no: int) -> RescueDecision:
        reserve = self._untried_admitted_non_anchor_reserve(run_state.retrieval_state)
        seed_candidates = [
            run_state.scorecards_by_resume_id[resume_id]
            for resume_id in run_state.top_pool_ids
            if resume_id in run_state.scorecards_by_resume_id
        ]
        seeds = select_feedback_seed_resumes(seed_candidates)
        decision = choose_rescue_lane(
            RescueInputs(
                stop_guidance=controller_context.stop_guidance,
                has_untried_reserve_family=reserve is not None,
                has_feedback_seed_resumes=len(seeds) >= 2,
                candidate_feedback_enabled=self.settings.candidate_feedback_enabled,
                candidate_feedback_attempted=run_state.retrieval_state.candidate_feedback_attempted,
                company_discovery_enabled=self.settings.company_discovery_enabled,
                company_discovery_attempted=run_state.retrieval_state.company_discovery_attempted,
                company_discovery_useful=self._company_discovery_useful(controller_context),
                anchor_only_broaden_attempted=run_state.retrieval_state.anchor_only_broaden_attempted,
            )
        )
        run_state.retrieval_state.rescue_lane_history.append(
            {"round_no": round_no, "selected_lane": decision.selected_lane}
        )
        return decision

    def _company_discovery_useful(self, controller_context: ControllerContext) -> bool:
        return bool(self.settings.bocha_api_key) and controller_context.stop_guidance.quality_gate_status in {
            "broaden_required",
            "low_quality_exhausted",
        }

    async def _continue_after_empty_feedback(
        self,
        *,
        run_state: RunState,
        controller_context: ControllerContext,
        round_no: int,
        tracer: RunTracer,
        rescue_decision: RescueDecision,
        progress_callback: ProgressCallback | None,
    ) -> tuple[RescueDecision, SearchControllerDecision]:
        skipped = [
            *rescue_decision.skipped_lanes,
            SkippedRescueLane(lane="candidate_feedback", reason="no_safe_feedback_term"),
        ]
        if (
            self.settings.company_discovery_enabled
            and not run_state.retrieval_state.company_discovery_attempted
            and self._company_discovery_useful(controller_context)
        ):
            company_rescue = rescue_decision.model_copy(
                update={"selected_lane": "web_company_discovery", "skipped_lanes": skipped}
            )
            run_state.retrieval_state.rescue_lane_history[-1]["selected_lane"] = "web_company_discovery"
            company_decision = await self._force_company_discovery_decision(
                run_state=run_state,
                round_no=round_no,
                reason=controller_context.stop_guidance.reason,
                tracer=tracer,
                progress_callback=progress_callback,
            )
            if company_decision is not None:
                return company_rescue, company_decision
            rescue_decision = company_rescue
        else:
            skipped.append(
                SkippedRescueLane(
                    lane="web_company_discovery",
                    reason=self._company_discovery_skip_reason(run_state, controller_context),
                )
            )
        anchor_rescue = self._select_anchor_only_after_failed_company_discovery(
            run_state=run_state,
            rescue_decision=rescue_decision.model_copy(update={"skipped_lanes": skipped}),
        )
        return anchor_rescue, self._force_anchor_only_decision(
            run_state=run_state,
            round_no=round_no,
            reason=controller_context.stop_guidance.reason,
        )

    def _company_discovery_skip_reason(self, run_state: RunState, controller_context: ControllerContext) -> str:
        if not self.settings.company_discovery_enabled:
            return "disabled"
        if run_state.retrieval_state.company_discovery_attempted:
            return "already_attempted"
        if not self._company_discovery_useful(controller_context):
            return "not_useful"
        return "no_usable_company_terms"

    def _select_anchor_only_after_failed_company_discovery(
        self,
        *,
        run_state: RunState,
        rescue_decision: RescueDecision,
    ) -> RescueDecision:
        run_state.retrieval_state.anchor_only_broaden_attempted = True
        run_state.retrieval_state.rescue_lane_history[-1]["selected_lane"] = "anchor_only"
        skipped = list(rescue_decision.skipped_lanes)
        if not any(item.lane == "web_company_discovery" for item in skipped):
            skipped.append(SkippedRescueLane(lane="web_company_discovery", reason="no_usable_company_terms"))
        return rescue_decision.model_copy(update={"selected_lane": "anchor_only", "skipped_lanes": skipped})

    def _write_rescue_decision(
        self,
        *,
        tracer: RunTracer,
        round_no: int,
        controller_context: ControllerContext,
        decision: RescueDecision,
        forced_query_terms: list[str],
    ) -> None:
        tracer.write_json(
            f"rounds/round_{round_no:02d}/rescue_decision.json",
            {
                "trigger_status": controller_context.stop_guidance.quality_gate_status,
                "selected_lane": decision.selected_lane,
                "skipped_lanes": [item.model_dump(mode="json") for item in decision.skipped_lanes],
                "forced_query_terms": forced_query_terms,
            },
        )

    def _force_candidate_feedback_decision(
        self,
        *,
        run_state: RunState,
        round_no: int,
        reason: str,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None,
    ) -> SearchControllerDecision | None:
        seeds = select_feedback_seed_resumes(
            [
                run_state.scorecards_by_resume_id[resume_id]
                for resume_id in run_state.top_pool_ids
                if resume_id in run_state.scorecards_by_resume_id
            ]
        )
        negatives = [
            item
            for item in run_state.scorecards_by_resume_id.values()
            if item.fit_bucket == "not_fit" or item.risk_score > 60
        ]
        sent_terms = [term for record in run_state.retrieval_state.sent_query_history for term in record.query_terms]
        feedback = build_feedback_decision(
            seed_resumes=seeds,
            negative_resumes=negatives,
            existing_terms=run_state.retrieval_state.query_term_pool,
            sent_query_terms=sent_terms,
            round_no=round_no,
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/candidate_feedback_input.json",
            {
                "seed_resume_ids": [item.resume_id for item in seeds],
                "negative_resume_ids": [item.resume_id for item in negatives],
                "sent_query_terms": sent_terms,
            },
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/candidate_feedback_terms.json",
            feedback.model_dump(mode="json"),
        )
        run_state.retrieval_state.candidate_feedback_attempted = True
        tracer.write_json(
            f"rounds/round_{round_no:02d}/candidate_feedback_decision.json",
            {
                "accepted_term": (
                    feedback.accepted_term.model_dump(mode="json") if feedback.accepted_term is not None else None
                ),
                "forced_query_terms": feedback.forced_query_terms,
                "skipped_reason": feedback.skipped_reason,
            },
        )
        if feedback.accepted_term is None:
            return None
        run_state.retrieval_state.query_term_pool.append(feedback.accepted_term)
        self._emit_progress(
            progress_callback,
            "rescue_lane_completed",
            f"Recall repair: extracted feedback term {feedback.accepted_term.term} from {len(seeds)} fit seed resumes.",
            round_no=round_no,
            payload={
                "stage": "rescue",
                "selected_lane": "candidate_feedback",
                "accepted_term": feedback.accepted_term.term,
                "seed_resume_count": len(seeds),
            },
        )
        return SearchControllerDecision(
            thought_summary="Runtime rescue: candidate feedback expansion.",
            action="search_cts",
            decision_rationale=f"Runtime rescue: candidate feedback term {feedback.accepted_term.term}; {reason}",
            proposed_query_terms=feedback.forced_query_terms,
            proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
            response_to_reflection=f"Runtime rescue: {reason}",
        )

    async def _force_company_discovery_decision(
        self,
        *,
        run_state: RunState,
        round_no: int,
        reason: str,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None,
    ) -> SearchControllerDecision | None:
        result = await self.company_discovery.discover_web(
            requirement_sheet=run_state.requirement_sheet,
            round_no=round_no,
            trigger_reason=reason,
        )
        run_state.retrieval_state.company_discovery_attempted = True
        run_state.retrieval_state.target_company_plan = result.plan.model_dump(mode="json")
        tracer.write_json(
            f"rounds/round_{round_no:02d}/company_discovery_result.json",
            result.model_dump(mode="json"),
        )
        prefix = f"rounds/round_{round_no:02d}"
        if result.discovery_input is not None:
            tracer.write_json(f"{prefix}/company_discovery_input.json", result.discovery_input.model_dump(mode="json"))
        tracer.write_json(f"{prefix}/company_discovery_plan.json", result.plan.model_dump(mode="json"))
        tracer.write_json(
            f"{prefix}/company_search_queries.json",
            [item.model_dump(mode="json") for item in result.search_tasks],
        )
        tracer.write_json(
            f"{prefix}/company_search_results.json",
            [item.model_dump(mode="json") for item in result.search_results],
        )
        tracer.write_json(
            f"{prefix}/company_search_rerank.json",
            [item.model_dump(mode="json") for item in result.reranked_results],
        )
        tracer.write_json(
            f"{prefix}/company_page_reads.json",
            [item.model_dump(mode="json") for item in result.page_reads],
        )
        tracer.write_json(
            f"{prefix}/company_evidence_cards.json",
            [item.model_dump(mode="json") for item in result.evidence_candidates],
        )
        run_state.retrieval_state.query_term_pool = inject_target_company_terms(
            run_state.retrieval_state.query_term_pool,
            result.plan,
            first_added_round=round_no,
            accepted_limit=self.settings.company_discovery_accepted_company_limit,
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/query_term_pool_after_company_discovery.json",
            [item.model_dump(mode="json") for item in run_state.retrieval_state.query_term_pool],
        )
        query_terms = select_company_seed_terms(
            run_state.retrieval_state.query_term_pool,
            run_state.retrieval_state.sent_query_history,
            forced_families=set(),
            max_terms=2,
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/company_discovery_decision.json",
            {
                "forced_query_terms": [item.term for item in query_terms],
                "accepted_company_count": len(result.plan.accepted_targets),
                "stop_reason": result.plan.stop_reason,
            },
        )
        self._emit_progress(
            progress_callback,
            "company_discovery_completed",
            "Target company discovery completed.",
            round_no=round_no,
            payload={
                "stage": "company_discovery",
                "search_result_count": len(result.search_results),
                "reranked_result_count": len(result.reranked_results),
                "opened_page_count": len(result.page_reads),
                "accepted_company_count": len(result.plan.accepted_targets),
                "accepted_companies": [item.name for item in result.plan.accepted_targets],
                "holdout_companies": result.plan.holdout_companies,
                "rejected_companies": result.plan.rejected_companies,
                "search_queries": [item.query for item in result.search_tasks],
                "reranked_pages": [f"{item.score:.2f} {item.title or item.url}" for item in result.reranked_results],
                "page_titles": [item.title or item.url for item in result.page_reads],
                "next_query_terms": [item.term for item in query_terms],
            },
        )
        if len(query_terms) < 2:
            return None
        return SearchControllerDecision(
            thought_summary="Runtime rescue: web target company discovery.",
            action="search_cts",
            decision_rationale=f"Runtime rescue: web company discovery found {query_terms[1].term}; {reason}",
            proposed_query_terms=[item.term for item in query_terms],
            proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
            response_to_reflection=f"Runtime rescue: {reason}",
        )

    def _force_anchor_only_decision(self, *, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
        anchor = self._active_admitted_anchor(run_state.retrieval_state.query_term_pool)
        return SearchControllerDecision(
            thought_summary="Runtime rescue: final anchor-only broaden.",
            action="search_cts",
            decision_rationale=f"Runtime broaden: anchor-only search; {reason}",
            proposed_query_terms=[anchor.term],
            proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
            response_to_reflection=f"Runtime rescue: {reason}",
        )

    def _force_broaden_decision(self, *, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
        anchor = self._active_admitted_anchor(run_state.retrieval_state.query_term_pool)
        reserve = self._untried_admitted_non_anchor_reserve(run_state.retrieval_state)
        if reserve is None:
            query_terms = [anchor.term]
            broaden_detail = "anchor-only search"
        else:
            run_state.retrieval_state.query_term_pool = self._activate_query_term(
                run_state.retrieval_state.query_term_pool,
                reserve.term,
            )
            query_terms = [anchor.term, reserve.term]
            broaden_detail = f"reserve admitted family {reserve.family}"
        rationale = f"Runtime broaden: {broaden_detail}; {reason}"
        return SearchControllerDecision(
            thought_summary="Runtime override: broaden before low-quality stop.",
            action="search_cts",
            decision_rationale=rationale,
            proposed_query_terms=query_terms,
            proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
            response_to_reflection=f"Runtime override: {reason}",
        )

    def _active_admitted_anchor(self, query_term_pool: list[QueryTermCandidate]) -> QueryTermCandidate:
        anchors = sorted(
            [
                item
                for item in query_term_pool
                if item.active and item.queryability == "admitted" and item.retrieval_role == "role_anchor"
            ],
            key=lambda item: (item.priority, item.first_added_round, item.term.casefold()),
        )
        if not anchors:
            raise ValueError("compiled query term pool must include one active admitted anchor.")
        return anchors[0]

    def _untried_admitted_non_anchor_reserve(self, retrieval_state: RetrievalState) -> QueryTermCandidate | None:
        tried = self._tried_query_families(retrieval_state)
        candidates = [
            item
            for item in retrieval_state.query_term_pool
            if item.queryability == "admitted" and item.retrieval_role != "role_anchor" and item.family not in tried
        ]
        return min(
            candidates,
            key=lambda item: (0 if item.active else 1, item.priority, item.first_added_round, item.family),
            default=None,
        )

    def _tried_query_families(self, retrieval_state: RetrievalState) -> set[str]:
        term_index = {self._query_term_key(item.term): item for item in retrieval_state.query_term_pool}
        return {
            candidate.family
            for record in retrieval_state.sent_query_history
            for term in record.query_terms
            if (candidate := term_index.get(self._query_term_key(term))) is not None
        }

    def _activate_query_term(
        self,
        query_term_pool: list[QueryTermCandidate],
        term: str,
    ) -> list[QueryTermCandidate]:
        key = self._query_term_key(term)
        return [
            item.model_copy(update={"active": True}) if self._query_term_key(item.term) == key else item
            for item in query_term_pool
        ]

    def _query_term_key(self, term: str) -> str:
        return " ".join(term.strip().split()).casefold()

    async def _reflect_round(
        self,
        *,
        context,
        run_state: RunState,
        prompt_cache_key: str | None = None,
    ) -> ReflectionAdvice:
        if not self.settings.enable_reflection:
            advice = ReflectionAdvice(
                reflection_summary="Reflection disabled.",
                reflection_rationale="Reflection is disabled for this run.",
            )
            return advice
        try:
            if isinstance(self.reflection_critic, ReflectionCritic):
                advice = await self.reflection_critic.reflect(context=context, prompt_cache_key=prompt_cache_key)
            else:
                advice = await self.reflection_critic.reflect(context=context)
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
        del round_no
        activate_terms = {item.casefold() for item in advice.keyword_advice.suggested_activate_terms}
        drop_terms = {item.casefold() for item in advice.keyword_advice.suggested_drop_terms}
        deprioritize_terms = {item.casefold() for item in advice.keyword_advice.suggested_deprioritize_terms}
        updated: list[QueryTermCandidate] = []
        for item in pool:
            candidate = item
            key = candidate.term.casefold()
            if candidate.source == "job_title":
                updated.append(candidate)
                continue
            active = key in activate_terms or (candidate.active and key not in drop_terms)
            if key in deprioritize_terms:
                candidate = candidate.model_copy(update={"priority": candidate.priority + 100})
            if candidate.active != active:
                candidate = candidate.model_copy(update={"active": active})
            updated.append(candidate)
        if not any(item.active for item in updated if item.source != "job_title"):
            fallback = min(
                (item for item in updated if item.source != "job_title"),
                key=lambda item: (item.priority, item.first_added_round, item.term.casefold()),
                default=None,
            )
            if fallback is not None:
                updated = [
                    item.model_copy(update={"active": True}) if item.term.casefold() == fallback.term.casefold() else item
                    for item in updated
                ]
        return updated

    async def _score_round(
        self,
        *,
        round_no: int,
        new_candidates: list[ResumeCandidate],
        run_state: RunState,
        tracer: RunTracer,
    ) -> tuple[list[ScoredCandidate], list[PoolDecision], list[ScoredCandidate]]:
        scoring_pool = self._build_scoring_pool(
            new_candidates=new_candidates,
            scorecards_by_resume_id=run_state.scorecards_by_resume_id,
        )
        normalized_scoring_pool = self._normalize_scoring_pool(
            round_no=round_no,
            scoring_pool=scoring_pool,
            tracer=tracer,
            normalized_store=run_state.normalized_store,
        )
        tracer.write_jsonl(
            f"rounds/round_{round_no:02d}/scoring_input_refs.jsonl",
            [self._scoring_input_ref(item) for item in normalized_scoring_pool],
        )
        scoring_contexts = [
            build_scoring_context(
                run_state=run_state,
                round_no=round_no,
                normalized_resume=item,
            )
            for item in normalized_scoring_pool
        ]
        previous_top_ids = set(run_state.top_pool_ids)
        if scoring_contexts:
            scored_candidates, scoring_failures = await self.resume_scorer.score_candidates_parallel(
                contexts=scoring_contexts,
                tracer=tracer,
            )
            if scoring_failures:
                raise RunStageError("scoring", self._format_scoring_failure_message(scoring_failures))
            for candidate in scored_candidates:
                if candidate.resume_id not in run_state.scorecards_by_resume_id:
                    run_state.scorecards_by_resume_id[candidate.resume_id] = candidate
        else:
            scored_candidates = []
        global_ranked_candidates = sorted(run_state.scorecards_by_resume_id.values(), key=scored_candidate_sort_key)
        current_top_candidates = global_ranked_candidates[:TOP_K]
        run_state.top_pool_ids = [item.resume_id for item in current_top_candidates]
        pool_decisions = self._build_pool_decisions(
            round_no=round_no,
            top_candidates=current_top_candidates,
            previous_top_ids=previous_top_ids,
        )
        tracer.write_jsonl(
            f"rounds/round_{round_no:02d}/scorecards.jsonl",
            [item.model_dump(mode="json") for item in scored_candidates],
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/top_pool_snapshot.json",
            self._slim_top_pool_snapshot(current_top_candidates),
        )
        dropped_candidates = [
            run_state.scorecards_by_resume_id[resume_id]
            for resume_id in previous_top_ids
            if resume_id not in run_state.top_pool_ids and resume_id in run_state.scorecards_by_resume_id
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
                "tui_summary_model": self.settings.effective_tui_summary_model,
                "judge_model": self.settings.effective_judge_model,
                "reasoning_effort": self.settings.reasoning_effort,
                "judge_reasoning_effort": self.settings.effective_judge_reasoning_effort,
                "requirements_enable_thinking": self.settings.requirements_enable_thinking,
                "controller_enable_thinking": self.settings.controller_enable_thinking,
                "reflection_enable_thinking": self.settings.reflection_enable_thinking,
                "structured_repair_model": self.settings.structured_repair_model,
                "structured_repair_reasoning_effort": self.settings.structured_repair_reasoning_effort,
                "judge_openai_base_url": self.settings.judge_openai_base_url,
                "candidate_feedback_enabled": self.settings.candidate_feedback_enabled,
                "candidate_feedback_model": self.settings.candidate_feedback_model,
                "candidate_feedback_reasoning_effort": self.settings.candidate_feedback_reasoning_effort,
                "target_company_enabled": self.settings.target_company_enabled,
                "company_discovery_enabled": self.settings.company_discovery_enabled,
                "company_discovery_provider": self.settings.company_discovery_provider,
                "has_bocha_key": bool(self.settings.bocha_api_key),
                "company_discovery_model": self.settings.company_discovery_model,
                "company_discovery_reasoning_effort": self.settings.company_discovery_reasoning_effort,
                "company_discovery_max_search_calls": self.settings.company_discovery_max_search_calls,
                "company_discovery_max_results_per_query": self.settings.company_discovery_max_results_per_query,
                "company_discovery_max_open_pages": self.settings.company_discovery_max_open_pages,
                "company_discovery_timeout_seconds": self.settings.company_discovery_timeout_seconds,
                "company_discovery_accepted_company_limit": self.settings.company_discovery_accepted_company_limit,
                "company_discovery_min_confidence": self.settings.company_discovery_min_confidence,
                "min_rounds": self.settings.min_rounds,
                "max_rounds": self.settings.max_rounds,
                "scoring_max_concurrency": self.settings.scoring_max_concurrency,
                "search_max_pages_per_round": self.settings.search_max_pages_per_round,
                "search_max_attempts_per_round": self.settings.search_max_attempts_per_round,
                "search_no_progress_limit": self.settings.search_no_progress_limit,
                "llm_cache_dir": self.settings.llm_cache_dir,
                "openai_prompt_cache_enabled": self.settings.openai_prompt_cache_enabled,
                "openai_prompt_cache_retention": self.settings.openai_prompt_cache_retention,
                "mock_cts": self.settings.mock_cts,
                "enable_eval": self.settings.enable_eval,
                "enable_reflection": self.settings.enable_reflection,
                "wandb_entity": self.settings.wandb_entity,
                "wandb_project": self.settings.wandb_project,
                "weave_entity": self.settings.effective_weave_entity,
                "weave_project": self.settings.weave_project,
            },
            "configured_providers": self._configured_providers(),
            "selected_openapi_file": str(self.settings.spec_file),
            "prompt_hashes": self.prompts.prompt_hashes(),
            "prompt_files": self.prompts.prompt_files(),
        }

    def _prompt_cache_key(self, *, stage: str, model_id: str, input_hash: str) -> str | None:
        if not self.settings.openai_prompt_cache_enabled:
            return None
        return f"{stage}:{model_id}:{input_hash}"

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
        user_payload: dict[str, Any],
        user_prompt_text: str,
        input_artifact_refs: list[str],
        output_artifact_refs: list[str],
        started_at: str,
        latency_ms: int | None,
        status: Literal["succeeded", "failed"],
        retries: int,
        output_retries: int,
        structured_output: dict[str, Any] | None = None,
        error_message: str | None = None,
        round_no: int | None = None,
        resume_id: str | None = None,
        branch_id: str | None = None,
        validator_retry_count: int = 0,
        validator_retry_reasons: list[str] | None = None,
        cache_hit: bool = False,
        cache_key: str | None = None,
        cache_lookup_latency_ms: int | None = None,
        prompt_cache_key: str | None = None,
        prompt_cache_retention: str | None = None,
        cached_input_tokens: int | None = None,
        repair_attempt_count: int = 0,
        repair_succeeded: bool = False,
        repair_model: str | None = None,
        repair_reason: str | None = None,
        full_retry_count: int = 0,
    ) -> LLMCallSnapshot:
        prompt = self.prompts.load(prompt_name)
        output_hash = json_sha256(structured_output) if structured_output is not None else None
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
            retries=retries,
            output_retries=output_retries,
            started_at=started_at,
            latency_ms=latency_ms,
            status=status,
            input_artifact_refs=input_artifact_refs,
            output_artifact_refs=output_artifact_refs,
            input_payload_sha256=text_sha256(user_prompt_text),
            structured_output_sha256=output_hash,
            prompt_chars=len(prompt.content),
            input_payload_chars=text_char_count(user_prompt_text),
            output_chars=json_char_count(structured_output) if structured_output is not None else 0,
            input_summary=self._llm_input_summary(stage=stage, payload=user_payload),
            output_summary=self._llm_output_summary(stage=stage, output=structured_output),
            error_message=error_message,
            validator_retry_count=validator_retry_count,
            validator_retry_reasons=validator_retry_reasons or [],
            cache_hit=cache_hit,
            cache_key=cache_key,
            cache_lookup_latency_ms=cache_lookup_latency_ms,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
            cached_input_tokens=cached_input_tokens,
            repair_attempt_count=repair_attempt_count,
            repair_succeeded=repair_succeeded,
            repair_model=repair_model,
            repair_reason=repair_reason,
            full_retry_count=full_retry_count,
        )

    def _llm_input_summary(self, *, stage: str, payload: dict[str, Any]) -> str:
        if stage == "requirements":
            truth = payload.get("INPUT_TRUTH", {})
            if isinstance(truth, dict):
                return (
                    f"job_title={truth.get('job_title', '')!r}; "
                    f"jd_chars={len(str(truth.get('jd', '')))}; "
                    f"notes_chars={len(str(truth.get('notes', '')))}"
                )
        if stage == "controller":
            context = payload.get("CONTROLLER_CONTEXT", {})
            if isinstance(context, dict):
                top_pool = context.get("current_top_pool") or []
                stop_guidance = context.get("stop_guidance") or {}
                return (
                    f"round={context.get('round_no')}; "
                    f"top_pool={len(top_pool) if isinstance(top_pool, list) else 0}; "
                    f"can_stop={stop_guidance.get('can_stop') if isinstance(stop_guidance, dict) else None}"
                )
        if stage == "reflection":
            context = payload.get("REFLECTION_CONTEXT", {})
            if isinstance(context, dict):
                observation = context.get("search_observation") or {}
                top_candidates = context.get("top_candidates") or []
                return (
                    f"round={context.get('round_no')}; "
                    f"unique_new={observation.get('unique_new_count') if isinstance(observation, dict) else None}; "
                    f"top_candidates={len(top_candidates) if isinstance(top_candidates, list) else 0}"
                )
        if stage == "finalize":
            context = payload.get("FINALIZATION_CONTEXT", {})
            if isinstance(context, dict):
                candidates = context.get("ranked_candidates") or []
                return (
                    f"rounds={context.get('rounds_executed')}; "
                    f"stop_reason={context.get('stop_reason')}; "
                    f"ranked_candidates={len(candidates) if isinstance(candidates, list) else 0}"
                )
        return f"{stage} input payload"

    def _llm_output_summary(self, *, stage: str, output: dict[str, Any] | None) -> str | None:
        if output is None:
            return None
        if stage == "requirements":
            return f"role_title={output.get('role_title', '')!r}; jd_terms={len(output.get('jd_query_terms') or [])}"
        if stage == "controller":
            action = output.get("action")
            if action == "search_cts":
                return f"action=search_cts; query_terms={len(output.get('proposed_query_terms') or [])}"
            return f"action=stop; stop_reason={output.get('stop_reason')}"
        if stage == "reflection":
            summary = str(output.get("reflection_summary", ""))
            rationale = str(output.get("reflection_rationale", ""))
            return (
                f"suggest_stop={output.get('suggest_stop')}; "
                f"{self._preview_text(summary, limit=100)}; "
                f"{self._preview_text(rationale, limit=140)}"
            )
        if stage == "finalize":
            return f"candidates={len(output.get('candidates') or [])}; {self._preview_text(str(output.get('summary', '')), limit=140)}"
        return f"{stage} output payload"

    def _input_text_refs(self, *, role_title: str, jd: str, notes: str) -> dict[str, object]:
        return {
            "input_truth_ref": "input_truth.json",
            "role_title": role_title,
            "jd_sha256": hashlib.sha256(jd.encode("utf-8")).hexdigest(),
            "notes_sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
            "jd_chars": len(jd),
            "notes_chars": len(notes),
        }

    def _slim_controller_context(self, context: ControllerContext) -> dict[str, object]:
        digest = context.requirement_digest or build_requirement_digest(context.requirement_sheet)
        return {
            "schema_version": "v0.2.3a",
            "context_type": "controller",
            "round_no": context.round_no,
            "input": self._input_text_refs(
                role_title=context.requirement_sheet.role_title,
                jd=context.full_jd,
                notes=context.full_notes,
            ),
            "refs": {
                "requirement_sheet": "requirement_sheet.json",
                "sent_query_history": "sent_query_history.json",
            },
            "budget": {
                "min_rounds": context.min_rounds,
                "max_rounds": context.max_rounds,
                "retrieval_rounds_completed": context.retrieval_rounds_completed,
                "rounds_remaining_after_current": context.rounds_remaining_after_current,
                "budget_used_ratio": context.budget_used_ratio,
                "near_budget_limit": context.near_budget_limit,
                "is_final_allowed_round": context.is_final_allowed_round,
                "target_new": context.target_new,
                "budget_reminder": context.budget_reminder,
            },
            "stop_guidance": context.stop_guidance.model_dump(mode="json"),
            "requirement_digest": digest.model_dump(mode="json"),
            "query_term_pool": [item.model_dump(mode="json") for item in context.query_term_pool],
            "current_top_pool": [item.model_dump(mode="json") for item in context.current_top_pool],
            "latest_search_observation": (
                context.latest_search_observation.model_dump(mode="json")
                if context.latest_search_observation is not None
                else None
            ),
            "previous_reflection": (
                context.previous_reflection.model_dump(mode="json") if context.previous_reflection is not None else None
            ),
            "latest_reflection_keyword_advice": (
                context.latest_reflection_keyword_advice.model_dump(mode="json")
                if context.latest_reflection_keyword_advice is not None
                else None
            ),
            "latest_reflection_filter_advice": (
                context.latest_reflection_filter_advice.model_dump(mode="json")
                if context.latest_reflection_filter_advice is not None
                else None
            ),
            "shortage_history": context.shortage_history,
        }

    def _slim_reflection_context(self, context: ReflectionContext) -> dict[str, object]:
        return {
            "schema_version": "v0.2.3a",
            "context_type": "reflection",
            "round_no": context.round_no,
            "input": self._input_text_refs(
                role_title=context.requirement_sheet.role_title,
                jd=context.full_jd,
                notes=context.full_notes,
            ),
            "refs": {
                "requirement_sheet": "requirement_sheet.json",
                "sent_query_history": "sent_query_history.json",
            },
            "requirement_digest": build_requirement_digest(context.requirement_sheet).model_dump(mode="json"),
            "current_retrieval_plan": context.current_retrieval_plan.model_dump(mode="json"),
            "search_observation": context.search_observation.model_dump(mode="json"),
            "search_attempts": [self._slim_search_attempt(item) for item in context.search_attempts],
            "top_candidates": [
                self._slim_scored_candidate(candidate, rank=index)
                for index, candidate in enumerate(context.top_candidates[:8], start=1)
            ],
            "dropped_candidates": [
                self._slim_scored_candidate(candidate, rank=index)
                for index, candidate in enumerate(context.dropped_candidates[:5], start=1)
            ],
            "scoring_failures": [item.model_dump(mode="json") for item in context.scoring_failures],
            "sent_query_count": len(context.sent_query_history),
        }

    def _slim_finalize_context(self, context: FinalizeContext) -> dict[str, object]:
        return {
            "schema_version": "v0.2.3a",
            "context_type": "finalize",
            "run_id": context.run_id,
            "run_dir": context.run_dir,
            "rounds_executed": context.rounds_executed,
            "stop_reason": context.stop_reason,
            "refs": {
                "requirement_sheet": "requirement_sheet.json",
                "sent_query_history": "sent_query_history.json",
                "scorecards": "rounds/*/scorecards.jsonl",
                "top_pool_snapshots": "rounds/*/top_pool_snapshot.json",
            },
            "requirement_digest": (
                context.requirement_digest.model_dump(mode="json")
                if context.requirement_digest is not None
                else None
            ),
            "top_candidates": [
                self._slim_scored_candidate(candidate, rank=index)
                for index, candidate in enumerate(context.top_candidates, start=1)
            ],
            "sent_query_count": len(context.sent_query_history),
        }

    def _slim_search_attempt(self, attempt: SearchAttempt) -> dict[str, object]:
        payload = attempt.model_dump(mode="json")
        request_payload = payload.pop("request_payload", {})
        payload["request_payload_sha256"] = json_sha256(request_payload)
        payload["request_payload_chars"] = json_char_count(request_payload)
        return payload

    def _scoring_input_ref(self, resume: NormalizedResume) -> dict[str, object]:
        payload = resume.model_dump(mode="json")
        return {
            "resume_id": resume.resume_id,
            "source_round": resume.source_round,
            "normalized_resume_ref": f"resumes/{resume.resume_id}.json",
            "normalized_resume_sha256": json_sha256(payload),
            "normalized_resume_chars": json_char_count(payload),
            "summary": resume.compact_summary(),
        }

    def _slim_scored_candidate(self, candidate: ScoredCandidate, *, rank: int | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "resume_id": candidate.resume_id,
            "fit_bucket": candidate.fit_bucket,
            "overall_score": candidate.overall_score,
            "must_have_match_score": candidate.must_have_match_score,
            "preferred_match_score": candidate.preferred_match_score,
            "risk_score": candidate.risk_score,
            "source_round": candidate.source_round,
            "sort_key": list(scored_candidate_sort_key(candidate)),
            "matched_must_haves": candidate.matched_must_haves[:3],
            "missing_must_haves": candidate.missing_must_haves[:1],
            "matched_preferences": candidate.matched_preferences[:1],
            "negative_signals": candidate.negative_signals[:1],
            "risk_flags": candidate.risk_flags[:1],
            "reasoning_summary": self._preview_text(candidate.reasoning_summary, limit=80),
        }
        if rank is not None:
            payload["rank"] = rank
        return payload

    def _slim_top_pool_snapshot(self, candidates: list[ScoredCandidate]) -> list[dict[str, object]]:
        return [
            {
                "resume_id": candidate.resume_id,
                "rank": index,
                "fit_bucket": candidate.fit_bucket,
                "overall_score": candidate.overall_score,
                "must_have_match_score": candidate.must_have_match_score,
                "risk_score": candidate.risk_score,
                "source_round": candidate.source_round,
                "sort_key": list(scored_candidate_sort_key(candidate)),
            }
            for index, candidate in enumerate(candidates, start=1)
        ]

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

    def _emit_progress(
        self,
        callback: ProgressCallback | None,
        event_type: str,
        message: str,
        *,
        round_no: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        if callback is None:
            return
        callback(ProgressEvent(type=event_type, message=message, round_no=round_no, payload=payload or {}))

    def _build_judge_packet(
        self,
        *,
        tracer: RunTracer,
        run_state: RunState,
        final_result: FinalResult,
        rounds_executed: int,
        stop_reason: str,
        terminal_controller_round: TerminalControllerRound | None,
    ) -> dict[str, object]:
        return {
            "schema_version": "v0.2",
            "run": {
                "run_id": tracer.run_id,
                "rounds_executed": rounds_executed,
                "stop_reason": stop_reason,
                "stop_decision_round": terminal_controller_round.round_no if terminal_controller_round else None,
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
            "terminal_controller_round": (
                terminal_controller_round.model_dump(mode="json") if terminal_controller_round is not None else None
            ),
            "final": {"final_result": final_result.model_dump(mode="json")},
        }

    def _build_search_diagnostics(
        self,
        *,
        tracer: RunTracer,
        run_state: RunState,
        final_result: FinalResult,
        terminal_controller_round: TerminalControllerRound | None,
    ) -> dict[str, object]:
        observations = [
            round_state.search_observation
            for round_state in run_state.round_history
            if round_state.search_observation is not None
        ]
        terminal_controller = None
        if terminal_controller_round is not None:
            terminal_controller = {
                "round_no": terminal_controller_round.round_no,
                "stop_reason": terminal_controller_round.controller_decision.stop_reason,
                "response_to_reflection": terminal_controller_round.controller_decision.response_to_reflection,
                "stop_guidance": terminal_controller_round.stop_guidance.model_dump(mode="json"),
            }
        return {
            "run_id": tracer.run_id,
            "input": {
                "job_title": run_state.input_truth.job_title,
                "jd_sha256": run_state.input_truth.jd_sha256,
                "notes_sha256": run_state.input_truth.notes_sha256,
            },
            "summary": {
                "rounds_executed": final_result.rounds_executed,
                "total_sent_queries": len(run_state.retrieval_state.sent_query_history),
                "total_raw_candidates": sum(item.raw_candidate_count for item in observations),
                "total_unique_new_candidates": sum(item.unique_new_count for item in observations),
                "final_candidate_count": len(final_result.candidates),
                "stop_reason": final_result.stop_reason,
                "terminal_controller": terminal_controller,
            },
            "llm_schema_pressure": self._collect_llm_schema_pressure(tracer.run_dir),
            "rounds": [
                self._build_round_search_diagnostics(run_state=run_state, round_state=round_state)
                for round_state in run_state.round_history
            ],
        }

    def _build_term_surface_audit(
        self,
        *,
        tracer: RunTracer,
        run_state: RunState,
        final_result: FinalResult,
        evaluation_result: EvaluationResult | None,
    ) -> dict[str, object]:
        stats_by_term = self._query_containing_term_stats(run_state)
        positive_final_ids = self._positive_final_candidate_ids(evaluation_result)
        terms = []
        used_term_count = 0
        for item in run_state.retrieval_state.query_term_pool:
            stats = stats_by_term.get(item.term.casefold(), _TermSurfaceStats())
            used_rounds = sorted(stats.used_rounds)
            if used_rounds:
                used_term_count += 1
            final_ids = {
                candidate.resume_id
                for candidate in final_result.candidates
                if candidate.source_round in used_rounds
            }
            terms.append(
                {
                    "term": item.term,
                    "source": item.source,
                    "category": item.category,
                    "retrieval_role": item.retrieval_role,
                    "queryability": item.queryability,
                    "family": item.family,
                    "active": item.active,
                    "used_rounds": used_rounds,
                    "sent_query_count": stats.sent_query_count,
                    "queries_containing_term_raw_candidate_count": stats.raw_candidate_count,
                    "queries_containing_term_unique_new_count": stats.unique_new_count,
                    "queries_containing_term_duplicate_count": stats.duplicate_count,
                    "final_candidate_count_from_used_rounds": len(final_ids),
                    "judge_positive_count_from_used_rounds": (
                        None if evaluation_result is None else len(final_ids & positive_final_ids)
                    ),
                    "human_label": None,
                }
            )
        surfaces, candidate_rules = self._build_surface_audit_rows(
            query_term_pool=run_state.retrieval_state.query_term_pool,
            stats_by_term=stats_by_term,
            positive_final_ids=positive_final_ids,
            final_result=final_result,
            evaluation_result=evaluation_result,
        )
        return {
            "run_id": tracer.run_id,
            "input": {
                "job_title": run_state.input_truth.job_title,
                "jd_sha256": run_state.input_truth.jd_sha256,
                "notes_sha256": run_state.input_truth.notes_sha256,
            },
            "summary": {
                "term_count": len(run_state.retrieval_state.query_term_pool),
                "used_term_count": used_term_count,
                "candidate_surface_rule_count": len(candidate_rules),
                "eval_enabled": evaluation_result is not None,
            },
            "terms": terms,
            "surfaces": surfaces,
            "candidate_surface_rules": candidate_rules,
        }

    def _query_containing_term_stats(self, run_state: RunState) -> dict[str, _TermSurfaceStats]:
        attempt_totals: dict[tuple[object, ...], Counter[str]] = {}
        for round_state in run_state.round_history:
            for attempt in round_state.search_attempts:
                key = self._sent_query_key(
                    round_no=round_state.round_no,
                    query_role=attempt.query_role,
                    city=attempt.city,
                    phase=attempt.phase,
                    batch_no=attempt.batch_no,
                )
                totals = attempt_totals.setdefault(key, Counter())
                totals["raw_candidate_count"] += attempt.raw_candidate_count
                totals["unique_new_count"] += attempt.batch_unique_new_count
                totals["duplicate_count"] += attempt.batch_duplicate_count

        stats_by_term: dict[str, _TermSurfaceStats] = {}
        for record in run_state.retrieval_state.sent_query_history:
            totals = attempt_totals.get(
                self._sent_query_key(
                    round_no=record.round_no,
                    query_role=record.query_role,
                    city=record.city,
                    phase=record.phase,
                    batch_no=record.batch_no,
                ),
                Counter(),
            )
            for term in record.query_terms:
                stats = stats_by_term.setdefault(term.casefold(), _TermSurfaceStats())
                stats.used_rounds.add(record.round_no)
                stats.sent_query_count += 1
                stats.raw_candidate_count += totals["raw_candidate_count"]
                stats.unique_new_count += totals["unique_new_count"]
                stats.duplicate_count += totals["duplicate_count"]
        return stats_by_term

    def _sent_query_key(
        self,
        *,
        round_no: int,
        query_role: QueryRole,
        city: str | None,
        phase: LocationExecutionPhase | None,
        batch_no: int | None,
    ) -> tuple[object, ...]:
        return (round_no, query_role, city, phase, batch_no)

    def _positive_final_candidate_ids(self, evaluation_result: EvaluationResult | None) -> set[str]:
        if evaluation_result is None:
            return set()
        return {
            candidate.resume_id
            for candidate in evaluation_result.final.candidates
            if candidate.judge_score >= 2
        }

    def _build_surface_audit_rows(
        self,
        *,
        query_term_pool: list[QueryTermCandidate],
        stats_by_term: dict[str, _TermSurfaceStats],
        positive_final_ids: set[str],
        final_result: FinalResult,
        evaluation_result: EvaluationResult | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        surfaces: list[dict[str, object]] = []
        candidate_rules: list[dict[str, object]] = []
        for item in query_term_pool:
            rule = self._candidate_surface_rule(item.term)
            if rule is None:
                continue
            stats = stats_by_term.get(item.term.casefold(), _TermSurfaceStats())
            used_rounds = set(stats.used_rounds)
            final_ids = {
                candidate.resume_id
                for candidate in final_result.candidates
                if candidate.source_round in used_rounds
            }
            surfaces.append(
                {
                    "original_term": item.term,
                    "retrieval_term": item.term,
                    "canonical_surface": rule["to_retrieval_term"],
                    "surface_family": rule["surface_family"],
                    "surface_transform": "candidate_alias_not_applied",
                    "surface_transform_reason": rule["reason"],
                    "used_in_query": bool(used_rounds),
                    "cts_raw_hits": stats.raw_candidate_count,
                    "unique_new_count": stats.unique_new_count,
                    "judge_positive_count": (
                        None if evaluation_result is None else len(final_ids & positive_final_ids)
                    ),
                }
            )
            candidate_rules.append(
                {
                    "from_original_term": item.term,
                    "to_retrieval_term": rule["to_retrieval_term"],
                    "domain": "agent_llm",
                    "applies_to": "retrieval_only",
                    "status": "candidate",
                    "evidence_status": "needs_surface_probe",
                }
            )
        return surfaces, candidate_rules

    def _candidate_surface_rule(self, term: str) -> dict[str, str] | None:
        clean = " ".join(term.strip().split())
        if clean.casefold() == "ai agent":
            return {
                "to_retrieval_term": "Agent",
                "surface_family": "role.agent",
                "reason": "Candidate resume surface may use broader Agent more often than AI Agent.",
            }
        compact = clean.replace(" ", "")
        suffixes = ("架构", "系统", "应用", "工程")
        if compact.casefold().startswith("multiagent") and compact.casefold() != "multiagent":
            if any(compact.endswith(suffix) for suffix in suffixes):
                return {
                    "to_retrieval_term": "MultiAgent",
                    "surface_family": "domain.multi_agent",
                    "reason": "Candidate resume surface may omit suffix context around MultiAgent.",
                }
        return None

    def _build_round_search_diagnostics(
        self,
        *,
        run_state: RunState,
        round_state: RoundState,
    ) -> dict[str, object]:
        if round_state.search_observation is None:
            raise ValueError("round_state.search_observation is required for search diagnostics")
        reflection = round_state.reflection_advice
        scored_this_round = [
            candidate
            for candidate in run_state.scorecards_by_resume_id.values()
            if candidate.source_round == round_state.round_no
        ]
        sent_queries = [
            item
            for item in run_state.retrieval_state.sent_query_history
            if item.round_no == round_state.round_no
        ]
        return {
            "round_no": round_state.round_no,
            "query_terms": round_state.retrieval_plan.query_terms,
            "keyword_query": round_state.retrieval_plan.keyword_query,
            "query_term_details": self._query_term_details(
                terms=round_state.retrieval_plan.query_terms,
                query_term_pool=run_state.retrieval_state.query_term_pool,
            ),
            "sent_queries": [
                {
                    "query_role": item.query_role,
                    "city": item.city,
                    "phase": item.phase,
                    "batch_no": item.batch_no,
                    "requested_count": item.requested_count,
                    "query_terms": item.query_terms,
                    "keyword_query": item.keyword_query,
                }
                for item in sent_queries
            ],
            "filters": {
                "projected_cts_filters": round_state.retrieval_plan.projected_cts_filters,
                "runtime_only_constraints": [
                    item.model_dump(mode="json")
                    for item in round_state.retrieval_plan.runtime_only_constraints
                ],
                "adapter_notes": (
                    round_state.constraint_projection_result.adapter_notes
                    if round_state.constraint_projection_result is not None
                    else []
                ),
            },
            "search": {
                "raw_candidate_count": round_state.search_observation.raw_candidate_count,
                "unique_new_count": round_state.search_observation.unique_new_count,
                "shortage_count": round_state.search_observation.shortage_count,
                "duplicate_count": sum(item.batch_duplicate_count for item in round_state.search_attempts),
                "fetch_attempt_count": round_state.search_observation.fetch_attempt_count,
                "exhausted_reason": round_state.search_observation.exhausted_reason,
            },
            "scoring": {
                "newly_scored_count": len(scored_this_round),
                "top_pool_count": len(round_state.top_candidates),
                "fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "fit"),
                "not_fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "not_fit"),
                "top_pool_snapshot": [
                    {
                        "resume_id": item.resume_id,
                        "fit_bucket": item.fit_bucket,
                        "overall_score": item.overall_score,
                        "must_have_match_score": item.must_have_match_score,
                        "risk_score": item.risk_score,
                        "source_round": item.source_round,
                    }
                    for item in round_state.top_candidates
                ],
            },
            "reflection": {
                "suggest_stop": reflection.suggest_stop if reflection is not None else False,
                "suggested_activate_terms": (
                    reflection.keyword_advice.suggested_activate_terms if reflection is not None else []
                ),
                "suggested_drop_terms": (
                    reflection.keyword_advice.suggested_drop_terms if reflection is not None else []
                ),
                "suggested_drop_filter_fields": (
                    reflection.filter_advice.suggested_drop_filter_fields if reflection is not None else []
                ),
                "reflection_summary": reflection.reflection_summary if reflection is not None else None,
            },
            "controller_response_to_previous_reflection": round_state.controller_decision.response_to_reflection,
        }

    def _query_term_details(
        self,
        *,
        terms: list[str],
        query_term_pool: list[QueryTermCandidate],
    ) -> list[dict[str, object]]:
        term_index = {item.term.casefold(): item for item in query_term_pool}
        details: list[dict[str, object]] = []
        for term in terms:
            candidate = term_index.get(term.casefold())
            details.append(
                {
                    "term": term,
                    "source": candidate.source if candidate is not None else None,
                    "category": candidate.category if candidate is not None else None,
                    "retrieval_role": candidate.retrieval_role if candidate is not None else None,
                    "queryability": candidate.queryability if candidate is not None else None,
                    "family": candidate.family if candidate is not None else None,
                }
            )
        return details

    def _collect_llm_schema_pressure(self, run_dir: Path) -> list[dict[str, object]]:
        pressure: list[dict[str, object]] = []
        requirements_call = run_dir / "requirements_call.json"
        pressure.append(self._llm_schema_pressure_item(json.loads(requirements_call.read_text(encoding="utf-8"))))

        rounds_dir = run_dir / "rounds"
        if rounds_dir.exists():
            for round_dir in sorted(rounds_dir.glob("round_*")):
                controller_call = round_dir / "controller_call.json"
                if controller_call.exists():
                    pressure.append(
                        self._llm_schema_pressure_item(json.loads(controller_call.read_text(encoding="utf-8")))
                    )
                scoring_calls = round_dir / "scoring_calls.jsonl"
                if scoring_calls.exists():
                    for line in scoring_calls.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            pressure.append(self._llm_schema_pressure_item(json.loads(line)))
                reflection_call = round_dir / "reflection_call.json"
                if reflection_call.exists():
                    pressure.append(
                        self._llm_schema_pressure_item(json.loads(reflection_call.read_text(encoding="utf-8")))
                    )

        finalizer_call = run_dir / "finalizer_call.json"
        pressure.append(self._llm_schema_pressure_item(json.loads(finalizer_call.read_text(encoding="utf-8"))))
        return pressure

    def _llm_schema_pressure_item(self, snapshot: dict[str, object]) -> dict[str, object]:
        return {
            "stage": snapshot["stage"],
            "call_id": snapshot["call_id"],
            "output_retries": snapshot["output_retries"],
            "validator_retry_count": snapshot.get("validator_retry_count", 0),
            "prompt_chars": snapshot.get("prompt_chars", 0),
            "input_payload_chars": snapshot.get("input_payload_chars", 0),
            "output_chars": snapshot.get("output_chars", 0),
            "input_payload_sha256": snapshot.get("input_payload_sha256"),
            "structured_output_sha256": snapshot.get("structured_output_sha256"),
        }

    def _render_run_summary(
        self,
        *,
        run_state: RunState,
        final_result: FinalResult,
        terminal_controller_round: TerminalControllerRound | None,
    ) -> str:
        lines = [
            "# Run Summary",
            "",
            f"- Run ID: `{final_result.run_id}`",
            f"- Rounds executed: `{final_result.rounds_executed}`",
            f"- Stop reason: `{final_result.stop_reason}`",
            f"- Eval enabled: `{self.settings.enable_eval}`",
            f"- Models: requirements=`{self.settings.requirements_model}`, controller=`{self.settings.controller_model}`, scoring=`{self.settings.scoring_model}`, reflection=`{self.settings.reflection_model}`, finalize=`{self.settings.finalize_model}`",
            "- Final candidates: `final_candidates.json`",
            "",
            "## Prompt Hashes",
            "",
        ]
        if self.settings.enable_eval:
            lines.insert(7, "- Judge packet: `judge_packet.json`")
        if terminal_controller_round is not None:
            lines[5:5] = [
                f"- Stop decision round: `{terminal_controller_round.round_no}`",
                f"- Terminal decision: {terminal_controller_round.controller_decision.decision_rationale}",
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
                f"global_top=`{', '.join(item.resume_id for item in round_state.top_candidates) or 'None'}`, "
                f"reflection=`{reflection.reflection_summary if reflection else 'none'}`"
            )
        lines.extend(["", "## Final Shortlist", ""])
        for candidate in final_result.candidates:
            lines.append(
                f"- Rank {candidate.rank}: `{candidate.resume_id}` score=`{candidate.final_score}` source_round=`{candidate.source_round}`"
            )
        return "\n".join(lines).strip() + "\n"

    def _render_run_finished_summary(
        self,
        *,
        rounds_executed: int,
        terminal_controller_round: TerminalControllerRound | None,
    ) -> str:
        if terminal_controller_round is None:
            return f"Run completed after {rounds_executed} retrieval rounds."
        return (
            f"Run completed after {rounds_executed} retrieval rounds; "
            f"controller stopped in round {terminal_controller_round.round_no}."
        )

    def _next_step_after_round(self, *, round_no: int) -> str:
        if round_no >= self.settings.max_rounds:
            return "finalize (max_rounds reached)"
        return f"continue to controller round {round_no + 1}"

    def _materialize_candidates(
        self,
        *,
        scored_candidates: list[ScoredCandidate],
        candidate_store: dict[str, ResumeCandidate],
    ) -> list[ResumeCandidate]:
        return [candidate_store[item.resume_id] for item in scored_candidates[:TOP_K]]

    def _require_live_llm_config(self) -> None:
        extra_model_specs: list[tuple[str, str | None, str | None]] = []
        if self.settings.candidate_feedback_enabled:
            extra_model_specs.append((self.settings.candidate_feedback_model, None, None))
        if self.settings.company_discovery_enabled and self.settings.bocha_api_key:
            extra_model_specs.append((self.settings.company_discovery_model, None, None))
        try:
            preflight_models(self.settings, extra_model_specs=extra_model_specs)
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
            self.settings.effective_tui_summary_model,
        ):
            provider = model_provider(model_id)
            if provider in seen:
                continue
            providers.append(provider)
            seen.add(provider)
        if self.settings.enable_eval:
            provider = model_provider(self.settings.effective_judge_model)
            if provider not in seen:
                providers.append(provider)
        return providers

    def _format_scoring_failure_message(self, failures: Collection[object]) -> str:
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

    def _build_round_query_states(
        self,
        *,
        round_no: int,
        retrieval_plan,
        title_anchor_term: str,
        query_term_pool: list[QueryTermCandidate],
        sent_query_history: list[SentQueryRecord],
    ) -> list[_LogicalQueryState]:
        query_states = [
            _LogicalQueryState(
                query_role="exploit",
                query_terms=list(retrieval_plan.query_terms),
                keyword_query=retrieval_plan.keyword_query,
            )
        ]
        if len(retrieval_plan.query_terms) == 1:
            return query_states
        if round_no == 1:
            return query_states
        if self._contains_target_company_term(retrieval_plan.query_terms, query_term_pool):
            return query_states
        explore_terms = derive_explore_query_terms(
            retrieval_plan.query_terms,
            title_anchor_term=title_anchor_term,
            query_term_pool=query_term_pool,
            sent_query_history=sent_query_history,
        )
        if explore_terms is None:
            return query_states
        query_states.append(
            _LogicalQueryState(
                query_role="explore",
                query_terms=explore_terms,
                keyword_query=serialize_keyword_query(explore_terms),
            )
        )
        return query_states

    def _contains_target_company_term(
        self,
        terms: list[str],
        query_term_pool: list[QueryTermCandidate],
    ) -> bool:
        term_index = {item.term.casefold(): item for item in query_term_pool}
        return any(
            (candidate := term_index.get(term.casefold())) is not None
            and candidate.retrieval_role == "target_company"
            for term in terms
        )

    async def _execute_location_search_plan(
        self,
        *,
        round_no: int,
        retrieval_plan,
        query_states: list[_LogicalQueryState],
        base_adapter_notes: list[str],
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
    ) -> tuple[list[CTSQuery], list[SentQueryRecord], list[ResumeCandidate], SearchObservation, list[SearchAttempt]]:
        location_plan = retrieval_plan.location_execution_plan
        for query_state in query_states:
            query_state.adapter_notes = list(base_adapter_notes)
            query_state.next_page = 1
            query_state.exhausted = False
            query_state.city_states = (
                {city: _CityExecutionState() for city in location_plan.allowed_locations}
                if location_plan.mode != "none"
                else {}
            )
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
        last_exhausted_reason: str | None = None

        async def collect_candidates_for_query(
            *,
            query_state: _LogicalQueryState,
            requested_count: int,
        ) -> None:
            nonlocal batch_no, raw_candidate_count, last_exhausted_reason
            if requested_count <= 0 or query_state.exhausted:
                return
            local_new_candidates: list[ResumeCandidate] = []
            local_search_attempts: list[SearchAttempt] = []
            local_city_summaries: list[CitySearchSummary] = []
            local_raw_candidate_count = 0

            async def run_dispatches(
                *,
                phase: LocationExecutionPhase,
                city_targets: list[tuple[str, int]],
            ) -> None:
                nonlocal batch_no, local_raw_candidate_count
                if not city_targets:
                    return
                batch_no += 1
                for city, city_requested_count in city_targets:
                    dispatch = await self._run_city_dispatch(
                        round_no=round_no,
                        retrieval_plan=retrieval_plan,
                        query_state=query_state,
                        city=city,
                        phase=phase,
                        batch_no=batch_no,
                        requested_count=city_requested_count,
                        city_state=query_state.city_states[city],
                        seen_resume_ids=global_seen_resume_ids,
                        seen_dedup_keys=global_seen_dedup_keys,
                        tracer=tracer,
                    )
                    cts_queries.append(dispatch["cts_query"])
                    sent_query_records.append(dispatch["sent_query_record"])
                    local_new_candidates.extend(dispatch["new_candidates"])
                    local_search_attempts.extend(dispatch["search_attempts"])
                    local_city_summaries.append(dispatch["city_summary"])
                    local_raw_candidate_count += dispatch["search_observation"].raw_candidate_count
                    query_state.adapter_notes = unique_strings(
                        query_state.adapter_notes + dispatch["search_observation"].adapter_notes
                    )

            if location_plan.mode == "none":
                batch_no += 1
                query = CTSQuery(
                    query_role=query_state.query_role,
                    query_terms=query_state.query_terms,
                    keyword_query=query_state.keyword_query,
                    native_filters=dict(retrieval_plan.projected_cts_filters),
                    page=query_state.next_page,
                    page_size=requested_count,
                    rationale=retrieval_plan.rationale,
                    adapter_notes=list(query_state.adapter_notes),
                )
                sent_query_record = SentQueryRecord(
                    round_no=round_no,
                    query_role=query_state.query_role,
                    batch_no=batch_no,
                    requested_count=requested_count,
                    query_terms=query_state.query_terms,
                    keyword_query=query_state.keyword_query,
                    source_plan_version=retrieval_plan.plan_version,
                    rationale=retrieval_plan.rationale,
                )
                new_candidates, search_observation, search_attempts, _ = await self._execute_search_tool(
                    round_no=round_no,
                    query=query,
                    target_new=requested_count,
                    seen_resume_ids=global_seen_resume_ids,
                    seen_dedup_keys=global_seen_dedup_keys,
                    tracer=tracer,
                    batch_no=batch_no,
                    write_round_artifacts=False,
                )
                cts_queries.append(query)
                sent_query_records.append(sent_query_record)
                local_new_candidates.extend(new_candidates)
                local_search_attempts.extend(search_attempts)
                local_raw_candidate_count += search_observation.raw_candidate_count
                query_state.adapter_notes = unique_strings(query_state.adapter_notes + search_observation.adapter_notes)
                if search_attempts:
                    query_state.next_page = search_attempts[-1].requested_page + 1
                if search_observation.exhausted_reason != "target_satisfied":
                    query_state.exhausted = True
                last_exhausted_reason = search_observation.exhausted_reason or last_exhausted_reason
            else:
                if location_plan.mode == "single":
                    await run_dispatches(
                        phase="balanced",
                        city_targets=[(location_plan.allowed_locations[0], requested_count)],
                    )
                else:
                    if location_plan.mode == "priority_then_fallback":
                        for city in location_plan.priority_order:
                            remaining_gap = requested_count - len(local_new_candidates)
                            if remaining_gap <= 0:
                                break
                            await run_dispatches(
                                phase="priority",
                                city_targets=[(city, remaining_gap)],
                            )
                    while True:
                        remaining_gap = requested_count - len(local_new_candidates)
                        if remaining_gap <= 0:
                            break
                        active_cities = [
                            city
                            for city in location_plan.balanced_order
                            if city in query_state.city_states and not query_state.city_states[city].exhausted
                        ]
                        if not active_cities:
                            break
                        city_targets = allocate_balanced_city_targets(
                            ordered_cities=active_cities,
                            target_new=remaining_gap,
                        )
                        if not city_targets:
                            break
                        await run_dispatches(
                            phase="balanced",
                            city_targets=city_targets,
                        )
                local_exhausted_reason = self._final_exhausted_reason(
                    target_new=requested_count,
                    new_candidate_count=len(local_new_candidates),
                    city_search_summaries=local_city_summaries,
                )
                if local_exhausted_reason != "target_satisfied":
                    query_state.exhausted = True
                last_exhausted_reason = local_exhausted_reason or last_exhausted_reason

            raw_candidate_count += local_raw_candidate_count
            all_new_candidates.extend(local_new_candidates)
            all_search_attempts.extend(local_search_attempts)
            city_search_summaries.extend(local_city_summaries)
            adapter_notes[:] = unique_strings(adapter_notes + query_state.adapter_notes)
            for candidate in local_new_candidates:
                global_seen_resume_ids.add(candidate.resume_id)
                global_seen_dedup_keys.add(candidate.dedup_key)

        initial_targets = (
            [target_new]
            if len(query_states) == 1
            else [target_new // 2, target_new - (target_new // 2)]
        )
        for query_state, requested_count in zip(query_states, initial_targets):
            await collect_candidates_for_query(
                query_state=query_state,
                requested_count=requested_count,
            )
        while len(all_new_candidates) < target_new:
            remaining_gap = target_new - len(all_new_candidates)
            progressed = False
            for query_state in query_states:
                if remaining_gap <= 0:
                    break
                before = len(all_new_candidates)
                await collect_candidates_for_query(
                    query_state=query_state,
                    requested_count=remaining_gap,
                )
                if len(all_new_candidates) > before:
                    progressed = True
                remaining_gap = target_new - len(all_new_candidates)
            if not progressed:
                break

        search_observation = SearchObservation(
            round_no=round_no,
            requested_count=target_new,
            raw_candidate_count=raw_candidate_count,
            unique_new_count=len(all_new_candidates),
            shortage_count=max(0, target_new - len(all_new_candidates)),
            fetch_attempt_count=len(all_search_attempts),
            exhausted_reason=(
                "target_satisfied"
                if len(all_new_candidates) >= target_new
                else (
                    self._final_exhausted_reason(
                        target_new=target_new,
                        new_candidate_count=len(all_new_candidates),
                        city_search_summaries=city_search_summaries,
                    )
                    if city_search_summaries
                    else (last_exhausted_reason or "cts_exhausted")
                )
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

    async def _run_city_dispatch(
        self,
        *,
        round_no: int,
        retrieval_plan,
        query_state: _LogicalQueryState,
        city: str,
        phase: LocationExecutionPhase,
        batch_no: int,
        requested_count: int,
        city_state: _CityExecutionState,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
    ) -> _CityDispatchResult:
        cts_query = CTSQuery(
            query_role=query_state.query_role,
            query_terms=query_state.query_terms,
            keyword_query=query_state.keyword_query,
            native_filters={**retrieval_plan.projected_cts_filters, "location": [city]},
            page=city_state.next_page,
            page_size=requested_count,
            rationale=retrieval_plan.rationale,
            adapter_notes=unique_strings([*query_state.adapter_notes, f"runtime location dispatch: {city}"]),
        )
        sent_query_record = SentQueryRecord(
            round_no=round_no,
            query_role=query_state.query_role,
            city=city,
            phase=phase,
            batch_no=batch_no,
            requested_count=requested_count,
            query_terms=query_state.query_terms,
            keyword_query=query_state.keyword_query,
            source_plan_version=retrieval_plan.plan_version,
            rationale=retrieval_plan.rationale,
        )
        new_candidates, search_observation, search_attempts, _ = await self._execute_search_tool(
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
            query_role=query_state.query_role,
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

    async def _execute_search_tool(
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
            fetch_result = await self._search_once(
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
                    query_role=query.query_role,
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
            payload={
                "round_no": search_observation.round_no,
                "requested_count": search_observation.requested_count,
                "raw_candidate_count": search_observation.raw_candidate_count,
                "unique_new_count": search_observation.unique_new_count,
                "shortage_count": search_observation.shortage_count,
                "fetch_attempt_count": search_observation.fetch_attempt_count,
                "exhausted_reason": search_observation.exhausted_reason,
            },
        )
        return all_new_candidates, search_observation, attempts, duplicate_count

    async def _search_once(
        self,
        *,
        attempt_query: CTSQuery,
        round_no: int,
        attempt_no: int,
        tracer: RunTracer,
    ) -> CTSFetchResult:
        try:
            return await self.cts_client.search(
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
        new_candidates: list[ResumeCandidate],
        scorecards_by_resume_id: dict[str, ScoredCandidate],
    ) -> list[ResumeCandidate]:
        pool: list[ResumeCandidate] = []
        seen_ids: set[str] = set()
        for candidate in new_candidates:
            if candidate.resume_id in seen_ids or candidate.resume_id in scorecards_by_resume_id:
                continue
            seen_ids.add(candidate.resume_id)
            pool.append(candidate)
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
                        or [f"Ranked into current global top pool with score {candidate.overall_score}."]
                    ),
                    reasons_for_rejection=candidate.weaknesses[:2],
                    compared_against_pool_summary=f"Deterministically ranked #{rank} in the global scored set.",
                )
            )
        for rank, resume_id in enumerate(
            sorted(previous_top_ids - top_ids),
            start=len(top_candidates) + 1,
        ):
            decisions.append(
                PoolDecision(
                    resume_id=resume_id,
                    round_no=round_no,
                    decision="dropped",
                    rank_in_round=rank,
                    reasons_for_selection=[],
                    reasons_for_rejection=["Replaced by higher-ranked resumes in the global scored set."],
                    compared_against_pool_summary="Dropped from the global top pool after this round's new scores landed.",
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
        newly_scored_count: int,
        pool_decisions: list[PoolDecision],
        top_candidates: list[ScoredCandidate],
        dropped_candidates: list[ScoredCandidate],
        reflection: ReflectionAdvice | None,
        next_step: str,
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
                    f"{city_summary.query_role} "
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
                f"- Newly scored this round: `{newly_scored_count}`",
                f"- Current global top pool: {', '.join(candidate.resume_id for candidate in top_candidates) or 'None'}",
                f"- Newly selected: {', '.join(selected) or 'None'}",
                f"- Retained: {', '.join(retained) or 'None'}",
                f"- Dropped from global top pool: {', '.join(dropped) or 'None'}",
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
                    f"- Reflection rationale: {reflection.reflection_rationale or 'None'}",
                    f"- Reflection decision: `{'stop' if reflection.suggest_stop else 'continue'}`",
                ]
            )
            if reflection.suggested_stop_reason:
                lines.append(f"- Stop reason: {reflection.suggested_stop_reason}")
        else:
            lines.extend(["", "## Reflection", "", "- Reflection summary: Reflection disabled."])
        lines.extend(["", f"- Next step: `{next_step}`"])
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
