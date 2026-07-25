"""Authenticated, production-unreachable safe-retry continuity admission."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
import math
import threading
import time
from typing import Literal, Never
import weakref

from pydantic import ValidationError

from seektalent.source_port import command_journal
from seektalent.source_port._safe_retry_continuity_store import (
    SafeRetryContinuityRejected,
    SafeRetryContinuityRejectReason,
    _SafeRetryContinuityStoreError,
    _admit_safe_retry_continuity,
)
from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionSubmit,
    VerifySessionAcceptedAckV1,
    VerifySessionRejectedV1,
    _AuthenticatedVerifySessionArrival,
    _bind_authenticated_verify_session_arrivals,
    _consume_authenticated_verify_session_arrival,
    _release_authenticated_verify_session_arrivals,
)
from seektalent.source_port.command_journal import CommandJournalSession
from seektalent.source_port.wire_primitives import canonical_json_bytes


MonotonicClock = Callable[[], float]


class VerifySessionContinuityAdmissionReason(StrEnum):
    JOURNAL_ERROR = "journal_error"
    SESSION_UNAVAILABLE = "session_unavailable"
    UNAUTHENTICATED_ARRIVAL = "unauthenticated_arrival"
    UNEXPECTED_MESSAGE = "unexpected_message"


class VerifySessionContinuityAdmissionError(RuntimeError):
    """A sanitized local composition failure."""

    def __init__(self, reason: VerifySessionContinuityAdmissionReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class VerifySessionContinuityAdmissionExchange:
    disposition: Literal["created", "exact_replay", "rejected"]
    outbound_frames: tuple[bytes, ...]
    accepted_generation: int | None
    accepted_journal_revision: int | None
    accepted_ack_bytes: bytes | None
    accepted_ack_hash: str | None
    accepted_ack_ref: str | None
    rejection_reason: SafeRetryContinuityRejectReason | None


@dataclass(slots=True)
class _AdmissionState:
    command_journal_session: CommandJournalSession
    frame_session: PostHandshakeVerifySessionSession
    monotonic_clock: MonotonicClock
    arrival_owner: object
    lifecycle_lock: threading.Lock = field(default_factory=threading.Lock)
    reply_lock: threading.Lock = field(default_factory=threading.Lock)
    closed: bool = False
    next_reply_number: int = 1


_ADMISSIONS: dict[
    int,
    tuple[
        weakref.ReferenceType["VerifySessionContinuityAdmission"],
        _AdmissionState,
    ],
] = {}
_ADMISSION_LOCK = threading.Lock()


class VerifySessionContinuityAdmission:
    """Factory-owned admission for one journal session and authenticated frame session."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("VerifySessionContinuityAdmission is factory-only")

    def feed(self, frame: bytes) -> VerifySessionContinuityAdmissionExchange:
        state = _admission_state(self)
        _require_open(state)
        received = state.frame_session.feed(frame)
        if len(received) != 1 or type(received[0]) is not _AuthenticatedVerifySessionArrival:
            raise VerifySessionContinuityAdmissionError(VerifySessionContinuityAdmissionReason.UNEXPECTED_MESSAGE)
        return self.handle_submit(received[0])

    def handle_submit(
        self,
        received: object,
    ) -> VerifySessionContinuityAdmissionExchange:
        state = _admission_state(self)
        _require_open(state)
        try:
            submit, arrival_monotonic = _consume_authenticated_verify_session_arrival(
                state.frame_session,
                owner=state.arrival_owner,
                arrival=received,
            )
        except (TypeError, ValueError):
            raise VerifySessionContinuityAdmissionError(
                VerifySessionContinuityAdmissionReason.UNAUTHENTICATED_ARRIVAL
            ) from None
        return _handle_submit(
            state,
            submit,
            arrival_monotonic=arrival_monotonic,
        )

    def close(self) -> None:
        with _ADMISSION_LOCK:
            entry = _ADMISSIONS.get(id(self))
            if entry is None or entry[0]() is not self:
                raise TypeError("VerifySessionContinuityAdmission must be a live factory admission")
            with entry[1].lifecycle_lock:
                entry[1].closed = True
            _release_authenticated_verify_session_arrivals(
                entry[1].frame_session,
                owner=entry[1].arrival_owner,
            )
            _ADMISSIONS.pop(id(self), None)

    def __copy__(self) -> Never:
        raise TypeError("VerifySessionContinuityAdmission cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("VerifySessionContinuityAdmission cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("VerifySessionContinuityAdmission cannot be serialized")


def create_verify_session_continuity_admission(
    *,
    command_journal_session: CommandJournalSession,
    frame_session: PostHandshakeVerifySessionSession,
    monotonic_clock: MonotonicClock = time.monotonic,
) -> VerifySessionContinuityAdmission:
    """Bind one live journal capability to one authenticated sidecar session."""
    if type(command_journal_session) is not CommandJournalSession:
        raise TypeError("command_journal_session must be a factory CommandJournalSession")
    command_journal._session_state(command_journal_session)
    if not isinstance(frame_session, PostHandshakeVerifySessionSession):
        raise TypeError("frame_session must be a factory Source Port verify session")
    if not callable(monotonic_clock):
        raise TypeError("monotonic_clock must be callable")

    arrival_owner = object()
    state = _AdmissionState(
        command_journal_session=command_journal_session,
        frame_session=frame_session,
        monotonic_clock=monotonic_clock,
        arrival_owner=arrival_owner,
    )
    admission = object.__new__(VerifySessionContinuityAdmission)
    admission_id = id(admission)

    def finalize(_: weakref.ReferenceType[VerifySessionContinuityAdmission]) -> None:
        with state.lifecycle_lock:
            state.closed = True
        _release_authenticated_verify_session_arrivals(
            state.frame_session,
            owner=state.arrival_owner,
        )
        with _ADMISSION_LOCK:
            _ADMISSIONS.pop(admission_id, None)

    _bind_authenticated_verify_session_arrivals(
        frame_session,
        owner=arrival_owner,
        monotonic_clock=monotonic_clock,
    )
    with _ADMISSION_LOCK:
        _ADMISSIONS[admission_id] = (weakref.ref(admission, finalize), state)
    return admission


def _handle_submit(
    state: _AdmissionState,
    received: ReceivedVerifySessionSubmit,
    *,
    arrival_monotonic: float | None,
) -> VerifySessionContinuityAdmissionExchange:
    try:
        session = command_journal._session_state(state.command_journal_session)
    except TypeError:
        raise VerifySessionContinuityAdmissionError(
            VerifySessionContinuityAdmissionReason.SESSION_UNAVAILABLE
        ) from None
    deadline_at = _arrival_deadline(
        received,
        arrival_monotonic=arrival_monotonic,
    )
    try:
        result = _admit_safe_retry_continuity(
            path=session.path,
            generation=session.generation,
            instance_id=session.instance_id,
            request=received.payload,
            arrival_deadline_at=deadline_at,
            monotonic_clock=state.monotonic_clock,
        )
    except SafeRetryContinuityRejected as rejected:
        payload = _rejection(received, rejected.reason)
        frame = state.frame_session.encode_rejected(
            message_id=_next_reply_message_id(state, "rejected"),
            reply_to=received.message_id,
            payload=payload,
        )
        return VerifySessionContinuityAdmissionExchange(
            disposition="rejected",
            outbound_frames=(frame,),
            accepted_generation=None,
            accepted_journal_revision=None,
            accepted_ack_bytes=None,
            accepted_ack_hash=None,
            accepted_ack_ref=None,
            rejection_reason=rejected.reason,
        )
    except _SafeRetryContinuityStoreError:
        raise VerifySessionContinuityAdmissionError(VerifySessionContinuityAdmissionReason.JOURNAL_ERROR) from None

    ack = _decode_durable_ack(result.accepted_ack_bytes)
    frame = state.frame_session.encode_accepted_ack(
        message_id=_next_reply_message_id(state, "accepted"),
        reply_to=received.message_id,
        payload=ack,
    )
    return VerifySessionContinuityAdmissionExchange(
        disposition=result.disposition,
        outbound_frames=(frame,),
        accepted_generation=result.accepted_generation,
        accepted_journal_revision=result.accepted_journal_revision,
        accepted_ack_bytes=result.accepted_ack_bytes,
        accepted_ack_hash=result.accepted_ack_hash,
        accepted_ack_ref=result.accepted_ack_ref,
        rejection_reason=None,
    )


def _arrival_deadline(
    received: ReceivedVerifySessionSubmit,
    *,
    arrival_monotonic: float | None,
) -> float | None:
    if arrival_monotonic is None:
        return None
    if (
        isinstance(arrival_monotonic, bool)
        or not isinstance(arrival_monotonic, (int, float))
        or not math.isfinite(arrival_monotonic)
    ):
        raise VerifySessionContinuityAdmissionError(VerifySessionContinuityAdmissionReason.JOURNAL_ERROR)
    deadline_at = float(arrival_monotonic) + received.payload.identity.deadline.value / 1_000
    if not math.isfinite(deadline_at):
        raise VerifySessionContinuityAdmissionError(VerifySessionContinuityAdmissionReason.JOURNAL_ERROR)
    return deadline_at


def _rejection(
    received: ReceivedVerifySessionSubmit,
    reason: SafeRetryContinuityRejectReason,
) -> VerifySessionRejectedV1:
    try:
        return VerifySessionRejectedV1.model_validate(
            {
                "contract_version": "seektalent.source.verify-session.rejected/v1",
                "identity": received.payload.identity,
                "rejection_reason": reason.value,
            },
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        raise VerifySessionContinuityAdmissionError(VerifySessionContinuityAdmissionReason.JOURNAL_ERROR) from None


def _decode_durable_ack(ack_bytes: bytes) -> VerifySessionAcceptedAckV1:
    try:
        ack = VerifySessionAcceptedAckV1.model_validate_json(ack_bytes, strict=True)
        if canonical_json_bytes(ack.model_dump(mode="json")) != ack_bytes:
            raise ValueError("noncanonical durable ack")
    except (TypeError, ValueError, ValidationError):
        raise VerifySessionContinuityAdmissionError(VerifySessionContinuityAdmissionReason.JOURNAL_ERROR) from None
    return ack


def _next_reply_message_id(
    state: _AdmissionState,
    kind: Literal["accepted", "rejected"],
) -> str:
    with state.reply_lock:
        number = state.next_reply_number
        state.next_reply_number += 1
    return f"verify-session-continuity-{kind}-{number}"


def _admission_state(
    admission: VerifySessionContinuityAdmission,
) -> _AdmissionState:
    if type(admission) is not VerifySessionContinuityAdmission:
        raise TypeError("VerifySessionContinuityAdmission must be a live factory admission")
    with _ADMISSION_LOCK:
        entry = _ADMISSIONS.get(id(admission))
    if entry is None or entry[0]() is not admission:
        raise TypeError("VerifySessionContinuityAdmission must be a live factory admission")
    return entry[1]


def _require_open(state: _AdmissionState) -> None:
    with state.lifecycle_lock:
        if state.closed or state.frame_session.closed:
            raise VerifySessionContinuityAdmissionError(VerifySessionContinuityAdmissionReason.SESSION_UNAVAILABLE)


__all__ = [
    "SafeRetryContinuityRejectReason",
    "VerifySessionContinuityAdmission",
    "VerifySessionContinuityAdmissionError",
    "VerifySessionContinuityAdmissionExchange",
    "VerifySessionContinuityAdmissionReason",
    "create_verify_session_continuity_admission",
]
