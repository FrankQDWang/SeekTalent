from __future__ import annotations

import re

from seektalent.models import (
    HardConstraintSlots,
    PreferenceSlots,
    QueryTermCandidate,
    QueryTermCategory,
    QueryTermSource,
    Queryability,
    QueryRetrievalRole,
    unique_strings,
)

ACTIVE_NON_ANCHOR_WINDOW = 6
TITLE_SUFFIXES = (
    "算法工程师",
    "开发工程师",
    "工程师",
    "架构师",
    "专家",
    "经理",
    "负责人",
    "岗位",
    "职位",
)
TITLE_PREFIX_SEPARATORS = ("-", "－", "–", "—", "|", "｜", ":", "：")
TITLE_PREFIX_HINTS = ("业务线", "业务", "品牌", "团队", "部门", "项目")
SCHOOL_TYPE_TERMS = {"985", "211", "双一流", "统招", "全日制", "海外", "海归", "强基计划", "双高计划", "the100"}
DEGREE_TOKENS = ("博士", "硕士", "研究生", "本科", "学士", "大专", "专科", "学历")
ABSTRACT_PATTERNS = (
    "任务拆解",
    "长链路业务问题",
    "复杂业务问题",
    "业务问题",
    "沟通",
    "协作",
    "抗压",
    "主人翁",
    "自驱",
    "抽象能力",
)
BLOCKED_PATTERNS = ("agentloop", "veadk", "googleadk")
FILTER_ONLY_PATTERNS = (
    "薪资",
    "薪酬",
    "年薪",
    "月薪",
    "预算",
    "面试",
    "到岗",
    "离职",
    "出差",
    "出国",
    "目标公司",
    "公司范围",
)
GENERIC_NOTES_PREFIXES = ("base",)
GENERIC_NOTES_PATTERNS = (
    "结果导向",
    "逻辑能力",
    "英文流利",
    "创业公司",
)
GENERIC_NOTES_SUFFIXES = ("能力", "导向", "流利", "公司")


def compile_query_term_pool(
    *,
    job_title: str,
    title_anchor_terms: list[str],
    title_anchor_term: str | None = None,
    jd_query_terms: list[str],
    notes_query_terms: list[str],
    hard_constraints: HardConstraintSlots | None = None,
    preferences: PreferenceSlots | None = None,
) -> list[QueryTermCandidate]:
    pool: list[QueryTermCandidate] = []
    seen: set[str] = set()
    active_non_anchor_count = 0
    constraint_keys = _constraint_keys(hard_constraints, preferences)

    def add_candidate(
        *,
        term: str,
        source: QueryTermSource,
        evidence: str,
        priority: int,
        role: QueryRetrievalRole | None = None,
        queryability: Queryability | None = None,
        category: QueryTermCategory | None = None,
        family: str | None = None,
    ) -> None:
        nonlocal active_non_anchor_count
        clean = _clean_text(term)
        if not clean:
            return
        dedupe_key = clean.casefold()
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        inferred_role, inferred_queryability, inferred_category, inferred_family = _classify_term(clean, constraint_keys)
        role = role or inferred_role
        queryability = queryability or inferred_queryability
        category = category or inferred_category
        family = family or inferred_family
        if source == "notes":
            if queryability == "admitted" and not _should_admit_notes_term(clean):
                return
        active = queryability == "admitted"
        if role not in {"primary_role_anchor", "secondary_title_anchor", "role_anchor"}:
            if active:
                active_non_anchor_count += 1
                active = active_non_anchor_count <= ACTIVE_NON_ANCHOR_WINDOW
        candidate = QueryTermCandidate(
            term=clean,
            source=source,
            category=category,
            priority=priority,
            evidence=evidence,
            first_added_round=0,
            active=active,
            retrieval_role=role,
            queryability=queryability,
            family=family,
        )
        pool.append(candidate)

    priority = 1
    for index, anchor in enumerate(_compile_title_anchors(
        job_title=job_title,
        title_anchor_terms=title_anchor_terms,
        title_anchor_term=title_anchor_term,
    )):
        add_candidate(
            term=anchor,
            source="job_title",
            category="role_anchor",
            role="primary_role_anchor" if index == 0 else "secondary_title_anchor",
            queryability="admitted",
            family=_family_for_role(anchor),
            priority=priority,
            evidence="Compiled job title anchor.",
        )
        priority += 1

    for term, source in _merge_query_terms(jd_query_terms=jd_query_terms, notes_query_terms=notes_query_terms, limit=8):
        add_candidate(
            term=term,
            source=source,
            priority=priority,
            evidence="JD query term." if source == "jd" else "Notes query term.",
        )
        priority += 1

    if not any(item.queryability == "admitted" and item.retrieval_role == "primary_role_anchor" for item in pool):
        raise ValueError("query compiler produced no admitted role anchor")
    if not any(
        item.queryability == "admitted"
        and item.retrieval_role not in {"primary_role_anchor", "secondary_title_anchor", "role_anchor"}
        for item in pool
    ):
        raise ValueError("query compiler produced no admitted non-anchor terms")
    return pool


