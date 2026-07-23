"""Mixed-family authenticated framing for one post-handshake Source Port endpoint."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter, ValidationError

from seektalent.source_port import authenticated_history_frames as history_frames
from seektalent.source_port import authenticated_verify_session_frames as verify_frames
from seektalent.source_port.authenticated_frame_core import (
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
from seektalent.source_port.authenticated_history_frames import (
    _HISTORY_RESULT_TYPES,
    _HistoryPending,
    _OperationQueryEnvelope,
    _OperationQueryResultEnvelope,
    _history_pending,
    _received_history_message,
    _result_echoes_query,
    _validate_history_reply,
)
from seektalent.source_port.authenticated_verify_session_frames import (
    _VerifySessionAcceptedAckEnvelope,
    _VerifySessionFailureEnvelope,
    _VerifySessionPending,
    _VerifySessionReconcileRequiredEnvelope,
    _VerifySessionRejectedEnvelope,
    _VerifySessionResultEnvelope,
    _VerifySessionSubmitEnvelope,
    _received_verify_session_message,
    _validate_verify_session_reply,
    _verify_session_pending,
)
from seektalent.source_port.history_contract import (
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)


_HistoryResult = (
    SourceHistoryMatched | SourceHistoryNotFound | SourceHistoryIdentityConflict | SourceHistoryUnavailable
)
_TransportEnvelope: TypeAlias = Annotated[
    _OperationQueryEnvelope
    | _OperationQueryResultEnvelope
    | _VerifySessionSubmitEnvelope
    | _VerifySessionAcceptedAckEnvelope
    | _VerifySessionRejectedEnvelope
    | _VerifySessionResultEnvelope
    | _VerifySessionFailureEnvelope
    | _VerifySessionReconcileRequiredEnvelope,
    Field(discriminator="message_type"),
]
_TRANSPORT_ENVELOPE_ADAPTER = TypeAdapter(_TransportEnvelope)
_TransportPending: TypeAlias = _HistoryPending | _VerifySessionPending
ReceivedSourcePortMessage: TypeAlias = history_frames.ReceivedHistoryMessage | verify_frames.ReceivedVerifySessionMessage


class SourcePortTransportFrameError(history_frames.HistoryFrameError, verify_frames.VerifySessionFrameError):
    """A shared transport failure retaining both family error contracts."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)


