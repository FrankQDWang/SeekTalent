"""History query/result composition over the reusable authenticated frame core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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
from seektalent.source_port.history_contract import (
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryResultV1,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)
from seektalent.source_port.wire_primitives import Opaque96, canonical_json_bytes


MAX_FRAME_BYTES = DEFAULT_MAX_FRAME_BYTES
MAX_SESSION_MESSAGES = DEFAULT_MAX_SESSION_MESSAGES
MAX_PENDING_QUERIES = DEFAULT_MAX_PENDING_REQUESTS

_HistoryResult = (
    SourceHistoryMatched | SourceHistoryNotFound | SourceHistoryIdentityConflict | SourceHistoryUnavailable
)
_HISTORY_RESULT_TYPES = (
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryIdentityConflict,
    SourceHistoryUnavailable,
)
_QUERY_ECHO_FIELDS = tuple(name for name in SourceHistoryQueryV1.model_fields if name != "contract_version")


class HistoryFrameReason(StrEnum):
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
    PENDING_QUERY_LIMIT = "source_port_pending_query_limit"
    WRONG_REPLY = "source_port_wrong_reply"
    RESULT_ECHO_MISMATCH = "source_port_result_echo_mismatch"


class HistoryFrameError(AuthenticatedFrameError):
    """A sanitized Source Port history-frame failure."""

    def __init__(self, reason: HistoryFrameReason | str) -> None:
        reason_code = reason.value if isinstance(reason, HistoryFrameReason) else reason
        super().__init__(reason_code)


class SourceHistoryAdmissionReason(StrEnum):
    QUERY_IN_FLIGHT = "source_history_admission_query_in_flight"
    SESSION_UNUSABLE = "source_history_admission_session_unusable"
    UNEXPECTED_MESSAGE = "source_history_admission_unexpected_message"
    MULTIPLE_MESSAGES = "source_history_admission_multiple_messages"
    READER_RESULT_INVALID = "source_history_admission_reader_result_invalid"


class SourceHistoryAdmissionError(ValueError):
    """A sanitized authenticated history exchange failure."""

    def __init__(self, reason: SourceHistoryAdmissionReason) -> None:
        self.reason = reason
        super().__init__(reason.value)

    def __repr__(self) -> str:
        return f"SourceHistoryAdmissionError(reason={self.reason.value!r})"


class _EnvelopeBase(AuthenticatedFrameEnvelopeBase):
    pass


class _OperationQueryEnvelope(_EnvelopeBase):
    reply_to: None
    message_type: Literal["operation.query"]
    payload: SourceHistoryQueryV1


class _OperationQueryResultEnvelope(_EnvelopeBase):
    reply_to: Opaque96
    message_type: Literal["operation.query.result"]
    payload: SourceHistoryQueryResultV1


_AuthenticatedEnvelope: TypeAlias = Annotated[
    _OperationQueryEnvelope | _OperationQueryResultEnvelope,
    Field(discriminator="message_type"),
]
_ENVELOPE_ADAPTER = TypeAdapter(_AuthenticatedEnvelope)
_HISTORY_RESULT_ADAPTER = TypeAdapter(SourceHistoryQueryResultV1)


@dataclass(frozen=True, slots=True)
class ReceivedHistoryQuery:
    message_id: str
    correlation_id: str | None
    payload: SourceHistoryQueryV1


@dataclass(frozen=True, slots=True)
class ReceivedHistoryResult:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: _HistoryResult


ReceivedHistoryMessage: TypeAlias = ReceivedHistoryQuery | ReceivedHistoryResult


@dataclass(frozen=True, slots=True)
class _HistoryPending:
    correlation_id: str | None
    payload: SourceHistoryQueryV1


class PostHandshakeHistorySession(
    AuthenticatedFrameSession[_AuthenticatedEnvelope, ReceivedHistoryMessage, _HistoryPending]
):
    """One endpoint's history-specific view of authenticated frame integrity."""

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
            error_factory=_history_error,
            main_send_types=frozenset({"operation.query"}),
            sidecar_send_types=frozenset({"operation.query.result"}),
            request_message_types=frozenset({"operation.query"}),
            response_message_types=frozenset({"operation.query.result"}),
            reply_validator=_validate_history_reply,
            received_message=_received_history_message,
            pending_from_request=_history_pending,
            reply_mismatch_reason=HistoryFrameReason.RESULT_ECHO_MISMATCH.value,
            max_frame_bytes=lambda: MAX_FRAME_BYTES,
            max_session_messages=lambda: MAX_SESSION_MESSAGES,
            max_pending_requests=lambda: MAX_PENDING_QUERIES,
        )

    @classmethod
    def for_main(
        cls,
        *,
        session_id: str,
        protocol_minor: int,
        main_to_sidecar_key: bytes,
        sidecar_to_main_key: bytes,
    ) -> PostHandshakeHistorySession:
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
    ) -> PostHandshakeHistorySession:
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
            self._fail(HistoryFrameReason.UNEXPECTED_DIRECTION.value)
        if not isinstance(payload, SourceHistoryQueryV1):
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION.value)
        envelope = self._build_query_envelope(
            sequence=self._require_send_sequence(),
            message_id=message_id,
            correlation_id=correlation_id,
            payload=payload,
        )
        return self.encode(envelope)

    def encode_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: _HistoryResult,
    ) -> bytes:
        self._require_open()
        if self._role != "sidecar":
            self._fail(HistoryFrameReason.UNEXPECTED_DIRECTION.value)
        if not isinstance(payload, _HISTORY_RESULT_TYPES) or type(reply_to) is not str:
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION.value)
        pending = self._pending_request(reply_to)
        if not isinstance(pending, _HistoryPending):
            self._fail(HistoryFrameReason.WRONG_REPLY.value)
        if not _result_echoes_query(payload, pending.payload):
            self._fail(HistoryFrameReason.RESULT_ECHO_MISMATCH.value)
        envelope = self._build_result_envelope(
            sequence=self._require_send_sequence(),
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=pending.correlation_id,
            payload=payload,
        )
        return self.encode(envelope)

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
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION.value)
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
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION.value)
        return envelope


