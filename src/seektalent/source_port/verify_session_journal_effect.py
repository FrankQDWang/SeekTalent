"""Production-unreachable verify-session composition over durable journal receipts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import math
import threading
import time
from typing import Literal, Never, TypeAlias
import weakref

from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionSubmit,
    VerifySessionAcceptedAckV1,
    VerifySessionFailureV1,
    VerifySessionResultV1,
)
from seektalent.source_port.command_journal import (
    CommandJournalSession,
    CommandJournalTransitionDisposition,
    CommandJournalTransitionReceipt,
)
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1
from seektalent.source_port.verify_session_journal_effect_durable import (
    VerifySessionEffect,
    VerifySessionEffectResult,
    VerifySessionJournalEffectError,
    VerifySessionJournalEffectReason,
    _accepted_ack_for_request,
    _accepted_ack_from_receipt,
    _invoke_effect,
    _record_accepted as _durable_record_accepted,
    _record_dispatch_intent as _durable_record_dispatch_intent,
    _record_observation as _durable_record_observation,
    _reconciliation_required_for_request,
    _terminal_reply_from_receipt,
    _validate_durable_accepted_ack,
)
from seektalent.source_port.verify_session_pending_effect import (
    VerifySessionPendingEffectAuthority,
    _create_pending_effect_authority,
)


MonotonicClock: TypeAlias = Callable[[], float]


@dataclass(frozen=True, slots=True)
class VerifySessionJournalEffectExchange:
    """One authenticated response batch and the durable receipts that authorize it."""

    disposition: Literal[
        "pending_effect",
        "observed_result",
        "observed_failure",
        "terminal_replay",
        "reconcile_first",
    ]
    outbound_frames: tuple[bytes, ...]
    receipts: tuple[CommandJournalTransitionReceipt, ...]
    pending_effect: VerifySessionPendingEffectAuthority | None = None


@dataclass(slots=True)
class _CompositionState:
    command_journal_session: CommandJournalSession
    frame_session: PostHandshakeVerifySessionSession
    effect: VerifySessionEffect
    monotonic_clock: MonotonicClock
    lifecycle_lock: threading.Lock = field(default_factory=threading.Lock)
    reply_lock: threading.Lock = field(default_factory=threading.Lock)
    closed: bool = False
    next_reply_number: int = 1


_COMPOSITIONS: dict[
    int,
    tuple[weakref.ReferenceType["VerifySessionJournalEffectComposition"], _CompositionState],
] = {}
_COMPOSITION_LOCK = threading.Lock()


class VerifySessionJournalEffectComposition:
    """Factory-only sidecar composition with no production route or real adapter."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("VerifySessionJournalEffectComposition is factory-only")

    def feed(self, frame: bytes) -> VerifySessionJournalEffectExchange:
        """Durably accept one submit and return its ack, replay, or pending-effect authority."""
        state = _composition_state(self)
        _require_open_state(state)
        received = state.frame_session.feed(frame)
        if len(received) != 1 or type(received[0]) is not ReceivedVerifySessionSubmit:
            raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.UNEXPECTED_MESSAGE)
        return _handle_submit(state, received[0])

    def close(self) -> None:
        with _COMPOSITION_LOCK:
            entry = _COMPOSITIONS.get(id(self))
            if entry is None or entry[0]() is not self:
                raise TypeError("VerifySessionJournalEffectComposition must be a live factory composition")
            with entry[1].lifecycle_lock:
                entry[1].closed = True
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
    monotonic_clock: MonotonicClock = time.monotonic,
) -> VerifySessionJournalEffectComposition:
    """Bind one real journal session to one authenticated sidecar frame session."""
    if type(command_journal_session) is not CommandJournalSession:
        raise TypeError("command_journal_session must be a factory CommandJournalSession")
    if type(frame_session) is not PostHandshakeVerifySessionSession:
        raise TypeError("frame_session must be a PostHandshakeVerifySessionSession")
    if not callable(effect):
        raise TypeError("effect must be callable")
    if not callable(monotonic_clock):
        raise TypeError("monotonic_clock must be callable")

    state = _CompositionState(
        command_journal_session=command_journal_session,
        frame_session=frame_session,
        effect=effect,
        monotonic_clock=monotonic_clock,
    )
    composition = object.__new__(VerifySessionJournalEffectComposition)
    composition_id = id(composition)

    def finalize(_: weakref.ReferenceType[VerifySessionJournalEffectComposition]) -> None:
        with state.lifecycle_lock:
            state.closed = True
        with _COMPOSITION_LOCK:
            _COMPOSITIONS.pop(composition_id, None)

    with _COMPOSITION_LOCK:
        _COMPOSITIONS[composition_id] = (weakref.ref(composition, finalize), state)
    return composition