def _compile_title_anchors(
    *,
    job_title: str,
    title_anchor_terms: list[str],
    title_anchor_term: str | None = None,
) -> list[str]:
    compiled: list[str] = []
    for value in unique_strings(title_anchor_terms):
        anchor = _clean_title_anchor(value)
        if anchor:
            compiled.append(anchor)
        if len(unique_strings(compiled)) == 2:
            return unique_strings(compiled)
    fallback = _clean_title_anchor(title_anchor_term) or _clean_title_anchor(job_title) or _clean_text(job_title)
    if not compiled and fallback:
        compiled.append(fallback)
    return unique_strings(compiled)[:2]


def _classify_term(term: str, constraint_keys: set[str]) -> tuple[QueryRetrievalRole, Queryability, QueryTermCategory, str]:
    key = term.casefold()
    compact = _compact_key(term)
    if key in constraint_keys or compact in constraint_keys or _is_filter_only(term, compact):
        return "filter_only", "filter_only", "domain", _filter_family(term, compact)
    if any(pattern in compact for pattern in BLOCKED_PATTERNS):
        return "score_only", "blocked", "expansion", f"blocked.{compact}"
    if any(pattern in key or pattern in term for pattern in ABSTRACT_PATTERNS):
        return "score_only", "score_only", "expansion", f"score.{compact or 'abstract'}"
    return "domain_context", "admitted", "domain", f"domain.{compact or 'unknown'}"


def _should_admit_notes_term(term: str) -> bool:
    compact = _compact_key(term)
    if not compact:
        return False
    key = term.casefold()
    if _is_filter_only(term, compact):
        return False
    if any(pattern in compact for pattern in BLOCKED_PATTERNS):
        return False
    if _is_abstract_notes_term(term):
        return False
    if any(pattern in key or pattern in term for pattern in GENERIC_NOTES_PATTERNS):
        return False
    if any(key.startswith(prefix) for prefix in GENERIC_NOTES_PREFIXES):
        return False
    if any(term.endswith(suffix) for suffix in GENERIC_NOTES_SUFFIXES):
        return False
    return _looks_like_domain_notes_term(term)


def _is_abstract_notes_term(term: str) -> bool:
    key = term.casefold()
    return any(pattern in key or pattern in term for pattern in ABSTRACT_PATTERNS)


def _looks_like_domain_notes_term(term: str) -> bool:
    compact = _compact_key(term)
    if len(compact) < 2 or len(compact) > 12:
        return False
    has_ascii = any(char.isascii() and char.isalpha() for char in term)
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in term)
    if has_ascii:
        if has_cjk:
            return True
        return " " not in term and any(char.isupper() for char in term)
    return len(term) >= 3


def _is_filter_only(term: str, compact: str) -> bool:
    if compact in SCHOOL_TYPE_TERMS:
        return True
    if any(token in term for token in DEGREE_TOKENS):
        return True
    if re.fullmatch(r"\d{1,2}(?:-|~|至|到)?\d{0,2}年(?:以上|以下|以内|经验)?", term):
        return True
    if re.fullmatch(r"\d{1,2}(?:-|~|至|到)?\d{0,2}岁(?:以上|以下|以内)?", term):
        return True
    if any(pattern.casefold() in term.casefold() for pattern in FILTER_ONLY_PATTERNS):
        return True
    return term in {"男", "女", "男性", "女性", "男女不限", "性别不限", "不限"}


