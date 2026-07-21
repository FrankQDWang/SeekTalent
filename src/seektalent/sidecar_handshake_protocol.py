"""Child-safe four-step sidecar readiness wire protocol and transport ownership."""

from __future__ import annotations

import base64
import hmac
import math
import os
import queue
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import IO, Never, Protocol

import rfc8785

from seektalent.strict_json import StrictJsonError, StrictJsonReason, strict_json_object_loads


HANDSHAKE_VERSION = 1
MAX_HANDSHAKE_FRAME_BYTES = 64 * 1024
DEFAULT_HANDSHAKE_TIMEOUT_SECONDS = 5.0
_READ_QUEUE_CHUNKS = 16
_HANDSHAKE_PROOF_DOMAIN = b"seektalent-sidecar-readiness-proof/v1"
_HANDSHAKE_KEY_DOMAIN = b"seektalent-sidecar-readiness-hkdf/v1"
_MAIN_TO_SIDECAR = b"main-to-sidecar"
_SIDECAR_TO_MAIN = b"sidecar-to-main"

__all__ = [
    "DEFAULT_HANDSHAKE_TIMEOUT_SECONDS",
    "MAX_HANDSHAKE_FRAME_BYTES",
    "SidecarHandshakeIdentity",
    "SidecarReadinessError",
    "SidecarReadinessReason",
]


class SidecarReadinessReason(StrEnum):
    INVALID_TIMEOUT = "sidecar_readiness_invalid_timeout"
    CHILD_FAILURE = "sidecar_readiness_child_failure"
    CHILD_EXIT = "sidecar_readiness_child_exit"
    READ_TIMEOUT = "sidecar_readiness_read_timeout"
    WRITE_TIMEOUT = "sidecar_readiness_write_timeout"
    EOF = "sidecar_readiness_eof"
    TRUNCATED_FRAME = "sidecar_readiness_truncated_frame"
    FRAME_LENGTH_INVALID = "sidecar_readiness_frame_length_invalid"
    FRAME_TOO_LARGE = "sidecar_readiness_frame_too_large"
    INVALID_UTF8 = "sidecar_readiness_invalid_utf8"
    INVALID_JSON = "sidecar_readiness_invalid_json"
    DUPLICATE_KEY = "sidecar_readiness_duplicate_key"
    ILLEGAL_NUMBER = "sidecar_readiness_illegal_number"
    INVALID_UNICODE = "sidecar_readiness_invalid_unicode"
    ROOT_NOT_OBJECT = "sidecar_readiness_root_not_object"
    UNKNOWN_FIELD = "sidecar_readiness_unknown_field"
    SCHEMA_VALIDATION = "sidecar_readiness_schema_validation"
    PROTOCOL_MISMATCH = "sidecar_readiness_protocol_mismatch"
    UNEXPECTED_MESSAGE = "sidecar_readiness_unexpected_message"
    SESSION_MISMATCH = "sidecar_readiness_session_mismatch"
    NONCE_MISMATCH = "sidecar_readiness_nonce_mismatch"
    IDENTITY_MISMATCH = "sidecar_readiness_identity_mismatch"
    BAD_PROOF = "sidecar_readiness_bad_proof"
    PIPE_IO_FAILURE = "sidecar_readiness_pipe_io_failure"


class SidecarReadinessError(RuntimeError):
    """A sanitized readiness failure that never retains handshake material."""

    def __init__(
        self,
        reason: SidecarReadinessReason,
        *,
        cleanup_error: BaseException | None = None,
    ) -> None:
        self.reason = reason
        self.cleanup_error = cleanup_error
        super().__init__(reason.value)

    def __repr__(self) -> str:
        return f"SidecarReadinessError(reason={self.reason.value!r})"


@dataclass(frozen=True, slots=True)
class SidecarHandshakeIdentity:
    """Build-time sidecar facts reported after receiving MainHello."""

    product_build_id: str
    sidecar_build_id: str
    protocol_id: str
    protocol_major: int
    protocol_min_minor: int
    protocol_max_minor: int
    protocol_capabilities: tuple[str, ...]
    expected_main_application_build_id: str = field(compare=False)

    def payload(self) -> dict[str, object]:
        return {
            "product_build_id": self.product_build_id,
            "sidecar_build_id": self.sidecar_build_id,
            "protocol": {
                "protocol_id": self.protocol_id,
                "major": self.protocol_major,
                "min_minor": self.protocol_min_minor,
                "max_minor": self.protocol_max_minor,
                "capabilities": list(self.protocol_capabilities),
            },
        }


