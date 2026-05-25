from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class LiepinCardDecisionAction(StrEnum):
    RECOMMEND_DETAIL = "recommend_detail"
    REJECT_OBVIOUS_MISMATCH = "reject_obvious_mismatch"
    HOLD_INSUFFICIENT_CARD_SIGNAL = "hold_insufficient_card_signal"


@dataclass(frozen=True, kw_only=True)
class LiepinCardSummary:
    candidate_resume_id: str
    provider_rank: int
    display_title: str | None = None
    current_or_recent_company: str | None = None
    current_or_recent_title: str | None = None
    work_years: int | None = None
    age: int | None = None
    city: str | None = None
    expected_city: str | None = None
    education_level: str | None = None
    school_names: tuple[str, ...] = ()
    major_names: tuple[str, ...] = ()
    skill_tags: tuple[str, ...] = ()
    job_intention: str | None = None
    recent_experience_text: str | None = None
    normalized_card_text: str = ""
    masked_name: bool = False


@dataclass(frozen=True, kw_only=True)
class LiepinCardDecision:
    candidate_resume_id: str
    provider_rank: int
    card_policy_rank: int | None
    action: LiepinCardDecisionAction
    value_score: int | None
    hard_filter_status: str
    budget_reason_code: str
    reason_codes: tuple[str, ...]


def build_liepin_card_decisions(
    *,
    cards: list[LiepinCardSummary],
    query_terms: tuple[str, ...],
    job_title: str,
    max_detail_recommendations: int,
) -> list[LiepinCardDecision]:
    remaining = max(0, max_detail_recommendations)
    card_policy_rank = 0
    decisions: list[LiepinCardDecision] = []
    for card in sorted(cards, key=lambda item: item.provider_rank):
        hard_reject = _hard_reject_reason(card=card, query_terms=query_terms, job_title=job_title)
        if hard_reject is not None:
            decisions.append(
                LiepinCardDecision(
                    candidate_resume_id=card.candidate_resume_id,
                    provider_rank=card.provider_rank,
                    card_policy_rank=None,
                    action=LiepinCardDecisionAction.REJECT_OBVIOUS_MISMATCH,
                    value_score=0,
                    hard_filter_status=hard_reject,
                    budget_reason_code=hard_reject,
                    reason_codes=(hard_reject,),
                )
            )
            continue

        score, reasons = _card_signal_score(card=card, query_terms=query_terms, job_title=job_title)
        if score < 2:
            decisions.append(
                LiepinCardDecision(
                    candidate_resume_id=card.candidate_resume_id,
                    provider_rank=card.provider_rank,
                    card_policy_rank=None,
                    action=LiepinCardDecisionAction.HOLD_INSUFFICIENT_CARD_SIGNAL,
                    value_score=score * 20,
                    hard_filter_status="hard_filter_passed",
                    budget_reason_code="insufficient_card_signal",
                    reason_codes=("hard_filter_passed", "insufficient_card_signal"),
                )
            )
            continue

        if card_policy_rank >= remaining:
            decisions.append(
                LiepinCardDecision(
                    candidate_resume_id=card.candidate_resume_id,
                    provider_rank=card.provider_rank,
                    card_policy_rank=None,
                    action=LiepinCardDecisionAction.HOLD_INSUFFICIENT_CARD_SIGNAL,
                    value_score=min(100, 40 + score * 15),
                    hard_filter_status="hard_filter_passed",
                    budget_reason_code="blocked_budget_exhausted",
                    reason_codes=("hard_filter_passed", "blocked_budget_exhausted"),
                )
            )
            continue

        card_policy_rank += 1
        decisions.append(
            LiepinCardDecision(
                candidate_resume_id=card.candidate_resume_id,
                provider_rank=card.provider_rank,
                card_policy_rank=card_policy_rank,
                action=LiepinCardDecisionAction.RECOMMEND_DETAIL,
                value_score=min(100, 40 + score * 15),
                hard_filter_status="hard_filter_passed",
                budget_reason_code="within_run_detail_budget",
                reason_codes=(
                    "hard_filter_passed",
                    "provider_rank_preserved",
                    "card_rank_budget",
                    "within_run_detail_budget",
                    *reasons,
                ),
            )
        )
    return decisions


