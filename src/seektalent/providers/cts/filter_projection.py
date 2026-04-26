from __future__ import annotations

from seektalent.models import (
    ConditionSource,
    ConstraintProjectionResult,
    FilterField,
    ProposedFilterPlan,
    RequirementSheet,
    RuntimeConstraint,
    unique_strings,
)

TEXT_NATIVE_FIELDS: dict[FilterField, str] = {
    "company_names": "company",
    "school_names": "school",
    "position": "position",
    "work_content": "workContent",
}
ENUM_NATIVE_FIELDS: dict[FilterField, str] = {
    "degree_requirement": "degree",
    "school_type_requirement": "schoolType",
    "experience_requirement": "workExperienceRange",
    "gender_requirement": "gender",
    "age_requirement": "age",
}
UNLIMITED = "不限"
EXPERIENCE_BUCKETS = (
    ("1年以下", 1, 0, 1),
    ("1-3年", 2, 1, 3),
    ("3-5年", 3, 3, 5),
    ("5-10年", 4, 5, 10),
    ("10年以上", 5, 10, None),
)
EXPERIENCE_TIE_ORDER = {
    "3-5年": 0,
    "5-10年": 1,
    "1-3年": 2,
    "10年以上": 3,
    "1年以下": 4,
}
AGE_BUCKETS = (
    ("20-25岁", 1, 20, 25),
    ("25-30岁", 2, 25, 30),
    ("30-35岁", 3, 30, 35),
    ("35-40岁", 4, 35, 40),
    ("40-45岁", 5, 40, 45),
    ("45岁以上", 6, 45, None),
)
AGE_TIE_ORDER = {
    "30-35岁": 0,
    "25-30岁": 1,
    "35-40岁": 2,
    "20-25岁": 3,
    "40-45岁": 4,
    "45岁以上": 5,
}
DEGREE_CODES = {
    "大专": 1,
    "大专及以上": 1,
    "本科": 2,
    "本科及以上": 2,
    "硕士": 3,
    "硕士及以上": 3,
}
GENDER_CODES = {
    "男": 1,
    "女": 2,
}
SCHOOL_TYPE_CODES = {
    "双一流": 1,
    "211": 2,
    "985": 3,
}


def build_default_filter_plan(requirement_sheet: RequirementSheet) -> ProposedFilterPlan:
    optional_filters: dict[FilterField, str | int | list[str]] = {}
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
        if value is None:
            continue
        optional_filters[field] = value
    return ProposedFilterPlan(optional_filters=optional_filters)


def canonicalize_filter_plan(
    *,
    requirement_sheet: RequirementSheet,
    filter_plan: ProposedFilterPlan,
) -> ProposedFilterPlan:
    dropped = set(filter_plan.dropped_filter_fields)
    pinned_filters: dict[FilterField, str | int | list[str]] = {}
    optional_filters: dict[FilterField, str | int | list[str]] = {}

    for field, value in filter_plan.pinned_filters.items():
        if field in dropped:
            continue
        pinned_filters[field] = _canonical_filter_value(requirement_sheet, field, value)

    for field, value in filter_plan.optional_filters.items():
        if field in dropped:
            continue
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


def project_constraints_to_cts(
    *,
    requirement_sheet: RequirementSheet,
    filter_plan: ProposedFilterPlan,
) -> ConstraintProjectionResult:
    native_filters: dict[str, str | int | list[str]] = {}
    runtime_only_constraints: list[RuntimeConstraint] = []
    adapter_notes: list[str] = []
    canonical_plan = canonicalize_filter_plan(requirement_sheet=requirement_sheet, filter_plan=filter_plan)
    merged = {**canonical_plan.pinned_filters, **canonical_plan.optional_filters}

    for field, value in merged.items():
        if field in TEXT_NATIVE_FIELDS:
            projected = _project_text_filter(field, value)
            if projected is None:
                adapter_notes.append(f"{field} was selected but empty after normalization.")
                continue
            native_filters[TEXT_NATIVE_FIELDS[field]] = projected
            continue
        if field in ENUM_NATIVE_FIELDS:
            projected, note, skip_runtime_only = _project_enum_filter(field, value)
            if note:
                adapter_notes.append(note)
            if projected is None:
                if skip_runtime_only or _is_unlimited_value(value):
                    continue
                runtime_only_constraints.append(
                    RuntimeConstraint(
                        field=field,
                        normalized_value=value,
                        source=_source_for_field(field),
                        rationale="Field stays runtime-only because no stable CTS enum mapping is available.",
                        blocking=field in canonical_plan.pinned_filters,
                    )
                )
                continue
            native_filters[ENUM_NATIVE_FIELDS[field]] = projected
            continue
        raise ValueError(f"unsupported filter field: {field}")

    return ConstraintProjectionResult(
        provider_filters=native_filters,
        runtime_only_constraints=runtime_only_constraints,
        adapter_notes=adapter_notes,
    )


