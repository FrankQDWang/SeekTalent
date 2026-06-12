from __future__ import annotations

from collections.abc import Mapping

from seektalent.config import AppSettings
from seektalent.runtime.source_lanes import RuntimeSourceLaneRequest, RuntimeSourceLaneResult
from seektalent.source_contracts import (
    RegisteredSource,
    SourceBudget,
    SourceCapabilities,
    SourceLaneRequest,
    SourceLaneResult,
    SourcePlan,
    SourceRegistry,
)
from seektalent.sources.liepin.runtime_lane import LiepinWorkerClient, run_liepin_source_lane
from seektalent.sources.provider_card_lane import run_provider_card_lane

def build_default_source_registry(settings: AppSettings) -> SourceRegistry:
    return SourceRegistry(
        [
            _registered_cts_source(settings),
            _registered_liepin_source(),
        ],
        default_source_ids=("cts",),
    )


def build_source_lane_request_runner(settings: AppSettings):
    async def run_source_lane_request(
        request: RuntimeSourceLaneRequest,
        source_client: object | None,
    ) -> RuntimeSourceLaneResult:
        return await run_liepin_source_lane(
            settings=settings,
            request=request,
            worker_client=_liepin_worker_client(source_client),
        )

    return run_source_lane_request


def _registered_cts_source(settings: AppSettings) -> RegisteredSource:
    budget = SourceBudget(card_target=10, detail_target=0, scan_limit=10)

    async def run_card_lane(request: SourceLaneRequest) -> SourceLaneResult:
        from .runtime_factory import _build_provider_retrieval_service

        retrieval_service = _build_provider_retrieval_service(settings)
        return await run_provider_card_lane(
            request=request,
            search=retrieval_service.search,
            provider_context={
                "runtime_source_lane_mode": "cts_single_page",
                "target_new": str(request.budget.card_target),
                "max_pages": "1",
                "allow_pagination": "false",
            },
        )

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
    )


def _registered_liepin_source() -> RegisteredSource:
    budget = SourceBudget(card_target=30, detail_target=6, scan_limit=30)

    async def run_card_lane(request: SourceLaneRequest) -> SourceLaneResult:
        return SourceLaneResult(
            runtime_run_id=request.runtime_run_id,
            source_plan_id=request.source_plan_id,
            source_lane_run_id=request.source_lane_run_id,
            source_id=request.source_id,
            lane_mode=request.lane_mode,
            attempt=request.attempt,
            status="blocked",
            blocked_reason_code="source_context_required",
        )

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
        run_card_lane=run_card_lane,
        run_detail_lane=run_card_lane,
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
