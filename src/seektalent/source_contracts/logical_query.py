from __future__ import annotations

from dataclasses import dataclass

from seektalent.models import LaneType, QueryRole


@dataclass(frozen=True)
class LogicalQueryDispatch:
    round_no: int
    query_role: QueryRole
    lane_type: LaneType
    query_instance_id: str
    query_fingerprint: str
    term_group_key: str
    primary_anchor_family_id: str
    non_anchor_term_family_ids: tuple[str, ...]
    query_terms: tuple[str, ...]
    keyword_query: str
    requested_count: int
    source_plan_version: str
