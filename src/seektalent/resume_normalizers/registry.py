from __future__ import annotations

from collections.abc import Callable

from seektalent.models import NormalizedResume, ResumeCandidate
from seektalent.resume_normalizers.cts import normalize_cts_resume
from seektalent.resume_normalizers.liepin import normalize_liepin_resume

ResumeNormalizer = Callable[[ResumeCandidate], NormalizedResume]
NORMALIZERS: dict[str, ResumeNormalizer] = {"cts": normalize_cts_resume, "liepin": normalize_liepin_resume}
LIEPIN_WHOLE_PAGE_TEXT_KEYS = frozenset(
    {
        "fullText",
        "full_text",
        "rawText",
        "raw_text",
        "page_text",
        "pageText",
        "resumeText",
        "resume_text",
        "resume_free_text",
        "detailBody",
        "detail_body",
    }
)
LIEPIN_STRONG_SINGLE_KEYS = frozenset({"safeCardSummary", "safe_card_summary"})
LIEPIN_JOB_INTENTION_COMPANION_KEYS = frozenset(
    {
        "activeStatus",
        "candidate_name",
        "candidateName",
        "currentCompany",
        "currentTitle",
        "jobStatus",
        "projectExperienceList",
        "skillTags",
        "workExperienceList",
        "workYears",
    }
)


def normalizer_key_for_candidate(candidate: ResumeCandidate) -> str:
    provider = candidate.raw.get("provider") or candidate.raw.get("source") or candidate.raw.get("source_provider")
    if isinstance(provider, str) and provider.strip():
        key = provider.strip().casefold()
        if key not in NORMALIZERS and _has_liepin_shape(candidate.raw):
            raise ValueError("Unsupported or unmigrated Liepin-shaped resume payload")
        return key
    if _has_liepin_shape(candidate.raw):
        raise ValueError("Unsupported or unmigrated Liepin-shaped resume payload")
    return "cts"


def normalize_resume(candidate: ResumeCandidate) -> NormalizedResume:
    key = normalizer_key_for_candidate(candidate)
    normalizer = NORMALIZERS.get(key)
    if normalizer is None:
        raise ValueError(f"Unsupported resume normalizer source: {key}")
    return normalizer(candidate)


def _legacy_normalize_resume(candidate: ResumeCandidate) -> NormalizedResume:
    return normalize_cts_resume(candidate)


def _has_liepin_shape(raw: dict[str, object]) -> bool:
    keys = set(raw)
    if keys & LIEPIN_WHOLE_PAGE_TEXT_KEYS:
        return True
    if keys & LIEPIN_STRONG_SINGLE_KEYS:
        return True
    if _is_liepin_url(raw.get("sourceUrl")):
        return True
    return "jobIntention" in keys and bool(keys & LIEPIN_JOB_INTENTION_COMPANION_KEYS)


def _is_liepin_url(value: object) -> bool:
    return isinstance(value, str) and "liepin.com" in value.casefold()
