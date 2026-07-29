"""Authenticated frames for the Liepin cards Source Operation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter, ValidationError, model_validator

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
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsObservationV1,
    LiepinCardsOperationRequestV1,
    canonical_liepin_cards_request_hash,
    stable_liepin_cards_operation_id,
)
from seektalent.source_port.operation_dispatch import (
    DispatchDeliveryV1,
    OperationIdentityV1,
)
from seektalent.source_port.wire_primitives import (
    Opaque96,
    PositiveJsonInteger,
    StrictWireModel,
)


class LiepinCardsFrameError(AuthenticatedFrameError):
    pass


class LiepinCardsSubmitV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-cards.submit/v1"]
    identity: OperationIdentityV1
    delivery: DispatchDeliveryV1
    request: LiepinCardsOperationRequestV1

    @model_validator(mode="after")
    def validate_binding(self) -> LiepinCardsSubmitV1:
        authorization = self.delivery.authorization
        if (
            self.identity.operation_kind != "cards"
            or self.identity.run_id != self.request.runtime_run_id
            or self.identity.operation_id != stable_liepin_cards_operation_id(self.request)
            or self.identity.request_hash
            != canonical_liepin_cards_request_hash(self.request)
            or authorization.run_id != self.identity.run_id
            or authorization.operation_id != self.identity.operation_id
            or authorization.request_hash != self.identity.request_hash
        ):
            raise ValueError("liepin_cards_submit_identity_mismatch")
        return self


class LiepinCardsAcceptedAckV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-cards.ack/v1"]
    identity: OperationIdentityV1
    sidecar_generation: PositiveJsonInteger
    accepted_journal_revision: PositiveJsonInteger
    ack_kind: Literal[
        "new_logical_operation",
        "same_intent_replay",
        "new_dispatch_authorization",
    ]
    dispatch_intent_ref: str

    @model_validator(mode="after")
    def validate_ack_kind(self) -> LiepinCardsAcceptedAckV1:
        expected = (
            "new_logical_operation"
            if self.identity.expected_reconciliation_revision == 0
            else "new_dispatch_authorization"
        )
        if self.ack_kind != expected:
            raise ValueError("liepin_cards_ack_kind_mismatch")
        return self


class LiepinCardsResultV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-cards.result/v1"]
    identity: OperationIdentityV1
    observation: LiepinCardsObservationV1


class LiepinCardsReconcileRequiredV1(StrictWireModel):
    contract_version: Literal[
        "seektalent.source.liepin-cards.reconcile-required/v1"
    ]
    identity: OperationIdentityV1
    history_fact: Literal["accepted_no_dispatch", "dispatch_not_observed"]


class _EnvelopeBase(AuthenticatedFrameEnvelopeBase):
    pass


class _SubmitEnvelope(_EnvelopeBase):
    reply_to: None
    message_type: Literal["liepin_cards.submit"]
    payload: LiepinCardsSubmitV1


class _AckEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_cards.accepted_ack"]
    payload: LiepinCardsAcceptedAckV1


class _ResultEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_cards.result"]
    payload: LiepinCardsResultV1


class _ReconcileEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_cards.reconcile_required"]
    payload: LiepinCardsReconcileRequiredV1


_Envelope: TypeAlias = Annotated[
    _SubmitEnvelope | _AckEnvelope | _ResultEnvelope | _ReconcileEnvelope,
    Field(discriminator="message_type"),
]
_ENVELOPE_ADAPTER = TypeAdapter(_Envelope)


@dataclass(frozen=True, slots=True)
class ReceivedLiepinCardsSubmit:
    message_id: str
    correlation_id: str | None
    payload: LiepinCardsSubmitV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinCardsAcceptedAck:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinCardsAcceptedAckV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinCardsResult:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinCardsResultV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinCardsReconcileRequired:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinCardsReconcileRequiredV1


ReceivedLiepinCardsMessage: TypeAlias = (
    ReceivedLiepinCardsSubmit
    | ReceivedLiepinCardsAcceptedAck
    | ReceivedLiepinCardsResult
    | ReceivedLiepinCardsReconcileRequired
)


@dataclass(frozen=True, slots=True)
class _CardsPending:
    identity: OperationIdentityV1


class PostHandshakeLiepinCardsSession(
    AuthenticatedFrameSession[_Envelope, ReceivedLiepinCardsMessage, _CardsPending]
):
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
            error_factory=LiepinCardsFrameError,
            main_send_types=frozenset({"liepin_cards.submit"}),
            sidecar_send_types=frozenset(
                {
                    "liepin_cards.accepted_ack",
                    "liepin_cards.result",
                    "liepin_cards.reconcile_required",
                }
            ),
            request_message_types=frozenset({"liepin_cards.submit"}),
            response_message_types=frozenset(
                {
                    "liepin_cards.accepted_ack",
                    "liepin_cards.result",
                    "liepin_cards.reconcile_required",
                }
            ),
            reply_validator=_validate_reply,
            received_message=_received_message,
            pending_from_request=lambda envelope: _CardsPending(
                identity=envelope.payload.identity
            ),
            reply_mismatch_reason="liepin_cards_reply_mismatch",
            pending_request_limit_reason="liepin_cards_pending_limit",
            max_frame_bytes=lambda: DEFAULT_MAX_FRAME_BYTES,
            max_session_messages=lambda: DEFAULT_MAX_SESSION_MESSAGES,
            max_pending_requests=lambda: DEFAULT_MAX_PENDING_REQUESTS,
        )

    def encode_submit(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        payload: LiepinCardsSubmitV1,
    ) -> bytes:
        return self._encode(
            _SubmitEnvelope,
            message_id=message_id,
            reply_to=None,
            correlation_id=correlation_id,
            message_type="liepin_cards.submit",
            payload=payload,
        )

    def encode_accepted_ack(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinCardsAcceptedAckV1,
    ) -> bytes:
        return self._encode(
            _AckEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_cards.accepted_ack",
            payload=payload,
        )

    def encode_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinCardsResultV1,
    ) -> bytes:
        return self._encode(
            _ResultEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_cards.result",
            payload=payload,
        )

    def encode_reconcile_required(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinCardsReconcileRequiredV1,
    ) -> bytes:
        return self._encode(
            _ReconcileEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_cards.reconcile_required",
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
            self._fail("liepin_cards_schema_validation")
        return self._encode_authenticated_envelope(envelope)


def _received_message(envelope: _Envelope) -> ReceivedLiepinCardsMessage:
    if isinstance(envelope, _SubmitEnvelope):
        return ReceivedLiepinCardsSubmit(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _AckEnvelope):
        return ReceivedLiepinCardsAcceptedAck(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _ResultEnvelope):
        return ReceivedLiepinCardsResult(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    return ReceivedLiepinCardsReconcileRequired(
        message_id=envelope.message_id,
        reply_to=envelope.reply_to,
        correlation_id=envelope.correlation_id,
        payload=envelope.payload,
    )


def _validate_reply(
    pending: _CardsPending,
    envelope: _Envelope,
    state: object | None,
) -> PendingReply:
    if isinstance(envelope, _SubmitEnvelope):
        raise ReplyValidationError("liepin_cards_reply_mismatch")
    if envelope.payload.identity != pending.identity:
        raise ReplyValidationError("liepin_cards_reply_identity_mismatch")
    if isinstance(envelope, _AckEnvelope):
        if state == "acked":
            raise ReplyValidationError("liepin_cards_duplicate_ack")
        return PendingReply.pending("acked")
    if not isinstance(envelope, (_ResultEnvelope, _ReconcileEnvelope)):
        raise ReplyValidationError("liepin_cards_reply_mismatch")
    if state != "acked":
        raise ReplyValidationError("liepin_cards_ack_missing")
    if isinstance(envelope, _ResultEnvelope):
        observation = envelope.payload.observation
        if (
            observation.operation_id != pending.identity.operation_id
            or observation.canonical_request_hash != pending.identity.request_hash
        ):
            raise ReplyValidationError("liepin_cards_result_identity_mismatch")
    return PendingReply.terminal()


__all__ = [
    "LiepinCardsAcceptedAckV1",
    "LiepinCardsFrameError",
    "LiepinCardsReconcileRequiredV1",
    "LiepinCardsResultV1",
    "LiepinCardsSubmitV1",
    "PostHandshakeLiepinCardsSession",
    "ReceivedLiepinCardsAcceptedAck",
    "ReceivedLiepinCardsMessage",
    "ReceivedLiepinCardsReconcileRequired",
    "ReceivedLiepinCardsResult",
    "ReceivedLiepinCardsSubmit",
]