class _ProcessProbe(Protocol):
    def poll(self) -> int | None: ...


class _ProtocolTransport:
    """One bounded reader and writer owner for a sidecar protocol pipe pair."""

    def __init__(self, reader_stream: IO[bytes], writer_stream: IO[bytes]) -> None:
        self._reader_stream = reader_stream
        self._writer_stream = writer_stream
        self._items: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=_READ_QUEUE_CHUNKS)
        self._buffer = bytearray()
        self._eof = False
        self._closed = threading.Event()
        self._write_lock = threading.Lock()

        def enqueue(item: bytes | BaseException | None) -> None:
            while not self._closed.is_set():
                try:
                    self._items.put(item, timeout=0.05)
                except queue.Full:
                    if self._closed.is_set():
                        return
                else:
                    return

        def read() -> None:
            try:
                descriptor = reader_stream.fileno()
            except (AttributeError, OSError, ValueError):
                read_chunk = getattr(reader_stream, "read1", reader_stream.read)
            else:
                def read_chunk(size: int) -> bytes:
                    return os.read(descriptor, size)
            try:
                while not self._closed.is_set():
                    chunk = read_chunk(4096)
                    if not chunk:
                        enqueue(None)
                        return
                    enqueue(chunk)
            except (OSError, ValueError) as exc:
                enqueue(exc)

        self._reader_thread = threading.Thread(target=read, daemon=True)
        self._reader_thread.start()

    def close(self) -> None:
        self._closed.set()
        for stream in (self._reader_stream, self._writer_stream):
            try:
                if not stream.closed:
                    stream.close()
            except (OSError, ValueError):
                self._closed.set()

    def write_handshake(self, payload: dict[str, object], deadline: float) -> bytes:
        body = _canonical_payload(payload)
        if not 0 < len(body) <= MAX_HANDSHAKE_FRAME_BYTES:
            _fail(SidecarReadinessReason.FRAME_TOO_LARGE)
        self.write_raw(len(body).to_bytes(4, "big") + body, deadline)
        return body

    def write_raw(self, data: bytes, deadline: float) -> None:
        if type(data) is not bytes:
            _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
        complete: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)

        def write() -> None:
            try:
                with self._write_lock:
                    self._writer_stream.write(data)
                    self._writer_stream.flush()
            except (OSError, ValueError) as exc:
                complete.put(exc)
            else:
                complete.put(None)

        try:
            threading.Thread(target=write, daemon=True).start()
        except RuntimeError:
            _fail(SidecarReadinessReason.PIPE_IO_FAILURE)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail(SidecarReadinessReason.WRITE_TIMEOUT)
        try:
            failure = complete.get(timeout=remaining)
        except queue.Empty:
            _fail(SidecarReadinessReason.WRITE_TIMEOUT)
        if failure is not None:
            _fail(SidecarReadinessReason.PIPE_IO_FAILURE)

    def read_handshake(self, deadline: float, process: _ProcessProbe | None) -> bytes:
        while True:
            complete = self._pop_complete_handshake_frame()
            if complete is not None:
                return complete
            self._append_next(deadline, process, handshake=True)

    def read_history_chunk(self, deadline: float, process: _ProcessProbe | None) -> bytes:
        while not self._buffer:
            self._append_next(deadline, process, handshake=False)
        chunk = bytes(self._buffer)
        self._buffer.clear()
        return chunk

    def _append_next(self, deadline: float, process: _ProcessProbe | None, *, handshake: bool) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._raise_timed_out_or_exited(process, handshake=handshake)
        try:
            item = self._items.get(timeout=min(remaining, 0.05))
        except queue.Empty:
            if process is not None and process.poll() is not None:
                _fail(SidecarReadinessReason.CHILD_EXIT)
            return
        if item is None:
            self._eof = True
            if process is not None and process.poll() is not None:
                _fail(SidecarReadinessReason.CHILD_EXIT)
            if handshake and self._buffer:
                _fail(SidecarReadinessReason.TRUNCATED_FRAME)
            _fail(SidecarReadinessReason.EOF)
        if isinstance(item, BaseException):
            _fail(SidecarReadinessReason.PIPE_IO_FAILURE)
        self._buffer.extend(item)

    def _pop_complete_handshake_frame(self) -> bytes | None:
        if len(self._buffer) < 4:
            return None
        frame_length = int.from_bytes(self._buffer[:4], "big")
        if frame_length == 0:
            _fail(SidecarReadinessReason.FRAME_LENGTH_INVALID)
        if frame_length > MAX_HANDSHAKE_FRAME_BYTES:
            _fail(SidecarReadinessReason.FRAME_TOO_LARGE)
        if len(self._buffer) < 4 + frame_length:
            return None
        body = bytes(self._buffer[4 : 4 + frame_length])
        del self._buffer[: 4 + frame_length]
        return body

    def _raise_timed_out_or_exited(self, process: _ProcessProbe | None, *, handshake: bool) -> Never:
        if process is not None and process.poll() is not None:
            _fail(SidecarReadinessReason.CHILD_EXIT)
        if handshake and self._buffer:
            _fail(SidecarReadinessReason.TRUNCATED_FRAME)
        _fail(SidecarReadinessReason.READ_TIMEOUT)


