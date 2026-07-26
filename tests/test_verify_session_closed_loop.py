from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import seektalent.sidecar_readiness as readiness
from seektalent.source_port import sidecar_transport
from seektalent.source_port.authenticated_verify_session_frames import (
    ReceivedVerifySessionAcceptedAck,
    ReceivedVerifySessionReconcileRequired,
    ReceivedVerifySessionRejected,
    ReceivedVerifySessionResult,
    VerifySessionAcceptedAckV1,
)
from seektalent.source_port.command_journal import (
    create_command_journal,
    open_command_journal,
)
from seektalent.source_port.history_contract import (
    ExactAuthorizationSelector,
    SourceHistoryQueryV1,
)
from seektalent.source_port.history_sqlite_reader import (
    SourceHistorySQLiteReader,
)
from seektalent.source_port.operation_dispatch import runtime_attempt_fence_ref
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1, VerifySessionResultV1
from seektalent.source_port.verify_session_journal_effect import (
    VerifySessionJournalEffectError,
    create_verify_session_journal_effect_composition,
)
from seektalent_runtime_control.source_operations import validate_source_dispatch_ack
from seektalent.verify_session_closed_loop import (
    VerifySessionLiveAuthority,
    _record_authenticated_ack,
    deliver_verify_session_outbox,
)
from seektalent.wtscli_connection_supervisor import WtsCliConnectionReceipt
from tests.test_sidecar_readiness import _connected_process, _identity
from tests.test_source_history_reconciliation import _store_with_operation
from tests.test_source_port_transport import (
    _verify_request,
    lease_factory as _transport_lease_factory,
)
from tests.test_source_port_verify_session_continuity_admission import (
    RAW_FENCE_REPLAY,
    _accepted_ack_bytes,
    _accepted_command,
    _main,
    _safe_retry_request,
    _seed_ordinal_one,
    _sidecar,
)


class _EffectCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(
        self,
        request: VerifySessionRequestV1,
        deadline_at: float,
    ) -> VerifySessionResultV1:
        del deadline_at
        self.count += 1
        return VerifySessionResultV1.model_validate(
            {
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
                "actual_profile_binding_ref": request.profile_binding_ref,
                "actual_provider_account_ref": request.provider_account_ref,
                "actual_profile_binding_generation": request.identity.profile_binding_generation,
                "safe_reason_code": None,
                "user_action": None,
                "component_receipt_refs": request.component_receipt_refs,
            },
            strict=True,
        )


@pytest.fixture
def ready_lease_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    return _transport_lease_factory.__wrapped__(tmp_path, monkeypatch)


def _submit(
    path: Path,
    request: VerifySessionRequestV1,
    effect: _EffectCounter,
    *,
    session_id: str,
):
    main = _main(session_id=session_id)
    composition = create_verify_session_journal_effect_composition(
        command_journal_session=open_command_journal(path).start(),
        frame_session=_sidecar(session_id=session_id),
        effect=effect,
    )
    exchange = composition.feed(
        main.encode_submit(
            message_id=f"{session_id}-submit",
            correlation_id=request.identity.correlation_id,
            payload=request,
        )
    )
    received = tuple(message for frame in exchange.outbound_frames for message in main.feed(frame))
    return main, composition, exchange, received


