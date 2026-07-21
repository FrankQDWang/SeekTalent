"""Child-safe four-step sidecar readiness wire protocol and transport ownership."""

from __future__ import annotations

import base64
import hmac
import math
import os
import queue
import select
import selectors
import secrets
import socket
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
_READER_CLOSE_SECONDS = 1.0
_CONSTRUCTOR_WAKE_CLOSE_ATTEMPTS = 3
_WINDOWS_POLL_SECONDS = 0.01
_THREAD_TERMINATE = 0x0001
_ERROR_BROKEN_PIPE = 109
_ERROR_NO_DATA = 232
_ERROR_PIPE_NOT_CONNECTED = 233
_HANDSHAKE_PROOF_DOMAIN = b"seektalent-sidecar-readiness-proof/v1"
_HANDSHAKE_KEY_DOMAIN = b"seektalent-sidecar-readiness-hkdf/v1"
_MAIN_TO_SIDECAR = b"main-to-sidecar"
_SIDECAR_TO_MAIN = b"sidecar-to-main"


_RETAINED_CONSTRUCTOR_WAKE_DESCRIPTORS: set[int] = set()
_RETAINED_CONSTRUCTOR_WAKE_LOCK = threading.Lock()

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
    EXTRA_FRAME = "sidecar_readiness_extra_frame"
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
        _maintain_retained_constructor_wake_descriptors()
        _maintain_retained_unclosed_transports()
        self._reader_stream = reader_stream
        self._writer_stream = writer_stream
        self._items: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=_READ_QUEUE_CHUNKS)
        self._buffer = bytearray()
        self._eof = False
        self._closed = threading.Event()
        self._reader_stopped = threading.Event()
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._boundary_requests: queue.Queue[queue.Queue[tuple[bool, BaseException | None, bool]]] = queue.Queue()
        self._reader_error: BaseException | None = None
        self._reader_descriptor: int | None = None
        self._wake_reader_descriptor: int | None = None
        self._wake_writer_descriptor: int | None = None
        self._reader_was_blocking: bool | None = None

        try:
            descriptor = reader_stream.fileno()
        except (AttributeError, OSError, ValueError):
            descriptor = None
        try:
            if descriptor is not None and os.name == "posix":
                wake_reader, wake_writer = os.pipe()
                self._wake_reader_descriptor = wake_reader
                self._wake_writer_descriptor = wake_writer
                self._reader_was_blocking = os.get_blocking(descriptor)
                # Record this before changing its mode so every later wakeup
                # setup failure can restore the caller-owned pipe exactly.
                self._reader_descriptor = descriptor
                os.set_blocking(descriptor, False)
                os.set_blocking(wake_reader, False)
                os.set_blocking(wake_writer, False)

            self._reader_thread = threading.Thread(target=self._read, daemon=True)
            self._reader_thread.start()
        except BaseException:
            self._rollback_reader_setup()
            raise

    @property
    def reader_stopped(self) -> bool:
        return self._reader_stopped.is_set() and not self._reader_thread.is_alive()

    def close(self) -> bool:
        with self._close_lock:
            return self._close_locked()

    def _close_locked(self) -> bool:
        self._closed.set()
        self._wake_reader()
        if os.name == "nt":
            _cancel_windows_synchronous_read(self._reader_thread.native_id)
        if self._reader_descriptor is None and os.name != "nt":
            self._close_stream(self._reader_stream)
        self._reader_thread.join(timeout=_READER_CLOSE_SECONDS)
        stream_dispositions = tuple(
            self._close_stream(stream) for stream in (self._reader_stream, self._writer_stream)
        )
        streams_closed = all(stream_dispositions)
        wake_descriptors_closed = self._close_wakeup_descriptors()
        return self.reader_stopped and streams_closed and wake_descriptors_closed

    def require_ready_handoff_liveness(self, deadline: float, process: _ProcessProbe) -> None:
        """Confirm the reader observed no EOF/error at the post-SidecarReady handoff."""
        if process.poll() is not None:
            _fail(SidecarReadinessReason.CHILD_EXIT)
        eof, reader_error, _ = self._observe_phase_boundary(deadline)
        if reader_error is not None:
            _fail(SidecarReadinessReason.PIPE_IO_FAILURE)
        if eof:
            _fail(SidecarReadinessReason.EOF)
        if process.poll() is not None:
            _fail(SidecarReadinessReason.CHILD_EXIT)

    def require_clean_pre_ready_boundary(self, deadline: float) -> None:
        """Reject every byte delivered before the child has emitted SidecarReady."""
        eof, reader_error, pipe_has_bytes = self._observe_phase_boundary(deadline)
        if pipe_has_bytes or self._has_pending_bytes():
            _fail(SidecarReadinessReason.EXTRA_FRAME)
        if reader_error is not None:
            _fail(SidecarReadinessReason.PIPE_IO_FAILURE)
        if eof:
            _fail(SidecarReadinessReason.EOF)

    def _observe_phase_boundary(self, deadline: float) -> tuple[bool, BaseException | None, bool]:
        if self._reader_descriptor is not None:
            return self._await_boundary_observation(deadline, wake_reader=True)
        elif os.name == "nt":
            return self._await_boundary_observation(deadline, wake_reader=False)
        eof, reader_error = self._reader_state()
        return eof, reader_error, False

    def _await_boundary_observation(
        self,
        deadline: float,
        *,
        wake_reader: bool,
    ) -> tuple[bool, BaseException | None, bool]:
        response: queue.Queue[tuple[bool, BaseException | None, bool]] = queue.Queue(maxsize=1)
        self._boundary_requests.put(response)
        if wake_reader:
            self._wake_reader()
        while True:
            eof, reader_error = self._reader_state()
            if eof or reader_error is not None:
                return eof, reader_error, False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(SidecarReadinessReason.READ_TIMEOUT)
            try:
                return response.get(timeout=min(remaining, _WINDOWS_POLL_SECONDS))
            except queue.Empty:
                if self._reader_stopped.is_set():
                    eof, reader_error = self._reader_state()
                    if not eof and reader_error is None:
                        _fail(SidecarReadinessReason.PIPE_IO_FAILURE)
                    return eof, reader_error, False

    def _read(self) -> None:
        try:
            if self._reader_descriptor is not None:
                self._read_posix()
            elif os.name == "nt":
                self._read_windows_polling()
            else:
                self._read_blocking()
        except (OSError, ValueError) as exc:
            self._set_reader_error(exc)
            self._enqueue(exc)
        finally:
            self._answer_boundary_requests()
            self._reader_stopped.set()

    def _read_posix(self) -> None:
        descriptor = self._reader_descriptor
        wake_reader = self._wake_reader_descriptor
        if descriptor is None or wake_reader is None:
            raise AssertionError("POSIX reader requires its pipe and wakeup descriptors")
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ, "pipe")
            selector.register(wake_reader, selectors.EVENT_READ, "wake")
            while not self._closed.is_set():
                events = selector.select()
                saw_wake = False
                for key, _ in events:
                    if key.data == "pipe":
                        if not self._drain_posix_pipe(descriptor):
                            return
                    else:
                        saw_wake = True
                        self._drain_wakeup()
                if saw_wake:
                    self._drain_posix_pipe(descriptor)
                    self._answer_boundary_requests()
                    if self._reader_state()[0]:
                        return

    def _read_blocking(self) -> None:
        read_chunk = getattr(self._reader_stream, "read1", self._reader_stream.read)
        while not self._closed.is_set():
            self._answer_boundary_requests()
            chunk = read_chunk(4096)
            if not chunk:
                self._set_eof()
                self._enqueue(None)
                self._answer_boundary_requests()
                return
            self._enqueue(chunk)
            self._answer_boundary_requests()

    def _read_windows_polling(self) -> None:
        read_chunk = getattr(self._reader_stream, "read1", self._reader_stream.read)
        while not self._closed.is_set():
            eof, has_bytes = _windows_pipe_status(self._reader_stream)
            if eof:
                self._set_eof()
                self._enqueue(None)
                return
            if has_bytes:
                chunk = read_chunk(4096)
                if not chunk:
                    self._set_eof()
                    self._enqueue(None)
                    return
                self._enqueue(chunk)
                continue
            self._answer_boundary_requests()
            self._closed.wait(_WINDOWS_POLL_SECONDS)

    def _drain_posix_pipe(self, descriptor: int) -> bool:
        while not self._closed.is_set():
            try:
                chunk = os.read(descriptor, 4096)
            except BlockingIOError:
                return True
            if not chunk:
                self._set_eof()
                self._enqueue(None)
                return False
            self._enqueue(chunk)
        return False

    def _enqueue(self, item: bytes | BaseException | None) -> None:
        while not self._closed.is_set():
            try:
                self._items.put(item, timeout=0.05)
            except queue.Full:
                if self._closed.is_set():
                    return
            else:
                return

    def _set_eof(self) -> None:
        with self._state_lock:
            self._eof = True

    def _set_reader_error(self, error: BaseException) -> None:
        with self._state_lock:
            self._reader_error = error

    def _reader_state(self) -> tuple[bool, BaseException | None]:
        with self._state_lock:
            return self._eof, self._reader_error

    def _wake_reader(self) -> None:
        descriptor = self._wake_writer_descriptor
        if descriptor is None:
            return
        try:
            os.write(descriptor, b"x")
        except (BlockingIOError, OSError):
            return

    def _drain_wakeup(self) -> None:
        descriptor = self._wake_reader_descriptor
        if descriptor is None:
            return
        while True:
            try:
                if not os.read(descriptor, 4096):
                    return
            except BlockingIOError:
                return

    def _answer_boundary_requests(self, *, pipe_has_bytes: bool = False) -> None:
        eof, reader_error = self._reader_state()
        state = eof, reader_error, pipe_has_bytes
        while True:
            try:
                response = self._boundary_requests.get_nowait()
            except queue.Empty:
                return
            response.put(state)

    def _has_pending_bytes(self) -> bool:
        with self._items.mutex:
            return bool(self._buffer) or any(type(item) is bytes for item in self._items.queue)

    def _close_stream(self, stream: IO[bytes]) -> bool:
        if stream.closed:
            return True
        try:
            stream.close()
        except (OSError, ValueError):
            return stream.closed
        return stream.closed

    def _close_wakeup_descriptors(self) -> bool:
        closed = True
        for attribute in ("_wake_reader_descriptor", "_wake_writer_descriptor"):
            descriptor = getattr(self, attribute)
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except OSError:
                closed = False
            else:
                setattr(self, attribute, None)
        return closed

    def _rollback_reader_setup(self) -> None:
        descriptor = self._reader_descriptor
        if descriptor is not None and self._reader_was_blocking is not None:
            try:
                os.set_blocking(descriptor, self._reader_was_blocking)
            except OSError:
                self._closed.set()
        self._reader_descriptor = None
        for _ in range(_CONSTRUCTOR_WAKE_CLOSE_ATTEMPTS):
            if self._close_wakeup_descriptors():
                return
        _retain_constructor_wake_descriptors(self)

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
        if self._closed.is_set():
            _fail(SidecarReadinessReason.EOF)
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


