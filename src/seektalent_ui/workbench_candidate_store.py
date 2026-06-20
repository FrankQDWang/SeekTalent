from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from typing import Literal, Protocol

from seektalent.models import RequirementSheet
from seektalent_ui.workbench_candidate_display import (
    candidate_education as _candidate_education,
    candidate_experience_years as _candidate_experience_years,
    finalizer_candidate_by_resume_id as _finalizer_candidate_by_resume_id,
    liepin_card_display_fields as _liepin_card_display_fields,
    mapping_items as _mapping_items,
    mapping_payload as _mapping_payload,
    runtime_fallback_final_evidence as _runtime_fallback_final_evidence,
    safe_string_list as _safe_string_list,
    snapshot_payload as _snapshot_payload,
)
from seektalent_ui.workbench_store_helpers import (
    attr as _attr,
    first as _first,
    int_or_none as _int_or_none,
    json_list as _json_list,
    json_to_list as _json_to_list,
    mapping_get as _mapping_get,
    now_iso as _now_iso,
    object_list as _object_list,
    safe_candidate_text as _safe_candidate_text,
    safe_list as _safe_list,
    sha256_text as _sha256_text,
    stable_id as _stable_id,
)
from seektalent_ui.workbench_store_types import (
    CandidateEvidenceLevel,
    CandidateReviewStatus,
    DEFAULT_TENANT_ID,
    LIEPIN_AUTO_DETAIL_REQUEST_LIMIT,
    LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD,
    WorkbenchCandidateEvidence,
    WorkbenchCandidateReviewItem,
    WorkbenchDetailOpenCandidateSnapshot,
    WorkbenchEvent,
    WorkbenchLiepinDetailOpenJobContext,
    WorkbenchRuntimeCandidateIdentitySnapshot,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchSourceRunJob,
    WorkbenchSourceRunJobContext,
    WorkbenchSourceRunPolicy,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]


class SessionExistsForUser(Protocol):
    def __call__(self, conn: sqlite3.Connection, *, user: WorkbenchUser, session_id: str) -> bool:
        raise NotImplementedError


class AppendWorkbenchEvent(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str | None,
        source_run_id: str | None,
        source_kind: Literal["cts", "liepin"] | None,
        event_name: str,
        payload: dict[str, object],
        schema_version: str = "workbench_event_v1",
        idempotency_key: str | None = None,
        occurred_at: str | None = None,
    ) -> WorkbenchEvent:
        raise NotImplementedError


