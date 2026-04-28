from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from seektalent.candidate_feedback import (
    FeedbackCandidateExpression,
    build_term_family_id,
    extract_feedback_candidate_expressions,
    select_feedback_seed_resumes,
)
from seektalent.candidate_feedback.proposal_runtime import (
    PRFProposalOutput,
    build_prf_proposal_bundle,
    build_prf_span_extractor,
)
from seektalent.candidate_feedback.span_extractors import LegacyRegexSpanExtractor
from seektalent.candidate_feedback.span_models import PhraseFamily, ProposalMetadata
from seektalent.candidate_feedback.policy import (
    MAX_NEGATIVE_SUPPORT_RATE,
    MIN_PRF_SEED_COUNT,
    PRF_POLICY_VERSION,
    PRFGateInput,
    PRFPolicyDecision,
    build_prf_policy_decision,
)
from seektalent.company_discovery import (
    CompanyDiscoveryService,
)
from seektalent.config import AppSettings
from seektalent.controller import ReActController
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.core.retrieval.service import RetrievalService
from seektalent.evaluation import TOP_K, AsyncJudgeLimiter, EvaluationResult, evaluate_run
from seektalent.finalize.finalizer import Finalizer
from seektalent.llm import model_provider, preflight_models
from seektalent.models import (
    CTSQuery,
    ControllerContext,
    ControllerDecision,
    FinalizeContext,
    FinalResult,
    LocationExecutionPhase,
    NormalizedResume,
    PoolDecision,
    QueryOutcomeThresholds,
    QueryResumeHit,
    ReflectionAdvice,
    ResumeCandidate,
    QueryTermCandidate,
    QueryRole,
    ReflectionContext,
    RuntimeConstraint,
    RoundState,
    RunState,
    ScoredCandidate,
    SearchControllerDecision,
    SearchAttempt,
    SearchObservation,
    SecondLaneDecision,
    SentQueryRecord,
    StopGuidance,
    StopControllerDecision,
    TerminalControllerRound,
    scored_candidate_sort_key,
    unique_strings,
)
from seektalent.normalization import normalize_resume
from seektalent.prompting import PromptRegistry
from seektalent.progress import ProgressCallback, ProgressEvent
from seektalent.providers import get_provider_adapter
from seektalent.providers.cts.filter_projection import (
    project_constraints_to_cts,
)
from seektalent.reflection.critic import ReflectionCritic
from seektalent.requirements import (
    RequirementExtractor,
    build_requirement_digest,
)
from seektalent.retrieval import (
    build_location_execution_plan,
    build_round_retrieval_plan,
)
from seektalent.retrieval.query_identity import build_job_intent_fingerprint
from seektalent.resume_quality import ResumeQualityCommenter
from seektalent.runtime.context_views import top_candidates
from seektalent.runtime import company_discovery_runtime
from seektalent.runtime import controller_runtime
from seektalent.runtime import finalize_runtime
from seektalent.runtime import post_finalize_runtime
from seektalent.runtime import reflection_runtime
from seektalent.runtime import round_decision_runtime
from seektalent.runtime import rescue_execution_runtime
from seektalent.runtime.controller_context import build_controller_context
from seektalent.runtime.finalize_context import build_finalize_context
from seektalent.runtime.requirements_runtime import build_run_state as build_requirements_run_state
from seektalent.runtime.runtime_diagnostics import (
    build_replay_snapshot as build_replay_snapshot_direct,
    build_judge_packet as build_judge_packet_direct,
    build_search_diagnostics as build_search_diagnostics_direct,
    build_term_surface_audit as build_term_surface_audit_direct,
    collect_llm_schema_pressure as collect_llm_schema_pressure_direct,
    _build_round_search_diagnostics as build_round_search_diagnostics_direct,
    _candidate_surface_rule as candidate_surface_rule_direct,
    _llm_schema_pressure_item as llm_schema_pressure_item_direct,
    _positive_final_candidate_ids as positive_final_candidate_ids_direct,
    _query_containing_term_stats as query_containing_term_stats_direct,
    _query_term_details as query_term_details_direct,
    _reflection_advice_application as reflection_advice_application_direct,
    _reflection_advice_application_for_decision as reflection_advice_application_for_decision_direct,
    _round_audit_labels as round_audit_labels_direct,
    _sent_query_key as sent_query_key_direct,
    _build_surface_audit_rows as build_surface_audit_rows_direct,
    slim_controller_context as slim_controller_context_payload,
    slim_finalize_context as slim_finalize_context_payload,
    slim_reflection_context as slim_reflection_context_payload,
    slim_scored_candidate as slim_scored_candidate_payload,
    slim_search_attempt as slim_search_attempt_payload,
    slim_top_pool_snapshot as slim_top_pool_snapshot_payload,
)
from seektalent.runtime.runtime_reports import (
    render_round_review as render_round_review_direct,
    render_run_finished_summary as render_run_finished_summary_direct,
    render_run_summary as render_run_summary_direct,
)
from seektalent.runtime.retrieval_runtime import (
    LogicalQueryState,
    RetrievalRuntime,
    build_logical_query_state,
)
from seektalent.runtime.rescue_router import RescueDecision, RescueInputs, choose_rescue_lane
from seektalent.runtime.second_lane_runtime import build_second_lane_decision
from seektalent.runtime.scoring_context import build_scoring_context
from seektalent.runtime.scoring_runtime import score_round as score_round_direct
from seektalent.scoring.scorer import ResumeScorer
from seektalent.tracing import LLMCallSnapshot, ProviderUsageSnapshot, RunTracer
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


def _register_artifact(
    tracer: RunTracer,
    logical_name: str,
    relative_path: str,
    *,
    content_type: str,
    schema_version: str | None = "v1",
) -> str:
    tracer.session.register_path(
        logical_name,
        relative_path,
        content_type=content_type,
        schema_version=schema_version,
    )
    return logical_name


def _round_artifact(
    tracer: RunTracer,
    *,
    round_no: int,
    subsystem: str,
    name: str,
    extension: str = "json",
    content_type: str = "application/json",
) -> str:
    return _register_artifact(
        tracer,
        f"round.{round_no:02d}.{subsystem}.{name}",
        f"rounds/{round_no:02d}/{subsystem}/{name}.{extension}",
        content_type=content_type,
        schema_version=None if content_type.startswith("text/") else "v1",
    )


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
    terminal_stop_guidance: StopGuidance | None


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


