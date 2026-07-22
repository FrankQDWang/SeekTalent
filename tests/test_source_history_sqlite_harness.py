from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

import seektalent.source_port.history_sqlite_reader as history_sqlite_reader
import tests.support.source_history_sqlite_storage as sqlite_storage
from seektalent.source_port.history_contract import (
    ExactAuthorizationSelector,
    JSON_SAFE_INTEGER,
    SQLITE_MAX_INTEGER,
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)
from seektalent.source_port.history_sqlite_reader import (
    SourceHistoryReadDeadlineExceeded,
    SourceHistorySQLiteReader,
)
from tests.support.source_history_sqlite_harness import (
    AcceptedHistoryInput,
    CommitAcknowledgementLost,
    InjectedJournalFault,
    JournalWriteConflict,
    SourceHistorySQLiteHarness,
)
from tests.support.source_history_sqlite_storage import (
    JournalUnavailable,
    Transaction,
    allocate_revision,
    connect_existing,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _accepted(
    operation_id: str = "operation-1",
    *,
    run_id: str = "run-1",
    idempotency_key: str | None = None,
    operation_kind: str = "search",
    request_hash: str = HASH_A,
    attempt_no: int = 1,
) -> AcceptedHistoryInput:
    return AcceptedHistoryInput(
        run_id=run_id,
        operation_id=operation_id,
        source="liepin",
        operation_kind=operation_kind,  # type: ignore[arg-type]
        idempotency_key=idempotency_key or f"key-{operation_id}",
        request_hash=request_hash,
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
    operation_id: str = "operation-1",
    *,
    run_id: str = "run-1",
    idempotency_key: str | None = None,
    operation_kind: str = "search",
    request_hash: str = HASH_A,
    attempt_no: int = 1,
    first_generation: int = 1,
    last_generation: int = 1,
    hint: int | None = None,
    expected_ledger_revision: int = 1,
    expected_reconciliation_revision: int = 0,
) -> SourceHistoryQueryV1:
    return SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id=run_id,
        operation_id=operation_id,
        source="liepin",
        operation_kind=operation_kind,  # type: ignore[arg-type]
        idempotency_key=idempotency_key or f"key-{operation_id}",
        request_hash=request_hash,
        attempt_no=attempt_no,
        authorization_selector=ExactAuthorizationSelector(kind="exact", ordinal=1),
        accepted_generation_hint=hint,
        searched_first_generation=first_generation,
        searched_last_generation=last_generation,
        expected_source_operation_ledger_revision=expected_ledger_revision,
        expected_reconciliation_revision=expected_reconciliation_revision,
    )


def _harness(tmp_path: Path, *generations: int) -> SourceHistorySQLiteHarness:
    harness = SourceHistorySQLiteHarness.create(tmp_path / "source_history.sqlite3")
    for generation in generations:
        harness.register_generation(generation)
    return harness


