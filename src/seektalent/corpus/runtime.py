from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from seektalent.artifacts import ArtifactSession, atomic_write_text, safe_artifact_path
from seektalent.corpus.documents import build_observation_row, build_resume_document_row, build_resume_subject_row
from seektalent.resumes.snapshots import snapshot_sha256 as build_snapshot_sha256
from seektalent.storage.json import sha256_json

SAFE_SNAPSHOT_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
OMITTED_RAW_PAYLOAD_INLINE_REASON = "omitted_from_external_refs_only_export"
MATERIALIZED_CORPUS_TABLES = {
    "jd_documents": "corpus.jd_documents",
    "resume_subjects": "corpus.resume_subjects",
    "resume_documents": "corpus.resume_documents",
    "resume_observations": "corpus.resume_observations",
    "run_corpus_links": "corpus.run_corpus_links",
    "corpus_collections": "corpus.corpus_collections",
    "corpus_memberships": "corpus.corpus_memberships",
}


@dataclass(frozen=True)
class RawPayloadArtifact:
    logical_name: str
    relative_path: str
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ProviderReturnedCandidate:
    candidate: Any
    stage_id: str
    round_no: int
    query_instance_id: str
    query_fingerprint: str
    provider_name: str
    provider_request_id: str
    provider_rank: int
    provider_page_no: int
    provider_fetch_no: int
    attempt_no: int


def write_raw_payload_artifact(
    session: ArtifactSession,
    snapshot_sha256: str,
    raw_payload: dict[str, Any],
) -> RawPayloadArtifact:
    if SAFE_SNAPSHOT_SHA256_RE.fullmatch(snapshot_sha256) is None:
        raise ValueError("snapshot_sha256 must be a lowercase 64-character hex string")

    logical_name = f"corpus.raw_payloads.{snapshot_sha256}"
    relative_path = f"raw_payloads/{snapshot_sha256}.json"
    text = json.dumps(raw_payload, ensure_ascii=False, indent=2, sort_keys=True)
    encoded = text.encode("utf-8")
    path = safe_artifact_path(session.root, relative_path)

    atomic_write_text(path, text)
    session.register_path(
        logical_name,
        relative_path,
        content_type="application/json",
        schema_version="v1",
    )
    return RawPayloadArtifact(
        logical_name=logical_name,
        relative_path=relative_path,
        content_sha256=sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
    )


def _record_session_artifact_ref(*, session: ArtifactSession, store: Any, logical_name: str) -> str:
    entry = session.manifest.logical_artifacts[logical_name]
    path = safe_artifact_path(session.root, entry.path)
    return store.record_artifact_ref(
        artifact_kind=session.manifest.artifact_kind.value,
        artifact_id=session.manifest.artifact_id,
        artifact_root=str(session.root),
        logical_name=logical_name,
        relative_path=entry.path,
        content_sha256=sha256(path.read_bytes()).hexdigest(),
        schema_version=entry.schema_version,
    )


def build_deterministic_provider_request_id(
    *,
    provider_name: str,
    query_instance_id: str,
    query_fingerprint: str,
    page_no: int,
    fetch_no: int,
    request_payload: dict[str, Any] | None = None,
) -> str:
    return sha256_json(
        {
            "provider_name": provider_name,
            "query_instance_id": query_instance_id,
            "query_fingerprint": query_fingerprint,
            "page_no": page_no,
            "fetch_no": fetch_no,
            "request_payload": request_payload,
        }
    )


