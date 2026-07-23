from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import logging
from pathlib import Path
import pickle
import sqlite3
import threading
from unittest.mock import patch

import pytest

import seektalent.source_port._command_journal_engine as journal_engine
import seektalent.source_port.verify_session_journal_effect as journal_effect
from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
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

    def __call__(self, request: VerifySessionRequestV1) -> VerifySessionResultV1 | VerifySessionFailureV1:
        self.calls += 1
        return _failure(request) if self.failure else _result(request)


def _composition(
    path: Path,
    effect: journal_effect.VerifySessionEffect,
    *,
    session_id: str,
    reopen: bool = False,
) -> tuple[
    PostHandshakeVerifySessionSession,
    journal_effect.VerifySessionJournalEffectComposition,
]:
    journal = open_command_journal(path) if reopen else create_command_journal(path)
    main, sidecar = _sessions(session_id)
    return main, journal_effect.create_verify_session_journal_effect_composition(
        command_journal_session=journal.start(),
        frame_session=sidecar,
        effect=effect,
    )


def _submit(
    main: PostHandshakeVerifySessionSession,
    composition: journal_effect.VerifySessionJournalEffectComposition,
    request: VerifySessionRequestV1,
) -> journal_effect.VerifySessionJournalEffectExchange:
    return composition.feed(
        main.encode_submit(
            message_id="submit-1",
            correlation_id=request.identity.correlation_id,
            payload=request,
        )
    )


