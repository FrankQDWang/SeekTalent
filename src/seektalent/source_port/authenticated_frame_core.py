"""Reusable post-handshake authenticated frame mechanics for Source Port contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
from typing import Annotated, Generic, Literal, Never, TypeAlias, TypeVar

from pydantic import Field, TypeAdapter, ValidationError

from seektalent.source_port.wire_primitives import (
    CanonicalJsonError,
    ExactIntegerOne,
    JSON_SAFE_INTEGER,
    NonNegativeJsonInteger,
    Opaque96,
    Sha256,
    StrictWireModel,
    canonical_json_bytes,
)


PROTOCOL_NAME = "seektalent.source-execution-port"
PROTOCOL_MAJOR = 1
DEFAULT_MAX_FRAME_BYTES = 1_048_576
DEFAULT_MAX_SESSION_MESSAGES = 65_536
DEFAULT_MAX_PENDING_REQUESTS = 256
FRAME_AUTH_DOMAIN = b"seektalent-source-port-frame-auth-v1"
MAIN_TO_SIDECAR = b"main-to-sidecar"
SIDECAR_TO_MAIN = b"sidecar-to-main"
ZERO_AUTH_TAG = "0" * 64

EndpointRole: TypeAlias = Literal["main", "sidecar"]
AuthTag = Annotated[Sha256, Field(repr=False)]
EnvelopeT = TypeVar("EnvelopeT", bound="AuthenticatedFrameEnvelopeBase")
ReceivedT = TypeVar("ReceivedT")
PendingT = TypeVar("PendingT")


class AuthenticatedFrameEnvelopeBase(StrictWireModel):
    """Fields authenticated identically for every post-handshake envelope."""

    protocol_name: Literal["seektalent.source-execution-port"]
    protocol_major: ExactIntegerOne
    protocol_minor: NonNegativeJsonInteger
    session_id: Opaque96
    direction_seq: Annotated[int, Field(strict=True, ge=1, le=JSON_SAFE_INTEGER)]
    message_id: Opaque96
    reply_to: Opaque96 | None
    message_type: str
    correlation_id: Opaque96 | None
    auth_tag: AuthTag


class AuthenticatedFrameError(ValueError):
    """A sanitized frame protocol failure with no wire-body detail."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(reason_code={self.reason_code!r})"


