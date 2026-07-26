"""Atomic SQLite admission for authenticated safe-retry authorization epochs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import math
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import ValidationError

from seektalent.source_port import _command_journal_engine as journal_engine
from seektalent.source_port._command_journal_types import (
    CommandJournalConflict,
    CommandJournalError,
    CommandJournalErrorReason,
)
from seektalent.source_port.authenticated_verify_session_frames import VerifySessionAcceptedAckV1
from seektalent.source_port.history_contract import AcceptedNoDispatchFact
from seektalent.source_port.history_sqlite_reader import (
    HistorySQLiteUnavailable,
    load_validated_history_facts,
    scalar_integer,
    verify_schema,
)
from seektalent.source_port.operation_dispatch import (
    dispatch_authorization_digest,
    validate_dispatch_authorization,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestV1,
    validate_verify_session_durable_reply_identity,
    verify_session_request_echo,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes


class SafeRetryContinuityRejectReason(StrEnum):
    DEADLINE_EXPIRED = "deadline_expired"
    ORDINAL_GAP = "continuity_ordinal_gap"
    IDENTITY_CONFLICT = "continuity_identity_conflict"
    ATTEMPT_NOT_INCREASING = "continuity_attempt_not_increasing"
    REVISION_NOT_INCREASING = "continuity_revision_not_increasing"
    AUTHORIZATION_CONFLICT = "continuity_authorization_conflict"
    SAFE_RETRY_REF_REUSED = "continuity_safe_retry_ref_reused"
    PRIOR_STATE_NOT_RETRYABLE = "continuity_prior_state_not_retryable"
    HISTORY_INCOMPLETE = "continuity_history_incomplete"
    REPLAY_CONFLICT = "continuity_replay_conflict"
    JOURNAL_BUSY = "journal_busy"
    JOURNAL_CORRUPT = "journal_corrupt"
    JOURNAL_SCHEMA_MISMATCH = "journal_schema_mismatch"
    JOURNAL_UNAVAILABLE = "journal_unavailable"


class SafeRetryContinuityRejected(RuntimeError):
    def __init__(self, reason: SafeRetryContinuityRejectReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class _SafeRetryContinuityStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SafeRetryContinuityStoreResult:
    disposition: Literal["created", "exact_replay"]
    accepted_generation: int
    accepted_journal_revision: int
    accepted_ack_bytes: bytes
    accepted_ack_hash: str
    accepted_ack_ref: str


MonotonicClock = Callable[[], float]


def _admit_safe_retry_continuity(
    *,
    path: Path,
    generation: int,
    instance_id: str,
    request: VerifySessionRequestV1,
    arrival_deadline_at: float | None,
    monotonic_clock: MonotonicClock,
) -> SafeRetryContinuityStoreResult:
    validated = _validated_request(request)
    connection: sqlite3.Connection | None = None
    created = False
    try:
        connection = _open_connection(path)
        connection.execute("BEGIN IMMEDIATE")
        verify_schema(connection)
        rows, _ = load_validated_history_facts(connection)
        try:
            journal_engine._require_session_generation(
                connection,
                generation=generation,
                instance_id=instance_id,
            )
        except CommandJournalConflict:
            raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE) from None
        _require_complete_generation_coverage(connection)
        result = _admit_in_transaction(
            connection,
            rows=rows,
            generation=generation,
            request=validated,
            arrival_deadline_at=arrival_deadline_at,
            monotonic_clock=monotonic_clock,
        )
        created = result.disposition == "created"
    except SafeRetryContinuityRejected:
        _rollback(connection)
        raise
    except HistorySQLiteUnavailable as exc:
        _rollback(connection)
        raise SafeRetryContinuityRejected(_history_reason(exc)) from None
    except CommandJournalError as exc:
        _rollback(connection)
        raise SafeRetryContinuityRejected(_journal_reason(exc.reason)) from None
    except sqlite3.Error as exc:
        _rollback(connection)
        error = journal_engine._sqlite_error(exc)
        raise SafeRetryContinuityRejected(_journal_reason(error.reason)) from None
    except RuntimeError:
        _rollback(connection)
        raise _SafeRetryContinuityStoreError from None
    else:
        _commit_continuity_transaction(connection)
    finally:
        if connection is not None:
            connection.close()
    if created:
        try:
            _continuity_commit_acknowledged()
        except RuntimeError:
            raise _SafeRetryContinuityStoreError from None
    return result


def _admit_in_transaction(
    connection: sqlite3.Connection,
    *,
    rows: list[sqlite3.Row],
    generation: int,
    request: VerifySessionRequestV1,
    arrival_deadline_at: float | None,
    monotonic_clock: MonotonicClock,
) -> SafeRetryContinuityStoreResult:
    identity = request.identity
    authorization = request.delivery.authorization
    if authorization.dispatch_authorization_ordinal <= 1 or authorization.safe_retry_commit_ref is None:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.AUTHORIZATION_CONFLICT)
    try:
        validate_dispatch_authorization(identity, authorization)
        if authorization.dispatch_intent_digest != dispatch_authorization_digest(authorization):
            raise ValueError("authorization digest mismatch")
        _accepted_fact(
            request,
            generation=generation,
            revision=1,
        )
    except (TypeError, ValueError, ValidationError):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.AUTHORIZATION_CONFLICT) from None

    existing = journal_engine._find_operation_head(
        connection,
        run_id=identity.run_id,
        operation_id=identity.operation_id,
        ordinal=authorization.dispatch_authorization_ordinal,
    )
    operation_rows = [
        row for row in rows if row["run_id"] == identity.run_id and row["operation_id"] == identity.operation_id
    ]
    if existing is not None:
        _latest_retryable_epoch(operation_rows)
        return _exact_replay(existing, request)
    if request.delivery.delivery_mode == "outbox_redelivery":
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.REPLAY_CONFLICT)

    if not operation_rows:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.IDENTITY_CONFLICT)
    if _has_identity_collision(rows, request) or any(
        not _stable_identity_matches(row, request) for row in operation_rows
    ):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.IDENTITY_CONFLICT)

    latest = _latest_retryable_epoch(operation_rows)
    if authorization.dispatch_authorization_ordinal != int(latest["dispatch_authorization_ordinal"]) + 1:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.ORDINAL_GAP)
    if identity.attempt_no <= int(latest["attempt_no"]):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.ATTEMPT_NOT_INCREASING)
    if (
        authorization.dispatch_intent_revision <= int(latest["authorized_dispatch_intent_revision"])
        or identity.expected_source_operation_ledger_revision
        <= int(latest["expected_source_operation_ledger_revision"])
        or identity.expected_reconciliation_revision <= int(latest["expected_reconciliation_revision"])
    ):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.REVISION_NOT_INCREASING)
    if identity.profile_binding_generation < int(latest["profile_binding_generation"]):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.AUTHORIZATION_CONFLICT)
    if any(row["safe_retry_commit_ref"] == authorization.safe_retry_commit_ref for row in operation_rows):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.SAFE_RETRY_REF_REUSED)

    _require_unexpired(arrival_deadline_at, monotonic_clock)
    revision = journal_engine._allocate_revision(connection)
    _continuity_checkpoint("after_revision_allocate")
    fact = _accepted_fact(
        request,
        generation=generation,
        revision=revision,
    )
    ack_bytes = _canonical_accepted_ack_bytes(
        request,
        generation=generation,
        revision=revision,
    )
    connection.execute(
        journal_engine._EVENT_INSERT,
        journal_engine._accepted_event_parameters(
            fact,
            accepted_ack_bytes=ack_bytes,
        ),
    )
    _continuity_checkpoint("after_event_insert")
    connection.execute(
        journal_engine._HEAD_INSERT,
        journal_engine._accepted_head_parameters(
            fact,
            accepted_ack_bytes=ack_bytes,
        ),
    )
    _continuity_checkpoint("after_head_insert")
    _require_unexpired(arrival_deadline_at, monotonic_clock)
    _continuity_checkpoint("before_commit")
    return _result(
        disposition="created",
        generation=generation,
        revision=revision,
        ack_bytes=ack_bytes,
    )


def _latest_retryable_epoch(operation_rows: list[sqlite3.Row]) -> sqlite3.Row:
    if not operation_rows:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE)
    operation_rows.sort(key=lambda row: int(row["dispatch_authorization_ordinal"]))
    ordinals = tuple(int(row["dispatch_authorization_ordinal"]) for row in operation_rows)
    if ordinals != tuple(range(1, len(operation_rows) + 1)):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE)
    if any(row["phase"] != "accepted" for row in operation_rows):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.PRIOR_STATE_NOT_RETRYABLE)
    for row in operation_rows:
        _validated_ack_for_row(row)
    return operation_rows[-1]


def _exact_replay(
    row: sqlite3.Row,
    request: VerifySessionRequestV1,
) -> SafeRetryContinuityStoreResult:
    if row["phase"] != "accepted":
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.PRIOR_STATE_NOT_RETRYABLE)
    expected = _accepted_values(request)
    ignored = (
        {"runtime_attempt_fence_ref", "browser_control_scope_id", "controller_fence_ref"}
        if request.delivery.delivery_mode == "outbox_redelivery"
        else set()
    )
    if any(row[name] != value for name, value in expected.items() if name not in ignored):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.REPLAY_CONFLICT)
    ack_bytes = row["accepted_ack_bytes"]
    if type(ack_bytes) is not bytes:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_CORRUPT)
    ack = _validated_ack_for_row(row)
    if ack.accepted_fact != "accepted_no_dispatch" or ack.dispatch_authorization != request.delivery.authorization:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_CORRUPT)
    try:
        validate_verify_session_durable_reply_identity(
            verify_session_request_echo(request),
            ack.identity,
        )
        if (
            request.delivery.delivery_mode == "outbox_redelivery"
            and request.identity.deadline.value > ack.identity.deadline.value
        ):
            raise ValueError("redelivery deadline increased")
    except (TypeError, ValueError, ValidationError):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.REPLAY_CONFLICT) from None
    return _result(
        disposition="exact_replay",
        generation=int(row["accepted_generation"]),
        revision=int(row["accepted_journal_revision"]),
        ack_bytes=ack_bytes,
    )


def _accepted_fact(
    request: VerifySessionRequestV1,
    *,
    generation: int,
    revision: int,
) -> AcceptedNoDispatchFact:
    return AcceptedNoDispatchFact.model_validate(
        {
            **_accepted_values(request),
            "conclusion": "accepted_no_dispatch",
            "accepted_generation": generation,
            "accepted_journal_revision": revision,
            "head_generation": generation,
            "head_journal_revision": revision,
        },
        strict=True,
    )


def _accepted_values(request: VerifySessionRequestV1) -> dict[str, object]:
    identity = request.identity
    authorization = request.delivery.authorization
    return {
        "run_id": identity.run_id,
        "operation_id": identity.operation_id,
        "source": identity.source,
        "operation_kind": identity.operation_kind,
        "idempotency_key": identity.idempotency_key,
        "request_hash": identity.request_hash,
        "attempt_no": identity.attempt_no,
        "accepted_requirement_revision_id": identity.accepted_requirement_revision_id,
        "runtime_attempt_fence_ref": identity.runtime_attempt_fence_ref,
        "dispatch_authorization_ordinal": authorization.dispatch_authorization_ordinal,
        "safe_retry_commit_ref": authorization.safe_retry_commit_ref,
        "expected_source_operation_ledger_revision": (identity.expected_source_operation_ledger_revision),
        "expected_reconciliation_revision": identity.expected_reconciliation_revision,
        "authorized_dispatch_intent_id": authorization.dispatch_intent_id,
        "authorized_dispatch_intent_revision": authorization.dispatch_intent_revision,
        "authorized_dispatch_intent_digest": authorization.dispatch_intent_digest,
        "profile_binding_generation": identity.profile_binding_generation,
        "browser_control_scope_id": identity.browser_control_scope_id,
        "controller_fence_ref": None,
    }


def _canonical_accepted_ack_bytes(
    request: VerifySessionRequestV1,
    *,
    generation: int,
    revision: int,
) -> bytes:
    ack = VerifySessionAcceptedAckV1.model_validate(
        {
            "contract_version": "seektalent.source.verify-session.accepted-ack/v1",
            "identity": request.identity,
            "dispatch_authorization": request.delivery.authorization,
            "accepted_generation": generation,
            "accepted_journal_revision": revision,
            "accepted_fact": "accepted_no_dispatch",
        },
        strict=True,
    )
    return canonical_json_bytes(ack.model_dump(mode="json"))


def _validated_ack_for_row(row: sqlite3.Row) -> VerifySessionAcceptedAckV1:
    ack_bytes = row["accepted_ack_bytes"]
    if type(ack_bytes) is not bytes:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_CORRUPT)
    try:
        ack = VerifySessionAcceptedAckV1.model_validate_json(ack_bytes, strict=True)
        if canonical_json_bytes(ack.model_dump(mode="json")) != ack_bytes:
            raise ValueError("noncanonical durable ack")
    except (TypeError, ValueError, ValidationError):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_CORRUPT) from None

    identity = ack.identity
    authorization = ack.dispatch_authorization
    identity_values = {
        "run_id": identity.run_id,
        "operation_id": identity.operation_id,
        "source": identity.source,
        "operation_kind": identity.operation_kind,
        "idempotency_key": identity.idempotency_key,
        "request_hash": identity.request_hash,
        "attempt_no": identity.attempt_no,
        "accepted_requirement_revision_id": identity.accepted_requirement_revision_id,
        "runtime_attempt_fence_ref": identity.runtime_attempt_fence_ref,
        "expected_source_operation_ledger_revision": (identity.expected_source_operation_ledger_revision),
        "expected_reconciliation_revision": identity.expected_reconciliation_revision,
        "profile_binding_generation": identity.profile_binding_generation,
        "browser_control_scope_id": identity.browser_control_scope_id,
    }
    authorization_values = {
        "run_id": authorization.run_id,
        "operation_id": authorization.operation_id,
        "attempt_no": authorization.attempt_no,
        "request_hash": authorization.request_hash,
        "dispatch_authorization_ordinal": authorization.dispatch_authorization_ordinal,
        "safe_retry_commit_ref": authorization.safe_retry_commit_ref,
        "expected_source_operation_ledger_revision": (authorization.expected_source_operation_ledger_revision),
        "expected_reconciliation_revision": (authorization.expected_reconciliation_revision),
        "authorized_dispatch_intent_id": authorization.dispatch_intent_id,
        "authorized_dispatch_intent_revision": authorization.dispatch_intent_revision,
        "authorized_dispatch_intent_digest": authorization.dispatch_intent_digest,
    }
    if (
        any(row[name] != value for name, value in identity_values.items())
        or any(row[name] != value for name, value in authorization_values.items())
        or ack.accepted_generation != row["accepted_generation"]
        or ack.accepted_journal_revision != row["accepted_journal_revision"]
    ):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_CORRUPT)
    if authorization.dispatch_authorization_ordinal > 1 and ack.accepted_fact != "accepted_no_dispatch":
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_CORRUPT)
    return ack


def _stable_identity_matches(row: sqlite3.Row, request: VerifySessionRequestV1) -> bool:
    identity = request.identity
    return (
        row["run_id"] == identity.run_id
        and row["operation_id"] == identity.operation_id
        and row["source"] == identity.source
        and row["operation_kind"] == identity.operation_kind
        and row["idempotency_key"] == identity.idempotency_key
        and row["request_hash"] == identity.request_hash
        and row["accepted_requirement_revision_id"] == identity.accepted_requirement_revision_id
    )


def _has_identity_collision(
    rows: list[sqlite3.Row],
    request: VerifySessionRequestV1,
) -> bool:
    identity = request.identity
    for row in rows:
        collision = (
            row["run_id"] == identity.run_id
            and (row["operation_id"] == identity.operation_id or row["idempotency_key"] == identity.idempotency_key)
        ) or (row["operation_id"] == identity.operation_id and row["idempotency_key"] == identity.idempotency_key)
        if collision and not _stable_identity_matches(row, request):
            return True
    return False


def _require_complete_generation_coverage(connection: sqlite3.Connection) -> None:
    last_generation = scalar_integer(
        connection,
        "SELECT last_sidecar_generation FROM source_history_state WHERE singleton = 1",
    )
    generations = connection.execute(
        """
        SELECT generation, retained, complete
        FROM source_history_generations
        ORDER BY generation
        """
    ).fetchall()
    if last_generation < 1:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE)
    expected = tuple(range(1, last_generation + 1))
    actual = tuple(int(row["generation"]) for row in generations)
    if actual != expected or any(int(row["retained"]) != 1 or int(row["complete"]) != 1 for row in generations):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE)


def _require_unexpired(
    deadline_at: float | None,
    monotonic_clock: MonotonicClock,
) -> None:
    if deadline_at is None:
        return
    try:
        now = monotonic_clock()
    except (ArithmeticError, RuntimeError, TypeError, ValueError):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_UNAVAILABLE) from None
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_UNAVAILABLE)
    if not math.isfinite(float(now)):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.JOURNAL_UNAVAILABLE)
    if float(now) >= deadline_at:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.DEADLINE_EXPIRED)


def _validated_request(request: VerifySessionRequestV1) -> VerifySessionRequestV1:
    if type(request) is not VerifySessionRequestV1:
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.AUTHORIZATION_CONFLICT)
    try:
        return VerifySessionRequestV1.model_validate(
            request.model_dump(mode="python", warnings="error"),
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        raise SafeRetryContinuityRejected(SafeRetryContinuityRejectReason.AUTHORIZATION_CONFLICT) from None


def _result(
    *,
    disposition: Literal["created", "exact_replay"],
    generation: int,
    revision: int,
    ack_bytes: bytes,
) -> SafeRetryContinuityStoreResult:
    digest = sha256(ack_bytes).hexdigest()
    return SafeRetryContinuityStoreResult(
        disposition=disposition,
        accepted_generation=generation,
        accepted_journal_revision=revision,
        accepted_ack_bytes=ack_bytes,
        accepted_ack_hash=digest,
        accepted_ack_ref=f"sha256:{digest}",
    )


def _history_reason(error: HistorySQLiteUnavailable) -> SafeRetryContinuityRejectReason:
    return {
        "busy": SafeRetryContinuityRejectReason.JOURNAL_BUSY,
        "corrupt": SafeRetryContinuityRejectReason.JOURNAL_CORRUPT,
        "pragma_mismatch": SafeRetryContinuityRejectReason.JOURNAL_SCHEMA_MISMATCH,
        "schema_mismatch": SafeRetryContinuityRejectReason.JOURNAL_SCHEMA_MISMATCH,
        "unreadable": SafeRetryContinuityRejectReason.JOURNAL_UNAVAILABLE,
        "unknown_generation": SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE,
        "retention_gap": SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE,
        "truncated": SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE,
    }[error.reason]


def _journal_reason(reason: CommandJournalErrorReason) -> SafeRetryContinuityRejectReason:
    if reason is CommandJournalErrorReason.BUSY:
        return SafeRetryContinuityRejectReason.JOURNAL_BUSY
    if reason is CommandJournalErrorReason.CORRUPT:
        return SafeRetryContinuityRejectReason.JOURNAL_CORRUPT
    if reason in {
        CommandJournalErrorReason.PRAGMA_MISMATCH,
        CommandJournalErrorReason.SCHEMA_MISMATCH,
    }:
        return SafeRetryContinuityRejectReason.JOURNAL_SCHEMA_MISMATCH
    return SafeRetryContinuityRejectReason.JOURNAL_UNAVAILABLE


def _open_connection(path: Path) -> sqlite3.Connection:
    return journal_engine._open_write_connection(path)


def _rollback(connection: sqlite3.Connection | None) -> None:
    if connection is None or not connection.in_transaction:
        return
    try:
        connection.rollback()
    except sqlite3.Error:
        raise _SafeRetryContinuityStoreError from None


def _commit_continuity_transaction(connection: sqlite3.Connection) -> None:
    try:
        connection.commit()
    except sqlite3.Error:
        _rollback(connection)
        raise _SafeRetryContinuityStoreError from None


def _continuity_checkpoint(_: str) -> None:
    return None


def _continuity_commit_acknowledged() -> None:
    return None
