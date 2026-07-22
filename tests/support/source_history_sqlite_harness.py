from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal

from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ObservedFailureFact,
    ObservedResultFact,
    OperationKind,
    SQLITE_MAX_INTEGER,
    SourceHistoryQueryResultV1,
    SourceHistoryQueryV1,
)
from seektalent.source_port.history_sqlite_reader import (
    SourceHistorySQLiteReader,
    load_validated_history_facts,
)
from tests.support.source_history_sqlite_storage import (
    JournalUnavailable as _JournalUnavailable,
    JournalWriteConflict,
    Transaction as _Transaction,
    allocate_revision as _allocate_revision,
    connect_existing as _connect_existing,
    create_database as _create_database,
    scalar_integer as _scalar_integer,
    write_error as _write_error,
)


FaultPoint = Literal["after_event_insert", "after_head_cas", "after_commit"]


class InjectedJournalFault(RuntimeError):
    pass


class CommitAcknowledgementLost(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True)
class AcceptedHistoryInput:
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


class SourceHistorySQLiteHarness:
    """Deterministic real-file producer used only by contract tests."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, path: Path) -> SourceHistorySQLiteHarness:
        _create_database(path)
        return cls(path)

    def register_generation(
        self,
        generation: int,
        *,
        retained: bool = True,
        complete: bool = True,
    ) -> None:
        _require_positive_integer(generation, "generation")
        _require_exact_bool(retained, "retained")
        _require_exact_bool(complete, "complete")
        with self._write_transaction() as connection:
            existing = connection.execute(
                "SELECT retained, complete FROM source_history_generations WHERE generation = ?",
                (generation,),
            ).fetchone()
            values = (int(retained), int(complete))
            if existing is not None:
                if tuple(existing) != values:
                    raise JournalWriteConflict("source_history_generation_replay_conflict")
                return
            connection.execute(
                "INSERT INTO source_history_generations(generation, retained, complete) VALUES (?, ?, ?)",
                (generation, *values),
            )

    def set_generation_fixture(
        self,
        generation: int,
        *,
        retained: bool,
        complete: bool,
    ) -> None:
        """Change retained-range facts for a test; this is not compaction logic."""
        _require_positive_integer(generation, "generation")
        _require_exact_bool(retained, "retained")
        _require_exact_bool(complete, "complete")
        with self._write_transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE source_history_generations
                SET retained = ?, complete = ?
                WHERE generation = ?
                """,
                (int(retained), int(complete), generation),
            )
            if cursor.rowcount != 1:
                raise JournalWriteConflict("source_history_generation_missing")

    def record_accepted(
        self,
        accepted: AcceptedHistoryInput,
        *,
        generation: int,
        fault_point: FaultPoint | None = None,
    ) -> int:
        _require_positive_integer(generation, "generation")
        _validate_accepted_input(accepted, generation=generation)
        committed_revision: int | None = None
        try:
            with self._write_transaction() as connection:
                self._require_generation(connection, generation)
                existing = self._find_operation_head(
                    connection,
                    run_id=accepted.run_id,
                    operation_id=accepted.operation_id,
                    ordinal=accepted.dispatch_authorization_ordinal,
                )
                if existing is not None:
                    self._require_accepted_replay(existing, accepted, generation=generation)
                    return int(existing["accepted_journal_revision"])

                self._require_no_identity_collision(connection, accepted)
                revision = _allocate_revision(connection)
                fact = AcceptedNoDispatchFact(
                    **_accepted_fact_values(accepted),
                    conclusion="accepted_no_dispatch",
                    accepted_generation=generation,
                    accepted_journal_revision=revision,
                    head_generation=generation,
                    head_journal_revision=revision,
                )
                connection.execute(
                    _ACCEPTED_EVENT_INSERT,
                    _accepted_event_parameters(fact),
                )
                self._inject(fault_point, "after_event_insert")
                connection.execute(
                    _ACCEPTED_HEAD_INSERT,
                    _accepted_head_parameters(fact),
                )
                self._inject(fault_point, "after_head_cas")
                committed_revision = revision
        except sqlite3.OperationalError as exc:
            raise _write_error(exc) from exc
        if fault_point == "after_commit":
            raise CommitAcknowledgementLost("source_history_commit_acknowledgement_lost")
        if committed_revision is None:
            raise AssertionError("source_history_missing_committed_revision")
        return committed_revision

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
        _require_positive_integer(expected_head_journal_revision, "expected_head_journal_revision")
        _require_positive_integer(generation, "generation")
        committed_revision: int | None = None
        try:
            with self._write_transaction() as connection:
                self._require_generation(connection, generation)
                head = self._require_head(connection, run_id=run_id, operation_id=operation_id)
                if head["phase"] in {"dispatch_intent", "observed_result", "observed_failure"}:
                    if (
                        int(head["accepted_journal_revision"]) != expected_head_journal_revision
                        or int(head["dispatch_intent_generation"]) != generation
                        or head["durable_dispatch_intent_ref"] != durable_dispatch_intent_ref
                    ):
                        raise JournalWriteConflict("source_history_dispatch_replay_conflict")
                    return int(head["dispatch_intent_journal_revision"])
                if int(head["head_journal_revision"]) != expected_head_journal_revision:
                    raise JournalWriteConflict("source_history_stale_head_revision")

                revision = _allocate_revision(connection)
                fact = DispatchNotObservedFact(
                    **_accepted_values_from_row(head),
                    conclusion="dispatch_not_observed",
                    head_generation=generation,
                    head_journal_revision=revision,
                    durable_dispatch_intent_ref=durable_dispatch_intent_ref,
                    dispatch_intent_generation=generation,
                    dispatch_intent_journal_revision=revision,
                )
                connection.execute(
                    _TRANSITION_EVENT_INSERT,
                    _transition_event_parameters(fact, phase="dispatch_intent"),
                )
                self._inject(fault_point, "after_event_insert")
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
                    raise JournalWriteConflict("source_history_head_cas_failed")
                self._inject(fault_point, "after_head_cas")
                committed_revision = revision
        except sqlite3.OperationalError as exc:
            raise _write_error(exc) from exc
        if fault_point == "after_commit":
            raise CommitAcknowledgementLost("source_history_commit_acknowledgement_lost")
        if committed_revision is None:
            raise AssertionError("source_history_missing_committed_revision")
        return committed_revision

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
        return self._record_observation(
            run_id=run_id,
            operation_id=operation_id,
            expected_head_journal_revision=expected_head_journal_revision,
            generation=generation,
            observation_kind="observed_result",
            observation_ref=result_ref,
            observation_hash=result_hash,
            fault_point=fault_point,
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
        return self._record_observation(
            run_id=run_id,
            operation_id=operation_id,
            expected_head_journal_revision=expected_head_journal_revision,
            generation=generation,
            observation_kind="observed_failure",
            observation_ref=failure_ref,
            observation_hash=failure_hash,
            fault_point=fault_point,
        )

    def query(self, request: SourceHistoryQueryV1) -> SourceHistoryQueryResultV1:
        return SourceHistorySQLiteReader(self.path).query(request)

    def read_event_count(self) -> int:
        connection = self._connect_existing()
        try:
            return int(_scalar_integer(connection, "SELECT COUNT(*) FROM source_history_events"))
        finally:
            connection.close()

    def schema_columns(self) -> tuple[str, ...]:
        connection = self._connect_existing()
        try:
            rows = connection.execute("PRAGMA table_info(source_history_heads)").fetchall()
            return tuple(str(row[1]) for row in rows)
        finally:
            connection.close()

    def _record_observation(
        self,
        *,
        run_id: str,
        operation_id: str,
        expected_head_journal_revision: int,
        generation: int,
        observation_kind: Literal["observed_result", "observed_failure"],
        observation_ref: str,
        observation_hash: str,
        fault_point: FaultPoint | None,
    ) -> int:
        _require_positive_integer(expected_head_journal_revision, "expected_head_journal_revision")
        _require_positive_integer(generation, "generation")
        committed_revision: int | None = None
        try:
            with self._write_transaction() as connection:
                self._require_generation(connection, generation)
                head = self._require_head(connection, run_id=run_id, operation_id=operation_id)
                if head["phase"] in {"observed_result", "observed_failure"}:
                    if (
                        int(head["dispatch_intent_journal_revision"]) != expected_head_journal_revision
                        or int(head["observation_generation"]) != generation
                        or head["phase"] != observation_kind
                        or head["observation_ref"] != observation_ref
                        or head["observation_hash"] != observation_hash
                    ):
                        raise JournalWriteConflict("source_history_observation_replay_conflict")
                    return int(head["observation_journal_revision"])
                if head["phase"] != "dispatch_intent":
                    raise JournalWriteConflict("source_history_observation_without_dispatch")
                if int(head["head_journal_revision"]) != expected_head_journal_revision:
                    raise JournalWriteConflict("source_history_stale_head_revision")

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
                    fact: ObservedResultFact | ObservedFailureFact = ObservedResultFact(
                        **common,
                        conclusion="observed_result",
                        result_ref=observation_ref,
                        result_hash=observation_hash,
                    )
                else:
                    fact = ObservedFailureFact(
                        **common,
                        conclusion="observed_failure",
                        failure_ref=observation_ref,
                        failure_hash=observation_hash,
                    )
                connection.execute(
                    _TRANSITION_EVENT_INSERT,
                    _transition_event_parameters(fact, phase=observation_kind),
                )
                self._inject(fault_point, "after_event_insert")
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
                    raise JournalWriteConflict("source_history_head_cas_failed")
                self._inject(fault_point, "after_head_cas")
                committed_revision = revision
        except sqlite3.OperationalError as exc:
            raise _write_error(exc) from exc
        if fault_point == "after_commit":
            raise CommitAcknowledgementLost("source_history_commit_acknowledgement_lost")
        if committed_revision is None:
            raise AssertionError("source_history_missing_committed_revision")
        return committed_revision

    def _connect_existing(self, *, begin_read: bool = False) -> sqlite3.Connection:
        connection = _connect_existing(self.path, begin_read=True)
        try:
            load_validated_history_facts(connection)
        except _JournalUnavailable:
            connection.close()
            raise
        if not begin_read:
            connection.rollback()
        return connection

    def _write_transaction(self) -> _Transaction:
        return _Transaction(self._connect_existing())

    @staticmethod
    def _require_generation(connection: sqlite3.Connection, generation: int) -> None:
        row = connection.execute(
            "SELECT retained, complete FROM source_history_generations WHERE generation = ?",
            (generation,),
        ).fetchone()
        if row is None:
            raise JournalWriteConflict("source_history_generation_missing")
        if tuple(row) != (1, 1):
            raise JournalWriteConflict("source_history_generation_not_writable")

    @staticmethod
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

    def _require_head(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        operation_id: str,
    ) -> sqlite3.Row:
        row = self._find_operation_head(
            connection,
            run_id=run_id,
            operation_id=operation_id,
            ordinal=1,
        )
        if row is None:
            raise JournalWriteConflict("source_history_head_missing")
        return row

    @staticmethod
    def _require_accepted_replay(
        row: sqlite3.Row,
        accepted: AcceptedHistoryInput,
        *,
        generation: int,
    ) -> None:
        expected = _accepted_fact_values(accepted)
        for name, value in expected.items():
            if row[name] != value:
                raise JournalWriteConflict("source_history_acceptance_replay_conflict")
        if int(row["accepted_generation"]) != generation:
            raise JournalWriteConflict("source_history_acceptance_replay_conflict")

    @staticmethod
    def _require_no_identity_collision(
        connection: sqlite3.Connection,
        accepted: AcceptedHistoryInput,
    ) -> None:
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
            raise JournalWriteConflict("source_history_acceptance_identity_conflict")

    @staticmethod
    def _inject(actual: FaultPoint | None, expected: FaultPoint) -> None:
        if actual == expected:
            raise InjectedJournalFault(f"source_history_fault_{expected}")


def _validate_accepted_input(accepted: AcceptedHistoryInput, *, generation: int) -> None:
    AcceptedNoDispatchFact(
        **_accepted_fact_values(accepted),
        conclusion="accepted_no_dispatch",
        accepted_generation=generation,
        accepted_journal_revision=1,
        head_generation=generation,
        head_journal_revision=1,
    )


def _accepted_fact_values(accepted: AcceptedHistoryInput) -> dict[str, object]:
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
        raise ValueError(f"source_history_invalid_{name}")


def _require_exact_bool(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"source_history_invalid_{name}")


_IDENTITY_COLUMNS = """
    run_id, operation_id, source, operation_kind, idempotency_key, request_hash,
    attempt_no, dispatch_authorization_ordinal
"""

_ACCEPTED_EVENT_INSERT = f"""
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

_TRANSITION_EVENT_INSERT = _ACCEPTED_EVENT_INSERT

_ACCEPTED_HEAD_INSERT = f"""
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
