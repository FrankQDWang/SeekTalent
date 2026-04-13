from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from seektalent.bootstrap import bootstrap_round0_async
from seektalent.bootstrap_assets import BootstrapAssets, default_bootstrap_assets
from seektalent.clients.cts_client import CTSClient, CTSClientProtocol, MockCTSClient
from seektalent.config import AppSettings
from seektalent.controller_llm import request_search_controller_decision_draft
from seektalent.frontier_ops import (
    carry_forward_frontier_state,
    generate_search_controller_decision_with_trace,
    select_active_frontier_node,
)
from seektalent.models import (
    BusinessPolicySnapshot,
    FrontierState_t,
    RuntimeRoundState,
    SearchRoundArtifact,
    SearchRunBootstrapArtifact,
    SearchRunBundle,
)
from seektalent.progress import ProgressCallback, emit_progress
from seektalent.run_artifacts import (
    RUNTIME_STATUS,
    build_run_id,
    build_search_run_eval,
    utc_isoformat,
    utc_now,
    write_run_bundle,
)
from seektalent.runtime_llm import (
    request_branch_evaluation_draft,
    request_search_run_summary_draft,
)
from seektalent.runtime_budget import (
    build_runtime_budget_state,
    resolve_runtime_search_budget,
)
from seektalent.runtime_ops import (
    build_effective_stop_guard,
    compute_node_reward_breakdown,
    evaluate_branch_outcome,
    evaluate_stop_condition,
    finalize_search_run,
    update_frontier_state,
)
from seektalent.rewrite_evidence import build_rewrite_term_pool
from seektalent.search_ops import (
    AsyncRerankRequest,
    execute_search_plan_sidecar,
    materialize_search_execution_plan,
    score_search_results,
)
from seektalent_rerank.models import RerankRequest, RerankResponse


