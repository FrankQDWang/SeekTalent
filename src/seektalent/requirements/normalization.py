from __future__ import annotations

import hashlib
import re

from seektalent.locations import normalize_locations
from seektalent.models import (
    HardConstraints,
    RequirementExtractionDraft,
    RequirementPreferences,
    RequirementSheet,
    SearchInputTruth,
    stable_deduplicate,
)

UNLIMITED = "不限"
DEGREE_PATTERNS = (
    ("博士及以上", ("博士",)),
    ("硕士及以上", ("硕士", "研究生")),
    ("本科及以上", ("本科", "学士")),
    ("大专及以上", ("大专", "专科")),
)


def build_input_truth(*, job_description: str, hiring_notes: str) -> SearchInputTruth:
    return SearchInputTruth(
        job_description=job_description,
        hiring_notes=hiring_notes,
        job_description_sha256=hashlib.sha256(job_description.encode("utf-8")).hexdigest(),
        hiring_notes_sha256=hashlib.sha256(hiring_notes.encode("utf-8")).hexdigest(),
    )


def normalize_requirement_draft(draft: RequirementExtractionDraft, *, input_truth: SearchInputTruth | None = None) -> RequirementSheet:
    role_title = _coalesce_title(
        draft.role_title_candidate,
        input_truth.job_description if input_truth is not None else "",
    )
    role_summary = _coalesce_summary(
        draft.role_summary_candidate,
        input_truth.job_description if input_truth is not None else "",
        input_truth.hiring_notes if input_truth is not None else "",
    )
    scoring_rationale = _normalize_text(draft.scoring_rationale_candidate)
    if not role_title:
        raise ValueError("role_title must not be empty after normalization")
    if not role_summary:
        raise ValueError("role_summary must not be empty after normalization")
    if not scoring_rationale:
        raise ValueError("scoring_rationale must not be empty after normalization")

    hard_constraints = draft.hard_constraint_candidates
    min_years, max_years = _ordered_range(hard_constraints.min_years, hard_constraints.max_years)
    min_age, max_age = _ordered_range(hard_constraints.min_age, hard_constraints.max_age)

    return RequirementSheet(
        role_title=role_title,
        role_summary=role_summary,
        must_have_capabilities=_normalize_list(draft.must_have_capability_candidates),
        preferred_capabilities=_normalize_list(draft.preferred_capability_candidates),
        exclusion_signals=_normalize_list(draft.exclusion_signal_candidates),
        preferences=RequirementPreferences(
            preferred_domains=_normalize_list(draft.preference_candidates.preferred_domains),
            preferred_backgrounds=_normalize_list(draft.preference_candidates.preferred_backgrounds),
        ),
        hard_constraints=HardConstraints(
            locations=normalize_locations(hard_constraints.locations),
            min_years=min_years,
            max_years=max_years,
            company_names=_normalize_list(hard_constraints.company_names),
            school_names=_normalize_list(hard_constraints.school_names),
            degree_requirement=_canonical_degree(hard_constraints.degree_requirement),
            school_type_requirement=_normalize_list(hard_constraints.school_type_requirement),
            gender_requirement=_canonical_gender(hard_constraints.gender_requirement),
            min_age=min_age,
            max_age=max_age,
        ),
        scoring_rationale=scoring_rationale,
    )


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split()).strip()


def _normalize_list(values: list[str]) -> list[str]:
    return stable_deduplicate([_normalize_text(value) for value in values])


def _coalesce_title(candidate: str, job_description: str) -> str:
    title = _strip_title_prefix(_normalize_text(candidate))
    if title:
        return title
    for line in job_description.splitlines():
        fallback = _strip_title_prefix(_normalize_text(line))
        if fallback:
            return fallback
    return ""


def _strip_title_prefix(value: str) -> str:
    if not value:
        return ""
    for prefix in ("招聘", "诚聘", "急招"):
        if value.startswith(prefix):
            return value.removeprefix(prefix).strip()
    return value


def _coalesce_summary(candidate: str, job_description: str, hiring_notes: str) -> str:
    summary = _normalize_text(candidate)
    if summary:
        return summary[:240]
    segments = re.split(r"[。！？.!?\n]+", f"{job_description} {hiring_notes}")
    for segment in segments:
        clean = _normalize_text(segment)
        if clean:
            return clean[:240]
    return ""


def _canonical_degree(value: str | None) -> str | None:
    clean = _normalize_text(value)
    if not clean or clean == UNLIMITED:
        return None
    for canonical, aliases in DEGREE_PATTERNS:
        if any(alias in clean for alias in aliases):
            return canonical
    return None


def _canonical_gender(value: str | None) -> str | None:
    clean = _normalize_text(value)
    if not clean or clean == UNLIMITED:
        return None
    if "男" in clean:
        return "男"
    if "女" in clean:
        return "女"
    return None


def _ordered_range(lower: int | None, upper: int | None) -> tuple[int | None, int | None]:
    if lower is None or upper is None or lower <= upper:
        return lower, upper
    return upper, lower


__all__ = ["build_input_truth", "normalize_requirement_draft"]
