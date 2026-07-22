"""Durable, production-unreachable SQLite command-journal writer."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
from typing import Literal, Never
import weakref

from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ObservedFailureFact,
    ObservedResultFact,
    OperationKind,
    SQLITE_MAX_INTEGER,
)
from seektalent.source_port.history_sqlite_reader import (
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    HistorySQLiteUnavailable,
    load_validated_history_facts,
    probe_existing_database,
    scalar_integer,
    scalar_text,
    verify_connection_pragmas,
    verify_schema,
)


__all__ = [
    "AcceptedCommand",
    "CommandJournal",
    "CommandJournalConflict",
    "CommandJournalConflictReason",
    "CommandJournalError",
    "CommandJournalErrorReason",
    "CommandJournalSession",
    "create_command_journal",
    "open_command_journal",
]


BUSY_TIMEOUT_MILLISECONDS = 1_000


class CommandJournalErrorReason(StrEnum):
    BUSY = "busy"
    CANNOT_OPEN = "cannot_open"
    CORRUPT = "corrupt"
    FULL = "full"
    IO_ERROR = "io_error"
    PRAGMA_MISMATCH = "pragma_mismatch"
    READONLY = "readonly"
    SCHEMA_MISMATCH = "schema_mismatch"


class CommandJournalConflictReason(StrEnum):
    ACCEPTANCE_REPLAY_CONFLICT = "acceptance_replay_conflict"
    CREATE_PATH_EXISTS = "create_path_exists"
    DISPATCH_REPLAY_CONFLICT = "dispatch_replay_conflict"
    GENERATION_EXHAUSTED = "generation_exhausted"
    HEAD_CAS_FAILED = "head_cas_failed"
    HEAD_MISSING = "head_missing"
    IDENTITY_CONFLICT = "identity_conflict"
    INSTANCE_ID_CONFLICT = "instance_id_conflict"
    OBSERVATION_REPLAY_CONFLICT = "observation_replay_conflict"
    OBSERVATION_WITHOUT_DISPATCH = "observation_without_dispatch"
    PHASE_ROLLBACK = "phase_rollback"
    REVISION_EXHAUSTED = "revision_exhausted"
    SESSION_GENERATION_INVALID = "session_generation_invalid"
    STALE_HEAD_REVISION = "stale_head_revision"


class CommandJournalError(RuntimeError):
    """A closed SQLite lifecycle or storage failure."""

    def __init__(self, reason: CommandJournalErrorReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class CommandJournalConflict(RuntimeError):
    """A durable command transition did not match the current journal head."""

    def __init__(self, reason: CommandJournalConflictReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedCommand:
    """Allowlisted command identity and acceptance facts for ordinal one."""

    run_id: str
    operation_id: str
    source: Literal["liepin"]
    operation_kind: OperationKind
    idempotency_key: str
    request_hash: str
    attempt_no: int
    accepted_requirement_revision_id: str
    runtime_attempt_fence_ref: str
    authorized_dispatch_intent_id: str
    authorized_dispatch_intent_revision: int
    authorized_dispatch_intent_digest: str
    profile_binding_generation: int
    browser_control_scope_id: str | None = None
    controller_fence_ref: str | None = None
    dispatch_authorization_ordinal: Literal[1] = 1


@dataclass(slots=True)
class _JournalState:
    path: Path


@dataclass(slots=True)
class _SessionState:
    path: Path
    generation: int
    instance_id: str


_JOURNALS: dict[int, tuple[weakref.ReferenceType["CommandJournal"], _JournalState]] = {}
_SESSIONS: dict[int, tuple[weakref.ReferenceType["CommandJournalSession"], _SessionState]] = {}
_FACTORY_LOCK = threading.Lock()


class CommandJournal:
    """Factory-only lifecycle for one explicit SQLite command journal."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CommandJournal is factory-only")

    @property
    def path(self) -> Path:
        return _journal_state(self).path

    def start(self) -> CommandJournalSession:
        """Allocate and persist one fresh sidecar generation capability."""
        state = _journal_state(self)
        generation, instance_id = _start_generation(state.path)
        return _new_session(state.path, generation=generation, instance_id=instance_id)

    def close(self) -> None:
        with _FACTORY_LOCK:
            entry = _JOURNALS.get(id(self))
            if entry is None or entry[0]() is not self:
                raise TypeError("CommandJournal must be a live factory journal")
            _JOURNALS.pop(id(self), None)

    def __copy__(self) -> Never:
        raise TypeError("CommandJournal cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("CommandJournal cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("CommandJournal cannot be serialized")


class CommandJournalSession:
    """Factory-only write authority for one durable sidecar generation."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CommandJournalSession is factory-only")

    @property
    def generation(self) -> int:
        return _session_state(self).generation

    @property
    def instance_id(self) -> str:
        return _session_state(self).instance_id

    def close(self) -> None:
        with _FACTORY_LOCK:
            entry = _SESSIONS.get(id(self))
            if entry is None or entry[0]() is not self:
                raise TypeError("CommandJournalSession must be a live factory session")
            _SESSIONS.pop(id(self), None)

    def record_accepted(self, accepted: AcceptedCommand) -> int:
        """Atomically persist the accepted phase for one command."""
        state = _session_state(self)
        return _record_accepted(state, accepted)

    def record_dispatch_intent(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        durable_dispatch_intent_ref: str,
    ) -> int:
        """Atomically persist a dispatch intent before any external effect."""
        state = _session_state(self)
        return _record_dispatch_intent(
            state,
            run_id=run_id,
            operation_id=operation_id,
            expected_head_journal_revision=expected_head_journal_revision,
            durable_dispatch_intent_ref=durable_dispatch_intent_ref,
        )

    def record_observed_result(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        result_ref: str,
        result_hash: str,
    ) -> int:
        """Atomically persist one observed result."""
        state = _session_state(self)
        return _record_observation(
            state,
            run_id=run_id,
            operation_id=operation_id,
            expected_head_journal_revision=expected_head_journal_revision,
            observation_kind="observed_result",
            observation_ref=result_ref,
            observation_hash=result_hash,
        )

    def record_observed_failure(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        failure_ref: str,
        failure_hash: str,
    ) -> int:
        """Atomically persist one observed failure."""
        state = _session_state(self)
        return _record_observation(
            state,
            run_id=run_id,
            operation_id=operation_id,
            expected_head_journal_revision=expected_head_journal_revision,
            observation_kind="observed_failure",
            observation_ref=failure_ref,
            observation_hash=failure_hash,
        )

    def __copy__(self) -> Never:
        raise TypeError("CommandJournalSession cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("CommandJournalSession cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("CommandJournalSession cannot be serialized")


def create_command_journal(path: Path) -> CommandJournal:
    """Create one new journal at an explicit absolute path without fallback."""
    _require_explicit_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
    except FileExistsError:
        raise CommandJournalConflict(CommandJournalConflictReason.CREATE_PATH_EXISTS) from None
    except OSError as exc:
        raise _path_error(exc) from None

    _initialize_database(path)
    return _new_journal(path)


def open_command_journal(path: Path) -> CommandJournal:
    """Open and validate one existing journal without repairing it."""
    _require_explicit_path(path)
    _validate_existing_journal(path)
    return _new_journal(path)


def _new_journal(path: Path) -> CommandJournal:
    journal = object.__new__(CommandJournal)
    journal_id = id(journal)

    def finalize(_: weakref.ReferenceType[CommandJournal]) -> None:
        with _FACTORY_LOCK:
            _JOURNALS.pop(journal_id, None)

    with _FACTORY_LOCK:
        _JOURNALS[journal_id] = (weakref.ref(journal, finalize), _JournalState(path=path))
    return journal


def _new_session(path: Path, *, generation: int, instance_id: str) -> CommandJournalSession:
    session = object.__new__(CommandJournalSession)
    session_id = id(session)

    def finalize(_: weakref.ReferenceType[CommandJournalSession]) -> None:
        with _FACTORY_LOCK:
            _SESSIONS.pop(session_id, None)

    state = _SessionState(path=path, generation=generation, instance_id=instance_id)
    with _FACTORY_LOCK:
        _SESSIONS[session_id] = (weakref.ref(session, finalize), state)
    return session


def _journal_state(journal: CommandJournal) -> _JournalState:
    if type(journal) is not CommandJournal:
        raise TypeError("CommandJournal must be a live factory journal")
    with _FACTORY_LOCK:
        entry = _JOURNALS.get(id(journal))
    if entry is None or entry[0]() is not journal:
        raise TypeError("CommandJournal must be a live factory journal")
    return entry[1]


def _session_state(session: CommandJournalSession) -> _SessionState:
    if type(session) is not CommandJournalSession:
        raise TypeError("CommandJournalSession must be a live factory session")
    with _FACTORY_LOCK:
        entry = _SESSIONS.get(id(session))
    if entry is None or entry[0]() is not session:
        raise TypeError("CommandJournalSession must be a live factory session")
    return entry[1]


def _require_explicit_path(path: Path) -> None:
    if not isinstance(path, Path):
        raise TypeError("command journal path must be a Path")
    if not path.is_absolute():
        raise ValueError("command journal path must be absolute")


def _initialize_database(path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_MILLISECONDS / 1_000)
        connection.row_factory = sqlite3.Row
        _configure_connection(connection, creating=True)
        connection.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.commit()
        verify_connection_pragmas(connection)
        verify_schema(connection)
    except HistorySQLiteUnavailable as exc:
        raise _validation_error(exc) from None
    except sqlite3.Error as exc:
        raise _sqlite_error(exc) from None
    finally:
        if connection is not None:
            connection.close()


def _validate_existing_journal(path: Path) -> None:
    connection = _open_write_connection(path)
    try:
        connection.execute("BEGIN")
        try:
            verify_schema(connection)
            load_validated_history_facts(connection)
        finally:
            _rollback(connection)
    except HistorySQLiteUnavailable as exc:
        raise _validation_error(exc) from None
    except sqlite3.Error as exc:
        raise _sqlite_error(exc) from None
    finally:
        connection.close()


def _start_generation(path: Path) -> tuple[int, str]:
    instance_id = secrets.token_hex(32)
    with _write_transaction(path) as connection:
        last_generation = scalar_integer(
            connection,
            "SELECT last_sidecar_generation FROM source_history_state WHERE singleton = 1",
        )
        if last_generation < 0 or last_generation >= SQLITE_MAX_INTEGER:
            raise CommandJournalConflict(CommandJournalConflictReason.GENERATION_EXHAUSTED)
        generation = last_generation + 1
        try:
            connection.execute(
                """
                INSERT INTO source_history_generations(
                    generation, sidecar_instance_id, retained, complete
                ) VALUES (?, ?, 1, 1)
                """,
                (generation, instance_id),
            )
        except sqlite3.IntegrityError as exc:
            if "sidecar_instance_id" in str(exc):
                raise CommandJournalConflict(CommandJournalConflictReason.INSTANCE_ID_CONFLICT) from None
            raise
        _startup_checkpoint("after_generation_insert")
        cursor = connection.execute(
            """
            UPDATE source_history_state
            SET last_sidecar_generation = ?
            WHERE singleton = 1 AND last_sidecar_generation = ?
            """,
            (generation, last_generation),
        )
        if cursor.rowcount != 1:
            raise CommandJournalConflict(CommandJournalConflictReason.SESSION_GENERATION_INVALID)
        _startup_checkpoint("after_generation_cas")
    _startup_commit_acknowledged()
    return generation, instance_id


def _record_accepted(state: _SessionState, accepted: AcceptedCommand) -> int:
    if type(accepted) is not AcceptedCommand:
        raise TypeError("accepted command must be an AcceptedCommand")
    _validate_accepted_input(accepted, generation=state.generation)
    with _write_transaction(state.path) as connection:
        _require_session_generation(connection, state)
        existing = _find_operation_head(
            connection,
            run_id=accepted.run_id,
            operation_id=accepted.operation_id,
            ordinal=accepted.dispatch_authorization_ordinal,
        )
        if existing is not None:
            if not _same_accepted_identity(existing, accepted):
                raise CommandJournalConflict(CommandJournalConflictReason.IDENTITY_CONFLICT)
            if str(existing["phase"]) != "accepted":
                raise CommandJournalConflict(CommandJournalConflictReason.PHASE_ROLLBACK)
            if int(existing["accepted_generation"]) != state.generation:
                raise CommandJournalConflict(CommandJournalConflictReason.ACCEPTANCE_REPLAY_CONFLICT)
            return int(existing["accepted_journal_revision"])

        _require_no_identity_collision(connection, accepted)
        revision = _allocate_revision(connection)
        fact = AcceptedNoDispatchFact.model_validate(
            {
                **_accepted_fact_values(accepted),
                "conclusion": "accepted_no_dispatch",
                "accepted_generation": state.generation,
                "accepted_journal_revision": revision,
                "head_generation": state.generation,
                "head_journal_revision": revision,
            },
            strict=True,
        )
        connection.execute(_EVENT_INSERT, _accepted_event_parameters(fact))
        _transition_checkpoint("after_event_insert")
        connection.execute(_HEAD_INSERT, _accepted_head_parameters(fact))
        _transition_checkpoint("after_head_cas")
    _transition_commit_acknowledged()
    return revision


def _record_dispatch_intent(
    state: _SessionState,
    *,
    run_id: str,
    operation_id: str,
    expected_head_journal_revision: int,
    durable_dispatch_intent_ref: str,
) -> int:
    _require_positive_integer(expected_head_journal_revision, "expected_head_journal_revision")
    with _write_transaction(state.path) as connection:
        _require_session_generation(connection, state)
        head = _require_head(connection, run_id=run_id, operation_id=operation_id)
        phase = str(head["phase"])
        if phase == "dispatch_intent":
            if (
                int(head["accepted_journal_revision"]) != expected_head_journal_revision
                or int(head["dispatch_intent_generation"]) != state.generation
                or head["durable_dispatch_intent_ref"] != durable_dispatch_intent_ref
            ):
                raise CommandJournalConflict(CommandJournalConflictReason.DISPATCH_REPLAY_CONFLICT)
            return int(head["dispatch_intent_journal_revision"])
        if phase in {"observed_result", "observed_failure"}:
            raise CommandJournalConflict(CommandJournalConflictReason.PHASE_ROLLBACK)
        if phase != "accepted":
            raise CommandJournalConflict(CommandJournalConflictReason.PHASE_ROLLBACK)
        if int(head["head_journal_revision"]) != expected_head_journal_revision:
            raise CommandJournalConflict(CommandJournalConflictReason.STALE_HEAD_REVISION)

        revision = _allocate_revision(connection)
        fact = DispatchNotObservedFact.model_validate(
            {
                **_accepted_values_from_row(head),
                "conclusion": "dispatch_not_observed",
                "head_generation": state.generation,
                "head_journal_revision": revision,
                "durable_dispatch_intent_ref": durable_dispatch_intent_ref,
                "dispatch_intent_generation": state.generation,
                "dispatch_intent_journal_revision": revision,
            },
            strict=True,
        )
        connection.execute(_EVENT_INSERT, _transition_event_parameters(fact, phase="dispatch_intent"))
        _transition_checkpoint("after_event_insert")
        cursor = connection.execute(
            """
            UPDATE source_history_heads
            SET phase = 'dispatch_intent',
                head_generation = ?,
                head_journal_revision = ?,
                durable_dispatch_intent_ref = ?,
                dispatch_intent_generation = ?,
                dispatch_intent_journal_revision = ?
            WHERE run_id = ? AND operation_id = ?
              AND dispatch_authorization_ordinal = 1
              AND head_journal_revision = ? AND phase = 'accepted'
            """,
            (
                state.generation,
                revision,
                durable_dispatch_intent_ref,
                state.generation,
                revision,
                run_id,
                operation_id,
                expected_head_journal_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise CommandJournalConflict(CommandJournalConflictReason.HEAD_CAS_FAILED)
        _transition_checkpoint("after_head_cas")
    _transition_commit_acknowledged()
    return revision


def _record_observation(
    state: _SessionState,
    *,
    run_id: str,
    operation_id: str,
    expected_head_journal_revision: int,
    observation_kind: Literal["observed_result", "observed_failure"],
    observation_ref: str,
    observation_hash: str,
) -> int:
    _require_positive_integer(expected_head_journal_revision, "expected_head_journal_revision")
    with _write_transaction(state.path) as connection:
        _require_session_generation(connection, state)
        head = _require_head(connection, run_id=run_id, operation_id=operation_id)
        phase = str(head["phase"])
        if phase in {"observed_result", "observed_failure"}:
            if phase != observation_kind:
                raise CommandJournalConflict(CommandJournalConflictReason.PHASE_ROLLBACK)
            if (
                int(head["dispatch_intent_journal_revision"]) != expected_head_journal_revision
                or int(head["observation_generation"]) != state.generation
                or head["observation_ref"] != observation_ref
                or head["observation_hash"] != observation_hash
            ):
                raise CommandJournalConflict(CommandJournalConflictReason.OBSERVATION_REPLAY_CONFLICT)
            return int(head["observation_journal_revision"])
        if phase != "dispatch_intent":
            raise CommandJournalConflict(CommandJournalConflictReason.OBSERVATION_WITHOUT_DISPATCH)
        if int(head["head_journal_revision"]) != expected_head_journal_revision:
            raise CommandJournalConflict(CommandJournalConflictReason.STALE_HEAD_REVISION)

        revision = _allocate_revision(connection)
        common = {
            **_accepted_values_from_row(head),
            "head_generation": state.generation,
            "head_journal_revision": revision,
            "durable_dispatch_intent_ref": head["durable_dispatch_intent_ref"],
            "dispatch_intent_generation": int(head["dispatch_intent_generation"]),
            "dispatch_intent_journal_revision": int(head["dispatch_intent_journal_revision"]),
            "observation_generation": state.generation,
            "observation_journal_revision": revision,
        }
        if observation_kind == "observed_result":
            fact: ObservedResultFact | ObservedFailureFact = ObservedResultFact.model_validate(
                {
                    **common,
                    "conclusion": "observed_result",
                    "result_ref": observation_ref,
                    "result_hash": observation_hash,
                },
                strict=True,
            )
        else:
            fact = ObservedFailureFact.model_validate(
                {
                    **common,
                    "conclusion": "observed_failure",
                    "failure_ref": observation_ref,
                    "failure_hash": observation_hash,
                },
                strict=True,
            )
        connection.execute(_EVENT_INSERT, _transition_event_parameters(fact, phase=observation_kind))
        _transition_checkpoint("after_event_insert")
        cursor = connection.execute(
            """
            UPDATE source_history_heads
            SET phase = ?,
                head_generation = ?,
                head_journal_revision = ?,
                observation_generation = ?,
                observation_journal_revision = ?,
                observation_ref = ?,
                observation_hash = ?
            WHERE run_id = ? AND operation_id = ?
              AND dispatch_authorization_ordinal = 1
              AND head_journal_revision = ? AND phase = 'dispatch_intent'
            """,
            (
                observation_kind,
                state.generation,
                revision,
                state.generation,
                revision,
                observation_ref,
                observation_hash,
                run_id,
                operation_id,
                expected_head_journal_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise CommandJournalConflict(CommandJournalConflictReason.HEAD_CAS_FAILED)
        _transition_checkpoint("after_head_cas")
    _transition_commit_acknowledged()
    return revision


@contextmanager
def _write_transaction(path: Path):
    connection = _open_write_connection(path)
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            verify_schema(connection)
            load_validated_history_facts(connection)
        except HistorySQLiteUnavailable as exc:
            _rollback(connection)
            raise _validation_error(exc) from None
        except sqlite3.Error as exc:
            _rollback(connection)
            raise _sqlite_error(exc) from None
        try:
            yield connection
            connection.commit()
        except CommandJournalConflict:
            _rollback(connection)
            raise
        except CommandJournalError:
            _rollback(connection)
            raise
        except HistorySQLiteUnavailable as exc:
            _rollback(connection)
            raise _validation_error(exc) from None
        except sqlite3.Error as exc:
            _rollback(connection)
            raise _sqlite_error(exc) from None
    finally:
        connection.close()


def _open_write_connection(path: Path) -> sqlite3.Connection:
    _probe_existing_for_write(path)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_MILLISECONDS / 1_000)
        connection.row_factory = sqlite3.Row
        _configure_connection(connection, creating=False)
        return connection
    except HistorySQLiteUnavailable as exc:
        if connection is not None:
            connection.close()
        raise _validation_error(exc) from None
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise _sqlite_error(exc) from None


def _probe_existing_for_write(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise CommandJournalError(CommandJournalErrorReason.CANNOT_OPEN) from None
    except OSError as exc:
        raise _path_error(exc) from None
    if not stat.S_ISREG(metadata.st_mode):
        raise CommandJournalError(CommandJournalErrorReason.CANNOT_OPEN)
    if metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0:
        raise CommandJournalError(CommandJournalErrorReason.READONLY)
    try:
        probe_existing_database(path)
    except HistorySQLiteUnavailable as exc:
        raise _validation_error(exc) from None


def _configure_connection(connection: sqlite3.Connection, *, creating: bool) -> None:
    if creating:
        mode = scalar_text(connection, "PRAGMA journal_mode=DELETE")
    else:
        mode = scalar_text(connection, "PRAGMA journal_mode")
    if mode.lower() != "delete":
        raise CommandJournalError(CommandJournalErrorReason.PRAGMA_MISMATCH)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MILLISECONDS}")
    verify_connection_pragmas(connection)
    if scalar_integer(connection, "PRAGMA busy_timeout") != BUSY_TIMEOUT_MILLISECONDS:
        raise CommandJournalError(CommandJournalErrorReason.PRAGMA_MISMATCH)


def _require_session_generation(connection: sqlite3.Connection, state: _SessionState) -> None:
    row = connection.execute(
        """
        SELECT sidecar_instance_id, retained, complete
        FROM source_history_generations
        WHERE generation = ?
        """,
        (state.generation,),
    ).fetchone()
    if (
        row is None
        or row["sidecar_instance_id"] != state.instance_id
        or tuple(row[1:]) != (1, 1)
    ):
        raise CommandJournalConflict(CommandJournalConflictReason.SESSION_GENERATION_INVALID)


def _find_operation_head(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    operation_id: str,
    ordinal: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT * FROM source_history_heads
        WHERE run_id = ? AND operation_id = ? AND dispatch_authorization_ordinal = ?
        """,
        (run_id, operation_id, ordinal),
    ).fetchone()


