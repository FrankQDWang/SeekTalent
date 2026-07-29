"""Authenticated frames for the Liepin details Source Operation."""

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
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsObservationV1,
    LiepinDetailsOperationRequestV1,
    canonical_liepin_details_request_hash,
    stable_liepin_details_operation_id,
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


class LiepinDetailsFrameError(AuthenticatedFrameError):
    pass


class LiepinDetailsSubmitV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-details.submit/v1"]
    identity: OperationIdentityV1
    delivery: DispatchDeliveryV1
    request: LiepinDetailsOperationRequestV1

    @model_validator(mode="after")
    def validate_binding(self) -> LiepinDetailsSubmitV1:
        authorization = self.delivery.authorization
        if (
            self.identity.operation_kind != "details"
            or self.identity.run_id != self.request.runtime_run_id
            or self.identity.operation_id != stable_liepin_details_operation_id(self.request)
            or self.identity.request_hash
            != canonical_liepin_details_request_hash(self.request)
            or authorization.run_id != self.identity.run_id
            or authorization.operation_id != self.identity.operation_id
            or authorization.request_hash != self.identity.request_hash
        ):
            raise ValueError("liepin_details_submit_identity_mismatch")
        return self


class LiepinDetailsAcceptedAckV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-details.ack/v1"]
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
    def validate_ack_kind(self) -> LiepinDetailsAcceptedAckV1:
        expected = (
            "new_logical_operation"
            if self.identity.expected_reconciliation_revision == 0
            else "new_dispatch_authorization"
        )
        if self.ack_kind != expected:
            raise ValueError("liepin_details_ack_kind_mismatch")
        return self


class LiepinDetailsResultV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-details.result/v1"]
    identity: OperationIdentityV1
    observation: LiepinDetailsObservationV1


class LiepinDetailsReconcileRequiredV1(StrictWireModel):
    contract_version: Literal[
        "seektalent.source.liepin-details.reconcile-required/v1"
    ]
    identity: OperationIdentityV1
    history_fact: Literal["accepted_no_dispatch", "dispatch_not_observed"]


class _EnvelopeBase(AuthenticatedFrameEnvelopeBase):
    pass


class _SubmitEnvelope(_EnvelopeBase):
    reply_to: None
    message_type: Literal["liepin_details.submit"]
    payload: LiepinDetailsSubmitV1


class _AckEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_details.accepted_ack"]
    payload: LiepinDetailsAcceptedAckV1


class _ResultEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_details.result"]
    payload: LiepinDetailsResultV1


class _ReconcileEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["liepin_details.reconcile_required"]
    payload: LiepinDetailsReconcileRequiredV1


_Envelope: TypeAlias = Annotated[
    _SubmitEnvelope | _AckEnvelope | _ResultEnvelope | _ReconcileEnvelope,
    Field(discriminator="message_type"),
]
_ENVELOPE_ADAPTER = TypeAdapter(_Envelope)


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsSubmit:
    message_id: str
    correlation_id: str | None
    payload: LiepinDetailsSubmitV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsAcceptedAck:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinDetailsAcceptedAckV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsResult:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinDetailsResultV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsReconcileRequired:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinDetailsReconcileRequiredV1


ReceivedLiepinDetailsMessage: TypeAlias = (
    ReceivedLiepinDetailsSubmit
    | ReceivedLiepinDetailsAcceptedAck
    | ReceivedLiepinDetailsResult
    | ReceivedLiepinDetailsReconcileRequired
)


@dataclass(frozen=True, slots=True)
class _DetailsPending:
    identity: OperationIdentityV1


