from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from collections import Counter
from collections.abc import Awaitable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Callable, Literal, TypeVar, cast

from seektalent.artifacts import ArtifactSession
from seektalent.candidate_feedback import (
    FeedbackCandidateExpression,
    GROUNDING_VALIDATOR_VERSION,
    LLMPRFExtraction,
    LLMPRFGroundingResult,
    LLMPRFInput,
    LLM_PRF_EXTRACTOR_VERSION,
    LLM_PRF_FAMILYING_VERSION,
    LLM_PRF_OUTPUT_RETRIES,
    build_term_family_id,
    build_llm_prf_artifact_refs,
    build_llm_prf_input,
    feedback_expressions_from_llm_grounding,
    ground_llm_prf_candidates,
    select_llm_prf_negative_resumes,
    select_feedback_seed_resumes,
)
from seektalent.candidate_feedback.llm_prf import (
    build_llm_prf_failure_call_artifact,
    build_llm_prf_success_call_artifact,
    render_llm_prf_prompt,
)
from seektalent.candidate_feedback.policy import (
    MAX_NEGATIVE_SUPPORT_RATE,
    MIN_PRF_SEED_COUNT,
    PRF_POLICY_VERSION,
    PRFGateInput,
    PRFPolicyDecision,
    build_prf_policy_decision,
)
from seektalent.config import AppSettings
from seektalent.corpus.documents import build_jd_document_row
from seektalent.corpus.runtime import (
    ProviderReturnedCandidate,
    record_corpus_provider_results,
    write_corpus_ingest_manifest,
)
from seektalent.corpus.store import DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_ID
from seektalent.core.retrieval.provider_contract import ProviderCapabilities, SearchRequest, SearchResult
from seektalent.core.retrieval.service import RetrievalService
from seektalent.evaluation import TOP_K, AsyncJudgeLimiter, EvaluationResult, evaluate_run
from seektalent.flywheel.outcomes import (
    build_rejected_prf_term_event,
    build_runtime_query_outcome_row,
    build_runtime_query_outcome_rows_from_hits,
    build_term_outcome_rows,
)
from seektalent.flywheel.runtime import (
    build_run_query_rows,
    materialize_flywheel_run_artifacts,
    query_hit_rows_from_hits,
)
from seektalent.flywheel.store import FlywheelStore
from seektalent.llm import preflight_models, resolve_stage_model_config
from seektalent.models import (
    ConstraintProjectionResult,
    ControllerContext,
    ControllerDecision,
    FinalizeContext,
    FinalResult,
    LocationExecutionPhase,
    LogicalQueryOutcome,
    NormalizedResume,
    PoolDecision,
    ProviderQuery,
    ProposedFilterPlan,
    QueryOutcomeThresholds,
    QueryResumeHit,
    ReflectionAdvice,
    ResumeCandidate,
    QueryTermCandidate,
    QueryRole,
    QueryOutcomeClassification,
    ReflectionContext,
    RuntimeConstraint,
    RoundRetrievalPlan,
    RoundState,
    RunState,
    RuntimeFinalizationRevision,
    RuntimeSourceCoverageSummary,
    RequirementSheet,
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
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.progress import ProgressCallback, ProgressEvent
from seektalent.artifacts.lifecycle import RuntimeArtifactLifecycleRef
from seektalent.runtime.candidate_intake import normalize_runtime_candidates, select_identity_top_candidates
from seektalent.reflection.critic import ReflectionCritic
from seektalent.retrieval import (
    build_location_execution_plan,
    build_round_retrieval_plan,
)
from seektalent.retrieval.query_identity import build_job_intent_fingerprint
from seektalent.resumes.snapshots import canonical_resume_snapshot_payload
from seektalent.runtime.context_views import top_candidates
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
    classify_text_llm_failure,
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
from seektalent.runtime.services import build_runtime_services
from seektalent.runtime.source_lanes import (
    RuntimeDetailEnrichmentResult,
    RuntimeSourceLaneEvent,
    RuntimeSourceLaneEventType,
    RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
    RuntimeSourceLaneStatus,
    SourceKind,
    apply_source_lane_result,
    build_runtime_source_plan,
    merge_source_lane_result_updates,
    rebuild_candidate_identities,
    runtime_source_lane_result_from_source_result,
)
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch, build_logical_query_dispatches
from seektalent.retrieval.query_identity import build_term_group_key
from seektalent.runtime.query_identity import (
    apply_post_merge_query_counts,
    assert_novel_term_group_keys,
    logical_outcomes_from_receipts,
    used_term_group_keys,
)
from seektalent.runtime.public_events import RuntimePublicEvent, make_runtime_public_event
from seektalent.runtime.retrieval_runtime import (
    LogicalQueryState,
    RetrievalExecutionResult,
    allocate_initial_lane_targets,
    build_logical_query_state,
)
from seektalent.runtime.rescue_router import RescueDecision, RescueInputs, SkippedRescueLane, choose_rescue_lane
from seektalent.runtime.source_round_dispatch import (
    SourceRoundAdapter,
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
    SourceRoundDispatchResult,
    SourceRoundDispatchStatus,
    dispatch_source_rounds,
)
from seektalent.runtime.source_filters import build_runtime_filter_intents, build_runtime_location_execution_intent
from seektalent.runtime.source_query_intent import (
    RuntimeSourceQueryPolicy,
    build_runtime_source_query_intents,
    source_requested_count,
)
from seektalent.runtime.second_lane_runtime import build_second_lane_decision
from seektalent.runtime.scoring_context import build_scoring_context
from seektalent.runtime.scoring_runtime import score_round as score_round_direct
from seektalent.runtime.stop_reasons import normalize_stop_reason
from seektalent.source_contracts import (
    SourceBudget,
    SourceLaneRequest as ContractSourceLaneRequest,
    SourceLaneResult as ContractSourceLaneResult,
    SourcePlan as ContractSourcePlan,
    SourceRegistry,
)
from seektalent.tracing import LLMCallSnapshot, ProviderUsageSnapshot, RunTracer
from seektalent.tracing import json_char_count, json_sha256, text_char_count, text_sha256


LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")
_PRIOR_CANDIDATE_FINALIZABLE_SOURCE_REASONS = frozenset(
    {
        "source_browser_backend_unavailable",
        "source_browser_extension_disconnected",
        "source_browser_timeout",
    }
)


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
    artifact_lifecycle_ref: RuntimeArtifactLifecycleRef | None = None
    finalization_revision: RuntimeFinalizationRevision | None = None
    source_coverage_summary: RuntimeSourceCoverageSummary | None = None
    run_state: RunState | None = None


@dataclass
class _TermSurfaceStats:
    used_rounds: set[int] = field(default_factory=set)
    sent_query_count: int = 0
    raw_candidate_count: int = 0
    unique_new_count: int = 0
    duplicate_count: int = 0


@dataclass
class _PRFBackendSelection:
    prf_decision: PRFPolicyDecision | None
    prf_probe_proposal_backend: str | None = None
    llm_prf_failure_kind: str | None = None
    llm_prf_input_artifact_ref: str | None = None
    llm_prf_call_artifact_ref: str | None = None
    llm_prf_candidates_artifact_ref: str | None = None
    llm_prf_grounding_artifact_ref: str | None = None
    llm_prf_policy_decision_artifact_ref: str | None = None
    llm_prf_snapshot_metadata: dict[str, object] | None = None


class RunStageError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_message = message


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceRoundContext:
    round_no: int
    retrieval_plan: RoundRetrievalPlan
    proposed_filter_plan: ProposedFilterPlan
    adapter_notes: tuple[str, ...]
    target_new: int
    seen_resume_ids: frozenset[str]
    seen_dedup_keys: frozenset[str]
    run_state: RunState
    source_plan_by_source: Mapping[str, RuntimeSourceLanePlan]
    source_context: Mapping[str, str | int | bool | None] | None
    tracer: RunTracer
    detail_open_claim_ledger: DetailOpenClaimLedger


RuntimeSourceRoundAdapterProvider = Callable[
    ["WorkflowRuntime", RuntimeSourceRoundContext],
    Mapping[str, SourceRoundAdapter],
]
RuntimeSourceQueryPolicyProvider = Callable[
    [tuple[RuntimeSourceLanePlan, ...]],
    Mapping[SourceKind, RuntimeSourceQueryPolicy],
]


RuntimeStartCallback = Callable[[str], None]
RuntimeCheckpointCallback = Callable[[object], None]
RuntimeRoundBoundaryCallback = Callable[[int], RequirementSheet | None]
RuntimeSourceLaneRunner = Callable[[RuntimeSourceLanePlan], Awaitable[RuntimeSourceLaneResult]]
RuntimeSourceLaneRequestRunner = Callable[[RuntimeSourceLaneRequest, object | None], Awaitable[RuntimeSourceLaneResult]]


class _UnavailableRetrievalProvider:
    name = "unconfigured"

    def describe_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_structured_filters=False,
            supports_detail_fetch=False,
            supports_fetch_mode_summary=False,
            supports_fetch_mode_detail=False,
            paging_mode="cursor",
            recommended_max_concurrency=1,
            has_stable_external_id=False,
            has_stable_dedup_key=False,
        )

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
        del request, round_no, trace_id
        raise RuntimeError("retrieval_service_not_configured")


def _source_query_terms_from_logical_queries(
    logical_queries: tuple[LogicalQueryDispatch, ...],
) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for query in logical_queries:
        for value in (*query.query_terms, query.keyword_query):
            text = value.strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            terms.append(text)
    return tuple(terms)


def _source_round_status_from_lane_status(status: str) -> SourceRoundDispatchStatus:
    if status == "completed":
        return "completed"
    if status == "blocked":
        return "blocked"
    if status == "failed":
        return "failed"
    return "partial"


def _planned_public_query_groups(logical_queries: Sequence[LogicalQueryDispatch]) -> list[dict[str, object]]:
    return [
        {
            "queryInstanceId": query.query_instance_id,
            "termGroupKey": query.term_group_key,
            "queryRole": query.query_role,
            "laneType": query.lane_type,
            "queryTerms": list(query.query_terms),
            "keywordQuery": query.keyword_query,
            "lifecycle": "planned",
            "executionStatus": None,
            "attempted": False,
            "rawCandidateCount": 0,
            "uniqueCandidateCount": 0,
            "duplicateCandidateCount": 0,
            "executions": [],
        }
        for query in logical_queries[:2]
    ]


def _executed_public_query_groups(
    query_outcomes: Sequence[LogicalQueryOutcome],
    *,
    source_order: Sequence[SourceKind],
) -> list[dict[str, object]]:
    source_indexes = {source: index for index, source in enumerate(source_order)}
    groups: list[dict[str, object]] = []
    for outcome in query_outcomes[:2]:
        executions: list[dict[str, object]] = []
        for receipt in sorted(
            outcome.receipts,
            key=lambda item: (source_indexes.get(item.source_kind, len(source_indexes)), item.source_kind),
        ):
            execution: dict[str, object] = {
                "sourceKind": receipt.source_kind,
                "status": receipt.status,
                "rawCandidateCount": receipt.raw_candidate_count,
                "uniqueCandidateCount": receipt.unique_candidate_count,
                "duplicateCandidateCount": receipt.duplicate_candidate_count,
            }
            if receipt.safe_reason_code is not None:
                execution["safeReasonCode"] = receipt.safe_reason_code
            executions.append(execution)
        groups.append(
            {
                "queryInstanceId": outcome.query_instance_id,
                "termGroupKey": outcome.term_group_key,
                "queryRole": outcome.query_role,
                "laneType": outcome.lane_type,
                "queryTerms": list(outcome.query_terms),
                "keywordQuery": outcome.keyword_query,
                "lifecycle": "executed",
                "executionStatus": outcome.status,
                "attempted": outcome.attempted,
                "rawCandidateCount": outcome.raw_candidate_count,
                "uniqueCandidateCount": outcome.unique_candidate_count,
                "duplicateCandidateCount": outcome.duplicate_candidate_count,
                "executions": executions,
            }
        )
    return groups


def _round_unique_identity_count(
    *,
    dispatch_result: SourceRoundDispatchResult,
    run_state: RunState,
    pre_round_seen_resume_ids: frozenset[str],
) -> int:
    touched_identity_ids = {
        identity_id
        for candidate in dispatch_result.candidates
        if (identity_id := run_state.candidate_identity_by_resume_id.get(candidate.resume_id)) is not None
    }
    new_identity_ids = {
        identity_id
        for identity_id in touched_identity_ids
        if not (set(run_state.candidate_identities[identity_id].resume_ids) & pre_round_seen_resume_ids)
    }
    return len(new_identity_ids)


