from typing import TypeGuard

from seektalent.models import (
    RECOMMENDATION_MIN_SCORE,
    is_recommendation_eligible as _is_recommendation_eligible,
)


def is_valid_candidate_score(score: int | None) -> TypeGuard[int]:
    return score is not None and 0 <= score <= 100


def is_recommendation_score(score: int | None) -> TypeGuard[int]:
    return is_valid_candidate_score(score) and score >= RECOMMENDATION_MIN_SCORE


def is_recommendation_eligible(*, score: int | None, fit_bucket: str | None) -> bool:
    return _is_recommendation_eligible(score=score, fit_bucket=fit_bucket)


def is_workbench_candidate_visible(*, score: int | None, fit_bucket: str | None) -> bool:
    return fit_bucket == "fit" and is_valid_candidate_score(score)


def risk_at_or_above(score: int | None, threshold: int) -> bool:
    return score is not None and score >= threshold


def risk_at_or_below(score: int | None, threshold: int) -> bool:
    return score is None or score <= threshold
