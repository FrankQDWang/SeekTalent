from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from seektalent.config import AppSettings
from seektalent.runtime.source_lanes import (
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
    runtime_source_lane_result_from_source_result,
)
from seektalent.source_contracts import (
    RegisteredSource,
    SourceBudget,
    SourceCapabilities,
    SourceLaneRequest,
    SourcePlan,
    SourceRegistry,
)
from seektalent.sources.liepin.runtime_lane import LiepinWorkerClient, run_liepin_source_lane
from seektalent.sources.provider_card_lane import run_provider_card_lane

if TYPE_CHECKING:
    from seektalent.liepin_cards_source_operation import LiepinCardsSourceOperationExecutor


def build_default_source_registry(
    settings: AppSettings,
    *,
    liepin_operation_executor: "LiepinCardsSourceOperationExecutor | None" = None,
    liepin_worker_client: LiepinWorkerClient | None = None,
) -> SourceRegistry:
    return SourceRegistry(
        [
            _registered_cts_source(settings),
            _registered_liepin_source(
                settings,
                operation_executor=liepin_operation_executor,
                worker_client=liepin_worker_client,
            ),
        ],
        default_source_ids=("liepin",),
    )


def _registered_cts_source(settings: AppSettings) -> RegisteredSource:
    budget = SourceBudget(card_target=10, detail_target=0, scan_limit=10)

    async def run_card_lane(request: RuntimeSourceLaneRequest) -> RuntimeSourceLaneResult:
        from .runtime_factory import _build_provider_retrieval_service

        retrieval_service = _build_provider_retrieval_service(
            settings,
            source_id="cts",
        )
        contract_request = _contract_lane_request(request)
        result = await run_provider_card_lane(
            request=contract_request,
            search=retrieval_service.search,
            provider_context={
                "runtime_source_lane_mode": "cts_single_page",
                "target_new": str(contract_request.budget.card_target),
                "max_pages": "1",
                "allow_pagination": "false",
            },
        )
        return runtime_source_lane_result_from_source_result(result)

    def build_round_adapter(runtime, context):
        from .round_adapters import _run_cts_source_round

        async def run(request):
            return await _run_cts_source_round(
                runtime=runtime,
                context=context,
                request=request,
                source_id="cts",
            )

        return run

    return RegisteredSource(
        source_id="cts",
        label="CTS",
        capabilities=SourceCapabilities(
            supports_card_search=True,
            supports_detail_fetch=False,
            supports_native_filters=True,
            supports_incremental_detail=False,
            requires_human_login=False,
            max_safe_concurrency=1,
            stable_external_id=True,
            stable_dedup_key=True,
        ),
        default_budget=budget,
        plan=_source_plan_builder(source_id="cts", label="CTS", budget=budget),
        run_card_lane=run_card_lane,
        build_round_adapter=build_round_adapter,
    )


def _registered_liepin_source(
    settings: AppSettings,
    *,
    operation_executor: "LiepinCardsSourceOperationExecutor | None" = None,
    worker_client: LiepinWorkerClient | None = None,
) -> RegisteredSource:
    budget = SourceBudget(card_target=30, detail_target=6, scan_limit=30)

    async def run_lane(request: RuntimeSourceLaneRequest) -> RuntimeSourceLaneResult:
        return await run_liepin_source_lane(
            settings=settings,
            request=request,
            worker_client=worker_client,
            cards_operation_executor=operation_executor,
        )

    def build_round_adapter(runtime, context):
        from .round_adapters import _run_liepin_source_round

        async def run(request):
            return await _run_liepin_source_round(
                runtime=runtime,
                context=context,
                request=request,
                source_id="liepin",
                cards_operation_executor=operation_executor,
                worker_client=worker_client,
            )

        return run

    def build_first_page_expander(detail_open_claim_ledger):
        from seektalent.sources.liepin.runtime_lane import run_liepin_first_page_expansion

        async def expand(request):
            return await run_liepin_first_page_expansion(
                settings=settings,
                request=request,
                detail_open_claim_ledger=detail_open_claim_ledger,
                cards_operation_executor=operation_executor,
                worker_client=worker_client,
            )

        return expand

    return RegisteredSource(
        source_id="liepin",
        label="Liepin",
        capabilities=SourceCapabilities(
            supports_card_search=True,
            supports_detail_fetch=True,
            supports_native_filters=True,
            supports_incremental_detail=True,
            requires_human_login=True,
            max_safe_concurrency=1,
            stable_external_id=True,
            stable_dedup_key=True,
        ),
        default_budget=budget,
        plan=_source_plan_builder(source_id="liepin", label="Liepin", budget=budget),
        run_card_lane=run_lane,
        run_detail_lane=run_lane,
        build_round_adapter=build_round_adapter,
        build_first_page_expander=build_first_page_expander,
    )


def _source_plan_builder(*, source_id: str, label: str, budget: SourceBudget):
    def build_plan(
        *,
        runtime_run_id: str,
        source_index: int,
        budget_overrides: Mapping[str, int] | None,
    ) -> SourcePlan:
        selected_budget = _budget_with_overrides(budget, budget_overrides)
        return SourcePlan(
            source_id=source_id,
            source_plan_id=f"{runtime_run_id}:source:{source_index}:{source_id}",
            runtime_run_id=runtime_run_id,
            label=label,
            budget=selected_budget,
        )

    return build_plan


def _budget_with_overrides(
    budget: SourceBudget,
    overrides: Mapping[str, int] | None,
) -> SourceBudget:
    if not overrides:
        return budget
    return SourceBudget(
        card_target=int(overrides.get("card_target", budget.card_target)),
        detail_target=int(overrides.get("detail_target", budget.detail_target)),
        scan_limit=int(overrides.get("scan_limit", budget.scan_limit)),
    )


def _liepin_worker_client(value: object | None) -> LiepinWorkerClient | None:
    if value is None:
        return None
    if isinstance(value, LiepinWorkerClient):
        return value
    raise TypeError("liepin_worker_client_invalid")


def _contract_lane_request(request: RuntimeSourceLaneRequest) -> SourceLaneRequest:
    runtime_run_id = request.runtime_run_id or f"runtime-source-lane:{request.source}"
    source_plan_id = request.source_plan_id or f"{runtime_run_id}:source:0:{request.source}"
    source_lane_run_id = request.source_lane_run_id or f"{source_plan_id}:lane:{request.attempt}"
    return SourceLaneRequest(
        source_id=request.source,
        lane_mode=request.lane_mode,
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        job_title=request.job_title,
        jd=request.jd,
        notes=request.notes,
        requirement_sheet=request.requirement_sheet,
        source_query_terms=request.source_query_terms,
        budget=SourceBudget(
            card_target=request.source_budget_policy.card_target,
            detail_target=request.source_budget_policy.detail_target,
            scan_limit=request.source_budget_policy.scan_limit,
        ),
        attempt=request.attempt,
        progress_callback=request.progress_callback,
    )