def test_new_safe_retry_epoch_runs_one_effect_and_redelivery_only_replays_terminal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _seed_ordinal_one(path)
    effect = _EffectCounter()
    request = _safe_retry_request()

    main, composition, accepted, received = _submit(
        path,
        request,
        effect,
        session_id="closed-loop-initial",
    )
    assert accepted.disposition == "pending_effect"
    assert len(received) == 1
    assert isinstance(received[0], ReceivedVerifySessionAcceptedAck)
    assert accepted.pending_effect is not None
    terminal = accepted.pending_effect.consume()
    terminal_received = tuple(message for frame in terminal.outbound_frames for message in main.feed(frame))
    assert terminal.disposition == "observed_result"
    assert len(terminal_received) == 1
    assert isinstance(terminal_received[0], ReceivedVerifySessionResult)
    assert effect.count == 1
    composition.close()

    redelivery = _safe_retry_request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token=RAW_FENCE_REPLAY,
        deadline_value=30_000,
    )
    _, replay_composition, replay, replay_received = _submit(
        path,
        redelivery,
        effect,
        session_id="closed-loop-redelivery",
    )
    assert replay.disposition == "terminal_replay"
    assert [type(message) for message in replay_received] == [
        ReceivedVerifySessionAcceptedAck,
        ReceivedVerifySessionResult,
    ]
    assert replay.pending_effect is None
    assert effect.count == 1
    replay_composition.close()


def test_safe_retry_acceptance_crash_before_dispatch_redelivers_accepted_no_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _seed_ordinal_one(path)
    effect = _EffectCounter()
    request = _safe_retry_request()

    from seektalent.source_port import verify_session_journal_effect as journal_effect

    original = journal_effect._record_dispatch_intent

    def crash_before_dispatch(*args: object, **kwargs: object):
        raise VerifySessionJournalEffectError(journal_effect.VerifySessionJournalEffectReason.JOURNAL_ERROR)

    monkeypatch.setattr(journal_effect, "_record_dispatch_intent", crash_before_dispatch)
    with pytest.raises(VerifySessionJournalEffectError):
        _submit(path, request, effect, session_id="closed-loop-crash")
    monkeypatch.setattr(journal_effect, "_record_dispatch_intent", original)

    redelivery = _safe_retry_request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token=RAW_FENCE_REPLAY,
        deadline_value=30_000,
    )
    _, composition, replay, received = _submit(
        path,
        redelivery,
        effect,
        session_id="closed-loop-after-crash",
    )
    assert replay.disposition == "reconcile_first"
    assert [type(message) for message in received] == [
        ReceivedVerifySessionAcceptedAck,
        ReceivedVerifySessionReconcileRequired,
    ]
    assert received[1].payload.reconciliation_fact == "accepted_no_dispatch"
    assert effect.count == 0
    composition.close()


def test_main_ack_contract_accepts_safe_retry_epoch() -> None:
    validate_source_dispatch_ack(
        runtime_run_id="run-1",
        operation_id="verify-session-1",
        outbox_id="outbox-2",
        canonical_request_hash="a" * 64,
        dispatch_intent_id="dispatch-intent-2",
        dispatch_intent_revision=2,
        dispatch_intent_digest="b" * 64,
        dispatch_authorization_ordinal=2,
        expected_outbox_revision=1,
        accepted_sidecar_generation=2,
        accepted_sidecar_journal_revision=2,
        ack_ref=f"sha256:{'c' * 64}",
        ack_kind="new_dispatch_authorization",
        acknowledged_at="2026-07-26T00:00:00Z",
    )


