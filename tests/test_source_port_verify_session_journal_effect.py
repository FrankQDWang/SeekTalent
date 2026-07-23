from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from contextlib import suppress
import copy
import logging
from pathlib import Path
import pickle
import sqlite3
import threading
import time
from unittest.mock import patch

import pytest

import seektalent.source_port._command_journal_engine as journal_engine
import seektalent.source_port.verify_session_journal_effect as journal_effect
from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionSubmit,
    VerifySessionFailureV1,
    VerifySessionFrameError,
    VerifySessionFrameReason,
    VerifySessionResultV1,
)
from seektalent.source_port.command_journal import (
    CommandJournalError,
    CommandJournalErrorReason,
    CommandJournalTransitionDisposition,
    CommandJournalTransitionReceipt,
    create_command_journal,
    open_command_journal,
)
from seektalent.source_port.history_contract import (
    ExactAuthorizationSelector,
    SourceHistoryMatched,
    SourceHistoryQueryV1,
)
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1


RAW_FENCE_TOKEN = "verify-session-journal-effect-fence-canary-" + "x" * 64
REDELIVERY_FENCE_TOKEN = "verify-session-journal-effect-redelivery-fence-" + "y" * 64
MAIN_TO_SIDECAR_KEY = bytes(range(32))
SIDECAR_TO_MAIN_KEY = bytes(range(32, 64))


def _request(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "run_id": "run-1",
        "operation_id": "verify-session-1",
        "attempt_no": 1,
        "idempotency_key": "verify-session-key-1",
        "correlation_id": "correlation-1",
        "accepted_requirement_revision_id": "requirement-revision-1",
        "runtime_attempt_fence_token": RAW_FENCE_TOKEN,
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


def _redelivery(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "delivery_mode": "outbox_redelivery",
        "runtime_attempt_fence_token": REDELIVERY_FENCE_TOKEN,
        "correlation_id": "correlation-2",
        "browser_control_scope_id": "browser-scope-2",
        "deadline_value": 59_999,
    }
    values.update(updates)
    return _request(**values)


def _result(request: VerifySessionRequestV1, **updates: object) -> VerifySessionResultV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.result/v1",
        "identity": request.identity,
        "process_readiness": "ready",
        "bridge_readiness": "ready",
        "extension_readiness": "ready",
        "profile_lock_readiness": "ready",
        "account_readiness": "ready",
        "search_surface_readiness": "ready",
        "risk_state": "clear",
        "session_readiness": "ready",
        "actual_profile_binding_ref": "profile-binding-1",
        "actual_provider_account_ref": "provider-account-1",
        "actual_profile_binding_generation": 1,
        "safe_reason_code": None,
        "user_action": None,
        "component_receipt_refs": ("main-receipt-1",),
    }
    values.update(updates)
    return VerifySessionResultV1.model_validate(values)


def _failure(request: VerifySessionRequestV1) -> VerifySessionFailureV1:
    return VerifySessionFailureV1.model_validate(
        {
            "contract_version": "seektalent.source.verify-session.failure/v1",
            "identity": request.identity,
            "failure_fact": "no_effect_performed",
            "failure_reason": "sidecar_not_ready",
        }
    )


def _history(request: VerifySessionRequestV1, *, searched_last_generation: int) -> SourceHistoryQueryV1:
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
        authorization_selector=ExactAuthorizationSelector(kind="exact", ordinal=1),
        searched_first_generation=1,
        searched_last_generation=searched_last_generation,
        expected_source_operation_ledger_revision=identity.expected_source_operation_ledger_revision,
        expected_reconciliation_revision=identity.expected_reconciliation_revision,
    )


def _sessions(session_id: str) -> tuple[PostHandshakeVerifySessionSession, PostHandshakeVerifySessionSession]:
    return (
        PostHandshakeVerifySessionSession.for_main(
            session_id=session_id,
            protocol_minor=0,
            main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
            sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
        ),
        PostHandshakeVerifySessionSession.for_sidecar(
            session_id=session_id,
            protocol_minor=0,
            main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
            sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
        ),
    )


class _Effect:
    def __init__(self, *, failure: bool = False) -> None:
        self.calls = 0
        self.failure = failure
        self.deadlines: list[float] = []

    def __call__(
        self,
        request: VerifySessionRequestV1,
        deadline_at: float,
    ) -> VerifySessionResultV1 | VerifySessionFailureV1:
        self.calls += 1
        self.deadlines.append(deadline_at)
        return _failure(request) if self.failure else _result(request)


class _MonotonicClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _SequenceClock:
    def __init__(self, values: tuple[float, ...]) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


