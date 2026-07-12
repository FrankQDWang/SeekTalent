from seektalent.candidate_quality import (
    WORKBENCH_MIN_CANDIDATE_SCORE,
    is_recommendation_eligible,
    is_workbench_visible_score,
    risk_at_or_above,
    risk_at_or_below,
)


def test_nullable_risk_semantics_are_shared() -> None:
    assert risk_at_or_above(None, 60) is False
    assert risk_at_or_above(60, 60) is True
    assert risk_at_or_below(None, 30) is True
    assert risk_at_or_below(31, 30) is False


def test_workbench_visibility_has_one_inclusive_threshold() -> None:
    assert WORKBENCH_MIN_CANDIDATE_SCORE == 60
    assert is_workbench_visible_score(None) is False
    assert is_workbench_visible_score(59) is False
    assert is_workbench_visible_score(60) is True


def test_recommendation_eligibility_requires_fit_and_score_floor() -> None:
    assert is_recommendation_eligible(score=90, fit_bucket="not_fit") is False
    assert is_recommendation_eligible(score=59, fit_bucket="fit") is False
    assert is_recommendation_eligible(score=60, fit_bucket="fit") is True
