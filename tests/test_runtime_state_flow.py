import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import seektalent.candidate_feedback.model_steps as candidate_feedback_model_steps
from seektalent.candidate_feedback.llm_prf import LLMPRFCandidate, LLMPRFExtraction, LLMPRFSourceEvidenceRef
from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation, SearchResult
from seektalent.models import (
    CTSQuery,
    FinalCandidate,
    FinalResult,
    HardConstraintSlots,
    InputTruth,
    LocationExecutionPlan,
    PoolDecision,
    ProposedFilterPlan,
    QueryExecutionReceipt,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RequirementExtractionDraft,
    RequirementSheet,
    ResumeCandidate,
    RetrievalState,
    RoundRetrievalPlan,
    RoundState,
    QueryOutcomeThresholds,
    RuntimeConstraint,
    RuntimeCanonicalIntakeSummary,
    RuntimeCanonicalResumeSelection,
    RuntimeIdentityConflict,
    ScoredCandidate,
    ScoringPolicy,
    ScoringFailure,
    SearchObservation,
    SearchControllerDecision,
    SentQueryRecord,
    StopControllerDecision,
    RunState,
)
from seektalent.retrieval import build_location_execution_plan, build_round_retrieval_plan
from seektalent.retrieval.query_identity import ResolvedQueryIdentity
import seektalent.runtime.orchestrator as orchestrator_module
import seektalent.runtime.controller_runtime as controller_runtime_module
import seektalent.runtime.rescue_execution_runtime as rescue_execution_runtime
from seektalent.runtime.candidate_intake import (
    build_canonical_scoring_intake,
    normalize_runtime_candidates,
    select_identity_top_candidates,
)
from seektalent.runtime.controller_context import build_controller_context
from seektalent.runtime.finalize_context import build_finalize_context
from seektalent.runtime.first_page_expansion import (
    ExpansionQueryMergeCounts,
    apply_first_page_expansion_to_receipts,
    canonical_scorecards_by_identity_id,
    decide_first_page_expansion,
    execute_first_page_decisions,
    select_qualified_first_page_expansions,
)
from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult, RetrievalRuntime
from seektalent.runtime.retrieval_runtime import LogicalQueryState, allocate_initial_lane_targets
from seektalent.runtime.reflection_context import build_reflection_context
from seektalent.runtime.runtime_reports import render_round_review as render_round_review_direct
from seektalent.runtime.source_round_dispatch import SourceRoundAdapterResult, SourceRoundDispatchResult
from seektalent.runtime.source_expansion import (
    SourceFirstPageExpansionError,
    SourceFirstPageExpansionRequest,
    SourceFirstPageExpansionResult,
)
from seektalent.runtime.source_round_dispatch import RuntimeSourceInvariantError
from seektalent.runtime.source_lanes import (
    RuntimeQueryCandidateAttribution,
    SourceQueryExecutionOutcome,
    build_runtime_source_plan,
    rebuild_candidate_identities,
)
from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.orchestrator import RuntimeSourceRoundContext
from seektalent.source_adapters import build_source_enabled_runtime
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent.tracing import RunTracer, json_sha256
from tests.settings_factory import make_settings


def _workflow_runtime(*args: Any, **kwargs: Any) -> WorkflowRuntime:
    return build_source_enabled_runtime(*args, **kwargs)


def test_first_page_expansion_requires_every_baseline_candidate_to_be_high_quality() -> None:
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="c1",
        opaque_ref="artifact://c1",
        source_kind="liepin",
        round_no=2,
        query_instance_id="q1",
        visible_candidate_count=3,
        eligible_candidate_count=3,
        initial_opened_count=2,
    )
    scores = [
        ScoredCandidate(
            resume_id=f"r{i}", fit_bucket="fit", overall_score=overall,
            must_have_match_score=80, preferred_match_score=None, risk_score=None,
            risk_flags=[], reasoning_summary="fixture", evidence=[], confidence="high",
            matched_must_haves=[], missing_must_haves=[], matched_preferences=[],
            negative_signals=[], strengths=[], weaknesses=[], source_round=2,
        )
        for i, overall in enumerate((90, 79))
    ]
    decision = decide_first_page_expansion(
        continuations=[continuation], requested_count=2, baseline_opened_count=2,
        baseline_identity_count=2, scorecards=scores,
    )
    assert decision.reason_code == "baseline_quality_below_threshold"


def _task7_score(resume_id: str, overall: int = 80, must: int = 70,
                 risk: int | None = 30, fit_bucket: str = "fit") -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id, fit_bucket=fit_bucket, overall_score=overall,
        must_have_match_score=must, preferred_match_score=None, risk_score=risk,
        risk_flags=[], reasoning_summary="fixture", evidence=[], confidence="high",
        matched_must_haves=[], missing_must_haves=[], matched_preferences=[],
        negative_signals=[], strengths=[], weaknesses=[], source_round=2,
    )


def _task7_continuation(query: str = "q1", continuation: str = "c1",
                        initial: int = 1) -> ProviderSearchContinuation:
    return ProviderSearchContinuation(
        kind="first_page_detail_expansion", continuation_id=continuation,
        opaque_ref=f"artifact://{continuation}", source_kind="liepin", round_no=2,
        query_instance_id=query, visible_candidate_count=3,
        eligible_candidate_count=3, initial_opened_count=initial,
    )


def _task7_receipt(query: str = "q1", requested: int = 1) -> QueryExecutionReceipt:
    return QueryExecutionReceipt(
        round_no=2, source_kind="liepin", query_instance_id=query,
        query_fingerprint=f"fp-{query}", term_group_key="group",
        primary_anchor_family_id="anchor", non_anchor_term_family_ids=[],
        query_role="exploit", lane_type="exploit", keyword_query="python",
        requested_count=requested, source_plan_version="1", status="completed",
        dispatch_started=True,
    )


@pytest.mark.parametrize(
    ("score", "expand", "reason"),
    [
        (_task7_score("r", overall=80, must=70, risk=30), True, "baseline_quality_gate_passed"),
        (_task7_score("r", overall=79), False, "baseline_quality_below_threshold"),
        (_task7_score("r", must=69), False, "baseline_quality_below_threshold"),
        (_task7_score("r", risk=31), False, "baseline_risk_above_threshold"),
        (_task7_score("r", risk=None), True, "baseline_quality_gate_passed"),
        (_task7_score("r", fit_bucket="not_fit"), False, "baseline_not_fit"),
    ],
)
def test_first_page_expansion_exact_quality_boundaries(score, expand, reason) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[score],
    )
    assert (decision.expand, decision.reason_code) == (expand, reason)


def test_first_page_expansion_rejects_target_and_scoring_shortfalls() -> None:
    target = decide_first_page_expansion(
        continuations=[_task7_continuation(initial=0)], requested_count=1,
        baseline_opened_count=0, baseline_identity_count=0, scorecards=[],
    )
    scoring = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[],
    )
    assert target.reason_code == "baseline_target_not_met"
    assert scoring.reason_code == "baseline_scoring_incomplete"


def test_first_page_selector_resolves_alias_and_groups_physical_targets() -> None:
    continuations = [_task7_continuation(), _task7_continuation(continuation="c2", initial=0)]
    decisions = select_qualified_first_page_expansions(
        continuations=continuations, receipts=[_task7_receipt()],
        candidate_attributions=[RuntimeQueryCandidateAttribution(
            source_kind="liepin", query_instance_id="q1", resume_id="alias", dedup_key="person")],
        candidate_identity_by_resume_id={"alias": "identity"},
        scorecards_by_identity_id={"identity": _task7_score("canonical")},
    )
    assert len(decisions) == 1
    assert decisions[0].expand is True
    assert [item.continuation_id for item in decisions[0].continuations] == ["c1", "c2"]


def test_canonical_scorecards_prefer_selected_canonical_resume() -> None:
    selection = RuntimeCanonicalResumeSelection(
        identity_id="identity", canonical_resume_id="r2",
    )
    result = canonical_scorecards_by_identity_id(
        scorecards_by_resume_id={"r1": _task7_score("r1", 90), "r2": _task7_score("r2", 80)},
        candidate_identity_by_resume_id={"r1": "identity", "r2": "identity"},
        canonical_resume_by_identity_id={"identity": selection},
    )
    assert result["identity"].resume_id == "r2"


def test_first_page_executor_orders_actions_and_isolates_typed_failure() -> None:
    qualified = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")],
    )
    rejected = replace(qualified, query_instance_id="q2", expand=False,
                       continuations=(_task7_continuation("q2", "c2"),))
    calls = []
    async def expand(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        calls.append((request.query_instance_id, request.action))
        if request.query_instance_id == "q1":
            raise SourceFirstPageExpansionError(
                "failed", status="failed", safe_reason_code="provider_failed", continuation_deleted=True)
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind, query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id, status="completed",
            first_page_visible_count=3, first_page_eligible_count=3,
            initial_opened_count=1, continuation_deleted=True,
        )
    outcomes = asyncio.run(execute_first_page_decisions(
        runtime_run_id="run", round_no=2, decisions=[qualified, rejected], expanders={"liepin": expand}))
    assert calls == [("q1", "expand"), ("q2", "discard")]
    assert [item.status for item in outcomes] == ["failed", "completed"]


def test_first_page_executor_rejects_malformed_provider_result_and_missing_cleanup() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    async def malformed(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind, query_instance_id="foreign",
            continuation_id=request.continuation_id, status="completed",
            first_page_visible_count=3, first_page_eligible_count=3, initial_opened_count=1)
    with pytest.raises(RuntimeSourceInvariantError, match="wrong_provenance"):
        asyncio.run(execute_first_page_decisions(
            runtime_run_id="run", round_no=2, decisions=[decision], expanders={"liepin": malformed}))


def test_first_page_executor_rejects_missing_expander() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    with pytest.raises(RuntimeSourceInvariantError, match="expander_unavailable"):
        asyncio.run(execute_first_page_decisions(
            runtime_run_id="run", round_no=2, decisions=[decision], expanders={}))


def test_first_page_receipt_preserves_baseline_on_failed_discard() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1,
        scorecards=[_task7_score("r", overall=79)])
    receipt = _task7_receipt().model_copy(update={"raw_candidate_count": 1, "unique_candidate_count": 1})
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin", query_instance_id="q1", continuation_id="c1", status="failed",
        first_page_visible_count=3, first_page_eligible_count=3, initial_opened_count=1,
        safe_reason_code="discard_failed", continuation_deleted=False)
    updated = apply_first_page_expansion_to_receipts(
        receipts=[receipt], decisions=[decision], outcomes=[outcome], merge_counts=[],
        scoring_failure_counts={})[0]
    assert (updated.raw_candidate_count, updated.unique_candidate_count) == (1, 1)
    assert updated.first_page_expansion_status == "failed"
    assert updated.first_page_expansion_reason_code == "first_page_continuation_discard_failed"


def test_first_page_receipt_rejects_unreconciled_merge_counts() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin", query_instance_id="q1", continuation_id="c1", status="completed",
        first_page_visible_count=3, first_page_eligible_count=3, initial_opened_count=1,
        expansion_opened_count=1, continuation_deleted=True)
    with pytest.raises(RuntimeSourceInvariantError, match="merge_count_exceeds_opened"):
        apply_first_page_expansion_to_receipts(
            receipts=[_task7_receipt()], decisions=[decision], outcomes=[outcome],
            merge_counts=[ExpansionQueryMergeCounts("liepin", "q1", 2, 0)],
            scoring_failure_counts={})


def test_first_page_receipt_rejects_missing_duplicate_and_foreign_outcomes() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin", query_instance_id="q1", continuation_id="c1", status="completed",
        first_page_visible_count=3, first_page_eligible_count=3, initial_opened_count=1,
        continuation_deleted=True)
    for outcomes in ([], [outcome, outcome], [replace(outcome, query_instance_id="foreign")]):
        with pytest.raises(RuntimeSourceInvariantError):
            apply_first_page_expansion_to_receipts(
                receipts=[_task7_receipt()], decisions=[decision], outcomes=outcomes,
                merge_counts=[], scoring_failure_counts={})


@pytest.mark.parametrize("case", ["status", "missing", "foreign", "duplicate"])
def test_first_page_executor_rejects_each_malformed_attribution_case(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    candidate = _make_candidate("r-new", source_round=2)
    good = RuntimeQueryCandidateAttribution(
        source_kind="liepin", query_instance_id="q1", resume_id="r-new", dedup_key="r-new")
    async def malformed(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        attributions = (good,)
        status = "completed"
        if case == "status":
            status = "unknown"
        elif case == "missing":
            attributions = ()
        elif case == "foreign":
            attributions = (replace(good, query_instance_id="foreign"),)
        elif case == "duplicate":
            attributions = (good, good)
        return SourceFirstPageExpansionResult(
            source_kind="liepin", query_instance_id="q1", continuation_id="c1",
            status=cast(Any, status), candidates=(candidate,),
            candidate_query_attributions=attributions, first_page_visible_count=3,
            first_page_eligible_count=3, initial_opened_count=1,
            expansion_opened_count=1, continuation_deleted=True)
    with pytest.raises(RuntimeSourceInvariantError):
        asyncio.run(execute_first_page_decisions(
            runtime_run_id="run", round_no=2, decisions=[decision], expanders={"liepin": malformed}))


@pytest.mark.parametrize("case", ["cleanup", "negative_provider", "excessive_provider"])
def test_first_page_executor_rejects_each_cleanup_and_provider_counter_invariant(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    async def malformed(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        return SourceFirstPageExpansionResult(
            source_kind="liepin", query_instance_id="q1", continuation_id="c1", status="completed",
            first_page_visible_count=3, first_page_eligible_count=3, initial_opened_count=1,
            expansion_opened_count=-1 if case == "negative_provider" else 3 if case == "excessive_provider" else 0,
            continuation_deleted=case != "cleanup")
    with pytest.raises(RuntimeSourceInvariantError):
        asyncio.run(execute_first_page_decisions(
            runtime_run_id="run", round_no=2, decisions=[decision], expanders={"liepin": malformed}))


@pytest.mark.parametrize("case", ["negative_merge", "negative_scoring", "excessive_scoring"])
def test_first_page_receipt_rejects_each_counter_invariant(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin", query_instance_id="q1", continuation_id="c1", status="completed",
        first_page_visible_count=3, first_page_eligible_count=3, initial_opened_count=1,
        expansion_opened_count=1, continuation_deleted=True)
    merge = ExpansionQueryMergeCounts("liepin", "q1", -1 if case == "negative_merge" else 1, 0)
    scoring = -1 if case == "negative_scoring" else 2 if case == "excessive_scoring" else 0
    with pytest.raises(RuntimeSourceInvariantError):
        apply_first_page_expansion_to_receipts(
            receipts=[_task7_receipt()], decisions=[decision], outcomes=[outcome],
            merge_counts=[merge], scoring_failure_counts={("liepin", "q1"): scoring})


@pytest.mark.parametrize("case", ["foreign_decision", "foreign_merge", "foreign_scoring", "duplicate_receipt"])
def test_first_page_receipt_rejects_each_foreign_or_duplicate_key(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()], requested_count=1,
        baseline_opened_count=1, baseline_identity_count=1, scorecards=[_task7_score("r")])
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin", query_instance_id="q1", continuation_id="c1", status="completed",
        first_page_visible_count=3, first_page_eligible_count=3, initial_opened_count=1,
        continuation_deleted=True)
    receipts = [_task7_receipt()]
    decisions = [decision]
    merges = []
    scoring = {}
    if case == "foreign_decision":
        decisions = [replace(decision, query_instance_id="foreign")]
    elif case == "foreign_merge":
        merges = [ExpansionQueryMergeCounts("liepin", "foreign", 0, 0)]
    elif case == "foreign_scoring":
        scoring = {("liepin", "foreign"): 0}
    else:
        receipts.append(_task7_receipt())
    with pytest.raises(RuntimeSourceInvariantError):
        apply_first_page_expansion_to_receipts(
            receipts=receipts, decisions=decisions, outcomes=[outcome],
            merge_counts=merges, scoring_failure_counts=scoring)


def test_two_target_qualified_receipt_reconciles_exact_counts_once() -> None:
    continuations = [_task7_continuation(), _task7_continuation(continuation="c2", initial=0)]
    decision = decide_first_page_expansion(
        continuations=continuations, requested_count=1, baseline_opened_count=1,
        baseline_identity_count=1, scorecards=[_task7_score("r")])
    outcomes = [
        SourceFirstPageExpansionResult(
            source_kind="liepin", query_instance_id="q1", continuation_id=item.continuation_id,
            status="completed", first_page_visible_count=3, first_page_eligible_count=3,
            initial_opened_count=item.initial_opened_count, expansion_opened_count=1,
            continuation_deleted=True)
        for item in continuations
    ]
    updated = apply_first_page_expansion_to_receipts(
        receipts=[_task7_receipt()], decisions=[decision], outcomes=outcomes,
        merge_counts=[ExpansionQueryMergeCounts("liepin", "q1", 1, 1)],
        scoring_failure_counts={("liepin", "q1"): 1})
    assert len(updated) == 1
    assert (updated[0].initial_opened_count, updated[0].expansion_opened_count) == (1, 2)
    assert (updated[0].unique_candidate_count, updated[0].duplicate_candidate_count) == (1, 1)
    assert updated[0].expansion_scoring_failure_count == 1


def _liepin_fixture_settings(**overrides: object):
    return make_settings(
        liepin_worker_mode="fake_fixture",
        liepin_allow_fake_fixture_worker=True,
        **overrides,
    )


def _cts_source_plan(runtime: WorkflowRuntime, tracer: RunTracer):
    return build_runtime_source_plan(
        source_kinds=["cts"],
        settings=runtime.settings,
        runtime_run_id=tracer.run_id,
        source_context=None,
    )


def _detail_open_claim_ledger(run_state: RunState) -> DetailOpenClaimLedger:
    return DetailOpenClaimLedger(run_state.detail_open_claims_by_provider_key)


def _mark_query_terms_dispatched(run_state: RunState, *, query_terms: list[str], query_id: str) -> None:
    from seektalent.retrieval.query_identity import resolve_query_identity

    identity = resolve_query_identity(
        query_terms=query_terms,
        query_term_pool=run_state.retrieval_state.query_term_pool,
    )
    run_state.retrieval_state.query_execution_ledger.append(
        QueryExecutionReceipt(
            round_no=0,
            source_kind="cts",
            query_instance_id=query_id,
            query_fingerprint=f"fp-{query_id}",
            term_group_key=identity.term_group_key,
            primary_anchor_family_id=identity.primary_anchor_family_id,
            non_anchor_term_family_ids=list(identity.non_anchor_term_family_ids),
            query_role="exploit",
            lane_type="exploit",
            query_terms=query_terms,
            keyword_query=" ".join(query_terms),
            requested_count=1,
            source_plan_version="test",
            status="completed",
            dispatch_started=True,
        )
    )


def _round_artifact(run_dir: Path, round_no: int, subsystem: str, name: str, *, extension: str = "json") -> Path:
    return run_dir / "rounds" / f"{round_no:02d}" / subsystem / f"{name}.{extension}"


def _runtime_artifact(run_dir: Path, name: str, *, extension: str = "json") -> Path:
    return run_dir / "runtime" / f"{name}.{extension}"


def _sample_inputs() -> tuple[str, str, str]:
    return (
        "Senior Python Engineer",
        "Senior Python Engineer responsible for resume matching workflows.",
        "Prefer retrieval experience and shipping production AI features.",
    )


def _make_candidate(
    resume_id: str,
    *,
    source_round: int = 1,
    project_names: list[str] | None = None,
    work_summaries: list[str] | None = None,
    search_text: str = "python retrieval trace resume search",
    raw: dict[str, object] | None = None,
) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        dedup_key=resume_id,
        source_round=source_round,
        now_location="上海",
        expected_location="上海",
        expected_job_category="Python Engineer",
        work_year=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=["Example Co | Python Engineer | Built retrieval workflows."],
        project_names=project_names or ["Resume search"],
        work_summaries=work_summaries or ["python", "retrieval", "trace"],
        search_text=search_text,
        raw=raw or {"resume_id": resume_id, "candidate_name": resume_id},
    )


class SequenceController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return SearchControllerDecision(
            thought_summary="Round 2 widens the domain surface.",
            action="search_cts",
            decision_rationale="Add one reflection term while keeping the same filter shape.",
            proposed_query_terms=["python", "resume matching", "trace"],
            proposed_filter_plan=ProposedFilterPlan(),
            response_to_reflection="Accepted the added trace term and left location execution to runtime.",
        )


class StubRequirementExtractor:
    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            jd_query_terms=["resume matching", "trace"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            job_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=[
                QueryTermCandidate(
                    term="python",
                    source="job_title",
                    category="role_anchor",
                    priority=1,
                    evidence="Job title",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="resume matching",
                    source="jd",
                    category="domain",
                    priority=2,
                    evidence="JD body",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                ),
            ],
            scoring_rationale="Score Python fit first.",
        )


class SingleFamilyRequirementExtractor:
    def __init__(self, *, include_reserve: bool) -> None:
        self.include_reserve = include_reserve

    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        pool = [
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="resume matching",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ]
        if self.include_reserve:
            pool.append(
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                    active=False,
                )
            )
        draft = RequirementExtractionDraft(
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            jd_query_terms=["resume matching"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            job_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Title maps directly to the Python role anchor.",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=pool,
            scoring_rationale="Score Python fit first.",
        )


def test_build_run_state_uses_approved_requirement_sheet_without_extraction(tmp_path: Path) -> None:
    from seektalent.runtime.requirements_runtime import build_run_state

    class FailingExtractor:
        async def extract_with_draft(self, **_: object) -> object:
            raise AssertionError("requirements extractor must not run when approved sheet is provided")

    emitted_events: list[tuple[str, str]] = []

    def emit_llm_event(**kwargs: object) -> None:
        emitted_events.append((str(kwargs["event_type"]), str(kwargs["status"])))

    def emit_progress(*_: object, **__: object) -> None:
        return None

    def snapshot_factory(**kwargs: object):
        class Snapshot:
            def model_dump(self, *, mode: str) -> dict[str, object]:
                assert mode == "json"
                return dict(kwargs)

        return Snapshot()

    sheet = RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=["AI Agent"],
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agent workflow systems.",
        must_have_capabilities=["LangGraph"],
        preferred_capabilities=["RAG"],
        exclusion_signals=[],
        hard_constraints={},
        preferences={},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize agent workflow evidence.",
    )
    settings = make_settings(workspace_root=str(tmp_path))
    tracer = RunTracer(settings.artifacts_path)

    try:
        run_state = asyncio.run(
            build_run_state(
                settings=settings,
                requirement_extractor=FailingExtractor(),
                tracer=tracer,
                job_title="AI Agent Engineer",
                jd="Build LangGraph systems.",
                notes="Original notes only.",
                requirement_cache_scope=None,
                approved_requirement_sheet=sheet,
                progress_callback=None,
                emit_llm_event=emit_llm_event,
                emit_progress=emit_progress,
                build_llm_call_snapshot=snapshot_factory,
                write_aux_llm_call_artifact=lambda **_: None,
                run_stage_error_factory=lambda stage, message: RuntimeError(f"{stage}:{message}"),
            )
        )
    finally:
        tracer.close(status="completed")

    assert run_state.requirement_sheet == sheet
    assert run_state.input_truth.notes == "Original notes only."
    assert ("requirements_completed", "succeeded") in emitted_events


