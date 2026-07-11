from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.models import QueryExecutionReceipt, RuntimeCanonicalResumeSelection, ScoredCandidate
from seektalent.runtime.source_expansion import (
    SourceFirstPageExpander,
    SourceFirstPageExpansionError,
    SourceFirstPageExpansionRequest,
    SourceFirstPageExpansionResult,
)
from seektalent.runtime.source_round_dispatch import RuntimeSourceInvariantError
from seektalent.source_contracts import RuntimeQueryCandidateAttribution

MIN_OVERALL_SCORE = 80
MIN_MUST_HAVE_SCORE = 70
MAX_APPLICABLE_RISK_SCORE = 30


@dataclass(frozen=True)
class FirstPageExpansionDecision:
    source_kind: str
    query_instance_id: str
    expand: bool
    reason_code: str
    continuations: tuple[ProviderSearchContinuation, ...]


@dataclass(frozen=True)
class ExpansionQueryMergeCounts:
    source_kind: str
    query_instance_id: str
    unique_candidate_count: int
    duplicate_candidate_count: int


def canonical_scorecards_by_identity_id(
    *,
    scorecards_by_resume_id: Mapping[str, ScoredCandidate],
    candidate_identity_by_resume_id: Mapping[str, str],
    canonical_resume_by_identity_id: Mapping[str, RuntimeCanonicalResumeSelection],
) -> dict[str, ScoredCandidate]:
    grouped: dict[str, list[tuple[str, ScoredCandidate]]] = {}
    for resume_id, scorecard in scorecards_by_resume_id.items():
        grouped.setdefault(candidate_identity_by_resume_id.get(resume_id, resume_id), []).append((resume_id, scorecard))
    result: dict[str, ScoredCandidate] = {}
    for identity_id, rows in grouped.items():
        preferred = getattr(canonical_resume_by_identity_id.get(identity_id), "canonical_resume_id", None)
        rows.sort(key=lambda row: (row[0] != preferred, row[0]))
        result[identity_id] = rows[0][1]
    return result


def decide_first_page_expansion(
    *,
    continuations: Sequence[ProviderSearchContinuation],
    requested_count: int,
    baseline_opened_count: int,
    baseline_identity_count: int,
    scorecards: Sequence[ScoredCandidate],
) -> FirstPageExpansionDecision:
    if not continuations:
        raise ValueError("first_page_continuation_group_empty")
    source_kind, query_instance_id = continuations[0].source_kind, continuations[0].query_instance_id
    if any((item.source_kind, item.query_instance_id) != (source_kind, query_instance_id) for item in continuations):
        raise ValueError("first_page_continuation_group_mixed_query")
    reason = "baseline_quality_gate_passed"
    if baseline_opened_count < requested_count or baseline_identity_count < requested_count:
        reason = "baseline_target_not_met"
    elif len(scorecards) != baseline_identity_count:
        reason = "baseline_scoring_incomplete"
    elif any(item.fit_bucket != "fit" for item in scorecards):
        reason = "baseline_not_fit"
    elif any(
        item.overall_score < MIN_OVERALL_SCORE or item.must_have_match_score < MIN_MUST_HAVE_SCORE
        for item in scorecards
    ):
        reason = "baseline_quality_below_threshold"
    elif any(item.risk_score is not None and item.risk_score > MAX_APPLICABLE_RISK_SCORE for item in scorecards):
        reason = "baseline_risk_above_threshold"
    return FirstPageExpansionDecision(
        source_kind, query_instance_id, reason == "baseline_quality_gate_passed", reason, tuple(continuations)
    )


def select_qualified_first_page_expansions(
    *,
    continuations: Sequence[ProviderSearchContinuation],
    receipts: Sequence[QueryExecutionReceipt],
    candidate_attributions: Sequence[RuntimeQueryCandidateAttribution],
    candidate_identity_by_resume_id: Mapping[str, str],
    scorecards_by_identity_id: Mapping[str, ScoredCandidate],
) -> list[FirstPageExpansionDecision]:
    receipts_by_key = {(r.source_kind, r.query_instance_id): r for r in receipts}
    groups: dict[tuple[str, str], list[ProviderSearchContinuation]] = {}
    seen_ids: set[str] = set()
    for item in continuations:
        if item.continuation_id in seen_ids:
            raise ValueError("duplicate_first_page_continuation")
        seen_ids.add(item.continuation_id)
        groups.setdefault((item.source_kind, item.query_instance_id), []).append(item)
    identities: dict[tuple[str, str], list[str]] = {}
    for item in candidate_attributions:
        key = (item.source_kind, item.query_instance_id)
        identity = candidate_identity_by_resume_id.get(item.resume_id, item.resume_id)
        if identity not in identities.setdefault(key, []):
            identities[key].append(identity)
    decisions = []
    for key, group in groups.items():
        receipt = receipts_by_key.get(key)
        if receipt is None:
            raise ValueError("first_page_continuation_missing_receipt")
        if receipt.status != "completed" or not receipt.dispatch_started:
            decisions.append(FirstPageExpansionDecision(*key, False, "baseline_query_not_completed", tuple(group)))
            continue
        identity_ids = identities.get(key, [])
        scores = [scorecards_by_identity_id[i] for i in identity_ids if i in scorecards_by_identity_id]
        decisions.append(
            decide_first_page_expansion(
                continuations=group,
                requested_count=receipt.requested_count,
                baseline_opened_count=sum(i.initial_opened_count for i in group),
                baseline_identity_count=len(identity_ids),
                scorecards=scores,
            )
        )
    return decisions