class ReplyValidationError(ValueError):
    """A contract-specific reply failure that remains safe for the frame boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class PendingReply:
    """The semantic disposition of an authenticated reply."""

    keep_pending: bool
    state: object | None = None

    @classmethod
    def terminal(cls) -> PendingReply:
        return cls(keep_pending=False)

    @classmethod
    def pending(cls, state: object | None) -> PendingReply:
        return cls(keep_pending=True, state=state)


@dataclass(slots=True)
class _PendingRequest(Generic[PendingT]):
    request: PendingT
    state: object | None = None


ErrorFactory: TypeAlias = Callable[[str], AuthenticatedFrameError]
ReplyValidator: TypeAlias = Callable[[PendingT, EnvelopeT, object | None], PendingReply]
ReceivedMessageFactory: TypeAlias = Callable[[EnvelopeT], ReceivedT]
PendingRequestFactory: TypeAlias = Callable[[EnvelopeT], PendingT]
LimitSupplier: TypeAlias = Callable[[], int]


class AuthenticatedFrameSession(Generic[EnvelopeT, ReceivedT, PendingT]):
    """One endpoint's bounded framing, authentication, sequencing, and reply state."""

    __slots__ = (
        "_body",
        "_closed",
        "_closed_reason",
        "_envelope_adapter",
        "_error_factory",
        "_expected_body_length",
        "_header",
        "_main_send_types",
        "_max_frame_bytes",
        "_max_pending_requests",
        "_max_session_messages",
        "_message_ids",
        "_next_receive_sequence",
        "_next_send_sequence",
        "_pending_requests",
        "_pending_from_request",
        "_pending_request_limit_reason",
        "_protocol_minor",
        "_receive_direction",
        "_receive_key",
        "_received_message",
        "_reply_mismatch_reason",
        "_reply_validator",
        "_request_message_types",
        "_response_message_types",
        "_role",
        "_send_direction",
        "_send_key",
        "_session_id",
        "_sidecar_send_types",
    )

    def __init__(
        self,
        *,
        role: EndpointRole,
        session_id: str,
        protocol_minor: int,
        main_to_sidecar_key: bytes,
        sidecar_to_main_key: bytes,
        envelope_adapter: TypeAdapter[EnvelopeT],
        error_factory: ErrorFactory,
        main_send_types: frozenset[str],
        sidecar_send_types: frozenset[str],
        request_message_types: frozenset[str],
        response_message_types: frozenset[str],
        reply_validator: ReplyValidator[PendingT, EnvelopeT],
        received_message: ReceivedMessageFactory[EnvelopeT, ReceivedT],
        pending_from_request: PendingRequestFactory[EnvelopeT, PendingT],
        reply_mismatch_reason: str,
        pending_request_limit_reason: str,
        max_frame_bytes: LimitSupplier,
        max_session_messages: LimitSupplier,
        max_pending_requests: LimitSupplier,
    ) -> None:
        self._error_factory = error_factory
        if role not in ("main", "sidecar"):
            raise self._new_error("source_port_invalid_session_config")
        self._session_id = self._validated_session_id(session_id)
        self._protocol_minor = self._validated_protocol_minor(protocol_minor)
        main_to_sidecar_key = self._validated_direction_key(main_to_sidecar_key)
        sidecar_to_main_key = self._validated_direction_key(sidecar_to_main_key)
        self._role = role
        if role == "main":
            self._send_key = main_to_sidecar_key
            self._receive_key = sidecar_to_main_key
            self._send_direction = MAIN_TO_SIDECAR
            self._receive_direction = SIDECAR_TO_MAIN
        else:
            self._send_key = sidecar_to_main_key
            self._receive_key = main_to_sidecar_key
            self._send_direction = SIDECAR_TO_MAIN
            self._receive_direction = MAIN_TO_SIDECAR
        self._envelope_adapter = envelope_adapter
        self._main_send_types = main_send_types
        self._sidecar_send_types = sidecar_send_types
        self._request_message_types = request_message_types
        self._response_message_types = response_message_types
        self._reply_validator = reply_validator
        self._received_message = received_message
        self._pending_from_request = pending_from_request
        self._reply_mismatch_reason = reply_mismatch_reason
        if type(pending_request_limit_reason) is not str or not pending_request_limit_reason:
            raise self._new_error("source_port_invalid_session_config")
        self._pending_request_limit_reason = pending_request_limit_reason
        self._max_frame_bytes = max_frame_bytes
        self._max_session_messages = max_session_messages
        self._max_pending_requests = max_pending_requests
        self._next_send_sequence = 1
        self._next_receive_sequence = 1
        self._message_ids: set[str] = set()
        self._pending_requests: dict[str, _PendingRequest[PendingT]] = {}
        self._header = bytearray()
        self._body = bytearray()
        self._expected_body_length: int | None = None
        self._closed = False
        self._closed_reason: str | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(role={self._role!r}, closed={self._closed!r})"

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def closed_reason(self) -> str | None:
        return self._closed_reason

    def encode(self, envelope: EnvelopeT) -> bytes:
        """Authenticate one strict outbound envelope and update bounded state."""
        self._require_open()
        validated = self._validated_envelope(envelope)
        self._require_outbound_context(validated)
        pending, reply = self._pending_for_outbound(validated)
        frame = self._encode_envelope(validated)
        self._message_ids.add(validated.message_id)
        self._apply_pending_transition(validated, pending, reply)
        self._next_send_sequence += 1
        return frame

    def feed(self, data: bytes) -> tuple[ReceivedT, ...]:
        """Accept complete authenticated envelopes from arbitrary frame fragments."""
        self._require_open()
        if type(data) is not bytes:
            self._fail("source_port_raw_input_required")
        received: list[ReceivedT] = []
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
                        self._fail("source_port_frame_length_invalid")
                    if frame_length > self._max_frame_bytes():
                        self._fail("source_port_frame_too_large")
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
                envelope = self._decode_envelope(body, frame_length=frame_length)
                self._accept_envelope(envelope)
                received.append(self._received_message(envelope))
        except AuthenticatedFrameError as exc:
            self._close(exc.reason_code)
            raise
        return tuple(received)

    def feed_eof(self) -> None:
        self._require_open()
        if self._header or self._body or self._expected_body_length is not None:
            self._fail("source_port_truncated_frame")
        self._close(None)

    def require_frame_boundary(self) -> None:
        """Fail closed when a complete message is followed by a partial frame."""
        self._require_open()
        if self._header or self._body or self._expected_body_length is not None:
            self._fail("source_port_truncated_frame")

    def _pending_request(self, message_id: str) -> PendingT | None:
        pending = self._pending_requests.get(message_id)
        return None if pending is None else pending.request

    def _require_outbound_context(self, envelope: EnvelopeT) -> None:
        if envelope.protocol_name != PROTOCOL_NAME or envelope.protocol_major != PROTOCOL_MAJOR:
            self._fail("source_port_protocol_mismatch")
        if envelope.protocol_minor != self._protocol_minor:
            self._fail("source_port_protocol_mismatch")
        if envelope.session_id != self._session_id:
            self._fail("source_port_session_mismatch")
        if envelope.auth_tag != ZERO_AUTH_TAG:
            self._fail("source_port_schema_validation")
        if envelope.direction_seq != self._require_send_sequence():
            self._fail("source_port_sequence_mismatch")
        if envelope.message_type not in self._outbound_message_types():
            self._fail("source_port_unexpected_direction")
        if envelope.message_type in self._request_message_types:
            if envelope.reply_to is not None:
                self._fail("source_port_schema_validation")
        elif envelope.message_type in self._response_message_types:
            if envelope.reply_to is None:
                self._fail("source_port_schema_validation")
        else:
            self._fail("source_port_schema_validation")
        self._require_new_message_id(envelope.message_id)

    def _pending_for_outbound(
        self,
        envelope: EnvelopeT,
    ) -> tuple[_PendingRequest[PendingT] | None, PendingReply | None]:
        if envelope.message_type in self._request_message_types:
            self._require_pending_capacity()
            return None, None
        assert envelope.reply_to is not None
        pending = self._pending_requests.get(envelope.reply_to)
        if pending is None:
            self._fail("source_port_wrong_reply")
        return pending, self._validated_reply(pending, envelope)

    def _apply_pending_transition(
        self,
        envelope: EnvelopeT,
        pending: _PendingRequest[PendingT] | None,
        reply: PendingReply | None,
    ) -> None:
        if envelope.message_type in self._request_message_types:
            pending_failed = False
            pending_request: PendingT | None = None
            try:
                pending_request = self._pending_from_request(envelope)
            except (TypeError, ValueError, ValidationError):
                pending_failed = True
            if pending_failed or pending_request is None:
                self._fail("source_port_schema_validation")
            self._pending_requests[envelope.message_id] = _PendingRequest(pending_request)
            return
        assert envelope.reply_to is not None
        assert pending is not None
        assert reply is not None
        if reply.keep_pending:
            pending.state = reply.state
        else:
            del self._pending_requests[envelope.reply_to]

    def _decode_envelope(self, body: bytes, *, frame_length: int) -> EnvelopeT:
        parsed = strict_canonical_json_object(body, error_factory=self._error_factory)
        validation_failed = False
        try:
            envelope = self._envelope_adapter.validate_json(body, strict=True)
        except ValidationError:
            validation_failed = True
            envelope = None
        if validation_failed:
            self._fail("source_port_schema_validation")
        assert envelope is not None
        if envelope.protocol_name != PROTOCOL_NAME or envelope.protocol_major != PROTOCOL_MAJOR:
            self._fail("source_port_protocol_mismatch")
        if envelope.protocol_minor != self._protocol_minor:
            self._fail("source_port_protocol_mismatch")
        if envelope.session_id != self._session_id:
            self._fail("source_port_session_mismatch")
        unsigned = dict(parsed)
        auth_tag = unsigned.pop("auth_tag")
        unsigned_body = self._canonical_bytes(unsigned)
        expected_length = len(self._canonical_bytes({**unsigned, "auth_tag": ZERO_AUTH_TAG}))
        if expected_length != frame_length:
            self._fail("source_port_frame_length_mismatch")
        expected_tag = hmac.new(
            self._receive_key,
            auth_input(
                session_id=self._session_id,
                direction=self._receive_direction,
                sequence=envelope.direction_seq,
                frame_length=frame_length,
                unsigned_body=unsigned_body,
            ),
            sha256,
        ).hexdigest()
        if not isinstance(auth_tag, str) or not hmac.compare_digest(auth_tag, expected_tag):
            self._fail("source_port_bad_auth_tag")
        return envelope

    def _accept_envelope(self, envelope: EnvelopeT) -> None:
        if envelope.direction_seq != self._next_receive_sequence:
            self._fail("source_port_sequence_mismatch")
        if self._next_receive_sequence >= JSON_SAFE_INTEGER:
            self._fail("source_port_sequence_exhausted")
        if envelope.message_type not in self._inbound_message_types():
            self._fail("source_port_unexpected_direction")
        if envelope.message_type in self._request_message_types:
            if envelope.reply_to is not None:
                self._fail("source_port_schema_validation")
        elif envelope.message_type in self._response_message_types:
            if envelope.reply_to is None:
                self._fail("source_port_schema_validation")
        else:
            self._fail("source_port_schema_validation")
        self._require_new_message_id(envelope.message_id)
        pending, reply = self._pending_for_inbound(envelope)
        self._message_ids.add(envelope.message_id)
        self._apply_pending_transition(envelope, pending, reply)
        self._next_receive_sequence += 1

    def _pending_for_inbound(
        self,
        envelope: EnvelopeT,
    ) -> tuple[_PendingRequest[PendingT] | None, PendingReply | None]:
        if envelope.message_type in self._request_message_types:
            self._require_pending_capacity()
            return None, None
        assert envelope.reply_to is not None
        pending = self._pending_requests.get(envelope.reply_to)
        if pending is None:
            self._fail("source_port_wrong_reply")
        return pending, self._validated_reply(pending, envelope)

    def _validated_reply(
        self,
        pending: _PendingRequest[PendingT],
        envelope: EnvelopeT,
    ) -> PendingReply:
        failure_reason: str | None = None
        decision: PendingReply | None = None
        try:
            decision = self._reply_validator(pending.request, envelope, pending.state)
        except ReplyValidationError as exc:
            failure_reason = exc.reason_code
        except (TypeError, ValueError, ValidationError):
            failure_reason = self._reply_mismatch_reason
        if failure_reason is not None or not isinstance(decision, PendingReply):
            self._fail(failure_reason or self._reply_mismatch_reason)
        return decision

    def _encode_envelope(self, envelope: EnvelopeT) -> bytes:
        unsigned = envelope.model_dump(mode="json", exclude={"auth_tag"})
        unsigned_body = self._canonical_bytes(unsigned)
        zero_tag_body = self._canonical_bytes({**unsigned, "auth_tag": ZERO_AUTH_TAG})
        frame_length = len(zero_tag_body)
        if frame_length > self._max_frame_bytes():
            self._fail("source_port_frame_too_large")
        auth_tag = hmac.new(
            self._send_key,
            auth_input(
                session_id=envelope.session_id,
                direction=self._send_direction,
                sequence=envelope.direction_seq,
                frame_length=frame_length,
                unsigned_body=unsigned_body,
            ),
            sha256,
        ).hexdigest()
        body = self._canonical_bytes({**unsigned, "auth_tag": auth_tag})
        if len(body) != frame_length:
            self._fail("source_port_frame_length_mismatch")
        return frame_length.to_bytes(4, "big") + body

    def _validated_envelope(self, value: EnvelopeT) -> EnvelopeT:
        validation_failed = False
        envelope: EnvelopeT | None = None
        if not isinstance(value, AuthenticatedFrameEnvelopeBase):
            validation_failed = True
        else:
            try:
                envelope = self._envelope_adapter.validate_python(
                    value.model_dump(mode="python", warnings="error"),
                    strict=True,
                )
            except (TypeError, ValueError, ValidationError):
                validation_failed = True
        if validation_failed or envelope is None:
            self._fail("source_port_schema_validation")
        return envelope

    def _canonical_bytes(self, payload: object) -> bytes:
        failed = False
        canonical: bytes | None = None
        try:
            canonical = canonical_json_bytes(payload)
        except ValueError:
            failed = True
        if failed or canonical is None:
            self._fail("source_port_schema_validation")
        return canonical

    def _outbound_message_types(self) -> frozenset[str]:
        return self._main_send_types if self._role == "main" else self._sidecar_send_types

    def _inbound_message_types(self) -> frozenset[str]:
        return self._sidecar_send_types if self._role == "main" else self._main_send_types

    def _require_new_message_id(self, message_id: str) -> None:
        if message_id in self._message_ids:
            self._fail("source_port_duplicate_message_id")
        if len(self._message_ids) >= self._max_session_messages():
            self._fail("source_port_message_limit")

    def _require_pending_capacity(self) -> None:
        if len(self._pending_requests) >= self._max_pending_requests():
            self._fail(self._pending_request_limit_reason)

    def _require_send_sequence(self) -> int:
        if self._next_send_sequence >= JSON_SAFE_INTEGER:
            self._fail("source_port_sequence_exhausted")
        return self._next_send_sequence

    def _require_open(self) -> None:
        if self._closed:
            raise self._new_error("source_port_session_closed")

    def _fail(self, reason_code: str) -> Never:
        self._close(reason_code)
        raise self._new_error(reason_code)

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
        self._pending_requests.clear()

    def _new_error(self, reason_code: str) -> AuthenticatedFrameError:
        return self._error_factory(reason_code)

    def _validated_session_id(self, value: str) -> str:
        adapter = TypeAdapter(Opaque96)
        validation_failed = False
        session_id: str | None = None
        try:
            session_id = adapter.validate_python(value, strict=True)
        except ValidationError:
            validation_failed = True
        if validation_failed or session_id is None:
            raise self._new_error("source_port_invalid_session_config")
        return session_id

    def _validated_protocol_minor(self, value: int) -> int:
        adapter = TypeAdapter(NonNegativeJsonInteger)
        validation_failed = False
        protocol_minor: int | None = None
        try:
            protocol_minor = adapter.validate_python(value, strict=True)
        except ValidationError:
            validation_failed = True
        if validation_failed or protocol_minor is None:
            raise self._new_error("source_port_invalid_session_config")
        return protocol_minor

    def _validated_direction_key(self, value: bytes) -> bytes:
        if type(value) is not bytes or len(value) != 32:
            raise self._new_error("source_port_invalid_direction_key")
        return value