class SequenceReflection:
    def __init__(self) -> None:
        self.calls = 0

    async def reflect(self, *, context) -> ReflectionAdvice:
        self.calls += 1
        if self.calls == 1:
            return ReflectionAdvice(
                keyword_advice=ReflectionKeywordAdvice(suggested_keep_terms=["trace"]),
                filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
                suggest_stop=False,
                reflection_summary="Continue with one extra tracing term.",
            )
        return ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
            suggest_stop=True,
            suggested_stop_reason="reflection_stop",
            reflection_summary="Stop after round 2.",
        )


class MutationAttemptReflection:
    async def reflect(self, *, context) -> ReflectionAdvice:
        del context
        return ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(
                suggested_activate_terms=["trace"],
                suggested_drop_terms=["resume matching"],
                suggested_deprioritize_terms=["resume matching"],
            ),
            filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
            suggest_stop=False,
            reflection_summary="Attempt to mutate the query term pool.",
        )


class StubScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []
        for context in contexts:
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=context.normalized_resume.resume_id,
                branch_id=f"r{context.round_no}-{context.normalized_resume.resume_id}",
                model="stub-scorer",
                summary="stub score",
                payload={},
            )
            scored.append(
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=90 if context.round_no == 1 else 91,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="Stub scorer accepted the candidate.",
                    evidence=["python", "retrieval"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["resume matching"],
                    negative_signals=[],
                    strengths=["Strong backend match."],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
            )
        return scored, failures


class PRFProbeScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []
        for context in contexts:
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=context.normalized_resume.resume_id,
                branch_id=f"r{context.round_no}-{context.normalized_resume.resume_id}",
                model="stub-scorer",
                summary="stub score",
                payload={},
            )
            scored.append(
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=92 if context.round_no == 1 else 90,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="PRF seed candidate.",
                    evidence=["LangGraph"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=[],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
            )
        return scored, failures


class GenericFallbackScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []
        for context in contexts:
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=context.normalized_resume.resume_id,
                branch_id=f"r{context.round_no}-{context.normalized_resume.resume_id}",
                model="stub-scorer",
                summary="stub score",
                payload={},
            )
            scored.append(
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=90 if context.round_no == 1 else 91,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="Fallback scorer accepted the candidate.",
                    evidence=["trace"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=[],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
            )
        return scored, failures


class SingleSeedScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        scored: list[ScoredCandidate] = []
        failures: list[ScoringFailure] = []
        for context in contexts:
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=context.normalized_resume.resume_id,
                branch_id=f"r{context.round_no}-{context.normalized_resume.resume_id}",
                model="stub-scorer",
                summary="stub score",
                payload={},
            )
            scored.append(
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=92 if context.normalized_resume.resume_id == "seed-1" else 55,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="Single usable PRF seed.",
                    evidence=["LangGraph"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=[],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
            )
        return scored, failures


class LowQualityScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        return (
            [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=65,
                    must_have_match_score=60,
                    preferred_match_score=50,
                    risk_score=40,
                    risk_flags=[],
                    reasoning_summary="Usable but not a strong fit.",
                    evidence=["python"],
                    confidence="medium",
                    matched_must_haves=["python"],
                    missing_must_haves=["resume matching"],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=["Some Python signal."],
                    weaknesses=["Weak retrieval-specific evidence."],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
                for context in contexts
            ],
            [],
        )


class ScorerSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        self.calls += 1
        scored: list[ScoredCandidate] = []
        for context in contexts:
            scored.append(
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=90,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="Spy scorer accepted the candidate.",
                    evidence=["LangGraph"],
                    confidence="high",
                    matched_must_haves=["LangGraph"],
                    missing_must_haves=[],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=[],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
            )
        return scored, []


class StubFinalizer:
    last_validator_retry_count = 0
    last_validator_retry_reasons: list[str] = []

    async def finalize(self, *, run_id, run_dir, rounds_executed, stop_reason, ranked_candidates) -> FinalResult:
        return FinalResult(
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            summary=f"Returned {len(ranked_candidates)} candidates after {rounds_executed} rounds.",
            candidates=[
                FinalCandidate(
                    resume_id=item.resume_id,
                    rank=index,
                    final_score=item.overall_score,
                    fit_bucket=item.fit_bucket,
                    match_summary="stub match summary",
                    strengths=item.strengths,
                    weaknesses=item.weaknesses,
                    matched_must_haves=item.matched_must_haves,
                    matched_preferences=item.matched_preferences,
                    risk_flags=item.risk_flags,
                    why_selected=item.reasoning_summary,
                    source_round=item.source_round,
                )
                for index, item in enumerate(ranked_candidates, start=1)
            ],
        )


class DuplicateAcrossLanesCTS:
    async def search(
        self,
        *,
        query_terms,
        query_role,
        keyword_query,
        adapter_notes,
        provider_filters,
        runtime_constraints,
        page_size,
        round_no,
        trace_id,
        fetch_mode="summary",
        cursor=None,
    ) -> SearchResult:
        del query_terms, query_role, keyword_query, adapter_notes, provider_filters, runtime_constraints, page_size, trace_id, fetch_mode
        if round_no == 1:
            return SearchResult(
                candidates=[],
                diagnostics=["round 1 returned no candidates"],
                request_payload={"round_no": round_no},
                raw_candidate_count=0,
                latency_ms=1,
            )
        if int(cursor or "1") > 1:
            return SearchResult(
                candidates=[],
                diagnostics=[f"round {round_no} page exhausted"],
                request_payload={"round_no": round_no, "cursor": cursor},
                raw_candidate_count=0,
                latency_ms=1,
            )
        candidate = _make_candidate("resume-1", source_round=round_no)
        return SearchResult(
            candidates=[candidate],
            diagnostics=[f"round {round_no} returned one candidate"],
            request_payload={"round_no": round_no, "cursor": cursor},
            raw_candidate_count=1,
            latency_ms=1,
        )


class PRFProbeCTS:
    async def search(
        self,
        *,
        query_terms,
        query_role,
        keyword_query,
        adapter_notes,
        provider_filters,
        runtime_constraints,
        page_size,
        round_no,
        trace_id,
        fetch_mode="summary",
        cursor=None,
    ) -> SearchResult:
        del query_terms, keyword_query, adapter_notes, provider_filters, runtime_constraints, page_size, trace_id, fetch_mode
        if int(cursor or "1") > 1:
            return SearchResult(
                candidates=[],
                diagnostics=[f"round {round_no} page exhausted"],
                request_payload={"round_no": round_no, "cursor": cursor},
                raw_candidate_count=0,
                latency_ms=1,
            )
        if round_no == 1:
            candidates = [
                _make_candidate(
                    "seed-1",
                    source_round=1,
                    project_names=["LangGraph"],
                    work_summaries=["LangGraph"],
                    search_text="LangGraph",
                ),
                _make_candidate(
                    "seed-2",
                    source_round=1,
                    project_names=["LangGraph"],
                    work_summaries=["LangGraph"],
                    search_text="LangGraph",
                ),
            ]
        elif query_role == "exploit":
            candidates = [_make_candidate("round-2-exploit", source_round=2)]
        else:
            candidates = [_make_candidate("round-2-prf", source_round=2)]
        return SearchResult(
            candidates=candidates,
            diagnostics=[f"round {round_no} returned {len(candidates)} candidates"],
            request_payload={"round_no": round_no, "cursor": cursor, "query_role": query_role},
            raw_candidate_count=len(candidates),
            latency_ms=1,
        )


class SingleSeedCTS(PRFProbeCTS):
    async def search(self, **kwargs) -> SearchResult:
        result = await super().search(**kwargs)
        if kwargs["round_no"] == 1 and int(kwargs.get("cursor") or "1") == 1:
            return replace(result, candidates=result.candidates[:1], raw_candidate_count=1)
        return result


class FakeLLMPRFExtractor:
    def __init__(
        self,
        extraction: Any = None,
        *,
        exc: Exception | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.extraction = extraction
        self.exc = exc
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.last_payload = None
        self.last_call_artifact: dict[str, object] | None = None

    async def propose(self, payload):
        self.calls += 1
        self.last_payload = payload
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.exc is not None:
            raise self.exc
        if callable(self.extraction):
            return self.extraction(payload)
        if self.extraction is None:
            return LLMPRFExtraction()
        return self.extraction


def _llm_langgraph_extraction(payload) -> LLMPRFExtraction:
    sources = [
        item
        for item in payload.source_texts
        if item.resume_id in {"seed-1", "seed-2"}
        and item.source_text_raw == "LangGraph"
        and item.support_eligible
    ][:2]
    assert [item.resume_id for item in sources] == ["seed-1", "seed-2"]
    return LLMPRFExtraction(
        candidates=[
            LLMPRFCandidate(
                surface="LangGraph",
                normalized_surface="LangGraph",
                candidate_term_type="technical_phrase",
                source_resume_ids=["seed-1", "seed-2"],
                source_evidence_refs=[
                    LLMPRFSourceEvidenceRef(
                        resume_id=sources[0].resume_id,
                        source_section=sources[0].source_section,
                        source_text_id=sources[0].source_text_id,
                        source_text_index=sources[0].source_text_index,
                        source_text_hash=sources[0].source_text_hash,
                    ),
                    LLMPRFSourceEvidenceRef(
                        resume_id=sources[1].resume_id,
                        source_section=sources[1].source_section,
                        source_text_id=sources[1].source_text_id,
                        source_text_index=sources[1].source_text_index,
                        source_text_hash=sources[1].source_text_hash,
                    ),
                ],
                linked_requirements=["resume matching"],
                rationale="Both seed resumes cite LangGraph.",
            )
        ]
    )


def _install_llm_prf_extractor(runtime: WorkflowRuntime, extractor: FakeLLMPRFExtractor) -> None:
    cast(Any, runtime).llm_prf_extractor = extractor


def _disable_llm_prf_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_preflight_models(settings, *, extra_stage_names=None):  # noqa: ANN001
        del settings, extra_stage_names

    monkeypatch.setattr(orchestrator_module, "preflight_models", fake_preflight_models)


class StopAfterSecondRoundController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        if self.calls == 2:
            return SearchControllerDecision(
                thought_summary="Round 2 widens the domain surface.",
                action="search_cts",
                decision_rationale="Add one reflection term while keeping the same filter shape.",
                proposed_query_terms=["python", "resume matching", "trace"],
                proposed_filter_plan=ProposedFilterPlan(),
                response_to_reflection="Accepted the added trace term and left location execution to runtime.",
            )
        return StopControllerDecision(
            thought_summary="Stop after two completed retrieval rounds.",
            action="stop",
            decision_rationale="The top pool has stabilized and the next search is unlikely to add fit candidates.",
            response_to_reflection="The latest reflection confirms low marginal value.",
            stop_reason="controller_stop",
        )


class StopOnSecondRoundController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the two strongest anchor terms.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return StopControllerDecision(
            thought_summary="Stop before trying all admitted families.",
            action="stop",
            decision_rationale="The current pool seems stable enough.",
            response_to_reflection="Acknowledged the latest reflection.",
            stop_reason="controller_stop",
        )


class SearchThenStopController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Round 1 anchor search.",
                action="search_cts",
                decision_rationale="Start with the active family.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return StopControllerDecision(
            thought_summary="Stop despite low-quality exhaustion.",
            action="stop",
            decision_rationale="Controller wants to stop.",
            response_to_reflection="Acknowledged the latest reflection.",
            stop_reason="controller_stop",
        )


def _install_runtime_stubs(runtime: WorkflowRuntime, *, controller: object, resume_scorer: object) -> None:
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = StubRequirementExtractor()
    runtime_any.controller = controller
    runtime_any.reflection_critic = SequenceReflection()
    runtime_any.resume_scorer = resume_scorer
    runtime_any.finalizer = StubFinalizer()


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "resume matching"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        preferences={"preferred_query_terms": ["python", "resume matching"]},
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="resume matching",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="trace",
                source="jd",
                category="tooling",
                priority=3,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )


def _runtime_for_strict_source_tests(tmp_path: Path) -> WorkflowRuntime:
    runtime = _workflow_runtime(
        make_settings(
            runs_dir=str(tmp_path / "runs"),
            mock_cts=True, provider_name="cts",
            min_rounds=1,
            max_rounds=1,
        )
    )
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=ScorerSpy())
    cast(Any, runtime)._require_live_llm_config = lambda: None
    return runtime


