import asyncio
from dataclasses import replace
from typing import Any, cast

import pytest

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.models import (
    QueryExecutionReceipt,
    RuntimeCanonicalResumeSelection,
    ScoredCandidate,
    ScoringFailure,
)
from seektalent.runtime.first_page_expansion import (
    ExpansionQueryMergeCounts,
    apply_first_page_expansion_to_receipts,
    canonical_scorecards_by_identity_id,
    decide_first_page_expansion,
    execute_first_page_decisions,
    select_qualified_first_page_expansions,
)
from seektalent.runtime.reflection_context import build_reflection_context
from seektalent.reflection.critic import render_reflection_prompt
from seektalent.source_contracts.first_page_expansion import (
    SourceFirstPageExpansionError,
    SourceFirstPageExpansionRequest,
    SourceFirstPageExpansionResult,
)
from seektalent.runtime.source_round_dispatch import RuntimeSourceInvariantError
from seektalent.runtime.source_lanes import (
    RuntimeQueryCandidateAttribution,
)
from tests.test_runtime_state_flow import (
    _make_candidate,
    _round_state_for_reflection_tests,
    _run_state_for_canonical_intake_tests,
)


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
            resume_id=f"r{i}",
            fit_bucket="fit",
            overall_score=overall,
            must_have_match_score=80,
            preferred_match_score=None,
            risk_score=None,
            risk_flags=[],
            reasoning_summary="fixture",
            evidence=[],
            confidence="high",
            matched_must_haves=[],
            missing_must_haves=[],
            matched_preferences=[],
            negative_signals=[],
            strengths=[],
            weaknesses=[],
            source_round=2,
        )
        for i, overall in enumerate((90, 79))
    ]
    decision = decide_first_page_expansion(
        continuations=[continuation],
        requested_count=2,
        baseline_opened_count=2,
        baseline_identity_count=2,
        scorecards=scores,
    )
    assert decision.reason_code == "baseline_quality_below_threshold"


def _task7_score(
    resume_id: str, overall: int = 80, must: int = 70, risk: int | None = 30, fit_bucket: str = "fit"
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket=fit_bucket,
        overall_score=overall,
        must_have_match_score=must,
        preferred_match_score=None,
        risk_score=risk,
        risk_flags=[],
        reasoning_summary="fixture",
        evidence=[],
        confidence="high",
        matched_must_haves=[],
        missing_must_haves=[],
        matched_preferences=[],
        negative_signals=[],
        strengths=[],
        weaknesses=[],
        source_round=2,
    )


def _task7_continuation(query: str = "q1", continuation: str = "c1", initial: int = 1) -> ProviderSearchContinuation:
    return ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id=continuation,
        opaque_ref=f"artifact://{continuation}",
        source_kind="liepin",
        round_no=2,
        query_instance_id=query,
        visible_candidate_count=3,
        eligible_candidate_count=3,
        initial_opened_count=initial,
    )


def _task7_receipt(query: str = "q1", requested: int = 1) -> QueryExecutionReceipt:
    return QueryExecutionReceipt(
        round_no=2,
        source_kind="liepin",
        query_instance_id=query,
        query_fingerprint=f"fp-{query}",
        term_group_key="group",
        primary_anchor_family_id="anchor",
        non_anchor_term_family_ids=[],
        query_role="exploit",
        lane_type="exploit",
        keyword_query="python",
        requested_count=requested,
        source_plan_version="1",
        status="completed",
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
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[score],
    )
    assert (decision.expand, decision.reason_code) == (expand, reason)


def test_first_page_expansion_rejects_target_and_scoring_shortfalls() -> None:
    target = decide_first_page_expansion(
        continuations=[_task7_continuation(initial=0)],
        requested_count=1,
        baseline_opened_count=0,
        baseline_identity_count=0,
        scorecards=[],
    )
    scoring = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[],
    )
    assert target.reason_code == "baseline_target_not_met"
    assert scoring.reason_code == "baseline_scoring_incomplete"


