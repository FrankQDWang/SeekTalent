from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import inspect
from pathlib import Path
import sqlite3
import threading
from typing import Literal, get_type_hints
from unittest.mock import patch

import pytest

import seektalent.source_port._safe_retry_continuity_store as continuity_store
from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionAcceptedAck,
    ReceivedVerifySessionRejected,
    ReceivedVerifySessionSubmit,
    VerifySessionAcceptedAckV1,
    VerifySessionFrameError,
)
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    CommandJournalSession,
    create_command_journal,
    open_command_journal,
)
from seektalent.source_port.history_contract import (
    AllAuthorizationsSelector,
    SourceHistoryMatched,
    SourceHistoryQueryV1,
)
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader
from seektalent.source_port.verify_session_continuity_admission import (
    SafeRetryContinuityRejectReason,
    VerifySessionContinuityAdmissionError,
    VerifySessionContinuityAdmissionReason,
    create_verify_session_continuity_admission,
)
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1
from seektalent.source_port.wire_primitives import canonical_json_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTINUITY_MODULE_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "verify_session_continuity_admission.py"
CONTINUITY_STORE_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "_safe_retry_continuity_store.py"
CONTRACT_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "verify_session_contract.py"
RAW_FENCE_ONE = "continuity-runtime-fence-one-" + "x" * 64
RAW_FENCE_TWO = "continuity-runtime-fence-two-" + "y" * 64
RAW_FENCE_REPLAY = "continuity-runtime-fence-replay-" + "z" * 64
MAIN_TO_SIDECAR_KEY = bytes(range(32))
SIDECAR_TO_MAIN_KEY = bytes(range(32, 64))
MUTATION_FAULT_POINTS = (
    "after_revision_allocate",
    "after_event_insert",
    "after_head_insert",
    "before_commit",
)


class InjectedContinuityFault(RuntimeError):
    pass


class CommitAcknowledgementLost(RuntimeError):
    pass


class _DelegatingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


class _CommittedThenRaiseConnection(_DelegatingConnection):
    def commit(self) -> None:
        self._connection.commit()
        assert self._connection.in_transaction is False
        raise sqlite3.OperationalError("injected commit acknowledgement loss")


class _ActiveTransactionCommitFailureConnection(_DelegatingConnection):
    def commit(self) -> None:
        assert self._connection.in_transaction is True
        raise sqlite3.OperationalError("injected commit failure")


def _request(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "run_id": "run-1",
        "operation_id": "verify-session-1",
        "attempt_no": 1,
        "idempotency_key": "verify-session-key-1",
        "correlation_id": "correlation-1",
        "accepted_requirement_revision_id": "requirement-revision-1",
        "runtime_attempt_fence_token": RAW_FENCE_ONE,
        "profile_binding_generation": 1,
        "browser_control_scope_id": "browser-scope-1",
        "deadline_value": 60_000,
        "expected_source_operation_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
        "delivery_mode": "initial",
        "dispatch_intent_id": "dispatch-intent-1",
        "dispatch_intent_revision": 1,
        "source_operation_acceptance_ref": "source-acceptance-1",
        "profile_binding_ref": "profile-binding-1",
        "provider_account_ref": "provider-account-1",
        "required_capabilities": ("bridge", "extension", "profile_lock", "search_surface"),
        "user_interaction_policy": "observe_only",
        "verify_search_surface": True,
        "component_receipt_refs": ("main-receipt-1",),
    }
    values.update(updates)
    return VerifySessionRequestV1.create(**values)


def _safe_retry_request(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "attempt_no": 2,
        "runtime_attempt_fence_token": RAW_FENCE_TWO,
        "profile_binding_generation": 2,
        "browser_control_scope_id": "browser-scope-2",
        "correlation_id": "correlation-2",
        "deadline_value": 45_000,
        "expected_source_operation_ledger_revision": 2,
        "expected_reconciliation_revision": 1,
        "dispatch_intent_id": "dispatch-intent-2",
        "dispatch_intent_revision": 2,
        "dispatch_authorization_ordinal": 2,
        "safe_retry_commit_ref": "safe-retry-commit-2",
    }
    values.update(updates)
    return _request(**values)


def _accepted_command(request: VerifySessionRequestV1) -> AcceptedCommand:
    identity = request.identity
    authorization = request.delivery.authorization
    return AcceptedCommand(
        run_id=identity.run_id,
        operation_id=identity.operation_id,
        source=identity.source,
        operation_kind=identity.operation_kind,
        idempotency_key=identity.idempotency_key,
        request_hash=identity.request_hash,
        attempt_no=identity.attempt_no,
        accepted_requirement_revision_id=identity.accepted_requirement_revision_id,
        runtime_attempt_fence_ref=identity.runtime_attempt_fence_ref,
        authorized_dispatch_intent_id=authorization.dispatch_intent_id,
        authorized_dispatch_intent_revision=authorization.dispatch_intent_revision,
        authorized_dispatch_intent_digest=authorization.dispatch_intent_digest,
        profile_binding_generation=identity.profile_binding_generation,
        browser_control_scope_id=identity.browser_control_scope_id,
    )


def _accepted_ack_bytes(request: VerifySessionRequestV1) -> bytes:
    ack = VerifySessionAcceptedAckV1.model_validate(
        {
            "contract_version": "seektalent.source.verify-session.accepted-ack/v1",
            "identity": request.identity,
            "dispatch_authorization": request.delivery.authorization,
            "accepted_generation": 1,
            "accepted_journal_revision": 1,
            "accepted_fact": "dispatch_authorized",
        },
        strict=True,
    )
    return canonical_json_bytes(ack.model_dump(mode="json"))


