from __future__ import annotations

from pathlib import Path
import sqlite3

from seektalent.source_port.history_contract import HistoryUnavailableReason, SQLITE_MAX_INTEGER


SCHEMA_VERSION = 1


class JournalWriteConflict(RuntimeError):
    pass


class JournalUnavailable(RuntimeError):
    def __init__(self, reason: HistoryUnavailableReason) -> None:
        super().__init__(reason)
        self.reason = reason


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
        mode = _scalar_text(connection, "PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        if mode.lower() != "delete":
            raise JournalUnavailable("pragma_mismatch")
        _verify_connection_pragmas(connection)
        _create_schema(connection)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        _verify_schema(connection)
    finally:
        connection.close()


def connect_existing(path: Path, *, begin_read: bool = False) -> sqlite3.Connection:
    _probe_existing_journal_mode(path)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path, isolation_level=None, timeout=0.1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=100")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        _verify_connection_pragmas(connection)
        connection.execute("BEGIN")
        _verify_schema(connection)
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


def generation_bounds(connection: sqlite3.Connection) -> tuple[int | None, int | None]:
    row = connection.execute(
        """
        SELECT MIN(CASE WHEN retained = 1 THEN generation END), MAX(generation)
        FROM source_history_generations
        """
    ).fetchone()
    if row is None:
        return None, None
    oldest = int(row[0]) if row[0] is not None else None
    newest = int(row[1]) if row[1] is not None else None
    return oldest, newest


def scalar_integer(connection: sqlite3.Connection, statement: str) -> int:
    row = connection.execute(statement).fetchone()
    if row is None or len(row) != 1 or not isinstance(row[0], int) or isinstance(row[0], bool):
        raise JournalUnavailable("schema_mismatch")
    return int(row[0])


def read_error(error: sqlite3.DatabaseError) -> JournalUnavailable:
    message = str(error).lower()
    if "locked" in message or "busy" in message:
        return JournalUnavailable("busy")
    if "unable to open" in message or "readonly" in message:
        return JournalUnavailable("unreadable")
    return JournalUnavailable("corrupt")


def write_error(error: sqlite3.OperationalError) -> JournalWriteConflict:
    if "locked" in str(error).lower() or "busy" in str(error).lower():
        return JournalWriteConflict("source_history_write_busy")
    return JournalWriteConflict("source_history_write_failed")


def _create_schema(connection: sqlite3.Connection) -> None:
    statements = (
        _STATE_TABLE_DDL,
        "INSERT INTO source_history_state(singleton, last_journal_revision) VALUES (1, 0)",
        _GENERATION_TABLE_DDL,
        _EVENT_TABLE_DDL,
        _HEAD_TABLE_DDL,
        *_TRIGGER_DDLS,
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _verify_connection_pragmas(connection: sqlite3.Connection) -> None:
    if _scalar_text(connection, "PRAGMA journal_mode").lower() != "delete":
        raise JournalUnavailable("pragma_mismatch")
    if scalar_integer(connection, "PRAGMA synchronous") != 2:
        raise JournalUnavailable("pragma_mismatch")
    if scalar_integer(connection, "PRAGMA foreign_keys") != 1:
        raise JournalUnavailable("pragma_mismatch")


def _verify_schema(connection: sqlite3.Connection) -> None:
    if scalar_integer(connection, "PRAGMA user_version") != SCHEMA_VERSION:
        raise JournalUnavailable("schema_mismatch")
    if _scalar_text(connection, "PRAGMA quick_check") != "ok":
        raise JournalUnavailable("corrupt")
    tables = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    required = {
        "source_history_state",
        "source_history_generations",
        "source_history_events",
        "source_history_heads",
    }
    if tables != required:
        raise JournalUnavailable("schema_mismatch")
    expected_columns = {
        "source_history_state": ("singleton", "last_journal_revision"),
        "source_history_generations": ("generation", "retained", "complete"),
        "source_history_events": _EVENT_COLUMN_NAMES,
        "source_history_heads": _HEAD_COLUMN_NAMES,
    }
    for table, expected in expected_columns.items():
        actual = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall())
        if actual != expected:
            raise JournalUnavailable("schema_mismatch")
    triggers = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    }
    if triggers != {
        "source_history_events_no_duplicate_revision",
        "source_history_events_no_update",
        "source_history_events_no_delete",
    }:
        raise JournalUnavailable("schema_mismatch")
    _verify_schema_sql(connection)
    _verify_foreign_keys(connection)
    try:
        _verify_journal_consistency(connection)
    except JournalUnavailable:
        raise
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        raise JournalUnavailable("corrupt") from exc


