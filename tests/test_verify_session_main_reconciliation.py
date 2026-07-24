from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import get_args

import pytest

from seektalent.installed_slot import InstalledSidecarLaunchLease
from seektalent.source_history_reconciliation import (
    SourceHistoryReconciliationError,
    commit_admitted_source_history_reconciliation,
)
from seektalent.source_port import sidecar_transport
from seektalent.source_port.authenticated_verify_session_frames import (
    ReceivedVerifySessionResult,
    VerifySessionFailureV1,
)
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader
from seektalent.source_port.operation_dispatch import OperationIdentityV1
from seektalent.source_port.verify_session_contract import (
    VerifySessionResultV1,
    VerifySessionSafeReasonCode,
    canonical_verify_session_result_bytes,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.store import RuntimeControlStore
from tests.test_source_history_reconciliation import (
    COMMITTED_AT,
    RETRY_COMMITTED_AT,
    _accepted,
    _close_exchange,
    _exchange,
    _history_harness,
    _query,
    _store_with_operation,
    ready_lease_factory as _source_history_ready_lease_factory,
)


_USER_ACTIONS = {
    "liepin_host_tab_missing": "verify_session.open_liepin_host",
    "liepin_opencli_identity_intercept": "verify_session.complete_identity_check",
    "liepin_opencli_login_required": "verify_session.log_in",
    "liepin_opencli_risk_page": "verify_session.complete_risk_check",
    "liepin_opencli_unknown_modal": "verify_session.dismiss_or_resolve_modal",
}
_INCOMPATIBILITY_REASONS = (
    "liepin_opencli_bridge_build_mismatch",
    "liepin_opencli_bridge_capability_missing",
    "liepin_opencli_bridge_integrity_failed",
    "liepin_opencli_bridge_protocol_mismatch",
    "liepin_opencli_bridge_wrong_implementation",
    "liepin_opencli_command_missing",
)
_FAILED_RESULT_REASONS = tuple(
    reason
    for reason in get_args(VerifySessionSafeReasonCode)
    if reason not in {"configured", *_USER_ACTIONS, *_INCOMPATIBILITY_REASONS}
) + (None,)
_FAILURE_REASONS = ("exchange_deadline_expired", "sidecar_not_ready", "session_closed")


@pytest.fixture
def ready_lease_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], InstalledSidecarLaunchLease]:
    return _source_history_ready_lease_factory.__wrapped__(tmp_path, monkeypatch)


def test_authenticated_ready_result_atomically_acknowledges_and_commits_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    terminal = _result()
    with _observed_case(
        tmp_path,
        monkeypatch,
        ready_lease_factory,
        terminal,
    ) as (store, admitted, digest):
        record = commit_admitted_source_history_reconciliation(
            admitted,
            store,
            terminal_payload=terminal,
            committed_at=COMMITTED_AT,
        )

        context = store.get_accepted_source_operation_context("runtime-run-1", "source-operation-1")
        assert record.history_conclusion == "observed_result"
        assert record.dispatch_intent_ref == "durable-dispatch-ref"
        assert record.conclusive_observation_ref == digest
        assert record.source_operation_disposition == "completed"
        assert record.retry_posture == "no_retry"
        assert context.dispatch.status == "acknowledged"
        assert context.dispatch.outbox_revision == 2
        assert context.dispatch.accepted_sidecar_generation == 1
        assert context.dispatch.accepted_sidecar_journal_revision == 1
        assert context.dispatch.ack_ref is not None
        assert context.dispatch.ack_ref.startswith("sha256:")
        assert context.dispatch.ack_kind == "new_logical_operation"
        assert context.dispatch.acknowledged_at == COMMITTED_AT
        assert store.list_pending_source_dispatches() == []


@pytest.mark.parametrize(("code", "instruction_key"), tuple(_USER_ACTIONS.items()))
def test_each_authenticated_user_action_commits_only_user_action_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    code: str,
    instruction_key: str,
) -> None:
    terminal = _result(
        session_readiness="not_ready",
        safe_reason_code=code,
        user_action={"code": code, "instruction_key": instruction_key},
    )
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):
        record = commit_admitted_source_history_reconciliation(
            admitted,
            store,
            terminal_payload=terminal,
            committed_at=COMMITTED_AT,
        )

        assert record.source_operation_disposition == "user_action_required"
        assert record.retry_posture == "no_retry"
        assert (
            store.get_source_operation(
                "runtime-run-1",
                "source-operation-1",
            ).source_operation_disposition
            == "user_action_required"
        )


