from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from seektalent.retrieval.query_plan import allocate_balanced_city_targets
from seektalent.runtime.source_lanes import RuntimeSourceBudgetPolicy
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent


LIEPIN_EXPERIENCE_BUCKETS = (
    ("应届生", 0, 0, 1),
    ("1-3年", 1, 1, 3),
    ("3-5年", 2, 3, 5),
    ("5-10年", 3, 5, 10),
    ("10年以上", 4, 10, None),
)
LIEPIN_EXPERIENCE_TIE_ORDER = {
    "3-5年": 0,
    "5-10年": 1,
    "1-3年": 2,
    "10年以上": 3,
    "应届生": 4,
}
LIEPIN_DEGREE_LABELS = {"大专", "本科", "硕士", "博士/博士后", "中专/中技", "高中及以下"}
LIEPIN_SCHOOL_TYPE_LABELS = {"211", "985", "双一流", "海外留学"}
LIEPIN_SCHOOL_TYPE_ORDER = ("双一流", "211", "985", "海外留学")
LIEPIN_RECRUITMENT_TYPE_BY_DEGREE = {
    "大专": "统招大专",
    "本科": "统招本科",
    "硕士": "统招硕士",
    "博士/博士后": "统招博士",
}


@dataclass(frozen=True)
class LiepinNativeFilterPartial:
    field: str
    safe_reason_code: str
    detail: str


@dataclass(frozen=True)
class LiepinNativeFilterTarget:
    phase: str
    batch_no: int
    requested_count: int
    city: str | None = None
    city_section: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    experience_label: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    age_label: str | None = None
    degree_label: str | None = None
    recruitment_type_label: str | None = None
    school_type_labels: tuple[str, ...] = ()
    partial_reasons: tuple[LiepinNativeFilterPartial, ...] = ()

    def to_safe_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.city and self.city_section:
            payload["city"] = {"section": self.city_section, "label": self.city}
        if self.experience_label:
            payload["experience"] = {"section": "experience", "label": self.experience_label}
        if self.age_label:
            payload["age"] = {"section": "age", "label": self.age_label}
        if self.degree_label:
            payload["degree"] = {"section": "education", "label": self.degree_label}
        if self.recruitment_type_label:
            payload["recruitmentType"] = {"section": "recruitment_type", "label": self.recruitment_type_label}
        if self.school_type_labels:
            payload["schoolTypes"] = [
                {"section": "school_type", "label": label}
                for label in self.school_type_labels
            ]
        if self.partial_reasons:
            payload["partialReasonCodes"] = [reason.safe_reason_code for reason in self.partial_reasons]
        payload["sourceTarget"] = {
            "phase": self.phase,
            "batchNo": self.batch_no,
            "requestedCount": self.requested_count,
        }
        return payload


@dataclass(frozen=True)
class LiepinNativeFilterPlan:
    targets: tuple[LiepinNativeFilterTarget, ...]


def compile_liepin_native_filters(
    intent: RuntimeSourceQueryIntent,
    *,
    budget_policy: RuntimeSourceBudgetPolicy,
) -> LiepinNativeFilterPlan:
    del budget_policy
    if intent.source_kind != "liepin":
        raise ValueError(f"liepin_filter_compiler_wrong_source:{intent.source_kind}")

    experience_min: int | None = None
    experience_max: int | None = None
    experience_label: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    age_label: str | None = None
    degree_label: str | None = None
    requires_recruitment_type = False
    school_type_labels: list[str] = []
    partial_reasons: list[LiepinNativeFilterPartial] = []

    for filter_intent in intent.filter_intents:
        if filter_intent.field == "experience_requirement":
            parsed = _parse_min_max(filter_intent.value)
            experience_min = parsed.get("min")
            experience_max = parsed.get("max")
            experience_label, partial = _project_liepin_range_label(
                field="experience_requirement",
                value=filter_intent.value,
                buckets=LIEPIN_EXPERIENCE_BUCKETS,
                tie_order=LIEPIN_EXPERIENCE_TIE_ORDER,
            )
            if partial is not None:
                partial_reasons.append(partial)
        elif filter_intent.field == "age_requirement":
            parsed = _parse_min_max(filter_intent.value)
            age_min = parsed.get("min")
            age_max = parsed.get("max")
            age_label = _age_label(age_min=age_min, age_max=age_max)
        elif filter_intent.field == "degree_requirement":
            label = str(filter_intent.value).strip()
            if label in LIEPIN_DEGREE_LABELS:
                degree_label = label
            elif label and label != "不限":
                partial_reasons.append(
                    LiepinNativeFilterPartial(
                        field="degree_requirement",
                        safe_reason_code="source_filter_partial",
                        detail="degree_requirement stayed runtime-only because Liepin has no stable label.",
                    )
                )
        elif filter_intent.field == "school_type_requirement":
            for raw in _iter_values(filter_intent.value):
                label = str(raw).strip()
                if not label or label == "不限":
                    continue
                if label == "统招":
                    requires_recruitment_type = True
                    continue
                if label in LIEPIN_SCHOOL_TYPE_LABELS:
                    if label not in school_type_labels:
                        school_type_labels.append(label)
                    continue
                partial_reasons.append(
                    LiepinNativeFilterPartial(
                        field="school_type_requirement",
                        safe_reason_code="source_filter_partial",
                        detail="school_type_requirement stayed runtime-only because Liepin has no stable label.",
                    )
                )

    recruitment_type_label = _liepin_recruitment_type_label(
        degree_label=degree_label,
        requires_recruitment_type=requires_recruitment_type,
        partial_reasons=partial_reasons,
    )

    return LiepinNativeFilterPlan(
        targets=tuple(
            LiepinNativeFilterTarget(
                phase=phase,
                batch_no=batch_no,
                requested_count=requested_count,
                city=city,
                city_section="expected" if city else None,
                experience_min_years=experience_min,
                experience_max_years=experience_max,
                experience_label=experience_label,
                age_min=age_min,
                age_max=age_max,
                age_label=age_label,
                degree_label=degree_label,
                recruitment_type_label=recruitment_type_label,
                school_type_labels=tuple(label for label in LIEPIN_SCHOOL_TYPE_ORDER if label in school_type_labels),
                partial_reasons=tuple(partial_reasons),
            )
            for phase, batch_no, city, requested_count in _location_targets(intent)
        )
    )


