"""Post-handshake frame integrity for the Source Port history contract.

This module does not establish peer identity or perform a handshake. It only
validates frames with direction keys supplied by the future spawn/handshake
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import hmac
import json
from typing import Annotated, Literal, Never, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
import rfc8785

from seektalent.source_port.history_contract import (
    JSON_SAFE_INTEGER,
    NonNegativeJsonInteger,
    Opaque96,
    Sha256,
    ExactIntegerOne,
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryResultV1,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)


PROTOCOL_NAME = "seektalent.source-execution-port"
PROTOCOL_MAJOR = 1
MAX_FRAME_BYTES = 1_048_576
MAX_SESSION_MESSAGES = 65_536
MAX_PENDING_QUERIES = 256
_FRAME_AUTH_DOMAIN = b"seektalent-source-port-frame-auth-v1"
_MAIN_TO_SIDECAR = b"main-to-sidecar"
_SIDECAR_TO_MAIN = b"sidecar-to-main"
_ZERO_AUTH_TAG = "0" * 64

_EndpointRole: TypeAlias = Literal["main", "sidecar"]
_HistoryResult = SourceHistoryMatched | SourceHistoryNotFound | SourceHistoryIdentityConflict | SourceHistoryUnavailable
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


class HistoryFrameError(ValueError):
    """A sanitized Source Port protocol failure."""

    def __init__(self, reason: HistoryFrameReason) -> None:
        self.reason_code = reason.value
        super().__init__(reason.value)

    def __repr__(self) -> str:
        return f"HistoryFrameError(reason_code={self.reason_code!r})"


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


AuthTag = Annotated[Sha256, Field(repr=False)]


class _EnvelopeBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    protocol_name: Literal["seektalent.source-execution-port"]
    protocol_major: ExactIntegerOne
    protocol_minor: NonNegativeJsonInteger
    session_id: Opaque96
    direction_seq: Annotated[int, Field(strict=True, ge=1, le=JSON_SAFE_INTEGER)]
    message_id: Opaque96
    correlation_id: Opaque96 | None
    auth_tag: AuthTag


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
_SESSION_ID_ADAPTER = TypeAdapter(Opaque96)
_PROTOCOL_MINOR_ADAPTER = TypeAdapter(NonNegativeJsonInteger)


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
class _PendingQuery:
    correlation_id: str | None
    payload: SourceHistoryQueryV1


class _StrictJsonFailure(Exception):
    def __init__(self, reason: HistoryFrameReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class PostHandshakeHistorySession:
    """One endpoint's bounded state for history query/result frame integrity."""

    __slots__ = (
        "_body",
        "_closed",
        "_closed_reason",
        "_expected_body_length",
        "_header",
        "_message_ids",
        "_next_receive_sequence",
        "_next_send_sequence",
        "_pending_queries",
        "_protocol_minor",
        "_receive_direction",
        "_receive_key",
        "_role",
        "_send_direction",
        "_send_key",
        "_session_id",
    )

    def __init__(
        self,
        *,
        role: _EndpointRole,
        session_id: str,
        protocol_minor: int,
        main_to_sidecar_key: bytes,
        sidecar_to_main_key: bytes,
    ) -> None:
        if role not in ("main", "sidecar"):
            raise HistoryFrameError(HistoryFrameReason.INVALID_SESSION_CONFIG)
        self._session_id = _validated_session_id(session_id)
        self._protocol_minor = _validated_protocol_minor(protocol_minor)
        main_to_sidecar_key = _validated_direction_key(main_to_sidecar_key)
        sidecar_to_main_key = _validated_direction_key(sidecar_to_main_key)
        self._role = role
        if role == "main":
            self._send_key = main_to_sidecar_key
            self._receive_key = sidecar_to_main_key
            self._send_direction = _MAIN_TO_SIDECAR
            self._receive_direction = _SIDECAR_TO_MAIN
        else:
            self._send_key = sidecar_to_main_key
            self._receive_key = main_to_sidecar_key
            self._send_direction = _SIDECAR_TO_MAIN
            self._receive_direction = _MAIN_TO_SIDECAR
        self._next_send_sequence = 1
        self._next_receive_sequence = 1
        self._message_ids: set[str] = set()
        self._pending_queries: dict[str, _PendingQuery] = {}
        self._header = bytearray()
        self._body = bytearray()
        self._expected_body_length: int | None = None
        self._closed = False
        self._closed_reason: str | None = None

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

    def __repr__(self) -> str:
        return f"PostHandshakeHistorySession(role={self._role!r}, closed={self._closed!r})"

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def closed_reason(self) -> str | None:
        return self._closed_reason

    def encode_query(
        self,
        *,
        message_id: str,
        correlation_id: str | None,
        payload: SourceHistoryQueryV1,
    ) -> bytes:
        self._require_open()
        if self._role != "main":
            self._fail(HistoryFrameReason.UNEXPECTED_DIRECTION)
        if not isinstance(payload, SourceHistoryQueryV1):
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION)
        sequence = self._require_send_sequence()
        envelope = self._build_query_envelope(
            sequence=sequence,
            message_id=message_id,
            correlation_id=correlation_id,
            payload=payload,
        )
        self._require_new_message_id(envelope.message_id)
        self._require_pending_capacity()
        frame = self._encode_or_close(envelope)
        self._message_ids.add(envelope.message_id)
        self._pending_queries[envelope.message_id] = _PendingQuery(
            correlation_id=envelope.correlation_id,
            payload=payload,
        )
        self._next_send_sequence += 1
        return frame

    def encode_result(
        self,
        *,
        message_id: str,
        reply_to: str,
        payload: _HistoryResult,
    ) -> bytes:
        self._require_open()
        if self._role != "sidecar":
            self._fail(HistoryFrameReason.UNEXPECTED_DIRECTION)
        if not isinstance(payload, _HISTORY_RESULT_TYPES):
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION)
        if type(reply_to) is not str:
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION)
        pending = self._pending_queries.get(reply_to)
        if pending is None:
            self._fail(HistoryFrameReason.WRONG_REPLY)
        if not _result_echoes_query(payload, pending.payload):
            self._fail(HistoryFrameReason.RESULT_ECHO_MISMATCH)
        sequence = self._require_send_sequence()
        envelope = self._build_result_envelope(
            sequence=sequence,
            message_id=message_id,
            reply_to=reply_to,
            correlation_id=pending.correlation_id,
            payload=payload,
        )
        self._require_new_message_id(envelope.message_id)
        frame = self._encode_or_close(envelope)
        self._message_ids.add(envelope.message_id)
        del self._pending_queries[envelope.reply_to]
        self._next_send_sequence += 1
        return frame

    def feed(self, data: bytes) -> tuple[ReceivedHistoryMessage, ...]:
        self._require_open()
        if type(data) is not bytes:
            self._fail(HistoryFrameReason.RAW_INPUT_REQUIRED)
        received: list[ReceivedHistoryMessage] = []
        view = memoryview(data)
        offset = 0
        try:
            while offset < len(view):
                if self._expected_body_length is None:
                    take = min(4 - len(self._header), len(view) - offset)
                    self._header.extend(view[offset : offset + take])
                    offset += take
                    if len(self._header) < 4:
                        break
                    frame_length = int.from_bytes(self._header, "big")
                    self._header.clear()
                    if frame_length == 0:
                        raise HistoryFrameError(HistoryFrameReason.FRAME_LENGTH_INVALID)
                    if frame_length > MAX_FRAME_BYTES:
                        raise HistoryFrameError(HistoryFrameReason.FRAME_TOO_LARGE)
                    self._expected_body_length = frame_length
                remaining = self._expected_body_length - len(self._body)
                take = min(remaining, len(view) - offset)
                self._body.extend(view[offset : offset + take])
                offset += take
                if len(self._body) < self._expected_body_length:
                    continue
                body = bytes(self._body)
                frame_length = self._expected_body_length
                self._body.clear()
                self._expected_body_length = None
                envelope = _decode_envelope(
                    body,
                    frame_length=frame_length,
                    session_id=self._session_id,
                    protocol_minor=self._protocol_minor,
                    direction=self._receive_direction,
                    direction_key=self._receive_key,
                )
                received.append(self._accept_envelope(envelope))
        except HistoryFrameError as exc:
            self._close(exc.reason_code)
            raise
        return tuple(received)

    def feed_eof(self) -> None:
        self._require_open()
        if self._header or self._body or self._expected_body_length is not None:
            self._fail(HistoryFrameReason.TRUNCATED_FRAME)
        self._close(None)

    def require_frame_boundary(self) -> None:
        """Fail closed when a completed message was followed by a partial frame."""
        self._require_open()
        if self._header or self._body or self._expected_body_length is not None:
            self._fail(HistoryFrameReason.TRUNCATED_FRAME)

    def _build_query_envelope(
        self,
        *,
        sequence: int,
        message_id: str,
        correlation_id: str | None,
        payload: SourceHistoryQueryV1,
    ) -> _OperationQueryEnvelope:
        failed = False
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
                auth_tag=_ZERO_AUTH_TAG,
            )
        except ValidationError:
            failed = True
        if failed:
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION)
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
        failed = False
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
                auth_tag=_ZERO_AUTH_TAG,
            )
        except ValidationError:
            failed = True
        if failed:
            self._fail(HistoryFrameReason.SCHEMA_VALIDATION)
        return envelope

    def _encode_or_close(self, envelope: _AuthenticatedEnvelope) -> bytes:
        try:
            return _encode_envelope(envelope, direction=self._send_direction, direction_key=self._send_key)
        except HistoryFrameError as exc:
            self._close(exc.reason_code)
            raise

    def _accept_envelope(self, envelope: _AuthenticatedEnvelope) -> ReceivedHistoryMessage:
        if envelope.direction_seq != self._next_receive_sequence:
            self._fail(HistoryFrameReason.SEQUENCE_MISMATCH)
        if self._next_receive_sequence >= JSON_SAFE_INTEGER:
            self._fail(HistoryFrameReason.SEQUENCE_EXHAUSTED)
        self._require_new_message_id(envelope.message_id)
        if isinstance(envelope, _OperationQueryEnvelope):
            if self._role != "sidecar":
                self._fail(HistoryFrameReason.UNEXPECTED_DIRECTION)
            self._require_pending_capacity()
            message: ReceivedHistoryMessage = ReceivedHistoryQuery(
                message_id=envelope.message_id,
                correlation_id=envelope.correlation_id,
                payload=envelope.payload,
            )
            self._pending_queries[envelope.message_id] = _PendingQuery(
                correlation_id=envelope.correlation_id,
                payload=envelope.payload,
            )
        else:
            if self._role != "main":
                self._fail(HistoryFrameReason.UNEXPECTED_DIRECTION)
            pending = self._pending_queries.get(envelope.reply_to)
            if pending is None:
                self._fail(HistoryFrameReason.WRONG_REPLY)
            if envelope.correlation_id != pending.correlation_id or not _result_echoes_query(
                envelope.payload, pending.payload
            ):
                self._fail(HistoryFrameReason.RESULT_ECHO_MISMATCH)
            message = ReceivedHistoryResult(
                message_id=envelope.message_id,
                reply_to=envelope.reply_to,
                correlation_id=envelope.correlation_id,
                payload=envelope.payload,
            )
            del self._pending_queries[envelope.reply_to]
        self._message_ids.add(envelope.message_id)
        self._next_receive_sequence += 1
        return message

    def _require_new_message_id(self, message_id: str) -> None:
        if message_id in self._message_ids:
            self._fail(HistoryFrameReason.DUPLICATE_MESSAGE_ID)
        if len(self._message_ids) >= MAX_SESSION_MESSAGES:
            self._fail(HistoryFrameReason.MESSAGE_LIMIT)

    def _require_pending_capacity(self) -> None:
        if len(self._pending_queries) >= MAX_PENDING_QUERIES:
            self._fail(HistoryFrameReason.PENDING_QUERY_LIMIT)

    def _require_send_sequence(self) -> int:
        if self._next_send_sequence >= JSON_SAFE_INTEGER:
            self._fail(HistoryFrameReason.SEQUENCE_EXHAUSTED)
        return self._next_send_sequence

    def _require_open(self) -> None:
        if self._closed:
            raise HistoryFrameError(HistoryFrameReason.SESSION_CLOSED)

    def _fail(self, reason: HistoryFrameReason) -> Never:
        self._close(reason.value)
        raise HistoryFrameError(reason)

    def _close(self, reason_code: str | None) -> None:
        if not self._closed:
            self._closed_reason = reason_code
        self._closed = True
        self._send_key = b""
        self._receive_key = b""
        self._header.clear()
        self._body.clear()
        self._expected_body_length = None
        self._message_ids.clear()
        self._pending_queries.clear()