class WorkflowRuntime:
    def __init__(
        self,
        settings: AppSettings,
        *,
        source_registry: SourceRegistry | None = None,
        source_lane_request_runner: RuntimeSourceLaneRequestRunner | None = None,
        source_round_adapter_provider: RuntimeSourceRoundAdapterProvider | None = None,
        source_query_policy_provider: RuntimeSourceQueryPolicyProvider | None = None,
        retrieval_service: RetrievalService | None = None,
        judge_limiter: AsyncJudgeLimiter | None = None,
        eval_remote_logging: bool = True,
    ) -> None:
        self.settings = settings
        self.source_registry = source_registry
        self.source_lane_request_runner = source_lane_request_runner
        self.source_round_adapter_provider = source_round_adapter_provider
        self.source_query_policy_provider = source_query_policy_provider
        self.judge_limiter = judge_limiter
        self.eval_remote_logging = eval_remote_logging
        self.prompts = PromptRegistry(settings.prompt_dir)
        prompt_map = self.prompts.load_many(
            [
                "requirements",
                "controller",
                "scoring",
                "reflection",
                "judge",
                "tui_summary",
                "prf_probe_phrase_proposal",
                "repair_requirements",
                "repair_controller",
                "repair_reflection",
            ]
        )
        self.judge_prompt = prompt_map["judge"]
        self.evaluation_runner = evaluate_run
        retrieval_service = retrieval_service or RetrievalService(provider=_UnavailableRetrievalProvider())
        self.services = build_runtime_services(
            settings=settings,
            prompt_map=prompt_map,
            retrieval_service=retrieval_service,
        )
        self.requirement_extractor = self.services.requirement_extractor
        self.controller = self.services.controller
        self.resume_scorer = self.services.resume_scorer
        self.resume_quality_commenter = self.services.resume_quality_commenter
        self.reflection_critic = self.services.reflection_critic
        self.llm_prf_extractor = self.services.llm_prf_extractor
        self.retrieval_runtime = self.services.retrieval_runtime
        self.retrieval_service = self.services.retrieval_service
        self.flywheel_store: FlywheelStore | None = (
            FlywheelStore(settings.flywheel_path) if settings.enable_flywheel else None
        )
        self._active_flywheel_task_id: str | None = None
        self._active_flywheel_run_id: str | None = None
        self.corpus_store = self.services.corpus_store
        self._active_corpus_session: ArtifactSession | None = None

    @property
    def retrieval_service(self) -> RetrievalService:
        return self._retrieval_service

    @retrieval_service.setter
    def retrieval_service(self, retrieval_service: RetrievalService) -> None:
        self._retrieval_service = retrieval_service
        provider = getattr(retrieval_service, "provider", None)
        if provider is not None:
            self.provider = provider
        if hasattr(self, "services"):
            self.services.retrieval_service = retrieval_service
        if hasattr(self, "retrieval_runtime"):
            object.__setattr__(self.retrieval_runtime, "retrieval_service", retrieval_service)

    def run(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        source_kinds: Sequence[str] | None = None,
        source_context: Mapping[str, str | int | bool | None] | None = None,
        progress_callback: ProgressCallback | None = None,
        runtime_start_callback: RuntimeStartCallback | None = None,
        runtime_checkpoint_callback: RuntimeCheckpointCallback | None = None,
        runtime_round_boundary_callback: RuntimeRoundBoundaryCallback | None = None,
        requirement_cache_scope: str | None = None,
        approved_requirement_sheet: RequirementSheet | None = None,
    ) -> RunArtifacts:
        return asyncio.run(
            self.run_async(
                job_title=job_title,
                jd=jd,
                notes=notes,
                source_kinds=source_kinds,
                source_context=source_context,
                progress_callback=progress_callback,
                runtime_start_callback=runtime_start_callback,
                runtime_checkpoint_callback=runtime_checkpoint_callback,
                runtime_round_boundary_callback=runtime_round_boundary_callback,
                requirement_cache_scope=requirement_cache_scope,
                approved_requirement_sheet=approved_requirement_sheet,
            )
        )

    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd: str,
        notes: str,
        progress_callback: ProgressCallback | None = None,
        requirement_cache_scope: str | None = None,
    ) -> RequirementSheet:
        return asyncio.run(
            self.extract_requirements_async(
                job_title=job_title,
                jd=jd,
                notes=notes,
                progress_callback=progress_callback,
                requirement_cache_scope=requirement_cache_scope,
            )
        )

    def run_source_lane(
        self,
        request: RuntimeSourceLaneRequest,
        *,
        source_client: object | None = None,
    ) -> RuntimeSourceLaneResult:
        return asyncio.run(
            self.run_source_lane_async(
                request,
                source_client=source_client,
            )
        )

    async def run_source_lane_async(
        self,
        request: RuntimeSourceLaneRequest,
        *,
        source_client: object | None = None,
    ) -> RuntimeSourceLaneResult:
        if self.source_lane_request_runner is None:
            raise ValueError(f"Unsupported source lane request source: {request.source}")
        return await self.source_lane_request_runner(request, source_client)

    async def run_source_round_for_testing(
        self,
        *,
        run_state: RunState,
        source_kinds: Sequence[str] | None,
        logical_queries: tuple[LogicalQueryDispatch, ...],
        round_no: int,
    ) -> SourceRoundDispatchResult:
        return await self._execute_registered_source_round(
            run_state=run_state,
            source_kinds=source_kinds,
            logical_queries=logical_queries,
            round_no=round_no,
            runtime_run_id=f"test-source-round:{round_no}",
        )

    async def _execute_registered_source_round(
        self,
        *,
        run_state: RunState,
        source_kinds: Sequence[str] | None,
        logical_queries: tuple[LogicalQueryDispatch, ...],
        round_no: int,
        runtime_run_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> SourceRoundDispatchResult:
        if self.source_registry is None:
            raise RunStageError("source_lanes", "source_registry_required")

        selected = self.source_registry.enabled_sources(source_kinds)
        source_plan = self.source_registry.build_plans(
            source_ids=tuple(source.source_id for source in selected),
            runtime_run_id=runtime_run_id,
        )
        source_by_id = {source.source_id: source for source in selected}
        source_order = {plan.source_id: index for index, plan in enumerate(source_plan)}
        query_terms = _source_query_terms_from_logical_queries(logical_queries)
        tasks: dict[str, asyncio.Task[ContractSourceLaneResult]] = {}
        async with asyncio.TaskGroup() as task_group:
            for plan in source_plan:
                source = source_by_id[plan.source_id]
                tasks[plan.source_id] = task_group.create_task(
                    source.run_card_lane(
                        self._registered_source_lane_request(
                            run_state=run_state,
                            plan=plan,
                            round_no=round_no,
                            query_terms=query_terms,
                            progress_callback=progress_callback,
                        )
                    )
                )

        source_results: list[SourceRoundAdapterResult] = []
        candidates: list[ResumeCandidate] = []
        raw_candidate_count = 0
        for plan in source_plan:
            source_result = tasks[plan.source_id].result()
            runtime_result = runtime_source_lane_result_from_source_result(source_result)
            apply_source_lane_result(
                run_state=run_state,
                result=runtime_result,
                source_order=source_order,
            )
            result_candidates = tuple(source_result.candidate_store_updates.values())
            raw_count = int(source_result.raw_candidate_count or len(result_candidates))
            candidates.extend(result_candidates)
            raw_candidate_count += raw_count
            source_results.append(
                SourceRoundAdapterResult(
                    source=source_result.source_id,
                    status=_source_round_status_from_lane_status(source_result.status),
                    candidates=result_candidates,
                    raw_candidate_count=raw_count,
                    safe_reason_code=source_result.stop_reason_code or source_result.blocked_reason_code,
                    lane_result=runtime_result,
                )
            )

        return SourceRoundDispatchResult(
            source_results=tuple(source_results),
            candidates=tuple(candidates),
            raw_candidate_count=raw_candidate_count,
        )

    def _registered_source_lane_request(
        self,
        *,
        run_state: RunState,
        plan: ContractSourcePlan,
        round_no: int,
        query_terms: tuple[str, ...],
        progress_callback: ProgressCallback | None,
    ) -> ContractSourceLaneRequest:
        plan_terms = plan.query_intents or query_terms or self._source_lane_query_terms(run_state)
        return ContractSourceLaneRequest(
            source_id=plan.source_id,
            lane_mode=plan.lane_mode,
            runtime_run_id=plan.runtime_run_id,
            source_plan_id=plan.source_plan_id,
            source_lane_run_id=f"{plan.source_plan_id}:lane:{round_no}",
            job_title=run_state.input_truth.job_title,
            jd=run_state.input_truth.jd,
            notes=run_state.input_truth.notes,
            requirement_sheet=run_state.requirement_sheet,
            source_query_terms=tuple(plan_terms),
            budget=plan.budget,
            progress_callback=progress_callback,
        )

    async def apply_approved_detail_lane_to_run_async(
        self,
        *,
        base_run_artifacts: RunArtifacts,
        base_finalization_revision: int,
        detail_lane_request: RuntimeSourceLaneRequest,
        source_client: object | None = None,
    ) -> RuntimeDetailEnrichmentResult:
        if base_run_artifacts.run_state is None:
            raise RunStageError("source_lanes", "Approved detail enrichment requires base run state.")
        if base_run_artifacts.finalization_revision is None:
            raise RunStageError("source_lanes", "Approved detail enrichment requires base finalization revision.")
        if base_run_artifacts.finalization_revision.revision != base_finalization_revision:
            raise RunStageError("source_lanes", "Approved detail enrichment received stale base finalization revision.")
        if detail_lane_request.lane_mode != "detail":
            raise RunStageError("source_lanes", "Approved detail enrichment requires a detail lane request.")
        if (
            detail_lane_request.runtime_run_id is not None
            and detail_lane_request.runtime_run_id != base_run_artifacts.run_id
        ):
            raise RunStageError("source_lanes", "Approved detail lease is bound to a different runtime run.")

        run_state = base_run_artifacts.run_state
        lane_result = await self.run_source_lane_async(detail_lane_request, source_client=source_client)
        selected_sources = base_run_artifacts.finalization_revision.selected_source_kinds or (
            detail_lane_request.source,
        )
        source_order = {source: index for index, source in enumerate(selected_sources)}
        apply_source_lane_result(run_state=run_state, result=lane_result, source_order=source_order)
        unscored_candidates = [
            run_state.candidate_store[resume_id]
            for resume_id in run_state.seen_resume_ids
            if resume_id in run_state.candidate_store and resume_id not in run_state.scorecards_by_resume_id
        ]
        tracer = RunTracer(self.settings.artifacts_path, output_mode=self.settings.runtime_artifact_output_mode)
        try:
            if unscored_candidates:
                await self._score_round(
                    round_no=max(1, self.settings.max_rounds + base_finalization_revision + 1),
                    new_candidates=unscored_candidates,
                    run_state=run_state,
                    tracer=tracer,
                    runtime_only_constraints=[],
                )
            identity_top_candidates = self._apply_identity_top_pool(run_state)
        finally:
            tracer.close()

        finalization_revision = RuntimeFinalizationRevision(
            revision=base_finalization_revision + 1,
            runtime_run_id=base_run_artifacts.run_id,
            reason_code="detail_enrichment_applied",
            selected_source_kinds=selected_sources,
            candidate_identity_ids=tuple(
                run_state.candidate_identity_by_resume_id.get(candidate.resume_id, candidate.resume_id)
                for candidate in identity_top_candidates
            ),
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            coverage_summary=run_state.source_coverage_summary,
        )
        run_state.finalization_revisions.append(finalization_revision)
        base_run_artifacts.finalization_revision = finalization_revision
        base_run_artifacts.source_coverage_summary = run_state.source_coverage_summary
        base_run_artifacts.candidate_store = run_state.candidate_store
        base_run_artifacts.normalized_store = run_state.normalized_store
        return RuntimeDetailEnrichmentResult(
            runtime_run_id=base_run_artifacts.run_id,
            base_finalization_revision=base_finalization_revision,
            lane_result=lane_result,
            finalization_revision=finalization_revision,
        )

    async def extract_requirements_async(
        self,
        *,
        job_title: str | None,
        jd: str,
        notes: str,
        progress_callback: ProgressCallback | None = None,
        requirement_cache_scope: str | None = None,
    ) -> RequirementSheet:
        tracer = RunTracer(self.settings.artifacts_path, output_mode=self.settings.runtime_artifact_output_mode)
        close_status = "completed"
        close_failure_summary: str | None = None
        try:
            self._write_run_preamble(tracer=tracer, job_title=job_title, jd=jd, notes=notes)
            self._emit_progress(
                progress_callback,
                "run_started",
                "Starting SeekTalent requirement extraction.",
                payload={"stage": "requirements"},
            )
            self._require_live_llm_config()
            run_state = await self._build_run_state(
                job_title=job_title,
                jd=jd,
                notes=notes,
                tracer=tracer,
                progress_callback=progress_callback,
                requirement_cache_scope=requirement_cache_scope,
            )
            return run_state.requirement_sheet
        except Exception as exc:
            close_status = "failed"
            close_failure_summary = str(exc)
            raise
        finally:
            tracer.close(status=close_status, failure_summary=close_failure_summary)

    async def run_async(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        source_kinds: Sequence[str] | None = None,
        source_context: Mapping[str, str | int | bool | None] | None = None,
        progress_callback: ProgressCallback | None = None,
        runtime_start_callback: RuntimeStartCallback | None = None,
        runtime_checkpoint_callback: RuntimeCheckpointCallback | None = None,
        runtime_round_boundary_callback: RuntimeRoundBoundaryCallback | None = None,
        requirement_cache_scope: str | None = None,
        approved_requirement_sheet: RequirementSheet | None = None,
    ) -> RunArtifacts:
        tracer = RunTracer(self.settings.artifacts_path, output_mode=self.settings.runtime_artifact_output_mode)
        corpus_session = tracer.store.create_root(
            kind="corpus",
            display_name=f"corpus ingest for {tracer.run_id}",
            producer="CorpusRuntime",
        )
        self._active_corpus_session = corpus_session
        close_status = "completed"
        close_failure_summary: str | None = None
        try:
            self._start_corpus_run(tracer=tracer, job_title=job_title, jd=jd, notes=notes)
            self._start_flywheel_run(tracer=tracer, job_title=job_title, jd=jd, notes=notes)
            if runtime_start_callback is not None:
                runtime_start_callback(tracer.run_id)
            self._write_run_preamble(tracer=tracer, job_title=job_title, jd=jd, notes=notes)
            selected_source_kinds = self._selected_source_kinds(source_kinds)
            source_plan = build_runtime_source_plan(
                source_kinds=selected_source_kinds,
                settings=self.settings,
                runtime_run_id=tracer.run_id,
                source_context=source_context,
            )
            self._write_source_plan_artifact(tracer=tracer, source_plan=source_plan)
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
                requirement_cache_scope=requirement_cache_scope,
                approved_requirement_sheet=approved_requirement_sheet,
            )
            detail_open_claim_ledger = DetailOpenClaimLedger(run_state.detail_open_claims_by_provider_key)
            top_scored, stop_reason, rounds_executed, terminal_controller_round = await self._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=detail_open_claim_ledger,
                tracer=tracer,
                source_plan=source_plan,
                source_context=source_context,
                progress_callback=progress_callback,
                runtime_checkpoint_callback=runtime_checkpoint_callback,
                runtime_round_boundary_callback=runtime_round_boundary_callback,
            )
            finalize_context = build_finalize_context(
                run_state=run_state,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                run_id=tracer.run_id,
                run_dir=str(tracer.run_dir),
            )
            (
                final_result,
                final_markdown,
                finalization_stage_state,
            ) = await finalize_runtime.run_deterministic_finalization_stage(
                finalize_context=finalize_context,
                tracer=tracer,
                progress_callback=progress_callback,
                emit_progress=self._emit_progress,
                slim_finalize_context=self._slim_finalize_context,
                render_final_markdown=self._render_final_markdown,
            )
            finalization_completed_artifacts = post_finalize_runtime.write_post_finalize_artifacts(
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
            tracer.emit(
                "finalization_completed",
                status="succeeded",
                latency_ms=finalization_stage_state["latency_ms"],
                summary=final_result.summary,
                artifact_paths=finalization_stage_state["artifacts"] + finalization_completed_artifacts,
                payload={
                    "engine": "deterministic_runtime",
                },
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
            self._refresh_runtime_candidate_checkpoint(
                runtime_checkpoint_callback=runtime_checkpoint_callback,
                tracer=tracer,
                run_state=run_state,
                detail_open_claim_ledger=detail_open_claim_ledger,
            )
            self._emit_runtime_public_event(
                tracer=tracer,
                progress_callback=progress_callback,
                event=make_runtime_public_event(
                    runtime_run_id=tracer.run_id,
                    stage="finalization",
                    event_seq=(rounds_executed + 1) * 100 + 90,
                    round_no=None,
                    counts={
                        "selectedIdentityCount": len(
                            run_state.finalization_revisions[-1].candidate_identity_ids
                            if run_state.finalization_revisions
                            else run_state.top_pool_ids
                        )
                    },
                    details={
                        "finalizationRevision": run_state.finalization_revisions[-1].revision,
                        "finalizationReasonCode": run_state.finalization_revisions[-1].reason_code,
                    }
                    if run_state.finalization_revisions
                    else {},
                ),
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
                artifact_lifecycle_ref=tracer.artifact_lifecycle_ref(),
                finalization_revision=run_state.finalization_revisions[-1]
                if run_state.finalization_revisions
                else None,
                source_coverage_summary=run_state.source_coverage_summary,
                run_state=run_state,
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
            corpus_finalization_error: RunStageError | None = None
            if self._active_corpus_session is corpus_session:
                try:
                    corpus_manifest_error: Exception | None = None
                    try:
                        write_corpus_ingest_manifest(
                            session=corpus_session,
                            run_id=tracer.run_id,
                            tenant_id=DEFAULT_TENANT_ID,
                            workspace_id=DEFAULT_WORKSPACE_ID,
                        )
                    except Exception as exc:  # noqa: BLE001
                        corpus_manifest_error = exc

                    if corpus_manifest_error is not None and close_status == "completed":
                        close_status = "failed"
                        close_failure_summary = str(corpus_manifest_error)
                        corpus_finalization_error = RunStageError("corpus_ingest", str(corpus_manifest_error))
                        try:
                            tracer.emit(
                                "run_failed",
                                summary=str(corpus_manifest_error),
                                payload={
                                    "stage": "corpus_ingest",
                                    "error_type": type(corpus_manifest_error).__name__,
                                    "error_message": str(corpus_manifest_error),
                                },
                            )
                        except Exception as log_exc:  # noqa: BLE001
                            LOGGER.warning(
                                "failed to emit corpus finalization failure event",
                                exc_info=log_exc,
                            )
                    elif corpus_manifest_error is not None:
                        try:
                            tracer.emit(
                                "corpus_ingest_finalization_failed",
                                summary=str(corpus_manifest_error),
                                payload={
                                    "stage": "corpus_ingest",
                                    "error_type": type(corpus_manifest_error).__name__,
                                    "error_message": str(corpus_manifest_error),
                                },
                            )
                        except Exception as log_exc:  # noqa: BLE001
                            LOGGER.warning(
                                "failed to emit corpus finalization failure event",
                                exc_info=log_exc,
                            )

                    try:
                        corpus_session.finalize(status=close_status, failure_summary=close_failure_summary)
                    except Exception as exc:  # noqa: BLE001
                        if close_status == "completed":
                            close_status = "failed"
                            close_failure_summary = str(exc)
                            corpus_finalization_error = RunStageError("corpus_ingest", str(exc))
                            try:
                                tracer.emit(
                                    "run_failed",
                                    summary=str(exc),
                                    payload={
                                        "stage": "corpus_ingest",
                                        "error_type": type(exc).__name__,
                                        "error_message": str(exc),
                                    },
                                )
                            except Exception as log_exc:  # noqa: BLE001
                                LOGGER.warning(
                                    "failed to emit corpus finalization failure event",
                                    exc_info=log_exc,
                                )
                        else:
                            try:
                                tracer.emit(
                                    "corpus_ingest_finalization_failed",
                                    summary=str(exc),
                                    payload={
                                        "stage": "corpus_ingest",
                                        "error_type": type(exc).__name__,
                                        "error_message": str(exc),
                                    },
                                )
                            except Exception as log_exc:  # noqa: BLE001
                                LOGGER.warning(
                                    "failed to emit corpus finalization failure event",
                                    exc_info=log_exc,
                                )
                finally:
                    self._active_corpus_session = None
            self.corpus_store.close()
            if self.flywheel_store is not None:
                if self._active_flywheel_run_id == tracer.run_id:
                    self.flywheel_store.complete_run(
                        run_id=tracer.run_id,
                        status=close_status,
                        failure_summary=close_failure_summary,
                    )
                    self._active_flywheel_run_id = None
                    self._active_flywheel_task_id = None
                self.flywheel_store.close()
            tracer.close(status=close_status, failure_summary=close_failure_summary)
            if corpus_finalization_error is not None:
                raise corpus_finalization_error

    def _selected_source_kinds(self, source_kinds: Sequence[str] | None) -> tuple[str, ...]:
        if self.source_registry is None:
            return tuple(source_kinds or ())
        return tuple(source.source_id for source in self.source_registry.enabled_sources(source_kinds))

    async def _build_run_state(
        self,
        *,
        job_title: str | None,
        jd: str,
        notes: str,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None = None,
        requirement_cache_scope: str | None = None,
        approved_requirement_sheet: RequirementSheet | None = None,
    ) -> RunState:
        return await build_requirements_run_state(
            settings=self.settings,
            requirement_extractor=self.requirement_extractor,
            tracer=tracer,
            job_title=job_title,
            jd=jd,
            notes=notes,
            requirement_cache_scope=requirement_cache_scope,
            progress_callback=progress_callback,
            approved_requirement_sheet=approved_requirement_sheet,
            emit_llm_event=self._emit_llm_event,
            emit_progress=self._emit_progress,
            build_llm_call_snapshot=self._build_llm_call_snapshot,
            write_aux_llm_call_artifact=self._write_aux_llm_call_artifact,
            run_stage_error_factory=RunStageError,
        )

    def _write_run_preamble(self, *, tracer: RunTracer, job_title: str | None, jd: str, notes: str) -> None:
        tracer.write_json("runtime.run_config", self._build_public_run_config())
        self._write_prompt_snapshots(tracer)
        input_snapshot = {
            "job_title_chars": len(job_title or ""),
            "jd_chars": len(jd),
            "notes_chars": len(notes),
            "job_title_sha256": hashlib.sha256((job_title or "").encode("utf-8")).hexdigest(),
            "jd_sha256": hashlib.sha256(jd.encode("utf-8")).hexdigest(),
            "notes_sha256": hashlib.sha256(notes.encode("utf-8")).hexdigest(),
            "job_title_preview": self._preview_text(job_title or "", limit=120),
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
                    "requirements": self.settings.requirements_model_id,
                    "controller": self.settings.controller_model_id,
                    "scoring": self.settings.scoring_model_id,
                    "reflection": self.settings.reflection_model_id,
                    "finalization_engine": "deterministic_runtime",
                    "tui_summary": self.settings.effective_tui_summary_model,
                },
                "enable_eval": self.settings.enable_eval,
                "configured_providers": self._configured_providers(),
            },
        )

    def _write_source_plan_artifact(
        self,
        *,
        tracer: RunTracer,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
    ) -> Path:
        payload = {
            "schema_version": "runtime_source_plan_v1",
            "runtime_run_id": tracer.run_id,
            "source_lanes": [lane.to_public_payload() for lane in source_plan],
        }
        path = tracer.write_json("runtime.source_plan", payload)
        tracer.emit(
            "source_plan_created",
            summary="Runtime source plan created.",
            artifact_paths=[str(path)],
            payload={
                "schema_version": payload["schema_version"],
                "source_count": len(source_plan),
                "sources": [lane.source for lane in source_plan],
            },
        )
        return path

    async def _run_full_source_lanes(
        self,
        *,
        run_state: RunState,
        tracer: RunTracer,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
        source_context: Mapping[str, str | int | bool | None] | None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[ScoredCandidate], str, int, TerminalControllerRound | None]:
        del source_context
        source_order = {lane.source: index for index, lane in enumerate(source_plan)}
        lane_tasks: dict[str, asyncio.Task[RuntimeSourceLaneResult]] = {}
        async with asyncio.TaskGroup() as task_group:
            for lane in source_plan:
                lane_tasks[lane.source_plan_id] = task_group.create_task(
                    self._run_source_lane_safely(
                        lane=lane,
                        run_lane=lambda lane=lane: self._run_registered_runtime_source_lane(
                            lane=lane,
                            run_state=run_state,
                            tracer=tracer,
                            progress_callback=progress_callback,
                        ),
                    )
                )

        lane_results = [lane_tasks[lane.source_plan_id].result() for lane in source_plan]
        for result in lane_results:
            self._write_source_lane_result_artifact(tracer=tracer, result=result)

        for result in lane_results:
            apply_source_lane_result(run_state=run_state, result=result, source_order=source_order)

        unscored_candidates = [
            run_state.candidate_store[resume_id]
            for resume_id in run_state.seen_resume_ids
            if resume_id in run_state.candidate_store and resume_id not in run_state.scorecards_by_resume_id
        ]
        score_round_no = max(1, self.settings.max_rounds + 1)
        if unscored_candidates:
            await self._score_round(
                round_no=score_round_no,
                new_candidates=unscored_candidates,
                run_state=run_state,
                tracer=tracer,
                runtime_only_constraints=[],
            )
        identity_top_candidates = self._apply_identity_top_pool(run_state)

        coverage_summary = self._source_coverage_summary(source_plan=source_plan, lane_results=lane_results)
        run_state.source_coverage_summary = coverage_summary
        finalization_revision = RuntimeFinalizationRevision(
            revision=1,
            runtime_run_id=tracer.run_id,
            reason_code="source_lanes_completed" if coverage_summary.status == "complete" else "source_lanes_degraded",
            selected_source_kinds=coverage_summary.selected_source_kinds,
            candidate_identity_ids=tuple(
                run_state.candidate_identity_by_resume_id.get(candidate.resume_id, candidate.resume_id)
                for candidate in identity_top_candidates
            ),
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            coverage_summary=coverage_summary,
        )
        run_state.finalization_revisions = [finalization_revision]
        tracer.write_json(
            "runtime.source_coverage",
            {
                "runtime_run_id": tracer.run_id,
                "source_statuses": {result.source: result.status for result in lane_results},
                **coverage_summary.to_public_payload(),
            },
        )
        stop_reason = "source_lanes_completed" if coverage_summary.status == "complete" else "source_lanes_degraded"
        return identity_top_candidates, stop_reason, score_round_no if unscored_candidates else 0, None

    def _apply_identity_top_pool(self, run_state: RunState) -> list[ScoredCandidate]:
        return select_identity_top_candidates(run_state)

    def _source_coverage_summary(
        self,
        *,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
        lane_results: list[RuntimeSourceLaneResult],
    ) -> RuntimeSourceCoverageSummary:
        selected_sources = tuple(lane.source for lane in source_plan)
        result_by_source = {result.source: result for result in lane_results}
        completed: list[str] = []
        blocked: list[str] = []
        failed: list[str] = []
        partial: list[str] = []
        empty: list[str] = []
        missing: list[str] = []
        for source in selected_sources:
            result = result_by_source.get(source)
            if result is None:
                missing.append(source)
                continue
            if result.status == "blocked":
                blocked.append(source)
            elif result.status == "failed":
                failed.append(source)
            elif result.status in {"partial", "cancelled"}:
                partial.append(source)
            elif result.status == "completed" and result.candidate_store_updates:
                completed.append(source)
            elif result.status == "completed":
                empty.append(source)

        any_candidates = any(result.candidate_store_updates for result in lane_results)
        if not any_candidates:
            status = "empty"
        elif blocked or failed or partial or empty or missing:
            status = "degraded"
        else:
            status = "complete"
        return RuntimeSourceCoverageSummary(
            status=status,
            selected_source_kinds=selected_sources,
            completed_source_kinds=tuple(cast(Any, completed)),
            blocked_source_kinds=tuple(cast(Any, blocked)),
            failed_source_kinds=tuple(cast(Any, failed)),
            partial_source_kinds=tuple(cast(Any, partial)),
            empty_source_kinds=tuple(cast(Any, empty)),
            missing_source_kinds=tuple(cast(Any, missing)),
            finalization_scope="selected_sources" if status == "complete" else "available_sources_only",
        )

    async def _run_registered_runtime_source_lane(
        self,
        *,
        lane: RuntimeSourceLanePlan,
        run_state: RunState,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None,
    ) -> RuntimeSourceLaneResult:
        if self.source_registry is None:
            raise RunStageError("source_lanes", "source_registry_required")
        source = self.source_registry.get(lane.source)
        source_result = await source.run_card_lane(
            ContractSourceLaneRequest(
                source_id=lane.source,
                lane_mode=lane.lane_mode,
                runtime_run_id=tracer.run_id,
                source_plan_id=lane.source_plan_id,
                source_lane_run_id=f"{lane.source_plan_id}:lane:1",
                job_title=run_state.input_truth.job_title,
                jd=run_state.input_truth.jd,
                notes=run_state.input_truth.notes,
                requirement_sheet=run_state.requirement_sheet,
                source_query_terms=self._source_lane_query_terms(run_state),
                budget=SourceBudget(
                    card_target=lane.source_budget_policy.card_target,
                    detail_target=lane.source_budget_policy.detail_target,
                    scan_limit=lane.source_budget_policy.scan_limit,
                ),
                progress_callback=progress_callback,
            )
        )
        return runtime_source_lane_result_from_source_result(source_result)

    async def _run_source_lane_safely(
        self,
        *,
        lane: RuntimeSourceLanePlan,
        run_lane: Callable[[], Awaitable[RuntimeSourceLaneResult]] | None,
    ) -> RuntimeSourceLaneResult:
        try:
            if run_lane is None:
                raise RunStageError("source_lanes", f"Unsupported runtime source lane: {lane.source}.")
            return await run_lane()
        except asyncio.CancelledError:
            raise
        except RunStageError:
            raise
        except Exception:  # noqa: BLE001
            return self._blocked_source_lane_result(
                lane=lane,
                reason_code="failed_provider_error",
                safe_error_summary=f"{lane.label} provider lane failed before completion.",
                status="failed",
                retryable=True,
            )

    def _blocked_source_lane_result(
        self,
        *,
        lane: RuntimeSourceLanePlan,
        reason_code: str,
        safe_error_summary: str | None = None,
        status: RuntimeSourceLaneStatus = "blocked",
        retryable: bool = False,
    ) -> RuntimeSourceLaneResult:
        source_lane_run_id = f"{lane.source_plan_id}:lane:1"
        event_type_by_status: dict[RuntimeSourceLaneStatus, RuntimeSourceLaneEventType] = {
            "blocked": "source_lane_blocked",
            "failed": "source_lane_failed",
            "cancelled": "source_lane_cancelled",
            "partial": "source_lane_partial",
            "completed": "source_lane_completed",
        }
        event_type = event_type_by_status.get(status, "source_lane_partial")
        return RuntimeSourceLaneResult(
            runtime_run_id=lane.runtime_run_id,
            source_plan_id=lane.source_plan_id,
            source_lane_run_id=source_lane_run_id,
            source=lane.source,
            lane_mode=lane.lane_mode,
            attempt=1,
            status=status,
            blocked_reason_code=reason_code if status == "blocked" else None,
            stop_reason_code=reason_code,
            retryable=retryable,
            safe_error_summary=safe_error_summary,
            events=(
                RuntimeSourceLaneEvent(
                    schema_version="runtime_source_lane_event_v1",
                    runtime_run_id=lane.runtime_run_id,
                    source_plan_id=lane.source_plan_id,
                    source_lane_run_id=source_lane_run_id,
                    source=lane.source,
                    attempt=1,
                    event_seq=1,
                    event_type=event_type,
                    status=status,
                    safe_reason_code=reason_code,
                ),
            ),
        )

    def _write_source_lane_result_artifact(
        self,
        *,
        tracer: RunTracer,
        result: RuntimeSourceLaneResult,
    ) -> Path:
        return tracer.write_json(
            f"runtime.source_lane.{result.source}.{result.lane_mode}.{result.attempt}",
            result.to_public_payload(),
        )

    def _source_lane_query_terms(self, run_state: RunState) -> tuple[str, ...]:
        terms: list[str] = []
        seen: set[str] = set()
        for value in (
            run_state.requirement_sheet.title_anchor_terms
            + run_state.requirement_sheet.must_have_capabilities
            + run_state.requirement_sheet.preferred_capabilities
            + [run_state.requirement_sheet.job_title]
        ):
            text = str(value).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            terms.append(text)
            if len(terms) >= 8:
                break
        return tuple(terms or [run_state.input_truth.job_title])

    def _start_flywheel_run(self, *, tracer: RunTracer, job_title: str, jd: str, notes: str) -> None:
        store = self.flywheel_store
        if store is None:
            return
        task_id = store.upsert_task(job_title=job_title, jd_text=jd, notes_text=notes)
        manifest_ref_id = store.record_artifact_ref(
            artifact_kind=tracer.session.manifest.artifact_kind.value,
            artifact_id=tracer.run_id,
            artifact_root=str(tracer.run_dir),
            logical_name="manifest.run",
            relative_path="manifests/run_manifest.json",
            content_sha256=None,
            schema_version=tracer.session.manifest.manifest_schema_version,
        )
        config_payload = self._build_public_run_config()
        store.start_run(
            run_id=tracer.run_id,
            task_id=task_id,
            version=tracer.session.manifest.producer_version,
            git_sha=tracer.session.manifest.git_sha,
            artifact_ref_id=manifest_ref_id,
            artifact_root=str(tracer.run_dir),
            config_hash=json_sha256(config_payload),
            config_payload=config_payload,
            status="running",
            eval_enabled=self.settings.enable_eval,
            benchmark_id=None,
            benchmark_case_id=None,
        )
        self._active_flywheel_task_id = task_id
        self._active_flywheel_run_id = tracer.run_id

    def _start_corpus_run(self, *, tracer: RunTracer, job_title: str, jd: str, notes: str) -> None:
        input_artifact_ref_id = self.corpus_store.record_artifact_ref(
            artifact_kind=tracer.session.manifest.artifact_kind.value,
            artifact_id=tracer.run_id,
            artifact_root=str(tracer.run_dir),
            logical_name="input.input_snapshot",
            relative_path="input/input_snapshot.json",
            content_sha256=None,
            schema_version="v1",
        )
        jd_doc_id = self.corpus_store.upsert_jd_document(
            build_jd_document_row(
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=DEFAULT_WORKSPACE_ID,
                job_title=job_title,
                jd_text=jd,
                notes_text=notes,
                source_kind="runtime_input",
                source_ref=f"run:{tracer.run_id}:input.input_snapshot",
            )
        )
        self.corpus_store.link_run_to_jd(
            run_id=tracer.run_id,
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=DEFAULT_WORKSPACE_ID,
            jd_doc_id=jd_doc_id,
            input_artifact_ref_id=input_artifact_ref_id,
        )

    async def _execute_multi_source_round_search(
        self,
        *,
        round_no: int,
        retrieval_plan: RoundRetrievalPlan,
        proposed_filter_plan: ProposedFilterPlan,
        query_states: tuple[LogicalQueryState, ...],
        adapter_notes: Sequence[str],
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        run_state: RunState,
        detail_open_claim_ledger: DetailOpenClaimLedger,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
        source_context: Mapping[str, str | int | bool | None] | None,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None = None,
        runtime_checkpoint_callback: RuntimeCheckpointCallback | None = None,
    ) -> RetrievalExecutionResult:
        assert_novel_term_group_keys(
            term_group_keys=[query_state.term_group_key for query_state in query_states],
            used_term_group_keys=used_term_group_keys(run_state.retrieval_state.query_execution_ledger),
        )
        lane_requested_counts = allocate_initial_lane_targets(query_states=list(query_states), target_new=target_new)
        logical_queries = build_logical_query_dispatches(
            round_no=round_no,
            query_states=query_states,
            lane_requested_counts=lane_requested_counts,
            source_plan_version=str(retrieval_plan.plan_version),
        )
        filter_intents = build_runtime_filter_intents(
            requirement_sheet=run_state.requirement_sheet,
            proposed_filter_plan=proposed_filter_plan,
        )
        location_intent = build_runtime_location_execution_intent(
            requirement_sheet=run_state.requirement_sheet,
            proposed_filter_plan=proposed_filter_plan,
            round_no=round_no,
            target_new=target_new,
        )
        source_query_policy = self._source_query_policies(source_plan)
        source_query_intents_by_source = build_runtime_source_query_intents(
            source_kinds=tuple(lane.source for lane in source_plan),
            logical_dispatches=logical_queries,
            filter_intents=filter_intents,
            location_intent=location_intent,
            age_intent=None,
            source_budget_policy={lane.source: lane.source_budget_policy for lane in source_plan},
            source_query_policy=source_query_policy,
            must_have_capabilities=tuple(run_state.requirement_sheet.must_have_capabilities),
            preferred_capabilities=tuple(run_state.requirement_sheet.preferred_capabilities),
        )
        source_raw_targets = {
            lane.source: sum(intent.requested_count for intent in source_query_intents_by_source.get(lane.source, ()))
            for lane in source_plan
        }
        self._emit_runtime_public_event(
            tracer=tracer,
            progress_callback=progress_callback,
            event=make_runtime_public_event(
                runtime_run_id=tracer.run_id,
                stage="round_query",
                event_seq=round_no * 100 + 1,
                round_no=round_no,
                counts={"topPoolCount": len(run_state.top_pool_ids)},
                details={
                    "queryGroups": _planned_public_query_groups(logical_queries),
                },
            ),
        )
        for source_index, lane in enumerate(source_plan, start=1):
            self._emit_runtime_public_event(
                tracer=tracer,
                progress_callback=progress_callback,
                event=make_runtime_public_event(
                    runtime_run_id=tracer.run_id,
                    stage="source_dispatch",
                    event_seq=round_no * 100 + 10 + source_index,
                    round_no=round_no,
                    source_kind=lane.source,
                    status="running",
                ),
            )
        source_event_indexes = {lane.source: index for index, lane in enumerate(source_plan, start=1)}

        def report_source_result(result: SourceRoundAdapterResult) -> None:
            source_index = source_event_indexes.get(result.source, 0)
            prior_returned, prior_identities = self._source_cumulative_counts(
                run_state=run_state,
                source=result.source,
            )
            if result.lane_result is not None:
                merge_source_lane_result_updates(
                    run_state=run_state,
                    result=result.lane_result,
                    source_order={lane.source: index for index, lane in enumerate(source_plan)},
                )
                self._refresh_runtime_candidate_checkpoint(
                    runtime_checkpoint_callback=runtime_checkpoint_callback,
                    tracer=tracer,
                    run_state=run_state,
                    detail_open_claim_ledger=detail_open_claim_ledger,
                )
            self._emit_runtime_public_event(
                tracer=tracer,
                progress_callback=progress_callback,
                event=make_runtime_public_event(
                    runtime_run_id=tracer.run_id,
                    stage="source_result",
                    event_seq=round_no * 100 + 30 + source_index,
                    round_no=round_no,
                    source_kind=result.source,
                    status=result.status,
                    counts={
                        "roundReturned": result.raw_candidate_count,
                        "roundIdentities": len(result.candidates),
                        "sourceCumulativeReturned": prior_returned + result.raw_candidate_count,
                        "sourceCumulativeIdentities": prior_identities + len(result.candidates),
                    },
                    safe_reason_code=result.safe_reason_code,
                ),
            )

        source_plan_by_source = {lane.source: lane for lane in source_plan}
        round_context = RuntimeSourceRoundContext(
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            proposed_filter_plan=proposed_filter_plan,
            adapter_notes=tuple(adapter_notes),
            target_new=target_new,
            seen_resume_ids=frozenset(seen_resume_ids),
            seen_dedup_keys=frozenset(seen_dedup_keys),
            run_state=run_state,
            source_plan_by_source=source_plan_by_source,
            source_context=source_context,
            tracer=tracer,
            detail_open_claim_ledger=detail_open_claim_ledger,
        )
        source_adapters = (
            self.source_round_adapter_provider(self, round_context)
            if self.source_round_adapter_provider is not None
            else {}
        )
        pre_round_seen_resume_ids = frozenset(run_state.seen_resume_ids)
        dispatch_result = await dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id=tracer.run_id,
                round_no=round_no,
                logical_queries=logical_queries,
                selected_sources=tuple(lane.source for lane in source_plan),
                seen_resume_ids=frozenset(seen_resume_ids),
                seen_dedup_keys=frozenset(seen_dedup_keys),
                source_query_intents_by_source=source_query_intents_by_source,
                requirement_sheet=run_state.requirement_sheet,
            ),
            source_adapters=source_adapters,
            result_callback=report_source_result,
        )
        run_state.retrieval_state.query_execution_ledger.extend(dispatch_result.query_execution_receipts)
        tracer.write_json(
            "runtime.query_execution_ledger",
            [item.model_dump(mode="json") for item in run_state.retrieval_state.query_execution_ledger],
        )
        tracer.write_json(
            _round_artifact(
                tracer,
                round_no=round_no,
                subsystem="retrieval",
                name="query_execution_receipts",
            ),
            [item.model_dump(mode="json") for item in dispatch_result.query_execution_receipts],
        )
        self._refresh_runtime_candidate_checkpoint(
            runtime_checkpoint_callback=runtime_checkpoint_callback,
            tracer=tracer,
            run_state=run_state,
            detail_open_claim_ledger=detail_open_claim_ledger,
        )
        self._merge_source_round_dispatch_result(
            run_state=run_state,
            dispatch_result=dispatch_result,
            source_plan=source_plan,
            round_no=round_no,
            tracer=tracer,
        )
        self._emit_runtime_public_event(
            tracer=tracer,
            progress_callback=progress_callback,
            event=make_runtime_public_event(
                runtime_run_id=tracer.run_id,
                stage="merge",
                event_seq=round_no * 100 + 50,
                round_no=round_no,
                counts={
                    "roundUniqueIdentities": _round_unique_identity_count(
                        dispatch_result=dispatch_result,
                        run_state=run_state,
                        pre_round_seen_resume_ids=pre_round_seen_resume_ids,
                    ),
                    "mergedIdentities": len(run_state.candidate_identities),
                },
            ),
        )
        if run_state.source_coverage_summary is None:
            raise RunStageError("source_lanes", "source_coverage_missing")
        not_ready_reason = self._source_round_not_ready_reason(
            coverage_summary=run_state.source_coverage_summary,
            dispatch_result=dispatch_result,
            has_prior_candidates=bool(pre_round_seen_resume_ids),
        )
        if not_ready_reason is not None:
            raise RunStageError("source_lanes", not_ready_reason)
        identities_seen_before_round = {
            identity_id
            for resume_id in pre_round_seen_resume_ids
            if (identity_id := run_state.candidate_identity_by_resume_id.get(resume_id)) is not None
        }
        query_outcomes = apply_post_merge_query_counts(
            outcomes=logical_outcomes_from_receipts(dispatch_result.query_execution_receipts),
            candidate_attributions=dispatch_result.candidate_query_attributions,
            candidate_identity_by_resume_id=run_state.candidate_identity_by_resume_id,
            dispatch_order=[query.query_instance_id for query in logical_queries],
            identities_seen_before_round=identities_seen_before_round,
        )
        return self._round_search_result_from_source_dispatch(
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            query_states=query_states,
            dispatch_result=dispatch_result,
            source_raw_targets=cast(Mapping[str, int], source_raw_targets),
            query_outcomes=query_outcomes,
            tracer=tracer,
        )

    def _merge_source_round_dispatch_result(
        self,
        *,
        run_state: RunState,
        dispatch_result: SourceRoundDispatchResult,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
        round_no: int,
        tracer: RunTracer,
    ) -> None:
        source_order = {lane.source: index for index, lane in enumerate(source_plan)}
        for candidate in dispatch_result.candidates:
            run_state.candidate_store[candidate.resume_id] = candidate
            if candidate.resume_id not in run_state.seen_resume_ids:
                run_state.seen_resume_ids.append(candidate.resume_id)
        for result in dispatch_result.source_results:
            if result.retrieval_result is not None:
                for candidate in result.retrieval_result.new_candidates:
                    run_state.candidate_store[candidate.resume_id] = candidate
                    if candidate.resume_id not in run_state.seen_resume_ids:
                        run_state.seen_resume_ids.append(candidate.resume_id)
            if result.lane_result is not None:
                merge_source_lane_result_updates(
                    run_state=run_state,
                    result=result.lane_result,
                    source_order=source_order,
                    rebuild_identity=False,
                )
        normalize_runtime_candidates(
            run_state=run_state,
            candidates=dispatch_result.candidates,
            round_no=round_no,
            tracer=tracer,
        )
        rebuild_candidate_identities(run_state, source_order=source_order)
        run_state.source_coverage_summary = self._source_coverage_summary_from_dispatch(
            source_plan=source_plan,
            dispatch_result=dispatch_result,
        )

    def _round_search_result_from_source_dispatch(
        self,
        *,
        round_no: int,
        retrieval_plan: RoundRetrievalPlan,
        query_states: tuple[LogicalQueryState, ...],
        dispatch_result: SourceRoundDispatchResult,
        source_raw_targets: Mapping[str, int] | None = None,
        tracer: RunTracer,
        query_outcomes: list[LogicalQueryOutcome] | None = None,
    ) -> RetrievalExecutionResult:
        del query_states
        retrieval_results = [
            result.retrieval_result for result in dispatch_result.source_results if result.retrieval_result is not None
        ]
        executed_queries = [query for result in retrieval_results for query in result.executed_queries]
        sent_query_records = [record for result in retrieval_results for record in result.sent_query_records]
        search_attempts = [attempt for result in retrieval_results for attempt in result.search_attempts]
        query_resume_hits = [hit for result in retrieval_results for hit in result.query_resume_hits]
        provider_returned_candidates = [
            item for result in retrieval_results for item in result.provider_returned_candidates
        ]
        candidates = list(dispatch_result.candidates)
        selected_source_count = max(1, len(dispatch_result.source_results))
        requested_source_count = (
            sum(source_raw_targets.values())
            if source_raw_targets is not None
            else retrieval_plan.target_new * selected_source_count
        )
        observation = SearchObservation(
            round_no=round_no,
            requested_count=requested_source_count,
            raw_candidate_count=dispatch_result.raw_candidate_count,
            unique_new_count=len(candidates),
            shortage_count=max(0, requested_source_count - len(candidates)),
            fetch_attempt_count=len(dispatch_result.source_results),
            exhausted_reason="target_satisfied"
            if len(candidates) >= requested_source_count
            else "source_lanes_exhausted",
            new_resume_ids=[candidate.resume_id for candidate in candidates],
            new_candidate_summaries=[candidate.compact_summary() for candidate in candidates],
            adapter_notes=[note for result in dispatch_result.source_results for note in result.diagnostics],
        )
        tracer.write_json(
            f"round.{round_no:02d}.retrieval.source_dispatch",
            {
                "round_no": round_no,
                "source_statuses": {result.source: result.status for result in dispatch_result.source_results},
                "raw_candidate_count": dispatch_result.raw_candidate_count,
                "unique_new_count": len(candidates),
            },
        )
        return RetrievalExecutionResult(
            executed_queries=executed_queries,
            sent_query_records=sent_query_records,
            new_candidates=candidates,
            search_observation=observation,
            search_attempts=search_attempts,
            query_resume_hits=query_resume_hits,
            provider_returned_candidates=provider_returned_candidates,
            executed_query_packages=dispatch_result.executed_query_packages,
            query_execution_receipts=list(dispatch_result.query_execution_receipts),
            candidate_query_attributions=list(dispatch_result.candidate_query_attributions),
            query_outcomes=query_outcomes or [],
        )

    def _source_coverage_summary_from_dispatch(
        self,
        *,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
        dispatch_result: SourceRoundDispatchResult,
    ) -> RuntimeSourceCoverageSummary:
        selected_sources = tuple(lane.source for lane in source_plan)
        result_by_source = {result.source: result for result in dispatch_result.source_results}
        completed: list[str] = []
        blocked: list[str] = []
        failed: list[str] = []
        partial: list[str] = []
        empty: list[str] = []
        missing: list[str] = []
        for source in selected_sources:
            result = result_by_source.get(source)
            if result is None:
                missing.append(source)
                continue
            if result.status == "blocked":
                blocked.append(source)
            elif result.status == "failed":
                failed.append(source)
            elif result.status == "partial":
                partial.append(source)
            elif result.status == "completed" and result.candidates:
                completed.append(source)
            elif result.status == "completed":
                empty.append(source)

        any_candidates = any(result.candidates for result in dispatch_result.source_results)
        if not any_candidates:
            status = "empty"
        elif blocked or failed or partial or empty or missing:
            status = "degraded"
        else:
            status = "complete"
        return RuntimeSourceCoverageSummary(
            status=status,
            selected_source_kinds=selected_sources,
            completed_source_kinds=tuple(cast(Any, completed)),
            blocked_source_kinds=tuple(cast(Any, blocked)),
            failed_source_kinds=tuple(cast(Any, failed)),
            partial_source_kinds=tuple(cast(Any, partial)),
            empty_source_kinds=tuple(cast(Any, empty)),
            missing_source_kinds=tuple(cast(Any, missing)),
            finalization_scope="selected_sources" if status == "complete" else "available_sources_only",
        )

    def _source_round_not_ready_reason(
        self,
        *,
        coverage_summary: RuntimeSourceCoverageSummary,
        dispatch_result: SourceRoundDispatchResult,
        has_prior_candidates: bool = False,
    ) -> str | None:
        if coverage_summary.status == "complete":
            return None
        if (
            coverage_summary.status == "empty"
            and not coverage_summary.blocked_source_kinds
            and not coverage_summary.failed_source_kinds
            and not coverage_summary.partial_source_kinds
            and not coverage_summary.missing_source_kinds
        ):
            return None
        result_by_source = {result.source: result for result in dispatch_result.source_results}
        if has_prior_candidates and self._source_round_can_finish_with_prior_candidates(
            coverage_summary=coverage_summary,
            result_by_source=result_by_source,
        ):
            return None
        for source in coverage_summary.blocked_source_kinds:
            result = result_by_source.get(source)
            diagnostic = self._generic_provider_failure_diagnostic(result)
            if diagnostic is not None:
                return diagnostic
            return (result.safe_reason_code if result is not None else None) or f"source_{source}_blocked"
        for source in coverage_summary.failed_source_kinds:
            result = result_by_source.get(source)
            diagnostic = self._generic_provider_failure_diagnostic(result)
            if diagnostic is not None:
                return diagnostic
            return (result.safe_reason_code if result is not None else None) or f"source_{source}_failed"
        for source in coverage_summary.partial_source_kinds:
            result = result_by_source.get(source)
            return (result.safe_reason_code if result is not None else None) or f"source_{source}_partial"
        for source in coverage_summary.empty_source_kinds:
            return f"source_{source}_empty"
        for source in coverage_summary.missing_source_kinds:
            return f"source_{source}_missing"
        return f"source_coverage_{coverage_summary.status}"

    def _source_round_can_finish_with_prior_candidates(
        self,
        *,
        coverage_summary: RuntimeSourceCoverageSummary,
        result_by_source: Mapping[str, SourceRoundAdapterResult],
    ) -> bool:
        unstable_sources = (
            *coverage_summary.blocked_source_kinds,
            *coverage_summary.failed_source_kinds,
            *coverage_summary.partial_source_kinds,
        )
        if not unstable_sources or coverage_summary.missing_source_kinds:
            return False
        if coverage_summary.completed_source_kinds or coverage_summary.empty_source_kinds:
            return False
        for source in unstable_sources:
            result = result_by_source.get(source)
            reason = result.safe_reason_code if result is not None else None
            if reason not in _PRIOR_CANDIDATE_FINALIZABLE_SOURCE_REASONS:
                return False
        return True

    def _generic_provider_failure_diagnostic(self, result: SourceRoundAdapterResult | None) -> str | None:
        if result is None or result.safe_reason_code not in {"failed_provider_error", "source_provider_failed"}:
            return None
        for diagnostic in result.diagnostics:
            text = str(diagnostic).strip()
            if text:
                return text
        return None

    async def _run_rounds(
        self,
        *,
        run_state: RunState,
        detail_open_claim_ledger: DetailOpenClaimLedger,
        tracer: RunTracer,
        source_plan: tuple[RuntimeSourceLanePlan, ...] | None = None,
        source_context: Mapping[str, str | int | bool | None] | None = None,
        progress_callback: ProgressCallback | None = None,
        runtime_checkpoint_callback: RuntimeCheckpointCallback | None = None,
        runtime_round_boundary_callback: RuntimeRoundBoundaryCallback | None = None,
    ) -> tuple[list[ScoredCandidate], str, int, TerminalControllerRound | None]:
        if source_plan is None:
            source_plan = build_runtime_source_plan(
                source_kinds=self._selected_source_kinds(None),
                settings=self.settings,
                runtime_run_id=tracer.run_id,
                source_context=source_context,
            )
        seen_dedup_keys: set[str] = set()
        stop_reason = "max_rounds_reached"
        rounds_executed = 0
        terminal_controller_round: TerminalControllerRound | None = None

        for round_no in range(1, self.settings.max_rounds + 1):
            if runtime_round_boundary_callback is not None:
                updated_requirement_sheet = runtime_round_boundary_callback(round_no)
                if updated_requirement_sheet is not None:
                    run_state.requirement_sheet = updated_requirement_sheet
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
            projection_result = ConstraintProjectionResult()
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
            prf_selection = await self._select_prf_backend_decision(
                run_state=run_state,
                retrieval_plan=retrieval_plan,
                tracer=tracer,
            )
            query_states, second_lane_decision = self._build_round_query_bundle(
                round_no=round_no,
                retrieval_plan=retrieval_plan,
                title_anchor_terms=run_state.requirement_sheet.title_anchor_terms,
                query_term_pool=run_state.retrieval_state.query_term_pool,
                used_term_group_keys=used_term_group_keys(run_state.retrieval_state.query_execution_ledger),
                prf_decision=prf_selection.prf_decision,
                run_id=tracer.run_id,
                job_intent_fingerprint=job_intent_fingerprint,
                source_plan_version=str(retrieval_plan.plan_version),
                prf_probe_proposal_backend=prf_selection.prf_probe_proposal_backend,
                llm_prf_failure_kind=prf_selection.llm_prf_failure_kind,
                llm_prf_input_artifact_ref=prf_selection.llm_prf_input_artifact_ref,
                llm_prf_call_artifact_ref=prf_selection.llm_prf_call_artifact_ref,
                llm_prf_candidates_artifact_ref=prf_selection.llm_prf_candidates_artifact_ref,
                llm_prf_grounding_artifact_ref=prf_selection.llm_prf_grounding_artifact_ref,
            )
            source_raw_targets = self._source_raw_targets(
                source_plan=source_plan,
                query_states=tuple(query_states),
                target_new=target_new,
            )
            tracer.write_json(
                f"round.{round_no:02d}.retrieval.second_lane_decision",
                second_lane_decision.model_dump(mode="json"),
            )
            run_state.retrieval_state.second_lane_decision_history.append(second_lane_decision)
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
                retrieval_result = await self._execute_multi_source_round_search(
                    round_no=round_no,
                    retrieval_plan=retrieval_plan,
                    proposed_filter_plan=controller_decision.proposed_filter_plan,
                    query_states=tuple(query_states),
                    adapter_notes=projection_result.adapter_notes,
                    target_new=target_new,
                    seen_resume_ids=set(run_state.seen_resume_ids),
                    seen_dedup_keys=seen_dedup_keys,
                    run_state=run_state,
                    detail_open_claim_ledger=detail_open_claim_ledger,
                    source_plan=source_plan,
                    source_context=source_context,
                    tracer=tracer,
                    progress_callback=progress_callback,
                    runtime_checkpoint_callback=runtime_checkpoint_callback,
                )
            except RunStageError as exc:
                self._emit_progress(
                    progress_callback,
                    "search_failed",
                    str(exc),
                    round_no=round_no,
                    payload={"stage": exc.stage, "error_type": type(exc).__name__},
                )
                raise
            except Exception as exc:  # noqa: BLE001
                self._emit_progress(
                    progress_callback,
                    "search_failed",
                    str(exc),
                    round_no=round_no,
                    payload={"stage": "search", "error_type": type(exc).__name__},
                )
                raise RunStageError("source_search", str(exc)) from exc
            executed_queries = retrieval_result.executed_queries
            sent_query_records = retrieval_result.sent_query_records
            new_candidates = retrieval_result.new_candidates
            search_observation = retrieval_result.search_observation
            search_attempts = retrieval_result.search_attempts
            query_resume_hits = retrieval_result.query_resume_hits
            query_outcomes = retrieval_result.query_outcomes
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
                    "executed_queries": self._executed_query_summaries(executed_queries),
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
                _round_artifact(
                    tracer,
                    round_no=round_no,
                    subsystem="retrieval",
                    name="query_outcomes",
                ),
                [item.model_dump(mode="json") for item in query_outcomes],
            )
            tracer.write_json(
                _round_artifact(tracer, round_no=round_no, subsystem="retrieval", name="executed_queries"),
                [item.model_dump(mode="json") for item in executed_queries],
            )

            for candidate in new_candidates:
                run_state.candidate_store[candidate.resume_id] = candidate
            run_state.seen_resume_ids.extend(
                item.resume_id for item in new_candidates if item.resume_id not in run_state.seen_resume_ids
            )
            seen_dedup_keys.update(item.dedup_key for item in new_candidates)
            self._refresh_runtime_candidate_checkpoint(
                runtime_checkpoint_callback=runtime_checkpoint_callback,
                tracer=tracer,
                run_state=run_state,
                detail_open_claim_ledger=detail_open_claim_ledger,
            )

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
                    selected_source_kinds=tuple(lane.source for lane in source_plan),
                    source_raw_targets=source_raw_targets,
                )
            finally:
                self._write_query_resume_hits(
                    tracer=tracer,
                    round_no=round_no,
                    query_resume_hits=query_resume_hits,
                    scorecards_by_resume_id=run_state.scorecards_by_resume_id,
                )
                self._record_flywheel_retrieval_rows(
                    tracer=tracer,
                    run_state=run_state,
                    executed_queries=executed_queries,
                    sent_query_records=sent_query_records,
                    query_resume_hits=query_resume_hits,
                    job_intent_fingerprint=job_intent_fingerprint,
                    prf_selection=prf_selection,
                )
                replay_snapshot = build_replay_snapshot_direct(
                    run_id=tracer.run_id,
                    round_no=round_no,
                    second_lane_decision=second_lane_decision,
                    search_attempts=search_attempts,
                    query_resume_hits=query_resume_hits,
                    search_observation=search_observation,
                    scoring_model_version=self.settings.scoring_model_id,
                    query_plan_version=str(retrieval_plan.plan_version),
                    llm_prf_snapshot_metadata=prf_selection.llm_prf_snapshot_metadata,
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
            self._emit_runtime_public_event(
                tracer=tracer,
                progress_callback=progress_callback,
                event=make_runtime_public_event(
                    runtime_run_id=tracer.run_id,
                    stage="scoring",
                    event_seq=round_no * 100 + 70,
                    round_no=round_no,
                    counts={
                        "roundIdentities": newly_scored_count,
                        "topPoolCount": len(current_top_candidates),
                    },
                ),
            )
            self._refresh_runtime_candidate_checkpoint(
                runtime_checkpoint_callback=runtime_checkpoint_callback,
                tracer=tracer,
                run_state=run_state,
                detail_open_claim_ledger=detail_open_claim_ledger,
            )
            resume_quality_comment: str | None = None
            resume_quality_comment_error: str | None = None
            try:
                resume_quality_comment = (
                    await self.resume_quality_commenter.comment(
                        round_no=round_no,
                        query_terms=retrieval_plan.query_terms,
                        candidates=sorted(scored_this_round, key=scored_candidate_sort_key)[:5],
                        normalized_store=run_state.normalized_store,
                    )
                    or None
                )
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
                executed_queries=executed_queries,
                search_observation=search_observation,
                search_attempts=search_attempts,
                top_candidates=current_top_candidates,
                dropped_candidates=dropped_candidates,
                top_pool_ids=run_state.top_pool_ids,
                dropped_candidate_ids=[candidate.resume_id for candidate in dropped_candidates],
                query_outcomes=query_outcomes,
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
            self._emit_runtime_public_event(
                tracer=tracer,
                progress_callback=progress_callback,
                event=make_runtime_public_event(
                    runtime_run_id=tracer.run_id,
                    stage="feedback",
                    event_seq=round_no * 100 + 80,
                    round_no=round_no,
                    counts={"feedbackCandidateCount": len(pool_decisions)},
                    details={
                        "queryGroups": _executed_public_query_groups(
                            round_state.query_outcomes,
                            source_order=[lane.source for lane in source_plan],
                        ),
                        "resumeQualityComment": resume_quality_comment,
                        "reflectionSummary": reflection_advice.reflection_summary,
                        "suggestStop": reflection_advice.suggest_stop,
                        "suggestedStopReason": reflection_advice.suggested_stop_reason,
                        "suggestedActivateTerms": reflection_advice.keyword_advice.suggested_activate_terms,
                        "suggestedKeepTerms": reflection_advice.keyword_advice.suggested_keep_terms,
                        "suggestedDeprioritizeTerms": reflection_advice.keyword_advice.suggested_deprioritize_terms,
                        "suggestedDropTerms": reflection_advice.keyword_advice.suggested_drop_terms,
                        "suggestedKeepFilterFields": reflection_advice.filter_advice.suggested_keep_filter_fields,
                        "suggestedDropFilterFields": reflection_advice.filter_advice.suggested_drop_filter_fields,
                        "suggestedAddFilterFields": reflection_advice.filter_advice.suggested_add_filter_fields,
                    },
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
                    executed_queries=executed_queries,
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

    def _refresh_runtime_candidate_checkpoint(
        self,
        *,
        runtime_checkpoint_callback: RuntimeCheckpointCallback | None,
        tracer: RunTracer,
        run_state: RunState,
        detail_open_claim_ledger: DetailOpenClaimLedger,
    ) -> None:
        if runtime_checkpoint_callback is None:
            return
        snapshot = detail_open_claim_ledger.snapshot()
        checkpoint_run_state = run_state.model_copy(
            update={"detail_open_claims_by_provider_key": snapshot},
        )
        runtime_checkpoint_callback(
            SimpleNamespace(
                run_id=tracer.run_id,
                run_state=checkpoint_run_state,
                candidate_store=checkpoint_run_state.candidate_store,
                normalized_store=checkpoint_run_state.normalized_store,
                final_result=SimpleNamespace(candidates=[]),
                finalization_revision=None,
                source_coverage_summary=checkpoint_run_state.source_coverage_summary,
            )
        )

    def _source_raw_targets(
        self,
        *,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
        query_states: tuple[LogicalQueryState, ...],
        target_new: int,
    ) -> dict[str, int]:
        lane_requested_counts = allocate_initial_lane_targets(query_states=list(query_states), target_new=target_new)
        source_query_policy = self._source_query_policies(source_plan)
        targets: dict[str, int] = {}
        for lane in source_plan:
            total = 0
            query_policy = source_query_policy.get(lane.source) if source_query_policy is not None else None
            for query_state in query_states:
                total += source_requested_count(
                    source_kind=lane.source,
                    lane_type=query_state.lane_type,
                    requested_count=int(lane_requested_counts.get(query_state.lane_type, 0)),
                    source_budget_policy=lane.source_budget_policy,
                    source_query_policy=query_policy,
                )
            targets[lane.source] = total
        return targets

    def _source_query_policies(
        self,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
    ) -> Mapping[SourceKind, RuntimeSourceQueryPolicy] | None:
        if self.source_query_policy_provider is None:
            return None
        return self.source_query_policy_provider(source_plan)

    def _build_round_progress_payload(
        self,
        *,
        run_state: RunState,
        round_no: int,
        retrieval_plan,
        executed_queries: list[ProviderQuery],
        observation: SearchObservation,
        newly_scored_count: int,
        pool_decisions: list[PoolDecision],
        reflection: ReflectionAdvice | None,
        resume_quality_comment: str | None = None,
        resume_quality_comment_error: str | None = None,
    ) -> dict[str, object]:
        scored_this_round = [
            candidate for candidate in run_state.scorecards_by_resume_id.values() if candidate.source_round == round_no
        ]
        decision_counts = Counter(item.decision for item in pool_decisions)
        return {
            "round_no": round_no,
            "query_terms": retrieval_plan.query_terms,
            "keyword_query": retrieval_plan.keyword_query,
            "executed_queries": self._executed_query_summaries(executed_queries),
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
            if scorecard.score_evidence_source is not None:
                hit.score_evidence_source = scorecard.score_evidence_source
            hit.card_scorecard_ref = scorecard.card_scorecard_ref
            hit.detail_scorecard_ref = scorecard.detail_scorecard_ref
            hit.score_delta = scorecard.score_delta
            hit.detail_open_reason = scorecard.detail_open_reason
            hit.detail_open_policy_version = scorecard.detail_open_policy_version
        tracer.write_json(
            f"round.{round_no:02d}.retrieval.query_resume_hits",
            [item.model_dump(mode="json") for item in query_resume_hits],
        )

    def _record_corpus_provider_results(
        self,
        *,
        tracer: RunTracer,
        returned_candidates: list[ProviderReturnedCandidate],
    ) -> None:
        corpus_session = self._active_corpus_session
        if corpus_session is None:
            return
        if not tracer.artifact_policy.writes_local_debug_artifacts:
            return
        try:
            record_corpus_provider_results(
                session=corpus_session,
                store=self.corpus_store,
                run_id=tracer.run_id,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=DEFAULT_WORKSPACE_ID,
                returned_candidates=returned_candidates,
            )
        except RunStageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RunStageError("corpus_ingest", str(exc)) from exc

    def _record_flywheel_retrieval_rows(
        self,
        *,
        tracer: RunTracer,
        run_state: RunState,
        executed_queries: list[ProviderQuery],
        sent_query_records: list[SentQueryRecord],
        query_resume_hits: list[QueryResumeHit],
        job_intent_fingerprint: str,
        prf_selection: _PRFBackendSelection,
    ) -> None:
        store = self.flywheel_store
        if store is None or self._active_flywheel_run_id != tracer.run_id:
            return

        def run_flywheel_step(step: str, fn: Callable[[], _T]) -> _T:
            try:
                return fn()
            except RunStageError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RunStageError(f"flywheel_{step}", f"{step}: {exc}") from exc

        provider_name = getattr(self.provider, "name", self.settings.provider_name)
        canonical_query_specs: dict[str, dict[str, object]] = {}
        for query in executed_queries:
            if query.query_instance_id is None or query.query_instance_id in canonical_query_specs:
                continue
            canonical_query_specs[query.query_instance_id] = {
                "lane_type": query.lane_type,
                "query_role": query.query_role,
                "query_terms": query.query_terms,
                "keyword_query": query.keyword_query,
                "provider_filters": query.native_filters,
                "rendered_provider_query": query.keyword_query,
                "provider_name": provider_name,
                "page": query.page,
                "page_size": query.page_size,
            }

        query_rows = build_run_query_rows(
            run_id=tracer.run_id,
            artifact_id=tracer.run_id,
            sent_query_records=sent_query_records,
            canonical_query_specs=canonical_query_specs,
            job_intent_fingerprint=job_intent_fingerprint,
            query_policy_version="typed-second-lane-v1",
        )
        run_flywheel_step("record_run_queries", lambda: store.record_run_queries(query_rows))

        available_snapshot_hashes: set[str] = set()
        for hit in query_resume_hits:
            snapshot_sha256 = hit.snapshot_sha256
            if not snapshot_sha256:
                continue
            candidate = run_state.candidate_store.get(hit.resume_id)
            if candidate is None:
                if store.resume_snapshot_exists(snapshot_sha256):
                    available_snapshot_hashes.add(snapshot_sha256)
                continue
            snapshot_payload = canonical_resume_snapshot_payload(candidate.raw)
            run_flywheel_step(
                "upsert_resume_snapshot",
                lambda candidate=candidate, snapshot_payload=snapshot_payload, snapshot_sha256=snapshot_sha256: (
                    store.upsert_resume_snapshot(
                        snapshot_sha256=snapshot_sha256,
                        source_resume_id=candidate.source_resume_id or candidate.resume_id,
                        dedup_key=candidate.dedup_key,
                        raw_payload=snapshot_payload,
                        normalized_preview={"search_text": candidate.search_text},
                    )
                ),
            )
            available_snapshot_hashes.add(snapshot_sha256)

        hit_rows = query_hit_rows_from_hits(query_resume_hits)
        for row in hit_rows:
            snapshot_hash = row.get("snapshot_sha256")
            if snapshot_hash and snapshot_hash not in available_snapshot_hashes:
                row["snapshot_sha256"] = None
                row["snapshot_missing_reason"] = "raw_payload_unavailable"
        run_flywheel_step("record_query_resume_hits", lambda: store.record_query_resume_hits(hit_rows))
        persisted_hits = run_flywheel_step(
            "query_hits_for_run_round",
            lambda: store.query_hits_for_run_round(
                run_id=tracer.run_id,
                round_no=query_resume_hits[0].round_no if query_resume_hits else 0,
            ),
        )
        outcome_rows = build_runtime_query_outcome_rows_from_hits(
            run_id=tracer.run_id,
            hits=persisted_hits,
            thresholds_payload=QueryOutcomeThresholds().model_dump(mode="json"),
        )
        covered_query_ids = {str(row["query_instance_id"]) for row in outcome_rows}
        thresholds_payload = QueryOutcomeThresholds().model_dump(mode="json")
        for record in sent_query_records:
            if record.query_instance_id is None or record.query_fingerprint is None:
                continue
            if record.query_instance_id in covered_query_ids:
                continue
            outcome_rows.append(
                build_runtime_query_outcome_row(
                    run_id=tracer.run_id,
                    query_instance_id=record.query_instance_id,
                    query_fingerprint=record.query_fingerprint,
                    round_no=record.round_no,
                    lane_type=str(record.lane_type or "exploit"),
                    provider_returned_count=0,
                    new_unique_resume_count=0,
                    duplicate_count=0,
                    scored_resume_count=0,
                    new_fit_count=0,
                    must_have_match_scores=[],
                    risk_scores=[],
                    off_intent_reason_count=0,
                    classification=QueryOutcomeClassification(
                        primary_label="zero_recall",
                        labels=["zero_recall"],
                        reasons=["provider_returned_count == 0"],
                    ),
                    thresholds_payload=thresholds_payload,
                )
            )
        run_flywheel_step("record_query_outcomes", lambda: store.record_query_outcomes(outcome_rows))

        term_events = self._build_flywheel_term_events(
            tracer=tracer,
            sent_query_records=sent_query_records,
            prf_selection=prf_selection,
            prf_artifact_ref_ids=run_flywheel_step(
                "record_prf_artifact_refs",
                lambda: self._record_prf_flywheel_artifact_refs(tracer=tracer, prf_selection=prf_selection),
            ),
        )
        if term_events:
            run_flywheel_step("record_term_events", lambda: store.record_term_events(term_events))
            runtime_outcomes = {str(row["query_instance_id"]): row for row in outcome_rows}
            run_flywheel_step(
                "record_term_outcomes",
                lambda: store.record_term_outcomes(
                    build_term_outcome_rows(term_events=term_events, runtime_outcomes=runtime_outcomes)
                ),
            )
        run_flywheel_step(
            "materialize_run_artifacts",
            lambda: materialize_flywheel_run_artifacts(
                session=tracer.session,
                store=store,
                run_id=tracer.run_id,
            ),
        )

    def _build_flywheel_term_events(
        self,
        *,
        tracer: RunTracer,
        sent_query_records: list[SentQueryRecord],
        prf_selection: _PRFBackendSelection,
        prf_artifact_ref_ids: dict[str, str],
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        for record in sent_query_records:
            if record.query_instance_id is None:
                continue
            for index, term in enumerate(record.query_terms):
                term_family_id = build_term_family_id(term)
                if record.lane_type == "prf_probe" and index > 0:
                    source = "accepted_prf_expression"
                    accepted_by_prf_gate: int | None = 1
                elif record.lane_type == "generic_explore":
                    source = "generic_explore"
                    accepted_by_prf_gate = None
                else:
                    source = "controller_query"
                    accepted_by_prf_gate = None
                events.append(
                    {
                        "run_id": tracer.run_id,
                        "term_event_id": f"executed:{record.query_instance_id}:{term_family_id}",
                        "proposal_id": None,
                        "prf_decision_id": None,
                        "prf_candidate_artifact_ref_id": None,
                        "prf_policy_decision_artifact_ref_id": None,
                        "prf_proposal_extractor_version": None,
                        "prf_familying_version": None,
                        "prf_gate_version": None,
                        "candidate_query_fingerprint": record.query_fingerprint,
                        "executed_query_instance_id": record.query_instance_id,
                        "selected_query_instance_id": record.query_instance_id,
                        "term_surface": term,
                        "term_family_id": term_family_id,
                        "term_role": "query_term",
                        "source": source,
                        "round_no": record.round_no,
                        "lane_type": record.lane_type,
                        "accepted_by_prf_gate": accepted_by_prf_gate,
                        "prf_reject_reasons_json": None,
                        "supporting_resume_ids_json": None,
                        "negative_resume_ids_json": None,
                        "artifact_ref_id": None,
                    }
                )

        decision = prf_selection.prf_decision
        if decision is None:
            return events
        proposal_id = f"{tracer.run_id}:round-{decision.gate_input.round_no}:llm-prf"
        prf_decision_id = f"{proposal_id}:policy"
        for expression in decision.candidate_expressions:
            if not expression.reject_reasons:
                continue
            events.append(
                build_rejected_prf_term_event(
                    run_id=tracer.run_id,
                    proposal_id=proposal_id,
                    prf_decision_id=prf_decision_id,
                    prf_candidate_artifact_ref_id=prf_artifact_ref_ids.get("candidates"),
                    prf_policy_decision_artifact_ref_id=prf_artifact_ref_ids.get("policy_decision"),
                    prf_proposal_extractor_version=LLM_PRF_EXTRACTOR_VERSION,
                    prf_familying_version=LLM_PRF_FAMILYING_VERSION,
                    prf_gate_version=PRF_POLICY_VERSION,
                    term_surface=expression.canonical_expression,
                    term_family_id=expression.term_family_id,
                    round_no=decision.gate_input.round_no,
                    reject_reasons=expression.reject_reasons,
                    supporting_resume_ids=expression.supporting_resume_ids,
                    negative_resume_ids=decision.gate_input.negative_resume_ids,
                )
            )
        return events

    def _record_prf_flywheel_artifact_refs(
        self,
        *,
        tracer: RunTracer,
        prf_selection: _PRFBackendSelection,
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        if prf_selection.prf_decision is None:
            return refs
        if prf_selection.llm_prf_candidates_artifact_ref:
            candidate_ref = self._record_flywheel_artifact_ref(
                tracer=tracer,
                logical_name=prf_selection.llm_prf_candidates_artifact_ref,
            )
            if candidate_ref is not None:
                refs["candidates"] = candidate_ref
        if prf_selection.llm_prf_policy_decision_artifact_ref:
            policy_ref = self._record_flywheel_artifact_ref(
                tracer=tracer,
                logical_name=prf_selection.llm_prf_policy_decision_artifact_ref,
            )
            if policy_ref is not None:
                refs["policy_decision"] = policy_ref
        return refs

    def _record_flywheel_artifact_ref(self, *, tracer: RunTracer, logical_name: str) -> str | None:
        store = self.flywheel_store
        if store is None:
            return None
        entry = tracer.session.manifest.logical_artifacts.get(logical_name)
        if entry is None:
            return None
        artifact_path = tracer.run_dir / entry.path
        content_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest() if artifact_path.is_file() else None
        return store.record_artifact_ref(
            artifact_kind=tracer.session.manifest.artifact_kind.value,
            artifact_id=tracer.run_id,
            artifact_root=str(tracer.run_dir),
            logical_name=logical_name,
            relative_path=entry.path,
            content_sha256=content_sha256,
            schema_version=entry.schema_version,
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

    def _executed_query_summaries(self, executed_queries: list[ProviderQuery]) -> list[dict[str, object]]:
        summaries: list[dict[str, object]] = []
        seen: set[tuple[str | None, str, tuple[str, ...], str]] = set()
        for query in executed_queries:
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

    def _choose_rescue_decision(
        self, *, run_state: RunState, controller_context: ControllerContext, round_no: int
    ) -> RescueDecision:
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
                anchor_only_broaden_attempted=run_state.retrieval_state.anchor_only_broaden_attempted,
                has_novel_anchor_only_group=rescue_execution_runtime.has_novel_anchor_only_group(
                    run_state.retrieval_state
                ),
            )
        )
        run_state.retrieval_state.rescue_lane_history.append(
            {"round_no": round_no, "selected_lane": decision.selected_lane}
        )
        return decision

    async def _continue_after_empty_feedback(
        self,
        *,
        run_state: RunState,
        controller_context: ControllerContext,
        round_no: int,
        tracer: RunTracer,
        rescue_decision: RescueDecision,
        progress_callback: ProgressCallback | None,
    ) -> RescueDecision:
        del controller_context, round_no, tracer, progress_callback
        skipped_lanes = [
            *rescue_decision.skipped_lanes,
            SkippedRescueLane(lane="candidate_feedback", reason="no_safe_feedback_term"),
        ]
        if run_state.retrieval_state.anchor_only_broaden_attempted:
            skipped_lanes.append(SkippedRescueLane(lane="anchor_only", reason="already_attempted"))
            return rescue_decision.model_copy(
                update={
                    "selected_lane": "allow_stop",
                    "skipped_lanes": skipped_lanes,
                }
            )
        if not rescue_execution_runtime.has_novel_anchor_only_group(run_state.retrieval_state):
            skipped_lanes.append(SkippedRescueLane(lane="anchor_only", reason="no_novel_anchor_only_query"))
            return rescue_decision.model_copy(
                update={
                    "selected_lane": "allow_stop",
                    "skipped_lanes": skipped_lanes,
                }
            )
        return rescue_decision.model_copy(
            update={
                "selected_lane": "anchor_only",
                "skipped_lanes": skipped_lanes,
            }
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

    def _force_anchor_only_decision(
        self, *, run_state: RunState, round_no: int, reason: str
    ) -> SearchControllerDecision:
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
        selected_source_kinds: tuple[str, ...] = (),
        source_raw_targets: dict[str, int] | None = None,
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
            selected_source_kinds=selected_source_kinds,
            source_raw_targets=source_raw_targets,
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

        class _NoOpSession:
            def register_path(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

        class _NoOpTracer:
            def __init__(self) -> None:
                self.session = _NoOpSession()

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
            tracer=_NoOpTracer(),  # ty:ignore[invalid-argument-type]
        )
        if scoring_failures:
            raise RunStageError("scoring", self._format_scoring_failure_message(scoring_failures))
        return scored_candidates

    def _build_public_run_config(self) -> dict[str, object]:
        return {
            "settings": {
                "provider_name": self.provider.name,
                "text_llm_protocol_family": self.settings.text_llm_protocol_family,
                "text_llm_provider_label": self.settings.text_llm_provider_label,
                "text_llm_endpoint_kind": self.settings.text_llm_endpoint_kind,
                "text_llm_endpoint_region": self.settings.text_llm_endpoint_region,
                "requirements_model_id": self.settings.requirements_model_id,
                "controller_model_id": self.settings.controller_model_id,
                "scoring_model_id": self.settings.scoring_model_id,
                "finalization_engine": "deterministic_runtime",
                "reflection_model_id": self.settings.reflection_model_id,
                "tui_summary_model_id": self.settings.effective_tui_summary_model,
                "judge_model_id": self.settings.judge_model_id,
                "reasoning_effort": self.settings.reasoning_effort,
                "judge_reasoning_effort": self.settings.effective_judge_reasoning_effort,
                "requirements_enable_thinking": self.settings.requirements_enable_thinking,
                "controller_enable_thinking": self.settings.controller_enable_thinking,
                "controller_timeout_seconds": self.settings.controller_timeout_seconds,
                "reflection_enable_thinking": self.settings.reflection_enable_thinking,
                "structured_repair_model_id": self.settings.structured_repair_model_id,
                "structured_repair_reasoning_effort": self.settings.structured_repair_reasoning_effort,
                "candidate_feedback_enabled": self.settings.candidate_feedback_enabled,
                "candidate_feedback_model_id": self.settings.candidate_feedback_model_id,
                "candidate_feedback_reasoning_effort": self.settings.candidate_feedback_reasoning_effort,
                "prf_probe_phrase_proposal_model_id": self.settings.prf_probe_phrase_proposal_model_id,
                "prf_probe_phrase_proposal_reasoning_effort": (
                    self.settings.prf_probe_phrase_proposal_reasoning_effort
                ),
                "prf_probe_phrase_proposal_timeout_seconds": (self.settings.prf_probe_phrase_proposal_timeout_seconds),
                "prf_probe_phrase_proposal_max_output_tokens": (
                    self.settings.prf_probe_phrase_proposal_max_output_tokens
                ),
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
                failure_kind=call_artifact.get("failure_kind"),
                provider_failure_kind=call_artifact.get("provider_failure_kind"),
                provider_status_code=call_artifact.get("provider_status_code"),
                provider_error_type=call_artifact.get("provider_error_type"),
                provider_error_code=call_artifact.get("provider_error_code"),
                provider_request_id=call_artifact.get("provider_request_id"),
                round_no=round_no,
                validator_retry_count=int(call_artifact.get("validator_retry_count", 0)),
                validator_retry_reasons=list(call_artifact.get("validator_retry_reasons") or []),
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
        failure_kind: Literal[
            "timeout",
            "transport_error",
            "provider_error",
            "response_validation_error",
            "structured_output_parse_error",
            "insufficient_prf_seed_support",
            "settings_migration_error",
            "unsupported_capability",
        ]
        | None = None,
        provider_failure_kind: Literal[
            "provider_auth_error",
            "provider_access_denied",
            "provider_quota_exceeded",
            "provider_rate_limited",
            "provider_model_not_found",
            "provider_endpoint_mismatch",
            "provider_invalid_request",
            "provider_unsupported_parameter",
            "provider_content_safety_block",
            "provider_schema_error",
            "provider_timeout",
            "provider_unknown_error",
        ]
        | None = None,
        provider_status_code: int | None = None,
        provider_error_type: str | None = None,
        provider_error_code: str | None = None,
        provider_request_id: str | None = None,
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
        resolved_stage = "structured_repair" if stage.startswith("repair_") else stage
        stage_config = resolve_stage_model_config(self.settings, stage=resolved_stage)
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
            provider=stage_config.provider_label,
            protocol_family=stage_config.protocol_family,  # ty:ignore[invalid-argument-type]
            endpoint_kind=stage_config.endpoint_kind,
            endpoint_region=stage_config.endpoint_region,
            prompt_hash=prompt.sha256,
            prompt_snapshot_path=self._prompt_snapshot_path(prompt_name),
            structured_output_mode=stage_config.structured_output_mode,
            thinking_mode=stage_config.thinking_mode,
            reasoning_effort=stage_config.reasoning_effort,
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
            output_summary=self._llm_output_summary(stage=stage, output=structured_output, input_payload=user_payload),
            error_message=error_message,
            failure_kind=failure_kind,
            provider_failure_kind=provider_failure_kind,
            provider_status_code=provider_status_code,
            provider_error_type=provider_error_type,
            provider_error_code=provider_error_code,
            provider_request_id=provider_request_id,
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

    def _llm_output_summary(
        self,
        *,
        stage: str,
        output: Any | None,
        input_payload: dict[str, Any] | None = None,
    ) -> str | None:
        if output is None:
            return None
        if stage == "tui_summary" and isinstance(output, dict):
            return self._preview_text(str(output.get("comment", "")), limit=140)
        if stage == "requirements" and isinstance(output, dict):
            job_title = self._job_title_from_llm_input(input_payload)
            return (
                f"job_title={job_title!r}; "
                f"title_anchors={len(output.get('title_anchor_terms') or [])}; "
                f"jd_terms={len(output.get('jd_query_terms') or [])}"
            )
        if stage == "controller":
            action = output.get("action")
            if action == "stop":
                return f"action=stop; stop_reason={output.get('stop_reason')}"
            return f"action=source_search; query_terms={len(output.get('proposed_query_terms') or [])}"
        if stage == "reflection":
            summary = str(output.get("reflection_summary", ""))
            return f"suggest_stop={output.get('suggest_stop')}; {self._preview_text(summary, limit=140)}"
        if stage == "finalize":
            return f"candidates={len(output.get('candidates') or [])}; {self._preview_text(str(output.get('summary', '')), limit=140)}"
        if stage == "repair_requirements" and isinstance(output, dict):
            job_title = self._job_title_from_llm_input(input_payload)
            return f"job_title={job_title!r}; title_anchors={len(output.get('title_anchor_terms') or [])}"
        if stage == "repair_controller" and isinstance(output, dict):
            action = output.get("action")
            return f"action={action}; query_terms={len(output.get('proposed_query_terms') or [])}"
        if stage == "repair_reflection" and isinstance(output, dict):
            return self._preview_text(str(output.get("reflection_summary", "")), limit=140)
        return f"{stage} output payload"

    def _job_title_from_llm_input(self, input_payload: dict[str, Any] | None) -> str:
        truth = (input_payload or {}).get("INPUT_TRUTH", {})
        if isinstance(truth, dict):
            return str(truth.get("job_title", ""))
        return ""

    def _input_text_refs(self, *, job_title: str | None, jd: str, notes: str) -> dict[str, object]:
        return {
            "input_truth_ref": "input_truth.json",
            "job_title": job_title or "",
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

    def _emit_runtime_public_event(
        self,
        *,
        tracer: RunTracer,
        progress_callback: ProgressCallback | None,
        event: RuntimePublicEvent,
    ) -> None:
        payload = dict(event)
        tracer.append_runtime_public_event_mirror(payload)
        self._emit_progress(
            progress_callback,
            "runtime_public_event",
            str(event["stage"]),
            round_no=event["roundNo"],
            payload=payload,
        )

    def _source_cumulative_counts(self, *, run_state: RunState, source: SourceKind) -> tuple[int, int]:
        returned_count = 0
        for evidence_items in run_state.source_evidence_by_resume_id.values():
            returned_count += sum(1 for evidence in evidence_items if evidence.source == source)
        identity_ids = {
            identity_id
            for identity_id, evidence_items in run_state.source_evidence_by_identity_id.items()
            if any(evidence.source == source for evidence in evidence_items)
        }
        return returned_count, len(identity_ids)

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
            requirements_model=self.settings.requirements_model_id,
            controller_model=self.settings.controller_model_id,
            scoring_model=self.settings.scoring_model_id,
            reflection_model=self.settings.reflection_model_id,
            finalization_engine="deterministic_runtime",
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
            build_round_search_diagnostics=self._build_round_search_diagnostics,  # ty:ignore[invalid-argument-type]
            reflection_advice_application_for_decision=self._reflection_advice_application_for_decision,  # ty:ignore[invalid-argument-type]
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
        return query_containing_term_stats_direct(run_state)  # ty:ignore[invalid-return-type]

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
            stats_by_term=stats_by_term,  # ty:ignore[invalid-argument-type]
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
        try:
            extra_stage_names = []
            if self.settings.candidate_feedback_enabled:
                extra_stage_names.append("candidate_feedback")
            preflight_models(self.settings, extra_stage_names=extra_stage_names)
        except Exception as exc:  # noqa: BLE001
            raise RunStageError("llm_preflight", str(exc)) from exc

    def _configured_providers(self) -> list[str]:
        stage_names = ["requirements", "controller", "scoring", "reflection", "tui_summary"]
        if self.settings.enable_eval:
            stage_names.append("judge")
        seen: set[str] = set()
        providers: list[str] = []
        for stage_name in stage_names:
            endpoint_kind = resolve_stage_model_config(self.settings, stage=stage_name).endpoint_kind
            if endpoint_kind in seen:
                continue
            seen.add(endpoint_kind)
            providers.append(endpoint_kind)
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
        return normalize_stop_reason(proposed)

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
        used_term_group_keys: Collection[str],
    ) -> list[LogicalQueryState]:
        query_states, _ = self._build_round_query_bundle(
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            title_anchor_terms=title_anchor_terms,
            query_term_pool=query_term_pool,
            used_term_group_keys=used_term_group_keys,
            prf_decision=None,
            run_id="test-run",
            job_intent_fingerprint="test-job-intent",
            source_plan_version=str(retrieval_plan.plan_version),
        )
        return query_states

    def _build_job_intent_fingerprint(self, *, run_state: RunState) -> str:
        requirement_sheet = run_state.requirement_sheet
        return build_job_intent_fingerprint(
            job_title=requirement_sheet.job_title,
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
        used_term_group_keys: Collection[str],
        prf_decision: PRFPolicyDecision | None,
        run_id: str,
        job_intent_fingerprint: str,
        source_plan_version: str,
        prf_probe_proposal_backend: str | None = None,
        llm_prf_failure_kind: str | None = None,
        llm_prf_input_artifact_ref: str | None = None,
        llm_prf_call_artifact_ref: str | None = None,
        llm_prf_candidates_artifact_ref: str | None = None,
        llm_prf_grounding_artifact_ref: str | None = None,
    ) -> tuple[list[LogicalQueryState], SecondLaneDecision]:
        del title_anchor_terms
        provider_name = getattr(self.provider, "name", self.settings.provider_name)
        exploit_query_state = build_logical_query_state(
            run_id=run_id,
            round_no=round_no,
            lane_type="exploit",
            query_terms=list(retrieval_plan.query_terms),
            job_intent_fingerprint=job_intent_fingerprint,
            source_plan_version=source_plan_version,
            provider_filters=retrieval_plan.projected_provider_filters,
            location_execution_plan=retrieval_plan.location_execution_plan,
            provider_name=provider_name,
        )
        query_states = [exploit_query_state]
        second_lane_decision, second_lane_query_state = build_second_lane_decision(
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            query_term_pool=query_term_pool,
            used_term_group_keys=used_term_group_keys,
            prf_decision=prf_decision,
            run_id=run_id,
            job_intent_fingerprint=job_intent_fingerprint,
            source_plan_version=source_plan_version,
            prf_probe_proposal_backend=prf_probe_proposal_backend,
            llm_prf_failure_kind=llm_prf_failure_kind,
            llm_prf_input_artifact_ref=llm_prf_input_artifact_ref,
            llm_prf_call_artifact_ref=llm_prf_call_artifact_ref,
            llm_prf_candidates_artifact_ref=llm_prf_candidates_artifact_ref,
            llm_prf_grounding_artifact_ref=llm_prf_grounding_artifact_ref,
            provider_name=provider_name,
        )
        if second_lane_query_state is not None:
            query_states.append(second_lane_query_state)
        for query_state in query_states:
            query_state.term_group_key = build_term_group_key(
                query_terms=query_state.query_terms,
                query_term_pool=query_term_pool,
            )
        assert_novel_term_group_keys(
            term_group_keys=[query_state.term_group_key for query_state in query_states],
            used_term_group_keys=used_term_group_keys,
        )
        return query_states, second_lane_decision

    async def _select_prf_backend_decision(
        self,
        *,
        run_state: RunState,
        retrieval_plan,
        tracer: RunTracer,
    ) -> _PRFBackendSelection:
        if not self._prf_second_lane_eligible(retrieval_plan):
            return _PRFBackendSelection(prf_decision=None)
        return await self._build_llm_prf_policy_decision(
            run_state=run_state,
            retrieval_plan=retrieval_plan,
            tracer=tracer,
        )

    def _prf_second_lane_eligible(self, retrieval_plan) -> bool:
        return retrieval_plan.round_no != 1 and len(retrieval_plan.query_terms) > 1

    async def _build_llm_prf_policy_decision(
        self,
        *,
        run_state: RunState,
        retrieval_plan,
        tracer: RunTracer,
    ) -> _PRFBackendSelection:
        round_no = retrieval_plan.round_no
        artifact_refs = build_llm_prf_artifact_refs(round_no=round_no)
        seeds, feedback_negatives = self._feedback_seed_sets(run_state=run_state)
        negatives = select_llm_prf_negative_resumes(feedback_negatives)
        tried_term_family_ids = self._tried_term_family_ids(run_state=run_state, retrieval_plan=retrieval_plan)
        payload = build_llm_prf_input(
            seed_resumes=seeds,
            negative_resumes=negatives,
            round_no=round_no,
            job_title=run_state.requirement_sheet.job_title,
            role_summary=run_state.requirement_sheet.role_summary,
            must_have_capabilities=run_state.requirement_sheet.must_have_capabilities,
            retrieval_query_terms=retrieval_plan.query_terms,
            existing_query_terms=[item.term for item in run_state.retrieval_state.query_term_pool],
            sent_query_terms=[
                term
                for receipt in run_state.retrieval_state.query_execution_ledger
                if receipt.dispatch_started
                for term in receipt.query_terms
            ],
            tried_term_family_ids=tried_term_family_ids,
            normalized_resumes_by_id=run_state.normalized_store,
        )
        if payload is None:
            payload = LLMPRFInput(
                round_no=round_no,
                job_title=run_state.requirement_sheet.job_title,
                role_summary=run_state.requirement_sheet.role_summary,
                must_have_capabilities=list(run_state.requirement_sheet.must_have_capabilities),
                retrieval_query_terms=list(retrieval_plan.query_terms),
                existing_query_terms=[item.term for item in run_state.retrieval_state.query_term_pool],
                sent_query_terms=[
                    term
                    for receipt in run_state.retrieval_state.query_execution_ledger
                    if receipt.dispatch_started
                    for term in receipt.query_terms
                ],
                tried_term_family_ids=tried_term_family_ids,
                seed_resume_ids=[item.resume_id for item in seeds],
                negative_resume_ids=[item.resume_id for item in negatives],
            )
            decision = self._empty_llm_prf_decision(
                payload=payload,
                failure_kind="insufficient_prf_seed_support",
            )
            self._write_llm_prf_artifacts(
                tracer=tracer,
                artifact_refs=artifact_refs,
                payload=payload,
                extraction=LLMPRFExtraction(),
                grounding=LLMPRFGroundingResult(),
                decision=decision,
                call_artifact=build_llm_prf_failure_call_artifact(
                    settings=self.settings,
                    payload=payload,
                    user_prompt_text=render_llm_prf_prompt(payload),
                    started_at=self._now_iso(),
                    latency_ms=0,
                    round_no=round_no,
                    failure_kind="insufficient_prf_seed_support",
                    error_message="insufficient_prf_seed_support",
                ),
            )
            return self._llm_prf_backend_selection(
                decision=decision,
                artifact_refs=artifact_refs,
                failure_kind="insufficient_prf_seed_support",
            )

        tracer.write_json(artifact_refs.input_artifact_ref, payload.model_dump(mode="json"))
        try:
            preflight_models(self.settings, extra_stage_names=["prf_probe_phrase_proposal"])
        except Exception as exc:  # noqa: BLE001
            failure_kind = "llm_prf_unsupported_capability"
            decision = self._empty_llm_prf_decision(payload=payload, failure_kind=failure_kind)
            self._write_llm_prf_artifacts(
                tracer=tracer,
                artifact_refs=artifact_refs,
                payload=payload,
                extraction=LLMPRFExtraction(),
                grounding=LLMPRFGroundingResult(),
                decision=decision,
                call_artifact=build_llm_prf_failure_call_artifact(
                    settings=self.settings,
                    payload=payload,
                    user_prompt_text=render_llm_prf_prompt(payload),
                    started_at=self._now_iso(),
                    latency_ms=0,
                    round_no=round_no,
                    failure_kind="unsupported_capability",
                    error_message=str(exc),
                ),
                input_already_written=True,
            )
            return self._llm_prf_backend_selection(
                decision=decision,
                artifact_refs=artifact_refs,
                failure_kind=failure_kind,
            )
        started_at = self._now_iso()
        start = perf_counter()
        user_prompt_text = render_llm_prf_prompt(payload)
        try:
            extraction = await asyncio.wait_for(
                self.llm_prf_extractor.propose(payload),
                timeout=self.settings.prf_probe_phrase_proposal_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            failure_info = classify_text_llm_failure(exc)
            latency_ms = int((perf_counter() - start) * 1000)
            failure_kind = self._llm_prf_failure_kind(failure_info.failure_kind)
            decision = self._empty_llm_prf_decision(payload=payload, failure_kind=failure_kind)
            call_artifact = build_llm_prf_failure_call_artifact(
                settings=self.settings,
                payload=payload,
                user_prompt_text=user_prompt_text,
                started_at=started_at,
                latency_ms=latency_ms,
                round_no=round_no,
                failure_kind=cast(Any, failure_info.failure_kind),
                error_message=str(exc),
                provider_usage=getattr(self.llm_prf_extractor, "last_provider_usage", None),
            )
            call_artifact["provider_failure_kind"] = failure_info.provider_failure_kind
            call_artifact["provider_status_code"] = failure_info.provider_status_code
            call_artifact["provider_error_type"] = failure_info.provider_error_type
            call_artifact["provider_error_code"] = failure_info.provider_error_code
            call_artifact["provider_request_id"] = failure_info.provider_request_id
            self._write_llm_prf_artifacts(
                tracer=tracer,
                artifact_refs=artifact_refs,
                payload=payload,
                extraction=LLMPRFExtraction(),
                grounding=LLMPRFGroundingResult(),
                decision=decision,
                call_artifact=call_artifact,
                input_already_written=True,
            )
            return self._llm_prf_backend_selection(
                decision=decision,
                artifact_refs=artifact_refs,
                failure_kind=failure_kind,
            )

        latency_ms = int((perf_counter() - start) * 1000)
        grounding = ground_llm_prf_candidates(payload, extraction)
        expressions = feedback_expressions_from_llm_grounding(
            payload,
            grounding,
            known_company_entities=self._known_company_entities(run_state=run_state),
            tried_term_family_ids=set(tried_term_family_ids),
        )
        decision = self._llm_prf_decision_from_expressions(
            payload=payload,
            expressions=expressions,
            run_state=run_state,
            retrieval_plan=retrieval_plan,
        )
        failure_kind = None if decision.gate_passed else "no_safe_llm_prf_expression"
        if failure_kind is not None:
            decision = decision.model_copy(update={"reject_reasons": [failure_kind]})
        call_artifact = build_llm_prf_success_call_artifact(
            settings=self.settings,
            payload=payload,
            user_prompt_text=user_prompt_text,
            extraction=extraction,
            started_at=started_at,
            latency_ms=latency_ms,
            round_no=round_no,
            provider_usage=getattr(self.llm_prf_extractor, "last_provider_usage", None),
        )
        self._write_llm_prf_artifacts(
            tracer=tracer,
            artifact_refs=artifact_refs,
            payload=payload,
            extraction=extraction,
            grounding=grounding,
            decision=decision,
            call_artifact=call_artifact,
            input_already_written=True,
        )
        return self._llm_prf_backend_selection(
            decision=decision,
            artifact_refs=artifact_refs,
            failure_kind=failure_kind,
        )

    def _llm_prf_decision_from_expressions(
        self,
        *,
        payload: LLMPRFInput,
        expressions: list[FeedbackCandidateExpression],
        run_state: RunState,
        retrieval_plan,
    ) -> PRFPolicyDecision:
        return build_prf_policy_decision(
            PRFGateInput(
                round_no=retrieval_plan.round_no,
                seed_resume_ids=payload.seed_resume_ids,
                seed_count=len(payload.seed_resume_ids),
                negative_resume_ids=payload.negative_resume_ids,
                candidate_expressions=expressions,
                candidate_expression_count=len(expressions),
                tried_term_family_ids=payload.tried_term_family_ids,
                tried_query_fingerprints=self._tried_query_fingerprints(run_state=run_state),
                min_seed_count=MIN_PRF_SEED_COUNT,
                max_negative_support_rate=MAX_NEGATIVE_SUPPORT_RATE,
                policy_version=PRF_POLICY_VERSION,
            )
        )

    def _empty_llm_prf_decision(self, *, payload: LLMPRFInput, failure_kind: str) -> PRFPolicyDecision:
        decision = build_prf_policy_decision(
            PRFGateInput(
                round_no=payload.round_no,
                seed_resume_ids=payload.seed_resume_ids,
                seed_count=len(payload.seed_resume_ids),
                negative_resume_ids=payload.negative_resume_ids,
                candidate_expressions=[],
                candidate_expression_count=0,
                tried_term_family_ids=payload.tried_term_family_ids,
                tried_query_fingerprints=[],
                min_seed_count=MIN_PRF_SEED_COUNT,
                max_negative_support_rate=MAX_NEGATIVE_SUPPORT_RATE,
                policy_version=PRF_POLICY_VERSION,
            )
        )
        return decision.model_copy(update={"reject_reasons": [failure_kind]})

    def _write_llm_prf_artifacts(
        self,
        *,
        tracer: RunTracer,
        artifact_refs,
        payload: LLMPRFInput,
        extraction: LLMPRFExtraction,
        grounding: LLMPRFGroundingResult,
        decision: PRFPolicyDecision,
        call_artifact: dict[str, Any],
        input_already_written: bool = False,
    ) -> None:
        if not input_already_written:
            tracer.write_json(artifact_refs.input_artifact_ref, payload.model_dump(mode="json"))
        self._write_aux_llm_call_artifact(
            tracer=tracer,
            path=artifact_refs.call_artifact_ref,
            call_artifact=call_artifact,
            input_artifact_refs=[artifact_refs.input_artifact_ref],
            output_artifact_refs=[
                artifact_refs.candidates_artifact_ref,
                artifact_refs.grounding_artifact_ref,
                artifact_refs.policy_decision_artifact_ref,
            ],
            round_no=payload.round_no,
        )
        tracer.write_json(artifact_refs.candidates_artifact_ref, extraction.model_dump(mode="json"))
        tracer.write_json(artifact_refs.grounding_artifact_ref, grounding.model_dump(mode="json"))
        tracer.write_json(artifact_refs.policy_decision_artifact_ref, decision.model_dump(mode="json"))

    def _llm_prf_backend_selection(
        self,
        *,
        decision: PRFPolicyDecision,
        artifact_refs,
        failure_kind: str | None,
    ) -> _PRFBackendSelection:
        return _PRFBackendSelection(
            prf_decision=decision,
            prf_probe_proposal_backend="llm_deepseek_v4_flash",
            llm_prf_failure_kind=failure_kind,
            llm_prf_input_artifact_ref=artifact_refs.input_artifact_ref,
            llm_prf_call_artifact_ref=artifact_refs.call_artifact_ref,
            llm_prf_candidates_artifact_ref=artifact_refs.candidates_artifact_ref,
            llm_prf_grounding_artifact_ref=artifact_refs.grounding_artifact_ref,
            llm_prf_policy_decision_artifact_ref=artifact_refs.policy_decision_artifact_ref,
            llm_prf_snapshot_metadata=self._llm_prf_snapshot_metadata(),
        )

    def _llm_prf_snapshot_metadata(self) -> dict[str, object]:
        stage_config = resolve_stage_model_config(self.settings, stage="prf_probe_phrase_proposal")
        prompt = self.prompts.load("prf_probe_phrase_proposal")
        return {
            "llm_prf_extractor_version": LLM_PRF_EXTRACTOR_VERSION,
            "llm_prf_grounding_validator_version": GROUNDING_VALIDATOR_VERSION,
            "llm_prf_familying_version": LLM_PRF_FAMILYING_VERSION,
            "llm_prf_model_id": stage_config.model_id,
            "llm_prf_protocol_family": stage_config.protocol_family,
            "llm_prf_endpoint_kind": stage_config.endpoint_kind,
            "llm_prf_endpoint_region": stage_config.endpoint_region,
            "llm_prf_structured_output_mode": stage_config.structured_output_mode,
            "llm_prf_prompt_hash": prompt.sha256,
            "llm_prf_output_retry_count": LLM_PRF_OUTPUT_RETRIES,
        }

    def _llm_prf_failure_kind(self, failure_kind: str) -> str:
        if failure_kind == "timeout":
            return "llm_prf_timeout"
        return f"llm_prf_{failure_kind}"

    def _now_iso(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

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

    def _tried_term_family_ids(self, *, run_state: RunState, retrieval_plan) -> list[str]:
        return unique_strings(
            [
                build_term_family_id(term)
                for receipt in run_state.retrieval_state.query_execution_ledger
                if receipt.dispatch_started
                for term in receipt.query_terms
            ]
            + [build_term_family_id(term) for term in retrieval_plan.query_terms]
        )

    def _tried_query_fingerprints(self, *, run_state: RunState) -> list[str]:
        return unique_strings(
            [
                receipt.query_fingerprint
                for receipt in run_state.retrieval_state.query_execution_ledger
                if receipt.dispatch_started
            ]
        )

    def _known_company_entities(self, *, run_state: RunState) -> set[str]:
        entities = {
            *run_state.requirement_sheet.hard_constraints.company_names,
            *run_state.requirement_sheet.preferences.preferred_companies,
        }
        return entities

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
    ) -> tuple[
        list[ProviderQuery], list[SentQueryRecord], list[ResumeCandidate], SearchObservation, list[SearchAttempt]
    ]:
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
            result.executed_queries,
            result.sent_query_records,
            result.new_candidates,
            result.search_observation,
            result.search_attempts,
        )

    async def _execute_search_tool(
        self,
        *,
        round_no: int,
        query: ProviderQuery,
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
        attempt_query: ProviderQuery,
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