def _liepin_recruitment_type_label(
    *,
    degree_label: str | None,
    requires_recruitment_type: bool,
    partial_reasons: list[LiepinNativeFilterPartial],
) -> str | None:
    if not requires_recruitment_type:
        return None
    if degree_label is None:
        partial_reasons.append(
            LiepinNativeFilterPartial(
                field="school_type_requirement",
                safe_reason_code="source_filter_partial",
                detail="school_type_requirement stayed runtime-only because Liepin combines recruitment type with degree.",
            )
        )
        return None
    label = LIEPIN_RECRUITMENT_TYPE_BY_DEGREE.get(degree_label)
    if label is not None:
        return label
    partial_reasons.append(
        LiepinNativeFilterPartial(
            field="school_type_requirement",
            safe_reason_code="source_filter_partial",
            detail="school_type_requirement stayed runtime-only because Liepin has no recruitment label for this degree.",
        )
    )
    return None


def _location_targets(intent: RuntimeSourceQueryIntent) -> tuple[tuple[str, int, str | None, int], ...]:
    location = intent.location_intent
    if location is None or not location.allowed_locations:
        return (("balanced", 1, None, intent.provider_scan_limit),)
    if location.mode == "single":
        return (("balanced", 1, location.allowed_locations[0], intent.provider_scan_limit),)
    if location.mode == "priority_then_fallback" and location.priority_order:
        targets: list[tuple[str, int, str | None, int]] = []
        batch_no = 1
        for city in location.priority_order:
            targets.append(("priority", batch_no, city, intent.provider_scan_limit))
            batch_no += 1
        for city, requested in allocate_balanced_city_targets(
            ordered_cities=list(location.balanced_order),
            target_new=intent.provider_scan_limit,
        ):
            targets.append(("balanced", batch_no, city, requested))
            batch_no += 1
        return tuple(targets)

    return tuple(
        ("balanced", batch_no, city, requested)
        for batch_no, (city, requested) in enumerate(
            allocate_balanced_city_targets(
                ordered_cities=list(location.balanced_order or location.allowed_locations),
                target_new=intent.provider_scan_limit,
            ),
            start=1,
        )
    )


def _parse_min_max(value: Any) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for item in _iter_values(value):
        text = str(item).strip()
        try:
            if text.startswith("min="):
                parsed["min"] = int(text.removeprefix("min="))
            elif text.startswith("max="):
                parsed["max"] = int(text.removeprefix("max="))
        except ValueError:
            return {}
    return parsed


def _iter_values(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        return value
    return (value,)


def _project_liepin_range_label(
    *,
    field: str,
    value: object,
    buckets: tuple[tuple[str, int, int, int | None], ...],
    tie_order: dict[str, int],
) -> tuple[str | None, LiepinNativeFilterPartial | None]:
    bounds = _parse_min_max(value)
    if not bounds:
        return None, LiepinNativeFilterPartial(
            field=field,
            safe_reason_code="source_filter_partial",
            detail=f"{field} stayed runtime-only because range normalization is invalid.",
        )
    lower = bounds.get("min")
    upper = bounds.get("max")
    if lower is not None and upper is None:
        exact_open_bucket = next(
            (
                label
                for label, _code, bucket_min, bucket_max in buckets
                if bucket_min == lower and bucket_max is None
            ),
            None,
        )
        if exact_open_bucket is not None:
            return exact_open_bucket, None
        return None, LiepinNativeFilterPartial(
            field=field,
            safe_reason_code="source_filter_partial",
            detail=f"{field} stayed runtime-only because open-ended minimum ranges are broader than one Liepin range.",
        )
    overlaps: list[tuple[str, float]] = []
    for label, _code, bucket_min, bucket_max in buckets:
        overlap = _range_overlap(lower, upper, bucket_min, bucket_max)
        if overlap > 0:
            overlaps.append((label, overlap))
    if not overlaps:
        return None, LiepinNativeFilterPartial(
            field=field,
            safe_reason_code="source_filter_partial",
            detail=f"{field} stayed runtime-only because it does not match any supported Liepin range.",
        )
    if len(overlaps) >= 3:
        return None, LiepinNativeFilterPartial(
            field=field,
            safe_reason_code="source_filter_partial",
            detail=f"{field} stayed runtime-only because it spans 3 or more Liepin ranges.",
        )
    overlaps.sort(key=lambda item: (-item[1], tie_order[item[0]]))
    first = overlaps[0]
    if len(overlaps) == 2 and first[1] == overlaps[1][1]:
        overlaps.sort(key=lambda item: tie_order[item[0]])
        first = overlaps[0]
    return first[0], None


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


def _age_label(*, age_min: int | None, age_max: int | None) -> str | None:
    if age_min is not None and age_max is not None:
        return f"{age_min}-{age_max}岁"
    if age_max is not None:
        return f"{age_max}岁以下"
    if age_min is not None:
        return f"{age_min}岁以上"
    return None
