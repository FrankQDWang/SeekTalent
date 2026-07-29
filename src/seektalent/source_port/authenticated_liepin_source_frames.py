"""Shared authenticated framing for cards and details on one supervised pipe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter, ValidationError

from seektalent.source_port.authenticated_frame_core import (
    AuthenticatedFrameEnvelopeBase,
    AuthenticatedFrameError,
    AuthenticatedFrameSession,
    DEFAULT_MAX_FRAME_BYTES,
    DEFAULT_MAX_PENDING_REQUESTS,
    DEFAULT_MAX_SESSION_MESSAGES,
    PendingReply,
    PROTOCOL_MAJOR,
    PROTOCOL_NAME,
    ReplyValidationError,
    ZERO_AUTH_TAG,
)
from seektalent.source_port.authenticated_liepin_cards_frames import (
    LiepinCardsAcceptedAckV1,
    LiepinCardsReconcileRequiredV1,
    LiepinCardsResultV1,
    LiepinCardsSubmitV1,
    ReceivedLiepinCardsAcceptedAck,
    ReceivedLiepinCardsReconcileRequired,
    ReceivedLiepinCardsResult,
    ReceivedLiepinCardsSubmit,
)
from seektalent.source_port.authenticated_liepin_details_frames import (
    LiepinDetailsAcceptedAckV1,
    LiepinDetailsReconcileRequiredV1,
    LiepinDetailsResultV1,
    LiepinDetailsSubmitV1,
    ReceivedLiepinDetailsAcceptedAck,
    ReceivedLiepinDetailsReconcileRequired,
    ReceivedLiepinDetailsResult,
    ReceivedLiepinDetailsSubmit,
)
from seektalent.source_port.operation_dispatch import OperationIdentityV1
from seektalent.source_port.wire_primitives import Opaque96


class LiepinSourceFrameError(AuthenticatedFrameError):
    pass


class _EnvelopeBase(AuthenticatedFrameEnvelopeBase):
    pass


class _CardsSubmitEnvelope(_EnvelopeBase):
    reply_to: None
    message_type: Literal["liepin_cards.submit"]
    payload: LiepinCardsSubmitV1


class _CardsAckEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_cards.accepted_ack"]
    payload: LiepinCardsAcceptedAckV1


class _CardsResultEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_cards.result"]
    payload: LiepinCardsResultV1


class _CardsReconcileEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_cards.reconcile_required"]
    payload: LiepinCardsReconcileRequiredV1


class _DetailsSubmitEnvelope(_EnvelopeBase):
    reply_to: None
    message_type: Literal["liepin_details.submit"]
    payload: LiepinDetailsSubmitV1


class _DetailsAckEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_details.accepted_ack"]
    payload: LiepinDetailsAcceptedAckV1


class _DetailsResultEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_details.result"]
    payload: LiepinDetailsResultV1


class _DetailsReconcileEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_details.reconcile_required"]
    payload: LiepinDetailsReconcileRequiredV1


_Envelope: TypeAlias = Annotated[
    _CardsSubmitEnvelope
    | _CardsAckEnvelope
    | _CardsResultEnvelope
    | _CardsReconcileEnvelope
    | _DetailsSubmitEnvelope
    | _DetailsAckEnvelope
    | _DetailsResultEnvelope
    | _DetailsReconcileEnvelope,
    Field(discriminator="message_type"),
]
_ENVELOPE_ADAPTER = TypeAdapter(_Envelope)

ReceivedLiepinSourceMessage: TypeAlias = (
    ReceivedLiepinCardsSubmit
    | ReceivedLiepinCardsAcceptedAck
    | ReceivedLiepinCardsResult
    | ReceivedLiepinCardsReconcileRequired
    | ReceivedLiepinDetailsSubmit
    | ReceivedLiepinDetailsAcceptedAck
    | ReceivedLiepinDetailsResult
    | ReceivedLiepinDetailsReconcileRequired
)


@dataclass(frozen=True, slots=True)
class _SourcePending:
    identity: OperationIdentityV1
    kind: Literal["cards", "details"]


class PostHandshakeLiepinSourceSession(
    AuthenticatedFrameSession[_Envelope, ReceivedLiepinSourceMessage, _SourcePending]
):
    """One pipe session owning both cards and details submit/ack/result framing."""

    __slots__ = ()

    def __init__(
        self,
        *,
        role: Literal["main", "sidecar"],
        session_id: str,
        protocol_minor: int,
        main_to_sidecar_key: bytes,
        sidecar_to_main_key: bytes,
    ) -> None:
        super().__init__(
            role=role,
            session_id=session_id,
            protocol_minor=protocol_minor,
            main_to_sidecar_key=main_to_sidecar_key,
            sidecar_to_main_key=sidecar_to_main_key,
            envelope_adapter=_ENVELOPE_ADAPTER,
            error_factory=LiepinSourceFrameError,
            main_send_types=frozenset(
                {"liepin_cards.submit", "liepin_details.submit"}
            ),
            sidecar_send_types=frozenset(
                {
                    "liepin_cards.accepted_ack",
                    "liepin_cards.result",
                    "liepin_cards.reconcile_required",
                    "liepin_details.accepted_ack",
                    "liepin_details.result",
                    "liepin_details.reconcile_required",
                }
            ),
            request_message_types=frozenset(
                {"liepin_cards.submit", "liepin_details.submit"}
            ),
            response_message_types=frozenset(
                {
                    "liepin_cards.accepted_ack",
                    "liepin_cards.result",
                    "liepin_cards.reconcile_required",
                    "liepin_details.accepted_ack",
                    "liepin_details.result",
                    "liepin_details.reconcile_required",
                }
            ),
            reply_validator=_validate_reply,
            received_message=_received_message,
            pending_from_request=_pending_from_request,
            reply_mismatch_reason="liepin_source_reply_mismatch",
            pending_request_limit_reason="liepin_source_pending_limit",
            max_frame_bytes=lambda: DEFAULT_MAX_FRAME_BYTES,
            max_session_messages=lambda: DEFAULT_MAX_SESSION_MESSAGES,
            max_pending_requests=lambda: DEFAULT_MAX_PENDING_REQUESTS,
        )

    def encode_cards_submit(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        payload: LiepinCardsSubmitV1,
    ) -> bytes:
        return self._encode(
            _CardsSubmitEnvelope,
            message_id=message_id,
            reply_to=None,
            correlation_id=correlation_id,
            message_type="liepin_cards.submit",
            payload=payload,
        )

    def encode_cards_accepted_ack(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinCardsAcceptedAckV1,
    ) -> bytes:
        return self._encode(
            _CardsAckEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_cards.accepted_ack",
            payload=payload,
        )

    def encode_cards_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinCardsResultV1,
    ) -> bytes:
        return self._encode(
            _CardsResultEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_cards.result",
            payload=payload,
        )

    def encode_cards_reconcile_required(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinCardsReconcileRequiredV1,
    ) -> bytes:
        return self._encode(
            _CardsReconcileEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_cards.reconcile_required",
            payload=payload,
        )

    def encode_details_submit(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        payload: LiepinDetailsSubmitV1,
    ) -> bytes:
        return self._encode(
            _DetailsSubmitEnvelope,
            message_id=message_id,
            reply_to=None,
            correlation_id=correlation_id,
            message_type="liepin_details.submit",
            payload=payload,
        )

    def encode_details_accepted_ack(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinDetailsAcceptedAckV1,
    ) -> bytes:
        return self._encode(
            _DetailsAckEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_details.accepted_ack",
            payload=payload,
        )

    def encode_details_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinDetailsResultV1,
    ) -> bytes:
        return self._encode(
            _DetailsResultEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_details.result",
            payload=payload,
        )

    def encode_details_reconcile_required(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinDetailsReconcileRequiredV1,
    ) -> bytes:
        return self._encode(
            _DetailsReconcileEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_details.reconcile_required",
            payload=payload,
        )

    def _encode(
        self,
        envelope_type,
        *,
        message_id: str,
        reply_to: str | None,
        correlation_id: str | None,
        message_type: str,
        payload: object,
    ) -> bytes:
        self._require_open()
        try:
            envelope = envelope_type(
                protocol_name=PROTOCOL_NAME,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=self._protocol_minor,
                session_id=self._session_id,
                direction_seq=self._require_send_sequence(),
                message_id=message_id,
                reply_to=reply_to,
                message_type=message_type,
                correlation_id=correlation_id,
                payload=payload,
                auth_tag=ZERO_AUTH_TAG,
            )
        except ValidationError:
            self._fail("liepin_source_schema_validation")
        return self._encode_authenticated_envelope(envelope)


def _pending_from_request(envelope: _Envelope) -> _SourcePending:
    if isinstance(envelope, _CardsSubmitEnvelope):
        return _SourcePending(identity=envelope.payload.identity, kind="cards")
    if isinstance(envelope, _DetailsSubmitEnvelope):
        return _SourcePending(identity=envelope.payload.identity, kind="details")
    raise ReplyValidationError("liepin_source_reply_mismatch")


def _received_message(envelope: _Envelope) -> ReceivedLiepinSourceMessage:
    if isinstance(envelope, _CardsSubmitEnvelope):
        return ReceivedLiepinCardsSubmit(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _CardsAckEnvelope):
        return ReceivedLiepinCardsAcceptedAck(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _CardsResultEnvelope):
        return ReceivedLiepinCardsResult(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _CardsReconcileEnvelope):
        return ReceivedLiepinCardsReconcileRequired(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _DetailsSubmitEnvelope):
        return ReceivedLiepinDetailsSubmit(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _DetailsAckEnvelope):
        return ReceivedLiepinDetailsAcceptedAck(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _DetailsResultEnvelope):
        return ReceivedLiepinDetailsResult(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    return ReceivedLiepinDetailsReconcileRequired(
        message_id=envelope.message_id,
        reply_to=envelope.reply_to,
        correlation_id=envelope.correlation_id,
        payload=envelope.payload,
    )


def _validate_reply(
    pending: _SourcePending,
    envelope: _Envelope,
    state: object | None,
) -> PendingReply:
    if isinstance(envelope, (_CardsSubmitEnvelope, _DetailsSubmitEnvelope)):
        raise ReplyValidationError("liepin_source_reply_mismatch")
    if envelope.payload.identity != pending.identity:
        raise ReplyValidationError("liepin_source_reply_identity_mismatch")
    if pending.kind == "cards":
        if isinstance(envelope, _CardsAckEnvelope):
            if state == "acked":
                raise ReplyValidationError("liepin_cards_duplicate_ack")
            return PendingReply.pending("acked")
        if not isinstance(envelope, (_CardsResultEnvelope, _CardsReconcileEnvelope)):
            raise ReplyValidationError("liepin_source_reply_mismatch")
        if state != "acked":
            raise ReplyValidationError("liepin_cards_ack_missing")
        if isinstance(envelope, _CardsResultEnvelope):
            observation = envelope.payload.observation
            if (
                observation.operation_id != pending.identity.operation_id
                or observation.canonical_request_hash != pending.identity.request_hash
            ):
                raise ReplyValidationError("liepin_cards_result_identity_mismatch")
        return PendingReply.terminal()
    if isinstance(envelope, _DetailsAckEnvelope):
        if state == "acked":
            raise ReplyValidationError("liepin_details_duplicate_ack")
        return PendingReply.pending("acked")
    if not isinstance(envelope, (_DetailsResultEnvelope, _DetailsReconcileEnvelope)):
        raise ReplyValidationError("liepin_source_reply_mismatch")
    if state != "acked":
        raise ReplyValidationError("liepin_details_ack_missing")
    if isinstance(envelope, _DetailsResultEnvelope):
        observation = envelope.payload.observation
        if (
            observation.operation_id != pending.identity.operation_id
            or observation.canonical_request_hash != pending.identity.request_hash
        ):
            raise ReplyValidationError("liepin_details_result_identity_mismatch")
    return PendingReply.terminal()


__all__ = [
    "LiepinSourceFrameError",
    "PostHandshakeLiepinSourceSession",
    "ReceivedLiepinSourceMessage",
]