def _candidate_raw_payload(candidate: Any) -> dict[str, Any]:
    raw_payload = getattr(candidate, "raw", None)
    if isinstance(raw_payload, dict):
        return raw_payload
    model_dump = getattr(candidate, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    raise ValueError("provider candidate must expose a raw payload")


def _candidate_text_attr(candidate: Any, attr: str) -> str | None:
    value = getattr(candidate, attr, None)
    return value if isinstance(value, str) and value else None


def _raw_text_value(raw_payload: dict[str, Any], key: str) -> str | None:
    value = raw_payload.get(key)
    return value if isinstance(value, str) and value else None


def _provider_candidate_id(candidate: Any, raw_payload: dict[str, Any]) -> str | None:
    return _candidate_text_attr(candidate, "provider_candidate_id") or _raw_text_value(
        raw_payload,
        "provider_candidate_id",
    )


def _snapshot_hash(candidate: Any, raw_payload: dict[str, Any]) -> str:
    return _candidate_text_attr(candidate, "snapshot_sha256") or build_snapshot_sha256(raw_payload)


def record_corpus_provider_results(
    *,
    session: ArtifactSession,
    store: Any,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
    returned_candidates: list[ProviderReturnedCandidate],
) -> None:
    collection_id = store.ensure_default_collection(tenant_id, workspace_id)
    observations: list[dict[str, Any]] = []
    memberships: list[tuple[str, str]] = []

    for returned in returned_candidates:
        candidate = returned.candidate
        raw_payload = _candidate_raw_payload(candidate)
        snapshot_hash = _snapshot_hash(candidate, raw_payload)
        raw_artifact = write_raw_payload_artifact(
            session=session,
            snapshot_sha256=snapshot_hash,
            raw_payload=raw_payload,
        )
        raw_artifact_ref_id = _record_session_artifact_ref(
            session=session,
            store=store,
            logical_name=raw_artifact.logical_name,
        )
        provider_candidate_id = _provider_candidate_id(candidate, raw_payload)
        source_resume_id = _candidate_text_attr(candidate, "source_resume_id") or _raw_text_value(
            raw_payload,
            "source_resume_id",
        )
        dedup_key = _candidate_text_attr(candidate, "dedup_key")
        subject_row = build_resume_subject_row(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            provider_name=returned.provider_name,
            provider_candidate_id=provider_candidate_id,
            source_resume_id=source_resume_id,
            dedup_key=dedup_key,
            snapshot_sha256=snapshot_hash,
        )
        store.upsert_resume_subject(subject_row)

        resume_doc_id = f"{tenant_id}:{workspace_id}:{snapshot_hash}"
        store.upsert_resume_document(
            build_resume_document_row(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                raw_payload=raw_payload,
                provider_name=returned.provider_name,
                provider_candidate_id=provider_candidate_id,
                source_resume_id=source_resume_id,
                dedup_key=dedup_key,
                resume_doc_id=resume_doc_id,
                subject_id=str(subject_row["subject_id"]),
                snapshot_sha256=snapshot_hash,
                raw_payload_artifact_ref_id=raw_artifact_ref_id,
                raw_payload_sha256=raw_artifact.content_sha256,
                raw_payload_size_bytes=raw_artifact.size_bytes,
                normalized_text=_candidate_text_attr(candidate, "search_text") or "",
                first_seen_run_id=run_id,
                first_seen_query_instance_id=returned.query_instance_id,
                first_seen_stage_id=returned.stage_id,
                first_seen_artifact_ref_id=raw_artifact_ref_id,
            )
        )
        observation = build_observation_row(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            resume_doc_id=resume_doc_id,
            run_id=run_id,
            round_no=returned.round_no,
            stage_id=returned.stage_id,
            query_instance_id=returned.query_instance_id,
            query_fingerprint=returned.query_fingerprint,
            provider_name=returned.provider_name,
            provider_request_id=returned.provider_request_id,
            provider_rank=returned.provider_rank,
            provider_page_no=returned.provider_page_no,
            provider_fetch_no=returned.provider_fetch_no,
            attempt_no=returned.attempt_no,
            source_artifact_ref_id=raw_artifact_ref_id,
        )
        observations.append(observation)
        memberships.append((resume_doc_id, str(observation["observation_id"])))

    store.record_resume_observations(observations)
    for resume_doc_id, observation_id in memberships:
        store.add_corpus_membership(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            corpus_collection_id=collection_id,
            resume_doc_id=resume_doc_id,
            added_by_observation_id=observation_id,
            inclusion_reason="observed_in_run",
        )
    if memberships:
        store.ensure_default_collection(tenant_id, workspace_id)


def write_corpus_ingest_manifest(
    *,
    session: ArtifactSession,
    run_id: str,
    tenant_id: str,
    workspace_id: str,
) -> None:
    raw_payload_paths = {
        logical_name: entry.path
        for logical_name, entry in sorted(session.manifest.logical_artifacts.items())
        if logical_name.startswith("corpus.raw_payloads.")
    }
    session.write_json(
        "corpus.ingest_manifest",
        {
            "artifact_id": session.manifest.artifact_id,
            "corpus_artifact_role": "ingest",
            "run_id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "raw_payload_count": len(raw_payload_paths),
            "raw_payload_paths": raw_payload_paths,
        },
    )


def _rows_for_materialized_export(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if table != "resume_documents":
        return rows

    exported_rows = []
    for row in rows:
        exported_row = dict(row)
        if exported_row.get("raw_payload_json") is not None:
            exported_row["raw_payload_json"] = None
            exported_row["raw_payload_inline_reason"] = OMITTED_RAW_PAYLOAD_INLINE_REASON
        exported_rows.append(exported_row)
    return exported_rows


def materialize_corpus_artifacts(
    *,
    session: ArtifactSession,
    store: Any,
    tenant_id: str,
    workspace_id: str,
) -> None:
    collection_id = store.ensure_default_collection(tenant_id, workspace_id)
    row_counts: dict[str, int] = {}

    for table, logical_name in MATERIALIZED_CORPUS_TABLES.items():
        rows = store.rows_for_tenant(table, tenant_id, workspace_id)
        session.write_jsonl(logical_name, _rows_for_materialized_export(table, rows))
        row_counts[logical_name] = len(rows)
        _record_session_artifact_ref(session=session, store=store, logical_name=logical_name)

    existing_export_rows = store.rows_for_tenant("corpus_exports", tenant_id, workspace_id)
    export_row_already_exists = any(row["corpus_export_id"] == session.manifest.artifact_id for row in existing_export_rows)
    row_counts["corpus.corpus_exports"] = len(existing_export_rows) + (0 if export_row_already_exists else 1)
    total_exported_row_count = sum(row_counts.values())
    logical_artifacts = sorted([*row_counts, "corpus.export_manifest"])
    manifest = {
        "artifact_id": session.manifest.artifact_id,
        "corpus_artifact_role": "materialized_export",
        "self_contained": False,
        "raw_payload_policy": "external_refs_only",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "logical_artifacts": logical_artifacts,
        "row_counts": row_counts,
        "total_exported_row_count": total_exported_row_count,
    }
    manifest_path = session.write_json("corpus.export_manifest", manifest)
    manifest_ref_id = _record_session_artifact_ref(
        session=session,
        store=store,
        logical_name="corpus.export_manifest",
    )
    store.record_corpus_export(
        corpus_export_id=session.manifest.artifact_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        corpus_collection_id=collection_id,
        artifact_ref_id=manifest_ref_id,
        builder_version="corpus-materializer-v1",
        builder_config={"tenant_id": tenant_id, "workspace_id": workspace_id},
        source_query="all tenant/workspace corpus rows",
        source_run_ids=[],
        row_count=total_exported_row_count,
        sha256_value=sha256(manifest_path.read_bytes()).hexdigest(),
    )

    session.write_jsonl("corpus.corpus_exports", store.rows_for_tenant("corpus_exports", tenant_id, workspace_id))
    _record_session_artifact_ref(session=session, store=store, logical_name="corpus.corpus_exports")
