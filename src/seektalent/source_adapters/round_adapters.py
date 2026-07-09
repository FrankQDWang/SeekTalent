from __future__ import annotations

from collections.abc import Mapping

import httpx

from seektalent.core.retrieval.provider_contract import ProviderSearchError
from seektalent.models import QueryOutcomeThresholds
from seektalent.runtime.orchestrator import RuntimeSourceRoundContext, WorkflowRuntime
from seektalent.runtime.source_query_intent import (
    RuntimeSourceQueryIntent,
    query_package_from_provider_query,
)
from seektalent.runtime.source_round_dispatch import (
    SourceRoundAdapter,
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
    SourceRoundDispatchStatus,
)
from seektalent.sources.cts.filter_projection import project_constraints_to_cts
from seektalent.sources.liepin.reason_codes import LIEPIN_PUBLIC_EVENT_REASON_MAP

from .evidence import _record_source_provider_results_from_lane, _source_lane_result_from_retrieval_result

_SOURCE_ROUND_STATUSES: dict[str, SourceRoundDispatchStatus] = {
    "blocked": "blocked", "completed": "completed", "failed": "failed", "partial": "partial"
}


def default_source_round_adapter_provider(
    runtime: WorkflowRuntime,
    context: RuntimeSourceRoundContext,
) -> Mapping[str, SourceRoundAdapter]:
    adapters: dict[str, SourceRoundAdapter] = {}
    for source_id in context.source_plan_by_source:
        if source_id == "cts":
            adapters[source_id] = lambda request, source_id=source_id: _run_cts_source_round(
                runtime=runtime,
                context=context,
                request=request,
                source_id=source_id,
            )
        elif source_id == "liepin":
            adapters[source_id] = lambda request, source_id=source_id: _run_liepin_source_round(
                runtime=runtime,
                context=context,
                request=request,
                source_id=source_id,
            )
    return adapters


async def _run_cts_source_round(
    *,
    runtime: WorkflowRuntime,
    context: RuntimeSourceRoundContext,
    request: SourceRoundDispatchRequest,
    source_id: str,
) -> SourceRoundAdapterResult:
    source_plan = context.source_plan_by_source[source_id]
    projection_result = project_constraints_to_cts(
        requirement_sheet=context.run_state.requirement_sheet,
        filter_plan=context.proposed_filter_plan,
    )
    retrieval_plan = context.retrieval_plan.model_copy(
        update={
            "projected_provider_filters": projection_result.provider_filters,
            "runtime_only_constraints": projection_result.runtime_only_constraints,
        }
    )
    try:
        result = await runtime.retrieval_runtime.execute_logical_dispatch_search(
            round_no=context.round_no,
            retrieval_plan=retrieval_plan,
            logical_queries=request.logical_queries,
            base_adapter_notes=[*context.adapter_notes, *projection_result.adapter_notes],
            target_new=context.target_new,
            seen_resume_ids=set(context.seen_resume_ids),
            seen_dedup_keys=set(context.seen_dedup_keys),
            tracer=context.tracer,
            score_for_query_outcome=lambda candidates: runtime._score_candidates_for_query_outcome(
                round_no=context.round_no,
                candidates=candidates,
                run_state=context.run_state,
                runtime_only_constraints=retrieval_plan.runtime_only_constraints,
            ),
            query_outcome_thresholds=QueryOutcomeThresholds(),
            record_provider_return_batch=lambda batch: runtime._record_corpus_provider_results(
                tracer=context.tracer,
                returned_candidates=batch,
            ),
        )
    except ProviderSearchError as exc:
        return SourceRoundAdapterResult(
            source=source_id,
            status="failed",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code=exc.reason_code,
            diagnostics=(exc.safe_message,),
        )
    except (TimeoutError, httpx.HTTPError):
        return SourceRoundAdapterResult(
            source=source_id,
            status="failed",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="source_provider_failed",
            diagnostics=(f"{source_id} provider request failed before completion",),
        )
    lane_result = _source_lane_result_from_retrieval_result(
        source_id=source_id,
        source_plan=source_plan,
        retrieval_result=result,
        round_no=context.round_no,
        runtime_run_id=context.tracer.run_id,
        logical_queries=request.logical_queries,
    )
    return SourceRoundAdapterResult(
        source=source_id,
        status="completed",
        candidates=tuple(result.new_candidates),
        raw_candidate_count=result.search_observation.raw_candidate_count,
        diagnostics=tuple(result.search_observation.adapter_notes),
        retrieval_result=result,
        lane_result=lane_result,
        executed_query_packages=tuple(
            query_package_from_provider_query(source_kind=source_id, query=query)
            for query in result.executed_queries
        ),
    )