def _maintain_retained_constructor_wake_descriptors() -> None:
    with _RETAINED_CONSTRUCTOR_WAKE_LOCK:
        descriptors = tuple(_RETAINED_CONSTRUCTOR_WAKE_DESCRIPTORS)
        _RETAINED_CONSTRUCTOR_WAKE_DESCRIPTORS.clear()
    for descriptor in descriptors:
        if not _close_retained_constructor_wake_descriptor(descriptor):
            with _RETAINED_CONSTRUCTOR_WAKE_LOCK:
                _RETAINED_CONSTRUCTOR_WAKE_DESCRIPTORS.add(descriptor)


def _close_retained_constructor_wake_descriptor(descriptor: int) -> bool:
    try:
        os.close(descriptor)
    except OSError:
        return False
    return True


def _retain_constructor_wake_descriptors(transport: _ProtocolTransport) -> None:
    descriptors: list[int] = []
    for attribute in ("_wake_reader_descriptor", "_wake_writer_descriptor"):
        descriptor = getattr(transport, attribute)
        if descriptor is not None:
            descriptors.append(descriptor)
            setattr(transport, attribute, None)
    with _RETAINED_CONSTRUCTOR_WAKE_LOCK:
        _RETAINED_CONSTRUCTOR_WAKE_DESCRIPTORS.update(descriptors)