class WorkflowRuntime:
    def __init__(
        self,
        settings: AppSettings,
        *,
        env_file: str | Path | None = ".env",
        assets: BootstrapAssets | None = None,
        cts_client: CTSClientProtocol | None = None,
        rerank_request: AsyncRerankRequest | None = None,
        requirement_extraction_model: Any | None = None,
        bootstrap_keyword_generation_model: Any | None = None,
        search_controller_decision_model: Any | None = None,
        branch_outcome_evaluation_model: Any | None = None,
        search_run_finalization_model: Any | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.settings = settings
        self.env_file = env_file
        self.assets = assets
        self.cts_client = cts_client
        self.rerank_request = rerank_request
        self.requirement_extraction_model = requirement_extraction_model
        self.bootstrap_keyword_generation_model = bootstrap_keyword_generation_model
        self.search_controller_decision_model = search_controller_decision_model
        self.branch_outcome_evaluation_model = branch_outcome_evaluation_model
        self.search_run_finalization_model = search_run_finalization_model
        self.progress_callback = progress_callback

    def run(
        self,
        *,
        job_description: str,
        hiring_notes: str = "",
        top_k: int = 10,
        round_budget: int | None = None,
    ) -> SearchRunBundle:
        return asyncio.run(
            self.run_async(
                job_description=job_description,
                hiring_notes=hiring_notes,
                top_k=top_k,
                round_budget=round_budget,
            )
        )

    async def run_async(
        self,
        *,
        job_description: str,
        hiring_notes: str = "",
        top_k: int = 10,
        round_budget: int | None = None,
    ) -> SearchRunBundle:
        try:
            if top_k < 1 or top_k > 10:
                raise ValueError("top_k must be between 1 and 10")
            created_at_utc = utc_now()
            active_assets = self.assets or default_bootstrap_assets()
            active_assets = replace(
                active_assets,
                runtime_search_budget=resolve_runtime_search_budget(
                    active_assets.runtime_search_budget,
                    round_budget
                    if round_budget is not None
                    else self.settings.round_budget,
                ),
            )
            active_cts_client = self.cts_client or _default_cts_client(self.settings)
            active_rerank_request = self.rerank_request or _build_http_rerank_request(
                self.settings
            )
            emit_progress(
                self.progress_callback,
                "phase_started",
                "bootstrap: normalize requirements and build the round-0 frontier",
                payload={"phase": "bootstrap"},
            )
            bootstrap_artifacts = await bootstrap_round0_async(
                job_description=job_description,
                hiring_notes=hiring_notes,
                assets=active_assets,
                rerank_request=active_rerank_request,
                requirement_extraction_model=self.requirement_extraction_model,
                bootstrap_keyword_generation_model=self.bootstrap_keyword_generation_model,
                env_file=self.env_file,
            )
            emit_progress(
                self.progress_callback,
                "phase_completed",
                (
                    "bootstrap complete: "
                    f"routing_mode={bootstrap_artifacts.routing_result.routing_mode}, "
                    "seed_count="
                    f"{len(bootstrap_artifacts.bootstrap_output.frontier_seed_specifications)}"
                ),
                payload={
                    "phase": "bootstrap",
                    "routing_mode": bootstrap_artifacts.routing_result.routing_mode,
                    "seed_count": len(
                        bootstrap_artifacts.bootstrap_output.frontier_seed_specifications
                    ),
                },
            )
            run_id = build_run_id(
                job_description_sha256=bootstrap_artifacts.input_truth.job_description_sha256,
                created_at_utc=created_at_utc,
            )
            bootstrap = SearchRunBootstrapArtifact(
                input_truth=bootstrap_artifacts.input_truth,
                requirement_extraction_audit=bootstrap_artifacts.requirement_extraction_audit,
                requirement_sheet=bootstrap_artifacts.requirement_sheet,
                business_policy_snapshot=BusinessPolicySnapshot(
                    policy_id=active_assets.policy_id,
                    policy_pack=active_assets.business_policy_pack,
                ),
                runtime_search_budget=active_assets.runtime_search_budget,
                routing_result=bootstrap_artifacts.routing_result,
                scoring_policy=bootstrap_artifacts.scoring_policy,
                bootstrap_keyword_generation_audit=bootstrap_artifacts.bootstrap_keyword_generation_audit,
                bootstrap_output=bootstrap_artifacts.bootstrap_output,
                frontier_state=bootstrap_artifacts.frontier_state,
            )
            frontier_state = bootstrap_artifacts.frontier_state
            runtime_round_state = RuntimeRoundState(runtime_round_index=0)
            rounds: list[SearchRoundArtifact] = []

            while True:
                frontier_state_before = FrontierState_t.model_validate(
                    frontier_state.model_dump(mode="python")
                )
                runtime_budget_state = build_runtime_budget_state(
                    initial_round_budget=active_assets.runtime_search_budget.initial_round_budget,
                    runtime_round_index=runtime_round_state.runtime_round_index,
                    remaining_budget=frontier_state.remaining_budget,
                )
                controller_context = select_active_frontier_node(
                    frontier_state,
                    bootstrap_artifacts.requirement_sheet,
                    bootstrap_artifacts.scoring_policy,
                    active_assets.crossover_guard_thresholds,
                    active_assets.runtime_term_budget_policy,
                    runtime_budget_state,
                    active_assets.runtime_selection_policy,
                )
                emit_progress(
                    self.progress_callback,
                    "round_started",
                    (
                        f"round {runtime_round_state.runtime_round_index + 1}/"
                        f"{active_assets.runtime_search_budget.initial_round_budget}: "
                        "evaluate active frontier node"
                    ),
                    round_index=runtime_round_state.runtime_round_index,
                    payload={
                        "remaining_budget": frontier_state.remaining_budget,
                        "search_phase": runtime_budget_state.search_phase,
                    },
                )
                controller_draft, controller_audit = await request_search_controller_decision_draft(
                    controller_context,
                    rewrite_fitness_weights=active_assets.rewrite_fitness_weights,
                    model=self.search_controller_decision_model,
                    env_file=self.env_file,
                )
                controller_decision, rewrite_choice_trace = generate_search_controller_decision_with_trace(
                    controller_context,
                    controller_draft,
                    active_assets.rewrite_fitness_weights,
                )
                emit_progress(
                    self.progress_callback,
                    "controller_decision",
                    f"controller: selected {controller_decision.selected_operator_name}",
                    round_index=runtime_round_state.runtime_round_index,
                    payload={
                        "selected_operator_name": controller_decision.selected_operator_name,
                        "action": controller_decision.action,
                    },
                )
                if controller_decision.action == "stop":
                    frontier_state_t1 = carry_forward_frontier_state(frontier_state)
                    effective_stop_guard = build_effective_stop_guard(
                        active_assets.stop_guard_thresholds,
                        runtime_budget_state,
                    )
                    stop_reason, continue_flag = evaluate_stop_condition(
                        frontier_state_t1,
                        controller_decision.action,
                        None,
                        None,
                        active_assets.stop_guard_thresholds,
                        runtime_budget_state,
                    )
                    rounds.append(
                        SearchRoundArtifact(
                            runtime_round_index=runtime_round_state.runtime_round_index,
                            frontier_state_before=frontier_state_before,
                            controller_context=controller_context,
                            controller_draft=controller_draft,
                            controller_audit=controller_audit,
                            controller_decision=controller_decision,
                            rewrite_choice_trace=rewrite_choice_trace,
                            effective_stop_guard=effective_stop_guard,
                            frontier_state_after=frontier_state_t1,
                            stop_reason=stop_reason,
                            continue_flag=continue_flag,
                        )
                    )
                else:
                    execution_plan = materialize_search_execution_plan(
                        frontier_state,
                        bootstrap_artifacts.requirement_sheet,
                        controller_decision,
                        controller_context.max_query_terms,
                        active_assets.runtime_search_budget,
                        active_assets.crossover_guard_thresholds,
                    )
                    execution_sidecar = await execute_search_plan_sidecar(
                        execution_plan,
                        active_cts_client,
                    )
                    execution_result = execution_sidecar.execution_result
                    emit_progress(
                        self.progress_callback,
                        "cts_fetch_completed",
                        (
                            "cts: fetched "
                            f"{len(execution_result.deduplicated_candidates)} candidates across "
                            f"{execution_result.search_page_statistics.pages_fetched} pages"
                        ),
                        round_index=runtime_round_state.runtime_round_index,
                        payload={
                            "candidate_count": len(execution_result.deduplicated_candidates),
                            "pages_fetched": execution_result.search_page_statistics.pages_fetched,
                        },
                    )
                    scoring_result = await score_search_results(
                        execution_result,
                        bootstrap_artifacts.scoring_policy,
                        active_rerank_request,
                    )
                    emit_progress(
                        self.progress_callback,
                        "rerank_completed",
                        (
                            "rerank: scored "
                            f"{len(scoring_result.scored_candidates)} candidates, "
                            f"{len(scoring_result.explanation_candidate_ids)} explanations"
                        ),
                        round_index=runtime_round_state.runtime_round_index,
                        payload={
                            "scored_candidate_count": len(scoring_result.scored_candidates),
                            "explanation_candidate_count": len(scoring_result.explanation_candidate_ids),
                        },
                    )
                    rewrite_term_pool = build_rewrite_term_pool(
                        bootstrap_artifacts.requirement_sheet,
                        execution_plan,
                        execution_result,
                        scoring_result,
                    )
                    branch_evaluation_draft, branch_evaluation_audit = await request_branch_evaluation_draft(
                        bootstrap_artifacts.requirement_sheet,
                        frontier_state,
                        execution_plan,
                        execution_result,
                        scoring_result,
                        runtime_budget_state,
                        model=self.branch_outcome_evaluation_model,
                        env_file=self.env_file,
                    )
                    branch_evaluation = evaluate_branch_outcome(
                        bootstrap_artifacts.requirement_sheet,
                        frontier_state,
                        execution_plan,
                        execution_result,
                        scoring_result,
                        branch_evaluation_draft,
                    )
                    reward_breakdown = compute_node_reward_breakdown(
                        frontier_state,
                        execution_plan,
                        execution_result,
                        scoring_result,
                        branch_evaluation,
                    )
                    frontier_state_t1 = update_frontier_state(
                        frontier_state,
                        execution_plan,
                        scoring_result,
                        branch_evaluation,
                        reward_breakdown,
                        rewrite_term_pool.accepted,
                    )
                    emit_progress(
                        self.progress_callback,
                        "reviewer_cards_completed",
                        (
                            "reviewer: built "
                            f"{len(scoring_result.candidate_evidence_cards)} candidate cards; "
                            f"run shortlist size={len(frontier_state_t1.run_shortlist_candidate_ids)}"
                        ),
                        round_index=runtime_round_state.runtime_round_index,
                        payload={
                            "candidate_card_count": len(scoring_result.candidate_evidence_cards),
                            "run_shortlist_count": len(frontier_state_t1.run_shortlist_candidate_ids),
                        },
                    )
                    effective_stop_guard = build_effective_stop_guard(
                        active_assets.stop_guard_thresholds,
                        runtime_budget_state,
                    )
                    stop_reason, continue_flag = evaluate_stop_condition(
                        frontier_state_t1,
                        controller_decision.action,
                        branch_evaluation,
                        reward_breakdown,
                        active_assets.stop_guard_thresholds,
                        runtime_budget_state,
                    )
                    rounds.append(
                        SearchRoundArtifact(
                            runtime_round_index=runtime_round_state.runtime_round_index,
                            frontier_state_before=frontier_state_before,
                            controller_context=controller_context,
                            controller_draft=controller_draft,
                            controller_audit=controller_audit,
                            controller_decision=controller_decision,
                            execution_plan=execution_plan,
                            execution_result=execution_result,
                            runtime_audit_tags=execution_sidecar.runtime_audit_tags,
                            rewrite_term_pool=rewrite_term_pool,
                            rewrite_choice_trace=rewrite_choice_trace,
                            scoring_result=scoring_result,
                            branch_evaluation_draft=branch_evaluation_draft,
                            branch_evaluation_audit=branch_evaluation_audit,
                            branch_evaluation=branch_evaluation,
                            reward_breakdown=reward_breakdown,
                            effective_stop_guard=effective_stop_guard,
                            frontier_state_after=frontier_state_t1,
                            stop_reason=stop_reason,
                            continue_flag=continue_flag,
                        )
                    )
                if continue_flag:
                    frontier_state = FrontierState_t.model_validate(
                        frontier_state_t1.model_dump(mode="python")
                    )
                    runtime_round_state = RuntimeRoundState(
                        runtime_round_index=runtime_round_state.runtime_round_index + 1
                    )
                    continue
                if stop_reason is None:
                    raise ValueError("stop_reason must not be null when continue_flag is false")
                emit_progress(
                    self.progress_callback,
                    "phase_started",
                    "finalize: summarize the run and build final candidate cards",
                    payload={"phase": "finalization"},
                )
                run_summary_draft, finalization_audit = await request_search_run_summary_draft(
                    bootstrap_artifacts.requirement_sheet,
                    frontier_state_t1,
                    rounds,
                    stop_reason,
                    model=self.search_run_finalization_model,
                    env_file=self.env_file,
                )
                final_result = finalize_search_run(
                    bootstrap_artifacts.requirement_sheet,
                    frontier_state_t1,
                    rounds,
                    stop_reason,
                    run_summary_draft,
                    top_k=top_k,
                )
                emit_progress(
                    self.progress_callback,
                    "finalization_completed",
                    (
                        "finalize: built "
                        f"{len(final_result.final_candidate_cards)} final candidate cards"
                    ),
                    payload={
                        "final_candidate_count": len(final_result.final_candidate_cards),
                    },
                )
                bundle = SearchRunBundle(
                    phase=RUNTIME_STATUS,
                    run_id=run_id,
                    run_dir=str(self.settings.runs_path / run_id),
                    created_at_utc=utc_isoformat(created_at_utc),
                    bootstrap=bootstrap,
                    rounds=rounds,
                    finalization_audit=finalization_audit,
                    final_result=final_result,
                )
                bundle = bundle.model_copy(update={"eval": build_search_run_eval(bundle)})
                write_run_bundle(bundle, runs_root=self.settings.runs_path)
                emit_progress(
                    self.progress_callback,
                    "run_completed",
                    f"completed: {final_result.stop_reason}",
                    payload={
                        "run_dir": bundle.run_dir,
                        "stop_reason": final_result.stop_reason,
                        "final_candidate_count": len(final_result.final_candidate_cards),
                    },
                )
                return bundle
        except Exception as exc:
            emit_progress(
                self.progress_callback,
                "run_failed",
                f"failed: {exc}",
                payload={"error_type": type(exc).__name__},
            )
            raise


def _default_cts_client(settings: AppSettings) -> CTSClientProtocol:
    if settings.mock_cts:
        return MockCTSClient(settings)
    return CTSClient(settings)


def _build_http_rerank_request(settings: AppSettings) -> AsyncRerankRequest:
    async def _request(request: RerankRequest) -> RerankResponse:
        async with httpx.AsyncClient(
            base_url=settings.rerank_base_url,
            timeout=settings.rerank_timeout_seconds,
        ) as client:
            response = await client.post(
                "/api/rerank",
                json=request.model_dump(mode="json"),
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"rerank_request_failed: status={response.status_code}, body={response.text}"
            )
        return RerankResponse.model_validate(response.json())

    return _request
