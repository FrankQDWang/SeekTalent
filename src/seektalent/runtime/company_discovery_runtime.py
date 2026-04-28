from __future__ import annotations

from collections.abc import Callable

from seektalent.company_discovery import (
    CompanyDiscoveryService,
    inject_target_company_terms,
    select_company_seed_terms,
)
from seektalent.config import AppSettings
from seektalent.models import ControllerContext, RunState, SearchControllerDecision
from seektalent.progress import ProgressCallback
from seektalent.providers.cts.filter_projection import build_default_filter_plan
from seektalent.runtime.rescue_router import RescueDecision, SkippedRescueLane
from seektalent.tracing import RunTracer


def _round_artifact(
    tracer: RunTracer,
    *,
    round_no: int,
    name: str,
    extension: str = "json",
    content_type: str = "application/json",
) -> str:
    logical_name = f"round.{round_no:02d}.retrieval.{name}"
    tracer.session.register_path(
        logical_name,
        f"rounds/{round_no:02d}/retrieval/{name}.{extension}",
        content_type=content_type,
        schema_version="v1",
    )
    return logical_name


async def continue_after_empty_feedback(
    *,
    settings: AppSettings,
    company_discovery: CompanyDiscoveryService,
    run_state: RunState,
    controller_context: ControllerContext,
    round_no: int,
    tracer: RunTracer,
    rescue_decision: RescueDecision,
    progress_callback: ProgressCallback | None,
    emit_progress: Callable[..., None],
    write_aux_llm_call_artifact: Callable[..., None],
    company_discovery_useful: Callable[[ControllerContext], bool],
    force_anchor_only_decision: Callable[..., SearchControllerDecision],
) -> tuple[RescueDecision, SearchControllerDecision]:
    skipped = [
        *rescue_decision.skipped_lanes,
        SkippedRescueLane(lane="candidate_feedback", reason="no_safe_feedback_term"),
    ]
    if (
        settings.company_discovery_enabled
        and not run_state.retrieval_state.company_discovery_attempted
        and company_discovery_useful(controller_context)
    ):
        company_rescue = rescue_decision.model_copy(
            update={"selected_lane": "web_company_discovery", "skipped_lanes": skipped}
        )
        run_state.retrieval_state.rescue_lane_history[-1]["selected_lane"] = "web_company_discovery"
        company_decision = await force_company_discovery_decision(
            settings=settings,
            company_discovery=company_discovery,
            run_state=run_state,
            round_no=round_no,
            reason=controller_context.stop_guidance.reason,
            tracer=tracer,
            progress_callback=progress_callback,
            emit_progress=emit_progress,
            write_aux_llm_call_artifact=write_aux_llm_call_artifact,
        )
        if company_decision is not None:
            return company_rescue, company_decision
        rescue_decision = company_rescue
    else:
        skipped.append(
            SkippedRescueLane(
                lane="web_company_discovery",
                reason=company_discovery_skip_reason(
                    settings=settings,
                    run_state=run_state,
                    controller_context=controller_context,
                    company_discovery_useful=company_discovery_useful,
                ),
            )
        )
    anchor_rescue = select_anchor_only_after_failed_company_discovery(
        run_state=run_state,
        rescue_decision=rescue_decision.model_copy(update={"skipped_lanes": skipped}),
    )
    return anchor_rescue, force_anchor_only_decision(
        run_state=run_state,
        round_no=round_no,
        reason=controller_context.stop_guidance.reason,
    )


def company_discovery_skip_reason(
    *,
    settings: AppSettings,
    run_state: RunState,
    controller_context: ControllerContext,
    company_discovery_useful: Callable[[ControllerContext], bool],
) -> str:
    if not settings.company_discovery_enabled:
        return "disabled"
    if run_state.retrieval_state.company_discovery_attempted:
        return "already_attempted"
    if not company_discovery_useful(controller_context):
        return "not_useful"
    return "no_usable_company_terms"