def _verify_schema_sql(connection: sqlite3.Connection) -> None:
    expected = {
        ("table", "source_history_state"): _STATE_TABLE_DDL,
        ("table", "source_history_generations"): _GENERATION_TABLE_DDL,
        ("table", "source_history_events"): _EVENT_TABLE_DDL,
        ("table", "source_history_heads"): _HEAD_TABLE_DDL,
        ("trigger", "source_history_events_no_duplicate_revision"): _TRIGGER_DDLS[0],
        ("trigger", "source_history_events_no_update"): _TRIGGER_DDLS[1],
        ("trigger", "source_history_events_no_delete"): _TRIGGER_DDLS[2],
    }
    actual = {
        (str(row[0]), str(row[1])): row[2]
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchall()
    }
    for key, statement in expected.items():
        stored = actual.get(key)
        if not isinstance(stored, str) or _normalize_schema_sql(stored) != _normalize_schema_sql(statement):
            raise JournalUnavailable("schema_mismatch")


def _normalize_schema_sql(statement: str) -> str:
    return " ".join(statement.strip().removesuffix(";").split())


def _verify_foreign_keys(connection: sqlite3.Connection) -> None:
    expected = {
        "source_history_events": {
            ("event_generation", "source_history_generations", "generation"),
            ("accepted_generation", "source_history_generations", "generation"),
            ("dispatch_intent_generation", "source_history_generations", "generation"),
            ("observation_generation", "source_history_generations", "generation"),
        },
        "source_history_heads": {
            ("accepted_generation", "source_history_generations", "generation"),
            ("head_generation", "source_history_generations", "generation"),
            ("dispatch_intent_generation", "source_history_generations", "generation"),
            ("observation_generation", "source_history_generations", "generation"),
        },
    }
    for table, expected_keys in expected.items():
        actual = {
            (str(row[3]), str(row[2]), str(row[4]))
            for row in connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        }
        if actual != expected_keys:
            raise JournalUnavailable("schema_mismatch")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise JournalUnavailable("corrupt")


