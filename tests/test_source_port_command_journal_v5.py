from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import inspect
from pathlib import Path
import sqlite3
from typing import get_type_hints
from unittest.mock import patch

import pytest

import seektalent.source_port._command_journal_engine as journal_engine
import seektalent.source_port.history_sqlite_reader as history_reader
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    CommandJournalError,
    CommandJournalErrorReason,
    CommandJournalSession,
    create_command_journal,
    open_command_journal,
)
from seektalent.source_port.history_contract import (
    AllAuthorizationsSelector,
    ExactAuthorizationSelector,
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader


LEGACY_SCHEMA_VERSION = 4
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
MIGRATION_FAULT_POINTS = (
    "after_validation",
    "after_events_create",
    "after_events_copy",
    "after_heads_create",
    "after_heads_copy",
    "after_legacy_drop",
    "after_table_rename",
    "after_schema_restore",
    "after_user_version",
    "after_v5_validation",
)


class InjectedMigrationFault(RuntimeError):
    pass


def _accepted(
    operation_id: str = "operation-1",
    *,
    attempt_no: int = 1,
    idempotency_key: str | None = None,
) -> AcceptedCommand:
    return AcceptedCommand(
        run_id="run-1",
        operation_id=operation_id,
        source="liepin",
        operation_kind="search",
        idempotency_key=idempotency_key or f"key-{operation_id}",
        request_hash=HASH_A,
        attempt_no=attempt_no,
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
    *,
    selector: ExactAuthorizationSelector | AllAuthorizationsSelector,
    attempt_no: int,
    last_generation: int,
) -> SourceHistoryQueryV1:
    return SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id="run-1",
        operation_id="operation-1",
        source="liepin",
        operation_kind="search",
        idempotency_key="key-operation-1",
        request_hash=HASH_A,
        attempt_no=attempt_no,
        authorization_selector=selector,
        searched_first_generation=1,
        searched_last_generation=last_generation,
        expected_source_operation_ledger_revision=1 if attempt_no == 1 else 3,
        expected_reconciliation_revision=0 if attempt_no == 1 else 1,
    )


def _legacy_schema_statements() -> tuple[str, ...]:
    return getattr(history_reader, "LEGACY_SCHEMA_STATEMENTS", history_reader.SCHEMA_STATEMENTS)


def _copy_database_as_v4(source_path: Path, legacy_path: Path) -> None:
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(legacy_path, isolation_level=None)
    try:
        target.execute("PRAGMA journal_mode=DELETE")
        target.execute("PRAGMA synchronous=FULL")
        target.execute("PRAGMA foreign_keys=ON")
        target.execute("BEGIN IMMEDIATE")
        for statement in _legacy_schema_statements():
            target.execute(statement)

        state = source.execute(
            """
            SELECT last_journal_revision, last_sidecar_generation
            FROM source_history_state WHERE singleton = 1
            """
        ).fetchone()
        assert state is not None
        target.execute(
            """
            UPDATE source_history_state
            SET last_journal_revision = ?, last_sidecar_generation = ?
            WHERE singleton = 1
            """,
            state,
        )
        generations = source.execute(
            """
            SELECT generation, sidecar_instance_id, retained, complete
            FROM source_history_generations ORDER BY generation
            """
        ).fetchall()
        target.executemany(
            """
            INSERT INTO source_history_generations(
                generation, sidecar_instance_id, retained, complete
            ) VALUES (?, ?, ?, ?)
            """,
            generations,
        )
        for table in ("source_history_events", "source_history_heads"):
            columns = tuple(str(row[1]) for row in target.execute(f"PRAGMA table_info({table})").fetchall())
            rows = source.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            target.executemany(
                f"INSERT INTO {table}({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                rows,
            )
        target.execute(f"PRAGMA user_version={LEGACY_SCHEMA_VERSION}")
        target.commit()
    finally:
        if target.in_transaction:
            target.rollback()
        target.close()
        source.close()


def _legacy_snapshot(path: Path) -> dict[str, tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]]:
    connection = sqlite3.connect(path)
    try:
        snapshot = {}
        for table in (
            "source_history_state",
            "source_history_generations",
            "source_history_events",
            "source_history_heads",
        ):
            columns = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall())
            order_by = {
                "source_history_state": "singleton",
                "source_history_generations": "generation",
                "source_history_events": "journal_revision",
                "source_history_heads": "run_id, operation_id, dispatch_authorization_ordinal",
            }[table]
            rows = tuple(connection.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_by}").fetchall())
            snapshot[table] = (columns, rows)
        return snapshot
    finally:
        connection.close()