def _run_state_for_canonical_intake_tests() -> RunState:
    requirement_sheet = _requirement_sheet()
    return RunState(
        input_truth=InputTruth(
            job_title="AI Agent Engineer",
            jd="Build agentic retrieval workflows.",
            notes="",
            job_title_sha256="job",
            jd_sha256="jd",
            notes_sha256="notes",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(),
    )


def _noop_tracer(tmp_path: Path) -> RunTracer:
    return RunTracer(tmp_path / "artifacts")


def _scored_candidate(
    resume_id: str,
    *,
    source_round: int = 1,
    overall_score: int = 88,
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=overall_score,
        must_have_match_score=90,
        preferred_match_score=80,
        risk_score=10,
        reasoning_summary=f"{resume_id} matches the role.",
        evidence=["Python retrieval evidence."],
        confidence="high",
        matched_must_haves=["Python"],
        source_round=source_round,
    )


def test_source_dispatch_merge_normalizes_candidates_before_identity_rebuild(tmp_path: Path) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    run_state = _run_state_for_canonical_intake_tests()
    source_plan = build_runtime_source_plan(
        source_kinds=("cts", "liepin"),
        settings=runtime.settings,
        runtime_run_id="run-test",
        liepin_context={"status": "ready"},
    )
    cts = _make_candidate(
        "cts-1",
        raw={
            "provider": "cts",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "AI Engineer",
        },
    )
    liepin = _make_candidate(
        "liepin-1",
        raw={
            "provider": "liepin",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "AI Engineer",
        },
    )

    runtime._merge_source_round_dispatch_result(
        run_state=run_state,
        dispatch_result=SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(source="cts", status="completed", candidates=(cts,), raw_candidate_count=1),
                SourceRoundAdapterResult(
                    source="liepin",
                    status="completed",
                    candidates=(liepin,),
                    raw_candidate_count=1,
                ),
            ),
            candidates=(cts, liepin),
            raw_candidate_count=2,
        ),
        source_plan=source_plan,
        round_no=1,
        tracer=_noop_tracer(tmp_path),
    )

    assert set(run_state.normalized_store) == {"cts-1", "liepin-1"}
    assert run_state.normalized_store["cts-1"].source_provider == "cts"
    assert run_state.normalized_store["liepin-1"].source_provider == "liepin"


def test_source_dispatch_observation_counts_selected_sources_as_raw_targets(tmp_path: Path) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    cts_candidates = tuple(_make_candidate(f"cts-{index}", raw={"provider": "cts"}) for index in range(10))
    liepin_candidates = tuple(_make_candidate(f"liepin-{index}", raw={"provider": "liepin"}) for index in range(10))
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        role_anchor_terms=["python"],
        must_have_anchor_terms=[],
        keyword_query="python",
        location_execution_plan=LocationExecutionPlan(mode="none", target_new=10),
        target_new=10,
        rationale="Target ten raw resumes per selected source.",
    )

    result = runtime._round_search_result_from_source_dispatch(
        round_no=1,
        retrieval_plan=retrieval_plan,
        query_states=(),
        dispatch_result=SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(
                    source="cts",
                    status="completed",
                    candidates=cts_candidates,
                    raw_candidate_count=10,
                ),
                SourceRoundAdapterResult(
                    source="liepin",
                    status="completed",
                    candidates=liepin_candidates,
                    raw_candidate_count=10,
                ),
            ),
            candidates=cts_candidates + liepin_candidates,
            raw_candidate_count=20,
        ),
        tracer=_noop_tracer(tmp_path),
    )

    assert result.search_observation.requested_count == 20
    assert result.search_observation.shortage_count == 0
    assert result.search_observation.unique_new_count == 20


def test_canonical_scoring_intake_scores_one_candidate_for_same_identity() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    cts = _make_candidate(
        "cts-1",
        raw={
            "provider": "cts",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "Senior AI Engineer",
        },
    )
    liepin = _make_candidate(
        "liepin-1",
        raw={
            "provider": "liepin",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "AI Engineer",
        },
    )
    run_state.candidate_store = {cts.resume_id: cts, liepin.resume_id: liepin}
    run_state.seen_resume_ids = [cts.resume_id, liepin.resume_id]
    normalize_runtime_candidates(run_state=run_state, candidates=(cts, liepin), round_no=1, tracer=None)
    rebuild_candidate_identities(run_state, source_order={"cts": 0, "liepin": 1})

    intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=1,
        new_candidates=[cts, liepin],
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
    )

    assert len(intake.scoring_candidates) == 1
    assert intake.summary.auto_merged_duplicate_count == 1
    assert intake.summary.source_raw_targets == {"cts": 10, "liepin": 10}
    assert intake.summary.per_source_raw_counts == {"cts": 1, "liepin": 1}
    assert set(intake.summary.canonical_resume_ids) == {intake.scoring_candidates[0].resume_id}


def test_canonical_scoring_intake_skips_already_scored_identity() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    old_candidate = _make_candidate(
        "cts-old",
        raw={
            "provider": "cts",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "AI Engineer",
        },
    )
    new_candidate = _make_candidate(
        "liepin-new",
        raw={
            "provider": "liepin",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "AI Engineer",
        },
    )
    run_state.candidate_store = {old_candidate.resume_id: old_candidate, new_candidate.resume_id: new_candidate}
    run_state.seen_resume_ids = [old_candidate.resume_id, new_candidate.resume_id]
    normalize_runtime_candidates(run_state=run_state, candidates=(old_candidate, new_candidate), round_no=1, tracer=None)
    rebuild_candidate_identities(run_state, source_order={"cts": 0, "liepin": 1})
    run_state.scorecards_by_resume_id[old_candidate.resume_id] = _scored_candidate(old_candidate.resume_id, source_round=1)
    identity_id = run_state.candidate_identity_by_resume_id[old_candidate.resume_id]
    run_state.canonical_resume_by_identity_id[identity_id] = RuntimeCanonicalResumeSelection(
        identity_id=identity_id,
        canonical_resume_id=old_candidate.resume_id,
    )

    intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=2,
        new_candidates=[new_candidate],
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
    )

    assert intake.scoring_candidates == []
    assert intake.summary.skipped_already_scored_identity_count == 1


def test_canonical_scoring_intake_scores_upgraded_canonical_resume_for_scored_identity() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    old_candidate = _make_candidate(
        "cts-card",
        raw={
            "provider": "cts",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "AI Engineer",
        },
    )
    detail_candidate = _make_candidate(
        "liepin-detail",
        raw={
            "provider": "liepin",
            "candidate_name": "Alice Chen",
            "current_company": "Acme",
            "current_title": "AI Engineer",
        },
    )
    run_state.candidate_store = {
        old_candidate.resume_id: old_candidate,
        detail_candidate.resume_id: detail_candidate,
    }
    normalize_runtime_candidates(
        run_state=run_state,
        candidates=(old_candidate, detail_candidate),
        round_no=1,
        tracer=None,
    )
    run_state.candidate_identity_by_resume_id = {
        old_candidate.resume_id: "identity-alice",
        detail_candidate.resume_id: "identity-alice",
    }
    run_state.canonical_resume_by_identity_id = {
        "identity-alice": RuntimeCanonicalResumeSelection(
            identity_id="identity-alice",
            canonical_resume_id=detail_candidate.resume_id,
            selected_evidence_id="evidence-detail",
            safe_reason_codes=("detail_evidence",),
        )
    }
    run_state.scorecards_by_resume_id[old_candidate.resume_id] = _scored_candidate(
        old_candidate.resume_id,
        source_round=1,
    )

    intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=2,
        new_candidates=[detail_candidate],
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
    )

    assert [candidate.resume_id for candidate in intake.scoring_candidates] == [detail_candidate.resume_id]
    assert intake.summary.skipped_already_scored_identity_count == 0
    assert intake.summary.canonical_resume_ids == (detail_candidate.resume_id,)


def test_canonical_scoring_intake_conflict_count_is_round_scoped() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    candidate = _make_candidate(
        "cts-new",
        raw={
            "provider": "cts",
            "candidate_name": "Bob Lee",
            "current_company": "Beta",
            "current_title": "Data Engineer",
        },
    )
    run_state.candidate_store = {candidate.resume_id: candidate}
    run_state.seen_resume_ids = [candidate.resume_id]
    normalize_runtime_candidates(run_state=run_state, candidates=(candidate,), round_no=2, tracer=None)
    rebuild_candidate_identities(run_state, source_order={"cts": 0, "liepin": 1})
    run_state.identity_conflicts = [
        RuntimeIdentityConflict(
            conflict_id="conflict-old",
            candidate_identity_ids=("identity-old-a", "identity-old-b"),
            resume_ids=("old-a", "old-b"),
            reason_code="medium_confidence_identity_match",
            match_score=75,
        )
    ]

    intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=2,
        new_candidates=[candidate],
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
    )

    assert intake.summary.uncertain_conflict_count == 0


def _round_state_for_reflection_tests(round_no: int) -> RoundState:
    controller_decision = SearchControllerDecision(
        thought_summary="Search one more round.",
        action="search_cts",
        decision_rationale="Need more candidates.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
    )
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=round_no,
        query_terms=["python", "retrieval"],
        role_anchor_terms=["python"],
        must_have_anchor_terms=["retrieval"],
        keyword_query="python retrieval",
        location_execution_plan=LocationExecutionPlan(mode="none", target_new=10),
        target_new=10,
        rationale="Need more candidates.",
    )
    return RoundState(
        round_no=round_no,
        controller_decision=controller_decision,
        retrieval_plan=retrieval_plan,
        search_observation=SearchObservation(
            round_no=round_no,
            requested_count=10,
            raw_candidate_count=20,
            unique_new_count=17,
            shortage_count=0,
            fetch_attempt_count=2,
            new_resume_ids=[],
        ),
    )


def test_reflection_context_includes_latest_canonical_intake_summary() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.latest_canonical_intake_summary = RuntimeCanonicalIntakeSummary(
        round_no=1,
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
        raw_candidate_count=20,
        normalized_candidate_count=20,
        identity_count=17,
        auto_merged_duplicate_count=3,
        uncertain_conflict_count=1,
        skipped_already_scored_identity_count=2,
        scoring_candidate_count=15,
        canonical_resume_ids=("resume-1",),
        per_source_raw_counts={"cts": 10, "liepin": 10},
    )
    round_state = _round_state_for_reflection_tests(round_no=1)

    context = build_reflection_context(run_state=run_state, round_state=round_state)

    assert context.canonical_intake_summary is not None
    assert context.canonical_intake_summary.auto_merged_duplicate_count == 3
    assert context.canonical_intake_summary.source_raw_targets == {"cts": 10, "liepin": 10}
    assert context.canonical_intake_summary.per_source_raw_counts == {"cts": 10, "liepin": 10}


def test_controller_context_includes_latest_canonical_intake_summary() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.latest_canonical_intake_summary = RuntimeCanonicalIntakeSummary(
        round_no=1,
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
        raw_candidate_count=20,
        normalized_candidate_count=20,
        identity_count=18,
        auto_merged_duplicate_count=2,
        uncertain_conflict_count=0,
        skipped_already_scored_identity_count=1,
        scoring_candidate_count=17,
        canonical_resume_ids=("resume-1", "resume-2"),
        per_source_raw_counts={"cts": 10, "liepin": 10},
    )

    context = build_controller_context(
        run_state=run_state,
        round_no=2,
        min_rounds=1,
        max_rounds=3,
        target_new=10,
    )

    assert context.latest_canonical_intake_summary is not None
    assert context.latest_canonical_intake_summary.identity_count == 18


def test_identity_top_pool_contains_one_scorecard_per_identity() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.candidate_identity_by_resume_id = {
        "cts-1": "identity-1",
        "liepin-1": "identity-1",
        "cts-2": "identity-2",
    }
    run_state.canonical_resume_by_identity_id = {
        "identity-1": RuntimeCanonicalResumeSelection(
            identity_id="identity-1",
            canonical_resume_id="liepin-1",
            safe_reason_codes=("detail_evidence",),
        ),
        "identity-2": RuntimeCanonicalResumeSelection(
            identity_id="identity-2",
            canonical_resume_id="cts-2",
            safe_reason_codes=("provider_rank_preserved",),
        ),
    }
    run_state.scorecards_by_resume_id = {
        "cts-1": _scored_candidate("cts-1", overall_score=95),
        "liepin-1": _scored_candidate("liepin-1", overall_score=90),
        "cts-2": _scored_candidate("cts-2", overall_score=80),
    }

    selected = select_identity_top_candidates(run_state)

    assert [item.resume_id for item in selected] == ["liepin-1", "cts-2"]
    assert run_state.top_pool_ids == ["liepin-1", "cts-2"]


def test_finalize_context_uses_identity_deduped_top_pool() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.candidate_identity_by_resume_id = {
        "cts-1": "identity-1",
        "liepin-1": "identity-1",
        "cts-2": "identity-2",
    }
    run_state.canonical_resume_by_identity_id = {
        "identity-1": RuntimeCanonicalResumeSelection(
            identity_id="identity-1",
            canonical_resume_id="liepin-1",
            safe_reason_codes=("detail_evidence",),
        ),
        "identity-2": RuntimeCanonicalResumeSelection(
            identity_id="identity-2",
            canonical_resume_id="cts-2",
            safe_reason_codes=("provider_rank_preserved",),
        ),
    }
    run_state.scorecards_by_resume_id = {
        "cts-1": _scored_candidate("cts-1", overall_score=95),
        "liepin-1": _scored_candidate("liepin-1", overall_score=90),
        "cts-2": _scored_candidate("cts-2", overall_score=80),
    }
    select_identity_top_candidates(run_state)

    context = build_finalize_context(
        run_state=run_state,
        rounds_executed=1,
        stop_reason="max_rounds_reached",
        run_id="run-test",
        run_dir="/tmp/run-test",
    )

    assert [item.resume_id for item in context.top_candidates] == ["liepin-1", "cts-2"]


def _install_broaden_stubs(runtime: WorkflowRuntime, *, include_reserve: bool) -> None:
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = SingleFamilyRequirementExtractor(include_reserve=include_reserve)
    runtime_any.controller = SearchThenStopController()
    runtime_any.reflection_critic = SequenceReflection()
    runtime_any.resume_scorer = LowQualityScorer()
    runtime_any.finalizer = StubFinalizer()


def test_dual_source_run_stops_before_scoring_when_liepin_blocked(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    scorer = ScorerSpy()
    runtime.resume_scorer = scorer

    async def fake_dispatch_source_rounds(*, request, source_adapters=None, result_callback=None):
        del request, source_adapters
        result = SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(
                    source="cts",
                    status="completed",
                    candidates=(_make_candidate("cts-1"),),
                    raw_candidate_count=1,
                ),
                SourceRoundAdapterResult(
                    source="liepin",
                    status="blocked",
                    candidates=(),
                    raw_candidate_count=0,
                    safe_reason_code="liepin_opencli_risk_page",
                ),
            ),
            candidates=(_make_candidate("cts-1"),),
            raw_candidate_count=1,
        )
        if result_callback is not None:
            for source_result in result.source_results:
                maybe_awaitable = result_callback(source_result)
                if maybe_awaitable is not None:
                    await maybe_awaitable
        return result

    monkeypatch.setattr(orchestrator_module, "dispatch_source_rounds", fake_dispatch_source_rounds)

    with pytest.raises(orchestrator_module.RunStageError, match="liepin_opencli_risk_page"):
        runtime.run(
            job_title="AI Agent Engineer",
            jd="Build agentic retrieval workflows.",
            notes="",
            source_kinds=("cts", "liepin"),
            approved_requirement_sheet=_requirement_sheet(),
        )

    assert scorer.calls == 0


@pytest.mark.parametrize(
    ("case_name", "liepin_result"),
    [
        (
            "partial",
            SourceRoundAdapterResult(source="liepin", status="partial", candidates=(), raw_candidate_count=0),
        ),
        (
            "failed",
            SourceRoundAdapterResult(source="liepin", status="failed", candidates=(), raw_candidate_count=0),
        ),
        (
            "empty",
            SourceRoundAdapterResult(source="liepin", status="completed", candidates=(), raw_candidate_count=0),
        ),
        ("missing", None),
    ],
)
def test_dual_source_run_stops_before_scoring_when_selected_source_is_degraded(
    monkeypatch,
    tmp_path: Path,
    case_name: str,
    liepin_result: SourceRoundAdapterResult | None,
) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    scorer = ScorerSpy()
    runtime.resume_scorer = scorer
    source_results = [
        SourceRoundAdapterResult(
            source="cts",
            status="completed",
            candidates=(_make_candidate("cts-1"),),
            raw_candidate_count=1,
        )
    ]
    if liepin_result is not None:
        source_results.append(liepin_result)

    async def fake_dispatch_source_rounds(*, request, source_adapters=None, result_callback=None):
        del request, source_adapters
        result = SourceRoundDispatchResult(
            source_results=tuple(source_results),
            candidates=(_make_candidate("cts-1"),),
            raw_candidate_count=1,
        )
        if result_callback is not None:
            for source_result in result.source_results:
                maybe_awaitable = result_callback(source_result)
                if maybe_awaitable is not None:
                    await maybe_awaitable
        return result

    monkeypatch.setattr(orchestrator_module, "dispatch_source_rounds", fake_dispatch_source_rounds)

    expected_reason_by_case = {
        "partial": "source_liepin_partial",
        "failed": "source_liepin_failed",
        "empty": "source_liepin_empty",
        "missing": "source_liepin_missing",
    }

    with pytest.raises(orchestrator_module.RunStageError, match=expected_reason_by_case[case_name]):
        runtime.run(
            job_title="AI Agent Engineer",
            jd="Build agentic retrieval workflows.",
            notes="",
            source_kinds=("cts", "liepin"),
            approved_requirement_sheet=_requirement_sheet(),
        )

    assert scorer.calls == 0


def test_dual_source_run_stops_before_scoring_when_no_source_candidates(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    scorer = ScorerSpy()
    runtime.resume_scorer = scorer

    async def fake_dispatch_source_rounds(*, request, source_adapters=None, result_callback=None):
        del request, source_adapters
        result = SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(source="cts", status="completed", candidates=(), raw_candidate_count=0),
                SourceRoundAdapterResult(
                    source="liepin",
                    status="failed",
                    candidates=(),
                    raw_candidate_count=0,
                    safe_reason_code="liepin_opencli_timeout",
                ),
            ),
            candidates=(),
            raw_candidate_count=0,
        )
        if result_callback is not None:
            for source_result in result.source_results:
                maybe_awaitable = result_callback(source_result)
                if maybe_awaitable is not None:
                    await maybe_awaitable
        return result

    monkeypatch.setattr(orchestrator_module, "dispatch_source_rounds", fake_dispatch_source_rounds)

    with pytest.raises(orchestrator_module.RunStageError, match="liepin_opencli_timeout"):
        runtime.run(
            job_title="AI Agent Engineer",
            jd="Build agentic retrieval workflows.",
            notes="",
            source_kinds=("cts", "liepin"),
            approved_requirement_sheet=_requirement_sheet(),
        )

    assert scorer.calls == 0