class WorkflowRuntime:
    def __init__(
        self,
        settings: AppSettings,
        *,
        judge_limiter: AsyncJudgeLimiter | None = None,
        eval_remote_logging: bool = True,
    ) -> None:
        self.settings = settings
        self.judge_limiter = judge_limiter
        self.eval_remote_logging = eval_remote_logging
        self.prompts = PromptRegistry(settings.prompt_dir)
        prompt_map = self.prompts.load_many(
            [
                "requirements",
                "controller",
                "scoring",
                "reflection",
                "finalize",
                "judge",
                "tui_summary",
                "company_discovery_plan",
                "company_discovery_extract",
                "company_discovery_reduce",
                "repair_requirements",
                "repair_controller",
                "repair_reflection",
            ]
        )
        self.requirement_extractor = RequirementExtractor(
            settings,
            prompt_map["requirements"],
            repair_prompt=prompt_map["repair_requirements"],
        )
        self.controller = ReActController(
            settings,
            prompt_map["controller"],
            repair_prompt=prompt_map["repair_controller"],
        )
        self.resume_scorer = ResumeScorer(settings, prompt_map["scoring"])
        self.resume_quality_commenter = ResumeQualityCommenter(settings, prompt_map["tui_summary"])
        self.reflection_critic = ReflectionCritic(
            settings,
            prompt_map["reflection"],
            repair_prompt=prompt_map["repair_reflection"],
        )
        self.finalizer = Finalizer(settings, prompt_map["finalize"])
        self.judge_prompt = prompt_map["judge"]
        self.evaluation_runner = evaluate_run
        self.provider = get_provider_adapter(settings)
        retrieval_service = RetrievalService(provider=self.provider)
        self.retrieval_runtime = RetrievalRuntime(
            settings=settings,
            retrieval_service=retrieval_service,
        )
        self.retrieval_service = retrieval_service
        self.company_discovery = CompanyDiscoveryService(
            settings,
            prompts={
                "company_discovery_plan": prompt_map["company_discovery_plan"],
                "company_discovery_extract": prompt_map["company_discovery_extract"],
                "company_discovery_reduce": prompt_map["company_discovery_reduce"],
            },
        )

    @property
    def retrieval_service(self) -> RetrievalService:
        return self._retrieval_service

    @retrieval_service.setter
    def retrieval_service(self, retrieval_service: RetrievalService) -> None:
        self._retrieval_service = retrieval_service
        if hasattr(self, "retrieval_runtime"):
            object.__setattr__(self.retrieval_runtime, "retrieval_service", retrieval_service)

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
        tracer = RunTracer(self.settings.artifacts_path)
        close_status = "completed"
        close_failure_summary: str | None = None
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
            final_result, final_markdown, finalizer_stage_state = await finalize_runtime.run_finalizer_stage(
                settings=self.settings,
                finalizer=self.finalizer,
                finalize_context=finalize_context,
                tracer=tracer,
                progress_callback=progress_callback,
                build_llm_call_snapshot=self._build_llm_call_snapshot,
                emit_llm_event=self._emit_llm_event,
                emit_progress=self._emit_progress,
                slim_finalize_context=self._slim_finalize_context,
                render_final_markdown=self._render_final_markdown,
                run_stage_error=RunStageError,
            )
            finalizer_completed_artifacts = post_finalize_runtime.write_post_finalize_artifacts(
                settings=self.settings,
                tracer=tracer,
                run_state=run_state,
                final_result=final_result,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                terminal_controller_round=terminal_controller_round,
                build_judge_packet=self._build_judge_packet,
                render_run_summary=self._render_run_summary,
                build_search_diagnostics=self._build_search_diagnostics,
            )
            finalize_runtime.finalize_finalizer_stage(
                settings=self.settings,
                finalize_context=finalize_context,
                final_result=final_result,
                finalizer_stage_state=finalizer_stage_state,
                completed_artifact_paths=finalizer_completed_artifacts,
                tracer=tracer,
                progress_callback=progress_callback,
                emit_llm_event=self._emit_llm_event,
                emit_progress=self._emit_progress,
            )
            post_finalize_result = await post_finalize_runtime.run_post_finalize_stage(
                settings=self.settings,
                tracer=tracer,
                progress_callback=progress_callback,
                emit_progress=self._emit_progress,
                run_state=run_state,
                final_result=final_result,
                top_scored=top_scored,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                terminal_controller_round=terminal_controller_round,
                judge_prompt=self.judge_prompt,
                evaluation_runner=self.evaluation_runner,
                judge_limiter=self.judge_limiter,
                eval_remote_logging=self.eval_remote_logging,
                materialize_candidates=self._materialize_candidates,
                build_term_surface_audit=self._build_term_surface_audit,
                render_run_finished_summary=self._render_run_finished_summary,
            )
            return RunArtifacts(
                final_result=final_result,
                final_markdown=final_markdown,
                run_id=tracer.run_id,
                run_dir=tracer.run_dir,
                trace_log_path=tracer.trace_log_path,
                candidate_store=run_state.candidate_store,
                normalized_store=run_state.normalized_store,
                evaluation_result=post_finalize_result.evaluation_result,
                terminal_stop_guidance=(
                    terminal_controller_round.stop_guidance if terminal_controller_round is not None else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            stage = exc.stage if isinstance(exc, RunStageError) else "runtime"
            close_status = "failed"
            close_failure_summary = str(exc)
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
            tracer.close(status=close_status, failure_summary=close_failure_summary)

    async def _build_run_state(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None = None,
    ) -> RunState:
        return await build_requirements_run_state(
            settings=self.settings,
            requirement_extractor=self.requirement_extractor,
            tracer=tracer,
            job_title=job_title,
            jd=jd,
            notes=notes,
            progress_callback=progress_callback,
            emit_llm_event=self._emit_llm_event,
            emit_progress=self._emit_progress,
            build_llm_call_snapshot=self._build_llm_call_snapshot,
            write_aux_llm_call_artifact=self._write_aux_llm_call_artifact,
            run_stage_error_factory=RunStageError,
        )

    def _write_run_preamble(self, *, tracer: RunTracer, job_title: str, jd: str, notes: str) -> None:
        tracer.write_json("runtime.run_config", self._build_public_run_config())
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
        tracer.write_json("input.input_snapshot", input_snapshot)
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
                _round_artifact(
                    tracer,
                    round_no=round_no,
                    subsystem="controller",
                    name="controller_context",
                ),
                self._slim_controller_context(controller_context),
            )
            controller_decision, controller_stage_state = await controller_runtime.run_controller_stage(
                settings=self.settings,
                controller=self.controller,
                controller_context=controller_context,
                round_no=round_no,
                tracer=tracer,
                progress_callback=progress_callback,
                build_llm_call_snapshot=self._build_llm_call_snapshot,
                write_aux_llm_call_artifact=self._write_aux_llm_call_artifact,
                emit_llm_event=self._emit_llm_event,
                emit_progress=self._emit_progress,
                prompt_cache_key=self._prompt_cache_key,
                run_stage_error=RunStageError,
            )
            controller_decision, rescue_decision = await round_decision_runtime.resolve_round_decision(
                run_state=run_state,
                round_no=round_no,
                max_rounds=self.settings.max_rounds,
                controller_context=controller_context,
                controller_decision=controller_decision,
                tracer=tracer,
                progress_callback=progress_callback,
                choose_rescue_decision=self._choose_rescue_decision,
                force_broaden_decision=self._force_broaden_decision,
                force_candidate_feedback_decision=self._force_candidate_feedback_decision,
                continue_after_empty_feedback=self._continue_after_empty_feedback,
                force_company_discovery_decision=self._force_company_discovery_decision,
                select_anchor_only_after_failed_company_discovery=self._select_anchor_only_after_failed_company_discovery,
                force_anchor_only_decision=self._force_anchor_only_decision,
                write_rescue_decision=self._write_rescue_decision,
            )
            controller_runtime.finalize_controller_stage(
                settings=self.settings,
                controller=self.controller,
                controller_decision=controller_decision,
                controller_stage_state=controller_stage_state,
                round_no=round_no,
                tracer=tracer,
                progress_callback=progress_callback,
                build_llm_call_snapshot=self._build_llm_call_snapshot,
                write_aux_llm_call_artifact=self._write_aux_llm_call_artifact,
                emit_llm_event=self._emit_llm_event,
                emit_progress=self._emit_progress,
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
                title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
                query_term_pool=run_state.retrieval_state.query_term_pool,
                projected_provider_filters=projection_result.provider_filters,
                runtime_only_constraints=projection_result.runtime_only_constraints,
                location_execution_plan=location_execution_plan,
                target_new=target_new,
                rationale=controller_decision.decision_rationale,
                allowed_inactive_non_anchor_terms=self._reflection_backed_inactive_terms(
                    run_state.round_history[-1].reflection_advice if run_state.round_history else None
                ),
                allow_anchor_only_query=(
                    controller_context.stop_guidance.quality_gate_status == "broaden_required"
                    or run_state.retrieval_state.anchor_only_broaden_attempted
                ),
            )
            run_state.retrieval_state.current_plan_version = retrieval_plan.plan_version
            run_state.retrieval_state.last_projection_result = projection_result
            tracer.write_json(
                _round_artifact(tracer, round_no=round_no, subsystem="retrieval", name="retrieval_plan"),
                retrieval_plan.model_dump(mode="json"),
            )
            tracer.write_json(
                _round_artifact(
                    tracer,
                    round_no=round_no,
                    subsystem="retrieval",
                    name="constraint_projection_result",
                ),
                projection_result.model_dump(mode="json"),
            )
            job_intent_fingerprint = self._build_job_intent_fingerprint(run_state=run_state)
            prf_v1_5_mode = self.settings.prf_v1_5_mode
            legacy_prf_decision = self._build_prf_policy_decision(run_state=run_state, retrieval_plan=retrieval_plan)
            prf_proposal: PRFProposalOutput | None = None
            prf_decision = legacy_prf_decision
            shadow_prf_v1_5_artifact_ref: str | None = None
            if prf_v1_5_mode != "disabled":
                prf_proposal, proposal_prf_decision = self._build_prf_v1_5_proposal_and_decision(
                    run_state=run_state,
                    retrieval_plan=retrieval_plan,
                )
                tracer.write_json(
                    f"round.{round_no:02d}.retrieval.prf_span_candidates",
                    [item.model_dump(mode="json") for item in prf_proposal.candidate_spans],
                )
                tracer.write_json(
                    f"round.{round_no:02d}.retrieval.prf_expression_families",
                    [item.model_dump(mode="json") for item in prf_proposal.phrase_families],
                )
                tracer.write_json(
                    f"round.{round_no:02d}.retrieval.prf_policy_decision",
                    proposal_prf_decision.model_dump(mode="json"),
                )
                if prf_v1_5_mode == "mainline":
                    prf_decision = proposal_prf_decision
                else:
                    shadow_prf_v1_5_artifact_ref = prf_proposal.artifact_refs.policy_decision_artifact_ref
            else:
                tracer.write_json(
                    f"round.{round_no:02d}.retrieval.prf_policy_decision",
                    legacy_prf_decision.model_dump(mode="json"),
                )
            query_states, second_lane_decision = self._build_round_query_bundle(
                round_no=round_no,
                retrieval_plan=retrieval_plan,
                title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
                query_term_pool=run_state.retrieval_state.query_term_pool,
                sent_query_history=run_state.retrieval_state.sent_query_history,
                prf_decision=prf_decision,
                run_id=tracer.run_id,
                job_intent_fingerprint=job_intent_fingerprint,
                source_plan_version=str(retrieval_plan.plan_version),
                prf_v1_5_mode=prf_v1_5_mode,
                shadow_prf_v1_5_artifact_ref=shadow_prf_v1_5_artifact_ref,
            )
            tracer.write_json(
                f"round.{round_no:02d}.retrieval.second_lane_decision",
                second_lane_decision.model_dump(mode="json"),
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
                retrieval_result = await self.retrieval_runtime.execute_round_search(
                    round_no=round_no,
                    retrieval_plan=retrieval_plan,
                    query_states=query_states,
                    base_adapter_notes=projection_result.adapter_notes,
                    target_new=target_new,
                    seen_resume_ids=set(run_state.seen_resume_ids),
                    seen_dedup_keys=seen_dedup_keys,
                    tracer=tracer,
                    score_for_query_outcome=lambda candidates: self._score_candidates_for_query_outcome(
                        round_no=round_no,
                        candidates=candidates,
                        run_state=run_state,
                        runtime_only_constraints=retrieval_plan.runtime_only_constraints,
                    ),
                    query_outcome_thresholds=QueryOutcomeThresholds(),
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
            cts_queries = retrieval_result.cts_queries
            sent_query_records = retrieval_result.sent_query_records
            new_candidates = retrieval_result.new_candidates
            search_observation = retrieval_result.search_observation
            search_attempts = retrieval_result.search_attempts
            query_resume_hits = retrieval_result.query_resume_hits
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
                "runtime.sent_query_history",
                [item.model_dump(mode="json") for item in run_state.retrieval_state.sent_query_history],
            )
            tracer.write_json(
                _round_artifact(
                    tracer,
                    round_no=round_no,
                    subsystem="retrieval",
                    name="sent_query_records",
                ),
                [item.model_dump(mode="json") for item in sent_query_records],
            )
            tracer.write_json(
                _round_artifact(tracer, round_no=round_no, subsystem="retrieval", name="cts_queries"),
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
            try:
                current_top_candidates, pool_decisions, dropped_candidates = await self._score_round(
                    round_no=round_no,
                    new_candidates=new_candidates,
                    run_state=run_state,
                    tracer=tracer,
                    runtime_only_constraints=retrieval_plan.runtime_only_constraints,
                )
            finally:
                self._write_query_resume_hits(
                    tracer=tracer,
                    round_no=round_no,
                    query_resume_hits=query_resume_hits,
                    scorecards_by_resume_id=run_state.scorecards_by_resume_id,
                )
                replay_snapshot = build_replay_snapshot_direct(
                    run_id=tracer.run_id,
                    round_no=round_no,
                    second_lane_decision=second_lane_decision,
                    search_attempts=search_attempts,
                    query_resume_hits=query_resume_hits,
                    search_observation=search_observation,
                    scoring_model_version=self.settings.scoring_model,
                    query_plan_version=str(retrieval_plan.plan_version),
                    prf_proposal=prf_proposal,
                )
                tracer.write_json(
                    f"round.{round_no:02d}.retrieval.replay_snapshot",
                    replay_snapshot.model_dump(mode="json"),
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
            tui_summary_output_refs: list[str] = []
            if resume_quality_comment:
                tracer.write_json(
                    _round_artifact(tracer, round_no=round_no, subsystem="scoring", name="tui_summary"),
                    {"comment": resume_quality_comment},
                )
                tui_summary_output_refs = [f"round.{round_no:02d}.scoring.tui_summary"]
            self._write_aux_llm_call_artifact(
                tracer=tracer,
                path=f"round.{round_no:02d}.scoring.tui_summary_call",
                call_artifact=getattr(self.resume_quality_commenter, "last_call_artifact", None),
                input_artifact_refs=[
                    f"round.{round_no:02d}.retrieval.retrieval_plan",
                    f"round.{round_no:02d}.scoring.scorecards",
                ],
                output_artifact_refs=tui_summary_output_refs,
                round_no=round_no,
            )
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
            reflection_advice = await reflection_runtime.run_reflection_stage(
                settings=self.settings,
                reflection_critic=self.reflection_critic,
                run_state=run_state,
                round_state=round_state,
                round_no=round_no,
                tracer=tracer,
                progress_callback=progress_callback,
                reflect_round=self._reflect_round,
                slim_reflection_context=self._slim_reflection_context,
                build_llm_call_snapshot=self._build_llm_call_snapshot,
                write_aux_llm_call_artifact=self._write_aux_llm_call_artifact,
                emit_llm_event=self._emit_llm_event,
                emit_progress=self._emit_progress,
                prompt_cache_key=self._prompt_cache_key,
                render_round_review=self._render_round_review,
                next_step=self._next_step_after_round(round_no=round_no),
                newly_scored_count=newly_scored_count,
                pool_decisions=pool_decisions,
                run_stage_error=RunStageError,
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

    def _write_query_resume_hits(
        self,
        *,
        tracer: RunTracer,
        round_no: int,
        query_resume_hits: list[QueryResumeHit],
        scorecards_by_resume_id: dict[str, ScoredCandidate],
    ) -> None:
        for hit in query_resume_hits:
            scorecard = scorecards_by_resume_id.get(hit.resume_id)
            if scorecard is None:
                hit.final_candidate_status = "not_scored"
                continue
            hit.scored_fit_bucket = scorecard.fit_bucket
            hit.overall_score = scorecard.overall_score
            hit.must_have_match_score = scorecard.must_have_match_score
            hit.risk_score = scorecard.risk_score
            hit.off_intent_reason_count = len(scorecard.negative_signals)
            hit.final_candidate_status = "fit" if scorecard.fit_bucket == "fit" else "not_fit"
        tracer.write_json(
            f"round.{round_no:02d}.retrieval.query_resume_hits",
            [item.model_dump(mode="json") for item in query_resume_hits],
        )

    def _logical_query_summaries(self, query_states: list[LogicalQueryState]) -> list[dict[str, object]]:
        return [
            {
                "query_role": query.query_role,
                "lane_type": query.lane_type,
                "query_terms": query.query_terms,
                "keyword_query": query.keyword_query,
            }
            for query in query_states
        ]

    def _executed_query_summaries(self, cts_queries: list[CTSQuery]) -> list[dict[str, object]]:
        summaries: list[dict[str, object]] = []
        seen: set[tuple[str | None, str, tuple[str, ...], str]] = set()
        for query in cts_queries:
            key = (query.lane_type, query.query_role, tuple(query.query_terms), query.keyword_query)
            if key in seen:
                continue
            seen.add(key)
            summaries.append(
                {
                    "query_role": query.query_role,
                    "lane_type": query.lane_type,
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
        return round_decision_runtime.sanitize_controller_decision(
            decision=decision,
            run_state=run_state,
            round_no=round_no,
            max_rounds=self.settings.max_rounds,
        )

    def _reflection_backed_inactive_terms(self, reflection_advice: ReflectionAdvice | None) -> set[str]:
        return round_decision_runtime.reflection_backed_inactive_terms(reflection_advice)

    def _sanitize_premature_max_round_claim(self, text: str, *, round_no: int) -> str:
        return round_decision_runtime.sanitize_premature_max_round_claim(
            text,
            round_no=round_no,
            max_rounds=self.settings.max_rounds,
        )

    def _force_continue_decision(self, *, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
        return round_decision_runtime.force_continue_decision(
            run_state=run_state,
            round_no=round_no,
            reason=reason,
        )

    def _choose_rescue_decision(self, *, run_state: RunState, controller_context: ControllerContext, round_no: int) -> RescueDecision:
        reserve = rescue_execution_runtime.untried_admitted_non_anchor_reserve(run_state.retrieval_state)
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
        return await company_discovery_runtime.continue_after_empty_feedback(
            settings=self.settings,
            company_discovery=self.company_discovery,
            run_state=run_state,
            controller_context=controller_context,
            round_no=round_no,
            tracer=tracer,
            rescue_decision=rescue_decision,
            progress_callback=progress_callback,
            emit_progress=self._emit_progress,
            write_aux_llm_call_artifact=self._write_aux_llm_call_artifact,
            company_discovery_useful=self._company_discovery_useful,
            force_anchor_only_decision=self._force_anchor_only_decision,
        )

    def _company_discovery_skip_reason(self, run_state: RunState, controller_context: ControllerContext) -> str:
        return company_discovery_runtime.company_discovery_skip_reason(
            settings=self.settings,
            run_state=run_state,
            controller_context=controller_context,
            company_discovery_useful=self._company_discovery_useful,
        )

    def _select_anchor_only_after_failed_company_discovery(
        self,
        *,
        run_state: RunState,
        rescue_decision: RescueDecision,
    ) -> RescueDecision:
        return company_discovery_runtime.select_anchor_only_after_failed_company_discovery(
            run_state=run_state,
            rescue_decision=rescue_decision,
        )

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
            _round_artifact(tracer, round_no=round_no, subsystem="controller", name="rescue_decision"),
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
        return rescue_execution_runtime.force_candidate_feedback_decision(
            run_state=run_state,
            round_no=round_no,
            reason=reason,
            tracer=tracer,
            progress_callback=progress_callback,
            emit_progress=self._emit_progress,
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
        return await company_discovery_runtime.force_company_discovery_decision(
            settings=self.settings,
            company_discovery=self.company_discovery,
            run_state=run_state,
            round_no=round_no,
            reason=reason,
            tracer=tracer,
            progress_callback=progress_callback,
            emit_progress=self._emit_progress,
            write_aux_llm_call_artifact=self._write_aux_llm_call_artifact,
        )

    def _force_anchor_only_decision(self, *, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
        return rescue_execution_runtime.force_anchor_only_decision(
            run_state=run_state,
            round_no=round_no,
            reason=reason,
        )

    def _force_broaden_decision(self, *, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
        return rescue_execution_runtime.force_broaden_decision(
            run_state=run_state,
            round_no=round_no,
            reason=reason,
        )

    async def _reflect_round(
        self,
        *,
        context,
        run_state: RunState,
        prompt_cache_key: str | None = None,
        source_user_prompt: str | None = None,
    ) -> ReflectionAdvice:
        if not self.settings.enable_reflection:
            advice = ReflectionAdvice(
                reflection_summary="Reflection disabled.",
                reflection_rationale="Reflection is disabled for this run.",
            )
            return advice
        try:
            if isinstance(self.reflection_critic, ReflectionCritic):
                advice = await self.reflection_critic.reflect(
                    context=context,
                    prompt_cache_key=prompt_cache_key,
                    source_user_prompt=source_user_prompt,
                )
            else:
                advice = await self.reflection_critic.reflect(context=context)
        except Exception as exc:  # noqa: BLE001
            raise RunStageError("reflection", str(exc)) from exc
        run_state.retrieval_state.reflection_keyword_advice_history.append(advice.keyword_advice)
        run_state.retrieval_state.reflection_filter_advice_history.append(advice.filter_advice)
        return advice

    async def _score_round(
        self,
        *,
        round_no: int,
        new_candidates: list[ResumeCandidate],
        run_state: RunState,
        tracer: RunTracer,
        runtime_only_constraints: list[RuntimeConstraint],
    ) -> tuple[list[ScoredCandidate], list[PoolDecision], list[ScoredCandidate]]:
        return await score_round_direct(
            round_no=round_no,
            new_candidates=new_candidates,
            run_state=run_state,
            tracer=tracer,
            runtime_only_constraints=runtime_only_constraints,
            resume_scorer=self.resume_scorer,
            format_scoring_failure_message=self._format_scoring_failure_message,
            run_stage_error=RunStageError,
        )

    async def _score_candidates_for_query_outcome(
        self,
        *,
        round_no: int,
        candidates: list[ResumeCandidate],
        run_state: RunState,
        runtime_only_constraints: list[RuntimeConstraint],
    ) -> list[ScoredCandidate]:
        if not candidates:
            return []

        class _NoOpTracer:
            def emit(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

            def append_jsonl(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

        scoring_contexts = [
            build_scoring_context(
                run_state=run_state,
                round_no=round_no,
                normalized_resume=normalize_resume(candidate),
                runtime_only_constraints=runtime_only_constraints,
            )
            for candidate in candidates
        ]
        scored_candidates, scoring_failures = await self.resume_scorer.score_candidates_parallel(
            contexts=scoring_contexts,
            tracer=_NoOpTracer(),
        )
        if scoring_failures:
            raise RunStageError("scoring", self._format_scoring_failure_message(scoring_failures))
        return scored_candidates

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
                "runtime_mode": self.settings.runtime_mode,
                "runs_dir": self.settings.runs_dir,
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
        return f"assets/prompts/{prompt_name}.md"

    def _write_prompt_snapshots(self, tracer: RunTracer) -> None:
        for prompt in self.prompts.loaded_prompts().values():
            tracer.write_text(
                _register_artifact(
                    tracer,
                    f"assets.prompts.{prompt.name}",
                    self._prompt_snapshot_path(prompt.name),
                    content_type="text/plain",
                    schema_version=None,
                ),
                prompt.content,
            )

    def _write_aux_llm_call_artifact(
        self,
        *,
        tracer: RunTracer,
        path: str,
        call_artifact: dict[str, Any] | None,
        input_artifact_refs: list[str],
        output_artifact_refs: list[str],
        round_no: int | None = None,
    ) -> None:
        if call_artifact is None:
            return
        if path.startswith("round."):
            _, round_text, subsystem, name = path.split(".", 3)
            logical_name = _round_artifact(
                tracer,
                round_no=int(round_text),
                subsystem=subsystem,
                name=name,
            )
        elif path.startswith("assets.prompts."):
            logical_name = _register_artifact(
                tracer,
                path,
                f"assets/prompts/{path.removeprefix('assets.prompts.')}.md",
                content_type="text/plain",
                schema_version=None,
            )
        else:
            filename = Path(path).stem
            logical_name = _register_artifact(
                tracer,
                path,
                path.replace(".", "/") + ".json",
                content_type="application/json",
            )
        filename = logical_name.rsplit(".", 1)[-1]
        tracer.write_json(
            logical_name,
            self._build_llm_call_snapshot(
                stage=str(call_artifact["stage"]),
                call_id=str(call_artifact.get("call_id") or filename),
                model_id=str(call_artifact["model_id"]),
                prompt_name=str(call_artifact.get("prompt_name") or call_artifact["stage"]),
                user_payload=dict(call_artifact.get("user_payload") or {}),
                user_prompt_text=str(call_artifact.get("user_prompt_text") or ""),
                input_artifact_refs=input_artifact_refs,
                output_artifact_refs=output_artifact_refs,
                started_at=str(call_artifact["started_at"]),
                latency_ms=call_artifact.get("latency_ms"),
                status=call_artifact.get("status", "succeeded"),
                retries=int(call_artifact.get("retries", 0)),
                output_retries=int(call_artifact.get("output_retries", 0)),
                structured_output=call_artifact.get("structured_output"),
                error_message=call_artifact.get("error_message"),
                round_no=round_no,
                provider_usage=call_artifact.get("provider_usage"),
            ).model_dump(mode="json"),
        )

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
        structured_output: Any | None = None,
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
        provider_usage: ProviderUsageSnapshot | dict[str, Any] | None = None,
        cached_input_tokens: int | None = None,
        repair_attempt_count: int = 0,
        repair_succeeded: bool = False,
        repair_model: str | None = None,
        repair_reason: str | None = None,
        full_retry_count: int = 0,
    ) -> LLMCallSnapshot:
        prompt = self.prompts.load(prompt_name)
        output_hash = json_sha256(structured_output) if structured_output is not None else None
        provider_usage_snapshot = (
            provider_usage
            if isinstance(provider_usage, ProviderUsageSnapshot)
            else ProviderUsageSnapshot.model_validate(provider_usage)
            if provider_usage is not None
            else None
        )
        if cached_input_tokens is None and provider_usage_snapshot is not None:
            cached_input_tokens = provider_usage_snapshot.cache_read_tokens
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
            provider_usage=provider_usage_snapshot,
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
        if stage == "tui_summary":
            context = payload.get("ROUND_RESUME_QUALITY_CONTEXT", {})
            if isinstance(context, dict):
                candidates = context.get("candidates") or []
                return (
                    f"round={context.get('round_no')}; "
                    f"query_terms={len(context.get('query_terms') or [])}; "
                    f"candidates={len(candidates) if isinstance(candidates, list) else 0}"
                )
        if stage == "company_discovery_plan":
            discovery_input = payload.get("DISCOVERY_INPUT", {})
            if isinstance(discovery_input, dict):
                return (
                    f"role_title={discovery_input.get('role_title', '')!r}; "
                    f"must_have={len(discovery_input.get('must_have_capabilities') or [])}"
                )
        if stage == "company_discovery_extract":
            return (
                f"pages={len(payload.get('PAGE_READS') or [])}; "
                f"search_results={len(payload.get('SEARCH_RESULTS') or [])}"
            )
        if stage == "company_discovery_reduce":
            return (
                f"candidates={len(payload.get('CANDIDATES') or [])}; "
                f"stop_reason={payload.get('STOP_REASON')}"
            )
        if stage == "repair_requirements":
            reason = payload.get("REPAIR_REASON", {})
            if isinstance(reason, dict):
                return f"reason={reason.get('reason')}"
        if stage == "repair_controller":
            reason = payload.get("REPAIR_REASON", {})
            if isinstance(reason, dict):
                return f"reason={reason.get('reason')}"
        if stage == "repair_reflection":
            reason = payload.get("REPAIR_REASON", {})
            if isinstance(reason, dict):
                return f"reason={reason.get('reason')}"
        return f"{stage} input payload"

    def _llm_output_summary(self, *, stage: str, output: Any | None) -> str | None:
        if output is None:
            return None
        if stage == "tui_summary" and isinstance(output, dict):
            return self._preview_text(str(output.get("comment", "")), limit=140)
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
        if stage == "company_discovery_plan" and isinstance(output, dict):
            return f"tasks={len(output.get('tasks') or [])}"
        if stage == "company_discovery_extract" and isinstance(output, dict):
            return f"candidates={len(output.get('candidates') or [])}"
        if stage == "company_discovery_reduce" and isinstance(output, dict):
            return (
                f"inferred_targets={len(output.get('inferred_targets') or [])}; "
                f"stop_reason={output.get('stop_reason')}"
            )
        if stage == "repair_requirements" and isinstance(output, dict):
            return f"role_title={output.get('role_title', '')!r}"
        if stage == "repair_controller" and isinstance(output, dict):
            action = output.get("action")
            return f"action={action}; query_terms={len(output.get('proposed_query_terms') or [])}"
        if stage == "repair_reflection" and isinstance(output, dict):
            return self._preview_text(str(output.get("reflection_summary", "")), limit=140)
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
        return slim_controller_context_payload(context=context, input_text_refs_builder=self._input_text_refs)

    def _slim_reflection_context(self, context: ReflectionContext) -> dict[str, object]:
        return slim_reflection_context_payload(
            context=context,
            input_text_refs_builder=self._input_text_refs,
            slim_search_attempt=self._slim_search_attempt,
            slim_scored_candidate=self._slim_scored_candidate,
        )

    def _slim_finalize_context(self, context: FinalizeContext) -> dict[str, object]:
        return slim_finalize_context_payload(context=context, slim_scored_candidate=self._slim_scored_candidate)

    def _slim_search_attempt(self, attempt: SearchAttempt) -> dict[str, object]:
        return slim_search_attempt_payload(attempt)

    def _slim_scored_candidate(self, candidate: ScoredCandidate, *, rank: int | None = None) -> dict[str, object]:
        return slim_scored_candidate_payload(candidate, rank=rank)

    def _slim_top_pool_snapshot(self, candidates: list[ScoredCandidate]) -> list[dict[str, object]]:
        return slim_top_pool_snapshot_payload(candidates)

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
        return build_judge_packet_direct(
            tracer=tracer,
            run_state=run_state,
            final_result=final_result,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            terminal_controller_round=terminal_controller_round,
            requirements_model=self.settings.requirements_model,
            controller_model=self.settings.controller_model,
            scoring_model=self.settings.scoring_model,
            reflection_model=self.settings.reflection_model,
            finalize_model=self.settings.finalize_model,
            prompt_hashes=self.prompts.prompt_hashes(),
        )

    def _build_search_diagnostics(
        self,
        *,
        tracer: RunTracer,
        run_state: RunState,
        final_result: FinalResult,
        terminal_controller_round: TerminalControllerRound | None,
    ) -> dict[str, object]:
        return build_search_diagnostics_direct(
            tracer=tracer,
            run_state=run_state,
            final_result=final_result,
            terminal_controller_round=terminal_controller_round,
            collect_llm_schema_pressure=self._collect_llm_schema_pressure,
            build_round_search_diagnostics=self._build_round_search_diagnostics,
            reflection_advice_application_for_decision=self._reflection_advice_application_for_decision,
        )

    def _build_term_surface_audit(
        self,
        *,
        tracer: RunTracer,
        run_state: RunState,
        final_result: FinalResult,
        evaluation_result: EvaluationResult | None,
    ) -> dict[str, object]:
        return build_term_surface_audit_direct(
            tracer=tracer,
            run_state=run_state,
            final_result=final_result,
            evaluation_result=evaluation_result,
        )

    def _query_containing_term_stats(self, run_state: RunState) -> dict[str, _TermSurfaceStats]:
        return query_containing_term_stats_direct(run_state)

    def _sent_query_key(
        self,
        *,
        round_no: int,
        query_role: QueryRole,
        city: str | None,
        phase: LocationExecutionPhase | None,
        batch_no: int | None,
    ) -> tuple[object, ...]:
        return sent_query_key_direct(
            round_no=round_no,
            query_role=query_role,
            city=city,
            phase=phase,
            batch_no=batch_no,
        )

    def _positive_final_candidate_ids(self, evaluation_result: EvaluationResult | None) -> set[str]:
        return positive_final_candidate_ids_direct(evaluation_result)

    def _build_surface_audit_rows(
        self,
        *,
        query_term_pool: list[QueryTermCandidate],
        stats_by_term: dict[str, _TermSurfaceStats],
        positive_final_ids: set[str],
        final_result: FinalResult,
        evaluation_result: EvaluationResult | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        return build_surface_audit_rows_direct(
            query_term_pool=query_term_pool,
            stats_by_term=stats_by_term,
            positive_final_ids=positive_final_ids,
            final_result=final_result,
            evaluation_result=evaluation_result,
        )

    def _candidate_surface_rule(self, term: str) -> dict[str, str] | None:
        return candidate_surface_rule_direct(term)

    def _reflection_advice_application_for_decision(
        self,
        *,
        run_state: RunState,
        round_no: int,
        controller_decision: ControllerDecision,
    ) -> dict[str, object]:
        return reflection_advice_application_for_decision_direct(
            run_state=run_state,
            round_no=round_no,
            controller_decision=controller_decision,
        )

    def _reflection_advice_application(self, *, run_state: RunState, round_state: RoundState) -> dict[str, object]:
        return reflection_advice_application_direct(run_state=run_state, round_state=round_state)

    def _build_round_search_diagnostics(
        self,
        *,
        run_state: RunState,
        round_state: RoundState,
    ) -> dict[str, object]:
        return build_round_search_diagnostics_direct(run_state=run_state, round_state=round_state)

    def _round_audit_labels(self, *, run_state: RunState, round_state: RoundState) -> list[str]:
        return round_audit_labels_direct(run_state=run_state, round_state=round_state)

    def _query_term_details(
        self,
        *,
        terms: list[str],
        query_term_pool: list[QueryTermCandidate],
    ) -> list[dict[str, object]]:
        return query_term_details_direct(terms=terms, query_term_pool=query_term_pool)

    def _collect_llm_schema_pressure(self, run_dir: Path) -> list[dict[str, object]]:
        return collect_llm_schema_pressure_direct(run_dir)

    def _llm_schema_pressure_item(self, snapshot: dict[str, object]) -> dict[str, object]:
        return llm_schema_pressure_item_direct(snapshot)

    def _render_run_summary(
        self,
        *,
        run_state: RunState,
        final_result: FinalResult,
        terminal_controller_round: TerminalControllerRound | None,
    ) -> str:
        return render_run_summary_direct(
            settings=self.settings,
            prompt_hashes=self.prompts.prompt_hashes(),
            run_state=run_state,
            final_result=final_result,
            terminal_controller_round=terminal_controller_round,
        )

    def _render_run_finished_summary(
        self,
        *,
        rounds_executed: int,
        terminal_controller_round: TerminalControllerRound | None,
    ) -> str:
        return render_run_finished_summary_direct(
            rounds_executed=rounds_executed,
            terminal_controller_round=terminal_controller_round,
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
        title_anchor_terms: list[str],
        query_term_pool: list[QueryTermCandidate],
        sent_query_history: list[SentQueryRecord],
    ) -> list[LogicalQueryState]:
        query_states, _ = self._build_round_query_bundle(
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            title_anchor_terms=title_anchor_terms,
            query_term_pool=query_term_pool,
            sent_query_history=sent_query_history,
            prf_decision=None,
            run_id="test-run",
            job_intent_fingerprint="test-job-intent",
            source_plan_version=str(retrieval_plan.plan_version),
        )
        return query_states

    def _build_job_intent_fingerprint(self, *, run_state: RunState) -> str:
        requirement_sheet = run_state.requirement_sheet
        return build_job_intent_fingerprint(
            role_title=requirement_sheet.role_title,
            must_haves=requirement_sheet.must_have_capabilities,
            preferred_terms=requirement_sheet.preferences.preferred_query_terms,
            hard_filters=requirement_sheet.hard_constraints.model_dump(mode="json", exclude_none=True),
            location_preferences=requirement_sheet.preferences.preferred_locations,
            normalized_intent_hash=run_state.input_truth.jd_sha256,
            intent_schema_version="typed-second-lane-v1",
        )

    def _build_round_query_bundle(
        self,
        *,
        round_no: int,
        retrieval_plan,
        title_anchor_terms: list[str],
        query_term_pool: list[QueryTermCandidate],
        sent_query_history: list[SentQueryRecord],
        prf_decision: PRFPolicyDecision | None,
        run_id: str,
        job_intent_fingerprint: str,
        source_plan_version: str,
        prf_v1_5_mode: str = "disabled",
        shadow_prf_v1_5_artifact_ref: str | None = None,
    ) -> tuple[list[LogicalQueryState], SecondLaneDecision]:
        del title_anchor_terms
        exploit_query_state = build_logical_query_state(
            run_id=run_id,
            round_no=round_no,
            lane_type="exploit",
            query_terms=list(retrieval_plan.query_terms),
            job_intent_fingerprint=job_intent_fingerprint,
            source_plan_version=source_plan_version,
            provider_filters=retrieval_plan.projected_provider_filters,
            location_execution_plan=retrieval_plan.location_execution_plan,
        )
        query_states = [exploit_query_state]
        if self._contains_target_company_term(retrieval_plan.query_terms, query_term_pool):
            return (
                query_states,
                SecondLaneDecision(
                    round_no=round_no,
                    attempted_prf=False,
                    prf_gate_passed=False,
                    reject_reasons=["target_company_lane_locked"],
                    no_fetch_reason="single_lane_round",
                    prf_policy_version="unavailable",
                ),
            )
        second_lane_decision, second_lane_query_state = build_second_lane_decision(
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            query_term_pool=query_term_pool,
            sent_query_history=sent_query_history,
            prf_decision=prf_decision,
            run_id=run_id,
            job_intent_fingerprint=job_intent_fingerprint,
            source_plan_version=source_plan_version,
            prf_v1_5_mode=prf_v1_5_mode,
            shadow_prf_v1_5_artifact_ref=shadow_prf_v1_5_artifact_ref,
        )
        if second_lane_query_state is not None:
            query_states.append(second_lane_query_state)
        return query_states, second_lane_decision

    def _build_prf_policy_decision(
        self,
        *,
        run_state: RunState,
        retrieval_plan,
    ) -> PRFPolicyDecision:
        seeds, negatives = self._feedback_seed_sets(run_state=run_state)
        expressions = extract_feedback_candidate_expressions(
            seed_resumes=seeds,
            negative_resumes=negatives,
            known_company_entities=self._known_company_entities(run_state=run_state),
            known_product_platforms=set(),
        )
        seed_resume_ids = unique_strings([item.resume_id for item in seeds])
        negative_resume_ids = unique_strings([item.resume_id for item in negatives])
        tried_term_family_ids = unique_strings(
            [
                build_term_family_id(term)
                for record in run_state.retrieval_state.sent_query_history
                for term in record.query_terms
            ]
            + [build_term_family_id(term) for term in retrieval_plan.query_terms]
        )
        tried_query_fingerprints = unique_strings(
            [
                record.query_fingerprint
                for record in run_state.retrieval_state.sent_query_history
                if record.query_fingerprint is not None
            ]
        )
        return build_prf_policy_decision(
            PRFGateInput(
                round_no=retrieval_plan.round_no,
                seed_resume_ids=seed_resume_ids,
                seed_count=len(seed_resume_ids),
                negative_resume_ids=negative_resume_ids,
                candidate_expressions=expressions,
                candidate_expression_count=len(expressions),
                tried_term_family_ids=tried_term_family_ids,
                tried_query_fingerprints=tried_query_fingerprints,
                min_seed_count=MIN_PRF_SEED_COUNT,
                max_negative_support_rate=MAX_NEGATIVE_SUPPORT_RATE,
                policy_version=PRF_POLICY_VERSION,
            )
        )

    def _feedback_seed_sets(self, *, run_state: RunState) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
        seed_candidates = [
            run_state.scorecards_by_resume_id[resume_id]
            for resume_id in run_state.top_pool_ids
            if resume_id in run_state.scorecards_by_resume_id
        ]
        seeds = select_feedback_seed_resumes(seed_candidates)
        negatives = [
            item
            for item in run_state.scorecards_by_resume_id.values()
            if item.fit_bucket == "not_fit" or item.risk_score > 60
        ]
        return seeds, negatives

    def _build_prf_v1_5_proposal_and_decision(
        self,
        *,
        run_state: RunState,
        retrieval_plan,
    ) -> tuple[PRFProposalOutput, PRFPolicyDecision]:
        seeds, negatives = self._feedback_seed_sets(run_state=run_state)
        extractor = build_prf_span_extractor(self.settings, backend=None)
        proposal = build_prf_proposal_bundle(
            positive_seed_resumes=seeds,
            negative_seed_resumes=negatives,
            extractor=extractor,
            metadata=self._build_prf_v1_5_metadata(extractor=extractor),
            round_no=retrieval_plan.round_no,
        )
        seed_resume_ids = unique_strings([item.resume_id for item in seeds])
        negative_resume_ids = unique_strings([item.resume_id for item in negatives])
        expressions = [
            self._family_to_feedback_expression(
                family=family,
                proposal=proposal,
                positive_seed_ids=set(seed_resume_ids),
                negative_seed_ids=set(negative_resume_ids),
            )
            for family in proposal.phrase_families
        ]
        tried_term_family_ids = unique_strings(
            [
                build_term_family_id(term)
                for record in run_state.retrieval_state.sent_query_history
                for term in record.query_terms
            ]
            + [build_term_family_id(term) for term in retrieval_plan.query_terms]
        )
        tried_query_fingerprints = unique_strings(
            [
                record.query_fingerprint
                for record in run_state.retrieval_state.sent_query_history
                if record.query_fingerprint is not None
            ]
        )
        decision = build_prf_policy_decision(
            PRFGateInput(
                round_no=retrieval_plan.round_no,
                seed_resume_ids=seed_resume_ids,
                seed_count=len(seed_resume_ids),
                negative_resume_ids=negative_resume_ids,
                candidate_expressions=expressions,
                candidate_expression_count=len(expressions),
                tried_term_family_ids=tried_term_family_ids,
                tried_query_fingerprints=tried_query_fingerprints,
                min_seed_count=MIN_PRF_SEED_COUNT,
                max_negative_support_rate=MAX_NEGATIVE_SUPPORT_RATE,
                policy_version=PRF_POLICY_VERSION,
            )
        )
        return proposal, decision

    def _build_prf_v1_5_metadata(
        self,
        *,
        extractor: LegacyRegexSpanExtractor | object,
    ) -> ProposalMetadata:
        using_legacy = isinstance(extractor, LegacyRegexSpanExtractor)
        return ProposalMetadata(
            extractor_version="legacy-regex-v1" if using_legacy else "prf-v1.5-model-v1",
            span_model_name="legacy-regex" if using_legacy else self.settings.prf_span_model_name,
            span_model_revision="local" if using_legacy else self.settings.prf_span_model_revision,
            tokenizer_revision="local" if using_legacy else self.settings.prf_span_tokenizer_revision,
            schema_version="legacy-regex-v1" if using_legacy else self.settings.prf_span_schema_version,
            schema_payload={"labels": ["technical_phrase"]},
            thresholds_version="prf-v1.5-thresholds-v1",
            embedding_model_name="none" if using_legacy else self.settings.prf_embedding_model_name,
            embedding_model_revision="none" if using_legacy else self.settings.prf_embedding_model_revision,
            familying_version="familying-v1",
            familying_thresholds={"embedding_similarity": self.settings.prf_familying_embedding_threshold},
            runtime_mode=self.settings.prf_v1_5_mode,
            top_n_candidate_cap=32,
        )

    def _family_to_feedback_expression(
        self,
        *,
        family: PhraseFamily,
        proposal: PRFProposalOutput,
        positive_seed_ids: set[str],
        negative_seed_ids: set[str],
    ) -> FeedbackCandidateExpression:
        spans_by_id = {span.span_id: span for span in proposal.candidate_spans}
        source_spans = [
            spans_by_id[span_id]
            for span_id in family.source_span_ids
            if span_id in spans_by_id
        ]
        source_seed_resume_ids = unique_strings(
            [span.source_resume_id for span in source_spans if span.source_resume_id in positive_seed_ids]
        )
        negative_support_count = len({span.source_resume_id for span in source_spans if span.source_resume_id in negative_seed_ids})
        field_hits: dict[str, int] = {}
        for span in source_spans:
            field_hits[span.source_field] = field_hits.get(span.source_field, 0) + 1
        candidate_term_type = family.candidate_term_type
        if candidate_term_type not in {"company_entity", "product_or_platform", "technical_phrase", "skill"}:
            candidate_term_type = "technical_phrase"
        return FeedbackCandidateExpression(
            term_family_id=family.family_id,
            canonical_expression=family.canonical_surface,
            surface_forms=list(family.surfaces),
            candidate_term_type=candidate_term_type,
            source_seed_resume_ids=source_seed_resume_ids,
            field_hits=field_hits,
            positive_seed_support_count=family.positive_seed_support_count,
            negative_support_count=negative_support_count,
            reject_reasons=list(family.reject_reasons),
        )

    def _known_company_entities(self, *, run_state: RunState) -> set[str]:
        entities = {
            item.term
            for item in run_state.retrieval_state.query_term_pool
            if item.category == "company" or item.retrieval_role == "target_company"
        }
        entities.update(run_state.requirement_sheet.hard_constraints.company_names)
        entities.update(run_state.requirement_sheet.preferences.preferred_companies)
        return entities

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
        query_states: list[LogicalQueryState],
        base_adapter_notes: list[str],
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
        score_for_query_outcome=None,
        query_outcome_thresholds: QueryOutcomeThresholds | None = None,
    ) -> tuple[list[CTSQuery], list[SentQueryRecord], list[ResumeCandidate], SearchObservation, list[SearchAttempt]]:
        result = await self.retrieval_runtime.execute_round_search(
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            query_states=query_states,
            base_adapter_notes=base_adapter_notes,
            target_new=target_new,
            seen_resume_ids=seen_resume_ids,
            seen_dedup_keys=seen_dedup_keys,
            tracer=tracer,
            score_for_query_outcome=score_for_query_outcome,
            query_outcome_thresholds=query_outcome_thresholds,
        )
        return (
            result.cts_queries,
            result.sent_query_records,
            result.new_candidates,
            result.search_observation,
            result.search_attempts,
        )

    async def _execute_search_tool(
        self,
        *,
        round_no: int,
        query: CTSQuery,
        runtime_constraints: list[RuntimeConstraint] | None = None,
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
        city: str | None = None,
        phase: LocationExecutionPhase | None = None,
        batch_no: int | None = None,
        write_round_artifacts: bool = True,
    ) -> tuple[list[ResumeCandidate], SearchObservation, list[SearchAttempt], int]:
        return await self.retrieval_runtime.execute_search_tool(
            round_no=round_no,
            query=query,
            runtime_constraints=runtime_constraints,
            target_new=target_new,
            seen_resume_ids=seen_resume_ids,
            seen_dedup_keys=seen_dedup_keys,
            tracer=tracer,
            city=city,
            phase=phase,
            batch_no=batch_no,
            write_round_artifacts=write_round_artifacts,
        )

    async def _search_once(
        self,
        *,
        attempt_query: CTSQuery,
        runtime_constraints: list[RuntimeConstraint],
        round_no: int,
        attempt_no: int,
        tracer: RunTracer,
    ) -> SearchResult:
        return await self.retrieval_runtime.search_once(
            attempt_query=attempt_query,
            runtime_constraints=runtime_constraints,
            round_no=round_no,
            attempt_no=attempt_no,
            tracer=tracer,
        )

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
        return render_round_review_direct(
            round_no=round_no,
            controller_decision=controller_decision,
            retrieval_plan=retrieval_plan,
            observation=observation,
            newly_scored_count=newly_scored_count,
            pool_decisions=pool_decisions,
            top_candidates=top_candidates,
            dropped_candidates=dropped_candidates,
            reflection=reflection,
            next_step=next_step,
        )

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