_RETAINED_UNCLOSED_TRANSPORTS: set[_ProtocolTransport] = set()
_RETAINED_UNCLOSED_TRANSPORTS_LOCK = threading.Lock()


def _retain_unclosed_transport(transport: _ProtocolTransport) -> None:
    with _RETAINED_UNCLOSED_TRANSPORTS_LOCK:
        _RETAINED_UNCLOSED_TRANSPORTS.add(transport)


def _maintain_retained_unclosed_transports() -> None:
    with _RETAINED_UNCLOSED_TRANSPORTS_LOCK:
        transports = tuple(_RETAINED_UNCLOSED_TRANSPORTS)
        _RETAINED_UNCLOSED_TRANSPORTS.clear()
    for transport in transports:
        if not transport.close():
            with _RETAINED_UNCLOSED_TRANSPORTS_LOCK:
                _RETAINED_UNCLOSED_TRANSPORTS.add(transport)


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
    transport.require_ready_handoff_liveness(deadline, process)

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
    transport.require_clean_pre_ready_boundary(deadline)
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


def _cancel_windows_synchronous_read(thread_id: int | None) -> bool:
    """Best-effort close fallback for a Windows reader that is still blocked in ReadFile."""
    if os.name != "nt" or thread_id is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
        kernel32.CancelSynchronousIo.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        thread = kernel32.OpenThread(_THREAD_TERMINATE, False, thread_id)
        if not thread:
            return False
        try:
            cancelled = bool(kernel32.CancelSynchronousIo(wintypes.HANDLE(thread)))
        finally:
            closed = bool(kernel32.CloseHandle(wintypes.HANDLE(thread)))
        return cancelled and closed
    except (AttributeError, OSError):
        return False


def _windows_pipe_status(stream: IO[bytes]) -> tuple[bool, bool]:
    if os.name != "nt":
        return False, False
    if isinstance(stream, socket.SocketIO):
        readable, _, exceptional = select.select([stream], [], [stream], 0)
        if exceptional:
            raise OSError("socket protocol pipe reported an exceptional condition")
        return False, bool(readable)
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

    except (AttributeError, ImportError):
        return False, False
    try:
        descriptor = stream.fileno()
    except AttributeError:
        return False, False
    except (OSError, ValueError) as error:
        raise OSError(error.errno, "could not inspect sidecar pipe") from error

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    available = wintypes.DWORD()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    if kernel32.PeekNamedPipe(
        handle,
        None,
        0,
        None,
        ctypes.byref(available),
        None,
    ):
        return False, bool(available.value)
    error_code = ctypes.get_last_error()
    if error_code in {_ERROR_BROKEN_PIPE, _ERROR_NO_DATA, _ERROR_PIPE_NOT_CONNECTED}:
        return True, False
    raise OSError(error_code, "PeekNamedPipe failed for sidecar protocol pipe")


def _fail(reason: SidecarReadinessReason) -> Never:
    raise SidecarReadinessError(reason)
