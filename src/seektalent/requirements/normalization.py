from __future__ import annotations

import hashlib
import re

from seektalent.locations import normalize_locations
from seektalent.models import (
    AgeRequirement,
    DegreeRequirement,
    ExperienceRequirement,
    GenderRequirement,
    HardConstraintSlots,
    InputTruth,
    PreferenceSlots,
    RequirementDigest,
    RequirementExtractionDraft,
    RequirementSheet,
    SchoolTypeRequirement,
    ScoringPolicy,
    unique_strings,
)
from seektalent.retrieval.query_compiler import compile_query_term_pool

UNLIMITED = "不限"
DEGREE_PATTERNS = (
    ("博士及以上", ("博士及以上", "博士以上", "博士研究生及以上")),
    ("硕士及以上", ("硕士及以上", "研究生及以上", "硕士以上", "研究生以上", "硕士研究生及以上")),
    ("本科及以上", ("本科及以上", "学士及以上", "本科以上", "本科以上学历", "统招本科及以上", "全日制本科及以上")),
    ("大专及以上", ("大专及以上", "专科及以上", "大专以上", "专科以上", "统招大专及以上")),
    ("博士", ("博士", "博士研究生")),
    ("硕士", ("硕士", "研究生", "硕士研究生")),
    ("本科", ("本科", "学士", "统招本科", "全日制本科")),
    ("大专", ("大专", "专科", "统招大专", "全日制大专")),
)
SCHOOL_TYPE_PATTERNS = {
    "985": ("985", "985院校", "985高校"),
    "211": ("211", "211院校", "211高校"),
    "双一流": ("双一流", "双一流院校", "双一流高校", "一流大学建设高校"),
    "统招": ("统招", "统招要求", "全日制", "全日制统招"),
    "海外": ("海外", "海归", "留学", "海外留学"),
    "强基计划": ("强基计划",),
    "双高计划": ("双高计划",),
    "THE100": ("the100", "the 100", "the前100", "times前100", "qs前100", "qs100", "top100", "世界前100"),
}
CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def build_input_truth(*, job_title: str, jd: str, notes: str) -> InputTruth:
    return InputTruth(
        job_title=job_title,
        jd=jd,
        notes=notes,
        job_title_sha256=hashlib.sha256(job_title.encode("utf-8")).hexdigest(),
        jd_sha256=hashlib.sha256(jd.encode("utf-8")).hexdigest(),
        notes_sha256=hashlib.sha256(notes.encode("utf-8")).hexdigest(),
    )


def normalize_requirement_draft(draft: RequirementExtractionDraft, *, job_title: str) -> RequirementSheet:
    role_title = _clean_text(job_title)
    title_anchor_term = _clean_text(draft.title_anchor_term)
    role_summary = _clean_text(draft.role_summary)
    scoring_rationale = _clean_text(draft.scoring_rationale)
    if not role_title:
        raise ValueError("role_title must not be empty after normalization")
    if not title_anchor_term:
        raise ValueError("title_anchor_term must not be empty after normalization")
    if not role_summary:
        raise ValueError("role_summary must not be empty after normalization")
    if not scoring_rationale:
        raise ValueError("scoring_rationale must not be empty after normalization")
    must_have = _clean_list(draft.must_have_capabilities, limit=8)
    preferred = _clean_list(draft.preferred_capabilities, limit=8)
    preferred_query_terms = _clean_list(draft.preferred_query_terms, limit=8)
    jd_query_terms = [
        term
        for term in _clean_list(draft.jd_query_terms, limit=8)
        if term.casefold() != title_anchor_term.casefold()
    ]
    notes_query_terms = [
        term
        for term in _clean_list(draft.notes_query_terms, limit=8)
        if term.casefold() != title_anchor_term.casefold()
    ]
    if not jd_query_terms:
        raise ValueError("jd_query_terms must contain at least one non-anchor term after normalization")
    allowed_locations = normalize_locations(draft.locations)
    preferred_locations = _normalize_preferred_locations(
        allowed_locations=allowed_locations,
        preferred_locations=draft.preferred_locations,
    )
    hard_constraints = HardConstraintSlots(
        locations=allowed_locations,
        school_names=_clean_list(draft.school_names, limit=6),
        degree_requirement=_normalize_degree_requirement(draft.degree_requirement),
        school_type_requirement=_normalize_school_type_requirement(draft.school_type_requirement),
        experience_requirement=_normalize_experience_requirement(draft.experience_requirement),
        gender_requirement=_normalize_gender_requirement(draft.gender_requirement),
        age_requirement=_normalize_age_requirement(draft.age_requirement),
        company_names=_clean_list(draft.company_names, limit=6),
    )
    preferences = PreferenceSlots(
        preferred_locations=preferred_locations,
        preferred_companies=_clean_list(draft.preferred_companies, limit=6),
        preferred_domains=_clean_list(draft.preferred_domains, limit=4),
        preferred_backgrounds=_clean_list(draft.preferred_backgrounds, limit=6),
        preferred_query_terms=preferred_query_terms[:4],
    )
    return RequirementSheet(
        role_title=role_title,
        title_anchor_term=title_anchor_term,
        role_summary=role_summary,
        must_have_capabilities=must_have,
        preferred_capabilities=preferred,
        exclusion_signals=_clean_list(draft.exclusion_signals, limit=8),
        hard_constraints=hard_constraints,
        preferences=preferences,
        initial_query_term_pool=compile_query_term_pool(
            job_title=role_title,
            title_anchor_term=title_anchor_term,
            jd_query_terms=jd_query_terms,
            notes_query_terms=notes_query_terms,
            hard_constraints=hard_constraints,
            preferences=preferences,
        ),
        scoring_rationale=scoring_rationale,
    )


