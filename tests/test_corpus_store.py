from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent.corpus.store import CORPUS_SCHEMA_VERSION, CorpusStore, canonical_json


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_corpus_store_creates_schema_and_pragmas(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    conn = store.connect()

    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == CORPUS_SCHEMA_VERSION
    assert {
        "schema_meta",
        "artifact_refs",
        "jd_documents",
        "resume_subjects",
        "resume_documents",
        "resume_observations",
        "run_corpus_links",
        "corpus_collections",
        "corpus_memberships",
        "corpus_exports",
    } <= _tables(conn)


def test_corpus_store_rejects_invalid_json_columns(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    conn = store.connect()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO jd_documents (
                jd_doc_id, tenant_id, workspace_id, job_title, jd_text, notes_text,
                jd_sha256, notes_sha256, task_sha256, domain_tags_json, source_kind,
                memory_eligible, allowed_uses_json, search_index_eligible,
                benchmark_eligible, training_eligible, external_export_eligible,
                internal_materialization_eligible, llm_ingestion_eligible,
                pii_classification_version, redaction_status,
                sensitivity_json, content_trust_level, contains_prompt_like_text,
                llm_ingestion_policy, retention_policy, schema_version, created_at,
                updated_at
            ) VALUES (
                'jd-1', 'tenant', 'workspace', 'title', 'jd', '', 'a', 'b', 'c',
                '{bad', 'manual_input', 0, '[]', 0, 0, 0, 0, 1, 0, 'pii-v1',
                'unredacted', '{}', 'untrusted_external', 0, 'quote_as_data_only',
                'retain_local', 'jd-v1', '2026-05-06T00:00:00Z', '2026-05-06T00:00:00Z'
            )
            """
        )


def test_same_snapshot_hash_is_tenant_scoped(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    for tenant in ("tenant-a", "tenant-b"):
        store.upsert_resume_subject(
            {
                "subject_id": f"{tenant}:subject",
                "tenant_id": tenant,
                "workspace_id": "workspace",
                "provider_name": "cts",
                "provider_candidate_id": "provider-1",
                "source_resume_id": "source-1",
                "dedup_key": "provider-1",
                "subject_confidence": "weak",
                "subject_binding_reason": "provider_candidate_id",
            }
        )
        store.upsert_resume_document(
            {
                "resume_doc_id": f"{tenant}:doc",
                "tenant_id": tenant,
                "workspace_id": "workspace",
                "subject_id": f"{tenant}:subject",
                "snapshot_sha256": "same-snapshot",
                "source_resume_id": "source-1",
                "provider_name": "cts",
                "provider_candidate_id": "provider-1",
                "dedup_key": "provider-1",
                "raw_payload_artifact_ref_id": None,
                "raw_payload_sha256": "raw-sha",
                "raw_payload_size_bytes": 12,
                "raw_payload_json": None,
                "raw_payload_inline_reason": None,
                "normalized_text": "Python backend",
                "normalized_sections_json": {},
                "skills_json": ["Python"],
                "experience_json": [],
                "education_json": [],
                "locations_json": [],
                "current_title": None,
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
                "first_seen_artifact_ref_id": None,
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

    rows = store.connect().execute("SELECT tenant_id FROM resume_documents WHERE snapshot_sha256 = 'same-snapshot'").fetchall()
    assert [row["tenant_id"] for row in rows] == ["tenant-a", "tenant-b"]
