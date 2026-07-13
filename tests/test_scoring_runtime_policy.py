from seektalent.models import ScoredCandidate, ScoringFailure
from seektalent.runtime.scoring_runtime import scoring_failures_are_recoverable


def _scored_candidate() -> ScoredCandidate:
    return ScoredCandidate(
        resume_id="scored",
        source_round=1,
        fit_bucket="fit",
        overall_score=80,
        must_have_match_score=80,
        preferred_match_score=None,
        risk_score=None,
        reasoning_summary="Scored successfully.",
        confidence="high",
    )


def _failure(kind: str) -> ScoringFailure:
    return ScoringFailure(
        resume_id="failed",
        branch_id="branch-failed",
        round_no=1,
        attempts=1,
        error_message=kind,
        failure_kind=kind,
    )


def test_partial_applicability_failure_is_recoverable() -> None:
    assert scoring_failures_are_recoverable(
        [_scored_candidate()],
        [_failure("score_applicability_error")],
    ) is True


def test_whole_batch_applicability_failure_is_not_recoverable() -> None:
    assert scoring_failures_are_recoverable(
        [],
        [_failure("score_applicability_error")],
    ) is False


def test_non_applicability_failure_is_not_recoverable() -> None:
    assert scoring_failures_are_recoverable(
        [_scored_candidate()],
        [_failure("response_validation_error")],
    ) is False
