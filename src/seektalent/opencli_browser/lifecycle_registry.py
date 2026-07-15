from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from seektalent.opencli_browser.contracts import (
    BrowserControlScope,
    OpenCliOwnedTab,
    OpenCliTabCloseResult,
    OpenCliTabKind,
)
from seektalent.opencli_browser.fault_isolation import isolated_call


_LOGGER = logging.getLogger(__name__)
_BACKGROUND_SQLITE_BUSY_TIMEOUT_MS = 100


@dataclass(frozen=True)
class RecoveredTab:
    scope_id: str
    tab: OpenCliOwnedTab


class BrowserControlRegistry:
    """Fail-open local ownership mirror; extension state remains authoritative."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ready = isolated_call(self._initialize, self._report_failure) is True

    @property
    def ready(self) -> bool:
        return self._ready

    def record_scope(self, scope: BrowserControlScope) -> None:
        self._write(lambda connection: self._insert_scope(connection, scope))

    def record_tab_allocation(
        self,
        scope: BrowserControlScope,
        *,
        tab_token: str,
        session: str,
        tab_kind: OpenCliTabKind,
    ) -> None:
        self._write(
            lambda connection: connection.execute(
                """
                INSERT INTO browser_owned_tabs (
                    tab_token, scope_id, session, tab_kind, state, created_at
                ) VALUES (?, ?, ?, ?, 'allocating', ?)
                """,
                (tab_token, scope.scope_id, session, tab_kind, time.time()),
            )
        )

    def record_owned_tab(self, scope: BrowserControlScope, tab: OpenCliOwnedTab) -> None:
        self._write(
            lambda connection: connection.execute(
                """
                UPDATE browser_owned_tabs
                SET page_id = ?, state = 'owned', idle_deadline_at = ?
                WHERE tab_token = ? AND scope_id = ? AND session = ?
                """,
                (tab.page_id, tab.idle_deadline_at, tab.tab_token, scope.scope_id, tab.session),
            )
        )

    def record_idle_deadline(self, tab: OpenCliOwnedTab) -> None:
        self._write(
            lambda connection: connection.execute(
                """
                UPDATE browser_owned_tabs
                SET idle_deadline_at = ?, last_command_completed_at = ?
                WHERE tab_token = ? AND session = ? AND page_id = ?
                """,
                (tab.idle_deadline_at, time.time(), tab.tab_token, tab.session, tab.page_id),
            )
        )

    def record_reclaim_requested(self, scope_id: str, tabs: tuple[OpenCliOwnedTab, ...]) -> None:
        def write(connection: sqlite3.Connection) -> None:
            now = time.time()
            connection.execute(
                """
                UPDATE browser_control_scopes
                SET state = 'reclaim_requested', reclaim_requested_at = ?
                WHERE scope_id = ?
                """,
                (now, scope_id),
            )
            connection.execute(
                """
                UPDATE browser_owned_tabs
                SET state = 'extension_fallback', reclaim_requested_at = ?
                WHERE scope_id = ? AND state = 'allocating'
                """,
                (now, scope_id),
            )
            connection.executemany(
                """
                UPDATE browser_owned_tabs
                SET state = 'reclaim_requested', reclaim_requested_at = ?
                WHERE tab_token = ? AND scope_id = ?
                """,
                ((now, tab.tab_token, scope_id) for tab in tabs),
            )

        self._write(write)

    def record_reclaim_result(self, scope_id: str, result: OpenCliTabCloseResult) -> None:
        state = "reclaimed" if result.outcome in {"closed", "already_missing"} else "reclaim_failed"

        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                UPDATE browser_owned_tabs
                SET state = ?, reclaimed_at = ?, close_outcome = ?, last_error_code = ?
                WHERE tab_token = ? AND scope_id = ?
                """,
                (
                    state,
                    time.time() if state == "reclaimed" else None,
                    result.outcome,
                    result.error_code,
                    result.tab_token,
                    scope_id,
                ),
            )
            remaining = connection.execute(
                "SELECT COUNT(*) FROM browser_owned_tabs WHERE scope_id = ? AND state != 'reclaimed'",
                (scope_id,),
            ).fetchone()
            if remaining == (0,):
                connection.execute(
                    """
                    UPDATE browser_control_scopes
                    SET state = 'reclaimed', reclaimed_at = ?
                    WHERE scope_id = ?
                    """,
                    (time.time(), scope_id),
                )

        self._write(write)

    def record_empty_scope_reclaimed(self, scope_id: str) -> None:
        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                UPDATE browser_owned_tabs
                SET state = 'extension_fallback'
                WHERE scope_id = ? AND state = 'allocating'
                """,
                (scope_id,),
            )
            connection.execute(
                """
                UPDATE browser_control_scopes
                SET state = 'reclaimed', reclaimed_at = ?
                WHERE scope_id = ?
                """,
                (time.time(), scope_id),
            )

        self._write(write)

    def pending_tabs(self) -> tuple[RecoveredTab, ...]:
        if not self._ready:
            return ()
        result = isolated_call(self._read_pending_tabs, self._report_failure)
        return result or ()

    def _initialize(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS browser_control_scopes (
                    scope_id TEXT PRIMARY KEY,
                    lane_key_hash TEXT NOT NULL,
                    fence_token INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    reclaim_requested_at REAL,
                    reclaimed_at REAL
                );
                CREATE INDEX IF NOT EXISTS browser_control_scope_lane
                    ON browser_control_scopes(lane_key_hash, state);
                CREATE TABLE IF NOT EXISTS browser_owned_tabs (
                    tab_token TEXT PRIMARY KEY,
                    scope_id TEXT NOT NULL REFERENCES browser_control_scopes(scope_id),
                    session TEXT NOT NULL UNIQUE,
                    page_id TEXT,
                    tab_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_command_completed_at REAL,
                    idle_deadline_at INTEGER,
                    reclaim_requested_at REAL,
                    reclaimed_at REAL,
                    close_outcome TEXT,
                    last_error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS browser_owned_tab_scope
                    ON browser_owned_tabs(scope_id, state);
                """
            )
        return True

    def _insert_scope(self, connection: sqlite3.Connection, scope: BrowserControlScope) -> None:
        lane_hash = hashlib.sha256(scope.control_key.encode("utf-8")).hexdigest()
        connection.execute(
            """
            UPDATE browser_control_scopes
            SET state = 'superseded'
            WHERE lane_key_hash = ? AND state = 'active'
            """,
            (lane_hash,),
        )
        connection.execute(
            """
            INSERT INTO browser_control_scopes (
                scope_id, lane_key_hash, fence_token, state, created_at
            ) VALUES (?, ?, ?, 'active', ?)
            """,
            (scope.scope_id, lane_hash, scope.fence_token, time.time()),
        )

    def _read_pending_tabs(self) -> tuple[RecoveredTab, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT scope_id, tab_token, session, page_id, tab_kind, idle_deadline_at
                FROM browser_owned_tabs
                WHERE page_id IS NOT NULL
                  AND state IN ('owned', 'reclaim_requested')
                ORDER BY created_at, tab_token
                """
            ).fetchall()
        recovered: list[RecoveredTab] = []
        for scope_id, tab_token, session, page_id, tab_kind, idle_deadline_at in rows:
            if tab_kind not in {"search", "detail"}:
                continue
            recovered.append(
                RecoveredTab(
                    scope_id=str(scope_id),
                    tab=OpenCliOwnedTab(
                        tab_token=str(tab_token),
                        session=str(session),
                        page_id=str(page_id),
                        tab_kind=tab_kind,
                        idle_deadline_at=idle_deadline_at if isinstance(idle_deadline_at, int) else None,
                    ),
                )
            )
        return tuple(recovered)

    def _write(self, action: Callable[[sqlite3.Connection], object]) -> None:
        if not self._ready:
            return

        def write() -> None:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                action(connection)
                connection.commit()

        isolated_call(write, self._report_failure)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=_BACKGROUND_SQLITE_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {_BACKGROUND_SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _report_failure(self, exc: Exception) -> None:
        _LOGGER.warning("browser_control_registry_failed error=%s", type(exc).__name__)