async def execute_first_page_decisions(
    *,
    runtime_run_id: str,
    round_no: int,
    decisions: Sequence[FirstPageExpansionDecision],
    expanders: Mapping[str, SourceFirstPageExpander],
) -> list[SourceFirstPageExpansionResult]:
    results = []
    for decision in decisions:
        expander = expanders.get(decision.source_kind)
        if expander is None:
            raise RuntimeSourceInvariantError("first_page_expander_unavailable")
        for continuation in decision.continuations:
            try:
                result = await expander(
                    SourceFirstPageExpansionRequest(
                        runtime_run_id=runtime_run_id,
                        round_no=round_no,
                        source_kind=decision.source_kind,
                        query_instance_id=decision.query_instance_id,
                        continuation_id=continuation.continuation_id,
                        continuation=continuation,
                        action="expand" if decision.expand else "discard",
                    )
                )
            except SourceFirstPageExpansionError as exc:
                result = SourceFirstPageExpansionResult(
                    source_kind=decision.source_kind,
                    query_instance_id=decision.query_instance_id,
                    continuation_id=continuation.continuation_id,
                    status=exc.status,
                    first_page_visible_count=continuation.visible_candidate_count,
                    first_page_eligible_count=continuation.eligible_candidate_count,
                    initial_opened_count=continuation.initial_opened_count,
                    safe_reason_code=exc.safe_reason_code,
                    continuation_deleted=exc.continuation_deleted,
                )
            results.append(result)
    return results


def apply_first_page_expansion_to_receipts(
    *,
    receipts: Sequence[QueryExecutionReceipt],
    decisions: Sequence[FirstPageExpansionDecision],
    outcomes: Sequence[SourceFirstPageExpansionResult],
    merge_counts: Sequence[ExpansionQueryMergeCounts],
    scoring_failure_counts: Mapping[tuple[str, str], int],
) -> list[QueryExecutionReceipt]:
    decisions_by_key = {(d.source_kind, d.query_instance_id): d for d in decisions}
    outcomes_by_key: dict[tuple[str, str], list[SourceFirstPageExpansionResult]] = {}
    for outcome in outcomes:
        outcomes_by_key.setdefault((outcome.source_kind, outcome.query_instance_id), []).append(outcome)
    merges = {(m.source_kind, m.query_instance_id): m for m in merge_counts}
    updated = []
    for receipt in receipts:
        key = (receipt.source_kind, receipt.query_instance_id)
        decision = decisions_by_key.get(key)
        if decision is None:
            updated.append(receipt)
            continue
        query_outcomes = outcomes_by_key.get(key, [])
        base = {
            "first_page_visible_count": sum(i.visible_candidate_count for i in decision.continuations),
            "first_page_eligible_count": sum(i.eligible_candidate_count for i in decision.continuations),
            "initial_opened_count": sum(i.initial_opened_count for i in decision.continuations),
            "first_page_expansion_qualified": decision.expand,
        }
        statuses = {i.status for i in query_outcomes}
        if not decision.expand:
            if len(query_outcomes) == len(decision.continuations) and statuses == {"completed"}:
                status, reason = "not_qualified", decision.reason_code
            elif "partial" in statuses or "completed" in statuses:
                status, reason = "partial", "first_page_continuation_discard_partial"
            elif statuses == {"blocked"}:
                status, reason = "blocked", "first_page_continuation_discard_blocked"
            else:
                status, reason = "failed", "first_page_continuation_discard_failed"
            updated.append(
                receipt.model_copy(
                    update=base | {"first_page_expansion_status": status, "first_page_expansion_reason_code": reason}
                )
            )
            continue
        if not query_outcomes:
            updated.append(
                receipt.model_copy(
                    update=base
                    | {
                        "first_page_expansion_status": "failed",
                        "first_page_expansion_reason_code": "first_page_expansion_result_missing",
                    }
                )
            )
            continue
        merge = merges.get(key, ExpansionQueryMergeCounts(*key, 0, 0))
        scoring = scoring_failure_counts.get(key, 0)
        opened = sum(i.expansion_opened_count for i in query_outcomes)
        skipped = sum(i.expansion_skipped_seen_count for i in query_outcomes)
        terminal = sum(i.expansion_terminal_failure_count for i in query_outcomes)
        status = (
            "completed"
            if statuses == {"completed"} and scoring == 0
            else "blocked"
            if statuses == {"blocked"} and opened == 0
            else "failed"
            if statuses <= {"failed", "blocked"} and opened == 0
            else "partial"
        )
        update = base | {
            "raw_candidate_count": receipt.raw_candidate_count + opened,
            "unique_candidate_count": receipt.unique_candidate_count + merge.unique_candidate_count,
            "duplicate_candidate_count": receipt.duplicate_candidate_count + skipped + merge.duplicate_candidate_count,
            "expansion_opened_count": opened,
            "expansion_skipped_seen_count": skipped,
            "expansion_terminal_failure_count": terminal,
            "expansion_scoring_failure_count": scoring,
            "first_page_expansion_status": status,
            "first_page_expansion_reason_code": next(
                (i.safe_reason_code for i in query_outcomes if i.safe_reason_code), decision.reason_code
            ),
        }
        # Provider outcomes are authoritative when present (including multiple targets).
        update.update(
            first_page_visible_count=sum(i.first_page_visible_count for i in query_outcomes),
            first_page_eligible_count=sum(i.first_page_eligible_count for i in query_outcomes),
            initial_opened_count=sum(i.initial_opened_count for i in query_outcomes),
        )
        updated.append(receipt.model_copy(update=update))
    return updated