def test_cts_only_run_can_score_without_liepin(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    scorer = ScorerSpy()
    runtime.resume_scorer = scorer

    async def fake_dispatch_source_rounds(*, request, source_adapters=None, result_callback=None):
        del source_adapters
        assert request.selected_sources == ("cts",)
        query = request.logical_queries[0]
        candidate = _make_candidate("cts-1")
        result = SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(
                    source="cts",
                    status="completed",
                    candidates=(candidate,),
                    raw_candidate_count=1,
                ),
            ),
            candidates=(candidate,),
            raw_candidate_count=1,
            query_execution_receipts=(
                QueryExecutionReceipt(
                    round_no=query.round_no,
                    source_kind="cts",
                    query_instance_id=query.query_instance_id,
                    query_fingerprint=query.query_fingerprint,
                    term_group_key=query.term_group_key,
                    primary_anchor_family_id="role.data-engineer",
                    non_anchor_term_family_ids=["skill.python"],
                    query_role=query.query_role,
                    lane_type=query.lane_type,
                    query_terms=list(query.query_terms),
                    keyword_query=query.keyword_query,
                    requested_count=query.requested_count,
                    source_plan_version=query.source_plan_version,
                    status="completed",
                    dispatch_started=True,
                    raw_candidate_count=1,
                    unique_candidate_count=1,
                ),
            ),
            candidate_query_attributions=(
                RuntimeQueryCandidateAttribution(
                    source_kind="cts",
                    query_instance_id=query.query_instance_id,
                    resume_id=candidate.resume_id,
                    dedup_key=candidate.dedup_key,
                ),
            ),
        )
        if result_callback is not None:
            for source_result in result.source_results:
                maybe_awaitable = result_callback(source_result)
                if maybe_awaitable is not None:
                    await maybe_awaitable
        return result

    monkeypatch.setattr(orchestrator_module, "dispatch_source_rounds", fake_dispatch_source_rounds)

    runtime.run(
        job_title="AI Agent Engineer",
        jd="Build agentic retrieval workflows.",
        notes="",
        source_kinds=("cts",),
        approved_requirement_sheet=_requirement_sheet(),
    )

    assert scorer.calls >= 1


def _round_review_fixture() -> dict[str, object]:
    return {
        "round_no": 2,
        "controller_decision": SearchControllerDecision(
            thought_summary="Round 2 widened the term surface.",
            action="search_cts",
            decision_rationale="The first round produced one strong hit but left domain coverage thin.",
            proposed_query_terms=["python", "resume matching", "trace"],
            proposed_filter_plan=ProposedFilterPlan(),
        ),
        "retrieval_plan": RoundRetrievalPlan(
            plan_version=1,
            round_no=2,
            query_terms=["python", "resume matching", "trace"],
            keyword_query="python resume matching trace",
            projected_provider_filters={"position": "Python Engineer"},
            runtime_only_constraints=[
                RuntimeConstraint(
                    field="work_content",
                    normalized_value="resume matching",
                    source="jd",
                    rationale="Keep the retrieval workflow signal explicit.",
                    blocking=False,
                )
            ],
            location_execution_plan=LocationExecutionPlan(
                mode="balanced_all",
                allowed_locations=["上海", "杭州"],
                preferred_locations=["上海"],
                priority_order=["上海", "杭州"],
                balanced_order=["上海", "杭州"],
                rotation_offset=1,
                target_new=8,
            ),
            target_new=8,
            rationale="Expand with one reflection term while keeping city coverage balanced.",
        ),
        "observation": SearchObservation(
            round_no=2,
            requested_count=8,
            raw_candidate_count=5,
            unique_new_count=3,
            shortage_count=5,
            fetch_attempt_count=2,
            exhausted_reason="max_pages_reached",
            adapter_notes=["city dispatch rotated to 杭州 first"],
        ),
        "newly_scored_count": 3,
        "pool_decisions": [
            PoolDecision(
                resume_id="resume-1",
                round_no=2,
                decision="selected",
                rank_in_round=1,
                reasons_for_selection=["Highest score in the round."],
                compared_against_pool_summary="Entered the top pool with the strongest evidence mix.",
            ),
            PoolDecision(
                resume_id="resume-2",
                round_no=2,
                decision="retained",
                rank_in_round=2,
                reasons_for_selection=["Still strong enough to stay in the pool."],
                compared_against_pool_summary="Held rank against the new candidates.",
            ),
            PoolDecision(
                resume_id="resume-3",
                round_no=2,
                decision="dropped",
                rank_in_round=3,
                reasons_for_rejection=["Replaced by higher-ranked resumes in the global scored set."],
                compared_against_pool_summary="Fell behind the refreshed pool.",
            ),
        ],
        "top_candidates": [
            ScoredCandidate(
                resume_id="resume-1",
                fit_bucket="fit",
                overall_score=92,
                must_have_match_score=90,
                preferred_match_score=80,
                risk_score=10,
                risk_flags=[],
                reasoning_summary="Strong retrieval and Python evidence.",
                evidence=["python", "resume matching"],
                confidence="high",
                matched_must_haves=["python"],
                missing_must_haves=[],
                matched_preferences=["trace"],
                negative_signals=[],
                strengths=["Strong retrieval depth."],
                weaknesses=[],
                source_round=2,
            ),
            ScoredCandidate(
                resume_id="resume-2",
                fit_bucket="fit",
                overall_score=88,
                must_have_match_score=86,
                preferred_match_score=72,
                risk_score=14,
                risk_flags=[],
                reasoning_summary="Consistent with previous round strength.",
                evidence=["python"],
                confidence="medium",
                matched_must_haves=["python"],
                missing_must_haves=[],
                matched_preferences=[],
                negative_signals=[],
                strengths=["Stable backend signal."],
                weaknesses=["Less trace depth."],
                source_round=1,
            ),
        ],
        "dropped_candidates": [
            ScoredCandidate(
                resume_id="resume-3",
                fit_bucket="not_fit",
                overall_score=70,
                must_have_match_score=72,
                preferred_match_score=50,
                risk_score=30,
                risk_flags=[],
                reasoning_summary="Good enough to review but no longer competitive.",
                evidence=["python"],
                confidence="medium",
                matched_must_haves=["python"],
                missing_must_haves=["resume matching"],
                matched_preferences=[],
                negative_signals=["weak retrieval evidence"],
                strengths=["Some Python signal."],
                weaknesses=["Weak retrieval evidence."],
                source_round=1,
            )
        ],
        "reflection": ReflectionAdvice(
            reflection_summary="Continue with one extra tracing term.",
            suggest_stop=False,
        ),
        "next_step": "continue to controller round 3",
    }


def test_runtime_reports_round_review_matches_legacy_renderer() -> None:
    runtime = _workflow_runtime(_liepin_fixture_settings())
    payload = _round_review_fixture()

    direct = render_round_review_direct(**payload)
    legacy = runtime._render_round_review(**payload)

    assert direct == legacy
    assert direct == (
        "# Round 2 Review\n"
        "\n"
        "## Controller\n"
        "\n"
        "- Thought summary: Round 2 widened the term surface.\n"
        "- Decision rationale: The first round produced one strong hit but left domain coverage thin.\n"
        "- Query terms: python, resume matching, trace\n"
        "- Keyword query: `python resume matching trace`\n"
        "- Projected provider filters: position='Python Engineer'\n"
        "- Runtime-only constraints: work_content='resume matching'\n"
        "\n"
        "## Location Execution\n"
        "\n"
        "- Mode: `balanced_all`\n"
        "- Allowed locations: 上海, 杭州\n"
        "- Preferred locations: 上海\n"
        "- Priority order: 上海, 杭州\n"
        "- Balanced order: 上海, 杭州\n"
        "- Rotation offset: `1`\n"
        "\n"
        "## Search Outcome\n"
        "\n"
        "- Requested new candidates: `8`\n"
        "- Unique new candidates: `3`\n"
        "- Shortage: `5`\n"
        "- Fetch attempts: `2`\n"
        "- Exhausted reason: `max_pages_reached`\n"
        "- Adapter notes: city dispatch rotated to 杭州 first\n"
        "\n"
        "## City Dispatches\n"
        "\n"
        "- None\n"
        "\n"
        "## Pool Review\n"
        "\n"
        "- Newly scored this round: `3`\n"
        "- Current global top pool: resume-1, resume-2\n"
        "- Newly selected: resume-1\n"
        "- Retained: resume-2\n"
        "- Dropped from global top pool: resume-3\n"
        "- Common drop reasons: Replaced by higher-ranked resumes in the global scored set. x1\n"
        "- Dropped candidates reviewed: `1`\n"
        "\n"
        "## Reflection\n"
        "\n"
        "- Reflection summary: Continue with one extra tracing term.\n"
        "- Reflection decision: `continue`\n"
        "\n"
        "- Next step: `continue to controller round 3`\n"
    )


def test_workflow_runtime_search_once_delegates_to_retrieval_runtime(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    captured: dict[str, object] = {}

    class FakeRetrievalRuntime:
        async def search_once(
            self,
            *,
            attempt_query,
            runtime_constraints,
            round_no,
            attempt_no,
            tracer,
        ) -> SearchResult:
            captured["attempt_query"] = attempt_query
            captured["runtime_constraints"] = runtime_constraints
            captured["round_no"] = round_no
            captured["attempt_no"] = attempt_no
            captured["tracer"] = tracer
            return SearchResult(
                candidates=[_make_candidate("resume-1")],
                diagnostics=["provider search"],
                request_payload={"page": 2, "pageSize": 5},
                raw_candidate_count=1,
                latency_ms=7,
            )

    runtime.retrieval_runtime = FakeRetrievalRuntime()
    tracer = RunTracer(tmp_path / "trace-runtime-search")
    attempt_query = CTSQuery(
        query_role="exploit",
        query_terms=["python", "resume matching"],
        keyword_query="python resume matching",
        native_filters={"schoolType": 2},
        page=2,
        page_size=5,
        rationale="runtime seam test",
        adapter_notes=["runtime location dispatch: 上海"],
    )

    try:
        result = asyncio.run(
            runtime._search_once(
                attempt_query=attempt_query,
                runtime_constraints=[],
                round_no=1,
                attempt_no=2,
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert captured["attempt_query"] is attempt_query
    assert captured["round_no"] == 1
    assert captured["attempt_no"] == 2
    assert result.raw_candidate_count == 1


def test_workflow_runtime_uses_retrieval_runtime_for_round_search(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    tracer = RunTracer(tmp_path / "trace-round-search")
    query_states: list[object] = []
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        keyword_query="python",
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="single",
            allowed_locations=["上海"],
            preferred_locations=[],
            priority_order=[],
            balanced_order=["上海"],
            rotation_offset=0,
            target_new=2,
        ),
        target_new=2,
        rationale="delegation test",
    )
    captured: dict[str, object] = {}
    async def score_for_query_outcome(candidates: list[ResumeCandidate]) -> list[ScoredCandidate]:
        del candidates
        return []
    thresholds = QueryOutcomeThresholds()

    class FakeRetrievalRuntime:
        async def execute_round_search(
            self,
            *,
            round_no,
            retrieval_plan,
            query_states,
            base_adapter_notes,
            target_new,
            seen_resume_ids,
            seen_dedup_keys,
            tracer,
            score_for_query_outcome,
            query_outcome_thresholds,
        ) -> RetrievalExecutionResult:
            captured["round_no"] = round_no
            captured["retrieval_plan"] = retrieval_plan
            captured["query_states"] = query_states
            captured["base_adapter_notes"] = base_adapter_notes
            captured["target_new"] = target_new
            captured["score_for_query_outcome"] = score_for_query_outcome
            captured["query_outcome_thresholds"] = query_outcome_thresholds
            return RetrievalExecutionResult(
                executed_queries=[],
                sent_query_records=[],
                new_candidates=[],
                search_observation=SearchObservation(
                    round_no=1,
                    requested_count=2,
                    raw_candidate_count=0,
                    unique_new_count=0,
                    shortage_count=2,
                    fetch_attempt_count=0,
                ),
                search_attempts=[],
            )

    runtime.retrieval_runtime = FakeRetrievalRuntime()

    try:
        result = asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=2,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
                score_for_query_outcome=score_for_query_outcome,
                query_outcome_thresholds=thresholds,
            )
        )
    finally:
        tracer.close()

    assert captured["retrieval_plan"] is retrieval_plan
    assert captured["base_adapter_notes"] == []
    assert captured["score_for_query_outcome"] is score_for_query_outcome
    assert captured["query_outcome_thresholds"] is thresholds
    assert result[2] == []


def test_second_lane_starts_with_seventy_thirty_allocation() -> None:
    query_states = [
        LogicalQueryState(
            query_role="exploit",
            lane_type="exploit",
            query_terms=["python", "resume matching"],
            keyword_query='python "resume matching"',
            query_instance_id="exploit-1",
            query_fingerprint="fp-exploit",
            identity=ResolvedQueryIdentity("group-exploit", "role.python", ("skill.resume-matching",)),
        ),
        LogicalQueryState(
            query_role="explore",
            lane_type="generic_explore",
            query_terms=["python", "trace"],
            keyword_query="python trace",
            query_instance_id="explore-1",
            query_fingerprint="fp-explore",
            identity=ResolvedQueryIdentity("group-explore", "role.python", ("framework.trace",)),
        ),
    ]

    assert allocate_initial_lane_targets(query_states=query_states, target_new=10) == {
        "exploit": 7,
        "generic_explore": 3,
    }


def test_second_lane_allocation_does_not_exceed_small_target() -> None:
    query_states = [
        LogicalQueryState(
            query_role="exploit",
            lane_type="exploit",
            query_terms=["python", "resume matching"],
            keyword_query='python "resume matching"',
            query_instance_id="exploit-1",
            query_fingerprint="fp-exploit",
            identity=ResolvedQueryIdentity("group-exploit", "role.python", ("skill.resume-matching",)),
        ),
        LogicalQueryState(
            query_role="explore",
            lane_type="generic_explore",
            query_terms=["python", "trace"],
            keyword_query="python trace",
            query_instance_id="explore-1",
            query_fingerprint="fp-explore",
            identity=ResolvedQueryIdentity("group-explore", "role.python", ("framework.trace",)),
        ),
    ]

    assert allocate_initial_lane_targets(query_states=query_states, target_new=1) == {
        "exploit": 1,
        "generic_explore": 0,
    }
    assert allocate_initial_lane_targets(query_states=query_states, target_new=2) == {
        "exploit": 1,
        "generic_explore": 1,
    }
    assert allocate_initial_lane_targets(query_states=query_states, target_new=3) == {
        "exploit": 2,
        "generic_explore": 1,
    }


def test_second_lane_stops_after_bad_current_batch_even_with_earlier_gain(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts")
    tracer = RunTracer(tmp_path / "trace-current-batch-gate")
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=2,
        query_terms=["python", "resume matching", "trace"],
        keyword_query='python "resume matching" trace',
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="balanced_all",
            allowed_locations=["A", "B", "C"],
            preferred_locations=[],
            priority_order=[],
            balanced_order=["A", "B", "C"],
            rotation_offset=0,
            target_new=10,
        ),
        target_new=10,
        rationale="current-batch gate",
    )
    query_states = [
        LogicalQueryState(
            query_role="exploit",
            lane_type="exploit",
            query_terms=["python", "resume matching", "trace"],
            keyword_query='python "resume matching" trace',
            query_instance_id="exploit-1",
            query_fingerprint="fp-exploit",
            identity=ResolvedQueryIdentity("group-exploit", "role.python", ("skill.resume-matching", "framework.trace")),
        ),
        LogicalQueryState(
            query_role="explore",
            lane_type="generic_explore",
            query_terms=["python", "trace"],
            keyword_query="python trace",
            query_instance_id="explore-1",
            query_fingerprint="fp-explore",
            identity=ResolvedQueryIdentity("group-explore", "role.python", ("framework.trace",)),
        ),
    ]
    searched_cities: list[tuple[str, str | None]] = []

    class CurrentBatchCTS:
        async def search(
            self,
            *,
            query_terms,
            query_role,
            keyword_query,
            adapter_notes,
            provider_filters,
            runtime_constraints,
            page_size,
            round_no,
            trace_id,
            fetch_mode="summary",
            cursor=None,
        ) -> SearchResult:
            del query_terms, keyword_query, provider_filters, runtime_constraints, page_size, round_no, trace_id, fetch_mode, cursor
            city = None
            for note in adapter_notes:
                if note.startswith("runtime location dispatch: "):
                    city = note.removeprefix("runtime location dispatch: ")
                    break
            searched_cities.append((query_role, city))
            if query_role == "primary":
                return SearchResult(
                    candidates=[],
                    diagnostics=["exploit lane returned nothing"],
                    request_payload={"query_role": query_role, "city": city},
                    raw_candidate_count=0,
                    latency_ms=1,
                )
            if city == "A":
                return SearchResult(
                    candidates=[_make_candidate("explore-good", source_round=2)],
                    diagnostics=["good explore batch"],
                    request_payload={"query_role": query_role, "city": city},
                    raw_candidate_count=1,
                    latency_ms=1,
                )
            if city == "B":
                return SearchResult(
                    candidates=[_make_candidate("explore-noise", source_round=2)],
                    diagnostics=["bad explore batch"],
                    request_payload={"query_role": query_role, "city": city},
                    raw_candidate_count=1,
                    latency_ms=1,
                )
            return SearchResult(
                candidates=[_make_candidate("explore-should-not-run", source_round=2)],
                diagnostics=["unexpected third explore batch"],
                request_payload={"query_role": query_role, "city": city},
                raw_candidate_count=1,
                latency_ms=1,
            )

    async def score_for_query_outcome(candidates: list[ResumeCandidate]) -> list[ScoredCandidate]:
        scored: list[ScoredCandidate] = []
        for candidate in candidates:
            if candidate.resume_id == "explore-good":
                scored.append(
                    ScoredCandidate(
                        resume_id=candidate.resume_id,
                        fit_bucket="fit",
                        overall_score=90,
                        must_have_match_score=85,
                        preferred_match_score=60,
                        risk_score=10,
                        risk_flags=[],
                        reasoning_summary="Good explore result.",
                        evidence=["trace"],
                        confidence="high",
                        matched_must_haves=["python"],
                        missing_must_haves=[],
                        matched_preferences=[],
                        negative_signals=[],
                        strengths=[],
                        weaknesses=[],
                        source_round=2,
                    )
                )
                continue
            scored.append(
                ScoredCandidate(
                    resume_id=candidate.resume_id,
                    fit_bucket="not_fit",
                    overall_score=20,
                    must_have_match_score=10,
                    preferred_match_score=10,
                    risk_score=80,
                    risk_flags=[],
                    reasoning_summary="Off-intent noisy result.",
                    evidence=[],
                    confidence="medium",
                    matched_must_haves=[],
                    missing_must_haves=["python"],
                    matched_preferences=[],
                    negative_signals=["off_intent", "weak_match"],
                    strengths=[],
                    weaknesses=["No role alignment."],
                    source_round=2,
                )
            )
        return scored

    runtime = RetrievalRuntime(
        settings=settings,
        retrieval_service=CurrentBatchCTS(),
    )

    try:
        result = asyncio.run(
            runtime.execute_round_search(
                round_no=2,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=10,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
                score_for_query_outcome=score_for_query_outcome,
            )
        )
    finally:
        tracer.close()

    generic_records = [record for record in result.sent_query_records if record.lane_type == "generic_explore"]
    assert [record.city for record in generic_records] == ["A", "B"]
    assert ("expansion", "C") not in searched_cities


