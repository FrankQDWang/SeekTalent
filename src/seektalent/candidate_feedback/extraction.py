from __future__ import annotations

import re
from collections import Counter, defaultdict

from seektalent.candidate_feedback.models import (
    CandidateFeedbackDecision,
    FeedbackCandidateExpression,
    FeedbackCandidateTerm,
)
from seektalent.models import QueryTermCandidate, ScoredCandidate, is_primary_anchor_role

GENERIC_TERMS = {
    "平台",
    "系统",
    "项目",
    "开发",
    "负责",
    "熟悉",
    "业务",
    "管理",
    "优化",
    "架构",
    "能力",
    "经验",
    "platform",
    "system",
    "project",
    "development",
    "develop",
    "responsible",
    "familiar",
    "business",
    "build",
    "built",
    "management",
    "optimization",
    "architecture",
    "ability",
    "experience",
}

FILTER_LIKE_RE = re.compile(
    r"(?:"
    r"\d{1,2}(?:\.\d+)?\s*(?:年|岁)(?:经验)?"
    r"|(?:本科|硕士|博士|大专|专科|学士|学历)"
    r"|(?:985|211|双一流|全日制|统招|海归|海外|学校|院校|高校)"
    r"|(?:薪资|薪酬|年薪|月薪|预算|面试|到岗|离职|出差|出国)"
    r"|(?:北京|上海|广州|深圳|杭州|成都|南京|苏州|武汉|重庆|天津|西安|长沙|厦门|济南|郑州)"
    r"|(?:男|女|男性|女性|男女不限|性别不限|不限)"
    r")"
)

_COMMON_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "used",
    "using",
    "we",
    "with",
}

_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,})*\b")
_CAMEL_CASE_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_MIXED_CASE_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b")
_SYMBOL_TOKEN_RE = re.compile(r"\b[A-Za-z0-9]+(?:[./+_-][A-Za-z0-9]+)+\b|C\+\+|C#")
_SHORT_ENGLISH_PHRASE_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9.+#-]*\s+[A-Za-z][A-Za-z0-9.+#-]*\b")
_SHORT_CHINESE_PHRASE_RE = re.compile(r"[\u4e00-\u9fff]{2,6}")
_SURFACE_PATTERNS = (
    _SYMBOL_TOKEN_RE,
    _CAMEL_CASE_RE,
    _ACRONYM_RE,
    _SHORT_ENGLISH_PHRASE_RE,
    _SHORT_CHINESE_PHRASE_RE,
)
_EXPRESSION_SURFACE_PATTERNS = (
    _SYMBOL_TOKEN_RE,
    _CAMEL_CASE_RE,
    _MIXED_CASE_TOKEN_RE,
    _ACRONYM_RE,
    _SHORT_ENGLISH_PHRASE_RE,
    _SHORT_CHINESE_PHRASE_RE,
)
_COMPANY_ENTITY_SUFFIX_RE = re.compile(
    r"(?:"
    r"inc|corp|llc|ltd|limited|company|technologies|technology|software"
    r"|公司|集团|科技|信息|软件|网络|股份|有限"
    r")$",
    re.IGNORECASE,
)
_PRODUCT_OR_PLATFORM_HINT_RE = re.compile(
    r"(?:"
    r"ai|gpt|copilot|sdk|api|cloud|platform|studio|workspace|graph|chain|db"
    r")",
    re.IGNORECASE,
)
_KNOWN_COMPANY_ENTITIES = {
    "amazon",
    "apple",
    "bytedance",
    "google",
    "meta",
    "microsoft",
    "openai",
    "salesforce",
    "tencent",
}


def select_feedback_seed_resumes(candidates: list[ScoredCandidate], *, limit: int = 5) -> list[ScoredCandidate]:
    limit = min(limit, 5)
    selected = [
        candidate
        for candidate in candidates
        if candidate.fit_bucket == "fit"
        and candidate.overall_score >= 75
        and candidate.must_have_match_score >= 70
        and candidate.risk_score <= 45
    ]
    selected.sort(key=lambda candidate: (-candidate.overall_score, -candidate.must_have_match_score, candidate.risk_score, candidate.resume_id))
    return selected[:limit]