@dataclass(frozen=True, slots=True)
class _HandshakeMaterial:
    session_id: str
    protocol_minor: int
    main_to_sidecar_key: bytes
    sidecar_to_main_key: bytes


def perform_main_handshake(
    transport: _ProtocolTransport,
    expected_identity: SidecarHandshakeIdentity,
    *,
    product_build_id: str,
    main_application_build_id: str,
    deadline: float,
    process: _ProcessProbe,
) -> _HandshakeMaterial:
    main_hello, session_secret = _new_main_hello(product_build_id, main_application_build_id)
    main_hello_raw = transport.write_handshake(main_hello, deadline)

    sidecar_hello_raw, sidecar_hello = _read_payload(transport, deadline, process)
    _require_message_type(sidecar_hello, "sidecar_hello")
    _require_shared_session_and_nonce(sidecar_hello, main_hello)
    if _identity_from_payload(sidecar_hello) != expected_identity:
        _fail(SidecarReadinessReason.IDENTITY_MISMATCH)
    _require_proof(sidecar_hello, session_secret, b"sidecar_hello", (main_hello_raw,))

    main_ready = _ready_payload(
        "main_ready",
        main_hello["session_id"],
        session_secret,
        b"main_ready",
        (main_hello_raw, sidecar_hello_raw),
    )
    main_ready_raw = transport.write_handshake(main_ready, deadline)

    sidecar_ready_raw, sidecar_ready = _read_payload(transport, deadline, process)
    _require_message_type(sidecar_ready, "sidecar_ready")
    _require_shared_session_and_nonce(sidecar_ready, main_hello, require_nonce=False)
    _require_proof(
        sidecar_ready,
        session_secret,
        b"sidecar_ready",
        (main_hello_raw, sidecar_hello_raw, main_ready_raw),
    )
    if process.poll() is not None:
        _fail(SidecarReadinessReason.CHILD_EXIT)

    session_id = _required_string(main_hello, "session_id")
    main_to_sidecar_key, sidecar_to_main_key = _derive_direction_keys(
        session_secret,
        session_id,
        (main_hello_raw, sidecar_hello_raw, main_ready_raw, sidecar_ready_raw),
    )
    return _HandshakeMaterial(
        session_id=session_id,
        protocol_minor=expected_identity.protocol_max_minor,
        main_to_sidecar_key=main_to_sidecar_key,
        sidecar_to_main_key=sidecar_to_main_key,
    )


def perform_sidecar_handshake(
    transport: _ProtocolTransport,
    identity: SidecarHandshakeIdentity,
    *,
    deadline: float,
) -> _HandshakeMaterial:
    """Complete child readiness without consuming post-SidecarReady bytes."""
    if not isinstance(identity, SidecarHandshakeIdentity):
        raise TypeError("identity must be SidecarHandshakeIdentity")
    main_hello_raw, main_hello = _read_payload(transport, deadline, None)
    _require_message_type(main_hello, "main_hello")
    session_id = _required_string(main_hello, "session_id")
    nonce = _required_string(main_hello, "nonce")
    session_secret = _decode_secret(_required_string(main_hello, "session_secret"))
    if _required_string(main_hello, "product_build_id") != identity.product_build_id:
        _fail(SidecarReadinessReason.IDENTITY_MISMATCH)
    if _required_string(main_hello, "main_application_build_id") != identity.expected_main_application_build_id:
        _fail(SidecarReadinessReason.IDENTITY_MISMATCH)

    sidecar_hello = _sidecar_hello_payload(identity, session_id, nonce, session_secret, (main_hello_raw,))
    sidecar_hello_raw = transport.write_handshake(sidecar_hello, deadline)

    main_ready_raw, main_ready = _read_payload(transport, deadline, None)
    _require_message_type(main_ready, "main_ready")
    _require_shared_session_and_nonce(main_ready, main_hello, require_nonce=False)
    _require_proof(main_ready, session_secret, b"main_ready", (main_hello_raw, sidecar_hello_raw))

    sidecar_ready = _ready_payload(
        "sidecar_ready",
        session_id,
        session_secret,
        b"sidecar_ready",
        (main_hello_raw, sidecar_hello_raw, main_ready_raw),
    )
    sidecar_ready_raw = transport.write_handshake(sidecar_ready, deadline)
    main_to_sidecar_key, sidecar_to_main_key = _derive_direction_keys(
        session_secret,
        session_id,
        (main_hello_raw, sidecar_hello_raw, main_ready_raw, sidecar_ready_raw),
    )
    return _HandshakeMaterial(
        session_id=session_id,
        protocol_minor=identity.protocol_max_minor,
        main_to_sidecar_key=main_to_sidecar_key,
        sidecar_to_main_key=sidecar_to_main_key,
    )


