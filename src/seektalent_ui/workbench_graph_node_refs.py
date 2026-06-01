from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from seektalent_ui.models import (
    WorkbenchGraphCandidateNodeScope,
    WorkbenchRuntimeGraphCandidateScopeResponse,
)


@dataclass(frozen=True)
class GraphNodeRef:
    node_id: str
    source_kind: Literal["cts", "liepin", "all"]
    node_kind: Literal["recall", "scoring", "final", "liepin_card", "detail_approval"]
    round_no: int | None = None
    has_candidate_index: bool = True


def parse_graph_node_ref(node_id: str) -> GraphNodeRef | None:
    recall_match = re.fullmatch(r"cts-round-(\d+)-result", node_id)
    if recall_match:
        return GraphNodeRef(node_id=node_id, source_kind="cts", node_kind="recall", round_no=int(recall_match.group(1)))
    score_match = re.fullmatch(r"cts-round-(\d+)-score", node_id)
    if score_match:
        return GraphNodeRef(node_id=node_id, source_kind="cts", node_kind="scoring", round_no=int(score_match.group(1)))
    return None


def runtime_node_ref_without_scope(node_id: str) -> GraphNodeRef | None:
    if node_id in {"job", "requirements"}:
        return GraphNodeRef(node_id=node_id, source_kind="all", node_kind="recall", has_candidate_index=False)
    if node_id in {"final-shortlist", "liepin-detail-approval"}:
        source_kind: Literal["all", "cts", "liepin"] = "liepin" if node_id == "liepin-detail-approval" else "all"
        return GraphNodeRef(node_id=node_id, source_kind=source_kind, node_kind="recall", has_candidate_index=False)
    runtime_round_match = re.fullmatch(r"round-(\d+)-(query|merge|score|feedback)", node_id)
    if runtime_round_match:
        return GraphNodeRef(
            node_id=node_id,
            source_kind="all",
            node_kind="recall",
            round_no=int(runtime_round_match.group(1)),
            has_candidate_index=False,
        )
    runtime_source_match = re.fullmatch(r"round-(\d+)-source-(cts|liepin)", node_id)
    if runtime_source_match:
        source_kind: Literal["cts", "liepin"] = "cts" if runtime_source_match.group(2) == "cts" else "liepin"
        return GraphNodeRef(
            node_id=node_id,
            source_kind=source_kind,
            node_kind="recall",
            round_no=int(runtime_source_match.group(1)),
            has_candidate_index=False,
        )
    return None


def node_ref_from_candidate_scope(
    node_id: str,
    scope: WorkbenchRuntimeGraphCandidateScopeResponse,
) -> GraphNodeRef:
    if scope.scopeKind == "round_recall":
        if scope.sourceKind == "cts":
            return GraphNodeRef(node_id=node_id, source_kind="cts", node_kind="recall", round_no=scope.roundNo)
        if scope.sourceKind == "liepin":
            return GraphNodeRef(node_id=node_id, source_kind="liepin", node_kind="liepin_card", round_no=scope.roundNo)
        raise ValueError("round_recall candidate scope requires cts or liepin sourceKind")
    if scope.scopeKind == "round_score":
        return GraphNodeRef(node_id=node_id, source_kind="all", node_kind="scoring", round_no=scope.roundNo)
    if scope.scopeKind == "final":
        return GraphNodeRef(node_id=node_id, source_kind="all", node_kind="final")
    if scope.scopeKind == "detail_approval":
        return GraphNodeRef(node_id=node_id, source_kind="liepin", node_kind="detail_approval")
    return GraphNodeRef(node_id=node_id, source_kind="all", node_kind="recall", has_candidate_index=False)


def node_scope_response(*, session_id: str, node: GraphNodeRef) -> WorkbenchGraphCandidateNodeScope:
    return WorkbenchGraphCandidateNodeScope(
        sessionId=session_id,
        source=node.source_kind,
        roundId=str(node.round_no) if node.round_no is not None else None,
        nodeKind=node.node_kind,
    )