def _verify_journal_consistency(connection: sqlite3.Connection) -> None:
    events = connection.execute("SELECT * FROM source_history_events ORDER BY journal_revision").fetchall()
    heads = connection.execute(
        """
        SELECT * FROM source_history_heads
        ORDER BY run_id, operation_id, dispatch_authorization_ordinal
        """
    ).fetchall()
    last_revision = scalar_integer(
        connection,
        "SELECT last_journal_revision FROM source_history_state WHERE singleton = 1",
    )
    newest_event_revision = int(events[-1]["journal_revision"]) if events else 0
    if last_revision != newest_event_revision:
        raise JournalUnavailable("corrupt")
    if any(
        int(event["journal_revision"]) != expected_revision for expected_revision, event in enumerate(events, start=1)
    ):
        raise JournalUnavailable("corrupt")

    event_groups: dict[tuple[str, str, int], list[sqlite3.Row]] = {}
    for event in events:
        key = (
            str(event["run_id"]),
            str(event["operation_id"]),
            int(event["dispatch_authorization_ordinal"]),
        )
        event_groups.setdefault(key, []).append(event)
    head_by_key = {
        (
            str(head["run_id"]),
            str(head["operation_id"]),
            int(head["dispatch_authorization_ordinal"]),
        ): head
        for head in heads
    }
    if set(event_groups) != set(head_by_key):
        raise JournalUnavailable("corrupt")

    expected_phases = {
        "accepted": ("accepted",),
        "dispatch_intent": ("accepted", "dispatch_intent"),
        "observed_result": ("accepted", "dispatch_intent", "observed_result"),
        "observed_failure": ("accepted", "dispatch_intent", "observed_failure"),
    }
    for key, head in head_by_key.items():
        grouped = event_groups[key]
        phases = tuple(str(event["phase"]) for event in grouped)
        generations = tuple(int(event["event_generation"]) for event in grouped)
        if phases != expected_phases.get(str(head["phase"])) or generations != tuple(sorted(generations)):
            raise JournalUnavailable("corrupt")
        if any(event[column] != head[column] for event in grouped for column in _IMMUTABLE_EVENT_HEAD_COLUMNS):
            raise JournalUnavailable("corrupt")

        accepted_event = grouped[0]
        if (
            int(accepted_event["journal_revision"]) != int(head["accepted_journal_revision"])
            or int(accepted_event["event_generation"]) != int(head["accepted_generation"])
            or any(accepted_event[column] is not None for column in _DISPATCH_AND_OBSERVATION_COLUMNS)
        ):
            raise JournalUnavailable("corrupt")
        if len(grouped) == 1 and any(head[column] is not None for column in _DISPATCH_AND_OBSERVATION_COLUMNS):
            raise JournalUnavailable("corrupt")

        if len(grouped) >= 2:
            dispatch_event = grouped[1]
            if (
                int(dispatch_event["journal_revision"]) != int(head["dispatch_intent_journal_revision"])
                or int(dispatch_event["event_generation"]) != int(head["dispatch_intent_generation"])
                or int(dispatch_event["dispatch_intent_journal_revision"]) != int(dispatch_event["journal_revision"])
                or int(dispatch_event["dispatch_intent_generation"]) != int(dispatch_event["event_generation"])
                or dispatch_event["durable_dispatch_intent_ref"] is None
                or dispatch_event["durable_dispatch_intent_ref"] != head["durable_dispatch_intent_ref"]
                or any(dispatch_event[column] is not None for column in _OBSERVATION_COLUMNS)
            ):
                raise JournalUnavailable("corrupt")
            if any(event[column] != head[column] for event in grouped[1:] for column in _DISPATCH_COLUMNS):
                raise JournalUnavailable("corrupt")
        if len(grouped) == 2 and any(head[column] is not None for column in _OBSERVATION_COLUMNS):
            raise JournalUnavailable("corrupt")

        if len(grouped) == 3:
            observation_event = grouped[2]
            if (
                int(observation_event["journal_revision"]) != int(head["observation_journal_revision"])
                or int(observation_event["event_generation"]) != int(head["observation_generation"])
                or int(observation_event["observation_journal_revision"]) != int(observation_event["journal_revision"])
                or int(observation_event["observation_generation"]) != int(observation_event["event_generation"])
                or observation_event["observation_ref"] is None
                or observation_event["observation_hash"] is None
                or observation_event["observation_ref"] != head["observation_ref"]
                or observation_event["observation_hash"] != head["observation_hash"]
            ):
                raise JournalUnavailable("corrupt")

        latest = grouped[-1]
        if int(latest["journal_revision"]) != int(head["head_journal_revision"]) or int(
            latest["event_generation"]
        ) != int(head["head_generation"]):
            raise JournalUnavailable("corrupt")


def _probe_existing_journal_mode(path: Path) -> None:
    if not path.is_file():
        raise JournalUnavailable("unreadable")
    try:
        with path.open("rb") as database:
            header = database.read(100)
    except OSError as exc:
        raise JournalUnavailable("unreadable") from exc
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        raise JournalUnavailable("corrupt")
    read_version = header[19]
    write_version = header[18]
    if (write_version, read_version) == (2, 2):
        raise JournalUnavailable("pragma_mismatch")
    if (write_version, read_version) != (1, 1):
        raise JournalUnavailable("corrupt")


