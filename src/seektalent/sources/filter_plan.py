from __future__ import annotations

from seektalent.models import ConstraintValue, FilterField, ProposedFilterPlan, RequirementSheet, unique_strings


UNLIMITED = "不限"
DISABLED_FILTER_FIELDS = frozenset({"position"})


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
        if field not in dropped and field not in DISABLED_FILTER_FIELDS:
            pinned_filters[field] = _canonical_filter_value(requirement_sheet, field, value)

    for field, value in filter_plan.optional_filters.items():
        if field not in dropped and field not in DISABLED_FILTER_FIELDS:
            optional_filters[field] = _canonical_filter_value(requirement_sheet, field, value)

    for field in filter_plan.added_filter_fields:
        if field in dropped or field in DISABLED_FILTER_FIELDS or field in pinned_filters or field in optional_filters:
            continue
        truth_value = _truth_filter_value(requirement_sheet, field)
        if truth_value is not None:
            optional_filters[field] = truth_value

    return ProposedFilterPlan(
        pinned_filters=pinned_filters,
        optional_filters=optional_filters,
        dropped_filter_fields=[field for field in filter_plan.dropped_filter_fields if field not in DISABLED_FILTER_FIELDS],
        added_filter_fields=[
            field for field in dict.fromkeys(filter_plan.added_filter_fields) if field not in DISABLED_FILTER_FIELDS
        ],
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
        return None if hard_constraints.degree_requirement.canonical_degree == UNLIMITED else hard_constraints.degree_requirement.canonical_degree
    if field == "school_type_requirement" and hard_constraints.school_type_requirement is not None:
        types = [item for item in hard_constraints.school_type_requirement.canonical_types if item != UNLIMITED]
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
        return None if hard_constraints.gender_requirement.canonical_gender == UNLIMITED else hard_constraints.gender_requirement.canonical_gender
    if field == "age_requirement" and hard_constraints.age_requirement is not None:
        requirement = hard_constraints.age_requirement
        parts: list[str] = []
        if requirement.min_age is not None:
            parts.append(f"min={requirement.min_age}")
        if requirement.max_age is not None:
            parts.append(f"max={requirement.max_age}")
        return parts or None
    if field == "work_content":
        return " ".join(requirement_sheet.must_have_capabilities[:3]) or None
    return None


def _normalize_freeform_value(value: ConstraintValue) -> ConstraintValue:
    if isinstance(value, list):
        return unique_strings([str(item).strip() for item in value if str(item).strip()])
    if isinstance(value, int):
        return value
    return str(value).strip()
