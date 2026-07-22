from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator, Literal
from unittest.mock import patch

import seektalent.source_port._command_journal_engine as journal_engine
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    CommandJournal,
    CommandJournalConflict,
    CommandJournalError,
    CommandJournalSession,
    create_command_journal,
)
from seektalent.source_port.history_contract import SourceHistoryQueryResultV1, SourceHistoryQueryV1
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader


FaultPoint = Literal["after_event_insert", "after_head_cas", "after_commit"]
AcceptedHistoryInput = AcceptedCommand
JournalWriteConflict = CommandJournalConflict
JournalUnavailable = CommandJournalError


class InjectedJournalFault(RuntimeError):
    pass


class CommitAcknowledgementLost(RuntimeError):
    pass


class SourceHistorySQLiteHarness:
    """Tests-only fixture and fault seam over the production command journal."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._journal: CommandJournal | None = None
        self._sessions: dict[int, CommandJournalSession] = {}

    @classmethod
    def create(cls, path: Path) -> SourceHistorySQLiteHarness:
        harness = cls(path)
        harness._journal = create_command_journal(path)
        return harness

    def register_generation(
        self,
        generation: int,
        *,
        retained: bool = True,
        complete: bool = True,
    ) -> None:
        session = self._require_journal().start()
        if session.generation != generation:
            raise AssertionError("source_history_fixture_generation_not_monotonic")
        self._sessions[generation] = session
        if retained is not True or complete is not True:
            self.set_generation_fixture(generation, retained=retained, complete=complete)

    def set_generation_fixture(
        self,
        generation: int,
        *,
        retained: bool,
        complete: bool,
    ) -> None:
        """Change coverage facts for a test; this is not product retention logic."""
        connection = sqlite3.connect(self.path, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE source_history_generations
                SET retained = ?, complete = ?
                WHERE generation = ?
                """,
                (int(retained), int(complete), generation),
            )
            if cursor.rowcount != 1:
                raise AssertionError("source_history_fixture_generation_missing")
            connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def record_accepted(
        self,
        accepted: AcceptedHistoryInput,
        *,
        generation: int,
        fault_point: FaultPoint | None = None,
    ) -> int:
        with self._fault(fault_point):
            return self._session(generation).record_accepted(accepted)

    def record_dispatch_intent(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        generation: int,
        durable_dispatch_intent_ref: str,
        fault_point: FaultPoint | None = None,
    ) -> int:
        with self._fault(fault_point):
            return self._session(generation).record_dispatch_intent(
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
        generation: int,
        result_ref: str,
        result_hash: str,
        fault_point: FaultPoint | None = None,
    ) -> int:
        with self._fault(fault_point):
            return self._session(generation).record_observed_result(
                run_id=run_id,
                operation_id=operation_id,
                expected_head_journal_revision=expected_head_journal_revision,
                result_ref=result_ref,
                result_hash=result_hash,
            )

    def record_observed_failure(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        generation: int,
        failure_ref: str,
        failure_hash: str,
        fault_point: FaultPoint | None = None,
    ) -> int:
        with self._fault(fault_point):
            return self._session(generation).record_observed_failure(
                run_id=run_id,
                operation_id=operation_id,
                expected_head_journal_revision=expected_head_journal_revision,
                failure_ref=failure_ref,
                failure_hash=failure_hash,
            )

    def query(self, request: SourceHistoryQueryV1) -> SourceHistoryQueryResultV1:
        return SourceHistorySQLiteReader(self.path).query(request)

    def read_event_count(self) -> int:
        connection = sqlite3.connect(self.path)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM source_history_events").fetchone()[0])
        finally:
            connection.close()

    def schema_columns(self) -> tuple[str, ...]:
        connection = sqlite3.connect(self.path)
        try:
            rows = connection.execute("PRAGMA table_info(source_history_heads)").fetchall()
            return tuple(str(row[1]) for row in rows)
        finally:
            connection.close()

    def _require_journal(self) -> CommandJournal:
        if self._journal is None:
            raise AssertionError("source_history_fixture_writer_not_created")
        return self._journal

    def _session(self, generation: int) -> CommandJournalSession:
        session = self._sessions.get(generation)
        if session is None:
            raise AssertionError("source_history_fixture_generation_not_started")
        return session

    @contextmanager
    def _fault(self, fault_point: FaultPoint | None) -> Iterator[None]:
        if fault_point is None:
            yield
            return
        if fault_point == "after_commit":
            with patch.object(
                journal_engine,
                "_transition_commit_acknowledged",
                side_effect=CommitAcknowledgementLost("source_history_commit_acknowledgement_lost"),
            ):
                yield
            return

        def checkpoint(actual: str) -> None:
            if actual == fault_point:
                raise InjectedJournalFault(f"source_history_fault_{actual}")

        with patch.object(journal_engine, "_transition_checkpoint", side_effect=checkpoint):
            yield