@pytest.mark.parametrize("reason", _INCOMPATIBILITY_REASONS)
def test_closed_verify_session_incompatibility_allowlist_maps_only_to_incompatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    reason: str,
) -> None:
    terminal = _result(session_readiness="not_ready", safe_reason_code=reason)
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):
        record = commit_admitted_source_history_reconciliation(
            admitted,
            store,
            terminal_payload=terminal,
            committed_at=COMMITTED_AT,
        )

        assert record.source_operation_disposition == "incompatible"
        assert record.retry_posture == "no_retry"


@pytest.mark.parametrize("reason", _FAILED_RESULT_REASONS)
def test_remaining_closed_not_ready_results_map_only_to_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    reason: str | None,
) -> None:
    terminal = _result(session_readiness="not_ready", safe_reason_code=reason)
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):
        record = commit_admitted_source_history_reconciliation(
            admitted,
            store,
            terminal_payload=terminal,
            committed_at=COMMITTED_AT,
        )

        assert record.source_operation_disposition == "failed"
        assert record.retry_posture == "no_retry"


@pytest.mark.parametrize("failure_reason", _FAILURE_REASONS)
def test_each_authenticated_failure_maps_only_to_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    failure_reason: str,
) -> None:
    terminal = _failure(failure_reason=failure_reason)
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):
        record = commit_admitted_source_history_reconciliation(
            admitted,
            store,
            terminal_payload=terminal,
            committed_at=COMMITTED_AT,
        )

        assert record.history_conclusion == "observed_failure"
        assert record.source_operation_disposition == "failed"
        assert record.retry_posture == "no_retry"


@pytest.mark.parametrize(
    ("history_kind", "expected_status", "expected_retry"),
    [
        ("accepted_no_dispatch", "acknowledged", "safe_retry"),
        ("not_found", "pending", "safe_retry"),
        ("dispatch_not_observed", "acknowledged", "reconcile_first"),
        ("history_unavailable", "pending", "reconcile_first"),
    ],
)
def test_every_reconciliation_retires_the_ordinal_one_pending_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    history_kind: str,
    expected_status: str,
    expected_retry: str,
) -> None:
    with _nonterminal_case(
        tmp_path,
        monkeypatch,
        ready_lease_factory,
        history_kind,
    ) as (store, admitted):
        record = commit_admitted_source_history_reconciliation(
            admitted,
            store,
            committed_at=COMMITTED_AT,
        )

        context = store.get_accepted_source_operation_context("runtime-run-1", "source-operation-1")
        assert record.retry_posture == expected_retry
        assert context.dispatch.status == expected_status
        assert store.list_pending_source_dispatches() == []
        if expected_status == "acknowledged":
            assert context.dispatch.ack_ref is not None
            assert context.dispatch.ack_ref.startswith("sha256:")
            assert context.dispatch.ack_kind == "new_logical_operation"
        else:
            assert context.dispatch.ack_ref is None


@pytest.mark.parametrize(
    "fault_point",
    [
        "before_outbox_update",
        "after_outbox_update",
        "before_operation_update",
        "after_operation_update",
        "before_reconciliation_insert",
        "after_reconciliation_insert",
        "before_commit",
    ],
)
def test_atomic_verify_session_statement_faults_leave_only_the_complete_old_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    fault_point: str,
) -> None:
    terminal = _result()
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):
        before = _database_snapshot(store)

        def fail(point: str) -> None:
            if point == fault_point:
                raise RuntimeError(f"injected {point}")

        with pytest.raises(RuntimeError, match=fault_point):
            commit_admitted_source_history_reconciliation(
                admitted,
                store,
                terminal_payload=terminal,
                committed_at=COMMITTED_AT,
                fault_injector=fail,
            )

        assert _database_snapshot(store) == before
        assert len(store.list_pending_source_dispatches()) == 1


