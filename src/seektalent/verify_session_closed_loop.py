"""Production-unreachable main composition for one durable verify-session dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal, Protocol

from seektalent.source_history_reconciliation import (
    commit_admitted_source_history_reconciliation,
)
from seektalent.source_port.authenticated_verify_session_frames import (
    ReceivedVerifySessionAcceptedAck,
    ReceivedVerifySessionFailure,
    ReceivedVerifySessionReconcileRequired,
    ReceivedVerifySessionRejected,
    ReceivedVerifySessionResult,
)
from seektalent.source_port.history_contract import (
    ExactAuthorizationSelector,
    SourceHistoryQueryV1,
)
from seektalent.source_port.sidecar_transport import (
    SourcePortEndpoint,
    VerifySessionExchangeResult,
    exchange_source_history,
    exchange_verify_session,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionCapability,
    VerifySessionRequestV1,
    validate_verify_session_durable_reply_identity,
    verify_session_request_echo,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes
from seektalent_runtime_control.source_operations import (
    AcceptedSourceOperation,
    SourceDispatchMetadata,
)
from seektalent_runtime_control.source_reconciliation import (
    SourceOperationReconciliationRecord,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent.wtscli_connection_supervisor import (
    WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS,
    WtsCliConnectionError,
    WtsCliConnectionReceipt,
)


class VerifySessionConnectionSupervisor(Protocol):
    def await_ready(
        self,
        *,
        timeout_seconds: float,
    ) -> WtsCliConnectionReceipt: ...


@dataclass(frozen=True, slots=True)
class VerifySessionLiveAuthority:
    """Current non-durable runtime/profile facts required to construct one request."""

    runtime_attempt_fence_token: str = field(repr=False)
    profile_binding_ref: str
    provider_account_ref: str | None
    required_capabilities: tuple[VerifySessionCapability, ...]
    user_interaction_policy: Literal[
        "observe_only",
        "headed_user_action_allowed",
    ]
    verify_search_surface: bool
    component_receipt_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VerifySessionMainLoopResult:
    """Authenticated terminal and the main durable state it produced."""

    disposition: Literal["rejected", "reconciled"]
    request: VerifySessionRequestV1
    dispatch: SourceDispatchMetadata
    exchange: VerifySessionExchangeResult
    reconciliation: SourceOperationReconciliationRecord | None
    connection_receipt: WtsCliConnectionReceipt | None


class VerifySessionMainLoopError(RuntimeError):
    """A closed local authority/context failure with no raw bearer surface."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def deliver_verify_session_outbox(
    *,
    store: RuntimeControlStore,
    endpoint: SourcePortEndpoint,
    runtime_run_id: str,
    operation_id: str,
    live_authority: VerifySessionLiveAuthority,
    delivery_mode: Literal["initial", "outbox_redelivery"],
    correlation_id: str,
    deadline_milliseconds: int,
    acknowledged_at: str,
    committed_at: str,
    timeout: float,
    connection_supervisor: VerifySessionConnectionSupervisor | None = None,
) -> VerifySessionMainLoopResult:
    """Deliver one main-owned epoch and close terminal facts through admitted history."""
    if type(store) is not RuntimeControlStore:
        raise TypeError("store must be a real RuntimeControlStore")
    if type(live_authority) is not VerifySessionLiveAuthority:
        raise TypeError("live_authority must be a VerifySessionLiveAuthority")
    context = store.get_accepted_source_operation_context(
        runtime_run_id,
        operation_id,
    )
    request = _request_from_context(
        context,
        live_authority,
        delivery_mode=delivery_mode,
        correlation_id=correlation_id,
        deadline_milliseconds=deadline_milliseconds,
    )
    connection_receipt: WtsCliConnectionReceipt | None = None
    if delivery_mode == "initial" and connection_supervisor is not None:
        try:
            connection_receipt = connection_supervisor.await_ready(
                timeout_seconds=min(
                    WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS,
                    deadline_milliseconds / 1000,
                ),
            )
        except WtsCliConnectionError as exc:
            raise VerifySessionMainLoopError(
                f"verify_session_{exc.safe_reason_code}"
            ) from None
    committed_dispatch: SourceDispatchMetadata | None = None

    def record_authenticated_ack(received: ReceivedVerifySessionAcceptedAck) -> None:
        nonlocal committed_dispatch
        committed_dispatch = _record_authenticated_ack(
            store,
            context,
            request,
            received,
            acknowledged_at=acknowledged_at,
        )

    exchange = exchange_verify_session(
        endpoint,
        request,
        timeout=timeout,
        accepted_ack_handler=record_authenticated_ack,
    )
    if isinstance(exchange.terminal, ReceivedVerifySessionRejected):
        return VerifySessionMainLoopResult(
            disposition="rejected",
            request=request,
            dispatch=context.dispatch,
            exchange=exchange,
            reconciliation=None,
            connection_receipt=connection_receipt,
        )
    if committed_dispatch is None or exchange.accepted_ack is None:
        raise VerifySessionMainLoopError("verify_session_authenticated_ack_missing")

    terminal_payload = (
        exchange.terminal.payload
        if isinstance(
            exchange.terminal,
            (ReceivedVerifySessionResult, ReceivedVerifySessionFailure),
        )
        else None
    )
    if not isinstance(
        exchange.terminal,
        (
            ReceivedVerifySessionResult,
            ReceivedVerifySessionFailure,
            ReceivedVerifySessionReconcileRequired,
        ),
    ):
        raise VerifySessionMainLoopError("verify_session_terminal_invalid")
    admitted = exchange_source_history(
        endpoint,
        _history_query(context, committed_dispatch),
        timeout=timeout,
    )
    reconciliation = commit_admitted_source_history_reconciliation(
        admitted,
        store,
        terminal_payload=terminal_payload,
        committed_at=committed_at,
    )
    return VerifySessionMainLoopResult(
        disposition="reconciled",
        request=request,
        dispatch=committed_dispatch,
        exchange=exchange,
        reconciliation=reconciliation,
        connection_receipt=connection_receipt,
    )


