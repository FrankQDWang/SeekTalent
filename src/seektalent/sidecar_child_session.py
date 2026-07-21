"""Child-side post-readiness transport ownership for the packaged bootstrap."""

from __future__ import annotations

import threading
import time
import weakref
from dataclasses import dataclass
from typing import IO, Never, SupportsIndex

from seektalent.sidecar_handshake_protocol import (
    DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
    SidecarHandshakeIdentity,
    SidecarReadinessError,
    SidecarReadinessReason,
    _HandshakeMaterial,
    _ProtocolTransport,
    _validated_timeout,
    perform_sidecar_handshake,
)
from seektalent.source_port.authenticated_history_frames import (
    PostHandshakeHistorySession,
    ReceivedHistoryMessage,
)


@dataclass(slots=True)
class _SidecarResultState:
    transport: _ProtocolTransport
    session_id: str
    protocol_minor: int
    history: PostHandshakeHistorySession


_RESULTS: dict[int, tuple[weakref.ReferenceType["SidecarHandshakeResult"], _SidecarResultState]] = {}
_RESULTS_LOCK = threading.Lock()


class SidecarHandshakeResult:
    """Factory-only child transport authority with one persistent history state."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("SidecarHandshakeResult is factory-only")

    @property
    def session_id(self) -> str:
        return _result_state(self).session_id

    @property
    def protocol_minor(self) -> int:
        return _result_state(self).protocol_minor

    def new_history_session(self) -> PostHandshakeHistorySession:
        return _result_state(self).history

    def send_history_frame(self, frame: bytes, *, timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS) -> None:
        state = _result_state(self)
        state.transport.write_raw(frame, time.monotonic() + _validated_timeout(timeout))

    def receive_history(self, *, timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS) -> tuple[ReceivedHistoryMessage, ...]:
        state = _result_state(self)
        return state.history.feed(
            state.transport.read_history_chunk(time.monotonic() + _validated_timeout(timeout), None)
        )

    def wait_for_parent_eof(self) -> None:
        state = _result_state(self)
        while True:
            try:
                chunk = state.transport.read_history_chunk(float("inf"), None)
            except SidecarReadinessError as error:
                if error.reason is not SidecarReadinessReason.EOF:
                    raise
                state.history.feed_eof()
                return
            state.history.feed(chunk)

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
        transport.close()
        raise


def _new_result(transport: _ProtocolTransport, material: _HandshakeMaterial) -> SidecarHandshakeResult:
    result = object.__new__(SidecarHandshakeResult)
    result_id = id(result)
    state = _SidecarResultState(
        transport=transport,
        session_id=material.session_id,
        protocol_minor=material.protocol_minor,
        history=PostHandshakeHistorySession.for_sidecar(
            session_id=material.session_id,
            protocol_minor=material.protocol_minor,
            main_to_sidecar_key=material.main_to_sidecar_key,
            sidecar_to_main_key=material.sidecar_to_main_key,
        ),
    )

    def finalize(_: weakref.ReferenceType[SidecarHandshakeResult]) -> None:
        with _RESULTS_LOCK:
            entry = _RESULTS.pop(result_id, None)
        if entry is not None:
            entry[1].transport.close()

    with _RESULTS_LOCK:
        _RESULTS[result_id] = (weakref.ref(result, finalize), state)
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
