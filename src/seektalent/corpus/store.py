from __future__ import annotations

import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any

from seektalent.storage.json import canonical_json, utc_now

CORPUS_SCHEMA_VERSION = "corpus-schema-v2"
DEFAULT_TENANT_ID = "local"
DEFAULT_WORKSPACE_ID = "default"

_CORPUS_TABLE_NAMES = {
    "artifact_refs",
    "jd_documents",
    "resume_subjects",
    "resume_documents",
    "resume_observations",
    "run_corpus_links",
    "corpus_collections",
    "corpus_memberships",
    "corpus_exports",
}

_TENANT_TABLE_ORDER_BY = {
    "jd_documents": "tenant_id, workspace_id, task_sha256, jd_doc_id",
    "resume_subjects": "tenant_id, workspace_id, subject_id",
    "resume_documents": "tenant_id, workspace_id, snapshot_sha256, resume_doc_id",
    "resume_observations": "tenant_id, workspace_id, run_id, query_instance_id, provider_rank, observation_id",
    "run_corpus_links": "tenant_id, workspace_id, run_id",
    "corpus_collections": "tenant_id, workspace_id, corpus_collection_id",
    "corpus_memberships": "tenant_id, workspace_id, corpus_collection_id, resume_doc_id",
    "corpus_exports": "tenant_id, workspace_id, corpus_export_id",
}


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
        UNIQUE(artifact_kind, artifact_id, logical_name)
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
        subject_id TEXT NOT NULL,
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
        PRIMARY KEY (tenant_id, workspace_id, subject_id)
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
        CHECK(
            raw_payload_artifact_ref_id IS NOT NULL
            OR (raw_payload_json IS NOT NULL AND raw_payload_inline_reason IS NOT NULL)
        ),
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
        source_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
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
        input_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
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

    def _table_names(self, conn: sqlite3.Connection) -> set[str]:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _enforce_schema_version(self, conn: sqlite3.Connection) -> None:
        table_names = self._table_names(conn)
        if "schema_meta" in table_names:
            row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
            schema_version = row["value"] if row is not None else None
            if schema_version != CORPUS_SCHEMA_VERSION:
                raise RuntimeError(
                    "Local corpus DB schema is stale; "
                    f"found {schema_version or 'missing schema_version'}, expected {CORPUS_SCHEMA_VERSION}. "
                    "Recreate the local corpus DB."
                )
            return

        existing_corpus_tables = sorted(table_names & _CORPUS_TABLE_NAMES)
        if existing_corpus_tables:
            raise RuntimeError(
                "Local corpus DB has unversioned corpus schema tables "
                f"({', '.join(existing_corpus_tables)}). Recreate the local corpus DB."
            )

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT json_valid(?)", ("{}",)).fetchone()[0] != 1:
            raise RuntimeError("SQLite JSON1 support is required for CorpusStore")
        self._enforce_schema_version(conn)
        conn.execute("PRAGMA user_version = 1")
        strict_suffix = self._strict_suffix(conn)
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement.format(strict=strict_suffix))
        conn.execute(
            """
            INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO NOTHING
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
        updates = [
            column
            for column in columns
            if column not in {"tenant_id", "workspace_id", "subject_id", "created_at"}
        ]
        self.connect().execute(
            f"""
            INSERT INTO resume_subjects ({", ".join(columns)})
            VALUES ({", ".join(f":{column}" for column in columns)})
            ON CONFLICT(tenant_id, workspace_id, subject_id) DO UPDATE SET
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

    def get_resume_documents_by_snapshot_sha256(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        snapshot_sha256_values: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not snapshot_sha256_values:
            return {}
        placeholders = ",".join("?" for _ in snapshot_sha256_values)
        rows = self.connect().execute(
            f"""
            SELECT
                resume_doc_id,
                snapshot_sha256,
                provider_name,
                raw_payload_artifact_ref_id,
                normalized_text,
                normalized_sections_json,
                skills_json,
                experience_json,
                education_json,
                locations_json,
                current_title,
                current_company,
                allowed_uses_json,
                internal_materialization_eligible,
                redaction_status
            FROM resume_documents
            WHERE tenant_id = ?
              AND workspace_id = ?
              AND snapshot_sha256 IN ({placeholders})
            """,
            (tenant_id, workspace_id, *snapshot_sha256_values),
        ).fetchall()
        return {str(row["snapshot_sha256"]): dict(row) for row in rows}

    def get_artifact_ref(self, artifact_ref_id: str) -> dict[str, Any] | None:
        row = self.connect().execute(
            """
            SELECT artifact_ref_id, artifact_root, relative_path, content_sha256, schema_version
            FROM artifact_refs
            WHERE artifact_ref_id = ?
            """,
            (artifact_ref_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def record_artifact_ref(
        self,
        *,
        artifact_kind: str,
        artifact_id: str,
        artifact_root: str,
        logical_name: str,
        relative_path: str | None,
        content_sha256: str | None,
        schema_version: str | None,
    ) -> str:
        artifact_ref_id = f"{artifact_kind}:{artifact_id}:{logical_name}"
        self.connect().execute(
            """
            INSERT INTO artifact_refs (
                artifact_ref_id, artifact_kind, artifact_id, artifact_root,
                logical_name, relative_path, content_sha256, schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_kind, artifact_id, logical_name) DO UPDATE SET
                artifact_root = excluded.artifact_root,
                relative_path = excluded.relative_path,
                content_sha256 = excluded.content_sha256,
                schema_version = excluded.schema_version
            """,
            (
                artifact_ref_id,
                artifact_kind,
                artifact_id,
                artifact_root,
                logical_name,
                relative_path or "",
                content_sha256,
                schema_version,
                utc_now(),
            ),
        )
        self.connect().commit()
        return artifact_ref_id

    def upsert_jd_document(self, row: dict[str, Any]) -> str:
        data = self._json_row(
            row,
            {
                "domain_tags_json",
                "allowed_uses_json",
                "sensitivity_json",
            },
        )
        for field in {
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
        updates = [
            f"{column} = excluded.{column}"
            for column in columns
            if column not in {"jd_doc_id", "tenant_id", "workspace_id", "task_sha256", "created_at"}
        ]
        self.connect().execute(
            f"""
            INSERT INTO jd_documents ({", ".join(columns)})
            VALUES ({", ".join(f":{column}" for column in columns)})
            ON CONFLICT(tenant_id, workspace_id, task_sha256) DO UPDATE SET
                {", ".join(updates)}
            """,
            data,
        )
        self.connect().commit()
        row = self.connect().execute(
            """
            SELECT jd_doc_id
            FROM jd_documents
            WHERE tenant_id = ? AND workspace_id = ? AND task_sha256 = ?
            """,
            (data["tenant_id"], data["workspace_id"], data["task_sha256"]),
        ).fetchone()
        return str(row["jd_doc_id"])

    def link_run_to_jd(
        self,
        *,
        run_id: str,
        tenant_id: str,
        workspace_id: str,
        jd_doc_id: str,
        input_artifact_ref_id: str | None,
    ) -> None:
        self.connect().execute(
            """
            INSERT INTO run_corpus_links (
                run_id, tenant_id, workspace_id, jd_doc_id, input_artifact_ref_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, tenant_id, workspace_id) DO UPDATE SET
                jd_doc_id = excluded.jd_doc_id,
                input_artifact_ref_id = excluded.input_artifact_ref_id
            """,
            (run_id, tenant_id, workspace_id, jd_doc_id, input_artifact_ref_id, utc_now()),
        )
        self.connect().commit()

    def record_resume_observations(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        conn = self.connect()
        now = utc_now()
        try:
            conn.execute("BEGIN")
            for row in rows:
                data = dict(row)
                data.setdefault("created_at", now)
                for field in {"was_scored", "was_judged", "was_selected_final"}:
                    data[field] = int(data[field])
                columns = list(data)
                conn.execute(
                    f"""
                    INSERT INTO resume_observations ({", ".join(columns)})
                    VALUES ({", ".join(f":{column}" for column in columns)})
                    ON CONFLICT(tenant_id, workspace_id, idempotency_key) DO UPDATE SET
                        was_scored = excluded.was_scored,
                        was_judged = excluded.was_judged,
                        was_selected_final = excluded.was_selected_final
                    """,
                    data,
                )
        except Exception:
            conn.rollback()
            raise
        conn.commit()

    def ensure_default_collection(self, tenant_id: str, workspace_id: str) -> str:
        collection_id = f"{tenant_id}:{workspace_id}:local-default-resume-corpus"
        row_count = self.connect().execute(
            """
            SELECT COUNT(*)
            FROM corpus_memberships
            WHERE tenant_id = ? AND workspace_id = ? AND corpus_collection_id = ?
            """,
            (tenant_id, workspace_id, collection_id),
        ).fetchone()[0]
        now = utc_now()
        self.connect().execute(
            """
            INSERT INTO corpus_collections (
                corpus_collection_id, tenant_id, workspace_id, name, description,
                mutable, builder_version, builder_config_json, row_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, workspace_id, corpus_collection_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                mutable = excluded.mutable,
                builder_version = excluded.builder_version,
                builder_config_json = excluded.builder_config_json,
                row_count = excluded.row_count,
                updated_at = excluded.updated_at
            """,
            (
                collection_id,
                tenant_id,
                workspace_id,
                "Local Default Resume Corpus",
                "Mutable local resume corpus for this workspace.",
                1,
                "corpus-store-v1",
                canonical_json(
                    {
                        "collection": "local-default-resume-corpus",
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                    }
                ),
                row_count,
                now,
                now,
            ),
        )
        self.connect().commit()
        return collection_id

    def add_corpus_membership(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        corpus_collection_id: str,
        resume_doc_id: str,
        added_by_observation_id: str | None,
        inclusion_reason: str,
    ) -> None:
        self.connect().execute(
            """
            INSERT INTO corpus_memberships (
                tenant_id, workspace_id, corpus_collection_id, resume_doc_id,
                added_by_observation_id, inclusion_reason, included_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, workspace_id, corpus_collection_id, resume_doc_id) DO UPDATE SET
                added_by_observation_id = COALESCE(
                    corpus_memberships.added_by_observation_id,
                    excluded.added_by_observation_id
                ),
                inclusion_reason = excluded.inclusion_reason
            """,
            (
                tenant_id,
                workspace_id,
                corpus_collection_id,
                resume_doc_id,
                added_by_observation_id,
                inclusion_reason,
                utc_now(),
            ),
        )
        self.connect().commit()

    def record_corpus_export(
        self,
        *,
        corpus_export_id: str,
        tenant_id: str,
        workspace_id: str,
        corpus_collection_id: str,
        artifact_ref_id: str,
        builder_version: str,
        builder_config: dict[str, Any],
        source_query: str,
        source_run_ids: list[str],
        row_count: int,
        sha256_value: str,
    ) -> None:
        builder_config_json = canonical_json(builder_config)
        source_run_ids_json = canonical_json(source_run_ids)
        self.connect().execute(
            """
            INSERT INTO corpus_exports (
                corpus_export_id, tenant_id, workspace_id, corpus_collection_id,
                artifact_ref_id, builder_version, builder_config_hash, builder_config_json,
                source_query, source_run_ids_json, row_count, sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(corpus_export_id) DO UPDATE SET
                artifact_ref_id = excluded.artifact_ref_id,
                builder_version = excluded.builder_version,
                builder_config_hash = excluded.builder_config_hash,
                builder_config_json = excluded.builder_config_json,
                source_query = excluded.source_query,
                source_run_ids_json = excluded.source_run_ids_json,
                row_count = excluded.row_count,
                sha256 = excluded.sha256
            """,
            (
                corpus_export_id,
                tenant_id,
                workspace_id,
                corpus_collection_id,
                artifact_ref_id,
                builder_version,
                sha256(builder_config_json.encode("utf-8")).hexdigest(),
                builder_config_json,
                source_query,
                source_run_ids_json,
                row_count,
                sha256_value,
                utc_now(),
            ),
        )
        self.connect().commit()

    def rows_for_tenant(self, table: str, tenant_id: str, workspace_id: str) -> list[dict[str, Any]]:
        order_by = _TENANT_TABLE_ORDER_BY.get(table)
        if order_by is None:
            raise ValueError(f"unsupported corpus tenant table: {table}")
        rows = self.connect().execute(
            f"""
            SELECT *
            FROM {table}
            WHERE tenant_id = ? AND workspace_id = ?
            ORDER BY {order_by}
            """,
            (tenant_id, workspace_id),
        ).fetchall()
        return [dict(row) for row in rows]