def _composition(
    path: Path,
    effect: journal_effect.VerifySessionEffect,
    *,
    session_id: str,
    reopen: bool = False,
    monotonic_clock: Callable[[], float] | None = None,
) -> tuple[
    PostHandshakeVerifySessionSession,
    journal_effect.VerifySessionJournalEffectComposition,
]:
    journal = open_command_journal(path) if reopen else create_command_journal(path)
    main, sidecar = _sessions(session_id)
    values: dict[str, object] = {
        "command_journal_session": journal.start(),
        "frame_session": sidecar,
        "effect": effect,
    }
    if monotonic_clock is not None:
        values["monotonic_clock"] = monotonic_clock
    return main, journal_effect.create_verify_session_journal_effect_composition(**values)  # type: ignore[arg-type]


def _submit(
    main: PostHandshakeVerifySessionSession,
    composition: journal_effect.VerifySessionJournalEffectComposition,
    request: VerifySessionRequestV1,
    *,
    message_id: str = "submit-1",
) -> journal_effect.VerifySessionJournalEffectExchange:
    return composition.feed(
        main.encode_submit(
            message_id=message_id,
            correlation_id=request.identity.correlation_id,
            payload=request,
        )
    )


def _consume_pending_effect(
    exchange: journal_effect.VerifySessionJournalEffectExchange,
) -> journal_effect.VerifySessionJournalEffectExchange:
    assert exchange.pending_effect is not None
    return exchange.pending_effect.consume()