def test_runtime_round_search_uses_cts_builder_for_non_location_query(tmp_path: Path, monkeypatch) -> None:
    from seektalent.retrieval.query_builder import ProviderQueryBuildInput

    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    tracer = RunTracer(tmp_path / "trace-builder")
    captured: list[ProviderQueryBuildInput] = []

    def fake_build_provider_query(input: ProviderQueryBuildInput) -> CTSQuery:
        captured.append(input)
        return CTSQuery(
            query_role=input.query_role,
            query_terms=input.query_terms,
            keyword_query=input.keyword_query,
            native_filters=dict(input.base_filters),
            page=input.page,
            page_size=input.page_size,
            rationale=input.rationale,
            adapter_notes=list(input.adapter_notes),
        )

    monkeypatch.setattr("seektalent.runtime.retrieval_runtime.build_provider_query", fake_build_provider_query)

    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        keyword_query="python",
        projected_provider_filters={"schoolType": 2},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="none",
            allowed_locations=[],
            preferred_locations=[],
            priority_order=[],
            balanced_order=[],
            rotation_offset=0,
            target_new=1,
        ),
        target_new=1,
        rationale="builder seam test",
    )
    query_states = runtime._build_round_query_states(
        round_no=1,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=["python"],
        query_term_pool=[QueryTermCandidate(
            term="python", source="job_title", category="role_anchor", priority=1,
            evidence="title", first_added_round=0, retrieval_role="primary_role_anchor",
            queryability="admitted", family="role.python",
        )],
        used_term_group_keys=set(),
    )

    try:
        asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=["projection: school_type_requirement mapped to CTS code 2"],
                target_new=1,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert len(captured) == 1
    assert captured[0].base_filters == {"schoolType": 2}
    assert captured[0].city is None


def test_runtime_city_dispatch_passes_city_to_cts_builder(tmp_path: Path, monkeypatch) -> None:
    from seektalent.retrieval.query_builder import ProviderQueryBuildInput

    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    tracer = RunTracer(tmp_path / "trace-city-builder")
    captured: list[ProviderQueryBuildInput] = []

    def fake_build_provider_query(input: ProviderQueryBuildInput) -> CTSQuery:
        captured.append(input)
        return CTSQuery(
            query_role=input.query_role,
            query_terms=input.query_terms,
            keyword_query=input.keyword_query,
            native_filters={"location": [input.city]} if input.city is not None else {},
            page=input.page,
            page_size=input.page_size,
            rationale=input.rationale,
            adapter_notes=list(input.adapter_notes),
        )

    monkeypatch.setattr("seektalent.runtime.retrieval_runtime.build_provider_query", fake_build_provider_query)

    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        keyword_query="python",
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="single",
            allowed_locations=["上海"],
            preferred_locations=[],
            priority_order=[],
            balanced_order=["上海"],
            rotation_offset=0,
            target_new=1,
        ),
        target_new=1,
        rationale="city builder seam test",
    )
    query_states = runtime._build_round_query_states(
        round_no=1,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=["python"],
        query_term_pool=[QueryTermCandidate(
            term="python", source="job_title", category="role_anchor", priority=1,
            evidence="title", first_added_round=0, retrieval_role="primary_role_anchor",
            queryability="admitted", family="role.python",
        )],
        used_term_group_keys=set(),
    )

    try:
        asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=1,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert any(input.city == "上海" and input.base_filters == {} for input in captured)


def _fit_scorecard(
    resume_id: str,
    *,
    overall_score: int,
    must_have_match_score: int,
    risk_score: int,
    reasoning_summary: str,
    evidence: list[str],
    matched_must_haves: list[str],
    strengths: list[str],
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=overall_score,
        must_have_match_score=must_have_match_score,
        preferred_match_score=60,
        risk_score=risk_score,
        risk_flags=[],
        reasoning_summary=reasoning_summary,
        evidence=evidence,
        confidence="high",
        matched_must_haves=matched_must_haves,
        missing_must_haves=[],
        matched_preferences=[],
        negative_signals=[],
        strengths=strengths,
        weaknesses=[],
        source_round=1,
    )


def _python_feedback_seed_scorecards() -> dict[str, ScoredCandidate]:
    return {
        "fit-1": _fit_scorecard(
            "fit-1",
            overall_score=90,
            must_have_match_score=82,
            risk_score=15,
            reasoning_summary="python",
            evidence=["python", "resume matching"],
            matched_must_haves=["python"],
            strengths=["python"],
        ),
        "fit-2": _fit_scorecard(
            "fit-2",
            overall_score=88,
            must_have_match_score=80,
            risk_score=18,
            reasoning_summary="python",
            evidence=["python", "resume matching"],
            matched_must_haves=["python"],
            strengths=["python"],
        ),
    }


def test_runtime_updates_run_state_across_rounds(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=2,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        top_candidates, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=progress_events.append)
        )
    finally:
        tracer.close()

    assert rounds_executed == 2
    assert stop_reason == "max_rounds_reached"
    assert terminal_controller_round is None
    assert len(top_candidates) > 0
    assert run_state.retrieval_state.current_plan_version == 2
    assert len(run_state.retrieval_state.query_execution_ledger) == 2
    assert [len(round_state.query_outcomes) for round_state in run_state.round_history] == [1, 1]
    assert all(
        outcome.term_group_key
        for round_state in run_state.round_history
        for outcome in round_state.query_outcomes
    )
    assert [item.round_no for item in run_state.retrieval_state.sent_query_history] == [1, 2]
    assert [item.city for item in run_state.retrieval_state.sent_query_history] == ["上海", "上海"]
    assert [item.query_role for item in run_state.retrieval_state.sent_query_history] == [
        "exploit",
        "exploit",
    ]
    assert run_state.retrieval_state.sent_query_history[1].query_terms == ["python", "trace"]
    assert all(
        sum(1 for term in item.query_terms if term == "python") == 1
        for item in run_state.retrieval_state.sent_query_history
    )
    assert all(len(item.query_terms) <= 3 for item in run_state.retrieval_state.sent_query_history)
    assert len(run_state.retrieval_state.reflection_keyword_advice_history) == 2
    assert len(run_state.retrieval_state.reflection_filter_advice_history) == 2
    assert [item.term for item in run_state.retrieval_state.query_term_pool] == ["python", "resume matching", "trace"]
    assert len(run_state.round_history) == 2
    assert run_state.round_history[0].reflection_advice is not None
    assert run_state.round_history[1].reflection_advice is not None
    assert run_state.round_history[1].reflection_advice.suggest_stop is True
    assert run_state.round_history[1].controller_decision.response_to_reflection
    round_02_queries = [
        CTSQuery.model_validate(item)
        for item in json.loads(
            _round_artifact(tracer.run_dir, 2, "retrieval", "executed_queries").read_text(encoding="utf-8")
        )
    ]
    round_02_normalized = [
        json.loads(line)
        for line in _round_artifact(tracer.run_dir, 2, "scoring", "scoring_input_refs", extension="jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item.query_role for item in round_02_queries] == ["exploit"]
    assert run_state.round_history[1].search_observation is not None
    assert run_state.round_history[1].search_observation.unique_new_count <= 10
    assert len(run_state.round_history[1].search_observation.new_resume_ids) <= 10
    assert run_state.candidate_identity_by_resume_id
    assert run_state.candidate_identities
    assert run_state.source_evidence_by_identity_id
    assert {
        evidence.source
        for evidence_items in run_state.source_evidence_by_identity_id.values()
        for evidence in evidence_items
    } == {"cts"}
    assert len(round_02_normalized) == run_state.round_history[1].search_observation.unique_new_count
    round_01_top_id = run_state.round_history[0].top_candidates[0].resume_id
    assert run_state.scorecards_by_resume_id[round_01_top_id].overall_score == 90
    assert run_state.round_history[1].executed_queries == round_02_queries
    round_02_search_started = next(
        event for event in progress_events if event.type == "search_started" and event.round_no == 2
    )
    round_02_search_completed = next(
        event for event in progress_events if event.type == "search_completed" and event.round_no == 2
    )
    assert round_02_search_started.payload["planned_queries"] == [
        {
            "query_role": "exploit",
            "lane_type": "exploit",
            "query_terms": ["python", "trace"],
            "keyword_query": "python trace",
        },
    ]
    assert round_02_search_completed.payload["executed_queries"] == [
        {
            "query_role": "exploit",
            "lane_type": "exploit",
            "query_terms": ["python", "trace"],
            "keyword_query": "python trace",
        },
    ]


def test_runtime_liepin_round_persists_two_logical_query_receipts_and_outcomes(tmp_path: Path) -> None:
    settings = _liepin_fixture_settings(
        runs_dir=str(tmp_path / "runs"),
        provider_name="liepin",
        min_rounds=1,
        max_rounds=2,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    source_context = {"backend_mode": "fake_fixture", "status": "ready"}
    source_plan = build_runtime_source_plan(
        source_kinds=["liepin"],
        settings=settings,
        runtime_run_id=tracer.run_id,
        source_context=source_context,
    )

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=_detail_open_claim_ledger(run_state),
                tracer=tracer,
                source_plan=source_plan,
                source_context=source_context,
            )
        )
    finally:
        tracer.close()

    second_round_receipts = [
        receipt for receipt in run_state.retrieval_state.query_execution_ledger if receipt.round_no == 2
    ]
    assert len(second_round_receipts) == 1
    assert len(run_state.round_history[1].query_outcomes) == 1
    assert {item.term_group_key for item in run_state.round_history[1].query_outcomes}


def test_round_two_serializes_exploit_and_generic_lane_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=2,
    )
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)
    _install_llm_prf_extractor(runtime, FakeLLMPRFExtractor(LLMPRFExtraction()))
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    queries = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "executed_queries").read_text())
    sent_query_records = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "sent_query_records").read_text())
    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    assert [item["lane_type"] for item in queries] == ["exploit"]
    assert [item["lane_type"] for item in sent_query_records] == ["exploit"]
    assert all(item["query_instance_id"] for item in queries)
    assert all(item["query_fingerprint"] for item in queries)
    assert all(item["query_instance_id"] for item in sent_query_records)
    assert all(item["query_fingerprint"] for item in sent_query_records)
    assert decision["attempted_prf"] is True
    assert decision["prf_gate_passed"] is False
    assert decision["selected_lane_type"] is None
    assert decision["fallback_lane_type"] is None
    assert decision["fallback_query_fingerprint"] is None
    assert decision["selected_query_fingerprint"] is None
    assert decision["reject_reasons"] == ["no_safe_llm_prf_expression"]


def test_round_two_uses_prf_probe_when_gate_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=2,
    )
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)
    _install_llm_prf_extractor(runtime, FakeLLMPRFExtractor(_llm_langgraph_extraction))
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    queries = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "executed_queries").read_text())
    sent_query_records = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "sent_query_records").read_text())
    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    prf_policy = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "prf_policy_decision").read_text())

    assert [item["lane_type"] for item in queries] == ["exploit", "prf_probe"]
    assert [item["lane_type"] for item in sent_query_records] == ["exploit", "prf_probe"]
    assert queries[1]["query_terms"] == ["python", "LangGraph"]
    assert sent_query_records[1]["query_terms"] == ["python", "LangGraph"]
    assert decision["attempted_prf"] is True
    assert decision["prf_gate_passed"] is True
    assert decision["selected_lane_type"] == "prf_probe"
    assert decision["accepted_prf_expression"] == "LangGraph"
    assert decision["accepted_prf_term_family_id"] == "feedback.langgraph"
    assert decision["prf_seed_resume_ids"] == ["seed-1", "seed-2"]
    assert decision["prf_candidate_expression_count"] == 1
    assert queries[1]["query_instance_id"] == decision["selected_query_instance_id"]
    assert queries[1]["query_fingerprint"] == decision["selected_query_fingerprint"]
    assert prf_policy["attempted"] is True
    assert prf_policy["gate_passed"] is True
    assert prf_policy["gate_input"]["round_no"] == 2
    assert prf_policy["gate_input"]["seed_resume_ids"] == ["seed-1", "seed-2"]
    assert prf_policy["gate_input"]["seed_count"] == 2
    assert prf_policy["gate_input"]["negative_resume_ids"] == []
    assert prf_policy["gate_input"]["candidate_expression_count"] == 1
    assert prf_policy["gate_input"]["tried_term_family_ids"] == [
        "domain.resumematching",
        "framework.trace",
    ]
    assert len(prf_policy["gate_input"]["tried_query_fingerprints"]) == 1
    assert prf_policy["gate_input"]["min_seed_count"] == 2
    assert prf_policy["gate_input"]["max_negative_support_rate"] == 0.4
    assert prf_policy["gate_input"]["policy_version"] == "prf-policy-v1"
    assert prf_policy["accepted_expression"]["canonical_expression"] == "LangGraph"


def test_default_llm_prf_backend_can_drive_prf_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)
    fake_extractor = FakeLLMPRFExtractor(_llm_langgraph_extraction)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    queries = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "executed_queries").read_text())
    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    prf_policy = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "prf_policy_decision").read_text())

    assert fake_extractor.calls == 1
    assert [item["lane_type"] for item in queries] == ["exploit", "prf_probe"]
    assert queries[1]["query_terms"] == ["python", "LangGraph"]
    assert decision["prf_probe_proposal_backend"] == "llm_deepseek_v4_flash"
    assert decision["selected_lane_type"] == "prf_probe"
    assert decision["accepted_prf_expression"] == "LangGraph"
    assert decision["llm_prf_call_artifact_ref"] == "round.02.retrieval.llm_prf_call"
    assert prf_policy["accepted_expression"]["canonical_expression"] == "LangGraph"


def test_prf_selection_uses_llm_prf_without_backend_setting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)
    fake_extractor = FakeLLMPRFExtractor(_llm_langgraph_extraction)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    assert not hasattr(runtime.settings, "prf_probe_proposal_backend")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())

    assert fake_extractor.calls == 1
    assert decision["prf_probe_proposal_backend"] == "llm_deepseek_v4_flash"
    assert decision["selected_lane_type"] == "prf_probe"


def test_default_llm_prf_backend_skips_round_one_without_artifacts(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=1)
    runtime = _workflow_runtime(settings)
    fake_extractor = FakeLLMPRFExtractor(_llm_langgraph_extraction)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    decision = json.loads(_round_artifact(tracer.run_dir, 1, "retrieval", "second_lane_decision").read_text())

    assert fake_extractor.calls == 0
    assert decision["attempted_prf"] is False
    assert decision["no_fetch_reason"] == "single_lane_round"
    assert decision["prf_probe_proposal_backend"] is None
    assert not _round_artifact(tracer.run_dir, 1, "retrieval", "llm_prf_input").exists()
    assert not _round_artifact(tracer.run_dir, 1, "retrieval", "llm_prf_call").exists()
    assert not _round_artifact(tracer.run_dir, 1, "retrieval", "prf_policy_decision").exists()


def test_prf_backend_eligibility_requires_round_two_plus_multi_term_plan(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    base_plan = _round_review_fixture()["retrieval_plan"]
    assert isinstance(base_plan, RoundRetrievalPlan)

    round_one_plan = base_plan.model_copy(update={"round_no": 1, "query_terms": ["python", "resume matching"]})
    anchor_only_plan = base_plan.model_copy(update={"round_no": 2, "query_terms": ["python"]})
    eligible_plan = base_plan.model_copy(update={"round_no": 2, "query_terms": ["python", "resume matching"]})

    assert runtime._prf_second_lane_eligible(round_one_plan) is False
    assert runtime._prf_second_lane_eligible(anchor_only_plan) is False
    assert runtime._prf_second_lane_eligible(eligible_plan) is True


def test_insufficient_prf_seed_support_does_not_require_prf_provider_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight_calls: list[list[str]] = []

    def fake_preflight_models(settings, *, extra_stage_names=None):  # noqa: ANN001
        del settings
        preflight_calls.append(list(extra_stage_names or []))

    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    fake_extractor = FakeLLMPRFExtractor(_llm_langgraph_extraction)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=SingleSeedScorer())
    runtime.retrieval_service = SingleSeedCTS()
    tracer = RunTracer(tmp_path / "trace")
    monkeypatch.setattr(orchestrator_module, "preflight_models", fake_preflight_models)

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    prf_policy = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "prf_policy_decision").read_text())
    call_artifact = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_call").read_text())

    assert fake_extractor.calls == 0
    assert preflight_calls == []
    assert decision["selected_lane_type"] is None
    assert decision["llm_prf_failure_kind"] == "insufficient_prf_seed_support"
    assert prf_policy["reject_reasons"] == ["insufficient_prf_seed_support"]
    assert call_artifact["failure_kind"] == "insufficient_prf_seed_support"
    assert _round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_input").exists()
    assert _round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_call").exists()
    assert not _round_artifact(tracer.run_dir, 2, "retrieval", "prf_span_candidates").exists()