def _legacy_projection(
    path: Path,
    snapshot: dict[str, tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]],
) -> dict[str, tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]]:
    connection = sqlite3.connect(path)
    try:
        projected = {}
        for table, (columns, _) in snapshot.items():
            order_by = {
                "source_history_state": "singleton",
                "source_history_generations": "generation",
                "source_history_events": "journal_revision",
                "source_history_heads": "run_id, operation_id, dispatch_authorization_ordinal",
            }[table]
            rows = tuple(connection.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_by}").fetchall())
            projected[table] = (columns, rows)
        return projected
    finally:
        connection.close()


def _schema_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    assert row is not None
    return int(row[0])


def _source_with_phases(path: Path) -> None:
    session = create_command_journal(path).start()
    session.record_accepted(_accepted("accepted"), accepted_ack_bytes=b'{"ack":"accepted"}')

    dispatch = session.record_accepted(_accepted("dispatch"), accepted_ack_bytes=b'{"ack":"dispatch"}')
    session.record_dispatch_intent(
        run_id="run-1",
        operation_id="dispatch",
        expected_head_journal_revision=dispatch,
        durable_dispatch_intent_ref="dispatch-ref",
    )

    result = session.record_accepted(_accepted("result"), accepted_ack_bytes=b'{"ack":"result"}')
    result_dispatch = session.record_dispatch_intent(
        run_id="run-1",
        operation_id="result",
        expected_head_journal_revision=result,
        durable_dispatch_intent_ref="result-dispatch-ref",
    )
    result_bytes = b'{"reply":"result"}'
    result_digest = sha256(result_bytes).hexdigest()
    session.record_observed_result(
        run_id="run-1",
        operation_id="result",
        expected_head_journal_revision=result_dispatch,
        result_ref=result_digest,
        result_hash=result_digest,
        terminal_reply_bytes=result_bytes,
    )

    failure = session.record_accepted(_accepted("failure"), accepted_ack_bytes=b'{"ack":"failure"}')
    failure_dispatch = session.record_dispatch_intent(
        run_id="run-1",
        operation_id="failure",
        expected_head_journal_revision=failure,
        durable_dispatch_intent_ref="failure-dispatch-ref",
    )
    failure_bytes = b'{"reply":"failure"}'
    failure_digest = sha256(failure_bytes).hexdigest()
    session.record_observed_failure(
        run_id="run-1",
        operation_id="failure",
        expected_head_journal_revision=failure_dispatch,
        failure_ref=failure_digest,
        failure_hash=failure_digest,
        terminal_reply_bytes=failure_bytes,
    )


def _v4_with_phases(tmp_path: Path) -> Path:
    source_path = tmp_path / "source.sqlite3"
    legacy_path = tmp_path / "legacy.sqlite3"
    _source_with_phases(source_path)
    _copy_database_as_v4(source_path, legacy_path)
    return legacy_path


def test_populated_v4_migrates_to_v5_without_changing_any_legacy_fact_or_bytes(tmp_path: Path) -> None:
    legacy_path = _v4_with_phases(tmp_path)
    before = _legacy_snapshot(legacy_path)

    open_command_journal(legacy_path).close()

    assert history_reader.SCHEMA_VERSION == 5
    assert _schema_version(legacy_path) == 5
    assert _legacy_projection(legacy_path, before) == before
    with sqlite3.connect(legacy_path) as connection:
        for table in ("source_history_events", "source_history_heads"):
            epochs = connection.execute(
                f"""
                SELECT DISTINCT dispatch_authorization_ordinal, safe_retry_commit_ref,
                                expected_source_operation_ledger_revision,
                                expected_reconciliation_revision
                FROM {table}
                """
            ).fetchall()
            assert epochs == [(1, None, 1, 0)]


