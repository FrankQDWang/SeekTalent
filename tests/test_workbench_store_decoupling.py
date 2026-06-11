from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seektalent_ui.server import create_app
from seektalent_ui.workbench_store import WorkbenchStore
from tests.settings_factory import make_settings


CSRF_COOKIE_NAME = "seektalent_workbench_csrf"
REQUIRED_TABLES = {
    "tenants",
    "workspaces",
    "users",
    "workspace_memberships",
    "user_sessions",
    "login_attempts",
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
        UserSessionTokens,
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
    assert UserSessionTokens(session_token="session", csrf_token="csrf").csrf_token == "csrf"
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


def test_auth_modules_do_not_import_workbench_store_facade() -> None:
    for relative_path in (
        "src/seektalent_ui/workbench_auth_store.py",
        "src/seektalent_ui/workbench_auth_service.py",
    ):
        _assert_no_workbench_store_import(relative_path)


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


def test_auth_store_preserves_readonly_lookup_and_logout(tmp_path: Path) -> None:
    from seektalent_ui.auth import hash_password, session_token_digest

    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, workspace = store.bootstrap_admin(
        email="admin@example.com",
        display_name="Admin User",
        password_hash=hash_password("correct horse"),
    )
    tokens = store.create_user_session(user_id=user.user_id, workspace_id=workspace.workspace_id)
    digest = session_token_digest(tokens.session_token)

    assert store.get_user_by_session_readonly(session_digest=digest) == user
    store.revoke_user_session(session_digest=digest, user=user)

    assert store.get_user_by_session_readonly(session_digest=digest) is None
    assert store.get_user_by_session(session_digest=digest) is None


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


def test_workbench_store_public_facade_keeps_auth_methods(tmp_path: Path) -> None:
    from seektalent_ui.auth import hash_password, session_token_digest

    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, workspace = store.bootstrap_admin(
        email="admin@example.com",
        display_name="Admin User",
        password_hash=hash_password("correct horse"),
    )
    login_row = store.get_user_for_login(email="admin@example.com")
    assert login_row is not None
    tokens = store.create_user_session(user_id=user.user_id, workspace_id=workspace.workspace_id)

    recovered = store.get_user_by_session(session_digest=session_token_digest(tokens.session_token))

    assert recovered == user
    assert store.verify_session_csrf(
        session_digest=session_token_digest(tokens.session_token),
        csrf_token=tokens.csrf_token,
    )


def test_auth_login_service_preserves_route_behavior(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    client = TestClient(
        create_app(settings=settings),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={
            "email": "admin@example.com",
            "password": "correct horse",
            "displayName": "Admin User",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text

    login = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "correct horse"},
    )

    assert login.status_code == 204, login.text
    assert client.cookies.get(CSRF_COOKIE_NAME) is not None


def test_schema_extraction_preserves_legacy_user_sessions_csrf_column(tmp_path: Path) -> None:
    from seektalent_ui.workbench_db import connect_workbench_db
    from seektalent_ui.workbench_schema import initialize_workbench_schema

    db_path = tmp_path / "workbench.sqlite3"
    with connect_workbench_db(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE user_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        initialize_workbench_schema(conn, now="2026-06-11T00:00:00+00:00")
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(user_sessions)").fetchall()
        }

    assert "csrf_token_digest" in columns


def test_auth_service_records_failed_login_without_creating_session(tmp_path: Path) -> None:
    from seektalent_ui.auth import hash_password
    from seektalent_ui.workbench_auth_service import WorkbenchAuthService, WorkbenchLoginInput

    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    store.bootstrap_admin(
        email="admin@example.com",
        display_name="Admin User",
        password_hash=hash_password("correct horse"),
    )
    service = WorkbenchAuthService(store=store)

    result = service.login(
        WorkbenchLoginInput(
            email="admin@example.com",
            password="wrong password",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    )

    assert result.status == "invalid_credentials"
    assert result.session_tokens is None
    audit_actions = [event.action for event in store.list_security_audit_events()]
    assert audit_actions == ["bootstrap_admin_created", "login"]
    assert store.get_user_by_session(session_digest=None) is None
