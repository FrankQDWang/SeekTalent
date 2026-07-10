from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from hashlib import sha256

from seektalent.models import (
    LogicalQueryOutcome,
    QueryExecutionReceipt,
    QueryExecutionStatus,
    QueryTermCandidate,
)
from seektalent.source_contracts.runtime_lanes import RuntimeQueryCandidateAttribution


def _term_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def build_term_group_key(
    *,
    query_terms: Sequence[str],
    query_term_pool: Sequence[QueryTermCandidate],
) -> str:
    families = {
        _term_key(item.term): _term_key(item.family)
        for item in query_term_pool
        if _term_key(item.term) and _term_key(item.family)
    }
    semantic_terms = sorted(
        {
            families.get(term_key) or f"term:{term_key}"
            for term in query_terms
            if (term_key := _term_key(term))
        }
    )
    if not semantic_terms:
        raise ValueError("term_group_key_requires_terms")
    payload = json.dumps(
        {"version": "term-group-v1", "members": semantic_terms},
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:32]


def used_term_group_keys(receipts: Sequence[QueryExecutionReceipt]) -> set[str]:
    return {receipt.term_group_key for receipt in receipts if receipt.dispatch_started}


def assert_novel_term_group_keys(
    *,
    term_group_keys: Sequence[str],
    used_term_group_keys: Collection[str],
) -> None:
    seen_keys = set(used_term_group_keys)
    for term_group_key in term_group_keys:
        if not term_group_key or term_group_key in seen_keys:
            raise ValueError("term_group_already_executed")
        seen_keys.add(term_group_key)


def _logical_identity(receipt: QueryExecutionReceipt) -> tuple[object, ...]:
    return (
        receipt.term_group_key,
        receipt.query_role,
        receipt.lane_type,
        tuple(_term_key(term) for term in receipt.query_terms),
        receipt.keyword_query,
    )


def _logical_status(statuses: set[QueryExecutionStatus]) -> QueryExecutionStatus:
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"blocked"}:
        return "blocked"
    if statuses == {"failed"}:
        return "failed"
    return "partial"


def logical_outcomes_from_receipts(
    receipts: Sequence[QueryExecutionReceipt],
) -> list[LogicalQueryOutcome]:
    grouped: dict[str, list[QueryExecutionReceipt]] = defaultdict(list)
    for receipt in receipts:
        grouped[receipt.query_instance_id].append(receipt)

    outcomes: list[LogicalQueryOutcome] = []
    for query_instance_id, members in sorted(grouped.items()):
        first = members[0]
        if any(_logical_identity(item) != _logical_identity(first) for item in members):
            raise ValueError("logical_query_receipt_identity_mismatch")
        outcomes.append(
            LogicalQueryOutcome(
                query_instance_id=query_instance_id,
                term_group_key=first.term_group_key,
                query_role=first.query_role,
                lane_type=first.lane_type,
                query_terms=list(first.query_terms),
                keyword_query=first.keyword_query,
                attempted=any(item.dispatch_started for item in members),
                status=_logical_status({item.status for item in members}),
                raw_candidate_count=sum(item.raw_candidate_count for item in members),
                unique_candidate_count=0,
                duplicate_candidate_count=0,
                receipts=members,
            )
        )
    return outcomes


def apply_post_merge_query_counts(
    *,
    outcomes: Sequence[LogicalQueryOutcome],
    candidate_attributions: Sequence[RuntimeQueryCandidateAttribution],
    candidate_identity_by_resume_id: Mapping[str, str],
    dispatch_order: Sequence[str],
    identities_seen_before_round: Collection[str],
) -> list[LogicalQueryOutcome]:
    outcome_by_query = {outcome.query_instance_id: outcome for outcome in outcomes}
    if len(dispatch_order) != len(outcome_by_query) or set(dispatch_order) != set(outcome_by_query):
        raise ValueError("query_outcome_dispatch_order_mismatch")

    attributions_by_query: dict[str, list[RuntimeQueryCandidateAttribution]] = defaultdict(list)
    for attribution in candidate_attributions:
        if attribution.query_instance_id not in outcome_by_query:
            raise ValueError("query_candidate_attribution_without_outcome")
        attributions_by_query[attribution.query_instance_id].append(attribution)

    allocated_identities = set(identities_seen_before_round)
    counted: list[LogicalQueryOutcome] = []
    for query_instance_id in dispatch_order:
        unique_count = 0
        duplicate_count = 0
        identities_in_query: set[str] = set()
        for attribution in sorted(
            attributions_by_query[query_instance_id],
            key=lambda item: (item.source_kind, item.resume_id, item.dedup_key or ""),
        ):
            identity_id = candidate_identity_by_resume_id.get(attribution.resume_id)
            if identity_id is None:
                raise ValueError("query_candidate_attribution_missing_identity")
            if identity_id in allocated_identities or identity_id in identities_in_query:
                duplicate_count += 1
                continue
            identities_in_query.add(identity_id)
            unique_count += 1
        allocated_identities.update(identities_in_query)
        counted.append(
            outcome_by_query[query_instance_id].model_copy(
                update={
                    "unique_candidate_count": unique_count,
                    "duplicate_candidate_count": duplicate_count,
                }
            )
        )
    return counted
