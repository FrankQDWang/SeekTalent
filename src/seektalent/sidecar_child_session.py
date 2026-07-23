"""Child-side post-readiness transport ownership for the packaged bootstrap."""

from __future__ import annotations

import threading
import time
import weakref
from dataclasses import dataclass
from typing import IO, Literal, Never, SupportsIndex

from seektalent.sidecar_handshake_protocol import (
    DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
    SidecarHandshakeIdentity,
    SidecarReadinessError,
    SidecarReadinessReason,
    _HandshakeMaterial,
    _ProtocolTransport,
    _retain_unclosed_transport,
    _validated_timeout,
    perform_sidecar_handshake,
)
from seektalent.source_port.sidecar_transport import (
    PostHandshakeSourcePortSession,
    ReceivedSourcePortMessage,
    SourcePortEndpoint,
    _register_source_port_endpoint,
    receive_source_port_messages,
    send_source_port_frame,
    wait_for_parent_eof as _wait_for_parent_eof,
)


@dataclass(slots=True)
class _SidecarResultState:
    transport: _ProtocolTransport
    session_id: str
    protocol_minor: int
    source_port: PostHandshakeSourcePortSession
    source_port_exchange_lock: threading.Lock
    source_port_exchange_in_flight: bool = False
    source_port_exchange_usable: bool = True


_RESULTS: dict[int, tuple[weakref.ReferenceType["SidecarHandshakeResult"], _SidecarResultState]] = {}
_RESULTS_LOCK = threading.Lock()


class SidecarHandshakeResult(SourcePortEndpoint):
    """Factory-only child transport authority with one shared Source Port state."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("SidecarHandshakeResult is factory-only")

    @property
    def session_id(self) -> str:
        return _result_state(self).session_id

    @property
    def protocol_minor(self) -> int:
        return _result_state(self).protocol_minor

    def source_port_session(self) -> PostHandshakeSourcePortSession:
        return _result_state(self).source_port

    def _send_source_port_frame(self, frame: bytes, deadline: float) -> None:
        _result_state(self).transport.write_raw(frame, deadline)

    def _receive_source_port_messages(self, deadline: float) -> tuple[ReceivedSourcePortMessage, ...]:
        state = _result_state(self)
        try:
            chunk = state.transport.read_history_chunk(deadline, None)
        except SidecarReadinessError as error:
            if error.reason is SidecarReadinessReason.EOF:
                state.source_port.feed_eof()
            raise
        return state.source_port.feed(chunk)

    def _begin_source_port_exchange(self) -> Literal["acquired", "in_flight", "unusable"]:
        state = _result_state(self)
        with state.source_port_exchange_lock:
            if state.source_port_exchange_in_flight:
                return "in_flight"
            if not state.source_port_exchange_usable:
                return "unusable"
            state.source_port_exchange_in_flight = True
            return "acquired"

    def _finish_source_port_exchange(self, *, succeeded: bool) -> None:
        state = _result_state(self)
        with state.source_port_exchange_lock:
            state.source_port_exchange_in_flight = False
            if not succeeded:
                state.source_port_exchange_usable = False

    def new_history_session(self) -> PostHandshakeSourcePortSession:
        """Compatibility view over the one shared post-handshake session."""
        return self.source_port_session()

    def send_history_frame(self, frame: bytes, *, timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS) -> None:
        send_source_port_frame(self, frame, timeout=timeout)

    def receive_history(self, *, timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS) -> tuple[ReceivedSourcePortMessage, ...]:
        return receive_source_port_messages(self, timeout=timeout)

    def wait_for_parent_eof(self) -> None:
        _wait_for_parent_eof(self)

    def close(self) -> None:
        if not _result_state(self).transport.close():
            raise SidecarReadinessError(SidecarReadinessReason.PIPE_IO_FAILURE)
        _discard_result(self)

    def __copy__(self) -> Never:
        raise TypeError("SidecarHandshakeResult cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("SidecarHandshakeResult cannot be copied")

    def __reduce_ex__(self, _: SupportsIndex) -> Never:
        raise TypeError("SidecarHandshakeResult cannot be serialized")


def serve_sidecar_handshake(
    reader_stream: IO[bytes],
    writer_stream: IO[bytes],
    identity: SidecarHandshakeIdentity,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> SidecarHandshakeResult:
    """Perform readiness, then hand the same bounded transport to the child session."""
    transport = _ProtocolTransport(reader_stream, writer_stream)
    try:
        material = perform_sidecar_handshake(
            transport,
            identity,
            deadline=time.monotonic() + _validated_timeout(timeout),
        )
        return _new_result(transport, material)
    except (OSError, RuntimeError, ValueError):
        if not transport.close():
            _retain_unclosed_transport(transport)
        raise


def _new_result(transport: _ProtocolTransport, material: _HandshakeMaterial) -> SidecarHandshakeResult:
    result = object.__new__(SidecarHandshakeResult)
    result_id = id(result)
    state = _SidecarResultState(
        transport=transport,
        session_id=material.session_id,
        protocol_minor=material.protocol_minor,
        source_port=PostHandshakeSourcePortSession.for_sidecar(
            session_id=material.session_id,
            protocol_minor=material.protocol_minor,
            main_to_sidecar_key=material.main_to_sidecar_key,
            sidecar_to_main_key=material.sidecar_to_main_key,
        ),
        source_port_exchange_lock=threading.Lock(),
    )

    def finalize(_: weakref.ReferenceType[SidecarHandshakeResult]) -> None:
        with _RESULTS_LOCK:
            entry = _RESULTS.pop(result_id, None)
        if entry is not None:
            if not entry[1].transport.close():
                _retain_unclosed_transport(entry[1].transport)

    with _RESULTS_LOCK:
        _RESULTS[result_id] = (weakref.ref(result, finalize), state)
    _register_source_port_endpoint(result)
    return result


def _result_state(result: SidecarHandshakeResult) -> _SidecarResultState:
    with _RESULTS_LOCK:
        entry = _RESULTS.get(id(result))
    if entry is None or entry[0]() is not result:
        raise TypeError("SidecarHandshakeResult must be a live factory result")
    return entry[1]


def _discard_result(result: SidecarHandshakeResult) -> None:
    with _RESULTS_LOCK:
        _RESULTS.pop(id(result), None)