def _handle_submit(
    state: _CompositionState,
    received: ReceivedVerifySessionSubmit,
) -> VerifySessionJournalEffectExchange:
    request = received.payload
    # This candidate starts at authenticated arrival, so journal lock/queue time consumes
    # the same local deadline as the eventual effect. Replays discard it without applying it.
    deadline_at = (
        None if request.delivery.delivery_mode == "outbox_redelivery" else _anchor_local_deadline(state, request)
    )
    accepted_ack = _accepted_ack_for_request(request)
    accepted_receipt = _record_accepted(state, request, accepted_ack)
    durable_ack = _accepted_ack_from_receipt(accepted_receipt)
    _validate_durable_accepted_ack(request, durable_ack)
    ack_frame = state.frame_session.encode_accepted_ack(
        message_id=_next_reply_message_id(state, "ack"),
        reply_to=received.message_id,
        payload=durable_ack,
    )

    if accepted_receipt.head_phase in {"observed_result", "observed_failure"}:
        terminal = _terminal_reply_from_receipt(request, accepted_receipt)
        return VerifySessionJournalEffectExchange(
            disposition="terminal_replay",
            outbound_frames=(ack_frame, _encode_terminal(state, received.message_id, terminal)),
            receipts=(accepted_receipt,),
        )
    if accepted_receipt.head_phase == "dispatch_intent":
        return _reconcile_after_ack(
            state,
            request=request,
            reply_to=received.message_id,
            receipts=(accepted_receipt,),
            reconciliation_fact="dispatch_not_observed",
            ack_frame=ack_frame,
        )
    if accepted_receipt.head_phase != "accepted":
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_ERROR)
    if (
        request.delivery.delivery_mode == "outbox_redelivery"
        or accepted_receipt.disposition is CommandJournalTransitionDisposition.EXACT_REPLAY
    ):
        return _reconcile_after_ack(
            state,
            request=request,
            reply_to=received.message_id,
            receipts=(accepted_receipt,),
            reconciliation_fact="accepted_no_dispatch",
            ack_frame=ack_frame,
        )
    if deadline_at is None:
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_ERROR)
    if _deadline_expired(state, deadline_at):
        return _reconcile_after_ack(
            state,
            request=request,
            reply_to=received.message_id,
            receipts=(accepted_receipt,),
            reconciliation_fact="accepted_no_dispatch",
            ack_frame=ack_frame,
        )

    dispatch_receipt = _record_dispatch_intent(state, request, accepted_receipt)
    if dispatch_receipt.disposition is not CommandJournalTransitionDisposition.CREATED:
        return _reconcile_after_ack(
            state,
            request=request,
            reply_to=received.message_id,
            receipts=(accepted_receipt, dispatch_receipt),
            reconciliation_fact="dispatch_not_observed",
            ack_frame=ack_frame,
        )
    if _deadline_expired(state, deadline_at):
        return _reconcile_after_ack(
            state,
            request=request,
            reply_to=received.message_id,
            receipts=(accepted_receipt, dispatch_receipt),
            reconciliation_fact="dispatch_not_observed",
            ack_frame=ack_frame,
        )

    pending_effect = _create_pending_effect_authority(
        consume_effect=lambda: _consume_pending_effect(
            state,
            request=request,
            reply_to=received.message_id,
            accepted_receipt=accepted_receipt,
            dispatch_receipt=dispatch_receipt,
            deadline_at=deadline_at,
        )
    )
    return VerifySessionJournalEffectExchange(
        disposition="pending_effect",
        outbound_frames=(ack_frame,),
        receipts=(accepted_receipt, dispatch_receipt),
        pending_effect=pending_effect,
    )


