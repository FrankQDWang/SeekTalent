"""Child-side post-readiness transport ownership for the packaged bootstrap."""

from __future__ import annotations

import inspect
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import secrets
from typing import IO, Never, SupportsIndex

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
from seektalent.source_port.authenticated_history_frames import (
    PostHandshakeHistorySession,
    ReceivedHistoryMessage,
    ReceivedHistoryQuery,
    SourceHistoryAdmissionError,
    SourceHistoryAdmissionReason,
)
from seektalent.source_port.history_contract import (
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
    SourceHistoryQueryResultV1,
    SourceHistoryUnavailable,
)

_QUERY_RESULT_CONTRACT_VERSION = "seektalent.source-port.query.result/v1"


@dataclass(slots=True)
class _SidecarResultState:
    transport: _ProtocolTransport
    session_id: str
    protocol_minor: int
    history: PostHandshakeHistorySession
    history_exchange_lock: threading.Lock
    history_query_in_flight: bool = False
    history_exchange_usable: bool = True


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
        try:
            chunk = state.transport.read_history_chunk(time.monotonic() + _validated_timeout(timeout), None)
        except SidecarReadinessError as error:
            if error.reason is SidecarReadinessReason.EOF:
                state.history.feed_eof()
            raise
        return state.history.feed(chunk)

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


def serve_source_history_query(
    session: SidecarHandshakeResult,
    reader: object,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> SourceHistoryQueryResultV1:
    """Serve one authenticated query with an injected read-only reader."""
    normalized_timeout = _validated_timeout(timeout)
    state = _result_state(session)
    _begin_history_exchange(state)
    succeeded = False
    try:
        deadline = time.monotonic() + normalized_timeout
        received = _receive_one_query(session, deadline)
        query = _reader_query(reader, received.payload, deadline)
        try:
            query_result = query(received.payload, deadline=deadline)
        except TimeoutError:
            raise SidecarReadinessError(SidecarReadinessReason.READ_TIMEOUT) from None
        except (OSError, RuntimeError, TypeError, ValueError):
            query_result = SourceHistoryUnavailable.model_validate(
                {
                    **received.payload.model_dump(exclude={"contract_version"}),
                    "contract_version": _QUERY_RESULT_CONTRACT_VERSION,
                    "outcome": "history_unavailable",
                    "reason": "unreadable",
                },
                strict=True,
            )
        if not isinstance(
            query_result,
            (SourceHistoryMatched, SourceHistoryNotFound, SourceHistoryIdentityConflict, SourceHistoryUnavailable),
        ):
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.READER_RESULT_INVALID)
        frame = state.history.encode_result(
            message_id=secrets.token_hex(16),
            reply_to=received.message_id,
            payload=query_result,
        )
        session.send_history_frame(frame, timeout=_remaining(deadline))
        succeeded = True
        return query_result
    finally:
        _finish_history_exchange(state, succeeded=succeeded)


def serve_test_source_history_database(
    session: SidecarHandshakeResult,
    path: Path,
    *,
    timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
) -> None:
    """Serve sequential queries for the explicit native test database until EOF."""
    from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader

    reader = SourceHistorySQLiteReader(path)
    while True:
        try:
            serve_source_history_query(session, reader, timeout=timeout)
        except SidecarReadinessError as error:
            if error.reason is SidecarReadinessReason.EOF:
                return
            raise


def _receive_one_query(session: SidecarHandshakeResult, deadline: float) -> ReceivedHistoryQuery:
    while True:
        messages = session.receive_history(timeout=_remaining(deadline))
        if not messages:
            continue
        if len(messages) != 1:
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.MULTIPLE_MESSAGES)
        session.new_history_session().require_frame_boundary()
        message = messages[0]
        if not isinstance(message, ReceivedHistoryQuery):
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.UNEXPECTED_MESSAGE)
        return message


def _reader_query(reader: object, request: SourceHistoryQueryV1, deadline: float) -> Callable[..., object]:
    try:
        query = getattr(reader, "query")
    except AttributeError:
        raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.READER_RESULT_INVALID) from None
    if not callable(query):
        raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.READER_RESULT_INVALID)
    try:
        inspect.signature(query).bind(request, deadline=deadline)
    except (TypeError, ValueError):
        raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.READER_RESULT_INVALID) from None
    return query


def _begin_history_exchange(state: _SidecarResultState) -> None:
    with state.history_exchange_lock:
        if state.history_query_in_flight:
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.QUERY_IN_FLIGHT)
        if not state.history_exchange_usable:
            raise SourceHistoryAdmissionError(SourceHistoryAdmissionReason.SESSION_UNUSABLE)
        state.history_query_in_flight = True


def _finish_history_exchange(state: _SidecarResultState, *, succeeded: bool) -> None:
    with state.history_exchange_lock:
        state.history_query_in_flight = False
        if not succeeded:
            state.history_exchange_usable = False


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SidecarReadinessError(SidecarReadinessReason.READ_TIMEOUT)
    return remaining


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
        history=PostHandshakeHistorySession.for_sidecar(
            session_id=material.session_id,
            protocol_minor=material.protocol_minor,
            main_to_sidecar_key=material.main_to_sidecar_key,
            sidecar_to_main_key=material.sidecar_to_main_key,
        ),
        history_exchange_lock=threading.Lock(),
    )

    def finalize(_: weakref.ReferenceType[SidecarHandshakeResult]) -> None:
        with _RESULTS_LOCK:
            entry = _RESULTS.pop(result_id, None)
        if entry is not None:
            if not entry[1].transport.close():
                _retain_unclosed_transport(entry[1].transport)

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
