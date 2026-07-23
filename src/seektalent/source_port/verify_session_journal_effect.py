"""Production-unreachable verify-session composition over durable journal receipts."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import threading
from typing import Literal, Never, TypeAlias
import weakref

from pydantic import ValidationError

from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionSubmit,
    VerifySessionAcceptedAckV1,
    VerifySessionFailureV1,
    VerifySessionResultV1,
)
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    CommandJournalConflict,
    CommandJournalSession,
    CommandJournalTransitionDisposition,
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
    UNEXPECTED_MESSAGE = "unexpected_message"


class VerifySessionJournalEffectError(RuntimeError):
    """A closed composition failure that never carries a runtime fence bearer."""

    def __init__(self, reason: VerifySessionJournalEffectReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class VerifySessionJournalEffectExchange:
    """One sidecar response batch and the durable receipts that authorize it."""

    disposition: Literal["observed_result", "observed_failure", "terminal_replay", "reconcile_first"]
    outbound_frames: tuple[bytes, ...]
    receipts: tuple[CommandJournalTransitionReceipt, ...]


@dataclass(slots=True)
class _CompositionState:
    command_journal_session: CommandJournalSession
    frame_session: PostHandshakeVerifySessionSession
    effect: VerifySessionEffect
    reply_lock: threading.Lock = field(default_factory=threading.Lock)
    next_reply_number: int = 1


_COMPOSITIONS: dict[
    int,
    tuple[weakref.ReferenceType["VerifySessionJournalEffectComposition"], _CompositionState],
] = {}
_COMPOSITION_LOCK = threading.Lock()
_NO_EFFECT_REPLY = object()


class VerifySessionJournalEffectComposition:
    """Factory-only sidecar composition with no production route or real adapter."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("VerifySessionJournalEffectComposition is factory-only")

    def feed(self, frame: bytes) -> VerifySessionJournalEffectExchange:
        """Accept one authenticated submit and return only durable replies."""
        state = _composition_state(self)
        received = state.frame_session.feed(frame)
        if len(received) != 1 or type(received[0]) is not ReceivedVerifySessionSubmit:
            raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.UNEXPECTED_MESSAGE)
        return _handle_submit(state, received[0])

    def close(self) -> None:
        with _COMPOSITION_LOCK:
            entry = _COMPOSITIONS.get(id(self))
            if entry is None or entry[0]() is not self:
                raise TypeError("VerifySessionJournalEffectComposition must be a live factory composition")
            _COMPOSITIONS.pop(id(self), None)

    def __copy__(self) -> Never:
        raise TypeError("VerifySessionJournalEffectComposition cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("VerifySessionJournalEffectComposition cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("VerifySessionJournalEffectComposition cannot be serialized")


def create_verify_session_journal_effect_composition(
    *,
    command_journal_session: CommandJournalSession,
    frame_session: PostHandshakeVerifySessionSession,
    effect: VerifySessionEffect,
) -> VerifySessionJournalEffectComposition:
    """Bind one real journal session to one authenticated sidecar frame session."""
    if type(command_journal_session) is not CommandJournalSession:
        raise TypeError("command_journal_session must be a factory CommandJournalSession")
    if type(frame_session) is not PostHandshakeVerifySessionSession:
        raise TypeError("frame_session must be a PostHandshakeVerifySessionSession")
    if not callable(effect):
        raise TypeError("effect must be callable")
    composition = object.__new__(VerifySessionJournalEffectComposition)
    composition_id = id(composition)

    def finalize(_: weakref.ReferenceType[VerifySessionJournalEffectComposition]) -> None:
        with _COMPOSITION_LOCK:
            _COMPOSITIONS.pop(composition_id, None)

    state = _CompositionState(
        command_journal_session=command_journal_session,
        frame_session=frame_session,
        effect=effect,
    )
    with _COMPOSITION_LOCK:
        _COMPOSITIONS[composition_id] = (weakref.ref(composition, finalize), state)
    return composition


def _handle_submit(
    state: _CompositionState,
    received: ReceivedVerifySessionSubmit,
) -> VerifySessionJournalEffectExchange:
    request = received.payload
    accepted_ack = _accepted_ack_for_request(request)
    accepted_receipt = _record_accepted(state, request, accepted_ack)
    durable_ack = _accepted_ack_from_receipt(accepted_receipt)
    _validate_durable_accepted_ack(request, durable_ack)
    outbound = [
        state.frame_session.encode_accepted_ack(
            message_id=_next_reply_message_id(state, "ack"),
            reply_to=received.message_id,
            payload=durable_ack,
        )
    ]

    if accepted_receipt.head_phase in {"observed_result", "observed_failure"}:
        terminal = _terminal_reply_from_receipt(request, accepted_receipt)
        outbound.append(_encode_terminal(state, received.message_id, terminal))
        return VerifySessionJournalEffectExchange(
            disposition="terminal_replay",
            outbound_frames=tuple(outbound),
            receipts=(accepted_receipt,),
        )
    if accepted_receipt.head_phase == "dispatch_intent":
        return VerifySessionJournalEffectExchange(
            disposition="reconcile_first",
            outbound_frames=tuple(outbound),
            receipts=(accepted_receipt,),
        )
    if accepted_receipt.head_phase != "accepted":
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_ERROR)
    if (
        request.delivery.delivery_mode == "outbox_redelivery"
        or accepted_receipt.disposition is CommandJournalTransitionDisposition.EXACT_REPLAY
    ):
        return VerifySessionJournalEffectExchange(
            disposition="reconcile_first",
            outbound_frames=tuple(outbound),
            receipts=(accepted_receipt,),
        )

    dispatch_receipt = _record_dispatch_intent(state, request, accepted_receipt)
    if dispatch_receipt.disposition is not CommandJournalTransitionDisposition.CREATED:
        return VerifySessionJournalEffectExchange(
            disposition="reconcile_first",
            outbound_frames=tuple(outbound),
            receipts=(accepted_receipt, dispatch_receipt),
        )

    _before_effect_invocation()
    effect_reply = _invoke_effect(state.effect, request)
    observed_receipt = _record_observation(state, request, dispatch_receipt, effect_reply)
    durable_terminal = _terminal_reply_from_receipt(request, observed_receipt)
    outbound.append(_encode_terminal(state, received.message_id, durable_terminal))
    disposition: Literal["observed_result", "observed_failure"] = (
        "observed_result" if type(durable_terminal) is VerifySessionResultV1 else "observed_failure"
    )
    return VerifySessionJournalEffectExchange(
        disposition=disposition,
        outbound_frames=tuple(outbound),
        receipts=(accepted_receipt, dispatch_receipt, observed_receipt),
    )


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