class PostHandshakeLiepinDetailsSession(
    AuthenticatedFrameSession[_Envelope, ReceivedLiepinDetailsMessage, _DetailsPending]
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
            error_factory=LiepinDetailsFrameError,
            main_send_types=frozenset({"liepin_details.submit"}),
            sidecar_send_types=frozenset(
                {
                    "liepin_details.accepted_ack",
                    "liepin_details.result",
                    "liepin_details.reconcile_required",
                }
            ),
            request_message_types=frozenset({"liepin_details.submit"}),
            response_message_types=frozenset(
                {
                    "liepin_details.accepted_ack",
                    "liepin_details.result",
                    "liepin_details.reconcile_required",
                }
            ),
            reply_validator=_validate_reply,
            received_message=_received_message,
            pending_from_request=lambda envelope: _DetailsPending(
                identity=envelope.payload.identity
            ),
            reply_mismatch_reason="liepin_details_reply_mismatch",
            pending_request_limit_reason="liepin_details_pending_limit",
            max_frame_bytes=lambda: DEFAULT_MAX_FRAME_BYTES,
            max_session_messages=lambda: DEFAULT_MAX_SESSION_MESSAGES,
            max_pending_requests=lambda: DEFAULT_MAX_PENDING_REQUESTS,
        )

    def encode_submit(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        payload: LiepinDetailsSubmitV1,
    ) -> bytes:
        return self._encode(
            _SubmitEnvelope,
            message_id=message_id,
            reply_to=None,
            correlation_id=correlation_id,
            message_type="liepin_details.submit",
            payload=payload,
        )

    def encode_accepted_ack(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinDetailsAcceptedAckV1,
    ) -> bytes:
        return self._encode(
            _AckEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_details.accepted_ack",
            payload=payload,
        )

    def encode_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinDetailsResultV1,
    ) -> bytes:
        return self._encode(
            _ResultEnvelope,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=correlation_id,
            message_type="liepin_details.result",
            payload=payload,
        )

    def encode_reconcile_required(
        self,
        *,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: LiepinDetailsReconcileRequiredV1,
    ) -> bytes:
        return self._encode(
            _ReconcileEnvelope,
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
            self._fail("liepin_details_schema_validation")
        return self._encode_authenticated_envelope(envelope)


def _received_message(envelope: _Envelope) -> ReceivedLiepinDetailsMessage:
    if isinstance(envelope, _SubmitEnvelope):
        return ReceivedLiepinDetailsSubmit(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _AckEnvelope):
        return ReceivedLiepinDetailsAcceptedAck(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _ResultEnvelope):
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
    pending: _DetailsPending,
    envelope: _Envelope,
    state: object | None,
) -> PendingReply:
    if isinstance(envelope, _SubmitEnvelope):
        raise ReplyValidationError("liepin_details_reply_mismatch")
    if envelope.payload.identity != pending.identity:
        raise ReplyValidationError("liepin_details_reply_identity_mismatch")
    if isinstance(envelope, _AckEnvelope):
        if state == "acked":
            raise ReplyValidationError("liepin_details_duplicate_ack")
        return PendingReply.pending("acked")
    if not isinstance(envelope, (_ResultEnvelope, _ReconcileEnvelope)):
        raise ReplyValidationError("liepin_details_reply_mismatch")
    if state != "acked":
        raise ReplyValidationError("liepin_details_ack_missing")
    if isinstance(envelope, _ResultEnvelope):
        observation = envelope.payload.observation
        if (
            observation.operation_id != pending.identity.operation_id
            or observation.canonical_request_hash != pending.identity.request_hash
        ):
            raise ReplyValidationError("liepin_details_result_identity_mismatch")
    return PendingReply.terminal()


__all__ = [
    "LiepinDetailsAcceptedAckV1",
    "LiepinDetailsFrameError",
    "LiepinDetailsReconcileRequiredV1",
    "LiepinDetailsResultV1",
    "LiepinDetailsSubmitV1",
    "PostHandshakeLiepinDetailsSession",
    "ReceivedLiepinDetailsAcceptedAck",
    "ReceivedLiepinDetailsMessage",
    "ReceivedLiepinDetailsReconcileRequired",
    "ReceivedLiepinDetailsResult",
    "ReceivedLiepinDetailsSubmit",
]
