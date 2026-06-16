from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from seektalent_ui.workbench_store_helpers import now_iso as _now_iso
from seektalent_ui.workbench_store_types import (
    DEFAULT_TENANT_ID,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    WorkbenchUser,
)


ConnectWorkbenchDb = Callable[[], AbstractContextManager[sqlite3.Connection]]
InitializeWorkbenchStore = Callable[[], None]
UserFromRow = Callable[[sqlite3.Row], WorkbenchUser]

LOCAL_ACTOR_USER_ID = "user_local"
LOCAL_ACTOR_EMAIL = "local@seektalent.local"
LOCAL_ACTOR_DISPLAY_NAME = "Local Workbench"
LOCAL_ACTOR_PASSWORD_SENTINEL = "local_actor_no_password"


class WorkbenchActorStore:
    def __init__(
        self,
        *,
        connect: ConnectWorkbenchDb,
        initialize: InitializeWorkbenchStore,
        user_from_row: UserFromRow,
    ) -> None:
        self._connect = connect
        self._initialize = initialize
        self._user_from_row = user_from_row

    def ensure_local_actor(self) -> WorkbenchUser:
        self._initialize()
        with self._connect() as conn:
            actor = self._local_actor_with_membership(conn)
            if actor is not None and self._is_complete_local_actor(actor):
                return self._user_from_row(actor)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            conn.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at) VALUES (?, ?, ?)",
                (DEFAULT_TENANT_ID, "Local", now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO workspaces (workspace_id, tenant_id, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (DEFAULT_WORKSPACE_ID, DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_NAME, now),
            )
            row = self._user_by_id(conn, user_id=LOCAL_ACTOR_USER_ID)
            if row is not None and (row["disabled_at"] is not None or row["email"] != LOCAL_ACTOR_EMAIL):
                raise RuntimeError("Local Workbench actor identity is unavailable.")
            if row is None:
                if self._local_actor_identity_exists(conn):
                    raise RuntimeError("Local Workbench actor identity is unavailable.")
                conn.execute(
                    """
                    INSERT INTO users (user_id, email, display_name, password_hash, disabled_at, created_at)
                    VALUES (?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        LOCAL_ACTOR_USER_ID,
                        LOCAL_ACTOR_EMAIL,
                        LOCAL_ACTOR_DISPLAY_NAME,
                        LOCAL_ACTOR_PASSWORD_SENTINEL,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET display_name = ?, password_hash = ?
                    WHERE user_id = ?
                    """,
                    (LOCAL_ACTOR_DISPLAY_NAME, LOCAL_ACTOR_PASSWORD_SENTINEL, LOCAL_ACTOR_USER_ID),
                )
            conn.execute(
                """
                INSERT INTO workspace_memberships (workspace_id, user_id, role, created_at)
                VALUES (?, ?, 'admin', ?)
                ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = excluded.role
                """,
                (DEFAULT_WORKSPACE_ID, LOCAL_ACTOR_USER_ID, now),
            )
            actor = self._local_actor_with_membership(conn)
        if actor is None or not self._is_complete_local_actor(actor):
            raise RuntimeError("Local Workbench actor was not created.")
        return self._user_from_row(actor)

    def _user_by_id(self, conn: sqlite3.Connection, *, user_id: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT user_id, email, display_name, password_hash, disabled_at
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    def _local_actor_with_membership(self, conn: sqlite3.Connection) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT u.user_id, u.email, u.display_name, u.password_hash, u.disabled_at,
                   m.workspace_id, m.role
            FROM users AS u
            JOIN workspace_memberships AS m ON m.user_id = u.user_id
            WHERE u.user_id = ? AND m.workspace_id = ?
            """,
            (LOCAL_ACTOR_USER_ID, DEFAULT_WORKSPACE_ID),
        ).fetchone()

    def _is_complete_local_actor(self, row: sqlite3.Row) -> bool:
        return (
            row["email"] == LOCAL_ACTOR_EMAIL
            and row["display_name"] == LOCAL_ACTOR_DISPLAY_NAME
            and row["password_hash"] == LOCAL_ACTOR_PASSWORD_SENTINEL
            and row["disabled_at"] is None
            and row["role"] == "admin"
        )

    def _local_actor_identity_exists(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ? OR email = ?",
            (LOCAL_ACTOR_USER_ID, LOCAL_ACTOR_EMAIL),
        ).fetchone()
        return row is not None
