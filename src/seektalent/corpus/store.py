from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from seektalent.storage.json import canonical_json, utc_now

CORPUS_SCHEMA_VERSION = "corpus-schema-v1"
DEFAULT_TENANT_ID = "local"
DEFAULT_WORKSPACE_ID = "default"


def _as_json(value: object) -> str:
    return canonical_json(value)


_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_refs (
        artifact_ref_id TEXT PRIMARY KEY,
        artifact_kind TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        artifact_root TEXT NOT NULL,
        logical_name TEXT NOT NULL,
        relative_path TEXT NOT NULL DEFAULT '',
        content_sha256 TEXT,
        schema_version TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(artifact_kind, artifact_id, logical_name, relative_path)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS jd_documents (
        jd_doc_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        job_title TEXT NOT NULL,
        jd_text TEXT NOT NULL,
        notes_text TEXT NOT NULL,
        jd_sha256 TEXT NOT NULL,
        notes_sha256 TEXT NOT NULL,
        task_sha256 TEXT NOT NULL,
        language TEXT,
        domain_tags_json TEXT NOT NULL CHECK(json_valid(domain_tags_json)),
        seniority TEXT,
        source_kind TEXT NOT NULL,
        source_ref TEXT,
        memory_eligible INTEGER NOT NULL,
        allowed_uses_json TEXT NOT NULL CHECK(json_valid(allowed_uses_json)),
        search_index_eligible INTEGER NOT NULL,
        benchmark_eligible INTEGER NOT NULL,
        training_eligible INTEGER NOT NULL,
        external_export_eligible INTEGER NOT NULL,
        internal_materialization_eligible INTEGER NOT NULL,
        llm_ingestion_eligible INTEGER NOT NULL,
        consent_basis TEXT,
        source_terms_ref TEXT,
        pii_classification_version TEXT NOT NULL,
        redaction_status TEXT NOT NULL,
        sensitivity_json TEXT NOT NULL CHECK(json_valid(sensitivity_json)),
        content_trust_level TEXT NOT NULL,
        contains_prompt_like_text INTEGER NOT NULL,
        llm_sanitization_version TEXT,
        llm_ingestion_policy TEXT NOT NULL,
        retention_policy TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, workspace_id, task_sha256),
        UNIQUE(tenant_id, workspace_id, jd_doc_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS resume_subjects (
        subject_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        provider_candidate_id TEXT,
        source_resume_id TEXT,
        dedup_key TEXT,
        subject_confidence TEXT NOT NULL,
        subject_binding_reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, workspace_id, subject_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS resume_documents (
        resume_doc_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        snapshot_sha256 TEXT NOT NULL,
        source_resume_id TEXT,
        provider_name TEXT NOT NULL,
        provider_candidate_id TEXT,
        dedup_key TEXT,
        raw_payload_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        raw_payload_sha256 TEXT NOT NULL,
        raw_payload_size_bytes INTEGER NOT NULL,
        raw_payload_json TEXT CHECK(raw_payload_json IS NULL OR json_valid(raw_payload_json)),
        raw_payload_inline_reason TEXT,
        normalized_text TEXT,
        normalized_sections_json TEXT NOT NULL CHECK(json_valid(normalized_sections_json)),
        skills_json TEXT NOT NULL CHECK(json_valid(skills_json)),
        experience_json TEXT NOT NULL CHECK(json_valid(experience_json)),
        education_json TEXT NOT NULL CHECK(json_valid(education_json)),
        locations_json TEXT NOT NULL CHECK(json_valid(locations_json)),
        current_title TEXT,
        current_company TEXT,
        searchable_text_version TEXT NOT NULL,
        normalization_version TEXT NOT NULL,
        normalization_status TEXT NOT NULL,
        normalization_failure_kind TEXT,
        normalization_warnings_json TEXT NOT NULL CHECK(json_valid(normalization_warnings_json)),
        payload_completeness TEXT NOT NULL,
        has_searchable_text INTEGER NOT NULL,
        source_kind TEXT NOT NULL,
        first_seen_run_id TEXT,
        first_seen_query_instance_id TEXT,
        first_seen_stage_id TEXT,
        first_seen_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        memory_eligible INTEGER NOT NULL,
        allowed_uses_json TEXT NOT NULL CHECK(json_valid(allowed_uses_json)),
        search_index_eligible INTEGER NOT NULL,
        benchmark_eligible INTEGER NOT NULL,
        training_eligible INTEGER NOT NULL,
        external_export_eligible INTEGER NOT NULL,
        internal_materialization_eligible INTEGER NOT NULL,
        llm_ingestion_eligible INTEGER NOT NULL,
        consent_basis TEXT,
        source_terms_ref TEXT,
        pii_classification_version TEXT NOT NULL,
        redaction_status TEXT NOT NULL,
        sensitivity_json TEXT NOT NULL CHECK(json_valid(sensitivity_json)),
        content_trust_level TEXT NOT NULL,
        contains_prompt_like_text INTEGER NOT NULL,
        llm_sanitization_version TEXT,
        llm_ingestion_policy TEXT NOT NULL,
        retention_policy TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, workspace_id, snapshot_sha256),
        UNIQUE(tenant_id, workspace_id, resume_doc_id),
        FOREIGN KEY(tenant_id, workspace_id, subject_id)
            REFERENCES resume_subjects(tenant_id, workspace_id, subject_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS resume_observations (
        observation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        resume_doc_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        round_no INTEGER,
        stage_id TEXT,
        query_instance_id TEXT,
        query_fingerprint TEXT,
        provider_name TEXT NOT NULL,
        provider_request_id TEXT,
        provider_rank INTEGER,
        provider_page_no INTEGER,
        provider_fetch_no INTEGER,
        attempt_no INTEGER NOT NULL,
        idempotency_key TEXT NOT NULL,
        was_scored INTEGER NOT NULL,
        was_judged INTEGER NOT NULL,
        was_selected_final INTEGER NOT NULL,
        source_artifact_ref_id TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(tenant_id, workspace_id, idempotency_key),
        UNIQUE(tenant_id, workspace_id, observation_id),
        FOREIGN KEY(tenant_id, workspace_id, resume_doc_id)
            REFERENCES resume_documents(tenant_id, workspace_id, resume_doc_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS run_corpus_links (
        run_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        jd_doc_id TEXT NOT NULL,
        input_artifact_ref_id TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, tenant_id, workspace_id),
        FOREIGN KEY(tenant_id, workspace_id, jd_doc_id)
            REFERENCES jd_documents(tenant_id, workspace_id, jd_doc_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS corpus_collections (
        corpus_collection_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        mutable INTEGER NOT NULL,
        builder_version TEXT NOT NULL,
        builder_config_json TEXT NOT NULL CHECK(json_valid(builder_config_json)),
        row_count INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id, workspace_id, corpus_collection_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS corpus_memberships (
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        corpus_collection_id TEXT NOT NULL,
        resume_doc_id TEXT NOT NULL,
        added_by_observation_id TEXT,
        inclusion_reason TEXT NOT NULL,
        included_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, workspace_id, corpus_collection_id, resume_doc_id),
        FOREIGN KEY(tenant_id, workspace_id, corpus_collection_id)
            REFERENCES corpus_collections(tenant_id, workspace_id, corpus_collection_id),
        FOREIGN KEY(tenant_id, workspace_id, resume_doc_id)
            REFERENCES resume_documents(tenant_id, workspace_id, resume_doc_id),
        FOREIGN KEY(tenant_id, workspace_id, added_by_observation_id)
            REFERENCES resume_observations(tenant_id, workspace_id, observation_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS corpus_exports (
        corpus_export_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        corpus_collection_id TEXT NOT NULL,
        artifact_ref_id TEXT NOT NULL REFERENCES artifact_refs(artifact_ref_id),
        builder_version TEXT NOT NULL,
        builder_config_hash TEXT NOT NULL,
        builder_config_json TEXT NOT NULL CHECK(json_valid(builder_config_json)),
        source_query TEXT NOT NULL,
        source_run_ids_json TEXT NOT NULL CHECK(json_valid(source_run_ids_json)),
        row_count INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(tenant_id, workspace_id, corpus_collection_id)
            REFERENCES corpus_collections(tenant_id, workspace_id, corpus_collection_id)
    ){strict}
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_jd_documents_task
    ON jd_documents(tenant_id, workspace_id, task_sha256)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resume_subjects_provider
    ON resume_subjects(tenant_id, workspace_id, provider_name, provider_candidate_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resume_subjects_dedup
    ON resume_subjects(tenant_id, workspace_id, dedup_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resume_documents_snapshot
    ON resume_documents(tenant_id, workspace_id, snapshot_sha256)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resume_documents_subject
    ON resume_documents(tenant_id, workspace_id, subject_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resume_observations_query
    ON resume_observations(tenant_id, workspace_id, run_id, query_instance_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_resume_observations_doc
    ON resume_observations(tenant_id, workspace_id, resume_doc_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_corpus_links_run
    ON run_corpus_links(tenant_id, workspace_id, run_id)
    """,
]


class CorpusStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=5)
            self._conn.row_factory = sqlite3.Row
            self._configure(self._conn)
            self._ensure_schema(self._conn)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")

    def _strict_suffix(self, conn: sqlite3.Connection) -> str:
        try:
            conn.execute("CREATE TEMP TABLE __corpus_strict_probe (value TEXT) STRICT")
            conn.execute("DROP TABLE __corpus_strict_probe")
        except sqlite3.OperationalError:
            return ""
        return " STRICT"

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT json_valid(?)", ("{}",)).fetchone()[0] != 1:
            raise RuntimeError("SQLite JSON1 support is required for CorpusStore")
        conn.execute("PRAGMA user_version = 1")
        strict_suffix = self._strict_suffix(conn)
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement.format(strict=strict_suffix))
        conn.execute(
            """
            INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (CORPUS_SCHEMA_VERSION,),
        )
        conn.commit()

    def _json_row(self, row: dict[str, Any], json_fields: set[str]) -> dict[str, Any]:
        result = dict(row)
        for field in json_fields:
            if field in result and result[field] is not None and not isinstance(result[field], str):
                result[field] = _as_json(result[field])
        return result

    def upsert_resume_subject(self, row: dict[str, Any]) -> None:
        data = dict(row)
        now = utc_now()
        data.setdefault("created_at", now)
        data["updated_at"] = now
        columns = list(data)
        updates = [column for column in columns if column not in {"subject_id", "created_at"}]
        self.connect().execute(
            f"""
            INSERT INTO resume_subjects ({", ".join(columns)})
            VALUES ({", ".join(f":{column}" for column in columns)})
            ON CONFLICT(subject_id) DO UPDATE SET
                {", ".join(f"{column} = excluded.{column}" for column in updates)}
            """,
            data,
        )
        self.connect().commit()

    def upsert_resume_document(self, row: dict[str, Any]) -> None:
        data = self._json_row(
            row,
            {
                "normalized_sections_json",
                "skills_json",
                "experience_json",
                "education_json",
                "locations_json",
                "normalization_warnings_json",
                "allowed_uses_json",
                "sensitivity_json",
                "raw_payload_json",
            },
        )
        for field in {
            "has_searchable_text",
            "memory_eligible",
            "search_index_eligible",
            "benchmark_eligible",
            "training_eligible",
            "external_export_eligible",
            "internal_materialization_eligible",
            "llm_ingestion_eligible",
            "contains_prompt_like_text",
        }:
            if field in data and data[field] is not None:
                data[field] = int(data[field])
        now = utc_now()
        data.setdefault("created_at", now)
        data["updated_at"] = now
        columns = list(data)
        first_seen_fields = {
            "first_seen_run_id",
            "first_seen_query_instance_id",
            "first_seen_stage_id",
            "first_seen_artifact_ref_id",
        }
        updates = []
        for column in columns:
            if column in {"resume_doc_id", "created_at"}:
                continue
            if column in first_seen_fields:
                updates.append(f"{column} = COALESCE(resume_documents.{column}, excluded.{column})")
            else:
                updates.append(f"{column} = excluded.{column}")
        self.connect().execute(
            f"""
            INSERT INTO resume_documents ({", ".join(columns)})
            VALUES ({", ".join(f":{column}" for column in columns)})
            ON CONFLICT(tenant_id, workspace_id, snapshot_sha256) DO UPDATE SET
                {", ".join(updates)}
            """,
            data,
        )
        self.connect().commit()