def _new_main_hello(product_build_id: str, main_application_build_id: str) -> tuple[dict[str, object], bytes]:
    session_secret = secrets.token_bytes(32)
    return (
        {
            "message_type": "main_hello",
            "handshake_version": HANDSHAKE_VERSION,
            "session_id": secrets.token_hex(16),
            "nonce": _encode_secret(secrets.token_bytes(32)),
            "session_secret": _encode_secret(session_secret),
            "product_build_id": product_build_id,
            "main_application_build_id": main_application_build_id,
        },
        session_secret,
    )


def _read_payload(
    transport: _ProtocolTransport,
    deadline: float,
    process: _ProcessProbe | None,
) -> tuple[bytes, dict[str, object]]:
    body = transport.read_handshake(deadline, process)
    try:
        payload = strict_json_object_loads(body)
    except StrictJsonError as exc:
        _fail(_strict_json_reason(exc.reason))
    _require_handshake_version(payload)
    return body, payload


def _sidecar_hello_payload(
    identity: SidecarHandshakeIdentity,
    session_id: str,
    nonce: str,
    session_secret: bytes,
    transcript: tuple[bytes, ...],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "message_type": "sidecar_hello",
        "handshake_version": HANDSHAKE_VERSION,
        "session_id": session_id,
        "nonce": nonce,
        **identity.payload(),
    }
    payload["proof"] = _proof(session_secret, b"sidecar_hello", transcript, payload)
    return payload


def _ready_payload(
    message_type: str,
    session_id: object,
    session_secret: bytes,
    stage: bytes,
    transcript: tuple[bytes, ...],
) -> dict[str, object]:
    if type(session_id) is not str:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    payload: dict[str, object] = {
        "message_type": message_type,
        "handshake_version": HANDSHAKE_VERSION,
        "session_id": session_id,
    }
    payload["proof"] = _proof(session_secret, stage, transcript, payload)
    return payload


def _require_handshake_version(payload: dict[str, object]) -> None:
    value = payload.get("handshake_version")
    if type(value) is not int:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    if value != HANDSHAKE_VERSION:
        _fail(SidecarReadinessReason.PROTOCOL_MISMATCH)


def _require_message_type(payload: dict[str, object], expected: str) -> None:
    message_type = payload.get("message_type")
    if type(message_type) is not str:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    if message_type != expected:
        _fail(SidecarReadinessReason.UNEXPECTED_MESSAGE)
    expected_fields = {
        "main_hello": {
            "message_type",
            "handshake_version",
            "session_id",
            "nonce",
            "session_secret",
            "product_build_id",
            "main_application_build_id",
        },
        "sidecar_hello": {
            "message_type",
            "handshake_version",
            "session_id",
            "nonce",
            "product_build_id",
            "sidecar_build_id",
            "protocol",
            "proof",
        },
        "main_ready": {"message_type", "handshake_version", "session_id", "proof"},
        "sidecar_ready": {"message_type", "handshake_version", "session_id", "proof"},
    }[expected]
    unknown = set(payload) - expected_fields
    missing = expected_fields - set(payload)
    if unknown:
        _fail(SidecarReadinessReason.UNKNOWN_FIELD)
    if missing:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)


def _require_shared_session_and_nonce(
    payload: dict[str, object],
    main_hello: dict[str, object],
    *,
    require_nonce: bool = True,
) -> None:
    if _required_string(payload, "session_id") != _required_string(main_hello, "session_id"):
        _fail(SidecarReadinessReason.SESSION_MISMATCH)
    if require_nonce and _required_string(payload, "nonce") != _required_string(main_hello, "nonce"):
        _fail(SidecarReadinessReason.NONCE_MISMATCH)


