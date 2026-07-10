from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from hashlib import sha256

from seektalent.models import (
    LogicalQueryOutcome,
    QueryExecutionReceipt,
    QueryExecutionStatus,
    QueryTermCandidate,
)


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