@pytest.mark.parametrize("phase", ["empty", "accepted", "dispatch_intent", "observed_result", "observed_failure"])
def test_empty_and_every_legal_v4_lifecycle_phase_migrate_and_reopen(
    tmp_path: Path,
    phase: str,
) -> None:
    source_path = tmp_path / f"{phase}-source.sqlite3"
    journal = create_command_journal(source_path)
    session = journal.start()
    if phase != "empty":
        accepted = session.record_accepted(_accepted(), accepted_ack_bytes=b"accepted-bytes")
        if phase != "accepted":
            dispatch = session.record_dispatch_intent(
                run_id="run-1",
                operation_id="operation-1",
                expected_head_journal_revision=accepted,
                durable_dispatch_intent_ref="dispatch-ref",
            )
            if phase == "observed_result":
                reply = b"result-bytes"
                digest = sha256(reply).hexdigest()
                session.record_observed_result(
                    run_id="run-1",
                    operation_id="operation-1",
                    expected_head_journal_revision=dispatch,
                    result_ref=digest,
                    result_hash=digest,
                    terminal_reply_bytes=reply,
                )
            elif phase == "observed_failure":
                reply = b"failure-bytes"
                digest = sha256(reply).hexdigest()
                session.record_observed_failure(
                    run_id="run-1",
                    operation_id="operation-1",
                    expected_head_journal_revision=dispatch,
                    failure_ref=digest,
                    failure_hash=digest,
                    terminal_reply_bytes=reply,
                )
    legacy_path = tmp_path / f"{phase}-legacy.sqlite3"
    _copy_database_as_v4(source_path, legacy_path)

    open_command_journal(legacy_path).close()
    reopened = open_command_journal(legacy_path)

    assert _schema_version(legacy_path) == 5
    assert reopened.start().generation == 2


@pytest.mark.parametrize("fault_point", MIGRATION_FAULT_POINTS)
def test_every_v4_to_v5_migration_fault_rolls_back_to_complete_v4(
    tmp_path: Path,
    fault_point: str,
) -> None:
    legacy_path = _v4_with_phases(tmp_path)
    before = _legacy_snapshot(legacy_path)

    def fail(actual: str) -> None:
        if actual == fault_point:
            raise InjectedMigrationFault(actual)

    with patch.object(journal_engine, "_migration_checkpoint", side_effect=fail, create=True):
        with pytest.raises(InjectedMigrationFault, match=fault_point):
            open_command_journal(legacy_path)

    assert _schema_version(legacy_path) == LEGACY_SCHEMA_VERSION
    assert _legacy_snapshot(legacy_path) == before
    open_command_journal(legacy_path).close()
    assert _schema_version(legacy_path) == 5


def test_migration_commit_failure_leaves_complete_v4_and_retry_succeeds(tmp_path: Path) -> None:
    legacy_path = _v4_with_phases(tmp_path)
    before = _legacy_snapshot(legacy_path)

    with patch.object(
        journal_engine,
        "_commit_migration",
        side_effect=sqlite3.OperationalError("simulated I/O error"),
        create=True,
    ):
        with pytest.raises(CommandJournalError) as failure:
            open_command_journal(legacy_path)

    assert failure.value.reason is CommandJournalErrorReason.IO_ERROR
    assert _schema_version(legacy_path) == LEGACY_SCHEMA_VERSION
    assert _legacy_snapshot(legacy_path) == before
    open_command_journal(legacy_path).close()
    assert _schema_version(legacy_path) == 5


def test_migration_commit_error_after_commit_leaves_complete_v5_and_retry_succeeds(tmp_path: Path) -> None:
    legacy_path = _v4_with_phases(tmp_path)
    before = _legacy_snapshot(legacy_path)

    def commit_then_fail(connection: sqlite3.Connection) -> None:
        connection.commit()
        error = sqlite3.OperationalError("simulated lost commit acknowledgement")
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        raise error

    with patch.object(journal_engine, "_commit_migration", side_effect=commit_then_fail, create=True):
        with pytest.raises(CommandJournalError) as failure:
            open_command_journal(legacy_path)

    assert failure.value.reason is CommandJournalErrorReason.IO_ERROR
    assert _schema_version(legacy_path) == 5
    assert _legacy_projection(legacy_path, before) == before
    open_command_journal(legacy_path).close()


