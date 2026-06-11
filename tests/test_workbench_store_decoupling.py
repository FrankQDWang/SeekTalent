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
