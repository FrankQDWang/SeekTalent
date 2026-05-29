from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent.config import AppSettings
from seektalent.flywheel.store import (
    FLYWHEEL_LABEL_SCHEMA_VERSION,
    FlywheelStore,
    build_judge_contract_hash,
    task_sha256,
)
from seektalent.storage.json import canonical_json


def test_flywheel_store_creates_tables_and_enables_foreign_keys(tmp_path: Path) -> None:
    store = FlywheelStore(tmp_path / "flywheel.sqlite3")
    try:
        conn = store.connect()
        table_names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        assert {
            "tasks",
            "schema_meta",
            "resume_snapshots",
            "artifact_refs",
            "runs",
            "run_queries",
            "query_resume_hits",
            "judge_labels",
            "query_outcomes",
            "query_judge_outcomes",
            "term_events",
            "term_outcomes",
            "query_rewrite_samples",
            "dataset_exports",
        } <= table_names
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT json_valid(?)", ("{}",)).fetchone()[0] == 1
        assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "flywheel-schema-v1"
    finally:
        store.close()


def test_settings_resolves_flywheel_path(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None, workspace_root=str(tmp_path), flywheel_db_path=".seektalent/flywheel.sqlite3")

    assert settings.flywheel_path == tmp_path / ".seektalent/flywheel.sqlite3"


def test_flywheel_enabled_by_default_only_in_dev(tmp_path: Path) -> None:
    dev_settings = AppSettings(_env_file=None, workspace_root=str(tmp_path))
    prod_settings = AppSettings(_env_file=None, workspace_root=str(tmp_path), runtime_mode="prod")

    assert dev_settings.enable_flywheel is True
    assert prod_settings.enable_flywheel is False
    assert dev_settings.with_overrides(runtime_mode="prod").enable_flywheel is False
    assert prod_settings.with_overrides(enable_flywheel=True).enable_flywheel is True


def test_task_hash_includes_job_title_and_notes() -> None:
    base = task_sha256(job_title="Agent Engineer", jd="Build agents.", notes="")
    different_title = task_sha256(job_title="Data Engineer", jd="Build agents.", notes="")
    different_notes = task_sha256(job_title="Agent Engineer", jd="Build agents.", notes="Prefer LangGraph")
    assert base != different_title
    assert base != different_notes


def test_json_columns_reject_invalid_json(tmp_path: Path) -> None:
    store = FlywheelStore(tmp_path / "flywheel.sqlite3")
    try:
        store.upsert_task(job_title="Agent Engineer", jd_text="JD", notes_text="")
        conn = store.connect()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO resume_snapshots (
                    snapshot_sha256, source_resume_id, dedup_key, raw_json,
                    normalized_preview_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("snapshot-invalid", "r1", "r1", "{bad json", "{}", "2026-05-06T00:00:00Z"),
            )
    finally:
        store.close()


