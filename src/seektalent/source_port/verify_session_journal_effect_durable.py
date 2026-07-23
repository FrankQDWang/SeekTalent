"""Durable journal and reply bindings for the verify-session effect lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from hashlib import sha256
from typing import Literal, TypeAlias

from pydantic import ValidationError

from seektalent.source_port.authenticated_verify_session_frames import (
    VerifySessionAcceptedAckV1,
    VerifySessionFailureV1,
    VerifySessionReconcileRequiredV1,
    VerifySessionResultV1,
)
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    CommandJournalConflict,
    CommandJournalSession,
    CommandJournalTransitionReceipt,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestV1,
    canonical_verify_session_result_bytes,
    validate_verify_session_durable_reply_identity,
    validate_verify_session_result_echo,
    verify_session_request_echo,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes


VerifySessionEffectResult: TypeAlias = VerifySessionResultV1 | VerifySessionFailureV1
VerifySessionEffect: TypeAlias = Callable[[VerifySessionRequestV1], VerifySessionEffectResult]


class VerifySessionJournalEffectReason(StrEnum):
    DURABLE_REPLY_INVALID = "durable_reply_invalid"
    DURABLE_REPLY_MISSING = "durable_reply_missing"
    EFFECT_FAILED = "effect_failed"
    EFFECT_OUTCOME_INVALID = "effect_outcome_invalid"
    JOURNAL_CONFLICT = "journal_conflict"
    JOURNAL_ERROR = "journal_error"
    PENDING_EFFECT_UNAVAILABLE = "pending_effect_unavailable"
    UNAUTHENTICATED_ARRIVAL = "unauthenticated_arrival"
    UNEXPECTED_MESSAGE = "unexpected_message"


class VerifySessionJournalEffectError(RuntimeError):
    """A closed composition failure that never carries a runtime fence bearer."""

    def __init__(self, reason: VerifySessionJournalEffectReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


_NO_EFFECT_REPLY = object()


def _accepted_ack_for_request(request: VerifySessionRequestV1) -> VerifySessionAcceptedAckV1:
    try:
        accepted_ack = VerifySessionAcceptedAckV1.model_validate(
            {
                "contract_version": "seektalent.source.verify-session.accepted-ack/v1",
                "identity": request.identity,
                "dispatch_authorization": request.delivery.authorization,
                "accepted_fact": "dispatch_authorized",
            },
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        accepted_ack = None
    if accepted_ack is None:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.EFFECT_OUTCOME_INVALID)
    return accepted_ack


def _reconciliation_required_for_request(
    request: VerifySessionRequestV1,
    reconciliation_fact: Literal["accepted_no_dispatch", "dispatch_not_observed"],
) -> VerifySessionReconcileRequiredV1:
    try:
        reconciliation = VerifySessionReconcileRequiredV1.model_validate(
            {
                "contract_version": "seektalent.source.verify-session.reconcile-required/v1",
                "identity": request.identity,
                "reconciliation_fact": reconciliation_fact,
            },
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        reconciliation = None
    if reconciliation is None:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_ERROR)
    return reconciliation


def _record_accepted(
    command_journal_session: CommandJournalSession,
    request: VerifySessionRequestV1,
    accepted_ack: VerifySessionAcceptedAckV1,
) -> CommandJournalTransitionReceipt:
    delivery_mode = request.delivery.delivery_mode
    try:
        return command_journal_session.record_accepted(
            _accepted_command(request),
            accepted_ack_bytes=_canonical_reply_bytes(accepted_ack),
            allow_existing_phase_replay=True,
            allow_transport_replay=delivery_mode == "outbox_redelivery",
            require_existing_replay=delivery_mode == "outbox_redelivery",
        )
    except CommandJournalConflict:
        reason = VerifySessionJournalEffectReason.JOURNAL_CONFLICT
    except (RuntimeError, TypeError, ValueError):
        reason = VerifySessionJournalEffectReason.JOURNAL_ERROR
    raise VerifySessionJournalEffectError(reason)


def _record_dispatch_intent(
    command_journal_session: CommandJournalSession,
    request: VerifySessionRequestV1,
    accepted_receipt: CommandJournalTransitionReceipt,
) -> CommandJournalTransitionReceipt:
    try:
        return command_journal_session.record_dispatch_intent(
            run_id=request.identity.run_id,
            operation_id=request.identity.operation_id,
            expected_head_journal_revision=accepted_receipt.revision,
            durable_dispatch_intent_ref=request.delivery.authorization.dispatch_intent_id,
        )
    except CommandJournalConflict:
        reason = VerifySessionJournalEffectReason.JOURNAL_CONFLICT
    except (RuntimeError, TypeError, ValueError):
        reason = VerifySessionJournalEffectReason.JOURNAL_ERROR
    raise VerifySessionJournalEffectError(reason)


def _record_observation(
    command_journal_session: CommandJournalSession,
    request: VerifySessionRequestV1,
    dispatch_receipt: CommandJournalTransitionReceipt,
    effect_reply: VerifySessionEffectResult,
) -> CommandJournalTransitionReceipt:
    reply_bytes = _canonical_reply_bytes(effect_reply)
    reply_hash = sha256(reply_bytes).hexdigest()
    try:
        if type(effect_reply) is VerifySessionResultV1:
            return command_journal_session.record_observed_result(
                run_id=request.identity.run_id,
                operation_id=request.identity.operation_id,
                expected_head_journal_revision=dispatch_receipt.revision,
                result_ref=reply_hash,
                result_hash=reply_hash,
                terminal_reply_bytes=reply_bytes,
            )
        return command_journal_session.record_observed_failure(
            run_id=request.identity.run_id,
            operation_id=request.identity.operation_id,
            expected_head_journal_revision=dispatch_receipt.revision,
            failure_ref=reply_hash,
            failure_hash=reply_hash,
            terminal_reply_bytes=reply_bytes,
        )
    except CommandJournalConflict:
        reason = VerifySessionJournalEffectReason.JOURNAL_CONFLICT
    except (RuntimeError, TypeError, ValueError):
        reason = VerifySessionJournalEffectReason.JOURNAL_ERROR
    raise VerifySessionJournalEffectError(reason)


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


def _invoke_effect(
    effect: VerifySessionEffect,
    request: VerifySessionRequestV1,
) -> VerifySessionEffectResult:
    reply: object = _NO_EFFECT_REPLY
    with suppress(Exception):
        reply = effect(request)
    if reply is _NO_EFFECT_REPLY:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.EFFECT_FAILED)
    try:
        if type(reply) is VerifySessionResultV1:
            result = VerifySessionResultV1.model_validate(reply.model_dump(mode="python"), strict=True)
            validate_verify_session_result_echo(request, result)
            return result
        if type(reply) is VerifySessionFailureV1:
            failure = VerifySessionFailureV1.model_validate(reply.model_dump(mode="python"), strict=True)
            validate_verify_session_durable_reply_identity(verify_session_request_echo(request), failure.identity)
            return failure
    except (TypeError, ValueError, ValidationError):
        invalid_outcome = True
    else:
        invalid_outcome = False
    if invalid_outcome:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.EFFECT_OUTCOME_INVALID)
    raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.EFFECT_OUTCOME_INVALID)


def _accepted_ack_from_receipt(receipt: CommandJournalTransitionReceipt) -> VerifySessionAcceptedAckV1:
    reply_bytes = receipt.accepted_ack_bytes
    if reply_bytes is None:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_MISSING)
    reply = _decode_canonical_reply(reply_bytes, VerifySessionAcceptedAckV1)
    if type(reply) is not VerifySessionAcceptedAckV1:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_INVALID)
    return reply


def _validate_durable_accepted_ack(
    request: VerifySessionRequestV1,
    accepted_ack: VerifySessionAcceptedAckV1,
) -> None:
    try:
        validate_verify_session_durable_reply_identity(verify_session_request_echo(request), accepted_ack.identity)
    except (TypeError, ValueError, ValidationError):
        accepted_ack_matches_request = False
    else:
        accepted_ack_matches_request = accepted_ack.dispatch_authorization == request.delivery.authorization
    if not accepted_ack_matches_request:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_CONFLICT)
    if (
        request.delivery.delivery_mode == "outbox_redelivery"
        and request.identity.deadline.value > accepted_ack.identity.deadline.value
    ):
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_CONFLICT)


def _terminal_reply_from_receipt(
    request: VerifySessionRequestV1,
    receipt: CommandJournalTransitionReceipt,
) -> VerifySessionEffectResult:
    reply_bytes = receipt.terminal_reply_bytes
    if reply_bytes is None:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_MISSING)
    if receipt.head_phase == "observed_result":
        reply = _decode_canonical_reply(reply_bytes, VerifySessionResultV1)
        if type(reply) is VerifySessionResultV1:
            _validate_durable_terminal_reply(request, reply)
            return reply
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_INVALID)
    if receipt.head_phase == "observed_failure":
        reply = _decode_canonical_reply(reply_bytes, VerifySessionFailureV1)
        if type(reply) is VerifySessionFailureV1:
            _validate_durable_terminal_reply(request, reply)
            return reply
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_INVALID)
    raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_INVALID)


def _decode_canonical_reply(
    reply_bytes: bytes,
    model: type[VerifySessionAcceptedAckV1] | type[VerifySessionEffectResult],
) -> VerifySessionAcceptedAckV1 | VerifySessionEffectResult:
    reply: VerifySessionAcceptedAckV1 | VerifySessionEffectResult | None = None
    try:
        reply = model.model_validate_json(reply_bytes, strict=True)
        if _canonical_reply_bytes(reply) != reply_bytes:
            raise ValueError("durable_reply_noncanonical")
    except (TypeError, ValueError, UnicodeDecodeError, ValidationError):
        invalid_reply = True
    else:
        invalid_reply = False
    if invalid_reply or reply is None:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_INVALID)
    return reply


def _validate_durable_terminal_reply(
    request: VerifySessionRequestV1,
    reply: VerifySessionEffectResult,
) -> None:
    try:
        if type(reply) is VerifySessionResultV1:
            validate_verify_session_result_echo(request, reply)
        else:
            validate_verify_session_durable_reply_identity(verify_session_request_echo(request), reply.identity)
    except (TypeError, ValueError, ValidationError):
        valid = False
    else:
        valid = True
    if not valid:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_INVALID)


def _canonical_reply_bytes(reply: VerifySessionAcceptedAckV1 | VerifySessionEffectResult) -> bytes:
    if type(reply) is VerifySessionResultV1:
        return canonical_verify_session_result_bytes(reply)
    return canonical_json_bytes(reply.model_dump(mode="json"))


__all__ = [
    "VerifySessionEffect",
    "VerifySessionEffectResult",
    "VerifySessionJournalEffectError",
    "VerifySessionJournalEffectReason",
]