_ENGINEERING_TOKENS = {
    "backend",
    "data",
    "developer",
    "engineer",
    "engineering",
    "fastapi",
    "frontend",
    "golang",
    "java",
    "python",
    "ranking",
    "search",
    "software",
    "systems",
}
_NON_ENGINEERING_ROLE_TOKENS = {"retail", "sales", "store"}
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _hard_reject_reason(*, card: LiepinCardSummary, query_terms: tuple[str, ...], job_title: str) -> str | None:
    expected_tokens = _tokens(" ".join((job_title, *query_terms)))
    card_tokens = _card_tokens(card)
    title_tokens = _tokens(card.current_or_recent_title or card.display_title or "")
    if not expected_tokens or not card_tokens or not title_tokens:
        return None

    expected_engineering = bool(expected_tokens & _ENGINEERING_TOKENS)
    card_has_non_engineering_role = bool(title_tokens & _NON_ENGINEERING_ROLE_TOKENS)
    card_has_engineering_signal = bool(card_tokens & _ENGINEERING_TOKENS)
    if expected_engineering and card_has_non_engineering_role and not card_has_engineering_signal:
        return "obvious_role_mismatch"
    return None


def _card_signal_score(
    *,
    card: LiepinCardSummary,
    query_terms: tuple[str, ...],
    job_title: str,
) -> tuple[int, tuple[str, ...]]:
    score = 0
    reasons: list[str] = []
    card_tokens = _card_tokens(card)
    query_tokens = _query_tokens(query_terms)
    role_tokens = _tokens(job_title)
    card_text = _compact_card_text(card)

    matched_query_tokens = card_tokens & (query_tokens - role_tokens)
    matched_query_phrases = _matched_cjk_query_phrases(card_text=card_text, query_terms=query_terms)
    matched_query_count = len(matched_query_tokens) + len(matched_query_phrases)
    if matched_query_count:
        score += 2 if matched_query_count >= 2 else 1
        reasons.append("matched_card_terms")
    compact_job_title = _compact_cjk(job_title)
    if (role_tokens and card_tokens & role_tokens) or (compact_job_title and compact_job_title in card_text):
        score += 1
        reasons.append("matched_job_title")
    if card.skill_tags:
        score += 1
        reasons.append("high_value_card")
    return score, tuple(reason for reason in reasons if reason != "matched_job_title")


def _card_tokens(card: LiepinCardSummary) -> set[str]:
    return _tokens(
        " ".join(
            value
            for value in (
                card.display_title,
                card.current_or_recent_company,
                card.current_or_recent_title,
                card.city,
                card.expected_city,
                card.education_level,
                card.job_intention,
                card.recent_experience_text,
                card.normalized_card_text,
                *card.school_names,
                *card.major_names,
                *card.skill_tags,
            )
            if value
        )
    )


def _query_tokens(query_terms: tuple[str, ...]) -> set[str]:
    return {token for term in query_terms for token in _tokens(term)}


def _tokens(value: str) -> set[str]:
    return set(_WORD_PATTERN.findall(value.casefold()))


def _matched_cjk_query_phrases(*, card_text: str, query_terms: tuple[str, ...]) -> set[str]:
    matches: set[str] = set()
    for term in query_terms:
        compact = _compact_cjk(term)
        if compact and compact in card_text:
            matches.add(compact)
    return matches


def _compact_card_text(card: LiepinCardSummary) -> str:
    return _compact_cjk(
        " ".join(
            value
            for value in (
                card.display_title,
                card.current_or_recent_company,
                card.current_or_recent_title,
                card.city,
                card.expected_city,
                card.education_level,
                card.job_intention,
                card.recent_experience_text,
                card.normalized_card_text,
                *card.school_names,
                *card.major_names,
                *card.skill_tags,
            )
            if value
        )
    )


def _compact_cjk(value: str) -> str:
    if not _CJK_PATTERN.search(value):
        return ""
    return re.sub(r"\s+", "", value.casefold())