def _scalar_text(connection: sqlite3.Connection, statement: str) -> str:
    row = connection.execute(statement).fetchone()
    if row is None or len(row) != 1 or not isinstance(row[0], str):
        raise JournalUnavailable("schema_mismatch")
    return row[0]


_EVENT_COLUMN_NAMES = (
    "journal_revision",
    "event_generation",
    "phase",
    "run_id",
    "operation_id",
    "source",
    "operation_kind",
    "idempotency_key",
    "request_hash",
    "attempt_no",
    "dispatch_authorization_ordinal",
    "accepted_requirement_revision_id",
    "runtime_attempt_fence_ref",
    "accepted_generation",
    "accepted_journal_revision",
    "authorized_dispatch_intent_id",
    "authorized_dispatch_intent_revision",
    "authorized_dispatch_intent_digest",
    "profile_binding_generation",
    "browser_control_scope_id",
    "controller_fence_ref",
    "durable_dispatch_intent_ref",
    "dispatch_intent_generation",
    "dispatch_intent_journal_revision",
    "observation_generation",
    "observation_journal_revision",
    "observation_ref",
    "observation_hash",
)

_HEAD_COLUMN_NAMES = (
    "run_id",
    "operation_id",
    "source",
    "operation_kind",
    "idempotency_key",
    "request_hash",
    "attempt_no",
    "dispatch_authorization_ordinal",
    "accepted_requirement_revision_id",
    "runtime_attempt_fence_ref",
    "accepted_generation",
    "accepted_journal_revision",
    "authorized_dispatch_intent_id",
    "authorized_dispatch_intent_revision",
    "authorized_dispatch_intent_digest",
    "profile_binding_generation",
    "browser_control_scope_id",
    "controller_fence_ref",
    "phase",
    "head_generation",
    "head_journal_revision",
    "durable_dispatch_intent_ref",
    "dispatch_intent_generation",
    "dispatch_intent_journal_revision",
    "observation_generation",
    "observation_journal_revision",
    "observation_ref",
    "observation_hash",
)

_IMMUTABLE_EVENT_HEAD_COLUMNS = (
    "run_id",
    "operation_id",
    "source",
    "operation_kind",
    "idempotency_key",
    "request_hash",
    "attempt_no",
    "dispatch_authorization_ordinal",
    "accepted_requirement_revision_id",
    "runtime_attempt_fence_ref",
    "accepted_generation",
    "accepted_journal_revision",
    "authorized_dispatch_intent_id",
    "authorized_dispatch_intent_revision",
    "authorized_dispatch_intent_digest",
    "profile_binding_generation",
    "browser_control_scope_id",
    "controller_fence_ref",
)

_OBSERVATION_COLUMNS = (
    "observation_generation",
    "observation_journal_revision",
    "observation_ref",
    "observation_hash",
)
_DISPATCH_COLUMNS = (
    "durable_dispatch_intent_ref",
    "dispatch_intent_generation",
    "dispatch_intent_journal_revision",
)
_DISPATCH_AND_OBSERVATION_COLUMNS = (
    *_DISPATCH_COLUMNS,
    *_OBSERVATION_COLUMNS,
)

_STATE_TABLE_DDL = f"""
CREATE TABLE source_history_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    last_journal_revision INTEGER NOT NULL
        CHECK(typeof(last_journal_revision) = 'integer')
        CHECK(last_journal_revision BETWEEN 0 AND {SQLITE_MAX_INTEGER})
)
"""

_GENERATION_TABLE_DDL = """
CREATE TABLE source_history_generations (
    generation INTEGER PRIMARY KEY CHECK(generation >= 1),
    retained INTEGER NOT NULL CHECK(retained IN (0, 1)),
    complete INTEGER NOT NULL CHECK(complete IN (0, 1))
)
"""