def _record_accepted(
    state: _CompositionState,
    request: VerifySessionRequestV1,
    accepted_ack: VerifySessionAcceptedAckV1,
) -> CommandJournalTransitionReceipt:
    delivery_mode = request.delivery.delivery_mode
    try:
        return state.command_journal_session.record_accepted(
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
    state: _CompositionState,
    request: VerifySessionRequestV1,
    accepted_receipt: CommandJournalTransitionReceipt,
) -> CommandJournalTransitionReceipt:
    try:
        return state.command_journal_session.record_dispatch_intent(
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
    state: _CompositionState,
    request: VerifySessionRequestV1,
    dispatch_receipt: CommandJournalTransitionReceipt,
    effect_reply: VerifySessionEffectResult,
) -> CommandJournalTransitionReceipt:
    reply_bytes = _canonical_reply_bytes(effect_reply)
    reply_hash = sha256(reply_bytes).hexdigest()
    try:
        if type(effect_reply) is VerifySessionResultV1:
            return state.command_journal_session.record_observed_result(
                run_id=request.identity.run_id,
                operation_id=request.identity.operation_id,
                expected_head_journal_revision=dispatch_receipt.revision,
                result_ref=reply_hash,
                result_hash=reply_hash,
                terminal_reply_bytes=reply_bytes,
            )
        return state.command_journal_session.record_observed_failure(
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
    # The injected effect is the one boundary allowed to fail without exposing its exception payload.
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


def _encode_terminal(
    state: _CompositionState,
    reply_to: str,
    reply: VerifySessionEffectResult,
) -> bytes:
    message_id = _next_reply_message_id(state, "terminal")
    if type(reply) is VerifySessionResultV1:
        return state.frame_session.encode_result(message_id=message_id, reply_to=reply_to, payload=reply)
    if type(reply) is VerifySessionFailureV1:
        return state.frame_session.encode_failure(message_id=message_id, reply_to=reply_to, payload=reply)
    raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.DURABLE_REPLY_INVALID)


def _next_reply_message_id(state: _CompositionState, kind: Literal["ack", "terminal"]) -> str:
    with state.reply_lock:
        number = state.next_reply_number
        state.next_reply_number += 1
    return f"verify-session-{kind}-{number}"


def _composition_state(composition: VerifySessionJournalEffectComposition) -> _CompositionState:
    if type(composition) is not VerifySessionJournalEffectComposition:
        raise TypeError("VerifySessionJournalEffectComposition must be a live factory composition")
    with _COMPOSITION_LOCK:
        entry = _COMPOSITIONS.get(id(composition))
    if entry is None or entry[0]() is not composition:
        raise TypeError("VerifySessionJournalEffectComposition must be a live factory composition")
    return entry[1]


def _before_effect_invocation() -> None:
    return None


__all__ = [
    "VerifySessionEffect",
    "VerifySessionEffectResult",
    "VerifySessionJournalEffectComposition",
    "VerifySessionJournalEffectError",
    "VerifySessionJournalEffectExchange",
    "VerifySessionJournalEffectReason",
    "create_verify_session_journal_effect_composition",
]