def test_first_page_selector_resolves_alias_and_groups_physical_targets() -> None:
    continuations = [_task7_continuation(), _task7_continuation(continuation="c2", initial=0)]
    decisions = select_qualified_first_page_expansions(
        continuations=continuations,
        receipts=[_task7_receipt()],
        candidate_attributions=[
            RuntimeQueryCandidateAttribution(
                source_kind="liepin", query_instance_id="q1", resume_id="alias", dedup_key="person"
            )
        ],
        candidate_identity_by_resume_id={"alias": "identity"},
        scorecards_by_identity_id={"identity": _task7_score("canonical")},
    )
    assert len(decisions) == 1
    assert decisions[0].expand is True
    assert [item.continuation_id for item in decisions[0].continuations] == ["c1", "c2"]


def test_canonical_scorecards_prefer_selected_canonical_resume() -> None:
    selection = RuntimeCanonicalResumeSelection(
        identity_id="identity",
        canonical_resume_id="r2",
    )
    result = canonical_scorecards_by_identity_id(
        scorecards_by_resume_id={"r1": _task7_score("r1", 90), "r2": _task7_score("r2", 80)},
        candidate_identity_by_resume_id={"r1": "identity", "r2": "identity"},
        canonical_resume_by_identity_id={"identity": selection},
    )
    assert result["identity"].resume_id == "r2"


def test_first_page_executor_orders_actions_and_isolates_typed_failure() -> None:
    qualified = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    rejected = replace(
        qualified, query_instance_id="q2", expand=False, continuations=(_task7_continuation("q2", "c2"),)
    )
    calls = []

    async def expand(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        calls.append((request.query_instance_id, request.action))
        if request.query_instance_id == "q1":
            raise SourceFirstPageExpansionError(
                "failed", status="failed", safe_reason_code="provider_failed", continuation_deleted=True
            )
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            first_page_visible_count=3,
            first_page_eligible_count=3,
            initial_opened_count=1,
            continuation_deleted=True,
        )

    outcomes = asyncio.run(
        execute_first_page_decisions(
            runtime_run_id="run", round_no=2, decisions=[qualified, rejected], expanders={"liepin": expand}
        )
    )
    assert calls == [("q1", "expand"), ("q2", "discard")]
    assert [item.status for item in outcomes] == ["failed", "completed"]


def test_first_page_executor_rejects_malformed_provider_result_and_missing_cleanup() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )

    async def malformed(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id="foreign",
            continuation_id=request.continuation_id,
            status="completed",
            first_page_visible_count=3,
            first_page_eligible_count=3,
            initial_opened_count=1,
        )

    with pytest.raises(RuntimeSourceInvariantError, match="wrong_provenance"):
        asyncio.run(
            execute_first_page_decisions(
                runtime_run_id="run", round_no=2, decisions=[decision], expanders={"liepin": malformed}
            )
        )


def test_first_page_executor_rejects_missing_expander() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    with pytest.raises(RuntimeSourceInvariantError, match="expander_unavailable"):
        asyncio.run(execute_first_page_decisions(runtime_run_id="run", round_no=2, decisions=[decision], expanders={}))


def test_first_page_receipt_preserves_baseline_on_failed_discard() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r", overall=79)],
    )
    receipt = _task7_receipt().model_copy(update={"raw_candidate_count": 1, "unique_candidate_count": 1})
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin",
        query_instance_id="q1",
        continuation_id="c1",
        status="failed",
        first_page_visible_count=3,
        first_page_eligible_count=3,
        initial_opened_count=1,
        safe_reason_code="discard_failed",
        continuation_deleted=False,
    )
    updated = apply_first_page_expansion_to_receipts(
        receipts=[receipt], decisions=[decision], outcomes=[outcome], merge_counts=[], scoring_failure_counts={}
    )[0]
    assert (updated.raw_candidate_count, updated.unique_candidate_count) == (1, 1)
    assert updated.first_page_expansion_status == "failed"
    assert updated.first_page_expansion_reason_code == "first_page_continuation_discard_failed"


def test_first_page_receipt_rejects_unreconciled_merge_counts() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin",
        query_instance_id="q1",
        continuation_id="c1",
        status="completed",
        first_page_visible_count=3,
        first_page_eligible_count=3,
        initial_opened_count=1,
        expansion_opened_count=1,
        continuation_deleted=True,
    )
    with pytest.raises(RuntimeSourceInvariantError, match="merge_count_exceeds_opened"):
        apply_first_page_expansion_to_receipts(
            receipts=[_task7_receipt()],
            decisions=[decision],
            outcomes=[outcome],
            merge_counts=[ExpansionQueryMergeCounts("liepin", "q1", 2, 0)],
            scoring_failure_counts={},
        )


