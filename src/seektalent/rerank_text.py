from __future__ import annotations

from seektalent.models import RequirementSheet, stable_deduplicate


def build_rerank_query_text(requirement_sheet: RequirementSheet) -> str:
    truth_gate = requirement_sheet.hard_constraints
    parts = [f"招聘岗位：{requirement_sheet.role_title}"]
    if _normalize_text(requirement_sheet.role_summary):
        parts.append(f"岗位概述：{requirement_sheet.role_summary}")
    must_have = stable_deduplicate(list(requirement_sheet.must_have_capabilities))
    if must_have:
        parts.append(f"必须条件：{', '.join(must_have)}")
    locations = stable_deduplicate(list(truth_gate.locations))
    if locations:
        parts.append(f"工作地点：{', '.join(locations)}")
    if truth_gate.min_years is not None:
        parts.append(f"最低工作年限：{truth_gate.min_years}年")
    if truth_gate.max_years is not None:
        parts.append(f"最高工作年限：{truth_gate.max_years}年")
    if _normalize_text(truth_gate.degree_requirement):
        parts.append(f"学历要求：{truth_gate.degree_requirement}")
    company_names = stable_deduplicate(list(truth_gate.company_names))
    if company_names:
        parts.append(f"目标公司背景：{', '.join(company_names)}")
    school_names = stable_deduplicate(list(truth_gate.school_names))
    if school_names:
        parts.append(f"目标学校背景：{', '.join(school_names)}")
    preferred = stable_deduplicate(list(requirement_sheet.preferred_capabilities))
    if preferred:
        parts.append(f"优先条件：{', '.join(preferred)}")
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