def _validated_session_id(value: str) -> str:
    failed = False
    try:
        validated = _SESSION_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        failed = True
    if failed:
        raise HistoryFrameError(HistoryFrameReason.INVALID_SESSION_CONFIG)
    return validated


def _validated_protocol_minor(value: int) -> int:
    failed = False
    try:
        validated = _PROTOCOL_MINOR_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        failed = True
    if failed:
        raise HistoryFrameError(HistoryFrameReason.INVALID_SESSION_CONFIG)
    return validated


def _validated_direction_key(value: bytes) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise HistoryFrameError(HistoryFrameReason.INVALID_DIRECTION_KEY)
    return value


def _encode_envelope(
    envelope: _AuthenticatedEnvelope,
    *,
    direction: bytes,
    direction_key: bytes,
) -> bytes:
    unsigned = envelope.model_dump(mode="json", exclude={"auth_tag"})
    canonical_failed = False
    try:
        unsigned_body = rfc8785.dumps(unsigned)
        zero_tag_body = rfc8785.dumps({**unsigned, "auth_tag": _ZERO_AUTH_TAG})
    except (rfc8785.CanonicalizationError, RecursionError):
        canonical_failed = True
    if canonical_failed:
        raise HistoryFrameError(HistoryFrameReason.SCHEMA_VALIDATION)
    frame_length = len(zero_tag_body)
    if frame_length > MAX_FRAME_BYTES:
        raise HistoryFrameError(HistoryFrameReason.FRAME_TOO_LARGE)
    auth_tag = hmac.new(
        direction_key,
        _auth_input(
            session_id=envelope.session_id,
            direction=direction,
            sequence=envelope.direction_seq,
            frame_length=frame_length,
            unsigned_body=unsigned_body,
        ),
        sha256,
    ).hexdigest()
    body = rfc8785.dumps({**unsigned, "auth_tag": auth_tag})
    if len(body) != frame_length:
        raise HistoryFrameError(HistoryFrameReason.FRAME_LENGTH_MISMATCH)
    return frame_length.to_bytes(4, "big") + body


