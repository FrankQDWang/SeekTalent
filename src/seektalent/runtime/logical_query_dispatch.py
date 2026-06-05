from __future__ import annotations

from collections.abc import Mapping, Sequence

from seektalent.models import LaneType
from seektalent.runtime.retrieval_runtime import LogicalQueryState
from seektalent.source_contracts import LogicalQueryDispatch


def build_logical_query_dispatches(
    *,
    round_no: int,
    query_states: Sequence[LogicalQueryState],
    lane_requested_counts: Mapping[LaneType, int],
    source_plan_version: str,
) -> tuple[LogicalQueryDispatch, ...]:
    dispatches: list[LogicalQueryDispatch] = []
    for query in query_states:
        if query.lane_type not in lane_requested_counts:
            raise ValueError("logical_query_dispatch_missing_requested_count")
        requested_count = int(lane_requested_counts[query.lane_type])
        if requested_count < 0:
            raise ValueError("logical_query_dispatch_negative_requested_count")
        dispatches.append(
            LogicalQueryDispatch(
                round_no=round_no,
                query_role=query.query_role,
                lane_type=query.lane_type,
                query_instance_id=query.query_instance_id,
                query_fingerprint=query.query_fingerprint,
                query_terms=tuple(query.query_terms),
                keyword_query=query.keyword_query,
                requested_count=requested_count,
                source_plan_version=source_plan_version,
            )
        )
    return tuple(dispatches)
