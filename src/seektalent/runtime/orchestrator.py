from __future__ import annotations

import asyncio
from dataclasses import replace
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
        assets: BootstrapAssets | None = None,
        cts_client: CTSClientProtocol | None = None,
        rerank_request: AsyncRerankRequest | None = None,
        requirement_extraction_model: Any | None = None,
        bootstrap_keyword_generation_model: Any | None = None,
        search_controller_decision_model: Any | None = None,
        branch_outcome_evaluation_model: Any | None = None,
        search_run_finalization_model: Any | None = None,
    ) -> None:
        self.settings = settings
        self.assets = assets
        self.cts_client = cts_client
        self.rerank_request = rerank_request
        self.requirement_extraction_model = requirement_extraction_model
        self.bootstrap_keyword_generation_model = bootstrap_keyword_generation_model
        self.search_controller_decision_model = search_controller_decision_model
        self.branch_outcome_evaluation_model = branch_outcome_evaluation_model
        self.search_run_finalization_model = search_run_finalization_model

    def run(
        self,
        *,
        job_description: str,
        hiring_notes: str = "",
        round_budget: int | None = None,
    ) -> SearchRunBundle:
        return asyncio.run(
            self.run_async(
                job_description=job_description,
                hiring_notes=hiring_notes,
                round_budget=round_budget,
            )
        )

    async def run_async(
        self,
        *,
        job_description: str,
        hiring_notes: str = "",
        round_budget: int | None = None,
    ) -> SearchRunBundle:
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
        bootstrap_artifacts = await bootstrap_round0_async(
            job_description=job_description,
            hiring_notes=hiring_notes,
            assets=active_assets,
            rerank_request=active_rerank_request,
            requirement_extraction_model=self.requirement_extraction_model,
            bootstrap_keyword_generation_model=self.bootstrap_keyword_generation_model,
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
            controller_draft, controller_audit = await request_search_controller_decision_draft(
                controller_context,
                rewrite_fitness_weights=active_assets.rewrite_fitness_weights,
                model=self.search_controller_decision_model,
            )
            controller_decision, rewrite_choice_trace = generate_search_controller_decision_with_trace(
                controller_context,
                controller_draft,
                active_assets.rewrite_fitness_weights,
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
                scoring_result = await score_search_results(
                    execution_result,
                    bootstrap_artifacts.scoring_policy,
                    active_rerank_request,
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
            run_summary_draft, finalization_audit = await request_search_run_summary_draft(
                bootstrap_artifacts.requirement_sheet,
                frontier_state_t1,
                rounds,
                stop_reason,
                model=self.search_run_finalization_model,
            )
            final_result = finalize_search_run(
                bootstrap_artifacts.requirement_sheet,
                frontier_state_t1,
                stop_reason,
                run_summary_draft,
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
            return bundle


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
