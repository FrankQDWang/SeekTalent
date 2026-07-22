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
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


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
        command_journal,
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
        connection.execute("PRAGMA user_version=3")
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


def test_journal_stays_production_unreachable_and_excludes_sensitive_payload_columns(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    module_path = project_root / "src" / "seektalent" / "source_port" / "command_journal.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_modules <= {
        "__future__",
        "contextlib",
        "dataclasses",
        "enum",
        "pathlib",
        "secrets",
        "sqlite3",
        "stat",
        "threading",
        "typing",
        "weakref",
        "seektalent.source_port.history_contract",
        "seektalent.source_port.history_sqlite_reader",
    }

    production_callers = []
    for path in (project_root / "src").rglob("*.py"):
        if path == module_path:
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