def test_two_initial_dispatchers_create_one_effect_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _seed_ordinal_one(path)
    effect = _EffectCounter()
    request = _safe_retry_request()

    def submit(index: int):
        return _submit(
            path,
            request,
            effect,
            session_id=f"closed-loop-dispatcher-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        exchanges = tuple(executor.map(submit, range(2)))

    assert sorted(exchange[2].disposition for exchange in exchanges) == [
        "pending_effect",
        "reconcile_first",
    ]
    pending = next(exchange[2] for exchange in exchanges if exchange[2].pending_effect is not None)
    assert pending.pending_effect is not None
    pending.pending_effect.consume()
    assert effect.count == 1
    for _, composition, _, _ in exchanges:
        composition.close()


def test_main_ack_commit_response_loss_redelivery_returns_exact_row(
    tmp_path: Path,
) -> None:
    request = _verify_request(
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        idempotency_key="source-key-1",
        accepted_requirement_revision_id="requirement-1",
        correlation_id="ack-loss-initial",
        runtime_attempt_fence_token="ack-loss-fence-" + "x" * 64,
        browser_control_scope_id="browser-scope-1",
        dispatch_intent_id="dispatch-intent-1",
        source_operation_acceptance_ref="source-acceptance-ref-1",
        profile_binding_ref="profile-binding-1",
        provider_account_ref="provider-account-1",
        required_capabilities=("bridge", "extension"),
    )
    query = SourceHistoryQueryV1.model_validate(
        {
            "contract_version": "seektalent.source-port.query.request/v1",
            "run_id": request.identity.run_id,
            "operation_id": request.identity.operation_id,
            "source": "liepin",
            "operation_kind": "verify_session",
            "idempotency_key": request.identity.idempotency_key,
            "request_hash": request.identity.request_hash,
            "attempt_no": 1,
            "authorization_selector": {"kind": "exact", "ordinal": 1},
            "accepted_generation_hint": None,
            "searched_first_generation": 1,
            "searched_last_generation": 1,
            "expected_source_operation_ledger_revision": 1,
            "expected_reconciliation_revision": 0,
        },
        strict=True,
    )
    store = _store_with_operation(
        tmp_path,
        query,
        acknowledge=False,
        acceptance_changes={
            "canonical_request_hash": request.identity.request_hash,
            "runtime_attempt_fence_ref": (request.identity.runtime_attempt_fence_ref),
            "controller_fence_ref": None,
            "dispatch_intent_digest": (request.delivery.authorization.dispatch_intent_digest),
        },
    )
    main = _main(session_id="ack-loss-session")
    sidecar = _sidecar(session_id="ack-loss-session")
    submit = main.encode_submit(
        message_id="ack-loss-submit",
        correlation_id=request.identity.correlation_id,
        payload=request,
    )
    sidecar.feed(submit)
    ack_payload = VerifySessionAcceptedAckV1.model_validate_json(
        _accepted_ack_bytes(request),
        strict=True,
    )
    received = main.feed(
        sidecar.encode_accepted_ack(
            message_id="ack-loss-ack",
            reply_to="ack-loss-submit",
            payload=ack_payload,
        )
    )
    assert len(received) == 1
    assert isinstance(received[0], ReceivedVerifySessionAcceptedAck)

    context = store.get_accepted_source_operation_context(
        request.identity.run_id,
        request.identity.operation_id,
    )
    committed_before_response_loss = _record_authenticated_ack(
        store,
        context,
        request,
        received[0],
        acknowledged_at="2026-07-26T00:00:00Z",
    )
    redelivery = _verify_request(
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        idempotency_key="source-key-1",
        accepted_requirement_revision_id="requirement-1",
        correlation_id="ack-loss-redelivery",
        runtime_attempt_fence_token="ack-loss-replay-" + "z" * 64,
        browser_control_scope_id="browser-scope-1",
        dispatch_intent_id="dispatch-intent-1",
        source_operation_acceptance_ref="source-acceptance-ref-1",
        profile_binding_ref="profile-binding-1",
        provider_account_ref="provider-account-1",
        required_capabilities=("bridge", "extension"),
        delivery_mode="outbox_redelivery",
        deadline_value=30_000,
    )
    committed_after_redelivery = _record_authenticated_ack(
        store,
        store.get_accepted_source_operation_context(
            request.identity.run_id,
            request.identity.operation_id,
        ),
        redelivery,
        received[0],
        acknowledged_at="2026-07-26T00:00:01Z",
    )

    assert committed_after_redelivery == committed_before_response_loss
    assert committed_after_redelivery.acknowledged_at == "2026-07-26T00:00:00Z"


@pytest.mark.parametrize(
    "case_request",
    (
        _safe_retry_request(
            delivery_mode="outbox_redelivery",
            runtime_attempt_fence_token=RAW_FENCE_REPLAY,
        ),
        _safe_retry_request(
            attempt_no=3,
            dispatch_authorization_ordinal=3,
            dispatch_intent_id="dispatch-intent-3",
            dispatch_intent_revision=3,
            safe_retry_commit_ref="safe-retry-commit-3",
            expected_source_operation_ledger_revision=3,
            expected_reconciliation_revision=2,
        ),
    ),
)
def test_redelivery_cannot_create_and_ordinal_gap_is_rejected_without_effect(
    tmp_path: Path,
    case_request: VerifySessionRequestV1,
) -> None:
    path = tmp_path / "journal.sqlite3"
    _seed_ordinal_one(path)
    effect = _EffectCounter()

    _, composition, exchange, received = _submit(
        path,
        case_request,
        effect,
        session_id=(f"closed-loop-rejected-{case_request.delivery.authorization.dispatch_authorization_ordinal}"),
    )

    assert exchange.disposition == "rejected"
    assert len(received) == 1
    assert isinstance(received[0], ReceivedVerifySessionRejected)
    assert effect.count == 0
    composition.close()


def test_main_outbox_authenticated_sidecar_history_and_reconciliation_close_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory,
) -> None:
    request = _verify_request(
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        idempotency_key="source-key-1",
        accepted_requirement_revision_id="requirement-1",
        correlation_id="closed-loop-correlation",
        runtime_attempt_fence_token="closed-loop-fence-" + "x" * 64,
        browser_control_scope_id="browser-scope-1",
        dispatch_intent_id="dispatch-intent-1",
        source_operation_acceptance_ref="source-acceptance-ref-1",
        profile_binding_ref="profile-binding-1",
        provider_account_ref="provider-account-1",
        required_capabilities=("bridge", "extension"),
        component_receipt_refs=("component-receipt-1",),
    )
    authorization = request.delivery.authorization
    query = SourceHistoryQueryV1.model_validate(
        {
            "contract_version": "seektalent.source-port.query.request/v1",
            "run_id": request.identity.run_id,
            "operation_id": request.identity.operation_id,
            "source": "liepin",
            "operation_kind": "verify_session",
            "idempotency_key": request.identity.idempotency_key,
            "request_hash": request.identity.request_hash,
            "attempt_no": 1,
            "authorization_selector": ExactAuthorizationSelector(
                kind="exact",
                ordinal=1,
            ),
            "accepted_generation_hint": None,
            "searched_first_generation": 1,
            "searched_last_generation": 1,
            "expected_source_operation_ledger_revision": 1,
            "expected_reconciliation_revision": 0,
        },
        strict=True,
    )
    store = _store_with_operation(
        tmp_path / "main",
        query,
        acknowledge=False,
        acceptance_changes={
            "canonical_request_hash": request.identity.request_hash,
            "runtime_attempt_fence_ref": request.identity.runtime_attempt_fence_ref,
            "controller_fence_ref": None,
            "dispatch_intent_digest": authorization.dispatch_intent_digest,
        },
    )
    journal_path = tmp_path / "sidecar-journal.sqlite3"
    effect = _EffectCounter()
    readiness_calls: list[float] = []

    class Supervisor:
        def await_ready(self, *, timeout_seconds: float):
            readiness_calls.append(timeout_seconds)
            return WtsCliConnectionReceipt(
                daemon_build_id="seektalent-wtscli-test",
                extension_build_id="seektalent-wtscli-test",
                endpoint="127.0.0.1:19826",
                ownership_ref="sha256:" + "a" * 64,
                last_connected_at=1,
                elapsed_milliseconds=1,
            )

    def serve(result: readiness.SidecarHandshakeResult) -> None:
        journal = create_command_journal(journal_path)
        composition = create_verify_session_journal_effect_composition(
            command_journal_session=journal.start(),
            frame_session=result.source_port_session(),
            effect=effect,
        )
        try:
            sidecar_transport.serve_test_source_port(
                result,
                SourceHistorySQLiteReader(journal_path),
                composition,
                timeout=1,
            )
        finally:
            composition.close()
            journal.close()
            result.close()

    lease = ready_lease_factory()
    process, _, child_thread, errors, _ = _connected_process(
        lease,
        _identity(lease.admission),
        after_sidecar_result=serve,
    )
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    endpoint = readiness.spawn_ready_sidecar(lease, timeout=1)

    result = deliver_verify_session_outbox(
        store=store,
        endpoint=endpoint,
        runtime_run_id=request.identity.run_id,
        operation_id=request.identity.operation_id,
        live_authority=VerifySessionLiveAuthority(
            runtime_attempt_fence_token=request.runtime_attempt_fence_token,
            profile_binding_ref=request.profile_binding_ref,
            provider_account_ref=request.provider_account_ref,
            required_capabilities=request.required_capabilities,
            user_interaction_policy=request.user_interaction_policy,
            verify_search_surface=request.verify_search_surface,
            component_receipt_refs=request.component_receipt_refs,
        ),
        delivery_mode="initial",
        correlation_id=request.identity.correlation_id,
        deadline_milliseconds=request.identity.deadline.value,
        acknowledged_at="2026-07-26T00:00:00Z",
        committed_at="2026-07-26T00:00:01Z",
        timeout=1,
        connection_supervisor=Supervisor(),
    )

    assert result.disposition == "reconciled"
    assert result.reconciliation is not None
    assert result.reconciliation.history_conclusion == "observed_result"
    assert result.reconciliation.source_operation_disposition == "completed"
    assert effect.count == 1
    assert readiness_calls == [40]
    context = store.get_accepted_source_operation_context(
        request.identity.run_id,
        request.identity.operation_id,
    )
    assert context.dispatch.status == "acknowledged"
    assert context.dispatch.ack_ref is not None
    assert context.dispatch.ack_ref.startswith("sha256:")
    assert context.dispatch.ack_kind == "new_logical_operation"
    raw_fence = request.runtime_attempt_fence_token.encode()
    assert raw_fence not in store.path.read_bytes()
    assert raw_fence not in journal_path.read_bytes()
    assert request.runtime_attempt_fence_token not in repr(
        VerifySessionLiveAuthority(
            runtime_attempt_fence_token=request.runtime_attempt_fence_token,
            profile_binding_ref=request.profile_binding_ref,
            provider_account_ref=request.provider_account_ref,
            required_capabilities=request.required_capabilities,
            user_interaction_policy=request.user_interaction_policy,
            verify_search_surface=request.verify_search_surface,
        )
    )

    endpoint.close(1)
    child_thread.join(timeout=1)
    assert not child_thread.is_alive()
    assert errors == []
    ready_lease_factory().close()


