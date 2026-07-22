from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ast
import copy
from dataclasses import replace
import os
from pathlib import Path
import pickle
import sqlite3
import threading
from unittest.mock import patch

import pytest

import seektalent.source_port._command_journal_engine as journal_engine
import seektalent.source_port.command_journal as command_journal
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    CommandJournalConflict,
    CommandJournalConflictReason,
    CommandJournalError,
    CommandJournalErrorReason,
    CommandJournalSession,
    create_command_journal,
    open_command_journal,
)
from seektalent.source_port.history_contract import (
    ExactAuthorizationSelector,
    SQLITE_MAX_INTEGER,
    SourceHistoryMatched,
    SourceHistoryQueryV1,
)
from seektalent.source_port.history_sqlite_reader import (
    SCHEMA_VERSION,
    HistorySQLiteUnavailable,
    SourceHistorySQLiteReader,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


class _CommitFailingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        object.__setattr__(self, "_connection", connection)

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(self._connection, name, value)

    def commit(self) -> None:
        raise sqlite3.OperationalError("simulated I/O error before publish")


def _accepted(
    operation_id: str = "operation-1",
    *,
    run_id: str = "run-1",
    idempotency_key: str | None = None,
    request_hash: str = HASH_A,
) -> AcceptedCommand:
    return AcceptedCommand(
        run_id=run_id,
        operation_id=operation_id,
        source="liepin",
        operation_kind="search",
        idempotency_key=idempotency_key or f"key-{operation_id}",
        request_hash=request_hash,
        attempt_no=1,
        accepted_requirement_revision_id="requirement-1",
        runtime_attempt_fence_ref=HASH_B,
        authorized_dispatch_intent_id=f"intent-{operation_id}",
        authorized_dispatch_intent_revision=1,
        authorized_dispatch_intent_digest=HASH_C,
        profile_binding_generation=1,
        browser_control_scope_id="browser-scope-1",
        controller_fence_ref=HASH_D,
    )


def _query(
    operation_id: str,
    *,
    first_generation: int,
    last_generation: int,
) -> SourceHistoryQueryV1:
    return SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id="run-1",
        operation_id=operation_id,
        source="liepin",
        operation_kind="search",
        idempotency_key=f"key-{operation_id}",
        request_hash=HASH_A,
        attempt_no=1,
        authorization_selector=ExactAuthorizationSelector(kind="exact", ordinal=1),
        searched_first_generation=first_generation,
        searched_last_generation=last_generation,
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
    )


def _event_count(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM source_history_events").fetchone()[0])
    finally:
        connection.close()


def test_create_open_and_start_use_explicit_paths_and_persist_fresh_generation_identity(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="Path"):
        create_command_journal(str(tmp_path / "journal.sqlite3"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="absolute"):
        create_command_journal(Path("journal.sqlite3"))

    path = tmp_path / "journal.sqlite3"
    journal = create_command_journal(path)
    first = journal.start()
    second = open_command_journal(path).start()

    assert (first.generation, second.generation) == (1, 2)
    assert first.instance_id != second.instance_id
    assert len(first.instance_id) == len(second.instance_id) == 64
    assert set(first.instance_id + second.instance_id) <= set("0123456789abcdef")

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT generation, sidecar_instance_id, retained, complete "
            "FROM source_history_generations ORDER BY generation"
        ).fetchall() == [(1, first.instance_id, 1, 1), (2, second.instance_id, 1, 1)]
        assert connection.execute(
            "SELECT last_sidecar_generation FROM source_history_state WHERE singleton = 1"
        ).fetchone() == (2,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("stage", "expected_reason"),
    (
        ("before_ddl", CommandJournalErrorReason.IO_ERROR),
        ("ddl_midway", CommandJournalErrorReason.CORRUPT),
        ("before_publish_commit", CommandJournalErrorReason.IO_ERROR),
        ("verification", CommandJournalErrorReason.SCHEMA_MISMATCH),
    ),
)
def test_failed_create_never_publishes_or_leaks_an_owned_database_and_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    expected_reason: CommandJournalErrorReason,
) -> None:
    path = tmp_path / "journal.sqlite3"

    with monkeypatch.context() as patched:
        if stage == "before_ddl":
            patched.setattr(
                journal_engine,
                "_configure_connection",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    CommandJournalError(CommandJournalErrorReason.IO_ERROR)
                ),
            )
        elif stage == "ddl_midway":
            patched.setattr(journal_engine, "SCHEMA_STATEMENTS", (*journal_engine.SCHEMA_STATEMENTS[:2], "not sql"))
        elif stage == "before_publish_commit":
            real_connect = sqlite3.connect
            patched.setattr(
                journal_engine.sqlite3,
                "connect",
                lambda *args, **kwargs: _CommitFailingConnection(real_connect(*args, **kwargs)),
            )
        else:
            patched.setattr(
                journal_engine,
                "verify_schema",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(HistorySQLiteUnavailable("schema_mismatch")),
            )

        with pytest.raises(CommandJournalError) as failure:
            create_command_journal(path)

    assert failure.value.reason is expected_reason
    assert not path.exists()
    assert tuple(tmp_path.iterdir()) == ()

    create_command_journal(path).close()