def _seed_ordinal_one(
    path: Path,
    *,
    phase: Literal["accepted", "dispatch_intent", "observed_result", "observed_failure"] = "accepted",
    generation_count: int = 2,
) -> tuple[VerifySessionRequestV1, CommandJournalSession]:
    request = _request()
    journal = create_command_journal(path)
    sessions = tuple(journal.start() for _ in range(generation_count))
    accepted = sessions[0].record_accepted(
        _accepted_command(request),
        accepted_ack_bytes=_accepted_ack_bytes(request),
    )
    if phase != "accepted":
        dispatch = sessions[0].record_dispatch_intent(
            run_id=request.identity.run_id,
            operation_id=request.identity.operation_id,
            expected_head_journal_revision=accepted.revision,
            durable_dispatch_intent_ref="ordinal-1-dispatch",
        )
        if phase == "observed_result":
            sessions[0].record_observed_result(
                run_id=request.identity.run_id,
                operation_id=request.identity.operation_id,
                expected_head_journal_revision=dispatch.revision,
                result_ref="result-ref",
                result_hash="a" * 64,
            )
        elif phase == "observed_failure":
            sessions[0].record_observed_failure(
                run_id=request.identity.run_id,
                operation_id=request.identity.operation_id,
                expected_head_journal_revision=dispatch.revision,
                failure_ref="failure-ref",
                failure_hash="b" * 64,
            )
    return request, sessions[-1]


def _main(
    *,
    session_id: str = "continuity-session-1",
) -> PostHandshakeVerifySessionSession:
    return PostHandshakeVerifySessionSession.for_main(
        session_id=session_id,
        protocol_minor=0,
        main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
        sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
    )


def _sidecar(
    *,
    session_id: str = "continuity-session-1",
) -> PostHandshakeVerifySessionSession:
    return PostHandshakeVerifySessionSession.for_sidecar(
        session_id=session_id,
        protocol_minor=0,
        main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
        sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
    )


def _composition(
    session: CommandJournalSession,
    sidecar: PostHandshakeVerifySessionSession,
    *,
    monotonic_clock: object | None = None,
):
    values = {
        "command_journal_session": session,
        "frame_session": sidecar,
    }
    if monotonic_clock is not None:
        values["monotonic_clock"] = monotonic_clock
    return create_verify_session_continuity_admission(**values)


def _submit(
    composition: object,
    main: PostHandshakeVerifySessionSession,
    request: VerifySessionRequestV1,
    *,
    message_id: str = "submit-1",
):
    frame = main.encode_submit(
        message_id=message_id,
        correlation_id=request.identity.correlation_id,
        payload=request,
    )
    return composition.feed(frame)  # type: ignore[attr-defined]


def _database_snapshot(path: Path) -> tuple[object, ...]:
    with sqlite3.connect(path) as connection:
        state = connection.execute(
            """
            SELECT last_journal_revision, last_sidecar_generation
            FROM source_history_state WHERE singleton = 1
            """
        ).fetchone()
        events = tuple(connection.execute("SELECT * FROM source_history_events ORDER BY journal_revision").fetchall())
        heads = tuple(
            connection.execute(
                """
                SELECT * FROM source_history_heads
                ORDER BY run_id, operation_id, dispatch_authorization_ordinal
                """
            ).fetchall()
        )
    return state, events, heads


def _row_counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as connection:
        event_count = int(connection.execute("SELECT COUNT(*) FROM source_history_events").fetchone()[0])
        head_count = int(connection.execute("SELECT COUNT(*) FROM source_history_heads").fetchone()[0])
        last_revision = int(
            connection.execute("SELECT last_journal_revision FROM source_history_state WHERE singleton = 1").fetchone()[
                0
            ]
        )
    return event_count, head_count, last_revision


def _assert_rejected(
    exchange: object,
    main: PostHandshakeVerifySessionSession,
    expected: SafeRetryContinuityRejectReason,
) -> None:
    assert exchange.disposition == "rejected"  # type: ignore[attr-defined]
    assert exchange.rejection_reason is expected  # type: ignore[attr-defined]
    assert exchange.accepted_ack_bytes is None  # type: ignore[attr-defined]
    received = main.feed(exchange.outbound_frames[0])  # type: ignore[attr-defined]
    assert len(received) == 1
    assert isinstance(received[0], ReceivedVerifySessionRejected)
    assert received[0].payload.rejection_reason == expected.value


def _all_history_query(request: VerifySessionRequestV1, *, last_generation: int) -> SourceHistoryQueryV1:
    identity = request.identity
    return SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id=identity.run_id,
        operation_id=identity.operation_id,
        source=identity.source,
        operation_kind=identity.operation_kind,
        idempotency_key=identity.idempotency_key,
        request_hash=identity.request_hash,
        attempt_no=identity.attempt_no,
        authorization_selector=AllAuthorizationsSelector(kind="all"),
        searched_first_generation=1,
        searched_last_generation=last_generation,
        expected_source_operation_ledger_revision=identity.expected_source_operation_ledger_revision,
        expected_reconciliation_revision=identity.expected_reconciliation_revision,
    )