def test_query_hits_require_snapshot_or_missing_reason(tmp_path: Path) -> None:
    store = FlywheelStore(tmp_path / "flywheel.sqlite3")
    try:
        task_id = store.upsert_task(job_title="Agent Engineer", jd_text="JD", notes_text="")
        store.start_run(
            run_id="run-1",
            task_id=task_id,
            version="0.6.2",
            git_sha="abc123",
            artifact_ref_id=None,
            artifact_root=str(tmp_path / "artifacts/runs/run-1"),
            config_hash="config-hash",
            config_payload={},
            status="running",
            eval_enabled=False,
            benchmark_id=None,
            benchmark_case_id=None,
        )
        conn = store.connect()
        conn.execute(
            """
            INSERT INTO run_queries (
                run_id, query_instance_id, query_fingerprint, round_no,
                lane_type, canonical_query_spec_json, query_spec_schema_version,
                query_policy_version, job_intent_fingerprint, provider_name,
                rendered_provider_query, keyword_query, query_terms_json,
                filters_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1", "query-1", "fingerprint-1", 1, "exploit",
                "{}", "canonical-query-spec-v1", "query-policy-v1", "intent-1",
                "cts", "agent", "agent", "[]", "{}",
                "2026-05-06T00:00:00Z",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO query_resume_hits (
                    run_id, query_instance_id, query_fingerprint, hit_sequence_no,
                    snapshot_sha256, snapshot_missing_reason, resume_id, round_no,
                    lane_type, batch_no, rank_in_query, provider_name,
                    was_new_to_pool, was_duplicate, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "run-1", "query-1", "fingerprint-1", 1,
                    None, None, "resume-1", 1, "exploit", 1, 1,
                    "cts", 1, 0, "2026-05-06T00:00:00Z",
                ),
            )
    finally:
        store.close()


def test_store_records_run_queries_and_hits_with_timestamps(tmp_path: Path) -> None:
    store = FlywheelStore(tmp_path / "flywheel.sqlite3")
    try:
        task_id = store.upsert_task(job_title="Agent Engineer", jd_text="JD", notes_text="")
        store.start_run(
            run_id="run-1",
            task_id=task_id,
            version="0.6.2",
            git_sha="abc123",
            artifact_ref_id=None,
            artifact_root=str(tmp_path / "artifacts/runs/run-1"),
            config_hash="config-hash",
            config_payload={"mock_cts": True},
            status="running",
            eval_enabled=False,
            benchmark_id=None,
            benchmark_case_id=None,
        )
        store.upsert_resume_snapshot(
            snapshot_sha256="snapshot-1",
            source_resume_id="resume-1",
            dedup_key="resume-1",
            raw_payload={"resume_id": "resume-1"},
            normalized_preview={"search_text": "agent"},
        )

        store.record_run_queries(
            [
                {
                    "run_id": "run-1",
                    "query_instance_id": "query-1",
                    "query_fingerprint": "fingerprint-1",
                    "round_no": 1,
                    "lane_type": "exploit",
                    "query_role": "exploit",
                    "canonical_query_spec_json": canonical_json({"lane_type": "exploit"}),
                    "query_spec_schema_version": "canonical-query-spec-v1",
                    "query_policy_version": "query-policy-v1",
                    "job_intent_fingerprint": "intent-1",
                    "provider_name": "cts",
                    "rendered_provider_query": "agent",
                    "keyword_query": "agent",
                    "query_terms_json": canonical_json(["agent"]),
                    "filters_json": canonical_json({}),
                    "location_key": "上海",
                    "batch_no": 1,
                    "source_plan_version": "1",
                    "selected_prf_expression": None,
                    "accepted_prf_term_family_id": None,
                    "fallback_reason": None,
                    "artifact_ref_id": None,
                }
            ]
        )
        store.record_query_resume_hits(
            [
                {
                    "run_id": "run-1",
                    "query_instance_id": "query-1",
                    "query_fingerprint": "fingerprint-1",
                    "hit_sequence_no": 1,
                    "snapshot_sha256": "snapshot-1",
                    "snapshot_missing_reason": None,
                    "resume_id": "resume-1",
                    "round_no": 1,
                    "lane_type": "exploit",
                    "location_key": "上海",
                    "location_type": "city",
                    "batch_no": 1,
                    "rank_in_query": 1,
                    "rank_global_in_query": 1,
                    "provider_name": "cts",
                    "provider_page_no": 1,
                    "provider_fetch_no": 1,
                    "provider_score_if_any": None,
                    "dedup_key": "resume-1",
                    "was_new_to_pool": True,
                    "was_duplicate": False,
                    "scored_fit_bucket": None,
                    "overall_score": None,
                    "must_have_match_score": None,
                    "risk_score": None,
                    "off_intent_reason_count": 0,
                    "final_candidate_status": None,
                    "score_evidence_source": "detail_enriched",
                    "card_scorecard_ref": "artifact:scorecards/card/resume-1.json",
                    "detail_scorecard_ref": "artifact:scorecards/detail/resume-1.json",
                    "score_delta": 14,
                    "detail_open_reason": "detail_budget_available",
                    "detail_open_policy_version": "detail-policy-v1",
                }
            ]
        )

        conn = store.connect()
        query_row = conn.execute("SELECT * FROM run_queries WHERE query_instance_id = 'query-1'").fetchone()
        hit_row = conn.execute("SELECT * FROM query_resume_hits WHERE resume_id = 'resume-1'").fetchone()
        assert query_row["created_at"].endswith("Z")
        assert hit_row["created_at"].endswith("Z")
        assert hit_row["snapshot_sha256"] == "snapshot-1"
        assert hit_row["score_evidence_source"] == "detail_enriched"
        assert hit_row["card_scorecard_ref"] == "artifact:scorecards/card/resume-1.json"
        assert hit_row["detail_scorecard_ref"] == "artifact:scorecards/detail/resume-1.json"
        assert hit_row["score_delta"] == 14
        assert hit_row["detail_open_reason"] == "detail_budget_available"
        assert hit_row["detail_open_policy_version"] == "detail-policy-v1"
    finally:
        store.close()


def test_judge_label_cache_uses_contract_hash(tmp_path: Path) -> None:
    store = FlywheelStore(tmp_path / "flywheel.sqlite3")
    try:
        task_id = store.upsert_task(job_title="Agent Engineer", jd_text="JD", notes_text="")
        store.upsert_resume_snapshot(
            snapshot_sha256="snapshot-1",
            source_resume_id="resume-1",
            dedup_key="resume-1",
            raw_payload={"resume_id": "resume-1"},
            normalized_preview={"search_text": "agent"},
        )
        first_contract = build_judge_contract_hash(
            judge_model_id="deepseek-v4-pro",
            judge_protocol_family="openai_chat_completions_compatible",
            judge_provider_label="bailian",
            judge_endpoint_kind="openai-compatible",
            structured_output_mode="strict_native_schema",
            judge_prompt_hash="prompt-a",
            judge_policy_version="judge-policy-v1",
            label_schema_version=FLYWHEEL_LABEL_SCHEMA_VERSION,
            judge_output_schema_hash="schema-hash",
            reasoning_effort=None,
            temperature=0.0,
        )
        second_contract = build_judge_contract_hash(
            judge_model_id="deepseek-v4-pro",
            judge_protocol_family="openai_chat_completions_compatible",
            judge_provider_label="bailian",
            judge_endpoint_kind="openai-compatible",
            structured_output_mode="strict_native_schema",
            judge_prompt_hash="prompt-b",
            judge_policy_version="judge-policy-v1",
            label_schema_version=FLYWHEEL_LABEL_SCHEMA_VERSION,
            judge_output_schema_hash="schema-hash",
            reasoning_effort=None,
            temperature=0.0,
        )
        store.record_judge_label(
            task_id=task_id,
            snapshot_sha256="snapshot-1",
            judge_model_id="deepseek-v4-pro",
            judge_protocol_family="openai_chat_completions_compatible",
            judge_provider_label="bailian",
            judge_endpoint_kind="openai-compatible",
            structured_output_mode="strict_native_schema",
            judge_prompt_hash="prompt-a",
            judge_contract_hash=first_contract,
            judge_policy_version="judge-policy-v1",
            label_schema_version=FLYWHEEL_LABEL_SCHEMA_VERSION,
            judge_output_schema_hash="schema-hash",
            reasoning_effort=None,
            temperature=0.0,
            score=3,
            rationale="Strong fit.",
            label_payload={"score": 3, "rationale": "Strong fit."},
            judge_prompt_text="prompt text",
        )
        assert store.get_cached_judge_label(
            task_id=task_id,
            snapshot_sha256="snapshot-1",
            judge_contract_hash=first_contract,
            label_schema_version=FLYWHEEL_LABEL_SCHEMA_VERSION,
        ) == {"score": 3, "rationale": "Strong fit."}
        assert store.get_cached_judge_label(
            task_id=task_id,
            snapshot_sha256="snapshot-1",
            judge_contract_hash=second_contract,
            label_schema_version=FLYWHEEL_LABEL_SCHEMA_VERSION,
        ) is None
    finally:
        store.close()


def test_record_judge_label_rejects_mismatched_contract_hash(tmp_path: Path) -> None:
    store = FlywheelStore(tmp_path / "flywheel.sqlite3")
    try:
        task_id = store.upsert_task(job_title="Agent Engineer", jd_text="JD", notes_text="")
        store.upsert_resume_snapshot(
            snapshot_sha256="snapshot-1",
            source_resume_id="resume-1",
            dedup_key="resume-1",
            raw_payload={"resume_id": "resume-1"},
            normalized_preview={"search_text": "agent"},
        )
        with pytest.raises(ValueError, match="judge_contract_hash"):
            store.record_judge_label(
                task_id=task_id,
                snapshot_sha256="snapshot-1",
                judge_model_id="deepseek-v4-pro",
                judge_protocol_family="openai_chat_completions_compatible",
                judge_provider_label="bailian",
                judge_endpoint_kind="openai-compatible",
                structured_output_mode="strict_native_schema",
                judge_prompt_hash="prompt-a",
                judge_contract_hash="wrong",
                judge_policy_version="judge-policy-v1",
                label_schema_version=FLYWHEEL_LABEL_SCHEMA_VERSION,
                judge_output_schema_hash="schema-hash",
                reasoning_effort=None,
                temperature=0.0,
                score=3,
                rationale="Strong fit.",
                label_payload={"score": 3, "rationale": "Strong fit."},
                judge_prompt_text="prompt text",
            )
    finally:
        store.close()


def test_canonical_json_is_stable() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_active_python_no_longer_mentions_legacy_judge_cache() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    forbidden_tokens = (
        "JudgeCache",
        "judge_cache",
        "judge_cache.sqlite3",
        "migrate_judge_assets",
        "migrate-judge-assets",
        "jd_assets",
        "resume_assets",
    )
    allowed = {Path("tests/test_flywheel_store.py")}
    offenders: list[str] = []

    for root in (repo_root / "src", repo_root / "tests", repo_root / "experiments"):
        for path in root.rglob("*.py"):
            relative = path.relative_to(repo_root)
            if relative in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in forbidden_tokens):
                offenders.append(str(relative))

    assert offenders == []


def test_tests_do_not_write_repo_root_cache_test_dirs() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []

    for path in (repo_root / "tests").rglob("*.py"):
        relative = path.relative_to(repo_root)
        if relative == Path("tests/test_flywheel_store.py"):
            continue
        if "cache-test-" in path.read_text(encoding="utf-8"):
            offenders.append(str(relative))

    assert offenders == []