def test_accepted_no_dispatch_authorizes_one_next_epoch_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory,
) -> None:
    first_request = _verify_request(
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        idempotency_key="source-key-1",
        accepted_requirement_revision_id="requirement-1",
        correlation_id="closed-loop-first",
        runtime_attempt_fence_token="closed-loop-first-fence-" + "x" * 64,
        browser_control_scope_id="browser-scope-1",
        dispatch_intent_id="dispatch-intent-1",
        source_operation_acceptance_ref="source-acceptance-ref-1",
        profile_binding_ref="profile-binding-1",
        provider_account_ref="provider-account-1",
        required_capabilities=("bridge", "extension"),
        component_receipt_refs=("component-receipt-1",),
    )
    authorization = first_request.delivery.authorization
    store_query = SourceHistoryQueryV1.model_validate(
        {
            "contract_version": "seektalent.source-port.query.request/v1",
            "run_id": first_request.identity.run_id,
            "operation_id": first_request.identity.operation_id,
            "source": "liepin",
            "operation_kind": "verify_session",
            "idempotency_key": first_request.identity.idempotency_key,
            "request_hash": first_request.identity.request_hash,
            "attempt_no": 1,
            "authorization_selector": {"kind": "exact", "ordinal": 1},
            "accepted_generation_hint": None,
            "searched_first_generation": 1,
            "searched_last_generation": 1,
            "expected_source_operation_ledger_revision": 1,
            "expected_reconciliation_revision": 0,
        },
        strict=True,
    )
    store = _store_with_operation(
        tmp_path / "main",
        store_query,
        acknowledge=False,
        acceptance_changes={
            "canonical_request_hash": first_request.identity.request_hash,
            "runtime_attempt_fence_ref": (first_request.identity.runtime_attempt_fence_ref),
            "controller_fence_ref": None,
            "dispatch_intent_digest": authorization.dispatch_intent_digest,
        },
    )
    journal_path = tmp_path / "sidecar-journal.sqlite3"
    journal = create_command_journal(journal_path)
    seed_session = journal.start()
    seed_session.record_accepted(
        _accepted_command(first_request),
        accepted_ack_bytes=_accepted_ack_bytes(first_request),
    )
    seed_session.close()
    journal.close()
    effect = _EffectCounter()

    def start_endpoint():
        def serve(result: readiness.SidecarHandshakeResult) -> None:
            current_journal = open_command_journal(journal_path)
            composition = create_verify_session_journal_effect_composition(
                command_journal_session=current_journal.start(),
                frame_session=result.source_port_session(),
                effect=effect,
            )
            try:
                sidecar_transport.serve_test_source_port(
                    result,
                    SourceHistorySQLiteReader(journal_path),
                    composition,
                    timeout=1,
                )
            finally:
                composition.close()
                current_journal.close()
                result.close()

        lease = ready_lease_factory()
        process, _, child_thread, errors, _ = _connected_process(
            lease,
            _identity(lease.admission),
            after_sidecar_result=serve,
        )
        monkeypatch.setattr(
            readiness,
            "spawn_owned_sidecar",
            lambda _: process,
        )
        endpoint = readiness.spawn_ready_sidecar(lease, timeout=1)
        return endpoint, child_thread, errors

    def close_endpoint(endpoint, child_thread, errors) -> None:
        endpoint.close(1)
        child_thread.join(timeout=1)
        assert not child_thread.is_alive()
        assert errors == []
        ready_lease_factory().close()

    live_authority = VerifySessionLiveAuthority(
        runtime_attempt_fence_token=(first_request.runtime_attempt_fence_token),
        profile_binding_ref=first_request.profile_binding_ref,
        provider_account_ref=first_request.provider_account_ref,
        required_capabilities=first_request.required_capabilities,
        user_interaction_policy=first_request.user_interaction_policy,
        verify_search_surface=first_request.verify_search_surface,
        component_receipt_refs=first_request.component_receipt_refs,
    )
    first_endpoint, first_thread, first_errors = start_endpoint()

    class RedeliverySupervisor:
        def await_ready(self, *, timeout_seconds: float):
            del timeout_seconds
            pytest.fail("durable redelivery must not start or wait for WTSCLI")

    first = deliver_verify_session_outbox(
        store=store,
        endpoint=first_endpoint,
        runtime_run_id=first_request.identity.run_id,
        operation_id=first_request.identity.operation_id,
        live_authority=live_authority,
        delivery_mode="outbox_redelivery",
        correlation_id="closed-loop-first-redelivery",
        deadline_milliseconds=30_000,
        acknowledged_at="2026-07-26T00:00:00Z",
        committed_at="2026-07-26T00:00:01Z",
        timeout=1,
        connection_supervisor=RedeliverySupervisor(),
    )
    assert first.reconciliation is not None
    assert first.reconciliation.history_conclusion == "accepted_no_dispatch"
    assert first.reconciliation.retry_posture == "safe_retry"
    assert effect.count == 0
    close_endpoint(first_endpoint, first_thread, first_errors)

    first_lease = store.acquire_executor_lease(
        runtime_run_id=first_request.identity.run_id,
        executor_id="executor-1",
        acquired_at="2026-07-26T00:00:02Z",
        lease_expires_at="2026-07-26T00:01:00Z",
    )
    store.release_executor_lease(
        runtime_run_id=first_request.identity.run_id,
        executor_id="executor-1",
        attempt_no=first_lease.attempt_no,
        released_at="2026-07-26T00:00:03Z",
    )
    second_lease = store.acquire_executor_lease(
        runtime_run_id=first_request.identity.run_id,
        executor_id="executor-1",
        acquired_at="2026-07-26T00:00:04Z",
        lease_expires_at="2026-07-26T00:01:00Z",
    )
    second_token = "closed-loop-second-fence-" + "y" * 64
    second_fence_ref = runtime_attempt_fence_ref(
        raw_runtime_attempt_fence_token=second_token,
        run_id=first_request.identity.run_id,
        operation_id=first_request.identity.operation_id,
        attempt_no=second_lease.attempt_no,
        request_hash=first_request.identity.request_hash,
        expected_source_operation_ledger_revision=(first.reconciliation.committed_ledger_revision + 1),
        expected_reconciliation_revision=(first.reconciliation.committed_reconciliation_revision),
    )
    retry_authority = store._mint_safe_retry_turnover_authority_for_test(
        runtime_run_id=first_request.identity.run_id,
        executor_id="executor-1",
        attempt_no=second_lease.attempt_no,
        observed_at="2026-07-26T00:00:05Z",
        runtime_attempt_authority_ref="runtime-attempt-authority-2",
        runtime_attempt_fence_ref=second_fence_ref,
        profile_binding_generation=2,
        browser_control_scope_id="browser-scope-2",
        controller_fence_ref=None,
    )
    retry_context = store.mint_safe_retry_dispatch_epoch(
        runtime_run_id=first_request.identity.run_id,
        operation_id=first_request.identity.operation_id,
        reconciliation_id=first.reconciliation.reconciliation_id,
        expected_reconciliation_ledger_revision=(first.reconciliation.committed_ledger_revision),
        expected_reconciliation_revision=(first.reconciliation.committed_reconciliation_revision),
        outbox_id="source-outbox-2",
        dispatch_intent_id="dispatch-intent-2",
        authority=retry_authority,
    )
    assert retry_context.dispatch.dispatch_authorization_ordinal == 2
    store.release_executor_lease(
        runtime_run_id=first_request.identity.run_id,
        executor_id="executor-1",
        attempt_no=second_lease.attempt_no,
        released_at="2026-07-26T00:00:05.500000Z",
    )

    second_endpoint, second_thread, second_errors = start_endpoint()
    second = deliver_verify_session_outbox(
        store=store,
        endpoint=second_endpoint,
        runtime_run_id=first_request.identity.run_id,
        operation_id=first_request.identity.operation_id,
        live_authority=VerifySessionLiveAuthority(
            runtime_attempt_fence_token=second_token,
            profile_binding_ref=first_request.profile_binding_ref,
            provider_account_ref=first_request.provider_account_ref,
            required_capabilities=first_request.required_capabilities,
            user_interaction_policy=(first_request.user_interaction_policy),
            verify_search_surface=first_request.verify_search_surface,
            component_receipt_refs=first_request.component_receipt_refs,
        ),
        delivery_mode="initial",
        correlation_id="closed-loop-second",
        deadline_milliseconds=30_000,
        acknowledged_at="2026-07-26T00:00:06Z",
        committed_at="2026-07-26T00:00:07Z",
        timeout=1,
    )
    assert second.reconciliation is not None
    assert second.reconciliation.history_conclusion == "observed_result"
    assert second.reconciliation.source_operation_disposition == "completed"
    assert second.dispatch.ack_kind == "new_dispatch_authorization"
    assert effect.count == 1
    close_endpoint(second_endpoint, second_thread, second_errors)
