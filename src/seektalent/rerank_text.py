from __future__ import annotations

from seektalent.models import RequirementSheet, stable_deduplicate


def build_rerank_query_text(requirement_sheet: RequirementSheet) -> str:
    truth_gate = requirement_sheet.hard_constraints
    parts = [f"Hiring for {requirement_sheet.role_title}"]
    if _normalize_text(requirement_sheet.role_summary):
        parts.append(f"Role summary: {requirement_sheet.role_summary}")
    must_have = stable_deduplicate(list(requirement_sheet.must_have_capabilities))
    if must_have:
        parts.append(f"Must have {', '.join(must_have)}")
    locations = stable_deduplicate(list(truth_gate.locations))
    if locations:
        parts.append(f"Location: {', '.join(locations)}")
    if truth_gate.min_years is not None:
        parts.append(f"Minimum {truth_gate.min_years} years of experience")
    if truth_gate.max_years is not None:
        parts.append(f"Maximum {truth_gate.max_years} years of experience")
    if _normalize_text(truth_gate.degree_requirement):
        parts.append(f"Degree requirement: {truth_gate.degree_requirement}")
    company_names = stable_deduplicate(list(truth_gate.company_names))
    if company_names:
        parts.append(f"Target company background: {', '.join(company_names)}")
    school_names = stable_deduplicate(list(truth_gate.school_names))
    if school_names:
        parts.append(f"Target school background: {', '.join(school_names)}")
    preferred = stable_deduplicate(list(requirement_sheet.preferred_capabilities))
    if preferred:
        parts.append(f"Preferred {', '.join(preferred)}")
    return " ".join(_sentence(part) for part in parts if _normalize_text(part))


def _sentence(value: str) -> str:
    clean = _normalize_text(value)
    if not clean:
        return ""
    if clean.endswith((".", "!", "?", "。", "！", "？")):
        return clean
    return f"{clean}."


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


__all__ = ["build_rerank_query_text"]