class _StrictJsonFailure(Exception):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def strict_canonical_json_object(
    body: bytes,
    *,
    error_factory: ErrorFactory,
) -> dict[str, object]:
    """Parse exactly one canonical JSON object without accepting aliases or duplicates."""
    decode_failed = False
    text: str | None = None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        decode_failed = True
    if decode_failed or text is None:
        raise error_factory("source_port_invalid_utf8")
    if text.startswith("\ufeff"):
        raise error_factory("source_port_bom_forbidden")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _StrictJsonFailure("source_port_duplicate_key")
            result[key] = value
        return result

    def reject_float(_: str) -> float:
        raise _StrictJsonFailure("source_port_illegal_number")

    def reject_constant(_: str) -> float:
        raise _StrictJsonFailure("source_port_illegal_number")

    def parse_integer(value: str) -> int:
        if value == "-0":
            raise _StrictJsonFailure("source_port_illegal_number")
        digits = value[1:] if value.startswith("-") else value
        if len(digits) > 16:
            raise _StrictJsonFailure("source_port_illegal_number")
        parsed = int(value)
        if parsed < -JSON_SAFE_INTEGER or parsed > JSON_SAFE_INTEGER:
            raise _StrictJsonFailure("source_port_illegal_number")
        return parsed

    reason_code: str | None = None
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
        reason_code = exc.reason_code
    except (json.JSONDecodeError, RecursionError):
        reason_code = "source_port_invalid_json"
    if reason_code is not None:
        raise error_factory(reason_code)
    if not isinstance(payload, dict):
        raise error_factory("source_port_root_not_object")
    if not has_only_unicode_scalar_strings(payload):
        raise error_factory("source_port_invalid_unicode")
    canonical_failure: str | None = None
    canonical: bytes | None = None
    try:
        canonical = canonical_json_bytes(payload)
    except CanonicalJsonError as exc:
        canonical_failure = exc.kind
    if canonical_failure is not None or canonical is None:
        reason_code = "source_port_invalid_json" if canonical_failure == "recursion" else "source_port_invalid_unicode"
        raise error_factory(reason_code)
    if canonical != body:
        raise error_factory("source_port_non_canonical_body")
    return payload


