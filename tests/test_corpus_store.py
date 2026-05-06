from __future__ import annotations

import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from seektalent.corpus.store import CORPUS_SCHEMA_VERSION, CorpusStore


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _subject_row(
    *,
    tenant_id: str,
    workspace_id: str = "workspace",
    subject_id: str = "subject-1",
) -> dict[str, object]:
    return {
        "subject_id": subject_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "provider_name": "cts",
        "provider_candidate_id": "provider-1",
        "source_resume_id": "source-1",
        "dedup_key": "provider-1",
        "subject_confidence": "weak",
        "subject_binding_reason": "provider_candidate_id",
    }


def _resume_document_row(
    *,
    tenant_id: str,
    resume_doc_id: str,
    subject_id: str,
    snapshot_sha256: str = "snapshot-1",
    normalized_text: str | None = "Python backend",
) -> dict[str, object]:
    return {
        "resume_doc_id": resume_doc_id,
        "tenant_id": tenant_id,
        "workspace_id": "workspace",
        "subject_id": subject_id,
        "snapshot_sha256": snapshot_sha256,
        "source_resume_id": "source-1",
        "provider_name": "cts",
        "provider_candidate_id": "provider-1",
        "dedup_key": "provider-1",
        "raw_payload_artifact_ref_id": None,
        "raw_payload_sha256": "raw-sha",
        "raw_payload_size_bytes": 12,
        "raw_payload_json": {"resume_id": resume_doc_id, "source": "test"},
        "raw_payload_inline_reason": "test_inline_payload",
        "normalized_text": normalized_text,
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


def _jd_document_row(
    *,
    tenant_id: str = "tenant-a",
    workspace_id: str = "workspace",
    jd_doc_id: str = "jd-doc-1",
) -> dict[str, object]:
    return {
        "jd_doc_id": jd_doc_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "job_title": "Backend Engineer",
        "jd_text": "Build Python services",
        "notes_text": "",
        "jd_sha256": sha256(b"Build Python services").hexdigest(),
        "notes_sha256": sha256(b"").hexdigest(),
        "task_sha256": f"{tenant_id}:{workspace_id}:task-sha-1",
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


def _seed_resume_document(
    store: CorpusStore,
    *,
    tenant_id: str = "tenant-a",
    workspace_id: str = "workspace",
    resume_doc_id: str = "resume-doc-1",
    subject_id: str = "subject-1",
) -> tuple[str, str]:
    store.upsert_resume_subject(_subject_row(tenant_id=tenant_id, workspace_id=workspace_id, subject_id=subject_id))
    artifact_ref_id = store.record_artifact_ref(
        artifact_kind="corpus",
        artifact_id="corpus-test",
        artifact_root=str(store.path.parent),
        logical_name=f"corpus.raw_payloads.{resume_doc_id}",
        relative_path=f"raw_payloads/{resume_doc_id}.json",
        content_sha256="raw-sha",
        schema_version="v1",
    )
    row = _resume_document_row(tenant_id=tenant_id, resume_doc_id=resume_doc_id, subject_id=subject_id)
    row["workspace_id"] = workspace_id
    row["raw_payload_artifact_ref_id"] = artifact_ref_id
    row["raw_payload_json"] = None
    row["raw_payload_inline_reason"] = None
    row["first_seen_artifact_ref_id"] = artifact_ref_id
    store.upsert_resume_document(row)
    return resume_doc_id, artifact_ref_id


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


def test_corpus_store_rejects_stale_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "corpus.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', 'corpus-schema-v1')")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="(?i)stale.*schema.*recreate"):
        CorpusStore(path).connect()


def test_corpus_store_rejects_unversioned_existing_schema(tmp_path: Path) -> None:
    path = tmp_path / "corpus.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE resume_documents (resume_doc_id TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="(?i)corpus.*schema.*recreate"):
        CorpusStore(path).connect()


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
        store.upsert_resume_subject(_subject_row(tenant_id=tenant, subject_id=f"{tenant}:subject"))
        store.upsert_resume_document(
            _resume_document_row(
                tenant_id=tenant,
                resume_doc_id=f"{tenant}:doc",
                subject_id=f"{tenant}:subject",
                snapshot_sha256="same-snapshot",
            )
        )

    rows = store.connect().execute("SELECT tenant_id FROM resume_documents WHERE snapshot_sha256 = 'same-snapshot'").fetchall()
    assert [row["tenant_id"] for row in rows] == ["tenant-a", "tenant-b"]


