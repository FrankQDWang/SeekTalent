from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seektalent.models import ConstraintValue, FilterField, LocationExecutionMode, ProposedFilterPlan, RequirementSheet
from seektalent.retrieval.query_plan import build_location_execution_plan
from seektalent.sources import filter_plan as source_filter_plan
from seektalent.sources.contracts import UnsupportedSourceFilter

__all__ = ["UnsupportedSourceFilter"]


FilterIntentOrigin = Literal["hard_constraint", "preference", "controller"]


@dataclass(frozen=True)
class RuntimeFilterIntent:
    field: FilterField
    value: ConstraintValue
    required: bool
    origin: FilterIntentOrigin


@dataclass(frozen=True)
class RuntimeLocationPreference:
    location: str
    priority: int


@dataclass(frozen=True)
class RuntimeLocationExecutionIntent:
    mode: LocationExecutionMode
    allowed_locations: tuple[str, ...]
    preferred_locations: tuple[str, ...]
    priority_order: tuple[str, ...]
    balanced_order: tuple[str, ...]
    rotation_offset: int
    target_new: int


@dataclass(frozen=True)
class RuntimeAgeExecutionIntent:
    value: ConstraintValue
    required: bool


def build_runtime_filter_intents(
    *,
    requirement_sheet: RequirementSheet,
    proposed_filter_plan: ProposedFilterPlan | None,
) -> tuple[RuntimeFilterIntent, ...]:
    filter_plan = proposed_filter_plan or source_filter_plan.build_default_filter_plan(requirement_sheet)
    canonical = source_filter_plan.canonicalize_filter_plan(requirement_sheet=requirement_sheet, filter_plan=filter_plan)
    intents: list[RuntimeFilterIntent] = []
    for field, value in canonical.pinned_filters.items():
        intents.append(
            RuntimeFilterIntent(
                field=field,
                value=value,
                required=True,
                origin="hard_constraint",
            )
        )
    for field, value in canonical.optional_filters.items():
        if field in canonical.pinned_filters:
            continue
        if field == "company_names":
            continue
        intents.append(
            RuntimeFilterIntent(
                field=field,
                value=value,
                required=False,
                origin="controller",
            )
        )
    return tuple(intents)


def build_runtime_location_execution_intent(
    *,
    requirement_sheet: RequirementSheet,
    proposed_filter_plan: ProposedFilterPlan | None,
    round_no: int,
    target_new: int = 0,
) -> RuntimeLocationExecutionIntent | None:
    del proposed_filter_plan
    location_plan = build_location_execution_plan(
        allowed_locations=requirement_sheet.hard_constraints.locations,
        preferred_locations=requirement_sheet.preferences.preferred_locations,
        round_no=round_no,
        target_new=target_new,
    )
    if location_plan.mode == "none" and not location_plan.allowed_locations:
        return None
    return RuntimeLocationExecutionIntent(
        mode=location_plan.mode,
        allowed_locations=tuple(location_plan.allowed_locations),
        preferred_locations=tuple(location_plan.preferred_locations),
        priority_order=tuple(location_plan.priority_order),
        balanced_order=tuple(location_plan.balanced_order),
        rotation_offset=location_plan.rotation_offset,
        target_new=location_plan.target_new,
    )