def test_atomic_verify_session_commit_ack_loss_replays_the_complete_new_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    terminal = _result()
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):

        def lose_ack(point: str) -> None:
            if point == "after_commit":
                raise ConnectionError("commit acknowledgement lost")

        with pytest.raises(ConnectionError, match="acknowledgement lost"):
            commit_admitted_source_history_reconciliation(
                admitted,
                store,
                terminal_payload=terminal,
                committed_at=COMMITTED_AT,
                fault_injector=lose_ack,
            )

        committed = _database_snapshot(store)
        replayed = commit_admitted_source_history_reconciliation(
            admitted,
            store,
            terminal_payload=terminal,
            committed_at=RETRY_COMMITTED_AT,
        )
        assert _database_snapshot(store) == committed
        assert replayed.committed_at == COMMITTED_AT
        context = store.get_accepted_source_operation_context("runtime-run-1", "source-operation-1")
        assert context.dispatch.acknowledged_at == COMMITTED_AT
        assert store.list_pending_source_dispatches() == []


def test_late_concurrent_ack_loses_the_atomic_composition_cas_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    terminal = _result()
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):
        original = RuntimeControlStore.commit_no_owner_source_reconciliation

        def commit_after_late_ack(
            self,
            decision,
            fault_injector=None,
            *,
            dispatch_precondition=None,
            dispatch_ack=None,
        ):
            dispatch = self.get_accepted_source_operation_context(
                "runtime-run-1",
                "source-operation-1",
            ).dispatch
            self.record_source_dispatch_ack(
                runtime_run_id=dispatch.runtime_run_id,
                operation_id=dispatch.operation_id,
                outbox_id=dispatch.outbox_id,
                canonical_request_hash=dispatch.canonical_request_hash,
                dispatch_intent_id=dispatch.dispatch_intent_id,
                dispatch_intent_revision=dispatch.dispatch_intent_revision,
                dispatch_intent_digest=dispatch.dispatch_intent_digest,
                dispatch_authorization_ordinal=dispatch.dispatch_authorization_ordinal,
                expected_outbox_revision=dispatch.outbox_revision,
                accepted_sidecar_generation=1,
                accepted_sidecar_journal_revision=1,
                ack_ref="late-ack-ref",
                ack_kind="new_logical_operation",
                acknowledged_at="2026-07-24T12:00:00.000000Z",
            )
            return original(
                self,
                decision,
                fault_injector,
                dispatch_precondition=dispatch_precondition,
                dispatch_ack=dispatch_ack,
            )

        monkeypatch.setattr(
            RuntimeControlStore,
            "commit_no_owner_source_reconciliation",
            commit_after_late_ack,
        )
        with pytest.raises(RuntimeControlError) as exc_info:
            commit_admitted_source_history_reconciliation(
                admitted,
                store,
                terminal_payload=terminal,
                committed_at=COMMITTED_AT,
            )

        assert exc_info.value.reason_code == "source_reconciliation_dispatch_conflict"
        assert (
            store.get_accepted_source_operation_context(
                "runtime-run-1",
                "source-operation-1",
            ).dispatch.ack_ref
            == "late-ack-ref"
        )
        assert _reconciliation_count(store) == 0


@pytest.mark.parametrize(
    ("terminal_factory", "fact_kind", "observation_ref", "observation_hash", "expected_reason"),
    [
        (
            lambda: _result(identity=_identity(operation_id="wrong-operation")),
            "observed_result",
            None,
            None,
            "source_history_reconciliation_terminal_identity_mismatch",
        ),
        (
            lambda: _result(),
            "observed_result",
            "f" * 64,
            "f" * 64,
            "source_history_reconciliation_terminal_observation_mismatch",
        ),
        (
            lambda: _result(),
            "observed_result",
            "e" * 64,
            "f" * 64,
            "source_history_reconciliation_terminal_observation_mismatch",
        ),
        (
            lambda: _failure(),
            "observed_result",
            None,
            None,
            "source_history_reconciliation_terminal_conclusion_mismatch",
        ),
    ],
)
def test_terminal_identity_hash_ref_and_conclusion_mismatches_are_zero_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
    terminal_factory,
    fact_kind: str,
    observation_ref: str | None,
    observation_hash: str | None,
    expected_reason: str,
) -> None:
    terminal = terminal_factory()
    with _observed_case(
        tmp_path,
        monkeypatch,
        ready_lease_factory,
        terminal,
        fact_kind=fact_kind,
        observation_ref=observation_ref,
        observation_hash=observation_hash,
    ) as (store, admitted, _):
        before = _database_snapshot(store)
        with pytest.raises(SourceHistoryReconciliationError) as exc_info:
            commit_admitted_source_history_reconciliation(
                admitted,
                store,
                terminal_payload=terminal,
                committed_at=COMMITTED_AT,
            )
        assert exc_info.value.reason_code == expected_reason
        assert _database_snapshot(store) == before


