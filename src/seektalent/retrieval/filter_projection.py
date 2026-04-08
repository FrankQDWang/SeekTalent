from __future__ import annotations

from seektalent.locations import normalize_locations
from seektalent.models import ConstraintValue, SearchExecutionPlan_t, stable_deduplicate

TEXT_NATIVE_FIELDS = {
    "company_names": "company",
    "school_names": "school",
    "derived_position": "position",
    "derived_work_content": "workContent",
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


def project_search_plan_to_cts(plan: SearchExecutionPlan_t) -> tuple[dict[str, ConstraintValue], list[str]]:
    native_filters: dict[str, ConstraintValue] = {}
    notes: list[str] = []
    hard_constraints = plan.projected_filters

    location_values = normalize_locations(hard_constraints.locations)
    if location_values:
        native_filters["location"] = location_values[0] if len(location_values) == 1 else location_values

    for field_name in ("company_names", "school_names"):
        value = getattr(hard_constraints, field_name)
        projected = _project_text_filter(value)
        if projected is not None:
            native_filters[TEXT_NATIVE_FIELDS[field_name]] = projected

    for field_name, value in (
        ("derived_position", plan.derived_position),
        ("derived_work_content", plan.derived_work_content),
    ):
        projected = _project_text_filter(value)
        if projected is not None:
            native_filters[TEXT_NATIVE_FIELDS[field_name]] = projected

    degree_code, degree_note = _project_direct_enum(
        "degree_requirement",
        hard_constraints.degree_requirement,
        DEGREE_CODES,
    )
    if degree_code is not None:
        native_filters["degree"] = degree_code
    if degree_note:
        notes.append(degree_note)

    school_type_code, school_type_note = _project_school_type_enum(hard_constraints.school_type_requirement)
    if school_type_code is not None:
        native_filters["schoolType"] = school_type_code
        notes.append(school_type_note)
    elif school_type_note:
        notes.append(school_type_note)

    experience_code, experience_note = _project_range_enum(
        "experience_requirement",
        hard_constraints.min_years,
        hard_constraints.max_years,
        EXPERIENCE_BUCKETS,
        EXPERIENCE_TIE_ORDER,
    )
    if experience_code is not None:
        native_filters["workExperienceRange"] = experience_code
        notes.append(experience_note)
    elif experience_note:
        notes.append(experience_note)

    gender_code, gender_note = _project_direct_enum(
        "gender_requirement",
        hard_constraints.gender_requirement,
        GENDER_CODES,
    )
    if gender_code is not None:
        native_filters["gender"] = gender_code
    if gender_note:
        notes.append(gender_note)

    age_code, age_note = _project_range_enum(
        "age_requirement",
        hard_constraints.min_age,
        hard_constraints.max_age,
        AGE_BUCKETS,
        AGE_TIE_ORDER,
    )
    if age_code is not None:
        native_filters["age"] = age_code
        notes.append(age_note)
    elif age_note:
        notes.append(age_note)

    return native_filters, notes


def _project_text_filter(value: str | list[str] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        items = stable_deduplicate(value)
        return " | ".join(items) if items else None
    clean = " ".join(value.split()).strip()
    return clean or None


def _project_direct_enum(
    field_name: str,
    value: str | None,
    mapping: dict[str, int],
) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if value == "不限":
        return None, f"{field_name} is explicitly unlimited and was omitted from CTS."
    code = mapping.get(value)
    if code is None:
        return None, f"{field_name} stayed outside CTS because `{value}` has no stable mapping."
    return code, f"{field_name} mapped to CTS code {code} ({value})."


def _project_school_type_enum(value: list[str]) -> tuple[int | None, str | None]:
    school_types = stable_deduplicate(value)
    if not school_types:
        return None, None
    if any(item not in SCHOOL_TYPE_CODES for item in school_types):
        return None, "school_type_requirement stayed outside CTS because the selected school types do not have a stable mapping."
    if "双一流" in school_types:
        return 1, "school_type_requirement mapped to CTS code 1 (双一流)."
    if "211" in school_types:
        return 2, "school_type_requirement mapped to CTS code 2 (211)."
    return 3, "school_type_requirement mapped to CTS code 3 (985)."


def _project_range_enum(
    field_name: str,
    lower: int | None,
    upper: int | None,
    buckets: tuple[tuple[str, int, int, int | None], ...],
    tie_order: dict[str, int],
) -> tuple[int | None, str | None]:
    if lower is None and upper is None:
        return None, None
    overlaps: list[tuple[str, int, float]] = []
    for label, code, bucket_min, bucket_max in buckets:
        overlap = _range_overlap(lower, upper, bucket_min, bucket_max)
        if overlap > 0:
            overlaps.append((label, code, overlap))
    if not overlaps:
        return None, f"{field_name} does not match any supported CTS range and was not sent to CTS."
    if len(overlaps) >= 3:
        return None, f"{field_name} spans 3 or more CTS ranges and was not sent to CTS."
    overlaps.sort(key=lambda item: (-item[2], tie_order[item[0]]))
    label, code, _ = overlaps[0]
    return code, f"{field_name} mapped to CTS code {code} ({label})."


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