def _require_head(connection: sqlite3.Connection, *, run_id: str, operation_id: str) -> sqlite3.Row:
    row = _find_operation_head(connection, run_id=run_id, operation_id=operation_id, ordinal=1)
    if row is None:
        raise CommandJournalConflict(CommandJournalConflictReason.HEAD_MISSING)
    return row


def _require_no_identity_collision(connection: sqlite3.Connection, accepted: AcceptedCommand) -> None:
    row = connection.execute(
        """
        SELECT 1 FROM source_history_heads
        WHERE (
            run_id = ? AND (operation_id = ? OR idempotency_key = ?)
        ) OR (
            operation_id = ? AND idempotency_key = ?
        )
        LIMIT 1
        """,
        (
            accepted.run_id,
            accepted.operation_id,
            accepted.idempotency_key,
            accepted.operation_id,
            accepted.idempotency_key,
        ),
    ).fetchone()
    if row is not None:
        raise CommandJournalConflict(CommandJournalConflictReason.IDENTITY_CONFLICT)


def _same_accepted_identity(row: sqlite3.Row, accepted: AcceptedCommand) -> bool:
    return all(row[name] == value for name, value in _accepted_fact_values(accepted).items())


def _allocate_revision(connection: sqlite3.Connection) -> int:
    last_revision = scalar_integer(
        connection,
        "SELECT last_journal_revision FROM source_history_state WHERE singleton = 1",
    )
    if last_revision < 0 or last_revision >= SQLITE_MAX_INTEGER:
        raise CommandJournalConflict(CommandJournalConflictReason.REVISION_EXHAUSTED)
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
        raise CommandJournalConflict(CommandJournalConflictReason.HEAD_CAS_FAILED)
    return revision


