from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

import seektalent.installed_release as installed_release
import seektalent.sidecar_child_session as child_session_module
import seektalent.sidecar_readiness as readiness
from seektalent.installed_slot import (
    ActiveSlotPointerV1,
    InstalledSidecarLaunchLease,
    acquire_installed_sidecar_launch_lease,
    canonical_active_slot_pointer_bytes,
)
from seektalent.source_port import sidecar_transport
from seektalent.source_port.authenticated_history_frames import ReceivedHistoryQuery
from seektalent.source_port.authenticated_source_port_session import (
    PostHandshakeSourcePortSession,
    SourcePortTransportFrameError,
)
from seektalent.source_port.authenticated_verify_session_frames import (
    ReceivedVerifySessionAcceptedAck,
    ReceivedVerifySessionRejected,
    ReceivedVerifySessionResult,
    ReceivedVerifySessionSubmit,
    ReceivedVerifySessionReconcileRequired,
    VerifySessionRejectedV1,
)
from seektalent.source_port.command_journal import create_command_journal, open_command_journal
from seektalent.source_port.history_contract import SourceHistoryMatched, SourceHistoryNotFound
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1, VerifySessionResultV1
from seektalent.source_port.verify_session_journal_effect import create_verify_session_journal_effect_composition
import seektalent.source_port.verify_session_journal_effect as journal_effect
from seektalent.release_manifest import parse_release_manifest
from tests.test_sidecar_readiness import (
    _connected_process,
    _history_query,
    _history_reader,
    _identity,
)
from tests.test_installed_release import _install_slot
from tests.test_release_signing import VERIFICATION_TIME, _policy, _signed
from tests.test_source_history_sqlite_harness import _query as _sqlite_query


@pytest.fixture
def lease_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], InstalledSidecarLaunchLease]:
    source_slot, _, _ = _install_slot(tmp_path, monkeypatch, executable_bytes=b"transport-probe\n")
    installation_root = tmp_path / "installation"
    slot_root = installation_root / "slots" / "A"
    slot_root.parent.mkdir(parents=True)
    source_slot.rename(slot_root)
    manifest_path = slot_root / "release" / "release-manifest.json"
    manifest = parse_release_manifest(manifest_path.read_bytes())
    _, signature_payload = _signed(manifest)
    manifest_path.parent.joinpath("signatures").mkdir()
    manifest_path.parent.joinpath("signatures", "release-manifest.sig").write_text(
        json.dumps(signature_payload, separators=(",", ":")), encoding="utf-8"
    )
    control = installation_root / "control"
    control.mkdir()
    control.joinpath("installation-id").write_bytes(b"transport-test-installation")
    control.joinpath("active-slot.lock").write_bytes(b"0")
    control.joinpath("slot-A.lock").write_bytes(b"0")
    control.joinpath("slot-B.lock").write_bytes(b"0")
    pointer = ActiveSlotPointerV1.model_construct(
        schema_version="seektalent.active-slot/v1",
        installation_id="transport-test-installation",
        physical_slot="A",
        pointer_generation=1,
        product_build_id=manifest.product_build_id,
        release_manifest_sha256=installed_release.release_manifest_digest(manifest),
        committed_at="2026-07-21T12:00:00Z",
    )
    control.joinpath("active-slot.json").write_bytes(canonical_active_slot_pointer_bytes(pointer))
    return lambda: acquire_installed_sidecar_launch_lease(installation_root, _policy(), VERIFICATION_TIME)


def _verify_request(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "run_id": "run-shared-1",
        "operation_id": "verify-shared-1",
        "attempt_no": 1,
        "idempotency_key": "verify-shared-key-1",
        "correlation_id": "verify-shared-correlation-1",
        "accepted_requirement_revision_id": "requirement-shared-1",
        "runtime_attempt_fence_token": "shared-transport-fence-" + "x" * 64,
        "profile_binding_generation": 1,
        "browser_control_scope_id": "browser-scope-shared-1",
        "deadline_value": 60_000,
        "expected_source_operation_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
        "delivery_mode": "initial",
        "dispatch_intent_id": "dispatch-intent-shared-1",
        "dispatch_intent_revision": 1,
        "source_operation_acceptance_ref": "source-acceptance-shared-1",
        "profile_binding_ref": "profile-binding-shared-1",
        "provider_account_ref": "provider-account-shared-1",
        "required_capabilities": ("bridge", "extension"),
        "user_interaction_policy": "observe_only",
        "verify_search_surface": True,
        "component_receipt_refs": ("main-receipt-shared-1",),
    }
    values.update(updates)
    return VerifySessionRequestV1.create(**values)


