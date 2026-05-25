from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from seektalent.models import LaneType, QueryRole, RuntimeSourceKind
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.source_filters import (
    RuntimeAgeExecutionIntent,
    RuntimeFilterIntent,
    RuntimeLocationExecutionIntent,
)
from seektalent.runtime.source_lanes import RuntimeSourceBudgetPolicy

SourceSearchAction = Literal["source_search", "stop"]


@dataclass(frozen=True)
class RuntimeSourceQueryIntent:
    round_no: int
    source_kind: RuntimeSourceKind
    query_role: QueryRole
    lane_type: LaneType
    query_instance_id: str
    query_fingerprint: str
    query_terms: tuple[str, ...]
    keyword_query: str
    requested_count: int
    provider_scan_limit: int
    source_plan_version: str
    filter_intents: tuple[RuntimeFilterIntent, ...]
    location_intent: RuntimeLocationExecutionIntent | None
    age_intent: RuntimeAgeExecutionIntent | None
    must_have_capabilities: tuple[str, ...] = ()
    preferred_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.requested_count < 0:
            raise ValueError("runtime_source_query_intent_negative_requested_count")
        if self.provider_scan_limit < 0:
            raise ValueError("runtime_source_query_intent_negative_provider_scan_limit")
        if not self.query_instance_id:
            raise ValueError("runtime_source_query_intent_missing_query_instance_id")
        if not self.query_fingerprint:
            raise ValueError("runtime_source_query_intent_missing_query_fingerprint")
        if self.source_kind not in {"cts", "liepin"}:
            raise ValueError(f"runtime_source_query_intent_unsupported_source:{self.source_kind}")


def normalize_source_search_action(action: str) -> SourceSearchAction:
    if action == "search_cts":
        return "source_search"
    if action == "stop":
        return "stop"
    raise ValueError(f"unsupported_controller_action:{action}")


def build_runtime_source_query_intents(
    *,
    source_kinds: tuple[RuntimeSourceKind, ...],
    logical_dispatches: tuple[LogicalQueryDispatch, ...],
    filter_intents: tuple[RuntimeFilterIntent, ...],
    location_intent: RuntimeLocationExecutionIntent | None,
    age_intent: RuntimeAgeExecutionIntent | None,
    source_budget_policy: RuntimeSourceBudgetPolicy | Mapping[RuntimeSourceKind, RuntimeSourceBudgetPolicy],
    must_have_capabilities: tuple[str, ...] = (),
    preferred_capabilities: tuple[str, ...] = (),
) -> Mapping[RuntimeSourceKind, tuple[RuntimeSourceQueryIntent, ...]]:
    intents_by_source: dict[RuntimeSourceKind, tuple[RuntimeSourceQueryIntent, ...]] = {}
    for source_kind in source_kinds:
        if source_kind not in {"cts", "liepin"}:
            raise ValueError(f"runtime_source_query_intent_unsupported_source:{source_kind}")
        budget_policy = _budget_policy_for_source(
            source_kind=source_kind,
            source_budget_policy=source_budget_policy,
        )
        intents_by_source[source_kind] = tuple(
            RuntimeSourceQueryIntent(
                round_no=dispatch.round_no,
                source_kind=source_kind,
                query_role=dispatch.query_role,
                lane_type=dispatch.lane_type,
                query_instance_id=dispatch.query_instance_id,
                query_fingerprint=dispatch.query_fingerprint,
                query_terms=dispatch.query_terms,
                keyword_query=dispatch.keyword_query,
                requested_count=dispatch.requested_count,
                provider_scan_limit=_provider_scan_limit(
                    source_kind=source_kind,
                    dispatch=dispatch,
                    source_budget_policy=budget_policy,
                ),
                source_plan_version=dispatch.source_plan_version,
                filter_intents=filter_intents,
                location_intent=location_intent,
                age_intent=age_intent,
                must_have_capabilities=must_have_capabilities,
                preferred_capabilities=preferred_capabilities,
            )
            for dispatch in logical_dispatches
        )
    return intents_by_source


def _budget_policy_for_source(
    *,
    source_kind: RuntimeSourceKind,
    source_budget_policy: RuntimeSourceBudgetPolicy | Mapping[RuntimeSourceKind, RuntimeSourceBudgetPolicy],
) -> RuntimeSourceBudgetPolicy:
    if isinstance(source_budget_policy, RuntimeSourceBudgetPolicy):
        return source_budget_policy
    try:
        return source_budget_policy[source_kind]
    except KeyError as exc:
        raise ValueError(f"runtime_source_query_intent_missing_budget_policy:{source_kind}") from exc


def _provider_scan_limit(
    *,
    source_kind: RuntimeSourceKind,
    dispatch: LogicalQueryDispatch,
    source_budget_policy: RuntimeSourceBudgetPolicy,
) -> int:
    if source_kind == "liepin":
        return min(max(dispatch.requested_count * 3, dispatch.requested_count), source_budget_policy.liepin_max_cards)
    return dispatch.requested_count