def _canonical_filter_value(
    requirement_sheet: RequirementSheet,
    field: FilterField,
    fallback_value: str | int | list[str],
) -> str | int | list[str]:
    truth_value = _truth_filter_value(requirement_sheet, field)
    if truth_value is not None:
        return truth_value
    return _normalize_freeform_value(fallback_value)


def _truth_filter_value(
    requirement_sheet: RequirementSheet,
    field: FilterField,
) -> str | int | list[str] | None:
    hard_constraints = requirement_sheet.hard_constraints
    if field == "company_names":
        return hard_constraints.company_names or None
    if field == "school_names":
        return hard_constraints.school_names or None
    if field == "degree_requirement" and hard_constraints.degree_requirement is not None:
        if hard_constraints.degree_requirement.canonical_degree == UNLIMITED:
            return None
        return hard_constraints.degree_requirement.canonical_degree
    if field == "school_type_requirement" and hard_constraints.school_type_requirement is not None:
        types = [item for item in hard_constraints.school_type_requirement.canonical_types if item != UNLIMITED]
        return types or None
    if field == "experience_requirement" and hard_constraints.experience_requirement is not None:
        requirement = hard_constraints.experience_requirement
        if requirement.min_years is None and requirement.max_years is None:
            return None
        parts: list[str] = []
        if requirement.min_years is not None:
            parts.append(f"min={requirement.min_years}")
        if requirement.max_years is not None:
            parts.append(f"max={requirement.max_years}")
        return parts
    if field == "gender_requirement" and hard_constraints.gender_requirement is not None:
        return None if hard_constraints.gender_requirement.canonical_gender == UNLIMITED else hard_constraints.gender_requirement.canonical_gender
    if field == "age_requirement" and hard_constraints.age_requirement is not None:
        requirement = hard_constraints.age_requirement
        if requirement.min_age is None and requirement.max_age is None:
            return None
        parts: list[str] = []
        if requirement.min_age is not None:
            parts.append(f"min={requirement.min_age}")
        if requirement.max_age is not None:
            parts.append(f"max={requirement.max_age}")
        return parts
    if field == "position":
        return requirement_sheet.role_title or None
    if field == "work_content":
        return " ".join(requirement_sheet.must_have_capabilities[:3]) or None
    return None


def _project_text_filter(field: str, value: str | int | list[str]) -> str | list[str] | None:
    if isinstance(value, list):
        items = unique_strings([str(item).strip() for item in value if str(item).strip()])
        return " | ".join(items) or None
    text = str(value).strip()
    return text or None


def _project_enum_filter(field: FilterField, value: str | int | list[str]) -> tuple[int | None, str | None, bool]:
    if _is_unlimited_value(value):
        return None, f"{field} is explicitly unlimited and was not sent to CTS.", True
    if field == "degree_requirement":
        return _project_direct_enum(field=field, value=value, mapping=DEGREE_CODES)
    if field == "school_type_requirement":
        return _project_school_type_enum(value)
    if field == "experience_requirement":
        return _project_range_enum(
            field=field,
            value=value,
            buckets=EXPERIENCE_BUCKETS,
            tie_order=EXPERIENCE_TIE_ORDER,
        )
    if field == "gender_requirement":
        return _project_direct_enum(field=field, value=value, mapping=GENDER_CODES)
    if field == "age_requirement":
        return _project_range_enum(
            field=field,
            value=value,
            buckets=AGE_BUCKETS,
            tie_order=AGE_TIE_ORDER,
        )
    return None, f"{field} stayed runtime-only because enum mapping is not implemented yet.", False