def test_first_page_receipt_rejects_missing_duplicate_and_foreign_outcomes() -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin",
        query_instance_id="q1",
        continuation_id="c1",
        status="completed",
        first_page_visible_count=3,
        first_page_eligible_count=3,
        initial_opened_count=1,
        continuation_deleted=True,
    )
    for outcomes in ([], [outcome, outcome], [replace(outcome, query_instance_id="foreign")]):
        with pytest.raises(RuntimeSourceInvariantError):
            apply_first_page_expansion_to_receipts(
                receipts=[_task7_receipt()],
                decisions=[decision],
                outcomes=outcomes,
                merge_counts=[],
                scoring_failure_counts={},
            )


@pytest.mark.parametrize("case", ["status", "missing", "foreign", "duplicate"])
def test_first_page_executor_rejects_each_malformed_attribution_case(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    candidate = _make_candidate("r-new", source_round=2)
    good = RuntimeQueryCandidateAttribution(
        source_kind="liepin", query_instance_id="q1", resume_id="r-new", dedup_key="r-new"
    )

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
            source_kind="liepin",
            query_instance_id="q1",
            continuation_id="c1",
            status=cast(Any, status),
            candidates=(candidate,),
            candidate_query_attributions=attributions,
            first_page_visible_count=3,
            first_page_eligible_count=3,
            initial_opened_count=1,
            expansion_opened_count=1,
            continuation_deleted=True,
        )

    with pytest.raises(RuntimeSourceInvariantError):
        asyncio.run(
            execute_first_page_decisions(
                runtime_run_id="run", round_no=2, decisions=[decision], expanders={"liepin": malformed}
            )
        )


