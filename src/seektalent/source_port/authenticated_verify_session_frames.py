"""Verify-session composition over the reusable authenticated frame core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

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
from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    OperationIdentityV1,
    validate_dispatch_authorization,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestEchoV1,
    VerifySessionRequestV1,
    VerifySessionResultV1,
    redact_runtime_attempt_fence_token_input,
    validate_verify_session_durable_reply_identity,
    validate_verify_session_result_echo_facts,
    verify_session_request_echo,
)
from seektalent.source_port.wire_primitives import Opaque96, StrictWireModel


MAX_FRAME_BYTES = DEFAULT_MAX_FRAME_BYTES
MAX_SESSION_MESSAGES = DEFAULT_MAX_SESSION_MESSAGES
MAX_PENDING_SUBMITS = DEFAULT_MAX_PENDING_REQUESTS


class VerifySessionFrameReason(StrEnum):
    RAW_INPUT_REQUIRED = "source_port_raw_input_required"
    INVALID_SESSION_CONFIG = "source_port_invalid_session_config"
    INVALID_DIRECTION_KEY = "source_port_invalid_direction_key"
    SESSION_CLOSED = "source_port_session_closed"
    FRAME_LENGTH_INVALID = "source_port_frame_length_invalid"
    FRAME_TOO_LARGE = "source_port_frame_too_large"
    TRUNCATED_FRAME = "source_port_truncated_frame"
    INVALID_UTF8 = "source_port_invalid_utf8"
    BOM_FORBIDDEN = "source_port_bom_forbidden"
    INVALID_JSON = "source_port_invalid_json"
    DUPLICATE_KEY = "source_port_duplicate_key"
    ILLEGAL_NUMBER = "source_port_illegal_number"
    INVALID_UNICODE = "source_port_invalid_unicode"
    ROOT_NOT_OBJECT = "source_port_root_not_object"
    NON_CANONICAL_BODY = "source_port_non_canonical_body"
    SCHEMA_VALIDATION = "source_port_schema_validation"
    PROTOCOL_MISMATCH = "source_port_protocol_mismatch"
    SESSION_MISMATCH = "source_port_session_mismatch"
    FRAME_LENGTH_MISMATCH = "source_port_frame_length_mismatch"
    BAD_AUTH_TAG = "source_port_bad_auth_tag"
    SEQUENCE_MISMATCH = "source_port_sequence_mismatch"
    SEQUENCE_EXHAUSTED = "source_port_sequence_exhausted"
    UNEXPECTED_DIRECTION = "source_port_unexpected_direction"
    DUPLICATE_MESSAGE_ID = "source_port_duplicate_message_id"
    MESSAGE_LIMIT = "source_port_message_limit"
    PENDING_SUBMIT_LIMIT = "source_port_pending_submit_limit"
    WRONG_REPLY = "source_port_wrong_reply"
    REPLY_MISMATCH = "source_port_verify_session_reply_mismatch"
    RESPONSE_STATE_MISMATCH = "source_port_verify_session_response_state_mismatch"


class VerifySessionFrameError(AuthenticatedFrameError):
    """A sanitized authenticated verify-session frame failure."""

    def __init__(self, reason: VerifySessionFrameReason | str) -> None:
        reason_code = reason.value if isinstance(reason, VerifySessionFrameReason) else reason
        super().__init__(reason_code)


class _VerifySessionFrameModel(StrictWireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )

    @model_validator(mode="before")
    @classmethod
    def redact_raw_fence_token_input(cls, value: object) -> object:
        if type(value) is cls:
            value = value.model_dump(mode="python")
        return redact_runtime_attempt_fence_token_input(value, invalid_input_field="invalid_frame_input")


def _verify_session_identity(identity: OperationIdentityV1) -> OperationIdentityV1:
    if identity.operation_kind != "verify_session":
        raise ValueError("verify_session_frame_operation_kind_invalid")
    return identity


class VerifySessionAcceptedAckV1(_VerifySessionFrameModel):
    contract_version: Literal["seektalent.source.verify-session.accepted-ack/v1"]
    identity: OperationIdentityV1
    dispatch_authorization: DispatchAuthorizationV1
    accepted_fact: Literal["dispatch_authorized"]

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, identity: OperationIdentityV1) -> OperationIdentityV1:
        return _verify_session_identity(identity)

    @model_validator(mode="after")
    def validate_authorization(self) -> VerifySessionAcceptedAckV1:
        try:
            validate_dispatch_authorization(self.identity, self.dispatch_authorization)
        except ValueError:
            raise ValueError("verify_session_frame_ack_authorization_invalid") from None
        return self


class VerifySessionRejectedV1(_VerifySessionFrameModel):
    contract_version: Literal["seektalent.source.verify-session.rejected/v1"]
    identity: OperationIdentityV1
    rejection_reason: Literal[
        "deadline_expired",
        "dispatch_authorization_invalid",
        "profile_binding_stale",
        "submit_not_admissible",
    ]

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, identity: OperationIdentityV1) -> OperationIdentityV1:
        return _verify_session_identity(identity)


class VerifySessionFailureV1(_VerifySessionFrameModel):
    contract_version: Literal["seektalent.source.verify-session.failure/v1"]
    identity: OperationIdentityV1
    failure_fact: Literal["no_effect_performed"]
    failure_reason: Literal[
        "exchange_deadline_expired",
        "sidecar_not_ready",
        "session_closed",
    ]

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, identity: OperationIdentityV1) -> OperationIdentityV1:
        return _verify_session_identity(identity)


class VerifySessionReconcileRequiredV1(_VerifySessionFrameModel):
    """A closed reply that transfers an accepted request to durable reconciliation."""

    contract_version: Literal["seektalent.source.verify-session.reconcile-required/v1"]
    identity: OperationIdentityV1
    reconciliation_fact: Literal["accepted_no_dispatch", "dispatch_not_observed"]

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, identity: OperationIdentityV1) -> OperationIdentityV1:
        return _verify_session_identity(identity)


class _EnvelopeBase(AuthenticatedFrameEnvelopeBase):
    pass


class _VerifySessionSubmitEnvelope(_EnvelopeBase):
    reply_to: None
    message_type: Literal["verify_session.submit"]
    payload: VerifySessionRequestV1


class _VerifySessionAcceptedAckEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["verify_session.accepted_ack"]
    payload: VerifySessionAcceptedAckV1


class _VerifySessionRejectedEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["verify_session.rejected"]
    payload: VerifySessionRejectedV1


class _VerifySessionResultEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["verify_session.result"]
    payload: VerifySessionResultV1


class _VerifySessionFailureEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["verify_session.failure"]
    payload: VerifySessionFailureV1


class _VerifySessionReconcileRequiredEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["verify_session.reconcile_required"]
    payload: VerifySessionReconcileRequiredV1


_AuthenticatedEnvelope: TypeAlias = Annotated[
    _VerifySessionSubmitEnvelope
    | _VerifySessionAcceptedAckEnvelope
    | _VerifySessionRejectedEnvelope
    | _VerifySessionResultEnvelope
    | _VerifySessionFailureEnvelope
    | _VerifySessionReconcileRequiredEnvelope,
    Field(discriminator="message_type"),
]
_ENVELOPE_ADAPTER = TypeAdapter(_AuthenticatedEnvelope)
_VerifySessionReplyPayload: TypeAlias = (
    VerifySessionAcceptedAckV1
    | VerifySessionRejectedV1
    | VerifySessionResultV1
    | VerifySessionFailureV1
    | VerifySessionReconcileRequiredV1
)
_VerifySessionReplyMessageType: TypeAlias = Literal[
    "verify_session.accepted_ack",
    "verify_session.rejected",
    "verify_session.result",
    "verify_session.failure",
    "verify_session.reconcile_required",
]


@dataclass(frozen=True, slots=True)
class ReceivedVerifySessionSubmit:
    message_id: str
    correlation_id: str | None
    payload: VerifySessionRequestV1


@dataclass(frozen=True, slots=True)
class ReceivedVerifySessionAcceptedAck:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: VerifySessionAcceptedAckV1


@dataclass(frozen=True, slots=True)
class ReceivedVerifySessionRejected:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: VerifySessionRejectedV1


@dataclass(frozen=True, slots=True)
class ReceivedVerifySessionResult:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: VerifySessionResultV1


@dataclass(frozen=True, slots=True)
class ReceivedVerifySessionFailure:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: VerifySessionFailureV1


@dataclass(frozen=True, slots=True)
class ReceivedVerifySessionReconcileRequired:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: VerifySessionReconcileRequiredV1


ReceivedVerifySessionMessage: TypeAlias = (
    ReceivedVerifySessionSubmit
    | ReceivedVerifySessionAcceptedAck
    | ReceivedVerifySessionRejected
    | ReceivedVerifySessionResult
    | ReceivedVerifySessionFailure
    | ReceivedVerifySessionReconcileRequired
)


@dataclass(frozen=True, slots=True)
class _VerifySessionPending:
    correlation_id: str | None
    request: VerifySessionRequestEchoV1


class PostHandshakeVerifySessionSession(
    AuthenticatedFrameSession[_AuthenticatedEnvelope, ReceivedVerifySessionMessage, _VerifySessionPending]
):
    """One endpoint's strict verify-session submit and reply framing state."""

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
            error_factory=_verify_session_error,
            main_send_types=frozenset({"verify_session.submit"}),
            sidecar_send_types=frozenset(
                {
                    "verify_session.accepted_ack",
                    "verify_session.rejected",
                    "verify_session.result",
                    "verify_session.failure",
                    "verify_session.reconcile_required",
                }
            ),
            request_message_types=frozenset({"verify_session.submit"}),
            response_message_types=frozenset(
                {
                    "verify_session.accepted_ack",
                    "verify_session.rejected",
                    "verify_session.result",
                    "verify_session.failure",
                    "verify_session.reconcile_required",
                }
            ),
            reply_validator=_validate_verify_session_reply,
            received_message=_received_verify_session_message,
            pending_from_request=_verify_session_pending,
            reply_mismatch_reason=VerifySessionFrameReason.REPLY_MISMATCH.value,
            pending_request_limit_reason=VerifySessionFrameReason.PENDING_SUBMIT_LIMIT.value,
            max_frame_bytes=lambda: MAX_FRAME_BYTES,
            max_session_messages=lambda: MAX_SESSION_MESSAGES,
            max_pending_requests=lambda: MAX_PENDING_SUBMITS,
        )

    @classmethod
    def for_main(
        cls,
        *,
        session_id: str,
        protocol_minor: int,
        main_to_sidecar_key: bytes,
        sidecar_to_main_key: bytes,
    ) -> PostHandshakeVerifySessionSession:
        return cls(
            role="main",
            session_id=session_id,
            protocol_minor=protocol_minor,
            main_to_sidecar_key=main_to_sidecar_key,
            sidecar_to_main_key=sidecar_to_main_key,
        )

    @classmethod
    def for_sidecar(
        cls,
        *,
        session_id: str,
        protocol_minor: int,
        main_to_sidecar_key: bytes,
        sidecar_to_main_key: bytes,
    ) -> PostHandshakeVerifySessionSession:
        return cls(
            role="sidecar",
            session_id=session_id,
            protocol_minor=protocol_minor,
            main_to_sidecar_key=main_to_sidecar_key,
            sidecar_to_main_key=sidecar_to_main_key,
        )

    def encode_submit(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        payload: VerifySessionRequestV1,
    ) -> bytes:
        self._require_open()
        if self._role != "main":
            self._fail(VerifySessionFrameReason.UNEXPECTED_DIRECTION.value)
        if not isinstance(payload, VerifySessionRequestV1):
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        return self.encode(
            self._build_submit_envelope(
                sequence=self._require_send_sequence(),
                message_id=message_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )

    def encode_accepted_ack(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: VerifySessionAcceptedAckV1,
    ) -> bytes:
        if not isinstance(payload, VerifySessionAcceptedAckV1):
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        sequence, pending = self._reply_context(reply_to=reply_to)
        return self.encode(
            self._build_reply_envelope(
                sequence=sequence,
                message_id=message_id,
                reply_to=reply_to,
                correlation_id=pending.correlation_id,
                payload=payload,
                message_type="verify_session.accepted_ack",
            )
        )

    def encode_rejected(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: VerifySessionRejectedV1,
    ) -> bytes:
        if not isinstance(payload, VerifySessionRejectedV1):
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        sequence, pending = self._reply_context(reply_to=reply_to)
        return self.encode(
            self._build_reply_envelope(
                sequence=sequence,
                message_id=message_id,
                reply_to=reply_to,
                correlation_id=pending.correlation_id,
                payload=payload,
                message_type="verify_session.rejected",
            )
        )

    def encode_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: VerifySessionResultV1,
    ) -> bytes:
        if not isinstance(payload, VerifySessionResultV1):
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        sequence, pending = self._reply_context(reply_to=reply_to)
        return self.encode(
            self._build_reply_envelope(
                sequence=sequence,
                message_id=message_id,
                reply_to=reply_to,
                correlation_id=pending.correlation_id,
                payload=payload,
                message_type="verify_session.result",
            )
        )

    def encode_failure(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: VerifySessionFailureV1,
    ) -> bytes:
        if not isinstance(payload, VerifySessionFailureV1):
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        sequence, pending = self._reply_context(reply_to=reply_to)
        return self.encode(
            self._build_reply_envelope(
                sequence=sequence,
                message_id=message_id,
                reply_to=reply_to,
                correlation_id=pending.correlation_id,
                payload=payload,
                message_type="verify_session.failure",
            )
        )

    def encode_reconcile_required(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: VerifySessionReconcileRequiredV1,
    ) -> bytes:
        if not isinstance(payload, VerifySessionReconcileRequiredV1):
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        sequence, pending = self._reply_context(reply_to=reply_to)
        return self.encode(
            self._build_reply_envelope(
                sequence=sequence,
                message_id=message_id,
                reply_to=reply_to,
                correlation_id=pending.correlation_id,
                payload=payload,
                message_type="verify_session.reconcile_required",
            )
        )

    def _reply_context(
        self,
        *,
        reply_to: str,
    ) -> tuple[int, _VerifySessionPending]:
        self._require_open()
        if self._role != "sidecar":
            self._fail(VerifySessionFrameReason.UNEXPECTED_DIRECTION.value)
        if type(reply_to) is not str:
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        pending = self._pending_request(reply_to)
        if not isinstance(pending, _VerifySessionPending):
            self._fail(VerifySessionFrameReason.WRONG_REPLY.value)
        return self._require_send_sequence(), pending

    def _build_reply_envelope(
        self,
        *,
        sequence: int,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: _VerifySessionReplyPayload,
        message_type: _VerifySessionReplyMessageType,
    ) -> _AuthenticatedEnvelope:
        validation_failed = False
        envelope: _AuthenticatedEnvelope | None = None
        try:
            envelope = _ENVELOPE_ADAPTER.validate_python(
                {
                    "protocol_name": PROTOCOL_NAME,
                    "protocol_major": PROTOCOL_MAJOR,
                    "protocol_minor": self._protocol_minor,
                    "session_id": self._session_id,
                    "direction_seq": sequence,
                    "message_id": message_id,
                    "reply_to": reply_to,
                    "message_type": message_type,
                    "correlation_id": correlation_id,
                    "payload": payload,
                    "auth_tag": ZERO_AUTH_TAG,
                },
                strict=True,
            )
        except (TypeError, ValueError, ValidationError):
            validation_failed = True
        if validation_failed or envelope is None:
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        return envelope

    def _build_submit_envelope(
        self,
        *,
        sequence: int,
        message_id: str,
        correlation_id: str | None,
        payload: VerifySessionRequestV1,
    ) -> _VerifySessionSubmitEnvelope:
        validation_failed = False
        envelope: _VerifySessionSubmitEnvelope | None = None
        try:
            envelope = _VerifySessionSubmitEnvelope(
                protocol_name=PROTOCOL_NAME,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=self._protocol_minor,
                session_id=self._session_id,
                direction_seq=sequence,
                message_id=message_id,
                reply_to=None,
                message_type="verify_session.submit",
                correlation_id=correlation_id,
                payload=payload,
                auth_tag=ZERO_AUTH_TAG,
            )
        except ValidationError:
            validation_failed = True
        if validation_failed or envelope is None:
            self._fail(VerifySessionFrameReason.SCHEMA_VALIDATION.value)
        return envelope