def _journal_phases(path: Path) -> list[tuple[str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT phase FROM source_history_heads").fetchall()


def _journal_snapshot(path: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (candidate.name, candidate.read_bytes())
        for candidate in sorted(path.parent.glob(f"{path.name}*"))
        if candidate.is_file()
    )


def test_handle_submit_rejects_a_constructed_dto_before_any_journal_or_frame_mutation(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    _, composition = _composition(path, effect, session_id="session-1")
    state = journal_effect._composition_state(composition)
    forged = ReceivedVerifySessionSubmit(
        message_id="unauthenticated",
        correlation_id="correlation-1",
        payload=_request(),
    )
    before_send_sequence = state.frame_session._next_send_sequence  # type: ignore[attr-defined]
    before_pending = dict(state.frame_session._pending_requests)  # type: ignore[attr-defined]
    before_journal = _journal_snapshot(path)

    with pytest.raises(journal_effect.VerifySessionJournalEffectError, match="unauthenticated_arrival") as rejection:
        composition.handle_submit(forged)

    assert _journal_phases(path) == []
    assert _journal_snapshot(path) == before_journal
    assert effect.calls == 0
    assert state.frame_session._next_send_sequence == before_send_sequence  # type: ignore[attr-defined]
    assert state.frame_session._pending_requests == before_pending  # type: ignore[attr-defined]
    assert RAW_FENCE_TOKEN not in "\n".join((str(rejection.value), repr(rejection.value), repr(rejection.value.args)))


def test_handle_submit_rejects_an_authenticated_arrival_from_another_live_session_without_writes(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.sqlite3"
    second_path = tmp_path / "second.sqlite3"
    first_main, first = _composition(first_path, _Effect(), session_id="session-1")
    _, second = _composition(second_path, _Effect(), session_id="session-2")
    first_state = journal_effect._composition_state(first)
    second_state = journal_effect._composition_state(second)
    arrival = first_state.frame_session.feed(
        first_main.encode_submit(
            message_id="submit-1",
            correlation_id="correlation-1",
            payload=_request(),
        )
    )[0]
    before_send_sequence = second_state.frame_session._next_send_sequence  # type: ignore[attr-defined]
    before_pending = dict(second_state.frame_session._pending_requests)  # type: ignore[attr-defined]
    first_before_journal = _journal_snapshot(first_path)
    second_before_journal = _journal_snapshot(second_path)

    with pytest.raises(journal_effect.VerifySessionJournalEffectError, match="unauthenticated_arrival"):
        second.handle_submit(arrival)

    assert _journal_phases(first_path) == []
    assert _journal_phases(second_path) == []
    assert _journal_snapshot(first_path) == first_before_journal
    assert _journal_snapshot(second_path) == second_before_journal
    assert second_state.frame_session._next_send_sequence == before_send_sequence  # type: ignore[attr-defined]
    assert second_state.frame_session._pending_requests == before_pending  # type: ignore[attr-defined]


def test_handle_submit_consumes_one_authenticated_arrival_only_once_without_a_second_write(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    main, composition = _composition(path, _Effect(), session_id="session-1")
    state = journal_effect._composition_state(composition)
    arrival = state.frame_session.feed(
        main.encode_submit(
            message_id="submit-1",
            correlation_id="correlation-1",
            payload=_request(),
        )
    )[0]

    accepted = composition.handle_submit(arrival)
    before_phases = _journal_phases(path)
    before_send_sequence = state.frame_session._next_send_sequence  # type: ignore[attr-defined]
    before_journal = _journal_snapshot(path)

    with pytest.raises(journal_effect.VerifySessionJournalEffectError, match="unauthenticated_arrival"):
        composition.handle_submit(arrival)

    assert accepted.pending_effect is not None
    assert _journal_phases(path) == before_phases
    assert _journal_snapshot(path) == before_journal
    assert state.frame_session._next_send_sequence == before_send_sequence  # type: ignore[attr-defined]


def test_handle_submit_rejects_a_caller_supplied_future_arrival_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    main, composition = _composition(path, _Effect(), session_id="session-1")
    state = journal_effect._composition_state(composition)
    arrival = state.frame_session.feed(
        main.encode_submit(
            message_id="submit-1",
            correlation_id="correlation-1",
            payload=_request(),
        )
    )[0]
    before_send_sequence = state.frame_session._next_send_sequence  # type: ignore[attr-defined]
    before_journal = _journal_snapshot(path)

    with pytest.raises(journal_effect.VerifySessionJournalEffectError, match="unauthenticated_arrival"):
        composition.handle_submit(arrival, arrival_monotonic=time.monotonic() + 60)

    assert _journal_phases(path) == []
    assert _journal_snapshot(path) == before_journal
    assert state.frame_session._next_send_sequence == before_send_sequence  # type: ignore[attr-defined]


def test_authenticated_arrival_is_factory_only_noncopyable_and_released_on_composition_close(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    main, composition = _composition(path, _Effect(), session_id="session-1")
    state = journal_effect._composition_state(composition)
    arrival = state.frame_session.feed(
        main.encode_submit(
            message_id="submit-1",
            correlation_id="correlation-1",
            payload=_request(),
        )
    )[0]
    before_journal = _journal_snapshot(path)
    forged = object.__new__(type(arrival))
    object.__setattr__(forged, "message_id", arrival.message_id)
    object.__setattr__(forged, "correlation_id", arrival.correlation_id)
    object.__setattr__(forged, "payload", arrival.payload)

    with pytest.raises(TypeError, match="copied"):
        copy.copy(arrival)
    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(arrival)
    with pytest.raises(journal_effect.VerifySessionJournalEffectError, match="unauthenticated_arrival"):
        composition.handle_submit(forged)

    composition.close()

    pending = state.frame_session._pending_request(arrival.message_id)  # type: ignore[attr-defined]
    assert pending is not None
    assert pending.authenticated_arrival is None
    assert _journal_snapshot(path) == before_journal


def test_durable_ack_is_deliverable_before_a_factory_only_pending_effect_is_consumed(tmp_path: Path) -> None:
    effect = _Effect()
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")

    accepted = _submit(main, composition, _request())

    assert accepted.disposition == "pending_effect"
    assert effect.calls == 0
    assert len(accepted.outbound_frames) == 1
    assert accepted.pending_effect is not None
    assert main.feed(accepted.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"

    with pytest.raises(TypeError, match="copied"):
        copy.copy(accepted.pending_effect)
    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(accepted.pending_effect)

    terminal = accepted.pending_effect.consume()

    assert effect.calls == 1
    assert terminal.disposition == "observed_result"
    assert len(terminal.outbound_frames) == 1
    assert main.feed(terminal.outbound_frames[0])[0].payload.session_readiness == "ready"
    with pytest.raises(TypeError, match="consumed"):
        accepted.pending_effect.consume()


def test_pending_effect_receives_the_original_arrival_anchored_absolute_deadline(tmp_path: Path) -> None:
    clock = _MonotonicClock(123.0)
    effect = _Effect()
    main, composition = _composition(
        tmp_path / "journal.sqlite3",
        effect,
        session_id="session-1",
        monotonic_clock=clock,
    )

    accepted = _submit(main, composition, _request(deadline_value=1_000))
    assert accepted.arrival_deadline_at == pytest.approx(124.0)
    clock.advance(0.250)
    terminal = _consume_pending_effect(accepted)

    assert terminal.disposition == "observed_result"
    assert effect.deadlines == [accepted.arrival_deadline_at]
    assert effect.deadlines[0] != clock() + 1


def test_pending_effect_authority_cannot_expose_or_reuse_its_effect_consumer(tmp_path: Path) -> None:
    effect = _Effect()
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")
    accepted = _submit(main, composition, _request())

    assert accepted.pending_effect is not None
    state_accessor = getattr(accepted.pending_effect, "_state", None)
    if state_accessor is not None:
        escaped_state = state_accessor()
        with suppress(Exception):
            escaped_state.consume_effect()
        with suppress(Exception):
            escaped_state.consume_effect()

    assert effect.calls == 0
    assert state_accessor is None
    assert not hasattr(accepted.pending_effect, "_install_state")

    terminal = accepted.pending_effect.consume()

    assert terminal.disposition == "observed_result"
    assert effect.calls == 1
    with pytest.raises(TypeError, match="consumed"):
        accepted.pending_effect.consume()


def test_expired_local_monotonic_deadline_never_starts_a_pending_effect(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    clock = _MonotonicClock()
    main, composition = _composition(path, effect, session_id="session-1", monotonic_clock=clock)

    accepted = _submit(main, composition, _request(deadline_value=1))

    assert accepted.disposition == "pending_effect"
    assert accepted.pending_effect is not None
    assert accepted.arrival_deadline_at == pytest.approx(0.001)
    assert effect.calls == 0
    assert main.feed(accepted.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"

    clock.advance(0.050)
    expired = accepted.pending_effect.consume()

    assert effect.calls == 0
    assert expired.disposition == "reconcile_first"
    assert expired.arrival_deadline_at == accepted.arrival_deadline_at
    assert len(expired.outbound_frames) == 1
    assert main.feed(expired.outbound_frames[0])[0].payload.reconciliation_fact == "dispatch_not_observed"
    facts = SourceHistorySQLiteReader(path).query(_history(_request(deadline_value=1), searched_last_generation=1))
    assert isinstance(facts, SourceHistoryMatched)
    assert facts.facts[0].conclusion == "dispatch_not_observed"


def test_deadline_expiry_after_the_pre_effect_hook_never_starts_the_effect(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    clock = _MonotonicClock()
    main, composition = _composition(path, effect, session_id="session-1", monotonic_clock=clock)
    pending = _submit(main, composition, _request(deadline_value=1))

    assert pending.disposition == "pending_effect"
    assert [receipt.head_phase for receipt in pending.receipts] == ["accepted", "dispatch_intent"]
    assert pending.pending_effect is not None
    assert main.feed(pending.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"

    with patch.object(journal_effect, "_before_effect_invocation", side_effect=lambda: clock.advance(0.050)):
        expired = pending.pending_effect.consume()

    assert effect.calls == 0
    assert expired.disposition == "reconcile_first"
    assert len(expired.outbound_frames) == 1
    assert main.feed(expired.outbound_frames[0])[0].payload.reconciliation_fact == "dispatch_not_observed"
    assert len(main._pending_requests) == 0  # type: ignore[attr-defined]


def test_deadline_anchor_includes_durable_acceptance_wait_before_effect_can_start(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    clock = _MonotonicClock()
    main, composition = _composition(path, effect, session_id="session-1", monotonic_clock=clock)
    original_record_accepted = journal_effect._record_accepted

    def delayed_record_accepted(*args: object) -> CommandJournalTransitionReceipt:
        receipt = original_record_accepted(*args)  # type: ignore[arg-type]
        clock.advance(0.050)
        return receipt

    with patch.object(journal_effect, "_record_accepted", side_effect=delayed_record_accepted):
        expired = _submit(main, composition, _request(deadline_value=1))

    assert expired.disposition == "reconcile_first"
    assert expired.pending_effect is None
    assert [receipt.head_phase for receipt in expired.receipts] == ["accepted"]
    assert effect.calls == 0
    main.feed(expired.outbound_frames[0])
    assert main.feed(expired.outbound_frames[1])[0].payload.reconciliation_fact == "accepted_no_dispatch"


def test_deadline_expiry_after_durable_acceptance_never_creates_dispatch_intent(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    clock = _SequenceClock((0.0, 0.002))
    main, composition = _composition(path, effect, session_id="session-1", monotonic_clock=clock)

    expired = _submit(main, composition, _request(deadline_value=1))

    assert expired.disposition == "reconcile_first"
    assert expired.pending_effect is None
    assert [receipt.head_phase for receipt in expired.receipts] == ["accepted"]
    assert effect.calls == 0
    main.feed(expired.outbound_frames[0])
    assert main.feed(expired.outbound_frames[1])[0].payload.reconciliation_fact == "accepted_no_dispatch"
    facts = SourceHistorySQLiteReader(path).query(_history(_request(deadline_value=1), searched_last_generation=1))
    assert isinstance(facts, SourceHistoryMatched)
    assert facts.facts[0].conclusion == "accepted_no_dispatch"


def test_deadline_expiry_after_durable_dispatch_never_mints_an_effect_authority(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    clock = _SequenceClock((0.0, 0.0, 0.002))
    main, composition = _composition(path, effect, session_id="session-1", monotonic_clock=clock)

    expired = _submit(main, composition, _request(deadline_value=1))

    assert expired.disposition == "reconcile_first"
    assert expired.pending_effect is None
    assert [receipt.head_phase for receipt in expired.receipts] == ["accepted", "dispatch_intent"]
    assert effect.calls == 0
    main.feed(expired.outbound_frames[0])
    assert main.feed(expired.outbound_frames[1])[0].payload.reconciliation_fact == "dispatch_not_observed"


def test_terminal_replay_never_applies_or_extends_a_local_deadline(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    first_main, first = _composition(
        path,
        effect,
        session_id="session-1",
        monotonic_clock=_MonotonicClock(),
    )
    _consume_pending_effect(_submit(first_main, first, _request(deadline_value=1)))

    exact_clock_values = iter((0.0, 0.050))

    def exact_clock() -> float:
        return next(exact_clock_values)

    def forbidden_outbox_clock() -> float:
        raise AssertionError("outbox redelivery must not anchor a local deadline")

    exact_main, exact = _composition(
        path,
        effect,
        session_id="session-2",
        reopen=True,
        monotonic_clock=exact_clock,
    )
    exact_replay = _submit(exact_main, exact, _request(deadline_value=1))

    outbox_main, outbox = _composition(
        path,
        effect,
        session_id="session-3",
        reopen=True,
        monotonic_clock=forbidden_outbox_clock,
    )
    outbox_replay = _submit(outbox_main, outbox, _redelivery(deadline_value=1))

    assert exact_replay.disposition == outbox_replay.disposition == "terminal_replay"
    assert effect.calls == 1


def test_reconcile_first_status_retires_each_replayed_request_without_pending_growth(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    first_main, first = _composition(path, effect, session_id="session-1")
    pending = _submit(first_main, first, _request())

    assert pending.disposition == "pending_effect"
    assert pending.pending_effect is not None
    assert effect.calls == 0

    main, retry = _composition(path, effect, session_id="session-2", reopen=True)
    for number in range(1, 6):
        exchange = _submit(main, retry, _redelivery(), message_id=f"submit-{number}")

        assert exchange.disposition == "reconcile_first"
        assert len(exchange.outbound_frames) == 2
        assert main.feed(exchange.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"
        assert main.feed(exchange.outbound_frames[1])[0].payload.reconciliation_fact == "dispatch_not_observed"
        assert len(main._pending_requests) == 0  # type: ignore[attr-defined]
        assert len(journal_effect._composition_state(retry).frame_session._pending_requests) == 0  # type: ignore[attr-defined]

    assert effect.calls == 0


def test_first_submit_returns_sealed_created_receipts_and_durable_ack_then_terminal(tmp_path: Path) -> None:
    effect = _Effect()
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")

    accepted = _submit(main, composition, _request())

    assert effect.calls == 0
    assert accepted.disposition == "pending_effect"
    assert len(accepted.outbound_frames) == 1
    assert tuple(receipt.disposition for receipt in accepted.receipts) == (
        CommandJournalTransitionDisposition.CREATED,
        CommandJournalTransitionDisposition.CREATED,
    )
    assert all(isinstance(receipt, CommandJournalTransitionReceipt) for receipt in accepted.receipts)
    assert [receipt.revision for receipt in accepted.receipts] == [1, 2]
    assert [receipt.startup_generation for receipt in accepted.receipts] == [1, 1]
    assert RAW_FENCE_TOKEN not in repr(accepted.receipts[0])
    assert main.feed(accepted.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"

    terminal = _consume_pending_effect(accepted)

    assert effect.calls == 1
    assert terminal.disposition == "observed_result"
    assert len(terminal.outbound_frames) == 1
    assert [receipt.revision for receipt in terminal.receipts] == [1, 2, 3]
    assert main.feed(terminal.outbound_frames[0])[0].payload.session_readiness == "ready"


def test_transition_receipts_are_factory_only_and_cannot_be_copied_or_forged(tmp_path: Path) -> None:
    effect = _Effect()
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")
    receipt = _submit(main, composition, _request()).receipts[0]

    with pytest.raises(TypeError, match="factory"):
        CommandJournalTransitionReceipt(1, result=object(), token=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="factory"):
        _ = int.__new__(CommandJournalTransitionReceipt, 1).disposition
    with pytest.raises(TypeError, match="copied"):
        copy.copy(receipt)
    with pytest.raises(TypeError, match="copied"):
        copy.deepcopy(receipt)
    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(receipt)


def test_durable_reply_bytes_never_contain_the_raw_fence_bearer(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    main, composition = _composition(path, _Effect(), session_id="session-1")
    _consume_pending_effect(_submit(main, composition, _request()))

    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT accepted_ack_bytes, terminal_reply_bytes FROM source_history_events ORDER BY journal_revision"
        ).fetchall()
    finally:
        connection.close()

    assert RAW_FENCE_TOKEN.encode() not in b"".join(value for row in rows for value in row if value is not None)


def test_terminal_reply_bytes_are_immutable_against_the_history_head(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    main, composition = _composition(path, _Effect(), session_id="session-1")
    _consume_pending_effect(_submit(main, composition, _request()))

    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE source_history_heads SET terminal_reply_bytes = ?", (b"tampered",))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CommandJournalError) as corrupt:
        open_command_journal(path)
    assert corrupt.value.reason is CommandJournalErrorReason.CORRUPT


def test_tampered_authenticated_submit_never_reaches_the_journal_or_effect(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")
    frame = main.encode_submit(message_id="submit-1", correlation_id="correlation-1", payload=_request())
    tampered = frame[:-1] + bytes((frame[-1] ^ 1,))

    with pytest.raises(VerifySessionFrameError):
        composition.feed(tampered)

    assert effect.calls == 0
    assert (
        SourceHistorySQLiteReader(path).query(_history(_request(), searched_last_generation=1)).outcome == "not_found"
    )


def test_exact_replay_replays_durable_ack_and_terminal_without_effect(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    first_main, first = _composition(path, effect, session_id="session-1")
    _consume_pending_effect(_submit(first_main, first, _request()))

    replay_main, replay = _composition(path, effect, session_id="session-2", reopen=True)
    exchange = _submit(replay_main, replay, _request())

    assert effect.calls == 1
    assert exchange.disposition == "terminal_replay"
    assert len(exchange.outbound_frames) == 2
    assert exchange.receipts[0].disposition is CommandJournalTransitionDisposition.EXACT_REPLAY
    assert (exchange.receipts[0].startup_generation, exchange.receipts[0].revision) == (2, 1)
    assert replay_main.feed(exchange.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"
    assert replay_main.feed(exchange.outbound_frames[1])[0].payload.identity == _request().identity


def test_ack_loss_outbox_redelivery_replays_original_durable_reply_without_effect(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    first_main, first = _composition(path, effect, session_id="session-1")
    lost = _submit(first_main, first, _request())
    assert len(lost.outbound_frames) == 1
    _consume_pending_effect(lost)

    retry_main, retry = _composition(path, effect, session_id="session-2", reopen=True)
    exchange = _submit(retry_main, retry, _redelivery())

    assert effect.calls == 1
    assert exchange.disposition == "terminal_replay"
    assert len(exchange.outbound_frames) == 2
    assert retry_main.feed(exchange.outbound_frames[0])[0].payload.identity == _request().identity
    assert retry_main.feed(exchange.outbound_frames[1])[0].payload.identity == _request().identity


def test_outbox_redelivery_with_only_an_accepted_head_is_reconcile_first_and_never_effects(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")

    with patch.object(journal_effect, "_record_dispatch_intent", side_effect=SystemExit("crash")):
        with pytest.raises(SystemExit, match="crash"):
            _submit(main, composition, _request())

    retry_main, retry = _composition(path, effect, session_id="session-2", reopen=True)
    exchange = _submit(retry_main, retry, _redelivery())

    assert exchange.disposition == "reconcile_first"
    assert len(exchange.outbound_frames) == 2
    assert effect.calls == 0
    retry_main.feed(exchange.outbound_frames[0])
    assert retry_main.feed(exchange.outbound_frames[1])[0].payload.reconciliation_fact == "accepted_no_dispatch"


def test_exact_replay_with_only_an_accepted_head_is_reconcile_first_and_never_effects(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")

    with patch.object(journal_effect, "_record_dispatch_intent", side_effect=SystemExit("crash")):
        with pytest.raises(SystemExit, match="crash"):
            _submit(main, composition, _request())

    retry_main, retry = _composition(path, effect, session_id="session-2", reopen=True)
    exchange = _submit(retry_main, retry, _request())

    assert exchange.disposition == "reconcile_first"
    assert len(exchange.outbound_frames) == 2
    assert effect.calls == 0
    retry_main.feed(exchange.outbound_frames[0])
    assert retry_main.feed(exchange.outbound_frames[1])[0].payload.reconciliation_fact == "accepted_no_dispatch"


def test_outbox_redelivery_cannot_extend_the_durable_deadline(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")
    _consume_pending_effect(_submit(main, composition, _request()))

    retry_main, retry = _composition(path, effect, session_id="session-2", reopen=True)
    with pytest.raises(journal_effect.VerifySessionJournalEffectError) as failure:
        _submit(retry_main, retry, _redelivery(deadline_value=60_001))

    assert failure.value.reason is journal_effect.VerifySessionJournalEffectReason.JOURNAL_CONFLICT
    assert effect.calls == 1


def test_concurrent_same_submit_invokes_the_effect_at_most_once(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    entered = threading.Event()
    release = threading.Event()

    class BlockingEffect(_Effect):
        def __call__(self, request: VerifySessionRequestV1, deadline_at: float) -> VerifySessionResultV1:
            self.calls += 1
            self.deadlines.append(deadline_at)
            entered.set()
            assert release.wait(timeout=5)
            return _result(request)

    effect = BlockingEffect()
    first_main, first = _composition(path, effect, session_id="session-1")
    second_main, second = _composition(path, effect, session_id="session-2", reopen=True)
    accepted = _submit(first_main, first, _request())
    assert accepted.pending_effect is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(accepted.pending_effect.consume)
        assert entered.wait(timeout=5)
        second_future = executor.submit(_submit, second_main, second, _request())
        second_exchange = second_future.result(timeout=5)
        release.set()
        terminal = first_future.result(timeout=5)

    assert effect.calls == 1
    assert terminal.disposition == "observed_result"
    assert second_exchange.disposition == "reconcile_first"
    assert len(second_exchange.outbound_frames) == 2
    assert second_exchange.receipts[0].disposition is CommandJournalTransitionDisposition.EXACT_REPLAY


@pytest.mark.parametrize(
    "conflicting",
    (
        lambda: _request(required_capabilities=("bridge", "extension")),
        lambda: _request(dispatch_intent_revision=2),
    ),
    ids=("identity", "authorization_digest"),
)
def test_conflicting_identity_or_dispatch_digest_fails_closed_without_another_effect(
    tmp_path: Path,
    conflicting: object,
) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")
    _consume_pending_effect(_submit(main, composition, _request()))

    retry_main, retry = _composition(path, effect, session_id="session-2", reopen=True)
    with pytest.raises(journal_effect.VerifySessionJournalEffectError) as failure:
        _submit(retry_main, retry, conflicting())  # type: ignore[operator]

    assert failure.value.reason is journal_effect.VerifySessionJournalEffectReason.JOURNAL_CONFLICT
    assert effect.calls == 1


def test_crash_after_durable_intent_leaves_reconcile_first_and_restart_never_redispatches(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")

    pending = _submit(main, composition, _request())
    assert pending.pending_effect is not None
    with patch.object(journal_effect, "_before_effect_invocation", side_effect=SystemExit("crash")):
        with pytest.raises(SystemExit, match="crash"):
            pending.pending_effect.consume()

    assert effect.calls == 0
    facts = SourceHistorySQLiteReader(path).query(_history(_request(), searched_last_generation=1))
    assert isinstance(facts, SourceHistoryMatched)
    assert facts.facts[0].conclusion == "dispatch_not_observed"

    replay_main, replay = _composition(path, effect, session_id="session-2", reopen=True)
    exchange = _submit(replay_main, replay, _redelivery())

    assert exchange.disposition == "reconcile_first"
    assert len(exchange.outbound_frames) == 2
    assert effect.calls == 0
    assert replay_main.feed(exchange.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"
    assert replay_main.feed(exchange.outbound_frames[1])[0].payload.reconciliation_fact == "dispatch_not_observed"


@pytest.mark.parametrize("failure", (False, True), ids=("result", "failure"))
def test_terminal_frames_are_encoded_only_after_the_matching_observation_is_durable(
    tmp_path: Path,
    failure: bool,
) -> None:
    effect = _Effect(failure=failure)
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")
    encode_name = "encode_failure" if failure else "encode_result"

    with patch.object(journal_engine, "_record_observation", side_effect=RuntimeError("database unavailable")):
        with patch.object(
            PostHandshakeVerifySessionSession,
            encode_name,
            side_effect=AssertionError("terminal was encoded before durable observation"),
        ):
            with pytest.raises(journal_effect.VerifySessionJournalEffectError) as write_failure:
                _consume_pending_effect(_submit(main, composition, _request()))

    assert write_failure.value.reason is journal_effect.VerifySessionJournalEffectReason.JOURNAL_ERROR
    assert effect.calls == 1


def test_accepted_ack_is_encoded_only_after_the_acceptance_receipt_is_durable(tmp_path: Path) -> None:
    effect = _Effect()
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")

    with patch.object(journal_engine, "_record_accepted", side_effect=RuntimeError("database unavailable")):
        with patch.object(
            PostHandshakeVerifySessionSession,
            "encode_accepted_ack",
            side_effect=AssertionError("accepted ack was encoded before durable acceptance"),
        ):
            with pytest.raises(journal_effect.VerifySessionJournalEffectError) as write_failure:
                _submit(main, composition, _request())

    assert write_failure.value.reason is journal_effect.VerifySessionJournalEffectReason.JOURNAL_ERROR
    assert effect.calls == 0


def test_failure_effect_is_durably_observed_before_its_terminal_frame(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect(failure=True)
    main, composition = _composition(path, effect, session_id="session-1")

    accepted = _submit(main, composition, _request())
    main.feed(accepted.outbound_frames[0])
    exchange = _consume_pending_effect(accepted)

    assert exchange.disposition == "observed_failure"
    assert main.feed(exchange.outbound_frames[0])[0].payload.failure_fact == "no_effect_performed"
    facts = SourceHistorySQLiteReader(path).query(_history(_request(), searched_last_generation=1))
    assert isinstance(facts, SourceHistoryMatched)
    assert facts.facts[0].conclusion == "observed_failure"


def test_effect_errors_and_composition_surfaces_never_leak_the_raw_fence_bearer(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class LeakingEffect:
        def __call__(self, request: VerifySessionRequestV1, deadline_at: float) -> VerifySessionResultV1:
            del deadline_at
            raise RuntimeError(request.runtime_attempt_fence_token)

    main, composition = _composition(tmp_path / "journal.sqlite3", LeakingEffect(), session_id="session-1")
    pending = _submit(main, composition, _request())
    with pytest.raises(journal_effect.VerifySessionJournalEffectError) as error:
        _consume_pending_effect(pending)

    logger = logging.getLogger("seektalent.source_port.verify_session_journal_effect")
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=logger.name):
        logger.error("verify_session_journal_effect_failed", exc_info=error.value)
    surfaces = "\n".join(
        (str(error.value), repr(error.value), repr(error.value.args), repr(error.value.__context__), caplog.text)
    )
    assert RAW_FENCE_TOKEN not in surfaces
    assert error.value.reason is journal_effect.VerifySessionJournalEffectReason.EFFECT_FAILED


def test_journal_effect_composition_is_factory_only_and_cannot_accept_a_forged_execution_claim(
    tmp_path: Path,
) -> None:
    effect = _Effect()
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")
    forged = object.__new__(journal_effect.VerifySessionJournalEffectComposition)
    pending = _submit(main, composition, _request())
    forged_authority = object.__new__(journal_effect.VerifySessionPendingEffectAuthority)

    with pytest.raises(TypeError, match="factory"):
        forged.feed(b"not a frame")
    with pytest.raises(TypeError, match="factory"):
        forged_authority.consume()
    with pytest.raises(TypeError, match="factory"):
        journal_effect.VerifySessionJournalEffectComposition()  # type: ignore[call-arg]
    with pytest.raises(VerifySessionFrameError) as no_bypass:
        composition.feed(b"not a frame")
    assert no_bypass.value.reason_code == VerifySessionFrameReason.FRAME_TOO_LARGE.value
    assert effect.calls == 0
    assert main.closed is False
    assert pending.pending_effect is not None


def test_only_bootstrap_and_transport_are_the_only_composition_callers_without_wtscli_or_browser() -> None:
    project_root = Path(__file__).parents[1]
    composition_modules = {
        project_root / "src" / "seektalent" / "source_port" / "verify_session_journal_effect.py",
        project_root / "src" / "seektalent" / "source_port" / "verify_session_journal_effect_durable.py",
        project_root / "src" / "seektalent" / "source_port" / "verify_session_pending_effect.py",
    }
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(composition_modules))
    all_source = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "src").rglob("*.py"))

    assert all(token not in source.lower() for token in ("wtscli", "opencli", "playwright", "selenium"))
    callers = [
        path.relative_to(project_root).as_posix()
        for path in (project_root / "src").rglob("*.py")
        if path not in composition_modules and "verify_session_journal_effect" in path.read_text(encoding="utf-8")
    ]
    assert callers == [
        "src/seektalent/sidecar_bootstrap.py",
        "src/seektalent/source_port/sidecar_transport.py",
    ]
    bootstrap = (project_root / "src" / "seektalent" / "sidecar_bootstrap.py").read_text(encoding="utf-8")
    transport = (project_root / "src" / "seektalent" / "source_port" / "sidecar_transport.py").read_text(
        encoding="utf-8"
    )
    assert "--test-only-verify-session-journal" in bootstrap
    assert "test-only-liepin_execution_sidecar-source-" in bootstrap
    assert "serve_test_source_port" in transport
    assert "verify_session_journal_effect" in all_source