def build_requirement_digest(requirement_sheet: RequirementSheet) -> RequirementDigest:
    summary: list[str] = []
    top_preferences = list(requirement_sheet.preferred_capabilities[:4])
    if requirement_sheet.hard_constraints.locations:
        summary.append(f"location={','.join(requirement_sheet.hard_constraints.locations)}")
    if requirement_sheet.preferences.preferred_locations:
        top_preferences = [
            f"preferred_location_order={','.join(requirement_sheet.preferences.preferred_locations)}",
            *top_preferences,
        ]
    if requirement_sheet.hard_constraints.degree_requirement is not None:
        summary.append(f"degree={requirement_sheet.hard_constraints.degree_requirement.canonical_degree}")
    return RequirementDigest(
        role_title=requirement_sheet.role_title,
        role_summary=requirement_sheet.role_summary,
        top_must_have_capabilities=requirement_sheet.must_have_capabilities[:4],
        top_preferences=top_preferences[:4],
        hard_constraint_summary=summary,
    )


def build_scoring_policy(requirement_sheet: RequirementSheet) -> ScoringPolicy:
    return ScoringPolicy.model_validate(
        {
            "role_title": requirement_sheet.role_title,
            "role_summary": requirement_sheet.role_summary,
            "must_have_capabilities": requirement_sheet.must_have_capabilities,
            "preferred_capabilities": requirement_sheet.preferred_capabilities,
            "exclusion_signals": requirement_sheet.exclusion_signals,
            "hard_constraints": requirement_sheet.hard_constraints.model_dump(mode="json"),
            "preferences": requirement_sheet.preferences.model_dump(mode="json"),
            "scoring_rationale": requirement_sheet.scoring_rationale,
        }
    )


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split()).strip()


def _clean_list(values: list[str], *, limit: int) -> list[str]:
    return unique_strings([_clean_text(value) for value in values if _clean_text(value)])[:limit]


def _normalize_degree_requirement(value: str | None) -> DegreeRequirement | None:
    clean = _clean_text(value)
    if not clean:
        return None
    if clean == UNLIMITED:
        return DegreeRequirement(canonical_degree=UNLIMITED, raw_text=clean)
    for canonical, aliases in DEGREE_PATTERNS:
        if clean == canonical or any(alias in clean for alias in aliases):
            return DegreeRequirement(canonical_degree=canonical, raw_text=clean)
    return DegreeRequirement(canonical_degree=clean, raw_text=clean)


def _normalize_school_type_requirement(values: list[str]) -> SchoolTypeRequirement | None:
    clean_values = _clean_list(values, limit=6)
    if not clean_values:
        return None
    if UNLIMITED in clean_values:
        return SchoolTypeRequirement(canonical_types=[UNLIMITED], raw_text=UNLIMITED)
    canonical_types: list[str] = []
    for value in clean_values:
        matched = [
            canonical
            for canonical, aliases in SCHOOL_TYPE_PATTERNS.items()
            if value.casefold() == canonical.casefold() or any(alias in value.casefold() for alias in aliases)
        ]
        canonical_types.extend(matched or [value])
    return SchoolTypeRequirement(
        canonical_types=unique_strings(canonical_types),
        raw_text="；".join(clean_values),
    )