def test_first_submit_returns_sealed_created_receipts_and_durable_ack_then_terminal(tmp_path: Path) -> None:
    effect = _Effect()
    main, composition = _composition(tmp_path / "journal.sqlite3", effect, session_id="session-1")

    exchange = _submit(main, composition, _request())

    assert effect.calls == 1
    assert exchange.disposition == "observed_result"
    assert len(exchange.outbound_frames) == 2
    assert tuple(receipt.disposition for receipt in exchange.receipts) == (
        CommandJournalTransitionDisposition.CREATED,
        CommandJournalTransitionDisposition.CREATED,
        CommandJournalTransitionDisposition.CREATED,
    )
    assert all(isinstance(receipt, CommandJournalTransitionReceipt) for receipt in exchange.receipts)
    assert [receipt.revision for receipt in exchange.receipts] == [1, 2, 3]
    assert [receipt.startup_generation for receipt in exchange.receipts] == [1, 1, 1]
    assert RAW_FENCE_TOKEN not in repr(exchange.receipts[0])
    assert main.feed(exchange.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"
    assert main.feed(exchange.outbound_frames[1])[0].payload.session_readiness == "ready"


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
    _submit(main, composition, _request())

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
    _submit(main, composition, _request())

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
    assert SourceHistorySQLiteReader(path).query(_history(_request(), searched_last_generation=1)).outcome == "not_found"


def test_exact_replay_replays_durable_ack_and_terminal_without_effect(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    first_main, first = _composition(path, effect, session_id="session-1")
    _submit(first_main, first, _request())

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
    assert len(lost.outbound_frames) == 2

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
    assert len(exchange.outbound_frames) == 1
    assert effect.calls == 0


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
    assert len(exchange.outbound_frames) == 1
    assert effect.calls == 0


def test_outbox_redelivery_cannot_extend_the_durable_deadline(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")
    _submit(main, composition, _request())

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
        def __call__(self, request: VerifySessionRequestV1) -> VerifySessionResultV1:
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return _result(request)

    effect = BlockingEffect()
    first_main, first = _composition(path, effect, session_id="session-1")
    second_main, second = _composition(path, effect, session_id="session-2", reopen=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_submit, first_main, first, _request())
        assert entered.wait(timeout=5)
        second_future = executor.submit(_submit, second_main, second, _request())
        second_exchange = second_future.result(timeout=5)
        release.set()
        first_exchange = first_future.result(timeout=5)

    assert effect.calls == 1
    assert first_exchange.disposition == "observed_result"
    assert second_exchange.disposition == "reconcile_first"
    assert len(second_exchange.outbound_frames) == 1
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
    _submit(main, composition, _request())

    retry_main, retry = _composition(path, effect, session_id="session-2", reopen=True)
    with pytest.raises(journal_effect.VerifySessionJournalEffectError) as failure:
        _submit(retry_main, retry, conflicting())  # type: ignore[operator]

    assert failure.value.reason is journal_effect.VerifySessionJournalEffectReason.JOURNAL_CONFLICT
    assert effect.calls == 1


def test_crash_after_durable_intent_leaves_reconcile_first_and_restart_never_redispatches(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    effect = _Effect()
    main, composition = _composition(path, effect, session_id="session-1")

    with patch.object(journal_effect, "_before_effect_invocation", side_effect=SystemExit("crash")):
        with pytest.raises(SystemExit, match="crash"):
            _submit(main, composition, _request())

    assert effect.calls == 0
    facts = SourceHistorySQLiteReader(path).query(_history(_request(), searched_last_generation=1))
    assert isinstance(facts, SourceHistoryMatched)
    assert facts.facts[0].conclusion == "dispatch_not_observed"

    replay_main, replay = _composition(path, effect, session_id="session-2", reopen=True)
    exchange = _submit(replay_main, replay, _redelivery())

    assert exchange.disposition == "reconcile_first"
    assert len(exchange.outbound_frames) == 1
    assert effect.calls == 0
    assert replay_main.feed(exchange.outbound_frames[0])[0].payload.accepted_fact == "dispatch_authorized"


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
                _submit(main, composition, _request())

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

    exchange = _submit(main, composition, _request())

    assert exchange.disposition == "observed_failure"
    main.feed(exchange.outbound_frames[0])
    assert main.feed(exchange.outbound_frames[1])[0].payload.failure_fact == "no_effect_performed"
    facts = SourceHistorySQLiteReader(path).query(_history(_request(), searched_last_generation=1))
    assert isinstance(facts, SourceHistoryMatched)
    assert facts.facts[0].conclusion == "observed_failure"


def test_effect_errors_and_composition_surfaces_never_leak_the_raw_fence_bearer(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class LeakingEffect:
        def __call__(self, request: VerifySessionRequestV1) -> VerifySessionResultV1:
            raise RuntimeError(request.runtime_attempt_fence_token)

    main, composition = _composition(tmp_path / "journal.sqlite3", LeakingEffect(), session_id="session-1")
    with pytest.raises(journal_effect.VerifySessionJournalEffectError) as error:
        _submit(main, composition, _request())

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

    with pytest.raises(TypeError, match="factory"):
        forged.feed(b"not a frame")
    with pytest.raises(TypeError, match="factory"):
        journal_effect.VerifySessionJournalEffectComposition()  # type: ignore[call-arg]
    with pytest.raises(VerifySessionFrameError) as no_bypass:
        composition.feed(b"not a frame")
    assert no_bypass.value.reason_code == VerifySessionFrameReason.FRAME_TOO_LARGE.value
    assert effect.calls == 0
    assert main.closed is False


def test_production_unreachable_composition_has_no_wtscli_browser_or_runtime_caller() -> None:
    project_root = Path(__file__).parents[1]
    source = (project_root / "src" / "seektalent" / "source_port" / "verify_session_journal_effect.py").read_text(
        encoding="utf-8"
    )
    all_source = "\n".join(path.read_text(encoding="utf-8") for path in (project_root / "src").rglob("*.py"))

    assert all(token not in source.lower() for token in ("wtscli", "opencli", "playwright", "selenium"))
    callers = [
        path.relative_to(project_root).as_posix()
        for path in (project_root / "src").rglob("*.py")
        if path.name != "verify_session_journal_effect.py"
        and "verify_session_journal_effect" in path.read_text(encoding="utf-8")
    ]
    assert callers == []
    assert "verify_session_journal_effect" in all_source
