from __future__ import annotations

from seektalent.source_contracts import RegisteredSource, SourceBudget, SourceCapabilities, SourcePlan, SourceRegistry


def test_runtime_control_validates_non_fixture_source_ids_through_registry() -> None:
    from seektalent_runtime_control.source_catalog import validate_runtime_source_ids

    budget = SourceBudget(card_target=3, detail_target=0, scan_limit=3)

    async def run_card_lane(request):  # type: ignore[no-untyped-def]
        raise AssertionError("source validation must not execute source lanes")

    registry = SourceRegistry(
        [
            RegisteredSource(
                source_id="internal_referrals",
                label="Internal Referrals",
                capabilities=SourceCapabilities(
                    supports_card_search=True,
                    supports_detail_fetch=False,
                    supports_native_filters=False,
                    supports_incremental_detail=False,
                    requires_human_login=False,
                    max_safe_concurrency=1,
                    stable_external_id=True,
                    stable_dedup_key=True,
                ),
                default_budget=budget,
                plan=lambda runtime_run_id, source_index, budget_overrides: SourcePlan(
                    source_id="internal_referrals",
                    source_plan_id=f"{runtime_run_id}:source:{source_index}",
                    runtime_run_id=runtime_run_id,
                    label="Internal Referrals",
                    budget=budget,
                ),
                run_card_lane=run_card_lane,
            )
        ],
        default_source_ids=("internal_referrals",),
    )

    selected = validate_runtime_source_ids(registry, ["internal_referrals"])

    assert [source.source_id for source in selected] == ["internal_referrals"]