def test_llm_prf_stage_preflight_failure_falls_back_without_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight_calls: list[list[str]] = []

    def fake_preflight_models(settings, *, extra_stage_names=None):  # noqa: ANN001
        del settings
        stages = list(extra_stage_names or [])
        preflight_calls.append(stages)
        if stages == ["prf_probe_phrase_proposal"]:
            raise RuntimeError("prf stage unsupported")

    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    fake_extractor = FakeLLMPRFExtractor(_llm_langgraph_extraction)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")
    monkeypatch.setattr(orchestrator_module, "preflight_models", fake_preflight_models)

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    prf_policy = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "prf_policy_decision").read_text())
    call_artifact = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_call").read_text())

    assert fake_extractor.calls == 0
    assert preflight_calls == [["prf_probe_phrase_proposal"]]
    assert decision["selected_lane_type"] is None
    assert decision["llm_prf_failure_kind"] == "llm_prf_unsupported_capability"
    assert prf_policy["reject_reasons"] == ["llm_prf_unsupported_capability"]
    assert call_artifact["failure_kind"] == "unsupported_capability"


def test_llm_prf_backend_falls_back_to_generic_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=2,
        prf_probe_phrase_proposal_timeout_seconds=0.01,
    )
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)
    fake_extractor = FakeLLMPRFExtractor(_llm_langgraph_extraction, delay_seconds=0.05)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    call = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_call").read_text())

    assert fake_extractor.calls == 1
    assert decision["selected_lane_type"] is None
    assert decision["llm_prf_failure_kind"] == "llm_prf_timeout"
    assert decision["accepted_prf_expression"] is None
    assert call["status"] == "failed"
    assert call["failure_kind"] == "timeout"
    assert not _round_artifact(tracer.run_dir, 2, "retrieval", "prf_span_candidates").exists()


def test_llm_prf_backend_falls_back_to_generic_on_provider_failure_without_legacy_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)
    fake_extractor = FakeLLMPRFExtractor(exc=RuntimeError("provider boom"))
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())

    assert fake_extractor.calls == 1
    assert decision["selected_lane_type"] is None
    assert decision["llm_prf_failure_kind"] == "llm_prf_response_validation_error"
    assert decision["accepted_prf_expression"] is None
    assert not _round_artifact(tracer.run_dir, 2, "retrieval", "prf_span_candidates").exists()


def test_llm_prf_backend_falls_back_to_generic_when_all_candidates_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)

    def rejected_extraction(payload) -> LLMPRFExtraction:
        source = next(
            item
            for item in payload.source_texts
            if item.resume_id == "seed-1" and item.source_text_raw == "LangGraph" and item.support_eligible
        )
        return LLMPRFExtraction(
            candidates=[
                LLMPRFCandidate(
                    surface="Kubernetes",
                    normalized_surface="Kubernetes",
                    source_resume_ids=["seed-1", "seed-2"],
                    source_evidence_refs=[
                        LLMPRFSourceEvidenceRef(
                            resume_id=source.resume_id,
                            source_section=source.source_section,
                            source_text_id=source.source_text_id,
                            source_text_index=source.source_text_index,
                            source_text_hash=source.source_text_hash,
                        )
                    ],
                )
            ]
        )

    fake_extractor = FakeLLMPRFExtractor(rejected_extraction)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    grounding = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_grounding").read_text())

    assert fake_extractor.calls == 1
    assert decision["selected_lane_type"] is None
    assert decision["llm_prf_failure_kind"] == "no_safe_llm_prf_expression"
    assert grounding["records"][0]["reject_reasons"] == ["substring_not_found"]


def test_llm_prf_backend_writes_input_candidates_grounding_and_policy_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    _disable_llm_prf_preflight(monkeypatch)
    fake_extractor = FakeLLMPRFExtractor(_llm_langgraph_extraction)
    _install_llm_prf_extractor(runtime, fake_extractor)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=PRFProbeScorer())
    runtime.retrieval_service = PRFProbeCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    llm_input = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_input").read_text())
    candidates = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_candidates").read_text())
    grounding = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "llm_prf_grounding").read_text())
    decision = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "second_lane_decision").read_text())
    snapshot = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "replay_snapshot").read_text())

    seed_evidence_texts = [
        item["source_text_raw"] for item in llm_input["source_texts"] if item["support_eligible"]
    ]
    assert seed_evidence_texts.count("LangGraph") == 2
    assert len(seed_evidence_texts) <= 8
    assert candidates["candidates"][0]["surface"] == "LangGraph"
    assert {record["resume_id"] for record in grounding["records"] if record["accepted"]} == {"seed-1", "seed-2"}
    assert decision["llm_prf_input_artifact_ref"] == "round.02.retrieval.llm_prf_input"
    assert decision["llm_prf_candidates_artifact_ref"] == "round.02.retrieval.llm_prf_candidates"
    assert decision["llm_prf_grounding_artifact_ref"] == "round.02.retrieval.llm_prf_grounding"
    assert snapshot["prf_probe_proposal_backend"] == "llm_deepseek_v4_flash"
    assert snapshot["llm_prf_input_artifact_ref"] == "round.02.retrieval.llm_prf_input"
    assert snapshot["llm_prf_grounding_validator_version"] == "llm-prf-grounding-v1"
    assert snapshot["llm_prf_model_id"] == "deepseek-v4-flash"


def test_family_novelty_avoids_duplicate_sibling_hit(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts", min_rounds=1, max_rounds=2)
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=GenericFallbackScorer())
    runtime.retrieval_service = DuplicateAcrossLanesCTS()
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    candidate = run_state.candidate_store["resume-1"]
    hits = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "query_resume_hits").read_text())
    assert [item["lane_type"] for item in hits] == ["exploit"]

    exploit_hit = hits[0]

    assert candidate.first_query_instance_id == exploit_hit["query_instance_id"]
    assert candidate.first_query_fingerprint == exploit_hit["query_fingerprint"]
    assert candidate.first_round_no == 2
    assert candidate.first_lane_type == "exploit"
    assert candidate.first_location_key == "上海"
    assert candidate.first_location_type == "city"
    assert candidate.first_batch_no == exploit_hit["batch_no"]
    assert exploit_hit["was_new_to_pool"] is True
    assert exploit_hit["was_duplicate"] is False


def test_run_rounds_delegates_controller_stage_to_runtime_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=1,
    )
    runtime = _workflow_runtime(settings)

    class FailingController:
        last_validator_retry_count = 0
        last_validator_retry_reasons: list[str] = []

        async def decide(self, *, context):
            del context
            raise AssertionError("controller.decide should not be called directly from _run_rounds")

    _install_runtime_stubs(runtime, controller=FailingController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    raw_decision = StopControllerDecision(
        thought_summary="Stop.",
        action="stop",
        decision_rationale="Raw controller decision before round resolution.",
        stop_reason="raw_controller_stop",
    )
    resolved_decision = StopControllerDecision(
        thought_summary="Stop after resolution.",
        action="stop",
        decision_rationale="Delegated controller stage decided to stop.",
        stop_reason="controller_stop",
    )
    recorded: dict[str, Any] = {}

    async def fake_run_controller_stage(**kwargs):
        recorded["round_no"] = kwargs["round_no"]
        recorded["controller_context_round_no"] = kwargs["controller_context"].round_no
        recorded["controller"] = kwargs["controller"]
        recorded["progress_callback"] = kwargs["progress_callback"]
        assert "resolve_round_decision" not in kwargs
        return raw_decision, {"stage_state": "controller-state"}

    async def fake_resolve_round_decision(**kwargs):
        recorded["resolved_input"] = kwargs["controller_decision"]
        assert kwargs["controller_decision"] is raw_decision
        return resolved_decision, None

    def fake_finalize_controller_stage(**kwargs):
        recorded["finalized_state"] = kwargs["controller_stage_state"]
        recorded["completed_decision"] = kwargs["controller_decision"]

    monkeypatch.setattr(
        orchestrator_module,
        "controller_runtime",
        SimpleNamespace(
            run_controller_stage=fake_run_controller_stage,
            finalize_controller_stage=fake_finalize_controller_stage,
        ),
        raising=False,
    )
    monkeypatch.setattr(orchestrator_module.round_decision_runtime, "resolve_round_decision", fake_resolve_round_decision)

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer))
        )
    finally:
        tracer.close()

    assert recorded["round_no"] == 1
    assert recorded["controller_context_round_no"] == 1
    assert recorded["controller"] is runtime.controller
    assert recorded["progress_callback"] is None
    assert recorded["resolved_input"] is raw_decision
    assert recorded["finalized_state"] == {"stage_state": "controller-state"}
    assert recorded["completed_decision"] is resolved_decision
    assert stop_reason == "controller_stop"
    assert rounds_executed == 0
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 1


def test_runtime_reflection_does_not_mutate_query_term_pool(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=1,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = SingleFamilyRequirementExtractor(include_reserve=True)
    runtime_any.reflection_critic = MutationAttemptReflection()
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer)))
    finally:
        tracer.close()

    terms = {item.term: item for item in run_state.retrieval_state.query_term_pool}
    assert terms["trace"].active is False
    assert terms["trace"].priority == 3
    assert terms["resume matching"].active is True
    assert terms["resume matching"].priority == 2
    assert len(run_state.retrieval_state.reflection_keyword_advice_history) == 1
    advice = run_state.retrieval_state.reflection_keyword_advice_history[0]
    assert advice.suggested_activate_terms == ["trace"]
    assert advice.suggested_drop_terms == ["resume matching"]
    assert advice.suggested_deprioritize_terms == ["resume matching"]


def test_run_rounds_delegates_reflection_stage_to_runtime_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=1,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())

    class FailingReflection:
        async def reflect(self, *, context):
            del context
            raise AssertionError("reflection_critic.reflect should not be called directly from _run_rounds")

    cast(Any, runtime).reflection_critic = FailingReflection()
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    expected_advice = ReflectionAdvice(
        keyword_advice=ReflectionKeywordAdvice(),
        filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
        suggest_stop=False,
        reflection_summary="Delegated reflection advice.",
    )
    recorded: dict[str, Any] = {}

    async def fake_run_reflection_stage(**kwargs):
        recorded["round_no"] = kwargs["round_no"]
        recorded["run_state"] = kwargs["run_state"]
        recorded["round_state"] = kwargs["round_state"]
        recorded["progress_callback"] = kwargs["progress_callback"]
        kwargs["round_state"].reflection_advice = expected_advice
        return expected_advice

    monkeypatch.setattr(
        orchestrator_module,
        "reflection_runtime",
        SimpleNamespace(run_reflection_stage=fake_run_reflection_stage),
        raising=False,
    )

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer))
        )
    finally:
        tracer.close()

    assert recorded["round_no"] == 1
    assert recorded["run_state"] is run_state
    assert recorded["round_state"] is run_state.round_history[0]
    assert recorded["progress_callback"] is None
    assert run_state.round_history[0].reflection_advice == expected_advice
    assert stop_reason == "max_rounds_reached"
    assert rounds_executed == 1
    assert terminal_controller_round is None


def test_run_async_delegates_deterministic_finalization_stage_to_runtime_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    recorded: dict[str, Any] = {}

    async def fake_run_deterministic_finalization_stage(**kwargs):
        recorded["finalize_context"] = kwargs["finalize_context"]
        recorded["progress_callback"] = kwargs["progress_callback"]
        assert "write_post_finalize_artifacts" not in kwargs
        kwargs["tracer"].session.register_path(
            "runtime.finalization_context",
            "runtime/finalization_context.json",
            content_type="application/json",
            schema_version="v1",
        )
        kwargs["tracer"].session.register_path(
            "runtime.finalization_call",
            "runtime/finalization_call.json",
            content_type="application/json",
            schema_version="v1",
        )
        kwargs["tracer"].session.register_path(
            "output.final_answer",
            "output/final_answer.md",
            content_type="text/markdown",
        )
        kwargs["tracer"].write_json(
            "runtime.finalization_context",
            kwargs["slim_finalize_context"](kwargs["finalize_context"]),
        )
        final_result = FinalResult(
            run_id=kwargs["finalize_context"].run_id,
            run_dir=kwargs["finalize_context"].run_dir,
            rounds_executed=kwargs["finalize_context"].rounds_executed,
            stop_reason=kwargs["finalize_context"].stop_reason,
            summary="Delegated finalizer summary.",
            candidates=[
                FinalCandidate(
                    resume_id=item.resume_id,
                    rank=index,
                    final_score=item.overall_score,
                    fit_bucket=item.fit_bucket,
                    match_summary="delegated match summary",
                    strengths=item.strengths,
                    weaknesses=item.weaknesses,
                    matched_must_haves=item.matched_must_haves,
                    matched_preferences=item.matched_preferences,
                    risk_flags=item.risk_flags,
                    why_selected=item.reasoning_summary,
                    source_round=item.source_round,
                )
                for index, item in enumerate(kwargs["finalize_context"].top_candidates, start=1)
            ],
        )
        final_markdown = "# Delegated final markdown\n"
        kwargs["tracer"].write_json(
            "runtime.finalization_call",
            {"stage": "finalization", "engine": "deterministic_runtime", "candidate_count": len(final_result.candidates)},
        )
        kwargs["tracer"].write_json("output.final_candidates", final_result.model_dump(mode="json"))
        kwargs["tracer"].write_text("output.final_answer", final_markdown)
        return final_result, final_markdown, {
            "artifacts": [
                "runtime/finalization_context.json",
                "runtime/finalization_call.json",
                "output/final_candidates.json",
                "output/final_answer.md",
            ],
            "latency_ms": 1,
        }

    monkeypatch.setattr(
        orchestrator_module,
        "finalize_runtime",
        SimpleNamespace(
            run_deterministic_finalization_stage=fake_run_deterministic_finalization_stage,
        ),
        raising=False,
    )

    artifacts = runtime.run(source_kinds=["cts"], job_title="Senior Python Engineer", jd="JD", notes="Notes")

    assert recorded["progress_callback"] is None
    assert recorded["finalize_context"].rounds_executed == 1
    assert recorded["finalize_context"].stop_reason == "max_rounds_reached"
    assert len(recorded["finalize_context"].top_candidates) > 0
    assert artifacts.final_result.summary == "Delegated finalizer summary."
    assert artifacts.final_markdown == "# Delegated final markdown\n"


def test_run_async_reuses_one_detail_open_claim_ledger_across_round_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        provider_name="cts",
        min_rounds=1,
        max_rounds=2,
        enable_eval=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    observed_ledgers: list[DetailOpenClaimLedger] = []

    def source_round_adapters(runtime: WorkflowRuntime, context: RuntimeSourceRoundContext):
        del runtime
        observed_ledgers.append(context.detail_open_claim_ledger)
        if context.round_no == 1:
            assert context.detail_open_claim_ledger.try_claim("opaque-run-owned-claim")

        async def cts_adapter(request):
            candidate = _make_candidate(f"ledger-round-{context.round_no}", source_round=context.round_no)
            return SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(candidate,),
                raw_candidate_count=1,
                query_execution_outcomes=tuple(
                    SourceQueryExecutionOutcome(
                        query_instance_id=intent.query_instance_id,
                        status="completed",
                        dispatch_started=True,
                        raw_candidate_count=1,
                        unique_candidate_count=1,
                    )
                    for intent in request.source_query_intents_by_source["cts"]
                ),
            )

        return {"cts": cts_adapter}

    cast(Any, runtime).source_round_adapter_provider = source_round_adapters
    progress_events = []

    artifacts = runtime.run(
        source_kinds=["cts"],
        job_title="Senior Python Engineer",
        jd="JD",
        notes="Notes",
        progress_callback=progress_events.append,
    )

    assert len(observed_ledgers) == 2
    assert observed_ledgers[0] is observed_ledgers[1]
    assert artifacts.run_state is not None
    assert "opaque-run-owned-claim" in artifacts.run_state.detail_open_claims_by_provider_key
    encoded_public_events = json.dumps(
        [event.payload for event in progress_events if event.type == "runtime_public_event"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "opaque-run-owned-claim" not in encoded_public_events
    assert "DetailOpenClaimLedger" not in encoded_public_events


def test_runtime_builds_plan_for_reflection_backed_inactive_term(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=2,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = SingleFamilyRequirementExtractor(include_reserve=True)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer)))
    finally:
        tracer.close()

    round_02_plan = json.loads(
        _round_artifact(tracer.run_dir, 2, "retrieval", "retrieval_plan").read_text(encoding="utf-8")
    )
    assert round_02_plan["query_terms"] == ["python", "trace"]
    assert {item.term: item for item in run_state.retrieval_state.query_term_pool}["trace"].active is False


