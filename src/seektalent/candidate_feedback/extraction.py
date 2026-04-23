from __future__ import annotations

import re
from collections import Counter, defaultdict

from seektalent.candidate_feedback.models import CandidateFeedbackDecision, FeedbackCandidateTerm
from seektalent.models import QueryTermCandidate, ScoredCandidate

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


def _active_anchor(existing_terms: list[QueryTermCandidate]) -> QueryTermCandidate | None:
    for term in existing_terms:
        if term.active and term.queryability == "admitted" and term.retrieval_role == "role_anchor":
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


def _term_key(value: str) -> str:
    return "".join(char.casefold() for char in value if char.isalnum())


def _slug(value: str) -> str:
    return _term_key(value) or "unknown"