def _verify_session_error(reason_code: str) -> VerifySessionFrameError:
    return VerifySessionFrameError(VerifySessionFrameReason(reason_code))


def _validate_verify_session_reply(
    request: _VerifySessionPending,
    response: _AuthenticatedEnvelope,
    state: object | None,
) -> PendingReply:
    if response.correlation_id != request.correlation_id:
        raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value)
    submit = request.request
    if isinstance(response, _VerifySessionAcceptedAckEnvelope):
        if state is not None:
            raise ReplyValidationError(VerifySessionFrameReason.RESPONSE_STATE_MISMATCH.value)
        try:
            validate_verify_session_durable_reply_identity(submit, response.payload.identity)
        except (TypeError, ValueError):
            raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value) from None
        if response.payload.dispatch_authorization != submit.dispatch_authorization:
            raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value)
        return PendingReply.pending("accepted")
    if isinstance(response, _VerifySessionRejectedEnvelope):
        if state is not None:
            raise ReplyValidationError(VerifySessionFrameReason.RESPONSE_STATE_MISMATCH.value)
        # Rejection is a fresh admission decision for this transport submit, not a durable replay.
        if response.payload.identity != submit.identity:
            raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value)
        return PendingReply.terminal()
    if isinstance(response, _VerifySessionResultEnvelope):
        if state != "accepted":
            raise ReplyValidationError(VerifySessionFrameReason.RESPONSE_STATE_MISMATCH.value)
        try:
            validate_verify_session_result_echo_facts(submit, response.payload)
        except ValueError:
            raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value) from None
        return PendingReply.terminal()
    if isinstance(response, _VerifySessionFailureEnvelope):
        if state != "accepted":
            raise ReplyValidationError(VerifySessionFrameReason.RESPONSE_STATE_MISMATCH.value)
        try:
            validate_verify_session_durable_reply_identity(submit, response.payload.identity)
        except (TypeError, ValueError):
            raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value)
        return PendingReply.terminal()
    if isinstance(response, _VerifySessionReconcileRequiredEnvelope):
        if state != "accepted":
            raise ReplyValidationError(VerifySessionFrameReason.RESPONSE_STATE_MISMATCH.value)
        try:
            validate_verify_session_durable_reply_identity(submit, response.payload.identity)
        except (TypeError, ValueError):
            raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value) from None
        return PendingReply.terminal()
    raise ReplyValidationError(VerifySessionFrameReason.REPLY_MISMATCH.value)