class RecordingScorer:
    def __init__(self) -> None:
        self.resume_ids: list[str] = []
        self.runtime_only_constraints: list[list[RuntimeConstraint]] = []

    async def score_candidates_parallel(self, *, contexts, tracer):
        del tracer
        self.resume_ids.extend(context.normalized_resume.resume_id for context in contexts)
        self.runtime_only_constraints.extend(context.runtime_only_constraints for context in contexts)
        return (
            [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=95,
                    must_have_match_score=90,
                    preferred_match_score=70,
                    risk_score=5,
                    risk_flags=[],
                    reasoning_summary="Fresh candidate scored once.",
                    evidence=["python"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["resume matching"],
                    negative_signals=[],
                    strengths=["Fresh strong match."],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
                for context in contexts
            ],
            [],
        )


def test_score_round_keeps_existing_scorecards_and_only_scores_new_resumes(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts")
    runtime = _workflow_runtime(settings)
    cast(Any, runtime).resume_scorer = RecordingScorer()
    _, requirement_sheet = asyncio.run(StubRequirementExtractor().extract_with_draft(input_truth=None))
    existing = ScoredCandidate(
        resume_id="seen",
        fit_bucket="fit",
        overall_score=80,
        must_have_match_score=78,
        preferred_match_score=60,
        risk_score=12,
        risk_flags=[],
        reasoning_summary="Existing score should stay untouched.",
        evidence=["python"],
        confidence="high",
        matched_must_haves=["python"],
        missing_must_haves=[],
        matched_preferences=["resume matching"],
        negative_signals=[],
        strengths=["Existing top match."],
        weaknesses=[],
        source_round=1,
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Senior Python Engineer",
            jd="JD",
            notes="Notes",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        candidate_store={"seen": _make_candidate("seen", source_round=1)},
        scorecards_by_resume_id={"seen": existing},
        top_pool_ids=["seen"],
    )
    tracer = RunTracer(tmp_path / "trace-runs")
    runtime_only_constraints = [
        RuntimeConstraint(
            field="age_requirement",
            normalized_value=["max=35"],
            source="notes",
            rationale="Age not projected to CTS.",
            blocking=False,
        )
    ]

    try:
        top_candidates, pool_decisions, dropped_candidates = asyncio.run(
            runtime._score_round(
                round_no=2,
                new_candidates=[_make_candidate("seen", source_round=2), _make_candidate("fresh", source_round=2)],
                run_state=run_state,
                tracer=tracer,
                runtime_only_constraints=runtime_only_constraints,
            )
        )
    finally:
        tracer.close()

    assert cast(Any, runtime).resume_scorer.resume_ids == ["fresh"]
    assert cast(Any, runtime).resume_scorer.runtime_only_constraints == [runtime_only_constraints]
    assert run_state.scorecards_by_resume_id["seen"].overall_score == 80
    assert run_state.scorecards_by_resume_id["fresh"].overall_score == 95
    assert [item.resume_id for item in top_candidates] == ["fresh", "seen"]
    assert [item.decision for item in pool_decisions] == ["selected", "retained"]
    assert dropped_candidates == []


class QueryOutcomeScorerRequiringSession:
    async def score_candidates_parallel(self, *, contexts, tracer):
        tracer.session.register_path(
            "round.01.scoring.scoring_calls",
            "rounds/01/scoring/scoring_calls.jsonl",
            content_type="application/jsonl",
            schema_version="v1",
        )
        return (
            [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=88,
                    must_have_match_score=84,
                    preferred_match_score=65,
                    risk_score=10,
                    risk_flags=[],
                    reasoning_summary="Query outcome scorer completed.",
                    evidence=["python"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["resume matching"],
                    negative_signals=[],
                    strengths=["Query outcome score."],
                    weaknesses=[],
                    source_round=context.normalized_resume.source_round or context.round_no,
                )
                for context in contexts
            ],
            [],
        )


def test_query_outcome_scoring_noop_tracer_exposes_session_contract(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts")
    runtime = _workflow_runtime(settings)
    cast(Any, runtime).resume_scorer = QueryOutcomeScorerRequiringSession()
    _, requirement_sheet = asyncio.run(StubRequirementExtractor().extract_with_draft(input_truth=None))
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Senior Python Engineer",
            jd="JD",
            notes="Notes",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        candidate_store={"query-outcome-1": _make_candidate("query-outcome-1", source_round=1)},
    )

    scored = asyncio.run(
        runtime._score_candidates_for_query_outcome(
            round_no=1,
            candidates=[_make_candidate("query-outcome-1")],
            run_state=run_state,
            runtime_only_constraints=[],
        )
    )

    assert [item.resume_id for item in scored] == ["query-outcome-1"]


def test_materialize_candidates_requires_candidate_store_entry(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    scored = ScoredCandidate(
        resume_id="missing",
        fit_bucket="fit",
        overall_score=90,
        must_have_match_score=88,
        preferred_match_score=70,
        risk_score=8,
        reasoning_summary="Scored candidate without source resume.",
        confidence="high",
        source_round=1,
    )

    with pytest.raises(KeyError, match="missing"):
        runtime._materialize_candidates(scored_candidates=[scored], candidate_store={})


def test_workflow_runtime_uses_retrieval_runtime_module_for_retrieval_execution(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))

    assert isinstance(runtime.retrieval_runtime, RetrievalRuntime)
    assert runtime.retrieval_runtime.settings is runtime.settings
    assert runtime.retrieval_runtime.retrieval_service is runtime.retrieval_service


def test_workflow_runtime_retrieval_service_rebind_syncs_retrieval_runtime(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    fake_retrieval_service = cast(Any, object())

    runtime.retrieval_service = fake_retrieval_service

    assert runtime.retrieval_service is fake_retrieval_service
    assert runtime.retrieval_runtime.retrieval_service is fake_retrieval_service


def test_workflow_runtime_retrieval_runtime_rejects_direct_rebinding(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))

    with pytest.raises(FrozenInstanceError):
        runtime.retrieval_runtime.retrieval_service = cast(Any, object())


def test_runtime_records_terminal_controller_round_separately(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=3,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=StopAfterSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer))
        )
    finally:
        tracer.close()

    assert rounds_executed == 3
    assert stop_reason == "max_rounds_reached"
    assert len(run_state.round_history) == 3
    assert terminal_controller_round is None


def test_runtime_rejects_controller_stop_when_stop_guidance_blocks_stop(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=3,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=StopOnSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = _python_feedback_seed_scorecards()
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        with pytest.raises(ValueError, match="controller_stop_not_allowed"):
            asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer)))
    finally:
        tracer.close()

    round_02_context = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "controller_context").read_text(encoding="utf-8")
    )

    assert round_02_context["stop_guidance"]["can_stop"] is False
    assert not _round_artifact(tracer.run_dir, 2, "controller", "controller_decision").exists()
    assert not _round_artifact(tracer.run_dir, 2, "retrieval", "retrieval_plan").exists()


def test_runtime_forces_broaden_with_inactive_admitted_reserve_term(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=10,
    )
    runtime = _workflow_runtime(settings)
    _install_broaden_stubs(runtime, include_reserve=True)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer))
        )
    finally:
        tracer.close()

    round_02_context = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "controller_context").read_text(encoding="utf-8")
    )
    round_02_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "controller_decision").read_text(encoding="utf-8")
    )
    round_02_plan = json.loads(
        _round_artifact(tracer.run_dir, 2, "retrieval", "retrieval_plan").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )

    assert round_02_context["stop_guidance"]["quality_gate_status"] == "broaden_required"
    assert rescue_decision["selected_lane"] == "reserve_broaden"
    assert rescue_decision["forced_query_terms"] == ["python", "trace"]
    assert round_02_decision["action"] == "source_search"
    assert "Runtime broaden" in round_02_decision["decision_rationale"]
    assert round_02_decision["proposed_query_terms"] == ["python", "trace"]
    assert round_02_plan["query_terms"] == ["python", "trace"]
    assert [item.term for item in run_state.retrieval_state.query_term_pool if item.active] == [
        "python",
        "resume matching",
        "trace",
    ]
    assert stop_reason == "query_family_exhausted"
    assert rounds_executed == 3
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 4
    assert terminal_controller_round.stop_guidance.quality_gate_status == "low_quality_exhausted"
    assert terminal_controller_round.stop_guidance.broadening_attempted is True


def test_runtime_forces_anchor_only_broaden_when_no_reserve_term_remains(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=10,
    )
    runtime = _workflow_runtime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer))
        )
    finally:
        tracer.close()

    round_02_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "controller_decision").read_text(encoding="utf-8")
    )
    round_02_plan = json.loads(
        _round_artifact(tracer.run_dir, 2, "retrieval", "retrieval_plan").read_text(encoding="utf-8")
    )
    round_02_queries = json.loads(
        _round_artifact(tracer.run_dir, 2, "retrieval", "executed_queries").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "anchor_only"
    assert rescue_decision["forced_query_terms"] == ["python"]
    assert round_02_decision["proposed_query_terms"] == ["python"]
    assert round_02_plan["query_terms"] == ["python"]
    assert [item["query_role"] for item in round_02_queries] == ["exploit"]
    assert run_state.retrieval_state.sent_query_history[-1].query_terms == ["python"]
    assert stop_reason == "query_family_exhausted"
    assert rounds_executed == 2
    assert terminal_controller_round is not None
    assert terminal_controller_round.stop_guidance.quality_gate_status == "low_quality_exhausted"
    assert terminal_controller_round.stop_guidance.broadening_attempted is True


def test_runtime_force_broaden_decision_delegates_to_rescue_execution_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    _install_broaden_stubs(runtime, include_reserve=True)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
    finally:
        tracer.close()
    expected = SearchControllerDecision(
        thought_summary="delegated",
        action="search_cts",
        decision_rationale="delegated rationale",
        proposed_query_terms=["python"],
        proposed_filter_plan=ProposedFilterPlan(),
    )
    recorded: dict[str, Any] = {}

    def fake_force_broaden_decision(*, run_state, round_no, reason):
        recorded["run_state"] = run_state
        recorded["round_no"] = round_no
        recorded["reason"] = reason
        return expected

    monkeypatch.setattr(rescue_execution_runtime, "force_broaden_decision", fake_force_broaden_decision)

    decision = runtime._force_broaden_decision(run_state=run_state, round_no=2, reason="broaden required")

    assert decision is expected
    assert recorded == {
        "run_state": run_state,
        "round_no": 2,
        "reason": "broaden required",
    }


def test_runtime_falls_back_to_anchor_only_when_candidate_feedback_has_no_safe_term(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=10,
        candidate_feedback_enabled=True,
    )
    runtime = _workflow_runtime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = _python_feedback_seed_scorecards()
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer)))
    finally:
        tracer.close()

    round_02_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "controller_decision").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )
    feedback_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "retrieval", "candidate_feedback_decision").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "anchor_only"
    assert {"lane": "candidate_feedback", "reason": "no_safe_feedback_term"} in rescue_decision["skipped_lanes"]
    assert all(item["lane"] != "web_company_discovery" for item in rescue_decision["skipped_lanes"])
    assert feedback_decision["accepted_term"] is None
    assert round_02_decision["proposed_query_terms"] == ["python"]


def test_candidate_feedback_lane_does_not_instantiate_model_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=10,
        candidate_feedback_enabled=True,
    )
    runtime = _workflow_runtime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    progress_events = []

    def fail_model_steps_init(self, settings, prompt) -> None:  # noqa: ANN001
        del self, settings, prompt
        raise AssertionError("active candidate feedback rescue lane should not instantiate CandidateFeedbackModelSteps")

    monkeypatch.setattr(candidate_feedback_model_steps.CandidateFeedbackModelSteps, "__init__", fail_model_steps_init)

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = {
            "fit-1": _fit_scorecard(
                "fit-1",
                overall_score=90,
                must_have_match_score=82,
                risk_score=15,
                reasoning_summary="Built LangGraph workflow orchestration.",
                evidence=["LangGraph workflow orchestration and tool calling."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph", "tool calling"],
            ),
            "fit-2": _fit_scorecard(
                "fit-2",
                overall_score=88,
                must_have_match_score=80,
                risk_score=18,
                reasoning_summary="Used LangGraph for Agent workflow.",
                evidence=["LangGraph and RAG workflow implementation."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph"],
            ),
        }
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=progress_events.append)
        )
    finally:
        tracer.close()

    round_02_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "controller_decision").read_text(encoding="utf-8")
    )
    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )
    feedback_terms = json.loads(
        _round_artifact(tracer.run_dir, 2, "retrieval", "candidate_feedback_terms").read_text(encoding="utf-8")
    )

    assert rescue_decision["selected_lane"] == "candidate_feedback"
    assert round_02_decision["proposed_query_terms"] == ["python", "LangGraph"]
    assert feedback_terms["accepted_term"]["term"] == "LangGraph"
    assert any(
        event.type == "rescue_lane_completed" and event.payload.get("accepted_term") == "LangGraph"
        for event in progress_events
    )
    assert run_state.retrieval_state.candidate_feedback_attempted is True
    assert stop_reason == "query_family_exhausted"
    assert rounds_executed == 3
    assert terminal_controller_round is not None
    assert terminal_controller_round.round_no == 4


def test_low_quality_rescue_candidate_feedback_does_not_call_llm_prf(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=10,
        candidate_feedback_enabled=True,
    )
    runtime = _workflow_runtime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)

    class ExplodingLLMPRFExtractor:
        async def propose(self, payload) -> LLMPRFExtraction:
            raise AssertionError("low-quality rescue must not call llm_prf")

    _install_llm_prf_extractor(runtime, cast(Any, ExplodingLLMPRFExtractor()))
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.scorecards_by_resume_id = {
            "fit-1": _fit_scorecard(
                "fit-1",
                overall_score=90,
                must_have_match_score=82,
                risk_score=15,
                reasoning_summary="Built LangGraph workflow orchestration.",
                evidence=["LangGraph workflow orchestration and tool calling."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph", "tool calling"],
            ),
            "fit-2": _fit_scorecard(
                "fit-2",
                overall_score=88,
                must_have_match_score=80,
                risk_score=18,
                reasoning_summary="Used LangGraph for Agent workflow.",
                evidence=["LangGraph and RAG workflow implementation."],
                matched_must_haves=["Agent workflow orchestration with LangGraph"],
                strengths=["LangGraph"],
            ),
        }
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer), progress_callback=None))
    finally:
        tracer.close()

    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )
    assert rescue_decision["selected_lane"] == "candidate_feedback"


def test_runtime_allows_stop_after_feedback_has_no_safe_term_once_anchor_only_was_attempted(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=1,
        max_rounds=10,
        candidate_feedback_enabled=True,
    )
    runtime = _workflow_runtime(settings)
    _install_broaden_stubs(runtime, include_reserve=False)
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state.retrieval_state.anchor_only_broaden_attempted = True
        run_state.scorecards_by_resume_id = _python_feedback_seed_scorecards()
        run_state.top_pool_ids = ["fit-1", "fit-2"]
        asyncio.run(runtime._run_rounds(run_state=run_state, detail_open_claim_ledger=_detail_open_claim_ledger(run_state), tracer=tracer, source_plan=_cts_source_plan(runtime, tracer)))
    finally:
        tracer.close()

    rescue_decision = json.loads(
        _round_artifact(tracer.run_dir, 2, "controller", "rescue_decision").read_text(encoding="utf-8")
    )
    assert rescue_decision["selected_lane"] == "allow_stop"
    assert {"lane": "candidate_feedback", "reason": "no_safe_feedback_term"} in rescue_decision["skipped_lanes"]
    assert {"lane": "anchor_only", "reason": "already_attempted"} in rescue_decision["skipped_lanes"]
    assert all(item["lane"] != "web_company_discovery" for item in rescue_decision["skipped_lanes"])
    assert {"round_no": 2, "selected_lane": "allow_stop"} in run_state.retrieval_state.rescue_lane_history


def test_runtime_min_rounds_count_completed_retrieval_rounds(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True, provider_name="cts",
        min_rounds=3,
        max_rounds=4,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=StopAfterSecondRoundController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()

    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        _, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=_detail_open_claim_ledger(run_state),
                tracer=tracer,
                source_plan=_cts_source_plan(runtime, tracer),
            )
        )
    finally:
        tracer.close()

    round_03_context = json.loads(
        _round_artifact(tracer.run_dir, 3, "controller", "controller_context").read_text(encoding="utf-8")
    )

    assert round_03_context["budget"]["retrieval_rounds_completed"] == 2
    assert round_03_context["stop_guidance"]["can_stop"] is False
    assert "2 retrieval rounds completed" in round_03_context["stop_guidance"]["reason"]
    assert stop_reason == "max_rounds_reached"
    assert rounds_executed == 4
    assert terminal_controller_round is None


def test_pre_controller_final_exhaustion_skips_controller_finalizer_and_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        provider_name="cts",
        min_rounds=3,
        max_rounds=4,
        candidate_feedback_enabled=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    calls = {"controller": 0, "finalizer": 0, "provider": 0}

    class ControllerSpy:
        async def decide(self, *, context):  # noqa: ANN001
            del context
            calls["controller"] += 1
            raise AssertionError("exhaustion must skip controller")

    def finalize_spy(**kwargs):  # noqa: ANN003
        del kwargs
        calls["finalizer"] += 1
        raise AssertionError("exhaustion must skip controller finalization")

    async def provider_spy(**kwargs):  # noqa: ANN003
        del kwargs
        calls["provider"] += 1
        raise AssertionError("exhaustion must skip provider dispatch")

    monkeypatch.setattr(controller_runtime_module, "finalize_controller_stage", finalize_spy)
    monkeypatch.setattr(orchestrator_module, "dispatch_source_rounds", provider_spy)
    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        runtime.controller = ControllerSpy()
        anchor = run_state.requirement_sheet.title_anchor_terms[0]
        for index, item in enumerate(run_state.retrieval_state.query_term_pool):
            if item.retrieval_role in {"primary_role_anchor", "role_anchor", "secondary_title_anchor"}:
                continue
            _mark_query_terms_dispatched(
                run_state,
                query_terms=[anchor, item.term],
                query_id=f"used-{index}",
            )
        _mark_query_terms_dispatched(run_state, query_terms=[anchor], query_id="used-anchor")
        _, stop_reason, rounds_executed, terminal = asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=_detail_open_claim_ledger(run_state),
                tracer=tracer,
                source_plan=_cts_source_plan(runtime, tracer),
            )
        )
    finally:
        tracer.close()

    assert calls == {"controller": 0, "finalizer": 0, "provider": 0}
    assert stop_reason == "query_family_exhausted"
    assert rounds_executed == 0
    assert terminal is not None
    assert terminal.stop_guidance.can_stop is False


