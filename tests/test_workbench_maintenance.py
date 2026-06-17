from __future__ import annotations

import json
import os
import stat
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import seektalent_ui.maintenance as maintenance
from seektalent_ui.maintenance import (
    BACKUP_METADATA_SCHEMA,
    EXCLUDED_DATA,
    RETENTION_POLICY,
    WORKBENCH_REQUIRED_COLUMNS,
    WORKBENCH_REQUIRED_INDEXES,
    WORKBENCH_REQUIRED_TABLES,
    MaintenanceError,
    backup_workbench,
    main,
    restore_workbench,
    run_local_storage_cleanup,
    run_database_group_backup,
    run_operator_health,
    run_runtime_control_retention,
    run_rollout_readiness,
)
from seektalent_ui.workbench_store import DEFAULT_TENANT_ID, WorkbenchSession, WorkbenchStore, WorkbenchUser


def _db_path(workspace_root: Path) -> Path:
    return workspace_root / ".seektalent" / "workbench.sqlite3"


def test_workbench_store_connect_closes_after_context(tmp_path: Path) -> None:
    store = WorkbenchStore(_db_path(tmp_path))

    with store._connect() as conn:
        conn.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1").fetchone()


def test_runtime_control_retention_operator_entrypoint_defaults_to_dry_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord
    from seektalent_runtime_control.store import RuntimeControlStore

    runtime_db = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(runtime_db)
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_maintenance",
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id="reqapproved_maintenance",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-05-01T00:00:00.000000Z",
            updated_at="2026-05-01T00:00:00.000000Z",
            completed_at="2026-05-01T00:00:00.000000Z",
        )
    )
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_maintenance_debug",
            runtime_run_id="runtime_run_maintenance",
            event_type="runtime_debug_note",
            stage="debug",
            round_no=None,
            source_id=None,
            status="completed",
            summary="debug row can be deleted only after apply",
            payload={"debug": True},
            visibility="developer",
            created_at="2026-05-01T00:00:01.000000Z",
        )
    )

    result = run_runtime_control_retention(
        runtime_control_db_path=runtime_db,
        now=lambda: "2026-06-08T00:00:00.000000Z",
        database_budget_bytes=1,
    )
    exit_code = main(
        [
            "runtime-retention",
            "--runtime-control-db",
            str(runtime_db),
            "--now",
            "2026-06-08T00:00:00.000000Z",
            "--database-budget-bytes",
            "1",
        ]
    )
    output = capsys.readouterr().out

    assert result.dry_run is True
    assert result.stats.nonpublic_event_count == 1
    assert result.stats.over_database_budget is True
    assert exit_code == 0
    assert "dry_run: true" in output
    assert "total_deletable_count: 1" in output
    assert store.get_event(runtime_run_id="runtime_run_maintenance", event_id="rtevt_maintenance_debug") is not None


def test_local_storage_cleanup_operator_entrypoint_defaults_to_dry_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"
    old_debug = artifacts_dir / "debug" / "2026" / "05" / "01" / "debug.json"
    old_debug.parent.mkdir(parents=True)
    old_debug.write_text("debug", encoding="utf-8")
    old_timestamp = datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old_debug, (old_timestamp, old_timestamp))

    result = run_local_storage_cleanup(
        workspace_root=tmp_path,
        runtime_mode="prod",
        artifacts_dir=str(artifacts_dir),
        llm_cache_dir=str(cache_dir),
        now=datetime(2026, 6, 17, tzinfo=timezone.utc),
        debug_retention_days=7,
    )
    exit_code = main(
        [
            "local-storage-cleanup",
            "--workspace-root",
            str(tmp_path),
            "--runtime-mode",
            "prod",
            "--artifacts-dir",
            str(artifacts_dir),
            "--llm-cache-dir",
            str(cache_dir),
            "--now",
            "2026-06-17T00:00:00+00:00",
            "--debug-retention-days",
            "7",
        ]
    )
    output = capsys.readouterr().out

    assert result.dry_run is True
    assert len(result.candidates) == 1
    assert old_debug.exists()
    assert exit_code == 0
    assert "dry_run: true" in output
    assert "candidate_count: 1" in output
    assert old_debug.exists()