def _received_verify_session_message(envelope: _AuthenticatedEnvelope) -> ReceivedVerifySessionMessage:
    if isinstance(envelope, _VerifySessionSubmitEnvelope):
        return ReceivedVerifySessionSubmit(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _VerifySessionAcceptedAckEnvelope):
        return ReceivedVerifySessionAcceptedAck(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _VerifySessionRejectedEnvelope):
        return ReceivedVerifySessionRejected(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _VerifySessionResultEnvelope):
        return ReceivedVerifySessionResult(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    if isinstance(envelope, _VerifySessionFailureEnvelope):
        return ReceivedVerifySessionFailure(
            message_id=envelope.message_id,
            reply_to=envelope.reply_to,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    return ReceivedVerifySessionReconcileRequired(
        message_id=envelope.message_id,
        reply_to=envelope.reply_to,
        correlation_id=envelope.correlation_id,
        payload=envelope.payload,
    )


def _verify_session_pending(envelope: _AuthenticatedEnvelope) -> _VerifySessionPending:
    if not isinstance(envelope, _VerifySessionSubmitEnvelope):
        raise ValueError("verify_session_frame_pending_message_invalid")
    return _VerifySessionPending(
        correlation_id=envelope.correlation_id,
        request=verify_session_request_echo(envelope.payload),
    )


__all__ = [
    "PostHandshakeVerifySessionSession",
    "ReceivedVerifySessionAcceptedAck",
    "ReceivedVerifySessionFailure",
    "ReceivedVerifySessionMessage",
    "ReceivedVerifySessionReconcileRequired",
    "ReceivedVerifySessionRejected",
    "ReceivedVerifySessionResult",
    "ReceivedVerifySessionSubmit",
    "VerifySessionAcceptedAckV1",
    "VerifySessionFailureV1",
    "VerifySessionFrameError",
    "VerifySessionFrameReason",
    "VerifySessionReconcileRequiredV1",
    "VerifySessionRejectedV1",
]
