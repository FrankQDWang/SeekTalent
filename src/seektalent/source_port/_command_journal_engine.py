"""Private SQLite storage engine for the production-unreachable command journal."""

from __future__ import annotations

from contextlib import contextmanager, suppress
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import tempfile
from typing import Literal, Never

from seektalent.source_port._command_journal_types import (
    AcceptedCommand,
    CommandJournalConflict,
    CommandJournalConflictReason,
    CommandJournalError,
    CommandJournalErrorReason,
)
from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ObservedFailureFact,
    ObservedResultFact,
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


BUSY_TIMEOUT_MILLISECONDS = 1_000


def _create_database(path: Path) -> None:
    """Atomically publish one fully initialized journal database at an explicit path."""
    _require_explicit_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _path_error(exc) from None

    temporary_path = _create_temporary_database_path(path)
    published = False
    try:
        _initialize_database(temporary_path)
        _sync_initialized_database(temporary_path)
        _publish_initialized_database(temporary_path, path)
        published = True
    finally:
        if not published:
            _cleanup_owned_temporary_database(temporary_path)


def _validate_existing_database(path: Path) -> None:
    """Validate one existing journal database without repairing it."""
    _require_explicit_path(path)
    _validate_existing_journal(path)


def _require_explicit_path(path: Path) -> None:
    if not isinstance(path, Path):
        raise TypeError("command journal path must be a Path")
    if not path.is_absolute():
        raise ValueError("command journal path must be absolute")


def _create_temporary_database_path(path: Path) -> Path:
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".creating",
            dir=path.parent,
        )
    except OSError as exc:
        raise _path_error(exc) from None
    temporary_path = Path(temporary_name)
    try:
        os.close(descriptor)
    except OSError as exc:
        _cleanup_owned_temporary_database(temporary_path)
        raise _path_error(exc) from None
    return temporary_path