def _request_from_context(
    context: AcceptedSourceOperation,
    live_authority: VerifySessionLiveAuthority,
    *,
    delivery_mode: Literal["initial", "outbox_redelivery"],
    correlation_id: str,
    deadline_milliseconds: int,
) -> VerifySessionRequestV1:
    operation = context.operation
    expectation = context.expectation
    dispatch = context.dispatch
    if operation.operation_kind != "verify_session":
        raise VerifySessionMainLoopError("verify_session_operation_kind_mismatch")
    if delivery_mode == "initial" and dispatch.status != "pending":
        raise VerifySessionMainLoopError("verify_session_outbox_not_deliverable")
    if expectation.browser_control_scope_id is None:
        raise VerifySessionMainLoopError("verify_session_browser_authority_missing")
    request = VerifySessionRequestV1.create(
        run_id=operation.runtime_run_id,
        operation_id=operation.operation_id,
        attempt_no=expectation.runtime_attempt_no,
        idempotency_key=operation.idempotency_key,
        correlation_id=correlation_id,
        accepted_requirement_revision_id=(operation.accepted_requirement_revision_id),
        runtime_attempt_fence_token=(live_authority.runtime_attempt_fence_token),
        profile_binding_generation=expectation.profile_binding_generation,
        browser_control_scope_id=expectation.browser_control_scope_id,
        deadline_value=deadline_milliseconds,
        expected_source_operation_ledger_revision=(dispatch.expected_ledger_revision),
        expected_reconciliation_revision=(dispatch.expected_reconciliation_revision),
        delivery_mode=delivery_mode,
        dispatch_intent_id=dispatch.dispatch_intent_id,
        dispatch_intent_revision=dispatch.dispatch_intent_revision,
        dispatch_authorization_ordinal=(dispatch.dispatch_authorization_ordinal),
        safe_retry_commit_ref=dispatch.safe_retry_commit_ref,
        source_operation_acceptance_ref=(dispatch.source_operation_acceptance_ref),
        profile_binding_ref=live_authority.profile_binding_ref,
        provider_account_ref=live_authority.provider_account_ref,
        required_capabilities=live_authority.required_capabilities,
        user_interaction_policy=live_authority.user_interaction_policy,
        verify_search_surface=live_authority.verify_search_surface,
        component_receipt_refs=live_authority.component_receipt_refs,
    )
    authorization = request.delivery.authorization
    if (
        request.identity.request_hash != operation.canonical_request_hash
        or request.identity.runtime_attempt_fence_ref != expectation.runtime_attempt_fence_ref
        or authorization.dispatch_intent_digest != dispatch.dispatch_intent_digest
        or authorization.dispatch_authorization_ordinal != dispatch.dispatch_authorization_ordinal
    ):
        raise VerifySessionMainLoopError("verify_session_live_authority_context_mismatch")
    return request