def test_resume_document_upsert_preserves_doc_id_and_first_seen(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    store.upsert_resume_subject(_subject_row(tenant_id="tenant-a"))
    store.upsert_resume_document(
        _resume_document_row(
            tenant_id="tenant-a",
            resume_doc_id="doc-original",
            subject_id="subject-1",
            normalized_text="original text",
        )
    )

    updated = _resume_document_row(
        tenant_id="tenant-a",
        resume_doc_id="doc-new",
        subject_id="subject-1",
        normalized_text="updated text",
    )
    updated["first_seen_run_id"] = "run-2"
    updated["first_seen_query_instance_id"] = "query-2"
    updated["first_seen_stage_id"] = "rerank"
    store.upsert_resume_document(updated)

    row = store.connect().execute(
        """
        SELECT resume_doc_id, first_seen_run_id, first_seen_query_instance_id,
               first_seen_stage_id, normalized_text
        FROM resume_documents
        WHERE tenant_id = 'tenant-a' AND workspace_id = 'workspace' AND snapshot_sha256 = 'snapshot-1'
        """
    ).fetchone()
    assert dict(row) == {
        "resume_doc_id": "doc-original",
        "first_seen_run_id": "run-1",
        "first_seen_query_instance_id": "query-1",
        "first_seen_stage_id": "retrieval",
        "normalized_text": "updated text",
    }


def test_corpus_store_rejects_cross_tenant_subject_reference(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    store.upsert_resume_subject(_subject_row(tenant_id="tenant-a", subject_id="shared-subject"))

    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_resume_document(
            _resume_document_row(
                tenant_id="tenant-b",
                resume_doc_id="tenant-b-doc",
                subject_id="shared-subject",
            )
        )


def test_artifact_refs_relative_path_uniqueness_handles_empty_path(tmp_path: Path) -> None:
    conn = CorpusStore(tmp_path / "corpus.sqlite3").connect()
    conn.execute(
        """
        INSERT INTO artifact_refs (
            artifact_ref_id, artifact_kind, artifact_id, artifact_root,
            logical_name, content_sha256, schema_version, created_at
        ) VALUES (
            'artifact-1', 'run', 'run-1', '/tmp/artifacts',
            'corpus.resume_documents', 'sha-1', 'artifact-v1', '2026-05-06T00:00:00Z'
        )
        """
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO artifact_refs (
                artifact_ref_id, artifact_kind, artifact_id, artifact_root,
                logical_name, relative_path, content_sha256, schema_version, created_at
            ) VALUES (
                'artifact-2', 'run', 'run-1', '/tmp/artifacts',
                'corpus.resume_documents', '', 'sha-1', 'artifact-v1', '2026-05-06T00:00:00Z'
            )
            """
        )


def test_record_artifact_ref_updates_path_for_same_logical_name(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")

    first_ref_id = store.record_artifact_ref(
        artifact_kind="corpus",
        artifact_id="corpus-1",
        artifact_root=str(tmp_path / "artifacts"),
        logical_name="corpus.resume_documents",
        relative_path="corpus/resume_documents.jsonl",
        content_sha256="sha-1",
        schema_version="v1",
    )
    second_ref_id = store.record_artifact_ref(
        artifact_kind="corpus",
        artifact_id="corpus-1",
        artifact_root=str(tmp_path / "artifacts"),
        logical_name="corpus.resume_documents",
        relative_path="corpus/resume_documents-v2.jsonl",
        content_sha256="sha-2",
        schema_version="v2",
    )

    rows = store.connect().execute("SELECT * FROM artifact_refs").fetchall()
    assert second_ref_id == first_ref_id == "corpus:corpus-1:corpus.resume_documents"
    assert len(rows) == 1
    assert dict(rows[0]) | {"created_at": "ignored"} == {
        "artifact_ref_id": "corpus:corpus-1:corpus.resume_documents",
        "artifact_kind": "corpus",
        "artifact_id": "corpus-1",
        "artifact_root": str(tmp_path / "artifacts"),
        "logical_name": "corpus.resume_documents",
        "relative_path": "corpus/resume_documents-v2.jsonl",
        "content_sha256": "sha-2",
        "schema_version": "v2",
        "created_at": "ignored",
    }


def test_resume_subject_identity_is_tenant_workspace_scoped(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    store.upsert_resume_subject(
        _subject_row(tenant_id="tenant-a", workspace_id="workspace-a", subject_id="shared-subject")
    )
    tenant_b = _subject_row(tenant_id="tenant-b", workspace_id="workspace-b", subject_id="shared-subject")
    tenant_b["provider_candidate_id"] = "provider-b"
    tenant_b["dedup_key"] = "provider-b"
    tenant_b["subject_confidence"] = "strong"
    store.upsert_resume_subject(tenant_b)

    tenant_b["subject_binding_reason"] = "dedup_key"
    store.upsert_resume_subject(tenant_b)

    rows = store.connect().execute(
        """
        SELECT tenant_id, workspace_id, subject_id, provider_candidate_id,
               dedup_key, subject_confidence, subject_binding_reason
        FROM resume_subjects
        WHERE subject_id = 'shared-subject'
        ORDER BY tenant_id, workspace_id
        """
    ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "subject_id": "shared-subject",
            "provider_candidate_id": "provider-1",
            "dedup_key": "provider-1",
            "subject_confidence": "weak",
            "subject_binding_reason": "provider_candidate_id",
        },
        {
            "tenant_id": "tenant-b",
            "workspace_id": "workspace-b",
            "subject_id": "shared-subject",
            "provider_candidate_id": "provider-b",
            "dedup_key": "provider-b",
            "subject_confidence": "strong",
            "subject_binding_reason": "dedup_key",
        },
    ]


def test_resume_document_requires_raw_payload_source(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    store.upsert_resume_subject(_subject_row(tenant_id="tenant-a"))
    row = _resume_document_row(
        tenant_id="tenant-a",
        resume_doc_id="doc-without-payload",
        subject_id="subject-1",
    )
    row["raw_payload_json"] = None
    row["raw_payload_inline_reason"] = None

    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_resume_document(row)


def test_record_resume_observation_is_idempotent(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    resume_doc_id, artifact_ref_id = _seed_resume_document(store)
    row = {
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
        "was_scored": False,
        "was_judged": True,
        "was_selected_final": False,
        "source_artifact_ref_id": artifact_ref_id,
    }

    store.record_resume_observations([row])
    store.record_resume_observations([row])

    count = store.connect().execute("SELECT COUNT(*) FROM resume_observations").fetchone()[0]
    assert count == 1


def test_record_resume_observations_rolls_back_batch_on_failure(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    resume_doc_id, artifact_ref_id = _seed_resume_document(store)
    valid_row = {
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
        "was_scored": False,
        "was_judged": True,
        "was_selected_final": False,
        "source_artifact_ref_id": artifact_ref_id,
    }
    invalid_row = dict(valid_row)
    invalid_row["observation_id"] = "obs-2"
    invalid_row["idempotency_key"] = "tenant-a:workspace:run-1:query-1:resume-doc-2"
    invalid_row["source_artifact_ref_id"] = "missing-artifact-ref"

    with pytest.raises(sqlite3.IntegrityError):
        store.record_resume_observations([valid_row, invalid_row])

    count = store.connect().execute("SELECT COUNT(*) FROM resume_observations").fetchone()[0]
    assert count == 0


def test_link_run_to_jd_rejects_missing_input_artifact_ref(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    jd_doc_id = store.upsert_jd_document(_jd_document_row())

    with pytest.raises(sqlite3.IntegrityError):
        store.link_run_to_jd(
            run_id="run-1",
            tenant_id="tenant-a",
            workspace_id="workspace",
            jd_doc_id=jd_doc_id,
            input_artifact_ref_id="missing-artifact-ref",
        )


def test_link_run_to_jd_allows_null_input_artifact_ref(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    jd_doc_id = store.upsert_jd_document(_jd_document_row())

    store.link_run_to_jd(
        run_id="run-1",
        tenant_id="tenant-a",
        workspace_id="workspace",
        jd_doc_id=jd_doc_id,
        input_artifact_ref_id=None,
    )

    row = store.connect().execute("SELECT input_artifact_ref_id FROM run_corpus_links").fetchone()
    assert row["input_artifact_ref_id"] is None


def test_record_resume_observation_rejects_missing_source_artifact_ref(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    resume_doc_id, _artifact_ref_id = _seed_resume_document(store)

    with pytest.raises(sqlite3.IntegrityError):
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
                    "was_scored": False,
                    "was_judged": True,
                    "was_selected_final": False,
                    "source_artifact_ref_id": "missing-artifact-ref",
                }
            ]
        )


def test_record_resume_observation_allows_null_source_artifact_ref(tmp_path: Path) -> None:
    store = CorpusStore(tmp_path / "corpus.sqlite3")
    resume_doc_id, _artifact_ref_id = _seed_resume_document(store)

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
                "was_scored": False,
                "was_judged": True,
                "was_selected_final": False,
                "source_artifact_ref_id": None,
            }
        ]
    )

    row = store.connect().execute("SELECT source_artifact_ref_id FROM resume_observations").fetchone()
    assert row["source_artifact_ref_id"] is None