def test_authenticated_ordinal_two_admission_atomically_persists_a_canonical_no_dispatch_ack(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    initial, retry_session = _seed_ordinal_one(path)
    request = _safe_retry_request()
    main = _main()
    sidecar = _sidecar()
    composition = _composition(retry_session, sidecar)

    exchange = _submit(composition, main, request)

    assert request.identity.request_hash == initial.identity.request_hash
    assert exchange.disposition == "created"
    assert exchange.accepted_generation == retry_session.generation == 2
    assert exchange.accepted_journal_revision == 2
    assert exchange.accepted_ack_hash == sha256(exchange.accepted_ack_bytes).hexdigest()
    assert exchange.accepted_ack_ref == f"sha256:{exchange.accepted_ack_hash}"
    assert exchange.rejection_reason is None
    assert len(exchange.outbound_frames) == 1
    received = main.feed(exchange.outbound_frames[0])
    assert len(received) == 1
    assert isinstance(received[0], ReceivedVerifySessionAcceptedAck)
    assert received[0].payload.accepted_fact == "accepted_no_dispatch"
    assert received[0].payload.dispatch_authorization == request.delivery.authorization
    assert received[0].payload.accepted_generation == retry_session.generation
    assert received[0].payload.accepted_journal_revision == 2
    assert canonical_json_bytes(received[0].payload.model_dump(mode="json")) == exchange.accepted_ack_bytes

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        event = connection.execute(
            """
            SELECT * FROM source_history_events
            WHERE dispatch_authorization_ordinal = 2
            """
        ).fetchone()
        head = connection.execute(
            """
            SELECT * FROM source_history_heads
            WHERE dispatch_authorization_ordinal = 2
            """
        ).fetchone()
    assert event is not None and head is not None
    assert event["accepted_ack_bytes"] == head["accepted_ack_bytes"] == exchange.accepted_ack_bytes
    assert event["phase"] == head["phase"] == "accepted"
    assert event["safe_retry_commit_ref"] == "safe-retry-commit-2"
    assert event["expected_source_operation_ledger_revision"] == 2
    assert event["expected_reconciliation_revision"] == 1
    assert event["authorized_dispatch_intent_digest"] == request.delivery.authorization.dispatch_intent_digest
    for field in (
        "durable_dispatch_intent_ref",
        "dispatch_intent_generation",
        "dispatch_intent_journal_revision",
        "observation_generation",
        "observation_journal_revision",
        "observation_ref",
        "observation_hash",
        "terminal_reply_bytes",
    ):
        assert event[field] is None
        assert head[field] is None

    history = SourceHistorySQLiteReader(path).query(_all_history_query(request, last_generation=2))
    assert isinstance(history, SourceHistoryMatched)
    assert tuple(fact.dispatch_authorization_ordinal for fact in history.facts) == (1, 2)
    assert tuple(fact.conclusion for fact in history.facts) == (
        "accepted_no_dispatch",
        "accepted_no_dispatch",
    )


def test_all_continuity_reads_validation_and_mutations_share_one_immediate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    statements: list[str] = []
    real_open = continuity_store._open_connection

    def traced_open(candidate: Path) -> sqlite3.Connection:
        connection = real_open(candidate)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(continuity_store, "_open_connection", traced_open)
    composition = _composition(retry_session, _sidecar())

    exchange = _submit(composition, _main(), _safe_retry_request())

    assert exchange.disposition == "created"
    normalized = [" ".join(statement.split()).upper() for statement in statements]
    begin_indexes = [index for index, statement in enumerate(normalized) if statement == "BEGIN IMMEDIATE"]
    commit_indexes = [index for index, statement in enumerate(normalized) if statement == "COMMIT"]
    assert len(begin_indexes) == len(commit_indexes) == 1
    begin = begin_indexes[0]
    commit = commit_indexes[0]
    continuity_reads = [
        index
        for index, statement in enumerate(normalized)
        if "SOURCE_HISTORY_" in statement and statement.startswith(("SELECT", "PRAGMA"))
    ]
    mutations = [
        index
        for index, statement in enumerate(normalized)
        if statement.startswith(("INSERT INTO SOURCE_HISTORY_", "UPDATE SOURCE_HISTORY_"))
    ]
    assert continuity_reads
    assert mutations
    assert all(begin < index < commit for index in (*continuity_reads, *mutations))


@pytest.mark.parametrize("fault_point", MUTATION_FAULT_POINTS)
def test_every_continuity_mutation_fault_rolls_back_all_ordinal_two_state(
    tmp_path: Path,
    fault_point: str,
) -> None:
    path = tmp_path / f"{fault_point}.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)

    def fail(actual: str) -> None:
        if actual == fault_point:
            raise InjectedContinuityFault(actual)

    with patch.object(continuity_store, "_continuity_checkpoint", side_effect=fail):
        with pytest.raises(VerifySessionContinuityAdmissionError) as failure:
            _submit(_composition(retry_session, _sidecar()), _main(), _safe_retry_request())

    assert failure.value.reason is VerifySessionContinuityAdmissionReason.JOURNAL_ERROR
    assert _database_snapshot(path) == before
    assert _row_counts(path) == (1, 1, 1)


def test_commit_ack_loss_then_restart_exact_replay_returns_the_original_durable_ack(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    initial_composition = _composition(retry_session, _sidecar())

    with patch.object(
        continuity_store,
        "_continuity_commit_acknowledged",
        side_effect=CommitAcknowledgementLost("accepted commit acknowledgement lost"),
    ):
        with pytest.raises(VerifySessionContinuityAdmissionError) as lost:
            _submit(initial_composition, _main(), _safe_retry_request())
    assert lost.value.reason is VerifySessionContinuityAdmissionReason.JOURNAL_ERROR
    assert _row_counts(path) == (2, 2, 2)
    with sqlite3.connect(path) as connection:
        durable_ack = connection.execute(
            """
            SELECT accepted_ack_bytes FROM source_history_heads
            WHERE dispatch_authorization_ordinal = 2
            """
        ).fetchone()[0]
    initial_composition.close()

    replay_session = open_command_journal(path).start()
    replay_main = _main(session_id="continuity-session-restart")
    replay_sidecar = _sidecar(session_id="continuity-session-restart")
    replay_request = _safe_retry_request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token=RAW_FENCE_REPLAY,
        browser_control_scope_id="browser-scope-replay",
        correlation_id="correlation-replay",
        deadline_value=30_000,
    )
    replay = _submit(
        _composition(replay_session, replay_sidecar),
        replay_main,
        replay_request,
        message_id="submit-replay",
    )

    assert replay.disposition == "exact_replay"
    assert replay.accepted_ack_bytes == durable_ack
    assert replay.accepted_ack_hash == sha256(durable_ack).hexdigest()
    assert _row_counts(path) == (2, 2, 2)
    received = replay_main.feed(replay.outbound_frames[0])
    assert len(received) == 1
    assert isinstance(received[0], ReceivedVerifySessionAcceptedAck)
    assert received[0].payload.accepted_fact == "accepted_no_dispatch"
    assert received[0].payload.accepted_generation == 2
    assert received[0].payload.accepted_journal_revision == 2
    assert canonical_json_bytes(received[0].payload.model_dump(mode="json")) == durable_ack
    assert received[0].payload.identity.runtime_attempt_fence_ref != replay_request.identity.runtime_attempt_fence_ref


def test_sqlite_commit_ack_loss_never_emits_rejection_and_replays_the_original_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    real_open = continuity_store._open_connection

    def committed_then_raise(candidate: Path) -> _CommittedThenRaiseConnection:
        return _CommittedThenRaiseConnection(real_open(candidate))

    monkeypatch.setattr(continuity_store, "_open_connection", committed_then_raise)
    initial_composition = _composition(retry_session, _sidecar())

    with pytest.raises(VerifySessionContinuityAdmissionError) as lost:
        _submit(initial_composition, _main(), _safe_retry_request())

    assert lost.value.reason is VerifySessionContinuityAdmissionReason.JOURNAL_ERROR
    assert _row_counts(path) == (2, 2, 2)
    with sqlite3.connect(path) as connection:
        durable = connection.execute(
            """
            SELECT accepted_ack_bytes, durable_dispatch_intent_ref,
                   observation_ref, terminal_reply_bytes
            FROM source_history_heads
            WHERE dispatch_authorization_ordinal = 2
            """
        ).fetchone()
    assert durable is not None
    durable_ack = durable[0]
    assert durable[1:] == (None, None, None)
    initial_composition.close()

    monkeypatch.setattr(continuity_store, "_open_connection", real_open)
    replay_session = open_command_journal(path).start()
    before_replay = _database_snapshot(path)
    replay_main = _main(session_id="continuity-sqlite-commit-restart")
    replay = _submit(
        _composition(
            replay_session,
            _sidecar(session_id="continuity-sqlite-commit-restart"),
        ),
        replay_main,
        _safe_retry_request(
            delivery_mode="outbox_redelivery",
            runtime_attempt_fence_token=RAW_FENCE_REPLAY,
            browser_control_scope_id="browser-scope-replay",
            correlation_id="correlation-replay",
            deadline_value=30_000,
        ),
        message_id="submit-sqlite-commit-replay",
    )

    assert replay.disposition == "exact_replay"
    assert replay.accepted_ack_bytes == durable_ack
    assert replay.accepted_ack_hash == sha256(durable_ack).hexdigest()
    assert _database_snapshot(path) == before_replay
    assert _row_counts(path) == (2, 2, 2)
    received = replay_main.feed(replay.outbound_frames[0])
    assert len(received) == 1
    assert isinstance(received[0], ReceivedVerifySessionAcceptedAck)
    assert received[0].payload.accepted_fact == "accepted_no_dispatch"


def test_sqlite_commit_failure_with_active_transaction_rolls_back_without_a_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    real_open = continuity_store._open_connection

    def fail_active_commit(candidate: Path) -> _ActiveTransactionCommitFailureConnection:
        return _ActiveTransactionCommitFailureConnection(real_open(candidate))

    monkeypatch.setattr(continuity_store, "_open_connection", fail_active_commit)

    with pytest.raises(VerifySessionContinuityAdmissionError) as failure:
        _submit(_composition(retry_session, _sidecar()), _main(), _safe_retry_request())

    assert failure.value.reason is VerifySessionContinuityAdmissionReason.JOURNAL_ERROR
    assert _database_snapshot(path) == before
    assert _row_counts(path) == (1, 1, 1)


def test_ordinal_two_dispatch_authorized_ack_is_journal_corrupt_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    created = _submit(
        _composition(retry_session, _sidecar()),
        _main(),
        _safe_retry_request(),
    )
    assert created.disposition == "created"

    with sqlite3.connect(path) as connection:
        current_ack = connection.execute(
            """
            SELECT accepted_ack_bytes FROM source_history_heads
            WHERE dispatch_authorization_ordinal = 2
            """
        ).fetchone()[0]
        parsed_ack = VerifySessionAcceptedAckV1.model_validate_json(current_ack, strict=True)
        tampered_ack = canonical_json_bytes(
            parsed_ack.model_copy(update={"accepted_fact": "dispatch_authorized"}).model_dump(mode="json")
        )
        event_trigger = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger' AND name = 'source_history_events_no_update'
            """
        ).fetchone()[0]
        connection.execute("DROP TRIGGER source_history_events_no_update")
        connection.execute(
            """
            UPDATE source_history_events SET accepted_ack_bytes = ?
            WHERE dispatch_authorization_ordinal = 2
            """,
            (tampered_ack,),
        )
        connection.execute(
            """
            UPDATE source_history_heads SET accepted_ack_bytes = ?
            WHERE dispatch_authorization_ordinal = 2
            """,
            (tampered_ack,),
        )
        connection.execute(event_trigger)
        connection.commit()

    replay_session = open_command_journal(path).start()
    before = _database_snapshot(path)
    replay_main = _main(session_id="continuity-fact-corruption-restart")
    exchange = _submit(
        _composition(
            replay_session,
            _sidecar(session_id="continuity-fact-corruption-restart"),
        ),
        replay_main,
        _safe_retry_request(
            delivery_mode="outbox_redelivery",
            runtime_attempt_fence_token=RAW_FENCE_REPLAY,
            browser_control_scope_id="browser-scope-replay",
            correlation_id="correlation-replay",
            deadline_value=30_000,
        ),
        message_id="submit-fact-corruption-replay",
    )

    _assert_rejected(exchange, replay_main, SafeRetryContinuityRejectReason.JOURNAL_CORRUPT)
    assert _database_snapshot(path) == before
    assert _row_counts(path) == (2, 2, 2)


def test_exact_replay_fails_closed_if_an_earlier_epoch_later_dispatched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    initial, retry_session = _seed_ordinal_one(path)
    created = _submit(_composition(retry_session, _sidecar()), _main(), _safe_retry_request())
    assert created.disposition == "created"
    retry_session.record_dispatch_intent(
        run_id=initial.identity.run_id,
        operation_id=initial.identity.operation_id,
        expected_head_journal_revision=1,
        durable_dispatch_intent_ref="late-ordinal-one-dispatch",
    )

    replay_session = open_command_journal(path).start()
    before = _database_snapshot(path)
    main = _main(session_id="continuity-prior-dispatch-replay")
    exchange = _submit(
        _composition(
            replay_session,
            _sidecar(session_id="continuity-prior-dispatch-replay"),
        ),
        main,
        _safe_retry_request(
            delivery_mode="outbox_redelivery",
            runtime_attempt_fence_token=RAW_FENCE_REPLAY,
            deadline_value=30_000,
        ),
        message_id="submit-prior-dispatch-replay",
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.PRIOR_STATE_NOT_RETRYABLE)
    assert _database_snapshot(path) == before


@pytest.mark.parametrize(
    "updates",
    (
        {"profile_binding_generation": 3},
        {"attempt_no": 3},
        {"safe_retry_commit_ref": "safe-retry-commit-conflict"},
        {"dispatch_intent_id": "dispatch-intent-conflict"},
        {"dispatch_intent_revision": 3},
        {"expected_source_operation_ledger_revision": 3},
        {"expected_reconciliation_revision": 2},
        {"profile_binding_ref": "profile-binding-conflict"},
        {"provider_account_ref": "provider-account-conflict"},
    ),
)
def test_same_ordinal_conflicts_are_typed_and_never_mutate_the_durable_ack(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    first = _submit(_composition(retry_session, _sidecar()), _main(), _safe_retry_request())
    assert first.disposition == "created"

    replay_session = open_command_journal(path).start()
    before = _database_snapshot(path)
    main = _main(session_id="continuity-conflict")
    sidecar = _sidecar(session_id="continuity-conflict")
    conflicting = _safe_retry_request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token=RAW_FENCE_REPLAY,
        deadline_value=30_000,
        **updates,
    )
    exchange = _submit(
        _composition(replay_session, sidecar),
        main,
        conflicting,
        message_id="submit-conflict",
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.REPLAY_CONFLICT)
    assert _database_snapshot(path) == before


@pytest.mark.parametrize(
    ("updates", "expected"),
    (
        (
            {
                "attempt_no": 3,
                "dispatch_authorization_ordinal": 3,
                "dispatch_intent_revision": 3,
                "expected_source_operation_ledger_revision": 3,
                "expected_reconciliation_revision": 2,
                "safe_retry_commit_ref": "safe-retry-commit-3",
            },
            SafeRetryContinuityRejectReason.ORDINAL_GAP,
        ),
        ({"attempt_no": 1}, SafeRetryContinuityRejectReason.ATTEMPT_NOT_INCREASING),
        (
            {"dispatch_intent_revision": 1},
            SafeRetryContinuityRejectReason.REVISION_NOT_INCREASING,
        ),
        (
            {"expected_source_operation_ledger_revision": 1},
            SafeRetryContinuityRejectReason.REVISION_NOT_INCREASING,
        ),
    ),
)
def test_gap_stale_attempt_and_stale_revision_reject_without_any_write(
    tmp_path: Path,
    updates: dict[str, object],
    expected: SafeRetryContinuityRejectReason,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    main = _main()
    exchange = _submit(
        _composition(retry_session, _sidecar()),
        main,
        _safe_retry_request(**updates),
    )

    _assert_rejected(exchange, main, expected)
    assert _database_snapshot(path) == before


@pytest.mark.parametrize(
    "updates",
    (
        {"run_id": "run-conflict"},
        {"operation_id": "verify-session-conflict"},
        {"idempotency_key": "verify-session-key-conflict"},
        {"accepted_requirement_revision_id": "requirement-revision-conflict"},
        {"profile_binding_ref": "profile-binding-conflict"},
        {"provider_account_ref": "provider-account-conflict"},
        {"required_capabilities": ("bridge", "extension")},
    ),
)
def test_cross_identity_and_logical_body_changes_are_typed_identity_conflicts(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    main = _main()
    exchange = _submit(
        _composition(retry_session, _sidecar()),
        main,
        _safe_retry_request(**updates),
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.IDENTITY_CONFLICT)
    assert _database_snapshot(path) == before


@pytest.mark.parametrize("phase", ("dispatch_intent", "observed_result", "observed_failure"))
def test_any_prior_dispatch_or_observation_fails_closed_without_a_new_epoch(
    tmp_path: Path,
    phase: Literal["dispatch_intent", "observed_result", "observed_failure"],
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path, phase=phase)
    before = _database_snapshot(path)
    main = _main()

    exchange = _submit(
        _composition(retry_session, _sidecar()),
        main,
        _safe_retry_request(),
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.PRIOR_STATE_NOT_RETRYABLE)
    assert _database_snapshot(path) == before


@pytest.mark.parametrize("coverage_update", ("retained", "complete", "generation_gap"))
def test_retention_and_generation_coverage_gaps_reject_without_mutation(
    tmp_path: Path,
    coverage_update: str,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path, generation_count=3)
    with sqlite3.connect(path) as connection:
        if coverage_update == "retained":
            connection.execute("UPDATE source_history_generations SET retained = 0 WHERE generation = 1")
        elif coverage_update == "complete":
            connection.execute("UPDATE source_history_generations SET complete = 0 WHERE generation = 1")
        else:
            connection.execute("DELETE FROM source_history_generations WHERE generation = 2")
        connection.commit()
    before = _database_snapshot(path)
    main = _main()

    exchange = _submit(
        _composition(retry_session, _sidecar()),
        main,
        _safe_retry_request(),
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.HISTORY_INCOMPLETE)
    assert _database_snapshot(path) == before


@pytest.mark.parametrize(
    "corruption",
    (
        "revision_tail",
        "event_head_divergence",
        "accepted_ack",
        "accepted_ack_position",
        "unknown_phase",
    ),
)
def test_partial_or_ambiguous_history_is_a_typed_corrupt_reject(
    tmp_path: Path,
    corruption: str,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    with sqlite3.connect(path) as connection:
        if corruption == "revision_tail":
            connection.execute("UPDATE source_history_state SET last_journal_revision = 2 WHERE singleton = 1")
        else:
            trigger = connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'trigger' AND name = 'source_history_events_no_update'
                """
            ).fetchone()
            assert trigger is not None
            connection.execute("DROP TRIGGER source_history_events_no_update")
            if corruption == "event_head_divergence":
                connection.execute(
                    """
                    UPDATE source_history_events
                    SET authorized_dispatch_intent_digest = ?
                    WHERE dispatch_authorization_ordinal = 1
                    """,
                    ("f" * 64,),
                )
            elif corruption in {"accepted_ack", "accepted_ack_position"}:
                if corruption == "accepted_ack":
                    invalid_ack = b'{"not":"a-verify-session-accepted-ack"}'
                else:
                    current_ack = connection.execute(
                        """
                        SELECT accepted_ack_bytes FROM source_history_heads
                        WHERE dispatch_authorization_ordinal = 1
                        """
                    ).fetchone()[0]
                    parsed_ack = VerifySessionAcceptedAckV1.model_validate_json(current_ack, strict=True)
                    invalid_ack = canonical_json_bytes(
                        parsed_ack.model_copy(update={"accepted_generation": 2}).model_dump(mode="json")
                    )
                connection.execute(
                    """
                    UPDATE source_history_events SET accepted_ack_bytes = ?
                    WHERE dispatch_authorization_ordinal = 1
                    """,
                    (invalid_ack,),
                )
                connection.execute(
                    """
                    UPDATE source_history_heads SET accepted_ack_bytes = ?
                    WHERE dispatch_authorization_ordinal = 1
                    """,
                    (invalid_ack,),
                )
            else:
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(
                    """
                    UPDATE source_history_events SET phase = 'unknown'
                    WHERE dispatch_authorization_ordinal = 1
                    """
                )
                connection.execute(
                    """
                    UPDATE source_history_heads SET phase = 'unknown'
                    WHERE dispatch_authorization_ordinal = 1
                    """
                )
            connection.execute(str(trigger[0]))
        connection.commit()
    before = _database_snapshot(path)
    main = _main()

    exchange = _submit(
        _composition(retry_session, _sidecar()),
        main,
        _safe_retry_request(),
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.JOURNAL_CORRUPT)
    assert _database_snapshot(path) == before


