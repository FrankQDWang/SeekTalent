from typing import TypeGuard


WORKBENCH_MIN_CANDIDATE_SCORE = 60


def is_workbench_visible_score(score: int | None) -> TypeGuard[int]:
    return score is not None and score >= WORKBENCH_MIN_CANDIDATE_SCORE


def is_recommendation_eligible(*, score: int | None, fit_bucket: str | None) -> bool:
    return fit_bucket == "fit" and is_workbench_visible_score(score)


def risk_at_or_above(score: int | None, threshold: int) -> bool:
    return score is not None and score >= threshold


def risk_at_or_below(score: int | None, threshold: int) -> bool:
    return score is None or score <= threshold