def test_create_never_clobbers_a_preexisting_target_when_publication_conflicts(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    original = b"rival-owned-bytes"
    path.write_bytes(original)

    with pytest.raises(CommandJournalConflict) as conflict:
        create_command_journal(path)

    assert conflict.value.reason is CommandJournalConflictReason.CREATE_PATH_EXISTS
    assert path.read_bytes() == original


def test_create_publish_race_preserves_the_other_creator_bytes_and_cleans_its_temporary_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    winner_bytes = b"other-creator-won-the-race"

    def lose_publish_race(_source: object, _target: object) -> None:
        path.write_bytes(winner_bytes)
        raise FileExistsError

    with patch.object(journal_engine.os, "link", side_effect=lose_publish_race):
        with pytest.raises(CommandJournalConflict) as conflict:
            create_command_journal(path)

    assert conflict.value.reason is CommandJournalConflictReason.CREATE_PATH_EXISTS
    assert path.read_bytes() == winner_bytes
    assert tuple(tmp_path.iterdir()) == (path,)


def test_create_returns_a_live_journal_when_post_publish_temporary_cleanup_ack_is_lost(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    original_unlink = Path.unlink
    temporary_unlink_calls = 0

    def lose_temporary_cleanup(candidate: Path, missing_ok: bool = False) -> None:
        nonlocal temporary_unlink_calls
        if candidate.name.endswith(".creating"):
            temporary_unlink_calls += 1
            raise OSError("simulated temporary cleanup acknowledgement loss")
        original_unlink(candidate, missing_ok=missing_ok)

    with patch.object(Path, "unlink", new=lose_temporary_cleanup):
        journal = create_command_journal(path)

    session = journal.start()
    assert session.record_accepted(_accepted()) == 1
    assert temporary_unlink_calls == 1
    assert path.exists()

    reopened = open_command_journal(path)
    assert reopened.start().generation == 2

    for temporary_path in tmp_path.glob(".journal.sqlite3.*.creating"):
        temporary_path.unlink()


def test_create_rolls_back_the_uncommitted_target_when_directory_persistence_fails(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"

    with patch.object(
        journal_engine,
        "_sync_published_directory",
        side_effect=CommandJournalError(CommandJournalErrorReason.IO_ERROR),
    ):
        with pytest.raises(CommandJournalError) as failure:
            create_command_journal(path)

    assert failure.value.reason is CommandJournalErrorReason.IO_ERROR
    assert not path.exists()
    assert tuple(tmp_path.iterdir()) == ()

    create_command_journal(path).close()


def test_create_directory_persistence_rollback_never_deletes_a_racing_replacement(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    replacement = b"other-creator-replaced-the-uncommitted-link"

    def replace_target_then_fail(_parent: Path) -> None:
        path.unlink()
        path.write_bytes(replacement)
        raise CommandJournalError(CommandJournalErrorReason.IO_ERROR)

    with patch.object(journal_engine, "_sync_published_directory", side_effect=replace_target_then_fail):
        with pytest.raises(CommandJournalError) as failure:
            create_command_journal(path)

    assert failure.value.reason is CommandJournalErrorReason.IO_ERROR
    assert path.read_bytes() == replacement
    assert tuple(tmp_path.iterdir()) == (path,)


def test_create_calls_parent_directory_persistence_after_no_clobber_publication(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    calls: list[str] = []
    real_link = os.link

    def record_link(*args: object, **kwargs: object) -> None:
        real_link(*args, **kwargs)  # type: ignore[arg-type]
        calls.append("link")

    def record_directory_persistence(_parent: Path) -> None:
        calls.append("directory-sync")

    with (
        patch.object(journal_engine.os, "link", new=record_link),
        patch.object(journal_engine, "_sync_published_directory", side_effect=record_directory_persistence),
    ):
        create_command_journal(path)

    assert calls == ["link", "directory-sync"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync proof")
def test_posix_directory_persistence_opens_and_fsyncs_the_parent_directory(tmp_path: Path) -> None:
    directory = tmp_path / "journal-parent"
    directory.mkdir()
    opened: list[tuple[object, int]] = []
    synced: list[int] = []
    real_open = os.open
    real_fsync = os.fsync

    def record_open(candidate: object, flags: int, *args: object, **kwargs: object) -> int:
        descriptor = real_open(candidate, flags, *args, **kwargs)  # type: ignore[arg-type]
        opened.append((candidate, flags))
        return descriptor

    def record_fsync(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    with (
        patch.object(journal_engine.os, "open", new=record_open),
        patch.object(journal_engine.os, "fsync", new=record_fsync),
    ):
        journal_engine._sync_published_directory(directory)

    assert opened == [(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))]
    assert len(synced) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows native directory flush proof")
def test_windows_directory_persistence_executes_the_native_flush_path(tmp_path: Path) -> None:
    directory = tmp_path / "journal-parent"
    directory.mkdir()

    journal_engine._sync_published_directory(directory)


def test_transition_hot_path_has_a_fixed_reader_audit_budget_after_history_grows(tmp_path: Path) -> None:
    session = create_command_journal(tmp_path / "journal.sqlite3").start()

    with (
        patch.object(journal_engine, "verify_schema", wraps=journal_engine.verify_schema) as verify_schema,
        patch.object(
            journal_engine,
            "load_validated_history_facts",
            wraps=journal_engine.load_validated_history_facts,
        ) as load_facts,
    ):
        for index in range(400):
            session.record_accepted(_accepted(f"operation-{index}"))

    assert verify_schema.call_count == 0
    assert load_facts.call_count == 0


@pytest.mark.parametrize(
    ("sqlite_errorcode", "expected_reason"),
    (
        (sqlite3.SQLITE_FULL, CommandJournalErrorReason.FULL),
        (sqlite3.SQLITE_IOERR, CommandJournalErrorReason.IO_ERROR),
    ),
)
def test_sqlite_error_mapping_keeps_full_and_io_reasons_stable(
    sqlite_errorcode: int,
    expected_reason: CommandJournalErrorReason,
) -> None:
    error = sqlite3.OperationalError("injected sqlite failure")
    error.sqlite_errorcode = sqlite_errorcode

    assert journal_engine._sqlite_error(error).reason is expected_reason


@pytest.mark.parametrize(
    ("sqlite_errorcode", "expected_reason"),
    (
        (sqlite3.SQLITE_FULL, CommandJournalErrorReason.FULL),
        (sqlite3.SQLITE_IOERR, CommandJournalErrorReason.IO_ERROR),
    ),
)
def test_public_transition_never_leaks_raw_sqlite_errors(
    tmp_path: Path,
    sqlite_errorcode: int,
    expected_reason: CommandJournalErrorReason,
) -> None:
    session = create_command_journal(tmp_path / "journal.sqlite3").start()
    error = sqlite3.OperationalError("injected sqlite failure")
    error.sqlite_errorcode = sqlite_errorcode

    with patch.object(journal_engine, "_open_write_connection", side_effect=error):
        with pytest.raises(CommandJournalError) as observed:
            session.record_accepted(_accepted())

    assert observed.value.reason is expected_reason


def test_open_missing_path_and_busy_transition_use_stable_public_reasons(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(CommandJournalError) as missing_error:
        open_command_journal(missing)
    assert missing_error.value.reason is CommandJournalErrorReason.CANNOT_OPEN

    path = tmp_path / "journal.sqlite3"
    session = create_command_journal(path).start()
    lock = sqlite3.connect(path, isolation_level=None)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        with pytest.raises(CommandJournalError) as busy_error:
            session.record_accepted(_accepted())
        assert busy_error.value.reason is CommandJournalErrorReason.BUSY
    finally:
        if lock.in_transaction:
            lock.rollback()
        lock.close()


def test_concurrent_startup_allocates_one_contiguous_generation_per_live_session(tmp_path: Path) -> None:
    journal = create_command_journal(tmp_path / "journal.sqlite3")
    workers = 12
    barrier = threading.Barrier(workers)

    def start() -> CommandJournalSession:
        barrier.wait()
        return journal.start()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        sessions = tuple(executor.map(lambda _: start(), range(workers)))

    assert sorted(session.generation for session in sessions) == list(range(1, workers + 1))
    assert len({session.instance_id for session in sessions}) == workers


def test_committed_startup_ack_loss_does_not_reallocate_the_generation_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = create_command_journal(path)

    with patch.object(
        journal_engine,
        "_startup_commit_acknowledged",
        side_effect=RuntimeError("startup acknowledgement lost"),
    ):
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            journal.start()

    restarted = open_command_journal(path).start()
    assert restarted.generation == 2
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT generation FROM source_history_generations ORDER BY generation").fetchall() == [
            (1,),
            (2,),
        ]
    finally:
        connection.close()


def test_generation_overflow_is_a_typed_conflict_without_a_partial_registry_write(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = create_command_journal(path)
    instance_id = f"{SQLITE_MAX_INTEGER:064x}"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO source_history_generations(generation, sidecar_instance_id, retained, complete)
            VALUES (?, ?, 1, 1)
            """,
            (SQLITE_MAX_INTEGER, instance_id),
        )
        connection.execute(
            "UPDATE source_history_state SET last_sidecar_generation = ? WHERE singleton = 1",
            (SQLITE_MAX_INTEGER,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CommandJournalConflict) as overflow:
        journal.start()
    assert overflow.value.reason is CommandJournalConflictReason.GENERATION_EXHAUSTED
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_history_generations").fetchone() == (1,)
        assert connection.execute(
            "SELECT last_sidecar_generation FROM source_history_state WHERE singleton = 1"
        ).fetchone() == (SQLITE_MAX_INTEGER,)
    finally:
        connection.close()


def test_live_session_is_factory_only_noncopyable_and_closed_session_has_no_write_authority(tmp_path: Path) -> None:
    session = create_command_journal(tmp_path / "journal.sqlite3").start()

    with pytest.raises(TypeError, match="factory-only"):
        CommandJournalSession()
    forged = object.__new__(CommandJournalSession)
    with pytest.raises(TypeError, match="live factory"):
        forged.record_accepted(_accepted())
    with pytest.raises(TypeError, match="copied"):
        copy.copy(session)
    with pytest.raises(TypeError, match="copied"):
        copy.deepcopy(session)
    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(session)
    with pytest.raises(TypeError):
        replace(session)

    session.close()
    with pytest.raises(TypeError, match="live factory"):
        session.record_accepted(_accepted())
    assert _event_count(tmp_path / "journal.sqlite3") == 0


def test_current_phase_replay_is_exact_and_rollbacks_identity_conflicts_and_stale_cas_do_not_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    session = create_command_journal(path).start()
    accepted = _accepted()
    accepted_revision = session.record_accepted(accepted)
    assert session.record_accepted(accepted) == accepted_revision

    with pytest.raises(CommandJournalConflict) as stale:
        session.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision + 1,
            durable_dispatch_intent_ref="dispatch-ref",
        )
    assert stale.value.reason is CommandJournalConflictReason.STALE_HEAD_REVISION
    assert _event_count(path) == 1

    dispatch_revision = session.record_dispatch_intent(
        run_id="run-1",
        operation_id="operation-1",
        expected_head_journal_revision=accepted_revision,
        durable_dispatch_intent_ref="dispatch-ref",
    )
    assert session.record_dispatch_intent(
        run_id="run-1",
        operation_id="operation-1",
        expected_head_journal_revision=accepted_revision,
        durable_dispatch_intent_ref="dispatch-ref",
    ) == dispatch_revision
    with pytest.raises(CommandJournalConflict) as dispatch_replay:
        session.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            durable_dispatch_intent_ref="different-dispatch-ref",
        )
    assert dispatch_replay.value.reason is CommandJournalConflictReason.DISPATCH_REPLAY_CONFLICT
    observed_revision = session.record_observed_result(
        run_id="run-1",
        operation_id="operation-1",
        expected_head_journal_revision=dispatch_revision,
        result_ref="result-ref",
        result_hash=HASH_D,
    )
    assert session.record_observed_result(
        run_id="run-1",
        operation_id="operation-1",
        expected_head_journal_revision=dispatch_revision,
        result_ref="result-ref",
        result_hash=HASH_D,
    ) == observed_revision

    for result_ref, result_hash in (("different-result-ref", HASH_D), ("result-ref", HASH_C)):
        with pytest.raises(CommandJournalConflict) as replay:
            session.record_observed_result(
                run_id="run-1",
                operation_id="operation-1",
                expected_head_journal_revision=dispatch_revision,
                result_ref=result_ref,
                result_hash=result_hash,
            )
        assert replay.value.reason is CommandJournalConflictReason.OBSERVATION_REPLAY_CONFLICT

    for callback in (
        lambda: session.record_accepted(accepted),
        lambda: session.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            durable_dispatch_intent_ref="dispatch-ref",
        ),
        lambda: session.record_observed_failure(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=dispatch_revision,
            failure_ref="failure-ref",
            failure_hash=HASH_D,
        ),
    ):
        with pytest.raises(CommandJournalConflict) as rollback:
            callback()
        assert rollback.value.reason is CommandJournalConflictReason.PHASE_ROLLBACK
    assert _event_count(path) == 3

    with pytest.raises(CommandJournalConflict) as identity:
        session.record_accepted(_accepted("other-operation", idempotency_key="key-operation-1"))
    assert identity.value.reason is CommandJournalConflictReason.IDENTITY_CONFLICT

    second_accepted = session.record_accepted(_accepted("second-operation"))
    with pytest.raises(CommandJournalConflict) as observation_before_dispatch:
        session.record_observed_result(
            run_id="run-1",
            operation_id="second-operation",
            expected_head_journal_revision=second_accepted,
            result_ref="result-ref",
            result_hash=HASH_D,
        )
    assert observation_before_dispatch.value.reason is CommandJournalConflictReason.OBSERVATION_WITHOUT_DISPATCH
    assert _event_count(path) == 4


def test_concurrent_same_and_different_transitions_preserve_one_complete_head(tmp_path: Path) -> None:
    session = create_command_journal(tmp_path / "journal.sqlite3").start()
    accepted = session.record_accepted(_accepted())
    same_start = threading.Barrier(2)

    def same_dispatch() -> int:
        same_start.wait()
        return session.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted,
            durable_dispatch_intent_ref="dispatch-ref",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = tuple(executor.map(lambda _: same_dispatch(), range(2)))
    assert first == second == 2

    second_accepted = session.record_accepted(_accepted("second-operation"))
    different_start = threading.Barrier(2)

    def different_dispatch(reference: str) -> int | CommandJournalConflictReason:
        different_start.wait()
        try:
            return session.record_dispatch_intent(
                run_id="run-1",
                operation_id="second-operation",
                expected_head_journal_revision=second_accepted,
                durable_dispatch_intent_ref=reference,
            )
        except CommandJournalConflict as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(different_dispatch, ("dispatch-a", "dispatch-b")))
    assert sum(isinstance(result, int) for result in results) == 1
    assert results.count(CommandJournalConflictReason.DISPATCH_REPLAY_CONFLICT) == 1


def test_writer_output_is_readable_across_generations_without_any_runtime_route(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = create_command_journal(path)
    first = journal.start()
    second = journal.start()

    accepted_only = first.record_accepted(_accepted("accepted"))
    dispatched_accepted = first.record_accepted(_accepted("dispatched"))
    dispatched = second.record_dispatch_intent(
        run_id="run-1",
        operation_id="dispatched",
        expected_head_journal_revision=dispatched_accepted,
        durable_dispatch_intent_ref="dispatch-ref",
    )
    result_accepted = first.record_accepted(_accepted("result"))
    result_dispatched = second.record_dispatch_intent(
        run_id="run-1",
        operation_id="result",
        expected_head_journal_revision=result_accepted,
        durable_dispatch_intent_ref="result-dispatch-ref",
    )
    result = second.record_observed_result(
        run_id="run-1",
        operation_id="result",
        expected_head_journal_revision=result_dispatched,
        result_ref="result-ref",
        result_hash=HASH_D,
    )
    failure_accepted = first.record_accepted(_accepted("failure"))
    failure_dispatched = second.record_dispatch_intent(
        run_id="run-1",
        operation_id="failure",
        expected_head_journal_revision=failure_accepted,
        durable_dispatch_intent_ref="failure-dispatch-ref",
    )
    failure = second.record_observed_failure(
        run_id="run-1",
        operation_id="failure",
        expected_head_journal_revision=failure_dispatched,
        failure_ref="failure-ref",
        failure_hash=HASH_D,
    )

    reader = SourceHistorySQLiteReader(path)
    facts = {
        operation_id: reader.query(_query(operation_id, first_generation=1, last_generation=2))
        for operation_id in ("accepted", "dispatched", "result", "failure")
    }

    assert accepted_only < dispatched < result < failure
    assert {
        operation_id: result.facts[0].conclusion
        for operation_id, result in facts.items()
        if isinstance(result, SourceHistoryMatched)
    } == {
        "accepted": "accepted_no_dispatch",
        "dispatched": "dispatch_not_observed",
        "result": "observed_result",
        "failure": "observed_failure",
    }


def test_open_failures_are_closed_and_do_not_repair_existing_files(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not sqlite")
    before = corrupt.read_bytes()

    with pytest.raises(CommandJournalError) as corrupt_error:
        open_command_journal(corrupt)
    assert corrupt_error.value.reason is CommandJournalErrorReason.CORRUPT
    assert corrupt.read_bytes() == before

    wal = tmp_path / "wal.sqlite3"
    connection = sqlite3.connect(wal)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker(value) VALUES ('preserve-me')")
        connection.commit()
    finally:
        connection.close()

    before = wal.read_bytes()
    with pytest.raises(CommandJournalError) as wal_error:
        open_command_journal(wal)
    assert wal_error.value.reason is CommandJournalErrorReason.PRAGMA_MISMATCH
    assert wal.read_bytes() == before
    connection = sqlite3.connect(wal)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert connection.execute("SELECT value FROM marker").fetchone() == ("preserve-me",)
    finally:
        connection.close()

    schema = tmp_path / "schema.sqlite3"
    create_command_journal(schema)
    connection = sqlite3.connect(schema)
    try:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        connection.commit()
    finally:
        connection.close()
    before = schema.read_bytes()
    with pytest.raises(CommandJournalError) as schema_error:
        open_command_journal(schema)
    assert schema_error.value.reason is CommandJournalErrorReason.SCHEMA_MISMATCH
    assert schema.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode proof")
def test_open_rejects_an_existing_readonly_journal_without_repairing_it(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    create_command_journal(path)
    before = path.read_bytes()
    path.chmod(0o444)
    try:
        with pytest.raises(CommandJournalError) as readonly_error:
            open_command_journal(path)
        assert readonly_error.value.reason is CommandJournalErrorReason.READONLY
        assert path.read_bytes() == before
    finally:
        path.chmod(0o644)


def test_command_journal_internal_modules_form_a_one_way_dag() -> None:
    project_root = Path(__file__).parents[1]
    source_port = project_root / "src" / "seektalent" / "source_port"
    facade_path = source_port / "command_journal.py"
    engine_path = source_port / "_command_journal_engine.py"
    types_path = source_port / "_command_journal_types.py"
    obsolete_capability_path = source_port / "command_journal_capability.py"

    def imported_modules(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        return modules

    private_modules = {
        "seektalent.source_port._command_journal_engine",
        "seektalent.source_port._command_journal_types",
    }
    module_imports = {
        facade_path: imported_modules(facade_path),
        engine_path: imported_modules(engine_path),
        types_path: imported_modules(types_path),
    }
    engine_tree = ast.parse(engine_path.read_text(encoding="utf-8"))
    engine_functions = {
        node.name for node in engine_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert not obsolete_capability_path.exists()
    assert all(name.startswith("_") for name in engine_functions)
    assert module_imports[facade_path] & private_modules == private_modules
    assert module_imports[engine_path] & private_modules == {"seektalent.source_port._command_journal_types"}
    assert not module_imports[types_path] & private_modules
    assert "seektalent.source_port.command_journal" not in module_imports[engine_path]
    assert "seektalent.source_port.command_journal" not in module_imports[types_path]

    allowed_private_imports = {
        facade_path: private_modules,
        engine_path: {"seektalent.source_port._command_journal_types"},
        types_path: set(),
    }
    for path in (project_root / "src").rglob("*.py"):
        private_imports = imported_modules(path) & private_modules
        assert private_imports <= allowed_private_imports.get(path, set())

    assert set(command_journal.__all__) == {
        "AcceptedCommand",
        "CommandJournal",
        "CommandJournalConflict",
        "CommandJournalConflictReason",
        "CommandJournalError",
        "CommandJournalErrorReason",
        "CommandJournalSession",
        "create_command_journal",
        "open_command_journal",
    }


def test_journal_stays_production_unreachable_and_excludes_sensitive_payload_columns(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    source_port = project_root / "src" / "seektalent" / "source_port"
    journal_modules = {
        source_port / "command_journal.py",
        source_port / "_command_journal_engine.py",
        source_port / "_command_journal_types.py",
    }

    production_callers = []
    for path in (project_root / "src").rglob("*.py"):
        if path in journal_modules:
            continue
        if "command_journal" in path.read_text(encoding="utf-8"):
            production_callers.append(path.relative_to(project_root).as_posix())
    assert production_callers == []

    connection_path = tmp_path / "journal.sqlite3"
    try:
        journal = create_command_journal(connection_path)
        journal.close()
        connection = sqlite3.connect(connection_path)
        try:
            columns = {
                str(row[1])
                for table in ("source_history_events", "source_history_heads")
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
        finally:
            connection.close()
    finally:
        connection_path.unlink(missing_ok=True)
    forbidden = {
        "raw_runtime_token",
        "browser_control_secret",
        "candidate",
        "resume",
        "payload",
        "result_spool",
        "safe_retry_ordinal",
        "retention",
        "compaction",
        "url",
        "cookie",
        "html",
        "stdout",
        "stderr",
    }
    assert columns.isdisjoint(forbidden)