def _redelivery() -> VerifySessionRequestV1:
    return _verify_request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token="shared-transport-redelivery-fence-" + "y" * 64,
        correlation_id="verify-shared-redelivery-correlation-1",
        browser_control_scope_id="browser-scope-shared-redelivery-1",
        deadline_value=59_999,
    )


def _verify_result(request: VerifySessionRequestV1) -> VerifySessionResultV1:
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


def _shared_source_port_pair() -> tuple[PostHandshakeSourcePortSession, PostHandshakeSourcePortSession]:
    values = {
        "session_id": "a" * 32,
        "protocol_minor": 0,
        "main_to_sidecar_key": bytes(range(32)),
        "sidecar_to_main_key": bytes(range(32, 64)),
    }
    return PostHandshakeSourcePortSession.for_main(**values), PostHandshakeSourcePortSession.for_sidecar(**values)


class _RecordingEffect:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: VerifySessionRequestV1) -> VerifySessionResultV1:
        self.calls += 1
        return _verify_result(request)


class _InterruptAfterIntent(RuntimeError):
    pass


def _journal_phase(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT phase FROM source_history_heads").fetchone()
    assert row is not None
    return row[0]


def _start_verify_sidecar(
    *,
    lease: InstalledSidecarLaunchLease,
    journal_path: Path,
    effect: _RecordingEffect,
    monkeypatch: pytest.MonkeyPatch,
    suppress: type[BaseException] | None = None,
) -> tuple[readiness.ReadySidecarSession, threading.Thread, list[BaseException]]:
    def serve(result: readiness.SidecarHandshakeResult) -> None:
        journal = open_command_journal(journal_path) if journal_path.exists() else create_command_journal(journal_path)
        composition = create_verify_session_journal_effect_composition(
            command_journal_session=journal.start(),
            frame_session=result.source_port_session(),
            effect=effect,
        )
        try:
            sidecar_transport.serve_test_source_port(result, object(), composition, timeout=1)
        except BaseException as error:
            if suppress is None or type(error) is not suppress:
                raise
        finally:
            composition.close()
            journal.close()
            result.close()

    process, _, thread, errors, _ = _connected_process(
        lease,
        _identity(lease.admission),
        after_sidecar_result=serve,
    )
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    return readiness.spawn_ready_sidecar(lease, timeout=1), thread, errors


def test_shared_post_handshake_session_has_one_sequence_and_message_id_space_across_families() -> None:
    main, sidecar = _shared_source_port_pair()
    history = _history_query()
    verify = _verify_request()

    history_frame = main.encode_query(
        message_id="history-message-1",
        correlation_id="history-correlation-1",
        payload=history,
    )
    verify_frame = main.encode_submit(
        message_id="verify-message-1",
        correlation_id="verify-correlation-1",
        payload=verify,
    )

    assert sidecar.feed(history_frame[:7]) == ()
    received = sidecar.feed(history_frame[7:] + verify_frame)
    assert isinstance(received[0], ReceivedHistoryQuery)
    assert received[0].payload == history
    assert received[1].payload == verify

    with pytest.raises(SourcePortTransportFrameError) as duplicate:
        main.encode_submit(
            message_id="history-message-1",
            correlation_id="verify-correlation-2",
            payload=verify,
        )
    assert duplicate.value.reason_code == "source_port_duplicate_message_id"


def test_shared_post_handshake_session_accepts_verify_then_history_and_closes_on_partial_tail() -> None:
    main, sidecar = _shared_source_port_pair()
    history = _history_query()
    verify = _verify_request()

    verify_frame = main.encode_submit(
        message_id="verify-message-1",
        correlation_id="verify-correlation-1",
        payload=verify,
    )
    history_frame = main.encode_query(
        message_id="history-message-1",
        correlation_id="history-correlation-1",
        payload=history,
    )

    received = sidecar.feed(verify_frame + history_frame + b"\x00\x00")
    assert received[0].payload == verify
    assert isinstance(received[1], ReceivedHistoryQuery)
    with pytest.raises(SourcePortTransportFrameError) as partial_tail:
        sidecar.require_frame_boundary()
    assert partial_tail.value.reason_code == "source_port_truncated_frame"
    assert sidecar.closed is True


def test_real_ready_pipe_writes_the_accepted_ack_before_consuming_the_fake_effect(
    tmp_path: Path,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, first_query = _history_reader(tmp_path)
    ack_written = threading.Event()
    expect_ack_write = threading.Event()
    release_effect = threading.Event()
    effect_started = threading.Event()
    verify_writes_recorded = threading.Event()
    effect_calls: list[VerifySessionRequestV1] = []
    verify_write_deadlines: list[float] = []
    original_send = child_session_module.SidecarHandshakeResult._send_source_port_frame

    def record_verify_write(
        child_session: child_session_module.SidecarHandshakeResult,
        frame: bytes,
        deadline: float,
    ) -> None:
        original_send(child_session, frame, deadline)
        message_type = json.loads(frame[4:])["message_type"]
        if expect_ack_write.is_set() and message_type.startswith("verify_session."):
            verify_write_deadlines.append(deadline)
            ack_written.set()
            if len(verify_write_deadlines) == 2:
                verify_writes_recorded.set()

    def fake_effect(request: VerifySessionRequestV1) -> VerifySessionResultV1:
        assert ack_written.wait(1)
        effect_calls.append(request)
        effect_started.set()
        assert release_effect.wait(1)
        return _verify_result(request)

    def serve_shared_transport(result: readiness.SidecarHandshakeResult) -> None:
        journal = create_command_journal(tmp_path / "verify-session-journal.sqlite3")
        composition = create_verify_session_journal_effect_composition(
            command_journal_session=journal.start(),
            frame_session=result.source_port_session(),
            effect=fake_effect,
        )
        try:
            sidecar_transport.serve_test_source_port(result, reader, composition, timeout=1)
        finally:
            composition.close()
            journal.close()
            result.close()

    lease = lease_factory()
    process, _, thread, errors, _ = _connected_process(
        lease,
        _identity(lease.admission),
        after_sidecar_result=serve_shared_transport,
    )
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    monkeypatch.setattr(child_session_module.SidecarHandshakeResult, "_send_source_port_frame", record_verify_write)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)

    try:
        before = sidecar_transport.exchange_source_history(session, first_query, timeout=1)
        assert isinstance(before.payload, SourceHistoryMatched)

        expect_ack_write.set()
        request = _verify_request()
        submit = session.source_port_session().encode_submit(
            message_id="verify-after-history",
            correlation_id=request.identity.correlation_id,
            payload=request,
        )
        session.send_history_frame(submit, timeout=1)
        ack_messages = session.receive_history(timeout=1)
        assert len(ack_messages) == 1
        assert isinstance(ack_messages[0], ReceivedVerifySessionAcceptedAck)
        assert ack_messages[0].payload.accepted_fact == "dispatch_authorized"
        assert ack_written.wait(1)
        assert effect_started.wait(1)
        assert effect_calls == [request]

        release_effect.set()
        terminal_messages = session.receive_history(timeout=1)
        assert len(terminal_messages) == 1
        assert isinstance(terminal_messages[0], ReceivedVerifySessionResult)
        assert terminal_messages[0].payload.session_readiness == "ready"
        assert verify_writes_recorded.wait(1)
        assert len(verify_write_deadlines) == 2
        assert verify_write_deadlines[0] == verify_write_deadlines[1]

        after = sidecar_transport.exchange_source_history(
            session,
            _sqlite_query(operation_id="history-after-verify", idempotency_key="history-after-verify-key"),
            timeout=1,
        )
        assert isinstance(after.payload, SourceHistoryNotFound)
    finally:
        release_effect.set()
        session.close(1)
        thread.join(timeout=1)

    assert not thread.is_alive()
    assert errors == []
    lease_factory().close()


def test_real_shared_pipe_allows_a_rejection_without_ack_then_serves_history(
    tmp_path: Path,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, first_query = _history_reader(tmp_path)
    request = _verify_request()

    def serve_rejection(result: readiness.SidecarHandshakeResult) -> None:
        try:
            sidecar_transport.serve_source_history_query(result, reader, timeout=1)
            received = result.receive_history(timeout=1)
            assert len(received) == 1
            assert isinstance(received[0], ReceivedVerifySessionSubmit)
            rejection = VerifySessionRejectedV1.model_validate(
                {
                    "contract_version": "seektalent.source.verify-session.rejected/v1",
                    "identity": request.identity,
                    "rejection_reason": "deadline_expired",
                },
                strict=True,
            )
            frame = result.source_port_session().encode_rejected(
                message_id="verify-rejected-1",
                reply_to=received[0].message_id,
                payload=rejection,
            )
            result.send_history_frame(frame, timeout=1)
            sidecar_transport.serve_source_history_query(result, reader, timeout=1)
        finally:
            result.close()

    lease = lease_factory()
    process, _, thread, errors, _ = _connected_process(
        lease,
        _identity(lease.admission),
        after_sidecar_result=serve_rejection,
    )
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)

    try:
        before = sidecar_transport.exchange_source_history(session, first_query, timeout=1)
        exchange = sidecar_transport.exchange_verify_session(session, request, timeout=1)
        after = sidecar_transport.exchange_source_history(
            session,
            _sqlite_query(operation_id="history-after-rejection", idempotency_key="history-after-rejection-key"),
            timeout=1,
        )
    finally:
        session.close(1)
        thread.join(timeout=1)

    assert isinstance(before.payload, SourceHistoryMatched)
    assert exchange.accepted_ack is None
    assert isinstance(exchange.terminal, ReceivedVerifySessionRejected)
    assert exchange.terminal.payload.rejection_reason == "deadline_expired"
    assert isinstance(after.payload, SourceHistoryNotFound)
    assert not thread.is_alive()
    assert errors == []
    lease_factory().close()


@pytest.mark.parametrize(
    ("request_factory", "label"),
    ((_verify_request, "exact_replay"), (_redelivery, "outbox_redelivery")),
)
def test_real_pipe_reconnect_replays_terminal_without_a_second_fake_effect(
    tmp_path: Path,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
    request_factory: Callable[[], VerifySessionRequestV1],
    label: str,
) -> None:
    del label
    journal_path = tmp_path / "journal.sqlite3"
    effect = _RecordingEffect()
    first_lease = lease_factory()
    first, first_thread, first_errors = _start_verify_sidecar(
        lease=first_lease,
        journal_path=journal_path,
        effect=effect,
        monkeypatch=monkeypatch,
    )

    try:
        initial = sidecar_transport.exchange_verify_session(first, _verify_request(), timeout=1)
        assert initial.accepted_ack is not None
        assert isinstance(initial.terminal, ReceivedVerifySessionResult)
    finally:
        first.close(1)
        first_thread.join(timeout=1)

    assert effect.calls == 1
    assert first_errors == []
    lease_factory().close()

    replay_lease = lease_factory()
    replay, replay_thread, replay_errors = _start_verify_sidecar(
        lease=replay_lease,
        journal_path=journal_path,
        effect=effect,
        monkeypatch=monkeypatch,
    )
    try:
        exchange = sidecar_transport.exchange_verify_session(replay, request_factory(), timeout=1)
    finally:
        replay.close(1)
        replay_thread.join(timeout=1)

    assert exchange.accepted_ack is not None
    assert isinstance(exchange.terminal, ReceivedVerifySessionResult)
    assert effect.calls == 1
    assert replay_errors == []
    lease_factory().close()


def test_real_pipe_interruption_after_dispatch_intent_reconnects_reconcile_first_without_effect(
    tmp_path: Path,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "journal.sqlite3"
    effect = _RecordingEffect()
    first_lease = lease_factory()
    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            journal_effect,
            "_before_effect_invocation",
            lambda: (_ for _ in ()).throw(_InterruptAfterIntent()),
        )
        first, first_thread, first_errors = _start_verify_sidecar(
            lease=first_lease,
            journal_path=journal_path,
            effect=effect,
            monkeypatch=interrupted,
            suppress=_InterruptAfterIntent,
        )
        try:
            with pytest.raises(readiness.SidecarReadinessError):
                sidecar_transport.exchange_verify_session(first, _verify_request(), timeout=1)
        finally:
            first.close(1)
            first_thread.join(timeout=1)

    assert _journal_phase(journal_path) == "dispatch_intent"
    assert effect.calls == 0
    assert first_errors == []
    lease_factory().close()

    replay_lease = lease_factory()
    replay, replay_thread, replay_errors = _start_verify_sidecar(
        lease=replay_lease,
        journal_path=journal_path,
        effect=effect,
        monkeypatch=monkeypatch,
    )
    try:
        exchange = sidecar_transport.exchange_verify_session(replay, _redelivery(), timeout=1)
    finally:
        replay.close(1)
        replay_thread.join(timeout=1)

    assert exchange.accepted_ack is not None
    assert isinstance(exchange.terminal, ReceivedVerifySessionReconcileRequired)
    assert exchange.terminal.payload.reconciliation_fact == "dispatch_not_observed"
    assert effect.calls == 0
    assert replay_errors == []
    lease_factory().close()


def test_real_pipe_ack_write_failure_keeps_dispatch_intent_for_authenticated_reconcile(
    tmp_path: Path,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "journal.sqlite3"
    effect = _RecordingEffect()
    original_send = child_session_module.SidecarHandshakeResult._send_source_port_frame

    def fail_accepted_ack(
        child_session: child_session_module.SidecarHandshakeResult,
        frame: bytes,
        deadline: float,
    ) -> None:
        if json.loads(frame[4:])["message_type"] == "verify_session.accepted_ack":
            raise OSError("accepted ack write failed")
        original_send(child_session, frame, deadline)

    first_lease = lease_factory()
    with monkeypatch.context() as failed_write:
        failed_write.setattr(child_session_module.SidecarHandshakeResult, "_send_source_port_frame", fail_accepted_ack)
        first, first_thread, first_errors = _start_verify_sidecar(
            lease=first_lease,
            journal_path=journal_path,
            effect=effect,
            monkeypatch=failed_write,
        )
        try:
            with pytest.raises(readiness.SidecarReadinessError):
                sidecar_transport.exchange_verify_session(first, _verify_request(), timeout=1)
        finally:
            first.close(1)
            first_thread.join(timeout=1)

    assert _journal_phase(journal_path) == "dispatch_intent"
    assert effect.calls == 0
    assert len(first_errors) == 1
    lease_factory().close()

    replay_lease = lease_factory()
    replay, replay_thread, replay_errors = _start_verify_sidecar(
        lease=replay_lease,
        journal_path=journal_path,
        effect=effect,
        monkeypatch=monkeypatch,
    )
    try:
        exchange = sidecar_transport.exchange_verify_session(replay, _redelivery(), timeout=1)
    finally:
        replay.close(1)
        replay_thread.join(timeout=1)

    assert exchange.accepted_ack is not None
    assert isinstance(exchange.terminal, ReceivedVerifySessionReconcileRequired)
    assert exchange.terminal.payload.reconciliation_fact == "dispatch_not_observed"
    assert effect.calls == 0
    assert replay_errors == []
    lease_factory().close()


def test_real_pipe_terminal_write_failure_keeps_observation_for_terminal_replay(
    tmp_path: Path,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal_path = tmp_path / "journal.sqlite3"
    effect = _RecordingEffect()
    original_send = child_session_module.SidecarHandshakeResult._send_source_port_frame

    def fail_terminal(
        child_session: child_session_module.SidecarHandshakeResult,
        frame: bytes,
        deadline: float,
    ) -> None:
        if json.loads(frame[4:])["message_type"] == "verify_session.result":
            raise OSError("terminal write failed")
        original_send(child_session, frame, deadline)

    first_lease = lease_factory()
    with monkeypatch.context() as failed_write:
        failed_write.setattr(child_session_module.SidecarHandshakeResult, "_send_source_port_frame", fail_terminal)
        first, first_thread, first_errors = _start_verify_sidecar(
            lease=first_lease,
            journal_path=journal_path,
            effect=effect,
            monkeypatch=failed_write,
        )
        try:
            with pytest.raises(readiness.SidecarReadinessError):
                sidecar_transport.exchange_verify_session(first, _verify_request(), timeout=1)
        finally:
            first.close(1)
            first_thread.join(timeout=1)

    assert _journal_phase(journal_path) == "observed_result"
    assert effect.calls == 1
    assert len(first_errors) == 1
    lease_factory().close()

    replay_lease = lease_factory()
    replay, replay_thread, replay_errors = _start_verify_sidecar(
        lease=replay_lease,
        journal_path=journal_path,
        effect=effect,
        monkeypatch=monkeypatch,
    )
    try:
        exchange = sidecar_transport.exchange_verify_session(replay, _redelivery(), timeout=1)
    finally:
        replay.close(1)
        replay_thread.join(timeout=1)

    assert exchange.accepted_ack is not None
    assert isinstance(exchange.terminal, ReceivedVerifySessionResult)
    assert effect.calls == 1
    assert replay_errors == []
    lease_factory().close()