def test_post_commit_reopen_failure_leaves_complete_v5_and_retry_succeeds(tmp_path: Path) -> None:
    legacy_path = _v4_with_phases(tmp_path)

    with patch.object(
        journal_engine,
        "_validate_migrated_database",
        side_effect=CommandJournalError(CommandJournalErrorReason.CANNOT_OPEN),
        create=True,
    ):
        with pytest.raises(CommandJournalError) as failure:
            open_command_journal(legacy_path)

    assert failure.value.reason is CommandJournalErrorReason.CANNOT_OPEN
    assert _schema_version(legacy_path) == 5
    open_command_journal(legacy_path).close()


def test_stale_v4_open_accepts_a_complete_v5_migrated_by_a_peer(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path)
    connection = journal_engine._open_write_connection(path)
    try:
        journal_engine._migrate_v4_to_v5(connection)
    finally:
        connection.close()

    assert _schema_version(path) == 5
    open_command_journal(path).close()


def test_v4_duplicate_identity_alias_is_rejected_without_replacement(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    session = create_command_journal(source_path).start()
    session.record_accepted(_accepted("first"))
    session.record_accepted(_accepted("second"))
    legacy_path = tmp_path / "legacy.sqlite3"
    _copy_database_as_v4(source_path, legacy_path)

    connection = sqlite3.connect(legacy_path)
    try:
        trigger = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger' AND name = 'source_history_events_no_update'
            """
        ).fetchone()
        assert trigger is not None
        connection.execute("DROP TRIGGER source_history_events_no_update")
        connection.execute(
            """
            UPDATE source_history_events SET idempotency_key = 'key-first'
            WHERE operation_id = 'second'
            """
        )
        connection.execute(
            """
            UPDATE source_history_heads SET idempotency_key = 'key-first'
            WHERE operation_id = 'second'
            """
        )
        connection.execute(str(trigger[0]))
        connection.commit()
    finally:
        connection.close()
    before = _legacy_snapshot(legacy_path)

    with pytest.raises(CommandJournalError) as failure:
        open_command_journal(legacy_path)

    assert failure.value.reason is CommandJournalErrorReason.CORRUPT
    assert _schema_version(legacy_path) == LEGACY_SCHEMA_VERSION
    assert _legacy_snapshot(legacy_path) == before


def test_v4_schema_drift_is_rejected_without_replacement(tmp_path: Path) -> None:
    legacy_path = _v4_with_phases(tmp_path)
    connection = sqlite3.connect(legacy_path)
    try:
        connection.execute("DROP TRIGGER source_history_events_no_delete")
        connection.commit()
    finally:
        connection.close()
    before = _legacy_snapshot(legacy_path)

    with pytest.raises(CommandJournalError) as failure:
        open_command_journal(legacy_path)

    assert failure.value.reason is CommandJournalErrorReason.SCHEMA_MISMATCH
    assert _schema_version(legacy_path) == LEGACY_SCHEMA_VERSION
    assert _legacy_snapshot(legacy_path) == before


def test_v4_corrupt_row_is_rejected_without_replacement(tmp_path: Path) -> None:
    legacy_path = _v4_with_phases(tmp_path)
    connection = sqlite3.connect(legacy_path)
    try:
        trigger = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger' AND name = 'source_history_events_no_update'
            """
        ).fetchone()
        assert trigger is not None
        connection.execute("DROP TRIGGER source_history_events_no_update")
        connection.execute(
            """
            UPDATE source_history_events
            SET accepted_journal_revision = accepted_journal_revision + 1
            WHERE phase = 'accepted' AND operation_id = 'accepted'
            """
        )
        connection.execute(str(trigger[0]))
        connection.commit()
    finally:
        connection.close()
    before = _legacy_snapshot(legacy_path)

    with pytest.raises(CommandJournalError) as failure:
        open_command_journal(legacy_path)

    assert failure.value.reason is CommandJournalErrorReason.CORRUPT
    assert _schema_version(legacy_path) == LEGACY_SCHEMA_VERSION
    assert _legacy_snapshot(legacy_path) == before


def test_read_only_history_query_does_not_migrate_v4(tmp_path: Path) -> None:
    legacy_path = _v4_with_phases(tmp_path)
    before = _legacy_snapshot(legacy_path)

    result = SourceHistorySQLiteReader(legacy_path).query(
        _query(
            selector=ExactAuthorizationSelector(kind="exact", ordinal=1),
            attempt_no=1,
            last_generation=1,
        )
    )

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "schema_mismatch"
    assert _schema_version(legacy_path) == LEGACY_SCHEMA_VERSION
    assert _legacy_snapshot(legacy_path) == before