def _decode_envelope(
    body: bytes,
    *,
    frame_length: int,
    session_id: str,
    protocol_minor: int,
    direction: bytes,
    direction_key: bytes,
) -> _AuthenticatedEnvelope:
    parsed = _strict_canonical_json_object(body)
    validation_failed = False
    try:
        envelope = _ENVELOPE_ADAPTER.validate_json(body, strict=True)
    except ValidationError:
        validation_failed = True
    if validation_failed:
        raise HistoryFrameError(HistoryFrameReason.SCHEMA_VALIDATION)
    if envelope.protocol_name != PROTOCOL_NAME or envelope.protocol_major != PROTOCOL_MAJOR:
        raise HistoryFrameError(HistoryFrameReason.PROTOCOL_MISMATCH)
    if envelope.protocol_minor != protocol_minor:
        raise HistoryFrameError(HistoryFrameReason.PROTOCOL_MISMATCH)
    if envelope.session_id != session_id:
        raise HistoryFrameError(HistoryFrameReason.SESSION_MISMATCH)
    unsigned = dict(parsed)
    auth_tag = unsigned.pop("auth_tag")
    canonical_failed = False
    try:
        unsigned_body = rfc8785.dumps(unsigned)
        expected_length = len(rfc8785.dumps({**unsigned, "auth_tag": _ZERO_AUTH_TAG}))
    except (rfc8785.CanonicalizationError, RecursionError):
        canonical_failed = True
    if canonical_failed:
        raise HistoryFrameError(HistoryFrameReason.SCHEMA_VALIDATION)
    if expected_length != frame_length:
        raise HistoryFrameError(HistoryFrameReason.FRAME_LENGTH_MISMATCH)
    expected_tag = hmac.new(
        direction_key,
        _auth_input(
            session_id=session_id,
            direction=direction,
            sequence=envelope.direction_seq,
            frame_length=frame_length,
            unsigned_body=unsigned_body,
        ),
        sha256,
    ).hexdigest()
    if not isinstance(auth_tag, str) or not hmac.compare_digest(auth_tag, expected_tag):
        raise HistoryFrameError(HistoryFrameReason.BAD_AUTH_TAG)
    return envelope


