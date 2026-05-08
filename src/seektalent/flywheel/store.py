from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

FLYWHEEL_SCHEMA_VERSION = "flywheel-schema-v1"
FLYWHEEL_TASK_SCHEMA_VERSION = "task-v1"
FLYWHEEL_LABEL_SCHEMA_VERSION = "judge-label-v1"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def task_sha256(*, job_title: str, jd: str, notes: str) -> str:
    payload = {
        "task_schema_version": FLYWHEEL_TASK_SCHEMA_VERSION,
        "job_title": job_title,
        "jd_text": jd,
        "notes_text": notes,
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _add_column_if_missing(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def build_judge_contract_hash(
    *,
    judge_model_id: str,
    judge_protocol_family: str,
    judge_provider_label: str,
    judge_endpoint_kind: str,
    structured_output_mode: str,
    judge_prompt_hash: str,
    judge_policy_version: str,
    label_schema_version: str,
    judge_output_schema_hash: str,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
) -> str:
    payload = {
        "judge_model_id": judge_model_id,
        "judge_protocol_family": judge_protocol_family,
        "judge_provider_label": judge_provider_label,
        "judge_endpoint_kind": judge_endpoint_kind,
        "structured_output_mode": structured_output_mode,
        "judge_prompt_hash": judge_prompt_hash,
        "judge_policy_version": judge_policy_version,
        "label_schema_version": label_schema_version,
        "judge_output_schema_hash": judge_output_schema_hash,
        "reasoning_effort": reasoning_effort,
        "temperature": temperature,
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        task_sha256 TEXT NOT NULL UNIQUE,
        task_schema_version TEXT NOT NULL,
        jd_sha256 TEXT NOT NULL,
        notes_sha256 TEXT NOT NULL,
        job_title TEXT NOT NULL,
        jd_text TEXT NOT NULL,
        notes_text TEXT NOT NULL,
        created_at TEXT NOT NULL
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS resume_snapshots (
        snapshot_sha256 TEXT PRIMARY KEY,
        source_resume_id TEXT,
        dedup_key TEXT,
        raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
        normalized_preview_json TEXT CHECK(normalized_preview_json IS NULL OR json_valid(normalized_preview_json)),
        created_at TEXT NOT NULL
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_refs (
        artifact_ref_id TEXT PRIMARY KEY,
        artifact_kind TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        artifact_root TEXT NOT NULL,
        logical_name TEXT NOT NULL,
        relative_path TEXT,
        content_sha256 TEXT,
        schema_version TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(artifact_kind, artifact_id, logical_name)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        version TEXT,
        git_sha TEXT,
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        artifact_root TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        config_json TEXT NOT NULL CHECK(json_valid(config_json)),
        status TEXT NOT NULL,
        eval_enabled INTEGER NOT NULL,
        benchmark_id TEXT,
        benchmark_case_id TEXT,
        failure_summary TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS run_queries (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        query_instance_id TEXT NOT NULL,
        query_fingerprint TEXT NOT NULL,
        round_no INTEGER NOT NULL,
        lane_type TEXT NOT NULL,
        query_role TEXT,
        canonical_query_spec_json TEXT NOT NULL CHECK(json_valid(canonical_query_spec_json)),
        query_spec_schema_version TEXT NOT NULL,
        query_policy_version TEXT NOT NULL,
        job_intent_fingerprint TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        rendered_provider_query TEXT NOT NULL,
        keyword_query TEXT NOT NULL,
        query_terms_json TEXT NOT NULL CHECK(json_valid(query_terms_json)),
        filters_json TEXT NOT NULL CHECK(json_valid(filters_json)),
        location_key TEXT,
        batch_no INTEGER,
        source_plan_version TEXT,
        selected_prf_expression TEXT,
        accepted_prf_term_family_id TEXT,
        fallback_reason TEXT,
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, query_instance_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS query_resume_hits (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        query_instance_id TEXT NOT NULL,
        query_fingerprint TEXT NOT NULL,
        hit_sequence_no INTEGER NOT NULL,
        snapshot_sha256 TEXT REFERENCES resume_snapshots(snapshot_sha256),
        snapshot_missing_reason TEXT,
        resume_id TEXT NOT NULL,
        round_no INTEGER NOT NULL,
        lane_type TEXT NOT NULL,
        location_key TEXT,
        location_type TEXT,
        batch_no INTEGER NOT NULL,
        rank_in_query INTEGER NOT NULL,
        rank_global_in_query INTEGER,
        provider_name TEXT NOT NULL,
        provider_page_no INTEGER,
        provider_fetch_no INTEGER,
        provider_score_if_any REAL,
        dedup_key TEXT,
        was_new_to_pool INTEGER NOT NULL,
        was_duplicate INTEGER NOT NULL,
        scored_fit_bucket TEXT,
        overall_score REAL,
        must_have_match_score REAL,
        risk_score REAL,
        off_intent_reason_count INTEGER NOT NULL DEFAULT 0,
        final_candidate_status TEXT,
        score_evidence_source TEXT,
        card_scorecard_ref TEXT,
        detail_scorecard_ref TEXT,
        score_delta INTEGER,
        detail_open_reason TEXT,
        detail_open_policy_version TEXT,
        created_at TEXT NOT NULL,
        CHECK(snapshot_sha256 IS NOT NULL OR snapshot_missing_reason IS NOT NULL),
        PRIMARY KEY (run_id, query_instance_id, hit_sequence_no),
        FOREIGN KEY (run_id, query_instance_id) REFERENCES run_queries(run_id, query_instance_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS judge_labels (
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        snapshot_sha256 TEXT NOT NULL REFERENCES resume_snapshots(snapshot_sha256),
        judge_model_id TEXT NOT NULL,
        judge_prompt_hash TEXT NOT NULL,
        judge_contract_hash TEXT NOT NULL,
        judge_protocol_family TEXT NOT NULL,
        judge_provider_label TEXT NOT NULL,
        judge_endpoint_kind TEXT NOT NULL,
        structured_output_mode TEXT NOT NULL,
        judge_policy_version TEXT NOT NULL,
        label_schema_version TEXT NOT NULL,
        judge_output_schema_hash TEXT NOT NULL,
        reasoning_effort TEXT,
        temperature REAL,
        score INTEGER NOT NULL,
        rationale TEXT NOT NULL,
        label_json TEXT NOT NULL CHECK(json_valid(label_json)),
        judge_prompt_text TEXT,
        judge_output_schema_json TEXT CHECK(judge_output_schema_json IS NULL OR json_valid(judge_output_schema_json)),
        latency_ms INTEGER,
        judge_call_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (task_id, snapshot_sha256, judge_contract_hash, label_schema_version)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS query_outcomes (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        query_instance_id TEXT NOT NULL,
        query_fingerprint TEXT NOT NULL,
        outcome_schema_version TEXT NOT NULL,
        outcome_policy_version TEXT NOT NULL,
        outcome_thresholds_hash TEXT NOT NULL,
        outcome_thresholds_json TEXT NOT NULL CHECK(json_valid(outcome_thresholds_json)),
        scoring_policy_version TEXT,
        dedupe_version TEXT,
        outcome_basis TEXT NOT NULL,
        round_no INTEGER NOT NULL,
        lane_type TEXT NOT NULL,
        provider_returned_count INTEGER NOT NULL,
        new_unique_resume_count INTEGER NOT NULL,
        duplicate_count INTEGER NOT NULL,
        scored_resume_count INTEGER NOT NULL,
        new_fit_count INTEGER NOT NULL,
        new_near_fit_count INTEGER NOT NULL,
        fit_rate_denominator TEXT,
        fit_rate REAL,
        must_have_match_avg REAL,
        risk_score_avg REAL,
        off_intent_reason_count INTEGER NOT NULL,
        primary_label TEXT NOT NULL,
        labels_json TEXT NOT NULL CHECK(json_valid(labels_json)),
        reasons_json TEXT NOT NULL CHECK(json_valid(reasons_json)),
        latency_ms INTEGER,
        cost_estimate_usd REAL,
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, query_instance_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS query_judge_outcomes (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        query_instance_id TEXT NOT NULL,
        query_fingerprint TEXT NOT NULL,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        judge_contract_hash TEXT NOT NULL,
        judge_model_id TEXT NOT NULL,
        judge_prompt_hash TEXT NOT NULL,
        label_schema_version TEXT NOT NULL,
        outcome_schema_version TEXT NOT NULL,
        outcome_policy_version TEXT NOT NULL,
        outcome_thresholds_hash TEXT NOT NULL,
        outcome_thresholds_json TEXT NOT NULL CHECK(json_valid(outcome_thresholds_json)),
        provider_returned_count INTEGER NOT NULL,
        new_unique_resume_count INTEGER NOT NULL,
        judged_resume_count INTEGER NOT NULL,
        new_judge_positive_count INTEGER NOT NULL,
        new_judge_near_positive_count INTEGER NOT NULL,
        judge_positive_rate REAL,
        duplicate_count INTEGER NOT NULL,
        primary_label TEXT NOT NULL,
        labels_json TEXT NOT NULL CHECK(json_valid(labels_json)),
        reasons_json TEXT NOT NULL CHECK(json_valid(reasons_json)),
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, query_instance_id, judge_contract_hash)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS term_events (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        term_event_id TEXT NOT NULL,
        proposal_id TEXT,
        prf_decision_id TEXT,
        prf_candidate_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        prf_policy_decision_artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        prf_proposal_extractor_version TEXT,
        prf_familying_version TEXT,
        prf_gate_version TEXT,
        candidate_query_fingerprint TEXT,
        executed_query_instance_id TEXT,
        selected_query_instance_id TEXT,
        term_surface TEXT NOT NULL,
        term_family_id TEXT NOT NULL,
        term_role TEXT NOT NULL,
        source TEXT NOT NULL,
        round_no INTEGER NOT NULL,
        lane_type TEXT,
        accepted_by_prf_gate INTEGER,
        prf_reject_reasons_json TEXT CHECK(prf_reject_reasons_json IS NULL OR json_valid(prf_reject_reasons_json)),
        supporting_resume_ids_json TEXT CHECK(supporting_resume_ids_json IS NULL OR json_valid(supporting_resume_ids_json)),
        negative_resume_ids_json TEXT CHECK(negative_resume_ids_json IS NULL OR json_valid(negative_resume_ids_json)),
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, term_event_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS term_outcomes (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        term_event_id TEXT NOT NULL,
        term_family_id TEXT NOT NULL,
        term_outcome_schema_version TEXT NOT NULL,
        term_familying_version TEXT NOT NULL,
        prf_gate_version TEXT,
        prf_policy_version TEXT,
        execution_status TEXT NOT NULL,
        runtime_outcome_json TEXT CHECK(runtime_outcome_json IS NULL OR json_valid(runtime_outcome_json)),
        judge_outcome_json TEXT CHECK(judge_outcome_json IS NULL OR json_valid(judge_outcome_json)),
        labels_json TEXT NOT NULL CHECK(json_valid(labels_json)),
        reasons_json TEXT NOT NULL CHECK(json_valid(reasons_json)),
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, term_event_id)
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS query_rewrite_samples (
        sample_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id),
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        source_query_instance_ids_json TEXT NOT NULL CHECK(json_valid(source_query_instance_ids_json)),
        sample_basis TEXT NOT NULL,
        input_json TEXT NOT NULL CHECK(json_valid(input_json)),
        target_json TEXT NOT NULL CHECK(json_valid(target_json)),
        reward_json TEXT NOT NULL CHECK(json_valid(reward_json)),
        schema_version TEXT NOT NULL,
        dataset_version TEXT NOT NULL,
        builder_version TEXT NOT NULL,
        builder_config_hash TEXT NOT NULL,
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        created_at TEXT NOT NULL
    ){strict}
    """,
    """
    CREATE TABLE IF NOT EXISTS dataset_exports (
        export_id TEXT PRIMARY KEY,
        dataset_name TEXT NOT NULL,
        dataset_version TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        builder_version TEXT NOT NULL,
        builder_config_hash TEXT NOT NULL,
        builder_config_json TEXT NOT NULL CHECK(json_valid(builder_config_json)),
        source_db_sha256 TEXT NOT NULL,
        source_run_ids_json TEXT NOT NULL CHECK(json_valid(source_run_ids_json)),
        source_query TEXT NOT NULL,
        source_artifact_refs_json TEXT NOT NULL CHECK(json_valid(source_artifact_refs_json)),
        git_sha TEXT,
        artifact_root TEXT NOT NULL,
        output_path TEXT NOT NULL,
        artifact_ref_id TEXT REFERENCES artifact_refs(artifact_ref_id),
        row_count INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    ){strict}
    """,
]

ALLOWED_RUN_ROW_TABLES = {
    "query_outcomes",
    "query_judge_outcomes",
    "term_events",
    "term_outcomes",
}
ALLOWED_EXPORT_ROW_TABLES = {*ALLOWED_RUN_ROW_TABLES, "query_rewrite_samples"}


class FlywheelStore:
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
            conn.execute("CREATE TEMP TABLE __flywheel_strict_probe (value TEXT) STRICT")
            conn.execute("DROP TABLE __flywheel_strict_probe")
        except sqlite3.OperationalError:
            return ""
        return " STRICT"

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT json_valid(?)", ("{}",)).fetchone()[0] != 1:
            raise RuntimeError("SQLite JSON1 support is required for FlywheelStore")
        conn.execute("PRAGMA user_version = 1")
        strict_suffix = self._strict_suffix(conn)
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement.format(strict=strict_suffix))
        _add_column_if_missing(conn, "query_resume_hits", "score_evidence_source", "TEXT")
        _add_column_if_missing(conn, "query_resume_hits", "card_scorecard_ref", "TEXT")
        _add_column_if_missing(conn, "query_resume_hits", "detail_scorecard_ref", "TEXT")
        _add_column_if_missing(conn, "query_resume_hits", "score_delta", "INTEGER")
        _add_column_if_missing(conn, "query_resume_hits", "detail_open_reason", "TEXT")
        _add_column_if_missing(conn, "query_resume_hits", "detail_open_policy_version", "TEXT")
        conn.execute(
            """
            INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (FLYWHEEL_SCHEMA_VERSION,),
        )
        conn.commit()

    def upsert_task(self, *, job_title: str, jd_text: str, notes_text: str) -> str:
        task_id = task_sha256(job_title=job_title, jd=jd_text, notes=notes_text)
        now = utc_now()
        self.connect().execute(
            """
            INSERT INTO tasks (
                task_id, task_sha256, task_schema_version, jd_sha256,
                notes_sha256, job_title, jd_text, notes_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                job_title = excluded.job_title,
                jd_text = excluded.jd_text,
                notes_text = excluded.notes_text
            """,
            (
                task_id,
                task_id,
                FLYWHEEL_TASK_SCHEMA_VERSION,
                sha256(jd_text.encode("utf-8")).hexdigest(),
                sha256(notes_text.encode("utf-8")).hexdigest(),
                job_title,
                jd_text,
                notes_text,
                now,
            ),
        )
        self.connect().commit()
        return task_id

    def upsert_resume_snapshot(
        self,
        *,
        snapshot_sha256: str,
        source_resume_id: str | None,
        dedup_key: str | None,
        raw_payload: dict[str, Any],
        normalized_preview: dict[str, Any] | None = None,
    ) -> None:
        self.connect().execute(
            """
            INSERT INTO resume_snapshots (
                snapshot_sha256, source_resume_id, dedup_key, raw_json,
                normalized_preview_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_sha256) DO UPDATE SET
                source_resume_id = excluded.source_resume_id,
                dedup_key = excluded.dedup_key,
                raw_json = excluded.raw_json,
                normalized_preview_json = excluded.normalized_preview_json
            """,
            (
                snapshot_sha256,
                source_resume_id,
                dedup_key,
                canonical_json(raw_payload),
                canonical_json(normalized_preview) if normalized_preview is not None else None,
                utc_now(),
            ),
        )
        self.connect().commit()

    def resume_snapshot_exists(self, snapshot_sha256: str) -> bool:
        row = self.connect().execute(
            "SELECT 1 FROM resume_snapshots WHERE snapshot_sha256 = ?",
            (snapshot_sha256,),
        ).fetchone()
        return row is not None

    def start_run(
        self,
        *,
        run_id: str,
        task_id: str,
        version: str | None,
        git_sha: str | None,
        artifact_ref_id: str | None,
        artifact_root: str,
        config_hash: str,
        config_payload: dict[str, Any],
        status: str,
        eval_enabled: bool,
        benchmark_id: str | None,
        benchmark_case_id: str | None,
    ) -> None:
        self.connect().execute(
            """
            INSERT INTO runs (
                run_id, task_id, version, git_sha, artifact_ref_id, artifact_root,
                config_hash, config_json, status, eval_enabled, benchmark_id,
                benchmark_case_id, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                config_hash = excluded.config_hash,
                config_json = excluded.config_json,
                eval_enabled = excluded.eval_enabled
            """,
            (
                run_id,
                task_id,
                version,
                git_sha,
                artifact_ref_id,
                artifact_root,
                config_hash,
                canonical_json(config_payload),
                status,
                int(eval_enabled),
                benchmark_id,
                benchmark_case_id,
                utc_now(),
            ),
        )
        self.connect().commit()

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
        artifact_ref_id = sha256(
            canonical_json(
                {
                    "artifact_kind": artifact_kind,
                    "artifact_id": artifact_id,
                    "logical_name": logical_name,
                }
            ).encode("utf-8")
        ).hexdigest()
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
                relative_path,
                content_sha256,
                schema_version,
                utc_now(),
            ),
        )
        self.connect().commit()
        return artifact_ref_id

    def complete_run(self, *, run_id: str, status: str, failure_summary: str | None = None) -> None:
        self.connect().execute(
            """
            UPDATE runs
            SET status = ?, failure_summary = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (status, failure_summary, utc_now(), run_id),
        )
        self.connect().commit()

    def record_run_queries(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        now = utc_now()
        values = [
            (
                row["run_id"],
                row["query_instance_id"],
                row["query_fingerprint"],
                row["round_no"],
                row["lane_type"],
                row.get("query_role"),
                row["canonical_query_spec_json"],
                row["query_spec_schema_version"],
                row["query_policy_version"],
                row["job_intent_fingerprint"],
                row["provider_name"],
                row["rendered_provider_query"],
                row["keyword_query"],
                row["query_terms_json"],
                row["filters_json"],
                row.get("location_key"),
                row.get("batch_no"),
                row.get("source_plan_version"),
                row.get("selected_prf_expression"),
                row.get("accepted_prf_term_family_id"),
                row.get("fallback_reason"),
                row.get("artifact_ref_id"),
                now,
            )
            for row in rows
        ]
        conn = self.connect()
        with conn:
            conn.executemany(
                """
                INSERT INTO run_queries (
                    run_id, query_instance_id, query_fingerprint, round_no,
                    lane_type, query_role, canonical_query_spec_json,
                    query_spec_schema_version, query_policy_version,
                    job_intent_fingerprint, provider_name, rendered_provider_query,
                    keyword_query, query_terms_json, filters_json, location_key,
                    batch_no, source_plan_version, selected_prf_expression,
                    accepted_prf_term_family_id, fallback_reason, artifact_ref_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, query_instance_id) DO UPDATE SET
                    query_fingerprint = excluded.query_fingerprint,
                    round_no = excluded.round_no,
                    lane_type = excluded.lane_type,
                    query_role = excluded.query_role,
                    canonical_query_spec_json = excluded.canonical_query_spec_json,
                    query_spec_schema_version = excluded.query_spec_schema_version,
                    query_policy_version = excluded.query_policy_version,
                    job_intent_fingerprint = excluded.job_intent_fingerprint,
                    provider_name = excluded.provider_name,
                    rendered_provider_query = excluded.rendered_provider_query,
                    keyword_query = excluded.keyword_query,
                    query_terms_json = excluded.query_terms_json,
                    filters_json = excluded.filters_json,
                    location_key = excluded.location_key,
                    batch_no = excluded.batch_no,
                    source_plan_version = excluded.source_plan_version,
                    selected_prf_expression = excluded.selected_prf_expression,
                    accepted_prf_term_family_id = excluded.accepted_prf_term_family_id,
                    fallback_reason = excluded.fallback_reason,
                    artifact_ref_id = excluded.artifact_ref_id
                """,
                values,
            )

    def record_query_resume_hits(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        now = utc_now()
        values = [
            (
                row["run_id"],
                row["query_instance_id"],
                row["query_fingerprint"],
                row["hit_sequence_no"],
                row.get("snapshot_sha256"),
                row.get("snapshot_missing_reason"),
                row["resume_id"],
                row["round_no"],
                row["lane_type"],
                row.get("location_key"),
                row.get("location_type"),
                row["batch_no"],
                row["rank_in_query"],
                row.get("rank_global_in_query"),
                row["provider_name"],
                row.get("provider_page_no"),
                row.get("provider_fetch_no"),
                row.get("provider_score_if_any"),
                row.get("dedup_key"),
                int(bool(row["was_new_to_pool"])),
                int(bool(row["was_duplicate"])),
                row.get("scored_fit_bucket"),
                row.get("overall_score"),
                row.get("must_have_match_score"),
                row.get("risk_score"),
                row.get("off_intent_reason_count") or 0,
                row.get("final_candidate_status"),
                row.get("score_evidence_source"),
                row.get("card_scorecard_ref"),
                row.get("detail_scorecard_ref"),
                row.get("score_delta"),
                row.get("detail_open_reason"),
                row.get("detail_open_policy_version"),
                now,
            )
            for row in rows
        ]
        conn = self.connect()
        with conn:
            conn.executemany(
                """
                INSERT INTO query_resume_hits (
                    run_id, query_instance_id, query_fingerprint, hit_sequence_no,
                    snapshot_sha256, snapshot_missing_reason, resume_id, round_no,
                    lane_type, location_key, location_type, batch_no, rank_in_query,
                    rank_global_in_query, provider_name, provider_page_no,
                    provider_fetch_no, provider_score_if_any, dedup_key,
                    was_new_to_pool, was_duplicate, scored_fit_bucket,
                    overall_score, must_have_match_score, risk_score,
                    off_intent_reason_count, final_candidate_status,
                    score_evidence_source, card_scorecard_ref, detail_scorecard_ref,
                    score_delta, detail_open_reason, detail_open_policy_version,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, query_instance_id, hit_sequence_no) DO UPDATE SET
                    query_fingerprint = excluded.query_fingerprint,
                    snapshot_sha256 = excluded.snapshot_sha256,
                    snapshot_missing_reason = excluded.snapshot_missing_reason,
                    resume_id = excluded.resume_id,
                    round_no = excluded.round_no,
                    lane_type = excluded.lane_type,
                    location_key = excluded.location_key,
                    location_type = excluded.location_type,
                    batch_no = excluded.batch_no,
                    rank_in_query = excluded.rank_in_query,
                    rank_global_in_query = excluded.rank_global_in_query,
                    provider_name = excluded.provider_name,
                    provider_page_no = excluded.provider_page_no,
                    provider_fetch_no = excluded.provider_fetch_no,
                    provider_score_if_any = excluded.provider_score_if_any,
                    dedup_key = excluded.dedup_key,
                    was_new_to_pool = excluded.was_new_to_pool,
                    was_duplicate = excluded.was_duplicate,
                    scored_fit_bucket = excluded.scored_fit_bucket,
                    overall_score = excluded.overall_score,
                    must_have_match_score = excluded.must_have_match_score,
                    risk_score = excluded.risk_score,
                    off_intent_reason_count = excluded.off_intent_reason_count,
                    final_candidate_status = excluded.final_candidate_status,
                    score_evidence_source = excluded.score_evidence_source,
                    card_scorecard_ref = excluded.card_scorecard_ref,
                    detail_scorecard_ref = excluded.detail_scorecard_ref,
                    score_delta = excluded.score_delta,
                    detail_open_reason = excluded.detail_open_reason,
                    detail_open_policy_version = excluded.detail_open_policy_version
                """,
                values,
            )

    def query_hits_for_run_round(self, *, run_id: str, round_no: int) -> list[dict[str, Any]]:
        rows = self.connect().execute(
            """
            SELECT *
            FROM query_resume_hits
            WHERE run_id = ? AND round_no = ?
            ORDER BY query_instance_id, hit_sequence_no
            """,
            (run_id, round_no),
        ).fetchall()
        return [dict(row) for row in rows]

    def query_hits_for_run(self, *, run_id: str) -> list[dict[str, Any]]:
        rows = self.connect().execute(
            """
            SELECT *
            FROM query_resume_hits
            WHERE run_id = ?
            ORDER BY round_no, query_instance_id, hit_sequence_no
            """,
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_query_outcomes(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        now = utc_now()
        values = []
        for row in rows:
            if "created_at" in row:
                raise ValueError("created_at is owned by FlywheelStore")
            values.append({**row, "created_at": now})
        conn = self.connect()
        with conn:
            conn.executemany(
                """
                INSERT INTO query_outcomes (
                    run_id, query_instance_id, query_fingerprint, outcome_schema_version,
                    outcome_policy_version, outcome_thresholds_hash, outcome_thresholds_json,
                    scoring_policy_version, dedupe_version, outcome_basis, round_no, lane_type,
                    provider_returned_count, new_unique_resume_count, duplicate_count,
                    scored_resume_count, new_fit_count, new_near_fit_count, fit_rate_denominator,
                    fit_rate, must_have_match_avg, risk_score_avg, off_intent_reason_count,
                    primary_label, labels_json, reasons_json, latency_ms, cost_estimate_usd,
                    artifact_ref_id, created_at
                ) VALUES (
                    :run_id, :query_instance_id, :query_fingerprint, :outcome_schema_version,
                    :outcome_policy_version, :outcome_thresholds_hash, :outcome_thresholds_json,
                    :scoring_policy_version, :dedupe_version, :outcome_basis, :round_no, :lane_type,
                    :provider_returned_count, :new_unique_resume_count, :duplicate_count,
                    :scored_resume_count, :new_fit_count, :new_near_fit_count, :fit_rate_denominator,
                    :fit_rate, :must_have_match_avg, :risk_score_avg, :off_intent_reason_count,
                    :primary_label, :labels_json, :reasons_json, :latency_ms, :cost_estimate_usd,
                    :artifact_ref_id, :created_at
                )
                ON CONFLICT(run_id, query_instance_id) DO UPDATE SET
                    query_fingerprint = excluded.query_fingerprint,
                    outcome_schema_version = excluded.outcome_schema_version,
                    outcome_policy_version = excluded.outcome_policy_version,
                    outcome_thresholds_hash = excluded.outcome_thresholds_hash,
                    outcome_thresholds_json = excluded.outcome_thresholds_json,
                    scoring_policy_version = excluded.scoring_policy_version,
                    dedupe_version = excluded.dedupe_version,
                    outcome_basis = excluded.outcome_basis,
                    provider_returned_count = excluded.provider_returned_count,
                    new_unique_resume_count = excluded.new_unique_resume_count,
                    duplicate_count = excluded.duplicate_count,
                    scored_resume_count = excluded.scored_resume_count,
                    new_fit_count = excluded.new_fit_count,
                    new_near_fit_count = excluded.new_near_fit_count,
                    fit_rate_denominator = excluded.fit_rate_denominator,
                    fit_rate = excluded.fit_rate,
                    must_have_match_avg = excluded.must_have_match_avg,
                    risk_score_avg = excluded.risk_score_avg,
                    off_intent_reason_count = excluded.off_intent_reason_count,
                    primary_label = excluded.primary_label,
                    labels_json = excluded.labels_json,
                    reasons_json = excluded.reasons_json,
                    latency_ms = excluded.latency_ms,
                    cost_estimate_usd = excluded.cost_estimate_usd
                """,
                values,
            )

    def record_query_judge_outcomes(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        now = utc_now()
        values = []
        for row in rows:
            if "created_at" in row:
                raise ValueError("created_at is owned by FlywheelStore")
            values.append({**row, "created_at": now})
        conn = self.connect()
        with conn:
            conn.executemany(
                """
                INSERT INTO query_judge_outcomes (
                    run_id, query_instance_id, query_fingerprint, task_id,
                    judge_contract_hash, judge_model_id, judge_prompt_hash,
                    label_schema_version, outcome_schema_version, outcome_policy_version,
                    outcome_thresholds_hash, outcome_thresholds_json,
                    provider_returned_count, new_unique_resume_count,
                    judged_resume_count, new_judge_positive_count,
                    new_judge_near_positive_count, judge_positive_rate,
                    duplicate_count, primary_label, labels_json, reasons_json,
                    artifact_ref_id, created_at
                ) VALUES (
                    :run_id, :query_instance_id, :query_fingerprint, :task_id,
                    :judge_contract_hash, :judge_model_id, :judge_prompt_hash,
                    :label_schema_version, :outcome_schema_version, :outcome_policy_version,
                    :outcome_thresholds_hash, :outcome_thresholds_json,
                    :provider_returned_count, :new_unique_resume_count,
                    :judged_resume_count, :new_judge_positive_count,
                    :new_judge_near_positive_count, :judge_positive_rate,
                    :duplicate_count, :primary_label, :labels_json, :reasons_json,
                    :artifact_ref_id, :created_at
                )
                ON CONFLICT(run_id, query_instance_id, judge_contract_hash) DO UPDATE SET
                    query_fingerprint = excluded.query_fingerprint,
                    judge_model_id = excluded.judge_model_id,
                    judge_prompt_hash = excluded.judge_prompt_hash,
                    label_schema_version = excluded.label_schema_version,
                    outcome_schema_version = excluded.outcome_schema_version,
                    outcome_policy_version = excluded.outcome_policy_version,
                    outcome_thresholds_hash = excluded.outcome_thresholds_hash,
                    outcome_thresholds_json = excluded.outcome_thresholds_json,
                    provider_returned_count = excluded.provider_returned_count,
                    new_unique_resume_count = excluded.new_unique_resume_count,
                    judged_resume_count = excluded.judged_resume_count,
                    new_judge_positive_count = excluded.new_judge_positive_count,
                    new_judge_near_positive_count = excluded.new_judge_near_positive_count,
                    judge_positive_rate = excluded.judge_positive_rate,
                    duplicate_count = excluded.duplicate_count,
                    primary_label = excluded.primary_label,
                    labels_json = excluded.labels_json,
                    reasons_json = excluded.reasons_json
                """,
                values,
            )

    def record_term_events(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        now = utc_now()
        values = []
        for row in rows:
            if "created_at" in row:
                raise ValueError("created_at is owned by FlywheelStore")
            values.append({**row, "created_at": now})
        conn = self.connect()
        with conn:
            conn.executemany(
                """
                INSERT INTO term_events (
                    run_id, term_event_id, proposal_id, prf_decision_id,
                    prf_candidate_artifact_ref_id, prf_policy_decision_artifact_ref_id,
                    prf_proposal_extractor_version, prf_familying_version,
                    prf_gate_version, candidate_query_fingerprint,
                    executed_query_instance_id, selected_query_instance_id,
                    term_surface, term_family_id, term_role, source, round_no,
                    lane_type, accepted_by_prf_gate, prf_reject_reasons_json,
                    supporting_resume_ids_json, negative_resume_ids_json,
                    artifact_ref_id, created_at
                ) VALUES (
                    :run_id, :term_event_id, :proposal_id, :prf_decision_id,
                    :prf_candidate_artifact_ref_id, :prf_policy_decision_artifact_ref_id,
                    :prf_proposal_extractor_version, :prf_familying_version,
                    :prf_gate_version, :candidate_query_fingerprint,
                    :executed_query_instance_id, :selected_query_instance_id,
                    :term_surface, :term_family_id, :term_role, :source, :round_no,
                    :lane_type, :accepted_by_prf_gate, :prf_reject_reasons_json,
                    :supporting_resume_ids_json, :negative_resume_ids_json,
                    :artifact_ref_id, :created_at
                )
                ON CONFLICT(run_id, term_event_id) DO UPDATE SET
                    proposal_id = excluded.proposal_id,
                    prf_decision_id = excluded.prf_decision_id,
                    prf_candidate_artifact_ref_id = excluded.prf_candidate_artifact_ref_id,
                    prf_policy_decision_artifact_ref_id = excluded.prf_policy_decision_artifact_ref_id,
                    prf_proposal_extractor_version = excluded.prf_proposal_extractor_version,
                    prf_familying_version = excluded.prf_familying_version,
                    prf_gate_version = excluded.prf_gate_version,
                    candidate_query_fingerprint = excluded.candidate_query_fingerprint,
                    executed_query_instance_id = excluded.executed_query_instance_id,
                    selected_query_instance_id = excluded.selected_query_instance_id,
                    term_surface = excluded.term_surface,
                    term_family_id = excluded.term_family_id,
                    term_role = excluded.term_role,
                    source = excluded.source,
                    round_no = excluded.round_no,
                    lane_type = excluded.lane_type,
                    accepted_by_prf_gate = excluded.accepted_by_prf_gate,
                    prf_reject_reasons_json = excluded.prf_reject_reasons_json,
                    supporting_resume_ids_json = excluded.supporting_resume_ids_json,
                    negative_resume_ids_json = excluded.negative_resume_ids_json
                """,
                values,
            )

    def record_term_outcomes(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        now = utc_now()
        values = []
        for row in rows:
            if "created_at" in row:
                raise ValueError("created_at is owned by FlywheelStore")
            values.append({**row, "created_at": now})
        conn = self.connect()
        with conn:
            conn.executemany(
                """
                INSERT INTO term_outcomes (
                    run_id, term_event_id, term_family_id, term_outcome_schema_version,
                    term_familying_version, prf_gate_version, prf_policy_version,
                    execution_status, runtime_outcome_json, judge_outcome_json,
                    labels_json, reasons_json, artifact_ref_id, created_at
                ) VALUES (
                    :run_id, :term_event_id, :term_family_id, :term_outcome_schema_version,
                    :term_familying_version, :prf_gate_version, :prf_policy_version,
                    :execution_status, :runtime_outcome_json, :judge_outcome_json,
                    :labels_json, :reasons_json, :artifact_ref_id, :created_at
                )
                ON CONFLICT(run_id, term_event_id) DO UPDATE SET
                    term_family_id = excluded.term_family_id,
                    term_outcome_schema_version = excluded.term_outcome_schema_version,
                    term_familying_version = excluded.term_familying_version,
                    prf_gate_version = excluded.prf_gate_version,
                    prf_policy_version = excluded.prf_policy_version,
                    execution_status = excluded.execution_status,
                    runtime_outcome_json = excluded.runtime_outcome_json,
                    judge_outcome_json = excluded.judge_outcome_json,
                    labels_json = excluded.labels_json,
                    reasons_json = excluded.reasons_json
                """,
                values,
            )

    def rows_for_run(self, table: str, *, run_id: str) -> list[dict[str, Any]]:
        if table not in ALLOWED_RUN_ROW_TABLES:
            raise ValueError(f"unsupported flywheel run table: {table}")
        rows = self.connect().execute(
            f"SELECT * FROM {table} WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def rows_for_runs(self, table: str, *, run_ids: list[str]) -> list[dict[str, Any]]:
        if table not in ALLOWED_EXPORT_ROW_TABLES:
            raise ValueError(f"unsupported flywheel export table: {table}")
        if not run_ids:
            return []
        placeholders = ",".join("?" for _ in run_ids)
        rows = self.connect().execute(
            f"SELECT * FROM {table} WHERE run_id IN ({placeholders}) ORDER BY run_id, rowid",
            tuple(run_ids),
        ).fetchall()
        return [dict(row) for row in rows]

    def attach_artifact_ref_to_run_rows(self, *, table: str, run_id: str, artifact_ref_id: str) -> None:
        if table not in ALLOWED_RUN_ROW_TABLES:
            raise ValueError(f"unsupported flywheel run table: {table}")
        self.connect().execute(
            f"UPDATE {table} SET artifact_ref_id = ? WHERE run_id = ?",
            (artifact_ref_id, run_id),
        )
        self.connect().commit()

    def query_rewrite_source_rows(self, *, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        placeholders = ",".join("?" for _ in run_ids)
        rows = self.connect().execute(
            f"""
            SELECT
                qjo.*,
                tasks.job_title,
                tasks.task_sha256,
                run_queries.keyword_query,
                run_queries.query_terms_json
            FROM query_judge_outcomes AS qjo
            JOIN tasks ON tasks.task_id = qjo.task_id
            LEFT JOIN run_queries
              ON run_queries.run_id = qjo.run_id
             AND run_queries.query_instance_id = qjo.query_instance_id
            WHERE qjo.run_id IN ({placeholders})
            ORDER BY qjo.run_id, qjo.query_instance_id, qjo.judge_contract_hash
            """,
            tuple(run_ids),
        ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            query_terms = json.loads(str(payload.get("query_terms_json") or "[]"))
            keyword_query = payload.get("keyword_query")
            positive = int(payload.get("new_judge_positive_count") or 0) > 0
            payload["query_history"] = [keyword_query] if keyword_query else []
            payload["failed_terms"] = [] if positive else query_terms
            payload["successful_terms"] = query_terms if positive else []
            payload["prf_evidence_summaries"] = []
            payload["top_positive_signals"] = json.loads(str(payload.get("labels_json") or "[]"))
            payload["top_negative_signals"] = json.loads(str(payload.get("reasons_json") or "[]"))
            payload["select_terms"] = query_terms if positive else []
            payload["suppress_terms"] = [] if positive else query_terms
            payload["rank_terms"] = query_terms
            output.append(payload)
        return output

    def source_artifact_refs_for_runs(self, *, run_ids: list[str]) -> list[str]:
        if not run_ids:
            return []
        refs: set[str] = set()
        placeholders = ",".join("?" for _ in run_ids)
        for table in ALLOWED_RUN_ROW_TABLES:
            rows = self.connect().execute(
                f"SELECT artifact_ref_id FROM {table} WHERE run_id IN ({placeholders}) AND artifact_ref_id IS NOT NULL",
                tuple(run_ids),
            ).fetchall()
            refs.update(str(row["artifact_ref_id"]) for row in rows)
        return sorted(refs)

    def record_query_rewrite_samples(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        now = utc_now()
        values = []
        for row in rows:
            if "created_at" in row:
                raise ValueError("created_at is owned by FlywheelStore")
            values.append({**row, "created_at": now})
        conn = self.connect()
        with conn:
            conn.executemany(
                """
                INSERT INTO query_rewrite_samples (
                    sample_id, task_id, run_id, source_query_instance_ids_json,
                    sample_basis, input_json, target_json, reward_json,
                    schema_version, dataset_version, builder_version,
                    builder_config_hash, artifact_ref_id, created_at
                ) VALUES (
                    :sample_id, :task_id, :run_id, :source_query_instance_ids_json,
                    :sample_basis, :input_json, :target_json, :reward_json,
                    :schema_version, :dataset_version, :builder_version,
                    :builder_config_hash, :artifact_ref_id, :created_at
                )
                ON CONFLICT(sample_id) DO UPDATE SET
                    input_json = excluded.input_json,
                    target_json = excluded.target_json,
                    reward_json = excluded.reward_json,
                    dataset_version = excluded.dataset_version,
                    builder_version = excluded.builder_version,
                    builder_config_hash = excluded.builder_config_hash,
                    artifact_ref_id = excluded.artifact_ref_id
                """,
                values,
            )

    def record_dataset_export(
        self,
        *,
        export_id: str,
        dataset_name: str,
        dataset_version: str,
        schema_version: str,
        builder_version: str,
        builder_config: dict[str, Any],
        source_run_ids: list[str],
        source_query: str,
        source_db_sha256: str,
        source_artifact_refs: list[str],
        git_sha: str | None,
        artifact_root: str,
        output_path: str,
        artifact_ref_id: str | None,
        row_count: int,
        sha256_value: str,
    ) -> None:
        builder_config_json = canonical_json(builder_config)
        self.connect().execute(
            """
            INSERT INTO dataset_exports (
                export_id, dataset_name, dataset_version, schema_version,
                builder_version, builder_config_hash, builder_config_json,
                source_db_sha256, source_run_ids_json, source_query,
                source_artifact_refs_json, git_sha, artifact_root, output_path,
                artifact_ref_id, row_count, sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(export_id) DO UPDATE SET
                row_count = excluded.row_count,
                sha256 = excluded.sha256,
                artifact_ref_id = excluded.artifact_ref_id
            """,
            (
                export_id,
                dataset_name,
                dataset_version,
                schema_version,
                builder_version,
                sha256(builder_config_json.encode("utf-8")).hexdigest(),
                builder_config_json,
                source_db_sha256,
                canonical_json(source_run_ids),
                source_query,
                canonical_json(source_artifact_refs),
                git_sha,
                artifact_root,
                output_path,
                artifact_ref_id,
                row_count,
                sha256_value,
                utc_now(),
            ),
        )
        self.connect().commit()

    def record_judge_label(
        self,
        *,
        task_id: str,
        snapshot_sha256: str,
        judge_model_id: str,
        judge_protocol_family: str,
        judge_provider_label: str,
        judge_endpoint_kind: str,
        structured_output_mode: str,
        judge_prompt_hash: str,
        judge_contract_hash: str,
        judge_policy_version: str,
        label_schema_version: str,
        judge_output_schema_hash: str,
        reasoning_effort: str | None,
        temperature: float | None,
        score: int,
        rationale: str,
        label_payload: dict[str, Any],
        judge_prompt_text: str | None,
        latency_ms: int | None = None,
    ) -> None:
        expected_contract = build_judge_contract_hash(
            judge_model_id=judge_model_id,
            judge_protocol_family=judge_protocol_family,
            judge_provider_label=judge_provider_label,
            judge_endpoint_kind=judge_endpoint_kind,
            structured_output_mode=structured_output_mode,
            judge_prompt_hash=judge_prompt_hash,
            judge_policy_version=judge_policy_version,
            label_schema_version=label_schema_version,
            judge_output_schema_hash=judge_output_schema_hash,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
        )
        if judge_contract_hash != expected_contract:
            raise ValueError("judge_contract_hash does not match judge label contract fields")
        now = utc_now()
        self.connect().execute(
            """
            INSERT INTO judge_labels (
                task_id, snapshot_sha256, judge_model_id, judge_prompt_hash,
                judge_contract_hash, judge_protocol_family, judge_provider_label,
                judge_endpoint_kind, structured_output_mode, judge_policy_version,
                label_schema_version, judge_output_schema_hash, reasoning_effort, temperature,
                score, rationale, label_json, judge_prompt_text, latency_ms,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, snapshot_sha256, judge_contract_hash, label_schema_version) DO UPDATE SET
                score = excluded.score,
                rationale = excluded.rationale,
                label_json = excluded.label_json,
                judge_prompt_text = excluded.judge_prompt_text,
                latency_ms = excluded.latency_ms,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                snapshot_sha256,
                judge_model_id,
                judge_prompt_hash,
                judge_contract_hash,
                judge_protocol_family,
                judge_provider_label,
                judge_endpoint_kind,
                structured_output_mode,
                judge_policy_version,
                label_schema_version,
                judge_output_schema_hash,
                reasoning_effort,
                temperature,
                score,
                rationale,
                canonical_json(label_payload),
                judge_prompt_text,
                latency_ms,
                now,
                now,
            ),
        )
        self.connect().commit()

    def get_cached_judge_label(
        self,
        *,
        task_id: str,
        snapshot_sha256: str,
        judge_contract_hash: str,
        label_schema_version: str,
    ) -> dict[str, Any] | None:
        row = self.connect().execute(
            """
            SELECT label_json
            FROM judge_labels
            WHERE task_id = ?
              AND snapshot_sha256 = ?
              AND judge_contract_hash = ?
              AND label_schema_version = ?
            """,
            (task_id, snapshot_sha256, judge_contract_hash, label_schema_version),
        ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["label_json"]))

    def judge_label_cache_summary(self, *, task_id: str, judge_contract_hash: str) -> dict[str, object]:
        row = self.connect().execute(
            """
            SELECT COUNT(*) AS hits
            FROM judge_labels
            WHERE task_id = ?
              AND judge_contract_hash = ?
            """,
            (task_id, judge_contract_hash),
        ).fetchone()
        return {"hits": int(row["hits"]), "contract_hash": judge_contract_hash}
