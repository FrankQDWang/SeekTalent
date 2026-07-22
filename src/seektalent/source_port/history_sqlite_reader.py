"""Read-only SQLite adapter for authenticated Source Port history queries."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import stat
import time
from collections.abc import Callable

from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ExactAuthorizationSelector,
    HistoryUnavailableReason,
    IdentityConflictReason,
    MatchedHistoryFact,
    ObservedFailureFact,
    ObservedResultFact,
    SQLITE_MAX_INTEGER,
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryResultV1,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)


QUERY_RESULT_CONTRACT_VERSION = "seektalent.source-port.query.result/v1"
SCHEMA_VERSION = 1


class HistorySQLiteUnavailable(RuntimeError):
    def __init__(self, reason: HistoryUnavailableReason) -> None:
        self.reason = reason
        super().__init__(reason)


class SourceHistoryReadDeadlineExceeded(TimeoutError):
    """The synchronous SQLite read exhausted its caller-owned deadline."""


class SourceHistorySQLiteReader:
    """Query one explicit existing history database without mutating it."""

    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("history database path must be a Path")
        if not path.is_absolute():
            raise ValueError("history database path must be absolute")
        self.path = path

    def query(
        self,
        request: SourceHistoryQueryV1,
        *,
        deadline: float | None = None,
    ) -> SourceHistoryQueryResultV1:
        if not isinstance(request, SourceHistoryQueryV1):
            raise TypeError("history query must be a SourceHistoryQueryV1")
        normalized_deadline = _validated_deadline(deadline)
        _require_deadline(normalized_deadline)
        try:
            connection = _connect_read_only(self.path, deadline=normalized_deadline)
        except HistorySQLiteUnavailable as exc:
            return _unavailable(request, exc.reason)

        try:
            oldest_retained, newest_known = generation_bounds(connection)
            try:
                all_rows, facts_by_key = load_validated_history_facts(
                    connection,
                    check_deadline=lambda: _require_deadline(normalized_deadline),
                )
            except HistorySQLiteUnavailable as exc:
                return _unavailable(request, exc.reason)
            except (IndexError, TypeError, ValueError, OverflowError):
                return _unavailable(request, "corrupt")
            rows: list[sqlite3.Row] = []
            for index, row in enumerate(all_rows):
                if index % 64 == 0:
                    _require_deadline(normalized_deadline)
                if (
                    request.searched_first_generation
                    <= int(row["accepted_generation"])
                    <= request.searched_last_generation
                ):
                    rows.append(row)
            exact_rows, collision_rows = _partition_rows(request, rows)
            conflict_reasons = _conflict_reasons(request, exact_rows, collision_rows)
            if conflict_reasons:
                return SourceHistoryIdentityConflict.model_validate(
                    {
                        **_query_echo(request),
                        "contract_version": QUERY_RESULT_CONTRACT_VERSION,
                        "outcome": "identity_conflict",
                        "conflict_reasons": conflict_reasons,
                        "oldest_retained_generation": oldest_retained,
                        "newest_known_generation": newest_known,
                    },
                    strict=True,
                )

            unavailable_reason = _coverage_failure(
                connection,
                request=request,
                oldest_retained=oldest_retained,
                newest_known=newest_known,
            )
            if unavailable_reason is not None:
                return _unavailable(
                    request,
                    unavailable_reason,
                    oldest_retained=oldest_retained,
                    newest_known=newest_known,
                )
            if oldest_retained is None or newest_known is None:
                raise AssertionError("source_history_complete_range_without_bounds")

            complete = {
                **_query_echo(request),
                "contract_version": QUERY_RESULT_CONTRACT_VERSION,
                "oldest_retained_generation": oldest_retained,
                "newest_known_generation": newest_known,
                "history_complete": True,
                "history_truncated": False,
            }
            if not exact_rows:
                return SourceHistoryNotFound.model_validate({**complete, "outcome": "not_found"}, strict=True)
            facts = tuple(facts_by_key[_head_key(row)] for row in exact_rows)
            return SourceHistoryMatched.model_validate(
                {**complete, "outcome": "matched", "facts": facts},
                strict=True,
            )
        except sqlite3.DatabaseError as exc:
            _require_deadline(normalized_deadline)
            return _unavailable(request, read_error(exc).reason)
        finally:
            connection.set_progress_handler(None, 0)
            connection.close()
            _require_deadline(normalized_deadline)


def _unavailable(
    request: SourceHistoryQueryV1,
    reason: HistoryUnavailableReason,
    *,
    oldest_retained: int | None = None,
    newest_known: int | None = None,
) -> SourceHistoryUnavailable:
    return SourceHistoryUnavailable.model_validate(
        {
            **_query_echo(request),
            "contract_version": QUERY_RESULT_CONTRACT_VERSION,
            "outcome": "history_unavailable",
            "reason": reason,
            "oldest_retained_generation": oldest_retained,
            "newest_known_generation": newest_known,
        },
        strict=True,
    )


def _validated_deadline(deadline: float | None) -> float:
    if deadline is None:
        return float("inf")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise TypeError("history query deadline must be monotonic seconds")
    return float(deadline)


def _require_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SourceHistoryReadDeadlineExceeded("source_history_read_deadline_exceeded")


def _remaining_timeout(deadline: float, maximum: float) -> float:
    if deadline == float("inf"):
        return maximum
    return max(0.0, min(maximum, deadline - time.monotonic()))


def _connect_read_only(path: Path, *, deadline: float) -> sqlite3.Connection:
    probe_existing_database(path)
    _require_deadline(deadline)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=_remaining_timeout(deadline, 0.1),
        )
        _require_deadline(deadline)
        connection.row_factory = sqlite3.Row
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        connection.execute("PRAGMA query_only=ON")
        busy_timeout_ms = int(_remaining_timeout(deadline, 0.1) * 1000)
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        verify_connection_pragmas(connection, read_only=True)
        connection.execute("BEGIN")
        verify_schema(connection, check_deadline=lambda: _require_deadline(deadline))
        return connection
    except HistorySQLiteUnavailable:
        if connection is not None:
            connection.set_progress_handler(None, 0)
            connection.close()
        raise
    except sqlite3.DatabaseError as exc:
        if connection is not None:
            connection.set_progress_handler(None, 0)
            connection.close()
        _require_deadline(deadline)
        raise read_error(exc) from exc


def probe_existing_database(path: Path) -> None:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise HistorySQLiteUnavailable("unreadable")
        with path.open("rb") as database:
            header = database.read(100)
    except HistorySQLiteUnavailable:
        raise
    except OSError as exc:
        raise HistorySQLiteUnavailable("unreadable") from exc
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        raise HistorySQLiteUnavailable("corrupt")
    write_version, read_version = header[18], header[19]
    if (write_version, read_version) == (2, 2):
        raise HistorySQLiteUnavailable("pragma_mismatch")
    if (write_version, read_version) != (1, 1):
        raise HistorySQLiteUnavailable("corrupt")


def read_error(error: sqlite3.DatabaseError) -> HistorySQLiteUnavailable:
    message = str(error).lower()
    if "locked" in message or "busy" in message:
        return HistorySQLiteUnavailable("busy")
    if "unable to open" in message or "readonly" in message:
        return HistorySQLiteUnavailable("unreadable")
    return HistorySQLiteUnavailable("corrupt")


def verify_connection_pragmas(connection: sqlite3.Connection, *, read_only: bool = False) -> None:
    if scalar_text(connection, "PRAGMA journal_mode").lower() != "delete":
        raise HistorySQLiteUnavailable("pragma_mismatch")
    if scalar_integer(connection, "PRAGMA synchronous") != 2:
        raise HistorySQLiteUnavailable("pragma_mismatch")
    if scalar_integer(connection, "PRAGMA foreign_keys") != 1:
        raise HistorySQLiteUnavailable("pragma_mismatch")
    if read_only and scalar_integer(connection, "PRAGMA query_only") != 1:
        raise HistorySQLiteUnavailable("pragma_mismatch")


def verify_schema(
    connection: sqlite3.Connection,
    *,
    check_deadline: Callable[[], None] = lambda: None,
) -> None:
    check_deadline()
    if scalar_integer(connection, "PRAGMA user_version") != SCHEMA_VERSION:
        raise HistorySQLiteUnavailable("schema_mismatch")
    if scalar_text(connection, "PRAGMA quick_check") != "ok":
        raise HistorySQLiteUnavailable("corrupt")
    tables = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if tables != {
        "source_history_state",
        "source_history_generations",
        "source_history_events",
        "source_history_heads",
    }:
        raise HistorySQLiteUnavailable("schema_mismatch")
    expected_columns = {
        "source_history_state": ("singleton", "last_journal_revision"),
        "source_history_generations": ("generation", "retained", "complete"),
        "source_history_events": _EVENT_COLUMN_NAMES,
        "source_history_heads": _HEAD_COLUMN_NAMES,
    }
    for table, expected in expected_columns.items():
        actual = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall())
        if actual != expected:
            raise HistorySQLiteUnavailable("schema_mismatch")
    triggers = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    }
    if triggers != {
        "source_history_events_no_duplicate_revision",
        "source_history_events_no_update",
        "source_history_events_no_delete",
    }:
        raise HistorySQLiteUnavailable("schema_mismatch")
    _verify_schema_sql(connection)
    _verify_foreign_keys(connection)
    try:
        _verify_journal_consistency(connection, check_deadline=check_deadline)
    except HistorySQLiteUnavailable:
        raise
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        raise HistorySQLiteUnavailable("corrupt") from exc


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
            raise HistorySQLiteUnavailable("schema_mismatch")


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
            raise HistorySQLiteUnavailable("schema_mismatch")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise HistorySQLiteUnavailable("corrupt")


def _verify_journal_consistency(
    connection: sqlite3.Connection,
    *,
    check_deadline: Callable[[], None],
) -> None:
    check_deadline()
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
        raise HistorySQLiteUnavailable("corrupt")
    if any(
        int(event["journal_revision"]) != expected_revision for expected_revision, event in enumerate(events, start=1)
    ):
        raise HistorySQLiteUnavailable("corrupt")

    event_groups: dict[tuple[str, str, int], list[sqlite3.Row]] = {}
    for index, event in enumerate(events):
        if index % 64 == 0:
            check_deadline()
        key = (
            str(event["run_id"]),
            str(event["operation_id"]),
            int(event["dispatch_authorization_ordinal"]),
        )
        event_groups.setdefault(key, []).append(event)
    head_by_key = {_head_key(head): head for head in heads}
    if set(event_groups) != set(head_by_key):
        raise HistorySQLiteUnavailable("corrupt")

    expected_phases = {
        "accepted": ("accepted",),
        "dispatch_intent": ("accepted", "dispatch_intent"),
        "observed_result": ("accepted", "dispatch_intent", "observed_result"),
        "observed_failure": ("accepted", "dispatch_intent", "observed_failure"),
    }
    for key, head in head_by_key.items():
        check_deadline()
        grouped = event_groups[key]
        phases = tuple(str(event["phase"]) for event in grouped)
        generations = tuple(int(event["event_generation"]) for event in grouped)
        if phases != expected_phases.get(str(head["phase"])) or generations != tuple(sorted(generations)):
            raise HistorySQLiteUnavailable("corrupt")
        if any(event[column] != head[column] for event in grouped for column in _IMMUTABLE_EVENT_HEAD_COLUMNS):
            raise HistorySQLiteUnavailable("corrupt")

        accepted_event = grouped[0]
        if (
            int(accepted_event["journal_revision"]) != int(head["accepted_journal_revision"])
            or int(accepted_event["event_generation"]) != int(head["accepted_generation"])
            or any(accepted_event[column] is not None for column in _DISPATCH_AND_OBSERVATION_COLUMNS)
        ):
            raise HistorySQLiteUnavailable("corrupt")
        if len(grouped) == 1 and any(head[column] is not None for column in _DISPATCH_AND_OBSERVATION_COLUMNS):
            raise HistorySQLiteUnavailable("corrupt")

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
                raise HistorySQLiteUnavailable("corrupt")
            if any(event[column] != head[column] for event in grouped[1:] for column in _DISPATCH_COLUMNS):
                raise HistorySQLiteUnavailable("corrupt")
        if len(grouped) == 2 and any(head[column] is not None for column in _OBSERVATION_COLUMNS):
            raise HistorySQLiteUnavailable("corrupt")

        if len(grouped) == 3:
            observation_event = grouped[2]
            if (
                int(observation_event["journal_revision"]) != int(head["observation_journal_revision"])
                or int(observation_event["event_generation"]) != int(head["observation_generation"])
                or int(observation_event["observation_journal_revision"])
                != int(observation_event["journal_revision"])
                or int(observation_event["observation_generation"]) != int(observation_event["event_generation"])
                or observation_event["observation_ref"] is None
                or observation_event["observation_hash"] is None
                or observation_event["observation_ref"] != head["observation_ref"]
                or observation_event["observation_hash"] != head["observation_hash"]
            ):
                raise HistorySQLiteUnavailable("corrupt")

        latest = grouped[-1]
        if int(latest["journal_revision"]) != int(head["head_journal_revision"]) or int(
            latest["event_generation"]
        ) != int(head["head_generation"]):
            raise HistorySQLiteUnavailable("corrupt")


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


def _coverage_failure(
    connection: sqlite3.Connection,
    *,
    request: SourceHistoryQueryV1,
    oldest_retained: int | None,
    newest_known: int | None,
) -> HistoryUnavailableReason | None:
    if newest_known is None or request.searched_last_generation > newest_known:
        return "unknown_generation"
    if oldest_retained is None or request.searched_first_generation < oldest_retained:
        return "retention_gap"
    rows = connection.execute(
        """
        SELECT generation, retained, complete
        FROM source_history_generations
        WHERE generation BETWEEN ? AND ?
        ORDER BY generation
        """,
        (request.searched_first_generation, request.searched_last_generation),
    ).fetchall()
    expected_count = request.searched_last_generation - request.searched_first_generation + 1
    if len(rows) != expected_count:
        return "retention_gap"
    previous = request.searched_first_generation - 1
    for row in rows:
        generation = int(row["generation"])
        if generation != previous + 1 or int(row["retained"]) != 1:
            return "retention_gap"
        previous = generation
    if any(int(row["complete"]) != 1 for row in rows):
        return "truncated"
    return None


def _partition_rows(
    request: SourceHistoryQueryV1,
    rows: list[sqlite3.Row],
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    exact: list[sqlite3.Row] = []
    collisions: list[sqlite3.Row] = []
    for row in rows:
        selector = request.authorization_selector
        ordinal_matches = (
            int(row["dispatch_authorization_ordinal"]) == selector.ordinal
            if isinstance(selector, ExactAuthorizationSelector)
            else True
        )
        identity_matches = (
            row["run_id"] == request.run_id
            and row["operation_id"] == request.operation_id
            and row["source"] == request.source
            and row["operation_kind"] == request.operation_kind
            and row["idempotency_key"] == request.idempotency_key
            and row["request_hash"] == request.request_hash
            and int(row["attempt_no"]) == request.attempt_no
        )
        if ordinal_matches and identity_matches:
            exact.append(row)
            continue
        collision = (
            row["run_id"] == request.run_id
            and (row["operation_id"] == request.operation_id or row["idempotency_key"] == request.idempotency_key)
        ) or (row["operation_id"] == request.operation_id and row["idempotency_key"] == request.idempotency_key)
        if collision:
            collisions.append(row)
    return exact, collisions


def _head_key(row: sqlite3.Row) -> tuple[str, str, int]:
    return (
        str(row["run_id"]),
        str(row["operation_id"]),
        int(row["dispatch_authorization_ordinal"]),
    )


def load_validated_history_facts(
    connection: sqlite3.Connection,
    *,
    check_deadline: Callable[[], None] = lambda: None,
) -> tuple[list[sqlite3.Row], dict[tuple[str, str, int], MatchedHistoryFact]]:
    rows = connection.execute(
        """
        SELECT * FROM source_history_heads
        ORDER BY run_id, operation_id, dispatch_authorization_ordinal
        """
    ).fetchall()
    facts: dict[tuple[str, str, int], MatchedHistoryFact] = {}
    try:
        for index, row in enumerate(rows):
            if index % 64 == 0:
                check_deadline()
            facts[_head_key(row)] = _fact_from_row(row)
    except (IndexError, TypeError, ValueError, OverflowError) as exc:
        raise HistorySQLiteUnavailable("corrupt") from exc
    check_deadline()
    return rows, facts


def _conflict_reasons(
    request: SourceHistoryQueryV1,
    exact_rows: list[sqlite3.Row],
    collision_rows: list[sqlite3.Row],
) -> tuple[IdentityConflictReason, ...]:
    reasons: list[IdentityConflictReason] = []
    for row in collision_rows:
        comparisons: tuple[tuple[str, object, IdentityConflictReason], ...] = (
            ("run_id", request.run_id, "run_id_mismatch"),
            ("operation_id", request.operation_id, "operation_id_mismatch"),
            ("source", request.source, "source_mismatch"),
            ("operation_kind", request.operation_kind, "operation_kind_mismatch"),
            ("idempotency_key", request.idempotency_key, "idempotency_key_mismatch"),
            ("request_hash", request.request_hash, "request_hash_mismatch"),
            ("attempt_no", request.attempt_no, "attempt_no_mismatch"),
        )
        for column, expected, reason in comparisons:
            if row[column] != expected and reason not in reasons:
                reasons.append(reason)
    if len(exact_rows) > 1:
        accepted_facts = {
            (
                row["accepted_requirement_revision_id"],
                row["runtime_attempt_fence_ref"],
                row["authorized_dispatch_intent_digest"],
                row["profile_binding_generation"],
            )
            for row in exact_rows
        }
        if len(accepted_facts) > 1:
            reasons.append("accepted_fact_mismatch")
    return tuple(reasons)


def _fact_from_row(row: sqlite3.Row) -> MatchedHistoryFact:
    accepted = _accepted_values_from_row(row)
    common = {
        **accepted,
        "head_generation": int(row["head_generation"]),
        "head_journal_revision": int(row["head_journal_revision"]),
    }
    phase = str(row["phase"])
    if phase == "accepted":
        return AcceptedNoDispatchFact.model_validate(
            {**common, "conclusion": "accepted_no_dispatch"},
            strict=True,
        )
    dispatched = {
        **common,
        "durable_dispatch_intent_ref": row["durable_dispatch_intent_ref"],
        "dispatch_intent_generation": int(row["dispatch_intent_generation"]),
        "dispatch_intent_journal_revision": int(row["dispatch_intent_journal_revision"]),
    }
    if phase == "dispatch_intent":
        return DispatchNotObservedFact.model_validate(
            {**dispatched, "conclusion": "dispatch_not_observed"},
            strict=True,
        )
    observed = {
        **dispatched,
        "observation_generation": int(row["observation_generation"]),
        "observation_journal_revision": int(row["observation_journal_revision"]),
    }
    if phase == "observed_result":
        return ObservedResultFact.model_validate(
            {
                **observed,
                "conclusion": "observed_result",
                "result_ref": row["observation_ref"],
                "result_hash": row["observation_hash"],
            },
            strict=True,
        )
    if phase == "observed_failure":
        return ObservedFailureFact.model_validate(
            {
                **observed,
                "conclusion": "observed_failure",
                "failure_ref": row["observation_ref"],
                "failure_hash": row["observation_hash"],
            },
            strict=True,
        )
    raise ValueError("source_history_unknown_phase")


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


def _query_echo(query: SourceHistoryQueryV1) -> dict[str, object]:
    return query.model_dump(exclude={"contract_version"})


def scalar_integer(connection: sqlite3.Connection, statement: str) -> int:
    row = connection.execute(statement).fetchone()
    if row is None or len(row) != 1 or not isinstance(row[0], int) or isinstance(row[0], bool):
        raise HistorySQLiteUnavailable("schema_mismatch")
    return int(row[0])


def scalar_text(connection: sqlite3.Connection, statement: str) -> str:
    row = connection.execute(statement).fetchone()
    if row is None or len(row) != 1 or not isinstance(row[0], str):
        raise HistorySQLiteUnavailable("schema_mismatch")
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

_IMMUTABLE_EVENT_HEAD_COLUMNS = _HEAD_COLUMN_NAMES[:18]
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
_DISPATCH_AND_OBSERVATION_COLUMNS = (*_DISPATCH_COLUMNS, *_OBSERVATION_COLUMNS)

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

SCHEMA_STATEMENTS = (
    _STATE_TABLE_DDL,
    "INSERT INTO source_history_state(singleton, last_journal_revision) VALUES (1, 0)",
    _GENERATION_TABLE_DDL,
    _EVENT_TABLE_DDL,
    _HEAD_TABLE_DDL,
    *_TRIGGER_DDLS,
)