def _consume_pending_effect(
    state: _CompositionState,
    *,
    request: VerifySessionRequestV1,
    reply_to: str,
    accepted_receipt: CommandJournalTransitionReceipt,
    dispatch_receipt: CommandJournalTransitionReceipt,
    deadline_at: float,
) -> VerifySessionJournalEffectExchange:
    _require_open_state(state)
    if _deadline_expired(state, deadline_at):
        return _reconcile_after_ack(
            state,
            request=request,
            reply_to=reply_to,
            receipts=(accepted_receipt, dispatch_receipt),
            reconciliation_fact="dispatch_not_observed",
        )

    _before_effect_invocation()
    if _deadline_expired(state, deadline_at):
        return _reconcile_after_ack(
            state,
            request=request,
            reply_to=reply_to,
            receipts=(accepted_receipt, dispatch_receipt),
            reconciliation_fact="dispatch_not_observed",
        )
    effect_reply = _invoke_effect(state.effect, request)
    observed_receipt = _record_observation(state, request, dispatch_receipt, effect_reply)
    durable_terminal = _terminal_reply_from_receipt(request, observed_receipt)
    disposition: Literal["observed_result", "observed_failure"] = (
        "observed_result" if type(durable_terminal) is VerifySessionResultV1 else "observed_failure"
    )
    return VerifySessionJournalEffectExchange(
        disposition=disposition,
        outbound_frames=(_encode_terminal(state, reply_to, durable_terminal),),
        receipts=(accepted_receipt, dispatch_receipt, observed_receipt),
    )


def _reconcile_after_ack(
    state: _CompositionState,
    *,
    request: VerifySessionRequestV1,
    reply_to: str,
    receipts: tuple[CommandJournalTransitionReceipt, ...],
    reconciliation_fact: Literal["accepted_no_dispatch", "dispatch_not_observed"],
    ack_frame: bytes | None = None,
) -> VerifySessionJournalEffectExchange:
    reconciliation = _reconciliation_required_for_request(request, reconciliation_fact)
    status_frame = state.frame_session.encode_reconcile_required(
        message_id=_next_reply_message_id(state, "reconcile"),
        reply_to=reply_to,
        payload=reconciliation,
    )
    outbound_frames = (status_frame,) if ack_frame is None else (ack_frame, status_frame)
    return VerifySessionJournalEffectExchange(
        disposition="reconcile_first",
        outbound_frames=outbound_frames,
        receipts=receipts,
    )


def _anchor_local_deadline(state: _CompositionState, request: VerifySessionRequestV1) -> float:
    now = _monotonic_now(state)
    deadline_at = now + request.identity.deadline.value / 1_000
    if not math.isfinite(deadline_at):
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_ERROR)
    return deadline_at


def _deadline_expired(state: _CompositionState, deadline_at: float) -> bool:
    return _monotonic_now(state) >= deadline_at


def _monotonic_now(state: _CompositionState) -> float:
    try:
        value = state.monotonic_clock()
    except (ArithmeticError, RuntimeError, TypeError, ValueError):
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_ERROR) from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.JOURNAL_ERROR)
    return float(value)


def _record_accepted(
    state: _CompositionState,
    request: VerifySessionRequestV1,
    accepted_ack: VerifySessionAcceptedAckV1,
) -> CommandJournalTransitionReceipt:
    return _durable_record_accepted(state.command_journal_session, request, accepted_ack)


def _record_dispatch_intent(
    state: _CompositionState,
    request: VerifySessionRequestV1,
    accepted_receipt: CommandJournalTransitionReceipt,
) -> CommandJournalTransitionReceipt:
    return _durable_record_dispatch_intent(state.command_journal_session, request, accepted_receipt)


def _record_observation(
    state: _CompositionState,
    request: VerifySessionRequestV1,
    dispatch_receipt: CommandJournalTransitionReceipt,
    effect_reply: VerifySessionEffectResult,
) -> CommandJournalTransitionReceipt:
    return _durable_record_observation(state.command_journal_session, request, dispatch_receipt, effect_reply)


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


def _next_reply_message_id(state: _CompositionState, kind: Literal["ack", "reconcile", "terminal"]) -> str:
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


def _require_open_state(state: _CompositionState) -> None:
    with state.lifecycle_lock:
        if state.closed or state.frame_session.closed:
            raise VerifySessionJournalEffectError(VerifySessionJournalEffectReason.PENDING_EFFECT_UNAVAILABLE)


def _before_effect_invocation() -> None:
    return None


__all__ = [
    "MonotonicClock",
    "VerifySessionEffect",
    "VerifySessionEffectResult",
    "VerifySessionJournalEffectComposition",
    "VerifySessionJournalEffectError",
    "VerifySessionJournalEffectExchange",
    "VerifySessionJournalEffectReason",
    "VerifySessionPendingEffectAuthority",
    "create_verify_session_journal_effect_composition",
]