def _synthetic_history(tmp_path: Path, *, generations: int = 3) -> tuple[Path, CommandJournalSession]:
    path = tmp_path / "history.sqlite3"
    journal = create_command_journal(path)
    current = journal.start()
    current.record_accepted(_accepted(), accepted_ack_bytes=b"ordinal-1-ack")
    for _ in range(1, generations):
        current = journal.start()
    return path, current


def _insert_synthetic_accepted_epoch(
    path: Path,
    *,
    ordinal: int,
    attempt_no: int,
    retry_ref: str | None,
    ledger_revision: int,
    reconciliation_revision: int,
    request_hash: str = HASH_A,
    accepted_requirement_revision_id: str = "requirement-1",
    ignore_check_constraints: bool = False,
) -> None:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        if ignore_check_constraints:
            connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("BEGIN IMMEDIATE")
        event_source = connection.execute(
            """
            SELECT * FROM source_history_events
            WHERE dispatch_authorization_ordinal = 1 AND phase = 'accepted'
            """
        ).fetchone()
        head_source = connection.execute(
            """
            SELECT * FROM source_history_heads
            WHERE dispatch_authorization_ordinal = 1
            """
        ).fetchone()
        assert event_source is not None and head_source is not None
        revision = (
            int(
                connection.execute(
                    "SELECT last_journal_revision FROM source_history_state WHERE singleton = 1"
                ).fetchone()[0]
            )
            + 1
        )
        event = dict(event_source)
        event.update(
            journal_revision=revision,
            event_generation=ordinal,
            attempt_no=attempt_no,
            request_hash=request_hash,
            dispatch_authorization_ordinal=ordinal,
            safe_retry_commit_ref=retry_ref,
            expected_source_operation_ledger_revision=ledger_revision,
            expected_reconciliation_revision=reconciliation_revision,
            accepted_requirement_revision_id=accepted_requirement_revision_id,
            runtime_attempt_fence_ref=HASH_C,
            accepted_generation=ordinal,
            accepted_journal_revision=revision,
            authorized_dispatch_intent_id=f"intent-{ordinal}",
            authorized_dispatch_intent_revision=ordinal,
            authorized_dispatch_intent_digest=HASH_D,
            profile_binding_generation=ordinal,
            browser_control_scope_id=f"browser-scope-{ordinal}",
            controller_fence_ref=HASH_B,
            accepted_ack_bytes=f"ordinal-{ordinal}-ack".encode(),
        )
        head = dict(head_source)
        head.update(
            attempt_no=attempt_no,
            request_hash=request_hash,
            dispatch_authorization_ordinal=ordinal,
            safe_retry_commit_ref=retry_ref,
            expected_source_operation_ledger_revision=ledger_revision,
            expected_reconciliation_revision=reconciliation_revision,
            accepted_requirement_revision_id=accepted_requirement_revision_id,
            runtime_attempt_fence_ref=HASH_C,
            accepted_generation=ordinal,
            accepted_journal_revision=revision,
            authorized_dispatch_intent_id=f"intent-{ordinal}",
            authorized_dispatch_intent_revision=ordinal,
            authorized_dispatch_intent_digest=HASH_D,
            profile_binding_generation=ordinal,
            browser_control_scope_id=f"browser-scope-{ordinal}",
            controller_fence_ref=HASH_B,
            accepted_ack_bytes=f"ordinal-{ordinal}-ack".encode(),
            head_generation=ordinal,
            head_journal_revision=revision,
        )
        event_columns = tuple(event_source.keys())
        head_columns = tuple(head_source.keys())
        connection.execute(
            f"""
            INSERT INTO source_history_events({", ".join(event_columns)})
            VALUES ({", ".join("?" for _ in event_columns)})
            """,
            tuple(event[column] for column in event_columns),
        )
        connection.execute(
            f"""
            INSERT INTO source_history_heads({", ".join(head_columns)})
            VALUES ({", ".join("?" for _ in head_columns)})
            """,
            tuple(head[column] for column in head_columns),
        )
        connection.execute(
            "UPDATE source_history_state SET last_journal_revision = ? WHERE singleton = 1",
            (revision,),
        )
        connection.commit()
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def test_strict_v5_schema_has_closed_epoch_columns_and_unique_retry_refs(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path)

    assert history_reader.SCHEMA_VERSION == 5
    with sqlite3.connect(path) as connection:
        columns = tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(source_history_heads)"))
        retry_index = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND name = 'source_history_heads_operation_retry_ref'
            """
        ).fetchone()
    assert (
        "safe_retry_commit_ref",
        "expected_source_operation_ledger_revision",
        "expected_reconciliation_revision",
    ) == tuple(
        name
        for name in columns
        if name
        in {
            "safe_retry_commit_ref",
            "expected_source_operation_ledger_revision",
            "expected_reconciliation_revision",
        }
    )
    assert retry_index is not None
    assert "CREATE UNIQUE INDEX" in str(retry_index[0])


def test_v5_near_miss_retry_index_is_rejected_as_schema_mismatch(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP INDEX source_history_heads_operation_retry_ref")
        connection.execute(
            """
            CREATE INDEX source_history_heads_operation_retry_ref
            ON source_history_heads(run_id, operation_id, safe_retry_commit_ref)
            """
        )
        connection.commit()
    finally:
        connection.close()

    result = SourceHistorySQLiteReader(path).query(
        _query(
            selector=ExactAuthorizationSelector(kind="exact", ordinal=1),
            attempt_no=1,
            last_generation=3,
        )
    )

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "schema_mismatch"


def test_exact_and_all_queries_read_synthetic_multi_ordinal_history_across_attempts(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path, generations=2)
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=2,
        attempt_no=2,
        retry_ref="reconciliation-1",
        ledger_revision=3,
        reconciliation_revision=1,
    )
    reader = SourceHistorySQLiteReader(path)

    first = reader.query(
        _query(
            selector=ExactAuthorizationSelector(kind="exact", ordinal=1),
            attempt_no=1,
            last_generation=2,
        )
    )
    second = reader.query(
        _query(
            selector=ExactAuthorizationSelector(kind="exact", ordinal=2),
            attempt_no=2,
            last_generation=2,
        )
    )
    all_epochs = reader.query(
        _query(
            selector=AllAuthorizationsSelector(kind="all"),
            attempt_no=2,
            last_generation=2,
        )
    )

    assert isinstance(first, SourceHistoryMatched)
    assert isinstance(second, SourceHistoryMatched)
    assert isinstance(all_epochs, SourceHistoryMatched)
    assert tuple(fact.dispatch_authorization_ordinal for fact in all_epochs.facts) == (1, 2)
    assert tuple(fact.attempt_no for fact in all_epochs.facts) == (1, 2)
    assert second.facts[0].safe_retry_commit_ref == "reconciliation-1"
    assert second.facts[0].expected_source_operation_ledger_revision == 3
    assert second.facts[0].expected_reconciliation_revision == 1


@pytest.mark.parametrize(
    ("ordinal", "attempt_no", "retry_ref", "ledger_revision", "reconciliation_revision"),
    [
        (3, 3, "reconciliation-2", 5, 2),
        (2, 1, "reconciliation-1", 3, 1),
        (2, 2, None, 3, 1),
        (2, 2, "reconciliation-1", 1, 0),
    ],
    ids=["ordinal-gap", "attempt-not-increasing", "missing-retry-ref", "wrong-revision-matrix"],
)
def test_malformed_synthetic_epoch_histories_fail_closed(
    tmp_path: Path,
    ordinal: int,
    attempt_no: int,
    retry_ref: str | None,
    ledger_revision: int,
    reconciliation_revision: int,
) -> None:
    path, _ = _synthetic_history(tmp_path, generations=3)
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=ordinal,
        attempt_no=attempt_no,
        retry_ref=retry_ref,
        ledger_revision=ledger_revision,
        reconciliation_revision=reconciliation_revision,
        ignore_check_constraints=retry_ref is None or reconciliation_revision == 0,
    )

    result = SourceHistorySQLiteReader(path).query(
        _query(
            selector=AllAuthorizationsSelector(kind="all"),
            attempt_no=attempt_no,
            last_generation=3,
        )
    )

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "corrupt"


def test_reused_retry_ref_is_rejected_by_v5_storage(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path, generations=3)
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=2,
        attempt_no=2,
        retry_ref="reconciliation-1",
        ledger_revision=3,
        reconciliation_revision=1,
    )

    with pytest.raises(sqlite3.IntegrityError):
        _insert_synthetic_accepted_epoch(
            path,
            ordinal=3,
            attempt_no=3,
            retry_ref="reconciliation-1",
            ledger_revision=5,
            reconciliation_revision=2,
        )


def test_duplicate_ordinal_is_rejected_by_v5_storage(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path, generations=2)
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=2,
        attempt_no=2,
        retry_ref="reconciliation-1",
        ledger_revision=3,
        reconciliation_revision=1,
    )

    with pytest.raises(sqlite3.IntegrityError):
        _insert_synthetic_accepted_epoch(
            path,
            ordinal=2,
            attempt_no=3,
            retry_ref="reconciliation-2",
            ledger_revision=5,
            reconciliation_revision=2,
        )


@pytest.mark.parametrize(
    ("ledger_revision", "reconciliation_revision"),
    [(3, 2), (5, 1)],
    ids=["ledger-not-increasing", "reconciliation-not-increasing"],
)
def test_non_monotonic_synthetic_epoch_history_fails_closed(
    tmp_path: Path,
    ledger_revision: int,
    reconciliation_revision: int,
) -> None:
    path, _ = _synthetic_history(tmp_path, generations=3)
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=2,
        attempt_no=2,
        retry_ref="reconciliation-1",
        ledger_revision=3,
        reconciliation_revision=1,
    )
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=3,
        attempt_no=3,
        retry_ref="reconciliation-2",
        ledger_revision=ledger_revision,
        reconciliation_revision=reconciliation_revision,
    )

    result = SourceHistorySQLiteReader(path).query(
        _query(
            selector=AllAuthorizationsSelector(kind="all"),
            attempt_no=3,
            last_generation=3,
        )
    )

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "corrupt"


def test_event_head_epoch_divergence_fails_closed(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path, generations=2)
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=2,
        attempt_no=2,
        retry_ref="reconciliation-1",
        ledger_revision=3,
        reconciliation_revision=1,
    )
    connection = sqlite3.connect(path)
    try:
        trigger = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger' AND name = 'source_history_events_no_update'
            """
        ).fetchone()
        assert trigger is not None
        connection.execute("DROP TRIGGER source_history_events_no_update")
        connection.execute(
            """
            UPDATE source_history_events
            SET safe_retry_commit_ref = 'different-reconciliation'
            WHERE dispatch_authorization_ordinal = 2
            """
        )
        connection.execute(str(trigger[0]))
        connection.commit()
    finally:
        connection.close()

    result = SourceHistorySQLiteReader(path).query(
        _query(
            selector=AllAuthorizationsSelector(kind="all"),
            attempt_no=2,
            last_generation=2,
        )
    )

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "corrupt"