def test_post_controller_novelty_exhaustion_keeps_model_evidence_and_skips_normal_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        provider_name="cts",
        min_rounds=1,
        max_rounds=2,
        candidate_feedback_enabled=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace-runs")
    job_title, jd, notes = _sample_inputs()
    calls = {"controller": 0, "finalizer": 0}
    run_state_holder: dict[str, RunState] = {}
    decision_holder: dict[str, SearchControllerDecision] = {}

    class ConsumingController:
        async def decide(self, *, context):  # noqa: ANN001
            calls["controller"] += 1
            run_state = run_state_holder["value"]
            anchor = run_state.requirement_sheet.title_anchor_terms[0]
            selected = None
            for index, item in enumerate(run_state.retrieval_state.query_term_pool):
                if item.retrieval_role in {"primary_role_anchor", "role_anchor", "secondary_title_anchor"}:
                    continue
                _mark_query_terms_dispatched(
                    run_state,
                    query_terms=[anchor, item.term],
                    query_id=f"race-{index}",
                )
                selected = selected or item.term
            assert selected is not None
            decision = SearchControllerDecision(
                thought_summary="Search a family that became consumed after preflight.",
                action="source_search",
                decision_rationale="Exercise the post-controller exhaustion path.",
                proposed_query_terms=[anchor, selected],
                proposed_filter_plan=ProposedFilterPlan(),
            )
            decision_holder["value"] = decision
            return decision

    def finalize_spy(**kwargs):  # noqa: ANN003
        del kwargs
        calls["finalizer"] += 1

    monkeypatch.setattr(controller_runtime_module, "finalize_controller_stage", finalize_spy)
    try:
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        run_state_holder["value"] = run_state
        runtime.controller = ConsumingController()
        anchor = run_state.requirement_sheet.title_anchor_terms[0]
        _mark_query_terms_dispatched(run_state, query_terms=[anchor], query_id="used-anchor")
        _, stop_reason, _, _ = asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=_detail_open_claim_ledger(run_state),
                tracer=tracer,
                source_plan=_cts_source_plan(runtime, tracer),
            )
        )
    finally:
        tracer.close()

    assert calls == {"controller": 1, "finalizer": 0}
    assert stop_reason == "query_family_exhausted"
    controller_call = json.loads(
        _round_artifact(tracer.run_dir, 1, "controller", "controller_call").read_text(encoding="utf-8")
    )
    override = json.loads(
        _round_artifact(tracer.run_dir, 1, "controller", "controller_decision").read_text(encoding="utf-8")
    )
    assert controller_call["status"] == "succeeded"
    assert controller_call["structured_output_sha256"] == json_sha256(
        decision_holder["value"].model_dump(mode="json")
    )
    assert override["stop_reason"] == "query_family_exhausted"


def test_four_round_ai_agent_runtime_never_replays_non_anchor_family_or_term_group(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        provider_name="cts",
        min_rounds=1,
        max_rounds=4,
        candidate_feedback_enabled=False,
    )
    runtime = _workflow_runtime(settings)
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=StubScorer())

    class AgentRequirementExtractor:
        async def extract_with_draft(self, *, input_truth):  # noqa: ANN001
            del input_truth
            sheet = RequirementSheet(
                job_title="AI Agent Engineer",
                title_anchor_terms=["AI Agent"],
                title_anchor_rationale="AI Agent is the primary title anchor.",
                role_summary="Build production agent systems.",
                must_have_capabilities=["LangChain", "RAG"],
                hard_constraints=HardConstraintSlots(locations=["上海"]),
                initial_query_term_pool=[
                    QueryTermCandidate(
                        term="AI Agent",
                        source="job_title",
                        category="role_anchor",
                        priority=1,
                        evidence="Job title",
                        first_added_round=0,
                        family="role.ai-agent",
                    ),
                    QueryTermCandidate(
                        term="LangChain",
                        source="jd",
                        category="tooling",
                        priority=2,
                        evidence="JD",
                        first_added_round=0,
                        family="framework.langchain",
                    ),
                    QueryTermCandidate(
                        term="RAG",
                        source="jd",
                        category="domain",
                        priority=3,
                        evidence="JD",
                        first_added_round=0,
                        family="domain.rag",
                    ),
                ],
                scoring_rationale="Prefer production agent evidence.",
            )
            draft = RequirementExtractionDraft(
                title_anchor_terms=["AI Agent"],
                title_anchor_rationale="AI Agent is the primary title anchor.",
                jd_query_terms=["LangChain", "RAG"],
                role_summary=sheet.role_summary,
                must_have_capabilities=sheet.must_have_capabilities,
                locations=["上海"],
                scoring_rationale=sheet.scoring_rationale,
            )
            return draft, sheet

    class ReplayingController:
        async def decide(self, *, context):  # noqa: ANN001
            return SearchControllerDecision(
                thought_summary="Repeat the primary family proposal.",
                action="source_search",
                decision_rationale="Runtime must project this onto novelty.",
                proposed_query_terms=["AI Agent", "LangChain"],
                proposed_filter_plan=ProposedFilterPlan(),
                response_to_reflection=("Apply novelty." if context.previous_reflection is not None else None),
            )

    runtime.requirement_extractor = AgentRequirementExtractor()
    runtime.controller = ReplayingController()
    tracer = RunTracer(tmp_path / "trace-runs")
    try:
        run_state = asyncio.run(
            runtime._build_run_state(
                job_title="AI Agent Engineer",
                jd="Build LangChain and RAG agent systems.",
                notes="Production experience required.",
                tracer=tracer,
            )
        )
        _, stop_reason, rounds_executed, terminal = asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                detail_open_claim_ledger=_detail_open_claim_ledger(run_state),
                tracer=tracer,
                source_plan=_cts_source_plan(runtime, tracer),
            )
        )
    finally:
        tracer.close()

    attempted = [
        outcome
        for round_state in run_state.round_history
        for outcome in round_state.query_outcomes
        if outcome.attempted
    ]
    non_anchor_families = [
        family
        for outcome in attempted
        for family in outcome.non_anchor_term_family_ids
    ]
    assert stop_reason == "query_family_exhausted"
    assert rounds_executed == 3
    assert terminal is not None and terminal.round_no == 4
    assert len(non_anchor_families) == len(set(non_anchor_families))
    assert len([outcome.term_group_key for outcome in attempted]) == len(
        {outcome.term_group_key for outcome in attempted}
    )
    assert all(
        sum(term == "AI Agent" for term in outcome.query_terms) == 1
        for outcome in attempted
    )
    assert any(len(round_state.query_outcomes) == 1 for round_state in run_state.round_history)


def test_runtime_degrades_to_single_query_when_no_distinct_explore_query_exists(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    requirement_sheet = RequirementSheet(
        job_title="Senior Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "resume matching"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="resume matching",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )
    retrieval_plan = build_round_retrieval_plan(
        plan_version=2,
        round_no=2,
        query_terms=["python", "resume matching"],
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=requirement_sheet.hard_constraints.locations,
            preferred_locations=requirement_sheet.preferences.preferred_locations,
            round_no=2,
            target_new=10,
        ),
        target_new=10,
        rationale="single query fallback",
    )

    query_states = runtime._build_round_query_states(
        round_no=2,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        used_term_group_keys=set(),
    )

    assert [item.query_role for item in query_states] == ["exploit"]


def test_runtime_diagnostics_does_not_label_collapsed_multi_anchor_query_after_round_one(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    requirement_sheet = RequirementSheet(
        job_title="Backend Platform Engineer",
        title_anchor_terms=["Backend", "Platform"],
        title_anchor_rationale="Title contributes both backend and platform anchors.",
        role_summary="Build backend platform services.",
        must_have_capabilities=["Python"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Backend",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.backend",
            ),
            QueryTermCandidate(
                term="Platform",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.platform",
            ),
            QueryTermCandidate(
                term="Python",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="core_skill",
                queryability="admitted",
                family="skill.python",
            ),
        ],
        scoring_rationale="Prefer backend platform resumes with Python signal.",
    )
    round_state = RoundState(
        round_no=2,
        controller_decision=SearchControllerDecision(
            thought_summary="Round 2 search.",
            action="search_cts",
            decision_rationale="Used a collapsed primary-plus-domain query.",
            proposed_query_terms=["Backend", "Python"],
            proposed_filter_plan=ProposedFilterPlan(),
        ),
        retrieval_plan=RoundRetrievalPlan(
            plan_version=1,
            round_no=2,
            query_terms=["Backend", "Python"],
            keyword_query="Backend Python",
            projected_provider_filters={},
            runtime_only_constraints=[],
            location_execution_plan=LocationExecutionPlan(
                mode="single",
                allowed_locations=["上海"],
                preferred_locations=[],
                priority_order=[],
                balanced_order=["上海"],
                rotation_offset=0,
                target_new=10,
            ),
            target_new=10,
            rationale="round 2",
        ),
        search_observation=SearchObservation(
            round_no=2,
            requested_count=10,
            raw_candidate_count=0,
            unique_new_count=0,
            shortage_count=10,
            fetch_attempt_count=1,
        ),
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Backend Platform Engineer",
            jd="Build backend platform services.",
            notes="Prefer Python signal.",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        round_history=[round_state],
    )

    diagnostics = runtime._build_round_search_diagnostics(run_state=run_state, round_state=round_state)

    assert diagnostics["audit_labels"] == []


def test_runtime_helpers_use_primary_anchor_and_skip_secondary_title_anchor_reserve() -> None:
    retrieval_state = RetrievalState(
        current_plan_version=1,
        query_term_pool=[
            QueryTermCandidate(
                term="Backend",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.backend",
            ),
            QueryTermCandidate(
                term="Platform",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.platform",
            ),
            QueryTermCandidate(
                term="Python",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                active=False,
                retrieval_role="core_skill",
                queryability="admitted",
                family="skill.python",
            ),
        ],
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["Backend", "Platform"],
                keyword_query="Backend Platform",
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )

    assert rescue_execution_runtime.active_admitted_anchor(retrieval_state.query_term_pool).term == "Backend"
    reserve = rescue_execution_runtime.untried_admitted_non_anchor_reserve(retrieval_state)
    assert reserve is not None
    assert reserve.term == "Python"


def _projection_run_state() -> RunState:
    requirement_sheet = RequirementSheet(
        job_title="AI 主观投资工程师",
        title_anchor_terms=["AI", "主观投资"],
        title_anchor_rationale="Title contributes both AI and investment anchors.",
        role_summary="Build AI investment systems.",
        must_have_capabilities=["AI", "模型部署"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="AI",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.ai",
            ),
            QueryTermCandidate(
                term="主观投资",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.investment",
            ),
            QueryTermCandidate(
                term="模型部署",
                source="jd",
                category="domain",
                priority=3,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="core_skill",
                queryability="admitted",
                family="skill.model-deploy",
            ),
            QueryTermCandidate(
                term="检索增强",
                source="jd",
                category="domain",
                priority=4,
                evidence="JD body",
                first_added_round=0,
                retrieval_role="domain_context",
                queryability="admitted",
                family="domain.rag",
            ),
        ],
        scoring_rationale="Score AI system fit first.",
    )
    return RunState(
        input_truth=InputTruth(
            job_title=requirement_sheet.job_title,
            jd="Build AI investment systems.",
            notes="Prefer model deployment.",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
    )


def test_runtime_sanitize_projects_secondary_title_anchor_exact_reason(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Use the title anchors.",
        proposed_query_terms=["AI", "主观投资"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    sanitized = runtime._sanitize_controller_decision(
        decision=decision,
        run_state=_projection_run_state(),
        round_no=3,
    )

    assert isinstance(sanitized, SearchControllerDecision)
    assert sanitized.proposed_query_terms == ["AI", "模型部署"]


def test_runtime_sanitize_does_not_project_duplicate_terms_with_secondary_anchor(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Use the title anchors.",
        proposed_query_terms=["AI", "AI", "主观投资"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    with pytest.raises(ValueError, match="duplicates"):
        runtime._sanitize_controller_decision(
            decision=decision,
            run_state=_projection_run_state(),
            round_no=3,
        )


def test_runtime_sanitize_does_not_project_too_many_terms_with_secondary_anchor(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Use the title anchors.",
        proposed_query_terms=["AI", "主观投资", "模型部署", "检索增强"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    with pytest.raises(ValueError, match="must not exceed 3 terms"):
        runtime._sanitize_controller_decision(
            decision=decision,
            run_state=_projection_run_state(),
            round_no=3,
        )


def test_runtime_sanitize_does_not_project_missing_pool_term_with_secondary_anchor(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    decision = SearchControllerDecision(
        thought_summary="Search.",
        action="search_cts",
        decision_rationale="Use the title anchors.",
        proposed_query_terms=["AI", "主观投资", "LLMTerm"],
        proposed_filter_plan=ProposedFilterPlan(),
    )

    with pytest.raises(ValueError, match="compiled query term pool"):
        runtime._sanitize_controller_decision(
            decision=decision,
            run_state=_projection_run_state(),
            round_no=3,
        )


def test_search_once_routes_through_retrieval_service_with_provider_filters(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts")
    runtime = _workflow_runtime(settings)
    captured: dict[str, object] = {}
    runtime_constraints = [
        RuntimeConstraint(
            field="school_type_requirement",
            normalized_value=["985", "211"],
            source="jd",
            rationale="School type note",
            blocking=True,
        )
    ]

    class FakeRetrievalService:
        async def search(
            self,
            *,
            query_terms,
            query_role,
            keyword_query,
            adapter_notes,
            provider_filters,
            runtime_constraints,
            page_size,
            round_no,
            trace_id,
            fetch_mode="summary",
            cursor=None,
        ):
            captured.update(
                {
                    "query_terms": query_terms,
                    "query_role": query_role,
                    "keyword_query": keyword_query,
                    "adapter_notes": adapter_notes,
                    "provider_filters": provider_filters,
                    "runtime_constraints": runtime_constraints,
                    "page_size": page_size,
                    "round_no": round_no,
                    "trace_id": trace_id,
                    "fetch_mode": fetch_mode,
                    "cursor": cursor,
                }
            )
            return SearchResult(
                candidates=[_make_candidate("resume-1")],
                diagnostics=["provider search"],
                request_payload={"page": 2, "pageSize": 5, "schoolType": 2},
                raw_candidate_count=1,
                latency_ms=7,
            )

    runtime.retrieval_service = FakeRetrievalService()
    attempt_query = CTSQuery(
        query_role="exploit",
        query_terms=["python", "resume matching"],
        keyword_query="python resume matching",
        native_filters={"schoolType": 2},
        page=2,
        page_size=5,
        rationale="runtime seam test",
        adapter_notes=["runtime location dispatch: 上海"],
    )
    tracer = RunTracer(tmp_path / "trace-runtime-search")

    try:
        result = asyncio.run(
            runtime._search_once(
                attempt_query=attempt_query,
                runtime_constraints=runtime_constraints,
                round_no=1,
                attempt_no=2,
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert captured["query_terms"] == ["python", "resume matching"]
    assert captured["query_role"] == "primary"
    assert captured["keyword_query"] == "python resume matching"
    assert captured["adapter_notes"] == ["runtime location dispatch: 上海"]
    assert captured["provider_filters"] == {"schoolType": 2}
    assert captured["runtime_constraints"] == runtime_constraints
    assert captured["page_size"] == 5
    assert captured["round_no"] == 1
    assert captured["fetch_mode"] == "summary"
    assert captured["cursor"] == "2"
    assert isinstance(captured["trace_id"], str)
    assert captured["trace_id"].endswith("-r1-a2")
    assert result.request_payload == {"page": 2, "pageSize": 5, "schoolType": 2}
    assert result.raw_candidate_count == 1
    assert result.latency_ms == 7


def test_runtime_diagnostics_does_not_flag_compiled_short_title_anchors_as_collapsed(tmp_path: Path) -> None:
    runtime = _workflow_runtime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, provider_name="cts"))
    requirement_sheet = RequirementSheet(
        job_title="Backend Platform Engineer",
        title_anchor_terms=["Backend Engineer", "Platform Engineer"],
        title_anchor_rationale="Compiled short anchors preserve both backend and platform signals.",
        role_summary="Build backend platform services.",
        must_have_capabilities=["Python"],
        hard_constraints=HardConstraintSlots(locations=["上海"]),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="Backend",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="primary_role_anchor",
                queryability="admitted",
                family="role.backend",
            ),
            QueryTermCandidate(
                term="Platform",
                source="job_title",
                category="role_anchor",
                priority=2,
                evidence="Compiled title",
                first_added_round=0,
                retrieval_role="secondary_title_anchor",
                queryability="admitted",
                family="role.platform",
            ),
        ],
        scoring_rationale="Prefer backend platform resumes with Python signal.",
    )
    round_state = RoundState(
        round_no=1,
        controller_decision=SearchControllerDecision(
            thought_summary="Round 1 search.",
            action="search_cts",
            decision_rationale="Used both compiled title anchors.",
            proposed_query_terms=["Backend", "Platform"],
            proposed_filter_plan=ProposedFilterPlan(),
        ),
        retrieval_plan=RoundRetrievalPlan(
            plan_version=1,
            round_no=1,
            query_terms=["Backend", "Platform"],
            keyword_query="Backend Platform",
            projected_provider_filters={},
            runtime_only_constraints=[],
            location_execution_plan=LocationExecutionPlan(
                mode="single",
                allowed_locations=["上海"],
                preferred_locations=[],
                priority_order=[],
                balanced_order=["上海"],
                rotation_offset=0,
                target_new=10,
            ),
            target_new=10,
            rationale="round 1",
        ),
        search_observation=SearchObservation(
            round_no=1,
            requested_count=10,
            raw_candidate_count=0,
            unique_new_count=0,
            shortage_count=10,
            fetch_attempt_count=1,
        ),
    )
    run_state = RunState(
        input_truth=InputTruth(
            job_title="Backend Platform Engineer",
            jd="Build backend platform services.",
            notes="Prefer Python signal.",
            job_title_sha256="title-hash",
            jd_sha256="jd-hash",
            notes_sha256="notes-hash",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=[],
            exclusion_signals=[],
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(
            current_plan_version=1,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
        round_history=[round_state],
    )

    diagnostics = runtime._build_round_search_diagnostics(run_state=run_state, round_state=round_state)

    assert diagnostics["audit_labels"] == []