def select_anchor_only_after_failed_company_discovery(
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


async def force_company_discovery_decision(
    *,
    settings: AppSettings,
    company_discovery: CompanyDiscoveryService,
    run_state: RunState,
    round_no: int,
    reason: str,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    emit_progress: Callable[..., None],
    write_aux_llm_call_artifact: Callable[..., None],
) -> SearchControllerDecision | None:
    try:
        result = await company_discovery.discover_web(
            requirement_sheet=run_state.requirement_sheet,
            round_no=round_no,
            trigger_reason=reason,
        )
    except Exception:
        for call_artifact in getattr(company_discovery, "last_call_artifacts", []):
            stage = str(call_artifact.get("stage", ""))
            if stage not in {
                "company_discovery_plan",
                "company_discovery_extract",
                "company_discovery_reduce",
            }:
                continue
            write_aux_llm_call_artifact(
                tracer=tracer,
                path=f"round.{round_no:02d}.retrieval.{stage}_call",
                call_artifact=call_artifact,
                input_artifact_refs=["input.requirement_sheet"],
                output_artifact_refs=[],
                round_no=round_no,
            )
        raise
    run_state.retrieval_state.company_discovery_attempted = True
    run_state.retrieval_state.target_company_plan = result.plan.model_dump(mode="json")
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_discovery_result"),
        result.model_dump(mode="json"),
    )
    if result.discovery_input is not None:
        tracer.write_json(
            _round_artifact(tracer, round_no=round_no, name="company_discovery_input"),
            result.discovery_input.model_dump(mode="json"),
        )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_discovery_plan"),
        result.plan.model_dump(mode="json"),
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_search_queries"),
        [item.model_dump(mode="json") for item in result.search_tasks],
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_search_results"),
        [item.model_dump(mode="json") for item in result.search_results],
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_search_rerank"),
        [item.model_dump(mode="json") for item in result.reranked_results],
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_page_reads"),
        [item.model_dump(mode="json") for item in result.page_reads],
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_evidence_cards"),
        [item.model_dump(mode="json") for item in result.evidence_candidates],
    )
    company_discovery_output_refs = {
        "company_discovery_plan": [f"round.{round_no:02d}.retrieval.company_search_queries"],
        "company_discovery_extract": [f"round.{round_no:02d}.retrieval.company_evidence_cards"],
        "company_discovery_reduce": [f"round.{round_no:02d}.retrieval.company_discovery_plan"],
    }
    for call_artifact in getattr(company_discovery, "last_call_artifacts", []):
        stage = str(call_artifact.get("stage", ""))
        if stage not in company_discovery_output_refs:
            continue
        write_aux_llm_call_artifact(
            tracer=tracer,
            path=f"round.{round_no:02d}.retrieval.{stage}_call",
            call_artifact=call_artifact,
            input_artifact_refs=[
                f"round.{round_no:02d}.retrieval.company_discovery_input",
                f"round.{round_no:02d}.retrieval.company_search_results",
                f"round.{round_no:02d}.retrieval.company_page_reads",
            ],
            output_artifact_refs=company_discovery_output_refs[stage],
            round_no=round_no,
        )
    run_state.retrieval_state.query_term_pool = inject_target_company_terms(
        run_state.retrieval_state.query_term_pool,
        result.plan,
        first_added_round=round_no,
        accepted_limit=settings.company_discovery_accepted_company_limit,
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="query_term_pool_after_company_discovery"),
        [item.model_dump(mode="json") for item in run_state.retrieval_state.query_term_pool],
    )
    query_terms = select_company_seed_terms(
        run_state.retrieval_state.query_term_pool,
        run_state.retrieval_state.sent_query_history,
        forced_families=set(),
        max_terms=2,
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="company_discovery_decision"),
        {
            "forced_query_terms": [item.term for item in query_terms],
            "accepted_company_count": len(result.plan.accepted_targets),
            "stop_reason": result.plan.stop_reason,
        },
    )
    emit_progress(
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
