from __future__ import annotations

from pathlib import Path

from seektalent_ui.workbench_store import WorkbenchStore


REQUIRED_TABLES = {
    "tenants",
    "workspaces",
    "users",
    "workspace_memberships",
    "sessions",
    "session_requirement_reviews",
    "source_runs",
    "source_connections",
    "connection_status_events",
    "security_audit_events",
    "source_run_policies",
    "source_run_jobs",
    "runtime_sourcing_jobs",
    "runtime_finalization_revisions",
    "runtime_candidate_identity_snapshots",
    "session_events",
    "runtime_source_lane_latest_state",
    "workbench_note_writer_leases",
    "candidate_review_items",
    "candidate_evidence",
    "candidate_actions",
    "detail_open_requests",
    "detail_open_ledger",
    "external_write_intents",
}


def _assert_no_workbench_store_import(relative_path: str) -> None:
    import ast

    root = Path(__file__).resolve().parents[1]
    source_path = root / relative_path
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names = {alias.name for alias in node.names}
            assert "seektalent_ui.workbench_store" not in imported_names
        if isinstance(node, ast.ImportFrom):
            imported = node.module or ""
            assert imported != "seektalent_ui.workbench_store"
            assert not (node.level == 1 and imported == "workbench_store")


def test_workbench_store_types_are_importable_from_dedicated_module() -> None:
    from seektalent_ui.workbench_store_types import (
        DEFAULT_TENANT_ID,
        DEFAULT_WORKSPACE_ID,
        WorkbenchSecurityAuditEvent,
        WorkbenchUser,
    )

    assert DEFAULT_TENANT_ID == "local"
    assert DEFAULT_WORKSPACE_ID == "default"
    assert WorkbenchUser(
        user_id="user_1",
        email="admin@example.com",
        display_name="Admin",
        role="admin",
        workspace_id="default",
    ).workspace_id == "default"
    assert WorkbenchSecurityAuditEvent(
        audit_id=1,
        actor_user_id="user_1",
        actor_role="admin",
        workspace_id="default",
        request_ip=None,
        user_agent=None,
        target_type="session",
        target_id="session_1",
        action="login",
        result="success",
        reason_code="success",
        metadata={},
        created_at="2026-06-11T00:00:00+00:00",
    ).action == "login"


def test_security_audit_store_preserves_redaction_and_facade(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    store.record_security_audit_event(
        workspace_id="default",
        actor_user_id="user_secret_value",
        actor_role="admin",
        target_type="login",
        target_id="target_secret_value",
        action="login",
        result="failure",
        reason_code="invalid_credentials",
        metadata={"token": "secret-token-value", "safe": "value"},
    )

    [event] = store.list_security_audit_events()

    assert event.action == "login"
    assert event.result == "failure"
    assert event.reason_code == "invalid_credentials"
    assert "secret-token-value" not in repr(event.metadata)
    assert event.metadata["safe"] == "value"


def test_security_audit_module_does_not_import_workbench_store_facade() -> None:
    _assert_no_workbench_store_import("src/seektalent_ui/workbench_security_audit_store.py")


def test_session_connection_job_stores_do_not_import_workbench_store_facade() -> None:
    for relative_path in (
        "src/seektalent_ui/workbench_session_store.py",
        "src/seektalent_ui/workbench_connection_store.py",
        "src/seektalent_ui/workbench_job_store.py",
    ):
        _assert_no_workbench_store_import(relative_path)


def test_event_store_does_not_import_workbench_store_facade() -> None:
    _assert_no_workbench_store_import("src/seektalent_ui/workbench_event_store.py")


def test_candidate_store_does_not_import_workbench_store_facade() -> None:
    _assert_no_workbench_store_import("src/seektalent_ui/workbench_candidate_store.py")


def test_detail_open_store_does_not_import_workbench_store_facade() -> None:
    _assert_no_workbench_store_import("src/seektalent_ui/workbench_detail_open_store.py")


def test_workbench_schema_module_creates_required_tables(tmp_path: Path) -> None:
    from seektalent_ui.workbench_db import connect_workbench_db
    from seektalent_ui.workbench_schema import initialize_workbench_schema

    db_path = tmp_path / "workbench.sqlite3"
    with connect_workbench_db(db_path) as conn:
        initialize_workbench_schema(conn, now="2026-06-11T00:00:00+00:00")
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert REQUIRED_TABLES <= tables


def test_workbench_schema_module_creates_required_indexes(tmp_path: Path) -> None:
    from seektalent_ui.workbench_db import connect_workbench_db
    from seektalent_ui.workbench_schema import initialize_workbench_schema

    db_path = tmp_path / "workbench.sqlite3"
    with connect_workbench_db(db_path) as conn:
        initialize_workbench_schema(conn, now="2026-06-11T00:00:00+00:00")
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert "idx_external_write_intents_pending" in indexes