def _normalize_experience_requirement(value: str | None) -> ExperienceRequirement | None:
    clean = _normalize_numeric_text(_clean_text(value))
    if not clean:
        return None
    if any(token in clean for token in ("经验不限", "不限经验", "年限不限", UNLIMITED)):
        return ExperienceRequirement(raw_text=clean)
    range_match = re.search(r"(\d{1,2})\s*(?:-|~|至|到)\s*(\d{1,2})\s*(?:年)?", clean)
    if range_match:
        min_years, max_years = sorted((int(range_match.group(1)), int(range_match.group(2))))
        return ExperienceRequirement(min_years=min_years, max_years=max_years, raw_text=clean)
    min_match = re.search(r"(\d{1,2})\s*(?:年)?(?:以上|及以上|\+|起)", clean)
    if min_match:
        return ExperienceRequirement(min_years=int(min_match.group(1)), raw_text=clean)
    max_match = re.search(r"(\d{1,2})\s*(?:年)?(?:以下|以内)", clean)
    if max_match:
        return ExperienceRequirement(max_years=int(max_match.group(1)), raw_text=clean)
    return ExperienceRequirement(raw_text=clean)


def _normalize_gender_requirement(value: str | None) -> GenderRequirement | None:
    clean = _clean_text(value)
    if not clean:
        return None
    if any(token in clean for token in ("性别不限", "男女不限", UNLIMITED)):
        return GenderRequirement(canonical_gender=UNLIMITED, raw_text=clean)
    if any(token in clean for token in ("男性", "男生", "男士")) or clean == "男":
        return GenderRequirement(canonical_gender="男", raw_text=clean)
    if any(token in clean for token in ("女性", "女生", "女士")) or clean == "女":
        return GenderRequirement(canonical_gender="女", raw_text=clean)
    if clean == "未知":
        return GenderRequirement(canonical_gender="未知", raw_text=clean)
    return GenderRequirement(canonical_gender=clean, raw_text=clean)


def _normalize_age_requirement(value: str | None) -> AgeRequirement | None:
    clean = _normalize_numeric_text(_clean_text(value))
    if not clean:
        return None
    if any(token in clean for token in ("年龄不限", "不限年龄", UNLIMITED)):
        return AgeRequirement(raw_text=clean)
    range_match = re.search(r"(\d{1,2})\s*(?:-|~|至|到)\s*(\d{1,2})\s*(?:岁)?", clean)
    if range_match:
        min_age, max_age = sorted((int(range_match.group(1)), int(range_match.group(2))))
        return AgeRequirement(min_age=min_age, max_age=max_age, raw_text=clean)
    min_match = re.search(r"(\d{1,2})\s*(?:岁)?(?:以上|及以上|起)", clean)
    if min_match:
        return AgeRequirement(min_age=int(min_match.group(1)), raw_text=clean)
    max_match = re.search(r"(\d{1,2})\s*(?:岁)?(?:以下|以内)", clean)
    if max_match:
        return AgeRequirement(max_age=int(max_match.group(1)), raw_text=clean)
    return AgeRequirement(raw_text=clean)


def _normalize_preferred_locations(
    *,
    allowed_locations: list[str],
    preferred_locations: list[str],
) -> list[str]:
    if len(allowed_locations) <= 1:
        return []
    allowed_keys = {value.casefold() for value in allowed_locations}
    cleaned = normalize_locations(preferred_locations)
    return [value for value in cleaned if value.casefold() in allowed_keys]


def _normalize_numeric_text(value: str) -> str:
    return re.sub(r"[零〇一二两三四五六七八九十]+", _replace_chinese_number, value)


def _replace_chinese_number(match: re.Match[str]) -> str:
    token = match.group(0)
    if "十" not in token:
        return "".join(str(CHINESE_DIGITS[char]) for char in token)
    left, _, right = token.partition("十")
    tens = 1 if not left else CHINESE_DIGITS[left]
    ones = 0 if not right else CHINESE_DIGITS[right]
    return str(tens * 10 + ones)