class AppendRuntimeSourceLaneEvent(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        source_run_id: str,
        source_kind: Literal["cts", "liepin"],
        event_name: str,
        schema_version: str,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> WorkbenchEvent:
        raise NotImplementedError


class PersistRuntimeFinalCandidateResults(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
        now: str,
        runtime_run_id: str | None,
        write_finalization_revision: bool = True,
        write_runtime_source_lane_events: bool = True,
        write_detail_recommendations: bool = True,
    ) -> dict[str, int]:
        raise NotImplementedError


class PersistCtsCandidateResults(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchSourceRunJobContext,
        artifacts: object,
        now: str,
    ) -> list[str]:
        raise NotImplementedError


class PersistLiepinDetailCandidateResults(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        result: object,
        now: str,
    ) -> list[str]:
        raise NotImplementedError


class DetailOpenCandidateSnapshotForReview(Protocol):
    def __call__(self, conn: sqlite3.Connection, review_item_id: str) -> WorkbenchDetailOpenCandidateSnapshot | None:
        raise NotImplementedError


class SourceRunPolicyForUser(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> WorkbenchSourceRunPolicy:
        raise NotImplementedError


class ConnectedLiepinConnectionForOwner(Protocol):
    def __call__(self, conn: sqlite3.Connection, *, workspace_id: str, user_id: str) -> sqlite3.Row | None:
        raise NotImplementedError


class CreateAutoLiepinDetailOpenRequest(Protocol):
    def __call__(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchSourceRunJobContext,
        connection_id: str,
        evidence_id: str,
        review_item_id: str,
        provider_key_hash: str,
        policy: WorkbenchSourceRunPolicy,
        decision_note: str,
        detail_candidates_json: str | None,
        now: str,
    ) -> str | None:
        raise NotImplementedError


class LeaseLiepinDetailOpenRequest(Protocol):
    def __call__(self, conn: sqlite3.Connection, *, request_id: str, now: str) -> str | None:
        raise NotImplementedError


class DetailCandidatesJson(Protocol):
    def __call__(
        self,
        *,
        candidate_id: str,
        provider_candidate_key_hash: str | None,
        value_score: int | None,
    ) -> str:
        raise NotImplementedError


class DetailCandidatesJsonFromRuntimeRecommendation(Protocol):
    def __call__(self, recommendation: Mapping[str, object]) -> str:
        raise NotImplementedError


class WorkbenchCandidateStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        append_workbench_event: AppendWorkbenchEvent,
        append_runtime_source_lane_event: AppendRuntimeSourceLaneEvent,
        session_exists_for_user: SessionExistsForUser,
        source_run_policy_for_user: SourceRunPolicyForUser,
        connected_liepin_connection_for_owner: ConnectedLiepinConnectionForOwner,
        create_auto_liepin_detail_open_request: CreateAutoLiepinDetailOpenRequest,
        lease_liepin_detail_open_request: LeaseLiepinDetailOpenRequest,
        detail_candidates_json: DetailCandidatesJson,
        detail_candidates_json_from_runtime_recommendation: DetailCandidatesJsonFromRuntimeRecommendation,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._append_workbench_event_conn = append_workbench_event
        self._append_runtime_source_lane_event_conn = append_runtime_source_lane_event
        self._session_exists_for_user_conn = session_exists_for_user
        self._source_run_policy_for_user_conn = source_run_policy_for_user
        self._connected_liepin_connection_for_owner_conn = connected_liepin_connection_for_owner
        self._create_auto_liepin_detail_open_request_conn = create_auto_liepin_detail_open_request
        self._lease_liepin_detail_open_request_conn = lease_liepin_detail_open_request
        self._detail_candidates_json = detail_candidates_json
        self._detail_candidates_json_from_runtime_recommendation = detail_candidates_json_from_runtime_recommendation

    @property
    def persist_runtime_final_candidate_results_conn(self) -> PersistRuntimeFinalCandidateResults:
        return self._persist_runtime_final_candidate_results_conn

    @property
    def persist_cts_candidate_results_conn(self) -> PersistCtsCandidateResults:
        return self._persist_cts_candidate_results_conn

    @property
    def persist_liepin_detail_candidate_results_conn(self) -> PersistLiepinDetailCandidateResults:
        return self._persist_liepin_detail_candidate_results_conn

    @property
    def detail_open_candidate_snapshot_conn(self) -> DetailOpenCandidateSnapshotForReview:
        return _detail_open_candidate_snapshot_conn

    def list_runtime_candidate_identity_snapshots(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        runtime_run_id: str,
    ) -> list[WorkbenchRuntimeCandidateIdentitySnapshot] | None:
        self._initialize()
        with self._connect() as conn:
            if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
                return None
            rows = conn.execute(
                """
                SELECT identity_id, canonical_resume_id, merged_resume_ids_json, source_evidence_ids_json
                FROM runtime_candidate_identity_snapshots
                WHERE session_id = ? AND runtime_run_id = ?
                ORDER BY created_at ASC, identity_id ASC
                """,
                (session_id, runtime_run_id),
            ).fetchall()
        return [
            WorkbenchRuntimeCandidateIdentitySnapshot(
                identity_id=row["identity_id"],
                canonical_resume_id=row["canonical_resume_id"],
                merged_resume_ids=_json_to_list(row["merged_resume_ids_json"]),
                source_evidence_ids=_json_to_list(row["source_evidence_ids_json"]),
            )
            for row in rows
        ]

    def persist_runtime_candidate_truth_from_control(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        runtime_run_id: str,
        identities: Iterable[object],
        evidence: Iterable[object],
        finalization_revision: object,
        projected_at: str,
    ) -> str:
        self._initialize()
        with self._connect() as conn, conn:
            if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
                raise ValueError("workbench_session_missing")
            source_run_by_kind = _source_run_by_kind_conn(conn, session_id=session_id)
            identity_rows: list[tuple[object, ...]] = []
            review_rows: list[tuple[object, ...]] = []
            evidence_rows: list[tuple[object, ...]] = []
            evidence_by_identity: dict[str, list[object]] = {}
            for item in evidence:
                identity_id = _safe_candidate_text(_attr(item, "identity_id"), 256)
                if identity_id:
                    evidence_by_identity.setdefault(identity_id, []).append(item)
            for identity in identities:
                identity_id = _safe_candidate_text(_attr(identity, "identity_id"), 256)
                canonical_resume_id = _safe_candidate_text(_attr(identity, "canonical_resume_id"), 256)
                if not identity_id or not canonical_resume_id:
                    continue
                source_evidence_ids = _safe_string_list(_attr(identity, "source_evidence_ids"))
                review_item_id = _stable_id("review", session_id, "identity", identity_id)
                primary_evidence_id = source_evidence_ids[0] if source_evidence_ids else _stable_id(
                    "evidence",
                    session_id,
                    identity_id,
                    "runtime-control",
                )
                identity_rows.append(
                    (
                        session_id,
                        runtime_run_id,
                        identity_id,
                        canonical_resume_id,
                        json.dumps(
                            _safe_string_list(_attr(identity, "merged_resume_ids")),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json.dumps(source_evidence_ids, ensure_ascii=False, separators=(",", ":")),
                        projected_at,
                    )
                )
                review_rows.append(
                    (
                        review_item_id,
                        DEFAULT_TENANT_ID,
                        user.workspace_id,
                        user.user_id,
                        session_id,
                        primary_evidence_id,
                        _safe_candidate_text(_attr(identity, "display_name"), 160) or f"Candidate {identity_id[-8:]}",
                        _safe_candidate_text(_attr(identity, "title"), 240) or "",
                        _safe_candidate_text(_attr(identity, "company"), 240) or "",
                        _safe_candidate_text(_attr(identity, "location"), 160) or "",
                        _candidate_education(identity),
                        _candidate_experience_years(identity),
                        _safe_candidate_text(_attr(identity, "summary"), 1000) or "",
                        _int_or_none(_attr(identity, "score")),
                        _safe_candidate_text(_attr(identity, "fit_bucket"), 64),
                        "",
                        _int_or_none(_attr(identity, "source_round")),
                        projected_at,
                        projected_at,
                    )
                )
                for evidence_item in evidence_by_identity.get(identity_id, []):
                    evidence_id = _safe_candidate_text(_attr(evidence_item, "evidence_id"), 256)
                    if not evidence_id:
                        continue
                    source_kind = _runtime_source_kind(_safe_candidate_text(_attr(evidence_item, "source_kind"), 32))
                    if source_kind is None:
                        continue
                    source_run_id = source_run_by_kind.get(source_kind) or f"{runtime_run_id}:workbench:{source_kind}"
                    evidence_rows.append(
                        (
                            evidence_id,
                            review_item_id,
                            DEFAULT_TENANT_ID,
                            user.workspace_id,
                            user.user_id,
                            session_id,
                            source_run_id,
                            source_kind,
                            _safe_candidate_text(_attr(evidence_item, "evidence_level"), 64) or "unknown",
                            _safe_candidate_text(_attr(evidence_item, "provider_candidate_key_hash"), 256) or "",
                            identity_id,
                            _safe_candidate_text(_attr(evidence_item, "resume_id"), 256) or canonical_resume_id,
                            _int_or_none(_attr(evidence_item, "score")),
                            _safe_candidate_text(_attr(evidence_item, "fit_bucket"), 64),
                            "[]",
                            "[]",
                            "[]",
                            "[]",
                            "[]",
                            projected_at,
                        )
                    )
            revision_no = _int_or_none(_attr(finalization_revision, "revision"))
            if revision_no is None:
                raise ValueError("runtime_finalization_revision_invalid")
            candidate_identity_ids = _safe_string_list(_attr(finalization_revision, "candidate_identity_ids"))
            coverage_summary = _mapping_payload(_attr(finalization_revision, "coverage_summary"))
            conn.execute(
                """
                INSERT INTO runtime_finalization_revisions (
                    session_id, runtime_run_id, revision, reason_code,
                    ordered_candidate_identity_ids_json, coverage_summary_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, runtime_run_id, revision) DO UPDATE SET
                    reason_code = excluded.reason_code,
                    ordered_candidate_identity_ids_json = excluded.ordered_candidate_identity_ids_json,
                    coverage_summary_json = excluded.coverage_summary_json,
                    created_at = excluded.created_at
                """,
                (
                    session_id,
                    runtime_run_id,
                    revision_no,
                    _safe_candidate_text(_attr(finalization_revision, "reason_code"), 128) or "runtime_finalized",
                    json.dumps(candidate_identity_ids, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(coverage_summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    projected_at,
                ),
            )
            if identity_rows:
                conn.executemany(
                    """
                    INSERT INTO runtime_candidate_identity_snapshots (
                        session_id, runtime_run_id, identity_id, canonical_resume_id,
                        merged_resume_ids_json, source_evidence_ids_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, runtime_run_id, identity_id) DO UPDATE SET
                        canonical_resume_id = excluded.canonical_resume_id,
                        merged_resume_ids_json = excluded.merged_resume_ids_json,
                        source_evidence_ids_json = excluded.source_evidence_ids_json
                    """,
                    identity_rows,
                )
            if review_rows:
                conn.executemany(
                    """
                    INSERT INTO candidate_review_items (
                        review_item_id, tenant_id, workspace_id, user_id, session_id,
                        primary_evidence_id, display_name, title, company, location, education,
                        experience_years, summary,
                        aggregate_score, fit_bucket, why_selected, source_round, review_status, note,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
                    ON CONFLICT(review_item_id) DO UPDATE SET
                        primary_evidence_id = excluded.primary_evidence_id,
                        display_name = excluded.display_name,
                        title = excluded.title,
                        company = excluded.company,
                        location = excluded.location,
                        education = excluded.education,
                        experience_years = excluded.experience_years,
                        summary = excluded.summary,
                        aggregate_score = excluded.aggregate_score,
                        fit_bucket = excluded.fit_bucket,
                        source_round = excluded.source_round,
                        updated_at = excluded.updated_at
                    """,
                    review_rows,
                )
            if evidence_rows:
                conn.executemany(
                    """
                    INSERT INTO candidate_evidence (
                        evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                        source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                        runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                        matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                        review_item_id = excluded.review_item_id,
                        source_run_id = excluded.source_run_id,
                        source_kind = excluded.source_kind,
                        evidence_level = excluded.evidence_level,
                        provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                        runtime_identity_id = excluded.runtime_identity_id,
                        resume_id = excluded.resume_id,
                        score = excluded.score,
                        fit_bucket = excluded.fit_bucket
                    """,
                    evidence_rows,
                )
            for row in review_rows:
                review_item_id = str(row[0])
                self._append_workbench_event_conn(
                    conn,
                    tenant_id=DEFAULT_TENANT_ID,
                    workspace_id=user.workspace_id,
                    user_id=user.user_id,
                    session_id=session_id,
                    source_run_id=None,
                    source_kind=None,
                    event_name="candidate_review_item_upserted",
                    payload={
                        "reviewItemId": review_item_id,
                        "runtimeRunId": runtime_run_id,
                        "candidateId": row[5],
                    },
                    idempotency_key=f"runtime-candidate-truth:{runtime_run_id}:{review_item_id}:{revision_no}",
                    occurred_at=projected_at,
                )
            return f"{session_id}:runtime-finalization:{revision_no}"


    def persist_cts_candidate_results(
        self,
        *,
        context: WorkbenchSourceRunJobContext,
        artifacts: object,
    ) -> list[WorkbenchCandidateReviewItem]:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            review_item_ids = self._persist_cts_candidate_results_conn(
                conn,
                context=context,
                artifacts=artifacts,
                now=now,
            )
        return self._list_candidate_review_items_by_ids(
            user=WorkbenchUser(
                user_id=context.session.owner_user_id,
                email="",
                display_name="",
                role="member",
                workspace_id=context.session.workspace_id,
            ),
            session_id=context.session.session_id,
            review_item_ids=review_item_ids,
        )


    def _persist_runtime_final_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        artifacts: object,
        now: str,
        runtime_run_id: str | None,
        write_finalization_revision: bool = True,
        write_runtime_source_lane_events: bool = True,
        write_detail_recommendations: bool = True,
    ) -> dict[str, int]:
        run_state = getattr(artifacts, "run_state", None)
        if run_state is None or runtime_run_id is None:
            return {}
        ordered_identity_ids = _runtime_final_identity_order_from_artifacts(artifacts)
        persist_identity_ids = _runtime_persist_identity_order_from_artifacts(
            artifacts,
            ordered_identity_ids=ordered_identity_ids,
        )
        artifacts_revision = _int_or_none(_attr(getattr(artifacts, "finalization_revision", None), "revision"))
        if artifacts_revision is not None:
            revision = artifacts_revision
        else:
            existing_revision = conn.execute(
                """
                SELECT revision
                FROM runtime_finalization_revisions
                WHERE session_id = ? AND runtime_run_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (context.session.session_id, runtime_run_id),
            ).fetchone()
            if existing_revision is not None:
                revision = int(existing_revision["revision"])
            else:
                next_revision = conn.execute(
                    """
                    SELECT COALESCE(MAX(revision), 0) + 1
                    FROM runtime_finalization_revisions
                    WHERE session_id = ?
                    """,
                    (context.session.session_id,),
                ).fetchone()[0]
                revision = int(next_revision or 1)
        if write_finalization_revision:
            conn.execute(
                """
                INSERT INTO runtime_finalization_revisions (
                    session_id, runtime_run_id, revision, reason_code,
                    ordered_candidate_identity_ids_json, coverage_summary_json, created_at
                )
                VALUES (?, ?, ?, 'runtime_finalized', ?, ?, ?)
                ON CONFLICT(session_id, runtime_run_id, revision) DO UPDATE SET
                    reason_code = excluded.reason_code,
                    ordered_candidate_identity_ids_json = excluded.ordered_candidate_identity_ids_json,
                    coverage_summary_json = excluded.coverage_summary_json,
                    created_at = excluded.created_at
                """,
                (
                    context.session.session_id,
                    runtime_run_id,
                    revision,
                    json.dumps(ordered_identity_ids, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(
                        _runtime_coverage_summary_payload(run_state),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
        source_run_by_kind: dict[str, str] = {
            source_run.source_kind: source_run.source_run_id for source_run in context.session.source_runs
        }
        source_counts: dict[str, int] = {source_run.source_run_id: 0 for source_run in context.session.source_runs}
        evidence_review_item_by_id: dict[str, str] = {}
        evidence_provider_hash_by_id: dict[str, str] = {}
        candidate_store = getattr(artifacts, "candidate_store", {}) or {}
        normalized_store = getattr(artifacts, "normalized_store", {}) or {}
        finalizer_candidate_by_resume_id = _finalizer_candidate_by_resume_id(artifacts)
        identity_snapshot_rows: list[tuple[object, ...]] = []
        review_item_rows: list[tuple[object, ...]] = []
        candidate_evidence_rows: list[tuple[object, ...]] = []
        for identity_id in persist_identity_ids:
            canonical_resume_id = _runtime_canonical_resume_id(run_state, identity_id)
            if not canonical_resume_id:
                continue
            merged_resume_ids = _runtime_merged_resume_ids(run_state, identity_id, canonical_resume_id)
            runtime_evidence = _runtime_source_evidence_for_identity(run_state, identity_id)
            source_evidence_ids = [
                evidence_id
                for evidence in runtime_evidence
                if (evidence_id := _safe_candidate_text(getattr(evidence, "evidence_id", None), 256))
            ]
            identity_snapshot_rows.append(
                (
                    context.session.session_id,
                    runtime_run_id,
                    identity_id,
                    canonical_resume_id,
                    json.dumps(merged_resume_ids, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(source_evidence_ids, ensure_ascii=False, separators=(",", ":")),
                    now,
                )
            )
            review_item_id = _stable_id("review", context.session.session_id, "identity", identity_id)
            primary_evidence_id = source_evidence_ids[0] if source_evidence_ids else _stable_id(
                "evidence",
                context.session.session_id,
                identity_id,
                "final",
            )
            raw_candidate = _mapping_get(candidate_store, canonical_resume_id)
            normalized = _mapping_get(normalized_store, canonical_resume_id)
            finalizer_candidate = finalizer_candidate_by_resume_id.get(canonical_resume_id)
            raw_payload = _attr(raw_candidate, "raw")
            display_name = (
                _safe_candidate_text(_attr(normalized, "candidate_name"), 160)
                or _safe_candidate_text(_attr(raw_payload, "candidate_name"), 160)
                or f"Candidate {review_item_id[-8:]}"
            )
            title = (
                _safe_candidate_text(_attr(normalized, "current_title"), 240)
                or _safe_candidate_text(_attr(raw_payload, "current_title"), 240)
                or _safe_candidate_text(_attr(raw_candidate, "expected_job_category"), 240)
                or ""
            )
            company = (
                _safe_candidate_text(_attr(normalized, "current_company"), 240)
                or _safe_candidate_text(_attr(raw_payload, "current_company"), 240)
                or ""
            )
            location = (
                _safe_candidate_text(_first(_attr(normalized, "locations")), 160)
                or _safe_candidate_text(_attr(raw_candidate, "now_location"), 160)
                or ""
            )
            education = _candidate_education(raw_candidate, normalized)
            experience_years = _candidate_experience_years(raw_candidate, normalized)
            score = _int_or_none(_attr(finalizer_candidate, "final_score"))
            scorecard = _mapping_get(getattr(run_state, "scorecards_by_resume_id", {}) or {}, canonical_resume_id)
            if score is None:
                score = _int_or_none(_attr(scorecard, "overall_score"))
            fit_bucket = _safe_candidate_text(_attr(finalizer_candidate, "fit_bucket"), 64) or _safe_candidate_text(
                _attr(scorecard, "fit_bucket"),
                64,
            )
            summary = (
                _safe_candidate_text(_attr(finalizer_candidate, "match_summary"), 1000)
                or _safe_candidate_text(_attr(finalizer_candidate, "why_selected"), 1000)
                or _safe_candidate_text(_attr(raw_candidate, "search_text"), 1000)
                or ""
            )
            why_selected = _safe_candidate_text(_attr(finalizer_candidate, "why_selected"), 1000) or ""
            source_round = _int_or_none(_attr(finalizer_candidate, "source_round"))
            if source_round is None:
                source_round = _int_or_none(_attr(raw_candidate, "source_round"))
            if source_round is None:
                source_round = _runtime_source_round_from_evidence_items(runtime_evidence)
            review_item_rows.append(
                (
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    primary_evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    education,
                    experience_years,
                    summary,
                    score,
                    fit_bucket,
                    why_selected,
                    source_round,
                    now,
                    now,
                )
            )
            evidence_items = runtime_evidence or [
                _runtime_fallback_final_evidence(
                    identity_id=identity_id,
                    canonical_resume_id=canonical_resume_id,
                    source_kind="cts" if "cts" in source_run_by_kind else next(iter(source_run_by_kind)),
                    evidence_id=primary_evidence_id,
                )
            ]
            for evidence in evidence_items:
                source_kind = _safe_candidate_text(getattr(evidence, "source", None), 32)
                if source_kind not in source_run_by_kind:
                    continue
                safe_source_kind = _runtime_source_kind(source_kind)
                if safe_source_kind is None:
                    continue
                source_kind = safe_source_kind
                source_run_id = source_run_by_kind[source_kind]
                source_counts[source_run_id] = source_counts.get(source_run_id, 0) + 1
                evidence_resume_id = (
                    _safe_candidate_text(getattr(evidence, "candidate_resume_id", None), 128) or canonical_resume_id
                )
                evidence_id = _safe_candidate_text(getattr(evidence, "evidence_id", None), 256) or _stable_id(
                    "evidence",
                    source_run_id,
                    identity_id,
                    source_kind,
                )
                provider_candidate_key_hash = _safe_candidate_text(
                    getattr(evidence, "provider_candidate_key_hash", None),
                    256,
                ) or _sha256_text(evidence_resume_id)
                candidate_evidence_rows.append(
                    (
                        evidence_id,
                        review_item_id,
                        DEFAULT_TENANT_ID,
                        context.session.workspace_id,
                        context.session.owner_user_id,
                        context.session.session_id,
                        source_run_id,
                        source_kind,
                        _safe_candidate_text(getattr(evidence, "evidence_level", None), 32) or "final",
                        provider_candidate_key_hash,
                        identity_id,
                        _stable_id("candidate", context.session.session_id, evidence_resume_id),
                        score,
                        fit_bucket,
                        _json_list(_safe_list(_attr(finalizer_candidate, "matched_must_haves"), 20, 240)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "matched_preferences"), 20, 240)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "risk_flags"), 12, 300)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "strengths"), 12, 300)),
                        _json_list(_safe_list(_attr(finalizer_candidate, "weaknesses"), 12, 300)),
                        now,
                    )
                )
                evidence_review_item_by_id[evidence_id] = review_item_id
                evidence_provider_hash_by_id[evidence_id] = provider_candidate_key_hash
        if identity_snapshot_rows:
            conn.executemany(
                """
                INSERT INTO runtime_candidate_identity_snapshots (
                    session_id, runtime_run_id, identity_id, canonical_resume_id,
                    merged_resume_ids_json, source_evidence_ids_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, runtime_run_id, identity_id) DO UPDATE SET
                    canonical_resume_id = excluded.canonical_resume_id,
                    merged_resume_ids_json = excluded.merged_resume_ids_json,
                    source_evidence_ids_json = excluded.source_evidence_ids_json
                """,
                identity_snapshot_rows,
            )
        if review_item_rows:
            conn.executemany(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, education,
                    experience_years, summary,
                    aggregate_score, fit_bucket, why_selected, source_round, review_status, note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
                ON CONFLICT(review_item_id) DO UPDATE SET
                    primary_evidence_id = excluded.primary_evidence_id,
                    display_name = excluded.display_name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    education = excluded.education,
                    experience_years = excluded.experience_years,
                    summary = excluded.summary,
                    aggregate_score = excluded.aggregate_score,
                    fit_bucket = excluded.fit_bucket,
                    why_selected = excluded.why_selected,
                    source_round = excluded.source_round,
                    updated_at = excluded.updated_at
                """,
                review_item_rows,
            )
        if candidate_evidence_rows:
            conn.executemany(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    source_run_id = excluded.source_run_id,
                    source_kind = excluded.source_kind,
                    evidence_level = excluded.evidence_level,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    runtime_identity_id = excluded.runtime_identity_id,
                    resume_id = excluded.resume_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    missing_risks_json = excluded.missing_risks_json,
                    strengths_json = excluded.strengths_json,
                    weaknesses_json = excluded.weaknesses_json
                """,
                candidate_evidence_rows,
            )
        if write_runtime_source_lane_events:
            self._persist_runtime_source_lane_events_conn(
                conn,
                context=context,
                run_state=run_state,
                runtime_run_id=runtime_run_id,
                revision=revision,
                ordered_identity_ids=ordered_identity_ids,
                source_run_by_kind=source_run_by_kind,
                source_counts=source_counts,
            )
        if write_detail_recommendations:
            self._persist_runtime_liepin_detail_recommendations_conn(
                conn,
                context=context,
                run_state=run_state,
                source_run_by_kind=source_run_by_kind,
                candidate_store=candidate_store,
                normalized_store=normalized_store,
                evidence_review_item_by_id=evidence_review_item_by_id,
                evidence_provider_hash_by_id=evidence_provider_hash_by_id,
                now=now,
            )
        return {source_run_id: count for source_run_id, count in source_counts.items() if count}


    def _persist_runtime_source_lane_events_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        run_state: object,
        runtime_run_id: str,
        revision: int,
        ordered_identity_ids: list[str],
        source_run_by_kind: Mapping[str, str],
        source_counts: Mapping[str, int],
    ) -> None:
        coverage_payload = _runtime_coverage_summary_payload(run_state)
        finalization_payload = {
            "revision": revision,
            "reason_code": "runtime_finalized",
            "candidate_identity_ids": ordered_identity_ids[:10],
        }
        seen_sources: set[str] = set()
        for result_payload in _runtime_source_lane_result_payloads(run_state):
            source_kind = _safe_candidate_text(result_payload.get("source"), 32)
            if source_kind not in source_run_by_kind:
                continue
            seen_sources.add(source_kind)
            events = _runtime_source_lane_events_from_result_payload(result_payload)
            for event_payload in events:
                payload = _augment_runtime_source_lane_event_payload(
                    event_payload,
                    result_payload=result_payload,
                    coverage_payload=coverage_payload,
                    finalization_payload=finalization_payload,
                    runtime_run_id=runtime_run_id,
                    source_kind=source_kind,
                )
                safe_source_kind = _runtime_source_kind(source_kind)
                if safe_source_kind is None:
                    continue
                self._append_runtime_source_lane_event_conn(
                    conn,
                    tenant_id=DEFAULT_TENANT_ID,
                    workspace_id=context.session.workspace_id,
                    user_id=context.session.owner_user_id,
                    session_id=context.session.session_id,
                    source_run_id=source_run_by_kind[source_kind],
                    source_kind=safe_source_kind,
                    event_name=_runtime_source_lane_event_name(payload),
                    schema_version=str(payload.get("schema_version") or "runtime_source_lane_event_v1"),
                    idempotency_key=_runtime_source_lane_event_idempotency_key(payload),
                    payload=payload,
                )
        for source_kind, source_run_id in source_run_by_kind.items():
            if source_kind in seen_sources:
                continue
            count = int(source_counts.get(source_run_id, 0))
            if count <= 0:
                continue
            payload: dict[str, object] = {
                "schema_version": "runtime_source_lane_event_v1",
                "runtime_run_id": runtime_run_id,
                "source_plan_id": f"{runtime_run_id}:workbench:{source_kind}",
                "source_lane_run_id": f"{runtime_run_id}:workbench:{source_kind}",
                "source": source_kind,
                "attempt": 1,
                "event_seq": 1,
                "event_type": "source_lane_completed",
                "status": "completed",
                "safe_counts": {"cards_seen": count, "candidates": count},
                "source_coverage_summary": coverage_payload,
                "finalization_revision": finalization_payload,
            }
            safe_source_kind = _runtime_source_kind(source_kind)
            if safe_source_kind is None:
                continue
            self._append_runtime_source_lane_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=source_run_id,
                source_kind=safe_source_kind,
                event_name="runtime_source_lane_completed",
                schema_version="runtime_source_lane_event_v1",
                idempotency_key=_runtime_source_lane_event_idempotency_key(payload),
                payload=payload,
            )


    def _persist_runtime_liepin_detail_recommendations_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        run_state: object,
        source_run_by_kind: Mapping[str, str],
        candidate_store: Mapping[object, object],
        normalized_store: Mapping[object, object],
        evidence_review_item_by_id: Mapping[str, str],
        evidence_provider_hash_by_id: Mapping[str, str],
        now: str,
    ) -> None:
        liepin_source_run_id = source_run_by_kind.get("liepin")
        if not liepin_source_run_id:
            return
        connection = self._connected_liepin_connection_for_owner_conn(
            conn,
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
        )
        if connection is None:
            return
        policy = self._source_run_policy_for_user_conn(
            conn,
            user=WorkbenchUser(
                user_id=context.session.owner_user_id,
                email="",
                display_name="",
                role="member",
                workspace_id=context.session.workspace_id,
            ),
            session_id=context.session.session_id,
        )
        projection_context = WorkbenchSourceRunJobContext(
            job=WorkbenchSourceRunJob(
                job_id=context.job.job_id,
                source_run_id=liepin_source_run_id,
                session_id=context.session.session_id,
                source_kind="liepin",
                status="running",
                attempt_count=context.job.attempt_count,
                error_message=None,
                created_at=context.job.created_at,
                updated_at=context.job.updated_at,
            ),
            session=context.session,
            requirement_review=context.requirement_review,
        )
        created_count = 0
        for recommendation in _runtime_detail_recommendation_payloads(run_state):
            if created_count >= LIEPIN_AUTO_DETAIL_REQUEST_LIMIT:
                return
            source_evidence_id = _safe_candidate_text(recommendation.get("source_evidence_id"), 256)
            if not source_evidence_id:
                continue
            review_item_id = evidence_review_item_by_id.get(source_evidence_id)
            provider_key_hash = (
                _safe_candidate_text(recommendation.get("provider_candidate_key_hash"), 256)
                or evidence_provider_hash_by_id.get(source_evidence_id)
            )
            if not review_item_id:
                materialized = self._ensure_runtime_liepin_recommended_card_review_item_conn(
                    conn,
                    context=context,
                    run_state=run_state,
                    source_run_id=liepin_source_run_id,
                    candidate_store=candidate_store,
                    normalized_store=normalized_store,
                    recommendation=recommendation,
                    source_evidence_id=source_evidence_id,
                    provider_key_hash=provider_key_hash,
                    now=now,
                )
                if materialized is None:
                    continue
                review_item_id, provider_key_hash = materialized
            if not provider_key_hash:
                continue
            auto_request_id = self._create_auto_liepin_detail_open_request_conn(
                conn,
                context=projection_context,
                connection_id=str(connection["connection_id"]),
                evidence_id=source_evidence_id,
                review_item_id=review_item_id,
                provider_key_hash=provider_key_hash,
                policy=policy,
                decision_note=_runtime_detail_recommendation_note(recommendation),
                detail_candidates_json=self._detail_candidates_json_from_runtime_recommendation(recommendation),
                now=now,
            )
            if auto_request_id is None:
                continue
            created_count += 1
            if policy.detail_open_mode == "bypass_confirm":
                self._lease_liepin_detail_open_request_conn(conn, request_id=auto_request_id, now=now)


    def _persist_cts_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchSourceRunJobContext,
        artifacts: object,
        now: str,
    ) -> list[str]:
        final_result = getattr(artifacts, "final_result", None)
        final_candidates = list(getattr(final_result, "candidates", []) or [])
        if not final_candidates:
            return []
        candidate_store = getattr(artifacts, "candidate_store", {}) or {}
        normalized_store = getattr(artifacts, "normalized_store", {}) or {}
        runtime_identity_by_resume_id = _runtime_identity_by_resume_id_from_artifacts(artifacts)
        review_item_ids: list[str] = []
        for candidate in final_candidates:
            provider_resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 128)
            if not provider_resume_id:
                continue
            workbench_resume_id = _stable_id("candidate", context.session.session_id, provider_resume_id)
            normalized = _mapping_get(normalized_store, provider_resume_id)
            raw_candidate = _mapping_get(candidate_store, provider_resume_id)
            review_item_id = _stable_id("review", context.session.session_id, provider_resume_id)
            evidence_id = _stable_id("evidence", context.job.source_run_id, provider_resume_id, "final")
            display_name = _safe_candidate_text(_attr(normalized, "candidate_name"), 160)
            if not display_name:
                display_name = f"Candidate {workbench_resume_id[-8:]}"
            title = _safe_candidate_text(_attr(normalized, "current_title"), 240)
            if not title:
                title = _safe_candidate_text(_attr(normalized, "headline"), 240) or ""
            company = _safe_candidate_text(_attr(normalized, "current_company"), 240) or ""
            location = _safe_candidate_text(_first(_attr(normalized, "locations")), 160) or ""
            education = _candidate_education(raw_candidate, normalized)
            experience_years = _candidate_experience_years(raw_candidate, normalized)
            why_selected = _safe_candidate_text(_attr(candidate, "why_selected"), 1000)
            summary = _safe_candidate_text(_attr(candidate, "match_summary"), 1000) or why_selected or ""
            score = _int_or_none(_attr(candidate, "final_score"))
            fit_bucket = _safe_candidate_text(_attr(candidate, "fit_bucket"), 64)
            source_round = _int_or_none(_attr(candidate, "source_round"))
            matched_must_haves = _safe_list(_attr(candidate, "matched_must_haves"), 20, 240)
            matched_preferences = _safe_list(_attr(candidate, "matched_preferences"), 20, 240)
            strengths = _safe_list(_attr(candidate, "strengths"), 12, 300)
            weaknesses = _safe_list(_attr(candidate, "weaknesses"), 12, 300)
            risk_flags = _safe_list(_attr(candidate, "risk_flags"), 12, 300)
            missing_risks = risk_flags
            provider_key_hash = _sha256_text(
                _safe_candidate_text(_attr(raw_candidate, "source_resume_id"), 256) or provider_resume_id
            )
            runtime_identity_id = runtime_identity_by_resume_id.get(provider_resume_id)
            conn.execute(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, education,
                    experience_years, summary,
                    aggregate_score, fit_bucket, why_selected, source_round, review_status, note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
                ON CONFLICT(review_item_id) DO UPDATE SET
                    primary_evidence_id = excluded.primary_evidence_id,
                    display_name = excluded.display_name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    education = excluded.education,
                    experience_years = excluded.experience_years,
                    summary = excluded.summary,
                    aggregate_score = excluded.aggregate_score,
                    fit_bucket = excluded.fit_bucket,
                    why_selected = excluded.why_selected,
                    source_round = excluded.source_round,
                    updated_at = excluded.updated_at
                """,
                (
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    education,
                    experience_years,
                    summary,
                    score,
                    fit_bucket,
                    why_selected,
                    source_round,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    runtime_identity_id = excluded.runtime_identity_id,
                    resume_id = excluded.resume_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    missing_risks_json = excluded.missing_risks_json,
                    strengths_json = excluded.strengths_json,
                    weaknesses_json = excluded.weaknesses_json
                """,
                (
                    evidence_id,
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    context.job.source_run_id,
                    context.job.source_kind,
                    provider_key_hash,
                    runtime_identity_id,
                    workbench_resume_id,
                    score,
                    fit_bucket,
                    _json_list(matched_must_haves),
                    _json_list(matched_preferences),
                    _json_list(missing_risks),
                    _json_list(strengths),
                    _json_list(weaknesses),
                    now,
                ),
            )
            self._append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.job.source_run_id,
                source_kind=context.job.source_kind,
                event_name="candidate_review_item_upserted",
                payload={
                    "reviewItemId": review_item_id,
                    "sourceRunId": context.job.source_run_id,
                    "sourceKind": context.job.source_kind,
                    "candidateId": workbench_resume_id,
                    "score": score,
                },
            )
            review_item_ids.append(review_item_id)
        return review_item_ids


    def _persist_liepin_card_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchSourceRunJobContext,
        result: object,
        now: str,
    ) -> list[str]:
        candidates = _object_list(_attr(result, "candidates"))
        if not candidates:
            candidate_updates = _attr(result, "candidate_store_updates")
            if isinstance(candidate_updates, Mapping):
                candidates = list(candidate_updates.values())
        snapshots = _object_list(_attr(result, "provider_snapshots"))
        runtime_recommendations = _object_list(_attr(result, "detail_recommendations"))
        runtime_recommendation_by_provider_resume_id = {
            _safe_candidate_text(_attr(item, "candidate_resume_id"), 128): item
            for item in runtime_recommendations
            if _safe_candidate_text(_attr(item, "candidate_resume_id"), 128)
        }
        uses_runtime_detail_recommendations = hasattr(result, "source_evidence_updates") and hasattr(
            result, "detail_recommendations"
        )
        review_item_ids: list[str] = []
        policy = self._source_run_policy_for_user_conn(
            conn,
            user=WorkbenchUser(
                user_id=context.session.owner_user_id,
                email="",
                display_name="",
                role="member",
                workspace_id=context.session.workspace_id,
            ),
            session_id=context.session.session_id,
        )
        connection = self._connected_liepin_connection_for_owner_conn(
            conn,
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
        )
        auto_detail_request_count = 0
        for index, candidate in enumerate(candidates):
            provider_resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 128)
            provider_key = (
                _safe_candidate_text(_attr(candidate, "source_resume_id"), 256)
                or _safe_candidate_text(_attr(candidate, "dedup_key"), 256)
                or provider_resume_id
            )
            if not provider_resume_id or not provider_key:
                continue
            workbench_resume_id = _stable_id("candidate", context.session.session_id, "liepin", provider_key)
            review_item_id = _stable_id("review", context.session.session_id, "liepin", provider_key)
            evidence_id = _stable_id("evidence", context.job.source_run_id, provider_key, "card")
            snapshot = snapshots[index] if index < len(snapshots) else None
            payload = _snapshot_payload(snapshot)
            display_name, title, company, location, summary = _liepin_card_display_fields(
                candidate=candidate,
                payload=payload,
                workbench_resume_id=workbench_resume_id,
            )
            education = _candidate_education(candidate, payload=payload)
            experience_years = _candidate_experience_years(candidate, payload=payload)
            card_text = " ".join([display_name, title, company, location, summary])
            sheet = _requirement_sheet_for_projection(context)
            matched_must_haves = _matched_terms(sheet.must_have_capabilities, card_text)
            matched_preferences = _matched_terms(sheet.preferred_capabilities, card_text)
            strengths = _unique_list([*matched_must_haves[:6], *matched_preferences[:6]])
            auto_score, auto_reason = _liepin_card_auto_detail_decision(
                matched_must_haves=matched_must_haves,
                matched_preferences=matched_preferences,
                title=title,
                summary=summary,
            )
            should_request_detail = (
                connection is not None
                and auto_detail_request_count < LIEPIN_AUTO_DETAIL_REQUEST_LIMIT
                and auto_score >= LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD
            )
            runtime_recommendation = runtime_recommendation_by_provider_resume_id.get(provider_resume_id)
            if uses_runtime_detail_recommendations:
                should_request_detail = (
                    connection is not None
                    and runtime_recommendation is not None
                    and auto_detail_request_count < LIEPIN_AUTO_DETAIL_REQUEST_LIMIT
                )
                if runtime_recommendation is not None:
                    recommendation_score = _int_or_none(_attr(runtime_recommendation, "value_score"))
                    auto_score = recommendation_score if recommendation_score is not None else auto_score
                    auto_reason = (
                        _safe_candidate_text(_attr(runtime_recommendation, "safe_reason"), 500)
                        or _safe_candidate_text(_attr(runtime_recommendation, "reason_code"), 500)
                        or auto_reason
                    )
            missing_risks = ["Detail page not opened yet."]
            if should_request_detail:
                missing_risks.append("Agent recommends detail review before final outreach.")
            provider_key_hash = _sha256_text(provider_key)
            runtime_identity_id = None
            conn.execute(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, education,
                    experience_years, summary,
                    aggregate_score, fit_bucket, review_status, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
                ON CONFLICT(review_item_id) DO UPDATE SET
                    primary_evidence_id = excluded.primary_evidence_id,
                    display_name = excluded.display_name,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    education = excluded.education,
                    experience_years = excluded.experience_years,
                    summary = excluded.summary,
                    aggregate_score = excluded.aggregate_score,
                    fit_bucket = excluded.fit_bucket,
                    updated_at = excluded.updated_at
                """,
                (
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    education,
                    experience_years,
                    summary,
                    auto_score,
                    "card_recommended" if should_request_detail else "card",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'card', ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    runtime_identity_id = excluded.runtime_identity_id,
                    resume_id = excluded.resume_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    missing_risks_json = excluded.missing_risks_json,
                    strengths_json = excluded.strengths_json
                """,
                (
                    evidence_id,
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    context.job.source_run_id,
                    provider_key_hash,
                    runtime_identity_id,
                    workbench_resume_id,
                    auto_score,
                    "card_recommended" if should_request_detail else "card",
                    _json_list(matched_must_haves),
                    _json_list(matched_preferences),
                    _json_list(missing_risks),
                    _json_list(strengths),
                    now,
                ),
            )
            self._append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=context.session.workspace_id,
                user_id=context.session.owner_user_id,
                session_id=context.session.session_id,
                source_run_id=context.job.source_run_id,
                source_kind="liepin",
                event_name="candidate_review_item_upserted",
                payload={
                    "reviewItemId": review_item_id,
                    "sourceRunId": context.job.source_run_id,
                    "sourceKind": "liepin",
                    "candidateId": workbench_resume_id,
                    "evidenceLevel": "card",
                    "autoDetailScore": auto_score,
                    "autoDetailRecommended": should_request_detail,
                },
            )
            if should_request_detail and connection is not None:
                auto_request_id = self._create_auto_liepin_detail_open_request_conn(
                    conn,
                    context=context,
                    connection_id=connection["connection_id"],
                    evidence_id=evidence_id,
                    review_item_id=review_item_id,
                    provider_key_hash=provider_key_hash,
                    policy=policy,
                    decision_note=auto_reason,
                    detail_candidates_json=self._detail_candidates_json(
                        candidate_id=provider_resume_id,
                        provider_candidate_key_hash=provider_key_hash,
                        value_score=auto_score,
                    ),
                    now=now,
                )
                if auto_request_id is not None:
                    auto_detail_request_count += 1
                    if policy.detail_open_mode == "bypass_confirm":
                        self._lease_liepin_detail_open_request_conn(conn, request_id=auto_request_id, now=now)
            review_item_ids.append(review_item_id)
        return review_item_ids


    def list_candidate_review_items(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        limit: int | None = None,
    ) -> list[WorkbenchCandidateReviewItem] | None:
        self._initialize()
        with self._connect() as conn:
            if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
                return None
            sql = """
                SELECT *
                FROM candidate_review_items
                WHERE workspace_id = ? AND user_id = ? AND session_id = ?
                ORDER BY COALESCE(aggregate_score, -1) DESC, created_at ASC, review_item_id ASC
                """
            params: list[object] = [user.workspace_id, user.user_id, session_id]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(sql, tuple(params)).fetchall()
            evidence_by_review = _evidence_by_review_item(conn, [row["review_item_id"] for row in rows])
        return [_review_item_from_row(row, evidence_by_review.get(row["review_item_id"], [])) for row in rows]

    def get_candidate_review_item(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
    ) -> WorkbenchCandidateReviewItem | None:
        self._initialize()
        with self._connect() as conn:
            if not self._session_exists_for_user_conn(conn, user=user, session_id=session_id):
                return None
            row = conn.execute(
                """
                SELECT *
                FROM candidate_review_items
                WHERE workspace_id = ? AND user_id = ? AND session_id = ? AND review_item_id = ?
                """,
                (user.workspace_id, user.user_id, session_id, review_item_id),
            ).fetchone()
            if row is None:
                return None
            evidence = _evidence_by_review_item(conn, [review_item_id]).get(review_item_id, [])
        return _review_item_from_row(row, evidence)


    def update_candidate_review_item(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_id: str,
        review_status: CandidateReviewStatus | None,
        note: str | None,
    ) -> WorkbenchCandidateReviewItem | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM candidate_review_items
                WHERE workspace_id = ? AND user_id = ? AND session_id = ? AND review_item_id = ?
                """,
                (user.workspace_id, user.user_id, session_id, review_item_id),
            ).fetchone()
            if row is None:
                return None
            next_status = review_status or row["review_status"]
            next_note = _safe_candidate_text(note if note is not None else row["note"], 2000) or ""
            if next_status == row["review_status"] and next_note == (row["note"] or ""):
                evidence = _evidence_by_review_item(conn, [review_item_id]).get(review_item_id, [])
                return _review_item_from_row(row, evidence)
            conn.execute(
                """
                UPDATE candidate_review_items
                SET review_status = ?, note = ?, updated_at = ?
                WHERE workspace_id = ? AND user_id = ? AND session_id = ? AND review_item_id = ?
                """,
                (next_status, next_note, now, user.workspace_id, user.user_id, session_id, review_item_id),
            )
            conn.execute(
                """
                INSERT INTO candidate_actions (
                    action_id, tenant_id, workspace_id, user_id, session_id,
                    review_item_id, action_kind, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"action_{uuid.uuid4().hex[:16]}",
                    DEFAULT_TENANT_ID,
                    user.workspace_id,
                    user.user_id,
                    session_id,
                    review_item_id,
                    next_status,
                    next_note,
                    now,
                ),
            )
            self._append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=None,
                source_kind=None,
                event_name="candidate_review_item_updated",
                payload={"reviewItemId": review_item_id, "reviewStatus": next_status},
            )
            refreshed = conn.execute(
                "SELECT * FROM candidate_review_items WHERE review_item_id = ?",
                (review_item_id,),
            ).fetchone()
            evidence = _evidence_by_review_item(conn, [review_item_id]).get(review_item_id, [])
        return _review_item_from_row(refreshed, evidence)


    def _persist_liepin_detail_candidate_results_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchLiepinDetailOpenJobContext,
        result: object,
        now: str,
    ) -> list[str]:
        evidence_updates = [
            evidence
            for evidence in _object_list(_attr(result, "source_evidence_updates"))
            if _safe_candidate_text(_attr(evidence, "source"), 32) == "liepin"
            and _safe_candidate_text(_attr(evidence, "evidence_level"), 32) == "detail"
        ]
        if not evidence_updates:
            return []
        candidate_updates = _attr(result, "candidate_store_updates")
        candidate_by_resume_id: dict[str, object] = {}
        candidate_update_items = _mapping_items(candidate_updates)
        if candidate_update_items:
            candidate_by_resume_id = {str(key): value for key, value in candidate_update_items}
            for _, candidate in candidate_update_items:
                candidate_resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 256)
                if candidate_resume_id:
                    candidate_by_resume_id[candidate_resume_id] = candidate
        existing = conn.execute(
            "SELECT * FROM candidate_review_items WHERE review_item_id = ?",
            (context.review_item_id,),
        ).fetchone()
        if existing is None:
            return []
        persisted: list[str] = []
        for index, evidence in enumerate(evidence_updates, start=1):
            evidence_resume_id = (
                _safe_candidate_text(_attr(evidence, "candidate_resume_id"), 256)
                or context.candidate_resume_id
                or context.review_item_id
            )
            candidate = candidate_by_resume_id.get(evidence_resume_id)
            raw = _attr(candidate, "raw")
            display_name = (
                _safe_candidate_text(_attr(raw, "candidate_name"), 160)
                or _safe_candidate_text(_attr(raw, "name"), 160)
                or existing["display_name"]
            )
            title = (
                _safe_candidate_text(_attr(raw, "current_title"), 240)
                or _safe_candidate_text(_attr(candidate, "expected_job_category"), 240)
                or existing["title"]
            )
            company = _safe_candidate_text(_attr(raw, "current_company"), 240) or existing["company"]
            location = _safe_candidate_text(_attr(candidate, "now_location"), 160) or existing["location"]
            education = _candidate_education(candidate) or existing["education"]
            experience_years = _candidate_experience_years(candidate)
            if experience_years is None:
                experience_years = existing["experience_years"]
            summary = _safe_candidate_text(_attr(candidate, "search_text"), 1000) or existing["summary"]
            detail_text = " ".join([display_name, title, company, location, summary])
            sheet = _requirement_sheet_for_projection(context)
            matched_must_haves = _matched_terms(sheet.must_have_capabilities, detail_text)
            matched_preferences = _matched_terms(sheet.preferred_capabilities, detail_text)
            strengths = _unique_list([*matched_must_haves[:6], *matched_preferences[:6]])
            evidence_id = (
                _safe_candidate_text(_attr(evidence, "evidence_id"), 256)
                or _stable_id("evidence", context.source_run_id, evidence_resume_id, "detail", str(index))
            )
            provider_candidate_key_hash = (
                _safe_candidate_text(_attr(evidence, "provider_candidate_key_hash"), 256)
                or context.provider_candidate_key_hash
            )
            score = _int_or_none(_attr(evidence, "score_hint")) or existing["aggregate_score"]
            fit_bucket = _safe_candidate_text(_attr(evidence, "fit_bucket"), 64) or existing["fit_bucket"] or "detail"
            conn.execute(
                """
                UPDATE candidate_review_items
                SET primary_evidence_id = ?,
                    display_name = ?,
                    title = ?,
                    company = ?,
                    location = ?,
                    education = ?,
                    experience_years = ?,
                    summary = ?,
                    aggregate_score = ?,
                    fit_bucket = ?,
                    updated_at = ?
                WHERE review_item_id = ?
                """,
                (
                    evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    education,
                    experience_years,
                    summary,
                    score,
                    fit_bucket,
                    now,
                    context.review_item_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO candidate_evidence (
                    evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                    source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                    runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                    matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'detail', ?, NULL, ?, ?, ?, ?, ?, '[]', ?, '[]', ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    review_item_id = excluded.review_item_id,
                    provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                    resume_id = excluded.resume_id,
                    score = excluded.score,
                    fit_bucket = excluded.fit_bucket,
                    matched_must_haves_json = excluded.matched_must_haves_json,
                    matched_preferences_json = excluded.matched_preferences_json,
                    strengths_json = excluded.strengths_json
                """,
                (
                    evidence_id,
                    context.review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    context.source_run_id,
                    provider_candidate_key_hash,
                    _stable_id("candidate", context.session.session_id, evidence_resume_id),
                    score,
                    fit_bucket,
                    _json_list(matched_must_haves),
                    _json_list(matched_preferences),
                    _json_list(strengths),
                    now,
                ),
            )
            persisted.append(evidence_id)
        return persisted


    def _list_candidate_review_items_by_ids(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        review_item_ids: list[str],
    ) -> list[WorkbenchCandidateReviewItem]:
        if not review_item_ids:
            return []
        self._initialize()
        placeholders = ",".join("?" for _ in review_item_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM candidate_review_items
                WHERE workspace_id = ? AND user_id = ? AND session_id = ?
                  AND review_item_id IN ({placeholders})
                ORDER BY COALESCE(aggregate_score, -1) DESC, created_at ASC, review_item_id ASC
                """,
                (user.workspace_id, user.user_id, session_id, *review_item_ids),
            ).fetchall()
            evidence_by_review = _evidence_by_review_item(conn, [row["review_item_id"] for row in rows])
        return [_review_item_from_row(row, evidence_by_review.get(row["review_item_id"], [])) for row in rows]


    def list_runtime_final_top_review_items(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
    ) -> tuple[int, list[WorkbenchCandidateReviewItem]] | None:
        self._initialize()
        with self._connect() as conn:
            revision_row = conn.execute(
                """
                SELECT *
                FROM runtime_finalization_revisions
                WHERE session_id = ?
                ORDER BY revision DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if revision_row is None:
                return None
            identity_ids = _json_to_list(revision_row["ordered_candidate_identity_ids_json"])[:10]
        review_item_ids = [_stable_id("review", session_id, "identity", identity_id) for identity_id in identity_ids]
        items = self._list_candidate_review_items_by_ids(
            user=user,
            session_id=session_id,
            review_item_ids=review_item_ids,
        )
        item_by_id = {item.review_item_id: item for item in items}
        ordered_items = [item_by_id[review_item_id] for review_item_id in review_item_ids if review_item_id in item_by_id]
        return int(revision_row["revision"]), ordered_items


    def _ensure_runtime_liepin_recommended_card_review_item_conn(
        self,
        conn: sqlite3.Connection,
        *,
        context: WorkbenchRuntimeSourcingJobContext,
        run_state: object,
        source_run_id: str,
        candidate_store: Mapping[object, object],
        normalized_store: Mapping[object, object],
        recommendation: Mapping[str, object],
        source_evidence_id: str,
        provider_key_hash: str | None,
        now: str,
    ) -> tuple[str, str] | None:
        candidate_resume_id = _safe_candidate_text(recommendation.get("candidate_resume_id"), 128)
        if not candidate_resume_id:
            return None
        safe_provider_key_hash = (
            provider_key_hash
            or _safe_candidate_text(recommendation.get("provider_candidate_key_hash"), 256)
            or _sha256_text(candidate_resume_id)
        )
        candidate = _mapping_get(candidate_store, candidate_resume_id)
        normalized = _mapping_get(normalized_store, candidate_resume_id)
        raw_payload = _attr(candidate, "raw")
        identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
        identity_id = _safe_candidate_text(_mapping_get(identity_by_resume_id, candidate_resume_id), 256) or candidate_resume_id
        review_item_id = _stable_id("review", context.session.session_id, "identity", identity_id)
        workbench_resume_id = _stable_id("candidate", context.session.session_id, candidate_resume_id)
        display_name = (
            _safe_candidate_text(_attr(normalized, "candidate_name"), 160)
            or _safe_candidate_text(_attr(raw_payload, "candidate_name"), 160)
            or _safe_candidate_text(_attr(raw_payload, "name"), 160)
            or f"Candidate {review_item_id[-8:]}"
        )
        title = (
            _safe_candidate_text(_attr(normalized, "current_title"), 240)
            or _safe_candidate_text(_attr(raw_payload, "current_title"), 240)
            or _safe_candidate_text(_attr(raw_payload, "title"), 240)
            or _safe_candidate_text(_attr(candidate, "expected_job_category"), 240)
            or "Liepin candidate card"
        )
        company = (
            _safe_candidate_text(_attr(normalized, "current_company"), 240)
            or _safe_candidate_text(_attr(raw_payload, "current_company"), 240)
            or _safe_candidate_text(_attr(raw_payload, "company"), 240)
            or ""
        )
        location = (
            _safe_candidate_text(_first(_attr(normalized, "locations")), 160)
            or _safe_candidate_text(_attr(candidate, "now_location"), 160)
            or _safe_candidate_text(_attr(raw_payload, "location"), 160)
            or _safe_candidate_text(_attr(raw_payload, "city"), 160)
            or ""
        )
        summary = (
            _safe_candidate_text(_attr(candidate, "search_text"), 1000)
            or _safe_candidate_text(_attr(raw_payload, "summary"), 1000)
            or ""
        )
        education = _candidate_education(candidate, normalized)
        experience_years = _candidate_experience_years(candidate, normalized)
        card_text = " ".join([display_name, title, company, location, summary])
        sheet = _requirement_sheet_for_projection(context)
        matched_must_haves = _matched_terms(sheet.must_have_capabilities, card_text)
        matched_preferences = _matched_terms(sheet.preferred_capabilities, card_text)
        missing_risks = ["Detail page not opened yet.", "Agent recommends detail review before final outreach."]
        strengths = _unique_list([*matched_must_haves[:6], *matched_preferences[:6]])
        score = _int_or_none(recommendation.get("value_score"))
        source_round = _runtime_source_round_for_recommendation(run_state, recommendation)
        existing = conn.execute(
            """
            SELECT 1
            FROM candidate_review_items
            WHERE review_item_id = ?
            """,
            (review_item_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO candidate_review_items (
                    review_item_id, tenant_id, workspace_id, user_id, session_id,
                    primary_evidence_id, display_name, title, company, location, education,
                    experience_years, summary,
                    aggregate_score, fit_bucket, source_round, review_status, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'card_recommended', ?, 'new', '', ?, ?)
                """,
                (
                    review_item_id,
                    DEFAULT_TENANT_ID,
                    context.session.workspace_id,
                    context.session.owner_user_id,
                    context.session.session_id,
                    source_evidence_id,
                    display_name,
                    title,
                    company,
                    location,
                    education,
                    experience_years,
                    summary,
                    score,
                    source_round,
                    now,
                    now,
                ),
            )
        elif source_round is not None:
            conn.execute(
                """
                UPDATE candidate_review_items
                SET source_round = COALESCE(source_round, ?), updated_at = ?
                WHERE review_item_id = ?
                """,
                (source_round, now, review_item_id),
            )
        conn.execute(
            """
            INSERT INTO candidate_evidence (
                evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'card', ?, ?, ?, ?, 'card_recommended', ?, ?, ?, ?, '[]', ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                review_item_id = excluded.review_item_id,
                provider_candidate_key_hash = excluded.provider_candidate_key_hash,
                runtime_identity_id = excluded.runtime_identity_id,
                resume_id = excluded.resume_id,
                score = excluded.score,
                fit_bucket = excluded.fit_bucket,
                matched_must_haves_json = excluded.matched_must_haves_json,
                matched_preferences_json = excluded.matched_preferences_json,
                missing_risks_json = excluded.missing_risks_json,
                strengths_json = excluded.strengths_json
            """,
            (
                source_evidence_id,
                review_item_id,
                DEFAULT_TENANT_ID,
                context.session.workspace_id,
                context.session.owner_user_id,
                context.session.session_id,
                source_run_id,
                safe_provider_key_hash,
                identity_id,
                workbench_resume_id,
                score,
                _json_list(matched_must_haves),
                _json_list(matched_preferences),
                _json_list(missing_risks),
                _json_list(strengths),
                now,
            ),
        )
        self._append_workbench_event_conn(
            conn,
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
            session_id=context.session.session_id,
            source_run_id=source_run_id,
            source_kind="liepin",
            event_name="candidate_review_item_upserted",
            payload={
                "reviewItemId": review_item_id,
                "sourceRunId": source_run_id,
                "sourceKind": "liepin",
                "candidateId": workbench_resume_id,
                "evidenceLevel": "card",
                "autoDetailRecommended": True,
            },
        )
        return review_item_id, safe_provider_key_hash


def _liepin_card_auto_detail_decision(
    *,
    matched_must_haves: list[str],
    matched_preferences: list[str],
    title: str,
    summary: str,
) -> tuple[int, str]:
    score = 0
    if matched_must_haves:
        score += 45 + min(len(matched_must_haves), 3) * 12
    score += min(len(matched_preferences), 4) * 8
    if title.strip():
        score += 6
    if len(summary.strip()) >= 80:
        score += 5
    score = min(score, 100)
    if score >= LIEPIN_AUTO_DETAIL_SCORE_THRESHOLD:
        reason_parts = ["Agent recommends opening detail after card review"]
        if matched_must_haves:
            reason_parts.append(f"must-have: {', '.join(matched_must_haves[:4])}")
        if matched_preferences:
            reason_parts.append(f"preference/synonym: {', '.join(matched_preferences[:4])}")
        reason_parts.append(f"card signal score: {score}")
        return score, "; ".join(reason_parts) + "."
    return score, f"Agent kept this at card level; card signal score {score} is below the detail threshold."


def _evidence_by_review_item(
    conn: sqlite3.Connection,
    review_item_ids: list[str],
) -> dict[str, list[WorkbenchCandidateEvidence]]:
    if not review_item_ids:
        return {}
    placeholders = ",".join("?" for _ in review_item_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM candidate_evidence
        WHERE review_item_id IN ({placeholders})
        ORDER BY created_at ASC, evidence_id ASC
        """,
        review_item_ids,
    ).fetchall()
    evidence_by_review: dict[str, list[WorkbenchCandidateEvidence]] = {}
    for row in rows:
        evidence_by_review.setdefault(row["review_item_id"], []).append(_candidate_evidence_from_row(row))
    return evidence_by_review


def _candidate_evidence_from_row(row: sqlite3.Row) -> WorkbenchCandidateEvidence:
    return WorkbenchCandidateEvidence(
        evidence_id=row["evidence_id"],
        review_item_id=row["review_item_id"],
        source_run_id=row["source_run_id"],
        source_kind=row["source_kind"],
        evidence_level=row["evidence_level"],
        provider_candidate_key_hash=row["provider_candidate_key_hash"],
        runtime_identity_id=row["runtime_identity_id"],
        resume_id=row["resume_id"],
        score=row["score"],
        fit_bucket=row["fit_bucket"],
        matched_must_haves=_json_to_list(row["matched_must_haves_json"]),
        matched_preferences=_json_to_list(row["matched_preferences_json"]),
        missing_risks=_json_to_list(row["missing_risks_json"]),
        strengths=_json_to_list(row["strengths_json"]),
        weaknesses=_json_to_list(row["weaknesses_json"]),
        created_at=row["created_at"],
    )


def _source_badge_for_evidence(evidence: WorkbenchCandidateEvidence) -> str:
    if evidence.source_kind == "cts":
        return "CTS final" if evidence.evidence_level == "final" else "CTS"
    if evidence.evidence_level == "detail":
        return "Liepin detail"
    return "Liepin card"


def _review_item_from_row(
    row: sqlite3.Row,
    evidence: list[WorkbenchCandidateEvidence],
) -> WorkbenchCandidateReviewItem:
    source_badges = _unique_list(_source_badge_for_evidence(item) for item in evidence)
    if len({item.source_kind for item in evidence}) > 1:
        source_badges.append("Multiple sources")
    evidence_level = _strongest_evidence_level(evidence)
    matched_must_haves = _unique_list(value for item in evidence for value in item.matched_must_haves)
    matched_preferences = _unique_list(value for item in evidence for value in item.matched_preferences)
    missing_risks = _unique_list(value for item in evidence for value in item.missing_risks)
    strengths = _unique_list(value for item in evidence for value in item.strengths)
    weaknesses = _unique_list(value for item in evidence for value in item.weaknesses)
    return WorkbenchCandidateReviewItem(
        review_item_id=row["review_item_id"],
        session_id=row["session_id"],
        status=row["review_status"],
        note=row["note"],
        display_name=row["display_name"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        education=row["education"],
        experience_years=row["experience_years"],
        summary=row["summary"],
        aggregate_score=row["aggregate_score"],
        fit_bucket=row["fit_bucket"],
        why_selected=row["why_selected"],
        source_round=row["source_round"],
        source_badges=source_badges,
        evidence_level=evidence_level,
        matched_must_haves=matched_must_haves,
        matched_preferences=matched_preferences,
        missing_risks=missing_risks,
        strengths=strengths,
        weaknesses=weaknesses,
        evidence=evidence,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _detail_open_candidate_snapshot_conn(
    conn: sqlite3.Connection,
    review_item_id: str,
) -> WorkbenchDetailOpenCandidateSnapshot | None:
    row = conn.execute(
        """
        SELECT *
        FROM candidate_review_items
        WHERE review_item_id = ?
        """,
        (review_item_id,),
    ).fetchone()
    if row is None:
        return None
    evidence = _evidence_by_review_item(conn, [review_item_id]).get(review_item_id, [])
    item = _review_item_from_row(row, evidence)
    return WorkbenchDetailOpenCandidateSnapshot(
        review_item_id=item.review_item_id,
        display_name=item.display_name,
        title=item.title,
        company=item.company,
        location=item.location,
        education=item.education,
        experience_years=item.experience_years,
        summary=item.summary,
        aggregate_score=item.aggregate_score,
        evidence_level=item.evidence_level,
        source_badges=item.source_badges,
        matched_must_haves=item.matched_must_haves,
        matched_preferences=item.matched_preferences,
        missing_risks=item.missing_risks,
    )


def _strongest_evidence_level(evidence: list[WorkbenchCandidateEvidence]) -> CandidateEvidenceLevel:
    rank = {"card": 0, "detail": 1, "final": 2}
    strongest: CandidateEvidenceLevel = "card"
    for item in evidence:
        if rank[item.evidence_level] > rank[strongest]:
            strongest = item.evidence_level
    return strongest


def _unique_list(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _runtime_identity_by_resume_id_from_artifacts(artifacts: object) -> dict[str, str]:
    run_state = getattr(artifacts, "run_state", None)
    value = getattr(run_state, "candidate_identity_by_resume_id", None)
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for resume_id, identity_id in value.items():
        safe_resume_id = _safe_candidate_text(resume_id, 128)
        safe_identity_id = _safe_candidate_text(identity_id, 256)
        if safe_resume_id and safe_identity_id:
            result[safe_resume_id] = safe_identity_id
    return result


def _runtime_final_identity_order_from_artifacts(artifacts: object) -> list[str]:
    run_state = getattr(artifacts, "run_state", None)
    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    result: list[str] = []
    for resume_id in list(getattr(run_state, "top_pool_ids", []) or []):
        safe_resume_id = _safe_candidate_text(resume_id, 128)
        if not safe_resume_id:
            continue
        identity_id = _safe_candidate_text(_mapping_get(identity_by_resume_id, safe_resume_id), 256) or safe_resume_id
        if identity_id not in result:
            result.append(identity_id)
        if len(result) >= 10:
            return result
    revision = getattr(artifacts, "finalization_revision", None)
    for identity_id in list(getattr(revision, "candidate_identity_ids", []) or []):
        safe_identity_id = _safe_candidate_text(identity_id, 256)
        if safe_identity_id and safe_identity_id not in result:
            result.append(safe_identity_id)
        if len(result) >= 10:
            break
    return result


def _runtime_persist_identity_order_from_artifacts(
    artifacts: object,
    *,
    ordered_identity_ids: list[str],
) -> list[str]:
    run_state = getattr(artifacts, "run_state", None)
    result = list(ordered_identity_ids)

    identities = getattr(run_state, "candidate_identities", {}) or {}
    if isinstance(identities, Mapping):
        for identity_id_value in identities:
            identity_id = _safe_candidate_text(identity_id_value, 256)
            if identity_id and identity_id not in result:
                result.append(identity_id)

    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    if isinstance(identity_by_resume_id, Mapping):
        for identity_id_value in identity_by_resume_id.values():
            identity_id = _safe_candidate_text(identity_id_value, 256)
            if identity_id and identity_id not in result:
                result.append(identity_id)

    evidence_by_identity = getattr(run_state, "source_evidence_by_identity_id", {}) or {}
    if isinstance(evidence_by_identity, Mapping):
        for identity_id_value in evidence_by_identity:
            identity_id = _safe_candidate_text(identity_id_value, 256)
            if identity_id and identity_id not in result:
                result.append(identity_id)

    return result


def _runtime_canonical_resume_id(run_state: object, identity_id: str) -> str | None:
    canonical_by_identity = getattr(run_state, "canonical_resume_by_identity_id", {}) or {}
    canonical = _mapping_get(canonical_by_identity, identity_id)
    resume_id = _safe_candidate_text(_attr(canonical, "canonical_resume_id"), 128)
    if resume_id:
        return resume_id
    identities = getattr(run_state, "candidate_identities", {}) or {}
    identity = _mapping_get(identities, identity_id)
    for resume_id_value in _object_list(_attr(identity, "resume_ids")):
        resume_id = _safe_candidate_text(resume_id_value, 128)
        if resume_id:
            return resume_id
    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    if isinstance(identity_by_resume_id, Mapping):
        for resume_id_value, mapped_identity_id in identity_by_resume_id.items():
            if _safe_candidate_text(mapped_identity_id, 256) == identity_id:
                return _safe_candidate_text(resume_id_value, 128)
    return None


def _runtime_merged_resume_ids(run_state: object, identity_id: str, canonical_resume_id: str) -> list[str]:
    identities = getattr(run_state, "candidate_identities", {}) or {}
    identity = _mapping_get(identities, identity_id)
    result: list[str] = []
    for resume_id_value in _object_list(_attr(identity, "resume_ids")):
        resume_id = _safe_candidate_text(resume_id_value, 128)
        if resume_id and resume_id not in result:
            result.append(resume_id)
    identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
    if isinstance(identity_by_resume_id, Mapping):
        for resume_id_value, mapped_identity_id in identity_by_resume_id.items():
            resume_id = _safe_candidate_text(resume_id_value, 128)
            if resume_id and _safe_candidate_text(mapped_identity_id, 256) == identity_id and resume_id not in result:
                result.append(resume_id)
    if canonical_resume_id not in result:
        result.insert(0, canonical_resume_id)
    return result


def _runtime_source_evidence_for_identity(run_state: object, identity_id: str) -> list[object]:
    evidence_by_identity = getattr(run_state, "source_evidence_by_identity_id", {}) or {}
    value = _mapping_get(evidence_by_identity, identity_id)
    if isinstance(value, list | tuple):
        return list(value)
    return []


def _runtime_source_evidence_for_resume(run_state: object, resume_id: str) -> list[object]:
    evidence_by_resume = getattr(run_state, "source_evidence_by_resume_id", {}) or {}
    value = _mapping_get(evidence_by_resume, resume_id)
    if isinstance(value, list | tuple):
        return list(value)
    return []


def _runtime_source_round_for_recommendation(
    run_state: object,
    recommendation: Mapping[str, object],
) -> int | None:
    source_round = _int_or_none(recommendation.get("source_round"))
    if source_round is not None:
        return source_round
    source_round = _source_round_from_lane_run_id(recommendation.get("source_lane_run_id"))
    if source_round is not None:
        return source_round

    source_evidence_id = _safe_candidate_text(recommendation.get("source_evidence_id"), 256)
    candidate_resume_id = _safe_candidate_text(recommendation.get("candidate_resume_id"), 128)
    candidates: list[object] = []
    if candidate_resume_id:
        candidates.extend(_runtime_source_evidence_for_resume(run_state, candidate_resume_id))
        identity_by_resume_id = getattr(run_state, "candidate_identity_by_resume_id", {}) or {}
        identity_id = _safe_candidate_text(_mapping_get(identity_by_resume_id, candidate_resume_id), 256)
        if identity_id:
            candidates.extend(_runtime_source_evidence_for_identity(run_state, identity_id))

    evidence_by_identity = getattr(run_state, "source_evidence_by_identity_id", {}) or {}
    if isinstance(evidence_by_identity, Mapping):
        for value in evidence_by_identity.values():
            if isinstance(value, list | tuple):
                candidates.extend(value)

    seen: set[str] = set()
    for evidence in candidates:
        evidence_id = _safe_candidate_text(_attr(evidence, "evidence_id"), 256)
        resume_id = _safe_candidate_text(_attr(evidence, "candidate_resume_id"), 128)
        if source_evidence_id and evidence_id != source_evidence_id:
            continue
        if not source_evidence_id and candidate_resume_id and resume_id != candidate_resume_id:
            continue
        key = evidence_id or f"resume:{resume_id}:{_safe_candidate_text(_attr(evidence, 'source_lane_run_id'), 256)}"
        if key in seen:
            continue
        seen.add(key)
        source_round = _source_round_from_lane_run_id(_attr(evidence, "source_lane_run_id"))
        if source_round is not None:
            return source_round
    return None


def _runtime_source_round_from_evidence_items(evidence_items: list[object]) -> int | None:
    for evidence in evidence_items:
        source_round = _source_round_from_lane_run_id(_attr(evidence, "source_lane_run_id"))
        if source_round is not None:
            return source_round
    return None


def _source_round_from_lane_run_id(value: object) -> int | None:
    text = _safe_candidate_text(value, 512)
    if not text:
        return None
    match = re.search(r"(?:^|:)round:(\d+)(?::|$)", text)
    if match is None:
        return None
    return _int_or_none(match.group(1))


def _runtime_coverage_summary_payload(run_state: object) -> dict[str, object]:
    coverage_summary = getattr(run_state, "source_coverage_summary", None)
    to_public_payload = getattr(coverage_summary, "to_public_payload", None)
    if callable(to_public_payload):
        payload = to_public_payload()
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


def _runtime_source_lane_result_payloads(run_state: object) -> list[dict[str, object]]:
    values = getattr(run_state, "runtime_source_lane_results", None)
    if values is None:
        return []
    result: list[dict[str, object]] = []
    for value in list(values or []):
        if isinstance(value, Mapping):
            result.append({str(key): item for key, item in value.items()})
            continue
        to_public_payload = getattr(value, "to_public_payload", None)
        if callable(to_public_payload):
            payload = to_public_payload()
            if isinstance(payload, Mapping):
                result.append({str(key): item for key, item in payload.items()})
    return result


def _runtime_source_lane_events_from_result_payload(result_payload: Mapping[str, object]) -> list[dict[str, object]]:
    raw_events = result_payload.get("events")
    events: list[dict[str, object]] = []
    if isinstance(raw_events, list | tuple):
        for item in raw_events:
            if isinstance(item, Mapping):
                events.append({str(key): value for key, value in item.items()})
    if events:
        return events
    source_kind = _safe_candidate_text(result_payload.get("source"), 32) or "cts"
    candidate_count = _int_or_none(result_payload.get("candidate_count")) or 0
    raw_candidate_count = _int_or_none(result_payload.get("raw_candidate_count")) or candidate_count
    detail_count = _int_or_none(result_payload.get("detail_recommendation_count")) or len(
        _object_list(result_payload.get("detail_recommendations"))
    )
    safe_counts: dict[str, int] = {"cards_seen": raw_candidate_count, "candidates": candidate_count}
    event_type = "source_lane_completed"
    if detail_count:
        safe_counts = {"detail_recommendations": detail_count}
        event_type = "detail_recommended"
    return [
        {
            "schema_version": "runtime_source_lane_event_v1",
            "runtime_run_id": result_payload.get("runtime_run_id"),
            "source_plan_id": result_payload.get("source_plan_id"),
            "source_lane_run_id": result_payload.get("source_lane_run_id"),
            "source": source_kind,
            "attempt": result_payload.get("attempt") or 1,
            "event_seq": 1,
            "event_type": event_type,
            "status": result_payload.get("status") or "completed",
            "safe_counts": safe_counts,
            "safe_reason_code": result_payload.get("stop_reason_code") or result_payload.get("blocked_reason_code"),
        }
    ]


def _augment_runtime_source_lane_event_payload(
    event_payload: Mapping[str, object],
    *,
    result_payload: Mapping[str, object],
    coverage_payload: Mapping[str, object],
    finalization_payload: Mapping[str, object],
    runtime_run_id: str,
    source_kind: str,
) -> dict[str, object]:
    payload = {str(key): value for key, value in event_payload.items()}
    payload["schema_version"] = payload.get("schema_version") or "runtime_source_lane_event_v1"
    payload["runtime_run_id"] = _safe_candidate_text(payload.get("runtime_run_id"), 256) or runtime_run_id
    payload["source_plan_id"] = _safe_candidate_text(payload.get("source_plan_id"), 256) or _safe_candidate_text(
        result_payload.get("source_plan_id"),
        256,
    ) or f"{runtime_run_id}:source:{source_kind}"
    payload["source_lane_run_id"] = _safe_candidate_text(
        payload.get("source_lane_run_id"),
        256,
    ) or _safe_candidate_text(result_payload.get("source_lane_run_id"), 256) or f"{runtime_run_id}:lane:{source_kind}"
    payload["source"] = source_kind
    payload["attempt"] = _int_or_none(payload.get("attempt")) or _int_or_none(result_payload.get("attempt")) or 1
    payload["event_seq"] = _int_or_none(payload.get("event_seq")) or 1
    payload["event_type"] = _safe_candidate_text(payload.get("event_type"), 128) or "source_lane_completed"
    payload["status"] = _safe_candidate_text(payload.get("status"), 64) or _safe_candidate_text(
        result_payload.get("status"),
        64,
    ) or "completed"
    if not isinstance(payload.get("safe_counts"), Mapping):
        candidate_count = _int_or_none(result_payload.get("candidate_count")) or 0
        raw_candidate_count = _int_or_none(result_payload.get("raw_candidate_count")) or candidate_count
        payload["safe_counts"] = {"cards_seen": raw_candidate_count, "candidates": candidate_count}
    if coverage_payload:
        payload["source_coverage_summary"] = dict(coverage_payload)
    payload["finalization_revision"] = dict(finalization_payload)
    return payload


def _runtime_source_lane_event_name(payload: Mapping[str, object]) -> str:
    event_type = _safe_candidate_text(payload.get("event_type"), 128) or "source_lane_completed"
    safe_event_type = "_".join(part for part in event_type.lower().split("_") if part)
    return f"runtime_{safe_event_type or 'source_lane_completed'}"


def _runtime_source_lane_event_idempotency_key(payload: Mapping[str, object]) -> str:
    runtime_run_id = _safe_candidate_text(payload.get("runtime_run_id"), 256) or "runtime"
    source_kind = _safe_candidate_text(payload.get("source"), 32) or "source"
    source_lane_run_id = _safe_candidate_text(payload.get("source_lane_run_id"), 256) or "lane"
    attempt = _int_or_none(payload.get("attempt")) or 0
    event_seq = _int_or_none(payload.get("event_seq")) or 0
    event_type = _safe_candidate_text(payload.get("event_type"), 128) or "event"
    return f"{runtime_run_id}:{source_kind}:{source_lane_run_id}:{attempt}:{event_seq}:{event_type}"


def _runtime_detail_recommendation_payloads(run_state: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for lane_payload in _runtime_source_lane_result_payloads(run_state):
        if _safe_candidate_text(lane_payload.get("source"), 32) != "liepin":
            continue
        for item in _object_list(lane_payload.get("detail_recommendations")):
            if isinstance(item, Mapping):
                result.append({str(key): value for key, value in item.items()})
            else:
                to_public_payload = getattr(item, "to_public_payload", None)
                if callable(to_public_payload):
                    payload = to_public_payload()
                    if isinstance(payload, Mapping):
                        result.append({str(key): value for key, value in payload.items()})
    return result


def _runtime_detail_recommendation_note(recommendation: Mapping[str, object]) -> str:
    score = _int_or_none(recommendation.get("value_score"))
    reason_codes: list[str] = []
    for value in _object_list(recommendation.get("safe_reason_codes")):
        reason_code = _safe_candidate_text(value, 80)
        if reason_code:
            reason_codes.append(reason_code)
    parts = ["Agent recommends opening detail before outreach."]
    if score is not None:
        parts.append(f"value score: {score}.")
    if reason_codes:
        parts.append(f"reasons: {', '.join(reason_codes[:4])}.")
    return " ".join(parts)


def _requirement_sheet_for_projection(
    context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext | WorkbenchLiepinDetailOpenJobContext,
) -> RequirementSheet:
    sheet = context.requirement_review.requirement_sheet
    if sheet is None:
        raise PermissionError("requirement_review_empty")
    return sheet


def _matched_terms(terms: list[str], text: str) -> list[str]:
    normalized = text.casefold()
    return _unique_list(term for term in terms if term.casefold() in normalized)


def _runtime_source_kind(value: object) -> Literal["cts", "liepin"] | None:
    source_kind = _safe_candidate_text(value, 32)
    if source_kind == "cts":
        return "cts"
    if source_kind == "liepin":
        return "liepin"
    return None


def _source_run_by_kind_conn(conn: sqlite3.Connection, *, session_id: str) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT source_kind, source_run_id
        FROM source_runs
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchall()
    return {
        row["source_kind"]: row["source_run_id"]
        for row in rows
        if isinstance(row["source_kind"], str) and isinstance(row["source_run_id"], str)
    }