def test_operator_health_cli_reports_product_database_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION

    runtime_db = tmp_path / ".seektalent" / "runtime_control.sqlite3"
    _create_marker_database(runtime_db, marker="runtime_control", user_version=RUNTIME_CONTROL_SCHEMA_VERSION)

    result = run_operator_health(workspace_root=tmp_path, required_free_bytes=1)
    exit_code = main(["operator-health", "--workspace-root", str(tmp_path), "--required-free-bytes", "1"])
    output = capsys.readouterr().out

    assert result.status == "warning"
    assert exit_code == 0
    assert "status: warning" in output
    assert "database_count: 7" in output
    assert "database:runtime_control:ok:db_ok" in output
    assert "database:conversation:warning:db_missing" in output


def test_database_group_backup_cli_writes_manifest_and_reports_partial_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_db = tmp_path / ".seektalent" / "runtime_control.sqlite3"
    _create_marker_database(runtime_db, marker="runtime_control", user_version=1)

    result = run_database_group_backup(
        workspace_root=tmp_path,
        now=datetime(2026, 6, 17, tzinfo=timezone.utc),
    )
    exit_code = main(
        [
            "backup-db-group",
            "--workspace-root",
            str(tmp_path),
            "--now",
            "2026-06-17T00:00:00+00:00",
        ]
    )
    output = capsys.readouterr().out

    assert result.status == "warning"
    assert result.manifest_path.exists()
    assert exit_code == 0
    assert "status: warning" in output
    assert "reason_code: db_group_backup_partial" in output
    assert "database_count: 7" in output
    assert "verified_count: 1" in output


def _create_workbench_fixture(workspace_root: Path, *, job_title: str) -> tuple[WorkbenchStore, WorkbenchUser, WorkbenchSession]:
    store = WorkbenchStore(_db_path(workspace_root))
    user = store.ensure_local_actor()
    session = store.create_workbench_session(
        user=user,
        job_title=job_title,
        jd_text="Own senior AI recruiting platform work.",
        notes="internal fixture",
    )
    connection, _ = store.get_or_create_liepin_source_connection(user=user)
    connected = store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection.connection_id,
        provider_account_hash="provider-account-hash",
    )
    assert connected is not None
    session = store.get_workbench_session(user=user, session_id=session.session_id)
    assert session is not None
    _insert_liepin_card_candidate(store.db_path, user=user, session=session)
    policy = store.update_liepin_source_run_policy(
        user=user,
        session_id=session.session_id,
        detail_open_mode="bypass_confirm",
    )
    assert policy is not None
    detail_request = store.create_liepin_detail_open_request(
        user=user,
        session_id=session.session_id,
        review_item_id="review_fixture",
        idempotency_key="fixture",
    )
    assert detail_request is not None
    assert detail_request.ledger is not None
    return store, user, session


