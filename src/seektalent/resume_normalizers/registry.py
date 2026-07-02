from __future__ import annotations

from collections.abc import Callable

from seektalent.models import NormalizedResume, ResumeCandidate
from seektalent.resume_normalizers.cts import normalize_cts_resume
from seektalent.resume_normalizers.liepin import normalize_liepin_resume

ResumeNormalizer = Callable[[ResumeCandidate], NormalizedResume]
NORMALIZERS: dict[str, ResumeNormalizer] = {"cts": normalize_cts_resume, "liepin": normalize_liepin_resume}


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
    liepin_text_aliases = {
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
    liepin_structured_keys = {
        "activeStatus",
        "candidateName",
        "currentCompany",
        "currentTitle",
        "jobIntention",
        "jobStatus",
        "projectExperienceList",
        "safeCardSummary",
        "safe_card_summary",
        "skillTags",
        "sourceUrl",
        "workYears",
    }
    return bool(set(raw) & (liepin_text_aliases | liepin_structured_keys))