def test_missing_mapping_and_malformed_terminal_payloads_are_zero_write_and_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ready_lease_factory: Callable[[], InstalledSidecarLaunchLease],
) -> None:
    terminal = _result()
    secret = "runtime-fence-bearer-secret-value"
    malformed = terminal.model_copy(update={"safe_reason_code": secret})
    with _observed_case(tmp_path, monkeypatch, ready_lease_factory, terminal) as (
        store,
        admitted,
        _,
    ):
        before = _database_snapshot(store)
        for payload, reason in (
            (None, "source_history_reconciliation_terminal_payload_required"),
            (
                terminal.model_dump(mode="json"),
                "source_history_reconciliation_terminal_payload_invalid",
            ),
            (malformed, "source_history_reconciliation_terminal_payload_invalid"),
        ):
            with pytest.raises(SourceHistoryReconciliationError) as exc_info:
                commit_admitted_source_history_reconciliation(
                    admitted,
                    store,
                    terminal_payload=payload,
                    committed_at=COMMITTED_AT,
                )
            assert exc_info.value.reason_code == reason
            assert secret not in str(exc_info.value)
            assert secret not in repr(exc_info.value)
            assert _database_snapshot(store) == before
        assert secret.encode() not in store.path.read_bytes()


def test_constructible_exchange_received_mapping_and_terminal_have_no_write_authority(
    tmp_path: Path,
) -> None:
    terminal = _result()
    received = ReceivedVerifySessionResult(
        message_id="terminal-message",
        reply_to="submit-message",
        correlation_id="correlation-1",
        payload=terminal,
    )
    exchange = object.__new__(sidecar_transport.VerifySessionExchangeResult)
    query = _query(operation_kind="verify_session", accepted_generation_hint=1)
    store = _store_with_operation(tmp_path, query, acknowledge=False)
    before = _database_snapshot(store)

    for raw in (exchange, received, terminal, terminal.model_dump(mode="json")):
        with pytest.raises(TypeError, match="live factory"):
            commit_admitted_source_history_reconciliation(
                raw,  # type: ignore[arg-type]
                store,
                terminal_payload=terminal,
                committed_at=COMMITTED_AT,
            )
        assert _database_snapshot(store) == before


@contextmanager
def _observed_case(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    terminal: VerifySessionResultV1 | VerifySessionFailureV1,
    *,
    fact_kind: str | None = None,
    observation_ref: str | None = None,
    observation_hash: str | None = None,
) -> Iterator[tuple[RuntimeControlStore, sidecar_transport.AdmittedSourceHistoryResult, str]]:
    reply_bytes = (
        canonical_verify_session_result_bytes(terminal)
        if type(terminal) is VerifySessionResultV1
        else canonical_json_bytes(terminal.model_dump(mode="json"))
    )
    digest = sha256(reply_bytes).hexdigest()
    history_kind = fact_kind or ("observed_result" if type(terminal) is VerifySessionResultV1 else "observed_failure")
    reference = observation_ref if observation_ref is not None else digest
    fact_hash = observation_hash if observation_hash is not None else digest
    harness = _history_harness(root, 1, 2, 3)
    accepted_revision = harness.record_accepted(
        _accepted(operation_kind="verify_session"),
        generation=1,
    )
    dispatch_revision = harness.record_dispatch_intent(
        run_id="runtime-run-1",
        operation_id="source-operation-1",
        expected_head_journal_revision=accepted_revision,
        generation=2,
        durable_dispatch_intent_ref="durable-dispatch-ref",
    )
    if history_kind == "observed_result":
        harness.record_observed_result(
            run_id="runtime-run-1",
            operation_id="source-operation-1",
            expected_head_journal_revision=dispatch_revision,
            generation=3,
            result_ref=reference,
            result_hash=fact_hash,
        )
    else:
        harness.record_observed_failure(
            run_id="runtime-run-1",
            operation_id="source-operation-1",
            expected_head_journal_revision=dispatch_revision,
            generation=3,
            failure_ref=reference,
            failure_hash=fact_hash,
        )
    query = _query(
        operation_kind="verify_session",
        first_generation=1,
        last_generation=3,
        accepted_generation_hint=1,
    )
    store = _store_with_operation(
        root,
        query,
        acknowledge=False,
        accepted_journal_revision=accepted_revision,
    )
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        lease_factory,
        monkeypatch,
    )
    try:
        yield store, admitted, digest
    finally:
        _close_exchange(session, child_thread, errors, lease_factory)


