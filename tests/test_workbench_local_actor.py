from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent_ui.workbench_store import WorkbenchUser


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


def test_workbench_store_does_not_reuse_legacy_bootstrap_admin(tmp_path: Path) -> None:
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
