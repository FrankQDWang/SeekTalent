from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal, cast

from seektalent.config import AppSettings
from seektalent.corpus.store import CorpusStore
from seektalent.flywheel.store import FlywheelStore
from seektalent_ui.final_top_candidates import project_final_top_candidates
from seektalent_ui.models import (
    WorkbenchGraphCandidateCoverageResponse,
    WorkbenchGraphCandidateListResponse,
    WorkbenchGraphCandidateSummaryResponse,
    WorkbenchGraphRelationshipKind,
)
from seektalent_ui.resume_snapshot_helpers import (
    json_list as _json_list,
    json_object as _json_object,
    safe_snapshot_text as _safe_text,
    snapshot_materialization_allowed as _snapshot_materialization_allowed,
)
from seektalent_ui.runtime_graph import build_runtime_graph, candidate_scope_for_node_id
from seektalent_ui.workbench_graph_cursors import decode_graph_candidate_cursor, encode_graph_candidate_cursor
from seektalent_ui.workbench_graph_node_refs import (
    GraphNodeRef,
    node_ref_from_candidate_scope,
    node_scope_response,
    parse_graph_node_ref,
    runtime_node_ref_without_scope,
)
from seektalent_ui.workbench_store import (
    DEFAULT_TENANT_ID,
    WorkbenchCandidateEvidence,
    WorkbenchCandidateReviewItem,
    WorkbenchRuntimeCandidateIdentitySnapshot,
    WorkbenchStore,
    WorkbenchUser,
)


MAX_GRAPH_CANDIDATE_LIMIT = 100
DEFAULT_GRAPH_CANDIDATE_LIMIT = 50


@dataclass(frozen=True)
class ResolvedGraphCandidate:
    summary: WorkbenchGraphCandidateSummaryResponse
    snapshot_sha256: str | None


@dataclass(frozen=True)
class GraphCandidateCollection:
    candidates: list[ResolvedGraphCandidate]
    coverage: WorkbenchGraphCandidateCoverageResponse


def _runtime_graph_context(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
) -> tuple[object, Sequence[object], object | None, Sequence[object], object | None] | None:
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        return None
    events = store.list_all_session_workbench_events(user=user, session_id=session_id)
    detail_open_requests = store.list_liepin_detail_open_requests(user=user, session_id=session_id)
    runtime_final = store.list_runtime_final_top_review_items(user=user, session_id=session_id)
    if runtime_final is not None:
        _, final_review_items = runtime_final
    else:
        final_review_items = []
    final_top = SimpleNamespace(items=project_final_top_candidates(final_review_items, limit=10)) if final_review_items else None
    return session, events, None, detail_open_requests, final_top


def list_graph_candidates(
    *,
    settings: AppSettings,
    graph_secret: str,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    node_id: str,
    limit: int,
    cursor: str | None,
) -> WorkbenchGraphCandidateListResponse | None:
    context = _runtime_graph_context(store=store, user=user, session_id=session_id)
    if context is None:
        return None
    session, events, runtime_source_state, detail_open_requests, final_top = context
    scope = candidate_scope_for_node_id(
        session=session,
        events=events,
        runtime_source_state=runtime_source_state,
        detail_open_requests=detail_open_requests,
        final_top=final_top,
        node_id=node_id,
    )
    if scope is None:
        runtime_node = runtime_node_ref_without_scope(node_id)
        if runtime_node is not None:
            return _empty_graph_candidate_response(
                session_id=session_id,
                node=runtime_node,
                recovery_reason="node_has_no_candidate_scope",
            )
        node = parse_graph_node_ref(node_id)
        if node is None:
            return None
    else:
        node = node_ref_from_candidate_scope(node_id, scope)
    safe_limit = min(max(limit, 1), MAX_GRAPH_CANDIDATE_LIMIT)
    offset = decode_graph_candidate_cursor(cursor, session_id=session_id, node_id=node_id, secret=graph_secret) if cursor else 0
    if offset is None:
        return None
    if not node.has_candidate_index:
        return _empty_graph_candidate_response(
            session_id=session_id,
            node=node,
            recovery_reason="unsupported_graph_node" if scope is None else "node_has_no_candidate_scope",
        )
    collection = _all_candidates(
        settings=settings,
        graph_secret=graph_secret,
        store=store,
        user=user,
        session_id=session_id,
        node=node,
    )
    if collection is None:
        coverage = _empty_coverage()
        return WorkbenchGraphCandidateListResponse(
            nodeId=node_id,
            nodeScope=node_scope_response(session_id=session_id, node=node),
            items=[],
            nextCursor=None,
            totalSourceResults=0,
            totalGraphCandidates=0,
            totalEstimate=0,
            coverage=coverage,
            truncated=False,
            generatedAt=_now_iso(),
            recoveryState="recoverable_empty",
            recoveryReason="runtime_link_missing",
        )

    candidates = collection.candidates
    total = len(candidates)
    page = candidates[offset : offset + safe_limit]
    next_offset = offset + safe_limit
    next_cursor = None
    if next_offset < total:
        next_cursor = encode_graph_candidate_cursor(next_offset, session_id=session_id, node_id=node_id, secret=graph_secret)
    return WorkbenchGraphCandidateListResponse(
        nodeId=node_id,
        nodeScope=node_scope_response(session_id=session_id, node=node),
        items=[candidate.summary for candidate in page],
        nextCursor=next_cursor,
        totalSourceResults=len(collection.coverage.sourceResultIdsSeen),
        totalGraphCandidates=total,
        totalEstimate=total,
        coverage=collection.coverage,
        truncated=next_cursor is not None,
        generatedAt=_now_iso(),
    )


