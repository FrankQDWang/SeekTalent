from __future__ import annotations

import asyncio
from pathlib import Path

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
from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.orchestrator import RunStageError
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


def test_requirement_rescore_failure_keeps_scores_uncommitted_and_skips_controller(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WorkflowRuntime(
        make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts")
    )
    run_state = _run_state()
    runtime.resume_scorer = FailingRevisionScorer(run_state.scorecards_by_resume_id["resume-1"])
    calls: list[str] = []

    async def controller_must_not_run(**kwargs):
        del kwargs
        calls.append("controller")
        raise AssertionError("controller must not run after revision rescore failure")

    monkeypatch.setattr(
        "seektalent.runtime.round_decision_runtime.resolve_pre_controller_exhaustion",
        controller_must_not_run,
    )
    tracer = RunTracer(tmp_path / "trace-failure")
    try:
        with pytest.raises(RunStageError):
            asyncio.run(
                runtime._run_rounds(
                    run_state=run_state,
                    detail_open_claim_ledger=DetailOpenClaimLedger({}),
                    tracer=tracer,
                    source_plan=(),
                    runtime_round_boundary_callback=lambda round_no: _updated_sheet(
                        run_state.requirement_sheet,
                        round_no=round_no,
                    ),
                    runtime_round_boundary_commit_callback=lambda round_no: calls.append(
                        f"commit:{round_no}"
                    ),
                    runtime_checkpoint_callback=lambda artifacts: calls.append(
                        f"checkpoint:{artifacts.safe_boundary}"
                    ),
                )
            )
    finally:
        tracer.close()

    assert calls == []
    assert run_state.requirement_sheet.must_have_capabilities == ["Python"]
    assert run_state.scorecards_by_resume_id["resume-1"].overall_score == 85


def test_requirement_rescore_commits_and_checkpoints_before_controller(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = WorkflowRuntime(
        make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts")
    )
    run_state = _run_state()
    runtime.resume_scorer = SuccessfulRevisionScorer(
        run_state.scorecards_by_resume_id["resume-1"]
    )
    calls: list[str] = []

    async def stop_at_controller(**kwargs):
        del kwargs
        calls.append("controller")
        raise ControllerReached

    monkeypatch.setattr(
        "seektalent.runtime.round_decision_runtime.resolve_pre_controller_exhaustion",
        stop_at_controller,
    )
    tracer = RunTracer(tmp_path / "trace-success")
    try:
        with pytest.raises(ControllerReached):
            asyncio.run(
                runtime._run_rounds(
                    run_state=run_state,
                    detail_open_claim_ledger=DetailOpenClaimLedger({}),
                    tracer=tracer,
                    source_plan=(),
                    runtime_round_boundary_callback=lambda round_no: _updated_sheet(
                        run_state.requirement_sheet,
                        round_no=round_no,
                    ),
                    runtime_round_boundary_commit_callback=lambda round_no: calls.append(
                        f"commit:{round_no}"
                    ),
                    runtime_checkpoint_callback=lambda artifacts: calls.append(
                        f"checkpoint:{artifacts.safe_boundary}"
                    ),
                )
            )
    finally:
        tracer.close()

    assert calls == ["commit:1", "checkpoint:before_round_controller", "controller"]
    assert run_state.requirement_sheet.must_have_capabilities == ["Python", "Kafka"]
    assert run_state.scoring_policy.must_have_capabilities == ["Python", "Kafka"]
    assert run_state.scorecards_by_resume_id["resume-1"].overall_score == 40
    assert "Kafka" in [item.term for item in run_state.retrieval_state.query_term_pool]


class ControllerReached(RuntimeError):
    pass


class FailingRevisionScorer:
    def __init__(self, current: ScoredCandidate) -> None:
        self.current = current

    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        return [
            self.current.model_copy(
                update={
                    "resume_id": contexts[0].normalized_resume.resume_id,
                    "overall_score": 40,
                }
            )
        ], [
            ScoringFailure(
                resume_id="resume-1",
                branch_id="revision-rescore",
                round_no=1,
                attempts=1,
                error_message="timeout",
                failure_kind="timeout",
            )
        ]


class SuccessfulRevisionScorer:
    def __init__(self, current: ScoredCandidate) -> None:
        self.current = current

    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        return [
            self.current.model_copy(
                update={
                    "resume_id": contexts[0].normalized_resume.resume_id,
                    "overall_score": 40,
                    "must_have_match_score": 40,
                }
            )
        ], []


def _run_state() -> RunState:
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
    normalized = NormalizedResume(
        resume_id="resume-1",
        dedup_key="dedup-resume-1",
        current_title="Senior Python Engineer",
        skills=["Python"],
        completeness_score=90,
    )
    scored = ScoredCandidate(
        resume_id="resume-1",
        source_round=1,
        fit_bucket="fit",
        overall_score=85,
        must_have_match_score=85,
        reasoning_summary="Strong Python experience.",
        confidence="high",
    )
    return RunState(
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
        normalized_store={normalized.resume_id: normalized},
        scorecards_by_resume_id={scored.resume_id: scored},
    )


def _updated_sheet(sheet: RequirementSheet, *, round_no: int) -> RequirementSheet:
    return sheet.model_copy(
        update={
            "must_have_capabilities": ["Python", "Kafka"],
            "initial_query_term_pool": [
                *sheet.initial_query_term_pool,
                QueryTermCandidate(
                    term="Kafka",
                    source="notes",
                    category="tooling",
                    priority=90,
                    evidence="Runtime amendment.",
                    first_added_round=round_no,
                ),
            ],
        }
    )