class PostHandshakeSourcePortSession(verify_frames.PostHandshakeVerifySessionSession):
    """One endpoint's single mutable framing state for both Source Port families."""

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
        AuthenticatedFrameSession.__init__(
            self,
            role=role,
            session_id=session_id,
            protocol_minor=protocol_minor,
            main_to_sidecar_key=main_to_sidecar_key,
            sidecar_to_main_key=sidecar_to_main_key,
            envelope_adapter=_TRANSPORT_ENVELOPE_ADAPTER,
            error_factory=SourcePortTransportFrameError,
            main_send_types=frozenset({"operation.query", "verify_session.submit"}),
            sidecar_send_types=frozenset(
                {
                    "operation.query.result",
                    "verify_session.accepted_ack",
                    "verify_session.rejected",
                    "verify_session.result",
                    "verify_session.failure",
                    "verify_session.reconcile_required",
                }
            ),
            request_message_types=frozenset({"operation.query", "verify_session.submit"}),
            response_message_types=frozenset(
                {
                    "operation.query.result",
                    "verify_session.accepted_ack",
                    "verify_session.rejected",
                    "verify_session.result",
                    "verify_session.failure",
                    "verify_session.reconcile_required",
                }
            ),
            reply_validator=_validate_transport_reply,
            received_message=_received_transport_message,
            pending_from_request=_transport_pending,
            reply_mismatch_reason="source_port_wrong_reply",
            pending_request_limit_reason="source_port_pending_request_limit",
            max_frame_bytes=lambda: DEFAULT_MAX_FRAME_BYTES,
            max_session_messages=lambda: DEFAULT_MAX_SESSION_MESSAGES,
            max_pending_requests=lambda: DEFAULT_MAX_PENDING_REQUESTS,
        )
        self._initialize_verify_arrival_state()

    @classmethod
    def for_main(
        cls,
        *,
        session_id: str,
        protocol_minor: int,
        main_to_sidecar_key: bytes,
        sidecar_to_main_key: bytes,
    ) -> PostHandshakeSourcePortSession:
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
    ) -> PostHandshakeSourcePortSession:
        return cls(
            role="sidecar",
            session_id=session_id,
            protocol_minor=protocol_minor,
            main_to_sidecar_key=main_to_sidecar_key,
            sidecar_to_main_key=sidecar_to_main_key,
        )

    def encode_query(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        payload: SourceHistoryQueryV1,
    ) -> bytes:
        self._require_open()
        if self._role != "main":
            self._fail(history_frames.HistoryFrameReason.UNEXPECTED_DIRECTION.value)
        if not isinstance(payload, SourceHistoryQueryV1):
            self._fail(history_frames.HistoryFrameReason.SCHEMA_VALIDATION.value)
        return self._encode_authenticated_envelope(
            self._build_query_envelope(
                sequence=self._require_send_sequence(),
                message_id=message_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )

    def encode_history_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: _HistoryResult,
    ) -> bytes:
        self._require_open()
        if self._role != "sidecar":
            self._fail(history_frames.HistoryFrameReason.UNEXPECTED_DIRECTION.value)
        if not isinstance(payload, _HISTORY_RESULT_TYPES) or type(reply_to) is not str:
            self._fail(history_frames.HistoryFrameReason.SCHEMA_VALIDATION.value)
        pending = self._pending_request(reply_to)
        if not isinstance(pending, _HistoryPending):
            self._fail(history_frames.HistoryFrameReason.WRONG_REPLY.value)
        if not _result_echoes_query(payload, pending.payload):
            self._fail(history_frames.HistoryFrameReason.RESULT_ECHO_MISMATCH.value)
        return self._encode_authenticated_envelope(
            self._build_result_envelope(
                sequence=self._require_send_sequence(),
                message_id=message_id,
                reply_to=reply_to,
                correlation_id=pending.correlation_id,
                payload=payload,
            )
        )

    def _build_query_envelope(
        self,
        *,
        sequence: int,
        message_id: str,
        correlation_id: str | None,
        payload: SourceHistoryQueryV1,
    ) -> _OperationQueryEnvelope:
        validation_failed = False
        envelope: _OperationQueryEnvelope | None = None
        try:
            envelope = _OperationQueryEnvelope(
                protocol_name=PROTOCOL_NAME,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=self._protocol_minor,
                session_id=self._session_id,
                direction_seq=sequence,
                message_id=message_id,
                reply_to=None,
                message_type="operation.query",
                correlation_id=correlation_id,
                payload=payload,
                auth_tag=ZERO_AUTH_TAG,
            )
        except ValidationError:
            validation_failed = True
        if validation_failed or envelope is None:
            self._fail(history_frames.HistoryFrameReason.SCHEMA_VALIDATION.value)
        return envelope

    def _build_result_envelope(
        self,
        *,
        sequence: int,
        message_id: str,
        reply_to: str,
        correlation_id: str | None,
        payload: _HistoryResult,
    ) -> _OperationQueryResultEnvelope:
        validation_failed = False
        envelope: _OperationQueryResultEnvelope | None = None
        try:
            envelope = _OperationQueryResultEnvelope(
                protocol_name=PROTOCOL_NAME,
                protocol_major=PROTOCOL_MAJOR,
                protocol_minor=self._protocol_minor,
                session_id=self._session_id,
                direction_seq=sequence,
                message_id=message_id,
                reply_to=reply_to,
                message_type="operation.query.result",
                correlation_id=correlation_id,
                payload=payload,
                auth_tag=ZERO_AUTH_TAG,
            )
        except ValidationError:
            validation_failed = True
        if validation_failed or envelope is None:
            self._fail(history_frames.HistoryFrameReason.SCHEMA_VALIDATION.value)
        return envelope


def _validate_transport_reply(
    request: _TransportPending,
    response: _TransportEnvelope,
    state: object | None,
) -> PendingReply:
    if isinstance(request, _HistoryPending):
        if not isinstance(response, (_OperationQueryEnvelope, _OperationQueryResultEnvelope)):
            raise ReplyValidationError("source_port_wrong_reply")
        return _validate_history_reply(request, response, state)
    if isinstance(request, _VerifySessionPending):
        if not isinstance(
            response,
            (
                _VerifySessionSubmitEnvelope,
                _VerifySessionAcceptedAckEnvelope,
                _VerifySessionRejectedEnvelope,
                _VerifySessionResultEnvelope,
                _VerifySessionFailureEnvelope,
                _VerifySessionReconcileRequiredEnvelope,
            ),
        ):
            raise ReplyValidationError("source_port_wrong_reply")
        return _validate_verify_session_reply(request, response, state)
    raise ReplyValidationError("source_port_wrong_reply")


def _received_transport_message(envelope: _TransportEnvelope) -> ReceivedSourcePortMessage:
    if isinstance(envelope, (_OperationQueryEnvelope, _OperationQueryResultEnvelope)):
        return _received_history_message(envelope)
    return _received_verify_session_message(envelope)


def _transport_pending(envelope: _TransportEnvelope) -> _TransportPending:
    if isinstance(envelope, _OperationQueryEnvelope):
        return _history_pending(envelope)
    if isinstance(envelope, _VerifySessionSubmitEnvelope):
        return _verify_session_pending(envelope)
    raise ValueError("source_port_transport_pending_message_invalid")


__all__ = [
    "PostHandshakeSourcePortSession",
    "ReceivedSourcePortMessage",
    "SourcePortTransportFrameError",
]