def _filter_family(term: str, compact: str) -> str:
    if compact in SCHOOL_TYPE_TERMS:
        return "constraint.school_type"
    if any(token in term for token in DEGREE_TOKENS):
        return "constraint.degree"
    if "岁" in term or "年龄" in term:
        return "constraint.age"
    if term in {"男", "女", "男性", "女性", "男女不限", "性别不限"}:
        return "constraint.gender"
    if "年" in term or "经验" in term:
        return "constraint.experience"
    return "constraint.filter"


def _constraint_keys(
    hard_constraints: HardConstraintSlots | None,
    preferences: PreferenceSlots | None,
) -> set[str]:
    values: list[str] = []
    if hard_constraints is not None:
        values.extend(hard_constraints.locations)
        values.extend(hard_constraints.school_names)
        values.extend(hard_constraints.company_names)
        if hard_constraints.degree_requirement is not None:
            values.extend([
                hard_constraints.degree_requirement.canonical_degree,
                hard_constraints.degree_requirement.raw_text,
            ])
        if hard_constraints.school_type_requirement is not None:
            values.extend(hard_constraints.school_type_requirement.canonical_types)
            values.append(hard_constraints.school_type_requirement.raw_text)
        if hard_constraints.experience_requirement is not None:
            values.append(hard_constraints.experience_requirement.raw_text)
        if hard_constraints.gender_requirement is not None:
            values.extend([
                hard_constraints.gender_requirement.canonical_gender,
                hard_constraints.gender_requirement.raw_text,
            ])
        if hard_constraints.age_requirement is not None:
            values.append(hard_constraints.age_requirement.raw_text)
    if preferences is not None:
        values.extend(preferences.preferred_locations)
        values.extend(preferences.preferred_companies)
    keys: set[str] = set()
    for value in values:
        clean = _clean_text(str(value))
        if not clean:
            continue
        keys.add(clean.casefold())
        keys.add(_compact_key(clean))
    return keys


def _merge_query_terms(
    *,
    jd_query_terms: list[str],
    notes_query_terms: list[str],
    limit: int,
) -> list[tuple[str, QueryTermSource]]:
    merged: list[tuple[str, QueryTermSource]] = []
    seen: set[str] = set()
    for terms, source in ((jd_query_terms, "jd"), (notes_query_terms, "notes")):
        for term in terms:
            clean = _clean_text(term)
            key = clean.casefold()
            if not clean or key in seen:
                continue
            seen.add(key)
            merged.append((clean, source))
            if len(merged) >= limit:
                return merged
    return merged


def _strip_title_suffix(value: str) -> str:
    clean = _clean_text(value)
    for suffix in TITLE_SUFFIXES:
        if clean.casefold().endswith(suffix.casefold()):
            return clean[: -len(suffix)].strip()
    return clean


def _clean_title_anchor(value: str) -> str:
    clean = _clean_text(value)
    for separator in TITLE_PREFIX_SEPARATORS:
        if separator not in clean:
            continue
        left, right = clean.split(separator, 1)
        right_anchor = _strip_title_suffix(right)
        if right_anchor and _looks_like_title_prefix(left, right_anchor):
            return right_anchor
    return _strip_title_suffix(clean)


def _looks_like_title_prefix(left: str, right_anchor: str) -> bool:
    left_key = _compact_key(left)
    if not left_key or len(left_key) > 12:
        return False
    if any(char.isascii() and char.isalpha() for char in right_anchor):
        return True
    return any(hint in left for hint in TITLE_PREFIX_HINTS)


def _family_for_role(term: str) -> str:
    compact = _compact_key(term)
    return f"role.{compact or 'unknown'}"


def _compact_key(value: str) -> str:
    return "".join(char.casefold() for char in value if char.isalnum())


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split()).strip()
