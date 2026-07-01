from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seektalent_ui.server import create_app
from seektalent_ui.workbench_store import WorkbenchUser
from tests.settings_factory import make_settings


def _client(tmp_path: Path, *, base_url: str = "http://localhost") -> TestClient:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True, provider_name="cts")
    return TestClient(create_app(settings=settings), base_url=base_url, client=("127.0.0.1", 50000))


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / ".seektalent" / "workbench.sqlite3"


def _insert_legacy_admin_user(
    db_path: Path,
    *,
    user_id: str = "user_legacy_admin",
    email: str = "admin@example.com",
) -> WorkbenchUser:
    now = "2026-01-01T00:00:00Z"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at) VALUES ('local', 'Local', ?)",
            (now,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO workspaces (workspace_id, tenant_id, name, created_at)
            VALUES ('default', 'local', 'Default Workspace', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, password_hash, disabled_at, created_at)
            VALUES (?, ?, 'Legacy Admin', 'legacy-password-hash', NULL, ?)
            """,
            (user_id, email, now),
        )
        conn.execute(
            """
            INSERT INTO workspace_memberships (workspace_id, user_id, role, created_at)
            VALUES ('default', ?, 'admin', ?)
            """,
            (user_id, now),
        )
    return WorkbenchUser(
        user_id=user_id,
        email=email,
        display_name="Legacy Admin",
        role="admin",
        workspace_id="default",
    )


def test_workbench_store_ensures_local_actor_once(tmp_path: Path) -> None:
    from seektalent_ui.workbench_store import WorkbenchStore

    store = WorkbenchStore(tmp_path / "workbench.sqlite3")

    first = store.ensure_local_actor()
    second = store.ensure_local_actor()

    assert first == second
    assert first.user_id == "user_local"
    assert first.email == "local@seektalent.local"
    assert first.workspace_id == "default"
    assert first.role == "admin"


def test_workbench_store_does_not_reuse_legacy_admin_user(tmp_path: Path) -> None:
    from seektalent_ui.workbench_store import WorkbenchStore

    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    store._initialize()
    legacy_user = _insert_legacy_admin_user(db_path)

    actor = store.ensure_local_actor()

    assert actor.user_id == "user_local"
    assert actor.email == "local@seektalent.local"
    assert actor != legacy_user


def test_workbench_store_raises_when_local_actor_email_is_already_taken(tmp_path: Path) -> None:
    from seektalent_ui.workbench_store import WorkbenchStore

    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    store._initialize()
    _insert_legacy_admin_user(db_path, user_id="user_email_collision", email="local@seektalent.local")

    with pytest.raises(RuntimeError, match="Local Workbench actor identity is unavailable"):
        store.ensure_local_actor()


def test_workbench_store_raises_when_local_actor_has_wrong_email_case(tmp_path: Path) -> None:
    from seektalent_ui.workbench_store import WorkbenchStore

    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    store._initialize()
    _insert_legacy_admin_user(db_path, user_id="user_local", email="LOCAL@SEEKTALENT.LOCAL")

    with pytest.raises(RuntimeError, match="Local Workbench actor identity is unavailable"):
        store.ensure_local_actor()


def test_workbench_store_repairs_missing_local_actor_membership(tmp_path: Path) -> None:
    from seektalent_ui.workbench_store import WorkbenchStore

    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    store.ensure_local_actor()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM workspace_memberships")

    actor = store.ensure_local_actor()

    assert actor.user_id == "user_local"
    assert actor.workspace_id == "default"


def test_workbench_store_repairs_local_actor_metadata_and_role(tmp_path: Path) -> None:
    from seektalent_ui.workbench_store import WorkbenchStore

    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    store.ensure_local_actor()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE users
            SET display_name = 'Wrong Name', password_hash = 'wrong-password'
            WHERE user_id = 'user_local'
            """
        )
        conn.execute(
            """
            UPDATE workspace_memberships
            SET role = 'member'
            WHERE user_id = 'user_local' AND workspace_id = 'default'
            """
        )

    actor = store.ensure_local_actor()

    assert actor.user_id == "user_local"
    assert actor.display_name == "Local Workbench"
    assert actor.role == "admin"
    with sqlite3.connect(db_path) as conn:
        password_hash = conn.execute("SELECT password_hash FROM users WHERE user_id = 'user_local'").fetchone()[0]
    assert password_hash == "local_actor_no_password"


def test_workbench_auth_routes_are_removed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    paths = {route.path for route in client.app.routes}

    assert "/api/auth/bootstrap" not in paths
    assert "/api/auth/login" not in paths
    assert "/api/auth/logout" not in paths
    assert "/api/auth/me" not in paths
    assert client.get("/api/auth/me").status_code == 404
    assert client.post("/api/auth/login", json={"email": "admin@example.com", "password": "secret"}).status_code == 404


def test_fresh_workbench_is_usable_without_login_or_csrf(tmp_path: Path) -> None:
    client = _client(tmp_path)

    settings_response = client.get("/api/workbench/settings")
    created = client.post(
        "/api/workbench/sessions",
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores.", "notes": ""},
    )

    assert settings_response.status_code == 200, settings_response.text
    assert created.status_code == 201, created.text
    assert client.cookies.get("seektalent_workbench_session") is None
    assert client.cookies.get("seektalent_workbench_csrf") is None
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.email, m.workspace_id, m.role
            FROM users AS u
            JOIN workspace_memberships AS m ON m.user_id = u.user_id
            ORDER BY u.created_at, u.user_id
            """
        ).fetchall()
    assert rows == [("user_local", "local@seektalent.local", "default", "admin")]


def test_legacy_bootstrap_user_data_is_not_reused_without_login(tmp_path: Path) -> None:
    client = _client(tmp_path)
    store = client.app.state.workbench_store
    store._initialize()
    legacy_user = _insert_legacy_admin_user(_db_path(tmp_path))
    legacy_session = store.create_workbench_session(
        user=legacy_user,
        job_title="Legacy Engineer",
        jd_text="Legacy admin/password owned data is intentionally not migrated.",
        notes="",
        source_kinds=["cts"],
    )

    response = client.get("/api/workbench/sessions")

    assert response.status_code == 200, response.text
    session_ids = [item["id"] for item in response.json()["sessions"]]
    assert legacy_session.session_id not in session_ids
    assert store.ensure_local_actor().user_id == "user_local"


def test_write_routes_do_not_accept_csrf_as_authentication(tmp_path: Path) -> None:
    client = _client(tmp_path)

    wrong_csrf = client.post(
        "/api/agent/conversations",
        headers={"X-CSRF-Token": "wrong-token"},
        json={"title": "Python Agent Engineer"},
    )
    no_csrf = client.post("/api/agent/conversations", json={"title": "Python Agent Engineer 2"})

    assert wrong_csrf.status_code == 201, wrong_csrf.text
    assert no_csrf.status_code == 201, no_csrf.text


def test_host_origin_guard_still_blocks_cross_origin_writes(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/agent/conversations",
        headers={"Origin": "http://evil.example"},
        json={"title": "Blocked"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin is not allowed."