def _project_range_enum(
    *,
    field: FilterField,
    value: str | int | list[str],
    buckets: tuple[tuple[str, int, int, int | None], ...],
    tie_order: dict[str, int],
) -> tuple[int | None, str | None, bool]:
    bounds = _parse_numeric_bounds(value)
    if bounds is None:
        return None, f"{field} stayed runtime-only because range normalization is invalid.", False
    lower, upper = bounds
    overlaps: list[tuple[str, int, float]] = []
    for label, code, bucket_min, bucket_max in buckets:
        overlap = _range_overlap(lower, upper, bucket_min, bucket_max)
        if overlap > 0:
            overlaps.append((label, code, overlap))
    if not overlaps:
        return None, f"{field} does not match any supported CTS range and was not sent to CTS.", True
    if len(overlaps) >= 3:
        return None, f"{field} spans 3 or more CTS ranges and was not sent to CTS.", True
    if len(overlaps) == 1:
        label, code, _ = overlaps[0]
        return code, f"{field} mapped to CTS code {code} ({label}).", False
    overlaps.sort(key=lambda item: (-item[2], tie_order[item[0]]))
    first, second = overlaps
    if first[2] == second[2]:
        overlaps.sort(key=lambda item: tie_order[item[0]])
    label, code, _ = overlaps[0]
    return code, f"{field} mapped to CTS code {code} ({label}).", False


def _project_direct_enum(
    *,
    field: FilterField,
    value: str | int | list[str],
    mapping: dict[str, int],
) -> tuple[int | None, str | None, bool]:
    if isinstance(value, list):
        return None, f"{field} stayed runtime-only because CTS expects a single enum value.", False
    text = str(value).strip()
    code = mapping.get(text)
    if code is None:
        return None, f"{field} stayed runtime-only because no stable CTS enum mapping is available for `{text}`.", False
    return code, f"{field} mapped to CTS code {code} ({text}).", False


def _project_school_type_enum(value: str | int | list[str]) -> tuple[int | None, str | None, bool]:
    if not isinstance(value, list):
        return None, "school_type_requirement stayed runtime-only because CTS expects a known school type set.", False
    types = unique_strings([str(item).strip() for item in value if str(item).strip()])
    if not types:
        return None, "school_type_requirement stayed runtime-only because CTS expects a known school type set.", False
    if any(item not in SCHOOL_TYPE_CODES for item in types):
        return None, "school_type_requirement stayed runtime-only because the selected school types do not have a stable CTS mapping.", False
    nested_types = {"双一流", "211", "985"}
    if any(item not in nested_types for item in types):
        return None, "school_type_requirement stayed runtime-only because the selected school types are not safely compressible to one CTS code.", False
    if "双一流" in types:
        return 1, "school_type_requirement mapped to CTS code 1 (双一流).", False
    if "211" in types:
        return 2, "school_type_requirement mapped to CTS code 2 (211).", False
    return 3, "school_type_requirement mapped to CTS code 3 (985).", False


def _parse_numeric_bounds(value: str | int | list[str]) -> tuple[int | None, int | None] | None:
    if not isinstance(value, list):
        return None
    lower: int | None = None
    upper: int | None = None
    for item in value:
        text = str(item).strip()
        if text.startswith("min="):
            lower = int(text.removeprefix("min="))
        elif text.startswith("max="):
            upper = int(text.removeprefix("max="))
    if lower is None and upper is None:
        return None
    return lower, upper


def _range_overlap(
    lower: int | None,
    upper: int | None,
    bucket_min: int,
    bucket_max: int | None,
) -> float:
    start = max(0 if lower is None else lower, bucket_min)
    end = min(float("inf") if upper is None else upper, float("inf") if bucket_max is None else bucket_max)
    if end <= start:
        return 0.0
    return end - start


def _normalize_freeform_value(value: str | int | list[str]) -> str | int | list[str]:
    if isinstance(value, list):
        return unique_strings([str(item).strip() for item in value if str(item).strip()])
    if isinstance(value, int):
        return value
    return " ".join(value.split()).strip()


def _is_unlimited_value(value: str | int | list[str]) -> bool:
    if isinstance(value, list):
        return not value
    return str(value).strip() == UNLIMITED


def _source_for_field(field: FilterField) -> ConditionSource:
    if field in {"gender_requirement", "age_requirement"}:
        return "notes"
    return "jd"
