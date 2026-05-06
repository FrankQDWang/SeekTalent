from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from seektalent.artifacts import ArtifactSession, atomic_write_text, safe_artifact_path

SAFE_SNAPSHOT_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
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
        session.write_jsonl(logical_name, rows)
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
