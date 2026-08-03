import asyncio

import pytest

from seektalent.models import (
    InputTruth,
    NormalizedResume,
    QueryTermCandidate,
    RequirementSheet,
    RetrievalState,
    RunState,
    ScoredCandidate,
    ScoringFailure,
)
from seektalent.requirements import build_scoring_policy
from seektalent.runtime.requirements_runtime import apply_approved_requirement_revision
from seektalent.runtime.scoring_runtime import rescore_requirement_revision_candidates, scoring_failures_are_recoverable


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


def test_partial_timeout_is_recoverable_without_creating_a_candidate() -> None:
    assert scoring_failures_are_recoverable(
        [_scored_candidate()],
        [_failure("timeout")],
    ) is True


def test_whole_batch_timeout_is_not_recoverable() -> None:
    assert scoring_failures_are_recoverable(
        [],
        [_failure("timeout")],
    ) is False


def test_non_applicability_failure_is_not_recoverable() -> None:
    assert scoring_failures_are_recoverable(
        [_scored_candidate()],
        [_failure("response_validation_error")],
    ) is False


def test_approved_requirement_revision_projects_sheet_policy_and_query_pool_together() -> None:
    run_state = _run_state()
    run_state.retrieval_state.query_term_pool.extend(
        [
            QueryTermCandidate(
                term="Legacy",
                source="notes",
                category="domain",
                priority=20,
                evidence="Old requirement.",
                first_added_round=1,
            ),
            QueryTermCandidate(
                term="Candidate feedback term",
                source="candidate_feedback",
                category="domain",
                priority=30,
                evidence="Runtime feedback.",
                first_added_round=2,
            ),
        ]
    )
    updated = run_state.requirement_sheet.model_copy(
        update={
            "must_have_capabilities": ["Python", "Kafka"],
            "initial_query_term_pool": [
                *run_state.requirement_sheet.initial_query_term_pool,
                QueryTermCandidate(
                    term="Kafka",
                    source="notes",
                    category="tooling",
                    priority=90,
                    evidence="Runtime amendment.",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="Redis",
                    source="notes",
                    category="tooling",
                    priority=91,
                    evidence="Score only.",
                    first_added_round=0,
                    active=True,
                    retrieval_role="score_only",
                    queryability="score_only",
                ),
            ],
        }
    )

    projection = apply_approved_requirement_revision(
        run_state=run_state,
        requirement_sheet=updated,
        effective_round_no=3,
    )

    assert projection.query_terms_changed is True
    assert projection.scoring_policy_changed is True
    assert run_state.requirement_sheet == updated
    assert run_state.scoring_policy.must_have_capabilities == ["Python", "Kafka"]
    assert [term.term for term in run_state.retrieval_state.query_term_pool] == [
        "Python",
        "Kafka",
        "Redis",
        "Candidate feedback term",
    ]
    kafka = next(term for term in run_state.retrieval_state.query_term_pool if term.term == "Kafka")
    redis = next(term for term in run_state.retrieval_state.query_term_pool if term.term == "Redis")
    assert kafka.first_added_round == 3
    assert kafka.active is True
    assert redis.first_added_round == 3
    assert redis.active is False


def test_requirement_revision_rescore_is_all_or_nothing() -> None:
    run_state = _run_state(with_score=True)
    original = dict(run_state.scorecards_by_resume_id)

    class FailingScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del tracer
            return [
                original[contexts[0].normalized_resume.resume_id].model_copy(update={"overall_score": 40})
            ], [_failure("timeout")]

    with pytest.raises(RuntimeError, match="revision rescore failed"):
        asyncio.run(
            rescore_requirement_revision_candidates(
                round_no=2,
                run_state=run_state,
                tracer=object(),
                runtime_only_constraints=[],
                resume_scorer=FailingScorer(),
                format_scoring_failure_message=lambda failures: "revision rescore failed",
                run_stage_error=lambda stage, message: RuntimeError(message),
            )
        )

    assert run_state.scorecards_by_resume_id == original


def test_requirement_revision_rescore_wraps_direct_timeout_without_mutation() -> None:
    run_state = _run_state(with_score=True)
    original = dict(run_state.scorecards_by_resume_id)

    class TimeoutScorer:
        async def score_candidates_parallel(self, *, contexts, tracer):
            del contexts, tracer
            raise TimeoutError("revision scoring timed out")

    with pytest.raises(RuntimeError, match="scoring:revision scoring timed out"):
        asyncio.run(
            rescore_requirement_revision_candidates(
                round_no=2,
                run_state=run_state,
                tracer=object(),
                runtime_only_constraints=[],
                resume_scorer=TimeoutScorer(),
                format_scoring_failure_message=lambda failures: "unexpected structured failure",
                run_stage_error=lambda stage, message: RuntimeError(f"{stage}:{message}"),
            )
        )

    assert run_state.scorecards_by_resume_id == original


def _run_state(*, with_score: bool = False) -> RunState:
    sheet = RequirementSheet(
        job_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Title is explicit.",
        role_summary="Build distributed systems.",
        must_have_capabilities=["Python"],
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title.",
                first_added_round=0,
            )
        ],
        scoring_rationale="Score Python experience.",
    )
    state = RunState(
        input_truth=InputTruth(
            job_title=sheet.job_title,
            jd="JD",
            notes="",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=sheet,
        scoring_policy=build_scoring_policy(sheet),
        retrieval_state=RetrievalState(
            current_plan_version=0,
            query_term_pool=list(sheet.initial_query_term_pool),
        ),
    )
    if with_score:
        state.normalized_store["scored"] = NormalizedResume(
            resume_id="scored",
            dedup_key="dedup-scored",
            current_title="Python Engineer",
            completeness_score=80,
            missing_fields=[],
        )
        state.scorecards_by_resume_id["scored"] = _scored_candidate()
    return state
