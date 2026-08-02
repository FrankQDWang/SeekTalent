from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from seektalent.runtime.orchestrator import RuntimeSourceRoundContext, WorkflowRuntime
from seektalent.runtime.source_round_dispatch import SourceRoundAdapter
from seektalent.source_contracts import (
    RegisteredSource,
    SourceBudget,
    SourceCapabilities,
    SourcePlan,
    SourceRegistry,
)
from seektalent.source_contracts.first_page_expansion import SourceFirstPageExpander


RoundAdapterProvider = Callable[
    [WorkflowRuntime, RuntimeSourceRoundContext],
    Mapping[str, SourceRoundAdapter],
]
FirstPageExpanderProvider = Callable[
    [WorkflowRuntime, object],
    Mapping[str, SourceFirstPageExpander],
]


def install_source_execution_fakes(
    runtime: WorkflowRuntime,
    *,
    source_ids: Sequence[str],
    round_adapter_provider: RoundAdapterProvider,
    first_page_expander_provider: FirstPageExpanderProvider | None = None,
    first_page_source_ids: Sequence[str] = (),
) -> None:
    adapter_cache: tuple[int, Mapping[str, SourceRoundAdapter]] | None = None
    expander_cache: tuple[int, Mapping[str, SourceFirstPageExpander]] | None = None

    def registered(source_id: str) -> RegisteredSource:
        budget = SourceBudget(card_target=10, detail_target=6, scan_limit=30)

        def plan(
            *,
            runtime_run_id: str,
            source_index: int,
            budget_overrides: Mapping[str, int] | None,
        ) -> SourcePlan:
            del budget_overrides
            return SourcePlan(
                source_id=source_id,
                source_plan_id=f"{runtime_run_id}:source:{source_index}:{source_id}",
                runtime_run_id=runtime_run_id,
                label=source_id,
                budget=budget,
            )

        async def run_lane(_request):
            raise AssertionError(f"unexpected_direct_source_lane:{source_id}")

        def build_round_adapter(
            current_runtime: WorkflowRuntime,
            context: RuntimeSourceRoundContext,
        ) -> SourceRoundAdapter:
            nonlocal adapter_cache
            cache_key = id(context)
            if adapter_cache is None or adapter_cache[0] != cache_key:
                adapter_cache = (
                    cache_key,
                    round_adapter_provider(current_runtime, context),
                )
            return adapter_cache[1][source_id]

        def build_first_page_expander(ledger: object) -> SourceFirstPageExpander:
            nonlocal expander_cache
            if first_page_expander_provider is None:
                raise AssertionError("unexpected_first_page_expander")
            cache_key = id(ledger)
            if expander_cache is None or expander_cache[0] != cache_key:
                expander_cache = (
                    cache_key,
                    first_page_expander_provider(runtime, ledger),
                )
            return expander_cache[1][source_id]

        return RegisteredSource(
            source_id=source_id,
            label=source_id,
            capabilities=SourceCapabilities(
                supports_card_search=True,
                supports_detail_fetch=source_id in first_page_source_ids,
                supports_native_filters=False,
                supports_incremental_detail=source_id in first_page_source_ids,
                requires_human_login=False,
                max_safe_concurrency=1,
                stable_external_id=True,
                stable_dedup_key=True,
            ),
            default_budget=budget,
            plan=plan,
            run_card_lane=run_lane,
            run_detail_lane=run_lane if source_id in first_page_source_ids else None,
            build_round_adapter=build_round_adapter,
            build_first_page_expander=(
                build_first_page_expander
                if source_id in first_page_source_ids
                else None
            ),
        )

    runtime.source_registry = SourceRegistry(
        [registered(source_id) for source_id in source_ids],
        default_source_ids=tuple(source_ids),
    )