_EVENT_TABLE_DDL = """
CREATE TABLE source_history_events (
    journal_revision INTEGER PRIMARY KEY CHECK(journal_revision >= 1),
    event_generation INTEGER NOT NULL REFERENCES source_history_generations(generation),
    phase TEXT NOT NULL CHECK(phase IN ('accepted', 'dispatch_intent', 'observed_result', 'observed_failure')),
    run_id TEXT NOT NULL, operation_id TEXT NOT NULL, source TEXT NOT NULL,
    operation_kind TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    dispatch_authorization_ordinal INTEGER NOT NULL CHECK(dispatch_authorization_ordinal = 1),
    accepted_requirement_revision_id TEXT NOT NULL, runtime_attempt_fence_ref TEXT NOT NULL,
    accepted_generation INTEGER NOT NULL REFERENCES source_history_generations(generation),
    accepted_journal_revision INTEGER NOT NULL, authorized_dispatch_intent_id TEXT NOT NULL,
    authorized_dispatch_intent_revision INTEGER NOT NULL, authorized_dispatch_intent_digest TEXT NOT NULL,
    profile_binding_generation INTEGER NOT NULL, browser_control_scope_id TEXT, controller_fence_ref TEXT,
    durable_dispatch_intent_ref TEXT,
    dispatch_intent_generation INTEGER REFERENCES source_history_generations(generation),
    dispatch_intent_journal_revision INTEGER,
    observation_generation INTEGER REFERENCES source_history_generations(generation),
    observation_journal_revision INTEGER, observation_ref TEXT, observation_hash TEXT
)
"""

_HEAD_TABLE_DDL = """
CREATE TABLE source_history_heads (
    run_id TEXT NOT NULL, operation_id TEXT NOT NULL, source TEXT NOT NULL,
    operation_kind TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    dispatch_authorization_ordinal INTEGER NOT NULL CHECK(dispatch_authorization_ordinal = 1),
    accepted_requirement_revision_id TEXT NOT NULL, runtime_attempt_fence_ref TEXT NOT NULL,
    accepted_generation INTEGER NOT NULL REFERENCES source_history_generations(generation),
    accepted_journal_revision INTEGER NOT NULL, authorized_dispatch_intent_id TEXT NOT NULL,
    authorized_dispatch_intent_revision INTEGER NOT NULL, authorized_dispatch_intent_digest TEXT NOT NULL,
    profile_binding_generation INTEGER NOT NULL, browser_control_scope_id TEXT, controller_fence_ref TEXT,
    phase TEXT NOT NULL CHECK(phase IN ('accepted', 'dispatch_intent', 'observed_result', 'observed_failure')),
    head_generation INTEGER NOT NULL REFERENCES source_history_generations(generation),
    head_journal_revision INTEGER NOT NULL, durable_dispatch_intent_ref TEXT,
    dispatch_intent_generation INTEGER REFERENCES source_history_generations(generation),
    dispatch_intent_journal_revision INTEGER,
    observation_generation INTEGER REFERENCES source_history_generations(generation),
    observation_journal_revision INTEGER, observation_ref TEXT, observation_hash TEXT,
    PRIMARY KEY(run_id, operation_id, dispatch_authorization_ordinal)
)
"""

_TRIGGER_DDLS = (
    """
    CREATE TRIGGER source_history_events_no_duplicate_revision
    BEFORE INSERT ON source_history_events
    WHEN EXISTS (
        SELECT 1 FROM source_history_events
        WHERE journal_revision = NEW.journal_revision
    )
    BEGIN SELECT RAISE(ABORT, 'source_history_events_immutable'); END
    """,
    """
    CREATE TRIGGER source_history_events_no_update
    BEFORE UPDATE ON source_history_events
    BEGIN SELECT RAISE(ABORT, 'source_history_events_immutable'); END
    """,
    """
    CREATE TRIGGER source_history_events_no_delete
    BEFORE DELETE ON source_history_events
    BEGIN SELECT RAISE(ABORT, 'source_history_events_immutable'); END
    """,
)