def _create_marker_database(path: Path, *, marker: str, user_version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES (?)", (marker,))
        conn.execute(f"PRAGMA user_version = {user_version}")


def _insert_liepin_card_candidate(database_path: Path, *, user: WorkbenchUser, session: WorkbenchSession) -> None:
    liepin_run = next(source_run for source_run in session.source_runs if source_run.source_kind == "liepin")
    now = "2026-05-10T00:00:00+00:00"
    with sqlite3.connect(database_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO candidate_review_items (
                review_item_id, tenant_id, workspace_id, user_id, session_id,
                primary_evidence_id, display_name, title, company, location, summary,
                aggregate_score, fit_bucket, review_status, note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
            """,
            (
                "review_fixture",
                DEFAULT_TENANT_ID,
                user.workspace_id,
                user.user_id,
                session.session_id,
                "evidence_fixture",
                "Candidate Fixture",
                "AI Recruiting Platform Lead",
                "Fixture Co",
                "Shanghai",
                "Strong workbench and sourcing automation background.",
                92,
                "strong",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO candidate_evidence (
                evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                resume_id, score, fit_bucket, matched_must_haves_json,
                matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'liepin', 'card', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evidence_fixture",
                "review_fixture",
                DEFAULT_TENANT_ID,
                user.workspace_id,
                user.user_id,
                session.session_id,
                liepin_run.source_run_id,
                "provider-candidate-hash",
                "resume_fixture",
                92,
                "strong",
                json.dumps(["AI recruiting platform"], ensure_ascii=False),
                json.dumps(["workflow automation"], ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                json.dumps(["multi-source sourcing"], ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                now,
            ),
        )
        conn.commit()


def _snapshot(workspace_root: Path) -> dict[str, object]:
    store = WorkbenchStore(_db_path(workspace_root))
    user = store.ensure_local_actor()
    sessions = store.list_workbench_sessions(user=user)
    assert len(sessions) == 1
    session = sessions[0]
    candidates = store.list_candidate_review_items(user=user, session_id=session.session_id)
    assert candidates is not None
    detail_requests = store.list_liepin_detail_open_requests(user=user, session_id=session.session_id)
    return {
        "job_title": session.job_title,
        "source_kinds": sorted(source_run.source_kind for source_run in session.source_runs),
        "candidate_count": len(candidates),
        "candidate_badges": candidates[0].source_badges if candidates else [],
        "detail_request_count": len(detail_requests),
        "detail_ledger_status": detail_requests[0].ledger.status if detail_requests and detail_requests[0].ledger else None,
    }


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _audit_actions(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT action FROM security_audit_events ORDER BY audit_id ASC").fetchall()
    return [row[0] for row in rows]


def _write_arbitrary_sqlite_backup(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items (name TEXT NOT NULL)")
        conn.execute("INSERT INTO items (name) VALUES ('not-workbench')")
        conn.commit()
    metadata = {
        "app_version": "test",
        "backup_database_name": path.name,
        "backup_database": str(path),
        "created_at": "2026-05-10T00:00:00+00:00",
        "created_by": "test",
        "excluded_data": EXCLUDED_DATA,
        "git_commit": None,
        "integrity_check": "ok",
        "metadata_schema": BACKUP_METADATA_SCHEMA,
        "retention_policy": RETENTION_POLICY,
        "source_database": str(path),
        "workbench_required_tables": ["items"],
    }
    path.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")


def _write_wrong_workbench_schema_backup(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        for table_name in sorted(WORKBENCH_REQUIRED_TABLES):
            conn.execute(f'CREATE TABLE "{table_name}" (placeholder TEXT)')
        conn.commit()
    metadata = {
        "app_version": "test",
        "backup_database_name": path.name,
        "backup_database": str(path),
        "created_at": "2026-05-10T00:00:00+00:00",
        "created_by": "test",
        "excluded_data": EXCLUDED_DATA,
        "git_commit": None,
        "integrity_check": "ok",
        "metadata_schema": BACKUP_METADATA_SCHEMA,
        "retention_policy": RETENTION_POLICY,
        "source_database": str(path),
        "workbench_required_columns": {
            table: sorted(columns) for table, columns in WORKBENCH_REQUIRED_COLUMNS.items()
        },
        "workbench_required_indexes": sorted(WORKBENCH_REQUIRED_INDEXES),
        "workbench_required_table_fragments": maintenance.WORKBENCH_REQUIRED_TABLE_FRAGMENTS,
        "workbench_required_tables": sorted(WORKBENCH_REQUIRED_TABLES),
    }
    path.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")


def _write_same_columns_wrong_schema_backup(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical_columns = _canonical_table_columns()
    canonical_schema = maintenance._canonical_schema_signature()
    with sqlite3.connect(path) as conn:
        for table_name, table_columns in canonical_columns.items():
            columns = ", ".join(f'"{column}" TEXT' for column in sorted(table_columns))
            conn.execute(f'CREATE TABLE "{table_name}" ({columns})')
        conn.commit()
    metadata = {
        "app_version": "test",
        "backup_database_name": path.name,
        "backup_database": str(path),
        "created_at": "2026-05-10T00:00:00+00:00",
        "created_by": "test",
        "excluded_data": EXCLUDED_DATA,
        "git_commit": None,
        "integrity_check": "ok",
        "metadata_schema": BACKUP_METADATA_SCHEMA,
        "retention_policy": RETENTION_POLICY,
        "source_database": str(path),
        "workbench_required_checks": {
            table: list(checks)
            for table, checks in canonical_schema["checks"].items()
            if checks
        },
        "workbench_required_columns": {table: sorted(columns) for table, columns in canonical_columns.items()},
        "workbench_required_foreign_keys": {
            table: [list(item) for item in foreign_keys]
            for table, foreign_keys in canonical_schema["foreign_keys"].items()
            if foreign_keys
        },
        "workbench_required_indexes": sorted(canonical_schema["indexes"]),
        "workbench_required_table_fragments": maintenance.WORKBENCH_REQUIRED_TABLE_FRAGMENTS,
        "workbench_required_tables": sorted(WORKBENCH_REQUIRED_TABLES),
    }
    path.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")


def _write_column_fragment_spoof_backup(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical_schema = maintenance._canonical_schema_signature()
    table_sql = dict(canonical_schema["tables"])
    table_sql["users"] = table_sql["users"].replace(
        "email TEXT NOT NULL UNIQUE COLLATE NOCASE",
        "email TEXT NOT NULL, not_email TEXT NOT NULL UNIQUE COLLATE NOCASE",
    )
    with sqlite3.connect(path) as conn:
        for table_sql_statement in table_sql.values():
            conn.execute(table_sql_statement)
        for index_sql_statement in canonical_schema["indexes"].values():
            conn.execute(index_sql_statement)
        conn.commit()
    metadata = {
        "app_version": "test",
        "backup_database_name": path.name,
        "backup_database": str(path),
        "created_at": "2026-05-10T00:00:00+00:00",
        "created_by": "test",
        "excluded_data": EXCLUDED_DATA,
        "git_commit": None,
        "integrity_check": "ok",
        "metadata_schema": BACKUP_METADATA_SCHEMA,
        "retention_policy": RETENTION_POLICY,
        "source_database": str(path),
        "workbench_required_checks": {
            table: list(checks)
            for table, checks in canonical_schema["checks"].items()
            if checks
        },
        "workbench_required_columns": {
            table: sorted(columns)
            for table, columns in _canonical_table_columns().items()
        },
        "workbench_required_foreign_keys": {
            table: [list(item) for item in foreign_keys]
            for table, foreign_keys in canonical_schema["foreign_keys"].items()
            if foreign_keys
        },
        "workbench_required_indexes": sorted(canonical_schema["indexes"]),
        "workbench_required_table_fragments": maintenance.WORKBENCH_REQUIRED_TABLE_FRAGMENTS,
        "workbench_required_tables": sorted(WORKBENCH_REQUIRED_TABLES),
    }
    path.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")


def _canonical_table_columns() -> dict[str, list[str]]:
    with TemporaryDirectory(prefix="seektalent-workbench-test-schema-") as temp_dir:
        database_path = Path(temp_dir) / "workbench.sqlite3"
        WorkbenchStore(database_path).list_security_audit_events()
        with sqlite3.connect(database_path) as conn:
            return {
                table: [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                for table in sorted(WORKBENCH_REQUIRED_TABLES)
            }


def test_backup_creates_database_metadata_and_owner_only_permissions(tmp_path: Path) -> None:
    _, _, _ = _create_workbench_fixture(tmp_path, job_title="AI Search Lead")

    backup = backup_workbench(workspace_root=tmp_path)

    assert backup.database_path.parent == tmp_path / ".seektalent" / "backups"
    assert backup.database_path.suffix == ".sqlite3"
    assert backup.metadata_path == backup.database_path.with_suffix(".json")
    assert _snapshot_from_database(backup.database_path)["job_title"] == "AI Search Lead"
    assert _mode(backup.database_path.parent) == 0o700
    assert _mode(backup.database_path) == 0o600
    assert _mode(backup.metadata_path) == 0o600

    metadata = json.loads(backup.metadata_path.read_text(encoding="utf-8"))
    assert metadata["metadata_schema"] == BACKUP_METADATA_SCHEMA
    assert metadata["source_database"] == str(_db_path(tmp_path))
    assert metadata["backup_database"] == str(backup.database_path)
    assert metadata["backup_database_name"] == backup.database_path.name
    assert metadata["app_version"]
    assert "git_commit" in metadata
    assert metadata["integrity_check"] == "ok"
    assert metadata["retention_policy"] == RETENTION_POLICY
    assert "ledger_id" in metadata["workbench_required_columns"]["detail_open_ledger"]
    assert "metadata_redacted_json" in metadata["workbench_required_columns"]["security_audit_events"]
    assert "source_runs" in metadata["workbench_required_foreign_keys"]
    assert "sessions" in metadata["workbench_required_checks"]
    assert "idx_detail_open_ledger_one_active_lease" in metadata["workbench_required_indexes"]
    assert "email TEXT NOT NULL UNIQUE COLLATE NOCASE" in metadata["workbench_required_table_fragments"]["users"]
    assert "detail_open_ledger" in metadata["workbench_required_tables"]
    assert "security_audit_events" in metadata["workbench_required_tables"]
    assert "browser_profiles" in metadata["excluded_data"]
    assert "raw_provider_session_state" in metadata["excluded_data"]
    assert "workbench_backup_created" in _audit_actions(_db_path(tmp_path))


def test_rollout_readiness_writes_redacted_manual_required_evidence(tmp_path: Path) -> None:
    _, _, _ = _create_workbench_fixture(tmp_path, job_title="Internal Rollout Lead")

    result = run_rollout_readiness(workspace_root=tmp_path)

    assert result.status == "manual_required"
    assert result.report_path.parent == tmp_path / ".seektalent" / "rollout-readiness"
    assert result.markdown_path.parent == result.report_path.parent
    assert _mode(result.report_path.parent) == 0o700
    assert _mode(result.report_path) == 0o600
    assert _mode(result.markdown_path) == 0o600

    checks = {check.name: check for check in result.checks}
    assert checks["workbench_schema"].status == "pass"
    assert checks["workbench_backup"].status == "pass"
    assert checks["backup_verify"].status == "pass"
    assert checks["restore_to_temp"].status == "pass"
    assert checks["workbench_read_path_smoke"].status == "pass"
    assert checks["real_device_lan_access"].status == "manual_required"
    assert checks["real_liepin_login_relay"].status == "manual_required"
    assert checks["provider_budget_detail_behavior"].status == "manual_required"

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "manual_required"
    assert report["manual_gates"] == [
        "real_device_lan_access",
        "real_liepin_login_relay",
        "provider_budget_detail_behavior",
    ]
    assert report["checks"][0]["name"] == "workbench_schema"
    evidence_text = result.report_path.read_text(encoding="utf-8") + result.markdown_path.read_text(encoding="utf-8")
    assert "uv run seektalent-ui-maintenance rollout-readiness --workspace-root ." in evidence_text
    forbidden_fragments = [
        "provider-account-hash",
        "provider-candidate-hash",
        "candidate fixture",
        "resume_fixture",
        "admin@example.com",
        "internal rollout lead",
        "own senior ai recruiting platform work",
        "ai recruiting platform lead",
        "fixture co",
        "shanghai",
        "raw_provider",
        "cookie",
        "storage_state",
        "authorization",
        "cdp",
    ]
    assert not any(fragment in evidence_text.lower() for fragment in forbidden_fragments)


def test_rollout_readiness_supports_custom_output_dir(tmp_path: Path) -> None:
    _, _, _ = _create_workbench_fixture(tmp_path, job_title="Internal Rollout Lead")
    output_dir = tmp_path / "reports"

    result = run_rollout_readiness(workspace_root=tmp_path, output_dir=output_dir)

    assert result.status == "manual_required"
    assert result.report_path.parent == output_dir
    assert result.markdown_path.parent == output_dir
    assert _mode(output_dir) == 0o700


def test_rollout_readiness_cli_success_writes_manual_required_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, _, _ = _create_workbench_fixture(tmp_path, job_title="Internal Rollout Lead")
    output_dir = tmp_path / "readiness"

    exit_code = main(["rollout-readiness", "--workspace-root", str(tmp_path), "--output-dir", str(output_dir)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "status: manual_required" in captured.out
    reports = sorted(output_dir.glob("rollout-readiness-*.json"))
    markdown_reports = sorted(output_dir.glob("rollout-readiness-*.md"))
    assert len(reports) == 1
    assert len(markdown_reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "manual_required"
    assert report["manual_gates"] == [
        "real_device_lan_access",
        "real_liepin_login_relay",
        "provider_budget_detail_behavior",
    ]


def test_rollout_readiness_missing_database_fails(tmp_path: Path) -> None:
    with pytest.raises(MaintenanceError, match="workbench database does not exist"):
        run_rollout_readiness(workspace_root=tmp_path)

    exit_code = main(["rollout-readiness", "--workspace-root", str(tmp_path)])

    assert exit_code == 1


def test_rollout_readiness_invalid_schema_cli_fails(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (name TEXT NOT NULL)")
        conn.commit()

    exit_code = main(["rollout-readiness", "--workspace-root", str(tmp_path)])

    assert exit_code == 1


def _snapshot_from_database(database_path: Path) -> dict[str, object]:
    workspace_root = database_path.parent.parent
    if database_path.name != "workbench.sqlite3":
        workspace_root = database_path.parent.parent.parent / f"snapshot-{database_path.stem}"
        target = _db_path(workspace_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(database_path.read_bytes())
    return _snapshot(workspace_root)


def test_restore_checks_backup_and_replaces_workbench_database(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _create_workbench_fixture(source_root, job_title="Restored AI Search Lead")
    backup = backup_workbench(workspace_root=source_root)

    workspace_root = tmp_path / "restore"
    target_db = _db_path(workspace_root)
    _create_workbench_fixture(workspace_root, job_title="Old Role")
    wal_path = target_db.with_name(f"{target_db.name}-wal")
    shm_path = target_db.with_name(f"{target_db.name}-shm")
    wal_path.write_text("stale wal", encoding="utf-8")
    shm_path.write_text("stale shm", encoding="utf-8")

    restored = restore_workbench(backup_path=backup.database_path, workspace_root=workspace_root, yes=True)

    assert restored.database_path == target_db
    assert restored.integrity_check == "ok"
    if wal_path.exists():
        assert wal_path.read_bytes() != b"stale wal"
    if shm_path.exists():
        assert shm_path.read_bytes() != b"stale shm"
    assert _snapshot(workspace_root) == {
        "job_title": "Restored AI Search Lead",
        "source_kinds": ["cts", "liepin"],
        "candidate_count": 1,
        "candidate_badges": ["Liepin card"],
        "detail_request_count": 1,
        "detail_ledger_status": "leased",
    }
    assert _mode(target_db.parent) == 0o700
    assert _mode(target_db) == 0o600
    assert "workbench_backup_restored" in _audit_actions(target_db)


def test_restore_without_yes_returns_nonzero_and_keeps_database(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _create_workbench_fixture(source_root, job_title="Restored")
    backup = backup_workbench(workspace_root=source_root)

    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")

    exit_code = main(["restore", str(backup.database_path), "--workspace-root", str(workspace_root)])

    assert exit_code != 0
    assert _snapshot(workspace_root)["job_title"] == "Old"


def test_restore_rejects_arbitrary_sqlite_and_keeps_existing_database(tmp_path: Path) -> None:
    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")
    arbitrary_backup = tmp_path / "bad-backups" / "workbench-bad.sqlite3"
    _write_arbitrary_sqlite_backup(arbitrary_backup)

    with pytest.raises(MaintenanceError, match="metadata|not a workbench database|schema tables"):
        restore_workbench(backup_path=arbitrary_backup, workspace_root=workspace_root, yes=True)

    assert _snapshot(workspace_root)["job_title"] == "Old"


def test_restore_rejects_spoofed_table_names_and_keeps_existing_database(tmp_path: Path) -> None:
    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")
    spoofed_backup = tmp_path / "bad-backups" / "workbench-spoofed.sqlite3"
    _write_wrong_workbench_schema_backup(spoofed_backup)

    with pytest.raises(MaintenanceError, match="metadata|missing columns"):
        restore_workbench(backup_path=spoofed_backup, workspace_root=workspace_root, yes=True)

    assert _snapshot(workspace_root)["job_title"] == "Old"


def test_restore_rejects_same_columns_wrong_schema_and_keeps_existing_database(tmp_path: Path) -> None:
    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")
    spoofed_backup = tmp_path / "bad-backups" / "workbench-spoofed-columns.sqlite3"
    _write_same_columns_wrong_schema_backup(spoofed_backup)

    with pytest.raises(MaintenanceError, match="schema mismatch|missing indexes|missing check|foreign key"):
        restore_workbench(backup_path=spoofed_backup, workspace_root=workspace_root, yes=True)

    assert _snapshot(workspace_root)["job_title"] == "Old"


def test_restore_rejects_column_fragment_spoof_and_keeps_existing_database(tmp_path: Path) -> None:
    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")
    spoofed_backup = tmp_path / "bad-backups" / "workbench-spoofed-fragment.sqlite3"
    _write_column_fragment_spoof_backup(spoofed_backup)

    with pytest.raises(MaintenanceError, match="missing table schema fragments"):
        restore_workbench(backup_path=spoofed_backup, workspace_root=workspace_root, yes=True)

    assert _snapshot(workspace_root)["job_title"] == "Old"


def test_restore_rejects_backup_with_trigger_and_keeps_existing_database(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _create_workbench_fixture(source_root, job_title="Restored")
    backup = backup_workbench(workspace_root=source_root)
    with sqlite3.connect(backup.database_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER tamper_after_restore_audit
            AFTER INSERT ON security_audit_events
            BEGIN
                UPDATE sessions SET job_title = 'Tampered';
            END
            """
        )
        conn.commit()

    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")

    with pytest.raises(MaintenanceError, match="executable schema objects"):
        restore_workbench(backup_path=backup.database_path, workspace_root=workspace_root, yes=True)

    assert _snapshot(workspace_root)["job_title"] == "Old"


def test_restore_restores_original_database_when_post_replace_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _create_workbench_fixture(source_root, job_title="Restored")
    backup = backup_workbench(workspace_root=source_root)

    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit failed")

    monkeypatch.setattr(maintenance, "_record_maintenance_audit", fail_audit)

    with pytest.raises(MaintenanceError, match="original database restored"):
        restore_workbench(backup_path=backup.database_path, workspace_root=workspace_root, yes=True)

    assert _snapshot(workspace_root)["job_title"] == "Old"
    assert not list((workspace_root / ".seektalent").glob("*.restore-*.old"))


def test_restore_rejects_missing_metadata_and_keeps_existing_database(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _create_workbench_fixture(source_root, job_title="Restored")
    backup = backup_workbench(workspace_root=source_root)
    backup.metadata_path.unlink()

    workspace_root = tmp_path / "restore"
    _create_workbench_fixture(workspace_root, job_title="Old")

    with pytest.raises(MaintenanceError, match="metadata"):
        restore_workbench(backup_path=backup.database_path, workspace_root=workspace_root, yes=True)

    assert _snapshot(workspace_root)["job_title"] == "Old"
