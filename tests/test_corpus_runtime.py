from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from seektalent.artifacts import ArtifactStore
from seektalent.corpus.runtime import materialize_corpus_artifacts, write_raw_payload_artifact
from seektalent.corpus.store import CorpusStore


def _jd_document_row() -> dict[str, object]:
    return {
        "jd_doc_id": "jd-doc-1",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace",
        "job_title": "Backend Engineer",
        "jd_text": "Build Python services",
        "notes_text": "",
        "jd_sha256": sha256(b"Build Python services").hexdigest(),
        "notes_sha256": sha256(b"").hexdigest(),
        "task_sha256": "task-sha-1",
        "language": "en",
        "domain_tags_json": ["backend"],
        "seniority": "senior",
        "source_kind": "manual_input",
        "source_ref": None,
        "memory_eligible": False,
        "allowed_uses_json": ["search"],
        "search_index_eligible": True,
        "benchmark_eligible": False,
        "training_eligible": False,
        "external_export_eligible": False,
        "internal_materialization_eligible": True,
        "llm_ingestion_eligible": False,
        "consent_basis": None,
        "source_terms_ref": None,
        "pii_classification_version": "pii-v1",
        "redaction_status": "unredacted",
        "sensitivity_json": {},
        "content_trust_level": "trusted_internal",
        "contains_prompt_like_text": False,
        "llm_sanitization_version": None,
        "llm_ingestion_policy": "quote_as_data_only",
        "retention_policy": "retain_local",
        "schema_version": "jd-doc-v1",
    }


def _seed_resume_document(store: CorpusStore) -> tuple[str, str]:
    store.upsert_resume_subject(
        {
            "subject_id": "subject-1",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace",
            "provider_name": "cts",
            "provider_candidate_id": "provider-1",
            "source_resume_id": "source-1",
            "dedup_key": "provider-1",
            "subject_confidence": "weak",
            "subject_binding_reason": "provider_candidate_id",
        }
    )
    artifact_ref_id = store.record_artifact_ref(
        artifact_kind="corpus",
        artifact_id="corpus-test",
        artifact_root=str(store.path.parent),
        logical_name="corpus.raw_payloads.resume-doc-1",
        relative_path="raw_payloads/resume-doc-1.json",
        content_sha256=sha256(b"raw").hexdigest(),
        schema_version="v1",
    )
    store.upsert_resume_document(
        {
            "resume_doc_id": "resume-doc-1",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace",
            "subject_id": "subject-1",
            "snapshot_sha256": "a" * 64,
            "source_resume_id": "source-1",
            "provider_name": "cts",
            "provider_candidate_id": "provider-1",
            "dedup_key": "provider-1",
            "raw_payload_artifact_ref_id": artifact_ref_id,
            "raw_payload_sha256": sha256(b"raw").hexdigest(),
            "raw_payload_size_bytes": 3,
            "raw_payload_json": None,
            "raw_payload_inline_reason": None,
            "normalized_text": "Python backend",
            "normalized_sections_json": {},
            "skills_json": ["Python"],
            "experience_json": [],
            "education_json": [],
            "locations_json": [],
            "current_title": "Engineer",
            "current_company": None,
            "searchable_text_version": "searchable-text-v1",
            "normalization_version": "resume-normalization-v1",
            "normalization_status": "ok",
            "normalization_failure_kind": None,
            "normalization_warnings_json": [],
            "payload_completeness": "search_result_summary",
            "has_searchable_text": True,
            "source_kind": "provider_return",
            "first_seen_run_id": "run-1",
            "first_seen_query_instance_id": "query-1",
            "first_seen_stage_id": "retrieval",
            "first_seen_artifact_ref_id": artifact_ref_id,
            "memory_eligible": False,
            "allowed_uses_json": ["search"],
            "search_index_eligible": True,
            "benchmark_eligible": False,
            "training_eligible": False,
            "external_export_eligible": False,
            "internal_materialization_eligible": True,
            "llm_ingestion_eligible": False,
            "consent_basis": None,
            "source_terms_ref": None,
            "pii_classification_version": "pii-v1",
            "redaction_status": "unredacted",
            "sensitivity_json": {"contains_pii": True},
            "content_trust_level": "untrusted_external",
            "contains_prompt_like_text": False,
            "llm_sanitization_version": None,
            "llm_ingestion_policy": "quote_as_data_only",
            "retention_policy": "retain_local",
            "schema_version": "resume-doc-v1",
        }
    )
    return "resume-doc-1", artifact_ref_id


