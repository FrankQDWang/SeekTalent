from __future__ import annotations

from pathlib import Path
import sqlite3

from seektalent.source_port.history_contract import SQLITE_MAX_INTEGER
from seektalent.source_port.history_sqlite_reader import (
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    HistorySQLiteUnavailable,
    probe_existing_database,
    read_error,
    scalar_integer,
    scalar_text,
    verify_connection_pragmas,
    verify_schema,
)


JournalUnavailable = HistorySQLiteUnavailable


class JournalWriteConflict(RuntimeError):
    pass


class Transaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.connection.close()
            raise
        return self.connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


def create_database(path: Path) -> None:
    if path.exists():
        raise ValueError("source_history_create_path_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        mode = scalar_text(connection, "PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        if mode.lower() != "delete":
            raise JournalUnavailable("pragma_mismatch")
        verify_connection_pragmas(connection)
        _create_schema(connection)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        verify_schema(connection)
    finally:
        connection.close()


def connect_existing(path: Path, *, begin_read: bool = False) -> sqlite3.Connection:
    probe_existing_database(path)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path, isolation_level=None, timeout=0.1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=100")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        verify_connection_pragmas(connection)
        connection.execute("BEGIN")
        verify_schema(connection)
        if not begin_read:
            connection.rollback()
        return connection
    except JournalUnavailable:
        if connection is not None:
            connection.close()
        raise
    except sqlite3.DatabaseError as exc:
        if connection is not None:
            connection.close()
        raise read_error(exc) from exc


def allocate_revision(connection: sqlite3.Connection) -> int:
    last_revision = scalar_integer(
        connection,
        "SELECT last_journal_revision FROM source_history_state WHERE singleton = 1",
    )
    if last_revision < 0 or last_revision >= SQLITE_MAX_INTEGER:
        raise JournalWriteConflict("source_history_revision_exhausted")
    revision = last_revision + 1
    cursor = connection.execute(
        """
        UPDATE source_history_state
        SET last_journal_revision = ?
        WHERE singleton = 1 AND last_journal_revision = ?
        """,
        (revision, last_revision),
    )
    if cursor.rowcount != 1:
        raise JournalWriteConflict("source_history_revision_cas_failed")
    return revision


def write_error(error: sqlite3.OperationalError) -> JournalWriteConflict:
    if "locked" in str(error).lower() or "busy" in str(error).lower():
        return JournalWriteConflict("source_history_write_busy")
    return JournalWriteConflict("source_history_write_failed")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