def _strict_canonical_json_object(body: bytes) -> dict[str, object]:
    decode_failed = False
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        decode_failed = True
    if decode_failed:
        raise HistoryFrameError(HistoryFrameReason.INVALID_UTF8)
    if text.startswith("\ufeff"):
        raise HistoryFrameError(HistoryFrameReason.BOM_FORBIDDEN)

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _StrictJsonFailure(HistoryFrameReason.DUPLICATE_KEY)
            result[key] = value
        return result

    def reject_float(_: str) -> float:
        raise _StrictJsonFailure(HistoryFrameReason.ILLEGAL_NUMBER)

    def reject_constant(_: str) -> float:
        raise _StrictJsonFailure(HistoryFrameReason.ILLEGAL_NUMBER)

    def parse_integer(value: str) -> int:
        if value == "-0":
            raise _StrictJsonFailure(HistoryFrameReason.ILLEGAL_NUMBER)
        digits = value[1:] if value.startswith("-") else value
        if len(digits) > 16:
            raise _StrictJsonFailure(HistoryFrameReason.ILLEGAL_NUMBER)
        parsed = int(value)
        if parsed < -JSON_SAFE_INTEGER or parsed > JSON_SAFE_INTEGER:
            raise _StrictJsonFailure(HistoryFrameReason.ILLEGAL_NUMBER)
        return parsed

    reason: HistoryFrameReason | None = None
    payload: object | None = None
    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_float=reject_float,
            parse_int=parse_integer,
            parse_constant=reject_constant,
        )
    except _StrictJsonFailure as exc:
        reason = exc.reason
    except (json.JSONDecodeError, RecursionError):
        reason = HistoryFrameReason.INVALID_JSON
    if reason is not None:
        raise HistoryFrameError(reason)
    if not isinstance(payload, dict):
        raise HistoryFrameError(HistoryFrameReason.ROOT_NOT_OBJECT)
    if not _has_only_unicode_scalar_strings(payload):
        raise HistoryFrameError(HistoryFrameReason.INVALID_UNICODE)
    canonical_failure: HistoryFrameReason | None = None
    try:
        canonical = rfc8785.dumps(payload)
    except rfc8785.CanonicalizationError:
        canonical_failure = HistoryFrameReason.INVALID_UNICODE
    except RecursionError:
        canonical_failure = HistoryFrameReason.INVALID_JSON
    if canonical_failure is not None:
        raise HistoryFrameError(canonical_failure)
    if canonical != body:
        raise HistoryFrameError(HistoryFrameReason.NON_CANONICAL_BODY)
    return payload


def _has_only_unicode_scalar_strings(root: object) -> bool:
    pending = [root]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
                return False
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return True


def _result_echoes_query(result: _HistoryResult, query: SourceHistoryQueryV1) -> bool:
    return all(getattr(result, field) == getattr(query, field) for field in _QUERY_ECHO_FIELDS)


def _auth_input(
    *,
    session_id: str,
    direction: bytes,
    sequence: int,
    frame_length: int,
    unsigned_body: bytes,
) -> bytes:
    return b"".join(
        (
            _length_prefix(_FRAME_AUTH_DOMAIN),
            _length_prefix(session_id.encode("utf-8")),
            _length_prefix(direction),
            sequence.to_bytes(8, "big"),
            frame_length.to_bytes(4, "big"),
            len(unsigned_body).to_bytes(4, "big"),
            unsigned_body,
        )
    )


def _length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value