def test_write_raw_payload_artifact_writes_json_and_registers_logical_artifact(tmp_path: Path) -> None:
    session = ArtifactStore(tmp_path / "artifacts").create_root(
        kind="corpus",
        display_name="corpus ingest",
        producer="CorpusRuntime",
    )
    snapshot_sha256 = "a" * 64

    artifact = write_raw_payload_artifact(
        session=session,
        snapshot_sha256=snapshot_sha256,
        raw_payload={"resume_id": "r1", "skills": ["Python"]},
    )

    path = session.root / artifact.relative_path
    content = path.read_bytes()
    logical_name = f"corpus.raw_payloads.{snapshot_sha256}"
    manifest_entry = session.load_manifest().logical_artifacts[logical_name]

    assert session.manifest.artifact_kind.value == "corpus"
    assert artifact.logical_name == logical_name
    assert artifact.relative_path == f"raw_payloads/{snapshot_sha256}.json"
    assert artifact.content_sha256 == sha256(content).hexdigest()
    assert artifact.size_bytes == len(content)
    assert path.exists()
    assert manifest_entry.path == artifact.relative_path
    assert manifest_entry.content_type == "application/json"
    assert manifest_entry.schema_version == "v1"


def test_write_raw_payload_artifact_rejects_unsafe_snapshot_name(tmp_path: Path) -> None:
    session = ArtifactStore(tmp_path / "artifacts").create_root(
        kind="corpus",
        display_name="corpus ingest",
        producer="CorpusRuntime",
    )

    with pytest.raises(ValueError):
        write_raw_payload_artifact(
            session=session,
            snapshot_sha256="../escape",
            raw_payload={"resume_id": "r1"},
        )


def test_materialize_corpus_artifacts_writes_export_rows(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    jd_doc_id = store.upsert_jd_document(_jd_document_row())
    resume_doc_id, artifact_ref_id = _seed_resume_document(store)
    input_ref_id = store.record_artifact_ref(
        artifact_kind="run",
        artifact_id="run-1",
        artifact_root=str(tmp_path / "artifacts"),
        logical_name="input.input_snapshot",
        relative_path="input/input_snapshot.json",
        content_sha256=sha256(b"input").hexdigest(),
        schema_version="v1",
    )
    store.link_run_to_jd(
        run_id="run-1",
        tenant_id="tenant-a",
        workspace_id="workspace",
        jd_doc_id=jd_doc_id,
        input_artifact_ref_id=input_ref_id,
    )
    store.record_resume_observations(
        [
            {
                "observation_id": "obs-1",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace",
                "resume_doc_id": resume_doc_id,
                "run_id": "run-1",
                "round_no": 1,
                "stage_id": "retrieval",
                "query_instance_id": "query-1",
                "query_fingerprint": "query-fingerprint-1",
                "provider_name": "cts",
                "provider_request_id": "request-1",
                "provider_rank": 1,
                "provider_page_no": 1,
                "provider_fetch_no": 1,
                "attempt_no": 1,
                "idempotency_key": "tenant-a:workspace:run-1:query-1:resume-doc-1",
                "was_scored": True,
                "was_judged": True,
                "was_selected_final": False,
                "source_artifact_ref_id": artifact_ref_id,
            }
        ]
    )
    collection_id = store.ensure_default_collection("tenant-a", "workspace")
    store.add_corpus_membership(
        tenant_id="tenant-a",
        workspace_id="workspace",
        corpus_collection_id=collection_id,
        resume_doc_id=resume_doc_id,
        added_by_observation_id="obs-1",
        inclusion_reason="observed_in_run",
    )
    session = ArtifactStore(tmp_path / "artifacts").create_root(
        kind="corpus",
        display_name="corpus export",
        producer="CorpusRuntime",
    )

    materialize_corpus_artifacts(
        session=session,
        store=store,
        tenant_id="tenant-a",
        workspace_id="workspace",
    )

    expected_jsonl = [
        "corpus/jd_documents.jsonl",
        "corpus/resume_subjects.jsonl",
        "corpus/resume_documents.jsonl",
        "corpus/resume_observations.jsonl",
        "corpus/run_corpus_links.jsonl",
        "corpus/corpus_collections.jsonl",
        "corpus/corpus_memberships.jsonl",
        "corpus/corpus_exports.jsonl",
    ]
    for relative_path in expected_jsonl:
        assert (session.root / relative_path).exists()

    export_manifest = json.loads((session.root / "corpus/export_manifest.json").read_text(encoding="utf-8"))
    assert export_manifest["self_contained"] is False
    assert export_manifest["raw_payload_policy"] == "external_refs_only"
    assert export_manifest["row_counts"]["corpus.jd_documents"] == 1
    assert "corpus.corpus_exports" in export_manifest["logical_artifacts"]

    export_rows = (session.root / "corpus/corpus_exports.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(export_rows) == 1
    assert json.loads(export_rows[0])["corpus_export_id"] == session.manifest.artifact_id