def test_schema_mismatch_is_a_typed_reject_and_does_not_repair_the_database(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 6")
        connection.commit()
    before = _database_snapshot(path)
    main = _main()

    exchange = _submit(
        _composition(retry_session, _sidecar()),
        main,
        _safe_retry_request(),
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.JOURNAL_SCHEMA_MISMATCH)
    assert _database_snapshot(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)


def test_expired_arrival_deadline_is_rejected_before_the_first_mutation(tmp_path: Path) -> None:
    class Clock:
        def __init__(self) -> None:
            self.values = iter((10.0, 10.01, 10.02))

        def __call__(self) -> float:
            return next(self.values)

    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    main = _main()
    sidecar = _sidecar()
    exchange = _submit(
        _composition(retry_session, sidecar, monotonic_clock=Clock()),
        main,
        _safe_retry_request(deadline_value=1),
    )

    _assert_rejected(exchange, main, SafeRetryContinuityRejectReason.DEADLINE_EXPIRED)
    assert _database_snapshot(path) == before


@pytest.mark.parametrize("invalid_frame", ("bad_hmac", "sequence_gap"))
def test_frame_authentication_and_sequence_fail_before_any_continuity_write(
    tmp_path: Path,
    invalid_frame: str,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    main = _main()
    sidecar = _sidecar()
    composition = _composition(retry_session, sidecar)
    if invalid_frame == "bad_hmac":
        frame = bytearray(
            main.encode_submit(
                message_id="submit-1",
                correlation_id="correlation-2",
                payload=_safe_retry_request(),
            )
        )
        frame[-1] ^= 1
        invalid = bytes(frame)
    else:
        main.encode_submit(
            message_id="submit-skipped",
            correlation_id="correlation-2",
            payload=_safe_retry_request(),
        )
        invalid = main.encode_submit(
            message_id="submit-2",
            correlation_id="correlation-2",
            payload=_safe_retry_request(),
        )

    with pytest.raises(VerifySessionFrameError):
        composition.feed(invalid)

    assert _database_snapshot(path) == before
    assert sidecar.closed is True


def test_public_submit_dto_and_forged_arrival_cannot_mint_ordinal_two_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    sidecar = _sidecar()
    composition = _composition(retry_session, sidecar)
    request = _safe_retry_request()

    with pytest.raises(VerifySessionContinuityAdmissionError) as public_dto:
        composition.handle_submit(
            ReceivedVerifySessionSubmit(
                message_id="forged-submit",
                correlation_id="correlation-2",
                payload=request,
            )
        )
    assert public_dto.value.reason is VerifySessionContinuityAdmissionReason.UNAUTHENTICATED_ARRIVAL

    forged_session = object.__new__(CommandJournalSession)
    with pytest.raises(TypeError, match="factory"):
        create_verify_session_continuity_admission(
            command_journal_session=forged_session,
            frame_session=_sidecar(session_id="forged-session"),
        )
    assert _database_snapshot(path) == before


def test_constructed_noncanonical_authorization_is_rejected_by_the_frame_before_storage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    request = _safe_retry_request()
    forged_authorization = request.delivery.authorization.model_copy(update={"dispatch_intent_digest": "f" * 64})
    forged_delivery = request.delivery.model_copy(update={"authorization": forged_authorization})
    forged = VerifySessionRequestV1.model_construct(
        **{
            **request.model_dump(mode="python"),
            "delivery": forged_delivery,
        }
    )
    main = _main()
    composition = _composition(retry_session, _sidecar())

    with pytest.raises(VerifySessionFrameError):
        composition.feed(
            main.encode_submit(
                message_id="submit-forged",
                correlation_id="correlation-2",
                payload=forged,
            )
        )

    assert _database_snapshot(path) == before


@pytest.mark.parametrize(
    ("field", "value"),
    (("source", "linkedin"), ("operation_kind", "search")),
)
def test_constructed_cross_source_or_operation_kind_is_rejected_before_storage(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    before = _database_snapshot(path)
    request = _safe_retry_request()
    forged_identity = request.identity.model_copy(update={field: value})
    forged = VerifySessionRequestV1.model_construct(
        **{
            **request.model_dump(mode="python"),
            "identity": forged_identity,
        }
    )
    main = _main()
    composition = _composition(retry_session, _sidecar())

    with pytest.raises(VerifySessionFrameError):
        composition.feed(
            main.encode_submit(
                message_id=f"submit-forged-{field}",
                correlation_id="correlation-2",
                payload=forged,
            )
        )

    assert _database_snapshot(path) == before


def test_reused_safe_retry_ref_and_equal_epoch_revisions_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    created = _submit(_composition(retry_session, _sidecar()), _main(), _safe_retry_request())
    assert created.disposition == "created"

    cases = (
        (
            {"safe_retry_commit_ref": "safe-retry-commit-2"},
            SafeRetryContinuityRejectReason.SAFE_RETRY_REF_REUSED,
        ),
        ({"attempt_no": 2}, SafeRetryContinuityRejectReason.ATTEMPT_NOT_INCREASING),
        (
            {"dispatch_intent_revision": 2},
            SafeRetryContinuityRejectReason.REVISION_NOT_INCREASING,
        ),
        (
            {"expected_source_operation_ledger_revision": 2},
            SafeRetryContinuityRejectReason.REVISION_NOT_INCREASING,
        ),
        (
            {"expected_reconciliation_revision": 1},
            SafeRetryContinuityRejectReason.REVISION_NOT_INCREASING,
        ),
    )
    for index, (updates, expected) in enumerate(cases, start=1):
        session = open_command_journal(path).start()
        session_id = f"continuity-third-{index}"
        main = _main(session_id=session_id)
        request_updates = {
            "attempt_no": 3,
            "runtime_attempt_fence_token": f"ordinal-three-fence-{index}-" + "q" * 64,
            "profile_binding_generation": 3,
            "browser_control_scope_id": "browser-scope-3",
            "correlation_id": "correlation-3",
            "dispatch_authorization_ordinal": 3,
            "dispatch_intent_id": "dispatch-intent-3",
            "dispatch_intent_revision": 3,
            "safe_retry_commit_ref": f"safe-retry-commit-3-{index}",
            "expected_source_operation_ledger_revision": 3,
            "expected_reconciliation_revision": 2,
        }
        request_updates.update(updates)
        before = _database_snapshot(path)
        exchange = _submit(
            _composition(session, _sidecar(session_id=session_id)),
            main,
            _safe_retry_request(**request_updates),
            message_id=f"submit-third-{index}",
        )

        _assert_rejected(exchange, main, expected)
        assert _database_snapshot(path) == before


def test_two_writers_create_at_most_one_epoch_and_return_one_identical_durable_ack(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _seed_ordinal_one(path)
    journal = open_command_journal(path)
    sessions = (journal.start(), journal.start())
    barrier = threading.Barrier(2)

    def submit(index: int):
        session_id = f"continuity-writer-{index}"
        main = _main(session_id=session_id)
        composition = _composition(sessions[index], _sidecar(session_id=session_id))
        barrier.wait()
        return _submit(
            composition,
            main,
            _safe_retry_request(),
            message_id="submit-writer",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        exchanges = tuple(executor.map(submit, range(2)))

    assert sorted(exchange.disposition for exchange in exchanges) == ["created", "exact_replay"]
    assert exchanges[0].accepted_ack_bytes == exchanges[1].accepted_ack_bytes
    assert exchanges[0].accepted_ack_hash == exchanges[1].accepted_ack_hash
    assert _row_counts(path) == (2, 2, 2)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM source_history_heads
            WHERE dispatch_authorization_ordinal = 2
            """
        ).fetchone() == (1,)


def test_raw_bearers_never_enter_ack_storage_rejects_or_error_surfaces(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    _, retry_session = _seed_ordinal_one(path)
    request = _safe_retry_request()
    exchange = _submit(_composition(retry_session, _sidecar()), _main(), request)
    assert exchange.disposition == "created"

    with sqlite3.connect(path) as connection:
        rows = tuple(connection.execute("SELECT * FROM source_history_events").fetchall())
        heads = tuple(connection.execute("SELECT * FROM source_history_heads").fetchall())
    surfaces = "\n".join(
        (
            repr(exchange),
            repr(rows),
            repr(heads),
            repr(exchange.accepted_ack_bytes),
            repr(exchange.outbound_frames),
        )
    )
    assert RAW_FENCE_ONE not in surfaces
    assert RAW_FENCE_TWO not in surfaces
    assert RAW_FENCE_REPLAY not in surfaces
    assert request.identity.runtime_attempt_fence_ref in surfaces

    forged = ReceivedVerifySessionSubmit(
        message_id="forged",
        correlation_id="correlation-2",
        payload=request,
    )
    with pytest.raises(VerifySessionContinuityAdmissionError) as error:
        _composition(
            open_command_journal(path).start(),
            _sidecar(session_id="leak-error"),
        ).handle_submit(forged)
    error_surfaces = "\n".join((str(error.value), repr(error.value), repr(error.value.args)))
    assert RAW_FENCE_TWO not in error_surfaces


def test_continuity_api_has_zero_production_callers_and_no_dispatch_or_effect_authority() -> None:
    continuity_source = CONTINUITY_MODULE_PATH.read_text(encoding="utf-8")
    store_source = CONTINUITY_STORE_PATH.read_text(encoding="utf-8")
    source_root = PROJECT_ROOT / "src"

    factory_callers = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in source_root.rglob("*.py")
        if path != CONTINUITY_MODULE_PATH
        and "create_verify_session_continuity_admission(" in path.read_text(encoding="utf-8")
    ]
    request_factory_callers = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in source_root.rglob("*.py")
        if path != CONTRACT_PATH and "VerifySessionRequestV1.create(" in path.read_text(encoding="utf-8")
    ]

    assert factory_callers == []
    assert request_factory_callers == []
    assert "record_dispatch_intent" not in continuity_source
    assert "record_dispatch_intent" not in store_source
    assert "VerifySessionPendingEffectAuthority" not in continuity_source
    assert "WtsCli" not in continuity_source
    assert "wtscli" not in continuity_source.casefold()
    assert "effect" not in store_source.casefold()
    assert "dispatch_authorization_ordinal: Literal[1] = 1" in (
        PROJECT_ROOT / "src" / "seektalent" / "source_port" / "_command_journal_types.py"
    ).read_text(encoding="utf-8")
    assert get_type_hints(AcceptedCommand)["dispatch_authorization_ordinal"] == Literal[1]
    assert "profile_binding_generation" not in inspect.getsource(
        __import__(
            "seektalent.source_port.verify_session_contract",
            fromlist=["_request_intent_payload"],
        )._request_intent_payload
    )


def test_public_accepted_command_still_cannot_target_ordinal_two_after_continuity_exists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    request = _request()
    session = create_command_journal(path).start()
    forged = replace(_accepted_command(request), dispatch_authorization_ordinal=2)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        session.record_accepted(forged)
    assert _row_counts(path) == (0, 0, 0)