def _validate_accepted_input(accepted: AcceptedCommand, *, generation: int) -> None:
    AcceptedNoDispatchFact.model_validate(
        {
            **_accepted_fact_values(accepted),
            "conclusion": "accepted_no_dispatch",
            "accepted_generation": generation,
            "accepted_journal_revision": 1,
            "head_generation": generation,
            "head_journal_revision": 1,
        },
        strict=True,
    )


def _accepted_fact_values(accepted: AcceptedCommand) -> dict[str, object]:
    return {
        "run_id": accepted.run_id,
        "operation_id": accepted.operation_id,
        "source": accepted.source,
        "operation_kind": accepted.operation_kind,
        "idempotency_key": accepted.idempotency_key,
        "request_hash": accepted.request_hash,
        "attempt_no": accepted.attempt_no,
        "accepted_requirement_revision_id": accepted.accepted_requirement_revision_id,
        "runtime_attempt_fence_ref": accepted.runtime_attempt_fence_ref,
        "dispatch_authorization_ordinal": accepted.dispatch_authorization_ordinal,
        "authorized_dispatch_intent_id": accepted.authorized_dispatch_intent_id,
        "authorized_dispatch_intent_revision": accepted.authorized_dispatch_intent_revision,
        "authorized_dispatch_intent_digest": accepted.authorized_dispatch_intent_digest,
        "profile_binding_generation": accepted.profile_binding_generation,
        "browser_control_scope_id": accepted.browser_control_scope_id,
        "controller_fence_ref": accepted.controller_fence_ref,
    }