def extract_surface_terms(texts: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for text in texts:
        clean = _clean_term(text)
        if not clean:
            continue
        for pattern in _SURFACE_PATTERNS:
            for match in pattern.finditer(clean):
                term = _clean_term(match.group(0))
                key = _term_key(term)
                if not _is_allowed_surface_term(term) or key in seen:
                    continue
                seen.add(key)
                terms.append(term)
    return terms


def normalize_expression(expression: str | None) -> str:
    return _clean_term(expression)


def build_term_family_id(expression: str) -> str:
    normalized = normalize_expression(expression)
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", normalized.casefold()).strip("-")
    return f"feedback.{slug or 'unknown'}"


def classify_candidate_expression(expression: str) -> FeedbackCandidateExpression:
    normalized = normalize_expression(expression)
    rejection_reason: str | None = None
    reject_reasons: list[str] = []

    if not normalized:
        candidate_term_type = "technical_phrase"
        rejection_reason = "empty_expression"
    elif not _is_allowed_expression_surface_term(normalized):
        candidate_term_type = _candidate_term_type(normalized)
        rejection_reason = "generic_or_filter_like"
    elif _looks_like_company_entity(normalized):
        candidate_term_type = "company_entity"
        rejection_reason = "company_entity"
    else:
        candidate_term_type = _candidate_term_type(normalized)

    if rejection_reason is not None:
        reject_reasons.append(rejection_reason)

    return FeedbackCandidateExpression(
        term_family_id=build_term_family_id(normalized),
        canonical_expression=normalized,
        surface_forms=[normalized] if normalized else [],
        candidate_term_type=candidate_term_type,
        reject_reasons=reject_reasons,
        rejection_reason=rejection_reason,
    )


def extract_feedback_candidate_expressions(
    *,
    seed_resumes: list[ScoredCandidate],
    negative_resumes: list[ScoredCandidate],
    include_rejected: bool = False,
) -> list[FeedbackCandidateExpression]:
    seed_support: dict[str, set[str]] = defaultdict(set)
    negative_support: dict[str, set[str]] = defaultdict(set)
    surface_forms: dict[str, set[str]] = defaultdict(set)
    display_expressions: dict[str, str] = {}

    for resume in seed_resumes:
        for texts in _shared_expression_field_texts(resume).values():
            for expression in _extract_expression_surface_terms(texts):
                family_id = build_term_family_id(expression)
                display_expressions.setdefault(family_id, normalize_expression(expression))
                surface_forms[family_id].add(expression)
                seed_support[family_id].add(resume.resume_id)
    for resume in negative_resumes:
        for texts in _shared_expression_field_texts(resume).values():
            for expression in _extract_expression_surface_terms(texts):
                family_id = build_term_family_id(expression)
                display_expressions.setdefault(family_id, normalize_expression(expression))
                surface_forms[family_id].add(expression)
                negative_support[family_id].add(resume.resume_id)

    expressions: list[FeedbackCandidateExpression] = []
    use_negative_support = len(negative_resumes) >= 3
    for family_id, expression in display_expressions.items():
        classified = classify_candidate_expression(expression)
        seed_ids = sorted(seed_support.get(family_id, set()))
        negative_ids = sorted(negative_support.get(family_id, set()))
        score = float(len(seed_ids) * 4 - (len(negative_ids) * 4 if use_negative_support else 0)) + _expression_shape_bonus(
            expression
        )
        rejection_reason = classified.rejection_reason
        reject_reasons = list(classified.reject_reasons)

        if rejection_reason is None and len(seed_ids) < 2:
            rejection_reason = "insufficient_seed_support"
        elif (
            rejection_reason is None
            and use_negative_support
            and negative_ids
            and len(negative_ids) >= len(seed_ids)
        ):
            rejection_reason = "negative_support_too_high"
        if rejection_reason is not None and rejection_reason not in reject_reasons:
            reject_reasons.append(rejection_reason)

        candidate = FeedbackCandidateExpression(
            term_family_id=family_id,
            canonical_expression=expression,
            surface_forms=sorted(surface_forms.get(family_id, {expression}), key=str.casefold),
            candidate_term_type=classified.candidate_term_type,
            supporting_resume_ids=seed_ids,
            negative_resume_ids=negative_ids,
            fit_support_count=len(seed_ids),
            fit_support_rate=_fit_rate(seed_ids, seed_resumes),
            not_fit_support_count=len(negative_ids),
            not_fit_support_rate=_negative_rate(negative_ids, negative_resumes),
            score=score,
            reject_reasons=reject_reasons,
            rejection_reason=rejection_reason,
        )
        if include_rejected or candidate.rejection_reason is None:
            expressions.append(candidate)

    expressions.sort(key=lambda item: (-item.score, -item.fit_support_count, item.canonical_expression.casefold()))
    return expressions


def build_feedback_decision(
    seed_resumes: list[ScoredCandidate],
    negative_resumes: list[ScoredCandidate],
    existing_terms: list[QueryTermCandidate],
    sent_query_terms: list[str],
    round_no: int,
) -> CandidateFeedbackDecision:
    seed_resume_ids = [resume.resume_id for resume in seed_resumes]
    if len(seed_resumes) < 2:
        return CandidateFeedbackDecision(seed_resume_ids=seed_resume_ids, skipped_reason="fewer_than_two_feedback_seed_resumes")

    anchor = _active_anchor(existing_terms)
    if anchor is None:
        return CandidateFeedbackDecision(seed_resume_ids=seed_resume_ids, skipped_reason="missing_active_anchor")

    excluded_keys = {_term_key(term.term) for term in existing_terms} | {_term_key(term) for term in sent_query_terms}
    scored_terms = _score_terms(
        seed_resumes=seed_resumes,
        negative_resumes=negative_resumes,
        excluded_keys=excluded_keys,
        anchor_term=anchor.term,
        round_no=round_no,
    )

    candidate_terms = sorted(scored_terms, key=lambda item: (-item.score, -len(item.supporting_resume_ids), item.term.casefold()))
    accepted_candidates = [item for item in candidate_terms if item.rejection_reason is None and item.score >= 8]
    accepted_candidate = accepted_candidates[0] if accepted_candidates else None

    if accepted_candidate is None:
        return CandidateFeedbackDecision(
            seed_resume_ids=seed_resume_ids,
            candidate_terms=candidate_terms,
            rejected_terms=[item for item in candidate_terms if item.rejection_reason is not None],
            skipped_reason="no_safe_feedback_term",
        )

    accepted_term = _materialize_term(
        accepted_candidate.term,
        round_no=round_no,
        supporting_resume_ids=accepted_candidate.supporting_resume_ids,
    )
    return CandidateFeedbackDecision(
        seed_resume_ids=seed_resume_ids,
        candidate_terms=candidate_terms,
        rejected_terms=[item for item in candidate_terms if item.rejection_reason is not None],
        accepted_candidates=[accepted_candidate],
        accepted_term=accepted_term,
        forced_query_terms=[anchor.term, accepted_term.term],
    )


def _score_terms(
    *,
    seed_resumes: list[ScoredCandidate],
    negative_resumes: list[ScoredCandidate],
    excluded_keys: set[str],
    anchor_term: str,
    round_no: int,
) -> list[FeedbackCandidateTerm]:
    seed_support: dict[str, set[str]] = defaultdict(set)
    negative_support: dict[str, set[str]] = defaultdict(set)
    field_hits: dict[str, Counter[str]] = defaultdict(Counter)
    display_terms: dict[str, str] = {}

    for resume in seed_resumes:
        for field_name, texts in _resume_field_texts(resume).items():
            for term in extract_surface_terms(texts):
                key = _term_key(term)
                display_terms.setdefault(key, term)
                seed_support[key].add(resume.resume_id)
                field_hits[key][field_name] += 1
    for resume in negative_resumes:
        for texts in _resume_field_texts(resume).values():
            for term in extract_surface_terms(texts):
                key = _term_key(term)
                display_terms.setdefault(key, term)
                negative_support[key].add(resume.resume_id)

    scored: list[FeedbackCandidateTerm] = []
    for key, term in display_terms.items():
        seed_ids = sorted(seed_support.get(key, set()))
        negative_ids = sorted(negative_support.get(key, set()))
        fit_rate = _fit_rate(seed_ids, seed_resumes)
        not_fit_rate = _negative_rate(negative_ids, negative_resumes)
        use_negative_support = len(negative_resumes) >= 3
        negative_penalty = len(negative_ids) if use_negative_support else 0
        score = float(len(seed_ids) * 4 - negative_penalty * 4) + _surface_shape_bonus(term)
        rejection_reason = None
        risk_flags: list[str] = []

        if key in excluded_keys:
            rejection_reason = "existing_or_tried"
        elif not _is_allowed_surface_term(term):
            rejection_reason = "generic_or_filter_like"
        elif len(seed_ids) < 2:
            rejection_reason = "insufficient_seed_support"
        elif use_negative_support and negative_ids and len(negative_ids) >= len(seed_ids):
            rejection_reason = "negative_support_too_high"

        if rejection_reason is not None:
            risk_flags.append(rejection_reason)
        elif score < 8:
            rejection_reason = "insufficient_seed_support"
            risk_flags.append(rejection_reason)

        scored.append(
            FeedbackCandidateTerm(
                term=term,
                supporting_resume_ids=seed_ids,
                linked_requirements=[anchor_term],
                field_hits=dict(field_hits.get(key, {})),
                fit_support_rate=fit_rate,
                not_fit_support_rate=not_fit_rate,
                score=score,
                risk_flags=risk_flags,
                rejection_reason=rejection_reason,
            )
        )
    return scored


def _resume_field_texts(resume: ScoredCandidate) -> dict[str, list[str]]:
    return {
        "reasoning_summary": [resume.reasoning_summary],
        "evidence": list(resume.evidence),
        "strengths": list(resume.strengths),
        "matched_must_haves": list(resume.matched_must_haves),
        "matched_preferences": list(resume.matched_preferences),
    }


def _shared_expression_field_texts(resume: ScoredCandidate) -> dict[str, list[str]]:
    return {
        "evidence": list(resume.evidence),
        "strengths": list(resume.strengths),
        "matched_must_haves": list(resume.matched_must_haves),
        "matched_preferences": list(resume.matched_preferences),
    }


def _active_anchor(existing_terms: list[QueryTermCandidate]) -> QueryTermCandidate | None:
    for term in existing_terms:
        if term.active and term.queryability == "admitted" and is_primary_anchor_role(term.retrieval_role):
            return term
    return None


def _materialize_term(term: str, *, round_no: int, supporting_resume_ids: list[str]) -> QueryTermCandidate:
    return QueryTermCandidate(
        term=term,
        source="candidate_feedback",
        category="expansion",
        priority=1,
        evidence=f"Supported by {len(supporting_resume_ids)} seed resumes: {', '.join(supporting_resume_ids)}.",
        first_added_round=round_no,
        active=True,
        retrieval_role="core_skill",
        queryability="admitted",
        family=f"feedback.{_slug(term)}",
    )


def _fit_rate(seed_ids: list[str], seed_resumes: list[ScoredCandidate]) -> float:
    return len(seed_ids) / len(seed_resumes)


def _negative_rate(negative_ids: list[str], negative_resumes: list[ScoredCandidate]) -> float:
    if not negative_resumes:
        return 0.0
    return len(negative_ids) / len(negative_resumes)


def _clean_term(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split()).strip(" \t\r\n,.;:，。；：!?")


def _is_allowed_surface_term(term: str) -> bool:
    if not term:
        return False
    if FILTER_LIKE_RE.search(term):
        return False
    key = _term_key(term)
    if key in {_term_key(value) for value in GENERIC_TERMS}:
        return False
    lower = term.casefold()
    if any(generic in lower for generic in GENERIC_TERMS if generic.isascii()):
        return False
    if any(generic in term for generic in GENERIC_TERMS if not generic.isascii()):
        return False
    if len(term) > 32:
        return False
    if re.fullmatch(r"[A-Za-z]+(?:\s+[A-Za-z]+){1,2}", term):
        words = [piece.casefold() for piece in term.split()]
        return len(words) == 2 and all(word not in _COMMON_WORDS for word in words)
    return any(pattern.fullmatch(term) for pattern in (_ACRONYM_RE, _CAMEL_CASE_RE, _SYMBOL_TOKEN_RE, _SHORT_CHINESE_PHRASE_RE))


def _surface_shape_bonus(term: str) -> float:
    if any(pattern.fullmatch(term) for pattern in (_ACRONYM_RE, _CAMEL_CASE_RE, _SYMBOL_TOKEN_RE)):
        return 2.0
    pieces = term.split()
    if len(pieces) == 2 and any(
        any(pattern.fullmatch(piece) for pattern in (_ACRONYM_RE, _CAMEL_CASE_RE, _SYMBOL_TOKEN_RE)) for piece in pieces
    ):
        return 1.0
    return 0.0


def _extract_expression_surface_terms(texts: list[str]) -> list[str]:
    expressions: list[str] = []
    seen: set[str] = set()
    for text in texts:
        clean = _clean_term(text)
        if not clean:
            continue
        for pattern in _EXPRESSION_SURFACE_PATTERNS:
            for match in pattern.finditer(clean):
                expression = normalize_expression(match.group(0))
                key = _term_key(expression)
                if not _is_allowed_expression_surface_term(expression) or key in seen:
                    continue
                seen.add(key)
                expressions.append(expression)
    return expressions


def _is_allowed_expression_surface_term(term: str) -> bool:
    if _is_allowed_surface_term(term):
        return True
    if not term:
        return False
    if FILTER_LIKE_RE.search(term):
        return False
    key = _term_key(term)
    if key in {_term_key(value) for value in GENERIC_TERMS}:
        return False
    if len(term) > 32:
        return False
    return _MIXED_CASE_TOKEN_RE.fullmatch(term) is not None


def _candidate_term_type(term: str) -> str:
    if _looks_like_company_entity(term):
        return "company_entity"
    if _looks_like_product_or_platform(term):
        return "product_or_platform"
    if " " in term or any("\u4e00" <= char <= "\u9fff" for char in term):
        return "technical_phrase"
    return "skill"


def _looks_like_company_entity(term: str) -> bool:
    normalized = term.casefold()
    return normalized in _KNOWN_COMPANY_ENTITIES or _COMPANY_ENTITY_SUFFIX_RE.search(normalized) is not None


def _looks_like_product_or_platform(term: str) -> bool:
    if _SYMBOL_TOKEN_RE.fullmatch(term) is not None:
        return True
    if _CAMEL_CASE_RE.fullmatch(term) is not None or _MIXED_CASE_TOKEN_RE.fullmatch(term) is not None:
        return True
    return _PRODUCT_OR_PLATFORM_HINT_RE.search(term) is not None


def _expression_shape_bonus(term: str) -> float:
    if any(pattern.fullmatch(term) for pattern in (_ACRONYM_RE, _CAMEL_CASE_RE, _MIXED_CASE_TOKEN_RE, _SYMBOL_TOKEN_RE)):
        return 2.0
    pieces = term.split()
    if len(pieces) == 2 and any(
        any(pattern.fullmatch(piece) for pattern in (_ACRONYM_RE, _CAMEL_CASE_RE, _MIXED_CASE_TOKEN_RE, _SYMBOL_TOKEN_RE))
        for piece in pieces
    ):
        return 1.0
    return 0.0


def _term_key(value: str) -> str:
    return "".join(char.casefold() for char in value if char.isalnum())


def _slug(value: str) -> str:
    return _term_key(value) or "unknown"