def test_production_reader_requires_an_explicit_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="Path"):
        SourceHistorySQLiteReader(str(tmp_path / "history.sqlite3"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="absolute"):
        SourceHistorySQLiteReader(Path("history.sqlite3"))


def test_tests_only_harness_delegates_query_to_the_production_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(tmp_path, 1)
    request = _query()
    sentinel = SourceHistoryNotFound.model_validate(
        {
            **request.model_dump(exclude={"contract_version"}),
            "contract_version": "seektalent.source-port.query.result/v1",
            "outcome": "not_found",
            "oldest_retained_generation": 1,
            "newest_known_generation": 1,
            "history_complete": True,
            "history_truncated": False,
        },
        strict=True,
    )
    calls: list[tuple[Path, SourceHistoryQueryV1]] = []

    def query(
        reader: SourceHistorySQLiteReader,
        value: SourceHistoryQueryV1,
        *,
        deadline: float | None = None,
    ) -> SourceHistoryNotFound:
        assert deadline is None
        calls.append((reader.path, value))
        return sentinel

    monkeypatch.setattr(SourceHistorySQLiteReader, "query", query)

    assert harness.query(request) is sentinel
    assert calls == [(harness.path, request)]


def test_tests_only_storage_does_not_redeclare_the_production_schema_contract() -> None:
    source = inspect.getsource(sqlite_storage)

    assert sqlite_storage.SCHEMA_STATEMENTS is history_sqlite_reader.SCHEMA_STATEMENTS
    assert sqlite_storage.SCHEMA_VERSION == history_sqlite_reader.SCHEMA_VERSION
    assert "_STATE_TABLE_DDL =" not in source
    assert "_GENERATION_TABLE_DDL =" not in source
    assert "_EVENT_TABLE_DDL =" not in source
    assert "_HEAD_TABLE_DDL =" not in source
    assert "_TRIGGER_DDLS =" not in source
    assert "def _verify_schema(" not in source
    assert "def _verify_journal_consistency(" not in source


def test_production_reader_returns_all_four_closed_history_results(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    reader = SourceHistorySQLiteReader(harness.path)

    matched = reader.query(_query())
    not_found = reader.query(_query(operation_id="absent", idempotency_key="key-absent"))
    conflict = reader.query(_query(request_hash=HASH_D))
    unavailable = reader.query(_query(first_generation=1, last_generation=2))

    assert isinstance(matched, SourceHistoryMatched)
    assert matched.facts[0].conclusion == "accepted_no_dispatch"
    assert isinstance(not_found, SourceHistoryNotFound)
    assert isinstance(conflict, SourceHistoryIdentityConflict)
    assert conflict.conflict_reasons == ("request_hash_mismatch",)
    assert isinstance(unavailable, SourceHistoryUnavailable)
    assert unavailable.reason == "unknown_generation"
    assert all(
        result.run_id == "run-1" and result.operation_id in {"operation-1", "absent"}
        for result in (matched, not_found, conflict, unavailable)
    )


def test_production_reader_closes_unknown_retention_gap_and_truncated_ranges(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, 2, 3)
    reader = SourceHistorySQLiteReader(harness.path)

    unknown = reader.query(_query(first_generation=1, last_generation=4))
    assert isinstance(unknown, SourceHistoryUnavailable)
    assert unknown.reason == "unknown_generation"

    harness.set_generation_fixture(2, retained=False, complete=True)
    gap = reader.query(_query(first_generation=1, last_generation=3))
    assert isinstance(gap, SourceHistoryUnavailable)
    assert gap.reason == "retention_gap"

    harness.set_generation_fixture(2, retained=True, complete=False)
    truncated = reader.query(_query(first_generation=1, last_generation=3))
    assert isinstance(truncated, SourceHistoryUnavailable)
    assert truncated.reason == "truncated"


def test_production_reader_file_failures_are_closed_and_do_not_create_missing_database(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.sqlite3"
    missing = SourceHistorySQLiteReader(missing_path).query(_query())
    assert isinstance(missing, SourceHistoryUnavailable)
    assert missing.reason == "unreadable"
    assert not missing_path.exists()

    corrupt_path = tmp_path / "corrupt.sqlite3"
    corrupt_path.write_bytes(b"not sqlite")
    corrupt = SourceHistorySQLiteReader(corrupt_path).query(_query())
    assert isinstance(corrupt, SourceHistoryUnavailable)
    assert corrupt.reason == "corrupt"

    schema = _harness(tmp_path / "schema", 1)
    connection = sqlite3.connect(schema.path)
    try:
        connection.execute("PRAGMA user_version=2")
    finally:
        connection.close()
    mismatch = SourceHistorySQLiteReader(schema.path).query(_query())
    assert isinstance(mismatch, SourceHistoryUnavailable)
    assert mismatch.reason == "schema_mismatch"

    wal_path = tmp_path / "wal.sqlite3"
    connection = sqlite3.connect(wal_path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.commit()
    finally:
        connection.close()
    pragma = SourceHistorySQLiteReader(wal_path).query(_query())
    assert isinstance(pragma, SourceHistoryUnavailable)
    assert pragma.reason == "pragma_mismatch"


def test_production_reader_does_not_change_database_bytes_mtime_or_data_version(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    path = harness.path
    observer = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        before_bytes = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns
        before_data_version = observer.execute("PRAGMA data_version").fetchone()

        result = SourceHistorySQLiteReader(path).query(_query())

        assert isinstance(result, SourceHistoryMatched)
        assert path.read_bytes() == before_bytes
        assert path.stat().st_mtime_ns == before_mtime
        assert observer.execute("PRAGMA data_version").fetchone() == before_data_version
        assert not Path(f"{path}-journal").exists()
        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()
    finally:
        observer.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode proof")
def test_production_reader_queries_a_filesystem_read_only_database(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    harness.path.chmod(0o444)

    result = SourceHistorySQLiteReader(harness.path).query(_query())

    assert isinstance(result, SourceHistoryMatched)


def test_production_reader_interrupts_a_slow_scan_and_releases_its_snapshot(tmp_path: Path) -> None:
    harness = SourceHistorySQLiteHarness.create(tmp_path / "deadline.sqlite3")
    connection = sqlite3.connect(harness.path)
    try:
        connection.executemany(
            "INSERT INTO source_history_generations(generation, retained, complete) VALUES (?, 1, 1)",
            ((generation,) for generation in range(1, 20_001)),
        )
        connection.commit()
    finally:
        connection.close()
    reader = SourceHistorySQLiteReader(harness.path)

    started = time.monotonic()
    with pytest.raises(SourceHistoryReadDeadlineExceeded):
        reader.query(
            _query(first_generation=1, last_generation=20_000),
            deadline=time.monotonic() + 0.001,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    harness.register_generation(20_001)
    recovered = reader.query(_query(first_generation=20_001, last_generation=20_001))
    assert isinstance(recovered, SourceHistoryNotFound)


def test_production_reader_busy_wait_obeys_the_caller_deadline(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    blocker = sqlite3.connect(harness.path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        with pytest.raises(SourceHistoryReadDeadlineExceeded):
            SourceHistorySQLiteReader(harness.path).query(
                _query(),
                deadline=time.monotonic() + 0.02,
            )
    finally:
        blocker.rollback()
        blocker.close()

    assert time.monotonic() - started < 0.08


def test_real_file_pragmas_and_complete_empty_range(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, 2, 3)

    connection = sqlite3.connect(harness.path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA synchronous").fetchone() == (2,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        connection.close()

    result = harness.query(_query(first_generation=1, last_generation=3, hint=2))
    assert isinstance(result, SourceHistoryNotFound)
    assert result.oldest_retained_generation == 1
    assert result.newest_known_generation == 3

    snapshot = connect_existing(harness.path, begin_read=True)
    try:
        assert snapshot.in_transaction is True
    finally:
        snapshot.close()


def test_all_four_query_conclusions_are_deterministic_after_restart(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, 2)

    accepted_revision = harness.record_accepted(_accepted("accepted"), generation=1)
    dispatch_accepted = harness.record_accepted(_accepted("dispatch"), generation=1)
    dispatch_revision = harness.record_dispatch_intent(
        run_id="run-1",
        operation_id="dispatch",
        expected_head_journal_revision=dispatch_accepted,
        generation=2,
        durable_dispatch_intent_ref="dispatch-ref",
    )
    result_accepted = harness.record_accepted(_accepted("result"), generation=1)
    result_dispatch = harness.record_dispatch_intent(
        run_id="run-1",
        operation_id="result",
        expected_head_journal_revision=result_accepted,
        generation=2,
        durable_dispatch_intent_ref="result-dispatch-ref",
    )
    result_revision = harness.record_observed_result(
        run_id="run-1",
        operation_id="result",
        expected_head_journal_revision=result_dispatch,
        generation=2,
        result_ref="result-ref",
        result_hash=HASH_D,
    )
    failure_accepted = harness.record_accepted(_accepted("failure"), generation=1)
    failure_dispatch = harness.record_dispatch_intent(
        run_id="run-1",
        operation_id="failure",
        expected_head_journal_revision=failure_accepted,
        generation=2,
        durable_dispatch_intent_ref="failure-dispatch-ref",
    )
    failure_revision = harness.record_observed_failure(
        run_id="run-1",
        operation_id="failure",
        expected_head_journal_revision=failure_dispatch,
        generation=2,
        failure_ref="failure-ref",
        failure_hash=HASH_D,
    )

    restarted = SourceHistorySQLiteHarness(harness.path)
    expected = {
        "accepted": ("accepted_no_dispatch", accepted_revision),
        "dispatch": ("dispatch_not_observed", dispatch_revision),
        "result": ("observed_result", result_revision),
        "failure": ("observed_failure", failure_revision),
    }
    for operation_id, (conclusion, revision) in expected.items():
        query_result = restarted.query(_query(operation_id, first_generation=1, last_generation=2, hint=2))
        assert isinstance(query_result, SourceHistoryMatched)
        assert query_result.facts[0].conclusion == conclusion
        assert query_result.facts[0].head_journal_revision == revision


def test_generation_hint_and_main_revisions_are_correlation_only(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, 2, 3)
    harness.record_accepted(_accepted(), generation=1)

    first = harness.query(
        _query(
            first_generation=1,
            last_generation=3,
            hint=1,
            expected_ledger_revision=4,
            expected_reconciliation_revision=2,
        )
    )
    wrong_hint = harness.query(
        _query(
            first_generation=1,
            last_generation=3,
            hint=3,
            expected_ledger_revision=99,
            expected_reconciliation_revision=77,
        )
    )

    assert isinstance(first, SourceHistoryMatched)
    assert isinstance(wrong_hint, SourceHistoryMatched)
    assert first.facts == wrong_hint.facts
    assert wrong_hint.accepted_generation_hint == 3
    assert wrong_hint.expected_source_operation_ledger_revision == 99
    assert harness.read_event_count() == 1


def test_all_authorizations_selector_uses_the_same_authoritative_history(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    values = _query().model_dump()
    values["authorization_selector"] = {"kind": "all"}

    result = harness.query(SourceHistoryQueryV1(**values))

    assert isinstance(result, SourceHistoryMatched)
    assert tuple(fact.dispatch_authorization_ordinal for fact in result.facts) == (1,)


@pytest.mark.parametrize(
    ("query_updates", "expected_reason"),
    [
        ({"idempotency_key": "different-key"}, "idempotency_key_mismatch"),
        ({"operation_id": "different-operation", "idempotency_key": "key-operation-1"}, "operation_id_mismatch"),
        ({"request_hash": HASH_D}, "request_hash_mismatch"),
        ({"attempt_no": 2}, "attempt_no_mismatch"),
        ({"operation_kind": "cards"}, "operation_kind_mismatch"),
        ({"run_id": "run-2"}, "run_id_mismatch"),
    ],
)
def test_positive_identity_collisions_are_not_not_found(
    tmp_path: Path,
    query_updates: dict[str, object],
    expected_reason: str,
) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    values = {
        "operation_id": "operation-1",
        "run_id": "run-1",
        "idempotency_key": "key-operation-1",
        "operation_kind": "search",
        "request_hash": HASH_A,
        "attempt_no": 1,
        **query_updates,
    }

    result = harness.query(_query(**values))  # type: ignore[arg-type]

    assert isinstance(result, SourceHistoryIdentityConflict)
    assert expected_reason in result.conflict_reasons


def test_acceptance_rejects_cross_run_operation_and_key_collision(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(run_id="run-1"), generation=1)

    with pytest.raises(JournalWriteConflict, match="acceptance_identity_conflict"):
        harness.record_accepted(_accepted(run_id="run-2"), generation=1)

    result = harness.query(_query(run_id="run-1"))
    assert isinstance(result, SourceHistoryMatched)
    assert result.facts[0].run_id == "run-1"


def test_incomplete_future_and_truncated_ranges_fail_closed(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, 2, 3)

    future = harness.query(_query(first_generation=1, last_generation=4))
    assert isinstance(future, SourceHistoryUnavailable)
    assert future.reason == "unknown_generation"
    assert future.newest_known_generation == 3

    harness.set_generation_fixture(2, retained=False, complete=True)
    gap = harness.query(_query(first_generation=1, last_generation=3))
    assert isinstance(gap, SourceHistoryUnavailable)
    assert gap.reason == "retention_gap"

    harness.set_generation_fixture(2, retained=True, complete=False)
    truncated = harness.query(_query(first_generation=1, last_generation=3))
    assert isinstance(truncated, SourceHistoryUnavailable)
    assert truncated.reason == "truncated"


def test_corrupt_unreadable_and_schema_mismatch_use_real_file_path(tmp_path: Path) -> None:
    request = _query()
    missing = SourceHistorySQLiteHarness(tmp_path / "missing.sqlite3").query(request)
    assert isinstance(missing, SourceHistoryUnavailable)
    assert missing.reason == "unreadable"

    corrupt_path = tmp_path / "corrupt.sqlite3"
    corrupt_path.write_bytes(b"not-a-sqlite-database")
    corrupt = SourceHistorySQLiteHarness(corrupt_path).query(request)
    assert isinstance(corrupt, SourceHistoryUnavailable)
    assert corrupt.reason == "corrupt"

    schema_harness = SourceHistorySQLiteHarness.create(tmp_path / "schema.sqlite3")
    connection = sqlite3.connect(schema_harness.path)
    try:
        connection.execute("PRAGMA user_version=2")
    finally:
        connection.close()
    schema = schema_harness.query(request)
    assert isinstance(schema, SourceHistoryUnavailable)
    assert schema.reason == "schema_mismatch"


def test_existing_wal_is_rejected_without_mode_conversion_or_database_write(tmp_path: Path) -> None:
    path = tmp_path / "wal.sqlite3"
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker(value) VALUES ('preserve-me')")
        connection.commit()
    finally:
        connection.close()
    before = path.read_bytes()

    result = SourceHistorySQLiteHarness(path).query(_query())

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "pragma_mismatch"
    assert path.read_bytes() == before
    verify = sqlite3.connect(path)
    try:
        assert verify.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert verify.execute("SELECT value FROM marker").fetchone() == ("preserve-me",)
    finally:
        verify.close()


def test_hot_delete_journal_is_recovered_after_hard_process_exit(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sqlite3, sys; "
                "connection = sqlite3.connect(sys.argv[1]); "
                "connection.execute('PRAGMA cache_size=1'); "
                "connection.execute('BEGIN IMMEDIATE'); "
                "connection.execute(\"UPDATE source_history_heads SET accepted_requirement_revision_id = 'crashed'\"); "
                "connection.execute('UPDATE source_history_state SET last_journal_revision = 2'); "
                "os._exit(0)"
            ),
            str(harness.path),
        ],
        check=True,
    )
    hot_journal = Path(f"{harness.path}-journal")
    assert hot_journal.is_file()
    assert hot_journal.stat().st_size > 0

    unavailable = harness.query(_query())

    assert isinstance(unavailable, SourceHistoryUnavailable)
    assert unavailable.reason == "unreadable"
    assert hot_journal.is_file()

    recovered_snapshot = connect_existing(harness.path, begin_read=True)
    recovered_snapshot.close()
    recovered = harness.query(_query())
    assert isinstance(recovered, SourceHistoryMatched)
    assert recovered.facts[0].accepted_requirement_revision_id == "requirement-1"
    assert not hot_journal.exists()
    assert isinstance(harness.query(_query()), SourceHistoryMatched)


@pytest.mark.parametrize("fault_point", ["after_event_insert", "after_head_cas"])
def test_precommit_dispatch_fault_keeps_previous_complete_head(
    tmp_path: Path,
    fault_point: str,
) -> None:
    harness = _harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)

    with pytest.raises(InjectedJournalFault):
        harness.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=1,
            durable_dispatch_intent_ref="dispatch-ref",
            fault_point=fault_point,  # type: ignore[arg-type]
        )

    restarted = SourceHistorySQLiteHarness(harness.path)
    result = restarted.query(_query())
    assert isinstance(result, SourceHistoryMatched)
    assert result.facts[0].conclusion == "accepted_no_dispatch"
    assert restarted.read_event_count() == 1


def test_commit_ack_loss_exposes_new_complete_head_and_exact_replay(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)

    with pytest.raises(CommitAcknowledgementLost):
        harness.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=1,
            durable_dispatch_intent_ref="dispatch-ref",
            fault_point="after_commit",
        )

    restarted = SourceHistorySQLiteHarness(harness.path)
    result = restarted.query(_query())
    assert isinstance(result, SourceHistoryMatched)
    assert result.facts[0].conclusion == "dispatch_not_observed"
    committed_revision = result.facts[0].head_journal_revision
    assert restarted.read_event_count() == 2
    assert (
        restarted.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=1,
            durable_dispatch_intent_ref="dispatch-ref",
        )
        == committed_revision
    )
    assert restarted.read_event_count() == 2


def test_stale_cas_and_event_mutation_fail_without_partial_visibility(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)

    with pytest.raises(JournalWriteConflict, match="stale_head_revision"):
        harness.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision + 1,
            generation=1,
            durable_dispatch_intent_ref="dispatch-ref",
        )
    assert harness.read_event_count() == 1

    connection = sqlite3.connect(harness.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="source_history_events_immutable"):
            connection.execute(
                "UPDATE source_history_events SET phase = 'dispatch_intent' WHERE journal_revision = ?",
                (accepted_revision,),
            )
    finally:
        connection.close()
    result = harness.query(_query())
    assert isinstance(result, SourceHistoryMatched)
    assert result.facts[0].conclusion == "accepted_no_dispatch"


def test_event_replace_is_rejected_and_original_row_survives(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    connection = sqlite3.connect(harness.path)
    try:
        row = connection.execute(
            "SELECT * FROM source_history_events WHERE journal_revision = ?",
            (accepted_revision,),
        ).fetchone()
        assert row is not None
        replacement = list(row)
        replacement[2] = "dispatch_intent"
        placeholders = ", ".join("?" for _ in replacement)
        with pytest.raises(sqlite3.IntegrityError, match="source_history_events_immutable"):
            connection.execute(
                f"INSERT OR REPLACE INTO source_history_events VALUES ({placeholders})",
                replacement,
            )
    finally:
        connection.close()

    result = harness.query(_query())
    assert isinstance(result, SourceHistoryMatched)
    assert result.facts[0].conclusion == "accepted_no_dispatch"


def test_orphan_event_and_schema_drift_cannot_become_not_found(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    connection = sqlite3.connect(harness.path)
    try:
        connection.execute("DELETE FROM source_history_heads")
        connection.commit()
    finally:
        connection.close()

    orphaned = harness.query(_query())
    assert isinstance(orphaned, SourceHistoryUnavailable)
    assert orphaned.reason == "corrupt"

    clean = SourceHistorySQLiteHarness.create(tmp_path / "schema-drift.sqlite3")
    clean.register_generation(1)
    connection = sqlite3.connect(clean.path)
    try:
        connection.execute("DROP TRIGGER source_history_events_no_update")
        connection.execute(
            """
            CREATE TRIGGER source_history_events_no_update
            BEFORE UPDATE ON source_history_events
            BEGIN SELECT 1; END
            """
        )
        connection.commit()
    finally:
        connection.close()
    drifted = clean.query(_query())
    assert isinstance(drifted, SourceHistoryUnavailable)
    assert drifted.reason == "schema_mismatch"


def test_malformed_typed_head_state_fails_closed_as_corrupt(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    connection = sqlite3.connect(harness.path)
    try:
        connection.execute("UPDATE source_history_heads SET accepted_journal_revision = 'not-an-integer'")
        connection.commit()
    finally:
        connection.close()

    result = harness.query(_query())

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "corrupt"
    with pytest.raises(JournalUnavailable, match="corrupt"):
        harness.record_accepted(_accepted("operation-new"), generation=1)
    connection = sqlite3.connect(harness.path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_history_events").fetchone() == (1,)
    finally:
        connection.close()


def test_dangling_generation_foreign_keys_fail_closed_as_corrupt(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, 2)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    harness.record_dispatch_intent(
        run_id="run-1",
        operation_id="operation-1",
        expected_head_journal_revision=accepted_revision,
        generation=2,
        durable_dispatch_intent_ref="dispatch-ref",
    )
    connection = sqlite3.connect(harness.path)
    try:
        connection.execute("DELETE FROM source_history_generations WHERE generation = 2")
        connection.commit()
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    finally:
        connection.close()

    result = harness.query(_query(first_generation=1, last_generation=1))

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "corrupt"
    with pytest.raises(JournalUnavailable, match="corrupt"):
        harness.record_accepted(_accepted("operation-new"), generation=1)
    connection = sqlite3.connect(harness.path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_history_events").fetchone() == (2,)
    finally:
        connection.close()


@pytest.mark.parametrize("phase", ["dispatch_intent", "observed_result"])
def test_partial_transition_event_payload_fails_closed_as_corrupt(
    tmp_path: Path,
    phase: str,
) -> None:
    harness = _harness(tmp_path, 1, 2, 3)
    accepted_revision = harness.record_accepted(_accepted(), generation=1)
    if phase == "observed_result":
        harness.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=2,
            durable_dispatch_intent_ref="dispatch-ref",
        )

    connection = sqlite3.connect(harness.path)
    connection.row_factory = sqlite3.Row
    try:
        source_phase = "accepted" if phase == "dispatch_intent" else "dispatch_intent"
        source = connection.execute(
            "SELECT * FROM source_history_events WHERE phase = ?",
            (source_phase,),
        ).fetchone()
        assert source is not None
        columns = tuple(source.keys())
        event = dict(source)
        revision = 2 if phase == "dispatch_intent" else 3
        generation = revision
        event.update(
            journal_revision=revision,
            event_generation=generation,
            phase=phase,
            durable_dispatch_intent_ref="dispatch-ref",
        )
        if phase == "observed_result":
            event.update(observation_ref="result-ref", observation_hash=HASH_D)
        connection.execute(
            f"INSERT INTO source_history_events({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(event[column] for column in columns),
        )
        if phase == "dispatch_intent":
            connection.execute(
                """
                UPDATE source_history_heads
                SET phase = 'dispatch_intent', head_generation = 2, head_journal_revision = 2,
                    durable_dispatch_intent_ref = 'dispatch-ref',
                    dispatch_intent_generation = 2, dispatch_intent_journal_revision = 2
                """
            )
        else:
            connection.execute(
                """
                UPDATE source_history_heads
                SET phase = 'observed_result', head_generation = 3, head_journal_revision = 3,
                    observation_generation = 3, observation_journal_revision = 3,
                    observation_ref = 'result-ref', observation_hash = ?
                """,
                (HASH_D,),
            )
        connection.execute(
            "UPDATE source_history_state SET last_journal_revision = ? WHERE singleton = 1",
            (revision,),
        )
        connection.commit()
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        connection.close()

    result = harness.query(_query(first_generation=1, last_generation=generation))

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "corrupt"


@pytest.mark.parametrize(
    ("revision", "accepted_requirement_revision_id"),
    [(2, ""), (3, "requirement-1")],
    ids=["contract-invalid", "journal-revision-gap"],
)
def test_invalid_persisted_fact_fails_closed_before_identity_result(
    tmp_path: Path,
    revision: int,
    accepted_requirement_revision_id: str,
) -> None:
    harness = _harness(tmp_path, 1)
    harness.record_accepted(_accepted(), generation=1)
    connection = sqlite3.connect(harness.path)
    connection.row_factory = sqlite3.Row
    try:
        event_source = connection.execute("SELECT * FROM source_history_events").fetchone()
        head_source = connection.execute("SELECT * FROM source_history_heads").fetchone()
        assert event_source is not None
        assert head_source is not None
        event = dict(event_source)
        event.update(
            journal_revision=revision,
            operation_id="operation-invalid",
            idempotency_key="key-operation-invalid",
            accepted_requirement_revision_id=accepted_requirement_revision_id,
            accepted_journal_revision=revision,
        )
        head = dict(head_source)
        head.update(
            operation_id="operation-invalid",
            idempotency_key="key-operation-invalid",
            accepted_requirement_revision_id=accepted_requirement_revision_id,
            accepted_journal_revision=revision,
            head_journal_revision=revision,
        )
        event_columns = tuple(event_source.keys())
        head_columns = tuple(head_source.keys())
        connection.execute(
            f"INSERT INTO source_history_events({', '.join(event_columns)}) "
            f"VALUES ({', '.join('?' for _ in event_columns)})",
            tuple(event[column] for column in event_columns),
        )
        connection.execute(
            f"INSERT INTO source_history_heads({', '.join(head_columns)}) "
            f"VALUES ({', '.join('?' for _ in head_columns)})",
            tuple(head[column] for column in head_columns),
        )
        connection.execute(
            "UPDATE source_history_state SET last_journal_revision = ? WHERE singleton = 1",
            (revision,),
        )
        connection.commit()
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        connection.close()

    result = harness.query(
        _query(
            operation_id="operation-invalid",
            idempotency_key="key-operation-invalid",
        )
    )

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "corrupt"
    with pytest.raises(JournalUnavailable, match="corrupt"):
        harness.record_accepted(_accepted("operation-new"), generation=1)
    connection = sqlite3.connect(harness.path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_history_events").fetchone() == (2,)
    finally:
        connection.close()


def test_phase_replays_require_original_predecessor_and_return_phase_revision(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, 2)
    accepted = _accepted()
    accepted_revision = harness.record_accepted(accepted, generation=1)
    dispatch_revision = harness.record_dispatch_intent(
        run_id="run-1",
        operation_id="operation-1",
        expected_head_journal_revision=accepted_revision,
        generation=1,
        durable_dispatch_intent_ref="dispatch-ref",
    )
    observation_revision = harness.record_observed_result(
        run_id="run-1",
        operation_id="operation-1",
        expected_head_journal_revision=dispatch_revision,
        generation=1,
        result_ref="result-ref",
        result_hash=HASH_D,
    )

    assert harness.record_accepted(accepted, generation=1) == accepted_revision
    with pytest.raises(JournalWriteConflict, match="acceptance_replay_conflict"):
        harness.record_accepted(accepted, generation=2)
    assert (
        harness.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=1,
            durable_dispatch_intent_ref="dispatch-ref",
        )
        == dispatch_revision
    )
    with pytest.raises(JournalWriteConflict, match="dispatch_replay_conflict"):
        harness.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=999,
            generation=1,
            durable_dispatch_intent_ref="dispatch-ref",
        )
    with pytest.raises(JournalWriteConflict, match="dispatch_replay_conflict"):
        harness.record_dispatch_intent(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=2,
            durable_dispatch_intent_ref="dispatch-ref",
        )
    assert (
        harness.record_observed_result(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=dispatch_revision,
            generation=1,
            result_ref="result-ref",
            result_hash=HASH_D,
        )
        == observation_revision
    )
    with pytest.raises(JournalWriteConflict, match="observation_replay_conflict"):
        harness.record_observed_result(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=999,
            generation=1,
            result_ref="result-ref",
            result_hash=HASH_D,
        )
    with pytest.raises(JournalWriteConflict, match="observation_replay_conflict"):
        harness.record_observed_result(
            run_id="run-1",
            operation_id="operation-1",
            expected_head_journal_revision=dispatch_revision,
            generation=2,
            result_ref="result-ref",
            result_hash=HASH_D,
        )


def test_sparse_maximum_generation_range_fails_without_materializing_the_span(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1, JSON_SAFE_INTEGER)

    result = harness.query(_query(first_generation=1, last_generation=JSON_SAFE_INTEGER))

    assert isinstance(result, SourceHistoryUnavailable)
    assert result.reason == "retention_gap"


def test_revision_allocator_rejects_exhaustion_without_mutating_integer_state() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE source_history_state (
            singleton INTEGER PRIMARY KEY,
            last_journal_revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO source_history_state(singleton, last_journal_revision) VALUES (1, ?)",
        (SQLITE_MAX_INTEGER,),
    )

    with pytest.raises(JournalWriteConflict, match="source_history_revision_exhausted"):
        allocate_revision(connection)

    stored = connection.execute("SELECT last_journal_revision FROM source_history_state WHERE singleton = 1").fetchone()
    connection.close()
    assert stored == (SQLITE_MAX_INTEGER,)
    assert isinstance(stored[0], int)


def test_transaction_closes_connection_when_begin_immediate_fails() -> None:
    class FailingConnection:
        def __init__(self) -> None:
            self.closed = False

        def execute(self, statement: str) -> None:
            assert statement == "BEGIN IMMEDIATE"
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            self.closed = True

    connection = FailingConnection()

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        Transaction(connection).__enter__()  # type: ignore[arg-type]

    assert connection.closed is True


def test_concurrent_writers_never_misclassify_a_healthy_journal_as_corrupt(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    start = threading.Barrier(2)

    def write_operations(worker: int) -> None:
        start.wait()
        for index in range(20):
            operation_id = f"operation-{worker}-{index}"
            for _ in range(30):
                try:
                    harness.record_accepted(
                        _accepted(operation_id, run_id=f"run-{worker}"),
                        generation=1,
                    )
                    break
                except JournalUnavailable as exc:
                    if exc.reason != "busy":
                        raise
                except JournalWriteConflict as exc:
                    if str(exc) != "source_history_write_busy":
                        raise
            else:
                raise AssertionError("source_history_concurrent_writer_retry_exhausted")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_operations, worker) for worker in range(2)]
        for future in futures:
            future.result()

    assert harness.read_event_count() == 40


def test_storage_split_is_one_way_and_tests_only() -> None:
    support = Path(__file__).parent / "support"
    storage = (support / "source_history_sqlite_storage.py").read_text(encoding="utf-8")
    harness = (support / "source_history_sqlite_harness.py").read_text(encoding="utf-8")

    assert "source_history_sqlite_harness" not in storage
    assert "from tests.support.source_history_sqlite_storage import" in harness


def test_schema_contains_no_authority_retry_or_business_payload_columns(tmp_path: Path) -> None:
    harness = _harness(tmp_path, 1)
    columns = set(harness.schema_columns())
    forbidden = {
        "authenticated",
        "trusted",
        "verified",
        "authority_valid",
        "retryable",
        "safe_to_retry",
        "retry_posture",
        "product_outcome",
        "main_commit_ref",
        "raw_runtime_token",
        "query",
        "jd",
        "resume",
        "candidate",
        "payload",
        "metadata",
        "url",
        "cookie",
        "html",
        "stdout",
        "stderr",
        "path",
    }
    assert columns.isdisjoint(forbidden)