def _accepted_values_from_row(row: sqlite3.Row) -> dict[str, object]:
    names = (
        "run_id",
        "operation_id",
        "source",
        "operation_kind",
        "idempotency_key",
        "request_hash",
        "attempt_no",
        "accepted_requirement_revision_id",
        "runtime_attempt_fence_ref",
        "accepted_generation",
        "accepted_journal_revision",
        "dispatch_authorization_ordinal",
        "authorized_dispatch_intent_id",
        "authorized_dispatch_intent_revision",
        "authorized_dispatch_intent_digest",
        "profile_binding_generation",
        "browser_control_scope_id",
        "controller_fence_ref",
    )
    return {name: row[name] for name in names}


def _accepted_event_parameters(fact: AcceptedNoDispatchFact) -> tuple[object, ...]:
    values = fact.model_dump()
    return (
        fact.accepted_journal_revision,
        fact.accepted_generation,
        "accepted",
        *(_identity_database_values(values)),
        fact.accepted_requirement_revision_id,
        fact.runtime_attempt_fence_ref,
        fact.accepted_generation,
        fact.accepted_journal_revision,
        fact.authorized_dispatch_intent_id,
        fact.authorized_dispatch_intent_revision,
        fact.authorized_dispatch_intent_digest,
        fact.profile_binding_generation,
        fact.browser_control_scope_id,
        fact.controller_fence_ref,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _accepted_head_parameters(fact: AcceptedNoDispatchFact) -> tuple[object, ...]:
    values = fact.model_dump()
    return (
        *(_identity_database_values(values)),
        fact.accepted_requirement_revision_id,
        fact.runtime_attempt_fence_ref,
        fact.accepted_generation,
        fact.accepted_journal_revision,
        fact.authorized_dispatch_intent_id,
        fact.authorized_dispatch_intent_revision,
        fact.authorized_dispatch_intent_digest,
        fact.profile_binding_generation,
        fact.browser_control_scope_id,
        fact.controller_fence_ref,
        "accepted",
        fact.head_generation,
        fact.head_journal_revision,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _transition_event_parameters(
    fact: DispatchNotObservedFact | ObservedResultFact | ObservedFailureFact,
    *,
    phase: Literal["dispatch_intent", "observed_result", "observed_failure"],
) -> tuple[object, ...]:
    values = fact.model_dump()
    observation_ref = values.get("result_ref") or values.get("failure_ref")
    observation_hash = values.get("result_hash") or values.get("failure_hash")
    return (
        fact.head_journal_revision,
        fact.head_generation,
        phase,
        *(_identity_database_values(values)),
        fact.accepted_requirement_revision_id,
        fact.runtime_attempt_fence_ref,
        fact.accepted_generation,
        fact.accepted_journal_revision,
        fact.authorized_dispatch_intent_id,
        fact.authorized_dispatch_intent_revision,
        fact.authorized_dispatch_intent_digest,
        fact.profile_binding_generation,
        fact.browser_control_scope_id,
        fact.controller_fence_ref,
        fact.durable_dispatch_intent_ref,
        fact.dispatch_intent_generation,
        fact.dispatch_intent_journal_revision,
        values.get("observation_generation"),
        values.get("observation_journal_revision"),
        observation_ref,
        observation_hash,
    )


def _identity_database_values(values: dict[str, object]) -> tuple[object, ...]:
    return (
        values["run_id"],
        values["operation_id"],
        values["source"],
        values["operation_kind"],
        values["idempotency_key"],
        values["request_hash"],
        values["attempt_no"],
        values["dispatch_authorization_ordinal"],
    )


def _require_positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= SQLITE_MAX_INTEGER:
        raise ValueError(f"command_journal_invalid_{name}")


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        try:
            connection.rollback()
        except sqlite3.Error as exc:
            raise _sqlite_error(exc) from None


def _validation_error(error: HistorySQLiteUnavailable) -> CommandJournalError:
    reasons = {
        "busy": CommandJournalErrorReason.BUSY,
        "corrupt": CommandJournalErrorReason.CORRUPT,
        "pragma_mismatch": CommandJournalErrorReason.PRAGMA_MISMATCH,
        "schema_mismatch": CommandJournalErrorReason.SCHEMA_MISMATCH,
        "unreadable": CommandJournalErrorReason.CANNOT_OPEN,
    }
    return CommandJournalError(reasons[error.reason])


def _path_error(error: OSError) -> CommandJournalError:
    if isinstance(error, FileNotFoundError):
        return CommandJournalError(CommandJournalErrorReason.CANNOT_OPEN)
    if isinstance(error, PermissionError):
        return CommandJournalError(CommandJournalErrorReason.READONLY)
    return CommandJournalError(CommandJournalErrorReason.IO_ERROR)


def _sqlite_error(error: sqlite3.Error) -> CommandJournalError:
    code = getattr(error, "sqlite_errorcode", None)
    primary = code & 0xFF if isinstance(code, int) else None
    if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        reason = CommandJournalErrorReason.BUSY
    elif primary == sqlite3.SQLITE_FULL:
        reason = CommandJournalErrorReason.FULL
    elif primary == sqlite3.SQLITE_READONLY:
        reason = CommandJournalErrorReason.READONLY
    elif primary == sqlite3.SQLITE_IOERR:
        reason = CommandJournalErrorReason.IO_ERROR
    elif primary == sqlite3.SQLITE_CANTOPEN:
        reason = CommandJournalErrorReason.CANNOT_OPEN
    elif primary in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
        reason = CommandJournalErrorReason.CORRUPT
    else:
        message = str(error).lower()
        if "locked" in message or "busy" in message:
            reason = CommandJournalErrorReason.BUSY
        elif "readonly" in message:
            reason = CommandJournalErrorReason.READONLY
        elif "full" in message:
            reason = CommandJournalErrorReason.FULL
        elif "i/o" in message:
            reason = CommandJournalErrorReason.IO_ERROR
        elif "unable to open" in message:
            reason = CommandJournalErrorReason.CANNOT_OPEN
        else:
            reason = CommandJournalErrorReason.CORRUPT
    return CommandJournalError(reason)


def _startup_checkpoint(_: str) -> None:
    return None


def _startup_commit_acknowledged() -> None:
    return None


def _transition_checkpoint(_: str) -> None:
    return None


def _transition_commit_acknowledged() -> None:
    return None


_IDENTITY_COLUMNS = """
    run_id, operation_id, source, operation_kind, idempotency_key, request_hash,
    attempt_no, dispatch_authorization_ordinal
"""

_EVENT_INSERT = f"""
INSERT INTO source_history_events(
    journal_revision, event_generation, phase, {_IDENTITY_COLUMNS},
    accepted_requirement_revision_id, runtime_attempt_fence_ref,
    accepted_generation, accepted_journal_revision,
    authorized_dispatch_intent_id, authorized_dispatch_intent_revision,
    authorized_dispatch_intent_digest, profile_binding_generation,
    browser_control_scope_id, controller_fence_ref,
    durable_dispatch_intent_ref, dispatch_intent_generation,
    dispatch_intent_journal_revision, observation_generation,
    observation_journal_revision, observation_ref, observation_hash
) VALUES ({", ".join("?" for _ in range(28))})
"""

_HEAD_INSERT = f"""
INSERT INTO source_history_heads(
    {_IDENTITY_COLUMNS},
    accepted_requirement_revision_id, runtime_attempt_fence_ref,
    accepted_generation, accepted_journal_revision,
    authorized_dispatch_intent_id, authorized_dispatch_intent_revision,
    authorized_dispatch_intent_digest, profile_binding_generation,
    browser_control_scope_id, controller_fence_ref,
    phase, head_generation, head_journal_revision,
    durable_dispatch_intent_ref, dispatch_intent_generation,
    dispatch_intent_journal_revision, observation_generation,
    observation_journal_revision, observation_ref, observation_hash
) VALUES ({", ".join("?" for _ in range(28))})
"""