def has_only_unicode_scalar_strings(root: object) -> bool:
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


def auth_input(
    *,
    session_id: str,
    direction: bytes,
    sequence: int,
    frame_length: int,
    unsigned_body: bytes,
) -> bytes:
    return b"".join(
        (
            length_prefix(FRAME_AUTH_DOMAIN),
            length_prefix(session_id.encode("utf-8")),
            length_prefix(direction),
            sequence.to_bytes(8, "big"),
            frame_length.to_bytes(4, "big"),
            len(unsigned_body).to_bytes(4, "big"),
            unsigned_body,
        )
    )


def length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


__all__ = [
    "AuthenticatedFrameEnvelopeBase",
    "AuthenticatedFrameError",
    "AuthenticatedFrameSession",
    "DEFAULT_MAX_FRAME_BYTES",
    "DEFAULT_MAX_PENDING_REQUESTS",
    "DEFAULT_MAX_SESSION_MESSAGES",
    "EndpointRole",
    "MAIN_TO_SIDECAR",
    "PendingReply",
    "PROTOCOL_MAJOR",
    "PROTOCOL_NAME",
    "ReplyValidationError",
    "SIDECAR_TO_MAIN",
    "ZERO_AUTH_TAG",
    "auth_input",
    "has_only_unicode_scalar_strings",
    "length_prefix",
    "strict_canonical_json_object",
]