def _sync_initialized_database(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError as exc:
        raise _path_error(exc) from None
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _publish_initialized_database(temporary_path: Path, path: Path) -> None:
    temporary_identity = _owned_temporary_file_identity(temporary_path)
    try:
        os.link(temporary_path, path)
    except FileExistsError:
        raise CommandJournalConflict(CommandJournalConflictReason.CREATE_PATH_EXISTS) from None
    except OSError as exc:
        raise _path_error(exc) from None
    try:
        _sync_published_directory(path.parent)
    except CommandJournalError:
        _rollback_uncommitted_publication(path, temporary_identity)
        raise
    with suppress(OSError):
        temporary_path.unlink()


def _owned_temporary_file_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _path_error(exc) from None
    if not stat.S_ISREG(metadata.st_mode):
        raise CommandJournalError(CommandJournalErrorReason.CORRUPT)
    return metadata.st_dev, metadata.st_ino


def _rollback_uncommitted_publication(path: Path, temporary_identity: tuple[int, int]) -> None:
    try:
        target_metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if (target_metadata.st_dev, target_metadata.st_ino) != temporary_identity:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _sync_published_directory(path: Path) -> None:
    if os.name == "nt":
        _sync_windows_directory(path)
        return

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError as exc:
        raise _path_error(exc) from None
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _sync_windows_directory(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_flag_backup_semantics = 0x02000000

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        str(path),
        generic_write,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_flag_backup_semantics,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        _raise_windows_directory_error(path, operation="CreateFileW")
    try:
        if not kernel32.FlushFileBuffers(handle):
            _raise_windows_directory_error(path, operation="FlushFileBuffers")
    finally:
        kernel32.CloseHandle(handle)


def _raise_windows_directory_error(path: Path, *, operation: str) -> Never:
    import ctypes

    error_code = getattr(ctypes, "get_last_error")()
    if error_code in {2, 3}:
        raise CommandJournalError(CommandJournalErrorReason.CANNOT_OPEN)
    if error_code == 5:
        raise CommandJournalError(CommandJournalErrorReason.READONLY)
    raise CommandJournalError(CommandJournalErrorReason.IO_ERROR) from OSError(error_code, operation, path)


def _cleanup_owned_temporary_database(path: Path) -> None:
    with suppress(OSError):
        path.unlink()


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
    with _write_transaction(path, validate_full=True) as connection:
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


def _record_accepted(
    *,
    path: Path,
    generation: int,
    instance_id: str,
    accepted: AcceptedCommand,
) -> int:
    if type(accepted) is not AcceptedCommand:
        raise TypeError("accepted command must be an AcceptedCommand")
    _validate_accepted_input(accepted, generation=generation)
    with _write_transaction(path) as connection:
        _require_session_generation(connection, generation=generation, instance_id=instance_id)
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
            if int(existing["accepted_generation"]) != generation:
                raise CommandJournalConflict(CommandJournalConflictReason.ACCEPTANCE_REPLAY_CONFLICT)
            return int(existing["accepted_journal_revision"])

        _require_no_identity_collision(connection, accepted)
        revision = _allocate_revision(connection)
        fact = AcceptedNoDispatchFact.model_validate(
            {
                **_accepted_fact_values(accepted),
                "conclusion": "accepted_no_dispatch",
                "accepted_generation": generation,
                "accepted_journal_revision": revision,
                "head_generation": generation,
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
    *,
    path: Path,
    generation: int,
    instance_id: str,
    run_id: str,
    operation_id: str,
    expected_head_journal_revision: int,
    durable_dispatch_intent_ref: str,
) -> int:
    _require_positive_integer(expected_head_journal_revision, "expected_head_journal_revision")
    with _write_transaction(path) as connection:
        _require_session_generation(connection, generation=generation, instance_id=instance_id)
        head = _require_head(connection, run_id=run_id, operation_id=operation_id)
        phase = str(head["phase"])
        if phase == "dispatch_intent":
            if (
                int(head["accepted_journal_revision"]) != expected_head_journal_revision
                or int(head["dispatch_intent_generation"]) != generation
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
                "head_generation": generation,
                "head_journal_revision": revision,
                "durable_dispatch_intent_ref": durable_dispatch_intent_ref,
                "dispatch_intent_generation": generation,
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
                generation,
                revision,
                durable_dispatch_intent_ref,
                generation,
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
    *,
    path: Path,
    generation: int,
    instance_id: str,
    run_id: str,
    operation_id: str,
    expected_head_journal_revision: int,
    observation_kind: Literal["observed_result", "observed_failure"],
    observation_ref: str,
    observation_hash: str,
) -> int:
    _require_positive_integer(expected_head_journal_revision, "expected_head_journal_revision")
    with _write_transaction(path) as connection:
        _require_session_generation(connection, generation=generation, instance_id=instance_id)
        head = _require_head(connection, run_id=run_id, operation_id=operation_id)
        phase = str(head["phase"])
        if phase in {"observed_result", "observed_failure"}:
            if phase != observation_kind:
                raise CommandJournalConflict(CommandJournalConflictReason.PHASE_ROLLBACK)
            if (
                int(head["dispatch_intent_journal_revision"]) != expected_head_journal_revision
                or int(head["observation_generation"]) != generation
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
            "head_generation": generation,
            "head_journal_revision": revision,
            "durable_dispatch_intent_ref": head["durable_dispatch_intent_ref"],
            "dispatch_intent_generation": int(head["dispatch_intent_generation"]),
            "dispatch_intent_journal_revision": int(head["dispatch_intent_journal_revision"]),
            "observation_generation": generation,
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
                generation,
                revision,
                generation,
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
def _write_transaction(path: Path, *, validate_full: bool = False):
    try:
        connection = _open_write_connection(path)
    except CommandJournalError:
        raise
    except sqlite3.Error as exc:
        raise _sqlite_error(exc) from None
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
            if validate_full:
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
    except CommandJournalError:
        if connection is not None:
            connection.close()
        raise


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


def _require_session_generation(
    connection: sqlite3.Connection,
    *,
    generation: int,
    instance_id: str,
) -> None:
    row = connection.execute(
        """
        SELECT sidecar_instance_id, retained, complete
        FROM source_history_generations
        WHERE generation = ?
        """,
        (generation,),
    ).fetchone()
    if (
        row is None
        or row["sidecar_instance_id"] != instance_id
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
    lookups = (
        (
            """
            SELECT 1 FROM source_history_heads
            WHERE run_id = ? AND operation_id = ? AND dispatch_authorization_ordinal = 1
            """,
            (accepted.run_id, accepted.operation_id),
        ),
        (
            """
            SELECT 1 FROM source_history_heads
            WHERE run_id = ? AND idempotency_key = ?
            """,
            (accepted.run_id, accepted.idempotency_key),
        ),
        (
            """
            SELECT 1 FROM source_history_heads
            WHERE operation_id = ? AND idempotency_key = ?
            """,
            (accepted.operation_id, accepted.idempotency_key),
        ),
    )
    for statement, parameters in lookups:
        if connection.execute(statement, parameters).fetchone() is not None:
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