def _record_authenticated_ack(
    store: RuntimeControlStore,
    context: AcceptedSourceOperation,
    request: VerifySessionRequestV1,
    received: ReceivedVerifySessionAcceptedAck,
    *,
    acknowledged_at: str,
) -> SourceDispatchMetadata:
    ack = received.payload
    try:
        validate_verify_session_durable_reply_identity(
            verify_session_request_echo(request),
            ack.identity,
        )
    except (TypeError, ValueError):
        raise VerifySessionMainLoopError("verify_session_authenticated_ack_identity_mismatch") from None
    expected_fact = (
        "dispatch_authorized" if context.dispatch.dispatch_authorization_ordinal == 1 else "accepted_no_dispatch"
    )
    if ack.dispatch_authorization != request.delivery.authorization or ack.accepted_fact != expected_fact:
        raise VerifySessionMainLoopError("verify_session_authenticated_ack_authorization_mismatch")
    ack_bytes = canonical_json_bytes(ack.model_dump(mode="json"))
    ack_ref = f"sha256:{sha256(ack_bytes).hexdigest()}"
    dispatch = context.dispatch
    durable_acknowledged_at = dispatch.acknowledged_at or acknowledged_at
    return store.record_source_dispatch_ack(
        runtime_run_id=dispatch.runtime_run_id,
        operation_id=dispatch.operation_id,
        outbox_id=dispatch.outbox_id,
        canonical_request_hash=dispatch.canonical_request_hash,
        dispatch_intent_id=dispatch.dispatch_intent_id,
        dispatch_intent_revision=dispatch.dispatch_intent_revision,
        dispatch_intent_digest=dispatch.dispatch_intent_digest,
        dispatch_authorization_ordinal=(dispatch.dispatch_authorization_ordinal),
        expected_outbox_revision=1,
        accepted_sidecar_generation=ack.accepted_generation,
        accepted_sidecar_journal_revision=(ack.accepted_journal_revision),
        ack_ref=ack_ref,
        ack_kind=(
            "new_logical_operation" if dispatch.dispatch_authorization_ordinal == 1 else "new_dispatch_authorization"
        ),
        acknowledged_at=durable_acknowledged_at,
    )


def _history_query(
    context: AcceptedSourceOperation,
    dispatch: SourceDispatchMetadata,
) -> SourceHistoryQueryV1:
    accepted_generation = dispatch.accepted_sidecar_generation
    if accepted_generation is None:
        raise VerifySessionMainLoopError("verify_session_accepted_generation_missing")
    return SourceHistoryQueryV1.model_validate(
        {
            "contract_version": "seektalent.source-port.query.request/v1",
            "run_id": context.operation.runtime_run_id,
            "operation_id": context.operation.operation_id,
            "source": context.operation.source_id,
            "operation_kind": context.operation.operation_kind,
            "idempotency_key": context.operation.idempotency_key,
            "request_hash": context.operation.canonical_request_hash,
            "attempt_no": context.expectation.runtime_attempt_no,
            "authorization_selector": ExactAuthorizationSelector(
                kind="exact",
                ordinal=dispatch.dispatch_authorization_ordinal,
            ),
            "accepted_generation_hint": accepted_generation,
            "searched_first_generation": accepted_generation,
            "searched_last_generation": accepted_generation,
            "expected_source_operation_ledger_revision": (dispatch.expected_ledger_revision),
            "expected_reconciliation_revision": (dispatch.expected_reconciliation_revision),
        },
        strict=True,
    )


__all__ = [
    "VerifySessionLiveAuthority",
    "VerifySessionConnectionSupervisor",
    "VerifySessionMainLoopError",
    "VerifySessionMainLoopResult",
    "deliver_verify_session_outbox",
]