@pytest.mark.parametrize("dedup_key", ["different-person", None])
def test_first_page_executor_rejects_mismatched_attribution_dedup_key(
    dedup_key: str | None,
) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    candidate = _make_candidate("r-new", source_round=2)

    async def malformed(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        return SourceFirstPageExpansionResult(
            source_kind="liepin",
            query_instance_id="q1",
            continuation_id="c1",
            status="completed",
            candidates=(candidate,),
            candidate_query_attributions=(
                RuntimeQueryCandidateAttribution(
                    source_kind="liepin", query_instance_id="q1", resume_id="r-new", dedup_key=dedup_key
                ),
            ),
            first_page_visible_count=3,
            first_page_eligible_count=3,
            initial_opened_count=1,
            expansion_opened_count=1,
            continuation_deleted=True,
        )

    with pytest.raises(RuntimeSourceInvariantError, match="dedup_mismatch"):
        asyncio.run(
            execute_first_page_decisions(
                runtime_run_id="run", round_no=2, decisions=[decision], expanders={"liepin": malformed}
            )
        )


@pytest.mark.parametrize("case", ["cleanup", "negative_provider", "excessive_provider"])
def test_first_page_executor_rejects_each_cleanup_and_provider_counter_invariant(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )

    async def malformed(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        return SourceFirstPageExpansionResult(
            source_kind="liepin",
            query_instance_id="q1",
            continuation_id="c1",
            status="completed",
            first_page_visible_count=3,
            first_page_eligible_count=3,
            initial_opened_count=1,
            expansion_opened_count=-1 if case == "negative_provider" else 3 if case == "excessive_provider" else 0,
            continuation_deleted=case != "cleanup",
        )

    with pytest.raises(RuntimeSourceInvariantError):
        asyncio.run(
            execute_first_page_decisions(
                runtime_run_id="run", round_no=2, decisions=[decision], expanders={"liepin": malformed}
            )
        )


@pytest.mark.parametrize("case", ["negative_merge", "negative_scoring", "excessive_scoring"])
def test_first_page_receipt_rejects_each_counter_invariant(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin",
        query_instance_id="q1",
        continuation_id="c1",
        status="completed",
        first_page_visible_count=3,
        first_page_eligible_count=3,
        initial_opened_count=1,
        expansion_opened_count=1,
        continuation_deleted=True,
    )
    merge = ExpansionQueryMergeCounts("liepin", "q1", -1 if case == "negative_merge" else 1, 0)
    scoring = -1 if case == "negative_scoring" else 2 if case == "excessive_scoring" else 0
    with pytest.raises(RuntimeSourceInvariantError):
        apply_first_page_expansion_to_receipts(
            receipts=[_task7_receipt()],
            decisions=[decision],
            outcomes=[outcome],
            merge_counts=[merge],
            scoring_failure_counts={("liepin", "q1"): scoring},
        )


@pytest.mark.parametrize(
    ("counter", "value"),
    [("merge", True), ("merge", 1.5), ("scoring", False), ("scoring", "1")],
)
def test_first_page_receipt_rejects_non_integer_counter_types(counter: str, value: object) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin",
        query_instance_id="q1",
        continuation_id="c1",
        status="completed",
        first_page_visible_count=3,
        first_page_eligible_count=3,
        initial_opened_count=1,
        expansion_opened_count=1,
        continuation_deleted=True,
    )
    merge_value = cast(int, value) if counter == "merge" else 1
    scoring_value = cast(int, value) if counter == "scoring" else 0
    with pytest.raises(RuntimeSourceInvariantError, match=f"invalid_{counter}_counter"):
        apply_first_page_expansion_to_receipts(
            receipts=[_task7_receipt()],
            decisions=[decision],
            outcomes=[outcome],
            merge_counts=[ExpansionQueryMergeCounts("liepin", "q1", merge_value, 0)],
            scoring_failure_counts={("liepin", "q1"): scoring_value},
        )


@pytest.mark.parametrize("case", ["foreign_decision", "foreign_merge", "foreign_scoring", "duplicate_receipt"])
def test_first_page_receipt_rejects_each_foreign_or_duplicate_key(case: str) -> None:
    decision = decide_first_page_expansion(
        continuations=[_task7_continuation()],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    outcome = SourceFirstPageExpansionResult(
        source_kind="liepin",
        query_instance_id="q1",
        continuation_id="c1",
        status="completed",
        first_page_visible_count=3,
        first_page_eligible_count=3,
        initial_opened_count=1,
        continuation_deleted=True,
    )
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
            receipts=receipts,
            decisions=decisions,
            outcomes=[outcome],
            merge_counts=merges,
            scoring_failure_counts=scoring,
        )


def test_two_target_qualified_receipt_reconciles_exact_counts_once() -> None:
    continuations = [_task7_continuation(), _task7_continuation(continuation="c2", initial=0)]
    decision = decide_first_page_expansion(
        continuations=continuations,
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=1,
        scorecards=[_task7_score("r")],
    )
    outcomes = [
        SourceFirstPageExpansionResult(
            source_kind="liepin",
            query_instance_id="q1",
            continuation_id=item.continuation_id,
            status="completed",
            first_page_visible_count=3,
            first_page_eligible_count=3,
            initial_opened_count=item.initial_opened_count,
            expansion_opened_count=1,
            continuation_deleted=True,
        )
        for item in continuations
    ]
    updated = apply_first_page_expansion_to_receipts(
        receipts=[_task7_receipt()],
        decisions=[decision],
        outcomes=outcomes,
        merge_counts=[ExpansionQueryMergeCounts("liepin", "q1", 1, 1)],
        scoring_failure_counts={("liepin", "q1"): 1},
    )
    assert len(updated) == 1
    assert (updated[0].initial_opened_count, updated[0].expansion_opened_count) == (1, 2)
    assert (updated[0].unique_candidate_count, updated[0].duplicate_candidate_count) == (1, 1)
    assert updated[0].expansion_scoring_failure_count == 1


def test_reflection_prompt_bounds_and_sanitizes_expansion_scoring_failures() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    marker = "artifact://protected/private-resume?providerCandidateId=secret"
    round_state = _round_state_for_reflection_tests(round_no=2).model_copy(
        update={
            "scoring_failures": [
                ScoringFailure(
                    resume_id=marker,
                    branch_id="private-branch",
                    round_no=2,
                    attempts=1,
                    error_message=f"raw error {marker}",
                )
                for _ in range(9)
            ]
        }
    )
    prompt = render_reflection_prompt(build_reflection_context(run_state=run_state, round_state=round_state))
    assert "expansion_scoring_failure_count=5" in prompt
    assert marker not in prompt
    assert "private-branch" not in prompt
    assert "raw error" not in prompt