def _empty_graph_candidate_response(
    *,
    session_id: str,
    node: GraphNodeRef,
    recovery_reason: str,
) -> WorkbenchGraphCandidateListResponse:
    return WorkbenchGraphCandidateListResponse(
        nodeId=node.node_id,
        nodeScope=node_scope_response(session_id=session_id, node=node),
        items=[],
        nextCursor=None,
        totalSourceResults=0,
        totalGraphCandidates=0,
        totalEstimate=0,
        coverage=_empty_coverage(),
        truncated=False,
        generatedAt=_now_iso(),
        recoveryState="recoverable_empty",
        recoveryReason=recovery_reason,
    )


def resolve_graph_candidate(
    *,
    settings: AppSettings,
    graph_secret: str,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    graph_candidate_id: str,
    node_id: str | None = None,
) -> ResolvedGraphCandidate | None:
    if store.get_workbench_session(user=user, session_id=session_id) is None:
        return None
    for node in _candidate_node_refs(settings=settings, store=store, user=user, session_id=session_id, node_id=node_id):
        collection = _all_candidates(
            settings=settings,
            graph_secret=graph_secret,
            store=store,
            user=user,
            session_id=session_id,
            node=node,
        )
        if collection is None:
            continue
        for candidate in collection.candidates:
            if hmac.compare_digest(candidate.summary.graphCandidateId, graph_candidate_id):
                return candidate
    return None


def _all_candidates(
    *,
    settings: AppSettings,
    graph_secret: str,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    node: GraphNodeRef,
) -> GraphCandidateCollection | None:
    if node.source_kind == "all" and node.node_kind == "scoring":
        candidates = _review_backed_candidates(
            settings=settings,
            graph_secret=graph_secret,
            store=store,
            user=user,
            session_id=session_id,
            node=node,
        )
        return _candidate_collection(candidates)
    if node.source_kind == "cts" and node.node_kind in {"recall", "scoring"}:
        link = store.get_scoped_source_run_runtime_link(user=user, session_id=session_id, source_kind="cts")
        if link is not None and link.runtime_run_id and node.round_no is not None:
            return _cts_round_candidates(
                settings=settings,
                graph_secret=graph_secret,
                user=user,
                session_id=session_id,
                source_run_id=link.source_run_id,
                runtime_run_id=link.runtime_run_id,
                node=node,
            )
    if node.node_kind == "detail_approval":
        candidates = _liepin_detail_approval_candidates(
            settings=settings,
            graph_secret=graph_secret,
            store=store,
            user=user,
            session_id=session_id,
            node=node,
        )
        return _candidate_collection(candidates)
    candidates = _review_backed_candidates(
        settings=settings,
        graph_secret=graph_secret,
        store=store,
        user=user,
        session_id=session_id,
        node=node,
    )
    return _candidate_collection(candidates)