@contextmanager
def _nonterminal_case(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    history_kind: str,
) -> Iterator[tuple[RuntimeControlStore, sidecar_transport.AdmittedSourceHistoryResult]]:
    generations = (1, 2) if history_kind == "dispatch_not_observed" else (1,)
    harness = _history_harness(root, *generations)
    accepted_revision = 1
    accepted_generation_hint = None
    last_generation = 2 if history_kind in {"dispatch_not_observed", "history_unavailable"} else 1
    if history_kind in {"accepted_no_dispatch", "dispatch_not_observed"}:
        accepted_revision = harness.record_accepted(
            _accepted(operation_kind="verify_session"),
            generation=1,
        )
        accepted_generation_hint = 1
    if history_kind == "dispatch_not_observed":
        harness.record_dispatch_intent(
            run_id="runtime-run-1",
            operation_id="source-operation-1",
            expected_head_journal_revision=accepted_revision,
            generation=2,
            durable_dispatch_intent_ref="durable-dispatch-ref",
        )
    query = _query(
        operation_kind="verify_session",
        first_generation=1,
        last_generation=last_generation,
        accepted_generation_hint=accepted_generation_hint,
    )
    store = _store_with_operation(root, query, acknowledge=False)
    admitted, session, child_thread, errors = _exchange(
        SourceHistorySQLiteReader(harness.path),
        query,
        lease_factory,
        monkeypatch,
    )
    try:
        yield store, admitted
    finally:
        _close_exchange(session, child_thread, errors, lease_factory)


def _identity(**changes: object) -> OperationIdentityV1:
    values: dict[str, object] = {
        "run_id": "runtime-run-1",
        "operation_id": "source-operation-1",
        "attempt_no": 1,
        "source": "liepin",
        "operation_kind": "verify_session",
        "request_hash": "a" * 64,
        "idempotency_key": "source-key-1",
        "correlation_id": "correlation-1",
        "accepted_requirement_revision_id": "requirement-1",
        "runtime_attempt_fence_ref": "b" * 64,
        "profile_binding_generation": 1,
        "browser_control_scope_id": "browser-scope-1",
        "deadline": {
            "value": 60_000,
            "clock": "relative_monotonic",
            "unit": "milliseconds",
        },
        "expected_source_operation_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
    }
    values.update(changes)
    return OperationIdentityV1.model_validate(values, strict=True)


def _result(**changes: object) -> VerifySessionResultV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.result/v1",
        "identity": _identity(),
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
        "component_receipt_refs": (),
    }
    values.update(changes)
    return VerifySessionResultV1.model_validate(values, strict=True)


def _failure(**changes: object) -> VerifySessionFailureV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.failure/v1",
        "identity": _identity(),
        "failure_fact": "no_effect_performed",
        "failure_reason": "sidecar_not_ready",
    }
    values.update(changes)
    return VerifySessionFailureV1.model_validate(values, strict=True)


def _database_snapshot(store: RuntimeControlStore) -> tuple[object, ...]:
    with sqlite3.connect(store.path) as conn:
        operation = conn.execute(
            """
            SELECT operation_phase, dispatch_intent_ref, conclusive_observation_ref,
                   source_operation_disposition, retry_posture,
                   reconciliation_revision, ledger_revision
            FROM runtime_control_source_operations
            """
        ).fetchone()
        dispatch = conn.execute(
            """
            SELECT status, outbox_revision, accepted_sidecar_generation,
                   accepted_sidecar_journal_revision, ack_ref, ack_kind, acknowledged_at
            FROM runtime_control_source_dispatch_outbox
            """
        ).fetchone()
        reconciliations = conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0]
    return operation, dispatch, reconciliations


def _reconciliation_count(store: RuntimeControlStore) -> int:
    with sqlite3.connect(store.path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM runtime_control_source_reconciliations").fetchone()[0])