def _identity_from_payload(payload: dict[str, object]) -> SidecarHandshakeIdentity:
    protocol_value = payload.get("protocol")
    if not isinstance(protocol_value, dict) or set(protocol_value) != {
        "protocol_id",
        "major",
        "min_minor",
        "max_minor",
        "capabilities",
    }:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    protocol: dict[str, object] = {}
    for key, value in protocol_value.items():
        if type(key) is not str:
            _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
        protocol[key] = value
    capabilities = _required_string_list(protocol["capabilities"])
    if capabilities != tuple(sorted(capabilities)) or len(capabilities) != len(set(capabilities)):
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    major = protocol["major"]
    min_minor = protocol["min_minor"]
    max_minor = protocol["max_minor"]
    if type(major) is not int or type(min_minor) is not int or type(max_minor) is not int:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    if not 1 <= major <= 65535 or not 0 <= min_minor <= max_minor <= 65535:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    return SidecarHandshakeIdentity(
        product_build_id=_required_string(payload, "product_build_id"),
        sidecar_build_id=_required_string(payload, "sidecar_build_id"),
        protocol_id=_required_string(protocol, "protocol_id"),
        protocol_major=major,
        protocol_min_minor=min_minor,
        protocol_max_minor=max_minor,
        protocol_capabilities=capabilities,
        expected_main_application_build_id="",
    )


def _require_proof(
    payload: dict[str, object],
    session_secret: bytes,
    stage: bytes,
    transcript: tuple[bytes, ...],
) -> None:
    received = _required_string(payload, "proof")
    unsigned = dict(payload)
    unsigned.pop("proof", None)
    expected = _proof(session_secret, stage, transcript, unsigned)
    if not hmac.compare_digest(received, expected):
        _fail(SidecarReadinessReason.BAD_PROOF)


def _proof(
    session_secret: bytes,
    stage: bytes,
    transcript: tuple[bytes, ...],
    unsigned_payload: dict[str, object],
) -> str:
    material = b"".join(_length_prefixed(item) for item in transcript)
    material += _length_prefixed(_canonical_payload(unsigned_payload))
    return hmac.new(
        session_secret,
        _HANDSHAKE_PROOF_DOMAIN + _length_prefixed(stage) + _length_prefixed(material),
        sha256,
    ).hexdigest()


def _derive_direction_keys(
    session_secret: bytes,
    session_id: str,
    transcript: tuple[bytes, ...],
) -> tuple[bytes, bytes]:
    transcript_digest = sha256(b"".join(_length_prefixed(item) for item in transcript)).digest()

    def derive(direction: bytes) -> bytes:
        info = _HANDSHAKE_KEY_DOMAIN + _length_prefixed(session_id.encode("ascii")) + _length_prefixed(direction)
        pseudorandom_key = hmac.new(transcript_digest, session_secret, sha256).digest()
        return hmac.new(pseudorandom_key, info + b"\x01", sha256).digest()

    return derive(_MAIN_TO_SIDECAR), derive(_SIDECAR_TO_MAIN)


def _required_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if type(value) is not str or not value or len(value) > 512:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    return value


def _required_string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    strings: list[str] = []
    for item in value:
        if type(item) is not str:
            _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
        strings.append(item)
    return tuple(strings)


def _encode_secret(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode_secret(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    if len(decoded) != 32:
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)
    return decoded


def _canonical_payload(payload: dict[str, object]) -> bytes:
    try:
        return rfc8785.dumps(payload)
    except (TypeError, ValueError):
        _fail(SidecarReadinessReason.SCHEMA_VALIDATION)


def _strict_json_reason(reason: StrictJsonReason) -> SidecarReadinessReason:
    return {
        StrictJsonReason.INVALID_UTF8: SidecarReadinessReason.INVALID_UTF8,
        StrictJsonReason.INVALID_JSON: SidecarReadinessReason.INVALID_JSON,
        StrictJsonReason.DUPLICATE_KEY: SidecarReadinessReason.DUPLICATE_KEY,
        StrictJsonReason.ILLEGAL_NUMBER: SidecarReadinessReason.ILLEGAL_NUMBER,
        StrictJsonReason.INVALID_UNICODE: SidecarReadinessReason.INVALID_UNICODE,
        StrictJsonReason.ROOT_NOT_OBJECT: SidecarReadinessReason.ROOT_NOT_OBJECT,
    }[reason]


def _validated_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(SidecarReadinessReason.INVALID_TIMEOUT)
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        _fail(SidecarReadinessReason.INVALID_TIMEOUT)
    return normalized


def _length_prefixed(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _fail(reason: SidecarReadinessReason) -> Never:
    raise SidecarReadinessError(reason)