def _cts_round_candidates(
    *,
    settings: AppSettings,
    graph_secret: str,
    user: WorkbenchUser,
    session_id: str,
    source_run_id: str,
    runtime_run_id: str,
    node: GraphNodeRef,
) -> GraphCandidateCollection:
    flywheel = FlywheelStore(settings.flywheel_path)
    corpus = CorpusStore(settings.corpus_path)
    rows = flywheel.query_resume_hits_with_queries_for_run_round(run_id=runtime_run_id, round_no=node.round_no or 0)
    scoped_rows = [
        row
        for row in rows
        if node.node_kind == "recall" or row.get("scored_fit_bucket") is not None or row.get("overall_score") is not None
    ]
    scoped_rows, duplicate_dropped_count = _deduplicate_cts_round_rows(scoped_rows)
    snapshot_sha256_values = [
        str(snapshot_sha256)
        for row in scoped_rows
        if (snapshot_sha256 := row.get("snapshot_sha256"))
    ]
    docs = corpus.get_resume_documents_by_snapshot_sha256(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        snapshot_sha256_values=snapshot_sha256_values,
    )
    candidates: list[ResolvedGraphCandidate] = []
    missing_snapshots = 0
    forbidden_snapshots = 0
    missing_safe_identity = 0
    source_result_ids_seen: list[str] = []
    for row in scoped_rows:
        snapshot_sha256 = row.get("snapshot_sha256")
        source_result_ids_seen.append(
            _source_result_id(
                graph_secret,
                session_id=session_id,
                node_id=node.node_id,
                source_run_id=source_run_id,
                row=row,
            )
        )
        doc = docs.get(str(snapshot_sha256 or ""))
        can_materialize = doc is not None and _snapshot_materialization_allowed(doc)
        candidate_key = str(row["resume_id"])
        graph_id = _graph_candidate_id(
            graph_secret,
            session_id=session_id,
            node_id=node.node_id,
            source_run_id=source_run_id,
            candidate_key=candidate_key,
            snapshot_sha256=str(snapshot_sha256) if snapshot_sha256 is not None else None,
        )
        materialized_doc = doc if can_materialize else None
        sections = _json_object(materialized_doc.get("normalized_sections_json")) if materialized_doc is not None else {}
        profile = _json_object(sections.get("profile")) if can_materialize else {}
        locations = _json_list(materialized_doc.get("locations_json")) if materialized_doc is not None else []
        normalized_text = _safe_text(materialized_doc.get("normalized_text"), 700) if materialized_doc is not None else None
        score = _int_or_none(row.get("overall_score"))
        fit_bucket = _text(row.get("scored_fit_bucket"), 64)
        relationship = _relationship_for_cts(node.node_kind, row)
        summary = _safe_text(profile.get("summary"), 500) if can_materialize else None
        if not summary and normalized_text:
            summary = normalized_text
        display_name = _safe_candidate_display_name(profile.get("name")) if can_materialize else None
        title = (_safe_text(doc.get("current_title"), 160) if doc is not None and can_materialize else "") or ""
        company = (_safe_text(doc.get("current_company"), 160) if doc is not None and can_materialize else "") or ""
        location = (_safe_text(locations[0], 160) if locations and can_materialize else "") or ""
        if doc is None:
            missing_snapshots += 1
            missing_safe_identity += 1
            display_name = "简历快照未写入"
            summary = "简历摘要暂不可展示"
        elif not can_materialize:
            forbidden_snapshots += 1
            missing_safe_identity += 1
            display_name = "简历快照受限"
            summary = ""
        elif display_name is None:
            missing_safe_identity += 1
            display_name = "姓名暂不可展示"
        candidates.append(
            ResolvedGraphCandidate(
                summary=WorkbenchGraphCandidateSummaryResponse(
                    graphCandidateId=graph_id,
                    sourceKind="cts",
                    sourceRunId=source_run_id,
                    nodeKind=node.node_kind,
                    roundNo=node.round_no,
                    laneType=_text(row.get("lane_type"), 80),
                    queryRole=_text(row.get("query_role"), 80),
                    relationshipKind=relationship,
                    displayName=display_name,
                    title=title,
                    company=company,
                    location=location,
                    sourceBadges=["CTS"],
                    score=score,
                    fitBucket=fit_bucket,
                    summary=summary or "",
                    matchedMustHaves=[],
                    strengths=[],
                    missingRisks=[],
                    reviewItemId=None,
                    evidenceLevel=None,
                    detailOpenRequestId=None,
                    canExpandResume=bool(snapshot_sha256 and can_materialize),
                    canMarkPromising=False,
                    canReject=False,
                    canSaveNote=False,
                    canRequestDetail=False,
                    canOpenProvider=False,
                ),
                snapshot_sha256=str(snapshot_sha256) if snapshot_sha256 is not None else None,
            )
        )
    if node.node_kind == "scoring":
        candidates = sorted(candidates, key=lambda candidate: _cts_sort_key(candidate.summary, node.node_kind))
    return GraphCandidateCollection(
        candidates=candidates,
        coverage=WorkbenchGraphCandidateCoverageResponse(
            sourceResultIdsSeen=source_result_ids_seen,
            missingSafeIdentityCount=missing_safe_identity,
            missingSnapshotCount=missing_snapshots,
            forbiddenSnapshotCount=forbidden_snapshots,
            droppedRows=duplicate_dropped_count + len(scoped_rows) - len(candidates),
        ),
    )