def test_cross_identity_synthetic_epoch_returns_typed_conflict_or_unavailable(tmp_path: Path) -> None:
    path, _ = _synthetic_history(tmp_path, generations=2)
    _insert_synthetic_accepted_epoch(
        path,
        ordinal=2,
        attempt_no=2,
        retry_ref="reconciliation-1",
        ledger_revision=3,
        reconciliation_revision=1,
        request_hash=HASH_D,
    )

    result = SourceHistorySQLiteReader(path).query(
        _query(
            selector=AllAuthorizationsSelector(kind="all"),
            attempt_no=2,
            last_generation=2,
        )
    )

    assert isinstance(result, SourceHistoryIdentityConflict | SourceHistoryUnavailable)


def test_public_writer_rejects_ordinal_two_without_durable_safe_retry_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "writer.sqlite3"
    session = create_command_journal(path).start()
    forged = replace(_accepted(), dispatch_authorization_ordinal=2)

    assert get_type_hints(AcceptedCommand)["dispatch_authorization_ordinal"] is int
    with pytest.raises(ValueError):
        session.record_accepted(forged)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_history_events").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM source_history_heads").fetchone() == (0,)
    for method in (
        CommandJournalSession.record_dispatch_intent,
        CommandJournalSession.record_observed_result,
        CommandJournalSession.record_observed_failure,
    ):
        parameter = inspect.signature(method).parameters[
            "dispatch_authorization_ordinal"
        ]
        assert parameter.default == 1