async def _run_liepin_source_round(
    *,
    runtime: WorkflowRuntime,
    context: RuntimeSourceRoundContext,
    request: SourceRoundDispatchRequest,
    source_id: str,
) -> SourceRoundAdapterResult:
    source_plan = context.source_plan_by_source[source_id]
    safe_posture = dict(source_plan.safe_posture)
    if source_plan.backend_mode == "blocked" or safe_posture.get("status") == "blocked" or context.source_context is None:
        safe_reason_code = str(
            safe_posture.get("safe_reason_code")
            or safe_posture.get("reason")
            or "source_browser_backend_unavailable"
        )
        return SourceRoundAdapterResult(
            source=source_id,
            status="blocked",
            safe_reason_code=safe_reason_code,
            diagnostics=(f"{source_id} source blocked before provider dispatch",),
        )
    from seektalent import source_adapters as source_adapters_facade

    result = await source_adapters_facade.run_liepin_logical_query_bundle(
        settings=runtime.settings,
        runtime_run_id=context.tracer.run_id,
        source_plan_id=source_plan.source_plan_id,
        job_title=str(getattr(context.run_state.input_truth, "job_title", "")),
        jd=str(getattr(context.run_state.input_truth, "jd", "")),
        notes=str(getattr(context.run_state.input_truth, "notes", "") or ""),
        requirement_sheet=request.requirement_sheet,
        logical_queries=request.logical_queries,
        source_query_intents=request.source_query_intents_by_source.get(source_id),
        source_budget_policy=source_plan.source_budget_policy,
        liepin_context=context.source_context,
    )
    _record_source_provider_results_from_lane(
        runtime=runtime,
        source_id=source_id,
        result=result,
        logical_queries=request.logical_queries,
        tracer=context.tracer,
    )
    filter_warning_reason = _source_filter_warning_reason(request.source_query_intents_by_source.get(source_id, ()))
    return SourceRoundAdapterResult(
        source=source_id,
        status=_source_round_status(result.status),
        candidates=tuple(result.candidate_store_updates.values()),
        raw_candidate_count=int(result.raw_candidate_count or 0),
        safe_reason_code=_public_liepin_reason_code(
            result.stop_reason_code or result.blocked_reason_code or filter_warning_reason
        ),
        diagnostics=((result.safe_error_summary,) if result.safe_error_summary else ()),
        lane_result=result,
        executed_query_packages=result.executed_query_packages,
    )


def _public_liepin_reason_code(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    text = str(reason_code).strip()
    if not text:
        return None
    return LIEPIN_PUBLIC_EVENT_REASON_MAP.get(text, text)


def _source_filter_warning_reason(intents: tuple[RuntimeSourceQueryIntent, ...]) -> str | None:
    supported_filter_fields = {
        "degree_requirement",
        "school_type_requirement",
        "experience_requirement",
        "age_requirement",
    }
    if any(
        filter_intent.field not in supported_filter_fields for intent in intents for filter_intent in intent.filter_intents
    ):
        return "source_filter_unsupported"
    return None


def _source_round_status(status: str) -> SourceRoundDispatchStatus:
    return _SOURCE_ROUND_STATUSES.get(status, "failed")