def _deduplicate_cts_round_rows(rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        key = _text(row.get("resume_id"), 256) or _text(row.get("dedup_key"), 256)
        if key is None:
            key = _text(row.get("snapshot_sha256"), 256) or f"row:{len(deduped)}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped, len(rows) - len(deduped)


def _review_backed_candidates(
    *,
    settings: AppSettings,
    graph_secret: str,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    node: GraphNodeRef,
) -> list[ResolvedGraphCandidate]:
    items = _review_items_for_node(store=store, user=user, session_id=session_id, node=node)
    if items is None:
        return []
    snapshot_lookup = _review_candidate_snapshot_lookup(
        settings=settings,
        store=store,
        user=user,
        session_id=session_id,
        items=items,
    )
    candidates: list[ResolvedGraphCandidate] = []
    for item in items:
        evidence = _select_review_evidence(item.evidence, node, item_source_round=item.source_round)
        if evidence is None:
            continue
        source_run_id = evidence.source_run_id
        if evidence.source_kind == "cts":
            snapshot_sha256 = _snapshot_for_cts_review_evidence(snapshot_lookup, evidence)
        else:
            snapshot_sha256 = snapshot_lookup.get(evidence.provider_candidate_key_hash)
        graph_id = _graph_candidate_id(
            graph_secret,
            session_id=session_id,
            node_id=node.node_id,
            source_run_id=source_run_id,
            candidate_key=item.review_item_id,
            snapshot_sha256=snapshot_sha256 or evidence.resume_id,
        )
        candidates.append(
            ResolvedGraphCandidate(
                summary=WorkbenchGraphCandidateSummaryResponse(
                    graphCandidateId=graph_id,
                    sourceKind=evidence.source_kind,
                    sourceRunId=source_run_id,
                    nodeKind=node.node_kind,
                    roundNo=node.round_no,
                    laneType=None,
                    queryRole=None,
                    relationshipKind=_relationship_for_review_node(node),
                    displayName=item.display_name,
                    title=item.title,
                    company=item.company,
                    location=item.location,
                    sourceBadges=item.source_badges,
                    score=item.aggregate_score if item.aggregate_score is not None else evidence.score,
                    fitBucket=item.fit_bucket or evidence.fit_bucket,
                    summary=item.summary,
                    matchedMustHaves=item.matched_must_haves or evidence.matched_must_haves,
                    strengths=item.strengths or evidence.strengths,
                    missingRisks=item.missing_risks or evidence.missing_risks,
                    reviewItemId=item.review_item_id,
                    evidenceLevel=evidence.evidence_level,
                    detailOpenRequestId=None,
                    canExpandResume=snapshot_sha256 is not None,
                    canMarkPromising=True,
                    canReject=True,
                    canSaveNote=True,
                    canRequestDetail=evidence.source_kind == "liepin",
                    canOpenProvider=evidence.source_kind == "liepin",
                ),
                snapshot_sha256=snapshot_sha256,
            )
        )
    return sorted(candidates, key=lambda candidate: (-(candidate.summary.score or -1), candidate.summary.reviewItemId or ""))


def _review_items_for_node(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    node: GraphNodeRef,
) -> list[WorkbenchCandidateReviewItem] | None:
    items = store.list_candidate_review_items(user=user, session_id=session_id)
    if items is None:
        return None
    if node.node_kind != "final":
        return items
    runtime_final = store.list_runtime_final_top_review_items(user=user, session_id=session_id)
    if runtime_final is not None:
        _, final_items = runtime_final
        return final_items
    return []


def _snapshot_for_cts_review_evidence(
    snapshot_lookup: dict[str, str],
    evidence: WorkbenchCandidateEvidence,
) -> str | None:
    for key in (
        evidence.resume_id,
        evidence.runtime_identity_id,
        evidence.evidence_id,
        evidence.provider_candidate_key_hash,
    ):
        if key and (snapshot_sha256 := snapshot_lookup.get(key)):
            return snapshot_sha256
    return None


def _review_candidate_snapshot_lookup(
    *,
    settings: AppSettings,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    items: list[WorkbenchCandidateReviewItem],
) -> dict[str, str]:
    key_to_snapshot: dict[str, str] = {}
    corpus = CorpusStore(settings.corpus_path)

    has_cts_evidence = any(
        evidence.source_kind == "cts" and evidence.resume_id
        for item in items
        for evidence in item.evidence
    )
    if has_cts_evidence:
        link = store.get_scoped_source_run_runtime_link(user=user, session_id=session_id, source_kind="cts")
        if link is not None and link.runtime_run_id:
            flywheel = FlywheelStore(settings.flywheel_path)
            rows = flywheel.query_hits_for_run(run_id=link.runtime_run_id)
            snapshots: list[str] = []
            for row in rows:
                snapshot_sha256 = _text(row.get("snapshot_sha256"), 128)
                if snapshot_sha256 is None:
                    continue
                snapshots.append(snapshot_sha256)
                source_keys = [
                    _text(row.get("resume_id"), 256),
                    _text(row.get("dedup_key"), 256),
                    _text(row.get("source_resume_id"), 256),
                ]
                for source_key in source_keys:
                    if source_key is None:
                        continue
                    key_to_snapshot[source_key] = snapshot_sha256
                    key_to_snapshot[_workbench_candidate_id(session_id, source_key)] = snapshot_sha256

            docs = corpus.get_resume_documents_by_snapshot_sha256(
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                snapshot_sha256_values=snapshots,
            )
            allowed_snapshots = {
                snapshot_sha256
                for snapshot_sha256, doc in docs.items()
                if doc is not None and _snapshot_materialization_allowed(doc)
            }
            key_to_snapshot = {
                key: snapshot_sha256
                for key, snapshot_sha256 in key_to_snapshot.items()
                if snapshot_sha256 in allowed_snapshots
            }
            identity_snapshots = store.list_runtime_candidate_identity_snapshots(
                user=user,
                session_id=session_id,
                runtime_run_id=link.runtime_run_id,
            )
            if identity_snapshots:
                _index_corpus_resume_key_snapshots(
                    corpus=corpus,
                    user=user,
                    key_to_snapshot=key_to_snapshot,
                    identity_snapshots=identity_snapshots,
                )
                _index_runtime_identity_snapshots(
                    key_to_snapshot=key_to_snapshot,
                    identity_snapshots=identity_snapshots,
                    items=items,
                )

    liepin_provider_ids = [
        evidence.provider_candidate_key_hash
        for item in items
        for evidence in item.evidence
        if evidence.source_kind == "liepin" and evidence.provider_candidate_key_hash
    ]
    liepin_docs = corpus.get_resume_documents_by_provider_candidate_id(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        provider_name="liepin",
        provider_candidate_ids=liepin_provider_ids,
    )
    for provider_candidate_id, doc in liepin_docs.items():
        if doc is None or not _snapshot_materialization_allowed(doc):
            continue
        snapshot_sha256 = _text(doc.get("snapshot_sha256"), 128)
        if snapshot_sha256 is not None:
            key_to_snapshot[provider_candidate_id] = snapshot_sha256
    return key_to_snapshot


def _index_corpus_resume_key_snapshots(
    *,
    corpus: CorpusStore,
    user: WorkbenchUser,
    key_to_snapshot: dict[str, str],
    identity_snapshots: list[WorkbenchRuntimeCandidateIdentitySnapshot],
) -> None:
    resume_keys = [
        resume_id
        for identity in identity_snapshots
        for resume_id in (identity.canonical_resume_id, *identity.merged_resume_ids)
        if resume_id and resume_id not in key_to_snapshot
    ]
    docs = corpus.get_resume_documents_by_resume_key(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        provider_name="cts",
        resume_keys=resume_keys,
    )
    for resume_key, doc in docs.items():
        if not _snapshot_materialization_allowed(doc):
            continue
        snapshot_sha256 = _text(doc.get("snapshot_sha256"), 128)
        if snapshot_sha256 is not None:
            key_to_snapshot[resume_key] = snapshot_sha256


def _index_runtime_identity_snapshots(
    *,
    key_to_snapshot: dict[str, str],
    identity_snapshots: list[WorkbenchRuntimeCandidateIdentitySnapshot],
    items: list[WorkbenchCandidateReviewItem],
) -> None:
    identity_to_snapshot: dict[str, str] = {}
    source_evidence_to_snapshot: dict[str, str] = {}
    for identity in identity_snapshots:
        snapshot_sha256 = _snapshot_for_runtime_identity(key_to_snapshot, identity)
        if snapshot_sha256 is None:
            continue
        identity_to_snapshot[identity.identity_id] = snapshot_sha256
        key_to_snapshot[identity.identity_id] = snapshot_sha256
        for source_evidence_id in identity.source_evidence_ids:
            source_evidence_to_snapshot[source_evidence_id] = snapshot_sha256
            key_to_snapshot[source_evidence_id] = snapshot_sha256

    for item in items:
        for evidence in item.evidence:
            if evidence.source_kind != "cts":
                continue
            snapshot_sha256 = None
            if evidence.runtime_identity_id:
                snapshot_sha256 = identity_to_snapshot.get(evidence.runtime_identity_id)
            if snapshot_sha256 is None:
                snapshot_sha256 = source_evidence_to_snapshot.get(evidence.evidence_id)
            if snapshot_sha256 is None:
                continue
            for key in (
                evidence.resume_id,
                evidence.provider_candidate_key_hash,
                evidence.evidence_id,
                evidence.runtime_identity_id,
            ):
                if key:
                    key_to_snapshot[key] = snapshot_sha256


def _snapshot_for_runtime_identity(
    key_to_snapshot: dict[str, str],
    identity: WorkbenchRuntimeCandidateIdentitySnapshot,
) -> str | None:
    for resume_id in (identity.canonical_resume_id, *identity.merged_resume_ids):
        if snapshot_sha256 := key_to_snapshot.get(resume_id):
            return snapshot_sha256
    return None


def _workbench_candidate_id(session_id: str, provider_resume_id: str) -> str:
    digest = hashlib.sha256("\x1f".join(["candidate", session_id, provider_resume_id]).encode("utf-8")).hexdigest()[:24]
    return f"candidate_{digest}"


def _liepin_detail_approval_candidates(
    *,
    settings: AppSettings,
    graph_secret: str,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    node: GraphNodeRef,
) -> list[ResolvedGraphCandidate]:
    requests = store.list_liepin_detail_open_requests(user=user, session_id=session_id)
    items = store.list_candidate_review_items(user=user, session_id=session_id) or []
    items_by_id = {item.review_item_id: item for item in items}
    candidates: list[ResolvedGraphCandidate] = []
    for request in requests:
        item = items_by_id.get(request.review_item_id)
        evidence = _select_source_evidence(item.evidence, "liepin") if item is not None and item.evidence else None
        candidate = request.candidate
        source_run_id = evidence.source_run_id if evidence is not None else ""
        graph_id = _graph_candidate_id(
            graph_secret,
            session_id=session_id,
            node_id=node.node_id,
            source_run_id=source_run_id,
            candidate_key=request.request_id,
            snapshot_sha256=None,
        )
        candidates.append(
            ResolvedGraphCandidate(
                summary=WorkbenchGraphCandidateSummaryResponse(
                    graphCandidateId=graph_id,
                    sourceKind="liepin",
                    sourceRunId=source_run_id,
                    nodeKind=node.node_kind,
                    roundNo=None,
                    laneType=None,
                    queryRole=None,
                    relationshipKind="detail_requested",
                    displayName=(candidate.display_name if candidate is not None else item.display_name if item is not None else ""),
                    title=(candidate.title if candidate is not None else item.title if item is not None else ""),
                    company=(candidate.company if candidate is not None else item.company if item is not None else ""),
                    location=(candidate.location if candidate is not None else item.location if item is not None else ""),
                    sourceBadges=(candidate.source_badges if candidate is not None else item.source_badges if item is not None else ["Liepin"]),
                    score=(candidate.aggregate_score if candidate is not None else item.aggregate_score if item is not None else None),
                    fitBucket=(item.fit_bucket if item is not None else None),
                    summary=(candidate.summary if candidate is not None else item.summary if item is not None else ""),
                    matchedMustHaves=(candidate.matched_must_haves if candidate is not None else item.matched_must_haves if item is not None else []),
                    strengths=(item.strengths if item is not None else []),
                    missingRisks=(candidate.missing_risks if candidate is not None else item.missing_risks if item is not None else []),
                    reviewItemId=request.review_item_id,
                    evidenceLevel=(candidate.evidence_level if candidate is not None else item.evidence_level if item is not None else None),
                    detailOpenRequestId=request.request_id,
                    canExpandResume=False,
                    canMarkPromising=item is not None,
                    canReject=item is not None,
                    canSaveNote=item is not None,
                    canRequestDetail=False,
                    canOpenProvider=request.provider_action is not None,
                ),
                snapshot_sha256=None,
            )
        )
    return candidates


def _relationship_for_cts(node_kind: str, row: dict[str, object]) -> WorkbenchGraphRelationshipKind:
    if node_kind == "scoring":
        fit_bucket = row.get("scored_fit_bucket")
        if fit_bucket == "fit":
            return "fit"
        if fit_bucket == "not_fit":
            return "not_fit"
        return "scored"
    return "new" if row.get("was_new_to_pool") else "recalled"


def _relationship_for_review_node(node: GraphNodeRef) -> WorkbenchGraphRelationshipKind:
    if node.node_kind == "final":
        return "final"
    if node.node_kind == "scoring":
        return "scored"
    if node.node_kind == "detail_approval":
        return "detail_requested"
    return "new"


def _candidate_collection(candidates: list[ResolvedGraphCandidate]) -> GraphCandidateCollection:
    coverage = WorkbenchGraphCandidateCoverageResponse(
        sourceResultIdsSeen=[candidate.summary.graphCandidateId for candidate in candidates],
        missingSafeIdentityCount=0,
        missingSnapshotCount=0,
        forbiddenSnapshotCount=0,
        droppedRows=0,
    )
    return GraphCandidateCollection(candidates=candidates, coverage=coverage)


def _empty_coverage() -> WorkbenchGraphCandidateCoverageResponse:
    return WorkbenchGraphCandidateCoverageResponse(
        sourceResultIdsSeen=[],
        missingSafeIdentityCount=0,
        missingSnapshotCount=0,
        forbiddenSnapshotCount=0,
        droppedRows=0,
    )


def _cts_sort_key(summary: WorkbenchGraphCandidateSummaryResponse, node_kind: str) -> tuple[object, ...]:
    if node_kind == "scoring":
        fit_order = {"fit": 0, "near_fit": 1, "not_fit": 2}
        return (fit_order.get(summary.fitBucket or "", 99), -(summary.score or -1), summary.displayName)
    return (summary.roundNo or 0, summary.laneType or "", summary.displayName)


def _select_review_evidence(
    evidence: list[WorkbenchCandidateEvidence],
    node: GraphNodeRef,
    *,
    item_source_round: int | None = None,
) -> WorkbenchCandidateEvidence | None:
    if node.round_no is not None and item_source_round != node.round_no:
        return None
    if node.source_kind in {"cts", "liepin"}:
        return _select_source_evidence(evidence, cast(Literal["cts", "liepin"], node.source_kind))
    return _strongest_evidence(evidence)


def _select_source_evidence(
    evidence: list[WorkbenchCandidateEvidence],
    source_kind: Literal["cts", "liepin"],
) -> WorkbenchCandidateEvidence | None:
    return _strongest_evidence([item for item in evidence if item.source_kind == source_kind])


def _strongest_evidence(evidence: list[WorkbenchCandidateEvidence]) -> WorkbenchCandidateEvidence | None:
    if not evidence:
        return None
    level_rank = {"detail": 0, "final": 1, "card": 2}
    return sorted(
        evidence,
        key=lambda item: (
            level_rank.get(item.evidence_level, 99),
            -(item.score or -1),
            item.created_at,
            item.evidence_id,
        ),
    )[0]


def _graph_candidate_id(
    secret: str,
    *,
    session_id: str,
    node_id: str,
    source_run_id: str,
    candidate_key: str,
    snapshot_sha256: str | None,
) -> str:
    payload = json.dumps(
        {
            "session_id": session_id,
            "node_id": node_id,
            "source_run_id": source_run_id,
            "candidate_key": candidate_key,
            "snapshot_sha256": snapshot_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return "gc_" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _source_result_id(
    secret: str,
    *,
    session_id: str,
    node_id: str,
    source_run_id: str,
    row: dict[str, object],
) -> str:
    payload = json.dumps(
        {
            "session_id": session_id,
            "node_id": node_id,
            "source_run_id": source_run_id,
            "query_instance_id": row.get("query_instance_id"),
            "hit_sequence_no": row.get("hit_sequence_no"),
            "resume_id": row.get("resume_id"),
            "snapshot_sha256": row.get("snapshot_sha256"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return "sr_" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _candidate_node_refs(
    *,
    settings: AppSettings,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    node_id: str | None,
) -> list[GraphNodeRef]:
    context = _runtime_graph_context(store=store, user=user, session_id=session_id)
    if context is None:
        return []
    session, events, runtime_source_state, detail_open_requests, final_top = context
    if node_id is not None:
        scope = candidate_scope_for_node_id(
            session=session,
            events=events,
            runtime_source_state=runtime_source_state,
            detail_open_requests=detail_open_requests,
            final_top=final_top,
            node_id=node_id,
        )
        if scope is not None:
            if scope.scopeKind == "none":
                return []
            return [node_ref_from_candidate_scope(node_id, scope)]
        if runtime_node_ref_without_scope(node_id) is not None:
            return []
        parsed = parse_graph_node_ref(node_id)
        return [parsed] if parsed is not None and parsed.has_candidate_index else []
    nodes: list[GraphNodeRef] = []
    graph = build_runtime_graph(
        session=session,
        events=events,
        runtime_source_state=runtime_source_state,
        detail_open_requests=detail_open_requests,
        final_top=final_top,
    )
    seen_node_ids: set[str] = set()

    def append_node(node: GraphNodeRef) -> None:
        if node.node_id in seen_node_ids:
            return
        seen_node_ids.add(node.node_id)
        nodes.append(node)

    for graph_node in graph.nodes:
        if graph_node.candidateScope.scopeKind == "none":
            continue
        append_node(node_ref_from_candidate_scope(graph_node.nodeId, graph_node.candidateScope))

    link = store.get_scoped_source_run_runtime_link(user=user, session_id=session_id, source_kind="cts")
    if link is not None and link.runtime_run_id:
        flywheel = FlywheelStore(settings.flywheel_path)
        for round_no in flywheel.round_numbers_for_run(run_id=link.runtime_run_id):
            append_node(
                GraphNodeRef(
                    node_id=f"cts-round-{round_no}-result",
                    source_kind="cts",
                    node_kind="recall",
                    round_no=round_no,
                )
            )
            append_node(
                GraphNodeRef(
                    node_id=f"cts-round-{round_no}-score",
                    source_kind="cts",
                    node_kind="scoring",
                    round_no=round_no,
                )
            )
    return nodes


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _text(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] if text else None


def _safe_candidate_display_name(value: object) -> str | None:
    text = _safe_text(value, 160)
    if text is None:
        return None
    if _looks_like_candidate_placeholder(text):
        return None
    return text


def _looks_like_candidate_placeholder(value: str) -> bool:
    normalized = " ".join(value.strip().split())
    return bool(re.fullmatch(r"candidate\s+[-_a-f0-9]{6,}", normalized, flags=re.I))


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