def canonical_source_history_semantics_bytes(
    query: SourceHistoryQueryV1,
    result: SourceHistoryQueryResultV1,
) -> bytes:
    """Return session-independent RFC 8785 bytes for one query and semantic result."""
    if type(query) is not SourceHistoryQueryV1 or type(result) not in _HISTORY_RESULT_TYPES:
        raise TypeError("strict source history query and result required")
    validation_failed = False
    validated_query: SourceHistoryQueryV1 | None = None
    validated_result: SourceHistoryQueryResultV1 | None = None
    try:
        validated_query = SourceHistoryQueryV1.model_validate(
            query.model_dump(mode="python", warnings="error"),
            strict=True,
        )
        validated_result = _HISTORY_RESULT_ADAPTER.validate_python(
            result.model_dump(mode="python", warnings="error"),
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        validation_failed = True
    if validation_failed or validated_query is None or validated_result is None:
        raise HistoryFrameError(HistoryFrameReason.SCHEMA_VALIDATION) from None
    canonical_failed = False
    canonical: bytes | None = None
    try:
        canonical = canonical_json_bytes(
            {
                "query": validated_query.model_dump(mode="json"),
                "result": validated_result.model_dump(mode="json"),
            }
        )
    except ValueError:
        canonical_failed = True
    if canonical_failed or canonical is None:
        raise HistoryFrameError(HistoryFrameReason.SCHEMA_VALIDATION) from None
    return canonical


def _history_error(reason_code: str) -> HistoryFrameError:
    return HistoryFrameError(HistoryFrameReason(reason_code))


def _validate_history_reply(
    request: _HistoryPending,
    response: _AuthenticatedEnvelope,
    _: object | None,
) -> PendingReply:
    if not isinstance(response, _OperationQueryResultEnvelope):
        raise ReplyValidationError(HistoryFrameReason.RESULT_ECHO_MISMATCH.value)
    if response.correlation_id != request.correlation_id or not _result_echoes_query(response.payload, request.payload):
        raise ReplyValidationError(HistoryFrameReason.RESULT_ECHO_MISMATCH.value)
    return PendingReply.terminal()


def _received_history_message(envelope: _AuthenticatedEnvelope) -> ReceivedHistoryMessage:
    if isinstance(envelope, _OperationQueryEnvelope):
        return ReceivedHistoryQuery(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            payload=envelope.payload,
        )
    return ReceivedHistoryResult(
        message_id=envelope.message_id,
        reply_to=envelope.reply_to,
        correlation_id=envelope.correlation_id,
        payload=envelope.payload,
    )


def _history_pending(envelope: _AuthenticatedEnvelope) -> _HistoryPending:
    if not isinstance(envelope, _OperationQueryEnvelope):
        raise ValueError("source_port_history_pending_message_invalid")
    return _HistoryPending(correlation_id=envelope.correlation_id, payload=envelope.payload)


def _result_echoes_query(result: _HistoryResult, query: SourceHistoryQueryV1) -> bool:
    return all(getattr(result, field) == getattr(query, field) for field in _QUERY_ECHO_FIELDS)


__all__ = [
    "HistoryFrameError",
    "HistoryFrameReason",
    "PostHandshakeHistorySession",
    "ReceivedHistoryMessage",
    "ReceivedHistoryQuery",
    "ReceivedHistoryResult",
    "SourceHistoryAdmissionError",
    "SourceHistoryAdmissionReason",
    "canonical_source_history_semantics_bytes",
]
