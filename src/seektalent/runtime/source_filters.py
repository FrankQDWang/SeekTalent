from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seektalent.models import ConstraintValue, FilterField, LocationExecutionMode, ProposedFilterPlan, RequirementSheet, unique_strings
from seektalent.retrieval.query_plan import build_location_execution_plan


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


@dataclass(frozen=True)
class UnsupportedSourceFilter:
    source_kind: str
    field: str
    query_instance_id: str | None
    safe_reason_code: str
    detail: str = ""


def build_default_filter_plan(requirement_sheet: RequirementSheet) -> ProposedFilterPlan:
    optional_filters: dict[FilterField, ConstraintValue] = {}
    for field in (
        "company_names",
        "school_names",
        "degree_requirement",
        "school_type_requirement",
        "experience_requirement",
        "gender_requirement",
        "age_requirement",
        "position",
    ):
        value = _truth_filter_value(requirement_sheet, field)
        if value is not None:
            optional_filters[field] = value
    return ProposedFilterPlan(optional_filters=optional_filters)


def canonicalize_filter_plan(
    *,
    requirement_sheet: RequirementSheet,
    filter_plan: ProposedFilterPlan,
) -> ProposedFilterPlan:
    dropped = set(filter_plan.dropped_filter_fields)
    pinned_filters: dict[FilterField, ConstraintValue] = {}
    optional_filters: dict[FilterField, ConstraintValue] = {}

    for field, value in filter_plan.pinned_filters.items():
        if field not in dropped:
            pinned_filters[field] = _canonical_filter_value(requirement_sheet, field, value)

    for field, value in filter_plan.optional_filters.items():
        if field not in dropped:
            optional_filters[field] = _canonical_filter_value(requirement_sheet, field, value)

    for field in filter_plan.added_filter_fields:
        if field in dropped or field in pinned_filters or field in optional_filters:
            continue
        truth_value = _truth_filter_value(requirement_sheet, field)
        if truth_value is not None:
            optional_filters[field] = truth_value

    return ProposedFilterPlan(
        pinned_filters=pinned_filters,
        optional_filters=optional_filters,
        dropped_filter_fields=list(filter_plan.dropped_filter_fields),
        added_filter_fields=list(dict.fromkeys(filter_plan.added_filter_fields)),
    )


def build_runtime_filter_intents(
    *,
    requirement_sheet: RequirementSheet,
    proposed_filter_plan: ProposedFilterPlan | None,
) -> tuple[RuntimeFilterIntent, ...]:
    filter_plan = proposed_filter_plan or build_default_filter_plan(requirement_sheet)
    canonical = canonicalize_filter_plan(requirement_sheet=requirement_sheet, filter_plan=filter_plan)
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


def _canonical_filter_value(
    requirement_sheet: RequirementSheet,
    field: FilterField,
    fallback_value: ConstraintValue,
) -> ConstraintValue:
    truth_value = _truth_filter_value(requirement_sheet, field)
    if truth_value is not None:
        return truth_value
    return _normalize_freeform_value(fallback_value)


def _truth_filter_value(
    requirement_sheet: RequirementSheet,
    field: FilterField,
) -> ConstraintValue | None:
    hard_constraints = requirement_sheet.hard_constraints
    if field == "company_names":
        return hard_constraints.company_names or None
    if field == "school_names":
        return hard_constraints.school_names or None
    if field == "degree_requirement" and hard_constraints.degree_requirement is not None:
        return None if hard_constraints.degree_requirement.canonical_degree == "不限" else hard_constraints.degree_requirement.canonical_degree
    if field == "school_type_requirement" and hard_constraints.school_type_requirement is not None:
        types = [item for item in hard_constraints.school_type_requirement.canonical_types if item != "不限"]
        return types or None
    if field == "experience_requirement" and hard_constraints.experience_requirement is not None:
        requirement = hard_constraints.experience_requirement
        parts: list[str] = []
        if requirement.min_years is not None:
            parts.append(f"min={requirement.min_years}")
        if requirement.max_years is not None:
            parts.append(f"max={requirement.max_years}")
        return parts or None
    if field == "gender_requirement" and hard_constraints.gender_requirement is not None:
        return None if hard_constraints.gender_requirement.canonical_gender == "不限" else hard_constraints.gender_requirement.canonical_gender
    if field == "age_requirement" and hard_constraints.age_requirement is not None:
        requirement = hard_constraints.age_requirement
        parts: list[str] = []
        if requirement.min_age is not None:
            parts.append(f"min={requirement.min_age}")
        if requirement.max_age is not None:
            parts.append(f"max={requirement.max_age}")
        return parts or None
    if field == "position":
        return requirement_sheet.role_title or None
    if field == "work_content":
        return " ".join(requirement_sheet.must_have_capabilities[:3]) or None
    return None


def _normalize_freeform_value(value: ConstraintValue) -> ConstraintValue:
    if isinstance(value, list):
        return unique_strings([str(item).strip() for item in value if str(item).strip()])
    if isinstance(value, int):
        return value
    return str(value).strip()
