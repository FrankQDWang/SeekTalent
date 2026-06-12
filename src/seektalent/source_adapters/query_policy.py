from __future__ import annotations

from collections.abc import Mapping

from seektalent.config import AppSettings
from seektalent.runtime.source_lanes import RuntimeSourceLanePlan
from seektalent.runtime.source_query_intent import RuntimeSourceQueryPolicy

def default_source_query_policies(
    *,
    settings: AppSettings,
    source_plan: tuple[RuntimeSourceLanePlan, ...],
) -> Mapping[str, RuntimeSourceQueryPolicy]:
    policies: dict[str, RuntimeSourceQueryPolicy] = {}
    for lane in source_plan:
        if lane.source == "liepin":
            policies[lane.source] = _liepin_source_query_policy(settings)
    return policies


def _liepin_source_query_policy(settings: AppSettings) -> RuntimeSourceQueryPolicy:
    return RuntimeSourceQueryPolicy(
        requested_count_caps_by_lane={
            "exploit": settings.liepin_exploit_detail_target,
            "generic_explore": settings.liepin_explore_detail_target,
        },
        provider_scan_multiplier=3,
        provider_scan_cap=settings.liepin_opencli_max_cards_per_task,
    )
