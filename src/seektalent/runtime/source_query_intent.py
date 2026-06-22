from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from seektalent.models import LaneType, ProviderQuery, QueryRole, RuntimeSourceKind
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.source_filters import (
    RuntimeAgeExecutionIntent,
    RuntimeFilterIntent,
    RuntimeLocationExecutionIntent,
)
from seektalent.source_contracts import RuntimeQueryPackage, RuntimeSourceBudgetPolicy

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


def query_package_from_intent(intent: RuntimeSourceQueryIntent) -> RuntimeQueryPackage:
    return RuntimeQueryPackage(
        source_kind=intent.source_kind,
        query_role=intent.query_role,
        lane_type=intent.lane_type,
        query_terms=tuple(intent.query_terms),
        keyword_query=intent.keyword_query,
    )


def query_package_from_provider_query(*, source_kind: RuntimeSourceKind | str, query: ProviderQuery) -> RuntimeQueryPackage:
    return RuntimeQueryPackage(
        source_kind=source_kind,
        query_role=query.query_role,
        lane_type=query.lane_type,
        query_terms=tuple(query.query_terms),
        keyword_query=query.keyword_query,
    )


@dataclass(frozen=True)
class RuntimeSourceQueryPolicy:
    requested_count_caps_by_lane: Mapping[LaneType, int] = field(default_factory=dict)
    provider_scan_multiplier: int = 1
    provider_scan_cap: int | None = None

    def requested_count(self, *, lane_type: LaneType, requested_count: int) -> int:
        cap = self.requested_count_caps_by_lane.get(lane_type)
        return requested_count if cap is None else min(requested_count, max(0, cap))

    def provider_scan_limit(self, *, requested_count: int) -> int:
        scan_limit = max(requested_count, requested_count * max(1, self.provider_scan_multiplier))
        if self.provider_scan_cap is not None:
            scan_limit = min(scan_limit, max(0, self.provider_scan_cap))
        return scan_limit


def normalize_source_search_action(action: str) -> SourceSearchAction:
    if action == "source_search":
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
    source_query_policy: Mapping[RuntimeSourceKind, RuntimeSourceQueryPolicy] | None = None,
    must_have_capabilities: tuple[str, ...] = (),
    preferred_capabilities: tuple[str, ...] = (),
) -> Mapping[RuntimeSourceKind, tuple[RuntimeSourceQueryIntent, ...]]:
    intents_by_source: dict[RuntimeSourceKind, tuple[RuntimeSourceQueryIntent, ...]] = {}
    for source_kind in source_kinds:
        budget_policy = _budget_policy_for_source(
            source_kind=source_kind,
            source_budget_policy=source_budget_policy,
        )
        query_policy = _query_policy_for_source(
            source_kind=source_kind,
            source_query_policy=source_query_policy,
        )
        intents: list[RuntimeSourceQueryIntent] = []
        for dispatch in logical_dispatches:
            requested_count = source_requested_count(
                source_kind=source_kind,
                lane_type=dispatch.lane_type,
                requested_count=dispatch.requested_count,
                source_budget_policy=budget_policy,
                source_query_policy=query_policy,
            )
            intents.append(
                RuntimeSourceQueryIntent(
                    round_no=dispatch.round_no,
                    source_kind=source_kind,
                    query_role=dispatch.query_role,
                    lane_type=dispatch.lane_type,
                    query_instance_id=dispatch.query_instance_id,
                    query_fingerprint=dispatch.query_fingerprint,
                    query_terms=dispatch.query_terms,
                    keyword_query=dispatch.keyword_query,
                    requested_count=requested_count,
                    provider_scan_limit=_provider_scan_limit(
                        source_kind=source_kind,
                        requested_count=requested_count,
                        source_budget_policy=budget_policy,
                        source_query_policy=query_policy,
                    ),
                    source_plan_version=dispatch.source_plan_version,
                    filter_intents=filter_intents,
                    location_intent=location_intent,
                    age_intent=age_intent,
                    must_have_capabilities=must_have_capabilities,
                    preferred_capabilities=preferred_capabilities,
                )
            )
        intents_by_source[source_kind] = tuple(intents)
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


def _query_policy_for_source(
    *,
    source_kind: RuntimeSourceKind,
    source_query_policy: Mapping[RuntimeSourceKind, RuntimeSourceQueryPolicy] | None,
) -> RuntimeSourceQueryPolicy:
    if source_query_policy is None:
        return RuntimeSourceQueryPolicy()
    return source_query_policy.get(source_kind, RuntimeSourceQueryPolicy())


def source_requested_count(
    *,
    source_kind: RuntimeSourceKind,
    lane_type: LaneType,
    requested_count: int,
    source_budget_policy: RuntimeSourceBudgetPolicy,
    source_query_policy: RuntimeSourceQueryPolicy | None = None,
) -> int:
    del source_kind, source_budget_policy
    policy = source_query_policy or RuntimeSourceQueryPolicy()
    return policy.requested_count(lane_type=lane_type, requested_count=requested_count)


def _provider_scan_limit(
    *,
    source_kind: RuntimeSourceKind,
    requested_count: int,
    source_budget_policy: RuntimeSourceBudgetPolicy,
    source_query_policy: RuntimeSourceQueryPolicy | None = None,
) -> int:
    del source_kind, source_budget_policy
    policy = source_query_policy or RuntimeSourceQueryPolicy()
    return policy.provider_scan_limit(requested_count=requested_count)
